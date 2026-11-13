"""Устойчивый учёт известных недовезённых заказов в собственной SQLite, отдельно от снимков маршрутов."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

DDL = (
    "CREATE TABLE IF NOT EXISTS warehouse_order(isn TEXT PRIMARY KEY, customer_id INTEGER NOT NULL, order_date TEXT NOT NULL, "
    "invoice TEXT NOT NULL DEFAULT '', state TEXT NOT NULL CHECK(state IN ('warehouse','assigned','deferred','dismissed','closed')), "
    "due_day TEXT, assigned_day TEXT, car TEXT, origin_day TEXT NOT NULL, seq INTEGER NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS warehouse_event(seq INTEGER PRIMARY KEY AUTOINCREMENT, isn TEXT NOT NULL, kind TEXT NOT NULL, "
    "plan_day TEXT NOT NULL, plan_rev INTEGER NOT NULL, at TEXT NOT NULL, actor TEXT, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS warehouse_event_order ON warehouse_event(isn,seq)",
)


def rows(conn):
    keys = ('isn', 'customer_id', 'order_date', 'invoice', 'state', 'due_day', 'assigned_day', 'car',
            'origin_day', 'seq', 'updated_at')
    out = {r[0]: dict(zip(keys, r)) for r in conn.execute('SELECT * FROM warehouse_order')}
    for row in out.values():
        row['fact_days'] = []
    for isn, day in conn.execute("SELECT DISTINCT isn,plan_day FROM warehouse_event WHERE kind='assigned'"):
        if isn in out:
            out[isn]['fact_days'].append(day)
    return out


def transition(conn, isn, state, day, rev, at, actor, *, due=None, car=None, evidence=None):
    row = conn.execute('SELECT state,due_day,assigned_day,car FROM warehouse_order WHERE isn=?', (isn,)).fetchone()
    if row is None:
        return
    assigned = day if state == 'assigned' else None
    if row == (state, due, assigned, car):
        return
    data = json.dumps({'state': state, 'due_day': due, 'car': car,
                       **({'evidence': evidence} if evidence is not None else {})}, sort_keys=True)
    seq = conn.execute('INSERT INTO warehouse_event(isn,kind,plan_day,plan_rev,at,actor,data) VALUES(?,?,?,?,?,?,?)',
                       (isn, state, day, rev, at, actor, data)).lastrowid
    conn.execute('UPDATE warehouse_order SET state=?,due_day=?,assigned_day=?,car=?,seq=?,updated_at=? WHERE isn=?',
                 (state, due, assigned, car, seq, at, isn))


def discover(conn, known, day, rev, at, actor):
    """Снимок может впервые познакомить с заказом; повторный снимок не меняет его состояние."""
    for isn, value in known.items():
        cid, order_date, *invoice = value
        date.fromisoformat(str(order_date))
        inv = invoice[0] if invoice else ''
        inserted = conn.execute("INSERT OR IGNORE INTO warehouse_order(isn,customer_id,order_date,invoice,state,origin_day,seq,updated_at) "
                                "VALUES(?,?,?,?,'warehouse',?,0,?)", (isn, cid, str(order_date), inv, day, at)).rowcount
        if inserted:
            seq = conn.execute('INSERT INTO warehouse_event(isn,kind,plan_day,plan_rev,at,actor,data) VALUES(?,?,?,?,?,?,?)',
                               (isn, 'discovered', day, rev, at, actor, '{}')).lastrowid
            conn.execute('UPDATE warehouse_order SET seq=? WHERE isn=?', (seq, isn))
        if inv:
            conn.execute('UPDATE warehouse_order SET invoice=? WHERE isn=?', (inv, isn))


def _settings(conn):
    return {k: json.loads(v) for k, v in conn.execute("SELECT key,value FROM settings WHERE key IN ('workdays','holidays')")}


def decisions(conn, old, new, day, rev, at, actor, context=None):
    from . import dispatch as dp
    settings = _settings(conn)
    workdays = settings.get('workdays', (1, 2, 3, 4, 5, 6))
    holidays = dp.holidays_of(settings)
    current = rows(conn)
    action = (context or {}).get('action')
    selected = set((context or {}).get('selected', ()))
    include = new.added - old.added
    if action == 'include':
        include |= selected
    for isn in include:
        row = current.get(isn)
        if row is not None and row['state'] == 'closed':
            raise dp.DispatchError('Պատվերն արդեն հանձնված է․ թարմացրեք էջը')
        if row is not None and row['state'] == 'assigned' and row['assigned_day'] != day:
            if (context or {}).get('bootstrap'):
                continue  # Старый снимок не доказывает возврат; отдельная сверка требует факта терминала.
            raise dp.DispatchError('Պատվերն արդեն այլ օրվա վարորդի մոտ է․ նախ ճշտեք ապրանքի վերադարձը պահեստ')
        if row is not None and row['state'] == 'assigned':
            continue  # Повторное «добавить» не отменяет уже отправленное назначение этого дня.
        transition(conn, isn, 'warehouse', day, rev, at, actor)
    cancelled = (new.excluded - old.excluded) | (new.dismissed - old.dismissed) | (new.dropped - old.dropped)
    if action == 'exclude':
        cancelled |= selected
    for isn in cancelled - new.deferred - set(new.later):
        transition(conn, isn, 'dismissed', day, rev, at, actor)
    for isn in new.deferred - old.deferred:
        due = dp.next_workday(date.fromisoformat(day), workdays, holidays).isoformat()
        transition(conn, isn, 'deferred', day, rev, at, actor, due=due)
    for isn, value in new.later.items():
        if old.later.get(isn) != value:
            due = dp.later_day(value[0], workdays, holidays).isoformat()
            transition(conn, isn, 'deferred', day, rev, at, actor, due=due)


def publication(conn, draft, day, rev, at, actor):
    sent = draft.for_drivers()
    if sent.released is None:
        return
    published = (sent.sent or {}).get('orders')
    current = rows(conn)
    if published is None:
        owners = {cid: trip.truck for trip in sent.trips for cid in trip.stops}
        published = {isn: [owners[r['customer_id']]] for isn, r in current.items() if r['customer_id'] in owners}
    from .dispatch import DispatchError
    for isn, value in published.items():
        row = current.get(isn)
        if row is not None and row['state'] == 'closed':
            raise DispatchError('Հանձնված պատվերը կրկին ուղարկել չի կարելի')
        if row is not None and row['state'] == 'dismissed':
            raise DispatchError('Պատվերը նշված է «Չտանել»․ թարմացրեք էջը և նախ փոխեք որոշումը')
        if row is not None and row['state'] == 'deferred' and (row['due_day'] or '') > day:
            raise DispatchError('Պատվերը տեղափոխված է ավելի ուշ օրվա․ թարմացրեք էջը')
        if row is not None and row['state'] == 'assigned' and row['assigned_day'] != day:
            raise DispatchError('Պատվերն արդեն այլ օրվա վարորդի մոտ է․ թարմացրեք էջը')
        transition(conn, isn, 'assigned', day, rev, at, actor, car=value[0])


def record_plan(conn, old_raw, new_raw, day, rev, at, actor, context=None):
    from .dispatch import Draft
    old, new = Draft.from_json(old_raw), Draft.from_json(new_raw)
    known = {isn: [cid, od.isoformat()] for isn, (cid, od) in new.auto_carried.items()}
    known.update((context or {}).get('known', {}))
    discover(conn, known, day, rev, at, actor)
    current = rows(conn)
    for isn in (context or {}).get('pending', ()):
        row = current.get(isn)
        proof = ((context or {}).get('pending') or {}).get(isn, {})
        if (row is not None and row['state'] == 'assigned' and proof.get('day') == row['assigned_day']
                and proof.get('seq') == row['seq']):
            transition(conn, isn, 'warehouse', day, rev, at, actor, evidence=proof)
    for isn in (context or {}).get('closed', ()):
        transition(conn, isn, 'closed', day, rev, at, actor)
    decisions(conn, old, new, day, rev, at, actor, context)
    if old.sent != new.sent:
        publication(conn, new, day, rev, at, actor)


def _stamp(value, *, local=False):
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone(timedelta(hours=4)) if local else timezone.utc)
        return moment.timestamp()
    except (TypeError, ValueError):
        return 0.0


def bootstrap(conn):
    """Однократный перенос известных остатков: публикации и решения по реальному времени, не дате маршрута."""
    if conn.execute("SELECT value FROM meta WHERE key='warehouse_initialized'").fetchone():
        return
    from .dispatch import Draft
    plans = conn.execute('SELECT day,data,rev,updated_at,updated_by FROM dispatch_plan ORDER BY updated_at,day').fetchall()
    events = []
    for day, raw, rev, at, actor in plans:
        try:
            draft = Draft.from_json(json.loads(raw))
        except (TypeError, ValueError):
            continue
        discover(conn, {isn: [cid, od.isoformat()] for isn, (cid, od) in draft.auto_carried.items()}, day, rev, at, actor)
        if draft.sent is not None:
            pub_at = draft.sent.get('at') or at
            events.append((_stamp(pub_at, local=True), 0, day, rev, pub_at, actor, 'publish', draft))
        elif draft.released is not None:
            events.append((_stamp(at), 0, day, rev, at, actor, 'publish', draft))
        prior = draft.for_drivers() if draft.sent is not None else Draft()
        events.append((_stamp(at), 1, day, rev, at, actor, 'decision', (prior, draft)))
    for _, _, day, rev, at, actor, kind, value in sorted(events, key=lambda x: x[:3]):
        if kind == 'publish':
            # Историческая публикация — достоверный переход; более раннюю может законно сменить следующая.
            sent = value.for_drivers()
            for isn, order in ((sent.sent or {}).get('orders') or {}).items():
                transition(conn, isn, 'assigned', day, rev, at, actor, car=order[0])
        else:
            decisions(conn, value[0], value[1], day, rev, at, actor, {'bootstrap': True})
    conn.execute("INSERT INTO meta(key,value) VALUES('warehouse_initialized','1')")

"""Замена дня: проверки факта, свободной машины и атомарная пересадка экипажа с планом."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date

from flask import session

from . import dispatch as dp, replacement as rp
from .store import _crew_upsert, _crew_restore_day
from .geo import is_valid_point


def _snapshot(conn, day, codes):
    names, rows = {}, {}
    for role in ('driver', 'helper'):
        table = 'truck_' + role
        permanent = conn.execute(f'SELECT d.car_code, d.name FROM {table} d WHERE only_day=0 AND from_day='
                                 f'(SELECT MAX(x.from_day) FROM {table} x WHERE x.car_code=d.car_code '
                                 'AND x.only_day=0 AND x.from_day<=?)', (day,)).fetchall()
        subs = conn.execute(f'SELECT car_code,name FROM {table} WHERE only_day=1 AND from_day=?', (day,)).fetchall()
        names[role] = dict(permanent) | dict(subs)
        rows[role] = {c: (list(row) if (row := conn.execute(
            f'SELECT name,updated_at,updated_by FROM {table} WHERE car_code=? AND from_day=? AND only_day=1',
            (c, day)).fetchone()) else None) for c in codes}
    absent = list(map(list, conn.execute('SELECT name,MAX(to_day) FROM driver_absence '
                         'WHERE from_day<=? AND to_day>=? GROUP BY name ORDER BY name', (day, day)).fetchall()))
    return {'names': names, 'rows': rows, 'absent': absent}


def _facts(state, dd):
    from .views import _same_day_now
    if dd.day < _same_day_now().date():
        raise dp.DispatchError('Անցած օրվա մեքենան չի փոխարինվում')
    if dd.day == _same_day_now().date():
        if state.live_facts is None and dd.draft.sent is not None:
            raise dp.DispatchError('Առաքումների փաստը հասանելի չէ․ փոխարինելուց առաջ վերականգնեք կապը «Առաքիչ»-ի հետ')
        facts = state.live_facts
        try:
            return getattr(facts, 'fresh_fleet', facts.fleet)(dd.day.isoformat()) if facts is not None else {}
        except (OSError, RuntimeError) as e:
            raise dp.DispatchError('Առաքումների փաստը հասանելի չէ․ վերականգնեք կապը «Առաքիչ»-ի հետ և կրկնեք') from e
    return {}


def _crew(state, dd, source, snapshot):
    from .views import _crew as crew_of
    all_crew, away = crew_of(state, dd.day, dd.bundle.trucks)
    v = dp.crew_view(dd.draft, all_crew).get(source) or {'name': snapshot['names']['driver'].get(source)}
    if not v.get('name') or v.get('warn'):
        raise dp.DispatchError('Նախ նշեք մեքենայի աշխատող վարորդին')
    if v['name'] in away:
        raise dp.DispatchError('Մեքենայի վարորդը բացակայում է')
    helper = snapshot['names']['helper'].get(source, '')
    if helper and helper == v['name']:
        raise dp.DispatchError('Վարորդն ու առաքիչը նույն մարդն են')
    if helper and helper in away:
        raise dp.DispatchError('Մեքենայի առաքիչը բացակայում է')
    return {'driver': v['name'], 'helper': helper}


def _token(dd, source, work, snapshot, config=None):
    raw = json.dumps([dd.rev, source, work, snapshot, config], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def source_context(state, dd, source):
    """Источник с заданием может быть отключён; получатели остаются только из активного парка."""
    if source not in dd.ctx.trucks:
        from .views import _ready_trucks
        old = _ready_trucks(dd.snap, dd.bundle, active_only=False).get(source)
        if old is None:
            raise dp.DispatchError('Սկզբնական մեքենայի նորմերը բացակայում են․ լրացրեք տարողությունն ու ծախսը')
        dd = replace(dd, ctx=replace(dd.ctx, trucks={**dd.ctx.trucks, source: old}))
    return dd


def configuration(payload):
    mode = payload.get('crew_mode', 'source')
    if mode not in ('source', 'target'):
        raise dp.DispatchError('Ընտրեք՝ որ անձնակազմն է շարունակում աշխատանքը')
    raw = payload.get('handover')
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or raw.get('mode', 'depot') not in ('depot', 'point'):
        raise dp.DispatchError('Ընտրեք փոխանցման վայրը')
    delay = raw.get('delay_min', 0)
    if not dp._is_num(delay) or not 0 <= delay <= 480:
        raise dp.DispatchError('Փոխանցման և սպասման ժամանակը՝ 0–480 րոպե')
    point = raw.get('point') if raw.get('mode') == 'point' else None
    if point is not None and (not isinstance(point, list) or len(point) != 2
                             or not all(dp._is_num(x) for x in point) or not is_valid_point(*point)):
        raise dp.DispatchError('Նշեք փոխանցման վայրի ճիշտ կոորդինատները')
    if raw.get('mode') == 'point' and point is None:
        raise dp.DispatchError('Նշեք փոխանցման վայրի կոորդինատները')
    return {'crew_mode': mode, 'handover': {'mode': raw.get('mode', 'depot'), 'delay_min': float(delay),
                                           **({'point': point} if point else {})}}


def _store_reason(dd, target, cids):
    """Какие магазины target не может везти и по какому правилу. Запрет уже решён пробным расчётом, здесь только текст."""
    from .views import _stop_info
    stops = {s.customer_id: s for s in dd.stops}
    center_ok = dd.ctx.trucks[target].center_ok
    banned = [c for c in sorted(cids) if c in stops and (not dp._vehicle_ok(dd.ctx, c, target)
                                                         or (dp._central(dd.ctx, stops[c]) and not center_ok))]
    info = _stop_info(dd)

    def clause(cid):
        view = info(stops[cid])
        store = f'«{view["name"] or view["code"]}»'
        rule = dd.ctx.vehicle_access.get(cid)
        if rule is None or rule.allows(target):
            return f'{store} խանութը կենտրոնում է, իսկ մեքենան կենտրոն մուտք գործելու թույլտվություն չունի'
        if rule.mode == 'deny':
            return f'{store} խանութին այս մեքենան արգելված է'
        if not rule.trucks:
            return f'{store} խանութին թույլատրված մեքենա չկա'
        place = f'{store} խանութը կենտրոնում է,' if dp._central(dd.ctx, stops[cid]) else f'{store} խանութին'
        return f'{place} թույլատրված է միայն {", ".join(rule.trucks)}'

    shown = '; '.join(clause(c) for c in banned[:3])
    return shown + (f'; և ևս {len(banned) - 3} խանութ' if len(banned) > 3 else '')


def options(state, dd, payload):
    source = payload.get('truck')
    if not isinstance(source, str):
        raise dp.DispatchError('Ընտրեք փոխարինվող մեքենան')
    dd = source_context(state, dd, source)
    from .assignment import require_ready
    require_ready(dd.draft)
    config = configuration(payload)
    fleet = _facts(state, dd)
    work = rp.work(dd, source, fleet)
    snapshot = state.store._read(lambda c: _snapshot(c, dd.day.isoformat(), []))
    crew = _crew(state, dd, source, snapshot) if config['crew_mode'] == 'source' else {}
    choices = []
    from .views import _today_min
    for target in sorted(dd.ctx.trucks):
        if target == source or target in rp.broken(dd.draft) or any(t.truck == target for t in dd.draft.trips):
            continue
        reason = None
        target_crew = crew
        if config['crew_mode'] == 'target':
            try:
                target_crew = _crew(state, dd, target, snapshot)
            except dp.DispatchError as e:
                reason = str(e)
        from .views import _crew as crew_of
        active_crew = dp.crew_view(dd.draft, crew_of(state, dd.day, dd.bundle.trucks)[0])
        chosen_people = {n for n in target_crew.values() if n}
        if chosen_people and any(t.truck not in (source, target) and chosen_people.intersection({
                (active_crew.get(t.truck) or {}).get('name'), snapshot['names']['helper'].get(t.truck)})
                for t in dd.draft.trips):
            reason = 'Անձնակազմի անդամն արդեն աշխատում է այլ մեքենայում'
        if any(s.get('status') not in rp.CLOSED for s in (fleet.get(target) or {}).get('stops', [])):
            reason = 'Մեքենան ունի չավարտված առաքումներ'
        if reason is None:
            try:
                trial = rp.apply(dd, source, target, work, {'confirm_loaded': True, 'confirm_reserve': True, **config},
                                 dd.day.isoformat(), None, target_crew, {}, _today_min(dd))
                remaining = [replace(s, orders=tuple(o for o in s.orders if o.isn not in rp.completed(trial)),
                                     kg=sum(o.kg for o in s.orders if o.isn not in rp.completed(trial)),
                                     revenue=sum(o.revenue for o in s.orders if o.isn not in rp.completed(trial)))
                             for s in dd.stops if any(o.isn not in rp.completed(trial) for o in s.orders)]
                plan = dp.plan_view(dd.ctx, remaining, trial, lambda s: {'customer_id': s.customer_id}, explain=False)
                trip_views = [tr for t in plan['trucks'] if t['car_code'] == target for tr in t['trips']]
            except dp.StoreTruckError as e:
                reason = _store_reason(dd, target, {*e.cids, *work['customers']}) or str(e)
            except dp.DispatchError as e:
                reason = str(e)
        choices.append({'car_code': target, 'name': dd.ctx.trucks[target].name or target,
                        'capacity_kg': dd.ctx.trucks[target].capacity_kg, 'reason': reason, 'crew': target_crew,
                        **({'depart': trip_views[0]['depart'], 'return': trip_views[-1]['return'],
                            'warnings': sum(t['window_miss'] for t in trip_views)} if reason is None and trip_views else {})})
    from .views import _same_day_now
    tracks = (fleet.get(source) or {}).get('track') or []
    point = None
    if tracks:
        last = tracks[-1]
        from .actuals import YEREVAN
        now = _same_day_now().replace(tzinfo=YEREVAN)
        if len(last) >= 4 and 0 <= now.timestamp() * 1000 - last[0] <= 15 * 60 * 1000 and is_valid_point(last[1], last[2]) and (last[3] is None or last[3] <= 100):
            point = {'lat': last[1], 'lon': last[2], 'at_ms': last[0]}
    return {'truck': source, 'choices': choices, 'crew': crew, 'work': work, 'source_point': point,
            'signature': _token(dd, source, work, snapshot, config), 'config': config,
            'sync_required': dd.day == _same_day_now().date() and dd.draft.sent is not None,
            'loaded': any(t.loaded is not None for t in dd.draft.trips if t.truck == source)}


def replace_truck(state, dd, payload, now_min):
    source, target = payload.get('truck'), payload.get('other_truck')
    if not all(isinstance(c, str) for c in (source, target)):
        raise dp.DispatchError('Ընտրեք փոխարինվող և ազատ մեքենաները')
    dd = source_context(state, dd, source)
    option = options(state, dd, payload)
    if payload.get('replacement_signature') != option['signature']:
        raise dp.DispatchError('Փոխվել են առաքումները կամ անձնակազմը․ փակեք պատուհանը և նորից ընտրեք մեքենան')
    choice = next((t for t in option['choices'] if t['car_code'] == target), None)
    if choice is None or choice['reason']:
        raise dp.DispatchError(choice['reason'] if choice else 'Ընտրեք ազատ մեքենա ցուցակից')
    if option['sync_required'] and payload.get('confirm_synced') is not True:
        raise dp.DispatchError('Նախ համաժամացրեք վարորդի տերմինալը և հաստատեք, որ չուղարկված աշխատանք չկա')
    if option['loaded'] and payload.get('confirm_loaded') is not True:
        raise dp.LoadedEdit(dp.LOADED_EDIT)
    d = dd.day.isoformat()
    snapshot = state.store._read(lambda c: _snapshot(c, d, [source, target]))
    token_snapshot = {**snapshot, 'rows': {'driver': {}, 'helper': {}}}
    if _token(dd, source, option['work'], token_snapshot, option['config']) != option['signature']:
        raise dp.DispatchError('Անձնակազմը փոխվել է․ թարմացրեք էջը և կրկնեք')
    from .views import _same_day_now
    at = _same_day_now().isoformat(timespec='seconds')
    names = choice['crew']
    draft = rp.apply(dd, source, target, option['work'], {**payload, **option['config']}, at, session.get('username'),
                     names, snapshot['rows'], now_min)

    def also(conn):
        current = _snapshot(conn, d, [source, target])
        if current != snapshot:
            raise dp.DispatchError('Անձնակազմը փոխվել է․ թարմացրեք էջը և կրկնեք')
        for role in ('driver', 'helper'):
            _crew_upsert(conn, 'truck_' + role, source, d, True, '', session.get('username'))
            _crew_upsert(conn, 'truck_' + role, target, d, True, names[role], session.get('username'))
    return draft, also


def restore_crew(state, day, before, after):
    """Undo/discard восстанавливает точные дневные подмены в транзакции плана; чужой выбор не перезаписывается."""
    common = 0
    for a, b in zip(before, after):
        if a != b:
            break
        common += 1
    if after[common:]:
        raise dp.DispatchError('Փոխարինման պատմությունը չի համընկնում․ թարմացրեք էջը')
    removed = before[common:]
    d = day.isoformat()

    def also(conn):
        for r in reversed(removed):
            a, b = r['from'], r['to']
            current = _snapshot(conn, d, [a, b])
            for role in ('driver', 'helper'):
                if current['names'][role].get(a, '') != '' or current['names'][role].get(b, '') != r['crew'][role]:
                    raise dp.DispatchError('Անձնակազմը փոխվել է․ փոխարինումը չի չեղարկվել, որպեսզի նոր ընտրությունը չկորչի')
                for code in (a, b):
                    old = r['crew_before'][role][code]
                    _crew_restore_day(conn, role, code, d, old)
    return also

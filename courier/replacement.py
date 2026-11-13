"""Выдача оставшихся документов замене без изменения накладных ERP и старых снимков."""
from dataclasses import replace


def closed_stop_ids(store, day):
    """Поздняя синхронизация старой машины не открывает доставку повторно у замены."""
    from . import merge as mg
    from .clock import parse_moment
    snaps = store.day_snapshots(day)
    versions, latest = {}, {}
    for snapshot, car, data in snaps.stops:
        sid = data['stop_id']
        versions.setdefault(sid, {})[snapshot] = data
        if sid not in latest or snapshot > latest[sid][0]:
            latest[sid] = (snapshot, data)
    events = []
    for e in store.events_for_day(day, skip_track=True):
        at = parse_moment(e.get('at_utc') or e.get('at'))
        if at is not None and e.get('stop_id') in versions:
            events.append({**e, 'at': at})
    statements = mg.latest_by_stop(events, 'delivery')
    stops = []
    for sid, (_, data) in latest.items():
        event = statements.get(sid)
        basis = (versions[sid].get(event.get('snapshot_id')) if event else None) or data
        stops.append({'stop_id': sid, 'source': 'order' if sid.startswith('O:') else 'invoice',
                      'collect': basis.get('collect'), 'current': True,
                      'lines': [{k: ln.get(k) for k in ('line_id', 'product_id', 'qty', 'price', 'gift')}
                                for ln in basis.get('lines', [])], 'replaces': data.get('replaces', [])})
    result = mg.merge(stops, events)
    return {sid for sid, view in result.stops.items() if view.status in ('full', 'partial', 'refused', 'covered')}


def owner(records, car, stop_id, replaces=()):
    identities = {stop_id, *replaces}
    for r in records:
        if car == r['from'] and identities.intersection(r['stop_ids']):
            car = r['to']
    return car


def apply(data, view, load):
    records = view.replacements if view.released else ()
    if not records:
        return data
    car = data.car_code
    docs = {d.stop_id: d for d in data.docs if owner(records, car, d.stop_id, d.replaces) == car}
    merged = data
    for source in sorted({r['from'] for r in records} - {car}):
        # Не делать лишние чтения ERP машин, которые не передавали документы этой машине.
        if not any(owner(records, source, sid) == car for r in records for sid in r['stop_ids'] if r['from'] == source):
            continue
        incoming = load(source)
        selected = [d for d in incoming.docs if owner(records, source, d.stop_id, d.replaces) == car]
        if not selected:
            continue
        for d in selected:
            # Накладная уже заменяет заказ; одна и та же сумма не появляется двумя строками.
            for sid in d.replaces:
                docs.pop(sid, None)
            docs[d.stop_id] = d
        maps = ('lines', 'products', 'gtins', 'customers', 'agents', 'tare_names', 'gps', 'gtin_units', 'gift_rules')
        merged = replace(merged, **{k: {**getattr(incoming, k), **getattr(merged, k)} for k in maps},
                         debts=None if incoming.debts is None or merged.debts is None
                         else {**incoming.debts, **merged.debts},
                         containers=tuple(dict.fromkeys((*merged.containers, *incoming.containers))))
    absorbed = {sid for d in docs.values() if d.source == 'invoice' for sid in d.replaces}
    return replace(merged, docs=tuple(d for sid, d in docs.items() if sid not in absorbed))

# -*- coding: utf-8 -*-
"""Точки магазинов по GPS водителей, предложения водителей и правка логиста (docs/plans/driver-geo-plan.md).

Синтетические данные, без ERP; courier.db и база «Маршрутов» — только временные.
Запуск из корня проекта:  python -m pytest tests/test_driver_geo.py -q
"""
import random
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import route_optimizer  # noqa: E402
from courier import clock, events as ev  # noqa: E402
from courier.geo import ArrivedFix, DriverSource, driver_point, driver_points  # noqa: E402
from courier.store import SCHEMA_VERSION, Store  # noqa: E402
from route_optimizer import evaluate, geo  # noqa: E402
from route_optimizer import store as rst  # noqa: E402
from test_route_optimizer import (_dispatch_setup, _dorder, _no_road_map, client,  # noqa: E402,F401
                                  make_snapshot)

NOW = datetime(2026, 10, 2, 9, 0, tzinfo=clock.YEREVAN)
SHOP = (40.18500, 44.50500)          # где реально разгружается грузовик
NEAR_M = 0.0003                      # ≈ 30 м по широте


def _fix(day, lat, lon, acc=10.0):
    return ArrivedFix(day, lat, lon, acc)


# ============================== правило медианы и разброса (§2) ==============================

def test_driver_point_needs_three_days():
    two = [_fix('2026-09-01', *SHOP), _fix('2026-09-01', *SHOP), _fix('2026-09-02', *SHOP)]
    assert driver_point(two) is None                       # 3 отметки, но 2 дня
    three = two + [_fix('2026-09-05', SHOP[0] + NEAR_M, SHOP[1])]
    lat, lon, days = driver_point(three)
    assert days == 3 and abs(lat - SHOP[0]) < 1e-9 and lon == SHOP[1]   # медиана по осям отдельно


def test_driver_point_accuracy_and_armenia_filters():
    good = [_fix(f'2026-09-0{i}', *SHOP) for i in (1, 2, 3)]
    assert driver_point(good) == (SHOP[0], SHOP[1], 3)
    assert driver_point([*good[:2], _fix('2026-09-03', *SHOP, acc=50.0)]) == (SHOP[0], SHOP[1], 3)   # 50 м — ещё да
    assert driver_point([*good[:2], _fix('2026-09-03', *SHOP, acc=50.1)]) is None    # хуже 50 м — не считается
    assert driver_point([*good[:2], _fix('2026-09-03', *SHOP, acc=None)]) is None    # точность не прислана
    assert driver_point([*good[:2], _fix('2026-09-03', *SHOP, acc=True)]) is None
    assert driver_point([*good[:2], _fix('2026-09-03', 55.75, 37.61)]) is None       # вне Армении
    assert driver_point([*good[:2], _fix('2026-09-03', 'x', SHOP[1])]) is None
    assert driver_point([]) is None


def test_driver_point_spread_two_thirds():
    far = (SHOP[0] + 0.01, SHOP[1])                       # ≈ 1,1 км
    close = [_fix(f'2026-09-{d:02d}', SHOP[0] + k * NEAR_M / 3, SHOP[1]) for k, d in enumerate((1, 2, 3, 4))]
    # 4 дня из 6 рядом с медианой — ровно 2/3: точка есть; дней — только рядом с медианой
    p = driver_point(close + [_fix('2026-09-10', *far), _fix('2026-09-11', *far)])
    assert p is not None and geo.haversine_km(p[:2], SHOP) * 1000 < 150 and p[2] == 4
    # 3 из 5 рядом — меньше 2/3: разброс большой, точки нет
    assert driver_point(close[:3] + [_fix('2026-09-10', *far), _fix('2026-09-11', *far)]) is None
    # граница 150 м: точка в 140 м от медианы — «рядом»
    edge = SHOP[0] + 140 / 111195
    p = driver_point([_fix('2026-09-01', *SHOP), _fix('2026-09-02', *SHOP), _fix('2026-09-03', edge, SHOP[1])])
    assert p == (SHOP[0], SHOP[1], 3)


OFF = SHOP[0] + 330 / 111195        # ≈ 330 м к северу — «не там»


def test_driver_point_one_fix_per_day():
    """Несколько накладных в один день не перевешивают другие дни: день — одна (самая точная) отметка."""
    bulk = [_fix('2026-09-01', OFF, SHOP[1], acc) for acc in (5.0, 6.0, 7.0, 8.0)]   # 4 доставки за день — мимо
    good = [_fix('2026-09-02', *SHOP), _fix('2026-09-03', *SHOP)]
    assert driver_point(bulk + good) is None                 # дней рядом с медианой — 2 из 3: точки нет
    lat, lon, days = driver_point(bulk + good + [_fix('2026-09-04', *SHOP)])
    assert (lat, lon, days) == (SHOP[0], SHOP[1], 3)          # медиана по дням — правильное место
    # в день — самая точная отметка: неточная (40 м) мимо, точная (5 м) — на месте
    mixed = [_fix(d, OFF, SHOP[1], 40.0) for d in ('2026-09-01', '2026-09-02', '2026-09-03')]
    mixed += [_fix(d, *SHOP, 5.0) for d in ('2026-09-01', '2026-09-02', '2026-09-03')]
    assert driver_point(mixed) == (SHOP[0], SHOP[1], 3)


def test_driver_point_days_counted_near_median_only():
    two_good_one_off = [_fix('2026-09-01', *SHOP), _fix('2026-09-02', *SHOP), _fix('2026-09-03', OFF, SHOP[1])]
    assert driver_point(two_good_one_off) is None             # 3 дня, но рядом с медианой — 2
    p = driver_point(two_good_one_off + [_fix('2026-09-04', *SHOP)])
    assert p == (SHOP[0], SHOP[1], 3)                          # 3 из 4 рядом (≥ 2/3), выброс не считается


def test_driver_points_groups_by_customer():
    rows = [(101, f'2026-09-0{i}', SHOP[0], SHOP[1], 5.0) for i in (1, 2, 3)]
    rows += [(102, '2026-09-01', 40.2, 44.6, 5.0), (102, '2026-09-02', 40.2, 44.6, 5.0)]   # 2 дня — нет
    assert driver_points(rows) == {101: (SHOP[0], SHOP[1], 3)}


# ============================== приоритет источников ==============================

def test_resolve_coord_priority_with_driver():
    erp_pt, gps_pt, drv, man = (40.18, 44.50), (40.181, 44.501), (40.185, 44.505), (40.3, 44.3)
    assert geo.resolve_coord(man, erp_pt, gps_pt, driver=drv) == geo.Coord(*man, 'manual')
    assert geo.resolve_coord(None, erp_pt, gps_pt, driver=drv) == geo.Coord(*drv, 'driver')
    assert geo.resolve_coord(None, None, None, driver=drv) == geo.Coord(*drv, 'driver')
    assert geo.resolve_coord(None, erp_pt, gps_pt, driver=None).source == 'erp'
    snap = make_snapshot()
    assert evaluate.visit_coord(snap, 101, 1001, {}, {101: drv}) == geo.Coord(*drv, 'driver')      # над ERP
    assert evaluate.visit_coord(snap, 103, 0, None, {103: drv}) == geo.Coord(*drv, 'driver')       # над GPS
    assert evaluate.visit_coord(snap, 101, 1001, {101: man}, {101: drv}) == geo.Coord(*man, 'manual')
    assert evaluate.visit_coord(snap, 999, 0, None, {999: drv}) == geo.Coord(*drv, 'driver')       # нет ни ERP, ни GPS
    assert evaluate.visit_coord(snap, 102, 0, None, {101: drv}).source == 'erp'                     # чужая точка


def _old_resolve(manual, erp, gps, max_gap_km=geo.ERP_GPS_MAX_GAP_KM):
    """resolve_coord до точек водителей (копия) — эталон неизменности."""
    if manual is not None:
        return geo.Coord(manual[0], manual[1], 'manual')
    if erp is not None:
        if gps is not None and geo.haversine_km(erp, gps) > max_gap_km:
            return geo.Coord(gps[0], gps[1], 'gps')
        return geo.Coord(erp[0], erp[1], 'erp')
    if gps is not None:
        return geo.Coord(gps[0], gps[1], 'gps')
    return geo.NO_COORD


def _old_visit_coord(snap, customer_id, address_id, manual=None):
    if manual and customer_id in manual:
        return _old_resolve(manual[customer_id], None, None)
    erp = None
    for addr in (address_id, snap.default_address.get(customer_id)):
        entry = snap.erp_points.get(addr) if addr else None
        if entry is not None and entry[0] == customer_id:
            erp = entry[1]
            break
    return _old_resolve(None, erp, snap.gps_points.get(customer_id))


def test_no_driver_points_is_exactly_old_coord():
    """Инвариант §2: без точек водителей координата — ровно прежняя (случайные снимки, ручные точки, адреса)."""
    rng = random.Random(43)
    pt = lambda: (round(40.0 + rng.random() * 0.5, 6), round(44.3 + rng.random() * 0.6, 6))  # noqa: E731
    for _ in range(300):
        cids = list(range(1, 30))
        snap = SimpleNamespace(
            erp_points={1000 + c: (c if rng.random() < 0.9 else c + 1, pt()) for c in cids if rng.random() < 0.7},
            default_address={c: 1000 + c for c in cids if rng.random() < 0.6},
            gps_points={c: pt() for c in cids if rng.random() < 0.5})
        manual = {c: pt() for c in cids if rng.random() < 0.15} if rng.random() < 0.7 else None
        for c in cids + [999]:
            addr = rng.choice([0, 1000 + c, 1000 + c + 1, 5])
            old = _old_visit_coord(snap, c, addr, manual)
            assert evaluate.visit_coord(snap, c, addr, manual) == old
            assert evaluate.visit_coord(snap, c, addr, manual, {}) == old
            assert evaluate.visit_coord(snap, c, addr, manual, None) == old
    for _ in range(300):
        args = [pt() if rng.random() < 0.6 else None for _ in range(3)]
        assert geo.resolve_coord(*args) == geo.resolve_coord(*args, driver=None) == _old_resolve(*args)


def test_bundle_fingerprint_unchanged_without_driver_points(tmp_path):
    bundle = rst.Store(str(tmp_path / 'routes.db')).load()
    assert replace(bundle, driver_points={}).fingerprint() == bundle.fingerprint()
    assert replace(bundle, driver_points={101: SHOP}).fingerprint() != bundle.fingerprint()


# ============================== courier.db: geo_suggest, решения, миграция ==============================

@pytest.fixture
def cstore(tmp_path, monkeypatch):
    monkeypatch.setattr(clock, 'now', lambda: NOW)
    return Store(str(tmp_path / 'courier.db'))


def _sid(n):
    return 'S:%08d-2222-4222-8222-222222222222' % n


def _who(store, car='CAR1'):
    did = store.save_driver(None, 'Արամ', True, '1234', 'admin')
    terminal, _ = store.create_terminal('Urovo 1', car, 'admin')
    return ev.Who(terminal.id, car, did, 'Արամ')


def _save_day(store, day, stops, car='CAR1'):
    """Снимок /day: stops — [(stop_id, клиент)]."""
    store.save_day(day, car, [{'stop_id': sid, 'seq': i, 'customer': {'id': cid, 'code': f'C{cid}', 'name': f'Խանութ {cid}'},
                               'lines': []} for i, (sid, cid) in enumerate(stops, 1)], 'v-' + day, day + 'T08:00:00+04:00')


_N = iter(range(1, 10 ** 6))


def _event(etype, sid, payload, day='2026-10-01'):
    return {'id': '%08d-3333-4333-8333-333333333333' % next(_N), 'type': etype, 'stop_id': sid, 'date': day,
            'at': day + 'T10:00:00+04:00', 'payload': payload}


def _suggest(sid, lat=40.1860, lon=44.5060, acc=8.0, **kw):
    return _event('geo_suggest', sid, {'lat': lat, 'lon': lon, 'accuracy': acc, **kw})


def test_geo_suggest_validation(cstore):
    who = _who(cstore)
    _save_day(cstore, '2026-10-01', [(_sid(1), 101)])
    ok = [_suggest(_sid(1)), _suggest(_sid(1), note='Մուտքը բակից է'), _suggest(_sid(1), acc=100), _suggest(_sid(1), note=None)]
    bad = [_suggest(_sid(1), lat=55.75, lon=37.61),                 # вне Армении
           _suggest(_sid(1), acc=None), _suggest(_sid(1), acc=0), _suggest(_sid(1), acc=100.5), _suggest(_sid(1), acc='5'),
           _suggest(_sid(1), note='x' * 201), _suggest(_sid(1), note=5),
           _suggest(_sid(1), lat='40.1'), _suggest(None)]           # stop_id обязателен
    r = ev.ingest(cstore, who, ok + bad).json()
    assert r['accepted'] == [e['id'] for e in ok]
    assert [x['id'] for x in r['rejected']] == [e['id'] for e in bad]
    assert 'Հայաստանից' in r['rejected'][0]['message']
    sugs = cstore.open_suggestions()
    assert len(sugs) == 4 and {s['customer_id'] for s in sugs} == {101}
    s = next(x for x in sugs if x['note'])
    assert (s['code'], s['name'], s['driver_name'], s['date'], s['lat'], s['accuracy']) == \
        ('C101', 'Խանութ 101', 'Արամ', '2026-10-01', 40.186, 8.0)


def test_geo_suggest_decision(cstore):
    who = _who(cstore)
    _save_day(cstore, '2026-10-01', [(_sid(1), 101), (_sid(2), 102)])
    a, b, b2, unknown = _suggest(_sid(1)), _suggest(_sid(2), lat=40.2), _suggest(_sid(2), lat=40.21), _suggest(_sid(9))
    assert ev.ingest(cstore, who, [a, b, b2, unknown]).json()['rejected'] == []
    assert {s['event_id'] for s in cstore.open_suggestions()} == {a['id'], b['id'], b2['id']}   # точка без клиента — нет

    def boom(_):
        raise RuntimeError('база «Маршрутов» недоступна')
    with pytest.raises(RuntimeError):
        cstore.decide_suggestion(a['id'], 'accepted', 'logist', boom)
    assert {s['event_id'] for s in cstore.open_suggestions()} == {a['id'], b['id'], b2['id']}   # сбой точки — решения нет
    with pytest.raises(ValueError):
        cstore.decide_suggestion(a['id'], 'maybe', 'logist', boom)
    applied = []
    got = cstore.decide_suggestion(a['id'], 'accepted', 'logist', applied.append)
    assert got['customer_id'] == 101 and got['superseded'] == [] and applied == [got]
    assert cstore.decide_suggestion(a['id'], 'rejected', 'other', applied.append) is None      # уже решено
    assert cstore.decide_suggestion(unknown['id'], 'accepted', 'x', applied.append) is None    # клиент неизвестен
    assert cstore.decide_suggestion('nope', 'accepted', 'x', applied.append) is None
    assert len(applied) == 1
    # «Մերժել» закрывает только это предложение; «Ընդունել» — и остальные того же магазина (superseded)
    rej = cstore.decide_suggestion(b['id'], 'rejected', 'logist', lambda s: None)
    assert (rej['customer_id'], rej['superseded']) == (102, [])
    assert [s['event_id'] for s in cstore.open_suggestions()] == [b2['id']]
    c, c2 = _suggest(_sid(1), lat=40.19), _suggest(_sid(1), lat=40.191)
    assert ev.ingest(cstore, who, [c, c2]).json()['rejected'] == []
    got = cstore.decide_suggestion(c2['id'], 'accepted', 'logist', lambda s: None)
    assert got['superseded'] == [c['id']]
    assert [s['event_id'] for s in cstore.open_suggestions()] == [b2['id']]
    with closing(sqlite3.connect(cstore.path)) as conn:
        rows = set(conn.execute('SELECT event_id, decision, decided_by FROM geo_suggest_decision').fetchall())
    assert rows == {(a['id'], 'accepted', 'logist'), (b['id'], 'rejected', 'logist'), (c2['id'], 'accepted', 'logist'),
                    (c['id'], 'rejected', 'auto: superseded')}


def test_arrived_fixes_and_points_from_courier_db(cstore):
    who = _who(cstore)
    for day in ('2026-09-28', '2026-09-29', '2026-09-30'):
        _save_day(cstore, day, [(_sid(1), 101), (_sid(2), 102)])
    evs = [_event('arrived', _sid(1), {'lat': SHOP[0], 'lon': SHOP[1], 'accuracy': 7.0}, d)
           for d in ('2026-09-28', '2026-09-29', '2026-09-30')]
    evs += [_event('arrived', _sid(2), {'lat': 40.2, 'lon': 44.6, 'accuracy': 7.0}, d) for d in ('2026-09-29', '2026-09-30')]
    evs += [_event('arrived', _sid(1), {'lat': SHOP[0], 'lon': SHOP[1], 'accuracy': 7.0}, '2026-01-01')]  # старше 180 дней
    evs += [_event('arrived', _sid(7), {'lat': SHOP[0], 'lon': SHOP[1], 'accuracy': 7.0}, '2026-09-30')]  # точка без клиента
    assert ev.ingest(cstore, who, evs).json()['rejected'] == []
    assert len(cstore.arrived_fixes('2026-04-05', '2026-10-02')) == 5
    src = DriverSource(cstore)
    assert src.points() == {101: (SHOP[0], SHOP[1], 3)}
    # не учитываются: дата позже сегодняшней и date_suspicious (дата не сходится с моментом терминала)
    off = {'lat': OFF, 'lon': SHOP[1], 'accuracy': 3.0}
    future = _event('arrived', _sid(1), off, '2026-10-05')
    suspicious = {**_event('arrived', _sid(1), off, '2026-09-20'), 'at': '2026-09-30T10:00:00+04:00'}
    assert ev.ingest(cstore, who, [future, suspicious]).json()['rejected'] == []
    with closing(sqlite3.connect(cstore.path)) as conn:
        assert 'date_suspicious' in conn.execute('SELECT flags FROM events WHERE id = ?', (suspicious['id'],)).fetchone()[0]
    assert len(cstore.arrived_fixes('2026-04-05', '2026-10-02')) == 5
    assert len(cstore.arrived_fixes('2026-04-05', '2026-10-05')) == 6       # будущая — только если до неё дошли
    assert src.points() == {101: (SHOP[0], SHOP[1], 3)}


def test_driver_source_without_courier_db(tmp_path):
    path = tmp_path / 'none' / 'courier.db'
    src = DriverSource(Store(str(path)))
    assert src.points() == {} and src.suggestions() == [] and src.decide('x', 'accepted', None, print) is None
    assert not path.exists()                               # чтением база не создаётся


V3_DROP = ("DROP TABLE geo_suggest_decision", "DROP INDEX events_type", "UPDATE meta SET value = '3' WHERE key = 'schema_version'")


def test_courier_migration_v3_to_v4(cstore):
    who = _who(cstore)
    _save_day(cstore, '2026-10-01', [(_sid(1), 101)])
    e = _suggest(_sid(1))
    assert ev.ingest(cstore, who, [e]).json()['accepted'] == [e['id']]
    with closing(sqlite3.connect(cstore.path)) as conn:     # база версии 3: без таблицы решений и индекса
        for sql in V3_DROP:
            conn.execute(sql)
        conn.commit()
    assert [s['event_id'] for s in Store(cstore.path).open_suggestions()] == [e['id']]   # событие на месте
    with closing(sqlite3.connect(cstore.path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {'geo_suggest_decision', 'events_type'} <= names
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO geo_suggest_decision VALUES('x', 'maybe', 't', NULL)")


# ============================== «Маршруты»: обзор, «Развоз», решение ==============================

def _courier_with_points(tmp_path):
    """courier.db: клиент 101 — 3 дня отметок в SHOP; клиент 102 — два предложения водителя (прежнее и последнее)."""
    store = Store(str(tmp_path / 'courier.db'))
    who = _who(store)
    for day in ('2026-09-28', '2026-09-29', '2026-09-30'):
        _save_day(store, day, [(_sid(1), 101), (_sid(2), 102)])
    evs = [_event('arrived', _sid(1), {'lat': SHOP[0], 'lon': SHOP[1], 'accuracy': 7.0}, d)
           for d in ('2026-09-28', '2026-09-29', '2026-09-30')]
    sug = _event('geo_suggest', _sid(2), {'lat': 40.1955, 'lon': 44.5250, 'accuracy': 6.0, 'note': 'Դարպասը ետևում է'},
                 '2026-09-30')
    older = _event('geo_suggest', _sid(2), {'lat': 40.1950, 'lon': 44.5240, 'accuracy': 9.0}, '2026-09-29')
    assert ev.ingest(store, who, evs + [older, sug]).json()['rejected'] == []
    return store, sug['id']


def _stops(plan):
    return {s['customer_id']: s for t in plan['trucks'] for tr in t['trips'] for s in tr['stops']}


def test_dispatch_driver_points_and_suggestions(client, tmp_path, monkeypatch):
    monkeypatch.setattr(clock, 'now', lambda: NOW)
    app = client.application
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    plain = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1']}).get_json()
    assert plain['geo_suggestions'] == {'count': 0, 'day_count': 0, 'items': []}
    assert _stops(plain['plan'])[101]['coord_source'] == 'erp'

    store, sug_id = _courier_with_points(tmp_path)
    route_optimizer.attach_driver_geo(app, DriverSource(store))
    d = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    s101, s102 = _stops(d['plan'])[101], _stops(d['plan'])[102]
    assert (s101['coord_source'], s101['lat'], s101['lon']) == ('driver', SHOP[0], SHOP[1])
    assert s102['coord_source'] == 'erp'
    gs = d['geo_suggestions']
    assert (gs['count'], gs['day_count'], len(gs['items'])) == (1, 1, 1)
    it = gs['items'][0]
    assert (it['event_id'], it['customer_id'], it['name'], it['driver_name'], it['note'], it['in_day'],
            it['suggestions']) == (sug_id, 102, 'Клиент 102', 'Արամ', 'Դարպասը ետևում է', True, 2)   # последнее и сколько
    assert it['current'] == {'lat': 40.19, 'lon': 44.52, 'source': 'erp'}
    assert it['distance_m'] == round(geo.haversine_km((40.19, 44.52), (40.1955, 44.525)) * 1000)
    assert client.get('/api/routes/overview').get_json()['customers']['101']['coord_source'] == 'driver'

    # решение: проверки входа
    for body in ({'event_id': sug_id}, {'event_id': 'x', 'decision': 'accepted'},
                 {'event_id': sug_id, 'decision': 'maybe'}, [1]):
        assert client.post('/api/routes/geo-suggest/decide', json=body).status_code == 400, body
    r = client.post('/api/routes/geo-suggest/decide', json={'event_id': sug_id.upper(), 'decision': 'accepted'})
    assert r.status_code == 200 and r.get_json()['customer_id'] == 102 and len(r.get_json()['superseded']) == 1
    assert rst.Store(str(tmp_path / 'routes.db')).load().geo_overrides[102] == (40.1955, 44.525)
    d = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert d['geo_suggestions']['count'] == 0
    assert (_stops(d['plan'])[102]['coord_source'], _stops(d['plan'])[102]['lat']) == ('manual', 40.1955)
    r = client.post('/api/routes/geo-suggest/decide', json={'event_id': sug_id, 'decision': 'rejected'})
    assert r.status_code == 404 and 'уже решено' in r.get_json()['error']
    # «Մերժել» предложения той же точки, что уже ручная: точка не меняется, ответ об этом говорит
    who = ev.Who(1, 'CAR1', 1, 'Արամ')
    again = _event('geo_suggest', _sid(2), {'lat': 40.1955, 'lon': 44.5250, 'accuracy': 6.0}, '2026-10-01')
    other = _event('geo_suggest', _sid(1), {'lat': 40.1700, 'lon': 44.4900, 'accuracy': 6.0}, '2026-10-01')
    assert ev.ingest(store, who, [again, other]).json()['rejected'] == []
    r = client.post('/api/routes/geo-suggest/decide', json={'event_id': again['id'], 'decision': 'rejected'}).get_json()
    assert (r['manual_same'], r['superseded']) == (True, [])
    assert rst.Store(str(tmp_path / 'routes.db')).load().geo_overrides[102] == (40.1955, 44.525)
    r = client.post('/api/routes/geo-suggest/decide', json={'event_id': other['id'], 'decision': 'rejected'}).get_json()
    assert r['manual_same'] is False
    # ручная точка любого магазина и «авто»: null убирает — снова точка водителей
    assert client.post('/api/routes/geo-override', json={'customer_id': 101, 'lat': 40.17, 'lon': 44.49}).status_code == 200
    assert _stops(client.get('/api/routes/dispatch?date=2026-10-01').get_json()['plan'])[101]['coord_source'] == 'manual'
    assert client.post('/api/routes/geo-override', json={'customer_id': 101, 'lat': None, 'lon': None}).status_code == 200
    assert _stops(client.get('/api/routes/dispatch?date=2026-10-01').get_json()['plan'])[101]['coord_source'] == 'driver'


def test_no_courier_db_or_broken_source_changes_nothing(client, tmp_path, monkeypatch):
    """Нет courier.db или сбой чтения — «Маршруты» считают ровно как без точек водителей."""
    monkeypatch.setattr(clock, 'now', lambda: NOW)
    app = client.application

    def overview():
        body = client.get('/api/routes/overview').get_json()
        return {k: v for k, v in body.items() if k not in ('generated_at', 'from_cache')}

    before = overview()
    route_optimizer.attach_driver_geo(app, DriverSource(Store(str(tmp_path / 'missing' / 'courier.db'))))
    app.extensions['route_optimizer'].results = type(app.extensions['route_optimizer'].results)()   # без кэша оценки
    assert overview() == before

    class Broken:
        def points(self):
            raise sqlite3.DatabaseError('file is not a database')

        def suggestions(self):
            raise sqlite3.DatabaseError('file is not a database')

    state = app.extensions['route_optimizer']
    state.driver_geo, state.driver_cache = Broken(), None
    state.results = type(state.results)()
    assert overview() == before
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    d = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert d['geo_suggestions'] == {'count': 0, 'day_count': 0, 'items': []}


def test_driver_points_cached_five_minutes(client, monkeypatch):
    state = client.application.extensions['route_optimizer']
    calls = []

    class Src:
        def points(self):
            calls.append(1)
            return {101: (SHOP[0], SHOP[1], 3), 102: (55.75, 37.61, 4)}   # вне Армении — отбрасывается

    state.driver_geo = Src()
    from route_optimizer import views
    clock_box = {'t': 1000.0}
    monkeypatch.setattr(views.time, 'monotonic', lambda: clock_box['t'])
    assert views._driver_points(state) == {101: SHOP}
    clock_box['t'] += views.DRIVER_TTL_SECONDS - 1
    views._driver_points(state)
    assert len(calls) == 1
    clock_box['t'] += 2
    views._driver_points(state)
    assert len(calls) == 2


def test_driver_points_rounded_and_stable(client, monkeypatch):
    """Медиана двигается с каждой отметкой: публикуется округлённая (5 знаков) и прежняя, пока сдвиг ≤ 15 м — отпечаток
    настроек и точки дорог не меняются каждые 5 минут."""
    from route_optimizer import views
    state = client.application.extensions['route_optimizer']
    box = {'p': (40.1850049, 44.5050049), 't': 1000.0}

    class Src:
        def points(self):
            return {101: (*box['p'], 3)}

    state.driver_geo = Src()
    monkeypatch.setattr(views.time, 'monotonic', lambda: box['t'])

    def refresh():
        box['t'] += views.DRIVER_TTL_SECONDS + 1
        return views._driver_points(state)

    first = refresh()
    assert first == {101: (40.185, 44.505)}
    fp = views._bundle(state).fingerprint()
    box['p'] = (40.18509, 44.50505)                          # ≈ 11 м — точка прежняя
    assert refresh() == first and views._bundle(state).fingerprint() == fp
    box['p'] = (40.18513, 44.50500)                          # ≈ 14,5 м от опубликованной — всё ещё прежняя
    assert refresh() == first
    box['p'] = (40.18520, 44.50500)                          # ≈ 22 м — новая
    assert refresh() == {101: (40.1852, 44.505)} and views._bundle(state).fingerprint() != fp
    assert views._stable_points({1: (40.0, 44.0), 2: (40.1, 44.1)}, {1: (40.0001, 44.0), 3: (40.2, 44.2)}) == \
        {1: (40.0001, 44.0), 2: (40.1, 44.1)}                # ушедший клиент не остаётся

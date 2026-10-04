"""Большая машина в Ереване (ответ владельца №68, «приоритет + время»): признак машины, зона Еревана, приоритет малых
машин, надбавка минут, пустая зона — прежний расчёт, миграция 21 → 22, модель парка менеджеров не меняется."""
import hashlib
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import geo  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import vrp  # noqa: E402
from test_route_dispatch_windows import GOLDEN_FLEET, _golden_days  # noqa: E402
from test_route_optimizer import (DP_DAY, DP_DEPOT, DP_NORMS, TN, _dispatch_setup, _dorder, _dp_stops,  # noqa: E402,F401
                                  _info, client)
from test_route_store_unload import _build  # noqa: E402

YZONE = tuple((lat, lon) for lat, lon in st.DEFAULT_SETTINGS['yerevan_zone'])
BIG = fl.FleetTruck('991AT61', 'HOWO', 5000.0, 16.0, big=True)
SMALL = fl.FleetTruck('333DO33', 'FORD', 2500.0, 16.0)
TNY = replace(TN, yerevan_zone=YZONE, yerevan_min={BIG.car_code: 10.0})     # зона и надбавка, как в views._dispatch_ctx
CITY = [(40.1777, 44.5126), (40.1700, 44.5000), (40.1900, 44.5200), (40.1600, 44.4900), (40.1750, 44.4800)]
OUTSIDE = [(40.200, 44.640), (40.205, 44.650), (40.210, 44.645)]
START = 9 * 60


def _items(trips, code):
    return sorted(i for t in trips if t.truck == code for i in t.items)


# ============================== зона и признак машины ==============================

def test_default_zone_is_yerevan_admin_boundary():
    """Стартовая зона — граница Еревана OSM (упрощённая): центр и окраины Еревана внутри, склад и восток — снаружи."""
    assert st.CENTER_ZONE_VERTICES[0] <= len(YZONE) <= st.CENTER_ZONE_VERTICES[1]
    assert all(geo.in_polygon(p, YZONE) for p in CITY)
    assert not any(geo.in_polygon(p, YZONE) for p in [DP_DEPOT, *OUTSIDE])
    assert st.DEFAULT_SETTINGS['big_truck_yerevan_min'] == 10
    assert TNY.in_yerevan(CITY[0]) and not TN.in_yerevan(CITY[0]) and not replace(TNY, yerevan_zone=YZONE[:2]).in_yerevan(CITY[0])
    assert (TNY.yerevan_of(BIG.car_code), TNY.yerevan_of(SMALL.car_code), TNY.yerevan_of(None)) == (10.0, 0.0, 0.0)


def test_truck_big_auto_by_capacity_and_manual(tmp_path):
    b = st.Store(str(tmp_path / 'r.db')).load()
    b = replace(b, trucks={'A': st.Truck('A', 5000.0), 'B': st.Truck('B', 4999.0), 'C': st.Truck('C'),
                           'D': st.Truck('D', 2500.0, big=True), 'E': st.Truck('E', 8000.0, big=False)})
    assert [b.truck_big(c) for c in 'ABCDE'] + [b.truck_big('NONE')] == [True, False, False, True, False, False]
    assert st.big_auto(st.BIG_TRUCK_AUTO_KG) and not st.big_auto(None) and not st.big_auto(4999.9)


def test_api_saves_big_flag_tri_state_and_zone(client):
    _dispatch_setup(client, [])
    r = client.post('/api/routes/settings', json={'settings': {'yerevan_zone': st.DEFAULT_SETTINGS['yerevan_zone']}})
    assert r.status_code == 200 and client.get('/api/routes/settings').get_json()['settings']['yerevan_zone'] ==         st.DEFAULT_SETTINGS['yerevan_zone']
    trucks = lambda: {t['car_code']: (t['big'], t['big_source'], t['auto_big'])  # noqa: E731
                      for t in client.get('/api/routes/settings').get_json()['trucks'] if t['car_code'] in ('CAR1', 'CAR2')}
    assert trucks() == {'CAR1': (True, 'auto', True), 'CAR2': (False, 'auto', False)}       # 10 т и 3,5 т
    r = client.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR1', 'big': False},
                                                             {'car_code': 'CAR2', 'big': True}]})
    assert r.status_code == 200, r.get_json()
    assert trucks() == {'CAR1': (False, 'manual', True), 'CAR2': (True, 'manual', False)}
    assert client.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR1', 'big': None}]}).status_code == 200
    assert trucks()['CAR1'] == (True, 'auto', True)                                         # null — снова «авто»
    r = client.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR1', 'big': 'yes'}]})
    assert r.status_code == 400 and 'trucks.0.big' in r.get_json()['errors']
    # зона Еревана: пусто — можно (правило выключено), 1–2 вершины — нельзя; стартовая — в ответе для «вернуть»
    assert client.post('/api/routes/settings', json={'settings': {'yerevan_zone': []}}).status_code == 200
    d = client.get('/api/routes/settings').get_json()
    assert d['settings']['yerevan_zone'] == [] and d['yerevan_zone_default'] == st.DEFAULT_SETTINGS['yerevan_zone']
    r = client.post('/api/routes/settings', json={'settings': {'yerevan_zone': [[40.18, 44.51], [40.17, 44.50]]}})
    assert r.status_code == 400 and 'settings.yerevan_zone' in r.get_json()['errors']
    r = client.post('/api/routes/settings', json={'settings': {'big_truck_yerevan_min': 121}})
    assert r.status_code == 400 and 'settings.big_truck_yerevan_min' in r.get_json()['errors']


def test_manual_truck_big_flag_saved(client):
    _dispatch_setup(client, [])
    r = client.post('/api/routes/settings', json={'manual_trucks': [
        {'car_code': 'M 1', 'capacity_kg': 6000, 'fuel_l_per_100km': 20, 'active': True, 'big': False},
        {'car_code': 'M 2', 'capacity_kg': 6000, 'fuel_l_per_100km': 20, 'active': True}]})
    assert r.status_code == 200, r.get_json()
    got = {t['car_code']: (t['big'], t['big_source']) for t in client.get('/api/routes/settings').get_json()['trucks']
           if t['manual']}
    assert got == {'M 1': (False, 'manual'), 'M 2': (True, 'auto')}


# ============================== миграция 21 → 22 ==============================

def test_store_migrates_21_to_22_keeps_trucks(tmp_path):
    path = str(tmp_path / 'v21.db')
    s = st.Store(path)
    b = s.load()
    s.save(st.Changes(settings=b.settings, depot_set=False, depot=None, managers=(),
                      trucks=(st.Truck('CAR1', 10000.0, 30.0, None, True, center_ok=True),
                              st.Truck('CAR2', 2500.0, 15.0, None, None)),
                      manual_trucks=(st.Truck('M1', 6000.0, 20.0, None, True, manual=True, name='HOWO'),)), 'qa')
    v21 = st._TRUCKS_COLUMNS.replace(', big INTEGER', '')
    with closing(sqlite3.connect(path)) as conn:      # база схемы 21: таблица машин без столбца «большая машина»
        conn.executescript(f'CREATE TABLE trucks_old({v21}); INSERT INTO trucks_old SELECT {st._TRUCKS_COPY_V21} FROM trucks; '
                           'DROP TABLE trucks; ALTER TABLE trucks_old RENAME TO trucks; '
                           "UPDATE meta SET value = '21' WHERE key = 'schema_version';")
        before = conn.execute(f'SELECT {st._TRUCKS_COPY_V21} FROM trucks ORDER BY car_code').fetchall()
    loaded = st.Store(path).load()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == (str(st.SCHEMA_VERSION),)
        assert conn.execute(f'SELECT {st._TRUCKS_COPY_V21} FROM trucks ORDER BY car_code').fetchall() == before
        assert conn.execute('SELECT DISTINCT big FROM trucks').fetchall() == [(None,)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'trucks_one_van'").fetchone()
    assert all(t.big is None for t in loaded.trucks.values())
    assert [loaded.truck_big(c) for c in ('CAR1', 'CAR2', 'M1')] == [True, False, True]
    assert loaded.trucks['CAR1'].center_ok is True and loaded.trucks['M1'].name == 'HOWO'


# ============================== пустая зона — расчёт прежний ==============================

GOLDEN_NO_WINDOWS = 'a0a3a6f6f018b9a7d84cdbbd337c0eb7d870a301d4c993b2955afe2a85a8887e'   # test_route_dispatch_windows


def _golden_hash(fleet, tn):
    out = []
    for seed, pts, kgs, revs, trucks, used in _golden_days():
        trucks = [next(f for f in fleet if f.car_code == t.car_code) for t in trucks]
        for overflow in (True, False):
            for earliest in (False, True):
                trips = fl.route_day(pts, kgs, revs, DP_DEPOT, trucks, DP_NORMS, tn, used, overflow, earliest)
                fields = ('truck', 'stops', 'kg', 'revenue', 'km', 'minutes', 'capacity_kg', 'liters', 'extra', 'items')
                out.append((seed, overflow, earliest, [tuple(getattr(t, key) for key in fields) for t in trips]))
    return hashlib.sha256(repr(out).encode()).hexdigest()


def test_empty_zone_trips_identical_with_big_trucks():
    """Все машины «большие», но зоны нет (пустая граница) — рейсы до байта те же, что до №68 (отпечаток того же теста
    без окон); зона есть, но больших машин нет — тоже (приоритета и надбавки нет)."""
    big = [replace(t, big=True) for t in GOLDEN_FLEET]
    assert _golden_hash(big, replace(TN, yerevan_zone=(), yerevan_min={t.car_code: 10.0 for t in big})) == GOLDEN_NO_WINDOWS
    assert _golden_hash(GOLDEN_FLEET, replace(TN, yerevan_zone=YZONE)) == GOLDEN_NO_WINDOWS


def test_fleet_model_ignores_big_flag():
    """Модель парка менеджеров (fleet_day: нормы из настроек, без зоны) от признака «большая машина» не зависит."""
    visits = [fl.DeliveryVisit(100 + i, 3, p, _Draw(), _Draw()) for i, p in enumerate(CITY + OUTSIDE)]
    tn = fl.TruckNorms.from_settings(st.DEFAULT_SETTINGS)
    plain = fl.fleet_day(1, 4, visits, DP_DEPOT, [SMALL, replace(BIG, big=False)], DP_NORMS, tn, 150000.0, n=5)
    flagged = fl.fleet_day(1, 4, visits, DP_DEPOT, [SMALL, BIG], DP_NORMS, tn, 150000.0, n=5)
    assert flagged == plain and plain.trips > 0


class _Draw:
    p = 1.0
    values = ((90000.0, 700.0), (60000.0, 400.0))


# ============================== приоритет малых машин ==============================

def _day(pts, kgs, trucks, tn, **kw):
    reasons = {}
    trips = fl.route_day(pts, kgs, [100000.0] * len(pts), DP_DEPOT, trucks, DP_NORMS, tn, overflow=False,
                         windows=[(-1e9, 1e9)] * len(pts), center=[False] * len(pts), reasons=reasons, balance=True,
                         solver=True, **kw)
    return trips, reasons


@pytest.mark.parametrize('solver', [True, False])
def test_priority_moves_yerevan_stops_to_small_truck(monkeypatch, solver):
    """Расход одинаковый: без зоны рейс Еревана берёт большая (при равной цене — вместительнее), с зоной — малая."""
    if not solver:
        monkeypatch.setattr(vrp, 'available', lambda: False)
    pts, kgs = CITY[:4], [300.0] * 4
    off, _ = _day(pts, kgs, [BIG, SMALL], TN)
    on, reasons = _day(pts, kgs, [BIG, SMALL], TNY)
    assert _items(off, BIG.car_code) == [0, 1, 2, 3]
    assert _items(on, SMALL.car_code) == [0, 1, 2, 3] and not reasons
    # окраина (вне зоны) — без приоритета: там большая машина как раньше
    out_on, _ = _day(OUTSIDE, [300.0] * 3, [BIG, SMALL], TNY)
    out_off, _ = _day(OUTSIDE, [300.0] * 3, [BIG, SMALL], TN)
    assert [(t.truck, t.items) for t in out_on] == [(t.truck, t.items) for t in out_off]


def test_big_truck_still_used_when_small_trucks_are_full():
    """Мягкий приоритет: заказ тяжелее малой машины и то, что малая не успевает за день, везёт большая — ничего не
    остаётся вне рейсов. Приоритет сам по себе магазин вне рейсов не оставляет; надбавка — время большой машины по
    правде: в очень коротком дне (150 мин) с ней не всё успевает и большая."""
    heavy, _ = _day(CITY[:3], [3000.0, 300.0, 300.0], [BIG, SMALL], TNY)
    assert 0 in _items(heavy, BIG.car_code) and sorted(i for t in heavy for i in t.items) == [0, 1, 2]
    kgs = [500.0] * 5 + [450.0] * 5
    pts = [(lat + 0.004 * k, lon) for k in range(2) for lat, lon in CITY]
    for minutes, extra in ((150.0, 0.0), (170.0, 10.0)):   # малой одной на всё не хватает ни тоннажа, ни времени
        tn = replace(TNY, work_minutes=minutes, yerevan_min={BIG.car_code: extra})
        trips, reasons = _day(pts, kgs, [BIG, SMALL], tn)
        assert not reasons and sorted(i for t in trips for i in t.items) == list(range(10)), (minutes, extra)
        assert _items(trips, BIG.car_code) and _items(trips, SMALL.car_code)


def test_balance_does_not_move_yerevan_stop_onto_big_truck():
    """Выравнивание не перекладывает магазин Еревана с малой машины на большую (№68), даже если загрузка ровнее."""
    pts, kgs = CITY[:3] + OUTSIDE[:1], [700.0, 700.0, 700.0, 300.0]
    trips, _ = _day(pts, kgs, [BIG, SMALL], TNY)
    big_city = [i for i in _items(trips, BIG.car_code) if i < 3]
    assert not big_city


# ============================== надбавка минут ==============================

def test_extra_minutes_only_for_big_truck_inside_zone():
    pts, kgs = [CITY[0], OUTSIDE[0]], [300.0, 300.0]
    got = {}
    for code in (BIG.car_code, SMALL.car_code):
        parts = {}
        got[code] = (fl.trip_schedule(pts, kgs, DP_DEPOT, DP_NORMS, TNY, truck=code, parts=parts), parts)
    (dep_b, arr_b, min_b), parts_b = got[BIG.car_code]
    (dep_s, arr_s, min_s), parts_s = got[SMALL.car_code]
    assert min_b == pytest.approx(min_s + 10.0) and arr_b[0] == pytest.approx(arr_s[0])
    assert arr_b[1] == pytest.approx(arr_s[1] + 10.0)                 # после точки в зоне — позже на надбавку
    assert parts_b['legs'][0][2] == pytest.approx(parts_s['legs'][0][2] + 10.0)
    assert parts_b['legs'][1][2] == pytest.approx(parts_s['legs'][1][2])   # точка вне зоны — без надбавки
    # без зоны большая машина — как малая
    assert fl.trip_schedule(pts, kgs, DP_DEPOT, DP_NORMS, TN, truck=BIG.car_code) == (dep_s, arr_s, min_s)


def test_plan_view_shows_extra_minutes_on_big_truck_stops():
    spec = [(101, CITY[0], 300.0), (102, OUTSIDE[0], 300.0), (103, CITY[1], 300.0)]
    stops, _ = _dp_stops(spec)
    ctx = dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in (BIG, SMALL)}, DP_NORMS, TNY, START)
    draft = dp.Draft(trucks=[BIG.car_code, SMALL.car_code],
                     trips=[dp.DraftTrip(1, BIG.car_code, [101, 102]), dp.DraftTrip(2, SMALL.car_code, [103])])
    v = dp.plan_view(ctx, stops, draft, _info)
    by = {s['customer_id']: s for t in v['trucks'] for tr in t['trips'] for s in tr['stops']}
    assert by[101]['yerevan_min'] == 10 and 'yerevan_min' not in by[102] and 'yerevan_min' not in by[103]
    big_trip = next(tr for t in v['trucks'] for tr in t['trips'] if tr['truck'] == BIG.car_code)
    assert big_trip['explain']['yerevan_min'] == 10
    assert v['explain']['yerevan'] == {'stores': 2, 'big': [BIG.car_code], 'minutes': 10.0,
                                       'penalty_km': fl.BIG_YEREVAN_KM}
    assert next(t for t in v['trucks'] if t['car_code'] == BIG.car_code)['big'] is True
    # тот же план без зоны: надбавки нет, пояснение дня без правила
    plain = dp.plan_view(replace(ctx, tn=TN), stops, draft, _info)
    assert 'yerevan' not in plain['explain'] and all('yerevan_min' not in s for t in plain['trucks']
                                                     for tr in t['trips'] for s in tr['stops'])
    assert big_trip['minutes'] == next(tr for t in plain['trucks'] for tr in t['trips']
                                       if tr['truck'] == BIG.car_code)['minutes'] + 10


# ============================== «Развоз» через API ==============================

def test_dispatch_api_big_truck_in_yerevan(client):
    """Стартовая зона (Ереван): CAR1 (10 т — «авто» большая) на точках Еревана — +10 мин в разгрузке точки; зона
    пуста — тех же рейсов цифры без надбавки."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0)])    # он выключает правило (пустая зона)
    assert client.post('/api/routes/settings', json={'settings': {
        'yerevan_zone': st.DEFAULT_SETTINGS['yerevan_zone']}}).status_code == 200
    state = client.application.extensions['route_optimizer']
    state.store.save_geo_override(101, CITY[0], 'qa')
    state.store.save_geo_override(102, OUTSIDE[0], 'qa')
    plan = _build(client, ('CAR1',))['plan']
    stops = {s['customer_id']: s for t in plan['trucks'] for tr in t['trips'] for s in tr['stops']}
    assert stops[101]['yerevan_min'] == 10 and 'yerevan_min' not in stops[102]
    assert plan['explain']['yerevan']['big'] == ['CAR1']
    assert client.post('/api/routes/settings', json={'settings': {'yerevan_zone': []}}).status_code == 200
    plain = _build(client, ('CAR1',))['plan']
    stops0 = {s['customer_id']: s for t in plain['trucks'] for tr in t['trips'] for s in tr['stops']}
    assert stops0[101]['unload_min'] == pytest.approx(stops[101]['unload_min'] - 10, abs=0.11)
    assert 'yerevan' not in plain['explain'] and 'yerevan_min' not in stops0[101]


# ============================== обучение: надбавка не учится второй раз ==============================

def test_learning_subtracts_big_truck_extra_from_unload_and_forecasts_it(client, monkeypatch):
    """CAR1 (10 т — большая) возит магазины Еревана: стоянки её визитов идут в обучение разгрузки (своё время магазина,
    темп машины) без надбавки плана (−10 мин за магазин), а прогноз рейса для запаса (trip_obs) — с ней: план её и так
    прибавит. Зона пуста — наблюдения прежние."""
    from route_optimizer import learning as lr
    from route_optimizer import views
    from test_learning_loop import TODAY, _learning_client
    state = _learning_client(client, monkeypatch)
    seen = {}
    real_unload, real_buffer = lr.fit_unload, lr.fit_buffer
    monkeypatch.setattr(lr, 'fit_unload', lambda obs, *a, **kw: seen.__setitem__('unload', list(obs)) or real_unload(obs, *a, **kw))
    monkeypatch.setattr(lr, 'fit_buffer', lambda obs, *a, **kw: seen.__setitem__('trips', list(obs)) or real_buffer(obs, *a, **kw))
    views.run_learning(state, TODAY)
    plain = seen.copy()
    assert client.post('/api/routes/settings', json={'settings': {
        'yerevan_zone': st.DEFAULT_SETTINGS['yerevan_zone']}}).status_code == 200
    views.run_learning(state, TODAY)
    key = lambda o: (o.day, o.customers)  # noqa: E731
    before = {key(o): o.minutes for o in plain['unload']}
    after = {key(o): o.minutes for o in seen['unload']}
    assert before and all(o.car == 'CAR1' for o in seen['unload'])
    expect = {k: m - 10.0 * len(k[1]) for k, m in before.items() if m - 10.0 * len(k[1]) >= lr.UNLOAD_MIN_OBS}
    assert after == pytest.approx(expect)
    trips0 = {(o.day, o.minutes): o.predicted for o in plain['trips']}
    trips1 = {(o.day, o.minutes): o.predicted for o in seen['trips']}
    assert trips0.keys() == trips1.keys() and all(trips1[k] > trips0[k] + 9.0 for k in trips0)

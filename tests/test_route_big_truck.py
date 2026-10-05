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
    assert st.DEFAULT_SETTINGS['big_truck_yerevan_km'] == fl.BIG_YEREVAN_KM == TN.yerevan_km == 3
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
    assert big_trip['explain']['yerevan_min'] == 10 and big_trip['explain']['yerevan_km'] == fl.BIG_YEREVAN_KM
    # «другие машины» на рейс малой машины (магазин Еревана): большая — с платой приоритета, км её пути
    small_trip = next(tr for t in v['trucks'] for tr in t['trips'] if tr['truck'] == SMALL.car_code)
    other = next(o for o in small_trip['explain']['others'] if o['car_code'] == BIG.car_code)
    assert other['big'] is True and other['yerevan_km'] == fl.BIG_YEREVAN_KM and 'yerevan_km' not in small_trip['explain']
    assert all('big' not in o for o in big_trip['explain']['others'])
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
    # сила приоритета — настройка (ползунок): 10 км — в пояснении дня; вне 0–20 — ошибка
    assert client.post('/api/routes/settings', json={'settings': {
        'yerevan_zone': st.DEFAULT_SETTINGS['yerevan_zone'], 'big_truck_yerevan_km': 10}}).status_code == 200
    assert _build(client, ('CAR1',))['plan']['explain']['yerevan']['penalty_km'] == 10
    r = client.post('/api/routes/settings', json={'settings': {'big_truck_yerevan_km': 21}})
    assert r.status_code == 400 and 'settings.big_truck_yerevan_km' in r.get_json()['errors']


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
    real_unload, real_buffer, real_lunch = lr.fit_unload, lr.fit_buffer, lr.lunch_obs
    monkeypatch.setattr(lr, 'lunch_obs', lambda *a, **kw: seen.__setitem__('lunch_norm', a[5]) or real_lunch(*a, **kw))
    monkeypatch.setattr(lr, 'fit_unload', lambda obs, *a, **kw: seen.__setitem__('unload', list(obs)) or real_unload(obs, *a, **kw))
    monkeypatch.setattr(lr, 'fit_buffer', lambda obs, *a, **kw: seen.__setitem__('trips', list(obs)) or real_buffer(obs, *a, **kw))
    views.run_learning(state, TODAY)
    plain = seen.copy()
    assert 'lunch_norm' in plain
    assert client.post('/api/routes/settings', json={'settings': {
        'yerevan_zone': st.DEFAULT_SETTINGS['yerevan_zone']}}).status_code == 200
    views.run_learning(state, TODAY)
    key = lambda o: (o.day, o.customers)  # noqa: E731
    before = {key(o): o.minutes for o in plain['unload']}
    after = {key(o): o.minutes for o in seen['unload']}
    assert before and all(o.car == 'CAR1' for o in seen['unload'])
    # вышло меньше минимума — наблюдение остаётся с минимумом (не выпадает: иначе выученное сместилось бы вверх)
    expect = {k: max(lr.UNLOAD_MIN_OBS, m - 10.0 * len(k[1])) for k, m in before.items()}
    assert after == pytest.approx(expect) and any(v == lr.UNLOAD_MIN_OBS for v in after.values())
    # норма разгрузки для обеда у магазина (lunch_obs) — тоже с надбавкой, как в плане
    probe = lr.UnloadObs(TODAY, 1, 0.5, 0.0, (101,))
    assert seen['lunch_norm'](probe) == pytest.approx(plain['lunch_norm'](probe) + 10.0)
    trips0 = {(o.day, o.minutes): o.predicted for o in plain['trips']}
    trips1 = {(o.day, o.minutes): o.predicted for o in seen['trips']}
    assert trips0.keys() == trips1.keys() and all(trips1[k] > trips0[k] + 9.0 for k in trips0)


# ============================== сила приоритета и все места, где действуют приоритет и надбавка ==============================

def test_strength_setting_drives_priority():
    """Сила 0 — только надбавка (большая берёт Ереван, как без правила при той же цене), 3 — малая."""
    pts, kgs = CITY[:4], [300.0] * 4
    zero, _ = _day(pts, kgs, [BIG, SMALL], replace(TNY, yerevan_km=0.0, yerevan_min={}))
    three, _ = _day(pts, kgs, [BIG, SMALL], replace(TNY, yerevan_min={}))
    assert _items(zero, BIG.car_code) == [0, 1, 2, 3] and _items(three, SMALL.car_code) == [0, 1, 2, 3]


def test_plan_trips_plain_path_priority_and_extra_minutes():
    """Прежний путь plan_trips (overflow=True, без окон): выбор машины — с платой (№68), минуты рейса — с надбавкой."""
    pts, kgs = CITY[:4], [300.0] * 4
    run = lambda trucks, tn: fl.route_day(pts, kgs, [1.0] * 4, DP_DEPOT, trucks, DP_NORMS, tn, overflow=True)  # noqa: E731
    assert [t.truck for t in run([BIG, SMALL], TN)] == [BIG.car_code]
    assert [t.truck for t in run([BIG, SMALL], TNY)] == [SMALL.car_code]
    (off,), (on,) = run([BIG], TN), run([BIG], TNY)
    assert on.items == off.items and on.minutes == pytest.approx(off.minutes + 40.0)


def test_earliest_mode_prefers_small_truck_for_yerevan():
    """«Везти после конца дня» (earliest): малая машина берёт рейс Еревана, если успевает, хотя большая кончит раньше."""
    pts, kgs, used = CITY[:2], [300.0] * 2, {SMALL.car_code: 200.0, BIG.car_code: 0.0}
    tn_on = replace(TNY, yerevan_min={})
    for kw in ({'overflow': True}, {'overflow': False, 'windows': [(0.0, 1e6)] * 2}):   # прежний путь и _plan_timed
        off = fl.route_day(pts, kgs, [1.0] * 2, DP_DEPOT, [BIG, SMALL], DP_NORMS, TN, used, earliest=True, **kw)
        on = fl.route_day(pts, kgs, [1.0] * 2, DP_DEPOT, [BIG, SMALL], DP_NORMS, tn_on, used, earliest=True, **kw)
        assert [t.truck for t in off] == [BIG.car_code] and [t.truck for t in on] == [SMALL.car_code], kw


def test_plan_timed_no_room_reason_counts_extra_minutes():
    """Одиночная точка Еревана, которую большая машина с надбавкой не успевает (а без неё — успела бы): причина — время,
    а не окно (plain_gap — с надбавкой)."""
    off = fl.route_day(CITY[:1], [300.0], [1.0], DP_DEPOT, [BIG], DP_NORMS, TN, overflow=True)[0].minutes
    reasons = {}
    trips = fl.route_day(CITY[:1], [300.0], [1.0], DP_DEPOT, [BIG], DP_NORMS, replace(TNY, work_minutes=off + 5.0),
                         overflow=False, windows=[(0.0, 1e6)], reasons=reasons)
    assert not trips and reasons == {0: 'time'}


def test_time_head_counts_extra_minutes():
    """Рейс не успевает до конца дня — голова рейса (_time_head) считается с надбавкой большой машины."""
    pts = [(40.1970, 44.5132), (40.1673, 44.5206), (40.1880, 44.5371), (40.1963, 44.5050)]
    reasons = {}
    trips = fl.route_day(pts, [800.0, 800.0, 200.0, 200.0], [1.0] * 4, DP_DEPOT, [BIG], DP_NORMS,
                         replace(TNY, work_minutes=114.0), overflow=False, reasons=reasons)
    assert [t.items for t in trips] == [(2, 3, 0)] and reasons == {1: 'time'}


def test_route_trip_window_guard_counts_extra_minutes():
    """2-opt рейса с окнами (route_trip) бережёт окна с надбавкой большой машины: порядок, который соблюдает окна только без
    неё, не считается допустимым."""
    import math
    cases = [([(40.1675, 44.5236), (40.1779, 44.5074), (40.1829, 44.5364), (40.1907, 44.5301)], 2, 97, [0, 1, 2, 3]),
             ([(40.1693, 44.4826), (40.1995, 44.4891), (40.1518, 44.5007)], 2, 83, [1, 0, 2])]
    for pts, k, late, want in cases:
        wins = [(-math.inf, math.inf)] * len(pts)
        wins[k] = (-math.inf, late)
        seq, _, _ = fl.route_trip(pts, [300.0] * len(pts), DP_DEPOT, DP_NORMS, TNY, True, wins, 0.0, BIG.car_code)
        assert seq == want


def _matrix(n):
    """Склад и n точек: до склада 10 км (20 мин), между точками 0 — перенос магазина км не меняет."""
    d = [[0.0 if a == b or (a and b) else 10.0 for b in range(n + 1)] for a in range(n + 1)]
    return d, [[2.0 * x for x in row] for row in d]


def _trips(spec, stops, d):
    return [fl.Trip(t.car_code, len(seq), sum(stops[i].kg for i in seq), 0.0, fl._closed(seq, stops, d), 0.0, t.capacity_kg,
                    0.0, False, tuple(seq)) for t, seq in spec]


@pytest.mark.parametrize('city', [True, False])
def test_balance_does_not_move_yerevan_stop_from_small_to_big(city):
    """Ровная загрузка: малая машина 92% (два магазина Еревана), большая — 10%. Без зоны магазин переходит на большую,
    с зоной — нет (№68)."""
    d, m = _matrix(3)
    stops = [fl._Stop(1, 1200.0, 0.0, 10.0, yerevan=city), fl._Stop(2, 1100.0, 0.0, 10.0, yerevan=city),
             fl._Stop(3, 500.0, 0.0, 10.0)]
    trips = _trips([(SMALL, [0, 1]), (BIG, [2])], stops, d)
    got = fl._balance(trips, stops, d, m, [BIG, SMALL], TN, None, None)
    assert (got == trips) is city


@pytest.mark.parametrize('city', [True, False])
def test_balance_does_not_swap_yerevan_stop_onto_big(city):
    """Обмен: большая 92% (вне Еревана), малая с магазином Еревана; ровнее только обмен — он переносит магазин Еревана на
    большую: с зоной — нельзя."""
    d, m = _matrix(3)
    stops = [fl._Stop(1, 2400.0, 0.0, 10.0), fl._Stop(2, 2200.0, 0.0, 10.0), fl._Stop(3, 1500.0, 0.0, 10.0, yerevan=city)]
    trips = _trips([(BIG, [0, 1]), (SMALL, [2])], stops, d)
    got = fl._balance(trips, stops, d, m, [BIG, SMALL], TN, None, None)
    assert (got == trips) is city
    if not city:
        assert 2 in got[0].items


def test_days_wait_does_not_count_extra_minutes():
    """Рейсы машины подряд (_days): ожидание у окна — только ожидание, надбавка Еревана в нём не считается."""
    d, m = _matrix(2)
    m[1][2] = m[2][1] = 10.0
    vs = [fl._Stop(1, 300.0, 0.0, 10.0, yerevan=True), fl._Stop(2, 300.0, 0.0, 10.0, early=60.0)]
    trip = fl.Trip(BIG.car_code, 2, 600.0, 0.0, 0.0, 0.0, BIG.capacity_kg, 0.0, False, (0, 1))
    (_, dep, minutes, wait), = fl._days([trip], vs, m, {BIG.car_code: 0.0}, TNY)[BIG.car_code]
    assert (dep, minutes, wait) == (0.0, 90.0, 10.0)       # 20 + 20 (разгрузка 10 + 10) + 10 + 10 ожидания + 10 + 20


# ============================== перетаскивание конца рейса (№59) ==============================

def test_resize_tools_apply_yerevan_priority():
    """«Короче» (_shrink): магазин Еревана уходит малой машине, а не большой (км одинаковы). «Длиннее» (_grow): большая
    берёт магазин вне Еревана, хотя магазин Еревана на 1 км ближе (плата — 3 км)."""
    small2 = replace(SMALL, car_code='A2')
    y, x, o, y2 = (40.19, 44.59), (40.216, 44.60), (40.200, 44.640), CITY[0]
    stops, _ = _dp_stops([(201, o, 300.0), (202, y, 300.0), (203, x, 300.0), (204, y2, 300.0)])
    routable = {s.customer_id: s for s in stops}
    got = {}
    for name, tn in (('off', TN), ('on', replace(TNY, yerevan_min={}))):
        ctx = dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in (BIG, SMALL, small2)}, DP_NORMS, tn, START)
        draft = dp.Draft(trucks=[BIG.car_code, SMALL.car_code, 'A2'], trips=[dp.DraftTrip(1, SMALL.car_code, [202, 204])],
                         next_id=2)
        end = dp._truck_day(ctx, draft.trips, routable, dp._shares(draft.trips), SMALL.car_code)[0][1]
        dp._shrink(ctx, draft, routable, draft.trips[0], end - 5.0)
        grow = dp.Draft(trucks=[BIG.car_code, SMALL.car_code], trips=[dp.DraftTrip(1, BIG.car_code, [201])], next_id=2)
        part = {c: s for c, s in routable.items() if c != 204}
        end = dp._truck_day(ctx, grow.trips, part, dp._shares(grow.trips), BIG.car_code)[0][1]
        dp._grow(ctx, grow, part, grow.trips[0], end + 20.0)
        got[name] = ([(t.truck, t.stops) for t in draft.trips], grow.trips[0].stops)
    assert got['off'] == ([(SMALL.car_code, [204]), (BIG.car_code, [202])], [202, 201])
    assert got['on'] == ([(SMALL.car_code, [204]), ('A2', [202])], [203, 201])

# -*- coding: utf-8 -*-
"""«Развоз»: пояснение «почему рейс такой» (explain у рейса и у дня в plan_view) — только цифры того же расчёта:
слагаемые минут рейса складываются в его минуты, объезд центра, другие машины на тот же рейс, что учтено в дне.

Синтетические данные, без БД ERP; база настроек — только временная.
Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_explain.py -q
"""
import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import vrp  # noqa: E402
from route_optimizer.traffic_validation import TrafficProfile  # noqa: E402
from route_optimizer.vehicle_access import VehicleAccess  # noqa: E402
from test_route_optimizer import (DP_DAY, DP_DEPOT, DP_NORMS, EAST, FORD, HOWO, TN,  # noqa: E402,F401
                                  _dispatch_setup, _dorder, _dp_stops, _info, _no_road_map, client)

JAC = fl.FleetTruck('475DD61', 'JAC', 2200.0, 12.0, center_ok=True)
ZONE = tuple((lat, lon) for lat, lon in st.DEFAULT_SETTINGS['center_zone'])
CENTER = (201, (40.1777, 44.5126), 100.0)
START = 9 * 60
LOADING = replace(TN, warehouse_load_fixed_min=10.0, warehouse_load_min_per_tonne=5.0)   # часовая модель (TravelMatrix)
# пробки по часам: 09–11 вдвое медленнее — время участка зависит от часа выезда
SLOW = replace(DP_NORMS, traffic=TrafficProfile({(c, 0, h): 0.5 for c in (True, False) for h in (9, 10)}))


def _hm(text):
    h, m = map(int, text.split(':'))
    return h * 60 + m


def _ctx(trucks, windows=None, zone=(), tn=TN, norms=DP_NORMS, access=None, model=None):
    return dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in trucks}, norms, tn, START,
                         windows=windows or {}, center_zone=zone, vehicle_access=access or {}, model=model or {})


def _view(ctx, stops, trips, trucks=None):
    draft = dp.Draft(trucks or sorted(ctx.trucks), trips=[dp.DraftTrip(i + 1, code, list(cids), pinned)
                                                          for i, (code, cids, pinned) in enumerate(trips)],
                     next_id=len(trips) + 1)
    return draft, dp.plan_view(ctx, stops, draft, _info)


def _trips(view):
    return [t for tr in view['trucks'] for t in tr['trips']]


# ============================== время рейса ==============================

@pytest.mark.parametrize('tn, norms', [(TN, DP_NORMS), (LOADING, DP_NORMS), (LOADING, SLOW)],
                         ids=['plain', 'loading', 'loading+traffic'])
def test_trip_time_parts_add_up_to_trip_minutes(tn, norms):
    """Загрузка + езда + разгрузка + ожидание окон = минуты рейса (выезд → возвращение), в т. ч. второй рейс машины
    и рейс, который выезжает позже под окно первой точки (простой на складе — не минуты рейса)."""
    stops, _ = _dp_stops(EAST)
    windows = {102: st.CustomerWindow('after', _hm('10:30')).span(), 103: st.CustomerWindow('after', _hm('13:00')).span()}
    ctx = _ctx([FORD], windows, tn=tn, norms=norms)
    draft, view = _view(ctx, stops, [(FORD.car_code, [101, 102], False), (FORD.car_code, [103], False)])
    routable = {s.customer_id: s for s in stops}
    parts = {}
    raw = dp._timeline(ctx, draft.trips, routable, dp._shares(draft.trips), parts)
    first, second = _trips(view)
    for trip in (first, second):
        depart, minutes, arrivals = raw[trip['id']]
        p = parts[trip['id']]
        assert len(p['legs']) == len(trip['stops']) + 1                    # к каждой точке и обратно на склад
        assert p['loading'] + math.fsum(x for leg in p['legs'] for x in leg) == pytest.approx(minutes, abs=1e-9)
        e = trip['explain']
        shown = e['loading_min'] + e['drive_min'] + e['unload_min'] + e['wait_min']
        assert shown == pytest.approx(minutes, abs=0.2)
        assert e['loading_min'] == trip['loading_minutes']
        assert sum(s['drive_min'] for s in trip['stops']) + e['back_min'] == pytest.approx(e['drive_min'], abs=0.2)
        assert sum(s['wait_min'] for s in trip['stops']) == pytest.approx(e['wait_min'], abs=0.2)
        assert sum(s['unload_min'] for s in trip['stops']) == pytest.approx(e['unload_min'], abs=0.2)
        assert abs(_hm(trip['return']) - _hm(trip['loading_start']) - round(minutes)) <= 1
        assert e['end_slack_min'] == pytest.approx(TN.work_minutes - depart - minutes, abs=0.06)
        for s, at in zip(trip['stops'], arrivals):
            assert s['eta'] == dp._hhmm(START + at)
    # первый рейс: у 102 «после 10:30» — машина ждёт там; выезжает в 09:00 — без простоя
    assert first['stops'][1]['eta'] == '10:30' and first['stops'][1]['wait_min'] > 0 and first['explain']['wait_min'] > 0
    assert first['stops'][0]['wait_min'] == 0 and first['explain']['idle_before_min'] == 0
    # второй рейс: 103 «после 13:00» — выезд позже, чтобы не ждать у неё; ожидания в рейсе нет
    assert second['stops'][0]['eta'] == '13:00' and second['explain']['wait_min'] == 0
    assert second['explain']['idle_before_min'] > 0
    assert _hm(second['loading_start']) == pytest.approx(_hm(first['return']) + second['explain']['idle_before_min'], abs=1)


def test_parts_do_not_change_schedule():
    """Слагаемые снимаются с того же расчёта: выезд, прибытия и минуты — те же, что без них (байт-в-байт)."""
    stops, _ = _dp_stops(EAST)
    windows = {102: st.CustomerWindow('after', _hm('10:30')).span()}
    for tn, norms in ((TN, DP_NORMS), (LOADING, SLOW)):
        ctx = _ctx([FORD, HOWO], windows, tn=tn, norms=norms)
        trips = [dp.DraftTrip(1, FORD.car_code, [101, 102]), dp.DraftTrip(2, FORD.car_code, [103]),
                 dp.DraftTrip(3, HOWO.car_code, [102])]
        routable = {s.customer_id: s for s in stops}
        shares = dp._shares(trips)
        assert dp._timeline(ctx, trips, routable, shares, {}) == dp._timeline(ctx, trips, routable, shares)
        pts, kgs = [s.point for s in stops], [s.kg for s in stops]
        spans = [dp._span(ctx, s.customer_id) for s in stops]
        assert fl.trip_schedule(pts, kgs, DP_DEPOT, norms, tn, 30.0, spans, {}) == \
            fl.trip_schedule(pts, kgs, DP_DEPOT, norms, tn, 30.0, spans)


def test_window_margin_is_minutes_left_to_window_end():
    stops, _ = _dp_stops(EAST[:2])
    windows = {101: st.CustomerWindow('before', _hm('12:00')).span(), 102: st.CustomerWindow('after', _hm('09:05')).span()}
    _, view = _view(_ctx([FORD], windows), stops, [(FORD.car_code, [101, 102], False)])
    a, b = _trips(view)[0]['stops']
    assert a['margin_min'] == pytest.approx(_hm('12:00') - _hm(a['eta']), abs=1)
    assert b['margin_min'] is None                                            # «после …» — конца у окна нет


# ============================== груз и другие машины ==============================

def test_other_trucks_legality_reasons_and_liters():
    """Другие машины дня на тот же рейс: мала (тоннаж), выше 90% (предел загрузки), центр, допуск магазина — и литры
    на тех же км по своему расходу."""
    spec = [(101, EAST[0][1], 1650.0), (102, EAST[1][1], 1650.0), (103, EAST[2][1], 300.0), CENTER]
    stops, _ = _dp_stops(spec)
    only_howo = VehicleAccess('allow', (HOWO.car_code,))
    ctx = _ctx([FORD, HOWO, JAC], zone=ZONE, access={103: only_howo})
    _, view = _view(ctx, stops, [(HOWO.car_code, [101, 102], False), (HOWO.car_code, [201], False),
                                 (HOWO.car_code, [103], False)])
    heavy, center, ruled = _trips(view)
    others = {o['car_code']: o for o in heavy['explain']['others']}
    assert set(others) == {FORD.car_code, JAC.car_code}                       # своя машина — не «другая»
    assert others[FORD.car_code]['reasons'] == ['load_cap'] and others[FORD.car_code]['load_pct'] == 94   # 3300 > 3150
    assert others[JAC.car_code]['reasons'] == ['capacity']                    # 3300 > 2200
    assert others[FORD.car_code]['liters'] == pytest.approx(heavy['km'] * FORD.l100 / 100, abs=0.06)
    assert heavy['liters'] == pytest.approx(heavy['km'] * HOWO.l100 / 100, abs=0.06)
    others = {o['car_code']: o for o in center['explain']['others']}
    assert others[FORD.car_code]['reasons'] == ['center'] and others[JAC.car_code]['reasons'] == []
    others = {o['car_code']: o for o in ruled['explain']['others']}
    assert others[FORD.car_code]['reasons'] == ['vehicle'] and others[FORD.car_code]['vehicle_denied'] == 1
    e = heavy['explain']
    assert (e['load_limit_kg'], e['over_limit'], e['heavy_alone'], e['load_cap_pct']) == (9000, False, False, 90)
    assert (e['l100'], e['fuel_empty_l100'], e['fuel_full_l100']) == (HOWO.l100, None, None)


def test_single_heavy_order_exception_follows_planner_rule():
    """Один заказ тяжелее 90% самой большой подходящей машины дня — предел весь тоннаж (иначе не увезти); есть машина
    побольше — предел 90%, и рейс малой машины выше предела."""
    stops, _ = _dp_stops([(104, EAST[0][1], 3400.0)])
    _, view = _view(_ctx([FORD, JAC]), stops, [(FORD.car_code, [104], False)])
    e = _trips(view)[0]['explain']
    assert (e['load_limit_kg'], e['over_limit'], e['heavy_alone']) == (3500, False, True)
    assert [o['reasons'] for o in e['others']] == [['capacity']]
    _, view = _view(_ctx([FORD, HOWO]), stops, [(FORD.car_code, [104], False)])
    e = _trips(view)[0]['explain']
    assert (e['load_limit_kg'], e['over_limit'], e['heavy_alone']) == (3150, True, False)
    assert [(o['car_code'], o['reasons']) for o in e['others']] == [(HOWO.car_code, [])]
    # то же правило, что у сборки
    assert fl.load_limit([3400.0], [False], [None], FORD, [FORD, JAC]) == fl._load_limit(
        [0], [fl._Stop(1, 3400.0, 0.0, 0.0)], FORD, [FORD, JAC], fl.LOAD_CAP)


# ============================== объезд центра ==============================

def test_bypass_extra_km_of_trip():
    """Рейс HOWO запад → восток: участок между ними в объезд центра (8 шагов вместо 6) — +2 шага, один участок; км без
    объезда — км рейса минус объезд. Рейс JAC в центр — по обычным дорогам, объезда нет; без границы центра — 0."""
    pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    from test_route_center_bypass import EAST as E, MID, STEP_KM, TOP, WEST, ZONE as GRID_ZONE, _bypass
    r = _bypass()
    howo = fl.FleetTruck('124AV61', 'HOWO', 10000.0, 30.0)
    ctx = dp.DayContext(DP_DAY, TOP, {t.car_code: t for t in (howo, JAC)}, replace(DP_NORMS, roads=r), TN, START,
                        center_zone=GRID_ZONE)
    stops, _ = _dp_stops([(301, WEST, 300.0), (302, E, 300.0), (304, MID, 300.0)])
    r.ensure([TOP, WEST, E, MID])
    _, view = _view(ctx, stops, [(howo.car_code, [301, 302], False), (JAC.car_code, [304], False)])
    around, center = _trips(view)
    assert around['explain']['bypass_legs'] == 1
    assert around['explain']['bypass_km'] == pytest.approx(2 * STEP_KM, abs=0.06)
    line = [TOP, WEST, E, TOP]
    assert around['km'] - around['explain']['bypass_km'] == pytest.approx(
        sum(r.base.km(a, b) for a, b in zip(line, line[1:])), abs=0.1)
    assert (center['explain']['bypass_km'], center['explain']['bypass_legs']) == (0.0, 0)
    # любой порядок: объезд не отрицательный и не больше км рейса
    for order in ([302, 301], [301], [302]):
        _, v = _view(ctx, stops, [(howo.car_code, order, False)])
        e = _trips(v)[0]
        assert 0 <= e['explain']['bypass_km'] <= e['km']
    plain = replace(ctx, norms=replace(DP_NORMS, roads=r.base), center_zone=())
    _, view = _view(plain, stops, [(howo.car_code, [301, 302], False)])
    assert (_trips(view)[0]['explain']['bypass_km'], _trips(view)[0]['explain']['bypass_legs']) == (0.0, 0)


def test_no_roads_no_bypass():
    stops, _ = _dp_stops(EAST)
    _, view = _view(_ctx([FORD], zone=ZONE), stops, [(FORD.car_code, [101, 102, 103], False)])
    assert (_trips(view)[0]['explain']['bypass_km'], _trips(view)[0]['explain']['bypass_legs']) == (0.0, 0)


# ============================== день ==============================

def test_day_explain_counts_and_flags():
    spec = [*EAST, CENTER, (999, None, 50.0)]
    stops, _ = _dp_stops(spec)
    windows = {101: st.CustomerWindow('before', _hm('12:00')).span(), 999: st.CustomerWindow('before', _hm('12:00')).span()}
    access = {102: VehicleAccess('deny', (JAC.car_code,))}
    model = {'km': 'osm', 'bypass': True}
    ctx = _ctx([FORD, JAC, HOWO], windows, zone=ZONE, access=access, model=model)
    _, view = _view(ctx, stops, [(FORD.car_code, [101, 102], True), (JAC.car_code, [201], False),
                                 (FORD.car_code, [103], False)], trucks=[FORD.car_code, JAC.car_code])
    e = view['explain']
    assert [(t['car_code'], t['trips'], t['center_ok']) for t in e['trucks']] == \
        [(FORD.car_code, 2, False), (JAC.car_code, 1, True)]                  # только машины дня (HOWO не отмечена)
    assert e['trucks'][0]['capacity_kg'] == FORD.capacity_kg and e['trucks'][0]['l100'] == FORD.l100
    assert (e['zone'], e['center_stores'], e['window_stores'], e['access_stores']) == (True, 1, 1, 1)   # 999 без точки
    assert (e['pinned_trips'], e['load_cap_pct'], e['solver'], e['solver_iterations']) == \
        (1, 90, vrp.available(), vrp.ITERATIONS)
    assert (e['unload_min_per_stop'], e['unload_min_per_tonne'], e['unload_stores']) == (8.0, 6.0, 0)
    assert e['model'] == model
    no_zone = _view(_ctx([FORD]), stops, [(FORD.car_code, [101], False)])[1]['explain']
    assert (no_zone['zone'], no_zone['center_stores'], no_zone['model']) == (False, 0, {})


def test_api_dispatch_explain_fields(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 200.0, agent=2)])
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    e = d['plan']['explain']
    assert {'trucks', 'zone', 'center_stores', 'window_stores', 'access_stores', 'load_cap_pct', 'pinned_trips',
            'solver', 'solver_iterations', 'model', 'carried', 'deferred'} <= set(e)
    assert (e['carried'], e['deferred']) == (0, 0)
    m = e['model']
    assert (m['km'], m['bypass'], m['unsnapped'], m['minutes']) == ('straight', False, None, 'zones')   # карты в тестах нет
    assert m['learned'] == {'travel': False, 'unload': False, 'loading': False, 'fuel': []}
    assert m['speed_city_source'] in ('gps', 'manual', 'default') and m['speed_city_kmh'] > 0
    trips = [tr for t in d['plan']['trucks'] for tr in t['trips']]
    assert trips and all({'loading_min', 'drive_min', 'unload_min', 'wait_min', 'back_min', 'idle_before_min',
                          'end_slack_min', 'load_limit_kg', 'over_limit', 'heavy_alone', 'l100', 'bypass_km',
                          'bypass_legs', 'others'} <= set(tr['explain']) for tr in trips)
    assert all({'drive_min', 'wait_min', 'unload_min', 'margin_min'} <= set(s) for tr in trips for s in tr['stops'])
    for tr in trips:
        x = tr['explain']
        assert x['loading_min'] + x['drive_min'] + x['unload_min'] + x['wait_min'] == pytest.approx(tr['minutes'], abs=0.6)

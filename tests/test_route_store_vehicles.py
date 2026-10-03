"""Допуск машины к магазину: сохранение, реальные решатели и ручные назначения без ERP."""
import copy
import json
import math
import random
import sqlite3
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import dispatch as dp, fleet as fl, store as st, vrp
from route_optimizer.vehicle_access import VehicleAccess, check_access
from test_route_optimizer import (DP_DAY, DP_DEPOT, DP_NORMS, TN, _dispatch_setup, _dorder,
                                  _dp_stops, _info, _no_road_map, client)

A = fl.FleetTruck('A', 'Большая', 5000, 20, center_ok=True)
B = fl.FleetTruck('B', 'Маленькая', 2000, 10, center_ok=True)


def ctx(rules=None, trucks=(A, B), tn=TN):
    return dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in trucks}, DP_NORMS, tn, 540,
                         overtime_minutes=660, vehicle_access=rules or {})


@pytest.mark.parametrize('raw', [[], {}, {'mode': 'other', 'trucks': []}, {'mode': 'allow', 'trucks': 'A'},
    {'mode': 'allow', 'trucks': [True]}, {'mode': 'allow', 'trucks': ['']},
    {'mode': 'deny', 'trucks': [' A']}, {'mode': 'allow', 'trucks': ['X']},
    {'mode': 'allow', 'trucks': ['A'], 'extra': 1}, {'mode': 'allow', 'trucks': [1]}])
def test_invalid_rule(raw):
    assert check_access(raw, {'A', 'B'})[1]


def test_allow_deny_empty_and_reset():
    rule, error = check_access({'mode': 'allow', 'trucks': ['B', 'A', 'B']}, {'A', 'B'})
    assert error is None and rule.trucks == ('A', 'B') and rule.allows('A') and not rule.allows('C')
    assert VehicleAccess('deny', ('A',)).allows('C') and not VehicleAccess('deny', ('A',)).allows('A')
    assert not VehicleAccess('allow', ()).allows('A') and VehicleAccess('deny', ()).allows('A')
    assert check_access(None) == (None, None)


def test_storage_roundtrip_and_schema11_migration(tmp_path):
    path = tmp_path / 'routes.db'
    store = st.Store(str(path))
    store.load()
    store.save_dispatch('2026-10-01', {'trips': [{'id': 7, 'truck': 'A', 'stops': [101]}]}, 'owner')
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE customer_vehicle_access')
        db.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
    assert store.load().vehicle_access == {}
    assert store.load_dispatch('2026-10-01')[0]['trips'][0]['id'] == 7
    rule = VehicleAccess('deny', ('A',))
    store.save_customer_vehicles(101, rule, 'owner')
    assert st.Store(str(path)).load().vehicle_access == {101: rule}
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT updated_by FROM customer_vehicle_access').fetchone() == ('owner',)
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() == (str(st.SCHEMA_VERSION),)
    store.save_customer_vehicles(101, None, 'owner')
    assert store.load().vehicle_access == {}


def test_corrupt_rule_does_not_silently_allow_every_truck(tmp_path):
    store = st.Store(str(tmp_path / 'routes.db'))
    store.load()
    store.save_customer_vehicles(101, VehicleAccess('allow', ('A',)), 'test')
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE customer_vehicle_access SET trucks='[false]'")
    with pytest.raises(st.StoreError, match='ограничение машин'):
        store.load()


@pytest.mark.parametrize('solver', [False, True])
@pytest.mark.parametrize('balance', [False, True])
def test_day_routes_never_mix_incompatible_shops(solver, balance):
    points = [(40.20, 44.52), (40.201, 44.52), (40.202, 44.52)]
    access = [{'A'}, {'B'}, {'A', 'B'}]
    trips = fl.route_day(points, [500] * 3, [10000] * 3, DP_DEPOT, [A, B], DP_NORMS, TN,
                         overflow=False, balance=balance, solver=solver, allowed_trucks=access)
    assert Counter(i for t in trips for i in t.items) == Counter(range(3))
    assert all(t.truck in access[i] for t in trips for i in t.items)


def test_heavy_order_splits_by_capacity_of_eligible_truck():
    trips = fl.route_day([(40.20, 44.52)], [4500], [10000], DP_DEPOT, [A, B], DP_NORMS, TN,
                         overflow=False, balance=True, solver=True, allowed_trucks=[{'B'}])
    assert len(trips) == 3 and all(t.truck == 'B' and t.items == (0,) for t in trips)
    assert sum(t.kg for t in trips) == 4500 and all(t.kg <= .9 * B.capacity_kg for t in trips)


def test_partial_heavy_delivery_is_removed_with_eligible_capacity():
    reasons = {}
    trips = fl.route_day([(40.20, 44.52)], [4500], [10000], DP_DEPOT, [A, B], DP_NORMS,
                         replace(TN, work_minutes=25), overflow=False, reasons=reasons, allowed_trucks=[{'B'}])
    assert trips == [] and reasons == {0: 'time'}


@pytest.mark.parametrize('overflow', [False, True])
def test_no_allowed_truck_is_not_bypassed_by_overtime(overflow):
    reasons = {}
    trips = fl.route_day([(40.20, 44.52)], [500], [10000], DP_DEPOT, [A, B], DP_NORMS, TN,
                         overflow=overflow, reasons=reasons, allowed_trucks=[set()])
    assert trips == [] and reasons == {0: 'vehicle'}


def test_access_intersects_center_permission():
    reasons = {}
    trucks = [replace(A, center_ok=False), B]
    trips = fl.route_day([(40.20, 44.52)], [500], [10000], DP_DEPOT, trucks, DP_NORMS, TN,
                         overflow=False, center=[True], reasons=reasons, allowed_trucks=[{'A'}])
    assert trips == [] and reasons == {0: 'center'}


def test_balance_moves_unrestricted_shops_and_preserves_restricted_ones():
    trucks = [replace(A, capacity_kg=1000, l100=10), replace(B, capacity_kg=1000, l100=10)]
    stops = [fl._Stop(1, 600, 1, 1, allowed_trucks=frozenset({'A'})),
             fl._Stop(2, 400, 1, 1), fl._Stop(3, 100, 1, 1, allowed_trucks=frozenset({'B'}))]
    matrix = [[0 if i == j else 1 for j in range(4)] for i in range(4)]
    trips = [fl.Trip('A', 2, 1000, 2, 3, 5, 1000, .3, False, (0, 1)),
             fl.Trip('B', 1, 100, 1, 2, 3, 1000, .2, False, (2,))]
    balanced = fl._balance(trips, stops, matrix, matrix, trucks, TN, None, None)
    assert balanced != trips
    assert all(stops[i].allowed_trucks is None or t.truck in stops[i].allowed_trucks for t in balanced for i in t.items)
    assert sorted(t.kg for t in balanced) == [500, 600]


def test_native_pyvrp_profiles_enforce_per_shop_access():
    if not vrp.available():
        pytest.skip('PyVRP не установлен')
    pieces = [vrp.Piece(1, 500, 1, None, None, False, True, frozenset({'A'})),
              vrp.Piece(2, 500, 1, None, None, False, True, frozenset({'B'}))]
    matrix = [[0, 1, 1], [1, 0, 1], [1, 1, 0]]
    shifts = [vrp.Shift('A', 0, 540), vrp.Shift('B', 0, 540)]
    result = vrp.solve(pieces, matrix, matrix, [vrp.Vehicle('A', 1000, 20, True),
                                            vrp.Vehicle('B', 1000, 1, True)], shifts, [(0, [[0]]), (1, [[1]])], .9)
    assert result is not None
    assert Counter(i for _, ts in result for t in ts for i in t) == Counter(range(2))
    assert all(shifts[k].truck in pieces[i].allowed_trucks for k, ts in result for t in ts for i in t)


def test_reject_bad_solver_assignment_even_if_solver_claims_success(monkeypatch):
    monkeypatch.setattr(vrp, 'available', lambda: True)
    monkeypatch.setattr(vrp, 'solve', lambda *a, **kw: [(0, [[0]])])
    # A — первый промежуток, но магазин разрешён только B.
    trips = fl.route_day([(40.20, 44.52)], [500], [10000], DP_DEPOT, [A, B], DP_NORMS, TN,
                         overflow=False, solver=True, allowed_trucks=[{'B'}])
    assert len(trips) == 1 and trips[0].truck == 'B'


def test_build_marks_unassigned_and_existing_incompatible_plan():
    stops, _ = _dp_stops([(101, (40.20, 44.52), 500)])
    context = ctx({101: VehicleAccess('allow', ('B',))})
    draft = dp.build(context, stops, None, ['A'], 'test')
    assert draft.no_vehicle == {101} and not draft.no_room and not draft.trips
    assert dp.plan_view(context, stops, draft, _info)['unassigned'][0]['no_vehicle']
    invalid = dp.Draft(trucks=['A', 'B'], trips=[dp.DraftTrip(1, 'A', [101])])
    view = dp.plan_view(context, stops, invalid, _info)
    assert view['summary']['vehicle_miss'] == 1 and view['trucks'][0]['trips'][0]['stops'][0]['vehicle_miss']
    assert dp.Draft.from_json(draft.to_json()).no_vehicle == {101}


@pytest.mark.parametrize('target', [{'to_trip': 2}, {'truck': 'A'}])
def test_manual_move_rejects_forbidden_truck_without_losing_source(target):
    stops, orders = _dp_stops([(101, (40.20, 44.52), 500), (102, (40.201, 44.52), 200)])
    context = ctx({101: VehicleAccess('deny', ('A',))})
    draft = dp.Draft(trucks=['A', 'B'], trips=[dp.DraftTrip(1, 'B', [101]), dp.DraftTrip(2, 'A', [102])])
    before = copy.deepcopy(draft.to_json())
    with pytest.raises(dp.DispatchError, match='не может обслуживать'):
        dp.apply_edit(context, stops, draft, {'action': 'move', 'customer_id': 101, 'from_trip': 1, **target},
                      {o.isn for o in orders})
    assert draft.to_json() == before


def test_pin_and_rebuild_reject_forbidden_pinned_truck():
    stops, orders = _dp_stops([(101, (40.20, 44.52), 500)])
    context = ctx({101: VehicleAccess('allow', ('B',))})
    draft = dp.Draft(trucks=['A', 'B'], trips=[dp.DraftTrip(1, 'B', [101])])
    with pytest.raises(dp.DispatchError, match='не может обслуживать'):
        dp.apply_edit(context, stops, draft, {'action': 'pin', 'trip': 1, 'truck': 'A'}, {o.isn for o in orders})
    draft.trips[0].truck, draft.trips[0].pinned = 'A', True
    with pytest.raises(dp.DispatchError, match='не может обслуживать'):
        dp.build(context, stops, draft, ['A', 'B'], 'test')
    draft.trips[0].pinned = False
    rebuilt = dp.build(context, stops, draft, ['A', 'B'], 'test')
    assert rebuilt.trips and all(t.truck == 'B' for t in rebuilt.trips)


def test_overtime_does_not_assign_to_forbidden_selected_truck():
    stops, _ = _dp_stops([(101, (40.20, 44.52), 500)])
    draft = dp.Draft(trucks=['A'], no_vehicle={101})
    updated = dp.overtime(ctx({101: VehicleAccess('allow', ('B',))}), stops, draft)
    assert updated.no_vehicle == {101} and not updated.trips


def test_api_rule_search_save_manual_validation_and_reset(client):
    _dispatch_setup(client, [_dorder(1, 101, 500), _dorder(2, 102, 300)])
    rule = {'mode': 'deny', 'trucks': ['CAR1']}
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 101, 'access': rule}).status_code == 200
    search = client.get('/api/routes/customer-vehicles?q=C101').get_json()
    assert search['customers'][0]['vehicle_access'] == rule
    assert client.get('/api/routes/customer-vehicles').get_json()['customers'][0]['customer_id'] == 101
    # Поиск работает и для магазина без заказов.
    assert client.get('/api/routes/customer-vehicles?q=C103').get_json()['customers'][0]['customer_id'] == 103
    built = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert built.status_code == 200, built.get_json()
    body = built.get_json()
    assigned = [(t, trip) for t in body['plan']['trucks'] for trip in t['trips']
                if any(s['customer_id'] == 101 for s in trip['stops'])]
    assert assigned and all(t['car_code'] == 'CAR2' for t, _ in assigned)
    blocked = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': body['rev'],
                          'action': 'pin', 'trip': assigned[0][1]['id'], 'truck': 'CAR1'})
    assert blocked.status_code == 400
    assert client.get('/api/routes/dispatch?date=2026-10-01').get_json()['rev'] == body['rev']
    invalid = client.post('/api/routes/customer-vehicles', json={'customer_id': 101,
                          'access': {'mode': 'allow', 'trucks': ['UNKNOWN']}})
    assert invalid.status_code == 400
    assert client.application.extensions['route_optimizer'].store.load().vehicle_access[101].to_json() == rule
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': True, 'access': None}).status_code == 400
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 987654321, 'access': None}).status_code == 400
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 101, 'access': None}).status_code == 200
    assert client.get('/api/routes/customer-vehicles?q=C101').get_json()['customers'][0]['vehicle_access'] is None


def test_api_incompatible_pinned_rebuild_is_400_and_preserves_draft(client):
    _dispatch_setup(client, [_dorder(1, 101, 500)])
    body = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1']}).get_json()
    trip = body['plan']['trucks'][0]['trips'][0]
    pinned = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': body['rev'],
                         'action': 'pin', 'trip': trip['id'], 'truck': 'CAR1'}).get_json()
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 101,
                       'access': {'mode': 'deny', 'trucks': ['CAR1']}}).status_code == 200
    view = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert view['plan']['summary']['vehicle_miss'] == 1
    response = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert response.status_code == 400 and 'не может обслуживать' in response.get_json()['error']
    assert client.get('/api/routes/dispatch?date=2026-10-01').get_json()['rev'] == pinned['rev']


def test_random_days_preserve_weights_and_access():
    rng = random.Random(73)
    for _ in range(8):
        points = [(40.19 + rng.random() * .03, 44.52 + rng.random() * .04) for _ in range(12)]
        kgs = [rng.randrange(50, 400) for _ in points]
        access = [rng.choice([None, {'A'}, {'B'}, {'A', 'B'}, set()]) for _ in points]
        reasons = {}
        trips = fl.route_day(points, kgs, [10000] * 12, DP_DEPOT, [A, B], DP_NORMS, TN,
                             overflow=False, balance=True, solver=True, reasons=reasons, allowed_trucks=access)
        placed = Counter(i for t in trips for i in t.items)
        assert all(n == 1 for n in placed.values())
        assert set(placed) | set(reasons) == set(range(12))
        assert not set(placed) & set(reasons)
        assert all(access[i] is None or t.truck in access[i] for t in trips for i in t.items)
        assert sum(t.kg for t in trips) == pytest.approx(sum(kgs[i] for i in placed))

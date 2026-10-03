"""Payload costs: synthetic measurements, private SQLite, no ERP connection."""
from dataclasses import replace
import sqlite3

import pytest

from route_optimizer import dispatch as dp, fleet as fl, optimize as opt, search as sr, store as st
from route_optimizer.running_costs import LOAD_COST_FIELDS, profile_fields, route_cost
from test_route_optimizer import (REF, DP_DEPOT, DP_NORMS, TN, HOWO, _dp_ctx, _dp_stops, _info,
                                  _big_snapshot, _truck_bundle, client, _dispatch_setup, _dorder, _no_road_map)


PROFILE = dict(fuel_empty_l_per_100km=10.0, fuel_full_l_per_100km=20.0,
               wear_amd_per_km=2.0, wear_load_amd_per_km=10.0)
TRUCK = fl.FleetTruck('T', 'test', 1000.0, 15.0, **PROFILE)


def save(store, payload):
    changes, errors = st.validate_payload(payload, store.load(), REF)
    assert not errors
    store.save(changes, 'test')


def test_same_distance_heavy_first_reduces_fuel_and_wear():
    heavy_first = route_cost([10.0] * 3, [900.0, 100.0], TRUCK)
    light_first = route_cost([10.0] * 3, [100.0, 900.0], TRUCK)
    assert heavy_first.liters == pytest.approx(4.1)
    assert light_first.liters == pytest.approx(4.9)
    assert heavy_first.wear_amd == pytest.approx(161.0)
    assert light_first.wear_amd == pytest.approx(241.0)
    assert heavy_first.payload_tonne_km == pytest.approx(11.0)


def test_empty_return_and_reloading_cost_each_trip():
    half = route_cost([10.0, 10.0], [500.0], TRUCK)
    full = route_cost([10.0, 10.0], [1000.0], TRUCK)
    assert (half.liters, full.liters) == pytest.approx((2.5, 3.0))
    assert (half.wear_amd, full.wear_amd) == pytest.approx((65.0, 140.0))
    assert 2 * half.liters == pytest.approx(5.0)
    assert 2 * half.payload_tonne_km == pytest.approx(10.0)


def test_unconfigured_keeps_existing_fuel_without_invented_wear():
    cost = route_cost([10.0, 20.0], [900.0], fl.FleetTruck('T', None, 1000.0, 15.0))
    assert cost.liters == pytest.approx(4.5)
    assert cost.wear_amd == 0.0
    assert not cost.fuel_load_configured and not cost.wear_configured


@pytest.mark.parametrize('extra', [
    {'fuel_empty_l_per_100km': 10.0},
    {'fuel_full_l_per_100km': 20.0},
    {'fuel_empty_l_per_100km': 20.0, 'fuel_full_l_per_100km': 10.0},
    {'fuel_empty_l_per_100km': True, 'fuel_full_l_per_100km': 20.0},
    {'fuel_empty_l_per_100km': float('nan'), 'fuel_full_l_per_100km': 20.0},
    {'wear_amd_per_km': -1.0}, {'wear_load_amd_per_km': float('inf')},
])
def test_invalid_profile_cannot_be_saved(tmp_path, extra):
    store = st.Store(str(tmp_path / 'settings.sqlite'))
    changes, errors = st.validate_payload({'trucks': [{'car_code': 'CAR1', **extra}]}, store.load(), REF)
    assert changes is None and errors
    assert store.load().trucks == {}


def test_private_schema_9_migration_retains_existing_data(tmp_path):
    path = tmp_path / 'settings.sqlite'
    store = st.Store(str(path))
    save(store, {'trucks': [{'car_code': 'CAR1', 'capacity_kg': 5000, 'fuel_l_per_100km': 18.0,
                           'active': True, 'center_ok': False}]})
    with sqlite3.connect(path) as conn:
        for key in LOAD_COST_FIELDS:
            conn.execute(f'ALTER TABLE trucks DROP COLUMN {key}')
        conn.execute("UPDATE meta SET value = '9' WHERE key = 'schema_version'")
    migrated = st.Store(str(path)).load()
    assert migrated.trucks['CAR1'].capacity_kg == 5000
    assert all(value is None for value in profile_fields(migrated.trucks['CAR1']).values())
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(st.SCHEMA_VERSION)
        assert conn.execute("SELECT COUNT(*) FROM route_measurement").fetchone()[0] == 0


def test_profile_round_trip_and_partial_update_for_erp_and_manual_trucks(tmp_path):
    store = st.Store(str(tmp_path / 'settings.sqlite'))
    save(store, {'trucks': [{'car_code': 'CAR1', 'capacity_kg': 1000, 'fuel_l_per_100km': 15.0,
                           'active': True, **PROFILE}],
                 'manual_trucks': [{'car_code': 'LOCAL1', 'capacity_kg': 1000, 'fuel_l_per_100km': 15.0,
                                    **PROFILE}]})
    save(store, {'trucks': [{'car_code': 'CAR1', 'center_ok': True}],
                 'manual_trucks': [{'car_code': 'LOCAL1', 'name': 'Changed'}]})
    for truck in store.load().trucks.values():
        assert profile_fields(truck) == PROFILE
    ready, incomplete = fl.fleet_trucks(store.load().trucks, {})
    assert not incomplete and len(ready) == 2
    assert all(profile_fields(t) == PROFILE for t in ready)


def test_heavy_first_is_used_but_receiving_window_is_preserved():
    d = [[0.0, 10.0, 10.0], [10.0, 0.0, 10.0], [10.0, 10.0, 0.0]]
    stops = [fl._Stop(1, 100.0, 1.0, 0.0), fl._Stop(2, 900.0, 1.0, 0.0)]
    trips = fl.plan_trips(stops, d, d, [TRUCK], TN)
    assert len(trips) == 1 and trips[0].items == (1, 0)
    assert trips[0].liters == pytest.approx(4.1)
    stops[0].late = 10.0
    trips = fl.plan_trips(stops, d, d, [TRUCK], TN, overflow=False)
    assert len(trips) == 1 and trips[0].items == (0, 1)
    assert trips[0].liters == pytest.approx(4.9)


def test_vehicle_selected_by_payload_cost_not_base_consumption():
    small = replace(TRUCK, l100=10.0, fuel_full_l_per_100km=30.0, wear_amd_per_km=None, wear_load_amd_per_km=None)
    big = replace(small, car_code='B', capacity_kg=2000.0, l100=15.0,
                  fuel_empty_l_per_100km=12.0, fuel_full_l_per_100km=14.0)
    d = [[0.0, 10.0], [10.0, 0.0]]
    [trip] = fl.plan_trips([fl._Stop(1, 1000.0, 1.0, 0.0)], d, d, [small, big], TN)
    assert trip.truck == 'B' and trip.liters == pytest.approx(2.5)


def test_dispatch_and_planner_use_same_cost_for_split_heavy_order(monkeypatch):
    monkeypatch.setattr(fl.vrp, 'available', lambda: False)
    truck = replace(TRUCK, capacity_kg=5000.0)
    ctx = _dp_ctx((truck,))
    stops, _ = _dp_stops([(1, (40.17, 44.49), 12000.0)])
    draft = dp.build(ctx, stops, None, [truck.car_code], 'now')
    view = dp.plan_view(ctx, stops, draft, _info)
    trips = view['trucks'][0]['trips']
    assert len(trips) == 3 and not view['unassigned']
    expected = fl.trip_running_cost([stops[0].point], [4000.0], ctx.depot, ctx.norms, truck)
    assert all(t['kg'] == 4000 and t['liters'] == pytest.approx(round(expected.liters, 1)) for t in trips)
    assert view['summary']['wear_amd'] == 3 * round(expected.wear_amd)
    assert not view['summary']['fuel_load_unconfigured'] and not view['summary']['wear_unconfigured']


def test_overtime_keeps_90_percent_limit(monkeypatch):
    monkeypatch.setattr(fl.vrp, 'available', lambda: False)
    truck = replace(HOWO, capacity_kg=5000.0)
    ctx = replace(_dp_ctx((truck,)), overtime_minutes=600.0)
    stops, _ = _dp_stops([(1, (40.17, 44.49), 2400.0), (2, (40.1701, 44.4901), 2400.0)])
    draft = dp.Draft(trucks=[truck.car_code], no_room={1, 2})
    dp.overtime(ctx, stops, draft)
    view = dp.plan_view(ctx, stops, draft, _info)
    assert not view['unassigned']
    assert all(t['load_pct'] <= 90 for row in view['trucks'] for t in row['trips'])


def test_load_search_cost_and_rebuild_are_consistent():
    d = [[0.0, 10.0, 10.0], [10.0, 0.0, 10.0], [10.0, 10.0, 0.0]]
    f = sr.FleetEstimate(d, [True] * 3, [0.0, 100.0, 900.0], capacity_kg=1000.0,
                         per_km=1.0, per_load_km=1.0, wear_load_per_km=10.0,
                         overtime_per_min=0.0, capacity_min=540.0, unload_stop=0.0,
                         unload_tonne=0.0, speed_city_kmh=60.0, speed_region_kmh=60.0, trip_minutes=540.0)
    assert f._load_charge([0, 2, 1]) == pytest.approx(112.0)
    assert f._load_charge([0, 1, 2]) == pytest.approx(200.0)
    f.build([(0, 1, [True] * sr.TRUCK_SCENARIOS), (0, 2, [True] * sr.TRUCK_SCENARIOS)])
    assert f.cost[0] == f.cost_of(f.km[0], f.mins[0], f.tours[0])
    assert f.consistency_error([(0, 1, [True] * sr.TRUCK_SCENARIOS), (0, 2, [True] * sr.TRUCK_SCENARIOS)]) == 0.0


def test_balance_recosts_remaining_payload_and_never_increases_total():
    truck = replace(TRUCK, capacity_kg=5000.0)
    d = [[0.0 if a == b else 10.0 for b in range(5)] for a in range(5)]
    stops = [fl._Stop(i + 1, kg, 1.0, 0.0) for i, kg in enumerate([2500.0, 2500.0, 1500.0, 1500.0])]
    before = fl.plan_trips(stops, d, d, [truck], TN)
    after = fl._balance(before, stops, d, d, [truck], TN, None, None)
    assert sorted(i for t in after for i in t.items) == [0, 1, 2, 3]
    assert sum(t.liters * TN.fuel_price + t.wear_amd for t in after) < sum(t.liters * TN.fuel_price + t.wear_amd for t in before)
    for t in after:
        expected = route_cost([10.0] * (len(t.items) + 1), [stops[i].kg for i in t.items], truck)
        assert (t.liters, t.wear_amd) == pytest.approx((expected.liters, expected.wear_amd))


def test_solver_candidate_is_recosted_by_actual_payload(monkeypatch):
    monkeypatch.setattr(fl.vrp, 'available', lambda: True)
    monkeypatch.setattr(fl.vrp, 'solve', lambda *args, **kw: [(0, [[1, 0]])])
    truck = replace(TRUCK, capacity_kg=2000.0)
    d = [[0.0, 10.0, 10.0], [10.0, 0.0, 10.0], [10.0, 10.0, 0.0]]
    stops = [fl._Stop(1, 100.0, 1.0, 0.0), fl._Stop(2, 900.0, 1.0, 0.0)]
    before = fl.plan_trips(stops, d, d, [truck], TN, overflow=False)
    after = fl._solver(before, stops, d, d, [truck], TN, None, None)
    assert after is not None and len(after) == 1
    expected = fl._sequence_cost(after[0].items, stops, d, truck)
    assert (after[0].liters, after[0].wear_amd) == pytest.approx((expected.liters, expected.wear_amd))


@pytest.mark.parametrize('mode', ['days', 'transfer'])
def test_manager_optimization_reports_and_checks_full_wear_cost(mode):
    snap, bundle = _big_snapshot(n_agents=1, per_agent=8), _truck_bundle()
    bundle = replace(bundle, trucks={c: replace(t, **PROFILE) for c, t in bundle.trucks.items()})
    result = opt.run_optimization(snap, bundle, None, [], {'mode': mode}).result
    gate = result['fleet_gate']
    assert gate['operating_before_amd'] > 0 and gate['operating_after_amd'] > 0
    assert gate['ok'] == (gate['liters_after'] <= gate['liters_before'] + .1
                          and gate['operating_after_amd'] <= gate['operating_before_amd'] + 1e-6)


def test_profile_settings_api_persists_only_private_store(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    response = client.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR1', **PROFILE}]})
    assert response.status_code == 200, response.get_json()
    data = client.get('/api/routes/settings').get_json()
    row = next(t for t in data['trucks'] if t['car_code'] == 'CAR1')
    assert {k: row[k] for k in LOAD_COST_FIELDS} == PROFILE

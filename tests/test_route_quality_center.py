"""Качество состава и шкалы рейсов; синтетические данные, без ERP."""
from collections import Counter
from dataclasses import replace
import math
from datetime import date

import pytest

from route_optimizer import fleet as fl
from route_optimizer import dispatch as dp
from route_optimizer.evaluate import Norms
from route_optimizer.route_quality import improve_routes


TRUCK = fl.FleetTruck('A', None, 2000.0, 10.0,
                      fuel_empty_l_per_100km=8.0, fuel_full_l_per_100km=16.0)
TN = fl.TruckNorms(500.0, 0.0, 0.0)


def matrix(points):
    return [[abs(a - b) for b in points] for a in points]


def trips(sequences, stops, d, trucks=(TRUCK,), codes=None):
    codes = codes or [TRUCK.car_code] * len(sequences)
    by = {t.car_code: t for t in trucks}
    out = []
    for code, seq in zip(codes, sequences):
        cost = fl._sequence_cost(seq, stops, d, by[code])
        out.append(fl.Trip(code, len(seq), math.fsum(stops[i].kg for i in seq),
                           math.fsum(stops[i].revenue for i in seq), fl._closed(seq, stops, d),
                           0.0, by[code].capacity_kg, cost.liters, False, tuple(seq),
                           cost.wear_amd, cost.payload_tonne_km))
    return out


def test_removes_small_reload_and_preserves_all_goods():
    d = matrix([0, 10, 11])
    stops = [fl._Stop(1, 500, 10000, 0), fl._Stop(2, 500, 20000, 0)]
    before = trips([[0], [1]], stops, d)
    after = improve_routes(before, stops, d, d, [TRUCK], TN, None, None)
    assert len(after) == 1
    assert set(after[0].items) == {0, 1}
    assert after[0].kg == 1000 and after[0].revenue == 30000
    assert after[0].km < sum(t.km for t in before)
    assert sum(t.liters for t in after) < sum(t.liters for t in before)
    assert [t.items for t in before] == [(0,), (1,)]


def test_relocates_to_the_nearby_vehicle_and_checks_its_access():
    d = matrix([0, 1, 10, 11])
    other = replace(TRUCK, car_code='B')
    stops = [fl._Stop(i + 1, 300, 100, 0) for i in range(3)]
    before = trips([[0, 2], [1]], stops, d, (TRUCK, other), ['A', 'B'])
    after = improve_routes(before, stops, d, d, [TRUCK, other], TN, None, None)
    assert sum(t.km for t in after) < sum(t.km for t in before)
    assert Counter(i for t in after for i in t.items) == Counter([0, 1, 2])
    stops[2].allowed_trucks = frozenset({'A'})
    guarded = improve_routes(before, stops, d, d, [TRUCK, other], TN, None, None)
    assert all(t.truck == 'A' for t in guarded if 2 in t.items)


@pytest.mark.parametrize('guard', ['capacity', 'solo', 'fixed', 'shared', 'windows', 'reserve'])
def test_cheaper_merge_respects_constraints(guard):
    d = matrix([0, 10, 11])
    m = [row[:] for row in d]
    stops = [fl._Stop(1, 500, 100, 0), fl._Stop(2, 500, 100, 0)]
    tn, fixed, sequences = TN, None, [[0], [1]]
    if guard == 'capacity':
        stops[0].kg, stops[1].kg = 1200, 1000
    elif guard == 'solo':
        stops[1].solo = True
    elif guard == 'fixed':
        fixed = [('A', [1], 0)]
    elif guard == 'shared':
        sequences = [[0, 1], [0]]
    elif guard == 'windows':
        m[1][2] = m[2][1] = 100
        stops[0].late = stops[1].late = 30
    elif guard == 'reserve':
        m[1][2] = m[2][1] = 100
        tn = replace(TN, work_minutes=70, end_reserve=20)
    before = trips(sequences, stops, d)
    after = improve_routes(before, stops, d, m, [TRUCK], tn, None, fixed)
    assert [t.items for t in after] == [t.items for t in before]


class LoadingMatrix(list):
    def __init__(self, values, tn):
        super().__init__(values)
        self.tn = tn
    def load(self, kg):
        return self.tn.load(kg)
    def travel(self, a, b, minute):
        return self[a][b]


@pytest.mark.parametrize('early_window', [False, True])
def test_preloaded_trip_order_uses_whole_day_including_windows(early_window):
    d = matrix([0, 1, 2])
    tn = replace(TN, preload=True, warehouse_load_fixed_min=60, warehouse_load_min_per_tonne=10)
    m = LoadingMatrix(d, tn)
    stops = [fl._Stop(1, 1000, 100, 0), fl._Stop(2, 1600, 100, 0)]
    if early_window:
        stops[0].late = 3
    before = trips([[0], [1]], stops, d)
    after = improve_routes(before, stops, d, m, [TRUCK], tn, None, None)
    assert [t.items for t in after] == ([(0,), (1,)] if early_window else [(1,), (0,)])
    old = fl._days(before, stops, m, {}, tn)['A']
    new = fl._days(after, stops, m, {}, tn)['A']
    if not early_window:
        assert new[-1][1] + new[-1][2] < old[-1][1] + old[-1][2]


def test_exact_cost_keeps_short_search_result_when_long_search_is_worse(monkeypatch):
    n = 12
    d = matrix(list(range(n + 1)))
    stops = [fl._Stop(i + 1, 1000 if i == 0 else 20, 100, 0) for i in range(n)]
    truck = replace(TRUCK, capacity_kg=3000)
    before = trips([list(reversed(range(n)))], stops, d, (truck,))
    seen = []
    def solve(*args, **kwargs):
        seen.append((kwargs['seed'], kwargs['iterations']))
        # pieces идут в порядке исходного рейса: короткая проба разворачивает
        # его и выгружает тяжёлый ближний заказ сразу, длинная — в конце.
        seq = list(reversed(range(n))) if kwargs['iterations'] == 5000 and kwargs['seed'] == 1 else list(range(n))
        return [(0, [seq])]
    monkeypatch.setattr(fl.vrp, 'available', lambda: True)
    monkeypatch.setattr(fl.vrp, 'solve', solve)
    got = fl._solver(before, stops, d, d, [truck], TN, None, None, (), 20000)
    assert got is not None and got[0].items[0] == 0
    assert (1, 5000) in seen and (1, 20000) in seen
    assert got[0].liters < before[0].liters


def test_repeatable_and_never_loses_a_split_delivery():
    d = matrix([0, 1, 2, 10])
    stops = [fl._Stop(1, 2100, 210, 0), fl._Stop(2, 300, 30, 0), fl._Stop(3, 300, 30, 0)]
    before = trips([[0], [0], [1], [2]], stops, d)
    a = improve_routes(before, stops, d, d, [TRUCK], TN, None, None)
    b = improve_routes(before, stops, d, d, [TRUCK], TN, None, None)
    assert a == b
    assert Counter(i for t in a for i in t.items) == Counter(i for t in before for i in t.items)
    assert [t.items for t in a if 0 in t.items] == [(0,), (0,)]


class InsertionRoads:
    def truck(self, **kwargs):
        return self
    def ensure(self, points):
        pass
    def km(self, a, b):
        return abs(a[1] - b[1])
    def minutes(self, a, b, city):
        if a == b:
            return 0.0
        return 100.0 if a[1] == 1 and b[1] == 10 else 1.0


@pytest.mark.parametrize('pin', ['none', 'approval', 'manual'])
def test_added_order_tries_other_positions_before_creating_a_reload(pin):
    truck = replace(TRUCK, fuel_empty_l_per_100km=None, fuel_full_l_per_100km=None)
    norms = Norms(70, 1, 30, 40, (0, 0), 20, 0, 0, roads=InsertionRoads())
    ctx = dp.DayContext(date(2026, 11, 5), (0, 0), {'A': truck}, norms,
                        fl.TruckNorms(70, 0, 0), 0, windows={1: (0, 3)})
    stops = [dp.Stop(1, (0, 10), 'erp', 300, 100, (), 0),
             dp.Stop(2, (0, 1), 'erp', 300, 100, (), 0)]
    draft = dp.Draft(trucks=['A'], trips=[dp.DraftTrip(1, 'A', [1], pinned=pin != 'none')], next_id=2)
    if pin == 'approval':
        draft.approved = {'at': '2026-11-04T19:00', 'by': 'test', 'pinned': [1]}
    placed = dp.place_added(ctx, stops, draft, [2])
    if pin == 'manual':
        assert placed == {2: 2} and draft.trips[0].stops == [1]
    else:
        assert placed == {2: 1}
        assert len(draft.trips) == 1 and draft.trips[0].stops == [1, 2]
        assert draft.trips[0].pinned == (pin == 'approval')


@pytest.mark.parametrize('load_cap, expected_trips', [(0.9, 2), (1.0, 1)])
def test_quality_uses_the_configured_load_limit(load_cap, expected_trips):
    d = matrix([0, 10, 11])
    stops = [fl._Stop(1, 950, 100, 0), fl._Stop(2, 950, 100, 0)]
    before = trips([[0], [1]], stops, d)
    after = improve_routes(before, stops, d, d, [TRUCK], replace(TN, load_cap=load_cap), None, None)
    assert len(after) == expected_trips
    assert all(t.kg <= TRUCK.capacity_kg * load_cap + 1e-6 for t in after)


@pytest.mark.parametrize('center_permit', [False, True])
def test_cheaper_vehicle_never_takes_center_without_its_own_permit(center_permit):
    d = matrix([0, 10, 11])
    permitted = replace(TRUCK, center_ok=True, l100=20.0,
                        fuel_empty_l_per_100km=None, fuel_full_l_per_100km=None)
    cheaper = replace(TRUCK, car_code='B', center_ok=center_permit, l100=10.0,
                      fuel_empty_l_per_100km=None, fuel_full_l_per_100km=None)
    stops = [fl._Stop(1, 500, 100, 0, center=True), fl._Stop(2, 500, 100, 0)]
    before = trips([[0], [1]], stops, d, [permitted, cheaper], ['A', 'B'])
    after = improve_routes(before, stops, d, d, [permitted, cheaper], TN, None, None)
    by = {t.car_code: t for t in [permitted, cheaper]}
    assert Counter(i for t in after for i in t.items) == Counter([0, 1])
    assert all(by[t.truck].center_ok for t in after if 0 in t.items)
    if center_permit:
        assert all(t.truck == 'B' for t in after)

"""Порядок после решателя: стоимость груза, последующие окна, смена и закрепления; без ERP."""
from collections import Counter
from dataclasses import replace
import math

import pytest

from route_optimizer import fleet as fl
from test_route_optimizer import DP_DEPOT, DP_NORMS

TRUCK = fl.FleetTruck('T', None, 2000.0, 15.0, fuel_empty_l_per_100km=10.0,
                      fuel_full_l_per_100km=20.0, wear_load_amd_per_km=3.0)
TN = fl.TruckNorms(100.0, 0.0, 0.0)
D = [[0.0, 10.0, 2.0, 1.0], [10.0, 0.0, 8.0, 9.0],
     [2.0, 8.0, 0.0, 1.0], [1.0, 9.0, 1.0, 0.0]]


def trips_for(sequences, stops, m=D, truck=TRUCK, tn=TN):
    shares = Counter(i for seq in sequences for i in seq)
    vs = [replace(s, kg=s.kg / shares.get(i, 1), revenue=s.revenue / shares.get(i, 1),
                  unload=tn.unload(s.kg / shares.get(i, 1))) for i, s in enumerate(stops)]
    out = []
    for seq in sequences:
        cost = fl._sequence_cost(seq, vs, D, truck)
        out.append(fl.Trip(truck.car_code, len(seq), math.fsum(vs[i].kg for i in seq),
                           math.fsum(vs[i].revenue for i in seq), fl._closed(seq, vs, D),
                           fl._schedule(seq, vs, m, 0.0)[0], truck.capacity_kg, cost.liters,
                           False, tuple(seq), cost.wear_amd, cost.payload_tonne_km))
    return out


def stops_for():
    return [fl._Stop(1, 100.0, 100.0, 0.0), fl._Stop(2, 900.0, 900.0, 0.0),
            fl._Stop(3, 100.0, 100.0, 0.0)]


@pytest.mark.parametrize('solver_available', [False, True])
def test_public_route_day_polishes_solver_and_fallback(monkeypatch, solver_available):
    stops = stops_for()[:2]
    before = trips_for([[0, 1]], stops)
    monkeypatch.setattr(fl, '_matrices', lambda *args: (D, D))
    monkeypatch.setattr(fl, 'plan_trips', lambda *args, **kwargs: before)
    monkeypatch.setattr(fl.vrp, 'available', lambda: solver_available)
    monkeypatch.setattr(fl.vrp, 'solve', lambda *args, **kwargs: [(0, [[0, 1]])])
    after = fl.route_day([(40.0, 44.0), (40.1, 44.1)], [100.0, 900.0], [100.0, 900.0],
                         DP_DEPOT, [TRUCK], DP_NORMS, TN, overflow=False, solver=True)
    assert after[0].items == (1, 0)
    assert after[0].km == before[0].km
    assert after[0].liters < before[0].liters
    assert after[0].wear_amd < before[0].wear_amd
    assert (after[0].truck, after[0].kg, after[0].revenue) == (before[0].truck, 1000.0, 1000.0)


def test_reversal_blocked_by_customer_window():
    stops = stops_for()[:2]
    stops[0].late = 12.0
    before = trips_for([[0, 1]], stops)
    # По симметричному времени разворот прибудет к дальней точке в ту же минуту;
    # направленное время даёт опоздание, хотя топливо по дорогам выгоднее.
    m = [row[:] for row in D]
    m[2][1] = 20.0
    after = fl._polish_orders(before, stops, D, m, [TRUCK], TN, None, None)
    assert after == before


@pytest.mark.parametrize('constraint', ['window', 'shift', 'pinned'])
def test_reversal_checks_the_next_trip(constraint):
    stops = stops_for()
    m = [row[:] for row in D]
    m[2][1] = 13.0  # первый рейс после разворота на пять минут длиннее
    if constraint == 'window':
        stops[2].late = 22.0
    tn = replace(TN, work_minutes=24.0) if constraint == 'shift' else TN
    before = trips_for([[0, 1], [2]], stops, m=m, tn=tn)
    fixed = [('T', (2,), 20.0)] if constraint == 'pinned' else None
    after = fl._polish_orders(before, stops, D, m, [TRUCK], tn, None, fixed)
    assert after == before


def test_cheaper_longer_reversal_is_allowed_when_day_fits():
    stops = stops_for()
    m = [row[:] for row in D]
    m[2][1] = 13.0
    before = trips_for([[0, 1], [2]], stops, m=m)
    after = fl._polish_orders(before, stops, D, m, [TRUCK], TN, None, None)
    assert after[0].items == (1, 0)
    assert after[0].minutes > before[0].minutes
    assert fl._days(after, stops, m, {})['T'][-1][1] + after[-1].minutes <= TN.work_minutes


def test_fixed_order_and_unconfigured_norms_are_preserved():
    stops = stops_for()[:2]
    before = trips_for([[0, 1]], stops)
    assert fl._polish_orders(before, stops, D, D, [TRUCK], TN, None, [('T', (0, 1), 0.0)]) == before
    unconfigured = replace(TRUCK, fuel_empty_l_per_100km=None, fuel_full_l_per_100km=None,
                           wear_load_amd_per_km=None)
    assert fl._polish_orders(before, stops, D, D, [unconfigured], TN, None, None) == before


def test_split_delivery_uses_actual_share_and_preserves_total_load():
    stops = stops_for()
    stops[1].kg = 1800.0  # один заказ делится на два рейса
    before = trips_for([[0, 1], [1, 2]], stops)
    after = fl._polish_orders(before, stops, D, D, [TRUCK], TN, None, None)
    shared = fl._shared(after, stops, TN)
    assert Counter(i for t in after for i in t.items) == Counter({0: 1, 1: 2, 2: 1})
    assert sum(t.kg for t in after) == sum(s.kg for s in stops)
    for trip in after:
        cost = fl._sequence_cost(trip.items, shared, D, TRUCK)
        assert (trip.liters, trip.wear_amd, trip.payload_tonne_km) == pytest.approx(
            (cost.liters, cost.wear_amd, cost.payload_tonne_km))


def test_dynamic_timing_checks_loading_and_recalculates_next_trip():
    class Dynamic(list):
        def load(self, kg):
            return 3.0

        def travel(self, a, b, start):
            return D[a][b] * (1.5 if start >= 25.0 else 1.0)

    stops = stops_for()
    m = Dynamic(D)
    before = trips_for([[0, 1], [2]], stops, m=m)
    after = fl._polish_orders(before, stops, D, m, [TRUCK], TN, {'T': 5.0}, None)
    days = fl._days(after, stops, m, {'T': 5.0})['T']
    assert after[0].items == (1, 0)
    assert all(after[k].minutes == pytest.approx(minutes) for k, _, minutes, _ in days)
    assert days[-1][1] + days[-1][2] <= TN.work_minutes

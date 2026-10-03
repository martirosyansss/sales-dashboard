from dataclasses import replace

import pytest

from route_optimizer import evaluate as ev, fleet as fl, optimize, store
from route_optimizer.plan import PlanDay
from route_optimizer.traffic_validation import TrafficProfile
from test_route_optimizer import _bundle, make_snapshot


HOME = (40.18, 44.51)
POINTS = [(40.19, 44.51), (40.20, 44.51)]


def norms(profile=None):
    return ev.Norms(35, 1, 60, 60, HOME, 100, 0, 0,
                    traffic=profile, traffic_start_min=520)


def test_visits_shift_the_next_leg_into_peak_hour(monkeypatch):
    monkeypatch.setattr(ev.Norms, 'km', lambda self, a, b: 10.)
    profile = TrafficProfile({(True, 0, 9): .5})
    n = norms(profile)
    assert ev.route_metrics(POINTS, HOME, n, 1, [0, 0])[1] == pytest.approx(40)
    assert ev.route_metrics(POINTS, HOME, n, 1, [20, 0])[1] == pytest.approx(50)


def test_manager_day_uses_its_own_weekday(monkeypatch):
    monkeypatch.setattr(ev.Norms, 'km', lambda self, a, b: 10.)
    n = replace(norms(TrafficProfile({(True, 1, 8): .5})), traffic_start_min=480)
    assert ev.route_metrics([POINTS[0]], HOME, n, 5)[1] == pytest.approx(20)
    assert ev.route_metrics([POINTS[0]], HOME, n, 6)[1] == pytest.approx(40)


def test_manager_shift_checks_include_service_and_traffic(monkeypatch):
    monkeypatch.setattr(ev.Norms, 'km', lambda self, a, b: 10.)
    n = norms(TrafficProfile({(True, 0, 9): .5}))
    visits = [ev.VisitModel(i + 1, point, minutes, ev.NO_DRAW, ev.NO_DRAW, ev.NO_DRAW)
              for i, (point, minutes) in enumerate(zip(POINTS, (20, 0)))]
    result = ev.evaluate_day(PlanDay(1, 1, 1, ()), visits, home=None, norms=n,
                             manager_l100=10, workday=True)
    assert result.drive_min == pytest.approx(20)
    assert result.plan_min == pytest.approx(40)
    assert result.overtime_min == pytest.approx(5)
    assert 'overtime' in result.flags


def test_no_profile_retains_the_original_arithmetic():
    n = norms()
    path = [HOME, *POINTS, HOME]
    km = minutes = 0.
    for a, b in zip(path, path[1:]):
        leg = n.km(a, b)
        km += leg
        minutes += leg / n.speed_city_kmh * 60.
    assert ev.route_metrics(POINTS, HOME, n, 6, [20, 30]) == (km, minutes)


def test_export_blocks_plan_that_only_fits_without_traffic():
    bundle = _bundle()
    bundle = replace(bundle, settings=dict(bundle.settings, work_start='09:00', work_end='20:00',
                                          visit_min_small=1, visit_min_medium=1, visit_min_large=1))
    distance = lambda a, b: 0. if a == b else 10.
    profile = TrafficProfile({(city, group, hour): .05 for city in (True, False)
                              for group in (0, 1) for hour in range(24)})
    baseline = optimize.plan_export(make_snapshot(), bundle, [], distance=distance)
    predicted = optimize.plan_export(make_snapshot(), bundle, [], distance=distance,
                                     calib=ev.Calibration(None, None, None, 0, traffic=profile))
    assert baseline['time_gate']['ok']
    assert not predicted['time_gate']['ok']
    assert predicted['time_gate']['days']


def test_fleet_uses_truck_start_when_manager_starts_later():
    settings = dict(store.DEFAULT_SETTINGS, work_start='09:00', truck_work_start='08:00',
                    speed_city_kmh=60, speed_region_kmh=60, city_radius_km=100)
    n = ev.Norms.from_settings(settings)
    profile = TrafficProfile({(True, 0, 8): .5})
    n = replace(n, traffic=profile, city_center=HOME)
    tn = fl.TruckNorms.from_settings(settings)
    _, matrix = fl._matrices([POINTS[0]], HOME, n, tn)
    expected = n.km(HOME, POINTS[0]) * 2
    assert matrix.travel(0, 1, 0) == pytest.approx(expected)
    assert ev.route_metrics([POINTS[0]], HOME, n, 1)[1] == pytest.approx(expected)


def test_provider_keeps_directional_distances_and_durations():
    from route_optimizer.traffic_provider import ProviderMatrix
    point = POINTS[0]
    provider = ProviderMatrix({(HOME, HOME): 0, (point, point): 0,
                               (HOME, point): 1, (point, HOME): 2},
                              {(HOME, HOME): 0, (point, point): 0,
                               (HOME, point): 5, (point, HOME): 8}, {})
    n = replace(norms(), provider=provider)
    tn = fl.TruckNorms(480, 0, 0)
    distance, minutes = fl._matrices([point], HOME, n, tn)
    assert distance == [[0, 1], [2, 0]]
    assert minutes.travel(0, 1, 0) == 5
    assert minutes.travel(1, 0, 0) == 8
    _, arrivals, duration = fl.trip_schedule([point], [1000], HOME, n, tn)
    assert arrivals == [5] and duration == 13


def test_directional_reversal_does_not_increase_real_distance():
    distance = [[0, 1, .1, 100], [100, 0, 1, .1],
                [100, 100, 0, 1], [1, 100, 100, 0]]
    stops = [fl._Stop(i, 1, 0, 0) for i in (1, 2, 3)]
    original = [0, 1, 2]
    # Крайние рёбра обещают экономию; обратное внутреннее ребро стоит 100 км.
    result = fl._two_opt(original, stops, distance)
    assert fl._closed(result, stops, distance) <= fl._closed(original, stops, distance)
    assert result == original

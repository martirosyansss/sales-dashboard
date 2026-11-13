"""Какой машине нужен объезд малого центра при открытии навигатора."""
from types import SimpleNamespace as NS

import pytest

from courier import api
from route_optimizer import guidance, roads as rd, route_policy as policy
from test_courier import _fresh_ref_cache, _pin_env, app, client, now, st, term

ZONE = ((40.19, 44.31), (40.21, 44.31), (40.21, 44.33), (40.19, 44.33))
A, MIDDLE, B, RING_1, RING_2 = (40.2, 44.30), (40.2, 44.32), (40.2, 44.34), (40.22, 44.31), (40.22, 44.33)
URL = '/api/courier/v1/navigation/route'


def ring_graph(direct_km):
    """A — центр — B напрямую и A — кольцо — B вокруг зоны (6 км)."""
    points = [A, MIDDLE, B, RING_1, RING_2]
    edges = [(0, 1, direct_km), (1, 2, direct_km), (0, 3, 2.), (3, 4, 2.), (4, 2, 2.)]
    edges += [(b, a, km) for a, b, km in edges]
    return rd.RoadGraph.from_arrays([p[0] for p in points], [p[1] for p in points], [e[0] for e in edges],
                                    [e[1] for e in edges], [e[2] for e in edges], cost=[e[2] for e in edges])


def networks(direct_km=2.):
    graph = ring_graph(direct_km)
    return rd.RoadNetwork(rd.without_zone(graph, ZONE)), rd.RoadNetwork(graph)


def center_roads(direct_km=2.):
    graph = ring_graph(direct_km)
    return rd.CenterBypassRoads(rd.RoadDistances.for_graph(graph),
                                rd.RoadDistances.for_graph(rd.without_zone(graph, ZONE)), ZONE)


def test_one_anchor_on_the_way_round_keeps_the_route_off_the_center():
    assert guidance.around_zone(*networks(), A, B) == [list(RING_1)]


def test_no_anchor_when_the_natural_route_already_avoids_the_center():
    assert guidance.around_zone(*networks(direct_km=5.), A, B) == []


def test_point_away_from_any_road_needs_no_anchor():
    assert guidance.around_zone(*networks(), (41., 45.), B) == []


def test_trip_into_or_out_of_the_center_is_not_bypassed():
    assert policy.center_bypass(center_roads(), MIDDLE, B) == []
    assert policy.center_bypass(center_roads(), A, MIDDLE) == []


def test_bypass_is_built_for_a_trip_past_the_center():
    assert policy.center_bypass(center_roads(), A, B) == [list(RING_1)]


def test_trip_far_from_the_center_does_not_load_the_road_networks():
    roads = center_roads()

    def loaded():
        raise AssertionError('road networks were loaded')
    roads.base._load_network = roads.bypass._load_network = loaded
    assert policy.center_bypass(roads, (40.8, 45.5), (40.9, 45.6)) == []


def test_no_center_model_means_no_anchors():
    assert policy.center_bypass(None, A, B) == []


def test_allowed_truck_is_not_bypassed():
    assert policy.center_bypass(center_roads(), A, B, barred=lambda: False) == []


def test_truck_rule_is_not_asked_for_a_trip_that_cannot_pass_the_center():
    def asked():
        raise AssertionError('truck rule was asked')
    assert policy.center_bypass(center_roads(), (40.8, 45.5), (40.9, 45.6), barred=asked) == []


def test_busy_bypass_gives_up_instead_of_queueing(monkeypatch):
    monkeypatch.setattr(policy, 'CENTER_WAIT_S', 0.01)
    assert policy._center_lock.acquire(timeout=1)
    try:
        assert policy.center_bypass(center_roads(), A, B) == []
    finally:
        policy._center_lock.release()


@pytest.mark.parametrize('name,via', [('FORD', [list(RING_1)]), ('JAC', [])])
def test_apk_route_pins_only_a_truck_barred_from_the_center(client, term, app, monkeypatch, name, via):
    monkeypatch.setattr(api, '_preferred_roads', center_roads)
    monkeypatch.setattr(api, '_car_name', lambda code: name)
    rules = NS(truck_center_ok=lambda car, car_name: car_name == 'JAC')
    monkeypatch.setitem(app.extensions, 'route_optimizer', NS(store=NS(load=lambda: rules)))
    route = client.post(URL, json={'from': list(A), 'to': list(B)}, headers=term['s']).get_json()
    assert route['via'] == via
    assert route['line'] == [list(A), *via, list(B)]
    assert route['guidance'] == 'road_anchors_v3' and route['policy'] == 'main_roads_v1'


def test_failure_of_the_center_rule_still_returns_a_route(client, term, app, monkeypatch):
    def broken():
        raise RuntimeError('store is down')
    monkeypatch.setattr(api, '_preferred_roads', center_roads)
    monkeypatch.setitem(app.extensions, 'route_optimizer', NS(store=NS(load=broken)))
    response = client.post(URL, json={'from': list(A), 'to': list(B)}, headers=term['s'])
    assert response.status_code == 200 and response.get_json()['via'] == []

"""Общая модель всех страниц, API/дня APK и прогноза времени, без production данных."""
from datetime import date
from types import SimpleNamespace as NS

import pytest

from courier import api, day as dy
from courier.routes_link import RoutesView
from route_optimizer import roads as rd, route_policy as policy
from route_optimizer.traffic_provider import ProviderMatrix, on_preferred_roads
from test_courier import _fresh_ref_cache, _pin_env, app, client, now, st, term
from test_route_main_roads import graph, point


@pytest.fixture
def roads():
    return rd.RoadDistances.for_graph(graph())


def test_route_and_navigation_use_main_corridor(roads):
    g = graph()
    a,b = point(g,0),point(g,3)
    result = policy.navigation(roads,a,b)
    assert result['policy'] == policy.POLICY_ID
    assert list(point(g,2)) in result['via']
    assert point(g,1) not in [tuple(p) for p in result['line']]


def test_unknown_road_is_not_a_straight_driving_route(roads):
    assert policy.navigation(roads,(40.8,45.5),(40.9,45.6)) is None
    assert policy.geometry(None,[[(40.,44.),(40.1,44.1)]]) == []


def test_geometry_cache_does_not_mix_different_graphs_with_same_map_name():
    a,b = point(graph(),0),point(graph(),3)
    one = rd.RoadDistances.for_graph(graph())
    two = rd.RoadDistances.for_graph(graph(main_km=20.))
    assert policy.geometry(one,[[a,b]]) != policy.geometry(two,[[a,b]])


@pytest.mark.parametrize('raw', [None, [True,44.5], [40.2,float('nan')], [40.2], [0,0], '40,44'])
def test_invalid_or_outside_points_rejected(raw):
    assert policy.point(raw) is None


def test_apk_route_requires_existing_terminal_and_driver_session(client, term, monkeypatch, roads):
    monkeypatch.setattr(api,'_preferred_roads',lambda:roads)
    g = graph()
    body = {'from':point(g,0),'to':point(g,3)}
    url = '/api/courier/v1/navigation/route'
    assert client.post(url,json=body).status_code == 401
    assert client.post(url,json=body,headers=term['h']).status_code == 401
    response = client.post(url,json=body,headers=term['s'])
    assert response.status_code == 200
    route = response.get_json()
    assert route['via'] == [] and route['line'] == [list(body['from']),list(body['to'])]
    assert route['from'] == list(body['from']) and route['to'] == list(body['to'])
    # The APK refuses a route without exactly these two identifiers.
    assert route['guidance'] == 'road_anchors_v3' and route['policy'] == 'main_roads_v1'
    assert client.post(url,json={'from':[0,0],'to':point(g,3)},headers=term['s']).status_code == 400


def test_apk_lines_and_navigation_without_road_data(client, term, monkeypatch, roads):
    monkeypatch.setattr(api,'_preferred_roads',lambda:roads)
    g=graph()
    response = client.post('/api/courier/v1/navigation/lines',json={'lines':[[point(g,0),point(g,3)]]},headers=term['s'])
    assert response.status_code == 200
    assert response.get_json()['policy'] == policy.POLICY_ID
    assert list(point(g,2)) in response.get_json()['lines'][0]
    for bad in ({'lines':[None]},{'lines':[[[True,44.5]]]},{'lines':[[[40.,44.]]*1501]}):
        assert client.post('/api/courier/v1/navigation/lines',json=bad,headers=term['s']).status_code == 400
    monkeypatch.setattr(api,'_preferred_roads',lambda:None)
    answer = client.post('/api/courier/v1/navigation/route',json={'from':point(g,0),'to':point(g,3)},headers=term['s'])
    assert answer.status_code == 200 and answer.get_json()['via'] == []


def test_apk_day_contains_cached_main_road_geometry(roads):
    g=graph()
    stops=[{'stop_id':'S:1','customer':{'id':1},'lat':point(g,3)[0],'lon':point(g,3)[1], 'weight_kg':50}]
    view=RoutesView(depot=point(g,0),roads=roads,route_roads=roads)
    trip=dy.trips_json(stops,view,'TEST',date(2026,11,4))[0]
    assert trip['routing_policy'] == policy.POLICY_ID and trip['matrix'] is None
    assert len(trip['route_lines']) == 2
    assert point(g,2) in [tuple(p) for p in trip['route_lines'][0]]


def test_provider_pace_cannot_replace_main_road_distance(roads):
    g=graph()
    a,b=point(g,0),point(g,3)
    roads.ensure([a,b])
    raw=ProviderMatrix({(a,b):2.},{(a,b):4.},{'source':'yandex'})
    adapted=on_preferred_roads(raw,NS(km=roads.km,roads=roads))
    assert adapted.km(a,b) == pytest.approx(6.)
    assert adapted.minutes(a,b) == pytest.approx(12.)
    assert adapted.report['time_method'] == 'provider_pace_on_main_roads'
    assert raw.km(a,b) == 2.

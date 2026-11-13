"""Приоритет основных дорог: выбор пути, честные км, подъезд, рельеф и кэш."""
from dataclasses import replace

import numpy as np
import pytest

from route_optimizer import roads as rd, terrain
from route_optimizer import valhalla_engine as ve


def graph(main_km=3.0):
    # 0–1–3: короткая жилая срезка, 0–2–3: основная дорога, 3–4: подъезд к магазину.
    edges = [(0, 1, 1., 4.), (1, 3, 1., 4.), (0, 2, main_km, main_km),
             (2, 3, main_km, main_km), (3, 4, .2, 1.6)]
    edges += [(b, a, km * 1.1, cost * 1.1) for a, b, km, cost in edges.copy()]
    return rd.RoadGraph.from_arrays([40., 40., 40.01, 40.01, 40.02],
                                    [44., 44.01, 44., 44.01, 44.01],
                                    [x[0] for x in edges], [x[1] for x in edges],
                                    [x[2] for x in edges], cost=[x[3] for x in edges])


def point(g, node):
    return float(g.lat[node]), float(g.lon[node])


def test_main_road_selected_and_km_are_physical_in_both_directions():
    g = graph()
    net = rd.RoadNetwork(g)
    assert net.distances([0], [3, 4])[0] == pytest.approx([6., 6.2])
    assert net.distances([0], [3, 4], reverse=True)[0] == pytest.approx([6.6, 6.82])
    paths = net.paths([(point(g, 0), point(g, 3))])
    lat, lon = paths[point(g, 0), point(g, 3)]
    assert list(zip(lat, lon)) == [point(g, i) for i in (0, 2, 3)]


def test_local_access_to_shop_is_retained():
    g = graph()
    net = rd.RoadNetwork(g)
    paths = net.paths([(point(g, 0), point(g, 4))])
    lat, lon = paths[point(g, 0), point(g, 4)]
    assert list(zip(lat, lon)) == [point(g, i) for i in (0, 2, 3, 4)]
    assert net.distances([3], [4])[0, 0] == pytest.approx(.2)


def test_preference_does_not_force_disproportionate_detour():
    assert rd.RoadNetwork(graph(main_km=20.)).distances([0], [3])[0, 0] == pytest.approx(2.)


def test_only_local_road_still_has_a_route():
    g = rd.RoadGraph.from_ways({0: (40., 44.), 1: (40., 44.01)}, [([0, 1], 0, 12.)])
    assert rd.RoadNetwork(g).distances([0], [1])[0, 0] == pytest.approx(g.km[0])


def test_oneway_is_preserved_in_weighted_network():
    g = rd.RoadGraph.from_arrays([40., 40., 40.01], [44., 44.01, 44.01],
                                 [0, 1, 2], [1, 2, 0], [1., 2., 3.], cost=[4., 2., 3.])
    net = rd.RoadNetwork(g)
    assert net.distances([0], [1])[0, 0] == pytest.approx(1.)
    assert net.distances([0], [1], reverse=True)[0, 0] == pytest.approx(5.)


def test_climb_uses_main_road_and_physical_edge_lengths():
    g = graph()
    net = rd.RoadNetwork(g)
    z = np.array([0., 100., 20., 30., 40.])
    forward = terrain.edge_climb(z[0], z[2], 3.) + terrain.edge_climb(z[2], z[3], 3.)
    backward = terrain.edge_climb(z[3], z[2], 3.3) + terrain.edge_climb(z[2], z[0], 3.3)
    assert net.climbs([0], [3], z)[0, 0] == pytest.approx(forward)
    assert net.climbs([0], [3], z, reverse=True)[0, 0] == pytest.approx(backward)


def test_parallel_edges_keep_preferred_edge_physical_length():
    g = rd.RoadGraph.from_arrays([40., 40.01], [44., 44.01], [0, 0, 1], [1, 1, 0],
                                 [1., 3., 3.], cost=[8., 3., 3.])
    assert rd.RoadNetwork(g).distances([0], [1])[0, 0] == pytest.approx(3.)


def test_graph_roundtrip_and_preference_change_invalidates_distance_cache(tmp_path):
    g = graph()
    graph_path, cache = tmp_path/'graph.npz', tmp_path/'dist.npz'
    g.save(str(graph_path))
    loaded = rd.RoadGraph.load(str(graph_path), '')
    assert loaded.identity == g.identity
    assert np.array_equal(loaded.route_cost, g.route_cost)
    points = [point(g, i) for i in (0, 3)]
    roads = rd.RoadDistances.for_graph(g, cache_path=str(cache))
    roads.ensure(points)
    assert roads.km(*points) == pytest.approx(6.)
    shortest = replace(g, cost=g.km.copy())
    assert shortest.identity != g.identity
    restored = rd.RoadDistances.for_graph(shortest, cache_path=str(cache))
    restored.ensure(points)
    assert restored.km(*points) == pytest.approx(2.)


def test_center_bypass_preserves_preference_costs():
    g = graph()
    zone = [(40.03, 44.03), (40.04, 44.03), (40.04, 44.04), (40.03, 44.04)]
    bypass = rd.without_zone(g, zone)
    assert np.array_equal(bypass.route_cost, g.route_cost)
    assert rd.RoadNetwork(bypass).distances([0], [3])[0, 0] == pytest.approx(6.)


@pytest.mark.parametrize('tags', [
    {'highway': 'residential'}, {'highway': 'living_street'}, {'highway': 'service', 'service': 'alley'},
    {'highway': 'unclassified'}, {'highway': 'road'}, {'highway': 'primary', 'surface': 'gravel'},
])
def test_minor_or_unpaved_roads_have_higher_cost_without_being_removed(tags):
    assert rd.way_factor(tags) > rd.way_factor({'highway': 'primary'})
    assert rd.way_direction(tags) is not None


def test_private_and_closed_roads_remain_forbidden():
    for key in ('access', 'motor_vehicle', 'motorcar'):
        for value in ('private', 'no'):
            assert rd.way_direction({'highway': 'primary', key: value}) is None


def test_valhalla_policy_changes_cache_keys_and_retains_vehicle_limits():
    cost = ve.truck_costing(3000)
    assert cost['low_class_factor'] == 4.
    assert cost['low_class_penalty'] == 120.
    assert cost['use_highways'] == 1.
    assert cost['use_living_streets'] == cost['use_tracks'] == 0.
    assert cost['weight'] == 6.
    assert all(cost[k] == v for k, v in ve.TRUCK_DIMS_M.items())
    old = {'weight': cost['weight'], **ve.TRUCK_DIMS_M}
    assert ve.costing_key(old) != ve.costing_key(cost)

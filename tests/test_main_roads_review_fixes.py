"""Regressions for the three concrete post-release routing review findings."""
from types import SimpleNamespace as NS
import numpy as np
from scipy.sparse.csgraph import dijkstra
from route_optimizer import roads as rd, route_policy as policy
from route_optimizer.guidance import navigation
from test_route_road_lines import client, _no_road_map, _roads, RN


def parallel():
    points={0:(40.2,44.3),1:(40.20018,44.3),2:(40.20018,44.31),3:(40.2,44.31)}
    graph=rd.RoadGraph.from_ways(points,[([0,1,2,3],0,1.),([0,3],0,4.)])
    return points,rd.RoadNetwork(graph)


def test_twenty_metre_parallel_main_road_keeps_graph_verified_anchors():
    points,net=parallel()
    result=navigation(net,points[0],points[3])
    assert result is not None and result['guidance']=='road_anchors_v3'
    assert result['via'] and all(tuple(p) in result['line'] for p in result['via'])
    pins=[result['from'],*result['via'],result['to']]
    nodes,_=net.snap(pins)
    for a,b in zip(nodes,nodes[1:]):
        _,pred=dijkstra(net._lengths,directed=True,indices=int(a),return_predecessors=True)
        selected=rd._walk_back(pred,int(a),int(b))
        physical_cost=float(np.asarray(net._g[selected[:-1],selected[1:]]).sum())
        preferred=dijkstra(net._g,directed=True,indices=int(a))[int(b)]
        assert physical_cost<=preferred+1e-6


def test_route_is_kept_when_required_anchors_do_not_fit_the_limit():
    points,net=parallel()
    result=navigation(net,points[0],points[3],max_via=0)
    assert result is not None and result['via']==[]


def test_straight_main_road_without_a_competing_shortcut_needs_no_anchor():
    points={0:(40.2,44.3),1:(40.2,44.31)}
    net=rd.RoadNetwork(rd.RoadGraph.from_ways(points,[([0,1],0,1.)]))
    assert navigation(net,points[0],points[1])['via']==[]


def test_bypass_anchors_are_checked_against_the_full_physical_graph():
    points={0:(40.2,44.3),1:(40.2,44.31),2:(40.21,44.305),3:(40.2,44.32)}
    graph=rd.RoadGraph.from_ways(points,[([0,1,3],0,1.),([0,2,3],0,1.)])
    zone=((40.199,44.309),(40.201,44.309),(40.201,44.311),(40.199,44.311))
    bypass=rd.RoadNetwork(rd.without_zone(graph,zone))
    result=navigation(bypass,points[0],points[3],comparison=rd.RoadNetwork(graph))
    assert result is not None and result['via']==[list(points[2])]


def test_missing_web_road_never_becomes_a_successful_straight_segment(client):
    client.application.extensions['route_optimizer'].roads=NS(get=_roads)
    response=client.post('/api/routes/road-lines',json={'lines':[[RN[0],(40.8,45.5)]]}).get_json()
    assert response['lines']==[[]] and response['segments']==[[]]
    assert response['line_status']==['unavailable']


def test_known_parts_around_a_missing_leg_remain_separate():
    roads=_roads()
    # A known leg, an unknown detached island, then another known leg.
    result=policy.map_geometry(roads,[[RN[0],RN[1],RN[3],RN[1],RN[2]]])
    assert result['lines']==[[]] and result['line_status']==['partial']
    assert len(result['segments'][0])==2
    assert tuple(result['segments'][0][0][-1])==RN[1]
    assert tuple(result['segments'][0][1][0])==RN[1]


def test_no_map_preserves_input_alignment_without_any_fake_path():
    result=policy.map_geometry(None,[[RN[0],RN[1]],[RN[1],RN[2]]])
    assert result['segments']==[[],[]] and result['lines']==[[],[]]
    assert result['line_status']==['unavailable','unavailable']

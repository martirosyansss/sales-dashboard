"""Keep the preferred road even when a longer side road is attractive to a time-based receiver."""
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from route_optimizer import roads as rd
from route_optimizer.guidance import navigation


def alternative_graph():
    edges=[(0,1,1.,1.),(1,3,1.,1.),(0,2,1.2,4.8),(2,3,1.2,4.8)]
    edges += [(b,a,km,cost) for a,b,km,cost in edges.copy()]
    return rd.RoadGraph.from_arrays([40.2,40.2,40.205,40.2],[44.3,44.31,44.31,44.32],
        [e[0] for e in edges],[e[1] for e in edges],[e[2] for e in edges],cost=[e[3] for e in edges])


def test_longer_faster_side_detour_requires_a_main_road_anchor():
    graph=alternative_graph();net=rd.RoadNetwork(graph)
    result=navigation(net,(40.2,44.3),(40.2,44.32))
    assert result is not None and result['guidance']=='road_anchors_v3'
    assert result['via']==[[40.2,44.31]]
    stress=csr_matrix((graph.km/np.clip(graph.route_cost/graph.km,1.,2.),(graph.src,graph.dst)),shape=net._g.shape)
    nodes,_=net.snap([result['from'],*result['via'],result['to']])
    for a,b in zip(nodes,nodes[1:]):
        _,pred=dijkstra(stress,directed=True,indices=int(a),return_predecessors=True)
        path=rd._walk_back(pred,int(a),int(b))
        cost=float(np.asarray(net._g[path[:-1],path[1:]]).sum())
        assert cost<=dijkstra(net._g,directed=True,indices=int(a))[int(b)]+1e-6


def test_route_is_still_returned_when_the_required_anchor_cannot_fit():
    net=rd.RoadNetwork(alternative_graph())
    result=navigation(net,(40.2,44.3),(40.2,44.32),max_via=0)
    assert result is not None and result['via']==[]


def diamond_chain(count):
    """Main road J-M-J-M-… with one side road per diamond that only the stress model prefers."""
    points,edges={},[]
    for i in range(count):
        j,m,s,after=3*i,3*i+1,3*i+2,3*i+3
        points.update({j:(40.2,44.3+i*.02),m:(40.2,44.31+i*.02),s:(40.205,44.31+i*.02),after:(40.2,44.32+i*.02)})
        edges+=[(j,m,1.,1.),(m,after,1.,1.),(j,s,1.2,4.8),(s,after,1.2,4.8)]
    edges+=[(b,a,km,cost) for a,b,km,cost in edges.copy()]
    return points,rd.RoadGraph.from_arrays([points[i][0] for i in sorted(points)],[points[i][1] for i in sorted(points)],
        [e[0] for e in edges],[e[1] for e in edges],[e[2] for e in edges],cost=[e[3] for e in edges])


def test_anchors_stay_spread_along_a_route_that_needs_more_than_the_limit():
    points,graph=diamond_chain(8)
    start,end=points[0],points[24]
    result=navigation(rd.RoadNetwork(graph),start,end,max_via=3)
    assert result is not None and len(result['via'])==3
    lons=[p[1] for p in result['via']]
    assert lons==sorted(lons) and lons[0]<(start[1]+end[1])/2<lons[-1]
    # The terminal rejects a route unless every anchor is a vertex of the line, in order.
    spots=[result['line'].index(tuple(p)) for p in result['via']]
    assert spots==sorted(spots)


def test_dead_end_driveway_does_not_add_an_unnecessary_anchor():
    points={0:(40.2,44.3),1:(40.2,44.31),2:(40.2,44.32),3:(40.205,44.31)}
    graph=rd.RoadGraph.from_ways(points,[([0,1,2],0,1.),([1,3],0,12.)])
    result=navigation(rd.RoadNetwork(graph),points[0],points[2])
    assert result is not None and result['via']==[]


def test_longer_shortcut_between_the_same_junctions_gets_an_interior_road_anchor():
    points={0:(40.2,44.3),1:(40.2,44.31),2:(40.205,44.305),3:(40.2,44.32)}
    edges=[(0,1,1.,1.),(1,3,1.,1.),(0,2,.6,2.4),(2,1,.6,2.4)]
    edges += [(b,a,km,cost) for a,b,km,cost in edges.copy()]
    graph=rd.RoadGraph.from_arrays([points[i][0] for i in range(4)],[points[i][1] for i in range(4)],
        [e[0] for e in edges],[e[1] for e in edges],[e[2] for e in edges],cost=[e[3] for e in edges])
    result=navigation(rd.RoadNetwork(graph),points[0],points[3])
    assert result is not None and [40.2,44.305] in result['via']
    assert all(tuple(p) in result['line'] for p in result['via'])


def test_interior_anchor_does_not_create_a_reverse_edge_on_a_oneway():
    from route_optimizer.guidance import _split_path_edges
    graph=rd.RoadGraph.from_ways({0:(40.2,44.3),1:(40.2,44.31),2:(40.21,44.31)},
                                [([0,1,2,0],1,1.)])
    net=rd.RoadNetwork(graph)
    split=_split_path_edges(net._g,np.array([0,1]),np.array([3]))
    assert split[0,3]>0 and split[3,1]>0
    assert split[3,0]==0 and split[1,3]==0

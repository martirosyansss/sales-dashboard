"""Navigation anchors verified against the road graph, independent of drawing simplification."""
from __future__ import annotations

import heapq
import math

from .roads import _simplify, _walk_back, csr_matrix, dijkstra, np

GUIDANCE_ID = 'road_anchors_v3'


def _split_path_edges(matrix, path, virtual):
    """Add interior road points without changing original edges or their directions."""
    a,b=path[:-1],path[1:]
    forward=np.asarray(matrix[a,b]).ravel()/2
    reverse=np.asarray(matrix[b,a]).ravel()/2
    rows=np.r_[a,virtual,b,virtual]
    cols=np.r_[virtual,b,virtual,a]
    values=np.r_[forward,forward,reverse,reverse]
    keep=values>0
    original=matrix.tocoo()
    size=matrix.shape[0]+len(virtual)
    return csr_matrix((np.r_[original.data,values[keep]],
                       (np.r_[original.row,rows[keep]],np.r_[original.col,cols[keep]])),shape=(size,size))


def navigation(net, origin, destination, max_via=18, comparison=None):
    nodes, _ = net.snap([origin, destination])
    start, end = map(int, nodes)
    if start < 0 or end < 0:
        return None
    if start == end:
        return {'from': list(origin), 'to': list(destination), 'via': [],
                'line': [origin, destination], 'guidance': GUIDANCE_ID}
    distances, pred = dijkstra(net._g, directed=True, indices=start, return_predecessors=True)
    if not math.isfinite(distances[end]):
        return None
    original_path = _walk_back(pred, start, end)
    other = comparison or net
    # This second model deliberately rewards roads penalized by the main-road policy.
    # It finds longer side-road detours that a time-based navigator could prefer. These
    # are stress weights, not claims about actual speed or current traffic.
    factor = np.clip(other.graph.route_cost/other.graph.km,1.,2.)
    local_bias = csr_matrix((other.graph.km/factor,(other.graph.src,other.graph.dst)),shape=other._g.shape)
    virtual=np.arange(net._g.shape[0],net._g.shape[0]+len(original_path)-1,dtype=np.int64)
    preferred=_split_path_edges(net._g,original_path,virtual)
    physical=_split_path_edges(other._lengths,original_path,virtual)
    local_bias=_split_path_edges(local_bias,original_path,virtual)
    lat=np.r_[net.graph.lat,(net.graph.lat[original_path[:-1]]+net.graph.lat[original_path[1:]])/2]
    lon=np.r_[net.graph.lon,(net.graph.lon[original_path[:-1]]+net.graph.lon[original_path[1:]])/2]
    path=np.empty(len(original_path)*2-1,dtype=np.int64)
    path[::2]=original_path;path[1::2]=virtual
    costs=np.asarray(preferred[path[:-1],path[1:]]).ravel()
    lengths=np.asarray(physical[path[:-1],path[1:]]).ravel()
    total_cost=np.r_[0.,np.cumsum(costs,dtype=np.float64)]
    total_length=np.r_[0.,np.cumsum(lengths,dtype=np.float64)]
    stress = np.asarray(local_bias[path[:-1],path[1:]]).ravel()
    total_stress = np.r_[0.,np.cumsum(stress,dtype=np.float64)]
    anchors = {0, len(path)-1}
    # Longest stretch first: a route that needs more anchors than the external app accepts
    # keeps the ones it gets spread along its whole length instead of piling up at one end.
    pending = [(-float(total_length[-1]), 0, len(path)-1)]
    while pending and len(anchors)-2 < max_via:
        _, left, right = heapq.heappop(pending)
        if right-left < 2:
            continue
        wanted = float(total_cost[right]-total_cost[left])
        conflict = None
        for matrix, total in ((physical,total_length),(local_bias,total_stress)):
            limit = float(total[right]-total[left])+1e-5
            d, p = dijkstra(matrix,directed=True,indices=int(path[left]),return_predecessors=True,limit=limit)
            if not math.isfinite(d[int(path[right])]):
                return None
            short = _walk_back(p,int(path[left]),int(path[right]))
            edge_costs = np.asarray(preferred[short[:-1],short[1:]]).ravel()
            short_cost = float(edge_costs.sum()) if np.all(edge_costs>0) else math.inf
            if short_cost>wanted+max(1e-7,wanted*1e-6):
                conflict = short
                break
        if conflict is None:
            continue
        on_short = set(map(int,conflict))
        choices = [i for i in range(left+1, right) if int(path[i]) not in on_short]
        if not choices:
            continue
        midpoint = (total_length[left]+total_length[right])/2
        cut = min(choices, key=lambda i: abs(total_length[i]-midpoint))
        anchors.add(cut)
        for a, b in ((left,cut),(cut,right)):
            heapq.heappush(pending, (-float(total_length[b]-total_length[a]), a, b))
    indices = sorted(anchors)
    via = [(float(lat[path[i]]),float(lon[path[i]])) for i in indices[1:-1]]
    # Drawing may be simplified, but every navigation anchor remains an exact vertex.
    line = [origin]
    for left, right in zip(indices,indices[1:]):
        part = path[left:right+1]
        xy = net._project(lat[part],lon[part])
        for i in _simplify(xy,.005):
            point = (float(lat[part[i]]),float(lon[part[i]]))
            if point != line[-1]: line.append(point)
    if line[-1] != destination: line.append(destination)
    if len(line)>20000:
        return None
    return {'from':list(origin),'to':list(destination),'via':[list(p) for p in via],
            'line':line,'guidance':GUIDANCE_ID}


def around_zone(net, full, origin, destination, max_via=18):
    """Fewest anchors that keep a navigator's own route out of a zone.

    net is full without the zone's edges. A stretch needs an anchor only when the best route over
    full, by preferred cost or by distance, would use one of those edges. Returns via points:
    [] when nothing needs pinning or no way around the zone is known.
    """
    nodes, _ = net.snap([origin, destination])
    start, end = map(int, nodes)
    if start < 0 or end < 0 or start == end:
        return []
    distances, pred = dijkstra(net._g, directed=True, indices=start, return_predecessors=True)
    if not math.isfinite(distances[end]):
        return []
    path = _walk_back(pred, start, end)
    lat, lon = net.graph.lat, net.graph.lon
    xy = net._project(lat[path], lon[path])
    models = [(m, np.r_[0., np.cumsum(np.asarray(m[path[:-1],path[1:]]).ravel(), dtype=np.float64)])
              for m in (full._g, full._lengths)]
    km = models[1][1]

    def crossing(left, right):
        source, target = int(path[left]), int(path[right])
        for matrix, total in models:
            limit = float(total[right]-total[left])+1e-5
            d, p = dijkstra(matrix, directed=True, indices=source, return_predecessors=True, limit=limit)
            if math.isfinite(d[target]):
                short = _walk_back(p, source, target)
                removed = np.asarray(net._g[short[:-1],short[1:]]).ravel() <= 0
                if removed.any():
                    return short, short[:-1][removed]
        return None

    anchors = {0, len(path)-1}
    pending = [(-float(km[-1]), 0, len(path)-1)]
    while pending and len(anchors)-2 < max_via:
        _, left, right = heapq.heappop(pending)
        if right-left < 2:
            continue
        found = crossing(left, right)
        if found is None:
            continue
        short, removed = found
        on_short = set(map(int,short))
        choices = np.array([i for i in range(left+1, right) if int(path[i]) not in on_short], dtype=np.int64)
        if not len(choices):
            continue
        # The way round passes closest to the crossing it replaces; one anchor there usually settles it.
        gate = net._project(lat[removed], lon[removed]).mean(axis=0)
        cut = int(choices[np.argmin(np.hypot(*(xy[choices]-gate).T))])
        anchors.add(cut)
        for a, b in ((left,cut),(cut,right)):
            heapq.heappush(pending, (-float(km[b]-km[a]), a, b))
    return [[float(lat[path[i]]), float(lon[path[i]])] for i in sorted(anchors)[1:-1]]

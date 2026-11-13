"""Общее правило плановых дорог для страниц, API терминалов и внешней навигации."""
from __future__ import annotations

import logging
import math
from collections import OrderedDict
from threading import Lock

from .geo import haversine_km, is_valid_point
from .guidance import around_zone
from .roads import CenterBypassRoads, roads_version

logger = logging.getLogger(__name__)

POLICY_ID = 'main_roads_v1'
MAX_NAV_VIA = 18  # start + end + 18 via points fit the mobile Maps 20-point limit
_cache = OrderedDict()
_lock = Lock()
_center_lock = Lock()  # the center bypass builds two road networks: one pair in memory at a time
CENTER_WAIT_S = 3  # a queue of trucks must not hold the app past its own time limit


def point(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in raw):
        return None
    return tuple(map(float, raw)) if is_valid_point(*raw) else None


def geometry(roads, lines):
    """Только найденные дорожные участки; неизвестный проезд не объявляется прямой дорогой."""
    if roads is None or getattr(roads, 'failed', False):
        return []
    roads = getattr(roads, 'fallback', roads)
    if roads is None or not hasattr(roads, 'leg_lines'):
        return []
    lines = tuple(tuple(tuple(p) for p in line) for line in lines)
    key = (POLICY_ID, roads, roads_version(roads), lines)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    legs = [(a, b) for line in lines for a, b in zip(line, line[1:])]
    found = roads.leg_lines(legs)
    result = [line for line, known in found if known and len(line) >= 2] if found is not None else []
    with _lock:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > 32:
            _cache.popitem(last=False)
    return result


def navigation(roads, origin, destination):
    roads = getattr(roads, 'fallback', roads)
    if roads is None or getattr(roads,'failed',False) or not hasattr(roads,'navigation'):
        return None
    result = roads.navigation(origin,destination,MAX_NAV_VIA)
    return {**result,'policy':POLICY_ID} if result is not None else None


def _center_on_the_way(zone, a, b):
    """Plain geometry first: the road networks are heavy, and most trips cannot plausibly pass the center."""
    return min(haversine_km(a,z)+haversine_km(z,b) for z in zone) <= 1.5*haversine_km(a,b)+2


def center_bypass(roads, origin, destination, barred=lambda: True):
    """Via points that keep a navigator out of the small center on a trip that need not enter it.

    barred is asked last: it may need ERP for the truck's name, which most trips never reach.
    """
    roads = getattr(roads,'fallback',roads)
    if not isinstance(roads,CenterBypassRoads) or roads.failed or roads.bypass.failed:
        return []
    if not roads.around(origin,destination) or not _center_on_the_way(roads.zone,origin,destination) or not barred():
        return []
    if not _center_lock.acquire(timeout=CENTER_WAIT_S):
        logger.warning('[Routes] Объезд центра пропущен: расчёт занят другим запросом')
        return []
    try:
        return around_zone(roads.bypass._load_network(),roads.base._load_network(),origin,destination,MAX_NAV_VIA)
    finally:
        _center_lock.release()


def map_geometry(roads, lines):
    """Keep input-line alignment and gaps. Legacy lines are populated only for complete routes."""
    roads = getattr(roads,'fallback',roads)
    legs = [(a,b) for line in lines for a,b in zip(line,line[1:])]
    found = roads.leg_lines(legs) if roads is not None and not getattr(roads,'failed',False) else None
    found = iter(found or [([],False)]*len(legs))
    complete, segments, statuses = [], [], []
    for points in lines:
        chunks, count, contiguous = [], 0, False
        for _ in zip(points,points[1:]):
            line, known = next(found)
            if known and len(line)>=2:
                count += 1
                if contiguous and chunks[-1][-1]==line[0]: chunks[-1].extend(line[1:])
                else: chunks.append(list(line))
                contiguous = True
            else: contiguous = False
        ready = len(points)>=2 and count==len(points)-1
        complete.append(chunks[0] if ready and len(chunks)==1 else [])
        segments.append(chunks)
        statuses.append('ready' if ready else ('partial' if count else 'unavailable'))
    return {'lines':complete,'segments':segments,'line_status':statuses,'routing_policy':POLICY_ID}

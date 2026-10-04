# -*- coding: utf-8 -*-
"""«Развоз»: объезд малого центра по дорогам (roads.CenterBypassRoads). Машине без права въезда центр закрыт не только
точками, но и в пути: участок между точками вне центра — по графу без центра, участок с точкой в центре — по обычному.

Синтетическая сетка дорог 7 × 7 (шаг 0,4 км, улицы в обе стороны), центр — квадрат вокруг среднего узла; карты, ERP
и pyvalhalla не нужно (Valhalla — подделка из test_route_valhalla).
Запуск из корня проекта:  python -m pytest tests/test_route_center_bypass.py -q
"""
import random
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip('numpy')
pytest.importorskip('scipy')

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import geo  # noqa: E402
from route_optimizer import roads as rd  # noqa: E402
from route_optimizer import valhalla_engine as ve  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.vehicle_access import VehicleAccess  # noqa: E402
from test_route_optimizer import (DP_DAY, DP_NORMS, TN, _dp_stops, _info, _no_road_map, _trip_of,  # noqa: E402,F401
                                  client, make_snapshot)
from test_route_valhalla import _view, fake  # noqa: E402,F401
from test_learning_loop import S101, S102, _learning_client  # noqa: E402
from test_learning_loop import TODAY as LEARNING_DAY  # noqa: E402
from test_truck_time_select import FakeProvider  # noqa: E402

KM_LAT = geo.haversine_km((40.0, 44.3), (41.0, 44.3))
KM_LON = geo.haversine_km((40.3, 44.3), (40.3, 45.3))
STEP = 0.4   # км между соседними узлами сетки
N = 7


def _at(r, c):
    """Точка сетки: ряд r (на север), столбец c (на восток); дробные — между узлами."""
    return 40.30 + r * STEP / KM_LAT, 44.30 + c * STEP / KM_LON


def _box(r, c, half):
    """Квадрат вокруг точки сетки (r, c) со стороной 2 × half шагов."""
    (la0, lo0), (la1, lo1) = _at(r - half, c - half), _at(r + half, c + half)
    return ((la0, lo0), (la0, lo1), (la1, lo1), (la1, lo0))


NODES = {r * N + c: _at(r, c) for r in range(N) for c in range(N)}
WAYS = ([([r * N + c for c in range(N)], 0) for r in range(N)]
        + [([r * N + c for r in range(N)], 0) for c in range(N)])
GRAPH = rd.RoadGraph.from_ways(NODES, WAYS, source='grid')
ZONE = _box(3, 3, 0.6)                    # в центре — только узел (3, 3)
WEST, EAST, MID, TOP = _at(3, 0), _at(3, 6), _at(3, 3), _at(0, 3)
STEP_KM = geo.haversine_km(_at(3, 0), _at(3, 1))


def _bypass(zone=ZONE, graph=GRAPH):
    base = rd.RoadDistances.for_graph(graph)
    return rd.CenterBypassRoads(base, rd.RoadDistances.around_zone(lambda: graph, zone, 'memory'), zone)


def _map(tmp_path, graph=GRAPH, content=b'grid'):
    """Карта на диске для RoadProvider: файл .osm.pbf (только версия) и готовый кэш графа рядом."""
    pbf = tmp_path / 'grid.osm.pbf'
    pbf.write_bytes(content)
    version = rd.map_signature(str(pbf))
    copy = rd.RoadGraph(graph.lat, graph.lon, graph.src, graph.dst, graph.km, version)   # «собран из этой карты»
    copy.save(rd._cache_path(str(pbf), 'graph'))
    return str(pbf)


def _crosses(line, zone, n=40):
    """Ломаная заходит в зону: точки вдоль каждого её отрезка."""
    return any(geo.in_polygon((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n), zone)
               for a, b in zip(line, line[1:]) for k in range(n + 1))


def _node_index(p):
    return int(np.flatnonzero((GRAPH.lat == p[0]) & (GRAPH.lon == p[1]))[0])


# ============================== граница и граф без центра ==============================

def test_polygon_mask_matches_geo_in_polygon():
    """Маска узлов графа — та же, что у точек развоза (dispatch._central): и на вершинах, и на широте вершин."""
    owner = ((40.188678, 44.508399), (40.188863, 44.517381), (40.189, 44.5245), (40.18, 44.5265),
             (40.1715, 44.5205), (40.1705, 44.51), (40.176, 44.503), (40.185, 44.5015))
    flat = ((40.17, 44.50), (40.17, 44.53), (40.19, 44.53), (40.19, 44.50))   # горизонтальные рёбра
    rng = random.Random(7)
    for poly in (owner, flat, ZONE, owner[:2]):
        lat0, lat1 = min(p[0] for p in poly) - 0.003, max(p[0] for p in poly) + 0.003
        lon0, lon1 = min(p[1] for p in poly) - 0.003, max(p[1] for p in poly) + 0.003
        pts = [(rng.uniform(lat0, lat1), rng.uniform(lon0, lon1)) for _ in range(3000)]
        pts += list(poly) + [(v[0], rng.uniform(lon0, lon1)) for v in poly for _ in range(20)]
        mask = rd._in_polygon_np(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]), poly)
        assert mask.tolist() == [geo.in_polygon(p, poly) for p in pts]


def test_without_zone_drops_edges_touching_zone():
    g = rd.without_zone(GRAPH, ZONE)
    mid = _node_index(MID)
    assert g.n_nodes == GRAPH.n_nodes and g.lat is GRAPH.lat           # узлы и индексы — те же
    assert not ((g.src == mid) | (g.dst == mid)).any()
    assert len(g.src) == len(GRAPH.src) - 8                              # 4 соседа, в обе стороны
    assert g.identity != GRAPH.identity and f'center:{rd.zone_key(ZONE)}' in g.identity
    # середина ребра в зоне, концы — нет: ребро (0, 0) ↔ (0, 1) тоже убирается
    thin = _box(0, 0.5, 0.15)
    a, b = _node_index(_at(0, 0)), _node_index(_at(0, 1))
    cut = rd.without_zone(GRAPH, thin)
    edges = set(zip(GRAPH.src.tolist(), GRAPH.dst.tolist()))
    assert edges - set(zip(cut.src.tolist(), cut.dst.tolist())) == {(a, b), (b, a)}
    assert len(rd.without_zone(GRAPH, ()).src) == len(GRAPH.src)        # зоны нет — граф тот же


def test_without_zone_drops_edges_by_end_in_zone_alone():
    """Маленький центр: у рёбер узла (3, 3) середина вне центра — убирает их одно правило «конец в центре»."""
    small = _box(3, 3, 0.3)
    mid = _node_index(MID)
    touching = (GRAPH.src == mid) | (GRAPH.dst == mid)
    lat = (GRAPH.lat[GRAPH.src] + GRAPH.lat[GRAPH.dst]) / 2
    lon = (GRAPH.lon[GRAPH.src] + GRAPH.lon[GRAPH.dst]) / 2
    assert touching.sum() == 8 and not rd._in_polygon_np(lat[touching], lon[touching], small).any()
    cut = rd.without_zone(GRAPH, small)
    assert len(cut.src) == len(GRAPH.src) - 8 and not ((cut.src == mid) | (cut.dst == mid)).any()


def test_bypass_never_snaps_to_nodes_in_zone():
    inside = rd._in_polygon_np(GRAPH.lat, GRAPH.lon, ZONE)
    rng = random.Random(3)
    pts = [MID] + [_at(3 + rng.uniform(-1, 1), 3 + rng.uniform(-1, 1)) for _ in range(300)]
    nodes, _ = rd.RoadNetwork(rd.without_zone(GRAPH, ZONE)).snap(pts)
    assert (nodes >= 0).all() and not inside[nodes].any()
    plain, _ = rd.RoadNetwork(GRAPH).snap([MID])
    assert inside[plain[0]]                                              # в обычном графе — узел центра


# ============================== км: правило участка ==============================

def test_crossing_leg_goes_around_center():
    r = _bypass()
    r.ensure([WEST, EAST, MID, TOP])
    assert r.base.km(WEST, EAST) == pytest.approx(6 * STEP_KM, rel=1e-3)        # напрямую через центр
    assert r.km(WEST, EAST) == pytest.approx(8 * STEP_KM, rel=1e-3)             # на ряд вверх или вниз и обратно
    assert r.km(EAST, WEST) == pytest.approx(8 * STEP_KM, rel=1e-3)
    assert r.detour(WEST, EAST) == pytest.approx(8 / 6, rel=1e-3)
    corner = _at(0, 0)                                                          # центр не по пути — те же км
    r.ensure([corner])
    assert r.km(WEST, corner) == r.base.km(WEST, corner) and r.detour(WEST, corner) == 1.0


def test_detour_never_below_one_when_bypass_is_shorter():
    """У границы объезд бывает и короче: в обычном графе точка привязана к узлу центра (ближе), в объезде — к узлу вне
    него. Км — объезда (участок между точками вне центра), а растяжение времени Valhalla — не меньше 1."""
    zone = _box(3, 3, 0.3)
    a, b = _at(3, 3.35), EAST
    r = _bypass(zone)
    r.ensure([a, b])
    assert r.around(a, b) and r.km(a, b) < r.base.km(a, b)
    assert r.detour(a, b) == 1.0


def test_one_way_streets_bypass_is_directed():
    """Одностороннее движение: ряды 2 и 4 — только на восток. На восток объезд — по ряду 2 или 4 (8 шагов), на запад —
    по ряду 1 или 5 (10 шагов); напрямую по ряду 3 — 6 шагов в обе стороны."""
    ways = ([([r * N + c for c in range(N)], 1 if r in (2, 4) else 0) for r in range(N)]
            + [([r * N + c for r in range(N)], 0) for c in range(N)])
    r = _bypass(graph=rd.RoadGraph.from_ways(NODES, ways, source='one-way'))
    r.ensure([WEST, EAST])
    assert r.base.km(WEST, EAST) == pytest.approx(6 * STEP_KM, rel=1e-3)
    assert r.base.km(EAST, WEST) == pytest.approx(6 * STEP_KM, rel=1e-3)
    assert r.km(WEST, EAST) == pytest.approx(8 * STEP_KM, rel=1e-3)
    assert r.km(EAST, WEST) == pytest.approx(10 * STEP_KM, rel=1e-3)
    there, back = r.lines([[WEST, EAST], [EAST, WEST]])
    assert not _crosses(there, ZONE) and not _crosses(back, ZONE)
    assert {round(p[0], 4) for p in back} & {round(_at(1, 0)[0], 4), round(_at(5, 0)[0], 4)}   # обратно — ряд 1 или 5


def test_leg_with_point_in_center_uses_plain_roads():
    """Точки в центре возит только машина с правом въезда — её участки к ним и от них по обычному графу."""
    r = _bypass()
    r.ensure([WEST, EAST, MID])
    for a, b in ((WEST, MID), (MID, EAST), (MID, WEST), (MID, MID)):
        assert not r.around(a, b)
        assert r.km(a, b) == r.base.km(a, b) and r.detour(a, b) == 1.0
    assert r.km(WEST, MID) == pytest.approx(3 * STEP_KM, rel=1e-3)


def test_bypass_without_value_falls_back_to_plain_roads():
    """Объезд не посчитался (граф без центра не собрался) — км и линии по обычному графу, как без объезда."""
    r = _bypass()
    r.bypass.failed = True
    r.ensure([WEST, EAST])
    assert r.km(WEST, EAST) == r.base.km(WEST, EAST) and r.detour(WEST, EAST) == 1.0
    lines = r.lines([[WEST, EAST]])
    assert lines == r.base.lines([[WEST, EAST]])


# ============================== линии на карте ==============================

def test_lines_follow_the_same_rule_as_km():
    r = _bypass()
    around, through = r.lines([[WEST, EAST], [WEST, MID, EAST]])
    assert not _crosses(around, ZONE) and len(around) > 2                # в объезд: с поворотами
    assert _crosses(r.base.lines([[WEST, EAST]])[0], ZONE)               # без объезда — прямо через центр
    assert through == r.base.lines([[WEST, MID, EAST]])[0]               # к точке в центре — обычный граф
    edge = [_at(0, 0), _at(0, 6)]                                        # центр не по пути — та же линия
    assert r.lines([edge]) == r.base.lines([edge])
    assert r.lines([[WEST], []]) == [[(round(WEST[0], 5), round(WEST[1], 5))], []]


def test_bypass_leg_at_one_road_node_is_straight_not_plain_path():
    """Обе точки вне центра привязаны объездом к одному узлу: км — по прямой, и линия — по прямой, а не обычным
    графом (там точка у границы привязана к узлу в центре, и путь шёл бы через него)."""
    zone = _box(3, 3, 0.3)
    a, b = _at(3, 3.35), _at(3.3, 4.05)
    r = _bypass(zone)
    r.ensure([a, b])
    assert r.around(a, b) and r.km(a, b) == pytest.approx(geo.haversine_km(a, b))
    lat, lon = rd.RoadNetwork(GRAPH).paths([(a, b)])[rd.point_key(a), rd.point_key(b)]
    assert any(geo.in_polygon(p, zone) for p in zip(lat, lon))          # обычный граф — через узел центра
    assert r.lines([[a, b]]) == [[tuple(round(x, 5) for x in a), tuple(round(x, 5) for x in b)]]


# ============================== кэш, провайдер, расчёт «Развоза» ==============================

def test_provider_bypass_cache_per_zone_next_to_plain_cache(tmp_path, monkeypatch):
    path = _map(tmp_path)
    provider = rd.RoadProvider(path)
    base = provider.get()
    pts = [WEST, EAST, MID, TOP]
    base.ensure(pts)
    plain_file = Path(rd._cache_path(path, 'dist'))
    plain_bytes = plain_file.read_bytes()
    r = provider.bypass(base, ZONE)
    assert provider.bypass(base, [list(p) for p in ZONE]) is r          # та же граница — тот же объезд
    assert provider.bypass(base, ()) is base and provider.bypass(base, ZONE[:2]) is base   # границы нет
    r.ensure(pts)
    assert plain_file.read_bytes() == plain_bytes                        # кэш обычного графа не тронут
    assert Path(rd._cache_path(path, 'dist-center')).exists()
    assert r.bypass.size[0] == 3                                         # точка в центре объезду не нужна
    km = r.km(WEST, EAST)
    calls = []
    real = rd.RoadNetwork.distances
    monkeypatch.setattr(rd.RoadNetwork, 'distances', lambda self, *a, **k: calls.append(1) or real(self, *a, **k))
    again = rd.RoadProvider(path)                                        # «перезапуск сервера»
    r2 = again.bypass(again.get(), ZONE)
    r2.ensure(pts)
    assert not calls and r2.km(WEST, EAST) == km and r2.version == r.version   # из файла, без Dijkstra
    other = _box(3, 2, 0.6)                                              # граница сдвинулась на узел западнее
    r3 = again.bypass(r2.base, other)
    assert r3 is not r2 and r3.version != r2.version
    r3.ensure(pts)
    assert calls and not r3.around(WEST, _at(3, 2)) and r3.around(WEST, MID)
    assert r3.km(WEST, EAST) == pytest.approx(8 * STEP_KM, rel=1e-3)
    calls.clear()
    back = rd.RoadProvider(path)                                         # в файле — уже другая граница: пересчёт
    back.bypass(back.get(), ZONE).ensure(pts)
    assert calls


def test_provider_new_bypass_when_map_or_exact_boundary_changes(tmp_path):
    path = _map(tmp_path)
    provider = rd.RoadProvider(path)
    base = provider.get()
    r = provider.bypass(base, ZONE)
    nudged = (*ZONE[:-1], (ZONE[-1][0], ZONE[-1][1] + 1e-7))            # сдвиг меньше точности отпечатка границы
    assert rd.zone_key(nudged) == rd.zone_key(ZONE)
    assert provider.bypass(base, nudged).zone == tuple(nudged)           # но граница другая — и объезд другой
    _map(tmp_path, content=b'grid, version 2')                           # подложили новую карту
    fresh = provider.get()
    assert fresh is not base
    r2 = provider.bypass(fresh, ZONE)
    assert r2 is not r and r2.base is fresh and r2.version.startswith(fresh.version)


def test_inside_cache_holds_only_ensured_points():
    """Точки запросов линий карты (любые) память объезда не растят — запоминаются только точки ensure."""
    r = _bypass()
    r.ensure([WEST, EAST])
    r.lines([[_at(1 + k / 10, 1), _at(5, 5 - k / 10)] for k in range(20)])
    assert set(r._inside) == {WEST, EAST}


def test_lines_bypass_graph_failure_falls_back_to_plain_lines(caplog):
    """Граф без центра не собрался в запросе линий — линии по обычному графу (как без объезда), а не по прямой."""
    def broken():
        raise RuntimeError('нет графа')
    base = rd.RoadDistances.for_graph(GRAPH)
    r = rd.CenterBypassRoads(base, rd.RoadDistances('memory|center:x', broken), ZONE)
    assert r.lines([[WEST, EAST]]) == base.lines([[WEST, EAST]])
    assert 'линии по обычному графу' in caplog.text


def test_failed_map_does_not_touch_bypass(tmp_path):
    """Карта не загрузилась — объезд (граф из той же карты) не грузится второй раз и второй ошибки в журнале нет."""
    pbf = tmp_path / 'broken.osm.pbf'
    pbf.write_bytes(b'not a pbf')
    provider = rd.RoadProvider(str(pbf))
    base = provider.get()
    r = provider.bypass(base, ZONE)
    r.ensure([WEST, EAST])
    assert base.failed and not r.bypass.failed and r.bypass.size == (0, 0)
    assert r.km(WEST, EAST) is None and r.lines([[WEST, EAST]]) is None


def test_views_roads_bypass_only_for_dispatch_points(tmp_path, monkeypatch):
    """«Развоз» получает объезд (точки развоза и склад), обзор и календарь менеджеров — обычный граф, как раньше."""
    path = _map(tmp_path)
    plan = [_at(0, c) for c in range(N)]
    monkeypatch.setattr(views.evaluate, 'plan_points', lambda *args: plan)
    state = NS(roads=rd.RoadProvider(path), valhalla=None)
    plain = views._roads(state, None, None, [WEST])
    assert type(plain) is rd.RoadDistances
    r = views._roads(state, None, None, [WEST, EAST, MID], center_zone=ZONE)
    assert isinstance(r, rd.CenterBypassRoads) and r.base is plain
    assert r.base.size[0] == N + 3 and r.bypass.size[0] == 2            # объезд — только точки развоза вне центра
    assert views._roads(state, None, None, [WEST], center_zone=()) is plain


def test_road_model_id_is_the_same_with_bypass(tmp_path, fake):
    """Объезд — правило расчёта на тех же дорогах: id модели тот же, выученные поправки времени остаются."""
    path = _map(tmp_path)
    provider = rd.RoadProvider(path)
    base = provider.get()
    r = provider.bypass(base, ZONE)
    assert ve.road_model_id(r) == ve.road_model_id(base)
    mem = _bypass()
    assert ve.road_model_id(mem) == ve.road_model_id(mem.base)
    v_plain = _view(tmp_path / 'a', base, time_only=True, truck_time=True)
    v_around = _view(tmp_path / 'b', r, time_only=True, truck_time=True)
    assert ve.road_model_id(v_around.truck()) == ve.road_model_id(v_plain.truck())


def test_valhalla_km_and_minutes_stretched_on_bypass_legs(tmp_path, fake):
    """Путь Valhalla — кратчайший: на участке в объезд его км и минуты растягиваются во столько же раз, во сколько
    объезд по графу длиннее; скорость участка (км / минуты) — та же."""
    r = _bypass()
    pts = [WEST, EAST, MID]
    r.ensure(pts)
    k = r.detour(WEST, EAST)
    assert k == pytest.approx(8 / 6, rel=1e-3)
    plain, around = _view(tmp_path / 'a', r.base), _view(tmp_path / 'b', r)        # режим valhalla: км и минуты
    for v in (plain, around):
        v.ensure(pts)
    assert around.km(WEST, EAST) == pytest.approx(plain.km(WEST, EAST) * k)
    assert around.valhalla_minutes(WEST, EAST, False) == pytest.approx(plain.valhalla_minutes(WEST, EAST, False) * k)
    for a, b in ((WEST, MID), (MID, EAST)):                                         # точка в центре — как есть
        assert around.km(a, b) == plain.km(a, b)
        assert around.valhalla_minutes(a, b, False) == plain.valhalla_minutes(a, b, False)
    # valhalla_time, минуты грузовиков из Valhalla: км — граф в объезд, минуты Valhalla × объезд
    t_plain = _view(tmp_path / 'c', r.base, time_only=True, truck_time=True).truck()
    t_around = _view(tmp_path / 'd', r, time_only=True, truck_time=True).truck()
    for v in (t_plain, t_around):
        v.ensure(pts)
    assert t_around.km(WEST, EAST) == pytest.approx(8 * STEP_KM, rel=1e-3)
    m_plain, m_around = t_plain.minutes(WEST, EAST, False), t_around.minutes(WEST, EAST, False)
    assert m_around == pytest.approx(m_plain * k)
    assert t_around.km(WEST, EAST) / m_around == pytest.approx(t_plain.km(WEST, EAST) / m_plain)


def test_dispatch_non_center_truck_goes_around_center():
    """Сборка рейсов: HOWO (без права въезда) везёт запад и восток — между ними в объезд центра, и км рейса — км
    объезда; точку в центре везёт JAC. Линия рейса HOWO на карте в центр не заходит."""
    r = _bypass()
    howo = fl.FleetTruck('124AV61', 'HOWO', 10000.0, 30.0)
    jac = fl.FleetTruck('475DD61', 'JAC', 2200.0, 12.0, center_ok=True)
    only_howo = VehicleAccess('allow', (howo.car_code,))
    ctx = dp.DayContext(DP_DAY, TOP, {t.car_code: t for t in (howo, jac)}, replace(DP_NORMS, roads=r), TN, 9 * 60,
                        center_zone=ZONE, vehicle_access={301: only_howo, 302: only_howo})
    stops, _ = _dp_stops([(301, WEST, 300.0), (302, EAST, 300.0), (304, MID, 300.0)])
    draft = dp.build(ctx, stops, None, [howo.car_code, jac.car_code], 'now')
    assert not (draft.no_center or draft.no_room or draft.no_vehicle or draft.no_window)
    point = {s.customer_id: s.point for s in stops}
    trips = {t.truck: t for t in draft.trips}
    assert sorted(trips[howo.car_code].stops) == [301, 302] and trips[jac.car_code].stops == [304]
    line = [TOP, *(point[c] for c in trips[howo.car_code].stops), TOP]
    assert all(r.around(a, b) for a, b in zip(line, line[1:]))
    assert not _crosses(r.lines([line])[0], ZONE) and _crosses(r.base.lines([line])[0], ZONE)
    view = dp.plan_view(ctx, stops, draft, _info)
    km = _trip_of(view, 301)[0]['km']
    assert km == pytest.approx(sum(r.km(a, b) for a, b in zip(line, line[1:])), abs=0.06)
    assert km == pytest.approx(20 * STEP_KM, abs=0.06)                   # 6 + 8 (объезд) + 6 шагов, не 18
    center = [TOP, MID, TOP]
    assert _trip_of(view, 304)[0]['km'] == pytest.approx(sum(r.base.km(a, b) for a, b in zip(center, center[1:])),
                                                         abs=0.06)


def test_api_road_lines_avoid_center(client, tmp_path):
    state = client.application.extensions['route_optimizer']
    r = client.post('/api/routes/settings', json={'settings': {'center_zone': [list(p) for p in ZONE]}})
    assert r.status_code == 200, r.get_json()
    body = {'lines': [[list(WEST), list(EAST)], [list(WEST), list(MID), list(EAST)]]}
    assert client.post('/api/routes/road-lines', json={**body, 'avoid_center': True}).get_json()['lines'] is None
    state.roads = rd.RoadProvider(_map(tmp_path))
    plain = client.post('/api/routes/road-lines', json=body).get_json()['lines']
    around = client.post('/api/routes/road-lines', json={**body, 'avoid_center': True}).get_json()['lines']
    assert _crosses(plain[0], ZONE) and not _crosses(around[0], ZONE)
    assert around[1] == plain[1]                                         # через точку в центре — обычный граф
    assert client.post('/api/routes/road-lines', json={**body, 'avoid_center': False}).get_json()['lines'] == plain
    for bad in ('yes', 1, None):
        r = client.post('/api/routes/road-lines', json={**body, 'avoid_center': bad})
        assert r.status_code == 400 and 'avoid_center' in r.get_json()['errors'], bad


def test_dispatch_yandex_matrix_stretched_on_bypass_legs(client, tmp_path, monkeypatch):
    """Матрица Яндекса (traffic_mode = yandex) — кратчайшие пути: у «Развоза» на участках в объезд центра её км и
    минуты × объезд, как у Valhalla; участки с точкой в центре — как есть."""
    from route_optimizer import traffic_provider as tp
    state = client.application.extensions['route_optimizer']
    state.roads = rd.RoadProvider(_map(tmp_path))
    pts = [TOP, WEST, EAST, MID]
    direct = {(a, b): geo.haversine_km(a, b) * 1.2 for a in pts for b in pts}
    matrix = tp.ProviderMatrix(direct, {pair: km * 2.0 for pair, km in direct.items()}, {'source': 'yandex'})
    monkeypatch.setattr(tp, 'load', lambda points, day, start: (matrix, matrix.report))
    monkeypatch.setattr(views.evaluate, 'plan_points', lambda *args: [])
    bundle = state.store.load()
    bundle = replace(bundle, depot=TOP, settings={**bundle.settings, 'traffic_mode': 'yandex',
                                                  'center_zone': [list(p) for p in ZONE]})
    howo = fl.FleetTruck('124AV61', 'HOWO', 10000.0, 30.0)
    ctx = views._dispatch_ctx(state, make_snapshot(), bundle, DP_DAY, {howo.car_code: howo}, [WEST, EAST, MID],
                              learned=False)
    k = ctx.norms.roads.detour(WEST, EAST)
    assert k == pytest.approx(8 / 6, rel=1e-3)
    assert ctx.norms.km(WEST, EAST) == pytest.approx(direct[WEST, EAST] * k)
    assert ctx.norms.provider.minutes(WEST, EAST) == pytest.approx(direct[WEST, EAST] * 2.0 * k)
    assert ctx.norms.km(WEST, MID) == direct[WEST, MID] and ctx.norms.km(MID, TOP) == direct[MID, TOP]
    assert matrix.distances[WEST, EAST] == direct[WEST, EAST]      # кэш матрицы Яндекса не меняется


def _area_graph(lat0, lon0, rows, cols, step=0.25):
    """Сетка улиц в обе стороны rows × cols с шагом step км от точки (lat0, lon0)."""
    nodes = {r * cols + c: (lat0 + r * step / KM_LAT, lon0 + c * step / KM_LON)
             for r in range(rows) for c in range(cols)}
    ways = ([([r * cols + c for c in range(cols)], 0) for r in range(rows)]
            + [([r * cols + c for r in range(rows)], 0) for c in range(cols)])
    return rd.RoadGraph.from_ways(nodes, ways, source='area')


@pytest.mark.parametrize('center_ok, ready', [(True, True), (False, True), (True, False)])
def test_learning_skips_center_truck_legs_on_bypass(client, fake, monkeypatch, tmp_path, center_ok, ready):
    """Обучение времени в пути: у машины с правом въезда (CAR1) участок 101 → 102, который расчёт объезжает (полоса
    центра между ними), с фактом не сравнивается — она могла ехать напрямую; у машины без права въезда — сравнивается
    (она объезжает, как и расчёт). Остальные участки — у обеих. Так и в участках выбора модели времени (Valhalla готов
    — обе модели сравниваются), и у машины с треком без тоннажа и расхода в настройках (не готова к расчёту)."""
    state = _learning_client(client, monkeypatch, days=10)
    band = [[40.2005, 44.540], [40.2005, 44.575], [40.2035, 44.575], [40.2035, 44.540]]
    car1 = {'car_code': 'CAR1', 'center_ok': center_ok}
    if not ready:
        car1.update(capacity_kg=None, fuel_l_per_100km=None)
    r = client.post('/api/routes/settings', json={'settings': {'center_zone': band}, 'trucks': [car1]})
    assert r.status_code == 200, r.get_json()
    snap, _ = state.snapshots.cached()
    assert ('CAR1' in views._ready_trucks(snap, views._bundle(state), active_only=False)) is ready
    state.roads = rd.RoadProvider(_map(tmp_path, _area_graph(40.185, 44.535, 17, 27)))
    state.valhalla = FakeProvider(tmp_path / 'valhalla')
    seen = {'leg_obs': [], 'truck_time_obs': []}
    for name in seen:
        real = getattr(views.learning, name)
        # leg_obs получает и машину (темп машины, №66) — шпион передаёт её дальше
        monkeypatch.setattr(views.learning, name, lambda day, actual, norms, *more, name=name, real=real:
                            seen[name].append(actual.legs) or real(day, actual, norms, *more))
    views.run_learning(state, LEARNING_DAY)
    crossing = (rd.point_key(S101), rd.point_key(S102))
    for name, days in seen.items():
        legs = {(rd.point_key(g.pa), rd.point_key(g.pb)) for day in days for g in day}
        assert len(legs) >= 4 and (crossing in legs) is not center_ok, name


def test_roads_warm_if_stale_survives_unreadable_routes_db(tmp_path, monkeypatch, caplog):
    """--if-stale (задача обновления) при битой базе маршрутов не падает: карты нет — база и не читается; карта есть —
    в журнал, и проверяется только кэш расстояний (без объезда центра), как до него."""
    db = tmp_path / 'broken.db'
    db.write_bytes(b'not a routes database')
    monkeypatch.setattr(rd, 'REPO_ROOT', str(tmp_path))                 # без .env
    monkeypatch.setenv('ROUTES_DB_PATH', str(db))
    monkeypatch.setenv('ROUTES_OSM_PATH', str(tmp_path / 'none.osm.pbf'))
    warmed = []
    monkeypatch.setattr(rd, '_warm', warmed.append)
    assert rd.main(['warm', '--if-stale']) == 0 and warmed == []         # карты нет
    assert 'не прочитана' not in caplog.text
    path = _map(tmp_path)
    monkeypatch.setenv('ROUTES_OSM_PATH', path)
    assert rd.main(['warm', '--if-stale']) == 0 and warmed == [path]     # кэша расстояний нет — греть
    rd.RoadProvider(path).get().ensure([WEST, EAST])
    assert rd.main(['warm', '--if-stale']) == 0 and warmed == [path]     # кэш расстояний годится, объезд не проверить
    assert 'Граница малого центра не прочитана из базы маршрутов' in caplog.text


def test_roads_warm_precomputes_bypass_for_plan_points(tmp_path, monkeypatch):
    """roads warm (задача обновления): за матрицей точек плана — объезд малого центра для них же (граница — из копии
    базы маршрутов, сама база не меняется); после него --if-stale видит, что оба кэша годятся. ERP — подделка."""
    from route_optimizer import evaluate as ev
    from route_optimizer import store as st

    path = _map(tmp_path)
    db = tmp_path / 'routes.db'
    store = st.Store(str(db))
    changes, errors = st.validate_payload({'settings': {'center_zone': [list(p) for p in ZONE]}}, store.load(),
                                          st.RefData(frozenset(), frozenset(), frozenset()))
    assert errors == {}
    store.save(changes, 'qa')
    data = db.read_bytes()
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(rd, 'REPO_ROOT', str(tmp_path))                 # без .env
    monkeypatch.setenv('ROUTES_OSM_PATH', path)
    monkeypatch.setenv('ROUTES_DB_PATH', str(db))
    monkeypatch.setitem(sys.modules, 'app_v2', NS(db=NS(connection_string='dummy')))
    monkeypatch.setattr('route_optimizer.snapshot.load_snapshot', lambda connection_string: 'snapshot')
    monkeypatch.setattr(ev, 'plan_points', lambda snap, bundle, coords: [TOP, WEST, EAST, MID])
    assert rd.main(['warm', '--if-stale']) == 0
    assert not rd._dist_cache_stale(path, ZONE) and db.read_bytes() == data
    provider = rd.RoadProvider(path)
    r = provider.bypass(provider.get(), ZONE)
    calls = []
    real = rd.RoadNetwork.distances
    monkeypatch.setattr(rd.RoadNetwork, 'distances', lambda self, *a, **k: calls.append(1) or real(self, *a, **k))
    r.ensure([TOP, WEST, EAST, MID])
    assert not calls and r.bypass.size[0] == 3 and r.km(WEST, EAST) == pytest.approx(8 * STEP_KM, rel=1e-3)

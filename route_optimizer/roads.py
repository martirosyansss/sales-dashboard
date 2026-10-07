# -*- coding: utf-8 -*-
"""Расстояния по дорогам OpenStreetMap (этап 5): граф для легковой машины, привязка точек, матрица км.

Без Flask и без БД. Карта — файл .osm.pbf (путь — env ROUTES_OSM_PATH, по умолчанию
<репо>/data/roads/armenia-latest.osm.pbf). Карты нет или нет numpy/scipy — дорог нет, и расчёт
идёт по прямой × извилистость, как раньше (RoadProvider.get() → None).

- граф: highway из CAR_HIGHWAYS без access/motor_vehicle = no/private; oneway=yes/1/true — только
  вперёд, -1 — только назад, кольцо и motorway — одностороннее по умолчанию; вес ребра — haversine
  между соседними узлами. Кэш — <карта>.graph.npz рядом с картой, пересборка при смене файла карты;
- привязка: ближайший узел наибольшей сильно связной компоненты (KD-дерево в локальной
  равнопромежуточной проекции, км); дальше SNAP_MAX_KM от дороги — точка не привязана;
- d(A, B) = привязка_A + дорога A→B + привязка_B (направленно: одностороннее движение — разные км туда и
  обратно); один и тот же узел — haversine;
- кэш расстояний — <карта>.dist.npz: точки (округление до 6 знаков) → узел и км привязки,
  направленная матрица км между узлами. Новые точки дозаполняются: Dijkstra от их узлов по графу
  и по обратному графу. Точка не привязана или пути нет — km() → None: участок считает вызывающий
  (по прямой × извилистость);
- объезд малого центра («Развоз», CenterBypassRoads): участок между точками вне центра — по графу без центра
  (without_zone: без рёбер, у которых конец или середина в центре), остальные — по обычному графу. Кэш объезда —
  <карта>.dist-center.npz (отпечаток графа без центра — карта и граница центра; сменились — пересчёт), точки — только
  точки развоза и склад.
- рельеф (№85, terrain): есть кэш высот узлов <карта>.elev.npz (команда dem) — RoadDistances.climb(a, b): эффективный
  подъём участка A → B, м (terrain.edge_climb по рёбрам того же кратчайшего пути, что и км; привязка — без подъёма).
  Считается только для точек ensure_climb («Развоз»), Dijkstra от их узлов с предшественниками: подъём — суммой по дереву
  кратчайших путей (_tree_sums), а не обходом путей. Кэш — <карта>.climb.npz (<карта>.climb-center.npz у объезда центра):
  узлы графа и матрица подъёмов между ними; ключ — граф, высоты и CLIMB_C. Кэша высот нет — terrain False, climb None.
  У сервера (RoadProvider) новые точки досчитывает фоновый поток (своя блокировка, не блокировка км): запрос не ждёт —
  пока подъёма нет, climb → None, литры рейса без рельефа; команды warm и dem досчитывают точки плана заранее.

Команды:  python -m route_optimizer.roads download | build | warm [--if-stale] | dem [--force]
(warm --if-stale — только если кэш расстояний или подъёмов не годится нынешнему графу и формату: задача обновления
сервера; кэш высот от прежней карты при этом пересобирается из уже скачанных тайлов. dem — скачать недостающие тайлы SRTM
для графа в data/roads/dem, собрать кэш высот узлов и прогреть подъёмы точек плана, как warm)
"""
from __future__ import annotations

import logging
import math
import os
import sys
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, Callable, Collection, Iterable, Mapping, Sequence

from . import terrain
from .geo import EARTH_RADIUS_KM, Point, haversine_km, in_polygon

if TYPE_CHECKING:
    from .store import Bundle

try:   # необязательные зависимости: без них дорог нет, всё считается по прямой
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components, dijkstra
    from scipy.spatial import cKDTree
except ImportError:   # pragma: no cover — на сервере без numpy/scipy
    np = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OSM_PATH = os.path.join(REPO_ROOT, 'data', 'roads', 'armenia-latest.osm.pbf')
# Geofabrik через прокси даёт бесконечный редирект — зеркало openstreetmap.fr
DOWNLOAD_URL = 'https://download.openstreetmap.fr/extracts/asia/armenia-latest.osm.pbf'

CAR_HIGHWAYS = frozenset({
    'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential',
    'living_street', 'service', 'road',
    'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link',
})
NO_ACCESS = frozenset({'no', 'private'})
ONEWAY_YES = frozenset({'yes', '1', 'true'})
ROUND_JUNCTIONS = frozenset({'roundabout', 'circular'})

SNAP_MAX_KM = 0.5          # дальше от дороги — точка не привязана (участок по прямой)
KEY_DECIMALS = 6           # ключ точки в кэше расстояний (~0,1 м)
DIJKSTRA_BATCH = 16        # источников за вызов: строка — по всем узлам графа (~6 МБ на источник)
MIN_EDGE_KM = 1e-9         # нулевые рёбра (узлы с одной координатой) — чуть больше нуля
PATH_SIMPLIFY_KM = 0.005   # линия на карте: отклонение от дороги не больше 5 м
PATH_LIMIT_FACTOR = 3.0    # поиск пути для карты — в пределах 3 × по прямой + 2 км (не нашёлся — без предела)
PATH_LIMIT_SLACK_KM = 2.0
CLIMB_CHUNK = 32           # подъёмы досчитываются кусками по столько узлов: кэш пишется после каждого (warm под пределом)
# фоновый поток подъёмов уступает процессор запросам: после каждого шага спит CLIMB_PAUSE × его время (Dijkstra — по
# CLIMB_BATCH источников). Замер 07.10 (сборка дня «Развоза», ~150 точек): без паузы во время фонового расчёта — 23 с
# вместо 11, с паузой 3 — 14 с; фон при этом дольше: 95 с вместо 34 (один раз на новые точки)
CLIMB_PAUSE = 3.0
CLIMB_BATCH = 4
GRAPH_FORMAT = 2
DIST_FORMAT = 3            # 3 — направленная матрица (было: среднее туда и обратно)
RULES_VERSION = 2          # правила way_direction: поменялись — граф и кэш расстояний пересобираются

Way = tuple[Sequence[int], int]   # (id узлов по ходу линии, направление: 1 вперёд, -1 назад, 0 оба)


def roads_supported() -> bool:
    """Есть numpy и scipy (osmium нужен только для сборки графа из карты)."""
    return np is not None


def osm_path() -> str:
    return os.environ.get('ROUTES_OSM_PATH') or DEFAULT_OSM_PATH


def map_signature(path: str) -> str | None:
    """Версия файла карты (время изменения и размер); файла нет — None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f'{st.st_mtime_ns}-{st.st_size}'


def _cache_path(path: str, kind: str) -> str:
    """<карта без .osm.pbf>.<kind>.npz рядом с картой."""
    base = path[:-len('.osm.pbf')] if path.endswith('.osm.pbf') else os.path.splitext(path)[0]
    return f'{base}.{kind}.npz'


def _save_npz(path: str, **arrays: Any) -> None:
    """Атомарная запись .npz: свой временный файл в той же папке, затем os.replace — сервер и
    команда warm не портят файлы друг друга. Ошибка — OSError, временный файл удаляется."""
    fd, tmp = tempfile.mkstemp(suffix='.npz', prefix=os.path.basename(path) + '.',
                               dir=os.path.dirname(path) or '.')
    try:
        with os.fdopen(fd, 'wb') as f:
            np.savez(f, **arrays)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _dist_key(graph_identity: str) -> str:
    """Версия кэша расстояний: строки матрицы — индексы узлов графа, поэтому кэш годится только
    для того же графа и тех же правил привязки."""
    return f'{graph_identity}|snap{SNAP_MAX_KM:g}|key{KEY_DECIMALS}|d{DIST_FORMAT}'


def way_direction(tags: Mapping[str, str]) -> int | None:
    """Направление линии для легковой машины: 1 — только вперёд, -1 — только назад, 0 — в обе
    стороны; None — не дорога для машины (не тот highway или проезд запрещён)."""
    highway = tags.get('highway')
    if highway not in CAR_HIGHWAYS:
        return None
    if any(tags.get(k) in NO_ACCESS for k in ('access', 'motor_vehicle', 'motorcar')):
        return None
    oneway = tags.get('oneway')
    if oneway in ONEWAY_YES:
        return 1
    if oneway == '-1':
        return -1
    if oneway is None and (tags.get('junction') in ROUND_JUNCTIONS or highway == 'motorway'):
        return 1
    return 0


def _haversine_np(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> Any:
    la1, lo1, la2, lo2 = (np.radians(x) for x in (lat1, lon1, lat2, lon2))
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.minimum(1.0, h)))


def _in_polygon_np(lat: Any, lon: Any, polygon: Sequence[Point]) -> Any:
    """geo.in_polygon для массивов точек — то же правило чётности и та же арифметика (граница — так же, как у точек
    развоза в dispatch._central). Меньше трёх вершин — все False."""
    inside = np.zeros(len(lat), dtype=bool)
    if len(polygon) < 3:
        return inside
    prev_lat, prev_lon = polygon[-1]
    for cur_lat, cur_lon in polygon:
        if cur_lat != prev_lat:   # горизонтальное ребро луч не пересекает (в geo.in_polygon — то же без деления)
            inside ^= (((cur_lat > lat) != (prev_lat > lat))
                       & (lon < (prev_lon - cur_lon) * (lat - cur_lat) / (prev_lat - cur_lat) + cur_lon))
        prev_lat, prev_lon = cur_lat, cur_lon
    return inside


def zone_key(zone: Sequence[Point]) -> str:
    """Отпечаток границы малого центра (вершины с точностью ключа точки) — в версии и отпечатке графа объезда."""
    raw = ';'.join(f'{lat:.{KEY_DECIMALS}f},{lon:.{KEY_DECIMALS}f}' for lat, lon in zone)
    return f'{zlib.crc32(raw.encode()):08x}'


# --- Граф ---

@dataclass(frozen=True)
class RoadGraph:
    """Направленный граф дорог: узлы (широта, долгота) и рёбра src → dst с весом km.
    Параллельных рёбер и петель нет (from_arrays оставляет кратчайшее)."""
    lat: Any    # np.ndarray float64 [узлы]
    lon: Any
    src: Any    # np.ndarray int32 [рёбра]
    dst: Any
    km: Any     # np.ndarray float64 [рёбра]
    source: str = field(default='', compare=False)   # версия карты, из которой собран

    @cached_property
    def identity(self) -> str:
        """Отпечаток графа: версия карты, формат и правила, размеры и crc32 массивов."""
        crc = 0
        for a in (self.lat, self.lon, self.src, self.dst, self.km):
            crc = zlib.crc32(np.ascontiguousarray(a).tobytes(), crc)
        return (f'{self.source}|g{GRAPH_FORMAT}|r{RULES_VERSION}|n{len(self.lat)}|e{len(self.src)}'
                f'|{crc:08x}')

    @classmethod
    def from_arrays(cls, lat: Any, lon: Any, src: Any, dst: Any, km: Any | None = None,
                    source: str = '') -> RoadGraph:
        """Граф из массивов; km не задан — haversine между концами ребра."""
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        src = np.asarray(src, dtype=np.int64)
        dst = np.asarray(dst, dtype=np.int64)
        km = (_haversine_np(lat[src], lon[src], lat[dst], lon[dst]) if km is None
              else np.asarray(km, dtype=np.float64))
        keep = src != dst
        src, dst, km = src[keep], dst[keep], np.maximum(km[keep], MIN_EDGE_KM)
        # csr_matrix складывает повторные рёбра — оставляем одно, кратчайшее
        order = np.lexsort((km, dst, src))
        src, dst, km = src[order], dst[order], km[order]
        first = np.ones(len(src), dtype=bool)
        first[1:] = (src[1:] != src[:-1]) | (dst[1:] != dst[:-1])
        return cls(lat, lon, src[first].astype(np.int32), dst[first].astype(np.int32), km[first],
                   source)

    @classmethod
    def from_ways(cls, nodes: Mapping[int, Point], ways: Iterable[Way],
                  source: str = '') -> RoadGraph:
        """Граф из линий OSM: nodes — id → (широта, долгота); узлы без координат пропускаются."""
        index: dict[int, int] = {}
        lat: list[float] = []
        lon: list[float] = []
        src: list[int] = []
        dst: list[int] = []

        def idx(ref: int) -> int:
            i = index.get(ref)
            if i is None:
                i = index[ref] = len(lat)
                lat.append(nodes[ref][0])
                lon.append(nodes[ref][1])
            return i

        for refs, direction in ways:
            chain = [idx(r) for r in refs if r in nodes]
            if direction == -1:
                chain.reverse()
            for a, b in zip(chain, chain[1:]):
                src.append(a)
                dst.append(b)
                if direction == 0:
                    src.append(b)
                    dst.append(a)
        return cls.from_arrays(lat, lon, src, dst, source=source)

    @property
    def n_nodes(self) -> int:
        return len(self.lat)

    def save(self, path: str) -> None:
        _save_npz(path, format=GRAPH_FORMAT, rules=RULES_VERSION, source=self.source,
                  identity=self.identity, lat=self.lat, lon=self.lon, src=self.src, dst=self.dst,
                  km=self.km)

    @staticmethod
    def _valid(z: Any, source: str) -> bool:
        if not {'format', 'rules', 'source', 'identity'} <= set(z.files):   # прежний формат
            return False
        return (int(z['format']) == GRAPH_FORMAT and int(z['rules']) == RULES_VERSION
                and str(z['source']) == source)

    @classmethod
    def load(cls, path: str, source: str) -> RoadGraph | None:
        """Граф из кэша, если он собран из этой версии карты по текущим правилам; файла нет или он
        битый (обрыв записи, чужой формат) — None: граф пересобирается из карты. Файл открываем
        сами: np.load при битом zip не закрывает его, и на Windows не прошла бы замена файла."""
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'rb') as f, np.load(f, allow_pickle=False) as z:
                if not cls._valid(z, source):
                    return None
                return cls(z['lat'], z['lon'], z['src'], z['dst'], z['km'], source)
        except Exception:   # zipfile.BadZipFile, EOFError, KeyError, ValueError, OSError…
            logger.warning('[Routes] Кэш графа %s не прочитан — граф пересобирается из карты',
                           os.path.basename(path), exc_info=True)
            return None

    @classmethod
    def stored_identity(cls, path: str, source: str) -> str | None:
        """Отпечаток графа из кэша без чтения массивов; кэша нет или он не годится — None."""
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'rb') as f, np.load(f, allow_pickle=False) as z:
                return str(z['identity']) if cls._valid(z, source) else None
        except Exception:
            return None


def graph_from_pbf(path: str, source: str = '') -> RoadGraph:
    """Граф для легковой машины из файла .osm.pbf (pyosmium, один проход с координатами узлов)."""
    import osmium   # только для сборки графа

    nodes: dict[int, Point] = {}
    ways: list[Way] = []

    class Handler(osmium.SimpleHandler):
        def way(self, w: Any) -> None:
            direction = way_direction(w.tags)
            if direction is None:
                return
            refs = []
            for n in w.nodes:
                if n.location.valid():
                    refs.append(n.ref)
                    nodes[n.ref] = (n.location.lat, n.location.lon)
            ways.append((refs, direction))

    Handler().apply_file(path, locations=True)
    return RoadGraph.from_ways(nodes, ways, source)


def load_graph(path: str, source: str, rebuild: bool = False) -> RoadGraph:
    """Граф из кэша рядом с картой; кэша нет, он битый или карта сменилась — сборка из карты и
    запись кэша (не записался — работаем с графом в памяти)."""
    graph_path = _cache_path(path, 'graph')
    graph = None if rebuild else RoadGraph.load(graph_path, source)
    if graph is None:
        started = time.perf_counter()
        graph = graph_from_pbf(path, source)
        logger.info('[Routes] Граф дорог собран из %s: узлов %d, рёбер %d, %.1f с',
                    os.path.basename(path), graph.n_nodes, len(graph.src),
                    time.perf_counter() - started)
        try:
            graph.save(graph_path)
        except OSError:
            logger.exception('[Routes] Кэш графа %s не записан — граф только в памяти', graph_path)
    return graph


def without_zone(graph: RoadGraph, zone: Sequence[Point]) -> RoadGraph:
    """Граф в объезд зоны (малого центра): без рёбер, у которых начало, конец или середина в зоне. Узлы и их индексы —
    те же; узлы в зоне остаются без рёбер, и RoadNetwork к ним не привязывает (они не в наибольшей сильно связной
    компоненте). Отпечаток — свой: версия карты, граница зоны и оставшиеся рёбра."""
    lat, lon, src, dst = graph.lat, graph.lon, graph.src, graph.dst
    inside = _in_polygon_np(lat, lon, zone)
    drop = inside[src] | inside[dst] | _in_polygon_np((lat[src] + lat[dst]) / 2, (lon[src] + lon[dst]) / 2, zone)
    keep = ~drop
    return RoadGraph(lat, lon, src[keep], dst[keep], graph.km[keep], f'{graph.source}|center:{zone_key(zone)}')


class RoadNetwork:
    """Граф в матричном виде, обратный граф и KD-дерево привязки (по наибольшей сильно связной
    компоненте: из любой привязанной точки есть путь в любую другую)."""

    def __init__(self, graph: RoadGraph):
        n = graph.n_nodes
        self.graph = graph
        self._g = csr_matrix((graph.km, (graph.src, graph.dst)), shape=(n, n))
        self._gt = self._g.T.tocsr()
        _, labels = connected_components(self._g, directed=True, connection='strong')
        big = int(np.argmax(np.bincount(labels))) if n else 0
        self._snap_nodes = np.flatnonzero(labels == big) if n else np.zeros(0, dtype=np.int64)
        self._lat0 = math.radians(float(np.mean(graph.lat))) if n else 0.0
        self._tree = (cKDTree(self._project(graph.lat[self._snap_nodes], graph.lon[self._snap_nodes]))
                      if len(self._snap_nodes) else None)

    def _project(self, lat: Any, lon: Any) -> Any:
        """Локальная равнопромежуточная проекция, км (для поиска ближайшего узла)."""
        k = EARTH_RADIUS_KM * math.pi / 180.0
        return np.column_stack((np.asarray(lon) * k * math.cos(self._lat0), np.asarray(lat) * k))

    def snap(self, points: Sequence[Point]) -> tuple[Any, Any]:
        """(узел или -1, км до узла) для каждой точки; дальше SNAP_MAX_KM — -1."""
        if not points or self._tree is None:
            return np.full(len(points), -1, dtype=np.int64), np.full(len(points), np.inf)
        lat = np.array([p[0] for p in points], dtype=np.float64)
        lon = np.array([p[1] for p in points], dtype=np.float64)
        _, i = self._tree.query(self._project(lat, lon))
        nodes = self._snap_nodes[i].astype(np.int64)
        km = _haversine_np(lat, lon, self.graph.lat[nodes], self.graph.lon[nodes])
        return np.where(km <= SNAP_MAX_KM, nodes, -1), km

    def distances(self, sources: Any, targets: Any, reverse: bool = False) -> Any:
        """Км по дорогам [len(sources) × len(targets)]: от sources[i] до targets[j]; reverse — от
        targets[j] до sources[i] (Dijkstra по обратному графу). Пути нет — inf."""
        g = self._gt if reverse else self._g
        out = np.empty((len(sources), len(targets)), dtype=np.float64)
        for k in range(0, len(sources), DIJKSTRA_BATCH):
            rows = dijkstra(g, directed=True, indices=sources[k:k + DIJKSTRA_BATCH])
            out[k:k + DIJKSTRA_BATCH] = rows[:, targets]
        return out

    def climbs(self, sources: Any, targets: Any, z: Any, reverse: bool = False, pause: float = 0.0) -> Any:
        """Эффективный подъём, м [len(sources) × len(targets)] по тем же кратчайшим путям, что у distances (reverse — от
        targets[j] к sources[i]); z — высоты узлов. Ребро дерева Dijkstra p → v — terrain.edge_climb, его длина — разность
        расстояний от источника; сумма по пути — _tree_sums. Пути нет — NaN. pause — фоновый расчёт: после каждого шага
        сон pause × его время, Dijkstra — по CLIMB_BATCH источников (запросы сервера не ждут процессор)."""
        g = self._gt if reverse else self._g
        out = np.empty((len(sources), len(targets)), dtype=np.float32)
        batch = CLIMB_BATCH if pause > 0 else DIJKSTRA_BATCH

        def rest(since: float) -> None:
            if pause > 0:
                time.sleep(pause * (time.perf_counter() - since))
        for k in range(0, len(sources), batch):
            started = time.perf_counter()
            dist, pred = dijkstra(g, directed=True, indices=sources[k:k + batch], return_predecessors=True)
            rest(started)
            for i in range(len(dist)):
                started = time.perf_counter()
                d, p = dist[i], pred[i]
                root = p < 0
                q = np.where(root, 0, p)
                length = np.where(root, 0.0, d - d[q])
                # по обратному графу предшественник — следующий узел пути к источнику: ребро узел → предшественник
                w = terrain.edge_climb(z, z[q], length) if reverse else terrain.edge_climb(z[q], z, length)
                w[root] = 0.0
                up = _tree_sums(p, w)[targets]
                out[k + i] = np.where(np.isfinite(d[targets]), up, np.nan)
                rest(started)
        return out

    def lines(self, lines: Sequence[Sequence[Point]]) -> list[list[Point]]:
        """Ломаные вдоль дорог для карты: каждая линия (точки по порядку объезда) → точки по кратчайшему
        пути A → B между соседними точками (paths), упрощённые до PATH_SIMPLIFY_KM (draw). Участок, где точка не
        привязана или пути нет, — по прямой."""
        return self.draw(lines, self.paths([(a, b) for line in lines for a, b in zip(line, line[1:])]))

    def paths(self, legs: Sequence[tuple[Point, Point]]) -> dict[tuple[Point, Point], tuple[Any, Any]]:
        """Кратчайшие пути участков A → B для карты: (ключ A, ключ B) → (широты, долготы) узлов пути; обе точки у
        одного узла — путь без узлов (по прямой, как и км). Участка, где точка не привязана или пути нет, в ответе
        нет. Поиск — Dijkstra от каждого узла-начала с пределом PATH_LIMIT_FACTOR × по прямой (не нашёлся — без
        предела)."""
        points = list({point_key(p) for leg in legs for p in leg})
        snapped, _ = self.snap(points)
        node = {p: int(n) for p, n in zip(points, snapped)}
        starts: dict[int, dict[int, float]] = {}   # узел-начало → узел-конец → км по прямой
        for a, b in legs:
            na, nb = node[point_key(a)], node[point_key(b)]
            if na >= 0 and nb >= 0 and na != nb:
                starts.setdefault(na, {})[nb] = haversine_km(a, b)
        found: dict[tuple[int, int], Any] = {}
        for na, targets in starts.items():
            limit = PATH_LIMIT_FACTOR * max(targets.values()) + PATH_LIMIT_SLACK_KM
            dist, pred = dijkstra(self._g, directed=True, indices=na, return_predecessors=True, limit=limit)
            if not all(math.isfinite(dist[nb]) for nb in targets):
                dist, pred = dijkstra(self._g, directed=True, indices=na, return_predecessors=True)
            for nb in targets:
                if math.isfinite(dist[nb]):
                    found[na, nb] = _walk_back(pred, na, nb)
        out: dict[tuple[Point, Point], tuple[Any, Any]] = {}
        for a, b in legs:
            ka, kb = point_key(a), point_key(b)
            na, nb = node[ka], node[kb]
            path = np.zeros(0, dtype=np.int64) if na >= 0 and na == nb else found.get((na, nb))
            if path is not None:
                out[ka, kb] = (self.graph.lat[path], self.graph.lon[path])
        return out

    def draw(self, lines: Sequence[Sequence[Point]],
             paths: Mapping[tuple[Point, Point], tuple[Any, Any]]) -> list[list[Point]]:
        """Линии для карты: точки линии, между соседними — путь участка из paths (RoadNetwork.paths; участка нет —
        по прямой), упрощённые до PATH_SIMPLIFY_KM."""
        out: list[list[Point]] = []
        for line in lines:
            if not line:
                out.append([])
                continue
            lat: list[Any] = [np.array([line[0][0]])]
            lon: list[Any] = [np.array([line[0][1]])]
            for a, b in zip(line, line[1:]):
                path = paths.get((point_key(a), point_key(b)))
                if path is not None:
                    lat.append(path[0])
                    lon.append(path[1])
                lat.append(np.array([b[0]]))
                lon.append(np.array([b[1]]))
            la, lo = np.concatenate(lat), np.concatenate(lon)
            keep = _simplify(self._project(la, lo), PATH_SIMPLIFY_KM)
            out.append([(round(float(la[i]), 5), round(float(lo[i]), 5)) for i in keep])
        return out


def _walk_back(pred: Any, source: int, target: int) -> Any:
    """Узлы пути source → target по массиву предшественников Dijkstra."""
    path = [target]
    while path[-1] != source:
        path.append(int(pred[path[-1]]))
    return np.array(path[::-1], dtype=np.int64)


def _tree_sums(pred: Any, w: Any) -> Any:
    """Сумма w по пути от корня дерева Dijkstra до каждого узла (w[v] — ребро предшественник → v; корень и узлы без пути —
    0): удвоение указателей — log₂(глубины) векторных шагов по всем узлам вместо обхода каждого пути в Python."""
    idx = np.arange(len(pred))
    p = np.where(pred < 0, idx, pred)
    acc = np.where(pred < 0, 0.0, w)
    while True:
        nxt = p[p]
        if np.array_equal(nxt, p):   # все указывают на корень: суммы полные
            return acc
        acc = acc + acc[p]
        p = nxt


def _simplify(xy: Any, tol: float) -> Any:
    """Индексы точек ломаной xy (км) после упрощения Рамера — Дугласа — Пекера с допуском tol км;
    первая и последняя точки остаются всегда."""
    n = len(xy)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        dx, dy = xy[j] - xy[i]
        rel = xy[i + 1:j] - xy[i]
        seg = math.hypot(dx, dy)
        d = (np.abs(dx * rel[:, 1] - dy * rel[:, 0]) / seg if seg > 0
             else np.hypot(rel[:, 0], rel[:, 1]))
        k = int(np.argmax(d))
        if d[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return np.flatnonzero(keep)


# --- Кэш расстояний ---

def point_key(p: Point) -> Point:
    return (round(float(p[0]), KEY_DECIMALS), round(float(p[1]), KEY_DECIMALS))


@dataclass(frozen=True)
class _Table:
    """Неизменяемое состояние кэша: чтение без блокировки, дозаполнение — заменой целиком."""
    points: dict[Point, tuple[int, float]]   # ключ точки → (строка матрицы или -1, км привязки)
    nodes: Any                               # np.ndarray int64: узел графа строки матрицы
    road: Any                                # np.ndarray float32 [k × k]: км по дорогам строка → столбец


def _empty_table() -> _Table:
    return _Table({}, np.zeros(0, dtype=np.int64), np.zeros((0, 0), dtype=np.float32))


@dataclass(frozen=True)
class _Climbs:
    """Неизменяемое состояние кэша подъёмов (№85): узлы графа и матрица подъёмов между ними (м, NaN — пути нет)."""
    key: str                  # граф, высоты и CLIMB_C (_climb_key); '' — не посчитано
    graph: str | None         # отпечаток графа таблицы км, к которой относится
    nodes: Any                # np.ndarray int64: узел графа строки
    up: Any                   # np.ndarray float32 [c × c]
    index: dict[int, int] = field(default_factory=dict)   # узел графа → строка


def _empty_climbs(key: str = '', graph: str | None = None) -> _Climbs:
    return _Climbs(key, graph, np.zeros(0, dtype=np.int64), np.zeros((0, 0), dtype=np.float32))


def _climb_key(graph_identity: str, elev: str) -> str:
    return f'{_dist_key(graph_identity)}|{elev}|c{terrain.CLIMB_C:g}'


def elevation_loader(path: str, version: str) -> Callable[[], tuple[str, Any] | None]:
    """Высоты узлов графа карты path из её кэша <карта>.elev.npz: () → (ключ высот, массив) или None (кэша нет, он от
    другого графа или параметров DEM). Файл перечитывается, только когда он сменился (os.stat на вызов): собранный
    командой dem кэш подхватывается без перезапуска сервера."""
    elev_path = _cache_path(path, 'elev')
    graph_path = _cache_path(path, 'graph')
    state: dict[str, Any] = {}
    lock = threading.Lock()

    def load() -> tuple[str, Any] | None:
        if not terrain.terrain_supported():
            return None
        sig = map_signature(elev_path)
        with lock:
            if sig is None:
                state.clear()
                return None
            if state.get('sig') != sig:
                identity = RoadGraph.stored_identity(graph_path, version)
                key = terrain.elev_key(identity) if identity is not None else None
                z = terrain.load_elevation(elev_path, key) if key is not None else None
                stored = terrain.stored_elevation(elev_path)
                if z is None and stored is not None and not state.get('warned'):
                    state['warned'] = True
                    logger.warning('[Routes] Кэш высот %s — от другого графа или параметров DEM: без рельефа до '
                                   '«python -m route_optimizer.roads dem» (или warm --if-stale)', os.path.basename(elev_path))
                # в ключе — и отпечаток тайлов: докачан тайл, высоты те же по ключу, но другие — подъёмы пересчитываются
                state.update(sig=sig, got=(f'{key}|{stored[1]}', z) if z is not None and stored is not None else None)
            return state['got']
    return load


class RoadDistances:
    """Км между точками по дорогам. ensure(точки) — один расчёт на весь набор (первый раз — минуты,
    дальше кэш), km(a, b) — O(1). Потокобезопасно: дозаполнение под lock, чтение — без него.

    Сбой графа (битая карта, нет osmium) — failed = True: km() → None для всех точек.

    Файл кэша годится, только если он посчитан на том же графе: graph_identity — отпечаток графа
    без его загрузки (None — проверить нечем, файл не читается)."""

    km_source = 'osm'   # откуда км (тот же интерфейс, что у valhalla_engine.ValhallaRoads)

    def __init__(self, version: str, load_network: Callable[[], RoadNetwork],
                 cache_path: str | None = None,
                 graph_identity: Callable[[], str | None] | None = None, map_path: str | None = None,
                 elevation: Callable[[], tuple[str, Any] | None] | None = None, climb_path: str | None = None,
                 background: bool = False):
        self.version = version
        self.map_path = map_path   # файл карты (для отпечатка содержимого в road_model_id); None — граф в памяти
        self._load_network = load_network
        self._cache_path = cache_path
        self._graph_identity = graph_identity
        self._cache_read = cache_path is None
        self._lock = threading.Lock()
        self._table = _empty_table()
        self._identity: str | None = None   # отпечаток графа, на котором посчитана таблица
        self.failed = False
        # рельеф (№85): высоты узлов графа () → (ключ, массив) или None, кэш подъёмов; нет высот — рельефа нет
        self.elevation = elevation
        self._climb_path = climb_path
        self._climbs = _empty_climbs()
        self._climb_failed = False
        self.background = background        # ensure_climb досчитывает новые узлы фоновым потоком (сервер), запрос не ждёт
        self._climb_lock = threading.Lock()   # расчёт и запись кэша подъёмов; блокировку км (_lock) не держит
        self._want_lock = threading.Lock()    # очередь узлов фонового потока — держится мгновения
        self._climb_want: set[int] = set()
        self._climb_thread: threading.Thread | None = None

    @classmethod
    def for_map(cls, path: str, version: str, cache_name: str = 'dist') -> RoadDistances:
        graph_path = _cache_path(path, 'graph')
        return cls(version, lambda: RoadNetwork(load_graph(path, version)),
                   _cache_path(path, cache_name),
                   lambda: RoadGraph.stored_identity(graph_path, version), path,
                   elevation_loader(path, version), _cache_path(path, cache_name.replace('dist', 'climb', 1)), True)

    @classmethod
    def for_graph(cls, graph: RoadGraph, version: str = 'memory',
                  cache_path: str | None = None, elevation: Any | None = None) -> RoadDistances:
        """Готовый граф (тесты и разовые расчёты); cache_path=None — без файла кэша; elevation — высоты узлов (рельеф)."""
        got = None if elevation is None else (f'memory|{zlib.crc32(np.ascontiguousarray(elevation).tobytes()):08x}',
                                              np.asarray(elevation, dtype=np.float32))
        return cls(version, lambda: RoadNetwork(graph), cache_path, lambda: graph.identity,
                   elevation=(lambda: got) if got is not None else None)

    @classmethod
    def around_zone(cls, load: Callable[[], RoadGraph], zone: Sequence[Point], version: str,
                    cache_path: str | None = None, map_path: str | None = None,
                    elevation: Callable[[], tuple[str, Any] | None] | None = None,
                    climb_path: str | None = None) -> RoadDistances:
        """Км в объезд зоны: граф load() без зоны (without_zone). Отпечаток графа без зоны — только после загрузки
        обычного графа (из кэша на диске — доли секунды), один раз на объект: при первом чтении файла кэша. Узлы —
        те же, что у графа load(): высоты elevation — его."""
        zone = tuple(zone)
        return cls(f'{version}|center:{zone_key(zone)}', lambda: RoadNetwork(without_zone(load(), zone)),
                   cache_path, lambda: without_zone(load(), zone).identity, map_path, elevation, climb_path,
                   map_path is not None)

    @property
    def size(self) -> tuple[int, int]:
        """(точек, узлов) в кэше."""
        t = self._table
        return len(t.points), len(t.nodes)

    def ensure(self, points: Iterable[Point | None]) -> None:
        """Досчитать расстояния для точек, которых ещё нет в кэше (все пары со всеми точками кэша)."""
        keys = {point_key(p) for p in points if p is not None}
        if self.failed or keys.issubset(self._table.points):
            return
        with self._lock:
            if self.failed:
                return
            try:
                if not self._cache_read:
                    self._read_cache()
                known = self._table.points
                new = sorted(k for k in keys if k not in known)
                if not new:
                    return
                started = time.perf_counter()
                net = self._load_network()
                logger.info('[Routes] Граф дорог загружен за %.1f с', time.perf_counter() - started)
                if self._identity is not None and self._identity != net.graph.identity:
                    # граф не тот, на котором посчитана таблица, — все точки заново
                    logger.info('[Routes] Граф дорог сменился — кэш расстояний пересчитывается')
                    new = sorted(set(known) | set(new))
                    self._table = _empty_table()
                self._extend(net, new)
                self._identity = net.graph.identity
                self._write_cache()
            except Exception:
                logger.exception('[Routes] Расстояния по дорогам не посчитаны — дальше по прямой')
                self.failed = True

    def km(self, a: Point, b: Point) -> float | None:
        """Км A → B по дорогам; None — точка не привязана, пути нет или дороги сломаны."""
        t = self._table
        pa, pb = t.points.get(point_key(a)), t.points.get(point_key(b))
        if pa is None or pb is None:
            if self.failed:
                return None
            logger.info('[Routes] Дороги: точка вне общего расчёта — досчитывается отдельно')
            self.ensure([a, b])
            t = self._table
            pa, pb = t.points.get(point_key(a)), t.points.get(point_key(b))
            if pa is None or pb is None:
                return None
        (ma, sa), (mb, sb) = pa, pb
        if ma < 0 or mb < 0:
            return None
        if ma == mb:
            return haversine_km(a, b)
        road = t.road.item(ma, mb)
        if not math.isfinite(road):
            return None
        return sa + road + sb

    def minutes(self, a: Point, b: Point, city: bool) -> float | None:
        """Времени граф не знает: минуты участка — км / скорость зоны (Norms.leg_speed). Тот же интерфейс,
        что у valhalla_engine.ValhallaRoads."""
        return None

    def truck(self, truck_time: bool | None = None) -> RoadDistances:
        """Граф один для всех машин (у ValhallaRoads — профиль грузовика) и времени не знает: truck_time (минуты
        грузовиков из Valhalla) здесь ничего не меняет — прежняя модель."""
        return self

    def lines(self, lines: Sequence[Sequence[Point]]) -> list[list[Point]] | None:
        """Линии рейсов вдоль дорог для карты (RoadNetwork.lines); дороги сломаны — None: рисовать по
        прямой. Граф загружается на время запроса (из кэша на диске — доли секунды)."""
        if self.failed:
            return None
        try:
            return self._load_network().lines(lines)
        except Exception:
            logger.exception('[Routes] Линии по дорогам не построены — на карте по прямой')
            return None

    def unsnapped(self, points: Iterable[Point | None]) -> int:
        """Сколько разных точек из набора дальше SNAP_MAX_KM от дороги (из посчитанных)."""
        t = self._table
        keys = {point_key(p) for p in points if p is not None}
        return sum(1 for k in keys if k in t.points and t.points[k][0] < 0)

    # --- Рельеф (№85) ---

    @property
    def terrain(self) -> bool:
        """Есть высоты узлов графа (кэш высот годится) и дороги не сломаны: участки получают подъём (climb)."""
        return (not self.failed and not self._climb_failed and self.elevation is not None
                and self.elevation() is not None)

    def ensure_climb(self, points: Iterable[Point | None], wait: bool | None = None) -> bool:
        """Подъёмы между точками (и со всеми точками кэша подъёмов): км — ensure, подъём — Dijkstra от новых узлов вперёд и по
        обратному графу (RoadNetwork.climbs). Все узлы уже в кэше — сразу, без блокировок. Иначе wait (None — у сервера
        нет, background) — досчитать сейчас; без ожидания — узлы в очередь фонового потока, ответ сразу. True — подъёмы
        всех точек есть (точки без привязки не в счёт); False — рельефа нет или подъёмы ещё считаются (climb → None)."""
        got = self.elevation() if self.elevation is not None and not self._climb_failed else None
        if got is None:
            self._climbs = _empty_climbs()
            return False
        points = [p for p in points if p is not None]
        self.ensure(points)
        t, identity = self._table, self._identity
        if self.failed or identity is None:
            return False
        key = _climb_key(identity, got[0])
        rows = {t.points[k][0] for k in {point_key(p) for p in points} if k in t.points}
        nodes = {int(t.nodes[r]) for r in rows if r >= 0}
        c = self._climbs
        if c.key == key and c.graph == identity and nodes <= c.index.keys():
            return True
        if wait if wait is not None else not self.background:
            self._fill_climbs(nodes)
            c = self._climbs
            return c.key == key and nodes <= c.index.keys()
        with self._want_lock:
            self._climb_want |= nodes
            if self._climb_thread is None:
                self._climb_thread = threading.Thread(target=self._climb_worker, name='routes-climbs', daemon=True)
                self._climb_thread.start()
        return False

    def _climb_worker(self) -> None:
        """Фоновый поток подъёмов: берёт очередь узлов, пока она не пуста, уступая процессор запросам (CLIMB_PAUSE; сбой —
        в журнал, рельефа нет)."""
        while True:
            with self._want_lock:
                todo, self._climb_want = self._climb_want, set()
                if not todo:
                    self._climb_thread = None
                    return
            self._fill_climbs(todo, CLIMB_PAUSE)

    def _fill_climbs(self, nodes: Collection[int], pause: float = 0.0) -> None:
        """Досчитать подъёмы узлов графа nodes (под своей блокировкой, кусками по CLIMB_CHUNK с записью кэша после
        каждого: прерванный warm не теряет сделанного; pause — фоновый поток, RoadNetwork.climbs). Сбой — рельефа нет до
        пересоздания дорог."""
        with self._climb_lock:
            try:
                got = self.elevation() if self.elevation is not None and not self._climb_failed else None
                t, identity = self._table, self._identity
                if got is None or identity is None:
                    return
                key = _climb_key(identity, got[0])
                if self._climbs.key != key or self._climbs.graph != identity:
                    self._read_climbs(key, identity)
                known = set(t.nodes.tolist())
                new = sorted(n for n in set(nodes) - self._climbs.index.keys() if n in known)
                if not new:
                    return
                started = time.perf_counter()
                net = self._load_network()
                if net.graph.identity != identity:   # граф сменился — км пересчитает следующий ensure, подъёмы — за ним
                    return
                z = got[1]
                for k in range(0, len(new), CLIMB_CHUNK):
                    c = self._climbs
                    part = np.array(new[k:k + CLIMB_CHUNK], dtype=np.int64)
                    k_old = len(c.nodes)
                    all_nodes = np.concatenate([c.nodes, part])
                    up = np.full((len(all_nodes), len(all_nodes)), np.nan, dtype=np.float32)
                    up[:k_old, :k_old] = c.up
                    up[k_old:, :] = net.climbs(part, all_nodes, z, pause=pause)                  # новый узел → все
                    if k_old:
                        up[:k_old, k_old:] = net.climbs(part, c.nodes, z, reverse=True, pause=pause).T   # прежние → новый
                    self._climbs = _Climbs(key, identity, all_nodes, up, {int(n): i for i, n in enumerate(all_nodes)})
                    self._write_climbs()
                logger.info('[Routes] Подъёмы участков: +%d узлов, всего %d; %.1f с', len(new), len(self._climbs.nodes),
                            time.perf_counter() - started)
            except Exception:
                logger.exception('[Routes] Подъёмы участков не посчитаны — без рельефа')
                self._climb_failed = True
                self._climbs = _empty_climbs()

    def climb(self, a: Point, b: Point) -> float | None:
        """Эффективный подъём участка A → B, м (может быть < 0: спуск); точки в одном узле — 0. None — рельефа нет,
        точка не привязана, пути нет или подъём не досчитан (ensure_climb)."""
        c, t = self._climbs, self._table
        if not c.key or c.graph != self._identity:
            return None
        pa, pb = t.points.get(point_key(a)), t.points.get(point_key(b))
        if pa is None or pb is None or pa[0] < 0 or pb[0] < 0:
            return None
        na, nb = int(t.nodes[pa[0]]), int(t.nodes[pb[0]])
        if na == nb:
            return 0.0
        ia, ib = c.index.get(na), c.index.get(nb)
        if ia is None or ib is None:
            return None
        v = c.up.item(ia, ib)
        return v if math.isfinite(v) else None

    def _read_climbs(self, key: str, identity: str) -> None:
        """Кэш подъёмов из файла, если он посчитан на том же графе и тех же высотах; иначе пустой с этим ключом."""
        self._climbs = _empty_climbs(key, identity)
        path = self._climb_path
        if path is None or not os.path.exists(path):
            return
        try:
            with open(path, 'rb') as f, np.load(f, allow_pickle=False) as z:
                if str(z['key']) != key:
                    logger.info('[Routes] Кэш подъёмов %s — от других дорог или высот, пересчёт', os.path.basename(path))
                    return
                nodes = z['nodes'].astype(np.int64)
                self._climbs = _Climbs(key, identity, nodes, z['up'].astype(np.float32),
                                       {int(n): i for i, n in enumerate(nodes)})
        except Exception:   # zipfile.BadZipFile, EOFError, KeyError, ValueError, OSError…
            logger.warning('[Routes] Кэш подъёмов %s не прочитан — пересчёт', os.path.basename(path), exc_info=True)

    def _write_climbs(self) -> None:
        path, c = self._climb_path, self._climbs
        if path is None or not c.key:
            return
        try:
            _save_npz(path, key=c.key, nodes=c.nodes, up=c.up)
        except OSError:   # кэш в памяти остаётся — не сохранился только файл
            logger.exception('[Routes] Кэш подъёмов %s не записан', path)

    def _extend(self, net: RoadNetwork, new: Sequence[Point]) -> None:
        """Граф (~100 МБ в памяти) держится только на время дозаполнения: из кэша на диске он
        загружается за доли секунды, а новые точки появляются редко."""
        started = time.perf_counter()
        t = self._table
        snapped, snap_km = net.snap(new)
        node_index = {int(n): i for i, n in enumerate(t.nodes)}
        fresh = sorted({int(n) for n in snapped if n >= 0} - node_index.keys())
        k_old, k = len(t.nodes), len(t.nodes) + len(fresh)
        nodes = np.concatenate([t.nodes, np.array(fresh, dtype=np.int64)])
        road = np.full((k, k), np.inf, dtype=np.float32)
        road[:k_old, :k_old] = t.road
        if fresh:
            src = np.array(fresh, dtype=np.int64)
            road[k_old:, :] = net.distances(src, nodes)                     # новый узел → все
            road[:k_old, k_old:] = net.distances(src, nodes[:k_old], reverse=True).T   # прежние → новый
        for i, n in enumerate(fresh):
            node_index[n] = k_old + i
        points = dict(t.points)
        for p, n, d in zip(new, snapped, snap_km):
            points[p] = (node_index[int(n)], float(d)) if n >= 0 else (-1, float(d))
        self._table = _Table(points, nodes, road)
        seconds = time.perf_counter() - started
        logger.info('[Routes] Дороги: +%d точек (новых узлов %d, не привязано %d), всего точек %d, '
                    'узлов %d; %.1f с', len(new), len(fresh), int(np.sum(snapped < 0)), len(points),
                    k, seconds)

    def _read_cache(self) -> None:
        """Таблица из файла, если он посчитан на том же графе; иначе (нет файла, другой граф,
        битый файл) — пустая таблица, точки досчитываются."""
        self._cache_read = True
        path = self._cache_path
        if path is None or not os.path.exists(path):
            return
        identity = self._graph_identity() if self._graph_identity is not None else None
        name = os.path.basename(path)
        try:
            with open(path, 'rb') as f, np.load(f, allow_pickle=False) as z:
                if identity is None or 'key' not in z.files or str(z['key']) != _dist_key(identity):
                    logger.info('[Routes] Кэш расстояний %s — от другого графа дорог, пересчёт', name)
                    return
                lat, lon, row, snap = z['lat'], z['lon'], z['row'], z['snap']
                points = {(float(a), float(b)): (int(r), float(s))
                          for a, b, r, s in zip(lat, lon, row, snap)}
                table = _Table(points, z['nodes'].astype(np.int64), z['road'].astype(np.float32))
        except Exception:   # zipfile.BadZipFile, EOFError, KeyError, ValueError, OSError…
            logger.warning('[Routes] Кэш расстояний %s не прочитан — пересчёт', name, exc_info=True)
            return
        self._table, self._identity = table, identity
        logger.info('[Routes] Кэш расстояний: точек %d, узлов %d', *self.size)

    def _write_cache(self) -> None:
        path = self._cache_path
        if path is None or self._identity is None:
            return
        t = self._table
        keys = list(t.points)
        try:
            _save_npz(path, key=_dist_key(self._identity),
                      lat=np.array([p[0] for p in keys], dtype=np.float64),
                      lon=np.array([p[1] for p in keys], dtype=np.float64),
                      row=np.array([t.points[p][0] for p in keys], dtype=np.int64),
                      snap=np.array([t.points[p][1] for p in keys], dtype=np.float64),
                      nodes=t.nodes, road=t.road)
        except OSError:   # кэш в памяти остаётся — не сохранился только файл
            logger.exception('[Routes] Кэш расстояний %s не записан', path)


class CenterBypassRoads:
    """Дороги «Развоза» с объездом малого центра (№39–41: в центр въезжают только машины с правом въезда) — тот же
    интерфейс, что у RoadDistances (ensure, km, minutes, truck, lines, unsnapped, version, failed, km_source, map_path,
    size) и detour. Правило одно для всех машин (у расчёта рейсов одна функция расстояния — Norms.km):
    - участок между точками вне центра — по графу без центра (bypass, without_zone); у объезда нет км (точка не
      привязана, пути нет, объезд не посчитался) — по обычному графу (base);
    - участок, где хоть одна точка в центре, — по обычному графу: точки в центре возит только машина с правом въезда.
    Машина без права въезда ездит только между точками вне центра (склад — тоже вне), значит, её участки центр
    объезжают. У машины с правом въезда участки между точками вне центра — тоже в объезд (принятое упрощение: км чуть
    осторожнее). Объезд считается только для точек, которые приходят в ensure (точки развоза и склад), а не для всего
    плана. Создаёт RoadProvider.bypass (границы нет — объезда нет)."""

    def __init__(self, base: RoadDistances, bypass: RoadDistances, zone: Sequence[Point]):
        self.base = base
        self.bypass = bypass
        self.zone = tuple((float(lat), float(lon)) for lat, lon in zone)
        self._inside: dict[Point, bool] = {}   # точка расчёта (ensure) → в центре: km спрашивает их тысячи раз

    @property
    def version(self) -> str:
        return self.bypass.version   # версия карты и граница центра

    @property
    def failed(self) -> bool:
        return self.base.failed

    @property
    def km_source(self) -> str:
        return self.base.km_source

    @property
    def map_path(self) -> str | None:
        return self.base.map_path

    @property
    def size(self) -> tuple[int, int]:
        return self.base.size

    def _inside_zone(self, p: Point) -> bool:
        """Точка в центре (geo.in_polygon — как у точек развоза, dispatch._central). Запомнены только точки ensure:
        точки запросов линий карты память не растят."""
        key = (p[0], p[1])
        hit = self._inside.get(key)
        return in_polygon(key, self.zone) if hit is None else hit

    def around(self, a: Point, b: Point) -> bool:
        """Участок A → B — в объезд центра: обе точки вне него."""
        return not (self._inside_zone(a) or self._inside_zone(b))

    def ensure(self, points: Iterable[Point | None]) -> None:
        """Обычный граф — для всех точек, объезд — для точек вне центра (первый раз — ~10 с на сотню точек, дальше —
        кэш на диске)."""
        points = [p for p in points if p is not None]
        for p in points:
            key = (p[0], p[1])
            if key not in self._inside:
                self._inside[key] = in_polygon(key, self.zone)
        self.base.ensure(points)
        if self.base.failed:   # карта не загрузилась — граф без центра из неё же: второй ошибки в журнале не нужно
            return
        before = self.bypass.size
        started = time.perf_counter()
        self.bypass.ensure([p for p in points if not self._inside_zone(p)])
        if self.bypass.size != before:   # прочитан кэш или досчитаны точки
            logger.info('[Routes] Объезд малого центра: точек %d, %.1f с', self.bypass.size[0],
                        time.perf_counter() - started)

    def km(self, a: Point, b: Point) -> float | None:
        if self.around(a, b):
            d = self.bypass.km(a, b)
            if d is not None:
                return d
        return self.base.km(a, b)

    @property
    def terrain(self) -> bool:
        return self.base.terrain

    def ensure_climb(self, points: Iterable[Point | None], wait: bool | None = None) -> bool:
        """Подъёмы — по тем же графам, что км: обычный — для всех точек, объезд — для точек вне центра. True — готовы оба."""
        points = [p for p in points if p is not None]
        self.ensure(points)
        ready = self.base.ensure_climb(points, wait)
        if self.base.failed:
            return False
        return self.bypass.ensure_climb([p for p in points if not self._inside_zone(p)], wait) and ready

    def climb(self, a: Point, b: Point) -> float | None:
        """Подъём участка по тому же пути, что km: в объезд, если у объезда есть км (подъёма нет — None, не путь обычного
        графа), иначе по обычному графу."""
        if self.around(a, b) and self.bypass.km(a, b) is not None:
            return self.bypass.climb(a, b)
        return self.base.climb(a, b)

    def detour(self, a: Point, b: Point) -> float:
        """Во сколько раз объезд длиннее кратчайшего пути по дорогам (≥ 1; не в объезд или км нет — 1). Им
        ValhallaRoads растягивает свои км и минуты (путь Valhalla — кратчайший): км и минуты участка — про один путь."""
        if not self.around(a, b):
            return 1.0
        d, direct = self.bypass.km(a, b), self.base.km(a, b)
        if d is None or direct is None or not d > direct > 0:
            return 1.0
        return d / direct

    def minutes(self, a: Point, b: Point, city: bool) -> float | None:
        """Времени граф не знает (как RoadDistances.minutes)."""
        return None

    def truck(self, truck_time: bool | None = None) -> CenterBypassRoads:
        return self

    def unsnapped(self, points: Iterable[Point | None]) -> int:
        """Точки без км по дорогам — по обычному графу (где объезд точку не привязал, км — по обычному)."""
        return self.base.unsnapped(points)

    def lines(self, lines: Sequence[Sequence[Point]]) -> list[list[Point]] | None:
        """Линии рейсов для карты по тому же правилу, что км: участок между точками вне центра — путь в объезд (нет
        его — по обычному графу), остальные — по обычному графу. Дороги сломаны — None; объезд сломан — как
        RoadDistances.lines (все участки — по обычному графу). Графы — по одному за раз (каждый ~100 МБ): сначала
        объезд, затем обычный."""
        if self.base.failed:
            return None
        legs = [(a, b) for line in lines for a, b in zip(line, line[1:])]
        around = [leg for leg in legs if self.around(*leg)]
        paths: dict[tuple[Point, Point], tuple[Any, Any]] = {}
        if around and not self.bypass.failed:
            try:
                paths = self.bypass._load_network().paths(around)
            except Exception:
                logger.exception('[Routes] Объезд малого центра для линий не построен — линии по обычному графу')
        try:
            direct = self.base._load_network()
            paths.update(direct.paths([(a, b) for a, b in legs if (point_key(a), point_key(b)) not in paths]))
            return direct.draw(lines, paths)
        except Exception:
            logger.exception('[Routes] Линии по дорогам не построены — на карте по прямой')
            return None


class RoadProvider:
    """Дороги для расчёта: карта есть — RoadDistances (пересоздаётся при смене файла карты; не
    собралась — с failed = True: по прямой с предупреждением roads_failed), нет — None.
    Проверка карты — os.stat на каждый вызов: подложенная карта подхватывается без перезапуска.
    «Развозу» — объезд малого центра поверх тех же дорог (bypass): один на карту и границу центра."""

    def __init__(self, path: str | None = None):
        self.path = path or osm_path()
        self._lock = threading.Lock()
        self._current: RoadDistances | None = None
        self._bypass: CenterBypassRoads | None = None

    def get(self) -> RoadDistances | None:
        version = map_signature(self.path)
        if version is None or not roads_supported():
            return None
        with self._lock:
            if self._current is None or self._current.version != version:
                self._current = RoadDistances.for_map(self.path, version)
            return self._current

    def bypass(self, base: RoadDistances, zone: Sequence[Point]) -> CenterBypassRoads | RoadDistances:
        """Дороги с объездом центра zone поверх base (то, что вернул get): сменились карта или граница — новый объезд
        (кэш на диске — <карта>.dist-center.npz, один на нынешнюю границу). Границы нет (меньше трёх вершин: центра
        нет и в dispatch._central) — сами base, расчёт как без объезда."""
        if len(zone) < 3:
            return base
        zone = tuple((float(lat), float(lon)) for lat, lon in zone)   # сравнение — по вершинам, не по отпечатку
        with self._lock:
            cur = self._bypass
            if cur is None or cur.base is not base or cur.zone != zone:
                bypass = RoadDistances.around_zone(lambda: load_graph(self.path, base.version), zone, base.version,
                                                   _cache_path(self.path, 'dist-center'), self.path, base.elevation,
                                                   _cache_path(self.path, 'climb-center'))
                cur = self._bypass = CenterBypassRoads(base, bypass, zone)
            return cur


def open_roads(path: str | None = None, cache_name: str = 'dist') -> RoadDistances | None:
    """RoadDistances по карте (для скриптов); карты или numpy/scipy нет — None."""
    path = path or osm_path()
    version = map_signature(path)
    if version is None or not roads_supported():
        return None
    return RoadDistances.for_map(path, version, cache_name)


def roads_version(roads: RoadDistances | CenterBypassRoads | None) -> str:
    """Для ключа кэша оценки: версия карты ('…:failed' — карта не загрузилась) или 'off'."""
    if roads is None:
        return 'off'
    return f'{roads.version}:failed' if roads.failed else roads.version


# --- Команды ---

def _download(path: str, url: str) -> None:
    import urllib.request

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    print(f'Скачивание {url} → {path}')
    started = time.perf_counter()
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, path)
    print(f'Готово: {os.path.getsize(path) / 1e6:.1f} МБ за {time.perf_counter() - started:.0f} с')


def _dist_cache_stale(path: str, zone: Sequence[Point] = ()) -> bool:
    """Кэш расстояний карты path не подходит к нынешнему графу и формату (сменился DIST_FORMAT, как 2 → 3, или карта),
    а с границей малого центра zone — и кэш объезда (<карта>.dist-center.npz: нет его, сменились карта или граница):
    первый расчёт после обновления пересчитал бы его целиком (~2 мин под блокировкой дорог). Проверка — без ERP; граф
    с диска (доли секунды) — только для отпечатка графа без центра; карты или numpy/scipy нет — греть нечего (False)."""
    version = map_signature(path)
    if version is None or not roads_supported():
        return False
    graph_path = _cache_path(path, 'graph')
    identity = RoadGraph.stored_identity(graph_path, version)
    if identity is None:
        return True   # графа в кэше нет или он от другой карты — warm соберёт и его
    if _cache_key_stale(_cache_path(path, 'dist'), identity):
        return True
    elev = elevation_loader(path, version)()   # рельеф (№85): высоты есть — и кэш подъёмов должен им годиться
    if elev is not None and _climb_cache_stale(_cache_path(path, 'climb'), _climb_key(identity, elev[0])):
        return True
    if len(zone) < 3:
        return False
    graph = RoadGraph.load(graph_path, version)
    if graph is None:
        return True
    around = without_zone(graph, zone).identity
    return _cache_key_stale(_cache_path(path, 'dist-center'), around) or (
        elev is not None and _climb_cache_stale(_cache_path(path, 'climb-center'), _climb_key(around, elev[0])))


def _climb_cache_stale(cache: str, key: str) -> bool:
    """Файл кэша подъёмов cache посчитан не с ключом key (нет файла, битый, другие граф или высоты)."""
    try:
        with open(cache, 'rb') as f, np.load(f, allow_pickle=False) as z:
            return str(z['key']) != key
    except Exception:   # файла нет или он битый
        return True


def _cache_key_stale(cache: str, identity: str) -> bool:
    """Файл кэша расстояний cache посчитан не на графе с отпечатком identity (нет файла, битый, другой граф)."""
    try:
        with open(cache, 'rb') as f, np.load(f, allow_pickle=False) as z:
            return 'key' not in z.files or str(z['key']) != _dist_key(identity)
    except Exception:   # файла нет или он битый
        return True


def _db_path() -> str:
    return os.environ.get('ROUTES_DB_PATH') or os.path.join(REPO_ROOT, 'route_optimizer.db')


def _load_bundle_readonly(db_path: str) -> Bundle:
    """Настройки маршрутов для команд warm — из копии базы (Store.load_copy), сама база не меняется: задача обновления
    запускает warm до перезапуска сервера, и миграция базы сломала бы каждое обращение к ней ещё работающему серверу
    прежней версии («База маршрутов создана более новой версией программы»). Базы нет — настройки по умолчанию, как у
    сервера при первом запуске."""
    from .store import SCHEMA_VERSION, Store

    bundle, version = Store(db_path).load_copy()
    if version is not None and version != SCHEMA_VERSION:
        print(f'База маршрутов: схема {version}, у программы — {SCHEMA_VERSION}. Настройки — из временной копии, '
              f'приведённой к схеме программы; сама база не меняется')
    return bundle


def _warm(path: str) -> None:
    """Матрица для всех точек текущего плана: снимок ERP (только чтение) + настройки маршрутов (из копии базы); затем
    объезд малого центра («Развоз») для тех же точек — заказы развоза почти все у клиентов плана, и первому расчёту
    дня после обновления не придётся считать его (~2 мин на точки плана)."""
    from .valhalla_engine import ENGINE_ENV, ENGINE_OSM

    sys.path.insert(0, REPO_ROOT)
    os.environ[ENGINE_ENV] = ENGINE_OSM   # import app_v2 вызывает init_app: фон Valhalla этой команде не нужен
    import app_v2  # noqa: F401 — только строка подключения к ERP; сервер не запускается

    from . import evaluate
    from .snapshot import load_snapshot

    provider = RoadProvider(path)
    roads = provider.get()
    if roads is None:
        raise SystemExit('Карты нет — сначала: python -m route_optimizer.roads download')
    snap = load_snapshot(app_v2.db.connection_string)
    bundle = _load_bundle_readonly(_db_path())
    points = evaluate.plan_points(snap, bundle, {})   # и склад
    started = time.perf_counter()
    roads.ensure(points)
    n_points, n_nodes = roads.size
    print(f'Точек плана {len(set(map(point_key, points)))}, не привязано {roads.unsnapped(points)}; '
          f'в кэше точек {n_points}, узлов {n_nodes}; {time.perf_counter() - started:.0f} с')
    around = provider.bypass(roads, [(lat, lon) for lat, lon in bundle.settings['center_zone']])
    if isinstance(around, CenterBypassRoads) and not roads.failed:
        started = time.perf_counter()
        around.ensure(points)
        n_points, n_nodes = around.bypass.size
        print(f'Объезд малого центра: в кэше точек {n_points}, узлов {n_nodes}; {time.perf_counter() - started:.0f} с')
    if roads.terrain:   # рельеф (№85): подъёмы точек плана — заранее (кусками с записью кэша), фону сервера — только новые
        for name, r in (('графа', roads), ('объезда центра', around if isinstance(around, CenterBypassRoads) else None)):
            if r is None:
                continue
            started = time.perf_counter()
            r.ensure_climb(points, wait=True)
            done = (r.bypass if isinstance(r, CenterBypassRoads) else r)._climbs
            print(f'Подъёмы {name}: узлов {len(done.nodes)}; {time.perf_counter() - started:.0f} с')


def _dem(path: str, force: bool = False, download: bool = True) -> int:
    """Рельеф (№85): тайлы SRTM для узлов графа карты — в terrain.dem_dir() (скачанные не трогаются; download False — только
    уже скачанные), затем кэш высот узлов <карта>.elev.npz. Кэш годится (тот же граф, параметры и тайлы) — не
    пересобирается (force — пересобрать)."""
    version = map_signature(path)
    if version is None or not roads_supported():
        print(f'Карты нет или нет numpy/scipy: {path}')
        return 1
    graph = load_graph(path, version)
    names = terrain.tiles_for(graph.lat, graph.lon)
    started = time.perf_counter()
    got = terrain.download_tiles(names) if download else []
    missing = [n for n in names if not os.path.exists(terrain.tile_path(n))]
    print(f'Тайлов высот {len(names)}: скачано сейчас {len(got)}, нет {len(missing)} {missing or ""}; '
          f'{time.perf_counter() - started:.0f} с')
    key, tiles = terrain.elev_key(graph.identity), terrain.tiles_key(names)
    elev_path = _cache_path(path, 'elev')
    if not force and terrain.stored_elevation(elev_path) == (key, tiles):
        print(f'Кэш высот годится: {elev_path}')
        return 0
    z = terrain.build_elevation(graph.lat, graph.lon)
    terrain.save_elevation(elev_path, key, tiles, z)
    print(f'Высоты узлов: {len(z)}, без высоты {int(np.sum(~np.isfinite(z)))} → {elev_path}')
    return 0


def _refresh_elevation(path: str) -> None:
    """Кэш высот есть, но от прежней карты (или параметров DEM) — пересобрать из уже скачанных тайлов (без сети): иначе после
    обновления карты рельефа нет до ручной команды dem. Кэша высот нет — рельеф не включён, ничего не делается."""
    version = map_signature(path)
    if version is None or not roads_supported() or terrain.stored_elevation(_cache_path(path, 'elev')) is None:
        return
    identity = RoadGraph.stored_identity(_cache_path(path, 'graph'), version)
    if identity is not None and elevation_loader(path, version)() is not None:
        return
    try:
        _dem(path, download=False)
    except Exception:   # рельеф — необязательный: обновление идёт дальше без него
        logger.exception('[Routes] Кэш высот не пересобран — без рельефа до команды dem')


def main(argv: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:   # ROUTES_OSM_PATH, ROUTES_DB_PATH — из .env сервера, как у app_v2: карта и база те же, что у сервера
        from dotenv import load_dotenv
        load_dotenv(os.path.join(REPO_ROOT, '.env'))
    except ImportError:
        pass
    command = argv[0] if argv else ''
    path = osm_path()
    if command == 'download':
        _download(path, argv[1] if len(argv) > 1 else DOWNLOAD_URL)
    elif command == 'build':
        version = map_signature(path)
        if version is None:
            print(f'Карты нет: {path}')
            return 1
        graph = load_graph(path, version, rebuild=True)
        RoadNetwork(graph)   # проверка: связность и KD-дерево строятся
        print(f'Граф: узлов {graph.n_nodes}, рёбер {len(graph.src)} → {_cache_path(path, "graph")}')
    elif command == 'dem':
        code = _dem(path, '--force' in argv)
        if code == 0:
            try:   # подъёмы точек плана — сразу (читает ERP); не вышло — сервер досчитает их в фоне
                _warm(path)
            except (Exception, SystemExit) as e:
                print(f'Подъёмы точек плана не прогреты ({e}) — сервер досчитает их в фоне')
        return code
    elif command == 'warm':
        if '--if-stale' in argv:
            _refresh_elevation(path)
            zone: list[Point] = []
            if map_signature(path) is not None and roads_supported():   # греть нечего — базу и не читаем
                try:   # граница малого центра — из копии базы: сама не меняется
                    zone = [(lat, lon) for lat, lon in _load_bundle_readonly(_db_path()).settings['center_zone']]
                except Exception:   # копия не читается (битая база) — проверка без объезда, как до него
                    logger.warning('[Routes] Граница малого центра не прочитана из базы маршрутов — проверяется только '
                                   'кэш расстояний', exc_info=True)
            if not _dist_cache_stale(path, zone):
                print('Кэш расстояний годится — пересчёт не нужен' if map_signature(path) else f'Карты нет: {path}')
                return 0
        _warm(path)
    else:
        print('Команды: download [url] | build | warm [--if-stale] | dem [--force]')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

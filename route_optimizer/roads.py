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
  (по прямой × извилистость).

Команды:  python -m route_optimizer.roads download | build | warm [--if-stale]
(warm --if-stale — только если кэш расстояний не годится нынешнему графу и формату: задача обновления сервера)
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
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from .geo import EARTH_RADIUS_KM, Point, haversine_km

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

    def lines(self, lines: Sequence[Sequence[Point]]) -> list[list[Point]]:
        """Ломаные вдоль дорог для карты: каждая линия (точки по порядку объезда) → точки по кратчайшему
        пути A → B между соседними точками, упрощённые до PATH_SIMPLIFY_KM. Участок, где точка не
        привязана или пути нет, — по прямой. Поиск — Dijkstra от каждого узла-начала с пределом
        PATH_LIMIT_FACTOR × по прямой (не нашёлся — без предела)."""
        points = list({point_key(p) for line in lines for p in line})
        snapped, _ = self.snap(points)
        node = {p: int(n) for p, n in zip(points, snapped)}
        legs: dict[int, dict[int, float]] = {}   # узел-начало → узел-конец → км по прямой
        for line in lines:
            for a, b in zip(line, line[1:]):
                na, nb = node[point_key(a)], node[point_key(b)]
                if na >= 0 and nb >= 0 and na != nb:
                    legs.setdefault(na, {})[nb] = haversine_km(a, b)
        paths: dict[tuple[int, int], Any] = {}
        for na, targets in legs.items():
            limit = PATH_LIMIT_FACTOR * max(targets.values()) + PATH_LIMIT_SLACK_KM
            dist, pred = dijkstra(self._g, directed=True, indices=na, return_predecessors=True, limit=limit)
            if not all(math.isfinite(dist[nb]) for nb in targets):
                dist, pred = dijkstra(self._g, directed=True, indices=na, return_predecessors=True)
            for nb in targets:
                if math.isfinite(dist[nb]):
                    paths[na, nb] = _walk_back(pred, na, nb)
        out: list[list[Point]] = []
        for line in lines:
            if not line:
                out.append([])
                continue
            lat: list[Any] = [np.array([line[0][0]])]
            lon: list[Any] = [np.array([line[0][1]])]
            for a, b in zip(line, line[1:]):
                path = paths.get((node[point_key(a)], node[point_key(b)]))
                if path is not None:
                    lat.append(self.graph.lat[path])
                    lon.append(self.graph.lon[path])
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


class RoadDistances:
    """Км между точками по дорогам. ensure(точки) — один расчёт на весь набор (первый раз — минуты,
    дальше кэш), km(a, b) — O(1). Потокобезопасно: дозаполнение под lock, чтение — без него.

    Сбой графа (битая карта, нет osmium) — failed = True: km() → None для всех точек.

    Файл кэша годится, только если он посчитан на том же графе: graph_identity — отпечаток графа
    без его загрузки (None — проверить нечем, файл не читается)."""

    km_source = 'osm'   # откуда км (тот же интерфейс, что у valhalla_engine.ValhallaRoads)

    def __init__(self, version: str, load_network: Callable[[], RoadNetwork],
                 cache_path: str | None = None,
                 graph_identity: Callable[[], str | None] | None = None, map_path: str | None = None):
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

    @classmethod
    def for_map(cls, path: str, version: str, cache_name: str = 'dist') -> RoadDistances:
        graph_path = _cache_path(path, 'graph')
        return cls(version, lambda: RoadNetwork(load_graph(path, version)),
                   _cache_path(path, cache_name),
                   lambda: RoadGraph.stored_identity(graph_path, version), path)

    @classmethod
    def for_graph(cls, graph: RoadGraph, version: str = 'memory',
                  cache_path: str | None = None) -> RoadDistances:
        """Готовый граф (тесты и разовые расчёты); cache_path=None — без файла кэша."""
        return cls(version, lambda: RoadNetwork(graph), cache_path, lambda: graph.identity)

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


class RoadProvider:
    """Дороги для расчёта: карта есть — RoadDistances (пересоздаётся при смене файла карты; не
    собралась — с failed = True: по прямой с предупреждением roads_failed), нет — None.
    Проверка карты — os.stat на каждый вызов: подложенная карта подхватывается без перезапуска."""

    def __init__(self, path: str | None = None):
        self.path = path or osm_path()
        self._lock = threading.Lock()
        self._current: RoadDistances | None = None

    def get(self) -> RoadDistances | None:
        version = map_signature(self.path)
        if version is None or not roads_supported():
            return None
        with self._lock:
            if self._current is None or self._current.version != version:
                self._current = RoadDistances.for_map(self.path, version)
            return self._current


def open_roads(path: str | None = None, cache_name: str = 'dist') -> RoadDistances | None:
    """RoadDistances по карте (для скриптов); карты или numpy/scipy нет — None."""
    path = path or osm_path()
    version = map_signature(path)
    if version is None or not roads_supported():
        return None
    return RoadDistances.for_map(path, version, cache_name)


def roads_version(roads: RoadDistances | None) -> str:
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


def _dist_cache_stale(path: str) -> bool:
    """Кэш расстояний карты path не подходит к нынешнему графу и формату (сменился DIST_FORMAT, как 2 → 3, или карта):
    первый расчёт после обновления пересчитал бы его целиком (~2 мин под блокировкой дорог). Проверка — без ERP и без
    загрузки графа; карты или numpy/scipy нет — греть нечего (False)."""
    version = map_signature(path)
    if version is None or not roads_supported():
        return False
    identity = RoadGraph.stored_identity(_cache_path(path, 'graph'), version)
    if identity is None:
        return True   # графа в кэше нет или он от другой карты — warm соберёт и его
    try:
        with open(_cache_path(path, 'dist'), 'rb') as f, np.load(f, allow_pickle=False) as z:
            return 'key' not in z.files or str(z['key']) != _dist_key(identity)
    except Exception:   # файла нет или он битый
        return True


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
    """Матрица для всех точек текущего плана: снимок ERP (только чтение) + настройки маршрутов (из копии базы)."""
    from .valhalla_engine import ENGINE_ENV, ENGINE_OSM

    sys.path.insert(0, REPO_ROOT)
    os.environ[ENGINE_ENV] = ENGINE_OSM   # import app_v2 вызывает init_app: фон Valhalla этой команде не нужен
    import app_v2  # noqa: F401 — только строка подключения к ERP; сервер не запускается

    from . import evaluate
    from .snapshot import load_snapshot

    roads = open_roads(path)
    if roads is None:
        raise SystemExit('Карты нет — сначала: python -m route_optimizer.roads download')
    db_path = os.environ.get('ROUTES_DB_PATH') or os.path.join(REPO_ROOT, 'route_optimizer.db')
    snap = load_snapshot(app_v2.db.connection_string)
    bundle = _load_bundle_readonly(db_path)
    points = evaluate.plan_points(snap, bundle, {})
    started = time.perf_counter()
    roads.ensure(points)
    n_points, n_nodes = roads.size
    print(f'Точек плана {len(set(map(point_key, points)))}, не привязано {roads.unsnapped(points)}; '
          f'в кэше точек {n_points}, узлов {n_nodes}; {time.perf_counter() - started:.0f} с')


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
    elif command == 'warm':
        if '--if-stale' in argv and not _dist_cache_stale(path):
            print('Кэш расстояний годится — пересчёт не нужен' if map_signature(path) else f'Карты нет: {path}')
            return 0
        _warm(path)
    else:
        print('Команды: download [url] | build | warm [--if-stale]')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

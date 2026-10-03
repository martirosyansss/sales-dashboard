# -*- coding: utf-8 -*-
"""Дороги через локальный движок Valhalla (план learning-loop, этап 2; решение владельца №46): тайлы из карты
.osm.pbf, направленные матрицы км и минут — профиль auto (машины менеджеров) и truck (грузовики развоза).

Без Flask и без БД. pyvalhalla (pip install pyvalhalla==3.9.0; есть wheel win_amd64 — без Docker, Java и WSL)
необязателен: нет пакета, тайлов или движок выключен — дороги считает roads.py (граф OSM), а пары, которых нет и
там, — по прямой × извилистость (evaluate.Norms.km).

- режим: env ROUTES_ROAD_ENGINE (по умолчанию DEFAULT_ENGINE — по сверке с GPS менеджеров, отчёт
  docs/research/07-valhalla-vs-roads.md):
    valhalla_time — минуты Valhalla, км — граф OSM (точнее по GPS: Valhalla ищет самый быстрый путь, а км
                    менеджеров ближе к кратчайшему); пары без км графа — км Valhalla;
    valhalla      — км и минуты Valhalla (пары без пути Valhalla — граф OSM);
    osm           — только граф OSM, минуты — км / скорость зоны (как до этапа 2);
- папка: env ROUTES_VALHALLA_DIR, по умолчанию %LOCALAPPDATA%/route_optimizer/valhalla (не в репозитории):
  tiles-<сборка>/ — тайлы и valhalla.json, current.json — действующая сборка, matrix-<сборка>-<профиль>.npz —
  кэш матриц;
- сборка (build_tiles): valhalla_build_admins + valhalla_build_tiles в новую папку, затем current.json — атомарно.
  Отпечаток сборки — карта (время изменения и размер), версия pyvalhalla и BUILD_FORMAT: такая сборка уже есть —
  ничего не делается (повторный запуск безопасен);
- привязка: Actor.locate; точка дальше SNAP_MAX_KM от дороги профиля — не привязана: её пары считает запасной путь;
- матрица: Actor.matrix (sources_to_targets, алгоритм timedistancematrix — Дейкстра от источника: на наших 1 500
  точках в ~15 раз быстрее CostMatrix, пути не длиннее, памяти мало) блоками ≤ BATCH источников × все цели в WORKERS
  потоках (у каждого потока свой Actor); ошибка блока — блок делится пополам вплоть до одной пары; пары без пути —
  NaN (запасной путь);
- км = привязка_A + путь Valhalla + привязка_B (как в roads.py); минуты — время Valhalla (свободный поток) ×
  поправка зоны TIME_FACTOR (город / область), подобранная по GPS менеджеров (отчёт 07). Пробки по часам —
  прежний GPS-профиль (traffic_validation.TrafficProfile) поверх этих минут;
- грузовик: размеры и вес машин в настройках не заданы (тоннаж — это груз, а не полная масса), поэтому
  TRUCK_COSTING пуст — умолчания Valhalla (21,77 т, 4,11 × 2,6 × 21,64 м): дороги с ограничением массы или
  высоты ниже этих чисел грузовик объезжает — оценка осторожная.

Команды:  python -m route_optimizer.valhalla_engine build [--force] | warm | status
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from .geo import Point, haversine_km
from .roads import KEY_DECIMALS, REPO_ROOT, SNAP_MAX_KM, map_signature, np, osm_path, point_key

if TYPE_CHECKING:
    from .roads import RoadDistances

logger = logging.getLogger(__name__)

ENGINE_ENV = 'ROUTES_ROAD_ENGINE'
DIR_ENV = 'ROUTES_VALHALLA_DIR'
ENGINE_OSM = 'osm'
ENGINE_VALHALLA = 'valhalla'
ENGINE_VALHALLA_TIME = 'valhalla_time'
ENGINES = (ENGINE_OSM, ENGINE_VALHALLA, ENGINE_VALHALLA_TIME)
DEFAULT_ENGINE = ENGINE_VALHALLA_TIME

PROFILE_CAR = 'auto'      # машины менеджеров
PROFILE_TRUCK = 'truck'   # грузовики развоза
TRUCK_COSTING: dict[str, Any] = {}   # умолчания Valhalla (см. шапку модуля)
COSTING = {PROFILE_CAR: {}, PROFILE_TRUCK: TRUCK_COSTING}
# Поправка ко времени Valhalla (свободный поток) по зоне участка: True — оба конца в городе. Σ минут движения по
# GPS / Σ минут Valhalla на переездах менеджеров между визитами, обучающие недели 21.08–24.09.2026 (1 773
# переезда; проверка на 25.09–01.10 — отчёт 07, скрипты — docs/research/valhalla). Грузовики — та же поправка
# (треков грузовиков пока нет: этап 3 плана).
TIME_FACTOR: dict[bool, float] = {True: 1.32, False: 1.18}

BUILD_FORMAT = 1          # правила сборки (config): поменялись — тайлы пересобираются
BUILD_ATTEMPTS = 3        # valhalla_build_tiles 3.9 под Windows изредка падает (0xC0000005) — повтор
MATRIX_FORMAT = 1
BATCH = 25                # источников в одном запросе матрицы (цели — все)
WORKERS = max(1, min(4, (os.cpu_count() or 2) - 1))   # потоков расчёта матрицы
LOCATE_BATCH = 200
CACHE_MB = 256            # кэш тайлов Actor в памяти (тайлы Армении — ~65 МБ)


def valhalla_module() -> Any | None:
    """Модуль pyvalhalla или None (не установлен, не загрузились DLL)."""
    try:
        import valhalla
    except (ImportError, OSError):
        return None
    return valhalla


def valhalla_supported() -> bool:
    return np is not None and valhalla_module() is not None


def engine_mode() -> str:
    """Режим дорог: env ROUTES_ROAD_ENGINE (по умолчанию DEFAULT_ENGINE); неизвестное значение — osm."""
    mode = (os.environ.get(ENGINE_ENV) or DEFAULT_ENGINE).strip().lower()
    if mode not in ENGINES:
        logger.warning('[Routes] %s=%r неизвестен — дороги по графу OSM', ENGINE_ENV, mode)
        return ENGINE_OSM
    return mode


def engine_enabled() -> bool:
    return engine_mode() != ENGINE_OSM


def base_dir() -> str:
    """Папка тайлов и кэшей: env ROUTES_VALHALLA_DIR, иначе %LOCALAPPDATA%/route_optimizer/valhalla."""
    env = os.environ.get(DIR_ENV)
    if env:
        return env
    root = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(root, 'route_optimizer', 'valhalla')


# --- Сборка тайлов ---

@dataclass(frozen=True)
class Build:
    """Действующая сборка: id — отпечаток (карта, pyvalhalla, правила), name — папка тайлов (уникальна для
    каждой сборки: версия кэшей матриц), config — valhalla.json сборки."""
    id: str
    name: str
    config: str
    info: dict[str, Any]


def build_id(pbf_signature: str, valhalla_version: str) -> str:
    raw = f'{pbf_signature}|{valhalla_version}|b{BUILD_FORMAT}'
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]


def current_build(base: str | None = None) -> Build | None:
    """Сборка из current.json; нет файла, папки тайлов или файл битый — None."""
    base = base or base_dir()
    try:
        with open(os.path.join(base, 'current.json'), encoding='utf-8') as f:
            info = json.load(f)
        name = str(info['name'])
        config = os.path.join(base, name, 'valhalla.json')
        if not os.path.isfile(config):
            return None
        return Build(str(info['id']), name, config, info)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _config(tiles: str) -> dict[str, Any]:
    """valhalla.json: умолчания pyvalhalla + тайлы в папке tiles, admin.sqlite рядом, без логов, сборка в один
    поток (воспроизводимо; дольше на секунды), матрица — timedistancematrix, лимиты матриц на всю Армению; резерв
    меток поиска уменьшен (с умолчанием — миллионы меток заранее на каждый Actor и каждую точку — CostMatrix на
    сотню точек падала с bad allocation; дальше массивы растут сами)."""
    config = valhalla_module().get_config(tile_extract='', tile_dir=tiles)
    mj = config['mjolnir']
    mj.update(admin=os.path.join(tiles, 'admin.sqlite'), timezone=os.path.join(tiles, 'tz_world.sqlite'),
              traffic_extract='', landmarks='', max_cache_size=CACHE_MB * 1024 * 1024, concurrency=1)
    config['thor'].update(source_to_target_algorithm='timedistancematrix',
                          max_reserved_labels_count_bidir_dijkstras=20000,
                          max_reserved_labels_count_dijkstras=40000, clear_reserved_memory=True)
    for profile in COSTING:
        config['service_limits'][profile].update(max_matrix_location_pairs=10 ** 7,
                                                 max_matrix_distance=1_000_000.0)
    return config


def _write_json(path: str, data: Any) -> None:
    """Атомарная запись JSON (свой временный файл в той же папке, затем os.replace)."""
    fd, tmp = tempfile.mkstemp(suffix='.json', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _run(tool: str, config: str, pbf: str) -> None:
    """Исполняемый файл pyvalhalla через python -m valhalla (он сам подключает DLL пакета)."""
    proc = subprocess.run([sys.executable, '-m', 'valhalla', tool, '-c', config, pbf],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    if proc.returncode != 0:
        raise RuntimeError(f'{tool}: код {proc.returncode}: {(proc.stderr or proc.stdout)[-2000:]}')


def build_tiles(pbf: str | None = None, base: str | None = None, force: bool = False) -> Build:
    """Тайлы Valhalla из карты pbf в base; та же сборка уже действует (и не force) — она и возвращается.
    Прежние сборки и их кэши удаляются, если не заняты (сервер их ещё читает — останутся до следующей сборки)."""
    pbf = pbf or osm_path()
    base = base or base_dir()
    module = valhalla_module()
    if module is None:
        raise RuntimeError('pyvalhalla не установлен: pip install pyvalhalla==3.9.0')
    signature = map_signature(pbf)
    if signature is None:
        raise FileNotFoundError(f'Карты нет: {pbf}')
    bid = build_id(signature, module.__version__)
    current = current_build(base)
    if current is not None and current.id == bid and not force:
        return current
    os.makedirs(base, exist_ok=True)
    started = time.perf_counter()
    for attempt in range(1, BUILD_ATTEMPTS + 1):
        tiles = tempfile.mkdtemp(prefix=f'tiles-{bid}-{datetime.now():%Y%m%d%H%M%S}-', dir=base)   # имя уникально
        name = os.path.basename(tiles)
        config_path = os.path.join(tiles, 'valhalla.json')
        try:
            _write_json(config_path, _config(tiles))
            _run('valhalla_build_admins', config_path, pbf)
            _run('valhalla_build_tiles', config_path, pbf)
            break
        except RuntimeError:
            shutil.rmtree(tiles, ignore_errors=True)
            if attempt == BUILD_ATTEMPTS:
                raise
            logger.warning('[Routes] Valhalla: сборка тайлов упала (попытка %d) — повтор', attempt, exc_info=True)
        except BaseException:
            shutil.rmtree(tiles, ignore_errors=True)
            raise
    info = {'id': bid, 'name': name, 'pbf': os.path.abspath(pbf), 'pbf_signature': signature,
            'valhalla': module.__version__, 'format': BUILD_FORMAT,
            'built_at': datetime.now().isoformat(timespec='seconds'),
            'seconds': round(time.perf_counter() - started, 1)}
    _write_json(os.path.join(base, 'current.json'), info)
    logger.info('[Routes] Valhalla: тайлы собраны за %.0f с → %s', info['seconds'], tiles)
    _cleanup(base, name)
    return Build(bid, name, config_path, info)


def _cleanup(base: str, keep: str) -> None:
    """Удалить прежние сборки и кэши их матриц (что занято — остаётся)."""
    for entry in os.listdir(base):
        path = os.path.join(base, entry)
        if entry.startswith('tiles-') and entry != keep:
            shutil.rmtree(path, ignore_errors=True)
        elif entry.startswith('matrix-') and not entry.startswith(f'matrix-{keep}-'):
            try:
                os.remove(path)
            except OSError:
                pass


# --- Движок и матрицы ---

class _Engine:
    """Сборка и Actor на каждый поток (потокобезопасность одного Actor не обещана): создаётся при первом запросе
    потока; потоки расчёта матрицы живут только на время расчёта — их Actor уходят вместе с ними."""

    def __init__(self, build: Build):
        self.build = build
        self._local = threading.local()

    def actor(self) -> Any:
        actor = getattr(self._local, 'actor', None)
        if actor is None:
            actor = self._local.actor = valhalla_module().Actor(self.build.config)
        return actor


@dataclass(frozen=True)
class _Table:
    """Неизменяемое состояние кэша профиля: чтение без блокировки, дозаполнение — заменой целиком."""
    index: dict[Point, int]   # ключ точки → строка и столбец матриц
    snap: Any                 # np.float64 [k]: км до дороги (inf — не привязана)
    km: Any                   # np.float32 [k × k]: км по дорогам строка → столбец, без привязки; NaN — пути нет
    minutes: Any              # np.float32 [k × k]: минуты Valhalla, без поправки зоны


def _empty_table() -> _Table:
    return _Table({}, np.zeros(0), np.zeros((0, 0), dtype=np.float32), np.zeros((0, 0), dtype=np.float32))


class _ProfileMatrix:
    """Направленные км и минуты профиля между всеми точками, что встречались (дозаполнение для новых точек).
    Кэш — matrix-<сборка>-<профиль>.npz в папке движка. Сбой движка — failed: дальше только запасной путь."""

    def __init__(self, engine: _Engine, profile: str, cache_path: str | None):
        self.engine = engine
        self.profile = profile
        self._cache_path = cache_path
        self._cache_read = cache_path is None
        self._lock = threading.Lock()
        self.table = _empty_table()
        self.failed = False

    @property
    def key(self) -> str:
        costing = json.dumps(COSTING[self.profile], sort_keys=True)
        return (f'{self.engine.build.name}|{self.profile}|{costing}|key{KEY_DECIMALS}|snap{SNAP_MAX_KM:g}'
                f'|m{MATRIX_FORMAT}')

    def ensure(self, points: Iterable[Point | None]) -> None:
        keys = {point_key(p) for p in points if p is not None}
        if self.failed or keys.issubset(self.table.index):
            return
        with self._lock:
            if self.failed:
                return
            try:
                if not self._cache_read:
                    self._read_cache()
                new = sorted(k for k in keys if k not in self.table.index)
                if new:
                    self._extend(new)
                    self._write_cache()
            except Exception:
                logger.exception('[Routes] Valhalla: матрица %s не посчитана — дальше запасной путь',
                                 self.profile)
                self.failed = True

    # -- расчёт --

    def _request(self, kind: str, body: dict[str, Any]) -> Any:
        return getattr(self.engine.actor(), kind)(body)

    def _locate(self, points: Sequence[Point]) -> Any:
        """Км от точки до дороги профиля (inf — дороги рядом нет)."""
        out = np.full(len(points), np.inf)
        for k in range(0, len(points), LOCATE_BATCH):
            chunk = points[k:k + LOCATE_BATCH]
            res = self._request('locate', {'locations': [{'lat': p[0], 'lon': p[1]} for p in chunk],
                                           'costing': self.profile, 'verbose': False})
            for i, (p, item) in enumerate(zip(chunk, res)):
                edges = (item or {}).get('edges') or []
                if edges:
                    e = edges[0]
                    out[k + i] = haversine_km(p, (float(e['correlated_lat']), float(e['correlated_lon'])))
        return out

    def _block(self, src: Sequence[Point], dst: Sequence[Point]) -> tuple[Any, Any]:
        """(км, минуты) [len(src) × len(dst)]; ошибка запроса — делим пополам до пары, пара с ошибкой — NaN."""
        km = np.full((len(src), len(dst)), np.nan, dtype=np.float32)
        minutes = np.full((len(src), len(dst)), np.nan, dtype=np.float32)
        try:
            body = {'sources': [{'lat': p[0], 'lon': p[1]} for p in src],
                    'targets': [{'lat': p[0], 'lon': p[1]} for p in dst],
                    'costing': self.profile, 'verbose': False}
            if COSTING[self.profile]:
                body['costing_options'] = {self.profile: COSTING[self.profile]}
            res = self._request('matrix', body)['sources_to_targets']
            for i, (drow, trow) in enumerate(zip(res['distances'], res['durations'])):
                for j, (d, t) in enumerate(zip(drow, trow)):
                    if d is not None and t is not None:
                        km[i, j] = d
                        minutes[i, j] = t / 60.0
            return km, minutes
        except Exception as exc:   # ValhallaError, RuntimeError: «unconnected regions» и пр.
            if len(src) > 1:
                h = len(src) // 2
                (k1, m1), (k2, m2) = self._block(src[:h], dst), self._block(src[h:], dst)
                return np.vstack([k1, k2]), np.vstack([m1, m2])
            if len(dst) > 1:
                h = len(dst) // 2
                (k1, m1), (k2, m2) = self._block(src, dst[:h]), self._block(src, dst[h:])
                return np.hstack([k1, k2]), np.hstack([m1, m2])
            logger.debug('[Routes] Valhalla: пары %s → %s нет (%s)', src[0], dst[0], exc)
            return km, minutes

    def _rows(self, src: Sequence[Point], dst: Sequence[Point]) -> tuple[Any, Any]:
        """Матрица src × dst блоками по BATCH источников в WORKERS потоках."""
        if not src or not dst:
            return (np.full((len(src), len(dst)), np.nan, dtype=np.float32),
                    np.full((len(src), len(dst)), np.nan, dtype=np.float32))
        chunks = [src[k:k + BATCH] for k in range(0, len(src), BATCH)]
        if len(chunks) == 1:
            parts = [self._block(chunks[0], dst)]
        else:
            with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='valhalla') as pool:
                parts = list(pool.map(lambda chunk: self._block(chunk, dst), chunks))
        return np.vstack([p[0] for p in parts]), np.vstack([p[1] for p in parts])

    def _extend(self, new: Sequence[Point]) -> None:
        """Новые точки: привязка, затем пути новые → все и прежние → новые (только привязанные точки)."""
        started = time.perf_counter()
        t = self.table
        old = sorted(t.index, key=t.index.get)
        k_old, k = len(old), len(old) + len(new)
        snap = np.concatenate([t.snap, self._locate(new)])
        ok = snap <= SNAP_MAX_KM
        km = np.full((k, k), np.nan, dtype=np.float32)
        minutes = np.full((k, k), np.nan, dtype=np.float32)
        km[:k_old, :k_old], minutes[:k_old, :k_old] = t.km, t.minutes
        every = [*old, *new]
        live = [i for i in range(k) if ok[i]]
        live_new = [i for i in live if i >= k_old]
        live_old = [i for i in live if i < k_old]
        if live_new:
            rk, rm = self._rows([every[i] for i in live_new], [every[i] for i in live])
            km[np.ix_(live_new, live)], minutes[np.ix_(live_new, live)] = rk, rm
            if live_old:
                ck, cm = self._rows([every[i] for i in live_old], [every[i] for i in live_new])
                km[np.ix_(live_old, live_new)], minutes[np.ix_(live_old, live_new)] = ck, cm
        index = dict(t.index)
        for i, p in enumerate(new):
            index[p] = k_old + i
        self.table = _Table(index, snap, km, minutes)
        missing = int(np.isnan(km[np.ix_(live, live)]).sum())
        logger.info('[Routes] Valhalla %s: +%d точек (не привязано %d), всего %d, пар без пути %d; %.1f с',
                    self.profile, len(new), int(np.sum(~ok[k_old:])), k, missing, time.perf_counter() - started)

    # -- кэш --

    def _read_cache(self) -> None:
        self._cache_read = True
        path = self._cache_path
        if path is None or not os.path.exists(path):
            return
        try:
            with open(path, 'rb') as f, np.load(f, allow_pickle=False) as z:
                if str(z['key']) != self.key:
                    return
                index = {(float(a), float(b)): i for i, (a, b) in enumerate(zip(z['lat'], z['lon']))}
                table = _Table(index, z['snap'].astype(np.float64), z['km'].astype(np.float32),
                               z['minutes'].astype(np.float32))
        except Exception:   # битый файл, чужой формат
            logger.warning('[Routes] Valhalla: кэш %s не прочитан — пересчёт', os.path.basename(path),
                           exc_info=True)
            return
        self.table = table
        logger.info('[Routes] Valhalla %s: кэш — точек %d', self.profile, len(index))

    def _write_cache(self) -> None:
        from .roads import _save_npz
        if self._cache_path is None:
            return
        t = self.table
        keys = sorted(t.index, key=t.index.get)
        try:
            _save_npz(self._cache_path, key=self.key,
                      lat=np.array([p[0] for p in keys], dtype=np.float64),
                      lon=np.array([p[1] for p in keys], dtype=np.float64),
                      snap=t.snap, km=t.km, minutes=t.minutes)
        except OSError:
            logger.exception('[Routes] Valhalla: кэш %s не записан', self._cache_path)


class ValhallaRoads:
    """Дороги Valhalla одного профиля — тот же интерфейс, что у roads.RoadDistances (ensure, km, minutes,
    unsnapped, lines, truck, version, failed). Пара без пути Valhalla (точка не привязана, нет пути, движок
    сломался) — fallback (граф OSM roads.py), его нет — None: участок по прямой × извилистость (Norms.km).
    time_only (режим valhalla_time) — км сначала по графу OSM, Valhalla — только минуты (и км, где графа нет)."""

    def __init__(self, matrices: dict[str, _ProfileMatrix], profile: str,
                 fallback: RoadDistances | None = None, time_only: bool = False):
        self._matrices = matrices
        self._m = matrices[profile]
        self.profile = profile
        self.fallback = fallback
        self.time_only = time_only

    @property
    def version(self) -> str:
        back = 'off' if self.fallback is None else self.fallback.version
        mode = ENGINE_VALHALLA_TIME if self.time_only else ENGINE_VALHALLA
        return f'{mode}:{self._m.engine.build.name}:{self.profile}|{back}'

    @property
    def failed(self) -> bool:
        return self._m.failed and (self.fallback is None or self.fallback.failed)

    @property
    def size(self) -> tuple[int, int]:
        n = len(self._m.table.index)
        return n, n

    def truck(self) -> ValhallaRoads:
        return ValhallaRoads(self._matrices, PROFILE_TRUCK, self.fallback, self.time_only)

    def ensure(self, points: Iterable[Point | None]) -> None:
        """Пути между всеми точками (первый раз — минуты, дальше кэш). Граф OSM — тоже всем набором, если км
        берутся из него (time_only) или движок сломался: иначе он досчитывал бы точки по одной."""
        points = list(points)
        self._m.ensure(points)
        if self.fallback is not None and (self.time_only or self._m.failed):
            self.fallback.ensure(points)

    def _pair(self, a: Point, b: Point) -> tuple[int, int] | None:
        t = self._m.table
        ia, ib = t.index.get(point_key(a)), t.index.get(point_key(b))
        if (ia is None or ib is None) and not self._m.failed:
            self._m.ensure([a, b])
            t = self._m.table
            ia, ib = t.index.get(point_key(a)), t.index.get(point_key(b))
        if ia is None or ib is None:
            return None
        return ia, ib

    def km(self, a: Point, b: Point) -> float | None:
        """Км A → B по дорогам: Valhalla, у кого пути нет — граф OSM (time_only — наоборот); нет обоих — None."""
        if self.time_only and self.fallback is not None:
            d = self.fallback.km(a, b)
            if d is not None:
                return d
        d = self._valhalla_km(a, b)
        if d is not None or self.time_only or self.fallback is None:
            return d
        return self.fallback.km(a, b)

    def _valhalla_km(self, a: Point, b: Point) -> float | None:
        pair = self._pair(a, b)
        if pair is None:
            return None
        t = self._m.table
        ia, ib = pair
        if ia == ib:
            return haversine_km(a, b)
        d = t.km.item(ia, ib)
        return t.snap.item(ia) + d + t.snap.item(ib) if math.isfinite(d) else None

    def minutes(self, a: Point, b: Point, city: bool) -> float | None:
        """Минуты езды A → B: время Valhalla × поправка зоны (city — оба конца в городе); нет — None."""
        pair = self._pair(a, b)
        if pair is None:
            return None
        ia, ib = pair
        if ia == ib:
            return None
        m = self._m.table.minutes.item(ia, ib)
        return m * TIME_FACTOR[city] if math.isfinite(m) else None

    def unsnapped(self, points: Iterable[Point | None]) -> int:
        """Сколько точек без км по дорогам: у time_only км — граф OSM, считает он."""
        if self.time_only and self.fallback is not None:
            return self.fallback.unsnapped(points)
        t = self._m.table
        keys = {point_key(p) for p in points if p is not None}
        return sum(1 for k in keys if k in t.index and not t.snap[t.index[k]] <= SNAP_MAX_KM)

    def lines(self, lines: Sequence[Sequence[Point]]) -> list[list[Point]] | None:
        """Линии на карте — по графу OSM (если есть)."""
        return self.fallback.lines(lines) if self.fallback is not None else None


class ValhallaProvider:
    """Valhalla для расчёта: режим не osm, pyvalhalla есть, карта есть и тайлы собраны из неё — ValhallaRoads (по
    профилю auto), иначе None (дороги — граф OSM). Карты нет — Valhalla тоже нет (как у RoadProvider). Тайлов этой
    карты (и этой версии pyvalhalla) нет — собираются при первом обращении (~20 с; сбой — None и запись в журнале,
    повтор — только после смены карты). Проверка карты и current.json — на каждый вызов: новая карта и собранные
    командой build тайлы подхватываются без перезапуска."""

    def __init__(self, base: str | None = None, pbf: str | None = None):
        self.base = base or base_dir()
        self.pbf = pbf or osm_path()
        self._lock = threading.Lock()
        self._matrices: dict[str, _ProfileMatrix] | None = None
        self._failed_for: str | None = None

    def get(self, fallback: RoadDistances | None = None) -> ValhallaRoads | None:
        if not engine_enabled() or not valhalla_supported():
            return None
        signature = map_signature(self.pbf)
        if signature is None:
            return None
        with self._lock:
            build = current_build(self.base)
            if build is None or build.id != build_id(signature, valhalla_module().__version__):
                if signature == self._failed_for:
                    return None
                try:
                    build = build_tiles(self.pbf, self.base)
                except Exception:
                    logger.exception('[Routes] Valhalla: тайлы не собраны — дороги по графу OSM')
                    self._failed_for = signature
                    return None
            if self._matrices is None or self._matrices[PROFILE_CAR].engine.build.name != build.name:
                engine = _Engine(build)
                self._matrices = {p: _ProfileMatrix(engine, p, os.path.join(self.base, f'matrix-{build.name}-{p}.npz'))
                                  for p in COSTING}
            return ValhallaRoads(self._matrices, PROFILE_CAR, fallback, engine_mode() == ENGINE_VALHALLA_TIME)


def road_model_id(roads: RoadDistances | ValhallaRoads | None) -> str:
    """Стабильный id действующей модели дорог — для выученных поправок времени (этап 4): поправка применяется,
    только если id тот же, что при обучении (Norms.roads; для развоза — Norms.for_trucks().roads).

    'straight' — по прямой × извилистость (дорог нет или они сломались);
    'osm-dijkstra:<версия карты>|r<правила>|d<формат кэша>' — граф OSM (и когда Valhalla сломался);
    '<valhalla|valhalla_time>:<отпечаток сборки>:<профиль>|tf<город>/<область>' — Valhalla. Отпечаток сборки — карта,
    версия pyvalhalla и правила: пересборка тех же тайлов id не меняет, новая карта или поправка TIME_FACTOR — меняют."""
    if roads is None or roads.failed:
        return 'straight'
    if isinstance(roads, ValhallaRoads):
        if not roads._m.failed:
            mode = ENGINE_VALHALLA_TIME if roads.time_only else ENGINE_VALHALLA
            return (f'{mode}:{roads._m.engine.build.id}:{roads.profile}'
                    f'|tf{TIME_FACTOR[True]:g}/{TIME_FACTOR[False]:g}')
        roads = roads.fallback
        if roads is None or roads.failed:
            return 'straight'
    from .roads import DIST_FORMAT, RULES_VERSION
    return f'osm-dijkstra:{roads.version}|r{RULES_VERSION}|d{DIST_FORMAT}'


def open_valhalla(fallback: RoadDistances | None = None, base: str | None = None,
                  time_only: bool = False) -> ValhallaRoads | None:
    """ValhallaRoads по действующей сборке (для скриптов; режима ROUTES_ROAD_ENGINE не требует)."""
    if not valhalla_supported():
        return None
    base = base or base_dir()
    build = current_build(base)
    if build is None:
        return None
    engine = _Engine(build)
    matrices = {p: _ProfileMatrix(engine, p, os.path.join(base, f'matrix-{build.name}-{p}.npz')) for p in COSTING}
    return ValhallaRoads(matrices, PROFILE_CAR, fallback, time_only)


# --- Команды ---

def _warm() -> None:
    """Матрицы обоих профилей для всех точек текущего плана: снимок ERP (только чтение) + настройки маршрутов."""
    sys.path.insert(0, REPO_ROOT)
    import app_v2  # noqa: F401 — только строка подключения к ERP; сервер не запускается

    from . import evaluate
    from .snapshot import load_snapshot
    from .store import Store

    roads = open_valhalla()
    if roads is None:
        raise SystemExit('Тайлов нет — сначала: python -m route_optimizer.valhalla_engine build')
    db_path = os.environ.get('ROUTES_DB_PATH') or os.path.join(REPO_ROOT, 'route_optimizer.db')
    snap = load_snapshot(app_v2.db.connection_string)
    bundle = Store(db_path).load()
    points = [*evaluate.plan_points(snap, bundle, {}), bundle.depot]
    for r in (roads, roads.truck()):
        started = time.perf_counter()
        r.ensure(points)
        print(f'{r.profile}: точек {r.size[0]}, не привязано {r.unsnapped(points)}; '
              f'{time.perf_counter() - started:.0f} с')


def main(argv: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:   # ROUTES_VALHALLA_DIR и пр. — из .env сервера, как у app_v2: тайлы там, где их ищет сервер
        from dotenv import load_dotenv
        load_dotenv(os.path.join(REPO_ROOT, '.env'))
    except ImportError:
        pass
    command = argv[0] if argv else ''
    if command == 'build':
        build = build_tiles(force='--force' in argv)
        print(json.dumps(build.info, ensure_ascii=False, indent=1))
    elif command == 'warm':
        _warm()
    elif command == 'status':
        build = current_build()
        print(json.dumps({'mode': engine_mode(), 'supported': valhalla_supported(), 'dir': base_dir(),
                          'build': build.info if build is not None else None}, ensure_ascii=False, indent=1))
    else:
        print('Команды: build [--force] | warm | status')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

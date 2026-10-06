# -*- coding: utf-8 -*-
"""Дороги через локальный движок Valhalla (план learning-loop, этап 2; решение владельца №46): тайлы из карты
.osm.pbf, направленные матрицы км и минут — профиль auto (машины менеджеров) и truck (грузовики развоза).

Без Flask и без БД. pyvalhalla (pip install pyvalhalla==3.9.0: wheel только для 64-битного Python ≥ 3.12; без Docker,
Java и WSL) необязателен: нет пакета, карты или тайлов — дороги считает roads.py (граф OSM), а пары, которых нет и
там, — по прямой × извилистость (evaluate.Norms.km).

Переключатели (env; читаются при каждом расчёте):
- ROUTES_ROAD_ENGINE (по умолчанию DEFAULT_ENGINE — по сверке с GPS менеджеров, отчёт
  docs/research/07-valhalla-vs-roads.md):
    valhalla_time — минуты машин менеджеров — Valhalla × поправка зоны, км — граф OSM (по GPS он точнее: Valhalla
                    ищет самый быстрый путь, а менеджеры ездят ближе к кратчайшему); пары без км графа — у машин
                    менеджеров км Valhalla, у грузовиков — по прямой × извилистость, как до этапа 2;
    valhalla      — км и минуты из Valhalla (пары без пути Valhalla — граф OSM);
    osm           — Valhalla не используется вовсе (ни сборки, ни фоновых потоков): км — граф OSM, минуты — км /
                    скорость зоны, как до этапа 2. Граф OSM при этом остаётся направленным (одностороннее движение,
                    roads.DIST_FORMAT 3) — к среднему «туда-обратно» откат не возвращает;
- ROUTES_TRUCK_TIME: минуты грузовиков развоза — model: прежняя модель (км / скорость зоны, часовой профиль пробок);
  valhalla: время Valhalla-грузовика × поправка зоны. Обычно не задаётся: «Развоз» берёт модель, которую выбрало
  обучение по трекам водителей (learning.fit_truck_time — обе модели на одних и тех же участках, truck_leg_minutes; до
  выбора — model). Задана — главнее выбора обучения (truck_time_source: env > выбор обучения > model). Обзор и календарь
  менеджеров выбора обучения не получают, как и других выученных норм: у них ROUTES_TRUCK_TIME или model;
- ROUTES_VALHALLA_DIR — папка тайлов и кэша; по умолчанию %PROGRAMDATA%/route_optimizer/valhalla (её видят и служба
  SYSTEM, и пользователи), вне Windows — ~/.cache/route_optimizer/valhalla.

Сборка тайлов (build_tiles): valhalla_build_admins + valhalla_build_tiles (исполняемые файлы пакета, с тайм-аутом) в
новую папку tiles-<сборка>-<время>-<случайное>, в ней — метка build.ok, затем current.json (атомарно). Отпечаток
сборки — содержимое карты (sha256, а не время изменения: та же карта на сервере — та же сборка), версия pyvalhalla и
BUILD_FORMAT: такая сборка уже есть — ничего не делается. Сборка, публикация и уборка — под файлом-блокировкой
build.lock (процессы сервера и команды build не мешают друг другу); убираются только папки и кэши по шаблону имени —
завершённые прежние сборки и брошенные недостроенные (старше LOCK_STALE_S). Блокировка — средствами ОС: умер
процесс — она снята сама.

Матрицы: Actor.locate (точка дальше SNAP_MAX_KM от дороги профиля — не привязана: её пары считает запасной путь),
Actor.matrix (sources_to_targets, timedistancematrix — Дейкстра от источника: на наших 1 500 точках в ~15 раз быстрее
CostMatrix, пути не длиннее, памяти мало). Блоки — BATCH точек меньшей стороны × вся другая сторона, в WORKERS потоках
с общим пулом Actor (≤ WORKERS на сборку). Ошибка запроса — блок делится пополам до одной точки, и если не идёт и она —
расчёт прерывается (без лавины запросов): пары без пути — это данные, а ошибки движка — нет. Расширение, где ошибка
движка или больше MAX_MISSING_SHARE пар без пути / точек без дороги, не принимается и на диск не пишется: профиль
выключен до перезапуска, дальше — граф OSM. Кэш — matrix-<сборка>-<профиль>-<стоимость>.npz; точек больше MAX_POINTS —
матрица считается заново только для нужных.

Сервер (ValhallaProvider): тайлы и матрицы готовит один фоновый поток (старт — init_app; точки — из запросов). Запрос
тяжёлой работы не ждёт: пока матрицы для его точек не готовы, расчёт идёт по графу OSM; готовы — версия дорог в ключе
кэша оценки меняется, и оценка пересчитывается уже с Valhalla. Каждый расчёт получает неизменяемый срез матриц
(ValhallaRoads): км, минуты и road_model_id в одном расчёте — из одной модели.

- км = привязка_A + путь Valhalla + привязка_B (как в roads.py); минуты — время Valhalla (свободный поток) ×
  TIME_FACTOR зоны; часовой GPS-профиль пробок (traffic_validation.TrafficProfile) — поверх, как и раньше;
- грузовик (truck_costing): масса и размеры машин в настройках не заданы, есть тоннаж — это груз. Полная масса ≈
  тоннаж × TRUCK_GROSS_PER_PAYLOAD (лёгкие грузовики 2–5 т: собственная масса ≈ грузу) в пределах TRUCK_GROSS_T;
  размеры — лёгкого грузовика (TRUCK_DIMS_M). Один профиль на парк — по самой грузоподъёмной активной машине
  (осторожно: дороги, куда нельзя ей, объезжают все).

Команды:  python -m route_optimizer.valhalla_engine build [--force] [--max-seconds N] | warm | status
(код выхода build: 3 — тайлы собирает другой процесс, 4 — нет pyvalhalla или карты)
"""
from __future__ import annotations

import atexit
import copy
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Sequence

from .geo import Point, haversine_km, in_city
from .roads import KEY_DECIMALS, REPO_ROOT, SNAP_MAX_KM, CenterBypassRoads, map_signature, np, osm_path, point_key

if TYPE_CHECKING:
    from .evaluate import Norms
    from .roads import RoadDistances

logger = logging.getLogger(__name__)

ENGINE_ENV = 'ROUTES_ROAD_ENGINE'
TRUCK_TIME_ENV = 'ROUTES_TRUCK_TIME'
DIR_ENV = 'ROUTES_VALHALLA_DIR'
ENGINE_OSM = 'osm'
ENGINE_VALHALLA = 'valhalla'
ENGINE_VALHALLA_TIME = 'valhalla_time'
ENGINES = (ENGINE_OSM, ENGINE_VALHALLA, ENGINE_VALHALLA_TIME)
DEFAULT_ENGINE = ENGINE_VALHALLA_TIME
TRUCK_TIME_MODEL = 'model'
TRUCK_TIME_VALHALLA = 'valhalla'
DEFAULT_TRUCK_TIME = TRUCK_TIME_MODEL

PROFILE_CAR = 'auto'      # машины менеджеров
PROFILE_TRUCK = 'truck'   # грузовики развоза
CAR_COSTING: dict[str, float] = {}
TRUCK_GROSS_PER_PAYLOAD = 2.0                 # полная масса ≈ тоннаж (груз) × 2 — лёгкие грузовики 2–5 т
TRUCK_GROSS_T = (3.5, 26.0)                   # пределы полной массы, т
TRUCK_DIMS_M = {'height': 3.2, 'width': 2.4, 'length': 7.0}   # лёгкий грузовик, м
DEFAULT_TRUCK_PAYLOAD_KG = 5000.0             # машин с тоннажем нет — как у самой большой машины парка
# Поправка ко времени Valhalla (свободный поток) по зоне участка: True — оба конца в городе. Σ минут движения по
# GPS / Σ минут Valhalla на переездах менеджеров между визитами, обучающие недели 21.08–24.09.2026 (1 773
# переезда; проверка на 25.09–01.10 — отчёт 07, скрипты — docs/research/valhalla). Грузовикам — когда их минуты из
# Valhalla (ROUTES_TRUCK_TIME=valhalla или выбор обучения); поправку по часам к ним обучение подбирает по трекам
# водителей отдельно (learning, вид travel той же дорожной модели).
TIME_FACTOR: dict[bool, float] = {True: 1.32, False: 1.18}

BUILD_FORMAT = 2          # правила сборки (config, метка): поменялись — тайлы пересобираются
BUILD_ATTEMPTS = 3        # valhalla_build_tiles 3.9 под Windows изредка падает (0xC0000005) — повтор
BUILD_TIMEOUT_S = 900.0   # на один исполняемый файл сборки (Армения — 10–60 с)
LOCK_NAME = 'build.lock'
LOCK_STALE_S = BUILD_TIMEOUT_S + 300.0   # недостроенную папку тайлов не меняли дольше — её сборщик умер
MARKER = 'build.ok'       # метка завершённой сборки в папке тайлов
MATRIX_FORMAT = 2
BATCH = 25                # точек меньшей стороны в одном запросе матрицы (другая сторона — вся)
WORKERS = max(1, min(4, (os.cpu_count() or 2) - 1))   # потоков расчёта и Actor на сборку
LOCATE_BATCH = 200
CACHE_MB = 256            # кэш тайлов одного Actor в памяти (тайлы Армении — ~65 МБ)
MAX_POINTS = 4000         # точек в матрице профиля (4 000² × 2 × 4 Б = 128 МБ); больше — заново только нужные
MAX_MISSING_SHARE = 0.5   # больше половины новых пар без пути (или точек без дороги) — движок неисправен
MIN_POINTS_FOR_SHARE = 10  # долю считаем, когда новых точек не меньше

_TILES_NAME = re.compile(r'^tiles-[0-9a-f]{12}-\d{14}-[a-z0-9_]{8}$')
_MATRIX_NAME = re.compile(r'^matrix-(tiles-[0-9a-f]{12}-\d{14}-[a-z0-9_]{8})-(auto|truck)-([0-9a-f]{8})\.npz$')


def valhalla_module() -> Any | None:
    """Модуль pyvalhalla или None (не установлен, не загрузились DLL)."""
    try:
        import valhalla
    except (ImportError, OSError):
        return None
    return valhalla


def valhalla_supported() -> bool:
    return np is not None and valhalla_module() is not None


def _env_choice(name: str, choices: Sequence[str], default: str) -> str:
    value = (os.environ.get(name) or default).strip().lower()
    if value not in choices:
        logger.warning('[Routes] %s=%r неизвестен — %s', name, value, choices[0])
        return choices[0]
    return value


def engine_mode() -> str:
    """Режим дорог: env ROUTES_ROAD_ENGINE (по умолчанию DEFAULT_ENGINE); неизвестное значение — osm."""
    return _env_choice(ENGINE_ENV, ENGINES, DEFAULT_ENGINE)


def engine_enabled() -> bool:
    return engine_mode() != ENGINE_OSM


def truck_time_mode() -> str:
    """Минуты грузовиков: env ROUTES_TRUCK_TIME — model (по умолчанию; неизвестное значение — тоже) | valhalla."""
    return _env_choice(TRUCK_TIME_ENV, (TRUCK_TIME_MODEL, TRUCK_TIME_VALHALLA), DEFAULT_TRUCK_TIME)


def truck_time_source(learned: str | None = None) -> tuple[str, str]:
    """Минуты грузовиков «Развоза» и почему — первое, что задано: env ROUTES_TRUCK_TIME не пуста — она ('env';
    неизвестное значение — model, как truck_time_mode); выбор обучения learned — model | valhalla ('learned'; вызывающий
    передаёт его, только если автообучение вида включено); иначе DEFAULT_TRUCK_TIME ('default')."""
    if (os.environ.get(TRUCK_TIME_ENV) or '').strip():
        return truck_time_mode(), 'env'
    if learned in (TRUCK_TIME_MODEL, TRUCK_TIME_VALHALLA):
        return learned, 'learned'
    return DEFAULT_TRUCK_TIME, 'default'


def base_dir() -> str:
    """Папка тайлов и кэшей: env ROUTES_VALHALLA_DIR, иначе %PROGRAMDATA%/route_optimizer/valhalla (Windows: общая
    для службы SYSTEM и пользователей), вне Windows — ~/.cache/route_optimizer/valhalla."""
    env = os.environ.get(DIR_ENV)
    if env:
        return env
    root = os.environ.get('PROGRAMDATA') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(root, 'route_optimizer', 'valhalla')


def truck_costing(capacity_kg: float | None) -> dict[str, float]:
    """Стоимость Valhalla для грузовика с тоннажем capacity_kg (груз, кг): полная масса = тоннаж × 2 в пределах
    TRUCK_GROSS_T, т (лёгкие грузовики 2–5 т: собственная масса ≈ грузу), размеры — лёгкого грузовика. Тоннаж не
    задан — DEFAULT_TRUCK_PAYLOAD_KG."""
    payload_t = (capacity_kg if capacity_kg is not None and capacity_kg > 0 else DEFAULT_TRUCK_PAYLOAD_KG) / 1000.0
    lo, hi = TRUCK_GROSS_T
    return {'weight': round(min(hi, max(lo, payload_t * TRUCK_GROSS_PER_PAYLOAD)), 1), **TRUCK_DIMS_M}


def costing_key(costing: dict[str, Any]) -> str:
    """Короткий отпечаток параметров стоимости (в имени и ключе кэша матрицы, в road_model_id)."""
    return hashlib.sha1(json.dumps(costing, sort_keys=True).encode('utf-8')).hexdigest()[:8]


_FINGERPRINTS: dict[str, tuple[str, str]] = {}   # путь карты → (подпись файла, отпечаток содержимого)
_FINGERPRINT_LOCK = threading.Lock()


def map_fingerprint(path: str, compute: bool = True) -> str | None:
    """Отпечаток содержимого карты (sha256, 16 знаков): та же карта после копирования — тот же отпечаток. Считается
    один раз на подпись файла (время изменения и размер); compute=False — только уже посчитанный (иначе None). Карты
    нет — None."""
    signature = map_signature(path)
    if signature is None:
        return None
    with _FINGERPRINT_LOCK:
        hit = _FINGERPRINTS.get(path)
    if hit is not None and hit[0] == signature:
        return hit[1]
    if not compute:
        return None
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    fingerprint = digest.hexdigest()[:16]
    with _FINGERPRINT_LOCK:
        _FINGERPRINTS[path] = (signature, fingerprint)
    return fingerprint


# --- Сборка тайлов ---

@dataclass(frozen=True)
class Build:
    """Действующая сборка: id — отпечаток (карта, pyvalhalla, правила), name — папка тайлов (уникальна для
    каждой сборки: версия кэшей матриц), config — valhalla.json сборки."""
    id: str
    name: str
    config: str
    info: dict[str, Any]


def build_id(fingerprint: str, valhalla_version: str) -> str:
    raw = f'{fingerprint}|{valhalla_version}|b{BUILD_FORMAT}'
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]


def current_build(base: str | None = None) -> Build | None:
    """Сборка из current.json — только завершённая (метка build.ok с тем же id); иначе None."""
    base = base or base_dir()
    try:
        with open(os.path.join(base, 'current.json'), encoding='utf-8') as f:
            info = json.load(f)
        name, bid = str(info['name']), str(info['id'])
        if not _TILES_NAME.match(name):
            return None
        folder = os.path.join(base, name)
        with open(os.path.join(folder, MARKER), encoding='utf-8') as f:
            if json.load(f).get('id') != bid:
                return None
        config = os.path.join(folder, 'valhalla.json')
        if not os.path.isfile(config):
            return None
        return Build(bid, name, config, info)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
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
    for profile in (PROFILE_CAR, PROFILE_TRUCK):
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


def _run(tool: str, config: str, pbf: str, timeout: float) -> None:
    """Исполняемый файл пакета pyvalhalla — напрямую, а не через python -m valhalla: при тайм-ауте завершается сам
    сборщик, а не только промежуточный python. DLL пакета — через PATH, как в valhalla._scripts."""
    package = os.path.dirname(os.path.abspath(valhalla_module().__file__))
    exe = os.path.join(package, 'bin', tool + ('.exe' if os.name == 'nt' else ''))
    env = dict(os.environ)
    libs = os.path.join(os.path.dirname(package), 'pyvalhalla.libs')
    if os.path.isdir(libs):
        env['PATH'] = libs + os.pathsep + env.get('PATH', '')
    try:
        proc = subprocess.run([exe, '-c', config, pbf], capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{tool}: дольше {timeout:.0f} с — остановлен') from None
    if proc.returncode != 0:
        raise RuntimeError(f'{tool}: код {proc.returncode}: {(proc.stderr or proc.stdout)[-2000:]}')


class BuildBusy(RuntimeError):
    """Тайлы собирает другой процесс, и ждать дольше нельзя."""


@contextmanager
def _build_lock(base: str, wait_s: float) -> Iterator[str]:
    """Блокировка сборки — блокировка ОС на файле build.lock (msvcrt.locking под Windows, flock — вне её). ОС снимает
    её сама, когда владелец отпускает её, завершается или падает, поэтому «протухших» блокировок нет — нечего
    взламывать, а файл никто не удаляет (и чужую блокировку не снять). Занята — ждём до wait_s, затем BuildBusy."""
    path = os.path.join(base, LOCK_NAME)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + wait_s
        while not _lock_file(fd, True):
            if time.monotonic() >= deadline:
                raise BuildBusy(f'тайлы собирает другой процесс ({path})')
            time.sleep(0.5)
        try:
            yield path
        finally:
            _lock_file(fd, False)
    finally:
        os.close(fd)   # закрытие отпускает блокировку и само


def _lock_file(fd: int, lock: bool) -> bool:
    """Взять (lock) или отпустить блокировку ОС на первый байт файла fd; занята — False."""
    try:
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK if lock else msvcrt.LK_UNLCK, 1)
        else:   # pragma: no cover — сервер под Windows
            import fcntl
            fcntl.flock(fd, (fcntl.LOCK_EX | fcntl.LOCK_NB) if lock else fcntl.LOCK_UN)
    except OSError:
        return False
    return True


def build_tiles(pbf: str | None = None, base: str | None = None, force: bool = False,
                max_seconds: float | None = None) -> Build:
    """Тайлы Valhalla из карты pbf в base; та же сборка уже действует (и не force) — она и возвращается.
    max_seconds — предел всей сборки (ожидание блокировки, попытки; тайм-аут — RuntimeError). Прежние сборки и их
    кэши удаляются, если не заняты (сервер ещё читает — останутся до следующей сборки)."""
    pbf = pbf or osm_path()
    base = base or base_dir()
    module = valhalla_module()
    if module is None:
        raise RuntimeError('pyvalhalla не установлен: pip install pyvalhalla==3.9.0 (64-битный Python ≥ 3.12)')
    fingerprint = map_fingerprint(pbf)
    if fingerprint is None:
        raise FileNotFoundError(f'Карты нет: {pbf}')
    bid = build_id(fingerprint, module.__version__)
    current = current_build(base)
    if current is not None and current.id == bid and not force:
        return current
    os.makedirs(base, exist_ok=True)
    started = time.monotonic()
    budget = BUILD_ATTEMPTS * 2 * BUILD_TIMEOUT_S if max_seconds is None else float(max_seconds)

    def left() -> float:
        return budget - (time.monotonic() - started)

    with _build_lock(base, wait_s=max(0.0, left())):
        current = current_build(base)   # пока ждали блокировку, другой процесс мог собрать то же
        if current is not None and current.id == bid and not force:
            return current
        for attempt in range(1, BUILD_ATTEMPTS + 1):
            tiles = tempfile.mkdtemp(prefix=f'tiles-{bid}-{datetime.now():%Y%m%d%H%M%S}-', dir=base)
            config_path = os.path.join(tiles, 'valhalla.json')
            try:
                _write_json(config_path, _config(tiles))
                for tool in ('valhalla_build_admins', 'valhalla_build_tiles'):
                    if left() <= 0:
                        raise TimeoutError(f'сборка тайлов не уложилась в {budget:.0f} с')
                    _run(tool, config_path, pbf, timeout=min(BUILD_TIMEOUT_S, left()))
                break
            except RuntimeError:
                shutil.rmtree(tiles, ignore_errors=True)
                if attempt == BUILD_ATTEMPTS or left() <= 0:
                    raise
                logger.warning('[Routes] Valhalla: сборка тайлов упала (попытка %d) — повтор', attempt, exc_info=True)
            except BaseException:
                shutil.rmtree(tiles, ignore_errors=True)
                raise
        name = os.path.basename(tiles)
        info = {'id': bid, 'name': name, 'pbf': os.path.abspath(pbf), 'map_sha256': fingerprint,
                'valhalla': module.__version__, 'format': BUILD_FORMAT,
                'built_at': datetime.now().isoformat(timespec='seconds'),
                'seconds': round(time.monotonic() - started, 1)}
        _write_json(os.path.join(tiles, MARKER), info)          # сборка завершена
        _write_json(os.path.join(base, 'current.json'), info)   # и опубликована
        logger.info('[Routes] Valhalla: тайлы собраны за %.0f с → %s', info['seconds'], tiles)
        _cleanup(base, name)
    return Build(bid, name, config_path, info)


def _cleanup(base: str, keep: str) -> None:
    """Под блокировкой сборки: прежние завершённые сборки (с меткой) и брошенные недостроенные (без метки, старше
    LOCK_STALE_S — строить их уже некому), кэши матриц прежних сборок. Чужие файлы (не по шаблону имён) не трогаются;
    что занято — остаётся."""
    now = time.time()
    for entry in os.listdir(base):
        path = os.path.join(base, entry)
        if _TILES_NAME.match(entry):
            if entry == keep or not os.path.isdir(path):
                continue
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                continue
            if os.path.isfile(os.path.join(path, MARKER)) or age > LOCK_STALE_S:
                shutil.rmtree(path, ignore_errors=True)
            continue
        found = _MATRIX_NAME.match(entry)
        if found and found.group(1) != keep:
            try:
                os.remove(path)
            except OSError:
                pass


# --- Движок и матрицы ---

class EngineBusy(RuntimeError):
    """Свободного Actor сейчас нет (запрос без ожидания)."""


class _Engine:
    """Сборка и общий пул Actor (≤ WORKERS на сборку): одному Actor потокобезопасность не обещана — запрос берёт
    свободный из пула и возвращает его; новых не больше WORKERS, сколько бы потоков ни считало."""

    def __init__(self, build: Build):
        self.build = build
        self._idle: list[Any] = []
        self._created = 0
        self._cond = threading.Condition()
        _ENGINES.add(self)

    def close(self) -> None:
        """Отпустить свободные Actor (новые создадутся при следующем запросе)."""
        with self._cond:
            self._idle.clear()
            self._created = 0

    @contextmanager
    def actor(self, wait: bool = True) -> Iterator[Any]:
        """Свободный Actor из пула. wait=False — только уже созданный и свободный сейчас, иначе EngineBusy сразу (карта
        машин: опрос не должен вставать в очередь за фоновой сборкой матриц)."""
        with self._cond:
            if not wait:
                if not self._idle:
                    raise EngineBusy('все Actor заняты')
            while not self._idle and self._created >= WORKERS:
                self._cond.wait()
            actor = self._idle.pop() if self._idle else None
            if actor is None:
                self._created += 1
        if actor is None:
            try:
                actor = valhalla_module().Actor(self.build.config)
            except BaseException:
                with self._cond:
                    self._created -= 1
                    self._cond.notify()
                raise
        try:
            yield actor
        finally:
            with self._cond:
                self._idle.append(actor)
                self._cond.notify()


_ENGINES: weakref.WeakSet[_Engine] = weakref.WeakSet()


@atexit.register
def _close_engines() -> None:
    """Actor — до выгрузки интерпретатора: иначе nanobind при выходе пишет в журнал об «утечке»."""
    for engine in list(_ENGINES):
        engine.close()


@dataclass(frozen=True)
class _Table:
    """Неизменяемое состояние кэша профиля: чтение без блокировки, дозаполнение — заменой целиком."""
    index: dict[Point, int]   # ключ точки → строка и столбец матриц
    snap: Any                 # np.float64 [k]: км до дороги (inf — не привязана)
    km: Any                   # np.float32 [k × k]: км по дорогам строка → столбец, без привязки; NaN — пути нет
    minutes: Any              # np.float32 [k × k]: минуты Valhalla, без поправки зоны


def _empty_table() -> _Table:
    return _Table({}, np.zeros(0), np.zeros((0, 0), dtype=np.float32), np.zeros((0, 0), dtype=np.float32))


class _BlockError(RuntimeError):
    """Запрос матрицы не идёт и для одной точки — движок неисправен, расчёт прерывается."""


class _ProfileMatrix:
    """Направленные км и минуты профиля (с его стоимостью) между всеми точками, что запрашивали. ensure — расчёт
    (фоновый поток сервера, команды и скрипты), table — неизменяемый срез для чтения. Сбой движка — failed: до
    перезапуска только запасной путь."""

    def __init__(self, engine: _Engine, profile: str, costing: dict[str, Any], cache_path: str | None):
        self.engine = engine
        self.profile = profile
        self.costing = dict(costing)
        self._cache_path = cache_path
        self._cache_read = cache_path is None
        self._lock = threading.Lock()
        self.table = _empty_table()
        self.failed = False

    @property
    def key(self) -> str:
        return (f'{self.engine.build.name}|{self.profile}|{json.dumps(self.costing, sort_keys=True)}'
                f'|key{KEY_DECIMALS}|snap{SNAP_MAX_KM:g}|m{MATRIX_FORMAT}')

    def ready(self, keys: Iterable[Point]) -> bool:
        """Все точки (ключи point_key) посчитаны, движок исправен."""
        return self._cache_read and not self.failed and set(keys).issubset(self.table.index)

    def load(self) -> None:
        """Кэш с диска, если ещё не читали (без расчёта)."""
        if self._cache_read:
            return
        with self._lock:
            if not self._cache_read:
                self._read_cache()

    def ensure(self, points: Iterable[Point | None]) -> None:
        """Досчитать точки, которых нет (одним расширением, одна запись кэша)."""
        keys = {point_key(p) for p in points if p is not None}
        if self.failed or (self._cache_read and keys.issubset(self.table.index)):
            return
        with self._lock:
            if self.failed:
                return
            if not self._cache_read:
                self._read_cache()
            new = sorted(k for k in keys if k not in self.table.index)
            if not new:
                return
            base = self.table
            if len(base.index) + len(new) > MAX_POINTS:   # новая таблица считается в стороне, прежняя — до конца
                logger.info('[Routes] Valhalla %s: точек больше %d — матрица заново только для нужных %d',
                            self.profile, MAX_POINTS, len(keys))
                base, new = _empty_table(), sorted(keys)
            try:
                self._extend(new, base)
            except Exception:
                logger.exception('[Routes] Valhalla %s: матрица не посчитана — профиль выключен до перезапуска, '
                                  'дороги по графу OSM', self.profile)
                self.failed = True
                return
            self._write_cache()

    # -- расчёт --

    def _request(self, kind: str, body: dict[str, Any]) -> Any:
        with self.engine.actor() as actor:
            return getattr(actor, kind)(body)

    def _locate(self, points: Sequence[Point]) -> Any:
        """Км от точки до дороги профиля (inf — дороги рядом нет)."""
        out = np.full(len(points), np.inf)
        for k in range(0, len(points), LOCATE_BATCH):
            chunk = points[k:k + LOCATE_BATCH]
            body = {'locations': [{'lat': p[0], 'lon': p[1]} for p in chunk], 'costing': self.profile,
                    'verbose': False}
            if self.costing:
                body['costing_options'] = {self.profile: self.costing}
            res = self._request('locate', body)
            for i, (p, item) in enumerate(zip(chunk, res)):
                edges = (item or {}).get('edges') or []
                if edges:
                    e = edges[0]
                    out[k + i] = haversine_km(p, (float(e['correlated_lat']), float(e['correlated_lon'])))
        return out

    def _block(self, src: Sequence[Point], dst: Sequence[Point], abort: threading.Event) -> tuple[Any, Any]:
        """(км, минуты) [len(src) × len(dst)], пути нет — NaN. Ошибка запроса — меньшая сторона делится пополам; не
        идёт и одна точка — _BlockError, и остальные блоки (abort) больше не запрашиваются."""
        if abort.is_set():
            raise _BlockError('расчёт прерван')
        km = np.full((len(src), len(dst)), np.nan, dtype=np.float32)
        minutes = np.full((len(src), len(dst)), np.nan, dtype=np.float32)
        body = {'sources': [{'lat': p[0], 'lon': p[1]} for p in src],
                'targets': [{'lat': p[0], 'lon': p[1]} for p in dst],
                'costing': self.profile, 'verbose': False}
        if self.costing:
            body['costing_options'] = {self.profile: self.costing}
        try:
            res = self._request('matrix', body)['sources_to_targets']
        except Exception as exc:   # ValhallaError, RuntimeError, MemoryError…
            by_src = len(src) <= len(dst)
            side = src if by_src else dst
            if len(side) == 1:
                abort.set()
                raise _BlockError(f'{src[0] if by_src else dst[0]}: {exc}') from exc
            h = len(side) // 2
            if by_src:
                (k1, m1), (k2, m2) = self._block(src[:h], dst, abort), self._block(src[h:], dst, abort)
                return np.vstack([k1, k2]), np.vstack([m1, m2])
            (k1, m1), (k2, m2) = self._block(src, dst[:h], abort), self._block(src, dst[h:], abort)
            return np.hstack([k1, k2]), np.hstack([m1, m2])
        for i, (drow, trow) in enumerate(zip(res['distances'], res['durations'])):
            for j, (d, t) in enumerate(zip(drow, trow)):
                if d is not None and t is not None:
                    km[i, j] = d
                    minutes[i, j] = t / 60.0
        return km, minutes

    def _rows(self, src: Sequence[Point], dst: Sequence[Point]) -> tuple[Any, Any]:
        """Матрица src × dst: блоки по BATCH точек меньшей стороны (Valhalla ищет от неё) в WORKERS потоках."""
        if not src or not dst:
            return (np.full((len(src), len(dst)), np.nan, dtype=np.float32),
                    np.full((len(src), len(dst)), np.nan, dtype=np.float32))
        by_src = len(src) <= len(dst)
        side = src if by_src else dst
        chunks = [side[k:k + BATCH] for k in range(0, len(side), BATCH)]
        abort = threading.Event()

        def run(chunk: Sequence[Point]) -> tuple[Any, Any]:
            return self._block(chunk, dst, abort) if by_src else self._block(src, chunk, abort)

        if len(chunks) == 1:
            parts = [run(chunks[0])]
        else:
            with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='valhalla') as pool:
                parts = list(pool.map(run, chunks))
        stack = np.vstack if by_src else np.hstack
        return stack([p[0] for p in parts]), stack([p[1] for p in parts])

    def _extend(self, new: Sequence[Point], base: _Table) -> None:
        """Таблица base + новые точки: привязка, затем пути новые → все и прежние → новые (только привязанные точки);
        готовая таблица заменяет действующую целиком. Ошибка движка или слишком много пар без пути — исключение:
        таблица и кэш не меняются."""
        started = time.perf_counter()
        t = base
        old = sorted(t.index, key=t.index.get)
        k_old, k = len(old), len(old) + len(new)
        snap = np.concatenate([t.snap, self._locate(new)])
        ok = snap <= SNAP_MAX_KM
        if len(new) >= MIN_POINTS_FOR_SHARE and np.mean(~ok[k_old:]) > MAX_MISSING_SHARE:
            raise RuntimeError(f'{int(np.sum(~ok[k_old:]))} из {len(new)} новых точек без дороги — тайлы неисправны')
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
            missing = float(np.mean(np.isnan(km[np.ix_(live_new, live)])))
            if len(live_new) >= MIN_POINTS_FOR_SHARE and missing > MAX_MISSING_SHARE:
                raise RuntimeError(f'пар без пути {missing:.0%} — тайлы или движок неисправны')
        index = dict(t.index)
        for i, p in enumerate(new):
            index[p] = k_old + i
        self.table = _Table(index, snap, km, minutes)
        logger.info('[Routes] Valhalla %s: +%d точек (не привязано %d), всего %d; %.1f с', self.profile, len(new),
                    int(np.sum(~ok[k_old:])), k, time.perf_counter() - started)

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
            logger.warning('[Routes] Valhalla: кэш %s не прочитан — пересчёт', os.path.basename(path), exc_info=True)
            return
        self.table = table
        logger.info('[Routes] Valhalla %s: кэш — точек %d', self.profile, len(index))

    def _write_cache(self) -> None:
        """Кэш на диск (атомарно); кэши той же сборки и профиля с другой стоимостью (другой парк) — удаляются."""
        from .roads import _save_npz
        path = self._cache_path
        if path is None:
            return
        t = self.table
        keys = sorted(t.index, key=t.index.get)
        try:
            _save_npz(path, key=self.key, lat=np.array([p[0] for p in keys], dtype=np.float64),
                      lon=np.array([p[1] for p in keys], dtype=np.float64), snap=t.snap, km=t.km, minutes=t.minutes)
        except OSError:
            logger.exception('[Routes] Valhalla: кэш %s не записан', path)
            return
        folder, mine = os.path.split(path)
        prefix = f'matrix-{self.engine.build.name}-{self.profile}-'
        for entry in os.listdir(folder):
            if entry != mine and entry.startswith(prefix) and _MATRIX_NAME.match(entry):
                try:
                    os.remove(os.path.join(folder, entry))
                except OSError:
                    pass


class _Registry:
    """Матрицы одной сборки: (профиль, стоимость) → _ProfileMatrix; общий пул Actor. У профиля — одна стоимость:
    матрица прежней (сменился парк) уходит из реестра — память не копится, а срезы, что её держат, дочитают свою."""

    def __init__(self, build: Build, base: str | None):
        self.build = build
        self.engine = _Engine(build)
        self._base = base
        self._lock = threading.Lock()
        self._matrices: dict[tuple[str, str], _ProfileMatrix] = {}

    def matrix(self, profile: str, costing: dict[str, Any]) -> _ProfileMatrix:
        key = (profile, costing_key(costing))
        with self._lock:
            m = self._matrices.get(key)
            if m is None:
                for other in [k for k in self._matrices if k[0] == profile]:
                    del self._matrices[other]
                path = (os.path.join(self._base, f'matrix-{self.build.name}-{profile}-{key[1]}.npz')
                        if self._base is not None else None)
                m = self._matrices[key] = _ProfileMatrix(self.engine, profile, costing, path)
            return m


class ValhallaRoads:
    """Дороги Valhalla одного профиля для одного расчёта — тот же интерфейс, что у roads.RoadDistances (ensure, km,
    minutes, unsnapped, lines, truck, version, failed, km_source).

    Срез матриц берётся при создании: в одном расчёте км, минуты и road_model_id — из одной модели, что бы ни делал
    фоновый поток. Пара, которой в срезе нет (точка не привязана, пути нет, точку не готовили), — запасной путь:
    км — граф OSM (fallback), его нет — None (по прямой × извилистость, Norms.km); минуты — None (скорость зоны).

    time_only (режим valhalla_time) — км сначала по графу OSM, Valhalla — минуты (у машин менеджеров — и км, где графа
    нет; у грузовиков — нет: по прямой × извилистость, как до этапа 2). Минуты грузовика (профиль truck) — только при
    truck_time (выбор вызывающего: ValhallaProvider.get, truck(truck_time=…); по умолчанию — ROUTES_TRUCK_TIME), иначе
    прежняя модель.
    Объезд малого центра («Развоз»: fallback — roads.CenterBypassRoads): путь Valhalla — кратчайший, поэтому его км и
    минуты на участках в объезд растягиваются во столько же раз, во сколько объезд по графу OSM длиннее (detour).
    compute — ensure считает недостающие точки сам (команды и скрипты); у сервера (False) — нет: считает фон."""

    def __init__(self, registry: _Registry, profile: str, fallback: RoadDistances | CenterBypassRoads | None = None, *,
                 time_only: bool = False, truck_time: bool = False, truck_cost: dict[str, float] | None = None,
                 compute: bool = False):
        self._registry = registry
        self.profile = profile
        self.truck_costing = dict(truck_cost) if truck_cost is not None else truck_costing(None)
        self.costing = CAR_COSTING if profile == PROFILE_CAR else self.truck_costing
        self._m = registry.matrix(profile, self.costing)
        self.fallback = fallback
        self.time_only = time_only
        self.truck_time = truck_time
        self.compute = compute
        self._table = self._m.table
        self.active = not self._m.failed
        # срез грузовика — в тот же момент, что и свой: в одном расчёте машины и грузовики — из одного состояния
        self._truck = (self if profile == PROFILE_TRUCK else
                       ValhallaRoads(registry, PROFILE_TRUCK, fallback, time_only=time_only, truck_time=truck_time,
                                     truck_cost=self.truck_costing, compute=compute))

    @property
    def build_id(self) -> str:
        return self._registry.build.id

    @property
    def serves_minutes(self) -> bool:
        """Минуты расчёта — из Valhalla (машины менеджеров; грузовики — при truck_time)."""
        return self.active and (self.profile == PROFILE_CAR or self.truck_time)

    @property
    def _graph_km_only(self) -> bool:
        """Грузовик в режиме valhalla_time: км — только граф OSM, точки вне графа — по прямой × извилистость, как до
        этапа 2. Матрица грузовика в этом режиме не входит в готовность среза: км Valhalla молча меняли бы расчёт,
        когда её досчитает фон."""
        return self.time_only and self.profile == PROFILE_TRUCK

    @property
    def km_source(self) -> str:
        """Откуда км: 'osm' — граф OSM (режим valhalla_time или Valhalla выключен сбоем); 'straight' — грузовик в
        режиме valhalla_time без графа (по прямой × извилистость); иначе 'valhalla:<профиль>:<стоимость>'."""
        if self.fallback is not None and not self.fallback.failed and (self.time_only or not self.active):
            return 'osm'
        if self._graph_km_only:
            return 'straight'
        return f'valhalla:{self.profile}:{costing_key(self.costing)}'

    @property
    def version(self) -> str:
        """Для ключа кэша оценки: сборка, режим, профиль грузовика и версия графа OSM."""
        back = 'off' if self.fallback is None else ('failed' if self.fallback.failed else self.fallback.version)
        mode = ENGINE_VALHALLA_TIME if self.time_only else ENGINE_VALHALLA
        state = 'on' if self.active else 'failed'
        return (f'{mode}:{self._registry.build.name}:{state}|truck:{int(self.truck_time)}:'
                f'{costing_key(self.truck_costing)}|{back}')

    @property
    def failed(self) -> bool:
        return not self.active and (self.fallback is None or self.fallback.failed)

    @property
    def size(self) -> tuple[int, int]:
        n = len(self._table.index)
        return n, n

    def truck(self, truck_time: bool | None = None) -> ValhallaRoads:
        """Срез профиля грузовика, снятый вместе с этим. truck_time — минуты грузовика (True — Valhalla, False — прежняя
        модель) вместо выбранных при срезе: новый объект на тот же срез матриц (км, минуты и road_model_id расчёта —
        из одного состояния), общее состояние не меняется — безопасно из разных потоков."""
        truck = self._truck
        if truck_time is None or bool(truck_time) == truck.truck_time:
            return truck
        other = copy.copy(truck)
        other.truck_time = bool(truck_time)
        other._truck = other
        return other

    def ensure(self, points: Iterable[Point | None]) -> None:
        """compute — досчитать точки (первый раз — минуты, дальше кэш), иначе Valhalla не трогается. Граф OSM —
        всем набором, если км из него (time_only) или Valhalla выключен сбоем: иначе он досчитывал бы точки по одной."""
        points = list(points)
        if self.compute and self.active:
            self._m.ensure(points)
            self._table = self._m.table
            self.active = not self._m.failed
        if self.fallback is not None and (self.time_only or not self.active):
            self.fallback.ensure(points)

    def _pair(self, a: Point, b: Point) -> tuple[_Table, int, int] | None:
        if not self.active:
            return None
        t = self._table
        ia, ib = t.index.get(point_key(a)), t.index.get(point_key(b))
        if ia is None or ib is None:
            return None
        return t, ia, ib

    def _valhalla_km(self, a: Point, b: Point) -> float | None:
        hit = self._pair(a, b)
        if hit is None:
            return None
        t, ia, ib = hit
        sa, sb = t.snap.item(ia), t.snap.item(ib)
        if not (sa <= SNAP_MAX_KM and sb <= SNAP_MAX_KM):
            return None
        if ia == ib:
            return haversine_km(a, b)
        d = t.km.item(ia, ib)
        return (sa + d + sb) * self._detour(a, b) if math.isfinite(d) else None

    def _detour(self, a: Point, b: Point) -> float:
        """Объезд малого центра на участке (roads.CenterBypassRoads.detour); без объезда — 1."""
        return self.fallback.detour(a, b) if isinstance(self.fallback, CenterBypassRoads) else 1.0

    def km(self, a: Point, b: Point) -> float | None:
        """Км A → B по дорогам: Valhalla, у кого пути нет — граф OSM (км из графа — наоборот; грузовик в режиме
        valhalla_time — только граф); нет — None (по прямой × извилистость, Norms.km)."""
        source = self.km_source
        if source == 'straight':
            return None
        if source == 'osm':
            d = self.fallback.km(a, b)
            return d if d is not None or self._graph_km_only else self._valhalla_km(a, b)
        d = self._valhalla_km(a, b)
        if d is not None or self.fallback is None:
            return d
        return self.fallback.km(a, b)

    def valhalla_minutes(self, a: Point, b: Point, city: bool) -> float | None:
        """Минуты Valhalla A → B × поправка зоны (city — оба конца в городе; объезд центра — × detour) — независимо от
        переключателей; нет — None."""
        hit = self._pair(a, b)
        if hit is None:
            return None
        t, ia, ib = hit
        if ia == ib:
            return None
        m = t.minutes.item(ia, ib)
        return m * TIME_FACTOR[city] * self._detour(a, b) if math.isfinite(m) else None

    def route(self, a: Point, b: Point, city: bool) -> tuple[float, float] | None:
        """(км, минуты) участка A → B отдельным запросом к Valhalla — для точек, которых нет в таблице (положение машины на
        карте «сейчас» — №76: таблицу такими точками не растим). Минуты — × поправка зоны, как valhalla_minutes; км — путь
        Valhalla без привязки к дороге. Движка нет, сбой запроса, пути нет или все Actor заняты сейчас (ждать нельзя) — None."""
        if not self.active:
            return None
        body = {'sources': [{'lat': a[0], 'lon': a[1]}], 'targets': [{'lat': b[0], 'lon': b[1]}],
                'costing': self.profile, 'verbose': False}
        if self._m.costing:
            body['costing_options'] = {self.profile: self._m.costing}
        try:
            with self._m.engine.actor(wait=False) as actor:   # занят пул — сразу запасная модель, очереди нет
                res = actor.matrix(body)['sources_to_targets']
            k, m = res['distances'][0][0], res['durations'][0][0]
            k, m = float(k), float(m) / 60.0
        except (EngineBusy, TypeError, ValueError, IndexError, KeyError, RuntimeError):   # ValhallaError — RuntimeError
            return None
        if not (math.isfinite(k) and math.isfinite(m)) or k <= 0 or m <= 0:
            return None
        return k * self._detour(a, b), m * TIME_FACTOR[city] * self._detour(a, b)

    def minutes(self, a: Point, b: Point, city: bool) -> float | None:
        """Минуты езды A → B для расчёта; None — скорость зоны (прежняя модель)."""
        return self.valhalla_minutes(a, b, city) if self.serves_minutes else None

    def unsnapped(self, points: Iterable[Point | None]) -> int:
        """Сколько точек без км по дорогам (км из графа OSM — считает он)."""
        source = self.km_source
        if source == 'osm':
            return self.fallback.unsnapped(points)
        keys = {point_key(p) for p in points if p is not None}
        if source == 'straight':
            return len(keys)
        t = self._table
        return sum(1 for k in keys if k in t.index and not t.snap[t.index[k]] <= SNAP_MAX_KM)

    def lines(self, lines: Sequence[Sequence[Point]]) -> list[list[Point]] | None:
        """Линии на карте — по графу OSM (если есть)."""
        return self.fallback.lines(lines) if self.fallback is not None else None


def _osm_id(roads: RoadDistances | CenterBypassRoads) -> str:
    from .roads import DIST_FORMAT, RULES_VERSION
    if isinstance(roads, CenterBypassRoads):   # объезд центра — правило расчёта на тех же дорогах: id тот же
        roads = roads.base
    path = roads.map_path
    fingerprint = map_fingerprint(path) if path else None
    return f'osm-dijkstra:{fingerprint or roads.version}|r{RULES_VERSION}|d{DIST_FORMAT}'


def road_model_id(roads: RoadDistances | CenterBypassRoads | ValhallaRoads | None) -> str:
    """Стабильный id модели дорог расчёта (Norms.roads; грузовики — Norms.for_trucks().roads) — для выученных
    поправок времени (этап 4): поправка применяется, только если id тот же, что при обучении.

    'straight' — по прямой × извилистость (дорог нет или они сломались);
    'osm-dijkstra:<отпечаток карты>|r<правила>|d<формат кэша>' — км графа OSM, минуты — км / скорость зоны;
    'valhalla:<сборка>:<профиль>:<стоимость>' — км Valhalla, минуты — км / скорость зоны;
    '<км>+valhalla-time:<сборка>:<профиль>:<стоимость>|tf<город>/<область>' — минуты Valhalla × поправка зоны.
    Сборка — отпечаток содержимого карты, версии pyvalhalla и правил: пересборка тех же тайлов и копирование карты id
    не меняют; новая карта, профиль грузовика или поправка — меняют. Id и расчёт берут модель из одного среза
    (ValhallaRoads): Valhalla выключен сбоем — id графа OSM."""
    if roads is None or roads.failed:
        return 'straight'
    if not isinstance(roads, ValhallaRoads):
        return _osm_id(roads)
    source = roads.km_source
    if source == 'osm':
        km = _osm_id(roads.fallback)
    elif source == 'straight':
        km = 'straight'
    else:
        km = f'valhalla:{roads.build_id}:{roads.profile}:{costing_key(roads.costing)}'
    if not roads.serves_minutes:
        return km
    return (f'{km}+valhalla-time:{roads.build_id}:{roads.profile}:{costing_key(roads.costing)}'
            f'|tf{TIME_FACTOR[True]:g}/{TIME_FACTOR[False]:g}')


@dataclass(frozen=True)
class TruckLegMinutes:
    """Участок грузовика двумя моделями времени — для выбора по фактическим трекам водителей (этап 4 плана)."""
    km: float                 # км участка в расчёте грузовика (Norms.for_trucks().km)
    model: float              # прежняя модель: км / скорость зоны, часовой профиль пробок от выезда
    valhalla: float | None    # Valhalla-грузовик × поправка зоны, тот же часовой профиль; None — Valhalla нет


def truck_leg_minutes(norms: Norms, a: Point, b: Point, depart_minute: float,
                      weekday: int | None = None) -> TruckLegMinutes:
    """Минуты участка грузовика a → b с выездом в depart_minute (минуты от полуночи; weekday — 0 = пн, по умолчанию
    norms.traffic_weekday) обеими моделями — сколько дал бы расчёт с минутами грузовиков model и valhalla (те же
    км и часовой профиль пробок norms.traffic, что в fleet._matrices / TravelMatrix; выбор минут в срезе не важен).
    valhalla — None, если Valhalla нет или пара не посчитана: точки готовит фоновый поток сервера (ValhallaProvider.get)
    или ensure открытой open_valhalla."""
    tn = norms.for_trucks()
    km = tn.km(a, b)
    city = in_city(a, tn.city_center, tn.city_radius_km) and in_city(b, tn.city_center, tn.city_radius_km)
    day = tn.traffic_weekday if weekday is None else weekday

    def minutes(speed: float) -> float:
        if km <= 0:
            return 0.0
        if tn.traffic is None:
            return km / speed * 60.0
        return tn.traffic.travel(km, speed, city, day, depart_minute)

    model = minutes(tn.speed_city_kmh if city else tn.speed_region_kmh)
    raw = tn.roads.valhalla_minutes(a, b, city) if isinstance(tn.roads, ValhallaRoads) else None
    valhalla = minutes(km / raw * 60.0) if raw is not None and raw > 0 and km > 0 else None
    return TruckLegMinutes(km, model, valhalla)


# --- Сервер: фоновая подготовка ---

class ValhallaProvider:
    """Valhalla для сервера. get() не ждёт тяжёлой работы: тайлы собирает и матрицы для запрошенных точек считает один
    фоновый поток (start — из init_app; повторные вызовы его не плодят), а пока не готово — None (расчёт по графу
    OSM). Готово — ValhallaRoads со срезом матриц. Режим osm, нет pyvalhalla или карты — None и никаких потоков.
    Карта и current.json проверяются на каждый вызов: новая карта и тайлы команды build подхватываются без
    перезапуска; сборка не удалась — до смены карты или перезапуска Valhalla нет."""

    def __init__(self, base: str | None = None, pbf: str | None = None):
        self.base = base or base_dir()
        self.pbf = pbf or osm_path()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._registry: _Registry | None = None
        self._wanted: dict[tuple[str, str], tuple[dict[str, Any], set[Point]]] = {}
        self._failed_for: str | None = None   # id сборки, которая не собралась

    def _usable(self) -> bool:
        return engine_enabled() and valhalla_supported() and map_signature(self.pbf) is not None

    def start(self) -> None:
        """Фоновая подготовка с запуска сервера: тайлы (если нужно) и кэш матриц машин менеджеров с диска."""
        if self._usable():
            with self._lock:
                self._kick()

    def preparing(self) -> bool:
        """Фон ещё работает (собирает тайлы или считает матрицы запрошенных точек): ждать готовности есть смысла.
        Valhalla выключен, сборка не удалась или всё готово — False."""
        with self._lock:
            return self._usable() and self._thread is not None and self._thread.is_alive()

    def get(self, fallback: RoadDistances | CenterBypassRoads | None, points: Iterable[Point | None],
            truck_capacity_kg: float | None = None, truck_time: bool | None = None) -> ValhallaRoads | None:
        """Срез Valhalla для расчёта по точкам points (truck_capacity_kg — тоннаж самой большой машины парка), если
        его матрицы для этих точек готовы; иначе фон получает точки, а расчёт — None (граф OSM). truck_time — минуты
        грузовиков из Valhalla (выбор вызывающего: «Развоз» — truck_time_source), None — ROUTES_TRUCK_TIME; с ними нужна
        готовая и матрица грузовика."""
        if not self._usable():
            return None
        time_only = engine_mode() == ENGINE_VALHALLA_TIME
        truck_time = truck_time_mode() == TRUCK_TIME_VALHALLA if truck_time is None else bool(truck_time)
        truck_cost = truck_costing(truck_capacity_kg)
        keys = {point_key(p) for p in points if p is not None}
        fingerprint = map_fingerprint(self.pbf, compute=False)   # считает фон: здесь — только уже известный
        with self._lock:
            if fingerprint is not None and build_id(fingerprint, valhalla_module().__version__) == self._failed_for:
                return None   # эта карта не собралась — до смены карты или перезапуска граф OSM
            self._want(PROFILE_CAR, CAR_COSTING, keys)
            self._want(PROFILE_TRUCK, truck_cost, keys)   # и при прежней модели — для truck_leg_minutes
            reg = self._current(fingerprint)
            if reg is None:
                self._kick()
                return None
            need = [reg.matrix(PROFILE_CAR, CAR_COSTING)]
            if truck_time or not time_only:            # грузовики берут из Valhalla минуты или км
                need.append(reg.matrix(PROFILE_TRUCK, truck_cost))
            if any(m.failed for m in need):
                return None
            if self._todo(reg):
                self._kick()
            if not all(m.ready(keys) for m in need):
                return None
            # срез — под той же блокировкой, что и проверка готовности
            return ValhallaRoads(reg, PROFILE_CAR, fallback, time_only=time_only, truck_time=truck_time,
                                 truck_cost=truck_cost)

    # -- под self._lock --

    def _want(self, profile: str, costing: dict[str, Any], keys: set[Point]) -> None:
        """Точки для фона: по профилю (у грузовика — только последняя стоимость); больше MAX_POINTS — только новые."""
        key = (profile, costing_key(costing))
        for other in [k for k in self._wanted if k[0] == profile and k != key]:
            del self._wanted[other]
        _, have = self._wanted.get(key, (costing, set()))
        if len(have | keys) > MAX_POINTS:
            have = set()
        self._wanted[key] = (costing, have | keys)

    def _current(self, fingerprint: str | None) -> _Registry | None:
        """Матрицы действующей сборки этой карты; сборки нет (или она другой карты) — None."""
        if fingerprint is None:
            return None
        bid = build_id(fingerprint, valhalla_module().__version__)
        build = current_build(self.base)
        if build is None or build.id != bid:
            return None
        if self._registry is None or self._registry.build.name != build.name:
            self._registry = _Registry(build, self.base)
        return self._registry

    def _todo(self, reg: _Registry) -> list[tuple[_ProfileMatrix, set[Point]]]:
        return [(m, keys) for m, keys in ((reg.matrix(p, cost), keys) for (p, _), (cost, keys) in self._wanted.items())
                if not m.failed and not m.ready(keys)]

    def _kick(self) -> None:
        """Фоновый поток — один: уже идёт — он сам заберёт новые точки."""
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._work, name='valhalla-prepare', daemon=True)
            self._thread.start()

    # -- фоновый поток --

    def _work(self) -> None:
        try:
            fingerprint = map_fingerprint(self.pbf)
            if fingerprint is None:
                return
            bid = build_id(fingerprint, valhalla_module().__version__)
            with self._lock:
                reg = self._current(fingerprint)
                failed = self._failed_for == bid
            if reg is None and not failed:
                try:
                    build_tiles(self.pbf, self.base)
                except Exception:
                    logger.exception('[Routes] Valhalla: тайлы не собраны — дороги по графу OSM')
                    with self._lock:
                        self._failed_for = bid
                    return
            while True:
                with self._lock:
                    reg = self._current(fingerprint)
                    if reg is None:
                        return
                    car = reg.matrix(PROFILE_CAR, CAR_COSTING)
                    todo = self._todo(reg)
                    if not todo and car._cache_read:
                        self._thread = None
                        return
                car.load()
                for m, keys in todo:
                    m.ensure(keys)
        except Exception:
            logger.exception('[Routes] Valhalla: фоновая подготовка упала — дороги по графу OSM')
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None


def open_valhalla(fallback: RoadDistances | CenterBypassRoads | None = None, base: str | None = None, *,
                  time_only: bool = False, truck_time: bool = False,
                  truck_capacity_kg: float | None = None) -> ValhallaRoads | None:
    """ValhallaRoads по действующей сборке, считающий сам (ensure) — для команд и скриптов; режима
    ROUTES_ROAD_ENGINE не требует. Тайлов нет — None."""
    if not valhalla_supported():
        return None
    base = base or base_dir()
    build = current_build(base)
    if build is None:
        return None
    return ValhallaRoads(_Registry(build, base), PROFILE_CAR, fallback, time_only=time_only, truck_time=truck_time,
                         truck_cost=truck_costing(truck_capacity_kg), compute=True)


# --- Команды ---

def _warm() -> None:
    """Матрицы обоих профилей для всех точек текущего плана: снимок ERP (только чтение) + настройки маршрутов (из копии
    базы: roads._load_bundle_readonly)."""
    sys.path.insert(0, REPO_ROOT)
    os.environ[ENGINE_ENV] = ENGINE_OSM   # import app_v2 вызывает init_app: фон сервера считал бы те же матрицы
    import app_v2  # noqa: F401 — только строка подключения к ERP; сервер не запускается

    from . import evaluate
    from .roads import _load_bundle_readonly
    from .snapshot import load_snapshot
    from .views import _ready_trucks

    db_path = os.environ.get('ROUTES_DB_PATH') or os.path.join(REPO_ROOT, 'route_optimizer.db')
    snap = load_snapshot(app_v2.db.connection_string)
    bundle = _load_bundle_readonly(db_path)
    capacity = max((t.capacity_kg for t in _ready_trucks(snap, bundle).values()), default=None)
    roads = open_valhalla(truck_capacity_kg=capacity)
    if roads is None:
        raise SystemExit('Тайлов нет — сначала: python -m route_optimizer.valhalla_engine build')
    points = [*evaluate.plan_points(snap, bundle, {}), bundle.depot]
    for r in (roads, roads.truck()):
        started = time.perf_counter()
        r.ensure(points)
        print(f'{r.profile} {r.costing}: точек {r.size[0]}, не привязано {r.unsnapped(points)}, '
              f'{"готово" if r.active else "СБОЙ — см. журнал"}; {time.perf_counter() - started:.0f} с')


def main(argv: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:   # ROUTES_VALHALLA_DIR и пр. — из .env сервера, как у app_v2: тайлы там, где их ищет сервер
        from dotenv import load_dotenv
        load_dotenv(os.path.join(REPO_ROOT, '.env'))
    except ImportError:
        pass
    command = argv[0] if argv else ''
    if command == 'build':
        limit = None
        if '--max-seconds' in argv:
            limit = float(argv[argv.index('--max-seconds') + 1])
        # ожидаемое (нет пакета или карты, сборка занята) — одной строкой без трассировки: журнал задачи обновления
        if valhalla_module() is None:
            print('Тайлы не собраны: pyvalhalla не установлен — дороги по графу OSM', file=sys.stderr)
            return 4
        if map_signature(osm_path()) is None:
            print(f'Тайлы не собраны: карты нет ({osm_path()}) — дороги по графу OSM', file=sys.stderr)
            return 4
        try:
            build = build_tiles(force='--force' in argv, max_seconds=limit)
        except BuildBusy as exc:
            print(f'Тайлы не собраны: {exc}', file=sys.stderr)
            return 3
        print(json.dumps(build.info, ensure_ascii=False, indent=1))
    elif command == 'warm':
        _warm()
    elif command == 'status':
        build = current_build()
        print(json.dumps({'mode': engine_mode(), 'truck_time': truck_time_mode(), 'supported': valhalla_supported(),
                          'dir': base_dir(), 'build': build.info if build is not None else None},
                         ensure_ascii=False, indent=1))
    else:
        print('Команды: build [--force] [--max-seconds N] | warm | status')
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

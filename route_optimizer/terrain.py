# -*- coding: utf-8 -*-
"""Рельеф для расхода дизеля (ответ владельца №85, docs/plans/terrain-fuel-plan.md): высоты узлов графа OSM из SRTM.

Без Flask и без БД. Нет numpy/scipy, тайлов или кэша высот — рельефа нет, и расчёт байт в байт прежний.

- Тайлы — SRTM 1″ (~30 м) в формате skadi (AWS Terrain Tiles, бесплатно, без ключа): <DEM_DIR>/N40E044.hgt.gz,
  3601 × 3601 int16 big-endian, строка 0 — северный край тайла; -32768 — нет данных (пусто).
- SRTM — модель поверхности (DSM): дома и деревья дают ложные бугры в 10–30 м на 1–3 пикселя. Растр сглаживается гауссом
  с σ = DEM_SIGMA_M (в пикселях — отдельно по широте и долготе) нормированной свёрткой: пустые пиксели и тайлы, которых
  нет, в среднее не входят. Тайл — с полями соседних тайлов (без ступенек на краях). Выбор σ — замер 07.10.2026 по
  рейсам «Развоза» (DEM_SIGMA_M ниже).
- Высота узла — билинейно по сглаженному растру; узел вне тайлов — NaN (участки к нему — без подъёма).
- Эффективный подъём ребра A → B: подъём − min(спуск, CLIMB_C · длина) (edge_climb): на спуске машина экономит не больше
  расхода ровной дороги (дальше — тормоз, рекуперации нет). Сумма по пути — подъём участка в RoadDistances.climb.
- Кэш высот — <карта>.elev.npz рядом с картой: ключ — отпечаток графа (карта, формат, правила) и параметры DEM
  (elev_key); сменились карта или σ — кэш не годится (рельефа нет до команды dem).

Команда: python -m route_optimizer.roads dem — скачать недостающие тайлы и собрать кэш высот.
"""
from __future__ import annotations

import gzip
import logging
import math
import os
import time
import zlib
from typing import Any, Iterable, Sequence

try:   # необязательные зависимости: без них рельефа нет
    import numpy as np
    from scipy.ndimage import gaussian_filter, map_coordinates
except ImportError:   # pragma: no cover — на сервере без numpy/scipy
    np = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DEM_DIR = os.path.join(REPO_ROOT, 'data', 'roads', 'dem')
DEM_URL = 'https://elevation-tiles-prod.s3.amazonaws.com/skadi/{folder}/{name}.hgt.gz'
DEM_VOID = -32768
# σ гаусса, м. Замер 07.10.2026 — 610 участков планов «Развоза» 29.09–05.10 (4172 км по графу OSM): подъём на км пути
# σ 0 / 50 / 100 / 150 / 250 / 400 м — 17,3 / 13,6 / 12,7 / 12,2 / 11,6 / 11,0 м/км (эффективный, CLIMB_C: 11,1 / 7,8 /
# 7,2 / 6,9 / 6,3 / 5,8). Шум DSM (дома, деревья — 1–3 пикселя) снимают первые ~100 м: −3,7 м/км на 0→50 и −0,9 на 50→100;
# дальше убывание ровное, ~0,5 м/км на каждые +100 м — это срезаются уже настоящие холмы. σ 100 м (полуширина ≈ 235 м,
# по дисперсии — как скользящее окно ≈ 350 м; замер владельца окном 330 м — +13% к ровной дороге) — шум снят, рельеф цел.
DEM_SIGMA_M = 100.0
DEM_TRUNCATE = 3.0     # ядро гаусса — до 3σ
ELEV_FORMAT = 1
CLIMB_C = 0.015        # спуск экономит не больше расхода ровной дороги: ≈ Crr 0,009 + воздух ≈ 0,006 (доля веса)
# Рельеф в выборе плана «Развоза» (ответ владельца по №85, 07.10.2026: «литры — да, план — пока нет»). False — план (рейсы,
# машины, порядок объезда, PyVRP, выравнивание, сравнение вариантов) строится по ровным литрам, ровно как без высот; рельеф
# только добавляется к показанным литрам рейсов, прогнозу и норме «Նորմ և փաստ». True — рельеф и в цели плана (груз вниз,
# пустым вверх). Включить — когда заправки и GPS подтвердят выигрыш (замер 07.10: −0,48% литров на 28 днях, +1,3% км).
IN_PLAN = False
_PX_M = 6371008.8 * math.pi / 180.0 / 3600.0   # 1″ широты, м


def terrain_supported() -> bool:
    return np is not None


def dem_dir() -> str:
    return os.environ.get('ROUTES_DEM_DIR') or DEFAULT_DEM_DIR


def tile_name(lat: float, lon: float) -> str:
    """Имя тайла skadi, в котором точка: N40E044 (юг и запад — S/W)."""
    a, b = math.floor(lat), math.floor(lon)
    return f'{"N" if a >= 0 else "S"}{abs(a):02d}{"E" if b >= 0 else "W"}{abs(b):03d}'


def _tile_origin(name: str) -> tuple[int, int]:
    lat = int(name[1:3]) * (1 if name[0] == 'N' else -1)
    lon = int(name[4:7]) * (1 if name[3] == 'E' else -1)
    return lat, lon


def tiles_for(lat: Any, lon: Any) -> list[str]:
    """Тайлы, в которых лежат точки (узлы графа), по имени."""
    cells = set(zip(np.floor(np.asarray(lat)).astype(int).tolist(), np.floor(np.asarray(lon)).astype(int).tolist()))
    return sorted(tile_name(a + 0.5, b + 0.5) for a, b in cells)


def tile_path(name: str, folder: str | None = None) -> str:
    return os.path.join(folder or dem_dir(), f'{name}.hgt.gz')


def download_tiles(names: Iterable[str], folder: str | None = None) -> list[str]:
    """Скачать тайлы, которых нет в папке (уже скачанные не трогаются). Тайла нет на сервере (море, вне покрытия) —
    пропуск с предупреждением. Ответ — имена скачанных сейчас."""
    import urllib.error
    import urllib.request

    folder = folder or dem_dir()
    os.makedirs(folder, exist_ok=True)
    got = []
    for name in names:
        path = tile_path(name, folder)
        if os.path.exists(path):
            continue
        url = DEM_URL.format(folder=name[:3], name=name)
        tmp = path + '.part'
        try:
            urllib.request.urlretrieve(url, tmp)
            with gzip.open(tmp) as f:   # проверка: распаковывается и квадратный
                _side(len(f.read()))
            os.replace(tmp, path)
            got.append(name)
        except (urllib.error.HTTPError, ValueError, OSError) as e:
            logger.warning('[Routes] Тайл высот %s не скачан: %s', name, e)
            try:
                os.remove(tmp)
            except OSError:
                pass
    return got


def _side(n_bytes: int) -> int:
    side = int(round(math.sqrt(n_bytes / 2)))
    if side * side * 2 != n_bytes or side < 2:
        raise ValueError(f'тайл высот: {n_bytes} байт — не квадрат int16')
    return side


def read_tile(path: str) -> Any | None:
    """Растр тайла float32 (пустые пиксели — NaN); файла нет или он битый — None."""
    try:
        with gzip.open(path) as f:
            raw = f.read()
        side = _side(len(raw))
    except (OSError, EOFError, ValueError):
        if os.path.exists(path):
            logger.warning('[Routes] Тайл высот %s не прочитан', os.path.basename(path), exc_info=True)
        return None
    z = np.frombuffer(raw, dtype='>i2').reshape(side, side).astype(np.float32)
    z[z == DEM_VOID] = np.nan
    return z


def tiles_key(names: Sequence[str], folder: str | None = None) -> str:
    """Отпечаток набора тайлов (имя и размер файла; нет файла — '-'): докачан тайл — кэш высот собирается заново."""
    folder = folder or dem_dir()
    raw = ';'.join(f'{n}:{os.path.getsize(tile_path(n, folder)) if os.path.exists(tile_path(n, folder)) else "-"}'
                   for n in sorted(names))
    return f'{zlib.crc32(raw.encode()):08x}'


def elev_params(sigma_m: float = DEM_SIGMA_M) -> str:
    return f'srtm1|s{sigma_m:g}|t{DEM_TRUNCATE:g}|e{ELEV_FORMAT}'


def elev_key(graph_identity: str, sigma_m: float = DEM_SIGMA_M) -> str:
    """Версия кэша высот: высоты — по индексам узлов графа, поэтому годятся только для того же графа."""
    return f'{graph_identity}|{elev_params(sigma_m)}'


class _Tiles:
    """Тайлы папки с кэшем последних прочитанных (соседи нужны для полей сглаживания)."""

    def __init__(self, folder: str, keep: int = 9):
        self.folder = folder
        self.keep = keep
        self._cache: dict[str, Any] = {}

    def get(self, name: str) -> Any | None:
        if name not in self._cache:
            if len(self._cache) >= self.keep:
                self._cache.pop(next(iter(self._cache)))
            self._cache[name] = read_tile(tile_path(name, self.folder))
        return self._cache[name]


def _smoothed(tiles: _Tiles, name: str, sigma_m: float) -> tuple[Any, int] | None:
    """Сглаженный растр тайла с полями h пикселей от соседних тайлов: (растр (side + 2h)², h); тайла нет — None."""
    z = tiles.get(name)
    if z is None:
        return None
    side = z.shape[0]
    la, lo = _tile_origin(name)
    px = _PX_M * 3600.0 / (side - 1)                 # шаг пикселя по широте, м
    sr = sigma_m / px
    sc = sigma_m / (px * math.cos(math.radians(la + 0.5)))
    h = int(math.ceil(DEM_TRUNCATE * max(sr, sc))) + 1 if sigma_m > 0 else 1
    win = np.full((side + 2 * h, side + 2 * h), np.nan, dtype=np.float32)
    step = side - 1                                  # соседние тайлы делят крайнюю строку и столбец
    # сначала сам тайл: общие с соседями край и угол — его
    for dla, dlo in [(0, 0), *((a, b) for a in (1, 0, -1) for b in (-1, 0, 1) if (a, b) != (0, 0))]:
        t = z if (dla, dlo) == (0, 0) else tiles.get(tile_name(la + dla + 0.5, lo + dlo + 0.5))
        if t is None or t.shape != z.shape:
            continue
        r0, c0 = h - dla * step, h + dlo * step      # где строка 0 / столбец 0 соседа в окне
        ra, rb = max(0, r0), min(win.shape[0], r0 + side)
        ca, cb = max(0, c0), min(win.shape[1], c0 + side)
        if ra < rb and ca < cb:
            part = t[ra - r0:rb - r0, ca - c0:cb - c0]
            cur = win[ra:rb, ca:cb]
            np.copyto(cur, part, where=np.isnan(cur))
    if sigma_m <= 0:
        return win, h
    ok = np.isfinite(win)
    num = gaussian_filter(np.where(ok, win, 0.0).astype(np.float32), (sr, sc), mode='constant', truncate=DEM_TRUNCATE)
    den = gaussian_filter(ok.astype(np.float32), (sr, sc), mode='constant', truncate=DEM_TRUNCATE)
    with np.errstate(invalid='ignore', divide='ignore'):
        out = np.where(den > 0.05, num / den, np.nan).astype(np.float32)
    return out, h


def sample(lat: Any, lon: Any, folder: str | None = None, sigma_m: float = DEM_SIGMA_M) -> Any:
    """Высоты точек (м, float64) по сглаженному растру — билинейно; точки вне тайлов — NaN. Тайлы — по одному."""
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    out = np.full(len(lat), np.nan)
    if not len(lat):
        return out
    tiles = _Tiles(folder or dem_dir())
    names = np.array([tile_name(a, b) for a, b in zip(np.floor(lat) + 0.5, np.floor(lon) + 0.5)])
    for name in sorted(set(names.tolist())):
        got = _smoothed(tiles, name, sigma_m)
        if got is None:
            continue
        win, h = got
        la, lo = _tile_origin(name)
        step = win.shape[0] - 2 * h - 1
        sel = np.flatnonzero(names == name)
        rows = (la + 1 - lat[sel]) * step + h
        cols = (lon[sel] - lo) * step + h
        out[sel] = map_coordinates(win, [rows, cols], order=1, mode='nearest', cval=np.nan)
    return out


def edge_climb(z_from: Any, z_to: Any, km: Any, c: float = CLIMB_C) -> Any:
    """Эффективный подъём ребра, м: подъём − min(спуск, c · длина); высоты нет (NaN) — 0."""
    dz = np.asarray(z_to, dtype=np.float64) - np.asarray(z_from, dtype=np.float64)
    out = np.maximum(dz, 0.0) - np.minimum(np.maximum(-dz, 0.0), c * 1000.0 * np.asarray(km, dtype=np.float64))
    return np.where(np.isfinite(out), out, 0.0)


# --- Кэш высот узлов графа ---

def build_elevation(lat: Any, lon: Any, folder: str | None = None, sigma_m: float = DEM_SIGMA_M) -> Any:
    """Высоты узлов графа (float32, NaN — нет тайла)."""
    started = time.perf_counter()
    z = sample(lat, lon, folder, sigma_m).astype(np.float32)
    logger.info('[Routes] Высоты узлов: %d, без высоты %d; %.1f с', len(z), int(np.sum(~np.isfinite(z))),
                time.perf_counter() - started)
    return z


def save_elevation(path: str, key: str, tiles: str, z: Any) -> None:
    from .roads import _save_npz
    _save_npz(path, key=key, tiles=tiles, z=z)


def stored_elevation(path: str) -> tuple[str, str] | None:
    """(ключ, отпечаток тайлов) кэша высот без чтения массива; нет файла или битый — None."""
    if np is None or not os.path.exists(path):
        return None
    try:
        with open(path, 'rb') as f, np.load(f, allow_pickle=False) as z:
            return str(z['key']), str(z['tiles'])
    except Exception:   # zipfile.BadZipFile, EOFError, KeyError, ValueError, OSError…
        return None


def load_elevation(path: str, key: str) -> Any | None:
    """Высоты узлов из кэша, если он собран для этого графа и этих параметров DEM (key — elev_key); иначе None."""
    if np is None or not os.path.exists(path):
        return None
    try:
        with open(path, 'rb') as f, np.load(f, allow_pickle=False) as z:
            if str(z['key']) != key:
                return None
            return z['z'].astype(np.float32)
    except Exception:
        logger.warning('[Routes] Кэш высот %s не прочитан — без рельефа', os.path.basename(path), exc_info=True)
        return None

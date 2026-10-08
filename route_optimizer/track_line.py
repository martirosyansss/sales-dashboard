# -*- coding: utf-8 -*-
"""Линия трека машины на карте (владелец 08.10, «Մեքենաները առցանց»: «такого не должно быть, это не профессионально» —
петли и «ёжики» у магазина, зигзаги через квартал, прямые сквозь дома). Как у Samsara / Wialon / Routific: стоянка —
одна точка, езда — гладкая линия по дорогам.

Только отображение. Км, расход, визиты, тревоги, отклонение от плана и прочие показатели считаются по своим точкам
(actuals.clean_track, reconstruct) и отсюда ничего не берут. Чистые функции — без Flask и БД; Valhalla — снаружи
(trace: тело запроса trace_attributes → ответ).

Правила:
- стоянки дня (actuals.reconstruct: склад, магазины, «не по плану») — линия проходит через одну точку (середину
  стоянки) дважды: в момент прибытия и в момент отъезда (воспроизведение: машина стоит на месте всю стоянку); точек
  изнутри стоянки нет. Стоянки, которые перекрываются, — одна (середина первой);
- между стоянками — без дрожания на месте: точка со скоростью терминала ниже actuals.STILL_MS; ближе к последней
  оставленной точке, чем max(STILL_M, погрешность хуже из двух, но не больше STILL_ACC_MAX_M); без скорости — стояла по
  смещению (медленнее actuals.STOP_MS за max(шаг, MOVE_STEP_S) и не дальше STAND_JITTER_M, как actuals._still) — не в
  линии. Маленький «ёжик» — точка, от которой трек сразу вернулся к прежней (прежняя и следующая не дальше
  actuals.SPIKE_BACK_M, а до неё от обеих — больше SPIKE_RATIO × расстояния между ними), — тоже не в линии;
- езда делится на куски для привязки к дорогам: перерыв трека дольше GAP_S — разрыв (между кусками прямая: данных
  нет); кусок — не больше CHUNK_POINTS точек, соседние делят крайнюю точку. Деление идёт от начала участка: у растущего
  хвоста дня готовые куски не меняются — кэш привязки (views._LiveTracks) переиспользует их, заново — только последний;
- кусок привязывается к дорогам (Valhalla map matching: trace_attributes, shape_match map_snap) по очереди профилями
  tries (грузовик, затем легковой). Привязка неправдоподобна — длина по дорогам больше LEN_RATIO × длины по точкам +
  LEN_EXTRA_M, вершина линии дальше max(OFF_M, половина шага трека) от трека в то же время, или не привязано больше
  UNMATCHED_SHARE точек. Не вышло ни одним профилем — кусок без привязки (точки после фильтра);
- моменты вершин привязанной линии — по положению привязанных точек вдоль неё (edge_index, distance_along_edge), между
  ними — по длине; не убывают;
- линия не длиннее max_points: Дуглас — Пекер с допуском от 5 м (×2, пока точек больше), точки стоянок остаются всегда.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from statistics import median
from typing import Any, Callable, Mapping, Sequence

from . import actuals as ac
from .geo import Fix, Point, haversine_km

STILL_M = 15.0              # смещение от последней оставленной точки меньше — дрожание на месте…
STILL_ACC_MAX_M = 30.0      # …или меньше погрешности точек, но не больше 30 м (в пути шаг APK 6–15 с — это 25–60 м)
SPIKE_RATIO = 3.0           # «ёжик»: до точки от обеих соседних больше 3 × расстояния между ними
GAP_S = 180.0               # перерыв трека в пути дольше — разрыв: куски не склеиваются привязкой
CHUNK_POINTS = 120          # точек в куске привязки (6–15 с шага — 12–30 мин езды)
MATCH_MIN_POINTS = 3        # кусок короче — без привязки
LEN_RATIO = 1.5             # привязка длиннее 1,5 × пути по точкам + 300 м — неправдоподобна (ушла в объезд)…
LEN_EXTRA_M = 300.0
OFF_M = 150.0               # …вершина дальше 150 м от трека в то же время (и дальше половины шага) — тоже
UNMATCHED_SHARE = 0.3       # …не привязано больше 30 % точек — тоже
GPS_ACC_M = (5.0, 50.0)     # погрешность для привязки (gps_accuracy) — медиана погрешности куска в этих пределах
SEARCH_M = (30.0, 100.0)    # радиус поиска дороги — 3 × погрешность в этих пределах
OFF_WINDOW = 2              # вершина сверяется с отрезками трека ±2 от своего момента
SIMPLIFY_EPS_M = 5.0

TPoint = tuple[float, float, float]   # (широта, долгота, момент — секунды эпохи)
Trace = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class Chunk:
    """Кусок линии: stay — стоянка (две точки в её середине: прибытие и отъезд), иначе — езда (точки после фильтра).
    points — (широта, долгота, момент — секунды эпохи, погрешность, м)."""
    points: tuple[tuple[float, float, float, float], ...]
    stay: bool = False

    @property
    def key(self) -> tuple[Any, ...]:
        """Ключ кэша привязки: тот же кусок (моменты и точки краёв, число точек) — та же линия."""
        p, q = self.points[0], self.points[-1]
        return (self.stay, p[2], q[2], len(self.points), p[0], p[1], q[0], q[1])

    def raw(self) -> list[TPoint]:
        return [(p[0], p[1], p[2]) for p in self.points]


def _m(a: Sequence[float], b: Sequence[float]) -> float:
    return haversine_km((a[0], a[1]), (b[0], b[1])) * 1000.0


def _still(k: tuple[float, float, float, float, float | None], f: tuple[float, float, float, float, float | None]) -> bool:
    """Точка f — дрожание на месте относительно последней оставленной k (правила — в описании модуля)."""
    spd = f[4]
    if spd is not None and spd < ac.STILL_MS:
        return True
    d = _m(k, f)
    if d < max(STILL_M, min(STILL_ACC_MAX_M, max(k[3], f[3]))):
        return True
    return spd is None and d < ac.STOP_MS * max(f[2] - k[2], ac.MOVE_STEP_S) and d <= ac.STAND_JITTER_M


def _despike(run: list[tuple[float, float, float, float, float | None]]) -> list[tuple[float, float, float, float, float | None]]:
    """Без маленьких «ёжиков» (последняя точка остаётся: её следующей ещё нет)."""
    out: list[tuple[float, float, float, float, float | None]] = []
    for i, f in enumerate(run):
        if out and i + 1 < len(run):
            back = _m(out[-1], run[i + 1])
            if back <= ac.SPIKE_BACK_M and min(_m(out[-1], f), _m(f, run[i + 1])) > SPIKE_RATIO * max(back, STILL_M):
                continue
        out.append(f)
    return out


def _split(run: Sequence[tuple[float, float, float, float, float | None]]) -> list[Chunk]:
    """Участок езды → куски привязки (перерыв дольше GAP_S — разрыв; не больше CHUNK_POINTS, соседние делят край)."""
    parts: list[list[tuple[float, float, float, float]]] = []
    for f in run:
        p = (f[0], f[1], f[2], f[3])
        if not parts or p[2] - parts[-1][-1][2] > GAP_S:
            parts.append([p])
        elif len(parts[-1]) >= CHUNK_POINTS:
            parts.append([parts[-1][-1], p])
        else:
            parts[-1].append(p)
    return [Chunk(tuple(x)) for x in parts]


def chunks(pts: Sequence[Fix], stays: Sequence[ac.Stay]) -> list[Chunk]:
    """Точки трека по времени (actuals.clean_track) и стоянки дня (reconstruct) → куски линии по порядку."""
    spans: list[list[Any]] = []
    for s in sorted((s for s in stays if s.center is not None), key=lambda s: (s.arrive, s.leave)):
        if spans and s.arrive <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], s.leave)
        else:
            spans.append([s.arrive, s.leave, s.center])
    out: list[Chunk] = []
    run: list[tuple[float, float, float, float, float | None]] = []
    last: tuple[float, float, float, float, float | None] | None = None

    def flush() -> None:
        if run:
            out.extend(_split(_despike(run)))
            run.clear()

    def add(f: Fix) -> None:
        nonlocal last
        acc = f.accuracy if f.accuracy is not None and math.isfinite(f.accuracy) else 0.0
        p = (f.lat, f.lon, f.at.timestamp(), acc, getattr(f, 'spd', None))
        if last is not None and _still(last, p):
            return
        run.append(p)
        last = p
    i = 0
    for arrive, leave, center in spans:
        while i < len(pts) and pts[i].at < arrive:
            add(pts[i])
            i += 1
        flush()
        a, b = arrive.timestamp(), leave.timestamp()
        out.append(Chunk(((center[0], center[1], a, 0.0), (center[0], center[1], b, 0.0)), True))
        while i < len(pts) and pts[i].at <= leave:
            i += 1
        last = (center[0], center[1], b, 0.0, None)
    while i < len(pts):
        add(pts[i])
        i += 1
    flush()
    return out


# --- привязка к дорогам ---

def _decode6(encoded: str) -> list[Point]:
    """Линия Valhalla (encoded polyline, точность 6 знаков) → точки."""
    out: list[Point] = []
    lat = lon = 0
    i, n = 0, len(encoded)
    while i < n:
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                if i >= n:
                    return out
                b = ord(encoded[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        lat += vals[0]
        lon += vals[1]
        out.append((lat / 1e6, lon / 1e6))
    return out


def _path_m(points: Sequence[Sequence[float]]) -> float:
    return math.fsum(_m(a, b) for a, b in zip(points, points[1:]))


def _seg_m(p: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    """Расстояние от p до отрезка a–b, м (плоское приближение у p: в пределах куска достаточно)."""
    kx = 111320.0 * math.cos(math.radians(p[0]))
    x, y = 0.0, 0.0
    x1, y1 = (a[1] - p[1]) * kx, (a[0] - p[0]) * 110540.0
    x2, y2 = (b[1] - p[1]) * kx, (b[0] - p[0]) * 110540.0
    dx, dy = x2 - x1, y2 - y1
    t = 0.0 if dx == 0 and dy == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x1 + t * dx, y1 + t * dy)


def _timed(res: Mapping[str, Any], raw: Sequence[TPoint]) -> tuple[list[TPoint], float] | None:
    """Ответ trace_attributes → (линия с моментами вершин, доля непривязанных точек); не разобрать — None."""
    shape = _decode6(str(res.get('shape') or ''))
    edges = res.get('edges') or []
    mps = res.get('matched_points') or []
    if len(shape) < 2 or len(mps) != len(raw):
        return None
    cum = [0.0]
    for a, b in zip(shape, shape[1:]):
        cum.append(cum[-1] + _m(a, b))
    xs: list[float] = []
    ts: list[float] = []
    for p, mp in zip(raw, mps):
        ei = mp.get('edge_index') if isinstance(mp, Mapping) else None
        if (not isinstance(mp, Mapping) or mp.get('type') not in ('matched', 'interpolated')
                or not isinstance(ei, int) or not 0 <= ei < len(edges)):
            continue
        e = edges[ei]
        b, en = e.get('begin_shape_index'), e.get('end_shape_index')
        if not (isinstance(b, int) and isinstance(en, int) and 0 <= b <= en < len(shape)):
            continue
        src, tgt = float(e.get('source_percent_along', 0.0)), float(e.get('target_percent_along', 1.0))
        frac = float(mp.get('distance_along_edge', 0.0))
        f = min(1.0, max(0.0, (frac - src) / (tgt - src))) if tgt > src else 0.0
        x = cum[b] + f * (cum[en] - cum[b])
        xs.append(max(x, xs[-1]) if xs else x)   # вдоль линии — не назад
        ts.append(p[2])
    if len(xs) < 2:
        return None
    line: list[TPoint] = []
    for (lat, lon), s in zip(shape, cum):
        j = bisect.bisect_right(xs, s)
        if j == 0:
            t = ts[0]
        elif j == len(xs):
            t = ts[-1]
        else:
            t = ts[j - 1] + (ts[j] - ts[j - 1]) * (s - xs[j - 1]) / (xs[j] - xs[j - 1])
        line.append((lat, lon, t))
    return line, 1.0 - len(xs) / len(raw)


def plausible(line: Sequence[TPoint], raw: Sequence[TPoint]) -> bool:
    """Привязка правдоподобна: не длиннее LEN_RATIO × пути по точкам + LEN_EXTRA_M и каждая вершина не дальше
    max(OFF_M, половина шага) от трека в то же время (отрезки трека ±OFF_WINDOW от её момента)."""
    if len(line) < 2 or len(raw) < 2:
        return False
    if _path_m(line) > LEN_RATIO * _path_m(raw) + LEN_EXTRA_M:
        return False
    times = [p[2] for p in raw]
    for v in line:
        j = bisect.bisect_left(times, v[2])
        lo, hi = max(0, j - OFF_WINDOW), min(len(raw) - 1, j + OFF_WINDOW)
        segs = [(raw[k], raw[k + 1]) for k in range(lo, hi)] or [(raw[lo], raw[lo])]
        d = min(_seg_m(v, a, b) for a, b in segs)
        if d > max(OFF_M, 0.5 * max(_m(a, b) for a, b in segs)):
            return False
    return True


def match_body(chunk: Chunk, profile: str, costing: Mapping[str, Any] | None) -> dict[str, Any]:
    """Тело trace_attributes для куска: моменты точек, погрешность — медиана куска (GPS_ACC_M), радиус поиска — 3 ×
    погрешность (SEARCH_M); только поля, нужные для моментов вершин."""
    acc = min(GPS_ACC_M[1], max(GPS_ACC_M[0], median(p[3] for p in chunk.points)))
    body: dict[str, Any] = {
        'shape': [{'lat': p[0], 'lon': p[1], 'time': int(p[2])} for p in chunk.points],
        'costing': profile, 'shape_match': 'map_snap',
        'trace_options': {'gps_accuracy': round(acc, 1),
                          'search_radius': round(min(SEARCH_M[1], max(SEARCH_M[0], 3 * acc)), 1)},
        'filters': {'attributes': ['shape', 'matched.type', 'matched.edge_index', 'matched.distance_along_edge',
                                   'edge.begin_shape_index', 'edge.end_shape_index'], 'action': 'include'}}
    if costing:
        body['costing_options'] = {profile: dict(costing)}
    return body


def match_chunk(chunk: Chunk, trace: Trace,
                tries: Sequence[tuple[str, Mapping[str, Any] | None]]) -> tuple[list[TPoint], bool] | None:
    """Кусок езды → (линия, привязана ли). Профили tries — по очереди: ошибка запроса (путь не найден), ответ не
    разобрать или привязка неправдоподобна — следующий; не вышло — точки куска (False). trace вернул None (Valhalla
    нет или выключен) — None: результат не окончательный, кэшировать нельзя."""
    raw = chunk.raw()
    if chunk.stay or len(raw) < MATCH_MIN_POINTS:
        return raw, False
    for profile, costing in tries:
        try:
            res = trace(match_body(chunk, profile, costing))
        except Exception:   # лучшая попытка: ValhallaError (нет пути, нет дороги рядом) и пр. — следующий профиль
            continue
        if res is None:
            return None
        got = _timed(res, raw) if isinstance(res, Mapping) else None
        if got is not None and got[1] <= UNMATCHED_SHARE and plausible(got[0], raw):
            return got[0], True
    return raw, False


# --- линия ---

def _fit(pts: Sequence[TPoint], keep: set[int], max_points: int) -> list[TPoint]:
    """Не больше max_points точек: Дуглас — Пекер с допуском от SIMPLIFY_EPS_M (×2, пока больше); первая, последняя и
    точки keep (стоянки) остаются всегда."""
    if len(pts) <= max_points:
        return list(pts)
    kx = 111320.0 * math.cos(math.radians(pts[0][0]))
    xy = [(p[1] * kx, p[0] * 110540.0) for p in pts]

    def dist(i: int, a: int, b: int) -> float:
        (x, y), (x1, y1), (x2, y2) = xy[i], xy[a], xy[b]
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(x - x1, y - y1)
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - x1 - t * dx, y - y1 - t * dy)
    anchors = sorted({0, len(pts) - 1} | {i for i in keep if 0 <= i < len(pts)})
    eps = SIMPLIFY_EPS_M
    while True:
        kept = set(anchors)
        stack = list(zip(anchors, anchors[1:]))
        while stack:
            a, b = stack.pop()
            if b - a < 2:
                continue
            far, d = max(((i, dist(i, a, b)) for i in range(a + 1, b)), key=lambda x: x[1])
            if d > eps:
                kept.add(far)
                stack += [(a, far), (far, b)]
        if len(kept) <= max_points or len(kept) == len(anchors):
            return [pts[i] for i in sorted(kept)]
        eps *= 2


def line(parts: Sequence[Chunk], matched: Mapping[Any, Sequence[TPoint]], max_points: int) -> list[TPoint]:
    """Куски по порядку → линия трека: кусок езды — привязанная линия из matched (по Chunk.key), нет — его точки;
    стоянка — её две точки. Моменты не убывают, подряд одинаковые точки — одна; не длиннее max_points (_fit)."""
    pts: list[TPoint] = []
    keep: set[int] = set()
    for c in parts:
        seg = None if c.stay else matched.get(c.key)
        for p in (seg if seg else c.raw()):
            q = (p[0], p[1], max(p[2], pts[-1][2])) if pts else (p[0], p[1], p[2])
            if not pts or q != pts[-1]:
                pts.append(q)
            if c.stay:
                keep.add(len(pts) - 1)
    return _fit(pts, keep, max_points)

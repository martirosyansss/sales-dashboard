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
- между стоянками — без дрожания на месте (как actuals._still, но от последней оставленной точки): точка дальше
  actuals.STAND_JITTER_M от последней оставленной — всегда в линии (машина сдвинулась: ползком в пробке — линия шагом
  ~75 м, не пропадает); ближе — не в линии точка со скоростью терминала ниже actuals.STOP_MS (режим «стоит» APK);
  ближе к последней оставленной, чем max(STILL_M, погрешность хуже из двух, но не больше STILL_ACC_MAX_M); медленнее
  STOP_MS от неё за max(прошло, MOVE_STEP_S) и не дальше STAND_JITTER_M; и, пока машина стоит (выброшена точка
  «стоит»: скорость ниже STOP_MS или её нет; после стоянки), — всё в STAND_JITTER_M от последней оставленной, какая бы
  ни была скорость: у стоящей машины скорость терминала шумит 0,5–1,5 м/с, а дрожание — десятки метров, и без этого
  короткая остановка (меньше стоянки «не по плану») после привязки — езда туда-обратно. Маленький «ёжик» — точка, от
  которой трек сразу вернулся к прежней (прежняя и следующая не дальше actuals.SPIKE_BACK_M, а до неё от обеих —
  больше SPIKE_RATIO × расстояния между ними), — тоже не в линии;
- кусок езды начинается серединой предыдущей стоянки (в момент отъезда) и кончается серединой следующей (в момент
  прибытия; погрешность STAY_ACC — признак середины стоянки, радиус поиска дороги — STAY_RADIUS_M): соединение стоянки с ездой
  идёт по дорогам, а не прямой через квартал; между стоянками без точек езды — кусок из двух середин (если они дальше
  STILL_M). Готовый кусок от этого не меняется: стоянка позади — её середина и отъезд уже известны;
- езда делится на куски для привязки к дорогам: перерыв трека дольше GAP_S (между точками трека, а не оставленными:
  выброшенная остановка — не перерыв) — разрыв (между кусками прямая: данных нет); кусок — не больше CHUNK_POINTS
  точек, соседние делят крайнюю точку. Деление идёт от начала участка: у растущего хвоста дня готовые куски не
  меняются — кэш привязки (views._LiveTracks) переиспользует их, заново — только последний;
- кусок привязывается к дорогам (Valhalla map matching: trace_attributes, shape_match map_snap) по очереди профилями
  tries (грузовик, затем легковой); штраф поворотов — по умолчанию Valhalla (turn_penalty_factor 300 на дне 08.10
  петлю у дома не убрал, а соединение стоянки с ездой сделал хуже — петли убирает местный объезд, ниже).
  Привязка неправдоподобна — длина по дорогам больше LEN_RATIO × длины по точкам + LEN_EXTRA_M, вершина линии дальше
  max(OFF_M, половина шага трека) от трека в то же время, или не привязано больше UNMATCHED_SHARE точек езды (середины
  стоянок не в счёт); ошибка «путь не найден» (unmatchable: ValhallaError) — тоже. Не вышло ни одним профилем — кусок
  без привязки (точки после фильтра). Другая ошибка (сбой движка) — исключение вызывающему: не окончательно;
- моменты вершин привязанной линии — по положению привязанных точек вдоль неё (edge_index, distance_along_edge), между
  ними — по длине; не убывают;
- местный объезд: между соседними привязанными точками путь по дорогам длиннее LOCAL_RATIO × пути по точкам +
  LOCAL_EXTRA_M (машина заехала во двор и выехала тем же путём, а Valhalla обвёл её вокруг дома) и — если машина
  перед второй точкой не стояла (признак точки куска: перед ней выброшены точки «стоит» или была стоянка) — длиннее,
  чем она могла проехать за шаг: max(скорость терминала у точек, путь / шаг; без скорости — и медиана путь / шаг по
  ±LOCAL_SPEED_STEPS соседним шагам) × шаг × LOCAL_SPEED + LOCAL_EXTRA_M (серпантин, крутой поворот, и на редком
  треке, — дорога, не объезд) — этот отрезок линии — точки трека, остальное — по дорогам;
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
MATCH_MIN_POINTS = 2        # кусок короче — без привязки (Valhalla нужно ≥ 2 точек)
LOCAL_RATIO = 1.5           # между соседними привязанными точками путь по дорогам длиннее 1,5 × пути по точкам + 40 м —
LOCAL_EXTRA_M = 40.0        # объезд квартала вместо разворота на месте: там — точки трека
LOCAL_SPEED = 1.3           # …и, если машина перед второй точкой не стояла, длиннее, чем могла проехать: max(скорость
                            # точек, путь / шаг) × шаг × 1,3 + 40 м (серпантин, крутой поворот — дорога, не объезд)
LOCAL_SPEED_STEPS = 2       # без скорости терминала — ещё медиана путь / шаг по ±2 соседним шагам
STAY_RADIUS_M = 100.0       # середина стоянки в куске езды: дорогу ищем дальше (стоянка — во дворе, на складе)
STAY_ACC = -1.0             # «погрешность» середины стоянки в точках куска — её признак (у точки трека ≥ 0)
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
    points — (широта, долгота, момент — секунды эпохи, погрешность, м — у середины стоянки STAY_ACC, скорость
    терминала, м/с — None, если нет, стояла ли машина перед точкой — выброшены точки «стоит» или была стоянка)."""
    points: tuple[tuple[float, float, float, float, float | None, bool], ...]
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


def _stood(p: Sequence[Any]) -> bool:
    """Машина стояла перед точкой куска (выброшены точки «стоит» или была стоянка)."""
    return len(p) > 5 and bool(p[5])


def _spd(p: Sequence[Any]) -> float | None:
    """Скорость терминала у точки куска (нет — None)."""
    v = p[4] if len(p) > 4 else None
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def _still(k: tuple[float, float, float, float, float | None], f: tuple[float, float, float, float, float | None],
           standing: bool) -> bool:
    """Точка f — дрожание на месте относительно последней оставленной k; standing — машина стоит (правила — в описании
    модуля)."""
    d = _m(k, f)
    if d > ac.STAND_JITTER_M:   # дальше дрожания стоя — машина сдвинулась (и ползком в пробке: линия — шагом ~75 м)
        return False
    spd = f[4]
    if spd is not None and spd < ac.STOP_MS:
        return True
    if d < max(STILL_M, min(STILL_ACC_MAX_M, max(k[3], f[3]))):
        return True
    return standing or d < ac.STOP_MS * max(f[2] - k[2], ac.MOVE_STEP_S)


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
    """Участок езды без перерывов → куски привязки (не больше CHUNK_POINTS, соседние делят край)."""
    parts: list[list[tuple[float, float, float, float, float | None, bool]]] = []
    for f in run:
        p = (f[0], f[1], f[2], f[3], f[4], _stood(f))
        if not parts:
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
    standing = False   # машина стоит: выброшена точка «стоит» или была стоянка (до следующей оставленной точки)
    seen: float | None = None   # момент прежней точки трека (и выброшенной): перерыв данных — по ним, не по оставленным

    start: tuple[float, float, float, float, float | None] | None = None   # середина прежней стоянки в момент отъезда

    def flush(end: tuple[float, float, float, float, float | None] | None = None) -> None:
        nonlocal start
        body = _despike(run) if run else []
        seq = ([start] if start is not None else []) + body + ([end] if end is not None else [])
        if body or (len(seq) == 2 and _m(seq[0], seq[1]) >= STILL_M):
            out.extend(_split(seq))
        run.clear()
        start = None

    def add(f: Fix) -> None:
        nonlocal last, standing, seen
        acc = f.accuracy if f.accuracy is not None and math.isfinite(f.accuracy) else 0.0
        p = (f.lat, f.lon, f.at.timestamp(), acc, getattr(f, 'spd', None))
        if seen is not None and p[2] - seen > GAP_S:   # перерыв трека — разрыв: куски не склеиваются привязкой
            flush()
        seen = p[2]
        if last is not None and _still(last, p, standing):
            standing = standing or p[4] is None or p[4] < ac.STOP_MS
            return
        run.append((*p, standing))   # стояла перед ней — признак для местного объезда
        last, standing = p, False
    i = 0
    for arrive, leave, center in spans:
        while i < len(pts) and pts[i].at < arrive:
            add(pts[i])
            i += 1
        a, b = arrive.timestamp(), leave.timestamp()
        flush((center[0], center[1], a, STAY_ACC, None))
        out.append(Chunk(((center[0], center[1], a, STAY_ACC, None), (center[0], center[1], b, STAY_ACC, None)), True))
        while i < len(pts) and pts[i].at <= leave:
            i += 1
        seen = b
        last, standing = (center[0], center[1], b, STAY_ACC, None), True
        start = last
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


def _timed(res: Mapping[str, Any], pts: Sequence[Sequence[float]]) -> tuple[list[TPoint], float] | None:
    """Ответ trace_attributes по точкам куска pts (Chunk.points: широта, долгота, момент, погрешность — STAY_ACC у
    середины стоянки, скорость терминала) → (линия с моментами вершин, доля непривязанных точек езды); не разобрать — None. Местный объезд (см.
    описание модуля) — отрезок линии между этими привязанными точками заменяется точками трека."""
    shape = _decode6(str(res.get('shape') or ''))
    edges = res.get('edges') or []
    mps = res.get('matched_points') or []
    if len(shape) < 2 or len(mps) != len(pts):
        return None
    cum = [0.0]
    for a, b in zip(shape, shape[1:]):
        cum.append(cum[-1] + _m(a, b))
    xs: list[float] = []
    ts: list[float] = []
    idx: list[int] = []   # номер точки куска у каждой привязанной
    for i, (p, mp) in enumerate(zip(pts, mps)):
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
        idx.append(i)
    if len(xs) < 2:
        return None

    def at(s: float) -> float:
        j = bisect.bisect_right(xs, s)
        if j == 0:
            return ts[0]
        if j == len(xs):
            return ts[-1]
        return ts[j - 1] + (ts[j] - ts[j - 1]) * (s - xs[j - 1]) / (xs[j] - xs[j - 1])
    walk = [0.0]
    for a, b in zip(pts, pts[1:]):
        walk.append(walk[-1] + _m(a, b))
    detours: list[list[Any]] = []   # [x начала, x конца, точка куска начала, точка куска конца]
    def detour(ia: int, ib: int, road: float) -> bool:
        path, dt = walk[ib] - walk[ia], pts[ib][2] - pts[ia][2]
        if road <= LOCAL_RATIO * path + LOCAL_EXTRA_M:
            return False
        if _stood(pts[ib]):   # перед точкой машина стояла (точки «стоит» выброшены) — успеть могла бы что угодно
            return True
        speeds = [x for x in (_spd(pts[ia]), _spd(pts[ib])) if x is not None]
        if not speeds:   # без скорости терминала — по соседним шагам (один шаг мог быть коротким)
            steps = [(walk[k + 1] - walk[k]) / (pts[k + 1][2] - pts[k][2])
                     for k in range(max(0, ia - LOCAL_SPEED_STEPS), min(len(pts) - 1, ib + LOCAL_SPEED_STEPS))
                     if pts[k + 1][2] > pts[k][2]]
            speeds = [median(steps)] if steps else []
        v = max([path / dt if dt > 0 else 0.0] + speeds)
        return road > v * dt * LOCAL_SPEED + LOCAL_EXTRA_M
    for k in range(len(xs) - 1):
        ia, ib = idx[k], idx[k + 1]
        if detour(ia, ib, xs[k + 1] - xs[k]):
            if detours and detours[-1][3] == ia:
                detours[-1][1], detours[-1][3] = xs[k + 1], ib
            else:
                detours.append([xs[k], xs[k + 1], ia, ib])
    line: list[TPoint] = []
    k = 0
    for (lat, lon), s in zip(shape, cum):
        while k < len(detours) and s >= detours[k][1]:   # объезд позади — вместо него точки трека
            line.extend((p[0], p[1], p[2]) for p in pts[detours[k][2]:detours[k][3] + 1])
            k += 1
        if k < len(detours) and s > detours[k][0]:
            continue
        line.append((lat, lon, at(s)))
    for d in detours[k:]:
        line.extend((p[0], p[1], p[2]) for p in pts[d[2]:d[3] + 1])
    real = [i for i, p in enumerate(pts) if len(p) < 4 or p[3] != STAY_ACC]
    unmatched = len(set(real) - set(idx)) / len(real) if real else 0.0
    return line, unmatched


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
    """Тело trace_attributes для куска: моменты точек, погрешность — медиана куска (без середин стоянок; GPS_ACC_M),
    радиус поиска — 3 × погрешность (SEARCH_M), у середины стоянки — STAY_RADIUS_M (radius точки); только поля,
    нужные для моментов вершин."""
    accs = [p[3] for p in chunk.points if p[3] != STAY_ACC]
    acc = min(GPS_ACC_M[1], max(GPS_ACC_M[0], median(accs) if accs else GPS_ACC_M[0]))
    body: dict[str, Any] = {
        'shape': [{'lat': p[0], 'lon': p[1], 'time': int(p[2]), **({'radius': STAY_RADIUS_M} if p[3] == STAY_ACC else {})}
                  for p in chunk.points],
        'costing': profile, 'shape_match': 'map_snap',
        'trace_options': {'gps_accuracy': round(acc, 1),
                          'search_radius': round(min(SEARCH_M[1], max(SEARCH_M[0], 3 * acc)), 1)},
        'filters': {'attributes': ['shape', 'matched.type', 'matched.edge_index', 'matched.distance_along_edge',
                                   'edge.begin_shape_index', 'edge.end_shape_index', 'edge.source_percent_along',
                                   'edge.target_percent_along'], 'action': 'include'}}
    if costing:
        body['costing_options'] = {profile: dict(costing)}
    return body


def match_chunk(chunk: Chunk, trace: Trace, tries: Sequence[tuple[str, Mapping[str, Any] | None]],
                unmatchable: tuple[type[BaseException], ...] = (RuntimeError,)) -> tuple[list[TPoint], bool] | None:
    """Кусок езды → (линия, привязана ли). Профили tries — по очереди: ошибка unmatchable (Valhalla: путь не найден,
    дороги рядом нет — ValhallaError), ответ не разобрать или привязка неправдоподобна — следующий; не вышло — точки
    куска (False, окончательно). trace вернул None (Valhalla нет или выключен) — None: не окончательно, кэшировать
    нельзя. Другие исключения (сбой движка, память) — вызывающему: тоже не окончательно."""
    raw = chunk.raw()
    if chunk.stay or len(raw) < MATCH_MIN_POINTS:
        return raw, False
    for profile, costing in tries:
        try:
            res = trace(match_body(chunk, profile, costing))
        except unmatchable:   # путь не найден — следующий профиль
            continue
        if res is None:
            return None
        got = _timed(res, chunk.points) if isinstance(res, Mapping) else None
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

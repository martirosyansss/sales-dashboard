# -*- coding: utf-8 -*-
"""Факт развоза по GPS-треку машины (learning-loop-plan.md, этап 4; контракт «Առաքիչ» v1.3 §7 п. 3).

Чистые функции — без Flask, БД и ERP; результат зависит только от входа (порядок точек и точек плана не важен).
Вход — трек машины за рабочий день (geo.Fix, момент с зоной), точки плана дня (PlanStop) и склад.

Правила:
- точка трека с погрешностью хуже MAX_ACC_M или вне Армении не используется; одиночный скачок GPS (к точке и
  обратно быстрее MAX_SPEED_KMH или дальше SPIKE_M от обеих соседних, которые рядом друг с другом) — тоже;
- склад — стоянка ≥ MIN_DWELL в DEPOT_RADIUS_M от склада; магазин — стоянка ≥ MIN_DWELL в STOP_RADIUS_M от точки
  плана. Точки плана ближе STOP_RADIUS_M друг к другу — одно «место»: стоянка там относится к точкам места в
  STOP_RADIUS_M от её середины (общая разгрузка — один визит нескольких точек). Кратковременный выход из зоны (скачок
  GPS, разворот) до JITTER_BREAK стоянку не разрывает. Стоянка ≥ OTHER_DWELL в OTHER_RADIUS_M вне склада и точек
  плана — «не по плану» (обед, заправка, чужой магазин; порог выше, чтобы медленная пробка не считалась остановкой);
- рейс — от выезда со склада до возвращения; загрузка рейса — стоянка на складе перед ним, если видно прибытие на
  склад (трек начался не на складе) и она не дольше MAX_LOAD_MIN; рейсы без визитов (заправка, сервис) — не рейсы;
- участок — от отъезда с одной стоянки плана (или склада) до прибытия на следующую в том же рейсе; «чистый»
  (годится для обучения скорости) — без стоянки не по плану и без дыры в треке длиннее LEG_GAP;
- км — geo.track_km (фильтр скачков и якорный фильтр 50 м — как у треков менеджеров);
- опоздание — прибытие (первый визит) позже конца окна приёма; порядок — сколько точек нарушают плановый порядок:
  n − длина наибольшей возрастающей подпоследовательности плановых мест в фактическом порядке.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable, Sequence

from .geo import Fix, Point, haversine_km, is_valid_point, track_km

YEREVAN = timezone(timedelta(hours=4))   # Армения не переводит часы
DEPOT_RADIUS_M = 150.0
STOP_RADIUS_M = 100.0
MIN_DWELL = timedelta(minutes=2)
MAX_ACC_M = 100.0
MAX_SPEED_KMH = 150.0
JITTER_BREAK = timedelta(minutes=3)   # на стоянке терминал пишет раз в 60 с: 1–2 точки мимо — не отъезд
SPIKE_M = 150.0                      # одиночная точка дальше этого от обеих соседних…
SPIKE_BACK_M = 50.0                  # …которые сами рядом друг с другом — скачок GPS
MAX_LOAD_MIN = 120.0
LEG_GAP = timedelta(minutes=10)
OTHER_DWELL = timedelta(minutes=5)   # стоянка не по плану (обед, заправка, чужой магазин)…
OTHER_RADIUS_M = 50.0                # …на месте в 50 м
DEPOT = 'depot'


@dataclass(frozen=True)
class PlanStop:
    """Точка плана дня: key — stop_id терминала; kg — вес по накладной (загружено); delivered_kg — доставлено по
    отметке водителя (None — неизвестно); window — прибыть не раньше/не позже, минуты от полуночи; rank — место в
    плановом порядке объезда машины за день (None — не в плане)."""
    key: str
    customer_id: int | None
    point: Point | None
    kg: float
    delivered_kg: float | None = None
    window: tuple[float, float] | None = None
    rank: int | None = None


@dataclass(frozen=True)
class Stay:
    kind: str                 # 'depot' | 'site' | 'other'
    arrive: datetime
    leave: datetime
    keys: tuple[str, ...] = ()
    seen_arrival: bool = True   # прибытие видно (стоянка началась не с первой точки трека)

    @property
    def minutes(self) -> float:
        return (self.leave - self.arrive).total_seconds() / 60.0


@dataclass(frozen=True)
class Visit:
    keys: tuple[str, ...]     # точки плана этой стоянки (несколько — общая разгрузка)
    arrive: datetime
    leave: datetime
    trip: int
    repeat: bool              # хоть одна точка уже была посещена раньше (повторный заезд)

    @property
    def minutes(self) -> float:
        return (self.leave - self.arrive).total_seconds() / 60.0


@dataclass(frozen=True)
class Leg:
    a: str                    # DEPOT или точка плана
    b: str
    pa: Point
    pb: Point
    depart: datetime
    arrive: datetime
    km_gps: float
    clean: bool

    @property
    def minutes(self) -> float:
        return (self.arrive - self.depart).total_seconds() / 60.0


@dataclass(frozen=True)
class Trip:
    depart: datetime | None   # None — трек начался уже в пути (выезд не виден)
    ret: datetime | None      # None — возвращения нет в треке
    load_min: float | None    # загрузка на складе перед рейсом (None — не видно)
    visits: tuple[int, ...]   # номера в DayActual.visits
    loaded_kg: float          # вес накладных точек, впервые посещённых в рейсе
    km_gps: float


@dataclass(frozen=True)
class DayActual:
    points: int
    km_gps: float
    first: datetime | None
    last: datetime | None
    stays: tuple[Stay, ...] = ()
    visits: tuple[Visit, ...] = ()
    trips: tuple[Trip, ...] = ()
    legs: tuple[Leg, ...] = ()
    unplanned_stays: int = 0
    no_point: tuple[str, ...] = field(default=())   # точки плана без координаты — сопоставить не с чем

    @property
    def visited(self) -> dict[str, datetime]:
        """Точка плана → прибытие при первом визите."""
        out: dict[str, datetime] = {}
        for v in self.visits:
            for k in v.keys:
                out.setdefault(k, v.arrive)
        return out


def _m(a: Point, b: Point) -> float:
    return haversine_km(a, b) * 1000.0


def clean_track(track: Iterable[Fix]) -> list[Fix]:
    """Точки по времени без повторов момента; погрешность ≤ MAX_ACC_M (нет погрешности — не используем), в Армении;
    одиночный скачок (к точке и от неё быстрее MAX_SPEED_KMH, а мимо неё — нет) — отброшен."""
    by_at: dict[datetime, Fix] = {}
    for f in track:
        acc = f.accuracy
        if acc is None or not math.isfinite(acc) or acc > MAX_ACC_M or not is_valid_point(f.lat, f.lon):
            continue
        by_at.setdefault(f.at, f)
    pts = [by_at[t] for t in sorted(by_at)]

    def fast(a: Fix, b: Fix) -> bool:
        h = (b.at - a.at).total_seconds() / 3600.0
        return h > 0 and haversine_km(a.point, b.point) / h > MAX_SPEED_KMH

    def jump(a: Fix, f: Fix, b: Fix) -> bool:
        return ((fast(a, f) and fast(f, b) and not fast(a, b))
                or (_m(a.point, f.point) > SPIKE_M and _m(f.point, b.point) > SPIKE_M
                    and _m(a.point, b.point) < SPIKE_BACK_M))

    out: list[Fix] = []
    for i, f in enumerate(pts):
        if out and i + 1 < len(pts) and jump(out[-1], f, pts[i + 1]):
            continue
        out.append(f)
    return out


def _sites(stops: Sequence[PlanStop]) -> list[list[PlanStop]]:
    """Точки плана с координатой → «места»: связные группы точек ближе STOP_RADIUS_M друг к другу (по ключу)."""
    pts = sorted((s for s in stops if s.point is not None), key=lambda s: s.key)
    parent = list(range(len(pts)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if _m(pts[i].point, pts[j].point) <= STOP_RADIUS_M:   # type: ignore[arg-type]
                parent[root(j)] = root(i)
    groups: dict[int, list[PlanStop]] = {}
    for i, s in enumerate(pts):
        groups.setdefault(root(i), []).append(s)
    return [groups[k] for k in sorted(groups, key=lambda k: groups[k][0].key)]


def _label(p: Point, depot: Point | None, sites: Sequence[Sequence[PlanStop]]) -> int | None:
    """-1 — склад; номер места — ближайшая точка плана в STOP_RADIUS_M; None — ни то, ни другое."""
    if depot is not None and _m(p, depot) <= DEPOT_RADIUS_M:
        return -1
    best: tuple[float, str, int] | None = None
    for i, site in enumerate(sites):
        for s in site:
            d = _m(p, s.point)   # type: ignore[arg-type]
            if d <= STOP_RADIUS_M and (best is None or (d, s.key) < best[:2]):
                best = (d, s.key, i)
    return best[2] if best is not None else None


def _rejoin(runs: list[list], pts: Sequence[Fix], i: int, lab: int) -> bool:
    """Точка i метки lab возвращает в зону прежнего отрезка lab, от конца которого прошло не больше JITTER_BREAK:
    отрезок продлевается до i, промежуточные (скачок GPS, разворот у магазина) убираются. True — продлён."""
    for k in range(len(runs) - 1, -1, -1):
        if pts[i].at - pts[runs[k][2]].at > JITTER_BREAK:
            return False
        if runs[k][0] == lab:
            runs[k][2] = i
            del runs[k + 1:]
            return True
    return False


def _runs(pts: Sequence[Fix], labels: Sequence[int | None]) -> list[list]:
    """Отрезки подряд идущих точек одной метки: [метка, первая, последняя] (_rejoin — через короткий перерыв)."""
    runs: list[list] = []
    for i, lab in enumerate(labels):
        if runs and runs[-1][0] == lab:
            runs[-1][2] = i
        elif lab is None or not _rejoin(runs, pts, i, lab):
            runs.append([lab, i, i])
    return runs


def _other_stays(pts: Sequence[Fix], first: int, last: int) -> list[tuple[int, int]]:
    """Стоянки не по плану в точках first…last (вне склада и точек плана): не меньше OTHER_DWELL в OTHER_RADIUS_M от
    первой точки. Порог выше, чем у магазина: медленная пробка не должна выглядеть остановкой."""
    out, i = [], first
    while i <= last:
        j = i
        while j + 1 <= last and _m(pts[i].point, pts[j + 1].point) <= OTHER_RADIUS_M:
            j += 1
        if pts[j].at - pts[i].at >= OTHER_DWELL:
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _km(pts: Sequence[Fix], start: datetime | None, end: datetime | None) -> float:
    return track_km(f for f in pts if (start is None or f.at >= start) and (end is None or f.at <= end))


def reconstruct(track: Iterable[Fix], stops: Sequence[PlanStop], depot: Point | None) -> DayActual:
    """Трек и точки плана дня → факт: стоянки, визиты, рейсы, участки, км."""
    pts = clean_track(track)
    no_point = tuple(sorted(s.key for s in stops if s.point is None))
    if not pts:
        return DayActual(0, 0.0, None, None, no_point=no_point)
    sites = _sites(stops)
    labels = [_label(f.point, depot, sites) for f in pts]
    stays: list[Stay] = []
    for lab, a, b in _runs(pts, labels):
        if lab is None:
            stays += [Stay('other', pts[i].at, pts[j].at) for i, j in _other_stays(pts, a, b)]
            continue
        if pts[b].at - pts[a].at < MIN_DWELL:
            continue
        if lab == -1:
            stays.append(Stay('depot', pts[a].at, pts[b].at, (), a > 0))
            continue
        center = (median(f.lat for f in pts[a:b + 1]), median(f.lon for f in pts[a:b + 1]))
        near = [s.key for s in sites[lab] if _m(center, s.point) <= STOP_RADIUS_M]   # type: ignore[arg-type]
        keys = tuple(sorted(near)) if near else (min(sites[lab], key=lambda s: (_m(center, s.point), s.key)).key,)
        stays.append(Stay('site', pts[a].at, pts[b].at, keys, a > 0))
    stays.sort(key=lambda s: (s.arrive, s.kind, s.keys))
    by_key = {s.key: s for s in stops}

    visits: list[Visit] = []
    trips: list[Trip] = []
    seen: set[str] = set()
    cur: dict | None = None
    last_depot: Stay | None = None

    def close(ret: datetime | None) -> None:
        nonlocal cur
        if cur is not None:
            trips.append(Trip(cur['depart'], ret, cur['load'], tuple(cur['visits']), cur['kg'],
                              _km(pts, cur['depart'] or pts[0].at, ret)))
        cur = None

    for st in stays:
        if st.kind == 'depot':
            close(st.arrive)
            last_depot = st
        elif st.kind == 'site':
            if cur is None:
                load = (last_depot.minutes if last_depot is not None and last_depot.seen_arrival
                        and last_depot.minutes <= MAX_LOAD_MIN else None)
                cur = {'depart': last_depot.leave if last_depot is not None else None, 'load': load,
                       'visits': [], 'kg': 0.0}
                last_depot = None
            fresh = [k for k in st.keys if k not in seen]
            cur['kg'] += math.fsum(by_key[k].kg for k in fresh)
            cur['visits'].append(len(visits))
            visits.append(Visit(st.keys, st.arrive, st.leave, len(trips), len(fresh) < len(st.keys)))
            seen.update(st.keys)
    close(None)

    others = [s for s in stays if s.kind == 'other']
    legs: list[Leg] = []
    for trip in trips:
        anchors: list[tuple[str, Point, datetime | None, datetime | None]] = []   # (ключ, точка, прибытие, отъезд)
        if trip.depart is not None and depot is not None:
            anchors.append((DEPOT, depot, None, trip.depart))
        for vi in trip.visits:
            v = visits[vi]
            anchors.append((v.keys[0], by_key[v.keys[0]].point, v.arrive, v.leave))   # type: ignore[arg-type]
        if trip.ret is not None and depot is not None:
            anchors.append((DEPOT, depot, trip.ret, None))
        for (ka, pa, _, dep), (kb, pb, arr, _) in zip(anchors, anchors[1:]):
            if dep is None or arr is None or arr <= dep:
                continue
            inside = [f for f in pts if dep <= f.at <= arr]
            gap = any(b.at - a.at > LEG_GAP for a, b in zip(inside, inside[1:]))
            stop = any(o.arrive < arr and o.leave > dep for o in others)
            legs.append(Leg(ka, kb, pa, pb, dep, arr, track_km(inside), not gap and not stop and len(inside) >= 2))
    return DayActual(len(pts), track_km(pts), pts[0].at, pts[-1].at, tuple(stays), tuple(visits), tuple(trips),
                     tuple(legs), len(others), no_point)


def local_minutes(t: datetime) -> float:
    """Момент → минуты от полуночи по Еревану."""
    x = t.astimezone(YEREVAN)
    return x.hour * 60 + x.minute + x.second / 60.0


def order_changes(ranks: Sequence[int]) -> int:
    """Плановые места точек в фактическом порядке → сколько точек нарушают плановый порядок (n − длина наибольшей
    строго возрастающей подпоследовательности)."""
    tails: list[int] = []
    for r in ranks:
        i = bisect.bisect_left(tails, r)
        if i == len(tails):
            tails.append(r)
        else:
            tails[i] = r
    return len(ranks) - len(tails)


@dataclass(frozen=True)
class VisitMetrics:
    planned: int              # точек в плане дня машины (с координатой)
    visited: int              # из них посещено (стоянка ≥ MIN_DWELL)
    with_window: int          # посещённых с окном приёма
    on_time: int              # из них прибыли не позже конца окна
    late_minutes: float       # сумма опозданий, мин
    order_changes: int        # точек не в плановом порядке
    ordered: int              # посещённых с плановым местом


def visit_metrics(actual: DayActual, stops: Sequence[PlanStop]) -> VisitMetrics:
    """Окна приёма и порядок объезда по первому визиту каждой точки плана."""
    first = actual.visited
    by_key = {s.key: s for s in stops}
    with_window = on_time = 0
    late = 0.0
    for key, at in first.items():
        s = by_key.get(key)
        if s is None or s.window is None:
            continue
        with_window += 1
        over = local_minutes(at) - s.window[1]
        if over <= 0:
            on_time += 1
        else:
            late += over
    order = [by_key[k].rank for k, _ in sorted(first.items(), key=lambda kv: (kv[1], kv[0]))
             if k in by_key and by_key[k].rank is not None]
    return VisitMetrics(sum(1 for s in stops if s.point is not None), sum(1 for k in first if k in by_key),
                        with_window, on_time, round(late, 1), order_changes(order), len(order))   # type: ignore[arg-type]


def load_profile(actual: DayActual, stops: Sequence[PlanStop]) -> list[tuple[datetime, float, float]]:
    """Груз на борту по участкам рейсов: [(начало участка, км по GPS, кг на борту)]. Рейс выезжает с весом накладных
    его точек; после визита на борту остаётся минус доставленное (неизвестно — вес накладной). Для расхода по
    загрузке (learning.fuel)."""
    by_key = {s.key: s for s in stops}
    out = []
    for trip in actual.trips:
        on_board = trip.loaded_kg
        legs = [g for g in actual.legs if trip.depart is not None and g.depart >= trip.depart
                and (trip.ret is None or g.arrive <= trip.ret)] if trip.depart is not None else []
        done: set[str] = set()
        visit_by_arrive = {actual.visits[i].arrive: actual.visits[i] for i in trip.visits}
        for g in sorted(legs, key=lambda g: g.depart):
            out.append((g.depart, g.km_gps, max(0.0, on_board)))
            v = visit_by_arrive.get(g.arrive)
            if v is not None:
                for k in v.keys:
                    if k not in done:
                        done.add(k)
                        s = by_key[k]
                        on_board -= s.delivered_kg if s.delivered_kg is not None else s.kg
    return out

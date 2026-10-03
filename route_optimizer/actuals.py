# -*- coding: utf-8 -*-
"""Факт развоза по GPS-треку машины (learning-loop-plan.md, этап 4; контракт «Առաքիչ» v1.3 §7 п. 3).

Чистые функции — без Flask, БД и ERP; результат зависит только от входа (порядок точек и точек плана не важен).
Вход — трек машины за рабочий день (TrackFix: момент с зоной, скорость терминала), точки плана дня (PlanStop) и склад.

Правила:
- точка трека с погрешностью хуже MAX_ACC_M или вне Армении не используется; одиночный скачок GPS (к точке и
  обратно быстрее MAX_SPEED_KMH или дальше SPIKE_M от обеих соседних, которые рядом друг с другом) — тоже;
- склад — стоянка ≥ MIN_DWELL в DEPOT_RADIUS_M от склада; магазин — стоянка ≥ MIN_DWELL в STOP_RADIUS_M от точки
  плана. Кратковременный выход из зоны (скачок GPS, разворот) до JITTER_BREAK стоянку не разрывает — но не через
  стоянку ≥ MIN_DWELL у другой точки плана (короткий заезд к соседнему магазину остаётся визитом);
- точки плана ближе STOP_RADIUS_M друг к другу — одно «место». Стоянка там относится к точкам по отметке доставки
  водителя (момент `delivery` в пределах стоянки ± DELIVERY_SLACK); без отметок — к ближайшей точке, если середина
  стоянки ближе к ней, чем половина расстояния до соседней точки места, иначе — ко всем точкам места в STOP_RADIUS_M
  (общая разгрузка). «Обслуживающий» визит точки — стоянка её доставки, без доставки — первый визит; остальные
  заезды к точке — repeat (в обучение разгрузки не идут). Стоянка ≥ OTHER_DWELL в OTHER_RADIUS_M вне склада и точек
  плана — «не по плану» (обед, заправка, чужой магазин; порог выше, чтобы медленная пробка не считалась остановкой);
- рейс — от выезда со склада до возвращения; загрузка рейса — стоянка на складе перед ним, если видно прибытие на
  склад (трек начался не на складе) и она не дольше MAX_LOAD_MIN; рейсы без визитов (заправка, сервис) — не рейсы.
  Груз рейса — вес накладных точек, обслуженных в рейсе, и точек без стоянки по GPS (доставка отмечена во время
  рейса или место точки в плане — между обслуженными точками рейса);
- участок — от отъезда с одной стоянки плана (или склада) до прибытия на следующую в том же рейсе; «чистый»
  (годится для обучения скорости) — без стоянки не по плану и без дыры в треке длиннее LEG_GAP;
- км — geo.track_km по точкам движения: стоянка — одна точка (её середина), точки вне стоянок со скоростью
  терминала ниже STILL_MS — тоже стоянка (дрожание GPS на месте км не добавляет);
- опоздание — прибытие обслуживающего визита позже конца окна приёма (минуты от полуночи рабочего дня: после
  полуночи — 24:xx); окно жёсткое (№36): доставка раньше начала окна (момент отметки, без неё — прибытие) — тоже мимо;
  порядок — сколько точек нарушают плановый порядок: n − длина наибольшей возрастающей подпоследовательности.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
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
MAX_LOAD_MIN = 60.0                  # стоянка на складе дольше — не только загрузка (обед, бумаги, ожидание)
LEG_GAP = timedelta(minutes=10)
OTHER_DWELL = timedelta(minutes=5)   # стоянка не по плану (обед, заправка, чужой магазин)…
OTHER_RADIUS_M = 50.0                # …на месте в 50 м
STILL_MS = 0.5                       # скорость терминала ниже, м/с, — машина стоит
DELIVERY_SLACK = timedelta(minutes=10)   # отметка доставки — в пределах стоянки ± 10 мин
DEPOT = 'depot'


@dataclass(frozen=True)
class TrackFix(Fix):
    """Точка трека терминала: + скорость, м/с (None — терминал не прислал)."""
    spd: float | None = None


@dataclass(frozen=True)
class PlanStop:
    """Точка плана дня: key — stop_id терминала; kg — вес по накладной (загружено); delivered_kg — доставлено по
    отметке водителя (None — неизвестно); window — прибыть не раньше/не позже, минуты от полуночи рабочего дня; rank —
    место в плановом порядке объезда машины за день (None — не в плане); delivered_at — момент отметки доставки."""
    key: str
    customer_id: int | None
    point: Point | None
    kg: float
    delivered_kg: float | None = None
    window: tuple[float, float] | None = None
    rank: int | None = None
    delivered_at: datetime | None = None


@dataclass(frozen=True)
class Stay:
    kind: str                 # 'depot' | 'site' | 'other'
    arrive: datetime
    leave: datetime
    keys: tuple[str, ...] = ()
    seen_arrival: bool = True   # прибытие видно (стоянка началась не с первой точки трека)
    center: Point | None = None

    @property
    def minutes(self) -> float:
        return (self.leave - self.arrive).total_seconds() / 60.0


@dataclass(frozen=True)
class Visit:
    keys: tuple[str, ...]     # точки плана этой стоянки (несколько — общая разгрузка)
    arrive: datetime
    leave: datetime
    trip: int
    repeat: bool              # не обслуживающий заезд хоть одной точки (повторный или доставка — на другой стоянке)

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
    load_min: float | None    # стоянка на складе перед рейсом (None — не видно)
    visits: tuple[int, ...]   # номера в DayActual.visits
    loaded_kg: float          # вес накладных точек рейса (обслуженных и unseen)
    km_gps: float
    unseen: tuple[str, ...] = ()   # точки рейса без стоянки по GPS (нет фикса, нет координаты)

    @property
    def arrive_depot(self) -> datetime | None:
        """Прибытие на склад перед рейсом (выезд − стоянка); не видно — None."""
        return self.depart - timedelta(minutes=self.load_min) if self.depart and self.load_min is not None else None


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
    no_point: tuple[str, ...] = field(default=())    # точки плана без координаты — сопоставить не с чем
    served: tuple[tuple[str, int], ...] = ()          # (точка, номер обслуживающего визита) по точке

    @property
    def visited(self) -> dict[str, datetime]:
        """Точка плана → прибытие обслуживающего визита."""
        return {k: self.visits[i].arrive for k, i in self.served}


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
    отрезок продлевается до i, промежуточные (скачок GPS, разворот у магазина) убираются. Между ними стоянка ≥
    MIN_DWELL с другой меткой (заезд к соседнему магазину или на склад) — не продлевается. True — продлён."""
    for k in range(len(runs) - 1, -1, -1):
        lab_k, a, b = runs[k]
        if pts[i].at - pts[b].at > JITTER_BREAK:
            return False
        if lab_k == lab:
            runs[k][2] = i
            del runs[k + 1:]
            return True
        if lab_k is not None and pts[b].at - pts[a].at >= MIN_DWELL:
            return False
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


def _center(seg: Sequence[Fix]) -> Point:
    return median(f.lat for f in seg), median(f.lon for f in seg)


def _nearest_keys(site: Sequence[PlanStop], center: Point) -> tuple[str, ...]:
    """Стоянка места без отметок доставки: ближайшая точка, если середина стоянки ближе к ней, чем половина расстояния
    до соседней точки места; иначе — все точки места в STOP_RADIUS_M (общая разгрузка)."""
    ranked = sorted(site, key=lambda s: (_m(center, s.point), s.key))   # type: ignore[arg-type]
    near = ranked[0]
    if len(ranked) == 1:
        return (near.key,)
    gap = min(_m(near.point, o.point) for o in ranked[1:])   # type: ignore[arg-type]
    if _m(center, near.point) < gap / 2:   # type: ignore[arg-type]
        return (near.key,)
    group = tuple(sorted(s.key for s in site if _m(center, s.point) <= STOP_RADIUS_M))   # type: ignore[arg-type]
    return group or (near.key,)


def _assign(site: Sequence[PlanStop], stays: Sequence[Stay]) -> tuple[list[tuple[str, ...]], dict[str, int]]:
    """Стоянки одного места (по времени) → (точки каждой стоянки, точка → номер её обслуживающей стоянки). Точка с
    отметкой доставки — к стоянке, ближайшей по времени к отметке (не дальше DELIVERY_SLACK); остальные стоянки —
    _nearest_keys. Обслуживающая стоянка точки без отметки — первая, где она есть."""
    assigned: dict[int, list[str]] = {}
    service: dict[str, int] = {}
    for s in site:
        if s.delivered_at is None:
            continue
        best: tuple[timedelta, int] | None = None
        for j, x in enumerate(stays):
            gap = max(timedelta(0), x.arrive - s.delivered_at, s.delivered_at - x.leave)
            if gap <= DELIVERY_SLACK and (best is None or gap < best[0]):
                best = (gap, j)
        if best is not None:
            assigned.setdefault(best[1], []).append(s.key)
            service[s.key] = best[1]
    keys = [tuple(sorted(assigned[j])) if j in assigned else _nearest_keys(site, x.center)   # type: ignore[arg-type]
            for j, x in enumerate(stays)]
    for j, ks in enumerate(keys):
        for k in ks:
            service.setdefault(k, j)
    return keys, service


def _moving(pts: Sequence[Fix], stays: Sequence[Stay]) -> list[Fix]:
    """Точки для км: стоянка — две точки в её середине (прибытие и отъезд: между ними 0 км), точки внутри стоянок — нет;
    вне стоянок точка со скоростью терминала ниже STILL_MS — нет (следующая точка движения соединяется с прежней)."""
    spans = sorted((s.arrive, s.leave) for s in stays)
    starts = [a for a, _ in spans]
    out: list[Fix] = []
    for f in pts:
        k = bisect.bisect_right(starts, f.at) - 1
        if k >= 0 and spans[k][0] <= f.at <= spans[k][1]:
            continue
        spd = getattr(f, 'spd', None)
        if spd is not None and spd < STILL_MS:
            continue
        out.append(f)
    for s in stays:
        if s.center is not None:
            out += [Fix(s.arrive, s.center[0], s.center[1], 1.0), Fix(s.leave, s.center[0], s.center[1], 1.0)]
    out.sort(key=lambda f: f.at)
    return out


def _km(moving: Sequence[Fix], start: datetime | None, end: datetime | None) -> float:
    return track_km(f for f in moving if (start is None or f.at >= start) and (end is None or f.at <= end))


def reconstruct(track: Iterable[Fix], stops: Sequence[PlanStop], depot: Point | None) -> DayActual:
    """Трек и точки плана дня → факт: стоянки, визиты, рейсы, участки, км."""
    pts = clean_track(track)
    no_point = tuple(sorted(s.key for s in stops if s.point is None))
    if not pts:
        return DayActual(0, 0.0, None, None, no_point=no_point)
    sites = _sites(stops)
    labels = [_label(f.point, depot, sites) for f in pts]
    stays: list[Stay] = []
    by_site: dict[int, list[int]] = {}
    for lab, a, b in _runs(pts, labels):
        if lab is None:
            stays += [Stay('other', pts[i].at, pts[j].at, (), True, _center(pts[i:j + 1]))
                      for i, j in _other_stays(pts, a, b)]
            continue
        if pts[b].at - pts[a].at < MIN_DWELL:
            continue
        if lab != -1:
            by_site.setdefault(lab, []).append(len(stays))
        stays.append(Stay('depot' if lab == -1 else 'site', pts[a].at, pts[b].at, (), a > 0, _center(pts[a:b + 1])))
    service_at: dict[str, datetime] = {}
    for lab, idx in by_site.items():
        keys, service = _assign(sites[lab], [stays[i] for i in idx])
        for i, ks in zip(idx, keys):
            stays[i] = replace(stays[i], keys=ks)
        service_at.update({k: stays[idx[j]].arrive for k, j in service.items()})
    stays.sort(key=lambda s: (s.arrive, s.kind, s.keys))
    by_key = {s.key: s for s in stops}
    moving = _moving(pts, stays)

    visits: list[Visit] = []
    served: dict[str, int] = {}
    trips: list[Trip] = []
    cur: dict | None = None
    last_depot: Stay | None = None

    def close(ret: datetime | None) -> None:
        nonlocal cur
        if cur is not None:
            trips.append(Trip(cur['depart'], ret, cur['load'], tuple(cur['visits']), cur['kg'],
                              _km(moving, cur['depart'] or pts[0].at, ret)))
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
            here = [k for k in st.keys if service_at.get(k) == st.arrive]
            cur['kg'] += math.fsum(by_key[k].kg for k in here)
            served.update({k: len(visits) for k in here})
            cur['visits'].append(len(visits))
            visits.append(Visit(st.keys, st.arrive, st.leave, len(trips), len(here) < len(st.keys)))
    close(None)
    trips = _unseen(trips, visits, stops, served)

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
            legs.append(Leg(ka, kb, pa, pb, dep, arr, _km(moving, dep, arr),
                            not gap and not stop and len(inside) >= 2))
    return DayActual(len(pts), track_km(moving), pts[0].at, pts[-1].at, tuple(stays), tuple(visits), tuple(trips),
                     tuple(legs), len(others), no_point, tuple(sorted(served.items())))


def _unseen(trips: list[Trip], visits: Sequence[Visit], stops: Sequence[PlanStop],
            served: dict[str, int]) -> list[Trip]:
    """Точки плана без обслуживающей стоянки (нет фикса, нет координаты) — в груз рейса: доставка отмечена во время
    рейса (± DELIVERY_SLACK), иначе место точки в плане — между обслуженными точками рейса. Первый подходящий рейс."""
    by_key = {s.key: s for s in stops}
    extra: list[list[str]] = [[] for _ in trips]
    for s in sorted(stops, key=lambda s: s.key):
        if s.key in served:
            continue
        for n, t in enumerate(trips):
            if s.delivered_at is not None:
                start = t.depart or visits[t.visits[0]].arrive
                if start - DELIVERY_SLACK <= s.delivered_at and (t.ret is None or s.delivered_at <= t.ret + DELIVERY_SLACK):
                    extra[n].append(s.key)
                    break
                continue
            ranks = [by_key[k].rank for i in t.visits for k, j in served.items() if j == i and by_key[k].rank is not None]
            if s.rank is not None and ranks and min(ranks) < s.rank < max(ranks):
                extra[n].append(s.key)
                break
    return [replace(t, loaded_kg=t.loaded_kg + math.fsum(by_key[k].kg for k in ks), unseen=tuple(ks)) if ks else t
            for t, ks in zip(trips, extra)]


def local_minutes(t: datetime) -> float:
    """Момент → минуты от полуночи по Еревану."""
    x = t.astimezone(YEREVAN)
    return x.hour * 60 + x.minute + x.second / 60.0


def day_minutes(day: date, t: datetime) -> float:
    """Момент → минуты от полуночи рабочего дня day (Ереван): после полуночи — больше 1440."""
    return (t - datetime(day.year, day.month, day.day, tzinfo=YEREVAN)).total_seconds() / 60.0


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
    visited: int              # из них обслужено (стоянка ≥ MIN_DWELL)
    with_window: int          # обслуженных с окном приёма
    on_time: int              # из них в окне: прибыли не позже конца и доставили не раньше начала
    late_minutes: float       # сумма опозданий, мин
    order_changes: int        # точек не в плановом порядке
    ordered: int              # обслуженных с плановым местом
    early: int = 0            # доставлено раньше начала окна (окно жёсткое, №36)


def stop_marks(actual: DayActual, stops: Sequence[PlanStop], day: date) -> dict[str, dict]:
    """Точка плана → {arrive, leave, late_min, early, window} обслуживающего визита. Минуты — от полуночи рабочего дня
    day (прибытие после полуночи — 24:xx, позже окна). Окно жёсткое (№36): раньше начала окна — момент отметки
    доставки (машина могла ждать у магазина), без отметки — прибытие."""
    by_key = {s.key: s for s in stops}
    out: dict[str, dict] = {}
    for key, vi in actual.served:
        v, s = actual.visits[vi], by_key.get(key)
        mark = {'arrive': v.arrive, 'leave': v.leave, 'late_min': 0.0, 'early': False, 'window': False}
        if s is not None and s.window is not None:
            lo, hi = s.window
            service = day_minutes(day, s.delivered_at if s.delivered_at is not None else v.arrive)
            mark.update(window=True, late_min=round(max(0.0, day_minutes(day, v.arrive) - hi), 1),
                        early=math.isfinite(lo) and service < lo)
        out[key] = mark
    return out


def visit_metrics(actual: DayActual, stops: Sequence[PlanStop], day: date) -> VisitMetrics:
    """Окна приёма (stop_marks) и порядок объезда по обслуживающему визиту каждой точки плана."""
    first = actual.visited
    by_key = {s.key: s for s in stops}
    marks = [m for k, m in stop_marks(actual, stops, day).items() if k in by_key and m['window']]
    with_window = len(marks)
    early = sum(1 for m in marks if m['early'])
    late = math.fsum(m['late_min'] for m in marks)
    on_time = sum(1 for m in marks if m['late_min'] <= 0 and not m['early'])
    order = [by_key[k].rank for k, _ in sorted(first.items(), key=lambda kv: (kv[1], kv[0]))
             if k in by_key and by_key[k].rank is not None]
    return VisitMetrics(sum(1 for s in stops if s.point is not None), sum(1 for k in first if k in by_key),
                        with_window, on_time, round(late, 1), order_changes(order), len(order),   # type: ignore[arg-type]
                        early)


def load_profile(actual: DayActual, stops: Sequence[PlanStop]) -> list[tuple[datetime, float, float]]:
    """Груз на борту по участкам рейсов: [(начало участка, км по GPS, кг на борту)]. Рейс выезжает с весом накладных
    своих точек; на обслуживающем визите точки на борту остаётся минус доставленное (неизвестно — вес накладной);
    точка без стоянки по GPS — в момент отметки доставки, без неё — на следующем визите рейса. Для расхода по
    загрузке (learning.fuel)."""
    by_key = {s.key: s for s in stops}
    served = dict(actual.served)
    out = []
    for trip in actual.trips:
        if trip.depart is None:
            continue
        events: list[tuple[datetime, float]] = []
        ranked: list[tuple[int, datetime]] = []      # (место в плане, прибытие) обслуженных точек рейса
        for vi in trip.visits:
            v = actual.visits[vi]
            for k in v.keys:
                if served.get(k) == vi:
                    s = by_key[k]
                    events.append((v.arrive, s.delivered_kg if s.delivered_kg is not None else s.kg))
                    if s.rank is not None:
                        ranked.append((s.rank, v.arrive))
        end = trip.ret or actual.visits[trip.visits[-1]].arrive
        for k in trip.unseen:
            s = by_key[k]
            after = [at for r, at in ranked if s.rank is not None and r > s.rank]
            when = s.delivered_at or (min(after) if after else end)
            events.append((when, s.delivered_kg if s.delivered_kg is not None else s.kg))
        events.sort(key=lambda e: e[0])
        on_board, done = trip.loaded_kg, 0
        legs = sorted((g for g in actual.legs if g.depart >= trip.depart and (trip.ret is None or g.arrive <= trip.ret)),
                      key=lambda g: g.depart)
        for g in legs:
            out.append((g.depart, g.km_gps, max(0.0, on_board)))
            while done < len(events) and events[done][0] <= g.arrive:
                on_board -= events[done][1]
                done += 1
    return out


def simplify(points: Sequence[Point], max_points: int = 1500) -> list[Point]:
    """Линия трека для карты: Дуглас — Пекер с допуском от 5 м (×2, пока точек больше max_points); первая и последняя
    точки остаются. Расстояния — в плоском приближении (метры) у первой точки: в пределах Армении достаточно."""
    if len(points) <= max_points:
        return list(points)
    kx = 111320.0 * math.cos(math.radians(points[0][0]))
    xy = [(p[1] * kx, p[0] * 110540.0) for p in points]

    def dist(i: int, a: int, b: int) -> float:
        (x, y), (x1, y1), (x2, y2) = xy[i], xy[a], xy[b]
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(x - x1, y - y1)
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - x1 - t * dx, y - y1 - t * dy)

    eps = 5.0
    while True:
        keep = {0, len(points) - 1}
        stack = [(0, len(points) - 1)]
        while stack:
            a, b = stack.pop()
            if b - a < 2:
                continue
            far, d = max(((i, dist(i, a, b)) for i in range(a + 1, b)), key=lambda x: x[1])
            if d > eps:
                keep.add(far)
                stack += [(a, far), (far, b)]
        if len(keep) <= max_points:
            return [points[i] for i in sorted(keep)]
        eps *= 2

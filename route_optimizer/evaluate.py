# -*- coding: utf-8 -*-
"""Оценка текущего плана: день менеджера, Монте-Карло, парк машин, агрегаты, факт GPS, калибровка.

Чистая логика — без Flask и без БД. Единицы: км, минуты, кг, драмы (AMD), вероятности 0..1.
Всё детерминировано: у каждого визита свой поток случайных чисел с seed = 42 + crc32(клиент|день
недели|цель) — общие случайные числа для «было» и «стало» (visit_seed).
"""
from __future__ import annotations

import math
import random
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import fmean, median
from typing import TYPE_CHECKING, Any, Collection, Iterable, Mapping, Sequence

from . import demand as dm
from . import fleet as fl
from . import status as cst
from .frequency import plural
from .geo import (GPS_MAX_ACCURACY_M, Coord, Fix, Point, haversine_km, in_city, is_valid_point,
                  resolve_coord, track_km, usable_fixes)
from .plan import WEEKDAY_LABELS, CurrentPlan, PlanDay, delivery_weekday
from .tsp import Distance, route_order

if TYPE_CHECKING:
    from .roads import RoadDistances
    from .snapshot import Snapshot
    from .store import Bundle

BASE_SEED = 42
MC_REVENUE_SAMPLES = 500   # низкий сезон: выручка дня и рейса
DAY_BELOW_MIN_P = 0.5      # «слабый» день: P(выручка ≥ минимума) < 0.5

FACT_WINDOW_DAYS = 56      # факт по GPS — последние 8 недель
ACTIVE_WINDOW_DAYS = 56    # «живой» шаблон: заказы или визиты агента за последние 8 недель
FACT_MIN_VISITS = 5
FACT_MIN_FIXES = 30        # работа и паузы дня — только по дням с треком
CALIB_WINDOW_DAYS = 42     # калибровка — последние 6 недель
CALIB_MIN_VISITS = 8
CALIB_MIN_FIXES = 30
CALIB_MIN_STRAIGHT_KM = 1.0
# «Движение» по треку: сегмент ≥ 50 м со скоростью 6–120 км/ч (медленнее — ходьба между
# магазинами или джиттер на стоянке, быстрее — скачок GPS)
SPEED_MIN_KMH = 6.0
SPEED_MAX_KMH = 120.0
MOVE_MIN_KM = 0.05
SPEED_MAX_GAP_MIN = 10.0        # скорость — по соседним точкам не дальше 10 мин друг от друга
SPEED_MIN_MOVING_HOURS = 1.0    # скорость класса (город / область) — только при ≥ 1 ч движения
STOP_MAX_GAP_MIN = 15.0         # стоянки и работа — по сегментам до 15 мин; разрывы длиннее — паузы
VISIT_CALIB_MIN_DAYS = 5        # средняя длительность визита по GPS — медиана по ≥ 5 дням

# Нормы дорог «авто»: настройка → калибровка по GPS → значение по умолчанию.
# (по умолчанию, мин, макс) — диапазоны те же, что проверяет store (_NUMERIC).
ROAD_NORMS: dict[str, tuple[float, float, float]] = {
    'detour_factor': (1.3, 1.0, 3.0),
    'speed_city_kmh': (25.0, 5.0, 120.0),
    'speed_region_kmh': (45.0, 5.0, 120.0),
}
ROAD_NORM_DIGITS = {'detour_factor': 2, 'speed_city_kmh': 1, 'speed_region_kmh': 1}  # как в настройках

# Длительность визита «авто» по классам размера: (вес относительно среднего магазина, минут по
# умолчанию — без калибровки по GPS). Диапазон — как проверяет store (_NUMERIC), шаг — 0,5 мин.
VISIT_NORMS: dict[str, tuple[float, float]] = {
    dm.SIZE_SMALL: (0.7, 7.0),
    dm.SIZE_MEDIUM: (1.0, 10.0),
    dm.SIZE_LARGE: (2.0, 20.0),
}
VISIT_MIN_RANGE = (1.0, 120.0)
VISIT_MIN_STEP = 0.5

SEASON_FALLBACK_MONTHS = 3  # порогам не подошёл ни один месяц — берём 3 крайних по индексу
MONTH_SHORT = ('янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек')

DEFAULT_MANAGER_FUEL = 'petrol'
SETTINGS_URL = '/routes/settings'


# --- Факт по GPS (ACTUALROUTES) ---

@dataclass(frozen=True)
class ActualVisit:
    """Чек-ин визита из мобильного приложения (ACTUALROUTES)."""
    customer_id: int
    agent_id: int
    day: date
    start: datetime
    end: datetime | None
    lat: float | None
    lon: float | None
    accuracy: float | None
    result: str  # '01' — результативный


def visit_point_ok(v: ActualVisit) -> bool:
    return is_valid_point(v.lat, v.lon) and (v.accuracy is None or v.accuracy <= GPS_MAX_ACCURACY_M)


@dataclass(frozen=True)
class Fact:
    """Фактический день менеджера по чек-инам (средние по дням с ≥ 5 визитами).

    hours — окно «первый визит → конец последнего» по всем дням факта. По дням с треком окно
    делится на работу (езда и стоянки) и паузы (разрывы трека > 15 мин): work_hours и
    pause_hours — средние по этим track_days дням; трека нет — None.
    """
    days: int
    visits_per_day: float
    day_start_min: float   # минуты от полуночи
    day_end_min: float
    hours: float
    productive_share: float
    work_hours: float | None = None
    pause_hours: float | None = None
    track_days: int = 0


def _minute_of_day(t: datetime) -> float:
    return t.hour * 60 + t.minute + t.second / 60.0


def visit_span(visits: Sequence[ActualVisit]) -> tuple[datetime, datetime]:
    """Рабочее время дня по чек-инам: первый fSTARTTIME → последний fENDTIME (нет конца — начало)."""
    start = min(v.start for v in visits)
    end = max([v.end for v in visits if v.end is not None] + [v.start for v in visits])
    return start, end


def manager_facts(visits: Iterable[ActualVisit], since: date, until: date,
                  min_visits: int = FACT_MIN_VISITS,
                  day_fixes: Mapping[tuple[int, date], Sequence[Fix]] | None = None,
                  min_fixes: int = FACT_MIN_FIXES) -> dict[int, Fact]:
    """Факт по дням [since, until): начало — первый fSTARTTIME, конец — последний fENDTIME.

    day_fixes — точки трека по дням (fixes_by_day). В днях с ≥ min_fixes точками окно дня
    делится на работу и паузы (work_pause_minutes). Трек снимка короче окна факта (45 дней
    против 8 недель), поэтому work_hours и pause_hours — средние по своим дням (track_days).
    """
    by_day: dict[tuple[int, date], list[ActualVisit]] = {}
    for v in visits:
        if since <= v.day < until:
            by_day.setdefault((v.agent_id, v.day), []).append(v)
    rows: dict[int, list[tuple[int, float, float, float, int]]] = {}
    splits: dict[int, list[tuple[float, float]]] = {}   # (работа, паузы), мин — дни с треком
    for (agent_id, day), vs in by_day.items():
        if len(vs) < min_visits:
            continue
        start, end = visit_span(vs)
        productive = sum(1 for v in vs if v.result == '01')
        rows.setdefault(agent_id, []).append((
            len(vs), _minute_of_day(start), _minute_of_day(end),
            (end - start).total_seconds() / 3600.0, productive))
        fixes = day_fixes.get((agent_id, day), ()) if day_fixes else ()
        if len(fixes) >= min_fixes:
            splits.setdefault(agent_id, []).append(work_pause_minutes(fixes, start, end))
    facts = {}
    for agent_id, rs in rows.items():
        visits_n = sum(r[0] for r in rs)
        split = splits.get(agent_id, [])
        facts[agent_id] = Fact(
            days=len(rs),
            visits_per_day=visits_n / len(rs),
            day_start_min=fmean(r[1] for r in rs),
            day_end_min=fmean(r[2] for r in rs),
            hours=fmean(r[3] for r in rs),
            productive_share=sum(r[4] for r in rs) / visits_n,
            work_hours=fmean(w for w, _ in split) / 60.0 if split else None,
            pause_hours=fmean(p for _, p in split) / 60.0 if split else None,
            track_days=len(split),
        )
    return facts


def active_agents(orders: Iterable[dm.Order], visits: Iterable[ActualVisit],
                  since: date, until: date) -> frozenset[int]:
    """Агенты с работой в [since, until): ≥ 1 заказ (агент заказа) или ≥ 1 визит ACTUALROUTES.
    Шаблон без работы по умолчанию не входит в расчёт (выбор владельца в настройках важнее)."""
    return frozenset({o.agent_id for o in orders if since <= o.date < until}
                     | {v.agent_id for v in visits if since <= v.day < until})


# --- Калибровка по GPS ---

@dataclass(frozen=True)
class DayTrack:
    """День менеджера для калибровки: км по прямой между визитами и км трека между ними,
    минуты стоянок между первым и последним визитом."""
    agent_id: int
    day: date
    visits: int                    # визиты с GPS-точкой
    straight_km: float
    track_km: float
    fixes: tuple[Fix, ...]         # отобранные точки трека за весь день
    visits_all: int                # все визиты дня (и без GPS-точки)
    stationary_min: float | None   # стоянки трека в visit_span всех визитов (stationary_minutes)


def fixes_by_day(fixes_by_agent: Mapping[int, Sequence[Fix]]) -> dict[tuple[int, date], list[Fix]]:
    out: dict[tuple[int, date], list[Fix]] = {}
    for agent_id, fixes in fixes_by_agent.items():
        for f in usable_fixes(fixes):
            out.setdefault((agent_id, f.at.date()), []).append(f)
    return out


def day_tracks(visits: Iterable[ActualVisit], day_fixes: Mapping[tuple[int, date], Sequence[Fix]],
               since: date, until: date, min_visits: int = CALIB_MIN_VISITS,
               min_fixes: int = CALIB_MIN_FIXES) -> list[DayTrack]:
    """Дни [since, until) с ≥ min_visits визитов с GPS-точкой и ≥ min_fixes точек трека.

    straight_km — по прямой между точками визитов в порядке fSTARTTIME;
    track_km — км трека от начала первого до начала последнего визита;
    stationary_min — стоянки трека от начала первого до конца последнего визита дня (считаются
    все визиты: у клиента без GPS-точки менеджер тоже стоит).
    """
    by_day: dict[tuple[int, date], list[ActualVisit]] = {}
    for v in visits:
        if since <= v.day < until:
            by_day.setdefault((v.agent_id, v.day), []).append(v)
    out = []
    for key in sorted(by_day):
        day_visits = by_day[key]
        vs = sorted((v for v in day_visits if visit_point_ok(v)), key=lambda v: v.start)
        fixes = day_fixes.get(key, ())
        if len(vs) < min_visits or len(fixes) < min_fixes:
            continue
        pts = [(v.lat, v.lon) for v in vs]
        straight = sum(haversine_km(a, b) for a, b in zip(pts, pts[1:]))
        t0, t1 = vs[0].start, vs[-1].start
        track = track_km([f for f in fixes if t0 <= f.at <= t1])
        start, end = visit_span(day_visits)
        stationary = stationary_minutes([f for f in fixes if start <= f.at <= end])
        out.append(DayTrack(key[0], key[1], len(vs), straight, track, tuple(fixes),
                            len(day_visits), stationary))
    return out


def _is_moving(km: float, hours: float) -> bool:
    """Сегмент трека — движение: ≥ 50 м и 6–120 км/ч (hours > 0 проверяет вызывающий)."""
    return km >= MOVE_MIN_KM and SPEED_MIN_KMH <= km / hours <= SPEED_MAX_KMH


def stationary_minutes(fixes: Sequence[Fix]) -> float | None:
    """Минуты стоянок по треку (точки по времени): Σ dt соседних точек с 0 < dt ≤ 15 мин, где
    сегмент — не движение (_is_moving): стоянка у клиента и ходьба между магазинами. Разрывы
    трека длиннее 15 мин не считаются. None — ни одного такого сегмента (трека в отрезке нет)."""
    total = 0.0
    measured = False
    for a, b in zip(fixes, fixes[1:]):
        minutes = (b.at - a.at).total_seconds() / 60.0
        if minutes <= 0 or minutes > STOP_MAX_GAP_MIN:
            continue
        measured = True
        if not _is_moving(haversine_km(a.point, b.point), minutes / 60.0):
            total += minutes
    return total if measured else None


def work_pause_minutes(fixes: Sequence[Fix], start: datetime, end: datetime) -> tuple[float, float]:
    """(работа, паузы) в окне [start, end], минуты; fixes — точки трека дня по времени.

    Работа — езда и стоянки: сегменты соседних точек с 0 < dt ≤ 15 мин (каждый — движение или
    стоянка по _is_moving, как в calibrate_speeds и stationary_minutes), обрезанные по окну.
    Паузы — остальное окно: разрывы трека длиннее 15 мин и время без точек в начале или в конце
    окна. Работа + паузы = окно.
    """
    span = max(0.0, (end - start).total_seconds() / 60.0)
    work = 0.0
    for a, b in zip(fixes, fixes[1:]):
        minutes = (b.at - a.at).total_seconds() / 60.0
        if minutes <= 0 or minutes > STOP_MAX_GAP_MIN:
            continue
        lo, hi = max(a.at, start), min(b.at, end)
        if hi > lo:
            work += (hi - lo).total_seconds() / 60.0
    return work, max(0.0, span - work)   # max: без «−0,0» от округления при окне без разрывов


def calibrate_detour(days: Iterable[DayTrack],
                     min_straight_km: float = CALIB_MIN_STRAIGHT_KM) -> tuple[float | None, int]:
    """Извилистость = медиана (км трека / км по прямой) по дням; дни короче 1 км по прямой — вне."""
    ratios = [d.track_km / d.straight_km for d in days if d.straight_km >= min_straight_km]
    return (median(ratios) if ratios else None), len(ratios)


def calibrate_speeds(days: Iterable[DayTrack], center: Point,
                     radius_km: float) -> tuple[float | None, float | None]:
    """Скорости движения (город, область) = Σ км / Σ часов движущихся сегментов трека.

    Сегмент — соседние точки с интервалом 0 < dt ≤ 10 мин; в скорость входит только движение
    (_is_moving: ≥ 50 м, 6–120 км/ч) — ходьба между магазинами и стоянки её не занижают,
    а долгий участок весит больше короткого. Город — оба конца в радиусе города.
    Меньше часа движения в классе — None (калибровки нет).
    """
    km = {True: 0.0, False: 0.0}      # ключ — оба конца в городе
    hours = {True: 0.0, False: 0.0}
    for d in days:
        for a, b in zip(d.fixes, d.fixes[1:]):
            h = (b.at - a.at).total_seconds() / 3600.0
            if h <= 0 or h * 60 > SPEED_MAX_GAP_MIN:
                continue
            dist = haversine_km(a.point, b.point)
            if not _is_moving(dist, h):
                continue
            city = in_city(a.point, center, radius_km) and in_city(b.point, center, radius_km)
            km[city] += dist
            hours[city] += h

    def speed(city: bool) -> float | None:
        return km[city] / hours[city] if hours[city] >= SPEED_MIN_MOVING_HOURS else None

    return speed(True), speed(False)


def calibrate_visit_minutes(days: Iterable[DayTrack],
                            min_days: int = VISIT_CALIB_MIN_DAYS) -> float | None:
    """Средняя длительность визита по GPS, мин: медиана по дням (минуты стоянок от первого до
    последнего визита / визиты дня). Дней с треком меньше min_days — None (калибровки нет)."""
    per_visit = [d.stationary_min / d.visits_all for d in days
                 if d.stationary_min is not None and d.visits_all > 0]
    return median(per_visit) if len(per_visit) >= min_days else None


@dataclass(frozen=True)
class Calibration:
    detour_factor: float | None
    speed_city_kmh: float | None
    speed_region_kmh: float | None
    days_used: int
    visit_min_avg: float | None = None   # стоянка на визит по GPS, мин (calibrate_visit_minutes)


def calibrate(visits: Iterable[ActualVisit], fixes_by_agent: Mapping[int, Sequence[Fix]],
              since: date, until: date, center: Point, radius_km: float) -> Calibration:
    days = day_tracks(visits, fixes_by_day(fixes_by_agent), since, until)
    detour, used = calibrate_detour(days)
    city, region = calibrate_speeds(days, center, radius_km)
    return Calibration(detour, city, region, used, calibrate_visit_minutes(days))


# --- Модель визита и нормы ---

@dataclass(frozen=True)
class Draw:
    """Спрос визита в сезоне: вероятность заказа и прошлые заказы (выручка, кг)."""
    p: float
    values: tuple[tuple[float, float], ...]

    @property
    def expected_revenue(self) -> float:
        if not self.values:
            return 0.0
        return self.p * fmean(v[0] for v in self.values)


NO_DRAW = Draw(0.0, ())


@dataclass(frozen=True)
class VisitModel:
    customer_id: int
    point: Point | None            # координата ЭТОГО визита (клиент + адрес шаблона)
    minutes: float
    low: Draw
    year: Draw
    peak: Draw
    erp_no: int = 0                # № в шаблоне ERP: позиция по fROWNUM с 1 (fROWNUM — с 0 и с пропусками)
    coord_source: str = 'none'     # источник point: manual | erp | gps | none


def road_norms(s: Mapping[str, Any], calib: Calibration | None) -> dict[str, tuple[float, str]]:
    """Нормы дорог: {ключ: (значение, источник)}.

    Источник: manual — число в настройках; gps — калибровка по трекам снимка (с точностью, как
    в настройках, и в допустимом диапазоне: «Применить» закрепляет ровно это число);
    default — калибровки нет, берём 1.3 / 25 / 45.
    """
    out: dict[str, tuple[float, str]] = {}
    for key, (default, lo, hi) in ROAD_NORMS.items():
        manual = s.get(key)
        gps = getattr(calib, key) if calib is not None else None
        if manual is not None:
            out[key] = (float(manual), 'manual')
        elif gps is not None:
            out[key] = (min(hi, max(lo, round(float(gps), ROAD_NORM_DIGITS[key]))), 'gps')
        else:
            out[key] = (default, 'default')
    return out


def _half_up(x: float, step: float = VISIT_MIN_STEP) -> float:
    """Округление до шага (0,5 мин); половина — вверх, а не «к чётному» как у round()."""
    return math.floor(x / step + 0.5) * step


def visit_norms(s: Mapping[str, Any], visit_min_avg: float | None,
                shares: Mapping[str, float]) -> dict[str, tuple[float, str]]:
    """Длительность визита по классам размера: {visit_min_<класс>: (минуты, источник)}.

    gps — калибровка: m = visit_min_avg / Σ(вес × доля класса), класс = вес × m (0,7 / 1 / 2),
    т.е. средний визит плана равен средней стоянке на визит по GPS; округление до 0,5 мин,
    в пределах [1, 120] («Применить» закрепляет ровно это число);
    manual — число в настройках, перекрывает только свой класс;
    default — калибровки нет: 7 / 10 / 20.
    shares — доли визитов плана по классам (size_shares); визитов нет — m = visit_min_avg.
    """
    lo, hi = VISIT_MIN_RANGE
    denom = sum(weight * shares.get(size, 0.0) for size, (weight, _) in VISIT_NORMS.items()) or 1.0
    out: dict[str, tuple[float, str]] = {}
    for size, (weight, default) in VISIT_NORMS.items():
        key = f'visit_min_{size}'
        manual = s.get(key)
        if manual is not None:
            out[key] = (float(manual), 'manual')
        elif visit_min_avg is not None:
            out[key] = (min(hi, max(lo, _half_up(weight * visit_min_avg / denom))), 'gps')
        else:
            out[key] = (default, 'default')
    return out


def size_shares(plan: CurrentPlan, sizes: Mapping[int, str], included: Collection[int],
                workdays: Collection[int]) -> dict[str, float]:
    """Доли визитов плана по классам размера (sizes: клиент → класс) — в рабочие дни недели
    у менеджеров в расчёте. Визитов нет — все доли 0."""
    agents, days_ok = set(included), set(workdays)
    counts = Counter(sizes[v.customer_id] for d in plan.days
                     if d.agent_id in agents and d.weekday in days_ok for v in d.visits)
    total = sum(counts.values())
    return {size: counts[size] / total if total else 0.0 for size in VISIT_NORMS}


@dataclass(frozen=True)
class Norms:
    work_minutes: float
    detour: float
    speed_city_kmh: float
    speed_region_kmh: float
    city_center: Point
    city_radius_km: float
    min_day_revenue: float
    min_trip_revenue: float
    roads: RoadDistances | None = field(default=None, compare=False, repr=False)   # None — по прямой

    @classmethod
    def from_settings(cls, s: Mapping[str, Any], calib: Calibration | None = None,
                      roads: RoadDistances | None = None) -> Norms:
        """Нормы расчёта; извилистость и скорости — действующие (road_norms), а не «как в поле»."""
        h1, m1 = map(int, s['work_start'].split(':'))
        h2, m2 = map(int, s['work_end'].split(':'))
        road = road_norms(s, calib)
        return cls(
            work_minutes=float((h2 * 60 + m2) - (h1 * 60 + m1)),
            detour=road['detour_factor'][0],
            speed_city_kmh=road['speed_city_kmh'][0],
            speed_region_kmh=road['speed_region_kmh'][0],
            city_center=(float(s['city_center_lat']), float(s['city_center_lon'])),
            city_radius_km=float(s['city_radius_km']),
            min_day_revenue=float(s['min_day_revenue']),
            min_trip_revenue=float(s['min_trip_revenue']),
            roads=roads,
        )

    def km(self, a: Point, b: Point) -> float:
        """Км участка — единая функция расстояния расчёта: по дорогам; карты нет, точка дальше
        0,5 км от дороги или пути нет — по прямой × извилистость."""
        if self.roads is not None:
            d = self.roads.km(a, b)
            if d is not None:
                return d
        return haversine_km(a, b) * self.detour

    @property
    def distance(self) -> Distance | None:
        """Функция расстояния для порядка объезда и рейсов (tsp): km; без дорог — None (tsp считает
        по прямой, извилистость — постоянный множитель, порядок тот же, что и раньше)."""
        return self.km if self.roads is not None else None


def visit_seed(customer_id: int, weekday: int, purpose: str) -> int:
    """Стабильный seed потока случайных чисел визита (не зависит от PYTHONHASHSEED и версии Python).

    Ключ — клиент, день недели и цель (low | truck_year | truck_peak). Неделя цикла, менеджер и место
    визита в дне в ключ не входят: у визита, который не меняется, одни и те же случайные числа
    в плане «было» (W = 1) и «стало» (W = 2) и в обеих неделях цикла — общие случайные числа,
    и неизменный день не даёт шума Монте-Карло в «было → стало»."""
    return BASE_SEED + zlib.crc32(f'{customer_id}|{weekday}|{purpose}'.encode('utf-8'))


def visit_uniforms(customer_id: int, weekday: int, purpose: str,
                   n: int) -> tuple[list[float], list[float]]:
    """Случайные числа визита на n проб: («заказал ли»: u < p, «какой из прошлых заказов»)."""
    rand = random.Random(visit_seed(customer_id, weekday, purpose)).random
    orders = [rand() for _ in range(n)]
    return orders, [rand() for _ in range(n)]


def route_metrics(points: Sequence[Point], home: Point | None, norms: Norms) -> tuple[float, float]:
    """(км, минуты в пути) маршрута менеджера: дом → точки → дом; без дома — от первой до последней.

    km участка — norms.km (по дорогам, иначе по прямой × извилистость); время участка =
    км / скорость × 60, скорость городская, если оба конца в городе, иначе областная.
    """
    path = [home, *points, home] if home is not None else list(points)
    km = minutes = 0.0
    for a, b in zip(path, path[1:]):
        leg = norms.km(a, b)
        both_city = (in_city(a, norms.city_center, norms.city_radius_km)
                     and in_city(b, norms.city_center, norms.city_radius_km))
        speed = norms.speed_city_kmh if both_city else norms.speed_region_kmh
        km += leg
        minutes += leg / speed * 60.0
    return km, minutes


# --- Монте-Карло ---
# У каждого визита свой поток случайных чисел (visit_uniforms): «заказал ли» и «какой из прошлых
# заказов» в пробе k — k-е числа потока визита. Исход визита в пробе не зависит ни от остальных
# визитов дня, ни от недели цикла и менеджера: дни «было» и «стало» с одними и теми же визитами
# дают одинаковые цифры (общие случайные числа), а меняются только от изменённых визитов.

def _outcomes(draws: Sequence[Draw], customers: Sequence[int], weekday: int, purpose: str,
              n: int) -> tuple[list[float], list[float], list[int]]:
    """n проб дня: выручка, кг и число заказов в каждой пробе.

    customers — клиенты визитов в порядке draws; порядок — канонический (по id клиента), чтобы суммы
    с плавающей точкой не зависели от порядка визитов в плане. Визиту без шанса заказа числа не нужны."""
    rev = [0.0] * n
    kg = [0.0] * n
    count = [0] * n
    for d, cid in zip(draws, customers):
        k = len(d.values)
        if not k or d.p <= 0.0:
            continue
        p, values = d.p, d.values
        orders, picks = visit_uniforms(cid, weekday, purpose, n)
        for s in range(n):
            if orders[s] < p:
                r, g = values[int(picks[s] * k)]
                rev[s] += r
                kg[s] += g
                count[s] += 1
    return rev, kg, count


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Квантиль с линейной интерполяцией (как numpy 'linear'); список уже отсортирован."""
    if not sorted_values:
        return 0.0
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


@dataclass(frozen=True)
class RevenueStats:
    expected: float      # точное ожидание Σ p × средний заказ (без шума Монте-Карло)
    p10: float
    p90: float
    p_ge_min: float      # P(выручка дня ≥ min_day_revenue)
    p_no_orders: float   # P(заказов нет) — рейса не будет
    p_poor_trip: float   # P(заказы есть, но выручка < min_trip_revenue)


def revenue_stats(draws: Sequence[Draw], customers: Sequence[int], weekday: int, n: int,
                  min_day_revenue: float, min_trip_revenue: float) -> RevenueStats:
    """Выручка дня низкого сезона: n проб Монте-Карло (потоки визитов — _outcomes, цель «low»)."""
    revenues, _, counts = _outcomes(draws, customers, weekday, 'low', n)
    ge = sum(1 for r in revenues if r >= min_day_revenue)
    none = counts.count(0)
    poor = sum(1 for r, c in zip(revenues, counts) if c and r < min_trip_revenue)
    revenues.sort()
    return RevenueStats(
        expected=math.fsum(d.expected_revenue for d in draws),
        p10=quantile(revenues, 0.1), p90=quantile(revenues, 0.9),
        p_ge_min=ge / n, p_no_orders=none / n, p_poor_trip=poor / n,
    )


@dataclass(frozen=True)
class DayResult:
    day: PlanDay
    workday: bool
    flags: tuple[str, ...]
    visits: int
    visits_no_coords: int
    drive_min: float
    visit_min: float
    plan_min: float           # весь день: work_min + commute_min
    work_min: float           # у клиентов: визиты + дорога между ними (сравнимо с фактом GPS)
    commute_min: float        # дорога из дома к первому клиенту и от последнего домой
    overtime_min: float
    km: float
    km_rownum: float          # тот же день в порядке fROWNUM — для сверки
    liters: float
    low: RevenueStats
    revenue_year: float
    revenue_peak: float       # ожидаемая выручка дня в пик (лето)
    stops: tuple[VisitModel, ...]  # в порядке объезда; визиты без координат — в конце

    @property
    def customer_ids(self) -> tuple[int, ...]:
        return tuple(v.customer_id for v in self.stops)


def evaluate_day(day: PlanDay, visits: Sequence[VisitModel], *, home: Point | None, norms: Norms,
                 manager_l100: float, workday: bool) -> DayResult:
    """Метрики дня менеджера (§6): маршрут, время, литры, выручка низкого сезона. Рейсы машин — по
    дням доставки всех менеджеров сразу (fleet.evaluate_fleet), а не по дню менеджера.

    Порядок объезда — NN + 2-opt от дома, а не fROWNUM: по GPS менеджеры порядок fROWNUM
    не соблюдают, и км «по fROWNUM» завышаются до 3 раз (A002/9: 423 км/день против 136 км
    трека). Км в порядке fROWNUM сохраняется отдельно (km_rownum) для сверки.
    """
    located = [v for v in visits if v.point is not None]
    rownum_points = [v.point for v in located]
    order = route_order(rownum_points, home, norms.distance)
    points = [rownum_points[i] for i in order]
    km, drive = route_metrics(points, home, norms)
    _, drive_between = route_metrics(points, None, norms)   # от первого клиента до последнего
    km_rownum, _ = route_metrics(rownum_points, home, norms)
    visit_min = float(sum(v.minutes for v in visits))
    plan_min = drive + visit_min
    work_min = visit_min + drive_between
    # Окно рабочего дня владельца (9:00–18:00) включает дорогу из дома: переработка — по всему дню
    overtime = max(0.0, plan_min - norms.work_minutes)
    # Монте-Карло — по визитам в каноническом порядке (id клиента) с потоками visit_uniforms:
    # цифры дня зависят от набора визитов, а не от недели цикла, менеджера и порядка в плане
    mc = sorted(visits, key=lambda v: v.customer_id)
    low = revenue_stats([v.low for v in mc], [v.customer_id for v in mc], day.weekday,
                        MC_REVENUE_SAMPLES, norms.min_day_revenue, norms.min_trip_revenue)
    flags = []
    if round(overtime) > 0:
        flags.append('overtime')
    if home is None:
        flags.append('no_home')
    if not workday:
        flags.append('off_day')
    if delivery_weekday(day.weekday)[1]:
        flags.append('sunday_order')
    stops = [located[i] for i in order] + [v for v in visits if v.point is None]
    return DayResult(
        day=day, workday=workday, flags=tuple(flags), visits=len(visits),
        visits_no_coords=len(visits) - len(located), drive_min=drive, visit_min=visit_min,
        plan_min=plan_min, work_min=work_min, commute_min=max(0.0, plan_min - work_min),
        overtime_min=overtime, km=km, km_rownum=km_rownum,
        liters=km * manager_l100 / 100.0,
        low=low, revenue_year=math.fsum(v.year.expected_revenue for v in visits),
        revenue_peak=math.fsum(v.peak.expected_revenue for v in visits),
        stops=tuple(stops),
    )


# --- Сезон и модели клиентов ---

@dataclass(frozen=True)
class Season:
    low: list[int]
    peak: list[int]
    source: str               # auto | manual | mixed
    index: dict[int, float] | None
    detected_low: list[int]
    detected_peak: list[int]
    fallback: tuple[str, ...] = ()   # 'low'/'peak': порогам не подошёл ни один месяц — взяты крайние


def _extreme_months(index: Mapping[int, float], exclude: Collection[int], highest: bool) -> list[int]:
    """SEASON_FALLBACK_MONTHS месяцев с самым низким (или высоким) индексом, кроме exclude."""
    ranked = sorted((m for m in index if m not in exclude),
                    key=lambda m: (-index[m] if highest else index[m], m))
    return sorted(ranked[:SEASON_FALLBACK_MONTHS])


def resolve_season(index: dict[int, float] | None, s: Mapping[str, Any]) -> Season:
    """Месяцы сезонов: ручной выбор важнее авто (авто-набор теряет месяцы ручного).

    Индекс есть, а сезон остался пустым (пороги слишком жёсткие) — 3 месяца с самым низким
    (для пика — высоким) индексом, кроме месяцев другого сезона; см. предупреждение season_empty.
    """
    det_low, det_peak = dm.classify_months(index, s['low_season_index_max'], s['peak_season_index_min'])
    manual_low, manual_peak = s['low_months'], s['peak_months']
    low = list(manual_low) if manual_low is not None else det_low
    peak = list(manual_peak) if manual_peak is not None else det_peak
    if manual_low is not None and manual_peak is None:
        peak = [m for m in peak if m not in low]
    if manual_peak is not None and manual_low is None:
        low = [m for m in low if m not in peak]
    fallback = []
    if index:
        if not low:
            low = _extreme_months(index, peak, highest=False)
            fallback.append('low')
        if not peak:
            peak = _extreme_months(index, low, highest=True)
            fallback.append('peak')
    manual = (manual_low is not None, manual_peak is not None)
    source = 'manual' if all(manual) else ('mixed' if any(manual) else 'auto')
    return Season(low, peak, source, index, det_low, det_peak, tuple(fallback))


@dataclass(frozen=True)
class CustomerModel:
    customer_id: int
    year: dm.Demand
    low: dm.Demand
    peak: dm.Demand
    size: str                  # длительность визита — по классу (visit_norms)
    visits_per_week: float
    status: cst.CustomerStatus | None = None   # §15: затих, потерян, без заказов — λ = 0 во всех сезонах
    hist: dm.Demand | None = None             # спрос за год до статуса (для показа); None — как year

    @property
    def year_hist(self) -> dm.Demand:
        """Спрос за год по истории заказов — сколько клиент брал, даже если сейчас λ = 0."""
        return self.hist if self.hist is not None else self.year

    def draw(self, season: str) -> Draw:
        dem: dm.Demand = getattr(self, season)
        return Draw(dm.visit_probability(dem.lam, self.visits_per_week), dem.values)


def customer_models(snap: Snapshot, s: Mapping[str, Any], season: Season,
                    included: Collection[int]) -> dict[int, CustomerModel]:
    """Спрос клиентов плана в окне [today − 365, today): год, низкий сезон, пик.

    f_i (визитов в неделю в p = min(1, λ/f)) — только по дням включённых менеджеров
    (CurrentPlan.visits_per_week_among). Статус клиента на today (§15, status.customer_status):
    у затихшего, потерянного и без заказов λ = 0 во всех сезонах — ожидаемая выручка 0; прошлые
    заказы (средний заказ и кг — класс размера) и спрос по истории (CustomerModel.hist) остаются.
    """
    freq = snap.plan.visits_per_week_among(included)
    end = snap.today
    year_w = [(snap.window_start, end)]
    low_w = dm.month_windows(season.low, snap.window_start, end)
    peak_w = dm.month_windows(season.peak, snap.window_start, end)
    rate_year = dm.company_rate(snap.company_orders_by_day, year_w)

    def rate_ratio(windows: Sequence[dm.DateWindow]) -> float:
        rate = dm.company_rate(snap.company_orders_by_day, windows)
        return rate / rate_year if rate is not None and rate_year else 1.0

    low_ratio, peak_ratio = rate_ratio(low_w), rate_ratio(peak_w)
    chain = set(s['chain_groups'])
    models = {}
    for cid in snap.plan.customer_ids:
        orders = snap.orders_by_customer.get(cid, ())
        first = snap.first_order.get(cid)
        year = dm.window_demand(orders, year_w, first, end)
        low = dm.season_demand(orders, low_w, first, end, year, low_ratio)
        peak = dm.season_demand(orders, peak_w, first, end, year, peak_ratio)
        customer = snap.customers.get(cid)
        size = dm.size_class(year.mean_kg if year.values else None,
                             customer.group if customer else None, chain,
                             s['size_small_max_kg'], s['size_medium_max_kg'])
        status = cst.customer_status(orders, first, end, season.peak, s)
        hist = year
        if status.silent:
            year, low, peak = (dm.Demand(0.0, d.values, d.exposure_days, d.fallback)
                               for d in (year, low, peak))
        models[cid] = CustomerModel(cid, year, low, peak, size, freq.get(cid, 0.0), status, hist)
    return models


def visit_coord(snap: Snapshot, customer_id: int, address_id: int,
                manual: Mapping[int, Point] | None = None,
                driver: Mapping[int, Point] | None = None) -> Coord:
    """Координата визита: ручная точка клиента (Bundle.geo_overrides — логист поставил на карте) →
    точка водителей (Bundle.driver_points) → ERP-адрес шаблона → дефолтный адрес клиента → GPS (§3)."""
    if manual and customer_id in manual:
        return resolve_coord(manual[customer_id], None, None)
    if driver and customer_id in driver:
        return resolve_coord(None, None, None, driver=driver[customer_id])
    erp: Point | None = None
    for addr in (address_id, snap.default_address.get(customer_id)):
        entry = snap.erp_points.get(addr) if addr else None
        if entry is not None and entry[0] == customer_id:
            erp = entry[1]
            break
    return resolve_coord(None, erp, snap.gps_points.get(customer_id))


# --- Сборка обзора (§10.1) ---

def _r(x: float | None, nd: int) -> float | None:
    return None if x is None else round(x, nd)


def _i(x: float | None) -> int | None:
    return None if x is None else int(round(x))


def _hhmm(minutes: float) -> str:
    m = int(round(minutes))
    return f'{m // 60:02d}:{m % 60:02d}'


def _num(x: float) -> float | int:
    """Число недели: целое без «.0», иначе 1 знак."""
    x = round(x, 1)
    return int(x) if x == int(x) else x


def _is_weak_day(r: DayResult) -> bool:
    """«Слабый» день — по той же округлённой (2 знака) вероятности, что видит пользователь."""
    return round(r.low.p_ge_min, 2) < DAY_BELOW_MIN_P


@dataclass(frozen=True)
class ManagerEval:
    agent_id: int
    included: bool
    active: bool              # есть заказы или визиты за 8 недель (Snapshot.active_agents)
    home: Point | None
    home_source: str
    fuel_type: str
    days: tuple[DayResult, ...]


def manager_home(snap: Snapshot, bundle: Bundle, agent_id: int) -> tuple[Point | None, str]:
    """Дом менеджера и источник: ручной из настроек → авто по GPS → нет."""
    profile = bundle.profile(agent_id)
    auto = snap.auto_homes.get(agent_id)
    if profile.home is not None:
        return profile.home, 'manual'
    if auto is not None:
        return (auto.lat, auto.lon), 'gps_auto'
    return None, 'none'


def plan_points(snap: Snapshot, bundle: Bundle,
                coords: dict[tuple[int, int], Coord]) -> list[Point]:
    """Все точки плана для расчёта расстояний одним разом: координаты визитов (заполняет coords),
    дома менеджеров плана и склад."""
    points: list[Point] = []
    for d in snap.plan.days:
        for pv in d.visits:
            key = (pv.customer_id, pv.address_id)
            if key not in coords:
                coords[key] = visit_coord(snap, pv.customer_id, pv.address_id, bundle.geo_overrides,
                                           bundle.driver_points)
            if coords[key].point is not None:
                points.append(coords[key].point)
    for agent_id in snap.plan.agent_ids:
        home, _ = manager_home(snap, bundle, agent_id)
        if home is not None:
            points.append(home)
    if bundle.depot is not None:
        points.append(bundle.depot)
    return points


def _day_visits(snap: Snapshot, day: PlanDay, models: Mapping[int, CustomerModel],
                visit_minutes: Mapping[str, float], coords: dict[tuple[int, int], Coord],
                manual: Mapping[int, Point] | None = None,
                driver: Mapping[int, Point] | None = None) -> list[VisitModel]:
    """Визиты дня в порядке fROWNUM; координата — этого визита (клиент + адрес шаблона)."""
    visits = []
    for erp_no, pv in enumerate(day.visits, 1):
        key = (pv.customer_id, pv.address_id)
        if key not in coords:
            coords[key] = visit_coord(snap, pv.customer_id, pv.address_id, manual, driver)
        coord = coords[key]
        m = models[pv.customer_id]
        visits.append(VisitModel(pv.customer_id, coord.point, visit_minutes[m.size],
                                 m.draw('low'), m.draw('year'), m.draw('peak'), erp_no, coord.source))
    return visits


def _evaluate_manager(snap: Snapshot, bundle: Bundle, agent_id: int, included: bool,
                      models: Mapping[int, CustomerModel], visit_minutes: Mapping[str, float],
                      norms: Norms, coords: dict[tuple[int, int], Coord]) -> ManagerEval:
    s = bundle.settings
    profile = bundle.profile(agent_id)
    home, home_source = manager_home(snap, bundle, agent_id)
    l100 = (profile.car_fuel_l_per_100km if profile.car_fuel_l_per_100km is not None
            else float(s['manager_car_default_l_per_100km']))
    workdays = set(s['workdays'])
    results = [evaluate_day(day, _day_visits(snap, day, models, visit_minutes, coords, bundle.geo_overrides,
                                                    bundle.driver_points),
                            home=home,
                            norms=norms, manager_l100=l100, workday=day.weekday in workdays)
               for day in snap.plan.days_of(agent_id)]
    return ManagerEval(agent_id, included, agent_id in snap.active_agents, home, home_source,
                       profile.car_fuel_type or DEFAULT_MANAGER_FUEL, tuple(results))


def _day_json(r: DayResult) -> dict[str, Any]:
    return {
        'week': r.day.week, 'weekday': r.day.weekday,
        'label': WEEKDAY_LABELS.get(r.day.weekday, str(r.day.weekday)),
        'delivery_label': WEEKDAY_LABELS[delivery_weekday(r.day.weekday)[0]],
        'visits': r.visits, 'visits_no_coords': r.visits_no_coords, 'flags': list(r.flags),
        'plan_minutes': _i(r.plan_min), 'drive_minutes': _i(r.drive_min),
        'visit_minutes': _i(r.visit_min), 'overtime_minutes': _i(r.overtime_min),
        # у клиентов + дорога из дома = весь день и после округления (commute_min ≥ 0 ⇒ разность ≥ 0)
        'work_minutes': _i(r.work_min), 'commute_minutes': _i(r.plan_min) - _i(r.work_min),
        'manager_km': _r(r.km, 1), 'manager_liters': _r(r.liters, 1),
        'manager_km_rownum': _r(r.km_rownum, 1),
        'revenue_low_exp': _i(r.low.expected), 'revenue_low_p10': _i(r.low.p10),
        'revenue_low_p90': _i(r.low.p90), 'p_day_ge_min': _r(r.low.p_ge_min, 2),
        'revenue_year_exp': _i(r.revenue_year),
        'customer_ids': list(r.customer_ids),
        # Порядок объезда (NN + 2-opt, см. evaluate_day); координата — этого визита, а не клиента
        'stops': [{'customer_id': v.customer_id, 'erp_rownum': v.erp_no,
                   'lat': v.point[0] if v.point else None, 'lon': v.point[1] if v.point else None,
                   'coord_source': v.coord_source} for v in r.stops],
    }


@dataclass(frozen=True)
class WeekTotals:
    """Сумма по дням цикла / W (неделя) для одного менеджера — неокруглённые значения."""
    visits: float
    revenue_low: float
    revenue_year: float
    revenue_peak: float            # пик (лето)
    manager_km: float
    manager_liters: float
    days_below_min: float
    avg_plan_hours: float | None
    avg_work_hours: float | None   # у клиентов (без дороги из дома) — сравнимо с фактом GPS


def _week(me: ManagerEval, cycle_weeks: int) -> WeekTotals:
    """Суммы — math.fsum (точное округление): неизменный план W = 1 и он же, развёрнутый в
    W = 2, дают одни и те же цифры недели до последнего бита, а не «почти равные»."""
    days = me.days
    work = [r for r in days if r.workday]
    w = float(cycle_weeks)
    fsum = math.fsum
    return WeekTotals(
        visits=sum(r.visits for r in days) / w,
        revenue_low=fsum(r.low.expected for r in days) / w,
        revenue_year=fsum(r.revenue_year for r in days) / w,
        revenue_peak=fsum(r.revenue_peak for r in days) / w,
        manager_km=fsum(r.km for r in days) / w,
        manager_liters=fsum(r.liters for r in days) / w,
        days_below_min=sum(1 for r in work if _is_weak_day(r)) / w,
        avg_plan_hours=fmean(r.plan_min for r in work) / 60.0 if work else None,
        avg_work_hours=fmean(r.work_min for r in work) / 60.0 if work else None,
    )


def _fact_json(f: Fact | None) -> dict[str, Any]:
    if f is None:
        return {'visits_per_day': None, 'day_start': None, 'day_end': None, 'hours': None,
                'work_hours': None, 'pause_hours': None, 'productive_share': None, 'days': 0,
                'track_days': 0}
    return {'visits_per_day': _r(f.visits_per_day, 1), 'day_start': _hhmm(f.day_start_min),
            'day_end': _hhmm(f.day_end_min), 'hours': _r(f.hours, 1),
            'work_hours': _r(f.work_hours, 1), 'pause_hours': _r(f.pause_hours, 1),
            'productive_share': _r(f.productive_share, 2), 'days': f.days,
            'track_days': f.track_days}


def _warning(code: str, text: str, anchor: str | None) -> dict[str, Any]:
    return {'code': code, 'text': text, 'link': f'{SETTINGS_URL}#{anchor}' if anchor else None}


@dataclass(frozen=True)
class PlanEvaluation:
    """Полная оценка плана snap.plan (текущего W = 1 или предложенного W = 2): нормы, модели
    клиентов, менеджеры и их недели, парк машин (fleet: заказы всех менеджеров в расчёте; None — нет
    склада или машин с тоннажем и расходом). Итоги компании — plan_totals."""
    norms: Norms
    road: dict[str, tuple[float, str]]
    visit: dict[str, tuple[float, str]]
    visit_minutes: dict[str, float]
    season: Season
    models: dict[int, CustomerModel]
    coords: dict[tuple[int, int], Coord]
    included_ids: frozenset[int]
    evals: tuple[ManagerEval, ...]
    weeks: dict[int, WeekTotals]
    fleet: fl.FleetEval | None = None
    trucks_incomplete: tuple[str, ...] = ()   # активные машины без тоннажа или расхода — не в расчёте


def evaluate_plan(snap: Snapshot, bundle: Bundle, calib: Calibration | None = None,
                  agent_ids: Collection[int] | None = None, *,
                  visit_minutes: Mapping[str, float] | None = None,
                  models: Mapping[int, CustomerModel] | None = None,
                  roads: RoadDistances | None = None) -> PlanEvaluation:
    """Оценка произвольного плана snap.plan тем же оценщиком этапа 1.

    agent_ids — каких менеджеров оценивать (None — всех с планом); f_i в p = min(1, λ/f) — по
    включённым менеджерам этого плана. visit_minutes — длительности визитов по классам (для
    «стало» — те же, что в «было», иначе доли классов нового плана сдвинули бы «авто»-нормы);
    models — готовые модели клиентов (спрос не пересчитывается). roads — расстояния по дорогам
    (None — по прямой × извилистость): все точки плана считаются одним разом до оценки; карта не
    загрузилась (roads.failed) — по прямой, norms.roads = None.
    """
    s = bundle.settings
    road = road_norms(s, calib)
    coords: dict[tuple[int, int], Coord] = {}
    if roads is not None:
        roads.ensure(plan_points(snap, bundle, coords))
        if roads.failed:   # граф не собрался — считаем по прямой (в журнале — причина)
            roads = None
    norms = Norms.from_settings(s, calib, roads)
    season = resolve_season(snap.season_index, s)
    cycle = snap.plan.cycle_weeks

    def agent_key(agent_id: int) -> tuple[str, int]:
        agent = snap.agents.get(agent_id)
        return (agent.code if agent else '', agent_id)

    all_ids = sorted(snap.plan.agent_ids, key=agent_key)
    included_ids = frozenset(a for a in all_ids if bundle.included(a, snap.active_agents))
    if models is None:
        models = customer_models(snap, s, season, included_ids)
    visit = visit_norms(s, calib.visit_min_avg if calib is not None else None,
                        size_shares(snap.plan, {c: m.size for c, m in models.items()},
                                    included_ids, s['workdays']))
    if visit_minutes is None:
        visit_minutes = {size: visit[f'visit_min_{size}'][0] for size in VISIT_NORMS}
    wanted = set(all_ids if agent_ids is None else agent_ids)
    evals = tuple(_evaluate_manager(snap, bundle, a, a in included_ids, models, visit_minutes,
                                    norms, coords)
                  for a in all_ids if a in wanted)
    trucks, incomplete = fl.fleet_trucks(bundle.resolved_trucks(snap.active_cars),
                                         {code: car.name for code, car in snap.cars.items()})
    fleet = None
    if bundle.depot is not None and trucks:
        fleet = _evaluate_fleet(snap, bundle, included_ids, evals, models, visit_minutes, coords, norms,
                                trucks)
    return PlanEvaluation(norms=norms, road=road, visit=visit, visit_minutes=dict(visit_minutes),
                          season=season, models=dict(models), coords=coords,
                          included_ids=included_ids, evals=evals,
                          weeks={me.agent_id: _week(me, cycle) for me in evals},
                          fleet=fleet, trucks_incomplete=tuple(incomplete))


def _evaluate_fleet(snap: Snapshot, bundle: Bundle, included_ids: Collection[int],
                    evals: Sequence[ManagerEval], models: Mapping[int, CustomerModel],
                    visit_minutes: Mapping[str, float], coords: dict[tuple[int, int], Coord],
                    norms: Norms, trucks: Sequence[fl.FleetTruck]) -> fl.FleetEval:
    """Парк по заказам ВСЕХ менеджеров в расчёте (и тех, кого сейчас не оценивают: их заказы везут те
    же машины). Визиты без координат в рейсы не входят."""
    days = {(r.day.agent_id, r.day.week, r.day.weekday): r.stops for me in evals for r in me.days}
    by_day: dict[tuple[int, int], list[fl.DeliveryVisit]] = {}
    for day in snap.plan.days:
        if day.agent_id not in included_ids:
            continue
        stops = days.get((day.agent_id, day.week, day.weekday))
        if stops is None:
            stops = _day_visits(snap, day, models, visit_minutes, coords, bundle.geo_overrides, bundle.driver_points)
        by_day.setdefault((day.week, day.weekday), []).extend(
            fl.DeliveryVisit(v.customer_id, day.weekday, v.point, v.year, v.peak)
            for v in stops if v.point is not None)
    return fl.evaluate_fleet(by_day, snap.plan.cycle_weeks, bundle.depot, trucks, norms,
                             fl.TruckNorms.from_settings(bundle.settings),
                             float(bundle.settings['min_trip_revenue']))


def plan_totals(snap: Snapshot, bundle: Bundle, pe: PlanEvaluation) -> dict[str, Any]:
    """Итоги компании по оценённым менеджерам в расчёте — в неделю (§6 этапа 1); парк — по всем
    менеджерам в расчёте."""
    return _totals(snap, bundle, [me for me in pe.evals if me.included], pe.weeks, pe.models,
                   pe.coords, pe.fleet)


def build_overview(snap: Snapshot, bundle: Bundle, calib: Calibration | None = None,
                   roads: RoadDistances | None = None) -> dict[str, Any]:
    """Оценка текущего плана → тело ответа /api/routes/overview (§10.1) без success/generated_at/from_cache.

    calib — калибровка по GPS этого снимка: из неё «авто»-нормы дорог (road_norms) и длительности
    визитов (visit_norms). roads — расстояния по дорогам (None — по прямой × извилистость).
    """
    s = bundle.settings
    pe = evaluate_plan(snap, bundle, calib, roads=roads)
    failed = roads is not None and pe.norms.roads is None   # карта есть, но не загрузилась
    roads = pe.norms.roads
    road, visit, season, models, coords = pe.road, pe.visit, pe.season, pe.models, pe.coords
    cycle = snap.plan.cycle_weeks
    evals, weeks = pe.evals, pe.weeks

    managers_json = []
    for me in evals:
        agent = snap.agents.get(me.agent_id)
        wk = weeks[me.agent_id]
        flags = []
        if me.home is None:
            flags.append('no_home')
        if not me.active:
            flags.append('inactive')
        managers_json.append({
            'agent_id': me.agent_id,
            'code': agent.code if agent else str(me.agent_id),
            'name': agent.name if agent else '',
            'included': me.included,
            'home': {'lat': me.home[0] if me.home else None, 'lon': me.home[1] if me.home else None,
                     'source': me.home_source},
            'flags': flags,
            'week': {
                'visits': _num(wk.visits), 'revenue_low': _i(wk.revenue_low),
                'revenue_year': _i(wk.revenue_year), 'manager_km': _r(wk.manager_km, 1),
                'manager_liters': _r(wk.manager_liters, 1), 'days_below_min': _num(wk.days_below_min),
                'avg_plan_hours': _r(wk.avg_plan_hours, 1),
                'avg_work_hours': _r(wk.avg_work_hours, 1),
            },
            'fact': _fact_json(snap.facts.get(me.agent_id)),
            'days': [_day_json(r) for r in me.days],
        })

    included = [me for me in evals if me.included]
    totals = _totals(snap, bundle, included, weeks, models, coords, pe.fleet)
    # вне расчёта «авто» (нет работы за 8 недель), а не по явному выбору владельца
    idle = [me for me in evals if not me.included and bundle.included_source(me.agent_id) == 'auto']
    warnings = _warnings(snap, bundle, included, totals, season, idle, pe)
    warnings += _road_warnings(roads, plan_points(snap, bundle, dict(coords)), failed)
    customers_json = _customers_json(snap, models, coords)

    plan_weekdays = {d.weekday for d in snap.plan.days}
    return {
        'data_as_of': snap.data_as_of.isoformat(timespec='seconds'),
        'distance_source': 'straight' if roads is None else 'roads',
        'warnings': warnings,
        'season': {
            'low_months': season.low, 'peak_months': season.peak, 'source': season.source,
            'index': {str(m): round(v, 2) for m, v in sorted((season.index or {}).items())},
        },
        'norms': {key: {'value': value, 'source': source}
                  for key, (value, source) in {**road, **visit}.items()},
        'cycle_weeks': cycle,
        'weekdays': sorted(set(s['workdays']) | plan_weekdays),
        'depot': {'lat': bundle.depot[0], 'lon': bundle.depot[1]} if bundle.depot else None,
        'totals': totals,
        'fleet': _fleet_json(pe.fleet, bundle) if pe.fleet is not None else None,
        'managers': managers_json,
        'customers': customers_json,
        'at_risk_customers': _at_risk_json(snap, models, included),
    }


def _totals(snap: Snapshot, bundle: Bundle, included: Sequence[ManagerEval],
            weeks: Mapping[int, WeekTotals], models: Mapping[int, CustomerModel],
            coords: Mapping[tuple[int, int], Coord], fleet: fl.FleetEval | None = None) -> dict[str, Any]:
    """Итоги компании — только по включённым менеджерам (§6); всё — в неделю: число дней и
    слабых дней делится на W (для W = 1 цифры те же). Грузовики — парк (fleet; None — не посчитан)."""
    s = bundle.settings
    cycle = snap.plan.cycle_weeks
    work_days = [r for me in included for r in me.days if r.workday]
    all_days = [r for me in included for r in me.days]
    wk = [weeks[me.agent_id] for me in included]

    fw = fleet.week() if fleet is not None else {}
    diesel = s['fuel_price_diesel']
    truck_liters = fw.get('liters')
    truck_amd = truck_liters * diesel if truck_liters is not None and diesel is not None else None

    manager_amd: float | None = 0.0
    for me in included:
        price = s.get(f'fuel_price_{me.fuel_type}')
        if price is None:
            manager_amd = None
            break
        manager_amd += weeks[me.agent_id].manager_liters * price

    facts = [snap.facts[me.agent_id] for me in included if me.agent_id in snap.facts]
    fact_days = sum(f.days for f in facts)
    fact_hours = sum(f.hours * f.days for f in facts) / fact_days if fact_days else None
    # работа и паузы — средние по дням с треком, поэтому вес — track_days, а не days
    tracked = [(f.work_hours, f.pause_hours, f.track_days) for f in facts
               if f.work_hours is not None and f.pause_hours is not None]
    track_days = sum(n for _, _, n in tracked)
    fact_work = sum(w * n for w, _, n in tracked) / track_days if track_days else None
    fact_pause = sum(p * n for _, p, n in tracked) / track_days if track_days else None

    visits_total = visits_coords = 0
    revs: list[float] = []
    revs_coords: list[float] = []
    for r in all_days:
        for pv in r.day.visits:
            rev = models[pv.customer_id].draw('year').expected_revenue
            visits_total += 1
            revs.append(rev)
            if coords[(pv.customer_id, pv.address_id)].point is not None:
                visits_coords += 1
                revs_coords.append(rev)
    rev_total, rev_coords = math.fsum(revs), math.fsum(revs_coords)
    plan_customers = sorted({pv.customer_id for r in all_days for pv in r.day.visits})

    return {
        'managers': len(included),
        'days_total': _num(len(work_days) / cycle),
        'days_below_min': _num(sum(1 for r in work_days if _is_weak_day(r)) / cycle),
        'avg_p_day_ge_min': _r(fmean(r.low.p_ge_min for r in work_days), 2) if work_days else None,
        # парк машин (fleet-plan §2): дни доставки всех менеджеров в расчёте, год; загрузка — и в пик
        'trips_poor_week': _r(fw.get('trips_poor'), 1),        # рейсов с выручкой < min_trip_revenue
        'truck_km_week': _r(fw.get('km'), 1),
        'truck_liters_week': _r(truck_liters, 1),
        'truck_amd_week': _i(truck_amd),
        'trips_week': _r(fw.get('trips'), 1),
        'trips_per_day': _r(fw.get('trips_per_day'), 1),
        'truck_kg_per_day': _i(fw.get('kg_per_day')),
        'truck_hours_week': _r(fw.get('hours'), 1),
        'truck_days_short_week': _num(fw['days_short']) if fleet is not None else None,
        'avg_load_pct': _r(fw.get('load_pct'), 1),
        'avg_trip_revenue': _i(fw.get('trip_revenue')),
        'poor_trip_share': _r(fw.get('poor_trip_share'), 2),
        'manager_km_week': _r(sum(w.manager_km for w in wk), 1),
        'manager_liters_week': _r(sum(w.manager_liters for w in wk), 1),
        'manager_amd_week': _i(manager_amd) if included else None,
        'avg_load_pct_peak': _r(fw.get('load_pct_peak'), 1),
        'avg_plan_hours': _r(fmean(r.plan_min for r in work_days) / 60.0, 1) if work_days else None,
        # у клиентов (визиты + дорога между ними) — сравнимо с работой по GPS (avg_fact_work_hours)
        'avg_plan_work_hours': (_r(fmean(r.work_min for r in work_days) / 60.0, 1)
                                if work_days else None),
        'avg_fact_hours': _r(fact_hours, 1),          # окно «первый визит → конец последнего»
        'avg_fact_work_hours': _r(fact_work, 1),      # в окне: езда и стоянки по треку
        'avg_fact_pause_hours': _r(fact_pause, 1),    # в окне: разрывы трека > 15 мин
        'revenue_week_low': _i(sum(w.revenue_low for w in wk)),
        'revenue_week_peak': _i(sum(w.revenue_peak for w in wk)),
        'revenue_week_year': _i(sum(w.revenue_year for w in wk)),
        'coords': {   # визиты в неделю (для W = 1 — визиты плана)
            'visits_total': _num(visits_total / cycle), 'visits_with_coords': _num(visits_coords / cycle),
            'revenue_share_with_coords': _r(rev_coords / rev_total, 2) if rev_total > 0 else None,
        },
        # §15: клиенты этих менеджеров под риском — затихли и потеряны (выручка за 12 мес, драм),
        # без заказов за год
        'at_risk': cst.risk_summary(models[c].status for c in plan_customers if models[c].status is not None),
    }


def _fleet_json(fe: fl.FleetEval, bundle: Bundle) -> dict[str, Any]:
    """Парк для «Обзора»: машины расчёта и «Развоз по дням» — день доставки → машины (средние за
    пробу, год), загрузка и нехватка машин в пик."""
    s = bundle.settings

    def truck_json(t: fl.TruckLoad) -> dict[str, Any]:
        return {'car_code': t.car_code, 'name': t.name, 'capacity_kg': _num(t.capacity_kg),
                'trips': _r(t.trips, 1), 'stops': _r(t.stops, 1), 'kg': _i(t.kg), 'km': _r(t.km, 1),
                'liters': _r(t.liters, 1), 'hours': _r(t.minutes / 60.0, 1), 'load_pct': _r(t.load_pct, 0)}

    return {
        'trucks': [{'car_code': t.car_code, 'name': t.name, 'capacity_kg': _num(t.capacity_kg),
                    'fuel_l_per_100km': _num(t.l100)} for t in fe.trucks],
        'work_start': s['truck_work_start'], 'work_end': s['truck_work_end'],
        'cycle_weeks': fe.cycle_weeks,
        'days': [{
            'week': d.week, 'weekday': d.weekday, 'label': WEEKDAY_LABELS[d.weekday],
            'visits': d.visits, 'orders': _r(d.orders, 1), 'kg': _i(d.kg), 'km': _r(d.km, 1),
            'liters': _r(d.liters, 1), 'trips': _r(d.trips, 1), 'hours': _r(d.minutes / 60.0, 1),
            'load_pct': _r(d.load_pct, 0), 'kg_peak': _i(d.kg_peak), 'trips_peak': _r(d.trips_peak, 1),
            'load_pct_peak': _r(d.load_pct_peak, 0), 'p_short': _r(d.p_short, 2),
            'p_short_peak': _r(d.p_short_peak, 2),
            'trucks': [truck_json(t) for t in d.trucks],
        } for d in fe.days],
    }


def _warnings(snap: Snapshot, bundle: Bundle, included: Sequence[ManagerEval],
              totals: Mapping[str, Any], season: Season,
              idle: Sequence[ManagerEval], pe: PlanEvaluation) -> list[dict[str, Any]]:
    s = bundle.settings
    out = []
    if idle:
        codes = ', '.join(snap.agents[me.agent_id].code if me.agent_id in snap.agents
                          else str(me.agent_id) for me in idle)
        out.append(_warning('inactive_templates', f'Шаблоны без работы 8 недель (нет заказов и '
                                                  f'визитов): {codes} — не входят в расчёт. '
                                                  f'Включить можно в настройках.', 'managers'))
    if bundle.depot is None:
        out.append(_warning('no_depot', 'Не указан склад — дизель грузовиков не посчитан', 'depot'))
    elif pe.fleet is None:
        out.append(_warning('no_fleet', 'Укажите тоннаж и расход машин — тогда программа посчитает '
                                        'дизель грузовиков', 'trucks'))
    if pe.trucks_incomplete and pe.fleet is not None:
        codes = pe.trucks_incomplete
        out.append(_warning('truck_incomplete', f'У {len(codes)} {plural(len(codes), "машины", "машин", "машин")} '
                                                f'не указан тоннаж или расход ({", ".join(codes)}) — в развозе '
                                                f'их нет', 'trucks'))
    short = totals.get('truck_days_short_week')
    if short:
        out.append(_warning('fleet_short', f'В пик в {_num(short)} дн. доставки в неделю машины не '
                                           f'успевают развезти заказы за рабочий день — нужна ещё машина '
                                           f'или рейс после конца дня', 'trucks'))
    needed = ({'diesel'} if pe.fleet is not None else set()) | {me.fuel_type for me in included}
    missing = sorted(f for f in needed if s.get(f'fuel_price_{f}') is None)
    if missing:
        names = {'diesel': 'дизель', 'petrol': 'бензин', 'lpg': 'газ'}
        out.append(_warning('no_fuel_prices', 'Не заданы цены топлива (' +
                            ', '.join(names[f] for f in missing) +
                            ') — стоимость в драмах не посчитана', 'norms'))
    no_home = [me for me in included if me.home is None]
    if no_home:
        out.append(_warning('no_home', f'Нет дома у {len(no_home)} менеджеров — маршрут дня '
                                       f'считается от первого до последнего визита', 'managers'))
    coords = totals['coords']
    missing_coords = coords['visits_total'] - coords['visits_with_coords']
    if missing_coords > 0:
        share = coords['revenue_share_with_coords']
        share_txt = f' (с координатами {share * 100:.0f}% выручки)' if share is not None else ''
        out.append(_warning('coords_missing',
                            f'Нет координат у {missing_coords} из {coords["visits_total"]} визитов '
                            f'плана{share_txt} — их км не посчитаны', None))
    off = sum(1 for me in included for r in me.days if not r.workday)
    if off:
        out.append(_warning('off_days', f'В плане {off} дн. в нерабочие дни недели — они показаны, '
                                        f'но не входят в «слабые дни» и средние часы', 'norms'))
    if snap.plan.multiweek:
        out.append(_warning('templates_multiweek_unverified',
                            'В шаблонах ERP есть неделя/периодичность ≠ 1 — формат не проверен, '
                            'цикл развёрнут по допущению', None))
    if snap.season_index is None and (s['low_months'] is None or s['peak_months'] is None):
        out.append(_warning('season_unknown', 'Мало истории продаж для сезонного индекса — '
                                              'задайте месяцы сезонов вручную', 'season'))
    if season.fallback:
        def months(ms: Sequence[int]) -> str:
            return ', '.join(MONTH_SHORT[m - 1] for m in ms)
        used = []
        if 'low' in season.fallback:
            used.append(f'низкий сезон — {months(season.low)} (самые низкие продажи)')
        if 'peak' in season.fallback:
            used.append(f'пик — {months(season.peak)} (самые высокие продажи)')
        out.append(_warning('season_empty', 'Под пороги сезонного индекса не подошёл ни один '
                                            'месяц, взяты 3 крайних: ' + '; '.join(used)
                            + '. Пороги можно поправить в настройках.', 'season'))
    return out


def _road_warnings(roads: RoadDistances | None, points: Sequence[Point],
                   failed: bool = False) -> list[dict[str, Any]]:
    """Км по прямой: карты нет или она не загрузилась — у всех участков, точка дальше 0,5 км от
    дороги — у её участков."""
    if failed:
        return [_warning('roads_failed', 'Карту дорог не удалось загрузить (подробности — в журнале '
                                         'сервера) — км считаются по прямой с поправкой на '
                                         'извилистость', 'norms')]
    if roads is None:
        return [_warning('roads_off', 'Карта дорог не подключена — км считаются по прямой с '
                                      'поправкой на извилистость', 'norms')]
    n = roads.unsnapped(points)
    if not n:
        return []
    return [_warning('roads_unsnapped', f'{n} {plural(n, "точка", "точки", "точек")} плана дальше '
                                        f'0,5 км от дорог на карте — км до них считаются по прямой '
                                        f'с поправкой на извилистость', None)]


def _customers_json(snap: Snapshot, models: Mapping[int, CustomerModel],
                    coords: Mapping[tuple[int, int], Coord]) -> dict[str, Any]:
    """Клиенты плана; координата — первого визита клиента в плане. Выручка и шансы — модели (у
    затихших и потерянных — 0); orders_per_week_year, rev_week_year_hist, rev_year_hist — по истории
    заказов: сколько клиент брал (§15)."""
    first: dict[int, Coord] = {}
    for d in snap.plan.days:
        for pv in d.visits:
            if pv.customer_id not in first:
                first[pv.customer_id] = coords[(pv.customer_id, pv.address_id)]
    out = {}
    for cid, m in models.items():
        c = snap.customers.get(cid)
        coord = first.get(cid)
        low, year = m.draw('low'), m.draw('year')
        hist = m.year_hist
        st = m.status
        hist_week = m.visits_per_week * dm.visit_probability(hist.lam, m.visits_per_week) * hist.mean_revenue
        out[str(cid)] = {
            'code': c.code if c else '', 'name': c.name if c else '',
            'lat': coord.lat if coord else None, 'lon': coord.lon if coord else None,
            'coord_source': coord.source if coord else 'none',
            'size': m.size,
            'group': (c.group_name or c.group) if c else None,
            'area': c.area if c else None,
            'orders_per_week_year': _r(hist.lam, 2),
            'avg_order_amd': _i(m.year.mean_revenue) if m.year.values else None,
            'avg_order_kg': _i(m.year.mean_kg) if m.year.values else None,
            'rev_week_low': _i(m.visits_per_week * low.expected_revenue),
            'rev_week_year': _i(m.visits_per_week * year.expected_revenue),
            'p_low': _r(low.p, 2),
            'p_year': _r(year.p, 2),
            'rev_visit_low': _i(low.expected_revenue),
            'visits_per_week': _r(m.visits_per_week, 2),
            # §15 — статус по давности последнего заказа
            'status': st.status if st else cst.ACTIVE,
            'silent_days': st.silent_days if st else None,
            'usual_interval_days': _r(st.usual_interval_days, 1) if st else None,
            'orders_year': st.orders if st else len(hist.values),
            'rev_year_hist': _i(st.revenue) if st else _i(math.fsum(v[0] for v in hist.values)),
            'rev_week_year_hist': _i(hist_week),
            'last_order_date': st.last_order.isoformat() if st and st.last_order else None,
        }
    return out


def _at_risk_json(snap: Snapshot, models: Mapping[int, CustomerModel],
                  included: Sequence[ManagerEval]) -> list[dict[str, Any]]:
    """«Клиенты под риском» (§15): затихшие, потерянные и без заказов за год у менеджеров в расчёте —
    по убыванию выручки за 12 месяцев (сколько брал), затем по дням без заказов."""
    agents: dict[int, set[int]] = {}
    for me in included:
        for r in me.days:
            for pv in r.day.visits:
                agents.setdefault(pv.customer_id, set()).add(me.agent_id)
    rows = []
    for cid, ids in agents.items():
        st = models[cid].status
        if st is None or not st.silent:
            continue
        c = snap.customers.get(cid)
        managers = sorted(({'agent_id': a, 'code': snap.agents[a].code if a in snap.agents else str(a),
                            'name': snap.agents[a].name if a in snap.agents else ''} for a in ids),
                          key=lambda x: (x['code'], x['agent_id']))
        rows.append({
            'customer_id': cid, 'code': c.code if c else '', 'name': c.name if c else '',
            'status': st.status, 'silent_days': st.silent_days,
            'usual_interval_days': _r(st.usual_interval_days, 1), 'orders_year': st.orders,
            'rev_year_hist': _i(st.revenue),
            'last_order_date': st.last_order.isoformat() if st.last_order else None,
            'managers': managers,
        })
    def silence(x: dict[str, Any]) -> int:   # без заказов за год (тишины нет) — как самые долгие
        return x['silent_days'] if x['silent_days'] is not None else 10 ** 6

    rows.sort(key=lambda x: (-x['rev_year_hist'], -silence(x), x['customer_id']))
    return rows

# -*- coding: utf-8 -*-
"""Геометрия: расстояния, валидность координат, медианы, км по GPS-треку, дом менеджера.

Чистая логика — без Flask и без БД. Координаты — WGS84 в градусах, расстояния — км.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from statistics import median
from typing import Iterable, Sequence

Point = tuple[float, float]  # (широта, долгота)

EARTH_RADIUS_KM = 6371.0088  # средний радиус Земли (IUGG)

# Габариты Армении: точка вне них — ошибка ввода или GPS.
ARMENIA_LAT = (38.8, 41.4)
ARMENIA_LON = (43.4, 46.7)

DEFAULT_POINT_DECIMALS = 5        # «одинаковые» координаты — после округления до 5 знаков (~1 м)
DEFAULT_POINT_MIN_CUSTOMERS = 3   # одна точка у ≥ 3 разных клиентов — «дефолтная», невалидна
GPS_MAX_ACCURACY_M = 100.0        # точки GPS с погрешностью больше — отбрасываем
GPS_MIN_VISITS = 3                # медиана GPS клиента — минимум по 3 визитам
ERP_GPS_MAX_GAP_KM = 2.0          # ERP-точка дальше от GPS визитов — считаем ERP ошибочной

TRACK_ANCHOR_KM = 0.05            # якорный фильтр трека: 50 м
TRACK_MAX_SPEED_KMH = 150.0       # сегмент быстрее — скачок GPS

HOME_NIGHT_END = time(8, 0)       # «ночные» точки: 00:00–07:59
HOME_MORNING_END = time(10, 0)    # первая точка дня до 10:00
HOME_MIN_NIGHT_POINTS = 20
HOME_MIN_NIGHTS = 5
HOME_MIN_MORNINGS = 5


def haversine_km(a: Point, b: Point) -> float:
    """Расстояние по большому кругу, км."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def is_valid_point(lat: float | None, lon: float | None) -> bool:
    """Координата задана, конечна и лежит в габаритах Армении."""
    if lat is None or lon is None:
        return False
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError, OverflowError):   # OverflowError — целое вроде 10**400 из JSON
        return False
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        return False
    return (ARMENIA_LAT[0] <= lat_f <= ARMENIA_LAT[1]
            and ARMENIA_LON[0] <= lon_f <= ARMENIA_LON[1])


def point_key(p: Point) -> Point:
    """Ключ «одной и той же» точки: округление до 5 знаков."""
    return (round(p[0], DEFAULT_POINT_DECIMALS), round(p[1], DEFAULT_POINT_DECIMALS))


def default_point_keys(points: Iterable[tuple[int, Point]]) -> set[Point]:
    """Ключи «дефолтных» точек: одна координата у ≥ 3 разных клиентов.

    points — пары (клиент, точка); ожидаются уже валидные точки.
    """
    owners: dict[Point, set[int]] = {}
    for customer_id, p in points:
        owners.setdefault(point_key(p), set()).add(customer_id)
    return {k for k, cs in owners.items() if len(cs) >= DEFAULT_POINT_MIN_CUSTOMERS}


def median_point(points: Sequence[Point]) -> Point:
    """Медиана по широте и по долготе отдельно (устойчива к выбросам GPS)."""
    if not points:
        raise ValueError('median_point: пустой набор точек')
    return (median(p[0] for p in points), median(p[1] for p in points))


def in_city(p: Point, center: Point, radius_km: float) -> bool:
    """Точка в радиусе города (иначе — область)."""
    return haversine_km(p, center) <= radius_km


def in_polygon(p: Point, polygon: Sequence[Point]) -> bool:
    """Точка внутри многоугольника (вершины — по порядку обхода): чётность пересечений луча по долготе.
    Для границы в пару километров плоского приближения хватает. Меньше трёх вершин — False."""
    if len(polygon) < 3:
        return False
    lat, lon = p
    inside = False
    prev_lat, prev_lon = polygon[-1]
    for cur_lat, cur_lon in polygon:
        if (cur_lat > lat) != (prev_lat > lat) \
                and lon < (prev_lon - cur_lon) * (lat - cur_lat) / (prev_lat - cur_lat) + cur_lon:
            inside = not inside
        prev_lat, prev_lon = cur_lat, cur_lon
    return inside


@dataclass(frozen=True)
class Coord:
    """Координата визита и её источник: manual | driver | erp | gps | none."""
    lat: float | None
    lon: float | None
    source: str

    @property
    def point(self) -> Point | None:
        return None if self.lat is None or self.lon is None else (self.lat, self.lon)


NO_COORD = Coord(None, None, 'none')


def resolve_coord(manual: Point | None, erp: Point | None, gps: Point | None,
                  max_gap_km: float = ERP_GPS_MAX_GAP_KM, *, driver: Point | None = None) -> Coord:
    """Координата визита по приоритету: ручная → точка водителей → ERP → медиана GPS → нет.

    erp — уже проверенная ERP-точка (валидна, не «дефолтная») или None; gps — медиана GPS
    визитов клиента (≥ 3 визита) или None. ERP проигрывает GPS, если дальше max_gap_km.
    Ручная координата (этап 2) перекрывает всё; driver — медиана отметок водителей при доставке
    (driver-geo-plan.md §2, правило — courier.geo) — всё, кроме ручной. Без driver — как раньше.
    """
    if manual is not None:
        return Coord(manual[0], manual[1], 'manual')
    if driver is not None:
        return Coord(driver[0], driver[1], 'driver')
    if erp is not None:
        if gps is not None and haversine_km(erp, gps) > max_gap_km:
            return Coord(gps[0], gps[1], 'gps')
        return Coord(erp[0], erp[1], 'erp')
    if gps is not None:
        return Coord(gps[0], gps[1], 'gps')
    return NO_COORD


@dataclass(frozen=True)
class Fix:
    """Точка GPS-трека менеджера."""
    at: datetime
    lat: float
    lon: float
    accuracy: float | None = None

    @property
    def point(self) -> Point:
        return (self.lat, self.lon)


def usable_fixes(fixes: Iterable[Fix], max_accuracy: float = GPS_MAX_ACCURACY_M) -> list[Fix]:
    """Точки трека, пригодные для расчёта: валидные, погрешность ≤ max (NULL — допускаем), по времени."""
    ok = [f for f in fixes
          if is_valid_point(f.lat, f.lon) and (f.accuracy is None or f.accuracy <= max_accuracy)]
    ok.sort(key=lambda f: f.at)
    return ok


def track_km(fixes: Iterable[Fix], *, anchor_km: float = TRACK_ANCHOR_KM,
             max_speed_kmh: float = TRACK_MAX_SPEED_KMH) -> float:
    """Км по GPS-треку.

    - точки по времени, погрешность > 100 м отброшена;
    - скачок GPS: переход от предыдущей принятой точки быстрее 150 км/ч — точка отбрасывается
      (скорость — от предыдущей точки, а не от якоря: иначе после долгой стоянки
      «разрешённый» скачок рос бы как 150 км/ч × длительность стоянки);
    - якорный фильтр: в км засчитывается точка не ближе 50 м от якоря (гасит джиттер на месте);
    - сумма haversine по засчитанным сегментам (по прямой между якорями).
    """
    total = 0.0
    anchor: Fix | None = None
    prev: Fix | None = None
    for f in usable_fixes(fixes):
        if anchor is None or prev is None:
            anchor = prev = f
            continue
        step = haversine_km(prev.point, f.point)
        hours = (f.at - prev.at).total_seconds() / 3600.0
        if step >= anchor_km and (hours <= 0 or step / hours > max_speed_kmh):
            continue
        prev = f
        d = haversine_km(anchor.point, f.point)
        if d < anchor_km:
            continue
        total += d
        anchor = f
    return total


@dataclass(frozen=True)
class HomeGuess:
    """Дом менеджера по GPS: method = night (ночные точки) | morning (первая точка дня до 10:00)."""
    lat: float
    lon: float
    method: str
    days: int


def guess_home(fixes: Iterable[Fix]) -> HomeGuess | None:
    """Дом менеджера по треку (окно задаёт вызывающий, по плану — 45 дней).

    1) медиана точек 00:00–07:59, если их ≥ 20 и они есть в ≥ 5 разных ночах;
    2) иначе медиана первой точки каждого дня, если она до 10:00 (нужно ≥ 5 дней);
    3) иначе дома нет.
    """
    ok = usable_fixes(fixes)
    night = [f for f in ok if f.at.time() < HOME_NIGHT_END]
    nights = {f.at.date() for f in night}
    if len(night) >= HOME_MIN_NIGHT_POINTS and len(nights) >= HOME_MIN_NIGHTS:
        lat, lon = median_point([f.point for f in night])
        return HomeGuess(lat, lon, 'night', len(nights))

    first_of_day: dict[date, Fix] = {}
    for f in ok:  # ok отсортирован по времени — первая запись дня и есть первая точка
        first_of_day.setdefault(f.at.date(), f)
    mornings = [f for f in first_of_day.values() if f.at.time() < HOME_MORNING_END]
    if len(mornings) >= HOME_MIN_MORNINGS:
        lat, lon = median_point([f.point for f in mornings])
        return HomeGuess(lat, lon, 'morning', len(mornings))
    return None

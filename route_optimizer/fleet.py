# -*- coding: utf-8 -*-
"""Дизель грузовиков: модель парка машин (план fleet-plan.md, ответ владельца №29).

Чистая логика — без Flask и без БД. Единицы: км, минуты, кг, драмы.
Машина закреплена за водителем, а не за менеджером: заказы дня доставки D (визиты предыдущего
рабочего дня ВСЕХ менеджеров, сб и вс → пн) распределяются по машинам парка и рейсам.

Точная оценка дня доставки (fleet_day) — Монте-Карло по визитам с теми же общими случайными
числами, что у остальной оценки (evaluate.visit_uniforms: клиент | день недели визита | цель):
  1. рейсы — эвристика Кларка–Райта (savings) от склада; тоннаж рейса — не больше самой большой
     машины парка, рейс не длиннее рабочего дня машины (езда + разгрузка);
  2. 2-opt внутри рейса;
  3. рейсы по машинам: самые тяжёлые и длинные — первыми; рейс везёт машина с самым низким расходом
     из тех, что поднимут его по тоннажу и успеют в свой рабочий день (машина делает несколько рейсов,
     пока укладывается). Ни одна не успевает — рейс режется по тоннажу машины, у которой ещё есть
     время; иначе — «рейс за пределами дня» (машин не хватает): его везёт машина, которая освободится
     раньше всех, день помечается;
  4. литры = Σ км рейса × расход машины, которая его везёт.
Быстрая оценка для поиска — search.FleetEstimate (гигантский тур дня, нарезанный по тоннажу).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .geo import Point, in_city

if TYPE_CHECKING:
    from .evaluate import Norms

FLEET_SAMPLES = 30          # проб Монте-Карло на день доставки (год и пик — по 30)
_EPS = 1e-9

Matrix = list[list[float]]


# --- Машины и нормы ---

@dataclass(frozen=True)
class FleetTruck:
    """Машина парка в расчёте: активна, тоннаж и расход заданы."""
    car_code: str
    name: str | None
    capacity_kg: float
    l100: float


@dataclass(frozen=True)
class TruckNorms:
    """Нормы машины: рабочий день и разгрузка на точке (8 мин + 6 мин на тонну)."""
    work_minutes: float
    unload_min_per_stop: float
    unload_min_per_tonne: float

    @classmethod
    def from_settings(cls, s: Mapping[str, Any]) -> TruckNorms:
        h1, m1 = map(int, s['truck_work_start'].split(':'))
        h2, m2 = map(int, s['truck_work_end'].split(':'))
        return cls(work_minutes=float((h2 * 60 + m2) - (h1 * 60 + m1)),
                   unload_min_per_stop=float(s['unload_min_per_stop']),
                   unload_min_per_tonne=float(s['unload_min_per_tonne']))

    def unload(self, kg: float) -> float:
        return self.unload_min_per_stop + self.unload_min_per_tonne * kg / 1000.0


def fleet_trucks(trucks: Mapping[str, Any], names: Mapping[str, str | None]) -> tuple[list[FleetTruck], list[str]]:
    """(машины расчёта, активные машины без тоннажа или расхода). trucks — store.Truck по коду,
    names — название машины по коду (CARS). Привязка машины к менеджеру в расчёте не участвует."""
    fleet, incomplete = [], []
    for code in sorted(trucks):
        t = trucks[code]
        if not t.active:
            continue
        if t.capacity_kg is None or t.fuel_l_per_100km is None:
            incomplete.append(code)
            continue
        fleet.append(FleetTruck(code, names.get(code), float(t.capacity_kg), float(t.fuel_l_per_100km)))
    return fleet, incomplete


# --- Рейсы одной пробы ---

@dataclass(frozen=True)
class Trip:
    truck: str             # код машины
    stops: int
    kg: float
    revenue: float
    km: float
    minutes: float         # езда + разгрузка
    capacity_kg: float     # тоннаж машины, которая везёт
    liters: float
    extra: bool            # не уложился в рабочий день ни одной машины
    items: tuple[int, ...] = ()   # номера заказов (индексы stops) по порядку объезда


@dataclass
class _Stop:
    node: int              # индекс точки в матрице дня (0 — склад)
    kg: float
    revenue: float
    unload: float


def _closed(seq: Sequence[int], stops: Sequence[_Stop], d: Matrix) -> float:
    """Длина рейса «склад → стопы seq → склад» по матрице d."""
    prev, total = 0, 0.0
    for v in seq:
        n = stops[v].node
        total += d[prev][n]
        prev = n
    return total + d[prev][0]


def _two_opt(seq: list[int], stops: Sequence[_Stop], d: Matrix) -> list[int]:
    """2-opt рейса «склад → seq → склад» (склад на месте); длина не растёт."""
    t = [-1, *seq]
    node = [0, *(stops[v].node for v in seq)]
    n = len(t)
    if n < 4:
        return seq
    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b, c = node[i - 1], node[i], node[j]
                e = node[j + 1] if j + 1 < n else node[0]
                if d[a][c] + d[b][e] - d[a][b] - d[c][e] < -_EPS:
                    t[i:j + 1] = t[i:j + 1][::-1]
                    node[i:j + 1] = node[i:j + 1][::-1]
                    improved = True
    return t[1:]


def _savings(light: Sequence[int], stops: Sequence[_Stop], d: Matrix, m: Matrix, cap: float,
             window: float) -> list[list[int]]:
    """Кларк–Райт (параллельная версия): s(i, j) = d(0,i) + d(0,j) − d(i,j), по убыванию (ничьи — по
    номеру); маршруты сливаются через концы, пока груз ≤ cap и время рейса ≤ window."""
    route = {v: v for v in light}
    members = {v: [v] for v in light}
    load = {v: stops[v].kg for v in light}
    time = {v: m[0][stops[v].node] + m[stops[v].node][0] + stops[v].unload for v in light}
    d0, m0 = d[0], m[0]
    pairs = []
    for x, i in enumerate(light):
        a = stops[i].node
        da, d0a = d[a], d0[a]
        for j in light[x + 1:]:
            b = stops[j].node
            s = d0a + d0[b] - da[b]
            if s > _EPS:
                pairs.append((-s, i, j))
    pairs.sort()
    for _, i, j in pairs:
        ri, rj = route[i], route[j]
        if ri == rj or load[ri] + load[rj] > cap + _EPS:
            continue
        A, B = members[ri], members[rj]
        if (A[-1] != i and A[0] != i) or (B[0] != j and B[-1] != j):
            continue
        a, b = stops[i].node, stops[j].node
        t = time[ri] + time[rj] - m[a][0] - m0[b] + m[a][b]
        if t > window + _EPS:
            continue
        if A[-1] != i:
            A.reverse()
        if B[0] != j:
            B.reverse()
        A.extend(B)
        for v in B:
            route[v] = ri
        load[ri] += load.pop(rj)
        time[ri] = t
        del members[rj], time[rj]
    return sorted(members.values(), key=min)


def _cut(seq: Sequence[int], stops: Sequence[_Stop], cap: float) -> list[list[int]]:
    """Рейс по ходу объезда — на куски не тяжелее cap."""
    out: list[list[int]] = []
    cur: list[int] = []
    load = 0.0
    for v in seq:
        if cur and load + stops[v].kg > cap + _EPS:
            out.append(cur)
            cur, load = [], 0.0
        cur.append(v)
        load += stops[v].kg
    if cur:
        out.append(cur)
    return out


def plan_trips(stops: Sequence[_Stop], d: Matrix, m: Matrix, trucks: Sequence[FleetTruck],
               tn: TruckNorms, used: Mapping[str, float] | None = None) -> list[Trip]:
    """Рейсы дня доставки одной пробы (шаги 1–4 из шапки модуля). stops — заказы пробы.
    used — минуты, которые машины уже заняты (закреплённые логистом рейсы плана развоза)."""
    if not stops or not trucks:
        return []
    cap = max(t.capacity_kg for t in trucks)
    window = tn.work_minutes
    # заказ тяжелее самой большой машины — отдельными рейсами «склад → клиент → склад» поровну
    vs: list[_Stop] = []
    origin: list[int] = []         # vs → номер заказа в stops
    singles: list[int] = []
    for i, s in enumerate(stops):
        if s.kg > cap + _EPS:
            n = math.ceil(s.kg / cap)
            for _ in range(n):
                singles.append(len(vs))
                origin.append(i)
                vs.append(_Stop(s.node, s.kg / n, s.revenue / n, tn.unload(s.kg / n)))
        else:
            origin.append(i)
            vs.append(s)
    single_set = set(singles)
    light = [v for v in range(len(vs)) if v not in single_set]
    routes = [_two_opt(r, vs, d) for r in _savings(light, vs, d, m, cap, window)]
    routes += [[v] for v in singles]

    def drive(seq: Sequence[int]) -> tuple[float, float]:
        return _closed(seq, vs, d), _closed(seq, vs, m)

    def item(seq: list[int]) -> tuple:
        km, drive_min = drive(seq)
        minutes = drive_min + math.fsum(vs[v].unload for v in seq)
        kg = math.fsum(vs[v].kg for v in seq)
        # очередь: тяжёлые и длинные — первыми; ничьи — по точкам рейса (детерминизм)
        return (-kg, -minutes, tuple(vs[v].node for v in seq), seq, km, minutes, kg)

    queue = sorted(item(r) for r in routes)
    used = {t.car_code: float((used or {}).get(t.car_code, 0.0)) for t in trucks}
    trips: list[Trip] = []
    while queue:
        _, _, _, seq, km, minutes, kg = queue.pop(0)
        fits = [t for t in trucks if t.capacity_kg >= kg - _EPS and used[t.car_code] + minutes <= window + _EPS]
        extra = not fits
        if extra:
            spare = [t for t in trucks if t.capacity_kg < kg - _EPS and used[t.car_code] < window - _EPS]
            if spare and len(seq) > 1:
                pieces = _cut(seq, vs, max(t.capacity_kg for t in spare))
                if len(pieces) > 1:
                    queue = sorted(queue + [item(_two_opt(p, vs, d)) for p in pieces])
                    continue
            # рейс за пределами дня: везёт машина, которая поднимет груз и освободится раньше всех
            fits = [t for t in trucks if t.capacity_kg >= kg - _EPS]
            truck = min(fits, key=lambda t: (used[t.car_code], t.l100, t.car_code))
        else:
            truck = min(fits, key=lambda t: (t.l100, -t.capacity_kg, t.car_code))
        used[truck.car_code] += minutes
        trips.append(Trip(truck.car_code, len(seq), kg, math.fsum(vs[v].revenue for v in seq), km, minutes,
                          truck.capacity_kg, km * truck.l100 / 100.0, extra, tuple(origin[v] for v in seq)))
    return trips


# --- День с известными заказами (план развоза, dispatch.py) ---

def route_day(points: Sequence[Point], kgs: Sequence[float], revenues: Sequence[float], depot: Point,
              trucks: Sequence[FleetTruck], norms: Norms, tn: TruckNorms,
              used: Mapping[str, float] | None = None) -> list[Trip]:
    """Рейсы дня по известным заказам — тот же расчёт, что у пробы Монте-Карло (plan_trips):
    заказ i — точка points[i], kgs[i] кг; Trip.items — номера заказов по порядку объезда.
    Тяжелее самой большой машины — несколько поездок к одному заказу поровну."""
    uniq = sorted(set(points))
    node = {p: i + 1 for i, p in enumerate(uniq)}
    d, m = _matrices(uniq, depot, norms)
    stops = [_Stop(node[p], float(kg), float(rev), tn.unload(float(kg)))
             for p, kg, rev in zip(points, kgs, revenues)]
    return plan_trips(stops, d, m, trucks, tn, used)


def route_trip(points: Sequence[Point], kgs: Sequence[float], depot: Point, norms: Norms,
               tn: TruckNorms, reorder: bool = True) -> tuple[list[int], float, float]:
    """Рейс «склад → points → склад»: (порядок объезда — номера points, км, минуты: езда + разгрузка).
    reorder — улучшить порядок 2-opt (от заданного; длина не растёт), иначе — как задан."""
    if not points:
        return [], 0.0, 0.0
    d, m = _matrices(points, depot, norms)
    stops = [_Stop(i + 1, float(kg), 0.0, tn.unload(float(kg))) for i, kg in enumerate(kgs)]
    seq = list(range(len(points)))
    if reorder:
        seq = _two_opt(seq, stops, d)
    minutes = _closed(seq, stops, m) + math.fsum(s.unload for s in stops)
    return seq, _closed(seq, stops, d), minutes


# --- День доставки: Монте-Карло ---

@dataclass(frozen=True)
class DeliveryVisit:
    """Визит, чьи заказы везёт день доставки: координата, день недели визита (для seed) и спрос."""
    customer_id: int
    weekday: int
    point: Point
    year: Any        # evaluate.Draw
    peak: Any


@dataclass(frozen=True)
class TruckLoad:
    """Машина в день доставки — средние за пробу (год)."""
    car_code: str
    name: str | None
    capacity_kg: float
    trips: float
    stops: float
    kg: float
    km: float
    liters: float
    minutes: float
    load_pct: float | None     # кг / (рейсы × тоннаж), %


@dataclass(frozen=True)
class FleetDay:
    week: int                  # неделя цикла дня доставки
    weekday: int               # день недели доставки
    visits: int                # визитов плана, чьи заказы везут в этот день
    orders: float              # заказов в среднем (год)
    kg: float
    km: float
    liters: float
    trips: float
    minutes: float             # машино-минуты: езда + разгрузка
    p_short: float             # доля проб года, где рейс не уложился в рабочий день
    trips_total: int           # рейсов во всех пробах года (знаменатель средних по рейсам)
    trip_revenue: float        # Σ выручки рейсов во всех пробах года
    poor_trips: int            # рейсов с выручкой < min_trip_revenue во всех пробах года
    trip_capacity: float       # Σ тоннажа машин по рейсам во всех пробах года (для загрузки)
    kg_total: float            # Σ кг рейсов во всех пробах года
    kg_peak: float             # пик: кг в среднем
    trips_peak: float
    p_short_peak: float
    peak_capacity: float       # пик: Σ тоннажа по рейсам во всех пробах
    peak_kg_total: float
    trucks: tuple[TruckLoad, ...]

    @property
    def load_pct(self) -> float | None:
        return self.kg_total / self.trip_capacity * 100.0 if self.trip_capacity > 0 else None

    @property
    def load_pct_peak(self) -> float | None:
        return self.peak_kg_total / self.peak_capacity * 100.0 if self.peak_capacity > 0 else None


def _matrices(points: Sequence[Point], depot: Point, norms: Norms) -> tuple[Matrix, Matrix]:
    """Км (norms.km — по дорогам или по прямой × извилистость) и минуты езды (скорость города, если
    оба конца в городе) между складом (0) и точками дня."""
    pts = [depot, *points]
    n = len(pts)
    city = [in_city(p, norms.city_center, norms.city_radius_km) for p in pts]
    d = [[0.0] * n for _ in range(n)]
    m = [[0.0] * n for _ in range(n)]
    for a in range(n):
        pa = pts[a]
        for b in range(a + 1, n):
            km = norms.km(pa, pts[b])
            speed = norms.speed_city_kmh if city[a] and city[b] else norms.speed_region_kmh
            d[a][b] = d[b][a] = km
            m[a][b] = m[b][a] = km / speed * 60.0
    return d, m


def _samples(visits: Sequence[DeliveryVisit], node: Mapping[Point, int], season: str, n: int,
             tn: TruckNorms) -> list[list[_Stop]]:
    """n проб: заказы визитов (u < p) и какой из прошлых заказов (кг, выручка) — потоки визитов."""
    from .evaluate import visit_uniforms   # evaluate импортирует fleet
    purpose = 'truck_year' if season == 'year' else 'truck_peak'
    out: list[list[_Stop]] = [[] for _ in range(n)]
    for v in visits:
        draw = v.year if season == 'year' else v.peak
        k = len(draw.values)
        if not k or draw.p <= 0.0:
            continue
        orders, picks = visit_uniforms(v.customer_id, v.weekday, purpose, n)
        for s in range(n):
            if orders[s] < draw.p:
                rev, kg = draw.values[int(picks[s] * k)]
                out[s].append(_Stop(node[v.point], kg, rev, tn.unload(kg)))
    return out


def fleet_day(week: int, weekday: int, visits: Sequence[DeliveryVisit], depot: Point,
              trucks: Sequence[FleetTruck], norms: Norms, tn: TruckNorms, min_trip_revenue: float,
              n: int = FLEET_SAMPLES) -> FleetDay:
    """День доставки: год (30 проб) — км, литры, рейсы, машино-часы, загрузка, выручка рейсов,
    нехватка машин; пик (30 проб) — кг, рейсы, загрузка и нехватка машин в пик.
    Визиты — в каноническом порядке (клиент, день визита, точка): цифры не зависят от порядка плана."""
    visits = sorted(visits, key=lambda v: (v.customer_id, v.weekday, v.point))
    points = sorted({v.point for v in visits})
    node = {p: i + 1 for i, p in enumerate(points)}
    d, m = _matrices(points, depot, norms)
    year = [plan_trips(st, d, m, trucks, tn) for st in _samples(visits, node, 'year', n, tn)]
    peak = [plan_trips(st, d, m, trucks, tn) for st in _samples(visits, node, 'peak', n, tn)]
    flat = [t for trips in year for t in trips]
    per: dict[str, list[Trip]] = {}
    for t in flat:
        per.setdefault(t.truck, []).append(t)
    loads = []
    for truck in trucks:
        mine = per.get(truck.car_code, [])
        if not mine:
            continue
        kg = math.fsum(t.kg for t in mine)
        loads.append(TruckLoad(
            truck.car_code, truck.name, truck.capacity_kg, trips=len(mine) / n,
            stops=sum(t.stops for t in mine) / n, kg=kg / n, km=math.fsum(t.km for t in mine) / n,
            liters=math.fsum(t.liters for t in mine) / n, minutes=math.fsum(t.minutes for t in mine) / n,
            load_pct=kg / (len(mine) * truck.capacity_kg) * 100.0))
    peak_flat = [t for trips in peak for t in trips]
    return FleetDay(
        week=week, weekday=weekday, visits=len(visits),
        orders=sum(t.stops for t in flat) / n, kg=math.fsum(t.kg for t in flat) / n,
        km=math.fsum(t.km for t in flat) / n, liters=math.fsum(t.liters for t in flat) / n,
        trips=len(flat) / n, minutes=math.fsum(t.minutes for t in flat) / n,
        p_short=sum(1 for trips in year if any(t.extra for t in trips)) / n,
        trips_total=len(flat), trip_revenue=math.fsum(t.revenue for t in flat),
        poor_trips=sum(1 for t in flat if t.revenue < min_trip_revenue),
        trip_capacity=math.fsum(t.capacity_kg for t in flat), kg_total=math.fsum(t.kg for t in flat),
        kg_peak=math.fsum(t.kg for t in peak_flat) / n, trips_peak=len(peak_flat) / n,
        p_short_peak=sum(1 for trips in peak if any(t.extra for t in trips)) / n,
        peak_capacity=math.fsum(t.capacity_kg for t in peak_flat),
        peak_kg_total=math.fsum(t.kg for t in peak_flat),
        trucks=tuple(loads),
    )


# --- Неделя парка ---

SHORT_DAY_P = 0.5   # «машин не хватает» в день доставки: так в пик в половине проб и чаще


@dataclass(frozen=True)
class FleetEval:
    trucks: tuple[FleetTruck, ...]
    days: tuple[FleetDay, ...]
    cycle_weeks: int

    def week(self) -> dict[str, float | None]:
        """Неделя парка (суммы по дням цикла / W) — неокруглённые значения."""
        w = float(self.cycle_weeks)
        days = self.days
        fsum = math.fsum
        trips_total = sum(d.trips_total for d in days)
        capacity = fsum(d.trip_capacity for d in days)
        peak_capacity = fsum(d.peak_capacity for d in days)
        active = [d for d in days if d.visits]
        return {
            'km': fsum(d.km for d in days) / w,
            'liters': fsum(d.liters for d in days) / w,
            'trips': fsum(d.trips for d in days) / w,
            'trips_per_day': fsum(d.trips for d in active) / len(active) if active else None,
            'kg_per_day': fsum(d.kg for d in active) / len(active) if active else None,
            'hours': fsum(d.minutes for d in days) / 60.0 / w,
            'days_short': sum(1 for d in days if d.p_short_peak >= SHORT_DAY_P) / w,
            'load_pct': fsum(d.kg_total for d in days) / capacity * 100.0 if capacity > 0 else None,
            'load_pct_peak': (fsum(d.peak_kg_total for d in days) / peak_capacity * 100.0
                              if peak_capacity > 0 else None),
            'trip_revenue': fsum(d.trip_revenue for d in days) / trips_total if trips_total else None,
            'poor_trip_share': sum(d.poor_trips for d in days) / trips_total if trips_total else None,
            'trips_poor': (fsum(d.trips * d.poor_trips / d.trips_total for d in days if d.trips_total) / w),
        }


def delivery_key(week: int, weekday: int, cycle_weeks: int) -> tuple[int, int]:
    """(неделя, день недели) доставки заказов визита: пн–пт → следующий день той же недели,
    сб и вс → пн следующей недели цикла."""
    if 1 <= weekday <= 5:
        return week, weekday + 1
    return week % cycle_weeks + 1, 1


def evaluate_fleet(visits_by_day: Mapping[tuple[int, int], Sequence[DeliveryVisit]], cycle_weeks: int,
                   depot: Point, trucks: Sequence[FleetTruck], norms: Norms, tn: TruckNorms,
                   min_trip_revenue: float) -> FleetEval:
    """Все дни доставки цикла. visits_by_day — (неделя, день) визита → визиты с координатами."""
    groups: dict[tuple[int, int], list[DeliveryVisit]] = {}
    for (week, weekday), vs in visits_by_day.items():
        groups.setdefault(delivery_key(week, weekday, cycle_weeks), []).extend(vs)
    days = []
    for key in sorted(groups):
        days.append(fleet_day(key[0], key[1], groups[key], depot, trucks, norms, tn, min_trip_revenue))
    return FleetEval(tuple(trucks), tuple(days), cycle_weeks)

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
  4. топливо и износ — по остаточному грузу каждого участка и нормам выбранной машины.
     Незаданные нормы сохраняют прежний расход; стоимость износа не является вероятностью поломки.
Быстрая оценка для поиска — search.FleetEstimate (гигантский тур дня, нарезанный по тоннажу).

Окна приёма магазинов и малый центр (windows-center-plan.md, ответы владельца №34–41) — только в плане
развоза: у точки есть окно или она в центре — plan_trips идёт путём _plan_timed; без них — прежним путём,
рейсы те же (модель парка и проверка дизеля №33 окон не знают).

Ровная загрузка рейсов (ответ владельца №44) — тоже только в плане развоза: после сборки route_day(balance=True)
разгружает рейсы тяжелее 90% тоннажа, перекладывая магазины в другие рейсы (5 т + 3 т → 4 т + 4 т), если км и литры
растут не больше 3% (_balance).

Обед водителей (ответ владельца №61) — тоже только в плане развоза (TruckNorms.from_settings(..., lunch=True)): в пути,
гибко, одна пауза lunch_minutes в день машины. Правило (_lunch_trip, _schedule) — одно для всех расчётов дня машины:
  - машина обедает, только если у неё есть работа, начинающаяся после начала окна обеда lunch_from (точка или рейс);
    день кончился раньше — обеда нет;
  - обед начинается на первой границе не раньше lunch_from: сразу после разгрузки у магазина или на складе между
    рейсами (до загрузки следующего; машина стоит на складе в начале окна — с lunch_from) — что раньше; к lunch_to
    границы не было и машина в пути (длинный перегон) — водитель останавливается в дороге в lunch_to (перегон длиннее на
    обед); в lunch_to машина у магазина (ждёт окна или разгружается) — обед сразу после его разгрузки;
  - ожидание окна приёма магазина после начала окна обеда — уже обед (водитель ест, пока ждёт): обед сначала
    «съедает» это ожидание (и простой на складе до загрузки), сверху добавляется только остаток;
  - всё после обеда сдвигается; окна приёма, центр и конец дня проверяются на сдвинутом времени.
Окно начала обеда — и для обучения (обед по факту — learning.lunch_obs). Без обеда (lunch_minutes 0) расчёт — байт в
байт прежний.

Запас на рейс и темп машины (ответ владельца №66, learning.fit_buffer / fit_pace) — тоже только в плане развоза, в том же
правиле дня машины (_schedule):
  - точки — по медиане (как без запаса): прибытия и окна приёма не меняются; рейс занимает D + запас(D), где D — минуты
    рейса от выезда со склада до возвращения (езда, разгрузка, ожидание окон, обед в рейсе; без загрузки), запас(D) =
    buffer_c · √D, не больше BUFFER_CAP_REL · D (trip_reserve). Запас стоит в конце рейса: сдвигает следующий рейс машины
    и проверяется против конца дня;
  - темп машины pace — (множитель разгрузки, множитель пути): разгрузка каждой точки × первый, минуты каждого участка ×
    второй; машины без него — (1, 1).
Запас и обед на складе складываются: рейс, вернувшийся (с запасом) после начала окна обеда, обедает на складе с конца
запаса — оценка с запасом (в жизни водитель, вернувшийся по медиане, может поесть в минуты запаса); так и задумано: запас —
на опоздание рейса, обед — сверх него. В Кларке–Райте (_savings) рейс без окон сливается, только если езда + разгрузка +
запас укладываются в день.
PyVRP пауз и запаса не знает: запас — линейной верхней оценкой (касательная к c·√D в типичном рейсе, _solver: минуты
рёбер и разгрузка × (1 + наклон), полкасательной — на рёбрах «точка → склад»), темп — профилем машины (рёбра × множитель
пути, разгрузка × множитель — на выходящих рёбрах); решает точная проверка (_days), не уложилось по времени — попытка с
запасом × RESERVE_RETRY. Без запаса (buffer_c 0) и темпа — расчёт байт в байт прежний.

Большая машина в Ереване (ответ владельца №68, «приоритет + время») — тоже только в плане развоза: точка в зоне Еревана
(TruckNorms.yerevan_zone → _Stop.yerevan) на большой машине (FleetTruck.big):
  - время — к её разгрузке TruckNorms.yerevan_of(машина) минут (парковка, манёвры, нести дальше): в том же правиле дня
    машины (_schedule, после темпа), PyVRP — профилем машины (минуты рёбер из такой точки);
  - приоритет — мягкий: каждая такая точка стоит как TruckNorms.yerevan_km км пути этой машины (сила приоритета —
    настройка big_truck_yerevan_km, по умолчанию BIG_YEREVAN_KM; _yerevan_bias, драмы по её расходу) — при выборе машины
    рейса, в PyVRP (метры рёбер в такую точку в профиле машины) и при сравнении решений (_better); «Везти после конца дня»
    (earliest) — такой рейс берёт малая машина, если кончает его до конца обычного дня (TruckNorms.day_end), иначе — как
    всегда, кто раньше кончит (плата — при равном времени); выравнивание (_balance)
    не перекладывает такие точки с малой машины на большую. Запрета нет: малым машинам не хватает тоннажа или времени —
    точку везёт большая. Сила 0 — только надбавка времени.
Пустая граница — точек в зоне нет: расчёт байт в байт прежний (модель парка менеджеров зоны не знает).
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field, replace
from statistics import median
from typing import TYPE_CHECKING, Any, Callable, Collection, Mapping, Sequence

from . import vrp
from .geo import Point, in_city, in_polygon
from .running_costs import RunningCost, configured, profile_fields, route_cost
from .tsp import TWO_OPT_MAX_PASSES, is_symmetric

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .evaluate import Norms

FLEET_SAMPLES = 30          # проб Монте-Карло на день доставки (год и пик — по 30)
_EPS = 1e-9
BUFFER_CAP_REL = 0.40       # запас на рейс (№66) — не больше 40% минут рейса
NO_PACE = (1.0, 1.0)        # темп машины без выученных множителей: (разгрузка, путь)
BIG_YEREVAN_KM = 3.0        # приоритет малых машин в Ереване (№68) по умолчанию: точка зоны на большой — как 3 км её пути

Matrix = list[list[float]]


# --- Машины и нормы ---

@dataclass(frozen=True)
class FleetTruck:
    """Машина парка в расчёте: активна, тоннаж и расход заданы."""
    car_code: str
    name: str | None
    capacity_kg: float
    l100: float
    center_ok: bool = False    # можно въезжать в малый центр (№39–41; store.Bundle.truck_center_ok)
    fuel_empty_l_per_100km: float | None = None
    fuel_full_l_per_100km: float | None = None
    wear_amd_per_km: float | None = None
    wear_load_amd_per_km: float | None = None
    big: bool = False          # большая машина (№68; store.Bundle.truck_big): в зоне Еревана — после малых и дольше


@dataclass(frozen=True)
class TruckNorms:
    """Нормы машины: рабочий день и разгрузка на точке (8 мин + 6 мин на тонну)."""
    work_minutes: float
    unload_min_per_stop: float
    unload_min_per_tonne: float
    fuel_price: float = 500.0   # существующий запасной вес топлива, если цена ещё не задана
    fuel_price_estimated: bool = True
    warehouse_load_fixed_min: float = 0.0
    warehouse_load_min_per_tonne: float = 0.0
    work_start_minute: float | None = None
    loading_configured: bool = True
    # своё время магазина — поправка разгрузки, мин на визит (learning.apply_learned: выученное или введённое, №50):
    # точка дня → минуты; нет — 0
    unload_extra: Mapping[Point, float] = field(default_factory=dict, compare=False)
    # обед в пути (№61, правило — в шапке модуля): минут (0 — без обеда), окно начала обеда — минуты от начала дня машины
    lunch_minutes: float = 0.0
    lunch_from: float = 0.0
    lunch_to: float = 0.0
    # запас на рейс (№66, правило — в шапке модуля): мин на √мин рейса (0 — без запаса); процентиль q настроек «Развоза»
    # (≤ 50 — без запаса): выученный buffer_c действует только при том q, при котором его проверили (learning.apply_learned)
    buffer_c: float = 0.0
    buffer_pct: float = 0.0
    # темп машины (№66): машина → (множитель разгрузки, множитель пути); нет машины — NO_PACE
    pace: Mapping[str, tuple[float, float]] = field(default_factory=dict, compare=False)
    # большая машина в Ереване (№68, правило — в шапке модуля): граница зоны Еревана (меньше трёх вершин — зоны нет) и
    # машина → минут к разгрузке каждой её точки в зоне (только большие машины; нет машины — 0)
    yerevan_zone: tuple[Point, ...] = field(default=(), compare=False)
    yerevan_min: Mapping[str, float] = field(default_factory=dict, compare=False)
    yerevan_km: float = BIG_YEREVAN_KM   # сила приоритета малых машин: точка зоны на большой — как столько км её пути
    # конец обычного дня, когда work_minutes — предел переработки («Везти после конца дня», dispatch.overtime); None — он же
    normal_minutes: float | None = None

    @classmethod
    def from_settings(cls, s: Mapping[str, Any], lunch: bool = False) -> TruckNorms:
        """Нормы из настроек. lunch — с обедом в пути (№61: только «Развоз»; модель парка менеджеров его не знает)."""
        h1, m1 = map(int, s['truck_work_start'].split(':'))
        h2, m2 = map(int, s['truck_work_end'].split(':'))
        meal: dict[str, float] = {}
        if lunch and s.get('truck_lunch_min') and s.get('truck_lunch_from') and s.get('truck_lunch_to'):
            (a, b), (c, e) = (map(int, s[k].split(':')) for k in ('truck_lunch_from', 'truck_lunch_to'))
            meal = {'lunch_minutes': float(s['truck_lunch_min']), 'lunch_from': float(a * 60 + b - (h1 * 60 + m1)),
                    'lunch_to': float(c * 60 + e - (h1 * 60 + m1))}
        if lunch and s.get('dispatch_buffer_pct'):   # запас на рейс (№66) — тоже только «Развоз»
            meal['buffer_pct'] = float(s['dispatch_buffer_pct'])
        return cls(work_minutes=float((h2 * 60 + m2) - (h1 * 60 + m1)),
                   unload_min_per_stop=float(s['unload_min_per_stop']),
                   unload_min_per_tonne=float(s['unload_min_per_tonne']),
                   fuel_price=float(s.get('fuel_price_diesel') or s.get('fuel_price_fallback', 500)),
                   fuel_price_estimated=s.get('fuel_price_diesel') is None,
                   warehouse_load_fixed_min=float(s.get('warehouse_load_fixed_min') or 0),
                   warehouse_load_min_per_tonne=float(s.get('warehouse_load_min_per_tonne') or 0),
                   work_start_minute=float(h1 * 60 + m1),
                   loading_configured=(s.get('warehouse_load_fixed_min') is not None and s.get('warehouse_load_min_per_tonne') is not None),
                   **meal)

    def unload(self, kg: float) -> float:
        return self.unload_min_per_stop + self.unload_min_per_tonne * kg / 1000.0

    def unload_at(self, kg: float, point: Point) -> float:
        """Разгрузка у точки: норма + поправка магазина (unload_extra; нет — норма, тот же float)."""
        extra = self.unload_extra.get(point)
        return self.unload(kg) if extra is None else self.unload(kg) + extra

    def load(self, kg: float) -> float:
        return self.warehouse_load_fixed_min + self.warehouse_load_min_per_tonne * kg / 1000.0

    def reserve(self, minutes: float) -> float:
        """Запас рейса D минут (trip_reserve; без запаса — 0.0)."""
        return trip_reserve(self.buffer_c, minutes)

    def pace_of(self, code: str | None) -> tuple[float, float]:
        """Темп машины: (множитель разгрузки, множитель пути); нет машины или множителей — NO_PACE."""
        return self.pace.get(code, NO_PACE) if code is not None else NO_PACE

    def yerevan_of(self, code: str | None) -> float:
        """Надбавка машины к разгрузке точки в зоне Еревана (№68), минут; не большая машина или нет машины — 0.0."""
        return self.yerevan_min.get(code, 0.0) if code is not None else 0.0

    @property
    def day_end(self) -> float:
        """Конец обычного рабочего дня машины (минуты от его начала): при переработке — не её предел."""
        return self.work_minutes if self.normal_minutes is None else self.normal_minutes

    def in_yerevan(self, p: Point) -> bool:
        """Точка в зоне Еревана (geo.in_polygon, как малый центр); зоны нет — False."""
        return len(self.yerevan_zone) >= 3 and in_polygon(p, self.yerevan_zone)


def trip_reserve(c: float, minutes: float) -> float:
    """Запас на рейс D минут (№66): c · √D, не больше BUFFER_CAP_REL · D; c ≤ 0 или D ≤ 0 — 0.0."""
    if c <= 0 or minutes <= 0:
        return 0.0
    return min(c * math.sqrt(minutes), BUFFER_CAP_REL * minutes)


def fleet_trucks(trucks: Mapping[str, Any], names: Mapping[str, str | None]) -> tuple[list[FleetTruck], list[str]]:
    """(машины расчёта, активные машины без тоннажа или расхода). trucks — store.Truck по коду с действующим
    «активна» (Bundle.resolved_trucks: «авто» не разрешено — машина не в расчёте), names — название машины по
    коду (CARS); у ручной машины — её название. Привязка машины к менеджеру в расчёте не участвует."""
    fleet, incomplete = [], []
    for code in sorted(trucks):
        t = trucks[code]
        if t.active is not True:
            continue
        if t.capacity_kg is None or t.fuel_l_per_100km is None:
            incomplete.append(code)
            continue
        fleet.append(FleetTruck(code, names.get(code) or t.name, float(t.capacity_kg),
                                float(t.fuel_l_per_100km), **profile_fields(t)))
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
    wear_amd: float = 0.0
    payload_tonne_km: float = 0.0


@dataclass
class _Stop:
    node: int              # индекс точки в матрице дня (0 — склад)
    kg: float
    revenue: float
    unload: float
    # окно приёма — прибытие (начало разгрузки) в минутах от начала рабочего дня машины: раньше early — машина
    # ждёт, позже late — нарушение; center — точка в малом центре: везёт только машина с правом въезда
    early: float = 0.0
    late: float = math.inf
    center: bool = False
    allowed_trucks: frozenset[str] | None = None
    yerevan: bool = False  # в зоне Еревана (№68): большой машине — надбавка к разгрузке и плата за точку


@dataclass(frozen=True)
class Break:
    """Обед в рейсе (№61): at — начало (минуты от начала дня машины), added — на сколько позже всё после него (обед без
    ожидания окна приёма и простоя на складе, что на него пришлись), stop — после разгрузки какой точки рейса (номер по
    порядку объезда); None — на складе до загрузки рейса. road — в дороге (в конце окна обеда): stop — к какой точке
    едет машина (len — обратно на склад)."""
    at: float
    added: float
    stop: int | None
    road: bool = False


def _split_unload(s: _Stop, n: int, tn: TruckNorms) -> float:
    """Разгрузка одной из n поездок тяжёлого заказа: норма по её кг + поправка магазина за визит (у s она уже в
    s.unload; без поправки разность — ровно 0.0, расчёт байт-в-байт прежний)."""
    return tn.unload(s.kg / n) + (s.unload - tn.unload(s.kg))


def _yerevan_extra(seq: Sequence[int], stops: Sequence[_Stop], tn: TruckNorms, code: str | None) -> float:
    """Надбавка машины code к разгрузке точек рейса seq в зоне Еревана (№68), минут; без неё — 0.0."""
    extra = tn.yerevan_of(code)
    return extra * sum(1 for v in seq if stops[v].yerevan) if extra else 0.0


def _yerevan_bias(seq: Sequence[int], stops: Sequence[_Stop], truck: FleetTruck, tn: TruckNorms) -> float:
    """Плата за точки рейса seq в зоне Еревана на большой машине (№68, приоритет малых машин): tn.yerevan_km км её
    пути за точку — дизель и износ, как стоимость км в PyVRP (vrp.unit_cost), драм; малая машина или точек в зоне нет —
    0.0."""
    n = sum(1 for v in seq if stops[v].yerevan) if truck.big else 0
    return n * tn.yerevan_km * (truck.l100 / 100.0 * tn.fuel_price + (truck.wear_amd_per_km or 0.0)) if n else 0.0


def _earliest_key(bias: float, end: float, tn: TruckNorms) -> bool:
    """Первый ключ выбора машины в режиме earliest (№68): False — машина без платы за Ереван кончает рейс до конца обычного
    дня (её и берём, раньше остальных); иначе True — дальше, как всегда, по времени окончания. Без зоны (плата 0) ключ
    растёт вместе с временем окончания — выбор прежний."""
    return not (bias <= 0 and end <= tn.day_end + _EPS)


def _vehicle_allowed(stop: _Stop, truck: FleetTruck) -> bool:
    return stop.allowed_trucks is None or truck.car_code in stop.allowed_trucks


def _eligible(seq: Sequence[int], stops: Sequence[_Stop], trucks: Sequence[FleetTruck]) -> list[FleetTruck]:
    return [t for t in trucks if all(_vehicle_allowed(stops[i], t) and (t.center_ok or not stops[i].center)
                                    for i in seq)]


def _closed(seq: Sequence[int], stops: Sequence[_Stop], d: Matrix) -> float:
    """Длина рейса «склад → стопы seq → склад» по матрице d."""
    prev, total = 0, 0.0
    for v in seq:
        n = stops[v].node
        total += d[prev][n]
        prev = n
    return total + d[prev][0]


def _schedule(seq: Sequence[int], stops: Sequence[_Stop], m: Matrix, start: float,
              arrivals: list[float] | None = None, parts: dict[str, Any] | None = None,
              lunch: tuple[float, float, bool, float] | None = None, breaks: list[Break] | None = None,
              buffer: float = 0.0, pace: tuple[float, float] = NO_PACE, yerevan: float = 0.0) -> tuple[float, bool]:
    """Рейс «склад → seq → склад» с выезда start (минуты от начала дня машины), с ожиданием у окон:
    (минуты от выезда до возвращения на склад — езда + разгрузка + ожидание, все окна соблюдены).
    Без ожидания минуты — ровно езда + разгрузка, как в остальном модуле. arrivals — сюда дописываются
    прибытия к точкам (начало разгрузки). parts — слагаемые этих минут из того же расчёта (пояснение на странице
    «Развоза»): 'loading' — загрузка на складе (без часовой модели — 0), 'legs' — (езда, ожидание, разгрузка) к каждой
    точке и последним — (езда на склад, 0, 0). lunch — (начало окна обеда, минуты обеда, после рейса у машины есть
    работа, конец окна обеда): обед машины ещё впереди — он встаёт после разгрузки первой точки, закончившейся не раньше
    начала окна (последней — только если работа после рейса есть или сама точка началась после начала окна), ожидание
    окна приёма этой точки после начала окна обеда идёт в счёт обеда; машина в пути в конце окна обеда — обед в дороге
    (перегон длиннее на обед; обратно на склад — только если работа после рейса есть); Break — в breaks (минуты рейса —
    с ним). buffer — c запаса на рейс (№66): к минутам рейса — запас trip_reserve(c, D) в конце, D — минуты без загрузки
    (parts['buffer']); прибытия — без него. pace — темп машины (множитель разгрузки, множитель пути): разгрузка точек и
    минуты участков — с ним. yerevan — надбавка большой машины к разгрузке точек в зоне Еревана (№68, после темпа)."""

    def on_road(t: float, drive: float, last: bool) -> bool:
        """Обед ещё впереди, а конец окна обеда приходится на этот перегон."""
        return (lunch is not None and breaks is not None and not breaks and (lunch[2] or not last)
                and t <= lunch[3] + _EPS and t + drive > lunch[3] + _EPS)
    dynamic = hasattr(m, 'travel')
    t, prev, wait, ok = start, 0, 0.0, True
    legs: list[tuple[float, float, float]] = []
    loading = paused = 0.0
    mu, mt = pace
    yerevan = yerevan if yerevan > 0 and any(stops[v].yerevan for v in seq) else 0.0
    paced = pace != NO_PACE or yerevan > 0
    if dynamic:
        loading = m.load(math.fsum(stops[v].kg for v in seq))
        t += loading
    for k, v in enumerate(seq):
        s = stops[v]
        drive = m.travel(prev, s.node, t) if dynamic else m[prev][s.node]
        unload = s.unload
        if paced:
            drive, unload = drive * mt, unload * mu
            if yerevan and s.yerevan:
                unload += yerevan
        if on_road(t, drive, False):
            breaks.append(Break(lunch[3], lunch[1], k, True))   # type: ignore[index, union-attr]
            t += lunch[1]   # type: ignore[index]
            paused += lunch[1]   # type: ignore[index]
        t += drive
        reach = t
        early = 0.0
        if t < s.early:
            early = s.early - t
            wait += early
            t = s.early
        if t > s.late + _EPS:
            ok = False
        if arrivals is not None:
            arrivals.append(t)
        if parts is not None:
            legs.append((drive, early, unload))
        t += unload
        prev = s.node
        if (lunch is not None and breaks is not None and not breaks and t >= lunch[0] - _EPS
                and (lunch[2] or k + 1 < len(seq) or reach >= lunch[0] - _EPS)):
            pause = max(0.0, lunch[1] - max(0.0, reach + early - max(reach, lunch[0])))
            breaks.append(Break(t, pause, k))
            t += pause
            paused += pause
    back = m.travel(prev, 0, t) if dynamic else m[prev][0]
    if paced:
        back *= mt
    if on_road(t, back, True):
        breaks.append(Break(lunch[3], lunch[1], len(seq), True))   # type: ignore[index, union-attr]
        t += lunch[1]   # type: ignore[index]
        paused += lunch[1]   # type: ignore[index]
    if parts is not None:
        parts['loading'] = loading
        parts['legs'] = [*legs, (back, 0.0, 0.0)]
    if dynamic or paced:
        total = t + back - start
    elif paused:
        total = _closed(seq, stops, m) + math.fsum(stops[v].unload for v in seq) + wait + paused
    else:
        total = _closed(seq, stops, m) + math.fsum(stops[v].unload for v in seq) + wait
    if buffer > 0:
        reserve = trip_reserve(buffer, total - loading)
        if parts is not None:
            parts['buffer'] = reserve
        return total + reserve, ok
    return total, ok


def _lunch_trip(seq: Sequence[int], stops: Sequence[_Stop], m: Matrix, free: float, tn: TruckNorms, pending: bool,
                more: bool, arrivals: list[float] | None = None, parts: dict[str, Any] | None = None,
                code: str | None = None) -> tuple[float, float, bool, Break | None]:
    """Рейс машины, свободной на складе с free (минуты от начала её дня), по правилу обеда (шапка модуля): (выезд —
    начало загрузки, минуты от выезда до возвращения, окна соблюдены, обед в рейсе или None). pending — обед машины ещё
    впереди (обед задан и не взят раньше), more — после рейса у машины есть рейсы. Выезд без обеда — как всегда
    (_departure: не ждать у окна первой точки) не раньше начала окна обеда — обед на складе с max(free, lunch_from),
    простой до выезда идёт в его счёт; иначе — обед в рейсе (_schedule). Без обеда — ровно _departure и _schedule.
    parts['lunch'] — обед (при заданном обеде). code — машина рейса: запас на рейс и её темп (№66, tn.buffer_c, pace_of),
    надбавка в зоне Еревана (№68, yerevan_of)."""
    pace, yer = tn.pace_of(code), tn.yerevan_of(code)
    depart = _departure(seq, stops, m, free, pace)
    brk = None
    if pending and tn.lunch_minutes > 0 and depart >= tn.lunch_from - _EPS:
        at = max(free, tn.lunch_from)
        later = _departure(seq, stops, m, at + tn.lunch_minutes, pace) if at + tn.lunch_minutes > depart + _EPS else depart
        brk = Break(at, later - depart, None)
        depart = later
        minutes, ok = _schedule(seq, stops, m, depart, arrivals, parts, buffer=tn.buffer_c, pace=pace, yerevan=yer)
    elif pending and tn.lunch_minutes > 0:
        got: list[Break] = []
        latest = tn.lunch_to if tn.lunch_to > tn.lunch_from else math.inf
        minutes, ok = _schedule(seq, stops, m, depart, arrivals, parts, (tn.lunch_from, tn.lunch_minutes, more, latest),
                                got, tn.buffer_c, pace, yer)
        brk = got[0] if got else None
    else:
        minutes, ok = _schedule(seq, stops, m, depart, arrivals, parts, buffer=tn.buffer_c, pace=pace, yerevan=yer)
    if parts is not None and tn.lunch_minutes > 0:
        parts['lunch'] = brk
    return depart, minutes, ok, brk


def _two_opt(seq: list[int], stops: Sequence[_Stop], d: Matrix,
             ok: Callable[[list[int]], bool] | None = None) -> list[int]:
    """2-opt рейса «склад → seq → склад» (склад на месте); длина не растёт. ok — ход принимается, только если
    рейс после него допустим (окна приёма). Направленная матрица — с внутренними рёбрами развёрнутого куска
    (как tsp.two_opt): ход, который короче только «в одну сторону», не принимается и не пропускается; рейс из двух
    точек тоже разворачивается. Проходов — не больше TWO_OPT_MAX_PASSES."""
    t = [-1, *seq]
    node = [0, *(stops[v].node for v in seq)]
    n = len(t)
    if n < 3:
        return seq
    directed = not is_symmetric(d, node)
    if n < 4 and not directed:
        return seq
    improved, passes = True, 0
    while improved and passes < TWO_OPT_MAX_PASSES:
        improved, passes = False, passes + 1
        for i in range(1, n - 1):
            fwd = back = 0.0   # ход куска node[i..j] вперёд и назад
            for j in range(i + 1, n):
                a, b, c = node[i - 1], node[i], node[j]
                e = node[j + 1] if j + 1 < n else node[0]
                delta = d[a][c] + d[b][e] - d[a][b] - d[c][e]
                if directed:
                    p = node[j - 1]
                    fwd += d[p][c]
                    back += d[c][p]
                    delta += back - fwd
                if delta < -_EPS:
                    if ok is not None and not ok(t[1:i] + t[i:j + 1][::-1] + t[j + 1:]):
                        continue
                    t[i:j + 1] = t[i:j + 1][::-1]
                    node[i:j + 1] = node[i:j + 1][::-1]
                    fwd, back = back, fwd
                    improved = True
    return t[1:]


def _departure(seq, stops, m, start, pace=NO_PACE):
    first = stops[seq[0]]
    mt = pace[1]
    if not hasattr(m, 'travel'):
        return max(start, first.early-(m[0][first.node] if mt == 1.0 else m[0][first.node]*mt))
    loading = m.load(math.fsum(stops[v].kg for v in seq))
    def arrival(t):
        drive = m.travel(0, first.node, t+loading)
        return t+loading+(drive if mt == 1.0 else drive*mt)
    if arrival(start) >= first.early:
        return start
    lo, hi = start, first.early
    for _ in range(40):
        mid = (lo+hi)/2
        if arrival(mid) < first.early:
            lo = mid
        else:
            hi = mid
    return hi


def _waiting(seq, stops, m, start, minutes):
    if not hasattr(m, 'travel'):
        return minutes-_closed(seq, stops, m)-math.fsum(stops[v].unload for v in seq)
    clock = start+m.load(math.fsum(stops[v].kg for v in seq))
    prev, wait = 0, 0.
    for v in seq:
        s = stops[v]
        arrival = clock+m.travel(prev,s.node,clock)
        wait += max(0.,s.early-arrival)
        clock, prev = max(arrival,s.early)+s.unload, s.node
    return wait


def _savings(light: Sequence[int], stops: Sequence[_Stop], d: Matrix, m: Matrix, cap: float,
             window: float, fits: Callable[..., list[int] | None] | None = None,
             reserve: Callable[[float], float] | None = None) -> list[list[int]]:
    """Кларк–Райт (параллельная версия): s(i, j) = d(0,i) + d(0,j) − d(i,j), по убыванию (ничьи — по
    номеру); маршруты сливаются через концы, пока груз ≤ cap и время рейса ≤ window. fits(a, b, reverse) — проверка
    слияния a + b (окна приёма, тоннаж центра): допустимый порядок объезда слитого рейса или None.

    Направленные матрицы (одностороннее движение): s(i → j) = d(i, 0) + d(0, j) − d(i, j) — рейс, который кончается
    в i, + рейс, который начинается с j, в обе стороны каждой пары; рейсы не разворачиваются (у развёрнутого другие км
    и минуты), поэтому время слитого рейса time(A) + time(B) − m(i, 0) − m(0, j) + m(i, j) — точное. Симметричные —
    прежний расчёт (те же рейсы)."""
    nodes = [0, *(stops[v].node for v in light)]
    directed = not (is_symmetric(d, nodes) and is_symmetric(m, nodes))
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
            if directed:   # конец рейса i → начало рейса j и конец j → начало i
                for u, v, s in ((i, j, da[0] + d0[b] - da[b]), (j, i, d[b][0] + d0a - d[b][a])):
                    if s > _EPS:
                        pairs.append((-s, u, v))
                continue
            s = d0a + d0[b] - da[b]
            if s > _EPS:
                pairs.append((-s, i, j))
    pairs.sort()
    for _, i, j in pairs:
        ri, rj = route[i], route[j]
        if ri == rj or load[ri] + load[rj] > cap + _EPS:
            continue
        A, B = members[ri], members[rj]
        if directed:
            if A[-1] != i or B[0] != j:
                continue
        elif (A[-1] != i and A[0] != i) or (B[0] != j and B[-1] != j):
            continue
        a, b = stops[i].node, stops[j].node
        t = time[ri] + time[rj] - m[a][0] - m0[b] + m[a][b]
        # езда + разгрузка (+ запас на рейс, №66: reserve) — нижняя граница времени рейса и с ожиданием у окон
        if (t + reserve(t) if reserve is not None else t) > window + _EPS:
            continue
        if fits is not None:
            merged = (fits(A, B, reverse=False) if directed else
                      fits(A if A[-1] == i else A[::-1], B if B[0] == j else B[::-1]))
            if merged is None:
                continue
            A[:] = merged
        else:
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


def _load_limit(seq: Sequence[int], stops: Sequence[_Stop], truck: FleetTruck,
                trucks: Sequence[FleetTruck], load_cap: float | None) -> float:
    """Предел рейса; исключение выше 90% — только отдельный неизбежно тяжёлый заказ."""
    if load_cap is None:
        return truck.capacity_kg
    if len(seq) == 1:
        stop = stops[seq[0]]
        top = max((t.capacity_kg for t in _eligible(seq, stops, trucks)), default=0.0)
        if stop.kg > load_cap * top + _EPS:
            return truck.capacity_kg
    return load_cap * truck.capacity_kg


def _time_head(seq: Sequence[int], stops: Sequence[_Stop], m: Matrix, trucks: Sequence[FleetTruck],
               used: Mapping[str, float], window: float, load_cap: float | None = None,
               meal: Callable[[str, float], float] | None = None, tn: TruckNorms | None = None) -> int:
    """Сколько первых точек рейса (по ходу объезда) успевает хоть одна машина до конца рабочего дня
    (с её тоннажем и уже занятым временем; meal — минуты рейса машины с обедом, plan_trips; tn — запас на рейс и темп
    машины, №66, и надбавка в зоне Еревана, №68); 0 — ни одной."""
    best = 0
    for t in trucks:
        left = window - used[t.car_code]
        mu, mt = tn.pace_of(t.car_code) if tn is not None else NO_PACE
        yer = tn.yerevan_of(t.car_code) if tn is not None else 0.0
        k, kg, unload, extra = 0, 0.0, 0.0, 0.0
        while k < len(seq):
            kg += stops[seq[k]].kg
            unload += stops[seq[k]].unload
            if yer and stops[seq[k]].yerevan:
                extra += yer
            need = (_closed(seq[:k + 1], stops, m) + unload if (mu, mt) == NO_PACE
                    else _closed(seq[:k + 1], stops, m) * mt + unload * mu)
            if extra:
                need += extra
            if tn is not None:
                need += tn.reserve(need)
            if meal is not None:
                need = meal(t.car_code, need)
            if kg > _load_limit(seq[:k + 1], stops, t, trucks, load_cap) + _EPS or need > left + _EPS:
                break
            k += 1
        best = max(best, k)
    return best


def _sequence_cost(seq: Sequence[int], stops: Sequence[_Stop], d: Matrix, truck: FleetTruck) -> RunningCost:
    nodes = [0, *(stops[i].node for i in seq), 0]
    return route_cost([d[a][b] for a, b in zip(nodes, nodes[1:])], [stops[i].kg for i in seq], truck)


def trip_running_cost(points: Sequence[Point], kgs: Sequence[float], depot: Point,
                      norms: Norms, truck: FleetTruck) -> RunningCost:
    norms = norms.for_trucks()
    nodes = [depot, *points, depot]
    return route_cost([norms.km(a, b) for a, b in zip(nodes, nodes[1:])], kgs, truck)


def _cost_order(seq: list[int], stops: Sequence[_Stop], d: Matrix, truck: FleetTruck,
                tn: TruckNorms, allowed: Callable[[list[int]], bool]) -> list[int]:
    """2-opt по топливу и износу; допустимость проверяется на полном новом порядке."""
    if not configured(truck) or len(seq) < 2:
        return seq
    best = list(seq)
    value = _sequence_cost(best, stops, d, truck).total_amd(tn.fuel_price)
    for _ in range(3):
        hit = None
        for a in range(len(best) - 1):
            for b in range(a + 2, len(best) + 1):
                candidate = best[:a] + best[a:b][::-1] + best[b:]
                cost = _sequence_cost(candidate, stops, d, truck).total_amd(tn.fuel_price)
                if cost < value - 1e-6 and allowed(candidate):
                    hit, value = candidate, cost
        if hit is None:
            break
        best = hit
    return best


def plan_trips(stops: Sequence[_Stop], d: Matrix, m: Matrix, trucks: Sequence[FleetTruck],
               tn: TruckNorms, used: Mapping[str, float] | None = None, overflow: bool = True,
               earliest: bool = False, reasons: dict[int, str] | None = None,
               busy: Mapping[str, Sequence[tuple[float, float]]] | None = None,
               departs: list[float] | None = None,
               fixed: Sequence[tuple[str, Sequence[int], float]] | None = None,
               load_cap: float | None = None, fed: Collection[str] = ()) -> list[Trip]:
    """Рейсы дня доставки одной пробы (шаги 1–4 из шапки модуля). stops — заказы пробы.
    used — минуты, которые машины уже заняты (закреплённые логистом рейсы плана развоза).
    overflow — рейс, который ни одна машина не успевает до конца рабочего дня, всё равно везёт машина,
    освободившаяся раньше всех (модель парка: так считается нехватка машин); False — за конец дня не
    планируем (план развоза): от такого рейса берётся начало по ходу объезда, которое машина ещё
    успевает, остальное — снова в очередь; что не успевает никто — не назначается. Целостность
    тяжёлых заказов при overflow=False — в route_day. earliest — рейс достаётся машине, которая раньше
    всех его закончит (переработка: часы сверх дня — короче), а не самой экономичной.
    Точка с окном приёма или в малом центре — расчёт _plan_timed; reasons — туда пишутся причины
    неназначенных заказов (номер в stops → window | center | time). busy — занятые отрезки машин [выезд,
    возвращение] вместо used, когда между ними есть промежутки (закреплённый рейс выезжает позже — под окно
    первой точки): тоже _plan_timed, рейсы встают и в промежутки. departs — туда пишутся выезды рейсов (только
    _plan_timed; рейсы машины надо везти в порядке выезда). fixed — готовые рейсы (машина, номера заказов по
    порядку объезда, выезд в прежнем плане: закреплённые логистом): машина, состав и, если можно, время не
    меняются, остальные рейсы раскладываются вокруг (_plan_timed). Без окон, центра, busy и fixed — прежний путь.
    Обед (tn.lunch_minutes, №61): fed — машины, чей обед уже в занятом времени (used, busy); прежний путь — с запасом:
    рейс, после которого день машины заходит за начало окна обеда, — с обедом (точно — dispatch._timeline)."""
    if not stops or not trucks:
        return []
    if hasattr(m, 'travel') or busy is not None or fixed is not None or any(
            s.early > 0 or s.late < math.inf or s.center or s.allowed_trucks is not None for s in stops):
        return _plan_timed(stops, d, m, trucks, tn, used, overflow, earliest, reasons, busy, departs, fixed, load_cap,
                           fed)
    physical_cap = max(t.capacity_kg for t in trucks)
    cap = physical_cap * (load_cap if load_cap is not None else 1.0)
    window = tn.work_minutes
    # заказ тяжелее самой большой машины — отдельными рейсами «склад → клиент → склад» поровну
    vs: list[_Stop] = []
    origin: list[int] = []         # vs → номер заказа в stops
    singles: list[int] = []
    for i, s in enumerate(stops):
        if s.kg > physical_cap + _EPS:
            n = math.ceil(s.kg / cap)
            for _ in range(n):
                singles.append(len(vs))
                origin.append(i)
                vs.append(_Stop(s.node, s.kg / n, s.revenue / n, _split_unload(s, n, tn), yerevan=s.yerevan))
        else:
            if s.kg > cap + _EPS:
                singles.append(len(vs))
            origin.append(i)
            vs.append(s)
    single_set = set(singles)
    light = [v for v in range(len(vs)) if v not in single_set]
    routes = [_two_opt(r, vs, d) for r in _savings(light, vs, d, m, cap, window, reserve=tn.reserve)]
    routes += [[v] for v in singles]

    def drive(seq: Sequence[int]) -> tuple[float, float]:
        return _closed(seq, vs, d), _closed(seq, vs, m)

    def shortest(seq: list[int]) -> tuple:
        """Рейс в порядке 2-opt (короче по км) или как есть — что быстрее по минутам: голова рейса
        резалась под время, и порядок, короче по км, может не успеть."""
        a, b = item(seq), item(_two_opt(seq, vs, d))
        return b if b[5] <= a[5] + _EPS else a

    def item(seq: list[int]) -> tuple:
        km, drive_min = drive(seq)
        minutes = drive_min + math.fsum(vs[v].unload for v in seq)
        kg = math.fsum(vs[v].kg for v in seq)
        # очередь: тяжёлые и длинные — первыми; ничьи — по точкам рейса (детерминизм)
        return (-kg, -minutes, tuple(vs[v].node for v in seq), seq, km, minutes, kg)

    queue = sorted(item(r) for r in routes)
    used = {t.car_code: float((used or {}).get(t.car_code, 0.0)) for t in trucks}
    eaten = {t.car_code: t.car_code in fed for t in trucks}

    def meal(code: str, minutes: float) -> float:
        """Минуты рейса машины с обедом: её день с рейсом заходит за начало окна, а обеда в нём ещё не было."""
        late = tn.lunch_minutes > 0 and not eaten[code] and used[code] + minutes > tn.lunch_from + _EPS
        return minutes + tn.lunch_minutes if late else minutes

    def occupy(code: str, seq: Sequence[int], minutes: float) -> float:
        """Минуты рейса у машины code (№66): езда × её множитель пути, разгрузка × множитель разгрузки, надбавка в зоне
        Еревана (№68) и запас на рейс; без темпа, надбавки и запаса — minutes (езда + разгрузка) до байта."""
        mu, mt = tn.pace_of(code)
        if (mu, mt) != NO_PACE:
            minutes = _closed(seq, vs, m) * mt + math.fsum(vs[v].unload for v in seq) * mu
        extra = _yerevan_extra(seq, vs, tn, code)
        if extra:
            minutes += extra
        return minutes + tn.reserve(minutes)
    trips: list[Trip] = []
    while queue:
        _, _, _, seq, km, minutes, kg = queue.pop(0)
        fits = [t for t in trucks if _load_limit(seq, vs, t, trucks, load_cap) >= kg - _EPS
                and used[t.car_code] + meal(t.car_code, occupy(t.car_code, seq, minutes)) <= window + _EPS]
        extra = not fits
        if extra:
            spare = [t for t in trucks if _load_limit(seq, vs, t, trucks, load_cap) < kg - _EPS
                     and used[t.car_code] < window - _EPS]
            if spare and len(seq) > 1:
                pieces = _cut(seq, vs, max(_load_limit(seq, vs, t, trucks, load_cap) for t in spare))
                if len(pieces) > 1:
                    queue = sorted(queue + [item(_two_opt(p, vs, d)) for p in pieces])
                    continue
            if not overflow:
                if len(seq) > 1:
                    # порядок объезда случаен по направлению: успевает больше — с того конца и режем;
                    # ни с одного конца ни одной точки — отрываем крайнюю, чтобы проверить остальные
                    lunch = meal if tn.lunch_minutes > 0 else None
                    fwd = _time_head(seq, vs, m, trucks, used, window, load_cap, lunch, tn)
                    back = _time_head(seq[::-1], vs, m, trucks, used, window, load_cap, lunch, tn)
                    s2, head = (seq, fwd) if fwd >= back else (seq[::-1], back)
                    head = min(max(head, 1), len(seq) - 1)
                    queue = sorted(queue + [shortest(s2[:head]), shortest(s2[head:])])
                continue
            # рейс за пределами дня: везёт машина, которая поднимет груз и освободится раньше всех
            fits = [t for t in trucks if t.capacity_kg >= kg - _EPS]
            truck = min(fits, key=lambda t: (used[t.car_code], _sequence_cost(seq, vs, d, t).total_amd(tn.fuel_price), t.car_code))
        elif earliest:   # приоритет малых в Ереване (№68): малая, что кончит до конца обычного дня, — _earliest_key
            def early(t: FleetTruck) -> tuple:
                end, bias = used[t.car_code] + occupy(t.car_code, seq, minutes), _yerevan_bias(seq, vs, t, tn)
                return (_earliest_key(bias, end, tn), end, _sequence_cost(seq, vs, d, t).total_amd(tn.fuel_price) + bias,
                        t.car_code)
            truck = min(fits, key=early)
        else:
            truck = min(fits, key=lambda t: (_sequence_cost(seq, vs, d, t).total_amd(tn.fuel_price)
                                             + _yerevan_bias(seq, vs, t, tn), -t.capacity_kg, t.car_code))
        seq = _cost_order(seq, vs, d, truck, tn,
                          lambda cand: _closed(cand, vs, m) <= _closed(seq, vs, m) + _EPS)
        km, drive_min = drive(seq)
        minutes = occupy(truck.car_code, seq, drive_min + math.fsum(vs[v].unload for v in seq))
        cost = _sequence_cost(seq, vs, d, truck)
        if tn.lunch_minutes > 0:
            busy_min = meal(truck.car_code, minutes)
            eaten[truck.car_code] = eaten[truck.car_code] or busy_min > minutes
            used[truck.car_code] += busy_min
        else:
            used[truck.car_code] += minutes
        trips.append(Trip(truck.car_code, len(seq), kg, math.fsum(vs[v].revenue for v in seq), km, minutes,
                          truck.capacity_kg, cost.liters, extra, tuple(origin[v] for v in seq),
                          cost.wear_amd, cost.payload_tonne_km))
    return trips


WAIT_MERGE_SLACK = 15.0     # слияние рейсов может добавить ожидания внутри рейса не больше, мин


def _plan_timed(stops: Sequence[_Stop], d: Matrix, m: Matrix, trucks: Sequence[FleetTruck], tn: TruckNorms,
                used: Mapping[str, float] | None, overflow: bool, earliest: bool,
                reasons: dict[int, str] | None, busy: Mapping[str, Sequence[tuple[float, float]]] | None = None,
                departs: list[float] | None = None,
                fixed: Sequence[tuple[str, Sequence[int], float]] | None = None,
                load_cap: float | None = None, fed: Collection[str] = ()) -> list[Trip]:
    """plan_trips с окнами приёма и малым центром (windows-center-plan.md §5). Отличия от прежнего пути:
      - время рейса — _schedule с ожиданием у окон (ожидание — время машины);
      - Кларк–Райт сливает рейсы, только если слитый допустим при выезде в начале дня и ожидание внутри рейса
        (после первой точки — его не убрать поздним выездом) растёт не больше WAIT_MERGE_SLACK; рейс с точкой
        центра («центровой») — не тяжелее самой большой машины с правом въезда; 2-opt окон не нарушает;
      - очередь: рейсы с крайним сроком (минимальный late) первыми, по возрастанию срока (EDF); затем
        центровые — раньше остальных; затем по времени, раньше которого рейс не начать (release); дальше — как
        прежде: тяжёлые и длинные первыми;
      - день машины — занятые отрезки, а не одно «занята до»: рейс встаёт в самый ранний промежуток, где
        укладывается с окнами; выезд — так, чтобы не ждать у первой точки (рейс «после 14:00» уходит к
        14:00, утро остаётся другим рейсам); подходит машина, которая поднимет рейс, имеет право на центр (для
        центрового) и где рейс встаёт; из подходящих — как прежде, самая экономичная, но пока в очереди есть
        центровые рейсы, нецентровому достаётся машина без права въезда (иначе JAC заберёт чужие рейсы и центр
        останется без машины);
      - никто не берёт (overflow=False): нарезка по тоннажу среди допустимых машин; иначе, если без окон
        машина рейс взяла бы, — точки с нарушенным окном выносятся одиночными рейсами; иначе — голова рейса,
        которую машина успевает, и хвост; одиночный рейс, который не берёт никто, не планируется
        (reasons: window — мешает окно, time — не успевают до конца дня); точка центра без машины с правом
        въезда среди машин дня — сразу center.
    Каждый шаг без назначения либо снимает рейс из очереди, либо заменяет его кусками строго короче —
    цикл конечен. overflow=True (без предела переработки) — рейс, который никто не берёт, везёт допустимая
    машина, что освободится раньше всех, окна при этом могут нарушаться (видно в плане).
    Рейсы — по времени выезда: машина везёт их подряд (dispatch._timeline), ранний выезд только раньше
    привозит, а у окна машина ждёт — окна и конец дня соблюдаются.
    fixed — закреплённые рейсы: не режутся и не сливаются, их машина — заданная (тоннаж и центр — выбор логиста);
    ставятся раньше остальных — со своего прежнего выезда, если там свободно и окна соблюдаются (закрепили — значит,
    рейс и его время устраивают), иначе в самый ранний промежуток, где укладываются, иначе в конец дня своей машины
    (extra); причины у них нет. Остальные рейсы встают вокруг.
    Обед (№61): отрезок рейса в дне машины — с обедом по правилу _lunch_trip, если обед машины к началу промежутка ещё
    не взят (held — концы отрезков, в которые он вошёл; fed — машины, чей обед уже в занятом времени busy / used: он —
    в последнем их отрезке), с запасом: и после последней точки (вдруг после рейса будет ещё рейс), а рейс в промежутке
    перед другим рейсом, вернувшийся после начала окна без обеда, — вместе с обедом на складе после него. Точный
    расчёт (dispatch._timeline) обед только переносит раньше или снимает — рейсы не позже своих отрезков."""
    window = tn.work_minutes
    lunch = tn.lunch_minutes > 0
    why = reasons if reasons is not None else {}
    physical_cap = max(t.capacity_kg for t in trucks)
    ratio = load_cap if load_cap is not None else 1.0
    cap = physical_cap * ratio
    central = [t for t in trucks if t.center_ok]
    physical_cap_c = max((t.capacity_kg for t in central), default=0.0)
    cap_c = physical_cap_c * ratio
    access_constrained = any(s.allowed_trucks is not None for s in stops)
    vs: list[_Stop] = []
    origin: list[int] = []
    singles: list[int] = []
    pinned = {i for _, idx, _ in (fixed or ()) for i in idx}
    at: dict[int, int] = {}                        # заказ закреплённого рейса → номер в vs
    for i, s in enumerate(stops):
        if i in pinned:
            at[i] = len(vs)
            origin.append(i)
            vs.append(s)
            continue
        accessible = [t for t in trucks if _vehicle_allowed(s, t)]
        if not accessible:
            why[i] = 'vehicle'
            continue
        eligible = [t for t in accessible if t.center_ok or not s.center]
        if not eligible:
            why[i] = 'center'
            continue
        physical = max(t.capacity_kg for t in eligible)
        c = physical * ratio
        if s.kg > physical + _EPS:
            n = math.ceil(s.kg / c)
            for _ in range(n):
                singles.append(len(vs))
                origin.append(i)
                vs.append(replace(s, kg=s.kg / n, revenue=s.revenue / n, unload=_split_unload(s, n, tn)))
        else:
            if s.kg > c + _EPS:
                singles.append(len(vs))
            origin.append(i)
            vs.append(s)

    def timed(seq: Sequence[int]) -> bool:
        return any(vs[v].early > 0 or vs[v].late < math.inf for v in seq)

    def is_central(seq: Sequence[int]) -> bool:
        return any(vs[v].center for v in seq)

    def ok_at_start(seq: list[int]) -> bool:
        minutes, ok = _schedule(seq, vs, m, 0.0, buffer=tn.buffer_c)
        if lunch and minutes >= tn.lunch_from - _EPS:      # с начала дня рейс заходит в окно обеда — с обедом
            dep, mins, ok, _ = _lunch_trip(seq, vs, m, 0.0, tn, True, True)
            minutes = dep + mins
        return ok and minutes <= window + _EPS

    def first_wait(seq: Sequence[int], start: float, code: str | None = None) -> float:
        """Ожидание у первой точки: его убирает поздний выезд (code — машина: её темп)."""
        return _departure(seq, vs, m, start, tn.pace_of(code))-start

    def idle(seq: Sequence[int]) -> float:
        """Ожидание внутри рейса — после первой точки (поздним выездом его не убрать)."""
        t, prev, wait = (m.load(math.fsum(vs[v].kg for v in seq)) if hasattr(m, 'load') else 0.), 0, 0.0
        for k, v in enumerate(seq):
            s = vs[v]
            t += m.travel(prev,s.node,t) if hasattr(m, 'travel') else m[prev][s.node]
            if t < s.early:
                if k:
                    wait += s.early - t
                t = s.early
            t += s.unload
            prev = s.node
        return wait

    def guard(seq: list[int]) -> Callable[[list[int]], bool] | None:
        """2-opt с окнами: ход — только при допустимом расписании с выезда в начале дня (рейс потом встаёт в
        промежуток машины со своим выездом) и если ожидание внутри рейса растёт не больше WAIT_MERGE_SLACK (короче
        по км, но с простоем у окна — не лучше); рейс и так недопустим — без проверки."""
        if not (timed(seq) and ok_at_start(seq)):
            return None
        limit = idle(seq) + WAIT_MERGE_SLACK
        return lambda s2: ok_at_start(s2) and idle(s2) <= limit

    def fits(a: list[int], b: list[int], reverse: bool = True) -> list[int] | None:
        """Слитый рейс a + b допустим: тоннаж (центр — свой предел) и окна; reverse — можно и задом наперёд (только у
        симметричных матриц: у направленных у развёрнутого рейса другие км)."""
        seq = a + b
        if is_central(seq) and math.fsum(vs[v].kg for v in seq) > cap_c + _EPS:
            return None
        eligible = _eligible(seq, vs, trucks)
        if not eligible or math.fsum(vs[v].kg for v in seq) > max(t.capacity_kg for t in eligible) * ratio + _EPS:
            return None
        if not timed(seq):
            return seq
        limit = idle(a) + idle(b) + WAIT_MERGE_SLACK
        ok = [s2 for s2 in ((seq, seq[::-1]) if reverse else (seq,)) if ok_at_start(s2) and idle(s2) <= limit]
        return min(ok, key=idle) if ok else None

    single_set = set(singles) | set(at.values())
    light = [v for v in range(len(vs)) if v not in single_set]
    routes = [_two_opt(r, vs, d, guard(r)) for r in _savings(light, vs, d, m, cap, window, fits, tn.reserve)]
    routes += [[v] for v in singles]

    def item(seq: list[int]) -> tuple:
        minutes, _ = _schedule(seq, vs, m, 0.0)
        kg = math.fsum(vs[v].kg for v in seq)
        deadline = min(vs[v].late for v in seq)
        release = max(0.0, max(vs[v].early for v in seq))
        # EDF; центровые — раньше; рейс, который раньше release не начать, — позже; дальше тяжёлые и длинные
        # первыми; ничьи — по точкам рейса
        scarcity = len(_eligible(seq, vs, trucks)) if access_constrained else len(trucks)
        return (deadline, not is_central(seq), (release, scarcity), -kg, -minutes, tuple(vs[v].node for v in seq), seq,
                _closed(seq, vs, d), minutes, kg)

    def shortest(seq: list[int]) -> tuple:
        """Рейс в порядке 2-opt или как есть — что быстрее по минутам (см. plan_trips)."""
        a, b = item(seq), item(_two_opt(seq, vs, d, guard(seq)))
        return b if b[8] <= a[8] + _EPS else a

    # день машины: занятые отрезки [выезд, возвращение]; закреплённые логистом рейсы — их отрезки (busy) или
    # одним куском с начала дня (used)
    slots: dict[str, list[tuple[float, float]]] = {}
    for t in trucks:
        if busy is not None:
            slots[t.car_code] = sorted((float(a), float(b)) for a, b in busy.get(t.car_code, ()))
            continue
        busy0 = float((used or {}).get(t.car_code, 0.0))
        slots[t.car_code] = [(0.0, busy0)] if busy0 > 0 else []
    held = {t.car_code: [max((b for _, b in slots[t.car_code]), default=0.0)] if t.car_code in fed else []
            for t in trucks}

    def pending(code: str, g0: float) -> bool:
        """Обед машины к началу промежутка g0 ещё впереди."""
        return lunch and not any(end <= g0 + _EPS for end in held[code])

    def run(seq: Sequence[int], code: str, g0: float, tail: bool) -> tuple[float, float, bool, bool]:
        """Отрезок рейса машины code с начала промежутка g0 (обед — по правилу, см. выше; tail — за промежутком в дне
        машины есть рейс): (ожидание до выезда, минуты от g0 до конца отрезка, окна соблюдены, обед в отрезке)."""
        minutes, ok = _schedule(seq, vs, m, g0, buffer=tn.buffer_c, pace=tn.pace_of(code),
                                yerevan=tn.yerevan_of(code))
        if not pending(code, g0) or g0 + minutes < tn.lunch_from - _EPS:
            return first_wait(seq, g0, code), minutes, ok, False
        dep, mins, ok, brk = _lunch_trip(seq, vs, m, g0, tn, True, True, code=code)
        rest = tn.lunch_minutes if tail and brk is None and dep + mins >= tn.lunch_from - _EPS else 0.0
        return dep - g0, dep + mins + rest - g0, ok, brk is not None or rest > 0

    def gaps(code: str) -> list[tuple[float, float]]:
        """Свободные промежутки машины по порядку; последний — до конца рабочего дня."""
        out, end = [], 0.0
        for a, b in slots[code]:
            if a > end + _EPS:
                out.append((end, a))
            end = max(end, b)
        out.append((end, max(end, window)))
        return out

    def place(seq: Sequence[int], code: str) -> tuple[float, float, float, bool] | None:
        """Самый ранний промежуток машины, где рейс укладывается с окнами: (начало промежутка, ожидание у первой
        точки — на столько позже выезд, минуты от начала промежутка до возвращения, обед в отрезке)."""
        gs = gaps(code)
        for n, (g0, g1) in enumerate(gs):
            if lunch:
                wait, minutes, ok, has = run(seq, code, g0, n + 1 < len(gs))
                if ok and g0 + minutes <= g1 + _EPS:
                    return g0, wait, minutes, has
                continue
            minutes, ok = _schedule(seq, vs, m, g0, buffer=tn.buffer_c, pace=tn.pace_of(code),
                                    yerevan=tn.yerevan_of(code))
            if ok and g0 + minutes <= g1 + _EPS:
                return g0, first_wait(seq, g0, code), minutes, False
        return None

    def at_end(seq: Sequence[int], code: str) -> tuple[float, float, float, bool]:
        """Рейс в конец дня машины (за рабочий день или переработку) — без проверки окон и конца дня."""
        g0 = last_end(code)
        if lunch:
            wait, minutes, _, has = run(seq, code, g0, False)
            return g0, wait, minutes, has
        return g0, first_wait(seq, g0, code), _schedule(seq, vs, m, g0, buffer=tn.buffer_c, pace=tn.pace_of(code),
                                                        yerevan=tn.yerevan_of(code))[0], False

    def last_end(code: str) -> float:
        return max((b for _, b in slots[code]), default=0.0)

    def misses(seq: Sequence[int], start: float, code: str) -> tuple[int, ...]:
        """Точки рейса, к которым машина code с выезда start приезжает позже окна (с обедом, если он впереди)."""
        arr: list[float] = []
        minutes, _ = _schedule(seq, vs, m, start, arr, buffer=tn.buffer_c, pace=tn.pace_of(code),
                               yerevan=tn.yerevan_of(code))
        if pending(code, start) and start + minutes >= tn.lunch_from - _EPS:
            arr = []
            _lunch_trip(seq, vs, m, start, tn, True, True, arr, code=code)
        return tuple(v for v, t in zip(seq, arr) if t > vs[v].late + _EPS)

    def plain_gap(seq: Sequence[int], kg: float, t: FleetTruck) -> float | None:
        """Начало первого промежутка, куда рейс встал бы без окон (тоннаж и время), или None."""
        if _load_limit(seq, vs, t, trucks, load_cap) < kg - _EPS:
            return None
        mu, mt = tn.pace_of(t.car_code)
        plain = (_closed(seq, vs, m) + math.fsum(vs[v].unload for v in seq) if (mu, mt) == NO_PACE
                 else _closed(seq, vs, m) * mt + math.fsum(vs[v].unload for v in seq) * mu)
        extra = _yerevan_extra(seq, vs, tn, t.car_code)   # надбавка в зоне Еревана (№68)
        if extra:
            plain += extra
        plain += tn.reserve(plain)                 # запас на рейс (№66): без него — + 0.0
        if hasattr(m, 'load'):
            plain += m.load(kg)

        def meal(g0: float) -> float:   # день машины с рейсом заходит в окно обеда, а обед впереди — с обедом
            return tn.lunch_minutes if pending(t.car_code, g0) and g0 + plain > tn.lunch_from + _EPS else 0.0
        return next((g0 for g0, g1 in gaps(t.car_code) if g0 + plain + meal(g0) <= g1 + _EPS), None)

    def head(seq: Sequence[int], allowed: Sequence[FleetTruck]) -> int:
        """Сколько первых точек рейса успевает хоть одна допустимая машина: тоннаж, окна, конец дня."""
        best = 0
        for t in allowed:
            k, kg = 0, 0.0
            while k < len(seq):
                kg += vs[seq[k]].kg
                if kg > _load_limit(seq[:k + 1], vs, t, trucks, load_cap) + _EPS or place(seq[:k + 1], t.car_code) is None:
                    break
                k += 1
            best = max(best, k)
        return best

    planned: list[tuple[float, int, Trip]] = []     # (выезд, номер назначения, рейс)

    def assign(truck: FleetTruck, seq: list[int], km: float, kg: float, spot: tuple[float, float, float, bool],
               extra: bool) -> None:
        g0, wait, minutes, has = spot
        cost = _sequence_cost(seq, vs, d, truck)
        depart = g0 + wait                         # не ждать у первой точки: машина выезжает позже
        slots[truck.car_code] = sorted(slots[truck.car_code] + [(depart, g0 + minutes)])
        if has:
            held[truck.car_code].append(g0 + minutes)
        planned.append((depart, len(planned), Trip(
            truck.car_code, len(seq), kg, math.fsum(vs[v].revenue for v in seq), km, minutes - wait,
            truck.capacity_kg, cost.liters, extra, tuple(origin[v] for v in seq), cost.wear_amd, cost.payload_tonne_km)))

    def free_at(a: float, b: float, code: str) -> bool:
        return any(g0 <= a + _EPS and b <= g1 + _EPS for g0, g1 in gaps(code))

    by_code = {t.car_code: t for t in trucks}
    for code, idx, start in sorted((f for f in (fixed or ()) if f[1] and f[0] in by_code), key=lambda f: f[2]):
        seq = [at[i] for i in idx]
        if any(not _vehicle_allowed(vs[i], by_code[code]) for i in seq):
            raise ValueError('Закреплённая машина не может обслуживать магазин')
        if lunch:
            wait, minutes, ok, has = run(seq, code, start, any(a > start + _EPS for a, _ in slots[code]))
            spot = (start, wait, minutes, has)
        else:
            minutes, ok = _schedule(seq, vs, m, start, buffer=tn.buffer_c, pace=tn.pace_of(code),
                                    yerevan=tn.yerevan_of(code))
            spot = (start, first_wait(seq, start, code), minutes, False)
        if not (ok and start + minutes <= window + _EPS and free_at(start + spot[1], start + minutes, code)):
            spot = place(seq, code)
        extra = spot is None
        if extra:   # не помещается — всё равно везёт его машина: в конец её дня (нарушение видно в плане)
            spot = at_end(seq, code)
        assign(by_code[code], seq, _closed(seq, vs, d), math.fsum(vs[v].kg for v in seq), spot, extra)

    queue = sorted(item(r) for r in routes)
    while queue:
        _, plain_order, _, _, _, _, seq, km, _, kg = queue.pop(0)
        allowed = _eligible(seq, vs, trucks)
        spots = {t.car_code: place(seq, t.car_code) for t in allowed
                 if _load_limit(seq, vs, t, trucks, load_cap) >= kg - _EPS}
        fit = [t for t in allowed if spots.get(t.car_code) is not None]
        extra = not fit
        if extra:
            spare = [t for t in allowed if _load_limit(seq, vs, t, trucks, load_cap) < kg - _EPS
                     and gaps(t.car_code)[-1][0] < window - _EPS]
            if spare and len(seq) > 1:
                pieces = _cut(seq, vs, max(_load_limit(seq, vs, t, trucks, load_cap) for t in spare))
                if len(pieces) > 1:
                    queue = sorted(queue + [item(_two_opt(p, vs, d, guard(p))) for p in pieces])
                    continue
            if not overflow:
                free = [(g0, t) for t in allowed if (g0 := plain_gap(seq, kg, t)) is not None]
                if len(seq) == 1:
                    why[origin[seq[0]]] = 'window' if free else 'time'
                    continue
                bad = min((misses(seq, g0, t.car_code) for g0, t in free), key=lambda b: (len(b), b), default=())
                if bad:
                    # без окон машина рейс взяла бы — мешают окна: такие точки — одиночными рейсами
                    rest = [v for v in seq if v not in bad]
                    queue = sorted(queue + [shortest([v]) for v in bad] + ([shortest(rest)] if rest else []))
                    continue
                fwd, back = head(seq, allowed), head(seq[::-1], allowed)
                s2, cut = (seq, fwd) if fwd >= back else (seq[::-1], back)
                cut = min(max(cut, 1), len(seq) - 1)
                queue = sorted(queue + [shortest(s2[:cut]), shortest(s2[cut:])])
                continue
            # рейс за пределами дня: допустимая машина, что поднимет груз и освободится раньше всех, — в конец дня
            fit = [t for t in allowed if t.capacity_kg >= kg - _EPS]
            truck = min(fit, key=lambda t: (last_end(t.car_code), t.l100, t.car_code))
            spots[truck.car_code] = at_end(seq, truck.car_code)
        else:
            # машину с правом въезда бережём для центровых рейсов, пока они есть в очереди
            spare_center = plain_order and any(not q[1] for q in queue)
            if earliest:   # приоритет малых в Ереване (№68) — как в plan_trips (_earliest_key)
                def early(t: FleetTruck) -> tuple:
                    end, bias = spots[t.car_code][0] + spots[t.car_code][2], _yerevan_bias(seq, vs, t, tn)
                    return (spare_center and t.center_ok, _earliest_key(bias, end, tn), end, bias, t.l100, t.car_code)
                truck = min(fit, key=early)
            else:
                truck = min(fit, key=lambda t: (spare_center and t.center_ok,
                                               _sequence_cost(seq, vs, d, t).total_amd(tn.fuel_price)
                                               + _yerevan_bias(seq, vs, t, tn), -t.capacity_kg, t.car_code))
        old_minutes = spots[truck.car_code][2]
        def fits_order(candidate: list[int]) -> bool:
            spot = place(candidate, truck.car_code)
            return spot is not None and spot[2] <= old_minutes + _EPS
        better_seq = _cost_order(seq, vs, d, truck, tn, fits_order)
        if better_seq != seq:
            seq = better_seq
            km = _closed(seq, vs, d)
            spots[truck.car_code] = place(seq, truck.car_code)
        assign(truck, seq, km, kg, spots[truck.car_code], extra)
    planned.sort(key=lambda x: (x[0], x[1]))
    if departs is not None:
        departs.extend(dep for dep, *_ in planned)
    return [t for *_, t in planned]


BALANCE_SLACK = 0.03    # ровная загрузка (ответ №44): км и литры изменённых рейсов растут не больше, доля от исходных
BALANCE_STEP = 0.01     # ход выравнивания: самый загруженный из двух рейсов легчает хотя бы на столько тоннажа, доля
BALANCE_FROM = 0.90     # порог прежней эвристики, когда нагрузочные нормы ещё не заданы


def _balance(trips: list[Trip], stops: Sequence[_Stop], d: Matrix, m: Matrix, trucks: Sequence[FleetTruck],
             tn: TruckNorms, used: Mapping[str, float] | None,
             fixed: Sequence[tuple[str, Sequence[int], float]] | None, fed: Collection[str] = ()) -> list[Trip]:
    """Ровная загрузка рейсов плана развоза (ответ владельца №44: 5 т + 3 т → 4 т + 4 т — машины не ломаются от
    перегруза). Загрузка рейса — кг / тоннаж его машины. Из самого загруженного рейса (тяжелее BALANCE_FROM) магазин
    переходит в менее загруженный или меняется с магазином оттуда, если самый загруженный из двух рейсов легчает
    хотя бы на BALANCE_STEP; из нескольких ходов — самый ровный (по целым процентам), потом самый короткий по км.
    Порог BALANCE_FROM — по живой проверке 01.09–02.10.2026: рейсов ≥95% 69 → 9 за +1,4% км (без порога 69 → 8
    за +2,2%).
    Ограничения — как у сборки: тоннаж машины, малый центр, окна приёма и конец рабочего дня (рейсы машины подряд
    с used, как их повезёт dispatch._timeline); ожидание у окон внутри рейса растёт не больше WAIT_MERGE_SLACK, а
    закреплённый рейс не выезжает позже, чем без выравнивания; км и литры изменённых рейсов — не больше чем на
    BALANCE_SLACK сверх исходных; магазин зоны Еревана с малой машины на большую не переходит (№68, приоритет малых).
    Магазин встаёт туда, где объезд удлиняется меньше всего, затем 2-opt (окон не нарушает).
    Не трогаются: закреплённые (fixed), рейсы с куском тяжёлого заказа, рейсы за пределами дня и все рейсы машины,
    чей день и так не укладывается. Машина рейса и порядок рейсов не меняются. Каждый ход строго уменьшает
    вектор загрузок рейсов, отсортированный по убыванию (лексикографически), — цикл конечен.
    При заданных нагрузочных нормах рассматриваются и более лёгкие рейсы; ход принимается только при
    снижении суммы дизеля и износа по остаточному грузу. Это оценка стоимости, не прогноз поломок.
    Обед (№61) — в дне машины по правилу _lunch_trip (fed — машины, чей обед уже в used)."""
    if len(trips) < 2:
        return trips
    window = tn.work_minutes
    by_code = {t.car_code: t for t in trucks}
    share = Counter(i for t in trips for i in t.items)
    vs = [s if share[i] <= 1 else replace(s, kg=s.kg / share[i], revenue=s.revenue / share[i],
                                          unload=_split_unload(s, share[i], tn))
          for i, s in enumerate(stops)]
    seqs = [list(t.items) for t in trips]
    kg = [math.fsum(vs[v].kg for v in s) for s in seqs]
    cap = [t.capacity_kg for t in trips]
    l100 = [by_code[t.truck].l100 for t in trips]
    load_aware = any(configured(t) for t in trucks)
    km0 = [_closed(s, vs, d) for s in seqs]
    km = list(km0)
    order: dict[str, list[int]] = {}
    for k, t in enumerate(trips):
        order.setdefault(t.truck, []).append(k)
    start0 = {code: float((used or {}).get(code, 0.0)) for code in order}

    def run(code: str, alt: Mapping[int, list[int]]) -> list[tuple[float, float, float]] | None:
        """Рейсы машины подряд (dispatch._timeline) с составами alt вместо текущих: (выезд, минуты от выезда,
        ожидание у окон внутри рейса) каждого или None — нарушено окно приёма или конец рабочего дня."""
        t, out = start0[code], []
        eaten = code in fed
        pace, yer = tn.pace_of(code), tn.yerevan_of(code)
        plain = tn.buffer_c <= 0 and pace == NO_PACE and not yer   # без запаса, темпа и надбавки — прежний расчёт до байта
        for n, k in enumerate(order[code]):
            seq = alt.get(k, seqs[k])
            parts: dict[str, Any] = {}
            if tn.lunch_minutes > 0:
                depart, minutes, ok, brk = _lunch_trip(seq, vs, m, t, tn, not eaten, n + 1 < len(order[code]),
                                                       parts=parts, code=code)
                eaten = eaten or brk is not None
            else:
                depart = _departure(seq, vs, m, t, pace)
                minutes, ok = _schedule(seq, vs, m, depart, parts=parts, buffer=tn.buffer_c, pace=pace, yerevan=yer)
                brk = None
            if not ok:
                return None
            t = depart + minutes
            wait = (_waiting(seq, vs, m, depart, minutes) if brk is None and plain
                    else math.fsum(w for _, w, _ in parts['legs']))   # ожидание окон — без обеда и запаса
            out.append((depart, minutes, wait))
        return out if t <= window + _EPS else None

    pinned = {(code, tuple(idx)) for code, idx, _ in (fixed or ())}
    pins = {k for k, t in enumerate(trips) if (t.truck, t.items) in pinned}
    base = {code: run(code, {}) for code in order}
    was = {k: x for code, r in base.items() if r is not None for k, x in zip(order[code], r)}
    free = [k for k, t in enumerate(trips)
            if not (t.extra or base[t.truck] is None or k in pins or any(share[i] > 1 for i in t.items))]

    def day(code: str, alt: Mapping[int, list[int]]) -> bool:
        """День машины с составами alt допустим: окна и конец дня соблюдаются, ожидание внутри рейса выросло не больше
        WAIT_MERGE_SLACK (у сборки — так же: поздним выездом его не убрать), закреплённый рейс выезжает не позже."""
        r = run(code, alt)
        return r is not None and all(wait <= was[k][2] + WAIT_MERGE_SLACK + _EPS
                                     and (k not in pins or dep <= was[k][0] + _EPS)
                                     for k, (dep, _, wait) in zip(order[code], r))
    changed: set[int] = set()

    def within(a: int, b: int, new_a: list[int], new_b: list[int]) -> bool:
        if any(not _vehicle_allowed(vs[i], by_code[trips[k].truck]) for k, seq in ((a, new_a), (b, new_b)) for i in seq):
            return False
        ch = changed | {a, b}
        ka, kb = _closed(new_a, vs, d), _closed(new_b, vs, d)
        new = {**{k: km[k] for k in ch}, a: ka, b: kb}
        distance_ok = (math.fsum(new.values()) <= (1 + BALANCE_SLACK) * math.fsum(km0[k] for k in ch) + _EPS
                and math.fsum(new[k] * l100[k] for k in ch)
                <= (1 + BALANCE_SLACK) * math.fsum(km0[k] * l100[k] for k in ch) + _EPS)
        if not distance_ok or not load_aware:
            return distance_ok
        old_cost = math.fsum(_sequence_cost(seqs[k], vs, d, by_code[trips[k].truck]).total_amd(tn.fuel_price)
                             for k in (a, b))
        new_cost = math.fsum(_sequence_cost(s, vs, d, by_code[trips[k].truck]).total_amd(tn.fuel_price)
                             for k, s in ((a, new_a), (b, new_b)))
        return new_cost < old_cost - _EPS

    def insert(seq: list[int], v: int) -> list[int]:
        """Вставить магазин туда, где объезд удлиняется меньше всего."""
        nodes = [0, *(vs[x].node for x in seq), 0]
        p = vs[v].node
        at = min(range(len(nodes) - 1),
                 key=lambda i: (d[nodes[i]][p] + d[p][nodes[i + 1]] - d[nodes[i]][nodes[i + 1]], i))
        return seq[:at] + [v] + seq[at:]

    def moves(a: int) -> list[tuple]:
        """Ходы из рейса a: (ключ, b, новый состав a, новый состав b) — переход магазина v и обмен v ↔ u."""
        fa, out = kg[a] / cap[a], []
        if fa <= (0.0 if load_aware else BALANCE_FROM) + _EPS:
            return out
        ta = by_code[trips[a].truck]
        for b in free:
            if b == a or kg[b] / cap[b] > fa - BALANCE_STEP + _EPS:
                continue
            tb = by_code[trips[b].truck]
            for v in seqs[a]:
                if vs[v].center and not tb.center_ok:
                    continue
                if vs[v].yerevan and tb.big and not ta.big:   # №68: точку Еревана с малой машины на большую — нет
                    continue
                rest_a = [x for x in seqs[a] if x != v]
                for u in [None, *seqs[b]]:
                    if u is None and not rest_a:
                        continue
                    if u is not None and vs[u].center and not ta.center_ok:
                        continue
                    if u is not None and vs[u].yerevan and ta.big and not tb.big:
                        continue
                    w = vs[v].kg - (vs[u].kg if u is not None else 0.0)
                    if kg[b] + w > cap[b] + _EPS:
                        continue
                    top = max((kg[a] - w) / cap[a], (kg[b] + w) / cap[b])
                    if top > fa - BALANCE_STEP + _EPS:
                        continue
                    new_a = rest_a if u is None else insert(rest_a, u)
                    new_b = insert([x for x in seqs[b] if x != u], v)
                    ka, kb = _closed(new_a, vs, d), _closed(new_b, vs, d)
                    if within(a, b, new_a, new_b):
                        cost_key = math.fsum(_sequence_cost(s, vs, d, by_code[trips[k].truck]).total_amd(tn.fuel_price)
                                             for k, s in ((a, new_a), (b, new_b))) if load_aware else round(top * 100)
                        out.append(((cost_key, ka + kb - km[a] - km[b], b, v, -1 if u is None else u),
                                    b, new_a, new_b))
        return sorted(out, key=lambda x: x[0])

    def apply(a: int, b: int, new_a: list[int], new_b: list[int]) -> bool:
        alt = {a: new_a, b: new_b}
        if not all(day(code, alt) for code in {trips[a].truck, trips[b].truck}):
            return False
        for k in (a, b):     # 2-opt: км не растут, день машины остаётся допустимым
            code = trips[k].truck
            allowed = lambda s2, k=k, code=code: day(code, {**alt, k: s2})  # noqa: E731
            alt[k] = (_cost_order(alt[k], vs, d, by_code[code], tn, allowed) if load_aware
                      else _two_opt(alt[k], vs, d, allowed))
        if not within(a, b, alt[a], alt[b]):
            return False
        for k in (a, b):
            seqs[k] = alt[k]
            kg[k] = math.fsum(vs[v].kg for v in seqs[k])
            km[k] = _closed(seqs[k], vs, d)
        changed.update((a, b))
        return True

    for _ in range(10 * len(stops)):     # предохранитель; цикл и так конечен (см. выше)
        if not any(apply(a, b, new_a, new_b)
                   for a in sorted(free, key=lambda k: (-kg[k] / cap[k], k))
                   for _, b, new_a, new_b in moves(a)):
            break
    if not changed:
        return trips
    # минуты — по дню машины после выравнивания у всех её рейсов (выезды соседних рейсов могли сдвинуться)
    moved = {trips[k].truck for k in changed}
    minutes = {k: x[1] for code in moved for k, x in zip(order[code], run(code, {}) or ())}
    costs = {k: _sequence_cost(seqs[k], vs, d, by_code[t.truck]) for k, t in enumerate(trips) if t.truck in moved}
    return [t if t.truck not in moved else replace(
        t, stops=len(seqs[k]), kg=kg[k], revenue=math.fsum(vs[v].revenue for v in seqs[k]), km=km[k],
        minutes=minutes[k], liters=costs[k].liters, wear_amd=costs[k].wear_amd,
        payload_tonne_km=costs[k].payload_tonne_km, items=tuple(seqs[k]))
        for k, t in enumerate(trips)]


# --- Решатель PyVRP поверх сборки (ответ владельца №45) ---

LOAD_CAP = 0.90     # предел загрузки рейса (ответ №45): тяжелее — только заказ, который иначе не увезти


def _shared(trips: Sequence[Trip], stops: Sequence[_Stop], tn: TruckNorms) -> list[_Stop]:
    """Заказы с грузом одной поездки: заказ в k рейсах (тяжёлый) — в каждом 1/k его кг (как dispatch._shares)."""
    share = Counter(i for t in trips for i in t.items)
    return [s if share[i] <= 1 else replace(s, kg=s.kg / share[i], revenue=s.revenue / share[i],
                                            unload=_split_unload(s, share[i], tn))
            for i, s in enumerate(stops)]


def _days(trips: Sequence[Trip], vs: Sequence[_Stop], m: Matrix, start0: Mapping[str, float],
          tn: TruckNorms | None = None, fed: Collection[str] = ()
          ) -> dict[str, list[tuple[int, float, float, float]] | None]:
    """Рейсы машин подряд, как их повезёт dispatch._timeline (выезд — когда машина свободна, но не раньше, чем нужно к
    окну первой точки; обед — по правилу _lunch_trip при tn с обедом, fed — машины, чей обед уже в start0): машина →
    [(номер рейса в trips, выезд, минуты от выезда, ожидание у окон внутри рейса)]; None — опоздание в окно приёма."""
    out: dict[str, list[tuple[int, float, float, float]] | None] = {}
    free = dict(start0)
    lunch = tn is not None and tn.lunch_minutes > 0
    last = {t.truck: k for k, t in enumerate(trips)}
    eaten = set(fed)
    for k, t in enumerate(trips):
        if t.truck in out and out[t.truck] is None:
            continue
        seq = list(t.items)
        arrivals = [] if hasattr(m, 'travel') else None
        brk = None
        parts: dict[str, Any] = {}
        pace = tn.pace_of(t.truck) if tn is not None else NO_PACE
        yer = tn.yerevan_of(t.truck) if tn is not None else 0.0
        if lunch:
            dep, minutes, ok, brk = _lunch_trip(seq, vs, m, free.get(t.truck, 0.0), tn, t.truck not in eaten,
                                                k != last[t.truck], arrivals, parts, t.truck)
            if brk is not None:
                eaten.add(t.truck)
        else:
            dep = _departure(seq, vs, m, free.get(t.truck, 0.0), pace)
            minutes, ok = _schedule(seq, vs, m, dep, arrivals, parts, buffer=tn.buffer_c if tn is not None else 0.0,
                                    pace=pace, yerevan=yer)
        if not ok:
            out[t.truck] = None
            continue
        # запас и темп (№66), надбавка в зоне Еревана (№68): ожидание окон — по слагаемым
        if (tn is not None and tn.buffer_c > 0) or pace != NO_PACE or yer:
            out.setdefault(t.truck, []).append((k, dep, minutes, math.fsum(w for _, w, _ in parts['legs'])))
            free[t.truck] = dep + minutes
            continue
        wait = minutes - _closed(seq, vs, m) - math.fsum(vs[v].unload for v in seq)
        if brk is not None and brk.stop is not None:
            wait -= brk.added                                 # ожидание окон — без обеда
        if arrivals is not None:
            clock, prev, wait = dep + m.load(math.fsum(vs[v].kg for v in seq)), 0, 0.
            for j, (v, arrival) in enumerate(zip(seq, arrivals)):
                drive = m.travel(prev, vs[v].node, clock)
                if brk is not None and brk.road and brk.stop == j:
                    clock += brk.added
                wait += max(0., arrival-clock-drive)
                clock, prev = arrival + vs[v].unload, vs[v].node
                if brk is not None and not brk.road and brk.stop == j:
                    clock += brk.added
        out.setdefault(t.truck, []).append((k, dep, minutes, wait))
        free[t.truck] = dep + minutes
    return out


def _over_cap(trips: Sequence[Trip]) -> int:
    """Рейсов тяжелее предела загрузки LOAD_CAP."""
    return sum(1 for t in trips if t.kg > LOAD_CAP * t.capacity_kg + 1e-6)


def _polish_orders(trips: list[Trip], stops: Sequence[_Stop], d: Matrix, m: Matrix,
                   trucks: Sequence[FleetTruck], tn: TruckNorms, used: Mapping[str, float] | None,
                   fixed: Sequence[tuple[str, Sequence[int], float]] | None, fed: Collection[str] = ()) -> list[Trip]:
    """Доводка порядка после сборки/решателя по остаточному грузу; состав и машина рейса те же.

    Каждый ход проверяется по всему дню машины, включая следующие окна, смену и закрепления.
    Закреплённый порядок и уже недопустимый день не меняются; доли тяжёлого заказа считаются один раз.
    """
    if not trips or not any(configured(t) for t in trucks):
        return trips
    by_code = {t.car_code: t for t in trucks}
    vs = _shared(trips, stops, tn)
    start0 = {code: float((used or {}).get(code, 0.0)) for code in by_code}
    base = _days(trips, vs, m, start0, tn, fed)
    locked = {code for code, rows in base.items()
              if rows is None or (rows and rows[-1][1] + rows[-1][2] > tn.work_minutes + _EPS)}
    fixed_keys = {(code, tuple(idx)) for code, idx, _ in (fixed or ())}
    pins = {k for k, trip in enumerate(trips) if (trip.truck, trip.items) in fixed_keys}
    departures = {k: dep for rows in base.values() if rows for k, dep, _, _ in rows}
    order: dict[str, list[int]] = {}
    for k, trip in enumerate(trips):
        order.setdefault(trip.truck, []).append(k)
    out = list(trips)
    changed: set[str] = set()
    for k, trip in enumerate(out):
        code = trip.truck
        truck = by_code[code]
        if trip.extra or k in pins or code in locked or not configured(truck) or len(trip.items) < 2:
            continue
        indices = order[code]

        def allowed(candidate: list[int]) -> bool:
            day_trips = [replace(out[i], items=tuple(candidate)) if i == k else out[i] for i in indices]
            rows = _days(day_trips, vs, m, {code: start0[code]}, tn, fed).get(code)
            if rows is None or rows[-1][1] + rows[-1][2] > tn.work_minutes + _EPS:
                return False
            if any(i in pins and dep > departures[i] + _EPS
                   for i, (_, dep, _, _) in zip(indices, rows)):
                return False
            return (math.fsum(wait for _, _, _, wait in rows)
                    <= math.fsum(wait for _, _, _, wait in base[code]) + WAIT_MERGE_SLACK + _EPS)

        seq = _cost_order(list(trip.items), vs, d, truck, tn, allowed)
        if tuple(seq) != trip.items:
            cost = _sequence_cost(seq, vs, d, truck)
            out[k] = replace(trip, items=tuple(seq), km=_closed(seq, vs, d), liters=cost.liters,
                             wear_amd=cost.wear_amd, payload_tonne_km=cost.payload_tonne_km)
            changed.add(code)
    if not changed:
        return trips
    # Новый выезд может изменить время следующих рейсов при почасовой модели скорости.
    days = _days(out, vs, m, start0, tn, fed)
    for code in changed:
        for k, _, minutes, _ in days[code]:
            out[k] = replace(out[k], minutes=minutes)
    return out


def _better(new: Sequence[Trip], old: Sequence[Trip], tn: TruckNorms | None = None,
            bias: Callable[[Sequence[Trip]], float] | None = None) -> bool:
    """Рейсы решателя лучше: заказов в рейсах больше; при тех же заказах — литров не больше или рейсов тяжелее
    предела загрузки меньше (ответ №45: предел важнее экономии). bias — плата за точки Еревана на больших машинах
    (№68, драм) — к литрам."""
    a, b = len({i for t in new for i in t.items}), len({i for t in old for i in t.items})
    if a != b:
        return a > b
    price = tn.fuel_price if tn is not None else 500.0
    extra = (bias(new), bias(old)) if bias is not None else (0.0, 0.0)
    return (math.fsum(t.liters * price + t.wear_amd for t in new) + extra[0]
            <= math.fsum(t.liters * price + t.wear_amd for t in old) + extra[1] + 1e-6
            or _over_cap(new) < _over_cap(old))


def _solver(trips: list[Trip], stops: Sequence[_Stop], d: Matrix, m: Matrix, trucks: Sequence[FleetTruck],
            tn: TruckNorms, used: Mapping[str, float] | None,
            fixed: Sequence[tuple[str, Sequence[int], float]] | None, fed: Collection[str] = (),
            iterations: int = vrp.ITERATIONS) -> list[Trip] | None:
    """Рейсы дня решателем PyVRP (vrp.solve: цель — литры, рейс не тяжелее LOAD_CAP; если так всё не помещается — без
    предела) со стартом от сборки trips. None — решателя нет или его рейсы не проходят проверку «Развоза»: каждый
    заказ сборки — в рейсах целиком (те же поездки), повторов нет, тоннаж, центр, окна и конец рабочего дня (рейсы
    машины подряд с used, как dispatch._timeline), ожидание у окон за день выросло не больше WAIT_MERGE_SLACK,
    закреплённые рейсы выезжают не позже. Как были остаются: закреплённые (fixed), рейсы за пределами дня и все рейсы
    машины, чей день в сборке и так не укладывается; машина свободна только в промежутках между оставленными рейсами.
    Заказы вне рейсов сборки решатель может поставить, если найдёт место.
    Обед (№61): у PyVRP пауз нет — у машины, чей обед не в занятом времени (fed), промежутки, кончающиеся позже начала
    окна обеда, короче на обед (машину, что кончает раньше, это не стесняет), а крайний срок окон приёма позже начала
    окна обеда — раньше на обед (не раньше начала окна): обед сдвигает всё после него не больше чем на свою длину.
    Решает точный расчёт с обедом (_days) — та же проверка, что выше.
    Запас на рейс и темп машин (№66): запас c·√D PyVRP получает линейной верхней оценкой — касательной в типичном рейсе
    сборки D0 (медиана минут её рейсов с темпом машины и ожиданием окон, без загрузки, обеда и запаса): c·√D ≤ c·√D0 / 2 +
    c / (2√D0) · D — минуты езды и разгрузки × (1 + c / (2√D0)), c·√D0 / 2 — на каждый рейс (vrp.solve); темп машины —
    её профилем. Это верхняя оценка только по езде и разгрузке, не строгая: ожидание окон и обед PyVRP не знает — их
    покрывают точная шкала и повтор: решение не прошло проверку по времени (окно или конец дня) — ещё одна попытка с
    запасом × RESERVE_RETRY (причина — в журнал INFO); решает та же точная проверка (_days)."""
    if not vrp.available() or not trips:
        return None
    window = tn.work_minutes
    by_code = {t.car_code: t for t in trucks}
    vs = _shared(trips, stops, tn)
    start0 = {c: float((used or {}).get(c, 0.0)) for c in by_code}
    base = _days(trips, vs, m, start0, tn, fed)
    locked = {c for c, r in base.items() if r is None or (r and r[-1][1] + r[-1][2] > window + _EPS)}
    pinned = {(code, tuple(idx)) for code, idx, _ in (fixed or ())}
    pins = {k for k, t in enumerate(trips) if (t.truck, t.items) in pinned}
    keep = {k for k, t in enumerate(trips) if t.extra or k in pins or t.truck in locked}
    when = {k: (dep, dep + mins) for r in base.values() if r for k, dep, mins, _ in r}
    wait0 = math.fsum(w for c, r in base.items() if r and c not in locked for *_, w in r)
    meal = tn.lunch_minutes if tn.lunch_minutes > 0 else 0.0
    hungry = {c for c in by_code if meal and c not in fed}    # конец промежутка режет только того, кто до него работает
    shifts: list[vrp.Shift] = []
    until: list[float] = []      # конец промежутка без поправки на обед: по нему рейсы сборки — в стартовом решении
    for c in sorted(by_code):
        if c in locked:
            continue
        t = start0[c]
        for a, b in sorted(when[k] for k in keep if trips[k].truck == c) + [(window, window)]:
            if a > t + _EPS:
                end = a - meal if c in hungry and a > tn.lunch_from else a
                if end > t + _EPS:
                    shifts.append(vrp.Shift(c, t, end))
                    until.append(a)
            t = max(t, b)

    def bound(x: float) -> float | None:
        return None if math.isinf(x) else x

    def due(x: float) -> float | None:   # крайний срок окна приёма для PyVRP: позже начала окна обеда — раньше на обед
        return bound(max(tn.lunch_from, x - meal) if hungry and x > tn.lunch_from else x)

    pieces: list[vrp.Piece] = []
    origin: list[int] = []
    start: dict[int, list[list[int]]] = {}
    for k, t in enumerate(trips):
        if k in keep:
            continue
        seq = []
        for i in t.items:
            s = vs[i]
            seq.append(len(pieces))
            pieces.append(vrp.Piece(s.node, s.kg, s.unload, bound(s.early), due(s.late), s.center, True, s.allowed_trucks,
                                    s.yerevan))
            origin.append(i)
        dep, end = when[k]
        at = next((n for n, s in enumerate(shifts) if s.truck == t.truck and s.start - _EPS <= dep and end <= until[n] + _EPS),
                  None)
        if at is not None:
            start.setdefault(at, []).append(seq)
    placed = Counter(i for t in trips for i in t.items)
    free_trucks = [by_code[s.truck] for s in shifts]
    for i, s in enumerate(stops):
        if i not in placed and any(v.capacity_kg >= s.kg - _EPS for v in _eligible([i], stops, free_trucks)):
            pieces.append(vrp.Piece(s.node, s.kg, s.unload, bound(s.early), due(s.late), s.center, False, s.allowed_trucks,
                                    s.yerevan))
            origin.append(i)
    # большая машина в Ереване (№68): надбавка к разгрузке и плата tn.yerevan_km км — профилем машины
    yer = any(p.yerevan for p in pieces)
    vehicles = [vrp.Vehicle(t.car_code, t.capacity_kg, t.l100, t.center_ok, t.wear_amd_per_km, tn.fuel_price,
                            tn.pace_of(t.car_code), tn.yerevan_of(t.car_code) if yer else 0.0,
                            round(tn.yerevan_km * 1000) if yer and t.big else 0) for t in trucks]
    begin = sorted(start.items())
    loading: dict[str, float] = ({'load_fixed_min': tn.warehouse_load_fixed_min,
                                  'load_tonne_min': tn.warehouse_load_min_per_tonne} if tn.load(1000) > 0 else {})
    loading['iterations'] = iterations
    tries: list[dict[str, float]] = [loading]
    if tn.buffer_c > 0:   # запас на рейс (№66): касательная к c·√D в типичном рейсе сборки (см. выше)
        plain = []
        for k, t in enumerate(trips):
            parts: dict[str, Any] = {}
            mins, _ = _schedule(list(t.items), vs, m, when[k][0] if k in when else 0.0, parts=parts,
                                pace=tn.pace_of(t.truck), yerevan=tn.yerevan_of(t.truck))
            plain.append(mins - parts['loading'])
        root = math.sqrt(max(median(plain), 1.0))
        tries = [{**loading, 'trip_reserve_min': f * tn.buffer_c * root / 2, 'reserve_slope': f * tn.buffer_c / (2 * root)}
                 for f in (1.0, RESERVE_RETRY)]
    got: list[Trip] | None = None
    why: str | None = None
    for kw in tries:
        got, why = _solver_try(trips, stops, d, m, trucks, tn, fed, pieces, origin, vehicles, shifts, begin, kw, keep,
                               when, placed, start0, locked, pinned, pins, wait0)
        if got is not None or why != 'time':
            break
        if kw is not tries[-1]:
            logger.info('[Routes] PyVRP: с запасом на рейс не прошло проверку (%s) — ещё попытка с запасом × %s',
                        why, RESERVE_RETRY)
    if got is None and why in ('load', 'time', 'wait'):
        logger.warning('[Routes] PyVRP: рейсы не прошли проверку «Развоза» — рейсы своим расчётом')
    return got


RESERVE_RETRY = 1.5   # решение PyVRP с запасом на рейс не уложилось по времени — ещё попытка с запасом × 1,5 (№66)


def _solver_try(trips: list[Trip], stops: Sequence[_Stop], d: Matrix, m: Matrix, trucks: Sequence[FleetTruck],
                tn: TruckNorms, fed: Collection[str], pieces: list[vrp.Piece], origin: list[int],
                vehicles: list[vrp.Vehicle], shifts: list[vrp.Shift], begin: list[tuple[int, list[list[int]]]],
                kw: Mapping[str, float], keep: set[int], when: Mapping[int, tuple[float, float]], placed: Counter,
                start0: Mapping[str, float], locked: set[str], pinned: set[tuple[str, tuple[int, ...]]], pins: set[int],
                wait0: float) -> tuple[list[Trip] | None, str | None]:
    """Одна попытка _solver: PyVRP с параметрами kw (загрузка, запас на рейс) и проверка «Развоза». (рейсы, None) или
    (None, причина): None — решения нет; 'orders' — заказы не сходятся со сборкой; 'vehicle' — допуск машин; 'load' —
    тоннаж, центр, предел загрузки; 'time' — окно приёма или конец дня по точной шкале; 'wait' — ожидание у окон выросло
    больше WAIT_MERGE_SLACK или закреплённый рейс выезжает позже."""
    window = tn.work_minutes
    by_code = {t.car_code: t for t in trucks}
    res = vrp.solve(pieces, d, m, vehicles, shifts, begin, LOAD_CAP, **kw)
    if res is None:
        res = vrp.solve(pieces, d, m, vehicles, shifts, begin, None, **kw)
    if res is None:
        return None, None
    rows: list[tuple[float, int, int, tuple[int, ...], str, Trip | None]] = \
        [(when[k][0], 0, k, t.items, t.truck, t) for k, t in enumerate(trips) if k in keep]
    for at, ts in res:
        for n, seq in enumerate(ts):
            rows.append((shifts[at].start, 1, n, tuple(origin[p] for p in seq), shifts[at].truck, None))
    rows.sort(key=lambda r: r[:3])
    share = Counter(i for r in rows for i in r[3])
    vs2 = _shared([Trip(r[4], 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False, r[3]) for r in rows], stops, tn)
    if any(share[i] != n for i, n in placed.items()) or any(n > 1 for i, n in share.items() if i not in placed) \
            or any(len(set(r[3])) != len(r[3]) for r in rows):
        logger.warning('[Routes] PyVRP: заказы не сходятся со сборкой — рейсы своим расчётом')
        return None, 'orders'
    out: list[Trip] = []
    for _, _, _, items, code, old in rows:
        if old is not None:
            out.append(old)
            continue
        truck = by_code[code]
        seq = list(items)
        km = _closed(seq, vs2, d)
        cost = _sequence_cost(seq, vs2, d, truck)
        out.append(Trip(code, len(seq), math.fsum(vs2[v].kg for v in seq), math.fsum(vs2[v].revenue for v in seq), km,
                        0.0, truck.capacity_kg, cost.liters, False, items, cost.wear_amd, cost.payload_tonne_km))
    days = _days(out, vs2, m, start0, tn, fed)
    if any(not _vehicle_allowed(vs2[i], by_code[t.truck]) for t in out for i in t.items):
        logger.warning('[Routes] PyVRP: нарушены ограничения машин магазинов — рейсы своим расчётом')
        return None, 'vehicle'
    ok = all(t.kg <= by_code[t.truck].capacity_kg + 1e-6 and (by_code[t.truck].center_ok or not any(vs2[v].center for v in t.items))
             for t in out if t.truck not in locked)
    ok = ok and all(t.kg <= _load_limit(t.items, vs2, by_code[t.truck], trucks, LOAD_CAP) + 1e-6
                    for t in out if t.truck not in locked and (t.truck, t.items) not in pinned)
    if not ok:
        return None, 'load'
    if not all(r is not None and (not r or r[-1][1] + r[-1][2] <= window + _EPS)
               for c, r in days.items() if c not in locked):
        return None, 'time'
    dep = {k: x for r in days.values() if r for k, x, *_ in r}
    idx = {id(t): k for k, t in enumerate(out)}
    if not (all(dep[idx[id(trips[k])]] <= when[k][0] + _EPS for k in pins) and
            math.fsum(w for c, r in days.items() if r and c not in locked for *_, w in r) <= wait0 + WAIT_MERGE_SLACK + _EPS):
        return None, 'wait'
    minutes = {k: mins for r in days.values() if r for k, _, mins, _ in r}
    return [t if t.extra or k not in minutes else replace(t, minutes=minutes[k]) for k, t in enumerate(out)], None


# --- День с известными заказами (план развоза, dispatch.py) ---

Window = tuple[float, float]   # окно приёма: прибыть не раньше и не позже, минуты от начала дня машины


def route_day(points: Sequence[Point], kgs: Sequence[float], revenues: Sequence[float], depot: Point,
              trucks: Sequence[FleetTruck], norms: Norms, tn: TruckNorms,
              used: Mapping[str, float] | None = None, overflow: bool = True,
              earliest: bool = False, windows: Sequence[Window] | None = None,
              center: Sequence[bool] | None = None, reasons: dict[int, str] | None = None,
              busy: Mapping[str, Sequence[tuple[float, float]]] | None = None,
              departs: list[float] | None = None,
              fixed: Sequence[tuple[str, Sequence[int], float]] | None = None,
              balance: bool = False, solver: bool = False, load_cap: float | None = None,
              allowed_trucks: Sequence[Collection[str] | None] | None = None, fed: Collection[str] = (),
              iterations: int = vrp.ITERATIONS) -> list[Trip]:
    """Рейсы дня по известным заказам — тот же расчёт, что у пробы Монте-Карло (plan_trips):
    заказ i — точка points[i], kgs[i] кг; Trip.items — номера заказов по порядку объезда.
    Тяжелее самой большой машины — несколько поездок к одному заказу поровну. overflow=False (план
    развоза) — за конец рабочего дня не планируем; тяжёлый заказ, у которого влезли не все поездки,
    снимается целиком (полдоставки не бывает), и рейсы собираются заново без него — его время
    достаётся другим заказам. windows[i] — окно приёма заказа i, center[i] — он в малом центре
    (только план развоза); allowed_trucks[i] — допустимые коды машин, None — без ограничения, пусто — никто.
    reasons — причины неназначенных заказов: window | center | vehicle | time; busy, departs, fixed
    (номера заказов закреплённых рейсов — в points) — как у plan_trips. Сборка плана развоза (overflow=False, без busy
    и departs): balance — выровнять загрузку рейсов (_balance, ответ №44); solver — рейсы решателем PyVRP поверх
    сборки (_solver, ответ №45; iterations — итераций поиска, меньше — быстрая проба набора машин, №77), берутся, если
    прошли проверку и лучше (_better), иначе — рейсы сборки. Обед (tn с
    обедом, №61) — во всех шагах по одному правилу; fed — машины, чей обед уже в занятом времени used / busy."""
    if (balance or solver) and (overflow or busy is not None or departs is not None):
        raise ValueError('balance и solver — только для сборки плана развоза: overflow=False, без busy и departs')
    load_cap = LOAD_CAP if solver else load_cap
    if load_cap is not None and not 0 < load_cap <= 1:
        raise ValueError('load_cap должен быть в пределах (0, 1]')
    uniq = sorted(set(points))
    node = {p: i + 1 for i, p in enumerate(uniq)}
    d, m = _matrices(uniq, depot, norms, tn)
    yerevan = {p for p in uniq if tn.in_yerevan(p)}      # зона Еревана (№68); зоны нет — пусто
    stops = [_Stop(node[p], float(kg), float(rev), tn.unload_at(float(kg), p),
                   *(windows[i] if windows is not None else (0.0, math.inf)), center is not None and bool(center[i]),
                   None if allowed_trucks is None or allowed_trucks[i] is None else frozenset(allowed_trucks[i]),
                   p in yerevan)
             for i, (p, kg, rev) in enumerate(zip(points, kgs, revenues))]
    if overflow or not trucks:
        return plan_trips(stops, d, m, trucks, tn, used, overflow, earliest, reasons, busy, departs, fixed, load_cap, fed)
    cap = max(t.capacity_kg for t in trucks)
    pinned = {i for _, idx, _ in (fixed or ()) for i in idx}
    # тяжёлый заказ в центре делится по тоннажу машин с правом въезда (_plan_timed)
    cap_c = max((t.capacity_kg for t in trucks if t.center_ok), default=cap)

    def cap_of(i: int) -> float:
        physical = max((t.capacity_kg for t in _eligible([i], stops, trucks)), default=cap)
        return physical * load_cap if load_cap is not None and stops[i].kg > physical + _EPS else physical

    keep = list(range(len(stops)))
    why: dict[int, str] = {}
    while True:
        got: dict[int, str] = {}
        when: list[float] = []
        pos = {i: k for k, i in enumerate(keep)}
        here = None if fixed is None else [(code, [pos[i] for i in idx], start) for code, idx, start in fixed]
        trips = [replace(t, items=tuple(keep[i] for i in t.items))
                 for t in plan_trips([stops[i] for i in keep], d, m, trucks, tn, used, overflow, earliest, got, busy,
                                      when, here, load_cap, fed)]
        why.update({keep[i]: r for i, r in got.items()})
        # закреплённые рейсы — уже куски тяжёлых заказов, их не проверяем
        pieces = Counter(i for t in trips for i in t.items if i not in pinned and stops[i].kg > cap_of(i) + _EPS)
        partial = {i for i, n in pieces.items() if n < math.ceil(stops[i].kg / cap_of(i))}
        if not partial:
            break
        keep = [i for i in keep if i not in partial]
    if balance or solver:
        best = _balance(trips, stops, d, m, trucks, tn, used, fixed, fed) if balance else trips
        if solver:
            fixed_keys = {(code, tuple(idx)) for code, idx, _ in (fixed or ())}

            def valid_load(rows: Sequence[Trip]) -> bool:
                shared = _shared(rows, stops, tn)
                truck_by_code = {t.car_code: t for t in trucks}
                return all((t.truck, t.items) in fixed_keys or
                           t.kg <= _load_limit(t.items, shared, truck_by_code[t.truck], trucks, LOAD_CAP) + 1e-6
                           for t in rows)

            if not valid_load(best):
                best = trips
        best = _polish_orders(best, stops, d, m, trucks, tn, used, fixed, fed)
        alt = _solver(trips, stops, d, m, trucks, tn, used, fixed, fed, iterations) if solver else None
        if alt is not None:
            balanced = _balance(alt, stops, d, m, trucks, tn, used, fixed, fed) if balance else alt
            alt = balanced if not solver or valid_load(balanced) else alt
            alt = _polish_orders(alt, stops, d, m, trucks, tn, used, fixed, fed)
            by = {t.car_code: t for t in trucks}
            # приоритет малых машин в Ереване (№68): плата за точки зоны на больших машинах — к литрам сравнения
            bias = ((lambda rows: math.fsum(_yerevan_bias(t.items, stops, by[t.truck], tn) for t in rows))
                    if yerevan else None)
            if _better(alt, best, tn, bias):
                best = alt
        trips = best
    if reasons is not None:
        placed = {i for t in trips for i in t.items}
        reasons.update({i: why.get(i, 'time') for i in range(len(stops)) if i not in placed})
    if departs is not None:
        departs.extend(when)
    return trips


def route_trip(points: Sequence[Point], kgs: Sequence[float], depot: Point, norms: Norms,
               tn: TruckNorms, reorder: bool = True, windows: Sequence[Window] | None = None,
               start: float = 0.0, truck: str | None = None) -> tuple[list[int], float, float]:
    """Рейс «склад → points → склад»: (порядок объезда — номера points, км, минуты: езда + разгрузка).
    reorder — улучшить порядок 2-opt (от заданного; длина не растёт), иначе — как задан. windows — окна
    приёма точек: если с выезда start (минуты от начала дня машины) заданный порядок их соблюдает, 2-opt
    их не нарушит; окна — с темпом машины truck (№66, tn.pace_of) и её надбавкой в зоне Еревана (№68, tn.yerevan_of;
    минуты рейса — без них, как прежде)."""
    if not points:
        return [], 0.0, 0.0
    pace, yer = tn.pace_of(truck), tn.yerevan_of(truck)
    d, m = _matrices(points, depot, norms, tn)
    stops = [_Stop(i + 1, float(kg), 0.0, tn.unload_at(float(kg), points[i]), yerevan=tn.in_yerevan(points[i]))
             for i, kg in enumerate(kgs)]
    if windows is not None:
        for s, (early, late) in zip(stops, windows):
            s.early, s.late = early, late
    seq = list(range(len(points)))
    if reorder:
        ok = None
        if windows is not None and _schedule(seq, stops, m, start, pace=pace, yerevan=yer)[1]:
            ok = lambda s2: _schedule(s2, stops, m, start, pace=pace, yerevan=yer)[1]   # noqa: E731
        seq = _two_opt(seq, stops, d, ok)
    minutes = (_schedule(seq, stops, m, start)[0] if hasattr(m, 'travel') else
               _closed(seq, stops, m) + math.fsum(s.unload for s in stops))
    return seq, _closed(seq, stops, d), minutes


def trip_schedule(points: Sequence[Point], kgs: Sequence[float], depot: Point, norms: Norms, tn: TruckNorms,
                  start: float = 0.0, windows: Sequence[Window] | None = None,
                  parts: dict[str, Any] | None = None,
                  lunch: tuple[bool, bool] | None = None, truck: str | None = None) -> tuple[float, list[float], float]:
    """Рейс «склад → points → склад» в заданном порядке, машина свободна с start (минуты от начала дня машины):
    (выезд, прибытия к точкам, минуты рейса от выезда — езда + разгрузка + ожидание у окон). Окно первой точки
    позже, чем машина туда доедет, — выезд позже, чтобы не ждать у неё (как ставит рейсы _plan_timed); выезд не
    позже, чем в плане, — прибытия не позже. Без окон выезд — start, минуты — те же, что у route_trip.
    parts — слагаемые минут рейса из этого же расчёта (_schedule): загрузка, езда, ожидание и разгрузка. lunch — (обед
    машины ещё впереди, после рейса у неё есть рейсы): рейс по правилу обеда (_lunch_trip; обед — parts['lunch']). truck —
    машина рейса: запас на рейс (№66; минуты — с ним, parts['buffer']), её темп (tn.pace_of) и надбавка в зоне Еревана
    (№68, tn.yerevan_of: разгрузка точки в parts['legs'] — с ней)."""
    if not points:
        return start, [], 0.0
    _, m = _matrices(points, depot, norms, tn)
    stops = [_Stop(i + 1, float(kg), 0.0, tn.unload_at(float(kg), points[i]),
                   *(windows[i] if windows is not None else (0.0, math.inf)), yerevan=tn.in_yerevan(points[i]))
             for i, kg in enumerate(kgs)]
    arrivals: list[float] = []
    if lunch is not None and tn.lunch_minutes > 0:
        depart, minutes, _, _ = _lunch_trip(range(len(points)), stops, m, start, tn, lunch[0], lunch[1], arrivals,
                                            parts, truck)
        return depart, arrivals, minutes
    pace = tn.pace_of(truck)
    depart = _departure(range(len(points)), stops, m, start, pace)
    minutes, _ = _schedule(range(len(points)), stops, m, depart, arrivals, parts, buffer=tn.buffer_c, pace=pace,
                           yerevan=tn.yerevan_of(truck))
    return depart, arrivals, minutes


def trip_forecast(points: Sequence[Point], kgs: Sequence[float], depot: Point, norms: Norms, tn: TruckNorms,
                  depart: float, windows: Sequence[Window] | None = None, lunch: tuple[bool, bool] | None = None,
                  truck: str | None = None) -> tuple[float, Break | None]:
    """Минуты рейса «склад → points → склад» по модели плана с выезда depart (минуты от начала дня машины, загрузка уже
    позади) — для обучения запаса на рейс (№66, learning.trip_obs): езда (часовой профиль — по часам выезда), разгрузка
    со своим временем магазинов, ожидание окон приёма (windows; нет — без окон), темп машины truck, её надбавка в зоне
    Еревана (№68) и обед в рейсе по
    правилу _schedule (lunch — (обед машины ещё впереди, после рейса у неё есть рейсы)); без запаса. (минуты от выезда
    до возвращения, обед в рейсе или None)."""
    _, m = _matrices(points, depot, norms, tn)
    stops = [_Stop(i + 1, float(kg), 0.0, tn.unload_at(float(kg), points[i]),
                   *(windows[i] if windows is not None else (-math.inf, math.inf)), yerevan=tn.in_yerevan(points[i]))
             for i, kg in enumerate(kgs)]
    loading = m.load(math.fsum(float(kg) for kg in kgs)) if hasattr(m, 'travel') else 0.0
    meal = None
    if lunch is not None and lunch[0] and tn.lunch_minutes > 0:
        meal = (tn.lunch_from, tn.lunch_minutes, lunch[1], tn.lunch_to if tn.lunch_to > tn.lunch_from else math.inf)
    got: list[Break] = []
    minutes, _ = _schedule(range(len(points)), stops, m, depart - loading, lunch=meal, breaks=got,
                           pace=tn.pace_of(truck), yerevan=tn.yerevan_of(truck))
    return minutes - loading, (got[0] if got else None)


def load_limit(kgs: Sequence[float], center: Sequence[bool], allowed: Sequence[Collection[str] | None],
               truck: FleetTruck, trucks: Sequence[FleetTruck]) -> float:
    """Предел груза рейса из заказов kgs (в центре, допустимые машины) для машины truck по правилу сборки LOAD_CAP
    (_load_limit): 90% тоннажа; рейс из одного заказа тяжелее 90% самой большой подходящей ему машины из trucks — весь
    тоннаж (такой заказ иначе не увезти)."""
    stops = [_Stop(0, float(kg), 0.0, 0.0, center=c, allowed_trucks=None if a is None else frozenset(a))
             for kg, c, a in zip(kgs, center, allowed)]
    return _load_limit(range(len(stops)), stops, truck, trucks, LOAD_CAP)


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
    wear_amd: float = 0.0


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
    wear_amd: float = 0.0

    @property
    def load_pct(self) -> float | None:
        return self.kg_total / self.trip_capacity * 100.0 if self.trip_capacity > 0 else None

    @property
    def load_pct_peak(self) -> float | None:
        return self.peak_kg_total / self.peak_capacity * 100.0 if self.peak_capacity > 0 else None


def _matrices(points: Sequence[Point], depot: Point, norms: Norms, tn: TruckNorms | None = None) -> tuple[Matrix, Matrix]:
    """Км (norms.km — по дорогам или по прямой × извилистость) и минуты езды между складом (0) и точками дня —
    направленно: d[a][b] — от a до b (одностороннее движение, Valhalla). Дороги — профиль грузовика
    (Norms.for_trucks); скорость участка — Norms.leg_speed (время Valhalla; иначе скорость города, если оба конца в
    городе, или области)."""
    norms = norms.for_trucks()
    if norms.traffic is not None and tn is not None and tn.work_start_minute is not None:
        norms = replace(norms, traffic_start_min=tn.work_start_minute)
    pts = [depot, *points]
    if norms.roads is not None:
        norms.roads.ensure(pts)
    n = len(pts)
    city = [in_city(p, norms.city_center, norms.city_radius_km) for p in pts]
    d = [[0.0] * n for _ in range(n)]
    m = [[0.0] * n for _ in range(n)]
    v = [[0.0] * n for _ in range(n)]   # скорость участка, км/ч (до часового профиля)
    for a in range(n):
        pa = pts[a]
        for b in range(n):
            if a == b:
                continue
            pb = pts[b]
            km = norms.km(pa, pb)
            both = city[a] and city[b]
            speed = v[a][b] = norms.leg_speed(pa, pb, km, both)
            if norms.traffic is not None:
                speed *= norms.traffic.minimum(both)
            d[a][b] = km
            m[a][b] = km / speed * 60.0
            if norms.provider is not None:
                m[a][b] = norms.provider.minutes(pa, pb)
    if norms.provider is not None or norms.traffic is not None or (tn is not None and tn.load(1000) > 0):
        from .traffic_validation import TravelMatrix
        m = TravelMatrix(m, d, city, norms, tn, pts, v)
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
    norms = replace(norms, traffic_weekday=weekday - 1)
    d, m = _matrices(points, depot, norms, tn)
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
            load_pct=kg / (len(mine) * truck.capacity_kg) * 100.0,
            wear_amd=math.fsum(t.wear_amd for t in mine) / n))
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
        wear_amd=math.fsum(t.wear_amd for t in flat) / n,
    )


def day_running_cost(visits: Sequence[DeliveryVisit], depot: Point, trucks: Sequence[FleetTruck], norms: Norms,
                     tn: TruckNorms, n: int = FLEET_SAMPLES) -> RunningCost:
    """Литры дня доставки — ровно fleet_day(...).liters (те же пробы года), без пика и прочих цифр:
    вдвое быстрее, для проверки дизеля в оптимизаторе (№33)."""
    visits = sorted(visits, key=lambda v: (v.customer_id, v.weekday, v.point))
    points = sorted({v.point for v in visits})
    node = {p: i + 1 for i, p in enumerate(points)}
    d, m = _matrices(points, depot, norms, tn)
    year = [plan_trips(st, d, m, trucks, tn) for st in _samples(visits, node, 'year', n, tn)]
    return RunningCost(math.fsum(t.liters for trips in year for t in trips) / n,
                       math.fsum(t.wear_amd for trips in year for t in trips) / n,
                       math.fsum(t.payload_tonne_km for trips in year for t in trips) / n,
                       all(t.fuel_empty_l_per_100km is not None for t in trucks),
                       all(t.wear_amd_per_km is not None or t.wear_load_amd_per_km is not None for t in trucks))


def day_liters(visits: Sequence[DeliveryVisit], depot: Point, trucks: Sequence[FleetTruck], norms: Norms,
               tn: TruckNorms, n: int = FLEET_SAMPLES) -> float:
    return day_running_cost(visits, depot, trucks, norms, tn, n).liters


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
            'wear_amd': fsum(d.wear_amd for d in days) / w,
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

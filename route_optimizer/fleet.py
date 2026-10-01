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

Окна приёма магазинов и малый центр (windows-center-plan.md, ответы владельца №34–41) — только в плане
развоза: у точки есть окно или она в центре — plan_trips идёт путём _plan_timed; без них — прежним путём,
рейсы те же (модель парка и проверка дизеля №33 окон не знают).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

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
    center_ok: bool = False    # можно въезжать в малый центр (№39–41; store.Bundle.truck_center_ok)


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
                                float(t.fuel_l_per_100km)))
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
    # окно приёма — прибытие (начало разгрузки) в минутах от начала рабочего дня машины: раньше early — машина
    # ждёт, позже late — нарушение; center — точка в малом центре: везёт только машина с правом въезда
    early: float = 0.0
    late: float = math.inf
    center: bool = False


def _closed(seq: Sequence[int], stops: Sequence[_Stop], d: Matrix) -> float:
    """Длина рейса «склад → стопы seq → склад» по матрице d."""
    prev, total = 0, 0.0
    for v in seq:
        n = stops[v].node
        total += d[prev][n]
        prev = n
    return total + d[prev][0]


def _schedule(seq: Sequence[int], stops: Sequence[_Stop], m: Matrix, start: float,
              arrivals: list[float] | None = None) -> tuple[float, bool]:
    """Рейс «склад → seq → склад» с выезда start (минуты от начала дня машины), с ожиданием у окон:
    (минуты от выезда до возвращения на склад — езда + разгрузка + ожидание, все окна соблюдены).
    Без ожидания минуты — ровно езда + разгрузка, как в остальном модуле. arrivals — сюда дописываются
    прибытия к точкам (начало разгрузки)."""
    t, prev, wait, ok = start, 0, 0.0, True
    for v in seq:
        s = stops[v]
        t += m[prev][s.node]
        if t < s.early:
            wait += s.early - t
            t = s.early
        if t > s.late + _EPS:
            ok = False
        if arrivals is not None:
            arrivals.append(t)
        t += s.unload
        prev = s.node
    return _closed(seq, stops, m) + math.fsum(stops[v].unload for v in seq) + wait, ok


def _two_opt(seq: list[int], stops: Sequence[_Stop], d: Matrix,
             ok: Callable[[list[int]], bool] | None = None) -> list[int]:
    """2-opt рейса «склад → seq → склад» (склад на месте); длина не растёт. ok — ход принимается, только если
    рейс после него допустим (окна приёма)."""
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
                    if ok is not None and not ok(t[1:i] + t[i:j + 1][::-1] + t[j + 1:]):
                        continue
                    t[i:j + 1] = t[i:j + 1][::-1]
                    node[i:j + 1] = node[i:j + 1][::-1]
                    improved = True
    return t[1:]


def _savings(light: Sequence[int], stops: Sequence[_Stop], d: Matrix, m: Matrix, cap: float,
             window: float, fits: Callable[[list[int], list[int]], list[int] | None] | None = None) -> list[list[int]]:
    """Кларк–Райт (параллельная версия): s(i, j) = d(0,i) + d(0,j) − d(i,j), по убыванию (ничьи — по
    номеру); маршруты сливаются через концы, пока груз ≤ cap и время рейса ≤ window. fits(a, b) — проверка
    слияния a + b (окна приёма, тоннаж центра): допустимый порядок объезда слитого рейса или None."""
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
        if t > window + _EPS:   # езда + разгрузка — нижняя граница времени рейса и с ожиданием у окон
            continue
        if fits is not None:
            merged = fits(A if A[-1] == i else A[::-1], B if B[0] == j else B[::-1])
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


def _time_head(seq: Sequence[int], stops: Sequence[_Stop], m: Matrix, trucks: Sequence[FleetTruck],
               used: Mapping[str, float], window: float) -> int:
    """Сколько первых точек рейса (по ходу объезда) успевает хоть одна машина до конца рабочего дня
    (с её тоннажем и уже занятым временем); 0 — ни одной."""
    best = 0
    for t in trucks:
        left = window - used[t.car_code]
        k, kg, unload = 0, 0.0, 0.0
        while k < len(seq):
            kg += stops[seq[k]].kg
            unload += stops[seq[k]].unload
            if kg > t.capacity_kg + _EPS or _closed(seq[:k + 1], stops, m) + unload > left + _EPS:
                break
            k += 1
        best = max(best, k)
    return best


def plan_trips(stops: Sequence[_Stop], d: Matrix, m: Matrix, trucks: Sequence[FleetTruck],
               tn: TruckNorms, used: Mapping[str, float] | None = None, overflow: bool = True,
               earliest: bool = False, reasons: dict[int, str] | None = None,
               busy: Mapping[str, Sequence[tuple[float, float]]] | None = None,
               departs: list[float] | None = None,
               fixed: Sequence[tuple[str, Sequence[int], float]] | None = None) -> list[Trip]:
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
    меняются, остальные рейсы раскладываются вокруг (_plan_timed). Без окон, центра, busy и fixed — прежний путь."""
    if not stops or not trucks:
        return []
    if busy is not None or fixed is not None or any(s.early > 0 or s.late < math.inf or s.center for s in stops):
        return _plan_timed(stops, d, m, trucks, tn, used, overflow, earliest, reasons, busy, departs, fixed)
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
            if not overflow:
                if len(seq) > 1:
                    # порядок объезда случаен по направлению: успевает больше — с того конца и режем;
                    # ни с одного конца ни одной точки — отрываем крайнюю, чтобы проверить остальные
                    fwd = _time_head(seq, vs, m, trucks, used, window)
                    back = _time_head(seq[::-1], vs, m, trucks, used, window)
                    s2, head = (seq, fwd) if fwd >= back else (seq[::-1], back)
                    head = min(max(head, 1), len(seq) - 1)
                    queue = sorted(queue + [shortest(s2[:head]), shortest(s2[head:])])
                continue
            # рейс за пределами дня: везёт машина, которая поднимет груз и освободится раньше всех
            fits = [t for t in trucks if t.capacity_kg >= kg - _EPS]
            truck = min(fits, key=lambda t: (used[t.car_code], t.l100, t.car_code))
        elif earliest:
            truck = min(fits, key=lambda t: (used[t.car_code] + minutes, t.l100, t.car_code))
        else:
            truck = min(fits, key=lambda t: (t.l100, -t.capacity_kg, t.car_code))
        used[truck.car_code] += minutes
        trips.append(Trip(truck.car_code, len(seq), kg, math.fsum(vs[v].revenue for v in seq), km, minutes,
                          truck.capacity_kg, km * truck.l100 / 100.0, extra, tuple(origin[v] for v in seq)))
    return trips


WAIT_MERGE_SLACK = 15.0     # слияние рейсов может добавить ожидания внутри рейса не больше, мин


def _plan_timed(stops: Sequence[_Stop], d: Matrix, m: Matrix, trucks: Sequence[FleetTruck], tn: TruckNorms,
                used: Mapping[str, float] | None, overflow: bool, earliest: bool,
                reasons: dict[int, str] | None, busy: Mapping[str, Sequence[tuple[float, float]]] | None = None,
                departs: list[float] | None = None,
                fixed: Sequence[tuple[str, Sequence[int], float]] | None = None) -> list[Trip]:
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
    (extra); причины у них нет. Остальные рейсы встают вокруг."""
    window = tn.work_minutes
    why = reasons if reasons is not None else {}
    cap = max(t.capacity_kg for t in trucks)
    central = [t for t in trucks if t.center_ok]
    cap_c = max((t.capacity_kg for t in central), default=0.0)
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
        if s.center and not central:
            why[i] = 'center'
            continue
        c = cap_c if s.center else cap
        if s.kg > c + _EPS:
            n = math.ceil(s.kg / c)
            for _ in range(n):
                singles.append(len(vs))
                origin.append(i)
                vs.append(_Stop(s.node, s.kg / n, s.revenue / n, tn.unload(s.kg / n), s.early, s.late, s.center))
        else:
            origin.append(i)
            vs.append(s)

    def timed(seq: Sequence[int]) -> bool:
        return any(vs[v].early > 0 or vs[v].late < math.inf for v in seq)

    def is_central(seq: Sequence[int]) -> bool:
        return any(vs[v].center for v in seq)

    def ok_at_start(seq: list[int]) -> bool:
        minutes, ok = _schedule(seq, vs, m, 0.0)
        return ok and minutes <= window + _EPS

    def first_wait(seq: Sequence[int], start: float) -> float:
        """Ожидание у первой точки: его убирает поздний выезд."""
        return max(0.0, vs[seq[0]].early - start - m[0][vs[seq[0]].node])

    def idle(seq: Sequence[int]) -> float:
        """Ожидание внутри рейса — после первой точки (поздним выездом его не убрать)."""
        t, prev, wait = 0.0, 0, 0.0
        for k, v in enumerate(seq):
            s = vs[v]
            t += m[prev][s.node]
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

    def fits(a: list[int], b: list[int]) -> list[int] | None:
        seq = a + b
        if is_central(seq) and math.fsum(vs[v].kg for v in seq) > cap_c + _EPS:
            return None
        if not timed(seq):
            return seq
        limit = idle(a) + idle(b) + WAIT_MERGE_SLACK
        ok = [s2 for s2 in (seq, seq[::-1]) if ok_at_start(s2) and idle(s2) <= limit]
        return min(ok, key=idle) if ok else None

    single_set = set(singles) | set(at.values())
    light = [v for v in range(len(vs)) if v not in single_set]
    routes = [_two_opt(r, vs, d, guard(r)) for r in _savings(light, vs, d, m, cap, window, fits)]
    routes += [[v] for v in singles]

    def item(seq: list[int]) -> tuple:
        minutes, _ = _schedule(seq, vs, m, 0.0)
        kg = math.fsum(vs[v].kg for v in seq)
        deadline = min(vs[v].late for v in seq)
        release = max(0.0, max(vs[v].early for v in seq))
        # EDF; центровые — раньше; рейс, который раньше release не начать, — позже; дальше тяжёлые и длинные
        # первыми; ничьи — по точкам рейса
        return (deadline, not is_central(seq), release, -kg, -minutes, tuple(vs[v].node for v in seq), seq,
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

    def gaps(code: str) -> list[tuple[float, float]]:
        """Свободные промежутки машины по порядку; последний — до конца рабочего дня."""
        out, end = [], 0.0
        for a, b in slots[code]:
            if a > end + _EPS:
                out.append((end, a))
            end = max(end, b)
        out.append((end, max(end, window)))
        return out

    def place(seq: Sequence[int], code: str) -> tuple[float, float, float] | None:
        """Самый ранний промежуток машины, где рейс укладывается с окнами: (начало промежутка, ожидание у первой
        точки — на столько позже выезд, минуты от начала промежутка до возвращения)."""
        for g0, g1 in gaps(code):
            minutes, ok = _schedule(seq, vs, m, g0)
            if ok and g0 + minutes <= g1 + _EPS:
                return g0, first_wait(seq, g0), minutes
        return None

    def last_end(code: str) -> float:
        return max((b for _, b in slots[code]), default=0.0)

    def misses(seq: Sequence[int], start: float) -> tuple[int, ...]:
        """Точки рейса, к которым машина с выезда start приезжает позже окна."""
        arr: list[float] = []
        _schedule(seq, vs, m, start, arr)
        return tuple(v for v, t in zip(seq, arr) if t > vs[v].late + _EPS)

    def plain_gap(seq: Sequence[int], kg: float, t: FleetTruck) -> float | None:
        """Начало первого промежутка, куда рейс встал бы без окон (тоннаж и время), или None."""
        if t.capacity_kg < kg - _EPS:
            return None
        plain = _closed(seq, vs, m) + math.fsum(vs[v].unload for v in seq)
        return next((g0 for g0, g1 in gaps(t.car_code) if g0 + plain <= g1 + _EPS), None)

    def head(seq: Sequence[int], allowed: Sequence[FleetTruck]) -> int:
        """Сколько первых точек рейса успевает хоть одна допустимая машина: тоннаж, окна, конец дня."""
        best = 0
        for t in allowed:
            k, kg = 0, 0.0
            while k < len(seq):
                kg += vs[seq[k]].kg
                if kg > t.capacity_kg + _EPS or place(seq[:k + 1], t.car_code) is None:
                    break
                k += 1
            best = max(best, k)
        return best

    planned: list[tuple[float, int, Trip]] = []     # (выезд, номер назначения, рейс)

    def assign(truck: FleetTruck, seq: list[int], km: float, kg: float, spot: tuple[float, float, float],
               extra: bool) -> None:
        g0, wait, minutes = spot
        depart = g0 + wait                         # не ждать у первой точки: машина выезжает позже
        slots[truck.car_code] = sorted(slots[truck.car_code] + [(depart, g0 + minutes)])
        planned.append((depart, len(planned), Trip(
            truck.car_code, len(seq), kg, math.fsum(vs[v].revenue for v in seq), km, minutes - wait,
            truck.capacity_kg, km * truck.l100 / 100.0, extra, tuple(origin[v] for v in seq))))

    def free_at(a: float, b: float, code: str) -> bool:
        return any(g0 <= a + _EPS and b <= g1 + _EPS for g0, g1 in gaps(code))

    by_code = {t.car_code: t for t in trucks}
    for code, idx, start in sorted((f for f in (fixed or ()) if f[1] and f[0] in by_code), key=lambda f: f[2]):
        seq = [at[i] for i in idx]
        minutes, ok = _schedule(seq, vs, m, start)
        spot = (start, first_wait(seq, start), minutes)
        if not (ok and start + minutes <= window + _EPS and free_at(start + spot[1], start + minutes, code)):
            spot = place(seq, code)
        extra = spot is None
        if extra:   # не помещается — всё равно везёт его машина: в конец её дня (нарушение видно в плане)
            g0 = last_end(code)
            spot = (g0, first_wait(seq, g0), _schedule(seq, vs, m, g0)[0])
        assign(by_code[code], seq, _closed(seq, vs, d), math.fsum(vs[v].kg for v in seq), spot, extra)

    queue = sorted(item(r) for r in routes)
    while queue:
        _, plain_order, _, _, _, _, seq, km, _, kg = queue.pop(0)
        allowed = central if not plain_order else list(trucks)
        spots = {t.car_code: place(seq, t.car_code) for t in allowed if t.capacity_kg >= kg - _EPS}
        fit = [t for t in allowed if spots.get(t.car_code) is not None]
        extra = not fit
        if extra:
            spare = [t for t in allowed if t.capacity_kg < kg - _EPS and gaps(t.car_code)[-1][0] < window - _EPS]
            if spare and len(seq) > 1:
                pieces = _cut(seq, vs, max(t.capacity_kg for t in spare))
                if len(pieces) > 1:
                    queue = sorted(queue + [item(_two_opt(p, vs, d, guard(p))) for p in pieces])
                    continue
            if not overflow:
                free = [(g0, t) for t in allowed if (g0 := plain_gap(seq, kg, t)) is not None]
                if len(seq) == 1:
                    why[origin[seq[0]]] = 'window' if free else 'time'
                    continue
                bad = min((misses(seq, g0) for g0, _ in free), key=lambda b: (len(b), b), default=())
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
            g0 = last_end(truck.car_code)
            spots[truck.car_code] = (g0, first_wait(seq, g0), _schedule(seq, vs, m, g0)[0])
        else:
            # машину с правом въезда бережём для центровых рейсов, пока они есть в очереди
            spare_center = plain_order and any(not q[1] for q in queue)
            if earliest:
                truck = min(fit, key=lambda t: (spare_center and t.center_ok, spots[t.car_code][0] + spots[t.car_code][2],
                                                t.l100, t.car_code))
            else:
                truck = min(fit, key=lambda t: (spare_center and t.center_ok, t.l100, -t.capacity_kg, t.car_code))
        assign(truck, seq, km, kg, spots[truck.car_code], extra)
    planned.sort(key=lambda x: (x[0], x[1]))
    if departs is not None:
        departs.extend(dep for dep, *_ in planned)
    return [t for *_, t in planned]


# --- День с известными заказами (план развоза, dispatch.py) ---

Window = tuple[float, float]   # окно приёма: прибыть не раньше и не позже, минуты от начала дня машины


def route_day(points: Sequence[Point], kgs: Sequence[float], revenues: Sequence[float], depot: Point,
              trucks: Sequence[FleetTruck], norms: Norms, tn: TruckNorms,
              used: Mapping[str, float] | None = None, overflow: bool = True,
              earliest: bool = False, windows: Sequence[Window] | None = None,
              center: Sequence[bool] | None = None, reasons: dict[int, str] | None = None,
              busy: Mapping[str, Sequence[tuple[float, float]]] | None = None,
              departs: list[float] | None = None,
              fixed: Sequence[tuple[str, Sequence[int], float]] | None = None) -> list[Trip]:
    """Рейсы дня по известным заказам — тот же расчёт, что у пробы Монте-Карло (plan_trips):
    заказ i — точка points[i], kgs[i] кг; Trip.items — номера заказов по порядку объезда.
    Тяжелее самой большой машины — несколько поездок к одному заказу поровну. overflow=False (план
    развоза) — за конец рабочего дня не планируем; тяжёлый заказ, у которого влезли не все поездки,
    снимается целиком (полдоставки не бывает), и рейсы собираются заново без него — его время
    достаётся другим заказам. windows[i] — окно приёма заказа i, center[i] — он в малом центре
    (только план развоза); reasons — причины неназначенных заказов: window | center | time; busy, departs, fixed
    (номера заказов закреплённых рейсов — в points) — как у plan_trips."""
    uniq = sorted(set(points))
    node = {p: i + 1 for i, p in enumerate(uniq)}
    d, m = _matrices(uniq, depot, norms)
    stops = [_Stop(node[p], float(kg), float(rev), tn.unload(float(kg)),
                   *(windows[i] if windows is not None else (0.0, math.inf)), center is not None and bool(center[i]))
             for i, (p, kg, rev) in enumerate(zip(points, kgs, revenues))]
    if overflow or not trucks:
        return plan_trips(stops, d, m, trucks, tn, used, overflow, earliest, reasons, busy, departs, fixed)
    cap = max(t.capacity_kg for t in trucks)
    pinned = {i for _, idx, _ in (fixed or ()) for i in idx}
    # тяжёлый заказ в центре делится по тоннажу машин с правом въезда (_plan_timed)
    cap_c = max((t.capacity_kg for t in trucks if t.center_ok), default=cap)

    def cap_of(i: int) -> float:
        return cap_c if stops[i].center else cap

    keep = list(range(len(stops)))
    why: dict[int, str] = {}
    while True:
        got: dict[int, str] = {}
        when: list[float] = []
        pos = {i: k for k, i in enumerate(keep)}
        here = None if fixed is None else [(code, [pos[i] for i in idx], start) for code, idx, start in fixed]
        trips = [replace(t, items=tuple(keep[i] for i in t.items))
                 for t in plan_trips([stops[i] for i in keep], d, m, trucks, tn, used, overflow, earliest, got, busy,
                                     when, here)]
        why.update({keep[i]: r for i, r in got.items()})
        # закреплённые рейсы — уже куски тяжёлых заказов, их не проверяем
        pieces = Counter(i for t in trips for i in t.items if i not in pinned and stops[i].kg > cap_of(i) + _EPS)
        partial = {i for i, n in pieces.items() if n < math.ceil(stops[i].kg / cap_of(i))}
        if not partial:
            break
        keep = [i for i in keep if i not in partial]
    if reasons is not None:
        placed = {i for t in trips for i in t.items}
        reasons.update({i: why.get(i, 'time') for i in range(len(stops)) if i not in placed})
    if departs is not None:
        departs.extend(when)
    return trips


def route_trip(points: Sequence[Point], kgs: Sequence[float], depot: Point, norms: Norms,
               tn: TruckNorms, reorder: bool = True, windows: Sequence[Window] | None = None,
               start: float = 0.0) -> tuple[list[int], float, float]:
    """Рейс «склад → points → склад»: (порядок объезда — номера points, км, минуты: езда + разгрузка).
    reorder — улучшить порядок 2-opt (от заданного; длина не растёт), иначе — как задан. windows — окна
    приёма точек: если с выезда start (минуты от начала дня машины) заданный порядок их соблюдает, 2-opt
    их не нарушит."""
    if not points:
        return [], 0.0, 0.0
    d, m = _matrices(points, depot, norms)
    stops = [_Stop(i + 1, float(kg), 0.0, tn.unload(float(kg))) for i, kg in enumerate(kgs)]
    if windows is not None:
        for s, (early, late) in zip(stops, windows):
            s.early, s.late = early, late
    seq = list(range(len(points)))
    if reorder:
        ok = None
        if windows is not None and _schedule(seq, stops, m, start)[1]:
            ok = lambda s2: _schedule(s2, stops, m, start)[1]   # noqa: E731
        seq = _two_opt(seq, stops, d, ok)
    minutes = _closed(seq, stops, m) + math.fsum(s.unload for s in stops)
    return seq, _closed(seq, stops, d), minutes


def trip_schedule(points: Sequence[Point], kgs: Sequence[float], depot: Point, norms: Norms, tn: TruckNorms,
                  start: float = 0.0, windows: Sequence[Window] | None = None) -> tuple[float, list[float], float]:
    """Рейс «склад → points → склад» в заданном порядке, машина свободна с start (минуты от начала дня машины):
    (выезд, прибытия к точкам, минуты рейса от выезда — езда + разгрузка + ожидание у окон). Окно первой точки
    позже, чем машина туда доедет, — выезд позже, чтобы не ждать у неё (как ставит рейсы _plan_timed); выезд не
    позже, чем в плане, — прибытия не позже. Без окон выезд — start, минуты — те же, что у route_trip."""
    if not points:
        return start, [], 0.0
    _, m = _matrices(points, depot, norms)
    stops = [_Stop(i + 1, float(kg), 0.0, tn.unload(float(kg)), *(windows[i] if windows is not None else (0.0, math.inf)))
             for i, kg in enumerate(kgs)]
    depart = max(start, stops[0].early - m[0][1])
    arrivals: list[float] = []
    minutes, _ = _schedule(range(len(points)), stops, m, depart, arrivals)
    return depart, arrivals, minutes


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


def day_liters(visits: Sequence[DeliveryVisit], depot: Point, trucks: Sequence[FleetTruck], norms: Norms,
               tn: TruckNorms, n: int = FLEET_SAMPLES) -> float:
    """Литры дня доставки — ровно fleet_day(...).liters (те же пробы года), без пика и прочих цифр:
    вдвое быстрее, для проверки дизеля в оптимизаторе (№33)."""
    visits = sorted(visits, key=lambda v: (v.customer_id, v.weekday, v.point))
    points = sorted({v.point for v in visits})
    node = {p: i + 1 for i, p in enumerate(points)}
    d, m = _matrices(points, depot, norms)
    year = [plan_trips(st, d, m, trucks, tn) for st in _samples(visits, node, 'year', n, tn)]
    return math.fsum(t.liters for trips in year for t in trips) / n


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

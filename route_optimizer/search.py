# -*- coding: utf-8 -*-
"""Быстрая оценка и поиск дней визитов внутри одного менеджера (этап 3, §5–§6, режим А).

Чистая логика — без Flask и без БД. Единицы: км (по прямой × извилистость — уже в матрицах),
минуты, драмы; стоимость C — драм в неделю: сумма по дням цикла / W (+ штраф за переносы).

Состояние дня обновляется при каждом ходе инкрементально:
- тур менеджера «дом → клиенты дня → дом»: удаление — выигрыш d(prev,c) + d(c,next) − d(prev,next),
  вставка — самая дешёвая позиция; без дома вершина 0 фиктивная (нулевые расстояния) — открытый путь;
- выручка дня (низкий сезон) — нормальное приближение: μ = Σ p·m, σ² = Σ (p·E[V²] − (p·m)²);
- грузовики — общий для всех менеджеров парк (FleetEstimate, план fleet-plan §3): S сценариев с
  общими случайными числами — у каждого визита клиента (порядковый номер k в цикле) заранее вытянуто
  равномерное u[k][s], «заказал» = u < p_год; флаг переезжает вместе с визитом. На слот и сценарий —
  гигантский тур «склад → заказавшие точки дня у всех менеджеров → склад»; км парка — тур, нарезанный
  по тоннажу. Менеджеры связаны через общие дни доставки: поиск идёт по менеджерам по очереди
  (Гаусс–Зейдель), стоимость состояния — его слагаемые + дизель всего парка.
Через каждые 50 принятых ходов изменённые туры менеджера проходят 2-opt, дни пересчитываются точно;
туры парка — 2-opt после поиска менеджера (FleetEstimate.polish).
"""
from __future__ import annotations

import heapq
import math
import random
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

W = 2                     # недель в цикле
SLOTS = 14                # слот = (неделя − 1) × 7 + (день недели − 1)
TRUCK_SCENARIOS = 4       # S
MAX_VISITS = SLOTS        # визитов клиента за цикл — не больше числа слотов
NEIGHBORS = 10            # кандидаты обмена — ближайшие клиенты менеджера
TWO_OPT_EVERY = 50        # 2-opt изменённых туров — через каждые 50 принятых ходов
MIN_GAIN = 1.0            # ход принимается при ΔC < −1 драм
PERTURB_SHARE = 0.05      # ILS: доля клиентов, получающих случайный шаблон
MAX_PERTURBATIONS = 30    # ILS: предел возмущений (детерминизм — счётчик, а не время)
# ILS: ранний стоп после 10 возмущений подряд без улучшения (при 3 старт «с нуля» застревал:
# у A003/9 план «с нуля» выходил заметно дороже текущего)
MAX_IDLE_PERTURBATIONS = 10
_VAR_EPS = 1e-6
_SQRT2 = math.sqrt(2.0)
_TWO_OPT_EPS = 1e-12

Matrix = list[list[float]]
SlotPattern = tuple[int, ...]   # отсортированные слоты


def slot_of_day(week: int, weekday: int) -> int:
    return (week - 1) * 7 + (weekday - 1)


def day_of_slot(slot: int) -> tuple[int, int]:
    return slot // 7 + 1, slot % 7 + 1


def is_moved(current: Sequence[int], pattern: Sequence[int]) -> bool:
    """Перенос для штрафа: дни другие, а не только снижена/повышена частота на прежних днях."""
    cur, new = set(current), set(pattern)
    return not (new <= cur or cur <= new)


# --- Выручка дня: нормальное приближение ---

def p_at_least(mu: float, var: float, threshold: float) -> float:
    """P(выручка ≥ порога) = 1 − Φ((T − μ)/σ); σ = 0 — ступенька."""
    if var <= _VAR_EPS:
        return 1.0 if mu >= threshold - 1e-9 else 0.0
    return 0.5 * math.erfc((threshold - mu) / (math.sqrt(var) * _SQRT2))


def p_poor(mu: float, var: float, sure: int, log_q: float, threshold: float) -> float:
    """P(0 < выручка < порога рейса) ≈ Φ((T − μ)/σ) − P(заказов нет); P(нет) = Π(1 − p) — точно."""
    if var <= _VAR_EPS:
        below = 1.0 if mu < threshold else 0.0
    else:
        below = 0.5 * math.erfc((mu - threshold) / (math.sqrt(var) * _SQRT2))
    none = 0.0 if sure > 0 else math.exp(log_q)
    return max(0.0, below - none)


# --- Входные данные ---

@dataclass(frozen=True)
class VisitParams:
    """Один визит клиента при данной частоте (p = min(1, λ/f))."""
    mu: float          # p × средний заказ, низкий сезон
    var: float         # p × E[V²] − (p × m)²
    p_low: float
    p_year: float      # флаги «заказал» для парка — по году, как в точной оценке
    kg: float          # кг заказа, если заказал (средний за год) — нарезка рейсов парка по тоннажу

    @property
    def sure(self) -> bool:
        return self.p_low >= 1.0

    @property
    def log_q(self) -> float:
        return math.log1p(-self.p_low) if 0.0 < self.p_low < 1.0 else 0.0


NO_VISIT = VisitParams(0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Weights:
    """Веса стоимости §4, драм. Дизель парка — в FleetEstimate. Штраф за переносы задаётся
    состоянию отдельно (при старте «fresh» он 0)."""
    manager_per_km: float
    weak_day: float
    poor_trip: float
    overtime_per_min: float
    window_min: float
    min_day_revenue: float
    min_trip_revenue: float


@dataclass(frozen=True)
class Line:
    """Клиент менеджера."""
    customer_id: int
    node: int                                   # вершина в матрицах ≥ 1; 0 — без координат
    minutes: float                              # длительность визита
    current: SlotPattern                        # текущий шаблон
    allowed: tuple[SlotPattern, ...]            # из чего выбирает поиск (закреплённый — один)
    locked: bool
    u: tuple[tuple[float, ...], ...]            # [визит k][сценарий s] — числа для флагов
    gnode: int = 0                              # вершина в матрице парка ≥ 1; 0 — без координат


@dataclass
class Problem:
    agent_id: int
    lines: list[Line]
    km: Matrix                  # менеджер: вершина 0 — дом (без дома — нули)
    mins: Matrix                # минуты в пути по тем же рёбрам
    weights: Weights
    workday: tuple[bool, ...]   # по слотам
    base: tuple[bool, ...]      # рабочие слоты, где менеджер работает сейчас: пустой — слабый день
    neighbors: list[tuple[int, ...]]
    seed: int


def make_u(rng: random.Random) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(rng.random() for _ in range(TRUCK_SCENARIOS)) for _ in range(MAX_VISITS))


def nearest_lines(nodes: Sequence[int], km: Matrix, k: int = NEIGHBORS) -> list[tuple[int, ...]]:
    """Для каждой строки с координатами — k ближайших строк с координатами (ничьи — по индексу)."""
    located = [i for i, x in enumerate(nodes) if x]
    out: list[tuple[int, ...]] = [() for _ in nodes]
    for i in located:
        row = km[nodes[i]]
        out[i] = tuple(heapq.nsmallest(k, (j for j in located if j != i),
                                       key=lambda j: (row[nodes[j]], j)))
    return out


# --- Туры ---

def tour_length(t: Sequence[int], d: Matrix) -> float:
    if len(t) < 2:
        return 0.0
    total = d[t[-1]][t[0]]
    for a, b in zip(t, t[1:]):
        total += d[a][b]
    return total


def two_opt(t: list[int], d: Matrix) -> list[int]:
    """2-opt замкнутого тура на месте; t[0] (дом, склад) остаётся первым. Длина не растёт."""
    n = len(t)
    if n < 4:
        return t
    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            a = t[i - 1]
            da = d[a]
            for j in range(i + 1, n):
                b = t[i]
                c = t[j]
                e = t[j + 1] if j + 1 < n else t[0]
                if da[c] + d[b][e] - da[b] - d[c][e] < -_TWO_OPT_EPS:
                    t[i:j + 1] = t[i:j + 1][::-1]
                    improved = True
    return t


def nn_tour(nodes: Iterable[int], d: Matrix) -> list[int]:
    """Nearest-neighbor от вершины 0 (ничьи — по номеру вершины) + 2-opt — как на этапе 1."""
    left = set(nodes)
    tour = [0]
    cur = 0
    while left:
        row = d[cur]
        cur = min(left, key=lambda j: (row[j], j))
        tour.append(cur)
        left.remove(cur)
    return two_opt(tour, d)


def _removal(t: list[int], x: int, d: Matrix) -> tuple[int, float, int, int]:
    """(позиция x, Δ длины при удалении, сосед слева, сосед справа)."""
    pos = t.index(x)
    a = t[pos - 1]
    b = t[pos + 1] if pos + 1 < len(t) else t[0]
    da = d[a]
    return pos, da[b] - da[x] - d[x][b], a, b


def _insertion(t: Sequence[int], x: int, d: Matrix) -> tuple[int, float, int, int]:
    """Самая дешёвая вставка x: (позиция в списке, Δ длины, сосед слева, сосед справа)."""
    row = d[x]
    n = len(t)
    a = t[0]
    best, best_pos, best_a, best_b = math.inf, n, a, a
    for pos in range(1, n):
        b = t[pos]
        delta = row[a] + row[b] - d[a][b]
        if delta < best:
            best, best_pos, best_a, best_b = delta, pos, a, b
        a = b
    b = t[0]
    delta = row[a] + row[b] - d[a][b]
    if delta < best:
        best, best_pos, best_a, best_b = delta, n, a, b
    return best_pos, best, best_a, best_b


def nn_multi(nodes: Iterable[int], d: Matrix) -> list[int]:
    """Nearest-neighbor от вершины 0 по мультимножеству вершин (одна точка может встретиться
    дважды: клиента в этот день посещают два менеджера) + 2-opt; ничьи — по номеру вершины."""
    left = Counter(nodes)
    tour = [0]
    cur = 0
    while left:
        row = d[cur]
        cur = min(left, key=lambda j: (row[j], j))
        tour.append(cur)
        left[cur] -= 1
        if not left[cur]:
            del left[cur]
    return two_opt(tour, d)


# --- Парк машин: быстрая оценка (fleet-plan §3) ---

class FleetEstimate:
    """Дизель парка для поиска — общий для всех менеджеров расчёта.

    На слот визита j (заказы везут на следующий рабочий день) и сценарий s — гигантский тур «склад →
    точки всех визитов дня, где клиент заказал (флаг сценария) → склад»: удаление и самая дешёвая
    вставка — как у тура менеджера. Км парка — тур, нарезанный по ходу объезда по тоннажу самой большой
    машины (route-first, cluster-second): новый рейс, когда следующий заказ переполнит машину или рейс не
    уложится в рабочий день машины (как в точной оценке); заказ тяжелее машины — отдельными рейсами. Литры — км × средний расход парка. Машино-минуты (езда +
    разгрузка) сверх рабочего дня всех машин — штраф как за переработку менеджера. Стоимость дня —
    драм в неделю (среднее по сценариям / W). Тур — на день доставки: заказы сб и вс одной недели везут
    вместе в пн, поэтому у слота вс тот же тур, что у сб (group).

    Вершины — клиенты расчёта с координатами (0 — склад); kg — кг заказа клиента. static — визиты
    менеджеров вне поиска (их дни не меняются). Оценки eval запоминаются до изменения тура (memo):
    поиск много раз проверяет одни и те же визиты, а туры меняются только принятыми ходами.
    """

    def __init__(self, km: Matrix, city: Sequence[bool], kg: Sequence[float], *, capacity_kg: float,
                 per_km: float, overtime_per_min: float, capacity_min: float, unload_stop: float,
                 unload_tonne: float, speed_city_kmh: float, speed_region_kmh: float, trip_minutes: float):
        self.d = km
        self.city = list(city)
        self.kg = list(kg)
        self.unload = [0.0] + [unload_stop + unload_tonne * w / 1000.0 for w in self.kg[1:]]
        self.cap = capacity_kg
        self.per_km = per_km
        self.ot = overtime_per_min
        self.capacity_min = capacity_min
        self.trip_minutes = trip_minutes   # рейс не длиннее рабочего дня машины
        self.fc = 60.0 / speed_city_kmh
        self.fr = 60.0 / speed_region_kmh
        self.static: list[tuple[int, int, tuple[bool, ...]]] = []
        # слот визита → тур дня доставки: вс — к сб той же недели (оба везут в пн); туры вс пустые
        self.group = [j - 1 if day_of_slot(j)[1] == 7 else j for j in range(SLOTS)]
        self.tours: list[list[list[int]]] = [[[0] for _ in range(TRUCK_SCENARIOS)] for _ in range(SLOTS)]
        self.km = [[0.0] * TRUCK_SCENARIOS for _ in range(SLOTS)]
        self.mins = [[0.0] * TRUCK_SCENARIOS for _ in range(SLOTS)]
        self.cost = [0.0] * SLOTS
        self.total = 0.0
        self.dirty: set[int] = set()
        self.memo: list[list[dict]] = [[{} for _ in range(TRUCK_SCENARIOS)] for _ in range(SLOTS)]
        self.pre: list[list[tuple | None]] = [[None] * TRUCK_SCENARIOS for _ in range(SLOTS)]   # _walk тура

    # -- состав и пересчёт --

    def build(self, entries: Iterable[tuple[int, int, Sequence[bool]]],
              static: Iterable[tuple[int, int, Sequence[bool]]] = ()) -> None:
        """Туры с нуля (NN + 2-opt) по визитам: (слот, вершина, флаги сценариев). static — визиты
        менеджеров вне поиска (входят в туры и в проверку согласованности)."""
        self.static = [(j, g, tuple(f)) for j, g, f in static]
        members = self._members(list(entries) + self.static)
        for j in range(SLOTS):
            for s in range(TRUCK_SCENARIOS):
                self.tours[j][s] = nn_multi(sorted(members[(j, s)].elements()), self.d)
            self._refresh(j)
        self.dirty.clear()
        self.total = sum(self.cost)

    def _members(self, entries: Iterable[tuple[int, int, Sequence[bool]]]) -> dict[tuple[int, int], Counter]:
        """Состав туров: (тур дня доставки, сценарий) → вершины; entries — (слот визита, вершина, флаги)."""
        out: dict[tuple[int, int], Counter] = {(j, s): Counter() for j in range(SLOTS)
                                               for s in range(TRUCK_SCENARIOS)}
        for j, g, flags in entries:
            if g:
                for s in range(TRUCK_SCENARIOS):
                    if flags[s]:
                        out[(self.group[j], s)][g] += 1
        return out

    def adopt(self, other: FleetEstimate) -> None:
        """Туры с тем же составом — из другого парка с той же нумерацией вершин (режим Б начинается
        с туров итога режима А); остальные остаются построенными заново."""
        for j in range(SLOTS):
            for s in range(TRUCK_SCENARIOS):
                if Counter(other.tours[j][s][1:]) == Counter(self.tours[j][s][1:]):
                    self.tours[j][s] = list(other.tours[j][s])
            self._refresh(j)
        self.total = sum(self.cost)

    def _walk(self, t: Sequence[int]) -> tuple:
        """Нарезка тура t (t[0] — склад) по ходу объезда с записью состояний: новый рейс, когда следующий
        заказ переполнит машину или рейс не уложится в рабочий день машины (езда + разгрузка + обратно на
        склад, как в точной оценке). Перед позицией k — (prev, load, минуты рейса, км, минуты) в P, L, T,
        K, M; если на k начинается рейс (после закрытия прежнего) — км и минуты в этот момент в SK, SM
        (иначе None); в конце — км и минуты всего тура."""
        d, kg, un, city = self.d, self.kg, self.unload, self.city
        cap, fc, fr, window = self.cap, self.fc, self.fr, self.trip_minutes
        d0, c0 = d[0], city[0]
        n = len(t)
        P, L, T = [0] * (n + 1), [0.0] * (n + 1), [0.0] * (n + 1)
        K, M = [0.0] * (n + 1), [0.0] * (n + 1)
        SK: list[float | None] = [None] * (n + 1)
        SM: list[float | None] = [None] * (n + 1)
        km = mins = load = trip = 0.0
        prev = 0
        for k in range(1, n):
            x = t[k]
            P[k], L[k], T[k], K[k], M[k] = prev, load, trip, km, mins
            w = kg[x]
            heavy = w > cap
            if prev:
                cx = city[x]
                go = d[prev][x] * (fc if city[prev] and cx else fr) + un[x]
                if heavy or load + w > cap or trip + go + d0[x] * (fc if c0 and cx else fr) > window:
                    e = d[prev][0]                          # закрыть текущий рейс
                    km += e
                    mins += e * (fc if city[prev] and c0 else fr)
                    prev, load, trip = 0, 0.0, 0.0
            if not prev:                                    # здесь начинается рейс
                SK[k], SM[k] = km, mins
            if heavy:                                       # тяжелее машины — отдельными рейсами
                e = d0[x] * 2.0 * math.ceil(w / cap)
                km += e
                mins += e * (fc if c0 and city[x] else fr) + un[x]
                continue
            e = d[prev][x]
            go = e * (fc if city[prev] and city[x] else fr) + un[x]
            km += e
            mins += go
            trip += go
            load += w
            prev = x
        P[n], L[n], T[n], K[n], M[n] = prev, load, trip, km, mins
        if prev:
            e = d[prev][0]
            km += e
            mins += e * (fc if city[prev] and c0 else fr)
        return P, L, T, K, M, SK, SM, km, mins

    def split(self, t: Sequence[int]) -> tuple[float, float]:
        """(км, машино-минуты) тура t (t[0] — склад), нарезанного на рейсы по ходу объезда (_walk)."""
        return self._walk(t)[7:]

    def _resume(self, new: Sequence[int], k0: int, pre: tuple, limit: int, delta: int) -> tuple[float, float]:
        """Нарезка нового тура new с позиции k0, где он впервые отличается от текущего: состояние перед
        k0 — из записи текущего тура pre (_walk). Начиная с limit тур new совпадает с текущим со сдвигом
        delta: как только рейс начинается там же, где у текущего тура, остаток км и минут — его."""
        d, kg, un, city = self.d, self.kg, self.unload, self.city
        cap, fc, fr, window = self.cap, self.fc, self.fr, self.trip_minutes
        d0, c0 = d[0], city[0]
        P, L, T, K, M, SK, SM, TK, TM = pre
        prev, load, trip, km, mins = P[k0], L[k0], T[k0], K[k0], M[k0]
        for k in range(k0, len(new)):
            x = new[k]
            w = kg[x]
            heavy = w > cap
            if prev:
                cx = city[x]
                go = d[prev][x] * (fc if city[prev] and cx else fr) + un[x]
                if heavy or load + w > cap or trip + go + d0[x] * (fc if c0 and cx else fr) > window:
                    e = d[prev][0]
                    km += e
                    mins += e * (fc if city[prev] and c0 else fr)
                    prev, load, trip = 0, 0.0, 0.0
            if not prev and k >= limit:
                q = k + delta
                sk = SK[q]
                if sk is not None:                          # рейс начинается там же — дальше всё как было
                    return km + (TK - sk), mins + (TM - SM[q])
            if heavy:
                e = d0[x] * 2.0 * math.ceil(w / cap)
                km += e
                mins += e * (fc if c0 and city[x] else fr) + un[x]
                continue
            e = d[prev][x]
            go = e * (fc if city[prev] and city[x] else fr) + un[x]
            km += e
            mins += go
            trip += go
            load += w
            prev = x
        if prev:
            e = d[prev][0]
            km += e
            mins += e * (fc if city[prev] and c0 else fr)
        return km, mins

    def cost_of(self, km: Sequence[float], mins: Sequence[float]) -> float:
        """Стоимость дня парка, драм в неделю: дизель + машино-минуты сверх рабочего дня всех машин."""
        over = sum(max(0.0, m - self.capacity_min) for m in mins)
        return (self.per_km * sum(km) + self.ot * over) / TRUCK_SCENARIOS / W

    def _refresh(self, j: int) -> None:
        for s in range(TRUCK_SCENARIOS):
            pre = self.pre[j][s] = self._walk(self.tours[j][s])
            self.km[j][s], self.mins[j][s] = pre[7], pre[8]
            self.memo[j][s].clear()
        self.cost[j] = self.cost_of(self.km[j], self.mins[j])

    # -- оценка и применение --

    def eval(self, j: int, s: int, rem: int, ins: int) -> tuple[int, int, float, float]:
        """Удалить вершину rem и/или вставить ins (0 — нет) в тур (j, s): (позиция удаления, позиция
        вставки — в туре после удаления, км, минуты). Тур не меняется."""
        memo = self.memo[j][s]
        hit = memo.get((rem, ins))
        if hit is not None:
            return hit
        t = self.tours[j][s]
        r_pos = i_pos = -1
        base = t
        k0 = limit = len(t) + 1
        if rem:
            r_pos = t.index(rem)
            base = t[:r_pos] + t[r_pos + 1:]
            k0 = limit = r_pos
        new = base
        if ins:
            i_pos = _insertion(base, ins, self.d)[0]
            new = base[:i_pos] + [ins] + base[i_pos:]
            k0 = min(k0, i_pos)
            limit = max(i_pos, limit) + 1 if rem else i_pos + 1
        pre = self.pre[j][s]
        if pre is None:
            pre = self.pre[j][s] = self._walk(t)
        km, mins = self._resume(new, k0, pre, limit, (1 if rem else 0) - (1 if ins else 0))
        hit = memo[(rem, ins)] = (r_pos, i_pos, km, mins)
        return hit

    def apply(self, j: int, ops: Sequence[tuple], cost: float) -> tuple:
        """ops — (s, позиция удаления, позиция вставки, rem, ins, км, минуты) из eval; возвращает
        запись для revert."""
        undo = []
        for s, r_pos, i_pos, rem, ins, km, mins in ops:
            t = self.tours[j][s]
            if r_pos >= 0:
                del t[r_pos]
            if i_pos >= 0:
                t.insert(i_pos, ins)
            undo.append((s, r_pos, i_pos, rem, ins, self.km[j][s], self.mins[j][s]))
            self.km[j][s], self.mins[j][s] = km, mins
            self.memo[j][s].clear()
            self.pre[j][s] = None
        record = (j, undo, self.cost[j])
        self.cost[j] = cost
        self.total = sum(self.cost)
        self.dirty.add(j)
        return record

    def revert(self, record: tuple) -> None:
        j, undo, cost = record
        for s, r_pos, i_pos, rem, ins, km, mins in reversed(undo):
            t = self.tours[j][s]
            if i_pos >= 0:
                del t[i_pos]
            if r_pos >= 0:
                t.insert(r_pos, rem)
            self.km[j][s], self.mins[j][s] = km, mins
            self.memo[j][s].clear()
            self.pre[j][s] = None
        self.cost[j] = cost
        self.total = sum(self.cost)

    def effect_km(self, removals: Iterable[tuple[int, int, Sequence[bool]]],
                  insertions: Iterable[tuple[int, int, Sequence[bool]]]) -> float:
        """Δ км парка в неделю, если визиты removals (слот визита, вершина, флаги) уходят, а insertions
        приходят. Изменения применяются по очереди и откатываются: туры остаются как были."""
        ops: dict[tuple[int, int], tuple[list[int], list[int]]] = {}
        for side, items in ((0, removals), (1, insertions)):
            for j, g, flags in items:
                if g:
                    for s in range(TRUCK_SCENARIOS):
                        if flags[s]:
                            ops.setdefault((self.group[j], s), ([], []))[side].append(g)
        before, dirty = sum(map(sum, self.km)), set(self.dirty)
        records = []
        for (jj, s), (rems, inss) in sorted(ops.items()):
            rems, inss = list(rems), list(inss)
            for g in [g for g in rems if g in inss]:          # та же точка уходит и приходит — тур тот же
                rems.remove(g)
                inss.remove(g)
            for k in range(max(len(rems), len(inss))):
                rem = rems[k] if k < len(rems) else 0
                ins = inss[k] if k < len(inss) else 0
                r_pos, i_pos, km, mins = self.eval(jj, s, rem, ins)
                records.append(self.apply(jj, [(s, r_pos, i_pos, rem, ins, km, mins)], self.cost[jj]))
        after = sum(map(sum, self.km))
        for record in reversed(records):
            self.revert(record)
        self.dirty = dirty
        return (after - before) / TRUCK_SCENARIOS / W

    # -- доводка, проверка, снимки --

    def polish(self, days: Iterable[int] | None = None) -> None:
        """2-opt туров изменённых дней (или указанных) и пересчёт их км и стоимости."""
        todo = sorted(self.dirty) if days is None else sorted(set(days))
        for j in todo:
            for t in self.tours[j]:
                two_opt(t, self.d)
            self._refresh(j)
        self.dirty.clear()
        self.total = sum(self.cost)

    def consistency_error(self, entries: Iterable[tuple[int, int, Sequence[bool]]]) -> float:
        """Наибольшее относительное расхождение хранимых км, минут и стоимости с расчётом по текущим
        турам; inf — состав туров не совпадает с визитами entries (+ static)."""
        members = self._members(list(entries) + self.static)
        worst = 0.0
        for j in range(SLOTS):
            for s in range(TRUCK_SCENARIOS):
                t = self.tours[j][s]
                if t[0] != 0 or Counter(t[1:]) != members[(j, s)]:
                    return math.inf
                km, mins = self.split(t)
                worst = max(worst, abs(km - self.km[j][s]) / max(1.0, km),
                            abs(mins - self.mins[j][s]) / max(1.0, mins))
            want = self.cost_of(self.km[j], self.mins[j])
            worst = max(worst, abs(want - self.cost[j]) / max(1.0, abs(want)))
        return max(worst, abs(sum(self.cost) - self.total) / max(1.0, abs(self.total)))

    def snapshot(self) -> tuple:
        return ([[list(t) for t in day] for day in self.tours], [list(x) for x in self.km],
                [list(x) for x in self.mins], list(self.cost), self.total, set(self.dirty))

    def restore(self, snap: tuple) -> None:
        tours, km, mins, cost, total, dirty = snap
        self.tours = [[list(t) for t in day] for day in tours]
        self.km = [list(x) for x in km]
        self.mins = [list(x) for x in mins]
        self.cost = list(cost)
        self.total = total
        self.dirty = set(dirty)
        for day in self.memo:
            for m in day:
                m.clear()
        self.pre = [[None] * TRUCK_SCENARIOS for _ in range(SLOTS)]


# --- Состояние ---

_AGG = ('km', 'drv', 'vm', 'mu', 'var', 'sure', 'logq', 'n', 'cost')


class State:
    """Решение менеджера: шаблоны клиентов, туры и агрегаты дней, стоимость C (драм/нед).

    fleet — общий парк (FleetEstimate), в котором уже есть визиты этого состояния (fleet_entries);
    total = свои слагаемые (own_total) + дизель всего парка."""

    def __init__(self, prob: Problem, patterns: Sequence[SlotPattern],
                 params: Sequence[VisitParams], *, change_penalty: float,
                 fleet: FleetEstimate | None = None):
        self.prob = prob
        self.w = prob.weights
        self.lines = prob.lines
        self.params = list(params)
        self.change_penalty = change_penalty
        self.fleet = fleet
        n = len(prob.lines)
        self.flags = [[tuple(u < self.params[i].p_year for u in prob.lines[i].u[k])
                       for k in range(MAX_VISITS)] for i in range(n)]
        self.pattern: list[SlotPattern] = [tuple(p) for p in patterns]
        self.slot_of: list[list[int]] = [list(p) for p in self.pattern]
        self.moved = [is_moved(prob.lines[i].current, self.pattern[i]) for i in range(n)]
        self.n_moved = sum(self.moved)
        self.members: list[set[int]] = [set() for _ in range(SLOTS)]
        for i, p in enumerate(self.pattern):
            for j in p:
                self.members[j].add(i)
        for name in _AGG:
            setattr(self, name, [0.0] * SLOTS)
        self.tour: list[list[int]] = [[0] for _ in range(SLOTS)]
        self.dirty: set[int] = set()
        for j in range(SLOTS):
            self._rebuild_tours(j)
            self._set_exact(j)
        self.total = self._total()

    def attach(self, fleet: FleetEstimate | None) -> None:
        """Подключить парк, в котором уже есть визиты этого состояния (fleet_entries)."""
        self.fleet = fleet
        self.total = self._total()

    def fleet_entries(self) -> list[tuple[int, int, tuple[bool, ...]]]:
        """Визиты состояния для парка: (слот, вершина парка, флаги «заказал» по сценариям)."""
        return [(j, self.lines[i].gnode, self.flags[i][k])
                for i, slots in enumerate(self.slot_of) if self.lines[i].gnode
                for k, j in enumerate(slots)]

    # -- точный расчёт дня --

    def _rebuild_tours(self, j: int) -> None:
        """Тур дня с нуля: NN + 2-opt (как на этапе 1)."""
        lines = self.lines
        mem = sorted(self.members[j])
        self.tour[j] = nn_tour((lines[i].node for i in mem if lines[i].node), self.prob.km)

    def exact_values(self, j: int) -> tuple[float, ...]:
        """Агрегаты дня с нуля по текущему туру и составу (порядок — как _AGG, без cost)."""
        prob = self.prob
        km = tour_length(self.tour[j], prob.km)
        drv = tour_length(self.tour[j], prob.mins)
        vm = mu = var = logq = 0.0
        sure = n = 0
        for i in sorted(self.members[j]):
            pr = self.params[i]
            vm += self.lines[i].minutes
            mu += pr.mu
            var += pr.var
            n += 1
            if pr.sure:
                sure += 1
            else:
                logq += pr.log_q
        return km, drv, vm, mu, var, sure, logq, n

    def _set_exact(self, j: int) -> None:
        vals = self.exact_values(j)
        for name, v in zip(_AGG, (*vals, self._cost(j, *vals))):
            getattr(self, name)[j] = v

    def _cost(self, j: int, km: float, drv: float, vm: float, mu: float, var: float, sure: int,
              logq: float, n: int) -> float:
        """Стоимость дня менеджера, драм в неделю (§4); дизель парка — в FleetEstimate."""
        w = self.w
        c = w.manager_per_km * km
        over = vm + drv - w.window_min
        if over > 0.0:
            c += w.overtime_per_min * over
        if w.weak_day and self.prob.workday[j] and (n > 0 or self.prob.base[j]):
            c += w.weak_day * (1.0 - p_at_least(mu, var, w.min_day_revenue))
        if w.poor_trip and n > 0:
            c += w.poor_trip * p_poor(mu, var, sure, logq, w.min_trip_revenue)
        return c / W

    def own_total(self) -> float:
        """Свои слагаемые менеджера (без парка) + штраф за переносы."""
        return sum(self.cost) + self.change_penalty * self.n_moved

    def _total(self) -> float:
        return self.own_total() + (self.fleet.total if self.fleet is not None else 0.0)

    def refresh(self) -> None:
        """Пересчитать total: парк общий, его могли изменить другие менеджеры."""
        self.total = self._total()

    def day_metrics(self, j: int) -> dict[str, float]:
        """Метрики дня для отчёта: км, минуты, ожидаемый «слабый» день."""
        w = self.w
        counted = self.prob.workday[j] and (self.n[j] > 0 or self.prob.base[j])
        return {
            'km': self.km[j],
            'minutes': self.vm[j] + self.drv[j],
            'weak': (1.0 - p_at_least(self.mu[j], self.var[j], w.min_day_revenue)) if counted else 0.0,
        }

    # -- оценка изменения одного дня --

    def _eval_day(self, j: int, out_i: int, out_k: int, in_i: int, in_k: int) -> tuple[float, tuple]:
        """Δ стоимости дня j (менеджер + парк), если из него уходит визит (out_i, out_k) и/или
        приходит (in_i, in_k); -1 — нет. Туры не меняются: возвращается план применения. Парк — тур дня
        доставки этого слота (fleet.group[j])."""
        prob = self.prob
        lines = self.lines
        kd, md = prob.km, prob.mins
        km, drv, vm, mu, var = self.km[j], self.drv[j], self.vm[j], self.mu[j], self.var[j]
        sure, logq, n = self.sure[j], self.logq[j], self.n[j]
        t = self.tour[j]
        m_rem = m_ins = -1
        if out_i >= 0:
            pr = self.params[out_i]
            vm -= lines[out_i].minutes
            mu -= pr.mu
            var -= pr.var
            n -= 1
            if pr.sure:
                sure -= 1
            else:
                logq -= pr.log_q
            x = lines[out_i].node
            if x:
                m_rem, dk, a, b = _removal(t, x, kd)
                km += dk
                drv += md[a][b] - md[a][x] - md[x][b]
        if in_i >= 0:
            pr = self.params[in_i]
            vm += lines[in_i].minutes
            mu += pr.mu
            var += pr.var
            n += 1
            if pr.sure:
                sure += 1
            else:
                logq += pr.log_q
            y = lines[in_i].node
            if y:
                base = t if m_rem < 0 else t[:m_rem] + t[m_rem + 1:]
                m_ins, dk, a, b = _insertion(base, y, kd)
                km += dk
                drv += md[a][y] + md[y][b] - md[a][b]
        # опустевший тур или день — точные нули: погрешность сложений не накапливается
        if m_rem >= 0 and m_ins < 0 and len(t) == 2:
            km = drv = 0.0
        if n == 0:
            vm = mu = var = logq = 0.0
        cost = self._cost(j, km, drv, vm, mu, var, sure, logq, n)
        delta = cost - self.cost[j]
        fops = None
        fcost = 0.0
        fleet = self.fleet
        gx = lines[out_i].gnode if out_i >= 0 else 0
        gy = lines[in_i].gnode if in_i >= 0 else 0
        fj = j
        if fleet is not None and (gx or gy):
            fj = fleet.group[j]
            kms, mins = list(fleet.km[fj]), list(fleet.mins[fj])
            for s in range(TRUCK_SCENARIOS):
                rem = gx if gx and self.flags[out_i][out_k][s] else 0
                ins = gy if gy and self.flags[in_i][in_k][s] else 0
                if rem == ins:      # оба нет — или та же точка уходит и приходит: тур тот же
                    continue
                r_pos, i_pos, kms[s], mins[s] = fleet.eval(fj, s, rem, ins)
                if fops is None:
                    fops = []
                fops.append((s, r_pos, i_pos, rem, ins, kms[s], mins[s]))
            if fops:
                fcost = fleet.cost_of(kms, mins)
                delta += fcost - fleet.cost[fj]
        plan = (j, out_i, in_i, m_rem, m_ins, (km, drv, vm, mu, var, sure, logq, n, cost), fops, fcost, fj)
        return delta, plan

    def _eval_days(self, items: Sequence[tuple], cache: dict | None = None) -> tuple[float, list[tuple]]:
        """Δ и планы дней хода: items — (ключ кэша или None, день, out_i, out_k, in_i, in_k). Дни с общим
        туром парка (сб и вс одной недели) оцениваются по очереди: следующий — по парку после
        предыдущего (временно применён и откатан), без кэша."""
        fleet = self.fleet
        shared: set[int] = set()
        if fleet is not None and len(items) > 1:
            seen: set[int] = set()
            for item in items:
                g = fleet.group[item[1]]
                (shared if g in seen else seen).add(g)
        delta = 0.0
        plans = []
        records = []
        for key, j, out_i, out_k, in_i, in_k in items:
            together = bool(shared) and fleet.group[j] in shared
            hit = cache.get(key) if cache is not None and key is not None and not together else None
            if hit is None:
                hit = self._eval_day(j, out_i, out_k, in_i, in_k)
                if cache is not None and key is not None and not together:
                    cache[key] = hit
            delta += hit[0]
            plans.append(hit[1])
            if together and hit[1][6]:
                records.append(fleet.apply(hit[1][8], hit[1][6], hit[1][7]))
        for record in reversed(records):
            fleet.revert(record)
        return delta, plans

    def _apply_day(self, plan: tuple) -> None:
        j, out_i, in_i, m_rem, m_ins, vals, fops, fcost, fj = plan
        t = self.tour[j]
        if m_rem >= 0:
            del t[m_rem]
        if m_ins >= 0:
            t.insert(m_ins, self.lines[in_i].node)
        if out_i >= 0:
            self.members[j].discard(out_i)
        if in_i >= 0:
            self.members[j].add(in_i)
        for name, v in zip(_AGG, vals):
            getattr(self, name)[j] = v
        if fops:
            self.fleet.apply(fj, fops, fcost)
        self.dirty.add(j)

    def fleet_push(self, move: tuple) -> list[tuple]:
        """Временно применить к парку изменения хода (режим Б: ход второго менеджера оценивается по
        парку после хода первого); вернуть записи для fleet_pop."""
        if self.fleet is None:
            return []
        return [self.fleet.apply(plan[8], plan[6], plan[7]) for plan in move[1] if plan[6]]

    def fleet_pop(self, records: Sequence[tuple]) -> None:
        for record in reversed(records):
            self.fleet.revert(record)

    # -- ходы --

    def eval_relocate(self, i: int, new: SlotPattern,
                      cache: dict | None = None) -> tuple[float, tuple]:
        """Δ C при смене шаблона клиента i на new (того же размера или из пустого).
        Визиты на общих днях остаются на месте со своими флагами; ушедшие визиты (по k) по порядку
        переходят на новые дни. cache — кэш оценок дней в пределах одного обхода клиента."""
        new_set = set(new)
        kept = [k for k, j in enumerate(self.slot_of[i]) if j in new_set]
        gone = [(k, j) for k, j in enumerate(self.slot_of[i]) if j not in new_set]
        old_set = set(self.pattern[i])
        added = [j for j in new if j not in old_set]
        free = sorted(set(range(len(new))) - set(kept))
        items = [(('out', j), j, i, k, -1, -1) for k, j in gone]
        items += [(('in', j, k if self.fleet is not None else 0), j, -1, -1, i, k) for k, j in zip(free, added)]
        delta, plans = self._eval_days(items, cache)
        new_slot_of = [0] * len(new)
        for k in kept:
            new_slot_of[k] = self.slot_of[i][k]
        for k, j in zip(free, added):
            new_slot_of[k] = j
        moved = is_moved(self.lines[i].current, new)
        delta += self.change_penalty * (moved - self.moved[i])
        return delta, (((i, tuple(new), new_slot_of, moved),), tuple(plans))

    def eval_swap(self, a: int, b: int) -> tuple[float, tuple]:
        """Δ C при обмене шаблонами клиентов a и b одной частоты. На каждом затронутом дне один
        визит уходит и один приходит; на общих днях оба остаются на месте."""
        pa, pb = self.pattern[a], self.pattern[b]
        sa, sb = set(pa), set(pb)
        a_gone = [(k, j) for k, j in enumerate(self.slot_of[a]) if j not in sb]
        b_gone = [(k, j) for k, j in enumerate(self.slot_of[b]) if j not in sa]
        a_added = [j for j in pb if j not in sa]
        b_added = [j for j in pa if j not in sb]
        a_new_k = dict(zip(a_added, sorted(k for k, _ in a_gone)))   # новый день a → номер визита
        b_new_k = dict(zip(b_added, sorted(k for k, _ in b_gone)))
        items = [(None, j, a, k, b, b_new_k[j]) for k, j in a_gone]          # a уходит, b приходит
        items += [(None, j, b, k, a, a_new_k[j]) for k, j in b_gone]         # b уходит, a приходит
        delta, plans = self._eval_days(items)
        a_slot_of = list(self.slot_of[a])
        for j, k in a_new_k.items():
            a_slot_of[k] = j
        b_slot_of = list(self.slot_of[b])
        for j, k in b_new_k.items():
            b_slot_of[k] = j
        moved_a = is_moved(self.lines[a].current, pb)
        moved_b = is_moved(self.lines[b].current, pa)
        delta += self.change_penalty * ((moved_a - self.moved[a]) + (moved_b - self.moved[b]))
        return delta, (((a, pb, a_slot_of, moved_a), (b, pa, b_slot_of, moved_b)), tuple(plans))

    def eval_exchange(self, out_i: int, in_i: int) -> tuple[float, tuple]:
        """Δ C, если клиент in_i (сейчас без визитов у этого менеджера) занимает ровно дни клиента
        out_i, а out_i остаётся без визитов (этап 4, обмен клиентами между менеджерами). На каждом
        дне один визит уходит и один приходит; k-й визит in_i — на дне k-го визита out_i."""
        slots = list(self.slot_of[out_i])
        delta, plans = self._eval_days([(None, j, out_i, k, in_i, k) for k, j in enumerate(slots)])
        new = self.pattern[out_i]
        moved_out = is_moved(self.lines[out_i].current, ())
        moved_in = is_moved(self.lines[in_i].current, new)
        delta += self.change_penalty * ((moved_out - self.moved[out_i]) + (moved_in - self.moved[in_i]))
        return delta, (((out_i, (), [], moved_out), (in_i, new, slots, moved_in)), tuple(plans))

    def apply(self, move: tuple) -> None:
        updates, plans = move
        for plan in plans:
            self._apply_day(plan)
        for i, new, slot_of, moved in updates:
            self.pattern[i] = new
            self.slot_of[i] = list(slot_of)
            self.n_moved += int(moved) - int(self.moved[i])
            self.moved[i] = moved
        self.total = self._total()

    def adopt_tours(self, other: State) -> None:
        """Туры дней с тем же составом — из другого состояния с той же нумерацией строк и вершин
        (режим Б начинается с туров итога режима А: старт стоит ровно столько же, сколько итог А).
        У строк с тем же шаблоном берётся и порядок визитов (k → день): от него флаги «заказал»
        для парка (fleet_entries — после adopt_tours); дни с другим составом строятся заново."""
        same = [i < len(other.lines) and self.pattern[i] == other.pattern[i]
                and self.params[i] == other.params[i] for i in range(len(self.lines))]
        for i, ok in enumerate(same):
            if ok:
                self.slot_of[i] = list(other.slot_of[i])
        for j in range(SLOTS):
            if self.members[j] == other.members[j] and all(same[i] for i in self.members[j]):
                self.tour[j] = list(other.tour[j])
            else:
                self._rebuild_tours(j)
            self._set_exact(j)
        self.total = self._total()

    # -- доводка, проверка, снимки --

    def polish(self, days: Iterable[int] | None = None) -> None:
        """2-opt туров менеджера изменённых дней (или указанных) и точный пересчёт их агрегатов.
        Туры парка — FleetEstimate.polish (после поиска менеджера: 2-opt гигантских туров дорог)."""
        todo = sorted(self.dirty) if days is None else sorted(set(days))
        for j in todo:
            two_opt(self.tour[j], self.prob.km)
            self._set_exact(j)
        self.dirty.clear()
        self.total = self._total()

    def consistency_error(self) -> float:
        """Наибольшее относительное расхождение инкрементальных агрегатов с расчётом с нуля
        (inf — туры не совпадают с составом дня). Для тестов §5. Парк — FleetEstimate.consistency_error."""
        worst = 0.0
        lines = self.lines
        for j in range(SLOTS):
            mem = self.members[j]
            nodes = sorted(lines[i].node for i in mem if lines[i].node)
            if sorted(self.tour[j][1:]) != nodes or self.tour[j][0] != 0:
                return math.inf
            exact = self.exact_values(j)
            stored = (self.km[j], self.drv[j], self.vm[j], self.mu[j], self.var[j], self.sure[j],
                      self.logq[j], self.n[j])
            for got, want in zip(stored, exact):
                worst = max(worst, abs(got - want) / max(1.0, abs(want)))
            want = self._cost(j, *exact)
            worst = max(worst, abs(self.cost[j] - want) / max(1.0, abs(want)))
        for i, p in enumerate(self.pattern):
            if sorted(self.slot_of[i]) != list(p):
                return math.inf
        return worst

    def snapshot(self, with_fleet: bool = True) -> tuple:
        """Снимок решения; with_fleet=False — без парка (его снимает тот, кто ведёт всех менеджеров)."""
        return (list(self.pattern), [list(x) for x in self.slot_of], list(self.moved), self.n_moved,
                [set(m) for m in self.members], [list(t) for t in self.tour],
                {name: list(getattr(self, name)) for name in _AGG}, set(self.dirty),
                self.fleet.snapshot() if with_fleet and self.fleet is not None else None)

    def restore(self, snap: tuple) -> None:
        (pattern, slot_of, moved, n_moved, members, tour, agg, dirty, fleet) = snap
        self.pattern = list(pattern)
        self.slot_of = [list(x) for x in slot_of]
        self.moved = list(moved)
        self.n_moved = n_moved
        self.members = [set(m) for m in members]
        self.tour = [list(t) for t in tour]
        for name in _AGG:
            setattr(self, name, list(agg[name]))
        self.dirty = set(dirty)
        if fleet is not None:
            self.fleet.restore(fleet)
        self.total = self._total()


# --- Стартовые решения ---

def current_start(prob: Problem, base: Sequence[SlotPattern] | None = None) -> list[SlotPattern]:
    """Старт «current»: текущие шаблоны, приведённые к целевой частоте. base — от чего строится
    старт по клиентам (по умолчанию текущие шаблоны; в расчёте — текущие, где визиты нерабочих дней
    уже на субботе той же недели, Р3-9). Закреплённые и допустимые base — как есть; остальные —
    допустимый шаблон с наибольшим числом общих с base дней (1/нед → 0.5: тот же день; 2/нед → 1:
    день из пары), при равенстве — с меньшей нагрузкой дней (минуты визитов уже расставленных
    клиентов), затем первый по порядку."""
    refs = [line.current for line in prob.lines] if base is None else list(base)
    load = [0.0] * SLOTS
    out: list[SlotPattern] = [()] * len(prob.lines)
    pending = []
    for i, line in enumerate(prob.lines):
        if line.locked:
            out[i] = line.allowed[0]
        elif refs[i] in line.allowed:
            out[i] = refs[i]
        else:
            pending.append(i)
            continue
        for j in out[i]:
            load[j] += line.minutes
    for i in pending:
        line = prob.lines[i]
        cur = set(refs[i])
        best = min(line.allowed, key=lambda p: (-len(cur & set(p)), sum(load[j] for j in p), p))
        out[i] = best
        for j in best:
            load[j] += line.minutes
    return out


def fresh_order(prob: Problem, home: tuple[float, float] | None,
                points: Sequence[tuple[float, float] | None]) -> list[int]:
    """Порядок жадной вставки «fresh»: по убыванию частоты, затем по углу вокруг дома (без дома —
    вокруг центра точек); клиенты без координат — после, по id."""
    located = [p for p in points if p is not None]
    center = home if home is not None else (
        (sum(p[0] for p in located) / len(located), sum(p[1] for p in located) / len(located))
        if located else (0.0, 0.0))

    def key(i: int) -> tuple:
        line, p = prob.lines[i], points[i]
        size = len(line.allowed[0])
        if p is None:
            return (-size, 1, 0.0, line.customer_id)
        return (-size, 0, math.atan2(p[0] - center[0], p[1] - center[1]), line.customer_id)

    return sorted((i for i, line in enumerate(prob.lines) if not line.locked), key=key)


def greedy_fill(state: State, order: Sequence[int]) -> None:
    """Старт «fresh»: каждый клиент (ещё без шаблона) — в допустимый шаблон с минимальным приростом C."""
    for i in order:
        best = None
        for p in state.lines[i].allowed:
            delta, move = state.eval_relocate(i, p)
            if best is None or delta < best[0]:
                best = (delta, move)
        if best is not None:
            state.apply(best[1])


# --- Поиск ---

@dataclass
class SearchStats:
    seconds: float = 0.0
    time_capped: bool = False
    perturbations: int = 0
    accepted: int = 0
    cost_start: float = 0.0
    cost_end: float = 0.0


@dataclass
class _Ctx:
    deadline: float
    clock: Callable[[], float]
    stats: SearchStats
    movable: list[bool] = field(default_factory=list)
    allowed_set: list[frozenset] = field(default_factory=list)


def _improve(state: State, i: int, ctx: _Ctx) -> tuple[int, ...]:
    """Лучший relocate клиента i, иначе лучший обмен с ближайшими соседями; ход — при ΔC < −1."""
    line = state.lines[i]
    cur = state.pattern[i]
    cache: dict = {}
    best = None
    for p in line.allowed:
        if p == cur:
            continue
        delta, move = state.eval_relocate(i, p, cache)
        if best is None or delta < best[0]:
            best = (delta, move)
    if best is not None and best[0] < -MIN_GAIN:
        state.apply(best[1])
        return (i,)
    best = None
    partner = -1
    for b in state.prob.neighbors[i]:
        pb = state.pattern[b]
        if (not ctx.movable[b] or pb == cur or len(pb) != len(cur)
                or pb not in ctx.allowed_set[i] or cur not in ctx.allowed_set[b]):
            continue
        delta, move = state.eval_swap(i, b)
        if best is None or delta < best[0]:
            best, partner = (delta, move), b
    if best is not None and best[0] < -MIN_GAIN:
        state.apply(best[1])
        return (i, partner)
    return ()


def _descent(state: State, seeds: Iterable[int], ctx: _Ctx) -> bool:
    """Локальный поиск от очереди seeds; клиенты затронутых ходов и их соседи — снова в очередь."""
    n = len(state.lines)
    queue: deque[int] = deque()
    queued = bytearray(n)
    for i in seeds:
        if ctx.movable[i] and not queued[i]:
            queue.append(i)
            queued[i] = 1
    improved = False
    steps = 0
    neighbors = state.prob.neighbors
    while queue:
        steps += 1
        if not steps % 16 and ctx.clock() >= ctx.deadline:
            ctx.stats.time_capped = True
            return improved
        i = queue.popleft()
        queued[i] = 0
        touched = _improve(state, i, ctx)
        if not touched:
            continue
        improved = True
        ctx.stats.accepted += 1
        if not ctx.stats.accepted % TWO_OPT_EVERY:
            state.polish()
        for t in touched:
            for x in (t, *neighbors[t]):
                if ctx.movable[x] and not queued[x]:
                    queue.append(x)
                    queued[x] = 1
    return improved


def _full_descent(state: State, order: Sequence[int], ctx: _Ctx) -> None:
    """Проходы по всем клиентам (порядок seed), пока проход находит улучшения."""
    while _descent(state, order, ctx) and not ctx.stats.time_capped:
        pass


def search(state: State, *, seconds: float, clock: Callable[[], float] = time.perf_counter,
           max_perturbations: int = MAX_PERTURBATIONS) -> SearchStats:
    """Локальный поиск (relocate + swap) и ILS (§6). Детерминизм: порядок и возмущения — от seed
    менеджера, число возмущений ограничено счётчиком; время — только страховочный стоп
    (time_capped — повторяемость не гарантируется)."""
    started = clock()
    stats = SearchStats(cost_start=state.total)
    lines = state.lines
    movable = [len(line.allowed) > 1 and not line.locked for line in lines]
    ctx = _Ctx(deadline=started + seconds, clock=clock, stats=stats, movable=movable,
               allowed_set=[frozenset(line.allowed) for line in lines])
    rng = random.Random(state.prob.seed)
    candidates = [i for i in range(len(lines)) if movable[i]]
    order = list(candidates)
    rng.shuffle(order)

    _full_descent(state, order, ctx)
    state.polish()
    best = state.snapshot()
    best_cost = state.total
    idle = 0
    while (candidates and not stats.time_capped and stats.perturbations < max_perturbations
           and idle < MAX_IDLE_PERTURBATIONS):
        if clock() >= ctx.deadline:
            stats.time_capped = True
            break
        count = max(1, round(PERTURB_SHARE * len(candidates)))
        chosen = rng.sample(candidates, min(count, len(candidates)))
        for i in chosen:
            alts = [p for p in lines[i].allowed if p != state.pattern[i]]
            if alts:
                state.apply(state.eval_relocate(i, rng.choice(alts))[1])
        seeds = list(chosen)
        for i in chosen:
            seeds.extend(state.prob.neighbors[i])
        _descent(state, seeds, ctx)
        state.polish()
        stats.perturbations += 1
        if state.total < best_cost - MIN_GAIN:
            best, best_cost, idle = state.snapshot(), state.total, 0
        else:
            state.restore(best)
            idle += 1
    if state.total > best_cost:
        state.restore(best)
    if not stats.time_capped:
        _full_descent(state, order, ctx)
    state.polish(range(SLOTS))
    stats.cost_end = state.total
    stats.seconds = clock() - started
    return stats


# --- Эффект отдельного изменения (§8): точный пересчёт затронутых дней ---

def _exact_day(prob: Problem, entries: Sequence[tuple[int, VisitParams]], j: int) -> dict[str, float]:
    """Метрики дня менеджера с нуля: entries — (строка, параметры визита)."""
    w = prob.weights
    lines = prob.lines
    nodes = [lines[i].node for i, _ in entries if lines[i].node]
    tour = nn_tour(nodes, prob.km)
    km = tour_length(tour, prob.km)
    drv = tour_length(tour, prob.mins)
    vm = sum(lines[i].minutes for i, _ in entries)
    mu = sum(pr.mu for _, pr in entries)
    var = sum(pr.var for _, pr in entries)
    counted = prob.workday[j] and (entries or prob.base[j])
    weak = (1.0 - p_at_least(mu, var, w.min_day_revenue)) if counted else 0.0
    return {'km': km, 'minutes': vm + drv, 'weak': weak}


def new_visit_flags(before: State, i: int, new: SlotPattern,
                    new_params: VisitParams) -> dict[int, tuple[bool, ...]]:
    """Флаги «заказал» визитов клиента i в шаблоне new: визиты на общих днях сохраняют номер k,
    новые дни получают свободные номера по порядку (как eval_relocate)."""
    new_set = set(new)
    kept = {j: k for k, j in enumerate(before.slot_of[i]) if j in new_set}
    free = [k for k in range(MAX_VISITS) if k not in kept.values()]
    new_k = dict(kept)
    new_k.update(zip([j for j in new if j not in kept], free))
    u = before.lines[i].u
    return {j: tuple(x < new_params.p_year for x in u[k]) for j, k in new_k.items()}


def fleet_change(before: State, i: int, new: SlotPattern,
                 new_params: VisitParams) -> tuple[list[tuple], list[tuple]]:
    """Визиты клиента i для парка (слот, вершина, флаги): какие уходят и какие приходят при смене
    шаблона на new. Параметры те же — только разные дни; другие — все дни (меняются флаги)."""
    g = before.lines[i].gnode
    if not g:
        return [], []
    old, new_set = before.pattern[i], set(new)
    same = before.params[i] == new_params
    flags = new_visit_flags(before, i, new, new_params)
    out = [(j, g, before.flags[i][k]) for k, j in enumerate(before.slot_of[i])
           if not same or j not in new_set]
    inn = [(j, g, flags[j]) for j in new if not same or j not in set(old)]
    return out, inn


def change_effect(before: State, i: int, new: SlotPattern, new_params: VisitParams) -> dict[str, float]:
    """Эффект смены шаблона (и частоты) клиента i, применённой к текущему плану (before — точное
    состояние «было»): Δ км менеджера, ожидаемых слабых дней и минут — в неделю; км парка — по
    быстрой оценке парка «было» (before.fleet; нет парка — 0). Дни менеджера пересчитываются с нуля
    (NN + 2-opt); при смене частоты меняется p клиента, поэтому затронуты все его дни «было» и «стало»."""
    prob = before.prob
    old = before.pattern[i]
    old_set, new_set = set(old), set(new)
    same_params = before.params[i] == new_params
    days = (old_set ^ new_set) if same_params else (old_set | new_set)
    out = {'km': 0.0, 'minutes': 0.0, 'weak': 0.0}
    for j in sorted(days):
        base = before.day_metrics(j)
        entries = [(m, before.params[m]) for m in sorted(before.members[j]) if m != i]
        if j in new_set:
            entries.append((i, new_params))
        after = _exact_day(prob, entries, j)
        for key in out:
            out[key] += after[key] - base[key]
    res = {key: value / W for key, value in out.items()}
    res['truck_km'] = (before.fleet.effect_km(*fleet_change(before, i, new, new_params))
                       if before.fleet is not None else 0.0)
    return res

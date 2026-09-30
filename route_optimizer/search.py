# -*- coding: utf-8 -*-
"""Быстрая оценка и поиск дней визитов внутри одного менеджера (этап 3, §5–§6, режим А).

Чистая логика — без Flask и без БД. Единицы: км (по прямой × извилистость — уже в матрицах),
минуты, драмы; стоимость C — драм в неделю: сумма по дням цикла / W (+ штраф за переносы).

Состояние дня обновляется при каждом ходе инкрементально:
- тур менеджера «дом → клиенты дня → дом»: удаление — выигрыш d(prev,c) + d(c,next) − d(prev,next),
  вставка — самая дешёвая позиция; без дома вершина 0 фиктивная (нулевые расстояния) — открытый путь;
- выручка дня (низкий сезон) — нормальное приближение: μ = Σ p·m, σ² = Σ (p·E[V²] − (p·m)²);
- грузовик — S сценариев с общими случайными числами: у каждого визита клиента (порядковый номер k
  в цикле) заранее вытянуто равномерное u[k][s], «заказал» = u < p_год; флаг переезжает вместе
  с визитом. Тур «склад → заказавшие → склад» на сценарий; км = среднее × ceil(кг пика / тоннаж).
Через каждые 50 принятых ходов изменённые туры проходят 2-opt, дни пересчитываются точно.
"""
from __future__ import annotations

import heapq
import math
import random
import time
from collections import deque
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
    p_year: float      # флаги «заказал» для км грузовика — по году, как на этапе 1
    kg: float          # ожидаемый кг в пик: p_пик × средний кг

    @property
    def sure(self) -> bool:
        return self.p_low >= 1.0

    @property
    def log_q(self) -> float:
        return math.log1p(-self.p_low) if 0.0 < self.p_low < 1.0 else 0.0


NO_VISIT = VisitParams(0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Weights:
    """Веса стоимости §4, драм. truck_per_km = 0 — грузовик не в стоимости. Штраф за переносы
    задаётся состоянию отдельно (при старте «fresh» он 0)."""
    manager_per_km: float
    truck_per_km: float
    weak_day: float
    poor_trip: float
    overtime_per_min: float
    window_min: float
    min_day_revenue: float
    min_trip_revenue: float
    truck_capacity_kg: float | None


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


@dataclass
class Problem:
    agent_id: int
    lines: list[Line]
    km: Matrix                  # менеджер: вершина 0 — дом (без дома — нули)
    mins: Matrix                # минуты в пути по тем же рёбрам
    tkm: Matrix | None          # грузовик: вершина 0 — склад; None — нет склада или машины
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


# --- Состояние ---

_AGG = ('km', 'drv', 'vm', 'mu', 'var', 'sure', 'logq', 'n', 'kg', 'tsum', 'cost')


class State:
    """Решение менеджера: шаблоны клиентов, туры и агрегаты дней, стоимость C (драм/нед)."""

    def __init__(self, prob: Problem, patterns: Sequence[SlotPattern],
                 params: Sequence[VisitParams], *, change_penalty: float, trucks: bool):
        self.prob = prob
        self.w = prob.weights
        self.lines = prob.lines
        self.params = list(params)
        self.change_penalty = change_penalty
        self.trucks = trucks and prob.tkm is not None
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
        self.tkm = [[0.0] * TRUCK_SCENARIOS for _ in range(SLOTS)]
        self.tour: list[list[int]] = [[0] for _ in range(SLOTS)]
        self.ttour: list[list[list[int]]] = [[[0] for _ in range(TRUCK_SCENARIOS)] for _ in range(SLOTS)]
        self.dirty: set[int] = set()
        for j in range(SLOTS):
            self._rebuild_tours(j)
            self._set_exact(j)
        self.total = self._total()

    # -- точный расчёт дня --

    def _flag(self, i: int, j: int, s: int) -> bool:
        return self.flags[i][self.slot_of[i].index(j)][s]

    def _rebuild_tours(self, j: int) -> None:
        """Туры дня с нуля: NN + 2-opt (как на этапе 1)."""
        lines = self.lines
        mem = sorted(self.members[j])
        self.tour[j] = nn_tour((lines[i].node for i in mem if lines[i].node), self.prob.km)
        if self.trucks:
            for s in range(TRUCK_SCENARIOS):
                self.ttour[j][s] = nn_tour((lines[i].node for i in mem
                                            if lines[i].node and self._flag(i, j, s)), self.prob.tkm)

    def exact_values(self, j: int) -> tuple[float, ...]:
        """Агрегаты дня с нуля по текущим турам и составу (порядок — как _AGG, без cost)."""
        prob = self.prob
        km = tour_length(self.tour[j], prob.km)
        drv = tour_length(self.tour[j], prob.mins)
        vm = mu = var = logq = kg = 0.0
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
            if self.lines[i].node:
                kg += pr.kg
        tkm = ([tour_length(t, prob.tkm) for t in self.ttour[j]] if self.trucks
               else [0.0] * TRUCK_SCENARIOS)
        return km, drv, vm, mu, var, sure, logq, n, kg, sum(tkm), tkm

    def _set_exact(self, j: int) -> None:
        km, drv, vm, mu, var, sure, logq, n, kg, tsum, tkm = self.exact_values(j)
        self.tkm[j] = tkm
        vals = (km, drv, vm, mu, var, sure, logq, n, kg, tsum,
                self._cost(j, km, drv, vm, mu, var, sure, logq, n, kg, tsum))
        for name, v in zip(_AGG, vals):
            getattr(self, name)[j] = v

    def _cost(self, j: int, km: float, drv: float, vm: float, mu: float, var: float, sure: int,
              logq: float, n: int, kg: float, tsum: float) -> float:
        """Стоимость дня, драм в неделю (§4)."""
        w = self.w
        c = w.manager_per_km * km
        over = vm + drv - w.window_min
        if over > 0.0:
            c += w.overtime_per_min * over
        if w.weak_day and self.prob.workday[j] and (n > 0 or self.prob.base[j]):
            c += w.weak_day * (1.0 - p_at_least(mu, var, w.min_day_revenue))
        if w.poor_trip and n > 0:
            c += w.poor_trip * p_poor(mu, var, sure, logq, w.min_trip_revenue)
        if self.trucks and w.truck_per_km and tsum > 0.0:
            cap = w.truck_capacity_kg
            mult = math.ceil(kg / cap) if cap and kg > cap else 1
            c += w.truck_per_km * tsum / TRUCK_SCENARIOS * mult
        return c / W

    def _total(self) -> float:
        return sum(self.cost) + self.change_penalty * self.n_moved

    def day_metrics(self, j: int) -> dict[str, float]:
        """Метрики дня для отчёта: км, минуты, ожидаемый «слабый» день, км грузовика (с резкой)."""
        w = self.w
        counted = self.prob.workday[j] and (self.n[j] > 0 or self.prob.base[j])
        cap = w.truck_capacity_kg
        mult = math.ceil(self.kg[j] / cap) if cap and self.kg[j] > cap else 1
        return {
            'km': self.km[j],
            'minutes': self.vm[j] + self.drv[j],
            'weak': (1.0 - p_at_least(self.mu[j], self.var[j], w.min_day_revenue)) if counted else 0.0,
            'truck_km': self.tsum[j] / TRUCK_SCENARIOS * mult,
        }

    # -- оценка изменения одного дня --

    def _eval_day(self, j: int, out_i: int, out_k: int, in_i: int, in_k: int) -> tuple[float, tuple]:
        """Δ стоимости дня j, если из него уходит визит (out_i, out_k) и/или приходит (in_i, in_k);
        -1 — нет. Туры не меняются: возвращается план применения (позиции вставки/удаления)."""
        prob = self.prob
        lines = self.lines
        kd, md = prob.km, prob.mins
        km, drv, vm, mu, var = self.km[j], self.drv[j], self.vm[j], self.mu[j], self.var[j]
        sure, logq, n, kg = self.sure[j], self.logq[j], self.n[j], self.kg[j]
        t = self.tour[j]
        m_rem = m_ins = -1
        x = y = 0
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
                kg -= pr.kg
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
                kg += pr.kg
                base = t if m_rem < 0 else t[:m_rem] + t[m_rem + 1:]
                m_ins, dk, a, b = _insertion(base, y, kd)
                km += dk
                drv += md[a][y] + md[y][b] - md[a][b]
        # опустевший тур или день — точные нули: погрешность сложений не накапливается
        if m_rem >= 0 and m_ins < 0 and len(t) == 2:
            km = drv = 0.0
        if n == 0:
            vm = mu = var = logq = kg = 0.0
        tsum = self.tsum[j]
        tops = None
        if self.trucks and (x or y):
            td = prob.tkm
            lengths = list(self.tkm[j])
            for s in range(TRUCK_SCENARIOS):
                rem = bool(x) and self.flags[out_i][out_k][s]
                ins = bool(y) and self.flags[in_i][in_k][s]
                if not (rem or ins):
                    continue
                tt = self.ttour[j][s]
                length = self.tkm[j][s]
                r_pos = i_pos = -1
                if rem:
                    r_pos, dk, _, _ = _removal(tt, x, td)
                    length += dk
                    if not ins and len(tt) == 2:
                        length = 0.0
                if ins:
                    base = tt if r_pos < 0 else tt[:r_pos] + tt[r_pos + 1:]
                    i_pos, dk, _, _ = _insertion(base, y, td)
                    length += dk
                if tops is None:
                    tops = []
                tops.append((s, r_pos, i_pos, length))
                lengths[s] = length
            tsum = sum(lengths)
        cost = self._cost(j, km, drv, vm, mu, var, sure, logq, n, kg, tsum)
        plan = (j, out_i, in_i, m_rem, m_ins, (km, drv, vm, mu, var, sure, logq, n, kg, tsum, cost), tops)
        return cost - self.cost[j], plan

    def _apply_day(self, plan: tuple) -> None:
        j, out_i, in_i, m_rem, m_ins, vals, tops = plan
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
        if tops:
            for s, r_pos, i_pos, length in tops:
                tt = self.ttour[j][s]
                if r_pos >= 0:
                    del tt[r_pos]
                if i_pos >= 0:
                    tt.insert(i_pos, self.lines[in_i].node)
                self.tkm[j][s] = length
        self.dirty.add(j)

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
        delta = 0.0
        plans = []
        for k, j in gone:
            key = ('out', j)
            hit = cache.get(key) if cache is not None else None
            if hit is None:
                hit = self._eval_day(j, i, k, -1, -1)
                if cache is not None:
                    cache[key] = hit
            delta += hit[0]
            plans.append(hit[1])
        for k, j in zip(free, added):
            key = ('in', j, k if self.trucks else 0)
            hit = cache.get(key) if cache is not None else None
            if hit is None:
                hit = self._eval_day(j, -1, -1, i, k)
                if cache is not None:
                    cache[key] = hit
            delta += hit[0]
            plans.append(hit[1])
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
        delta = 0.0
        plans = []
        for k, j in a_gone:                      # a уходит, b приходит
            d, plan = self._eval_day(j, a, k, b, b_new_k[j])
            delta += d
            plans.append(plan)
        for k, j in b_gone:                      # b уходит, a приходит
            d, plan = self._eval_day(j, b, k, a, a_new_k[j])
            delta += d
            plans.append(plan)
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

    # -- доводка, проверка, снимки --

    def polish(self, days: Iterable[int] | None = None) -> None:
        """2-opt туров изменённых дней (или указанных) и точный пересчёт их агрегатов."""
        todo = sorted(self.dirty) if days is None else sorted(set(days))
        for j in todo:
            two_opt(self.tour[j], self.prob.km)
            if self.trucks:
                for tt in self.ttour[j]:
                    two_opt(tt, self.prob.tkm)
            self._set_exact(j)
        self.dirty.clear()
        self.total = self._total()

    def consistency_error(self) -> float:
        """Наибольшее относительное расхождение инкрементальных агрегатов с расчётом с нуля
        (inf — туры не совпадают с составом дня). Для тестов §5."""
        worst = 0.0
        lines = self.lines
        for j in range(SLOTS):
            mem = self.members[j]
            nodes = sorted(lines[i].node for i in mem if lines[i].node)
            if sorted(self.tour[j][1:]) != nodes or self.tour[j][0] != 0:
                return math.inf
            if self.trucks:
                for s in range(TRUCK_SCENARIOS):
                    want = sorted(lines[i].node for i in mem if lines[i].node and self._flag(i, j, s))
                    if sorted(self.ttour[j][s][1:]) != want:
                        return math.inf
            exact = self.exact_values(j)
            stored = (self.km[j], self.drv[j], self.vm[j], self.mu[j], self.var[j], self.sure[j],
                      self.logq[j], self.n[j], self.kg[j], self.tsum[j])
            for got, want in zip(stored, exact[:10]):
                worst = max(worst, abs(got - want) / max(1.0, abs(want)))
            for got, want in zip(self.tkm[j], exact[10]):
                worst = max(worst, abs(got - want) / max(1.0, abs(want)))
            want = self._cost(j, *exact[:10])
            worst = max(worst, abs(self.cost[j] - want) / max(1.0, abs(want)))
        for i, p in enumerate(self.pattern):
            if sorted(self.slot_of[i]) != list(p):
                return math.inf
        return worst

    def snapshot(self) -> tuple:
        return (list(self.pattern), [list(x) for x in self.slot_of], list(self.moved), self.n_moved,
                [set(m) for m in self.members], [list(t) for t in self.tour],
                [[list(t) for t in day] for day in self.ttour], [list(x) for x in self.tkm],
                {name: list(getattr(self, name)) for name in _AGG}, set(self.dirty), self.total)

    def restore(self, snap: tuple) -> None:
        (pattern, slot_of, moved, n_moved, members, tour, ttour, tkm, agg, dirty, total) = snap
        self.pattern = list(pattern)
        self.slot_of = [list(x) for x in slot_of]
        self.moved = list(moved)
        self.n_moved = n_moved
        self.members = [set(m) for m in members]
        self.tour = [list(t) for t in tour]
        self.ttour = [[list(t) for t in day] for day in ttour]
        self.tkm = [list(x) for x in tkm]
        for name in _AGG:
            setattr(self, name, list(agg[name]))
        self.dirty = set(dirty)
        self.total = total


# --- Стартовые решения ---

def current_start(prob: Problem) -> list[SlotPattern]:
    """Старт «current»: текущие шаблоны, приведённые к целевой частоте. Закреплённые и допустимые
    текущие — как есть; остальные — допустимый шаблон с наибольшим числом общих с текущим дней
    (1/нед → 0.5: тот же день; 2/нед → 1: день из пары), при равенстве — с меньшей нагрузкой дней
    (минуты визитов уже расставленных клиентов), затем первый по порядку."""
    load = [0.0] * SLOTS
    out: list[SlotPattern] = [()] * len(prob.lines)
    pending = []
    for i, line in enumerate(prob.lines):
        if line.locked:
            out[i] = line.allowed[0]
        elif line.current in line.allowed:
            out[i] = line.current
        else:
            pending.append(i)
            continue
        for j in out[i]:
            load[j] += line.minutes
    for i in pending:
        line = prob.lines[i]
        cur = set(line.current)
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

def _exact_day(prob: Problem, entries: Sequence[tuple[int, VisitParams, tuple[bool, ...]]],
               j: int, trucks: bool) -> dict[str, float]:
    """Метрики дня с нуля: entries — (строка, параметры визита, флаги по сценариям)."""
    w = prob.weights
    lines = prob.lines
    nodes = [lines[i].node for i, _, _ in entries if lines[i].node]
    tour = nn_tour(nodes, prob.km)
    km = tour_length(tour, prob.km)
    drv = tour_length(tour, prob.mins)
    vm = sum(lines[i].minutes for i, _, _ in entries)
    mu = sum(pr.mu for _, pr, _ in entries)
    var = sum(pr.var for _, pr, _ in entries)
    counted = prob.workday[j] and (entries or prob.base[j])
    weak = (1.0 - p_at_least(mu, var, w.min_day_revenue)) if counted else 0.0
    truck_km = 0.0
    if trucks and prob.tkm is not None:
        kg = sum(pr.kg for i, pr, _ in entries if lines[i].node)
        total = 0.0
        for s in range(TRUCK_SCENARIOS):
            t = nn_tour([lines[i].node for i, _, fl in entries if lines[i].node and fl[s]], prob.tkm)
            total += tour_length(t, prob.tkm)
        cap = w.truck_capacity_kg
        mult = math.ceil(kg / cap) if cap and kg > cap else 1
        truck_km = total / TRUCK_SCENARIOS * mult
    return {'km': km, 'minutes': vm + drv, 'weak': weak, 'truck_km': truck_km}


def change_effect(before: State, i: int, new: SlotPattern, new_params: VisitParams) -> dict[str, float]:
    """Эффект смены шаблона (и частоты) клиента i, применённой к текущему плану (before — точное
    состояние «было»): Δ км менеджера, км грузовика, ожидаемых слабых дней и минут — в неделю.
    Затронутые дни пересчитываются с нуля (NN + 2-opt); при смене частоты меняется p клиента,
    поэтому затронуты все его дни «было» и «стало»."""
    prob = before.prob
    old = before.pattern[i]
    old_set, new_set = set(old), set(new)
    same_params = before.params[i] == new_params
    days = (old_set ^ new_set) if same_params else (old_set | new_set)
    # визиты на общих днях сохраняют номер k (и флаги), новые дни получают свободные номера по порядку
    kept = {j: k for k, j in enumerate(before.slot_of[i]) if j in new_set}
    free = [k for k in range(MAX_VISITS) if k not in kept.values()]
    new_k = dict(kept)
    new_k.update(zip([j for j in new if j not in kept], free))
    new_flags = {j: tuple(u < new_params.p_year for u in prob.lines[i].u[k]) for j, k in new_k.items()}
    out = {'km': 0.0, 'minutes': 0.0, 'weak': 0.0, 'truck_km': 0.0}
    for j in sorted(days):
        base = before.day_metrics(j)
        entries = [(m, before.params[m], before.flags[m][before.slot_of[m].index(j)])
                   for m in sorted(before.members[j]) if m != i]
        if j in new_set:
            entries.append((i, new_params, new_flags[j]))
        after = _exact_day(prob, entries, j, before.trucks)
        for key in out:
            out[key] += after[key] - base[key]
    return {key: value / W for key, value in out.items()}

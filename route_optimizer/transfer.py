# -*- coding: utf-8 -*-
"""Передача магазинов между менеджерами — режим Б (этап 4, план §3–§4).

Чистая логика — без Flask и без БД. Каждый менеджер — своё состояние search.State (быстрая оценка
этапа 3). В задаче менеджера, кроме его клиентов, есть «гости» — клиенты других менеджеров, которых
можно ему передать: у гостя шаблон () — визитов нет. Клиент в каждый момент посещается ровно одним
менеджером (owner); его строка у остальных — пустая.

Ходы (оценка — инкрементальная, только по двум затронутым менеджерам):
- передача: у X шаблон клиента → (), у Y гость: () → лучший для него допустимый шаблон;
- обмен: клиенты c (у X) и d (у Y) меняются днями — c у Y в днях d, d у X в днях c
  (State.eval_exchange у обоих менеджеров);
- передача группой: c и ближайшие клиенты X в те же дни — к Y (одна поездка X в район исчезает
  только вместе со всеми её клиентами);
- переносы дней внутри менеджера — ходы режима А (search._descent) по тем, кто сейчас у менеджера.
Стоимость компании = Σ стоимостей менеджеров (драм в неделю) + penalty_transfer × число клиентов,
которые посещаются не своим менеджером по плану ERP. Детерминизм — как в режиме А: порядок и
возмущения ILS от seed, число возмущений ограничено счётчиком, время — только страховочный стоп.
"""
from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

from . import search as sr

PERTURB_SHARE = 0.01          # ILS: доля передаваемых клиентов, которых возмущение отдаёт случайному кандидату
MAX_ROUNDS = 6                # проходов «передачи + переносы внутри менеджеров» до отсутствия улучшений
GROUP_MAX = 8                 # передача группой: клиент и до 7 ближайших клиентов того же менеджера


@dataclass(frozen=True)
class Client:
    """Клиент, который участвует в передачах: origin — менеджер по плану ERP; lines — менеджер →
    строка клиента в задаче менеджера (у origin — своя строка, у кандидатов — гостевая);
    fixed — менеджер, у которого клиент закреплён принятым решением «передать» (не двигается)."""
    customer_id: int
    origin: int
    lines: Mapping[int, int]
    fixed: int | None = None
    near: frozenset[int] = frozenset()   # кандидаты, у которых рядом есть свои клиенты (передача группой)


@dataclass
class TransferStats:
    seconds: float = 0.0
    time_capped: bool = False
    perturbations: int = 0
    accepted: int = 0             # принятых передач, обменов и передач группой
    intra_accepted: int = 0       # принятых ходов внутри менеджеров
    cost_start: float = 0.0
    cost_end: float = 0.0


class Market:
    """Решение всех менеджеров сразу: состояния, кто сейчас посещает клиента, стоимость компании."""

    def __init__(self, states: Mapping[int, sr.State], clients: Sequence[Client],
                 neighbors: Mapping[int, Sequence[int]], penalty: float,
                 around: Mapping[int, Sequence[int]] | None = None):
        """neighbors — ближайшие клиенты (партнёры обмена, повторная проверка после хода); around —
        все клиенты в радиусе по близости (из них — группа для передачи группой; нет — neighbors)."""
        self.states = dict(states)
        self.agents = sorted(self.states)
        self.clients = {c.customer_id: c for c in clients}
        self.neighbors = {cid: tuple(neighbors.get(cid, ())) for cid in self.clients}
        around = neighbors if around is None else around
        self.around = {cid: tuple(around.get(cid, ())) for cid in self.clients}
        self.penalty = penalty
        self.owner: dict[int, int] = {}
        for c in clients:
            present = [a for a in sorted(c.lines) if self.states[a].pattern[c.lines[a]]]
            if len(present) > 1:
                raise ValueError(f'клиент {c.customer_id} в плане {len(present)} менеджеров')
            self.owner[c.customer_id] = present[0] if present else c.origin
        self.n_moved = sum(1 for cid, c in self.clients.items() if self.owner[cid] != c.origin)
        self._allowed = {a: [frozenset(line.allowed) for line in st.lines] for a, st in self.states.items()}
        self.total = self._total()

    def _total(self) -> float:
        return sum(self.states[a].total for a in self.agents) + self.penalty * self.n_moved

    def movable(self, cid: int) -> bool:
        return self.clients[cid].fixed is None and len(self.clients[cid].lines) > 1

    def _moved_delta(self, cid: int, frm: int, to: int) -> int:
        origin = self.clients[cid].origin
        return int(to != origin) - int(frm != origin)

    # -- ходы --

    def eval_transfer(self, cid: int, to: int,
                      out: tuple[float, tuple] | None = None) -> tuple[float, tuple]:
        """Δ стоимости компании, если клиент перейдёт к менеджеру to в лучший для него шаблон.
        out — уже посчитанное удаление клиента у нынешнего менеджера (одно на все to)."""
        c = self.clients[cid]
        frm = self.owner[cid]
        if out is None:
            out = self.states[frm].eval_relocate(c.lines[frm], ())
        st = self.states[to]
        i = c.lines[to]
        cache: dict = {}
        best = None
        for p in st.lines[i].allowed:
            d, move = st.eval_relocate(i, p, cache)
            if best is None or d < best[0]:
                best = (d, move)
        delta = out[0] + best[0] + self.penalty * self._moved_delta(cid, frm, to)
        return delta, ('transfer', cid, frm, to, out[1], best[1])

    def eval_swap(self, c1: int, c2: int) -> tuple[float, tuple] | None:
        """Обмен днями клиентов разных менеджеров; None — обмен невозможен (нет строки у другого
        менеджера, разная частота или чужие дни не входят в допустимые шаблоны)."""
        a, b = self.clients[c1], self.clients[c2]
        x, y = self.owner[c1], self.owner[c2]
        if x == y or y not in a.lines or x not in b.lines:
            return None
        sx, sy = self.states[x], self.states[y]
        p1, p2 = sx.pattern[a.lines[x]], sy.pattern[b.lines[y]]
        if len(p1) != len(p2) or p2 not in self._allowed[y][a.lines[y]] \
                or p1 not in self._allowed[x][b.lines[x]]:
            return None
        dx, mx = sx.eval_exchange(a.lines[x], b.lines[x])     # c2 занимает дни c1 у x
        dy, my = sy.eval_exchange(b.lines[y], a.lines[y])     # c1 занимает дни c2 у y
        dn = self._moved_delta(c1, x, y) + self._moved_delta(c2, y, x)
        return dx + dy + self.penalty * dn, ('swap', c1, c2, x, y, mx, my)

    def eval_group(self, cid: int, to: int) -> tuple[float, tuple] | None:
        """Передача группой: клиент и ближайшие к нему клиенты того же менеджера в те же дни (одна
        поездка в район) — менеджеру to, каждый в лучший для него шаблон, по очереди. Выгода часто только
        у группы: пока у менеджера в том районе в этот день остаётся хоть один клиент, поездка туда не
        исчезает. Оценка точная: ходы применяются к двум затронутым менеджерам и откатываются из
        снимка; None — группы нет (меньше двух)."""
        frm = self.owner[cid]
        st = self.states[frm]
        days = set(st.pattern[self.clients[cid].lines[frm]])
        group = [cid] + [d for d in self.around[cid] if d != cid and self.movable(d) and self.owner[d] == frm
                         and to in self.clients[d].lines and days & set(st.pattern[self.clients[d].lines[frm]])]
        if len(group) < 2:
            return None
        saved = (self.states[frm].snapshot(), self.states[to].snapshot(), self.n_moved, self.total)
        moves = []
        for d in group[:GROUP_MAX]:
            move = self.eval_transfer(d, to)[1]
            self.apply(move)
            moves.append(move)
        delta = self.total - saved[3]
        self.states[frm].restore(saved[0])
        self.states[to].restore(saved[1])
        for move in moves:
            self.owner[move[1]] = frm
        self.n_moved, self.total = saved[2], saved[3]
        return delta, ('group', tuple(moves))

    def apply(self, move: tuple) -> None:
        if move[0] == 'group':   # ходы группы — по очереди, с того же состояния, на котором оценены
            for m in move[1]:
                self.apply(m)
            return
        if move[0] == 'transfer':
            _, cid, frm, to, m_out, m_in = move
            self.states[frm].apply(m_out)
            self.states[to].apply(m_in)
            self.n_moved += self._moved_delta(cid, frm, to)
            self.owner[cid] = to
        else:
            _, c1, c2, x, y, mx, my = move
            self.states[x].apply(mx)
            self.states[y].apply(my)
            self.n_moved += self._moved_delta(c1, x, y) + self._moved_delta(c2, y, x)
            self.owner[c1], self.owner[c2] = y, x
        self.total = self._total()

    def best_move(self, cid: int) -> tuple[float, tuple] | None:
        """Лучшая передача клиента cid (любому кандидату или обратно своему менеджеру) или обмен с
        ближайшим клиентом другого менеджера."""
        if not self.movable(cid):
            return None
        c = self.clients[cid]
        frm = self.owner[cid]
        out = self.states[frm].eval_relocate(c.lines[frm], ())
        best = None
        for to in sorted(c.lines):
            if to == frm:
                continue
            hit = self.eval_transfer(cid, to, out)
            if best is None or hit[0] < best[0]:
                best = hit
        for other in self.neighbors[cid]:
            if other == cid or not self.movable(other) or self.owner[other] == frm:
                continue
            hit = self.eval_swap(cid, other)
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit
        return best

    # -- доводка и снимки --

    def polish(self, all_days: bool = False) -> None:
        for a in self.agents:
            self.states[a].polish(range(sr.SLOTS) if all_days else None)
        self.total = self._total()

    def snapshot(self) -> tuple:
        return ({a: self.states[a].snapshot() for a in self.agents}, dict(self.owner), self.n_moved,
                self.total)

    def restore(self, snap: tuple) -> None:
        states, owner, n_moved, total = snap
        for a in self.agents:
            self.states[a].restore(states[a])
        self.owner = dict(owner)
        self.n_moved = n_moved
        self.total = total

    def recomputed_total(self) -> float:
        """Стоимость компании заново по состояниям и владельцам (для тестов: равна self.total)."""
        moved = sum(1 for cid, c in self.clients.items() if self.owner[cid] != c.origin)
        return sum(self.states[a].total for a in self.agents) + self.penalty * moved

    def consistency_error(self) -> float:
        """Наибольшее расхождение инкрементальных агрегатов с расчётом с нуля по всем менеджерам;
        inf — клиент посещается не ровно одним менеджером или owner неверен."""
        for cid, c in self.clients.items():
            present = [a for a in c.lines if self.states[a].pattern[c.lines[a]]]
            if present != [self.owner[cid]]:
                return float('inf')
        worst = max((self.states[a].consistency_error() for a in self.agents), default=0.0)
        return max(worst, abs(self.recomputed_total() - self.total) / max(1.0, abs(self.total)))


# --- Поиск ---

@dataclass
class _Run:
    deadline: float
    clock: Callable[[], float]
    stats: TransferStats


def _intra_ctx(market: Market, agent: int, run: _Run) -> sr._Ctx:
    """Контекст ходов режима А для менеджера: двигаются только те, кто сейчас у него и не закреплён."""
    st = market.states[agent]
    movable = [bool(st.pattern[i]) and not line.locked and len(line.allowed) > 1
               for i, line in enumerate(st.lines)]
    return sr._Ctx(deadline=run.deadline, clock=run.clock, stats=sr.SearchStats(), movable=movable,
                   allowed_set=market._allowed[agent])


def _intra(market: Market, seeds: Mapping[int, set[int]], run: _Run) -> bool:
    """Переносы дней внутри затронутых менеджеров (relocate + swap режима А) от строк seeds."""
    improved = False
    for a in sorted(seeds):
        if not seeds[a]:
            continue
        ctx = _intra_ctx(market, a, run)
        st = market.states[a]
        order = sorted({x for i in seeds[a] for x in (i, *st.prob.neighbors[i])})
        if sr._descent(st, order, ctx):
            improved = True
        run.stats.intra_accepted += ctx.stats.accepted
        if ctx.stats.time_capped:
            run.stats.time_capped = True
    market.total = market._total()
    return improved


def _touch(market: Market, move: tuple, seeds: dict[int, set[int]]) -> list[int]:
    """Строки, вокруг которых после хода стоит переложить дни, и клиенты, которых стоит проверить снова."""
    if move[0] == 'group':
        return [x for m in move[1] for x in _touch(market, m, seeds)]
    if move[0] == 'transfer':
        cids, agents = (move[1],), (move[2], move[3])
    else:
        cids, agents = (move[1], move[2]), (move[3], move[4])
    for cid in cids:
        c = market.clients[cid]
        for a in agents:
            if a in c.lines:
                seeds.setdefault(a, set()).add(c.lines[a])
    return [x for cid in cids for x in (cid, *market.neighbors[cid])]


def _descent(market: Market, order: Iterable[int], run: _Run, seeds: dict[int, set[int]]) -> bool:
    """Локальный поиск передач и обменов от очереди клиентов; ход — при ΔC < −1 драм."""
    queue: deque[int] = deque()
    queued: set[int] = set()
    for cid in order:
        if cid not in queued and market.movable(cid):
            queue.append(cid)
            queued.add(cid)
    improved = False
    steps = 0
    while queue:
        steps += 1
        if not steps % 16 and run.clock() >= run.deadline:
            run.stats.time_capped = True
            return improved
        cid = queue.popleft()
        queued.discard(cid)
        hit = market.best_move(cid)
        if hit is None or hit[0] >= -sr.MIN_GAIN:
            continue
        market.apply(hit[1])
        improved = True
        run.stats.accepted += 1
        if not run.stats.accepted % sr.TWO_OPT_EVERY:
            market.polish()
        for x in _touch(market, hit[1], seeds):
            if x not in queued and market.movable(x):
                queue.append(x)
                queued.add(x)
    return improved


def _group_pass(market: Market, order: Sequence[int], run: _Run, seeds: dict[int, set[int]]) -> bool:
    """Передачи группой (Market.eval_group) — менеджерам, у которых рядом свои клиенты, и обратно своему."""
    improved = False
    for step, cid in enumerate(order, 1):
        if not step % 16 and run.clock() >= run.deadline:
            run.stats.time_capped = True
            return improved
        if not market.movable(cid):
            continue
        c = market.clients[cid]
        best = None
        for to in sorted((c.near | {c.origin}) - {market.owner[cid]}):
            hit = market.eval_group(cid, to) if to in c.lines else None
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit
        if best is not None and best[0] < -sr.MIN_GAIN:
            market.apply(best[1])
            improved = True
            run.stats.accepted += 1
            _touch(market, best[1], seeds)
    return improved


def _rounds(market: Market, order: Sequence[int], run: _Run) -> None:
    """Передачи и обмены, передачи группой, затем переносы дней у затронутых менеджеров — пока что-то
    улучшается."""
    todo = list(order)
    for _ in range(MAX_ROUNDS):
        seeds: dict[int, set[int]] = {}
        moved = _descent(market, todo, run, seeds)
        if run.stats.time_capped:
            break
        grouped = _group_pass(market, order, run, seeds)
        if run.stats.time_capped:
            break
        intra = _intra(market, seeds, run) if seeds else False
        market.polish()
        if run.stats.time_capped or not (moved or grouped or intra):
            break
        # после переносов внутри менеджеров выгодными могут стать другие передачи — снова все
        todo = list(order)


def search(market: Market, *, seconds: float, seed: int, clock: Callable[[], float] = time.perf_counter,
           max_perturbations: int = sr.MAX_PERTURBATIONS) -> TransferStats:
    """Передачи, обмены и переносы внутри менеджеров + ILS (возмущение — случайные передачи)."""
    started = clock()
    stats = TransferStats(cost_start=market.total)
    run = _Run(started + seconds, clock, stats)
    rng = random.Random(seed)
    candidates = sorted(cid for cid in market.clients if market.movable(cid))
    order = list(candidates)
    rng.shuffle(order)

    _rounds(market, order, run)
    best = market.snapshot()
    best_cost = market.total
    idle = 0
    while (candidates and not stats.time_capped and stats.perturbations < max_perturbations
           and idle < sr.MAX_IDLE_PERTURBATIONS):
        if clock() >= run.deadline:
            stats.time_capped = True
            break
        count = max(1, round(PERTURB_SHARE * len(candidates)))
        chosen = rng.sample(candidates, min(count, len(candidates)))
        seeds: dict[int, set[int]] = {}
        requeue: list[int] = []
        for cid in chosen:
            targets = [a for a in sorted(market.clients[cid].lines) if a != market.owner[cid]]
            if not targets:
                continue
            move = market.eval_transfer(cid, rng.choice(targets))[1]
            market.apply(move)
            requeue += _touch(market, move, seeds)
        _descent(market, requeue, run, seeds)
        if not stats.time_capped:
            _intra(market, seeds, run)
        market.polish()
        stats.perturbations += 1
        if market.total < best_cost - sr.MIN_GAIN:
            best, best_cost, idle = market.snapshot(), market.total, 0
        else:
            market.restore(best)
            idle += 1
    if market.total > best_cost:
        market.restore(best)
    if not stats.time_capped:
        _rounds(market, order, run)
    market.polish(all_days=True)
    stats.cost_end = market.total
    stats.seconds = clock() - started
    return stats


# --- Эффект передачи, применённой к текущему плану ---

EFFECT_ZERO = {'km': 0.0, 'minutes': 0.0, 'weak': 0.0, 'truck_km': 0.0, 'over': 0.0}


def side_effect(before: sr.State, i: int, new: sr.SlotPattern,
                new_params: sr.VisitParams) -> dict[str, float]:
    """Эффект для одного менеджера, если клиент i получает шаблон new (() — уходит от менеджера,
    из () — приходит к нему), к текущему плану before: Δ км, минут, ожидаемых слабых дней, км грузовика
    и минут сверх рабочего дня — в неделю. Затронутые дни — с нуля (NN + 2-opt), как change_effect."""
    prob = before.prob
    window = prob.weights.window_min
    old = before.pattern[i]
    old_set, new_set = set(old), set(new)
    days = (old_set ^ new_set) if before.params[i] == new_params else (old_set | new_set)
    kept = {j: k for k, j in enumerate(before.slot_of[i]) if j in new_set}
    free = [k for k in range(sr.MAX_VISITS) if k not in kept.values()]
    new_k = dict(kept)
    new_k.update(zip([j for j in new if j not in kept], free))
    new_flags = {j: tuple(u < new_params.p_year for u in prob.lines[i].u[k]) for j, k in new_k.items()}
    out = dict(EFFECT_ZERO)
    for j in sorted(days):
        base = before.day_metrics(j)
        entries = [(m, before.params[m], before.flags[m][before.slot_of[m].index(j)])
                   for m in sorted(before.members[j]) if m != i]
        if j in new_set:
            entries.append((i, new_params, new_flags[j]))
        after = sr._exact_day(prob, entries, j, before.trucks)
        for key in ('km', 'minutes', 'weak', 'truck_km'):
            out[key] += after[key] - base[key]
        out['over'] += max(0.0, after['minutes'] - window) - max(0.0, base['minutes'] - window)
    return {key: value / sr.W for key, value in out.items()}


def main_effect(giver: Mapping[str, float], taker: Mapping[str, float], giver_km_cost: float,
                taker_km_cost: float, weak_day: float, overtime_per_min: float) -> str | None:
    """Главный выигрыш передачи (для причины простыми словами), по тем же весам, что в стоимости, и
    по обоим менеджерам вместе: 'km' — меньше км; 'weak' — меньше слабых дней (только если день
    принимающего становится сильнее); 'overload' — меньше минут сверх рабочего дня (только если
    у отдающего день был перегружен); None — отдельно эта передача ни по одной статье не выигрывает
    (выгода — вместе с остальными изменениями)."""
    gains = {'km': -(giver['km'] * giver_km_cost + taker['km'] * taker_km_cost)}
    if taker['weak'] < 0:
        gains['weak'] = -(giver['weak'] + taker['weak']) * weak_day
    if giver['over'] < 0:
        gains['overload'] = -(giver['over'] + taker['over']) * overtime_per_min
    kind, gain = max(sorted(gains.items()), key=lambda kv: kv[1])
    return kind if gain > 0 else None

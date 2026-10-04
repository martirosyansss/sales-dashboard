# -*- coding: utf-8 -*-
"""Текущий план визитов из шаблонов ERP (ROUTETEMPLATES × ROUTETEMPLATESLIST).

Чистая логика — без Flask и без БД.
Допущения (не проверены на данных, где все шаблоны fWEEK=1, fPERIODICITY=1):
  - шаблон (P = fPERIODICITY, w = fWEEK) действует в неделях цикла k ∈ 1..W,
    где W = max(P) и (k − 1) mod P == (w − 1) mod P;
  - fWEEKDAY: 1 = понедельник … 7 = воскресенье.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Collection, Iterable

WEEKDAY_LABELS = {1: 'Երկ', 2: 'Երք', 3: 'Չրք', 4: 'Հնգ', 5: 'Ուրբ', 6: 'Շբթ', 7: 'Կիր'}


@dataclass(frozen=True)
class TemplateRow:
    """Строка шаблона маршрута: агент, неделя/день/периодичность, клиент и его порядок."""
    agent_id: int
    template_id: int
    week: int
    weekday: int
    periodicity: int
    customer_id: int
    rownum: int
    address_id: int


@dataclass(frozen=True)
class PlanVisit:
    customer_id: int
    address_id: int  # CUSTOMERDELIVERYADDRESSES.fID из шаблона (0 — не задан)
    rownum: int


@dataclass(frozen=True)
class PlanDay:
    """День менеджера: (агент, неделя цикла, день недели) и визиты по fROWNUM."""
    agent_id: int
    week: int
    weekday: int
    visits: tuple[PlanVisit, ...]


@dataclass(frozen=True)
class CurrentPlan:
    cycle_weeks: int
    days: tuple[PlanDay, ...]
    visits_per_week: dict[int, float]  # f_i: визитов клиента в неделю (у всех агентов)
    multiweek: bool                    # встретились fPERIODICITY ≠ 1 или fWEEK ≠ 1

    @property
    def agent_ids(self) -> list[int]:
        return sorted({d.agent_id for d in self.days})

    @property
    def customer_ids(self) -> list[int]:
        return sorted({v.customer_id for d in self.days for v in d.visits})

    def days_of(self, agent_id: int) -> list[PlanDay]:
        return [d for d in self.days if d.agent_id == agent_id]

    def customers_of(self, agent_id: int) -> set[int]:
        return {v.customer_id for d in self.days if d.agent_id == agent_id for v in d.visits}

    def visits_per_week_among(self, agent_ids: Collection[int]) -> dict[int, float]:
        """f_i только по дням этих агентов (включённых в расчёт): дубль шаблона вне расчёта
        не делит вероятность заказа пополам. К клиенту не ездит никто из них — f_i по всем
        агентам, чтобы у исключённых менеджеров цифры оставались осмысленными."""
        agents = set(agent_ids)
        counts = Counter(v.customer_id for d in self.days if d.agent_id in agents for v in d.visits)
        return {c: counts[c] / self.cycle_weeks if counts[c] else f
                for c, f in self.visits_per_week.items()}


def is_active(k: int, week: int, periodicity: int) -> bool:
    """Действует ли шаблон (P, w) в неделе цикла k."""
    p = max(periodicity, 1)
    return (k - 1) % p == (week - 1) % p


def build_plan(rows: Iterable[TemplateRow]) -> CurrentPlan:
    """Развёртка шаблонов в дни цикла; дубли клиента в одном дне схлопываются (первый по fROWNUM)."""
    rows = list(rows)
    if not rows:
        return CurrentPlan(1, (), {}, False)
    cycle = max(max(r.periodicity, 1) for r in rows)
    multiweek = any(r.periodicity != 1 or r.week != 1 for r in rows)

    grouped: dict[tuple[int, int, int], list[TemplateRow]] = {}
    for r in rows:
        for k in range(1, cycle + 1):
            if is_active(k, r.week, r.periodicity):
                grouped.setdefault((r.agent_id, k, r.weekday), []).append(r)

    days = []
    for key in sorted(grouped):
        seen: set[int] = set()
        visits = []
        for r in sorted(grouped[key], key=lambda x: (x.rownum, x.template_id, x.customer_id)):
            if r.customer_id in seen:
                continue
            seen.add(r.customer_id)
            visits.append(PlanVisit(r.customer_id, r.address_id, r.rownum))
        days.append(PlanDay(key[0], key[1], key[2], tuple(visits)))

    counts = Counter(v.customer_id for d in days for v in d.visits)
    return CurrentPlan(cycle, tuple(days), {c: n / cycle for c, n in counts.items()}, multiweek)


def delivery_weekday(weekday: int) -> tuple[int, bool]:
    """День доставки заказов дня визита: пн–пт → следующий день, сб → пн, вс → пн (с пометкой).

    Возвращает (день недели доставки, заказ воскресенья).
    """
    if 1 <= weekday <= 5:
        return weekday + 1, False
    return 1, weekday == 7

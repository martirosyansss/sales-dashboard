"""Сравнение назначения рейсов по действующей месячной формуле зарплаты.

Чистый расчёт сценария: накладные ERP и параметры не изменяются. Километры
для оплаты считаются тем же нормативным туром, что на странице зарплаты.
Неполное сопоставление людей/накладных возвращает объяснение вместо суммы.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Mapping, Sequence

from . import crew_pay as cp


def _name_key(name: str) -> frozenset[str]:
    return frozenset(name.casefold().split())


@dataclass(frozen=True)
class PayrollScenario:
    valid: bool
    monthly_before_amd: int | None = None
    monthly_after_amd: int | None = None
    change_amd: int | None = None
    reasons: tuple[str, ...] = ()
    missing_coordinates: tuple[int, ...] = ()


class PayrollScenarios:
    def __init__(self, data: cp.CrewData, params: cp.Params, tours: cp.Tours,
                 calendar_rest, scope: set[int], day: date, crew: Mapping[str, str],
                 depot, coords, distances):
        self.data, self.params, self.tours = data, params, tours
        self.calendar_rest, self.scope, self.day = calendar_rest, frozenset(scope), day
        self.crew, self.depot, self.coords, self.distances = dict(crew), depot, coords, distances
        self.base = cp.compute(data, params, calendar_rest, tours)
        self.before = sum(row.pay for row in self.base.rows if set(row.agent_ids) & self.scope)
        self.memo, self.cache = {}, {}

    def _agent(self, name: str) -> tuple[int | None, str | None]:
        ids = {aid for aid, (_, candidate) in self.data.agents.items()
               if _name_key(candidate) == _name_key(name) and aid in self.scope}
        if not ids:
            return None, 'crew_not_mapped'
        active = {inv.van_id for inv in self.data.invoices if inv.day == self.day and inv.van_id in ids}
        if len(active) > 1:
            return None, 'ambiguous_same_name_codes'
        if active:
            return next(iter(active)), None
        # Не создаём пересечение прежних кодов одного человека. Если в этот
        # день он не работал, выбирается последний использованный код.
        previous = [(inv.day, inv.van_id) for inv in self.data.invoices
                    if inv.van_id in ids and inv.day < self.day]
        if previous:
            return max(previous)[1], None
        return (next(iter(ids)), None) if len(ids) == 1 else (None, 'ambiguous_same_name_codes')

    def evaluate(self, routes: Sequence[tuple[str, Sequence[int]]]) -> PayrollScenario:
        assigned = {}
        for code, customers in routes:
            for cid in customers:
                if cid in assigned and assigned[cid] != code:
                    return PayrollScenario(False, reasons=('customer_split_between_workers',))
                assigned[cid] = code
        key = tuple(sorted(assigned.items()))
        if key in self.cache:
            return self.cache[key]
        result = self._evaluate(assigned)
        self.cache[key] = result
        return result

    def _evaluate(self, assigned: Mapping[int, str]) -> PayrollScenario:
        agents, reasons = {}, set()
        for code in set(assigned.values()):
            if not self.crew.get(code):
                reasons.add('crew_not_recorded')
                continue
            aid, reason = self._agent(self.crew[code])
            if reason:
                reasons.add(reason)
            else:
                agents[code] = aid
                if self.data.agents[aid][0].strip().upper() in self.params.excluded_people:
                    reasons.add('crew_excluded_from_payroll')
        known = {inv.customer_id for inv in self.data.invoices if inv.day == self.day}
        if set(assigned) - known:
            reasons.add('planned_customer_without_invoice')
        source_ids = {inv.van_id for inv in self.data.invoices
                      if inv.day == self.day and inv.customer_id in assigned}
        if source_ids - self.scope:
            reasons.add('invoice_worker_outside_recorded_crew')
        if any(inv.day == self.day and inv.customer_id in assigned and
               inv.line_id == agents.get(assigned[inv.customer_id])
               for inv in self.data.invoices):
            reasons.add('crew_is_invoice_sales_agent')
        if reasons:
            return PayrollScenario(False, reasons=tuple(sorted(reasons)))

        invoices = tuple(replace(inv, van_id=agents[assigned[inv.customer_id]])
                         if inv.day == self.day and inv.customer_id in assigned else inv
                         for inv in self.data.invoices)
        scenario = cp.CrewData(invoices, self.data.agents)
        kept, _, _, _ = cp._kept(scenario, self.params)
        sets = {frozenset(cid for (day, cid) in per if day == self.day)
                for aid, per in kept.items() if aid in self.scope}
        sets.discard(frozenset())
        km = dict(self.tours.km)
        missing = set(self.tours.no_coords)
        if self.params.rate_km > 0:
            extra, _ = cp.plan_tours(self.depot, self.coords, sets,
                                    lambda a, b: self.distances.get((a, b)), self.memo)
            km.update(extra.km)
            missing.update(extra.no_coords)
        result = cp.compute(scenario, self.params, self.calendar_rest, cp.Tours(km, frozenset(missing)))
        total = sum(row.pay for row in result.rows if set(row.agent_ids) & self.scope)
        return PayrollScenario(True, self.before, total, total - self.before,
                               missing_coordinates=tuple(sorted(set(assigned) & missing)))

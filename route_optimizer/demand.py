# -*- coding: utf-8 -*-
"""Спрос клиентов: заказы, сезонный индекс, λ и p по окнам дат, класс размера.

Чистая логика — без Flask и без БД. Деньги — драмы (AMD), вес — кг, частоты — заказов в неделю.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import fmean
from typing import Iterable, Mapping, Sequence

DateWindow = tuple[date, date]  # полуинтервал [начало, конец)

MIN_EXPOSURE_DAYS = 21       # меньше 3 недель истории в окне — λ по окну ненадёжна
SEASON_HISTORY_MONTHS = 36   # сезонный индекс — по последним 36 полным месяцам

SIZE_SMALL, SIZE_MEDIUM, SIZE_LARGE = 'small', 'medium', 'large'


@dataclass(frozen=True)
class SaleDoc:
    """Документ продажи (SALES, fSTATE=2) с датой заказа-родителя (ORDERS.fDATE), если он есть."""
    customer_id: int
    agent_id: int
    sale_date: date
    order_date: date | None
    revenue: float  # драм
    kg: float


@dataclass(frozen=True)
class Order:
    """Заказ = документы клиента за одну дату заказа."""
    customer_id: int
    date: date
    agent_id: int
    revenue: float  # драм
    kg: float


def group_orders(docs: Iterable[SaleDoc]) -> list[Order]:
    """Документы → заказы по (клиент, дата заказа).

    Дата заказа — ORDERS.fDATE, иначе SALES.fDATE. Выручка и кг — суммы по документам.
    Агент заказа — агент самого крупного документа (при равенстве — детерминированно).
    """
    groups: dict[tuple[int, date], list[SaleDoc]] = {}
    for d in docs:
        groups.setdefault((d.customer_id, d.order_date or d.sale_date), []).append(d)
    orders = []
    for (customer_id, day), ds in groups.items():
        biggest = max(ds, key=lambda x: (x.revenue, x.kg, -x.agent_id))
        orders.append(Order(customer_id, day, biggest.agent_id,
                            sum(x.revenue for x in ds), sum(x.kg for x in ds)))
    orders.sort(key=lambda o: (o.date, o.customer_id))
    return orders


# --- Окна дат ---

def window_days(windows: Iterable[DateWindow]) -> int:
    return sum((end - start).days for start, end in windows if end > start)


def clip_windows(windows: Iterable[DateWindow], start: date, end: date) -> list[DateWindow]:
    """Пересечение набора окон с [start, end)."""
    out = []
    for s, e in windows:
        s2, e2 = max(s, start), min(e, end)
        if e2 > s2:
            out.append((s2, e2))
    return out


def in_windows(day: date, windows: Iterable[DateWindow]) -> bool:
    return any(s <= day < e for s, e in windows)


def exposure_days(windows: Sequence[DateWindow], first_date: date | None, end: date) -> int:
    """Дни окон W, в которые клиент уже существовал: |W ∩ [first_date, end)|."""
    if first_date is None:
        return 0
    return window_days(clip_windows(windows, first_date, end))


def month_add(d: date, months: int) -> date:
    """Первое число месяца, сдвинутого на months от месяца d."""
    idx = d.year * 12 + (d.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 1)


def month_windows(months: Iterable[int], start: date, end: date) -> list[DateWindow]:
    """Части [start, end), попадающие в календарные месяцы months."""
    wanted = set(months)
    out = []
    cur = date(start.year, start.month, 1)
    while cur < end:
        nxt = month_add(cur, 1)
        if cur.month in wanted:
            s, e = max(cur, start), min(nxt, end)
            if e > s:
                out.append((s, e))
        cur = nxt
    return out


def company_rate(orders_by_day: Mapping[date, int], windows: Sequence[DateWindow]) -> float | None:
    """Заказов компании в неделю в окнах (None, если окна пустые)."""
    days = window_days(windows)
    if days <= 0:
        return None
    n = sum(c for d, c in orders_by_day.items() if in_windows(d, windows))
    return n / (days / 7.0)


# --- Параметры клиента ---

@dataclass(frozen=True)
class Demand:
    """Модель спроса клиента в наборе окон: λ заказов в неделю и прошлые заказы (выручка, кг)."""
    lam: float
    values: tuple[tuple[float, float], ...]
    exposure_days: int
    fallback: bool = False

    @property
    def mean_revenue(self) -> float:
        return fmean(v[0] for v in self.values) if self.values else 0.0

    @property
    def mean_kg(self) -> float:
        return fmean(v[1] for v in self.values) if self.values else 0.0


NO_DEMAND = Demand(0.0, (), 0)


def window_demand(orders: Sequence[Order], windows: Sequence[DateWindow],
                  first_date: date | None, end: date, scale: float = 1.0) -> Demand:
    """λ = заказов в W / (exposure_days / 7) × scale; values — (выручка, кг) этих заказов.

    Экспозиция снизу ограничена 21 днём: клиент с одним заказом неделю назад иначе получил бы
    λ ≈ 1/нед по одному наблюдению (для сезонов при малой экспозиции — фолбэк season_demand).
    """
    in_w = [o for o in orders if in_windows(o.date, windows)]
    exposure = exposure_days(windows, first_date, end)
    if not in_w:
        return Demand(0.0, (), exposure)
    lam = len(in_w) / (max(exposure, MIN_EXPOSURE_DAYS) / 7.0) * scale
    return Demand(lam, tuple((o.revenue, o.kg) for o in in_w), exposure)


def season_demand(orders: Sequence[Order], windows: Sequence[DateWindow], first_date: date | None,
                  end: date, year: Demand, rate_ratio: float, scale: float = 1.0) -> Demand:
    """Спрос в сезоне S. Если экспозиция в S < 21 дня — фолбэк:
    λ = λ_год × (rate_S / rate_год) (rate_ratio), values = values_год. scale уже учтён в year."""
    exposure = exposure_days(windows, first_date, end)
    if exposure < MIN_EXPOSURE_DAYS:
        return Demand(year.lam * rate_ratio, year.values, exposure, fallback=True)
    return window_demand(orders, windows, first_date, end, scale)


def visit_probability(lam: float, visits_per_week: float) -> float:
    """p = min(1, λ / f): вероятность заказа при одном визите."""
    if visits_per_week <= 0 or lam <= 0:
        return 0.0
    return min(1.0, lam / visits_per_week)


# --- Сезонность ---

def seasonal_index(monthly_revenue: Mapping[tuple[int, int], float], end_month: date,
                   history_months: int = SEASON_HISTORY_MONTHS) -> dict[int, float] | None:
    """Сезонный индекс месяца: idx[m] = среднее по окнам «12 месяцев подряд»
    (скользящим внутри последних history_months полных месяцев до end_month)
    отношения выручка_m / средняя месячная выручка окна.

    Окна с нулевым месяцем пропускаются (ноль выручки у компании — пробел в данных).
    None — если хотя бы для одного месяца нет ни одного окна.
    """
    seq = [month_add(end_month, -history_months + i) for i in range(history_months)]
    vals = [float(monthly_revenue.get((m.year, m.month), 0.0)) for m in seq]
    ratios: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for i in range(len(seq) - 11):
        window = vals[i:i + 12]
        if min(window) <= 0:
            continue
        mu = fmean(window)
        for month_start, v in zip(seq[i:i + 12], window):
            ratios[month_start.month].append(v / mu)
    if any(not r for r in ratios.values()):
        return None
    return {m: fmean(r) for m, r in ratios.items()}


def classify_months(index: Mapping[int, float] | None, low_max: float,
                    peak_min: float) -> tuple[list[int], list[int]]:
    """Низкий сезон — idx ≤ low_max, пик — idx ≥ peak_min (по неокруглённому индексу)."""
    if not index:
        return [], []
    low = sorted(m for m, v in index.items() if v <= low_max)
    peak = sorted(m for m, v in index.items() if v >= peak_min)
    return low, peak


# --- Класс размера ---

def size_class(avg_order_kg: float | None, group: str | None, chain_groups: Iterable[str],
               small_max_kg: float, medium_max_kg: float) -> str:
    """Класс по среднему кг заказа за год; группы-сети — всегда large; нет заказов — small."""
    if group and group in set(chain_groups):
        return SIZE_LARGE
    if avg_order_kg is None:
        return SIZE_SMALL
    if avg_order_kg <= small_max_kg:
        return SIZE_SMALL
    if avg_order_kg <= medium_max_kg:
        return SIZE_MEDIUM
    return SIZE_LARGE

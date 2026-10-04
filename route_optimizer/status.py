# -*- coding: utf-8 -*-
"""Статус клиента по давности последнего заказа (этап 3, §15; ответ владельца №27).

Чистая логика — без Flask и без БД. Статус считается на дату as_of без утечки будущего: заказы
с датой ≥ as_of и первый заказ не раньше as_of не учитываются.

| Статус   | Условие (проверяется сверху вниз)                                                    |
|----------|--------------------------------------------------------------------------------------|
| new      | первый заказ за всю историю < status_new_days дней назад — не трогаем                |
| never    | в окне 12 мес [as_of − 365, as_of) нет ни одного заказа                              |
| seasonal | ≥ 80% заказов окна — в пиковых месяцах, а месяц as_of не пиковый: затихшим не считается |
| lost     | ≥ 3 заказов: тишина > max(lost_min_days, lost_mult × обычный интервал); 1–2 — > 240 дн |
| dormant  | ≥ 3 заказов: тишина > max(dormant_min_days, dormant_mult × интервал); 1–2 — > 120 дн |
| active   | всё остальное                                                                        |

Заказ — дата заказа клиента (demand.group_orders); тишина — дней от последнего заказа до as_of;
обычный интервал — медиана промежутков между датами заказов окна. «Потерян» проверяется раньше
«затих»: его порог строже. Затихший, потерянный и «без заказов» (SILENT) — λ = 0 во всех сезонах,
ожидаемая выручка 0; потерянным и «без заказов» (REMOVABLE) оптимизатор предлагает «убрать из
маршрута».
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Any, Collection, Iterable, Mapping

from .demand import Order

WINDOW_DAYS = 365
MIN_ORDERS_FOR_INTERVAL = 3      # меньше — порог по числу дней, а не по обычному интервалу
SEASONAL_SHARE = 0.8
FEW_ORDERS_DORMANT_DAYS = 120    # 1–2 заказа за год
FEW_ORDERS_LOST_DAYS = 240

NEW, NEVER, SEASONAL, DORMANT, LOST, ACTIVE = 'new', 'never', 'seasonal', 'dormant', 'lost', 'active'
SILENT = frozenset({DORMANT, LOST, NEVER})     # λ = 0 во всех сезонах
REMOVABLE = frozenset({LOST, NEVER})           # предложение «убрать из маршрута»

REMOVE_TEXT = 'հանել երթուղուց'
WIN_BACK_TEXT = 'այց ամեն շաբաթ՝ փորձել վերադարձնել'


@dataclass(frozen=True)
class CustomerStatus:
    """Статус клиента на дату as_of и то, по чему он определён (окно 12 месяцев)."""
    status: str
    silent_days: int | None              # дней с последнего заказа; заказов нет — None
    usual_interval_days: float | None    # медиана интервалов между заказами (от 3 заказов)
    orders: int                          # заказов (дат заказа) за 12 месяцев
    revenue: float                       # выручка этих заказов, драм
    last_order: date | None

    @property
    def silent(self) -> bool:
        """Затих, потерян или без заказов: ожидаемая выручка 0 во всех сезонах."""
        return self.status in SILENT


def customer_status(orders: Iterable[Order], first_order: date | None, as_of: date,
                    peak_months: Collection[int], s: Mapping[str, Any]) -> CustomerStatus:
    """Статус клиента на as_of (таблица в описании модуля). orders — заказы клиента (лишние вне
    окна отбрасываются); first_order — первая покупка за всю историю (None — неизвестна);
    peak_months — месяцы пика (1..12); s — настройки: status_new_days, dormant_min_days,
    dormant_mult, lost_min_days, lost_mult."""
    start = as_of - timedelta(days=WINDOW_DAYS)
    window = [o for o in orders if start <= o.date < as_of]
    dates = sorted({o.date for o in window})
    n = len(dates)
    last = dates[-1] if dates else None
    silent = (as_of - last).days if last is not None else None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    interval = float(median(gaps)) if n >= MIN_ORDERS_FOR_INTERVAL else None
    revenue = math.fsum(o.revenue for o in window)
    # первый заказ: из истории ERP, если он раньше as_of, иначе — первый заказ окна
    firsts = [d for d in (first_order, dates[0] if dates else None) if d is not None and d < as_of]
    first = min(firsts) if firsts else None

    peak = set(peak_months)
    if first is not None and (as_of - first).days < s['status_new_days']:
        status = NEW
    elif not n:
        status = NEVER
    elif peak and as_of.month not in peak \
            and sum(1 for d in dates if d.month in peak) >= SEASONAL_SHARE * n:
        status = SEASONAL
    else:
        if interval is not None:
            dormant_after = max(float(s['dormant_min_days']), float(s['dormant_mult']) * interval)
            lost_after = max(float(s['lost_min_days']), float(s['lost_mult']) * interval)
        else:
            dormant_after, lost_after = FEW_ORDERS_DORMANT_DAYS, FEW_ORDERS_LOST_DAYS
        status = LOST if silent > lost_after else (DORMANT if silent > dormant_after else ACTIVE)
    return CustomerStatus(status, silent, interval, n, revenue, last)


# --- Тексты ---

def silence_text(st: CustomerStatus) -> str:
    """«не покупает 150 дн (обычно раз в 14 дн)», «не покупает 130 дн», «ни одного заказа за год»."""
    if st.silent_days is None:
        return 'վերջին տարում ոչ մի պատվեր'
    text = f'չի գնում {st.silent_days} օր'
    if st.usual_interval_days is not None:
        text += f' (սովորաբար՝ {math.floor(st.usual_interval_days + 0.5)} օրը մեկ)'
    return text


def win_back_text(st: CustomerStatus) -> str:
    """Причина частоты «раз в неделю» у затихшего (или оставленного владельцем потерянного):
    «не покупает 60 дн (обычно раз в 7 дн) — визит каждую неделю, попробовать вернуть»."""
    return f'{silence_text(st)} — {WIN_BACK_TEXT}'


def risk_summary(statuses: Iterable[CustomerStatus]) -> dict[str, int]:
    """Итог «под риском»: сколько клиентов затихли и потеряны, сколько они брали за год (драм),
    и сколько без заказов за год (они же — потерянные для предложения «убрать»)."""
    out = {'dormant': 0, 'dormant_rev_year': 0.0, 'lost': 0, 'lost_rev_year': 0.0, 'never': 0}
    for st in statuses:
        if st.status == DORMANT:
            out['dormant'] += 1
            out['dormant_rev_year'] += st.revenue
        elif st.status == LOST:
            out['lost'] += 1
            out['lost_rev_year'] += st.revenue
        elif st.status == NEVER:
            out['never'] += 1
    return {k: int(round(v)) for k, v in out.items()}

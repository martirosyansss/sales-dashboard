# -*- coding: utf-8 -*-
"""Журнал гаража → «ремонт ֏/км» машины (ответ владельца №53, docs/plans/garage-journal-plan.md).

Чистая логика — без Flask и без БД. Единицы: дни, км, драмы (целые).

Цена км машины на дату as_of: ремонты, запчасти и ТО (kind repair) за последние 12 месяцев ÷ пробег за то же время по
спидометру. Окно W = (as_of − 365 дн, as_of]; записи позже as_of не участвуют.
  - показания пробега — все записи журнала машины (у каждой есть спидометр) + одометр заправок из APK (readings);
  - end — день последнего показания ≤ as_of, km_end — его км;
  - есть показание не позже начала окна — start = as_of − 365, км в start — линейная интерполяция по дням между
    последним показанием до и первым после; иначе start — день самого раннего показания в окне;
  - km = km_end − km_start; cost — ремонты с днём в (start, end] (первое показание — точка отсчёта); крупный ремонт,
    растянутый на N = 24 или 36 месяцев (spread_months), — равномерно по дням на [день, день + N мес.): в cost идёт
    доля дней, попавших в (start, end] (и у ремонта, сделанного до начала окна);
  - готово, если end − start ≥ READY_DAYS (≈ 6 мес.), km ≥ READY_KM и в (start, end] попал хоть один ремонт (или доля
    растянутого): своя цена = cost / km (֏/км, до 0,1); иначе — «накапливается: N из 6 мес.», «мало км» или «ремонтов в
    журнале нет».
Сглаживание (blend): своя цена машины тянется к средней по модели — (km·своя + BLEND_KM·m) / (km + BLEND_KM) = (cost +
BLEND_KM·m) / (km + BLEND_KM); m = Σcost / Σkm готовых машин этой модели (вместе с ней), если их ≥ 2, иначе — готовых
машин парка, если их ≥ 2, иначе цена — своя. Модель — первое слово имени машины заглавными, без имени — тоннаж (model_of).
ДТП (accident) и страховка / техосмотр / налог (fixed) — только итоги окна, в цену не входят: от того, куда поедет
машина, они не зависят. Ноль ремонтов за полгода и больше — это не «0 ֏/км», а нет данных (журнал ведут только для
пробега или ремонты не вносят): цены нет (status no_repairs), в расчёте — ручное «Износ, драм/км». Без записей журнала
о машине в окне цены нет тем более (status none, «нет пробега»): одометр APK только дополняет показания журнала.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence

KINDS = ('repair', 'accident', 'fixed', 'odometer')
WINDOW_DAYS = 365
READY_DAYS = 182          # ≈ 6 месяцев покрытия показаниями спидометра (ответ владельца №53)
READY_KM = 500            # меньше — делить не на что: цена была бы случайной
MONTHS_READY = 6
SPREAD_MONTHS = (24, 36)  # крупный ремонт (двигатель, КПП, шины комплектом) растягивается на столько месяцев
BLEND_KM = 20_000         # вес средней модели в км: машина с 20 000 км своего пробега — наполовину своя цена
KM_PER_DAY_MAX = 1500.0   # спидометр и одометр заправок не прирастают быстрее (store, learning.odometer_plausible)


@dataclass(frozen=True)
class Entry:
    """Живая (не удалённая) запись журнала одной машины."""
    day: date
    kind: str
    amount_amd: int
    odometer_km: int
    spread_months: int | None = None   # ремонт растянут на столько месяцев (SPREAD_MONTHS); None — обычный


@dataclass(frozen=True)
class Price:
    """Ремонт ֏/км машины на дату. price — в расчёте (своя, сглаженная к средней модели), только у status ready;
    own — своя (cost_amd / km — ровно то, что делится); model_price — средняя модели или парка (blend: model | fleet)."""
    price: float | None
    status: str                 # ready | accumulating (мало месяцев) | low_km (мало км) | no_repairs (ремонтов нет)
    #                             | none (нет пробега)
    cost_amd: int               # ремонт в (start, end] — с долями растянутых, целые драмы
    km: float                   # пробег (start, end]
    start: date | None
    end: date | None            # последнее показание ≤ as_of
    days: int
    months: int                 # полных «месяцев» покрытия: days · 6 // READY_DAYS (182 дня — 6, 365 — 12)
    repair_amd: int             # итоги окна (as_of − 365, as_of] по дню записи
    accident_amd: int
    fixed_amd: int
    own: float | None = None          # своя цена, ֏/км (0,1)
    model: str | None = None          # модель машины (model_of)
    model_price: float | None = None  # средняя модели (blend model) или парка (fleet), ֏/км (0,1)
    blend: str | None = None          # с чем сглажена: model | fleet | None — не сглажена
    cost: float = 0.0                 # cost_amd без округления


def add_months(d: date, n: int) -> date:
    """d + n месяцев; день — не позже последнего дня месяца (31.01 + 1 → 28.02)."""
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def repair_share(e: Entry, start: date, end: date) -> float:
    """Доля ремонта в стоимости (start, end]: обычный — вся сумма, если его день в (start, end]; растянутый на N месяцев —
    равномерно по дням на [день, день + N мес.): доля дней, попавших в (start, end]."""
    if not e.spread_months:
        return float(e.amount_amd) if start < e.day <= end else 0.0
    stop = add_months(e.day, e.spread_months)
    lo, hi = max(e.day, start + timedelta(days=1)), min(stop, end + timedelta(days=1))
    return e.amount_amd * max(0, (hi - lo).days) / (stop - e.day).days


def model_of(name: str | None, capacity_kg: float | None) -> str | None:
    """Модель машины для сглаживания: первое слово имени заглавными; без имени — тоннаж («2200 կգ»: подпись видна на
    страницах — по-армянски); ничего — None."""
    words = (name or '').split()
    if words:
        return words[0].upper()
    return f'{round(capacity_kg)} կգ' if capacity_kg else None


def readings(entries: Sequence[Entry], apk: Iterable[tuple[date, float]]) -> list[tuple[date, float]]:
    """Показания пробега машины по (день, км): все записи журнала + одометр заправок APK. Журнал главнее: показание
    APK в день, где есть запись журнала, и показание APK, нарушающее неубывание относительно журнала (меньше показания
    журнала более раннего дня или больше — более позднего), отбрасываются; среди APK — и меньшее уже принятого
    раннего (опечатка не ломает расчёт)."""
    journal = sorted((e.day, float(e.odometer_km)) for e in entries)
    days = {d for d, _ in journal}
    kept: list[tuple[date, float]] = []
    for d, km in sorted((d, float(km)) for d, km in apk):
        if d in days or any((jd < d and jk > km) or (jd > d and jk < km) for jd, jk in journal):
            continue
        if kept and km < kept[-1][1]:
            continue
        kept.append((d, km))
    return sorted(journal + kept)


def price(entries: Sequence[Entry], as_of: date, apk: Iterable[tuple[date, float]] = ()) -> Price:
    """Ремонт ֏/км одной машины на as_of (правила — в описании модуля)."""
    live = [e for e in entries if e.day <= as_of]
    ws = as_of - timedelta(days=WINDOW_DAYS)
    window = [e for e in live if e.day > ws]
    totals = {kind: sum(e.amount_amd for e in window if e.kind == kind) for kind in ('repair', 'accident', 'fixed')}
    if not window:
        return Price(None, 'none', 0, 0.0, None, None, 0, 0, totals['repair'], totals['accident'], totals['fixed'])
    pts = readings(live, ((d, km) for d, km in apk if d <= as_of))
    end, km_end = pts[-1]
    before = [p for p in pts if p[0] <= ws]
    if before:
        (d0, k0), (d1, k1) = before[-1], next(p for p in pts if p[0] > ws)   # в окне есть запись — есть и показание
        start, km_start = ws, k0 + (k1 - k0) * (ws - d0).days / (d1 - d0).days
    else:
        start, km_start = pts[0]
    km = km_end - km_start
    shares = [repair_share(e, start, end) for e in live if e.kind == 'repair']
    cost = math.fsum(shares)
    days = (end - start).days
    months = days * MONTHS_READY // READY_DAYS
    if days < READY_DAYS:
        status = 'accumulating'
    elif km < READY_KM:
        status = 'low_km'
    elif not any(x > 0 for x in shares):
        status = 'no_repairs'
    else:
        status = 'ready'
    own = round(cost / km, 1) if status == 'ready' else None
    return Price(own, status, round(cost), km, start, end, days, months, totals['repair'], totals['accident'],
                 totals['fixed'], own=own, cost=cost)


def blend(found: Mapping[str, Price], models: Mapping[str, str | None]) -> dict[str, Price]:
    """Своя цена готовых машин → сглаженная к средней модели или парка (правило — в описании модуля); у машины без своей
    готовой цены цены нет и теперь (в расчёте — ручное значение)."""
    ready = {code: p for code, p in found.items() if p.price is not None}
    out = {}
    for code, p in found.items():
        key = models.get(code)
        same = [q for c, q in ready.items() if key is not None and models.get(c) == key]
        group, scope = (same, 'model') if len(same) >= 2 else (list(ready.values()), 'fleet')
        if p.price is None or len(group) < 2:
            out[code] = replace(p, model=key)
            continue
        m = math.fsum(q.cost for q in group) / math.fsum(q.km for q in group)
        out[code] = replace(p, price=round((p.cost + BLEND_KM * m) / (p.km + BLEND_KM), 1), model=key,
                            model_price=round(m, 1), blend=scope)
    return out


def prices(entries: Mapping[str, Sequence[Entry]], as_of: date,
           apk: Mapping[str, Sequence[tuple[date, float]]] | None = None,
           models: Mapping[str, str | None] | None = None) -> dict[str, Price]:
    """Цена каждой машины журнала на as_of — своя, сглаженная к средней модели (blend); apk — одометр заправок по
    машине, models — модель машины (model_of; нет — сглаживание только к средней парка)."""
    own = {code: price(items, as_of, (apk or {}).get(code, ())) for code, items in sorted(entries.items())}
    return blend(own, models or {})


def effective(found: Mapping[str, Price]) -> dict[str, float]:
    """Машина → ремонт ֏/км в расчёте: только готовые цены. Остальные машины — с ручным «Износ, драм/км»."""
    return {code: p.price for code, p in found.items() if p.price is not None}

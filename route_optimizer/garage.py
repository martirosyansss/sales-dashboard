# -*- coding: utf-8 -*-
"""Журнал гаража → «ремонт ֏/км» машины (ответ владельца №53, docs/plans/garage-journal-plan.md).

Чистая логика — без Flask и без БД. Единицы: дни, км, драмы (целые).

Цена км машины на дату as_of: ремонты, запчасти и ТО (kind repair) за последние 12 месяцев ÷ пробег за то же время по
спидометру. Окно W = (as_of − 365 дн, as_of]; записи позже as_of не участвуют.
  - показания пробега — все записи журнала машины (у каждой есть спидометр) + одометр заправок из APK (readings);
  - end — день последнего показания ≤ as_of, km_end — его км;
  - есть показание не позже начала окна — start = as_of − 365, км в start — линейная интерполяция по дням между
    последним показанием до и первым после; иначе start — день самого раннего показания в окне;
  - km = km_end − km_start; cost — ремонты с днём в (start, end] (первое показание — точка отсчёта);
  - готово, если end − start ≥ READY_DAYS (≈ 6 мес.), km ≥ READY_KM и в (start, end] есть хоть один ремонт: price =
    cost / km (֏/км, до 0,1); иначе — «накапливается: N из 6 мес.», «мало км» или «ремонтов в журнале нет».
ДТП (accident) и страховка / техосмотр / налог (fixed) — только итоги окна, в цену не входят: от того, куда поедет
машина, они не зависят. Ноль ремонтов за полгода и больше — это не «0 ֏/км», а нет данных (журнал ведут только для
пробега или ремонты не вносят): цены нет (status no_repairs), в расчёте — ручное «Износ, драм/км». Без записей журнала
о машине в окне цены нет тем более (status none, «нет пробега»): одометр APK только дополняет показания журнала.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence

KINDS = ('repair', 'accident', 'fixed', 'odometer')
WINDOW_DAYS = 365
READY_DAYS = 182          # ≈ 6 месяцев покрытия показаниями спидометра (ответ владельца №53)
READY_KM = 500            # меньше — делить не на что: цена была бы случайной
MONTHS_READY = 6


@dataclass(frozen=True)
class Entry:
    """Живая (не удалённая) запись журнала одной машины."""
    day: date
    kind: str
    amount_amd: int
    odometer_km: int


@dataclass(frozen=True)
class Price:
    """Ремонт ֏/км машины на дату. price — только у status ready; cost_amd / km — ровно то, что делится."""
    price: float | None
    status: str                 # ready | accumulating (мало месяцев) | low_km (мало км) | no_repairs (ремонтов нет)
    #                             | none (нет пробега)
    cost_amd: int               # ремонт в (start, end]
    km: float                   # пробег (start, end]
    start: date | None
    end: date | None            # последнее показание ≤ as_of
    days: int
    months: int                 # полных «месяцев» покрытия: days · 6 // READY_DAYS (182 дня — 6, 365 — 12)
    repair_amd: int             # итоги окна (as_of − 365, as_of] по дню записи
    accident_amd: int
    fixed_amd: int


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
    repairs = [e.amount_amd for e in live if e.kind == 'repair' and start < e.day <= end]
    cost = sum(repairs)
    days = (end - start).days
    months = days * MONTHS_READY // READY_DAYS
    if days < READY_DAYS:
        status = 'accumulating'
    elif km < READY_KM:
        status = 'low_km'
    elif not repairs:
        status = 'no_repairs'
    else:
        status = 'ready'
    return Price(round(cost / km, 1) if status == 'ready' else None, status, cost, km, start, end, days, months,
                 totals['repair'], totals['accident'], totals['fixed'])


def prices(entries: Mapping[str, Sequence[Entry]], as_of: date,
           apk: Mapping[str, Sequence[tuple[date, float]]] | None = None) -> dict[str, Price]:
    """Цена каждой машины журнала на as_of; apk — одометр заправок по машине."""
    return {code: price(items, as_of, (apk or {}).get(code, ())) for code, items in sorted(entries.items())}


def effective(found: Mapping[str, Price]) -> dict[str, float]:
    """Машина → ремонт ֏/км в расчёте: только готовые цены. Остальные машины — с ручным «Износ, драм/км»."""
    return {code: p.price for code, p in found.items() if p.price is not None}

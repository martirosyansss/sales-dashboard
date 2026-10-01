# -*- coding: utf-8 -*-
"""Точки магазинов по GPS водителей и предложения «точка неверная» (driver-geo-plan.md §2, §4).

Правило точки водителей — чистая функция driver_point(); чтение — Store.arrived_fixes / open_suggestions.
В «Маршруты» точки и предложения передаёт DriverSource (app_v2 → route_optimizer.attach_driver_geo): пакет
route_optimizer не импортирует courier. Нет courier.db — пусто, файл не создаётся.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Iterable

from route_optimizer.geo import haversine_km, is_valid_point, median_point

from . import clock
from .store import Store

DRIVER_MAX_ACCURACY_M = 50.0   # отметка с точностью хуже — не учитывается
DRIVER_WINDOW_DAYS = 180       # отметки за последние полгода
DRIVER_MIN_DAYS = 3            # точка — по дням: не меньше 3 разных дней рядом с медианой
DRIVER_NEAR_M = 150.0          # «рядом с медианой»
DRIVER_NEAR_SHARE = (2, 3)     # не меньше 2/3 дней рядом с медианой, иначе разброс большой — точки нет


@dataclass(frozen=True)
class ArrivedFix:
    """Отметка arrived терминала: дата (рабочий день) и координата как прислал терминал."""
    day: str
    lat: Any
    lon: Any
    accuracy: Any


def _usable(f: ArrivedFix) -> bool:
    acc = f.accuracy
    return (isinstance(acc, (int, float)) and not isinstance(acc, bool) and 0 <= acc <= DRIVER_MAX_ACCURACY_M
            and is_valid_point(f.lat, f.lon))


def day_fixes(fixes: Iterable[ArrivedFix]) -> list[ArrivedFix]:
    """Одна отметка на день — самая точная (при равенстве — первая): у клиента бывает несколько накладных в день, и
    день с многими доставками не должен перевешивать остальные."""
    best: dict[str, ArrivedFix] = {}
    for f in fixes:
        if _usable(f) and (f.day not in best or f.accuracy < best[f.day].accuracy):
            best[f.day] = f
    return [best[d] for d in sorted(best)]


def driver_point(fixes: Iterable[ArrivedFix]) -> tuple[float, float, int] | None:
    """Точка магазина по отметкам водителей одного клиента (окно дат задаёт вызывающий). Отметки — с точностью
    ≤ DRIVER_MAX_ACCURACY_M в Армении, по одной на день (day_fixes); медиана по широте и долготе дней; точка есть, если
    в пределах DRIVER_NEAR_M от медианы ≥ 2/3 дней и не меньше DRIVER_MIN_DAYS дней. (широта, долгота, дней рядом с
    медианой) или None."""
    days = day_fixes(fixes)
    if len(days) < DRIVER_MIN_DAYS:
        return None
    med = median_point([(float(f.lat), float(f.lon)) for f in days])
    near = sum(1 for f in days if haversine_km(med, (float(f.lat), float(f.lon))) * 1000 <= DRIVER_NEAR_M)
    num, den = DRIVER_NEAR_SHARE
    if near < DRIVER_MIN_DAYS or near * den < len(days) * num:
        return None
    return med[0], med[1], near


def driver_points(rows: Iterable[tuple[int, str, Any, Any, Any]]) -> dict[int, tuple[float, float, int]]:
    """Строки Store.arrived_fixes → {клиент: (широта, долгота, дней)} по правилу driver_point."""
    by_customer: dict[int, list[ArrivedFix]] = {}
    for cid, day, lat, lon, acc in rows:
        by_customer.setdefault(cid, []).append(ArrivedFix(day, lat, lon, acc))
    out = {}
    for cid, fixes in by_customer.items():
        p = driver_point(fixes)
        if p is not None:
            out[cid] = p
    return out


class DriverSource:
    """Точки и предложения водителей для «Маршрутов» (протокол route_optimizer.views.DriverGeo)."""

    def __init__(self, store: Store):
        self.store = store

    def _exists(self) -> bool:
        return os.path.exists(self.store.path)   # нет базы — не создаём её чтением

    def points(self) -> dict[int, tuple[float, float, int]]:
        if not self._exists():
            return {}
        today = clock.now().date()
        since = today - timedelta(days=DRIVER_WINDOW_DAYS)
        return driver_points(self.store.arrived_fixes(since.isoformat(), today.isoformat()))

    def suggestions(self) -> list[dict[str, Any]]:
        return self.store.open_suggestions() if self._exists() else []

    def decide(self, event_id: str, decision: str, user: str | None,
               apply: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
        if not self._exists():
            return None
        return self.store.decide_suggestion(event_id, decision, user, apply)

# -*- coding: utf-8 -*-
"""Связь с разделом «Маршруты»: склад, ручные точки клиентов, рабочие дни, план «Развоза» на дату, дороги.

Только чтение через публичный API route_optimizer (Store.load, Store.load_dispatch, Draft.from_json,
RoadProvider.get) — файлы route_optimizer/ не меняются. Раздела нет или его база битая — пустой вид:
порядок «auto» от склада не строится (склада нет), точки без ручных координат.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Collection, Mapping, Sequence

from route_optimizer import dispatch as dp
from route_optimizer.dispatch import DispatchOrder
from route_optimizer.geo import Point, haversine_km
from route_optimizer.store import StoreError as RoutesStoreError
from route_optimizer.tsp import Distance

logger = logging.getLogger(__name__)

DEFAULT_WORKDAYS = (1, 2, 3, 4, 5, 6)
Places = Callable[[Sequence[int]], Mapping[int, dp.Place]]   # клиенты → (адрес, название) из ERP (№74)
DETOUR = 1.3   # участок без дороги — по прямой × извилистость (как evaluate.road_norms без калибровки)


@dataclass(frozen=True)
class RoutesView:
    depot: Point | None = None
    geo_overrides: Mapping[int, Point] = field(default_factory=dict)
    workdays: tuple[int, ...] = DEFAULT_WORKDAYS
    holidays: frozenset[date] = frozenset()                # нерабочие даты настроек (№64)
    plan_exists: bool = False                              # логист собрал план «Развоза» на дату
    trips: tuple[tuple[str, tuple[int, ...]], ...] = ()    # (машина, клиенты по порядку) в порядке черновика
    excluded: frozenset[str] = frozenset()                 # заказы «не везём сегодня»
    added: frozenset[str] = frozenset()                    # заказы прошлых дней, добавленные логистом
    carried: frozenset[str] = frozenset()                  # «Везти завтра» прошлых дней
    dropped: frozenset[str] = frozenset()                  # перенесённые сюда, но убранные логистом
    agents_off: frozenset[int] = frozenset()               # менеджеры, чьи заказы не везём (фильтр «Մենեջերներ»)
    same_day: frozenset[str] = frozenset()                 # заказы с датой дня, взятые в его развоз (№72)
    taken: frozenset[str] = frozenset()                    # взятые в развоз дня их приёма в прошлые дни — не везём
    fleet: dp.FleetRule = dp.NO_RULE                       # чьи заказы везут машины — правило настроек «Развоза» (№74)
    roads: Any = None                                      # RoadDistances | None

    def car_customers(self, car_code: str) -> list[int]:
        """Клиенты машины по плану: порядок рейсов черновика, внутри — порядок объезда; повтор (тяжёлый
        заказ в нескольких рейсах) — по первому появлению."""
        out: list[int] = []
        seen: set[int] = set()
        for truck, stops in self.trips:
            if truck != car_code:
                continue
            for cid in stops:
                if cid not in seen:
                    seen.add(cid)
                    out.append(cid)
        return out

    def plan_trucks(self) -> dict[int, set[str]]:
        """Клиент → машины, которым его отдал план."""
        out: dict[int, set[str]] = {}
        for truck, stops in self.trips:
            for cid in stops:
                out.setdefault(cid, set()).add(truck)
        return out


def routes_view(state: Any, day: date) -> RoutesView:
    """Вид на «Маршруты» для даты; state — app.extensions['route_optimizer'] (RoutesState) или None."""
    if state is None:
        return RoutesView()
    try:
        bundle = state.store.load()
    except RoutesStoreError:
        logger.warning('[Courier] База «Маршрутов» недоступна — склад, ручные точки и план не учтены', exc_info=True)
        return RoutesView()
    workdays = tuple(bundle.settings.get('workdays') or DEFAULT_WORKDAYS)
    holidays = dp.holidays_of(bundle.settings)
    roads = None
    try:
        roads = state.roads.get() if getattr(state, 'roads', None) is not None else None
    except Exception:   # карта дорог — необязательна: сбой → по прямой
        logger.warning('[Courier] Карта дорог недоступна — порядок по прямой', exc_info=True)
    base = RoutesView(depot=bundle.depot, geo_overrides=dict(bundle.geo_overrides), workdays=workdays,
                      holidays=holidays, fleet=dp.FleetRule.from_settings(bundle.settings), roads=roads)
    try:
        stored = state.store.load_dispatch(day.isoformat())
        carried = _carried(state, day, workdays, holidays)
        taken = _taken(state, day, workdays, holidays)
    except RoutesStoreError:
        logger.warning('[Courier] План «Развоза» на %s не прочитан', day, exc_info=True)
        return base
    if stored is None:
        # плана нет — менеджеры, чьи заказы не везём, по правилу настроек «Развоза» (№69)
        return RoutesView(depot=base.depot, geo_overrides=base.geo_overrides, workdays=workdays, holidays=holidays,
                          carried=frozenset(carried), agents_off=frozenset(dp.agents_off_of(None, bundle.settings)),
                          taken=frozenset(taken), fleet=base.fleet, roads=roads)
    draft = dp.Draft.from_json(stored[0])   # правило №74 — то, с которым день собран: заказы рейсов не пропадут
    return RoutesView(depot=bundle.depot, geo_overrides=base.geo_overrides, workdays=workdays, holidays=holidays,
                      plan_exists=bool(draft.trips),
                      trips=tuple((t.truck, tuple(t.stops)) for t in draft.trips),
                      excluded=frozenset(draft.excluded), added=frozenset(draft.added),
                      carried=frozenset(carried), dropped=frozenset(draft.dropped),
                      agents_off=frozenset(dp.agents_off_of(draft, bundle.settings)), same_day=frozenset(draft.same_day),
                      taken=frozenset(taken), fleet=dp.fleet_rule_of(draft, bundle.settings), roads=roads)


def routes_depot(state: Any) -> Point | None:
    """Склад из настроек «Маршрутов» (без карты дорог и плана); раздела нет или база битая — None."""
    if state is None:
        return None
    try:
        return state.store.load().depot
    except RoutesStoreError:
        logger.warning('[Courier] База «Маршрутов» недоступна — склад не учтён', exc_info=True)
        return None


def _carried(state: Any, day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> set[str]:
    """«Везти завтра» из планов с прошлого рабочего дня по вчера (как views._carried «Маршрутов»)."""
    out: set[str] = set()
    d = dp.previous_workday(day, workdays, holidays)
    while d < day:
        stored = state.store.load_dispatch(d.isoformat())
        if stored is not None:
            out |= dp.Draft.from_json(stored[0]).deferred
        d += timedelta(days=1)
    return out


def _taken(state: Any, day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> set[str]:
    """Взятые в развоз дня их приёма (№72) в планах с прошлого рабочего дня по вчера (как views._same_day_taken)."""
    out: set[str] = set()
    d = dp.previous_workday(day, workdays, holidays)
    while d < day:
        stored = state.store.load_dispatch(d.isoformat())
        if stored is not None:
            out |= dp.Draft.from_json(stored[0]).same_day
        d += timedelta(days=1)
    return out


def orders_window(day: date, view: RoutesView) -> tuple[date, date]:
    """Даты заказов, которые смотрит «Развоз» для дня (с «не отгружены с прошлых дней»); логист взял в развоз заказы
    самого дня (№72) — и его дата."""
    since, until = dp.order_window(day, view.workdays, view.holidays)
    return dp.backlog_since(since, view.workdays, holidays=view.holidays), until + timedelta(days=1 if view.same_day else 0)


def pick_orders(orders: Sequence[DispatchOrder], day: date, view: RoutesView, car_code: str,
                places: Places | None = None) -> list[DispatchOrder]:
    """Заказы, которые «Развоз» отдал машине: отбор как на странице «Развоз» (заказы дня без «не везём
    сегодня» и без взятых в развоз дня их приёма в прошлые дни + добавленные и перенесённые прошлых дней + заказы
    самого дня, взятые в его развоз (№72), без заказов менеджеров, снятых фильтром; только заказы для машин парка по
    правилу дня, №74 — places: клиенты → (адрес, название) для городов-исключений, спрашивается только когда нужен;
    None — города не проверяются); план на дату есть — клиенты рейсов машины, нет — машина в самом заказе
    (ORDERS.fDELIVERYCAR)."""
    since, _ = dp.order_window(day, view.workdays, view.holidays)
    need = sorted({o.customer_id for o in orders if view.fleet.needs_place(o)})
    texts = places(need) if need and places is not None else {}
    sel = dp.to_deliver(orders, day, since, view.fleet, lambda cid: texts.get(cid, ('', '')))
    inside = set(view.added) | (set(view.carried) - set(view.dropped))
    active = [o for o in sel.main if o.isn not in view.excluded and o.isn not in view.taken
              and (o.order_date < day or o.isn in view.same_day)] + [o for o in sel.backlog if o.isn in inside]
    active = [o for o in active if o.agent_id not in view.agents_off]
    if view.plan_exists:
        mine = set(view.car_customers(car_code))
        return [o for o in active if o.customer_id in mine]
    return [o for o in active if o.car_code == car_code]


def invoice_owner(view: RoutesView, car_code: str) -> Callable[[int], bool]:
    """Клиент, чью накладную без машины (fDELIVERYCAR пуст) везёт машина: план есть — клиент только в рейсах этой
    машины (клиент в рейсах нескольких машин — накладная ничья, иначе одну сумму взяли бы два водителя; офис ставит
    машину в ERP, plan_mismatches это показывает); плана нет — заказ и так отобран по своей машине (ORDERS.fDELIVERYCAR)."""
    if not view.plan_exists:
        return lambda cid: True
    owners = view.plan_trucks()
    return lambda cid: owners.get(cid) == {car_code}


def distance_fn(view: RoutesView, points: Sequence[Point]) -> Distance | None:
    """Км по дорогам (карта «Маршрутов»), участок без дороги — по прямой × DETOUR; карты нет — None (tsp
    считает по прямой: постоянный множитель порядок не меняет)."""
    roads = view.roads
    if roads is None or getattr(roads, 'failed', False):
        return None
    try:
        roads.ensure(list(points))
    except Exception:
        logger.warning('[Courier] Расстояния по дорогам не посчитаны — по прямой', exc_info=True)
        return None
    if roads.failed:
        return None

    def km(a: Point, b: Point) -> float:
        d = roads.km(a, b)
        return d if d is not None else haversine_km(a, b) * DETOUR
    return km

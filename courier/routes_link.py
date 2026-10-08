# -*- coding: utf-8 -*-
"""Связь с разделом «Маршруты»: склад, ручные точки клиентов, рабочие дни, план «Развоза» на дату, экипаж машин, дороги.

Только чтение через публичный API route_optimizer (Store.load, Store.load_dispatch, Store.truck_drivers, Draft.from_json,
RoadProvider.get); запись — только экипаж машины из входа и решения экипажа (№84, record_crew → Store.save_apk_crew);
отметка выпуска плана (№80, Draft.released) — в route_optimizer/dispatch. Раздела нет — пустой вид: порядок «auto» от
склада не строится (склада нет), точки без ручных координат. База раздела не читается — RoutesStoreError (№80): пустой
вид значил бы «плана нет» — терминалы потеряли бы точки плана и /day сохранил бы урезанный снимок; ошибка оставляет
терминалу прежний день.

План «Развоза» идёт на терминал только выпущенный (ответ владельца №80): утверждён «Հաստատել օրվա պլանը» хотя бы раз за
день (Draft.released). До этого и без плана терминал не получает из плана ничего — ни заказов (O:), ни накладных без
машины по плану, ни порядка объезда плана (RoutesView.released); накладные ERP с машиной (SALES.fDELIVERYCAR) — всегда.
После выпуска терминал видит отправленный снимок плана (№81, Draft.sent): правки логиста — после «Ուղարկել վարորդներին».
Офис (courier.views.plan_mismatches) видит план и до выпуска.

Срок магазина у водителя (ответ владельца №93, контракт v1.8 §12): вид несёт окна приёма дня (Bundle.windows_on), запас
до срока (настройка until_buffer_min), плановый выезд рейсов машин (прогноз сборки) и дорожную модель «Развоза» для матрицы
рейса (route_optimizer.views.trip_road — вызывается только при сборке /day).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Collection, Mapping, Sequence

from route_optimizer import dispatch as dp
from route_optimizer.dispatch import DispatchOrder
from route_optimizer.geo import Point, haversine_km
from route_optimizer.store import CREW_BY_APK, Bundle, check_driver_name
from route_optimizer.store import StoreError as RoutesStoreError
from route_optimizer.tsp import Distance

logger = logging.getLogger(__name__)

DEFAULT_WORKDAYS = (1, 2, 3, 4, 5, 6)
Places = Callable[[Sequence[int]], Mapping[int, dp.Place]]   # клиенты → (адрес, название) из ERP (№74)
# клиенты рейса → дорожная модель «Развоза» дня (route_optimizer.views.trip_road, live.Road): матрица рейса /day (№93)
TripRoad = Callable[[Mapping[int, Point], str], Any]   # (клиенты, машина) → модель
_HHMM = re.compile(r'^(\d{1,2}):(\d{2})$')
DETOUR = 1.3   # участок без дороги — по прямой × извилистость (как evaluate.road_norms без калибровки)


@dataclass(frozen=True)
class RoutesView:
    depot: Point | None = None
    geo_overrides: Mapping[int, Point] = field(default_factory=dict)
    workdays: tuple[int, ...] = DEFAULT_WORKDAYS
    holidays: frozenset[date] = frozenset()                # нерабочие даты настроек (№64)
    plan_exists: bool = False                              # логист собрал план «Развоза» на дату
    released: bool = False                                 # план есть и выпущен на терминалы — утверждён хотя бы раз (№80)
    trips: tuple[tuple[str, tuple[int, ...]], ...] = ()    # (машина, клиенты по порядку) в порядке черновика
    excluded: frozenset[str] = frozenset()                 # заказы «не везём сегодня»
    added: frozenset[str] = frozenset()                    # заказы прошлых дней, добавленные логистом
    carried: frozenset[str] = frozenset()                  # «Везти завтра» прошлых дней и «Երբ տանել» на дату
    carried_since: date | None = None                      # самая ранняя дата заказа «Երբ տանել» на дату (orders_window)
    dropped: frozenset[str] = frozenset()                  # перенесённые сюда, но убранные логистом
    agents_off: frozenset[int] = frozenset()               # менеджеры, чьи заказы не везём (фильтр «Մենեջերներ»)
    same_day: frozenset[str] = frozenset()                 # заказы с датой дня, взятые в его развоз (№72)
    taken: frozenset[str] = frozenset()                    # взятые в развоз дня их приёма в прошлые дни — не везём
    seen: dp.PlanSeen = dp.PlanSeen()                      # что видел план прошлого рабочего дня (№79)
    fleet: dp.FleetRule = dp.NO_RULE                       # чьи заказы везут машины — правило настроек «Развоза» (№74)
    roads: Any = None                                      # RoadDistances | None
    # срок магазина (№93): окна приёма дня — клиент → (не раньше, не позже), минуты от полуночи (Bundle.windows_on)
    windows: Mapping[int, tuple[float, float]] = field(default_factory=dict)
    until_buffer_min: float = 0.0                         # запас до срока — настройка «Развоза» (№93)
    work_start_min: float = 540.0                         # начало дня машины — выезд рейса без прогноза
    departs: Mapping[str, tuple[float | None, ...]] = field(default_factory=dict)   # машина → плановый выезд рейсов
    road: TripRoad | None = None                           # дорожная модель для матрицы рейса; нет — матрицы нет

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
    """Вид на «Маршруты» для даты; state — app.extensions['route_optimizer'] (RoutesState) или None (раздела нет — пустой
    вид). База «Маршрутов» не читается — RoutesStoreError (№80)."""
    if state is None:
        return RoutesView()
    bundle = state.store.load()   # RoutesStoreError — наружу (№80, docstring модуля)
    workdays = tuple(bundle.settings.get('workdays') or DEFAULT_WORKDAYS)
    holidays = dp.holidays_of(bundle.settings)
    roads = None
    try:
        roads = state.roads.get() if getattr(state, 'roads', None) is not None else None
    except Exception:   # карта дорог — необязательна: сбой → по прямой
        logger.warning('[Courier] Карта дорог недоступна — порядок по прямой', exc_info=True)
    stored = state.store.load_dispatch(day.isoformat())   # RoutesStoreError — наружу (№80): не «плана нет»
    s = bundle.settings
    h, m = map(int, s['truck_work_start'].split(':'))
    # №93: срок магазина на день, запас и матрица рейса — для терминала (courier.day)
    timing = dict(windows={cid: w.span() for cid, w in bundle.windows_on(day).items()},
                  until_buffer_min=float(s['until_buffer_min']), work_start_min=float(h * 60 + m),
                  road=lambda customers, car: _trip_road(state, day, customers, car))
    base = RoutesView(depot=bundle.depot, geo_overrides=dict(bundle.geo_overrides), workdays=workdays,
                      holidays=holidays, fleet=dp.FleetRule.from_settings(bundle.settings), roads=roads, **timing)
    load = _plans(state)
    later = _later(load, day, workdays, holidays)
    carried = _carried(state, day, workdays, holidays, load) | set(later)
    carried_since = min(later.values(), default=None)
    taken = _taken(state, day, workdays, holidays, load)
    seen = dp.PlanSeen.of(load(dp.previous_workday(day, workdays, holidays)))   # №81
    if stored is None:
        # плана нет — менеджеры, чьи заказы не везём, по правилу настроек «Развоза» (№69)
        return RoutesView(depot=base.depot, geo_overrides=base.geo_overrides, workdays=workdays, holidays=holidays,
                          carried=frozenset(carried), carried_since=carried_since,
                          agents_off=frozenset(dp.agents_off_of(None, bundle.settings)),
                          taken=frozenset(taken), fleet=base.fleet, roads=roads, seen=seen, **timing)
    # водителям — отправленный план (№81, Draft.for_drivers), не черновик с неотправленными правками; правило №74 — то, с
    # которым день собран: заказы рейсов не пропадут
    draft = dp.Draft.from_json(stored[0]).for_drivers()
    return RoutesView(depot=bundle.depot, geo_overrides=base.geo_overrides, workdays=workdays, holidays=holidays,
                      plan_exists=bool(draft.trips), released=bool(draft.trips) and draft.released is not None,
                      trips=tuple((t.truck, tuple(t.stops)) for t in draft.trips),
                      excluded=frozenset(draft.excluded), added=frozenset(draft.added),
                      carried=frozenset(carried), carried_since=carried_since, dropped=frozenset(draft.dropped),
                      agents_off=frozenset(dp.agents_off_of(draft, bundle.settings)), same_day=frozenset(draft.same_day),
                      taken=frozenset(taken), fleet=dp.fleet_rule_of(draft, bundle.settings), roads=roads, seen=seen,
                      departs=_departs(draft), **timing)


def _trip_road(state: Any, day: date, customers: Mapping[int, Point], car: str) -> Any:
    """Дорожная модель «Развоза» дня по точкам рейсов машины (route_optimizer.views.trip_road — раздел подключён, его
    модуль уже загружен); ошибки — наружу (courier.day: матрицы нет)."""
    from route_optimizer.views import trip_road
    return trip_road(state, day, customers, car)


def _departs(draft: dp.Draft) -> dict[str, tuple[float | None, ...]]:
    """Плановый выезд рейсов машин (минуты от полуночи) из прогноза сборки отправленного плана: машина → по рейсам плана;
    в прогнозе другое число рейсов машины (логист менял рейсы после сборки) или выезда нет — None."""
    pred = (draft.prediction or {}).get('trucks') or {}
    out: dict[str, tuple[float | None, ...]] = {}
    for car in {t.truck for t in draft.trips}:
        n = sum(1 for t in draft.trips if t.truck == car)
        mine = pred.get(car) if isinstance(pred.get(car), Mapping) else {}
        trips = [t for t in mine.get('trips') or () if isinstance(t, Mapping)]
        hm = [_HHMM.match(t.get('depart')) if isinstance(t.get('depart'), str) else None for t in trips]
        out[car] = tuple(int(x[1]) * 60 + int(x[2]) if x else None for x in hm) if len(trips) == n else (None,) * n
    return out


def routes_depot(state: Any) -> Point | None:
    """Склад из настроек «Маршрутов» (без карты дорог и плана); раздела нет или база битая — None."""
    if state is None:
        return None
    try:
        return state.store.load().depot
    except RoutesStoreError:
        logger.warning('[Courier] База «Маршрутов» недоступна — склад не учтён', exc_info=True)
        return None


def routes_bundle(state: Any) -> Bundle | None:
    """Настройки «Маршрутов» (Store.load: таблица парка, вкл. ручные машины) для списка машин терминалов
    (erp_day.merge_cars). Раздела нет или любой сбой «Маршрутов» — None (сбой — в лог): список строится по ERP."""
    if state is None:
        return None
    try:
        return state.store.load()
    except Exception:   # парк — подсказка списка машин: сбой «Маршрутов» не закрывает регистрацию терминала
        logger.warning('[Courier] Парк «Маршрутов» не прочитан — машины терминалов только по ERP', exc_info=True)
        return None


def planned_crew(state: Any, day: date) -> dict[str, dict[str, str | None]] | None:
    """Экипаж машин по плану «Развоза» на дату (№62, Store.truck_drivers): машина → {'driver': имя | None, 'helper':
    имя | None}; машины без записей — нет в словаре. Раздела нет или его база битая — None (не ошибка запроса)."""
    if state is None:
        return None
    try:
        drivers, _ = state.store.truck_drivers(day.isoformat(), 'driver')
        helpers, _ = state.store.truck_drivers(day.isoformat(), 'helper')
    except Exception:   # экипаж плана — подсказка: любой сбой «Маршрутов» не роняет терминал и офис
        logger.warning('[Courier] Экипаж плана «Развоза» на %s не прочитан', day, exc_info=True)
        return None
    return {car: {'driver': drivers.get(car), 'helper': helpers.get(car)} for car in sorted({*drivers, *helpers})}


def driver_name_hints(state: Any) -> list[str]:
    """Имена водителей «Развоза» (ERP и свои, route_optimizer.views.driver_name_hints) — подсказка имени в офисе: правила
    №84 сравнивают имя водителя APK с записями «Маршрутов» строкой. Раздела нет или любой сбой — пусто (в лог)."""
    if state is None:
        return []
    try:
        from route_optimizer.views import driver_name_hints as hints   # раздел подключён — его модуль уже загружен
        return hints(state)
    except Exception:   # подсказка — необязательна: сбой «Маршрутов» не мешает офису
        logger.warning('[Courier] Имена водителей «Маршрутов» не прочитаны — без подсказки', exc_info=True)
        return []


def record_crew(state: Any, car_code: str, day: date, role: str, name: str, only_day: bool, terminal_id: int) -> bool:
    """Экипаж машины из «Առաքիչ» — в «Маршруты» (ответ владельца №84: терминал привязан к машине, вход по PIN — её
    водитель, решение экипажа — её առաքիչ): Store.save_apk_crew с меткой CREW_BY_APK + id терминала, день — рабочий день
    события по Еревану. Зовётся после записи в courier.db; только машины из настроек «Маршрутов». Необязательно для
    терминала: раздела нет — ничего; негодное имя и любой сбой «Маршрутов» — только в лог, ответ терминалу тот же. True —
экипаж в «Маршрутах» изменён."""
    if state is None:
        return False
    try:
        clean, bad = check_driver_name(name)
        if bad is not None or (not clean and (name or not only_day)):   # «никого» — только «один» на день
            logger.warning('[Courier] Экипаж машины %s не передан в «Маршруты»: имя не прошло проверку', car_code)
            return False
        if not state.store.save_apk_crew(car_code, day.isoformat(), role, clean, only_day, f'{CREW_BY_APK}{terminal_id}'):
            return False
    except Exception:   # «Маршруты» — дополнение: вход и экипаж терминала от них не зависят
        logger.warning('[Courier] Экипаж машины %s не передан в «Маршруты»', car_code, exc_info=True)
        return False
    logger.info('[Courier] Экипаж машины %s на %s из терминала %s: %s%s', car_code, day, terminal_id, role,
                ' (на день)' if only_day else '')
    return True


def _plans(state: Any) -> Callable[[date], dp.Draft | None]:
    """Прошлые планы дня, отправленные водителям (№81, Draft.for_drivers): день → план | None (плана нет); каждый читается и
    разбирается один раз за вид. База не читается — RoutesStoreError (и при повторе)."""
    memo: dict[date, dp.Draft | RoutesStoreError | None] = {}

    def load(d: date) -> dp.Draft | None:
        if d not in memo:
            try:
                stored = state.store.load_dispatch(d.isoformat())
                memo[d] = dp.Draft.from_json(stored[0]).for_drivers() if stored is not None else None
            except RoutesStoreError as e:
                memo[d] = e
        out = memo[d]
        if isinstance(out, RoutesStoreError):
            raise out
        return out
    return load


def _carried(state: Any, day: date, workdays: Sequence[int],
             holidays: Collection[date] = (), load: Callable[[date], dp.Draft | None] | None = None) -> set[str]:
    """«Везти завтра» из планов с прошлого рабочего дня по вчера (как views._carried «Маршрутов»); load — общее чтение
    планов вида (_plans)."""
    load = load or _plans(state)
    out: set[str] = set()
    d = dp.previous_workday(day, workdays, holidays)
    while d < day:
        plan = load(d)   # RoutesStoreError — наружу (№80)
        if plan is not None:
            out |= plan.deferred
        d += timedelta(days=1)
    return out


def _later(load: Callable[[date], dp.Draft | None], day: date, workdays: Sequence[int],
           holidays: Collection[date] = ()) -> dict[str, date]:
    """«Երբ տանել» на дату из планов LATER_SCAN_DAYS дней до неё (dp.later_carried, как views._later_scan «Маршрутов»):
    isn → дата заказа. Непрочитанный старый план — без его переносов (в журнал), как на странице: один битый план не
    закрывает все терминалы; планы, которые вид читает строго (свой день, прошлый рабочий), падают и так."""
    plans: list[tuple[date, dp.Draft]] = []
    d = day - timedelta(days=dp.LATER_SCAN_DAYS)
    while d < day:
        try:
            plan = load(d)
        except RoutesStoreError:
            logger.warning('[Courier] План «Развоза» на %s не прочитан — его «Երբ տանել» на %s не учтены', d, day,
                           exc_info=True)
            plan = None
        if plan is not None:
            plans.append((d, plan))
        d += timedelta(days=1)
    return {isn: od for isn, (to, od, _) in dp.later_carried(plans, workdays, holidays).items() if to == day}


def _taken(state: Any, day: date, workdays: Sequence[int],
           holidays: Collection[date] = (), load: Callable[[date], dp.Draft | None] | None = None) -> set[str]:
    """Взятые в развоз дня их приёма (№72) в планах с прошлого рабочего дня по вчера (как views._same_day_taken); load —
    общее чтение планов вида (_plans)."""
    load = load or _plans(state)
    out: set[str] = set()
    d = dp.previous_workday(day, workdays, holidays)
    while d < day:
        plan = load(d)   # RoutesStoreError — наружу (№80)
        if plan is not None:
            out |= plan.same_day
        d += timedelta(days=1)
    return out


def orders_window(day: date, view: RoutesView) -> tuple[date, date]:
    """Даты заказов, которые смотрит «Развоз» для дня (с «не отгружены с прошлых дней», и с даты самого раннего заказа
    «Երբ տանել» на день — carried_since) и сам день: заказы, заведённые заранее на него (№79), и заказы дня, взятые
    логистом в его развоз (№72). Лишние старые заказы pick_orders не берёт: заказ прошлых дней — только в развозе дня."""
    since, until = dp.order_window(day, view.workdays, view.holidays)
    first = dp.backlog_since(since, view.workdays, holidays=view.holidays)
    return min(first, view.carried_since) if view.carried_since is not None else first, until + timedelta(days=1)


def pick_orders(orders: Sequence[DispatchOrder], day: date, view: RoutesView, car_code: str,
                places: Places | None = None) -> list[DispatchOrder]:
    """Заказы, которые «Развоз» отдал машине: отбор как на странице «Развоз» (заказы дня без «не везём
    сегодня» и без взятых в развоз дня их приёма в прошлые дни + добавленные и перенесённые прошлых дней + заказы
    самого дня, взятые в его развоз (№72), без заказов менеджеров, снятых фильтром; только заказы для машин парка по
    правилу дня, №74 — places: клиенты → (адрес, название) для городов-исключений, спрашивается только когда нужен;
    None — города не проверяются); клиенты рейсов машины в плане на дату. План не выпущен (не утверждён ни разу, №80) или
    его нет — ничего: терминал ждёт утверждения (прежний запасной отбор по машине заказа ORDERS.fDELIVERYCAR снят)."""
    if not view.released:
        return []
    since, _ = dp.order_window(day, view.workdays, view.holidays)
    need = sorted({o.customer_id for o in orders if view.fleet.needs_place(o)})
    texts = places(need) if need and places is not None else {}
    place = lambda cid: texts.get(cid, ('', ''))   # noqa: E731
    sel = dp.settle_predated(dp.to_deliver(orders, day, since, view.fleet, place), since, view.seen)
    inside = set(view.added) | (set(view.carried) - set(view.dropped))
    same = [o for o in dp.same_day_candidates(orders, day, view.fleet, place) if o.isn in view.same_day]
    # заказ прошлых дней, взятый логистом «Տանել այսօր», — и при менеджере, снятом фильтром (как на странице)
    active = [o for o in sel.main + same if o.isn not in view.excluded and o.isn not in view.taken
              and o.agent_id not in view.agents_off] \
        + [o for o in sel.backlog if dp.backlog_delivered(o, inside, view.added, view.agents_off)]
    mine = set(view.car_customers(car_code))
    return [o for o in active if o.customer_id in mine]


def invoice_owner(view: RoutesView, car_code: str) -> Callable[[int], bool]:
    """Клиент, чью накладную без машины (fDELIVERYCAR пуст) везёт машина: план выпущен — клиент только в рейсах этой
    машины (клиент в рейсах нескольких машин — накладная ничья, иначе одну сумму взяли бы два водителя; офис ставит
    машину в ERP, plan_mismatches это показывает); план не выпущен или его нет (№80) — ничья."""
    if not view.released:
        return lambda cid: False
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

# -*- coding: utf-8 -*-
"""План развоза на завтра (docs/plans/dispatch-plan.md): заказы дня → рейсы машин, правки логиста,
сравнение «по менеджерам» и «план и факт».

Чистая логика — без Flask, БД и ERP. Единицы: км, минуты, кг, драмы.
- Заказы к доставке в день D — проведённые заказы ERP с датой от предыдущего рабочего дня до D
  (правило владельца №5/№19: заказы дня везут на следующий рабочий день, субботние и воскресные —
  в понедельник), кроме уже отгруженных до D (реализация SALES с датой раньше D).
- «Не отгружены с прошлых дней» — заказы ещё BACKLOG_WORKDAYS рабочих дней раньше, без реализации
  до D: по данным сентября около половины их везут в D, остальные не везут вовсе — поэтому в план
  они не входят, пока логист не добавит их сам (Draft.added).
- Остановка — клиент: все его заказы дня одной точкой. Координата — ручная точка логиста → адрес
  клиента в ERP → медиана GPS визитов (evaluate.visit_coord).
- Рейсы — fleet.route_day (Кларк–Райт, 2-opt, тоннаж, рабочий день машины, разгрузка); тяжёлый
  заказ — несколько поездок поровну: клиент встречается в k рейсах — в каждом 1/k его кг. Сборка разгружает
  рейсы тяжелее 90% тоннажа (5 т + 3 т → 4 т + 4 т, ответ владельца №44), если км и литры растут не больше 3%.
- Черновик плана (store.dispatch_plan) — машины дня, исключённые заказы и рейсы (клиенты по порядку
  объезда, машина, «закреплён»). Цифры рейсов всегда пересчитываются по текущим заказам: новые
  заказы попадают в «ещё не в рейсах», исчезнувшие — убираются из рейсов.
- Свежесть заказов: до dispatch_ready_time в последний день приёма заказов на D они ещё поступают
  (orders_still_coming); черновик помнит заказы последней сборки (Draft.built_orders) — новые и
  отменённые/отгруженные с тех пор считает since_build.
- Окна приёма магазинов и малый центр (windows-center-plan.md): окно — прибытие к магазину (раньше — машина
  ждёт, ожидание — её время), центр — только машины с правом въезда. Сборка и «Везти после конца дня» их
  соблюдают (fleet._plan_timed); что не помещается в окно или без машины для центра — no_window / no_center.
  Правки логиста не запрещаются: нарушение видно в плане (window_miss, center_miss).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Callable, Collection, Mapping, Sequence

from . import fleet as fl
from . import vrp
from .geo import ERP_GPS_MAX_GAP_KM, Coord, Point, in_polygon
from .running_costs import configured
from .vehicle_access import VehicleAccess

if TYPE_CHECKING:
    from .evaluate import Norms

ISN_RE = re.compile(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$')
_EPS = 1e-6
MAX_TRIPS = 500           # защита от битого черновика
MAX_BUILT_ORDERS = 20000  # заказов в отметке сборки — тоже защита от битого черновика
BACKLOG_WORKDAYS = 2      # «не отгружены с прошлых дней» — заказы ещё двух рабочих дней раньше окна
BYPASS_MIN_KM = 0.01      # пояснение рейса: участок длиннее из-за объезда центра хотя бы на 10 м — «в объезд»
WEEKDAY_FULL = {1: 'понедельник', 2: 'вторник', 3: 'среда', 4: 'четверг', 5: 'пятница', 6: 'суббота',
                7: 'воскресенье'}


class DispatchError(ValueError):
    """Правка логиста не применима (текст — для пользователя)."""


# --- Данные ERP ---

@dataclass(frozen=True)
class DispatchOrder:
    """Заказ ERP (ORDERS): кг — строки заказа × вес товара; shipped — дата первой проведённой
    реализации по заказу (None — ещё не отгружен)."""
    isn: str
    doc_num: str
    order_date: date
    customer_id: int
    agent_id: int
    car_code: str          # машина в заказе (ORDERS.fDELIVERYCAR), '' — не указана
    revenue: float
    kg: float
    shipped: date | None
    van_agent_id: int = 0  # кто везёт (ORDERS.fVANAGENTID): сам менеджер — не для машин парка

    @property
    def self_delivery(self) -> bool:
        return self.van_agent_id != 0 and self.van_agent_id == self.agent_id


@dataclass(frozen=True)
class ShippedDoc:
    """Реализация за прошедший день (SALES): кто и на какой машине вёз. van_agent_id — кто вёз
    (SALES.fVANAGENTID): экспедиторы возят без машины в накладной — тогда car_code пуст."""
    customer_id: int
    agent_id: int
    car_code: str
    revenue: float
    kg: float
    van_agent_id: int = 0


def fact_car(d: ShippedDoc, van_trucks: Mapping[int, str]) -> str:
    """Машина, которая фактически везла накладную: машина в накладной; без неё — ручная машина экспедитора
    (store.Bundle.van_trucks). Менеджер развозит сам (fVANAGENTID = fSALESAGENTID) — не машина парка: ''."""
    if d.car_code:
        return d.car_code
    if d.van_agent_id and d.van_agent_id != d.agent_id:
        return van_trucks.get(d.van_agent_id, '')
    return ''


@dataclass(frozen=True)
class DispatchData:
    orders: tuple[DispatchOrder, ...]
    customers: dict[int, tuple[str, str]]          # клиент → (код, название)
    addresses: dict[int, str]                      # клиент → адрес текстом
    # менеджер → кто возил за 90 дней, от самого частого: код машины ERP или id экспедитора без машины (int)
    agent_cars: dict[int, tuple[str | int, ...]]
    loaded_at: datetime


@dataclass(frozen=True)
class FactData:
    docs: tuple[ShippedDoc, ...]
    customers: dict[int, tuple[str, str]]


# --- Дни ---

def previous_workday(day: date, workdays: Sequence[int]) -> date:
    """Последний рабочий день строго раньше day (дни недели 1 = пн … 7 = вс)."""
    wd = set(workdays) or set(range(1, 8))
    d = day - timedelta(days=1)
    while d.isoweekday() not in wd:
        d -= timedelta(days=1)
    return d


def next_workday(day: date, workdays: Sequence[int]) -> date:
    """Первый рабочий день строго позже day — «завтра» для логиста (в субботу — понедельник)."""
    wd = set(workdays) or set(range(1, 8))
    d = day + timedelta(days=1)
    while d.isoweekday() not in wd:
        d += timedelta(days=1)
    return d


def order_window(day: date, workdays: Sequence[int]) -> tuple[date, date]:
    """Даты заказов, которые везут в day: [предыдущий рабочий день, day). В понедельник — заказы
    субботы и воскресенья."""
    return previous_workday(day, workdays), day


def backlog_since(since: date, workdays: Sequence[int], n: int = BACKLOG_WORKDAYS) -> date:
    """Начало окна «не отгружены с прошлых дней»: n рабочих дней раньше окна заказов дня."""
    for _ in range(n):
        since = previous_workday(since, workdays)
    return since


@dataclass(frozen=True)
class Selection:
    main: list[DispatchOrder]            # заказы дня к доставке
    backlog: list[DispatchOrder]         # не отгружены с прошлых дней (в план — только по выбору логиста)
    shipped_before: int                  # заказов дня уже отгружено раньше дня развоза
    self_delivery: list[DispatchOrder]   # заказы дня, которые менеджер развозит сам


def to_deliver(orders: Sequence[DispatchOrder], day: date, since: date) -> Selection:
    """Заказы к доставке в day: с датой от since — заказы дня, раньше — «не отгружены с прошлых дней».
    Отгруженные в day и позже — к доставке: для прошедшей даты это и есть то, что везли (или должны
    были везти). Заказы, которые менеджер развозит сам, машинам парка не достаются."""
    key = lambda o: (o.customer_id, o.order_date, o.isn)   # noqa: E731
    pending = [o for o in orders if o.shipped is None or o.shipped >= day]
    trucks = [o for o in pending if not o.self_delivery]
    return Selection(main=sorted((o for o in trucks if o.order_date >= since), key=key),
                     backlog=sorted((o for o in trucks if o.order_date < since), key=key),
                     shipped_before=sum(1 for o in orders if o.order_date >= since) - len(
                         [o for o in pending if o.order_date >= since]),
                     self_delivery=sorted((o for o in pending if o.self_delivery and o.order_date >= since), key=key))


# --- Свежесть заказов ---

def orders_still_coming(day: date, workdays: Sequence[int], now: datetime, ready_time: str) -> bool:
    """Заказы на day ещё поступают: сегодня — последний день приёма заказов на day (предыдущий рабочий
    день, для понедельника — суббота) и сейчас раньше ready_time (ЧЧ:ММ). Прошедшая дата, выходной
    перед днём развоза или дата через несколько дней — False."""
    if now.date() != previous_workday(day, workdays):
        return False
    h, m = map(int, ready_time.split(':'))
    return now.time() < time(h, m)


def order_marks(orders: Sequence[DispatchOrder]) -> dict[str, tuple[float, float]]:
    """Отметка сборки: какие заказы (кг, сумма) логист видел, когда собирал рейсы."""
    return {o.isn: (o.kg, o.revenue) for o in orders}


def _totals(items: Sequence[tuple[float, float]]) -> dict[str, Any]:
    return {'count': len(items), 'kg': round(math.fsum(kg for kg, _ in items)),
            'revenue': round(math.fsum(rev for _, rev in items))}


def since_build(built: Mapping[str, tuple[float, float]] | None, main: Sequence[DispatchOrder],
                pending: Sequence[DispatchOrder], excluded: set[str]) -> dict[str, Any] | None:
    """Что изменилось с последней сборки. new — заказы дня (и перенесённые сюда из прошлого дня), которых
    при сборке не было (кроме тех, что логист уже исключил); removed — заказы сборки, которых больше нет
    в развозе дня (отменили, уже отгрузили, сняли перенос; их кг и сумма — из отметки). pending — заказы
    развоза дня: заказы дня и прошлых дней, что в нём. Отметки нет — None."""
    if built is None:
        return None
    new = [(o.kg, o.revenue) for o in main if o.isn not in built and o.isn not in excluded]
    alive = {o.isn for o in pending}
    removed = [v for k, v in sorted(built.items()) if k not in alive]
    return {'new': _totals(new), 'removed': _totals(removed)}


# --- Остановки ---

@dataclass(frozen=True)
class Stop:
    customer_id: int
    point: Point | None
    coord_source: str
    kg: float
    revenue: float
    orders: tuple[DispatchOrder, ...]
    agent_id: int            # менеджер самого тяжёлого заказа клиента


def build_stops(orders: Sequence[DispatchOrder], coord: Callable[[int], Coord]) -> list[Stop]:
    """Клиент с заказами дня — одна остановка; порядок — по коду клиента (детерминизм)."""
    by: dict[int, list[DispatchOrder]] = {}
    for o in orders:
        by.setdefault(o.customer_id, []).append(o)
    out = []
    for cid in sorted(by):
        os = by[cid]
        c = coord(cid)
        main = max(os, key=lambda o: (o.kg, o.revenue, o.isn))
        out.append(Stop(cid, c.point, c.source, math.fsum(o.kg for o in os), math.fsum(o.revenue for o in os),
                        tuple(os), main.agent_id))
    return out


# --- Черновик ---

@dataclass
class DraftTrip:
    id: int
    truck: str
    stops: list[int]          # клиенты по порядку объезда
    pinned: bool = False


@dataclass
class Draft:
    trucks: list[str] = field(default_factory=list)       # машины дня (выбраны логистом)
    excluded: set[str] = field(default_factory=set)       # заказы «не везём сегодня» (fISN)
    added: set[str] = field(default_factory=set)          # заказы прошлых дней, добавленные логистом
    trips: list[DraftTrip] = field(default_factory=list)
    next_id: int = 1
    built_at: str | None = None
    # заказы последней сборки: fISN → (кг, сумма); None — черновик собран до этой отметки (сравнивать не с чем)
    built_orders: dict[str, tuple[float, float]] | None = None
    # клиенты, которых сборка не успела развезти до конца рабочего дня выбранными машинами
    no_room: set[int] = field(default_factory=set)
    # хоть одна машина в плане работает дольше рабочего дня (форс-мажор, ответ владельца №32) — runs_late;
    # пересчитывается при каждом сохранении (сборка, правка, «Везти после конца дня»); по ней — счётчик дней
    overtime: bool = False
    # логист нажал «Везти после конца дня»: рейсы до предела (truck_overtime_end) — принятая переработка,
    # а не ошибка; новая сборка сбрасывает
    overtime_ok: bool = False
    # заказы (fISN), перенесённые на следующий день доставки («Везти завтра», №25): следующий день сам
    # берёт их в развоз при загрузке (carried), искать их среди «не отгружены с прошлых дней» не нужно
    deferred: set[str] = field(default_factory=set)
    # перенесённые сюда из прошлого дня (carried), которые логист этого дня убрал из развоза
    dropped: set[str] = field(default_factory=set)
    # клиенты, которых сборка не поставила в рейс: не успеваем в окно приёма / в центре, а машины с правом
    # въезда сегодня нет (no_room — только «не успели до конца дня»); перенос в рейс убирает из всех трёх
    no_window: set[int] = field(default_factory=set)
    no_center: set[int] = field(default_factory=set)
    prediction: dict[str, Any] | None = None
    no_vehicle: set[int] = field(default_factory=set)

    def to_json(self) -> dict[str, Any]:
        built = None if self.built_orders is None else {
            k: [round(kg, 3), round(rev, 2)] for k, (kg, rev) in sorted(self.built_orders.items())}
        return {'trucks': list(self.trucks), 'excluded': sorted(self.excluded), 'added': sorted(self.added),
                'trips': [{'id': t.id, 'truck': t.truck, 'stops': list(t.stops), 'pinned': t.pinned}
                          for t in self.trips],
                'next_id': self.next_id, 'built_at': self.built_at, 'built_orders': built,
                'no_room': sorted(self.no_room), 'overtime': self.overtime, 'overtime_ok': self.overtime_ok,
                'deferred': sorted(self.deferred), 'dropped': sorted(self.dropped),
                'no_window': sorted(self.no_window), 'no_center': sorted(self.no_center), 'prediction': self.prediction,
                'no_vehicle': sorted(self.no_vehicle)}

    @classmethod
    def from_json(cls, raw: Any) -> Draft:
        """Черновик из базы; битые элементы пропускаются (черновик — не источник истины)."""
        if not isinstance(raw, dict):
            return cls()
        trucks = [t for t in raw.get('trucks') or [] if isinstance(t, str)]
        excluded = {x for x in raw.get('excluded') or [] if isinstance(x, str) and ISN_RE.match(x)}
        added = {x for x in raw.get('added') or [] if isinstance(x, str) and ISN_RE.match(x)}
        trips: list[DraftTrip] = []
        seen: set[int] = set()
        for t in (raw.get('trips') or [])[:MAX_TRIPS]:
            if not isinstance(t, dict):
                continue
            tid, truck, stops = t.get('id'), t.get('truck'), t.get('stops')
            if not _is_int(tid) or tid in seen or not isinstance(truck, str) or not isinstance(stops, list):
                continue
            seen.add(tid)
            trips.append(DraftTrip(tid, truck, [c for c in stops if _is_int(c)], t.get('pinned') is True))
        next_id = raw.get('next_id')
        next_id = max([next_id if _is_int(next_id) else 1, *(t.id + 1 for t in trips)])
        built = raw.get('built_at') if isinstance(raw.get('built_at'), str) else None
        def cids(key: str) -> set[int]:
            return {c for c in (raw.get(key) or [])[:MAX_BUILT_ORDERS] if _is_int(c)}

        def isns(key: str) -> set[str]:
            return {x for x in (raw.get(key) or [])[:MAX_BUILT_ORDERS] if isinstance(x, str) and ISN_RE.match(x)}
        return cls(trucks, excluded, added, trips, next_id, built, _built_orders(raw.get('built_orders')), cids('no_room'),
                   raw.get('overtime') is True, raw.get('overtime_ok') is True, isns('deferred'), isns('dropped'),
                   cids('no_window'), cids('no_center'), raw.get('prediction') if isinstance(raw.get('prediction'), dict) else None,
                   no_vehicle=cids('no_vehicle'))


def _built_orders(raw: Any) -> dict[str, tuple[float, float]] | None:
    """Отметка сборки из черновика; нет её (старый черновик) или не словарь — None, битые строки — мимо."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, tuple[float, float]] = {}
    for k, v in list(raw.items())[:MAX_BUILT_ORDERS]:
        if isinstance(k, str) and ISN_RE.match(k) and isinstance(v, list) and len(v) == 2 \
                and all(_is_num(x) for x in v):
            out[k] = (float(v[0]), float(v[1]))
    return out


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# --- Контекст расчёта ---

@dataclass(frozen=True)
class DayContext:
    day: date
    depot: Point
    trucks: dict[str, fl.FleetTruck]     # машины, готовые к расчёту (активны, тоннаж и расход заданы)
    norms: Norms
    tn: fl.TruckNorms
    work_start_min: int                  # начало рабочего дня машины, минут от полуночи
    overtime_minutes: float | None = None   # форс-мажор: длина дня машины до truck_overtime_end; None — без предела
    min_trip_revenue: float = 0.0        # рейс дешевле — «бедный» (ответ владельца №25: везти сейчас или завтра)
    # окна приёма: клиент → (прибыть не раньше, не позже), минуты от полуночи (store.CustomerWindow.span)
    windows: Mapping[int, tuple[float, float]] = field(default_factory=dict)
    center_zone: tuple[Point, ...] = ()  # граница малого центра; пусто — центра нет
    vehicle_access: Mapping[int, VehicleAccess] = field(default_factory=dict)
    # что учитывает расчёт (views._dispatch_ctx: откуда км и минуты, выученные нормы) — только для пояснения на странице
    model: Mapping[str, Any] = field(default_factory=dict)


def _hhmm(minutes: float) -> str:
    """Минуты от полуночи дня доставки → «17:32»; следующие сутки — «04:06 (+1)»."""
    m = int(round(minutes))
    days = m // (24 * 60)
    text = f'{m // 60 % 24:02d}:{m % 60:02d}'
    return f'{text} (+{days})' if days else text


def _selected(ctx: DayContext, codes: Sequence[str]) -> list[fl.FleetTruck]:
    return [ctx.trucks[c] for c in sorted(set(codes)) if c in ctx.trucks]


def _shares(trips: Sequence[DraftTrip]) -> dict[int, int]:
    """Клиент → в скольких рейсах он встречается (тяжёлый заказ — несколько поездок поровну)."""
    n: dict[int, int] = {}
    for t in trips:
        for c in t.stops:
            n[c] = n.get(c, 0) + 1
    return n


def _clean(draft: Draft, routable: Mapping[int, Stop]) -> None:
    """Из рейсов уходят клиенты, которых нет среди заказов с координатами; пустые рейсы — тоже.
    Повтор клиента в одном рейсе схлопывается."""
    for t in draft.trips:
        seen: set[int] = set()
        t.stops = [c for c in t.stops if c in routable and not (c in seen or seen.add(c))]
    draft.trips = [t for t in draft.trips if t.stops]


def _span(ctx: DayContext, cid: int) -> fl.Window:
    """Окно приёма клиента в минутах от начала дня машины; без окна — (−∞, +∞)."""
    lo, hi = ctx.windows.get(cid, (-math.inf, math.inf))
    return lo - ctx.work_start_min, hi - ctx.work_start_min


def _central(ctx: DayContext, s: Stop) -> bool:
    """Точка в малом центре (везёт только машина с правом въезда)."""
    return bool(ctx.center_zone) and s.point is not None and in_polygon(s.point, ctx.center_zone)


def _allowed_trucks(ctx: DayContext, cid: int) -> frozenset[str] | None:
    rule = ctx.vehicle_access.get(cid)
    return None if rule is None else frozenset(code for code in ctx.trucks if rule.allows(code))


def _vehicle_ok(ctx: DayContext, cid: int, code: str) -> bool:
    rule = ctx.vehicle_access.get(cid)
    return rule is None or rule.allows(code)


def _require_vehicle(ctx: DayContext, cids: Sequence[int], code: str) -> None:
    if any(not _vehicle_ok(ctx, cid, code) for cid in cids):
        raise DispatchError('Эта машина не может обслуживать магазин: выберите разрешённую машину. '
                            'Для закреплённого рейса сначала измените машину или снимите закрепление.')


def _route(ctx: DayContext, cids: Sequence[int], stops: Mapping[int, Stop], shares: Mapping[int, int],
           reorder: bool, start: float = 0.0) -> tuple[list[int], float, float, float]:
    """(порядок клиентов, км, минуты, кг) рейса. reorder с выезда start: если порядок соблюдает окна приёма,
    2-opt их не нарушит."""
    kgs = [stops[c].kg / shares.get(c, 1) for c in cids]
    windows = [_span(ctx, c) for c in cids] if reorder and any(c in ctx.windows for c in cids) else None
    seq, km, minutes = fl.route_trip([stops[c].point for c in cids], kgs, ctx.depot, ctx.norms, ctx.tn,
                                     reorder=reorder, windows=windows, start=start)
    return [cids[i] for i in seq], km, minutes, math.fsum(kgs)


def _timeline(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop],
              shares: Mapping[int, int], parts: dict[int, dict[str, Any]] | None = None
              ) -> dict[int, tuple[float, float, list[float]]]:
    """Рейсы машин подряд, с ожиданием у окон приёма: рейс → (выезд, минуты рейса от выезда, прибытия к точкам),
    время — минуты от начала дня машины. Рейс выезжает, как только машина вернулась, но не раньше, чем нужно к
    окну первой точки (fl.trip_schedule) — как его поставил fleet._plan_timed. Без окон — подряд, минуты те же,
    что у _route. parts — рейс → слагаемые его минут (fl.trip_schedule: загрузка, езда, ожидание, разгрузка)."""
    used: dict[str, float] = {}
    out: dict[int, tuple[float, float, list[float]]] = {}
    for t in trips:
        cids = [c for c in t.stops if c in stops]
        if not cids:
            continue
        got: dict[str, Any] | None = {} if parts is not None else None
        depart, arrivals, minutes = fl.trip_schedule(
            [stops[c].point for c in cids], [stops[c].kg / shares.get(c, 1) for c in cids], ctx.depot, ctx.norms,
            ctx.tn, used.get(t.truck, 0.0), [_span(ctx, c) for c in cids], got)
        used[t.truck] = depart + minutes
        out[t.id] = (depart, minutes, arrivals)
        if parts is not None:
            parts[t.id] = got
    return out


def _occupied(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop], shares: Mapping[int, int]
              ) -> tuple[dict[str, float], dict[str, list[tuple[float, float]]] | None, dict[int, float]]:
    """Чем машины уже заняты (рейсы trips по _timeline): (конец последнего рейса машины; занятые отрезки
    [выезд, возвращение] — только если между ними есть промежуток (рейс выезжает позже под окно первой точки),
    иначе None — день занят одним куском, как раньше; выезд каждого рейса)."""
    tl = _timeline(ctx, trips, stops, shares)
    used: dict[str, float] = {}
    spans: dict[str, list[tuple[float, float]]] = {}
    gap = False
    for t in trips:
        if t.id in tl:
            depart, minutes, _ = tl[t.id]
            gap = gap or depart > used.get(t.truck, 0.0) + _EPS
            used[t.truck] = depart + minutes
            spans.setdefault(t.truck, []).append((depart, depart + minutes))
    return used, (spans if gap else None), {tid: v[0] for tid, v in tl.items()}


def _by_departure(old: list[DraftTrip], old_departs: Mapping[int, float], new: list[DraftTrip],
                  new_departs: Sequence[float]) -> list[DraftTrip]:
    """Рейсы черновика: прежние + новые. Расчёт с окнами (new_departs есть) мог поставить новый рейс в промежуток
    до прежнего — рейсы машины идут по времени выезда, иначе _timeline повёз бы их не в том порядке."""
    if not new_departs:
        return old + new
    when = {**{t.id: old_departs[t.id] for t in old}, **{t.id: dep for t, dep in zip(new, new_departs)}}
    return sorted(old + new, key=lambda t: when[t.id])


# --- Сборка рейсов ---

def _plan_around(ctx: DayContext, sel: Sequence[fl.FleetTruck], routable: Mapping[int, Stop], keep: list[DraftTrip],
                 was: Mapping[int, tuple[float, float, list[float]]], next_id: int
                 ) -> tuple[list[DraftTrip], list[Stop], dict[int, str], int]:
    """Раскладка с окнами и центром вокруг готовых рейсов keep (fleet.route_day, fixed): их машина и состав не
    меняются, время — их выезд в прежнем плане (was), если там свободно; остальные магазины раскладываются вокруг.
    (рейсы по времени выезда, магазины вне keep, причины неназначенных — номер в них, следующий id)."""
    shares = _shares(keep)
    rest = [routable[c] for c in sorted(routable) if c not in shares]
    pts, kgs, revs = [s.point for s in rest], [s.kg for s in rest], [s.revenue for s in rest]
    wins, cen = [_span(ctx, s.customer_id) for s in rest], [_central(ctx, s) for s in rest]
    access = [_allowed_trucks(ctx, s.customer_id) for s in rest]
    fixed: list[tuple[str, list[int], float]] = []
    for t in keep:
        fixed.append((t.truck, list(range(len(pts), len(pts) + len(t.stops))), was[t.id][0] if t.id in was else 0.0))
        for c in t.stops:
            s = routable[c]
            pts.append(s.point)
            kgs.append(s.kg / shares[c])
            revs.append(s.revenue / shares[c])
            wins.append(_span(ctx, c))
            cen.append(_central(ctx, s))
            access.append(_allowed_trucks(ctx, c))
    reasons: dict[int, str] = {}
    trips = fl.route_day(pts, kgs, revs, ctx.depot, sel, ctx.norms, ctx.tn, overflow=False, windows=wins, center=cen,
                         reasons=reasons, fixed=fixed, balance=True, solver=True, allowed_trucks=access)
    own = {tuple(idx): t for t, (_, idx, _) in zip(keep, fixed)}
    out: list[DraftTrip] = []
    for t in trips:
        if t.items in own:
            out.append(own[t.items])
        else:
            out.append(DraftTrip(next_id, t.truck, [rest[i].customer_id for i in t.items]))
            next_id += 1
    return out, rest, reasons, next_id


def build(ctx: DayContext, stops: Sequence[Stop], old: Draft | None, trucks: Sequence[str],
          now: str) -> Draft:
    """«Собрать рейсы»: заказы с координатами (кроме исключённых) → рейсы выбранных машин.
    Закреплённые логистом рейсы прежнего черновика остаются как есть (если их машина работает), их время
    машина уже занята; остальное раскладывается заново. За конец рабочего дня машин рейсы не планируются:
    что не успевают выбранные машины, остаётся вне рейсов (no_room) — логист добавляет машину или
    решает сам. Окна приёма и малый центр соблюдаются: не помещается в окно — no_window, в центре без
    машины с правом въезда — no_center. С окнами или центром закреплённые рейсы остаются и на своём прежнем
    времени, если там свободно и окна соблюдаются («после 15:00» — в конце дня, утро — другим рейсам), иначе — в
    самый ранний промежуток, где укладываются; остальное раскладывается заново вокруг них.
    Как и раньше: закреплён один рейс тяжёлого заказа на несколько поездок — в его рейсе заказ показывается
    целиком (доля считается по рейсам черновика, остальные поездки при пересборке не закреплены)."""
    old = old or Draft()
    routable = {s.customer_id: s for s in stops if s.point is not None}
    sel = _selected(ctx, trucks)
    codes = {t.car_code for t in sel}
    draft = Draft(trucks=sorted(codes), excluded=set(old.excluded), added=set(old.added), next_id=old.next_id,
                  built_at=now, deferred=set(old.deferred), dropped=set(old.dropped))
    pinned = [DraftTrip(t.id, t.truck, list(t.stops), True) for t in old.trips if t.pinned and t.truck in codes]
    tmp = Draft(trips=pinned)
    _clean(tmp, routable)
    pinned = tmp.trips
    for trip in pinned:
        _require_vehicle(ctx, trip.stops, trip.truck)
    timed = any(c in ctx.windows or _central(ctx, s) for c, s in routable.items())
    if timed and pinned and sel:
        was = _timeline(ctx, old.trips, routable, _shares(old.trips))
        draft.trips, rest, reasons, draft.next_id = _plan_around(ctx, sel, routable, pinned, was, draft.next_id)
    else:
        shares = _shares(pinned)
        rest = [routable[c] for c in sorted(routable) if c not in shares]
        reasons = {}
        used, _, _ = _occupied(ctx, pinned, routable, shares)
        trips = fl.route_day([s.point for s in rest], [s.kg for s in rest], [s.revenue for s in rest],
                             ctx.depot, sel, ctx.norms, ctx.tn, used, overflow=False,
                             windows=[_span(ctx, s.customer_id) for s in rest], center=[_central(ctx, s) for s in rest],
                             reasons=reasons, balance=True, solver=True,
                             allowed_trucks=[_allowed_trucks(ctx, s.customer_id) for s in rest]) if rest and sel else []
        draft.trips = list(pinned)
        for t in trips:
            draft.trips.append(DraftTrip(draft.next_id, t.truck, [rest[i].customer_id for i in t.items]))
            draft.next_id += 1
    placed = {c for t in draft.trips for c in t.stops}
    if sel:
        for i, s in enumerate(rest):
            if s.customer_id not in placed:
                {'window': draft.no_window, 'center': draft.no_center, 'vehicle': draft.no_vehicle}.get(
                    reasons.get(i), draft.no_room).add(s.customer_id)
    return draft


def overtime(ctx: DayContext, stops: Sequence[Stop], draft: Draft) -> Draft:
    """«Везти после конца дня» (форс-мажор, ответ владельца №32): магазины, не поместившиеся до конца
    рабочего дня (no_room) и всё ещё вне рейсов, раскладываются по машинам дня поверх их рейсов — с
    переработкой, но не позже предела (ctx.overtime_minutes, настройка truck_overtime_end); что не
    успевает и к пределу — остаётся «не поместились». Рейсы дня не меняются. Окна приёма и центр — как при
    сборке; не помещавшиеся в окно тоже пробуются: «после 17:30» могло не поместиться только до конца дня."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    _clean(draft, routable)
    shares = _shares(draft.trips)
    sel = _selected(ctx, draft.trucks)
    rest = [routable[c] for c in sorted(draft.no_room | draft.no_window | draft.no_vehicle) if c in routable and c not in shares]
    draft.overtime_ok = True
    if not rest or not sel:
        return draft
    used, busy, day_departs = _occupied(ctx, draft.trips, routable, shares)
    limit = replace(ctx.tn, work_minutes=ctx.overtime_minutes) if ctx.overtime_minutes is not None else ctx.tn
    reasons: dict[int, str] = {}
    departs: list[float] = []
    trips = fl.route_day([s.point for s in rest], [s.kg for s in rest], [s.revenue for s in rest],
                         ctx.depot, sel, ctx.norms, limit, used, overflow=ctx.overtime_minutes is None,
                         earliest=True, windows=[_span(ctx, s.customer_id) for s in rest],
                         center=[_central(ctx, s) for s in rest], reasons=reasons, busy=busy, departs=departs,
                         load_cap=fl.LOAD_CAP, allowed_trucks=[_allowed_trucks(ctx, s.customer_id) for s in rest])
    new = []
    for t in trips:
        new.append(DraftTrip(draft.next_id, t.truck, [rest[i].customer_id for i in t.items]))
        draft.next_id += 1
    draft.trips = _by_departure(draft.trips, day_departs, new, departs)
    placed = {c for t in draft.trips for c in t.stops}
    # что не поместилось и с переработкой — по причине этой раскладки: окно, центр или время
    for i, s in enumerate(rest):
        cid = s.customer_id
        for left in (draft.no_room, draft.no_window, draft.no_center, draft.no_vehicle):
            left.discard(cid)
        if cid not in placed:
            {'window': draft.no_window, 'center': draft.no_center, 'vehicle': draft.no_vehicle}.get(
                reasons.get(i), draft.no_room).add(cid)
    return draft


def runs_late(ctx: DayContext, stops: Sequence[Stop], draft: Draft) -> bool:
    """Хоть одна машина плана работает дольше рабочего дня (рейсы подряд с ожиданием у окон, как в plan_view)."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    used, _, _ = _occupied(ctx, draft.trips, routable, _shares(draft.trips))
    return any(v > ctx.tn.work_minutes + _EPS for v in used.values())


# --- Правки логиста ---

def _trip(draft: Draft, tid: Any) -> DraftTrip:
    for t in draft.trips:
        if t.id == tid:
            return t
    raise DispatchError('Рейс не найден — обновите страницу')


def _insert_cheapest(ctx: DayContext, cids: list[int], cid: int, stops: Mapping[int, Stop]) -> list[int]:
    """Вставить клиента в рейс там, где объезд удлиняется меньше всего."""
    km = ctx.norms.km
    pts = [ctx.depot, *(stops[c].point for c in cids), ctx.depot]
    p = stops[cid].point
    best = min(range(len(pts) - 1), key=lambda i: (km(pts[i], p) + km(p, pts[i + 1]) - km(pts[i], pts[i + 1]), i))
    return cids[:best] + [cid] + cids[best:]


def apply_edit(ctx: DayContext, stops: Sequence[Stop], draft: Draft, edit: Mapping[str, Any],
               order_ids: set[str], backlog_ids: set[str] = frozenset(), defer_since: date | None = None,
               carried: Collection[str] = ()) -> Draft:
    """Правка логиста (§3) поверх черновика; затронутые рейсы пересчитываются (порядок — 2-opt).
    edit:
      {"action": "move", "customer_id", "from_trip": id|null, "to_trip": id|null, "truck": код|null}
          — перенести клиента в другой рейс; to_trip = null и truck — новый рейс этой машины;
            to_trip = null и truck = null — убрать из рейсов («ещё не в рейсах»);
      {"action": "pin", "trip": id, "truck": код} — закрепить машину за рейсом (рейс уходит к ней);
      {"action": "unpin", "trip": id};
      {"action": "exclude" | "include", "order": fISN} — «не везём сегодня» / вернуть; заказ прошлых
          дней (backlog_ids) — убрать из развоза / добавить в развоз (перенесённый сюда — carried —
          убранный запоминается в dropped); перенос на завтра снимается;
      {"action": "defer_trip", "trip": id} — «везти завтра» (№25: рейс дешевле min_trip_revenue): заказы
          рейса — не сегодня (excluded / из added) и в deferred — следующий день доставки возьмёт их сам;
          заказ старше defer_since (вне окна «не отгружены с прошлых дней» следующего дня) — ошибка:
          завтра его не будет видно, решать надо сегодня.
    Ошибка — DispatchError с текстом для логиста."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    _clean(draft, routable)
    action = edit.get('action')
    if action in ('exclude', 'include'):
        isn = edit.get('order')
        isn = isn.upper() if isinstance(isn, str) else None
        if isn in backlog_ids:
            if action == 'exclude':
                draft.added.discard(isn)
                if isn in carried:
                    draft.dropped.add(isn)
            else:
                draft.added.add(isn)
                draft.dropped.discard(isn)
            draft.deferred.discard(isn)
            return draft
        if isn not in order_ids:
            raise DispatchError('Заказ не найден среди заказов дня — обновите страницу')
        (draft.excluded.add if action == 'exclude' else draft.excluded.discard)(isn)
        draft.deferred.discard(isn)
        return draft
    if action == 'defer_trip':
        trip = _trip(draft, edit.get('trip'))
        if defer_since is not None and any(o.order_date < defer_since for c in trip.stops for o in routable[c].orders):
            raise DispatchError('Заказы этого рейса слишком давние для переноса на завтра — решите сегодня')
        for cid in trip.stops:
            for o in routable[cid].orders:
                if o.isn in order_ids:
                    draft.excluded.add(o.isn)
                else:
                    draft.added.discard(o.isn)
                draft.deferred.add(o.isn)
        moved = set(trip.stops)
        for t in draft.trips:                    # тяжёлый заказ в нескольких рейсах — переносится целиком
            t.stops = [c for c in t.stops if c not in moved]
        draft.trips = [t for t in draft.trips if t.stops]
        return draft
    if action in ('pin', 'unpin'):
        trip = _trip(draft, edit.get('trip'))
        if action == 'unpin':
            trip.pinned = False
            return draft
        truck = edit.get('truck')
        if truck not in ctx.trucks or truck not in draft.trucks:
            raise DispatchError('Эта машина сегодня не работает — отметьте её в шаге 1')
        _require_vehicle(ctx, trip.stops, truck)
        if truck != trip.truck:   # рейс уходит последним к выбранной машине
            draft.trips.remove(trip)
            trip.truck = truck
            draft.trips.append(trip)
        trip.pinned = True
        return draft
    if action != 'move':
        raise DispatchError('Неизвестное действие')
    cid = edit.get('customer_id')
    if not _is_int(cid) or cid not in routable:
        raise DispatchError('Точка не найдена — обновите страницу')
    shares = _shares(draft.trips)
    src_id, dst_id, truck = edit.get('from_trip'), edit.get('to_trip'), edit.get('truck')
    src = _trip(draft, src_id) if src_id is not None else None
    if src is None and cid in shares:
        raise DispatchError('Точка уже в рейсе — укажите, из какого')
    if src is not None and cid not in src.stops:
        raise DispatchError('Этой точки нет в рейсе — обновите страницу')
    dst = _trip(draft, dst_id) if dst_id is not None else None
    if dst is None and truck is not None and (truck not in ctx.trucks or truck not in draft.trucks):
        raise DispatchError('Эта машина сегодня не работает — отметьте её в шаге 1')
    target_truck = dst.truck if dst is not None else truck
    if target_truck is not None:
        _require_vehicle(ctx, [cid], target_truck)
    if dst is not None and dst is src:
        return draft
    if src is not None:
        src.stops.remove(cid)
    if dst is None and truck is not None:
        dst = DraftTrip(draft.next_id, truck, [])
        draft.next_id += 1
        draft.trips.append(dst)
    if dst is not None and cid not in dst.stops:
        dst.stops = _insert_cheapest(ctx, dst.stops, cid, routable)
        for left in (draft.no_room, draft.no_window, draft.no_center, draft.no_vehicle):
            left.discard(cid)
    draft.trips = [t for t in draft.trips if t.stops]
    shares = _shares(draft.trips)
    for t in (src, dst):
        if t is not None and t.stops:
            # выезд рейса — для окон приёма при 2-opt; заново после перестановки src (та же машина — другой выезд)
            start = _timeline(ctx, draft.trips, routable, shares)[t.id][0]
            t.stops, *_ = _route(ctx, t.stops, routable, shares, reorder=True, start=start)
    return draft


# --- Вид страницы ---

def _r(x: float, nd: int = 1) -> float:
    return round(x, nd)


def _bypass_km(ctx: DayContext, points: Sequence[Point]) -> tuple[float, int]:
    """(на сколько км объезд малого центра удлинил рейс «склад → points → склад», участков в объезд). Участок — км
    расчёта (Norms.km грузовика, как у км рейса) против того же участка без объезда: км / во сколько раз объезд длиннее
    (CenterBypassRoads.detour; тем же множителем Valhalla и Яндекс растягивают свой путь). Длиннее меньше чем на
    BYPASS_MIN_KM — расхождение двух графов (привязка точки, округление км), а не объезд. Объезда нет (нет дорог или
    границы центра) — (0, 0). Типы дорог здесь не импортируются: объезд — сами дороги или запасной путь Valhalla."""
    norms = ctx.norms.for_trucks()
    roads = getattr(norms.roads, 'fallback', norms.roads)
    if not hasattr(roads, 'detour'):
        return 0.0, 0
    nodes = [ctx.depot, *points, ctx.depot]
    extra: list[float] = []
    for a, b in zip(nodes, nodes[1:]):
        k = roads.detour(a, b)
        if k > 1.0:
            km = norms.km(a, b)
            if km - km / k >= BYPASS_MIN_KM:
                extra.append(km - km / k)
    return math.fsum(extra), len(extra)


def _alternatives(ctx: DayContext, sel: Sequence[fl.FleetTruck], code: str, cids: Sequence[int],
                  routable: Mapping[int, Stop], kgs: Sequence[float]) -> list[dict[str, Any]]:
    """Другие машины дня на этот же рейс (те же точки, тот же порядок): могла бы она его везти по правилам сборки —
    тоннаж и предел загрузки (fl.load_limit), центр, допуск магазина — и сколько литров у неё вышло бы на тех же км.
    Занятость машины своими рейсами здесь не учитывается: сборка ищет меньше дизеля за весь день, а не за один рейс."""
    kg = math.fsum(kgs)
    pts = [routable[c].point for c in cids]
    center = [_central(ctx, routable[c]) for c in cids]
    allowed = [_allowed_trucks(ctx, c) for c in cids]
    out = []
    for o in sel:
        if o.car_code == code:
            continue
        why = []
        if kg > o.capacity_kg + _EPS:
            why.append('capacity')
        elif kg > fl.load_limit(kgs, center, allowed, o, sel) + _EPS:
            why.append('load_cap')
        if any(center) and not o.center_ok:
            why.append('center')
        denied = sum(1 for c in cids if not _vehicle_ok(ctx, c, o.car_code))
        if denied:
            why.append('vehicle')
        out.append({'car_code': o.car_code, 'name': o.name, 'capacity_kg': o.capacity_kg, 'center_ok': o.center_ok,
                    'load_pct': round(kg / o.capacity_kg * 100.0), 'reasons': why, 'vehicle_denied': denied,
                    'liters': _r(fl.trip_running_cost(pts, kgs, ctx.depot, ctx.norms, o).liters)})
    return out


def _trip_explain(ctx: DayContext, sel: Sequence[fl.FleetTruck], code: str, truck: fl.FleetTruck | None,
                  cids: Sequence[int], routable: Mapping[int, Stop], kgs: Sequence[float], parts: Mapping[str, Any],
                  free: float, depart: float, minutes: float) -> dict[str, Any]:
    """Почему рейс такой («Ինչու է այս երթը այսպես» на странице) — только цифры этого же расчёта: слагаемые минут рейса
    (из _timeline, в сумме — его минуты), простой на складе до выезда (выезд позже, чтобы не ждать у окна первой точки),
    запас до конца рабочего дня, предел загрузки, расход, объезд центра и другие машины дня на этот рейс."""
    legs = parts['legs']
    kg = math.fsum(kgs)
    limit = (fl.load_limit(kgs, [_central(ctx, routable[c]) for c in cids], [_allowed_trucks(ctx, c) for c in cids],
                           truck, sel) if truck is not None else None)
    bypass_km, bypass_legs = _bypass_km(ctx, [routable[c].point for c in cids])
    heavy = (truck is not None and limit is not None and len(cids) == 1
             and limit > fl.LOAD_CAP * truck.capacity_kg + _EPS and kg > fl.LOAD_CAP * truck.capacity_kg + _EPS)
    return {
        'loading_min': _r(parts['loading']), 'drive_min': _r(math.fsum(x for x, _, _ in legs)),
        'unload_min': _r(math.fsum(x for _, _, x in legs)), 'wait_min': _r(math.fsum(x for _, x, _ in legs)),
        'back_min': _r(legs[-1][0]), 'idle_before_min': _r(depart - free),
        'end_slack_min': _r(ctx.tn.work_minutes - (depart + minutes)),
        'load_cap_pct': round(fl.LOAD_CAP * 100), 'load_limit_kg': round(limit) if limit is not None else None,
        'over_limit': limit is not None and kg > limit + _EPS, 'heavy_alone': heavy,
        'l100': _r(truck.l100) if truck else None,
        'fuel_empty_l100': truck.fuel_empty_l_per_100km if truck else None,
        'fuel_full_l100': truck.fuel_full_l_per_100km if truck else None,
        'bypass_km': _r(bypass_km), 'bypass_legs': bypass_legs,
        'others': _alternatives(ctx, sel, code, cids, routable, kgs),
    }


def _day_explain(ctx: DayContext, routable: Mapping[int, Stop], draft: Draft, sel: Sequence[fl.FleetTruck],
                 trips_of: Mapping[str, int]) -> dict[str, Any]:
    """Что учитывает расчёт дня («Ինչ է հաշվի առել հաշվարկը» на странице): машины дня (с действующим расходом),
    магазины в центре, с окном приёма и с допуском машин, предел загрузки и выравнивание загрузки (fleet._balance),
    закреплённые рейсы, нормы загрузки и разгрузки (загрузка — те же числа, что у tn.load), правило точки магазина
    (ERP дальше ERP_GPS_MAX_GAP_KM от GPS менеджера — GPS), решатель и ctx.model (дороги, минуты, выученные нормы — views._dispatch_ctx)."""
    tn = ctx.tn
    return {
        'trucks': [{'car_code': x.car_code, 'name': x.name, 'capacity_kg': x.capacity_kg, 'l100': _r(x.l100),
                    'fuel_empty_l100': x.fuel_empty_l_per_100km, 'fuel_full_l100': x.fuel_full_l_per_100km,
                    'wear': x.wear_amd_per_km is not None or x.wear_load_amd_per_km is not None,
                    'center_ok': x.center_ok, 'trips': trips_of.get(x.car_code, 0)} for x in sel],
        'zone': len(ctx.center_zone) >= 3,
        'center_stores': sum(1 for s in routable.values() if _central(ctx, s)),
        'window_stores': sum(1 for c in routable if c in ctx.windows),
        'access_stores': sum(1 for c in routable if c in ctx.vehicle_access),
        'load_cap_pct': round(fl.LOAD_CAP * 100),
        'balance_from_pct': round(fl.BALANCE_FROM * 100), 'balance_slack_pct': round(fl.BALANCE_SLACK * 100),
        'balance_load_aware': any(configured(x) for x in sel),
        'pinned_trips': sum(1 for t in draft.trips if t.pinned),
        'unload_min_per_stop': tn.unload_min_per_stop, 'unload_min_per_tonne': tn.unload_min_per_tonne,
        'unload_stores': sum(1 for s in routable.values() if s.point in tn.unload_extra),
        'loading_fixed_min': tn.warehouse_load_fixed_min, 'loading_min_per_tonne': tn.warehouse_load_min_per_tonne,
        'erp_gps_gap_km': ERP_GPS_MAX_GAP_KM,
        'solver': vrp.available(), 'solver_iterations': vrp.ITERATIONS,
        'model': dict(ctx.model),
    }


def _advice(ctx: DayContext, draft: Draft, trips_json: Sequence[Mapping[str, Any]],
            unassigned: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Совет, когда не всё поместилось (ответ владельца №54): рейсы позже конца дня (over_time; с принятой переработкой —
    позже её предела) или магазины вне рейсов «не успели» / «центр без машины» (no_room, no_center; кроме тех, кому
    среди отмеченных машин нет допущенной, — no_vehicle, у них своя карточка). Ничего такого — None.
    Сама сборка машин не добавляет (№32): совет — логисту, решает он одной кнопкой на странице.
    - pinned_late — опаздывающие закреплённые рейсы (id): пересборка их не меняет, ни она, ни ещё машина им не помогут —
      страница советует снять закрепление или перенести магазины; дальше в совете они не участвуют;
    - rebuild — отмеченные машины дня без рейсов. Свежая сборка за конец дня не планирует, пока отмеченные машины
      свободны: опаздывающие рейсы рядом со свободными машинами — план собран при других настройках или правлен
      вручную, обычная пересборка загрузит и их;
    - add — иначе одна готовая, но не отмеченная машина. Нужен центр (магазин no_center или опаздывающий рейс машины с
      правом въезда, в котором есть точки центра) — из машин с правом въезда (таких нет — из всех, for_center: false);
      из них — допущенные ко всем этим магазинам (допуск магазина, VehicleAccess), если такие есть.
      Груз need_kg — самый тяжёлый опаздывающий рейс или все магазины вне рейсов вместе: из машин, что его берут, —
      с меньшим расходом (затем вместительнее, затем код), иначе самая вместительная — need_kg в совете: тоннаж меньше —
      страница пишет «возьмёт часть груза». Свободной машины нет (или опаздывают только закреплённые) — None;
    - no_free — беда есть и сверх закреплённых рейсов, а свободной машины нет: страница пишет «все машины уже отмечены»
      (только закреплённые — False: прежние тексты страницы).
    Это оценка без расчёта рейсов (дёшево и детерминированно): что на самом деле поместится, покажет пересборка."""
    late = [t for t in trips_json if t['over_time']]
    pinned_late = [t['id'] for t in late if t['pinned']]
    late = [t for t in late if not t['pinned']]
    left = [u for u in unassigned if (u['no_room'] or u['no_center']) and not u['no_vehicle']]
    if not late and not left and not pinned_late:
        return None
    busy = {t['truck'] for t in trips_json}
    idle = [c for c in sorted(set(draft.trucks)) if c in ctx.trucks and c not in busy]
    if late and idle:
        return {'rebuild': idle, 'add': None, 'pinned_late': pinned_late, 'no_free': False}
    if not late and not left:                   # опаздывают только закреплённые рейсы
        return {'rebuild': [], 'add': None, 'pinned_late': pinned_late, 'no_free': False}
    free = [t for c, t in sorted(ctx.trucks.items()) if c not in draft.trucks]
    center_ok = {c for c, t in ctx.trucks.items() if t.center_ok}
    need_center = any(u['no_center'] for u in left) or any(
        t['truck'] in center_ok and any(s['center'] for s in t['stops']) for t in late)
    pool = [t for t in free if t.center_ok] if need_center else []
    for_center = bool(pool)
    pool = pool or free
    cids = [u['customer_id'] for u in left] + [s['customer_id'] for t in late for s in t['stops']]
    pool = [t for t in pool if all(_vehicle_ok(ctx, c, t.car_code) for c in cids)] or pool
    if not pool:
        return {'rebuild': [], 'add': None, 'pinned_late': pinned_late, 'no_free': True}
    need_kg = max([t['kg'] for t in late] + [sum(u['kg'] for u in left)])
    fits = [t for t in pool if t.capacity_kg >= need_kg]
    pick = (min(fits, key=lambda t: (t.l100, -t.capacity_kg, t.car_code)) if fits
            else min(pool, key=lambda t: (-t.capacity_kg, t.l100, t.car_code)))
    return {'rebuild': [], 'pinned_late': pinned_late, 'no_free': False,
            'add': {'car_code': pick.car_code, 'name': pick.name, 'capacity_kg': pick.capacity_kg, 'l100': pick.l100,
                    'center_ok': pick.center_ok, 'for_center': for_center, 'need_kg': int(need_kg)}}


def plan_view(ctx: DayContext, stops: Sequence[Stop], draft: Draft,
              info: Callable[[Stop], dict[str, Any]], explain: bool = True) -> dict[str, Any]:
    """Рейсы черновика с цифрами по текущим заказам: машины → рейсы по порядку (выезд, возвращение,
    км, литры, загрузка), точки по порядку; «ещё не в рейсах»; «не помещается» и совет, что с этим делать (advice —
    _advice). Время — рейсы машины подряд с ожиданием у окон приёма; у точки — прибытие (eta), вне окна (window_miss —
    бывает после правки логиста), в центре (center) и в центре на машине без права въезда (center_miss). Пояснение
    «почему так» — у рейса explain (_trip_explain), у дня — explain (_day_explain): только цифры того же расчёта, текст
    строит страница.
    explain=False — без них (км до правки, прогноз для «план — факт»: литры других машин и объезд не нужны)."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    _clean(draft, routable)
    shares = _shares(draft.trips)
    window = ctx.tn.work_minutes
    # принятая переработка: «опаздывает» — только позже предела; позже конца дня — пометка late
    limit = ctx.overtime_minutes if draft.overtime_ok and ctx.overtime_minutes is not None else window
    parts: dict[int, dict[str, Any]] = {}
    times = _timeline(ctx, draft.trips, routable, shares, parts)
    sel = _selected(ctx, draft.trucks)
    per_truck: dict[str, dict[str, Any]] = {}
    trips_json = []
    for t in draft.trips:
        cids, km, _, kg = _route(ctx, t.stops, routable, shares, reorder=False)
        truck = ctx.trucks.get(t.truck)
        cap = truck.capacity_kg if truck else None
        kgs = [routable[c].kg / shares[c] for c in cids]
        cost = fl.trip_running_cost([routable[c].point for c in cids], kgs, ctx.depot, ctx.norms, truck) if truck else None
        slot = per_truck.setdefault(t.truck, {'used': 0.0, 'trips': []})
        free = slot['used']
        depart, minutes, arrivals = times[t.id]
        slot['used'] = depart + minutes
        revenue = math.fsum(routable[c].revenue / shares[c] for c in cids)
        marks = []
        # у точки — и слагаемые её времени (езда от предыдущей точки, ожидание окна, разгрузка), приезд (eta минус
        # ожидание окна) и запас до конца окна
        for c, at, (drive, wait, unload) in zip(cids, arrivals, parts[t.id]['legs']):
            central = _central(ctx, routable[c])
            late = _span(ctx, c)[1]
            marks.append({'eta': _hhmm(ctx.work_start_min + at), 'window_miss': at > late + _EPS,
                          'center': central, 'center_miss': central and not (truck is not None and truck.center_ok),
                          'vehicle_miss': not _vehicle_ok(ctx, c, t.truck),
                          'arrive': _hhmm(ctx.work_start_min + at - wait),   # приехал; eta — начало разгрузки
                          'drive_min': _r(drive), 'wait_min': _r(wait), 'unload_min': _r(unload),
                          'margin_min': _r(late - at) if math.isfinite(late) else None})
        tj = {
            'id': t.id, 'truck': t.truck, 'pinned': t.pinned,
            'km': _r(km), 'minutes': round(minutes), 'kg': round(kg),
            'revenue': round(revenue),
            'liters': _r(cost.liters) if cost else None,
            'wear_amd': round(cost.wear_amd) if cost else None,
            'operating_cost_amd': round(cost.total_amd(ctx.tn.fuel_price)) if cost else None,
            'payload_tonne_km': _r(cost.payload_tonne_km) if cost else None,
            'fuel_load_configured': cost.fuel_load_configured if cost else False,
            'wear_configured': cost.wear_configured if cost else False,
            'load_pct': round(kg / cap * 100.0) if cap else None,
            'loading_start': _hhmm(ctx.work_start_min + depart),
            'loading_minutes': _r(ctx.tn.load(kg)),
            'depart': _hhmm(ctx.work_start_min + depart + ctx.tn.load(kg)), 'return': _hhmm(ctx.work_start_min + slot['used']),
            'over_time': slot['used'] > limit + _EPS, 'late': slot['used'] > window + _EPS,
            # бедный — по полной выручке точек: тяжёлый заказ на несколько поездок перенос не объединит
            'poor': ctx.min_trip_revenue > 0
                    and math.fsum(routable[c].revenue for c in cids) < ctx.min_trip_revenue,
            'over_capacity': cap is not None and kg > cap + 0.5,
            'no_truck': truck is None,
            'stops': [{**info(routable[c]), 'kg': round(routable[c].kg / shares[c]),
                       'share': shares[c], **mark} for c, mark in zip(cids, marks)],
            'window_miss': sum(1 for x in marks if x['window_miss']),
            'center_miss': sum(1 for x in marks if x['center_miss']),
            'vehicle_miss': sum(1 for x in marks if x['vehicle_miss']),
        }
        if explain:
            tj['explain'] = _trip_explain(ctx, sel, t.truck, truck, cids, routable, kgs, parts[t.id], free, depart, minutes)
        slot['trips'].append(tj)
        trips_json.append(tj)
    trucks_json = []
    for code in sorted(per_truck, key=lambda c: (c not in draft.trucks, c)):
        slot = per_truck[code]
        truck = ctx.trucks.get(code)
        ts = slot['trips']
        trucks_json.append({
            'car_code': code, 'name': truck.name if truck else None,
            'capacity_kg': truck.capacity_kg if truck else None, 'l100': truck.l100 if truck else None,
            'center_ok': truck.center_ok if truck else None,
            'trips': ts, 'km': _r(math.fsum(t['km'] for t in ts)),
            'liters': _r(math.fsum(t['liters'] or 0.0 for t in ts)), 'kg': sum(t['kg'] for t in ts),
            'wear_amd': sum(t['wear_amd'] or 0 for t in ts),
            'operating_cost_amd': sum(t['operating_cost_amd'] or 0 for t in ts),
            'fuel_load_configured': all(t['fuel_load_configured'] for t in ts),
            'wear_configured': all(t['wear_configured'] for t in ts),
            'stops': sum(len(t['stops']) for t in ts), 'minutes': round(slot['used']),
            'loading_minutes': _r(sum(t['loading_minutes'] for t in ts)),
            'payload_tonne_km': _r(sum(t['payload_tonne_km'] or 0 for t in ts)),
            'return': ts[-1]['return'], 'over_time': slot['used'] > limit + _EPS,
            'late': slot['used'] > window + _EPS,
        })
    in_trips = set(shares)
    missing_coords = [s for s in stops if s.point is None]
    assigned_kg = math.fsum(s.kg for s in stops if s.customer_id in in_trips)
    unassigned = [info(s) | {'kg': round(s.kg), 'no_room': s.customer_id in draft.no_room,
                             'no_window': s.customer_id in draft.no_window,
                             'no_center': s.customer_id in draft.no_center, 'center': _central(ctx, s),
                             'no_vehicle': s.customer_id in ctx.vehicle_access and not any(
                                 _vehicle_ok(ctx, s.customer_id, code) for code in draft.trucks if code in ctx.trucks)}
                  for s in stops if s.point is not None and s.customer_id not in in_trips]
    over = [t for t in trips_json if t['over_time'] or t['over_capacity'] or t['no_truck'] or t['vehicle_miss']]
    km_total = math.fsum(t['km'] for t in trips_json)
    return {
        'trucks': trucks_json,
        'unassigned': unassigned,
        'overflow': {'trips': len(over), 'kg': sum(t['kg'] for t in over),
                     'unassigned_kg': sum(u['kg'] for u in unassigned)},
        'advice': _advice(ctx, draft, trips_json, unassigned),
        'summary': {'trips': len(trips_json), 'trucks': len(trucks_json), 'km': _r(km_total),
                    'poor_trips': sum(1 for t in trips_json if t['poor']),
                    'liters': _r(math.fsum(t['liters'] or 0.0 for t in trips_json)),
                    'wear_amd': sum(t['wear_amd'] or 0 for t in trips_json),
                    'operating_cost_amd': sum(t['operating_cost_amd'] or 0 for t in trips_json),
                    'fuel_load_unconfigured': sum(not t['fuel_load_configured'] for t in trips_json),
                    'wear_unconfigured': sum(not t['wear_configured'] for t in trips_json),
                    'fuel_price_amd': ctx.tn.fuel_price,
                    'fuel_price_estimated': ctx.tn.fuel_price_estimated,
                    'loading_minutes': _r(sum(t['loading_minutes'] for t in trips_json)),
                    'loading_configured': ctx.tn.loading_configured,
                    'traffic': (ctx.norms.traffic_status or (ctx.norms.traffic.report if ctx.norms.traffic is not None else
                                {'status': 'static', 'live': False})),
                    'kg': round(assigned_kg), 'stops': len(in_trips),
                    'window_miss': sum(t['window_miss'] for t in trips_json),
                    'center_miss': sum(t['center_miss'] for t in trips_json),
                    'vehicle_miss': sum(t['vehicle_miss'] for t in trips_json)},
        'coverage': {'stops_total': len(stops), 'stops_assigned': len(in_trips),
                     'stops_no_coords': len(missing_coords), 'stops_unassigned': len(unassigned),
                     'kg_total': round(math.fsum(s.kg for s in stops)), 'kg_assigned': round(assigned_kg),
                     'kg_no_coords': round(math.fsum(s.kg for s in missing_coords)),
                     'revenue_no_coords': round(math.fsum(s.revenue for s in missing_coords)),
                     'complete': not missing_coords and not unassigned},
        **({'explain': _day_explain(ctx, routable, draft, sel, {c: len(s['trips']) for c, s in per_truck.items()})}
           if explain else {}),
    }


# --- Сравнение «по менеджерам» ---

def history_cars(agent_cars: Mapping[int, Sequence[str | int]], van_trucks: Mapping[int, str]) -> dict[int, tuple[str, ...]]:
    """Машины менеджера по истории: экспедитор без машины (int) → закреплённая за ним ручная машина; не
    закреплена — пропускается. Порядок (от самой частой) и без повторов."""
    out: dict[int, tuple[str, ...]] = {}
    for agent, cars in agent_cars.items():
        codes = [c if isinstance(c, str) else van_trucks.get(c, '') for c in cars]
        out[agent] = tuple(dict.fromkeys(c for c in codes if c))
    return out


def manager_trucks(stops: Sequence[Stop], agent_cars: Mapping[int, Sequence[str]],
                   trucks: Sequence[fl.FleetTruck]) -> dict[str, list[Stop]]:
    """Привычная схема: заказы менеджера везёт его машина по истории (SALES.fDELIVERYCAR за 90 дней) —
    самая частая из работающих сегодня; у менеджера таких нет — самая большая машина дня."""
    codes = {t.car_code for t in trucks}
    if not codes:
        return {}
    fallback = max(trucks, key=lambda t: (t.capacity_kg, -t.l100, t.car_code)).car_code
    out: dict[str, list[Stop]] = {}
    for s in stops:
        car = next((c for c in agent_cars.get(s.agent_id, ()) if c in codes), fallback)
        out.setdefault(car, []).append(s)
    return out


def _group_km(ctx: DayContext, groups: Mapping[str, Sequence[Stop]]) -> dict[str, Any]:
    """Каждая машина везёт свои точки лучшим для неё маршрутом (рейсы по тоннажу, 2-opt)."""
    km = liters = wear = 0.0
    trips = extra = 0
    per = []
    for code in sorted(groups):
        truck = ctx.trucks[code]
        ss = [s for s in groups[code] if s.point is not None]
        if not ss:
            continue
        ts = fl.route_day([s.point for s in ss], [s.kg for s in ss], [s.revenue for s in ss], ctx.depot,
                          [truck], ctx.norms, ctx.tn)
        k = math.fsum(t.km for t in ts)
        km += k
        liters += math.fsum(t.liters for t in ts)
        wear += math.fsum(t.wear_amd for t in ts)
        trips += len(ts)
        extra += sum(1 for t in ts if t.extra)
        per.append({'car_code': code, 'stops': len(ss), 'kg': round(math.fsum(s.kg for s in ss)),
                    'trips': len(ts), 'km': _r(k), 'over_time': any(t.extra for t in ts)})
    return {'km': _r(km), 'liters': _r(liters), 'wear_amd': round(wear),
            'operating_cost_amd': round(liters * ctx.tn.fuel_price + wear),
            'trips': trips, 'trips_over_time': extra, 'trucks': per}


def baseline(ctx: DayContext, stops: Sequence[Stop], draft: Draft,
             agent_cars: Mapping[int, Sequence[str]]) -> dict[str, Any] | None:
    """«Как обычно»: те же заказы, что в рейсах плана (без исключённых и не поместившихся), разложенные
    по машинам менеджеров."""
    in_trips = {c for t in draft.trips for c in t.stops}
    routable = [s for s in stops if s.point is not None and s.customer_id in in_trips]
    sel = _selected(ctx, draft.trucks)
    if not routable or not sel:
        return None
    return _group_km(ctx, manager_trucks(routable, agent_cars, sel))


# --- План и факт (прошедшая дата) ---

def plan_vs_fact(ctx: DayContext, docs: Sequence[ShippedDoc], coord: Callable[[int], Coord],
                 van_trucks: Mapping[int, str] | None = None) -> dict[str, Any]:
    """Те же доставки дня: как их фактически развезли машины ERP (каждая — лучшим для неё маршрутом)
    против рейсов программы на тех же машинах. Накладная без машины, которую вёз экспедитор, — рейс его
    ручной машины (van_trucks: экспедитор → машина). Машины без тоннажа и расхода в настройках и клиенты
    без координат в сравнение не входят (их число — в skipped)."""
    vans = van_trucks or {}
    groups: dict[str, dict[int, list[ShippedDoc]]] = {}
    skipped_docs = skipped_kg = 0.0
    no_car: set[str] = set()
    for d in docs:
        car = fact_car(d, vans)
        if car not in ctx.trucks or coord(d.customer_id).point is None:
            skipped_docs += 1
            skipped_kg += d.kg
            if car and car not in ctx.trucks:
                no_car.add(car)
            continue
        groups.setdefault(car, {}).setdefault(d.customer_id, []).append(d)

    def stop(cid: int, ds: Sequence[ShippedDoc]) -> Stop:
        c = coord(cid)
        return Stop(cid, c.point, c.source, math.fsum(x.kg for x in ds), math.fsum(x.revenue for x in ds), (),
                    ds[0].agent_id)

    fact = _group_km(ctx, {car: [stop(c, ds) for c, ds in sorted(cs.items())] for car, cs in groups.items()})
    merged: dict[int, list[ShippedDoc]] = {}
    for cs in groups.values():
        for c, ds in cs.items():
            merged.setdefault(c, []).extend(ds)
    all_stops = [stop(c, merged[c]) for c in sorted(merged)]
    trucks = [ctx.trucks[c] for c in sorted(groups)]
    trips = fl.route_day([s.point for s in all_stops], [s.kg for s in all_stops], [s.revenue for s in all_stops],
                         ctx.depot, trucks, ctx.norms, ctx.tn) if all_stops else []
    plan = {'km': _r(math.fsum(t.km for t in trips)), 'liters': _r(math.fsum(t.liters for t in trips)),
            'trips': len(trips), 'trips_over_time': sum(1 for t in trips if t.extra),
            'max_load_pct': round(max((t.kg / t.capacity_kg * 100.0 for t in trips), default=0.0))}
    return {'day': ctx.day.isoformat(), 'stops': len(all_stops), 'kg': round(math.fsum(s.kg for s in all_stops)),
            'trucks': sorted(groups), 'fact': fact, 'plan': plan,
            'saved_km': _r(fact['km'] - plan['km']),
            'skipped': {'docs': int(skipped_docs), 'kg': round(skipped_kg), 'cars_not_set': sorted(no_car)}}

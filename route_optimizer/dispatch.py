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
- Большая машина в Ереване (ответ владельца №68, правило — fleet): зона Еревана и надбавка — в ctx.tn (views._dispatch_ctx);
  у точки в зоне на большой машине план показывает надбавку (yerevan_min) — она уже в её разгрузке и во времени рейса.
- Новые заказы дня (ответ владельца №72): заказы с датой D — развоз D+1, но логист может взять их в развоз D
  (Draft.same_day) — вставкой в рейс, который по плану ещё не грузится, новым рейсом машины после её возвращения или
  машиной, которая сегодня не выезжала (same_day_options; машина в рейсе новый заказ не берёт — ответ «Բ»). Рейс, чья
  загрузка по плану уже началась, не меняется; прочие рейсы и точки плана не переставляются. Новый рейс не грузится
  раньше «сейчас» (DraftTrip.not_before). Взятые в D заказы D+1 не везёт (views._same_day_taken).
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
MAX_AGENTS = 500          # менеджеров в «чьи заказы не везём» — защита от битого черновика и запроса
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

def holidays_of(settings: Mapping[str, Any]) -> frozenset[date]:
    """Нерабочие даты из настроек (праздники и прочие выходные компании, №64): ISO-строки → даты."""
    return frozenset(date.fromisoformat(d) for d in settings.get('holidays') or ())


def is_workday(day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> bool:
    """Рабочий день: день недели (1 = пн … 7 = вс) отмечен рабочим и даты нет среди нерабочих (№64)."""
    return day.isoweekday() in (set(workdays) or set(range(1, 8))) and day not in holidays


def previous_workday(day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> date:
    """Последний рабочий день строго раньше day. Цикл конечен: нерабочих дат не больше
    store.MAX_HOLIDAYS, а хотя бы один день недели рабочий."""
    d = day - timedelta(days=1)
    while not is_workday(d, workdays, holidays):
        d -= timedelta(days=1)
    return d


def next_workday(day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> date:
    """Первый рабочий день строго позже day — «завтра» для логиста (в субботу — понедельник)."""
    d = day + timedelta(days=1)
    while not is_workday(d, workdays, holidays):
        d += timedelta(days=1)
    return d


def order_window(day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> tuple[date, date]:
    """Даты заказов, которые везут в day: [предыдущий рабочий день, day). В понедельник — заказы
    субботы и воскресенья; после праздника — и заказы праздника."""
    return previous_workday(day, workdays, holidays), day


def backlog_since(since: date, workdays: Sequence[int], n: int = BACKLOG_WORKDAYS,
                  holidays: Collection[date] = ()) -> date:
    """Начало окна «не отгружены с прошлых дней»: n рабочих дней раньше окна заказов дня."""
    for _ in range(n):
        since = previous_workday(since, workdays, holidays)
    return since


@dataclass(frozen=True)
class Selection:
    main: list[DispatchOrder]            # заказы дня к доставке
    backlog: list[DispatchOrder]         # не отгружены с прошлых дней (в план — только по выбору логиста)
    shipped_before: int                  # заказов дня уже отгружено раньше дня развоза
    self_delivery: list[DispatchOrder]   # заказы дня, которые менеджер развозит сам
    same_day_taken: int = 0              # заказов дня, взятых в развоз дня их приёма (№72, views._day_orders)
    same_day_unread: bool = False        # план прошлого дня не прочитан: взятые в нём заказы могут прийти сюда повторно


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


def same_day_candidates(orders: Sequence[DispatchOrder], day: date) -> list[DispatchOrder]:
    """Новые заказы дня day (№72): с датой day, не отгруженные раньше day, кроме тех, что менеджер развозит сам. Это заказы
    развоза следующего рабочего дня — в развоз day только по выбору логиста (Draft.same_day)."""
    key = lambda o: (o.customer_id, o.order_date, o.isn)   # noqa: E731
    return sorted((o for o in orders if o.order_date == day and (o.shipped is None or o.shipped >= day)
                   and not o.self_delivery), key=key)


@dataclass(frozen=True)
class SameDayData:
    """Заказы ERP с датой дня (№72) и когда их завели (DOCUMENTS.fCREATIONDATE: fISN → время; нет — не в словаре)."""
    orders: tuple[DispatchOrder, ...]
    created: dict[str, datetime]
    customers: dict[int, tuple[str, str]]          # клиент → (код, название)
    addresses: dict[int, str]
    loaded_at: datetime


# --- Свежесть заказов ---

def orders_still_coming(day: date, workdays: Sequence[int], now: datetime, ready_time: str,
                        holidays: Collection[date] = ()) -> bool:
    """Заказы на day ещё поступают: сегодня — последний день приёма заказов на day (предыдущий рабочий
    день, для понедельника — суббота) и сейчас раньше ready_time (ЧЧ:ММ). Прошедшая дата, выходной
    перед днём развоза или дата через несколько дней — False."""
    if now.date() != previous_workday(day, workdays, holidays):
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
    # новый рейс с заказами дня (№72): загрузка не раньше этого времени (минуты от полуночи); None — как обычно
    not_before: float | None = None


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
    # менеджеры (agent_id), чьи заказы сегодня не везём: фильтр «Մենեջերներ» — все их заказы дня и прошлых дней
    # вне развоза, пока менеджера не вернут (новые заказы этих менеджеров тоже); «не везём сегодня» — отдельно
    agents_off: set[int] = field(default_factory=set)
    # черновик до последнего «resize» (to_json без prediction и undo): «Չեղարկել» возвращает его (apply_edit «undo»);
    # любая другая правка, сборка и «Везти после конца дня» его сбрасывают — отменить можно только сам перенос
    undo: dict[str, Any] | None = None
    # заказы (fISN) с датой этого дня, которые логист взял в развоз этого же дня (№72); следующий день их не везёт
    same_day: set[str] = field(default_factory=set)

    def to_json(self) -> dict[str, Any]:
        built = None if self.built_orders is None else {
            k: [round(kg, 3), round(rev, 2)] for k, (kg, rev) in sorted(self.built_orders.items())}
        return {'trucks': list(self.trucks), 'excluded': sorted(self.excluded), 'added': sorted(self.added),
                'trips': [{'id': t.id, 'truck': t.truck, 'stops': list(t.stops), 'pinned': t.pinned,
                           **({'not_before': t.not_before} if t.not_before is not None else {})}
                          for t in self.trips],
                'next_id': self.next_id, 'built_at': self.built_at, 'built_orders': built,
                'no_room': sorted(self.no_room), 'overtime': self.overtime, 'overtime_ok': self.overtime_ok,
                'deferred': sorted(self.deferred), 'dropped': sorted(self.dropped),
                'no_window': sorted(self.no_window), 'no_center': sorted(self.no_center), 'prediction': self.prediction,
                'no_vehicle': sorted(self.no_vehicle), 'agents_off': sorted(self.agents_off),
                **({'undo': self.undo} if self.undo is not None else {}),
                **({'same_day': sorted(self.same_day)} if self.same_day else {})}

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
            nb = t.get('not_before')
            trips.append(DraftTrip(tid, truck, [c for c in stops if _is_int(c)], t.get('pinned') is True,
                                   float(nb) if _is_num(nb) and 0 <= nb <= 2 * 24 * 60 else None))
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
                   no_vehicle=cids('no_vehicle'),
                   agents_off={x for x in (raw.get('agents_off') or [])[:MAX_AGENTS] if _is_int(x)}
                   if isinstance(raw.get('agents_off'), list) else set(),
                   undo=raw.get('undo') if isinstance(raw.get('undo'), dict) else None, same_day=isns('same_day'))


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


def parse_agents(raw: Any) -> set[int] | None:
    """Список менеджеров (agent_id) из запроса или черновика; не список целых, длиннее MAX_AGENTS — None."""
    if not isinstance(raw, list) or len(raw) > MAX_AGENTS or not all(_is_int(x) for x in raw):
        return None
    return set(raw)


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


def prune(draft: Draft, stops: Sequence[Stop]) -> None:
    """Рейсы черновика — по точкам дня stops (после правки, меняющей заказы дня): клиент без заказов или без точки
    уходит из рейсов, пустой рейс — тоже (как _clean перед расчётом, но для сохраняемого черновика)."""
    _clean(draft, {s.customer_id: s for s in stops if s.point is not None})


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


def _yerevan(ctx: DayContext, s: Stop) -> bool:
    """Точка в зоне Еревана (№68: большая машина везёт её после малых и дольше)."""
    return s.point is not None and ctx.tn.in_yerevan(s.point)


def big_shown(ctx: DayContext, truck: fl.FleetTruck | None) -> bool:
    """Показывать ли «большая машина» (№68): машина большая и зона Еревана задана (пустая — правила нет, план прежний)."""
    return truck is not None and truck.big and len(ctx.tn.yerevan_zone) >= 3


def _yerevan_km(ctx: DayContext, code: str, s: Stop) -> float:
    """Плата за точку s на машине code (№68, приоритет малых машин): точка в зоне Еревана на большой машине — как
    ctx.tn.yerevan_km км пути (как в сборке, fleet._yerevan_bias); иначе 0.0."""
    return ctx.tn.yerevan_km if big_shown(ctx, ctx.trucks.get(code)) and _yerevan(ctx, s) else 0.0


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
           reorder: bool, start: float = 0.0, truck: str | None = None) -> tuple[list[int], float, float, float]:
    """(порядок клиентов, км, минуты, кг) рейса. reorder с выезда start: если порядок соблюдает окна приёма,
    2-opt их не нарушит (окна — с темпом машины truck, №66)."""
    kgs = [stops[c].kg / shares.get(c, 1) for c in cids]
    windows = [_span(ctx, c) for c in cids] if reorder and any(c in ctx.windows for c in cids) else None
    seq, km, minutes = fl.route_trip([stops[c].point for c in cids], kgs, ctx.depot, ctx.norms, ctx.tn,
                                     reorder=reorder, windows=windows, start=start, truck=truck)
    return [cids[i] for i in seq], km, minutes, math.fsum(kgs)


def _timeline(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop],
              shares: Mapping[int, int], parts: dict[int, dict[str, Any]] | None = None, open_end: bool = False
              ) -> dict[int, tuple[float, float, list[float]]]:
    """Рейсы машин подряд, с ожиданием у окон приёма: рейс → (выезд, минуты рейса от выезда, прибытия к точкам),
    время — минуты от начала дня машины. Рейс выезжает, как только машина вернулась, но не раньше, чем нужно к
    окну первой точки (fl.trip_schedule) — как его поставил fleet._plan_timed. Без окон — подряд, минуты те же,
    что у _route. parts — рейс → слагаемые его минут (fl.trip_schedule: загрузка, езда, ожидание, разгрузка; при обеде —
    и 'lunch': fl.Break или None). Обед в пути (№61, ctx.tn.lunch_minutes) — по правилу fleet (шапка модуля): один на
    день машины; open_end — за последним рейсом машины будут ещё рейсы (занятое время для раскладки вокруг: обед может
    встать и после его последней точки). Запас на рейс и темп машины (№66, ctx.tn) — по правилу fleet: минуты рейса — с
    запасом в конце (parts['buffer']), прибытия — без него."""
    used: dict[str, float] = {}
    out: dict[int, tuple[float, float, list[float]]] = {}
    lunch = ctx.tn.lunch_minutes > 0
    last = {t.truck: t.id for t in trips if any(c in stops for c in t.stops)}
    eaten: set[str] = set()
    for t in trips:
        cids = [c for c in t.stops if c in stops]
        if not cids:
            continue
        got: dict[str, Any] | None = {} if parts is not None or lunch else None
        free = used.get(t.truck, 0.0)
        if t.not_before is not None:   # новый рейс с заказами дня (№72) — не раньше «сейчас»
            free = max(free, t.not_before - ctx.work_start_min)
        depart, arrivals, minutes = fl.trip_schedule(
            [stops[c].point for c in cids], [stops[c].kg / shares.get(c, 1) for c in cids], ctx.depot, ctx.norms,
            ctx.tn, free, [_span(ctx, c) for c in cids], got,
            (t.truck not in eaten, open_end or last[t.truck] != t.id) if lunch else None, t.truck)
        if lunch and got.get('lunch') is not None:   # type: ignore[union-attr]
            eaten.add(t.truck)
        used[t.truck] = depart + minutes
        out[t.id] = (depart, minutes, arrivals)
        if parts is not None:
            parts[t.id] = got
    return out


def _occupied(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop], shares: Mapping[int, int],
              fed: set[str] | None = None
              ) -> tuple[dict[str, float], dict[str, list[tuple[float, float]]] | None, dict[int, float]]:
    """Чем машины уже заняты (рейсы trips по _timeline): (конец последнего рейса машины; занятые отрезки
    [выезд, возвращение] — только если между ними есть промежуток (рейс выезжает позже под окно первой точки),
    иначе None — день занят одним куском, как раньше; выезд каждого рейса). За рейсами встанут новые — обед (№61) по
    правилу с запасом (open_end); fed — сюда машины, чей обед уже в этих рейсах."""
    found: dict[int, dict[str, Any]] = {}
    tl = _timeline(ctx, trips, stops, shares, found, open_end=True)
    used: dict[str, float] = {}
    spans: dict[str, list[tuple[float, float]]] = {}
    gap = False
    for t in trips:
        if t.id in tl:
            depart, minutes, _ = tl[t.id]
            gap = gap or depart > used.get(t.truck, 0.0) + _EPS
            used[t.truck] = depart + minutes
            spans.setdefault(t.truck, []).append((depart, depart + minutes))
    if fed is not None:
        fed.update(t.truck for t in trips if (found.get(t.id) or {}).get('lunch') is not None)
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
                  built_at=now, deferred=set(old.deferred), dropped=set(old.dropped), agents_off=set(old.agents_off),
                  same_day=set(old.same_day))
    pinned = [DraftTrip(t.id, t.truck, list(t.stops), True, t.not_before) for t in old.trips if t.pinned and t.truck in codes]
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
        fed: set[str] = set()
        used, _, _ = _occupied(ctx, pinned, routable, shares, fed)
        trips = fl.route_day([s.point for s in rest], [s.kg for s in rest], [s.revenue for s in rest],
                             ctx.depot, sel, ctx.norms, ctx.tn, used, overflow=False,
                             windows=[_span(ctx, s.customer_id) for s in rest], center=[_central(ctx, s) for s in rest],
                             reasons=reasons, balance=True, solver=True,
                             allowed_trucks=[_allowed_trucks(ctx, s.customer_id) for s in rest], fed=fed) if rest and sel else []
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
    draft.undo = None
    shares = _shares(draft.trips)
    sel = _selected(ctx, draft.trucks)
    rest = [routable[c] for c in sorted(draft.no_room | draft.no_window | draft.no_vehicle) if c in routable and c not in shares]
    draft.overtime_ok = True
    if not rest or not sel:
        return draft
    fed: set[str] = set()
    used, busy, day_departs = _occupied(ctx, draft.trips, routable, shares, fed)
    # предел переработки; конец обычного дня — для приоритета малых машин в Ереване (№68, fleet._earliest_key)
    limit = (replace(ctx.tn, work_minutes=ctx.overtime_minutes, normal_minutes=ctx.tn.work_minutes)
             if ctx.overtime_minutes is not None else ctx.tn)
    reasons: dict[int, str] = {}
    departs: list[float] = []
    trips = fl.route_day([s.point for s in rest], [s.kg for s in rest], [s.revenue for s in rest],
                         ctx.depot, sel, ctx.norms, limit, used, overflow=ctx.overtime_minutes is None,
                         earliest=True, windows=[_span(ctx, s.customer_id) for s in rest],
                         center=[_central(ctx, s) for s in rest], reasons=reasons, busy=busy, departs=departs,
                         load_cap=fl.LOAD_CAP, allowed_trucks=[_allowed_trucks(ctx, s.customer_id) for s in rest], fed=fed)
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
    """Хоть одна машина плана работает дольше рабочего дня (рейсы подряд с ожиданием у окон и обедом, как в plan_view)."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    tl = _timeline(ctx, draft.trips, routable, _shares(draft.trips))
    return any(depart + minutes > ctx.tn.work_minutes + _EPS for depart, minutes, _ in tl.values())


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


# --- Конец рейса тянут мышью на шкале дня (ответ владельца №59) ---

def _closed_km(ctx: DayContext, cids: Sequence[int], stops: Mapping[int, Stop]) -> float:
    """Км рейса «склад → cids по порядку → склад» (без рейса — 0)."""
    if not cids:
        return 0.0
    pts = [ctx.depot, *(stops[c].point for c in cids), ctx.depot]
    return math.fsum(ctx.norms.km(a, b) for a, b in zip(pts, pts[1:]))


def _truck_day(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop], shares: Mapping[int, int],
               code: str) -> tuple[dict[int, float], float, int]:
    """Рейсы машины code подряд (_timeline): (возвращение каждого рейса, конец дня машины, точек позже окна приёма) —
    минуты от начала дня машины."""
    mine = [t for t in trips if t.truck == code]
    tl = _timeline(ctx, mine, stops, shares)
    ends: dict[int, float] = {}
    misses = 0
    for t in mine:
        if t.id in tl:
            depart, minutes, arrivals = tl[t.id]
            ends[t.id] = depart + minutes
            misses += sum(1 for c, at in zip([c for c in t.stops if c in stops], arrivals) if at > _span(ctx, c)[1] + _EPS)
    return ends, max(ends.values(), default=0.0), misses


def _can_carry(ctx: DayContext, sel: Sequence[fl.FleetTruck], code: str, cids: Sequence[int],
               stops: Mapping[int, Stop], shares: Mapping[int, int]) -> bool:
    """Рейс cids по силам машине code: право въезда (машина, центр) и груз не тяжелее предела сборки (fl.load_limit,
    №45: 90% тоннажа)."""
    truck = ctx.trucks[code]
    if any(not _vehicle_ok(ctx, c, code) or (_central(ctx, stops[c]) and not truck.center_ok) for c in cids):
        return False
    kgs = [stops[c].kg / shares.get(c, 1) for c in cids]
    lim = fl.load_limit(kgs, [_central(ctx, stops[c]) for c in cids], [_allowed_trucks(ctx, c) for c in cids], truck, sel)
    return math.fsum(kgs) <= lim + 0.5


def _moved(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop], cid: int, src: int | None,
           dst: DraftTrip) -> list[DraftTrip]:
    """Копия рейсов, где клиент cid перешёл из рейса src (None — «ещё не в рейсах») в рейс dst (новый рейс — dst, которого
    нет среди trips: он встаёт последним) на место с наименьшим объездом; опустевший рейс уходит."""
    out = [replace(t, stops=list(t.stops)) for t in trips]
    if all(t.id != dst.id for t in out):
        out.append(replace(dst, stops=list(dst.stops)))
    for t in out:
        if t.id == src:
            t.stops.remove(cid)
        if t.id == dst.id:
            t.stops = _insert_cheapest(ctx, t.stops, cid, stops)
    return [t for t in out if t.stops]


def _take(ctx: DayContext, draft: Draft, stops: Mapping[int, Stop], trips: list[DraftTrip], cid: int,
          touched: Sequence[int]) -> None:
    """Принять перенос: рейсы trips — в черновик, у затронутых рейсов порядок заново (2-opt с их выезда, как у move) —
    если рейс с ним возвращается не позже и окна приёма машины не нарушаются чаще (2-opt укорачивает км, а минуты с
    пробками и окнами могут вырасти; окна он бережёт, только если порядок их уже соблюдал)."""
    draft.trips = trips
    if any(t.id >= draft.next_id for t in trips):
        draft.next_id = max(t.id for t in trips) + 1
    for left in (draft.no_room, draft.no_window, draft.no_center, draft.no_vehicle):
        left.discard(cid)
    shares = _shares(trips)
    for t in trips:
        if t.id in touched:
            start = _timeline(ctx, trips, stops, shares)[t.id][0]
            was = t.stops
            ends, _, misses = _truck_day(ctx, trips, stops, shares, t.truck)
            t.stops, *_ = _route(ctx, was, stops, shares, reorder=True, start=start)
            ends2, _, misses2 = _truck_day(ctx, trips, stops, shares, t.truck)
            if ends2[t.id] > ends[t.id] + _EPS or misses2 > misses:
                t.stops = was


def _shrink(ctx: DayContext, draft: Draft, stops: Mapping[int, Stop], trip: DraftTrip, target: float) -> None:
    """Рейс trip должен вернуться к target: его магазины по одному уходят в рейсы других машин (или новым рейсом
    отмеченной машине), пока не вернётся. Каждый раз — перенос с наименьшим ростом км всего плана на минуту, которую рейс
    выигрывает (выигрыш сверх нужного до target не в счёт; переносы без роста км — первыми). Принимающая машина
    не позже конца дня (или своего прежнего конца), окна приёма у неё не нарушаются, груз — в пределе сборки;
    закреплённые рейсы не трогаются. Некуда — остаётся сколько успели. Км — с платой за точки Еревана на больших машинах
    (№68, _yerevan_km: как в сборке, малые машины — первыми)."""
    sel = _selected(ctx, draft.trucks)
    limit = ctx.overtime_minutes if draft.overtime_ok and ctx.overtime_minutes is not None else ctx.tn.work_minutes
    others = [t.car_code for t in sel if t.car_code != trip.truck]
    tid = trip.id
    for _ in range(len(trip.stops)):
        trip = next((t for t in draft.trips if t.id == tid), None)   # _take кладёт в черновик копии рейсов
        if trip is None:
            return
        shares = _shares(draft.trips)
        ends, _, _ = _truck_day(ctx, draft.trips, stops, shares, trip.truck)
        now = ends.get(trip.id)
        if now is None or now <= target + _EPS:
            return
        receivers = [t for t in draft.trips if t.truck in others and not t.pinned]
        base_km = _closed_km(ctx, trip.stops, stops)
        cands = []
        for cid in trip.stops:
            if shares[cid] != 1:          # тяжёлый заказ на несколько поездок — переносят целиком вручную
                continue
            rest = [c for c in trip.stops if c != cid]
            saved_km = base_km - _closed_km(ctx, rest, stops)
            if rest:
                kept = [replace(t, stops=rest if t.id == trip.id else t.stops) for t in draft.trips]
                saved = now - _truck_day(ctx, kept, stops, shares, trip.truck)[0][trip.id]
            else:
                saved = math.inf
            if saved <= _EPS:
                continue
            useful = min(saved, now - target)   # сверх нужного выигрыш не в счёт
            options = [*receivers, *(DraftTrip(draft.next_id, code, []) for code in others)]
            for r in options:
                if not _can_carry(ctx, sel, r.truck, [*r.stops, cid], stops, shares):
                    continue
                net = (_closed_km(ctx, _insert_cheapest(ctx, r.stops, cid, stops), stops)
                       - _closed_km(ctx, r.stops, stops) - saved_km
                       + _yerevan_km(ctx, r.truck, stops[cid]) - _yerevan_km(ctx, trip.truck, stops[cid]))
                cands.append(((0, -useful) if net <= 0 else (1, net / useful), cid, r.id, r))
        state: dict[str, tuple[float, int]] = {}
        for _, cid, _, r in sorted(cands, key=lambda x: (x[0], x[1], x[2])):
            if r.truck not in state:
                _, end0, miss0 = _truck_day(ctx, draft.trips, stops, shares, r.truck)
                state[r.truck] = (end0, miss0)
            end0, miss0 = state[r.truck]
            trial = _moved(ctx, draft.trips, stops, cid, trip.id, r)
            _, end1, miss1 = _truck_day(ctx, trial, stops, _shares(trial), r.truck)
            if end1 <= max(limit, end0) + _EPS and miss1 <= miss0:
                _take(ctx, draft, stops, trial, cid, (trip.id, r.id))
                break
        else:
            return


def _grow(ctx: DayContext, draft: Draft, stops: Mapping[int, Stop], trip: DraftTrip, target: float) -> None:
    """Машина рейса trip работает до target: в рейс берутся магазины «ещё не в рейсах» (сначала — их надо везти), затем
    из рейсов других машин — каждый раз тот, с которым км всего плана растут меньше всего, пока рейс успевает к target.
    Рейс полон (предел сборки) и он у машины последний — после него новый рейс этой машины. Окна приёма машины не
    нарушаются; позже конца дня — только если сам конец рейса тянут за него (не позже предела переработки); закреплённые
    рейсы других машин не трогаются. Км — с платой за точки Еревана на больших машинах (№68, _yerevan_km, как в _shrink)."""
    sel = _selected(ctx, draft.trucks)
    code = trip.truck
    limit = ctx.overtime_minutes if draft.overtime_ok and ctx.overtime_minutes is not None else ctx.tn.work_minutes
    shares = _shares(draft.trips)
    ends, end, _ = _truck_day(ctx, draft.trips, stops, shares, code)
    if trip.id not in ends:
        return
    last = [t for t in draft.trips if t.truck == code][-1].id == trip.id
    # за конец дня — только если туда тянут сам последний рейс; следующие рейсы машины сдвигаются не дальше конца дня
    cap = max(limit, target) if last else max(limit, end)
    if ctx.overtime_minutes is not None:
        cap = min(cap, max(limit, ctx.overtime_minutes))
    follow: int | None = None
    for _ in range(len(stops)):
        shares = _shares(draft.trips)
        ends, _, miss = _truck_day(ctx, draft.trips, stops, shares, code)
        lead = follow if follow in ends else trip.id
        if ends[lead] >= target - _EPS:
            return
        by_id = {t.id: t for t in draft.trips}
        dests = [by_id[i] for i in (trip.id, follow) if i in by_id]
        if last and follow not in by_id:
            dests.append(DraftTrip(draft.next_id, code, []))
        pool = [(cid, None) for cid in sorted(stops) if cid not in shares]
        pool += [(cid, t) for t in draft.trips if t.truck != code and not t.pinned for cid in t.stops if shares[cid] == 1]
        base = {t.id: _closed_km(ctx, t.stops, stops) for t in draft.trips if t.truck != code}
        cands = []
        for cid, src in pool:
            saved_km = 0.0 if src is None else base[src.id] - _closed_km(ctx, [c for c in src.stops if c != cid], stops)
            saved_km -= _yerevan_km(ctx, code, stops[cid]) - (0.0 if src is None else _yerevan_km(ctx, src.truck, stops[cid]))
            for r in dests:
                if not _can_carry(ctx, sel, code, [*r.stops, cid], stops, shares):
                    continue
                net = (_closed_km(ctx, _insert_cheapest(ctx, r.stops, cid, stops), stops)
                       - _closed_km(ctx, r.stops, stops) - saved_km)
                cands.append(((src is not None, net, cid, r.id), cid, src, r))
        for _, cid, src, r in sorted(cands, key=lambda x: x[0]):
            trial = _moved(ctx, draft.trips, stops, cid, None if src is None else src.id, r)
            t_ends, t_end, t_miss = _truck_day(ctx, trial, stops, _shares(trial), code)
            at = t_ends[r.id if r.id != trip.id else (follow if follow in t_ends else trip.id)]
            if at <= target + _EPS and t_end <= cap + _EPS and t_miss <= miss:
                _take(ctx, draft, stops, trial, cid, (r.id, *(() if src is None else (src.id,))))
                if r.id != trip.id:
                    follow = r.id
                break
        else:
            return


def _resize(ctx: DayContext, draft: Draft, stops: Mapping[int, Stop], edit: Mapping[str, Any]) -> Draft:
    """{"action": "resize", "trip": id, "return": минут от полуночи} — конец полосы рейса на шкале дня потянули мышью:
    раньше — магазины уходят другим машинам (_shrink), позже — машина берёт магазины других и «ещё не в рейсах» (_grow)."""
    trip = _trip(draft, edit.get('trip'))
    at = edit.get('return')
    if not _is_num(at) or not 0 <= at <= 2 * 24 * 60:
        raise DispatchError('Նշեք, թե երբ պետք է վերադառնա երթը')
    if trip.truck not in ctx.trucks or trip.truck not in draft.trucks:
        raise DispatchError('Эта машина сегодня не работает — отметьте её в шаге 1')
    target = at - ctx.work_start_min
    ends, _, _ = _truck_day(ctx, draft.trips, stops, _shares(draft.trips), trip.truck)
    if trip.id not in ends:
        return draft
    if target < ends[trip.id] - _EPS:
        _shrink(ctx, draft, stops, trip, target)
    elif target > ends[trip.id] + _EPS:
        _grow(ctx, draft, stops, trip, target)
    return draft


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
      {"action": "resize", "trip": id, "return": минут от полуночи} — конец рейса потянули на шкале дня (_resize);
      {"action": "undo"} — «Չեղարկել»: черновик до последнего resize (Draft.undo);
      {"action": "exclude" | "include", "order": fISN} — «не везём сегодня» / вернуть; заказ прошлых
          дней (backlog_ids) — убрать из развоза / добавить в развоз (перенесённый сюда — carried —
          убранный запоминается в dropped); перенос на завтра снимается;
      {"action": "agents", "off": [agent_id, …]} — фильтр «Մենեջերներ»: заказы этих менеджеров не везём
          (остальные — везём); точки, где заказов не осталось, уходят из рейсов, вернувшиеся — «ещё не в рейсах»;
      {"action": "defer_trip", "trip": id} — «везти завтра» (№25: рейс дешевле min_trip_revenue): заказы
          рейса — не сегодня (excluded / из added) и в deferred — следующий день доставки возьмёт их сам;
          заказ старше defer_since (вне окна «не отгружены с прошлых дней» следующего дня) — ошибка:
          завтра его не будет видно, решать надо сегодня.
    Ошибка — DispatchError с текстом для логиста."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    _clean(draft, routable)
    action = edit.get('action')
    if action == 'undo':
        if draft.undo is None:
            raise DispatchError('Չեղարկելու բան չկա՝ պլանը դրանից հետո արդեն փոխվել է')
        return Draft.from_json(draft.undo)
    draft.undo = None
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
    if action == 'agents':
        off = parse_agents(edit.get('off'))
        if off is None:
            raise DispatchError('Список менеджеров не принят — обновите страницу')
        draft.agents_off = off
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
                draft.same_day.discard(o.isn)    # заказ дня, взятый сегодня (№72), — снова заказ следующего дня
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
    if action == 'resize':
        before = {k: v for k, v in draft.to_json().items() if k not in ('prediction', 'undo')}
        draft = _resize(ctx, draft, routable, edit)
        draft.undo = before
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
            t.stops, *_ = _route(ctx, t.stops, routable, shares, reorder=True, start=start, truck=t.truck)
    return draft


# --- Новые заказы дня: взять в сегодняшний развоз (ответ владельца №72) ---

SAME_DAY_SHOWN = 8     # вариантов в ответе — самые дешёвые
# вид варианта при равной цене: в ту же точку / в рейс, затем новый рейс отмеченной машины, затем машина не из шага 1
_SAME_DAY_RANK = {'same_stop': 0, 'insert': 0, 'trip': 1, 'idle': 1, 'extra': 2}
# машина не из шага 1 (extra) — после всех вариантов отмеченных машин, при любой цене: «все работающие машины всегда
# отмечены» (№71) — неотмеченная, скорее всего, сегодня не выходит. Пока владелец не решил (№72); False — по цене со всеми
SAME_DAY_EXTRA_LAST = True


@dataclass
class _SameDayPlan:
    key: str                     # 'same_stop' | 'insert:<рейс>' | 'trip:<машина>' | 'extra:<машина>'
    kind: str
    truck: str
    trips: list[DraftTrip]       # рейсы черновика с новыми заказами
    changed: set[int]            # рейсы, которые вариант меняет или добавляет
    view: dict[str, Any]


def _trip_amd(ctx: DayContext, cids: Sequence[int], routable: Mapping[int, Stop], shares: Mapping[int, int],
              code: str) -> float:
    """Расход рейса в драмах — как operating_cost_amd плана (топливо по цене дня + износ)."""
    kgs = [routable[c].kg / shares.get(c, 1) for c in cids]
    cost = fl.trip_running_cost([routable[c].point for c in cids], kgs, ctx.depot, ctx.norms, ctx.trucks[code])
    return cost.total_amd(ctx.tn.fuel_price)


def _same_day_base(ctx: DayContext, base: Sequence[Stop], draft: Draft, now_min: float
                   ) -> tuple[list[DraftTrip], dict[int, Stop], dict[int, int], dict[int, tuple[float, float, list[float]]],
                              set[int]]:
    """План дня без новых заказов: (копии рейсов по точкам base, точки, доли, время рейсов, начатые рейсы — начало загрузки
    по плану не позже now_min, минуты от начала дня машины)."""
    routable0 = {s.customer_id: s for s in base if s.point is not None}
    tmp = Draft(trips=[replace(t, stops=list(t.stops)) for t in draft.trips])
    _clean(tmp, routable0)
    shares0 = _shares(tmp.trips)
    tl0 = _timeline(ctx, tmp.trips, routable0, shares0)
    return tmp.trips, routable0, shares0, tl0, {tid for tid, (depart, _, _) in tl0.items() if depart <= now_min + _EPS}


def started_trips(ctx: DayContext, base: Sequence[Stop], draft: Draft, now_min: float) -> set[int]:
    """Рейсы, чья загрузка по плану уже началась (начало загрузки не позже now_min): машина в рейсе новый заказ не берёт и
    взятый заказ из него уже не вернуть на завтра (№72)."""
    return _same_day_base(ctx, base, draft, now_min)[4]


def same_day_blocked(ctx: DayContext, base: Sequence[Stop], stops: Sequence[Stop], draft: Draft, cids: Collection[int],
                     now_min: float) -> dict[int, str]:
    """Клиенты cids, чьи новые заказы сегодня не взять: 'no_coords' — нет точки (stops — точки дня с новыми заказами);
    'started' — клиент уже в рейсе, чья загрузка началась (base — точки дня без новых заказов)."""
    trips0, _, _, _, started = _same_day_base(ctx, base, draft, now_min)
    routable = {s.customer_id for s in stops if s.point is not None}
    blocked: dict[int, str] = {}
    for c in sorted(set(cids)):
        if c not in routable:
            blocked[c] = 'no_coords'
        elif any(c in t.stops for t in trips0 if t.id in started):
            blocked[c] = 'started'
    return blocked


def _same_day_plans(ctx: DayContext, base: Sequence[Stop], stops: Sequence[Stop], draft: Draft, cids: Collection[int],
                    now_min: float) -> tuple[list[_SameDayPlan], dict[int, str]]:
    """Как взять новые заказы дня клиентов cids в развоз сегодня — все варианты, самые дешёвые первыми, и клиенты, которых
    взять нельзя: {клиент: 'no_coords' — нет точки | 'started' — он уже в рейсе, чья загрузка по плану началась}.
    base — точки дня без этих заказов, stops — с ними (у клиента, который уже в развозе, точка тяжелее); now_min — сейчас,
    минуты от начала дня машины. Рейс «начат», если начало его загрузки по плану (_timeline) не позже now_min: его машина
    и точки не меняются — машина в рейсе новый заказ не берёт (ответ «Բ»). Клиент уже в рейсе, который ещё не грузится, —
    заказ едет в ту же точку того же рейса; остальные (новые точки) — все вместе одним из способов:
    - insert — вставкой (по одной, на место с наименьшим объездом) в рейс отмеченной машины, чья загрузка не началась
      (закреплённый рейс — нет: его состав логист уже решил);
    - trip / idle — новым рейсом отмеченной машины после её последнего рейса (idle — сегодня рейсов у неё нет);
    - extra — новым рейсом готовой машины, не отмеченной в шаге 1.
    Новый рейс грузится не раньше «сейчас» (not_before), порядок его точек — 2-opt с окнами приёма. Вариант годится, если
    груз изменённого рейса — в пределе сборки (_can_carry: тоннаж, центр, допуск машин), машина возвращается не позже
    конца рабочего дня (принятая переработка — её предела; машина и так позже — не позже, чем сейчас) и окна приёма у неё
    не нарушаются чаще, чем сейчас. Порядок точек плана не переставляется. Цена — рост расхода в драмах (как
    operating_cost_amd плана), км и минут работы изменённых и новых рейсов; extra — после остальных (SAME_DAY_EXTRA_LAST)."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    trips0, routable0, shares0, tl0, started = _same_day_base(ctx, base, draft, now_min)
    blocked = same_day_blocked(ctx, base, stops, draft, cids, now_min)
    want = [c for c in sorted(set(cids)) if c not in blocked]
    if not want:
        return [], blocked
    same = [c for c in want if c in shares0]
    free = [c for c in want if c not in shares0]
    same_trips = {t.id for t in trips0 if any(c in t.stops for c in same)}
    sel = _selected(ctx, draft.trucks)
    limit = ctx.overtime_minutes if draft.overtime_ok and ctx.overtime_minutes is not None else ctx.tn.work_minutes
    day0: dict[str, tuple[float, int]] = {}
    old = {t.id: t for t in trips0}
    out: list[_SameDayPlan] = []

    def try_plan(key: str, kind: str, truck: str, trip: int, trial: list[DraftTrip],
                 fleet: Sequence[fl.FleetTruck]) -> None:
        ids = same_trips | {trip}
        shares = _shares(trial)
        if any(t.id in ids and (t.truck not in ctx.trucks or not _can_carry(ctx, fleet, t.truck, t.stops, routable, shares))
               for t in trial):
            return
        codes = {t.truck for t in trial if t.id in ids}
        for code in codes:
            if code not in day0:
                day0[code] = _truck_day(ctx, trips0, routable0, shares0, code)[1:]
            end0, miss0 = day0[code]
            _, end1, miss1 = _truck_day(ctx, trial, routable, shares, code)
            if end1 > max(limit, end0) + _EPS or miss1 > miss0:
                return
        tl = _timeline(ctx, [t for t in trial if t.truck in codes], routable, shares)
        km = amd = minutes = 0.0
        etas = []
        for t in trial:
            if t.id not in ids:
                continue
            km += _closed_km(ctx, t.stops, routable)
            amd += _trip_amd(ctx, t.stops, routable, shares, t.truck)
            minutes += tl[t.id][1]
            if t.id in old:
                km -= _closed_km(ctx, old[t.id].stops, routable0)
                amd -= _trip_amd(ctx, old[t.id].stops, routable0, shares0, t.truck)
                minutes -= tl0[t.id][1]
            etas += [{'customer_id': c, 'eta': _hhmm(ctx.work_start_min + at)}
                     for c, at in zip(t.stops, tl[t.id][2]) if c in want]
        depart, trip_min, _ = tl[trip]
        carried = next(t for t in trial if t.id == trip).stops
        load = ctx.tn.load(math.fsum(routable[c].kg / shares[c] for c in carried))
        out.append(_SameDayPlan(key, kind, truck, trial, ids, {
            'key': key, 'kind': kind, 'truck': truck, 'name': ctx.trucks[truck].name, 'trip': trip if trip in old else None,
            'km': _r(km), 'minutes': round(minutes), 'amd': round(amd),
            'loading_start': _hhmm(ctx.work_start_min + depart), 'depart': _hhmm(ctx.work_start_min + depart + load),
            'return': _hhmm(ctx.work_start_min + depart + trip_min), 'stops': etas}))

    def copy() -> list[DraftTrip]:
        return [replace(t, stops=list(t.stops)) for t in trips0]

    if not free:
        first = next(t for t in trips0 if t.id in same_trips)
        try_plan('same_stop', 'same_stop', first.truck, first.id, copy(), sel)
    else:
        for t in trips0:
            if t.id in started or t.pinned or t.truck not in draft.trucks or t.truck not in ctx.trucks:
                continue
            trial = copy()
            dst = next(x for x in trial if x.id == t.id)
            for c in free:
                dst.stops = _insert_cheapest(ctx, dst.stops, c, routable)
            try_plan(f'insert:{t.id}', 'insert', t.truck, t.id, trial, sel)
        ones = {c: 1 for c in free}
        working = sorted(set(draft.trucks) & set(ctx.trucks))
        for code in working + sorted(set(ctx.trucks) - set(draft.trucks)):
            extra = code not in draft.trucks
            busy = any(t.truck == code for t in trips0)
            start = max(now_min, _truck_day(ctx, trips0, routable0, shares0, code)[1])
            order, *_ = _route(ctx, free, routable, ones, reorder=True, start=start, truck=code)
            trial = [*copy(), DraftTrip(draft.next_id, code, order, not_before=float(ctx.work_start_min + now_min))]
            try_plan(f'{"extra" if extra else "trip"}:{code}', 'extra' if extra else 'trip' if busy else 'idle', code,
                     draft.next_id, trial, [*sel, ctx.trucks[code]] if extra else sel)
    out.sort(key=lambda p: (SAME_DAY_EXTRA_LAST and p.kind == 'extra', p.view['amd'], p.view['km'],
                            _SAME_DAY_RANK[p.kind], p.key))
    return out, blocked


def same_day_options(ctx: DayContext, base: Sequence[Stop], stops: Sequence[Stop], draft: Draft, cids: Collection[int],
                     now_min: float) -> dict[str, Any]:
    """Предложения странице (№72): {'options': до SAME_DAY_SHOWN самых дешёвых вариантов _same_day_plans — key (его шлёт
    «Ընտրել»), вид, машина, меняемый рейс (новый — None), +км, +минуты работы, +֏, начало загрузки, выезд и возвращение
    рейса, прибытие к клиентам cids; 'blocked': клиенты, которых сегодня взять нельзя, и почему}. «Թողնել վաղվան» можно
    всегда, без расчёта: ничего не меняется."""
    plans, blocked = _same_day_plans(ctx, base, stops, draft, cids, now_min)
    return {'options': [p.view for p in plans[:SAME_DAY_SHOWN]],
            'blocked': [{'customer_id': c, 'reason': r} for c, r in sorted(blocked.items())]}


def take_same_day(ctx: DayContext, base: Sequence[Stop], stops: Sequence[Stop], draft: Draft, cids: Collection[int],
                  isns: Collection[str], key: Any, now_min: float) -> Draft:
    """Взять новые заказы дня isns (их клиенты — cids) в развоз сегодня вариантом key (_same_day_plans). Варианты считаются
    заново на «сейчас»: пока логист выбирал, рейс мог начать грузиться. Варианта нет или клиента взять нельзя —
    DispatchError. Рейсы черновика — рейсы варианта; изменённые и новые рейсы закрепляются (пересборка их не трогает и
    новый рейс не уйдёт раньше «сейчас»); машина не из шага 1 (extra) становится машиной дня."""
    plans, blocked = _same_day_plans(ctx, base, stops, draft, cids, now_min)
    if blocked:
        raise DispatchError('Այս պատվերներից մեկի մեքենան արդեն բեռնվում է կամ խանութի տեղը քարտեզում չկա — '
                            'թարմացրեք առաջարկները')
    plan = next((p for p in plans if p.key == key), None)
    if plan is None:
        raise DispatchError('Այս առաջարկն այլևս հնարավոր չէ — թարմացրեք առաջարկները')
    draft.undo = None
    draft.trips = plan.trips
    for t in draft.trips:
        if t.id in plan.changed:
            t.pinned = True
    draft.next_id = max(draft.next_id, *(t.id + 1 for t in plan.trips))
    if plan.kind == 'extra':
        draft.trucks = sorted({*draft.trucks, plan.truck})
    draft.same_day |= set(isns)
    for left in (draft.no_room, draft.no_window, draft.no_center, draft.no_vehicle):
        left.difference_update(cids)
    return draft


def drop_same_day(draft: Draft, isns: Collection[str]) -> Draft:
    """Вернуть взятые заказы дня isns в развоз следующего дня: точки без заказов уходят из рейсов (views — prune по точкам
    дня), опустевший рейс — тоже."""
    if not isns or not set(isns) <= draft.same_day:
        raise DispatchError('Պատվերն այսօրվա առաքման մեջ չէ — թարմացրեք էջը')
    draft.undo = None
    draft.same_day -= set(isns)
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
    Занятость машины своими рейсами здесь не учитывается: сборка ищет меньше дизеля за весь день, а не за один рейс.
    Большая машина (№68) — big; с точками Еревана — плата приоритета малых машин yerevan_km (км её пути, как в сборке)."""
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
        penalty = math.fsum(_yerevan_km(ctx, o.car_code, routable[c]) for c in cids)
        out.append({'car_code': o.car_code, 'name': o.name, 'capacity_kg': o.capacity_kg, 'center_ok': o.center_ok,
                    'load_pct': round(kg / o.capacity_kg * 100.0), 'reasons': why, 'vehicle_denied': denied,
                    'liters': _r(fl.trip_running_cost(pts, kgs, ctx.depot, ctx.norms, o).liters),
                    **({'big': True} if big_shown(ctx, o) else {}), **({'yerevan_km': _r(penalty)} if penalty else {})})
    return out


def _trip_explain(ctx: DayContext, sel: Sequence[fl.FleetTruck], code: str, truck: fl.FleetTruck | None,
                  cids: Sequence[int], routable: Mapping[int, Stop], kgs: Sequence[float], parts: Mapping[str, Any],
                  free: float, depart: float, minutes: float) -> dict[str, Any]:
    """Почему рейс такой («Ինչու է այս երթը այսպես» на странице) — только цифры этого же расчёта: слагаемые минут рейса
    (из _timeline, в сумме — его минуты; обед в рейсе или в дороге — lunch_min), простой на складе до выезда (выезд
    позже, чтобы не ждать у окна первой точки; обед на складе до загрузки — не в нём), запас до конца рабочего дня, предел
    загрузки, расход, объезд центра и другие машины дня на этот рейс."""
    legs = parts['legs']
    brk = parts.get('lunch')
    kg = math.fsum(kgs)
    limit = (fl.load_limit(kgs, [_central(ctx, routable[c]) for c in cids], [_allowed_trucks(ctx, c) for c in cids],
                           truck, sel) if truck is not None else None)
    bypass_km, bypass_legs = _bypass_km(ctx, [routable[c].point for c in cids])
    heavy = (truck is not None and limit is not None and len(cids) == 1
             and limit > fl.LOAD_CAP * truck.capacity_kg + _EPS and kg > fl.LOAD_CAP * truck.capacity_kg + _EPS)
    city = ctx.tn.yerevan_of(code) * sum(1 for c in cids if _yerevan(ctx, routable[c]))   # №68: уже в unload_min
    penalty = math.fsum(_yerevan_km(ctx, code, routable[c]) for c in cids)                  # №68: плата приоритета, км
    return {
        'loading_min': _r(parts['loading']), 'drive_min': _r(math.fsum(x for x, _, _ in legs)),
        'unload_min': _r(math.fsum(x for _, _, x in legs)), 'wait_min': _r(math.fsum(x for _, x, _ in legs)),
        'back_min': _r(legs[-1][0]),
        # простой до выезда без обеда на складе (его показывает строка обеда; простой из-за окна первой точки — свой текст)
        'idle_before_min': _r(max(0.0, depart - free - (ctx.tn.lunch_minutes if brk is not None and brk.stop is None
                                                         else 0.0))),
        'end_slack_min': _r(ctx.tn.work_minutes - (depart + minutes)),
        'load_cap_pct': round(fl.LOAD_CAP * 100), 'load_limit_kg': round(limit) if limit is not None else None,
        'over_limit': limit is not None and kg > limit + _EPS, 'heavy_alone': heavy,
        'l100': _r(truck.l100) if truck else None,
        'fuel_empty_l100': truck.fuel_empty_l_per_100km if truck else None,
        'fuel_full_l100': truck.fuel_full_l_per_100km if truck else None,
        'bypass_km': _r(bypass_km), 'bypass_legs': bypass_legs,
        'others': _alternatives(ctx, sel, code, cids, routable, kgs),
        **({'lunch_min': _r(brk.added if brk.stop is not None else 0.0)} if brk is not None else {}),
        **({'buffer_min': _r(parts['buffer'])} if 'buffer' in parts else {}),   # запас на рейс (№66)
        **({'yerevan_min': _r(city)} if city else {}),   # большая машина в Ереване (№68)
        **({'yerevan_km': _r(penalty)} if penalty else {}),
    }


def _day_explain(ctx: DayContext, routable: Mapping[int, Stop], draft: Draft, sel: Sequence[fl.FleetTruck],
                 trips_of: Mapping[str, int]) -> dict[str, Any]:
    """Что учитывает расчёт дня («Ինչ է հաշվի առել հաշվարկը» на странице): машины дня (с действующим расходом),
    магазины в центре, с окном приёма и с допуском машин, предел загрузки и выравнивание загрузки (fleet._balance),
    закреплённые рейсы, нормы загрузки и разгрузки (загрузка — те же числа, что у tn.load), правило точки магазина
    (ERP дальше ERP_GPS_MAX_GAP_KM от GPS менеджера — GPS), решатель и ctx.model (дороги, минуты, выученные нормы — views._dispatch_ctx)."""
    tn = ctx.tn
    # большая машина в Ереване (№68) — только когда правило в деле: среди машин дня есть большая и в зоне есть магазины
    city = sum(1 for s in routable.values() if _yerevan(ctx, s))
    big = [x.car_code for x in sel if big_shown(ctx, x)]
    return {
        'trucks': [{'car_code': x.car_code, 'name': x.name, 'capacity_kg': x.capacity_kg, 'l100': _r(x.l100),
                    'fuel_empty_l100': x.fuel_empty_l_per_100km, 'fuel_full_l100': x.fuel_full_l_per_100km,
                    'wear': x.wear_amd_per_km is not None or x.wear_load_amd_per_km is not None,
                    'center_ok': x.center_ok, 'trips': trips_of.get(x.car_code, 0),
                    **({'big': True} if big_shown(ctx, x) else {})} for x in sel],
        **({'yerevan': {'stores': city, 'big': big, 'minutes': max(tn.yerevan_of(c) for c in big),
                        'penalty_km': tn.yerevan_km}} if city and big else {}),
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
    explain=False — без них (км до правки, прогноз для «план — факт»: литры других машин и объезд не нужны).
    Обед в пути (№61): у рейса, где он есть, — lunch: где (where: store — после разгрузки у магазина, depot — на складе
    до загрузки, road — в дороге в конце окна обеда), начало и конец (HH:MM; на складе и в дороге — весь обед, у магазина
    — сколько добавилось после разгрузки), минут обеда, добавлено к рейсу, после какой точки (номер в stops; None — до
    первой); время рейса и машины — с ним. Без обеда план — прежний до байта.
    Запас на рейс (№66): у рейса с запасом — buffer: минуты и с какого времени (возвращение по медиане; return — с
    запасом). Без выученного запаса — прежний до байта."""
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
            city = ctx.tn.yerevan_of(t.truck) if _yerevan(ctx, routable[c]) else 0.0   # №68: уже в unload_min
            marks.append({'eta': _hhmm(ctx.work_start_min + at), 'window_miss': at > late + _EPS,
                          'center': central, 'center_miss': central and not (truck is not None and truck.center_ok),
                          'vehicle_miss': not _vehicle_ok(ctx, c, t.truck),
                          'arrive': _hhmm(ctx.work_start_min + at - wait),   # приехал; eta — начало разгрузки
                          'drive_min': _r(drive), 'wait_min': _r(wait), 'unload_min': _r(unload),
                          'margin_min': _r(late - at) if math.isfinite(late) else None,
                          **({'yerevan_min': _r(city)} if city else {})})
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
        brk = parts[t.id].get('lunch')
        if brk is not None:
            meal = ctx.tn.lunch_minutes if brk.stop is None else brk.added
            after = (brk.stop - 1 if brk.stop else None) if brk.road else brk.stop
            tj['lunch'] = {'start': _hhmm(ctx.work_start_min + brk.at), 'end': _hhmm(ctx.work_start_min + brk.at + meal),
                           'minutes': _r(ctx.tn.lunch_minutes), 'added_min': _r(brk.added), 'after_stop': after,
                           'where': 'road' if brk.road else 'depot' if brk.stop is None else 'store'}
        if parts[t.id].get('buffer'):
            tj['buffer'] = {'minutes': _r(parts[t.id]['buffer']),
                            'start': _hhmm(ctx.work_start_min + slot['used'] - parts[t.id]['buffer'])}
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
            'center_ok': truck.center_ok if truck else None, **({'big': True} if big_shown(ctx, truck) else {}),
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

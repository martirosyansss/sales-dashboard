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
  заказ — несколько поездок поровну: клиент встречается в k рейсах — в каждом 1/k его кг.
- Черновик плана (store.dispatch_plan) — машины дня, исключённые заказы и рейсы (клиенты по порядку
  объезда, машина, «закреплён»). Цифры рейсов всегда пересчитываются по текущим заказам: новые
  заказы попадают в «ещё не в рейсах», исчезнувшие — убираются из рейсов.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from . import fleet as fl
from .geo import Coord, Point

if TYPE_CHECKING:
    from .evaluate import Norms

ISN_RE = re.compile(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$')
_EPS = 1e-6
MAX_TRIPS = 500           # защита от битого черновика
BACKLOG_WORKDAYS = 2      # «не отгружены с прошлых дней» — заказы ещё двух рабочих дней раньше окна
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
    """Реализация за прошедший день (SALES): кто и на какой машине вёз."""
    customer_id: int
    agent_id: int
    car_code: str
    revenue: float
    kg: float


@dataclass(frozen=True)
class DispatchData:
    orders: tuple[DispatchOrder, ...]
    customers: dict[int, tuple[str, str]]          # клиент → (код, название)
    addresses: dict[int, str]                      # клиент → адрес текстом
    agent_cars: dict[int, tuple[str, ...]]         # менеджер → машины за 90 дней, от самой частой
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

    def to_json(self) -> dict[str, Any]:
        return {'trucks': list(self.trucks), 'excluded': sorted(self.excluded), 'added': sorted(self.added),
                'trips': [{'id': t.id, 'truck': t.truck, 'stops': list(t.stops), 'pinned': t.pinned}
                          for t in self.trips],
                'next_id': self.next_id, 'built_at': self.built_at}

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
        return cls(trucks, excluded, added, trips, next_id, built)


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


def _hhmm(minutes: float) -> str:
    m = int(round(minutes))
    return f'{m // 60 % 24:02d}:{m % 60:02d}'


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


def _route(ctx: DayContext, cids: Sequence[int], stops: Mapping[int, Stop], shares: Mapping[int, int],
           reorder: bool) -> tuple[list[int], float, float, float]:
    """(порядок клиентов, км, минуты, кг) рейса."""
    kgs = [stops[c].kg / shares.get(c, 1) for c in cids]
    seq, km, minutes = fl.route_trip([stops[c].point for c in cids], kgs, ctx.depot, ctx.norms, ctx.tn,
                                     reorder=reorder)
    return [cids[i] for i in seq], km, minutes, math.fsum(kgs)


# --- Сборка рейсов ---

def build(ctx: DayContext, stops: Sequence[Stop], old: Draft | None, trucks: Sequence[str],
          now: str) -> Draft:
    """«Собрать рейсы»: заказы с координатами (кроме исключённых) → рейсы выбранных машин.
    Закреплённые логистом рейсы прежнего черновика остаются как есть (если их машина работает), их время
    машина уже занята; остальное раскладывается заново."""
    old = old or Draft()
    routable = {s.customer_id: s for s in stops if s.point is not None}
    sel = _selected(ctx, trucks)
    codes = {t.car_code for t in sel}
    draft = Draft(trucks=sorted(codes), excluded=set(old.excluded), added=set(old.added), next_id=old.next_id,
                  built_at=now)
    pinned = [DraftTrip(t.id, t.truck, list(t.stops), True) for t in old.trips if t.pinned and t.truck in codes]
    tmp = Draft(trips=pinned)
    _clean(tmp, routable)
    pinned = tmp.trips
    shares = _shares(pinned)
    used: dict[str, float] = {}
    for t in pinned:
        _, _, minutes, _ = _route(ctx, t.stops, routable, shares, reorder=False)
        used[t.truck] = used.get(t.truck, 0.0) + minutes
    taken = set(shares)
    rest = [routable[c] for c in sorted(routable) if c not in taken]
    trips = fl.route_day([s.point for s in rest], [s.kg for s in rest], [s.revenue for s in rest],
                         ctx.depot, sel, ctx.norms, ctx.tn, used) if rest and sel else []
    draft.trips = list(pinned)
    for t in trips:
        draft.trips.append(DraftTrip(draft.next_id, t.truck, [rest[i].customer_id for i in t.items]))
        draft.next_id += 1
    return draft


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
               order_ids: set[str], backlog_ids: set[str] = frozenset()) -> Draft:
    """Правка логиста (§3) поверх черновика; затронутые рейсы пересчитываются (порядок — 2-opt).
    edit:
      {"action": "move", "customer_id", "from_trip": id|null, "to_trip": id|null, "truck": код|null}
          — перенести клиента в другой рейс; to_trip = null и truck — новый рейс этой машины;
            to_trip = null и truck = null — убрать из рейсов («ещё не в рейсах»);
      {"action": "pin", "trip": id, "truck": код} — закрепить машину за рейсом (рейс уходит к ней);
      {"action": "unpin", "trip": id};
      {"action": "exclude" | "include", "order": fISN} — «не везём сегодня» / вернуть; заказ прошлых
          дней (backlog_ids) — убрать из развоза / добавить в развоз.
    Ошибка — DispatchError с текстом для логиста."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    _clean(draft, routable)
    action = edit.get('action')
    if action in ('exclude', 'include'):
        isn = edit.get('order')
        isn = isn.upper() if isinstance(isn, str) else None
        if isn in backlog_ids:
            (draft.added.discard if action == 'exclude' else draft.added.add)(isn)
            return draft
        if isn not in order_ids:
            raise DispatchError('Заказ не найден среди заказов дня — обновите страницу')
        (draft.excluded.add if action == 'exclude' else draft.excluded.discard)(isn)
        return draft
    if action in ('pin', 'unpin'):
        trip = _trip(draft, edit.get('trip'))
        if action == 'unpin':
            trip.pinned = False
            return draft
        truck = edit.get('truck')
        if truck not in ctx.trucks or truck not in draft.trucks:
            raise DispatchError('Эта машина сегодня не работает — отметьте её в шаге 1')
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
    draft.trips = [t for t in draft.trips if t.stops]
    shares = _shares(draft.trips)
    for t in (src, dst):
        if t is not None and t.stops:
            t.stops, *_ = _route(ctx, t.stops, routable, shares, reorder=True)
    return draft


# --- Вид страницы ---

def _r(x: float, nd: int = 1) -> float:
    return round(x, nd)


def plan_view(ctx: DayContext, stops: Sequence[Stop], draft: Draft,
              info: Callable[[Stop], dict[str, Any]]) -> dict[str, Any]:
    """Рейсы черновика с цифрами по текущим заказам: машины → рейсы по порядку (выезд, возвращение,
    км, литры, загрузка), точки по порядку; «ещё не в рейсах»; «не помещается»."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    _clean(draft, routable)
    shares = _shares(draft.trips)
    window = ctx.tn.work_minutes
    per_truck: dict[str, dict[str, Any]] = {}
    trips_json = []
    for t in draft.trips:
        cids, km, minutes, kg = _route(ctx, t.stops, routable, shares, reorder=False)
        truck = ctx.trucks.get(t.truck)
        cap = truck.capacity_kg if truck else None
        l100 = truck.l100 if truck else None
        slot = per_truck.setdefault(t.truck, {'used': 0.0, 'trips': []})
        depart = slot['used']
        slot['used'] += minutes
        tj = {
            'id': t.id, 'truck': t.truck, 'pinned': t.pinned,
            'km': _r(km), 'minutes': round(minutes), 'kg': round(kg),
            'revenue': round(math.fsum(routable[c].revenue / shares[c] for c in cids)),
            'liters': _r(km * l100 / 100.0) if l100 is not None else None,
            'load_pct': round(kg / cap * 100.0) if cap else None,
            'depart': _hhmm(ctx.work_start_min + depart), 'return': _hhmm(ctx.work_start_min + slot['used']),
            'over_time': slot['used'] > window + _EPS,
            'over_capacity': cap is not None and kg > cap + 0.5,
            'no_truck': truck is None,
            'stops': [{**info(routable[c]), 'kg': round(routable[c].kg / shares[c]),
                       'share': shares[c]} for c in cids],
        }
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
            'trips': ts, 'km': _r(math.fsum(t['km'] for t in ts)),
            'liters': _r(math.fsum(t['liters'] or 0.0 for t in ts)), 'kg': sum(t['kg'] for t in ts),
            'stops': sum(len(t['stops']) for t in ts), 'minutes': round(slot['used']),
            'return': ts[-1]['return'], 'over_time': slot['used'] > window + _EPS,
        })
    in_trips = set(shares)
    unassigned = [info(s) | {'kg': round(s.kg)} for s in stops if s.point is not None and s.customer_id not in in_trips]
    over = [t for t in trips_json if t['over_time'] or t['over_capacity'] or t['no_truck']]
    km_total = math.fsum(t['km'] for t in trips_json)
    return {
        'trucks': trucks_json,
        'unassigned': unassigned,
        'overflow': {'trips': len(over), 'kg': sum(t['kg'] for t in over),
                     'unassigned_kg': sum(u['kg'] for u in unassigned)},
        'summary': {'trips': len(trips_json), 'trucks': len(trucks_json), 'km': _r(km_total),
                    'liters': _r(math.fsum(t['liters'] or 0.0 for t in trips_json)),
                    'kg': sum(t['kg'] for t in trips_json), 'stops': len(in_trips)},
    }


# --- Сравнение «по менеджерам» ---

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
    km = liters = 0.0
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
        trips += len(ts)
        extra += sum(1 for t in ts if t.extra)
        per.append({'car_code': code, 'stops': len(ss), 'kg': round(math.fsum(s.kg for s in ss)),
                    'trips': len(ts), 'km': _r(k), 'over_time': any(t.extra for t in ts)})
    return {'km': _r(km), 'liters': _r(liters), 'trips': trips, 'trips_over_time': extra, 'trucks': per}


def baseline(ctx: DayContext, stops: Sequence[Stop], draft: Draft,
             agent_cars: Mapping[int, Sequence[str]]) -> dict[str, Any] | None:
    """«Как обычно»: те же заказы (без исключённых), разложенные по машинам менеджеров."""
    routable = [s for s in stops if s.point is not None]
    sel = _selected(ctx, draft.trucks)
    if not routable or not sel:
        return None
    return _group_km(ctx, manager_trucks(routable, agent_cars, sel))


# --- План и факт (прошедшая дата) ---

def plan_vs_fact(ctx: DayContext, docs: Sequence[ShippedDoc], coord: Callable[[int], Coord]) -> dict[str, Any]:
    """Те же доставки дня: как их фактически развезли машины ERP (каждая — лучшим для неё маршрутом)
    против рейсов программы на тех же машинах. Машины без тоннажа и расхода в настройках и клиенты
    без координат в сравнение не входят (их число — в skipped)."""
    groups: dict[str, dict[int, list[ShippedDoc]]] = {}
    skipped_docs = skipped_kg = 0.0
    no_car: set[str] = set()
    for d in docs:
        if d.car_code not in ctx.trucks or coord(d.customer_id).point is None:
            skipped_docs += 1
            skipped_kg += d.kg
            if d.car_code and d.car_code not in ctx.trucks:
                no_car.add(d.car_code)
            continue
        groups.setdefault(d.car_code, {}).setdefault(d.customer_id, []).append(d)

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

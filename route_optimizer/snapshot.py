# -*- coding: utf-8 -*-
"""Снимок данных ERP для раздела «Маршруты»: erp → geo → demand; кэш в памяти (TTL 10 мин, lock).

Снимок НЕ зависит от настроек: всё, что от них зависит (сезоны, классы размера, оценка плана),
считает evaluate.build_overview. Кэш результатов — ключ (id снимка, отпечаток настроек, …).
"""
from __future__ import annotations

import itertools
import logging
import threading
import time as _time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Hashable

from . import erp
from .demand import Order, group_orders, month_add, seasonal_index
from .evaluate import (ACTIVE_WINDOW_DAYS, CALIB_WINDOW_DAYS, FACT_WINDOW_DAYS, ActualVisit, Fact,
                       active_agents, fixes_by_day, manager_facts, visit_point_ok)
from .geo import (GPS_MIN_VISITS, Fix, HomeGuess, Point, default_point_keys, guess_home,
                  is_valid_point, median_point, point_key)
from .plan import CurrentPlan, build_plan
from .store import TRUCK_CAPACITY_KG, RefData

logger = logging.getLogger(__name__)

DEMAND_WINDOW_DAYS = 365   # окно спроса [today − 365, today)
DOCS_LOOKBACK_DAYS = 14    # документы берём с запасом: заказ у границы окна мог отгружаться позже
VALIDATION_LOOKBACK_DAYS = 126   # независимая проверка: 12 нед. обучения + 4 теста и прошлый год
TRACK_WINDOW_DAYS = 45     # трек — дом менеджера и калибровка
CAR_USAGE_DAYS = 90        # подсказка «сколько машина возит в день»; экспедиторы без машины — за тот же срок
CAR_IDLE_DAYS = 60         # машина ERP без накладных дольше — «активна» по умолчанию выключена
EXPEDITOR_MIN_DOCS = 5     # меньше накладных без машины за 90 дней — случайность, не экспедитор
SNAPSHOT_TTL_SECONDS = 600
MIN_REFRESH_SECONDS = 60   # ERP боевая: снимок не пересобирается чаще раза в минуту


@dataclass(frozen=True)
class Snapshot:
    id: str
    data_as_of: datetime
    today: date
    window_start: date
    agents: dict[int, erp.Agent]
    plan: CurrentPlan
    customers: dict[int, erp.Customer]              # клиенты плана
    customer_groups: dict[str, str]                 # код → название (CustGrp)
    erp_points: dict[int, tuple[int, Point]]        # адрес → (клиент, валидная не «дефолтная» точка)
    default_address: dict[int, int]                 # клиент → id адреса по умолчанию
    gps_points: dict[int, Point]                    # клиент → медиана GPS визитов (≥ 3)
    orders_by_customer: dict[int, tuple[Order, ...]]  # клиенты плана: заказы в окне спроса
    company_orders_by_day: dict[date, int]          # все заказы компании в окне спроса
    first_order: dict[int, date]                    # клиенты плана: первая покупка
    season_index: dict[int, float] | None
    auto_homes: dict[int, HomeGuess]
    facts: dict[int, Fact]
    active_agents: frozenset[int]                   # заказы или визиты за 8 недель — в расчёте по умолчанию
    recent_visits: tuple[ActualVisit, ...]          # последние 6 недель — калибровка
    fixes_by_agent: dict[int, tuple[Fix, ...]]      # последние 45 дней
    cars: dict[str, erp.Car]
    car_days: dict[str, tuple[int, float, float]]   # машина → (дней, кг в день в среднем, максимум) за 90 дней
    # клиенты плана: долг на сегодня по формуле дашборда (этап 4, режим Б); нет записи — долга нет
    debts: dict[int, float] = field(default_factory=dict)
    car_last_used: dict[str, date] = field(default_factory=dict)       # машина → последняя накладная (всё время)
    # экспедитор → (накладных без машины, последний день) за 90 дней; от EXPEDITOR_MIN_DOCS накладных
    expeditors: dict[int, tuple[int, date]] = field(default_factory=dict)
    validation_orders: tuple[Order, ...] = ()    # история для проверки, обучение и тест не смешиваются

    def ref_data(self) -> RefData:
        return RefData(car_codes=frozenset(self.cars), agent_ids=frozenset(self.plan.agent_ids),
                       group_codes=frozenset(self.customer_groups), van_agent_ids=frozenset(self.expeditors))

    @property
    def car_capacity(self) -> dict[str, float]:
        """Машина → грузоподъёмность из карточки ERP, кг: тоннаж машины, у которой в настройках пусто. Только заданные и
        в пределах поля настроек (TRUCK_CAPACITY_KG): кг вместо тонн в карточке не превратят машину в 3500 т."""
        lo, hi = TRUCK_CAPACITY_KG
        return {code: car.capacity_kg for code, car in self.cars.items()
                if car.capacity_kg is not None and lo <= car.capacity_kg <= hi}

    @property
    def active_cars(self) -> frozenset[str]:
        """Машины ERP, которые «авто» работают: не закрыты и возили накладные за последние CAR_IDLE_DAYS дней."""
        return frozenset(code for code, car in self.cars.items()
                         if not car.closed and (last := self.car_last_used.get(code)) is not None
                         and (self.today - last).days <= CAR_IDLE_DAYS)


def build_snapshot(conn: Any, today: date, snapshot_id: str, as_of: datetime) -> Snapshot:
    """Все выборки одним соединением + вычисления, не зависящие от настроек."""
    window_start = today - timedelta(days=DEMAND_WINDOW_DAYS)
    midnight = datetime.combine(today, time.min)

    agents = erp.agents(conn)
    plan = build_plan(erp.route_templates(conn))
    plan_customers = plan.customer_ids
    plan_set = set(plan_customers)
    customers = erp.customers(conn, plan_customers)
    groups = erp.customer_groups(conn)
    addresses = erp.addresses(conn)
    docs = erp.sales_docs(conn, window_start - timedelta(days=VALIDATION_LOOKBACK_DAYS+DOCS_LOOKBACK_DAYS),
                          today + timedelta(days=1))
    first_sale = erp.first_sale_dates(conn, plan_customers)
    cur_month = date(today.year, today.month, 1)
    monthly = erp.monthly_revenue(conn, month_add(cur_month, -36), cur_month)
    visits = erp.visits(conn, window_start, today)
    fixes = erp.tracks(conn, midnight - timedelta(days=TRACK_WINDOW_DAYS), midnight)
    cars = erp.cars(conn)
    usage = car_load_stats(erp.car_days(conn, today - timedelta(days=CAR_USAGE_DAYS), today))
    last_used = erp.car_last_used(conn)
    vans = {a: v for a, v in erp.expeditors(conn, today - timedelta(days=CAR_USAGE_DAYS),
                                            today + timedelta(days=1)).items() if v[0] >= EXPEDITOR_MIN_DOCS}
    debts = erp.customer_debts(conn, plan_customers)

    # Координаты ERP: валидные и не «дефолтные» (одна точка у ≥ 3 клиентов во всём справочнике).
    valid = [a for a in addresses if is_valid_point(a.lat, a.lon)]
    defaults = default_point_keys((a.customer_id, (a.lat, a.lon)) for a in valid)
    erp_points = {a.id: (a.customer_id, (a.lat, a.lon)) for a in valid
                  if not a.closed and point_key((a.lat, a.lon)) not in defaults}
    default_address: dict[int, int] = {}
    for a in sorted(addresses, key=lambda x: x.id):
        if a.is_default and not a.closed:
            default_address.setdefault(a.customer_id, a.id)

    # GPS клиента: медиана по визитам с точной точкой, если их ≥ 3.
    gps_raw: dict[int, list[Point]] = {}
    for v in visits:
        # Развоз включает клиентов вне шаблонов менеджеров. Их подтверждённые GPS-визиты
        # нужны так же, как точки клиентов плана; порог качества и числа визитов не меняется.
        if visit_point_ok(v):
            gps_raw.setdefault(v.customer_id, []).append((v.lat, v.lon))
    gps_points = {c: median_point(ps) for c, ps in gps_raw.items() if len(ps) >= GPS_MIN_VISITS}

    # Заказы в окне спроса [window_start, today).
    all_orders = group_orders(docs)
    orders = [o for o in all_orders if window_start <= o.date < today]
    company = Counter(o.date for o in orders)
    by_customer: dict[int, list[Order]] = {}
    for o in orders:
        if o.customer_id in plan_set:
            by_customer.setdefault(o.customer_id, []).append(o)
    first_order: dict[int, date] = {}
    for cid in plan_customers:
        candidates = [d for d in (first_sale.get(cid),
                                  by_customer[cid][0].date if cid in by_customer else None) if d]
        if candidates:
            first_order[cid] = min(candidates)

    snap = Snapshot(
        id=snapshot_id, data_as_of=as_of, today=today, window_start=window_start,
        agents=agents, plan=plan, customers=customers, customer_groups=groups,
        erp_points=erp_points, default_address=default_address, gps_points=gps_points,
        orders_by_customer={c: tuple(os) for c, os in by_customer.items()},
        company_orders_by_day=dict(company), first_order=first_order,
        season_index=seasonal_index(monthly, cur_month),
        auto_homes={a: h for a in plan.agent_ids if (h := guess_home(fixes.get(a, ()))) is not None},
        # работа и паузы дня — по треку снимка (45 дней), окно и визиты — по 8 неделям
        facts=manager_facts(visits, today - timedelta(days=FACT_WINDOW_DAYS), today,
                            day_fixes=fixes_by_day(fixes)),
        active_agents=active_agents(orders, visits, today - timedelta(days=ACTIVE_WINDOW_DAYS), today),
        recent_visits=tuple(v for v in visits if v.day >= today - timedelta(days=CALIB_WINDOW_DAYS)),
        fixes_by_agent={a: tuple(fs) for a, fs in fixes.items()},
        cars=cars, car_days=usage, debts=debts, car_last_used=last_used, expeditors=vans,
        validation_orders=tuple(o for o in all_orders if o.date < today),
    )
    logger.info('[Routes] Снимок %s: агентов с планом %d (с работой за 8 нед. %d), клиентов плана %d, '
                'заказов в окне %d, визитов %d, точек трека %d', snapshot_id, len(plan.agent_ids),
                len(snap.active_agents & set(plan.agent_ids)), len(plan_customers),
                len(orders), len(visits), sum(len(f) for f in fixes.values()))
    return snap


def car_load_stats(rows: list[erp.CarDay]) -> dict[str, tuple[int, float, float]]:
    """Машина → (дней с развозом, кг в день в среднем, кг в самый тяжёлый день) — подсказка к тоннажу
    в настройках: машина закреплена за водителем и за день возит заказы нескольких менеджеров."""
    by_car: dict[str, list[float]] = {}
    for r in rows:
        if r.kg > 0:
            by_car.setdefault(r.car_code, []).append(r.kg)
    return {car: (len(kgs), sum(kgs) / len(kgs), max(kgs)) for car, kgs in sorted(by_car.items())}


_snapshot_ids = itertools.count(1)


def load_snapshot(connection_string: str) -> Snapshot:
    """Открыть read-only соединение с ERP, собрать снимок, закрыть соединение."""
    started = _time.perf_counter()
    as_of = datetime.now().replace(microsecond=0)
    conn = erp.connect(connection_string)
    try:
        snap = build_snapshot(conn, as_of.date(), f'{as_of:%Y%m%d%H%M%S}-{next(_snapshot_ids)}', as_of)
    finally:
        erp.close_quietly(conn)
    logger.info('[Routes] Снимок ERP собран за %.1f с', _time.perf_counter() - started)
    return snap


class SnapshotCache:
    """Снимок в памяти процесса: TTL 10 минут; сборка под lock — параллельные запросы ждут одну.

    - refresh («Обновить») не пересобирает снимок моложе MIN_REFRESH_SECONDS;
    - пересборка упала с ErpError, а прежний снимок есть — вызывающий с allow_stale получает
      прежний снимок с пометкой «устарел»; следующая попытка — не раньше чем через
      MIN_REFRESH_SECONDS (иначе каждый запрос ждал бы таймаута подключения к ERP);
    - прежнего снимка нет — в эту минуту после сбоя ErpError сразу, без нового подключения.
    """

    def __init__(self, loader: Callable[[], Snapshot], ttl_seconds: float = SNAPSHOT_TTL_SECONDS,
                 clock: Callable[[], float] = _time.monotonic):
        self._loader = loader
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._snap: Snapshot | None = None
        self._built_at = 0.0
        self._failed_at: float | None = None

    def get(self, refresh: bool = False, allow_stale: bool = False) -> tuple[Snapshot, bool]:
        """(снимок, устарел ли). Без прежнего снимка (или без allow_stale) ErpError пробрасывается."""
        with self._lock:
            prev = self._snap
            recently_failed = (self._failed_at is not None
                               and self._clock() - self._failed_at < MIN_REFRESH_SECONDS)
            if prev is not None:
                age = self._clock() - self._built_at
                if age < MIN_REFRESH_SECONDS or (age < self._ttl and not refresh):
                    if refresh:
                        logger.info('[Routes] Снимок %s моложе %d с — «Обновить» не читает ERP заново',
                                    prev.id, MIN_REFRESH_SECONDS)
                    return prev, False
                if allow_stale and recently_failed:
                    return prev, True
            elif recently_failed:
                raise erp.ErpError(f'ERP недоступна — повтор подключения не раньше чем через '
                                   f'{MIN_REFRESH_SECONDS} с после сбоя')
            try:
                snap = self._loader()
            except erp.ErpError:
                self._failed_at = self._clock()
                if prev is None or not allow_stale:
                    raise
                logger.warning('[Routes] ERP недоступна — отдаём прежний снимок %s (данные на %s)',
                               prev.id, prev.data_as_of, exc_info=True)
                return prev, True
            self._snap, self._built_at, self._failed_at = snap, self._clock(), None
            return snap, False

    def cached(self) -> tuple[Snapshot, bool]:
        """Снимок из кэша любого возраста, без пересборки и без чтения ERP: решения и выгрузка
        плана работают по тем же данным, что и страница с последним расчётом. Кэш пуст (сервер
        перезапускался) — как get(allow_stale=True)."""
        with self._lock:
            snap = self._snap
        if snap is not None:
            return snap, False
        return self.get(allow_stale=True)

    def peek(self) -> Snapshot | None:
        """Снимок из кэша, если он есть; никогда не читает ERP (кэш пуст — None)."""
        with self._lock:
            return self._snap


class ResultCache:
    """Кэш результатов расчёта по ключу (id снимка, отпечаток настроек, …); вытесняется старейший."""

    def __init__(self, max_entries: int = 16):
        self._max = max_entries
        self._lock = threading.Lock()
        self._data: dict[Hashable, Any] = {}

    def get_or_compute(self, key: Hashable, compute: Callable[[], Any]) -> tuple[Any, bool]:
        """(значение, взято ли из кэша). Расчёт под lock: одинаковые запросы не считаются дважды."""
        with self._lock:
            if key in self._data:
                return self._data[key], True
            value = compute()
            while len(self._data) >= self._max:
                self._data.pop(next(iter(self._data)))
            self._data[key] = value
            return value, False

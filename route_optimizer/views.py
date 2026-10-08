# -*- coding: utf-8 -*-
"""Страницы и API раздела «Маршруты» (§10).

Доступ обеспечивает глобальный before_request дашборда: аноним — 401/редирект на вход,
роль user — 403 (раздела нет в allowlist), garage — журнал гаража (/routes/garage и его API), карта машин и «Վարորդներ»
(без денег), admin — полный доступ.
Клиенту не отдаём текст исключений: ERP → 503, прочее → 500, подробности — в лог с [Routes].
"""
from __future__ import annotations

import copy
import hashlib
import logging
import math
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any, Callable, Collection, Mapping, Protocol, Sequence

from flask import (Blueprint, Response, current_app, g, has_app_context, has_request_context, jsonify,
                   render_template, request, session)

from . import actuals as ac
from . import ai_chat
from . import cost_to_serve as cts
from . import crew_kpi as ck
from . import crew_pay as cp
from . import dispatch as dp
from . import evaluate, garage, learning, live, optimize
from . import scorecard as sc
from . import track_line as tl
from . import fleet as fl
from . import waybill as wb
from . import terrain as dem
from .running_costs import TERRAIN_U_BAR_TRACK, curb_tonnes, profile_fields, route_cost, terrain_liters
from .erp import CUSTOMER_FIND_MAX_LEN, CustomerHint, CustomerRef, ErpError
from .geo import Point, haversine_km, in_city, in_polygon, is_valid_point
from .roads import SNAP_MAX_KM, CenterBypassRoads, RoadDistances, RoadProvider, roads_version
from .snapshot import CAR_IDLE_DAYS, MIN_REFRESH_SECONDS, ResultCache, Snapshot, SnapshotCache
from .store import (CREW_TABLES, DEFAULT_MANAGER_FUEL, DEFAULT_SETTINGS, KEEP, Bundle, Decision, GarageError, Store, StoreError,
                    big_auto, center_auto, check_driver_name, check_garage_entry, check_live_explanation,
                    check_unload_min, check_window, until_floor, validate_payload)
from .valhalla_engine import (CAR_COSTING, PROFILE_CAR, PROFILE_TRUCK, TRUCK_TIME_MODEL, TRUCK_TIME_VALHALLA, ValhallaProvider,
                              ValhallaRoads, truck_costing, truck_leg_minutes, truck_time_source, valhalla_error)
from .vehicle_access import check_access

logger = logging.getLogger(__name__)

EXTENSION_KEY = 'route_optimizer'
NO_CACHE_HEADERS = {
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
}

bp = Blueprint('route_optimizer', __name__)

JOBS_KEPT = 10                            # задачи оптимизации в памяти процесса
_JOB_ID_RE = re.compile(r'^[0-9a-f]{32}$')


@dataclass
class OptimizeJob:
    """Фоновый расчёт оптимизации (этап 3). Поля меняются под OptimizeJobs.lock."""
    id: str
    params: dict[str, Any]
    created_by: str | None
    started_at: str
    status: str = 'running'               # running | done | error
    error: str | None = None              # по-русски, без деталей исключения
    finished_at: str | None = None
    done: int = 0
    total: int = 0
    agent_code: str | None = None
    result: dict[str, Any] | None = None

    def job_json(self) -> dict[str, Any]:
        return {'id': self.id, 'status': self.status, 'error': self.error, 'params': self.params,
                'created_by': self.created_by, 'started_at': self.started_at,
                'finished_at': self.finished_at}

    def progress_json(self) -> dict[str, Any]:
        return {'done': self.done, 'total': self.total, 'agent_code': self.agent_code}


class OptimizeJobs:
    """Расчёты оптимизации: одновременно — один. В памяти — последние задачи (статус и прогресс) и
    результат только последнего успешного расчёта: результат занимает десятки мегабайт, прежние
    хранятся в таблице scenario (и переживают перезапуск)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running: OptimizeJob | None = None
        self.jobs: dict[str, OptimizeJob] = {}
        self.last: OptimizeJob | None = None   # последняя успешно завершённая

    def start(self, params: dict[str, Any], user: str | None) -> tuple[OptimizeJob | None, str | None]:
        """(новая задача, None) или (None, id идущей задачи), если расчёт уже идёт."""
        with self.lock:
            if self.running is not None:
                return None, self.running.id
            job = OptimizeJob(uuid.uuid4().hex, params, user, _now())
            self.running = job
            self.jobs[job.id] = job
            while len(self.jobs) > JOBS_KEPT:
                self.jobs.pop(next(iter(self.jobs)))
            return job, None

    def running_id(self) -> str | None:
        with self.lock:
            return self.running.id if self.running is not None else None


class DriverGeo(Protocol):
    """Точки и предложения водителей (courier.db, driver-geo-plan.md §4). Подключает app_v2
    (route_optimizer.attach_driver_geo): пакет route_optimizer не импортирует courier."""

    def points(self) -> Mapping[int, tuple[float, float, int]]:
        """Клиент → (широта, долгота, дней с отметками) по правилу §2."""
        ...

    def suggestions(self) -> list[dict[str, Any]]:
        """Предложения водителей без решения, новые первыми: event_id, customer_id, code, name, lat, lon,
        accuracy, note, driver_name, date, at."""
        ...

    def decide(self, event_id: str, decision: str, user: str | None,
               apply: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
        """Решение по открытому предложению; apply(предложение) вызывается до фиксации решения: исключение в apply —
        решения нет. apply пишет в другую базу и фиксирует её сам — это не одна атомарная транзакция (см.
        courier.store.Store.decide_suggestion). accepted закрывает остальные предложения того же клиента (superseded).
        None — предложения нет или оно уже решено."""
        ...


@dataclass
class RoutesState:
    store: Store
    snapshots: SnapshotCache
    results: ResultCache
    jobs: OptimizeJobs = field(default_factory=OptimizeJobs)
    roads: RoadProvider | None = None     # None — карты дорог нет, км по прямой
    valhalla: ValhallaProvider | None = None   # Valhalla (ROUTES_ROAD_ENGINE); нет или выключен — граф OSM
    # план развоза: заказы ERP на дату (since, until, day) → DispatchData; факт развоза за дату → FactData
    dispatch_loader: Callable[[date, date, date], dp.DispatchData] | None = None
    fact_loader: Callable[[date], dp.FactData] | None = None
    # новые заказы дня (№72): заказы ERP с датой дня и время их ввода → SameDayData; None — без них, всё как раньше
    same_day_loader: Callable[[date], dp.SameDayData] | None = None
    # Բեռնագիր (№57): fISN заказов точек машины → их строки (из проведённой накладной заказа, если она есть) и товары
    waybill_loader: Callable[[Sequence[str]], wb.Lines] | None = None
    # водители-экспедиторы ERP за [since, until) для выбора в «Վարորդ» (№62); кэш — (time.monotonic() до, имена)
    driver_list_loader: Callable[[date, date], list[str]] | None = None
    # клиенты → код группы (CustGrp) из ERP — сети для сглаживания времени магазина в обучении (№66); None — только снимок
    group_loader: Callable[[Sequence[int]], dict[int, str]] | None = None
    # клиенты для списка «машины не везут» (№74): (поиск, id) → клиенты ERP; (с, по) → подсказка «без адреса / вне
    # Армении»; None — без ERP (поиск пуст)
    customer_ref_loader: Callable[[str, Sequence[int]], list[CustomerRef]] | None = None
    customer_hint_loader: Callable[[date, date], list[CustomerHint]] | None = None
    # «Աշխատավարձ»: накладные экспедиторов ERP за [с, по) и справочник менеджеров; кэш — (с, по) → (time.monotonic(),
    # данные, посчитанные туры плановых км: (склад, версия дорог) → memo cp.plan_tours) — туры живут столько же, сколько данные
    crew_pay_loader: Callable[[date, date], cp.CrewData] | None = None
    crew_pay_cache: dict[tuple[date, date], tuple[float, cp.CrewData, dict[Any, dict[Any, Any]]]] = \
        field(default_factory=dict)
    crew_pay_lock: threading.Lock = field(default_factory=threading.Lock)
    # «Առաքման արժեք» (№87, п. 6): накладные ERP за [с, по) по дню и клиенту; кэш — (с, по) → (time.monotonic(), данные);
    # расчёт дня — день → (ключ входа, рейсы с долями магазинов, time.monotonic() расчёта, посчитан без рельефа): прошлый
    # день не пересчитывается, пока не изменились план (номер правки), его накладные, точки, машины, цена дизеля, ставки и
    # дороги (_cost_compute) — но не дольше COST_DAY_TTL_SECONDS, без подъёмов точек — COST_RETRY_SECONDS
    cost_sales_loader: Callable[[date, date], cts.SalesData] | None = None
    cost_sales_cache: dict[tuple[date, date], tuple[float, cts.SalesData]] = field(default_factory=dict)
    cost_days: dict[date, tuple[Any, list[cts.TripCost], float, bool]] = field(default_factory=dict)
    cost_lock: threading.Lock = field(default_factory=threading.Lock)   # только словари кэша: расчёт не под ним
    # туры экипажа для платы за км (cts.crew_km): (склад, версия дорог) → точки тура → (км, порядок); хранится один ключ
    cost_tours: dict[Any, cts.TourMemo] = field(default_factory=dict)
    # расчёт периода — в фоне (_cost_result): (с, по) → поток (не больше одного на сервер) и последний итог потока —
    # (time.monotonic() начала и конца расчёта, _CostResult или исключение); холодный первый расчёт (дороги без кэша) —
    # минуты. cost_changed — time.monotonic() последнего сохранения настроек, наценки или ставок (_cost_changed): итог,
    # начатый раньше, не отдаётся
    cost_warm: dict[tuple[date, date], threading.Thread] = field(default_factory=dict)
    cost_results: dict[tuple[date, date], tuple[float, float, Any]] = field(default_factory=dict)
    cost_changed: float = 0.0
    driver_list_cache: tuple[float, list[str]] | None = None
    driver_list_lock: threading.Lock = field(default_factory=threading.Lock)   # перечитывает один запрос
    # (машина ERP, имя, день) — кто возил машины за [since, until) (№84, waybill.load_car_crew_days): экипаж машин без
    # записей; None — без подбора по ERP, всё как раньше. Подбор — не чаще ERP_CREW_TTL_S (time.monotonic() следующего)
    crew_days_loader: Callable[[date, date], list[tuple[str, str, date]]] | None = None
    erp_crew_next: float = 0.0
    erp_crew_lock: threading.Lock = field(default_factory=threading.Lock)
    dispatch_cache: dict[tuple[date, date, date], tuple[float, dp.DispatchData]] = field(default_factory=dict)
    dispatch_lock: threading.Lock = field(default_factory=threading.Lock)
    same_day_cache: dict[date, tuple[float, dp.SameDayData]] = field(default_factory=dict)   # под dispatch_lock
    driver_geo: DriverGeo | None = None   # None — раздела «Առաքիչ» нет: точек водителей нет, всё как раньше
    driver_cache: tuple[float, dict[int, Point]] | None = None   # (time.monotonic(), точки) — DRIVER_TTL_SECONDS
    # факт машин (трек, точки дня, заправки) из «Առաքիչ» — обучение «Развоза»; None — без обучения, всё как раньше
    fleet_facts: learning.FleetFacts | None = None
    # факт терминалов за день для «Մեքենաները առցանց» (№76, courier.live); None — карта пуста, всё как раньше
    live_facts: live.LiveFacts | None = None
    # «Մեքենաները առցանց»: дорожная модель ETA (views._LiveRoads) и карточки флота на 10 с (views._live_cards)
    live_roads: _LiveRoads = field(default_factory=lambda: _LiveRoads())
    live_lines: _LivePlanLines = field(default_factory=lambda: _LivePlanLines())   # плановые линии по дорогам (08.10)
    live_tracks: _LiveTracks = field(default_factory=lambda: _LiveTracks())   # линии треков, привязанные к дорогам (08.10)
    live_cards: dict[date, tuple[Any, float, Any, datetime, dict[str, dict[str, Any]]]] = field(default_factory=dict)
    live_lock: threading.Lock = field(default_factory=threading.Lock)   # только словари кэша: расчёт под ним не идёт
    live_flight: dict[date, threading.Lock] = field(default_factory=dict)   # пересчёт флота — один на день
    # следование плановой линии машино-дней для «Վարորդներ» (scorecard «Երթուղի», 08.10) — считает фон (_ScoreRoutes)
    score_routes: _ScoreRoutes = field(default_factory=lambda: _ScoreRoutes())
    learning_lock: threading.Lock = field(default_factory=threading.Lock)   # прогон обучения — один за раз
    learning_job: dict[str, Any] = field(default_factory=dict)              # последний прогон: статус, время, ошибка
    learning_warning: dict[str, str] | None = None   # выученные нормы не применились (битый журнал) — для страницы
    # факт машино-дня: (машина, день) → (отпечаток данных, точки плана, факт) — views._learning_days
    actuals_cache: dict[tuple[str, str], tuple[Any, Any, Any]] = field(default_factory=dict)
    actuals_lock: threading.Lock = field(default_factory=threading.Lock)
    # «Նորմ և փաստ»: факт месяца, который считается в фоне, (с, по) → поток (не больше одного) и периоды, чей расчёт
    # упал (views._month_ready; под actuals_lock)
    garage_warm: dict[tuple[date, date], threading.Thread] = field(default_factory=dict)
    garage_warm_failed: set[tuple[date, date]] = field(default_factory=set)
    # «Վարորդներ»: кто закрыл точки, деньги и тара по людям за день (courier.scorecard); None — страница пуста
    crew_facts: sc.CrewFacts | None = None
    # сводка дня «Վարորդներ»: день → (отпечаток данных дня, сводка) — views._scorecard_days
    scorecard_cache: dict[str, tuple[Any, dict[str, Any]]] = field(default_factory=dict)
    scorecard_lock: threading.Lock = field(default_factory=threading.Lock)   # только словарь кэша: расчёт не под ним
    # «Վարորդներ» по машино-дням (под scorecard_lock): (машина, день) → (отпечаток, (превышений, минут стоянок вне
    # магазинов)) — _scorecard_cars; интервал заправок → (отпечаток, день → км трека машины) и (машина, день) →
    # (отпечаток, (вид, (литры, норма) | None)) — _scorecard_fuel
    scorecard_track: dict[tuple[str, str], tuple[Any, Any]] = field(default_factory=dict)
    fuel_span_cache: dict[tuple[Any, ...], tuple[Any, dict[date, float]]] = field(default_factory=dict)
    fuel_day_cache: dict[tuple[str, str], tuple[Any, Any]] = field(default_factory=dict)
    # неделя для APK (week_score): понедельник → (момент расчёта, scorecard.period недели); идущий расчёт — понедельник
    # → фоновый поток (не больше одного); замок — только словари, расчёт не под ним
    score_week_cache: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)
    score_week_running: dict[str, threading.Thread] = field(default_factory=dict)
    score_week_failed: dict[str, float] = field(default_factory=dict)   # неделя → момент сбоя расчёта (monotonic)
    score_week_lock: threading.Lock = field(default_factory=threading.Lock)
    # рельеф трека машино-дня (№85): (машина, день) → (отпечаток трека и высот, (подъём м, км) или None) — _track_climbs
    track_climbs: dict[tuple[str, str], tuple[Any, Any]] = field(default_factory=dict)


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _clock() -> datetime:
    """Местное время сервера для «заказы ещё поступают» (тесты подменяют)."""
    return datetime.now()


def _state() -> RoutesState:
    return current_app.extensions[EXTENSION_KEY]


DRIVER_TTL_SECONDS = 300   # точки водителей: courier.db перечитывается не чаще раза в 5 минут
DRIVER_DECIMALS = 5        # точка водителей — до ~1 м: стабильный отпечаток настроек и ключ точки в кэше дорог
DRIVER_KEEP_M = 15.0       # медиана сдвинулась не больше — остаётся прежняя точка (кэш оценки и дорог не сбрасывается)


def _stable_points(fresh: Mapping[int, Point], prev: Mapping[int, Point]) -> dict[int, Point]:
    """Гистерезис: у клиента остаётся прежняя опубликованная точка, пока новая медиана не дальше DRIVER_KEEP_M —
    иначе каждая новая отметка доставки сбрасывала бы кэш оценки и расширяла таблицу точек дорог."""
    return {cid: prev[cid] if cid in prev and haversine_km(prev[cid], p) * 1000 <= DRIVER_KEEP_M else p
            for cid, p in fresh.items()}


def _driver_points(state: RoutesState) -> dict[int, Point]:
    """Точки водителей из DriverGeo (кэш DRIVER_TTL_SECONDS, округление DRIVER_DECIMALS, гистерезис _stable_points).
    Источника нет или чтение не удалось — пусто: раздел считает как раньше (ERP и GPS менеджеров), сбой — в журнал."""
    if state.driver_geo is None:
        return {}
    hit = state.driver_cache
    if hit is not None and time.monotonic() - hit[0] < DRIVER_TTL_SECONDS:
        return hit[1]
    try:
        fresh = {int(cid): (round(float(p[0]), DRIVER_DECIMALS), round(float(p[1]), DRIVER_DECIMALS))
                 for cid, p in state.driver_geo.points().items() if is_valid_point(p[0], p[1])}
        points = _stable_points(fresh, hit[1] if hit is not None else {})
    except Exception:
        logger.warning('[Routes] Точки водителей не прочитаны — считаем без них', exc_info=True)
        points = {}
    state.driver_cache = (time.monotonic(), points)
    return points


def _bundle(state: RoutesState) -> Bundle:
    """Настройки раздела + точки водителей (Bundle.driver_points) + ремонт ֏/км журнала гаража на сегодня
    (Bundle.garage_wear; «Развоз» берёт его на свой день — _load_day): ими считают обзор, оптимизация и развоз."""
    bundle = state.store.load()
    points = _driver_points(state)
    bundle = replace(bundle, driver_points=points) if points else bundle
    return _with_garage(state, bundle, _clock().date())


GARAGE_APK_YEARS = 2   # одометр APK для журнала гаража — с 1 января позапрошлого года: окно 365 дней + опора до него


def _apk_odometers(state: RoutesState, since: date) -> dict[str, list[tuple[date, float]]]:
    """Одометр заправок из APK (контракт §7, courier.db) с дня since: машина → [(день по Еревану, км)] — только
    действующие заправки (исправленные — нет) с согласованным одометром (learning.odometer_plausible, как у расхода
    топлива). Источника нет или чтение не удалось — пусто: журнал гаража считается без них, сбой — в журнал."""
    if state.fleet_facts is None:
        return {}
    try:
        refuels = state.fleet_facts.refuels(since.isoformat())
    except Exception:
        logger.warning('[Routes] Заправки «Առաքիչ» не прочитаны — журнал гаража без одометра APK', exc_info=True)
        return {}
    out: dict[str, list[tuple[date, float]]] = {}
    for car, items in learning.effective_refuels(refuels).items():
        plausible = learning.odometer_plausible([(at, p.get('odometer_km')) for at, _, p in items])
        out[car] = [(at.astimezone(ac.YEREVAN).date(), float(p['odometer_km']))
                    for (at, _, p), good in zip(items, plausible) if good]
    return out


def _garage_by_car(entries: Sequence[Any]) -> dict[str, list[garage.Entry]]:
    """Живые записи журнала (store.GarageEntry) → машина → записи для расчёта (garage.Entry)."""
    by_car: dict[str, list[garage.Entry]] = {}
    for e in entries:
        by_car.setdefault(e.car_code, []).append(garage.Entry(e.day, e.kind, e.amount_amd, e.odometer_km,
                                                              e.spread_months))
    return by_car


def _garage_memo(key: Any, compute: Callable[[], Any]) -> Any:
    """Один раз на запрос (g): «Развоз» берёт цену журнала и на сегодня (_bundle), и на свой день (_load_day) — журнал и
    заправки читаются один раз. Вне контекста приложения — без запоминания."""
    if not has_app_context():
        return compute()
    memo = g.setdefault('_garage_memo', {})
    if key not in memo:
        memo[key] = compute()
    return memo[key]


def _garage_models(state: RoutesState, trucks: Mapping[str, Any]) -> dict[str, str | None]:
    """Модель машины для сглаживания цены журнала (garage.model_of): по имени, как его показывает раздел — ERP CARS из
    снимка в памяти (SnapshotCache.peek: ERP здесь не читается; расчёты «Развоза», обзора и оптимизации берут снимок до
    цены), у ручной машины — своё; без имени — тоннаж."""
    snap = state.snapshots.peek()

    def compute() -> dict[str, str | None]:
        out = {}
        for code, t in trucks.items():
            car = snap.cars.get(code) if snap is not None and not t.manual else None
            out[code] = garage.model_of(car.name if car is not None else t.name, t.capacity_kg)
        return out

    # запоминается по снимку и по машинам: модели зависят и от их названий, ручного признака и тоннажа
    names = tuple(sorted((code, t.manual, t.name, t.capacity_kg) for code, t in trucks.items()))
    return _garage_memo(('models', snap.id if snap is not None else None, names), compute)


def _garage_prices(state: RoutesState, as_of: date, trucks: Mapping[str, Any]) -> dict[str, garage.Price]:
    """Ремонт ֏/км машин журнала гаража на as_of (garage.prices: своя, сглаженная к средней модели; trucks — машины
    раздела, по ним модели). Журнал пуст — пусто, заправки не читаются; иначе — заправки с 1 января (as_of, но не позже
    сегодня) − GARAGE_APK_YEARS лет, а не все."""
    by_car = _garage_memo('entries', lambda: _garage_by_car(state.store.garage_entries()))
    if not by_car:
        return {}
    since = date(min(as_of, _clock().date()).year - GARAGE_APK_YEARS, 1, 1)
    return garage.prices(by_car, as_of, _garage_memo(('apk', since), lambda: _apk_odometers(state, since)),
                         _garage_models(state, trucks))


def _garage_priors(state: RoutesState, prices: Mapping[str, garage.Price],
                   trucks: Mapping[str, Any]) -> dict[str, garage.Prior]:
    """Средняя модели или парка машинам раздела (trucks) без своей готовой цены журнала (garage.priors); журнал пуст —
    пусто."""
    return garage.priors(prices, _garage_models(state, trucks)) if prices else {}


def _garage_bundle(bundle: Bundle, prices: Mapping[str, garage.Price],
                   priors: Mapping[str, garage.Prior]) -> Bundle:
    """Настройки с ценами журнала: готовые (garage_wear) перекрывают ручной износ, средние (garage_prior) — только
    пустой ручной (Bundle.resolved_trucks). Ничего не изменилось — те же настройки (расчёт и отпечаток — прежние)."""
    wear = garage.effective(prices)
    prior = {code: p.price for code, p in priors.items()}
    if wear == bundle.garage_wear and prior == bundle.garage_prior:
        return bundle
    return replace(bundle, garage_wear=wear, garage_prior=prior)


def _with_garage(state: RoutesState, bundle: Bundle, as_of: date) -> Bundle:
    """Настройки с ремонтом ֏/км журнала гаража на as_of (_garage_bundle). Готовых цен нет — настройки как есть."""
    prices = _garage_prices(state, as_of, bundle.trucks)
    return _garage_bundle(bundle, prices, _garage_priors(state, prices, bundle.trucks))


def _api(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Единая обработка ошибок API раздела."""
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ErpError:
            logger.exception('[Routes] ERP недоступна (%s)', request.path)
            return jsonify({'success': False, 'error': 'ERP տվյալների բազան հասանելի չէ'}), 503
        except StoreError as e:
            # Текст StoreError формируем сами — он предназначен пользователю (битая база настроек
            # должна быть видна в UI, а не молча заменяться дефолтами).
            logger.exception('[Routes] База маршрутов (%s)', request.path)
            return jsonify({'success': False, 'error': str(e), 'store_error': True}), 500
        except (optimize.OptimizeError, dp.DispatchError) as e:   # проверенная ошибка правил для пользователя
            logger.warning('[Routes] %s: %s', request.path, e)
            return jsonify({'success': False, 'error': str(e)}), 400
        except Exception:
            logger.exception('[Routes] Внутренняя ошибка (%s)', request.path)
            return jsonify({'success': False, 'error': 'Սերվերի ներքին սխալ'}), 500
    return wrapper


def _admin_only(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Зарплаты, «Առաքման արժեք» и объяснения тревог карты машин — только администратору. Гейт app_v2 и так пускает в
    «Маршруты» лишь admin (прочие роли — default-deny; «Гаражу» карта машин — только на чтение); эта проверка — вторая
    линия: роль, которой когда-нибудь откроют раздел, денег и записи всё равно не получит."""
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if g.get('user_role') != 'admin':
            logger.warning('[Routes] Только администратору: отказ роли %r (%s)', g.get('user_role'), request.path)
            return jsonify({'success': False, 'error': PAY_FORBIDDEN}), 403
        return fn(*args, **kwargs)
    return wrapper


@bp.after_request
def _no_store(resp: Response) -> Response:
    resp.headers.update(NO_CACHE_HEADERS)
    return resp


# --- Страницы ---

_YANDEX_KEY_RE = re.compile(r'[0-9A-Za-z-]{16,64}')
_yandex_key_warned: set[str] = set()


def _yandex_tiles_key(env: str = 'ROUTES_YANDEX_TILES_KEY') -> str:
    """Ключ Yandex Tiles API для подложки карт (№47) из переменной env или '' — тогда карты на OpenStreetMap."""
    import os
    key = os.environ.get(env, '').strip()
    if key and not _YANDEX_KEY_RE.fullmatch(key):
        if key not in _yandex_key_warned:       # один раз на значение, а не на каждый показ страницы
            _yandex_key_warned.add(key)
            logger.warning('%s не похож на ключ Яндекса — карты на OpenStreetMap', env)
        return ''
    return key


@bp.get('/routes')
def overview_page() -> str:
    return render_template('routes_overview.html', yandex_tiles_key=_yandex_tiles_key())


@bp.get('/routes/settings')
def settings_page() -> str:
    return render_template('routes_settings.html', yandex_tiles_key=_yandex_tiles_key())


@bp.get('/routes/optimize')
def optimize_page() -> str:
    return render_template('routes_optimize.html', yandex_tiles_key=_yandex_tiles_key())


@bp.get('/routes/dispatch')
def dispatch_page() -> str:
    return render_template('routes_dispatch.html', yandex_tiles_key=_yandex_tiles_key(), ai_enabled=ai_chat.available())


@bp.get('/routes/garage')
def garage_page() -> str:
    return render_template('routes_garage.html')


@bp.get('/routes/warehouse')
def warehouse_page() -> str:
    """«Պահեստ» (№78): склад с телефона отмечает погрузку рейсов утверждённого плана."""
    return render_template('routes_warehouse.html')


# --- API ---

@bp.get('/api/routes/overview')
@_api
def api_overview() -> Any:
    """Оценка текущего плана (§10.1). ?refresh=1 — пересобрать снимок ERP (не чаще раза в минуту).

    ERP недоступна, а прежний снимок есть — считаем по нему и предупреждаем (erp_stale).
    """
    state = _state()
    snap, stale = state.snapshots.get(refresh=request.args.get('refresh') == '1', allow_stale=True)
    bundle = _bundle(state)
    # калибровку и расстояния по дорогам — до кэша оценки: get_or_compute держит lock (вложенный
    # вызов бы завис, а первый расчёт матрицы дорог — минуты — задержал бы остальные запросы)
    calib = _calibration(state, snap, bundle.settings)
    roads = _roads(state, snap, bundle)
    # версия карты — в ключе: появилась или обновилась карта — оценка пересчитывается
    payload, from_cache = state.results.get_or_compute(
        ('overview', snap.id, bundle.fingerprint(), roads_version(roads)),
        lambda: _compute_overview(snap, bundle, calib, roads))
    body = {'success': True, **payload, 'from_cache': from_cache}
    if stale:   # кэшированный payload не трогаем — предупреждение только в этом ответе
        body['warnings'] = [{'code': 'erp_stale', 'link': None,
                             'text': f'ERP-ն հիմա հասանելի չէ — ցույց են տրված տվյալները '
                                     f'{snap.data_as_of:%d.%m, %H:%M} դրությամբ'}, *payload['warnings']]
    return jsonify(body)


def _compute_overview(snap: Snapshot, bundle: Bundle, calib: evaluate.Calibration,
                      roads: RoadDistances | ValhallaRoads | None) -> dict[str, Any]:
    started = time.perf_counter()
    payload = evaluate.build_overview(snap, bundle, calib, roads)
    payload['generated_at'] = datetime.now().isoformat(timespec='seconds')
    logger.info('[Routes] Оценка плана за %.1f с (снимок %s)', time.perf_counter() - started, snap.id)
    return payload


def _roads(state: RoutesState, snap: Snapshot, bundle: Bundle, extra: Sequence[Point] = (),
           truck_time: bool | None = None,
           center_zone: Sequence[Point] = ()) -> RoadDistances | CenterBypassRoads | ValhallaRoads | None:
    """Расстояния по дорогам для расчёта по плану снимка (и точкам extra — заказы развоза) — до кэша оценки:
    граф OSM для всех точек — одним расчётом (первый раз — минуты, дальше кэш на диске). Valhalla — только если его
    матрицы для этих точек (у машин менеджеров и, когда грузовикам нужен Valhalla, у грузовиков) уже готовы: тайлы и
    матрицы считает фоновый поток (ValhallaProvider), а пока — граф OSM; запрос тяжёлой работы Valhalla не ждёт.
    truck_time — минуты грузовиков из Valhalla («Развоз»: _truck_time_choice); None — ROUTES_TRUCK_TIME (обзор и
    календарь менеджеров: выбор обучения действует только в «Развозе»).
    center_zone — граница малого центра («Развоз»): граф OSM — с объездом центра (roads.CenterBypassRoads; объезд
    считается только для точек extra — заказов и склада), и он же — запасной путь Valhalla; пусто — без объезда
    (обзор и календарь менеджеров).
    Карты нет — None; граф не собрался — roads.failed (оценка считает по прямой и предупреждает roads_failed)."""
    points = [*evaluate.plan_points(snap, bundle, {}), *extra]
    roads = state.roads.get() if state.roads is not None else None
    if roads is not None:
        roads.ensure(points)
        if center_zone and not roads.failed:
            roads = state.roads.bypass(roads, center_zone)
            roads.ensure(extra)
    if state.valhalla is not None:
        capacity = max((t.capacity_kg for t in _ready_trucks(snap, bundle).values()), default=None)
        roads = state.valhalla.get(roads, points, capacity, truck_time=truck_time) or roads
    return roads


def _calibration(state: RoutesState, snap: Snapshot, s: dict[str, Any]) -> evaluate.Calibration:
    """Калибровка по GPS-трекам снимка; кэш — на снимок (и центр/радиус города: от них скорости)."""
    center = (float(s['city_center_lat']), float(s['city_center_lon']))
    radius = float(s['city_radius_km'])
    calib, _ = state.results.get_or_compute(
        ('calibration', snap.id, center, radius),
        lambda: evaluate.calibrate(snap.recent_visits, snap.fixes_by_agent,
                                   snap.today - timedelta(days=evaluate.CALIB_WINDOW_DAYS),
                                   snap.today, center, radius))
    return calib


@bp.get('/api/routes/settings')
@_api
def api_settings_get() -> Any:
    """Настройки и справочники для страницы настроек (§10.2)."""
    import os
    state = _state()
    bundle = state.store.load()
    snap, _ = state.snapshots.get(allow_stale=True)
    s = bundle.settings
    calib = _calibration(state, snap, s)
    season = evaluate.resolve_season(snap.season_index, s)
    # «авто» без ручных чисел: ровно те значения по GPS (округлённые, в диапазоне), что возьмёт расчёт
    gps = {**evaluate.road_norms({}, calib), **_visit_suggestions(state, snap, bundle, calib, season)}
    prices = _garage_prices(state, _clock().date(), bundle.trucks)
    return jsonify({
        'success': True,
        'settings': s,
        'traffic_provider': {'name': 'yandex', 'configured': bool(os.environ.get('ROUTES_YANDEX_API_KEY'))},
        'center_zone_default': DEFAULT_SETTINGS['center_zone'],
        'yerevan_zone_default': DEFAULT_SETTINGS['yerevan_zone'],
        'depot': {'lat': bundle.depot[0], 'lon': bundle.depot[1]} if bundle.depot else None,
        'trucks': _trucks_json(snap, bundle, prices, _garage_priors(state, prices, bundle.trucks)),
        'expeditors': _expeditors_json(snap, bundle),
        'car_idle_days': CAR_IDLE_DAYS,
        'managers': _managers_json(snap, bundle),
        'dispatch_agents': _dispatch_agents_json(snap, bundle),
        'customer_groups': _groups_json(snap),
        'season': {
            'index': {str(m): round(v, 2) for m, v in sorted((snap.season_index or {}).items())},
            'detected_low': season.detected_low,
            'detected_peak': season.detected_peak,
            # действующие месяцы — как в расчёте, с запасным выбором 3 крайних (season_empty)
            'low_months': season.low,
            'peak_months': season.peak,
            'fallback_low': 'low' in season.fallback,
            'fallback_peak': 'peak' in season.fallback,
        },
        'calibration': {
            'traffic': calib.traffic.report if getattr(calib, 'traffic', None) is not None else None,
            **{key: value if source == 'gps' else None for key, (value, source) in gps.items()},
            'visit_min_avg': (round(calib.visit_min_avg, 1)
                              if calib.visit_min_avg is not None else None),
            'days_used': calib.days_used,
        },
    })


def _visit_suggestions(state: RoutesState, snap: Snapshot, bundle: Bundle,
                       calib: evaluate.Calibration,
                       season: evaluate.Season) -> dict[str, tuple[float, str]]:
    """Длительности визита «авто» по GPS для сохранённых настроек: доли классов размера в плане
    зависят от порогов, сетей, рабочих дней и менеджеров в расчёте. Кэш — на снимок и настройки."""
    if calib.visit_min_avg is None:
        return evaluate.visit_norms({}, None, {})

    def compute() -> dict[str, tuple[float, str]]:
        s = bundle.settings
        included = {a for a in snap.plan.agent_ids if bundle.included(a, snap.active_agents)}
        models = evaluate.customer_models(snap, s, season, included)
        shares = evaluate.size_shares(snap.plan, {c: m.size for c, m in models.items()},
                                      included, s['workdays'])
        return evaluate.visit_norms({}, calib.visit_min_avg, shares)

    norms, _ = state.results.get_or_compute(('visit_norms', snap.id, bundle.fingerprint()), compute)
    return norms


def _json_body() -> tuple[Any, Any]:
    """(тело, None) или (None, ответ 415/400): POST раздела принимает только JSON."""
    if not request.is_json:
        return None, (jsonify({'success': False,
                               'error': 'Սերվերը չընդունեց հարցումը'}), 415)
    try:
        payload = request.get_json(silent=True)
    except RecursionError:   # silent глотает только ValueError, а сверхглубокая вложенность — это
        payload = None       # RecursionError парсера: тоже некорректный запрос (400), а не сбой (500)
    if payload is None:
        return None, (jsonify({'success': False, 'error': 'Սերվերը չընդունեց հարցումը',
                               'errors': {'_': 'Սերվերը չընդունեց հարցումը'}}), 400)
    return payload, None


def _bad_request(errors: dict[str, str]) -> Any:
    return jsonify({'success': False, 'error': next(iter(errors.values())), 'errors': errors}), 400


@bp.post('/api/routes/settings')
@_api
def api_settings_post() -> Any:
    """Сохранение настроек (§10.3): всё или ничего; ошибки — по путям полей."""
    if not request.is_json:
        return jsonify({'success': False,
                        'error': 'Սերվերը չընդունեց հարցումը'}), 415
    try:
        payload = request.get_json(silent=True)
    except RecursionError:   # silent глотает только ValueError, а сверхглубокая вложенность — это
        payload = None       # RecursionError парсера: тоже некорректный запрос (400), а не сбой (500)
    if payload is None:
        return jsonify({'success': False, 'errors': {'_': 'Սերվերը չընդունեց հարցումը'}}), 400
    state = _state()
    bundle = state.store.load()
    snap, _ = state.snapshots.get(allow_stale=True)
    merge_error = _merge_customers_off(payload, bundle)
    if merge_error:
        return jsonify({'success': False, 'errors': {'settings.dispatch_customers_off': merge_error}}), 400
    changes, errors = validate_payload(payload, bundle, snap.ref_data())
    if errors:
        return jsonify({'success': False, 'errors': errors}), 400
    user = session.get('username')
    state.store.save(changes, user)
    _cost_changed(state)
    logger.info('[Routes] Настройки сохранены пользователем %s', user)
    return jsonify({'success': True})


def _merge_customers_off(payload: Any, bundle: Bundle) -> str | None:
    """«Машины не везут» (№74) пишет не только страница настроек, но и «×» в «Развозе» (правило «никогда»): страница,
    открытая раньше, прислала бы весь свой старый список и стёрла новое. Поэтому она присылает и список, с которым
    открылась (settings.dispatch_customers_off_base): к нынешнему списку применяются только её добавления и удаления.
    Без base — как раньше (список целиком). Меняет payload на месте; ошибка base — текст."""
    s = payload.get('settings') if isinstance(payload, dict) else None
    if not isinstance(s, dict) or 'dispatch_customers_off_base' not in s:
        return None
    base, sent = s.pop('dispatch_customers_off_base'), s.get('dispatch_customers_off')
    ok = lambda v: isinstance(v, list) and all(isinstance(x, int) and not isinstance(x, bool) for x in v)  # noqa: E731
    if not ok(base) or not ok(sent):
        return 'սպասվում էր հաճախորդների ցուցակ'
    current = set(bundle.settings.get('dispatch_customers_off') or ())
    s['dispatch_customers_off'] = sorted((current - (set(base) - set(sent))) | (set(sent) - set(base)))
    return None


# --- Сборка ответа настроек ---

def _agent_order(snap: Snapshot) -> list[int]:
    return sorted(snap.plan.agent_ids,
                  key=lambda a: (snap.agents[a].code if a in snap.agents else '', a))


def _trucks_json(snap: Snapshot, bundle: Bundle, prices: Mapping[str, garage.Price] | None = None,
                 priors: Mapping[str, garage.Prior] | None = None) -> list[dict[str, Any]]:
    """Машины CARS и их настройки, затем ручные машины (их нет в ERP). Машина закреплена за водителем, а не
    за менеджером (ответ владельца №29) — вместо менеджера подсказка из ERP: сколько машина возит в день
    (90 дней) и когда последний раз была в накладных. «Активна» — действующее значение; active_source:
    manual — выбор владельца, auto — решают накладные (auto_active: возила за CAR_IDLE_DAYS дней).
    «Можно в центр» (center_ok) — так же: center_ok_source, auto_center_ok — по названию (машины JAC). «Большая машина»
    (big, №68) — так же: big_source, auto_big — по тоннажу (store.big_auto; страница пересчитывает при правке тоннажа).
    Тоннаж: capacity_kg — из настроек (его и сохраняет страница), erp_capacity_kg — из карточки ERP (CARS): в расчёте,
    когда своё поле пусто (Bundle.resolved_trucks), и по нему «большая машина» авто.
    Износ: wear_amd_per_km — ручное значение (его и сохраняет страница); garage — ремонт ֏/км журнала гаража на
    сегодня (только чтение), garage_prior — средняя модели или парка машине без своей цены журнала (garage.Prior),
    wear_source — какое значение в расчёте (garage | manual | garage_avg | None)."""
    prices, priors = prices or {}, priors or {}
    used = _garage_bundle(bundle, prices, priors)
    out = []
    active_cars, erp_capacity = snap.active_cars, snap.car_capacity
    for code, car in sorted(snap.cars.items()):
        t = bundle.trucks.get(code)
        if t is not None and t.manual:
            continue   # номер ручной машины появился в ERP — показываем её среди ручных
        days, avg, top = snap.car_days.get(code, (0, None, None))
        last = snap.car_last_used.get(code)
        out.append({
            'car_code': code,
            'name': car.name,
            'manual': False,
            'erp_closed': car.closed,
            'capacity_kg': t.capacity_kg if t else None,
            'erp_capacity_kg': erp_capacity.get(code),   # карточка ERP: в расчёте, когда своё поле пусто (resolved_trucks)
            'fuel_l_per_100km': t.fuel_l_per_100km if t else None,
            **profile_fields(t),
            'active': bundle.truck_active(code, active_cars),
            'active_source': 'auto' if t is None or t.active is None else 'manual',
            'auto_active': code in active_cars,
            'center_ok': bundle.truck_center_ok(code, car.name),
            'center_ok_source': 'auto' if t is None or t.center_ok is None else 'manual',
            'auto_center_ok': center_auto(car.name),
            'big': bundle.truck_big(code, erp_capacity),
            'big_source': 'auto' if t is None or t.big is None else 'manual',
            'auto_big': big_auto(bundle.truck_capacity(code, erp_capacity)),
            'garage': _garage_price_json(prices.get(code)), 'wear_source': used.wear_source(code),
            'garage_prior': _garage_prior_json(priors.get(code)),
            'last_used': last.isoformat() if last else None,
            'erp_days': days,
            'erp_kg_day': round(avg) if avg is not None else None,
            'erp_kg_day_max': round(top) if top is not None else None,
        })
    for code, t in sorted(bundle.trucks.items()):
        if t.manual:
            out.append({
                'car_code': code, 'name': t.name, 'manual': True, 'erp_closed': False,
                'capacity_kg': t.capacity_kg, 'fuel_l_per_100km': t.fuel_l_per_100km,
                **profile_fields(t),
                'active': bool(t.active), 'active_source': 'manual', 'auto_active': None, 'last_used': None,
                'center_ok': bundle.truck_center_ok(code, t.name),
                'center_ok_source': 'auto' if t.center_ok is None else 'manual', 'auto_center_ok': center_auto(t.name),
                'big': bundle.truck_big(code), 'big_source': 'auto' if t.big is None else 'manual',
                'auto_big': big_auto(t.capacity_kg),
                'garage': _garage_price_json(prices.get(code)), 'wear_source': used.wear_source(code),
                'garage_prior': _garage_prior_json(priors.get(code)),
                'van_agent_id': t.van_agent_id, 'van': _agent_json(snap, t.van_agent_id),
                'erp_days': 0, 'erp_kg_day': None, 'erp_kg_day_max': None,
            })
    return out


def _garage_price_json(p: garage.Price | None) -> dict[str, Any] | None:
    """Ремонт ֏/км машины по журналу гаража (garage.Price) для страниц; нет записей журнала — None."""
    if p is None:
        return None
    return {'price': p.price, 'status': p.status, 'months': p.months, 'days': p.days, 'km': round(p.km),
            'cost_amd': p.cost_amd, 'start': p.start.isoformat() if p.start else None,
            'end': p.end.isoformat() if p.end else None, 'repair_amd': p.repair_amd, 'accident_amd': p.accident_amd,
            'fixed_amd': p.fixed_amd, 'ready_months': garage.MONTHS_READY, 'own': p.own, 'model': p.model,
            'model_price': p.model_price, 'blend': p.blend, 'blend_km': garage.BLEND_KM}


def _garage_prior_json(p: garage.Prior | None) -> dict[str, Any] | None:
    """Средняя модели (scope model) или парка (fleet) машине без своей готовой цены журнала; нет — None."""
    return None if p is None else {'price': p.price, 'scope': p.scope, 'model': p.model}


def _agent_json(snap: Snapshot, agent_id: int | None) -> dict[str, Any] | None:
    """Экспедитор: код, имя, накладных без машины за 90 дней и последний день (если возил)."""
    if agent_id is None:
        return None
    agent = snap.agents.get(agent_id)
    docs, last = snap.expeditors.get(agent_id, (0, None))
    return {'agent_id': agent_id, 'code': agent.code if agent else str(agent_id), 'name': agent.name if agent else '',
            'docs': docs, 'last_day': last.isoformat() if last else None}


def _expeditors_json(snap: Snapshot, bundle: Bundle) -> list[dict[str, Any]]:
    """Кто возил заказы без машины в накладных за 90 дней (от самого частого) и за какой ручной машиной
    закреплён (truck: код или None) — выбор экспедитора и подсказка «нет нужной машины?»."""
    linked = bundle.van_trucks()
    return [{**_agent_json(snap, a), 'truck': linked.get(a)}
            for a in sorted(snap.expeditors, key=lambda a: (-snap.expeditors[a][0], a))]


CUSTOMER_HINT_DAYS = 60    # подсказка к списку «машины не везут» (№74): заказы за 60 дней
CUSTOMER_HINT_MAX = 200    # клиентов в подсказке — самые большие по сумме
CUSTOMER_IDS_MAX = 200     # id в одном ?ids= — страница шлёт частями (адрес короче буфера заголовков nginx)


@bp.get('/api/routes/settings/customers')
@_api
def api_settings_customers() -> Any:
    """Клиенты для списка «машины не везут» (№74): ?q= — поиск в ERP по коду (с начала) или названию (часть), до 30;
    ?ids=1,2 — клиенты по id (названия уже отмеченных). Только чтение ERP; без загрузчика — пусто."""
    query = request.args.get('q', '')
    if len(query) > CUSTOMER_FIND_MAX_LEN:
        return _bad_request({'q': f'Որոնում՝ առավելագույնը {CUSTOMER_FIND_MAX_LEN} նիշ'})
    raw = request.args.get('ids', '')
    parts = raw.split(',') if raw else []
    if len(parts) > CUSTOMER_IDS_MAX or not all(
            p.isascii() and p.isdigit() and len(p) <= 10 and 0 < int(p) < 2 ** 31 for p in parts):
        return _bad_request({'ids': 'Սպասվում էր հաճախորդների համարներ'})
    state = _state()
    if state.customer_ref_loader is None or not (query.strip() or parts):
        return jsonify({'success': True, 'customers': []})
    refs = state.customer_ref_loader(query, sorted({int(p) for p in parts}))
    return jsonify({'success': True, 'customers': [
        {'customer_id': c.customer_id, 'code': c.code, 'name': c.name, 'address': c.address} for c in refs]})


@bp.get('/api/routes/settings/customer-hints')
@_api
def api_settings_customer_hints() -> Any:
    """Подсказка к списку «машины не везут» (№74): клиенты с заказами за CUSTOMER_HINT_DAYS дней без адреса или вне
    Армении (erp.customer_hints) — с менеджерами, заказами и суммой. Только чтение ERP."""
    state = _state()
    today = _clock().date()
    if state.customer_hint_loader is None:
        return jsonify({'success': True, 'days': CUSTOMER_HINT_DAYS, 'customers': []})
    hints = state.customer_hint_loader(today - timedelta(days=CUSTOMER_HINT_DAYS), today + timedelta(days=1))
    snap, _ = state.snapshots.get(allow_stale=True)

    def agent(aid: int) -> dict[str, Any]:
        a = snap.agents.get(aid)
        return {'agent_id': aid, 'code': a.code if a else '', 'name': a.name if a else ''}
    return jsonify({'success': True, 'days': CUSTOMER_HINT_DAYS, 'customers': [
        {'customer_id': h.customer_id, 'code': h.code, 'name': h.name, 'address': h.address, 'reason': h.reason,
         'orders': h.orders, 'revenue': round(h.revenue), 'last_day': h.last_day.isoformat(),
         'agents': [agent(a) for a in h.agents]} for h in hints[:CUSTOMER_HINT_MAX]]})


def _dispatch_agents_json(snap: Snapshot, bundle: Bundle) -> list[dict[str, Any]]:
    """Менеджеры карточки «чьи заказы везём» (№69): с заказами или визитами за 8 недель (кроме закрытых в ERP) и все,
    кого правило уже снимает, — их можно вернуть, даже если работы больше нет. По имени, неизвестные ERP — в конце."""
    ids = {a for a in snap.active_agents if not (a in snap.agents and snap.agents[a].closed)}
    ids |= set(bundle.settings['dispatch_agents_off']) | set(bundle.settings['dispatch_fleet_agents'])   # и правило №74
    out = []
    for agent_id in ids:
        agent = snap.agents.get(agent_id)
        out.append({'agent_id': agent_id, 'code': agent.code if agent else '', 'name': agent.name if agent else '',
                    'area': agent.area if agent else ''})
    out.sort(key=lambda a: (not (a['name'] or a['code']), a['name'] or a['code'], a['agent_id']))
    return out


def _managers_json(snap: Snapshot, bundle: Bundle) -> list[dict[str, Any]]:
    out = []
    for agent_id in _agent_order(snap):
        agent = snap.agents.get(agent_id)
        profile = bundle.profile(agent_id)
        auto = snap.auto_homes.get(agent_id)
        out.append({
            'agent_id': agent_id,
            'code': agent.code if agent else str(agent_id),
            'name': agent.name if agent else '',
            'included': bundle.included(agent_id, snap.active_agents),   # действующее значение
            'included_source': bundle.included_source(agent_id),        # manual | auto
            'inactive': agent_id not in snap.active_agents,              # нет работы 8 недель
            'home': {'lat': profile.home_lat, 'lon': profile.home_lon},
            'home_suggestion': None if auto is None else {
                'lat': round(auto.lat, 6), 'lon': round(auto.lon, 6),
                'method': auto.method, 'days': auto.days},
            'car_fuel_l_per_100km': profile.car_fuel_l_per_100km,
            'car_fuel_type': profile.car_fuel_type or DEFAULT_MANAGER_FUEL,
        })
    return out


def _groups_json(snap: Snapshot) -> list[dict[str, Any]]:
    """Группы клиентов (CustGrp); customers — сколько клиентов плана в группе."""
    counts = Counter(c.group for c in snap.customers.values())
    return [{'code': code, 'name': name, 'customers': counts.get(code, 0)}
            for code, name in sorted(snap.customer_groups.items())]


# --- Этап 3: оптимизация дней визитов (режим А) ---

def _busy(job_id: str | None) -> Any:
    """409: расчёт уже идёт; job_id — чтобы страница показала ход идущей задачи."""
    body: dict[str, Any] = {'success': False, 'error': 'Հաշվարկն արդեն ընթանում է'}
    if job_id is not None:
        body['job_id'] = job_id
    return jsonify(body), 409


def _live_decisions(state: RoutesState, snap: Snapshot) -> list[Decision]:
    """Действующие решения владельца для плана снимка. Отработавшие (принятое уже исполнено в ERP;
    отклонённое — план клиента с тех пор изменился) отмечаются в базе как retired и дальше не
    закрепляют и не запрещают. Устаревшие принятые остаются: их видит владелец (stale)."""
    decisions = state.store.load_decisions()
    pairs = optimize.plan_pairs(snap.plan)
    done = {d for d in decisions if optimize.decision_state(d, pairs) == optimize.DECISION_RETIRED}
    if done:
        n = state.store.retire_decisions(done)
        logger.info('[Routes] Решений отработало (исполнены в ERP или план клиента изменился): %d', n)
    return [d for d in decisions if d not in done]


def _last_result(state: RoutesState) -> dict[str, Any] | None:
    """Результат последнего успешного расчёта (в памяти или в таблице scenario)."""
    with state.jobs.lock:
        last = state.jobs.last.result if state.jobs.last is not None else None
    if last is None:
        scenario = state.store.last_scenario()
        last = scenario.result if scenario is not None else None
    return last


@bp.post('/api/routes/optimize')
@_api
def api_optimize_start() -> Any:
    """Запуск расчёта в фоновом потоке. Одновременно — одна задача (иначе 409 с id идущей). Снимок
    ERP берётся один раз при запуске (кэш этапа 1, допускается устаревший), решения владельца — на
    этот момент (отработавшие — в архив, устаревшие — не применяются)."""
    payload, error = _json_body()
    if error is not None:
        return error
    params, errors = optimize.parse_params(payload)
    if errors:
        return _bad_request(errors)
    state = _state()
    running = state.jobs.running_id()
    if running is not None:
        return _busy(running)
    snap, stale = state.snapshots.get(allow_stale=True)
    bundle = _bundle(state)
    run_ids, problem = optimize.resolve_agents(snap, bundle, params['agent_ids'])
    if problem:
        return _bad_request({'agent_ids': problem})
    decisions = _live_decisions(state, snap)
    job, running = state.jobs.start({**params, 'agent_ids': run_ids}, session.get('username'))
    if job is None:
        return _busy(running)
    try:
        threading.Thread(target=_run_optimize, args=(state, job, snap, bundle, decisions),
                         name=f'routes-optimize-{job.id[:8]}', daemon=True).start()
    except BaseException:
        _finish(state, job, error='Չհաջողվեց սկսել հաշվարկը')
        raise
    logger.info('[Routes] Оптимизация %s запущена (%s): менеджеров %d, старт %s, частоты %s, режим %s%s',
                job.id, job.created_by, len(run_ids), params['start'], params['frequencies'],
                params.get('mode', optimize.MODE_DAYS), ', снимок ERP устарел' if stale else '')
    return jsonify({'success': True, 'job_id': job.id})


def _finish(state: RoutesState, job: OptimizeJob, *, result: dict[str, Any] | None = None,
            error: str | None = None) -> None:
    """Завершить задачу (повторный вызов ничего не меняет) и освободить место для следующей."""
    with state.jobs.lock:
        if job.status == 'running':
            job.status = 'done' if error is None else 'error'
            job.result, job.error, job.finished_at = result, error, _now()
            if error is None:
                job.done = job.total
                job.agent_code = None
                state.jobs.last = job
                # в памяти — только последний результат; прежние — в таблице scenario
                for other in state.jobs.jobs.values():
                    if other is not job:
                        other.result = None
        if state.jobs.running is job:
            state.jobs.running = None


def _run_optimize(state: RoutesState, job: OptimizeJob, snap: Snapshot, bundle: Bundle,
                  decisions: list[Decision]) -> None:
    """Тело фоновой задачи: ошибки — в job.error по-русски, подробности — только в журнал."""
    try:
        calib = _calibration(state, snap, bundle.settings)
        roads = _roads(state, snap, bundle)

        def progress(done: int, total: int, agent_code: str | None) -> None:
            with state.jobs.lock:
                job.done, job.total, job.agent_code = done, total, agent_code

        outcome = optimize.run_optimization(snap, bundle, calib, decisions, job.params,
                                            progress=progress, roads=roads)
        try:
            state.store.save_scenario(job.id, job.created_by, job.params, outcome.result)
        except Exception:   # расчёт готов — отдаём его; не сохранился только след в таблице scenario
            logger.exception('[Routes] Оптимизация %s: расчёт не сохранён в базу маршрутов — '
                             'результат есть только в памяти', job.id)
        _finish(state, job, result=outcome.result)
    except ErpError:
        logger.exception('[Routes] Оптимизация %s: ERP недоступна', job.id)
        _finish(state, job, error='ERP տվյալների բազան հասանելի չէ')
    except StoreError as e:
        logger.exception('[Routes] Оптимизация %s: база маршрутов', job.id)
        _finish(state, job, error=str(e))   # текст StoreError — для пользователя
    except optimize.OptimizeError as e:
        logger.warning('[Routes] Оптимизация %s не выполнена: %s', job.id, e)
        _finish(state, job, error=str(e))
    except Exception:
        logger.exception('[Routes] Оптимизация %s: внутренняя ошибка', job.id)
        _finish(state, job, error='Հաշվարկի ներքին սխալ — մանրամասները սերվերի մատյանում են')
    finally:
        _finish(state, job, error='Հաշվարկն ընդհատվել է')   # задача уже завершена — ничего не меняет


def _job_body(state: RoutesState, job: dict[str, Any], progress: dict[str, Any],
              result: dict[str, Any] | None) -> dict[str, Any]:
    """Ответ о задаче; статусы решений в результате — актуальные (таблица decision)."""
    if result is not None:
        result = optimize.with_decisions(result, state.store.load_decisions())
    return {'success': True, 'job': job, 'progress': progress, 'result': result}


def _scenario_job(scenario: Any) -> OptimizeJob:
    """Сохранённый расчёт (после перезапуска сервера) — как завершённая задача."""
    total = len(scenario.params.get('agent_ids') or [])
    return OptimizeJob(scenario.id, scenario.params, scenario.created_by, scenario.created_at,
                       status='done', finished_at=scenario.created_at, done=total, total=total,
                       result=scenario.result)


def _job_view(job: OptimizeJob) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    return job.job_json(), job.progress_json(), job.result


@bp.get('/api/routes/optimize/last')
@_api
def api_optimize_last() -> Any:
    """Последний успешно завершённый расчёт (в памяти или в таблице scenario); нет — 404."""
    state = _state()
    with state.jobs.lock:
        view = _job_view(state.jobs.last) if state.jobs.last is not None else None
    if view is None:
        scenario = state.store.last_scenario()
        if scenario is None:
            return jsonify({'success': False, 'error': 'Հաշվարկներ դեռ չեն եղել'}), 404
        job = _scenario_job(scenario)
        with state.jobs.lock:
            if state.jobs.last is None:
                state.jobs.last = job
        view = _job_view(job)
    return jsonify(_job_body(state, *view))


@bp.get('/api/routes/optimize/<job_id>')
@_api
def api_optimize_job(job_id: str) -> Any:
    """Статус задачи, прогресс и (когда готово) результат §10.1."""
    state = _state()
    with state.jobs.lock:
        job = state.jobs.jobs.get(job_id)
        view = _job_view(job) if job is not None else None
    if view is not None and view[0]['status'] == 'done' and view[2] is None:
        # в памяти — только последний результат; результат прежней задачи — из таблицы scenario
        scenario = state.store.get_scenario(job_id)
        view = (view[0], view[1], scenario.result if scenario is not None else None)
    if view is None:
        scenario = state.store.get_scenario(job_id) if _JOB_ID_RE.match(job_id) else None
        if scenario is None:
            return jsonify({'success': False, 'error': 'Հաշվարկը չի գտնվել'}), 404
        view = _job_view(_scenario_job(scenario))
    return jsonify(_job_body(state, *view))


@bp.get('/api/routes/decisions')
@_api
def api_decisions_list() -> Any:
    """«Ваши решения»: действующие решения владельца с именами и текстами «было → стало», пометка
    «устарело» (план клиента в ERP изменился после решения), итоги и сколько изменений войдёт в
    план для ERP. Снимок — из кэша, ERP не перечитывается."""
    state = _state()
    snap, _ = state.snapshots.cached()
    bundle = _bundle(state)
    decisions = _live_decisions(state, snap)
    body = optimize.decisions_view(snap, bundle, decisions, optimize.proposals_of(_last_result(state)))
    return jsonify({'success': True, **body})


@bp.post('/api/routes/decisions')
@_api
def api_decisions() -> Any:
    """Решения по предложениям: accept — закрепить, reject — запретить, reset — снять. Тело — одно
    решение, {"items": [...]} (одной транзакцией: все или ни одного, ошибки по строкам) или
    {"action": "reset_all"} («Сбросить все»). Решение привязано к «было» предложения (from; нет —
    текущий план снимка из кэша, ERP не перечитывается). Действует в следующем расчёте; статус сразу
    виден в последнем результате (накладывается при выдаче)."""
    payload, error = _json_body()
    if error is not None:
        return error
    req, errors = optimize.parse_decisions(payload)
    if errors:
        return _bad_request(errors)
    state = _state()
    user = session.get('username')
    if req.mode == 'reset_all':
        n = state.store.reset_decisions()
        logger.info('[Routes] Все решения сброшены (%s): было действующих %d', user, n)
        return jsonify({'success': True, 'deleted': n})
    snap, _ = state.snapshots.cached()
    items, errors = optimize.bind_decisions(req, optimize.plan_pairs(snap.plan))
    if errors:
        return _bad_request(errors)
    state.store.save_decisions(items, user)
    if req.mode == 'batch':
        logger.info('[Routes] Решения пачкой (%s): %s', user,
                    dict(Counter(f'{d.action}/{d.kind}' for d in items)))
        return jsonify({'success': True, 'saved': len(items)})
    d = items[0]
    logger.info('[Routes] Решение %s (%s): клиент %d, менеджер %d, %s %s (от %s)', d.action, user,
                d.customer_id, d.agent_id, d.kind, d.value, d.from_value)
    return jsonify({'success': True})


@bp.get('/api/routes/plan-export')
@_api
def api_plan_export() -> Any:
    """План для ERP: текущий план + только принятые изменения, цикл 2 недели (§9). Снимок — из кэша,
    ERP не перечитывается; устаревшие решения не применяются и перечислены в stale_decisions."""
    state = _state()
    snap, _ = state.snapshots.cached()
    bundle = _bundle(state)
    # порядок внутри дня — той же функцией расстояния, что и в оценке (по дорогам, если есть карта)
    roads = _roads(state, snap, bundle)
    distance = None
    calib = _calibration(state, snap, bundle.settings)
    if roads is not None and not roads.failed:
        distance = evaluate.Norms.from_settings(bundle.settings, calib, roads).distance
    else:
        roads = None
    # принята только частота — в план идёт тот шаблон, что был в предложении
    body = optimize.plan_export(snap, bundle, _live_decisions(state, snap),
                                optimize.proposals_of(_last_result(state)), distance, calib, roads)
    if not body['time_gate']['ok']:
        return jsonify({'success':False,'code':'shift_exceeded',
                        'error':'Ընդունված պլանը չի տեղավորվում աշխատանքային օրվա մեջ։ Ստուգեք օրերը և հաճախականությունները, '
                                'ապա վերահաշվեք պլանը։',
                        'time_gate':body['time_gate']}), 409
    return jsonify({'success': True, 'generated_at': _now(), **body})


# --- План развоза на завтра (dispatch-plan.md) ---

DISPATCH_TTL_SECONDS = 120        # заказы дня из ERP: правки логиста не перечитывают ERP каждый раз
DISPATCH_CACHE_DAYS = 8
_DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_MAX_TRUCKS = 50


def _parse_day(raw: Any) -> date | None:
    if not isinstance(raw, str) or not _DAY_RE.match(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _dispatch_data(state: RoutesState, since: date, until: date, day: date, refresh: bool) -> dp.DispatchData:
    """Заказы дня из ERP; кэш DISPATCH_TTL_SECONDS. «Обновить» перечитывает ERP не чаще раза в минуту."""
    key = (since, until, day)
    now = time.monotonic()
    with state.dispatch_lock:
        hit = state.dispatch_cache.get(key)
    if hit is not None:
        age = now - hit[0]
        if age < MIN_REFRESH_SECONDS or (age < DISPATCH_TTL_SECONDS and not refresh):
            return hit[1]
    if state.dispatch_loader is None:
        raise ErpError('Загрузчик заказов не подключён')
    data = state.dispatch_loader(since, until, day)
    with state.dispatch_lock:
        state.dispatch_cache[key] = (time.monotonic(), data)
        while len(state.dispatch_cache) > DISPATCH_CACHE_DAYS:
            state.dispatch_cache.pop(next(iter(state.dispatch_cache)))
    return data


def _same_day_data(state: RoutesState, day: date, refresh: bool) -> dp.SameDayData | None:
    """Заказы ERP с датой day (новые заказы дня, №72) — по правилам кэша _dispatch_data; загрузчика нет — None."""
    if state.same_day_loader is None:
        return None
    now = time.monotonic()
    with state.dispatch_lock:
        hit = state.same_day_cache.get(day)
    if hit is not None:
        age = now - hit[0]
        if age < MIN_REFRESH_SECONDS or (age < DISPATCH_TTL_SECONDS and not refresh):
            return hit[1]
    data = state.same_day_loader(day)
    with state.dispatch_lock:
        state.same_day_cache[day] = (time.monotonic(), data)
        while len(state.same_day_cache) > DISPATCH_CACHE_DAYS:
            state.same_day_cache.pop(next(iter(state.same_day_cache)))
    return data


def _same_day_now() -> datetime:
    """Сейчас по Еревану без пояса — «сегодня» и «сейчас» новых заказов дня (№72): какие рейсы уже грузятся."""
    return _yerevan_now().replace(tzinfo=None)


def _peek_car_capacity(state: RoutesState) -> dict[str, float] | None:
    """Тоннаж машин из карточек ERP по снимку в памяти (SnapshotCache.peek: ERP здесь не читается) — для отчётов,
    чтобы загрузка считалась от того же тоннажа, что и в расчёте; снимка нет — только тоннаж из настроек."""
    snap = state.snapshots.peek()
    return snap.car_capacity if snap is not None else None


def _ready_trucks(snap: Snapshot, bundle: Bundle, active_only: bool = True) -> dict[str, fl.FleetTruck]:
    """Машины, готовые к расчёту: тоннаж и расход заданы (и активны — для плана); с правом въезда в центр и признаком
    «большая машина» (№68); износ — как в расчёте (Bundle.resolved_trucks: журнал гаража или ручной)."""
    names = {code: car.name for code, car in snap.cars.items()}
    resolved = bundle.resolved_trucks(snap.active_cars, snap.car_capacity)
    if active_only:
        ready, _ = fl.fleet_trucks(resolved, names)
        return {t.car_code: replace(t, center_ok=bundle.truck_center_ok(t.car_code, names.get(t.car_code)),
                                    big=bundle.truck_big(t.car_code, snap.car_capacity))
                for t in ready}
    return {code: fl.FleetTruck(code, names.get(code) or t.name, float(t.capacity_kg), float(t.fuel_l_per_100km),
                                bundle.truck_center_ok(code, names.get(code)), **profile_fields(t),
                                big=bundle.truck_big(code, snap.car_capacity))
            for code, t in sorted(resolved.items())
            if t.capacity_kg is not None and t.fuel_l_per_100km is not None}


def _dispatch_ctx(state: RoutesState, snap: Snapshot, bundle: Bundle, day: date,
                  trucks: dict[str, fl.FleetTruck], points: list[Any], customers: Mapping[int, Point] | None = None,
                  learned_before: str | None = None, learned: bool = True,
                  truck_time: bool | None = None) -> dp.DayContext | None:
    """Контекст расчёта рейсов; склада или машин нет — None (страница объясняет, что заполнить). Действующие
    выученные нормы (_with_learned; learned_before — только прогоны раньше этого дня; learned=False — без них) — поверх
    настроек, введённое время магазинов (№50) — всегда; customers — клиент → точка дня (своё время магазина — по точке).
    Минуты грузовиков — truck_time (True — Valhalla, False — прежняя модель); None — _truck_time_choice по тому же
    чтению журнала (learned=False — без выбора обучения). Срез дорог — один на расчёт: км, минуты, road_model_id и
    поправка по часам — из одной модели; Valhalla для этих точек не готов — прежняя модель (граф OSM)."""
    if bundle.depot is None or not trucks:
        return None
    s = bundle.settings
    journal = _learned_journal(state, learned_before) if learned else None
    if truck_time is None:
        truck_time = _truck_time_choice(journal)[0] == TRUCK_TIME_VALHALLA
    calib = _calibration(state, snap, s)
    zone = tuple((lat, lon) for lat, lon in s['center_zone'])
    # участки между точками вне малого центра — в объезд него: машинам без права въезда центр закрыт и в пути
    roads = _roads(state, snap, bundle, [*points, bundle.depot], truck_time=truck_time, center_zone=zone)
    norms = evaluate.Norms.from_settings(s, calib, roads if roads is not None and not roads.failed else None)
    norms = norms.for_trucks()   # развоз — профиль грузовика (км Valhalla — в режиме valhalla, минуты — truck_time)
    h, m = map(int, s['truck_work_start'].split(':'))
    norms = replace(norms, traffic_weekday=day.weekday(), traffic_start_min=float(h * 60 + m))
    if s.get('traffic_mode') == 'yandex':
        from .traffic_provider import load
        provider, status = load([bundle.depot, *points], day, s['truck_work_start'])
        osm = roads.fallback if isinstance(roads, ValhallaRoads) else roads
        if provider is not None and isinstance(osm, CenterBypassRoads):
            # путь Яндекса — кратчайший: на участках в объезд центра его км и минуты × объезд (как у ValhallaRoads)
            k = {pair: osm.detour(*pair) for pair in provider.distances}
            provider = replace(provider, distances={p: v * k[p] for p, v in provider.distances.items()},
                               durations={p: v * k[p] for p, v in provider.durations.items()})
        norms = replace(norms, provider=provider, traffic_status=status)
    h2, m2 = map(int, s['truck_overtime_end'].split(':'))
    tn = fl.TruckNorms.from_settings(s, lunch=True)     # обед в пути (№61) — только «Развоз»
    yerevan = tuple((lat, lon) for lat, lon in s['yerevan_zone'])
    if len(yerevan) >= 3:   # большая машина в Ереване (№68): зона и надбавка больших машин; пустая зона — правила нет
        extra = float(s['big_truck_yerevan_min'])
        tn = replace(tn, yerevan_zone=yerevan, yerevan_km=float(s['big_truck_yerevan_km']),
                     yerevan_min={code: extra for code, t in trucks.items() if t.big and extra > 0})
    # learned=False — журнала нет: выученных норм нет (eff пуст), только введённое время магазинов
    norms, tn, trucks, eff = _with_learned(state, norms, tn, trucks, customers or {}, journal, bundle.unload_min)
    # №78: вне сезона утренней погрузки первые рейсы загружены с вечера; запас в конце дня — горизонт сборки
    tn = replace(tn, preload=not dp.morning_loading(day, s))
    return dp.DayContext(day, bundle.depot, trucks, norms, tn, h * 60 + m,
                         float(h2 * 60 + m2 - (h * 60 + m)), float(s['min_trip_revenue']),
                         {cid: w.span() for cid, w in bundle.windows_on(day).items()}, zone,
                         vehicle_access=bundle.vehicle_access,
                         model=_model_note(s, calib, norms, eff, [p for p in points if p is not None], trucks),
                         end_reserve_min=float(s['truck_end_reserve_min']), solo=bundle.solo,
                         center_allow=bundle.center_allow, solo_spare_max_pct=float(s['solo_spare_max_pct']))


def _model_note(s: Mapping[str, Any], calib: evaluate.Calibration, norms: Any, eff: learning.InEffect,
                points: Sequence[Point], trucks: Mapping[str, fl.FleetTruck]) -> dict[str, Any]:
    """Что учитывает расчёт «Развоза» — для пояснения на странице («Ի՞նչ է հաշվի առնված հաշվարկում»), в расчёте не
    участвует: км участков — как их берёт Norms.km (Яндекс, Valhalla, граф OSM или по прямой × извилистость), сколько точек
    считается по прямой (_straight_points), объезд малого центра, минуты (скорость зоны, Valhalla или Яндекс), скорости
    зон и их источник, часовой профиль по GPS менеджеров и действующие выученные нормы (_with_learned; расход — только
    у машин расчёта; обед — минуты, если выученный действует и обед в настройках включён)."""
    roads = norms.roads
    osm = roads.fallback if isinstance(roads, ValhallaRoads) else roads
    source = 'straight' if roads is None or roads.failed else roads.km_source
    # Norms.km сначала спрашивает поставщика (Яндекс): его км, у кого их нет — дороги
    km = 'yandex' if norms.provider is not None else source if source in ('osm', 'straight') else 'valhalla'
    road = evaluate.road_norms(s, calib)
    report = norms.traffic.report if norms.traffic is not None else {}
    return {
        'km': km, 'detour': norms.detour,
        'unsnapped': _straight_points(roads, points) if km in ('osm', 'valhalla') else None, 'snap_km': SNAP_MAX_KM,
        'bypass': isinstance(osm, CenterBypassRoads) and not osm.failed and not osm.bypass.failed,
        'minutes': ('yandex' if norms.provider is not None else
                    'valhalla' if isinstance(roads, ValhallaRoads) and roads.serves_minutes else 'zones'),
        'speed_city_kmh': norms.speed_city_kmh, 'speed_city_source': road['speed_city_kmh'][1],
        'speed_region_kmh': norms.speed_region_kmh, 'speed_region_source': road['speed_region_kmh'][1],
        'hourly_gps': report.get('source') == 'historical_gps',
        'learned': {'travel': bool(eff.travel), 'unload': bool(eff.unload), 'loading': bool(eff.loading),
                    'fuel': sorted(c for c in (eff.fuel or ()) if c in trucks),
                    # обед (№61) — минуты выученного, только когда он действует: без него пояснение — прежнее до байта
                    **({'lunch': float(eff.lunch['minutes'])}
                       if eff.lunch and float(s.get('truck_lunch_min') or 0) > 0 else {}),
                    # запас на рейс и темп машин (№66) — только когда действуют: без них пояснение — прежнее до байта
                    **({'buffer_pct': float(s['dispatch_buffer_pct'])}
                       if learning.buffer_c_for(eff.buffer, float(s.get('dispatch_buffer_pct') or 0)) else {}),
                    **({'pace': {c: list(p) for c, p in sorted(pace.items()) if c in trucks}}
                       if (pace := learning.truck_pace(eff, learning.road_model_id(norms))) else {})},
    }


def _straight_points(roads: RoadDistances | CenterBypassRoads | ValhallaRoads, points: Sequence[Point]) -> int:
    """Точки дня дальше SNAP_MAX_KM от дороги — их участки Norms.km считает по прямой × извилистость. У Valhalla точка без
    своей привязки берёт км графа OSM (запасной путь) — по прямой, только если не привязана и к нему."""
    fallback = roads.fallback if isinstance(roads, ValhallaRoads) else None
    if fallback is None or fallback.failed:
        return roads.unsnapped(points)
    return sum(1 for p in {p for p in points if p is not None} if roads.unsnapped([p]) and fallback.unsnapped([p]))


@dataclass
class _DispatchDay:
    """Всё о дне развоза для ответа и правок."""
    day: date
    since: date
    until: date
    data: dp.DispatchData
    deliver: list[dp.DispatchOrder]
    backlog: list[dp.DispatchOrder]
    shipped_before: int
    self_delivery: list[dp.DispatchOrder]
    draft: dp.Draft | None
    rev: int
    stops: list[dp.Stop]
    ctx: dp.DayContext | None
    ready: dict[str, fl.FleetTruck]
    snap: Snapshot
    bundle: Bundle
    carried: set[str] = field(default_factory=set)   # перенесённые на этот день «Везти завтра» прошлых дней
    # новые заказы дня (№72): заказы с датой этого дня (кандидаты; взятые — в draft.same_day) и их данные ERP; день не
    # сегодняшний и взятых нет — пусто и None
    same_day: list[dp.DispatchOrder] = field(default_factory=list)
    same_day_data: dp.SameDayData | None = None
    same_day_taken: int = 0   # заказов окна дня, взятых в развоз дня их приёма (их не везём: _same_day_taken)
    same_day_unread: bool = False   # план прошлого дня не прочитан — взятые в нём заказы могли не исключиться
    # №74: заказы дня, которые везут другие машины (менеджер правила, город-исключение) и клиентов «машины не везут»
    other_vehicle: list[dp.DispatchOrder] = field(default_factory=list)
    customers_off: list[dp.DispatchOrder] = field(default_factory=list)
    # «Երբ տանել» прошлых дней (dp.later_carried): isn → (день доставки | None — снят, дата заказа, день плана)
    later: dict[str, tuple[date | None, date, date]] = field(default_factory=dict)


def _sent_plan(state: RoutesState, d: date) -> dp.Draft | None:
    """План дня d, отправленный водителям (№81, Draft.for_drivers); плана нет — None, не прочитан — StoreError. Один раз
    за запрос (g): переносы (_carried), взятые заказы дня (_same_day_taken), «видел» (№79, _plan_seen) и «Երբ տանել»
    (_later_scan) смотрят одни и те же прошлые планы. Только для чтения: черновик своего дня правят через _stored_draft."""
    def load() -> dp.Draft | StoreError | None:
        try:
            stored = state.store.load_dispatch(d.isoformat())
        except StoreError as e:
            return e
        return dp.Draft.from_json(stored[0]).for_drivers() if stored is not None else None
    if has_request_context():
        memo = g.setdefault('_sent_plans', {})
        key = (id(state), d)
        if key not in memo:
            memo[key] = load()
        out = memo[key]
    else:
        out = load()
    if isinstance(out, StoreError):
        raise out
    return out


def _later_scan(state: RoutesState, day: date, workdays: Sequence[int],
                holidays: Collection[date] = ()) -> dict[str, tuple[date | None, date, date]]:
    """«Երբ տանել» из планов LATER_SCAN_DAYS дней до day (dp.later_carried; отправленные водителям — №81). Битый или
    непрочитанный черновик — без его переносов (как _carried)."""
    plans: list[tuple[date, dp.Draft]] = []
    d = day - timedelta(days=dp.LATER_SCAN_DAYS)
    while d < day:
        try:
            plan = _sent_plan(state, d)
        except StoreError:
            logger.warning('[Routes] План развоза на %s не прочитан — его «Երբ տանել» на %s не учтены', d, day,
                           exc_info=True)
            plan = None
        if plan is not None:
            plans.append((d, plan))
        d += timedelta(days=1)
    return dp.later_carried(plans, workdays, holidays)


def _later_due(later: Mapping[str, tuple[date | None, date, date]], day: date) -> dict[str, date]:
    """Перенесённые «Երբ տանել» именно на day: isn → дата заказа. Сам в развоз дня входит только такой заказ."""
    return {isn: od for isn, (to, od, _) in later.items() if to == day}


def _day_orders(state: RoutesState, bundle: Bundle, day: date, refresh: bool, rule: dp.FleetRule,
                later: Mapping[str, tuple[date | None, date, date]] | None = None
                ) -> tuple[date, date, dp.DispatchData, dp.Selection]:
    """Окно заказов дня, заказы ERP (кэш _dispatch_data) и отбор к доставке по правилу дня «чьи заказы везут машины»
    (№74, dp.fleet_rule_of) — без заказов, взятых в развоз дня их приёма (№72, _same_day_taken). ERP читается и за сам
    день: заказы, заведённые заранее на него (№79), — его заказы; прочие заказы с датой дня — новые заказы дня (№72,
    _same_day_data), из данных дня они убраны. later — «Երբ տանել» прошлых планов (_later_scan): ERP читается и с даты
    самого раннего их заказа (не старше DEFER_MAX_AGE_DAYS до дня плана, dp.later_carried), но из более старых заказов
    остаются только те, что dp.later_shown (перенесены сюда или позже, не довезены в свой день) — «Նախորդ օրերից» прежний."""
    workdays, off = bundle.settings['workdays'], dp.holidays_of(bundle.settings)
    since, until = dp.order_window(day, workdays, off)
    first = dp.backlog_since(since, workdays, holidays=off)
    later = later or {}
    data = _dispatch_data(state, min([first, *(od for _, od, _ in later.values())]), until + timedelta(days=1), day,
                          refresh)

    def target(to: date | None) -> dp.Draft | None:   # отправленный план дня доставки (прошедшего); не прочитан — нет
        if to is None or to >= day:
            return None
        try:
            return _sent_plan(state, to)
        except StoreError:
            return None
    data = replace(data, orders=tuple(o for o in data.orders if (o.order_date < until or o.predated) and (
        o.order_date >= first or (o.isn in later and dp.later_shown(later[o.isn], o, day, target(later[o.isn][0]))))))
    sel = dp.to_deliver(data.orders, day, since, rule, dp.place_of(data.customers, data.addresses))
    seen = _plan_seen(state, since)
    sel = dp.settle_predated(sel, since, seen if seen is not None else dp.PlanSeen())
    taken, unread = _same_day_taken(state, day, workdays, off)
    if unread or seen is None:   # план прошлого дня не прочитан — страница предупреждает о возможном повторе
        sel = replace(sel, same_day_unread=True)
    if taken & {o.isn for o in sel.main}:
        # взятые в развоз дня их приёма (№72) уже везли — даже если накладной ещё нет
        main = [o for o in sel.main if o.isn not in taken]
        sel = replace(sel, main=main, same_day_taken=len(sel.main) - len(main))
    return since, until, data, sel


def _stored_draft(state: RoutesState, day: date) -> tuple[dp.Draft | None, int]:
    stored = state.store.load_dispatch(day.isoformat())
    return (dp.Draft.from_json(stored[0]), stored[1]) if stored is not None else (None, 0)


def _backlog_in(draft: dp.Draft | None, carried: Collection[str]) -> set[str]:
    """Заказы прошлых дней в развозе дня: добавленные логистом + перенесённые сюда «Везти завтра»
    (carried), кроме тех, что логист этого дня из развоза убрал (dropped)."""
    if draft is None:
        return set(carried)
    return set(draft.added) | (set(carried) - draft.dropped)


def _active_orders(deliver: list[dp.DispatchOrder], backlog: list[dp.DispatchOrder],
                   draft: dp.Draft | None, carried: Collection[str], settings: Mapping[str, Any],
                   today: Sequence[dp.DispatchOrder] = ()) -> list[dp.DispatchOrder]:
    """Заказы в развозе: заказы дня без «не везём сегодня» + заказы прошлых дней в развозе (_backlog_in) + новые заказы
    дня today, взятые логистом в развоз сегодня (№72, draft.same_day); заказы менеджеров, снятых фильтром «Մենեջերներ»
    (dp.agents_off_of: без черновика — правило настроек, №69), — ни те, ни другие. Кроме заказа прошлых дней, который
    логист сам взял «Տանել այսօր» (dp.backlog_delivered, владелец 08.10): явный выбор сильнее фильтра."""
    excluded = draft.excluded if draft is not None else set()
    off = dp.agents_off_of(draft, settings)
    inside = _backlog_in(draft, carried)
    same = draft.same_day if draft is not None else set()
    return [o for o in deliver if o.isn not in excluded and o.agent_id not in off] \
        + [o for o in backlog if dp.backlog_delivered(o, inside, draft.added if draft is not None else (), off)] \
        + [o for o in today if o.isn in same and o.agent_id not in off]


def _carried(state: RoutesState, day: date, workdays: Sequence[int], backlog: list[dp.DispatchOrder],
             holidays: Collection[date] = (), later: Collection[str] = ()) -> set[str]:
    """Заказы, которые логист перенёс на этот день («Везти завтра», №25) в планах с прошлого рабочего дня
    по вчера (и из нерабочего дня между ними), и «Երբ տանել» на этот день (later, _later_due), которые ещё не отгружены.
    Битый черновик — без переносов."""
    out: set[str] = set()
    d = dp.previous_workday(day, workdays, holidays)
    while d < day:
        try:
            plan = _sent_plan(state, d)
        except StoreError:
            plan = None
        if plan is not None:
            out |= plan.deferred   # №81: «везти завтра», отправленное водителям
        d += timedelta(days=1)
    return (out | set(later)) & {o.isn for o in backlog}


def _plan_seen(state: RoutesState, day: date) -> dp.PlanSeen | None:
    """Что видел план дня day (№79, dp.settle_predated); плана нет — ничего: заказ, заведённый заранее на day, едет и
    на следующий рабочий день (не теряется); не прочитан — None (так же, и страница предупреждает)."""
    try:
        plan = _sent_plan(state, day)
    except StoreError:
        logger.warning('[Routes] План развоза на %s не прочитан — заказы, заведённые заранее на него, едут и дальше',
                       day, exc_info=True)
        return None
    return dp.PlanSeen.of(plan)   # №81: видел — отправленный план


def _same_day_taken(state: RoutesState, day: date, workdays: Sequence[int],
                    holidays: Collection[date] = ()) -> tuple[set[str], bool]:
    """Заказы, которые логист взял в развоз дня их приёма (№72, Draft.same_day) в планах с прошлого рабочего дня по вчера
    (и нерабочего дня между ними): их уже везли — этот день их не везёт, даже без накладной. (заказы, не прочитан ли
    какой-то из этих планов — тогда его взятые заказы могут попасть сюда повторно: страница предупреждает)."""
    out: set[str] = set()
    unread = False
    d = dp.previous_workday(day, workdays, holidays)
    while d < day:
        try:
            plan = _sent_plan(state, d)
        except StoreError:
            logger.warning('[Routes] План развоза на %s не прочитан — взятые в его развоз заказы дня не исключены из %s',
                           d, day, exc_info=True)
            plan, unread = None, True
        if plan is not None:
            out |= plan.same_day   # №81: взяли в развоз только отправленные
        d += timedelta(days=1)
    return out, unread


def _defer_target(day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> tuple[date, date]:
    """(день доставки, куда «Везти завтра», самая ранняя дата заказа, которую он ещё видит)."""
    target = dp.next_workday(day, workdays, holidays)
    since, _ = dp.order_window(target, workdays, holidays)
    return target, dp.backlog_since(since, workdays, holidays=holidays)





def _orders_sig(data: dp.DispatchData) -> str:
    """Отпечаток заказов ERP: изменился — странице есть что перечитать."""
    raw = repr(sorted((o.isn, o.customer_id, o.agent_id, o.van_agent_id, round(o.kg, 3), round(o.revenue, 2),
                       o.shipped) for o in data.orders))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def _freshness(day: date, bundle: Bundle, data: dp.DispatchData, deliver: list[dp.DispatchOrder],
               backlog: list[dp.DispatchOrder], draft: dp.Draft | None,
               carried: Collection[str] = (), ruled_out: Collection[str] = ()) -> dict[str, Any]:
    """«Заказы ещё поступают» и что изменилось с последней сборки — для подсказки вверху страницы.
    Перенесённые сюда из прошлого дня — как заказы дня: появился перенос — «новые», сняли — «убраны». ruled_out —
    заказы, которые вывело правило дня (№74; «никогда» у «×» дописывает его и в уже собранные дни): не «отменили или
    доставили»."""
    s = bundle.settings
    changes = None
    if draft is not None:
        inside = _backlog_in(draft, carried)
        moved_in = [o for o in backlog if o.isn in set(carried) - draft.dropped]
        built = draft.built_orders
        if built is not None and ruled_out:
            built = {k: v for k, v in built.items() if k not in set(ruled_out)}
        # заказы менеджеров, снятых фильтром, — не «новые»: их сегодня не везём
        changes = dp.since_build(built, [o for o in (*deliver, *moved_in) if o.agent_id not in draft.agents_off],
                                 [*deliver, *(o for o in backlog if o.isn in inside)], draft.excluded)
    return {
        'orders_still_coming': dp.orders_still_coming(day, s['workdays'], _clock(), s['dispatch_ready_time'],
                                                      dp.holidays_of(s)),
        'ready_time': s['dispatch_ready_time'],
        'built_at': draft.built_at if draft is not None else None,
        'new_since_build': changes['new'] if changes else None,
        'removed_since_build': changes['removed'] if changes else None,
        'orders_sig': _orders_sig(data),
        'data_as_of': data.loaded_at.isoformat(timespec='seconds'),
    }


def _load_day(state: RoutesState, bundle: Bundle, day: date, refresh: bool = False,
              draft: dp.Draft | None = None, rev: int | None = None) -> _DispatchDay:
    snap, _ = state.snapshots.cached()          # до цены журнала: модели машин — по именам снимка
    bundle = _with_garage(state, bundle, day)   # ремонт ֏/км журнала гаража — на день развоза
    if draft is None:
        draft, rev = _stored_draft(state, day)
    # правило №74 — то, с которым день собран (черновик), без плана — из настроек
    rule = dp.fleet_rule_of(draft, bundle.settings)
    later = _later_scan(state, day, bundle.settings['workdays'], dp.holidays_of(bundle.settings))
    due = _later_due(later, day)
    since, until, data, sel = _day_orders(state, bundle, day, refresh, rule, later)
    deliver, backlog = sel.main, sel.backlog
    carried = _carried(state, day, bundle.settings['workdays'], backlog, dp.holidays_of(bundle.settings), due)
    # новые заказы дня (№72): сегодня — кандидаты; другой день — только если в нём есть взятые (их точки в плане). Взятых
    # нет — ERP не ответила: день без подсказки о новых заказах (план от них не зависит); есть — ошибка, как у заказов дня
    taken = draft is not None and bool(draft.same_day)
    same_data = None
    if taken or day == _same_day_now().date():
        try:
            same_data = _same_day_data(state, day, refresh)
        except ErpError:
            if taken:
                raise
            logger.warning('[Routes] Новые заказы дня %s не прочитаны — без подсказки', day, exc_info=True)
    today = dp.same_day_candidates(same_data.orders, day, rule, dp.place_of(same_data.customers, same_data.addresses)) \
        if same_data is not None else []
    if same_data is not None:   # клиенты новых заказов — в справочниках дня (данные окна дня главнее)
        data = replace(data, customers={**same_data.customers, **data.customers},
                       addresses={**same_data.addresses, **data.addresses})
    coords: dict[int, Any] = {}

    def coord(cid: int) -> Any:
        if cid not in coords:
            coords[cid] = evaluate.visit_coord(snap, cid, 0, bundle.geo_overrides, bundle.driver_points)
        return coords[cid]

    stops = dp.build_stops(_active_orders(deliver, backlog, draft, carried, bundle.settings, today), coord)
    ready = _ready_trucks(snap, bundle)
    ctx = _dispatch_ctx(state, snap, bundle, day, ready, [s.point for s in stops if s.point is not None],
                        {s.customer_id: s.point for s in stops if s.point is not None})
    return _DispatchDay(day, since, until, data, deliver, backlog, sel.shipped_before, sel.self_delivery, draft,
                        rev or 0, stops, ctx, ready, snap, bundle, carried, today, same_data, sel.same_day_taken,
                        sel.same_day_unread, other_vehicle=sel.other_vehicle, customers_off=sel.customers_off,
                        later=later)


def _day_stops(dd: _DispatchDay, draft: dp.Draft) -> list[dp.Stop]:
    """Точки развоза дня для черновика draft (его «не везём сегодня» и добавленные заказы)."""
    return dp.build_stops(_active_orders(dd.deliver, dd.backlog, draft, dd.carried, dd.bundle.settings, dd.same_day),
                          lambda cid: evaluate.visit_coord(dd.snap, cid, 0, dd.bundle.geo_overrides,
                                                           dd.bundle.driver_points))


def _stop_info(dd: _DispatchDay) -> Callable[[dp.Stop], dict[str, Any]]:
    agents = dd.snap.agents
    windows = dd.bundle.windows_on(dd.day)   # окно, которое действует в этот день («Մինչև ժամը» дня — вместо постоянного)
    until = dd.bundle.day_until.get(dd.day, {})

    def info(s: dp.Stop) -> dict[str, Any]:
        code, name = dd.data.customers.get(s.customer_id) or (
            (dd.snap.customers[s.customer_id].code, dd.snap.customers[s.customer_id].name)
            if s.customer_id in dd.snap.customers else (str(s.customer_id), ''))
        agent = agents.get(s.agent_id)
        return {
            'customer_id': s.customer_id, 'code': code, 'name': name,
            'address': dd.data.addresses.get(s.customer_id, ''),
            'lat': round(s.point[0], 6) if s.point else None, 'lon': round(s.point[1], 6) if s.point else None,
            'coord_source': s.coord_source, 'revenue': round(s.revenue),
            'agent_id': s.agent_id, 'agent_code': agent.code if agent else '', 'agent_name': agent.name if agent else '',
            'orders': [{'isn': o.isn, 'doc_num': o.doc_num, 'kg': round(o.kg), 'revenue': round(o.revenue),
                        'order_date': o.order_date.isoformat()} for o in s.orders],
            'window': windows[s.customer_id].to_json() if s.customer_id in windows else None,
            'vehicle_access': dd.bundle.vehicle_access[s.customer_id].to_json()
                              if s.customer_id in dd.bundle.vehicle_access else None,
            # срок «Մինչև ժամը» только этого дня, минуты от полуночи (window — уже с ним)
            **({'until_day': until[s.customer_id]} if s.customer_id in until else {}),
        }
    return info


def _dispatch_body(dd: _DispatchDay) -> dict[str, Any]:
    s = dd.bundle.settings
    holidays = dp.holidays_of(s)
    today = _clock().date()
    info = _stop_info(dd)
    draft = dd.draft
    excluded = draft.excluded if draft is not None else set()
    # отмеченные: машины дня и отмеченные, которые сегодня без водителя (№77, unmanned — почему; имён здесь нет)
    selected = set(draft.trucks) | set(draft.unmanned) if draft is not None else set(dd.ready)
    unmanned = draft.unmanned if draft is not None else {}
    problems = []
    if dd.bundle.depot is None:
        problems.append({'code': 'no_depot', 'text': 'Укажите склад — откуда выезжают машины',
                         'link': SETTINGS_TRUCKS_URL.replace('#trucks', '#depot')})
    if not dd.ready:
        problems.append({'code': 'no_trucks', 'text': 'Укажите тоннаж и расход машин', 'link': SETTINGS_TRUCKS_URL})
    trucks = []
    manual = {code: t for code, t in dd.bundle.trucks.items() if t.manual}
    names = {code: car.name for code, car in dd.snap.cars.items() if code not in manual}
    names.update({code: t.name for code, t in manual.items()})
    for code in sorted(names):
        ready = dd.ready.get(code)
        # не настроена или не работает — в списке не нужна
        if ready is None and (code not in dd.bundle.trucks or not dd.bundle.truck_active(code, dd.snap.active_cars)):
            continue
        trucks.append({'car_code': code, 'name': names[code], 'manual': code in manual,
                       'capacity_kg': ready.capacity_kg if ready else None,
                       'l100': ready.l100 if ready else None, 'center_ok': ready.center_ok if ready else None,
                       # большая машина (№68) — только при заданной зоне Еревана
                       **({'big': True} if dd.ctx is not None and dp.big_shown(dd.ctx, ready) else {}),
                       'ready': ready is not None, 'selected': ready is not None and code in selected,
                       **({'unmanned': unmanned[code]} if code in unmanned else {}),
                       # износ в расчёте дня: garage — ремонт ֏/км журнала гаража, manual — из настроек, garage_avg —
                       # средняя модели или парка по журналу (ручное пусто; №53)
                       'wear_amd_per_km': ready.wear_amd_per_km if ready else None,
                       'wear_source': dd.bundle.wear_source(code)})
    added = _backlog_in(draft, dd.carried)
    no_coords = [s for s in dd.stops if s.point is None]
    active = _active_orders(dd.deliver, dd.backlog, draft, dd.carried, s, dd.same_day)
    excl = [o for o in dd.deliver if o.isn in excluded]
    off = dp.agents_off_of(draft, s)
    # фильтр «Մենեջերներ»: менеджеры заказов развоза дня (заказы дня без «не везём сегодня», прошлых дней в развозе и
    # новые заказы дня, взятые в него, №72; и снятые фильтром — менеджер в списке, пока у него есть такие заказы) и
    # менеджеры новых заказов дня, которые ещё решать (ревью №72 L2: у кого только они, тоже можно снять или вернуть) —
    # их число и вес отдельно (same_day), в заказы развоза не входят
    taken = draft.same_day if draft is not None else set()
    picked = draft.added if draft is not None else set()   # взятые «Տանել այսօր» везём и при снятом менеджере
    by_agent: dict[int, list[dp.DispatchOrder]] = {}
    # снятому менеджеру взятые «Տանել այսօր» не в счёт: их везём при любом фильтре
    for o in [*(o for o in dd.deliver if o.isn not in excluded),
              *(o for o in dd.backlog if o.isn in added and not (o.agent_id in off and o.isn in picked)),
              *(o for o in dd.same_day if o.isn in taken)]:
        by_agent.setdefault(o.agent_id, []).append(o)
    pending: dict[int, list[dp.DispatchOrder]] = {}
    if dd.day == _same_day_now().date():
        for o in dd.same_day:
            if o.isn not in taken and o.isn not in excluded:
                pending.setdefault(o.agent_id, []).append(o)
    agents_json = []
    for aid in [*by_agent, *(a for a in pending if a not in by_agent)]:
        orders = by_agent.get(aid, [])
        agent = dd.snap.agents.get(aid)
        agents_json.append({'agent_id': aid, 'code': agent.code if agent else '', 'name': agent.name if agent else '',
                            'area': agent.area if agent else '',
                            'count': len(orders), 'kg': round(sum(o.kg for o in orders)),
                            'revenue': round(sum(o.revenue for o in orders)), 'off': aid in off,
                            **({'same_day': {'count': len(pending[aid]), 'kg': round(sum(o.kg for o in pending[aid]))}}
                               if aid in pending else {})})
    agents_json.sort(key=lambda a: (a['name'] or a['code'] or '~', a['agent_id']))
    hidden = [o for os_ in (by_agent.get(a, []) for a in off) for o in os_]

    rule = dp.fleet_rule_of(draft, s)
    place = dp.place_of(dd.data.customers, dd.data.addresses)
    differ: dict[str, bool] = {}
    # прошедшему дню «Կիրառել կարգավորումները» не предлагаем — сервер его отклонит (PAST_DAY_SETTINGS)
    if draft is not None and dd.day >= _same_day_now().date():
        differ = {k: v for k, v in (('agents', draft.agents_off != set(s['dispatch_agents_off'])),
                                    ('fleet', not rule.same_as(dp.FleetRule.from_settings(s)))) if v}

    def order_json(o: dp.DispatchOrder) -> dict[str, Any]:
        code, name = dd.data.customers.get(o.customer_id) or ('', '')
        # «Երբ տանել»: когда повезём — по плану этого дня или (ещё не тот день и этот день его не взял и не перенёс) по
        # плану прошлого (later_from — какого); перенесён сюда — откуда
        mine = draft.later.get(o.isn) if draft is not None else None
        prev = dd.later.get(o.isn)
        decided = draft is not None and (o.isn in draft.added or o.isn in draft.deferred)
        later_to = later_from = None
        if mine is not None:
            later_to = dp.later_day(mine[0], s['workdays'], holidays)
        elif prev is not None and prev[0] is not None and prev[0] > dd.day and not decided:
            later_to, later_from = prev[0], prev[2]
        return {'isn': o.isn, 'doc_num': o.doc_num, 'customer_id': o.customer_id, 'code': code, 'name': name,
                'order_date': o.order_date.isoformat(), 'kg': round(o.kg), 'revenue': round(o.revenue),
                'added': o.isn in added, 'deferred': draft is not None and o.isn in draft.deferred,
                'carried': o.isn in dd.carried, 'agent_off': o.agent_id in off,
                **({'later_to': later_to.isoformat()} if later_to is not None else {}),
                **({'later_from': later_from.isoformat()} if later_from is not None else {}),
                **({'carried_from': prev[2].isoformat()}
                   if prev is not None and prev[0] == dd.day and o.isn in dd.carried else {})}

    # рейс магазина; тяжёлый магазин в нескольких рейсах — первый из них
    trip_of = {c: t for t in reversed(draft.trips if draft is not None else ()) for c in t.stops}

    def backlog_json(o: dp.DispatchOrder) -> dict[str, Any]:
        """Заказ прошлых дней (владелец 08.10): + менеджер, в развозе ли он (taken — как _active_orders: с фильтром
        менеджеров), «Չտանել» (dismissed) и рейс, где стоит его магазин."""
        agent = dd.snap.agents.get(o.agent_id)
        trip = trip_of.get(o.customer_id)
        return order_json(o) | {'agent_name': agent.name if agent else '',
                                'taken': dp.backlog_delivered(o, added, picked, off),
                                'dismissed': draft is not None and o.isn in draft.dismissed,
                                'trip': {'id': trip.id, 'truck': trip.truck} if trip is not None else None}

    body: dict[str, Any] = {
        'day': dd.day.isoformat(), 'weekday': dd.day.isoweekday(),
        'today': today.isoformat(), 'is_past': dd.day < today,
        'default_day': dp.next_workday(today, s['workdays'], holidays).isoformat(),
        'day_off': not dp.is_workday(dd.day, s['workdays'], holidays),
        'order_dates': {'since': dd.since.isoformat(), 'until': (dd.until - timedelta(days=1)).isoformat()},
        'work_start': s['truck_work_start'], 'work_end': s['truck_work_end'],
        'overtime_end': s['truck_overtime_end'],
        'depot': {'lat': dd.bundle.depot[0], 'lon': dd.bundle.depot[1]} if dd.bundle.depot else None,
        'problems': problems, 'trucks': trucks, 'rev': dd.rev,
        'vehicle_options': [{'car_code': code, 'name': name} for code, name in sorted(names.items())],
        'orders': {'count': len(active), 'kg': round(sum(o.kg for o in active)),
                   'revenue': round(sum(o.revenue for o in active)), 'customers': len(dd.stops),
                   'shipped_before': dd.shipped_before, 'excluded': len(excl),
                   'agents_off': len(hidden), 'agents_off_kg': round(sum(o.kg for o in hidden)),
                   'self_delivery': len(dd.self_delivery),
                   'self_delivery_kg': round(sum(o.kg for o in dd.self_delivery)),
                   # №74: везут другие машины (менеджер правила, город-исключение) и клиенты «машины не везут» — как
                   # «везёт сам», вне развоза; правила нет — ключей нет (ответ — прежний до байта)
                   **({'other_vehicle': len(dd.other_vehicle),
                       'other_vehicle_kg': round(sum(o.kg for o in dd.other_vehicle))} if dd.other_vehicle else {}),
                   **({'customers_off': len(dd.customers_off),
                       'customers_off_kg': round(sum(o.kg for o in dd.customers_off))} if dd.customers_off else {}),
                   'no_coords': len(no_coords), 'no_coords_kg': round(sum(x.kg for x in no_coords)),
                   # взяты в развоз дня их приёма (№72) — сегодня их не везём
                   **({'same_day_taken': dd.same_day_taken} if dd.same_day_taken else {})},
        'stops_no_coords': [info(x) | {'kg': round(x.kg)} for x in no_coords],
        'excluded': [order_json(o) for o in excl],
        # №74: заказы, которые везут другие машины (менеджер правила, город-исключение), — с менеджером и городом
        **({'other_vehicle': [order_json(o) | {'city': rule.matched_city(place(o.customer_id)),
                                               'agent_code': agent.code if (agent := dd.snap.agents.get(o.agent_id)) else '',
                                               'agent_name': agent.name if agent else ''}
                              for o in dd.other_vehicle]} if dd.other_vehicle else {}),
        # день с планом выбран не по нынешним настройкам (№69, №74): страница предлагает «Կիրառել կարգավորումները»
        **({'settings_differ': differ} if differ else {}),
        'agents': agents_json, 'agents_off': sorted(off),
        # у дня ещё нет плана, а правило настроек кого-то снимает (№69): выбор выше — из настроек
        **({'agents_from_settings': True} if draft is None and off else {}),
        # не отгружены с прошлых дней: в план — только добавленные логистом (added)
        'backlog': [backlog_json(o) for o in dd.backlog],
        'backlog_since': dp.backlog_since(dd.since, s['workdays'], holidays=holidays).isoformat(),
        'plan': None,
        'overtime': draft.overtime if draft is not None else False,
        'overtime_ok': draft.overtime_ok if draft is not None else False,
        'min_trip_revenue': s['min_trip_revenue'],
        'defer_to': _defer_target(dd.day, s['workdays'], holidays)[0].isoformat(),
        # «Երբ տանել» (владелец 08.10): куда «Այսօր չենք տանում» может перенести заказы магазина; прошедший день — никуда
        'defer_days': [] if dd.day < today else [d.isoformat() for d in dp.defer_days(dd.day, s['workdays'], holidays)],
        'overtime_days_month': _overtime_days(_state().store, dd.day),
        'geo_suggestions': _geo_suggestions(_state(), dd),
        # №73: план дня утверждён — когда (кем — только странице: _dispatch_page_body)
        **({'approved': {'at': draft.approved['at']}} if draft is not None and draft.approved is not None else {}),
        # №72: план прошлого дня не прочитан — заказы, взятые в его развоз, могли попасть и сюда
        **({'same_day_unread': True} if dd.same_day_unread else {}),
        **_freshness(dd.day, dd.bundle, dd.data, dd.deliver, dd.backlog, draft, dd.carried,
                     {o.isn for o in (*dd.customers_off, *dd.other_vehicle)}),
    }
    if draft is not None and dd.ctx is not None:
        plan = dp.plan_view(dd.ctx, dd.stops, draft, info, crew=_crew(_state(), dd.day, dd.bundle.trucks)[0])
        # пояснение дня: заказы, перенесённые сюда «Везти завтра» прошлого дня, и отсюда — на следующий день
        plan['explain'].update(carried=len(dd.carried & {o.isn for o in active}), deferred=len(draft.deferred))
        plan['built_at'] = draft.built_at
        plan['baseline'] = dp.baseline(dd.ctx, dd.stops, draft,
                                       dp.history_cars(dd.data.agent_cars, dd.bundle.van_trucks()))
        body['plan'] = plan
    return body


def _dispatch_page_body(dd: _DispatchDay) -> dict[str, Any]:
    """Ответ дня для страницы: _dispatch_body и store_unload — своё время у магазинов дня, где оно задано (№50; клиент →
    мин, плашка у точки; у остальных — норма). Отдельно от plan (unload_min точки рейса — разгрузка, посчитанная планом)
    и не в данных «Հարցրու AI-ին»: /ask берёт _dispatch_body, а ai_chat убирает customer_id у точек — номера клиентов
    модели ничего бы не сказали. Водители (№62, _drivers_json; №77, _crew_json) — тоже только странице: имена людей модели не
    отправляются."""
    return {**_dispatch_body(dd), 'store_unload': {st.customer_id: dd.bundle.unload_min[st.customer_id]
                                                   for st in dd.stops if st.customer_id in dd.bundle.unload_min},
            **_drivers_json(_state(), dd.day), **_crew_json(_state(), dd.day, dd.draft, dd.bundle.trucks), **_same_day_json(dd),
            # №73: кто утвердил план — имя человека, только странице
            **({'approved': {'at': dd.draft.approved['at'], 'by': dd.draft.approved['by']}}
               if dd.draft is not None and dd.draft.approved is not None else {}),
            # №78: кто отметил «Բեռնված է» — логин, только странице (рейс → кто)
            **({'loaded_by': {str(t.id): t.loaded['by'] for t in dd.draft.trips if t.loaded is not None}}
               if dd.draft is not None and any(t.loaded is not None for t in dd.draft.trips) else {}),
            # №80: план выпущен на терминалы — «Ջնջել երթերը» не показывается (сервер и так откажет)
            **({'released': True} if dd.draft is not None and dd.draft.released is not None else {}),
            # №81: когда план ушёл водителям (кто — имя человека, только странице) и что с тех пор не отправлено
            **({'sent': {'at': dd.draft.sent['at'], 'by': dd.draft.sent['by']}, 'unsent': _unsent_json(dd)}
               if dd.draft is not None and dd.draft.sent is not None else {})}


def _unsent_json(dd: _DispatchDay) -> dict[str, Any] | None:
    """Неотправленные правки (№81, dp.unsent) и on_road — машины из них, чей рейс по отправленному плану уже грузится или
    в пути (сегодня): водитель увидит правку в дороге — страница предупреждает перед «Ուղարկել»."""
    out = dp.unsent(dd.draft)
    if out is None:
        return None
    now = _same_day_now()
    road: set[str] = set()
    if dd.ctx is not None and dd.day == now.date() and out['trucks']:
        sent = dd.draft.for_drivers()
        started = dp.started_trips(dd.ctx, dd.stops, sent, _now_min(dd.ctx, now))
        road = {t.truck for t in sent.trips if t.id in started}
    return {**out, 'on_road': sorted(road & set(out['trucks']))}


# --- Новые заказы дня (ответ владельца №72) ---

SAME_DAY_MAX_ORDERS = 500   # заказов в одном запросе «Առաջարկներ» / «Ընտրել» — защита от битого запроса


def _same_day_open(orders: Sequence[dp.DispatchOrder], draft: dp.Draft | None,
                   settings: Mapping[str, Any]) -> list[dp.DispatchOrder]:
    """Новые заказы дня, которые ещё решать: не взятые сегодня, не «не везём сегодня», не менеджеров, снятых фильтром
    (dp.agents_off_of: у дня без плана — правило настроек, №69)."""
    off = dp.agents_off_of(draft, settings)
    if draft is None:
        return [o for o in orders if o.agent_id not in off]
    return [o for o in orders if o.isn not in draft.same_day and o.isn not in draft.excluded and o.agent_id not in off]


def _totals_of(orders: Sequence[dp.DispatchOrder]) -> dict[str, Any]:
    return {'count': len(orders), 'kg': round(sum(o.kg for o in orders)), 'revenue': round(sum(o.revenue for o in orders))}


def _same_day_summary(orders: Sequence[dp.DispatchOrder]) -> dict[str, Any]:
    """Плашка новых заказов дня: count, kg, revenue — новые без накладной (их решает логист); invoiced — с накладной на
    сегодня (офис уже решил, что везут сегодня: их надо поставить в рейс, на завтра они не останутся — для D+1 они
    «отгружены раньше»); sig — отпечаток всех: изменился — странице есть что показать."""
    raw = repr(sorted((o.isn, round(o.kg, 3), round(o.revenue, 2), o.shipped) for o in orders))
    return {**_totals_of([o for o in orders if o.shipped is None]),
            'invoiced': _totals_of([o for o in orders if o.shipped is not None]),
            'sig': hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}


def _today_min(dd: _DispatchDay) -> float | None:
    """Сейчас (минуты от начала дня машины), если день — сегодняшний по Еревану; иначе None (правки без «уже грузится»)."""
    now = _same_day_now()
    return _now_min(dd.ctx, now) if dd.ctx is not None and dd.day == now.date() else None


def _now_min(ctx: dp.DayContext, now: datetime) -> float:
    """Сейчас — минуты от начала дня машины."""
    return now.hour * 60 + now.minute + now.second / 60.0 - ctx.work_start_min


def _same_day_json(dd: _DispatchDay) -> dict[str, Any]:
    """Новые заказы дня для страницы (№72): сегодня — сколько их ещё решать (count, kg, revenue, sig — плашка вверху) и
    список: магазин, менеджер, кг, ֏, когда завели (ERP, created — «ЧЧ:ММ», заведён в другой день — «ДД.ММ ЧЧ:ММ»), взят ли
    сегодня и какими машинами, рейс магазина уже грузится (started: взятый — не вернуть на завтра, новый — только завтра),
    есть ли уже накладная (invoiced:
    офис сам решил — везут сегодня), нет точки (no_coords). Другой день — только взятые. Данных нет — пусто (ответ —
    прежний до байта)."""
    sd = dd.same_day_data
    if sd is None:
        return {}
    draft = dd.draft
    now = _same_day_now()
    is_today = dd.day == now.date()
    open_ = _same_day_open(dd.same_day, draft, dd.bundle.settings) if is_today else []
    taken = [o for o in dd.same_day if draft is not None and o.isn in draft.same_day]
    trucks: dict[int, set[str]] = {}
    for t in (draft.trips if draft is not None else ()):
        for c in t.stops:
            trucks.setdefault(c, set()).add(t.truck)
    agents = dd.snap.agents
    point = {s.customer_id: s.point for s in dd.stops}
    moving: set[int] = set()   # клиенты в рейсах, чья загрузка уже началась
    if is_today and draft is not None and dd.ctx is not None:
        moving = dp.started_customers(dd.ctx, dd.stops, draft, _now_min(dd.ctx, now))

    def item(o: dp.DispatchOrder, took: bool) -> dict[str, Any]:
        code, name = dd.data.customers.get(o.customer_id) or ('', '')
        agent = agents.get(o.agent_id)
        at = sd.created.get(o.isn)
        p = point[o.customer_id] if o.customer_id in point else evaluate.visit_coord(
            dd.snap, o.customer_id, 0, dd.bundle.geo_overrides, dd.bundle.driver_points).point
        return {'isn': o.isn, 'doc_num': o.doc_num, 'customer_id': o.customer_id, 'code': code, 'name': name,
                'address': dd.data.addresses.get(o.customer_id, ''), 'agent_id': o.agent_id,
                'agent_name': agent.name if agent else '', 'agent_code': agent.code if agent else '',
                'kg': round(o.kg), 'revenue': round(o.revenue),
                'created': None if at is None else at.strftime('%H:%M' if at.date() == dd.day else '%d.%m %H:%M'),
                'taken': took, 'trucks': sorted(trucks.get(o.customer_id, ())) if took else [],
                'started': o.customer_id in moving,
                'invoiced': o.shipped is not None, 'no_coords': p is None}

    return {'same_day': {'today': is_today, 'now': now.strftime('%H:%M'), **_same_day_summary(open_),
                         'taken': _totals_of(taken),
                         'orders': [item(o, False) for o in open_] + [item(o, True) for o in taken]}}


def _same_day_pick(state: RoutesState, bundle: Bundle, dd: _DispatchDay, raw: Any
                   ) -> tuple[_DispatchDay, set[str], set[int], float, list[dict[str, Any]]]:
    """Новые заказы дня из запроса (fISN) — что считать: (день со взятыми из них в развозе — точки и контекст расчёта с их
    точками, заказы, которые можно взять, их клиенты, «сейчас» — минуты от начала дня машины, остальные — клиент, почему
    (dispatch.same_day_blocked: нет точки, рейс клиента уже грузится) и их заказы: они остаются на завтра). Не сегодня,
    битый список или заказ уже не новый — DispatchError (текст — логисту)."""
    now = _same_day_now()
    if dd.day != now.date():
        raise dp.DispatchError('Նոր պատվերները կարելի է վերցնել միայն այսօրվա առաքման մեջ')
    if not isinstance(raw, list) or not raw or len(raw) > SAME_DAY_MAX_ORDERS \
            or not all(isinstance(x, str) and dp.ISN_RE.match(x.upper()) for x in raw):
        raise dp.DispatchError('Պատվերների ցուցակը չընդունվեց — թարմացրեք էջը')
    isns = {x.upper() for x in raw}
    picked = [o for o in _same_day_open(dd.same_day, dd.draft, bundle.settings) if o.isn in isns]
    if len(picked) != len(isns):
        raise dp.DispatchError('Պատվերներից մեկն այլևս նոր չէ կամ արդեն վերցված է — թարմացրեք էջը')
    def load(take: Collection[str]) -> _DispatchDay:
        trial = dp.Draft.from_json(dd.draft.to_json())
        trial.same_day |= set(take)
        return _load_day(state, bundle, dd.day, draft=trial, rev=dd.rev)

    with_new = load(isns)
    now_min = _now_min(with_new.ctx, now)
    blocked = dp.same_day_blocked(with_new.ctx, dd.stops, with_new.stops, dd.draft,
                                  {o.customer_id for o in picked}, now_min)
    if blocked:
        picked = [o for o in picked if o.customer_id not in blocked]
        isns = {o.isn for o in picked}
        if picked:   # точки и контекст — без заказов, которых не взять
            with_new = load(isns)
    out = [{'customer_id': c, 'reason': r, 'orders': sorted(o.isn for o in dd.same_day if o.customer_id == c
                                                             and o.isn in {x.upper() for x in raw})}
           for c, r in sorted(blocked.items())]
    return with_new, isns, {o.customer_id for o in picked}, now_min, out


def _same_day_edit(state: RoutesState, bundle: Bundle, dd: _DispatchDay, payload: Mapping[str, Any]) -> dp.Draft:
    """Правка новых заказов дня (№72): same_day — взять заказы orders вариантом option (dispatch.take_same_day);
    same_day_drop — вернуть взятые orders в развоз следующего дня; exclude взятого заказа — то же. Только сегодня."""
    action = payload.get('action')
    if action == 'same_day':
        with_new, isns, cids, now_min, blocked = _same_day_pick(state, bundle, dd, payload.get('orders'))
        if blocked:   # страница шлёт только те, что можно взять: значит, рейс клиента начал грузиться после расчёта
            raise dp.DispatchError('Այս պատվերներից մեկի մեքենան արդեն բեռնվում է կամ խանութի տեղը քարտեզում չկա — '
                                   'թարմացրեք առաջարկները')
        return dp.take_same_day(with_new.ctx, dd.stops, with_new.stops, dd.draft, cids, isns, payload.get('option'),
                                now_min, _crew(state, dd.day, bundle.trucks)[0])
    now = _same_day_now()
    if dd.day != now.date():
        raise dp.DispatchError('Նոր պատվերները կարելի է փոխել միայն այսօրվա առաքման մեջ')
    raw = [payload.get('order')] if action == 'exclude' else payload.get('orders')
    if not isinstance(raw, list) or len(raw) > SAME_DAY_MAX_ORDERS or not all(isinstance(x, str) for x in raw):
        raise dp.DispatchError('Պատվերների ցուցակը չընդունվեց — թարմացրեք էջը')
    isns = {x.upper() for x in raw}
    # рейс, чья загрузка началась, уже везёт заказ: вернуть его на завтра — значит отвезти дважды
    cids = {o.customer_id for o in dd.same_day if o.isn in isns}
    if cids & dp.started_customers(dd.ctx, dd.stops, dd.draft, _now_min(dd.ctx, now)):
        raise dp.DispatchError('Մեքենան արդեն բեռնվում է կամ ճանապարհին է՝ այս պատվերով — այն այսօր է գնում, '
                               'վաղվան թողնել չի կարելի')
    return dp.drop_same_day(dd.draft, isns)


PLAN_LOADED = 'Պլանում կան բեռնված երթեր — այն չի ջնջվում։ Նախ հանեք «Բեռնված է» նշումները։'
LOADED_TRUCK_OFF = dp.LOADED_TRUCK_OFF   # №78: + коды машин
PLAN_APPROVED = 'Պլանը հաստատված է։ Ամբողջական վերակազմման համար նախ չեղարկեք հաստատումը։'   # №73: пересборка утверждённого плана
# №80: выпущенный план (утверждён хотя бы раз) не стирается — водители уже везут его точки; пересборка — можно
PLAN_RELEASED = 'Պլանն արդեն ուղարկված է վարորդներին․ ամբողջը նորից կազմելու համար օգտագործեք «Վերակազմել երթերը»։'
PAST_DAY_APPROVE = 'Անցած օրվա պլանը չի հաստատվում և չի չեղարկվում'


def _approve_edit(dd: _DispatchDay, payload: Mapping[str, Any]) -> dp.Draft:
    """«Հաստատել օրվա պլանը» / «Չեղարկել հաստատումը» (№73), «Ուղարկել վարորդներին» (№81 — сам снимок делает
    api_dispatch_edit последним шагом, dp.send): прошедший день — DispatchError."""
    if dd.day < _same_day_now().date():
        raise dp.DispatchError(PAST_DAY_APPROVE)
    if payload.get('action') == 'send':
        if dd.draft.released is None:
            raise dp.DispatchError('Նախ հաստատեք օրվա պլանը')
        return dd.draft
    if payload.get('action') == 'discard':   # №81: назад к отправленному водителям плану
        return dp.discard(dd.draft)
    if payload.get('action') == 'approve':
        # время утверждения — по Еревану, как «сегодня» и «сейчас» новых заказов дня
        return dp.approve(dd.draft, _same_day_now().isoformat(timespec='seconds'), session.get('username'))
    return dp.unapprove(dd.draft)


def _loaded_confirm() -> Any:
    """Ответ «товар уже в машине» (№78): страница спрашивает логиста и повторяет правку с confirm_loaded."""
    return jsonify({'success': False, 'error': dp.LOADED_EDIT, 'errors': {'_': dp.LOADED_EDIT}, 'loaded_confirm': True}), 400


def _loaded_edit(dd: _DispatchDay, payload: Mapping[str, Any]) -> dp.Draft:
    """«Բեռնված է» / «Հանել բեռնված նշումը» логистом на «Развозе» (№78): отметить — только по утверждённому плану
    (dispatch.mark_loaded), снять — без срока (склад снимает только свою и недолго, api_warehouse_loaded)."""
    if payload.get('action') == 'loaded':
        return dp.mark_loaded(dd.draft, payload.get('trip'), _same_day_now().isoformat(timespec='seconds'),
                              session.get('username'))
    return dp.unmark_loaded(dd.draft, payload.get('trip'))


SETTINGS_ON_STARTED = ('Մեքենան արդեն բեռնվում է կամ ճանապարհին է այն խանութների պատվերներով, որոնք կարգավորումները '
                       'կհանեին։ Այդ պատվերներն այսօր գնում են. կարգավորումները կկիրառվեն հաջորդ օրերին։')
PAST_DAY_SETTINGS = 'Անցած օրվա պլանին կարգավորումները չեն կիրառվում'


def _apply_settings_edit(state: RoutesState, bundle: Bundle, dd: _DispatchDay) -> dp.Draft:
    """«Կիրառել կարգավորումները» (№74, ревью M3): день с планом — по нынешним правилам настроек (менеджеры №69, «чьи
    заказы везут машины» №74) — ручная правка, утверждённый план тоже. Прошедший день — нельзя; рейс, чья загрузка уже
    началась, своих заказов не теряет — иначе отказ (DispatchError). Заказы, которые новое правило вывело из развоза, —
    не «убраны после сборки»: из отметки сборки их нет."""
    if dd.day < _same_day_now().date():
        raise dp.DispatchError(PAST_DAY_SETTINGS)
    s = bundle.settings
    new = dp.Draft.from_json(dd.draft.to_json())
    new.agents_off = set(s['dispatch_agents_off'])
    new.fleet = dp.FleetRule.from_settings(s).to_json()
    new.undo = None
    after = _load_day(state, bundle, dd.day, draft=new, rev=dd.rev)
    now = _same_day_now()
    if dd.ctx is not None and dd.day == now.date():
        moving = dp.started_customers(dd.ctx, dd.stops, dd.draft, _now_min(dd.ctx, now))
        before = {o.isn for st in dd.stops if st.customer_id in moving for o in st.orders}
        if before - {o.isn for st in after.stops for o in st.orders}:
            raise dp.DispatchError(SETTINGS_ON_STARTED)
    if new.built_orders is not None:
        gone = {o.isn for o in dd.deliver} - {o.isn for o in after.deliver}
        new.built_orders = {k: v for k, v in new.built_orders.items() if k not in gone}
    return new


STOP_RULES = ('deny_truck', 'only_trucks', 'never')
STOP_RULE_MAX_TRUCKS = 100
PAST_DAY_RULE = 'Անցած օրվա պլանում խանութը կանոնով չի հանվում'
ONLY_ALLOWED_TRUCK = ('Սա այս խանութի միակ թույլատրված մեքենան է։ Ընտրեք «Միշտ տանել միայն ընտրված մեքենաներով» '
                      'և նշեք, թե որ մեքենաներն են տանում')
RULE_REFUSED = 'Կարգավորումները չեն ընդունում այս խանութը'


@dataclass
class _StopRule:
    """Правка «×» с причиной-правилом (и «Մինչև ժամը», _until_edit): черновик дня после неё, bundle с новым правилом (по
    нему день считается заново), also — запись правила в транзакции плана (store.save_dispatch), kept — будущие дни, где
    «никогда» не применено: магазин там уже в загруженной машине (№78)."""
    draft: dp.Draft
    bundle: Bundle
    also: Callable[[Any], None]
    kept: list[str] = field(default_factory=list)


def _fleet_with_off(raw: Any, old: Mapping[str, Any], new: Mapping[str, Any], cid: int) -> dict[str, Any] | None:
    """Правило «чьи заказы везут машины» собранного дня (Draft.fleet) с клиентом cid в «машины не везут». День был собран
    по нынешним настройкам — и дальше по ним (иначе плашка «план не по настройкам» без причины); свой снимок — к нему
    добавляется только cid."""
    rule = dp.FleetRule.from_json(raw)
    if rule.same_as(dp.FleetRule.from_settings(old)):
        return dp.FleetRule.from_settings(new).to_json()
    return replace(rule, customers_off=rule.customers_off | {cid}).to_json()


def _stop_rule_edit(state: RoutesState, bundle: Bundle, dd: _DispatchDay, payload: Mapping[str, Any],
                    confirmed: bool) -> _StopRule:
    """«×» у магазина с причиной-правилом (владелец 07.10: «обязательно с объяснением… чтобы фильтры всегда работали»):
    {"action": "stop_rule", "trip", "customer_id", "rule": deny_truck | only_trucks | never, "trucks": [код, …]
    (только only_trucks)}. Магазин уходит из рейса (trip_stops remove) и запоминается правило на все дни:
      deny_truck — машине рейса нельзя к магазину (допуск магазина: deny + машина; allow — машина вычёркивается);
      only_trucks — магазин возят только эти машины (допуск allow; машины рейса среди них нет — её и убираем);
      never — «машины не везут» (настройка dispatch_customers_off, №74): и в правиле этого дня, и в уже собранных
          следующих днях (Draft.fleet) — кроме дня, где магазин уже в загруженной машине (_StopRule.kept).
    Допуск машин действует на все дни сразу (dispatch._require_vehicle, plan_view). Ничего не сохраняет сама: правило
    пишется в той же транзакции, что и черновик (_StopRule.also). «Չեղարկել» правило не отменяет — undo у правки нет
    (правило меняют в «Առաքման պայմաններ» и в настройках)."""
    kind, cid, tid = payload.get('rule'), payload.get('customer_id'), payload.get('trip')
    if kind not in STOP_RULES or not dp._is_int(cid) or not dp._is_int(tid):
        raise dp.DispatchError('Սերվերը չընդունեց հարցումը — թարմացրեք էջը')
    if dd.day < _clock().date():   # те же часы, что у is_past страницы
        raise dp.DispatchError(PAST_DAY_RULE)
    trip = dp.trip_of(dd.draft, tid)
    if cid not in trip.stops:
        raise dp.DispatchError('Այս խանութն արդեն երթում չէ — թարմացրեք էջը')
    user = session.get('username')
    snap, _ = state.snapshots.get(allow_stale=True)
    draft = dp.apply_edit(dd.ctx, dd.stops, dd.draft, {'action': 'trip_stops', 'trip': tid, 'remove': [cid]},
                          {o.isn for o in dd.deliver}, {o.isn for o in dd.backlog}, carried=dd.carried,
                          now_min=_today_min(dd), loaded_ok=confirmed)
    draft.undo = None
    if kind == 'never':
        old_s = bundle.settings
        off = sorted({*(old_s.get('dispatch_customers_off') or ()), cid})
        changes, errors = validate_payload({'settings': {'dispatch_customers_off': off}}, bundle, snap.ref_data())
        if errors or changes is None:
            raise dp.DispatchError(RULE_REFUSED + '՝ ' + '; '.join(errors.values()) if errors else RULE_REFUSED)
        new_s = {**old_s, 'dispatch_customers_off': off}
        draft.fleet = _fleet_with_off(draft.fleet, old_s, new_s, cid)
        if draft.built_orders is not None:   # как _apply_settings_edit: выведенные правилом — не «убраны после сборки»
            gone = {o.isn for o in dd.deliver if o.customer_id == cid} | {
                o.isn for st in dd.stops if st.customer_id == cid for o in st.orders}
            draft.built_orders = {k: v for k, v in draft.built_orders.items() if k not in gone}
        kept: list[str] = []

        def later(day: str, data: dict[str, Any]) -> dict[str, Any] | None:
            if day == dd.day.isoformat():
                return None
            trips = data.get('trips') if isinstance(data.get('trips'), list) else []
            if any(isinstance(t, dict) and t.get('loaded') and isinstance(t.get('stops'), list) and cid in t['stops']
                   for t in trips):
                kept.append(day)                  # товар уже в машине — день решает логист сам
                return None
            fleet = _fleet_with_off(data.get('fleet'), old_s, new_s, cid)
            return None if fleet == data.get('fleet') else {**data, 'fleet': fleet}

        def also(conn: Any) -> None:
            try:
                state.store.add_customer_off(conn, cid)
            except ValueError as e:
                raise dp.DispatchError(RULE_REFUSED + '՝ ' + str(e)) from e
            # сегодняшний план уже в работе — правило дня у него своё; следующие дни — все уже собранные
            state.store.rewrite_dispatch_after(conn, _clock().date().isoformat(), later, user)
            logger.info('[Routes] «Развоз» %s: клиент %d — машины не везут никогда (%s)', dd.day, cid, user)
        return _StopRule(draft, replace(bundle, settings=new_s), also, kept)
    old = bundle.vehicle_access.get(cid)
    if kind == 'deny_truck':
        if old is not None and old.mode == 'allow':
            if trip.truck not in old.trucks:   # и так не разрешена (магазин в рейсе вопреки допуску) — правило уже есть
                raw: Any = old.to_json()
            else:
                rest = [c for c in old.trucks if c != trip.truck]
                if not rest:
                    raise dp.DispatchError(ONLY_ALLOWED_TRUCK)
                raw = {'mode': 'allow', 'trucks': rest}
        else:
            raw = {'mode': 'deny', 'trucks': sorted({*(old.trucks if old else ()), trip.truck})}
    else:
        trucks = payload.get('trucks')
        if not isinstance(trucks, list) or not trucks or len(trucks) > STOP_RULE_MAX_TRUCKS:
            raise dp.DispatchError('Նշեք գոնե մեկ մեքենա, որը տանում է այս խանութը')
        if trip.truck in trucks:
            raise dp.DispatchError('Երթի մեքենան ընտրվածների մեջ է՝ խանութն այդ դեպքում երթից հանելու կարիք չկա')
        raw = {'mode': 'allow', 'trucks': trucks}
    access, err = check_access(raw, set(snap.cars) | set(bundle.trucks) | set(old.trucks if old else ()))
    if err or access is None:
        raise dp.DispatchError(err or 'Սերվերը չընդունեց հարցումը — թարմացրեք էջը')

    def also_access(conn: Any) -> None:
        if state.store.customer_access_in(conn, cid) != old:
            raise dp.DispatchError('Խանութի մեքենաների կանոնը հենց նոր փոխվել է — թարմացրեք էջը')
        state.store._write_customer_vehicles(conn, cid, access, user)
        logger.info('[Routes] «Развоз» %s: допуск машин клиента %d: %s (%s)', dd.day, cid, access, user)
    return _StopRule(draft, replace(bundle, vehicle_access={**bundle.vehicle_access, cid: access}), also_access)


UNTIL_SCOPES = ('day', 'always')
_UNTIL_RE = re.compile(r'^(\d{2}):([0-5]\d)$')   # часы 24 и больше — ошибка check_window (минуты за пределом суток)
PAST_DAY_UNTIL = 'Անցած օրվա պլանում ժամը չի փոխվում'
UNTIL_GONE = 'Այս ժամն արդեն նշված չէ — թարմացրեք էջը'
UNTIL_OTHER_KIND = 'Խանութի մշտական ընդունման ժամը «մինչև» չէ — այն փոխվում է «Առաքման պայմաններ»-ում'
UNTIL_BEFORE_FLOOR = ('Խանութն ընդունում է {}-ից ոչ շուտ (մշտական ընդունման ժամը) — նշեք ավելի ուշ ժամ '
                      'կամ փոխեք մշտական ժամը «Առաքման պայմաններ»-ում')


def _until_edit(state: RoutesState, bundle: Bundle, dd: _DispatchDay,
                payload: Mapping[str, Any]) -> tuple[_StopRule, dict[str, Any]]:
    """«Մինչև ժամը» у магазина (владелец 08.10): {"action": "until", "customer_id", "time": "HH:MM" | null,
    "scope": "day" | "always"}. day — срок только этого дня (store.customer_day_until: в этот день — вместо окна магазина);
    always — постоянное окно «до» (store.customer_window; прежнее окно любого вида заменяется, срок этого дня снимается —
    иначе он перекрыл бы новое окно). time null — снять: day — срок дня (снова постоянное окно), always — постоянное окно,
    только если оно «до» (окна других видов меняют в «Առաքման պայմաններ»). Время проверяет check_window. Срок дня — конец
    окна этого дня, начало постоянного окна остаётся (Bundle.windows_on): срок не позже начала — отказ. Рейс, где магазин
    опаздывает, переставляется под окно (dp.fit_until; закреплённые логистом, загруженные и уже грузящиеся — нет). Ничего не
    сохраняет сама: срок пишется в транзакции плана (_StopRule.also); «Չեղարկել» его не отменяет — undo у правки нет.
    Второе значение — ответ fit_until (странице: успевает ли магазин, подсказка машины)."""
    cid, raw, scope = payload.get('customer_id'), payload.get('time'), payload.get('scope')
    if scope not in UNTIL_SCOPES or not dp._is_int(cid) or not (raw is None or isinstance(raw, str)):
        raise dp.DispatchError('Սերվերը չընդունեց հարցումը — թարմացրեք էջը')
    if dd.day < _clock().date():   # те же часы, что у is_past страницы
        raise dp.DispatchError(PAST_DAY_UNTIL)
    if all(s.customer_id != cid for s in dd.stops):
        raise dp.DispatchError('Խանութն այս օրվա պատվերներում չէ — թարմացրեք էջը')
    window = None
    if raw is not None:
        m = _UNTIL_RE.match(raw)
        window, err = check_window({'kind': 'before', 't1': int(m[1]) * 60 + int(m[2]) if m else None})
        if err:
            raise dp.DispatchError(err)
    old = bundle.windows.get(cid)
    windows = dict(bundle.windows)
    mine = dict(bundle.day_until.get(dd.day, {}))
    if scope == 'day':
        if window is None and cid not in mine:
            raise dp.DispatchError(UNTIL_GONE)
        floor = until_floor(old)
        if window is not None and floor is not None and window.t1 <= floor:   # срок дня — конец окна, начало — постоянное
            raise dp.DispatchError(UNTIL_BEFORE_FLOOR.format(f'{floor // 60:02d}:{floor % 60:02d}'))
        if window is None:
            del mine[cid]
        else:
            mine[cid] = window.t1
    elif window is None:
        if old is None or old.kind != 'before':
            raise dp.DispatchError(UNTIL_OTHER_KIND if old is not None else UNTIL_GONE)
        del windows[cid]
    else:
        windows[cid] = window
        mine.pop(cid, None)
    day_until = {**bundle.day_until, dd.day: mine} if mine else {d: v for d, v in bundle.day_until.items() if d != dd.day}
    new = replace(bundle, windows=windows, day_until=day_until)
    ctx = replace(dd.ctx, windows={c: w.span() for c, w in new.windows_on(dd.day).items()})
    draft = dd.draft
    draft.undo = None
    now_min = _today_min(dd)
    # начатые рейсы — по плану до правки (окна прежние): срок не делает рейс «ещё не грузящимся»
    frozen = dp.started_trips(dd.ctx, dd.stops, draft, now_min) if now_min is not None else set()
    fit = dp.fit_until(ctx, dd.stops, draft, cid, now_min=now_min, frozen=frozen)
    user = session.get('username')

    def also(conn: Any) -> None:
        if scope == 'day':
            Store._write_customer_day_until(conn, dd.day, cid, window.t1 if window is not None else None, user)
        else:
            if Store.customer_window_in(conn, cid) != old:   # окно сменили в «Առաքման պայմաններ» — не затираем
                raise dp.DispatchError('Խանութի ընդունման ժամը հենց նոր փոխվել է — թարմացրեք էջը')
            Store._write_customer_window(conn, cid, window, user)
            if window is not None:
                Store._write_customer_day_until(conn, dd.day, cid, None, user)
        logger.info('[Routes] «Развоз» %s: клиент %d — «Մինչև ժամը» %s (%s), переставлены рейсы %s (%s)', dd.day, cid,
                    raw, scope, fit['reordered'], user)
    return _StopRule(draft, new, also), fit



def _check_take_started(dd: _DispatchDay, payload: Mapping[str, Any]) -> set[str]:
    """«Տանել այսօր» заказа прошлых дней (владелец 08.10): заказы прошлых дней из правки. Магазин уже в рейсе, чья загрузка
    по плану началась (сегодня; и по отправленному водителям плану, №81), — заказ к нему не прилипнет: товар не в машине
    (как у заказов дня, №72 «started») — DispatchError с названиями магазинов."""
    raw = payload.get('orders') if 'orders' in payload else [payload.get('order')]
    picked = {x.upper() for x in raw if isinstance(x, str)} & {o.isn for o in dd.backlog} if isinstance(raw, list) else set()
    now_min = _today_min(dd)
    if not picked or now_min is None or dd.draft is None or dd.ctx is None:
        return picked
    started = dp.started_customers(dd.ctx, dd.stops, dd.draft, now_min)
    late = sorted({o.customer_id for o in dd.backlog if o.isn in picked and o.customer_id in started})
    if late:
        names = ', '.join('«' + (dd.data.customers.get(c) or ('', str(c)))[1] + '»' for c in late)
        raise dp.DispatchError(names + '՝ մեքենան արդեն բեռնվում է կամ ճանապարհին է, պատվերն այսօր այդ երթով չի գնա։ '
                               'Ավելացրեք այն վաղը կամ ուղարկեք առանձին։')
    return picked


def _check_defer_same_day(dd: _DispatchDay, trip_id: Any) -> None:
    """«Везти завтра» рейса, который уже грузится или в пути, с взятыми сегодня заказами дня (№72) — нельзя: они снова
    стали бы заказами завтра и их отвезли бы дважды (DispatchError)."""
    now = _same_day_now()
    if dd.draft is None or dd.ctx is None or not dd.draft.same_day or dd.day != now.date():
        return
    trip = next((t for t in dd.draft.trips if t.id == trip_id), None)
    taken = {o.customer_id for o in dd.same_day if o.isn in dd.draft.same_day}
    if trip is not None and taken & set(trip.stops) \
            and (trip.id in dp.started_trips(dd.ctx, dd.stops, dd.draft, _now_min(dd.ctx, now))
                 # №81: заказ дня уже едет по отправленному плану в другом рейсе — тоже нельзя
                 or taken & set(trip.stops) & dp.started_customers(dd.ctx, dd.stops, dd.draft, _now_min(dd.ctx, now))):
        raise dp.DispatchError('Մեքենան արդեն բեռնվում է կամ ճանապարհին է՝ այսօրվա նոր պատվերներով — երթը վաղվան '
                               'տեղափոխել չի կարելի')


def _defer_days(dd: _DispatchDay) -> list[date]:
    """Куда «Երբ տանել» переносит заказы магазина (dp.defer_days); прошедший день — никуда (как defer_days страницы)."""
    s = dd.bundle.settings
    return [] if dd.day < _clock().date() else dp.defer_days(dd.day, s['workdays'], dp.holidays_of(s))


def _check_defer_store(dd: _DispatchDay, cid: Any) -> None:
    """«Այսօր չենք տանում» магазина со взятыми в развоз сегодня новыми заказами дня (№72) — по правилам их «не везём»
    (_same_day_edit): только сегодня и не тогда, когда машина с ними уже грузится или в пути — иначе их отвезли бы дважды."""
    if dd.draft is None or dd.ctx is None or not dd.draft.same_day:
        return
    if not any(o.customer_id == cid and o.isn in dd.draft.same_day for o in dd.same_day):
        return
    now = _same_day_now()
    if dd.day != now.date():
        raise dp.DispatchError('Նոր պատվերները կարելի է փոխել միայն այսօրվա առաքման մեջ')
    if cid in dp.started_customers(dd.ctx, dd.stops, dd.draft, _now_min(dd.ctx, now)):
        raise dp.DispatchError('Մեքենան արդեն բեռնվում է կամ ճանապարհին է՝ այս պատվերով — այն այսօր է գնում, '
                               'վաղվան թողնել չի կարելի')


def _is_same_day_edit(dd: _DispatchDay, payload: Mapping[str, Any]) -> bool:
    """Правка новых заказов дня: свои действия или «не везём сегодня» для взятого заказа дня (он не в заказах окна дня;
    взятый до правила №79 заказ, заведённый заранее, — уже в них: его «не везём» — обычное)."""
    action = payload.get('action')
    order = payload.get('order')
    return action in ('same_day', 'same_day_drop') or (
        action == 'exclude' and isinstance(order, str) and dd.draft is not None and order.upper() in dd.draft.same_day
        and all(o.isn != order.upper() for o in dd.deliver))


DRIVER_LIST_DAYS = 90        # водители ERP и свои — кто встречался за 90 дней
DRIVER_LIST_TTL_S = 3600     # список ERP перечитывается не чаще раза в час
DRIVER_LIST_RETRY_S = 60     # ERP недоступна — снова спросить через минуту (страница работает со своими)


def _erp_drivers(state: RoutesState) -> list[str]:
    """Водители-экспедиторы ERP (waybill.load_drivers) из кэша. Перечитывает только GET (открытие дня) и только один
    запрос за раз: правки плана (POST) ERP ради необязательного списка не ждут — им прежний список, даже устаревший. ERP
    недоступна — прежний список остаётся (в логе), повтор через DRIVER_LIST_RETRY_S; страница не падает."""
    cached = state.driver_list_cache
    names = cached[1] if cached is not None else []
    if (cached is not None and time.monotonic() < cached[0]) or state.driver_list_loader is None \
            or not (has_request_context() and request.method == 'GET') or not state.driver_list_lock.acquire(blocking=False):
        return names
    try:
        today = _clock().date()
        try:
            names = state.driver_list_loader(today - timedelta(days=DRIVER_LIST_DAYS), today + timedelta(days=1))
            ttl = DRIVER_LIST_TTL_S
        except ErpError:
            logger.warning('[Routes] Список водителей ERP не прочитан — прежний список', exc_info=True)
            ttl = DRIVER_LIST_RETRY_S
        state.driver_list_cache = (time.monotonic() + ttl, names)
        return names
    finally:
        state.driver_list_lock.release()


def driver_name_hints(state: RoutesState) -> list[str]:
    """Имена водителей для подсказки в офисе «Առաքիչ» (№84, ревью: имя водителя APK сравнивается с записями «Развоза»
    строкой) — тот же список, что у «Վարորդ»: ERP (кэш _erp_drivers) и свои за DRIVER_LIST_DAYS, по алфавиту. ERP
    здесь не ждём: кэша нет или он устарел — перечитывается в фоне (один поток за раз), ответ — с тем, что есть."""
    cached = state.driver_list_cache
    if (cached is None or time.monotonic() >= cached[0]) and state.driver_list_loader is not None \
            and state.driver_list_lock.acquire(blocking=False):
        today = _clock().date()

        def refresh() -> None:
            names, ttl = (cached[1] if cached is not None else []), DRIVER_LIST_RETRY_S   # сбой — прежний, повтор скоро
            try:
                names = state.driver_list_loader(today - timedelta(days=DRIVER_LIST_DAYS), today + timedelta(days=1))
                ttl = DRIVER_LIST_TTL_S
            except ErpError:
                logger.warning('[Routes] Список водителей ERP не прочитан — прежний список', exc_info=True)
            except Exception:   # фоновый поток не должен падать молча
                logger.exception('[Routes] Список водителей ERP: сбой')
            finally:
                state.driver_list_cache = (time.monotonic() + ttl, names)
                state.driver_list_lock.release()
        try:
            threading.Thread(target=refresh, name='routes-driver-list', daemon=True).start()
        except BaseException:   # поток не запустился — замок не должен остаться занятым навсегда
            state.driver_list_lock.release()
            logger.exception('[Routes] Список водителей ERP: фоновое чтение не запущено')
    since = (_clock().date() - timedelta(days=DRIVER_LIST_DAYS)).isoformat()
    return sorted(set(cached[1] if cached is not None else []) | set(state.store.driver_names(since)))


ERP_CREW_DAYS = 30        # экипаж по ERP (№84) — кто возил машину за 30 дней
ERP_CREW_TTL_S = 3600     # подбор по ERP — не чаще раза в час
ERP_CREW_RETRY_S = 60     # ERP недоступна — снова через минуту (страница работает с прежними записями)


def _erp_crew(state: RoutesState, trucks: Collection[str]) -> None:
    """Экипаж по ERP машинам без записей (ответ владельца №84: waybill.pick_car_crews за ERP_CREW_DAYS, запись —
    store.fill_erp_crew). Как _erp_drivers: только GET (открытие дня), один запрос за раз, не чаще ERP_CREW_TTL_S; ERP
    недоступна — повтор через ERP_CREW_RETRY_S. Любой сбой — в лог: страница работает с прежними записями."""
    if state.crew_days_loader is None or time.monotonic() < state.erp_crew_next \
            or not (has_request_context() and request.method == 'GET') or not state.erp_crew_lock.acquire(blocking=False):
        return
    try:
        today = _clock().date()
        ttl = ERP_CREW_TTL_S
        try:
            rows = state.crew_days_loader(today - timedelta(days=ERP_CREW_DAYS), today + timedelta(days=1))
            written = state.store.fill_erp_crew(today.isoformat(), lambda taken: wb.pick_car_crews(rows, trucks, taken))
            if written:
                logger.info('[Routes] Экипаж по ERP с %s: %s', today, ', '.join(f'{c} {r}' for c, r, _ in written))
        except Exception:   # ERP, база настроек — подбор необязателен: страница не падает
            logger.warning('[Routes] Экипаж по ERP не подобран — прежние записи', exc_info=True)
            ttl = ERP_CREW_RETRY_S
        state.erp_crew_next = time.monotonic() + ttl
    finally:
        state.erp_crew_lock.release()


def _drivers_json(state: RoutesState, day: date) -> dict[str, Any]:
    """Водители машин на день (№62): drivers — машина → имя, substitutes — машины с подменой на этот день (только он);
    helpers, helper_substitutes — то же для առաքիչ (второй человек в машине);
    driver_list — из чего выбирать (ответ владельца: «из ERP + добавить своих»): [{name, erp}] — экспедиторы ERP за
    DRIVER_LIST_DAYS, затем свои (вписанные в «Վարորդ» за тот же срок и не из ERP)."""
    store = state.store
    drivers, subs = store.truck_drivers(day.isoformat())
    helpers, helper_subs = store.truck_drivers(day.isoformat(), 'helper')
    erp_names = _erp_drivers(state)
    in_erp = set(erp_names)
    today = _clock().date()
    since = (today - timedelta(days=DRIVER_LIST_DAYS)).isoformat()
    # свои — вписанные за срок и все, кто в машинах сейчас или в этот день (давно закреплённый не пропадает из выбора)
    current = {n for role in CREW_TABLES for n in store.truck_drivers(today.isoformat(), role)[0].values()}
    own = sorted((set(store.driver_names(since)) | current | set(drivers.values()) | set(helpers.values())) - in_erp)
    return {'drivers': drivers, 'substitutes': sorted(subs), 'helpers': helpers, 'helper_substitutes': sorted(helper_subs),
            'driver_list': [{'name': n, 'erp': True} for n in erp_names] + [{'name': n, 'erp': False} for n in own]}


def _crew(state: RoutesState, day: date, trucks: Collection[str]) -> tuple[dp.Crew, dict[str, str]]:
    """Водители дня (№77): (dispatch.Crew — машина → водитель на день (№62) у машин из настроек trucks (запись машины,
    удалённой из настроек, не в счёт), машины с подменой логиста на этот день, кто из этих водителей не вышел; не вышедшие —
    имя → по какой день, store.driver_absences)."""
    own, subs = state.store.truck_drivers(day.isoformat())
    own = {c: n for c, n in own.items() if c in trucks}
    away = state.store.driver_absences(day.isoformat())
    return dp.Crew(own, frozenset(subs) & set(own), frozenset(n for n in set(own.values()) if n in away)), away


def _crew_json(state: RoutesState, day: date, draft: dp.Draft | None, trucks: Collection[str]) -> dict[str, Any]:
    """Водители дня для страницы (№77) — только ей (имена людей): crew.drivers — [{name, trucks — машины, где он водитель
    на день, absent, until — по какой день его нет}] по имени; crew.trucks — кто ведёт машины плана (dispatch.crew_view);
    crew.stale — план собран при других водителях, пересобрать: изменилось, кто не вышел; у отмеченной машины без водителя
    снова есть свой; появился свободный водитель (у неотмеченной машины), а отмеченные машины стоят без водителя; посадка
    сборки больше не нужна (у машины снова свой водитель)."""
    crew, away = _crew(state, day, trucks)
    names: dict[str, list[str]] = {}
    for code, name in sorted(crew.own.items()):
        names.setdefault(name, []).append(code)
    view = dp.crew_view(draft, crew) if draft is not None else {}
    driving = {v['name'] for v in view.values()}
    stale = draft is not None and draft.built_at is not None and (
        set(draft.absent) != crew.absent
        or any(crew.own.get(c) and crew.own[c] not in crew.absent and crew.own[c] not in driving for c in draft.unmanned)
        or bool(draft.unmanned and set(dp.seating(crew, [*draft.trucks, *draft.unmanned])[2]) - driving)
        or any(v['stale'] for v in view.values()))
    sources = state.store.crew_sources(day.isoformat())    # №84: запись из «Առաքիչ» или ERP — у первой машины человека
    return {'crew': {'drivers': [{'name': n, 'trucks': codes, 'absent': n in crew.absent,
                                  **({'until': away[n]} if n in crew.absent else {}),
                                  **({'source': sources[codes[0]]} if codes[0] in sources else {})}
                                 for n, codes in sorted(names.items())],
                     'trucks': view, 'stale': stale}}


DRIVER_ABSENCE_MAX_DAYS = 366   # «до даты» — не дальше года: отпуск и болезнь
ABSENCE_BAD_UNTIL = 'Նշեք օրը, մինչև որը վարորդը չի աշխատի'
ABSENCE_UNKNOWN = 'Այս վարորդն այդ օրը մեքենա չունի — թարմացրեք էջը'


@bp.post('/api/routes/dispatch/absence')
@_api
def api_dispatch_absence() -> Any:
    """Вышел ли водитель (ответ владельца №77): {"date", "name", "absent": true, "until"?: "ГГГГ-ММ-ДД"} — не вышел только
    в этот день (по умолчанию) или с этого дня по until включительно (отпуск, болезнь; не дальше DRIVER_ABSENCE_MAX_DAYS);
    {"date", "name", "absent": false} — вышел: отсутствие, куда попадает день, кончается накануне. name — водитель машины на
    этот день (№62). План не меняется — пересобирает логист (crew.stale). Ответ — водители дня (_crew_json)."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    name, absent, until = payload.get('name'), payload.get('absent'), payload.get('until')
    errors = {}
    if not isinstance(absent, bool):
        errors['absent'] = BAD_ONLY_DAY
    if until is not None:
        until = _parse_day(until)
        if absent is not True or until is None or not day <= until <= day + timedelta(days=DRIVER_ABSENCE_MAX_DAYS):
            errors['until'] = ABSENCE_BAD_UNTIL
    state = _state()
    if not isinstance(name, str) or name not in set(state.store.truck_drivers(day.isoformat())[0].values()):
        errors['name'] = ABSENCE_UNKNOWN
    if errors:
        return _bad_request(errors)
    if absent:
        state.store.save_driver_absence(name, day.isoformat(), until.isoformat() if until else None, session.get('username'))
    else:
        state.store.save_driver_present(name, day.isoformat(), session.get('username'))
    logger.info('[Routes] Водитель на %s: %s%s (%s)', day, 'не вышел' if absent else 'вышел',
                f' по {until}' if until else '', session.get('username'))
    return jsonify({'success': True, 'day': day.isoformat(),
                    **_crew_json(state, day, _stored_draft(state, day)[0], state.store.load().trucks)})


SETTINGS_TRUCKS_URL = '/routes/settings#trucks'
GEO_SUGGESTIONS_MAX = 50   # предложений водителей в ответе «Развоза»


def _geo_suggestions(state: RoutesState, dd: _DispatchDay) -> dict[str, Any]:
    """Открытые предложения водителей «точка неверная» (driver-geo-plan.md §4) — по магазинам: у магазина последнее
    предложение и сколько их всего (suggestions), логист решает один раз. Сначала магазины дня, затем остальные,
    внутри — по свежести; count / day_count — магазины, в списке не больше GEO_SUGGESTIONS_MAX. У каждого — текущая
    точка магазина (с её источником) и на сколько метров предложенная от неё. Источника нет или сбой — пусто."""
    out: dict[str, Any] = {'count': 0, 'day_count': 0, 'items': []}
    if state.driver_geo is None:
        return out
    try:
        found = state.driver_geo.suggestions()
    except Exception:
        logger.warning('[Routes] Предложения водителей не прочитаны', exc_info=True)
        return out
    day = {s.customer_id for s in dd.stops}
    per_customer: Counter[int] = Counter()
    latest: list[dict[str, Any]] = []
    for x in found:   # новые первыми: первое по клиенту — его последнее предложение
        if isinstance(x.get('customer_id'), int) and is_valid_point(x.get('lat'), x.get('lon')):
            per_customer[x['customer_id']] += 1
            if per_customer[x['customer_id']] == 1:
                latest.append(x)
    found = sorted(latest, key=lambda x: x['customer_id'] not in day)   # sorted устойчив: свежие первыми остаются

    def item(x: dict[str, Any]) -> dict[str, Any]:
        cid = x['customer_id']
        cur = evaluate.visit_coord(dd.snap, cid, 0, dd.bundle.geo_overrides, dd.bundle.driver_points)
        code, name = dd.data.customers.get(cid) or (
            (dd.snap.customers[cid].code, dd.snap.customers[cid].name) if cid in dd.snap.customers
            else (x.get('code') or str(cid), x.get('name') or ''))
        point = (float(x['lat']), float(x['lon']))
        return {
            'event_id': x['event_id'], 'customer_id': cid, 'code': code, 'name': name,
            'address': dd.data.addresses.get(cid, ''),
            'lat': round(point[0], 6), 'lon': round(point[1], 6), 'accuracy': x.get('accuracy'),
            'note': x.get('note'), 'driver_name': x.get('driver_name'), 'date': x.get('date'), 'at': x.get('at'),
            'in_day': cid in day, 'suggestions': per_customer[cid],
            'current': ({'lat': round(cur.lat, 6), 'lon': round(cur.lon, 6), 'source': cur.source}
                        if cur.point is not None else None),
            'distance_m': round(haversine_km(cur.point, point) * 1000) if cur.point is not None else None,
        }
    return {'count': len(found), 'day_count': sum(1 for x in found if x['customer_id'] in day),
            'items': [item(x) for x in found[:GEO_SUGGESTIONS_MAX]]}


def _overtime_days(store: Store, day: date) -> int:
    """Дней месяца, когда машины по плану развоза работали после конца дня: форс-мажор не должен стать
    нормой (ответ владельца №32)."""
    first = day.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return store.count_dispatch_overtime(first.isoformat(), last.isoformat())


@bp.get('/api/routes/dispatch')
@_api
def api_dispatch() -> Any:
    """День развоза (?date=YYYY-MM-DD, по умолчанию — следующий рабочий день): машины, заказы,
    черновик рейсов с цифрами, сравнение «по менеджерам». ?refresh=1 — перечитать заказы ERP."""
    state = _state()
    bundle = _bundle(state)
    raw = request.args.get('date')
    day = _parse_day(raw) if raw else dp.next_workday(_clock().date(), bundle.settings['workdays'],
                                                      dp.holidays_of(bundle.settings))
    if day is None:
        return _bad_request({'date': 'дата в формате ГГГГ-ММ-ДД'})
    _erp_crew(state, bundle.trucks)     # №84: машинам без записей — экипаж по ERP, до чтения водителей дня
    dd = _load_day(state, bundle, day, refresh=request.args.get('refresh') == '1')
    return jsonify({'success': True, **_dispatch_page_body(dd)})


@bp.get('/api/routes/dispatch/status')
@_api
def api_dispatch_status() -> Any:
    """Лёгкая проверка для автообновления страницы (раз в 5 минут): заказы дня по тем же правилам кэша
    (ERP только читается), «заказы ещё поступают» и что изменилось с последней сборки. Рейсы не
    пересчитываются и не собираются — это делает логист кнопкой."""
    state = _state()
    bundle = state.store.load()
    day = _parse_day(request.args.get('date'))
    if day is None:
        return _bad_request({'date': 'дата в формате ГГГГ-ММ-ДД'})
    draft, rev = _stored_draft(state, day)
    rule = dp.fleet_rule_of(draft, bundle.settings)
    later = _later_scan(state, day, bundle.settings['workdays'], dp.holidays_of(bundle.settings))
    due = _later_due(later, day)
    _, _, data, sel = _day_orders(state, bundle, day, False, rule, later)
    carried = _carried(state, day, bundle.settings['workdays'], sel.backlog, dp.holidays_of(bundle.settings), due)
    # новые заказы дня (№72): сегодня — сколько их ещё решать (плашка без перезагрузки); ERP не ответила — без них
    today: list[dp.DispatchOrder] = []
    same: dict[str, Any] = {}
    if day == _same_day_now().date() or (draft is not None and draft.same_day):
        try:
            sd = _same_day_data(state, day, refresh=False)
        except ErpError:
            logger.warning('[Routes] Новые заказы дня %s не прочитаны — без подсказки', day, exc_info=True)
            sd = None
        if sd is not None:
            today = dp.same_day_candidates(sd.orders, day, rule, dp.place_of(sd.customers, sd.addresses))
            if day == _same_day_now().date():
                same = {'same_day': _same_day_summary(_same_day_open(today, draft, bundle.settings))}
    active = _active_orders(sel.main, sel.backlog, draft, carried, bundle.settings, today)
    return jsonify({'success': True, 'day': day.isoformat(), 'rev': rev,
                    'orders': {'count': len(active), 'kg': round(sum(o.kg for o in active)),
                               'revenue': round(sum(o.revenue for o in active))},
                    **_freshness(day, bundle, data, sel.main, sel.backlog, draft, carried,
                                 {o.isn for o in (*sel.customers_off, *sel.other_vehicle)}), **same})


def _dispatch_request() -> tuple[Any, Any, Any]:
    """(тело, день, None) или (None, None, ответ с ошибкой)."""
    payload, error = _json_body()
    if error is not None:
        return None, None, error
    if not isinstance(payload, dict):
        return None, None, _bad_request({'_': 'ожидался JSON-объект'})
    day = _parse_day(payload.get('date'))
    if day is None:
        return None, None, _bad_request({'date': 'дата в формате ГГГГ-ММ-ДД'})
    return payload, day, None


def _planned_lunch(tr: Mapping[str, Any]) -> dict[str, Any]:
    """Обед рейса плана (plan_view: lunch) → запись прогноза: где, магазин (после разгрузки которого), начало, минуты
    обеда и сколько он добавил к рейсу (остальное — ожидание окна приёма)."""
    lunch = tr['lunch']
    where = lunch['where']
    return {'where': where, 'customer': tr['stops'][lunch['after_stop']]['customer_id'] if where == 'store' else None,
            'start': lunch['start'], 'minutes': lunch['minutes'], 'added': lunch['added_min']}


def _capture_prediction(dd, draft):
    view = dp.plan_view(dd.ctx, dd.stops, draft, _stop_info(dd), explain=False)
    now = _clock()
    start = datetime.combine(dd.day, datetime.strptime(dd.bundle.settings['truck_work_start'], '%H:%M').time())
    # depart/return — выезд первого рейса и возвращение последнего (HH:MM): «время работы» отчёта «план — факт»;
    # trips — начало загрузки, выезд, возвращение и ETA точек каждого рейса: плановое ожидание на складе (обучение
    # загрузки) и карта «план — факт»; lunch — обед по плану (№61): где (магазин — после разгрузки которого, склад, дорога),
    # начало, минуты — обучение обеда ищет его там, а разгрузка и загрузка эту стоянку не учитывают; return — по медиане
    # (без запаса на рейс, №66), buffer — минуты запаса отдельно: запас не плановое возвращение и не плановый простой
    # (обучение загрузки и обеда — learning.plan_trips, «время работы» — learning._plan_minutes)
    def back(tr: Mapping[str, Any]) -> str:
        return tr['buffer']['start'] if tr.get('buffer') else tr['return']
    draft.prediction = {'created_at': now.isoformat(), 'prospective': now < start,
        'trucks': {t['car_code']: {**{key: t.get(key) for key in ('km', 'minutes', 'liters', 'loading_minutes', 'wear_amd')},
                                   'depart': t['trips'][0]['depart'] if t['trips'] else None,
                                   'return': back(t['trips'][-1]) if t['trips'] else t.get('return'),
                                   'trips': [{'loading_start': tr['loading_start'], 'depart': tr['depart'],
                                              'return': back(tr),
                                              'stops': [[x['customer_id'], x.get('eta')] for x in tr['stops']],
                                              **({'lunch': _planned_lunch(tr)} if tr.get('lunch') else {}),
                                              **({'buffer': tr['buffer']['minutes']} if tr.get('buffer') else {}),
                                              # рельеф (№85): подъём и литры рейса по плану; без высот — ключей нет
                                              **({key: tr[key] for key in ('climb_m', 'terrain_l')}
                                                 if 'terrain_l' in tr else {})}
                                             for tr in t['trips']]}
                   for t in view['trucks']}}


def _conflict(text: str) -> Any:
    return jsonify({'success': False, 'error': text, 'conflict': True}), 409


@bp.post('/api/routes/dispatch/build')
@_api
def api_dispatch_build() -> Any:
    """«Собрать рейсы»: {"date", "trucks": [коды машин дня], "agents_off"?: [agent_id]}. Закреплённые рейсы и
    исключённые заказы прежнего черновика сохраняются, остальное раскладывается заново; agents_off — фильтр
    «Մենեջերներ» (чьи заказы не везём), без него — фильтр прежнего черновика. Машин в рейсах — не больше вышедших водителей
    (№77, dispatch.build_crewed): отмеченные машины без водителя сегодня — в unmanned."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    codes = payload.get('trucks')
    if not isinstance(codes, list) or len(codes) > _MAX_TRUCKS or not all(isinstance(c, str) for c in codes):
        return _bad_request({'trucks': 'ожидался список машин'})
    state = _state()
    bundle = _bundle(state)
    dd = _load_day(state, bundle, day)
    if dd.ctx is None:
        return _bad_request({'_': 'Сначала укажите склад и тоннаж с расходом машин в настройках'})
    if dd.draft is not None and dd.draft.approved is not None:   # №73: полная пересборка — только после снятия
        return _bad_request({'_': PLAN_APPROVED})
    unknown = sorted(set(codes) - set(dd.ready))
    if unknown:
        return _bad_request({'trucks': 'машина не готова к расчёту: ' + ', '.join(unknown)})
    if not codes:
        return _bad_request({'trucks': 'отметьте хотя бы одну машину'})
    # №78: загруженный рейс пересборка не трогает — его машина обязана остаться в плане
    gone = sorted({t.truck for t in (dd.draft.trips if dd.draft is not None else ()) if t.loaded is not None} - set(codes))
    if gone:
        return _bad_request({'trucks': LOADED_TRUCK_OFF + ', '.join(gone)})
    # первая сборка дня: черновик начинается с правила менеджеров из настроек (№69) — по нему и точки дня без черновика
    base = dd.draft if dd.draft is not None else dp.Draft(agents_off=dp.agents_off_of(None, bundle.settings),
                                                          fleet=dp.FleetRule.from_settings(bundle.settings).to_json())
    cargo = dp.loaded_cargo(base, dd.stops)   # №78: что уже в машинах — до смены фильтра менеджеров
    if 'agents_off' in payload:
        # фильтр «Մենեջերներ» до первой сборки живёт на странице — приходит со сборкой; точки дня — по нему
        off = dp.parse_agents(payload['agents_off'])
        if off is None:
            return _bad_request({'agents_off': 'ожидался список менеджеров'})
        if off != base.agents_off:
            base.agents_off = off
            dd = _load_day(state, bundle, day, draft=base, rev=dd.rev)
    started = time.perf_counter()
    # первая сборка дня: перенесённые сюда заказы прошлого дня — сразу в развозе; машин — не больше вышедших водителей (№77)
    timing: dict[str, Any] = {}
    draft = dp.build_crewed(dd.ctx, dd.stops, base, codes, _now(), _crew(state, day, bundle.trucks)[0], timing)
    # №78: новый фильтр менеджеров снял точки загруженного рейса — только с подтверждением логиста, без него не сохраняется
    if payload.get('confirm_loaded') is not True and dp.loaded_cargo(draft, dd.stops, cargo) != cargo:
        return _loaded_confirm()
    # отметка сборки: все заказы дня (и исключённые — они не «новые») + добавленные заказы прошлых дней
    inside = _backlog_in(draft, dd.carried)
    draft.built_orders = dp.order_marks([*dd.deliver, *(o for o in dd.backlog if o.isn in inside)])
    draft.overtime = dp.runs_late(dd.ctx, dd.stops, draft)
    _capture_prediction(dd, draft)
    # сборка идёт секунды: черновик, изменённый тем временем в другой вкладке (утверждён — №80: выпущен на терминалы),
    # не затирается старой основой — 409, как у правок
    rev = state.store.save_dispatch(day.isoformat(), draft.to_json(), session.get('username'), expected_rev=dd.rev)
    if rev is None:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    logger.info('[Routes] Развоз на %s собран (%s) за %.1f с: точек %d, рейсов %d, машин %d (без водителя %d, проб %d; '
                'отдельный рейс: проб %d, %.1f с)', day, session.get('username'), time.perf_counter() - started, len(dd.stops),
                len(draft.trips), len(codes), len(draft.unmanned), timing.get('trials', 0), timing.get('solo_trials', 0),
                timing.get('solo_seconds', 0.0))
    dd.draft, dd.rev = draft, rev or 0
    return jsonify({'success': True, **_dispatch_page_body(dd)})


@bp.post('/api/routes/dispatch/edit')
@_api
def api_dispatch_edit() -> Any:
    """Правка логиста: {"date", "rev", "action": move | trip_stops | stop_rule | until | pin | unpin | exclude | include | defer_store | agents | defer_trip | resize | undo, …}
    (dispatch.apply_edit; until — «Մինչև ժամը», _until_edit: в ответе ещё until — dispatch.fit_until).
    rev — номер черновика, от которого правка: план изменён в другой вкладке — 409.
    В ответе — день целиком и delta_km: как изменились км плана. resize с "preview": true — только подсказка во время
    перетаскивания (ничего не сохраняется): {"preview": {delta_km, stops — сколько точек у машины рейса прибавилось
    (минус — ушло), return — возвращение рейса «HH:MM» или null — рейса больше нет}}."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    state = _state()
    bundle = _bundle(state)
    dd = _load_day(state, bundle, day)
    if dd.draft is None or dd.ctx is None:
        return _conflict('Сначала соберите рейсы')
    if payload.get('rev') != dd.rev:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    info = _stop_info(dd)
    view_before = dp.plan_view(dd.ctx, dd.stops, dd.draft, info, explain=False)
    km_before = view_before['summary']['km']
    if payload.get('preview') is True and payload.get('action') == 'resize':
        return _resize_preview(dd, payload, info, view_before)
    workdays = bundle.settings['workdays']
    deferred_before, later_before = set(dd.draft.deferred), dict(dd.draft.later)
    trips_before = {t.id for t in dd.draft.trips}   # №73: рейсы, появившиеся при правке утверждённого плана, — закрепить
    cargo = dp.loaded_cargo(dd.draft, dd.stops)     # №78: что уже в машинах — до правки (правки меняют черновик на месте)
    confirmed = payload.get('confirm_loaded') is True
    rule: _StopRule | None = None   # «×» с правилом: правило пишется в транзакции черновика
    until: dict[str, Any] | None = None   # «Մինչև ժամը»: успевает ли магазин, подсказка машины (dp.fit_until)
    try:
        if payload.get('action') == 'defer_trip':
            _check_defer_same_day(dd, payload.get('trip'))
        if payload.get('action') == 'defer_store':
            _check_defer_store(dd, payload.get('customer_id'))
        picked = _check_take_started(dd, payload) if payload.get('action') == 'include' else set()
        if payload.get('action') in ('approve', 'unapprove', 'send', 'discard'):   # утверждение (№73), отправка (№81)
            draft = _approve_edit(dd, payload)
            if payload.get('action') == 'discard':   # рейсы снимка — не «новые рейсы утверждённого плана» (keep_approved)
                trips_before = {t.id for t in draft.trips}
        elif payload.get('action') in ('loaded', 'unloaded'):   # «Բեռնված է» логистом (№78)
            draft = _loaded_edit(dd, payload)
        elif payload.get('action') == 'apply_settings':   # день — по нынешним правилам настроек (№69, №74)
            draft = _apply_settings_edit(state, bundle, dd)
        elif payload.get('action') == 'stop_rule':        # «×» у магазина с причиной-правилом на все дни
            rule = _stop_rule_edit(state, bundle, dd, payload, confirmed)
            draft, bundle = rule.draft, rule.bundle
        elif payload.get('action') == 'until':            # «Մինչև ժամը»: срок магазина и порядок его рейсов под него
            rule, until = _until_edit(state, bundle, dd, payload)
            draft, bundle = rule.draft, rule.bundle
        elif _is_same_day_edit(dd, payload):    # новые заказы дня (№72)
            draft = _same_day_edit(state, bundle, dd, payload)
        else:
            draft = dp.apply_edit(dd.ctx, dd.stops, dd.draft, payload, {o.isn for o in dd.deliver},
                                  {o.isn for o in dd.backlog},
                                  defer_since=_defer_target(day, workdays, dp.holidays_of(bundle.settings))[1],
                                  carried=dd.carried, now_min=_today_min(dd), loaded_ok=confirmed,
                                  defer_to=_defer_days(dd))
    except dp.LoadedEdit:   # №78: товар уже в машине — страница спрашивает и повторяет с confirm_loaded
        return _loaded_confirm()
    except dp.DispatchError as e:
        return _bad_request({'_': str(e)})
    if day < _clock().date() and (draft.deferred != deferred_before or draft.later != later_before):
        # перенос с прошедшего дня меняет развоз уже другого дня — задним числом нельзя
        return _bad_request({'_': 'Прошедший день — перенос на другой день не меняется'})
    # точки дня после правки («не везём сегодня», фильтр «Մենեջերներ», вернуть меняют точки и вес): по ним — рейсы
    # черновика (магазин без заказов уходит и из сохранённого плана: его читают обучение и приложение водителя),
    # отметка дня и прогноз
    dd = _load_day(state, bundle, day, draft=draft, rev=dd.rev)
    dp.prune(draft, dd.stops)
    # «Տանել այսօր» заказа прошлых дней — сразу в рейс (владелец 08.10); прошедший день — как было: рейсы уже проехали
    if payload.get('action') == 'include' and day >= _clock().date():
        dp.place_added(dd.ctx, dd.stops, draft, [o.customer_id for o in dd.backlog if o.isn in picked],
                       now_min=_today_min(dd))
    # №78: груз загруженного рейса изменился (любая правка: фильтр менеджеров, настройки дня, заказы дня, перенос тяжёлого
    # заказа соседнего рейса…) — только с подтверждением логиста; без него ничего не сохраняется
    # №81: «Չեղարկել փոփոխությունները» возвращает отправленный план — склад грузил по нему (_warehouse_body), товар в
    # машине ему и соответствует: не спрашиваем
    if not confirmed and payload.get('action') != 'discard' and dp.loaded_cargo(draft, dd.stops, cargo) != cargo:
        return _loaded_confirm()
    # №81 + №78: склад грузил по отправленному плану — отправка, меняющая состав загруженного рейса, только с подтверждением
    if not confirmed and payload.get('action') == 'send' and dp.loaded_changed(draft):
        return _loaded_confirm()
    dp.release_same_day_trucks(draft)   # №72: машина, отмеченная взятием заказа дня, без рейсов — снова не отмечена
    dp.keep_approved(draft, trips_before)   # №73: пока план утверждён, новые рейсы тоже закреплены
    draft.overtime = dp.runs_late(dd.ctx, dd.stops, draft)
    _capture_prediction(dd, draft)
    if payload.get('action') in ('approve', 'send'):   # №81: водителям — план в том виде, в каком он сохраняется
        dp.send(draft, _same_day_now().isoformat(timespec='seconds'), session.get('username'))
    try:
        rev = state.store.save_dispatch(day.isoformat(), draft.to_json(), session.get('username'), expected_rev=dd.rev,
                                        also=rule.also if rule is not None else None)
    except dp.DispatchError as e:   # правило не записалось — и черновик тоже (одна транзакция)
        return _bad_request({'_': str(e)})
    if rev is None:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    dd.rev = rev
    body = _dispatch_page_body(dd)
    body['delta_km'] = round(body['plan']['summary']['km'] - km_before, 1) if body['plan'] else None
    if payload.get('action') == 'trip_stops' and payload.get('reason') == 'today':
        logger.info('[Routes] «Развоз» %s: из рейса %s убраны %s — причина «միայն այսօր» (%s)', day, payload.get('trip'),
                    payload.get('remove'), session.get('username'))
    if until is not None:
        body['until'] = until
    elif rule is not None:
        body['rule_kept_days'] = rule.kept   # «никогда»: дни, где магазин уже в загруженной машине, — не тронуты
    return jsonify({'success': True, **body})


@bp.post('/api/routes/dispatch/same-day')
@_api
def api_dispatch_same_day() -> Any:
    """Новые заказы дня (№72): {"date" — сегодня, "orders": [fISN, …]} — как взять их в развоз сегодня все вместе
    (dispatch.same_day_options: самые дешёвые варианты) и кого взять нельзя (blocked — они остаются на завтра, варианты —
    без них; orders — что можно взять). Ничего не сохраняет; выбор — POST /api/routes/dispatch/edit {"action": "same_day",
    "orders": orders ответа, "option": key, "rev"}."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    state = _state()
    bundle = _bundle(state)
    dd = _load_day(state, bundle, day)
    if dd.draft is None or dd.ctx is None:
        return _conflict('Сначала соберите рейсы')
    try:
        with_new, isns, cids, now_min, blocked = _same_day_pick(state, bundle, dd, payload.get('orders'))
    except dp.DispatchError as e:
        return _bad_request({'_': str(e)})
    options = dp.same_day_options(with_new.ctx, dd.stops, with_new.stops, dd.draft, cids, now_min,
                                  _crew(state, day, bundle.trucks)[0])['options'] \
        if cids else []
    # orders — что можно взять (их шлёт «Ընտրել»); blocked — клиенты, которые остаются на завтра, и их заказы
    return jsonify({'success': True, 'rev': dd.rev, 'orders': sorted(isns), 'options': options, 'blocked': blocked})


def _resize_preview(dd: _DispatchDay, payload: Mapping[str, Any], info: Callable[[dp.Stop], dict[str, Any]],
                    view_before: Mapping[str, Any]) -> Any:
    """Подсказка во время перетаскивания конца рейса: тот же resize на копии черновика, без сохранения."""
    trial = dp.Draft.from_json(dd.draft.to_json())
    try:
        trial = dp.apply_edit(dd.ctx, dd.stops, trial, payload, {o.isn for o in dd.deliver}, now_min=_today_min(dd))
    except dp.DispatchError as e:
        return _bad_request({'_': str(e)})
    view = dp.plan_view(dd.ctx, dd.stops, trial, info, explain=False)
    code = next((t.truck for t in dd.draft.trips if t.id == payload.get('trip')), None)

    def stops_of(v: Mapping[str, Any]) -> int:
        return next((t['stops'] for t in v['trucks'] if t['car_code'] == code), 0)

    back = next((tr['return'] for t in view['trucks'] for tr in t['trips'] if tr['id'] == payload.get('trip')), None)
    return jsonify({'success': True, 'preview': {
        'delta_km': round(view['summary']['km'] - view_before['summary']['km'], 1),
        'stops': stops_of(view) - stops_of(view_before), 'return': back}})


@bp.post('/api/routes/dispatch/overtime')
@_api
def api_dispatch_overtime() -> Any:
    """«Везти после конца дня» (форс-мажор, ответ владельца №32): {"date", "rev"} — магазины, не
    поместившиеся до конца рабочего дня, раскладываются по машинам дня с переработкой (dispatch.overtime).
    rev — номер черновика: план изменён в другой вкладке — 409."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    state = _state()
    bundle = _bundle(state)
    dd = _load_day(state, bundle, day)
    if dd.draft is None or dd.ctx is None:
        return _conflict('Сначала соберите рейсы')
    if payload.get('rev') != dd.rev:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    trips_before = {t.id for t in dd.draft.trips}
    draft = dp.overtime(dd.ctx, dd.stops, dd.draft)
    dp.keep_approved(draft, trips_before)   # №73: пока план утверждён, новые рейсы тоже закреплены
    draft.overtime = dp.runs_late(dd.ctx, dd.stops, draft)
    _capture_prediction(dd, draft)
    rev = state.store.save_dispatch(day.isoformat(), draft.to_json(), session.get('username'), expected_rev=dd.rev)
    if rev is None:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    logger.info('[Routes] Развоз на %s: после конца дня (%s), переработка: %s', day, session.get('username'),
                draft.overtime)
    dd = _load_day(state, bundle, day, draft=draft, rev=rev)
    return jsonify({'success': True, **_dispatch_page_body(dd)})


@bp.post('/api/routes/dispatch/reset')
@_api
def api_dispatch_reset() -> Any:
    """«Начать заново»: черновик на дату удаляется (исключения и закрепления — тоже). Утверждённый (№73) и выпущенный на
    терминалы (№80) — нет."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    state = _state()
    draft, rev = _stored_draft(state, day)
    if draft is not None and draft.approved is not None:   # №73: утверждённый план не стирается — сначала снять
        return _bad_request({'_': PLAN_APPROVED})
    if draft is not None and any(t.loaded is not None for t in draft.trips):   # №78: отметки склада не стираются
        return _bad_request({'_': PLAN_LOADED})
    if draft is not None and draft.released is not None:   # №80: план у водителей — стереть значит снять их точки
        return _bad_request({'_': PLAN_RELEASED})
    # склад мог отметить рейс, пока читали: стирается ровно прочитанный черновик (№78)
    if not state.store.delete_dispatch(day.isoformat(), expected_rev=rev):
        return _conflict('План изменили в другой вкладке — обновите страницу')
    dd = _load_day(state, _bundle(state), day)
    return jsonify({'success': True, **_dispatch_page_body(dd)})


def _ai_preview(dd: _DispatchDay, codes: list[str], memo: dict[str, Any]) -> dict[str, Any]:
    """«Что если» для чата: рейсы дня с другим набором машин — та же сборка, что «Վերակազմել երթերը»
    (закреплённые рейсы остаются, исключённые заказы — вне), но только в памяти: ничего не сохраняется.
    Рядом — такая же пересборка с нынешними машинами (раз на вопрос, memo): сохранённый план может содержать ручные
    правки и принятую переработку, сравнивать смену машин честно только с пересборкой."""
    if dd.ctx is None:
        raise ai_chat.SimulationError('settings are incomplete: no depot or no trucks with capacity and fuel')
    unknown = sorted(set(codes) - set(dd.ready))
    if unknown:
        raise ai_chat.SimulationError('not ready or unknown trucks: %s; ready trucks: %s'
                                      % (', '.join(unknown), ', '.join(sorted(dd.ready))))

    def rebuild(trucks: list[str]) -> dict[str, Any]:
        try:
            draft = dp.build(dd.ctx, dd.stops, copy.deepcopy(dd.draft), trucks, _now())
        except dp.DispatchError as e:          # закреплённый рейс машине больше нельзя и т. п.
            raise ai_chat.SimulationError('the planner refused: %s' % e) from None
        return dp.plan_view(dd.ctx, dd.stops, draft, _stop_info(dd), explain=False)

    now = sorted(t for t in (dd.draft.trucks if dd.draft is not None else []) if t in dd.ready)
    same, compare = None, 'no_plan' if not now else 'self' if now == codes else 'unavailable'
    if now and now != codes:
        if 'same' not in memo:                 # раз на вопрос; не вышло — без сравнения, но «что если» отвечаем
            try:
                memo['same'] = ai_chat.simulation_brief(rebuild(now))
            except ai_chat.SimulationError:
                memo['same'] = None
        same = memo['same']
    return ai_chat.simulation_summary(rebuild(codes), codes, same, compare)


AI_SEEN_MAX = 500           # рейсов плана страницы в вопросе AI — как рейсов в черновике (dispatch.MAX_TRIPS)
AI_RETURN_SLACK_MIN = 5     # возвращение рейса на странице и сейчас расходится больше — на экране уже другой план
AI_STALE = 'План или настройки изменились после открытия страницы — обновите страницу'
_RETURN_RE = re.compile(r'^(\d{2}):(\d{2})(?: \(\+(\d+)\))?$')   # возвращение рейса: «17:32», «04:06 (+1)» (dp._hhmm)


def _return_min(text: str | None) -> int | None:
    """Возвращение рейса (dispatch._hhmm) → минуты от полуночи дня доставки; None — None."""
    if text is None:
        return None
    h, m, days = _RETURN_RE.match(text).groups()
    return (int(days or 0) * 24 + int(h)) * 60 + int(m)


def _seen_error(payload: Mapping[str, Any]) -> dict[str, str] | None:
    """Проверка плана, который логист видит на странице (вопрос AI): rev — номер черновика; seen — рейсы плана
    [[id, возвращение «ЧЧ:ММ» | null, over_time], …] не больше AI_SEEN_MAX или null (плана нет). Ключа нет — он не
    проверяется (страница прежней версии)."""
    rev = payload.get('rev')
    if 'rev' in payload and (not isinstance(rev, int) or isinstance(rev, bool)):
        return {'rev': 'номер плана: ожидалось целое число'}
    seen = payload.get('seen')
    if seen is not None and not (isinstance(seen, list) and len(seen) <= AI_SEEN_MAX and all(
            isinstance(x, list) and len(x) == 3 and isinstance(x[0], int) and not isinstance(x[0], bool)
            and (x[1] is None or isinstance(x[1], str) and _RETURN_RE.match(x[1]) is not None)
            and isinstance(x[2], bool) for x in seen)):
        return {'seen': 'рейсы плана на странице: ожидался список [id, возвращение, опаздывает]'}
    return None


def _seen_stale(payload: Mapping[str, Any], body: Mapping[str, Any]) -> bool:
    """План на странице — не тот, что сейчас в ответе дня (body): другой номер черновика, другие рейсы, другая пометка
    «опаздывает» или возвращение рейса расходится больше AI_RETURN_SLACK_MIN минут (рейсы пересчитываются по текущим
    настройкам, заказам и дорогам — план мог «уехать» и без новой сборки). Проверка — только по пришедшим ключам."""
    if 'rev' in payload and payload['rev'] != body['rev']:
        return True
    if 'seen' not in payload:
        return False
    plan, seen = body['plan'], payload['seen']
    if plan is None or seen is None:
        return (plan is None) != (seen is None)
    now = {t['id']: (_return_min(t['return']), t['over_time']) for truck in plan['trucks'] for t in truck['trips']}
    page = {x[0]: (_return_min(x[1]), x[2]) for x in seen}
    if len(page) != len(seen) or page.keys() != now.keys():
        return True
    for tid, (ret, late) in page.items():
        ret_now, late_now = now[tid]
        if late != late_now or (ret is None) != (ret_now is None) or (
                ret is not None and abs(ret - ret_now) > AI_RETURN_SLACK_MIN):
            return True
    return False


@bp.post('/api/routes/dispatch/ask')
@_api
def api_dispatch_ask() -> Any:
    """«Հարցրու AI-ին» (ответ владельца №52): {"date", "question", "history": [{"role", "text"}], "focus"?, "rev"?,
    "seen"?} → {"answer"}. Модель видит тот же ответ дня, что и страница, и ничего не меняет; история — у страницы.
    rev и seen — план на экране (_seen_error): с открытия страницы план или настройки изменились (_seen_stale) — 409
    stale без вызова модели, иначе AI ответил бы не о том плане, что видит логист."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    bad = _seen_error(payload)
    if bad is not None:
        return _bad_request(bad)
    try:
        question, history, focus = ai_chat.parse_request(payload)
        ai_chat.ensure_available()          # без ключа — не читать день зря
        state = _state()
        dd = _load_day(state, _bundle(state), day)
        body = _dispatch_body(dd)
        if _seen_stale(payload, body):
            logger.info('[Routes] AI-вопрос по развозу на %s (%s): план на странице устарел', day, session.get('username'))
            return jsonify({'success': False, 'error': AI_STALE, 'stale': True}), 409
        memo: dict[str, Any] = {}
        result = ai_chat.ask(body, question, history, focus, simulate=lambda codes: _ai_preview(dd, codes, memo))
    except ai_chat.AiError as e:
        return jsonify({'success': False, 'error': str(e)}), e.status
    logger.info('[Routes] AI-вопрос по развозу на %s (%s)', day, session.get('username'))
    return jsonify({'success': True, **result})


@bp.get('/api/routes/dispatch/fact')
@_api
def api_dispatch_fact() -> Any:
    """«План и факт» за прошедшую дату: км фактической раскладки по машинам ERP (каждая — лучшим
    маршрутом) против рейсов программы на тех же машинах и тех же доставках. Накладные экспедитора без
    машины — рейсы закреплённой за ним ручной машины."""
    state = _state()
    day = _parse_day(request.args.get('date'))
    if day is None or day >= _clock().date():
        return _bad_request({'date': 'прошедшая дата в формате ГГГГ-ММ-ДД'})
    if state.fact_loader is None:
        raise ErpError('Загрузчик факта не подключён')
    snap, _ = state.snapshots.cached()
    bundle = _with_garage(state, _bundle(state), day)   # ремонт ֏/км журнала гаража — на тот день
    trucks = _ready_trucks(snap, bundle, active_only=False)
    data = state.fact_loader(day)

    def coord(cid: int) -> Any:
        return evaluate.visit_coord(snap, cid, 0, bundle.geo_overrides, bundle.driver_points)

    points = {d.customer_id: p for d in data.docs if (p := coord(d.customer_id).point) is not None}
    ctx = _dispatch_ctx(state, snap, bundle, day, trucks, list(points.values()), points)
    if ctx is None:
        return _bad_request({'_': 'Сначала укажите склад и тоннаж с расходом машин в настройках'})
    return jsonify({'success': True, 'fact': dp.plan_vs_fact(ctx, data.docs, coord, bundle.van_trucks())})


# тексты для страницы — сразу по-армянски (ответ владельца №58: раздел только на армянском), SERVER_HY их не переводит
WAYBILL_STALE = 'Էջը բացելուց հետո պլանը փոխվել է․ բեռնագիրը չէր համընկնի էկրանի պլանի հետ։ Թարմացրեք էջը։'
WAYBILL_NO_PLAN = 'Այս օրվա երթերը դեռ կազմված չեն'
WAYBILL_NO_SETUP = 'Նախ նշեք պահեստը և մեքենաների տոննաժն ու ծախսը կարգավորումներում'
WAYBILL_NO_TRUCK = 'Այս մեքենան օրվա պլանում չկա — թարմացրեք էջը'
_REV_RE = re.compile(r'^\d{1,9}$')


@bp.get('/api/routes/dispatch/waybill')
@_api
def api_dispatch_waybill() -> Any:
    """Բեռնագիր машины (ответ владельца №57): ?date=ГГГГ-ММ-ДД&truck=код&rev=номер плана на странице → её рейсы по
    порядку плана, в каждом — что грузить на складе (waybill.truck_waybill; строки накладных и заказов — из ERP, только
    чтение). rev не совпал с черновиком или машины нет в плане — 409 stale: накладная разошлась бы с планом на экране
    (состав рейсов страница сверяет сама по basis). Водитель — кто сегодня за рулём (№77, dispatch.crew_view). Ничего не
    сохраняет."""
    state = _state()
    day = _parse_day(request.args.get('date'))
    car = (request.args.get('truck') or '').strip()
    rev = request.args.get('rev')
    errors = {}
    if day is None:
        errors['date'] = 'дата в формате ГГГГ-ММ-ДД'
    if not car or len(car) > 64:
        errors['truck'] = 'Նշեք մեքենայի կոդը'
    if rev is not None and not _REV_RE.match(rev):
        errors['rev'] = 'номер плана: ожидалось целое число'
    if errors:
        return _bad_request(errors)
    dd = _load_day(state, _bundle(state), day)
    if rev is not None and int(rev) != dd.rev:
        return jsonify({'success': False, 'error': WAYBILL_STALE, 'stale': True}), 409
    if dd.draft is None:
        return jsonify({'success': False, 'error': WAYBILL_NO_PLAN}), 409
    if dd.ctx is None:      # склад или машины не настроены — рейсы не посчитать
        return _bad_request({'_': WAYBILL_NO_SETUP})
    plan = dp.plan_view(dd.ctx, dd.stops, dd.draft, _stop_info(dd), explain=False)
    truck = next((t for t in plan['trucks'] if t['car_code'] == car), None)
    if truck is None:
        return jsonify({'success': False, 'error': WAYBILL_NO_TRUCK, 'stale': True}), 409
    logger.info('[Routes] Բեռնագիր %s на %s (%s)', car, day, session.get('username'))
    return jsonify(_waybill_body(state, dd, plan, truck))


def _waybill_body(state: RoutesState, dd: _DispatchDay, plan: Mapping[str, Any],
                  truck: Mapping[str, Any]) -> dict[str, Any]:
    """Ответ Բեռնագիր машины плана дня (общий у «Развоза» и склада): рейсы с товаром (waybill.truck_waybill; строки
    накладных и заказов — из ERP, только чтение), водитель и առաքիչ дня."""
    if state.waybill_loader is None:
        raise ErpError('Загрузчик строк заказов не подключён')
    car = truck['car_code']
    lines = state.waybill_loader([o['isn'] for tr in truck['trips'] for s in tr['stops'] for o in s['orders']])
    seat = dp.crew_view(dd.draft, _crew(state, dd.day, dd.bundle.trucks)[0]).get(car) or {'name': None, 'seat': False,
                                                                                         'warn': None}
    # водитель — кто в этот день за рулём (№77: посаженный сборкой вместо водителя машины — с пометкой driver_seat); не
    # вышел (закреплённый рейс остался) — строка пустая, вписать от руки
    return {'success': True, 'day': dd.day.isoformat(), 'rev': dd.rev, **wb.truck_waybill(plan, car, lines),
            'driver': seat['name'] if seat['warn'] != 'absent' else None,
            **({'driver_seat': True} if seat['seat'] else {}),
            'helper': state.store.truck_drivers(dd.day.isoformat(), 'helper')[0].get(car)}


DRIVER_NO_TRUCK = 'Մեքենան չի գտնվել — թարմացրեք էջը'
CREW_EMPTY = 'Նշեք վարորդին կամ առաքիչին'
CREW_SAME = 'Վարորդն ու առաքիչը նույն մարդն են'
BAD_ONLY_DAY = 'Սերվերը չընդունեց հարցումը'          # only_day не true/false — ошибка страницы, не логиста


@bp.post('/api/routes/dispatch/driver')
@_api
def api_dispatch_driver() -> Any:
    """Водитель и առաքիչ машины для «Բեռնագիր» (ответ владельца №62): {"date", "car_code", "name"?, "only_day"?,
    "helper"?, "helper_only_day"?} — name — водитель, helper — առաքիչ (второй человек; хотя бы один из двух), "" — никого;
    у каждого свой срок: постоянно с этого дня и до следующей смены (прежние дни не меняются) или true — подмена только
    на этот день; прошедший день — всегда подмена (store.save_truck_crew). Водитель и առաքիչ — не один человек. В ответе
    only_day — {роль: подмена ли} по сохранённым ролям.
    Машина — из настроенных (store.trucks). Ответ — водители машин на этот день и все имена (как в ответе дня),
    only_day — изменён только этот день, crew — водители дня (№77, _crew_json). План не меняется: выбор логиста на этот день
    главнее посадки сборки (dispatch.crew_view), пересборка его не меняет."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    car = payload.get('car_code')
    errors = {}
    people: dict[str, tuple[str, bool]] = {}
    # кто пришёл — того и меняем; у каждого свой срок: only_day — водителя, helper_only_day — առաքիչ
    for role, key, day_key in (('driver', 'name', 'only_day'), ('helper', 'helper', 'helper_only_day')):
        one_day = payload.get(day_key, False)
        if not isinstance(one_day, bool):
            errors[day_key] = BAD_ONLY_DAY
        if key in payload:
            value, bad = check_driver_name(payload[key])
            if bad is not None:
                errors[key] = bad
            elif isinstance(one_day, bool):
                people[role] = (value, one_day)
    if not people and not errors:
        errors['name'] = CREW_EMPTY
    if not isinstance(car, str) or not car.strip() or len(car) > 64:
        errors['car_code'] = 'Նշեք մեքենայի կոդը'
    if errors:
        return _bad_request(errors)
    state = _state()
    car = car.strip()
    if car not in state.store.load().trucks:
        return _bad_request({'car_code': DRIVER_NO_TRUCK})
    crew = {role: state.store.truck_drivers(day.isoformat(), role)[0].get(car, '') for role in CREW_TABLES} | {
        role: name for role, (name, _) in people.items()}
    if crew['driver'] and crew['driver'] == crew['helper']:
        return _bad_request({'helper': CREW_SAME})
    past = day < _clock().date()                    # прошедший день — всегда только он
    people = {role: (name, one_day or past) for role, (name, one_day) in people.items()}
    state.store.save_truck_crew(car, day.isoformat(), people, session.get('username'))
    logger.info('[Routes] Машина %s на %s: %s (%s)', car, day,
                ', '.join(f'{role} {"на день" if one_day else "с дня"}' for role, (_, one_day) in people.items()),
                session.get('username'))
    return jsonify({'success': True, 'day': day.isoformat(),
                    'only_day': {role: one_day for role, (_, one_day) in people.items()}, **_drivers_json(state, day),
                    **_crew_json(state, day, _stored_draft(state, day)[0], state.store.load().trucks)})


@bp.get('/api/routes/measurements')
@_api
def api_measurements_get() -> Any:
    from .measurements import summary
    return jsonify({'success': True, **summary(_state().store.measurements())})


def _missing_coordinates(state, snap, bundle):
    since = (snap.today.replace(day=1) - timedelta(days=1)).replace(day=1)
    workdays, off = bundle.settings['workdays'], dp.holidays_of(bundle.settings)
    last_day = dp.next_workday(snap.today, workdays, off)
    # по последний день включительно: заказы, заведённые заранее (№79), везут в их дату
    data = _dispatch_data(state, since - timedelta(days=10), last_day + timedelta(days=1), snap.today, False)
    ids = set(snap.plan.customer_ids)
    day = since
    place = dp.place_of(data.customers, data.addresses)
    while day <= last_day:
        if dp.is_workday(day, workdays, off):
            lo, hi = dp.order_window(day, workdays, off)
            try:
                draft, _ = _stored_draft(state, day)
            except StoreError:
                draft = None
            selected = dp.to_deliver([o for o in data.orders if lo <= o.order_date <= hi], day, lo,
                                     dp.fleet_rule_of(draft, bundle.settings), place)
            agents_off = dp.agents_off_of(draft, bundle.settings)
            ids.update(o.customer_id for o in selected.main if o.agent_id not in agents_off)
        day += timedelta(days=1)
    rows = []
    for cid in sorted(ids):
        if evaluate.visit_coord(snap, cid, 0, bundle.geo_overrides, bundle.driver_points).point is not None:
            continue
        customer = snap.customers.get(cid)
        code, name = data.customers.get(cid, (getattr(customer, 'code', ''), getattr(customer, 'name', '')))
        rows.append({'customer_id': cid, 'code': code, 'name': name, 'address': data.addresses.get(cid, '')})
    return rows


@bp.get('/api/routes/coordinates/missing.csv')
@_api
def api_missing_coordinates() -> Any:
    import csv
    import io
    state = _state()
    bundle = _bundle(state)
    snap, _ = state.snapshots.cached()
    rows = _missing_coordinates(state, snap, bundle)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Հաճախորդի ID', 'Կոդ', 'Անվանում', 'Հասցե', 'Լայնություն', 'Երկայնություն'])
    def safe(value):
        value = str(value or '')
        return "'" + value if value[:1] in ('=', '+', '-', '@', '\t', '\r') else value
    for row in rows:
        writer.writerow([row['customer_id'], safe(row['code']), safe(row['name']), safe(row['address']), '', ''])
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="customers-missing-coordinates.csv"'})


@bp.post('/api/routes/measurements')
@_api
def api_measurements_post() -> Any:
    from .measurements import validate, summary
    raw, error = _json_body()
    if error is not None:
        return error
    state = _state()
    bundle = state.store.load()
    out, errors = validate(raw, bundle.trucks, _clock().date())
    if errors:
        return _bad_request(errors)
    day, code = out.pop('day'), out.pop('car_code')
    old = next((r for r in state.store.measurements() if r['day'] == day and r['car_code'] == code), None)
    if old is not None:
        out['predicted'] = old.get('predicted')
        out['prospective'] = old.get('prospective', False)
        out['prediction_at'] = old.get('prediction_at')
    else:
        stored = state.store.load_dispatch(day)
        prediction = dp.sent_json(stored[0]).get('prediction', {}) if stored is not None else {}   # №81: план водителей
        prediction = prediction if isinstance(prediction, dict) else {}
        out['predicted'] = prediction.get('trucks', {}).get(code)
        out['prospective'] = prediction.get('prospective') is True
        out['prediction_at'] = prediction.get('created_at')
    state.store.save_measurement(day, code, out, session.get('username'))
    return jsonify({'success': True, **summary(state.store.measurements())})


@bp.post('/api/routes/geo-override')
@_api
def api_geo_override() -> Any:
    """Ручная точка клиента: {"customer_id", "lat", "lon"}; lat и lon = null — убрать (снова «авто»: точка
    водителей, ERP, GPS).
    Точка действует во всём разделе: обзор, оптимизация, развоз."""
    payload, error = _json_body()
    if error is not None:
        return error
    if not isinstance(payload, dict) or set(payload) != {'customer_id', 'lat', 'lon'}:
        return _bad_request({'_': 'Սերվերը չընդունեց հարցումը'})
    cid = payload['customer_id']
    if isinstance(cid, bool) or not isinstance(cid, int) or not 0 < cid < 2 ** 31:
        return _bad_request({'customer_id': 'Սպասվում էր հաճախորդի կոդ'})
    lat, lon = payload['lat'], payload['lon']
    point = None
    if lat is not None or lon is not None:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (lat, lon)) \
                or not is_valid_point(lat, lon):
            return _bad_request({'point': 'Կետը Հայաստանից դուրս է'})
        point = (float(lat), float(lon))
    state = _state()
    state.store.save_geo_override(cid, point, session.get('username'))
    logger.info('[Routes] Точка клиента %d %s (%s)', cid, 'поставлена' if point else 'убрана', session.get('username'))
    return jsonify({'success': True})


_EVENT_ID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
GEO_DECISIONS = ('accepted', 'rejected')
GEO_GONE = 'Առաջարկը չի գտնվել կամ արդեն որոշված է — թարմացրեք էջը'


@bp.post('/api/routes/geo-suggest/decide')
@_api
def api_geo_suggest_decide() -> Any:
    """Решение логиста по предложению водителя: {"event_id", "decision": "accepted" | "rejected"}.
    accepted — предложенная точка становится ручной точкой магазина (save_geo_override, как «Փոխել տեղը»);
    rejected — только решение, точка магазина не меняется: если ручная точка магазина уже совпадает с предложенной
    (например, такую же приняли раньше), в ответе manual_same: true — интерфейс подсказывает, что её меняют кнопкой
    «Փոխել տեղը». accepted закрывает остальные открытые предложения того же магазина (superseded — их id). Ручная
    точка сохраняется до фиксации решения (DriverGeo.decide): точка не сохранилась — решения нет; обратное не
    атомарно — точка сохранена, а решение не записалось: предложение остаётся открытым, повторное «принять»
    безопасно. Предложения нет или оно уже решено — 404. Рейсы не пересобираются."""
    payload, error = _json_body()
    if error is not None:
        return error
    if not isinstance(payload, dict) or set(payload) != {'event_id', 'decision'}:
        return _bad_request({'_': 'Սերվերը չընդունեց հարցումը'})
    event_id = payload['event_id']
    if not isinstance(event_id, str) or not _EVENT_ID_RE.match(event_id):
        return _bad_request({'event_id': 'Սերվերը չընդունեց հարցումը'})
    decision = payload['decision']
    if decision not in GEO_DECISIONS:
        return _bad_request({'decision': 'Սերվերը չընդունեց հարցումը'})
    state = _state()
    if state.driver_geo is None:
        return jsonify({'success': False, 'error': GEO_GONE}), 404
    user = session.get('username')

    def apply(sug: dict[str, Any]) -> None:
        if decision == 'accepted':
            state.store.save_geo_override(int(sug['customer_id']), (float(sug['lat']), float(sug['lon'])), user)

    done = state.driver_geo.decide(event_id.lower(), decision, user, apply)
    if done is None:
        return jsonify({'success': False, 'error': GEO_GONE}), 404
    cid = done['customer_id']
    superseded = list(done.get('superseded') or ())
    logger.info('[Routes] Предложение водителя %s по клиенту %s: %s (%s), закрыто вместе с ним: %d', event_id, cid,
                decision, user, len(superseded))
    body: dict[str, Any] = {'success': True, 'customer_id': cid, 'superseded': superseded}
    if decision == 'rejected':
        manual = state.store.load().geo_overrides.get(cid)
        body['manual_same'] = (manual is not None
                               and haversine_km(manual, (float(done['lat']), float(done['lon']))) * 1000 < 1.0)
    return jsonify(body)


@bp.post('/api/routes/customer-window')
@_api
def api_customer_window() -> Any:
    """Окно приёма клиента: {"customer_id", "window": {"kind", "t1", "t2", "tol"} | null} — время в минутах от
    полуночи (store.check_window); null — убрать. Действует только в «Развозе»: сборка, «Везти после конца дня»,
    вид рейсов."""
    payload, error = _json_body()
    if error is not None:
        return error
    if not isinstance(payload, dict) or set(payload) != {'customer_id', 'window'}:
        return _bad_request({'_': 'Սերվերը չընդունեց հարցումը'})
    cid = payload['customer_id']
    if isinstance(cid, bool) or not isinstance(cid, int) or not 0 < cid < 2 ** 31:
        return _bad_request({'customer_id': 'Սպասվում էր հաճախորդի կոդ'})
    window = None
    if payload['window'] is not None:
        window, err = check_window(payload['window'])
        if err:
            return _bad_request({'window': err})
    state = _state()
    state.store.save_customer_window(cid, window, session.get('username'))
    logger.info('[Routes] Окно приёма клиента %d: %s (%s)', cid, window or 'убрано', session.get('username'))
    return jsonify({'success': True})


CUSTOMER_FLAGS = frozenset({'solo', 'center'})   # флажки карточки магазина «Развоза» (№78)


@bp.post('/api/routes/customer-vehicles')
@_api
def api_customer_vehicles() -> Any:
    """Допуск магазина, необязательное окно приёма и время у магазина ("unload_min": целые минуты 1–120 или null —
    по норме; №50; только вместе с "window"): всё сохраняется одной транзакцией. Без "unload_min" время у магазина не
    меняется. {"customer_id", "unload_min"} — только время у магазина («Развоз»): допуск и окно остаются как есть, их не
    пересылают — правка не затрёт параллельную правку условий. Обратное не защищено: форма «Условий магазина» всегда
    присылает "unload_min", поэтому открытая до правки в «Развозе» и сохранённая после неё вернёт прежнее время."""
    payload, error = _json_body()
    if error is not None:
        return error
    # "solo", "center" (№78: отдельный рейс; въезд в центр машинам допуска allow ради магазина; true | false) —
    # необязательны, только вместе с "window" (карточка «Условий магазина»)
    shapes = ({'customer_id', 'access'}, {'customer_id', 'access', 'window'}, {'customer_id', 'access', 'window', 'unload_min'},
              {'customer_id', 'unload_min'})
    if not isinstance(payload, dict) or set(payload) - CUSTOMER_FLAGS not in shapes or any(
            k in payload and ('window' not in payload or not isinstance(payload[k], bool)) for k in CUSTOMER_FLAGS):
        return _bad_request({'_': 'Սերվերը չընդունեց հարցումը'})
    cid = payload['customer_id']
    if isinstance(cid, bool) or not isinstance(cid, int) or not 0 < cid < 2 ** 31:
        return _bad_request({'customer_id': 'Սպասվում էր հաճախորդի կոդ'})
    state = _state()
    snap, _ = state.snapshots.get(allow_stale=True)
    if cid not in snap.customers:
        return _bad_request({'customer_id': 'Խանութը չի գտնվել — թարմացրեք էջը'})
    if 'access' not in payload:     # только время у магазина
        minutes = None
        if payload['unload_min'] is not None:
            minutes, err = check_unload_min(payload['unload_min'])
            if err:
                return _bad_request({'unload_min': err})
        state.store.save_customer_unload(cid, minutes, session.get('username'))
        logger.info('[Routes] Время у магазина %d: %s (%s)', cid, f'{minutes:g} мин' if minutes else 'по норме',
                    session.get('username'))
        return jsonify({'success': True, 'customer_id': cid, 'unload_min': minutes})
    bundle = _bundle(state)
    old = bundle.vehicle_access.get(cid)
    access, err = check_access(payload['access'], set(snap.cars) | set(bundle.trucks) | set(old.trucks if old else ()))
    if err:
        return _bad_request({'access': err})
    window = None
    if 'window' in payload:
        if payload['window'] is not None:
            window, err = check_window(payload['window'])
            if err:
                return _bad_request({'window': err})
        unload: Any = KEEP
        if 'unload_min' in payload:
            unload = None
            if payload['unload_min'] is not None:
                unload, err = check_unload_min(payload['unload_min'])
                if err:
                    return _bad_request({'unload_min': err})
        state.store.save_customer_constraints(cid, access, window, session.get('username'), unload,
                                              payload.get('solo', KEEP), payload.get('center', KEEP))
        logger.info('[Routes] Окно приёма клиента %d: %s (%s)', cid, window or 'убрано', session.get('username'))
        if CUSTOMER_FLAGS & set(payload):
            logger.info('[Routes] Правило магазина %d: отдельный рейс %s, центр машинам допуска %s (%s)', cid,
                        payload.get('solo', '—'), payload.get('center', '—'), session.get('username'))
        if unload is not KEEP:
            logger.info('[Routes] Время у магазина %d: %s (%s)', cid, f'{unload:g} мин' if unload else 'по норме',
                        session.get('username'))
    else:
        state.store.save_customer_vehicles(cid, access, session.get('username'))
    logger.info('[Routes] Допуск машин магазина %d: %s (%s)', cid, access or 'без ограничений', session.get('username'))
    return jsonify({'success': True, 'customer_id': cid, 'access': access.to_json() if access else None})


@bp.get('/api/routes/customer-vehicles')
@_api
def api_customer_vehicles_search() -> Any:
    """Поиск магазина для настройки допуска, в том числе без заказов на выбранный день."""
    query = request.args.get('q', '').strip().casefold()
    if len(query) > 100:
        return _bad_request({'q': 'Որոնում՝ առավելագույնը 100 նիշ'})
    customer_id = request.args.get('customer_id')
    if customer_id is not None:
        if not customer_id.isascii() or not customer_id.isdigit() or len(customer_id) > 10 or not 0 < int(customer_id) < 2 ** 31:
            return _bad_request({'customer_id': 'Սպասվում էր հաճախորդի կոդ'})
        customer_id = int(customer_id)
    state = _state()
    snap, _ = state.snapshots.get(allow_stale=True)
    bundle = _bundle(state)
    customers = [c for cid, c in snap.customers.items() if
                 (cid == customer_id if customer_id is not None else
                  (not query and (cid in bundle.vehicle_access or cid in bundle.windows or cid in bundle.unload_min
                                  or cid in bundle.solo or cid in bundle.center_allow)) or
                  (query and query in f'{c.code} {c.name} {cid}'.casefold()))]
    customers.sort(key=lambda c: (c.name or '', c.id))
    unload = _unload_now(state)
    per_stop, per_tonne = _unload_norms(bundle, unload)
    empty = learning.store_extras(per_stop, {}, unload)    # поле пустое — своё время магазина только по факту
    stats = learning.store_stats(unload)
    norms = {'per_stop_min': per_stop, 'per_tonne_min': per_tonne}
    if learning.store_rule(unload) == 'shrink':   # №66: проверка выбрала сглаживание к группе — подсказка о нём
        norms['store_rule'] = 'shrink'
        stats = {c: (n, fact) for c, (n, fact, _) in learning.store_shrink(unload).items()}
    least = 1 if 'store_rule' in norms else learning.STORE_MIN_OBS   # сглаживание: без введённого — и 1-й визит
    return jsonify({'success': True, 'total': len(customers),
                    'vehicles': [{'car_code': t['car_code'], 'name': t['name']} for t in _trucks_json(snap, bundle)],
                    'unload_norms': norms,
                    'customers': [
        {'customer_id': c.id, 'code': c.code, 'name': c.name,
         'vehicle_access': bundle.vehicle_access[c.id].to_json() if c.id in bundle.vehicle_access else None,
         'window': bundle.windows[c.id].to_json() if c.id in bundle.windows else None,
         'unload_min': bundle.unload_min.get(c.id),
         'solo': c.id in bundle.solo, 'center': c.id in bundle.center_allow,   # №78: отдельный рейс, центр машинам допуска
         # подсказка: сколько «Развоз» возьмёт с пустым полем; разгрузок по GPS — время по ним, введённое не участвует
         'unload_auto_min': round(per_stop + empty.get(c.id, 0.0), 1),
         'unload_visits': stats[c.id][0] if stats.get(c.id, (0, 0.0))[0] >= least else None}
        for c in customers[:30]]})


ROAD_LINES_MAX_POINTS = 3000   # точек во всех линиях одного запроса (день развоза — сотни)


@bp.post('/api/routes/road-lines')
@_api
def api_road_lines() -> Any:
    """Линии рейсов для карты вдоль дорог: {"lines": [[[широта, долгота], …], …]} — точки каждой линии
    по порядку объезда; "avoid_center": true («Развоз») — участки между точками вне малого центра в объезд него, как
    считаются км рейсов (граница — из настроек). Ответ: те же линии по дорогам; "lines": null — карты дорог нет или
    она не загрузилась: карта рисует по прямой."""
    payload, error = _json_body()
    if error is not None:
        return error
    lines = payload.get('lines') if isinstance(payload, dict) else None
    if not isinstance(lines, list) or not all(isinstance(line, list) for line in lines):
        return _bad_request({'lines': 'Սպասվում էր գծերի ցուցակ՝ [լայնություն, երկայնություն] կետերից'})
    avoid_center = payload.get('avoid_center', False)
    if not isinstance(avoid_center, bool):
        return _bad_request({'avoid_center': 'Սպասվում էր true կամ false'})
    parsed: list[list[tuple[float, float]]] = []
    for line in lines:
        points = []
        for p in line:
            if not (isinstance(p, list) and len(p) == 2
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)
                    and is_valid_point(p[0], p[1])):
                return _bad_request({'lines': 'Կետը Հայաստանից դուրս է կամ [լայնություն, երկայնություն] չէ'})
            points.append((float(p[0]), float(p[1])))
        parsed.append(points)
    if sum(map(len, parsed)) > ROAD_LINES_MAX_POINTS:
        return _bad_request({'lines': f'Մեկ հարցման մեջ՝ առավելագույնը {ROAD_LINES_MAX_POINTS:,} կետ'
                                      .replace(',', ' ')})
    state = _state()
    roads = state.roads.get() if state.roads is not None else None
    if roads is not None and avoid_center and not roads.failed:
        zone = tuple((lat, lon) for lat, lon in state.store.load().settings['center_zone'])
        roads = state.roads.bypass(roads, zone)
    out = roads.lines(parsed) if roads is not None else None
    return jsonify({'success': True, 'lines': out})


# --- Обучение по факту машин и «план — факт» (learning-loop-plan.md, этапы 4–5) ---

LEARNING_REPORT_DAYS = 14         # отчёт по умолчанию — две недели до вчера
LEARNING_REPORT_MAX_DAYS = 62
ACTUALS_CACHE_MAX = 3000          # факт машино-дней в памяти (отчёт и ночной прогон не пересчитывают неизменённые дни)
VALHALLA_WAIT_S = 600.0           # обучение ждёт фон Valhalla (матрица грузовика для точек факта) не дольше…
VALHALLA_POLL_S = 5.0             # …проверяя готовность так часто
MAP_TRACK_POINTS = 1500           # трек на карте — упрощённый (Дуглас — Пекер)


def _yerevan_now() -> datetime:
    """Сейчас по Еревану: рабочий день обучения, отчёта и кнопки «Пересчитать» (не часы сервера; тесты подменяют)."""
    return datetime.now(ac.YEREVAN)


@bp.get('/routes/learning')
def learning_page() -> str:
    return render_template('routes_learning.html', yandex_tiles_key=_yandex_tiles_key())


_DayRead = Callable[[str, str, Any, Mapping[str, Any], list[ac.PlanStop], ac.DayActual], None]


def _learning_days(state: RoutesState, bundle: Bundle, since: date, until: date, on_read: _DayRead | None = None,
                   versions: dict[tuple[str, str], Any] | None = None
                   ) -> list[tuple[str, date, list[ac.PlanStop], ac.DayActual, dict[str, Any] | None, int, int]]:
    """Машино-дни с треком за since…until: (машина, день, точки плана, факт, черновик развоза, рейсов и точек машины
    по плану). Место точки в объезде — по черновику «Развоза» дня, без него — порядок /day терминала. Факт дня — из
    кэша, пока не изменились трек, доставки, снимок /day (FleetFacts.version), склад, план машины и окна приёма.
    on_read(машина, день, отпечаток кэша, данные FleetFacts.day, точки, факт) — после чтения машино-дня мимо кэша:
    вызывающий берёт из того же чтения своё (трек «Վարորդներ» — _scorecard_cars), не читая день снова. versions —
    заполняется (машина, день) → отпечаток, с которым посчитан факт каждой возвращённой строки."""
    if state.fleet_facts is None:
        return []
    drafts: dict[str, dict[str, Any] | None] = {}
    # окна приёма дня («Մինչև ժամը» дня — вместо постоянного) и их отпечаток для кэша
    by_day: dict[str, tuple[dict[int, tuple[float, float]], tuple[Any, ...]]] = {}
    out = []
    for car, ds in state.fleet_facts.car_days(since.isoformat(), until.isoformat()):
        if ds not in drafts:
            got = state.store.load_dispatch(ds)
            drafts[ds] = dp.sent_json(got[0]) if got is not None else None   # №81: факт сравниваем с планом водителей
            spans = {cid: w.span() for cid, w in bundle.windows_on(date.fromisoformat(ds)).items()}
            by_day[ds] = (spans, tuple(sorted(spans.items())))
        windows, windows_key = by_day[ds]
        ranks, plan_trips = learning.draft_ranks(drafts[ds], car)
        version = (state.fleet_facts.version(car, ds), bundle.depot, tuple(sorted(ranks.items())), windows_key)
        with state.actuals_lock:
            hit = state.actuals_cache.get((car, ds))
        if hit is not None and hit[0] == version:
            stops, actual = hit[1], hit[2]
        else:
            data = state.fleet_facts.day(car, ds)
            stops = learning.plan_stops(data['stops'], ranks, windows)
            actual = ac.reconstruct(learning.track_fixes(data['track']), stops, bundle.depot)
            with state.actuals_lock:
                state.actuals_cache.pop((car, ds), None)
                state.actuals_cache[(car, ds)] = (version, stops, actual)
                while len(state.actuals_cache) > ACTUALS_CACHE_MAX:
                    state.actuals_cache.pop(next(iter(state.actuals_cache)))
            if on_read is not None:
                on_read(car, ds, version, data, stops, actual)
        if versions is not None:
            versions[(car, ds)] = version
        out.append((car, date.fromisoformat(ds), stops, actual, drafts[ds], plan_trips, len(ranks)))
    return out


_Journal = tuple[list[dict[str, Any]], dict[str, bool]]   # принятые строки learned_norms и переключатели автообучения


def _learning_failed(state: RoutesState) -> None:
    """Выученные нормы не применились (битая строка журнала, база): в журнал сервера и предупреждение на странице
    «Обучение и факт» (снимается, как только нормы снова применились). Вызывать из обработчика исключения."""
    logger.exception('[Routes] Выученные нормы не применены — расчёт по нормам из настроек')
    state.learning_warning = {'at': _yerevan_now().isoformat(timespec='seconds'),
                              'text': 'Սովորած նորմերը չկիրառվեցին (ուսուցման գրառումների սխալ) — «Առաքում» էջը '
                                      'հաշվում է կարգավորումների նորմերով։ Սեղմեք «Վերահաշվել հիմա»։'}


def _learned_journal(state: RoutesState, before: str | None) -> _Journal | None:
    """Принятые строки журнала обучения (прогоны раньше before) и переключатели — одним чтением на расчёт: модель
    времени грузовиков и выученные нормы — из одного состояния журнала. Сбой чтения — None (_learning_failed)."""
    try:
        return state.store.learned(before, accepted_only=True), state.store.learning_auto()
    except Exception:
        _learning_failed(state)
        return None


def _truck_time_choice(journal: _Journal | None) -> tuple[str, str]:
    """(минуты грузовиков «Развоза» — model | valhalla, почему — env | learned | default): переменная окружения
    ROUTES_TRUCK_TIME, если задана, > выбор обучения (вид truck_time, автообучение вида включено) > прежняя модель."""
    learned = None
    if journal is not None and learning.auto_on(journal[1], 'truck_time'):
        learned = learning.truck_time_learned(journal[0])
    return truck_time_source(learned)


def _with_learned(state: RoutesState, norms: Any, tn: fl.TruckNorms, trucks: dict[str, fl.FleetTruck],
                  customers: Mapping[int, Point], journal: _Journal | None, manual: Mapping[int, float] | None = None
                  ) -> tuple[Any, fl.TruckNorms, dict[str, fl.FleetTruck], learning.InEffect]:
    """Действующие выученные нормы журнала journal (_learned_journal) поверх настроек; поправка по часам — только той
    дорожной модели, что у norms (road_model_id). Введённое время магазинов (manual, №50) — всегда: и без журнала, и
    при выключенном автообучении, и при сбое журнала. Сбой (битая строка журнала, база) — расчёт на нормах из настроек:
    «Развоз» не падает (_learning_failed)."""
    if journal is not None:
        try:
            eff = learning.in_effect(*journal, learning.road_model_id(norms), learning.travel_scope(norms))
            if eff or manual:
                norms, tn, trucks = learning.apply_learned(norms, tn, trucks, eff, customers, manual)
            state.learning_warning = None
            return norms, tn, trucks, eff
        except Exception:
            _learning_failed(state)
    if manual:
        norms, tn, trucks = learning.apply_learned(norms, tn, trucks, learning.InEffect(), customers, manual)
    return norms, tn, trucks, learning.InEffect()


def _active_unload(rows: Sequence[Mapping[str, Any]], auto: Mapping[str, bool]) -> Mapping[str, Any] | None:
    """Действующая строка разгрузки — как в расчёте «Развоза» (learning.in_effect: последняя принятая с корректными
    параметрами, автообучение вида включено); нет — None."""
    return learning.in_effect([r for r in rows if r['accepted']], auto, None).unload


def _unload_now(state: RoutesState) -> Mapping[str, Any] | None:
    """Действующая строка разгрузки сейчас (_active_unload по журналу); сбой журнала — None (нормы из настроек)."""
    journal = _learned_journal(state, None)
    return _active_unload(*journal) if journal is not None else None


def _unload_norms(bundle: Bundle, unload: Mapping[str, Any] | None) -> tuple[float, float]:
    """(мин на точку, мин на тонну), которыми «Развоз» считает разгрузку: действующей строки unload, без неё — из
    настроек."""
    if unload:
        return float(unload['per_stop_min']), float(unload['per_tonne_min'])
    s = bundle.settings
    return float(s['unload_min_per_stop']), float(s['unload_min_per_tonne'])


def _valhalla_ready(ctx: dp.DayContext) -> bool:
    """Срез дорог расчёта — Valhalla (матрицы для его точек готовы)."""
    return isinstance(ctx.norms.roads, ValhallaRoads) and ctx.norms.roads.active


def _await_valhalla(state: RoutesState, ctx: dp.DayContext, make: Callable[[], dp.DayContext]) -> dp.DayContext:
    """Контекст расчёта с Valhalla, если фон его ещё готовит: ночной прогон идёт в 03:00, когда матрицу грузовика для
    точек факта никто ещё не запрашивал (ValhallaProvider.get не ждёт — сразу граф OSM; 04.10.2026 так и вышло).
    make — тот же контекст заново (точки уже переданы фону); ждём, пока фон работает, не дольше VALHALLA_WAIT_S."""
    deadline = time.monotonic() + VALHALLA_WAIT_S
    while (not _valhalla_ready(ctx) and state.valhalla is not None and state.valhalla.preparing()
           and time.monotonic() < deadline):
        time.sleep(VALHALLA_POLL_S)
        ctx = make()
    return ctx


def _chain_customers(state: RoutesState, snap: Any, bundle: Bundle, ids: set[int],
                     lookup: bool = True) -> dict[int, str]:
    """Магазины-сети среди ids (№66: своя группа сглаживания): клиент → код группы (CustGrp) из settings chain_groups.
    Группа — из снимка ERP (клиенты плана менеджеров); остальных (в «Развозе» бывают и магазины вне плана) — одним
    запросом state.group_loader (ERP, только чтение) и только если сети заданы; lookup=False — только снимок. Ошибка
    ERP — ErpError (run_learning: этой ночью правило времени магазина не меняется, остальное учится)."""
    chain_set = set(bundle.settings.get('chain_groups') or ())
    if not chain_set or not ids:
        return {}
    groups = {cid: snap.customers[cid].group for cid in ids if cid in snap.customers}
    missing = sorted(ids - set(groups))
    if missing and lookup and state.group_loader is not None:
        groups.update({cid: g for cid, g in state.group_loader(missing).items() if cid in ids})
    return {cid: g for cid, g in sorted(groups.items()) if g in chain_set}


def run_learning(state: RoutesState, today: date) -> list[learning.Outcome]:
    """Прогон обучения за день today (Ереван; ночью — за наступивший день): наблюдения из факта до вчера включительно,
    проверка на последней неделе, итог — в learned_norms (повтор того же дня заменяет). «Действующая норма» для
    сравнения — по прогонам раньше today: повторный прогон даёт тот же итог. Внешние пробки (Яндекс) не запрашиваются;
    трека и заправок ещё нет — ERP не читается, ничего не пишется.

    Время в пути: участки и поправка по часам (travel) — в модели, которой «Развоз» считает грузовики сейчас
    (_truck_time_choice; Valhalla для точек факта не готов — прежняя модель). Выбор модели (truck_time) — обе модели
    на одних и тех же участках, срез дорог с матрицей грузовика (learning.fit_truck_time; автообучение travel
    выключено — «как есть»). Выбранная модель — не та, которой учился этот прогон (только что переключились, env,
    галочка), — рядом пишется и её поправка по часам (travel её scope): перейдёт на неё «Развоз» — поправка уже есть.
    Сравнения нет (Valhalla не готов, Яндекс) — решение этого дня из прежнего прогона не затирается. Чистых участков
    факта хватает на сравнение, а фон Valhalla ещё готовит матрицу грузовика для их точек — прогон её ждёт
    (_await_valhalla); причина строки truck_time — по правде: мало участков, матрица ещё считается или Valhalla нет."""
    if state.fleet_facts is None:
        return []
    bundle = _bundle(state)
    if bundle.depot is None:
        raise dp.DispatchError('Նախ կարգավորումներում նշեք պահեստը')
    train_from, _ = learning.windows(today)
    days = _learning_days(state, bundle, train_from, today - timedelta(days=1))
    refuels = [r for r in state.fleet_facts.refuels()
               if (r.get('eff_date') or r.get('date') or '') >= (train_from - timedelta(days=30)).isoformat()]
    if not days and not refuels:
        logger.info('[Routes] Обучение за %s: трека и заправок машин ещё нет — нечего учить', today)
        return []
    snap, _ = state.snapshots.cached()   # ERP (только чтение) — скорости и машины, как у «Развоза»
    mode = bundle.settings.get('traffic_mode', 'gps')
    calc = replace(bundle, settings={**bundle.settings, 'traffic_mode': 'gps' if mode == 'gps' else 'off'})
    ready = _ready_trucks(snap, bundle, active_only=False)
    points = sorted({s.point for _, _, stops, *_ in days for s in stops if s.point is not None})
    customers = {s.customer_id: s.point for _, _, stops, *_ in days for s in stops
                 if s.point is not None and s.customer_id is not None}
    journal = _learned_journal(state, today.isoformat())
    source = _truck_time_choice(journal)[0]

    def make() -> dp.DayContext | None:
        # нормы без выученного; срез Valhalla — с матрицей грузовика (её минуты нужны обеим моделям сравнения)
        return _dispatch_ctx(state, snap, calc, today, ready, points, customers, learned=False, truck_time=True)
    base = make() if ready else None
    if base is None:
        raise dp.DispatchError('Նախ կարգավորումներում նշեք մեքենաների տոննաժը և ծախսը')
    # чистые участки факта (по дню): мало их — выбирать модель не на чем, и Valhalla ждать незачем
    clean = [day for _, day, _, actual, *_ in days for g in actual.legs if g.clean and g.minutes > 0]
    if mode != 'yandex' and learning.truck_time_short(clean, today) is None:
        base = _await_valhalla(state, base, make)
    variants = {TRUCK_TIME_MODEL: base.norms}   # модель → нормы грузовиков из одного среза дорог
    if _valhalla_ready(base):
        variants = {m: base.norms.for_trucks(truck_time=m == TRUCK_TIME_VALHALLA) for m in learning.TRUCK_TIME_SOURCES}
    base_run = source if source in variants else TRUCK_TIME_MODEL   # как считает «Развоз» (Valhalla не готов — прежняя)
    plain = variants[base_run]
    norms, tn, trucks, eff = _with_learned(state, plain, base.tn, dict(base.trucks), customers, journal,
                                           bundle.unload_min)
    compare = mode != 'yandex' and TRUCK_TIME_VALHALLA in variants
    # своё время магазинов в «действующей норме» — ровно как в расчёте: выученное, у остальных введённое (№50)
    extras = learning.store_extras(tn.unload_min_per_stop, bundle.unload_min, eff.unload)

    def unload_norm(o: learning.UnloadObs) -> float:
        return (tn.unload_min_per_stop * o.n + tn.unload_min_per_tonne * o.tonnes
                + math.fsum(extras.get(c, 0.0) for c in o.customers))

    def city_extra(car: str, cids: Sequence[int]) -> float:
        """Надбавка большой машины car к разгрузке магазинов cids в зоне Еревана (№68, как в плане: tn того же расчёта)."""
        extra = tn.yerevan_of(car)
        return extra * sum(1 for c in cids if c in customers and tn.in_yerevan(customers[c])) if extra else 0.0
    unload: list[learning.UnloadObs] = []
    loads: list[learning.LoadObs] = []
    lunches: list[learning.LunchObs] = []
    trips: list[learning.TripObs] = []
    s = bundle.settings
    work_start = float(int(s['truck_work_start'][:2]) * 60 + int(s['truck_work_start'][3:]))
    lunch_window = tuple(float(int(s[k][:2]) * 60 + int(s[k][3:])) for k in ('truck_lunch_from', 'truck_lunch_to'))
    legs: list[learning.LegObs] = []
    pairs: list[tuple[learning.LegObs, learning.LegObs]] = []
    no_valhalla = 0
    profiles: dict[str, list[tuple[datetime, float, float]]] = {}
    osm = plain.roads.fallback if isinstance(plain.roads, ValhallaRoads) else plain.roads
    # право въезда — у каждой машины с треком (выбор владельца или «JAC» в названии), а не только у готовой к расчёту
    names = {code: c.name for code, c in snap.cars.items()}
    for car, day, stops, actual, draft, *_ in days:
        prediction = (((draft or {}).get('prediction') or {}).get('trucks') or {}).get(car)
        plan = learning.plan_trips(prediction, day)
        # обед по плану (№61): визит магазина и стоянка на складе с обедом — не в разгрузку и загрузку; обед по факту —
        # и там, где он по плану (излишек над действующей нормой разгрузки), и в стороне
        # большая машина в Ереване (№68): надбавка стоит в плане сверху — из стоянки её визитов в зоне она вычитается
        # (иначе своё время магазина и темп машины выучили бы её, и план прибавил бы её дважды); вышло меньше
        # UNLOAD_MIN_OBS — наблюдение остаётся с этим минимумом, а не выпадает: отбрасывать короткие стоянки — смещать
        # выученное вверх (остались бы только длинные)
        for o in learning.unload_obs(day, actual, stops, learning.lunch_customers(plan), car):
            extra = city_extra(car, o.customers)
            unload.append(replace(o, minutes=max(learning.UNLOAD_MIN_OBS, o.minutes - extra)) if extra else o)
        # вне сезона утренней погрузки (№78) первый рейс загружен с вечера: стоянка перед ним — не загрузка
        loads += learning.load_obs(day, actual, stops, plan, preloaded=not dp.morning_loading(day, s))
        meal = learning.lunch_obs(day, actual, lunch_window, stops, plan,   # type: ignore[arg-type]
                                  lambda o, car=car: unload_norm(o) + city_extra(car, o.customers))
        if meal is not None:
            lunches.append(meal)
        driven = actual
        if isinstance(osm, CenterBypassRoads) and bundle.truck_center_ok(car, names.get(car)):
            # машине с правом въезда центр открыт: где расчёт объезжает центр (detour > 1), её путь неизвестен —
            # напрямую или в объезд; км и минуты модели (объезд) с её фактом не сравниваются, иначе поправка времени
            # в пути всех машин занизится
            driven = replace(actual, legs=tuple(g for g in actual.legs if osm.detour(g.pa, g.pb) <= 1.0))
        legs += learning.leg_obs(day, driven, norms, car)
        # запас на рейс (№66): рейсы факта и их минуты по той же модели, что строит план (действующие нормы и темп)
        trips += learning.trip_obs(day, car, actual, stops, norms, tn, bundle.depot, work_start)
        if compare:
            got, missing = learning.truck_time_obs(day, driven, variants[TRUCK_TIME_MODEL])
            pairs += got
            no_valhalla += missing
        profiles.setdefault(car, []).extend(ac.load_profile(actual, stops))
    loading_now = ((tn.warehouse_load_fixed_min, tn.warehouse_load_min_per_tonne)
                   if tn.loading_configured and tn.load(1000) > 0 else None)
    # правило времени магазина (№66): группа — размер по среднему доставленному весу, сети (chain_groups) — своей
    # группой; выбор — от действующей строки (её правило и k — гистерезис)
    ids = {c for o in unload for c in o.customers}
    try:
        chains, rule_switch = _chain_customers(state, snap, bundle, ids), True
    except ErpError:
        # группы магазинов вне плана не получены: прогон не срывается — сети только по снимку, а правило времени
        # магазина (№66) этой ночью не меняется (с неполными сетями сравнение правил не честное)
        logger.warning('[Routes] Обучение: группы магазинов из ERP не получены — правило времени магазина не меняется',
                       exc_info=True)
        chains, rule_switch = _chain_customers(state, snap, bundle, ids, lookup=False), False
    size_kg = (float(bundle.settings['size_small_max_kg']), float(bundle.settings['size_medium_max_kg']))
    outcomes = [
        learning.fit_unload(unload, lambda o: tn.unload_min_per_stop * o.n + tn.unload_min_per_tonne * o.tonnes
                            + math.fsum(extras.get(c, 0.0) for c in o.customers), today, bundle.unload_min,
                            lambda o: tn.unload_min_per_stop * o.n + tn.unload_min_per_tonne * o.tonnes,
                            eff.unload, chains, size_kg, rule_switch=rule_switch),
        learning.fit_loading(loads, today, loading_now),
        learning.fit_lunch(lunches, tn.lunch_minutes, today, float(s['truck_lunch_min'])),
    ]
    model_id = learning.road_model_id(norms)
    # запас на рейс и темп машин (№66): против действующих (запас — того же процентиля q; темп — без него, если нет)
    q = float(s.get('dispatch_buffer_pct') or 0)
    outcomes.append(learning.fit_buffer(trips, today, q, learning.buffer_c_for(eff.buffer, q) or 0.0,
                                        bool(eff.buffer) and float(eff.buffer['q']) != q))
    pace_now = learning.truck_pace(eff, model_id)
    outcomes.append(learning.fit_pace('truck_unload', [(o.day, o.car, unload_norm(o), o.minutes) for o in unload], today,
                                      {c: p[0] for c, p in pace_now.items()}))
    if mode == 'yandex':
        outcomes.append(learning.Outcome('truck_travel', '', False, 'ճանապարհի ժամանակը հաշվում է Յանդեքսը՝ '
                                         'խցանումներով — մեքենայի գործակիցը չի սովորվում'))
    else:
        outcomes.append(learning.fit_pace('truck_travel', [(o.day, o.car, o.current, o.minutes) for o in legs], today,
                                          {c: p[1] for c, p in pace_now.items()}, model_id or 'straight'))
    if mode == 'yandex':
        outcomes.append(learning.Outcome('travel', '', False, 'ճանապարհի ժամանակը հաշվում է Յանդեքսը՝ խցանումներով — '
                                         'ժամային ճշգրտում պետք չէ'))
        decision = learning.Outcome('truck_time', '', False, 'ճանապարհի ժամանակը հաշվում է Յանդեքսը՝ խցանումներով — '
                                    'բեռնատարների ժամանակի մոդելը չի ընտրվում')
    else:
        prev = eff.travel if eff.travel is not None and eff.travel.get('model_id') == model_id else None
        regular = learning.fit_travel(legs, today, model_id or 'straight', learning.model_ref(plain), plain, prev)
        outcomes.append(regular)
        rows, auto = journal if journal is not None else ([], {})
        incumbent = learning.truck_time_learned(rows) or TRUCK_TIME_MODEL
        prevs = {m: learning.in_effect(rows, auto, learning.road_model_id(n), learning.travel_scope(n)).travel
                 for m, n in variants.items()}
        decision, fitted = learning.fit_truck_time(
            pairs, today, incumbent, variants, prevs, no_valhalla, learning.auto_on(auto, 'travel'), {base_run: regular},
            fact=legs, preparing=(TRUCK_TIME_VALHALLA not in variants and state.valhalla is not None
                                  and state.valhalla.preparing()))
        chosen = decision.params['source'] if decision.accepted else incumbent
        if chosen != base_run and chosen in fitted:
            # выбранная модель — не та, которой этот прогон учил «Развоз» (только что переключились, env, галочка):
            # её поправка по часам — в журнал рядом (свой scope), перейдёт на неё «Развоз» — поправка уже есть
            o = fitted[chosen]
            outcomes.append(replace(o, reason=f'ընտրված մոդելի համար («{learning.TRUCK_TIME_TITLES[chosen]}»)՝ '
                                              f'{o.reason}'))
    # сравнения не было (Valhalla ещё не готов, Яндекс) — сравнение этого дня из прежнего прогона не затирается
    if decision.params is not None or not any(r['kind'] == 'truck_time' and r['run_day'] == today.isoformat()
                                              and r['params'] is not None for r in state.store.learned()):
        outcomes.append(decision)
    capacity = {code: t.capacity_kg for code, t in trucks.items()}
    intervals = learning.fuel_intervals(refuels)
    by_car = learning.fuel_obs(intervals, profiles, capacity)
    for car in sorted({iv.car_code for iv in intervals}):
        truck = trucks.get(car)
        if truck is None:
            outcomes.append(learning.Outcome('fuel', car, False, 'մեքենան կարգավորված չէ (տոննաժ և ծախս)'))
            continue

        def fuel_now(load: float, t: fl.FleetTruck = truck) -> float:
            if t.fuel_empty_l_per_100km is None or t.fuel_full_l_per_100km is None:
                return t.l100
            return t.fuel_empty_l_per_100km + (t.fuel_full_l_per_100km - t.fuel_empty_l_per_100km) * load
        outcomes.append(learning.fit_fuel([o for o in by_car.get(car, []) if o.day < today], fuel_now, car))
    state.store.save_learned(today.isoformat(), outcomes)
    logger.info('[Routes] Обучение за %s: машино-дней %d; %s', today, len(days),
                ', '.join(f'{o.kind}{":" + o.scope if o.scope else ""}={"да" if o.accepted else "нет"}'
                          for o in outcomes))
    return outcomes


def run_learning_job(state: RoutesState, today: date, user: str | None) -> bool:
    """Прогон обучения с отметкой статуса (для страницы); уже идёт — False. Ошибка — в журнал и статус. Удачный прогон
    (и «нечего учить») записывает день — по нему ночной поток узнаёт пропущенный прогон (learning.missed_run)."""
    if not state.learning_lock.acquire(blocking=False):
        return False
    try:
        state.learning_job = {'status': 'running', 'started_at': _yerevan_now().isoformat(timespec='seconds'),
                              'by': user}
        try:
            run_learning(state, today)
            state.store.save_learning_last_run(today.isoformat())
            state.learning_job.update(status='done', finished_at=_yerevan_now().isoformat(timespec='seconds'))
        except ErpError:
            logger.warning('[Routes] Обучение не выполнено: ERP недоступна', exc_info=True)
            state.learning_job.update(status='error', finished_at=_yerevan_now().isoformat(timespec='seconds'),
                                      error='ERP տվյալների բազան հասանելի չէ')
        except (StoreError, dp.DispatchError) as e:
            logger.warning('[Routes] Обучение не выполнено: %s', e)
            state.learning_job.update(status='error', finished_at=_yerevan_now().isoformat(timespec='seconds'),
                                      error=str(e))
        except Exception:
            logger.exception('[Routes] Обучение: внутренняя ошибка')
            state.learning_job.update(status='error', finished_at=_yerevan_now().isoformat(timespec='seconds'),
                                      error='Սերվերի ներքին սխալ')
    finally:
        state.learning_lock.release()
    return True


def _learning_status(state: RoutesState, bundle: Bundle) -> list[dict[str, Any]]:
    """Что выучено по каждому виду (расход — по машине): последний прогон, действующая норма (строка журнала или None —
    ручная из настроек), ручная норма, переключатель автообучения (нет выбора — learning.DEFAULT_AUTO, у загрузки —
    выключено). Скорость по часам — поправка модели времени, которой «Развоз» считает грузовики (scope travel),
    дорожная модель — последнего её прогона. У модели времени грузовиков ещё source: какой моделью «Развоз» считает
    сейчас и почему (_truck_time_choice: env, выбор обучения или по умолчанию) и выбор обучения; у разгрузки —
    stores: разгрузка по магазинам (_store_unload)."""
    rows = state.store.learned()
    auto = state.store.learning_auto()
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        latest[(r['kind'], r['scope'])] = r
    source, why = _truck_time_choice(([r for r in rows if r['accepted']], auto))
    scope_travel = TRUCK_TIME_VALHALLA if source == TRUCK_TIME_VALHALLA else ''   # learning.travel_scope
    model = latest.get(('travel', scope_travel), {}).get('model_id')
    s = bundle.settings
    manual: dict[str, Any] = {
        'unload': {'per_stop_min': s.get('unload_min_per_stop'), 'per_tonne_min': s.get('unload_min_per_tonne')},
        'loading': {'fixed_min': s.get('warehouse_load_fixed_min'),
                    'per_tonne_min': s.get('warehouse_load_min_per_tonne')},
        'travel': None,
        'truck_time': None,
        'lunch': {'minutes': s.get('truck_lunch_min'), 'from': s.get('truck_lunch_from'), 'to': s.get('truck_lunch_to')},
        'buffer': {'q': s.get('dispatch_buffer_pct')},     # запас на рейс (№66): процентиль настроек
        'truck_unload': None, 'truck_travel': None,
    }
    keys = [(k, scope_travel if k == 'travel' else '') for k in learning.KINDS if k != 'fuel'] + \
        sorted(k for k in latest if k[0] == 'fuel')
    out = []
    for kind, scope in keys:
        on = learning.auto_on(auto, kind)
        effect = None
        if on:
            effect = next((r for r in reversed(rows) if r['kind'] == kind and r['scope'] == scope and r['accepted']
                           and learning.valid_params(kind, r['params'])
                           and (kind != 'travel' or r['model_id'] == model)), None)
            if kind == 'buffer' and effect is not None and learning.buffer_c_for(
                    effect['params'], float(s.get('dispatch_buffer_pct') or 0)) is None:
                effect = None   # q ≤ 50 или у строки нет c для q настроек — запас не действует (learning.apply_learned)
            elif kind == 'buffer' and effect is not None and float(effect['params']['q']) != float(s['dispatch_buffer_pct']):
                # сменили q: действует c тех же рейсов при новом q (c_by_q) — его и показываем, без покрытия (не проверен)
                p, q_now = effect['params'], float(s['dispatch_buffer_pct'])
                c_now = learning.buffer_c_for(p, q_now)
                typical = p.get('typical_min')
                effect = {**effect, 'params': {
                    'c': c_now, 'q': q_now, 'unchecked': True,
                    **({'typical_min': typical, 'typical_reserve_min': round(fl.trip_reserve(c_now, typical), 1)}
                       if typical is not None else {})}}
        if kind == 'fuel':
            t = bundle.trucks.get(scope)
            man = {'l100': t.fuel_l_per_100km, 'empty_l100': t.fuel_empty_l_per_100km,
                   'full_l100': t.fuel_full_l_per_100km} if t is not None else None
        else:
            man = manual[kind]
        item = {'kind': kind, 'scope': scope, 'title': learning.KIND_TITLES[kind], 'auto': on,
                'auto_chosen': kind in auto, 'default_auto': learning.DEFAULT_AUTO[kind],
                'last': latest.get((kind, scope)), 'in_effect': effect, 'manual': man}
        if kind == 'truck_time':
            item['source'] = {'value': source, 'why': why, 'learned': learning.truck_time_learned(rows)}
        if kind == 'unload':
            item['stores'] = _store_unload(state, bundle, rows, _active_unload(rows, auto))
        out.append(item)
    return out


STORES_SHOWN = 100   # строк в таблице «Разгрузка по магазинам»


def _store_unload(state: RoutesState, bundle: Bundle, rows: Sequence[Mapping[str, Any]],
                  unload: Mapping[str, Any] | None) -> dict[str, Any]:
    """«Разгрузка по магазинам» (№50) для страницы обучения: магазины с введённым временем, со своим временем по факту
    (store_stats последнего пересчёта с корректными параметрами) и со своим временем в действующей строке unload (и у
    строк до №50 без store_stats, и когда последний пересчёт не принят) — по числу визитов: введено, по факту (визитов,
    мин; split — два визита сильно расходятся, ждём третий: learning.store_waits), в расчёте — постоянная часть, которой
    «Развоз» считает сейчас (learning.store_times, как в расчёте; время на груз — сверху). Не больше STORES_SHOWN строк.
    Действует сглаживание к группе (№66, learning.store_rule) — rule 'shrink' и k, магазины и визиты — и из store_shrink
    (без введённого в расчёт идёт и 1-й визит).
    Названия — из снимка ERP, если он уже в памяти (ERP не читается: страница опрашивает статус во время пересчёта);
    снимка нет — без названий."""
    per_stop, per_tonne = _unload_norms(bundle, unload)
    times = learning.store_times(per_stop, bundle.unload_min, unload)
    last = next((r for r in reversed(rows) if r['kind'] == 'unload' and r['params'] is not None
                 and learning.valid_params('unload', r['params'])), None)
    stats = learning.store_stats(last['params'] if last else None)
    waits = learning.store_waits(last['params'] if last else None)
    active = {int(c) for c in (unload or {}).get('store_offsets') or {}} | set(learning.store_stats(unload))
    rule = learning.store_rule(unload)
    if rule == 'shrink':
        shrunk = learning.store_shrink(last['params'] if last else None) or learning.store_shrink(unload)
        stats = {**{c: (n, fact) for c, (n, fact, _) in shrunk.items()}, **stats}
        active |= set(learning.store_shrink(unload))
    cids = sorted(set(bundle.unload_min) | set(stats) | active, key=lambda c: (-stats[c][0] if c in stats else 0, c))
    snap = state.snapshots.peek()
    customers = snap.customers if snap is not None else {}
    out = []
    for cid in cids[:STORES_SHOWN]:
        c = customers.get(cid)
        visits, fact = stats.get(cid, (None, None))
        extra, source = times.get(cid, (0.0, 'norm'))
        out.append({'customer_id': cid, 'code': c.code if c else None, 'name': c.name if c else None,
                    'manual_min': bundle.unload_min.get(cid), 'visits': visits,
                    'fact_min': None if fact is None else max(0.0, fact), 'split': cid in waits,
                    'in_calc_min': round(per_stop + extra, 1), 'source': source})
    return {'per_stop_min': per_stop, 'per_tonne_min': per_tonne, 'min_visits': learning.STORE_MIN_OBS, 'rule': rule,
            'k': unload['store_rule']['k'] if rule == 'shrink' else None,   # type: ignore[index]
            'run_day': last['run_day'] if last else None, 'total': len(cids), 'shown': len(out), 'rows': out}


def _report_range() -> tuple[date, date] | None:
    today = _yerevan_now().date()
    until = _parse_day(request.args.get('to')) if request.args.get('to') else today - timedelta(days=1)
    since = _parse_day(request.args.get('from')) if request.args.get('from') else \
        (until - timedelta(days=LEARNING_REPORT_DAYS - 1) if until else None)
    if since is None or until is None or since > until or (until - since).days >= LEARNING_REPORT_MAX_DAYS:
        return None
    return since, until


def _status_body(state: RoutesState) -> dict[str, Any]:
    return {'status': _learning_status(state, state.store.load()), 'job': dict(state.learning_job),
            'warning': state.learning_warning}


@bp.get('/api/routes/learning')
@_api
def api_learning() -> Any:
    """«Обучение и факт»: план и факт машин по дням (from…to, до LEARNING_REPORT_MAX_DAYS дней) и что выучено.
    ERP не читается: план — сохранённые черновики «Развоза», факт — трек и отметки терминалов (кэш машино-дней)."""
    rng = _report_range()
    if rng is None:
        return _bad_request({'date': f'Ժամանակահատված՝ ՏՏՏՏ-ԱԱ-ՕՕ ամսաթվեր, '
                                     f'առավելագույնը {LEARNING_REPORT_MAX_DAYS} օր'})
    state = _state()
    bundle = _bundle(state)
    days = _learning_days(state, bundle, *rng)
    refuels = state.fleet_facts.refuels() if state.fleet_facts is not None else []
    since = (rng[0] - timedelta(days=60)).isoformat()
    intervals = learning.fuel_intervals([r for r in refuels if (r.get('eff_date') or r.get('date') or '') >= since])
    rows = []
    errors: list[float] = []   # точность планового ETA за период (№87 п. 7)
    erp_capacity = _peek_car_capacity(state)
    for car, day, stops, actual, draft, plan_trips, plan_stops in days:
        prediction = (((draft or {}).get('prediction') or {}).get('trucks') or {}).get(car)
        rows.append(learning.day_report(car, day, actual, stops, prediction, plan_trips, plan_stops,
                                        bundle.truck_capacity(car, erp_capacity),
                                        learning.daily_l100(intervals, car, day)))
        errors += _eta_errors(car, day, stops, actual, draft)
    rows.sort(key=lambda r: (r['day'], r['car_code']), reverse=True)
    return jsonify({'success': True, 'from': rng[0].isoformat(), 'to': rng[1].isoformat(),
                    'connected': state.fleet_facts is not None, 'depot': bundle.depot is not None,
                    'days': rows, 'eta': sc.eta_accuracy(errors), **_status_body(state),
                    'rules': {'holdout_days': learning.HOLDOUT_DAYS, 'train_days': learning.TRAIN_DAYS,
                              'min_gain_pct': round(learning.MIN_GAIN * 100), 'unload_min': learning.UNLOAD_MIN,
                              'loading_min': learning.LOADING_MIN, 'travel_min_test': learning.TRAVEL_MIN_TEST,
                              'truck_time_min': learning.TRUCK_TIME_MIN, 'lunch_min': learning.LUNCH_MIN,
                              'buffer_min': learning.BUFFER_MIN, 'buffer_cap_pct': round(fl.BUFFER_CAP_REL * 100),
                              'pace_unload_min': learning.PACE_UNLOAD_MIN, 'pace_travel_min': learning.PACE_TRAVEL_MIN,
                              'boot_share_pct': round(learning.BOOT_SHARE * 100),
                              'boot_resamples': learning.BOOT_RESAMPLES,
                              'fuel_min_intervals': learning.FUEL_MIN_INTERVALS,
                              'nightly_at': '%02d:%02d' % learning.NIGHTLY_AT}})


@bp.get('/api/routes/learning/status')
@_api
def api_learning_status() -> Any:
    """Лёгкий статус для опроса во время «Пересчитать»: прогон, что выучено, предупреждение (без отчёта по дням)."""
    return jsonify({'success': True, **_status_body(_state())})


def _hm(t: datetime | None) -> str | None:
    return t.astimezone(ac.YEREVAN).strftime('%H:%M') if t is not None else None


@bp.get('/api/routes/learning/day')
@_api
def api_learning_day() -> Any:
    """Карта «план — факт» машины за день (ответ владельца №46): упрощённый трек GPS (≤ MAP_TRACK_POINTS точек), склад,
    точки дня с плановым (на момент сборки) и фактическим временем и опозданием, плановые рейсы (точки по порядку —
    линии по дорогам строит страница через /api/routes/road-lines). ERP не читается."""
    return _day_map()


def _day_map() -> Any:
    """Ответ карты дня ?date=&car= (api_learning_day; ту же карту видит «Ավտոտնակ» — api_garage_day)."""
    day = _parse_day(request.args.get('date'))
    car = request.args.get('car')
    if day is None or not isinstance(car, str) or not car or len(car) > 20:
        return _bad_request({'_': 'Անհրաժեշտ են date=ՏՏՏՏ-ԱԱ-ՕՕ և car'})
    state = _state()
    if state.fleet_facts is None:
        return _bad_request({'_': '«Առաքիչ» բաժինը միացված չէ — փաստ չկա'})
    bundle = _bundle(state)
    days = [d for d in _learning_days(state, bundle, day, day) if d[0] == car]
    data = state.fleet_facts.day(car, day.isoformat())
    track = ac.clean_track(learning.track_fixes(data['track']))
    stops, actual, draft = (days[0][2], days[0][3], days[0][4]) if days else ([], ac.DayActual(0, 0.0, None, None), None)
    # линия как на карте машин (08.10): стоянка — одна точка, езда — без дрожания, по дорогам (track_line)
    truck = bundle.trucks.get(car)
    line = _track_line(state, day, car, truck.capacity_kg if truck is not None else None, track, actual.stays,
                       MAP_TRACK_POINTS, LIVE_TRACK_WAIT_S)   # страница не переспрашивает — ждём привязку (≤ 3 с)
    prediction = (((draft or {}).get('prediction') or {}).get('trucks') or {}).get(car) or {}
    eta = {c[0]: c[1] for t in prediction.get('trips') or () for c in t.get('stops') or ()
           if isinstance(c, list) and len(c) == 2}
    marks = ac.stop_marks(actual, stops, day)
    names = {s.get('stop_id'): s.get('name') for s in data['stops']}
    out_stops = []
    for s in stops:
        mk = marks.get(s.key)
        out_stops.append({'stop_id': s.key, 'customer_id': s.customer_id, 'name': names.get(s.key),
                          'lat': s.point[0] if s.point else None, 'lon': s.point[1] if s.point else None,
                          'rank': s.rank, 'planned_eta': eta.get(s.customer_id),
                          'window': [None if not math.isfinite(x) else x for x in s.window] if s.window else None,
                          'arrive': _hm(mk['arrive']) if mk else None, 'leave': _hm(mk['leave']) if mk else None,
                          'late_min': mk['late_min'] if mk else None, 'early': mk['early'] if mk else None})
    point_of = {s.customer_id: s.point for s in stops if s.point is not None and s.customer_id is not None}
    depot = [bundle.depot[0], bundle.depot[1]] if bundle.depot else None
    planned = []
    for t in (draft or {}).get('trips') or ():
        if isinstance(t, dict) and t.get('truck') == car:
            pts = [list(point_of[c]) for c in t.get('stops') or () if c in point_of]
            if pts:
                planned.append(([depot] if depot else []) + pts + ([depot] if depot else []))
    return jsonify({'success': True, 'date': day.isoformat(), 'car_code': car, 'depot': depot,
                    'track': [[round(p[0], 6), round(p[1], 6)] for p in line], 'track_points': len(track),
                    'km_gps': round(actual.km_gps, 1), 'stops': out_stops, 'planned': planned,
                    'trips': [{'depart': _hm(t.depart), 'return': _hm(t.ret),
                               'load_min': round(t.load_min) if t.load_min is not None else None}
                              for t in actual.trips]})


@bp.post('/api/routes/learning/run')
@_api
def api_learning_run() -> Any:
    """«Пересчитать»: обучение в фоне за сегодняшний день по Еревану (страница опрашивает статус); уже идёт — 409."""
    state = _state()
    if state.fleet_facts is None:
        return _bad_request({'_': '«Առաքիչ» բաժինը միացված չէ — սովորելու տվյալներ չկան'})
    if state.learning_lock.locked():
        return _conflict('Ուսուցումն արդեն ընթանում է')
    today, user = _yerevan_now().date(), session.get('username')
    threading.Thread(target=run_learning_job, args=(state, today, user), name='routes-learning-run',
                     daemon=True).start()
    return jsonify({'success': True, 'started': True})


@bp.post('/api/routes/learning/auto')
@_api
def api_learning_auto() -> Any:
    """Автообучение вида вкл./выкл. {"kind", "auto"}: выключено — действуют ручные настройки."""
    payload, error = _json_body()
    if error is not None:
        return error
    kind = payload.get('kind') if isinstance(payload, dict) else None
    auto = payload.get('auto') if isinstance(payload, dict) else None
    if kind not in learning.KINDS or not isinstance(auto, bool):
        return _bad_request({'_': 'Սպասվում էր {"kind": տեսակ, "auto": true|false}'})
    state = _state()
    state.store.save_learning_auto(kind, auto, session.get('username'))
    return jsonify({'success': True, **_status_body(state)})


# --- «Մեքենաները առցանց» (№76, docs/plans/live-map-plan.md) ---
# Машины на карте сейчас: администратор и роль «Гараж» (и из интернета, как «Ավտոտնակ» — app_v2._garage_path_allowed,
# _public_path_allowed). Факт — из «Առաքիչ» (state.live_facts), расчёт — live.car_view. ERP не читается: имена машин и
# калибровка дорог — из снимка в памяти (SnapshotCache.peek), его нет — без имён и с нормами дорог по умолчанию.

LIVE_CAR_MAX = 20   # номер машины в ?car=


@bp.get('/routes/live')
def live_page() -> str:
    # Подложка — Яндекс (владелец 06.10, «на свой риск»: бесплатный Tiles API, п. 5.1.3, мониторинг транспорта
    # запрещает). Свой ключ ROUTES_YANDEX_LIVE_KEY — чтобы блокировка ключа этой страницы не выключила остальные карты.
    return render_template('routes_live.html',
                           yandex_tiles_key=_yandex_tiles_key('ROUTES_YANDEX_LIVE_KEY') or _yandex_tiles_key())


def _live_day() -> tuple[date | None, Any]:
    """(день ?date=, по умолчанию сегодня по Еревану; None и ответ 400 — дата неверна)."""
    raw = request.args.get('date')
    day = _parse_day(raw) if raw else _yerevan_now().date()
    if day is None:
        return None, _bad_request({'date': 'Ամսաթիվը՝ ՏՏՏՏ-ԱԱ-ՕՕ'})
    return day, None


_monotonic = time.monotonic   # подмена в тестах
LIVE_ROAD_TTL_S = 300.0     # дорожная модель ETA пересобирается не чаще (нормы, выученное, Valhalla дошёл до готовности)
LIVE_ROAD_BACKGROUND = True  # собирать модель в фоне (тесты — сразу)
LIVE_BUDGET_S = 2.0         # на один пересчёт флота: дальше участки «от машины» — запасная модель (без запросов Valhalla)
LIVE_HERE_TTL_S = 60.0      # участок от машины: по (машина, точка ~100 м, магазин) — столько секунд
LIVE_FAIL_TTL_S = 10.0      # …а «не получилось» (Valhalla занят) — недолго
LIVE_ROAD_FAIL_TTL_S = 300.0   # сбой сборки дорожной модели не повторяется на каждом опросе
_budget = threading.local()  # until — момент, после которого пересчёт флота Valhalla не спрашивает
LIVE_CARDS_TTL_S = 10.0     # карточки флота за сегодня — как кэш флота (courier.live.TTL_TODAY_S): опрос 15 с у каждого зрителя


class _LiveRoads:
    """Дорожная модель ETA карты (live.Road.legs/unload) по набору точек дня: собирается в фоне — граф дорог для
    сотни точек при первом обращении считается секунды, опрос карты их ждать не должен: пока модели нет, ETA — запасная
    модель (по прямой × извилистость). Готовая живёт LIVE_ROAD_TTL_S, потом пересобирается (старая работает до замены)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[Any, tuple[float, live.Road | None]] = {}   # None — сборка не удалась (на LIVE_ROAD_FAIL_TTL_S)
        self._building: set[Any] = set()

    def get(self, key: Any, build: Callable[[], live.Road], fallback: live.Road) -> live.Road:
        with self._lock:
            hit = self._items.get(key)
            stale = hit is None or _monotonic() - hit[0] > (LIVE_ROAD_TTL_S if hit[1] is not None else LIVE_ROAD_FAIL_TTL_S)
            start = stale and key not in self._building
            if start:
                self._building.add(key)

        def run() -> None:
            try:
                road = build()
                with self._lock:
                    self._items[key] = (_monotonic(), road)
                    while len(self._items) > 8:   # дни и наборы точек не копятся
                        self._items.pop(next(iter(self._items)))
            except Exception:
                logger.exception('[Routes] Карта машин: дорожная модель ETA не собрана — запасная модель')
                with self._lock:
                    self._items[key] = (_monotonic(), None)   # не повторять на каждом опросе
            finally:
                with self._lock:
                    self._building.discard(key)
        if start:
            if LIVE_ROAD_BACKGROUND:
                threading.Thread(target=run, name='routes-live-road', daemon=True).start()
            else:
                run()
        with self._lock:
            hit = self._items.get(key)
        return hit[1] if hit is not None and hit[1] is not None else fallback


@dataclass(frozen=True)
class _LiveContext:
    rules: live.Rules
    road: live.Road
    depot: Point | None
    plans: dict[str, list[live.PlanTrip]]
    trucks: dict[str, live.TruckSpec]
    names: dict[str, str]
    crew: dict[str, dict[str, str]]       # машина → {'driver': имя, 'helper': имя} по «Վարորդ» / «Առաքիչ» (№62)
    planned: tuple[str, ...]               # машины плана дня
    windows: dict[int, tuple[float, float]] = field(default_factory=dict)   # окна приёма клиентов (№87, late_forecast)
    # плановые линии машин (08.10, _live_plan_routes) — только если план отправлен водителям (sent: Draft.released)
    routes: dict[str, live.PlanRoute] = field(default_factory=dict)
    sent: bool = False
    # линия трека машины по дорогам (08.10, _LiveTracks): (машина, куски линии) → привязанные куски; None — без привязки
    tracks: Callable[[str, Sequence[tl.Chunk]], Mapping[Any, Sequence[tl.TPoint]]] | None = None
    # объяснения диспетчера за день (схема 26, store.live_explanations): машина → объяснения
    explained: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


LIVE_LINES_MAX = 300   # машино-дней плановых линий в памяти (кэш _LivePlanLines)
LIVE_LINES_DAYS = 3    # …и не больше стольких дней


class _LivePlanLines:
    """Плановые линии машин по дорогам (live.RouteGeometry) — кэш по машино-дню: ключ машины — её линии по прямой,
    граница малого центра и версия карты; сменился план одной машины — строится только она. Строит один фоновый поток
    на весь процесс (граф дорог ~100 МБ грузится на время построения): пока он занят, новые сборки ждут следующего
    опроса. Пока линии машины перестраиваются (план поменяли, карта обновилась), отдаются прежние линии того же дня — без
    мигания «по дорогам → по прямой» и пропадающих отклонений; не построились — по прямой (None), повтор — не раньше
    LIVE_ROAD_FAIL_TTL_S. Память: не больше LIVE_LINES_DAYS дней и LIVE_LINES_MAX машино-дней — уходят те, которые дольше
    всего не спрашивали (не самые старые даты: открытый прошлый день не перестраивается на каждом пересчёте); только что
    построенный день не уходит никогда."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (день, машина) → {'key', 'value' (None — сбой), 'at' (когда построено), 'good' — последнее удачное этого дня,
        # 'used' — когда спрашивали последний раз}
        self._slots: dict[tuple[date, str], dict[str, Any]] = {}
        self._busy = False

    def get(self, day: date, wanted: Mapping[str, Any],
            build: Callable[[dict[str, Any]], dict[str, live.RouteGeometry | None]]) -> dict[str, live.RouteGeometry]:
        def serve() -> tuple[dict[str, live.RouteGeometry], dict[str, Any]]:
            out: dict[str, live.RouteGeometry] = {}
            todo: dict[str, Any] = {}
            for car, key in wanted.items():
                slot = self._slots.get((day, car))
                if slot is not None:
                    slot['used'] = _monotonic()
                if slot is not None and slot['key'] == key:
                    if slot['value'] is not None:
                        out[car] = slot['value']
                        continue
                    if _monotonic() - slot['at'] < LIVE_ROAD_FAIL_TTL_S:   # недавний сбой — по прямой, без повтора
                        continue
                todo[car] = key
                if slot is not None and slot.get('good') is not None:
                    out[car] = slot['good']   # прежние линии — пока строятся новые
            return out, todo
        with self._lock:
            out, todo = serve()
            start = bool(todo) and not self._busy
            if start:
                self._busy = True

        def run() -> None:
            try:
                got = build(todo)
            except Exception:
                logger.exception('[Routes] Карта машин: плановые линии по дорогам не построены — по прямой')
                got = {}
            try:
                with self._lock:
                    for car, key in todo.items():
                        value = got.get(car)
                        slot = self._slots.pop((day, car), {})
                        self._slots[(day, car)] = {'key': key, 'value': value, 'at': _monotonic(), 'used': _monotonic(),
                                                   'good': value if value is not None else slot.get('good')}
                    self._trim(day)
            finally:
                with self._lock:
                    self._busy = False
        if not start:
            return out
        if LIVE_ROAD_BACKGROUND:
            threading.Thread(target=run, name='routes-live-lines', daemon=True).start()
            return out
        run()
        with self._lock:
            return serve()[0]


    def _trim(self, built: date) -> None:
        """Под замком: дни и машино-дни, которые дольше всего не спрашивали, — вон; день built остаётся."""
        used: dict[date, float] = {}
        for (d, _), slot in self._slots.items():
            used[d] = max(used.get(d, -math.inf), slot['used'])
        recent = sorted((d for d in used if d != built), key=lambda d: used[d], reverse=True)
        keep = {built, *recent[:LIVE_LINES_DAYS - 1]}
        for k in [k for k in self._slots if k[0] not in keep]:
            del self._slots[k]
        while len(self._slots) > LIVE_LINES_MAX:
            old = min((k for k in self._slots if k[0] != built), key=lambda k: self._slots[k]['used'], default=None)
            if old is None:
                break
            del self._slots[old]


def _plan_geometry(roads: Any, todo: Mapping[str, Any]) -> dict[str, live.RouteGeometry | None]:
    """Линии рейсов машин todo (машина → ключ _LivePlanLines: (линии по прямой, граница центра, версия карты)) по дорогам
    одним построением: каждый участок (магазин → магазин) — своей линией roads.leg_lines, с отметкой, нашёлся ли путь по
    дорогам (RoadNetwork.paths). Участок без пути (точка дальше roads.SNAP_MAX_KM от дороги, пути нет) — по прямой
    (RouteGeometry.straight); прямая дорога, упрощённая до двух точек, — по дорогам; км участка по дорогам — длина его
    линии (RouteGeometry.leg_km: план участка перепробега). Дороги не построились — машине None."""
    legs = list(dict.fromkeys((a, b) for key in todo.values() for line in key[0] for a, b in zip(line, line[1:])
                              if a != b))
    got = roads.leg_lines(legs) if legs else []
    if got is None:
        logger.warning('[Routes] Карта машин: дороги для плановых линий не построились — линии по прямой')
        return {car: None for car in todo}
    drawn = dict(zip(legs, got))
    out: dict[str, live.RouteGeometry | None] = {}
    for car, key in todo.items():
        trips: list[tuple[Point, ...]] = []
        parts: list[tuple[Point, ...]] = []
        straight: list[tuple[Point, Point]] = []
        leg_km: dict[tuple[Point, Point], float] = {}
        for line in key[0]:
            pts: list[Point] = [line[0]]
            for a, b in zip(line, line[1:]):
                road, found = drawn.get((a, b), (None, False)) if a != b else (None, False)
                if road is not None and found and len(road) > 1:
                    parts.append(tuple(tuple(p) for p in road))
                    pts.extend(tuple(p) for p in road[1:])
                    leg_km[(a, b)] = math.fsum(haversine_km(tuple(x), tuple(y)) for x, y in zip(road, road[1:]))
                else:
                    if a != b:
                        straight.append((a, b))
                    pts.append(b)
            trips.append(tuple(pts))
        out[car] = live.RouteGeometry(trips, parts, straight, leg_km)
    return out


LIVE_TRACK_POINTS = 200_000   # вершин привязанных кусков треков в памяти (все машино-дни; ~40 МБ)
LIVE_TRACK_QUEUE = 5_000      # кусков в очереди привязки (больше — уходят самые старые заказы)
LIVE_TRACK_WAIT_S = 3.0       # карта «план — факт» / «Ավտոտնակ» (не опрашивает) ждёт привязку дня не дольше
LIVE_TRACK_WARN_S = 600.0     # сбой привязки пишется в журнал не чаще раза в 10 мин (со счётчиком)


class _LiveTracks:
    """Линии треков машин по дорогам (владелец 08.10: петли и прямые сквозь дома — «не профессионально»): кэш кусков
    езды (track_line.Chunk) по (день, машина, ключ куска). Кусок привязывается к дорогам один раз (track_line.match_chunk:
    Valhalla map matching; путь не найден — его точки, тоже в кэш: повторять бессмысленно); у растущего хвоста дня
    готовые куски не меняются — заново привязывается только последний, а прежняя версия хвоста (тот же первый момент)
    при записи новой уходит из кэша. Заказы — в очередь (не теряются, пока поток занят; не больше LIVE_TRACK_QUEUE),
    её разбирает один фоновый поток на весь процесс. get не ждёт (wait_s=0 — опрос карты машин: где привязки ещё нет,
    линия без неё) или ждёт свои куски не дольше wait_s (страницы, которые линию не переспрашивают). Valhalla нет,
    выключен, сборки нет или сбой движка — ничего не кэшируется (появится — привяжется); сбой — в журнал не чаще
    LIVE_TRACK_WARN_S, со счётчиком. Память: не больше LIVE_TRACK_POINTS вершин — уходят куски, которые дольше всего
    не спрашивали. Свой замок, не live_lock: привязка под ним не идёт."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._items: OrderedDict[tuple[date, str, Any], list[tl.TPoint]] = OrderedDict()
        self._size = 0
        self._tails: dict[tuple[date, str], Any] = {}   # (день, машина) → ключ последнего записанного куска
        self._queue: OrderedDict[tuple[date, str, Any], tuple[tl.Chunk, Callable[..., Any]]] = OrderedDict()
        self._current: tuple[date, str, Any] | None = None
        self._busy = False
        self._failed = 0          # кусков со сбоем привязки с последней записи в журнал
        self._warned = -math.inf

    def get(self, day: date, car: str, parts: Sequence[tl.Chunk],
            match: Callable[[tl.Chunk], tuple[list[tl.TPoint], bool] | None] | None,
            wait_s: float = 0.0) -> dict[Any, list[tl.TPoint]]:
        mine = [c for c in parts if not c.stay and len(c.points) >= tl.MATCH_MIN_POINTS]

        def serve() -> dict[Any, list[tl.TPoint]]:
            out: dict[Any, list[tl.TPoint]] = {}
            for c in mine:
                k = (day, car, c.key)
                hit = self._items.get(k)
                if hit is not None:
                    self._items.move_to_end(k)
                    out[c.key] = hit
            return out
        with self._lock:
            out = serve()
            if match is None:
                return out
            for c in mine:
                k = (day, car, c.key)
                if c.key not in out and k not in self._queue and k != self._current:
                    self._queue[k] = (c, match)
            while len(self._queue) > LIVE_TRACK_QUEUE:
                self._queue.popitem(last=False)
            start = bool(self._queue) and not self._busy
            if start:
                self._busy = True
        if start:
            if LIVE_ROAD_BACKGROUND:
                try:
                    threading.Thread(target=self._work, name='routes-live-tracks', daemon=True).start()
                except Exception:   # поток не стартовал (нет ресурсов) — следующий опрос попробует снова
                    logger.exception('[Routes] Карта машин: поток привязки треков не запущен')
                    with self._lock:
                        self._busy = False
                    return out
            else:
                self._work()
        keys = [(day, car, c.key) for c in mine]
        with self._cond:
            if wait_s > 0:
                self._cond.wait_for(lambda: all(k not in self._queue and k != self._current for k in keys), wait_s)
            return serve()

    def _work(self) -> None:
        """Фоновый поток: очередь — по порядку заказов, пока не опустеет. «Свободен» — в той же критической секции, где
        очередь увидена пустой: иначе заказ, пришедший между ними, видел бы «занят» и остался бы в очереди без потока."""
        done = False
        try:
            while True:
                with self._cond:
                    if not self._queue:
                        self._busy, self._current, done = False, None, True
                        self._cond.notify_all()
                        return
                    k, (chunk, match) = self._queue.popitem(last=False)
                    self._current = k
                try:
                    got = match(chunk)
                except Exception as exc:   # сбой движка — не окончательно: не кэшируем, в журнал — редко, со счётчиком
                    got = None
                    self._fail(exc)
                with self._cond:
                    self._current = None
                    if got is not None:
                        self._put(k, got[0])
                    self._cond.notify_all()
        finally:
            if not done:   # исключение мимо match (не должно быть) — поток всё равно освобождается
                with self._cond:
                    self._busy = False
                    self._current = None
                    self._cond.notify_all()

    def _put(self, k: tuple[date, str, Any], line: list[tl.TPoint]) -> None:
        """Под замком: кусок в кэш; прежняя версия хвоста машино-дня (тот же первый момент) — вон; предел вершин."""
        slot = k[:2]
        prev = self._tails.get(slot)
        if prev is not None and prev != k[2] and prev[1] == k[2][1]:
            old = self._items.pop((*slot, prev), None)
            self._size -= len(old) if old is not None else 0
        if prev is None or k[2][1] >= prev[1]:
            self._tails[slot] = k[2]
        if len(self._tails) > 1000:
            self._tails.clear()
        old = self._items.pop(k, None)
        self._size += len(line) - (len(old) if old is not None else 0)
        self._items[k] = line
        while self._size > LIVE_TRACK_POINTS and len(self._items) > 1:
            self._size -= len(self._items.popitem(last=False)[1])

    def _fail(self, exc: BaseException) -> None:
        with self._lock:
            self._failed += 1
            now = _monotonic()
            if now - self._warned < LIVE_TRACK_WARN_S:
                return
            self._warned, n, self._failed = now, self._failed, 0
        logger.warning('[Routes] Карта машин: привязка трека к дорогам не удалась (%s кусков с прошлой записи): %r — '
                       'линия без привязки', n, exc)


def _track_matcher(state: RoutesState, capacity_kg: float | None
                   ) -> Callable[[tl.Chunk], tuple[list[tl.TPoint], bool] | None] | None:
    """Привязка куска трека машины к дорогам: Valhalla сервера (ValhallaProvider.trace), профиль грузовика с тоннажем
    машины (как матрицы «Развоза»), не вышло — легкового; «путь не найден» — ValhallaError. Valhalla нет или выключен
    (режим osm, нет pyvalhalla или карты) — None (линия без привязки, очередь не растёт)."""
    provider = state.valhalla
    if provider is None or not provider.usable():
        return None
    tries = ((PROFILE_TRUCK, truck_costing(capacity_kg)), (PROFILE_CAR, CAR_COSTING))
    unmatchable = (valhalla_error() or RuntimeError,)
    return lambda c: tl.match_chunk(c, provider.trace, tries, unmatchable)


def _track_line(state: RoutesState, day: date, car: str, capacity_kg: float | None, pts: Sequence[Any],
                stays: Sequence[ac.Stay], max_points: int, wait_s: float = 0.0) -> list[tl.TPoint]:
    """Линия трека машино-дня для карты (track_line): стоянка — одна точка, езда — по дорогам, где уже привязана
    (_LiveTracks: остальное — в фоне, ждём не дольше wait_s), иначе — точки без дрожания."""
    parts = tl.chunks(pts, stays)
    got = state.live_tracks.get(day, car, parts, _track_matcher(state, capacity_kg), wait_s)
    return tl.line(parts, got, max_points)


def _live_plan_routes(state: RoutesState, bundle: Bundle, snap: Any, day: date, draft: dp.Draft,
                      fleet: Mapping[str, Mapping[str, Any]], now: bool = False) -> dict[str, live.PlanRoute]:
    """Плановые линии машин по отправленному плану draft (владелец 08.10: «его маршрут, который дал ему софт»): рейс —
    склад → магазины по порядку плана → склад, как линии «Развоза» (routes_dispatch.js → /api/routes/road-lines с
    avoid_center). Точка магазина — с терминала (что водитель видит в «Առաքիչ»), нет — visit_coord снимка в памяти (ERP
    не читается), нет и её — магазина на линии нет. Линии по дорогам — в фоне (state.live_lines, _plan_geometry); пока не
    готовы впервые, карты нет или дороги сломаны — по прямой (road false: отклонение не считается). Км плана — прогноз
    сборки машины (prediction km), если его рейсы — те же клиенты в том же порядке; иначе длина линий, если все участки
    по дорогам; иначе неизвестно. now — линии по дорогам строятся сразу, без кэша (фон «Երթուղի» в «Վարորդներ»,
    _ScoreRoutes: прошлые дни не держат линии в памяти)."""
    known = {x['customer_id']: (x['lat'], x['lon']) for facts in fleet.values() for x in facts.get('stops') or ()
             if isinstance(x.get('customer_id'), int) and x.get('lat') is not None and x.get('lon') is not None}

    def where(cid: int) -> Point | None:
        if cid in known:
            return known[cid]
        if snap is None:
            return None
        return evaluate.visit_coord(snap, cid, 0, bundle.geo_overrides, bundle.driver_points).point
    depot = bundle.depot
    trips: dict[str, list[list[int]]] = {}
    stops: dict[str, list[tuple[int, Point]]] = {}
    straight: dict[str, list[tuple[Point, ...]]] = {}
    for t in draft.trips:
        pts = [(c, p) for c in t.stops if (p := where(c)) is not None]
        trips.setdefault(t.truck, []).append(list(t.stops))
        stops.setdefault(t.truck, []).extend(pts)
        line = (depot, *(p for _, p in pts), depot) if depot is not None else tuple(p for _, p in pts)
        if pts and len(line) > 1:   # ни одной известной точки магазина — линии рейса нет
            straight.setdefault(t.truck, []).append(line)
    drawn: dict[str, live.RouteGeometry] = {}
    roads = state.roads.get() if state.roads is not None else None
    if roads is not None and not roads.failed and straight:
        zone = tuple((float(lat), float(lon)) for lat, lon in bundle.settings['center_zone'])
        wanted = {car: (tuple(lines), zone, roads.version) for car, lines in straight.items()}
        provider = state.roads

        def build(todo: dict[str, Any]) -> dict[str, live.RouteGeometry | None]:
            return _plan_geometry(provider.bypass(roads, zone), todo)   # type: ignore[union-attr]
        if now:
            drawn = {car: geo for car, geo in build(wanted).items() if geo is not None}
        else:
            drawn = state.live_lines.get(day, wanted, build)
    pred = (draft.prediction or {}).get('trucks') or {}
    out: dict[str, live.PlanRoute] = {}
    for car, lines in straight.items():
        geo = drawn.get(car) or live.RouteGeometry(lines, (), [(a, b) for line in lines for a, b in zip(line, line[1:])])
        p = pred.get(car) if isinstance(pred.get(car), Mapping) else {}
        same = [[c[0] for c in tr.get('stops') or () if isinstance(c, list) and c]
                for tr in p.get('trips') or () if isinstance(tr, Mapping)] == trips[car]
        km = p.get('km') if same and isinstance(p.get('km'), (int, float)) and not isinstance(p.get('km'), bool) else None
        km = float(km) if km is not None else geo.km
        out[car] = live.PlanRoute(geo, tuple(stops[car]), km)
    return out


def _live_road_build(state: RoutesState, bundle: Bundle, snap: Any, calib: Any, day: date, base: live.Road,
                     customers: Mapping[int, Point]) -> live.Road:
    """Дорожная модель «Развоза» для ETA: те же дороги (Valhalla, если готов, иначе граф OSM; в объезд малого центра),
    часовой профиль пробок и выученные поправки, что в расчёте рейсов (_dispatch_ctx), и время у магазина №50/№60
    (введённое и выученное по GPS, иначе норма). Положение машины сейчас в таблицы не кладётся: участок от неё — отдельным
    запросом Valhalla (ValhallaRoads.route), нет Valhalla — запасная модель. Дорог нет — только время у магазина."""
    s = bundle.settings
    journal = _learned_journal(state, None)
    truck_time = _truck_time_choice(journal)[0] == TRUCK_TIME_VALHALLA
    roads = None
    if snap is not None and bundle.depot is not None:
        zone = tuple((lat, lon) for lat, lon in s['center_zone'])
        roads = _roads(state, snap, bundle, [*customers.values(), bundle.depot], truck_time=truck_time, center_zone=zone)
    norms = evaluate.Norms.from_settings(s, calib, roads if roads is not None and not roads.failed else None)
    norms = norms.for_trucks()
    h, m = map(int, s['truck_work_start'].split(':'))
    norms = replace(norms, traffic_weekday=day.weekday(), traffic_start_min=float(h * 60 + m))
    tn = fl.TruckNorms.from_settings(s, lunch=True)
    norms, tn, _, _ = _with_learned(state, norms, tn, {}, customers, journal, bundle.unload_min)

    here_cache: dict[Any, tuple[float, float | None]] = {}   # (точка ~100 м, магазин) → (когда, минуты или None)

    def here_leg(r: Any, a: Point, b: Point, city: bool, minute: float) -> float | None:
        """Участок от машины: не в таблицах — прямой запрос Valhalla без ожидания (занят пул или вышел бюджет пересчёта —
        None, запасная модель); результат помнится по (точка ~100 м, магазин) LIVE_HERE_TTL_S."""
        if not isinstance(r, ValhallaRoads):
            return None
        key = (round(a[0], 3), round(a[1], 3), b)
        hit = here_cache.get(key)
        if hit is not None and _monotonic() - hit[0] < (LIVE_HERE_TTL_S if hit[1] is not None else LIVE_FAIL_TTL_S):
            return hit[1]
        if _monotonic() > getattr(_budget, 'until', math.inf):
            return None
        try:
            got = r.route(a, b, city)
        except Exception:   # участок от машины — лучшая попытка: любой сбой движка не должен ронять флот
            got = None
        out = None
        if got is not None:
            km, raw = got
            speed = km / raw * 60.0 if truck_time else (norms.speed_city_kmh if city else norms.speed_region_kmh)
            out = km / speed * 60.0 if norms.traffic is None else norms.traffic.travel(km, speed, city, norms.traffic_weekday,
                                                                                     minute)
        if len(here_cache) > 500:
            here_cache.clear()
        here_cache[key] = (_monotonic(), out)
        return out

    def legs(a: Point, b: Point, minute: float, here: bool) -> float | None:
        r = norms.roads
        if r is None:
            return None
        city = in_city(a, norms.city_center, norms.city_radius_km) and in_city(b, norms.city_center, norms.city_radius_km)
        if here:
            return here_leg(r, a, b, city, minute)
        if r.km(a, b) is None:   # пары в дорогах нет — по прямой это та же запасная модель, не «дороги»
            return None
        leg = truck_leg_minutes(norms, a, b, minute)
        return leg.valhalla if truck_time and leg.valhalla is not None else leg.model

    def pair(a: Point, b: Point, minute: float) -> tuple[float, float] | None:
        """План участка перепробега (live.detour_legs): км и минуты склад/магазин → магазин/склад по тем же дорогам и
        минутам, что участки ETA (таблицы дорог дня; положения машины тут нет); пары нет — None."""
        r = norms.roads
        if r is None or r.km(a, b) is None:
            return None
        leg = truck_leg_minutes(norms, a, b, minute)
        return leg.km, (leg.valhalla if truck_time and leg.valhalla is not None else leg.model)
    return replace(base, legs=legs if norms.roads is not None else None,
                   unload=lambda p, kg: tn.unload_at(kg, p), pair=pair if norms.roads is not None else None)


LIVE_WEAR_TTL_S = 300.0   # износ ֏/км машин карты (журнал гаража, заправки APK) пересчитывается не чаще
_LIVE_WEAR: dict[date, tuple[float, dict[str, float | None]]] = {}


def _live_wear(state: RoutesState, bundle: Bundle, day: date) -> dict[str, float | None]:
    """Износ ֏/км машин на день — как в расчёте «Развоза» (Bundle.resolved_trucks: цена журнала гаража на этот день, иначе
    ручной, пустой ручной — средняя модели или парка). Журнал и заправки читаются не чаще LIVE_WEAR_TTL_S (пересчёт флота
    — раз в 10 с); сбой журнала — ручной износ (перепробег в ֏ — оценка, карта от него не падает)."""
    with state.live_lock:
        hit = _LIVE_WEAR.get(day)
    if hit is not None and _monotonic() - hit[0] < LIVE_WEAR_TTL_S:
        return hit[1]
    try:
        out = {code: t.wear_amd_per_km for code, t in _with_garage(state, bundle, day).resolved_trucks(()).items()}
    except Exception:
        logger.warning('[Routes] Карта машин: износ журнала гаража не посчитан — ручной', exc_info=True)
        out = {code: t.wear_amd_per_km for code, t in bundle.trucks.items()}
    with state.live_lock:
        _LIVE_WEAR[day] = (_monotonic(), out)
        while len(_LIVE_WEAR) > 4:
            _LIVE_WEAR.pop(next(iter(_LIVE_WEAR)))
    return out


def _live_context(state: RoutesState, day: date,
                  fleet: Mapping[str, Mapping[str, Any]] | None = None, lines_now: bool = False) -> _LiveContext:
    """Настройки, нормы машин, план «Развоза» на день и экипажи — для live.car_view (ERP не читается). fleet — факт
    терминалов дня: точки магазинов для дорожной модели ETA (_LiveRoads). lines_now — плановые линии строятся сразу
    (_live_plan_routes now)."""
    bundle = state.store.load()
    s = bundle.settings
    snap = state.snapshots.peek()
    calib = _calibration(state, snap, s) if snap is not None else None
    norms = evaluate.road_norms(s, calib)
    road = live.Road(norms['detour_factor'][0], norms['speed_city_kmh'][0], norms['speed_region_kmh'][0],
                     (float(s['city_center_lat']), float(s['city_center_lon'])), float(s['city_radius_km']))
    customers = {x['customer_id']: (x['lat'], x['lon']) for facts in (fleet or {}).values()
                 for x in facts.get('stops') or () if isinstance(x.get('customer_id'), int)
                 and x.get('lat') is not None and x.get('lon') is not None}
    if customers and day == _yerevan_now().date():   # прошлые дни: ETA нет (live=False) — модель не собираем
        key = (day, frozenset(customers.items()), bundle.depot)
        road = state.live_roads.get(key, lambda: _live_road_build(state, bundle, snap, calib, day, road, customers), road)
    names = {code: car.name for code, car in snap.cars.items()} if snap is not None else {}
    trucks = {}
    wear = _live_wear(state, bundle, day)   # перепробег в ֏ — с износом машины, как «Развоз»
    for code, t in bundle.trucks.items():
        name = (t.name if t.manual else names.get(code)) or None
        if t.name and code not in names:
            names[code] = t.name
        known = t.center_ok is not None or name is not None   # «авто» без названия (ERP не в памяти) — неизвестно
        trucks[code] = live.TruckSpec(t.capacity_kg, t.fuel_l_per_100km, t.fuel_empty_l_per_100km,
                                      t.fuel_full_l_per_100km, bundle.truck_center_ok(code, name) if known else None,
                                      wear_amd_per_km=wear.get(code), wear_load_amd_per_km=t.wear_load_amd_per_km)
    stored = state.store.load_dispatch(day.isoformat())
    plans: dict[str, list[live.PlanTrip]] = {}
    planned: list[str] = []
    routes: dict[str, live.PlanRoute] = {}
    rules = live.Rules.from_settings(s)
    if stored is not None:
        full = dp.Draft.from_json(stored[0])
        draft = full.for_drivers()   # №81: машины едут по отправленному плану
        if full.overtime_ok:   # №32: логист принял переработку — «не успеет вернуться» только позже её предела (как «Развоз»)
            h, m = map(int, s['truck_overtime_end'].split(':'))
            rules = replace(rules, work_end=float(h * 60 + m))
        by_truck: dict[str, list[list[int]]] = {}
        first: dict[str, dp.DraftTrip] = {}
        for t in draft.trips:
            by_truck.setdefault(t.truck, []).append(list(t.stops))
            first.setdefault(t.truck, t)
        pred = (draft.prediction or {}).get('trucks') or {}
        # №78: вне сезона утренней погрузки первый рейс машины загружен с вечера (не рейс заказов дня — как _timeline)
        off = not dp.morning_loading(day, s)
        plans = {car: live.plan_trips(trips, pred.get(car), day, off and first[car].not_before is None)
                 for car, trips in by_truck.items()}
        # №78: магазины плана машины, куда она въезжает в центр по правилу магазина, — у их заездов тревоги «центр» нет
        for car, trips in by_truck.items():
            mine = frozenset(c for trip in trips for c in trip if c in bundle.center_allow
                             and (r := bundle.vehicle_access.get(c)) is not None and r.mode == 'allow' and car in r.trucks)
            if car in trucks and mine:
                trucks[car] = replace(trucks[car], center_customers=mine)
        planned = list(by_truck)
        if full.released is not None:   # №80/№81: план отправлен водителям — его линия на карте и отклонение от неё
            routes = _live_plan_routes(state, bundle, snap, day, draft, fleet or {}, lines_now)
    ds = day.isoformat()
    drivers, helpers = state.store.truck_drivers(ds)[0], state.store.truck_drivers(ds, 'helper')[0]
    crew = {car: {'driver': drivers.get(car), 'helper': helpers.get(car)} for car in set(drivers) | set(helpers)}
    caps = {code: t.capacity_kg for code, t in bundle.trucks.items()}

    def tracks(car: str, parts: Sequence[tl.Chunk]) -> Mapping[Any, Sequence[tl.TPoint]]:
        return state.live_tracks.get(day, car, parts, _track_matcher(state, caps.get(car)))
    return _LiveContext(rules, road, bundle.depot, plans, trucks, names, crew, tuple(planned),
                        {cid: w.span() for cid, w in bundle.windows_on(day).items()}, routes,
                        stored is not None and full.released is not None, tracks, state.store.live_explanations(ds))


def _live_card(ctx: _LiveContext, day: date, now: datetime, car: str, facts: Mapping[str, Any] | None,
               detail: bool, snap: bool = False) -> dict[str, Any]:
    """Карточка машины (live.car_view). snap — линия трека по дорогам (_LiveTracks): только карточка машины на карте;
    ход дня «Развоза» линию не показывает — привязку не заказывает."""
    crew = ctx.crew.get(car, {})
    tracks = ctx.tracks if snap and detail else None
    return {'car_code': car, 'name': ctx.names.get(car), 'driver': crew.get('driver'), 'helper': crew.get('helper'),
            'planned': car in ctx.planned, 'plan_sent': ctx.sent,
            **live.car_view(day, now, facts or {}, ctx.plans.get(car, []), ctx.trucks.get(car, live.TruckSpec()),
                            ctx.depot, ctx.rules, ctx.road, detail, ctx.windows, ctx.routes.get(car),
                            (lambda parts: tracks(car, parts)) if tracks is not None else None,
                            ctx.explained.get(car, ()))}


def _live_cards(state: RoutesState, day: date) -> tuple[_LiveContext, datetime, dict[str, Any],
                                                         dict[str, dict[str, Any]]]:
    """(контекст, момент расчёта, факт флота, карточки всех машин дня без трека): расчёт переиспользуется, пока тот же
    факт флота (courier.live кэширует его на 10 с) и не старше LIVE_CARDS_TTL_S у сегодняшнего дня — опрос карты у
    нескольких зрителей, детали машины и поток Telegram считают флот один раз. Карточка содержит alerts_log — журнал
    тревог дня (API флота его не отдаёт)."""
    fleet = state.live_facts.fleet(day.isoformat())   # type: ignore[union-attr]
    today = _yerevan_now().date()

    def cached(stale_ok: bool) -> tuple[Any, ...] | None:
        with state.live_lock:
            hit = state.live_cards.get(day)
        if hit is None:
            return None
        if hit[0] is fleet and (day != today or _monotonic() - hit[1] < LIVE_CARDS_TTL_S):
            return hit[2], hit[3], fleet, hit[4]
        return (hit[2], hit[3], hit[0], hit[4]) if stale_ok else None
    got = cached(False)
    if got is not None:
        return got   # type: ignore[return-value]
    with state.live_lock:
        flight = state.live_flight.setdefault(day, threading.Lock())
    if not flight.acquire(blocking=False):   # пересчёт уже идёт: берём прежний результат, а прежнего нет — ждём этот
        got = cached(True)
        if got is not None:
            return got   # type: ignore[return-value]
        flight.acquire()
    try:
        got = cached(False)   # пока ждали — уже пересчитали
        if got is not None:
            return got   # type: ignore[return-value]
        now = _yerevan_now()
        _budget.until = _monotonic() + LIVE_BUDGET_S   # дальше Valhalla не спрашиваем: участки «от машины» — запасная модель
        try:
            ctx = _live_context(state, day, fleet)
            cars = sorted(set(fleet) | set(ctx.planned))
            cards = {car: _live_card(ctx, day, now, car, fleet.get(car), False) for car in cars}
        finally:
            _budget.until = math.inf
        with state.live_lock:
            state.live_cards[day] = (fleet, _monotonic(), ctx, now, cards)
            while len(state.live_cards) > 4:
                state.live_cards.pop(next(iter(state.live_cards)))
            for d in [d for d, lock in state.live_flight.items() if d not in state.live_cards and not lock.locked()]:
                del state.live_flight[d]   # замки дней — вместе с кэшем, не копятся
        return ctx, now, fleet, cards
    finally:
        flight.release()


def _live_head(ctx: _LiveContext, day: date, now: datetime) -> dict[str, Any]:
    r = ctx.rules
    return {'success': True, 'date': day.isoformat(), 'now': now.isoformat(timespec='seconds'),
            'depot': list(ctx.depot) if ctx.depot else None,
            'thresholds': {'speed_kmh': r.speed_kmh, 'speed_sec': r.speed_sec, 'stop_min': r.stop_min,
                           'no_contact_min': r.no_contact_min, 'stale_s': live.STALE_S,
                           'old_apk_silent_min': live.OLD_APK_SILENT_MIN, 'deviation_m': r.deviation_m,
                           'detour_min_km': r.detour_min_km},
            # «Բացատրել» (объяснение отклонения и порядка объезда) — только администратору; сервер проверяет сам
            'can_explain': g.get('user_role') == 'admin',
            'center_zone': [list(p) for p in r.center_zone]}


@bp.get('/api/routes/live')
@_api
def api_live() -> Any:
    """Все машины дня ?date= (по умолчанию сегодня): карточка каждой (live.car_view без трека). Машины — с данными
    терминала за день и машины плана «Развоза»."""
    day, error = _live_day()
    if error is not None:
        return error
    state = _state()
    if state.live_facts is None:
        return _bad_request({'_': '«Առաքիչ» բաժինը միացված չէ — տվյալներ չկան'})
    ctx, now, _, cards = _live_cards(state, day)
    return jsonify({**_live_head(ctx, day, now),
                    'trucks': [{k: v for k, v in card.items() if k != 'alerts_log'} for card in cards.values()]})


@bp.get('/api/routes/live/truck')
@_api
def api_live_truck() -> Any:
    """Одна машина ?car=&date=: карточка + трек дня (упрощённый), точки дня со статусами и журнал тревог."""
    day, error = _live_day()
    if error is not None:
        return error
    car = request.args.get('car')
    if not isinstance(car, str) or not car or len(car) > LIVE_CAR_MAX:
        return _bad_request({'car': 'Անհրաժեշտ է car'})
    state = _state()
    if state.live_facts is None:
        return _bad_request({'_': '«Առաքիչ» բաժինը միացված չէ — տվյալներ չկան'})
    ctx, _, fleet, cards = _live_cards(state, day)
    if car not in cards:
        return jsonify({'success': False, 'error': 'Այս մեքենան այս օրը տվյալներ չունի'}), 404
    now = _yerevan_now()
    return jsonify({**_live_head(ctx, day, now), 'truck': _live_card(ctx, day, now, car, fleet.get(car), True, True)})


# «Բացատրել» (владелец 08.10): диспетчер объясняет отклонение или нарушение порядка объезда — причина и заметка (схема 26).
# Только администратор (_admin_only — вторая линия после гейта app_v2: «Гаражу» карта открыта только на чтение) и только
# JSON (_json_body: форма другого сайта JSON не пошлёт; cookie сессии — SameSite=Lax), как остальные POST раздела.
LIVE_EXPLAIN_GONE = 'Ահազանգը չի գտնվել՝ թարմացրեք էջը'


def _live_forget(state: RoutesState, day: date) -> None:
    """Объяснения дня изменились: карточки флота дня — пересчитать на следующем опросе (не ждать LIVE_CARDS_TTL_S)."""
    with state.live_lock:
        state.live_cards.pop(day, None)


@bp.post('/api/routes/live/explain')
@_admin_only
@_api
def api_live_explain() -> Any:
    """{date, car, kind: deviation | sequence, from, reason, note}: тревога ищется в пересчёте карточки машины — того же
    вида с началом from ± live.EXPLAIN_FROM_TOL (отклонение между опросами сдвигается на несколько точек), ближайшая по
    началу. В базу — её время: начало и конец, у идущей — последняя точка GPS (данные до), а не «сейчас»: случай, который
    придёт позже пачкой старого APK, этим объяснением не покрывается; у порядка объезда — ещё пропущенные точки эпизода
    (live.explains: эпизод разросся — объяснять заново). Нет такой — 404, уже объяснена — 409."""
    payload, error = _json_body()
    if error is not None:
        return error
    if not isinstance(payload, dict):
        return _bad_request({'_': 'Սերվերը չընդունեց հարցումը'})
    note, errors = check_live_explanation(payload.get('kind'), payload.get('reason'), payload.get('note'))
    day = _parse_day(payload.get('date'))
    if day is None or day > _yerevan_now().date():
        errors['date'] = 'Ամսաթիվը՝ ՏՏՏՏ-ԱԱ-ՕՕ, ոչ ապագայից'
    car = payload.get('car')
    if not isinstance(car, str) or not car or len(car) > LIVE_CAR_MAX:
        errors['car'] = 'Անհրաժեշտ է car'
    a_from = live._moment(payload.get('from'))
    if a_from is None:
        errors['from'] = 'Ահազանգի ժամը սխալ է'
    if errors:
        return _bad_request(errors)
    state = _state()
    if state.live_facts is None:
        return _bad_request({'_': '«Առաքիչ» բաժինը միացված չէ — տվյալներ չկան'})
    ctx, _, fleet, cards = _live_cards(state, day)   # type: ignore[arg-type]
    if car not in cards:
        return jsonify({'success': False, 'error': LIVE_EXPLAIN_GONE}), 404
    now = _yerevan_now()
    card = _live_card(ctx, day, now, car, fleet.get(car), True)   # type: ignore[arg-type]
    gap = {id(a): abs(datetime.fromisoformat(a['from']) - a_from) for a in card['alerts_log']
           if a['kind'] == payload['kind'] and a.get('from')}
    hit = min((a for a in card['alerts_log'] if gap.get(id(a), live.EXPLAIN_FROM_TOL * 2) <= live.EXPLAIN_FROM_TOL),
              key=lambda a: gap[id(a)], default=None)
    if hit is None:
        return jsonify({'success': False, 'error': LIVE_EXPLAIN_GONE}), 404
    if 'explained' in hit:
        return jsonify({'success': False, 'error': 'Այս ահազանգն արդեն բացատրված է'}), 409
    to = hit['to'] or (card.get('position') or {}).get('at') or card.get('data_until') or hit['from']
    to = max(to, hit['from'], key=lambda t: datetime.fromisoformat(t))
    skipped = [x['stop_id'] for x in hit.get('skipped') or ()]
    new_id = state.store.add_live_explanation(day.isoformat(), car, hit['kind'], hit['from'], to,   # type: ignore[union-attr]
                                              payload['reason'], note, session.get('username'), skipped)
    _live_forget(state, day)   # type: ignore[arg-type]
    logger.info('[Routes] Карта машин: %s объяснил %s %s %s–%s (%s)', session.get('username'), hit['kind'], car,
                hit['from'], to, payload['reason'])
    return jsonify({'success': True, 'id': new_id})


@bp.post('/api/routes/live/unexplain')
@_admin_only
@_api
def api_live_unexplain() -> Any:
    """«Չեղարկել»: {id} — объяснение снимается, тревога снова считается (активна, если ещё идёт). Нет такого — 404."""
    payload, error = _json_body()
    if error is not None:
        return error
    raw = payload.get('id') if isinstance(payload, dict) else None
    if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
        return _bad_request({'id': 'Անհրաժեշտ է id'})
    state = _state()
    gone = state.store.delete_live_explanation(raw)
    if gone is None:
        return jsonify({'success': False, 'error': 'Բացատրությունը չի գտնվել'}), 404
    day = _parse_day(gone[0])
    if day is not None:
        _live_forget(state, day)
    logger.info('[Routes] Карта машин: %s отменил объяснение #%d (%s %s)', session.get('username'), raw, gone[1], gone[0])
    return jsonify({'success': True})


# --- Ход дня на шкале «Развоза» (ответ владельца №82: как мониторинг Яндекса / Routific live) ---
# Кружок магазина на шкале — по факту терминала «Առաքիչ»: доставлен, частично, отказ, машина на месте, опаздывает
# («не успеет», №87: live.late_forecast — окно приёма или план + late_nowin_min). Только сегодня; данные — тот же расчёт,
# что у онлайн-карты (№76).

PROGRESS_STATE = {'full': 'done', 'covered': 'done', 'partial': 'partial', 'refused': 'refused', 'in_progress': 'here'}


def _iso_hm(iso: Any) -> str | None:
    """«HH:MM» по Еревану из ISO-времени онлайн-карты; нет или битое — None."""
    if not isinstance(iso, str):
        return None
    try:
        return datetime.fromisoformat(iso).astimezone(live.YEREVAN).strftime('%H:%M')
    except ValueError:
        return None


def _day_progress(state: RoutesState, day: date) -> dict[str, dict[str, Any]]:
    """{'trucks': машина → клиент (str) → {'s': done | partial | refused | here | late | pending, 'at': «HH:MM»
    (доставлен — когда, иначе ETA) | None, 'delay': минуты ETA позже плана | None; у late ещё 'late_kind': window | plan и
    'late_min': на сколько позже конца окна / плана}, 'returns': машина → {'eta', 'limit': «HH:MM», 'late_min'} — не
    успевает вернуться на склад до конца рабочего дня (№87)}. Машины без точек терминала — нет."""
    ctx, now, fleet, cards = _live_cards(state, day)
    with state.live_lock:
        hit = _PROGRESS_CACHE.get(day)
    if hit is not None and hit[0] is cards:    # тот же расчёт флота (_live_cards) — тот же ход дня
        return hit[1]
    out: dict[str, dict[str, Any]] = {'trucks': {}, 'returns': {}}
    _budget.until = _monotonic() + LIVE_BUDGET_S   # как у онлайн-карты: дальше участки «от машины» — запасная модель
    try:
        _progress_fill(ctx, day, now, fleet, out)
    finally:
        _budget.until = math.inf
    with state.live_lock:
        _PROGRESS_CACHE[day] = (cards, out)
        while len(_PROGRESS_CACHE) > 4:
            _PROGRESS_CACHE.pop(next(iter(_PROGRESS_CACHE)))
    return out


_PROGRESS_CACHE: dict[date, tuple[Any, dict[str, dict[str, Any]]]] = {}


def _progress_fill(ctx: _LiveContext, day: date, now: datetime, fleet: Mapping[str, Any],
                   out: dict[str, dict[str, Any]]) -> None:
    for car, facts in sorted(fleet.items()):
        stops = [s for s in (facts or {}).get('stops') or () if isinstance(s.get('customer_id'), int)]
        if not stops:
            continue
        card = _live_card(ctx, day, now, car, facts, True)   # ETA каждого магазина — в подробной карточке
        detail = {s['stop_id']: s for s in card.get('stops') or ()}
        here = (card.get('next') or {}).get('stop_id') if (card.get('next') or {}).get('here') else None
        late = {x['customer_id']: x for x in card.get('late') or () if x['late_kind'] != 'return'}
        mine: dict[str, dict[str, Any]] = {}
        for s in stops:
            d = detail.get(s['stop_id'], {})
            st = PROGRESS_STATE.get(s.get('status') or '', 'pending')
            if st == 'pending' and s['stop_id'] == here:
                st = 'here'
            delay = None
            if st in ('pending', 'here') and d.get('eta') and d.get('planned_eta'):
                delay = round((datetime.fromisoformat(d['eta']) - datetime.fromisoformat(d['planned_eta'])).total_seconds() / 60)
            if st == 'pending' and s['customer_id'] in late:
                st = 'late'
            at = _iso_hm(d.get('delivered_at') or s.get('delivered_at')) if st in ('done', 'partial', 'refused') else _iso_hm(d.get('eta'))
            prev = mine.get(str(s['customer_id']))
            # магазин с несколькими накладными — худшее состояние (не доставлено важнее доставленного)
            if prev is None or _PROGRESS_RANK[st] > _PROGRESS_RANK[prev['s']]:
                x = late.get(s['customer_id']) if st == 'late' else None
                mine[str(s['customer_id'])] = {'s': st, 'at': at, 'delay': delay,
                                               **({'late_kind': x['late_kind'], 'late_min': x['over_min']} if x else {})}
        out['trucks'][car] = mine
        back = next((x for x in card.get('late') or () if x['late_kind'] == 'return'), None)
        if back is not None:
            out['returns'][car] = {'eta': _iso_hm(back['eta']), 'limit': _iso_hm(back['limit']), 'late_min': back['over_min']}


# худшее у магазина с несколькими накладными; «машина на месте» важнее «опаздывает» (она уже у него)
_PROGRESS_RANK = {'done': 0, 'partial': 1, 'refused': 2, 'pending': 3, 'late': 4, 'here': 5}


@bp.get('/api/routes/dispatch/progress')
@_api
def api_dispatch_progress() -> Any:
    """Ход дня для шкалы «Развоза» ?date= (только сегодня по Еревану): {'live': bool, 'trucks', 'returns' —
    _day_progress}. Раздела «Առաքիչ» нет или день не сегодня — live false, машин нет (страница не красит кружки)."""
    day, error = _live_day()
    if error is not None:
        return error
    state = _state()
    if state.live_facts is None or day != _yerevan_now().date():
        return jsonify({'success': True, 'live': False, 'trucks': {}})
    return jsonify({'success': True, 'live': True, 'now': _yerevan_now().strftime('%H:%M'),
                    **_day_progress(state, day)})


# --- «Վարորդներ»: показатели водителей за период (как driver analytics Omnitracs / Routific; №83, №87 п. 3 и 7) ---
# Правила — route_optimizer.scorecard. Факт людей — «Առաքիչ» (crew_facts), GPS-факт машин — кэш обучения
# (_learning_days → actuals_cache) и трек (скорость, стоянки — правила карты машин live), план — отправленный водителям
# черновик «Развоза», литры — заправки и норма «Նորմ և փաստ». ERP не читается. В памяти, пока не изменился отпечаток:
# сводка дня (события — в т.ч. трек, снимки /day, «сдал фактически», правка плана, склад, окна приёма, пороги тревог),
# скорость и стоянки машино-дня (отпечаток факта обучения), км треков интервала заправок и литры машино-дня — прошлые
# дни не пересчитываются и не перечитываются. Доступ: администратор и «Гараж» (№87: гаражу — без денег, сервер их не
# отдаёт); APK — своя неделя водителя (week_score, courier GET /api/courier/v1/score).

SCORECARD_DEFAULT_DAYS = 30
SCORECARD_CACHE_DAYS = 400   # сводок дней в памяти; больше — вытесняется самая давняя по записи
SCORECARD_CACHE_CAR_DAYS = 3000   # машино-дней (трек, литры) и интервалов заправок в памяти — как ACTUALS_CACHE_MAX
SCORECARD_ROLES = ('admin', 'garage')   # кто видит «Վարորդներ»; деньги (Կանխիկ) — только admin
SCORE_WEEK_TTL_S = 300.0     # неделя для APK (week_score): готовый расчёт отдаётся терминалам столько секунд
SCORE_WEEK_WAIT_S = 2.0      # запрос, начавший расчёт недели, ждёт его не дольше; остальные — не ждут вовсе
SCORE_WEEK_KEEP = 16         # недель в памяти: прежний расчёт отдаётся, пока считается новый
SCORE_WEEK_RETRY_S = 30.0    # расчёт недели упал — снова не раньше (до того — прежний расчёт или 503 score)
SCORECARD_FORBIDDEN = 'Մուտքն արգելված է'


def _scorecard_role() -> str | None:
    """Роль запроса, если ей открыты «Վարորդներ» (вторая линия после гейта app_v2), иначе None."""
    role = g.get('user_role')
    return role if role in SCORECARD_ROLES else None


def _bounded(cache: dict[Any, Any], key: Any, value: Any, limit: int) -> None:
    """Записать в кэш (вызывать под его замком): новая запись — в конец, сверх limit вытесняются самые давние."""
    cache.pop(key, None)
    cache[key] = value
    while len(cache) > limit:
        cache.pop(next(iter(cache)))


@bp.get('/routes/drivers')
def drivers_page() -> Any:
    if _scorecard_role() is None:
        return jsonify({'success': False, 'error': SCORECARD_FORBIDDEN}), 403
    return render_template('routes_drivers.html')


def _scorecard_range() -> tuple[tuple[date, date] | None, Any]:
    """((с, по), None) или (None, ответ 400): ?from=&to= (по умолчанию — SCORECARD_DEFAULT_DAYS дней по сегодня
    включительно), не длиннее scorecard.MAX_DAYS дней."""
    raw_from, raw_to = request.args.get('from'), request.args.get('to')
    until = _parse_day(raw_to) if raw_to is not None else _yerevan_now().date()
    if raw_from is not None:
        since = _parse_day(raw_from)
    else:
        since = until - timedelta(days=SCORECARD_DEFAULT_DAYS - 1) if until is not None else None
    if since is None or until is None:
        return None, _bad_request({'date': 'Ամսաթվերը՝ ՏՏՏՏ-ԱԱ-ՕՕ'})
    if since > until:
        return None, _bad_request({'date': 'Սկզբի ամսաթիվը չի կարող լինել վերջից ուշ'})
    if (until - since).days + 1 > sc.MAX_DAYS:
        return None, _bad_request({'date': f'Ժամանակահատվածը՝ առավելագույնը {sc.MAX_DAYS} օր'})
    return (since, until), None


def _plan_etas(draft: Mapping[str, Any] | None, car: str, day: date
               ) -> tuple[dict[int, datetime], list[dict[int, datetime]]]:
    """Плановое ETA клиентов машины за день — прогноз сборки отправленного водителям плана: (клиент → ETA первого
    появления (live.plan_trips), по рейсам плана — клиент → ETA в этом рейсе). По рейсам — только если в прогнозе
    столько же рейсов, сколько в плане (логист не менял рейсы после сборки), иначе []."""
    trips = [list(t.get('stops') or ()) for t in (draft or {}).get('trips') or ()
             if isinstance(t, dict) and t.get('truck') == car]
    pred = (((draft or {}).get('prediction') or {}).get('trucks') or {}).get(car)
    pred = pred if isinstance(pred, dict) else None
    first = {c: e for t in live.plan_trips(trips, pred, day) for c, e in t.etas.items()}
    ptrips = [t for t in (pred or {}).get('trips') or () if isinstance(t, Mapping)]
    if len(ptrips) != len(trips):
        return first, []
    midnight = datetime(day.year, day.month, day.day, tzinfo=ac.YEREVAN)
    per_trip = []
    for t in ptrips:
        etas: dict[int, datetime] = {}
        for c in t.get('stops') or ():
            if isinstance(c, list) and len(c) == 2 and isinstance(c[0], int) and not isinstance(c[0], bool) \
                    and c[0] not in etas and (m := learning._hhmm(c[1])) is not None:
                etas[c[0]] = midnight + timedelta(minutes=m)
        per_trip.append(etas)
    return first, per_trip


def _stop_etas(draft: Mapping[str, Any] | None, car: str, day: date, stops: Sequence[ac.PlanStop],
               actual: ac.DayActual) -> dict[str, datetime]:
    """Плановое ETA обслуженных точек машино-дня: ETA клиента в рейсе плана с номером фактического рейса визита
    (тяжёлый заказ, разбитый на рейсы, — у каждой точки своё ETA); клиента в этом рейсе плана нет (лишний заезд на
    склад сдвинул номера) или по рейсам сравнивать нельзя — ETA его первого появления в плане."""
    first, per_trip = _plan_etas(draft, car, day)
    by_key = {s.key: s for s in stops}
    out: dict[str, datetime] = {}
    for key, vi in actual.served:
        s = by_key.get(key)
        if s is None or s.customer_id is None:
            continue
        k = actual.visits[vi].trip
        eta = per_trip[k].get(s.customer_id) if k < len(per_trip) else None
        eta = eta if eta is not None else first.get(s.customer_id)
        if eta is not None:
            out[key] = eta
    return out


def _eta_errors(car: str, day: date, stops: Sequence[ac.PlanStop], actual: ac.DayActual,
                draft: Mapping[str, Any] | None) -> list[float]:
    """Ошибки планового ETA обслуженных точек машино-дня, минуты (scorecard.eta_error): прибытие по GPS − ETA."""
    marks = ac.stop_marks(actual, stops, day)
    return [sc.eta_error(marks[k], eta) for k, eta in _stop_etas(draft, car, day, stops, actual).items() if k in marks]


def _offroute_end(actual: ac.DayActual) -> datetime | None:
    """Конец рабочего дня для стоянок вне магазинов: возвращение последнего рейса; не вернулся (или трек кончился в
    пути) — уход от последнего магазина; визитов нет — None (до конца трека)."""
    last = actual.trips[-1] if actual.trips else None
    if last is not None and last.ret is not None:
        return last.ret
    return max((v.leave for v in actual.visits), default=None)


def _track_metrics(track: Sequence[Sequence[Any]], actual: ac.DayActual, day: date, depot: Point | None,
                   rules: live.Rules) -> tuple[int | None, float | None]:
    """(превышений скорости, минут стоянок вне магазинов сверх порога) машино-дня по чистому треку с порогами карты
    машин: live.speed_alerts; live.stop_alerts от первого выезда до _offroute_end, обед — сверх обеда. Трека нет —
    (None, None)."""
    pts = ac.clean_track(learning.track_fixes(track))
    if not pts:
        return None, None
    speed = len(live.speed_alerts(pts, rules, False))   # type: ignore[arg-type]
    deps = live.departures(pts, actual, depot)
    stays = live.stop_alerts(actual, day, rules, deps[0] if deps else None, _offroute_end(actual), None, False)
    return speed, math.fsum(max(0.0, a['minutes'] - (rules.lunch_min if a['lunch'] else 0.0)) for a in stays)


@dataclass(frozen=True)
class _UnloadNorm:
    """Норма разгрузки «Развоза» для балла առաքիչ (№88): мин на точку и на тонну строки обучения, действовавшей в этот
    день (без неё — настройки), своё время магазинов (learning.store_extras: выученное, у остальных введённое — №50) и
    надбавка большой машины в зоне Еревана (№68). Темп машины (№66) не умножается: иначе медлительность экипажа,
    выученная в темп, стала бы его нормой."""
    per_stop: float
    per_tonne: float
    extras: Mapping[int, float]
    zone: tuple[tuple[float, float], ...]
    city_min: float

    def minutes(self, o: learning.UnloadObs, big: bool, points: Mapping[int, Point]) -> float:
        city = self.city_min * sum(1 for c in o.customers if c in points and in_polygon(points[c], self.zone)) \
            if big and self.city_min > 0 and len(self.zone) >= 3 else 0.0
        return (self.per_stop * o.n + self.per_tonne * o.tonnes
                + math.fsum(self.extras.get(c, 0.0) for c in o.customers) + city)


def _scorecard_unload_norm(state: RoutesState, bundle: Bundle, day: str) -> _UnloadNorm:
    """Норма, по которой «Развоз» строил план дня day: действующая строка разгрузки из прогонов обучения до этого дня
    (сбой журнала — настройки). Прошлый день не пересчитывается по сегодняшней норме."""
    journal = _learned_journal(state, day)
    unload = _active_unload(*journal) if journal is not None else None
    per_stop, per_tonne = _unload_norms(bundle, unload)
    s = bundle.settings
    return _UnloadNorm(per_stop, per_tonne, learning.store_extras(per_stop, bundle.unload_min, unload),
                       tuple((float(lat), float(lon)) for lat, lon in s.get('yerevan_zone') or ()),
                       float(s.get('big_truck_yerevan_min') or 0.0))


def _day_span(actual: ac.DayActual, prediction: Mapping[str, Any] | None, day: date, today: date
              ) -> tuple[float, float] | None:
    """(минут дня машины по GPS, минут по плану): первый выезд со склада → последнее возвращение, против планового
    первого выезда → планового возвращения последнего рейса (прогноз сборки). Что-то не видно, день ещё идёт (сегодня)
    или рейсов по факту меньше плановых (рейс отменён, перенесён) — None: короткий недоделанный день не «быстрее плана»."""
    trips = [t for t in (prediction or {}).get('trips') or () if isinstance(t, Mapping)]
    if day >= today or not trips or len(actual.trips) < len(trips) or actual.trips[0].depart is None \
            or actual.trips[-1].ret is None:
        return None
    start, end = learning._hhmm(trips[0].get('depart')), learning._hhmm(trips[-1].get('return'))
    if start is None or end is None or end <= start:
        return None
    fact = (actual.trips[-1].ret - actual.trips[0].depart).total_seconds() / 60.0
    return (round(fact, 1), float(end - start)) if fact > 0 else None


def _scorecard_cars(state: RoutesState, bundle: Bundle, day: date, rules: live.Rules,
                    unload_norm: _UnloadNorm | None = None, erp_capacity: Mapping[str, float] | None = None
                    ) -> dict[str, sc.CarDay]:
    """GPS-факт машин дня: км и отметки визитов (actuals.stop_marks: прибытие, окно) — из кэша обучения
    (_learning_days); плановые ETA (_plan_etas, по точке — _stop_etas); скорость и стоянки (_track_metrics) — из
    кэша scorecard_track, пока не сменились отпечаток факта обучения, склад и пороги: трек холодного дня читается один
    раз (тем же чтением, что и факт обучения, — on_read); порядок объезда — actuals.visit_metrics. Для балла առաքիչ
    (№88): разгрузка по GPS (learning.unload_obs — как в обучении, обед по плану — до отметки) против unload_norm
    (большая машина — с тоннажем карточки ERP erp_capacity, как в «Развозе») и длительность дня против плана
    (_day_span)."""
    out: dict[str, sc.CarDay] = {}
    facts = state.fleet_facts
    if facts is None:
        return out
    ds = day.isoformat()
    rkey = (bundle.depot, rules.speed_kmh, rules.speed_sec, rules.stop_min, rules.lunch_min, rules.lunch_window)

    def remember(car: str, d: str, version: Any, data: Mapping[str, Any], stops: list[ac.PlanStop],
                 actual: ac.DayActual) -> None:
        got = _track_metrics(data['track'], actual, day, bundle.depot, rules)
        with state.scorecard_lock:
            _bounded(state.scorecard_track, (car, d), ((version, rkey), got), SCORECARD_CACHE_CAR_DAYS)

    versions: dict[tuple[str, str], Any] = {}
    for car, _, stops, actual, draft, _, _ in _learning_days(state, bundle, day, day, remember, versions):
        version = versions[(car, ds)]   # отпечаток этой строки факта (не перечитанный кэш: его мог сменить поток)
        with state.scorecard_lock:
            hit = state.scorecard_track.get((car, ds))
        if hit is not None and hit[0] == (version, rkey):
            speed, offroute = hit[1]
        else:   # факт обучения был в кэше, а скорости и стоянок нет (пороги сменились, вытеснено) — одно чтение
            speed, offroute = _track_metrics(facts.day(car, ds)['track'], actual, day, bundle.depot, rules)
            with state.scorecard_lock:
                _bounded(state.scorecard_track, (car, ds), ((version, rkey), (speed, offroute)),
                         SCORECARD_CACHE_CAR_DAYS)
        vm = ac.visit_metrics(actual, stops, day)
        first, _ = _plan_etas(draft, car, day)
        prediction = (((draft or {}).get('prediction') or {}).get('trucks') or {}).get(car)
        prediction = prediction if isinstance(prediction, Mapping) else None
        unload = None
        if unload_norm is not None:
            obs = learning.unload_obs(day, actual, stops, learning.lunch_customers(learning.plan_trips(prediction, day)),
                                      car)
            points = {p.customer_id: p.point for p in stops if p.customer_id is not None and p.point is not None}
            big = bundle.truck_big(car, erp_capacity)
            norm = math.fsum(unload_norm.minutes(o, big, points) for o in obs)
            if obs and norm > 0:
                unload = (round(math.fsum(o.minutes for o in obs), 1), round(norm, 1))
        out[car] = sc.CarDay(actual.km_gps, ac.stop_marks(actual, stops, day), first, speed, offroute,
                             (vm.order_changes, vm.ordered) if vm.ordered else None,
                             _stop_etas(draft, car, day, stops, actual), unload,
                             _day_span(actual, prediction, day, _yerevan_now().date()))
    return out


def _scorecard_days(state: RoutesState, bundle: Bundle, since: date, until: date) -> list[tuple[date, dict[str, Any]]]:
    """Сводки дней since…until (scorecard.day_summary), у которых есть данные «Առաքիչ»: из кэша, пока отпечаток дня
    тот же, иначе расчёт. Отпечатки — двумя запросами на весь период (crew_facts.versions, store.dispatch_revs)."""
    assert state.crew_facts is not None
    lo, hi = since.isoformat(), until.isoformat()
    versions = state.crew_facts.versions(lo, hi)
    revs = state.store.dispatch_revs(lo, hi)
    rules = live.Rules.from_settings(bundle.settings)
    erp = _peek_car_capacity(state)   # большая машина — с тоннажем ERP, как в «Развозе» (№68)
    # норма разгрузки дня (_scorecard_unload_norm) в ключ не входит: она по журналу до этого дня и для прошлого дня не
    # меняется; ночной прогон обучения не сбрасывает весь кэш
    base = (bundle.depot, tuple(sorted((cid, w.span()) for cid, w in bundle.windows.items())), sc.LATE_SLACK_MIN,
            state.fleet_facts is not None,
            (rules.speed_kmh, rules.speed_sec, rules.stop_min, rules.lunch_min, rules.lunch_window),
            tuple(sorted((code, bundle.truck_big(code, erp)) for code in bundle.trucks)),
            hash(tuple(sorted(bundle.unload_min.items()))))
    # առաքիչ машины дня из «Развоза» (№84: PIN в APK, логист, ERP) → id «Առաքիչ» по имени — для точек без helper_id;
    # имя у нескольких записей «Առաքիչ» (тёзки, старая выключенная запись) — не сопоставляется: чьё — неизвестно
    by_name: dict[str, list[int]] = {}
    for pid, name in state.crew_facts.names().items():
        by_name.setdefault(' '.join(str(name).split()).casefold(), []).append(pid)
    ids = {k: v[0] for k, v in by_name.items() if len(v) == 1}
    today = _yerevan_now().date().isoformat()
    out = []
    for ds in sorted(versions):
        helpers = {car: ids[k] for car, name in state.store.truck_drivers(ds, 'helper')[0].items()
                   if (k := ' '.join(name.split()).casefold()) in ids}
        # день ещё идёт — «день против плана» не считается (_day_span); после полуночи пересчитать. «Մինչև ժամը» дня —
        # в ключе дня (постоянные окна — в base)
        key = (versions[ds], revs.get(ds), base, tuple(sorted(helpers.items())), ds >= today,
               tuple(sorted(bundle.day_until.get(date.fromisoformat(ds), {}).items())))
        with state.scorecard_lock:
            hit = state.scorecard_cache.get(ds)
        if hit is not None and hit[0] == key:
            summary = hit[1]
        else:
            day = date.fromisoformat(ds)
            summary = sc.day_summary({**state.crew_facts.day(ds), 'helpers': helpers},
                                     _scorecard_cars(state, bundle, day, rules, _scorecard_unload_norm(state, bundle, ds),
                                                     erp))
            with state.scorecard_lock:
                _bounded(state.scorecard_cache, ds, (key, summary), SCORECARD_CACHE_DAYS)
        out.append((date.fromisoformat(ds), summary))
    return out


def _scorecard_fuel(state: RoutesState, bundle: Bundle, since: date, until: date
                    ) -> tuple[dict[tuple[str, str], tuple[float, float]], dict[str, int]]:
    """Литры к норме машино-дней since…until (№87 п. 3): (день, машина) → (литры, норма). Литры — расход интервала
    заправок «полный бак → полный бак», в который попадает день (как learning.daily_l100), × км GPS дня; норма — как
    в «Նորմ և փաստ»: км GPS × норма машины (_fuel_norms) + литры подъёма трека (№85, _terrain_norm); высот трека нет —
    без подъёма. Правило покрытия гаража (GARAGE_TERRAIN_MIN_COVER): треки машины покрывают не меньше этой доли км
    интервала — иначе расход интервала описывает в основном пробег без трека, значения нет. Счётчики: terrain / flat
    — дни с нормой по рельефу и без, uncovered — мало трека в интервале, no_norm — у машины нет нормы.

    Кэш (scorecard_lock): км треков машины по дням интервала — пока у его дней те же отпечатки «Առաքիչ» (события, в т.ч.
    трек и снимки /day — crew_facts.versions), правки плана и склад (fuel_span_cache); результат машино-дня — пока те
    же интервал с его отпечатком, норма, тоннаж и тайлы высот (fuel_day_cache). Тёплый вызов: заправки, отпечатки
    дней и правки плана — по одному запросу на весь диапазон, норма машин; ни трека, ни факта обучения."""
    counts = {'terrain': 0, 'flat': 0, 'uncovered': 0, 'no_norm': 0}
    facts, crew = state.fleet_facts, state.crew_facts
    if facts is None or crew is None:
        return {}, counts
    lookback = (since - timedelta(days=GARAGE_REFUEL_LOOKBACK_DAYS)).isoformat()
    refuels = [r for r in facts.refuels(lookback) if (r.get('eff_date') or r.get('date') or '') >= lookback]

    def local(t: datetime) -> date:
        return t.astimezone(ac.YEREVAN).date()
    spans = [(iv, local(iv.start), local(iv.end)) for iv in learning.fuel_intervals(refuels) if iv.km > 0]
    spans = [x for x in spans if x[1] <= until and x[2] >= since]
    if not spans:
        return {}, counts
    lo, hi = min(x[1] for x in spans), max(x[2] for x in spans)
    versions = crew.versions(lo.isoformat(), hi.isoformat())
    revs = state.store.dispatch_revs(lo.isoformat(), hi.isoformat())

    def span_key(j: int) -> tuple[Any, ...]:
        iv = spans[j][0]
        return iv.car_code, iv.start, iv.end, iv.liters, iv.km

    def span_fp(j: int) -> tuple[Any, ...]:
        a, b = spans[j][1], spans[j][2]
        days = [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
        return bundle.depot, tuple((d, versions.get(d), revs.get(d)) for d in days)

    km: dict[tuple[str, date], float] = {}
    fps = {j: span_fp(j) for j in range(len(spans))}
    todo = []
    for j in range(len(spans)):
        with state.scorecard_lock:
            hit = state.fuel_span_cache.get(span_key(j))
        if hit is not None and hit[0] == fps[j]:
            km.update({(spans[j][0].car_code, d): v for d, v in hit[1].items()})
        else:
            todo.append(j)
    if todo:
        got = {(car, d): actual.km_gps for car, d, _, actual, _, _, _ in
               _learning_days(state, bundle, min(spans[j][1] for j in todo), max(spans[j][2] for j in todo))}
        for j in todo:
            iv, a, b = spans[j]
            mine = {d: v for (c, d), v in got.items() if c == iv.car_code and a <= d <= b}
            km.update({(iv.car_code, d): v for d, v in mine.items()})
            with state.scorecard_lock:
                _bounded(state.fuel_span_cache, span_key(j), (fps[j], mine), SCORECARD_CACHE_CAR_DAYS)
    tracked = {j: math.fsum(v for (c, d), v in km.items() if c == iv.car_code and a <= d <= b)
               for j, (iv, a, b) in enumerate(spans)}
    norms = _fuel_norms(state, bundle, (until + timedelta(days=1)).isoformat())
    erp_capacity = _peek_car_capacity(state)
    try:
        dem_key = (dem.elev_params(), dem.dem_signature()) if dem.terrain_supported() else None
    except Exception:   # тайлы высот недоступны — как в _track_climbs: без рельефа, не ошибка
        dem_key = None
    out: dict[tuple[str, str], tuple[float, float]] = {}
    pending: list[tuple[str, date, int, Any, float, float]] = []   # (машина, день, интервал, отпечаток, км, норма)
    for (car, d), k in sorted(km.items()):
        if not since <= d <= until or k <= 0:
            continue
        # день заправки посреди дня — граница двух интервалов: берётся первый (как learning.daily_l100); км такого дня
        # входят в покрытие обоих интервалов (дневной трек на части до и после заправки не делится)
        j = next((i for i, x in enumerate(spans) if x[0].car_code == car and x[1] <= d <= x[2]), None)
        if j is None:
            continue
        iv = spans[j][0]
        l100 = (norms.get(car) or {}).get('l100')
        capacity = bundle.truck_capacity(car, erp_capacity)
        fp = (span_key(j), fps[j], tracked[j], k, l100, capacity, dem_key)
        with state.scorecard_lock:
            hit = state.fuel_day_cache.get((car, d.isoformat()))
        if hit is not None and hit[0] == fp:
            kind, value = hit[1]
        elif tracked[j] < GARAGE_TERRAIN_MIN_COVER * iv.km or l100 is None:
            kind, value = ('uncovered' if tracked[j] < GARAGE_TERRAIN_MIN_COVER * iv.km else 'no_norm'), None
            with state.scorecard_lock:
                _bounded(state.fuel_day_cache, (car, d.isoformat()), (fp, (kind, value)), SCORECARD_CACHE_CAR_DAYS)
        else:
            pending.append((car, d, j, fp, k, l100))
            continue
        counts[kind] += 1
        if value is not None:
            out[(d.isoformat(), car)] = value
    if pending:   # рельеф — только для машино-дней без готового результата (_track_climbs читает трек мимо своего кэша)
        climbs = _track_climbs(state, min(p[1] for p in pending), max(p[1] for p in pending))
        for car, d, j, fp, k, l100 in pending:
            hill = _terrain_norm(bundle.truck_capacity(car, erp_capacity), k, l100, climbs.get((car, d.isoformat())))
            kind = 'terrain' if hill is not None else 'flat'
            value = (k * spans[j][0].l100 / 100.0, hill[1] if hill is not None else k * l100 / 100.0)
            with state.scorecard_lock:
                _bounded(state.fuel_day_cache, (car, d.isoformat()), (fp, (kind, value)), SCORECARD_CACHE_CAR_DAYS)
            counts[kind] += 1
            out[(d.isoformat(), car)] = value
    return out, counts


class _ScoreRoutes:
    """Следование плановой линии машино-дней для «Վարորդներ» (scorecard «Երթուղի»): день считает один фоновый поток на
    процесс (линии плана по дорогам прошлого дня строятся заново — секунды на день), запрос «Վարորդներ» его не ждёт: чего
    ещё нет — у дня значения нет (coverage.route.pending), появится при следующем запросе. Результат дня — машина → (км
    езды в счёте дня, км отклонений без объяснённых) — помнится, пока тот же отпечаток дня; линии после расчёта не
    хранятся. Дни — не больше SCORECARD_CACHE_DAYS (уходят давно не спрошенные). Расчёт дня вернул None (дорог нет) — не
    кэшируется: повтор при следующем запросе."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[Any, dict[str, tuple[float, float]]]] = OrderedDict()
        self._busy = False

    def get(self, wanted: Mapping[str, Any], compute: Callable[[str], dict[str, tuple[float, float]] | None]
            ) -> tuple[dict[str, dict[str, tuple[float, float]]], list[str]]:
        """(день → результат — готовые с тем же отпечатком, дни, которые ещё считаются)."""
        def serve() -> tuple[dict[str, dict[str, tuple[float, float]]], list[str]]:
            ready, todo = {}, []
            for ds, fp in wanted.items():
                hit = self._items.get(ds)
                if hit is not None and hit[0] == fp:
                    self._items.move_to_end(ds)
                    ready[ds] = hit[1]
                else:
                    todo.append(ds)
            return ready, todo
        with self._lock:
            ready, todo = serve()
            start = bool(todo) and not self._busy
            if start:
                self._busy = True

        def run() -> None:
            try:
                for ds in todo:
                    got = compute(ds)
                    if got is None:
                        continue
                    with self._lock:
                        self._items.pop(ds, None)
                        self._items[ds] = (wanted[ds], got)
                        while len(self._items) > SCORECARD_CACHE_DAYS:
                            self._items.popitem(last=False)
            except Exception:
                logger.exception('[Routes] Վարորդներ: следование плановой линии не посчитано')
            finally:
                with self._lock:
                    self._busy = False
        if not start:
            return ready, todo
        if LIVE_ROAD_BACKGROUND:
            threading.Thread(target=run, name='routes-score-route', daemon=True).start()
            return ready, todo
        run()
        with self._lock:
            return serve()


def _route_day(state: RoutesState, ds: str) -> dict[str, tuple[float, float]] | None:
    """Машина → (км езды в счёте дня, км отклонений без объяснённых) прошлого дня ds — тот же расчёт карточки машины, что
    у карты (live.car_view: deviation.counted_km / off_km, объяснения диспетчера), по линии отправленного плана по
    дорогам, построенной сразу. Дорог нет — None (повтор позже); машина без линии по дорогам или без езды — нет в ответе."""
    roads = state.roads.get() if state.roads is not None else None
    if roads is None or roads.failed or state.live_facts is None:
        return None
    day = date.fromisoformat(ds)
    fleet = state.live_facts.fleet(ds)
    ctx = _live_context(state, day, fleet, lines_now=True)
    now = _yerevan_now()
    out: dict[str, tuple[float, float]] = {}
    for car, route in ctx.routes.items():
        if not route.geo.road or car not in fleet:
            continue
        card = live.car_view(day, now, fleet[car], ctx.plans.get(car, []), ctx.trucks.get(car, live.TruckSpec()),
                             ctx.depot, ctx.rules, ctx.road, False, ctx.windows, route, None, ctx.explained.get(car, ()))
        dev = card.get('deviation')
        if dev is not None and dev.get('adherence_pct') is not None:
            out[car] = (float(dev['counted_km']), float(dev['off_km']))
    return out


def _scorecard_route(state: RoutesState, bundle: Bundle, since: date, until: date
                     ) -> tuple[dict[tuple[str, str], tuple[float, float]], dict[str, int]]:
    """Следование плановой линии машино-дней since…until (scorecard «Երթուղի»): (день, машина) → (км, км отклонений)
    и счётчики {'days': дней с расчётом, 'pending': дней ещё в расчёте, 'no_roads': дней без карты дорог}. Только
    прошедшие дни (сегодня день идёт) с планом «Развоза»; отпечаток дня — данные «Առաքիչ» (crew_facts.versions), правка
    плана, объяснения диспетчера, пороги отклонения, склад и версия карты дорог. Карты дорог нет — значения нет (не штраф)."""
    counts = {'days': 0, 'pending': 0, 'no_roads': 0}
    if state.live_facts is None or state.crew_facts is None:
        return {}, counts
    today = _yerevan_now().date()
    hi = min(until, today - timedelta(days=1))
    if hi < since:
        return {}, counts
    lo_s, hi_s = since.isoformat(), hi.isoformat()
    versions = state.crew_facts.versions(lo_s, hi_s)
    revs = state.store.dispatch_revs(lo_s, hi_s)
    roads = state.roads.get() if state.roads is not None else None
    if roads is None or roads.failed:
        counts['no_roads'] = sum(1 for ds in versions if ds in revs)
        return {}, counts
    rules = live.Rules.from_settings(bundle.settings)
    base = (bundle.depot, rules.deviation_m, rules.detour_min_km, getattr(roads, 'version', None))
    wanted = {}
    for ds in sorted(versions):
        if ds in revs:
            expl = tuple((car, e['id'], e['kind'], e['from'], e['to'])
                         for car, items in sorted(state.store.live_explanations(ds).items()) for e in items)
            wanted[ds] = (versions[ds], revs[ds], expl, base)
    app = current_app._get_current_object()

    def compute(ds: str) -> dict[str, tuple[float, float]] | None:
        with app.app_context():
            return _route_day(state, ds)
    ready, todo = state.score_routes.get(wanted, compute)
    counts.update(days=len(ready), pending=len(todo))
    return {(ds, car): v for ds, cars in ready.items() for car, v in cars.items()}, counts


def _scorecard(state: RoutesState, since: date, until: date) -> dict[str, Any]:
    """Показатели людей за since…until (scorecard.period) с литрами к норме и следованием плановой линии; coverage.fuel и
    coverage.route — счётчики _scorecard_fuel и _scorecard_route."""
    assert state.crew_facts is not None
    bundle = state.store.load()
    fuel, fuel_cov = _scorecard_fuel(state, bundle, since, until)
    route, route_cov = _scorecard_route(state, bundle, since, until)
    body = sc.period(_scorecard_days(state, bundle, since, until), state.crew_facts.names(), fuel, route)
    body['coverage']['fuel'] = fuel_cov
    body['coverage']['route'] = route_cov
    return body


@bp.get('/api/routes/drivers/scorecard')
@_api
def api_drivers_scorecard() -> Any:
    """Показатели людей за ?from=&to= (scorecard.period): строка на водителя (и отдельно на առաքիչ) с баллом, местом и
    разбивкой по дням и опоздавшими магазинами; eta — точность планового ETA парка (п. 7); coverage — сколько точек
    оценено и почему остальные нет. Деньги (cash) — только администратору: «Гаражу» сервер их не отдаёт."""
    role = _scorecard_role()
    if role is None:
        logger.warning('[Routes] Վարորդներ: отказ роли %r (%s)', g.get('user_role'), request.path)
        return jsonify({'success': False, 'error': SCORECARD_FORBIDDEN}), 403
    rng, error = _scorecard_range()
    if error is not None:
        return error
    state = _state()
    if state.crew_facts is None:
        return _bad_request({'_': '«Առաքիչ» բաժինը միացված չէ — տվյալներ չկան'})
    since, until = rng   # type: ignore[misc]
    body = _scorecard(state, since, until)
    cash = role == 'admin'
    if not cash:
        for r in body['drivers']:
            r.pop('cash', None)
            for d in r['detail']:
                d.pop('cash', None)
    return jsonify({'success': True, 'from': since.isoformat(), 'to': until.isoformat(),
                    'today': _yerevan_now().date().isoformat(), 'days': (until - since).days + 1,
                    'gps': state.fleet_facts is not None, 'cash': cash, 'rules': sc.rules(), **body})


def _week_job(app: Any, state: RoutesState, key: str, monday: date) -> None:
    """Фоновый расчёт недели для APK (week_score): результат — в score_week_cache; сбой — в журнал и момент сбоя в
    score_week_failed (заново — не раньше SCORE_WEEK_RETRY_S). Контекст приложения — для факта «Առաքիչ»
    (courier.scorecard берёт базу из него)."""
    try:
        with app.app_context():
            body = _scorecard(state, monday, monday + timedelta(days=6))
        with state.score_week_lock:
            _bounded(state.score_week_cache, key, (time.monotonic(), body), SCORE_WEEK_KEEP)
            state.score_week_failed.pop(key, None)
    except Exception:
        logger.exception('[Routes] Վարորդներ: неделя %s для APK не посчитана', key)
        with state.score_week_lock:
            state.score_week_failed[key] = time.monotonic()
    finally:
        with state.score_week_lock:
            state.score_week_running.pop(key, None)


def week_score(state: RoutesState | None, driver_id: int, monday: date) -> tuple[str, dict[str, Any] | None]:
    """Своя неделя водителя для APK (courier GET /api/courier/v1/score, контракт §10): ('ok', ответ) — показатели за
    пн–вс, балл и место среди водителей с ≥ scorecard.MIN_DAYS днями, без имён и id других; ('busy', None) — неделя
    считается, прежнего расчёта нет (APK повторит позже); ('off', None) — «Վարորդներ» не подключены (нет раздела
    «Маршруты» или «Առաքիչ»). Неделя считается одна на все терминалы, в одном фоновом потоке и одна за раз (другие
    недели ждут своей очереди): запрос, начавший расчёт, ждёт его не дольше SCORE_WEEK_WAIT_S, остальные не ждут —
    получают прежний расчёт этой недели, если он есть. Готовый расчёт отдаётся SCORE_WEEK_TTL_S секунд, потом
    пересчитывается (до готовности — прежний). Потоки сервера, нужные терминалам, расчёт не держит."""
    if state is None or getattr(state, 'crew_facts', None) is None:
        return 'off', None
    key = monday.isoformat()
    sunday = monday + timedelta(days=6)
    with state.score_week_lock:
        hit = state.score_week_cache.get(key)
        fresh = hit is not None and time.monotonic() - hit[0] <= SCORE_WEEK_TTL_S
        job = None
        failed = state.score_week_failed.get(key)
        backoff = failed is not None and time.monotonic() - failed < SCORE_WEEK_RETRY_S
        if not fresh and not state.score_week_running and not backoff:
            job = threading.Thread(target=_week_job, args=(current_app._get_current_object(), state, key, monday),
                                   name='routes-score-week', daemon=True)
            state.score_week_running[key] = job
            try:
                job.start()
            except Exception:   # поток не стартовал (нет ресурсов): отметка «считается» не должна остаться навсегда
                logger.exception('[Routes] Վարորդներ: расчёт недели %s для APK не запущен', key)
                state.score_week_running.pop(key, None)
                state.score_week_failed[key] = time.monotonic()
                job = None
    if job is not None:
        job.join(SCORE_WEEK_WAIT_S)
        with state.score_week_lock:
            hit = state.score_week_cache.get(key)
    if hit is None:
        return 'busy', None
    body = hit[1]
    row = next((r for r in body['drivers'] if r['role'] == 'driver' and r['id'] == driver_id), None)
    me: dict[str, Any] = {'days': 0, 'stops': 0, 'on_time_pct': None, 'on_time_n': 0, 'on_time_of': 0,
                          'avg_late_min': None, 'order_pct': None, 'speed_events': None, 'speed_per_100km': None,
                          'offroute_stop_min': None, 'liters_vs_norm_pct': None, 'score': None, 'parts': {}}
    if row is not None:
        me.update(days=row['days'], stops=row['stops'], on_time_pct=row['on_time_pct'], on_time_n=row['on_time'],
                  on_time_of=row['rated'], avg_late_min=row['late_mean_min'], order_pct=row['order_pct'],
                  speed_events=row['speed_events'], speed_per_100km=row['speed_per_100km'],
                  offroute_stop_min=row['offroute_min'], liters_vs_norm_pct=row['liters_vs_norm_pct'],
                  score=row['score'], parts={k: dict(v) for k, v in row['parts'].items()})
    return 'ok', {'week_from': key, 'week_to': sunday.isoformat(), 'me': me,
                  'rank': row['rank'] if row is not None else None, 'of': body['ranked'],
                  'enough_data': bool(row is not None and row['enough_data']), 'min_days': sc.MIN_DAYS}


# --- Журнал гаража «Ավտոտնակ» (№53, docs/plans/garage-journal-plan.md) ---
# Страница начальника гаража (роль «Гараж» видит только её; доступ — app_v2._auth_and_scope_gate) и администратора.
# Ошибки записи — по-армянски (store.check_garage_entry, GarageError). Ремонт ֏/км — garage.price на сегодня.

GARAGE_STALE_DAYS = 30          # последний пробег старше — плашка наверху и предупреждение в «Ամփոփում»
GARAGE_ODOMETER_ROWS_MAX = 200  # строк в «Պահպանել բոլորը»
GARAGE_MONTHS = 12              # расходы по месяцам: этот и 11 до него
GARAGE_SPREAD_SUGGEST_AMD = 300_000   # ремонт дороже — форма предлагает растянуть его на 24 или 36 месяцев
_MONTH_RE = re.compile(r'^\d{4}-\d{2}$')


def _garage_admin() -> bool:
    """Администратор дашборда (app_v2 кладёт роль вошедшего в g.user_role): видит удалённые записи. Нет роли — нет."""
    return g.get('user_role') == 'admin'


def _garage_trucks(state: RoutesState, bundle: Bundle) -> list[dict[str, Any]]:
    """Машины раздела (таблица trucks): номер, название (ERP CARS, у ручной — своё), в работе ли — машина расчёта
    (работает, тоннаж — свой или из карточки ERP — и расход заданы). ERP недоступна — названия только у ручных,
    «работает» — не выключена, тоннаж — только свой."""
    try:
        snap, _ = state.snapshots.cached()
    except ErpError:
        logger.warning('[Routes] Журнал гаража: ERP недоступна — машины без названий ERP', exc_info=True)
        snap = None
    out = []
    for code, t in sorted(bundle.trucks.items()):
        car = snap.cars.get(code) if snap is not None and not t.manual else None
        active = bundle.truck_active(code, snap.active_cars) if snap is not None else t.active is not False
        capacity = bundle.truck_capacity(code, snap.car_capacity if snap is not None else None)
        out.append({'car_code': code, 'name': (car.name if car is not None else t.name) or '',
                    'active': bool(active and capacity is not None and t.fuel_l_per_100km is not None)})
    return out


def _garage_months(entries: Sequence[Any], today: date) -> list[dict[str, Any]]:
    """Расходы журнала по месяцам (все машины): ремонт, ДТП, страховка и налоги — GARAGE_MONTHS месяцев до этого."""
    first = today.replace(day=1)
    keys = []
    for _ in range(GARAGE_MONTHS):
        keys.append(first.isoformat()[:7])
        first = (first - timedelta(days=1)).replace(day=1)
    out = []
    for key in reversed(keys):
        sums = {k: sum(e.amount_amd for e in entries if e.kind == k and e.day.isoformat()[:7] == key)
                for k in ('repair', 'accident', 'fixed')}
        out.append({'month': key, **sums, 'total': sum(sums.values())})
    return out


@bp.get('/api/routes/garage')
@_api
def api_garage() -> Any:
    """«Ավտոտնակ»: машины (последнее показание пробега — журнал и одометр APK), по машине за 12 месяцев — ремонт, ДТП,
    страховка и налоги, км, ремонт ֏/км и статус на сегодня, средняя модели или парка машине без своей цены, какое
    значение в расчёте (wear_source, как в «Настройках»), расходы по месяцам. Записи — /api/routes/garage/entries."""
    state = _state()
    today = _clock().date()
    bundle = state.store.load()
    entries = state.store.garage_entries()
    by_car = _garage_by_car(entries)
    rows = _garage_trucks(state, bundle)   # снимок ERP — до цены: модели машин по его именам
    apk = _apk_odometers(state, date(today.year - GARAGE_APK_YEARS, 1, 1)) if by_car else {}
    models = _garage_models(state, bundle.trucks)
    prices = garage.prices(by_car, today, apk, models)
    priors = garage.priors(prices, models) if prices else {}
    used = _garage_bundle(bundle, prices, priors)
    trucks, summary = [], []
    for t in rows:
        code = t['car_code']
        if not t['active'] and code not in by_car:
            continue
        last = [p for p in garage.readings(by_car.get(code, ()), apk.get(code, ())) if p[0] <= today]
        row = {**t, 'last_day': last[-1][0].isoformat() if last else None,
               'last_km': round(last[-1][1]) if last else None}
        trucks.append(row)
        ago = (today - last[-1][0]).days if last else None
        summary.append({**row, 'garage': _garage_price_json(prices.get(code)), 'days_since_last': ago,
                        'stale': ago is None or ago > GARAGE_STALE_DAYS, 'in_calc': code in used.garage_wear,
                        'garage_prior': _garage_prior_json(priors.get(code)), 'wear_source': used.wear_source(code)})
    return jsonify({'success': True, 'today': today.isoformat(), 'admin': _garage_admin(), 'trucks': trucks,
                    'summary': summary, 'months': _garage_months(entries, today), 'journal_empty': not entries,
                    'rules': {'ready_months': garage.MONTHS_READY, 'ready_km': garage.READY_KM,
                              'stale_days': GARAGE_STALE_DAYS, 'blend_km': garage.BLEND_KM,
                              'spread_months': list(garage.SPREAD_MONTHS), 'spread_suggest_amd': GARAGE_SPREAD_SUGGEST_AMD}})


@bp.get('/api/routes/garage/entries')
@_api
def api_garage_entries() -> Any:
    """Записи журнала, новые первыми: ?car= — машина, ?month=YYYY-MM — месяц, ?deleted=1 — и удалённые (только
    администратору; прежние версии изменённых записей — с replaced_by). total_amd — сумма живых записей отбора. Кто
    внёс, изменил, удалил (логины) — только администратору."""
    state = _state()
    car, month = request.args.get('car') or '', request.args.get('month') or ''
    if month and not _MONTH_RE.match(month):
        return _bad_request({'month': 'Ամիսը՝ ՏՏՏՏ-ԱԱ'})
    found = [e for e in state.store.garage_entries(deleted=_garage_admin() and request.args.get('deleted') == '1')
             if (not car or e.car_code == car) and (not month or e.day.isoformat()[:7] == month)]
    found.sort(key=lambda e: (e.day, e.id), reverse=True)
    rows = [e.to_json() for e in found]
    if not _garage_admin():   # логины пользователей — только администратору
        rows = [{k: v for k, v in r.items() if k not in ('created_by', 'updated_by', 'deleted_by')} for r in rows]
    return jsonify({'success': True, 'entries': rows,
                    'total_amd': sum(e.amount_amd for e in found if e.deleted_at is None)})


def _entry_id(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 < raw < 2 ** 63 else None


@bp.post('/api/routes/garage/entries')
@_api
def api_garage_save() -> Any:
    """Новая запись или правка ({"id": …, поля записи}). «Только пробег» в день, где он уже есть, — обновляет его.
    Ошибки — по полям, по-армянски; спидометр не убывает во времени (ошибка называет конфликтующую запись)."""
    payload, error = _json_body()
    if error is not None:
        return error
    if not isinstance(payload, dict):
        return _bad_request({'_': 'Սպասվում էր JSON օբյեկտ'})
    payload = dict(payload)
    raw_id = payload.pop('id', None)
    entry_id = _entry_id(raw_id)
    if raw_id is not None and entry_id is None:
        return _bad_request({'_': 'Գրառման համարը սխալ է'})
    state = _state()
    item, errors = check_garage_entry(payload, state.store.load().trucks, _clock().date())
    if errors:
        return _bad_request(errors)
    try:
        saved = state.store.save_garage_entry(item, session.get('username'), entry_id)
    except GarageError as e:
        return _bad_request(e.errors)
    logger.info('[Routes] Журнал гаража (%s): запись %d — %s %s %s', session.get('username'), saved, item.car_code,
                item.day, item.kind)
    return jsonify({'success': True, 'id': saved})


@bp.post('/api/routes/garage/entries/delete')
@_api
def api_garage_delete() -> Any:
    """Удалить запись ({"id"}): мягко — администратор видит её среди удалённых, в расчётах её нет."""
    payload, error = _json_body()
    if error is not None:
        return error
    entry_id = _entry_id(payload.get('id') if isinstance(payload, dict) else None)
    if entry_id is None:
        return _bad_request({'_': 'Գրառման համարը սխալ է'})
    if not _state().store.delete_garage_entry(entry_id, session.get('username')):
        return jsonify({'success': False, 'error': 'Գրառումը չի գտնվել կամ արդեն ջնջված է'}), 404
    logger.info('[Routes] Журнал гаража (%s): запись %d удалена', session.get('username'), entry_id)
    return jsonify({'success': True})


@bp.post('/api/routes/garage/odometers')
@_api
def api_garage_odometers() -> Any:
    """Пробег машин одной таблицей: {"items": [{"car_code", "day", "odometer_km"}]} — «только пробег» каждой машины
    (тот же день — обновляет). Записываются строки без ошибок; ошибки — построчно {номер строки: причина}."""
    payload, error = _json_body()
    if error is not None:
        return error
    items = payload.get('items') if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items or len(items) > GARAGE_ODOMETER_ROWS_MAX:
        return _bad_request({'items': 'Լրացրեք գոնե մեկ մեքենայի վազքը'})
    state = _state()
    cars, today = state.store.load().trucks, _clock().date()
    errors: dict[int, str] = {}
    valid: list[tuple[int, Any]] = []
    seen: set[str] = set()
    for i, row in enumerate(items):
        if not isinstance(row, dict) or not set(row) <= {'car_code', 'day', 'odometer_km'}:
            errors[i] = 'Տողը սխալ է'
            continue
        item, problems = check_garage_entry({**row, 'kind': 'odometer'}, cars, today)
        if problems:
            errors[i] = next(iter(problems.values()))
        elif item.car_code in seen:
            errors[i] = 'Մեքենան կրկնվում է'
        else:
            seen.add(item.car_code)
            valid.append((i, item))
    conflicts = state.store.save_garage_odometers([item for _, item in valid], session.get('username')) if valid else {}
    errors.update({valid[k][0]: text for k, text in conflicts.items()})
    saved = sorted(i for i, _ in valid if i not in errors)
    logger.info('[Routes] Журнал гаража (%s): пробег — записано %d, ошибок %d', session.get('username'), len(saved),
                len(errors))
    return jsonify({'success': True, 'saved': saved, 'errors': {str(i): t for i, t in sorted(errors.items())}})


# --- «Նորմ և փաստ»: расход и км машины за месяц против нормы и плана (вкладка «Ավտոտնակ», 04.10) ---
# Факт — тот же, что у «Обучения и факта» (_learning_days, learning.day_report, learning.fuel_intervals); ERP не
# читается, кроме названий машин (_garage_trucks, снимок в кэше).

GARAGE_REFUEL_LOOKBACK_DAYS = 60   # заправки до месяца — начало первого интервала (как отчёт api_learning)
GARAGE_NORM_MONTHS = 12            # раньше — не считается (тело «տվյալ չկա», без потока): факт таких месяцев не в кэше
# Факт машино-дня без кэша — ≈ 0,03–0,1 с (actuals.reconstruct): месяц 20 машин впервые после перезапуска — до минуты.
# Дольше этого ответ не ждёт: факт досчитывается в фоне (в кэш _learning_days), страница спрашивает снова (pending).
GARAGE_NORM_WAIT_S = 1.5


def _add_months(first: date, n: int) -> date:
    return date(first.year + (first.month - 1 + n) // 12, (first.month - 1 + n) % 12 + 1, 1)


def _garage_month(raw: str, today: date) -> date | None:
    """«YYYY-MM» → первый день месяца: не позже месяца today и не раньше 2000 года; пусто — месяц today."""
    if not raw:
        return today.replace(day=1)
    if not _MONTH_RE.match(raw):
        return None
    year, month = int(raw[:4]), int(raw[5:])
    if not 1 <= month <= 12 or year < 2000:
        return None
    first = date(year, month, 1)
    return first if first <= today.replace(day=1) else None


def _warm_days(state: RoutesState, bundle: Bundle, since: date, until: date) -> None:
    """Фоновый расчёт факта за since…until (в кэш _learning_days и рельеф треков _track_climbs). Сбой — в журнал и в
    garage_warm_failed: следующий запрос этого периода получит {failed: true}, а не тот же долгий пересчёт с 500."""
    try:
        _learning_days(state, bundle, since, until)
        _track_climbs(state, since, until)
    except Exception:
        logger.exception('[Routes] «Նորմ և փաստ»: факт %s…%s не посчитан', since, until)
        with state.actuals_lock:
            state.garage_warm_failed.add((since, until))
    finally:
        with state.actuals_lock:
            state.garage_warm.pop((since, until), None)


GARAGE_TRACK_STEP_KM = 0.05   # рельеф трека (№85): точки не ближе 50 м друг к другу — дрожание на стоянке не подъём
# флаг расхода месяца — от нормы с рельефом, только если км треков с рельефом ≥ этой доли км интервалов заправок машины за
# месяц (решение по №85): иначе норма с рельефом описывает малую часть пробега — флаг от прежней нормы (low_coverage)
GARAGE_TERRAIN_MIN_COVER = 0.5


def _track_climbs(state: RoutesState, since: date, until: date) -> dict[tuple[str, str], tuple[float, float]]:
    """Рельеф GPS-треков машино-дней since…until (№85): (машина, день) → (эффективный подъём, м; км трека) — высоты прямо из
    сглаженного DEM в точках трека (terrain.track_climbs; точки — не ближе GARAGE_TRACK_STEP_KM). Кэш — пока не сменились
    трек (FleetFacts.version), параметры DEM и тайлы. Тайлов нет — {}; сбой — в журнал и {}: страница — без рельефа, не
    ошибка."""
    try:
        sig = dem.dem_signature() if dem.terrain_supported() else None
        if state.fleet_facts is None or sig is None:
            return {}
        key = (dem.elev_params(), sig)
        out: dict[tuple[str, str], tuple[float, float]] = {}
        todo = []
        for car, ds in state.fleet_facts.car_days(since.isoformat(), until.isoformat()):
            version = (state.fleet_facts.version(car, ds), key)
            with state.actuals_lock:
                hit = state.track_climbs.get((car, ds))
            if hit is not None and hit[0] == version:
                if hit[1] is not None:
                    out[car, ds] = hit[1]
                continue
            line: list[Point] = []
            for f in ac.clean_track(learning.track_fixes(state.fleet_facts.day(car, ds)['track'])):
                if not line or haversine_km(line[-1], f.point) >= GARAGE_TRACK_STEP_KM:
                    line.append(f.point)
            todo.append(((car, ds), version, line))
        if todo:
            got = dem.track_climbs([line for _, _, line in todo])
            with state.actuals_lock:
                for (k, version, _), climb in zip(todo, got):
                    state.track_climbs.pop(k, None)
                    state.track_climbs[k] = (version, climb)
                    if climb is not None:
                        out[k] = climb
                while len(state.track_climbs) > ACTUALS_CACHE_MAX:
                    state.track_climbs.pop(next(iter(state.track_climbs)))
        return out
    except Exception:
        logger.exception('[Routes] «Նորմ և փաստ»: рельеф треков %s…%s не посчитан — без рельефа', since, until)
        return {}


def _month_ready(state: RoutesState, bundle: Bundle, since: date, until: date) -> str:
    """Факт машин за since…until: ready — в кэше _learning_days (досчитан не дольше GARAGE_NORM_WAIT_S); pending —
    досчитывается в фоне; failed — фоновый расчёт упал (отметка снимается: следующий запрос пробует снова). Поток
    один на весь сервер: идёт расчёт другого месяца — этот ждёт своей очереди (pending), CPU не делится на несколько."""
    with state.actuals_lock:
        if (since, until) in state.garage_warm_failed:
            state.garage_warm_failed.discard((since, until))
            return 'failed'
        job = state.garage_warm.get((since, until))
        if job is None:
            if state.garage_warm:
                return 'pending'
            job = threading.Thread(target=_warm_days, args=(state, bundle, since, until), name='routes-garage-norm',
                                   daemon=True)
            state.garage_warm[(since, until)] = job
            job.start()
    job.join(GARAGE_NORM_WAIT_S)
    if job.is_alive():
        return 'pending'
    with state.actuals_lock:
        if (since, until) in state.garage_warm_failed:
            state.garage_warm_failed.discard((since, until))
            return 'failed'
    return 'ready'


def _terrain_norm(capacity_kg: float | None, km: float | None, l100: float | None,
                  climb: tuple[float, float] | None) -> tuple[float, float] | None:
    """Норма машино-дня по треку с рельефом (№85): (литры подъёма, норма дня в литрах) — км GPS × норма + литры подъёма
    трека сверх среднего (ū трека — постоянная масса: собственная + полгруза, груз по участкам трека не известен); не
    ниже нуля. Рельефа, нормы, тоннажа или км нет — None. Общая для «Նորմ և փաստ» и «Վարորդներ»."""
    if climb is None or l100 is None or not capacity_kg or km is None:
        return None
    extra = terrain_liters(curb_tonnes(capacity_kg) + capacity_kg / 2000.0, *climb, u_bar=TERRAIN_U_BAR_TRACK)
    return extra, max(0.0, km * l100 / 100.0 + extra)


def _fuel_norms(state: RoutesState, bundle: Bundle, before: str) -> dict[str, dict[str, Any]]:
    """Машина → норма тревоги «Ավտոտնակ» и выученный расход. Норма — ручная из настроек: пустой и полный заданы — их
    середина (manual_profile; ими считает рейс running_costs.route_cost), иначе «Расход, л/100 км» (manual). Выученный
    (learned: середина «пустой — полный» действующей строки журнала до дня before — конец показываемого месяца) —
    только рядом: он подогнан под те же заправки, что и факт, и перерасход «выучился» бы. Ручной нормы нет — норма
    выученная (source learned: тревога слабая, страница это говорит)."""
    journal = _learned_journal(state, before)
    fuel = (learning.in_effect(*journal, None).fuel or {}) if journal is not None else {}
    out = {}
    for code, t in bundle.trucks.items():
        p = fuel.get(code)
        learned = (float(p['empty_l100']) + float(p['full_l100'])) / 2 if p is not None else None
        if t.fuel_empty_l_per_100km is not None and t.fuel_full_l_per_100km is not None:
            norm, source = (float(t.fuel_empty_l_per_100km) + float(t.fuel_full_l_per_100km)) / 2, 'manual_profile'
        elif t.fuel_l_per_100km is not None:
            norm, source = float(t.fuel_l_per_100km), 'manual'
        else:
            norm, source = learned, 'learned' if learned is not None else None
        out[code] = {'l100': norm, 'source': source, 'learned': learned}
    return out


def _r1(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


@bp.get('/api/routes/garage/norm')
@_api
def api_garage_norm() -> Any:
    """«Նորմ և փաստ» за месяц ?month=YYYY-MM (пусто — текущий по Еревану): по машине — норма тревоги (_fuel_norms:
    ручная, выученный — рядом) и расход по заправкам (garage.fuel_month: интервал — в месяц закрывающей заправки;
    расход вне learning.FUEL_L100 — подозрительные заправки), км плана «Развоза» и GPS в дни, где есть оба
    (garage.km_month), стоянки вне плана и точки не по порядку, флаги выше нормы / плана больше чем на
    garage.ALERT_PCT %, по дням — строки learning.day_report. Флаг расхода — от нормы с рельефом треков (№85: норма
    дня — км GPS × норма + литры подъёма трека, норма месяца — по дням с треком; norm.basis terrain), рельефа нет — от
    прежней нормы (basis flat, terrain_missing). Дни GPS — до вчера (сегодня ещё не кончилось). Месяц
    раньше GARAGE_NORM_MONTHS назад — too_old, без данных. Факт месяца не успел посчитаться за GARAGE_NORM_WAIT_S —
    {pending: true}: досчитывается в фоне, страница спрашивает снова; фоновый расчёт упал — {failed: true}. Карта дня —
    /api/routes/garage/day."""
    today = _yerevan_now().date()
    first = _garage_month(request.args.get('month') or '', today)
    if first is None:
        return _bad_request({'month': 'Ամիսը՝ ՏՏՏՏ-ԱԱ, ոչ ուշ, քան ընթացիկ ամիսը'})
    last = _add_months(first, 1) - timedelta(days=1)
    gps_to = min(last, today - timedelta(days=1))
    oldest = _add_months(today.replace(day=1), -(GARAGE_NORM_MONTHS - 1))   # 12 месяцев вместе с текущим
    too_old = first < oldest
    state = _state()
    bundle = state.store.load()
    facts = None if too_old else state.fleet_facts
    head = {'success': True, 'month': first.isoformat()[:7]}
    # трека в месяце нет (дешёвый запрос по индексу) — считать нечего, поток не нужен
    if facts is not None and gps_to >= first and facts.car_days(first.isoformat(), gps_to.isoformat()):
        ready = _month_ready(state, bundle, first, gps_to)
        if ready != 'ready':
            return jsonify({**head, 'pending': ready == 'pending', 'failed': ready == 'failed'})
        days = _learning_days(state, bundle, first, gps_to)
        climbs = _track_climbs(state, first, gps_to)   # уже в кэше: считал фоновый _warm_days
    else:
        days, climbs = [], {}
    since = (first - timedelta(days=GARAGE_REFUEL_LOOKBACK_DAYS)).isoformat()
    refuels = [r for r in (facts.refuels(since) if facts is not None else [])
               if (r.get('eff_date') or r.get('date') or '') >= since]
    rejected: list[learning.Interval] = []
    intervals = learning.fuel_intervals(refuels, rejected)
    reports: dict[str, list[dict[str, Any]]] = {}
    erp_capacity = _peek_car_capacity(state)
    for car, day, stops, actual, draft, plan_trips, plan_stops in days:
        prediction = (((draft or {}).get('prediction') or {}).get('trucks') or {}).get(car)
        reports.setdefault(car, []).append(learning.day_report(
            car, day, actual, stops, prediction, plan_trips, plan_stops, bundle.truck_capacity(car, erp_capacity),
            learning.daily_l100(intervals, car, day)))
    ends: dict[str, list[tuple[date, float, float]]] = {}
    for iv in intervals:
        ends.setdefault(iv.car_code, []).append((iv.end.astimezone(ac.YEREVAN).date(), iv.liters, iv.km))
    odd: dict[str, list[tuple[date, bool]]] = {}
    for iv in rejected:
        odd.setdefault(iv.car_code, []).append((iv.end.astimezone(ac.YEREVAN).date(), iv.l100 > learning.FUEL_L100[1]))
    refuel_days: dict[str, list[date]] = {}
    for r in refuels:
        when = _parse_day(r.get('eff_date') or r.get('date'))
        if not r.get('superseded') and when is not None:
            refuel_days.setdefault(r['car_code'], []).append(when)
    norms = _fuel_norms(state, bundle, _add_months(first, 1).isoformat())
    known = {t['car_code']: t for t in _garage_trucks(state, bundle)}

    has_dem = dem.terrain_supported() and dem.dem_signature() is not None

    def hills(code: str, r: Mapping[str, Any], l100: float | None) -> tuple[dict[str, Any], float | None]:
        """Норма дня по треку с рельефом (_terrain_norm): (поля строки, норма дня в литрах без округления); рельефа или
        нормы нет — ({}, None): строка прежняя."""
        climb = climbs.get((code, r['day']))
        got = _terrain_norm(bundle.truck_capacity(code, erp_capacity), r['fact']['km'], l100, climb)
        if got is None or climb is None:
            return {}, None
        extra, norm_l = got
        return {'climb_m': round(climb[0]), 'terrain_l': round(extra, 1), 'norm_l': round(norm_l, 1)}, norm_l
    out = []
    for code in sorted(set(known) | set(reports) | set(ends) | set(odd) | set(refuel_days)):
        t = known.get(code, {'car_code': code, 'name': '', 'active': False})
        fuel = garage.fuel_month(ends.get(code, ()), refuel_days.get(code, ()), first, last, odd.get(code, ()))
        km = garage.km_month(reports.get(code, ()))
        if not t['active'] and not km.days and not fuel.refuels and fuel.reason is not None and fuel.reason != 'suspicious':
            continue   # не в работе и в месяце ничего нет
        norm = norms.get(code, {'l100': None, 'source': None, 'learned': None})
        rows = sorted(reports.get(code, ()), key=lambda r: r['day'], reverse=True)
        by_day = {r['day']: hills(code, r, norm['l100']) for r in rows}
        hilly = [(r['fact']['km'], by_day[r['day']][1]) for r in rows if by_day[r['day']][1] is not None]
        hill_km = math.fsum(k for k, _ in hilly)
        # норма месяца по трекам с рельефом (№85): по ней — красный флаг (решение владельца), если треки покрывают не меньше
        # GARAGE_TERRAIN_MIN_COVER км интервалов заправок; иначе — прежняя норма, и ответ говорит почему (basis,
        # terrain_missing: no_dem — нет тайлов высот, no_track — нет дней с треком и нормой, low_coverage — треков мало;
        # норма с рельефом тогда — только рядом)
        terrain_l100 = 100.0 * math.fsum(x for _, x in hilly) / hill_km if hill_km > 0 else None
        covered = terrain_l100 is not None and (fuel.km <= 0 or hill_km >= GARAGE_TERRAIN_MIN_COVER * fuel.km)
        flag_l100 = terrain_l100 if covered else norm['l100']
        hill_note = {'terrain_l100': _r1(terrain_l100), 'terrain_days': len(hilly), 'terrain_km': round(hill_km, 1)}
        basis = ({'basis': 'terrain', **hill_note} if covered else
                 {'basis': 'flat', 'terrain_missing': 'low_coverage', **hill_note} if terrain_l100 is not None else
                 {'basis': 'flat', 'terrain_missing': 'no_dem' if not has_dem else 'no_track'})
        out.append({**t, 'norm': {'l100': _r1(norm['l100']), 'source': norm['source'], 'learned': _r1(norm['learned']),
                                  **basis},
                    'fuel': {'l100': _r1(fuel.l100), 'liters': round(fuel.liters, 1), 'km': round(fuel.km, 1),
                             'intervals': fuel.intervals, 'refuels': fuel.refuels, 'reason': fuel.reason,
                             'too_high': fuel.too_high, 'too_low': fuel.too_low,
                             'delta_pct': _r1(garage.delta_pct(fuel.l100, flag_l100)),
                             'over': garage.over(fuel.l100, flag_l100)},
                    'km': {'days': km.days, 'plan_days': km.plan_days, 'plan': round(km.plan_km, 1),
                           'fact': round(km.fact_km, 1),
                           'delta': round(km.fact_km - km.plan_km, 1) if km.plan_days else None,
                           'delta_pct': _r1(garage.delta_pct(km.fact_km, km.plan_km)) if km.plan_days else None,
                           'over': bool(km.plan_days) and garage.over(km.fact_km, km.plan_km)},
                    'unplanned_stays': km.unplanned_stays, 'order_changes': km.order_changes,
                    'days': [{'day': r['day'], 'plan_km': r['plan']['km'], 'fact_km': r['fact']['km'],
                              'liters': r['fact']['liters'], 'unplanned_stays': r['fact']['unplanned_stays'],
                              'order_changes': r['kpi']['order_changes'],
                              'over': garage.over(r['fact']['km'], r['plan']['km']), **by_day[r['day']][0]}
                             for r in rows]})
    # сначала машины с флагом (и с подозрительными заправками), затем с данными — начальнику гаража на телефоне не листать
    out.sort(key=lambda x: (not (x['fuel']['over'] or x['km']['over'] or x['fuel']['too_high'] or x['fuel']['too_low']),
                            not (x['km']['days'] or x['fuel']['refuels'])))
    return jsonify({**head, 'pending': False, 'failed': False, 'too_old': too_old, 'from': first.isoformat(),
                    'to': last.isoformat(), 'gps_to': gps_to.isoformat() if gps_to >= first else None,
                    'current_month': today.isoformat()[:7], 'oldest_month': oldest.isoformat()[:7],
                    'connected': state.fleet_facts is not None,
                    'has_data': any(x['km']['days'] or x['fuel']['refuels'] or x['fuel']['intervals'] for x in out),
                    'trucks': out,
                    'rules': {'alert_pct': garage.ALERT_PCT, 'fuel_min_km': learning.FUEL_MIN_KM,
                              'fuel_l100': list(learning.FUEL_L100)}})


@bp.get('/api/routes/garage/day')
@_api
def api_garage_day() -> Any:
    """Карта дня машины ?date=&car= для «Նորմ և փաստ»: ровно ответ /api/routes/learning/day (трек GPS, точки плана с
    плановым и фактическим временем, плановые рейсы). Линии плана по дорогам странице гаража не строятся (road-lines —
    API администратора): по прямой между точками."""
    return _day_map()


# --- «Պահեստ»: склад отмечает погрузку с телефона (ответ владельца №78, 9–12, 15) ---
# Роль «warehouse» (app_v2: default-deny, из интернета — как «Гараж») видит только страницу /routes/warehouse и этот API.
# Дни — сегодня и следующий рабочий (вечером грузят на завтра); машины и рейсы — только утверждённого плана (ответ 15).
# Отметку снимают в любое время (ответ владельца №78, 19): склад — любой пользователь роли (смена склада одна, отметка —
# факт о машине, а не о человеке; кто снял — в журнале сервера), логист — на «Развозе» (_loaded_edit). Снятие открепляет
# рейс по общему правилу (dispatch.unmark_loaded: держит утверждение — остаётся закреплённым), страница спрашивает.
WAREHOUSE_STALE = 'Պլանը փոխվել է — թարմացրեք էջը'
WAREHOUSE_NO_PLAN = 'Այս օրվա պլանը դեռ կազմված չէ'
WAREHOUSE_NO_SETUP = 'Պլանի ժամերը հաշվել չի հաջողվում — դիմեք լոգիստին'
WAREHOUSE_BAD_DAY = 'Ընտրեք այսօրը կամ հաջորդ աշխատանքային օրը'


def _warehouse_days(bundle: Bundle) -> list[date]:
    """Дни склада: сегодня (по Еревану) и следующий рабочий."""
    today = _same_day_now().date()
    return [today, dp.next_workday(today, bundle.settings['workdays'], dp.holidays_of(bundle.settings))]


def _warehouse_day(bundle: Bundle, raw: Any) -> date | None:
    """День запроса склада: нет — сегодня; не сегодня и не следующий рабочий — None."""
    days = _warehouse_days(bundle)
    if raw is None:
        return days[0]
    day = _parse_day(raw)
    return day if day in days else None


def _warehouse_body(state: RoutesState, bundle: Bundle, day: date) -> dict[str, Any]:
    """Машины и рейсы утверждённого плана дня для склада: машина, водитель, рейс N, кг, точки, загрузка/выезд, отметка
    «Բեռնված է» (время по Еревану, кто, своя ли и до какого времени её можно снять). Не утверждён — без машин."""
    draft, rev = _stored_draft(state, day)   # без утверждённого плана заказы ERP не нужны — не читаются
    head = {'success': True, 'day': day.isoformat(), 'days': [d.isoformat() for d in _warehouse_days(bundle)],
            'rev': rev}
    if draft is None or not draft.trips:
        return {**head, 'approved': False, 'planned': False, 'trucks': []}
    if draft.approved is None:
        return {**head, 'approved': False, 'planned': True, 'trucks': []}
    # №81: склад грузит то, что отправлено водителям (не неотправленные правки черновика); отметки «Բեռնված է» — черновика
    dd = _load_day(state, bundle, day, draft=draft.for_drivers(), rev=rev)
    if dd.ctx is None:
        raise dp.DispatchError(WAREHOUSE_NO_SETUP)
    plan = dp.plan_view(dd.ctx, dd.stops, dd.draft, _stop_info(dd), explain=False)
    seats = dp.crew_view(dd.draft, _crew(state, day, bundle.trucks)[0])   # водители — плана водителей, как и рейсы
    by_id = {t.id: t for t in dd.draft.trips}
    marks = {t.id: t.loaded for t in draft.trips}
    changing = _changing_trucks(draft)   # логист меняет машину и не отправил — ни отметки, ни печати (одно правило)
    trucks = []
    for t in plan['trucks']:
        trips = []
        for no, tr in enumerate(t['trips'], 1):
            mark = marks.get(tr['id'])   # рейса водителей нет в черновике — отметки нет (машина и так «меняется»)
            loaded = {'at': mark['at'][11:16], 'by': mark['by']} if mark is not None else None
            trips.append({'id': tr['id'], 'no': no, 'of': len(t['trips']), 'kg': tr['kg'], 'stops': len(tr['stops']),
                          'loading_start': tr['loading_start'], 'depart': tr['depart'],
                          'preloaded': bool(tr.get('preloaded')), 'loaded': loaded,
                          **({'changing': True} if t['car_code'] in changing else {})})
        trucks.append({'car_code': t['car_code'], 'name': t['name'], 'capacity_kg': t['capacity_kg'],
                       'driver': (seats.get(t['car_code']) or {}).get('name'), 'trips': trips,
                       **({'changing': True} if t['car_code'] in changing else {})})
    return {**head, 'approved': True, 'planned': True, 'trucks': trucks}


def _changing_trucks(draft: dp.Draft) -> set[str]:
    """Машины, которые логист меняет и ещё не отправил водителям (№81) — склад их не грузит: ни «Բեռնված է» (поставить и
    снять), ни Բեռնագիր (ответы владельца 07.10 «Запретить до отправки», «Запретить и отметку» — одно правило). Машина
    «меняется», если у водителей и в черновике разные:
    - её рейсы (номер и точки по порядку) — рейс убрали, переложили, поменяли точки или их порядок;
    - доли её магазинов (dp.trip_parts — тот же расчёт, что у листа): убрали магазин из рейса другой машины, и доля здесь
      стала целой;
    - отбор заказов дня (dp.unsent: orders) — груз любой машины, «меняются» все.
    План не отправляли — водители видят сам черновик, правок «до отправки» нет."""
    if draft.sent is None:
        return set()
    sent = draft.for_drivers()
    every = {t.truck for d in (draft, sent) for t in d.trips}
    out = dp.unsent(draft)
    if out is not None and out['orders']:
        return every
    now, was = dp.trip_parts(draft), dp.trip_parts(sent)

    def trips(d: dp.Draft, car: str) -> list[tuple[int, tuple[int, ...]]]:
        return sorted((t.id, tuple(t.stops)) for t in d.trips if t.truck == car)
    return {car for car in every
            if (out is not None and car in out['trucks']) or trips(draft, car) != trips(sent, car)
            or any(now[t.id] != was[t.id] for t in draft.trips if t.truck == car)}


@bp.get('/api/routes/warehouse')
@_api
def api_warehouse() -> Any:
    """Машины дня склада (?date= — сегодня или следующий рабочий; нет — сегодня), _warehouse_body."""
    state = _state()
    bundle = _bundle(state)
    day = _warehouse_day(bundle, request.args.get('date'))
    if day is None:
        return _bad_request({'date': WAREHOUSE_BAD_DAY})
    try:
        return jsonify(_warehouse_body(state, bundle, day))
    except dp.DispatchError as e:
        return _bad_request({'_': str(e)})


@bp.get('/api/routes/warehouse/goods')
@_api
def api_warehouse_goods() -> Any:
    """Товар рейса машины для склада (?date=&truck=&trip=&rev=): строки Բեռնագիր этого рейса (waybill.truck_waybill —
    код, товар, количество, упаковки, кг; ERP — только чтение). Только утверждённый план; rev или рейс не те — 409."""
    state = _state()
    bundle = _bundle(state)
    day = _warehouse_day(bundle, request.args.get('date'))
    car = (request.args.get('truck') or '').strip()
    trip, rev = request.args.get('trip') or '', request.args.get('rev') or ''
    if day is None:
        return _bad_request({'date': WAREHOUSE_BAD_DAY})
    if not car or len(car) > 64 or not _REV_RE.match(trip) or not _REV_RE.match(rev):
        return _bad_request({'_': WAREHOUSE_STALE})
    dd = _load_day(state, bundle, day)
    if int(rev) != dd.rev or dd.draft is None or dd.draft.approved is None:
        return _conflict(WAREHOUSE_STALE)
    dd = _load_day(state, bundle, day, draft=dd.draft.for_drivers(), rev=dd.rev)   # №81: товар — по плану водителей
    if dd.ctx is None:
        return _bad_request({'_': WAREHOUSE_NO_SETUP})
    plan = dp.plan_view(dd.ctx, dd.stops, dd.draft, _stop_info(dd), explain=False)
    truck = next((t for t in plan['trucks'] if t['car_code'] == car), None)
    if truck is None or all(tr['id'] != int(trip) for tr in truck['trips']):
        return _conflict(WAREHOUSE_STALE)
    if state.waybill_loader is None:
        raise ErpError('Загрузчик строк заказов не подключён')
    lines = state.waybill_loader([o['isn'] for tr in truck['trips'] for s in tr['stops'] for o in s['orders']])
    got = next(x for x in wb.truck_waybill(plan, car, lines)['trips'] if x['id'] == int(trip))
    keys = ('code', 'name', 'unit', 'qty', 'pack', 'packs', 'loose', 'kg', 'unknown')
    return jsonify({'success': True, 'kg': got['kg'], 'rows': [{k: r[k] for k in keys} for r in got['rows']]})


@bp.get('/api/routes/warehouse/waybill')
@_api
def api_warehouse_waybill() -> Any:
    """Բեռնագիր машины для склада (?date=&truck=&rev=): тот же ответ, что у «Развоза» (_waybill_body), — страница печатает
    тот же документ; плюс weekday (у склада дня недели нет). План — как у всей страницы склада: утверждённый (ответ 15),
    отправленный водителям (№81; не отправляли — он и есть черновик «Развоза»). rev не тот или машины нет — 409; у машины
    неотправленные правки логиста — 409 с changing (ответы владельца 07.10, _changing_trucks); отказы — без ERP. Без basis
    (сверка «Развоза»: номера заказов ERP). API «Развоза» складу закрыт (app_v2: default-deny) — Բեռնագիր только здесь."""
    state = _state()
    bundle = _bundle(state)
    day = _warehouse_day(bundle, request.args.get('date'))
    car = (request.args.get('truck') or '').strip()
    rev = request.args.get('rev') or ''
    if day is None:
        return _bad_request({'date': WAREHOUSE_BAD_DAY})
    if not car or len(car) > 64 or not _REV_RE.match(rev):
        return _bad_request({'_': WAREHOUSE_STALE})
    draft, cur = _stored_draft(state, day)   # проверки — по сохранённому черновику, до заказов и строк ERP
    if int(rev) != cur or draft is None or draft.approved is None:
        return _conflict(WAREHOUSE_STALE)
    if car in _changing_trucks(draft):       # ответ владельца 07.10: до «Ուղարկել» — не печатать
        return jsonify({'success': False, 'error': WAREHOUSE_WAYBILL_CHANGING, 'conflict': True, 'changing': True}), 409
    if all(t.truck != car for t in draft.for_drivers().trips):
        return _conflict(WAREHOUSE_STALE)
    dd = _load_day(state, bundle, day, draft=draft.for_drivers(), rev=cur)   # №81: как страница — план водителей
    if dd.ctx is None:
        return _bad_request({'_': WAREHOUSE_NO_SETUP})
    plan = dp.plan_view(dd.ctx, dd.stops, dd.draft, _stop_info(dd), explain=False)
    truck = next((t for t in plan['trucks'] if t['car_code'] == car), None)
    if truck is None:
        return _conflict(WAREHOUSE_STALE)
    logger.info('[Routes] Склад: Բեռնագիր %s на %s (%s)', car, day, session.get('username'))
    body = _waybill_body(state, dd, plan, truck)
    # basis (номера заказов ERP, доли) — сверка «Развоза»; на лист не нужен, роли из интернета не отдаётся. Порядок
    # погрузки складу — только номера точек (решение владельца №87): код и название магазина роли не отдаются
    return jsonify({**body, 'trips': [{**{k: v for k, v in tr.items() if k != 'basis'},
                                       'loading': [{k: v for k, v in x.items() if k not in ('code', 'name')}
                                                   for x in tr['loading']]} for tr in body['trips']],
                    'weekday': day.isoweekday()})


WAREHOUSE_CHANGING = 'Լոգիստը փոխել է այս երթը և դեռ չի ուղարկել վարորդին — զանգահարեք լոգիստին'
WAREHOUSE_WAYBILL_CHANGING = ('Լոգիստը փոխել է այս մեքենայի երթերը և դեռ չի ուղարկել վարորդին — բեռնագիրը կարելի է տպել '
                              'ուղարկելուց հետո, զանգահարեք լոգիստին')


@bp.post('/api/routes/warehouse/loaded')
@_api
def api_warehouse_loaded() -> Any:
    """«Բեռնված է» со склада: {"date", "rev", "trip", "loaded": true | false}. Отметить — только по утверждённому плану
    (иначе 409, dispatch.mark_loaded); снять — в любое время, любой пользователь склада (ответ №78, 19). rev — номер
    плана на странице: план изменился — 409 «թարմացրեք էջը» (как у правок «Развоза»). В ответе — день склада заново."""
    payload, error = _json_body()
    if error is not None:
        return error
    if not isinstance(payload, dict) or not isinstance(payload.get('loaded'), bool):
        return _bad_request({'_': WAREHOUSE_STALE})
    state = _state()
    bundle = _bundle(state)
    day = _warehouse_day(bundle, payload.get('date')) if payload.get('date') is not None else None
    if day is None:
        return _bad_request({'date': WAREHOUSE_BAD_DAY})
    draft, rev = _stored_draft(state, day)
    if draft is None:
        return _conflict(WAREHOUSE_NO_PLAN)
    if payload.get('rev') != rev:
        return _conflict(WAREHOUSE_STALE)
    # №81 и ответ владельца 07.10 «Запретить и отметку»: машину рейса логист меняет и не отправил — ни поставить, ни снять
    # (снятие убрало бы защиту груза, который уже в машине, пока логист её правит: его правки и «Ուղարկել» перестали бы
    # спрашивать подтверждение). Машина рейса — у водителей, нет там — в черновике.
    car = next((t.truck for d in (draft.for_drivers(), draft) for t in d.trips if t.id == payload.get('trip')), None)
    if car is not None and car in _changing_trucks(draft):
        return _conflict(WAREHOUSE_CHANGING)
    me = session.get('username')
    now = _same_day_now()
    try:
        if payload['loaded']:
            dp.mark_loaded(draft, payload.get('trip'), now.isoformat(timespec='seconds'), me)
        else:
            dp.unmark_loaded(draft, payload.get('trip'))
    except dp.DispatchError as e:
        return _conflict(str(e))
    if state.store.save_dispatch(day.isoformat(), draft.to_json(), me, expected_rev=rev) is None:
        return _conflict(WAREHOUSE_STALE)
    logger.info('[Routes] Склад: рейс %s на %s — %s (%s)', payload.get('trip'), day,
                'загружен' if payload['loaded'] else 'отметка снята', me)
    try:
        return jsonify(_warehouse_body(state, bundle, day))
    except dp.DispatchError as e:
        return _bad_request({'_': str(e)})


# --- «Աշխատավարձ» (09-crew-pay.md): зарплата առաքիչ за месяц по формуле владельца, только администратору ---

PAY_MONTHS = 12            # выбор месяца: этот и 11 до него
# кэш месяцев: выбор + ещё TREND_MONTHS − 1 до самого раннего — тренд «Առաքիչների KPI» (crew_kpi) не вытесняет сам себя
PAY_CACHE_MONTHS = PAY_MONTHS + ck.TREND_MONTHS - 1
PAY_TTL_SECONDS = 300      # накладные месяца из ERP: CSV, правка параметров и повтор не перечитывают ERP
PAY_FORBIDDEN = 'Доступ запрещён'


@bp.get('/routes/pay')
@_admin_only
def pay_page() -> str:
    return render_template('routes_pay.html')


def _pay_month(raw: str, today: date) -> date | None:
    """«YYYY-MM» из PAY_MONTHS последних месяцев → первый день; пусто — этот месяц; иначе None."""
    first = _garage_month(raw, today)
    return first if first is not None and first >= _add_months(today.replace(day=1), 1 - PAY_MONTHS) else None


def _pay_data(state: RoutesState, since: date, until: date) -> tuple[cp.CrewData, dict[Any, dict[Any, Any]]]:
    """(накладные месяца, memo туров плановых км этих накладных) — из кэша PAY_TTL_SECONDS или из ERP."""
    if state.crew_pay_loader is None:
        raise ErpError('Загрузчик накладных не подключён')
    key = (since, until)
    with state.crew_pay_lock:
        hit = state.crew_pay_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < PAY_TTL_SECONDS:
            state.crew_pay_cache[key] = state.crew_pay_cache.pop(key)   # LRU: прочитанный — в конец очереди
            return hit[1], hit[2]
    data = state.crew_pay_loader(since, until)
    memo: dict[Any, dict[Any, Any]] = {}
    with state.crew_pay_lock:
        state.crew_pay_cache.pop(key, None)   # перечитанный месяц — в конец очереди: вытесняется самый давний
        state.crew_pay_cache[key] = (time.monotonic(), data, memo)
        while len(state.crew_pay_cache) > PAY_CACHE_MONTHS:
            state.crew_pay_cache.pop(next(iter(state.crew_pay_cache)))
    return data, memo


@dataclass(frozen=True)
class _PayMonth:
    first: date
    params: cp.Params
    params_at: str | None    # когда и кем параметры сохранены (None — значения по умолчанию)
    params_by: str | None
    result: cp.Result
    calendar_warning: str | None = None   # календарь «Маршрутов» не прочитан: текущий месяц посчитан от D
    km_warning: str | None = None         # км не посчитаны (нет склада) или посчитаны приблизительно (по прямой × 1,3)


def _calendar_rest(today: date, settings: Mapping[str, Any]) -> frozenset[date]:
    """Рабочие дни с today (включительно) до конца месяца по календарю «Маршрутов»: дни недели settings['workdays'] без
    нерабочих дат (№64) — тот же dp.is_workday, что у «Развоза»."""
    workdays, off = settings['workdays'], dp.holidays_of(settings)
    end = _add_months(today.replace(day=1), 1)
    return frozenset(d for d in (today + timedelta(days=i) for i in range((end - today).days))
                     if dp.is_workday(d, workdays, off))


PAY_CALENDAR_WARNING = ('Աշխատանքային օրացույցը (կարգավորումները) չհաջողվեց կարդալ․ ընթացիկ ամիսը հաշվված է միայն '
                        'առաքման օրերով, ուստի ֆիքսը և նորմը կարող են ավելի մեծ լինել։')


PAY_KM_NO_DEPOT = ('Պահեստի կոորդինատները նշված չեն (Կարգավորումներ) — կմ-ն հաշվված չէ, գործավարձը հաշվված է առանց '
                   'կմ-ի։')
PAY_KM_NO_ROADS = 'Կմ-ն մոտավոր է (ճանապարհների քարտեզ չկա)՝ ուղիղ գծով × 1,3։'
PAY_KM_FAILED = 'Կմ-ն չհաշվվեց, գործավարձը հաշվված է առանց կմ-ի՝ '


def _pay_tours(state: RoutesState, data: cp.CrewData, params: cp.Params, bundle: Bundle | None,
               memo: dict[Any, dict[Any, Any]]) -> tuple[cp.Tours | None, str | None]:
    """Плановые км дней месяца (cp.plan_tours): склад — из настроек, координаты магазинов — те же, что у обзора и
    «Развоза» (evaluate.visit_coord: ручная → водителей → ERP → GPS менеджеров), км — общие дороги раздела (state.roads:
    дисковый кэш расстояний уже тёплый). Блокировка — только та, что внутри RoadDistances.ensure. Карты нет или она
    сломана — по прямой × 1,3 с предупреждением. → (туры или None — склада нет, предупреждение)."""
    if bundle is None or bundle.depot is None:
        return None, PAY_KM_NO_DEPOT
    days = cp.day_stores(data, params)
    customers = {c for stores in days for c in stores}
    if not customers:
        return cp.Tours({}), None
    snap, _ = state.snapshots.cached()
    driver = _driver_points(state)
    coords = {cid: pt for cid in customers
              if (pt := evaluate.visit_coord(snap, cid, 0, bundle.geo_overrides, driver).point) is not None}
    roads = state.roads.get() if state.roads is not None else None
    if roads is not None:
        started = time.perf_counter()
        roads.ensure([bundle.depot, *coords.values()])
        logger.info('[Routes] Աշխատավարձ: дороги для %d магазинов за %.1f с', len(coords), time.perf_counter() - started)
    ok = roads is not None and not roads.failed
    road_km: Callable[[Point, Point], float | None] = roads.km if roads is not None and ok else (lambda a, b: None)
    tours, straight = cp.plan_tours(bundle.depot, coords, days, road_km,
                                    memo.setdefault((bundle.depot, roads_version(roads)), {}))
    if not ok:
        return tours, PAY_KM_NO_ROADS
    if straight:
        return tours, (f'Կմ-ն մասամբ մոտավոր է․ {straight} հատված հաշվված է ուղիղ գծով × 1,3 (կետը ճանապարհից հեռու է '
                       'կամ ճանապարհ չի գտնվել)։')
    return tours, None


def _pay_month_result(state: RoutesState, first: date, today: date,
                      saved: tuple[cp.Params, str | None, str | None] | None = None) -> _PayMonth:
    """Расчёт месяца first: текущий — по сегодня включительно (накладные «из будущего» не берём), фикс и норма — от
    D_month: прошедшие дни с доставкой + рабочие дни календаря с сегодня до конца месяца (начислено по сегодня);
    прошлый — от D, как раньше. Календарь не читается — текущий месяц от D и предупреждение, а не 500; без склада (или
    настроек) км не считаются — тоже предупреждение. rate_km = 0 — дороги и снимок не трогаются. saved — уже
    прочитанные параметры (store.crew_pay_params), чтобы не читать их на каждый месяц."""
    until = min(_add_months(first, 1), today + timedelta(days=1))
    params, at, by = saved or state.store.crew_pay_params()
    try:
        bundle: Bundle | None = state.store.load()
    except StoreError:
        logger.exception('[Routes] Աշխատավարձ: настройки не читаются — без календаря и склада')
        bundle = None
    rest, warning = None, None
    if first == today.replace(day=1):
        if bundle is not None:
            rest = _calendar_rest(today, bundle.settings)
        else:
            warning = PAY_CALENDAR_WARNING
    data, memo = _pay_data(state, first, until)
    tours, km_warning = None, None
    if params.rate_km > 0:
        try:   # снимок, координаты, дороги: сбой км не ломает зарплату — без км и с предупреждением
            tours, km_warning = _pay_tours(state, data, params, bundle, memo)
        except ErpError:
            logger.exception('[Routes] Աշխատավարձ: снимок ERP для координат не прочитан — без км')
            km_warning = PAY_KM_FAILED + 'խանութների կոորդինատները ERP-ից չհաջողվեց կարդալ։ Կրկնեք մի փոքր ուշ։'
        except Exception:
            logger.exception('[Routes] Աշխատավարձ: км не посчитаны — без км')
            km_warning = PAY_KM_FAILED + 'ներքին սխալ (մանրամասները՝ սերվերի մատյանում)։'
    return _PayMonth(first, params, at, by, cp.compute(data, params, rest, tours), warning, km_warning)


def _pay_no_coords_warning(result: cp.Result) -> str | None:
    """Точки без координат (их км — 0): сколько и у кого — на странице и в CSV «Ստուգել»."""
    rows = [r for r in result.rows if r.no_coords]
    if not result.km_counted or not rows:
        return None
    return (f'{sum(r.no_coords for r in rows)} կետ առանց կոորդինատների — կմ-ն պակաս է հաշվված ('
            + ', '.join(f'{r.name or r.code}՝ {r.no_coords}' for r in rows) + ')։')


def _pay_row_json(r: cp.Row) -> dict[str, Any]:
    return {'agent_ids': list(r.agent_ids), 'code': r.code, 'name': r.name, 'days': r.days, 'points': r.points,
            'tonnes': round(r.tonnes, 3), 'km': round(r.km, 1), 'no_coords': r.no_coords, 'sales': r.sales, 'fix': r.fix,
            'piece': r.piece, 'minimum': r.minimum, 'pay': r.pay, 'old': r.old, 'diff': r.diff,
            'min_applied': r.min_applied,
            'by_day': [{'date': d.day.isoformat(), 'points': d.points, 'tonnes': round(d.tonnes, 3), 'km': round(d.km, 1),
                        'no_coords': d.no_coords, 'piece': d.piece} for d in r.by_day]}


def _pay_request() -> tuple[_PayMonth | None, Any]:
    """(месяц из ?month=, None) или (None, ответ 400)."""
    today = _yerevan_now().date()   # дата Еревана, а не часов сервера: месяц и «по сегодня»
    first = _pay_month(request.args.get('month', ''), today)
    if first is None:
        return None, _bad_request({'month': 'Ընտրեք ամիսը վերջին 12 ամիսներից'})
    return _pay_month_result(_state(), first, today), None


@bp.get('/api/routes/pay')
@_admin_only
@_api
def api_pay() -> Any:
    """Месяц ?month=YYYY-MM (пусто — этот): люди, итог, D, параметры формулы и список месяцев для выбора."""
    m, error = _pay_request()
    if m is None:
        return error
    this = _yerevan_now().date().replace(day=1)
    result = m.result
    return jsonify({'success': True, 'month': m.first.strftime('%Y-%m'), 'current': m.first == this,
                    'months': [_add_months(this, -i).strftime('%Y-%m') for i in range(PAY_MONTHS)],
                    'params': m.params.json(), 'params_updated_at': m.params_at, 'params_updated_by': m.params_by,
                    'workdays': result.workdays, 'workdays_month': result.workdays_month,
                    'unknown_codes': list(result.unknown_codes),
                    'overlapping_codes': [list(c) for c in result.overlapping_codes],
                    'excluded_kin': list(result.excluded_kin), 'calendar_warning': m.calendar_warning,
                    'km_counted': result.km_counted,
                    'km_warnings': [w for w in (m.km_warning, _pay_no_coords_warning(result)) if w],
                    'rows': [_pay_row_json(r) for r in result.rows],
                    'totals': {k: round(v, 3) if k == 'tonnes' else round(v, 1) if k == 'km' else v
                               for k, v in cp.totals(result.rows).items()}})


def _csv_cell(value: Any) -> str:
    """Текст ячейки CSV: начало «=+-@» Excel принял бы за формулу — экранируем апострофом (как missing.csv)."""
    text = str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


# Подписи параметров в шапке CSV — как на странице
PAY_PARAM_LABELS = (('fix', 'Ֆիքս ամսական, ֏'), ('rate_point', 'Մեկ կետի համար, ֏'),
                    ('rate_tonne', 'Մեկ տոննայի համար, ֏'), ('rate_km', 'Մեկ կմ-ի համար, ֏'),
                    ('minimum', 'Նվազագույն ամսական, ֏'),
                    ('norm_per_day', 'Նորմ՝ կետ մեկ աշխատանքային օրում'), ('old_fix', 'Հին սխեմա՝ ֆիքս, ֏'),
                    ('old_pct', 'Հին սխեմա՝ վաճառքի տոկոս, %'), ('excluded_lines', 'Չհաշվվող գծեր'),
                    ('excluded_people', 'Չհաշվվող առաքիչներ'))


@bp.get('/api/routes/pay.csv')
@_admin_only
@_api
def api_pay_csv() -> Any:
    """Таблица месяца для Excel: «;», десятичная запятая, UTF-8 с BOM. Параметры применяются и к прошлым месяцам,
    поэтому в начале файла — с какими параметрами и когда он посчитан."""
    import csv
    import io
    m, error = _pay_request()
    if m is None:
        return error
    result, params = m.result, m.params

    def n(x: float) -> str:   # число параметра без лишних нулей, десятичная запятая (Excel ru)
        return f'{x:.3f}'.rstrip('0').rstrip('.').replace('.', ',')

    out = io.StringIO()
    w = csv.writer(out, delimiter=';', lineterminator='\r\n')
    w.writerow(['Աշխատավարձ', m.first.strftime('%Y-%m')])
    w.writerow(['Հաշվված է', _yerevan_now().strftime('%Y-%m-%d %H:%M')])
    if m.params_at:
        w.writerow(['Պարամետրերը փոխվել են', m.params_at.replace('T', ' ')[:16], _csv_cell(m.params_by or '')])
    for key, label in PAY_PARAM_LABELS:
        value = getattr(params, key)
        w.writerow([label, _csv_cell(', '.join(value)) if isinstance(value, tuple) else n(value)])
    # D_month — знаменатель фикса и нормы: у текущего месяца — рабочие дни всего месяца, в таблице «Աշխատանքային օրեր» — D
    w.writerow(['Աշխատանքային օրեր ամսում', result.workdays_month])
    # то же, что предупреждения на странице: тёзки с общими днями и исключённые с учтённым кодом того же имени
    for codes in result.overlapping_codes:
        w.writerow(['Ստուգել', _csv_cell('Նույն անունով կոդեր, որոնցից մի քանիսը աշխատել են նույն օրերին՝ '
                                         + ', '.join(codes) + ' — հաշվված են առանձին')])
    if m.calendar_warning:
        w.writerow(['Ստուգել', m.calendar_warning])
    if result.excluded_kin:
        w.writerow(['Ստուգել', _csv_cell('Հաշվվում են, բայց նույն անունով կոդ կա չհաշվվողների մեջ՝ '
                                         + ', '.join(result.excluded_kin))])
    for warning in (m.km_warning, _pay_no_coords_warning(result)):
        if warning:
            w.writerow(['Ստուգել', _csv_cell(warning)])
    w.writerow([])
    w.writerow(['Կոդ', 'Առաքիչ', 'Օրեր', 'Աշխատանքային օրեր', 'Կետեր', 'Տոննա', 'Կմ', 'Ֆիքս', 'Գործավարձ', 'Նվազագույն',
                'Վճարել', 'Նվազագույնը կիրառված է', f'Հին սխեմա ({n(params.old_pct)}%)', 'Տարբերություն'])
    for r in result.rows:
        w.writerow([_csv_cell(r.code), _csv_cell(r.name), r.days, result.workdays, r.points, f'{r.tonnes:.3f}'.replace('.', ','),
                    f'{r.km:.1f}'.replace('.', ','), r.fix, r.piece, r.minimum, r.pay, 'այո' if r.min_applied else '',
                    r.old, r.diff])
    if result.rows:
        s = cp.totals(result.rows)
        w.writerow(['', 'Ընդամենը', '', result.workdays, s['points'], f"{s['tonnes']:.3f}".replace('.', ','),
                    f"{s['km']:.1f}".replace('.', ','), s['fix'], s['piece'], '', s['pay'], '', s['old'], s['diff']])
    name = f'crew-pay-{m.first:%Y-%m}.csv'
    return Response('\ufeff' + out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


def _pay_params_body(state: RoutesState) -> dict[str, Any]:
    """Параметры формулы без ERP: форма редактируется, даже когда ERP недоступна. Битая запись в базе — значения по
    умолчанию и store_error: сохранение формы перезапишет её."""
    try:
        params, at, by = state.store.crew_pay_params()
        broken = False
    except StoreError:
        logger.exception('[Routes] Աշխատավարձ: параметры в базе не читаются — форма с значениями по умолчанию')
        params, at, by, broken = cp.Params(), None, None, True
    return {'success': True, 'params': params.json(), 'updated_at': at, 'updated_by': by, 'store_error': broken}


@bp.get('/api/routes/pay/params')
@_admin_only
@_api
def api_pay_params_get() -> Any:
    return jsonify(_pay_params_body(_state()))


@bp.post('/api/routes/pay/params')
@_admin_only
@_api
def api_pay_params() -> Any:
    """Сохранить параметры формулы (все поля; ошибки — по полям). ERP не трогается: только route_optimizer.db; битую
    запись перезаписывает. Ответ — как у GET: параметры и кто/когда сохранил."""
    payload, error = _json_body()
    if error is not None:
        return error
    params, errors = cp.check_params(payload)
    if params is None:
        return _bad_request(errors)
    state = _state()
    state.store.save_crew_pay_params(params, session.get('username'))
    _cost_changed(state)   # ставки за точку и тонну — и в «Առաքման արժեք»
    logger.info('[Routes] Աշխատավարձ: параметры сохранены пользователем %s: %s', session.get('username'), params.json())
    return jsonify(_pay_params_body(state))


# --- «Առաքիչների KPI» (crew_kpi): показатели առաքիչ за месяц и тренд — из тех же расчётов, что «Աշխատավարձ» ---

@bp.get('/routes/araqich')
@_admin_only
def araqich_kpi_page() -> str:
    return render_template('routes_araqich.html')


def _round_or_none(x: float | None, n: int) -> float | None:
    return None if x is None else round(x, n)


def _kpi_stats_json(s: ck.Stats | None) -> dict[str, Any] | None:
    if s is None:
        return None
    r = _round_or_none
    return {'days': s.days, 'points': s.points, 'tonnes': round(s.tonnes, 3), 'sales': s.sales, 'pay': s.pay,
            'attendance': r(s.attendance, 4), 'points_day': round(s.points_day, 2), 'tonnes_day': round(s.tonnes_day, 3),
            'kg_point': r(s.kg_point, 1), 'sales_day': round(s.sales_day), 'norm': r(s.norm, 4),
            'cost_tonne': r(s.cost_tonne, 0)}


def _kpi_team_json(t: ck.Team | None) -> dict[str, Any] | None:
    if t is None:
        return None
    r = _round_or_none
    return {'people': t.people, 'workdays': t.workdays, 'points': t.points, 'tonnes': round(t.tonnes, 3),
            'sales': t.sales, 'pay': t.pay, 'median_points_day': r(t.median_points_day, 2),
            'tonnes_day': r(t.tonnes_day, 3), 'norm': r(t.norm, 4), 'cost_tonne': r(t.cost_tonne, 0)}


@bp.get('/api/routes/araqich')
@_admin_only
@_api
def api_araqich_kpi() -> Any:
    """Месяц ?month=YYYY-MM (пусто — этот; выбор — как у «Աշխատավարձ»): люди с показателями и сравнением с прошлым
    месяцем, команда, тренд кетов в день за TREND_MONTHS месяцев. Месяцы читаются из ERP через кэш «Աշխատավարձ»."""
    today = _yerevan_now().date()
    first = _pay_month(request.args.get('month', ''), today)
    if first is None:
        return _bad_request({'month': 'Ընտրեք ամիսը վերջին 12 ամիսներից'})
    state = _state()
    saved = state.store.crew_pay_params()
    pays = [_pay_month_result(state, _add_months(first, -i), today, saved) for i in range(ck.TREND_MONTHS - 1, -1, -1)]
    months = [ck.Month(m.first, m.params.norm_per_day, m.result) for m in pays]
    rep = ck.report(months)
    this = today.replace(day=1)
    return jsonify({
        'success': True, 'month': first.strftime('%Y-%m'), 'current': first == this,
        'months': [_add_months(this, -i).strftime('%Y-%m') for i in range(PAY_MONTHS)],
        'trend_months': [m.strftime('%Y-%m') for m in rep.trend_months],
        'norm_per_day': months[-1].norm_per_day, 'min_days': ck.MIN_DAYS, 'good': ck.GOOD, 'bad': ck.BAD,
        'calendar_warning': pays[-1].calendar_warning,   # текущий месяц от D: фикс полнее, ֏/տոննա завышен
        'team': _kpi_team_json(rep.team), 'prev_team': _kpi_team_json(rep.prev_team),
        'people': [{'code': p.code, 'name': p.name, 'grade': p.grade,
                    'vs_median': _round_or_none(p.vs_median, 4),
                    'now': _kpi_stats_json(p.now), 'prev': _kpi_stats_json(p.prev),
                    'trend': [_round_or_none(x, 2) for x in p.trend],
                    'by_day': [{'date': d.day.isoformat(), 'points': d.points, 'tonnes': round(d.tonnes, 3)}
                               for d in p.by_day]}
                   for p in rep.people]})


# --- «Առաքման արժեք» (№87, п. 6; cost_to_serve): стоимость обслуживания магазина против его продаж, только администратору ---

COST_TTL_SECONDS = 300     # накладные периода из ERP: CSV, смена наценки и повтор не перечитывают ERP
COST_SALES_KEPT = 6        # периодов накладных в кэше
COST_DAYS_KEPT = 200       # дней расчёта в кэше (больше периода 92 дня — с запасом на смену периода)
COST_RETRY_SECONDS = 60    # день, посчитанный без рельефа (подъёмы точек ещё в фоне, _cost_leg), — пересчитать не раньше
COST_DAY_TTL_SECONDS = 3600   # страховка: день пересчитывается не реже раза в час, даже если ключ входа тот же
COST_WAIT_S = 10.0         # запрос ждёт фоновый расчёт периода не дольше; дальше — {pending: true}, страница спросит снова
COST_FRESH_S = 30.0        # готовый итог периода моложе — отдаётся без нового расчёта (CSV и повтор сразу за страницей)
COST_PENDING = 'Հաշվարկը դեռ ընթանում է (առաջին անգամ՝ մինչև մի քանի րոպե)։ Կրկնեք մի փոքր ուշ։'
COST_NO_CTX = 'Սկզբում նշեք պահեստը և մեքենաների տոննաժն ու ծախսը կարգավորումներում'


@bp.get('/routes/cost')
@_admin_only
def cost_page() -> str:
    return render_template('routes_cost.html')


def _cost_sales(state: RoutesState, since: date, until: date) -> cts.SalesData:
    """Накладные ERP за [since, until) — кэш COST_TTL_SECONDS, не больше COST_SALES_KEPT периодов."""
    if state.cost_sales_loader is None:
        raise ErpError('Загрузчик накладных не подключён')
    key = (since, until)
    with state.cost_lock:
        hit = state.cost_sales_cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < COST_TTL_SECONDS:
        return hit[1]
    data = state.cost_sales_loader(since, until)
    with state.cost_lock:
        now = time.monotonic()
        for k in [k for k, (at, _) in state.cost_sales_cache.items() if now - at >= COST_TTL_SECONDS]:
            del state.cost_sales_cache[k]   # истёкшие не держим в памяти
        state.cost_sales_cache.pop(key, None)
        state.cost_sales_cache[key] = (now, data)
        while len(state.cost_sales_cache) > COST_SALES_KEPT:
            state.cost_sales_cache.pop(next(iter(state.cost_sales_cache)))
    return data


def _cost_margin(state: RoutesState) -> tuple[float | None, str | None, str | None, bool]:
    """(наценка, когда, кто, запись битая): битая — без красного и предупреждение, а не 500 (сохранение её перезапишет)."""
    try:
        return (*state.store.cost_margin(), False)
    except StoreError:
        logger.exception('[Routes] Առաքման արժեք: наценка в базе не читается — без красного')
        return None, None, None, True


@dataclass(frozen=True)
class _CostResult:
    since: date
    until: date
    report: cts.Report
    names: Mapping[int, tuple[str, str]]
    params: cp.Params
    margin: float | None
    margin_at: str | None
    margin_by: str | None
    margin_broken: bool
    sources: Mapping[str, int]      # дней: отправленный план (sent), черновик до первой отправки (draft), неотправленный
                                    # черновик после неё / сегодня — не считан (unsent), битая запись (broken)
    fuel_price: float
    fuel_price_estimated: bool


def _cost_plans(state: RoutesState, since: date, until: date) -> tuple[dict[date, tuple[str, int, list[cts.Trip]]],
                                                                       dict[str, int]]:
    """Рейсы дней периода: отправленный водителям план (№81); черновик — только прошедшего дня раньше первой отправки
    (cts.plan_source, шапка cost_to_serve)."""
    days: dict[date, tuple[str, int, list[cts.Trip]]] = {}
    sources = {'sent': 0, 'draft': 0, 'unsent': 0, 'broken': 0}
    first = state.store.first_sent_day()
    first_sent = date.fromisoformat(first) if first else None
    today = _yerevan_now().date()
    for raw_day, raw, rev in state.store.dispatch_range(since.isoformat(), until.isoformat()):
        try:
            day: date | None = date.fromisoformat(raw_day)
        except ValueError:
            day = None
        if raw is None or day is None:
            sources['broken'] += 1
            continue
        draft = dp.Draft.from_json(raw)
        source = cts.plan_source(day, draft.sent is not None, first_sent, today)
        plan = draft.for_drivers() if source == 'sent' else draft
        trips = [cts.Trip(day, t.id, t.truck, tuple(t.stops)) for t in plan.trips if t.stops]
        if not trips:
            continue
        if source is None:
            sources['unsent'] += 1
            continue
        days[day] = (source, rev, trips)
        sources[source] += 1
    return days, sources


def _cost_leg(ctx: dp.DayContext, points: Sequence[Point]) -> tuple[cts.CostFn, bool, bool]:
    """(расход рейса для cost_to_serve, рельеф включён, подъёмы ещё считаются). Та же модель, что fl.trip_running_cost
    (plan_view: км norms.for_trucks(), литры с рельефом №85 — route_cost по участкам), но участки и подъёмы — из памяти:
    варианты «без магазина» повторяют участки рейса, а подъёмы всех точек периода проверяются один раз (ensure_climb
    подмножества точек — то же, что всех). Подъёмы ещё в фоне (pending) — литры без рельефа, как у _leg_climbs."""
    norms = ctx.norms.for_trucks()
    roads = norms.roads
    terrain = roads is not None and bool(getattr(roads, 'terrain', False))
    climbs = terrain and roads.ensure_climb([ctx.depot, *points])
    legs: dict[tuple[Point, Point], tuple[float, float | None]] = {}

    def part(a: Point, b: Point) -> tuple[float, float | None]:
        got = legs.get((a, b))
        if got is None:
            got = legs[(a, b)] = (norms.km(a, b), roads.climb(a, b) if climbs else None)
        return got

    def leg(code: str, pts: Sequence[Point], kgs: Sequence[float]) -> cts.Leg | None:
        truck = ctx.trucks.get(code)
        if truck is None or not truck.capacity_kg > 0:   # без норм — дизеля и износа нет (unpriced)
            return None
        nodes = [ctx.depot, *pts, ctx.depot]
        parts = [part(a, b) for a, b in zip(nodes, nodes[1:])]
        cost = route_cost([d for d, _ in parts], kgs, truck, [u for _, u in parts] if climbs else None)
        return cts.Leg(cost.total_amd(ctx.tn.fuel_price), math.fsum(d for d, _ in parts))
    return leg, terrain, terrain and not climbs


def _cost_crew_km(state: RoutesState, snap: Snapshot, bundle: Bundle, data: cts.SalesData, params: cp.Params,
                  days: Collection[date]) -> dict[tuple[date, int], int]:
    """Доли платы экипажа за км (cts.crew_km) — тем же расчётом км, что «Աշխատավարձ» (_pay_tours): склад настроек,
    координаты магазинов (evaluate.visit_coord: ручная → водителей → ERP → GPS), общие дороги раздела state.roads без
    объезда центра и профиля грузовика; карты нет или она сломана — по прямой × 1,3. rate_km = 0 — дороги не трогаются.
    Туры — только дней days (с планом): дни без плана отчёт не показывает."""
    if params.rate_km <= 0 or bundle.depot is None:
        return {}
    customers = sorted({s.customer_id for s in data.sales if s.crew and s.day in days})
    coords = {c: evaluate.visit_coord(snap, c, 0, bundle.geo_overrides, bundle.driver_points).point for c in customers}
    roads = state.roads.get() if state.roads is not None else None
    if roads is not None:
        roads.ensure([bundle.depot, *(p for p in coords.values() if p is not None)])
    ok = roads is not None and not roads.failed
    road_km: Callable[[Point, Point], float | None] = roads.km if roads is not None and ok else (lambda a, b: None)
    key = (bundle.depot, roads_version(roads) if ok else 'off')
    with state.cost_lock:
        memo = state.cost_tours.get(key)
        if memo is None:
            state.cost_tours.clear()   # другой склад или карта — прежние туры не нужны
            memo = state.cost_tours[key] = {}
    return cts.crew_km(data, params.excluded_lines, params.excluded_people, params.rate_km, bundle.depot, coords, road_km,
                       memo, days)


def _cost_compute(state: RoutesState, since: date, until: date) -> _CostResult:
    """Отчёт за [since, until]: рейсы планов, накладные ERP (одним запросом), модель расхода «Развоза» (_dispatch_ctx: км
    дорог, рельеф, нормы машин, выученный расход) с ценами сегодня — дизель настроек, износ гаража на сегодня, ставки
    «Աշխատավարձ». Яндекс-пробки не спрашиваются: они меняют минуты, а не км (и для прошлых дат их нет)."""
    params, _, _ = state.store.crew_pay_params()
    margin, at, by, broken = _cost_margin(state)
    days, sources = _cost_plans(state, since, until)
    snap, _ = state.snapshots.cached()
    bundle = _bundle(state)
    if bundle.settings.get('traffic_mode') == 'yandex':
        bundle = replace(bundle, settings={**bundle.settings, 'traffic_mode': 'gps'})
    cids = sorted({c for _, _, trips in days.values() for t in trips for c in t.stops})
    coords = {c: evaluate.visit_coord(snap, c, 0, bundle.geo_overrides, bundle.driver_points).point for c in cids}
    trucks = _ready_trucks(snap, bundle, active_only=False)
    ctx = _dispatch_ctx(state, snap, bundle, _yerevan_now().date(), trucks, [p for p in coords.values() if p is not None])
    if ctx is None:
        raise dp.DispatchError(COST_NO_CTX)
    data = _cost_sales(state, since, until + timedelta(days=1))   # ERP — только когда считать есть чем
    delivered = cts.deliveries(data, params.excluded_lines, params.excluded_people,
                               _cost_crew_km(state, snap, bundle, data, params, set(days)))
    leg, terrain, pending = _cost_leg(ctx, [p for p in coords.values() if p is not None])
    # общее для всех дней: машины с нормами, цена дизеля, ставки экипажа, дороги (версия карты, объезд центра и его
    # граница, извилистость, откуда км), рельеф, склад
    fixed = (sorted(ctx.trucks.items()), ctx.tn.fuel_price, params.rate_point, params.rate_tonne, params.rate_km,
             roads_version(ctx.norms.roads), ctx.center_zone, ctx.norms.detour, ctx.model.get('bypass'), ctx.model.get('km'),
             terrain, pending, ctx.depot)
    out: list[cts.TripCost] = []
    for day in sorted(days):
        source, rev, trips = days[day]
        day_cids = sorted({c for t in trips for c in t.stops})
        key = (source, rev, trips, [(c, delivered.get((day, c)), coords.get(c)) for c in day_cids], fixed)
        with state.cost_lock:
            hit = state.cost_days.get(day)
        age = time.monotonic() - hit[2] if hit is not None else math.inf
        if hit is not None and hit[0] == key and age < (COST_RETRY_SECONDS if hit[3] else COST_DAY_TTL_SECONDS):
            out += hit[1]
            continue
        costs = cts.trip_costs(trips, delivered, coords, leg, params)
        out += costs
        with state.cost_lock:
            state.cost_days.pop(day, None)
            state.cost_days[day] = (key, costs, time.monotonic(), pending)
            while len(state.cost_days) > COST_DAYS_KEPT:
                state.cost_days.pop(next(iter(state.cost_days)))
    names = {c: data.customers.get(c) or ((snap.customers[c].code, snap.customers[c].name) if c in snap.customers
                                          else (str(c), '')) for c in cids}
    return _CostResult(since, until, cts.report(out, cts.sales_by_customer(data, set(days)), margin), names, params, margin, at, by,
                       broken, sources, ctx.tn.fuel_price, ctx.tn.fuel_price_estimated)


def _cost_warm(state: RoutesState, since: date, until: date) -> None:
    """Фоновый расчёт периода: итог или исключение (ERP, нет склада, база) — в cost_results; запрос, дождавшийся потока,
    отдаёт итог или поднимает то же исключение (_api: 503 / 400 / 500), как при расчёте в самом запросе."""
    started = time.monotonic()
    try:
        got: Any = _cost_compute(state, since, until)
    except Exception as e:   # noqa: BLE001 — передаём запросу как есть; без трассировки: кадры потока не держим в памяти
        got = e.with_traceback(None)
    with state.cost_lock:
        state.cost_results.pop((since, until), None)
        state.cost_results[(since, until)] = (started, time.monotonic(), got)
        while len(state.cost_results) > COST_SALES_KEPT:
            state.cost_results.pop(next(iter(state.cost_results)))
        state.cost_warm.pop((since, until), None)


def _cost_result(state: RoutesState, since: date, until: date) -> _CostResult | None:
    """Отчёт периода — расчётом в фоне (как «Նորմ և փաստ», _month_ready): готовый итог моложе COST_FRESH_S, начатый после
    последнего сохранения настроек (cost_changed), отдаётся сразу; иначе запрос запускает свежий расчёт (кэш дней делает
    его быстрым) и ждёт его не дольше COST_WAIT_S; не дождался — None (pending). Поток один на сервер: идёт расчёт другого
    периода — этот ждёт очереди (None). Итог расчёта, начатого до сохранения настроек, — тоже None: следующий опрос
    запустит новый. Холодный первый расчёт после перезапуска или смены карты (расстояния точек периода — с нуля) не держит
    запрос и воркер минутами."""
    key = (since, until)
    with state.cost_lock:
        job = state.cost_warm.get(key)
        if job is None:
            hit = state.cost_results.get(key)
            if hit is not None and hit[0] >= state.cost_changed and time.monotonic() - hit[1] < COST_FRESH_S \
                    and not isinstance(hit[2], BaseException):
                return hit[2]
            if state.cost_warm:
                return None
            job = threading.Thread(target=_cost_warm, args=(state, since, until), name='routes-cost-to-serve',
                                   daemon=True)
            state.cost_warm[key] = job
            job.start()
    job.join(COST_WAIT_S)
    if job.is_alive():
        return None
    with state.cost_lock:
        hit = state.cost_results.get(key)
        changed = state.cost_changed
    if hit is None or hit[0] < changed:   # вытеснен другими периодами или начат до сохранения настроек — спросить снова
        return None
    if isinstance(hit[2], BaseException):   # копия: трассировка запроса не прилипает к хранимому исключению
        raise copy.copy(hit[2]) from None
    return hit[2]


def _cost_changed(state: RoutesState) -> None:
    """Сохранены настройки, наценка или ставки: готовые итоги периодов устарели, идущий расчёт — тоже (_cost_result)."""
    with state.cost_lock:
        state.cost_changed = time.monotonic()
        state.cost_results.clear()


def _cost_request() -> tuple[_CostResult | None, Any]:
    """(отчёт за период из ?days= или ?from=&to=, None), (None, ответ 400) или (None, ответ 503 {pending: true})."""
    a = request.args
    span, error = cts.period(a.get('days'), a.get('from'), a.get('to'), _yerevan_now().date())
    if span is None:
        return None, _bad_request({'period': error or ''})
    res = _cost_result(_state(), *span)
    if res is None:
        return None, (jsonify({'success': False, 'pending': True, 'error': COST_PENDING}), 503)
    return res, None


def _cost_pct(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


@bp.get('/api/routes/cost')
@_admin_only
@_api
def api_cost() -> Any:
    """Период ?days=30|90 (полные дни по вчера) или ?from=&to= (до MAX_DAYS дней): магазины с ֏ доставки, продажами, % и
    красным; рейсы магазина; итог; наценка, ставки и откуда рейсы."""
    res, error = _cost_request()
    if res is None:
        return error
    rep = res.report
    rows = []
    for r in rep.rows:
        code, name = res.names.get(r.customer_id, (str(r.customer_id), ''))
        rows.append({'customer_id': r.customer_id, 'code': code, 'name': name, 'visits': r.visits, 'fuel': r.fuel,
                     'crew': r.crew, 'cost': r.cost, 'per_visit': round(r.per_visit), 'sales': round(r.sales),
                     'pct': _cost_pct(r.pct), 'red': r.red, 'unrouted': r.unrouted,
                     'trips': [{'date': v.day.isoformat(), 'truck': v.truck, 'trip': v.trip_id, 'fuel': v.fuel,
                                'crew': v.crew, 'detour_km': round(v.detour_km, 1), 'trip_fuel': v.trip_fuel,
                                'trip_stops': v.trip_stops} for v in r.trips]})
    return jsonify({'success': True, 'from': res.since.isoformat(), 'to': res.until.isoformat(),
                    'days': (res.until - res.since).days + 1, 'presets': list(cts.PRESET_DAYS), 'max_days': cts.MAX_DAYS,
                    'margin': res.margin, 'margin_updated_at': res.margin_at, 'margin_updated_by': res.margin_by,
                    'margin_store_error': res.margin_broken,
                    'rates': {'rate_point': res.params.rate_point, 'rate_tonne': res.params.rate_tonne,
                              'rate_km': res.params.rate_km},
                    'fuel_price': res.fuel_price, 'fuel_price_estimated': res.fuel_price_estimated,
                    'sources': dict(res.sources),
                    'totals': {'cost': rep.fuel + rep.crew, 'fuel': rep.fuel, 'crew': rep.crew, 'sales': round(rep.sales),
                               'pct': _cost_pct(rep.pct), 'red': rep.red, 'stores': len(rep.rows), 'trips': rep.trips,
                               'unpriced_trips': rep.unpriced_trips, 'visits': sum(r.visits for r in rep.rows)},
                    'rows': rows})


@bp.get('/api/routes/cost.csv')
@_admin_only
@_api
def api_cost_csv() -> Any:
    """Таблица магазинов для Excel: «;», десятичная запятая, UTF-8 с BOM (как pay.csv); в начале — период, наценка, ставки."""
    import csv
    import io
    res, error = _cost_request()
    if res is None:
        return error
    rep = res.report

    def n(x: float | None, d: int = 2) -> str:
        return '' if x is None else f'{x:.{d}f}'.replace('.', ',')

    out = io.StringIO()
    w = csv.writer(out, delimiter=';', lineterminator='\r\n')
    w.writerow(['Առաքման արժեք', res.since.isoformat(), res.until.isoformat()])
    w.writerow(['Հաշվված է', _yerevan_now().strftime('%Y-%m-%d %H:%M')])
    w.writerow(['Պլանով օրեր', res.sources['sent'] + res.sources['draft']])   # продажи — только за них
    w.writerow(['Միջին հավելագին, %', n(res.margin)])
    w.writerow(['Դիզել, ֏/լ', n(res.fuel_price, 0)])
    w.writerow(['Մեկ կետի համար, ֏', n(res.params.rate_point, 0)])
    w.writerow(['Մեկ տոննայի համար, ֏', n(res.params.rate_tonne, 0)])
    w.writerow(['Մեկ կմ-ի համար, ֏', n(res.params.rate_km, 0)])
    w.writerow(['Ընդամենը առաքում, ֏', rep.fuel + rep.crew])
    w.writerow(['Վաճառքից, %', n(rep.pct)])
    w.writerow([])
    w.writerow(['Կոդ', 'Խանութ', 'Այցեր', 'Դիզել և մաշվածք, ֏', 'Անձնակազմ, ֏', 'Առաքում, ֏', 'Մեկ այցը, ֏', 'Վաճառք, ֏',
                'Վաճառքից, %', 'Կարմիր'])
    for r in rep.rows:
        code, name = res.names.get(r.customer_id, (str(r.customer_id), ''))
        w.writerow([_csv_cell(code), _csv_cell(name), r.visits, r.fuel, r.crew, r.cost, round(r.per_visit), round(r.sales),
                    n(r.pct), 'այո' if r.red else ''])
    name = f'cost-to-serve-{res.since:%Y%m%d}-{res.until:%Y%m%d}.csv'
    return Response('﻿' + out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


def _cost_margin_body(state: RoutesState) -> dict[str, Any]:
    value, at, by, broken = _cost_margin(state)
    return {'success': True, 'value': value, 'updated_at': at, 'updated_by': by, 'store_error': broken}


@bp.get('/api/routes/cost/margin')
@_admin_only
@_api
def api_cost_margin_get() -> Any:
    return jsonify(_cost_margin_body(_state()))


@bp.post('/api/routes/cost/margin')
@_admin_only
@_api
def api_cost_margin() -> Any:
    """Средняя наценка владельца, %: {"value": 0–100 | null} (null или пусто — без красного). Только route_optimizer.db."""
    payload, error = _json_body()
    if error is not None:
        return error
    if not isinstance(payload, dict):
        return _bad_request({'value': 'Լրացրեք թիվը'})
    value, err = cts.check_margin(payload.get('value'))
    if err is not None:
        return _bad_request({'value': err})
    state = _state()
    state.store.save_cost_margin(value, session.get('username'))
    _cost_changed(state)
    logger.info('[Routes] Առաքման արժեք: наценка %s сохранена пользователем %s', value, session.get('username'))
    return jsonify(_cost_margin_body(state))

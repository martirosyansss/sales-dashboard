# -*- coding: utf-8 -*-
"""Страницы и API раздела «Маршруты» (§10).

Доступ обеспечивает глобальный before_request дашборда: аноним — 401/редирект на вход,
роль user — 403 (раздела нет в allowlist), garage — только журнал гаража (/routes/garage и его API), admin — полный доступ.
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
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any, Callable, Collection, Mapping, Protocol, Sequence

from flask import (Blueprint, Response, current_app, g, has_app_context, has_request_context, jsonify,
                   render_template, request, session)

from . import actuals as ac
from . import ai_chat
from . import dispatch as dp
from . import evaluate, garage, learning, optimize
from . import fleet as fl
from . import waybill as wb
from .running_costs import profile_fields
from .erp import CUSTOMER_FIND_MAX_LEN, CustomerHint, CustomerRef, ErpError
from .geo import Point, haversine_km, is_valid_point
from .roads import SNAP_MAX_KM, CenterBypassRoads, RoadDistances, RoadProvider, roads_version
from .snapshot import CAR_IDLE_DAYS, MIN_REFRESH_SECONDS, ResultCache, Snapshot, SnapshotCache
from .store import (CREW_TABLES, DEFAULT_MANAGER_FUEL, DEFAULT_SETTINGS, KEEP, Bundle, Decision, GarageError, Store, StoreError,
                    big_auto, center_auto, check_driver_name, check_garage_entry, check_unload_min, check_window,
                    validate_payload)
from .valhalla_engine import TRUCK_TIME_MODEL, TRUCK_TIME_VALHALLA, ValhallaProvider, ValhallaRoads, truck_time_source
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
    driver_list_cache: tuple[float, list[str]] | None = None
    driver_list_lock: threading.Lock = field(default_factory=threading.Lock)   # перечитывает один запрос
    dispatch_cache: dict[tuple[date, date, date], tuple[float, dp.DispatchData]] = field(default_factory=dict)
    dispatch_lock: threading.Lock = field(default_factory=threading.Lock)
    same_day_cache: dict[date, tuple[float, dp.SameDayData]] = field(default_factory=dict)   # под dispatch_lock
    driver_geo: DriverGeo | None = None   # None — раздела «Առաքիչ» нет: точек водителей нет, всё как раньше
    driver_cache: tuple[float, dict[int, Point]] | None = None   # (time.monotonic(), точки) — DRIVER_TTL_SECONDS
    # факт машин (трек, точки дня, заправки) из «Առաքիչ» — обучение «Развоза»; None — без обучения, всё как раньше
    fleet_facts: learning.FleetFacts | None = None
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


@bp.after_request
def _no_store(resp: Response) -> Response:
    resp.headers.update(NO_CACHE_HEADERS)
    return resp


# --- Страницы ---

_YANDEX_KEY_RE = re.compile(r'[0-9A-Za-z-]{16,64}')
_yandex_key_warned: set[str] = set()


def _yandex_tiles_key() -> str:
    """Ключ Yandex Tiles API для подложки карт (№47) или '' — тогда карты на OpenStreetMap."""
    import os
    key = os.environ.get('ROUTES_YANDEX_TILES_KEY', '').strip()
    if key and not _YANDEX_KEY_RE.fullmatch(key):
        if key not in _yandex_key_warned:       # один раз на значение, а не на каждый показ страницы
            _yandex_key_warned.add(key)
            logger.warning('ROUTES_YANDEX_TILES_KEY не похож на ключ Яндекса — карты на OpenStreetMap')
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
    changes, errors = validate_payload(payload, bundle, snap.ref_data())
    if errors:
        return jsonify({'success': False, 'errors': errors}), 400
    user = session.get('username')
    state.store.save(changes, user)
    logger.info('[Routes] Настройки сохранены пользователем %s', user)
    return jsonify({'success': True})


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
    Износ: wear_amd_per_km — ручное значение (его и сохраняет страница); garage — ремонт ֏/км журнала гаража на
    сегодня (только чтение), garage_prior — средняя модели или парка машине без своей цены журнала (garage.Prior),
    wear_source — какое значение в расчёте (garage | manual | garage_avg | None)."""
    prices, priors = prices or {}, priors or {}
    used = _garage_bundle(bundle, prices, priors)
    out = []
    active_cars = snap.active_cars
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
            'fuel_l_per_100km': t.fuel_l_per_100km if t else None,
            **profile_fields(t),
            'active': bundle.truck_active(code, active_cars),
            'active_source': 'auto' if t is None or t.active is None else 'manual',
            'auto_active': code in active_cars,
            'center_ok': bundle.truck_center_ok(code, car.name),
            'center_ok_source': 'auto' if t is None or t.center_ok is None else 'manual',
            'auto_center_ok': center_auto(car.name),
            'big': bundle.truck_big(code), 'big_source': 'auto' if t is None or t.big is None else 'manual',
            'auto_big': big_auto(t.capacity_kg if t else None),
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


def _ready_trucks(snap: Snapshot, bundle: Bundle, active_only: bool = True) -> dict[str, fl.FleetTruck]:
    """Машины, готовые к расчёту: тоннаж и расход заданы (и активны — для плана); с правом въезда в центр и признаком
    «большая машина» (№68); износ — как в расчёте (Bundle.resolved_trucks: журнал гаража или ручной)."""
    names = {code: car.name for code, car in snap.cars.items()}
    resolved = bundle.resolved_trucks(snap.active_cars)
    if active_only:
        ready, _ = fl.fleet_trucks(resolved, names)
        return {t.car_code: replace(t, center_ok=bundle.truck_center_ok(t.car_code, names.get(t.car_code)),
                                    big=bundle.truck_big(t.car_code))
                for t in ready}
    return {code: fl.FleetTruck(code, names.get(code) or t.name, float(t.capacity_kg), float(t.fuel_l_per_100km),
                                bundle.truck_center_ok(code, names.get(code)), **profile_fields(t),
                                big=bundle.truck_big(code))
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
    return dp.DayContext(day, bundle.depot, trucks, norms, tn, h * 60 + m,
                         float(h2 * 60 + m2 - (h * 60 + m)), float(s['min_trip_revenue']),
                         {cid: w.span() for cid, w in bundle.windows.items()}, zone,
                         vehicle_access=bundle.vehicle_access,
                         model=_model_note(s, calib, norms, eff, [p for p in points if p is not None], trucks))


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


def _day_orders(state: RoutesState, bundle: Bundle, day: date, refresh: bool,
                rule: dp.FleetRule) -> tuple[date, date, dp.DispatchData, dp.Selection]:
    """Окно заказов дня, заказы ERP (кэш _dispatch_data) и отбор к доставке по правилу дня «чьи заказы везут машины»
    (№74, dp.fleet_rule_of) — без заказов, взятых в развоз дня их приёма (№72, _same_day_taken)."""
    workdays, off = bundle.settings['workdays'], dp.holidays_of(bundle.settings)
    since, until = dp.order_window(day, workdays, off)
    data = _dispatch_data(state, dp.backlog_since(since, workdays, holidays=off), until, day, refresh)
    sel = dp.to_deliver(data.orders, day, since, rule, dp.place_of(data.customers, data.addresses))
    taken, unread = _same_day_taken(state, day, workdays, off)
    if unread:
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
    (dp.agents_off_of: без черновика — правило настроек, №69), — ни те, ни другие."""
    excluded = draft.excluded if draft is not None else set()
    off = dp.agents_off_of(draft, settings)
    inside = _backlog_in(draft, carried)
    same = draft.same_day if draft is not None else set()
    return [o for o in deliver if o.isn not in excluded and o.agent_id not in off] \
        + [o for o in backlog if o.isn in inside and o.agent_id not in off] \
        + [o for o in today if o.isn in same and o.agent_id not in off]


def _carried(state: RoutesState, day: date, workdays: Sequence[int], backlog: list[dp.DispatchOrder],
             holidays: Collection[date] = ()) -> set[str]:
    """Заказы, которые логист перенёс на этот день («Везти завтра», №25) в планах с прошлого рабочего дня
    по вчера (и из нерабочего дня между ними) и которые ещё не отгружены. Битый черновик — без переносов."""
    out: set[str] = set()
    d = dp.previous_workday(day, workdays, holidays)
    while d < day:
        try:
            stored = state.store.load_dispatch(d.isoformat())
        except StoreError:
            stored = None
        if stored is not None:
            out |= dp.Draft.from_json(stored[0]).deferred
        d += timedelta(days=1)
    return out & {o.isn for o in backlog}


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
            stored = state.store.load_dispatch(d.isoformat())
        except StoreError:
            logger.warning('[Routes] План развоза на %s не прочитан — взятые в его развоз заказы дня не исключены из %s',
                           d, day, exc_info=True)
            stored, unread = None, True
        if stored is not None:
            out |= dp.Draft.from_json(stored[0]).same_day
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
               carried: Collection[str] = ()) -> dict[str, Any]:
    """«Заказы ещё поступают» и что изменилось с последней сборки — для подсказки вверху страницы.
    Перенесённые сюда из прошлого дня — как заказы дня: появился перенос — «новые», сняли — «убраны»."""
    s = bundle.settings
    changes = None
    if draft is not None:
        inside = _backlog_in(draft, carried)
        moved_in = [o for o in backlog if o.isn in set(carried) - draft.dropped]
        # заказы менеджеров, снятых фильтром, — не «новые»: их сегодня не везём
        changes = dp.since_build(draft.built_orders, [o for o in (*deliver, *moved_in) if o.agent_id not in draft.agents_off],
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
    since, until, data, sel = _day_orders(state, bundle, day, refresh, rule)
    deliver, backlog = sel.main, sel.backlog
    carried = _carried(state, day, bundle.settings['workdays'], backlog, dp.holidays_of(bundle.settings))
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
                        sel.same_day_unread, other_vehicle=sel.other_vehicle, customers_off=sel.customers_off)


def _day_stops(dd: _DispatchDay, draft: dp.Draft) -> list[dp.Stop]:
    """Точки развоза дня для черновика draft (его «не везём сегодня» и добавленные заказы)."""
    return dp.build_stops(_active_orders(dd.deliver, dd.backlog, draft, dd.carried, dd.bundle.settings, dd.same_day),
                          lambda cid: evaluate.visit_coord(dd.snap, cid, 0, dd.bundle.geo_overrides,
                                                           dd.bundle.driver_points))


def _stop_info(dd: _DispatchDay) -> Callable[[dp.Stop], dict[str, Any]]:
    agents = dd.snap.agents
    windows = dd.bundle.windows

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
        }
    return info


def _dispatch_body(dd: _DispatchDay) -> dict[str, Any]:
    s = dd.bundle.settings
    holidays = dp.holidays_of(s)
    today = _clock().date()
    info = _stop_info(dd)
    draft = dd.draft
    excluded = draft.excluded if draft is not None else set()
    selected = set(draft.trucks) if draft is not None else set(dd.ready)
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
    by_agent: dict[int, list[dp.DispatchOrder]] = {}
    for o in [*(o for o in dd.deliver if o.isn not in excluded), *(o for o in dd.backlog if o.isn in added),
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
        return {'isn': o.isn, 'doc_num': o.doc_num, 'customer_id': o.customer_id, 'code': code, 'name': name,
                'order_date': o.order_date.isoformat(), 'kg': round(o.kg), 'revenue': round(o.revenue),
                'added': o.isn in added, 'deferred': draft is not None and o.isn in draft.deferred,
                'carried': o.isn in dd.carried, 'agent_off': o.agent_id in off}

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
        'backlog': [order_json(o) for o in dd.backlog],
        'backlog_since': dp.backlog_since(dd.since, s['workdays'], holidays=holidays).isoformat(),
        'plan': None,
        'overtime': draft.overtime if draft is not None else False,
        'overtime_ok': draft.overtime_ok if draft is not None else False,
        'min_trip_revenue': s['min_trip_revenue'],
        'defer_to': _defer_target(dd.day, s['workdays'], holidays)[0].isoformat(),
        'overtime_days_month': _overtime_days(_state().store, dd.day),
        'geo_suggestions': _geo_suggestions(_state(), dd),
        # №73: план дня утверждён — когда (кем — только странице: _dispatch_page_body)
        **({'approved': {'at': draft.approved['at']}} if draft is not None and draft.approved is not None else {}),
        # №72: план прошлого дня не прочитан — заказы, взятые в его развоз, могли попасть и сюда
        **({'same_day_unread': True} if dd.same_day_unread else {}),
        **_freshness(dd.day, dd.bundle, dd.data, dd.deliver, dd.backlog, draft, dd.carried),
    }
    if draft is not None and dd.ctx is not None:
        plan = dp.plan_view(dd.ctx, dd.stops, draft, info)
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
    модели ничего бы не сказали. Водители (№62, _drivers_json) — тоже только странице: имена людей модели не отправляются."""
    return {**_dispatch_body(dd), 'store_unload': {st.customer_id: dd.bundle.unload_min[st.customer_id]
                                                   for st in dd.stops if st.customer_id in dd.bundle.unload_min},
            **_drivers_json(_state(), dd.day), **_same_day_json(dd),
            # №73: кто утвердил план — имя человека, только странице
            **({'approved': {'at': dd.draft.approved['at'], 'by': dd.draft.approved['by']}}
               if dd.draft is not None and dd.draft.approved is not None else {})}


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
        started = dp.started_trips(dd.ctx, dd.stops, draft, _now_min(dd.ctx, now))
        moving = {c for t in draft.trips if t.id in started for c in t.stops}

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
                                now_min)
    now = _same_day_now()
    if dd.day != now.date():
        raise dp.DispatchError('Նոր պատվերները կարելի է փոխել միայն այսօրվա առաքման մեջ')
    raw = [payload.get('order')] if action == 'exclude' else payload.get('orders')
    if not isinstance(raw, list) or len(raw) > SAME_DAY_MAX_ORDERS or not all(isinstance(x, str) for x in raw):
        raise dp.DispatchError('Պատվերների ցուցակը չընդունվեց — թարմացրեք էջը')
    isns = {x.upper() for x in raw}
    # рейс, чья загрузка началась, уже везёт заказ: вернуть его на завтра — значит отвезти дважды
    cids = {o.customer_id for o in dd.same_day if o.isn in isns}
    started = dp.started_trips(dd.ctx, dd.stops, dd.draft, _now_min(dd.ctx, now))
    if any(t.id in started and cids & set(t.stops) for t in dd.draft.trips):
        raise dp.DispatchError('Մեքենան արդեն բեռնվում է կամ ճանապարհին է՝ այս պատվերով — այն այսօր է գնում, '
                               'վաղվան թողնել չի կարելի')
    return dp.drop_same_day(dd.draft, isns)


PLAN_APPROVED = 'Պլանը հաստատված է։ Ամբողջական վերակազմման համար նախ չեղարկեք հաստատումը։'   # №73: пересборка утверждённого плана
PAST_DAY_APPROVE = 'Անցած օրվա պլանը չի հաստատվում և չի չեղարկվում'


def _approve_edit(dd: _DispatchDay, payload: Mapping[str, Any]) -> dp.Draft:
    """«Հաստատել օրվա պլանը» / «Չեղարկել հաստատումը» (№73): прошедший день — DispatchError."""
    if dd.day < _same_day_now().date():
        raise dp.DispatchError(PAST_DAY_APPROVE)
    if payload.get('action') == 'approve':
        # время утверждения — по Еревану, как «сегодня» и «сейчас» новых заказов дня
        return dp.approve(dd.draft, _same_day_now().isoformat(timespec='seconds'), session.get('username'))
    return dp.unapprove(dd.draft)


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
        started = dp.started_trips(dd.ctx, dd.stops, dd.draft, _now_min(dd.ctx, now))
        moving = {c for t in dd.draft.trips if t.id in started for c in t.stops}
        before = {o.isn for st in dd.stops if st.customer_id in moving for o in st.orders}
        if before - {o.isn for st in after.stops for o in st.orders}:
            raise dp.DispatchError(SETTINGS_ON_STARTED)
    if new.built_orders is not None:
        gone = {o.isn for o in dd.deliver} - {o.isn for o in after.deliver}
        new.built_orders = {k: v for k, v in new.built_orders.items() if k not in gone}
    return new


def _check_defer_same_day(dd: _DispatchDay, trip_id: Any) -> None:
    """«Везти завтра» рейса, который уже грузится или в пути, с взятыми сегодня заказами дня (№72) — нельзя: они снова
    стали бы заказами завтра и их отвезли бы дважды (DispatchError)."""
    now = _same_day_now()
    if dd.draft is None or dd.ctx is None or not dd.draft.same_day or dd.day != now.date():
        return
    trip = next((t for t in dd.draft.trips if t.id == trip_id), None)
    taken = {o.customer_id for o in dd.same_day if o.isn in dd.draft.same_day}
    if trip is not None and taken & set(trip.stops) \
            and trip.id in dp.started_trips(dd.ctx, dd.stops, dd.draft, _now_min(dd.ctx, now)):
        raise dp.DispatchError('Մեքենան արդեն բեռնվում է կամ ճանապարհին է՝ այսօրվա նոր պատվերներով — երթը վաղվան '
                               'տեղափոխել չի կարելի')


def _is_same_day_edit(dd: _DispatchDay, payload: Mapping[str, Any]) -> bool:
    """Правка новых заказов дня: свои действия или «не везём сегодня» для взятого заказа дня (он не в заказах окна дня)."""
    action = payload.get('action')
    order = payload.get('order')
    return action in ('same_day', 'same_day_drop') or (
        action == 'exclude' and isinstance(order, str) and dd.draft is not None and order.upper() in dd.draft.same_day)


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
    _, _, data, sel = _day_orders(state, bundle, day, False, rule)
    carried = _carried(state, day, bundle.settings['workdays'], sel.backlog, dp.holidays_of(bundle.settings))
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
                    **_freshness(day, bundle, data, sel.main, sel.backlog, draft, carried), **same})


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
                                              **({'buffer': tr['buffer']['minutes']} if tr.get('buffer') else {})}
                                             for tr in t['trips']]}
                   for t in view['trucks']}}


def _conflict(text: str) -> Any:
    return jsonify({'success': False, 'error': text, 'conflict': True}), 409


@bp.post('/api/routes/dispatch/build')
@_api
def api_dispatch_build() -> Any:
    """«Собрать рейсы»: {"date", "trucks": [коды машин дня], "agents_off"?: [agent_id]}. Закреплённые рейсы и
    исключённые заказы прежнего черновика сохраняются, остальное раскладывается заново; agents_off — фильтр
    «Մենեջերներ» (чьи заказы не везём), без него — фильтр прежнего черновика."""
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
    # первая сборка дня: черновик начинается с правила менеджеров из настроек (№69) — по нему и точки дня без черновика
    base = dd.draft if dd.draft is not None else dp.Draft(agents_off=dp.agents_off_of(None, bundle.settings),
                                                          fleet=dp.FleetRule.from_settings(bundle.settings).to_json())
    if 'agents_off' in payload:
        # фильтр «Մենեջերներ» до первой сборки живёт на странице — приходит со сборкой; точки дня — по нему
        off = dp.parse_agents(payload['agents_off'])
        if off is None:
            return _bad_request({'agents_off': 'ожидался список менеджеров'})
        if off != base.agents_off:
            base.agents_off = off
            dd = _load_day(state, bundle, day, draft=base, rev=dd.rev)
    started = time.perf_counter()
    # первая сборка дня: перенесённые сюда заказы прошлого дня — сразу в развозе
    draft = dp.build(dd.ctx, dd.stops, base, codes, _now())
    # отметка сборки: все заказы дня (и исключённые — они не «новые») + добавленные заказы прошлых дней
    inside = _backlog_in(draft, dd.carried)
    draft.built_orders = dp.order_marks([*dd.deliver, *(o for o in dd.backlog if o.isn in inside)])
    draft.overtime = dp.runs_late(dd.ctx, dd.stops, draft)
    _capture_prediction(dd, draft)
    rev = state.store.save_dispatch(day.isoformat(), draft.to_json(), session.get('username'))
    logger.info('[Routes] Развоз на %s собран (%s) за %.1f с: точек %d, рейсов %d, машин %d', day,
                session.get('username'), time.perf_counter() - started, len(dd.stops), len(draft.trips), len(codes))
    dd.draft, dd.rev = draft, rev or 0
    return jsonify({'success': True, **_dispatch_page_body(dd)})


@bp.post('/api/routes/dispatch/edit')
@_api
def api_dispatch_edit() -> Any:
    """Правка логиста: {"date", "rev", "action": move | pin | unpin | exclude | include | agents | defer_trip | resize | undo, …}
    (dispatch.apply_edit). rev — номер черновика, от которого правка: план изменён в другой вкладке — 409.
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
    deferred_before = set(dd.draft.deferred)
    trips_before = {t.id for t in dd.draft.trips}   # №73: рейсы, появившиеся при правке утверждённого плана, — закрепить
    try:
        if payload.get('action') == 'defer_trip':
            _check_defer_same_day(dd, payload.get('trip'))
        if payload.get('action') in ('approve', 'unapprove'):   # утверждение плана дня (№73)
            draft = _approve_edit(dd, payload)
        elif payload.get('action') == 'apply_settings':   # день — по нынешним правилам настроек (№69, №74)
            draft = _apply_settings_edit(state, bundle, dd)
        elif _is_same_day_edit(dd, payload):    # новые заказы дня (№72)
            draft = _same_day_edit(state, bundle, dd, payload)
        else:
            draft = dp.apply_edit(dd.ctx, dd.stops, dd.draft, payload, {o.isn for o in dd.deliver},
                                  {o.isn for o in dd.backlog},
                                  defer_since=_defer_target(day, workdays, dp.holidays_of(bundle.settings))[1],
                                  carried=dd.carried, now_min=_today_min(dd))
    except dp.DispatchError as e:
        return _bad_request({'_': str(e)})
    if day < _clock().date() and draft.deferred != deferred_before:
        # перенос с прошедшего дня меняет развоз уже другого дня — задним числом нельзя
        return _bad_request({'_': 'Прошедший день — перенос на другой день не меняется'})
    # точки дня после правки («не везём сегодня», фильтр «Մենեջերներ», вернуть меняют точки и вес): по ним — рейсы
    # черновика (магазин без заказов уходит и из сохранённого плана: его читают обучение и приложение водителя),
    # отметка дня и прогноз
    dd = _load_day(state, bundle, day, draft=draft, rev=dd.rev)
    dp.prune(draft, dd.stops)
    dp.release_same_day_trucks(draft)   # №72: машина, отмеченная взятием заказа дня, без рейсов — снова не отмечена
    dp.keep_approved(draft, trips_before)   # №73: пока план утверждён, новые рейсы тоже закреплены
    draft.overtime = dp.runs_late(dd.ctx, dd.stops, draft)
    _capture_prediction(dd, draft)
    rev = state.store.save_dispatch(day.isoformat(), draft.to_json(), session.get('username'), expected_rev=dd.rev)
    if rev is None:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    dd.rev = rev
    body = _dispatch_page_body(dd)
    body['delta_km'] = round(body['plan']['summary']['km'] - km_before, 1) if body['plan'] else None
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
    options = dp.same_day_options(with_new.ctx, dd.stops, with_new.stops, dd.draft, cids, now_min)['options'] \
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
    """«Начать заново»: черновик на дату удаляется (исключения и закрепления — тоже)."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    state = _state()
    draft, _ = _stored_draft(state, day)
    if draft is not None and draft.approved is not None:   # №73: утверждённый план не стирается — сначала снять
        return _bad_request({'_': PLAN_APPROVED})
    state.store.delete_dispatch(day.isoformat())
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
    (состав рейсов страница сверяет сама по basis). Ничего не сохраняет."""
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
    if state.waybill_loader is None:
        raise ErpError('Загрузчик строк заказов не подключён')
    lines = state.waybill_loader([o['isn'] for tr in truck['trips'] for s in tr['stops'] for o in s['orders']])
    logger.info('[Routes] Բեռնագիր %s на %s (%s)', car, day, session.get('username'))
    return jsonify({'success': True, 'day': day.isoformat(), 'rev': dd.rev, **wb.truck_waybill(plan, car, lines),
                    'driver': state.store.truck_drivers(day.isoformat())[0].get(car),
                    'helper': state.store.truck_drivers(day.isoformat(), 'helper')[0].get(car)})


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
    only_day — изменён только этот день. План не меняется."""
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
                    'only_day': {role: one_day for role, (_, one_day) in people.items()}, **_drivers_json(state, day)})


@bp.get('/api/routes/measurements')
@_api
def api_measurements_get() -> Any:
    from .measurements import summary
    return jsonify({'success': True, **summary(_state().store.measurements())})


def _missing_coordinates(state, snap, bundle):
    since = (snap.today.replace(day=1) - timedelta(days=1)).replace(day=1)
    data = _dispatch_data(state, since - timedelta(days=10), snap.today + timedelta(days=1), snap.today, False)
    ids = set(snap.plan.customer_ids)
    day = since
    workdays, off = bundle.settings['workdays'], dp.holidays_of(bundle.settings)
    last_day = dp.next_workday(snap.today, workdays, off)
    place = dp.place_of(data.customers, data.addresses)
    while day <= last_day:
        if dp.is_workday(day, workdays, off):
            lo, hi = dp.order_window(day, workdays, off)
            try:
                draft, _ = _stored_draft(state, day)
            except StoreError:
                draft = None
            selected = dp.to_deliver([o for o in data.orders if lo <= o.order_date < hi], day, lo,
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
        prediction = stored[0].get('prediction', {}) if stored is not None else {}
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
    if not isinstance(payload, dict) or set(payload) not in ({'customer_id', 'access'}, {'customer_id', 'access', 'window'},
                                                             {'customer_id', 'access', 'window', 'unload_min'},
                                                             {'customer_id', 'unload_min'}):
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
        state.store.save_customer_constraints(cid, access, window, session.get('username'), unload)
        logger.info('[Routes] Окно приёма клиента %d: %s (%s)', cid, window or 'убрано', session.get('username'))
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
                  (not query and (cid in bundle.vehicle_access or cid in bundle.windows or cid in bundle.unload_min)) or
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


def _learning_days(state: RoutesState, bundle: Bundle, since: date, until: date
                   ) -> list[tuple[str, date, list[ac.PlanStop], ac.DayActual, dict[str, Any] | None, int, int]]:
    """Машино-дни с треком за since…until: (машина, день, точки плана, факт, черновик развоза, рейсов и точек машины
    по плану). Место точки в объезде — по черновику «Развоза» дня, без него — порядок /day терминала. Факт дня — из
    кэша, пока не изменились трек, доставки, снимок /day (FleetFacts.version), склад, план машины и окна приёма."""
    if state.fleet_facts is None:
        return []
    windows = {cid: w.span() for cid, w in bundle.windows.items()}
    windows_key = tuple(sorted(windows.items()))
    drafts: dict[str, dict[str, Any] | None] = {}
    out = []
    for car, ds in state.fleet_facts.car_days(since.isoformat(), until.isoformat()):
        if ds not in drafts:
            got = state.store.load_dispatch(ds)
            drafts[ds] = got[0] if got is not None else None
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
        loads += learning.load_obs(day, actual, stops, plan)
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
    for car, day, stops, actual, draft, plan_trips, plan_stops in days:
        truck = bundle.trucks.get(car)
        prediction = (((draft or {}).get('prediction') or {}).get('trucks') or {}).get(car)
        rows.append(learning.day_report(car, day, actual, stops, prediction, plan_trips, plan_stops,
                                        truck.capacity_kg if truck is not None else None,
                                        learning.daily_l100(intervals, car, day)))
    rows.sort(key=lambda r: (r['day'], r['car_code']), reverse=True)
    return jsonify({'success': True, 'from': rng[0].isoformat(), 'to': rng[1].isoformat(),
                    'connected': state.fleet_facts is not None, 'depot': bundle.depot is not None,
                    'days': rows, **_status_body(state),
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
    line = ac.simplify([f.point for f in track], MAP_TRACK_POINTS)
    stops, actual, draft = (days[0][2], days[0][3], days[0][4]) if days else ([], ac.DayActual(0, 0.0, None, None), None)
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
    (работает, тоннаж и расход заданы). ERP недоступна — названия только у ручных, «работает» — не выключена."""
    try:
        snap, _ = state.snapshots.cached()
    except ErpError:
        logger.warning('[Routes] Журнал гаража: ERP недоступна — машины без названий ERP', exc_info=True)
        snap = None
    out = []
    for code, t in sorted(bundle.trucks.items()):
        car = snap.cars.get(code) if snap is not None and not t.manual else None
        active = bundle.truck_active(code, snap.active_cars) if snap is not None else t.active is not False
        out.append({'car_code': code, 'name': (car.name if car is not None else t.name) or '',
                    'active': bool(active and t.capacity_kg is not None and t.fuel_l_per_100km is not None)})
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
    """Фоновый расчёт факта за since…until (в кэш _learning_days). Сбой — в журнал и в garage_warm_failed: следующий
    запрос этого периода получит {failed: true}, а не тот же долгий пересчёт с 500."""
    try:
        _learning_days(state, bundle, since, until)
    except Exception:
        logger.exception('[Routes] «Նորմ և փաստ»: факт %s…%s не посчитан', since, until)
        with state.actuals_lock:
            state.garage_warm_failed.add((since, until))
    finally:
        with state.actuals_lock:
            state.garage_warm.pop((since, until), None)


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
    garage.ALERT_PCT %, по дням — строки learning.day_report. Дни GPS — до вчера (сегодня ещё не кончилось). Месяц
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
    else:
        days = []
    since = (first - timedelta(days=GARAGE_REFUEL_LOOKBACK_DAYS)).isoformat()
    refuels = [r for r in (facts.refuels(since) if facts is not None else [])
               if (r.get('eff_date') or r.get('date') or '') >= since]
    rejected: list[learning.Interval] = []
    intervals = learning.fuel_intervals(refuels, rejected)
    reports: dict[str, list[dict[str, Any]]] = {}
    for car, day, stops, actual, draft, plan_trips, plan_stops in days:
        truck = bundle.trucks.get(car)
        prediction = (((draft or {}).get('prediction') or {}).get('trucks') or {}).get(car)
        reports.setdefault(car, []).append(learning.day_report(
            car, day, actual, stops, prediction, plan_trips, plan_stops, truck.capacity_kg if truck is not None else None,
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
    out = []
    for code in sorted(set(known) | set(reports) | set(ends) | set(odd) | set(refuel_days)):
        t = known.get(code, {'car_code': code, 'name': '', 'active': False})
        fuel = garage.fuel_month(ends.get(code, ()), refuel_days.get(code, ()), first, last, odd.get(code, ()))
        km = garage.km_month(reports.get(code, ()))
        if not t['active'] and not km.days and not fuel.refuels and fuel.reason is not None and fuel.reason != 'suspicious':
            continue   # не в работе и в месяце ничего нет
        norm = norms.get(code, {'l100': None, 'source': None, 'learned': None})
        rows = sorted(reports.get(code, ()), key=lambda r: r['day'], reverse=True)
        out.append({**t, 'norm': {'l100': _r1(norm['l100']), 'source': norm['source'], 'learned': _r1(norm['learned'])},
                    'fuel': {'l100': _r1(fuel.l100), 'liters': round(fuel.liters, 1), 'km': round(fuel.km, 1),
                             'intervals': fuel.intervals, 'refuels': fuel.refuels, 'reason': fuel.reason,
                             'too_high': fuel.too_high, 'too_low': fuel.too_low,
                             'delta_pct': _r1(garage.delta_pct(fuel.l100, norm['l100'])),
                             'over': garage.over(fuel.l100, norm['l100'])},
                    'km': {'days': km.days, 'plan_days': km.plan_days, 'plan': round(km.plan_km, 1),
                           'fact': round(km.fact_km, 1),
                           'delta': round(km.fact_km - km.plan_km, 1) if km.plan_days else None,
                           'delta_pct': _r1(garage.delta_pct(km.fact_km, km.plan_km)) if km.plan_days else None,
                           'over': bool(km.plan_days) and garage.over(km.fact_km, km.plan_km)},
                    'unplanned_stays': km.unplanned_stays, 'order_changes': km.order_changes,
                    'days': [{'day': r['day'], 'plan_km': r['plan']['km'], 'fact_km': r['fact']['km'],
                              'liters': r['fact']['liters'], 'unplanned_stays': r['fact']['unplanned_stays'],
                              'order_changes': r['kpi']['order_changes'],
                              'over': garage.over(r['fact']['km'], r['plan']['km'])} for r in rows]})
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

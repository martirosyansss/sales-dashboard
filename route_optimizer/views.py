# -*- coding: utf-8 -*-
"""Страницы и API раздела «Маршруты» (§10).

Доступ обеспечивает глобальный before_request дашборда: аноним — 401/редирект на вход,
роль user — 403 (раздела нет в allowlist), admin — полный доступ.
Клиенту не отдаём текст исключений: ERP → 503, прочее → 500, подробности — в лог с [Routes].
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any, Callable, Collection, Mapping, Protocol, Sequence

from flask import Blueprint, Response, current_app, jsonify, render_template, request, session

from . import dispatch as dp
from . import evaluate, optimize
from . import fleet as fl
from .running_costs import profile_fields
from .erp import ErpError
from .geo import Point, haversine_km, is_valid_point
from .roads import RoadDistances, RoadProvider, roads_version
from .snapshot import CAR_IDLE_DAYS, MIN_REFRESH_SECONDS, ResultCache, Snapshot, SnapshotCache
from .store import (DEFAULT_MANAGER_FUEL, DEFAULT_SETTINGS, Bundle, Decision, Store, StoreError, center_auto,
                    check_window, validate_payload)
from .valhalla_engine import ValhallaProvider, ValhallaRoads
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
    dispatch_cache: dict[tuple[date, date, date], tuple[float, dp.DispatchData]] = field(default_factory=dict)
    dispatch_lock: threading.Lock = field(default_factory=threading.Lock)
    driver_geo: DriverGeo | None = None   # None — раздела «Առաքիչ» нет: точек водителей нет, всё как раньше
    driver_cache: tuple[float, dict[int, Point]] | None = None   # (time.monotonic(), точки) — DRIVER_TTL_SECONDS


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
    """Настройки раздела + точки водителей (Bundle.driver_points): ими считают обзор, оптимизация и развоз."""
    bundle = state.store.load()
    points = _driver_points(state)
    return replace(bundle, driver_points=points) if points else bundle


def _api(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Единая обработка ошибок API раздела."""
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ErpError:
            logger.exception('[Routes] ERP недоступна (%s)', request.path)
            return jsonify({'success': False, 'error': 'База данных ERP недоступна'}), 503
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
            return jsonify({'success': False, 'error': 'Внутренняя ошибка'}), 500
    return wrapper


@bp.after_request
def _no_store(resp: Response) -> Response:
    resp.headers.update(NO_CACHE_HEADERS)
    return resp


# --- Страницы ---

@bp.get('/routes')
def overview_page() -> str:
    return render_template('routes_overview.html')


@bp.get('/routes/settings')
def settings_page() -> str:
    return render_template('routes_settings.html')


@bp.get('/routes/optimize')
def optimize_page() -> str:
    return render_template('routes_optimize.html')


@bp.get('/routes/dispatch')
def dispatch_page() -> str:
    return render_template('routes_dispatch.html')


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
                             'text': f'ERP сейчас недоступна — показаны данные на '
                                     f'{snap.data_as_of:%d.%m %H:%M}'}, *payload['warnings']]
    return jsonify(body)


def _compute_overview(snap: Snapshot, bundle: Bundle, calib: evaluate.Calibration,
                      roads: RoadDistances | ValhallaRoads | None) -> dict[str, Any]:
    started = time.perf_counter()
    payload = evaluate.build_overview(snap, bundle, calib, roads)
    payload['generated_at'] = datetime.now().isoformat(timespec='seconds')
    logger.info('[Routes] Оценка плана за %.1f с (снимок %s)', time.perf_counter() - started, snap.id)
    return payload


def _roads(state: RoutesState, snap: Snapshot, bundle: Bundle,
           extra: Sequence[Point] = ()) -> RoadDistances | ValhallaRoads | None:
    """Расстояния по дорогам для расчёта по плану снимка (и точкам extra — заказы развоза) — до кэша оценки:
    граф OSM для всех точек — одним расчётом (первый раз — минуты, дальше кэш на диске). Valhalla — только если его
    матрицы для этих точек (у машин менеджеров и, когда грузовикам нужен Valhalla, у грузовиков) уже готовы: тайлы и
    матрицы считает фоновый поток (ValhallaProvider), а пока — граф OSM; запрос тяжёлой работы Valhalla не ждёт.
    Карты нет — None; граф не собрался — roads.failed (оценка считает по прямой и предупреждает roads_failed)."""
    points = [*evaluate.plan_points(snap, bundle, {}), *extra]
    roads = state.roads.get() if state.roads is not None else None
    if roads is not None:
        roads.ensure(points)
    if state.valhalla is not None:
        capacity = max((t.capacity_kg for t in _ready_trucks(snap, bundle).values()), default=None)
        roads = state.valhalla.get(roads, points, capacity) or roads
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
    return jsonify({
        'success': True,
        'settings': s,
        'traffic_provider': {'name': 'yandex', 'configured': bool(os.environ.get('ROUTES_YANDEX_API_KEY'))},
        'center_zone_default': DEFAULT_SETTINGS['center_zone'],
        'depot': {'lat': bundle.depot[0], 'lon': bundle.depot[1]} if bundle.depot else None,
        'trucks': _trucks_json(snap, bundle),
        'expeditors': _expeditors_json(snap, bundle),
        'car_idle_days': CAR_IDLE_DAYS,
        'managers': _managers_json(snap, bundle),
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
                               'error': 'Ожидается JSON (Content-Type: application/json)'}), 415)
    try:
        payload = request.get_json(silent=True)
    except RecursionError:   # silent глотает только ValueError, а сверхглубокая вложенность — это
        payload = None       # RecursionError парсера: тоже некорректный запрос (400), а не сбой (500)
    if payload is None:
        return None, (jsonify({'success': False, 'error': 'Некорректный JSON',
                               'errors': {'_': 'Некорректный JSON'}}), 400)
    return payload, None


def _bad_request(errors: dict[str, str]) -> Any:
    return jsonify({'success': False, 'error': next(iter(errors.values())), 'errors': errors}), 400


@bp.post('/api/routes/settings')
@_api
def api_settings_post() -> Any:
    """Сохранение настроек (§10.3): всё или ничего; ошибки — по путям полей."""
    if not request.is_json:
        return jsonify({'success': False,
                        'error': 'Ожидается JSON (Content-Type: application/json)'}), 415
    try:
        payload = request.get_json(silent=True)
    except RecursionError:   # silent глотает только ValueError, а сверхглубокая вложенность — это
        payload = None       # RecursionError парсера: тоже некорректный запрос (400), а не сбой (500)
    if payload is None:
        return jsonify({'success': False, 'errors': {'_': 'Некорректный JSON'}}), 400
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


def _trucks_json(snap: Snapshot, bundle: Bundle) -> list[dict[str, Any]]:
    """Машины CARS и их настройки, затем ручные машины (их нет в ERP). Машина закреплена за водителем, а не
    за менеджером (ответ владельца №29) — вместо менеджера подсказка из ERP: сколько машина возит в день
    (90 дней) и когда последний раз была в накладных. «Активна» — действующее значение; active_source:
    manual — выбор владельца, auto — решают накладные (auto_active: возила за CAR_IDLE_DAYS дней).
    «Можно в центр» (center_ok) — так же: center_ok_source, auto_center_ok — по названию (машины JAC)."""
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
                'van_agent_id': t.van_agent_id, 'van': _agent_json(snap, t.van_agent_id),
                'erp_days': 0, 'erp_kg_day': None, 'erp_kg_day_max': None,
            })
    return out


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
    body: dict[str, Any] = {'success': False, 'error': 'Расчёт уже идёт'}
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
        _finish(state, job, error='Не удалось запустить расчёт')
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
        _finish(state, job, error='База данных ERP недоступна')
    except StoreError as e:
        logger.exception('[Routes] Оптимизация %s: база маршрутов', job.id)
        _finish(state, job, error=str(e))   # текст StoreError — для пользователя
    except optimize.OptimizeError as e:
        logger.warning('[Routes] Оптимизация %s не выполнена: %s', job.id, e)
        _finish(state, job, error=str(e))
    except Exception:
        logger.exception('[Routes] Оптимизация %s: внутренняя ошибка', job.id)
        _finish(state, job, error='Внутренняя ошибка расчёта — подробности в журнале сервера')
    finally:
        _finish(state, job, error='Расчёт прерван')   # задача уже завершена — ничего не меняет


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
            return jsonify({'success': False, 'error': 'Расчётов ещё не было'}), 404
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
            return jsonify({'success': False, 'error': 'Расчёт не найден'}), 404
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
                        'error':'Принятый план не помещается в смену. Проверьте дни и частоты и пересчитайте план.',
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


def _ready_trucks(snap: Snapshot, bundle: Bundle, active_only: bool = True) -> dict[str, fl.FleetTruck]:
    """Машины, готовые к расчёту: тоннаж и расход заданы (и активны — для плана); с правом въезда в центр."""
    names = {code: car.name for code, car in snap.cars.items()}
    if active_only:
        ready, _ = fl.fleet_trucks(bundle.resolved_trucks(snap.active_cars), names)
        return {t.car_code: replace(t, center_ok=bundle.truck_center_ok(t.car_code, names.get(t.car_code)))
                for t in ready}
    return {code: fl.FleetTruck(code, names.get(code) or t.name, float(t.capacity_kg), float(t.fuel_l_per_100km),
                                bundle.truck_center_ok(code, names.get(code)), **profile_fields(t))
            for code, t in sorted(bundle.trucks.items())
            if t.capacity_kg is not None and t.fuel_l_per_100km is not None}


def _dispatch_ctx(state: RoutesState, snap: Snapshot, bundle: Bundle, day: date,
                  trucks: dict[str, fl.FleetTruck], points: list[Any]) -> dp.DayContext | None:
    """Контекст расчёта рейсов; склада или машин нет — None (страница объясняет, что заполнить)."""
    if bundle.depot is None or not trucks:
        return None
    s = bundle.settings
    calib = _calibration(state, snap, s)
    roads = _roads(state, snap, bundle, [*points, bundle.depot])
    norms = evaluate.Norms.from_settings(s, calib, roads if roads is not None and not roads.failed else None)
    norms = norms.for_trucks()   # развоз — профиль грузовика (км Valhalla — в режиме valhalla, минуты — ROUTES_TRUCK_TIME)
    h, m = map(int, s['truck_work_start'].split(':'))
    norms = replace(norms, traffic_weekday=day.weekday(), traffic_start_min=float(h * 60 + m))
    if s.get('traffic_mode') == 'yandex':
        from .traffic_provider import load
        provider, status = load([bundle.depot, *points], day, s['truck_work_start'])
        norms = replace(norms, provider=provider, traffic_status=status)
    h2, m2 = map(int, s['truck_overtime_end'].split(':'))
    return dp.DayContext(day, bundle.depot, trucks, norms, fl.TruckNorms.from_settings(s), h * 60 + m,
                         float(h2 * 60 + m2 - (h * 60 + m)), float(s['min_trip_revenue']),
                         {cid: w.span() for cid, w in bundle.windows.items()},
                         tuple((lat, lon) for lat, lon in s['center_zone']), vehicle_access=bundle.vehicle_access)


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


def _day_orders(state: RoutesState, bundle: Bundle, day: date,
                refresh: bool) -> tuple[date, date, dp.DispatchData, dp.Selection]:
    """Окно заказов дня, заказы ERP (кэш _dispatch_data) и отбор к доставке."""
    since, until = dp.order_window(day, bundle.settings['workdays'])
    data = _dispatch_data(state, dp.backlog_since(since, bundle.settings['workdays']), until, day, refresh)
    return since, until, data, dp.to_deliver(data.orders, day, since)


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
                   draft: dp.Draft | None, carried: Collection[str] = ()) -> list[dp.DispatchOrder]:
    """Заказы в развозе: заказы дня без «не везём сегодня» + заказы прошлых дней в развозе (_backlog_in)."""
    excluded = draft.excluded if draft is not None else set()
    inside = _backlog_in(draft, carried)
    return [o for o in deliver if o.isn not in excluded] + [o for o in backlog if o.isn in inside]


def _carried(state: RoutesState, day: date, workdays: Sequence[int], backlog: list[dp.DispatchOrder]) -> set[str]:
    """Заказы, которые логист перенёс на этот день («Везти завтра», №25) в планах с прошлого рабочего дня
    по вчера (и из нерабочего дня между ними) и которые ещё не отгружены. Битый черновик — без переносов."""
    out: set[str] = set()
    d = dp.previous_workday(day, workdays)
    while d < day:
        try:
            stored = state.store.load_dispatch(d.isoformat())
        except StoreError:
            stored = None
        if stored is not None:
            out |= dp.Draft.from_json(stored[0]).deferred
        d += timedelta(days=1)
    return out & {o.isn for o in backlog}


def _defer_target(day: date, workdays: Sequence[int]) -> tuple[date, date]:
    """(день доставки, куда «Везти завтра», самая ранняя дата заказа, которую он ещё видит)."""
    target = dp.next_workday(day, workdays)
    since, _ = dp.order_window(target, workdays)
    return target, dp.backlog_since(since, workdays)





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
        changes = dp.since_build(draft.built_orders, [*deliver, *moved_in],
                                 [*deliver, *(o for o in backlog if o.isn in inside)], draft.excluded)
    return {
        'orders_still_coming': dp.orders_still_coming(day, s['workdays'], _clock(), s['dispatch_ready_time']),
        'ready_time': s['dispatch_ready_time'],
        'built_at': draft.built_at if draft is not None else None,
        'new_since_build': changes['new'] if changes else None,
        'removed_since_build': changes['removed'] if changes else None,
        'orders_sig': _orders_sig(data),
        'data_as_of': data.loaded_at.isoformat(timespec='seconds'),
    }


def _load_day(state: RoutesState, bundle: Bundle, day: date, refresh: bool = False,
              draft: dp.Draft | None = None, rev: int | None = None) -> _DispatchDay:
    snap, _ = state.snapshots.cached()
    since, until, data, sel = _day_orders(state, bundle, day, refresh)
    deliver, backlog = sel.main, sel.backlog
    if draft is None:
        draft, rev = _stored_draft(state, day)
    carried = _carried(state, day, bundle.settings['workdays'], backlog)
    coords: dict[int, Any] = {}

    def coord(cid: int) -> Any:
        if cid not in coords:
            coords[cid] = evaluate.visit_coord(snap, cid, 0, bundle.geo_overrides, bundle.driver_points)
        return coords[cid]

    stops = dp.build_stops(_active_orders(deliver, backlog, draft, carried), coord)
    ready = _ready_trucks(snap, bundle)
    ctx = _dispatch_ctx(state, snap, bundle, day, ready, [s.point for s in stops if s.point is not None])
    return _DispatchDay(day, since, until, data, deliver, backlog, sel.shipped_before, sel.self_delivery, draft,
                        rev or 0, stops, ctx, ready, snap, bundle, carried)


def _day_stops(dd: _DispatchDay, draft: dp.Draft) -> list[dp.Stop]:
    """Точки развоза дня для черновика draft (его «не везём сегодня» и добавленные заказы)."""
    return dp.build_stops(_active_orders(dd.deliver, dd.backlog, draft, dd.carried),
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
                       'ready': ready is not None, 'selected': ready is not None and code in selected})
    added = _backlog_in(draft, dd.carried)
    no_coords = [s for s in dd.stops if s.point is None]
    active = _active_orders(dd.deliver, dd.backlog, draft, dd.carried)
    excl = [o for o in dd.deliver if o.isn in excluded]

    def order_json(o: dp.DispatchOrder) -> dict[str, Any]:
        code, name = dd.data.customers.get(o.customer_id) or ('', '')
        return {'isn': o.isn, 'doc_num': o.doc_num, 'customer_id': o.customer_id, 'code': code, 'name': name,
                'order_date': o.order_date.isoformat(), 'kg': round(o.kg), 'revenue': round(o.revenue),
                'added': o.isn in added, 'deferred': draft is not None and o.isn in draft.deferred,
                'carried': o.isn in dd.carried}

    body: dict[str, Any] = {
        'day': dd.day.isoformat(), 'weekday': dd.day.isoweekday(),
        'today': today.isoformat(), 'is_past': dd.day < today,
        'default_day': dp.next_workday(today, s['workdays']).isoformat(),
        'order_dates': {'since': dd.since.isoformat(), 'until': (dd.until - timedelta(days=1)).isoformat()},
        'work_start': s['truck_work_start'], 'work_end': s['truck_work_end'],
        'overtime_end': s['truck_overtime_end'],
        'depot': {'lat': dd.bundle.depot[0], 'lon': dd.bundle.depot[1]} if dd.bundle.depot else None,
        'problems': problems, 'trucks': trucks, 'rev': dd.rev,
        'vehicle_options': [{'car_code': code, 'name': name} for code, name in sorted(names.items())],
        'orders': {'count': len(active), 'kg': round(sum(o.kg for o in active)),
                   'revenue': round(sum(o.revenue for o in active)), 'customers': len(dd.stops),
                   'shipped_before': dd.shipped_before, 'excluded': len(excl),
                   'self_delivery': len(dd.self_delivery),
                   'self_delivery_kg': round(sum(o.kg for o in dd.self_delivery)),
                   'no_coords': len(no_coords), 'no_coords_kg': round(sum(x.kg for x in no_coords))},
        'stops_no_coords': [info(x) | {'kg': round(x.kg)} for x in no_coords],
        'excluded': [order_json(o) for o in excl],
        # не отгружены с прошлых дней: в план — только добавленные логистом (added)
        'backlog': [order_json(o) for o in dd.backlog],
        'backlog_since': dp.backlog_since(dd.since, s['workdays']).isoformat(),
        'plan': None,
        'overtime': draft.overtime if draft is not None else False,
        'overtime_ok': draft.overtime_ok if draft is not None else False,
        'min_trip_revenue': s['min_trip_revenue'],
        'defer_to': _defer_target(dd.day, s['workdays'])[0].isoformat(),
        'overtime_days_month': _overtime_days(_state().store, dd.day),
        'geo_suggestions': _geo_suggestions(_state(), dd),
        **_freshness(dd.day, dd.bundle, dd.data, dd.deliver, dd.backlog, draft, dd.carried),
    }
    if draft is not None and dd.ctx is not None:
        plan = dp.plan_view(dd.ctx, dd.stops, draft, info)
        plan['built_at'] = draft.built_at
        plan['baseline'] = dp.baseline(dd.ctx, dd.stops, draft,
                                       dp.history_cars(dd.data.agent_cars, dd.bundle.van_trucks()))
        body['plan'] = plan
    return body


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
    day = _parse_day(raw) if raw else dp.next_workday(_clock().date(), bundle.settings['workdays'])
    if day is None:
        return _bad_request({'date': 'дата в формате ГГГГ-ММ-ДД'})
    dd = _load_day(state, bundle, day, refresh=request.args.get('refresh') == '1')
    return jsonify({'success': True, **_dispatch_body(dd)})


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
    _, _, data, sel = _day_orders(state, bundle, day, refresh=False)
    draft, rev = _stored_draft(state, day)
    carried = _carried(state, day, bundle.settings['workdays'], sel.backlog)
    active = _active_orders(sel.main, sel.backlog, draft, carried)
    return jsonify({'success': True, 'day': day.isoformat(), 'rev': rev,
                    'orders': {'count': len(active), 'kg': round(sum(o.kg for o in active)),
                               'revenue': round(sum(o.revenue for o in active))},
                    **_freshness(day, bundle, data, sel.main, sel.backlog, draft, carried)})


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


def _capture_prediction(dd, draft):
    view = dp.plan_view(dd.ctx, dd.stops, draft, _stop_info(dd))
    now = _clock()
    start = datetime.combine(dd.day, datetime.strptime(dd.bundle.settings['truck_work_start'], '%H:%M').time())
    draft.prediction = {'created_at': now.isoformat(), 'prospective': now < start,
        'trucks': {t['car_code']: {key: t.get(key) for key in ('km', 'minutes', 'liters', 'loading_minutes', 'wear_amd')}
                   for t in view['trucks']}}


def _conflict(text: str) -> Any:
    return jsonify({'success': False, 'error': text, 'conflict': True}), 409


@bp.post('/api/routes/dispatch/build')
@_api
def api_dispatch_build() -> Any:
    """«Собрать рейсы»: {"date", "trucks": [коды машин дня]}. Закреплённые рейсы и исключённые заказы
    прежнего черновика сохраняются, остальное раскладывается заново."""
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
    unknown = sorted(set(codes) - set(dd.ready))
    if unknown:
        return _bad_request({'trucks': 'машина не готова к расчёту: ' + ', '.join(unknown)})
    if not codes:
        return _bad_request({'trucks': 'отметьте хотя бы одну машину'})
    started = time.perf_counter()
    # первая сборка дня: перенесённые сюда заказы прошлого дня — сразу в развозе
    draft = dp.build(dd.ctx, dd.stops, dd.draft, codes, _now())
    # отметка сборки: все заказы дня (и исключённые — они не «новые») + добавленные заказы прошлых дней
    inside = _backlog_in(draft, dd.carried)
    draft.built_orders = dp.order_marks([*dd.deliver, *(o for o in dd.backlog if o.isn in inside)])
    draft.overtime = dp.runs_late(dd.ctx, dd.stops, draft)
    _capture_prediction(dd, draft)
    rev = state.store.save_dispatch(day.isoformat(), draft.to_json(), session.get('username'))
    logger.info('[Routes] Развоз на %s собран (%s) за %.1f с: точек %d, рейсов %d, машин %d', day,
                session.get('username'), time.perf_counter() - started, len(dd.stops), len(draft.trips), len(codes))
    dd.draft, dd.rev = draft, rev or 0
    return jsonify({'success': True, **_dispatch_body(dd)})


@bp.post('/api/routes/dispatch/edit')
@_api
def api_dispatch_edit() -> Any:
    """Правка логиста: {"date", "rev", "action": move | pin | unpin | exclude | include, …}
    (dispatch.apply_edit). rev — номер черновика, от которого правка: план изменён в другой вкладке — 409.
    В ответе — день целиком и delta_km: как изменились км плана."""
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
    km_before = dp.plan_view(dd.ctx, dd.stops, dd.draft, info)['summary']['km']
    workdays = bundle.settings['workdays']
    deferred_before = set(dd.draft.deferred)
    try:
        draft = dp.apply_edit(dd.ctx, dd.stops, dd.draft, payload, {o.isn for o in dd.deliver},
                              {o.isn for o in dd.backlog}, defer_since=_defer_target(day, workdays)[1],
                              carried=dd.carried)
    except dp.DispatchError as e:
        return _bad_request({'_': str(e)})
    if day < _clock().date() and draft.deferred != deferred_before:
        # перенос с прошедшего дня меняет развоз уже другого дня — задним числом нельзя
        return _bad_request({'_': 'Прошедший день — перенос на другой день не меняется'})
    # заказы после правки («не везём сегодня» / вернуть меняют точки и вес) — отметка дня по ним
    draft.overtime = dp.runs_late(dd.ctx, _day_stops(dd, draft), draft)
    _capture_prediction(dd, draft)
    rev = state.store.save_dispatch(day.isoformat(), draft.to_json(), session.get('username'), expected_rev=dd.rev)
    if rev is None:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    dd = _load_day(state, bundle, day, draft=draft, rev=rev)
    body = _dispatch_body(dd)
    body['delta_km'] = round(body['plan']['summary']['km'] - km_before, 1) if body['plan'] else None
    return jsonify({'success': True, **body})


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
    draft = dp.overtime(dd.ctx, dd.stops, dd.draft)
    draft.overtime = dp.runs_late(dd.ctx, dd.stops, draft)
    _capture_prediction(dd, draft)
    rev = state.store.save_dispatch(day.isoformat(), draft.to_json(), session.get('username'), expected_rev=dd.rev)
    if rev is None:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    logger.info('[Routes] Развоз на %s: после конца дня (%s), переработка: %s', day, session.get('username'),
                draft.overtime)
    dd = _load_day(state, bundle, day, draft=draft, rev=rev)
    return jsonify({'success': True, **_dispatch_body(dd)})


@bp.post('/api/routes/dispatch/reset')
@_api
def api_dispatch_reset() -> Any:
    """«Начать заново»: черновик на дату удаляется (исключения и закрепления — тоже)."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    state = _state()
    state.store.delete_dispatch(day.isoformat())
    dd = _load_day(state, _bundle(state), day)
    return jsonify({'success': True, **_dispatch_body(dd)})


@bp.get('/api/routes/dispatch/fact')
@_api
def api_dispatch_fact() -> Any:
    """«План и факт» за прошедшую дату: км фактической раскладки по машинам ERP (каждая — лучшим
    маршрутом) против рейсов программы на тех же машинах и тех же доставках. Накладные экспедитора без
    машины — рейсы закреплённой за ним ручной машины."""
    state = _state()
    bundle = _bundle(state)
    day = _parse_day(request.args.get('date'))
    if day is None or day >= _clock().date():
        return _bad_request({'date': 'прошедшая дата в формате ГГГГ-ММ-ДД'})
    if state.fact_loader is None:
        raise ErpError('Загрузчик факта не подключён')
    snap, _ = state.snapshots.cached()
    trucks = _ready_trucks(snap, bundle, active_only=False)
    data = state.fact_loader(day)

    def coord(cid: int) -> Any:
        return evaluate.visit_coord(snap, cid, 0, bundle.geo_overrides, bundle.driver_points)

    ctx = _dispatch_ctx(state, snap, bundle, day, trucks,
                        [p for d in data.docs if (p := coord(d.customer_id).point) is not None])
    if ctx is None:
        return _bad_request({'_': 'Сначала укажите склад и тоннаж с расходом машин в настройках'})
    return jsonify({'success': True, 'fact': dp.plan_vs_fact(ctx, data.docs, coord, bundle.van_trucks())})


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
    last_day = dp.next_workday(snap.today, bundle.settings['workdays'])
    while day <= last_day:
        if day.isoweekday() in bundle.settings['workdays']:
            lo, hi = dp.order_window(day, bundle.settings['workdays'])
            selected = dp.to_deliver([o for o in data.orders if lo <= o.order_date < hi], day, lo)
            ids.update(o.customer_id for o in selected.main)
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
    writer.writerow(['ID клиента', 'Код', 'Название', 'Адрес', 'Широта', 'Долгота'])
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
        return _bad_request({'_': 'ожидалось {"customer_id", "lat", "lon"}'})
    cid = payload['customer_id']
    if isinstance(cid, bool) or not isinstance(cid, int) or not 0 < cid < 2 ** 31:
        return _bad_request({'customer_id': 'ожидался код клиента'})
    lat, lon = payload['lat'], payload['lon']
    point = None
    if lat is not None or lon is not None:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (lat, lon)) \
                or not is_valid_point(lat, lon):
            return _bad_request({'point': 'точка вне Армении'})
        point = (float(lat), float(lon))
    state = _state()
    state.store.save_geo_override(cid, point, session.get('username'))
    logger.info('[Routes] Точка клиента %d %s (%s)', cid, 'поставлена' if point else 'убрана', session.get('username'))
    return jsonify({'success': True})


_EVENT_ID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
GEO_DECISIONS = ('accepted', 'rejected')
GEO_GONE = 'Предложение не найдено или уже решено — обновите страницу'


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
        return _bad_request({'_': 'ожидалось {"event_id", "decision"}'})
    event_id = payload['event_id']
    if not isinstance(event_id, str) or not _EVENT_ID_RE.match(event_id):
        return _bad_request({'event_id': 'ожидался id предложения'})
    decision = payload['decision']
    if decision not in GEO_DECISIONS:
        return _bad_request({'decision': 'решение: accepted или rejected'})
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
        return _bad_request({'_': 'ожидалось {"customer_id", "window"}'})
    cid = payload['customer_id']
    if isinstance(cid, bool) or not isinstance(cid, int) or not 0 < cid < 2 ** 31:
        return _bad_request({'customer_id': 'ожидался код клиента'})
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
    """Допуск магазина и необязательное окно приёма: оба поля сохраняются атомарно."""
    payload, error = _json_body()
    if error is not None:
        return error
    if not isinstance(payload, dict) or set(payload) not in ({'customer_id', 'access'}, {'customer_id', 'access', 'window'}):
        return _bad_request({'_': 'ожидалось {"customer_id", "access"} с необязательным "window"'})
    cid = payload['customer_id']
    if isinstance(cid, bool) or not isinstance(cid, int) or not 0 < cid < 2 ** 31:
        return _bad_request({'customer_id': 'ожидался код клиента'})
    state = _state()
    snap, _ = state.snapshots.get(allow_stale=True)
    if cid not in snap.customers:
        return _bad_request({'customer_id': 'магазин не найден — обновите страницу'})
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
        state.store.save_customer_constraints(cid, access, window, session.get('username'))
        logger.info('[Routes] Окно приёма клиента %d: %s (%s)', cid, window or 'убрано', session.get('username'))
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
        return _bad_request({'q': 'поиск: не больше 100 символов'})
    customer_id = request.args.get('customer_id')
    if customer_id is not None:
        if not customer_id.isascii() or not customer_id.isdigit() or len(customer_id) > 10 or not 0 < int(customer_id) < 2 ** 31:
            return _bad_request({'customer_id': 'ожидался код клиента'})
        customer_id = int(customer_id)
    state = _state()
    snap, _ = state.snapshots.get(allow_stale=True)
    bundle = _bundle(state)
    customers = [c for cid, c in snap.customers.items() if
                 (cid == customer_id if customer_id is not None else
                  (not query and (cid in bundle.vehicle_access or cid in bundle.windows)) or
                  (query and query in f'{c.code} {c.name} {cid}'.casefold()))]
    customers.sort(key=lambda c: (c.name or '', c.id))
    return jsonify({'success': True, 'total': len(customers),
                    'vehicles': [{'car_code': t['car_code'], 'name': t['name']} for t in _trucks_json(snap, bundle)],
                    'customers': [
        {'customer_id': c.id, 'code': c.code, 'name': c.name,
         'vehicle_access': bundle.vehicle_access[c.id].to_json() if c.id in bundle.vehicle_access else None,
         'window': bundle.windows[c.id].to_json() if c.id in bundle.windows else None}
        for c in customers[:30]]})


ROAD_LINES_MAX_POINTS = 3000   # точек во всех линиях одного запроса (день развоза — сотни)


@bp.post('/api/routes/road-lines')
@_api
def api_road_lines() -> Any:
    """Линии рейсов для карты вдоль дорог: {"lines": [[[широта, долгота], …], …]} — точки каждой линии
    по порядку объезда. Ответ: те же линии по дорогам; "lines": null — карты дорог нет или она не
    загрузилась: карта рисует по прямой."""
    payload, error = _json_body()
    if error is not None:
        return error
    lines = payload.get('lines') if isinstance(payload, dict) else None
    if not isinstance(lines, list) or not all(isinstance(line, list) for line in lines):
        return _bad_request({'lines': 'ожидался список линий из точек [широта, долгота]'})
    parsed: list[list[tuple[float, float]]] = []
    for line in lines:
        points = []
        for p in line:
            if not (isinstance(p, list) and len(p) == 2
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)
                    and is_valid_point(p[0], p[1])):
                return _bad_request({'lines': 'точка вне Армении или не [широта, долгота]'})
            points.append((float(p[0]), float(p[1])))
        parsed.append(points)
    if sum(map(len, parsed)) > ROAD_LINES_MAX_POINTS:
        return _bad_request({'lines': f'не больше {ROAD_LINES_MAX_POINTS} точек за запрос'})
    state = _state()
    roads = state.roads.get() if state.roads is not None else None
    out = roads.lines(parsed) if roads is not None else None
    return jsonify({'success': True, 'lines': out})

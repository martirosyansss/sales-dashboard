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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any, Callable

from flask import Blueprint, Response, current_app, jsonify, render_template, request, session

from . import dispatch as dp
from . import evaluate, optimize
from . import fleet as fl
from .erp import ErpError
from .geo import is_valid_point
from .roads import RoadDistances, RoadProvider, roads_version
from .snapshot import CAR_IDLE_DAYS, MIN_REFRESH_SECONDS, ResultCache, Snapshot, SnapshotCache
from .store import DEFAULT_MANAGER_FUEL, Bundle, Decision, Store, StoreError, validate_payload

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


@dataclass
class RoutesState:
    store: Store
    snapshots: SnapshotCache
    results: ResultCache
    jobs: OptimizeJobs = field(default_factory=OptimizeJobs)
    roads: RoadProvider | None = None     # None — карты дорог нет, км по прямой
    # план развоза: заказы ERP на дату (since, until, day) → DispatchData; факт развоза за дату → FactData
    dispatch_loader: Callable[[date, date, date], dp.DispatchData] | None = None
    fact_loader: Callable[[date], dp.FactData] | None = None
    dispatch_cache: dict[tuple[date, date, date], tuple[float, dp.DispatchData]] = field(default_factory=dict)
    dispatch_lock: threading.Lock = field(default_factory=threading.Lock)


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _clock() -> datetime:
    """Местное время сервера для «заказы ещё поступают» (тесты подменяют)."""
    return datetime.now()


def _state() -> RoutesState:
    return current_app.extensions[EXTENSION_KEY]


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
        except optimize.OptimizeError as e:   # текст — для пользователя (например, цикл ERP > 2 недель)
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
    bundle = state.store.load()
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
                      roads: RoadDistances | None) -> dict[str, Any]:
    started = time.perf_counter()
    payload = evaluate.build_overview(snap, bundle, calib, roads)
    payload['generated_at'] = datetime.now().isoformat(timespec='seconds')
    logger.info('[Routes] Оценка плана за %.1f с (снимок %s)', time.perf_counter() - started, snap.id)
    return payload


def _roads(state: RoutesState, snap: Snapshot, bundle: Bundle) -> RoadDistances | None:
    """Расстояния по дорогам для плана снимка: все точки плана — одним расчётом (первый раз —
    минуты, дальше кэш на диске). Карты нет — None; граф не собрался — roads.failed (оценка
    считает по прямой и предупреждает roads_failed)."""
    roads = state.roads.get() if state.roads is not None else None
    if roads is not None:
        roads.ensure(evaluate.plan_points(snap, bundle, {}))
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
    manual — выбор владельца, auto — решают накладные (auto_active: возила за CAR_IDLE_DAYS дней)."""
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
            'active': bundle.truck_active(code, active_cars),
            'active_source': 'auto' if t is None or t.active is None else 'manual',
            'auto_active': code in active_cars,
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
                'active': bool(t.active), 'active_source': 'manual', 'auto_active': None, 'last_used': None,
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
    bundle = state.store.load()
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
    bundle = state.store.load()
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
    bundle = state.store.load()
    # порядок внутри дня — той же функцией расстояния, что и в оценке (по дорогам, если есть карта)
    roads = _roads(state, snap, bundle)
    distance = None
    if roads is not None and not roads.failed:
        calib = _calibration(state, snap, bundle.settings)
        distance = evaluate.Norms.from_settings(bundle.settings, calib, roads).distance
    # принята только частота — в план идёт тот шаблон, что был в предложении
    body = optimize.plan_export(snap, bundle, _live_decisions(state, snap),
                                optimize.proposals_of(_last_result(state)), distance)
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
    """Машины, готовые к расчёту: тоннаж и расход заданы (и активны — для плана)."""
    names = {code: car.name for code, car in snap.cars.items()}
    if active_only:
        ready, _ = fl.fleet_trucks(bundle.resolved_trucks(snap.active_cars), names)
        return {t.car_code: t for t in ready}
    return {code: fl.FleetTruck(code, names.get(code) or t.name, float(t.capacity_kg), float(t.fuel_l_per_100km))
            for code, t in sorted(bundle.trucks.items())
            if t.capacity_kg is not None and t.fuel_l_per_100km is not None}


def _dispatch_ctx(state: RoutesState, snap: Snapshot, bundle: Bundle, day: date,
                  trucks: dict[str, fl.FleetTruck], points: list[Any]) -> dp.DayContext | None:
    """Контекст расчёта рейсов; склада или машин нет — None (страница объясняет, что заполнить)."""
    if bundle.depot is None or not trucks:
        return None
    s = bundle.settings
    calib = _calibration(state, snap, s)
    roads = _roads(state, snap, bundle)
    if roads is not None and not roads.failed:
        roads.ensure([*points, bundle.depot])
    norms = evaluate.Norms.from_settings(s, calib, roads if roads is not None and not roads.failed else None)
    h, m = map(int, s['truck_work_start'].split(':'))
    return dp.DayContext(day, bundle.depot, trucks, norms, fl.TruckNorms.from_settings(s), h * 60 + m)


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


def _day_orders(state: RoutesState, bundle: Bundle, day: date,
                refresh: bool) -> tuple[date, date, dp.DispatchData, dp.Selection]:
    """Окно заказов дня, заказы ERP (кэш _dispatch_data) и отбор к доставке."""
    since, until = dp.order_window(day, bundle.settings['workdays'])
    data = _dispatch_data(state, dp.backlog_since(since, bundle.settings['workdays']), until, day, refresh)
    return since, until, data, dp.to_deliver(data.orders, day, since)


def _stored_draft(state: RoutesState, day: date) -> tuple[dp.Draft | None, int]:
    stored = state.store.load_dispatch(day.isoformat())
    return (dp.Draft.from_json(stored[0]), stored[1]) if stored is not None else (None, 0)


def _active_orders(deliver: list[dp.DispatchOrder], backlog: list[dp.DispatchOrder],
                   draft: dp.Draft | None) -> list[dp.DispatchOrder]:
    """Заказы в развозе: заказы дня без «не везём сегодня» + добавленные логистом заказы прошлых дней."""
    excluded = draft.excluded if draft is not None else set()
    added = draft.added if draft is not None else set()
    return [o for o in deliver if o.isn not in excluded] + [o for o in backlog if o.isn in added]


def _orders_sig(data: dp.DispatchData) -> str:
    """Отпечаток заказов ERP: изменился — странице есть что перечитать."""
    raw = repr(sorted((o.isn, o.customer_id, o.agent_id, o.van_agent_id, round(o.kg, 3), round(o.revenue, 2),
                       o.shipped) for o in data.orders))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def _freshness(day: date, bundle: Bundle, data: dp.DispatchData, deliver: list[dp.DispatchOrder],
               backlog: list[dp.DispatchOrder], draft: dp.Draft | None) -> dict[str, Any]:
    """«Заказы ещё поступают» и что изменилось с последней сборки — для подсказки вверху страницы."""
    s = bundle.settings
    changes = None if draft is None else dp.since_build(draft.built_orders, deliver, [*deliver, *backlog],
                                                        draft.excluded)
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
    coords: dict[int, Any] = {}

    def coord(cid: int) -> Any:
        if cid not in coords:
            coords[cid] = evaluate.visit_coord(snap, cid, 0, bundle.geo_overrides)
        return coords[cid]

    stops = dp.build_stops(_active_orders(deliver, backlog, draft), coord)
    ready = _ready_trucks(snap, bundle)
    ctx = _dispatch_ctx(state, snap, bundle, day, ready, [s.point for s in stops if s.point is not None])
    return _DispatchDay(day, since, until, data, deliver, backlog, sel.shipped_before, sel.self_delivery, draft,
                        rev or 0, stops, ctx, ready, snap, bundle)


def _stop_info(dd: _DispatchDay) -> Callable[[dp.Stop], dict[str, Any]]:
    agents = dd.snap.agents

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
        }
    return info


def _dispatch_body(dd: _DispatchDay) -> dict[str, Any]:
    s = dd.bundle.settings
    today = date.today()
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
                       'l100': ready.l100 if ready else None,
                       'ready': ready is not None, 'selected': ready is not None and code in selected})
    added = draft.added if draft is not None else set()
    no_coords = [s for s in dd.stops if s.point is None]
    active = _active_orders(dd.deliver, dd.backlog, draft)
    excl = [o for o in dd.deliver if o.isn in excluded]

    def order_json(o: dp.DispatchOrder) -> dict[str, Any]:
        code, name = dd.data.customers.get(o.customer_id) or ('', '')
        return {'isn': o.isn, 'doc_num': o.doc_num, 'customer_id': o.customer_id, 'code': code, 'name': name,
                'order_date': o.order_date.isoformat(), 'kg': round(o.kg), 'revenue': round(o.revenue),
                'added': o.isn in added}

    body: dict[str, Any] = {
        'day': dd.day.isoformat(), 'weekday': dd.day.isoweekday(),
        'today': today.isoformat(), 'is_past': dd.day < today,
        'default_day': dp.next_workday(today, s['workdays']).isoformat(),
        'order_dates': {'since': dd.since.isoformat(), 'until': (dd.until - timedelta(days=1)).isoformat()},
        'work_start': s['truck_work_start'], 'work_end': s['truck_work_end'],
        'depot': {'lat': dd.bundle.depot[0], 'lon': dd.bundle.depot[1]} if dd.bundle.depot else None,
        'problems': problems, 'trucks': trucks, 'rev': dd.rev,
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
        **_freshness(dd.day, dd.bundle, dd.data, dd.deliver, dd.backlog, draft),
    }
    if draft is not None and dd.ctx is not None:
        plan = dp.plan_view(dd.ctx, dd.stops, draft, info)
        plan['built_at'] = draft.built_at
        plan['baseline'] = dp.baseline(dd.ctx, dd.stops, draft,
                                       dp.history_cars(dd.data.agent_cars, dd.bundle.van_trucks()))
        body['plan'] = plan
    return body


SETTINGS_TRUCKS_URL = '/routes/settings#trucks'


@bp.get('/api/routes/dispatch')
@_api
def api_dispatch() -> Any:
    """День развоза (?date=YYYY-MM-DD, по умолчанию — следующий рабочий день): машины, заказы,
    черновик рейсов с цифрами, сравнение «по менеджерам». ?refresh=1 — перечитать заказы ERP."""
    state = _state()
    bundle = state.store.load()
    raw = request.args.get('date')
    day = _parse_day(raw) if raw else dp.next_workday(date.today(), bundle.settings['workdays'])
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
    active = _active_orders(sel.main, sel.backlog, draft)
    return jsonify({'success': True, 'day': day.isoformat(), 'rev': rev,
                    'orders': {'count': len(active), 'kg': round(sum(o.kg for o in active)),
                               'revenue': round(sum(o.revenue for o in active))},
                    **_freshness(day, bundle, data, sel.main, sel.backlog, draft)})


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
    bundle = state.store.load()
    dd = _load_day(state, bundle, day)
    if dd.ctx is None:
        return _bad_request({'_': 'Сначала укажите склад и тоннаж с расходом машин в настройках'})
    unknown = sorted(set(codes) - set(dd.ready))
    if unknown:
        return _bad_request({'trucks': 'машина не готова к расчёту: ' + ', '.join(unknown)})
    if not codes:
        return _bad_request({'trucks': 'отметьте хотя бы одну машину'})
    started = time.perf_counter()
    draft = dp.build(dd.ctx, dd.stops, dd.draft, codes, _now())
    # отметка сборки: все заказы дня (и исключённые — они не «новые») + добавленные заказы прошлых дней
    draft.built_orders = dp.order_marks([*dd.deliver, *(o for o in dd.backlog if o.isn in draft.added)])
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
    bundle = state.store.load()
    dd = _load_day(state, bundle, day)
    if dd.draft is None or dd.ctx is None:
        return _conflict('Сначала соберите рейсы')
    if payload.get('rev') != dd.rev:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    info = _stop_info(dd)
    km_before = dp.plan_view(dd.ctx, dd.stops, dd.draft, info)['summary']['km']
    try:
        draft = dp.apply_edit(dd.ctx, dd.stops, dd.draft, payload, {o.isn for o in dd.deliver},
                              {o.isn for o in dd.backlog})
    except dp.DispatchError as e:
        return _bad_request({'_': str(e)})
    rev = state.store.save_dispatch(day.isoformat(), draft.to_json(), session.get('username'), expected_rev=dd.rev)
    if rev is None:
        return _conflict('План изменили в другой вкладке — обновите страницу')
    dd = _load_day(state, bundle, day, draft=draft, rev=rev)
    body = _dispatch_body(dd)
    body['delta_km'] = round(body['plan']['summary']['km'] - km_before, 1) if body['plan'] else None
    return jsonify({'success': True, **body})


@bp.post('/api/routes/dispatch/reset')
@_api
def api_dispatch_reset() -> Any:
    """«Начать заново»: черновик на дату удаляется (исключения и закрепления — тоже)."""
    payload, day, error = _dispatch_request()
    if error is not None:
        return error
    state = _state()
    state.store.delete_dispatch(day.isoformat())
    dd = _load_day(state, state.store.load(), day)
    return jsonify({'success': True, **_dispatch_body(dd)})


@bp.get('/api/routes/dispatch/fact')
@_api
def api_dispatch_fact() -> Any:
    """«План и факт» за прошедшую дату: км фактической раскладки по машинам ERP (каждая — лучшим
    маршрутом) против рейсов программы на тех же машинах и тех же доставках. Накладные экспедитора без
    машины — рейсы закреплённой за ним ручной машины."""
    state = _state()
    bundle = state.store.load()
    day = _parse_day(request.args.get('date'))
    if day is None or day >= date.today():
        return _bad_request({'date': 'прошедшая дата в формате ГГГГ-ММ-ДД'})
    if state.fact_loader is None:
        raise ErpError('Загрузчик факта не подключён')
    snap, _ = state.snapshots.cached()
    trucks = _ready_trucks(snap, bundle, active_only=False)
    data = state.fact_loader(day)

    def coord(cid: int) -> Any:
        return evaluate.visit_coord(snap, cid, 0, bundle.geo_overrides)

    ctx = _dispatch_ctx(state, snap, bundle, day, trucks,
                        [p for d in data.docs if (p := coord(d.customer_id).point) is not None])
    if ctx is None:
        return _bad_request({'_': 'Сначала укажите склад и тоннаж с расходом машин в настройках'})
    return jsonify({'success': True, 'fact': dp.plan_vs_fact(ctx, data.docs, coord, bundle.van_trucks())})


@bp.post('/api/routes/geo-override')
@_api
def api_geo_override() -> Any:
    """Ручная точка клиента: {"customer_id", "lat", "lon"}; lat и lon = null — убрать (снова ERP/GPS).
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

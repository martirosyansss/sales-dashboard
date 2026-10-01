# -*- coding: utf-8 -*-
"""Страницы и API раздела «Маршруты» (§10).

Доступ обеспечивает глобальный before_request дашборда: аноним — 401/редирект на вход,
роль user — 403 (раздела нет в allowlist), admin — полный доступ.
Клиенту не отдаём текст исключений: ERP → 503, прочее → 500, подробности — в лог с [Routes].
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable

from flask import Blueprint, Response, current_app, jsonify, render_template, request, session

from . import evaluate, optimize
from .erp import ErpError
from .roads import RoadDistances, RoadProvider, roads_version
from .snapshot import ResultCache, Snapshot, SnapshotCache
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


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


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


def _suggested_agent(snap: Snapshot, car_code: str, plan_agents: set[int]) -> int | None:
    """Агент с шаблонами, чьи продажи эта машина возила чаще всех за 90 дней."""
    usage = [(n, a) for a, n in snap.car_usage.get(car_code, {}).items() if a in plan_agents]
    return max(usage)[1] if usage else None


def _trucks_json(snap: Snapshot, bundle: Bundle) -> list[dict[str, Any]]:
    plan_agents = set(snap.plan.agent_ids)
    out = []
    for code, car in sorted(snap.cars.items()):
        t = bundle.trucks.get(code)
        out.append({
            'car_code': code,
            'name': car.name,
            'erp_closed': car.closed,
            'capacity_kg': t.capacity_kg if t else None,
            'fuel_l_per_100km': t.fuel_l_per_100km if t else None,
            'agent_id': t.agent_id if t else None,
            'suggested_agent_id': _suggested_agent(snap, code, plan_agents),
            'active': t.active if t else not car.closed,
        })
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

# -*- coding: utf-8 -*-
"""API терминала «Առաքիչ» /api/courier/v1/* (docs/plans/courier-api-contract.md).

Доступ: сессия дашборда НЕ нужна (app_v2 пропускает префикс мимо входа), вместо неё —
- `Authorization: Bearer <device_token>` во всех запросах: нет/неизвестен/отозван → 401 `unauthorized`;
- `X-Courier-Session` во всех, кроме ping, login, app-version, app/apk: нет/просрочена/чужого терминала → 401 `session`.
Ошибка — {"error": код, "message": текст по-армянски}; подробности исключений — только в лог с [Courier].
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta
from functools import wraps
from typing import Any, Callable

from flask import Blueprint, Response, g, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

from route_optimizer.erp import ErpError

from . import clock, events as ev
from .photos import MAX_PHOTO_BYTES, PHOTO_KINDS, image_ext, save_photo
from .security import token_hash, token_shape_ok, valid_pin
from .state import state
from .store import StoreError

logger = logging.getLogger(__name__)

bp = Blueprint('courier_api', __name__, url_prefix='/api/courier/v1')

MAX_BODY_BYTES = 4 * 1024 * 1024       # пачка ≤ 200 событий или фото ≤ 2 МБ + заголовки multipart
SEEN_EVERY = timedelta(seconds=30)     # «последняя связь» терминала пишется не чаще
NO_SESSION = {'courier_api.ping', 'courier_api.login', 'courier_api.app_version', 'courier_api.app_apk'}
NO_STORE = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}

MSG = {
    'unauthorized': 'Տերմինալը գրանցված չէ կամ անջատված է',
    'session': 'Մուտք գործեք PIN-ով',
    'pin': 'Սխալ PIN',
    'not_found': 'Չի գտնվել',
    'too_large': 'Չափազանց մեծ հարցում',
    'server': 'Սերվերի սխալ',
    'erp': 'ERP-ն հասանելի չէ, փորձեք մի փոքր ուշ',
}


def error(status: int, code: str, message: str | None = None, **extra: Any) -> tuple[Response, int]:
    resp = jsonify({'error': code, 'message': message or MSG.get(code, MSG['server']), **extra})
    resp.headers.update(NO_STORE)
    return resp, status


def _api(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except HTTPException:   # 413/400 Flask (тело больше предела, битый multipart) — как есть
            raise
        except ErpError:
            logger.exception('[Courier] ERP недоступна (%s)', request.path)
            return error(503, 'server', MSG['erp'])
        except StoreError:
            logger.exception('[Courier] База courier.db (%s)', request.path)
            return error(500, 'server')
        except Exception:
            logger.exception('[Courier] Внутренняя ошибка (%s)', request.path)
            return error(500, 'server')
    return wrapper


@bp.after_request
def _no_store(resp: Response) -> Response:
    resp.headers.update(NO_STORE)
    return resp


@bp.before_request
def _authenticate() -> Any:
    """Размер тела, токен терминала, сессия водителя — до любого обработчика."""
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        return error(413, 'too_large')
    try:
        request.max_content_length = MAX_BODY_BYTES   # Flask ≥ 3.1: предел и для тела без Content-Length
    except AttributeError:
        pass
    try:
        st = state()
        auth = request.headers.get('Authorization', '')
        token = auth[7:].strip() if auth[:7].lower() == 'bearer ' else ''
        terminal = st.store.terminal_by_token_hash(token_hash(token)) if token_shape_ok(token) else None
        if terminal is None or terminal.revoked:
            return error(401, 'unauthorized')
        g.courier_terminal = terminal
        g.courier_session = None
        if request.endpoint not in NO_SESSION:
            sess = request.headers.get('X-Courier-Session', '').strip()
            session = st.store.session(token_hash(sess), terminal.id) if token_shape_ok(sess) else None
            if session is None:
                return error(401, 'session')
            g.courier_session = session
        if terminal.last_seen_at is None or terminal.last_seen_at < clock.iso(clock.now() - SEEN_EVERY):
            st.store.touch_terminal(terminal.id)
    except StoreError:
        logger.exception('[Courier] База courier.db (%s)', request.path)
        return error(500, 'server')
    return None


def _json_body() -> dict[str, Any] | None:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _day_arg() -> Any:
    raw = request.args.get('date')
    return clock.today() if raw is None else clock.parse_day(raw)


def _car_name(code: str) -> str:
    st = state()
    if st.demo and code == 'TEST':
        return 'Թեստ'
    if st.cars_loader is None:
        return ''
    try:
        cars = st.refs.get(('cars', clock.today()), lambda: st.cars_loader(clock.today()))
    except ErpError:
        logger.warning('[Courier] Название машины %s не прочитано из ERP', code, exc_info=True)
        return ''
    return next((c['name'] for c in cars if c['code'] == code), '')


# --- эндпоинты ---

@bp.get('/ping')
@_api
def ping() -> Any:
    t = g.courier_terminal
    return jsonify({'ok': True, 'server_time': clock.iso(clock.now()), 'terminal': t.name, 'car_code': t.car_code})


@bp.post('/login')
@_api
def login() -> Any:
    """PIN водителя → сессия до 04:00 следующего дня. Блокировка — по терминалу: 5 неверных PIN подряд →
    429 на 15 минут (за туннелем у всех терминалов один IP). PIN неверного формата не считается попыткой."""
    st, t = state(), g.courier_terminal
    body = _json_body()
    pin = body.get('pin') if body else None
    now = clock.now()
    if t.locked_until and t.locked_until > clock.iso(now):
        return _locked(t.locked_until)
    if not valid_pin(pin):
        return error(400, 'bad_request', 'PIN-ը 4–6 թվանշան է')
    drivers = st.store.match_pin(pin)
    if len(drivers) > 1:
        logger.warning('[Courier] Один PIN у нескольких активных водителей: %s', [d.id for d in drivers])
        return error(403, 'pin', 'Այս PIN-ը կրկնվում է․ դիմեք ադմինիստրատորին')
    if not drivers:
        until = st.store.pin_failed(t.id)
        return _locked(until) if until else error(403, 'pin')
    driver = drivers[0]
    expires = clock.session_expiry(now)
    token = st.store.open_session(t.id, driver.id, expires)
    return jsonify({'session': token, 'expires_at': clock.iso(expires),
                    'driver': {'id': driver.id, 'name': driver.name},
                    'car': {'code': t.car_code, 'name': _car_name(t.car_code)}})


def _locked(until: str) -> Any:
    left = clock.parse_moment(until)
    retry = max(1, int((left - clock.now()).total_seconds())) if left else 900
    return error(429, 'locked', f'Չափազանց շատ սխալ փորձ։ Փորձեք {max(1, (retry + 59) // 60)} րոպեից',
                 retry_after=retry)


@bp.post('/logout')
@_api
def logout() -> Any:
    st = state()
    st.store.close_session(token_hash(request.headers.get('X-Courier-Session', '').strip()))
    return jsonify({'ok': True})


@bp.get('/day')
@_api
def day() -> Any:
    d = _day_arg()
    if d is None:
        return error(400, 'bad_request', 'date՝ ՏՏՏՏ-ԱԱ-ՕՕ ձևաչափով')
    return jsonify(state().days.get(g.courier_terminal.car_code, d))


@bp.post('/events')
@_api
def post_events() -> Any:
    body = _json_body()
    items = body.get('events') if body else None
    if not isinstance(items, list):
        return error(400, 'bad_request', 'Սպասվում է {"events": [...]}')
    if len(items) > ev.MAX_BATCH:
        return error(400, 'bad_request', f'Մեկ հարցումում՝ ոչ ավելի, քան {ev.MAX_BATCH} իրադարձություն')
    t, s = g.courier_terminal, g.courier_session
    result = ev.ingest(state().store, ev.Who(t.id, t.car_code, s.driver_id, s.driver_name), items)
    return jsonify(result.json())


@bp.post('/photos')
@_api
def post_photo() -> Any:
    st, t = state(), g.courier_terminal
    photo_id, event_id, kind = request.form.get('id'), request.form.get('event_id'), request.form.get('kind')
    if not isinstance(photo_id, str) or not ev.UUID_RE.match(photo_id):
        return error(400, 'bad_request', 'id՝ պետք է լինի uuid')
    if not isinstance(event_id, str) or not ev.UUID_RE.match(event_id):
        return error(400, 'bad_request', 'event_id՝ պետք է լինի uuid')
    if kind not in PHOTO_KINDS:
        return error(400, 'bad_request', 'kind՝ photo կամ signature')
    if st.store.has_photo(photo_id.lower()):
        return jsonify({'ok': True, 'duplicate': True})
    upload = request.files.get('file')
    if upload is None:
        return error(400, 'bad_request', 'file՝ պարտադիր է')
    data = upload.stream.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        return error(413, 'too_large', 'Ֆայլը 2 ՄԲ-ից մեծ է')
    if not data or image_ext(data) is None:
        return error(400, 'bad_request', 'Միայն JPEG կամ PNG')
    saved = save_photo(st.store, photo_id.lower(), event_id.lower(), kind, data, t.id)
    return jsonify({'ok': True, 'duplicate': True} if not saved else {'ok': True})


@bp.get('/status')
@_api
def status() -> Any:
    """Деньги водителя за день (по его событиям payment) и отметка кассира; отклонённые события."""
    d = _day_arg()
    if d is None:
        return error(400, 'bad_request', 'date՝ ՏՏՏՏ-ԱԱ-ՕՕ ձևաչափով')
    st, s = state(), g.courier_session
    day = d.isoformat()
    mine = [e for e in st.store.events_for_day(day) if e['driver_id'] == s.driver_id]
    money = ev.payments_total(mine)
    hand = st.store.handovers(day).get(s.driver_id)
    return jsonify({'date': day,
                    'cash': {'collected_invoice': money['invoice'], 'collected_debt': money['debt'],
                             'handed': hand['handed'] if hand else None,
                             'handed_at': hand['handed_at'] if hand else None,
                             'handed_by': hand['handed_by'] if hand else None},
                    'rejected_events': [{'id': r['id'], 'message': r['message']}
                                        for r in st.store.rejected_for(s.driver_id, day)]})


@bp.get('/app-version')
@_api
def app_version() -> Any:
    rel = state().store.latest_release()
    if rel is None:
        return error(404, 'not_found', 'APK-ն դեռ բեռնված չէ')
    return jsonify({'version_code': rel.version_code, 'version_name': rel.version_name, 'sha256': rel.sha256,
                    'size': rel.size, 'url': '/api/courier/v1/app/apk'})


@bp.get('/app/apk')
@_api
def app_apk() -> Any:
    st = state()
    rel = st.store.latest_release()
    path = os.path.join(st.store.apk_dir, rel.path) if rel else None
    if rel is None or path is None or not os.path.isfile(path):
        return error(404, 'not_found', 'APK-ն դեռ բեռնված չէ')
    return send_file(path, mimetype='application/vnd.android.package-archive', as_attachment=True,
                     download_name=f'araqich-{rel.version_name}.apk', max_age=0)


def json_http_error(e: Any) -> Any:
    """404/405/413 под префиксом API — JSON по контракту; остальные пути — как было (страница Flask)."""
    from .state import API_PREFIX
    if not request.path.startswith(API_PREFIX):
        return e
    code = getattr(e, 'code', 500)
    if code == 413:
        return error(413, 'too_large')
    if code == 405:
        return error(405, 'bad_request', 'Մեթոդը չի թույլատրվում')
    return error(404, 'not_found')

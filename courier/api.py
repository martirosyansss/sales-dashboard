# -*- coding: utf-8 -*-
"""API терминала «Առաքիչ» /api/courier/v1/* (docs/plans/courier-api-contract.md).

Доступ: сессия дашборда НЕ нужна (app_v2 пропускает префикс мимо входа), вместо неё —
- `Authorization: Bearer <device_token>` во всех запросах, кроме download/apk: нет/неизвестен/отозван → 401 `unauthorized`;
- `X-Courier-Session` во всех, кроме ping, login, app-version, app/apk: нет/просрочена/чужого терминала → 401 `session`.
Ошибка — {"error": код, "message": текст по-армянски}; подробности исключений — только в лог с [Courier].
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, timedelta
from functools import wraps
from typing import Any, Callable

from flask import Blueprint, Response, current_app, g, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

from route_optimizer.erp import ErpError

from . import clock, events as ev
from .day import DEMO_DAY
from .photos import MAX_PHOTO_BYTES, PHOTO_KINDS, image_ext, save_photo
from .routes_link import planned_crew
from .security import token_hash, token_shape_ok, valid_pin
from .state import state
from .store import PHOTO_BYTES_PER_DAY, PHOTOS_PER_DAY, Driver, PhotoLimit, PinReset, Session, StoreError, Terminal

logger = logging.getLogger(__name__)

bp = Blueprint('courier_api', __name__, url_prefix='/api/courier/v1')

MAX_BODY_BYTES = 4 * 1024 * 1024       # пачка ≤ 200 событий или фото ≤ 2 МБ + заголовки multipart
SEEN_EVERY = timedelta(seconds=30)     # «последняя связь» терминала пишется не чаще
NO_SESSION = {'courier_api.ping', 'courier_api.login', 'courier_api.app_version', 'courier_api.app_apk'}
NO_STORE = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}
DAY_WINDOW = 1                         # /day терминала: сегодня ± 1 день (контракт §5 п. 3)
LOGIN_BUSY_RETRY = 2                   # секунд: вход на этом терминале уже проверяется
PLAN_TTL = 60                          # секунд: экипаж плана «Развоза» дня в ответах терминалу (_planned)
PLAN_FAIL_TTL = 5                      # «Маршруты» недоступны — повторить не раньше

# Вход по PIN на терминале — по одному: параллельные попытки того же терминала сразу получают 429 (резерв
# попытки в базе — Store.pin_attempt — ограничивает перебор и между процессами).
_login_locks: dict[int, threading.Lock] = {}
_login_locks_guard = threading.Lock()

MSG = {
    'unauthorized': 'Տերմինալը գրանցված չէ կամ անջատված է',
    'session': 'Մուտք գործեք PIN-ով',
    'pin': 'Սխալ PIN',
    'pin_reset': 'Սխալ PIN կամ PIN-ը պետք է նորից սահմանել գրասենյակում',   # опечатка или PIN без перца — не различить
    'not_found': 'Չի գտնվել',
    'too_large': 'Չափազանց մեծ հարցում',
    'server': 'Սերվերի սխալ',
    'erp': 'ERP-ն հասանելի չէ, փորձեք մի փոքր ուշ',
    'same_person': 'Սա վարորդի PIN-ն է։ Առաքիչը պետք է մուտքագրի իր PIN-ը։',
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
    """Размер тела, токен терминала, сессия водителя; установка APK доступна до регистрации."""
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        return error(413, 'too_large')
    try:
        request.max_content_length = MAX_BODY_BYTES   # Flask ≥ 3.1: предел и для тела без Content-Length
    except AttributeError:
        pass
    if request.endpoint == 'courier_api.download_apk':
        return None
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
            st.store.touch_terminal(terminal.id)   # best-effort: занятая база не роняет запрос (лог)
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


def _day_allowed(d: date) -> bool:
    """/day терминала — только сегодня ± DAY_WINDOW (Ереван); демо-дата — при COURIER_DEMO=1."""
    if state().demo and d == DEMO_DAY:
        return True
    return abs((d - clock.today()).days) <= DAY_WINDOW


def _login_lock(terminal_id: int) -> threading.Lock:
    with _login_locks_guard:
        lock = _login_locks.get(terminal_id)
        if lock is None:
            lock = _login_locks[terminal_id] = threading.Lock()
        return lock


def _car_name(code: str) -> str:
    st = state()
    if st.demo and code == 'TEST':
        return 'Թեստ'
    if st.cars_loader is None:
        return ''
    try:
        cars = st.refs.get(('cars', clock.today()), lambda: st.cars_loader(clock.today()))
    except Exception:   # название — подпись в ответе: любой сбой списка машин (ERP, «Маршруты») не роняет вход
        logger.warning('[Courier] Название машины %s не прочитано', code, exc_info=True)
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
    """PIN водителя → сессия до 04:00 следующего дня (не дольше 20 ч). Блокировка — по терминалу: 5 неверных PIN
    в окне 15 минут → 429 на 15 минут (за туннелем у всех терминалов один IP). PIN неверного формата не считается
    попыткой. Против гонки: вход терминала — по одному (параллельный → 429 с коротким retry_after), а попытка
    резервируется в базе атомарно ДО проверки PIN — проверок PIN не больше 5 на окно, сколько бы запросов ни пришло.
    PIN не узнан, а у кого-то из водителей PIN с перцем, которого нет в среде (security), — 403 `pin` с текстом
    «неверный PIN или PIN задать заново в офисе» (опечатку и такой PIN не различить); попытка в счёт, как любая
    неверная."""
    st, t = state(), g.courier_terminal
    body = _json_body()
    now = clock.now()
    driver, failure = _pin_driver(t, body.get('pin') if body else None)
    if driver is None:
        return failure
    expires = clock.session_expiry(now)
    token = st.store.open_session(t.id, driver.id, expires, t.car_code)
    if token is None:   # офис сменил машину или отозвал терминал после проверки токена — вход заново узнает новое
        current = st.store.terminal(t.id)
        if current is None or current.revoked:
            return error(401, 'unauthorized')
        return error(401, 'session', 'Մեքենան փոխվել է․ մուտք գործեք PIN-ով նորից')
    return jsonify({'session': token, 'expires_at': clock.iso(expires),
                    'driver': {'id': driver.id, 'name': driver.name},
                    'car': {'code': t.car_code, 'name': _car_name(t.car_code)},
                    'helper': None, 'planned': _planned(t.car_code)})   # v1.4 §8: экипаж новой сессии не решён


def _pin_driver(t: Terminal, pin: Any) -> tuple[Driver | None, Any]:
    """PIN на терминале — одна проверка для /login и /crew (тот же бюджет попыток и блокировка терминала):
    (водитель, None) или (None, ответ-ошибка). Терминал заблокирован — 429; PIN неверного формата — 400, не попытка;
    проверка на терминале — по одной (параллельная → 429); попытка резервируется в базе до проверки, верный PIN
    снимает резерв; PinReset — 403 «неверный PIN или задать заново»; один PIN у нескольких активных — 403."""
    st = state()
    if t.locked_until and t.locked_until > clock.iso(clock.now()):
        return None, _locked(t.locked_until)
    if not valid_pin(pin):
        return None, error(400, 'bad_request', 'PIN-ը 4–6 թվանշան է')
    lock = _login_lock(t.id)
    if not lock.acquire(blocking=False):
        return None, error(429, 'locked', 'Մուտքն արդեն ստուգվում է, փորձեք մի քանի վայրկյանից',
                           retry_after=LOGIN_BUSY_RETRY)
    try:
        attempt = st.store.pin_attempt(t.id)
        if isinstance(attempt, str):
            return None, _locked(attempt)
        try:
            drivers, failure = st.store.match_pin(pin), MSG['pin']
        except PinReset:
            drivers, failure = [], MSG['pin_reset']
        if not drivers:
            return None, _locked(attempt.locked_until) if attempt.locked_until else error(403, 'pin', failure)
        st.store.pin_release(t.id, attempt)   # PIN верный — попытка не в счёт
    finally:
        lock.release()
    if len(drivers) > 1:
        logger.warning('[Courier] Один PIN у нескольких активных водителей: %s', [d.id for d in drivers])
        return None, error(403, 'pin', 'Այս PIN-ը կրկնվում է․ դիմեք ադմինիստրատորին')
    return drivers[0], None


def _locked(until: str) -> Any:
    left = clock.parse_moment(until)
    retry = max(1, int((left - clock.now()).total_seconds())) if left else 900
    return error(429, 'locked', f'Չափազանց շատ սխալ փորձ։ Փորձեք {max(1, (retry + 59) // 60)} րոպեից',
                 retry_after=retry)


# --- экипаж: второй человек в машине (v1.4 §8) ---

def _planned(car_code: str) -> dict[str, str | None] | None:
    """Экипаж машины по плану «Развоза» на сегодня; «Маршрутов» нет или сбой — None. План дня кэшируется на
    PLAN_TTL секунд (терминалы зовут /status часто), сбой — только на PLAN_FAIL_TTL: «Маршруты» поднимутся — план
    вернётся скоро."""
    st, day = state(), clock.today().isoformat()
    hit = st.crew_plan.get(day)
    if hit is None or time.monotonic() - hit[0] >= (PLAN_TTL if hit[1] is not None else PLAN_FAIL_TTL):
        hit = (time.monotonic(), planned_crew(current_app.extensions.get('route_optimizer'), clock.today()))
        st.crew_plan.clear()                 # только сегодняшний день
        st.crew_plan[day] = hit
    return None if hit[1] is None else hit[1].get(car_code, {'driver': None, 'helper': None})


def _crew(t: Terminal, s: Session, helper: Driver | None) -> dict[str, Any]:
    """Crew: водитель сессии, առաքիչ, решён ли экипаж этой сессии (последнее решение в crew_log после входа — не
    'revoked'), revoked — кого снял офис (последнее решение сессии — 'revoked': терминал сбрасывает своего помощника и
    спрашивает экипаж заново), план."""
    since = clock.parse_moment(s.created_at)
    last = state().store.crew_last(t.id, s.driver_id, clock.utc_key(since) if since else '')
    revoked = last is not None and last['kind'] == 'revoked'
    return {'driver': {'id': s.driver_id, 'name': s.driver_name},
            'helper': {'id': helper.id, 'name': helper.name} if helper and not revoked else None,
            'decided': last is not None and not revoked,
            'revoked': {'id': last['helper_id'], 'name': last['helper_name'], 'at': last['at_utc']} if revoked else None,
            'planned': _planned(t.car_code)}


def _session_helper(s: Session) -> Driver | None:
    """Առաքիչ сессии; выключенный — не он (save_driver снимает его с сессий; здесь — на случай гонки)."""
    helper = state().store.driver(s.helper_id) if s.helper_id is not None else None
    return helper if helper is not None and helper.active else None


@bp.get('/crew')
@_api
def get_crew() -> Any:
    return jsonify(_crew(g.courier_terminal, g.courier_session, _session_helper(g.courier_session)))


@bp.post('/crew')
@_api
def post_crew() -> Any:
    """{"helper_pin": "1234"} — առաքիչ подтверждает себя своим PIN на терминале водителя (проверка как у /login,
    _pin_driver: тот же бюджет попыток и блокировка терминала); PIN самого водителя — 409 same_person.
    {"alone": true} — водитель один (снять помощника). Сессия водителя не меняется: sessions.helper_id и строка
    crew_log. Ответ — Crew."""
    st, t, s = state(), g.courier_terminal, g.courier_session
    body = _json_body() or {}
    alone, pin = body.get('alone'), body.get('helper_pin')
    if not (alone is None or alone is True) or (alone is True) == (pin is not None):
        return error(400, 'bad_request', 'Սպասվում է {"helper_pin": "..."} կամ {"alone": true}')
    helper = None
    if pin is not None:
        helper, failure = _pin_driver(t, pin)
        if helper is None:
            return failure
        if helper.id == s.driver_id:
            return error(409, 'same_person')
    digest = token_hash(request.headers.get('X-Courier-Session', '').strip())
    if not st.store.set_crew(digest, t.id, t.car_code, s.driver_id, helper):
        if st.store.session(digest, t.id) is None:   # сессию закрыли (выход, новый вход, водитель выключен)
            return error(401, 'session')
        return error(403, 'pin')                     # помощника выключили или сменили PIN, пока проверялся PIN
    return jsonify(_crew(t, s, helper))


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
    if not _day_allowed(d):
        return error(400, 'bad_request', 'Հասանելի են միայն երեկը, այսօրը և վաղը')
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
    count, total = st.store.photo_usage(t.id)
    if count >= PHOTOS_PER_DAY:
        return _photo_limit('count')
    if total >= PHOTO_BYTES_PER_DAY:
        return _photo_limit('bytes')
    upload = request.files.get('file')
    if upload is None:
        return error(400, 'bad_request', 'file՝ պարտադիր է')
    data = upload.stream.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        return error(413, 'too_large', 'Ֆայլը 2 ՄԲ-ից մեծ է')
    if not data or image_ext(data) is None:
        return error(400, 'bad_request', 'Միայն JPEG կամ PNG')
    try:
        saved = save_photo(st.store, photo_id.lower(), event_id.lower(), kind, data, t.id)
    except PhotoLimit as e:
        return _photo_limit(e.kind)
    return jsonify({'ok': True, 'duplicate': True} if not saved else {'ok': True})


def _photo_limit(kind: str) -> Any:
    """Предел фото терминала за день (контракт §5 п. 10): штук — 429 с retry_after до полуночи, байт — 413."""
    if kind == 'bytes':
        return error(413, 'too_large', f'Այսօրվա լուսանկարների ծավալը սպառված է ({PHOTO_BYTES_PER_DAY // 1048576} ՄԲ)')
    now = clock.now()
    midnight = now.replace(hour=0, minute=0, second=0) + timedelta(days=1)
    return error(429, 'too_large', f'Այսօրվա լուսանկարների քանակը սպառված է ({PHOTOS_PER_DAY})',
                 retry_after=max(1, int((midnight - now).total_seconds())))


@bp.get('/status')
@_api
def status() -> Any:
    """Деньги водителя за день (по его событиям payment) и отметка кассира; отклонённые события; экипаж сессии
    (crew, v1.4 §8). Деньги — только водителя сессии, не помощника."""
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
                                        for r in st.store.rejected_for(s.driver_id, day)],
                    'crew': _crew(g.courier_terminal, s, _session_helper(s))})


@bp.get('/app-version')
@_api
def app_version() -> Any:
    rel = state().store.latest_release()
    if rel is None:
        return error(404, 'not_found', 'APK-ն դեռ բեռնված չէ')
    return jsonify({'version_code': rel.version_code, 'version_name': rel.version_name, 'sha256': rel.sha256,
                    'size': rel.size, 'url': '/api/courier/v1/app/apk'})


@bp.get('/download/apk', endpoint='download_apk')
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

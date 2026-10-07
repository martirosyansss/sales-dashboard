# -*- coding: utf-8 -*-
"""Страница офиса «Առաքիչ» (/courier) и её API (/api/courier/admin/*) — только для администратора.

Доступ обеспечивает глобальный before_request дашборда (как у «Маршрутов»): аноним — вход, роль user — 403.
POST — только JSON (или multipart с X-Requested-With для APK): форма с чужого сайта их не отправит.
ERP только читается (справочники и /day — через кэш); всё, что вводит офис, пишется в courier.db.
Прошлые даты — только из сохранённых снимков /day (ERP и план «Развоза» за прошлое не перечитываются).
Статус, сумма по правилу и оплачено по точке («Առաքում այսօր», «Գումար»), накладные заказа («Մակնշում») — по одному
правилу с терминалом (контракт §5 п. 12, merge.merge) для каждой машины даты (day_model): точки и связи `replaces` —
только из снимков /day этой даты; заказы O:, из которых сделана накладная S:, показываются у неё; заказ, разделённый
на несколько накладных, — `split_order`; несовместимые записи заказа и накладной — `merge_conflict`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import wraps
from typing import Any, Callable, Iterable, Mapping

from flask import Blueprint, Response, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException

from route_optimizer.erp import ErpError
from route_optimizer.store import StoreError as RoutesStoreError

from . import clock, events as ev, merge as mg, tare as tr
from .facts import gps_summary, office_window, refuel_flags
from .routes_link import RoutesView, driver_name_hints, planned_crew, routes_depot, routes_view
from .state import state
from .store import MarkSetting, PinConflict, PinPepperMissing, PinUnverifiable, Release, StoreError, TareOpening, Terminal

logger = logging.getLogger(__name__)

bp = Blueprint('courier', __name__)

APK_MAX_BYTES = 200 * 1024 * 1024
ROUTES_DOWN = '«Առաքում» պլանը հասանելի չէ'   # база «Маршрутов» не читается (№80)
PACK_QTY_MAX = 10000
PHOTO_REQUIRED = ('return', 'unreadable')   # + delivery «частично»/«отказ» (№18)
MARKS_LIMIT = 20000
INVOICE_FIND_DAYS = 31                      # «Ապրանքագիր»: дат в поиске (правило дня строится по каждой)
INVOICE_FIND_ROWS = 500                     # и точек
STOP_ID_MAX = 80                            # S:/O: + uuid — с запасом
CASH_COLLECT = ('cash', 'cash_ecr')         # деньги берёт водитель — «Գումար» ждёт оплату
STATUSES = ('pending', 'in_progress', 'full', 'partial', 'refused', 'covered')   # контракт §5 п. 12
PEPPER_MISSING_HY = 'Չի հաջողվում ստուգել PIN-ի կրկնությունը. վերականգնեք COURIER_PIN_PEPPER-ը'


def _api(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except HTTPException:   # 413/400 Flask (тело больше предела, битый multipart) — как есть
            raise
        except ErpError:
            logger.exception('[Courier] ERP недоступна (%s)', request.path)
            return jsonify({'success': False, 'error': 'ERP-ն հասանելի չէ'}), 503
        except StoreError as e:
            logger.exception('[Courier] База courier.db (%s)', request.path)
            return jsonify({'success': False, 'error': str(e)}), 500
        except Exception:
            logger.exception('[Courier] Внутренняя ошибка (%s)', request.path)
            return jsonify({'success': False, 'error': 'Սերվերի ներքին սխալ'}), 500
    return wrapper


def _bad(text: str, status: int = 400) -> Any:
    return jsonify({'success': False, 'error': text}), status


def _body() -> Mapping[str, Any] | None:
    if not request.is_json:
        return None
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _user() -> str | None:
    return session.get('username')


def _day() -> date | None:
    raw = request.args.get('date')
    return clock.today() if not raw else clock.parse_day(raw)


@bp.after_request
def _no_store(resp: Response) -> Response:
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.get('/courier')
def page() -> str:
    return render_template('courier.html')


@bp.get('/courier/money')
def money_page() -> str:
    """«Վարորդների գումարը» — касса отдельной страницей (была вкладка «Գումար» на /courier)."""
    return render_template('courier_money.html')


@bp.get('/courier/invoice')
def invoice_page() -> str:
    """«Ապրանքագիր» — одна накладная: деньги, доставка, сканы маркировки, фото и подпись (API /api/courier/admin/invoice)."""
    return render_template('courier_invoice.html')


@bp.get('/courier/tare')
def tare_page() -> str:
    """«Տարա» — баланс тары магазинов (№87 п. 9; API /api/courier/admin/tare*)."""
    return render_template('courier_tare.html')


# --- «Վարորդներ»: водители и терминалы ---

def _cars(today: date) -> tuple[list[dict[str, Any]], bool]:
    """Машины для терминала (erp_day.merge_cars: ERP CARS, накладные за 90 дней, парк «Маршрутов»); закрытая в ERP —
    только если она возила накладные за эти 90 дней (docs; карточку закрыли, а машина работает — 06.10.2026: 2660062)
    или к ней уже привязан действующий терминал. (машины, ERP недоступна). Тот же список — проверка машины
    при регистрации и смене машины (_car_listed) и название машины в /login (api._car_name)."""
    st = state()
    cars: list[dict[str, Any]] = []
    failed = False
    if st.cars_loader is not None:
        try:
            cars = list(st.refs.get(('cars', today), lambda: st.cars_loader(today)))
        except ErpError:
            logger.warning('[Courier] Машины ERP не прочитаны', exc_info=True)
            failed = True
    if any(c.get('closed') for c in cars):
        bound = {t.car_code for t in st.store.list_terminals() if not t.revoked}
        cars = [c for c in cars if not c.get('closed') or c.get('docs') or c['code'] in bound]
    if st.demo:
        cars.append({'code': 'TEST', 'name': 'Թեստ (COURIER_DEMO)', 'docs': 0, 'last': None, 'fleet': False,
                     'closed': False, 'capacity_kg': None})
    return cars, failed


def _car_listed(car: Any) -> bool:
    """Машина из списка _cars; список пуст (ERP недоступна) — любая строка (форму проверяет store)."""
    cars, _ = _cars(clock.today())
    return isinstance(car, str) and (not cars or car in {c['code'] for c in cars})


@bp.get('/api/courier/admin/drivers')
@_api
def drivers_list() -> Any:
    st = state()
    cars, failed = _cars(clock.today())
    return jsonify({'success': True,
                    'drivers': [{'id': d.id, 'name': d.name, 'active': d.active, 'has_pin': d.has_pin,
                                 'pin_reset': d.pin_reset} for d in st.store.list_drivers()],
                    'terminals': [{'id': t.id, 'name': t.name, 'car_code': t.car_code, 'created_at': t.created_at,
                                   'revoked_at': t.revoked_at, 'last_seen_at': t.last_seen_at,
                                   'locked_until': t.locked_until} for t in st.store.list_terminals()],
                    'cars': cars, 'cars_erp_failed': failed,
                    # №84: имена водителей «Развоза» — подсказка имени (с ними сравнивается вход по PIN)
                    'name_hints': driver_name_hints(_routes_state()),
                    'public_url': st.public_url, 'lan_url': request.host_url.rstrip('/') + '/api/courier/v1'})


@bp.post('/api/courier/admin/drivers')
@_api
def drivers_save() -> Any:
    body = _body()
    if body is None:
        return _bad('Սպասվում է JSON')
    did, pin = body.get('id'), body.get('pin')
    if did is not None and (isinstance(did, bool) or not isinstance(did, int)):
        return _bad('Սխալ վարորդ')
    if pin in ('', None):
        pin = None
    elif not isinstance(pin, str):
        return _bad('PIN-ը 4–6 թվանշան է')
    reset = body.get('reset_unverifiable', False)   # офис явно сбрасывает PIN водителей, чей перец потерян
    if not isinstance(reset, bool):
        return _bad('Սխալ հարցում')
    try:
        new_id = state().store.save_driver(did, body.get('name'), body.get('active', True) is not False, pin, _user(),
                                           reset_unverifiable=reset)
    except PinPepperMissing as e:   # PIN других водителей не проверить: восстановить перец или сбросить их PIN
        return jsonify({'success': False, 'error': PEPPER_MISSING_HY,
                        'unverifiable': [{'id': i, 'name': n} for i, n in e.drivers]}), 409
    except PinConflict:
        return _bad('Այս PIN-ն արդեն ունի մեկ այլ վարորդ — ընտրեք ուրիշը')
    except PinUnverifiable:
        return _bad('Չհաջողվեց ստուգել, որ PIN-ը չի կրկնվում․ միացնելիս նշեք նոր PIN')
    except LookupError:
        return _bad('Վարորդը չի գտնվել', 404)
    except ValueError as e:
        return _bad(_hy(str(e)))
    return jsonify({'success': True, 'id': new_id})


def _hy(text: str) -> str:
    """Тексты проверок store (по-русски) → армянский; незнакомое — как есть."""
    return {
        'Имя водителя: пусто': 'Գրեք վարորդի անունը',
        'Имя терминала: пусто': 'Գրեք տերմինալի անունը',
        'PIN — 4–6 цифр': 'PIN-ը 4–6 թվանշան է',
        'Машина не выбрана': 'Ընտրեք մեքենան',
        'Название тары: пусто': 'Գրեք տարայի անունը',
        'Текст причины: пусто': 'Գրեք պատճառը',
    }.get(text, text)


def qr_svg(text: str) -> str | None:
    """QR (SVG) на сервере — segno (чистый Python); библиотеки нет — None (страница покажет текст)."""
    try:
        import segno
    except ImportError:
        logger.warning('[Courier] segno не установлен — QR не построен (pip install -r requirements.txt)')
        return None
    buf = io.BytesIO()
    # viewBox масштабирует рисунок вместе с рамкой SVG; четыре модуля — белое поле QR.
    segno.make(text, error='m').save(buf, kind='svg', scale=6, border=4, omitsize=True, xmldecl=False, svgns=True,
                                     dark='#000', light='#fff')
    return buf.getvalue().decode('utf-8')


@bp.post('/api/courier/admin/terminals')
@_api
def terminals_create() -> Any:
    """Новый терминал: токен и QR показываются ОДИН раз (в базе — только sha256)."""
    st = state()
    body = _body()
    if body is None:
        return _bad('Սպասվում է JSON')
    car = body.get('car_code')
    if not _car_listed(car):
        return _bad('Ընտրեք մեքենան ցուցակից')
    admin_pin = _admin_pin()
    try:
        terminal, token = st.store.create_terminal(body.get('name'), car, _user(), admin_pin)
    except ValueError as e:
        return _bad(_hy(str(e)))
    return _qr_reply(terminal, token, admin_pin, body.get('url'))


def _admin_pin() -> str:
    """PIN скрытых настроек терминала (§5 п. 11): свой у каждого терминала и у каждого его QR."""
    return f'{secrets.randbelow(10 ** 6):06d}'


def _qr_reply(terminal: Terminal, token: str, admin_pin: str, url: Any) -> Any:
    """Ответ с QR регистрации (новый терминал и «Նոր QR»): url 'public' (по умолчанию) — туннель, иначе адрес этого
    сервера."""
    base = state().public_url if url in (None, 'public') else request.host_url.rstrip('/') + '/api/courier/v1'
    qr_text = json.dumps({'araqich': 1, 'url': base, 'token': token, 'terminal': terminal.name, 'admin_pin': admin_pin},
                         ensure_ascii=False, separators=(',', ':'))
    return jsonify({'success': True, 'terminal': {'id': terminal.id, 'name': terminal.name, 'car_code': terminal.car_code},
                    'qr_text': qr_text, 'qr_svg': qr_svg(qr_text), 'admin_pin': admin_pin})


@bp.post('/api/courier/admin/terminals/<int:terminal_id>/reissue')
@_api
def terminals_reissue(terminal_id: int) -> Any:
    """«Նոր QR»: устройство потеряло регистрацию — новый QR тому же терминалу (имя, машина и история прежние); прежний
    QR и сессия водителя сразу недействительны. Отозванный терминал — нет (вместо него создают новый)."""
    body = _body()
    if body is None:
        return _bad('Սպասվում է JSON')
    st = state()
    t = st.store.terminal(terminal_id)
    if t is None:
        return _bad('Տերմինալը չի գտնվել', 404)
    admin_pin = _admin_pin()
    issued = None if t.revoked else st.store.reissue_terminal(terminal_id, admin_pin, _user())
    if issued is None:   # отозван (и между чтением и записью)
        return _bad('Տերմինալն անջատված է․ ստեղծեք նոր տերմինալ')
    logger.info('[Courier] Терминал %s «%s» (%s): новый QR, выдал %s', t.id, t.name, t.car_code, _user())
    return _qr_reply(issued[0], issued[1], admin_pin, body.get('url'))


@bp.post('/api/courier/admin/terminals/<int:terminal_id>/car')
@_api
def terminals_car(terminal_id: int) -> Any:
    """«Փոխել մեքենան»: действующий терминал — на другую машину списка (_car_listed). Прошлое остаётся у прежней машины:
    события, снимки /day, экипаж и треки хранят машину на момент записи. Сессия водителя закрывается — вход по PIN
    сообщит терминалу новую машину; /day терминала с этого момента — день новой машины."""
    body = _body()
    if body is None:
        return _bad('Սպասվում է JSON')
    st = state()
    car = body.get('car_code')
    t = st.store.terminal(terminal_id)
    if t is None:
        return _bad('Տերմինալը չի գտնվել', 404)
    if t.revoked:
        return _bad('Տերմինալն անջատված է')
    if isinstance(car, str) and car.strip() == t.car_code:
        return _bad('Տերմինալն արդեն այս մեքենայի վրա է')
    if not _car_listed(car):
        return _bad('Ընտրեք մեքենան ցուցակից')
    try:
        changed = st.store.set_terminal_car(terminal_id, car, _user())
    except ValueError as e:
        return _bad(_hy(str(e)))
    if not changed:   # отозван между чтением и записью
        return _bad('Տերմինալն անջատված է')
    logger.info('[Courier] Терминал %s «%s»: машина %s → %s, сменил %s', t.id, t.name, t.car_code, car, _user())
    return jsonify({'success': True, 'terminal': {'id': t.id, 'name': t.name, 'car_code': car.strip()}})


@bp.post('/api/courier/admin/terminals/<int:terminal_id>/revoke')
@_api
def terminals_revoke(terminal_id: int) -> Any:
    if _body() is None:
        return _bad('Սպասվում է JSON')
    if not state().store.revoke_terminal(terminal_id, _user()):
        return _bad('Տերմինալը չի գտնվել կամ արդեն անջատված է', 404)
    return jsonify({'success': True})


# --- «Առաքում այսօր» ---

@dataclass
class DayModel:
    """Дата для офиса — как её видит терминал каждой машины (контракт §5 п. 12: известные точки (машина, дата)):
    - известные точки — точки всех снимков /day ЭТОЙ даты (M2: снимок другой даты, например накладная на остаток
      заказа завтра, день не меняет); точка — у одной машины: где она в последнем снимке (у нескольких — самый новый
      снимок), иначе где она была в самом новом снимке. Текущая — есть в последнем снимке своей машины;
    - data — версия точки для показа и правила: текущей — из последнего снимка, прочей — самая новая за дату;
      строки, цены и collect правила — версии, по которой записана действующая доставка точки (snapshot_id
      события: версия /day, которую видел водитель, `day_version` §5 п. 16, иначе действующая при получении), если
      она этой даты; иначе и replaces — из data;
    - события — все принятые события даты; в правило — события известных точек (любого терминала).
    views / absorbed_by / groups / paid_to — ответ правила по всем машинам даты (stop_id не повторяются между
    машинами); collect — collect точки, по которому считало правило."""
    current: dict[str, list[dict[str, Any]]]          # машина → точки последнего снимка (её точки, по seq)
    data: dict[str, dict[str, Any]]                   # точка → версия для показа (car_code — машина точки)
    versions: dict[str, dict[int, dict[str, Any]]]    # точка → снимок даты → версия
    inputs: list[dict[str, Any]]                      # события даты в форме правила (at — datetime с зоной)
    views: dict[str, mg.StopView]
    absorbed_by: dict[str, str]
    groups: dict[str, tuple[str, ...]]
    paid_to: dict[str, str]                           # covered-сестра → владелец: её оплаты у владельца (v1.2.3)
    collect: dict[str, Any]                           # точка → collect правила

    def office(self, stop_id: str | None) -> str | None:
        """Точка, у которой показывается событие точки stop_id (поглощённая — у владельца)."""
        return self.absorbed_by.get(stop_id or '', stop_id)

    def payee(self, stop_id: str | None) -> str | None:
        """Точка, в paid которой правило считает оплату точки stop_id: поглощённой и covered-сестры — владелец."""
        sid = self.office(stop_id)
        return self.paid_to.get(sid or '', sid)


def _moment(e: Mapping[str, Any]) -> datetime | None:
    """Момент события для порядка правила (at, затем id): at_utc сервера, иначе at терминала."""
    try:
        return datetime.fromisoformat(e['at_utc'])
    except (TypeError, ValueError):
        return clock.parse_moment(e.get('at'))


def _rule_lines(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{'line_id': ln.get('line_id'), 'product_id': ln.get('product_id'), 'qty': ln.get('qty'),
             'price': ln.get('price')} for ln in data.get('lines') or () if isinstance(ln, dict)]


def day_model(ds: str, events: list[dict[str, Any]]) -> DayModel:
    """Точки и события даты → правило §5 п. 12 по каждой машине (см. DayModel)."""
    snaps = state().store.day_snapshots(ds)
    versions: dict[str, dict[int, dict[str, Any]]] = {}
    latest_stops: dict[str, list[dict[str, Any]]] = {car: [] for car in snaps.latest}
    for sid, car, data in snaps.stops:
        versions.setdefault(data['stop_id'], {})[sid] = data
        if sid == snaps.latest[car]:
            latest_stops[car].append(data)
    in_latest: dict[str, list[str]] = {}
    for car, stops in latest_stops.items():
        for d in stops:
            in_latest.setdefault(d['stop_id'], []).append(car)
    home: dict[str, str] = {}
    data: dict[str, dict[str, Any]] = {}
    for x, vs in versions.items():
        cars = in_latest.get(x)
        if cars:
            car = max(cars, key=lambda c: snaps.latest[c])
            data[x] = vs[snaps.latest[car]]
        else:
            data[x] = vs[max(vs)]
        home[x] = data[x]['car_code']
    current = {car: [d for d in stops if home[d['stop_id']] == car] for car, stops in latest_stops.items()}

    inputs = []
    for e in events:
        at = _moment(e)
        if at is None:
            logger.warning('[Courier] Событие %s без момента — вне правила дня', e['id'])
            continue
        inputs.append({'id': e['id'], 'type': e['type'], 'stop_id': e['stop_id'], 'at': at, 'payload': e['payload'],
                       'snapshot_id': e.get('snapshot_id')})
    known = [e for e in inputs if e['stop_id'] in data]
    stated = mg.latest_by_stop(known, 'delivery')   # строки правила — версии действующей доставки точки
    by_car: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    collect: dict[str, Any] = {}
    for x, d in data.items():
        stmt = stated.get(x)
        basis = (versions[x].get(stmt['snapshot_id']) if stmt is not None else None) or d   # версия заявления
        source = 'order' if x.startswith('O:') else 'invoice'
        collect[x] = basis.get('collect')
        stop = {'stop_id': x, 'source': source, 'collect': collect[x], 'current': x in in_latest,
                'lines': _rule_lines(basis)}
        if source == 'invoice':
            stop['replaces'] = [o for o in d.get('replaces') or () if isinstance(o, str)]
        by_car.setdefault(home[x], ([], []))[0].append(stop)
    for e in known:
        by_car[home[e['stop_id']]][1].append(e)
    views: dict[str, mg.StopView] = {}
    absorbed: dict[str, str] = {}
    groups: dict[str, tuple[str, ...]] = {}
    paid_to: dict[str, str] = {}
    for car in sorted(by_car):
        result = mg.merge(*by_car[car])
        views.update(result.stops)
        absorbed.update(result.absorbed_by)
        groups.update(result.groups)
        paid_to.update(result.paid_to)
    return DayModel(current, data, versions, inputs, views, absorbed, groups, paid_to, collect)


def _money(d: Decimal) -> float:
    """Деньги правила (точный Decimal) → JSON: 2 знака, половина — от нуля."""
    return float(d.quantize(mg.CENT, rounding=ROUND_HALF_UP))


def _event_version(model: DayModel, extra: Mapping[str, list[dict[str, Any]]],
                   e: Mapping[str, Any]) -> dict[str, Any] | None:
    """Версия точки события: снимок, по которому событие проверено (snapshot_id), иначе самая новая известная."""
    vs = model.versions.get(e['stop_id'] or '') or {v['snapshot_id']: v['data'] for v in extra.get(e['stop_id'] or '', [])}
    if not vs:
        return None
    return vs.get(e.get('snapshot_id')) or vs[max(vs)]


def _needs_photo(e: Mapping[str, Any], version: Mapping[str, Any] | None) -> bool:
    """Фото обязательно (№18): return, unreadable и доставка «частично» или «отказ» — по строкам её же версии точки
    (точка неизвестна — проверить нечем, фото нужно)."""
    if e['type'] in PHOTO_REQUIRED:
        return True
    if e['type'] != 'delivery':
        return False
    if version is None:
        return True
    delivered = {str(i.get('line_id')): i.get('qty') or 0 for i in e['payload'].get('lines') or [] if isinstance(i, dict)}
    lines = {str(ln.get('line_id')): ln.get('qty') or 0 for ln in version.get('lines') or [] if isinstance(ln, dict)}
    return mg.statement_status(delivered, lines) in ('partial', 'refused')


def _tare(model: DayModel) -> dict[str, list[dict[str, Any]]]:
    """Действующая тара показываемой точки (§5 п. 17, как на терминале): по каждой точке зоны владельца — самой
    точке и поглощённым ею — последняя отметка tare (без вытесненных `supersedes`), их количества — сумма по tare_id;
    у сестры — только своя. Количество — точная сумма (Decimal), в JSON — число."""
    total: dict[str, dict[str, Decimal]] = {}
    for x, e in sorted(mg.latest_by_stop(model.inputs, 'tare').items()):
        sid = model.office(x)
        if sid is None:
            continue
        acc = total.setdefault(sid, {})
        for i in e['payload'].get('items') or []:
            if not isinstance(i, dict) or not isinstance(i.get('tare_id'), str):
                continue
            q = i.get('qty')   # проверено при приёме (events._tare): число ≥ 0
            if isinstance(q, (int, float)) and not isinstance(q, bool):
                acc[i['tare_id']] = acc.get(i['tare_id'], Decimal(0)) + Decimal(str(q))
    return {sid: [{'tare_id': t, 'qty': float(q)} for t, q in sorted(acc.items())] for sid, acc in total.items()}


def _stop_row(stop: Mapping[str, Any], v: mg.StopView, tare: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {'stop_id': stop.get('stop_id'), 'seq': stop.get('seq'), 'doc_number': stop.get('doc_number'),
            'customer': (stop.get('customer') or {}).get('name'), 'collect': stop.get('collect'),
            'amount_due': stop.get('amount_due'), 'status': v.status, 'due': _money(v.due), 'paid': _money(v.paid),
            'removed': v.removed, 'flags': list(v.flags), 'tare': tare}


def day_overview(day: date, load: bool = True) -> dict[str, Any]:
    """Сводка дня по машинам: точки из выдачи /day (load — терминалам с машиной загружается из ERP; для прошлых
    дат вызывающий передаёт False — только сохранённые снимки), статус, due и оплачено точки по правилу §5 п. 12,
    «հանված» точки со своими событиями (removed), действующая тара, события с флагами и фото. Счётчики событий
    (водители, связь, флаги) — по машине терминала. Экипаж (v1.4 §8): водители — из событий и решений экипажа
    (crew_log) дня, помощники (helpers) — подтверждённые в событиях и crew_log; helper_until — помощник → момент, когда
    офис его снял (последнее решение по нему — 'revoked'); helper_info — [{name, since, until}] в порядке первого
    подтверждения за день (офис — отдельная «персона» на каждого); alone — экипаж решался, а помощника не было весь день
    (старый APK экипаж не сообщает — ни helpers, ни alone)."""
    st = state()
    ds = day.isoformat()
    terminals = st.store.list_terminals()
    errors: dict[str, str] = {}
    if load:
        for car in sorted({t.car_code for t in terminals if not t.revoked}):
            try:
                st.days.get(car, day)
            except ErpError:
                errors[car] = 'ERP-ն հասանելի չէ'
            except RoutesStoreError:   # №80: план дня не прочитан — /day не собран, терминал остаётся с прежним днём
                logger.warning('[Courier] /day %s: база «Маршрутов» недоступна', car, exc_info=True)
                errors[car] = ROUTES_DOWN
    events = st.store.events_for_day(ds)
    model = day_model(ds, events)
    other = sorted({e['stop_id'] for e in events if e['stop_id'] and e['stop_id'] not in model.data})
    extra = st.store.stop_versions(other) if other else {}
    photos = st.store.photos_for_events([e['id'] for e in events])
    tare = _tare(model)
    by_car: dict[str, dict[str, Any]] = {}

    def car_row(code: str) -> dict[str, Any]:
        return by_car.setdefault(code, {'car_code': code, 'stops': [], 'removed': [], 'total': 0,
                                        **{x: 0 for x in STATUSES}, 'unreadable': 0, 'foreign': 0, 'flagged': 0,
                                        'drivers': set(), 'helpers': set(), 'helper_until': {}, 'crew_known': False,
                                        'last_contact': None, 'error': errors.get(code), 'gps': None, 'refuels': []})

    refuels = st.store.refuels()
    rflags = refuel_flags(refuels, *office_window(day))   # odometer_suspicious — пересчитан вокруг дня, не сохранённый
    for e in events:
        if e['type'] == 'refuel':
            e['flags'] = rflags.get(e['id'], e['flags'])
    for code in errors:
        car_row(code)
    for code, stops in model.current.items():
        for s in stops:
            v = model.views[s['stop_id']]
            row = car_row(code)
            row['total'] += 1
            row[v.status] += 1
            row['stops'].append(_stop_row(s, v, tare.get(s['stop_id'])))
    for sid, v in model.views.items():
        if v.removed:
            car_row(model.data[sid]['car_code'])['removed'].append(_stop_row(model.data[sid], v, tare.get(sid)))
    for e in events:
        row = car_row(e['car_code'])
        row['drivers'].add(e['driver_name'] or f'#{e["driver_id"]}')
        if e['helper_id'] is not None:
            row['helpers'].add(e['helper_name'] or f'#{e["helper_id"]}')
            row['crew_known'] = True
        if row['last_contact'] is None or e['received_at'] > row['last_contact']:
            row['last_contact'] = e['received_at']
        if _needs_photo(e, _event_version(model, extra, e)) and not photos.get(e['id']):
            e['flags'] = [*e['flags'], 'no_photo']
        if e['type'] == 'unreadable':
            row['unreadable'] += 1
        if 'foreign' in e['flags']:
            row['foreign'] += 1
        if e['flags']:
            row['flagged'] += 1
    # трек и заправки (контракт v1.3 §7): км движения по GPS за рабочий день (стоянки у точек дня и склада — 0 км);
    # заправки дня исходной заправки (исправление — у неё, а не в день исправления), и вытесненные (superseded)
    # машины трека — по точкам дня (точки события после смены машины — у машины своего момента), и по событиям track
    tracked = {e['car_code'] for e in events if e['type'] == 'track'} | set(st.store.track_cars(ds))
    depot = routes_depot(_routes_state()) if tracked else None
    for code in sorted(tracked):
        car_row(code)['gps'] = gps_summary(st.store.track(code, ds), model.current.get(code, []), depot)
    shown = sorted((r for r in refuels if r['eff_date'] == ds), key=lambda r: (r['car_code'], r['eff_at_utc'], r['at_utc']))
    rphotos = st.store.photos_for_events([r['id'] for r in shown])
    for r in shown:
        p = r['payload']
        car_row(r['car_code'])['refuels'].append({
            'id': r['id'], 'at': r['eff_at'], 'sent_at': r['at'], 'driver_name': r['driver_name'],
            'liters': p.get('liters'), 'odometer_km': p.get('odometer_km'), 'full_tank': p.get('full_tank'),
            'amount_amd': p.get('amount_amd'), 'flags': rflags[r['id']], 'superseded': r['superseded'],
            'photos': rphotos.get(r['id'], [])})
    last_kind: dict[tuple[str, str], tuple[str, str]] = {}   # (машина, помощник) → последнее (helper|revoked, момент)
    first_seen: dict[tuple[str, str], str] = {}               # (машина, помощник) → первое подтверждение за день
    for c in st.store.crew_for_day(ds):   # решения экипажа: водитель вошёл и решил, даже если событий ещё нет
        row = car_row(c['car_code'])
        row['drivers'].add(c['driver_name'] or f'#{c["driver_id"]}')
        row['crew_known'] = True
        if c['kind'] in ('helper', 'revoked'):
            name = c['helper_name'] or f'#{c["helper_id"]}'
            if c['kind'] == 'helper':
                row['helpers'].add(name)
                first_seen.setdefault((c['car_code'], name), c['at_utc'])
            last_kind[(c['car_code'], name)] = (c['kind'], c['at_utc'])
    for (code, name), (kind, at_utc) in last_kind.items():   # офис снял помощника — «до ЧЧ:ММ»
        if kind == 'revoked' and name in by_car[code]['helpers']:
            by_car[code]['helper_until'][name] = clock.iso(datetime.fromisoformat(at_utc))
    car_since = st.store.car_since()
    for t in terminals:
        # связь действующего терминала — у машины, на которой он сейчас: только в своей дате и не раньше, чем его
        # поставили на эту машину (раньше — связь прежней машины), как live.py
        seen = t.last_seen_at
        if not t.revoked and t.car_code in by_car and seen and seen[:10] == ds and seen >= car_since.get(t.id, '') and (
                by_car[t.car_code]['last_contact'] is None or seen > by_car[t.car_code]['last_contact']):
            by_car[t.car_code]['last_contact'] = seen
    cars = []
    for code in sorted(by_car):
        row = by_car[code]
        row['drivers'] = sorted(row['drivers'])
        row['helpers'] = sorted(row['helpers'])
        row['alone'] = row.pop('crew_known') and not row['helpers']
        # по помощнику — с какого момента (первое подтверждение дня) и до какого (снят офисом); в порядке появления,
        # помощники только из событий (без решения экипажа этого дня) — в конце
        since = {n: clock.iso(datetime.fromisoformat(first_seen[(code, n)])) for n in row['helpers']
                 if (code, n) in first_seen}
        row['helper_info'] = sorted(({'name': n, 'since': since.get(n), 'until': row['helper_until'].get(n)}
                                     for n in row['helpers']),
                                    key=lambda h: (h['since'] is None, h['since'] or '', h['name']))
        cars.append(row)

    def brief(e: Mapping[str, Any]) -> dict[str, Any]:
        sid = model.office(e['stop_id'])
        s = model.data.get(sid or '') or (extra.get(sid or '') or [{}])[-1].get('data') or {}
        return {'id': e['id'], 'car_code': e['car_code'], 'driver_name': e['driver_name'],
                'helper_name': e['helper_name'], 'type': e['type'],
                'stop_id': sid, 'orig_stop_id': e['stop_id'], 'doc_number': s.get('doc_number'),
                'customer': (s.get('customer') or {}).get('name'), 'at': e['at'], 'flags': e['flags'],
                'photos': photos.get(e['id'], [])}

    flagged = [brief(e) for e in events if e['flags']]
    with_photos = [brief(e) for e in events if photos.get(e['id'])]
    return {'date': ds, 'cars': cars, 'flagged': flagged, 'photo_events': with_photos,
            'rejected': st.store.rejected_for_day(ds)}


def plan_mismatches(day: date) -> dict[str, Any]:
    """Сверка с планом «Развоза» (только чтение). Прошлые даты (Ереван) не сравниваются: ERP и route_optimizer за
    прошлое не читаются (`past`).
    - items — накладные ERP с машиной, которая не та, что в плане (точка уйдёт машине накладной), и накладные без
      машины у клиента в рейсах нескольких машин (ничьи — routes_link.invoice_owner; erp_car None);
    - no_car — накладных без машины у клиентов одной машины плана (машину берёт план, erp_day.SQL_PLAN_SALES);
    - coverage — по машине плана с выдачей /day на дату: клиентов в плане и сколько из них дошло до терминала
      (последний снимок /day, пустой — 0); машины без снимка (терминала нет / не запрашивал) не показываются;
    - released — план выпущен на терминалы (утверждён хотя бы раз, №80); до этого терминалы точек плана не получают —
      сверка накладных с планом есть, а coverage пуст (недошедшие точки — не сбой, а ожидание утверждения)."""
    st = state()
    if day < clock.today():
        return {'plan_exists': None, 'items': [], 'past': True}
    view = routes_view(_routes_state(), day)
    if not view.plan_exists or st.invoice_loader is None:
        return {'plan_exists': view.plan_exists, 'items': []}
    invoices, names = st.invoice_loader(day)
    plan = view.plan_trucks()
    items = []
    no_car = 0
    for inv in sorted(invoices, key=lambda x: (x.customer_id, x.doc_number)):
        trucks = plan.get(inv.customer_id)
        if not trucks:
            continue
        if not inv.car_code and len(trucks) == 1:
            no_car += 1
        elif inv.car_code not in trucks:
            code, name = names.get(inv.customer_id, (str(inv.customer_id), ''))
            items.append({'doc_number': inv.doc_number, 'customer_code': code, 'customer_name': name,
                          'erp_car': inv.car_code or None, 'plan_cars': sorted(trucks)})
    return {'plan_exists': True, 'released': view.released, 'items': items, 'no_car': no_car,
            'coverage': _plan_coverage(day, view) if view.released else []}


def _crew_name(name: str) -> str:
    """Имя для сравнения плана с фактом: без регистра и лишних пробелов."""
    return ' '.join(name.split()).casefold()


def crew_mismatches(day: date, cars: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """«План ≠ факт» по экипажу (v1.4 §8): для машин дня (day_overview) — водитель и առաքիչ плана «Развоза»
    (routes_link.planned_crew) против фактических. Сравниваются только известные стороны: водитель — если в плане есть
    водитель, а у машины есть водители дня (ни один не он); առաքիչ — если в плане есть առաքիչ, а экипаж на терминале
    решался (fact пусто — «մենակ»). planned — экипаж плана машин дня (офис показывает помощника, которого нет в плане,
    как сведение, а не предупреждение). Плана нет (раздела нет, сбой) — available False."""
    plan = planned_crew(_routes_state(), day)
    if plan is None:
        return {'available': False, 'items': []}
    items = []
    planned = {}
    for row in cars:
        p = plan.get(row['car_code'])
        if not p:
            continue
        planned[row['car_code']] = p
        if p['driver'] and row['drivers'] and _crew_name(p['driver']) not in {_crew_name(x) for x in row['drivers']}:
            items.append({'car_code': row['car_code'], 'role': 'driver', 'planned': p['driver'], 'fact': row['drivers']})
        if p['helper'] and (row['helpers'] or row['alone']) \
                and _crew_name(p['helper']) not in {_crew_name(x) for x in row['helpers']}:
            items.append({'car_code': row['car_code'], 'role': 'helper', 'planned': p['helper'], 'fact': row['helpers']})
    return {'available': True, 'items': items, 'planned': planned}


def _plan_coverage(day: date, view: RoutesView) -> list[dict[str, Any]]:
    """Машина плана → клиентов в плане и дошедших до терминала (последний снимок /day машины на дату)."""
    store = state().store
    ds = day.isoformat()
    has_snapshot = store.day_snapshots(ds).latest     # и пустой снимок: машина запросила день и получила 0 точек
    got: dict[str, set[int]] = {}
    for s in store.day_stops(ds):
        cid = (s.get('customer') or {}).get('id')
        if cid is not None:
            got.setdefault(s['car_code'], set()).add(int(cid))
    out = []
    for car in sorted({truck for truck, _ in view.trips}):
        if car not in has_snapshot:
            continue
        planned = set(view.car_customers(car))
        out.append({'car_code': car, 'plan': len(planned), 'terminal': len(planned & got.get(car, set()))})
    return out


def _routes_state() -> Any:
    from flask import current_app
    return current_app.extensions.get('route_optimizer')


@bp.get('/api/courier/admin/today')
@_api
def today_view() -> Any:
    d = _day()
    if d is None:
        return _bad('Սխալ ամսաթիվ')
    body = day_overview(d, load=d >= clock.today())   # прошлые даты — из сохранённых снимков, без ERP
    body['crew_mismatch'] = crew_mismatches(d, body['cars'])
    try:
        body['mismatch'] = plan_mismatches(d)
    except ErpError:
        logger.warning('[Courier] Сравнение с планом «Развоза» не выполнено', exc_info=True)
        body['mismatch'] = {'plan_exists': None, 'items': [], 'error': 'ERP-ն հասանելի չէ'}
    except RoutesStoreError:
        logger.warning('[Courier] Сравнение с планом «Развоза» не выполнено: база «Маршрутов» недоступна', exc_info=True)
        body['mismatch'] = {'plan_exists': None, 'items': [], 'error': ROUTES_DOWN}
    return jsonify({'success': True, **body})


# --- «Գումար»: деньги водителей ---

def money_view(day: date) -> dict[str, Any]:
    """«Գումար» за дату: money_drivers по событиям и правилу дня."""
    ds = day.isoformat()
    events = state().store.events_for_day(ds)
    return {'date': ds, 'drivers': money_drivers(ds, events, day_model(ds, events))}


def money_drivers(ds: str, events: list[dict[str, Any]], model: DayModel) -> list[dict[str, Any]]:
    """«Գումար» по водителю: строка — точка, как её показывает правило §5 п. 12 (оплаты поглощённых заказов и
    covered-сестёр разделённого заказа — у владельца, v1.2.3): статус, `due` (сколько стоит по правилу), сколько надо
    взять (`expected`), сколько взял по накладной он сам (`invoice`) и все водители (`invoice_all` = paid правила), в
    счёт долга, чеки ՀԴՄ (без отменённых). collect — тот, по которому считало правило (версия заявления, §5 п. 16).
    Инварианты:
    - `expected` — только cash / cash_ecr и один раз за день: у водителя «заявления» точки (StopView.statement —
      доставка, от которой статус и due); full / partial / refused — due; in_progress — due на сейчас (статус
      «Ընթացքում»); covered — 0 (товар отдан по заказу); pending — ничего; конфликт — due заявления накладной и флаг
      `merge_conflict` (проверить в офисе);
    - сверка `short` — с оплатами ВСЕХ водителей; взял другой водитель — `collected_by_other`, а не `no_payment`;
    - `no_payment` — надо взять, а оплаты по накладной нет ни у кого.
    Оплата по точке, которой нет в снимках даты, — строка без ожидания. Итог водителя: `expected` — сумма его
    `expected`, `expected_short` — сумма его `short`; «сдал фактически». `known` — точка есть в снимках даты (её
    карточка «Ապրանքագիր» открывается)."""
    st = state()
    hand = st.store.handovers(ds)
    names = {d.id: d.name for d in st.store.list_drivers()}
    by_id = {e['id']: e for e in events}
    cancelled = {e['payload'].get('cancel_of') for e in events
                 if e['type'] == 'payment' and e['payload'].get('cancel_of')}   # id отменённых платежей
    rows: dict[int, dict[str, list[dict[str, Any]]]] = {}   # водитель → точка офиса → его платежи
    payments: dict[int, list[dict[str, Any]]] = {}
    by_stop: dict[str, list[dict[str, Any]]] = {}            # точка офиса → платежи всех водителей
    for e in events:
        if e['type'] == 'payment':
            sid = model.payee(e['stop_id']) or ''
            rows.setdefault(e['driver_id'], {}).setdefault(sid, []).append(e)
            payments.setdefault(e['driver_id'], []).append(e)
            by_stop.setdefault(sid, []).append(e)
    for sid, v in model.views.items():   # надо взять, а оплат нет — строка у водителя заявления («վճարում չկա»)
        author = by_id.get(v.statement or '')
        if author is not None and model.collect.get(sid) in CASH_COLLECT:
            rows.setdefault(author['driver_id'], {}).setdefault(sid, [])
    other = sorted({sid for drv in rows.values() for sid in drv if sid and sid not in model.data})
    extra = st.store.stop_versions(other) if other else {}

    def order(sid: str) -> tuple[Any, ...]:
        s = model.data.get(sid)
        if s is None:
            return 2, '', 0, sid
        v = model.views.get(sid)
        return int(v is None or v.removed), s['car_code'], s.get('seq') or 0, sid

    out = []
    for did in sorted(set(rows) | set(hand), key=lambda i: (names.get(i) or '', i)):
        lines = []
        expected_total = short_total = 0.0
        for sid, mine in sorted(rows.get(did, {}).items(), key=lambda kv: order(kv[0])):
            s = model.data.get(sid) or (extra.get(sid) or [{}])[-1].get('data') or {}
            v = model.views.get(sid)
            collect = model.collect.get(sid, s.get('collect'))
            money = ev.payments_total(mine)
            receipts = sorted({str(e['payload'].get('ecr_receipt')) for e in mine
                               if e['payload'].get('ecr_receipt') and not e['payload'].get('cancel_of')
                               and e['id'] not in cancelled})
            flags = {f for e in mine for f in e['flags']}
            expected = due = None
            if v is None:   # точки нет в снимках даты — правило её не видит
                status, removed, paid_all = None, False, ev.payments_total(by_stop.get(sid, []))['invoice']
            else:
                status, removed, paid_all = v.status, v.removed, _money(v.paid)
                flags |= set(v.flags)
                author = (by_id.get(v.statement or '') or {}).get('driver_id')
                if v.statement is not None or v.status == 'covered':
                    due = _money(v.due)
                if collect in CASH_COLLECT:
                    if v.status == 'covered':
                        expected = 0.0
                    elif v.statement is not None and author == did:
                        expected = due
            short = None if expected is None else round(expected - paid_all, 2)
            if expected and expected > 0:
                if paid_all <= 0:
                    flags.add('no_payment')
                elif money['invoice'] < paid_all - ev.EPS:
                    flags.add('collected_by_other')
            expected_total += expected or 0.0
            short_total += short or 0.0
            lines.append({'stop_id': sid, 'doc_number': s.get('doc_number'), 'car_code': s.get('car_code'),
                          'customer': (s.get('customer') or {}).get('name'), 'collect': collect,
                          'status': status, 'removed': removed, 'due': due, 'invoice_amount': s.get('amount_due'),
                          'expected': expected, 'short': short, 'invoice': money['invoice'], 'invoice_all': paid_all,
                          'debt': money['debt'], 'receipts': receipts, 'flags': sorted(flags),
                          'known': sid in model.data})
        total = ev.payments_total(payments.get(did, []))
        h = hand.get(did)
        collected = round(total['invoice'] + total['debt'], 2)
        out.append({'driver_id': did, 'name': names.get(did) or f'#{did}', 'rows': lines,
                    'expected': round(expected_total, 2), 'expected_short': round(short_total, 2),
                    'no_payment': sum(1 for r in lines if 'no_payment' in r['flags']),
                    'collected_invoice': total['invoice'], 'collected_debt': total['debt'], 'collected': collected,
                    'handed': h['handed'] if h else None, 'handed_at': h['handed_at'] if h else None,
                    'handed_by': h['handed_by'] if h else None, 'comment': h['comment'] if h else None,
                    'diff': round(h['handed'] - collected, 2) if h else None})
    return out


@bp.get('/api/courier/admin/money')
@_api
def money() -> Any:
    d = _day()
    if d is None:
        return _bad('Սխալ ամսաթիվ')
    return jsonify({'success': True, **money_view(d)})


@bp.post('/api/courier/admin/money/handover')
@_api
def money_handover() -> Any:
    body = _body()
    if body is None:
        return _bad('Սպասվում է JSON')
    d = clock.parse_day(body.get('date'))
    did, handed = body.get('driver_id'), body.get('handed')
    if d is None or isinstance(did, bool) or not isinstance(did, int) or state().store.driver(did) is None:
        return _bad('Սխալ ամսաթիվ կամ վարորդ')
    if handed is not None and (isinstance(handed, bool) or not isinstance(handed, (int, float))
                               or not math.isfinite(handed) or not 0 <= handed <= 1e9):
        return _bad('Գումարը պետք է լինի 0 կամ ավելի')
    comment = body.get('comment')
    if comment is not None and (not isinstance(comment, str) or len(comment) > 200):
        return _bad('Մեկնաբանությունը՝ մինչև 200 նիշ')
    state().store.save_handover(d.isoformat(), did, None if handed is None else float(handed), comment or None, _user())
    return jsonify({'success': True})


# --- «Տարա»: баланс тары магазинов (№87 п. 9, courier/tare.py) ---

TARE_CSV_COLUMNS = (
    ('code', 'Խանութի կոդ'), ('name', 'Խանութ'), ('agent', 'Մենեջեր'), ('car_code', 'Մեքենա'), ('tare', 'Տարա'),
    ('opening', 'Սկզբնական մնացորդ'), ('as_of', 'Սկզբնական մնացորդի ամսաթիվ'), ('went', 'Տարվել է'),
    ('returned', 'Հետ է վերցվել'), ('balance', 'Մնացորդ խանութում'),
)


def _tare_links() -> tuple[dict[int, dict[str, float]], dict[str, str], bool]:
    """Связи товар → тара ERP (tr.links_by_product), названия видов ERP ('erp:N' → название) и сбой ли ERP."""
    st = state()
    if st.tare_links_loader is None:
        return {}, {}, False
    try:
        links, names = st.tare_links_loader()
    except ErpError:
        logger.warning('[Courier] Связи тары ERP не прочитаны — частичные доставки по доле веса', exc_info=True)
        return {}, {}, True
    return tr.links_by_product(links), {f'erp:{k}': v for k, v in names.items()}, False


def tare_days() -> tuple[list[tr.DayTare], dict[str, str], bool]:
    """Движение тары по всем датам с доставками или отметками тары (tr.day_moves по правилу дня day_model и _tare),
    названия видов тары (из tare_expected, ERP, свои) и сбой ли ERP (связи тары). Дата пересчитывается, только если
    изменились её данные (store.tare_day_keys) или связи тары ERP; иначе — из кэша процесса (CourierState.tare_days)."""
    st = state()
    links, names, failed = _tare_links()
    links_key = repr(sorted((p, sorted(m.items())) for p, m in links.items()))
    keys = st.store.tare_day_keys()
    out = []
    for ds, key in keys.items():
        full = (key, links_key)
        hit = st.tare_days.get(ds)
        if hit is None or hit[0] != full:
            model = day_model(ds, st.store.events_for_day(ds, skip_track=True))
            marks = _tare(model)
            other = sorted(set(marks) - set(model.views))
            foreign = {x: vs[-1]['data'] for x, vs in st.store.stop_versions(other).items()} if other else {}
            hit = (full, tr.day_moves(ds, model, marks, links, foreign))
            st.tare_days[ds] = hit
        out.append(hit[1])
    for ds in set(st.tare_days) - set(keys):   # даты без данных (удалены) кэш не держит
        st.tare_days.pop(ds, None)
    kinds = {k: v for day in out for k, v in day.names.items()}
    kinds.update(names)
    kinds.update({f'custom:{t["id"]}': t['name'] for t in st.store.tare_custom(active_only=False)})
    return out, kinds, failed


def tare_view() -> dict[str, Any]:
    """«Տարա»: строки (магазин, вид тары) с балансом (tr.balances), виды тары, период данных терминалов."""
    days, kinds, failed = tare_days()
    rows = tr.balances([m for d in days for m in d.moves], state().store.tare_openings())
    used = {r['tare_id'] for r in rows}
    return {'rows': rows, 'kinds': [{'tare_id': t, 'name': kinds.get(t) or t} for t in sorted(kinds.keys() | used)],
            'first_day': days[0].day if days else None, 'last_day': days[-1].day if days else None,
            'lost': sum(d.lost for d in days), 'links_failed': failed}


@bp.get('/api/courier/admin/tare')
@_api
def tare_balance() -> Any:
    return jsonify({'success': True, **tare_view()})


@bp.get('/api/courier/admin/tare/history')
@_api
def tare_history() -> Any:
    raw = request.args.get('customer', '')
    if not raw.isdigit() or len(raw) > 12:
        return _bad('Սխալ խանութ')
    days, _, _ = tare_days()
    return jsonify({'success': True, **tr.history([m for d in days for m in d.moves], state().store.tare_openings(),
                                                   int(raw))})


def _known_customers() -> dict[int, tuple[str, str]]:
    """Магазины, которым можно вписать начальный остаток: из снимков /day и уже записанных остатков (id → код, имя)."""
    st = state()
    out = {cid: (code, name) for code, (cid, name) in st.store.snapshot_customers().items()}
    for o in st.store.tare_openings():
        out.setdefault(o['customer_id'], (o['code'], o['name']))
    return out


@bp.post('/api/courier/admin/tare/opening')
@_api
def tare_opening() -> Any:
    """Начальный остаток одной пары: {customer_id, tare_id, qty (null — убрать), as_of}."""
    body = _body()
    if body is None:
        return _bad('Սպասվում է JSON')
    cid, tare_id = body.get('customer_id'), body.get('tare_id')
    if isinstance(cid, bool) or not isinstance(cid, int):
        return _bad('Խանութը չի գտնվել')
    if not isinstance(tare_id, str) or not ev.TARE_RE.match(tare_id):
        return _bad('Տարայի այդպիսի տեսակ չկա')
    if body.get('qty') is None:
        state().store.save_tare_openings([TareOpening(cid, tare_id, None, None)], _user())
        return jsonify({'success': True})
    known = _known_customers()
    if cid not in known:
        return _bad('Խանութը չի գտնվել')
    _, kinds, _ = tare_days()
    if tare_id not in kinds:
        return _bad('Տարայի այդպիսի տեսակ չկա')
    qty, as_of = tr.parse_qty(body.get('qty')), tr.parse_day(body.get('as_of'), clock.today())
    if qty is None:
        return _bad(f'Քանակը՝ թիվ 0-ից {int(tr.QTY_MAX)}')
    if as_of is None:
        return _bad('Ամսաթիվը՝ ոչ ուշ քան այսօր')
    code, name = known[cid]
    state().store.save_tare_openings([TareOpening(cid, tare_id, qty, as_of, code, name)], _user())
    return jsonify({'success': True})


def _customers_by_code(codes: list[str]) -> dict[str, tuple[int, str]]:
    """Код магазина → (customer_id, название): снимки /day и записанные остатки, остальные коды — ERP (только чтение;
    ERP недоступна — такие коды не найдены)."""
    st = state()
    found = st.store.snapshot_customers()
    for o in st.store.tare_openings():
        found.setdefault(o['code'], (o['customer_id'], o['name']))
    out = {c: found[c] for c in codes if c in found}
    rest = [c for c in codes if c not in out]
    if rest and st.customer_code_loader is not None:
        try:
            out.update(st.customer_code_loader(rest))
        except ErpError:
            logger.warning('[Courier] Магазины ERP по коду не прочитаны', exc_info=True)
    return out


@bp.post('/api/courier/admin/tare/import')
@_api
def tare_import() -> Any:
    """Импорт начальных остатков: {rows: [{row, code, tare, qty, as_of}], apply}. Без apply — предпросмотр (что
    запишется, что заменится, ошибки); с apply — запись всех строк одной транзакцией, только если ошибок нет (иначе 400
    с ошибками и ничего не записано)."""
    body = _body()
    rows = body.get('rows') if body else None
    if not isinstance(rows, list) or not rows:
        return _bad('Ֆայլում տողեր չկան')
    if len(rows) > tr.IMPORT_ROWS_MAX:
        return _bad(f'Մեկ ֆայլում՝ մինչև {tr.IMPORT_ROWS_MAX} տող')
    _, kinds, _ = tare_days()
    items, errors = tr.check_import(rows, _customers_by_code, kinds, clock.today())
    old = {(o['customer_id'], o['tare_id']): o for o in state().store.tare_openings()}
    preview = []
    for x in items:
        was = old.get((x.customer_id, x.tare_id))
        preview.append({'customer_id': x.customer_id, 'code': x.code, 'name': x.name, 'tare_id': x.tare_id,
                        'tare_name': kinds.get(x.tare_id, x.tare_id), 'qty': x.qty, 'as_of': x.as_of,
                        'old': {'qty': was['qty'], 'as_of': was['as_of']} if was else None})
    applied = body.get('apply') is True
    if applied:
        if errors:
            return jsonify({'success': False, 'error': 'Ֆայլում սխալներ կան — ոչինչ չի գրանցվել', 'errors': errors}), 400
        state().store.save_tare_openings(items, _user())
        logger.info('[Courier] Начальные остатки тары: %d строк (%s)', len(items), _user())
    return jsonify({'success': True, 'rows': preview, 'errors': errors, 'applied': applied})


def tare_csv(rows: Iterable[Mapping[str, Any]], kinds: Mapping[str, str]) -> str:
    """CSV баланса тары для Excel (как marks_csv: UTF-8 с BOM, «;», защита от формул)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';', lineterminator='\r\n')
    w.writerow([title for _, title in TARE_CSV_COLUMNS])
    for r in rows:
        line = {**r, 'tare': kinds.get(r['tare_id']) or r['tare_id']}
        w.writerow(['' if line.get(k) is None else _csv_safe(str(line[k])) for k, _ in TARE_CSV_COLUMNS])
    return '\ufeff' + buf.getvalue()


@bp.get('/api/courier/admin/tare.csv')
@_api
def tare_export() -> Any:
    view = tare_view()
    text = tare_csv(view['rows'], {k['tare_id']: k['name'] for k in view['kinds']})
    name = f'tara_{clock.today().isoformat()}.csv'
    return Response(text.encode('utf-8'), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


# --- «Ապրանքագիր»: одна накладная — деньги, доставка, сканы, фото ---

def invoice_search(q: str, date_from: str | None, date_to: str | None) -> dict[str, Any]:
    """Поиск точек по номеру документа или клиенту (store.find_stops: INVOICE_FIND_DAYS самых новых дат, не больше
    INVOICE_FIND_ROWS точек) → строки «как показывает офис»: найденная поглощённая точка (заказ O:, прежняя
    накладная) — строкой своего владельца (`found_as` — номер найденной; совпал и сам владелец — None); одна точка
    даты — одна строка. Статус и оплачено — по правилу §5 п. 12 той же даты; точки без строки правила (убраны из
    /day до любых событий) — status None, removed. `more` — поиск обрезан по датам или строкам."""
    st = state()
    hits, more = st.store.find_stops(q, date_from, date_to, max_days=INVOICE_FIND_DAYS, max_rows=INVOICE_FIND_ROWS)
    models: dict[str, DayModel] = {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for ds, x in hits:
        if ds not in models:
            models[ds] = day_model(ds, st.store.events_for_day(ds, skip_track=True))
        model = models[ds]
        sid = model.office(x) or x
        if sid not in model.data:
            continue
        found = model.data[x].get('doc_number') if x != sid and x in model.data else None
        if (ds, sid) in rows:
            if found is None:   # совпала и сама точка — «найдено по» не нужно
                rows[(ds, sid)]['found_as'] = None
            continue
        s, v = model.data[sid], model.views.get(sid)
        rows[(ds, sid)] = {
            'date': ds, 'stop_id': sid, 'source': 'order' if sid.startswith('O:') else 'invoice',
            'doc_number': s.get('doc_number'), 'customer': (s.get('customer') or {}).get('name'),
            'customer_code': (s.get('customer') or {}).get('code'), 'car_code': s.get('car_code'),
            'collect': model.collect.get(sid), 'amount_due': s.get('amount_due'),
            'status': v.status if v else None, 'removed': v.removed if v else True,
            'paid': _money(v.paid) if v else None, 'found_as': found}
    return {'rows': list(rows.values()), 'more': more, 'days': INVOICE_FIND_DAYS, 'limit': INVOICE_FIND_ROWS}


def _assign_scanned(lines: list[dict[str, Any]], covered: Mapping[Any, float]) -> None:
    """Закрытое маркировкой по товару (сканы продажи + «не читается») → строкам маркируемого товара по порядку,
    каждой — до её нужного количества (доставлено, иначе по накладной); остаток — последней строке товара."""
    left = dict(covered)
    marked = [ln for ln in lines if ln['marked']]
    for i, ln in enumerate(marked):
        pid = ln['product_id']
        last = all(m['product_id'] != pid for m in marked[i + 1:])
        need = ln['delivered'] if ln['delivered'] is not None else ln['qty'] or 0
        take = left.get(pid, 0.0) if last else min(left.get(pid, 0.0), need)
        ln['scanned'] = round(take, 3)
        left[pid] = left.get(pid, 0.0) - take


def invoice_card(day: date, stop_id: str) -> dict[str, Any] | None:
    """Карточка точки офиса за дату (None — точки нет в снимках /day даты). stop_id поглощённой точки — карточка её
    владельца (`opened_as`). Всё — по правилу §5 п. 12, как «Առաքում այսօր» и «Գումար» (только чтение; события дня
    читаются один раз, модель дня — одна на всю карточку):
    - события точки — свои и поглощённых ею; оплаты covered-сестры засчитаны владельцу (paid_to) — у владельца видны и
      они; у самой сестры её оплаты показаны, а деньги (`money`) — пусто и `related.paid_to` → владелец;
    - `money` — строки money_drivers этой точки по водителям (тот же расчёт, что на /courier/money);
    - строки — версии действующего заявления, если оно записано на этой точке (как day_model: строки и цены
      заявления; `lines_changed` — версия для показа с тех пор изменилась), иначе — версия для показа; `delivered` —
      из этого заявления; заявления других точек (заказов, прежних накладных) — в `statements` целиком;
    - маркировка — как терминал (events._scan_shortfall): точка, поглощённые ею и заказы её `replaces`; `scanned`
      строки — сканы продажи (засчитанные, не отменённые) + «не читается» этого товара, по маркируемым строкам."""
    st = state()
    ds = day.isoformat()
    events = st.store.events_for_day(ds, skip_track=True)
    model = day_model(ds, events)
    sid = model.office(stop_id) or stop_id
    s = model.data.get(sid)
    if s is None:
        return None
    v = model.views.get(sid)
    payee = model.payee(sid) or sid
    by_id = {e['id']: e for e in events}

    def ours(e: Mapping[str, Any]) -> bool:
        if not e['stop_id']:
            return False
        return model.office(e['stop_id']) == sid or (e['type'] == 'payment' and model.payee(e['stop_id']) == sid)

    mine = [e for e in events if ours(e)]
    photos = st.store.photos_for_events([e['id'] for e in mine])
    refuse = {r['id']: r['text'] for r in st.store.reasons('refuse', active_only=False)}
    back = {r['id']: r['text'] for r in st.store.reasons('return', active_only=False)}
    doc_of = lambda x: (model.data.get(x or '') or {}).get('doc_number')   # noqa: E731
    statements = list(v.statements) if v else []

    def version_lines(e: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        ver = _event_version(model, {}, e) or {}
        return {str(ln.get('line_id')): ln for ln in ver.get('lines') or () if isinstance(ln, dict)}

    def delivered_of(e: Mapping[str, Any]) -> dict[str, float]:
        return {str(i.get('line_id')): float(i.get('qty') or 0) for i in e['payload'].get('lines') or []
                if isinstance(i, dict)}

    def shape(d: Mapping[str, Any]) -> list[tuple[Any, ...]]:
        return [(ln.get('line_id'), ln.get('qty'), ln.get('price')) for ln in d.get('lines') or ()
                if isinstance(ln, dict)]

    actual = by_id.get(v.statement) if v and v.statement else None
    here = delivered_of(actual) if actual is not None and actual['stop_id'] == sid else None
    basis = s
    if here is not None:   # версия заявления — как day_model (строки и цены, по которым правило считало due)
        basis = model.versions[sid].get(actual['snapshot_id']) or s
    lines = [{'line_id': ln.get('line_id'), 'product_id': ln.get('product_id'), 'code': ln.get('code'),
              'name': ln.get('name'), 'unit': ln.get('unit'), 'qty': ln.get('qty'), 'price': ln.get('price'),
              'sum': ln.get('sum'), 'marked': bool(ln.get('marked')),
              'delivered': None if here is None else here.get(str(ln.get('line_id')), 0.0), 'scanned': None}
             for ln in basis.get('lines') or () if isinstance(ln, dict)]
    names = {ln['product_id']: ln['name'] for ln in lines}

    absorbed = [x for x, o in sorted(model.absorbed_by.items()) if o == sid]
    zone = list(dict.fromkeys([sid, *absorbed, *(o for o in s.get('replaces') or () if isinstance(o, str))]))
    scans = st.store.search_scans('', ds, ds, limit=MARKS_LIMIT, stop_ids=zone)
    scans.sort(key=lambda r: (r['at'] or '', r['event_id']))
    covered: dict[Any, float] = {}
    for r in scans:
        if r['kind'] == 'sale' and r['counted'] and not r['cancelled']:
            covered[r['product_id']] = covered.get(r['product_id'], 0.0) + float(r['units'] or 0)
    unreadable = st.store.unreadable_by_product(ds, zone)
    for pid, q in unreadable.items():
        covered[pid] = covered.get(pid, 0.0) + q
    _assign_scanned(lines, covered)

    other_statements = []
    for eid in statements:
        e = by_id.get(eid)
        if e is None or e['stop_id'] == sid:
            continue
        vl, got = version_lines(e), delivered_of(e)
        other_statements.append({
            'event_id': eid, 'stop_id': e['stop_id'], 'doc_number': doc_of(e['stop_id']), 'at': e['at'],
            'source': 'order' if (e['stop_id'] or '').startswith('O:') else 'invoice',
            'driver_name': e['driver_name'],
            'lines': [{'code': ln.get('code'), 'name': ln.get('name'), 'unit': ln.get('unit'), 'qty': ln.get('qty'),
                       'delivered': got.get(lid, 0.0)} for lid, ln in vl.items()]})

    cancelled = {e['payload'].get('cancel_of') for e in events
                 if e['type'] == 'payment' and e['payload'].get('cancel_of')}
    payments = [{'id': e['id'], 'at': e['at'], 'driver_name': e['driver_name'], 'helper_name': e['helper_name'],
                 'amount': e['payload'].get('amount'), 'kind': e['payload'].get('kind'),
                 'receipt': e['payload'].get('ecr_receipt'), 'is_cancel': bool(e['payload'].get('cancel_of')),
                 'cancelled': e['id'] in cancelled, 'flags': e['flags'],
                 'doc_number': doc_of(e['stop_id']) if e['stop_id'] != sid else None}
                for e in mine if e['type'] == 'payment']
    money_rows = []
    if payee == sid:
        for drv in money_drivers(ds, events, model):
            for r in drv['rows']:
                if r['stop_id'] == sid:
                    money_rows.append({'driver_id': drv['driver_id'], 'name': drv['name'], **r})

    timeline = []
    for e in mine:
        p = e['payload']
        flags = list(e['flags'])
        if _needs_photo(e, _event_version(model, {}, e)) and not photos.get(e['id']):
            flags.append('no_photo')
        info: dict[str, Any] = {}
        if e['type'] == 'delivery':
            vl = version_lines(e)
            info = {'status': mg.statement_status(delivered_of(e), {lid: ln.get('qty') or 0 for lid, ln in vl.items()}),
                    'actual': e['id'] == (v.statement if v else None), 'reason': refuse.get(p.get('reason_id') or ''),
                    'comment': p.get('comment')}
        elif e['type'] == 'payment':
            info = {'amount': p.get('amount'), 'kind': p.get('kind'), 'receipt': p.get('ecr_receipt'),
                    'is_cancel': bool(p.get('cancel_of'))}
        elif e['type'] == 'scan':
            info = {'raw': p.get('raw'), 'kind': p.get('kind'), 'units': p.get('units')}
        elif e['type'] == 'return':
            info = {'product': names.get(p.get('product_id')) or p.get('product_id'), 'qty': p.get('qty'),
                    'reason': back.get(p.get('reason_id') or ''), 'comment': p.get('comment')}
        elif e['type'] in ('unreadable', 'tare'):
            info = {'comment': p.get('comment')}
        timeline.append({'id': e['id'], 'type': e['type'], 'at': e['at'], 'driver_name': e['driver_name'],
                         'helper_name': e['helper_name'], 'flags': flags, 'photos': photos.get(e['id'], []),
                         'doc_number': doc_of(e['stop_id']) if e['stop_id'] != sid else None, 'info': info})

    tare_names = {t.get('tare_id'): t.get('name') for t in s.get('tare_expected') or () if isinstance(t, dict)}
    tare_names.update({f'custom:{t["id"]}': t['name'] for t in st.store.tare_custom(active_only=False)})
    group = next((g for g in model.groups.values() if sid in g), (sid,))
    customer = s.get('customer') or {}
    return {
        'date': ds,
        'opened_as': doc_of(stop_id) if stop_id != sid else None,
        'stop': {'stop_id': sid, 'source': 'order' if sid.startswith('O:') else 'invoice',
                 'doc_number': s.get('doc_number'), 'seq': s.get('seq'), 'car_code': s.get('car_code'),
                 'current': any(d['stop_id'] == sid for d in model.current.get(s.get('car_code') or '', [])),
                 'customer': {k: customer.get(k) for k in ('name', 'code', 'tax_id', 'address', 'phone')},
                 'agent_name': s.get('agent_name'), 'collect': model.collect.get(sid, s.get('collect')),
                 'amount_due': s.get('amount_due'), 'weight_kg': s.get('weight_kg'),
                 'status': v.status if v else None, 'removed': v.removed if v else True,
                 'due': _money(v.due) if v else None, 'paid': _money(v.paid) if v else None,
                 'flags': list(v.flags) if v else [], 'lines_changed': shape(basis) != shape(s)},
        'related': {'split': [{'stop_id': m, 'doc_number': doc_of(m)} for m in group if m != sid],
                    'absorbed': [{'stop_id': x, 'doc_number': doc_of(x), 'source': 'order' if x.startswith('O:') else 'invoice'}
                                 for x in absorbed],
                    'paid_to': {'stop_id': payee, 'doc_number': doc_of(payee)} if payee != sid else None},
        'drivers': sorted({e['driver_name'] or f'#{e["driver_id"]}' for e in mine}),
        'helpers': sorted({e['helper_name'] or f'#{e["helper_id"]}' for e in mine if e['helper_id'] is not None}),
        'lines': lines,
        'statements': other_statements,
        'money': money_rows,
        'payments': payments,
        'unreadable': round(sum(unreadable.values()), 3),
        'scans': [{k: r[k] for k in ('event_id', 'at', 'raw', 'gtin', 'serial', 'product_name', 'units', 'kind',
                                     'is_group', 'driver_name', 'counted', 'duplicate_elsewhere', 'cancelled')}
                  for r in scans],
        'tare': {'expected': [{'tare_id': t.get('tare_id'), 'name': t.get('name'), 'qty': t.get('qty')}
                              for t in s.get('tare_expected') or () if isinstance(t, dict)],
                 'marked': [{**t, 'name': tare_names.get(t['tare_id'])} for t in _tare(model).get(sid, [])]},
        'timeline': timeline,
    }


@bp.get('/api/courier/admin/invoices')
@_api
def invoices_find() -> Any:
    args = _marks_args()
    if args is None:
        return _bad('Սխալ ամսաթիվ')
    q, f, t = args
    if not q.strip() and not (f and t):
        return _bad('Նշեք համարը, հաճախորդը կամ օրը')
    return jsonify({'success': True, **invoice_search(q, f, t)})


@bp.get('/api/courier/admin/invoice')
@_api
def invoice() -> Any:
    d = clock.parse_day(request.args.get('date') or '')
    stop_id = request.args.get('stop') or ''
    if d is None or not stop_id or len(stop_id) > STOP_ID_MAX:
        return _bad('Սխալ ամսաթիվ կամ կետ')
    card = invoice_card(d, stop_id)
    if card is None:
        return _bad('Այս օրը այդպիսի ապրանքագիր չկա', 404)
    return jsonify({'success': True, **card})


# --- «Մակնշում»: коды маркировки ---

MARK_COLUMNS = (
    ('raw', 'Կոդ'), ('gtin', 'GTIN'), ('serial', 'Սերիական համար'), ('product_code', 'Ապրանքի կոդ'),
    ('product_name', 'Ապրանք'), ('customer_code', 'Հաճախորդի կոդ'), ('customer_name', 'Հաճախորդ'),
    ('tax_id', 'ՀՎՀՀ'), ('doc_number', 'Ապրանքագիր'), ('order_number', 'Պատվեր'),
    ('old_invoice_number', 'Նախկին ապրանքագիր'), ('split', 'Բաժանված պատվեր'),
    ('date', 'Ամսաթիվ'), ('at', 'Ժամանակ'),
    ('driver_name', 'Վարորդ'), ('car_code', 'Մեքենա'), ('kind', 'Տեսակ'), ('is_group', 'Խմբային'),
    ('units', 'Հատ'), ('duplicate_elsewhere', 'Կրկնված այլ տեղ'), ('cancelled', 'Չեղարկված'),
)
KIND_HY = {'sale': 'վաճառք', 'return': 'վերադարձ'}


def _marks_args() -> tuple[str, str | None, str | None] | None:
    q = request.args.get('q', '')[:200]
    f, t = request.args.get('from') or None, request.args.get('to') or None
    if (f and clock.parse_day(f) is None) or (t and clock.parse_day(t) is None):
        return None
    return q, f, t


def _scan_rows(q: str, date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    """Сканы для «Մակնշում» — по правилу §5 п. 12 на снимках той же даты (day_model по каждой дате строк). Скан по
    точке, поглощённой другой, — у владельца: и по заказу O:, из которого сделана накладная, и по прежней накладной
    S:, вместо которой по тому же заказу выписана новая (пример 14). `doc_number` — номера ВСЕХ накладных владельца
    (`invoice_numbers`, через запятую), заказ разделён на несколько накладных — `split`; номер, под которым сделан
    скан, — в своей колонке: заказа — `order_number`, прежней накладной — `old_invoice_number`. Точка не поглощена —
    номер как при скане (заказ без накладной — номер заказа в `doc_number`, накладных нет)."""
    st = state()
    rows = st.store.search_scans(q, date_from, date_to, limit=MARKS_LIMIT)
    models = {d: day_model(d, st.store.events_for_day(d)) for d in sorted({r['date'] for r in rows if r['stop_id']})}
    for r in rows:
        r['order_number'], r['old_invoice_number'], r['split'] = None, None, False
        r['invoice_numbers'] = [r['doc_number']] if (r['stop_id'] or '').startswith('S:') and r['doc_number'] else []
        model = models.get(r['date'])
        r['known'] = model is not None and (r['stop_id'] or '') in model.data
        owner = model.absorbed_by.get(r['stop_id']) if model is not None and r['stop_id'] else None
        if model is None or owner is None:
            continue
        members = model.groups.get(owner) or (owner,)
        r['order_number' if r['stop_id'].startswith('O:') else 'old_invoice_number'] = r['doc_number']
        r['stop_id'] = owner
        r['invoice_numbers'] = [model.data[m].get('doc_number') or m for m in members]
        r['doc_number'] = ', '.join(r['invoice_numbers'])
        r['split'] = len(members) > 1
    return rows


@bp.get('/api/courier/admin/marks')
@_api
def marks() -> Any:
    args = _marks_args()
    if args is None:
        return _bad('Սխալ ամսաթիվ')
    return jsonify({'success': True, 'rows': _scan_rows(*args), 'limit': MARKS_LIMIT})


def marks_csv(rows: Iterable[Mapping[str, Any]]) -> str:
    """CSV для Excel: UTF-8 с BOM, «;» — разделитель армянской/русской Windows-локали."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';', lineterminator='\r\n')
    w.writerow([title for _, title in MARK_COLUMNS])
    for r in rows:
        out = []
        for key, _ in MARK_COLUMNS:
            v = r.get(key)
            if key == 'kind':
                v = KIND_HY.get(v, v)
            elif isinstance(v, bool):
                v = 'այո' if v else ''
            elif key == 'raw' and isinstance(v, str):
                v = v.replace('\x1d', '<GS>')   # GS невидим в Excel — показываем явно
            out.append('' if v is None else _csv_safe(str(v)))
        w.writerow(out)
    return '﻿' + buf.getvalue()


def _csv_safe(v: str) -> str:
    """Защита от формул в Excel: значение, начинающееся с = + - @, — с апострофом (CSV injection)."""
    return "'" + v if v[:1] in ('=', '+', '-', '@', '\t', '\r') else v


@bp.get('/api/courier/admin/marks.csv')
@_api
def marks_export() -> Any:
    args = _marks_args()
    if args is None:
        return _bad('Սխալ ամսաթիվ')
    text = marks_csv(_scan_rows(*args))
    name = f'makanshum_{args[1] or "all"}_{args[2] or "all"}.csv'
    return Response(text.encode('utf-8'), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


# --- Фото и подписи терминала ---

@bp.get('/api/courier/admin/photos/<photo_id>')
@_api
def photo_file(photo_id: str) -> Any:
    """Фото/подпись по id (uuid) — только офису (admin-гейт дашборда). Путь из базы разрешается строго внутри
    папки фото: запись с чужим путём (../, абсолютный) — 404, а не файл."""
    if not ev.UUID_RE.match(photo_id):
        return _bad('Լուսանկարը չի գտնվել', 404)
    st = state()
    rec = st.store.photo(photo_id.lower())
    root = os.path.realpath(st.store.photos_dir)
    path = os.path.realpath(os.path.join(root, rec['path'])) if rec else ''
    try:
        inside = bool(path) and os.path.commonpath([root, path]) == root and path != root
    except ValueError:   # другой диск (Windows) — не внутри
        inside = False
    if not inside or not os.path.isfile(path):
        return _bad('Լուսանկարը չի գտնվել', 404)
    resp = send_file(path, mimetype='image/png' if path.lower().endswith('.png') else 'image/jpeg', max_age=0)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


# --- «Կարգավորումներ» ---

@bp.get('/api/courier/admin/settings')
@_api
def settings_get() -> Any:
    st = state()
    today = clock.today()
    products, erp_failed = [], False
    if st.catalog_loader is not None:
        try:
            catalog = st.refs.get(('catalog', today), lambda: st.catalog_loader(today))
        except ErpError:
            logger.warning('[Courier] Товары ERP не прочитаны', exc_info=True)
            catalog, erp_failed = [], True
        marks_ = st.store.mark_settings()
        for p in catalog:
            m = marks_.get(p.id)
            products.append({'id': p.id, 'code': p.code, 'name': p.name, 'unit': p.unit, 'closed': p.closed,
                             'container': p.is_container, 'sold_90': p.sold_qty_90 > 0,
                             'sold_qty_90': round(p.sold_qty_90, 2), 'marked_erp': p.markable_erp,
                             'pack_qty_erp': p.pack_qty_erp, 'saved': m is not None,
                             'marked': m.marked if m else p.markable_erp,
                             'pack_qty': m.pack_qty if m else p.pack_qty_erp})
    rel = st.store.latest_release()
    return jsonify({'success': True, 'products': products, 'products_erp_failed': erp_failed,
                    'tare_custom': st.store.tare_custom(active_only=False),
                    'reasons': {k: st.store.reasons(k, active_only=False) for k in ('refuse', 'return')},
                    'release': None if rel is None else {'version_code': rel.version_code,
                                                         'version_name': rel.version_name, 'size': rel.size,
                                                         'sha256': rel.sha256, 'uploaded_at': rel.uploaded_at},
                    'demo': st.demo})


@bp.post('/api/courier/admin/settings/products')
@_api
def settings_products() -> Any:
    body = _body()
    items = body.get('items') if body else None
    if not isinstance(items, list) or not items or len(items) > 2000:
        return _bad('Սպասվում է ցուցակ')
    out = []
    for it in items:
        if not isinstance(it, dict):
            return _bad('Սխալ տող')
        pid, marked, pack = it.get('product_id'), it.get('marked'), it.get('pack_qty')
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or not isinstance(marked, bool):
            return _bad('Սխալ ապրանք')
        if pack is not None and (isinstance(pack, bool) or not isinstance(pack, (int, float))
                                 or not math.isfinite(pack) or not 1 <= pack <= PACK_QTY_MAX):
            return _bad(f'Տուփում հատերի քանակը՝ 1-ից {PACK_QTY_MAX}')
        out.append(MarkSetting(pid, marked, None if pack is None else float(pack)))
    state().store.save_mark_settings(out, _user())
    state().days.invalidate()
    return jsonify({'success': True})


@bp.post('/api/courier/admin/settings/tare')
@_api
def settings_tare() -> Any:
    body = _body()
    if body is None:
        return _bad('Սպասվում է JSON')
    tid = body.get('id')
    if tid is not None and (isinstance(tid, bool) or not isinstance(tid, int)):
        return _bad('Սխալ տարա')
    try:
        new_id = state().store.save_tare_custom(tid, body.get('name'), body.get('active', True) is not False, _user())
    except LookupError:
        return _bad('Տարան չի գտնվել', 404)
    except ValueError as e:
        return _bad(_hy(str(e)))
    state().days.invalidate()
    return jsonify({'success': True, 'id': new_id})


@bp.post('/api/courier/admin/settings/reasons')
@_api
def settings_reasons() -> Any:
    body = _body()
    if body is None:
        return _bad('Սպասվում է JSON')
    rid = body.get('id')
    if rid in (None, ''):
        rid = 'r' + uuid.uuid4().hex[:8]
    try:
        state().store.save_reason(body.get('kind'), rid, body.get('text'), body.get('active', True) is not False)
    except ValueError as e:
        return _bad(_hy(str(e)))
    state().days.invalidate()
    return jsonify({'success': True, 'id': rid})


@bp.post('/api/courier/admin/apk')
@_api
def apk_upload() -> Any:
    """APK для обновления терминалов (GET /app-version): multipart, version_code растёт."""
    if request.headers.get('X-Requested-With') != 'fetch':
        return _bad('Սխալ հարցում', 415)
    st = state()
    upload = request.files.get('file')
    code_raw, name = request.form.get('version_code', ''), (request.form.get('version_name') or '').strip()
    if upload is None or not code_raw.isdigit() or not 0 < int(code_raw) < 2 ** 31 or not 0 < len(name) <= 40:
        return _bad('Ընտրեք APK ֆայլը, գրեք version_code (թիվ) և version_name')
    data = upload.stream.read(APK_MAX_BYTES + 1)
    if len(data) > APK_MAX_BYTES or not data.startswith(b'PK\x03\x04'):
        return _bad('Սա APK ֆայլ չէ (կամ 200 ՄԲ-ից մեծ է)')
    top = st.store.latest_release()
    if top is not None and int(code_raw) <= top.version_code:
        return _bad(f'version_code-ը պետք է մեծ լինի {top.version_code}-ից')
    os.makedirs(st.store.apk_dir, exist_ok=True)
    rel = f'araqich-{int(code_raw)}-{uuid.uuid4().hex[:8]}.apk'   # гонка двух загрузок не смешает файлы
    path = os.path.join(st.store.apk_dir, rel)
    tmp = f'{path}.{uuid.uuid4().hex}.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    try:
        st.store.save_release(Release(int(code_raw), name, hashlib.sha256(data).hexdigest(), len(data), rel,
                                      clock.iso(clock.now())), _user())
    except ValueError:   # параллельная загрузка успела записать версию не ниже
        return _bad('version_code-ը պետք է մեծ լինի նախորդից')
    return jsonify({'success': True})

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

from . import clock, events as ev, merge as mg
from .facts import gps_summary, office_window, refuel_flags
from .routes_link import RoutesView, planned_crew, routes_depot, routes_view
from .state import state
from .store import MarkSetting, PinConflict, PinPepperMissing, PinUnverifiable, Release, StoreError

logger = logging.getLogger(__name__)

bp = Blueprint('courier', __name__)

APK_MAX_BYTES = 200 * 1024 * 1024
PACK_QTY_MAX = 10000
PHOTO_REQUIRED = ('return', 'unreadable')   # + delivery «частично»/«отказ» (№18)
MARKS_LIMIT = 20000
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


# --- «Վարորդներ»: водители и терминалы ---

def _cars(today: date) -> tuple[list[dict[str, Any]], bool]:
    """Машины для терминала (SALES.fDELIVERYCAR за 90 дней); (машины, ERP недоступна)."""
    st = state()
    cars: list[dict[str, Any]] = []
    failed = False
    if st.cars_loader is not None:
        try:
            cars = list(st.refs.get(('cars', today), lambda: st.cars_loader(today)))
        except ErpError:
            logger.warning('[Courier] Машины ERP не прочитаны', exc_info=True)
            failed = True
    if st.demo:
        cars.append({'code': 'TEST', 'name': 'Թեստ (COURIER_DEMO)', 'docs': 0, 'last': None})
    return cars, failed


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
    cars, _ = _cars(clock.today())
    if not isinstance(car, str) or (cars and car not in {c['code'] for c in cars}):
        return _bad('Ընտրեք մեքենան ցուցակից')
    url = body.get('url')
    base = st.public_url if url in (None, 'public') else request.host_url.rstrip('/') + '/api/courier/v1'
    admin_pin = f'{secrets.randbelow(10 ** 6):06d}'   # PIN скрытых настроек терминала (§5 п. 11), свой у каждого
    try:
        terminal, token = st.store.create_terminal(body.get('name'), car, _user(), admin_pin)
    except ValueError as e:
        return _bad(_hy(str(e)))
    qr_text = json.dumps({'araqich': 1, 'url': base, 'token': token, 'terminal': terminal.name, 'admin_pin': admin_pin},
                         ensure_ascii=False, separators=(',', ':'))
    return jsonify({'success': True, 'terminal': {'id': terminal.id, 'name': terminal.name, 'car_code': terminal.car_code},
                    'qr_text': qr_text, 'qr_svg': qr_svg(qr_text), 'admin_pin': admin_pin})


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
    (crew_log) дня, помощники (helpers) — подтверждённые в событиях и crew_log; alone — экипаж решался, а помощника не
    было весь день (старый APK экипаж не сообщает — ни helpers, ни alone)."""
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
                                        'drivers': set(), 'helpers': set(), 'crew_known': False,
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
    depot = routes_depot(_routes_state()) if any(e['type'] == 'track' for e in events) else None
    for code in sorted({e['car_code'] for e in events if e['type'] == 'track'}):
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
    for c in st.store.crew_for_day(ds):   # решения экипажа: водитель вошёл и решил, даже если событий ещё нет
        row = car_row(c['car_code'])
        row['drivers'].add(c['driver_name'] or f'#{c["driver_id"]}')
        row['crew_known'] = True
        if c['kind'] == 'helper':
            row['helpers'].add(c['helper_name'] or f'#{c["helper_id"]}')
    for t in terminals:
        if t.car_code in by_car and t.last_seen_at and (by_car[t.car_code]['last_contact'] is None
                                                         or t.last_seen_at > by_car[t.car_code]['last_contact']):
            by_car[t.car_code]['last_contact'] = t.last_seen_at
    cars = []
    for code in sorted(by_car):
        row = by_car[code]
        row['drivers'] = sorted(row['drivers'])
        row['helpers'] = sorted(row['helpers'])
        row['alone'] = row.pop('crew_known') and not row['helpers']
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
      (последний снимок /day, пустой — 0); машины без снимка (терминала нет / не запрашивал) не показываются."""
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
    return {'plan_exists': True, 'items': items, 'no_car': no_car, 'coverage': _plan_coverage(day, view)}


def _crew_name(name: str) -> str:
    """Имя для сравнения плана с фактом: без регистра и лишних пробелов."""
    return ' '.join(name.split()).casefold()


def crew_mismatches(day: date, cars: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """«План ≠ факт» по экипажу (v1.4 §8): для машин дня (day_overview) — водитель и առաքիչ плана «Развоза»
    (routes_link.planned_crew) против фактических. Сравниваются только известные стороны: водитель — если в плане есть
    водитель, а у машины есть водители дня (ни один не он); առաքիչ — если в плане есть առաքիչ, а экипаж на терминале
    решался (fact пусто — «մենակ»). Плана нет (раздела нет, сбой) — available False."""
    plan = planned_crew(_routes_state(), day)
    if plan is None:
        return {'available': False, 'items': []}
    items = []
    for row in cars:
        p = plan.get(row['car_code'])
        if not p:
            continue
        if p['driver'] and row['drivers'] and _crew_name(p['driver']) not in {_crew_name(x) for x in row['drivers']}:
            items.append({'car_code': row['car_code'], 'role': 'driver', 'planned': p['driver'], 'fact': row['drivers']})
        if p['helper'] and (row['helpers'] or row['alone']) \
                and _crew_name(p['helper']) not in {_crew_name(x) for x in row['helpers']}:
            items.append({'car_code': row['car_code'], 'role': 'helper', 'planned': p['helper'], 'fact': row['helpers']})
    return {'available': True, 'items': items}


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
    return jsonify({'success': True, **body})


# --- «Գումար»: деньги водителей ---

def money_view(day: date) -> dict[str, Any]:
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
    `expected`, `expected_short` — сумма его `short`; «сдал фактически»."""
    st = state()
    ds = day.isoformat()
    events = st.store.events_for_day(ds)
    model = day_model(ds, events)
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
                          'debt': money['debt'], 'receipts': receipts, 'flags': sorted(flags)})
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
    return {'date': ds, 'drivers': out}


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

# -*- coding: utf-8 -*-
"""Страница офиса «Առաքիչ» (/courier) и её API (/api/courier/admin/*) — только для администратора.

Доступ обеспечивает глобальный before_request дашборда (как у «Маршрутов»): аноним — вход, роль user — 403.
POST — только JSON (или multipart с X-Requested-With для APK): форма с чужого сайта их не отправит.
ERP только читается (справочники и /day — через кэш); всё, что вводит офис, пишется в courier.db.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import uuid
from datetime import date
from functools import wraps
from typing import Any, Callable, Iterable, Mapping

from flask import Blueprint, Response, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from route_optimizer.erp import ErpError

from . import clock, events as ev
from .routes_link import routes_view
from .state import state
from .store import MarkSetting, PinConflict, Release, StoreError

logger = logging.getLogger(__name__)

bp = Blueprint('courier', __name__)

APK_MAX_BYTES = 200 * 1024 * 1024
PACK_QTY_MAX = 10000
PHOTO_REQUIRED = ('return', 'unreadable')   # + delivery «частично»/«отказ» (№18)
MARKS_LIMIT = 20000


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
                    'drivers': [{'id': d.id, 'name': d.name, 'active': d.active, 'has_pin': d.has_pin}
                                for d in st.store.list_drivers()],
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
    try:
        new_id = state().store.save_driver(did, body.get('name'), body.get('active', True) is not False, pin, _user())
    except PinConflict:
        return _bad('Այս PIN-ն արդեն ունի մեկ այլ վարորդ — ընտրեք ուրիշը')
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
    segno.make(text, error='m').save(buf, kind='svg', scale=6, border=2, xmldecl=False, svgns=True,
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
    try:
        terminal, token = st.store.create_terminal(body.get('name'), car, _user())
    except ValueError as e:
        return _bad(_hy(str(e)))
    qr_text = json.dumps({'araqich': 1, 'url': base, 'token': token, 'terminal': terminal.name},
                         ensure_ascii=False, separators=(',', ':'))
    return jsonify({'success': True, 'terminal': {'id': terminal.id, 'name': terminal.name, 'car_code': terminal.car_code},
                    'qr_text': qr_text, 'qr_svg': qr_svg(qr_text)})


@bp.post('/api/courier/admin/terminals/<int:terminal_id>/revoke')
@_api
def terminals_revoke(terminal_id: int) -> Any:
    if _body() is None:
        return _bad('Սպասվում է JSON')
    if not state().store.revoke_terminal(terminal_id, _user()):
        return _bad('Տերմինալը չի գտնվել կամ արդեն անջատված է', 404)
    return jsonify({'success': True})


# --- «Առաքում այսօր» ---

def _stop_status(stop: Mapping[str, Any] | None, delivery: Mapping[str, Any] | None) -> str:
    if delivery is None:
        return 'pending'
    delivered = {str(i.get('line_id')): float(i.get('qty') or 0) for i in delivery['payload'].get('lines') or []
                 if isinstance(i, dict)}
    invoice = {str(ln['line_id']): ln for ln in (stop or {}).get('lines') or []}
    return ev.delivery_status(delivered, invoice)


def _needs_photo(e: Mapping[str, Any], status: str | None) -> bool:
    return e['type'] in PHOTO_REQUIRED or (e['type'] == 'delivery' and status in ('partial', 'refuse'))


def day_overview(day: date, load: bool = True) -> dict[str, Any]:
    """Сводка дня по машинам: точки из выдачи /day (терминалам с машиной — загружается, если не была),
    события, флаги, расхождения плана «Развоза» с ERP."""
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
    stops = st.store.day_stops(ds)
    events = st.store.events_for_day(ds)
    photos = st.store.photos_for_events([e['id'] for e in events])
    by_car: dict[str, dict[str, Any]] = {}

    def car_row(code: str) -> dict[str, Any]:
        return by_car.setdefault(code, {'car_code': code, 'stops': [], 'total': 0, 'full': 0, 'partial': 0, 'refuse': 0,
                                        'pending': 0, 'unreadable': 0, 'foreign': 0, 'flagged': 0, 'drivers': set(),
                                        'last_contact': None, 'removed': [], 'error': errors.get(code)})

    for code in errors:
        car_row(code)
    deliveries = ev.effective(events, 'delivery')
    stop_ids = {(s['car_code'], s['stop_id']) for s in stops}
    stop_by_id = {s['stop_id']: s for s in stops}
    for s in stops:
        row = car_row(s['car_code'])
        status = _stop_status(s, deliveries.get(s['stop_id']))
        row['total'] += 1
        row[status] += 1
        row['stops'].append({'stop_id': s['stop_id'], 'seq': s.get('seq'), 'doc_number': s.get('doc_number'),
                             'customer': (s.get('customer') or {}).get('name'), 'collect': s.get('collect'),
                             'amount_due': s.get('amount_due'), 'status': status})
    for e in events:
        row = car_row(e['car_code'])
        row['drivers'].add(e['driver_name'] or f'#{e["driver_id"]}')
        if row['last_contact'] is None or e['received_at'] > row['last_contact']:
            row['last_contact'] = e['received_at']
        status = _stop_status(stop_by_id.get(e['stop_id']), e) if e['type'] == 'delivery' else None
        if _needs_photo(e, status) and not photos.get(e['id']):
            e['flags'] = [*e['flags'], 'no_photo']
        if e['type'] == 'unreadable':
            row['unreadable'] += 1
        if 'foreign' in e['flags']:
            row['foreign'] += 1
        if e['flags']:
            row['flagged'] += 1
        flags = e['flags']
        if e['stop_id'] and (e['car_code'], e['stop_id']) not in stop_ids and 'foreign' not in flags \
                and e['stop_id'] not in row['removed']:
            row['removed'].append(e['stop_id'])
    for t in terminals:
        if t.car_code in by_car and t.last_seen_at and (by_car[t.car_code]['last_contact'] is None
                                                         or t.last_seen_at > by_car[t.car_code]['last_contact']):
            by_car[t.car_code]['last_contact'] = t.last_seen_at
    cars = []
    for code in sorted(by_car):
        row = by_car[code]
        row['drivers'] = sorted(row['drivers'])
        cars.append(row)
    flagged = [{'id': e['id'], 'car_code': e['car_code'], 'driver_name': e['driver_name'], 'type': e['type'],
                'stop_id': e['stop_id'], 'at': e['at'], 'flags': e['flags']} for e in events if e['flags']]
    return {'date': ds, 'cars': cars, 'flagged': flagged, 'rejected': st.store.rejected_for_day(ds)}


def plan_mismatches(day: date) -> dict[str, Any]:
    """Накладные ERP, которые везёт не та машина, которой их отдал план «Развоза» (только чтение)."""
    st = state()
    view = routes_view(_routes_state(), day)
    if not view.plan_exists or st.invoice_loader is None:
        return {'plan_exists': view.plan_exists, 'items': []}
    invoices, names = st.invoice_loader(day)
    plan = view.plan_trucks()
    items = []
    for inv in sorted(invoices, key=lambda x: (x.customer_id, x.doc_number)):
        trucks = plan.get(inv.customer_id)
        if trucks and inv.car_code not in trucks:
            code, name = names.get(inv.customer_id, (str(inv.customer_id), ''))
            items.append({'doc_number': inv.doc_number, 'customer_code': code, 'customer_name': name,
                          'erp_car': inv.car_code or None, 'plan_cars': sorted(trucks)})
    return {'plan_exists': True, 'items': items}


def _routes_state() -> Any:
    from flask import current_app
    return current_app.extensions.get('route_optimizer')


@bp.get('/api/courier/admin/today')
@_api
def today_view() -> Any:
    d = _day()
    if d is None:
        return _bad('Սխալ ամսաթիվ')
    body = day_overview(d)
    try:
        body['mismatch'] = plan_mismatches(d)
    except ErpError:
        logger.warning('[Courier] Сравнение с планом «Развоза» не выполнено', exc_info=True)
        body['mismatch'] = {'plan_exists': None, 'items': [], 'error': 'ERP-ն հասանելի չէ'}
    return jsonify({'success': True, **body})


# --- «Գումար»: деньги водителей ---

def money_view(day: date) -> dict[str, Any]:
    """По водителю: по каждой точке — взял по накладной и в счёт долга, чеки ՀԴՄ; итог и «сдал фактически»."""
    st = state()
    ds = day.isoformat()
    stops = {s['stop_id']: s for s in st.store.day_stops(ds)}
    events = st.store.events_for_day(ds)
    hand = st.store.handovers(ds)
    names = {d.id: d.name for d in st.store.list_drivers()}
    deliveries = ev.effective(events, 'delivery')
    drivers: dict[int, dict[str, Any]] = {}
    for e in events:
        if e['type'] != 'payment':
            continue
        drv = drivers.setdefault(e['driver_id'], {'rows': {}, 'events': []})
        drv['events'].append(e)
        sid = e['stop_id'] or ''
        row = drv['rows'].setdefault(sid, {'events': []})
        row['events'].append(e)
    out = []
    for did in sorted(set(drivers) | set(hand), key=lambda i: (names.get(i) or '', i)):
        data = drivers.get(did, {'rows': {}, 'events': []})
        rows = []
        for sid, r in sorted(data['rows'].items(), key=lambda kv: ((stops.get(kv[0]) or {}).get('seq') or 0, kv[0])):
            s = stops.get(sid) or {}
            money = ev.payments_total(r['events'])
            receipts = sorted({str(e['payload'].get('ecr_receipt')) for e in r['events']
                               if e['payload'].get('ecr_receipt') and not e['payload'].get('cancel_of')})
            rows.append({'stop_id': sid, 'doc_number': s.get('doc_number'), 'car_code': s.get('car_code'),
                         'customer': (s.get('customer') or {}).get('name'), 'collect': s.get('collect'),
                         'amount_due': s.get('amount_due'), 'invoice': money['invoice'], 'debt': money['debt'],
                         'receipts': receipts, 'status': _stop_status(s, deliveries.get(sid)) if s else None,
                         'flags': sorted({f for e in r['events'] for f in e['flags']})})
        total = ev.payments_total(data['events'])
        h = hand.get(did)
        collected = round(total['invoice'] + total['debt'], 2)
        out.append({'driver_id': did, 'name': names.get(did) or f'#{did}', 'rows': rows,
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
    ('tax_id', 'ՀՎՀՀ'), ('doc_number', 'Ապրանքագիր'), ('date', 'Ամսաթիվ'), ('at', 'Ժամանակ'),
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


@bp.get('/api/courier/admin/marks')
@_api
def marks() -> Any:
    args = _marks_args()
    if args is None:
        return _bad('Սխալ ամսաթիվ')
    rows = state().store.search_scans(*args, limit=MARKS_LIMIT)
    return jsonify({'success': True, 'rows': rows, 'limit': MARKS_LIMIT})


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
    text = marks_csv(state().store.search_scans(*args, limit=MARKS_LIMIT))
    name = f'makanshum_{args[1] or "all"}_{args[2] or "all"}.csv'
    return Response(text.encode('utf-8'), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


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

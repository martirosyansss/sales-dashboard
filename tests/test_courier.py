# -*- coding: utf-8 -*-
"""Тесты раздела «Առաքիչ» (сервер терминалов водителей): временные базы, ERP подменён, без сети.

Запуск из корня проекта:  python -m pytest tests/test_courier.py -q
"""
import io
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import courier  # noqa: E402
from courier import clock, day as dy, erp_day as ed, events as ev, order as od, routes_link as rl  # noqa: E402
from courier.state import CourierState  # noqa: E402
from courier.store import SCHEMA_VERSION, MarkSetting, PinConflict, Store, StoreError  # noqa: E402
from route_optimizer import erp  # noqa: E402
from route_optimizer.dispatch import DispatchOrder  # noqa: E402

DEMO = '2000-01-01'
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=clock.YEREVAN)
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 100
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100


class FakeDb:
    connection_string = 'DRIVER={none};'


@pytest.fixture
def now(monkeypatch):
    """Подменяемое «сейчас» (Ереван): now['t'] = … двигает часы."""
    box = {'t': NOW}
    monkeypatch.setattr(clock, 'now', lambda: box['t'])
    return box


@pytest.fixture
def app(tmp_path, monkeypatch, now):
    from flask import Flask
    monkeypatch.setenv('COURIER_DEMO', '1')
    monkeypatch.delenv('COURIER_PUBLIC_HOST', raising=False)
    app = Flask(__name__, template_folder=str(ROOT / 'templates'))
    app.secret_key = 'test'
    courier.init_app(app, FakeDb(), db_path=str(tmp_path / 'courier.db'))
    st = app.extensions['courier']
    st.cars_loader = lambda today: [{'code': '991AT61', 'name': 'HOWO', 'docs': 40, 'last': '2026-10-01'}]
    st.catalog_loader = None
    st.invoice_loader = None
    return app


@pytest.fixture
def st(app) -> CourierState:
    return app.extensions['courier']


@pytest.fixture
def client(app):
    return app.test_client()


def make_terminal(st, car='TEST', pin='1234', name='Արամ'):
    did = st.store.save_driver(None, name, True, pin, 'admin')
    terminal, token = st.store.create_terminal('Urovo 1', car, 'admin')
    return did, terminal, {'Authorization': f'Bearer {token}'}


def login(client, headers, pin='1234'):
    r = client.post('/api/courier/v1/login', json={'pin': pin}, headers=headers)
    assert r.status_code == 200, r.get_json()
    return {**headers, 'X-Courier-Session': r.get_json()['session']}


@pytest.fixture
def term(st, client):
    """Терминал машины TEST с вошедшим водителем и загруженным демо-днём."""
    did, terminal, h = make_terminal(st)
    s = login(client, h)
    day = client.get(f'/api/courier/v1/day?date={DEMO}', headers=s).get_json()
    return {'driver_id': did, 'terminal': terminal, 'h': h, 's': s, 'day': day}


def eid() -> str:
    return str(uuid.uuid4())


def event(etype, stop_id, payload, at='2000-01-01T10:00:00+04:00', date_=DEMO, **kw):
    return {'id': kw.get('id', eid()), 'type': etype, 'stop_id': stop_id, 'date': date_, 'at': at, 'payload': payload}


def post(client, s, *events):
    r = client.post('/api/courier/v1/events', json={'events': list(events)}, headers=s)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def full_lines(stop, factor=1.0):
    return [{'line_id': ln['line_id'], 'qty': ln['qty'] * factor} for ln in stop['lines']]


# ============================== чистые правила ==============================

@pytest.mark.parametrize('pay_type, collect', [
    ('1', 'cash'), ('5', 'cash_ecr'), ('2', 'none'), ('3', 'none'), ('6', 'none'),
    ('', 'ask'), (None, 'ask'), ('4', 'ask'), (' 1 ', 'cash'), ('9', 'ask'),
])
def test_pay_type_mapping(pay_type, collect):
    assert ed.collect_for(pay_type) == collect


def test_debt_formula_dashboard():
    # дебет (D − C) − |Type01| − |Type02|; знаки остатков не важны (abs), клиента без записей — 0
    debts = ed.compose_debts([1, 2, 3, 4], {1: 100000.0, 2: 5000.0, 4: -2000.0},
                             {1: (-15000.0, 0.0), 2: (0.0, 7000.0), 3: (500.0, -500.0)})
    assert debts == {1: 85000.0, 2: -2000.0, 3: -1000.0, 4: -2000.0}


def test_expected_tare():
    links = [ed.ContainerLink(135, 54, 20, 20), ed.ContainerLink(135, 55, 20, 1),   # 20 бут. : 20 бут. + 1 ящик
             ed.ContainerLink(200, 202, 1, 1), ed.ContainerLink(300, 99, 0, 1)]     # битая связь (0) — мимо
    tare = ed.expected_tare([(135, 40.0), (200, 10.0), (300, 5.0), (999, 3.0), (200, 2.5)], links)
    assert tare == {54: 40.0, 55: 2.0, 202: 12.5}


def test_gtin_and_pack_qty():
    assert ed.to_gtin14('4850002370146') == '04850002370146'
    assert ed.to_gtin14('04850002370146') == '04850002370146'
    assert ed.to_gtin14('12345678') == '00000012345678'
    assert ed.to_gtin14('ABC') is None and ed.to_gtin14('123') is None and ed.to_gtin14(None) is None
    assert ed.pack_qty_from_units(True, 12, 1) == 12
    assert ed.pack_qty_from_units(True, 6, 1) == 6
    assert ed.pack_qty_from_units(False, 12, 1) is None
    assert ed.pack_qty_from_units(True, 1, 1) is None      # 1 штука — не упаковка
    assert ed.pack_qty_from_units(True, 12, 0) is None


def test_erp_sql_is_read_only():
    """Все запросы раздела проходят read-only guard «Маршрутов» (только SELECT, WITH (NOLOCK), без записи)."""
    sqls = [v for k, v in vars(ed).items() if k.startswith('SQL_')]
    assert len(sqls) >= 10
    for sql in sqls:
        erp.check_sql(sql)
        assert 'WITH (NOLOCK)' in sql


def test_delivery_status():
    inv = {'a': {'qty': 10}, 'b': {'qty': 5}}
    assert ev.delivery_status({'a': 10, 'b': 5}, inv) == 'full'
    assert ev.delivery_status({'a': 0, 'b': 0}, inv) == 'refuse'
    assert ev.delivery_status({'a': 10, 'b': 2}, inv) == 'partial'
    assert ev.delivery_status({'a': 10}, inv) == 'partial'     # строка не указана — 0


# ============================== порядок точек ==============================

YEREVAN = (40.1792, 44.4991)


def test_order_auto_from_depot_and_no_coords_last():
    pts = {3: (40.30, 44.60), 1: (40.20, 44.50), 2: (40.25, 44.55), 9: None, 7: None}
    order, source = od.order_customers(pts, YEREVAN, [])
    assert source == 'auto'
    assert order == [1, 2, 3, 7, 9]     # по линии от склада; без координат — в конце, по id


def test_order_follows_dispatch_plan_then_rest():
    pts = {1: (40.20, 44.50), 2: (40.25, 44.55), 3: (40.30, 44.60), 4: (40.31, 44.61), 5: None}
    order, source = od.order_customers(pts, YEREVAN, [3, 99, 1, 5])
    assert source == 'dispatch'
    assert order[:2] == [3, 1]          # план; 99 нет в накладных, 5 без координат
    assert set(order[2:4]) == {2, 4} and order[-1] == 5


def test_order_without_depot_is_open_path():
    pts = {1: (40.20, 44.50), 2: (40.21, 44.51), 3: (40.50, 44.80)}
    order, source = od.order_customers(pts, None, [])
    assert source == 'auto' and sorted(order) == [1, 2, 3]
    assert order.index(2) in (order.index(1) - 1, order.index(1) + 1)   # соседние точки — подряд


def _order(isn, customer, car='', when=date(2026, 10, 1), shipped=None, agent=7, van=0):
    return DispatchOrder(isn=isn, doc_num='N' + isn[:4], order_date=when, customer_id=customer, agent_id=agent,
                         car_code=car, revenue=1000.0, kg=10.0, shipped=shipped, van_agent_id=van)


ISN = ['0000000{}-0000-0000-0000-000000000000'.format(i) for i in range(1, 8)]


def test_pick_orders_by_plan_or_car():
    d = date(2026, 10, 2)
    orders = [_order(ISN[0], 1, 'A'), _order(ISN[1], 2, 'B'), _order(ISN[2], 3, 'A', shipped=date(2026, 10, 1)),
              _order(ISN[3], 4, 'A', agent=5, van=5), _order(ISN[4], 5, 'A', when=date(2026, 9, 25))]
    no_plan = rl.RoutesView()
    assert [o.isn for o in rl.pick_orders(orders, d, no_plan, 'A')] == [ISN[0]]   # отгружен, сам везёт, старый — нет
    plan = rl.RoutesView(plan_exists=True, trips=(('A', (2, 5)), ('B', (1,))), added=frozenset({ISN[4]}))
    assert [o.isn for o in rl.pick_orders(orders, d, plan, 'A')] == [ISN[1], ISN[4]]
    excluded = rl.RoutesView(plan_exists=True, trips=(('A', (2,)),), excluded=frozenset({ISN[1]}))
    assert rl.pick_orders(orders, d, excluded, 'A') == []


def test_routes_view_car_customers():
    v = rl.RoutesView(plan_exists=True, trips=(('A', (3, 1)), ('B', (2,)), ('A', (1, 4))))
    assert v.car_customers('A') == [3, 1, 4]
    assert v.plan_trucks() == {3: {'A'}, 1: {'A'}, 2: {'B'}, 4: {'A'}}


# ============================== загрузка ERP (подменённой) ==============================

class FakeErp:
    """Подмена _select: ответ по тексту запроса. Записывает выполненные запросы."""

    def __init__(self, sales=(), orders=()):
        self.sales = list(sales)
        self.orders = list(orders)
        self.calls = []

    def __call__(self, conn, sql, params=()):
        erp.check_sql(sql)
        self.calls.append(sql)
        if sql == ed.SQL_DAY_SALES:
            return self.sales
        if sql == erp.SQL_DISPATCH_ORDERS:
            return self.orders
        if sql.startswith(ed.SQL_ORDER_PAYTYPES.split('{')[0]):
            return [(ISN[1], '5')]
        if sql.startswith(ed.SQL_DOC_LINES.split('{')[0]):
            return [(ISN[0], 1, 135, 40, 150, 6000), (ISN[0], 2, 200, 10, 1200, 12000), (ISN[1], 1, 200, 3, 1200, 3600)]
        if sql == ed.SQL_CONTAINERS:
            return [(135, 54, 20, 20, 'Ապակե շիշ'), (135, 55, 20, 1, 'Արկղ'), (200, 202, 1, 1, 'Տարա 20լ')]
        if sql.startswith(ed.SQL_PRODUCTS.split('{')[0]):
            return [(135, '2251', 'Գառնի ապակի 0.5', 'հատ', 0.9, False, True, 20, 1, False),
                    (200, '2901', 'Գառնի 19լ', 'հատ', 19.5, True, False, 1, 1, False),
                    (54, '4001', 'Ապակե շիշ', 'հատ', 0.4, False, False, 1, 1, True)]
        if sql.startswith(ed.SQL_BARCODES.split('{')[0]):
            return [(135, '4850002370146'), (200, 'BAD')]
        if sql == ed.SQL_DEFAULT_POINTS:
            return [(40.1, 44.5)]
        if sql.startswith(ed.SQL_CUSTOMER_INFO.split('{')[0]):
            return [(11, '0456', 'Խանութ', '010', '0123', 'հին հասցե', 'Աբովյան 1', 40.2, 44.52),
                    (12, '0457', 'Խանութ 2', '', None, 'Կոմիտաս 2', None, 40.1, 44.5)]   # «дефолтная» точка
        if sql.startswith(ed.SQL_GPS_VISITS.split('{')[0]):
            return [(12, 40.3, 44.6, 10), (12, 40.3, 44.6, 10), (12, 40.3, 44.6, 500), (12, 40.3, 44.6, None)]
        if sql == erp.SQL_AGENTS:
            return [(7, 'A7', 'Մենեջեր', False)]
        if sql == erp.SQL_CARS:
            return [('991AT61', 'HOWO', False)]
        if sql.startswith(ed.SQL_DEBIT_BEFORE.split('{')[0]):
            assert params[-1] == date(2026, 10, 2)      # долг — на утро дня, без накладных дня
            return [(11, 50000), (12, 1000)]
        if sql.startswith(erp.SQL_CUSTOMER_REST.split('{')[0]):
            return [(11, -5000, 1000)]
        raise AssertionError(f'неожиданный запрос: {sql[:80]}')


@pytest.fixture
def fake_erp(monkeypatch):
    fake = FakeErp()
    monkeypatch.setattr(erp, '_select', fake)
    monkeypatch.setattr(ed, '_select', fake)
    monkeypatch.setattr(erp, 'connect', lambda cs, **kw: object())
    monkeypatch.setattr(erp, 'close_quietly', lambda conn: None)
    return fake


def test_load_day_invoices(fake_erp):
    fake_erp.sales = [(ISN[0], '000318001', 11, 7, '1', 18000), (ISN[1], '000318002', 12, 7, '', 3600)]
    data = ed.load_day('cs', '991AT61', date(2026, 10, 2), (date(2026, 9, 29), date(2026, 10, 2)),
                       lambda orders: pytest.fail('накладные есть — заказы не нужны'))
    assert [d.stop_id for d in data.docs] == [f'S:{ISN[0]}', f'S:{ISN[1]}']
    assert data.car_name == 'HOWO'
    assert data.gtins == {135: ('04850002370146',)}
    assert data.products[135].pack_qty_erp == 20
    assert data.customers[11].erp_point == (40.2, 44.52) and data.customers[11].address == 'Աբովյան 1'
    assert data.customers[12].erp_point is None                 # «дефолтная» точка отброшена
    assert data.gps == {12: (40.3, 44.6)}                       # 3 точных визита (500 м — мимо)
    assert data.debts == {11: 50000 - 5000 - 1000, 12: 1000.0}
    assert erp.SQL_DISPATCH_ORDERS not in fake_erp.calls


def test_load_day_falls_back_to_orders(fake_erp):
    fake_erp.orders = [(ISN[1], 'Z0002', date(2026, 10, 1), 12, 7, '991AT61', 3600, 58.5, None, 0)]
    picked = []

    def pick(orders):
        picked.extend(orders)
        return list(orders)
    data = ed.load_day('cs', '991AT61', date(2026, 10, 2), (date(2026, 9, 29), date(2026, 10, 2)), pick)
    assert [d.stop_id for d in data.docs] == [f'O:{ISN[1]}']
    assert data.docs[0].source == 'order' and data.docs[0].pay_type == '5' and data.docs[0].amount == 3600


def test_day_payload_from_erp(fake_erp, tmp_path, now):
    fake_erp.sales = [(ISN[0], '000318001', 11, 7, '1', 18000), (ISN[1], '000318002', 12, 7, '6', 3600)]
    store = Store(str(tmp_path / 'c.db'))
    store.save_mark_settings([MarkSetting(200, False, None), MarkSetting(135, True, 6)], 'admin')
    view = rl.RoutesView(depot=YEREVAN, geo_overrides={12: (40.15, 44.45)})
    svc = dy.DayService(store, lambda car, d, w, pick: ed.load_day('cs', car, d, w, pick), lambda d: view)
    body = svc.get('991AT61', date(2026, 10, 2))
    s1 = next(s for s in body['stops'] if s['customer']['id'] == 11)
    s2 = next(s for s in body['stops'] if s['customer']['id'] == 12)
    assert (s2['lat'], s2['lon']) == (40.15, 44.45)            # ручная точка логиста перекрывает GPS
    assert s1['collect'] == 'cash' and s2['collect'] == 'none' and s2['pay_type'] == '6'
    assert s1['debt'] == 44000.0 and s1['amount_due'] == 18000.0
    lines = {ln['product_id']: ln for ln in s1['lines']}
    assert lines[135]['marked'] is True and lines[135]['pack_qty'] == 6       # настройка перекрывает ERP
    assert lines[200]['marked'] is False                                       # ERP fMARKABLE снят настройкой
    assert lines[135]['line_id'] == f'{ISN[0]}:1' and lines[135]['gtins'] == ['04850002370146']
    assert s1['weight_kg'] == round(40 * 0.9 + 10 * 19.5, 1)
    assert s1['tare_expected'] == [{'tare_id': 'erp:54', 'name': 'Ապակե շիշ', 'qty': 40.0},
                                   {'tare_id': 'erp:55', 'name': 'Արկղ', 'qty': 2.0},
                                   {'tare_id': 'erp:202', 'name': 'Տարա 20լ', 'qty': 10.0}]
    assert body['order_source'] == 'auto' and [s['seq'] for s in body['stops']] == [1, 2]
    # детерминизм: тот же ERP → тот же version; кэш не перечитывает ERP
    calls = len(fake_erp.calls)
    assert svc.get('991AT61', date(2026, 10, 2))['version'] == body['version'] and len(fake_erp.calls) == calls
    svc.invalidate()
    assert svc.get('991AT61', date(2026, 10, 2))['version'] == body['version'] and len(fake_erp.calls) > calls
    assert {s['stop_id'] for s in store.day_stops('2026-10-02', '991AT61')} == {s1['stop_id'], s2['stop_id']}


# ============================== /day демо: форма контракта ==============================

STOP_KEYS = {'stop_id', 'seq', 'source', 'doc_number', 'customer', 'lat', 'lon', 'agent_name', 'pay_type', 'collect',
             'amount_due', 'debt', 'weight_kg', 'lines', 'tare_expected'}
LINE_KEYS = {'line_id', 'product_id', 'code', 'name', 'qty', 'unit', 'price', 'sum', 'marked', 'pack_qty', 'gtins'}


def test_demo_day_matches_contract(term):
    body = term['day']
    assert set(body) == {'date', 'version', 'loaded_at', 'car', 'depot', 'order_source', 'stops', 'tare_types',
                         'refuse_reasons', 'return_reasons'}
    assert body['date'] == DEMO and isinstance(body['version'], str) and len(body['version']) == 40
    assert clock.parse_moment(body['loaded_at']) is not None
    assert body['car'] == {'code': 'TEST', 'name': 'Թեստ'}
    assert set(body['depot']) == {'lat', 'lon'} and body['order_source'] in ('dispatch', 'auto')
    assert len(body['stops']) == 3
    assert sorted(s['collect'] for s in body['stops']) == ['cash', 'cash_ecr', 'none']
    for i, s in enumerate(body['stops'], 1):
        assert set(s) == STOP_KEYS and s['seq'] == i and s['source'] == 'invoice'
        assert s['stop_id'].startswith('S:') and isinstance(s['doc_number'], str)
        assert set(s['customer']) == {'id', 'code', 'name', 'address', 'phone', 'tax_id'}
        assert isinstance(s['customer']['id'], int)
        assert isinstance(s['lat'], float) and isinstance(s['lon'], float)
        assert isinstance(s['amount_due'], float) and isinstance(s['weight_kg'], float)
        assert s['debt'] is None or isinstance(s['debt'], float)
        for ln in s['lines']:
            assert set(ln) == LINE_KEYS
            assert isinstance(ln['qty'], float) and isinstance(ln['price'], float) and isinstance(ln['sum'], float)
            assert isinstance(ln['marked'], bool) and isinstance(ln['gtins'], list)
            assert ln['pack_qty'] is None or isinstance(ln['pack_qty'], int)
        for t in s['tare_expected']:
            assert set(t) == {'tare_id', 'name', 'qty'} and t['tare_id'].startswith('erp:')
    marked = [ln for s in body['stops'] for ln in s['lines'] if ln['marked']]
    assert len(marked) == 1 and marked[0]['pack_qty'] == 6
    assert all(set(t) == {'tare_id', 'name'} for t in body['tare_types'])
    assert body['refuse_reasons'][0] == {'id': 'closed', 'text': 'Խանութը փակ է'}
    assert {'id': 'defect', 'text': 'Խոտան'} in body['return_reasons']


def test_demo_only_with_flag(st, client, monkeypatch):
    _, _, h = make_terminal(st)
    s = login(client, h)
    st.days.demo = False
    r = client.get(f'/api/courier/v1/day?date={DEMO}', headers=s)
    assert r.status_code == 503 and r.get_json()['error'] == 'server'   # ERP «не подключена» (FakeDb)


def test_day_bad_date(term, client):
    r = client.get('/api/courier/v1/day?date=02.10.2026', headers=term['s'])
    assert r.status_code == 400 and r.get_json()['error'] == 'bad_request'


# ============================== авторизация ==============================

def test_no_token_and_bad_token_401(client, st):
    for headers in ({}, {'Authorization': 'Bearer short'}, {'Authorization': 'Bearer ' + 'x' * 43},
                    {'Authorization': 'Basic abc'}):
        r = client.get('/api/courier/v1/ping', headers=headers)
        assert r.status_code == 401 and r.get_json()['error'] == 'unauthorized'
        assert r.get_json()['message']


def test_ping_and_revoked_401(client, st):
    _, terminal, h = make_terminal(st)
    r = client.get('/api/courier/v1/ping', headers=h)
    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'server_time': clock.iso(NOW), 'terminal': 'Urovo 1', 'car_code': 'TEST'}
    s = login(client, h)
    assert st.store.revoke_terminal(terminal.id, 'admin')
    for path in ('/api/courier/v1/ping', '/api/courier/v1/day', '/api/courier/v1/app-version'):
        r = client.get(path, headers=s)
        assert r.status_code == 401 and r.get_json()['error'] == 'unauthorized'


def test_token_stored_only_as_hash(st, tmp_path):
    _, terminal, h = make_terminal(st)
    token = h['Authorization'][7:]
    with closing(sqlite3.connect(st.store.path)) as conn:
        dump = '\n'.join(conn.iterdump())
    assert token not in dump
    assert len(token) >= 43     # 32 байта base64url


def test_login_bad_pin_and_lockout(client, st, now):
    _, terminal, h = make_terminal(st)
    for _ in range(4):
        r = client.post('/api/courier/v1/login', json={'pin': '9999'}, headers=h)
        assert r.status_code == 403 and r.get_json()['error'] == 'pin'
    r = client.post('/api/courier/v1/login', json={'pin': '9999'}, headers=h)
    assert r.status_code == 429 and r.get_json()['error'] == 'locked' and r.get_json()['retry_after'] == 900
    # во время блокировки и верный PIN — 429
    now['t'] = NOW + timedelta(minutes=10)
    r = client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h)
    assert r.status_code == 429 and 290 <= r.get_json()['retry_after'] <= 300
    # неверный формат PIN — 400 и не попытка
    now['t'] = NOW + timedelta(minutes=16)
    assert client.post('/api/courier/v1/login', json={'pin': '12'}, headers=h).status_code == 400
    assert client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h).status_code == 200
    assert st.store.terminal(terminal.id).failed_pin_count == 0


def test_lockout_is_per_terminal(client, st):
    _, _, h1 = make_terminal(st)
    h2 = {'Authorization': 'Bearer ' + st.store.create_terminal('Urovo 2', 'TEST', 'admin')[1]}
    for _ in range(5):
        client.post('/api/courier/v1/login', json={'pin': '0000'}, headers=h1)
    assert client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h1).status_code == 429
    assert client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h2).status_code == 200


def test_login_response_and_session_expiry(client, st, now):
    did, _, h = make_terminal(st)
    r = client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h).get_json()
    assert r['driver'] == {'id': did, 'name': 'Արամ'} and r['car'] == {'code': 'TEST', 'name': 'Թեստ'}
    assert r['expires_at'] == '2026-10-03T04:00:00+04:00'
    s = {**h, 'X-Courier-Session': r['session']}
    assert client.get('/api/courier/v1/status', headers=s).status_code == 200
    now['t'] = datetime(2026, 10, 3, 3, 59, tzinfo=clock.YEREVAN)
    assert client.get('/api/courier/v1/status', headers=s).status_code == 200
    now['t'] = datetime(2026, 10, 3, 4, 0, tzinfo=clock.YEREVAN)
    r = client.get('/api/courier/v1/status', headers=s)
    assert r.status_code == 401 and r.get_json()['error'] == 'session'


def test_session_rules(client, st):
    did, terminal, h = make_terminal(st)
    s1 = login(client, h)
    assert client.get('/api/courier/v1/status', headers=h).get_json()['error'] == 'session'   # без сессии
    s2 = login(client, h)                                                                       # новая отменяет старую
    assert client.get('/api/courier/v1/status', headers=s1).get_json()['error'] == 'session'
    assert client.get('/api/courier/v1/status', headers=s2).status_code == 200
    # чужая сессия: сессия терминала 1 на терминале 2
    h_other = {'Authorization': 'Bearer ' + st.store.create_terminal('Urovo 2', 'TEST', 'admin')[1]}
    r = client.get('/api/courier/v1/status', headers={**h_other, 'X-Courier-Session': s2['X-Courier-Session']})
    assert r.status_code == 401 and r.get_json()['error'] == 'session'
    # выключенный водитель теряет сессию; logout закрывает
    st.store.save_driver(did, 'Արամ', False, None, 'admin')
    assert client.get('/api/courier/v1/status', headers=s2).get_json()['error'] == 'session'
    st.store.save_driver(did, 'Արամ', True, None, 'admin')
    s3 = login(client, h)
    assert client.post('/api/courier/v1/logout', headers=s3).get_json() == {'ok': True}
    assert client.get('/api/courier/v1/status', headers=s3).get_json()['error'] == 'session'


def test_pin_unique_among_active(st):
    st.store.save_driver(None, 'Ա', True, '1234', None)
    with pytest.raises(PinConflict):
        st.store.save_driver(None, 'Բ', True, '1234', None)
    st.store.save_driver(None, 'Գ', False, '1234', None)      # неактивный — можно
    with pytest.raises(ValueError):
        st.store.save_driver(None, 'Դ', True, '12a4', None)


# ============================== события ==============================

def test_events_idempotent_and_duplicates(term, client):
    s1 = term['day']['stops'][0]
    e1 = event('arrived', s1['stop_id'], {'lat': 40.1, 'lon': 44.5, 'accuracy': 12.0})
    e2 = event('delivery', s1['stop_id'], {'lines': full_lines(s1), 'reason_id': None, 'comment': None})
    r = post(client, term['s'], e1, e2, dict(e1))
    assert r == {'accepted': [e1['id'], e2['id']], 'duplicates': [e1['id']], 'rejected': []}
    r = post(client, term['s'], e2, {**e1, 'payload': {'lat': 'changed'}})   # тело не сравнивается
    assert r == {'accepted': [], 'duplicates': [e2['id'], e1['id']], 'rejected': []}
    e3 = {**event('arrived', s1['stop_id'], {'lat': 40.1, 'lon': 44.5}), 'id': e1['id'].upper()}
    assert post(client, term['s'], e3)['duplicates'] == [e3['id']]          # регистр uuid не важен


@pytest.mark.parametrize('mutate, needle', [
    (lambda e: e.update(type='teleport'), 'տեսակ'),
    (lambda e: e.update(date='2000-13-01'), 'date'),
    (lambda e: e.update(at='2000-01-01T10:00:00'), 'at'),              # без зоны
    (lambda e: e.update(payload=[1]), 'payload'),
    (lambda e: e.update(stop_id=None), 'stop_id'),
    (lambda e: e.update(stop_id='S:123'), 'stop_id'),
    (lambda e: e['payload'].update(lines=[]), 'lines'),
    (lambda e: e['payload']['lines'][0].update(qty=-1), 'qty'),
    (lambda e: e['payload']['lines'][0].update(qty=1e9), 'qty'),
    (lambda e: e['payload']['lines'][0].update(qty=1000), 'գերազանցել'),   # сверх накладной (№22)
    (lambda e: e['payload']['lines'][0].update(line_id='X:1'), 'Անհայտ'),
    (lambda e: e['payload']['lines'].append(dict(e['payload']['lines'][0])), 'կրկնվում'),
    (lambda e: e['payload']['lines'][0].update(qty=1), 'պատճառ'),          # «частично» без причины
])
def test_delivery_rejections(term, client, mutate, needle):
    s1 = term['day']['stops'][0]
    e = event('delivery', s1['stop_id'], {'lines': full_lines(s1), 'reason_id': None})
    mutate(e)
    r = post(client, term['s'], e)
    assert r['accepted'] == [] and len(r['rejected']) == 1
    assert r['rejected'][0]['id'] == e['id'] and r['rejected'][0]['error'] == 'bad_request'
    assert needle in r['rejected'][0]['message']


def test_rejected_listed_in_status_and_can_be_fixed(term, client):
    s1 = term['day']['stops'][0]
    bad = event('payment', s1['stop_id'], {'amount': 0, 'kind': 'invoice'})
    assert post(client, term['s'], bad)['rejected'][0]['id'] == bad['id']
    status = client.get(f'/api/courier/v1/status?date={DEMO}', headers=term['s']).get_json()
    assert [r['id'] for r in status['rejected_events']] == [bad['id']]
    assert post(client, term['s'], {**bad, 'payload': {'amount': 10.0, 'kind': 'invoice'}})['accepted'] == [bad['id']]
    status = client.get(f'/api/courier/v1/status?date={DEMO}', headers=term['s']).get_json()
    assert status['rejected_events'] == []


def test_bad_ids_and_batch_limit(term, client):
    r = post(client, term['s'], {'id': 'nope', 'type': 'arrived'}, 'garbage')
    assert [x['id'] for x in r['rejected']] == ['nope', ''] and r['accepted'] == []
    many = [event('arrived', term['day']['stops'][0]['stop_id'], {'lat': 1, 'lon': 1}) for _ in range(201)]
    r = client.post('/api/courier/v1/events', json={'events': many}, headers=term['s'])
    assert r.status_code == 400 and r.get_json()['error'] == 'bad_request'
    assert client.post('/api/courier/v1/events', json=[1], headers=term['s']).status_code == 400


def test_delivery_replacement_last_by_at(term, client, st):
    s2 = term['day']['stops'][1]
    later = event('delivery', s2['stop_id'], {'lines': full_lines(s2, 0), 'reason_id': 'closed'},
                  at='2000-01-01T12:00:00+04:00')
    # пришла позже, но сделана раньше (другая зона: 07:30Z = 11:30+04:00)
    earlier = event('delivery', s2['stop_id'], {'lines': full_lines(s2)}, at='2000-01-01T07:30:00Z')
    assert len(post(client, term['s'], later, earlier)['accepted']) == 2
    eff = ev.effective(st.store.events_for_day(DEMO), 'delivery')
    assert eff[s2['stop_id']]['id'] == later['id']
    today = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()
    car = today['cars'][0]
    assert car['refuse'] == 1 and car['full'] == 0
    newest = event('delivery', s2['stop_id'], {'lines': full_lines(s2)}, at='2000-01-01T12:05:00+04:00')
    post(client, term['s'], newest)
    car = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()['cars'][0]
    assert car['full'] == 1 and car['refuse'] == 0 and car['pending'] == 2


def test_payments_cancel_and_flags(term, client, st):
    stops = {s['collect']: s for s in term['day']['stops']}
    cash, ecr, none = stops['cash'], stops['cash_ecr'], stops['none']
    p1 = event('payment', cash['stop_id'], {'amount': 12000.0, 'kind': 'invoice', 'ecr_receipt': None})
    p2 = event('payment', cash['stop_id'], {'amount': 5000.0, 'kind': 'debt', 'ecr_receipt': None})
    p3 = event('payment', ecr['stop_id'], {'amount': 8400.0, 'kind': 'invoice', 'ecr_receipt': None})
    p4 = event('payment', none['stop_id'], {'amount': 100.0, 'kind': 'invoice'})
    assert len(post(client, term['s'], p1, p2, p3, p4)['accepted']) == 4
    flags = {e['id']: e['flags'] for e in st.store.events_for_day(DEMO)}
    assert flags[p1['id']] == [] and flags[p3['id']] == ['no_ecr_receipt'] and flags[p4['id']] == ['paid_collect_none']
    cancel = event('payment', none['stop_id'], {'amount': 100.0, 'kind': 'invoice', 'cancel_of': p4['id']})
    assert post(client, term['s'], cancel)['accepted'] == [cancel['id']]
    # повторная отмена, отмена не той суммы, отмена несуществующего — отказ
    for bad in ({'amount': 100.0, 'kind': 'invoice', 'cancel_of': p4['id']},
                {'amount': 1.0, 'kind': 'debt', 'cancel_of': p2['id']},
                {'amount': 1.0, 'kind': 'invoice', 'cancel_of': str(uuid.uuid4())}):
        assert post(client, term['s'], event('payment', cash['stop_id'], bad))['rejected']
    status = client.get(f'/api/courier/v1/status?date={DEMO}', headers=term['s']).get_json()
    assert status['cash'] == {'collected_invoice': 20400.0, 'collected_debt': 5000.0, 'handed': None,
                              'handed_at': None, 'handed_by': None}


def test_foreign_and_unknown_stop(term, client, st):
    s1 = term['day']['stops'][0]
    other = event('arrived', s1['stop_id'], {'lat': 40.1, 'lon': 44.5}, date_='2000-01-02')   # чужая дата
    unknown = event('tare', 'S:11111111-2222-3333-4444-555555555555', {'items': [{'tare_id': 'erp:1', 'qty': 2}]})
    assert len(post(client, term['s'], other, unknown)['accepted']) == 2
    flags = {e['id']: e['flags'] for e in st.store.events_for_day('2000-01-02') + st.store.events_for_day(DEMO)}
    assert flags[other['id']] == ['foreign'] and flags[unknown['id']] == ['unknown_stop']
    # чужая машина: точка TEST, а терминал машины 991AT61
    _, _, h2 = make_terminal(st, car='991AT61', pin='5678', name='Բաբկեն')
    s2 = login(client, h2, '5678')
    e = event('delivery', s1['stop_id'], {'lines': full_lines(s1)})
    assert post(client, s2, e)['accepted'] == [e['id']]
    assert next(x for x in st.store.events_for_day(DEMO) if x['id'] == e['id'])['flags'] == ['foreign']


def test_scans_duplicates_repeat_and_cancel(term, client, st):
    stops = term['day']['stops']
    marked_stop = next(s for s in stops if any(ln['marked'] for ln in s['lines']))
    line = next(ln for ln in marked_stop['lines'] if ln['marked'])
    other_stop = next(s for s in stops if s is not marked_stop)
    raw = '0104850002370146215abc\u001d93xyz'

    def scan(stop, raw_=raw, kind='sale', units=6, is_group=True, line_id=line['line_id']):
        return event('scan', stop['stop_id'], {'raw': raw_, 'line_id': line_id, 'kind': kind, 'gtin': '04850002370146',
                                               'serial': '5abc', 'is_group': is_group, 'units': units})
    a, a_again, b = scan(marked_stop), scan(marked_stop), scan(other_stop, line_id=None)
    ret = scan(other_stop, kind='return', line_id=None)
    assert len(post(client, term['s'], a, a_again, b, ret)['accepted']) == 4
    rows = {r['event_id']: r for r in st.store.search_scans()}
    assert rows[a['id']]['counted'] and not rows[a['id']]['duplicate_elsewhere']
    assert not rows[a_again['id']]['counted']                         # тот же raw+kind по той же точке
    assert rows[b['id']]['duplicate_elsewhere']                       # уже отдан по другой точке
    assert not rows[ret['id']]['duplicate_elsewhere']                 # возврат — другой вид
    assert rows[a['id']]['customer_name'] == marked_stop['customer']['name']
    assert rows[a['id']]['product_name'] == line['name'] and rows[a['id']]['doc_number'] == marked_stop['doc_number']
    assert rows[b['id']]['product_id'] is None                        # GTIN не из накладной точки
    flags = {e['id']: set(e['flags']) for e in st.store.events_for_day(DEMO)}
    assert 'repeat' in flags[a_again['id']] and {'duplicate_elsewhere', 'gtin_not_in_invoice'} <= flags[b['id']]
    # отмена скана; отмена, пришедшая раньше скана, — тоже действует
    c = scan(marked_stop, raw_='CODE-2', units=1, is_group=False)
    pre = event('scan_cancel', None, {'scan_event_id': c['id']})
    assert post(client, term['s'], pre)['accepted'] == [pre['id']]
    post(client, term['s'], c, event('scan_cancel', marked_stop['stop_id'], {'scan_event_id': a['id']}),
         event('scan_cancel', marked_stop['stop_id'], {'scan_event_id': a_again['id']}))
    rows = {r['event_id']: r for r in st.store.search_scans()}
    assert rows[a['id']]['cancelled'] and rows[c['id']]['cancelled'] and rows[a_again['id']]['cancelled']
    # отменённые сканы не участвуют в проверке повторов: по other_stop код повторён (b), но не «отдан в другом месте»
    d = scan(other_stop, line_id=None)
    post(client, term['s'], d)
    row = {r['event_id']: r for r in st.store.search_scans()}[d['id']]
    assert not row['counted'] and not row['duplicate_elsewhere']


def test_unit_gtin():
    assert ev.unit_gtin('14850001234564') == '04850001234562'          # упаковка → штука, контрольная цифра GS1
    assert ev.unit_gtin('04850001234562') is None                      # уже штука
    assert ev.unit_gtin('94850001234561') is None                      # 9 — переменный вес, не упаковка
    assert ev.unit_gtin('123') is None and ev.unit_gtin(None) is None


def test_pack_gtin_matches_unit_line(term, client, st):
    """/day отдаёт штучный GTIN; групповой код с GTIN упаковки — тот же товар, не «GTIN не из накладной»."""
    stop = next(s for s in term['day']['stops'] if any(ln['marked'] for ln in s['lines']))
    line = next(ln for ln in stop['lines'] if ln['marked'])
    unit = line['gtins'][0]
    body = '1' + unit[1:13]
    pack = body + ev._gs1_check_digit(body)
    assert ev.unit_gtin(pack) == unit
    sc = event('scan', stop['stop_id'], {'raw': f'01{pack}21PK1', 'line_id': None, 'kind': 'sale', 'gtin': pack,
                                         'serial': 'PK1', 'is_group': True, 'units': line['pack_qty']})
    assert post(client, term['s'], sc)['accepted'] == [sc['id']]
    flags = {e['id']: set(e['flags']) for e in st.store.events_for_day(DEMO)}
    assert 'gtin_not_in_invoice' not in flags[sc['id']]
    row = {r['event_id']: r for r in st.store.search_scans()}[sc['id']]
    assert row['product_name'] == line['name']                          # строка найдена по GTIN штуки


def test_delivery_scan_shortfall_flag(term, client, st):
    stop = next(s for s in term['day']['stops'] if any(ln['marked'] for ln in s['lines']))
    line = next(ln for ln in stop['lines'] if ln['marked'])     # 24 шт, упаковка 6
    scans = [event('scan', stop['stop_id'], {'raw': f'PACK-{i}', 'line_id': line['line_id'], 'kind': 'sale',
                                             'gtin': None, 'serial': None, 'is_group': True, 'units': 6})
             for i in range(3)]
    d1 = event('delivery', stop['stop_id'], {'lines': full_lines(stop)}, at='2000-01-01T10:10:00+04:00')
    post(client, term['s'], *scans, d1)
    flags = {e['id']: e['flags'] for e in st.store.events_for_day(DEMO)}
    assert 'scan_short' in flags[d1['id']]                     # 18 из 24
    unread = event('unreadable', stop['stop_id'], {'line_id': line['line_id'], 'qty': 6.0, 'reason': 'պատռված'})
    d2 = event('delivery', stop['stop_id'], {'lines': full_lines(stop)}, at='2000-01-01T10:20:00+04:00')
    post(client, term['s'], unread, d2)
    flags = {e['id']: e['flags'] for e in st.store.events_for_day(DEMO)}
    assert 'scan_short' not in flags[d2['id']]
    today = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()
    flagged = {f['id']: f['flags'] for f in today['flagged']}
    assert 'no_photo' in flagged[unread['id']]                 # «не читается» — фото обязательно (№18)


def test_other_event_types(term, client):
    s1 = term['day']['stops'][0]
    ok = [event('tare', s1['stop_id'], {'items': [{'tare_id': 'erp:990202', 'qty': 5.0}, {'tare_id': 'custom:1', 'qty': 0}]}),
          event('return', s1['stop_id'], {'product_id': 990019, 'qty': 2.0, 'reason_id': 'defect', 'comment': None}),
          event('return', s1['stop_id'], {'product_id': 990019, 'qty': 1}),   # причин в офисе нет — без причины
          event('day_closed', None, {'summary': {'stops': 3}})]
    assert len(post(client, term['s'], *ok)['accepted']) == 4
    bad = [event('tare', s1['stop_id'], {'items': [{'tare_id': 'keg', 'qty': 1}]}),
           event('tare', s1['stop_id'], {'items': [{'tare_id': 'erp:1', 'qty': -1}]}),
           event('return', s1['stop_id'], {'product_id': 990019, 'qty': 0, 'reason_id': 'defect'}),
           event('arrived', s1['stop_id'], {'lat': 'x', 'lon': 44}),
           event('day_closed', None, {})]
    assert len(post(client, term['s'], *bad)['rejected']) == len(bad)


# ============================== фото ==============================

def photo_form(data, pid=None, kind='photo', event_id=None):
    return {'id': pid or str(uuid.uuid4()), 'event_id': event_id or str(uuid.uuid4()), 'kind': kind,
            'file': (io.BytesIO(data), 'x.jpg')}


def test_photos(term, client, st):
    form = photo_form(JPEG)
    pid = form['id']
    r = client.post('/api/courier/v1/photos', data=form, headers=term['s'], content_type='multipart/form-data')
    assert r.status_code == 200 and r.get_json() == {'ok': True}
    r = client.post('/api/courier/v1/photos', data=photo_form(PNG, pid), headers=term['s'],
                    content_type='multipart/form-data')
    assert r.get_json() == {'ok': True, 'duplicate': True}
    saved = st.store.photo(pid)
    path = Path(st.store.photos_dir) / saved['path']
    assert path.read_bytes() == JPEG and saved['size'] == len(JPEG)   # повтор не перезаписал файл
    assert client.post('/api/courier/v1/photos', data=photo_form(PNG, kind='signature'), headers=term['s'],
                       content_type='multipart/form-data').status_code == 200
    r = client.post('/api/courier/v1/photos', data=photo_form(b'GIF89a' + b'\x00' * 10), headers=term['s'],
                    content_type='multipart/form-data')
    assert r.status_code == 400 and r.get_json()['error'] == 'bad_request'
    r = client.post('/api/courier/v1/photos', data=photo_form(JPEG + b'\x00' * (2 * 1024 * 1024)), headers=term['s'],
                    content_type='multipart/form-data')
    assert r.status_code == 413 and r.get_json()['error'] == 'too_large'
    for bad in ({'id': '../../etc'}, {'kind': 'video'}, {'event_id': 'x'}):
        r = client.post('/api/courier/v1/photos', data={**photo_form(JPEG), **bad}, headers=term['s'],
                        content_type='multipart/form-data')
        assert r.status_code == 400
    r = client.post('/api/courier/v1/photos', data={'id': str(uuid.uuid4()), 'event_id': str(uuid.uuid4()), 'kind': 'photo'},
                    headers=term['s'], content_type='multipart/form-data')
    assert r.status_code == 400


def test_body_size_limit(term, client):
    r = client.post('/api/courier/v1/events', data=b'{' + b' ' * (5 * 1024 * 1024) + b'}', headers=term['s'],
                    content_type='application/json')
    assert r.status_code == 413 and r.get_json()['error'] == 'too_large'


def test_unknown_api_path_json_404(term, client):
    r = client.get('/api/courier/v1/nothing', headers=term['s'])
    assert r.status_code == 404 and r.get_json()['error'] == 'not_found'
    r = client.delete('/api/courier/v1/day', headers=term['s'])
    assert r.status_code == 405 and r.get_json()['error'] == 'bad_request'


# ============================== APK ==============================

def test_app_version_and_apk(term, client):
    r = client.get('/api/courier/v1/app-version', headers=term['h'])
    assert r.status_code == 404 and r.get_json()['error'] == 'not_found'
    apk = b'PK\x03\x04' + b'apk' * 100
    up = lambda code, data=apk: client.post('/api/courier/admin/apk', headers={'X-Requested-With': 'fetch'},  # noqa: E731
                                            data={'file': (io.BytesIO(data), 'a.apk'), 'version_code': str(code),
                                                  'version_name': '1.0.' + str(code)},
                                            content_type='multipart/form-data')
    assert up(3).status_code == 200
    assert up(2).status_code == 400 and up(5, b'not a zip').status_code == 400
    assert client.post('/api/courier/admin/apk', data={}, content_type='multipart/form-data').status_code == 415
    r = client.get('/api/courier/v1/app-version', headers=term['h']).get_json()
    assert r['version_code'] == 3 and r['version_name'] == '1.0.3' and r['size'] == len(apk)
    assert r['url'] == '/api/courier/v1/app/apk' and len(r['sha256']) == 64
    r = client.get('/api/courier/v1/app/apk', headers=term['h'])
    assert r.status_code == 200 and r.data == apk
    assert client.get('/api/courier/v1/app/apk').status_code == 401


# ============================== офис ==============================

def test_cash_handover(term, client, st):
    cash = next(s for s in term['day']['stops'] if s['collect'] == 'cash')
    post(client, term['s'], event('payment', cash['stop_id'], {'amount': 12000.0, 'kind': 'invoice', 'ecr_receipt': '77'}),
         event('payment', cash['stop_id'], {'amount': 3000.0, 'kind': 'debt'}))
    m = client.get(f'/api/courier/admin/money?date={DEMO}').get_json()
    drv = m['drivers'][0]
    assert (drv['collected_invoice'], drv['collected_debt'], drv['collected'], drv['handed']) == (12000.0, 3000.0, 15000.0, None)
    assert drv['rows'][0]['receipts'] == ['77'] and drv['rows'][0]['doc_number'] == cash['doc_number']
    r = client.post('/api/courier/admin/money/handover', json={'date': DEMO, 'driver_id': term['driver_id'], 'handed': 14500})
    assert r.get_json() == {'success': True}
    drv = client.get(f'/api/courier/admin/money?date={DEMO}').get_json()['drivers'][0]
    assert drv['handed'] == 14500.0 and drv['diff'] == -500.0
    status = client.get(f'/api/courier/v1/status?date={DEMO}', headers=term['s']).get_json()
    assert status['cash']['handed'] == 14500.0 and status['cash']['handed_at'] == clock.iso(NOW)
    for bad in ({'date': DEMO, 'driver_id': term['driver_id'], 'handed': -1},
                {'date': 'x', 'driver_id': term['driver_id'], 'handed': 1},
                {'date': DEMO, 'driver_id': 999, 'handed': 1}):
        assert client.post('/api/courier/admin/money/handover', json=bad).status_code == 400
    assert client.post('/api/courier/admin/money/handover', data='date=x').status_code == 400   # только JSON
    client.post('/api/courier/admin/money/handover', json={'date': DEMO, 'driver_id': term['driver_id'], 'handed': None})
    assert client.get(f'/api/courier/v1/status?date={DEMO}', headers=term['s']).get_json()['cash']['handed'] is None


def test_marks_search_and_csv(term, client):
    stop = next(s for s in term['day']['stops'] if any(ln['marked'] for ln in s['lines']))
    line = next(ln for ln in stop['lines'] if ln['marked'])
    post(client, term['s'],
         event('scan', stop['stop_id'], {'raw': '01048500023701462100A\u001d93zz', 'line_id': line['line_id'],
                                         'kind': 'sale', 'gtin': '04850002370146', 'serial': '00A',
                                         'is_group': False, 'units': 1}),
         event('scan', stop['stop_id'], {'raw': '=HYPERLINK("x")', 'line_id': None, 'kind': 'return',
                                         'gtin': None, 'serial': None, 'is_group': False, 'units': 1}))
    rows = client.get('/api/courier/admin/marks?q=0485').get_json()['rows']
    assert len(rows) == 1 and rows[0]['tax_id'] == stop['customer']['tax_id']
    assert client.get(f'/api/courier/admin/marks?from={DEMO}&to={DEMO}').get_json()['rows'].__len__() == 2
    assert client.get('/api/courier/admin/marks?from=2001-01-01').get_json()['rows'] == []
    assert client.get('/api/courier/admin/marks?from=bad').status_code == 400
    r = client.get(f'/api/courier/admin/marks.csv?from={DEMO}')
    assert r.status_code == 200 and r.mimetype == 'text/csv' and 'attachment' in r.headers['Content-Disposition']
    text = r.data.decode('utf-8')
    assert text.startswith('﻿Կոդ;GTIN;Սերիական համար;')
    lines = text.lstrip('﻿').strip().split('\r\n')
    assert len(lines) == 3
    assert any('01048500023701462100A<GS>93zz' in ln and 'վաճառք' in ln and stop['doc_number'] in ln for ln in lines)
    assert "'=HYPERLINK" in text and ';=' not in text and ';"=' not in text      # защита от формул (CSV injection)


def test_settings_products_and_reasons(app, client, st):
    st.catalog_loader = lambda today: [
        ed.CatalogItem(1, '2101', 'Գառնի 0.33', 'հատ', True, 6.0, False, False, 120.0),
        ed.CatalogItem(2, '1001', 'Կոլա 0.5', 'հատ', False, 12.0, False, False, 0.0)]
    body = client.get('/api/courier/admin/settings').get_json()
    p = {x['id']: x for x in body['products']}
    assert p[1]['marked'] and p[1]['pack_qty'] == 6.0 and p[1]['sold_90'] and not p[1]['saved']
    assert not p[2]['marked'] and not p[2]['sold_90']
    r = client.post('/api/courier/admin/settings/products', json={'items': [{'product_id': 2, 'marked': True, 'pack_qty': 24}]})
    assert r.get_json() == {'success': True}
    assert st.store.mark_settings()[2] == MarkSetting(2, True, 24.0)
    for bad in ({'product_id': 2, 'marked': 'yes', 'pack_qty': 1}, {'product_id': 2, 'marked': True, 'pack_qty': 0},
                {'product_id': True, 'marked': True, 'pack_qty': None}):
        assert client.post('/api/courier/admin/settings/products', json={'items': [bad]}).status_code == 400
    r = client.post('/api/courier/admin/settings/reasons', json={'kind': 'refuse', 'text': 'Վնասված է'}).get_json()
    assert r['success'] and any(x['text'] == 'Վնասված է' for x in st.store.reasons('refuse'))
    client.post('/api/courier/admin/settings/reasons', json={'kind': 'refuse', 'id': 'closed', 'text': 'Փակ', 'active': False})
    assert all(x['id'] != 'closed' for x in st.store.reasons('refuse'))
    tid = client.post('/api/courier/admin/settings/tare', json={'name': 'Կեգ 30լ'}).get_json()['id']
    day = st.days.get('TEST', date(2000, 1, 1))
    assert {'tare_id': f'custom:{tid}', 'name': 'Կեգ 30լ'} in day['tare_types']
    assert all(r['id'] != 'closed' for r in day['refuse_reasons'])


def test_drivers_terminals_admin_and_qr(client, st):
    r = client.post('/api/courier/admin/drivers', json={'name': 'Արամ', 'pin': '4321'}).get_json()
    assert r['success']
    assert client.post('/api/courier/admin/drivers', json={'name': 'Դավիթ', 'pin': '4321'}).status_code == 400
    assert client.post('/api/courier/admin/drivers', json={'name': '', 'pin': '1111'}).status_code == 400
    lst = client.get('/api/courier/admin/drivers').get_json()
    assert [c['code'] for c in lst['cars']] == ['991AT61', 'TEST']
    assert lst['drivers'][0]['has_pin'] and 'pin_hash' not in json.dumps(lst)
    assert client.post('/api/courier/admin/terminals', json={'name': 'X', 'car_code': 'NOPE'}).status_code == 400
    r = client.post('/api/courier/admin/terminals', json={'name': 'Urovo HOWO', 'car_code': '991AT61'}).get_json()
    qr = json.loads(r['qr_text'])
    assert qr['araqich'] == 1 and qr['url'] == 'https://araqich.orix.am/api/courier/v1' and qr['terminal'] == 'Urovo HOWO'
    assert r['qr_svg'] is None or r['qr_svg'].lstrip().startswith('<svg')
    h = {'Authorization': 'Bearer ' + qr['token']}
    assert client.get('/api/courier/v1/ping', headers=h).get_json()['car_code'] == '991AT61'
    lan = client.post('/api/courier/admin/terminals', json={'name': 'LAN', 'car_code': 'TEST', 'url': 'lan'}).get_json()
    assert json.loads(lan['qr_text'])['url'] == 'http://localhost/api/courier/v1'
    tid = r['terminal']['id']
    assert client.post(f'/api/courier/admin/terminals/{tid}/revoke', json={}).get_json() == {'success': True}
    assert client.get('/api/courier/v1/ping', headers=h).status_code == 401
    assert client.post(f'/api/courier/admin/terminals/{tid}/revoke', json={}).status_code == 404


def test_courier_page_renders(app, client):
    app.add_url_rule('/logout', 'logout', lambda: '')     # base_v2.html ссылается на выход дашборда
    r = client.get('/courier')
    assert r.status_code == 200
    html = r.data.decode('utf-8')
    assert 'Վարորդներ' in html and 'Առաքում այսօր' in html and 'Մակնշում' in html and 'js/courier.js' in html


def test_plan_mismatch(app, st, monkeypatch):
    from courier import views as cv
    view = rl.RoutesView(plan_exists=True, trips=(('A', (1, 2)), ('B', (3,))))
    monkeypatch.setattr(cv, 'routes_view', lambda state, d: view)
    st.invoice_loader = lambda d: ([ed.InvoiceCar(ISN[0], '001', 1, 'A'), ed.InvoiceCar(ISN[1], '002', 2, 'B'),
                                    ed.InvoiceCar(ISN[2], '003', 3, ''), ed.InvoiceCar(ISN[3], '004', 4, 'A')],
                                   {1: ('C1', 'Մեկ'), 2: ('C2', 'Երկու'), 3: ('C3', 'Երեք')})
    with app.test_request_context():
        out = cv.plan_mismatches(date(2026, 10, 2))
    assert out['plan_exists'] is True
    assert [(x['doc_number'], x['erp_car'], x['plan_cars']) for x in out['items']] == [('002', 'B', ['A']), ('003', None, ['B'])]


# ============================== хранилище ==============================

def test_store_schema_and_foreign_file(tmp_path):
    s = Store(str(tmp_path / 'c.db'))
    assert s.list_drivers() == []
    with closing(sqlite3.connect(s.path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
    other = tmp_path / 'other.db'
    with closing(sqlite3.connect(other)) as conn:
        conn.execute('CREATE TABLE foo(x)')
        conn.commit()
    with pytest.raises(StoreError):
        Store(str(other)).list_drivers()
    with closing(sqlite3.connect(s.path)) as conn:
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        conn.commit()
    with pytest.raises(StoreError):
        s.list_drivers()


def test_init_does_not_touch_files(tmp_path, monkeypatch):
    from flask import Flask
    path = tmp_path / 'never.db'
    courier.init_app(Flask(__name__), FakeDb(), db_path=str(path))
    assert not path.exists()


# ============================== внешний хост (туннель) ==============================

@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    """Настоящий app_v2 (гейт входа и защита за туннелем); база «Առաքիչ» — временная."""
    import app_v2
    from courier.day import DayService
    store = Store(str(tmp_path / 'c.db'))
    state = CourierState(store=store, days=DayService(store, None, lambda d: rl.RoutesView()),
                         public_host='araqich.orix.am', public_url='https://araqich.orix.am/api/courier/v1')
    monkeypatch.setitem(app_v2.app.extensions, 'courier', state)
    return app_v2.app.test_client(), state


def test_public_host_guard(dashboard):
    client, state = dashboard
    public = {'Host': 'araqich.orix.am'}
    for path in ('/', '/login', '/routes', '/courier', '/api/customers', '/static/css/courier.css', '/favicon.ico'):
        assert client.get(path, headers=public).status_code == 404, path
    assert client.get('/login', headers={'Host': '192.168.1.10:5000', 'Cf-Connecting-Ip': '1.2.3.4'}).status_code == 404
    assert client.get('/login', headers={'Host': 'ARAQICH.ORIX.AM:443'}).status_code == 404
    # API терминалов снаружи открыт — но только с токеном
    r = client.get('/api/courier/v1/ping', headers=public)
    assert r.status_code == 401 and r.get_json()['error'] == 'unauthorized'
    _, token = state.store.create_terminal('Urovo', 'TEST', 'admin')
    r = client.get('/api/courier/v1/ping', headers={**public, 'Authorization': f'Bearer {token}'})
    assert r.status_code == 200 and r.get_json()['ok'] is True


def test_lan_access_unaffected(dashboard):
    client, _ = dashboard
    lan = {'Host': '192.168.1.10:5000'}
    assert client.get('/login', headers=lan).status_code == 200
    r = client.get('/courier', headers=lan)
    assert r.status_code == 302 and '/login' in r.headers['Location']        # офис — только после входа
    assert client.get('/api/courier/admin/today', headers=lan).status_code == 401
    assert client.get('/api/courier/v1/ping', headers=lan).status_code == 401  # API — токен, не сессия


# ============================== регрессии ревью ==============================

def test_pin_window_not_reset_by_own_login(client, st):
    """Водитель со своим PIN не может перебирать чужой: успешный вход не обнуляет окно ошибок."""
    _, _, h = make_terminal(st)
    for _ in range(4):
        assert client.post('/api/courier/v1/login', json={'pin': '0000'}, headers=h).status_code == 403
    assert client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h).status_code == 200
    r = client.post('/api/courier/v1/login', json={'pin': '0001'}, headers=h)
    assert r.status_code == 429 and r.get_json()['error'] == 'locked'


def test_pin_window_expires(client, st, now):
    _, _, h = make_terminal(st)
    for _ in range(4):
        client.post('/api/courier/v1/login', json={'pin': '0000'}, headers=h)
    now['t'] = NOW + timedelta(minutes=16)                       # окно прошло — счёт заново
    assert client.post('/api/courier/v1/login', json={'pin': '0000'}, headers=h).status_code == 403


def test_payment_cancel_only_own_same_stop_and_day(term, client, st):
    cash = next(s for s in term['day']['stops'] if s['collect'] == 'cash')
    other = next(s for s in term['day']['stops'] if s['collect'] != 'cash')
    pay = event('payment', cash['stop_id'], {'amount': 12000.0, 'kind': 'invoice'})
    post(client, term['s'], pay)
    cancel = {'amount': 12000.0, 'kind': 'invoice', 'cancel_of': pay['id']}
    assert post(client, term['s'], event('payment', other['stop_id'], cancel))['rejected']           # другая точка
    assert post(client, term['s'], event('payment', cash['stop_id'], cancel, date_='2000-01-02'))['rejected']
    _, _, h2 = make_terminal(st, pin='5678', name='Բաբկեն')
    s2 = login(client, h2, '5678')
    assert post(client, s2, event('payment', cash['stop_id'], cancel))['rejected']                     # чужой водитель
    assert post(client, term['s'], event('payment', cash['stop_id'], cancel))['accepted']
    m = client.get(f'/api/courier/admin/money?date={DEMO}').get_json()
    assert [d['collected'] for d in m['drivers']] == [0.0]


def test_scan_cancel_only_own(term, client, st):
    stop = term['day']['stops'][0]
    sc = event('scan', stop['stop_id'], {'raw': 'OWN-1', 'line_id': None, 'kind': 'sale', 'gtin': None,
                                         'serial': None, 'is_group': False, 'units': 1})
    post(client, term['s'], sc)
    _, _, h2 = make_terminal(st, pin='5678', name='Բաբկեն')
    s2 = login(client, h2, '5678')
    r = post(client, s2, event('scan_cancel', stop['stop_id'], {'scan_event_id': sc['id']}))
    assert r['rejected'] and not {x['event_id']: x for x in st.store.search_scans()}[sc['id']]['cancelled']


def test_bad_moment_rejects_event_not_batch(term, client):
    stop = term['day']['stops'][0]
    bad = event('arrived', stop['stop_id'], {'lat': 40.1, 'lon': 44.5}, at='0001-01-01T00:00:00+04:00')
    good = event('arrived', stop['stop_id'], {'lat': 40.1, 'lon': 44.5})
    r = post(client, term['s'], bad, good)
    assert r['accepted'] == [good['id']] and [x['id'] for x in r['rejected']] == [bad['id']]
    assert clock.parse_moment('9999-12-31T23:59:59-04:00') is None

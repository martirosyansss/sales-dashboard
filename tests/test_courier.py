# -*- coding: utf-8 -*-
"""Тесты раздела «Առաքիչ» (сервер терминалов водителей): временные базы, ERP подменён, без сети.

Запуск из корня проекта:  python -m pytest tests/test_courier.py -q
"""
import hashlib
import hmac
import io
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import werkzeug.security as wz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import courier  # noqa: E402
from courier import clock, day as dy, erp_day as ed, events as ev, merge as mg, order as od, routes_link as rl  # noqa: E402
from courier.state import CourierState  # noqa: E402
from courier.store import SCHEMA_VERSION, MarkSetting, PinConflict, PinReset, Store, StoreError  # noqa: E402
from route_optimizer import erp  # noqa: E402
from route_optimizer.dispatch import DispatchOrder  # noqa: E402

DEMO = '2000-01-01'
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=clock.YEREVAN)
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 100
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
REAL_PBKDF2_ITERATIONS = wz.DEFAULT_PBKDF2_ITERATIONS


class FakeDb:
    connection_string = 'DRIVER={none};'


@pytest.fixture(autouse=True)
def _fresh_ref_cache():
    """Кэш справочников ERP (erp_day._ref) — модульный: между тестами не переносится."""
    ed.clear_ref_cache()
    yield
    ed.clear_ref_cache()


@pytest.fixture(autouse=True)
def _pin_env(monkeypatch):
    """Перца нет, пока тест не задаст его сам (.env дашборда не влияет); хеш PIN — 1000 итераций pbkdf2 вместо
    werkzeug по умолчанию (только скорость тестов: метод тот же, «устаревший хеш» считается от этого числа)."""
    monkeypatch.delenv('COURIER_PIN_PEPPER', raising=False)
    monkeypatch.delenv('COURIER_PIN_PEPPER_OLD', raising=False)
    monkeypatch.setattr(wz, 'DEFAULT_PBKDF2_ITERATIONS', 1000)


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


def test_statement_status():
    inv = {'a': 10.0, 'b': 5.0}
    assert mg.statement_status({'a': 10, 'b': 5}, inv) == 'full'
    assert mg.statement_status({'a': 0, 'b': 0}, inv) == 'refused'
    assert mg.statement_status({'a': 10, 'b': 2}, inv) == 'partial'
    assert mg.statement_status({'a': 10}, inv) == 'partial'     # строка не указана — 0
    assert mg.statement_status({'a': 10, 'b': 5, 'x': 3}, inv) == 'full'   # чужая строка не учитывается
    assert mg.statement_status({'a': 0.1 + 0.2}, {'a': 0.3}) == 'partial'   # точно, без допуска (0.30000000000000004)


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

    def __init__(self, sales=(), orders=(), parents=()):
        self.sales = list(sales)
        self.orders = list(orders)
        self.parents = list(parents)
        self.calls = []

    def __call__(self, conn, sql, params=()):
        erp.check_sql(sql)
        self.calls.append(sql)
        if sql == ed.SQL_DAY_SALES:
            return self.sales
        if sql == erp.SQL_DISPATCH_ORDERS:
            return self.orders
        if sql.startswith(ed.SQL_SALE_PARENTS.split('{')[0]):
            assert 'DOCPARENTS' in sql and 'fPARENTDOCTYPE = 1' in sql
            return [r for r in self.parents if r[0] in params]
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
            return [(135, '4850002370146', 1, 1), (200, 'BAD', 1, 1)]
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
    fake_erp.parents = [(ISN[0], ISN[5])]
    data = ed.load_day('cs', '991AT61', date(2026, 10, 2), (date(2026, 9, 29), date(2026, 10, 2)),
                       lambda orders: [])
    assert [d.stop_id for d in data.docs] == [f'S:{ISN[0]}', f'S:{ISN[1]}']
    assert data.docs[0].replaces == (f'O:{ISN[5]}',) and data.docs[1].replaces == ()
    assert data.car_name == 'HOWO'
    assert data.gtins == {135: ('04850002370146',)}
    assert data.products[135].pack_qty_erp == 20
    assert data.customers[11].erp_point == (40.2, 44.52) and data.customers[11].address == 'Աբովյան 1'
    assert data.customers[12].erp_point is None                 # «дефолтная» точка отброшена
    assert data.gps == {12: (40.3, 44.6)}                       # 3 точных визита (500 м — мимо)
    assert data.debts == {11: 50000 - 5000 - 1000, 12: 1000.0}


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
LINE_KEYS = {'line_id', 'product_id', 'code', 'name', 'qty', 'unit', 'price', 'sum', 'marked', 'pack_qty', 'gtins', 'gtin_units'}


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
    today = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()
    car = today['cars'][0]
    assert car['refused'] == 1 and car['full'] == 0
    assert next(x for x in car['stops'] if x['stop_id'] == s2['stop_id'])['status'] == 'refused'
    newest = event('delivery', s2['stop_id'], {'lines': full_lines(s2)}, at='2000-01-01T12:05:00+04:00')
    post(client, term['s'], newest)
    car = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()['cars'][0]
    assert car['full'] == 1 and car['refused'] == 0 and car['pending'] == 2


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


def test_public_apk_download(client, st):
    """Первичная установка без токена; ссылка отдаёт последний APK и не открывает остальные API."""
    url = '/api/courier/v1/download/apk'
    assert client.get(url).status_code == 404
    for code in (3, 4):
        apk = b'PK\x03\x04' + bytes([code]) * 100
        uploaded = client.post('/api/courier/admin/apk', headers={'X-Requested-With': 'fetch'},
                               data={'file': (io.BytesIO(apk), 'a.apk'), 'version_code': str(code),
                                     'version_name': f'1.0.{code}'}, content_type='multipart/form-data')
        assert uploaded.status_code == 200
        with client.get(url, headers={'Host': 'araqich.orix.am'}) as r:
            assert r.status_code == 200 and r.data == apk
            assert r.mimetype == 'application/vnd.android.package-archive'
            assert r.headers['Cache-Control'] == 'no-store'
            assert f'attachment; filename=araqich-1.0.{code}.apk' == r.headers['Content-Disposition']
        assert client.head(url).headers['Content-Length'] == str(len(apk))
    for path in ('ping', 'day', 'app-version', 'app/apk'):
        assert client.get(f'/api/courier/v1/{path}').status_code == 401
    release = st.store.latest_release()
    (Path(st.store.apk_dir) / release.path).unlink()
    assert client.get(url).status_code == 404


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


# ============================== регрессии независимой проверки (v1.1) ==============================

def _parallel(n, fn):
    """n потоков стартуют разом (Barrier); результаты — в порядке потоков."""
    import threading
    barrier = threading.Barrier(n)
    out = [None] * n

    def run(i):
        barrier.wait()
        out[i] = fn(i)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return out


def test_h1_parallel_wrong_pins_at_most_five_evaluations(app, st, monkeypatch):
    """H1: 60 параллельных неверных PIN — PIN проверяется не больше 5 раз, дальше 429 (раньше: 48×403 + 12×429)."""
    _, terminal, h = make_terminal(st)
    calls = []
    real = Store.match_pin

    def counting(self, pin):
        calls.append(pin)
        return real(self, pin)
    monkeypatch.setattr(Store, 'match_pin', counting)

    def attempt(i):
        r = app.test_client().post('/api/courier/v1/login', json={'pin': f'{9000 + i}'}, headers=h)
        return r.status_code, r.get_json()

    res = _parallel(60, attempt)
    codes = [c for c, _ in res]
    assert set(codes) <= {403, 429}
    assert len(calls) <= 5 and codes.count(403) <= 5
    assert all(b['error'] == 'locked' and b['retry_after'] >= 1 for c, b in res if c == 429)
    # ровно столько попыток, сколько проверок: после 5-й ошибки терминал заблокирован
    for _ in range(5 - len(calls)):
        app.test_client().post('/api/courier/v1/login', json={'pin': '0000'}, headers=h)
    r = app.test_client().post('/api/courier/v1/login', json={'pin': '1234'}, headers=h)
    assert r.status_code == 429 and r.get_json()['retry_after'] > 60
    assert len(calls) <= 5


def test_h1_login_in_progress_gets_429(client, st):
    from courier import api
    _, terminal, h = make_terminal(st)
    lock = api._login_lock(terminal.id)
    lock.acquire()
    try:
        r = client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h)
    finally:
        lock.release()
    assert r.status_code == 429 and r.get_json()['error'] == 'locked' and r.get_json()['retry_after'] == 2
    assert client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h).status_code == 200


def test_parallel_duplicate_batches_accept_once(app, term, st):
    stop = term['day']['stops'][0]
    batch = [event('arrived', stop['stop_id'], {'lat': 40.1, 'lon': 44.5}) for _ in range(5)]

    def send(i):
        r = app.test_client().post('/api/courier/v1/events', json={'events': batch}, headers=term['s'])
        return r.status_code, r.get_json()
    res = _parallel(8, send)
    assert all(c == 200 for c, _ in res)
    accepted = [x for _, b in res for x in b['accepted']]
    assert sorted(accepted) == sorted(e['id'] for e in batch)          # каждый id принят ровно один раз
    assert sum(len(b['duplicates']) for _, b in res) == 5 * 7
    assert len([e for e in st.store.events_for_day(DEMO) if e['type'] == 'arrived']) == 5


# --- M1: окно дат /day, снимки, прошлые даты офиса ---

def test_m1_day_date_window(term, client, st):
    s = term['s']
    for d in ('2026-09-30', '2026-10-04', '2025-10-02'):
        r = client.get(f'/api/courier/v1/day?date={d}', headers=s)
        assert r.status_code == 400 and r.get_json()['error'] == 'bad_request', d
    for d in ('2026-10-01', '2026-10-02', '2026-10-03', None):   # ERP «не подключена» (FakeDb) — 503, но не 400
        r = client.get('/api/courier/v1/day' + (f'?date={d}' if d else ''), headers=s)
        assert r.status_code == 503, d
    assert client.get(f'/api/courier/v1/day?date={DEMO}', headers=s).status_code == 200   # COURIER_DEMO=1
    st.demo = False
    assert client.get(f'/api/courier/v1/day?date={DEMO}', headers=s).status_code == 400


def _stops_v2(stops, mutate):
    out = json.loads(json.dumps(stops))
    for s in out:
        mutate(s)
    return out


def test_m1_snapshots_append_only(st, term):
    v1 = term['day']['stops']
    cash = next(s for s in v1 if s['collect'] == 'cash')

    def shrink(s):
        if s['stop_id'] == cash['stop_id']:
            s['lines'][0]['qty'] = 8.0
    v2 = _stops_v2(v1, shrink)
    sid1 = st.store.save_day(DEMO, 'TEST', v1, term['day']['version'], '2000-01-01T08:00:00+04:00')
    sid2 = st.store.save_day(DEMO, 'TEST', v2, 'v2', '2000-01-01T09:00:00+04:00')
    assert sid2 > sid1 and st.store.save_day(DEMO, 'TEST', v2, 'v2', 'x') == sid2      # та же версия — без записи
    sid3 = st.store.save_day(DEMO, 'TEST', v1, term['day']['version'], '2000-01-01T10:00:00+04:00')
    assert sid3 > sid2                                                                   # вернулась старая — новый снимок
    with closing(sqlite3.connect(st.store.path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM day_snapshots').fetchone()[0] == 3
    hist = st.store.stop_versions([cash['stop_id']])[cash['stop_id']]
    assert [v['data']['lines'][0]['qty'] for v in hist] == [10.0, 8.0, 10.0]             # прежние версии целы
    st.store.save_day(DEMO, 'TEST', v2, 'v2', 'x')
    current = {s['stop_id']: s for s in st.store.day_stops(DEMO, 'TEST')}
    assert current[cash['stop_id']]['lines'][0]['qty'] == 8.0


def test_m1_office_past_date_does_not_reload_erp(client, st, monkeypatch, now):
    _, _, h = make_terminal(st, car='991AT61')
    calls = []
    monkeypatch.setattr(st.days, 'get', lambda car, d: calls.append((car, d)) or {'stops': []})
    assert client.get('/api/courier/admin/today?date=2026-10-01').status_code == 200
    assert client.get('/api/courier/admin/today?date=2000-01-01').status_code == 200
    assert calls == []                                                   # прошлое — только сохранённые снимки
    assert client.get('/api/courier/admin/today?date=2026-10-02').status_code == 200
    assert client.get('/api/courier/admin/today?date=2026-10-03').status_code == 200
    assert calls == [('991AT61', date(2026, 10, 2)), ('991AT61', date(2026, 10, 3))]


V1_SCHEMA = (
    "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE drivers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pin_hash TEXT, "
    "active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
    "updated_by TEXT)",
    "CREATE TABLE terminals(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, car_code TEXT NOT NULL, "
    "token_sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, created_by TEXT, revoked_at TEXT, revoked_by TEXT, "
    "failed_pin_count INTEGER NOT NULL DEFAULT 0, pin_window_start TEXT, locked_until TEXT, last_seen_at TEXT)",
    "CREATE TABLE sessions(token_sha256 TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, "
    "created_at TEXT NOT NULL, expires_at TEXT NOT NULL)",
    "CREATE TABLE events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, "
    "car_code TEXT NOT NULL, date TEXT NOT NULL, stop_id TEXT, type TEXT NOT NULL, at_device TEXT NOT NULL, "
    "at_utc TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL, flags TEXT NOT NULL DEFAULT '[]')",
    "CREATE INDEX events_stop ON events(stop_id, type)",
    "CREATE TABLE rejected_events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, "
    "date TEXT, type TEXT, received_at TEXT NOT NULL, error TEXT NOT NULL, message TEXT NOT NULL, body TEXT)",
    "CREATE TABLE scans(event_id TEXT PRIMARY KEY, raw TEXT NOT NULL, gtin TEXT, serial TEXT, is_group INTEGER NOT NULL, "
    "units REAL NOT NULL, kind TEXT NOT NULL, stop_id TEXT, line_id TEXT, customer_id INTEGER, customer_code TEXT, "
    "customer_name TEXT, tax_id TEXT, doc_number TEXT, product_id INTEGER, product_code TEXT, product_name TEXT, "
    "date TEXT NOT NULL, at_device TEXT NOT NULL, driver_id INTEGER NOT NULL, driver_name TEXT, car_code TEXT NOT NULL, "
    "counted INTEGER NOT NULL, duplicate_elsewhere INTEGER NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0, "
    "cancel_event_id TEXT)",
    "CREATE TABLE photos(id TEXT PRIMARY KEY, event_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL, "
    "size INTEGER NOT NULL, sha256 TEXT NOT NULL, terminal_id INTEGER NOT NULL, received_at TEXT NOT NULL)",
    "CREATE TABLE day_stops(date TEXT NOT NULL, stop_id TEXT NOT NULL, car_code TEXT NOT NULL, seq INTEGER NOT NULL, "
    "data TEXT NOT NULL, version TEXT NOT NULL, loaded_at TEXT NOT NULL, PRIMARY KEY (date, stop_id))",
    "CREATE TABLE cash_handover(date TEXT NOT NULL, driver_id INTEGER NOT NULL, handed REAL NOT NULL, comment TEXT, "
    "updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (date, driver_id))",
    "CREATE TABLE marked_products(product_id INTEGER PRIMARY KEY, marked INTEGER NOT NULL, pack_qty REAL, "
    "updated_at TEXT NOT NULL, updated_by TEXT)",
    "CREATE TABLE tare_custom(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
    "updated_at TEXT NOT NULL, updated_by TEXT)",
    "CREATE TABLE reasons(kind TEXT NOT NULL CHECK (kind IN ('refuse', 'return')), id TEXT NOT NULL, text TEXT NOT NULL, "
    "sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (kind, id))",
    "CREATE TABLE app_release(version_code INTEGER PRIMARY KEY, version_name TEXT NOT NULL, sha256 TEXT NOT NULL, "
    "size INTEGER NOT NULL, path TEXT NOT NULL, uploaded_at TEXT NOT NULL, uploaded_by TEXT)",
    "INSERT INTO meta(key, value) VALUES('schema_version', '1')",
)


def test_m1_migration_v1_to_v2(tmp_path, now):
    from courier.security import hash_pin
    path = tmp_path / 'old.db'
    stop = lambda sid, qty: json.dumps({'stop_id': sid, 'seq': 1, 'lines': [{'line_id': 'L:1', 'qty': qty}]})  # noqa: E731
    with closing(sqlite3.connect(path)) as conn:
        for ddl in V1_SCHEMA:
            conn.execute(ddl)
        conn.executemany('INSERT INTO day_stops VALUES(?, ?, ?, ?, ?, ?, ?)', [
            ('2026-10-01', 'S:A', 'CAR1', 1, stop('S:A', 5.0), 'v1', '2026-10-01T08:00:00+04:00'),
            ('2026-10-01', 'S:B', 'CAR1', 2, stop('S:B', 3.0), 'v1', '2026-10-01T08:00:00+04:00'),
            ('2026-10-01', 'S:C', 'CAR2', 1, stop('S:C', 1.0), 'w1', '2026-10-01T08:05:00+04:00')])
        conn.execute("INSERT INTO drivers(name, pin_hash, active, created_at, updated_at) VALUES('Old', ?, 0, 'x', 'x')",
                     (hash_pin('1234'),))
        conn.execute("INSERT INTO drivers(name, pin_hash, active, created_at, updated_at) VALUES('Live', ?, 1, 'x', 'x')",
                     (hash_pin('5555'),))
        conn.execute("INSERT INTO events(id, terminal_id, driver_id, car_code, date, stop_id, type, at_device, at_utc, "
                     "received_at, payload) VALUES('e1', 1, 2, 'CAR1', '2026-10-01', 'S:A', 'arrived', 'a', 'a', 'r', '{}')")
        conn.commit()
    s = Store(str(path))
    assert [x['stop_id'] for x in s.day_stops('2026-10-01')] == ['S:A', 'S:B', 'S:C']
    assert s.events_for_day('2026-10-01')[0]['snapshot_id'] is None
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        assert len(conn.execute("SELECT value FROM meta WHERE key = 'pin_salt'").fetchone()[0]) == 32
        assert conn.execute('SELECT COUNT(*) FROM day_snapshots').fetchone()[0] == 2
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'day_stops'").fetchone() is None
    # PIN до схемы 2: включить без нового PIN нельзя (сравнить нечем); вход дописывает pin_tag
    with pytest.raises(ValueError):
        s.save_driver(1, 'Old', True, None, 'admin')
    assert [d.name for d in s.match_pin('5555')] == ['Live']
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT pin_tag FROM drivers WHERE id = 2').fetchone()[0]
    with pytest.raises(PinConflict):
        s.save_driver(1, 'Old', True, '5555', 'admin')
    s.save_driver(1, 'Old', True, '7777', 'admin')
    assert [d.name for d in s.match_pin('7777')] == ['Old']


# --- M2 и v1.1 §4–6: версии точки, мягкие флаги ---

def test_m2_rejection_vs_flag_after_snapshot_change(term, client, st):
    v1 = term['day']['stops']
    cash = next(s for s in v1 if s['collect'] == 'cash')          # 1 строка: 10 шт
    none = next(s for s in v1 if s['collect'] == 'none')          # 2 строки
    removed_line = none['lines'][1]

    def change(s):
        if s['stop_id'] == cash['stop_id']:
            s['lines'][0]['qty'] = 8.0                              # офис уменьшил накладную
        if s['stop_id'] == none['stop_id']:
            s['lines'] = s['lines'][:1]                             # и убрал строку
    st.store.save_day(DEMO, 'TEST', _stops_v2(v1, change), 'v2', '2000-01-01T11:00:00+04:00')
    over = event('delivery', cash['stop_id'], {'lines': [{'line_id': cash['lines'][0]['line_id'], 'qty': 10.0}]})
    exact = event('delivery', cash['stop_id'], {'lines': [{'line_id': cash['lines'][0]['line_id'], 'qty': 8.0}]},
                  at='2000-01-01T10:05:00+04:00')
    too_much = event('delivery', cash['stop_id'], {'lines': [{'line_id': cash['lines'][0]['line_id'], 'qty': 10.5}]})
    gone = event('delivery', none['stop_id'], {'lines': [{'line_id': none['lines'][0]['line_id'], 'qty': 24.0},
                                                         {'line_id': removed_line['line_id'], 'qty': 15.0}]})
    unknown = event('delivery', none['stop_id'], {'lines': [{'line_id': 'X:9', 'qty': 1.0}]})
    r = post(client, term['s'], over, exact, too_much, gone, unknown)
    assert sorted(r['accepted']) == sorted([over['id'], exact['id'], gone['id']])
    assert {x['id'] for x in r['rejected']} == {too_much['id'], unknown['id']}
    flags = {e['id']: e['flags'] for e in st.store.events_for_day(DEMO)}
    assert 'qty_over_invoice' in flags[over['id']] and 'qty_over_invoice' not in flags[exact['id']]
    assert 'qty_over_invoice' in flags[gone['id']]                 # строки нет в новой версии, но была в прежней
    snap = {e['id']: e['snapshot_id'] for e in st.store.events_for_day(DEMO)}
    with closing(sqlite3.connect(st.store.path)) as conn:
        newest = conn.execute('SELECT MAX(id) FROM day_snapshots').fetchone()[0]
    assert snap[over['id']] == newest                               # проверено по действующей версии


def test_v11_no_reason_and_date_suspicious_flags(term, client, st):
    s1 = next(s for s in term['day']['stops'] if s['collect'] == 'cash')
    partial = event('delivery', s1['stop_id'], {'lines': full_lines(s1, 0.5), 'reason_id': None})
    refuse = event('delivery', s1['stop_id'], {'lines': full_lines(s1, 0)}, at='2000-01-01T11:00:00+04:00')
    far = event('arrived', s1['stop_id'], {'lat': 40.1, 'lon': 44.5}, at='2000-01-04T10:00:00+04:00')
    near = event('arrived', s1['stop_id'], {'lat': 40.1, 'lon': 44.5}, at='2000-01-03T10:00:00+04:00')
    late_utc = event('arrived', s1['stop_id'], {'lat': 40.1, 'lon': 44.5}, at='2000-01-02T22:30:00Z')   # 03.01 Ереван
    r = post(client, term['s'], partial, refuse, far, near, late_utc)
    assert len(r['accepted']) == 5 and r['rejected'] == []
    flags = {e['id']: e['flags'] for e in st.store.events_for_day(DEMO)}
    assert 'no_reason' in flags[partial['id']] and 'no_reason' in flags[refuse['id']]
    assert 'date_suspicious' in flags[far['id']]
    assert 'date_suspicious' not in flags[near['id']] and 'date_suspicious' not in flags[late_utc['id']]


# --- M3: заказы → накладные ---

def test_m3_load_day_per_customer_source(fake_erp, tmp_path):
    """Клиент с накладной машины — S: (с replaces); без накладной — O:; заказ, по которому накладная уже есть
    (на другой машине), — не точка этой машины."""
    d = date(2026, 10, 2)
    fake_erp.sales = [(ISN[0], '000318001', 11, 7, '1', 18000)]
    fake_erp.parents = [(ISN[0], ISN[2])]
    fake_erp.orders = [(ISN[2], 'Z0011', date(2026, 10, 1), 11, 7, '991AT61', 18000, 10, d, 0),     # стал накладной
                       (ISN[1], 'Z0012', date(2026, 10, 1), 12, 7, '991AT61', 3600, 5, None, 0),    # накладной нет
                       (ISN[3], 'Z0013', date(2026, 10, 1), 13, 7, '991AT61', 900, 1, d, 0)]        # накладная у другой
    data = ed.load_day('cs', '991AT61', d, (date(2026, 9, 29), d), lambda orders: list(orders))
    assert [(x.stop_id, x.replaces) for x in data.docs] == [(f'S:{ISN[0]}', (f'O:{ISN[2]}',)), (f'O:{ISN[1]}', ())]
    view = rl.RoutesView(depot=YEREVAN)
    body = dy.day_payload(data, view, Store(str(tmp_path / 'c.db')), datetime(2026, 10, 2, 8, 0, tzinfo=clock.YEREVAN))
    stops = {s['stop_id']: s for s in body['stops']}
    assert stops[f'S:{ISN[0]}']['replaces'] == [f'O:{ISN[2]}'] and 'replaces' not in stops[f'O:{ISN[1]}']
    assert stops[f'O:{ISN[1]}']['source'] == 'order'


ORDER_ISN = '0000000A-0000-4000-8000-00000000000A'


def test_m3_order_events_attributed_to_invoice(term, client, st):
    """Водитель работал по заказу O: (накладной ещё не было), потом появилась накладная S: с replaces —
    офис («Առաքում այսօր», «Գումար», «Մակնշում») показывает события у накладной."""
    v1 = term['day']['stops']
    cash = next(s for s in v1 if s['collect'] == 'cash')
    marked = next(s for s in v1 if any(ln['marked'] for ln in s['lines']))
    order = json.loads(json.dumps(cash))
    order.update(stop_id=f'O:{ORDER_ISN}', source='order', doc_number='Z-77')
    order['lines'] = [{**order['lines'][0], 'line_id': f'{ORDER_ISN}:1'}]
    others = [s for s in v1 if s['stop_id'] != cash['stop_id']]
    st.store.save_day(DEMO, 'TEST', [order, *others], 'with-order', '2000-01-01T08:00:00+04:00')
    d = event('delivery', order['stop_id'], {'lines': [{'line_id': f'{ORDER_ISN}:1', 'qty': 10.0}]})
    pay = event('payment', order['stop_id'], {'amount': 12000.0, 'kind': 'invoice'})
    sc = event('scan', order['stop_id'], {'raw': 'ORDER-CODE-1', 'line_id': f'{ORDER_ISN}:1', 'kind': 'sale', 'gtin': None,
                                          'serial': None, 'is_group': False, 'units': 1})
    unread = event('unreadable', order['stop_id'], {'line_id': f'{ORDER_ISN}:1', 'qty': 1.0, 'reason': 'x'})
    assert len(post(client, term['s'], d, pay, sc, unread)['accepted']) == 4
    invoice = {**json.loads(json.dumps(cash)), 'replaces': [order['stop_id']]}
    st.store.save_day(DEMO, 'TEST', [invoice, *others], 'with-invoice', '2000-01-01T09:00:00+04:00')

    today = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()
    car = today['cars'][0]
    row = next(s for s in car['stops'] if s['stop_id'] == cash['stop_id'])
    assert row['status'] == 'full' and row['doc_number'] == cash['doc_number']   # по строкам заказа — полностью
    assert car['removed'] == [] and car['full'] == 1
    fl = next(f for f in today['flagged'] if f['id'] == unread['id'])
    assert fl['stop_id'] == cash['stop_id'] and fl['orig_stop_id'] == order['stop_id']
    assert fl['doc_number'] == cash['doc_number'] and fl['customer'] == cash['customer']['name']

    money = client.get(f'/api/courier/admin/money?date={DEMO}').get_json()['drivers'][0]
    mrow = next(r for r in money['rows'] if r['stop_id'] == cash['stop_id'])
    assert (mrow['doc_number'], mrow['invoice'], mrow['expected'], mrow['short']) == (cash['doc_number'], 12000.0,
                                                                                        12000.0, 0.0)
    assert 'no_payment' not in mrow['flags'] and len(money['rows']) == 1

    marks = client.get('/api/courier/admin/marks?q=ORDER-CODE').get_json()['rows']
    assert marks[0]['doc_number'] == cash['doc_number'] and marks[0]['order_number'] == 'Z-77'
    assert 'Z-77' in client.get(f'/api/courier/admin/marks.csv?from={DEMO}').data.decode('utf-8')
    assert marked  # демо-день с маркируемой строкой цел


# --- M4: «Գումար» — сколько надо взять ---

def test_m4_money_expected_and_no_payment(term, client):
    stops = {s['collect']: s for s in term['day']['stops']}
    cash, ecr, none = stops['cash'], stops['cash_ecr'], stops['none']
    post(client, term['s'],
         event('delivery', cash['stop_id'], {'lines': full_lines(cash, 0.6), 'reason_id': 'no_money'}),      # 6 × 1200
         event('delivery', ecr['stop_id'], {'lines': full_lines(ecr)}),                                       # 12 × 700
         event('payment', ecr['stop_id'], {'amount': 8000.0, 'kind': 'invoice', 'ecr_receipt': '55'}),
         event('delivery', none['stop_id'], {'lines': full_lines(none)}))
    drv = client.get(f'/api/courier/admin/money?date={DEMO}').get_json()['drivers'][0]
    rows = {r['stop_id']: r for r in drv['rows']}
    assert set(rows) == {cash['stop_id'], ecr['stop_id']}          # «none» без оплаты — не к сдаче
    c, e = rows[cash['stop_id']], rows[ecr['stop_id']]
    assert (c['due'], c['invoice_amount'], c['expected'], c['invoice'], c['short']) == (7200.0, 12000.0, 7200.0,
                                                                                        0.0, 7200.0)
    assert 'no_payment' in c['flags'] and c['status'] == 'partial'
    assert (e['expected'], e['invoice'], e['short']) == (8400.0, 8000.0, 400.0) and 'no_payment' not in e['flags']
    assert (drv['expected'], drv['collected_invoice'], drv['expected_short'], drv['no_payment']) == (15600.0, 8000.0,
                                                                                                    7600.0, 1)
    post(client, term['s'], event('delivery', cash['stop_id'], {'lines': full_lines(cash, 0), 'reason_id': 'closed'},
                                  at='2000-01-01T12:00:00+04:00'))
    c = {r['stop_id']: r for r in client.get(f'/api/courier/admin/money?date={DEMO}').get_json()['drivers'][0]['rows']}
    assert c[cash['stop_id']]['expected'] == 0.0 and 'no_payment' not in c[cash['stop_id']]['flags']   # отказ — 0


# --- M5: фото в офисе ---

def test_m5_admin_photo_viewer(term, client, st, tmp_path):
    stop = term['day']['stops'][0]
    unread = event('unreadable', stop['stop_id'], {'line_id': stop['lines'][0]['line_id'], 'qty': 1.0, 'reason': 'x'})
    post(client, term['s'], unread)
    form = photo_form(JPEG, event_id=unread['id'])
    pid = form['id']
    assert client.post('/api/courier/v1/photos', data=form, headers=term['s'],
                       content_type='multipart/form-data').status_code == 200
    r = client.get(f'/api/courier/admin/photos/{pid}')
    assert r.status_code == 200 and r.data == JPEG and r.mimetype == 'image/jpeg'
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert client.get(f'/api/courier/admin/photos/{pid.upper()}').status_code == 200
    for bad in ('nope', '..%2F..%2Fcourier.db', str(uuid.uuid4())):
        assert client.get(f'/api/courier/admin/photos/{bad}').status_code == 404
    # путь в базе ведёт наружу — не отдаём
    secret = tmp_path / 'secret.jpg'
    secret.write_bytes(JPEG)
    evil = str(uuid.uuid4())
    with closing(sqlite3.connect(st.store.path)) as conn:
        conn.execute("INSERT INTO photos VALUES(?, ?, 'photo', ?, 1, 'x', 1, 'x')", (evil, unread['id'], '../secret.jpg'))
        conn.execute("INSERT INTO photos VALUES(?, ?, 'photo', ?, 1, 'x', 1, 'x')",
                     (str(uuid.uuid4()), unread['id'], str(secret)))
        conn.commit()
    assert client.get(f'/api/courier/admin/photos/{evil}').status_code == 404
    today = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()
    assert pid in [p['id'] for f in today['photo_events'] for p in f['photos']]
    assert unread['id'] in {f['id'] for f in today['photo_events']}


# --- доступ: роль user ---

def test_role_user_blocked_from_courier_office(dashboard, monkeypatch):
    import app_v2
    client, _ = dashboard
    users = {'u': {'role': 'user', 'areas': ['01'], 'password_hash': 'x'},
             'boss': {'role': 'admin', 'areas': [], 'password_hash': 'x'}}
    monkeypatch.setattr(app_v2, 'load_users', lambda: users)
    lan = {}   # localhost: внутренняя сеть (cookie сессии теста — на localhost)
    with client.session_transaction() as sess:
        sess['username'] = 'u'
    r = client.get('/courier', headers=lan)
    assert r.status_code in (302, 403) and '/courier' not in r.headers.get('Location', '')
    for path in ('/api/courier/admin/today', '/api/courier/admin/drivers', '/api/courier/admin/money',
                 f'/api/courier/admin/photos/{uuid.uuid4()}', '/api/courier/admin/marks.csv'):
        assert client.get(path, headers=lan).status_code == 403, path
    assert client.post('/api/courier/admin/drivers', json={'name': 'x', 'pin': '1234'}, headers=lan).status_code == 403
    with client.session_transaction() as sess:
        sess['username'] = 'boss'
    assert client.get('/api/courier/admin/drivers', headers=lan).status_code == 200


# --- L2–L11 ---

def test_l2_payment_cancel_lookup_uses_index(term, st):
    from courier.store import EventTx
    with closing(sqlite3.connect(st.store.path)) as conn:
        plan = ' '.join(str(r) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM events WHERE stop_id = ? AND type = 'payment' "
            "AND json_extract(payload, '$.cancel_of') = ?", ('S:x', 'y')).fetchall())
        assert 'events_stop' in plan and 'SCAN events' not in plan
        assert EventTx(conn).payment_cancelled('y', 'S:x') is False


def test_l3_scan_matched_by_gtin_counts_for_line(term, client, st):
    stop = next(s for s in term['day']['stops'] if any(ln['marked'] for ln in s['lines']))
    line = next(ln for ln in stop['lines'] if ln['marked'])          # 24 шт, упаковка 6
    scans = [event('scan', stop['stop_id'], {'raw': f'01{line["gtins"][0]}21S{i}', 'line_id': None, 'kind': 'sale',
                                             'gtin': line['gtins'][0], 'serial': f'S{i}', 'is_group': True, 'units': 6})
             for i in range(4)]
    d = event('delivery', stop['stop_id'], {'lines': full_lines(stop)}, at='2000-01-01T11:00:00+04:00')
    post(client, term['s'], *scans, d)
    with closing(sqlite3.connect(st.store.path)) as conn:
        assert {r[0] for r in conn.execute('SELECT line_id FROM scans')} == {line['line_id']}
    flags = {e['id']: e['flags'] for e in st.store.events_for_day(DEMO)}
    assert 'scan_short' not in flags[d['id']]


def test_l4_reactivation_runs_duplicate_pin_check(client, st):
    a = st.store.save_driver(None, 'Ա', True, '1234', 'admin')
    st.store.save_driver(a, 'Ա', False, None, 'admin')
    st.store.save_driver(None, 'Բ', True, '1234', 'admin')            # PIN свободен: Ա выключен
    r = client.post('/api/courier/admin/drivers', json={'id': a, 'name': 'Ա', 'active': True})
    assert r.status_code == 400 and 'PIN' in r.get_json()['error']
    assert not st.store.driver(a).active
    assert client.post('/api/courier/admin/drivers', json={'id': a, 'name': 'Ա', 'active': True,
                                                           'pin': '4321'}).status_code == 200
    c = st.store.save_driver(None, 'Գ', False, '9999', 'admin')
    assert client.post('/api/courier/admin/drivers', json={'id': c, 'name': 'Գ', 'active': True}).status_code == 200


def test_l5_pin_change_revokes_sessions(client, st):
    did, _, h = make_terminal(st)
    s = login(client, h)
    st.store.save_driver(did, 'Արամ', True, None, 'admin')            # без нового PIN — сессия жива
    assert client.get('/api/courier/v1/status', headers=s).status_code == 200
    st.store.save_driver(did, 'Արամ', True, '8765', 'admin')
    assert client.get('/api/courier/v1/status', headers=s).get_json()['error'] == 'session'
    assert client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h).status_code == 403
    login(client, h, '8765')


def test_l6_session_capped_at_20_hours(client, st, now):
    _, _, h = make_terminal(st)
    now['t'] = datetime(2026, 10, 2, 7, 0, tzinfo=clock.YEREVAN)
    r = client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h).get_json()
    assert r['expires_at'] == '2026-10-03T03:00:00+04:00'
    now['t'] = datetime(2026, 10, 2, 23, 30, tzinfo=clock.YEREVAN)
    r = client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h).get_json()
    assert r['expires_at'] == '2026-10-03T04:00:00+04:00'
    assert clock.session_expiry(datetime(2026, 10, 2, 0, 30, tzinfo=clock.YEREVAN)) == \
        datetime(2026, 10, 2, 20, 30, tzinfo=clock.YEREVAN)


def test_l7_last_seen_write_never_fails_request(client, st):
    _, terminal, h = make_terminal(st)
    blocker = sqlite3.connect(st.store.path, isolation_level=None)
    try:
        blocker.execute('BEGIN IMMEDIATE')                                # база занята другой записью
        r = client.get('/api/courier/v1/ping', headers=h)
        assert r.status_code == 200 and r.get_json()['ok'] is True
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()
    assert st.store.terminal(terminal.id).last_seen_at is None            # не записано — и ладно
    assert client.get('/api/courier/v1/ping', headers=h).status_code == 200
    assert st.store.terminal(terminal.id).last_seen_at is not None


def test_l8_daily_limits(term, client, st, monkeypatch):
    from courier import api, store as cs
    monkeypatch.setattr(cs, 'PHOTOS_PER_DAY', 2)
    monkeypatch.setattr(api, 'PHOTOS_PER_DAY', 2)
    send = lambda data=JPEG: client.post('/api/courier/v1/photos', data=photo_form(data), headers=term['s'],  # noqa: E731
                                         content_type='multipart/form-data')
    assert send().status_code == 200 and send().status_code == 200
    r = send()
    assert r.status_code == 429 and r.get_json()['error'] == 'too_large' and r.get_json()['retry_after'] > 0
    monkeypatch.setattr(cs, 'PHOTOS_PER_DAY', 100)
    monkeypatch.setattr(api, 'PHOTOS_PER_DAY', 100)
    monkeypatch.setattr(cs, 'PHOTO_BYTES_PER_DAY', 3 * len(JPEG) - 1)
    r = send()
    assert r.status_code == 413 and r.get_json()['error'] == 'too_large'
    assert len(list(Path(st.store.photos_dir).rglob('*.jpg'))) == 2        # отклонённый файл не остался
    # отказы: сохраняется не больше предела, в ответе — все
    monkeypatch.setattr(ev, 'REJECTED_PER_DAY', 2)
    bad = [event('payment', term['day']['stops'][0]['stop_id'], {'amount': 0, 'kind': 'invoice'}) for _ in range(4)]
    assert len(post(client, term['s'], *bad)['rejected']) == 4
    with closing(sqlite3.connect(st.store.path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM rejected_events').fetchone()[0] == 2


def test_l11_reference_data_cached(fake_erp):
    fake_erp.sales = [(ISN[0], '000318001', 11, 7, '1', 18000)]
    for _ in range(2):
        ed.load_day('cs', '991AT61', date(2026, 10, 2), (date(2026, 9, 29), date(2026, 10, 2)), lambda o: [])
    for sql in (ed.SQL_CONTAINERS, ed.SQL_DEFAULT_POINTS, erp.SQL_AGENTS, erp.SQL_CARS):
        assert fake_erp.calls.count(sql) == 1
    assert fake_erp.calls.count(ed.SQL_DAY_SALES) == 2                   # данные дня — каждый раз


# ============================== вторая проверка: деньги, связи O: → S:, хранение, перец ==============================

PAST = '2026-10-01'      # вчера (часы тестов — 02.10.2026): офис читает только сохранённое, без ERP


def _uid(n):
    return '%08d-1111-4111-8111-111111111111' % n


def _stop(sid, lines, collect='cash', replaces=None, seq=1, marked=False, product=1):
    d = {'stop_id': sid, 'seq': seq, 'collect': collect, 'doc_number': sid[-4:] + str(seq),
         'customer': {'id': 1, 'name': 'C'}, 'amount_due': sum(q * p for _, q, p in lines),
         'lines': [{'line_id': lid, 'qty': q, 'price': p, 'product_id': product, 'marked': marked}
                   for lid, q, p in lines]}
    if replaces:
        d['replaces'] = replaces
    return d


def _who(st, name, pin, car='CAR1'):
    did = st.store.save_driver(None, name, True, pin, 'admin')
    terminal, _ = st.store.create_terminal('U-' + name, car, 'admin')
    return ev.Who(terminal.id, car, did, name)


def _ev(etype, sid, payload, at, day=PAST):
    return {'id': eid(), 'type': etype, 'stop_id': sid, 'date': day, 'at': f'{day}T{at}+04:00', 'payload': payload}


def _ingest(st, who, *events):
    r = ev.ingest(st.store, who, list(events)).json()
    assert r['rejected'] == [], r['rejected']
    return r


def _deliver(sid, lines, at, reason=None):
    return _ev('delivery', sid, {'lines': [{'line_id': lid, 'qty': q} for lid, q in lines], 'reason_id': reason}, at)


def _money(client, day=PAST):
    body = client.get(f'/api/courier/admin/money?date={day}').get_json()
    assert body['success'], body
    return {d['name']: d for d in body['drivers']}


def _today_stops(client, day=PAST):
    body = client.get(f'/api/courier/admin/today?date={day}').get_json()
    assert body['success'], body
    return {s['stop_id']: s for c in body['cars'] for s in c['stops']}


def test_ma_merged_orders_keep_every_delivery(st, client):
    """M-A: заказы O:1 (1000) и O:2 (500) доставлены и оплачены, потом объединены в накладную S1 — доставка
    обоих видна у S1 (было: 500 к оплате при оплате 1500, «недостача» −1000)."""
    a = _who(st, 'A', '1111')
    o1, o2, s1 = 'O:' + _uid(1), 'O:' + _uid(2), 'S:' + _uid(3)
    st.store.save_day(PAST, 'CAR1', [_stop(o1, [('o1:1', 10, 100)]), _stop(o2, [('o2:1', 5, 100)], seq=2)], 'v1',
                      PAST + 'T08:00:00+04:00')
    _ingest(st, a, _deliver(o1, [('o1:1', 10)], '10:00:00'), _deliver(o2, [('o2:1', 5)], '10:05:00'),
            _ev('payment', o1, {'amount': 1000, 'kind': 'invoice'}, '10:06:00'),
            _ev('payment', o2, {'amount': 500, 'kind': 'invoice'}, '10:07:00'))
    st.store.save_day(PAST, 'CAR1', [_stop(s1, [('s1:1', 10, 100), ('s1:2', 5, 100)], replaces=[o1, o2])], 'v2',
                      PAST + 'T13:00:00+04:00')
    (row,) = _money(client)['A']['rows']
    assert (row['stop_id'], row['due'], row['expected'], row['invoice'], row['short'], row['status']) == \
        (s1, 1500.0, 1500.0, 1500.0, 0.0, 'full')
    assert row['flags'] == []
    assert _today_stops(client)[s1]['status'] == 'full'
    # один заказ довезли частично — накладная «частично», сумма по обоим
    _ingest(st, a, _deliver(o2, [('o2:1', 2)], '10:30:00', reason='no_money'))
    (row,) = _money(client)['A']['rows']
    assert (row['due'], row['status']) == (1200.0, 'partial')
    assert _today_stops(client)[s1]['status'] == 'partial'
    # терминал узнал о накладной и записал её целиком позже всех заказов — действует только она
    _ingest(st, a, _deliver(s1, [('s1:1', 10), ('s1:2', 5)], '11:00:00'))
    (row,) = _money(client)['A']['rows']
    assert (row['due'], row['short'], row['status']) == (1500.0, 0.0, 'full')


def test_mb_expected_once_for_effective_driver(st, client):
    """M-B: «надо взять» — один раз, у водителя действующей доставки; оплата другого водителя засчитывается
    (флаг «վերցրել է այլ վարորդ» вместо «վճարում չկա»)."""
    a, b = _who(st, 'A', '1111'), _who(st, 'B', '2222')
    s2, s3 = 'S:' + _uid(4), 'S:' + _uid(5)
    st.store.save_day(PAST, 'CAR1', [_stop(s2, [('s2:1', 4, 250)]), _stop(s3, [('s3:1', 2, 100)], seq=2)], 'v1',
                      PAST + 'T08:00:00+04:00')
    _ingest(st, a, _deliver(s2, [('s2:1', 4)], '11:00:00'), _deliver(s3, [('s3:1', 2)], '11:05:00'))
    _ingest(st, b, _ev('payment', s2, {'amount': 1000, 'kind': 'invoice'}, '12:00:00'))
    m = _money(client)
    ra, rb = {r['stop_id']: r for r in m['A']['rows']}, {r['stop_id']: r for r in m['B']['rows']}
    assert (ra[s2]['expected'], ra[s2]['invoice'], ra[s2]['invoice_all'], ra[s2]['short']) == (1000.0, 0.0, 1000.0, 0.0)
    assert 'collected_by_other' in ra[s2]['flags'] and 'no_payment' not in ra[s2]['flags']
    assert ra[s3]['expected'] == 200.0 and 'no_payment' in ra[s3]['flags']
    assert (m['A']['expected'], m['A']['expected_short'], m['A']['no_payment']) == (1200.0, 200.0, 1)
    assert set(rb) == {s2} and (rb[s2]['expected'], rb[s2]['short'], rb[s2]['invoice']) == (None, None, 1000.0)
    assert (m['B']['expected'], m['B']['expected_short'], m['B']['collected_invoice']) == (0.0, 0.0, 1000.0)
    assert sum(d['expected'] for d in m.values()) == 1200.0                 # каждая точка — один раз
    # B довёз s3 позже — ожидание переходит к нему, у A строки s3 нет
    _ingest(st, b, _deliver(s3, [('s3:1', 2)], '13:00:00'))
    m = _money(client)
    assert {r['stop_id'] for r in m['A']['rows']} == {s2} and m['A']['expected'] == 1000.0
    rb = {r['stop_id']: r for r in m['B']['rows']}
    assert rb[s3]['expected'] == 200.0 and 'no_payment' in rb[s3]['flags'] and m['B']['expected'] == 200.0


def test_l1_order_mapping_prefers_newest_and_handles_split(st, client):
    """L1 (вторая проверка) по правилу v1.2: заказ — у накладной текущего /day (переделанная накладная не уходит к
    старой); заказ, разделённый на две накладные, — деньги один раз у владельца, сестра «covered» (пример 11), обе
    `split_order`; накладные убрали из /day — заказ со своими событиями «հանված է» (пример 18)."""
    a = _who(st, 'A', '1111')
    o, sa, sb, sc = 'O:' + _uid(10), 'S:' + _uid(11), 'S:' + _uid(12), 'S:' + _uid(13)
    st.store.save_day(PAST, 'CAR1', [_stop(o, [('o:1', 10, 100)])], 'v1', PAST + 'T08:00:00+04:00')
    _ingest(st, a, _deliver(o, [('o:1', 10)], '10:00:00'),
            _ev('payment', o, {'amount': 1000, 'kind': 'invoice'}, '10:01:00'))
    st.store.save_day(PAST, 'CAR1', [_stop(sa, [('a:1', 10, 100)], replaces=[o])], 'v2', PAST + 'T09:00:00+04:00')
    (row,) = _money(client)['A']['rows']
    assert (row['stop_id'], row['status'], row['expected']) == (sa, 'full', 1000.0)
    st.store.save_day(PAST, 'CAR1', [_stop(sb, [('b:1', 10, 100)], replaces=[o])], 'v3', PAST + 'T10:00:00+04:00')
    (row,) = _money(client)['A']['rows']
    assert (row['stop_id'], row['expected'], row['invoice'], row['short']) == (sb, 1000.0, 1000.0, 0.0)
    assert set(_today_stops(client)) == {sb}
    # заказ разделён на две накладные
    st.store.save_day(PAST, 'CAR1', [_stop(sb, [('b:1', 6, 100)], replaces=[o]),
                                     _stop(sc, [('c:1', 4, 100)], replaces=[o], seq=2)], 'v4', PAST + 'T11:00:00+04:00')
    m = _money(client)['A']
    (row,) = m['rows']
    assert (row['stop_id'], row['due'], row['invoice'], m['expected']) == (sb, 1000.0, 1000.0, 1000.0)
    assert 'split_order' in row['flags']
    stops = _today_stops(client)
    assert (stops[sb]['status'], stops[sb]['due'], stops[sb]['paid'], stops[sb]['flags']) == \
        ('full', 1000.0, 1000.0, ['split_order'])
    assert (stops[sc]['status'], stops[sc]['due'], stops[sc]['flags']) == ('covered', 0.0, ['split_order'])
    # накладные убрали из выдачи — заказ со своими событиями показывается сам по себе, «հանված է»
    st.store.save_day(PAST, 'CAR1', [], 'v5', PAST + 'T12:00:00+04:00')
    body = client.get(f'/api/courier/admin/today?date={PAST}').get_json()
    (car,) = body['cars']
    assert car['stops'] == [] and [(r['stop_id'], r['status'], r['due'], r['removed']) for r in car['removed']] == \
        [(o, 'full', 1000.0, True)]
    (row,) = _money(client)['A']['rows']
    assert (row['stop_id'], row['removed'], row['expected'], row['short']) == (o, True, 1000.0, 0.0)


def test_l2_cancelled_payment_receipt_hidden(st, client):
    a = _who(st, 'A', '1111')
    s = 'S:' + _uid(20)
    st.store.save_day(PAST, 'CAR1', [_stop(s, [('s:1', 1, 1000)], collect='cash_ecr')], 'v1', PAST + 'T08:00:00+04:00')
    pay = _ev('payment', s, {'amount': 1000, 'kind': 'invoice', 'ecr_receipt': '77'}, '10:00:00')
    _ingest(st, a, _deliver(s, [('s:1', 1)], '09:59:00'), pay,
            _ev('payment', s, {'amount': 1000, 'kind': 'invoice', 'cancel_of': pay['id']}, '10:01:00'),
            _ev('payment', s, {'amount': 1000, 'kind': 'invoice', 'ecr_receipt': '78'}, '10:02:00'))
    (row,) = _money(client)['A']['rows']
    assert (row['receipts'], row['invoice'], row['short']) == (['78'], 1000.0, 0.0)


def test_l3_scans_on_replaced_order_cover_invoice_line(st):
    """L3: сканы и «не читается» по заказу O: засчитываются строке накладной S: с тем же товаром."""
    a = _who(st, 'A', '1111')
    o, s = 'O:' + _uid(30), 'S:' + _uid(31)
    st.store.save_day(PAST, 'CAR1', [_stop(o, [('o:1', 3, 100)], marked=True, product=55)], 'v1',
                      PAST + 'T08:00:00+04:00')

    def scan(raw):
        return _ev('scan', o, {'raw': raw, 'line_id': 'o:1', 'kind': 'sale', 'gtin': None, 'serial': None,
                               'is_group': False, 'units': 1}, '10:00:00')
    s1, s2 = scan('MARK-1'), scan('MARK-2')
    _ingest(st, a, s1, s2, _ev('unreadable', o, {'line_id': 'o:1', 'qty': 1, 'reason': 'x'}, '10:01:00'))
    st.store.save_day(PAST, 'CAR1', [_stop(s, [('s:1', 3, 100)], marked=True, product=55, replaces=[o]),
                                     _stop('S:' + _uid(32), [('t:1', 1, 100)], marked=True, product=56, seq=2)],
                      'v2', PAST + 'T09:00:00+04:00')
    ok = _deliver(s, [('s:1', 3)], '11:00:00')
    _ingest(st, a, ok)
    _ingest(st, a, _ev('scan_cancel', None, {'scan_event_id': s1['id']}, '11:05:00'))
    short = _deliver(s, [('s:1', 3)], '11:10:00')
    _ingest(st, a, short)
    flags = {e['id']: e['flags'] for e in st.store.events_for_day(PAST)}
    assert 'scan_short' not in flags[ok['id']]                           # 2 скана + 1 «не читается» по заказу
    assert 'scan_short' in flags[short['id']]                            # скан отменён — не хватает


def test_l4_snapshot_contents_stored_once_and_retention(st, now):
    a = _who(st, 'A', '1111')
    stops = [_stop('S:' + _uid(40 + i), [('l:1', 1, 10)], seq=i + 1) for i in range(5)]
    sids = []

    def save(v):
        stops[0]['lines'][0]['qty'] = float(v + 1)                       # офис правит одну накладную из пяти
        sids.append(st.store.save_day(PAST, 'CAR1', stops, f'v{v}', PAST + 'T08:00:00+04:00'))
    save(0)
    save(1)
    _ingest(st, a, _ev('arrived', stops[1]['stop_id'], {'lat': 40.1, 'lon': 44.5}, '10:00:00'))   # ссылка на снимок v1
    save(2)
    save(3)
    with closing(sqlite3.connect(st.store.path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM snapshot_stops').fetchone()[0] == 20
        assert conn.execute('SELECT COUNT(*) FROM stop_data').fetchone()[0] == 5 + 3      # не 20
        assert conn.execute('SELECT snapshot_id FROM events').fetchone()[0] == sids[1]
    hist = st.store.stop_versions([stops[0]['stop_id']])[stops[0]['stop_id']]
    assert [v['data']['lines'][0]['qty'] for v in hist] == [1.0, 2.0, 3.0, 4.0]
    now['t'] = NOW + timedelta(days=6)
    st.store.save_day('2026-10-08', 'CAR1', stops[:1], 'w', '2026-10-08T08:00:00+04:00')
    assert len(st.store.stop_versions([stops[0]['stop_id']])[stops[0]['stop_id']]) == 5      # моложе 7 дней — целы
    now['t'] = NOW + timedelta(days=8)
    st.store.save_day('2026-10-10', 'CAR1', stops[:1], 'w', '2026-10-10T08:00:00+04:00')
    with closing(sqlite3.connect(st.store.path)) as conn:
        kept = [r[0] for r in conn.execute("SELECT id FROM day_snapshots WHERE date = ? ORDER BY id", (PAST,))]
        assert kept == [sids[1], sids[3]]                                # на снимок есть событие + последний
        assert conn.execute('SELECT COUNT(*) FROM snapshot_stops WHERE snapshot_id NOT IN '
                            '(SELECT id FROM day_snapshots)').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM stop_data WHERE hash NOT IN '
                            '(SELECT data_hash FROM snapshot_stops)').fetchone()[0] == 0
    hist = st.store.stop_versions([stops[0]['stop_id']])[stops[0]['stop_id']]
    assert [(v['date'], v['data']['lines'][0]['qty']) for v in hist] == [(PAST, 2.0), (PAST, 4.0), ('2026-10-08', 4.0),
                                                                        ('2026-10-10', 4.0)]
    assert [x['stop_id'] for x in st.store.day_stops(PAST, 'CAR1')] == [x['stop_id'] for x in stops]


V2_SCHEMA = (   # схема 2, как её создаёт код e8a2894 (sqlite_master настоящей базы)
    "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE drivers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pin_hash TEXT, active INTEGER NOT NULL "
    "DEFAULT 1 CHECK (active IN (0, 1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT, pin_tag TEXT)",
    "CREATE INDEX drivers_pin_tag ON drivers(pin_tag)",
    "CREATE TABLE terminals(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, car_code TEXT NOT NULL, "
    "token_sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, created_by TEXT, revoked_at TEXT, revoked_by TEXT, "
    "failed_pin_count INTEGER NOT NULL DEFAULT 0, pin_window_start TEXT, locked_until TEXT, last_seen_at TEXT)",
    "CREATE TABLE sessions(token_sha256 TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, "
    "created_at TEXT NOT NULL, expires_at TEXT NOT NULL)",
    "CREATE INDEX sessions_terminal ON sessions(terminal_id)",
    "CREATE TABLE events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, "
    "car_code TEXT NOT NULL, date TEXT NOT NULL, stop_id TEXT, type TEXT NOT NULL, at_device TEXT NOT NULL, at_utc TEXT NOT NULL, "
    "received_at TEXT NOT NULL, payload TEXT NOT NULL, flags TEXT NOT NULL DEFAULT '[]', snapshot_id INTEGER)",
    "CREATE INDEX events_day ON events(date, car_code)",
    "CREATE INDEX events_stop ON events(stop_id, type)",
    "CREATE INDEX events_driver ON events(driver_id, date)",
    "CREATE TABLE rejected_events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, date TEXT, "
    "type TEXT, received_at TEXT NOT NULL, error TEXT NOT NULL, message TEXT NOT NULL, body TEXT)",
    "CREATE INDEX rejected_driver ON rejected_events(driver_id, date)",
    "CREATE INDEX rejected_terminal ON rejected_events(terminal_id, received_at)",
    V1_SCHEMA[7],   # scans — без изменений со схемы 1
    "CREATE INDEX scans_raw ON scans(raw)",
    "CREATE INDEX scans_date ON scans(date)",
    V1_SCHEMA[8],   # photos
    "CREATE INDEX photos_event ON photos(event_id)",
    "CREATE INDEX photos_terminal ON photos(terminal_id, received_at)",
    "CREATE TABLE day_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, car_code TEXT NOT NULL, "
    "version TEXT NOT NULL, loaded_at TEXT NOT NULL, saved_at TEXT NOT NULL)",
    "CREATE INDEX day_snapshots_day ON day_snapshots(date, car_code, id)",
    "CREATE TABLE snapshot_stops(snapshot_id INTEGER NOT NULL, stop_id TEXT NOT NULL, date TEXT NOT NULL, car_code TEXT "
    "NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (snapshot_id, stop_id))",
    "CREATE INDEX snapshot_stops_stop ON snapshot_stops(stop_id, snapshot_id)",
    "CREATE INDEX snapshot_stops_day ON snapshot_stops(date, snapshot_id)",
    "CREATE TABLE stop_replaces(order_stop_id TEXT NOT NULL, invoice_stop_id TEXT NOT NULL, date TEXT NOT NULL, "
    "PRIMARY KEY (order_stop_id, invoice_stop_id))",
    *V1_SCHEMA[10:15],   # cash_handover, marked_products, tare_custom, reasons, app_release
    "INSERT INTO meta(key, value) VALUES('schema_version', '2')",
    "INSERT INTO meta(key, value) VALUES('pin_salt', '00112233445566778899aabbccddeeff')",
)


def test_l4_migration_v2_to_v3(tmp_path, now):
    from courier.security import hash_pin, tag_base
    assert {x.split('(')[0][13:] for x in V2_SCHEMA if x.startswith('CREATE TABLE')} == {
        'meta', 'drivers', 'terminals', 'sessions', 'events', 'rejected_events', 'scans', 'photos', 'day_snapshots',
        'snapshot_stops', 'stop_replaces', 'cash_handover', 'marked_products', 'tare_custom', 'reasons', 'app_release'}
    path = tmp_path / 'v2.db'
    o, s, x = 'O:' + _uid(50), 'S:' + _uid(51), 'S:' + _uid(52)
    dump = lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True)  # noqa: E731
    x_stop, o_stop = _stop(x, [('x:1', 2, 10)], seq=2), _stop(o, [('o:1', 3, 10)])
    s_stop = _stop(s, [('s:1', 3, 10)], replaces=[o])
    with closing(sqlite3.connect(path)) as conn:
        for ddl in V2_SCHEMA:
            conn.execute(ddl)
        conn.executemany('INSERT INTO day_snapshots(id, date, car_code, version, loaded_at, saved_at) '
                         'VALUES(?, ?, ?, ?, ?, ?)',
                         [(1, PAST, 'CAR1', 'v1', 't', PAST + 'T08:00:00+04:00'),
                          (2, PAST, 'CAR1', 'v2', 't', PAST + 'T09:00:00+04:00')])
        conn.executemany('INSERT INTO snapshot_stops VALUES(?, ?, ?, ?, ?, ?)', [
            (1, o, PAST, 'CAR1', 1, dump(o_stop)), (1, x, PAST, 'CAR1', 2, dump(x_stop)),
            (2, s, PAST, 'CAR1', 1, dump(s_stop)), (2, x, PAST, 'CAR1', 2, dump(x_stop))])
        conn.execute('INSERT INTO stop_replaces VALUES(?, ?, ?)', (o, s, PAST))
        conn.execute("INSERT INTO drivers(name, pin_hash, active, created_at, updated_at, pin_tag) "
                     "VALUES('A', ?, 1, 'x', 'x', ?)",
                     (hash_pin('1234'), tag_base('1234', '00112233445566778899aabbccddeeff')))
        conn.execute("INSERT INTO terminals(name, car_code, token_sha256, created_at) VALUES('U', 'CAR1', 'h', 'x')")
        conn.execute("INSERT INTO events(id, terminal_id, driver_id, car_code, date, stop_id, type, at_device, at_utc, "
                     "received_at, payload, snapshot_id) VALUES('e1', 1, 1, 'CAR1', ?, ?, 'delivery', 'a', 'a', 'r', ?, 1)",
                     (PAST, o, json.dumps({'lines': [{'line_id': 'o:1', 'qty': 3}]})))
        conn.commit()
    st_ = Store(str(path))
    assert [(d['stop_id'], d['lines']) for d in st_.day_stops(PAST, 'CAR1')] == [(s, s_stop['lines']), (x, x_stop['lines'])]
    assert [v['snapshot_id'] for v in st_.stop_versions([x])[x]] == [1, 2]
    assert st_.stop_versions([o])[o][0]['data']['lines'] == o_stop['lines']
    assert [d.name for d in st_.match_pin('1234')] == ['A']
    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        assert version == str(SCHEMA_VERSION)   # v2 → v3 → … → текущая
        assert conn.execute('SELECT COUNT(*) FROM stop_data').fetchone()[0] == 3              # x — один раз
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'stop_replaces'").fetchone() is None   # v5
        assert conn.execute('SELECT stop_id, max_qty FROM line_max ORDER BY stop_id').fetchall() == \
            [(o, 3.0), (s, 3.0), (x, 2.0)]
        cols = {r[1] for r in conn.execute('PRAGMA table_info(snapshot_stops)')}
        assert 'data' not in cols and 'data_hash' in cols
        assert 'admin_pin_hash' in {r[1] for r in conn.execute('PRAGMA table_info(terminals)')}
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'snapshot_stops_v3'").fetchone() is None


def test_l5_plan_mismatch_not_read_for_past_dates(app, st, monkeypatch):
    from courier import views as cv
    calls = []
    monkeypatch.setattr(cv, 'routes_view', lambda state, d: calls.append(d) or rl.RoutesView())
    st.invoice_loader = lambda d: calls.append(('erp', d)) or ([], {})
    with app.test_request_context():
        assert cv.plan_mismatches(date(2026, 10, 1)) == {'plan_exists': None, 'items': [], 'past': True}
        assert calls == []                                                # ни ERP, ни route_optimizer
        cv.plan_mismatches(date(2026, 10, 2))
    assert calls == [date(2026, 10, 2)]


def test_admin_pin_in_registration_qr(client, st):
    """§5 п. 11: в QR — случайный 6-значный PIN настроек, свой у каждого терминала; в базе — только хеш."""
    from courier.security import check_pin
    out = [client.post('/api/courier/admin/terminals', json={'name': f'T{i}', 'car_code': 'TEST'}).get_json()
           for i in range(2)]
    pins = [json.loads(r['qr_text'])['admin_pin'] for r in out]
    assert all(len(p) == 6 and p.isdigit() for p in pins) and pins == [r['admin_pin'] for r in out]
    assert pins[0] != pins[1]
    with closing(sqlite3.connect(st.store.path)) as conn:
        hashes = [r[0] for r in conn.execute('SELECT admin_pin_hash FROM terminals ORDER BY id')]
    assert all(h and p not in h for h, p in zip(hashes, pins)) and check_pin(hashes[0], pins[0])


# ============================== третья проверка: правило v1.2 в офисе, перец, line_max, сканы по товару ==============================

def _stopp(sid, lines, collect='cash', replaces=None, seq=1):
    """Точка со своим товаром у каждой строки: lines — (line_id, qty, price, product_id)."""
    d = _stop(sid, [(lid, q, p) for lid, q, p, _ in lines], collect=collect, replaces=replaces, seq=seq)
    for ln, (_, _, _, pid) in zip(d['lines'], lines):
        ln['product_id'] = pid
    return d


def test_v12_office_in_progress_covered_conflict(st, client):
    """«Առաքում այսօր» и «Գումար» по правилу v1.2, как на терминале: in_progress (пример 06) — due на сейчас, у
    водителя заявления; covered (пример 11) — сестра разделённого заказа, ждать 0; merge_conflict (пример 09: заказ
    отмечен после накладной) — статус и due от накладной, флаг для проверки в офисе."""
    a = _who(st, 'A', '1111')
    o1, o2, sa = 'O:' + _uid(101), 'O:' + _uid(102), 'S:' + _uid(103)
    o3, s10, s20 = 'O:' + _uid(104), 'S:' + _uid(105), 'S:' + _uid(106)
    o4, sb = 'O:' + _uid(107), 'S:' + _uid(108)
    st.store.save_day(PAST, 'CAR1', [_stopp(o1, [('o1', 10, 100, 100)]), _stopp(o2, [('o2', 10, 200, 200)], seq=2),
                                     _stopp(o3, [('o3', 20, 100, 300)], seq=3),
                                     _stopp(o4, [('o4', 10, 100, 400)], seq=4)], 'v1', PAST + 'T08:00:00+04:00')
    _ingest(st, a, _deliver(o1, [('o1', 10)], '09:00:00'), _ev('payment', o1, {'amount': 1000, 'kind': 'invoice'}, '09:01:00'),
            _deliver(o3, [('o3', 20)], '09:10:00'), _ev('payment', o3, {'amount': 2000, 'kind': 'invoice'}, '09:11:00'))
    st.store.save_day(PAST, 'CAR1', [
        _stopp(sa, [('s1', 10, 100, 100), ('s2', 10, 200, 200)], replaces=[o1, o2]),
        _stopp(s10, [('t1', 10, 100, 300)], replaces=[o3], seq=2), _stopp(s20, [('u1', 10, 100, 300)], replaces=[o3], seq=3),
        _stopp(sb, [('b1', 10, 100, 400)], replaces=[o4], seq=4)], 'v2', PAST + 'T09:30:00+04:00')
    _ingest(st, a, _deliver(sb, [('b1', 10)], '10:00:00'), _ev('payment', sb, {'amount': 1000, 'kind': 'invoice'}, '10:01:00'),
            _deliver(o4, [('o4', 10)], '10:30:00'))           # терминал без связи отметил прежний заказ после накладной
    stops = _today_stops(client)
    assert [(x, stops[x]['status'], stops[x]['due'], stops[x]['paid'], stops[x]['flags']) for x in (sa, s10, s20, sb)] == [
        (sa, 'in_progress', 3000.0, 1000.0, []), (s10, 'full', 2000.0, 2000.0, ['split_order']),
        (s20, 'covered', 0.0, 0.0, ['split_order']), (sb, 'full', 1000.0, 1000.0, ['merge_conflict'])]
    car = client.get(f'/api/courier/admin/today?date={PAST}').get_json()['cars'][0]
    assert (car['total'], car['in_progress'], car['covered'], car['full'], car['pending'], car['removed']) == (4, 1, 1, 2, 0, [])
    rows = {r['stop_id']: r for r in _money(client)['A']['rows']}
    assert set(rows) == {sa, s10, sb}                         # covered: ни оплат, ни ожидания
    assert (rows[sa]['status'], rows[sa]['due'], rows[sa]['expected'], rows[sa]['invoice'], rows[sa]['short']) == \
        ('in_progress', 3000.0, 3000.0, 1000.0, 2000.0)
    assert (rows[s10]['expected'], rows[s10]['invoice_all'], rows[s10]['short'], rows[s10]['flags']) == \
        (2000.0, 2000.0, 0.0, ['split_order'])
    assert (rows[sb]['expected'], rows[sb]['short'], rows[sb]['flags']) == (1000.0, 0.0, ['merge_conflict'])


def test_v12_expected_on_author_of_statement(st, client):
    """«Надо взять» — один раз, у водителя авторитетного заявления (StopView.statement): заказы, слитые в накладную,
    доставили A и B, последним — B; оплату взял A — у B «Վերցրել է այլ վարորդ» (collected_by_other), а не «нет оплаты»."""
    a, b = _who(st, 'A', '1111'), _who(st, 'B', '2222')
    o1, o2, s1 = 'O:' + _uid(110), 'O:' + _uid(111), 'S:' + _uid(112)
    st.store.save_day(PAST, 'CAR1', [_stop(o1, [('o1:1', 10, 100)]), _stop(o2, [('o2:1', 5, 100)], seq=2)], 'v1',
                      PAST + 'T08:00:00+04:00')
    _ingest(st, a, _deliver(o1, [('o1:1', 10)], '10:00:00'), _ev('payment', o1, {'amount': 1000, 'kind': 'invoice'}, '10:01:00'))
    _ingest(st, b, _deliver(o2, [('o2:1', 5)], '10:05:00'))
    st.store.save_day(PAST, 'CAR1', [_stop(s1, [('s1:1', 10, 100), ('s1:2', 5, 100)], replaces=[o1, o2])], 'v2',
                      PAST + 'T11:00:00+04:00')
    m = _money(client)
    (ra,), (rb,) = m['A']['rows'], m['B']['rows']
    assert (ra['stop_id'], ra['expected'], ra['invoice'], ra['short']) == (s1, None, 1000.0, None)
    assert (rb['stop_id'], rb['expected'], rb['invoice'], rb['invoice_all'], rb['short']) == (s1, 1500.0, 0.0, 1000.0, 500.0)
    assert 'collected_by_other' in rb['flags'] and 'no_payment' not in rb['flags']
    assert (m['A']['expected'], m['B']['expected']) == (0.0, 1500.0)


def test_m2_next_day_backorder_does_not_change_day(st, client):
    """M2: накладная на остаток заказа на следующий день (тот же заказ в DOCPARENTS) не меняет вид прошлого дня —
    связи берутся из снимков той же даты; в новом дне накладная — сама по себе."""
    day1, day2 = '2026-09-30', PAST
    a = _who(st, 'A', '1111')
    o, s2 = 'O:' + _uid(70), 'S:' + _uid(71)
    order = _stop(o, [('o:1', 10, 100)])
    order['doc_number'] = 'Z-70'
    st.store.save_day(day1, 'CAR1', [order], 'v1', day1 + 'T08:00:00+04:00')
    _ingest(st, a, _ev('delivery', o, {'lines': [{'line_id': 'o:1', 'qty': 6}], 'reason_id': 'no_money'}, '10:00:00', day1),
            _ev('payment', o, {'amount': 600, 'kind': 'invoice'}, '10:01:00', day1),
            _ev('scan', o, {'raw': 'M2-CODE', 'line_id': 'o:1', 'kind': 'sale', 'gtin': None, 'serial': None,
                            'is_group': False, 'units': 1}, '09:59:00', day1))
    marks = lambda: client.get('/api/courier/admin/marks?q=M2-CODE').get_json()['rows']  # noqa: E731
    before = (_today_stops(client, day1), _money(client, day1), marks())
    assert (before[0][o]['status'], before[0][o]['due'], before[0][o]['paid']) == ('partial', 600.0, 600.0)
    assert [(r['stop_id'], r['expected'], r['short']) for r in before[1]['A']['rows']] == [(o, 600.0, 0.0)]
    assert [(r['doc_number'], r['order_number'], r['split']) for r in before[2]] == [('Z-70', None, False)]
    st.store.save_day(day2, 'CAR1', [_stop(s2, [('s2:1', 4, 100)], replaces=[o])], 'w1', day2 + 'T08:00:00+04:00')
    assert (_today_stops(client, day1), _money(client, day1), marks()) == before
    after = _today_stops(client, day2)
    assert (after[s2]['status'], after[s2]['due'], after[s2]['paid']) == ('pending', 400.0, 0.0)
    assert _money(client, day2) == {}


def _pin_row(path, did):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute('SELECT pin_hash, pin_tag, pin_scheme FROM drivers WHERE id = ?', (did,)).fetchone()


def _peppered(pepper, pin):
    return hmac.new(pepper.encode(), pin.encode(), hashlib.sha256).hexdigest()


def test_m3_pepper_protects_pin_hash_set_rotate_unset(tmp_path, monkeypatch, now):
    """M3: с перцем pin_hash — от HMAC(перец, PIN) (схема 'pepper:<id>'): по базе без перца PIN не проверить.
    Включение перца, смена (прежний — в COURIER_PIN_PEPPER_OLD), снятие: водитель входит как обычно и сразу получает
    хеш и tag текущей схемы; tag без перца переводится сразу (без PIN)."""
    from courier.security import pepper_from_env
    path = str(tmp_path / 'c.db')
    a = Store(path).save_driver(None, 'A', True, '1234', 'x')
    h0, tag0, scheme0 = _pin_row(path, a)
    assert scheme0 == 'plain' and wz.check_password_hash(h0, '1234') and len(tag0) == 64
    # перец включили
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'pepper-1')
    p1 = pepper_from_env()
    s = Store(path)
    assert [d.pin_reset for d in s.list_drivers()] == [False]
    h, tag1, scheme = _pin_row(path, a)
    assert tag1.startswith(f'p2:{p1.id}:') and (h, scheme) == (h0, 'plain')          # tag — сразу, хеш — при входе
    assert [d.id for d in s.match_pin('1234')] == [a]
    h1, tag, scheme = _pin_row(path, a)
    assert scheme == f'pepper:{p1.id}' and tag == tag1 and h1 != h0
    assert not wz.check_password_hash(h1, '1234') and wz.check_password_hash(h1, _peppered('pepper-1', '1234'))
    b = s.save_driver(None, 'B', True, '5678', 'x')
    assert _pin_row(path, b)[2] == f'pepper:{p1.id}'
    with pytest.raises(PinConflict):
        s.save_driver(None, 'C', True, '1234', 'x')
    # перец сменили, прежний — в COURIER_PIN_PEPPER_OLD
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'pepper-2')
    monkeypatch.setenv('COURIER_PIN_PEPPER_OLD', 'pepper-1')
    p2 = pepper_from_env()
    s = Store(path)
    with pytest.raises(PinConflict):                                                    # PIN A — по tag прежнего перца
        s.save_driver(None, 'C', True, '1234', 'x')
    assert [d.id for d in s.match_pin('1234')] == [a]
    h2, tag2, scheme2 = _pin_row(path, a)
    assert scheme2 == f'pepper:{p2.id}' and tag2.startswith(f'p2:{p2.id}:')
    assert wz.check_password_hash(h2, _peppered('pepper-2', '1234'))
    assert _pin_row(path, b)[2] == f'pepper:{p1.id}' and not any(d.pin_reset for d in s.list_drivers())
    # перец сняли (прежний — в COURIER_PIN_PEPPER_OLD): вход возвращает схему без перца
    monkeypatch.delenv('COURIER_PIN_PEPPER')
    monkeypatch.setenv('COURIER_PIN_PEPPER_OLD', 'pepper-2')
    s = Store(path)
    assert [d.id for d in s.match_pin('1234')] == [a]
    h3, tag3, scheme3 = _pin_row(path, a)
    assert scheme3 == 'plain' and len(tag3) == 64 and wz.check_password_hash(h3, '1234')
    assert {d.name: d.pin_reset for d in s.list_drivers()} == {'A': False, 'B': True}   # перца B (pepper-1) нет
    with pytest.raises(PinReset):
        s.match_pin('5678')
    assert [d.id for d in s.match_pin('1234')] == [a]                                   # A это не мешает


def test_m3_lost_pepper_login_says_reset_in_office(st, client, monkeypatch):
    """Перца хеша нет ни в COURIER_PIN_PEPPER, ни в COURIER_PIN_PEPPER_OLD: вход — 403 «Սխալ PIN կամ PIN-ը պետք է
    նորից սահմանել գրասենյակում» (опечатку и такой PIN не различить; попытка в счёт блокировки), офис видит pin_reset
    и задаёт новый PIN."""
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'lost-pepper')
    did, terminal, h = make_terminal(st)
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'new-pepper')            # перец заменили, прежний не сохранили
    st.store = Store(st.store.path)                                   # перезапуск сервера
    r = client.post('/api/courier/v1/login', json={'pin': '1234'}, headers=h)
    assert r.status_code == 403 and r.get_json() == {'error': 'pin',
                                                     'message': 'Սխալ PIN կամ PIN-ը պետք է նորից սահմանել գրասենյակում'}
    assert st.store.terminal(terminal.id).failed_pin_count == 1
    assert [(d['id'], d['pin_reset']) for d in client.get('/api/courier/admin/drivers').get_json()['drivers']] == [(did, True)]
    assert client.post('/api/courier/admin/drivers', json={'id': did, 'name': 'Արամ', 'pin': '2468'}).get_json()['success']
    assert login(client, h, '2468')
    assert client.get('/api/courier/admin/drivers').get_json()['drivers'][0]['pin_reset'] is False
    for _ in range(4):                                                 # блокировка — как раньше
        client.post('/api/courier/v1/login', json={'pin': '0000'}, headers=h)
    assert client.post('/api/courier/v1/login', json={'pin': '2468'}, headers=h).status_code == 429


def test_m3_login_upgrades_old_60k_hash(tmp_path, now):
    """Хеш до схемы 5 (pbkdf2:sha256:60000) пересчитывается при первом входе числом итераций werkzeug по умолчанию;
    дальше не пересчитывается."""
    path = str(tmp_path / 'c.db')
    a = Store(path).save_driver(None, 'A', True, '1234', 'x')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('UPDATE drivers SET pin_hash = ? WHERE id = ?',
                     (wz.generate_password_hash('1234', 'pbkdf2:sha256:60000'), a))
        conn.commit()
    s = Store(path)
    assert [d.id for d in s.match_pin('1234')] == [a]
    h, _, scheme = _pin_row(path, a)
    assert scheme == 'plain' and h.startswith(f'pbkdf2:sha256:{wz.DEFAULT_PBKDF2_ITERATIONS}$')
    assert wz.check_password_hash(h, '1234')
    assert [d.id for d in s.match_pin('1234')] == [a] and _pin_row(path, a)[0] == h


def test_m3_new_hashes_use_werkzeug_default_iterations(monkeypatch):
    from courier import security
    monkeypatch.setattr(wz, 'DEFAULT_PBKDF2_ITERATIONS', REAL_PBKDF2_ITERATIONS)
    assert REAL_PBKDF2_ITERATIONS >= 600000
    h = security.hash_pin('1234')
    assert h.startswith(f'pbkdf2:sha256:{REAL_PBKDF2_ITERATIONS}$') and not security.hash_outdated(h)
    assert security.hash_outdated('pbkdf2:sha256:60000$salt$hash')


def test_m3_steady_login_is_one_tag_pbkdf2(tmp_path, monkeypatch, now):
    """Вход в установившемся режиме — один pbkdf2 tag, без проверки и пересчёта хеша PIN (1 000 000 итераций werkzeug):
    время входа не растёт от итераций хеша и числа водителей."""
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'pepper-1')
    path = str(tmp_path / 'c.db')
    s = Store(path)
    a = s.save_driver(None, 'A', True, '1234', 'x')
    for i in range(10):
        s.save_driver(None, f'D{i}', True, f'{5000 + i}', 'x')
    assert [d.id for d in s.match_pin('1234')] == [a]
    calls = []
    check, generate = wz.check_password_hash, wz.generate_password_hash
    monkeypatch.setattr(wz, 'check_password_hash', lambda *x: calls.append('check') or check(*x))
    monkeypatch.setattr(wz, 'generate_password_hash', lambda *x, **k: calls.append('generate') or generate(*x, **k))
    for _ in range(3):
        assert [d.id for d in s.match_pin('1234')] == [a]
    assert calls == []


V4_SCHEMA = (   # схема 4, как её создаёт код 914dfad (sqlite_master базы, созданной этим кодом, по rowid)
    "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE drivers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pin_hash TEXT, active INTEGER NOT NULL "
    "DEFAULT 1 CHECK (active IN (0, 1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT, pin_tag TEXT)",
    "CREATE INDEX drivers_pin_tag ON drivers(pin_tag)",
    "CREATE TABLE terminals(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, car_code TEXT NOT NULL, "
    "token_sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, created_by TEXT, revoked_at TEXT, revoked_by TEXT, "
    "failed_pin_count INTEGER NOT NULL DEFAULT 0, pin_window_start TEXT, locked_until TEXT, last_seen_at TEXT, "
    "admin_pin_hash TEXT)",
    "CREATE TABLE sessions(token_sha256 TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, "
    "created_at TEXT NOT NULL, expires_at TEXT NOT NULL)",
    "CREATE INDEX sessions_terminal ON sessions(terminal_id)",
    "CREATE TABLE events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, "
    "car_code TEXT NOT NULL, date TEXT NOT NULL, stop_id TEXT, type TEXT NOT NULL, at_device TEXT NOT NULL, at_utc TEXT NOT NULL, "
    "received_at TEXT NOT NULL, payload TEXT NOT NULL, flags TEXT NOT NULL DEFAULT '[]', snapshot_id INTEGER)",
    "CREATE INDEX events_day ON events(date, car_code)",
    "CREATE INDEX events_stop ON events(stop_id, type)",
    "CREATE INDEX events_driver ON events(driver_id, date)",
    "CREATE INDEX events_snapshot ON events(snapshot_id)",
    "CREATE INDEX events_type ON events(type, date)",
    "CREATE TABLE geo_suggest_decision(event_id TEXT PRIMARY KEY, decision TEXT NOT NULL CHECK (decision IN "
    "('accepted','rejected')), decided_at TEXT NOT NULL, decided_by TEXT)",
    "CREATE TABLE rejected_events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, driver_id INTEGER NOT NULL, date TEXT, "
    "type TEXT, received_at TEXT NOT NULL, error TEXT NOT NULL, message TEXT NOT NULL, body TEXT)",
    "CREATE INDEX rejected_driver ON rejected_events(driver_id, date)",
    "CREATE INDEX rejected_terminal ON rejected_events(terminal_id, received_at)",
    V1_SCHEMA[7],   # scans — без изменений со схемы 1
    "CREATE INDEX scans_raw ON scans(raw)",
    "CREATE INDEX scans_date ON scans(date)",
    V1_SCHEMA[8],   # photos
    "CREATE INDEX photos_event ON photos(event_id)",
    "CREATE INDEX photos_terminal ON photos(terminal_id, received_at)",
    "CREATE TABLE day_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, car_code TEXT NOT NULL, "
    "version TEXT NOT NULL, loaded_at TEXT NOT NULL, saved_at TEXT NOT NULL)",
    "CREATE INDEX day_snapshots_day ON day_snapshots(date, car_code, id)",
    "CREATE TABLE stop_data(hash BLOB PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE snapshot_stops(snapshot_id INTEGER NOT NULL, stop_id TEXT NOT NULL, date TEXT NOT NULL, car_code TEXT "
    "NOT NULL, seq INTEGER NOT NULL, data_hash BLOB NOT NULL, PRIMARY KEY (snapshot_id, stop_id))",
    "CREATE INDEX snapshot_stops_stop ON snapshot_stops(stop_id, snapshot_id)",
    "CREATE INDEX snapshot_stops_day ON snapshot_stops(date, snapshot_id)",
    "CREATE TABLE stop_replaces(order_stop_id TEXT NOT NULL, invoice_stop_id TEXT NOT NULL, date TEXT NOT NULL, "
    "snapshot_id INTEGER, PRIMARY KEY (order_stop_id, invoice_stop_id))",
    *V1_SCHEMA[10:15],   # cash_handover, marked_products, tare_custom, reasons, app_release
    "INSERT INTO meta(key, value) VALUES('schema_version', '4')",
    "INSERT INTO meta(key, value) VALUES('pin_salt', '00112233445566778899aabbccddeeff')",
)


def test_l1_migration_v4_to_v5_with_data(st, client, monkeypatch, now):
    """Схема 4 (как её создаёт код 914dfad) → 5 на базе с данными, одной транзакцией: снимки, события, решения по
    предложениям водителей и водители целы; line_max — наибольшее qty строк по всем версиям (проверка опоздавшего
    события работает сразу); stop_replaces больше нет — офис сводит заказ к накладной по снимкам даты; хеши PIN —
    схема 'plain', tag с перцем — с id перца; вход узнаёт водителей и переводит хеш на перец."""
    path = st.store.path                    # база сервера: init_app файлы не трогает — кладём базу схемы 4 до запросов
    salt = '00112233445566778899aabbccddeeff'
    o, s1, s2 = 'O:' + _uid(60), 'S:' + _uid(61), 'S:' + _uid(62)
    v1 = [_stop(o, [('o:1', 5, 100)]), _stop(s2, [('t:1', 9, 10)], seq=2, product=7)]
    v2 = [_stop(s1, [('s:1', 5, 100)], replaces=[o]), _stop(s2, [('t:1', 7, 10)], seq=2, product=7)]   # s2: 9 → 7
    pid16 = hmac.new(b'old-pepper', b'courier-pin-pepper-id', hashlib.sha256).hexdigest()[:16]

    def p1_tag(pin):   # tag с перцем схемы 4: 'p1:' + HMAC(перец, pbkdf2(PIN, соль базы, 60000))
        base = hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), 60000).hex()
        return 'p1:' + hmac.new(b'old-pepper', base.encode(), hashlib.sha256).hexdigest()
    with closing(sqlite3.connect(path)) as conn:
        for ddl in V4_SCHEMA:
            conn.execute(ddl)
        conn.execute("INSERT INTO meta(key, value) VALUES('pin_pepper_id', ?)", (pid16,))
        for sid, stops in ((1, v1), (2, v2)):
            conn.execute('INSERT INTO day_snapshots VALUES(?, ?, ?, ?, ?, ?)',
                         (sid, PAST, 'CAR1', f'v{sid}', 't', f'{PAST}T0{7 + sid}:00:00+04:00'))
            for seq, stop in enumerate(stops, 1):
                text = json.dumps(stop, ensure_ascii=False, sort_keys=True)
                digest = hashlib.sha256(text.encode('utf-8')).digest()
                conn.execute('INSERT OR IGNORE INTO stop_data VALUES(?, ?)', (digest, text))
                conn.execute('INSERT INTO snapshot_stops VALUES(?, ?, ?, ?, ?, ?)',
                             (sid, stop['stop_id'], PAST, 'CAR1', seq, digest))
        conn.execute('INSERT INTO stop_replaces VALUES(?, ?, ?, ?)', (o, s1, PAST, 2))
        old_hash = lambda pin: wz.generate_password_hash(pin, 'pbkdf2:sha256:60000')  # noqa: E731
        conn.execute("INSERT INTO drivers(name, pin_hash, active, created_at, updated_at, pin_tag) "
                     "VALUES('A', ?, 1, 'x', 'x', ?)", (old_hash('1234'), p1_tag('1234')))
        conn.execute("INSERT INTO drivers(name, pin_hash, active, created_at, updated_at) VALUES('B', ?, 1, 'x', 'x')",
                     (old_hash('5678'),))                                       # PIN до схемы 2: без tag
        conn.execute("INSERT INTO terminals(name, car_code, token_sha256, created_at) VALUES('U', 'CAR1', 'h', 'x')")
        for eid_, etype, at, payload in (('e1', 'delivery', '10:00', {'lines': [{'line_id': 'o:1', 'qty': 5}]}),
                                         ('e2', 'payment', '10:01', {'amount': 500, 'kind': 'invoice'}),
                                         ('e3', 'geo_suggest', '10:02', {'lat': 40.18, 'lon': 44.51, 'accuracy': 8})):
            conn.execute("INSERT INTO events(id, terminal_id, driver_id, car_code, date, stop_id, type, at_device, at_utc, "
                         "received_at, payload, snapshot_id) VALUES(?, 1, 1, 'CAR1', ?, ?, ?, ?, ?, 'r', ?, 1)",
                         (eid_, PAST, o, etype, f'{PAST}T{at}:00+04:00', f'{PAST}T{at[:2]}:{at[3:]}:00.000000+04:00',
                          json.dumps(payload)))
        conn.execute("INSERT INTO geo_suggest_decision VALUES('e3', 'rejected', 'x', 'admin')")
        conn.commit()
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'old-pepper')
    st.store = s = Store(path)
    assert [x['stop_id'] for x in s.day_stops(PAST, 'CAR1')] == [s1, s2]
    assert [e['id'] for e in s.events_for_day(PAST)] == ['e1', 'e2', 'e3']
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION) == '5'
        assert conn.execute("SELECT value FROM meta WHERE key = 'pin_pepper_id'").fetchone() is None
        assert conn.execute('SELECT stop_id, line_id, max_qty, product_id FROM line_max ORDER BY stop_id, line_id'
                            ).fetchall() == [(o, 'o:1', 5.0, 1), (s1, 's:1', 5.0, 1), (s2, 't:1', 9.0, 7)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'stop_replaces'").fetchone() is None
        assert conn.execute('SELECT decision FROM geo_suggest_decision').fetchall() == [('rejected',)]
        drivers = conn.execute('SELECT name, pin_scheme, pin_tag FROM drivers ORDER BY id').fetchall()
    assert drivers == [('A', 'plain', f'p2:{pid16[:8]}:' + p1_tag('1234')[3:]), ('B', 'plain', None)]
    assert [d.name for d in s.match_pin('1234')] == ['A'] and [d.name for d in s.match_pin('5678')] == ['B']
    with closing(sqlite3.connect(path)) as conn:
        assert [r[0] for r in conn.execute('SELECT pin_scheme FROM drivers ORDER BY id')] == [f'pepper:{pid16[:8]}'] * 2
    # офис: заказ — у накладной текущего снимка той же даты (без stop_replaces)
    (row,) = _money(client)['A']['rows']
    assert (row['stop_id'], row['status'], row['expected'], row['short']) == (s1, 'full', 500.0, 0.0)
    # опоздавшее событие: строки t:1 в действующей версии 7, в прежней — 9
    r = ev.ingest(s, ev.Who(1, 'CAR1', 1, 'A'), [_deliver(s2, [('t:1', 9)], '11:00:00'),
                                                 _deliver(s2, [('t:1', 9.5)], '11:01:00')]).json()
    assert len(r['accepted']) == 1 and len(r['rejected']) == 1


def test_l1_late_event_after_purge_uses_max_table(st, now):
    """L1 (§5 п. 13): прежняя версия накладной удалена хранением снимков, а опоздавшее событие по её количеству
    принимается (qty_over_invoice) — наибольшее qty строки хранится отдельно и не удаляется."""
    a = _who(st, 'A', '1111')
    x = 'S:' + _uid(80)
    st.store.save_day(PAST, 'CAR1', [_stop(x, [('x:1', 10, 100)])], 'v1', PAST + 'T08:00:00+04:00')
    st.store.save_day(PAST, 'CAR1', [_stop(x, [('x:1', 8, 100)])], 'v2', PAST + 'T09:00:00+04:00')   # офис уменьшил
    now['t'] = NOW + timedelta(days=8)
    st.store.save_day('2026-10-10', 'CAR1', [], 'w', '2026-10-10T08:00:00+04:00')                     # чистка снимков
    assert [v['data']['lines'][0]['qty'] for v in st.store.stop_versions([x])[x]] == [8.0]           # v1 удалена
    late, too_much = _deliver(x, [('x:1', 10)], '10:00:00'), _deliver(x, [('x:1', 10.5)], '10:05:00')
    r = ev.ingest(st.store, a, [late, too_much]).json()
    assert r['accepted'] == [late['id']] and [x_['id'] for x_ in r['rejected']] == [too_much['id']]
    assert 'qty_over_invoice' in {e['id']: e['flags'] for e in st.store.events_for_day(PAST)}[late['id']]


def test_l2_scan_short_by_product_across_lines(st):
    """L2: «сканов хватает» — по товару на всю доставку: две строки одного маркируемого товара, все коды
    отсканированы на одну строку — недостачи нет; кода не хватает на сумму строк — scan_short."""
    a = _who(st, 'A', '1111')
    x = 'S:' + _uid(85)
    st.store.save_day(PAST, 'CAR1', [_stop(x, [('x:1', 3, 100), ('x:2', 2, 100)], marked=True, product=55)], 'v1',
                      PAST + 'T08:00:00+04:00')
    scans = [_ev('scan', x, {'raw': f'L2-{i}', 'line_id': 'x:1', 'kind': 'sale', 'gtin': None, 'serial': None,
                             'is_group': False, 'units': 1}, '10:00:00') for i in range(5)]
    ok = _deliver(x, [('x:1', 3), ('x:2', 2)], '10:10:00')
    _ingest(st, a, *scans, ok)
    _ingest(st, a, _ev('scan_cancel', None, {'scan_event_id': scans[0]['id']}, '10:20:00'))
    short = _deliver(x, [('x:1', 3), ('x:2', 2)], '10:30:00')
    _ingest(st, a, short)
    flags = {e['id']: e['flags'] for e in st.store.events_for_day(PAST)}
    assert 'scan_short' not in flags[ok['id']] and 'scan_short' in flags[short['id']]


def test_l3_marks_split_order_lists_all_invoices(st, client):
    """L3: скан по заказу, разделённому на две накладные, — номер заказа и номера ОБЕИХ накладных, «բաժանված»;
    то же в CSV (накладные через запятую и столбец «Բաժանված պատվեր»)."""
    a = _who(st, 'A', '1111')
    o, s1, s2 = 'O:' + _uid(90), 'S:' + _uid(91), 'S:' + _uid(92)
    order = _stop(o, [('o:1', 20, 100)], marked=True, product=55)
    order['doc_number'] = 'Z-9'
    st.store.save_day(PAST, 'CAR1', [order], 'v1', PAST + 'T08:00:00+04:00')
    _ingest(st, a, _ev('scan', o, {'raw': 'SPLIT-1', 'line_id': 'o:1', 'kind': 'sale', 'gtin': None, 'serial': None,
                                   'is_group': False, 'units': 1}, '10:00:00'))
    inv1, inv2 = _stop(s1, [('a:1', 12, 100)], replaces=[o]), _stop(s2, [('b:1', 8, 100)], replaces=[o], seq=2)
    inv1['doc_number'], inv2['doc_number'] = 'A-1', 'A-2'
    st.store.save_day(PAST, 'CAR1', [inv1, inv2], 'v2', PAST + 'T09:00:00+04:00')
    (row,) = client.get('/api/courier/admin/marks?q=SPLIT').get_json()['rows']
    assert (row['order_number'], row['invoice_numbers'], row['doc_number'], row['split']) == \
        ('Z-9', ['A-1', 'A-2'], 'A-1, A-2', True)
    text = client.get(f'/api/courier/admin/marks.csv?from={PAST}').data.decode('utf-8')
    head, line = text.lstrip('﻿').split('\r\n')[:2]
    cells = dict(zip(head.split(';'), line.split(';')))
    assert (cells['Ապրանքագիր'], cells['Պատվեր'], cells['Բաժանված պատվեր']) == ('A-1, A-2', 'Z-9', 'այո')
    # накладную разделённого заказа отсканировали напрямую — у неё свой номер, без «բաժանված»
    _ingest(st, a, _ev('scan', s2, {'raw': 'DIRECT-1', 'line_id': 'b:1', 'kind': 'sale', 'gtin': None, 'serial': None,
                                    'is_group': False, 'units': 1}, '10:05:00'))
    (row,) = client.get('/api/courier/admin/marks?q=DIRECT').get_json()['rows']
    assert (row['doc_number'], row['order_number'], row['invoice_numbers'], row['split']) == ('A-2', None, ['A-2'], False)


def test_supersedes_validation(term, client, st):
    """§5 п. 14: `supersedes` в delivery и tare — строка до 64 символов (может ссылаться на ещё не полученное
    событие; хранится в нижнем регистре); не строка, длиннее, ссылка на себя — отказ."""
    s1 = term['day']['stops'][0]
    target = str(uuid.uuid4())
    ok = event('delivery', s1['stop_id'], {'lines': full_lines(s1), 'supersedes': target.upper()})
    tare = event('tare', s1['stop_id'], {'items': [], 'supersedes': 'x' * 64})
    long_ = event('delivery', s1['stop_id'], {'lines': full_lines(s1), 'supersedes': 'x' * 65})
    number = event('tare', s1['stop_id'], {'items': [], 'supersedes': 5})
    itself = event('delivery', s1['stop_id'], {'lines': full_lines(s1)})
    itself['payload']['supersedes'] = itself['id']
    r = post(client, term['s'], ok, tare, long_, number, itself)
    assert r['accepted'] == [ok['id'], tare['id']]
    assert [x['id'] for x in r['rejected']] == [long_['id'], number['id'], itself['id']]
    stored = {e['id']: e['payload'] for e in st.store.events_for_day(DEMO)}
    assert stored[ok['id']]['supersedes'] == target and stored[tare['id']]['supersedes'] == 'x' * 64


def test_supersedes_clock_reset_end_to_end(term, client, st):
    """§5 п. 14, пример 16 через HTTP API: часы терминала ушли вперёд (доставка с at в 2030 году) и вернулись —
    исправление с `supersedes` вытесняет прежнюю доставку, хотя его at раньше, в каком бы порядке события ни пришли.
    Офис: «частично», due = 6 × цена, оплачено полностью; тара — тоже по исправлению."""
    cash = next(s for s in term['day']['stops'] if s['collect'] == 'cash')          # 10 × 1200
    line = cash['lines'][0]['line_id']
    first = event('delivery', cash['stop_id'], {'lines': [{'line_id': line, 'qty': 10}]}, at='2030-01-01T10:00:00+04:00')
    tare1 = event('tare', cash['stop_id'], {'items': [{'tare_id': 'erp:990202', 'qty': 5}]}, at='2030-01-01T10:00:30+04:00')
    assert post(client, term['s'], first, tare1)['accepted'] == [first['id'], tare1['id']]
    flags = {e['id']: e['flags'] for e in st.store.events_for_day(DEMO)}
    assert 'date_suspicious' in flags[first['id']]                                   # §5 п. 15: принято, с флагом

    def stop_row():
        car = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()['cars'][0]
        return car, next(x for x in car['stops'] if x['stop_id'] == cash['stop_id'])
    car, row = stop_row()
    assert (row['status'], row['due'], row['tare']) == ('full', 12000.0, [{'tare_id': 'erp:990202', 'qty': 5}])
    fix = event('delivery', cash['stop_id'], {'lines': [{'line_id': line, 'qty': 6}], 'reason_id': 'no_money',
                                              'supersedes': first['id']}, at='2000-01-01T11:00:00+04:00')
    tare2 = event('tare', cash['stop_id'], {'items': [{'tare_id': 'erp:990202', 'qty': 3}], 'supersedes': tare1['id']},
                  at='2000-01-01T11:00:30+04:00')
    pay = event('payment', cash['stop_id'], {'amount': 7200.0, 'kind': 'invoice'}, at='2000-01-01T11:01:00+04:00')
    assert post(client, term['s'], fix, tare2, pay)['accepted'] == [fix['id'], tare2['id'], pay['id']]
    car, row = stop_row()
    assert (row['status'], row['due'], row['paid'], car['partial'], car['full']) == ('partial', 7200.0, 7200.0, 1, 0)
    assert row['tare'] == [{'tare_id': 'erp:990202', 'qty': 3}]
    (mrow,) = client.get(f'/api/courier/admin/money?date={DEMO}').get_json()['drivers'][0]['rows']
    assert (mrow['status'], mrow['due'], mrow['expected'], mrow['short']) == ('partial', 7200.0, 7200.0, 0.0)
    # то же, если исправление пришло раньше вытесненного события (ссылка на ещё не полученное событие)
    other = next(s for s in term['day']['stops'] if s['collect'] == 'cash_ecr')     # 12 × 700
    ol = other['lines'][0]['line_id']
    late = event('delivery', other['stop_id'], {'lines': [{'line_id': ol, 'qty': 12}]}, at='2030-01-01T10:00:00+04:00')
    fix2 = event('delivery', other['stop_id'], {'lines': [{'line_id': ol, 'qty': 0}], 'reason_id': 'closed',
                                                'supersedes': late['id']}, at='2000-01-01T11:00:00+04:00')
    assert post(client, term['s'], fix2)['accepted'] == [fix2['id']]
    assert post(client, term['s'], late)['accepted'] == [late['id']]
    car = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()['cars'][0]
    assert next(x for x in car['stops'] if x['stop_id'] == other['stop_id'])['status'] == 'refused'


def test_at_2099_accepted_with_date_suspicious(term, client, st):
    """§5 п. 15: момент до 2100 года принимается (дата далеко — флаг date_suspicious), позже 2100 — отказ."""
    s1 = term['day']['stops'][0]
    far = event('arrived', s1['stop_id'], {'lat': 40.1, 'lon': 44.5}, at='2099-12-31T10:00:00+04:00')
    beyond = event('arrived', s1['stop_id'], {'lat': 40.1, 'lon': 44.5}, at='2101-01-01T10:00:00+04:00')
    r = post(client, term['s'], far, beyond)
    assert r['accepted'] == [far['id']] and [x['id'] for x in r['rejected']] == [beyond['id']]
    assert 'at' in r['rejected'][0]['message']
    assert {e['id']: e['flags'] for e in st.store.events_for_day(DEMO)}[far['id']] == ['date_suspicious']


# ============================== четвёртая проверка: правило v1.2.3, day_version, тара, циклы, перец, сканы ==============================

def test_v123_payment_on_covered_sibling_counts_at_owner(st, client):
    """§5 п. 12 v1.2.3 (пример 27): заказ доставлен до разделения, накладные S:a и S:b появились в РАЗНЫХ версиях
    /day, оплату записали на covered-сестру S:b. «Առաքում այսօր»: S:a — full, оплачено 2000; S:b — covered, 0.
    «Գումար»: оплата — в строке владельца S:a (ждать 2000, недостачи нет), отдельной строки S:b нет. Оплату взял
    другой водитель — у него строка S:a без ожидания, у водителя заявления — «Վերցրել է այլ վարորդ», не недостача."""
    ids = {}
    for car, payer_is_b, base, pins in (('CAR1', False, 400, ('1111', '2222')), ('CAR2', True, 410, ('3333', '4444'))):
        sfx = '' if car == 'CAR1' else '2'
        a, b = _who(st, 'A' + sfx, pins[0], car), _who(st, 'B' + sfx, pins[1], car)
        o, sa, sb = 'O:' + _uid(base), 'S:' + _uid(base + 1), 'S:' + _uid(base + 2)
        ids[car] = (sa, sb)
        st.store.save_day(PAST, car, [_stopp(o, [('o1', 20, 100, 100)])], 'v1', PAST + 'T08:00:00+04:00')
        _ingest(st, a, _deliver(o, [('o1', 20)], '09:00:00'))
        inv_a = _stopp(sa, [('a1', 10, 100, 100)], replaces=[o])
        st.store.save_day(PAST, car, [inv_a], 'v2', PAST + 'T09:10:00+04:00')
        st.store.save_day(PAST, car, [inv_a, _stopp(sb, [('b1', 10, 100, 100)], replaces=[o], seq=2)], 'v3',
                          PAST + 'T09:20:00+04:00')
        _ingest(st, b if payer_is_b else a, _ev('payment', sb, {'amount': 2000, 'kind': 'invoice'}, '09:30:00'))
    stops = _today_stops(client)
    for sa, sb in ids.values():
        assert (stops[sa]['status'], stops[sa]['due'], stops[sa]['paid'], stops[sa]['flags']) == \
            ('full', 2000.0, 2000.0, ['split_order'])
        assert (stops[sb]['status'], stops[sb]['due'], stops[sb]['paid']) == ('covered', 0.0, 0.0)
    m = _money(client)
    sa, _ = ids['CAR1']
    (row,) = m['A']['rows']
    assert (row['stop_id'], row['expected'], row['invoice'], row['invoice_all'], row['short']) == (sa, 2000.0, 2000.0, 2000.0, 0.0)
    assert 'no_payment' not in row['flags'] and (m['A']['expected_short'], m['A']['collected']) == (0.0, 2000.0)
    assert 'B' not in m
    sa, _ = ids['CAR2']
    (ra,), (rb,) = m['A2']['rows'], m['B2']['rows']
    assert (ra['stop_id'], ra['expected'], ra['invoice'], ra['invoice_all'], ra['short']) == (sa, 2000.0, 0.0, 2000.0, 0.0)
    assert 'collected_by_other' in ra['flags'] and 'no_payment' not in ra['flags']
    assert (rb['stop_id'], rb['expected'], rb['invoice'], rb['short'], m['B2']['collected']) == (sa, None, 2000.0, None, 2000.0)


def test_day_version_statement_priced_by_version_driver_saw(st, client, now):
    """§5 п. 16 (замечания L-C, M-A): водитель без связи доставил по версии v1 (10 × 100, collect cash), а офис тем
    временем изменил накладную: v2 — 8 × 120, collect none. Доставка с day_version=v1 — строки, цены и collect
    заявления из v1: офис видит ровно то, что посчитал терминал по правилу на строках v1 (full, 1000, ждать 1000).
    Без day_version и с неизвестной версией — по версии на момент получения (partial, 1200). Проверка qty — по
    line_max (принято с qty_over_invoice); «нет причины» — по версии водителя (full — флага нет). Снимок v1 хранится
    и после чистки снимков (на него ссылается событие)."""
    a = _who(st, 'A', '1111')
    x, y, z = 'S:' + _uid(301), 'S:' + _uid(302), 'S:' + _uid(303)
    v1 = [_stop(x, [('x:1', 10, 100)]), _stop(y, [('y:1', 10, 100)], seq=2), _stop(z, [('z:1', 10, 100)], seq=3)]
    v2 = [_stop(s['stop_id'], [(s['lines'][0]['line_id'], 8, 120)], collect='none', seq=s['seq']) for s in v1]
    snap1 = st.store.save_day(PAST, 'CAR1', v1, 'v1', PAST + 'T08:00:00+04:00')
    snap2 = st.store.save_day(PAST, 'CAR1', v2, 'v2', PAST + 'T09:00:00+04:00')
    dx = _ev('delivery', x, {'lines': [{'line_id': 'x:1', 'qty': 10}], 'day_version': 'v1'}, '10:00:00')
    px = _ev('payment', x, {'amount': 1000, 'kind': 'invoice'}, '10:01:00')
    dy = _ev('delivery', y, {'lines': [{'line_id': 'y:1', 'qty': 10}]}, '10:05:00')
    dz = _ev('delivery', z, {'lines': [{'line_id': 'z:1', 'qty': 10}], 'day_version': 'v-unknown'}, '10:10:00')
    tare = _ev('tare', x, {'items': [{'tare_id': 'erp:5', 'qty': 1}], 'day_version': 'v1'}, '10:02:00')
    _ingest(st, a, dx, px, dy, dz, tare)
    terminal = mg.merge([{'stop_id': x, 'source': 'invoice', 'collect': 'cash', 'current': True,
                          'lines': v1[0]['lines']}], [dx, px]).stops[x]
    stops = _today_stops(client)
    assert (stops[x]['status'], stops[x]['due'], stops[x]['paid']) == \
        (terminal.status, float(terminal.due), float(terminal.paid)) == ('full', 1000.0, 1000.0)
    assert [(stops[s]['status'], stops[s]['due']) for s in (y, z)] == [('partial', 1200.0)] * 2
    (row,) = _money(client)['A']['rows']                                 # y, z — collect none (v2): ждать нечего
    assert (row['stop_id'], row['collect'], row['expected'], row['short']) == (x, 'cash', 1000.0, 0.0)
    stored = {e['id']: e for e in st.store.events_for_day(PAST)}
    assert [stored[e['id']]['snapshot_id'] for e in (dx, tare, dy, dz)] == [snap1, snap1, snap2, snap2]
    assert 'qty_over_invoice' in stored[dx['id']]['flags'] and 'no_reason' not in stored[dx['id']]['flags']
    assert {'qty_over_invoice', 'no_reason'} <= set(stored[dy['id']]['flags'])
    now['t'] = NOW + timedelta(days=8)
    st.store.save_day('2026-10-10', 'CAR1', [], 'w', '2026-10-10T08:00:00+04:00')     # чистка снимков
    assert _today_stops(client)[x]['due'] == 1000.0
    bad = [_ev('delivery', x, {'lines': [{'line_id': 'x:1', 'qty': 1}], 'day_version': 'v' * 65}, '11:00:00'),
           _ev('tare', x, {'items': [], 'day_version': 5}, '11:01:00')]
    r = ev.ingest(st.store, a, bad).json()
    assert r['accepted'] == [] and [x_['message'] for x_ in r['rejected']] == \
        ['day_version՝ չափազանց երկար', 'day_version՝ սխալ արժեք']


def test_tare_of_merged_stop_is_sum_per_stop_in_owner_scope(st, client):
    """§5 п. 17: тара объединённой точки — сумма по tare_id последних (не вытесненных) отметок тары каждой точки зоны
    владельца: O:1 — 5, O:2 — 3 (и 2 другой тары), слиты в S:A → 8 и 2; исправление O:1 (supersedes) 5 → 4 → 7.
    У сестры разделённого заказа — только своя тара, тара поглощённого заказа — у владельца."""
    a = _who(st, 'A', '1111')
    o1, o2, sa = 'O:' + _uid(331), 'O:' + _uid(332), 'S:' + _uid(333)
    o3, s10, s20 = 'O:' + _uid(334), 'S:' + _uid(335), 'S:' + _uid(336)
    st.store.save_day(PAST, 'CAR1', [_stop(o1, [('o1', 10, 100)]), _stop(o2, [('o2', 10, 100)], seq=2),
                                     _stop(o3, [('o3', 20, 100)], seq=3)], 'v1', PAST + 'T08:00:00+04:00')
    t1 = _ev('tare', o1, {'items': [{'tare_id': 'erp:5', 'qty': 5}]}, '09:00:00')
    _ingest(st, a, t1, _ev('tare', o2, {'items': [{'tare_id': 'erp:5', 'qty': 3}, {'tare_id': 'custom:1', 'qty': 2}]},
                           '09:05:00'),
            _ev('tare', o3, {'items': [{'tare_id': 'erp:5', 'qty': 2}]}, '09:10:00'))
    st.store.save_day(PAST, 'CAR1', [_stop(sa, [('s1', 10, 100), ('s2', 10, 100)], replaces=[o1, o2]),
                                     _stop(s10, [('t1', 10, 100)], replaces=[o3], seq=2),
                                     _stop(s20, [('u1', 10, 100)], replaces=[o3], seq=3)], 'v2', PAST + 'T09:30:00+04:00')
    _ingest(st, a, _ev('tare', s20, {'items': [{'tare_id': 'erp:5', 'qty': 1}]}, '10:00:00'))
    stops = _today_stops(client)
    assert stops[sa]['tare'] == [{'tare_id': 'custom:1', 'qty': 2}, {'tare_id': 'erp:5', 'qty': 8}]
    assert (stops[s10]['tare'], stops[s20]['tare']) == ([{'tare_id': 'erp:5', 'qty': 2}], [{'tare_id': 'erp:5', 'qty': 1}])
    _ingest(st, a, _ev('tare', o1, {'items': [{'tare_id': 'erp:5', 'qty': 4}], 'supersedes': t1['id']}, '08:00:00'))
    assert _today_stops(client)[sa]['tare'] == [{'tare_id': 'custom:1', 'qty': 2}, {'tare_id': 'erp:5', 'qty': 7}]


def test_supersedes_cycle_rejected_at_ingest(st):
    """§5 п. 18: цикл supersedes отклоняется при приёме — прямой (a ↔ b, ссылка в любом регистре) и через цепочку
    (x → y → z → x, все в одной пачке); ссылка на ещё не полученное событие, на событие другой точки или другого
    типа — не цикл."""
    a = _who(st, 'A', '1111')
    s1, s2 = 'S:' + _uid(341), 'S:' + _uid(342)
    st.store.save_day(PAST, 'CAR1', [_stop(s1, [('a:1', 10, 100)]), _stop(s2, [('b:1', 10, 100)], seq=2)], 'v1',
                      PAST + 'T08:00:00+04:00')
    cycle = 'supersedes-ը շրջան է կազմում'

    def d(sid, line, qty, sup, at, id_=None):
        e = _ev('delivery', sid, {'lines': [{'line_id': line, 'qty': qty}], 'reason_id': 'x', 'supersedes': sup}, at)
        e['id'] = id_ or e['id']
        return e
    ida, idb = eid(), eid()
    assert ev.ingest(st.store, a, [d(s1, 'a:1', 10, idb, '10:00:00', ida)]).json()['accepted'] == [ida]  # b ещё нет
    for ref in (ida, ida.upper()):
        r = ev.ingest(st.store, a, [d(s1, 'a:1', 6, ref, '10:05:00', idb)]).json()
        assert r['accepted'] == [] and r['rejected'] == [{'id': idb, 'error': 'bad_request', 'message': cycle}]
    x, y, z = eid(), eid(), eid()
    r = ev.ingest(st.store, a, [d(s1, 'a:1', 5, y, '11:00:00', x), d(s1, 'a:1', 4, z, '11:01:00', y),
                                d(s1, 'a:1', 3, x, '11:02:00', z)]).json()
    assert r['accepted'] == [x, y] and [(e['id'], e['message']) for e in r['rejected']] == [(z, cycle)]
    other_stop = d(s2, 'b:1', 1, ida, '12:00:00')                       # ссылка на доставку другой точки
    other_type = _ev('tare', s1, {'items': [], 'supersedes': x}, '12:01:00')
    back = _ev('tare', s1, {'items': [], 'supersedes': other_type['id']}, '12:02:00')   # tare → tare → delivery
    assert ev.ingest(st.store, a, [other_stop, other_type, back]).json()['accepted'] == \
        [other_stop['id'], other_type['id'], back['id']]
    assert {e['id'] for e in st.store.events_for_day(PAST)} & {idb, z} == set()


def test_pepper_missing_tags_kept_new_pin_refused_until_restored_or_reset(st, client, monkeypatch, caplog):
    """Перец M-A / L-B: сервер запустили без перца (не загрузился .env). tag водителей НЕ сбрасываются, WARNING в
    лог; вход — 403 «Սխալ PIN կամ PIN-ը պետք է նորից սահմանել գրասենյակում» и на опечатку, и на их PIN. Новый PIN
    другому водителю не задаётся (409 «Չի հաջողվում ստուգել PIN-ի կրկնությունը…» и список таких водителей): он мог
    совпасть, и после возврата перца двое с одним PIN не вошли бы. Перец вернули — входят как раньше, быстро (по
    tag). Офис явно сбросил PIN таких водителей — новый PIN задан, у сброшенных PIN нет, их сессии отменены."""
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'pepper-P')
    a, _, h = make_terminal(st, pin='1234', name='A')
    b = st.store.save_driver(None, 'B', True, '5678', 'admin')
    login(client, h, '5678')
    tags = {d: _pin_row(st.store.path, d)[1] for d in (a, b)}
    assert all(t.startswith('p2:') for t in tags.values())

    def restart(pepper):
        if pepper:
            monkeypatch.setenv('COURIER_PIN_PEPPER', pepper)
        else:
            monkeypatch.delenv('COURIER_PIN_PEPPER', raising=False)
        st.store = Store(st.store.path)

    def drivers():
        return {d['name']: d for d in client.get('/api/courier/admin/drivers').get_json()['drivers']}
    restart(None)
    with caplog.at_level('WARNING', logger='courier.store'):
        assert {n: d['pin_reset'] for n, d in drivers().items()} == {'A': True, 'B': True}
    assert any('нет в среде' in r.getMessage() and str(sorted((a, b))) in r.getMessage() for r in caplog.records)
    assert {d: _pin_row(st.store.path, d)[1] for d in (a, b)} == tags                     # tag не сброшены
    for pin in ('1234', '0000'):
        r = client.post('/api/courier/v1/login', json={'pin': pin}, headers=h)
        assert (r.status_code, r.get_json()['message']) == (403, 'Սխալ PIN կամ PIN-ը պետք է նորից սահմանել գրասենյակում')
    r = client.post('/api/courier/admin/drivers', json={'name': 'C', 'pin': '1234'})
    assert r.status_code == 409 and r.get_json()['error'] == \
        'Չի հաջողվում ստուգել PIN-ի կրկնությունը. վերականգնեք COURIER_PIN_PEPPER-ը'
    assert [d['name'] for d in r.get_json()['unverifiable']] == ['A', 'B']
    r = client.post('/api/courier/admin/drivers', json={'id': a, 'name': 'A', 'pin': '2468'})   # новому PIN A мешает B
    assert r.status_code == 409 and [d['name'] for d in r.get_json()['unverifiable']] == ['B']
    restart('pepper-P')                                                                   # перец вернули
    assert login(client, h, '1234') and login(client, h, '5678')
    assert {n: (d['has_pin'], d['pin_reset']) for n, d in drivers().items()} == {'A': (True, False), 'B': (True, False)}
    restart(None)                                                                         # перец потерян насовсем
    r = client.post('/api/courier/admin/drivers', json={'id': a, 'name': 'A', 'pin': '2468', 'reset_unverifiable': True})
    assert r.get_json() == {'success': True, 'id': a}
    assert {n: (d['has_pin'], d['pin_reset']) for n, d in drivers().items()} == {'A': (True, False), 'B': (False, False)}
    with closing(sqlite3.connect(st.store.path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM sessions WHERE driver_id = ?', (b,)).fetchone()[0] == 0
    assert login(client, h, '2468')
    r = client.post('/api/courier/v1/login', json={'pin': '5678'}, headers=h)
    assert (r.status_code, r.get_json()['message']) == (403, 'Սխալ PIN')
    assert client.post('/api/courier/admin/drivers', json={'id': b, 'name': 'B', 'pin': '5678'}).get_json()['success']


def test_marks_scan_on_replaced_invoice_shown_under_new_invoice(st, client):
    """M-B (пример 14): скан по накладной S:1 (сделана из заказа O:1); S:1 отменили, по тому же заказу выписали S:2.
    «Մակնշում» показывает скан у S:2, как «Առաքում այսօր»: номер A-2, прежний номер A-1 — в своей колонке (и в CSV);
    модель дня строится для каждой даты строк, а не только для дат со сканами по заказам."""
    a = _who(st, 'A', '1111')
    o, s1, s2 = 'O:' + _uid(351), 'S:' + _uid(352), 'S:' + _uid(353)

    def doc(stop, number):
        stop['doc_number'] = number
        return stop
    st.store.save_day(PAST, 'CAR1', [doc(_stop(o, [('o:1', 3, 100)], marked=True, product=5), 'Z-1')], 'v0',
                      PAST + 'T07:00:00+04:00')
    st.store.save_day(PAST, 'CAR1', [doc(_stop(s1, [('a:1', 3, 100)], replaces=[o], marked=True, product=5), 'A-1')],
                      'v1', PAST + 'T08:00:00+04:00')
    _ingest(st, a, *[_ev('scan', s1, {'raw': f'MB-{i}', 'line_id': 'a:1', 'kind': 'sale', 'gtin': None, 'serial': None,
                                      'is_group': False, 'units': 1}, '09:00:00') for i in range(3)],
            _deliver(s1, [('a:1', 3)], '09:05:00'))
    st.store.save_day(PAST, 'CAR1', [doc(_stop(s2, [('b:1', 3, 100)], replaces=[o], marked=True, product=5), 'A-2')],
                      'v2', PAST + 'T10:00:00+04:00')
    assert _today_stops(client)[s2]['status'] == 'full'
    rows = client.get('/api/courier/admin/marks?q=MB-').get_json()['rows']
    assert {(r['stop_id'], r['doc_number'], r['old_invoice_number'], r['order_number'], tuple(r['invoice_numbers']),
             r['split']) for r in rows} == {(s2, 'A-2', 'A-1', None, ('A-2',), False)} and len(rows) == 3
    text = client.get(f'/api/courier/admin/marks.csv?q=MB-0&from={PAST}').data.decode('utf-8')
    head, line = text.lstrip('﻿').split('\r\n')[:2]
    cells = dict(zip(head.split(';'), line.split(';')))
    assert (cells['Ապրանքագիր'], cells['Նախկին ապրանքագիր'], cells['Պատվեր']) == ('A-2', 'A-1', '')


def test_pepper_missing_tag_falls_back_to_plain_hash(tmp_path, monkeypatch, now):
    """Хеш PIN без перца (как после миграции v4 → v5), а tag — с перцем, которого нет в среде: водитель входит по
    хешу (tag пересчитывается), не считается «PIN задать заново», и его PIN проверяется на повтор по хешу."""
    path = str(tmp_path / 'c.db')
    a = Store(path).save_driver(None, 'A', True, '1234', 'x')
    monkeypatch.setenv('COURIER_PIN_PEPPER', 'gone')
    Store(path).list_drivers()                                          # tag без перца → p2:<gone>, хеш — при входе
    assert _pin_row(path, a)[1].startswith('p2:') and _pin_row(path, a)[2] == 'plain'
    monkeypatch.delenv('COURIER_PIN_PEPPER')
    s = Store(path)
    assert [d.pin_reset for d in s.list_drivers()] == [False]
    with pytest.raises(PinConflict) as e:
        s.save_driver(None, 'B', True, '1234', 'x')
    assert type(e.value) is PinConflict                                 # повтор найден по хешу, а не «нет перца»
    s.save_driver(None, 'B', True, '5678', 'x')
    assert [d.id for d in s.match_pin('1234')] == [a]
    assert len(_pin_row(path, a)[1]) == 64                              # tag — без перца, как в среде

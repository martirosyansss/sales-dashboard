# -*- coding: utf-8 -*-
"""«Բեռնագիր» машины на «Развозе» (ответ владельца №57): деление товаров тяжёлого магазина между рейсами (waybill.split_qty
и split_stop — сумма по рейсам равна документам точно, упаковки целыми, рейсы ровные по кг), сборка накладной по плану
(truck_waybill), чтение ERP на поддельном соединении (накладная заказа главнее заказа и считается один раз, только
SELECT) и API /api/routes/dispatch/waybill (409 при устаревшем плане, магазин на несколько рейсов настоящего plan_view).
Без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_waybill.py -q
"""
import itertools
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import erp, views  # noqa: E402
from route_optimizer import waybill as wb  # noqa: E402
from route_optimizer.erp import ErpError  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _isn, _no_road_map, client  # noqa: E402,F401

WATER = wb.Product(10, '2801', 'Գառնի 6լ', 'հատ', 6.03, 2)
COLA = wb.Product(11, '1113', 'Կոլա 1.5լ', 'հատ', 1.65, 6)
CUP = wb.Product(12, 'A-1', 'Բաժակ', 'տուփ', 0.2, None)
PRODUCTS = {p.id: p for p in (WATER, COLA, CUP)}


# ============================== деление строки между рейсами ==============================

def test_split_qty_examples():
    assert [wb.split_qty(50, 3, i, 6) for i in range(3)] == [19, 19, 12]     # упаковки 3/3/2, штуки 1/1/0
    assert [wb.split_qty(60, 4, i, 12) for i in range(4)] == [24, 12, 12, 12]   # 5 упаковок: целыми
    assert [wb.split_qty(7, 2, i) for i in range(2)] == [4, 3]
    assert [wb.split_qty(-7, 2, i) for i in range(2)] == [-4, -3]
    assert [wb.split_qty(2.5, 2, i) for i in range(2)] == [1.25, 1.25]
    assert [wb.split_qty(0.0003, 2, i) for i in range(2)] == [0.0002, 0.0001]
    assert wb.split_qty(13, 1, 0, 6) == 13.0 and isinstance(wb.split_qty(13, 1, 0), float)
    for bad in ((5, 0, 0), (5, 2, 2), (5, 2, -1)):
        with pytest.raises(ValueError):
            wb.split_qty(*bad)


@pytest.mark.parametrize('pack', [None, 1, 2, 6, 12])
def test_split_qty_sums_exactly_and_keeps_packs(pack):
    for qty, parts in itertools.product(range(0, 130), range(1, 6)):
        shares = [wb.split_qty(qty, parts, i, pack) for i in range(parts)]
        assert sum(shares) == qty, (qty, parts, pack, shares)
        assert all(float(x).is_integer() for x in shares)
        assert max(shares) - min(shares) <= (pack or 1) + 1           # поровну с точностью до упаковки и штуки
        if pack and pack > 1 and qty % pack == 0:
            assert all(x % pack == 0 for x in shares), (qty, parts, pack, shares)


def test_split_qty_fractional_sums_to_erp_precision():
    for qty in (0.0001, 1.2345, 7.5, 12.3456):
        for parts in range(1, 5):
            shares = [wb.split_qty(qty, parts, i) for i in range(parts)]
            assert round(sum(shares), 4) == qty


def test_split_stop_balances_trips_by_kg():
    """Замечание ревью: остаток каждой строки первому рейсу копился — 30 строк по упаковке давали рейсы [все, 0]."""
    heavy = {1: wb.Product(1, '1', 'Ջուր 19լ', 'հատ', 19.0, None), 2: wb.Product(2, '2', 'Կոլա', 'հատ', 1.5, 6)}
    shares = wb.split_stop([(2, 6.0)] * 30, 2, heavy)                 # 30 строк по упаковке → 15 упаковок на рейс
    assert [x[2] for x in shares] == [90.0, 90.0]
    shares = wb.split_stop([(1, 7.0)] * 20, 2, heavy)                 # 20 × 7 бутылей → 70 / 70, а не 80 / 60
    assert [x[1] for x in shares] == [70.0, 70.0]
    big = {3: wb.Product(3, '3', 'Ջուր 19լ', 'հատ', 19.0, None), 2: heavy[2]}
    shares = wb.split_stop([(2, 6.0), (2, 6.0), (2, 6.0), (3, 1.0)], 2, big)   # тяжёлое — первым (не по номеру товара)
    assert shares == [{3: 1.0, 2: 6.0}, {2: 12.0}]
    assert wb.split_stop([(1, 5.0), (1, 2.0), (99, 3.0)], 1, heavy) == [{1: 7.0, 99: 3.0}]
    assert wb.split_stop([], 3, heavy) == [{}, {}, {}]
    with pytest.raises(ValueError):
        wb.split_stop([(1, 1.0)], 0, heavy)


def test_split_stop_property_exact_and_even():
    rnd = random.Random(57)
    products = {pid: wb.Product(pid, str(pid), '', 'հատ', rnd.choice((0.0, 0.5, 1.65, 6.03, 19.9)), rnd.choice((None, 2, 6, 12)))
                for pid in range(1, 9)}
    for _ in range(400):
        lines = [(rnd.randint(1, 9), float(rnd.randint(0, 60))) for _ in range(rnd.randint(0, 25))]
        parts = rnd.randint(1, 5)
        shares = wb.split_stop(lines, parts, products)
        for pid in {x for x, _ in lines}:
            assert sum(sh.get(pid, 0.0) for sh in shares) == sum(q for x, q in lines if x == pid)
        assert all(q > 0 and q.is_integer() for sh in shares for q in sh.values())
        kg = [sum(q * (products[x].kg if x in products else 0.0) for x, q in sh.items()) for sh in shares]
        heaviest = max([products[x].kg * (products[x].pack or 1) for x, _ in lines if x in products] + [0.0])
        assert max(kg) - min(kg) <= heaviest + 1e-6, (lines, parts, kg)
        again = wb.split_stop(list(reversed(lines)), parts, products)      # порядок строк не важен: у каждой машины так же
        assert again == shares


def test_split_stop_fractional_and_negative():
    shares = wb.split_stop([(1, 2.5), (2, -7.0)], 2, {})
    assert [x[1] for x in shares] == [1.25, 1.25] and [x[2] for x in shares] == [-4.0, -3.0]
    # 0.1 + 0.2 + 0.7 в одном порядке float даёт 1.0, в другом 0.9999…: целое число штук — при любом порядке строк
    lines = [(1, 0.1), (1, 0.2), (1, 0.7)]
    assert wb.split_stop(lines, 3, {}) == wb.split_stop(lines[::-1], 3, {}) == [{1: 1.0}, {}, {}]
    lines = [(1, 0.1234), (1, 1.5), (1, 0.0007)]
    shares = wb.split_stop(lines, 3, {})
    assert shares == wb.split_stop(lines[::-1], 3, {}) and round(sum(x[1] for x in shares), 4) == 1.6241


def test_pack_size():
    assert wb.pack_size(True, 6, 1) == 6 and wb.pack_size(1, 12.0, 1.0) == 12 and wb.pack_size(True, 12, 2) == 6
    for args in ((False, 6, 1), (True, 1, 1), (True, 2.5, 1), (True, 0, 0), (True, 6, 0), (True, None, 1),
                 (True, 'x', 1), (None, 6, 1)):
        assert wb.pack_size(*args) is None, args


# ============================== накладная машины по плану ==============================

def _stop(cid, isns, share=1):
    return {'customer_id': cid, 'share': share, 'orders': [{'isn': i} for i in isns]}


def _trip(tid, stops, depart='09:30'):
    return {'id': tid, 'loading_start': '09:00', 'depart': depart, 'return': '13:00', 'stops': stops}


def _plan():
    """Машина A: рейс 1 (магазины 101, 102), рейс 2 (101 — второй раз); машина B: рейс 3 (101 — третий раз).
    Тяжёлый магазин 101 — три рейса на двух машинах."""
    return {'trucks': [
        {'car_code': 'A', 'name': 'HOWO', 'trips': [_trip(1, [_stop(101, ['o1', 'o2'], 3), _stop(102, ['o3'])]),
                                                    _trip(2, [_stop(101, ['o1', 'o2'], 3)], depart='13:30')]},
        {'car_code': 'B', 'name': None, 'trips': [_trip(3, [_stop(101, ['o1', 'o2'], 3)])]}]}


def _lines():
    return wb.Lines(by_order={'O1': ((10, 50.0), (11, 60.0)), 'O2': ((10, 4.0),), 'O3': ((11, 7.0), (12, 3.0), (99, 2.0))},
                    invoiced=frozenset({'O1', 'O3'}), products=PRODUCTS)


def _qty(trip):
    return {r['product_id']: r['qty'] for r in trip['rows']}


def test_truck_waybill_trips_rows_and_counts():
    got = wb.truck_waybill(_plan(), 'A', _lines())
    assert (got['car_code'], got['name'], [t['no'] for t in got['trips']], [t['id'] for t in got['trips']]) == \
        ('A', 'HOWO', [1, 2], [1, 2])
    t1, t2 = got['trips']
    # рейс 1: 101 — первая из трёх долей (вода 50 + 4 = 27 упаковок по 2 → 9/9/9; кола 60 → 10 упаковок → 3/3/3 и одна —
    # самому лёгкому, при равенстве первому); 102 — целиком; товар 99 в ERP не найден
    assert _qty(t1) == {10: 18, 11: 24 + 7, 12: 3, 99: 2}
    assert [r['code'] for r in t1['rows']] == ['1113', '2801', '', 'A-1']        # числовые коды по числу, затем прочие
    cola = next(r for r in t1['rows'] if r['product_id'] == 11)
    assert (cola['unit'], cola['pack'], cola['packs'], cola['loose'], cola['kg']) == ('հատ', 6, 5, 1, round(31 * 1.65, 1))
    cup = next(r for r in t1['rows'] if r['product_id'] == 12)
    assert (cup['pack'], cup['packs'], cup['loose']) == (None, None, None)
    unknown = next(r for r in t1['rows'] if r['product_id'] == 99)
    assert (unknown['unknown'], unknown['code'], unknown['name'], unknown['kg'], t1['unknown']) == (True, '', '', 0.0, 1)
    assert not any(r['unknown'] for r in t1['rows'] if r['product_id'] != 99)
    assert (t1['stops'], t1['orders'], t1['invoiced'], t1['mixed'], t1['split']) == (2, 3, 2, 0, 1)
    assert t1['kg'] == round(18 * 6.03 + 31 * 1.65 + 3 * 0.2)
    assert t1['basis'] == [[101, 3, ['o1', 'o2']], [102, 1, ['o3']]]
    assert (t1['loading_start'], t1['depart'], t1['return']) == ('09:00', '09:30', '13:00')
    assert _qty(t2) == {10: 18, 11: 18} and (t2['orders'], t2['invoiced'], t2['split']) == (2, 1, 1)


def test_truck_waybill_split_sums_to_documents_across_trucks():
    lines = _lines()
    total: dict = {}
    for code in ('A', 'B'):
        for t in wb.truck_waybill(_plan(), code, lines)['trips']:
            for pid, q in _qty(t).items():
                total[pid] = total.get(pid, 0) + q
    docs: dict = {}
    for isn in ('O1', 'O2', 'O3'):
        for pid, q in lines.by_order[isn]:
            docs[pid] = docs.get(pid, 0) + q
    assert total == docs


def test_truck_waybill_missing_truck_and_empty_lines():
    assert wb.truck_waybill(_plan(), 'ZZZ', _lines()) is None
    got = wb.truck_waybill(_plan(), 'B', wb.Lines({}, frozenset(), {}))
    assert got['trips'][0]['rows'] == [] and got['trips'][0]['kg'] == 0 and got['trips'][0]['invoiced'] == 0


# ============================== чтение ERP (поддельное соединение) ==============================

class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def execute(self, sql, params=None):
        self.conn.calls.append((sql, list(params or [])))
        p = list(params or [])
        if 'FROM DOCPARENTS' in sql and 'p.fPARENTISN IN' in sql:
            self.rows = [(o.lower(), s) for o, s in self.conn.invoices if o.upper() in p]
        elif 'FROM DOCPARENTS' in sql:
            self.rows = [(s.lower(), o) for o, s in self.conn.invoices if s.upper() in p]
        elif 'FROM SALEDOCDETAILS' in sql:
            self.rows = [(d.lower(), pid, q) for d, pid, q in self.conn.lines if d.upper() in p]
        elif 'FROM PRODUCTS' in sql:
            self.rows = [r for r in self.conn.products if r[0] in p]
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Conn:
    def __init__(self, invoices, lines, products):
        self.invoices, self.lines, self.products, self.calls, self.closed = invoices, lines, products, [], False

    def cursor(self):
        return _Cursor(self)

    def close(self):
        self.closed = True


def test_load_lines_prefers_posted_invoice(monkeypatch):
    o1, o2, s1 = _isn(1), _isn(2), _isn(91)
    conn = _Conn(invoices=[(o1, s1)],
                 lines=[(o1, 10, 50), (s1, 10, 48), (s1, 11, 6), (o2, 11, 12)],
                 products=[(10, '2801 ', 'Գառնի 6լ', 'հատ', 6.03, True, 2, 1), (11, '1113', 'Կոլա', 'հատ', None, False, 6, 1)])
    monkeypatch.setattr(erp, 'connect', lambda cs: conn)
    got = wb.load_lines('DRIVER={none};', [o1.lower(), o2, o2])
    assert got.by_order == {o1: ((10, 48.0), (11, 6.0)), o2: ((11, 12.0),)}       # заказ o1 — по накладной s1
    assert got.invoiced == frozenset({o1})
    assert got.products == {10: wb.Product(10, '2801', 'Գառնի 6լ', 'հատ', 6.03, 2),
                            11: wb.Product(11, '1113', 'Կոլա', 'հատ', 0.0, None)}
    lines_sql = [p for sql, p in conn.calls if 'FROM SALEDOCDETAILS' in sql]
    assert lines_sql == [sorted([o2, s1])]                  # строки заказа с накладной не читаются
    assert conn.closed
    # накладная — только проведённая (fSTATE = 2) и сделанная из заказа (fPARENTDOCTYPE = 1), как shipped в erp.SQL_DISPATCH_ORDERS
    assert 'p.fPARENTDOCTYPE = 1 AND s.fSTATE = 2' in wb.SQL_ORDER_INVOICES
    for sql, _ in conn.calls:                               # read-only guard пропускает, у каждой таблицы NOLOCK
        erp.check_sql(sql)
        assert sql.count('WITH (NOLOCK)') == sql.count('FROM ') + sql.count('JOIN ')


def test_load_lines_invoice_of_several_orders_counted_once(monkeypatch):
    """Замечание ревью: накладная из заказов o1 и o2 одного магазина отдавалась обоим — товар вдвое. Теперь — у заказа с
    наименьшим fISN; накладная s2 сделана и из заказа ox, которого в рейсах нет (напр. «не везём сегодня»), — mixed."""
    o1, o2, o3, ox, s1, s2 = _isn(1), _isn(2), _isn(3), _isn(50), _isn(91), _isn(92)
    conn = _Conn(invoices=[(o1, s1), (o2, s1), (o3, s2), (ox, s2)],
                 lines=[(s1, 10, 10), (s2, 11, 12), (o1, 10, 99), (o2, 10, 99), (o3, 11, 99)],
                 products=[(10, '2801', 'Ջուր', 'հատ', 6.03, True, 2, 1), (11, '1113', 'Կոլա', 'հատ', 1.65, True, 6, 1)])
    monkeypatch.setattr(erp, 'connect', lambda cs: conn)
    got = wb.load_lines('DRIVER={none};', [o2, o1, o3])
    assert got.by_order == {o1: ((10, 10.0),), o2: (), o3: ((11, 12.0),)}
    assert got.invoiced == frozenset({o1, o2, o3}) and got.mixed == frozenset({o3})
    plan = {'trucks': [{'car_code': 'A', 'name': None, 'trips': [_trip(1, [_stop(101, [o1, o2]), _stop(103, [o3])])]}]}
    t = wb.truck_waybill(plan, 'A', got)['trips'][0]
    assert (_qty(t), t['orders'], t['invoiced'], t['mixed']) == ({10: 10, 11: 12}, 3, 3, 1)


def test_load_lines_closes_connection_on_error(monkeypatch):
    conn = _Conn([], [], [])
    conn.cursor = lambda: (_ for _ in ()).throw(erp.pyodbc.Error('boom'))
    monkeypatch.setattr(erp, 'connect', lambda cs: conn)
    with pytest.raises(ErpError):
        wb.load_lines('DRIVER={none};', [_isn(1)])
    assert conn.closed


# ============================== API ==============================

@pytest.fixture
def day(client):
    """План 01.10 на CAR1 и CAR2; строки заказов — подделка (запоминает, какие заказы спросили)."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2)])
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200, r.get_json()
    asked = []

    def loader(isns):
        asked.append(sorted(isns))
        return wb.Lines({i.upper(): ((10, 12.0),) for i in isns}, frozenset({_isn(1)}), PRODUCTS)

    client.application.extensions['route_optimizer'].waybill_loader = loader
    return client.get('/api/routes/dispatch?date=2026-10-01').get_json(), asked


def _get(client, **q):
    return client.get('/api/routes/dispatch/waybill', query_string=q)


def test_api_waybill_matches_page_plan(client, day):
    page, asked = day
    truck = page['plan']['trucks'][0]
    r = _get(client, date='2026-10-01', truck=truck['car_code'], rev=page['rev'])
    assert r.status_code == 200 and r.headers['Cache-Control'].startswith('no-cache')
    got = r.get_json()
    assert (got['success'], got['day'], got['rev'], got['car_code']) == (True, '2026-10-01', page['rev'], truck['car_code'])
    assert [t['id'] for t in got['trips']] == [t['id'] for t in truck['trips']]
    for t, tr in zip(got['trips'], truck['trips']):            # то же, что сверяет страница (wbBasis)
        assert t['basis'] == [[s['customer_id'], s['share'], sorted(o['isn'] for o in s['orders'])] for s in tr['stops']]
        assert (t['depart'], t['loading_start'], t['return']) == (tr['depart'], tr['loading_start'], tr['return'])
    assert asked == [sorted(o['isn'] for tr in truck['trips'] for s in tr['stops'] for o in s['orders'])]
    assert _get(client, date='2026-10-01', truck=truck['car_code']).status_code == 200    # без rev — без сверки


def test_api_waybill_stale_and_missing(client, day):
    page, asked = day
    code = page['plan']['trucks'][0]['car_code']
    for q in ({'rev': page['rev'] + 1}, {'rev': page['rev'] - 1}):
        r = _get(client, date='2026-10-01', truck=code, **q)
        assert r.status_code == 409 and r.get_json() == {'success': False, 'error': views.WAYBILL_STALE, 'stale': True}
    r = _get(client, date='2026-10-01', truck='NOPE', rev=page['rev'])
    assert r.status_code == 409 and r.get_json()['error'] == views.WAYBILL_NO_TRUCK
    r = _get(client, date='2026-10-02', truck=code)                 # день без собранных рейсов
    assert r.status_code == 409 and r.get_json()['error'] == views.WAYBILL_NO_PLAN
    assert client.post('/api/routes/settings', json={'depot': None}).status_code == 200   # склад убрали после сборки
    r = _get(client, date='2026-10-01', truck=code)
    assert r.status_code == 400 and r.get_json()['error'] == views.WAYBILL_NO_SETUP
    assert asked == []                                              # ERP не читали


def test_api_waybill_bad_request(client, day):
    page, _ = day
    code = page['plan']['trucks'][0]['car_code']
    for q in ({'truck': code}, {'date': '2026-13-01', 'truck': code}, {'date': '2026-10-01'},
              {'date': '2026-10-01', 'truck': ' '}, {'date': '2026-10-01', 'truck': 'X' * 65},
              {'date': '2026-10-01', 'truck': code, 'rev': '-1'}, {'date': '2026-10-01', 'truck': code, 'rev': '1.0'},
              {'date': '2026-10-01', 'truck': code, 'rev': ''}):
        assert _get(client, **q).status_code == 400, q


def test_api_waybill_heavy_store_over_trips_sums_to_documents(client):
    """Магазин 104 тяжелее любой машины: настоящий plan_view везёт его несколькими рейсами (share > 1); накладные всех
    машин вместе дают ровно его документы, каждая — сверка basis с планом страницы."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(3, 104, 14000.0, agent=2), _dorder(4, 104, 2000.0, agent=2)])
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200, r.get_json()
    docs = {_isn(1): ((10, 40.0),), _isn(3): ((10, 1400.0), (11, 61.0)), _isn(4): ((11, 7.0),)}
    client.application.extensions['route_optimizer'].waybill_loader = lambda isns: wb.Lines(
        {i.upper(): docs[i.upper()] for i in isns}, frozenset(), PRODUCTS)
    page = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    shares = {s['share'] for t in page['plan']['trucks'] for tr in t['trips'] for s in tr['stops'] if s['customer_id'] == 104}
    assert shares and min(shares) > 1, shares
    total: dict = {}
    for t in page['plan']['trucks']:
        got = _get(client, date='2026-10-01', truck=t['car_code'], rev=page['rev']).get_json()
        for x, tr in zip(got['trips'], t['trips']):
            assert x['basis'] == [[s['customer_id'], s['share'], sorted(o['isn'] for o in s['orders'])] for s in tr['stops']]
            for pid, q in _qty(x).items():
                total[pid] = total.get(pid, 0) + q
    assert total == {10: 1440, 11: 68}


def test_api_waybill_erp_down_is_503(client, day):
    page, _ = day

    def down(isns):
        raise ErpError('нет связи')

    client.application.extensions['route_optimizer'].waybill_loader = down
    r = _get(client, date='2026-10-01', truck=page['plan']['trucks'][0]['car_code'], rev=page['rev'])
    assert r.status_code == 503 and r.get_json()['success'] is False

# -*- coding: utf-8 -*-
"""«Գրել ERP-ում» (ответ владельца №97, docs/plans/erp-write-plan.md): сайт пишет в заказы дня առաքիչ и машину по
отправленному водителям плану «Развоза». Единственная запись в боевую ERP — route_optimizer.erp_write. Здесь: правка fBODY
(rewrite_body), решения по заказам (plan_assignments), запись подделкой транзакционного соединения (apply: успех,
оптимистичный конфликт, дочерний документ, сбой посреди записи), состав SQL записи, API (доступ, прошедший день,
неотправленный план, отпечаток, выключено без DSN), «Վերադարձնել» и миграция схемы 29. Ни ERP, ни CT115 не трогаются.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_erp_write.py -q
"""
import ast
import copy
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import erp_write as ew  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.erp import Agent, Car, OrderState, check_sql, pyodbc  # noqa: E402
from test_route_dispatch_same_day import DAY, _build, _page_setup, _plan_stops, client  # noqa: E402,F401
from test_route_optimizer import _SQL_WRITE_RE, _isn  # noqa: E402

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN)
I1, I2, I3, I4 = _isn(1), _isn(2), _isn(3), _isn(4)
AGENTS = {1: Agent(1, 'A001', 'Менеджер 1', False), 2: Agent(2, 'A002', 'Менеджер 2', False),
          3199: Agent(3199, 'B001/1', 'Արամ  Պետրոսյան', False),          # в ERP двойной пробел
          3200: Agent(3200, 'B002/1', 'Գագիկ Սարգսյան', False),
          3201: Agent(3201, 'B003/1', 'Գագիկ Սարգսյան', True),            # закрытый тёзка — не в счёт
          3300: Agent(3300, 'B004/1', 'Վարդան Մկրտչյան', False),
          3301: Agent(3301, 'B005/1', 'վարդան մկրտչյան', False)}           # открытые тёзки
CARS = {'333DO33': Car('333DO33', 'HOWO', False), '504 CQ 61': Car('504 CQ 61', 'FORD', False),
        'OLD1': Car('OLD1', 'старая', True)}


def body(van=1, car=None, head='\r\nDOCNUM:N1\r\nDATE:20261001', tail='\r\nCOMMENT:տուն 5\r\n'):
    """fBODY заказа в виде ERP: строки \\r\\nKEY:VALUE\\r\\n."""
    lines = head + f'\r\nVANAGENTID:{van}' + (f'\r\nDELIVERYCAR:{car}' if car is not None else '') + tail
    return lines


def _rest(text):
    return [x for x in text.split('\r\n') if not x.startswith('VANAGENTID:') and not x.startswith('DELIVERYCAR:')]


# ============================== fBODY ==============================

def test_rewrite_body_replaces_van_and_inserts_car_after_it():
    old = body(van=1)
    new = ew.rewrite_body(old, 3199, '333DO33')
    assert new == '\r\nDOCNUM:N1\r\nDATE:20261001\r\nVANAGENTID:3199\r\nDELIVERYCAR:333DO33\r\nCOMMENT:տուն 5\r\n'
    assert _rest(new) == _rest(old) and ew.body_values(new) == ('3199', '333DO33')


def test_rewrite_body_replaces_car_with_spaces_and_removes_empty_car():
    old = body(van=1, car='OLD 1', tail='\r\nX:1\r\nY: два слова \r\n')
    new = ew.rewrite_body(old, 7, '504 CQ 61')
    assert new == old.replace('VANAGENTID:1', 'VANAGENTID:7').replace('DELIVERYCAR:OLD 1', 'DELIVERYCAR:504 CQ 61')
    assert ew.body_values(new) == ('7', '504 CQ 61')
    gone = ew.rewrite_body(old, 7, '')
    assert 'DELIVERYCAR' not in gone and gone == old.replace('VANAGENTID:1', 'VANAGENTID:7').replace('\r\nDELIVERYCAR:OLD 1', '')
    assert ew.body_values(gone) == ('7', None)
    # None — машина не меняется: строка как была (и отсутствующая не появляется)
    assert ew.rewrite_body(old, 7, None) == old.replace('VANAGENTID:1', 'VANAGENTID:7')
    assert ew.rewrite_body(body(van=1), 7, None) == body(van=7)
    assert ew.rewrite_body(body(van=1), 7, '') == body(van=7)        # пустую и так нет


def test_rewrite_body_keeps_every_other_byte():
    """Всё, кроме двух строк, — до байта: пустые строки, \\n внутри значения, кириллица и армянский, край текста."""
    head = 'HDR:1\r\n\r\nNOTE:строка\nвторая\r\nA:  пробелы  '
    old = head + '\r\nDELIVERYCAR:OLD1\r\nZ:9\r\nVANAGENTID:5\r\nՀ:ա'
    new = ew.rewrite_body(old, 3200, '333DO33')
    assert new == head + '\r\nDELIVERYCAR:333DO33\r\nZ:9\r\nVANAGENTID:3200\r\nՀ:ա'   # машина — на своём месте
    assert _rest(new) == _rest(old) and new.startswith(head) and new.endswith('\r\nՀ:ա')


@pytest.mark.parametrize('bad', [
    '\r\nDOCNUM:N1\r\nDELIVERYCAR:X\r\n',                                  # нет VANAGENTID
    '\r\nVANAGENTID:1\r\nVANAGENTID:2\r\n',                                # ключ дважды
    '\r\nVANAGENTID:1\r\nDELIVERYCAR:A\r\nDELIVERYCAR:B\r\n',
    '\nVANAGENTID:1\nDELIVERYCAR:A\n',                                     # строки без \r — не наш вид
    '\r\nXVANAGENTID:1\r\nVANAGENTIDX:2\r\n',                              # похожие ключи — не ключи
    b'\r\nVANAGENTID:1\r\n',                                               # не текст
    None,
])
def test_rewrite_body_refuses_unexpected_format(bad):
    with pytest.raises(ew.BodyFormatError):
        ew.rewrite_body(bad, 3199, '333DO33')


@pytest.mark.parametrize('van, car', [(0, 'A'), (-1, 'A'), (True, 'A'), ('3199', 'A'), (3199, 'A\r\nX:1'),
                                      (3199, ' A'), (3199, 'A\n')])
def test_rewrite_body_refuses_bad_values(van, car):
    with pytest.raises(ew.BodyFormatError):
        ew.rewrite_body(body(), van, car)


# ============================== решения ==============================

def _o(isn, cid, *, van=1, car='', state=2, children=False):
    return OrderState(isn, 'N' + isn[-1], cid, state, van, car, children)


DRIVERS = {'333DO33': 'Արամ Պետրոսյան', '504 CQ 61': 'գագիկ  սարգսյան', 'MANUAL': 'Արամ Պետրոսյան',
           'TWINS': 'Վարդան Մկրտչյան', 'NOBODY': None, 'GHOST': 'Չկա Այդպիսի', 'OLD1': 'Արամ Պետրոսյան'}


def _plan(trips, orders, drivers=DRIVERS):
    return {d.isn: d for d in ew.plan_assignments(trips, orders, drivers, AGENTS, CARS)}


def test_plan_writes_courier_and_car_of_the_truck():
    got = _plan([('333DO33', [101, 102]), ('504 CQ 61', [103])],
                [_o(I1, 101), _o(I2, 102, van=3199, car='333DO33'), _o(I3, 103, car='OLD1')])
    a, b, c = got[I1], got[I2], got[I3]
    assert (a.write, a.reason, a.old_van, a.new_van, a.old_car, a.new_car, a.truck) == \
        (True, '', 1, 3199, '', '333DO33', '333DO33')
    assert (a.old_code, a.new_code, a.new_name) == ('A001', 'B001/1', 'Արամ Պետրոսյան')   # имя без двойного пробела
    assert (b.write, b.reason) == (False, 'unchanged')                                     # повтор ничего не пишет
    assert (c.write, c.new_van, c.new_car, c.new_code) == (True, 3200, '504 CQ 61', 'B002/1')   # закрытый тёзка — мимо


def test_plan_car_not_in_erp_or_closed_writes_courier_only():
    got = _plan([('MANUAL', [101]), ('OLD1', [102])], [_o(I1, 101, car='X 1'), _o(I2, 102)])
    for isn, car in ((I1, 'X 1'), (I2, '')):
        d = got[isn]
        assert (d.write, d.reason, d.new_van, d.old_car, d.new_car, d.car_changes) == \
            (True, ew.CAR_NOT_IN_ERP, 3199, car, car, False)
    # առաքիչ уже тот — ничего
    assert _plan([('MANUAL', [101])], [_o(I1, 101, van=3199)])[I1].reason == 'unchanged'


def test_plan_car_only_change_writes():
    d = _plan([('333DO33', [101])], [_o(I1, 101, van=3199, car='OLD1')])[I1]
    assert (d.write, d.old_van, d.new_van, d.car_changes, d.new_car) == (True, 3199, 3199, True, '333DO33')


def test_plan_skip_reasons():
    trips = [('333DO33', [101, 105, 106]), ('504 CQ 61', [105]), ('TWINS', [107]), ('NOBODY', [108]), ('GHOST', [109]),
             ('333DO33', [110])]
    orders = [_o(_isn(0x10), 100), _o(_isn(0x11), 105), _o(_isn(0x12), 106, state=1, children=True), _o(_isn(0x13), 107),
              _o(_isn(0x14), 108), _o(_isn(0x15), 109), _o(_isn(0x16), 110, state=0), _o(_isn(0x17), 110, state=3),
              _o(_isn(0x18), 101, state=1)]
    got = _plan(trips, orders)
    assert {k[-2:]: (v.write, v.reason) for k, v in got.items()} == {
        '10': (False, 'not_in_plan'), '11': (False, 'split_store'), '12': (False, 'has_child_docs'),
        '13': (False, 'courier_ambiguous'), '14': (False, 'courier_not_found'), '15': (False, 'courier_not_found'),
        '16': (False, 'order_state'), '17': (False, 'order_state'), '18': (True, '')}
    assert got[_isn(0x11)].truck == '333DO33, 504 CQ 61' and got[_isn(0x10)].truck == ''
    # пропуск ничего не меняет: стало = было
    assert all((d.new_van, d.new_car) == (d.old_van, d.old_car) for d in got.values() if not d.write)
    assert set(ew.SKIP_REASONS) == {d.reason for d in got.values() if not d.write} | {'unchanged'}
    assert all(r in ew.REASON_HY for r in (*ew.SKIP_REASONS, *ew.FAIL_REASONS, ew.CAR_NOT_IN_ERP, ew.VERIFY_FAILED))


def test_plan_same_truck_in_two_trips_is_not_split():
    d = _plan([('333DO33', [101]), ('333DO33', [102, 101])], [_o(I1, 101)])[I1]
    assert (d.write, d.truck) == (True, '333DO33')


def test_courier_of_names_and_closed():
    assert ew.courier_of('  արամ   ՊԵՏՐՈՍՅԱՆ ', AGENTS)[0].id == 3199
    assert ew.courier_of('Վարդան Մկրտչյան', AGENTS) == (None, 'courier_ambiguous')
    assert ew.courier_of('', AGENTS) == (None, 'courier_not_found') == ew.courier_of(None, AGENTS)
    closed = {k: Agent(a.id, a.code, a.name, True) for k, a in AGENTS.items()}
    assert ew.courier_of('Արամ Պետրոսյան', closed) == (None, 'courier_not_found')


def test_signature_changes_with_any_field():
    ds = ew.plan_assignments([('333DO33', [101])], [_o(I1, 101), _o(I2, 102)], DRIVERS, AGENTS, CARS)
    sig = ew.signature(ds)
    assert sig == ew.signature(list(reversed(ds))) and len(sig) == 32
    ds2 = ew.plan_assignments([('333DO33', [101])], [_o(I1, 101, van=2), _o(I2, 102)], DRIVERS, AGENTS, CARS)
    assert ew.signature(ds2) != sig
    ds3 = ew.plan_assignments([('333DO33', [101])], [_o(I1, 101), _o(I2, 102, car='X')], DRIVERS, AGENTS, CARS)
    assert ew.signature(ds3) != sig


def test_change_description_like_erp_and_comment():
    d = _plan([('333DO33', [101])], [_o(I1, 101, van=2, car='OLD1')])[I1]
    assert ew.change_description(d) == '("Առաքիչ", "A002" -> "B001/1"), ("Առաքող ավտոմեքենա", "OLD1" -> "333DO33")'
    d = _plan([('MANUAL', [101])], [_o(I1, 101, van=0)])[I1]
    assert ew.change_description(d) == '("Առաքիչ", "" -> "B001/1")'
    d = _plan([('333DO33', [101])], [_o(I1, 101, van=3199)])[I1]
    assert ew.change_description(d) == '("Առաքող ավտոմեքենա", "" -> "333DO33")'
    assert ew.log_comment('boss') == 'Կայք՝ boss' and ew.log_comment(None) == 'Կայք՝ '
    assert len(ew.log_comment('x' * 500)) == ew.COMMENT_MAX == 100


# ============================== подделка транзакционной ERP ==============================

class FakeErp:
    """ORDERS / DOCUMENTS / DOCPARENTS / DOCUMENTSLOG в памяти с транзакцией (autocommit выключен: первый оператор
    начинает её, rollback — всё назад). Принимает только SQL из erp_write.SQL_W_* (иначе AssertionError). hooks: имя
    константы → fn(erp, params) перед выполнением (может бросить или изменить данные «другим пользователем»)."""

    def __init__(self, orders, bodies, children=None):
        self.orders = {k: dict(v) for k, v in orders.items()}       # isn → {state, van, car (как в столбце)}
        self.docs = {k: {'body': b, 'ts': b'\x00' * 7 + bytes([1]), 'mod': None} for k, b in bodies.items()}
        self.children = dict(children or {})
        self.log = []
        self.calls = []
        self.hooks = {}
        self.verify_override = None
        self.tx = None
        self.commits = self.rollbacks = 0
        self.closed = False
        self.names = {getattr(ew, n): n for n in dir(ew) if n.startswith('SQL_W_')}

    def _state(self):
        return copy.deepcopy((self.orders, self.docs, self.children, self.log))

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.tx = None
        self.commits += 1

    def rollback(self):
        if self.tx is not None:
            self.orders, self.docs, self.children, self.log = self.tx
        self.tx = None
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeCursor:
    def __init__(self, erp):
        self.erp, self.rows, self.rowcount = erp, [], -1

    def execute(self, sql, params=()):
        e = self.erp
        name = e.names.get(sql)
        assert name is not None, f'SQL мимо erp_write.SQL_W_*: {sql!r}'
        assert isinstance(params, tuple) and all(not isinstance(p, (list, dict)) for p in params)
        if e.tx is None:
            e.tx = e._state()
        e.calls.append((name, params))
        if name in e.hooks:
            e.hooks[name](e, params)
        self.rows, self.rowcount = [], -1
        if name == 'SQL_W_ORDER_CAR':
            o = e.orders.get(params[0])
            self.rows = [(o['car'],)] if o else []
        elif name in ('SQL_W_UPDATE_ORDER', 'SQL_W_UPDATE_ORDER_VAN'):
            if name == 'SQL_W_UPDATE_ORDER':
                van, car, isn, state, old_van, old_car = params
            else:
                (van, isn, state, old_van, old_car), car = params, None
            o = e.orders.get(isn)
            hit = (o is not None and o['state'] == state and (o['van'] or 0) == old_van
                   and (o['car'] or '').strip() == old_car and not e.children.get(isn))
            if hit:
                o['van'] = van
                if name == 'SQL_W_UPDATE_ORDER':
                    o['car'] = car
            self.rowcount = int(hit)
        elif name == 'SQL_W_CHILDREN':
            self.rows = [(e.children.get(params[0], 0),)]
        elif name == 'SQL_W_BODY':
            d = e.docs.get(params[0])
            self.rows = [(d['body'], d['ts'])] if d else []
        elif name == 'SQL_W_UPDATE_BODY':
            new, isn, ts = params
            d = e.docs.get(isn)
            hit = d is not None and d['ts'] == ts
            if hit:
                d['body'], d['mod'] = new, ('GETDATE()', 0)
                d['ts'] = (int.from_bytes(d['ts'], 'big') + 1).to_bytes(8, 'big')
            self.rowcount = int(hit)
        elif name == 'SQL_W_LOG':
            isn, state, comment, comp, desc = params
            e.log.append({'isn': isn, 'state': state, 'comment': comment, 'compname': comp, 'desc': desc})
            self.rowcount = 1
        elif name == 'SQL_W_VERIFY':
            o, d = e.orders.get(params[0]), e.docs.get(params[0])
            self.rows = [(o['van'], (o['car'] or '').strip(), d['body'])] if o and d else []
            if e.verify_override is not None:
                self.rows = e.verify_override
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


def _erp(**over):
    """Два заказа: I1 (առաքիչ — менеджер 1, без машины, fDELIVERYCAR NULL), I2 (առաքիչ 2, машина OLD1)."""
    orders = {I1: {'state': 2, 'van': 1, 'car': None}, I2: {'state': 1, 'van': 2, 'car': 'OLD1 '}}
    bodies = {I1: body(van=1), I2: body(van=2, car='OLD1')}
    orders.update(over.get('orders', {}))
    bodies.update(over.get('bodies', {}))
    return FakeErp(orders, bodies, over.get('children'))


def _decisions(erp, trips=(('333DO33', [101]), ('504 CQ 61', [102]))):
    states = [OrderState(i, 'N' + i[-1], 101 if i == I1 else 102, o['state'], o['van'] or 0, (o['car'] or '').strip(),
                         bool(erp.children.get(i))) for i, o in sorted(erp.orders.items())]
    return ew.plan_assignments(list(trips), states, DRIVERS, AGENTS, CARS)


def test_apply_success_exact_sql_and_values():
    erp = _erp()
    ds = _decisions(erp)
    seen = []
    res = ew.apply(erp, ds, 'boss', on_result=seen.append)
    assert [(r.decision.isn, r.ok, r.reason) for r in res] == [(I1, True, ''), (I2, True, '')] and seen == res
    assert erp.orders[I1] == {'state': 2, 'van': 3199, 'car': '333DO33'}
    assert erp.orders[I2] == {'state': 1, 'van': 3200, 'car': '504 CQ 61'}
    assert erp.docs[I1]['body'] == ew.rewrite_body(body(van=1), 3199, '333DO33') and erp.docs[I1]['mod'] == ('GETDATE()', 0)
    assert _rest(erp.docs[I2]['body']) == _rest(body(van=2, car='OLD1'))
    assert erp.log == [
        {'isn': I1, 'state': 2, 'comment': 'Կայք՝ boss', 'compname': 'ARAQICH-SITE',
         'desc': '("Առաքիչ", "A001" -> "B001/1"), ("Առաքող ավտոմեքենա", "" -> "333DO33")'},
        {'isn': I2, 'state': 1, 'comment': 'Կայք՝ boss', 'compname': 'ARAQICH-SITE',
         'desc': '("Առաքիչ", "A002" -> "B002/1"), ("Առաքող ավտոմեքենա", "OLD1" -> "504 CQ 61")'}]
    ts0 = b'\x00' * 7 + bytes([1])
    assert erp.calls[:8] == [
        ('SQL_W_ORDER_CAR', (I1,)),
        ('SQL_W_UPDATE_ORDER', (3199, '333DO33', I1, 2, 1, '')),
        ('SQL_W_CHILDREN', (I1,)),
        ('SQL_W_BODY', (I1,)),
        ('SQL_W_UPDATE_BODY', (erp.docs[I1]['body'], I1, ts0)),
        ('SQL_W_LOG', (I1, 2, 'Կայք՝ boss', 'ARAQICH-SITE', erp.log[0]['desc'])),
        ('SQL_W_VERIFY', (I1,)),
        ('SQL_W_ORDER_CAR', (I2,))]
    assert erp.commits == 2 and erp.tx is None
    assert res[0].old_car_null is True and res[1].old_car_null is False


def test_apply_courier_only_does_not_touch_car_column_or_line():
    erp = _erp()
    ds = _decisions(erp, trips=(('MANUAL', [101, 102]),))
    res = ew.apply(erp, ds, 'boss')
    assert all(r.ok and not r.reason for r in res)
    assert erp.orders[I1]['car'] is None and erp.orders[I2]['car'] == 'OLD1 '   # NULL и сырое значение — как были
    assert erp.docs[I2]['body'] == body(van=3199, car='OLD1')
    assert [c for c in erp.calls if c[0].startswith('SQL_W_UPDATE_ORDER')] == [
        ('SQL_W_UPDATE_ORDER_VAN', (3199, I1, 2, 1, '')), ('SQL_W_UPDATE_ORDER_VAN', (3199, I2, 1, 2, 'OLD1'))]
    assert erp.log[1]['desc'] == '("Առաքիչ", "A002" -> "B001/1")'


def test_apply_optimistic_conflict_rolls_back():
    erp = _erp()
    ds = _decisions(erp)
    erp.orders[I1]['van'] = 3300            # в ERP тем временем поменяли առաքիչ
    erp.orders[I2]['car'] = 'NEW'           # и машину
    before = erp._state()
    res = ew.apply(erp, ds, 'boss')
    assert [(r.ok, r.reason) for r in res] == [(False, 'changed_in_erp'), (False, 'changed_in_erp')]
    assert erp._state() == before and erp.commits == 0 and erp.rollbacks == 2
    assert not any(c[0] in ('SQL_W_UPDATE_BODY', 'SQL_W_LOG') for c in erp.calls)


def test_apply_child_doc_before_and_after_update():
    erp = _erp()
    ds = _decisions(erp)
    erp.children[I1] = 1                                                  # накладная уже есть
    # у I2 накладная появляется сразу после UPDATE ORDERS (другой пользователь) — повторная проверка
    erp.hooks['SQL_W_CHILDREN'] = lambda e, p: e.children.__setitem__(I2, 1) if p == (I2,) else None
    before = erp._state()
    res = ew.apply(erp, ds, 'boss')
    assert [(r.ok, r.reason) for r in res] == [(False, 'has_child_docs'), (False, 'has_child_docs')]
    o, d, _, log = erp._state()
    assert (o, d, log) == (before[0], before[1], before[3]) and erp.commits == 0   # ORDERS I2 откатан


def test_apply_exception_midway_rolls_back_and_continues():
    erp = _erp()
    ds = _decisions(erp)

    def boom(e, p):
        if p[0] == I1:
            raise pyodbc.Error('42000', 'INSERT DOCUMENTSLOG failed')
    erp.hooks['SQL_W_LOG'] = boom
    res = ew.apply(erp, ds, 'boss')
    assert [(r.decision.isn, r.ok, r.reason) for r in res] == [(I1, False, 'error'), (I2, True, '')]
    assert erp.orders[I1] == {'state': 2, 'van': 1, 'car': None} and erp.docs[I1]['body'] == body(van=1)   # откат
    assert erp.orders[I2]['van'] == 3200 and [x['isn'] for x in erp.log] == [I2] and erp.commits == 1


def test_apply_body_mismatch_format_and_concurrent_body_change():
    erp = _erp(bodies={I1: body(van=999), I2: '\r\nNO:VAN\r\n'})
    res = ew.apply(erp, _decisions(erp), 'boss')
    assert [(r.ok, r.reason) for r in res] == [(False, 'body_mismatch'), (False, 'body_format')]
    assert erp.orders[I1]['van'] == 1 and erp.orders[I2]['van'] == 2 and erp.commits == 0
    erp = _erp()
    erp.hooks['SQL_W_UPDATE_BODY'] = lambda e, p: e.docs[p[1]].__setitem__('ts', b'other')   # fBODY изменили
    res = ew.apply(erp, _decisions(erp), 'boss')
    assert [(r.ok, r.reason) for r in res] == [(False, 'changed_in_erp')] * 2 and erp.orders[I1]['van'] == 1
    erp = _erp()
    erp.docs.pop(I1)                                                      # нет DOCUMENTS — не пишем
    assert ew.apply(erp, _decisions(erp)[:1], 'boss')[0].reason == 'body_format' and erp.orders[I1]['van'] == 1


def test_apply_verify_mismatch_and_skips_untouched():
    erp = _erp()
    ds = _decisions(erp)
    erp.verify_override = [(1, '', body(van=1))]
    res = ew.apply(erp, ds, 'boss')
    assert [(r.ok, r.reason) for r in res] == [(True, ew.VERIFY_FAILED)] * 2 and erp.commits == 2
    erp = _erp()
    skip = [ew.Decision(I1, 'N1', 101, '', 2, 1, 1, '', '', False, 'not_in_plan')]
    assert ew.apply(erp, skip, 'boss') == [] and erp.calls == []
    bad = [ew.Decision(I1, 'N1', 101, 'T', 2, 1, 0, '', '', True, '')]          # առաքիչ 0 — не пишем
    assert ew.apply(erp, bad, 'boss')[0].reason == 'error' and erp.orders[I1]['van'] == 1


@pytest.mark.parametrize('committed', [True, False])
def test_apply_commit_lost_is_decided_by_read_back(committed):
    """Обрыв на COMMIT: зафиксировано ли — решает чтение назад, не догадка."""
    erp = _erp()

    def commit():
        if committed:
            erp.tx = None                 # сервер успел зафиксировать, ответ потерян
        raise pyodbc.Error('08S01', 'Communication link failure')
    erp.commit = commit
    r = ew.apply(erp, _decisions(erp)[:1], 'boss')[0]
    assert (r.ok, r.reason) == ((True, '') if committed else (False, 'error'))
    assert erp.orders[I1]['van'] == (3199 if committed else 1)


def test_apply_back_to_empty_car_keeps_null_or_blank():
    for null, want in ((True, None), (False, '')):
        erp = _erp(orders={I1: {'state': 2, 'van': 3199, 'car': '333DO33'}}, bodies={I1: body(van=3199, car='333DO33')})
        d = ew.Decision(I1, 'N1', 101, '333DO33', 2, 3199, 1, '333DO33', '', True, '', 'B001/1', 'A001', '', null)
        r = ew.apply(erp, [d], 'boss')[0]
        assert (r.ok, r.reason) == (True, '') and erp.orders[I1]['car'] is want and erp.docs[I1]['body'] == body(van=1)
        assert erp.log[0]['desc'] == '("Առաքիչ", "B001/1" -> "A001"), ("Առաքող ավտոմեքենա", "333DO33" -> "")'


# ============================== состав SQL записи ==============================

def test_write_sql_is_static_and_minimal():
    """Пишется ТОЛЬКО: ORDERS.fVANAGENTID, ORDERS.fDELIVERYCAR; DOCUMENTS.fBODY, fLASTMODIFYDATE, fLASTMODIFIERID;
    INSERT DOCUMENTSLOG. Весь SQL модуля — в константах SQL_W_*; execute — только с ними; ни ';', ни DELETE/EXEC/DDL."""
    sql = {n: getattr(ew, n) for n in dir(ew) if n.startswith('SQL_W_')}
    writes = {n for n, v in sql.items() if _SQL_WRITE_RE.search(v)}
    assert writes == {'SQL_W_UPDATE_ORDER', 'SQL_W_UPDATE_ORDER_VAN', 'SQL_W_UPDATE_BODY', 'SQL_W_LOG'}
    sets = {n: re.search(r'UPDATE\s+(\w+)\s+SET\s+(.*?)\s+WHERE', sql[n], re.S) for n in writes - {'SQL_W_LOG'}}
    cols = {n: (m.group(1), {c.split('=')[0].strip() for c in m.group(2).split(',')}) for n, m in sets.items()}
    assert cols == {'SQL_W_UPDATE_ORDER': ('ORDERS', {'fVANAGENTID', 'fDELIVERYCAR'}),
                    'SQL_W_UPDATE_ORDER_VAN': ('ORDERS', {'fVANAGENTID'}),
                    'SQL_W_UPDATE_BODY': ('DOCUMENTS', {'fBODY', 'fLASTMODIFYDATE', 'fLASTMODIFIERID'})}
    assert re.match(r'INSERT INTO DOCUMENTSLOG \(fISN, fDATE, fUSERID, fSTATE, fOP, fCOMMENT, fCOMPNAME, '
                    r'fCHANGEDESCRIPTION, fSOURCE\)\s+VALUES \(\?, GETDATE\(\), 0, \?, 2, \?, \?, \?, 0\)$', sql['SQL_W_LOG'])
    for n, v in sql.items():
        assert ';' not in v and not re.search(r'\b(DELETE|DROP|ALTER|CREATE|EXEC|EXECUTE|MERGE|TRUNCATE|GRANT)\b', v), n
        assert '{' not in v and '%' not in v, n                               # не шаблон — параметры только `?`
    for n in ('SQL_W_UPDATE_ORDER', 'SQL_W_UPDATE_ORDER_VAN'):                # оптимистично и без дочерних документов
        assert 'ISNULL(fVANAGENTID, 0) = ?' in sql[n] and "LTRIM(RTRIM(ISNULL(fDELIVERYCAR, ''))) = ?" in sql[n]
        assert 'fSTATE = ?' in sql[n] and 'NOT EXISTS (SELECT 1 FROM DOCPARENTS p WHERE p.fPARENTISN = ORDERS.fISN)' in sql[n]
    assert sql['SQL_W_UPDATE_BODY'].rstrip().endswith('WHERE fISN = ? AND fDOCTYPE = 1 AND fTS = ?')
    tree = ast.parse((ROOT / 'route_optimizer' / 'erp_write.py').read_text(encoding='utf-8'))
    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
             and c.func.attr in ('execute', 'executemany')]
    assert calls and all(c.func.attr == 'execute' and isinstance(c.args[0], ast.Name)
                         and c.args[0].id.startswith('SQL_W_') for c in calls)
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert sorted(s for s in strings if _SQL_WRITE_RE.search(s)) == sorted(sql[n] for n in writes)


def test_preview_read_sql_passes_read_only_guard():
    from route_optimizer import erp
    sql = erp.SQL_ORDER_STATES.format(ph='?, ?')
    check_sql(sql)
    assert sql.count('WITH (NOLOCK)') == 2 and 'DOCPARENTS' in sql


def test_load_order_states_reads_through_guard(monkeypatch):
    from route_optimizer import erp
    asked = []

    class Cur:
        def execute(self, sql, params=None):
            asked.append((sql, params))
            erp.check_sql(sql)
            self.rows = ([(I1.lower(), 'N1 ', 101, 2, 3199, '333DO33', 0), (I2, 'N2', 102, 1, None, '', 2)]
                         if 'FROM ORDERS' in sql else
                         [(1, 'A001', 'Менеджер 1', 0, 'Ереван')] if 'SALESAGENTS' in sql else [('CAR1', 'HOWO', 1, 2.5)])

        def fetchall(self):
            return self.rows

        def close(self):
            pass

    class Conn:
        closed = False

        def cursor(self):
            return Cur()

        def close(self):
            Conn.closed = True
    monkeypatch.setattr(erp, 'connect', lambda cs: Conn())
    states, agents, cars = erp.load_order_states('DRIVER={none};', [I2, I1, I1.lower()])
    assert states == {I1: OrderState(I1, 'N1', 101, 2, 3199, '333DO33', False),
                      I2: OrderState(I2, 'N2', 102, 1, 0, '', True)}
    assert asked[0][1] == [I1, I2] and agents[1].code == 'A001' and cars['CAR1'].closed and Conn.closed
    assert erp.order_states(Conn(), []) == {}


def test_connect_write_hides_driver_text(monkeypatch):
    def fail(*a, **k):
        raise pyodbc.Error('08001', 'Login failed for user araqich_rw; PWD=secret')
    monkeypatch.setattr(ew.pyodbc, 'connect', fail)
    with pytest.raises(ew.ErpWriteError) as e:
        ew.connect_write('DRIVER={x};UID=araqich_rw;PWD=secret')
    assert 'secret' not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__
    got = {}

    class C:
        timeout = 0
    monkeypatch.setattr(ew.pyodbc, 'connect', lambda dsn, **k: got.update(k, dsn=dsn) or C())
    assert ew.connect_write('DSN=x').timeout == ew.QUERY_TIMEOUT_S and got['autocommit'] is False
    monkeypatch.delenv(ew.DSN_ENV, raising=False)
    assert ew.write_dsn() is None
    monkeypatch.setenv(ew.DSN_ENV, '  ')
    assert ew.write_dsn() is None
    monkeypatch.setenv(ew.DSN_ENV, ' DSN=x ')
    assert ew.write_dsn() == 'DSN=x'


# ============================== API ==============================

CAR_DRIVERS = {'CAR1': 'Արամ Պետրոսյան', 'CAR2': 'Գագիկ Սարգսյան'}
API_CARS = {'CAR1': Car('CAR1', 'HOWO', False), 'CAR2': Car('CAR2', 'FORD', True)}   # CAR2 закрыта — только առաքիչ


@pytest.fixture
def day(client, monkeypatch):
    """План 01.10 (сегодня) на CAR1 и CAR2; ERP — подделка FakeErp (её же читает предпросмотр); роль — из g.user_role (как
    ставит app_v2), по умолчанию admin. Хук роли — до первого запроса (Flask не принимает его позже)."""
    role = {'value': 'admin'}

    @client.application.before_request
    def _role():
        from flask import g
        if role['value']:
            g.user_role = role['value']
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    for car, name in CAR_DRIVERS.items():
        state.store.save_truck_driver(car, DAY, name, 'qa')
    isns = [_isn(1), _isn(2), _isn(3)]
    cids = {_isn(1): 101, _isn(2): 102, _isn(3): 104}
    erp = FakeErp({i: {'state': 2, 'van': 1, 'car': None} for i in isns}, {i: body(van=1) for i in isns})
    reads = []

    def states(asked):
        reads.append(sorted(asked))
        out = {i: OrderState(i, 'N' + i[-1], cids[i], o['state'], o['van'] or 0, (o['car'] or '').strip(),
                             bool(erp.children.get(i))) for i, o in erp.orders.items() if i in asked}
        return out, AGENTS, API_CARS
    conns = []
    state.erp_states_loader = states
    state.erp_write_conn = lambda: conns.append(1) or erp
    client.erp, client.role, client.reads, client.conns, client.state = erp, role, reads, conns, state
    return client


def _sent(client):
    d = _build(client, ('CAR1', 'CAR2'))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _preview(client):
    r = client.get('/api/routes/dispatch/erp-write?date=' + DAY)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_api_disabled_without_dsn(client, monkeypatch):
    @client.application.before_request
    def _role():
        from flask import g
        g.user_role = 'admin'
    _page_setup(client, monkeypatch, now=NOW)
    state = client.application.extensions['route_optimizer']
    assert state.erp_write_conn is None and state.erp_states_loader is None   # тестовая среда без ROUTES_ERP_WRITE_DSN
    d = _sent(client)
    assert 'erp_write' not in d
    assert client.get('/api/routes/dispatch/erp-write?date=' + DAY).status_code == 404
    assert client.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': 'x'}).status_code == 404
    assert client.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [_isn(1)]}).status_code == 404


def test_init_app_enables_write_only_with_dsn(tmp_path, monkeypatch):
    from flask import Flask
    import route_optimizer

    class FakeDb:
        connection_string = 'DRIVER={none};'
    monkeypatch.setenv(ew.DSN_ENV, 'DRIVER={x};PWD=secret')
    app = Flask(__name__)
    route_optimizer.init_app(app, FakeDb(), db_path=str(tmp_path / 'r.db'))
    state = app.extensions['route_optimizer']
    assert state.erp_write_conn is not None and state.erp_states_loader is not None
    got = []
    monkeypatch.setattr(ew.pyodbc, 'connect', lambda dsn, **k: got.append(dsn) or type('C', (), {'timeout': 0})())
    state.erp_write_conn()
    assert got == ['DRIVER={x};PWD=secret']


def test_api_admin_only(day):
    _sent(day)
    for role in ('user', 'garage', 'warehouse', None):
        day.role['value'] = role
        assert day.get('/api/routes/dispatch/erp-write?date=' + DAY).status_code == 403
        assert day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': 'x'}).status_code == 403
        assert day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [_isn(1)]}).status_code == 403
    assert day.erp.calls == [] and day.conns == []


def test_api_refuses_unsent_plan_and_past_day(day, monkeypatch):
    d = _build(day, ('CAR1', 'CAR2'))
    assert 'sent' not in d and d['erp_write'] == {'trucks': {}}
    r = day.get('/api/routes/dispatch/erp-write?date=' + DAY)
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_NOT_SENT
    r = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': 'x'})
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_NOT_SENT
    r = day.get('/api/routes/dispatch/erp-write?date=2026-09-30')
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_PAST
    _sent(day)
    sig = _preview(day)['signature']
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 2, 9, 0, tzinfo=ac.YEREVAN))
    r = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': sig})
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_PAST
    assert day.post('/api/routes/dispatch/erp-write', json={'date': 'x', 'expected': sig}).status_code == 400
    assert day.erp.calls == [] and day.conns == []


def test_api_preview_write_and_idempotent_repeat(day):
    d = _sent(day)
    owner = _plan_stops(d)
    p = _preview(day)
    assert day.erp.calls == [] and day.conns == []                    # предпросмотр ERP не пишет
    assert sorted(day.reads[-1]) == sorted([_isn(1), _isn(2), _isn(3)])
    rows = {o['isn']: (t['truck'], t['driver'], o) for t in p['trucks'] for o in t['orders']}
    cid_of = {_isn(1): 101, _isn(2): 102, _isn(3): 104}
    for isn, (truck, driver, o) in rows.items():
        assert truck == owner[cid_of[isn]] and driver == CAR_DRIVERS[truck] and o['write'] is True
        assert o['old_van'] == {'id': 1, 'code': 'A001', 'name': 'Менеджер 1'}
        if truck == 'CAR1':
            assert (o['new_van']['code'], o['new_car'], o['reason']) == ('B001/1', 'CAR1', '')
        else:                                                         # CAR2 закрыта в ERP — только առաքիչ
            assert (o['new_van']['code'], o['new_car'], o['reason'], o['reason_text']) == \
                ('B002/1', '', 'car_not_in_erp', ew.REASON_HY['car_not_in_erp'])
    assert p['counts'] == {'write': 3, 'skip': 0} and len(p['signature']) == 32
    bad = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': 'other'})
    assert bad.status_code == 409 and bad.get_json()['error'] == views.ERP_WRITE_STALE and day.conns == []
    r = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': p['signature']})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out['counts'] == {'ok': 3, 'failed': 0} and day.conns == [1] and day.erp.closed
    assert all(day.erp.orders[i]['van'] == (3199 if owner[cid_of[i]] == 'CAR1' else 3200) for i in cid_of)
    assert all(x['comment'] == 'Կայք՝ ' and x['compname'] == 'ARAQICH-SITE' for x in day.erp.log)
    log = day.state.store.erp_write_log(DAY)
    assert [(x['op'], x['result'], x['order_isn']) for x in log] == [('write', 'ok', x['isn']) for x in out['results']]
    page = day.get('/api/routes/dispatch?date=' + DAY).get_json()
    trucks = {owner[cid_of[i]] for i in cid_of}
    assert set(page['erp_write']['trucks']) == trucks
    assert sum(t['written'] for t in page['erp_write']['trucks'].values()) == 3
    # повтор: ничего не меняется — нечего писать
    p2 = _preview(day)
    assert p2['counts'] == {'write': 0, 'skip': 3} and {o['reason'] for t in p2['trucks'] for o in t['orders']} == {'unchanged'}
    calls = len(day.erp.calls)
    r = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': p2['signature']})
    assert r.get_json()['counts'] == {'ok': 0, 'failed': 0} and len(day.erp.calls) == calls


def test_api_signature_mismatch_when_erp_changed_after_preview(day):
    _sent(day)
    p = _preview(day)
    day.erp.children[_isn(1)] = 1                                      # офис выписал накладную
    r = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': p['signature']})
    assert r.status_code == 409 and r.get_json()['conflict'] is True and day.erp.calls == []
    p = _preview(day)
    row = next(o for t in p['trucks'] for o in t['orders'] if o['isn'] == _isn(1))
    assert (row['write'], row['reason']) == (False, 'has_child_docs')


def test_api_write_busy_is_conflict(day):
    _sent(day)
    p = _preview(day)
    assert day.state.erp_write_lock.acquire(blocking=False)
    try:
        r = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': p['signature']})
        assert r.status_code == 409 and r.get_json()['error'] == views.ERP_WRITE_BUSY
    finally:
        day.state.erp_write_lock.release()
    assert day.erp.calls == []


def test_api_undo_reverts_our_write_once(day):
    _sent(day)
    p = _preview(day)
    out = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': p['signature']}).get_json()
    done = [x['isn'] for x in out['results'] if x['ok']]
    # третий заказ тем временем изменили в ERP — его не трогаем
    day.erp.orders[_isn(3)]['van'] = 3300
    r = day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': done})
    assert r.status_code == 200, r.get_json()
    got = {x['isn']: (x['ok'], x['reason']) for x in r.get_json()['results']}
    assert got == {_isn(1): (True, ''), _isn(2): (True, ''), _isn(3): (False, 'changed_in_erp')}
    assert day.erp.orders[_isn(1)] == {'state': 2, 'van': 1, 'car': None} and day.erp.docs[_isn(1)]['body'] == body(van=1)
    assert day.erp.orders[_isn(2)] == {'state': 2, 'van': 1, 'car': None}
    assert day.erp.log[-1]['desc'].startswith('("Առաքիչ", "B00')
    log = day.state.store.erp_write_log(DAY)
    assert [x['op'] for x in log].count('undo') == 2
    # второй раз — нечего возвращать (последняя запись — возврат); чужой заказ — тоже
    r = day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [_isn(1), _isn(9)]}).get_json()
    assert {x['isn']: x['reason'] for x in r['results']} == {_isn(1): 'not_written', _isn(9): 'not_written'}
    for bad in ([], ['x'], 'x', [_isn(1)] * 0, [1]):
        assert day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': bad}).status_code == 400
    page = day.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert sum(t['undone'] for t in page['erp_write']['trucks'].values()) == 2


def test_api_unknown_driver_is_skipped(day):
    day.state.store.save_truck_driver('CAR1', DAY, 'Չկա Այդպիսի', 'qa', only_day=True)
    d = _sent(day)
    owner = _plan_stops(d)
    p = _preview(day)
    for t in p['trucks']:
        for o in t['orders']:
            if t['truck'] == 'CAR1':
                assert (o['write'], o['reason']) == (False, 'courier_not_found')
    assert p['counts']['write'] == sum(1 for c in (101, 102, 104) if owner[c] == 'CAR2')


# ============================== схема 29 ==============================

def test_schema_29_migration_adds_log_and_roundtrip(tmp_path):
    path = str(tmp_path / 'v28.db')
    s = st.Store(path)
    s.save_truck_driver('CAR1', '2026-10-01', 'Արամ', 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE erp_write_log')
        conn.execute("UPDATE meta SET value = '28' WHERE key = 'schema_version'")
        conn.commit()
    s2 = st.Store(path)
    assert s2.erp_write_log('2026-10-01') == []
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('29',)
    assert st.SCHEMA_VERSION == 29 and st._MIGRATIONS[28] == (st._ERP_WRITE_LOG_TABLE, st._ERP_WRITE_LOG_INDEX)
    assert s2.truck_drivers('2026-10-01')[0] == {'CAR1': 'Արամ'}
    row = {'order_isn': I1, 'doc_num': 'N1', 'customer_id': 101, 'truck': 'CAR1', 'old_van': 1, 'new_van': 3199,
           'old_car': '', 'new_car': 'CAR1', 'old_car_null': True, 'result': 'ok', 'reason': ''}
    assert s2.add_erp_write('2026-10-01', 'write', row, 'boss') == 1
    s2.add_erp_write('2026-10-01', 'undo', {**row, 'old_car_null': False, 'result': 'failed', 'reason': 'error'}, None)
    got = s2.erp_write_log('2026-10-01')
    assert [(x['id'], x['op'], x['old_car_null'], x['result'], x['by_user']) for x in got] == \
        [(1, 'write', True, 'ok', 'boss'), (2, 'undo', False, 'failed', None)]
    assert set(got[0]) == set(st.ERP_WRITE_LOG_FIELDS) and s2.erp_write_log('2026-10-02') == []
    for bad in ({**row, 'result': 'maybe'}, {**row, 'old_van': '1'}, {k: v for k, v in row.items() if k != 'truck'}):
        with pytest.raises(ValueError):
            s2.add_erp_write('2026-10-01', 'write', bad, 'boss')
    with pytest.raises(ValueError):
        s2.add_erp_write('2026-10-01', 'delete', row, 'boss')
    with pytest.raises(ValueError):
        s2.add_erp_write('01.10.2026', 'write', row, 'boss')


def test_page_assets_bumped_and_texts_safe():
    page = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    assert "routes_dispatch.js') }}?v=117" in page and "routes_dispatch.css') }}?v=74" in page
    assert 'id="dpErpDlg"' in page and 'Գրել ERP-ում' in page
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    start, end = js.index('// ---------- «Գրել ERP-ում»'), js.index('// ---------- Требует внимания (№81)')
    part = js[start:end]
    # строки ERP — только textContent: innerHTML — лишь у кнопки с постоянной разметкой
    assert part.count('innerHTML') == 1 and "b.innerHTML = '<i class=\"fas fa-file-export\"" in part
    assert '/api/routes/dispatch/erp-write' in part and 'expected: erp.sig' in part

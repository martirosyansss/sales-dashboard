# -*- coding: utf-8 -*-
"""«Գրել ERP-ում» (ответ владельца №97, docs/plans/erp-write-plan.md): сайт пишет в заказы дня առաքիչ и машину по
отправленному водителям плану «Развоза». Единственная запись в боевую ERP — route_optimizer.erp_write. Здесь: правка fBODY
(rewrite_body), решения по заказам (plan_assignments), запись подделкой транзакционного соединения (apply: успех,
оптимистичный конфликт, дочерний документ, чужая правка fBODY, сбой посреди записи, обрыв на COMMIT, сбой журнала), состав
SQL записи, API (доступ, прошедший день, неотправленный план, заказ после отправки, отпечаток, проба одним заказом,
выключено без DSN, замок записи), «Վերադարձնել» и миграция схемы 29. Ни ERP, ни CT115 не трогаются.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_erp_write.py -q
"""
import ast
import copy
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
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
from test_route_optimizer import _SQL_WRITE_RE, _dorder, _isn  # noqa: E402

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN)
I1, I2, I3, I4 = _isn(1), _isn(2), _isn(3), _isn(4)
AGENTS = {1: Agent(1, 'A001', 'Менеджер 1', False), 2: Agent(2, 'A002', 'Менеджер 2', False),
          3199: Agent(3199, 'B001/1', 'Արամ  Պետրոսյան', False),          # в ERP двойной пробел
          3200: Agent(3200, 'B002/1', 'Գագիկ Սարգսյան', False),
          3201: Agent(3201, 'B003/1', 'Գագիկ Սարգսյան', True),            # закрытый тёзка — не в счёт
          3300: Agent(3300, 'B004/1', 'Վարդան Մկրտչյան', False),
          3301: Agent(3301, 'B005/1', 'վարդան մկրտչյան', False),           # открытые тёзки
          8: Agent(8, 'A008/6', 'Սամվել Սահակյան', False),                 # менеджер (A…) — не առաքիչ
          3400: Agent(3400, 'B006/1', 'Կարեն Ավագյան', False)}
CARS = {'333DO33': Car('333DO33', 'HOWO', False), '504 CQ 61': Car('504 CQ 61', 'FORD', False),
        'OLD1': Car('OLD1', 'старая', True), 'TOO-LONG-CODE': Car('TOO-LONG-CODE', 'длинный', False)}


def body(van=1, car=None, head='\r\nDOCNUM:N1\r\nDATE:20261001', tail='\r\nCOMMENT:տուն 5\r\n'):
    """fBODY заказа в виде ERP: строки \\r\\nKEY:VALUE\\r\\n."""
    return head + f'\r\nVANAGENTID:{van}' + (f'\r\nDELIVERYCAR:{car}' if car else '') + tail


def _rest(text):
    return [x for x in text.split('\r\n') if not x.startswith('VANAGENTID:') and not x.startswith('DELIVERYCAR:')]


def rw(text, van, car, old_van=1, old_car=''):
    return ew.rewrite_body(text, van, car, old_van=old_van, old_car=old_car)


# ============================== fBODY ==============================

def test_rewrite_body_replaces_van_and_inserts_car_after_it():
    old = body(van=1)
    new = rw(old, 3199, '333DO33')
    assert new == '\r\nDOCNUM:N1\r\nDATE:20261001\r\nVANAGENTID:3199\r\nDELIVERYCAR:333DO33\r\nCOMMENT:տուն 5\r\n'
    assert _rest(new) == _rest(old) and ew.body_values(new) == ('3199', '333DO33')


def test_rewrite_body_replaces_car_with_spaces_and_removes_empty_car():
    old = body(van=1, car='OLD 1', tail='\r\nX:1\r\nY: два слова \r\n')
    new = rw(old, 7, '504 CQ 61', old_car='OLD 1')
    assert new == old.replace('VANAGENTID:1', 'VANAGENTID:7').replace('DELIVERYCAR:OLD 1', 'DELIVERYCAR:504 CQ 61')
    assert ew.body_values(new) == ('7', '504 CQ 61')
    gone = rw(old, 7, '', old_car='OLD 1')
    assert 'DELIVERYCAR' not in gone and gone == old.replace('VANAGENTID:1', 'VANAGENTID:7').replace('\r\nDELIVERYCAR:OLD 1', '')
    assert ew.body_values(gone) == ('7', None)
    # None — машина не меняется: строка как была (и отсутствующая не появляется)
    assert rw(old, 7, None, old_car='OLD 1') == old.replace('VANAGENTID:1', 'VANAGENTID:7')
    assert rw(body(van=1), 7, None) == body(van=7)
    assert rw(body(van=1), 7, '') == body(van=7)        # пустую и так нет


def test_rewrite_body_keeps_every_other_byte():
    """Всё, кроме двух строк, — до байта: пустые строки, \\n внутри значения, кириллица и армянский, край текста."""
    head = 'HDR:1\r\n\r\nNOTE:строка\nвторая\r\nA:  пробелы  '
    old = head + '\r\nDELIVERYCAR:OLD1\r\nZ:9\r\nVANAGENTID:5\r\nՀ:ա'
    new = rw(old, 3200, '333DO33', old_van=5, old_car='OLD1')
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
        rw(bad, 3199, '333DO33')


@pytest.mark.parametrize('text, old_van, old_car', [
    (body(van=2), 1, ''),                       # VANAGENTID не тот, что в ORDERS
    (body(van=1, car='OLD1'), 1, ''),           # в fBODY машина есть, в ORDERS — нет
    (body(van=1), 1, 'OLD1'),                   # наоборот
    (body(van=1, car='OLD2'), 1, 'OLD1'),       # другая машина
    (body(van=1, car='OLD1 '), 1, 'OLD1'),      # в fBODY — с пробелом в конце (ровно та строка, не похожая)
    (body(van=' 1'), 1, ''),                    # «VANAGENTID: 1» — не ровно
    (body(van='01'), 1, ''),
])
def test_rewrite_body_verifies_original_lines_exactly(text, old_van, old_car):
    with pytest.raises(ew.BodyMismatchError):
        rw(text, 3199, '333DO33', old_van=old_van, old_car=old_car)


@pytest.mark.parametrize('van, car', [(0, 'A'), (-1, 'A'), (True, 'A'), ('3199', 'A'), (3199, 'A\r\nX:1'),
                                      (3199, ' A'), (3199, 'A\n'), (3199, 'A\r'), (3199, 'X' * 13), (3199, 5)])
def test_rewrite_body_refuses_bad_values(van, car):
    with pytest.raises(ew.BodyFormatError):
        rw(body(), van, car)


def test_rewrite_body_refuses_bad_old_values():
    with pytest.raises(ew.BodyFormatError):
        rw(body(van=1, car='A'), 3199, 'B', old_car='A\r\nX:1')
    with pytest.raises(ew.BodyFormatError):
        rw(body(van=1), 3199, 'B', old_van=-1)


# ============================== решения ==============================

def _o(isn, cid, *, van=1, car='', state=2, children=False, text=None):
    return OrderState(isn, 'N' + isn[-1], cid, state, van, car, children, body(van, car) if text is None else text)


DRIVERS = {'333DO33': 'Արամ Պետրոսյան', '504 CQ 61': 'գագիկ  սարգսյան', 'MANUAL': 'Արամ Պետրոսյան',
           'TWINS': 'Վարդան Մկրտչյան', 'NOBODY': None, 'GHOST': 'Չկա Այդպիսի', 'OLD1': 'Արամ Պետրոսյան',
           'MANAGER': 'Սամվել Սահակյան', 'TOO-LONG-CODE': 'Արամ Պետրոսյան'}


def _plan(trips, orders, drivers=DRIVERS, seen=None):
    seen = {o.isn for o in orders} if seen is None else seen
    return {d.isn: d for d in ew.plan_assignments(trips, orders, drivers, AGENTS, CARS, seen)}


def test_plan_writes_courier_and_car_of_the_truck():
    got = _plan([('333DO33', [101, 102]), ('504 CQ 61', [103])],
                [_o(I1, 101), _o(I2, 102, van=3199, car='333DO33'), _o(I3, 103, car='OLD1')])
    a, b, c = got[I1], got[I2], got[I3]
    assert (a.write, a.reason, a.old_van, a.new_van, a.old_car, a.new_car, a.truck) == \
        (True, '', 1, 3199, '', '333DO33', '333DO33')
    assert (a.old_code, a.new_code, a.new_name) == ('A001', 'B001/1', 'Արամ Պետրոսյան')   # имя без двойного пробела
    assert a.body_sha == ew.body_sha(body(1)) and len(a.body_sha) == 64
    assert (b.write, b.reason) == (False, 'unchanged')                                     # повтор ничего не пишет
    assert (c.write, c.new_van, c.new_car, c.new_code) == (True, 3200, '504 CQ 61', 'B002/1')   # закрытый тёзка — мимо


def test_plan_car_not_in_erp_closed_or_too_long_writes_courier_only():
    got = _plan([('MANUAL', [101]), ('OLD1', [102]), ('TOO-LONG-CODE', [103])],
                [_o(I1, 101, car='X 1'), _o(I2, 102), _o(I3, 103)])
    for isn, car in ((I1, 'X 1'), (I2, ''), (I3, '')):
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
             ('333DO33', [110, 111, 112]), ('MANAGER', [113])]
    orders = [_o(_isn(0x10), 100), _o(_isn(0x11), 105), _o(_isn(0x12), 106, state=1, children=True), _o(_isn(0x13), 107),
              _o(_isn(0x14), 108), _o(_isn(0x15), 109), _o(_isn(0x16), 110, state=0), _o(_isn(0x17), 110, state=3),
              _o(_isn(0x18), 101, state=1), _o(_isn(0x19), 101), _o(_isn(0x1A), 111, text=body(van=9)),
              _o(_isn(0x1B), 112, text='\r\nNO:VAN\r\n'), _o(_isn(0x1C), 113), _o(_isn(0x1D), 112, text=None)]
    orders[-1] = replace(orders[-1], body=None)                                  # документа нет
    seen = {o.isn for o in orders} - {_isn(0x19)}                                # 19 заведён после отправки
    got = _plan(trips, orders, seen=seen)
    assert {k[-2:]: (v.write, v.reason) for k, v in got.items()} == {
        '10': (False, 'not_in_plan'), '11': (False, 'split_store'), '12': (False, 'has_child_docs'),
        '13': (False, 'courier_ambiguous'), '14': (False, 'courier_not_found'), '15': (False, 'courier_not_found'),
        '16': (False, 'order_state'), '17': (False, 'order_state'), '18': (True, ''), '19': (False, 'not_in_sent_plan'),
        '1A': (False, 'body_mismatch'), '1B': (False, 'body_format'), '1C': (False, 'courier_not_found'),
        '1D': (False, 'body_format')}
    assert got[_isn(0x11)].truck == '333DO33, 504 CQ 61' and got[_isn(0x10)].truck == ''
    # пропуск ничего не меняет: стало = было
    assert all((d.new_van, d.new_car) == (d.old_van, d.old_car) for d in got.values() if not d.write)
    assert set(ew.SKIP_REASONS) == {d.reason for d in got.values() if not d.write} | {'unchanged'}
    assert all(r in ew.REASON_HY for r in (*ew.SKIP_REASONS, *ew.FAIL_REASONS, ew.CAR_NOT_IN_ERP, ew.VERIFY_FAILED,
                                           ew.COMMIT_UNKNOWN, 'not_written'))
    assert ew.REASON_HY['not_in_sent_plan'] == 'Ուղարկելուց հետո մուտքագրված պատվեր'
    assert ew.REASON_HY['commit_unknown'] == 'Անհայտ՝ ստուգեք ERP-ում'


def test_plan_same_truck_in_two_trips_is_not_split():
    d = _plan([('333DO33', [101]), ('333DO33', [102, 101])], [_o(I1, 101)])[I1]
    assert (d.write, d.truck) == (True, '333DO33')


def test_courier_of_only_open_b_codes():
    assert ew.courier_of('  արամ   ՊԵՏՐՈՍՅԱՆ ', AGENTS)[0].id == 3199
    assert ew.courier_of('Վարդան Մկրտչյան', AGENTS) == (None, 'courier_ambiguous')
    assert ew.courier_of('', AGENTS) == (None, 'courier_not_found') == ew.courier_of(None, AGENTS)
    closed = {k: Agent(a.id, a.code, a.name, True) for k, a in AGENTS.items()}
    assert ew.courier_of('Արամ Պետրոսյան', closed) == (None, 'courier_not_found')
    # менеджер A… с тем же именем — не առաքիչ; рядом с B… — не тёзка, берём B…
    assert ew.courier_of('Սամվել Սահակյան', AGENTS) == (None, 'courier_not_found')
    both = {**AGENTS, 9: Agent(9, 'A009', 'Արամ Պետրոսյան', False)}
    assert ew.courier_of('Արամ Պետրոսյան', both)[0].id == 3199


def test_signature_changes_with_any_field_and_body():
    ds = ew.plan_assignments([('333DO33', [101])], [_o(I1, 101), _o(I2, 102)], DRIVERS, AGENTS, CARS, {I1, I2})
    sig = ew.signature(ds)
    assert sig == ew.signature(list(reversed(ds))) and len(sig) == 32
    for orders in ([_o(I1, 101, van=2), _o(I2, 102)], [_o(I1, 101), _o(I2, 102, car='X')],
                   [_o(I1, 101, text=body(1) + 'Z:1'), _o(I2, 102)]):
        assert ew.signature(ew.plan_assignments([('333DO33', [101])], orders, DRIVERS, AGENTS, CARS, {I1, I2})) != sig


def test_change_description_like_erp_and_comment():
    d = _plan([('333DO33', [101])], [_o(I1, 101, van=2, car='OLD1')])[I1]
    assert ew.change_description(d) == '("Առաքիչ", "A002" -> "B001/1"), ("Առաքող ավտոմեքենա", "OLD1" -> "333DO33")'
    d = _plan([('MANUAL', [101])], [_o(I1, 101, van=0, text=body(0))])[I1]
    assert ew.change_description(d) == '("Առաքիչ", "" -> "B001/1")'
    d = _plan([('333DO33', [101])], [_o(I1, 101, van=3199)])[I1]
    assert ew.change_description(d) == '("Առաքող ավտոմեքենա", "" -> "333DO33")'
    assert ew.log_comment('boss') == 'Կայք՝ boss' and ew.log_comment(None) == 'Կայք՝ '
    assert len(ew.log_comment('x' * 500)) == ew.COMMENT_MAX == 100 and len(ew.COMPNAME) <= 32


def test_plan_ambiguous_driver_is_skipped():
    """Водитель дня неоднозначен (ведёт две машины, посадка сборки устарела) — courier_ambiguous, не угадываем."""
    orders = [_o(I1, 101), _o(I2, 102)]
    got = {d.isn: d for d in ew.plan_assignments([('333DO33', [101]), ('MANUAL', [102])], orders, DRIVERS, AGENTS, CARS,
                                                   {I1, I2}, ambiguous={'333DO33'})}
    assert (got[I1].write, got[I1].reason) == (False, 'courier_ambiguous') and got[I2].write


def test_body_sha_and_signature_survive_lone_surrogate():
    text = body(1) + 'X:\ud800\r\n'
    assert len(ew.body_sha(text)) == 64
    ds = ew.plan_assignments([('333DO33', [101])], [_o(I1, 101, text=text)], DRIVERS, AGENTS, CARS, {I1})
    assert ds[0].write and len(ew.signature(ds)) == 32


def test_max_orders_env(monkeypatch):
    monkeypatch.delenv(ew.MAX_ORDERS_ENV, raising=False)
    assert ew.max_orders() == 1
    for raw, want in (('5', 5), (' 3 ', 3), ('0', 1), ('-2', 1), ('x', 1), ('', 1)):
        monkeypatch.setenv(ew.MAX_ORDERS_ENV, raw)
        assert ew.max_orders() == want, raw


# ============================== подделка транзакционной ERP ==============================

def _sql_trim(v):
    """LTRIM(RTRIM(ISNULL(x, ''))) SQL Server: только пробелы, не табуляция и не перевод строки."""
    return (v or '').strip(' ')


def _sql_eq(a, b):
    """= SQL Server в столбце без учёта регистра (CI): регистр не важен, хвостовые пробелы не важны."""
    return a.rstrip(' ').casefold() == b.rstrip(' ').casefold()


T0 = datetime(2026, 10, 1, 9, 30, 15, 997000)


class FakeErp:
    """ORDERS / DOCUMENTS / DOCPARENTS / DOCUMENTSLOG в памяти с транзакцией (autocommit выключен: первый оператор с
    таблицей начинает её, rollback — всё назад). Сравнение по ORDERS — как SQL Server с CI-сортировкой (_sql_eq, _sql_trim),
    fBODY — двоичное. Принимает только SQL из erp_write.SQL_W_* (иначе AssertionError). hooks: имя константы → fn(erp,
    params) перед выполнением (может бросить или изменить данные «другим пользователем»); after — после; rowcount —
    подменить rowcount оператора."""

    def __init__(self, orders, bodies, children=None):
        self.orders = {k: dict(v) for k, v in orders.items()}       # isn → {state, van, car (как в столбце)}
        self.docs = {k: {'body': b, 'ts': b'\x00' * 7 + bytes([1]), 'mod': None} for k, b in bodies.items()}
        self.children = dict(children or {})
        self.log = []
        self.calls = []
        self.hooks, self.after, self.rowcount = {}, {}, {}
        self.verify_override = None
        self.now = T0
        self.tx = None
        self.commits = self.rollbacks = 0
        self.closed = False
        self.session = []
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
        e.calls.append((name, params))
        if name in e.hooks:
            e.hooks[name](e, params)
        if e.tx is None and name not in ('SQL_W_TRANCOUNT', 'SQL_W_XACT_ABORT', 'SQL_W_DEADLOCK', 'SQL_W_LOCK_TIMEOUT'):
            e.tx = e._state()
        self.rows, self.rowcount = [], -1
        if name in ('SQL_W_XACT_ABORT', 'SQL_W_DEADLOCK', 'SQL_W_LOCK_TIMEOUT'):
            e.session.append(sql)
        elif name == 'SQL_W_TRANCOUNT':
            self.rows = [(1 if e.tx is not None else 0,)]
        elif name == 'SQL_W_NOW':
            self.rows = [(e.now,)]
            e.now += timedelta(milliseconds=10)                              # часы сервера идут
        elif name == 'SQL_W_ORDER':
            o = e.orders.get(params[0])
            self.rows = [(o['state'], o['van'], o['car'])] if o else []
        elif name in ('SQL_W_UPDATE_ORDER', 'SQL_W_UPDATE_ORDER_VAN'):
            if name == 'SQL_W_UPDATE_ORDER':
                van, car, isn, state, old_van, old_car = params
            else:
                (van, isn, state, old_van, old_car), car = params, None
            assert car is not None or name == 'SQL_W_UPDATE_ORDER_VAN'        # NULL машину не пишем никогда
            o = e.orders.get(isn)
            hit = (o is not None and o['state'] == state and (o['van'] or 0) == old_van
                   and _sql_eq(_sql_trim(o['car']), old_car) and not e.children.get(isn))
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
            new, now, isn, ts, old = params
            d = e.docs.get(isn)
            hit = d is not None and d['ts'] == ts and d['body'] == old       # fBODY — двоичное сравнение
            if hit:
                d['body'], d['mod'] = new, (now, 0)
                d['ts'] = (int.from_bytes(d['ts'], 'big') + 1).to_bytes(8, 'big')
            self.rowcount = int(hit)
        elif name == 'SQL_W_LOG':
            isn, now, state, comment, comp, desc = params
            if any(x['isn'] == isn and x['date'] == now for x in e.log):     # ключ (fISN, fDATE, fOP)
                raise pyodbc.IntegrityError('23000', 'Violation of PRIMARY KEY constraint')
            e.log.append({'isn': isn, 'date': now, 'state': state, 'comment': comment, 'compname': comp, 'desc': desc})
            self.rowcount = 1
        elif name == 'SQL_W_VERIFY':
            o, d = e.orders.get(params[0]), e.docs.get(params[0])
            self.rows = [(o['van'], _sql_trim(o['car']), d['body'])] if o and d else []
            if e.verify_override is not None:
                self.rows = e.verify_override
        if name in e.rowcount:
            self.rowcount = e.rowcount[name]
        if name in e.after:
            e.after[name](e, params, self)
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class FakeJournal:
    """Журнал сайта для apply: pending до COMMIT, finish после; fail — где бросить JournalError; lease — продлевается ли."""

    def __init__(self, erp=None, fail=(), lease=True):
        self.rows, self.fail, self.lease, self.erp, self.renews = [], set(fail), lease, erp, 0
        self.pending_seen_tx = []

    def renew(self):
        self.renews += 1
        return self.lease if not callable(self.lease) else self.lease(self.renews)

    def pending(self, d, old_raw):
        if 'pending' in self.fail:
            raise ew.JournalError('x')
        if self.erp is not None:
            self.pending_seen_tx.append(self.erp.tx is not None)    # до COMMIT: транзакция ERP ещё открыта
        self.rows.append({'isn': d.isn, 'result': 'pending', 'old_raw': old_raw})
        return len(self.rows) - 1

    def finish(self, token, r):
        if 'finish' in self.fail:
            raise ew.JournalError('x')
        result = 'unknown' if r.reason == ew.COMMIT_UNKNOWN else 'ok' if r.ok else 'failed'
        if token is None:
            self.rows.append({'isn': r.decision.isn, 'result': result, 'old_raw': r.old_car_raw})
        else:
            self.rows[token]['result'] = result


def _erp(**over):
    """Два заказа: I1 (առաքիչ — менеджер 1, без машины, fDELIVERYCAR NULL), I2 (առաքիչ 2, машина OLD1)."""
    orders = {I1: {'state': 2, 'van': 1, 'car': None}, I2: {'state': 1, 'van': 2, 'car': 'OLD1'}}
    bodies = {I1: body(van=1), I2: body(van=2, car='OLD1')}
    orders.update(over.get('orders', {}))
    bodies.update(over.get('bodies', {}))
    return FakeErp(orders, bodies, over.get('children'))


def _decisions(erp, trips=(('333DO33', [101]), ('504 CQ 61', [102]))):
    states = [OrderState(i, 'N' + i[-1], 101 if i == I1 else 102, o['state'], o['van'] or 0, (o['car'] or '').strip(),
                         bool(erp.children.get(i)), erp.docs[i]['body'] if i in erp.docs else None)
              for i, o in sorted(erp.orders.items())]
    return ew.plan_assignments(list(trips), states, DRIVERS, AGENTS, CARS, set(erp.orders))


def _apply(erp, ds, **kw):
    return list(ew.apply(erp, ds, 'boss', **kw).results)


def test_apply_success_exact_sql_and_values():
    erp = _erp()
    ds = _decisions(erp)
    assert [d.write for d in ds] == [True, True]
    j = FakeJournal(erp)
    batch = ew.apply(erp, ds, 'boss', journal=j)
    res = list(batch.results)
    assert batch.stopped == '' and [(r.decision.isn, r.ok, r.reason) for r in res] == [(I1, True, ''), (I2, True, '')]
    assert j.rows == [{'isn': I1, 'result': 'ok', 'old_raw': ''}, {'isn': I2, 'result': 'ok', 'old_raw': 'OLD1'}]
    assert j.pending_seen_tx == [True, True] and j.renews == 2      # pending — до COMMIT; замок — перед каждым заказом
    assert erp.orders[I1] == {'state': 2, 'van': 3199, 'car': '333DO33'}
    assert erp.orders[I2] == {'state': 1, 'van': 3200, 'car': '504 CQ 61'}
    new1 = rw(body(van=1), 3199, '333DO33')
    # одно время правки: DOCUMENTS.fLASTMODIFYDATE и DOCUMENTSLOG.fDATE — одинаковые (как у ERP), fLASTMODIFIERID 0
    assert erp.docs[I1]['body'] == new1 and erp.docs[I1]['mod'] == (T0, 0) and erp.log[0]['date'] == T0
    assert _rest(erp.docs[I2]['body']) == _rest(body(van=2, car='OLD1'))
    assert [{k: v for k, v in x.items() if k != 'date'} for x in erp.log] == [
        {'isn': I1, 'state': 2, 'comment': 'Կայք՝ boss', 'compname': 'ARAQICH-SITE',
         'desc': '("Առաքիչ", "A001" -> "B001/1"), ("Առաքող ավտոմեքենա", "" -> "333DO33")'},
        {'isn': I2, 'state': 1, 'comment': 'Կայք՝ boss', 'compname': 'ARAQICH-SITE',
         'desc': '("Առաքիչ", "A002" -> "B002/1"), ("Առաքող ավտոմեքենա", "OLD1" -> "504 CQ 61")'}]
    ts0 = b'\x00' * 7 + bytes([1])
    assert erp.calls[:13] == [
        ('SQL_W_TRANCOUNT', ()),
        ('SQL_W_ORDER', (I1,)),
        ('SQL_W_UPDATE_ORDER', (3199, '333DO33', I1, 2, 1, '')),
        ('SQL_W_CHILDREN', (I1,)),
        ('SQL_W_BODY', (I1,)),
        ('SQL_W_NOW', ()),
        ('SQL_W_UPDATE_BODY', (new1, T0, I1, ts0, body(van=1))),
        ('SQL_W_BODY', (I1,)),
        ('SQL_W_ORDER', (I1,)),
        ('SQL_W_LOG', (I1, T0, 2, 'Կայք՝ boss', 'ARAQICH-SITE', erp.log[0]['desc'])),
        ('SQL_W_VERIFY', (I1,)),
        ('SQL_W_TRANCOUNT', ()),
        ('SQL_W_ORDER', (I2,))]
    assert erp.commits == 2 and erp.tx is None
    assert res[0].old_car_raw == '' and res[1].old_car_raw == 'OLD1'


def test_apply_courier_only_does_not_touch_car_column_or_line():
    erp = _erp(orders={I2: {'state': 1, 'van': 2, 'car': 'OLD1'}})
    ds = _decisions(erp, trips=(('MANUAL', [101, 102]),))
    res = _apply(erp, ds)
    assert all(r.ok and not r.reason for r in res)
    assert erp.orders[I1]['car'] is None and erp.orders[I2]['car'] == 'OLD1'   # NULL и значение — как были
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
    res = _apply(erp, ds)
    assert [(r.ok, r.reason) for r in res] == [(False, 'changed_in_erp'), (False, 'changed_in_erp')]
    assert erp._state() == before and erp.commits == 0 and erp.rollbacks == 2
    assert not any(c[0] in ('SQL_W_UPDATE_BODY', 'SQL_W_LOG') for c in erp.calls)


def test_apply_sql_semantics_case_and_spaces_only_trim():
    """Как SQL Server: LTRIM/RTRIM режут только пробелы (машина с табуляцией — не та, откат), сравнение без регистра (UPDATE
    проходит, но fBODY и чтение назад в транзакции ловят чужую правку — откат)."""
    erp = _erp(orders={I2: {'state': 1, 'van': 2, 'car': '  OLD1  '}})       # пробелы — режутся
    ds = _decisions(erp)
    assert ds[1].old_car == 'OLD1' and [(r.ok, r.reason) for r in _apply(erp, ds)][1] == (True, '')
    erp = _erp(orders={I2: {'state': 1, 'van': 2, 'car': 'OLD1'}})
    ds = _decisions(erp)
    erp.orders[I2]['car'] = 'OLD1\t'                                          # табуляция — не режется
    assert _apply(erp, ds)[1].reason == 'changed_in_erp' and erp.orders[I2]['car'] == 'OLD1\t'
    erp = _erp()
    ds = _decisions(erp)
    erp.orders[I2]['car'] = 'old1'                                            # регистр: UPDATE проходит…
    erp.docs[I2]['body'] = body(van=2, car='old1')                            # …ERP поменяла и fBODY
    r = _apply(erp, ds)[1]
    assert (r.ok, r.reason) == (False, 'changed_in_erp') and erp.orders[I2] == {'state': 1, 'van': 2, 'car': 'old1'}


def test_apply_child_doc_before_and_after_update():
    erp = _erp()
    ds = _decisions(erp)
    erp.children[I1] = 1                                                  # накладная уже есть
    # у I2 накладная появляется сразу после UPDATE ORDERS (другой пользователь) — повторная проверка
    erp.hooks['SQL_W_CHILDREN'] = lambda e, p: e.children.__setitem__(I2, 1) if p == (I2,) else None
    before = erp._state()
    res = _apply(erp, ds)
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
    res = _apply(erp, ds)
    assert [(r.decision.isn, r.ok, r.reason) for r in res] == [(I1, False, 'error'), (I2, True, '')]
    assert erp.orders[I1] == {'state': 2, 'van': 1, 'car': None} and erp.docs[I1]['body'] == body(van=1)   # откат
    assert erp.orders[I2]['van'] == 3200 and [x['isn'] for x in erp.log] == [I2] and erp.commits == 1


def test_apply_log_key_conflict_rolls_back():
    """Ключ DOCUMENTSLOG (fISN, fDATE, fOP) уже занят (та же миллисекунда) — log_conflict, откат."""
    erp = _erp()
    ds = _decisions(erp)
    erp.log.append({'isn': I1, 'date': T0, 'state': 2, 'comment': '', 'compname': 'ERP', 'desc': ''})
    res = _apply(erp, ds)
    assert [(r.ok, r.reason) for r in res] == [(False, 'log_conflict'), (True, '')]
    assert erp.orders[I1]['van'] == 1 and erp.docs[I1]['body'] == body(van=1)


def test_apply_rollback_failure_stops_batch():
    """Откат не удался — транзакция, может быть, открыта: дальше не пишем; заказ 1 не зафиксирован."""
    erp = _erp()
    ds = _decisions(erp)
    erp.orders[I1]['van'] = 3300                                         # заказ 1 откатится (конфликт)…

    def broken():
        raise pyodbc.Error('08S01', 'link failure')
    erp.rollback = broken                                                # …а откат падает
    batch = ew.apply(erp, ds, 'boss')
    assert batch.stopped == 'rollback' and [(r.decision.isn, r.reason) for r in batch.results] == [(I1, 'changed_in_erp')]
    assert erp.commits == 0 and not any(c[1][:1] == (I2,) for c in erp.calls)


def test_apply_open_transaction_after_lock_timeout_stops():
    """Таймаут замка, транзакция осталась открытой (откат «прошёл», но @@TRANCOUNT ≠ 0) — следующий заказ не пишется."""
    erp = _erp()
    ds = _decisions(erp)

    def timeout(e, p):
        if p == (3199, '333DO33', I1, 2, 1, ''):
            raise pyodbc.Error('HYT00', 'Lock request time out period exceeded')
    erp.hooks['SQL_W_UPDATE_ORDER'] = timeout
    erp.rollback = lambda: None                                          # транзакция не закрылась
    batch = ew.apply(erp, ds, 'boss')
    assert [(r.decision.isn, r.reason) for r in batch.results] == [(I1, 'error')] and batch.stopped == 'trancount'
    assert not any(c[1][:1] == (I2,) for c in erp.calls) and erp.commits == 0


def test_apply_rowcount_minus_one_aborts():
    """rowcount −1 (SET NOCOUNT ON) — не знаем, сколько строк: откат, ничего не записано."""
    for name in ('SQL_W_UPDATE_ORDER', 'SQL_W_UPDATE_BODY', 'SQL_W_LOG'):
        erp = _erp()
        ds = _decisions(erp)
        erp.rowcount[name] = -1
        res = _apply(erp, ds)
        assert all(not r.ok for r in res) and erp.commits == 0, name
        assert erp.orders[I1]['van'] == 1 and erp.docs[I1]['body'] == body(van=1) and erp.log == [], name


def test_apply_body_mismatch_format_and_concurrent_body_change():
    erp = _erp()
    ds = _decisions(erp)
    erp.docs[I1]['body'] = body(van=999)            # после предпросмотра fBODY изменили: отпечаток другой
    erp.docs[I2]['body'] = '\r\nNO:VAN\r\n'
    res = _apply(erp, ds)
    assert [(r.ok, r.reason) for r in res] == [(False, 'changed_in_erp'), (False, 'changed_in_erp')]
    assert erp.orders[I1]['van'] == 1 and erp.orders[I2]['van'] == 2 and erp.commits == 0
    # без отпечатка (как у «Վերադարձնել») — строки fBODY проверяются сами
    ds2 = [replace(d, body_sha='') for d in ds]
    res = _apply(erp, ds2)
    assert [(r.ok, r.reason) for r in res] == [(False, 'body_mismatch'), (False, 'body_format')]
    assert erp.orders[I1]['van'] == 1 and erp.orders[I2]['van'] == 2 and erp.commits == 0


def test_apply_body_changed_between_read_and_update():
    """fTS тот же, но текст fBODY другой (UPDATE … AND fBODY = ?) — и наоборот: 0 строк, откат."""
    for change in ('ts', 'body'):
        erp = _erp()
        ds = _decisions(erp)

        def sneak(e, p, change=change):
            if change == 'ts':
                e.docs[p[2]]['ts'] = b'other'
            else:
                e.docs[p[2]]['body'] = e.docs[p[2]]['body'] + 'X:1\r\n'
        erp.hooks['SQL_W_UPDATE_BODY'] = sneak
        res = _apply(erp, ds)
        assert [(r.ok, r.reason) for r in res] == [(False, 'changed_in_erp')] * 2, change
        assert erp.orders[I1]['van'] == 1 and erp.orders[I2]['van'] == 2 and erp.commits == 0


def test_apply_read_back_inside_transaction():
    """Перед COMMIT: fBODY — ровно новый текст, ORDERS — ровно новые значения; иначе откат."""
    erp = _erp()
    ds = _decisions(erp)
    calls = {'n': 0}

    def mangle(e, p, cur):                                # второе чтение fBODY (после UPDATE) — не то
        calls['n'] += 1
        if calls['n'] == 2:
            cur.rows = [(cur.rows[0][0] + ' ', cur.rows[0][1])]
    erp.after['SQL_W_BODY'] = mangle
    assert [(r.ok, r.reason) for r in _apply(erp, ds[:1])] == [(False, 'body_mismatch')]
    assert erp.orders[I1]['van'] == 1 and erp.docs[I1]['body'] == body(van=1) and erp.commits == 0
    erp = _erp()
    ds = _decisions(erp)
    seen = {'n': 0}

    def other(e, p, cur):                                 # второе чтение ORDERS — машина не та
        seen['n'] += 1
        if seen['n'] == 2:
            cur.rows = [(2, 3199, 'X')]
    erp.after['SQL_W_ORDER'] = other
    assert [(r.ok, r.reason) for r in _apply(erp, ds[:1])] == [(False, 'changed_in_erp')]
    assert erp.orders[I1]['van'] == 1 and erp.commits == 0 and erp.log == []


def test_apply_verify_failed_stops_and_skips_untouched():
    erp = _erp()
    ds = _decisions(erp)
    erp.verify_override = [(1, '', body(van=1))]
    batch = ew.apply(erp, ds, 'boss')
    assert [(r.ok, r.reason) for r in batch.results] == [(True, ew.VERIFY_FAILED)] and batch.stopped == ew.VERIFY_FAILED
    assert erp.commits == 1 and erp.orders[I2]['van'] == 2                     # второй заказ не трогали
    erp = _erp()
    skip = [ew.Decision(I1, 'N1', 101, '', 2, 1, 1, '', '', False, 'not_in_plan')]
    assert _apply(erp, skip) == [] and erp.calls == []
    bad = [ew.Decision(I1, 'N1', 101, 'T', 2, 1, 0, '', '', True, '')]          # առաքիչ 0 — не пишем
    r = _apply(erp, bad)[0]
    assert r.reason == 'error' and erp.orders[I1]['van'] == 1 and [c[0] for c in erp.calls] == ['SQL_W_TRANCOUNT']


@pytest.mark.parametrize('committed', [True, False])
def test_apply_commit_lost_is_verified_on_new_connection_and_stops(committed):
    """Обрыв на COMMIT: записано ли — решает чтение назад НОВЫМ соединением; не подтвердилось — commit_unknown и стоп."""
    erp = _erp()

    def commit():
        if committed:
            erp.tx = None                 # сервер успел зафиксировать, ответ потерян
        raise pyodbc.Error('08S01', 'Communication link failure')
    erp.commit = commit
    fresh = []

    def reconnect():
        fresh.append(1)
        return erp
    batch = ew.apply(erp, _decisions(erp), 'boss', reconnect=reconnect)
    if committed:
        assert [(r.ok, r.reason) for r in batch.results] == [(True, ''), (True, '')] and batch.stopped == ''
        assert erp.orders[I1]['van'] == 3199 and fresh == [1, 1]
    else:
        assert [(r.ok, r.reason) for r in batch.results] == [(False, ew.COMMIT_UNKNOWN)] and fresh == [1]
        assert batch.stopped == ew.COMMIT_UNKNOWN and erp.orders[I2]['van'] == 2   # второй заказ не трогали
    # без способа проверить — тоже неизвестно
    erp = _erp()
    erp.commit = lambda: (_ for _ in ()).throw(pyodbc.Error('08S01', 'x'))
    batch = ew.apply(erp, _decisions(erp), 'boss')
    assert [r.reason for r in batch.results] == [ew.COMMIT_UNKNOWN] and batch.stopped == ew.COMMIT_UNKNOWN


def test_apply_journal_pending_failure_rolls_back_and_stops():
    erp = _erp()
    j = FakeJournal(erp, fail={'pending'})
    batch = ew.apply(erp, _decisions(erp), 'boss', journal=j)
    assert batch.stopped == 'journal' and [(r.decision.isn, r.ok) for r in batch.results] == [(I1, False)]
    assert erp.orders[I1]['van'] == 1 and erp.commits == 0 and erp.log == []     # без строки журнала — не фиксируем


def test_apply_journal_finish_failure_leaves_pending_and_stops():
    """Сбой между COMMIT и итогом журнала — строка «pending» остаётся, запись остановлена."""
    erp = _erp()
    j = FakeJournal(erp, fail={'finish'})
    batch = ew.apply(erp, _decisions(erp), 'boss', journal=j)
    assert batch.stopped == 'journal' and [r.decision.isn for r in batch.results] == [I1]
    assert j.rows == [{'isn': I1, 'result': 'pending', 'old_raw': ''}]
    assert erp.orders[I1]['van'] == 3199 and erp.orders[I2]['van'] == 2


def test_apply_lease_lost_stops():
    erp = _erp()
    j = FakeJournal(erp, lease=lambda n: n == 1)                               # второй заказ — замок не продлился
    batch = ew.apply(erp, _decisions(erp), 'boss', journal=j)
    assert batch.stopped == 'lease' and [r.decision.isn for r in batch.results] == [I1] and erp.orders[I2]['van'] == 2


def test_apply_back_to_empty_car_restores_exact_string_never_null():
    for raw in ('', '  '):
        erp = _erp(orders={I1: {'state': 2, 'van': 3199, 'car': '333DO33'}}, bodies={I1: body(van=3199, car='333DO33')})
        d = ew.Decision(I1, 'N1', 101, '333DO33', 2, 3199, 1, '333DO33', '', True, '', 'B001/1', 'A001', '', car_raw=raw)
        r = _apply(erp, [d])[0]
        assert (r.ok, r.reason) == (True, '') and erp.orders[I1]['car'] == raw and erp.docs[I1]['body'] == body(van=1)
        assert erp.log[0]['desc'] == '("Առաքիչ", "B001/1" -> "A001"), ("Առաքող ավտոմեքենա", "333DO33" -> "")'
    erp = _erp(orders={I1: {'state': 2, 'van': 3199, 'car': '333DO33'}}, bodies={I1: body(van=3199, car='OLD 1')})
    d = ew.Decision(I1, 'N1', 101, '333DO33', 2, 3199, 1, '333DO33', 'OLD 1', True, '', car_raw='OLD 1 ')
    erp.docs[I1]['body'] = body(van=3199, car='333DO33')
    assert _apply(erp, [d])[0].ok and erp.orders[I1]['car'] == 'OLD 1 '      # ровно прежняя строка столбца


# ============================== состав SQL записи ==============================

def test_write_sql_is_static_and_minimal():
    """Пишется ТОЛЬКО: ORDERS.fVANAGENTID, ORDERS.fDELIVERYCAR; DOCUMENTS.fBODY, fLASTMODIFYDATE, fLASTMODIFIERID;
    INSERT DOCUMENTSLOG. Весь SQL модуля — в константах SQL_W_*; execute — только с ними; ни ';', ни DELETE/EXEC/DDL."""
    sql = {n: getattr(ew, n) for n in dir(ew) if n.startswith('SQL_W_')}
    assert set(sql) == {'SQL_W_XACT_ABORT', 'SQL_W_DEADLOCK', 'SQL_W_LOCK_TIMEOUT', 'SQL_W_TRANCOUNT', 'SQL_W_ORDER',
                        'SQL_W_UPDATE_ORDER', 'SQL_W_UPDATE_ORDER_VAN', 'SQL_W_CHILDREN', 'SQL_W_BODY', 'SQL_W_NOW',
                        'SQL_W_UPDATE_BODY', 'SQL_W_LOG', 'SQL_W_VERIFY'}
    writes = {n for n, v in sql.items() if _SQL_WRITE_RE.search(v)}
    assert writes == {'SQL_W_UPDATE_ORDER', 'SQL_W_UPDATE_ORDER_VAN', 'SQL_W_UPDATE_BODY', 'SQL_W_LOG'}
    sets = {n: re.search(r'UPDATE\s+(\w+)\s+SET\s+(.*?)\s+WHERE', sql[n], re.S) for n in writes - {'SQL_W_LOG'}}
    cols = {n: (m.group(1), {c.split('=')[0].strip() for c in m.group(2).split(',')}) for n, m in sets.items()}
    assert cols == {'SQL_W_UPDATE_ORDER': ('ORDERS', {'fVANAGENTID', 'fDELIVERYCAR'}),
                    'SQL_W_UPDATE_ORDER_VAN': ('ORDERS', {'fVANAGENTID'}),
                    'SQL_W_UPDATE_BODY': ('DOCUMENTS', {'fBODY', 'fLASTMODIFYDATE', 'fLASTMODIFIERID'})}
    assert re.match(r'INSERT INTO DOCUMENTSLOG \(fISN, fDATE, fUSERID, fSTATE, fOP, fCOMMENT, fCOMPNAME, '
                    r'fCHANGEDESCRIPTION, fSOURCE\)\s+VALUES \(\?, \?, 0, \?, 2, \?, \?, \?, 0\)$', sql['SQL_W_LOG'])
    assert (sql['SQL_W_XACT_ABORT'], sql['SQL_W_DEADLOCK'], sql['SQL_W_LOCK_TIMEOUT']) == \
        ('SET XACT_ABORT ON', 'SET DEADLOCK_PRIORITY LOW', f'SET LOCK_TIMEOUT {ew.LOCK_TIMEOUT_MS}')
    assert ew.LOCK_TIMEOUT_MS == 5000 and sql['SQL_W_TRANCOUNT'] == 'SELECT @@TRANCOUNT' and sql['SQL_W_NOW'] == 'SELECT GETDATE()'
    for n, v in sql.items():
        assert ';' not in v and not re.search(r'\b(DELETE|DROP|ALTER|CREATE|EXEC|EXECUTE|MERGE|TRUNCATE|GRANT)\b', v), n
        assert '{' not in v and '%' not in v and 'NOLOCK' not in v, n       # не шаблон; в транзакции — без грязного чтения
    for n in ('SQL_W_UPDATE_ORDER', 'SQL_W_UPDATE_ORDER_VAN'):                # оптимистично и без дочерних документов
        assert 'ISNULL(fVANAGENTID, 0) = ?' in sql[n] and "LTRIM(RTRIM(ISNULL(fDELIVERYCAR, ''))) = ?" in sql[n]
        assert 'fSTATE = ?' in sql[n]
        assert 'NOT EXISTS (SELECT 1 FROM DOCPARENTS p WITH (UPDLOCK, HOLDLOCK) WHERE p.fPARENTISN = ORDERS.fISN)' in sql[n]
    assert 'DOCPARENTS WITH (UPDLOCK, HOLDLOCK) WHERE fPARENTISN = ?' in sql['SQL_W_CHILDREN']
    # длинный fBODY pyodbc передаёт как ntext: и в SET, и в сравнении — CAST к nvarchar(max) (ошибка 402)
    assert sql['SQL_W_UPDATE_BODY'].rstrip().endswith(
        'WHERE fISN = ? AND fDOCTYPE = 1 AND fTS = ? AND fBODY = CAST(? AS nvarchar(max))')
    assert 'SET fBODY = CAST(? AS nvarchar(max)), fLASTMODIFYDATE = ?, fLASTMODIFIERID = 0' in sql['SQL_W_UPDATE_BODY']
    assert 'GETDATE' not in sql['SQL_W_UPDATE_BODY'] + sql['SQL_W_LOG']      # одно время — параметром (SQL_W_NOW)
    tree = ast.parse((ROOT / 'route_optimizer' / 'erp_write.py').read_text(encoding='utf-8'))
    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
             and c.func.attr in ('execute', 'executemany')]
    assert calls and all(c.func.attr == 'execute' and isinstance(c.args[0], ast.Name)
                         and (c.args[0].id.startswith('SQL_W_') or c.args[0].id == 'sql') for c in calls)
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert sorted(s for s in strings if _SQL_WRITE_RE.search(s)) == sorted(sql[n] for n in writes)


def test_preview_read_sql_passes_read_only_guard():
    from route_optimizer import erp
    sql = erp.SQL_ORDER_STATES.format(ph='?, ?')
    check_sql(sql)
    assert sql.count('WITH (NOLOCK)') == 3 and 'DOCPARENTS' in sql and 'd.fBODY' in sql


def test_load_order_states_reads_through_guard(monkeypatch):
    from route_optimizer import erp
    asked = []

    class Cur:
        def execute(self, sql, params=None):
            asked.append((sql, params))
            erp.check_sql(sql)
            self.rows = ([(I1.lower(), 'N1 ', 101, 2, 3199, '333DO33', 0, body(3199, '333DO33')),
                          (I2, 'N2', 102, 1, None, '', 2, None)]
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
    assert states == {I1: OrderState(I1, 'N1', 101, 2, 3199, '333DO33', False, body(3199, '333DO33')),
                      I2: OrderState(I2, 'N2', 102, 1, 0, '', True, None)}
    assert asked[0][1] == [I1, I2] and agents[1].code == 'A001' and cars['CAR1'].closed and Conn.closed
    assert erp.order_states(Conn(), []) == {}


class _WConn:
    timeout = 0

    def __init__(self, fail=False):
        self.sql, self.commits, self.closed, self.fail = [], 0, False, fail

    def cursor(self):
        conn = self

        class C:
            def execute(self, sql, params=()):
                if conn.fail:
                    raise pyodbc.Error('42000', 'no')
                conn.sql.append(sql)
                return self

            def close(self):
                pass
        return C()

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def test_connect_write_hides_driver_text_and_sets_session(monkeypatch):
    def fail(*a, **k):
        raise pyodbc.Error('08001', 'Login failed for user araqich_rw; PWD=secret')
    monkeypatch.setattr(ew.pyodbc, 'connect', fail)
    with pytest.raises(ew.ErpWriteError) as e:
        ew.connect_write('DRIVER={x};UID=araqich_rw;PWD=secret')
    assert 'secret' not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__
    got = {}
    conn = _WConn()
    monkeypatch.setattr(ew.pyodbc, 'connect', lambda dsn, **k: got.update(k, dsn=dsn) or conn)
    assert ew.connect_write('DSN=x') is conn and conn.timeout == ew.QUERY_TIMEOUT_S and got['autocommit'] is False
    assert conn.sql == ['SET XACT_ABORT ON', 'SET DEADLOCK_PRIORITY LOW', 'SET LOCK_TIMEOUT 5000'] and conn.commits == 1
    bad = _WConn(fail=True)
    monkeypatch.setattr(ew.pyodbc, 'connect', lambda dsn, **k: bad)
    with pytest.raises(ew.ErpWriteError):
        ew.connect_write('DSN=x')
    assert bad.closed
    monkeypatch.delenv(ew.DSN_ENV, raising=False)
    assert ew.write_dsn() is None
    monkeypatch.setenv(ew.DSN_ENV, '  ')
    assert ew.write_dsn() is None
    monkeypatch.setenv(ew.DSN_ENV, ' DSN=x ')
    assert ew.write_dsn() == 'DSN=x'
    assert 'ТОЛЬКО на CT115' in ew.__doc__


# ============================== API ==============================

CAR_DRIVERS = {'CAR1': 'Արամ Պետրոսյան', 'CAR2': 'Գագիկ Սարգսյան'}
API_CARS = {'CAR1': Car('CAR1', 'HOWO', False), 'CAR2': Car('CAR2', 'FORD', True)}   # CAR2 закрыта — только առաքիչ
CIDS = {_isn(1): 101, _isn(2): 102, _isn(3): 104, _isn(70): 101}
ALL3 = [_isn(1), _isn(2), _isn(3)]


@pytest.fixture
def day(client, monkeypatch):
    """План 01.10 (сегодня) на CAR1 и CAR2; ERP — подделка FakeErp (её же читает предпросмотр); роль — из g.user_role (как
    ставит app_v2), по умолчанию admin. Хук роли — до первого запроса (Flask не принимает его позже). Предел заказов за
    раз — 10 (по умолчанию 1 — test_api_default_cap_is_one)."""
    role = {'value': 'admin'}

    @client.application.before_request
    def _role():
        from flask import g
        if role['value']:
            g.user_role = role['value']
    monkeypatch.setenv(ew.MAX_ORDERS_ENV, '10')
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    for car, name in CAR_DRIVERS.items():
        state.store.save_truck_driver(car, DAY, name, 'qa')
    isns = [_isn(1), _isn(2), _isn(3), _isn(70)]
    erp = FakeErp({i: {'state': 2, 'van': 1, 'car': ''} for i in isns}, {i: body(van=1) for i in isns})
    reads = []

    def states(asked):
        reads.append(sorted(asked))
        out = {i: OrderState(i, 'N' + i[-1], CIDS[i], o['state'], o['van'] or 0, (o['car'] or '').strip(),
                             bool(erp.children.get(i)), erp.docs[i]['body'] if i in erp.docs else None)
               for i, o in erp.orders.items() if i in asked}
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


def _rows(p):
    return {o['isn']: (t['truck'], t['driver'], o) for t in p['trucks'] for o in t['orders']}


def _write(client, p, orders=None):
    orders = [o['isn'] for t in p['trucks'] for o in t['orders'] if o['write']] if orders is None else orders
    return client.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': p['signature'], 'orders': orders})


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
    assert client.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': 'x', 'orders': [I1]}).status_code == 404
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
    monkeypatch.setattr(ew.pyodbc, 'connect', lambda dsn, **k: got.append(dsn) or _WConn())
    state.erp_write_conn()
    assert got == ['DRIVER={x};PWD=secret']


def test_api_admin_only(day):
    _sent(day)
    for role in ('user', 'garage', 'warehouse', None):
        day.role['value'] = role
        assert day.get('/api/routes/dispatch/erp-write?date=' + DAY).status_code == 403
        assert day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': 'x', 'orders': [I1]}).status_code == 403
        assert day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [_isn(1)]}).status_code == 403
    assert day.erp.calls == [] and day.conns == []


def test_api_refuses_unsent_plan_and_past_day(day, monkeypatch):
    d = _build(day, ('CAR1', 'CAR2'))
    assert 'sent' not in d and d['erp_write'] == {'max_orders': 10, 'trucks': {}}
    r = day.get('/api/routes/dispatch/erp-write?date=' + DAY)
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_NOT_SENT
    r = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': 'x', 'orders': [_isn(1)]})
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_NOT_SENT
    r = day.get('/api/routes/dispatch/erp-write?date=2026-09-30')
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_PAST
    _sent(day)
    p = _preview(day)
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 2, 9, 0, tzinfo=ac.YEREVAN))
    r = _write(day, p)
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_PAST
    r = day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [_isn(1)]})
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_PAST
    assert day.post('/api/routes/dispatch/erp-write', json={'date': 'x', 'expected': p['signature']}).status_code == 400
    assert day.erp.calls == [] and day.conns == []


def test_api_orders_are_required_and_capped(day, monkeypatch):
    _sent(day)
    p = _preview(day)
    assert p['max_orders'] == 10
    for bad in (None, [], 'x', ['x']):
        body_ = {'date': DAY, 'expected': p['signature'], **({'orders': bad} if bad is not None else {})}
        r = day.post('/api/routes/dispatch/erp-write', json=body_)
        assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_BAD_ORDERS, bad
    monkeypatch.setenv(ew.MAX_ORDERS_ENV, '2')
    r = _write(day, p)
    assert r.status_code == 400 and r.get_json()['error'] == 'Մեկ անգամից կարելի է գրել ոչ ավելի, քան 2 պատվեր'
    assert day.erp.calls == [] and day.conns == []


def test_api_default_cap_is_one(day, monkeypatch):
    monkeypatch.delenv(ew.MAX_ORDERS_ENV)
    _sent(day)
    p = _preview(day)
    assert p['max_orders'] == 1
    r = _write(day, p)
    assert r.status_code == 400 and '1 պատվեր' in r.get_json()['error'] and day.erp.calls == []
    one = sorted(_rows(p))[0]
    r = _write(day, p, [one])
    assert r.status_code == 200 and [x['isn'] for x in r.get_json()['results']] == [one]


def test_api_preview_write_and_idempotent_repeat(day):
    d = _sent(day)
    owner = _plan_stops(d)
    p = _preview(day)
    assert day.erp.calls == [] and day.conns == []                    # предпросмотр ERP не пишет
    assert sorted(day.reads[-1]) == sorted(ALL3)
    cid_of = {_isn(1): 101, _isn(2): 102, _isn(3): 104}
    for isn, (truck, driver, o) in _rows(p).items():
        assert truck == owner[cid_of[isn]] and driver == CAR_DRIVERS[truck] and o['write'] is True
        assert o['old_van'] == {'id': 1, 'code': 'A001', 'name': 'Менеджер 1'}
        assert o['old_body'] == body(van=1)                           # проба: документ до и после
        if truck == 'CAR1':
            assert (o['new_van']['code'], o['new_car'], o['reason']) == ('B001/1', 'CAR1', '')
            assert o['new_body'] == rw(body(van=1), 3199, 'CAR1')
        else:                                                         # CAR2 закрыта в ERP — только առաքիչ
            assert (o['new_van']['code'], o['new_car'], o['reason'], o['reason_text']) == \
                ('B002/1', '', 'car_not_in_erp', ew.REASON_HY['car_not_in_erp'])
            assert o['new_body'] == body(van=3200)
    assert p['counts'] == {'write': 3, 'skip': 0} and len(p['signature']) == 32
    bad = day.post('/api/routes/dispatch/erp-write', json={'date': DAY, 'expected': 'other', 'orders': ALL3})
    assert bad.status_code == 409 and bad.get_json()['error'] == views.ERP_WRITE_STALE and day.conns == []
    r = _write(day, p)
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out['counts'] == {'ok': 3, 'failed': 0, 'unknown': 0} and 'stopped' not in out
    assert day.conns == [1] and day.erp.closed
    assert all(day.erp.orders[i]['van'] == (3199 if owner[cid_of[i]] == 'CAR1' else 3200) for i in cid_of)
    assert all(x['comment'] == 'Կայք՝ ' and x['compname'] == 'ARAQICH-SITE' for x in day.erp.log)
    log = day.state.store.erp_write_log(DAY)
    assert [(x['op'], x['result'], x['order_isn']) for x in log] == [('write', 'ok', x['isn']) for x in out['results']]
    page = day.get('/api/routes/dispatch?date=' + DAY).get_json()
    trucks = {owner[cid_of[i]] for i in cid_of}
    assert set(page['erp_write']['trucks']) == trucks
    assert sum(t['written'] for t in page['erp_write']['trucks'].values()) == 3
    assert not any('drift' in t for t in page['erp_write']['trucks'].values())   # ERP = записанное = план
    # повтор: ничего не меняется — нечего писать
    p2 = _preview(day)
    assert p2['counts'] == {'write': 0, 'skip': 3} and {o['reason'] for t in p2['trucks'] for o in t['orders']} == {'unchanged'}
    calls = len(day.erp.calls)
    r = _write(day, p2, ALL3)
    assert r.status_code == 400 and r.get_json()['error'] == views.ERP_WRITE_NOT_IN_PREVIEW and len(day.erp.calls) == calls
    # замок записи снят (и в базе)
    assert day.state.store.erp_write_lease('other') is True


def test_api_page_shows_drift_from_erp(day):
    d = _sent(day)
    owner = _plan_stops(d)
    p = _preview(day)
    assert _write(day, p).status_code == 200
    day.erp.orders[_isn(1)]['van'] = 3300                              # в ERP առաքիչ поменяли руками
    page = day.get('/api/routes/dispatch?date=' + DAY).get_json()
    t = page['erp_write']['trucks'][owner[101]]
    assert t['drift'] == 1
    reads = len(day.reads)
    day.get('/api/routes/dispatch?date=' + DAY)                         # кэш: ERP повторно не читается
    assert len(day.reads) == reads
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert 'ERP-ն տարբերվում է պլանից' in js


def test_api_trial_writes_exactly_one_order(day):
    _sent(day)
    p = _preview(day)
    one = sorted(_rows(p))[0]
    r = _write(day, p, [one])
    assert r.status_code == 200, r.get_json()
    assert [x['isn'] for x in r.get_json()['results']] == [one] and [x['isn'] for x in day.erp.log] == [one]
    assert sum(o['van'] != 1 for o in day.erp.orders.values()) == 1
    # не из списка записи (повтор того же — уже unchanged; чужой) — 400, ничего не пишется
    p = _preview(day)
    calls = len(day.erp.calls)
    for bad in ([one], [_isn(99)], [], ['x'], 'x'):
        assert _write(day, p, bad).status_code == 400, bad
    assert len(day.erp.calls) == calls


def test_api_order_entered_after_sending_is_not_written(day):
    _sent(day)
    late = _dorder(70, 101, 50.0)                                       # магазин 101 из плана, заказ — после отправки
    loader = day.state.dispatch_loader
    day.state.dispatch_loader = lambda since, until, d: replace(loader(since, until, d),
                                                                orders=(*loader(since, until, d).orders, late))
    day.state.dispatch_cache.clear()
    p = _preview(day)
    row = _rows(p)[_isn(70)][2]
    assert (row['write'], row['reason'], row['reason_text']) == (False, 'not_in_sent_plan', 'Ուղարկելուց հետո մուտքագրված պատվեր')
    assert p['counts'] == {'write': 3, 'skip': 1}
    assert _write(day, p, [_isn(70)]).status_code == 400
    r = _write(day, p).get_json()
    assert _isn(70) not in [x['isn'] for x in r['results']] and day.erp.orders[_isn(70)]['van'] == 1


def test_api_driver_on_two_trucks_is_ambiguous(day):
    _sent(day)
    day.state.store.save_truck_driver('CAR2', DAY, CAR_DRIVERS['CAR1'], 'qa', only_day=True)   # ведёт и CAR2
    p = _preview(day)
    assert p['counts']['write'] == 0
    assert {o['reason'] for t in p['trucks'] for o in t['orders']} == {'courier_ambiguous'}


def test_api_lone_surrogate_body_does_not_500(day):
    day.erp.docs[_isn(1)]['body'] = body(van=1) + 'X:\ud800\r\n'
    _sent(day)
    p = _preview(day)
    row = _rows(p)[_isn(1)][2]
    assert row['write'] and row['old_body'].endswith('X:\ud800\r\n')
    r = _write(day, p, [_isn(1)])
    assert r.status_code == 200 and r.get_json()['results'][0]['ok']


def test_api_signature_mismatch_when_erp_changed_after_preview(day):
    _sent(day)
    p = _preview(day)
    day.erp.children[_isn(1)] = 1                                      # офис выписал накладную
    r = _write(day, p, ALL3)
    assert r.status_code == 409 and r.get_json()['conflict'] is True and day.erp.calls == []
    p = _preview(day)
    row = _rows(p)[_isn(1)][2]
    assert (row['write'], row['reason']) == (False, 'has_child_docs') and 'new_body' not in row
    day.erp.docs[_isn(2)]['body'] += 'X:1\r\n'                          # правка документа — тоже другой отпечаток
    r = _write(day, p, [_isn(2)])
    assert r.status_code == 409 and day.erp.calls == []


def test_api_write_busy_is_conflict(day):
    _sent(day)
    p = _preview(day)
    assert day.state.erp_write_lock.acquire(blocking=False)
    try:
        r = _write(day, p)
        assert r.status_code == 409 and r.get_json()['error'] == views.ERP_WRITE_BUSY
    finally:
        day.state.erp_write_lock.release()
    # другой процесс сервера держит замок в базе
    assert day.state.store.erp_write_lease('other-process') is True
    r = _write(day, p)
    assert r.status_code == 409 and r.get_json()['error'] == views.ERP_WRITE_BUSY
    r = day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [_isn(1)]})
    assert r.status_code == 409
    assert day.erp.calls == [] and day.state.erp_write_lock.acquire(blocking=False)   # замок процесса отпущен
    day.state.erp_write_lock.release()
    day.state.store.erp_write_release('other-process')
    assert _write(day, p).status_code == 200


def test_api_journal_failure_before_commit_rolls_back(day, monkeypatch, caplog):
    _sent(day)
    p = _preview(day)

    def broken(*a, **k):
        raise st.StoreError('база недоступна')
    monkeypatch.setattr(day.state.store, 'add_erp_write', broken)
    r = _write(day, p)
    assert r.status_code == 200
    out = r.get_json()
    assert out['stopped'] == views.ERP_WRITE_STOP['journal'] and len(out['results']) == 1 and not out['results'][0]['ok']
    assert all(o['van'] == 1 for o in day.erp.orders.values()) and day.erp.commits == 0   # без журнала — не фиксируем
    isn = out['results'][0]['isn']
    assert any('журнал сайта НЕ записан' in m and isn in m for m in caplog.messages)


def test_api_crash_between_commit_and_journal_leaves_pending(day, monkeypatch, caplog):
    _sent(day)
    p = _preview(day)

    def broken(*a, **k):
        raise st.StoreError('база недоступна')
    monkeypatch.setattr(day.state.store, 'finish_erp_write', broken)
    out = _write(day, p).get_json()
    assert out['stopped'] == views.ERP_WRITE_STOP['journal'] and len(out['results']) == 1 and out['results'][0]['ok']
    isn = out['results'][0]['isn']
    assert day.erp.orders[isn]['van'] != 1 and sum(o['van'] != 1 for o in day.erp.orders.values()) == 1
    assert [(x['order_isn'], x['result']) for x in day.state.store.erp_write_log(DAY)] == [(isn, 'pending')]
    assert any('журнал сайта НЕ записан' in m and isn in m for m in caplog.messages)
    monkeypatch.delattr(day.state.store, 'finish_erp_write')            # база снова пишется (метод класса)
    page = day.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert sum(t['unknown'] for t in page['erp_write']['trucks'].values()) == 1
    p2 = _preview(day)                                                   # сверка с ERP только чтением
    assert p2['pending'] == [{'isn': isn, 'doc_num': 'N' + isn[-1], 'truck': out['results'][0]['truck'], 'erp': 'written',
                              'text': 'Անհայտ՝ ստուգեք ERP-ում'}]
    # «Վերադարձնել» разрешён: в ERP ровно наши значения
    r = day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [isn]}).get_json()
    assert [(x['isn'], x['ok']) for x in r['results']] == [(isn, True)] and day.erp.orders[isn]['van'] == 1


def test_api_commit_unknown_stops_and_page_shows_it(day):
    _sent(day)
    p = _preview(day)
    erp = day.erp
    real = erp.commit

    def lost():
        erp.tx = None                                                  # сервер зафиксировал, ответ потерян
        raise pyodbc.Error('08S01', 'Communication link failure')
    erp.commit = lost
    erp.verify_override = []                                           # и проверка новым соединением не удалась
    out = _write(day, p).get_json()
    assert out['stopped'] == views.ERP_WRITE_STOP[ew.COMMIT_UNKNOWN] and out['counts'] == {'ok': 0, 'failed': 0, 'unknown': 1}
    isn = out['results'][0]['isn']
    assert out['results'][0]['reason_text'] == 'Անհայտ՝ ստուգեք ERP-ում'
    log = day.state.store.erp_write_log(DAY)
    assert [(x['order_isn'], x['result'], x['reason']) for x in log] == [(isn, 'unknown', 'commit_unknown')]
    page = day.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert sum(t['unknown'] for t in page['erp_write']['trucks'].values()) == 1
    # «Վերադարձնել»: в ERP наши значения — проверка прошла, возврат разрешён
    erp.commit, erp.verify_override = real, None
    r = day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [isn]}).get_json()
    assert [(x['isn'], x['ok']) for x in r['results']] == [(isn, True)] and erp.orders[isn]['van'] == 1


def test_api_undo_reverts_our_write_once(day):
    _sent(day)
    p = _preview(day)
    out = _write(day, p).get_json()
    done = [x['isn'] for x in out['results'] if x['ok']]
    # третий заказ тем временем изменили в ERP — его не трогаем
    day.erp.orders[_isn(3)]['van'] = 3300
    r = day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': done})
    assert r.status_code == 200, r.get_json()
    got = {x['isn']: (x['ok'], x['reason']) for x in r.get_json()['results']}
    assert got == {_isn(1): (True, ''), _isn(2): (True, ''), _isn(3): (False, 'changed_in_erp')}
    # машина — ровно прежняя строка столбца ('' — не NULL), документ — как был
    assert day.erp.orders[_isn(1)] == {'state': 2, 'van': 1, 'car': ''} and day.erp.docs[_isn(1)]['body'] == body(van=1)
    assert day.erp.orders[_isn(2)] == {'state': 2, 'van': 1, 'car': ''}
    assert day.erp.log[-1]['desc'].startswith('("Առաքիչ", "B00')
    log = day.state.store.erp_write_log(DAY)
    assert [x['op'] for x in log].count('undo') == 2
    # второй раз — нечего возвращать (последняя запись — возврат); чужой заказ — тоже
    r = day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [_isn(1), _isn(9)]}).get_json()
    assert {x['isn']: x['reason'] for x in r['results']} == {_isn(1): 'not_written', _isn(9): 'not_written'}
    for bad in ([], ['x'], 'x', [1]):
        assert day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': bad}).status_code == 400
    page = day.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert sum(t['undone'] for t in page['erp_write']['trucks'].values()) == 2


def test_api_undo_after_two_writes_restores_previous(day):
    """A → B (запись), водитель машины сменился, B → C (вторая запись); «Վերադարձնել» — к B, не к A."""
    d = _sent(day)
    owner = _plan_stops(d)
    isn = _isn(1)
    truck = owner[101]
    p = _preview(day)
    assert _write(day, p, [isn]).status_code == 200
    b = day.erp.orders[isn]['van']
    assert b in (3199, 3200)
    day.state.store.save_truck_driver(truck, DAY, 'Կարեն Ավագյան', 'qa', only_day=True)
    p = _preview(day)
    row = _rows(p)[isn][2]
    assert row['write'] and row['new_van']['id'] == 3400
    assert _write(day, p, [isn]).status_code == 200 and day.erp.orders[isn]['van'] == 3400
    r = day.post('/api/routes/dispatch/erp-write/undo', json={'date': DAY, 'orders': [isn]}).get_json()
    assert [(x['isn'], x['ok']) for x in r['results']] == [(isn, True)] and day.erp.orders[isn]['van'] == b


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
        conn.execute('DROP TABLE erp_write_lease')
        conn.execute("UPDATE meta SET value = '28' WHERE key = 'schema_version'")
        conn.commit()
    s2 = st.Store(path)
    assert s2.erp_write_log('2026-10-01') == []
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('29',)
    assert st.SCHEMA_VERSION == 29
    assert st._MIGRATIONS[28] == (st._ERP_WRITE_LOG_TABLE, st._ERP_WRITE_LOG_INDEX, st._ERP_WRITE_LEASE_TABLE)
    assert s2.truck_drivers('2026-10-01')[0] == {'CAR1': 'Արամ'}
    row = {'order_isn': I1, 'doc_num': 'N1', 'customer_id': 101, 'truck': 'CAR1', 'old_van': 1, 'new_van': 3199,
           'old_car': '', 'new_car': 'CAR1', 'old_car_raw': '', 'result': 'ok', 'reason': ''}
    assert s2.add_erp_write('2026-10-01', 'write', row, 'boss') == 1
    s2.add_erp_write('2026-10-01', 'undo', {**row, 'old_car_raw': 'X ', 'result': 'failed', 'reason': 'error'}, None)
    pid = s2.add_erp_write('2026-10-01', 'write', {**row, 'result': 'pending'}, 'boss')
    s2.finish_erp_write(pid, 'unknown', 'commit_unknown')
    with pytest.raises(ValueError):
        s2.finish_erp_write(pid, 'ok', '')                                  # итог — один раз
    with pytest.raises(ValueError):
        s2.finish_erp_write(1, 'pending', '')
    got = s2.erp_write_log('2026-10-01')
    assert [(x['id'], x['op'], x['old_car_raw'], x['result'], x['by_user']) for x in got] == \
        [(1, 'write', '', 'ok', 'boss'), (2, 'undo', 'X ', 'failed', None), (3, 'write', '', 'unknown', 'boss')]
    assert set(got[0]) == set(st.ERP_WRITE_LOG_FIELDS) and s2.erp_write_log('2026-10-02') == []
    for bad in ({**row, 'result': 'maybe'}, {**row, 'old_van': '1'}, {k: v for k, v in row.items() if k != 'truck'},
                {**row, 'old_car_raw': None}):
        with pytest.raises(ValueError):
            s2.add_erp_write('2026-10-01', 'write', bad, 'boss')
    with pytest.raises(ValueError):
        s2.add_erp_write('2026-10-01', 'delete', row, 'boss')
    with pytest.raises(ValueError):
        s2.add_erp_write('01.10.2026', 'write', row, 'boss')


def test_write_lease_is_shared_and_expires(tmp_path):
    a, b = st.Store(str(tmp_path / 'r.db')), st.Store(str(tmp_path / 'r.db'))   # два процесса — одна база
    assert a.erp_write_lease('t1') is True and b.erp_write_lease('t2') is False
    assert a.erp_write_lease('t1') is True                                     # свой — продлевается
    b.erp_write_release('t2')                                                  # чужой токен — не снимает
    assert b.erp_write_lease('t2') is False
    a.erp_write_release('t1')
    assert b.erp_write_lease('t2') is True
    assert a.erp_write_lease('t3', ttl_s=-1) is False                          # занят t2
    b.erp_write_release('t2')
    assert a.erp_write_lease('t3', ttl_s=-1) is True and b.erp_write_lease('t4') is True   # просроченный — свободен
    with pytest.raises(ValueError):
        a.erp_write_lease('')


def test_page_assets_bumped_and_ui_selects_nothing_by_default():
    page = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    assert "routes_dispatch.js') }}?v=128" in page and "routes_dispatch.css') }}?v=83" in page
    assert 'id="dpErpDlg"' in page and 'Գրել ERP-ում' in page and '<span>Հաստատել (0)</span>' in page
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    start, end = js.index('// ---------- «Գրել ERP-ում»'), js.index('// ---------- Требует внимания (№81)')
    part = js[start:end]
    # строки ERP — только textContent: innerHTML — лишь у кнопки с постоянной разметкой
    assert part.count('innerHTML') == 1 and "b.innerHTML = '<i class=\"fas fa-file-export\"" in part
    # H1: по умолчанию не отмечено ничего; кнопка — «Հաստատել (N)», при 0 и сверх предела — неактивна; список — всегда явно
    assert 'cb.checked = false' in part and 'cb.checked = true' not in part
    assert "'Հաստատել (' + n + ')'" in part and "$('dpErpOk').disabled = !erp.sig || !n || over" in part
    assert 'orders: picked' in part and 'erp.all' not in part
    assert '/api/routes/dispatch/erp-write' in part and 'expected: erp.sig' in part
    assert 'Անհայտ՝ ստուգեք ERP-ում' in part

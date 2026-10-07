# -*- coding: utf-8 -*-
"""Баланс тары магазинов «Տարա» (ответ владельца №87 п. 9, courier/tare.py): ушло — tare_expected доставленных точек
(полная — вся, частичная — по доставленному товару через связи тары ERP, без связей — по доле веса и approx, отказ —
ничего), забрано — действующие отметки tare (supersedes), начальный остаток с as_of (дни до него и сам день не
считаются), история магазина, courier.db 8 → 9 (tare_opening), импорт начальных остатков (предпросмотр, ошибки, всё или
ничего), CSV, доступ (только администратор, снаружи — 404).

Синтетические данные: courier.db — временная, ERP подменена (связи тары, магазины по коду), сеть не нужна.
Запуск из корня проекта:  python -m pytest tests/test_courier_tare.py -q
"""
import csv
import io
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import store as cstore, tare as tr  # noqa: E402
from courier.erp_day import ContainerLink  # noqa: E402
from courier.store import SCHEMA_VERSION, Store  # noqa: E402
from route_optimizer.erp import ErpError  # noqa: E402
from test_courier import (PAST, _ev, _fresh_ref_cache, _ingest, _pin_env, _uid, _who, app, client, now,  # noqa: E402,F401
                          st)

DAY0 = '2026-09-30'                       # день раньше PAST (2026-10-01); «сегодня» тестов — 2026-10-02
API = '/api/courier/admin/tare'
BOTTLE, CRATE = 'erp:900', 'erp:901'
LINKS = (ContainerLink(1, 900, 1.0, 1.0),   # вода 19 л — бутыль на штуку
         ContainerLink(2, 901, 6.0, 1.0))   # кола — ящик на 6 штук
NAMES = {900: 'Շիշ 19լ', 901: 'Արկղ'}
WATER, COLA, CUP = 1, 2, 3                # CUP — без тары


def _stop(sid, cid, lines, tare, seq=1, agent='Մենեջեր Ա', name=None):
    """Точка /day: lines — (line_id, qty, product_id, кг строки); tare — {вид: количество} (tare_expected)."""
    return {'stop_id': sid, 'seq': seq, 'collect': 'none', 'doc_number': 'N' + sid[7:10],
            'customer': {'id': cid, 'code': f'C{cid}', 'name': name or f'Խանութ {cid}'}, 'agent_name': agent,
            'amount_due': 0,
            'lines': [{'line_id': lid, 'qty': q, 'price': 0, 'product_id': pid, 'weight_kg': kg}
                      for lid, q, pid, kg in lines],
            'tare_expected': [{'tare_id': t, 'name': {BOTTLE: NAMES[900], CRATE: NAMES[901]}.get(t, ''), 'qty': q}
                              for t, q in sorted(tare.items())]}


def _deliver(sid, lines, at, day=PAST, **extra):
    return _ev('delivery', sid, {'lines': [{'line_id': lid, 'qty': q} for lid, q in lines], 'reason_id': 'other',
                                 **extra}, at, day)


def _tare(sid, items, at, day=PAST, **extra):
    return _ev('tare', sid, {'items': [{'tare_id': t, 'qty': q} for t, q in items], **extra}, at, day)


@pytest.fixture
def erp(st):
    """Подделка ERP: связи тары и магазины по коду; calls — сколько раз читали."""
    box = {'links': lambda: (LINKS, NAMES), 'codes': {'C77': (77, 'Նոր խանութ')}, 'calls': []}

    def links():
        box['calls'].append('links')
        return box['links']()

    def codes(want):
        box['calls'].append(('codes', sorted(want)))
        return {c: box['codes'][c] for c in want if c in box['codes']}
    st.tare_links_loader = links
    st.customer_code_loader = codes
    return box


@pytest.fixture
def days(st, erp):
    """Два дня машины CAR1 (DAY0 и PAST), магазины 10, 11, 12:
    DAY0: 10 — вода 10 → доставлено всё (ушло 10 бутылей);
    PAST: 10 — вода 10 + стаканы 20, стаканы не взяли (partial: ушло 10 бутылей — по товару, а не по весу), тара 6 →
          исправлена (supersedes) на 7; 11 — вода 4 + кола 6, доставлено всё (4 бутыли, 1 ящик);
          12 — вода 5, отказ (ничего не ушло), но водитель забрал 3 бутыли."""
    a = _who(st, 'A', '1111')
    s0 = 'S:' + _uid(500)
    st.store.save_day(DAY0, 'CAR1', [_stop(s0, 10, [('w0', 10, WATER, 190.0)], {BOTTLE: 10})], 'v0',
                      DAY0 + 'T08:00:00+04:00')
    _ingest(st, a, _deliver(s0, [('w0', 10)], '10:00:00', DAY0))
    sa, sb, sc = 'S:' + _uid(501), 'S:' + _uid(502), 'S:' + _uid(503)
    st.store.save_day(PAST, 'CAR1', [
        _stop(sa, 10, [('a1', 10, WATER, 190.0), ('a2', 20, CUP, 20.0)], {BOTTLE: 10}),
        _stop(sb, 11, [('b1', 4, WATER, 76.0), ('b2', 6, COLA, 9.0)], {BOTTLE: 4, CRATE: 1}, seq=2, agent='Մենեջեր Բ'),
        _stop(sc, 12, [('c1', 5, WATER, 95.0)], {BOTTLE: 5}, seq=3)], 'v1', PAST + 'T08:00:00+04:00')
    first = _tare(sa, [(BOTTLE, 6)], '10:05:00')
    _ingest(st, a, _deliver(sa, [('a1', 10), ('a2', 0)], '10:00:00'), first,
            _deliver(sb, [('b1', 4), ('b2', 6)], '11:00:00'),
            _deliver(sc, [('c1', 0)], '12:00:00'), _tare(sc, [(BOTTLE, 3)], '12:01:00'))
    _ingest(st, a, _tare(sa, [(BOTTLE, 7)], '09:00:00', supersedes=first['id']))   # исправление раньше по часам
    return {'sa': sa, 'sb': sb, 'sc': sc, 's0': s0, 'who': a}


def _rows(client):
    body = client.get(API).get_json()
    assert body['success'], body
    return body, {(r['customer_id'], r['tare_id']): r for r in body['rows']}


# ============================== правила ==============================

def test_went_rules():
    links = tr.links_by_product(LINKS)
    assert links == {1: {BOTTLE: 1.0}, 2: {CRATE: 1 / 6}}
    basis = {'tare_expected': [{'tare_id': BOTTLE, 'qty': 10}, {'tare_id': CRATE, 'qty': 2}],
             'lines': [{'product_id': WATER, 'qty': 10, 'weight_kg': 190}, {'product_id': COLA, 'qty': 12, 'weight_kg': 18},
                       {'product_id': CUP, 'qty': 20, 'weight_kg': 20}]}
    assert tr.went('full', basis, {}, links) == ({BOTTLE: 10, CRATE: 2}, False)
    assert tr.went('covered', basis, {}, links) == ({BOTTLE: 10, CRATE: 2}, False)
    assert tr.went('refused', basis, {WATER: 10}, links) == ({}, False)
    assert tr.went('pending', basis, {}, links) == ({}, False)
    # частично: вода 7 из 10, кола 6 из 12, стаканы — 0 (тары нет): бутыли 7, ящики 1
    assert tr.went('partial', basis, {WATER: 7, COLA: 6}, links) == ({BOTTLE: 7, CRATE: 1}, False)
    assert tr.went('in_progress', basis, {WATER: 15}, links) == ({BOTTLE: 10}, False)   # сверх строки не считается
    # связей нет (ERP недоступна) — доля веса строк: (7·19 + 6·1.5) / 228 кг
    got, approx = tr.went('partial', basis, {WATER: 7, COLA: 6}, {})
    share = (190 * 0.7 + 18 * 0.5) / 228
    assert approx and got == {BOTTLE: round(10 * share, 2), CRATE: round(2 * share, 2)}


def test_parse_values():
    from datetime import date
    today = date(2026, 10, 2)
    assert [tr.parse_qty(v) for v in (5, 2.345, '12,5', ' 7 ', '1 000', 0)] == [5.0, 2.35, 12.5, 7.0, 1000.0, 0.0]
    assert [tr.parse_qty(v) for v in (-1, 'x', None, True, float('nan'), 1e7, '')] == [None] * 7
    assert [tr.parse_day(v, today) for v in ('2026-10-02', '01.10.2026', '1/9/2026')] == \
        ['2026-10-02', '2026-10-01', '2026-09-01']
    assert [tr.parse_day(v, today) for v in ('2026-10-03', '31.02.2026', '1999-12-31', 20261001, '')] == [None] * 5
    kinds = {BOTTLE: 'Շիշ 19լ', CRATE: 'Արկղ', 'custom:1': 'Կեգ', 'custom:2': 'կեգ '}
    assert tr.resolve_kind(' շիշ  19Լ ', kinds) == (BOTTLE, None) and tr.resolve_kind('erp:901', kinds) == (CRATE, None)
    assert tr.resolve_kind('erp:5', kinds)[0] is None and tr.resolve_kind('Կեգ', kinds)[0] is None   # двусмысленно
    assert tr.resolve_kind('', kinds)[1] and tr.resolve_kind(None, kinds)[1]


def test_erp_loaders_read_only_cached_and_short_timeouts(monkeypatch):
    """Связи тары — общий с /day кэш 'containers' (второй вызов без ERP); магазины по коду — один SELECT по чанкам;
    короткие таймауты соединения; только SELECT (read-only guard)."""
    from courier import erp_day as ed
    from route_optimizer import erp as rerp
    calls, conns = [], []

    def select(conn, sql, params=()):
        rerp.check_sql(sql)
        calls.append((sql, list(params)))
        if sql == ed.SQL_CONTAINERS:
            return [(1, 900, 1, 1, 'Շիշ 19լ')]
        assert sql.startswith(ed.SQL_CUSTOMERS_BY_CODE.split('{')[0]) and 'WITH (NOLOCK)' in sql
        return [(77, 'C77 ', 'Նոր')] if 'C77' in params else []
    monkeypatch.setattr(ed, '_select', select)
    monkeypatch.setattr(rerp, 'connect', lambda cs, **kw: conns.append(kw) or object())
    monkeypatch.setattr(rerp, 'close_quietly', lambda conn: None)
    assert ed.container_links('X') == ((ContainerLink(1, 900, 1.0, 1.0),), {900: 'Շիշ 19լ'})
    assert ed.container_links('X')[1] == {900: 'Շիշ 19լ'} and len(calls) == 1
    assert ed.customers_by_code('X', [' C77', 'C404', '']) == {'C77': (77, 'Նոր')}
    assert calls[-1][1] == ['C404', 'C77'] and ed.customers_by_code('X', ['  ']) == {}
    assert conns == [{'login_timeout': 3, 'query_timeout': 10}] * 2


# ============================== баланс и история ==============================

def test_balance_full_partial_refused_supersedes(client, days, erp):
    body, rows = _rows(client)
    assert (body['first_day'], body['last_day'], body['lost'], body['links_failed']) == (DAY0, PAST, 0, False)
    assert {(k, r['went'], r['returned'], r['balance'], r['approx']) for k, r in rows.items()} == {
        ((10, BOTTLE), 20.0, 7.0, 13.0, False),     # 10 + 10 (стаканы без тары), забрано 7 (исправление)
        ((11, BOTTLE), 4.0, 0.0, 4.0, False), ((11, CRATE), 1.0, 0.0, 1.0, False),
        ((12, BOTTLE), 0.0, 3.0, -3.0, False)}      # отказ — ничего не ушло, забрали 3
    assert [(r['customer_id'], r['tare_id']) for r in body['rows']][0] == (10, BOTTLE)   # больший долг первым
    r = rows[(11, CRATE)]
    assert (r['code'], r['name'], r['agent'], r['car_code'], r['last_day'], r['opening'], r['as_of']) == \
        ('C11', 'Խանութ 11', 'Մենեջեր Բ', 'CAR1', PAST, None, None)
    assert {k['tare_id']: k['name'] for k in body['kinds']} == {BOTTLE: 'Շիշ 19լ', CRATE: 'Արկղ'}


def test_balance_cached_until_day_changes(client, st, days, erp, monkeypatch):
    _rows(client)
    built = []
    real = tr.day_moves
    monkeypatch.setattr(tr, 'day_moves', lambda *a: built.append(a[0]) or real(*a))
    _rows(client)
    assert built == []                                                    # ничего не изменилось — кэш
    _ingest(st, days['who'], _tare(days['sb'], [(CRATE, 1)], '13:00:00'))
    _, rows = _rows(client)
    assert built == [PAST] and rows[(11, CRATE)]['balance'] == 0.0      # пересчитан только изменившийся день


def test_partial_without_erp_links_is_by_weight(client, days, erp):
    def down():
        raise ErpError('нет связи')
    erp['links'] = down
    body, rows = _rows(client)
    assert body['links_failed'] is True
    share = 190 / 210                                                      # вода — 190 из 210 кг, стаканы не взяли
    assert rows[(10, BOTTLE)]['went'] == round(10 + 10 * share, 2) and rows[(10, BOTTLE)]['approx'] is True
    assert rows[(11, BOTTLE)]['approx'] is False                          # полная доставка — вся тара, без связей


def test_opening_as_of_cuts_days_and_history(client, st, days, erp):
    assert client.post(f'{API}/opening', json={'customer_id': 10, 'tare_id': BOTTLE, 'qty': 50, 'as_of': '30.09.2026'}
                       ).get_json() == {'success': True}
    _, rows = _rows(client)
    r = rows[(10, BOTTLE)]
    assert (r['opening'], r['as_of'], r['went'], r['returned'], r['balance']) == (50.0, DAY0, 10.0, 7.0, 53.0)
    h = client.get(f'{API}/history?customer=10').get_json()
    assert h['success'] and h['balance'] == {BOTTLE: 53.0}
    assert [(d['date'], d['went'], d['returned'], d['counted'], d['balance'], d['docs'], d['cars']) for d in h['days']] == [
        (PAST, {BOTTLE: 10.0}, {BOTTLE: 7.0}, {BOTTLE: True}, {BOTTLE: 53.0}, ['N501'], ['CAR1']),
        (DAY0, {BOTTLE: 10.0}, {}, {BOTTLE: False}, {}, ['N500'], ['CAR1'])]   # день as_of — уже в остатке
    assert h['openings'][0]['qty'] == 50.0 and h['openings'][0]['updated_by'] is None
    # магазин без движения — строка остатка; удаление остатка — снова с первого дня
    assert client.post(f'{API}/opening', json={'customer_id': 12, 'tare_id': CRATE, 'qty': 2, 'as_of': PAST}
                       ).status_code == 200
    _, rows = _rows(client)
    assert (rows[(12, CRATE)]['balance'], rows[(12, CRATE)]['went']) == (2.0, 0.0)
    assert client.post(f'{API}/opening', json={'customer_id': 10, 'tare_id': BOTTLE, 'qty': None}).status_code == 200
    _, rows = _rows(client)
    assert (rows[(10, BOTTLE)]['opening'], rows[(10, BOTTLE)]['balance']) == (None, 13.0)


@pytest.mark.parametrize('body, error', [
    ({'customer_id': 999, 'tare_id': BOTTLE, 'qty': 1, 'as_of': PAST}, 'Խանութը չի գտնվել'),
    ({'customer_id': '10', 'tare_id': BOTTLE, 'qty': 1, 'as_of': PAST}, 'Խանութը չի գտնվել'),
    ({'customer_id': 10, 'tare_id': 'keg', 'qty': 1, 'as_of': PAST}, 'Տարայի այդպիսի տեսակ չկա'),
    ({'customer_id': 10, 'tare_id': 'erp:5', 'qty': 1, 'as_of': PAST}, 'Տարայի այդպիսի տեսակ չկա'),
    ({'customer_id': 10, 'tare_id': BOTTLE, 'qty': -1, 'as_of': PAST}, 'Քանակը'),
    ({'customer_id': 10, 'tare_id': BOTTLE, 'qty': 1, 'as_of': '2026-10-03'}, 'Ամսաթիվը'),
])
def test_opening_bad_input(client, st, days, body, error):
    r = client.post(f'{API}/opening', json=body)
    assert r.status_code == 400 and error in r.get_json()['error']
    assert st.store.tare_openings() == []


def test_tare_mark_of_stop_from_other_date_counts_at_its_store(client, st, days):
    """Отметка тары на точку, которой нет в снимках этой даты (точка вчерашнего снимка) — у магазина точки."""
    _ingest(st, days['who'], _tare(days['s0'], [(BOTTLE, 2)], '15:00:00'))   # дата PAST, точка — из снимка DAY0
    _, rows = _rows(client)
    assert rows[(10, BOTTLE)]['returned'] == 9.0


# ============================== импорт начальных остатков ==============================

def test_import_preview_errors_and_all_or_nothing(client, st, days, erp):
    st.store.save_tare_custom(None, 'Կեգ 30լ', True, 'admin')
    rows = [{'row': 2, 'code': 'C10', 'tare': 'շիշ 19լ', 'qty': '40', 'as_of': '30.09.2026'},
            {'row': 3, 'code': 'C77', 'tare': 'Կեգ 30լ', 'qty': 2, 'as_of': '2026-10-01'},   # не в снимках — из ERP
            {'row': 4, 'code': 'C404', 'tare': 'erp:900', 'qty': 1, 'as_of': '2026-10-01'},
            {'row': 5, 'code': 'C10', 'tare': 'erp:900', 'qty': 1, 'as_of': '2026-10-01'},
            {'row': 6, 'code': 'C11', 'tare': 'Տակառ', 'qty': 'x', 'as_of': '2030-01-01'},
            {'row': 7, 'code': '', 'tare': 'Արկղ', 'qty': 1, 'as_of': '2026-10-01'}]
    body = client.post(f'{API}/import', json={'rows': rows}).get_json()
    assert body['success'] and body['applied'] is False
    assert [(e['row'], e['field']) for e in body['errors']] == [
        (4, 'code'), (5, 'tare'), (6, 'tare'), (6, 'qty'), (6, 'as_of'), (7, 'code')]
    assert 'Այդ կոդով խանութ չի գտնվել' in body['errors'][0]['error'] and '2-րդ' in body['errors'][1]['error']
    assert [(x['customer_id'], x['tare_id'], x['qty'], x['as_of'], x['name']) for x in body['rows']] == [
        (10, BOTTLE, 40.0, DAY0, 'Խանութ 10'), (77, 'custom:1', 2.0, PAST, 'Նոր խանութ')]
    assert ('codes', ['C404', 'C77']) in erp['calls']                     # ERP — только для неизвестных кодов
    r = client.post(f'{API}/import', json={'rows': rows, 'apply': True})
    assert r.status_code == 400 and r.get_json()['errors'] and st.store.tare_openings() == []   # ничего не записано
    good = rows[:2]
    r = client.post(f'{API}/import', json={'rows': good, 'apply': True})
    assert r.status_code == 200 and r.get_json()['applied'] is True
    saved = {(o['customer_id'], o['tare_id']): o for o in st.store.tare_openings()}
    assert saved[(77, 'custom:1')]['code'] == 'C77' and saved[(10, BOTTLE)]['qty'] == 40.0
    _, balance = _rows(client)
    assert balance[(10, BOTTLE)]['balance'] == 40.0 + 10 - 7 and balance[(77, 'custom:1')]['name'] == 'Նոր խանութ'
    again = client.post(f'{API}/import', json={'rows': [{**good[0], 'qty': 41}]}).get_json()
    assert again['rows'][0]['old'] == {'qty': 40.0, 'as_of': DAY0}        # предпросмотр показывает замену


def test_import_bad_bodies(client, days):
    for body in ({}, {'rows': []}, {'rows': 'x'}, {'rows': [{}] * (tr.IMPORT_ROWS_MAX + 1)}):
        assert client.post(f'{API}/import', json=body).status_code == 400
    r = client.post(f'{API}/import', json={'rows': ['junk', 5]})
    assert r.status_code == 200 and len(r.get_json()['errors']) == 8      # по 4 поля на строку


def test_csv_export_escapes_formulas(client, st, days):
    st.store.save_tare_openings([cstore.TareOpening(10, BOTTLE, 50.0, DAY0, 'C10', '=HYPERLINK("x")')], 'qa')
    r = client.get(f'{API}.csv')
    assert r.status_code == 200 and r.mimetype == 'text/csv' and 'attachment' in r.headers['Content-Disposition']
    text = r.get_data().decode('utf-8')
    assert text.startswith('﻿')
    table = list(csv.reader(io.StringIO(text[1:]), delimiter=';'))
    assert table[0][:3] == ['Խանութի կոդ', 'Խանութ', 'Մենեջեր'] and len(table) == 1 + 4
    first = table[1]
    assert first[0] == 'C10' and first[4] == 'Շիշ 19լ' and first[-1] == '53.0'
    assert first[1] == 'Խանութ 10'                                        # имя — из последнего дня, не из записи
    st.store.save_tare_openings([cstore.TareOpening(5, BOTTLE, 1.0, DAY0, '-1', '=cmd()')], 'qa')
    table = list(csv.reader(io.StringIO(client.get(f'{API}.csv').get_data().decode('utf-8')[1:]), delimiter=';'))
    row = next(x for x in table if x[1].endswith('cmd()'))
    assert row[:2] == ["'-1", "'=cmd()"]


# ============================== схема 8 → 9 ==============================

def test_migration_v8_to_v9_keeps_rows_and_adds_tare_opening(tmp_path, st, days):
    """Копия базы схемы 8 (как сейчас на ПК и CT115: без tare_opening) → 9: все строки на месте, таблица — как у новой
    базы, баланс считается; повторное открытие ничего не меняет."""
    path = str(tmp_path / 'v8.db')
    with closing(sqlite3.connect(st.store.path)) as src, closing(sqlite3.connect(path)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE tare_opening')
        conn.execute("UPDATE meta SET value = '8' WHERE key = 'schema_version'")
        conn.commit()
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name NOT LIKE 'sqlite_%'")]
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables if t != 'meta'}
    migrated = Store(path)
    assert migrated.tare_openings() == []
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION) == '9'
        assert {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in counts} == counts
        got = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'tare_opening'").fetchone()[0]
    with closing(sqlite3.connect(st.store.path)) as conn:
        assert got == conn.execute("SELECT sql FROM sqlite_master WHERE name = 'tare_opening'").fetchone()[0]
    migrated.save_tare_openings([cstore.TareOpening(10, BOTTLE, 3.0, DAY0, 'C10', 'X')], 'qa')
    assert Store(path).tare_openings()[0]['qty'] == 3.0 and Store(path).tare_day_keys().keys() == {DAY0, PAST}


def test_migration_v8_to_v9_is_one_transaction(tmp_path, monkeypatch):
    path = str(tmp_path / 'v8.db')
    Store(path).list_drivers()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE tare_opening')
        conn.execute("UPDATE meta SET value = '8' WHERE key = 'schema_version'")
        conn.commit()

    def boom(conn):
        raise sqlite3.OperationalError('сбой посреди миграции')
    monkeypatch.setitem(cstore._MIGRATIONS, 8, (*cstore._MIGRATIONS[8], boom))
    with pytest.raises(cstore.StoreError):
        Store(path).list_drivers()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == '8'
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'tare_opening'").fetchone() is None


# ============================== страница ==============================

def test_tare_page_renders(app, client):
    app.add_url_rule('/logout', 'logout', lambda: '')     # base_v2.html ссылается на выход дашборда
    r = client.get('/courier/tare')
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    html = r.get_data(as_text=True)
    assert 'id="ctPage"' in html and 'js/courier_tare.js?v=1' in html and 'css/courier_tare.css?v=1' in html
    assert 'Տարա' in html and 'lang="hy"' in html

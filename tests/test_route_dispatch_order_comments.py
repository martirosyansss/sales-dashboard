# -*- coding: utf-8 -*-
"""«Развоз»: комментарий менеджера к заказу в ERP (DOCUMENTS.fCOMMENT) виден диспетчеру в плане и в «Նախորդ օրերից».
Только показ: заказ с комментарием и без — один и тот же план, равенство и хеш заказа его не видят. Синтетические данные,
без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_order_comments.py -q
"""
import json
import re
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import erp  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _isn, client  # noqa: E402,F401

DAY = '2026-10-01'
OLD = date(2026, 9, 28)
TEXT = 'տանել հինգշաբթի <b>300֏</b>'


@pytest.fixture(autouse=True)
def _before_day(monkeypatch):
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 18, 0))
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 9, 30, 18, 0))


def _row(i, comment='', **kw):
    return (_isn(i), f'N{i}', datetime(2026, 9, 30), 101, 1, '', 100, 10, None, 0, None, '', comment)


def test_loader_maps_and_trims_comment(monkeypatch):
    rows = [_row(1, '  ' + TEXT + ' \n'), _row(2, None), _row(3, '   '), _row(4)[:12]]
    monkeypatch.setattr(erp, '_select', lambda conn, sql, params=(): rows)
    got = erp.dispatch_orders(None, date(2026, 9, 1), date(2026, 10, 1))
    assert [o.comment for o in got] == [TEXT, '', '', '']
    got = erp.dispatch_orders_by_isns(None, [_isn(1), _isn(2)])
    assert [o.comment for o in got] == [TEXT, '', '', '']


def test_query_reads_comment_as_last_column():
    erp.check_sql(erp.SQL_DISPATCH_ORDERS)
    head, _, _ = erp.SQL_DISPATCH_ORDERS.partition('FROM ORDERS')
    assert head.rstrip().endswith('cr.comment')
    assert 'd.fCOMMENT' in erp.SQL_DISPATCH_ORDERS and 'ORDER BY' not in erp.SQL_DISPATCH_ORDERS


def test_comment_is_outside_equality_hash_and_repr():
    a = _dorder(1, 101, 10.0)
    b = replace(a, comment=TEXT)
    assert a == b and hash(a) == hash(b) and repr(a) == repr(b)
    assert len({a, b}) == 1
    assert views._orders_sig(dp.DispatchData((a,), {}, {}, {}, datetime(2026, 9, 30))) == \
        views._orders_sig(dp.DispatchData((b,), {}, {}, {}, datetime(2026, 9, 30)))


def _orders(comments):
    c = comments
    return [replace(_dorder(1, 101, 400.0), comment=c.get(1, '')), replace(_dorder(2, 102, 300.0, agent=2), comment=c.get(2, '')),
            replace(_dorder(3, 101, 50.0), comment=c.get(3, '')),
            replace(_dorder(6, 104, 70.0, agent=2, day=OLD), comment=c.get(6, ''))]


def _build(client):
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['plan'] is not None, d
    return d


def _stops(d):
    return {s['customer_id']: s for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}


def _strip(node):
    """Ответ без ключей comment: всё остальное должно совпасть до байта."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k != 'comment'}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def test_json_carries_comment_for_stops_and_backlog(client):
    _dispatch_setup(client, _orders({1: TEXT, 6: 'Ֆակտուրա տալ'}))
    d = _build(client)
    stop = _stops(d)[101]
    by_isn = {o['isn']: o for o in stop['orders']}
    assert by_isn[_isn(1)]['comment'] == TEXT
    assert 'comment' not in by_isn[_isn(3)]                              # без комментария ключа нет
    assert [o['comment'] for o in d['backlog'] if o['isn'] == _isn(6)] == ['Ֆակտուրա տալ']
    assert all('comment' not in o for o in _stops(d)[102]['orders'])


_BUILT = {}


@pytest.mark.parametrize('noted', [False, True])
def test_build_plan_with_and_without_comments(client, noted):
    """Каждый вариант — своя чистая база (фикстура client); сравнение — в следующем тесте."""
    state = client.application.extensions['route_optimizer']
    _dispatch_setup(client, _orders({1: TEXT, 2: 'a', 3: 'b', 6: 'c'} if noted else {}))
    d = _build(client)
    _BUILT[noted] = (json.dumps(state.store.load_dispatch(DAY)[0], sort_keys=True, ensure_ascii=False),
                     json.dumps(_strip(d), sort_keys=True, ensure_ascii=False), 'comment' in json.dumps(d, ensure_ascii=False))


def test_plan_is_byte_identical_with_and_without_comments():
    assert set(_BUILT) == {False, True}
    (plain_draft, plain_page, plain_has), (noted_draft, noted_page, noted_has) = _BUILT[False], _BUILT[True]
    assert plain_draft == noted_draft                                   # сохранённый план: рейсы, прогноз, подписи
    assert plain_page == noted_page                                     # ответ страницы без самих комментариев
    assert noted_has and not plain_has


def test_editing_comment_in_erp_does_not_change_plan_state(client):
    """Комментарий поправили в ERP: страница читает день заново — ревизия, подписи и «изменён» те же."""
    _dispatch_setup(client, _orders({1: 'old'}))
    before = _build(client)
    _dispatch_setup(client, _orders({1: 'new', 2: 'x'}))
    after = client.get(f'/api/routes/dispatch?date={DAY}&refresh=1').get_json()
    for key in ('rev', 'orders_sig', 'crew_sig', 'changed', 'unsent', 'approved', 'sent'):
        assert before.get(key) == after.get(key), key
    assert _strip(before['plan']) == _strip(after['plan'])


# --- страница: тот же код, что в браузере; комментарий — только textContent -------------------------------------------

JS = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')


def _body(name):
    m = re.search(r'\n    function ' + name + r'\(.*?\n    }\n', JS, re.S)
    assert m, name
    return m.group(0)


def test_page_shows_comment_through_textcontent_only():
    notes = _body('orderNotes')
    assert 'new Set' in notes and 'comment' in notes
    for name in ('stopItem', 'stopRow'):
        body = _body(name)
        assert 'orderNotes(' in body
        shown = body[body.index('orderNotes('):]
        assert re.search(r"nt\.textContent = note;", shown)
    back = _body('backlogRow')
    assert re.search(r"if \(o\.comment\) main\.append\(span\(o\.comment\)\)", back)
    assert not re.search(r'innerHTML[^\n]*(comment|note\b)', notes + _body('stopItem') + _body('stopRow') + back)


def test_asset_version_bumped():
    html = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    assert 'routes_dispatch.js\') }}?v=r14-20261110-geo-marks' in html

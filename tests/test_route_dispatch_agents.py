# -*- coding: utf-8 -*-
"""«Развоз»: фильтр «Մենեջերներ» — везём заказы только выбранных менеджеров (Draft.agents_off). До первой сборки выбор
приходит со сборкой (build: agents_off), после — правкой {"action": "agents", "off": [...]}; заказы снятых менеджеров
не попадают ни в точки дня, ни в «новые после сборки», ни в заказы машины приложения водителя. Синтетические данные,
без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_agents.py -q
"""
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import routes_link as rl  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from test_route_optimizer import (EAST, FORD, HOWO, _dispatch_setup, _dorder, _dp_ctx, _dp_stops, _isn,  # noqa: E402,F401
                                  client)

DAY = '2026-10-01'


def _stop_orders(plan):
    """Клиент → заказы (fISN) в рейсах и «ещё не в рейсах»."""
    out = {}
    for s in [s for t in plan['trucks'] for tr in t['trips'] for s in tr['stops']] + plan['unassigned']:
        out.setdefault(s['customer_id'], set()).update(o['isn'] for o in s['orders'])
    return out


# ============================== черновик и правка ==============================

def test_draft_agents_off_roundtrip_and_garbage():
    d = dp.Draft(['CAR1'], agents_off={2, 7})
    raw = json.loads(json.dumps(d.to_json()))
    assert raw['agents_off'] == [2, 7] and dp.Draft.from_json(raw) == d
    # старый черновик без поля — фильтра нет; битые элементы пропускаются, остальные менеджеры остаются снятыми
    assert dp.Draft.from_json({'trucks': []}).agents_off == set()
    for junk in ('2', [True], [2.0], None):
        assert dp.Draft.from_json({'agents_off': junk}).agents_off == set()
    assert dp.Draft.from_json({'agents_off': [2, 'x', True, 7]}).agents_off == {2, 7}
    assert len(dp.Draft.from_json({'agents_off': list(range(dp.MAX_AGENTS + 9))}).agents_off) == dp.MAX_AGENTS


def test_apply_edit_agents_sets_filter_and_rejects_garbage():
    stops, orders = _dp_stops(EAST)
    ctx = _dp_ctx()
    draft = dp.build(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now')
    ids = {o.isn for o in orders}
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'agents', 'off': [3, 5]}, ids)
    assert draft.agents_off == {3, 5}
    assert dp.apply_edit(ctx, stops, draft, {'action': 'agents', 'off': []}, ids).agents_off == set()
    for bad in ({'action': 'agents'}, {'action': 'agents', 'off': [1, 'x']}, {'action': 'agents', 'off': 2},
                {'action': 'agents', 'off': [False]}):
        with pytest.raises(dp.DispatchError, match='Список менеджеров'):
            dp.apply_edit(ctx, stops, draft, bad, ids)


def test_rebuild_keeps_agents_off():
    stops, _ = _dp_stops(EAST)
    ctx = _dp_ctx()
    old = dp.Draft(agents_off={9})
    assert dp.build(ctx, stops, old, [HOWO.car_code], 'now').agents_off == {9}


# ============================== страница «Развоз» ==============================

def test_api_agents_filter_before_and_after_build(client):
    # менеджер 1: 101 и 999 (без точки); менеджер 2: 102, 104 и половина заказов 102 общего магазина с менеджером 1
    orders = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2),
              _dorder(4, 999, 50.0), _dorder(5, 102, 80.0)]
    _dispatch_setup(client, orders)
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert [(a['agent_id'], a['code'], a['count'], a['kg'], a['off']) for a in d['agents']] == \
        [(1, 'A001', 3, 530, False), (2, 'A002', 2, 1500, False)]
    assert d['agents_off'] == [] and (d['orders']['agents_off'], d['orders']['agents_off_kg']) == (0, 0)

    # до сборки фильтр приходит со сборкой: везём только менеджера 1
    bad = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1'], 'agents_off': ['2']})
    assert bad.status_code == 400 and bad.get_json()['errors'] == {'agents_off': 'ожидался список менеджеров'}
    r = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2'], 'agents_off': [2]})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d['agents_off'] == [2] and [a['off'] for a in d['agents']] == [False, True]
    assert (d['orders']['count'], d['orders']['kg'], d['orders']['agents_off'], d['orders']['agents_off_kg']) == (3, 530, 2, 1500)
    assert d['orders']['excluded'] == 0                       # фильтр — не «не везём сегодня»
    # общий магазин 102 остаётся — только с заказом менеджера 1; 104 (только менеджер 2) — нигде
    assert _stop_orders(d['plan']) == {101: {_isn(1)}, 102: {_isn(5)}}
    assert d['new_since_build'] == {'count': 0, 'kg': 0, 'revenue': 0}
    status = client.get('/api/routes/dispatch/status?date=' + DAY).get_json()
    assert status['orders']['count'] == 3

    # пересборка без поля — фильтр прежний
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['agents_off'] == [2] and 104 not in _stop_orders(d['plan'])

    # после сборки — правкой: вернуть менеджера 2 → его заказы «ещё не в рейсах»
    bad = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'agents', 'off': 'x'})
    assert bad.status_code == 400 and 'Список менеджеров' in bad.get_json()['error']
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'agents', 'off': []})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d['agents_off'] == [] and d['orders']['count'] == 5
    assert {u['customer_id'] for u in d['plan']['unassigned']} == {104}
    assert _stop_orders(d['plan'])[102] == {_isn(2), _isn(5)}
    # снять менеджера 1: из рейсов уходят 101 и его половина 102
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'agents', 'off': [1]}).get_json()
    assert _stop_orders(d['plan']) == {102: {_isn(2)}, 104: {_isn(3)}}
    # и из сохранённого черновика (его читают обучение и приложение водителя) — рейсы и прогноз без 101
    state = client.application.extensions['route_optimizer']
    stored = dp.Draft.from_json(state.store.load_dispatch(DAY)[0])
    assert all(101 not in t.stops for t in stored.trips)
    assert all(c != 101 for t in stored.prediction['trucks'].values() for tr in t['trips'] for c, _ in tr['stops'])
    # заказы дня в «не везём сегодня» — не в счёте менеджера
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'exclude', 'order': _isn(3)}).get_json()
    assert [(a['agent_id'], a['count']) for a in d['agents']] == [(1, 3), (2, 1)]
    assert (d['orders']['agents_off'], d['orders']['excluded']) == (3, 1)
    # «Начать заново» снимает и фильтр
    d = client.post('/api/routes/dispatch/reset', json={'date': DAY}).get_json()
    assert d['agents_off'] == [] and d['orders']['count'] == 5


def test_new_orders_of_filtered_manager_are_not_new_since_build(client):
    orders = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)]
    _dispatch_setup(client, orders)
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1'], 'agents_off': [2]}).get_json()
    assert d['new_since_build']['count'] == 0
    # после сборки пришёл заказ снятого менеджера 2 и заказ менеджера 1 — «новый» только второй
    state = client.application.extensions['route_optimizer']
    more = [*orders, _dorder(3, 104, 500.0, agent=2), _dorder(4, 104, 70.0)]
    data = replace(state.dispatch_loader(None, None, None), orders=tuple(more))
    state.dispatch_loader = lambda since, until, day: data
    state.dispatch_cache.clear()                                        # как будто прошло 5 минут (TTL кэша)
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert d['new_since_build'] == {'count': 1, 'kg': 70, 'revenue': 10000}
    assert [(a['agent_id'], a['count'], a['off']) for a in d['agents']] == [(1, 2, False), (2, 2, True)]


# ============================== приложение водителя ==============================

def test_courier_pick_orders_skips_filtered_managers():
    d = date(2026, 10, 2)
    isn = ['0000000{}-0000-0000-0000-000000000000'.format(i) for i in range(1, 4)]
    orders = [dp.DispatchOrder(isn[0], 'N1', date(2026, 10, 1), 2, 7, '', 1000.0, 10.0, None),
              dp.DispatchOrder(isn[1], 'N2', date(2026, 10, 1), 2, 8, '', 1000.0, 10.0, None),
              dp.DispatchOrder(isn[2], 'N3', date(2026, 9, 25), 3, 8, '', 1000.0, 10.0, None)]
    plan = rl.RoutesView(plan_exists=True, trips=(('A', (2, 3)),), added=frozenset({isn[2]}))
    assert [o.isn for o in rl.pick_orders(orders, d, plan, 'A')] == isn
    # магазин 2 в рейсе ради заказа менеджера 7: заказ менеджера 8 там же и его заказ прошлых дней водитель не везёт
    off = rl.RoutesView(plan_exists=True, trips=(('A', (2, 3)),), added=frozenset({isn[2]}), agents_off=frozenset({8}))
    assert [o.isn for o in rl.pick_orders(orders, d, off, 'A')] == [isn[0]]

# -*- coding: utf-8 -*-
"""«Развоз» (№69): чьи заказы везём — правило в настройках (dispatch_agents_off), чтобы не отмечать каждый день.
Правило — для дней без плана: точки дня, счётчики и список менеджеров «Развоза», первая сборка, приложение водителя.
У дня с планом — свой выбор (Draft.agents_off), правило настроек его не меняет. Пустое правило — всё как раньше.
Синтетические данные, без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_agents_default.py -q
"""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import routes_link as rl  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _isn, client  # noqa: E402,F401

DAY = '2026-10-01'
NEXT = '2026-10-02'
# менеджер 1: 101 и 999 (без точки); менеджер 2: 102 и 104
ORDERS = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2),
          _dorder(4, 999, 50.0)]


def _rule(client, off):
    r = client.post('/api/routes/settings', json={'settings': {'dispatch_agents_off': off}})
    assert r.status_code == 200, r.get_json()


def _plan_customers(plan):
    return {s['customer_id'] for t in plan['trucks'] for tr in t['trips'] for s in tr['stops']} \
        | {s['customer_id'] for s in plan['unassigned']}


def _state(client):
    return client.application.extensions['route_optimizer']


# ============================== настройка ==============================

def test_setting_default_and_validation():
    assert st.DEFAULT_SETTINGS['dispatch_agents_off'] == []
    assert st.MAX_AGENTS_OFF == dp.MAX_AGENTS
    out, errors = st.validate_settings({**st.DEFAULT_SETTINGS, 'dispatch_agents_off': [7, 2, 7]}, None)
    assert errors == {} and out['dispatch_agents_off'] == [2, 7]          # повторы схлопнуты, по возрастанию
    # нет ключа (база до №69) — пусто
    raw = dict(st.DEFAULT_SETTINGS)
    del raw['dispatch_agents_off']
    out, errors = st.validate_settings(raw, None)
    assert errors == {} and out['dispatch_agents_off'] == []
    for bad in ('2', 2, None, [1, '2'], [True], [False], [2.0], [0], [-1], [2 ** 31], [10 ** 30],
                list(range(1, st.MAX_AGENTS_OFF + 2))):
        _, errors = st.validate_settings({**st.DEFAULT_SETTINGS, 'dispatch_agents_off': bad}, None)
        assert set(errors) == {'dispatch_agents_off'}, bad
    assert st.validate_settings({**st.DEFAULT_SETTINGS, 'dispatch_agents_off': list(range(1, st.MAX_AGENTS_OFF + 1))},
                                None)[1] == {}
    assert st.validate_settings({**st.DEFAULT_SETTINGS, 'dispatch_agents_off': [1, 2 ** 31 - 1]},
                                None)[0]['dispatch_agents_off'] == [1, 2 ** 31 - 1]


def test_agents_off_of_prefers_draft():
    s = {'dispatch_agents_off': [2, 5]}
    assert dp.agents_off_of(None, s) == {2, 5}
    assert dp.agents_off_of(None, {}) == set()
    assert dp.agents_off_of(dp.Draft(agents_off={9}), s) == {9}
    assert dp.agents_off_of(dp.Draft(), s) == set()                         # у дня с планом — его выбор, и пустой


def test_settings_api_saves_rule_and_lists_managers(client):
    d = client.get('/api/routes/settings').get_json()
    assert d['settings']['dispatch_agents_off'] == []
    assert [(a['agent_id'], a['code'], a['name']) for a in d['dispatch_agents']] == \
        [(1, 'A001', 'Менеджер 1'), (2, 'A002', 'Менеджер 2')]
    bad = client.post('/api/routes/settings', json={'settings': {'dispatch_agents_off': ['x']}})
    assert bad.status_code == 400 and 'settings.dispatch_agents_off' in bad.get_json()['errors']
    # снятый правилом менеджер, которого нет среди работающих, — в списке, чтобы его можно было вернуть
    _rule(client, [77, 2, 2])
    d = client.get('/api/routes/settings').get_json()
    assert d['settings']['dispatch_agents_off'] == [2, 77]
    assert [a['agent_id'] for a in d['dispatch_agents']] == [1, 2, 77]
    assert d['dispatch_agents'][2] == {'agent_id': 77, 'code': '', 'name': '', 'area': ''}


# ============================== «Развоз» ==============================

def test_day_without_plan_uses_rule(client):
    _dispatch_setup(client, ORDERS)
    _rule(client, [2])
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert d['agents_off'] == [2] and d['agents_from_settings'] is True
    assert [(a['agent_id'], a['off']) for a in d['agents']] == [(1, False), (2, True)]
    assert (d['orders']['count'], d['orders']['kg'], d['orders']['agents_off'], d['orders']['agents_off_kg']) == \
        (2, 450, 2, 1500)
    assert d['orders']['customers'] == 2 and d['plan'] is None
    assert client.get('/api/routes/dispatch/status?date=' + DAY).get_json()['orders']['count'] == 2
    assert _state(client).store.load_dispatch(DAY) is None                  # правило не создаёт план


def test_first_build_without_payload_starts_from_rule(client):
    _dispatch_setup(client, ORDERS)
    _rule(client, [2])
    r = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d['agents_off'] == [2] and 'agents_from_settings' not in d
    assert _plan_customers(d['plan']) == {101}
    assert dp.Draft.from_json(_state(client).store.load_dispatch(DAY)[0]).agents_off == {2}
    assert d['new_since_build']['count'] == 0


def test_build_payload_overrides_rule(client):
    _dispatch_setup(client, ORDERS)
    _rule(client, [2])
    # страница прислала выбор, совпадающий с правилом, — тот же план
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1'], 'agents_off': [2]}).get_json()
    assert d['agents_off'] == [2] and _plan_customers(d['plan']) == {101}
    # другой день: логист вернул всех до сборки — этот день везёт и менеджера 2
    d = client.post('/api/routes/dispatch/build', json={'date': NEXT, 'trucks': ['CAR1'], 'agents_off': []}).get_json()
    assert d['agents_off'] == [] and dp.Draft.from_json(_state(client).store.load_dispatch(NEXT)[0]).agents_off == set()


def test_day_with_plan_keeps_its_choice_when_rule_changes(client):
    _dispatch_setup(client, ORDERS)
    _rule(client, [2])
    client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']})
    _rule(client, [1])
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert d['agents_off'] == [2] and _plan_customers(d['plan']) == {101}
    # день без плана — уже по новому правилу
    d = client.get('/api/routes/dispatch?date=' + NEXT).get_json()
    assert d['agents_off'] == [1] and d['agents_from_settings'] is True
    # «Начать заново» — день снова без плана, правило настроек
    d = client.post('/api/routes/dispatch/reset', json={'date': DAY}).get_json()
    assert d['agents_off'] == [1] and d['agents_from_settings'] is True


def test_build_with_stale_page_choice_stores_what_page_showed(client):
    # страница открыта при правиле [2]; правило сменили на [1] в другой вкладке — сборка сохраняет выбор страницы
    _dispatch_setup(client, ORDERS)
    _rule(client, [2])
    shown = client.get('/api/routes/dispatch?date=' + DAY).get_json()['agents_off']
    _rule(client, [1])
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2'], 'agents_off': shown}).get_json()
    assert d['agents_off'] == [2] and _plan_customers(d['plan']) == {101}
    assert dp.Draft.from_json(_state(client).store.load_dispatch(DAY)[0]).agents_off == {2}


def test_status_on_planned_day_uses_draft_after_rule_change(client):
    _dispatch_setup(client, ORDERS)
    _rule(client, [2])
    client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']})
    _rule(client, [])
    status = client.get('/api/routes/dispatch/status?date=' + DAY).get_json()
    assert (status['orders']['count'], status['orders']['kg']) == (2, 450)            # менеджер 2 снят планом дня
    client.post('/api/routes/dispatch/reset', json={'date': DAY})                    # без плана — по правилу (пусто)
    assert client.get('/api/routes/dispatch/status?date=' + DAY).get_json()['orders']['count'] == 4


def test_edit_before_build_creates_no_draft(client):
    # правки — только поверх собранного плана: до сборки ничего не сохраняется, день остаётся на правиле
    _dispatch_setup(client, ORDERS)
    _rule(client, [2])
    for edit in ({'action': 'exclude', 'order': _isn(1)}, {'action': 'agents', 'off': []}):
        r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': 0, **edit})
        assert r.status_code == 409
    assert _state(client).store.load_dispatch(DAY) is None
    assert client.get('/api/routes/dispatch?date=' + DAY).get_json()['agents_off'] == [2]


def test_empty_rule_changes_nothing(client):
    _dispatch_setup(client, ORDERS)
    before = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    _rule(client, [])
    after = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert after == before and 'agents_from_settings' not in after and after['agents_off'] == []
    assert after['orders']['count'] == 4
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['agents_off'] == [] and _plan_customers(d['plan']) == {101, 102, 104}


# ============================== приложение водителя ==============================

def test_courier_view_without_plan_uses_rule(client):
    _dispatch_setup(client, ORDERS)
    state = _state(client)
    assert rl.routes_view(state, date(2026, 10, 1)).agents_off == frozenset()
    _rule(client, [2])
    view = rl.routes_view(state, date(2026, 10, 1))
    assert view.plan_exists is False and view.agents_off == frozenset({2})
    orders = [_dorder(1, 101, 400.0, car='CAR1'), _dorder(2, 102, 300.0, agent=2, car='CAR1')]
    assert [o.isn for o in rl.pick_orders(orders, date(2026, 10, 1), view, 'CAR1')] == [_isn(1)]
    # план есть — его выбор, а не правило
    client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1'], 'agents_off': []})
    assert rl.routes_view(state, date(2026, 10, 1)).agents_off == frozenset()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))

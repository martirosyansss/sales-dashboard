# -*- coding: utf-8 -*-
"""Телефон менеджера у водителя (запрос владельца 09.10): настройка «Մենեջերների հեռախոսները» (agent_phones) →
вид «Маршрутов» для терминала (RoutesView.agent_phones) → /day: у точки agent_phone (контракт v1.9 §13).

- Проверка настройки: нормализация номера, ошибки по ключу agent_phones.<id>, пустой номер — записи нет, ключи-строки
  и целые, предел; база без ключа — пусто; сохранённое читается обратно.
- RoutesView: телефоны из настроек и без плана, и с планом; раздела нет — пусто.
- /day: agent_phone у точки (задан / null / у менеджера без номера); версия рейса плана от телефонов не зависит; демо.
- Страница настроек: карточка и ?v=43.

Синтетические данные, без ERP; базы — временные.

Запуск из корня проекта:  python -m pytest tests/test_agent_phone.py -q
"""
import sqlite3
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import day as dy  # noqa: E402
from courier import routes_link as rl  # noqa: E402
from courier.store import Store as CourierStore  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, client  # noqa: E402,F401

DAY = '2026-10-01'
BAD = 'հեռախոսը՝ թվերով, օրինակ +37491123456 կամ 091123456'
ORDERS = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2)]


def _check(phones):
    return st.validate_settings({**st.DEFAULT_SETTINGS, 'agent_phones': phones}, None)


def _state(client):
    return client.application.extensions['route_optimizer']


# ============================== настройка ==============================

def test_default_and_missing_key():
    assert st.DEFAULT_SETTINGS['agent_phones'] == {}
    assert st.MAX_AGENT_PHONES == st.MAX_AGENTS_OFF
    raw = dict(st.DEFAULT_SETTINGS)
    del raw['agent_phones']                                    # база до этой настройки
    out, errors = st.validate_settings(raw, None)
    assert errors == {} and out['agent_phones'] == {}


def test_normalizes_and_sorts_by_agent_id():
    out, errors = _check({'12': ' +374 (91) 12-34.56 ', 3: '091 123 456', '7': '010 000 001'})
    assert errors == {}
    assert out['agent_phones'] == {'3': '091123456', '7': '010000001', '12': '+37491123456'}
    assert list(out['agent_phones']) == ['3', '7', '12']       # по числу, не по строке
    # то же значение ещё раз — то же (сохранённое проходит проверку чтения базы)
    again, errors = _check(out['agent_phones'])
    assert errors == {} and again['agent_phones'] == out['agent_phones']


def test_empty_value_removes_entry():
    out, errors = _check({'1': '', '2': '   ', '3': None, '4': ' - ( ) ', '5': '091123456'})
    assert errors == {} and out['agent_phones'] == {'5': '091123456'}


@pytest.mark.parametrize('phone', ['abc', '12345', '+3749112345678901', '++37491123456', '37491+123456', '091/123456',
                                   '0911234５6', '091-123-456x', 12345678, ['091123456'], True])
def test_bad_phone_error_keyed_by_agent(phone):
    out, errors = _check({'7': phone, '8': '091123456'})
    assert errors == {'agent_phones.7': BAD} and 'agent_phones' not in out


@pytest.mark.parametrize('phones', [[], 'x', None, {'0': '091123456'}, {'-1': '091123456'}, {'07': '091123456'},
                                    {'1.0': '091123456'}, {' 1': '091123456'}, {'1\n': '091123456'},
                                    {str(2 ** 31): '091123456'}, {2 ** 31: '091123456'}, {0: '091123456'},
                                    {True: '091123456'}, {'abc': '091123456'}])
def test_bad_shape_or_key(phones):
    out, errors = _check(phones)
    assert set(errors) == {'agent_phones'} and 'agent_phones' not in out


def test_limit():
    many = {str(i): '091123456' for i in range(1, st.MAX_AGENT_PHONES + 1)}
    out, errors = _check(many)
    assert errors == {} and len(out['agent_phones']) == st.MAX_AGENT_PHONES
    out, errors = _check({**many, str(st.MAX_AGENT_PHONES + 1): '091123456'})
    assert set(errors) == {'agent_phones'}


def test_api_round_trip_and_reload(client, tmp_path):
    r = client.post('/api/routes/settings', json={'settings': {'agent_phones': {'2': '091 12-34-56', '1': '+37410000009'}}})
    assert r.status_code == 200, r.get_json()
    d = client.get('/api/routes/settings').get_json()
    assert d['settings']['agent_phones'] == {'1': '+37410000009', '2': '091123456'}
    # другой процесс читает ту же базу — проверка чтения (known_groups=None) принимает сохранённое
    assert st.Store(str(tmp_path / 'routes.db')).load().settings['agent_phones'] == {'1': '+37410000009', '2': '091123456'}
    # ошибка — у поля менеджера; ничего не сохранено
    bad = client.post('/api/routes/settings', json={'settings': {'agent_phones': {'2': '12'}}})
    assert bad.status_code == 400 and bad.get_json()['errors'] == {'settings.agent_phones.2': BAD}
    assert client.get('/api/routes/settings').get_json()['settings']['agent_phones']['2'] == '091123456'
    # убрать все номера
    assert client.post('/api/routes/settings', json={'settings': {'agent_phones': {}}}).status_code == 200
    assert client.get('/api/routes/settings').get_json()['settings']['agent_phones'] == {}


def test_db_without_row_loads(client, tmp_path):
    assert client.post('/api/routes/settings', json={'settings': {'agent_phones': {'1': '091123456'}}}).status_code == 200
    path = str(tmp_path / 'routes.db')
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("DELETE FROM settings WHERE key = 'agent_phones'")   # база до этой настройки
    conn.close()
    assert st.Store(path).load().settings['agent_phones'] == {}


# ============================== вид «Маршрутов» для терминала ==============================

def test_routes_view_carries_phones_with_and_without_plan(client):
    assert rl.routes_view(None, date(2026, 10, 1)).agent_phones == {}            # раздела нет
    _dispatch_setup(client, ORDERS)
    state = _state(client)
    assert rl.routes_view(state, date(2026, 10, 1)).agent_phones == {}           # номеров нет
    r = client.post('/api/routes/settings', json={'settings': {'agent_phones': {'2': '091123456'}}})
    assert r.status_code == 200, r.get_json()
    view = rl.routes_view(state, date(2026, 10, 1))
    assert not view.plan_exists and view.agent_phones == {2: '091123456'}        # без плана
    r = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1']})
    assert r.status_code == 200, r.get_json()
    view = rl.routes_view(state, date(2026, 10, 1))
    assert view.plan_exists and view.agent_phones == {2: '091123456'}            # с планом — те же, из настроек


# ============================== /day ==============================

def test_build_stops_agent_phone():
    data, _, _ = dy.demo_data()
    order = [900001, 900002, 900003]
    assert all(s['agent_phone'] is None for s in dy.build_stops(data, order, {}, {}))            # номеров нет
    assert all(s['agent_phone'] is None for s in dy.build_stops(data, order, {}, {}, agent_phones={2: '091123456'}))
    stops = dy.build_stops(data, order, {}, {}, agent_phones={1: '+37491123456'})
    assert [s['agent_phone'] for s in stops] == ['+37491123456'] * 3
    keys = list(stops[0])
    assert keys[keys.index('agent_name') + 1] == 'agent_phone'


def test_day_payload_phone_changes_version_not_plan_version(tmp_path):
    store = CourierStore(str(tmp_path / 'c.db'))
    data, view, loaded = dy.demo_data()
    plan = replace(view, agent_phones={}, plan_exists=True, trips=(('TEST', (900003, 900001, 900002)),))
    a = dy.day_payload(data, plan, store, loaded)
    b = dy.day_payload(data, replace(plan, agent_phones={1: '091123456'}), store, loaded)
    assert [s['agent_phone'] for s in a['stops']] == [None] * 3
    assert [s['agent_phone'] for s in b['stops']] == ['091123456'] * 3
    assert a['version'] != b['version']                                          # точки изменились
    assert [t['plan_version'] for t in a['trips']] == [t['plan_version'] for t in b['trips']]
    assert a['trips'][0]['plan_version'] is not None                             # рейс плана — свой порядок APK цел
    assert [s['stop_id'] for s in a['stops']] == [s['stop_id'] for s in b['stops']]


def test_demo_day_has_manager_phone(tmp_path):
    data, view, loaded = dy.demo_data()
    body = dy.day_payload(data, view, CourierStore(str(tmp_path / 'c.db')), loaded)
    assert {(s['agent_name'], s['agent_phone']) for s in body['stops']} == {('Թեստ մենեջեր', '+37410000009')}
    assert st.AGENT_PHONE_RE.fullmatch(body['stops'][0]['agent_phone'])


# ============================== страница настроек ==============================

def test_settings_page_has_card():
    page = (ROOT / 'templates' / 'routes_settings.html').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'js' / 'routes_settings.js').read_text(encoding='utf-8')
    assert 'id="agentphones"' in page and 'id="rsStAgentPhones"' in page and 'id="rsAgentPhones"' in page
    assert 'Մենեջերների հեռախոսները' in page and 'fa-phone' in page
    assert page.index('id="agents"') < page.index('id="agentphones"') < page.index('id="fleet"')
    assert "routes_settings.js') }}?v=43" in page
    assert "'agents', 'agentphones', 'fleet'" in js and 'renderAgentPhones()' in js
    assert "'settings.agent_phones.' + " in js and 's.agent_phones = phones' in js
    assert "type: 'tel'" in js and "inputmode: 'tel'" in js and 'maxlength: 24' in js
    assert r'/^\+?\d{6,15}$/' in js                                            # как store.AGENT_PHONE_RE


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))

# -*- coding: utf-8 -*-
"""«Աշխատավարձ» в настоящем app_v2 (гейт ролей, вход, CSRF): зарплаты видит только администратор; «Гараж», «Склад»,
пользователь территории и аноним не получают ни страницы, ни API, ни ссылки на неё."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import crew_pay as cp  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_garage_public import LAN, _session_as, app_v2, client  # noqa: E402,F401
from test_route_optimizer import _no_road_map  # noqa: E402,F401

PATHS = ('/routes/pay', '/api/routes/pay', '/api/routes/pay.csv?month=2026-09', '/api/routes/pay/params',
         '/routes/araqich', '/api/routes/araqich?month=2026-09')   # «Առաքիչների KPI» — те же данные, тот же доступ


@pytest.fixture
def users(app_v2):
    from werkzeug.security import generate_password_hash
    app_v2.test_users['wh1'] = {'role': 'warehouse', 'areas': [], 'display_name': 'Պահեստ',
                                'password_hash': generate_password_hash('warehouse-pass-1', method='pbkdf2:sha256:1000')}
    return app_v2.test_users


@pytest.fixture
def calls(app_v2, monkeypatch):
    state = app_v2.app.extensions['route_optimizer']
    seen = []
    monkeypatch.setattr(state, 'crew_pay_loader', lambda since, until: seen.append((since, until)) or cp.CrewData(
        (cp.Invoice(11, date(2026, 9, 1), 1, 1, 1000.0, 100.0),), {1: ('A001/4', 'Մ'), 11: ('B001/1', 'Կորյուն')}))
    monkeypatch.setattr(state, 'crew_pay_cache', {})
    # «сегодня» страницы — по Еревану и фиксировано: сентябрь 2026 остаётся среди последних 12 месяцев
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 7, 10, 0, tzinfo=timezone(timedelta(hours=4))))
    return seen


@pytest.mark.parametrize('name', ['garage1', 'wh1', 'u', 'odd'])
def test_other_roles_get_nothing(client, users, calls, name):
    h = _session_as(client, name, LAN)
    for path in PATHS:
        r = client.get(path, base_url=LAN)
        assert r.status_code in (302, 403), (name, path, r.status_code)
        assert 'Իսկանդարյան' not in r.get_data(as_text=True) and 'Կորյուն' not in r.get_data(as_text=True)
        if r.status_code == 302:
            assert '/routes/pay' not in r.headers['Location'] and '/routes/araqich' not in r.headers['Location']
    assert client.post('/api/routes/pay/params', json=cp.Params().json(), headers=h, base_url=LAN).status_code == 403
    assert calls == []                                                             # ERP даже не читали


def test_anonymous_must_log_in(client, calls):
    _session_as(client, None, LAN)
    assert client.get('/api/routes/pay', base_url=LAN).status_code == 401
    r = client.get('/routes/pay', base_url=LAN)
    assert r.status_code == 302 and '/login' in r.headers['Location']
    assert calls == []


def test_garage_page_has_no_pay_link(client, users):
    _session_as(client, 'garage1', LAN)
    html = client.get('/routes/garage', base_url=LAN).get_data(as_text=True)
    assert 'Ավտոտնակ' in html and '/routes/pay' not in html and 'Աշխատավարձ' not in html
    assert '/routes/araqich' not in html and 'KPI' not in html


def test_admin_sees_page_tab_and_data(client, users, calls):
    h = _session_as(client, 'boss', LAN)
    page = client.get('/routes/pay', base_url=LAN)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert '<a href="/routes/pay" aria-current="page">Աշխատավարձ</a>' in html and 'js/routes_pay.js?v=6' in html and 'css/routes_pay.css?v=2' in html
    assert '<a href="/routes/pay">Աշխատավարձ</a>' in client.get('/routes/garage', base_url=LAN).get_data(as_text=True)
    body = client.get('/api/routes/pay?month=2026-09', base_url=LAN).get_json()
    assert body['success'] and [r['name'] for r in body['rows']] == ['Կորյուն']
    assert client.get('/api/routes/pay', base_url=LAN).get_json()['month'] == '2026-10'          # часы подменены
    assert client.post('/api/routes/pay/params', json=cp.Params().json(), base_url=LAN).status_code == 403   # без CSRF
    assert client.post('/api/routes/pay/params', json=cp.Params().json(), headers=h, base_url=LAN).status_code == 200


def test_admin_sees_kpi_page_and_tab(client, users, calls):
    """«Առաքիչների KPI» — вкладка рядом с «Աշխատավարձ» на страницах раздела, страница и API — администратору."""
    _session_as(client, 'boss', LAN)
    html = client.get('/routes/araqich', base_url=LAN).get_data(as_text=True)
    assert '<a href="/routes/araqich" aria-current="page">Առաքիչների KPI</a>' in html
    assert 'js/routes_araqich.js?v=2' in html and 'css/routes_araqich.css?v=2' in html
    assert '<a href="/routes/araqich">Առաքիչների KPI</a>' in client.get('/routes/pay', base_url=LAN).get_data(as_text=True)
    body = client.get('/api/routes/araqich?month=2026-09', base_url=LAN).get_json()
    assert body['success'] and [p['name'] for p in body['people']] == ['Կորյուն']

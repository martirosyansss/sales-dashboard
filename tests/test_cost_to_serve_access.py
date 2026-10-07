# -*- coding: utf-8 -*-
"""«Առաքման արժեք» в настоящем app_v2 (гейт ролей, вход, CSRF, туннель): страницу и API видит только администратор в
офисной сети; «Գարաժ», «Պահեստ», пользователь территории и аноним не получают ни страницы, ни API, ни ссылки; с публичного
хоста туннеля (araqich.orix.am) отчёт не открывается никому — 404."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import cost_to_serve as cts  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_garage_public import LAN, PUBLIC, _session_as, app_v2, client  # noqa: E402,F401
from test_route_optimizer import _no_road_map  # noqa: E402,F401

PATHS = ('/routes/cost', '/api/routes/cost', '/api/routes/cost.csv?days=90', '/api/routes/cost/margin')


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
    monkeypatch.setattr(state, 'cost_sales_loader', lambda since, until: seen.append((since, until)) or cts.SalesData(
        (cts.Sale(date(2026, 10, 1), 101, 1, 11, 50_000.0, 100.0),), {1: 'A001/4'}, {101: ('C101', 'Մեծ խանութ')}))
    monkeypatch.setattr(state, 'cost_sales_cache', {})
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 7, 10, 0, tzinfo=timezone(timedelta(hours=4))))
    # склад и CAR1 (10 т, 30 л) — без них считать нечего (COST_NO_CTX)
    state.store.save(st.Changes(dict(st.DEFAULT_SETTINGS), True, (40.19462, 44.6004),
                                (st.Truck('CAR1', 10000.0, 30.0, active=True),), ()), 'qa')
    state.store.save_dispatch('2026-10-01', {'trucks': ['CAR1'], 'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [101]}]}, 'qa')
    return seen


@pytest.mark.parametrize('name', ['garage1', 'wh1', 'u', 'odd'])
def test_other_roles_get_nothing(client, users, calls, name):
    h = _session_as(client, name, LAN)
    for path in PATHS:
        r = client.get(path, base_url=LAN)
        assert r.status_code in (302, 403), (name, path, r.status_code)
        assert 'Մեծ խանութ' not in r.get_data(as_text=True)
        if r.status_code == 302:
            assert '/routes/cost' not in r.headers['Location']
    assert client.post('/api/routes/cost/margin', json={'value': 5}, headers=h, base_url=LAN).status_code == 403
    assert calls == []                                                             # ERP даже не читали


def test_anonymous_must_log_in(client, calls):
    _session_as(client, None, LAN)
    assert client.get('/api/routes/cost', base_url=LAN).status_code == 401
    r = client.get('/routes/cost', base_url=LAN)
    assert r.status_code == 302 and '/login' in r.headers['Location']
    assert calls == []


@pytest.mark.parametrize('name', ['boss', 'garage1', 'wh1', None])
def test_public_host_never_exposes_the_report(client, users, calls, name):
    h = _session_as(client, name, PUBLIC)
    for path in PATHS:
        r = client.get(path, base_url=PUBLIC)
        assert r.status_code == 404 and 'Մեծ խանութ' not in r.get_data(as_text=True), (name, path, r.status_code)
    assert client.post('/api/routes/cost/margin', json={'value': 5}, headers=h, base_url=PUBLIC).status_code == 404
    assert calls == []


def test_garage_page_has_no_cost_link(client, users):
    _session_as(client, 'garage1', LAN)
    html = client.get('/routes/garage', base_url=LAN).get_data(as_text=True)
    assert 'Ավտոտնակ' in html and '/routes/cost' not in html and 'Առաքման արժեք' not in html


def test_admin_sees_page_tab_and_data(client, users, calls):
    h = _session_as(client, 'boss', LAN)
    page = client.get('/routes/cost', base_url=LAN)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert '<a href="/routes/cost" aria-current="page">Առաքման արժեք</a>' in html
    assert 'js/routes_cost.js?v=1' in html and 'css/routes_cost.css?v=1' in html
    assert '<a href="/routes/cost">Առաքման արժեք</a>' in client.get('/routes/garage', base_url=LAN).get_data(as_text=True)
    body = client.get('/api/routes/cost?days=30', base_url=LAN).get_json()
    assert body['success'] and [r['name'] for r in body['rows']] == ['Մեծ խանութ'] and body['sources']['draft'] == 1
    assert calls == [(date(2026, 9, 7), date(2026, 10, 7))]
    assert client.post('/api/routes/cost/margin', json={'value': 5}, base_url=LAN).status_code == 403   # без CSRF
    assert client.post('/api/routes/cost/margin', json={'value': 5}, headers=h, base_url=LAN).status_code == 200

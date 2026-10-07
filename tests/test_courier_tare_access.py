# -*- coding: utf-8 -*-
"""«Տարա» (№87 п. 9) в настоящем app_v2 (гейт ролей, вход, CSRF, туннель): страницу и API видит только администратор в
офисе; «Գараж», «Պահեստ», пользователь территории и неизвестная роль — 403 или своя страница, аноним — вход; снаружи
(araqich.orix.am) — 404 для любой сессии, в списке открытого снаружи этих путей нет."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_garage_public import LAN, PUBLIC, _session_as, app_v2, client  # noqa: E402,F401
from test_route_optimizer import _no_road_map  # noqa: E402,F401

PATHS = ('/courier/tare', '/api/courier/admin/tare', '/api/courier/admin/tare/history?customer=1',
         '/api/courier/admin/tare.csv')
POSTS = (('/api/courier/admin/tare/opening', {'customer_id': 1, 'tare_id': 'erp:1', 'qty': None}),
         ('/api/courier/admin/tare/import', {'rows': [{'code': 'C1', 'tare': 'erp:1', 'qty': 1, 'as_of': '2026-10-01'}]}))


@pytest.fixture
def users(app_v2, monkeypatch):
    from werkzeug.security import generate_password_hash
    app_v2.test_users['wh1'] = {'role': 'warehouse', 'areas': [], 'display_name': 'Պահեստ',
                                'password_hash': generate_password_hash('warehouse-pass-1', method='pbkdf2:sha256:1000')}
    st = app_v2.app.extensions['courier']
    seen = []
    monkeypatch.setattr(st, 'tare_links_loader', lambda: seen.append('links') or ((), {}))   # ERP не трогаем
    monkeypatch.setattr(st, 'customer_code_loader', lambda codes: seen.append(codes) or {})
    monkeypatch.setattr(st, 'tare_days', {})
    return seen


@pytest.mark.parametrize('name', ['garage1', 'wh1', 'u', 'odd'])
def test_other_roles_get_nothing(client, users, name):
    h = _session_as(client, name, LAN)
    for path in PATHS:
        r = client.get(path, base_url=LAN)
        assert r.status_code in (302, 403), (name, path, r.status_code)
        if r.status_code == 302:
            assert '/courier/tare' not in r.headers['Location']
    for path, body in POSTS:
        assert client.post(path, json=body, headers=h, base_url=LAN).status_code == 403, (name, path)
    assert users == []                                                     # до ERP дело не дошло


def test_anonymous_must_log_in(client, users):
    _session_as(client, None, LAN)
    assert client.get('/api/courier/admin/tare', base_url=LAN).status_code == 401
    r = client.get('/courier/tare', base_url=LAN)
    assert r.status_code == 302 and '/login' in r.headers['Location']


@pytest.mark.parametrize('who', [None, 'garage1', 'wh1', 'boss'])
def test_public_host_is_404(client, app_v2, users, who):
    h = _session_as(client, who)
    for path in PATHS:
        r = client.get(path, base_url=PUBLIC)
        assert r.status_code == 404 and r.get_data(as_text=True) == 'Not Found', (who, path)
        assert app_v2._public_path_allowed(path.split('?')[0], 'GET') is False
    for path, body in POSTS:
        assert client.post(path, json=body, headers=h, base_url=PUBLIC).status_code == 404


def test_admin_page_api_and_csrf(client, users):
    h = _session_as(client, 'boss', LAN)
    page = client.get('/courier/tare', base_url=LAN)
    assert page.status_code == 200 and 'id="ctPage"' in page.get_data(as_text=True)
    assert 'href="/courier/tare"' in client.get('/courier', base_url=LAN).get_data(as_text=True)   # пункт меню
    body = client.get('/api/courier/admin/tare', base_url=LAN).get_json()
    assert body['success'] and body['rows'] == []
    path, data = POSTS[0]
    assert client.post(path, json=data, base_url=LAN).status_code == 403               # без CSRF
    assert client.post(path, json=data, headers=h, base_url=LAN).get_json() == {'success': True}

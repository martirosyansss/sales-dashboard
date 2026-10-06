"""Роль «Склад» (ответ владельца №78, 11–12): default-deny, как «Гараж» — только страница «Պահեստ» и её API, в офисе и из
интернета; посторонняя сессия снаружи — 404; вход снаружи — с паролем ≥ 10 символов; Настройки → Пользователи."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_garage_public import LAN, PUBLIC, TUNNEL, _login, _session_as, app_v2, client  # noqa: E402,F401
from test_route_optimizer import _no_road_map  # noqa: E402,F401

WH_PW = 'warehouse-pass-1'
ALLOWED = [('/routes/warehouse', 'GET'), ('/routes/warehouse', 'HEAD'), ('/api/routes/warehouse', 'GET'),
           ('/api/routes/warehouse/goods', 'GET'), ('/api/routes/warehouse/loaded', 'POST'),
           ('/static/css/routes_warehouse.css', 'GET'), ('/static/js/routes_warehouse.js', 'GET')]
DENIED = ['/routes/warehouse/', '/routes/warehouse-x', '/routes/warehousex', '/ROUTES/WAREHOUSE', '/routes//warehouse',
          '/api/routes/warehouse/', '/api/routes/warehouse-x', '/api/routes/warehousex/loaded',
          '/api/routes/warehouse/../dispatch', '/api/routes/warehouse/./loaded', '/api/routes/warehouse//loaded',
          '/api/routes/warehouse/Loaded', '/api/routes/warehouse/loaded\x00', '/api/routes/warehouse\\..\\users',
          '/static/css/routes_warehouse.css/', '/static/js/routes_warehouse.js\x00']


@pytest.fixture
def users(app_v2):
    from werkzeug.security import generate_password_hash
    app_v2.test_users['wh1'] = {'role': 'warehouse', 'areas': [], 'display_name': 'Պահեստ',
                                'password_hash': generate_password_hash(WH_PW, method='pbkdf2:sha256:1000')}
    app_v2.test_users['whshort'] = {'role': 'warehouse', 'areas': [], 'display_name': 'x',
                                    'password_hash': generate_password_hash('short-pw', method='pbkdf2:sha256:1000')}
    return app_v2.test_users


@pytest.mark.parametrize('path,method', ALLOWED)
def test_public_allowlist_has_warehouse(app_v2, path, method):
    assert app_v2._public_path_allowed(path, method) is True


@pytest.mark.parametrize('path', DENIED)
@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_public_allowlist_denies_lookalikes(app_v2, path, method):
    assert app_v2._public_path_allowed(path, method) is False


@pytest.mark.parametrize('path,method', [('/routes/warehouse', 'POST'), ('/api/routes/warehouse', 'PUT'),
                                         ('/api/routes/warehouse/loaded', 'DELETE')])
def test_public_allowlist_denies_methods(app_v2, path, method):
    assert app_v2._public_path_allowed(path, method) is False


@pytest.mark.parametrize('base', [LAN, PUBLIC])
def test_warehouse_session_sees_only_its_page(client, users, base):
    h = _session_as(client, 'wh1', base)
    page = client.get('/routes/warehouse', base_url=base)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'lang="hy"' in html and 'js/routes_warehouse.js' in html and 'Պահեստ' in html
    for link in ('href="/routes/settings"', 'href="/routes/dispatch"', 'href="/settings"', 'href="/"'):
        assert link not in html, link
    for path in ('/static/css/tokens.css', '/static/css/base.css', '/static/js/base.js', '/static/css/routes.css',
                 '/static/css/routes_warehouse.css', '/static/js/routes_warehouse.js'):
        assert path in html, path
        with client.get(path, base_url=base) as r:
            assert r.status_code == 200, path
    assert client.get('/api/routes/warehouse', base_url=base).status_code == 200
    # всё прочее: офис — 403 / на свою страницу, интернет — 404 (вне списка открытых) или 403 (чужая роль в списке)
    for path in ('/api/routes/dispatch?date=2026-10-03', '/api/routes/dispatch/waybill?date=2026-10-03&truck=CAR1',
                 '/api/routes/settings', '/api/users', '/api/routes/garage', '/api/routes/live'):
        assert client.get(path, base_url=base).status_code in ((403,) if base == LAN else (403, 404)), path
    for path, body in (('/api/routes/dispatch/edit', {'date': '2026-10-03', 'rev': 1, 'action': 'unloaded', 'trip': 1}),
                       ('/api/routes/dispatch/build', {'date': '2026-10-03', 'trucks': ['CAR1']}),
                       ('/api/routes/garage/entries', {})):
        assert client.post(path, json=body, base_url=base, headers={**h, 'Origin': base}).status_code in (403, 404), path
    r = client.get('/routes/dispatch', base_url=base)
    assert (r.status_code, r.headers.get('Location', '')) in ((302, '/routes/warehouse'), (404, ''))
    if base == LAN:
        assert r.status_code == 302


@pytest.mark.parametrize('who', [None, 'boss', 'u', 'odd'])
def test_public_foreign_session_gets_404_on_warehouse(client, users, who):
    h = _session_as(client, who)
    for path in ('/routes/warehouse', '/api/routes/warehouse', '/api/routes/warehouse/goods'):
        r = client.get(path, base_url=PUBLIC)
        if who is None:   # без входа — на страницу входа / 401, как у «Гаража»
            assert r.status_code in (302, 401), path
            continue
        assert r.status_code == 404 and r.get_data() == b'Not Found', (who, path)
    if who is not None:
        r = client.post('/api/routes/warehouse/loaded', json={}, base_url=PUBLIC, headers={**h, 'Origin': PUBLIC})
        assert r.status_code == 404


def test_garage_and_warehouse_do_not_see_each_other(client, users):
    _session_as(client, 'garage1', LAN)
    assert client.get('/api/routes/warehouse', base_url=LAN).status_code == 403
    assert client.get('/routes/warehouse', base_url=LAN).headers['Location'].endswith('/routes/garage')
    _session_as(client, 'wh1', LAN)
    assert client.get('/api/routes/garage', base_url=LAN).status_code == 403
    assert client.get('/routes/garage', base_url=LAN).headers['Location'].endswith('/routes/warehouse')


def test_public_warehouse_login_needs_long_password(client, users):
    r = _login(client, 'wh1', WH_PW, peer=TUNNEL, cf='203.0.113.30')
    assert r.status_code == 302 and r.headers['Location'] == '/routes/warehouse'
    r = client.get('/login', base_url=PUBLIC)
    assert r.status_code == 302 and r.headers['Location'] == '/routes/warehouse'
    r = _login(client, 'whshort', 'short-pw', peer=TUNNEL, cf='203.0.113.31')
    assert r.status_code == 401                                        # снаружи — пароль не короче 10
    r = _login(client, 'whshort', 'short-pw', base=LAN)
    assert r.status_code == 302 and r.headers['Location'] == '/routes/warehouse'   # в офисе — как раньше у «Гаража»


def test_users_api_accepts_warehouse_role(client, users, app_v2):
    h = _session_as(client, 'boss', LAN)
    post = lambda body: client.post('/api/users', json=body, base_url=LAN, headers={**h, 'Origin': LAN})   # noqa: E731
    r = post({'username': 'wh2', 'password': 'pw-warehouse-10', 'role': 'warehouse', 'areas': ['01'], 'display_name': 'Ս'})
    assert r.status_code == 200, r.get_json()
    assert users['wh2']['role'] == 'warehouse' and users['wh2']['areas'] == []
    r = post({'username': 'wh3', 'password': 'short', 'role': 'warehouse'})
    assert r.status_code == 400 and '«Склад»' in r.get_json()['error']
    r = post({'username': 'u', 'role': 'warehouse'})                  # сменить роль на «Склад» без нового пароля — нет
    assert r.status_code == 400 and users['u']['role'] == 'user'

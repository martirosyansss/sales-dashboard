"""Журнал гаража из интернета (№53, docs/plans/garage-journal-plan.md, последний раздел): что открыто на публичном
хосте туннеля, вход только роли «Гараж», ключ счётчика неудач по Cf-Connecting-Ip только от узла туннеля,
Secure-cookie и HSTS только снаружи, пароль «Гаража» ≥ 10 символов. Офисная сеть (LAN) — без изменений.
После ревью безопасности: ≤ 2 проверки пароля одновременно снаружи, бюджет неудач логина из интернета, журнал входов,
нейтральная страница входа снаружи, отзыв сессий (выход, смена пароля, 7 дней), пересчёт устаревших хэшей,
открытый редирект через управляющие символы в next."""
import json
import logging
import os
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_optimizer import _no_road_map, make_snapshot  # noqa: E402,F401

NOW = datetime(2026, 10, 3, 10, 0)
PUBLIC = 'https://araqich.orix.am'          # как видит запрос приложение на CT115: Host от туннеля, схема — https (ProxyFix)
LAN = 'http://192.168.1.24:5000'
TUNNEL = '192.168.1.11'
PASSWORDS = {'garage1': 'garage-pass-1', 'gshort': 'short-pw', 'boss': 'boss-password', 'u': 'user-password',
             'odd': 'odd-password'}
ROLES = {'garage1': 'garage', 'gshort': 'garage', 'boss': 'admin', 'u': 'user', 'odd': 'superuser'}
STATIC = ('/static/css/tokens.css', '/static/css/base.css', '/static/js/base.js', '/static/css/routes.css',
          '/static/css/routes_garage.css', '/static/js/routes_garage.js', '/favicon.ico')
ENTRY = {'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'Կոճղակներ', 'amount_amd': 85_000,
         'odometer_km': 120_500}


@pytest.fixture
def app_v2(tmp_path, monkeypatch):
    """Настоящий app_v2 (гейт, вход, CSRF, туннель); пользователи — в памяти, база маршрутов — временная."""
    import app_v2 as module
    from werkzeug.security import generate_password_hash
    users = {name: {'role': ROLES[name], 'areas': ['01'] if ROLES[name] == 'user' else [], 'display_name': name,
                    'password_hash': generate_password_hash(pw, method='pbkdf2:sha256:1000')}
             for name, pw in PASSWORDS.items()}
    monkeypatch.setattr(module, 'load_users', lambda: users)
    monkeypatch.setattr(module, 'save_users', lambda data: users.update(data) or True)
    monkeypatch.setattr(module, '_login_attempts', {})
    monkeypatch.setattr(module, '_public_failures', {})
    monkeypatch.setattr(module, '_TUNNEL_PEERS', frozenset({TUNNEL}))
    state = module.app.extensions['route_optimizer']
    monkeypatch.setattr(state, 'store', st.Store(str(tmp_path / 'routes.db')))
    monkeypatch.setattr(state, 'snapshots', SnapshotCache(lambda: make_snapshot()))
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    state.store.save(st.Changes(dict(st.DEFAULT_SETTINGS), False, None,
                                (st.Truck('CAR1', 10000.0, 30.0, active=True),), ()), 'qa')
    module.test_users = users
    return module


@pytest.fixture
def client(app_v2):
    return app_v2.app.test_client()


def _session_as(c, name, base=PUBLIC):
    """Сессия как после входа name (None — без входа) с известным токеном формы."""
    import app_v2
    with c.session_transaction(base_url=base) as s:
        s.clear()
        if name:
            app_v2._stamp_session(s, name, app_v2.test_users[name])
        s['_office_csrf'] = 'x' * 40
    return {'X-CSRF-Token': 'x' * 40}


def _login(c, name, password, base=PUBLIC, peer='127.0.0.1', cf=None):
    """POST /login как браузер с открытой формой входа (в сессии — только токен формы), Origin своего хоста.
    Настоящий GET /login → токен — в test_public_garage_flow и test_ct115_proxy_chain."""
    h = _session_as(c, None, base)
    headers = {'Origin': base, **({'Cf-Connecting-Ip': cf} if cf else {})}
    return c.post('/login', base_url=base, environ_base={'REMOTE_ADDR': peer}, headers=headers,
                  data={'username': name, 'password': password, 'csrf_token': h['X-CSRF-Token']})


# ============================== что открыто снаружи ==============================

ALLOWED = [('/login', m) for m in ('GET', 'HEAD', 'POST')] + [('/logout', 'POST')] + \
    [('/routes/garage', m) for m in ('GET', 'HEAD')] + [('/api/routes/garage', 'GET')] + \
    [(p, m) for p in ('/api/routes/garage/entries',) for m in ('GET', 'HEAD', 'POST')] + \
    [('/api/routes/garage/entries/delete', 'POST'), ('/api/routes/garage/odometers', 'POST')] + \
    [(p, m) for p in STATIC for m in ('GET', 'HEAD')]

# Пути уже раскодированы сервером (waitress / тестовый клиент раскодируют %XX один раз).
DENIED_PATHS = [
    '/', '/routes', '/routes/', '/routes/garage/', '/routes/garage/x', '/routes/garage-x', '/routes/garagex',
    '/Routes/Garage', '/ROUTES/GARAGE', '/routes//garage', '/routes/garage\x00', '/routes/garage\n', '/routes/garage.',
    '/login/', '/LOGIN', '/Login', '/login\x00', '/logout/',
    '/api/routes/garage/', '/api/routes/garage-x', '/api/routes/garagex', '/api/routes/garagex/entries',
    '/api/routes/garage/../../users', '/api/routes/garage/entries/../../../users', '/api/routes/garage/..',
    '/api/routes/garage/./entries', '/api/routes/garage//entries', '/api/routes/garage/entries/',
    '/api/routes/garage/Entries', '/API/routes/garage', '/api/routes/garage/entries\n', '/api/routes/garage/entries\x00',
    '/api/routes/garage\\..\\..\\users', '/api/routes/garage/%2e%2e/users', '/api/routes/garage/entries;x',
    '/api/routes/garage/entries%2Fdelete',
    '/static/css/routes_dispatch.css', '/static/js/routes_dispatch.js', '/static/js/settings.js', '/static/css/style.css',
    '/static/css/../js/settings.js', '/static/css/routes.css/', '/static/CSS/routes.css', '/static/css/routes.css\x00',
    '/static//css/routes.css', '/static/css/./routes.css', '/favicon.ico/', '/static/favicon.ico', '/static/',
    '/settings', '/api/users', '/api/courier/admin/today', '/courier', '/routes/settings', '/api/routes/settings',
    '/api/routes/dispatch', '/areas', '/customers-grid', '/api/customers', '/test-db',
]
DENIED_METHODS = [('/logout', 'GET'), ('/logout', 'HEAD'), ('/login', 'PUT'), ('/login', 'DELETE'), ('/login', 'OPTIONS'),
                  ('/login', 'PATCH'), ('/routes/garage', 'POST'), ('/routes/garage', 'OPTIONS'),
                  ('/api/routes/garage', 'OPTIONS'), ('/api/routes/garage/entries', 'PUT'),
                  ('/api/routes/garage/entries', 'DELETE'), ('/api/routes/garage/entries', 'PATCH'),
                  ('/static/css/routes.css', 'POST'), ('/favicon.ico', 'POST'), ('/static/js/base.js', 'OPTIONS')]


@pytest.mark.parametrize('path,method', ALLOWED)
def test_public_allowlist_allows(app_v2, path, method):
    assert app_v2._public_path_allowed(path, method) is True


@pytest.mark.parametrize('path', DENIED_PATHS)
@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_public_allowlist_denies_paths(app_v2, path, method):
    assert app_v2._public_path_allowed(path, method) is False


@pytest.mark.parametrize('path,method', DENIED_METHODS)
def test_public_allowlist_denies_methods(app_v2, path, method):
    assert app_v2._public_path_allowed(path, method) is False


# Сырые запросы (как пришли бы из туннеля, с процент-кодированием) — 404 для любой сессии.
RAW_DENIED = ['/', '/settings', '/routes', '/routes/garage/', '/routes/garage-x', '/ROUTES/GARAGE', '/routes//garage',
              '/routes/garage%2F', '/routes/garage%00', '/api/routes/garage-x', '/api/routes/garagex/entries',
              '/api/routes/garage/%2e%2e/%2e%2e/users', '/api/routes/garage%2f..%2f..%2fusers',
              '/api/routes/garage/..%2F..%2Fusers', '/api/routes/garage/entries/../../../users',
              '/api/routes/garage/%252e%252e/users', '/api/routes/garage%5c..%5cusers', '/api/users',
              '/api/routes/settings', '/static/js/settings.js', '/static/css/..%2fjs/settings.js',
              '/static/css/routes_dispatch.css', '/api/courier/admin/today', '/courier']


@pytest.mark.parametrize('who', [None, 'garage1', 'boss', 'u', 'odd'])
def test_public_denied_is_plain_404_for_every_session(client, who):
    h = _session_as(client, who)
    for path in RAW_DENIED:
        for method in ('GET', 'POST'):
            r = client.open(path, method=method, base_url=PUBLIC, headers=h)
            assert r.status_code == 404, (who, method, path, r.status_code)
            assert r.mimetype == 'text/plain' and r.get_data() == b'Not Found', path
    for path, method in DENIED_METHODS:
        assert client.open(path, method=method, base_url=PUBLIC, headers=h).status_code == 404, (who, method, path)


@pytest.mark.parametrize('who', ['boss', 'u', 'odd'])
def test_public_non_garage_session_gets_404(client, who):
    """Сессия админа / по территориям / неизвестной роли снаружи: журнал и его API — 404 (дашборд не виден)."""
    h = _session_as(client, who)
    for path in ('/routes/garage', '/api/routes/garage', '/api/routes/garage/entries'):
        r = client.get(path, base_url=PUBLIC)
        assert r.status_code == 404 and r.get_data() == b'Not Found', (who, path)
    assert client.post('/api/routes/garage/entries', json=ENTRY, base_url=PUBLIC, headers=h).status_code == 404
    r = client.get('/login', base_url=PUBLIC)                    # форма входа, не редирект в дашборд
    assert r.status_code == 200 and 'name="password"' in r.get_data(as_text=True)
    # в офисе та же сессия работает как раньше
    _session_as(client, who, base=LAN)
    assert client.get('/login', base_url=LAN).status_code == 302


def test_public_anonymous(client):
    r = client.get('/routes/garage', base_url=PUBLIC)
    assert r.status_code == 302 and r.headers['Location'].startswith('/login?next=')
    r = client.get('/api/routes/garage', base_url=PUBLIC)
    assert r.status_code == 401 and r.get_json()['success'] is False
    assert client.get('/login', base_url=PUBLIC).status_code == 200
    assert client.head('/login', base_url=PUBLIC).status_code == 200
    for path in STATIC:
        with client.get(path, base_url=PUBLIC) as r:
            assert r.status_code == 200, path
    assert client.post('/logout', base_url=PUBLIC).status_code == 403          # CSRF — как у всего офиса


def test_public_host_detected_by_header_and_case(client):
    """Публичный — и Host araqich.orix.am в любом регистре/с портом, и любой запрос с заголовком Cloudflare."""
    for kw in ({'base_url': 'https://ARAQICH.ORIX.AM:443'},
               {'base_url': LAN, 'headers': {'Cf-Connecting-Ip': '203.0.113.9'}}):
        assert client.get('/settings', **kw).status_code == 404
        assert client.get('/login', **kw).status_code == 200


def test_public_garage_flow(client, app_v2):
    """Начальник гаража с телефона: вход → журнал → запись → выход; cookie Secure, HSTS."""
    env, cf = {'REMOTE_ADDR': TUNNEL}, {'Cf-Connecting-Ip': '203.0.113.5'}
    html = client.get('/login', base_url=PUBLIC, environ_base=env, headers=cf).get_data(as_text=True)
    token = html.split('name="csrf_token" value="')[1].split('"')[0]
    r = client.post('/login', base_url=PUBLIC, environ_base=env, headers={**cf, 'Origin': PUBLIC},
                    data={'username': 'garage1', 'password': PASSWORDS['garage1'], 'csrf_token': token})
    assert r.status_code == 302 and r.headers['Location'] == '/routes/garage'
    cookie = r.headers['Set-Cookie']
    assert '; Secure' in cookie and '; HttpOnly' in cookie and 'SameSite=Lax' in cookie, cookie
    assert r.headers['Strict-Transport-Security'] == 'max-age=31536000'
    page = client.get('/routes/garage', base_url=PUBLIC)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for path in STATIC[:-1]:                                     # вся статика страницы — из списка открытых
        assert path in html, path
    assert 'href="/routes/settings"' not in html and 'href="/"' not in html
    token = html.split('name="csrf-token" content="')[1].split('"')[0]
    h = {'X-CSRF-Token': token, 'Origin': PUBLIC}
    assert client.get('/api/routes/garage', base_url=PUBLIC).status_code == 200
    r = client.post('/api/routes/garage/entries', json=ENTRY, base_url=PUBLIC, headers=h)
    assert r.status_code == 200, r.get_json()
    assert client.post('/api/routes/garage/odometers', base_url=PUBLIC, headers=h,
                       json={'items': [{'car_code': 'CAR1', 'day': '2026-10-01', 'odometer_km': 121_000}]}).status_code == 200
    assert client.post('/api/routes/garage/entries/delete', json={'id': r.get_json()['id']}, base_url=PUBLIC,
                       headers=h).status_code == 200
    assert client.post('/api/routes/garage/entries', json=ENTRY, base_url=PUBLIC,
                       headers={'Origin': PUBLIC}).status_code == 403                       # без CSRF — нет
    assert client.post('/api/routes/garage/entries', json=ENTRY, base_url=PUBLIC,
                       headers={**h, 'Origin': 'https://evil.example'}).status_code == 403
    r = client.get('/login', base_url=PUBLIC)                    # уже вошёл — сразу журнал ('/' снаружи закрыт)
    assert r.status_code == 302 and r.headers['Location'] == '/routes/garage'
    r = client.post('/logout', base_url=PUBLIC, headers=h)
    assert r.status_code == 302 and r.headers['Location'] == '/login'
    assert '; Secure' in r.headers['Set-Cookie'] and 'Expires=Thu, 01 Jan 1970' in r.headers['Set-Cookie']
    assert client.get('/api/routes/garage', base_url=PUBLIC).status_code == 401


# ============================== вход снаружи: только «Гараж», без перечисления ==============================

def test_public_login_only_garage_same_generic_failure(client, app_v2, monkeypatch):
    checked = []
    real = app_v2.check_password_hash
    monkeypatch.setattr(app_v2, 'check_password_hash', lambda h, p: checked.append(h) or real(h, p))
    attempts = [('boss', PASSWORDS['boss']), ('u', PASSWORDS['u']), ('odd', PASSWORDS['odd']),
                ('gshort', PASSWORDS['gshort']), ('boss', 'wrong-password'), ('garage1', 'wrong-password'),
                ('nobody', 'whatever-pass')]
    bodies = set()
    for name, pw in attempts:
        checked.clear()
        r = _login(client, name, pw, peer=TUNNEL, cf='203.0.113.20')
        assert r.status_code == 401, name
        assert 'Սխալ մուտքանուն կամ գաղտնաբառ' in r.get_data(as_text=True)
        bodies.add(r.get_data())
        # одна проверка хэша на попытку: своего у существующего логина, фиктивного — у несуществующего (время то же)
        users = app_v2.test_users
        assert checked == [users[name]['password_hash'] if name in users else app_v2._DUMMY_PWD_HASH], name
        with client.session_transaction(base_url=PUBLIC) as s:
            assert 'username' not in s
    assert len(bodies) == 1                                      # ответы неотличимы
    # в офисе те же логины входят как раньше (и короткий пароль «Гаража» — тоже)
    for name, target in (('boss', '/'), ('u', '/areas'), ('gshort', '/routes/garage'), ('garage1', '/routes/garage')):
        r = _login(client, name, PASSWORDS[name], base=LAN)
        assert r.status_code == 302 and r.headers['Location'] == target, name


def test_public_non_garage_login_counts_for_lockout(client):
    for _ in range(5):
        assert _login(client, 'boss', PASSWORDS['boss'], peer=TUNNEL, cf='203.0.113.30').status_code == 401
    r = _login(client, 'boss', PASSWORDS['boss'], peer=TUNNEL, cf='203.0.113.30')
    assert r.status_code == 429
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.30').status_code == 302


# ============================== ключ счётчика неудач ==============================

@pytest.mark.parametrize('peer,headers,expected', [
    (TUNNEL, {'Cf-Connecting-Ip': '203.0.113.5'}, '203.0.113.5'),
    (TUNNEL, {'Cf-Connecting-Ip': ' 203.0.113.5 '}, '203.0.113.5'),
    (TUNNEL, {'Cf-Connecting-Ip': '2001:db8:1:2:aaaa::1'}, '2001:db8:1:2::/64'),
    (TUNNEL, {'Cf-Connecting-Ip': '2001:0db8:0001:0002:ffff:ffff:ffff:ffff'}, '2001:db8:1:2::/64'),
    (TUNNEL, {'Cf-Connecting-Ip': 'garbage'}, TUNNEL),
    (TUNNEL, {'Cf-Connecting-Ip': '203.0.113.5, 198.51.100.1'}, TUNNEL),
    (TUNNEL, {}, TUNNEL),
    ('192.168.1.50', {'Cf-Connecting-Ip': '203.0.113.5'}, '192.168.1.50'),   # подделка из офиса / LAN
    ('127.0.0.1', {'Cf-Connecting-Ip': '203.0.113.5'}, '127.0.0.1'),
    ('192.168.1.111', {'Cf-Connecting-Ip': '203.0.113.5'}, '192.168.1.111'),
])
def test_login_client_ip(app_v2, peer, headers, expected):
    with app_v2.app.test_request_context('/login', base_url=PUBLIC, environ_base={'REMOTE_ADDR': peer}, headers=headers):
        assert app_v2._login_client_ip() == expected
        assert app_v2._login_key('Garage1') == ('garage1', expected)


def test_login_client_ip_no_trusted_peers(app_v2, monkeypatch):
    monkeypatch.setattr(app_v2, '_TUNNEL_PEERS', frozenset())
    with app_v2.app.test_request_context('/login', base_url=PUBLIC, environ_base={'REMOTE_ADDR': TUNNEL},
                                         headers={'Cf-Connecting-Ip': '203.0.113.5'}):
        assert app_v2._login_client_ip() == TUNNEL


def test_lockout_per_client_behind_tunnel(client):
    for _ in range(5):
        assert _login(client, 'garage1', 'wrong-password', peer=TUNNEL, cf='203.0.113.7').status_code == 401
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.7').status_code == 429
    # другой клиент из интернета — свой счётчик: чужие ошибки не блокируют начальника гаража
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.8').status_code == 302


def test_lockout_ipv6_by_64(client):
    for i in range(1, 6):
        assert _login(client, 'garage1', 'wrong-password', peer=TUNNEL, cf=f'2001:db8:1:2::{i}').status_code == 401
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='2001:db8:1:2::99').status_code == 429
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='2001:db8:1:3::1').status_code == 302


def test_spoofed_cf_header_from_untrusted_peer_ignored(client):
    """Подделанный Cf-Connecting-Ip не от узла туннеля не помогает: ключ — сам пир, смена заголовка не обходит лимит."""
    for i in range(5):
        assert _login(client, 'garage1', 'wrong-password', peer='192.168.1.50',
                      cf=f'198.51.100.{i}').status_code == 401
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer='192.168.1.50', cf='198.51.100.77').status_code == 429


def test_ct115_proxy_chain(client, app_v2, monkeypatch):
    """Как на CT115: waitress ← nginx (127.0.0.1) с X-Forwarded-For = пир nginx и X-Forwarded-Proto https,
    FLASK_TRUSTED_PROXY_HOPS=1 → ProxyFix. От туннеля ключ — Cf-Connecting-Ip, Origin https проходит CSRF;
    из офиса (nginx :443, XFF = адрес в LAN) подделанный Cf-Connecting-Ip не работает."""
    monkeypatch.setattr(app_v2.app, 'wsgi_app', ProxyFix(app_v2.app.wsgi_app, x_for=1, x_proto=1))
    base = 'http://araqich.orix.am'            # до ProxyFix — http (nginx → waitress)

    def attempt(xff, cf, password):
        with client.session_transaction(base_url=base) as s:
            s.clear()                          # новый браузер: форма входа с нуля
        env = {'REMOTE_ADDR': '127.0.0.1'}
        h = {'X-Forwarded-For': xff, 'X-Forwarded-Proto': 'https', 'Cf-Connecting-Ip': cf}
        html = client.get('/login', base_url=base, environ_base=env, headers=h).get_data(as_text=True)
        token = html.split('name="csrf_token" value="')[1].split('"')[0]
        return client.post('/login', base_url=base, environ_base=env, headers={**h, 'Origin': PUBLIC},
                           data={'username': 'garage1', 'password': password, 'csrf_token': token})

    for _ in range(5):
        assert attempt(TUNNEL, '203.0.113.40', 'wrong-password').status_code == 401
    assert attempt(TUNNEL, '203.0.113.40', PASSWORDS['garage1']).status_code == 429
    r = attempt(TUNNEL, '203.0.113.41', PASSWORDS['garage1'])
    assert r.status_code == 302 and r.headers['Location'] == '/routes/garage' and '; Secure' in r.headers['Set-Cookie']
    for i in range(5):
        assert attempt('192.168.1.50', f'198.51.100.{i}', 'wrong-password').status_code == 401
    assert attempt('192.168.1.50', '198.51.100.99', PASSWORDS['garage1']).status_code == 429


# ============================== Secure-cookie и HSTS — только снаружи ==============================

def test_secure_cookie_and_hsts_only_on_public_host(client):
    r = client.get('/login', base_url=PUBLIC)
    assert '; Secure' in r.headers['Set-Cookie'] and '; HttpOnly' in r.headers['Set-Cookie']
    assert r.headers['Strict-Transport-Security'] == 'max-age=31536000'
    for path in ('/settings', '/api/courier/v1/ping', '/static/css/routes.css'):
        with client.get(path, base_url=PUBLIC) as r:
            assert r.headers.get('Strict-Transport-Security') == 'max-age=31536000', path
    lan = client.get('/login', base_url=LAN)
    assert 'Secure' not in lan.headers['Set-Cookie'] and '; HttpOnly' in lan.headers['Set-Cookie']
    assert 'SameSite=Lax' in lan.headers['Set-Cookie']
    for path in ('/login', '/', '/settings', '/api/courier/v1/ping', '/static/css/routes.css', '/no-such-page'):
        with client.get(path, base_url=LAN) as r:
            assert 'Strict-Transport-Security' not in r.headers, path
    r = _login(client, 'boss', PASSWORDS['boss'], base=LAN)
    assert r.status_code == 302 and 'Secure' not in r.headers['Set-Cookie']


def test_lan_unchanged(client):
    """Офис: вход ролей, редиректы, гейт — как до №53 (в т.ч. от адреса узла туннеля без заголовка Cloudflare)."""
    r = _login(client, 'boss', PASSWORDS['boss'], base=LAN)
    assert r.status_code == 302 and r.headers['Location'] == '/'
    assert client.get('/login', base_url=LAN).headers['Location'] == '/'
    assert client.get('/settings', base_url=LAN).status_code == 200
    r = _login(client, 'garage1', PASSWORDS['garage1'], base=LAN, peer=TUNNEL)
    assert r.status_code == 302 and r.headers['Location'] == '/routes/garage'
    r = client.get('/login', base_url=LAN)
    assert r.status_code == 302 and r.headers['Location'] == '/'
    r = client.get('/settings', base_url=LAN)
    assert r.status_code == 302 and r.headers['Location'] == '/routes/garage'
    assert client.get('/logout', base_url=LAN).status_code == 405
    _session_as(client, 'u', base=LAN)
    assert client.get('/settings', base_url=LAN).headers['Location'] == '/areas'
    assert client.get('/routes/garage', base_url=LAN).headers['Location'] == '/areas'
    _session_as(client, None, base=LAN)
    r = client.get('/routes/garage', base_url=LAN)
    assert r.status_code == 302 and r.headers['Location'].startswith('/login')


# ============================== пароль роли «Гараж» ≥ 10 символов ==============================

def test_garage_password_min_length(client, app_v2):
    users = app_v2.test_users
    h = _session_as(client, 'boss', base=LAN)

    def save(body):
        return client.post('/api/users', json=body, base_url=LAN, headers=h)

    r = save({'username': 'g9', 'password': '123456789', 'role': 'garage'})
    assert r.status_code == 400 and 'не короче 10 символов' in r.get_json()['error'] and 'g9' not in users
    assert save({'username': 'g10', 'password': '1234567890', 'role': 'garage'}).status_code == 200
    assert users['g10']['role'] == 'garage'
    old_hash = users['g10']['password_hash']
    assert save({'username': 'g10', 'password': 'short', 'role': 'garage'}).status_code == 400
    assert users['g10']['password_hash'] == old_hash
    assert save({'username': 'g10', 'role': 'garage', 'display_name': 'Գ'}).status_code == 200   # без пароля — не меняется
    assert users['g10']['password_hash'] == old_hash and users['g10']['display_name'] == 'Գ'
    # стать «Гаражом» можно только с новым длинным паролем
    r = save({'username': 'u', 'role': 'garage'})
    assert r.status_code == 400 and 'не короче 10 символов' in r.get_json()['error'] and users['u']['role'] == 'user'
    assert save({'username': 'u', 'role': 'garage', 'password': 'abc'}).status_code == 400
    assert save({'username': 'u', 'role': 'garage', 'password': 'abcdefghij'}).status_code == 200
    assert users['u']['role'] == 'garage'
    # прочие роли — как раньше: длина не проверяется
    assert save({'username': 'a2', 'password': 'pw', 'role': 'admin'}).status_code == 200
    assert save({'username': 'u2', 'password': 'pw', 'role': 'user', 'areas': ['01']}).status_code == 200
    assert save({'username': 'u2', 'role': 'user', 'areas': ['01']}).status_code == 200


# ============================== ревью: нагрузка входом из интернета (≤ 2 проверки пароля сразу) ==============================

THROTTLED_HY = 'Չափազանց շատ փորձեր։ Կրկնեք մի քանի րոպեից։'


def _spy_hash(app_v2, monkeypatch, hold=None):
    """Счётчик вызовов check_password_hash; пароль hold держит проверку, пока не откроют gate."""
    real, calls = app_v2.check_password_hash, []
    gate, inside = threading.Event(), threading.Semaphore(0)

    def spy(h, p):
        calls.append(h)
        if p == hold:
            inside.release()
            assert gate.wait(10)
        return real(h, p)
    monkeypatch.setattr(app_v2, 'check_password_hash', spy)
    return calls, gate, inside


def test_public_hash_slots_busy_429_without_hashing(client, app_v2, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='app_v2')
    calls, _, _ = _spy_hash(app_v2, monkeypatch)
    slots = app_v2._PUBLIC_HASH_SLOTS
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False)      # обе проверки «заняты»
    try:
        r = _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.50')
        html = r.get_data(as_text=True)
        assert r.status_code == 429 and THROTTLED_HY in html and 'name="password"' in html and calls == []
        assert 'заняты проверки паролей' in caplog.text
        r = _login(client, 'garage1', PASSWORDS['garage1'], base=LAN)          # офис — без этого предела
        assert r.status_code == 302 and len(calls) == 1
    finally:
        slots.release()
        slots.release()
    r = _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.50')
    assert r.status_code == 302                                                 # занятость — не неудача


def test_public_hash_slots_real_concurrency(client, app_v2, monkeypatch):
    """Две проверки снаружи идут — третья сразу 429 (без проверки), офис проходит; слоты освобождаются."""
    calls, gate, inside = _spy_hash(app_v2, monkeypatch, hold='hold-the-slot!')
    codes = []

    def attempt(i):
        codes.append(_login(app_v2.app.test_client(), 'garage1', 'hold-the-slot!', peer=TUNNEL,
                            cf=f'203.0.113.{60 + i}').status_code)
    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    try:
        assert inside.acquire(timeout=10) and inside.acquire(timeout=10)
        r = _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.70')
        assert r.status_code == 429 and len(calls) == 2
        assert _login(app_v2.app.test_client(), 'boss', PASSWORDS['boss'], base=LAN).status_code == 302
    finally:
        gate.set()
        for t in threads:
            t.join(10)
    assert sorted(codes) == [401, 401]
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.71').status_code == 302


def test_public_hash_slot_released_on_error(client, app_v2, monkeypatch):
    def boom(h, p):
        raise RuntimeError('hash backend failed')
    monkeypatch.setattr(app_v2, 'check_password_hash', boom)
    monkeypatch.setitem(app_v2.app.config, 'PROPAGATE_EXCEPTIONS', False)
    for _ in range(3):
        assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.80').status_code == 500
    slots = app_v2._PUBLIC_HASH_SLOTS
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False)      # обе свободны
    slots.release()
    slots.release()


# ============================== ревью: бюджет неудач логина из интернета (со всех адресов) ==============================

def test_public_budget_per_username_across_ips(client, app_v2, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='app_v2')
    budget = app_v2._PUBLIC_FAIL_BUDGET
    assert budget == 30 and app_v2._PUBLIC_FAIL_WINDOW == 3600
    for i in range(budget):                                   # по одной неудаче с каждого адреса: блокировка (5) молчит
        assert _login(client, 'Garage1' if i % 2 else 'garage1', 'wrong-password', peer=TUNNEL,
                      cf=f'198.51.100.{i}').status_code == 401
    calls, _, _ = _spy_hash(app_v2, monkeypatch)
    r = _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.90')    # новый адрес, верный пароль
    assert r.status_code == 429 and THROTTLED_HY in r.get_data(as_text=True) and calls == []
    assert f'больше {budget} неудач за час из интернета' in caplog.text
    assert any(rec.levelno == logging.WARNING and 'отклонён' in rec.getMessage() for rec in caplog.records)
    assert _login(client, 'garage1', PASSWORDS['garage1'], base=LAN).status_code == 302      # офис не заблокирован
    assert _login(client, 'boss', 'wrong-password', peer=TUNNEL, cf='203.0.113.91').status_code == 401  # другой логин
    # окно скользит: отметки старше часа не считаются
    with app_v2._public_failures_lock:
        for key, ts in app_v2._public_failures.items():
            app_v2._public_failures[key] = [t - 3601 for t in ts]
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.92').status_code == 302


def test_public_budget_counts_only_internet_failures(client, app_v2):
    for i in range(40):                                       # офис ошибается сколько угодно (с разных адресов)
        assert _login(client, 'garage1', 'wrong-password', base=LAN, peer=f'192.168.1.{100 + i}').status_code == 401
    assert app_v2._public_failures == {}
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.93').status_code == 302


def test_public_budget_memory_bounded(app_v2, monkeypatch):
    monkeypatch.setattr(app_v2, '_PUBLIC_FAIL_MAX_KEYS', 5)
    for _ in range(100):
        app_v2._public_register_failure('garage1')
    assert len(app_v2._public_failures['garage1']) == app_v2._PUBLIC_FAIL_BUDGET
    assert app_v2._public_budget_exhausted('GARAGE1')
    for i in range(4):
        app_v2._public_register_failure(f'random{i}')
    app_v2._public_register_failure('garage1')                # свежая неудача — в конец очереди вытеснения
    for i in range(4, 20):                                    # поток случайных логинов
        app_v2._public_register_failure(f'random{i}')
        assert len(app_v2._public_failures) <= 5
        if i < 7:
            assert 'garage1' in app_v2._public_failures      # вытесняются сначала давно не ошибавшиеся
    assert 'random0' not in app_v2._public_failures and 'random19' in app_v2._public_failures


# ============================== ревью: журнал входов ==============================

def test_login_events_logged_without_password(client, app_v2, caplog):
    caplog.set_level(logging.INFO, logger='app_v2')

    def last(level):
        recs = [r for r in caplog.records if r.name == 'app_v2' and r.levelno == level]
        return recs[-1].getMessage() if recs else ''
    _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.100')
    assert "Вход: 'garage1' (роль garage), IP 203.0.113.100, интернет" in last(logging.INFO)
    _login(client, 'garage1', 'wrong-pass-123', peer=TUNNEL, cf='203.0.113.101')
    assert "Неудачный вход: 'garage1', IP 203.0.113.101, интернет" in last(logging.WARNING)
    _login(client, 'boss', PASSWORDS['boss'], peer=TUNNEL, cf='203.0.113.102')
    assert any("не разрешён: 'boss' (роль admin, пароль верный), IP 203.0.113.102" in r.getMessage()
               for r in caplog.records)
    _login(client, 'gshort', PASSWORDS['gshort'], peer=TUNNEL, cf='203.0.113.103')
    assert any("не разрешён: 'gshort' (роль garage, пароль верный, короче 10 символов)" in r.getMessage()
               for r in caplog.records)
    for _ in range(6):
        _login(client, 'u', 'wrong-pass-456', base=LAN, peer='192.168.1.77')
    assert "Вход 'u' отклонён (блокировка после 5 неудач с этого адреса): IP 192.168.1.77, офис" in last(logging.WARNING)
    _login(client, 'boss', PASSWORDS['boss'], base=LAN, peer='192.168.1.78')
    assert "Вход: 'boss' (роль admin), IP 192.168.1.78, офис" in last(logging.INFO)
    _login(client, 'evil\nFAKE ENTRY', 'wrong-pass-789', peer=TUNNEL, cf='203.0.113.104')
    assert "'evil\\nFAKE ENTRY'" in last(logging.WARNING)                         # подделать строку журнала нельзя
    text = '\n'.join(r.getMessage() for r in caplog.records)
    for secret in (*PASSWORDS.values(), 'wrong-pass-123', 'wrong-pass-456', 'wrong-pass-789'):
        assert secret not in text, secret


# ============================== ревью: нейтральная страница входа снаружи ==============================

def test_public_login_page_neutral_lan_unchanged(client):
    for r in (client.get('/login', base_url=PUBLIC),
              _login(client, 'garage1', 'wrong-password', peer=TUNNEL, cf='203.0.113.110')):
        html = r.get_data(as_text=True)
        assert 'lang="hy"' in html and 'Ավտոտնակ' in html and 'Գաղտնաբառ' in html
        for word in ('Sales Dashboard', 'AS-Sales', 'READ-ONLY', 'Вход', 'Логин', 'Пароль', 'Неверный'):
            assert word not in html, word
    lan = client.get('/login', base_url=LAN).get_data(as_text=True)
    assert 'lang="ru"' in lan and '<title>Вход — Sales Dashboard v2.0</title>' in lan
    assert 'READ-ONLY аналитика · AS-Sales Management 7' in lan and 'Ավտոտնակ' not in lan
    r = _login(client, 'garage1', 'wrong-password', base=LAN)
    assert 'Неверный логин или пароль' in r.get_data(as_text=True)


# ============================== ревью: пересчёт устаревшего хэша при входе ==============================

def test_outdated_hash_rehashed_on_login(client, app_v2, monkeypatch):
    from werkzeug.security import check_password_hash as real_check
    users = app_v2.test_users
    assert users['garage1']['password_hash'].startswith('pbkdf2:sha256:1000$')          # «старые» параметры
    assert _login(client, 'garage1', 'wrong-password', peer=TUNNEL, cf='203.0.113.120').status_code == 401
    assert users['garage1']['password_hash'].startswith('pbkdf2:sha256:1000$')          # неудача — не трогаем
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.120').status_code == 302
    new = users['garage1']['password_hash']
    assert new.split('$', 1)[0] == app_v2._PWD_HASH_PARAMS == app_v2._DUMMY_PWD_HASH.split('$', 1)[0]
    assert real_check(new, PASSWORDS['garage1'])
    assert client.get('/api/routes/garage', base_url=PUBLIC).status_code == 200          # сессия — от нового хэша
    saves = []
    monkeypatch.setattr(app_v2, 'save_users', lambda data: saves.append(1) or True)
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.121').status_code == 302
    assert saves == [] and users['garage1']['password_hash'] == new                     # текущие параметры — без записи


# ============================== ревью: отзыв сессий ==============================

def _host(base):
    return base.split('//')[1].split(':')[0]


def _replay(app_v2, cookie, path='/api/routes/garage', base=PUBLIC):
    c = app_v2.app.test_client()
    c.set_cookie('session', cookie, domain=_host(base))
    return c.get(path, base_url=base).status_code


def test_logout_revokes_replayed_cookie_on_all_devices(client, app_v2):
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.130').status_code == 302
    phone2 = app_v2.app.test_client()
    assert _login(phone2, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.131').status_code == 302
    stolen = client.get_cookie('session', domain=_host(PUBLIC)).value
    assert _replay(app_v2, stolen) == 200
    page = client.get('/routes/garage', base_url=PUBLIC).get_data(as_text=True)
    token = page.split('name="csrf-token" content="')[1].split('"')[0]
    assert client.post('/logout', base_url=PUBLIC, headers={'X-CSRF-Token': token, 'Origin': PUBLIC}).status_code == 302
    assert _replay(app_v2, stolen) == 401                                               # скопированная cookie — мертва
    assert phone2.get('/api/routes/garage', base_url=PUBLIC).status_code == 401         # и на другом устройстве
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.130').status_code == 302
    assert client.get('/api/routes/garage', base_url=PUBLIC).status_code == 200         # новый вход — как обычно


def test_logout_with_revoked_session_writes_nothing(client, app_v2, monkeypatch):
    _session_as(client, 'garage1')
    with client.session_transaction(base_url=PUBLIC) as s:
        s['pwv'] = '0' * 16                                   # отозванная / чужая cookie
    saves = []
    monkeypatch.setattr(app_v2, 'save_users', lambda data: saves.append(1) or True)
    r = client.post('/logout', base_url=PUBLIC, headers={'X-CSRF-Token': 'x' * 40, 'Origin': PUBLIC})
    assert r.status_code == 302 and saves == [] and 'session_gen' not in app_v2.test_users['garage1']


def test_password_change_revokes_sessions(client, app_v2):
    assert _login(client, 'garage1', PASSWORDS['garage1'], peer=TUNNEL, cf='203.0.113.140').status_code == 302
    garage_cookie = client.get_cookie('session', domain=_host(PUBLIC)).value
    admin = app_v2.app.test_client()
    assert _login(admin, 'boss', PASSWORDS['boss'], base=LAN).status_code == 302
    admin2 = app_v2.app.test_client()                         # тот же админ на втором компьютере
    assert _login(admin2, 'boss', PASSWORDS['boss'], base=LAN).status_code == 302
    page = admin.get('/settings', base_url=LAN).get_data(as_text=True)
    h = {'X-CSRF-Token': page.split('name="csrf-token" content="')[1].split('"')[0], 'Origin': LAN}
    r = admin.post('/api/users', base_url=LAN, headers=h, json={'username': 'garage1', 'role': 'garage',
                                                               'password': 'new-garage-pass-2'})
    assert r.status_code == 200, r.get_json()
    assert _replay(app_v2, garage_cookie) == 401                                       # телефон потерян → новый пароль
    r = admin.post('/api/users', base_url=LAN, headers=h, json={'username': 'boss', 'role': 'admin',
                                                               'password': 'new-boss-password'})
    assert r.status_code == 200
    assert admin.get('/api/users', base_url=LAN).status_code == 200                    # свой пароль — сессия остаётся
    assert admin2.get('/api/users', base_url=LAN).status_code == 401                   # прочие сессии — отозваны
    r = admin.post('/api/users', base_url=LAN, headers=h, json={'username': 'u', 'role': 'user', 'areas': ['01'],
                                                               'display_name': 'x'})
    assert r.status_code == 200 and admin.get('/api/users', base_url=LAN).status_code == 200  # без пароля — не трогаем


@pytest.mark.parametrize('age,ok', [(7 * 24 * 3600 - 60, True), (7 * 24 * 3600, False), (30 * 24 * 3600, False)])
def test_session_absolute_lifetime(client, app_v2, age, ok):
    _session_as(client, 'garage1')
    with client.session_transaction(base_url=PUBLIC) as s:
        s['iat'] = int(datetime.now().timestamp()) - age
        iat = s['iat']
    assert client.get('/api/routes/garage', base_url=PUBLIC).status_code == (200 if ok else 401)
    if ok:                                                    # работа не продлевает срок (абсолютный, не скользящий)
        with client.session_transaction(base_url=PUBLIC) as s:
            assert s['iat'] == iat


@pytest.mark.parametrize('broken', ['legacy', 'no_iat', 'str_iat', 'bad_pwv'])
def test_session_without_valid_stamp_rejected(client, app_v2, broken):
    _session_as(client, 'boss', base=LAN)
    with client.session_transaction(base_url=LAN) as s:
        if broken == 'legacy':                                # cookie до этой версии: только логин
            del s['pwv'], s['iat']
        elif broken == 'no_iat':
            del s['iat']
        elif broken == 'str_iat':
            s['iat'] = str(s['iat'])
        else:
            s['pwv'] = '0' * 16
    r = client.get('/settings', base_url=LAN)
    assert r.status_code == 302 and r.headers['Location'].startswith('/login')
    assert client.get('/api/users', base_url=LAN).status_code == 401


# ============================== ревью: открытый редирект через next ==============================

@pytest.mark.parametrize('url', ['/\t/evil.example', '/\n/evil.example', '/\r/evil.example', '/\x00/x', '/\x1f/evil',
                                 '\t//evil.example', '//evil.example', '/\\evil.example', '/\\/evil.example',
                                 'https://evil.example', 'javascript:alert(1)', 'evil.example', '', None, '/a\tb'])
def test_safe_next_url_rejects(app_v2, url):
    assert app_v2._safe_next_url(url) is None


@pytest.mark.parametrize('url', ['/', '/settings', '/areas?x=1&y=/z', '/routes/garage#top', '/a//b', '/%09/evil.example'])
def test_safe_next_url_accepts(app_v2, url):
    assert app_v2._safe_next_url(url) == url


@pytest.mark.parametrize('nxt,target', [('/\t/evil.example', '/'), ('/\n/evil.example', '/'), ('/settings', '/settings')])
def test_admin_login_next_redirect(client, nxt, target):
    h = _session_as(client, None, LAN)
    r = client.post('/login', base_url=LAN, headers={'Origin': LAN}, data={
        'username': 'boss', 'password': PASSWORDS['boss'], 'csrf_token': h['X-CSRF-Token'], 'next': nxt})
    assert r.status_code == 302 and r.headers['Location'] == target


@pytest.mark.parametrize('who', ['boss', 'u'])
def test_office_logout_unchanged_only_this_browser(client, app_v2, monkeypatch, who):
    """Офисные роли: «Выход» — как раньше, только этот браузер; users.json не трогается."""
    pc1 = app_v2.app.test_client()
    pc2 = app_v2.app.test_client()
    for c in (pc1, pc2):
        assert _login(c, who, PASSWORDS[who], base=LAN).status_code == 302
    copied = pc1.get_cookie('session', domain=_host(LAN)).value
    path = '/api/users' if who == 'boss' else '/areas'            # без ERP: 200 — вошёл, иначе 401 / на /login
    saves = []
    monkeypatch.setattr(app_v2, 'save_users', lambda data: saves.append(1) or True)
    page = pc1.get('/settings' if who == 'boss' else '/areas', base_url=LAN).get_data(as_text=True)
    token = page.split('name="csrf-token" content="')[1].split('"')[0]
    r = pc1.post('/logout', base_url=LAN, headers={'X-CSRF-Token': token, 'Origin': LAN})
    assert r.status_code == 302 and r.headers['Location'] == '/login'
    assert pc1.get(path, base_url=LAN).status_code in (401, 302)                       # этот браузер вышел
    assert pc2.get(path, base_url=LAN).status_code == 200                              # второй компьютер — как был
    assert _replay(app_v2, copied, path, base=LAN) == 200                              # как до ревью: cookie жива
    assert saves == [] and 'session_gen' not in app_v2.test_users[who]


# ============================== ревью: users.json пишется атомарно ==============================

USERS_ON_DISK = {'boss': {'role': 'admin', 'areas': [], 'password_hash': 'pbkdf2:sha256:1$x$y', 'display_name': 'Գլխավոր'}}


@pytest.fixture
def users_file(tmp_path, monkeypatch):
    """Настоящий save_users/load_users на временном users.json (не на рабочем)."""
    import app_v2 as module
    path = tmp_path / 'users.json'
    path.write_text(json.dumps(USERS_ON_DISK, ensure_ascii=False, indent=2), encoding='utf-8')
    monkeypatch.setattr(module, 'USERS_FILE', str(path))
    return module, path


def test_save_users_atomic_replace(users_file, monkeypatch):
    module, path = users_file
    if os.name == 'posix':
        os.chmod(path, 0o640)
    mode = os.stat(path).st_mode
    events, real_fsync, real_replace, real_copymode = [], os.fsync, os.replace, shutil.copymode
    monkeypatch.setattr(os, 'fsync', lambda fd: events.append(('fsync',)) or real_fsync(fd))
    monkeypatch.setattr(os, 'replace', lambda src, dst: events.append(('replace', src, dst)) or real_replace(src, dst))
    monkeypatch.setattr(shutil, 'copymode', lambda src, dst: events.append(('copymode', src, dst)) or real_copymode(src, dst))
    new = {**USERS_ON_DISK, 'garage1': {'role': 'garage', 'areas': [], 'password_hash': 'h', 'display_name': 'Գարեգին'}}
    assert module.save_users(new) is True
    assert path.read_text(encoding='utf-8') == json.dumps(new, ensure_ascii=False, indent=2)   # кодировка как раньше
    assert module.load_users() == new
    assert [e[0] for e in events] == ['fsync', 'copymode', 'replace']                  # на диске — до подмены
    tmp = events[-1][1]
    assert events[-1][2] == str(path) and os.path.dirname(tmp) == str(path.parent)     # та же папка: rename атомарен
    assert events[1][1:] == (str(path), tmp)                                           # права прежнего файла
    assert os.stat(path).st_mode == mode
    assert sorted(p.name for p in path.parent.iterdir()) == ['users.json']             # временного не осталось


@pytest.mark.parametrize('stage', ['write', 'fsync', 'replace'])
def test_save_users_crash_keeps_old_file(users_file, monkeypatch, stage):
    """Сбой посреди записи (диск полон / нет прав на подмену): прежний users.json цел, временный удалён."""
    module, path = users_file
    before = path.read_bytes()
    if stage == 'write':
        def half_then_fail(obj, f, **kw):
            f.write('{"boss": {"ro')
            raise OSError(28, 'No space left on device')
        monkeypatch.setattr(module.json, 'dump', half_then_fail)
    elif stage == 'fsync':
        monkeypatch.setattr(os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError(5, 'I/O error')))
    else:
        monkeypatch.setattr(os, 'replace', lambda src, dst: (_ for _ in ()).throw(PermissionError(13, 'denied')))
    assert module.save_users({'other': {'role': 'admin', 'password_hash': 'z'}}) is False
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == ['users.json']
    monkeypatch.undo()                                        # load_users — настоящий json; файл — прежний
    monkeypatch.setattr(module, 'USERS_FILE', str(path))
    assert module.load_users() == USERS_ON_DISK               # не «администратор по умолчанию»


def test_save_users_creates_missing_file(tmp_path, monkeypatch):
    import app_v2 as module
    path = tmp_path / 'users.json'
    monkeypatch.setattr(module, 'USERS_FILE', str(path))
    assert module.save_users(USERS_ON_DISK) is True and module.load_users() == USERS_ON_DISK
    assert sorted(p.name for p in tmp_path.iterdir()) == ['users.json']

"""Роль «Гараж» (ответ владельца №53): после входа — только журнал гаража; Настройки → Пользователи; навигация раздела."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_optimizer import _no_road_map, make_snapshot  # noqa: E402,F401

DAY = '2026-10-01'
NOW = datetime(2026, 10, 3, 10, 0)


# ============================== роль «Гараж» (app_v2) ==============================

@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    """Настоящий app_v2 (вход, гейт ролей, CSRF); база маршрутов — временная, снимок ERP — тестовый."""
    import app_v2
    from werkzeug.security import generate_password_hash
    users = {'garage1': {'role': 'garage', 'areas': [], 'password_hash': generate_password_hash('pw-garage'),
                         'display_name': 'Գարեգին'},
             'u': {'role': 'user', 'areas': ['01'], 'password_hash': 'x'},
             'odd': {'role': 'superuser', 'areas': [], 'password_hash': 'x'},
             'boss': {'role': 'admin', 'areas': [], 'password_hash': 'x'}}
    monkeypatch.setattr(app_v2, 'load_users', lambda: users)
    monkeypatch.setattr(app_v2, 'save_users', lambda data: users.update(data) or True)
    state = app_v2.app.extensions['route_optimizer']
    monkeypatch.setattr(state, 'store', st.Store(str(tmp_path / 'routes.db')))
    monkeypatch.setattr(state, 'snapshots', SnapshotCache(lambda: make_snapshot()))
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    state.store.save(st.Changes(dict(st.DEFAULT_SETTINGS), False, None,
                                (st.Truck('CAR1', 10000.0, 30.0, active=True),), ()), 'qa')
    c = app_v2.app.test_client()
    c.users = users
    return c


def _login_as(c, name):
    with c.session_transaction() as s:
        s.clear()
        s['username'] = name
        s['_office_csrf'] = 'x' * 40
    return {'X-CSRF-Token': 'x' * 40}


GARAGE_ENTRY = {'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'Կոճղակներ', 'amount_amd': 85_000,
                'odometer_km': 120_500}


def test_garage_role_allowed_paths(dashboard):
    c = dashboard
    h = _login_as(c, 'garage1')
    page = c.get('/routes/garage')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'Ավտոտնակ' in html and 'js/routes_garage.js' in html and 'Գարեգին' in html
    for link in ('href="/routes/settings"', 'href="/routes"', 'href="/areas"', 'href="/customers-grid"',
                 'href="/settings"', 'href="/courier"', 'href="/"'):
        assert link not in html, link                                          # ссылок на другие страницы нет
    assert c.get('/api/routes/garage').status_code == 200
    r = c.post('/api/routes/garage/entries', json=GARAGE_ENTRY, headers=h)
    assert r.status_code == 200, r.get_json()
    listed = c.get('/api/routes/garage/entries').get_json()
    assert listed['total_amd'] == 85_000 and 'created_by' not in listed['entries'][0]     # логины — не гаражу
    assert c.post('/api/routes/garage/odometers', json={'items': [{'car_code': 'CAR1', 'day': '2026-10-01',
                                                                     'odometer_km': 121_000}]}, headers=h).status_code == 200
    assert c.post('/api/routes/garage/entries/delete', json={'id': r.get_json()['id']}, headers=h).status_code == 200
    assert c.get('/static/css/routes_garage.css').status_code == 200
    # удалённые — не начальнику гаража
    shown = c.get('/api/routes/garage/entries?deleted=1').get_json()['entries']
    assert [e['kind'] for e in shown] == ['odometer'] and not any(e['deleted_at'] for e in shown)
    assert c.get('/api/routes/garage').get_json()['admin'] is False
    assert c.post('/api/routes/garage/entries', json=GARAGE_ENTRY).status_code == 403   # без CSRF — нет
    r = c.post('/logout', headers=h)
    assert r.status_code == 302 and '/login' in r.headers['Location']


@pytest.mark.parametrize('path', ['/api/routes/settings', '/api/routes/dispatch?date=2026-10-01', '/api/routes/overview',
                                  '/api/users', '/api/courier/admin/today', '/api/customers', '/api/sales-areas',
                                  '/api/routes/garage-x', '/api/routes/garagex/entries', '/api/routes/learning'])
def test_garage_role_denied_api(dashboard, path):
    _login_as(dashboard, 'garage1')
    r = dashboard.get(path)
    assert r.status_code == 403 and r.get_json()['success'] is False


@pytest.mark.parametrize('path', ['/', '/routes', '/routes/settings', '/routes/dispatch', '/areas', '/customers-grid',
                                  '/settings', '/courier', '/routes/garage/', '/routes/garage/x'])
def test_garage_role_pages_redirect_to_garage(dashboard, path):
    _login_as(dashboard, 'garage1')
    r = dashboard.get(path)
    assert r.status_code == 302 and r.headers['Location'].endswith('/routes/garage')


def test_garage_role_cannot_mutate_elsewhere(dashboard):
    c = dashboard
    h = _login_as(c, 'garage1')
    for path, body in (('/api/routes/settings', {'trucks': []}), ('/api/users', {'username': 'x', 'role': 'admin'}),
                       ('/api/routes/dispatch/build', {'date': DAY, 'trucks': ['CAR1']}),
                       ('/api/routes/customer-vehicles', {'customer_id': 1})):
        assert c.post(path, json=body, headers=h).status_code == 403, path
    assert c.put('/api/routes/garage/entries', json=GARAGE_ENTRY, headers=h).status_code in (403, 405)
    assert 'x' not in c.users


def test_garage_login_redirects_to_garage(dashboard):
    c = dashboard
    r = c.post('/login?next=/settings', data={'username': 'garage1', 'password': 'pw-garage',
                                             'csrf_token': _csrf_from_login(c)})
    assert r.status_code == 302 and r.headers['Location'].endswith('/routes/garage')


def _csrf_from_login(c):
    html = c.get('/login').get_data(as_text=True)
    return html.split('name="csrf_token" value="')[1].split('"')[0]


def test_admin_and_territory_roles_unchanged(dashboard):
    c = dashboard
    _login_as(c, 'boss')
    page = c.get('/routes/garage').get_data(as_text=True)
    assert 'href="/routes/settings"' in page and 'href="/routes/dispatch"' in page        # навигация раздела
    assert c.get('/api/routes/garage').get_json()['admin'] is True
    h = _login_as(c, 'boss')
    assert c.post('/api/routes/garage/entries', json=GARAGE_ENTRY, headers=h).status_code == 200
    assert c.get('/api/routes/garage/entries').get_json()['entries'][0]['created_by'] == 'boss'
    assert c.get('/api/routes/settings').status_code == 200
    for name in ('u', 'odd'):                                                  # «по территориям» и неизвестная роль
        _login_as(c, name)
        assert c.get('/api/routes/garage').status_code == 403
        assert c.get('/api/routes/settings').status_code == 403
        r = c.get('/routes/garage')
        assert r.status_code == 302 and r.headers['Location'].endswith('/areas')
    _login_as(c, 'u')
    assert c.get('/areas').status_code != 403


def test_routes_pages_link_garage(dashboard):
    _login_as(dashboard, 'boss')
    for path in ('/routes', '/routes/optimize', '/routes/learning', '/routes/settings'):
        assert 'href="/routes/garage"' in dashboard.get(path).get_data(as_text=True), path


def test_users_api_accepts_garage_role(dashboard):
    c = dashboard
    h = _login_as(c, 'boss')
    r = c.post('/api/users', json={'username': 'g2', 'password': 'pw-garage-10', 'role': 'garage', 'areas': ['01'],
                                   'display_name': 'Գ'}, headers=h)
    assert r.status_code == 200, r.get_json()
    assert c.users['g2']['role'] == 'garage' and c.users['g2']['areas'] == []
    assert c.post('/api/users', json={'username': 'u2', 'password': 'pw', 'role': 'user'}, headers=h).status_code == 400
    assert c.post('/api/users', json={'username': 'u3', 'password': 'pw', 'role': 'root', 'areas': ['01']},
                  headers=h).status_code == 200 and c.users['u3']['role'] == 'user'
    roles = {u['username']: u['role'] for u in c.get('/api/users').get_json()['data']}
    assert roles['g2'] == 'garage'

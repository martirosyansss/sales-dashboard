import sqlite3
from pathlib import Path
from urllib.parse import urlencode

import pytest
from harness import create_app, gates

API = '/api/routes/garage/refuels'

@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path)

def get(app, **params):
    return app.test_client().get(API + '?' + urlencode(params))

def test_default_current_month_and_unknown_amount(app):
    d = get(app).json
    assert d['since'] == '2026-10-01' and d['until'] == '2026-10-27'
    assert d['totals'] == dict(count=2, cars=2, liters=65, amount_amd=24000, missing_amount=1)
    assert [x['car_code'] for x in d['entries']] == ['222BB22', '111AA11']

def test_correction_chain_uses_original_day_even_if_received_later(app):
    d = get(app, since='2026-09-30', until='2026-09-30').json
    assert d['total'] == 1 and d['totals']['liters'] == 13
    assert d['entries'][0]['correction'] and d['entries'][0]['day'] == '2026-09-30'
    assert d['entries'][0]['sent_at'].startswith('2026-10-28')

def test_show_superseded_preserves_totals(app):
    d = get(app, since='2026-09-01', until='2026-09-30', corrected=1).json
    assert d['total'] == 3 and d['totals']['count'] == 1 and d['totals']['amount_amd'] == 6500
    assert sum(x['superseded'] for x in d['entries']) == 2

def test_vehicle_filter_and_missing_amount_is_not_zero(app):
    d = get(app, car='111AA11').json
    assert d['total'] == 1 and d['totals']['liters'] == 25
    assert d['totals']['amount_amd'] is None and d['totals']['missing_amount'] == 1
    assert d['cars'] == ['111AA11', '222BB22', '333CC33']

def test_pagination_keeps_full_result_totals(app):
    first = get(app, since='2026-08-01', until='2026-08-31').json
    last = get(app, since='2026-08-01', until='2026-08-31', page=2).json
    assert first['total'] == 55 and len(first['entries']) == 50 and len(last['entries']) == 5
    assert first['totals'] == last['totals']
    assert first['totals']['liters'] == 60.5 and first['totals']['amount_amd'] == 3052.5
    assert not {x['id'] for x in first['entries']} & {x['id'] for x in last['entries']}
    assert get(app, since='2026-08-01', until='2026-08-31', page=999).json['page'] == 2

@pytest.mark.parametrize('params', [dict(since='bad'), dict(since='2026-10-28'), dict(until='2026-02-30'),
    dict(since='2024-01-01'), dict(page='0'), dict(page='no'), dict(page='9'*5000), dict(page='１２'),
    dict(car='x'*65), dict(car='\x01'), dict(corrected='yes')])
def test_invalid_filters_are_rejected(app, params):
    assert get(app, **params).status_code == 400

def test_empty_and_single_day(app):
    assert get(app, since='2026-10-20', until='2026-10-20').json['total'] == 1
    d = get(app, car='unknown').json
    assert d['entries'] == [] and d['totals']['count'] == 0 and d['totals']['amount_amd'] == 0

@pytest.mark.parametrize('role,status', [('admin',200),('garage',200),('user',403),('warehouse',403),('anonymous',401)])
def test_role_access(app, role, status):
    client = app.test_client()
    for path in ('/routes/refuels', API):
        assert client.get(path, headers={'X-QA-Role': role}).status_code == status

def test_actual_auth_allowlists_keep_exact_boundaries():
    env = gates()
    for method in ('GET', 'HEAD'):
        assert env['_garage_path_allowed']('/routes/refuels', method)
        assert env['_public_path_allowed']('/routes/refuels', method)
        assert env['_public_path_allowed']('/static/js/routes_refuels.js', method)
        assert env['_public_path_allowed'](API, method)
    for path in ('/routes/refuels-extra','/routes/refuels/extra','/static/js/routes_refuels-evil.js', '/api/courier/admin/photos/abc'):
        assert not env['_public_path_allowed'](path, 'GET')
    assert not env['_garage_path_allowed']('/routes/refuels', 'POST')
    assert not env['_public_path_allowed']('/routes/refuels', 'POST')

def test_read_only_requests_and_post_not_allowed(app):
    store = app.extensions['courier'].store
    def snapshot():
        with sqlite3.connect(store.path) as conn:
            return '\n'.join(conn.iterdump())
    before = snapshot()
    for params in ({}, {'corrected':1}, {'since':'2026-09-01','until':'2026-09-30'}):
        assert get(app, **params).status_code == 200
    assert app.test_client().post(API, json={}).status_code == 405
    assert snapshot() == before

def test_only_refueling_photos_and_path_containment(app):
    client = app.test_client()
    assert client.get(API + '/photos/20000000-0000-4000-8000-000000000001').status_code == 200
    for photo in ('20000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000003', 'bad-id'):
        assert client.get(API + '/photos/' + photo).status_code == 404

def test_no_cache_and_failure_does_not_become_empty_success(app):
    assert 'no-store' in get(app).headers['Cache-Control']
    app.extensions['courier'].store.refuels = lambda: (_ for _ in ()).throw(OSError('QA unavailable'))
    response = get(app)
    assert response.status_code == 503 and response.json['success'] is False

def test_garage_navigation_and_entrypoint(app):
    client = app.test_client()
    page = client.get('/routes/refuels', headers={'X-QA-Role':'garage'}).data.decode()
    assert 'href="/routes/refuels" aria-current="page"' in page
    garage = client.get('/routes/garage', headers={'X-QA-Role':'garage'}).data.decode()
    assert 'href="/routes/refuels" class="rt-btn' in garage

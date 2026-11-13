"""Отключение машины в настройках не скрывает её из учёта гаража."""
import pytest

from test_route_garage import gclient, client, _no_road_map  # noqa: F401
from test_garage_norm import _norm_client
from route_optimizer import views
from route_optimizer.erp import ErpError
from route_optimizer.snapshot import SnapshotCache


@pytest.mark.parametrize('erp_down', [False, True])
def test_disabled_vehicle_without_entries_remains_visible(gclient, erp_down):
    c = gclient
    state = c.application.extensions['route_optimizer']
    assert c.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR2', 'active': False}]}).status_code == 200
    before = state.store.load()
    if erp_down:
        def unavailable():
            raise ErpError('offline')
        state.snapshots = SnapshotCache(unavailable)
    body = c.get('/api/routes/garage').get_json()
    car = next(t for t in body['trucks'] if t['car_code'] == 'CAR2')
    summary = next(t for t in body['summary'] if t['car_code'] == 'CAR2')
    assert car['active'] is False and car['last_km'] is None
    assert summary['garage'] is None and body['journal_empty']
    assert state.store.load() == before
    if not erp_down:
        snap, _ = state.snapshots.cached()
        assert 'CAR2' not in views._ready_trucks(snap, before)
    result = c.post('/api/routes/garage/entries', json={
        'car_code': 'CAR2', 'day': '2026-10-01', 'kind': 'repair',
        'what': 'Արգելակներ', 'amount_amd': 25000, 'odometer_km': 120000})
    assert result.status_code == 200
    rows = c.get('/api/routes/garage').get_json()['summary']
    row = next(t for t in rows if t['car_code'] == 'CAR2')
    assert row['active'] is False and row['garage']['repair_amd'] == 25000
    assert state.store.load() == before


def test_disabled_vehicle_without_month_data_remains_in_norm(client, monkeypatch):
    state = _norm_client(client, monkeypatch, refuels=())
    state.fleet_facts = None
    assert client.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR2', 'active': False}]}).status_code == 200
    before = state.store.load()
    body = client.get('/api/routes/garage/norm?month=2026-10').get_json()
    row = next(t for t in body['trucks'] if t['car_code'] == 'CAR2')
    assert row['active'] is False and row['days'] == []
    assert row['km']['days'] == 0 and row['fuel']['reason'] == 'no_refuels'
    assert state.store.load() == before

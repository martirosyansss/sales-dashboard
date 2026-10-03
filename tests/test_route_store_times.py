"""Общая карточка магазина: атомарность машин/времени и реальные ограничения рейса."""
import sqlite3

import pytest

from route_optimizer import store as st
from route_optimizer.vehicle_access import VehicleAccess
from test_route_optimizer import _dispatch_setup, _dorder, client, _no_road_map


@pytest.mark.parametrize('window', [
    {'kind': 'at', 't1': 660, 'tol': 0},
    {'kind': 'between', 't1': 600, 't2': 720},
    {'kind': 'before', 't1': 720},
    {'kind': 'after', 't1': 660},
])
def test_combined_settings_reach_search_and_actual_plan(client, window):
    _dispatch_setup(client, [_dorder(1, 101, 500)])
    access = {'mode': 'allow', 'trucks': ['CAR2']}
    assert client.post('/api/routes/customer-vehicles', json={
        'customer_id': 101, 'access': access, 'window': window}).status_code == 200
    row = client.get('/api/routes/customer-vehicles?q=C101').get_json()['customers'][0]
    expected, err = st.check_window(window)
    assert err is None and row['window'] == expected.to_json() and row['vehicle_access'] == access
    response = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert response.status_code == 200, response.get_json()
    plan = response.get_json()['plan']
    assigned = [(t, s) for t in plan['trucks'] for trip in t['trips'] for s in trip['stops'] if s['customer_id'] == 101]
    assert len(assigned) == 1, plan
    truck, stop = assigned[0]
    assert truck['car_code'] == 'CAR2' and stop['window'] == expected.to_json()
    assert not stop['window_miss']
    lo, hi = expected.span()
    hours, minutes = map(int, stop['eta'].split(':'))
    assert lo <= hours * 60 + minutes <= hi


def test_separate_editors_preserve_other_constraint_and_combined_reset(client):
    _dispatch_setup(client, [])
    payload = {'customer_id': 103, 'access': {'mode': 'deny', 'trucks': ['CAR1']},
               'window': {'kind': 'at', 't1': 660, 'tol': 15}}
    assert client.post('/api/routes/customer-vehicles', json=payload).status_code == 200
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 103, 'access': None}).status_code == 200
    row = client.get('/api/routes/customer-vehicles').get_json()['customers'][0]
    assert row['customer_id'] == 103 and row['window']['tol'] == 15 and row['vehicle_access'] is None
    payload['access'] = {'mode': 'allow', 'trucks': ['CAR2']}
    assert client.post('/api/routes/customer-vehicles', json=payload).status_code == 200
    assert client.post('/api/routes/customer-window', json={'customer_id': 103,
                       'window': {'kind': 'before', 't1': 720}}).status_code == 200
    row = client.get('/api/routes/customer-vehicles?q=C103').get_json()['customers'][0]
    assert row['vehicle_access'] == payload['access'] and row['window']['kind'] == 'before'
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 103, 'access': None, 'window': None}).status_code == 200
    assert client.get('/api/routes/customer-vehicles').get_json()['customers'] == []


@pytest.mark.parametrize('invalid', [
    {'kind': 'between', 't1': 720, 't2': 600},
    {'kind': 'at', 't1': 660, 'tol': -1},
    {'kind': 'before', 't1': 1440},
    {'kind': 'at', 't1': True, 'tol': 0},
    [],
])
def test_bad_time_cannot_partially_save_vehicles(client, invalid):
    _dispatch_setup(client, [])
    saved = {'customer_id': 103, 'access': {'mode': 'deny', 'trucks': ['CAR1']},
             'window': {'kind': 'before', 't1': 720}}
    assert client.post('/api/routes/customer-vehicles', json=saved).status_code == 200
    before = client.get('/api/routes/customer-vehicles?q=C103').get_json()
    assert client.post('/api/routes/customer-vehicles', json={
        'customer_id': 103, 'access': None, 'window': invalid}).status_code == 400
    assert client.get('/api/routes/customer-vehicles?q=C103').get_json() == before


def test_transaction_failure_rolls_back_both_settings(tmp_path):
    path = tmp_path / 'settings.sqlite'
    store = st.Store(path)
    store.save_customer_constraints(101, VehicleAccess('allow', ('CAR1',)), st.CustomerWindow('before', 720), 'qa')
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER reject_new_time BEFORE UPDATE ON customer_window BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(st.StoreError):
        store.save_customer_constraints(101, VehicleAccess('deny', ('CAR2',)), st.CustomerWindow('at', 660, tol=0), 'qa')
    bundle = store.load()
    assert bundle.vehicle_access[101] == VehicleAccess('allow', ('CAR1',))
    assert bundle.windows[101] == st.CustomerWindow('before', 720)


def test_settings_search_includes_all_vehicles_and_exact_customer_without_orders(client):
    _dispatch_setup(client, [])
    data = client.get('/api/routes/customer-vehicles?customer_id=103&q=C101').get_json()
    assert [s['customer_id'] for s in data['customers']] == [103]
    assert {'CAR1', 'CAR2'} <= {t['car_code'] for t in data['vehicles']}
    assert all('name' in t for t in data['vehicles'])
    assert client.get('/api/routes/customer-vehicles?customer_id=999999').get_json()['customers'] == []
    for bad in ('0', '-1', '2147483648', 'abc', '1' * 100):
        assert client.get('/api/routes/customer-vehicles?customer_id=' + bad).status_code == 400

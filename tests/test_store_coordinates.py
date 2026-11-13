"""Синтетические проверки обновления ERP и единой точки офиса/APK; без рабочих баз и сети."""
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from courier import day as dy, routes_link as rl
from route_optimizer import evaluate, views
from route_optimizer.snapshot import SnapshotCache
from test_route_optimizer import client, make_snapshot, _dispatch_setup, _dorder  # noqa: F401


def _find_stop(value, cid=101):
    if isinstance(value, dict):
        if value.get('customer_id') == cid and 'coord_source' in value:
            return value
        for part in value.values():
            found = _find_stop(part, cid)
            if found:
                return found
    if isinstance(value, list):
        for part in value:
            found = _find_stop(part, cid)
            if found:
                return found


def _office(client):
    _dispatch_setup(client, [_dorder(1, 101, 100.0)])
    state = client.application.extensions['route_optimizer']
    timer = [1000.0]
    current = [make_snapshot('old')]
    calls = []

    def load():
        calls.append(current[0].id)
        return current[0]

    state.snapshots = SnapshotCache(load, clock=lambda: timer[0])
    result = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1']})
    assert result.status_code == 200, result.get_json()
    initial = state.store.load_dispatch('2026-10-01')
    old = current[0]
    current[0] = replace(old, id='new', data_as_of=old.data_as_of + timedelta(hours=1),
                         erp_points={**old.erp_points, 1001: (101, (40.181, 44.501))})
    return state, timer, calls, initial


def test_explicit_refresh_reads_new_coordinates_without_changing_plan(client):
    state, timer, calls, initial = _office(client)
    url = '/api/routes/dispatch?date=2026-10-01'
    assert _find_stop(client.get(url).get_json())['lat'] == 40.18
    timer[0] += 61  # соблюдаем защиту ERP: не чаще раза в минуту
    body = client.get(url + '&refresh=1').get_json()
    stop = _find_stop(body)
    assert (stop['lat'], stop['lon'], stop['coord_source']) == (40.181, 44.501, 'erp')
    assert stop['coord_loaded_at'] == '2026-09-30T13:00:00'
    assert stop['coord_stale'] is False
    assert calls == ['old', 'new']
    assert state.store.load_dispatch('2026-10-01') == initial


def test_normal_read_expires_coordinate_snapshot(client):
    state, timer, calls, initial = _office(client)
    timer[0] += 601
    body = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert _find_stop(body)['lat'] == 40.181
    assert calls == ['old', 'new']
    assert state.store.load_dispatch('2026-10-01') == initial


def test_auto_poll_detects_only_coordinate_changes_without_touching_plan(client):
    state, timer, calls, initial = _office(client)
    url = '/api/routes/dispatch?date=2026-10-01'
    before = client.get(url).get_json()
    timer[0] += 601
    status = client.get('/api/routes/dispatch/status?date=2026-10-01').get_json()
    assert status['coord_sig'] != before['coord_sig']
    assert status['orders_sig'] == before['orders_sig'] and status['rev'] == before['rev']
    assert calls == ['old', 'new']
    after = client.get(url).get_json()
    assert after['coord_sig'] == status['coord_sig']
    assert _find_stop(after)['lat'] == 40.181
    assert state.store.load_dispatch('2026-10-01') == initial


def test_auto_poll_detects_manual_coordinates_and_stays_stable_between_reads(client):
    state, timer, calls, initial = _office(client)
    url = '/api/routes/dispatch/status?date=2026-10-01'
    before = client.get(url).get_json()
    state.store.save_geo_override(101, (40.185, 44.505), 'synthetic-logistician')
    after = client.get(url).get_json()
    assert after['coord_sig'] != before['coord_sig']
    assert after['orders_sig'] == before['orders_sig'] and after['rev'] == before['rev']
    assert client.get(url).get_json()['coord_sig'] == after['coord_sig']
    assert state.store.load_dispatch('2026-10-01') == initial


def test_failed_refresh_warning_survives_normal_reads_until_success(client):
    state, timer, calls, initial = _office(client)
    url = '/api/routes/dispatch?date=2026-10-01'
    original_loader = state.snapshots._loader
    healthy_sig = client.get(url).get_json()['coord_sig']
    timer[0] += 61

    def fail():
        raise views.ErpError('synthetic outage')

    state.snapshots._loader = fail
    failed = client.get(url + '&refresh=1').get_json()
    assert failed['coord_sig'] != healthy_sig
    assert _find_stop(failed)['coord_stale'] is True
    for delta in (0, 61):
        timer[0] += delta
        normal = client.get(url).get_json()
        assert normal['coord_sig'] == failed['coord_sig']
        assert _find_stop(normal)['coord_stale'] is True
        assert state.snapshots.cached()[1] is True
    state.snapshots._loader = original_loader
    restored = client.get(url + '&refresh=1').get_json()
    assert _find_stop(restored)['coord_stale'] is False
    assert _find_stop(restored)['lat'] == 40.181
    assert restored['coord_sig'] != failed['coord_sig']
    assert state.store.load_dispatch('2026-10-01') == initial


def test_failed_coordinate_refresh_marks_old_erp_point_as_stale(client):
    state, timer, calls, initial = _office(client)
    timer[0] += 61

    def fail():
        raise views.ErpError('synthetic outage')

    state.snapshots._loader = fail
    body = client.get('/api/routes/dispatch?date=2026-10-01&refresh=1').get_json()
    stop = _find_stop(body)
    assert stop['lat'] == 40.18 and stop['coord_stale'] is True
    assert stop['coord_loaded_at'] == '2026-09-30T12:00:00'
    assert state.store.load_dispatch('2026-10-01') == initial


@pytest.mark.parametrize('manual,driver,erp,gps,source', [
    ((40.183, 44.503), (40.184, 44.504), (40.18, 44.50), (40.181, 44.501), 'manual'),
    (None, (40.184, 44.504), (40.18, 44.50), (40.181, 44.501), 'driver'),
    (None, None, (40.18, 44.50), (40.181, 44.501), 'erp'),
    (None, None, (40.18, 44.50), (40.23, 44.55), 'gps'),
    (None, None, None, (40.181, 44.501), 'gps'),
    (None, None, None, None, 'none'),
])
def test_office_and_apk_choose_the_same_point(manual, driver, erp, gps, source):
    snap = replace(make_snapshot(), erp_points={1001: (101, erp)} if erp else {},
                   gps_points={101: gps} if gps else {})
    view = rl.RoutesView(geo_overrides={101: manual} if manual else {},
                         driver_points={101: driver} if driver else {})
    data = SimpleNamespace(docs=[SimpleNamespace(customer_id=101)],
                           customers={101: SimpleNamespace(erp_point=erp)}, gps=snap.gps_points)
    office = evaluate.visit_coord(snap, 101, 0, view.geo_overrides, view.driver_points)
    apk = dy._resolve_coords(data, view)[101]
    assert office == apk and apk.source == source


def test_routes_view_shares_office_driver_cache_and_rounding(client):
    state = client.application.extensions['route_optimizer']
    calls = []

    class DriverSource:
        def points(self):
            calls.append(1)
            return {101: (40.1850049, 44.5050049, 3)}

    state.driver_geo = DriverSource()
    office = views._bundle(state)
    apk = rl.routes_view(state, date(2026, 10, 1))
    assert apk.driver_points == office.driver_points == {101: (40.185, 44.505)}
    assert calls == [1]


def test_apk_source_metadata_and_retrieval_time_do_not_churn_version(tmp_path):
    from courier.store import Store
    data, view, loaded = dy.demo_data()
    cid = data.docs[0].customer_id
    view = replace(view, driver_points={cid: (40.185, 44.505)})
    store = Store(str(tmp_path / 'courier.db'))
    first = dy.day_payload(data, view, store, loaded)
    second = dy.day_payload(data, view, store, loaded + timedelta(minutes=1))
    stop = next(s for s in first['stops'] if s['customer']['id'] == cid)
    assert stop['coord_source'] == 'driver'
    assert (stop['lat'], stop['lon']) == (40.185, 44.505)
    assert first['version'] == second['version']
    assert first['loaded_at'] != second['loaded_at']

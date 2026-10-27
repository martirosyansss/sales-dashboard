"""Isolated dispatch regression tests; no production database or ERP connection."""
import copy
import importlib
import sys
import types
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3]
package = types.ModuleType('route_optimizer')
package.__path__ = [str(SOURCE / 'route_optimizer')]
sys.modules.setdefault('route_optimizer', package)
dp = importlib.import_module('route_optimizer.dispatch')
fl = importlib.import_module('route_optimizer.fleet')
Norms = importlib.import_module('route_optimizer.evaluate').Norms
VehicleAccess = importlib.import_module('route_optimizer.vehicle_access').VehicleAccess
DAY = date(2026, 10, 26)


def scenario():
    trucks = {c: fl.FleetTruck(c, c, 1000, 15, True) for c in ('A', 'B', 'C')}
    norms = Norms(480, 1.2, 40, 60, (40.18, 44.51), 10, 0, 0)
    ctx = dp.DayContext(DAY, (40.18, 44.51), trucks, norms, fl.TruckNorms(480, 8, 6), 540)
    stops = []
    for cid in (1, 2, 3, 4):
        o = dp.DispatchOrder(str(cid), str(cid), DAY, cid, 1, '', 10000, 100, None)
        stops.append(dp.Stop(cid, (40.18 + cid * .005, 44.51), 'synthetic', 100, 10000, (o,), 1))
    draft = dp.Draft(trucks=['A', 'B', 'C'], trips=[dp.DraftTrip(1, 'A', [1, 2]),
        dp.DraftTrip(2, 'B', [3], pinned=True), dp.DraftTrip(3, 'A', [4], not_before=600)], next_id=4)
    return ctx, stops, draft


def swap(ctx, stops, draft, **kwargs):
    return dp.apply_edit(ctx, stops, draft, {'action': 'swap_trucks', 'truck': 'A', 'other_truck': 'B'},
                         {o.isn for s in stops for o in s.orders}, **kwargs)


def test_complete_exchange_preserves_order_and_undo():
    ctx, stops, draft = scenario()
    draft.locks = {1: 'A', 3: 'B'}
    draft.seats = {'A': 'Driver A', 'B': 'Driver B'}
    draft.approved = {'at': '2026-10-25T10:00:00', 'by': 'qa', 'pinned': [1]}
    draft.released = {'at': '2026-10-25T10:00:00', 'by': 'qa'}
    draft.same_day_trips = {3}
    dp.send(draft, '2026-10-25T10:00:00', 'qa', orders=dp.published_orders(draft, stops))
    before = copy.deepcopy(draft.to_json())
    result = swap(ctx, stops, draft)
    assert draft.to_json() == before
    assert [(t.id, t.truck, t.stops) for t in result.trips] == [(1, 'B', [1, 2]), (2, 'A', [3]), (3, 'B', [4])]
    assert result.trips[1].pinned and result.trips[2].not_before == 600
    assert result.seats == draft.seats and result.approved == draft.approved
    assert result.sent == draft.sent and result.released == draft.released
    assert result.same_day_trips == {3}
    assert result.locks == {1: 'B', 2: 'B', 3: 'A', 4: 'B'}
    assert dp.published_orders(result, stops)['1'][0] == 'B'
    undo = dp.apply_edit(ctx, stops, result, {'action': 'undo'}, set())
    assert undo.to_json() == before


@pytest.mark.parametrize('a,b', [('A','A'), ('A','X'), ('X','B'), (None,'B'), ([], 'B'), ('A',{}), ('','B')])
def test_invalid_selection_is_atomic(a, b):
    ctx, stops, draft = scenario()
    draft.undo = {'trucks': ['old']}
    before = copy.deepcopy(draft.to_json())
    with pytest.raises(dp.DispatchError):
        dp.apply_edit(ctx, stops, draft, {'action': 'swap_trucks', 'truck': a, 'other_truck': b}, set())
    assert draft.to_json() == before


def test_unmanned_and_unselected_rejected():
    ctx, stops, draft = scenario()
    draft.unmanned['B'] = 'absent'
    with pytest.raises(dp.DispatchError):
        swap(ctx, stops, draft)
    draft.unmanned = {}
    draft.trucks.remove('B')
    with pytest.raises(dp.DispatchError):
        swap(ctx, stops, draft)


@pytest.mark.parametrize('code', ['A', 'B'])
def test_loaded_exchange_requires_explicit_confirmation(code):
    ctx, stops, draft = scenario()
    trip = next(t for t in draft.trips if t.truck == code)
    trip.loaded = {'at': '2026-10-26T08:00:00', 'by': 'warehouse', 'pin': True}
    before = copy.deepcopy(draft.to_json())
    with pytest.raises(dp.LoadedEdit):
        swap(ctx, stops, draft)
    assert draft.to_json() == before
    result = swap(ctx, stops, draft, loaded_ok=True)
    assert next(t for t in result.trips if t.id == trip.id).loaded == trip.loaded


@pytest.mark.parametrize('rule', ['capacity', 'vehicle', 'center', 'window', 'end'])
def test_invalid_exchange_does_not_mutate_draft(rule):
    ctx, stops, draft = scenario()
    if rule == 'capacity':
        ctx.trucks['B'] = replace(ctx.trucks['B'], capacity_kg=150)
    elif rule == 'vehicle':
        ctx = replace(ctx, vehicle_access={1: VehicleAccess('allow', ('A',))})
    elif rule == 'center':
        ctx.trucks['B'] = replace(ctx.trucks['B'], center_ok=False)
        ctx = replace(ctx, center_zone=((40,44), (41,44), (41,45), (40,45)))
    elif rule == 'window':
        ctx = replace(ctx, tn=replace(ctx.tn, pace={'B': (1, 10)}), windows={1: (540, 542)})
    elif rule == 'end':
        ctx = replace(ctx, tn=replace(ctx.tn, work_minutes=100, pace={'B': (10, 10)}))
    before = copy.deepcopy(draft.to_json())
    with pytest.raises(dp.DispatchError):
        swap(ctx, stops, draft)
    assert draft.to_json() == before


def test_idle_vehicle_receives_all_routes():
    ctx, stops, draft = scenario()
    draft.trips = [t for t in draft.trips if t.truck == 'A']
    result = swap(ctx, stops, draft)
    assert all(t.truck == 'B' for t in result.trips)
    draft.trips = []
    with pytest.raises(dp.DispatchError):
        swap(ctx, stops, draft)


def test_split_visits_keep_exact_total_weight():
    ctx, stops, draft = scenario()
    draft.trips[2].stops = [1, 4]
    result = swap(ctx, stops, draft)
    kg, owner = dp.trip_kgs(result, {s.customer_id: s for s in stops})
    assert owner['1'] == 'B'
    assert kg.get((1, 1), 50) + kg.get((3, 1), 50) == 100
    assert sum(s.kg for s in stops) == sum(t['kg'] for truck in dp.plan_view(ctx, stops, result, info)['trucks'] for t in truck['trips'])


def info(stop):
    return {'customer_id': stop.customer_id, 'name': f'QA {stop.customer_id}', 'address': 'QA',
            'code': str(stop.customer_id), 'lat': stop.point[0], 'lon': stop.point[1],
            'orders': [{'isn': o.isn, 'kg': o.kg, 'revenue': o.revenue} for o in stop.orders]}


@pytest.fixture
def api_client(tmp_path):
    from harness import create_app
    app = create_app(tmp_path)
    app.testing = True
    return app.test_client()


def test_api_exchange_persists_once_and_stale_revision_is_rejected(api_client):
    initial = api_client.get('/api/routes/dispatch').get_json()
    payload = {'date': DAY.isoformat(), 'rev': initial['rev'], 'action': 'swap_trucks', 'truck': 'A', 'other_truck': 'B'}
    result = api_client.post('/api/routes/dispatch/edit', json=payload)
    assert result.status_code == 200, result.get_json()
    data = result.get_json()
    assert data['rev'] == initial['rev'] + 1
    assert next(t for t in data['plan']['trucks'] if t['car_code'] == 'B')['stops'] == 3
    assert api_client.post('/api/routes/dispatch/edit', json=payload).status_code == 409
    assert api_client.get('/api/routes/dispatch').get_json()['rev'] == data['rev']
    undo = api_client.post('/api/routes/dispatch/edit', json={'date': DAY.isoformat(), 'rev': data['rev'], 'action': 'undo'})
    assert undo.status_code == 200
    assert next(t for t in undo.get_json()['plan']['trucks'] if t['car_code'] == 'A')['stops'] == 3


def test_api_invalid_exchange_leaves_sqlite_unchanged(api_client):
    before = api_client.get('/api/routes/dispatch').get_json()
    result = api_client.post('/api/routes/dispatch/edit', json={'date': DAY.isoformat(), 'rev': before['rev'],
                             'action': 'swap_trucks', 'truck': 'A', 'other_truck': 'A'})
    assert result.status_code == 400
    after = api_client.get('/api/routes/dispatch').get_json()
    assert after['rev'] == before['rev'] and after['plan'] == before['plan']


def test_api_loaded_confirmation_and_undo(api_client):
    store = api_client.application.qa_store
    raw, rev = store.load_dispatch(DAY.isoformat())
    raw['trips'][0]['loaded'] = {'at':'2026-10-26T08:00:00', 'by':'qa', 'pin':True}
    rev = store.save_dispatch(DAY.isoformat(), raw, 'qa', expected_rev=rev)
    payload = {'date': DAY.isoformat(), 'rev':rev, 'action':'swap_trucks', 'truck':'A', 'other_truck':'B'}
    ask = api_client.post('/api/routes/dispatch/edit', json=payload)
    assert ask.get_json()['loaded_confirm'] is True
    assert store.load_dispatch(DAY.isoformat()) == (raw, rev)
    accepted = api_client.post('/api/routes/dispatch/edit', json={**payload, 'confirm_loaded':True})
    assert accepted.status_code == 200
    result = accepted.get_json()
    reverted = api_client.post('/api/routes/dispatch/edit', json={'date': DAY.isoformat(), 'rev':result['rev'], 'action':'undo'})
    assert reverted.get_json()['loaded_confirm'] is True
    assert store.load_dispatch(DAY.isoformat())[1] == result['rev']


def test_api_capacity_error_leaves_sqlite_unchanged(api_client):
    ctx = api_client.application.qa_ctx
    ctx.trucks['B'] = replace(ctx.trucks['B'], capacity_kg=150)
    before = api_client.application.qa_store.load_dispatch(DAY.isoformat())
    result = api_client.post('/api/routes/dispatch/edit', json={'date':DAY.isoformat(), 'rev':before[1],
        'action':'swap_trucks', 'truck':'A', 'other_truck':'B'})
    assert result.status_code == 400
    assert api_client.application.qa_store.load_dispatch(DAY.isoformat()) == before


def test_api_competing_save_wins_without_partial_exchange(api_client, monkeypatch):
    store = api_client.application.qa_store
    original_save = store.save_dispatch
    raw, rev = store.load_dispatch(DAY.isoformat())
    competitor = copy.deepcopy(raw)
    competitor['locks'] = {'1':'A'}
    def race(*args, **kwargs):
        original_save(DAY.isoformat(), competitor, 'competing-tab', expected_rev=rev)
        return original_save(*args, **kwargs)
    monkeypatch.setattr(store, 'save_dispatch', race)
    result = api_client.post('/api/routes/dispatch/edit', json={'date':DAY.isoformat(), 'rev':rev,
        'action':'swap_trucks', 'truck':'A', 'other_truck':'B'})
    assert result.status_code == 409
    assert store.load_dispatch(DAY.isoformat()) == (competitor, rev + 1)


def test_double_swap_restores_assignments():
    ctx, stops, draft = scenario()
    result = swap(ctx, stops, swap(ctx, stops, draft))
    assert [(t.id, t.truck, t.stops) for t in result.trips] == [(t.id, t.truck, t.stops) for t in draft.trips]


def test_reported_reserve_limit_distinguishes_physical_capacity():
    ctx, stops, draft = scenario()
    ctx.trucks['A'] = replace(ctx.trucks['A'], capacity_kg=2800)
    ctx.trucks['B'] = replace(ctx.trucks['B'], capacity_kg=2500)
    stops = [replace(s, kg=1148.577, orders=tuple(replace(o, kg=1148.577) for o in s.orders))
             if s.customer_id in (1, 2) else s for s in stops]
    before = copy.deepcopy(draft.to_json())
    with pytest.raises(dp.DispatchError) as exc:
        swap(ctx, stops, draft)
    message = str(exc.value)
    assert all(x in message for x in ('B', '2 297.2', '2 250.0', '90%', '2 500.0', '47.2'))
    assert draft.to_json() == before


def test_physical_overload_names_vehicle_capacity_and_remedy():
    ctx, stops, draft = scenario()
    ctx.trucks['B'] = replace(ctx.trucks['B'], capacity_kg=150)
    with pytest.raises(dp.DispatchError) as exc:
        swap(ctx, stops, draft)
    assert all(x in str(exc.value) for x in ('B', '200.0', '150.0', '65.0'))

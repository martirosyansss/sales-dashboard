"""Одна машина: закрытая ERP-карточка «1 / 991AT61» и действующая «991AT61 / HOWO»."""
from dataclasses import replace

from test_route_garage import gclient, client, _no_road_map  # noqa: F401
from route_optimizer import store as st, views
from route_optimizer.erp import Car
from route_optimizer.snapshot import SnapshotCache


def setup_cards(c):
    state = c.application.extensions['route_optimizer']
    snap, _ = state.snapshots.cached()
    snap = replace(snap, cars={**snap.cars,
                              '1': Car('1', '991AT61', True),
                              '991AT61': Car('991AT61', 'HOWO', False),
                              'OLD': Car('OLD', 'FORD', True)})
    state.snapshots = SnapshotCache(lambda: snap)
    b = state.store.load()
    state.store.save(st.Changes(dict(b.settings), False, None,
                               (st.Truck('991AT61', 5000.0, 20.1, active=True),
                                st.Truck('OLD', 2000.0, 15.0, active=False)), ()), 'qa')
    return state, snap


def test_settings_show_one_card_and_keep_other_disabled_vehicles(gclient):
    state, snap = setup_cards(gclient)
    before = state.store.load()
    rows = views._trucks_json(snap, before)
    codes = [r['car_code'] for r in rows]
    assert '1' not in codes and codes.count('991AT61') == 1 and 'OLD' in codes
    assert next(r for r in rows if r['car_code'] == '991AT61')['fuel_l_per_100km'] == 20.1
    garage = gclient.get('/api/routes/garage').get_json()['trucks']
    assert '1' not in {r['car_code'] for r in garage}
    assert {'991AT61', 'OLD'} <= {r['car_code'] for r in garage}
    assert state.store.load() == before


def test_stale_settings_save_does_not_recreate_merged_card(gclient):
    state, _ = setup_cards(gclient)
    canonical = state.store.load().trucks['991AT61']
    response = gclient.post('/api/routes/settings', json={'trucks': [
        {'car_code': '1', 'active': False, 'capacity_kg': None, 'fuel_l_per_100km': None},
        {'car_code': '991AT61', 'active': True, 'capacity_kg': 5000.0, 'fuel_l_per_100km': 20.1}]})
    assert response.status_code == 200
    assert '1' not in state.store.load().trucks
    actual = state.store.load().trucks['991AT61']
    assert replace(actual, updated_at=canonical.updated_at, updated_by=canonical.updated_by) == canonical


def test_alias_rules_keep_manual_enabled_and_distinct_closed_cards(gclient):
    state, snap = setup_cards(gclient)
    b = state.store.load()
    assert views._retired_truck_aliases(snap, b) == {'1': '991AT61'}
    for truck in [st.Truck('1', 5000.0, 20.0, active=True),
                  st.Truck('1', 5000.0, 20.0, active=False, manual=True, name='Other')]:
        current = replace(b, trucks={**b.trucks, '1': truck})
        assert views._retired_truck_aliases(snap, current) == {}
    closed = replace(snap, cars={**snap.cars, '991AT61': Car('991AT61', 'HOWO', True)})
    assert views._retired_truck_aliases(closed, b) == {}

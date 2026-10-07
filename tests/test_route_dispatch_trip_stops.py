# -*- coding: utf-8 -*-
"""«Развоз»: быстрая правка состава рейса — «×» у магазина и «Ավելացնել խանութ» у рейса
(dispatch.apply_edit {"action": "trip_stops", "trip", "add", "remove"}).

Синтетические данные, без БД ERP и без карты дорог (км по прямой).
Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_trip_stops.py -q
"""
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer.vehicle_access import VehicleAccess  # noqa: E402
from test_route_optimizer import (DP_DEPOT, FORD, HOWO, _dispatch_setup, _dorder, _dp_ctx, _dp_stops, _info,  # noqa: E402,F401
                                  _no_road_map, client)

LAT, LON = DP_DEPOT
WEST = [(101 + i, (LAT + 0.004 * i, LON - 0.05)) for i in range(4)]
EAST = [(201 + i, (LAT + 0.004 * i, LON + 0.05)) for i in range(3)]
LOOSE = [(301, (LAT + 0.01, LON + 0.055)), (302, (LAT - 0.01, LON - 0.045))]   # «ещё не в рейсах»


def _setup(kg=200.0):
    stops, orders = _dp_stops([(c, p, kg) for c, p in WEST + EAST + LOOSE])
    draft = dp.Draft(trucks=sorted([HOWO.car_code, FORD.car_code]), next_id=3, trips=[
        dp.DraftTrip(1, HOWO.car_code, [c for c, _ in WEST]),
        dp.DraftTrip(2, FORD.car_code, [c for c, _ in EAST])])
    return _dp_ctx(), stops, draft, {o.isn for o in orders}


def _edit(ctx, stops, draft, ids, **kw):
    return dp.apply_edit(ctx, stops, draft, {'action': 'trip_stops', **kw}, ids)


def _trip(draft, tid):
    return next((t for t in draft.trips if t.id == tid), None)


def _placed(draft):
    return sorted(c for t in draft.trips for c in t.stops)


def _view(ctx, stops, draft):
    return dp.plan_view(ctx, stops, draft, _info, explain=False)


def test_remove_sends_stores_to_not_in_trips():
    ctx, stops, draft, ids = _setup()
    draft = _edit(ctx, stops, draft, ids, trip=1, remove=[101, 103])
    assert sorted(_trip(draft, 1).stops) == [102, 104]
    assert _trip(draft, 2).stops == [201, 202, 203]                 # другой рейс не тронут
    unassigned = {s['customer_id'] for s in _view(ctx, stops, draft)['unassigned']}
    assert {101, 103} <= unassigned and not {101, 103} & set(_placed(draft))


def test_add_from_not_in_trips_and_from_other_trip_in_one_edit():
    ctx, stops, draft, ids = _setup()
    draft = _edit(ctx, stops, draft, ids, trip=1, add=[302, 201])
    assert set(_trip(draft, 1).stops) == {101, 102, 103, 104, 302, 201}
    assert sorted(_trip(draft, 2).stops) == [202, 203]              # из другого рейса — переехал, не задвоен
    assert _placed(draft).count(201) == 1 and 301 not in _placed(draft)


def test_add_and_remove_together_and_trip_reordered():
    ctx, stops, draft, ids = _setup()
    draft = _edit(ctx, stops, draft, ids, trip=2, add=[301], remove=[203])
    assert set(_trip(draft, 2).stops) == {201, 202, 301}
    km = ctx.norms.km
    pts = [ctx.depot, *(next(s.point for s in stops if s.customer_id == c) for c in _trip(draft, 2).stops), ctx.depot]
    order_km = sum(km(a, b) for a, b in zip(pts, pts[1:]))
    # порядок пересчитан (2-opt): не длиннее, чем 301 просто в конце
    tail = [ctx.depot, *(next(s.point for s in stops if s.customer_id == c) for c in (201, 202, 301)), ctx.depot]
    assert order_km <= sum(km(a, b) for a, b in zip(tail, tail[1:])) + 1e-9


def test_taking_last_store_drops_source_trip():
    ctx, stops, draft, ids = _setup()
    draft = _edit(ctx, stops, draft, ids, trip=1, add=[201, 202, 203])
    assert _trip(draft, 2) is None and len(_trip(draft, 1).stops) == 7


def test_removing_all_stores_drops_the_trip():
    ctx, stops, draft, ids = _setup()
    draft = _edit(ctx, stops, draft, ids, trip=2, remove=[201, 202, 203])
    assert _trip(draft, 2) is None and _placed(draft) == [101, 102, 103, 104]


def test_added_store_loses_left_over_reason():
    ctx, stops, draft, ids = _setup()
    draft.no_room.add(301)
    draft.no_window.add(301)
    draft = _edit(ctx, stops, draft, ids, trip=2, add=[301])
    assert 301 not in draft.no_room and 301 not in draft.no_window


def test_undo_returns_plan_before_and_only_once():
    ctx, stops, draft, ids = _setup()
    trips = [(t.id, t.truck, list(t.stops)) for t in draft.trips]
    draft = _edit(ctx, stops, draft, ids, trip=1, add=[201], remove=[101])
    assert draft.undo is not None
    draft = dp.Draft.from_json(draft.to_json())                    # как из базы
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'undo'}, ids)
    assert [(t.id, t.truck, t.stops) for t in draft.trips] == trips and draft.undo is None
    with pytest.raises(dp.DispatchError):
        dp.apply_edit(ctx, stops, draft, {'action': 'undo'}, ids)


@pytest.mark.parametrize('kw', [
    {'trip': 99, 'remove': [101]},                     # нет рейса
    {'trip': True, 'remove': [101]},                   # True == 1 — не номер рейса
    {'trip': [1], 'remove': [101]},
    {'trip': '1', 'remove': [101]},
    {'trip': 1},                                       # ничего не выбрано
    {'trip': 1, 'add': [], 'remove': []},
    {'trip': 1, 'remove': [201]},                      # не в этом рейсе
    {'trip': 1, 'add': [101]},                         # уже в этом рейсе
    {'trip': 1, 'add': [999]},                         # не магазин дня
    {'trip': 1, 'add': [301], 'remove': [301]},
    {'trip': 1, 'add': '301'},                         # не список
    {'trip': 1, 'add': [301.0]},                       # не целые
    {'trip': 1, 'add': [True]},
    {'trip': 1, 'add': list(range(dp.TRIP_STOPS_MAX + 1))},
])
def test_bad_requests_change_nothing(kw):
    ctx, stops, draft, ids = _setup()
    before = draft.to_json()
    with pytest.raises(dp.DispatchError):
        _edit(ctx, stops, draft, ids, **kw)
    assert draft.to_json() == before


def test_repeated_ids_count_once():
    ctx, stops, draft, ids = _setup()
    draft = _edit(ctx, stops, draft, ids, trip=1, add=[301, 301], remove=[101, 101])
    assert _placed(draft).count(301) == 1 and 101 not in _placed(draft)


def test_store_not_allowed_for_truck_is_refused():
    ctx, stops, draft, ids = _setup()
    ctx = replace(ctx, vehicle_access={301: VehicleAccess('allow', (FORD.car_code,))})
    before = draft.to_json()
    with pytest.raises(dp.DispatchError):
        _edit(ctx, stops, draft, ids, trip=1, add=[302, 301])        # HOWO в 301 нельзя — ничего не меняется
    assert draft.to_json() == before
    draft = _edit(ctx, stops, draft, ids, trip=2, add=[301])
    assert 301 in _trip(draft, 2).stops


def test_split_heavy_store_is_left_to_move():
    ctx, stops, draft, ids = _setup()
    draft.trips.append(dp.DraftTrip(3, FORD.car_code, [201]))       # 201 — в двух рейсах (тяжёлый заказ)
    draft.next_id = 4
    with pytest.raises(dp.DispatchError):
        _edit(ctx, stops, draft, ids, trip=1, add=[201])
    with pytest.raises(dp.DispatchError):
        _edit(ctx, stops, draft, ids, trip=2, remove=[201])


def test_loaded_trip_needs_confirmation():
    ctx, stops, draft, ids = _setup()
    _trip(draft, 2).loaded = {'at': '2026-10-01T09:00:00', 'by': 'u', 'pin': False}
    with pytest.raises(dp.LoadedEdit):
        _edit(ctx, stops, draft, ids, trip=2, remove=[201])          # сам рейс загружен
    with pytest.raises(dp.LoadedEdit):
        _edit(ctx, stops, draft, ids, trip=1, add=[202])             # магазин берут из загруженного
    with pytest.raises(dp.DispatchError):
        _edit(ctx, stops, draft, ids, trip=[2], remove=[201])        # не номер рейса — ошибка логиста, не 500
    draft = _edit(ctx, stops, draft, ids, trip=1, add=[301])        # загруженного не касается
    assert 301 in _trip(draft, 1).stops
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'trip_stops', 'trip': 2, 'remove': [201]}, ids, loaded_ok=True)
    assert 201 not in _trip(draft, 2).stops


def test_api_trip_stops_saves_and_undo_restores(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2)])
    day = '2026-10-01'
    body = client.post('/api/routes/dispatch/build', json={'date': day, 'trucks': ['CAR1', 'CAR2']}).get_json()
    trips = [tr for t in body['plan']['trucks'] for tr in t['trips']]
    trip = next(tr for tr in trips if len(tr['stops']) > 1)
    cid = trip['stops'][0]['customer_id']
    r = client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': body['rev'], 'action': 'trip_stops',
                                                       'trip': trip['id'], 'remove': [cid]})
    assert r.status_code == 200, r.get_json()
    done = r.get_json()
    assert cid in [s['customer_id'] for s in done['plan']['unassigned']]
    assert done['delta_km'] is not None
    back = client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': done['rev'], 'action': 'trip_stops',
                                                          'trip': trip['id'], 'add': [cid]}).get_json()
    assert not back['plan']['unassigned']
    undo = client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': back['rev'], 'action': 'undo'}).get_json()
    assert cid in [s['customer_id'] for s in undo['plan']['unassigned']]           # отменена только последняя правка
    stale = client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': body['rev'], 'action': 'trip_stops',
                                                           'trip': trip['id'], 'remove': [cid]})
    assert stale.status_code == 409                                                # план изменён в другой вкладке
    bad = client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': undo['rev'], 'action': 'trip_stops',
                                                         'trip': trip['id'], 'add': 'x'})
    assert bad.status_code == 400

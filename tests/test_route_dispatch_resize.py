# -*- coding: utf-8 -*-
"""«Развоз»: конец рейса тянут мышью на шкале дня (ответ владельца №59) — dispatch.apply_edit {"action": "resize"}.

Раньше — магазины уходят другим машинам, позже — машина берёт магазины других; каждый раз с наименьшим ростом км.
Синтетические данные, без БД ERP и без карты дорог (км по прямой).
Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_resize.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from test_route_optimizer import (DP_DEPOT, FORD, HOWO, _dispatch_setup, _dorder, _dp_ctx, _dp_stops, _info,  # noqa: E402,F401
                                  _no_road_map, client)

LAT, LON = DP_DEPOT
WEST = [(101 + i, (LAT + 0.004 * i, LON - 0.05)) for i in range(4)]      # ≈ 4 км к западу от склада
EAST = [(201 + i, (LAT + 0.004 * i, LON + 0.05)) for i in range(4)]      # ≈ 4 км к востоку
FORD_EAST = [(301 + i, (LAT + 0.004 * i, LON + 0.06)) for i in range(3)]
WEST_IDS, EAST_IDS = {c for c, _ in WEST}, {c for c, _ in EAST}


def _setup(kg=300.0, ford_kg=300.0, extra=(), pinned=False, ctx=None):
    spec = [(c, p, kg) for c, p in WEST + EAST] + [(c, p, ford_kg) for c, p in FORD_EAST] + list(extra)
    stops, orders = _dp_stops(spec)
    draft = dp.Draft(trucks=sorted([HOWO.car_code, FORD.car_code]), next_id=3, trips=[
        dp.DraftTrip(1, HOWO.car_code, [c for c, _ in WEST + EAST]),
        dp.DraftTrip(2, FORD.car_code, [c for c, _ in FORD_EAST], pinned)])
    return ctx or _dp_ctx(), stops, draft, {o.isn for o in orders}


def _view(ctx, stops, draft):
    return dp.plan_view(ctx, stops, draft, _info, explain=False)


def _min(hhmm):
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def _ret(ctx, stops, draft, tid):
    return _min(next(t for tk in _view(ctx, stops, draft)['trucks'] for t in tk['trips'] if t['id'] == tid)['return'])


def _resize(ctx, stops, draft, ids, tid, at):
    return dp.apply_edit(ctx, stops, draft, {'action': 'resize', 'trip': tid, 'return': at}, ids)


def _placed(draft):
    return sorted(c for t in draft.trips for c in t.stops)


def _of(draft, code):
    return [t for t in draft.trips if t.truck == code]


def test_shrink_moves_nearest_stops_to_other_truck_until_return():
    ctx, stops, draft, ids = _setup()
    before = _placed(draft)
    target = _ret(ctx, stops, draft, 1) - 30
    draft = _resize(ctx, stops, draft, ids, 1, target)
    assert _ret(ctx, stops, draft, 1) <= target
    moved = set(c for t in _of(draft, FORD.car_code) for c in t.stops) - {c for c, _ in FORD_EAST}
    assert moved and moved <= EAST_IDS                 # уходят восточные — рядом с рейсом FORD, а не западные
    assert _placed(draft) == before                    # ни один магазин не потерян и не задвоен
    view = _view(ctx, stops, draft)
    assert not any(t['over_time'] or t['over_capacity'] or t['window_miss'] for tk in view['trucks'] for t in tk['trips'])


def test_shrink_to_nothing_hands_whole_trip_away():
    ctx, stops, draft, ids = _setup()
    draft = _resize(ctx, stops, draft, ids, 1, 9 * 60)
    assert not _of(draft, HOWO.car_code)
    assert len(_placed(draft)) == 11


def test_grow_takes_unassigned_first_then_nearest_from_other_trucks():
    lone = (401, (LAT + 0.01, LON + 0.065), 200.0)        # «ещё не в рейсах», рядом с рейсом FORD
    ctx, stops, draft, ids = _setup(extra=[lone])
    draft.no_room.add(401)
    target = _ret(ctx, stops, draft, 2) + 40
    draft = _resize(ctx, stops, draft, ids, 2, target)
    ford = set(_of(draft, FORD.car_code)[0].stops)
    assert 401 in ford and 401 not in draft.no_room
    assert ford - {c for c, _ in FORD_EAST} - {401} <= EAST_IDS     # от HOWO — ближние восточные
    assert len(ford) > 4
    assert _ret(ctx, stops, draft, 2) <= target
    assert _placed(draft) == sorted({c for c, _ in WEST + EAST + FORD_EAST} | {401})


def test_grow_full_trip_adds_next_trip_of_same_truck():
    ctx, stops, draft, ids = _setup(ford_kg=1000.0)       # 3 т из 3,5 т — больше 90% не положить
    target = _ret(ctx, stops, draft, 2) + 120
    draft = _resize(ctx, stops, draft, ids, 2, target)
    ford = _of(draft, FORD.car_code)
    assert len(ford) == 2 and ford[0].id == 2 and sorted(ford[0].stops) == [301, 302, 303]
    assert set(ford[1].stops) <= WEST_IDS | EAST_IDS
    assert _ret(ctx, stops, draft, ford[1].id) <= target
    assert draft.next_id == ford[1].id + 1


def test_pinned_trip_of_other_truck_is_not_touched():
    ctx, stops, draft, ids = _setup(pinned=True)
    draft = _resize(ctx, stops, draft, ids, 1, _ret(ctx, stops, draft, 1) - 30)
    pinned = next(t for t in draft.trips if t.id == 2)
    assert pinned.pinned and pinned.stops == [301, 302, 303]
    # магазины ушли новым рейсом FORD после закреплённого
    assert len(_of(draft, FORD.car_code)) == 2


def test_receiver_never_runs_past_end_of_day():
    ctx, stops, draft, ids = _setup()
    short = dp.DayContext(ctx.day, ctx.depot, ctx.trucks, ctx.norms, fl.TruckNorms(
        work_minutes=_ret(ctx, stops, draft, 2) - 9 * 60 + 5, unload_min_per_stop=8.0, unload_min_per_tonne=6.0), 9 * 60)
    target = _ret(short, stops, draft, 1) - 60
    draft = _resize(short, stops, draft, ids, 1, target)
    assert _of(draft, FORD.car_code)[0].stops == [301, 302, 303]   # FORD к концу дня ничего не успевает взять
    assert _ret(short, stops, draft, 1) > target                   # некуда — HOWO остаётся как был


def test_central_stops_go_only_to_truck_with_center_access():
    zone = ((LAT - 0.01, LON + 0.04), (LAT + 0.03, LON + 0.04), (LAT + 0.03, LON + 0.055), (LAT - 0.01, LON + 0.055))
    ctx, stops, draft, ids = _setup()
    ctx = dp.DayContext(ctx.day, ctx.depot, ctx.trucks, ctx.norms, ctx.tn, 9 * 60, center_zone=zone)
    draft.trips[0].stops = [c for c, _ in WEST]                     # центральные у FORD быть не могут
    draft.trips[0].stops += sorted(EAST_IDS)
    draft = _resize(ctx, stops, draft, ids, 1, _ret(ctx, stops, draft, 1) - 30)
    assert not EAST_IDS & {c for t in _of(draft, FORD.car_code) for c in t.stops}


def test_same_return_changes_nothing():
    ctx, stops, draft, ids = _setup()
    trips = [(t.id, list(t.stops)) for t in draft.trips]
    draft = _resize(ctx, stops, draft, ids, 1, _ret(ctx, stops, draft, 1))
    assert [(t.id, t.stops) for t in draft.trips] == trips


@pytest.mark.parametrize('at', [None, 'abc', True, -5, float('nan'), 10 ** 6])
def test_bad_return_is_rejected(at):
    ctx, stops, draft, ids = _setup()
    with pytest.raises(dp.DispatchError):
        _resize(ctx, stops, draft, ids, 1, at)


def test_unknown_trip_is_rejected():
    ctx, stops, draft, ids = _setup()
    with pytest.raises(dp.DispatchError):
        _resize(ctx, stops, draft, ids, 99, 600)


def test_grow_of_earlier_trip_does_not_push_later_trip_past_end_of_day():
    ctx, stops, draft, ids = _setup()
    draft.trips[1].stops = [301, 302]
    draft.trips.append(dp.DraftTrip(3, FORD.car_code, [303]))
    draft.next_id = 4
    end = _ret(ctx, stops, draft, 3)
    short = dp.DayContext(ctx.day, ctx.depot, ctx.trucks, ctx.norms, fl.TruckNorms(
        work_minutes=end - 9 * 60 + 10, unload_min_per_stop=8.0, unload_min_per_tonne=6.0), 9 * 60)
    draft = _resize(short, stops, draft, ids, 2, _ret(short, stops, draft, 2) + 90)
    assert _ret(short, stops, draft, 3) <= end + 10          # второй рейс FORD — не позже конца дня


def test_drag_of_last_trip_past_end_of_day_is_allowed_up_to_target():
    ctx, stops, draft, ids = _setup()
    end = _ret(ctx, stops, draft, 2)
    short = dp.DayContext(ctx.day, ctx.depot, ctx.trucks, ctx.norms, fl.TruckNorms(
        work_minutes=end - 9 * 60, unload_min_per_stop=8.0, unload_min_per_tonne=6.0), 9 * 60)
    draft = _resize(short, stops, draft, ids, 2, end + 40)
    assert end < _ret(short, stops, draft, 2) <= end + 40    # тянут сам последний рейс за конец дня — переработка


def test_reorder_after_move_never_adds_window_misses():
    """2-opt бережёт окна, только если порядок их уже соблюдал: в рейсе с опозданием он не должен добавить новых."""
    import math
    import random
    checked = 0
    for seed in range(200):
        rnd = random.Random(seed)
        n = rnd.randint(4, 7)
        spec = [(100 + i, (LAT + rnd.uniform(-.06, .06), LON + rnd.uniform(-.08, .08)), 200.0) for i in range(n)]
        stops, _ = _dp_stops(spec)
        routable = {s.customer_id: s for s in stops}
        wins = {100 + i: (-math.inf, 9 * 60 + rnd.randint(15, 120)) for i in range(n) if rnd.random() < .6}
        base = _dp_ctx()
        ctx = dp.DayContext(base.day, DP_DEPOT, base.trucks, base.norms, base.tn, 9 * 60, windows=wins)
        order = [100 + i for i in range(n)]
        rnd.shuffle(order)
        draft = dp.Draft(trucks=[HOWO.car_code, FORD.car_code], next_id=2, trips=[dp.DraftTrip(1, FORD.car_code, order)])
        shares = dp._shares(draft.trips)
        _, _, before = dp._truck_day(ctx, draft.trips, routable, shares, FORD.car_code)
        if not before:
            continue
        dp._take(ctx, draft, routable, [dp.DraftTrip(1, FORD.car_code, list(order))], -1, (1,))
        assert dp._truck_day(ctx, draft.trips, routable, shares, FORD.car_code)[2] <= before, seed
        checked += 1
    assert checked > 20


def test_heavy_order_split_over_trips_is_not_moved():
    ctx, stops, draft, ids = _setup()
    draft.trips.append(dp.DraftTrip(3, HOWO.car_code, [201]))     # 201 — в двух рейсах HOWO (тяжёлый заказ)
    draft.next_id = 4
    draft = _resize(ctx, stops, draft, ids, 1, 9 * 60)
    assert [t.stops for t in draft.trips if t.id == 1] == [[201]]   # ушли все, кроме доли тяжёлого заказа
    assert sum(c == 201 for c in _placed(draft)) == 2


def test_shrink_into_new_trip_advances_next_id():
    ctx, stops, draft, ids = _setup(pinned=True)
    draft = _resize(ctx, stops, draft, ids, 1, _ret(ctx, stops, draft, 1) - 30)
    assert draft.next_id == max(t.id for t in draft.trips) + 1 == 4


# ============================== «Չեղարկել» и подсказка во время перетаскивания ==============================

def test_undo_returns_plan_before_resize_and_only_once():
    ctx, stops, draft, ids = _setup()
    trips = [(t.id, t.truck, list(t.stops)) for t in draft.trips]
    draft = _resize(ctx, stops, draft, ids, 1, _ret(ctx, stops, draft, 1) - 30)
    assert draft.undo is not None and [(t.id, t.truck, t.stops) for t in draft.trips] != trips
    draft = dp.Draft.from_json(draft.to_json())                    # как из базы
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'undo'}, ids)
    assert [(t.id, t.truck, t.stops) for t in draft.trips] == trips and draft.undo is None
    with pytest.raises(dp.DispatchError):
        dp.apply_edit(ctx, stops, draft, {'action': 'undo'}, ids)


def test_any_other_change_drops_undo():
    ctx, stops, draft, ids = _setup()
    draft = _resize(ctx, stops, draft, ids, 1, _ret(ctx, stops, draft, 1) - 30)
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'unpin', 'trip': 2}, ids)
    assert draft.undo is None
    draft = _resize(ctx, stops, draft, ids, 1, _ret(ctx, stops, draft, 1) - 30)
    draft = dp.overtime(ctx, stops, draft)
    assert draft.undo is None
    assert 'undo' not in dp.Draft().to_json()                      # без отмены — черновик как раньше


def test_api_preview_saves_nothing_and_undo_restores(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2)])
    day = '2026-10-01'
    body = client.post('/api/routes/dispatch/build', json={'date': day, 'trucks': ['CAR1', 'CAR2']}).get_json()
    trip = body['plan']['trucks'][0]['trips'][0]
    back = int(trip['return'][:2]) * 60 + int(trip['return'][3:5])
    req = {'date': day, 'rev': body['rev'], 'action': 'resize', 'trip': trip['id'], 'return': back + 60}
    r = client.post('/api/routes/dispatch/edit', json={**req, 'preview': True})
    assert r.status_code == 200, r.get_json()
    prev = r.get_json()['preview']
    assert set(prev) == {'delta_km', 'stops', 'return'} and 'plan' not in r.get_json()
    assert client.get(f'/api/routes/dispatch?date={day}').get_json()['rev'] == body['rev']      # ничего не сохранено
    done = client.post('/api/routes/dispatch/edit', json=req).get_json()
    assert done['success'] and done['rev'] == body['rev'] + 1
    undo = client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': done['rev'], 'action': 'undo'}).get_json()
    assert undo['success'] and undo['plan']['trucks'] == body['plan']['trucks']
    again = client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': undo['rev'], 'action': 'undo'})
    assert again.status_code == 400


def test_api_undo_only_right_after_resize_and_preview_validates(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    day = '2026-10-01'
    body = client.post('/api/routes/dispatch/build', json={'date': day, 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': body['rev'], 'action': 'undo'}).status_code == 400
    trip = body['plan']['trucks'][0]['trips'][0]['id']
    bad = client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': body['rev'], 'action': 'resize', 'trip': trip,
                                                         'return': 'x', 'preview': True})
    assert bad.status_code == 400
    assert client.get(f'/api/routes/dispatch?date={day}').get_json()['rev'] == body['rev']

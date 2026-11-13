# -*- coding: utf-8 -*-
"""«Развоз»: цена правки заранее. POST /api/routes/dispatch/add-preview — сколько стоит «Տանել այսօր» (только считает, те же
цифры, что потом вернёт сама правка) и plan_view.stops.remove_saves — сколько сбережёт «×». Синтетические данные, без ERP;
база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_add_preview.py -q
"""
import copy
import json
import sys
import threading
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_optimizer import (DP_DEPOT, FORD, HOWO, _dispatch_setup, _dorder, _dp_ctx, _dp_stops, _info,  # noqa: E402,F401
                                  _isn, client)

DAY = '2026-10-01'
OLD = date(2026, 9, 28)          # заказ прошлых дней для развоза 01.10
LAT, LON = DP_DEPOT


@pytest.fixture(autouse=True)
def _before_day(monkeypatch):
    """Сейчас — вечер 30.09: развоз 01.10 — завтрашний день."""
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 18, 0))
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 9, 30, 18, 0))


def _orders(*backlog):
    """Заказы дня 101 и 102 и заказы прошлых дней backlog — (номер, клиент, кг)."""
    return [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2),
            *(_dorder(i, cid, kg, agent=2, day=OLD) for i, cid, kg in backlog)]


def _build(client):
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['plan'] is not None, d
    return d


def _preview(client, d, **body):
    r = client.post('/api/routes/dispatch/add-preview', json={'date': DAY, 'rev': d['rev'], **body})
    return r.status_code, r.get_json()


def _edit(client, d, **body):
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], **body})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _item(body, cid):
    return next(x for x in body['items'] if x['customer_id'] == cid)


def _trips_of(d, cid):
    return [(t['car_code'], tr['id']) for t in d['plan']['trucks'] for tr in t['trips'] for x in tr['stops'] if x['customer_id'] == cid]


def _minutes(d):
    return sum(t['minutes'] for t in d['plan']['trucks'])


def _assert_parity(delta, before, after):
    assert delta['delta_km'] == after['delta_km']
    assert delta['delta_amd'] == after['plan']['summary']['operating_cost_amd'] - before['plan']['summary']['operating_cost_amd']
    assert delta['delta_min'] == _minutes(after) - _minutes(before)


def _stored(client):
    state = client.application.extensions['route_optimizer']
    return state.store.load_dispatch(DAY)


# --- Только считает ---

def test_preview_changes_nothing(client):
    _dispatch_setup(client, _orders((6, 104, 70.0), (7, 101, 40.0)))
    d = _build(client)
    stored = json.dumps(_stored(client)[0], sort_keys=True)
    code, body = _preview(client, d, orders=[_isn(6), _isn(7)])
    assert code == 200 and body['success'] and body['rev'] == d['rev']
    now = _stored(client)
    assert json.dumps(now[0], sort_keys=True) == stored and now[1] == d['rev']
    again = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert again['rev'] == d['rev'] and {o['isn'] for o in again['backlog'] if o['taken']} == set()


def test_item_shape(client):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    code, body = _preview(client, _build(client))
    assert code == 200 and len(body['items']) == 1 and 'selection' not in body
    item = body['items'][0]
    assert set(item) == {'customer_id', 'isns', 'kg', 'status', 'truck', 'trip_id', 'trip_no', 'delta_km', 'delta_min', 'delta_amd', 'reason'}
    assert (item['customer_id'], item['isns'], item['kg']) == (104, [_isn(6)], 70)


# --- Паритет с настоящей правкой include ---

def test_one_order_parity_with_include(client):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _build(client)
    _, body = _preview(client, d)
    item = _item(body, 104)
    assert item['status'] == 'new_stop' and item['delta_km'] > 0
    after = _edit(client, d, action='include', order=_isn(6))
    _assert_parity(item, d, after)
    assert _trips_of(after, 104) == [(item['truck'], item['trip_id'])]
    trips = next(t for t in after['plan']['trucks'] if t['car_code'] == item['truck'])['trips']
    assert trips[item['trip_no'] - 1]['id'] == item['trip_id']


def test_set_parity_with_include(client):
    _dispatch_setup(client, _orders((6, 104, 70.0), (7, 101, 40.0), (8, 103, 55.0)))
    d = _build(client)
    picked = [_isn(6), _isn(7), _isn(8)]
    _, body = _preview(client, d, orders=picked)
    sel = body['selection']
    assert (sel['placed'], sel['missed']) == (3, 0)
    assert {x['customer_id']: x['status'] for x in sel['customers']} == {101: 'same_stop', 103: 'new_stop', 104: 'new_stop'}
    after = _edit(client, d, action='include', orders=picked)
    _assert_parity(sel, d, after)
    for x in sel['customers']:
        assert (x['truck'], x['trip_id']) in _trips_of(after, x['customer_id'])


def test_selection_is_not_the_sum_of_items(client):
    """Два магазина вместе — один объезд на двоих: совместная дельта — по правке, а не сумма строк."""
    _dispatch_setup(client, _orders((6, 104, 70.0), (8, 103, 55.0)))
    d = _build(client)
    _, body = _preview(client, d, orders=[_isn(6), _isn(8)])
    _, rows = _preview(client, d)
    after = _edit(client, d, action='include', orders=[_isn(6), _isn(8)])
    _assert_parity(body['selection'], d, after)
    assert body['selection']['delta_km'] != pytest.approx(_item(rows, 104)['delta_km'] + _item(rows, 103)['delta_km'])


def test_same_stop_weight_only_and_not_inflated_by_other_candidates(client):
    """Магазин уже в рейсе: км не меняются, цена — по весу только его заказа, а не всех кандидатов дня."""
    _dispatch_setup(client, _orders((7, 101, 900.0), (6, 104, 2500.0)))
    d = _build(client)
    _, body = _preview(client, d)
    item = _item(body, 101)
    assert item['status'] == 'same_stop' and item['delta_km'] == 0.0 and item['kg'] == 900 and item['reason'] is None
    assert item['trip_id'] == _trips_of(d, 101)[0][1]
    after = _edit(client, d, action='include', order=_isn(7))
    _assert_parity(item, d, after)
    assert after['delta_km'] == 0.0


def test_same_stop_overload_is_reported(client):
    """В рейс магазина вес не проверяется: план станет перегруженным — строка об этом говорит."""
    _dispatch_setup(client, _orders((7, 101, 15000.0)))
    d = _build(client)
    _, body = _preview(client, d)
    item = _item(body, 101)
    assert item['status'] == 'same_stop' and item['reason'] == dp.ADD_OVERLOAD
    after = _edit(client, d, action='include', order=_isn(7))
    assert any(tr['over_capacity'] for t in after['plan']['trucks'] for tr in t['trips'])


# --- Закреплённые и утверждённые рейсы ---

def test_pinned_trips_give_new_trip_on_a_truck(client):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _build(client)
    for t in d['plan']['trucks']:
        for tr in t['trips']:
            d = _edit(client, d, action='pin', trip=tr['id'], truck=t['car_code'])
    _, body = _preview(client, d)
    item = _item(body, 104)
    assert item['status'] == 'new_trip' and item['delta_km'] > 0
    after = _edit(client, d, action='include', order=_isn(6))
    _assert_parity(item, d, after)
    assert _trips_of(after, 104) == [(item['truck'], item['trip_id'])]
    assert item['trip_id'] not in {tr['id'] for t in d['plan']['trucks'] for tr in t['trips']}


def test_approved_trips_still_take_a_store_like_the_real_edit(client):
    """Утверждение закрепляет рейсы, но вставлять заказы в них можно (dispatch._insertable, №73) — закрытые только рейсы,
    закреплённые логистом (test_pinned_trips_…). Цена заранее — та же, что у правки."""
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _edit(client, _build(client), action='approve')
    assert d['approved'] and all(tr['pinned'] for t in d['plan']['trucks'] for tr in t['trips'])
    _, body = _preview(client, d)
    item = _item(body, 104)
    assert item['status'] == 'new_stop'
    after = _edit(client, d, action='include', order=_isn(6))
    _assert_parity(item, d, after)


# --- Места нет, нельзя взять ---

def test_no_room_keeps_row_and_reason(client):
    _dispatch_setup(client, _orders((6, 104, 12000.0)))
    d = _build(client)
    _, body = _preview(client, d, orders=[_isn(6)])
    item = _item(_preview(client, d)[1], 104)
    assert item['status'] == 'no_room' and item['reason'] == dp.ADD_NO_WEIGHT and item['truck'] is None and 'cannot' not in item
    assert (item['delta_km'], item['delta_min'], item['delta_amd']) == (0.0, 0, 0)
    assert (body['selection']['placed'], body['selection']['missed']) == (0, 1)
    after = _edit(client, d, action='include', order=_isn(6))
    assert after['delta_km'] == 0.0 and not _trips_of(after, 104)


def test_store_without_map_point_is_no_room(client):
    _dispatch_setup(client, _orders((6, 999, 50.0)))
    _, body = _preview(client, _build(client))
    item = _item(body, 999)
    assert item['status'] == 'no_room' and item['reason'] == dp.ADD_NO_POINT and item['cannot'] is True


def test_store_in_started_trip_is_refused_like_the_edit(client, monkeypatch):
    """Сегодня 15:00: магазин в рейсе, который уже грузится, — правка откажет всему набору; строка говорит об этом честно,
    совместный итог считается без него."""
    _dispatch_setup(client, _orders((7, 101, 40.0), (6, 104, 70.0)))
    _build(client)
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 1, 15, 0))
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 10, 1, 15, 0))
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    _, body = _preview(client, d, orders=[_isn(7), _isn(6)])
    _, rows = _preview(client, d)
    item = _item(rows, 101)
    assert item['status'] == 'no_room' and item['refused'] is True and item['reason'] == dp.ADD_STARTED and item['cannot'] is True
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'include', 'orders': [_isn(7), _isn(6)]})
    assert r.status_code == 400                                             # весь набор — отказ
    assert body['selection']['missed'] == 1
    after = _edit(client, d, action='include', order=_isn(6))
    _assert_parity(body['selection'], d, after)
    assert _item(rows, 104)['status'] in ('new_trip', 'no_room')            # начатые рейсы не трогаются, заказ — новым рейсом


def test_warehouse_blocked_order_is_refused(client, monkeypatch):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _build(client)
    monkeypatch.setattr(views, '_warehouse_blocked', lambda *a, **k: {_isn(6)})
    _, body = _preview(client, d)
    item = _item(body, 104)
    assert item['status'] == 'no_room' and item['refused'] is True and item['reason'] == dp.ADD_BLOCKED and item['cannot'] is True


# --- Границы запроса ---

def test_rev_conflict_is_the_same_409_as_edit(client):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _build(client)
    code, body = _preview(client, {'rev': d['rev'] + 5})
    edit = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'] + 5, 'action': 'include', 'order': _isn(6)})
    assert code == 409 == edit.status_code and body['error'] == edit.get_json()['error']


def test_without_plan_is_409(client):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert d['plan'] is None
    assert _preview(client, d)[0] == 409


def test_past_day_is_empty(client, monkeypatch):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _build(client)
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 5, 10, 0))
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 10, 5, 10, 0))
    code, body = _preview(client, d)
    assert code == 200 and body['items'] == [] and 'selection' not in body
    code, body = _preview(client, d, orders=[_isn(6)])
    assert code == 200 and body['items'] == [] and (body['selection']['placed'], body['selection']['delta_km']) == (0, 0.0)


def test_bad_orders_are_refused(client):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _build(client)
    assert _preview(client, d, orders='x')[0] == 400
    assert _preview(client, d, orders=[_isn(1)])[0] == 400                  # заказ дня — не из прошлых дней
    assert _preview(client, d, orders=[])[1]['selection']['placed'] == 0


def test_busy_is_429_and_slots_released(client, monkeypatch):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _build(client)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(views, '_PREVIEW_SLOTS', slots)
    assert slots.acquire(blocking=False)
    assert _preview(client, d)[0] == 429
    slots.release()
    assert _preview(client, d)[0] == 200
    assert slots.acquire(blocking=False)                                    # место вернулось и после расчёта


def test_preview_and_compare_do_not_share_slots(client, monkeypatch):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _build(client)
    compare, preview = threading.BoundedSemaphore(1), threading.BoundedSemaphore(1)
    monkeypatch.setattr(views, '_COMPARE_SLOTS', compare)
    monkeypatch.setattr(views, '_PREVIEW_SLOTS', preview)
    assert compare.acquire(blocking=False)
    assert _preview(client, d)[0] == 200                                    # занятое сравнение не мешает цене заказов
    compare.release()
    assert preview.acquire(blocking=False)
    r = client.post('/api/routes/dispatch/compare', json={'date': DAY, 'rev': d['rev'], 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200, r.get_json()                               # и наоборот
    preview.release()


def test_ticks_do_not_recompute_items(client, monkeypatch):
    _dispatch_setup(client, _orders((6, 104, 70.0)))
    d = _build(client)

    def boom(*a, **k):
        raise AssertionError('items are not needed for ticks')
    monkeypatch.setattr(views.dp, 'add_preview', boom)
    code, body = _preview(client, d, orders=[_isn(6)])
    assert code == 200 and body['items'] == [] and body['selection']['placed'] == 1


# --- «×»: сколько сбережёт ---

def _plan_figures(view):
    return view['summary']['km'], sum(t['minutes'] for t in view['trucks']), view['summary']['operating_cost_amd']


def _stop_of(d, cid):
    return next(x for t in d['plan']['trucks'] for tr in t['trips'] for x in tr['stops'] if x['customer_id'] == cid)


def _assert_saved(saves, before, after):
    b, a = _plan_figures(before['plan']), _plan_figures(after['plan'])
    assert saves == {'km': round(b[0] - a[0], 1), 'minutes': b[1] - a[1], 'amd': b[2] - a[2]}


@pytest.mark.parametrize('body', [{'action': 'defer_store', 'customer_id': 104, 'to': None},
                                  {'action': 'trip_stops', 'remove': [104], 'reason': 'today'}])
def test_remove_saves_matches_real_removal(client, body):
    _dispatch_setup(client, _orders() + [_dorder(3, 104, 80.0), _dorder(4, 103, 60.0)])
    d = _build(client)
    trip = next(tr for t in d['plan']['trucks'] for tr in t['trips'] if any(x['customer_id'] == 104 for x in tr['stops']))
    saves = _stop_of(d, 104)['remove_saves']
    assert saves['km'] >= 0
    edit = {**body, 'trip': trip['id']} if body['action'] == 'trip_stops' else body
    _assert_saved(saves, d, _edit(client, d, **edit))


def _line_trip():
    """Рейс с плохим порядком: без магазина X (первый) 2-opt переставит оставшиеся, у «вычёркивания» порядок прежний."""
    spots = [(101, (LAT + 0.03, LON + 0.04)), (102, (LAT + 0.01, LON + 0.02)), (103, (LAT - 0.01, LON + 0.06)),
             (104, (LAT - 0.01, LON + 0.02)), (105, (LAT + 0.01, LON + 0.06))]   # 102–105: крест-накрест, порядок A, B, C, D плохой
    stops, orders = _dp_stops([(c, p, 150.0) for c, p in spots])
    draft = dp.Draft(trucks=[HOWO.car_code, FORD.car_code], next_id=2, trips=[dp.DraftTrip(1, HOWO.car_code, [101, 102, 103, 104, 105])])
    return _dp_ctx(), stops, draft, {o.isn for o in orders}


def test_remove_saves_follows_two_opt_and_as_is_follows_defer_store():
    ctx, stops, draft, ids = _line_trip()
    before = dp.plan_view(ctx, stops, copy.deepcopy(draft), _info)
    saves = next(x['remove_saves'] for t in before['trucks'] for tr in t['trips'] for x in tr['stops'] if x['customer_id'] == 101)
    assert 'as_is' in saves and saves['km'] > saves['as_is']['km']          # 2-opt переставил оставшиеся — путь короче
    rule = dp.apply_edit(ctx, stops, copy.deepcopy(draft), {'action': 'trip_stops', 'trip': 1, 'remove': [101]}, ids)
    b = _plan_figures(before)
    a = _plan_figures(dp.plan_view(ctx, stops, rule, _info, explain=False))
    assert {k: saves[k] for k in ('km', 'minutes', 'amd')} == {'km': round(b[0] - a[0], 1), 'minutes': b[1] - a[1], 'amd': b[2] - a[2]}
    rest = [s for s in stops if s.customer_id != 101]
    defer = copy.deepcopy(draft)
    dp.prune(defer, rest)
    a = _plan_figures(dp.plan_view(ctx, rest, defer, _info, explain=False))
    assert saves['as_is'] == {'km': round(b[0] - a[0], 1), 'minutes': b[1] - a[1], 'amd': b[2] - a[2]}


def test_remove_saves_last_stop_takes_the_whole_trip_away():
    ctx, stops, draft, _ = _line_trip()
    draft.trips = [dp.DraftTrip(1, HOWO.car_code, [101, 102]), dp.DraftTrip(2, HOWO.car_code, [103]), dp.DraftTrip(3, FORD.car_code, [104])]
    draft.next_id = 4
    before = dp.plan_view(ctx, stops, copy.deepcopy(draft), _info)
    saved = next(x['remove_saves'] for t in before['trucks'] for tr in t['trips'] for x in tr['stops'] if x['customer_id'] == 103)
    rest = [s for s in stops if s.customer_id != 103]
    gone = copy.deepcopy(draft)
    dp.prune(gone, rest)
    b, a = _plan_figures(before), _plan_figures(dp.plan_view(ctx, rest, gone, _info, explain=False))
    assert saved == {'km': round(b[0] - a[0], 1), 'minutes': b[1] - a[1], 'amd': b[2] - a[2]}


def test_remove_saves_null_when_split_or_unpriced():
    stops, _ = _dp_stops([(101, (LAT + 0.01, LON + 0.05), 900.0), (102, (LAT, LON + 0.02), 100.0), (103, (LAT, LON + 0.03), 100.0),
                          (104, (LAT, LON + 0.04), 100.0)])
    ctx = _dp_ctx()
    draft = dp.Draft(trucks=[HOWO.car_code, FORD.car_code], next_id=4, trips=[dp.DraftTrip(1, HOWO.car_code, [101, 102]),
                                                                              dp.DraftTrip(2, FORD.car_code, [101, 103]),
                                                                              dp.DraftTrip(3, 'GONE', [104])])
    view = dp.plan_view(ctx, stops, draft, _info)
    by = {(tr['id'], x['customer_id']): x['remove_saves'] for t in view['trucks'] for tr in t['trips'] for x in tr['stops']}
    assert by[(1, 101)] is None and by[(2, 101)] is None                   # разделён между рейсами
    assert by[(3, 104)] is None                                            # машины рейса нет в расчёте
    assert by[(1, 102)] is not None


def test_plan_view_without_explain_has_no_remove_saves():
    ctx, stops, draft, _ = _line_trip()
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    assert all('remove_saves' not in x for t in view['trucks'] for tr in t['trips'] for x in tr['stops'])


def test_matrix_memo_slice_equals_full_calculation():
    ctx = _dp_ctx()
    tn = replace(ctx.tn, warehouse_load_fixed_min=5.0)                      # с загрузкой минуты — TravelMatrix
    pts = [(LAT + 0.01 * i, LON + 0.02 * i) for i in range(1, 6)]
    norms = replace(ctx.norms, matrices=fl.MatrixMemo())
    fl._matrices(pts, ctx.depot, norms, tn)
    sub = [pts[0], pts[2], pts[4]]
    plain_d, plain_m = fl._matrices(sub, ctx.depot, ctx.norms, tn)
    slice_d, slice_m = fl._matrices(sub, ctx.depot, norms, tn)
    assert len(norms.matrices.rows) == 1                                    # срез, без нового расчёта
    assert slice_d == plain_d and list(slice_m) == list(plain_m) and slice_m.travel(1, 2, 30.0) == plain_m.travel(1, 2, 30.0)
    fl._matrices([pts[0], pts[0]], ctx.depot, norms, tn)                    # одинаковые точки — считается заново
    assert len(norms.matrices.rows) == 2

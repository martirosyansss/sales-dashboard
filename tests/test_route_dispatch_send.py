# -*- coding: utf-8 -*-
"""«Развоз»: отправка плана водителям (ответ владельца №81, как «Publish changes» у Routific). Утверждение отправляет
снимок плана (Draft.sent); правки после него копятся в черновике и до терминалов, сверки офиса и карты машин не доходят,
пока логист не нажмёт «Ուղարկել վարորդներին» (action send). Страница видит, что не отправлено (unsent). Выпущенный до
№81 план без снимка — отправлен он сам. Синтетические данные, без ERP.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_send.py -q
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import views  # noqa: E402
from courier import routes_link as rl  # noqa: E402
from test_route_dispatch_same_day import DAY, FORD, HOWO, D, _base, _build, _page_setup, _plan_stops, client  # noqa: E402,F401
from test_route_optimizer import _dorder, _isn  # noqa: E402

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN)
ORDERS = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 600.0, agent=2)]


def _terminal(state):
    view = rl.routes_view(state, D)
    return {car: [o.isn for o in rl.pick_orders(ORDERS, D, view, car)] for car in ('CAR1', 'CAR2')}


def _edit(client, d, **body):
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], **body})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _move_one(d):
    """Правка: первый магазин первого рейса — в рейс другой машины (или новым рейсом этой же машины)."""
    trips = [(t['car_code'], tr) for t in d['plan']['trucks'] for tr in t['trips']]
    car, tr = trips[0]
    other = next(((c, x) for c, x in trips if c != car), None)
    cid = tr['stops'][0]['customer_id']
    if other is not None:
        return {'action': 'move', 'customer_id': cid, 'from_trip': tr['id'], 'to_trip': other[1]['id'], 'truck': other[0]}
    return {'action': 'move', 'customer_id': cid, 'from_trip': tr['id'], 'to_trip': None, 'truck': car}


# ============================== черновик ==============================

def test_approve_sends_snapshot_and_round_trips():
    *_, draft = _base((FORD, HOWO))
    assert draft.sent is None and 'sent' not in draft.to_json() and draft.for_drivers() is draft
    assert dp.unsent(draft) is None
    dp.approve(draft, '2026-10-01T08:30:00', 'logist')
    assert draft.sent['at'] == '2026-10-01T08:30:00' and draft.sent['by'] == 'logist'
    assert 'undo' not in draft.sent['plan'] and 'sent' not in draft.sent['plan']
    raw = json.loads(json.dumps(draft.to_json()))
    assert dp.Draft.from_json(raw) == draft
    assert dp.unsent(draft) is None
    # снятие утверждения снимок не трогает — машины уже в пути
    sent = draft.sent
    dp.unapprove(draft)
    assert draft.sent == sent and dp.unsent(draft) is None


def test_edit_after_send_is_unsent_until_send():
    ctx, base, orders, _, _ = _base((FORD, HOWO))
    draft = dp.Draft([FORD.car_code, HOWO.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101]),
                                                            dp.DraftTrip(2, HOWO.car_code, [102, 103])], next_id=3)
    dp.approve(draft, 'a', 'u')
    was = {t.truck: list(t.stops) for t in draft.for_drivers().trips}
    trip, other = draft.trips
    cid = trip.stops[0]
    draft = dp.apply_edit(ctx, base, draft, {'action': 'move', 'customer_id': cid, 'from_trip': trip.id,
                                             'to_trip': other.id, 'truck': other.truck}, {o.isn for o in orders})
    assert dp.unsent(draft) == {'trucks': sorted({trip.truck, other.truck}), 'orders': False}
    # водители — по-прежнему по снимку
    assert {t.truck: list(t.stops) for t in draft.for_drivers().trips} == was
    dp.send(draft, 'b', 'u2')
    assert dp.unsent(draft) is None and draft.sent['at'] == 'b'
    assert cid in next(t for t in draft.for_drivers().trips if t.id == other.id).stops


def test_order_selection_change_is_unsent_without_stop_change():
    *_, draft = _base((FORD,))
    dp.approve(draft, 'a', 'u')
    draft.agents_off = {7}
    assert dp.unsent(draft) == {'trucks': [], 'orders': True}


def test_reorder_inside_truck_counts_split_into_trips_does_not():
    *_, draft = _base((FORD,))
    dp.approve(draft, 'a', 'u')
    stops = list(draft.trips[0].stops)
    assert len(stops) >= 2
    # тот же порядок, но двумя рейсами — терминал видит то же (порядок клиентов машины)
    split = dp.Draft.from_json(draft.to_json())
    split.trips = [dp.DraftTrip(1, FORD.car_code, stops[:1]), dp.DraftTrip(2, FORD.car_code, stops[1:])]
    assert dp.unsent(split) is None
    draft.trips[0].stops = list(reversed(stops))
    assert dp.unsent(draft) == {'trucks': [FORD.car_code], 'orders': False}


def test_send_needs_release_and_legacy_or_garbage_snapshot_means_current_plan():
    *_, draft = _base((FORD,))
    with pytest.raises(dp.DispatchError, match='հաստատեք'):
        dp.send(draft, 'a', 'u')
    # выпущен до №81: снимка нет — водители видят сам черновик, он и отправлен
    raw = {**draft.to_json(), 'released': {'at': 'r', 'by': 'u'}}
    legacy = dp.Draft.from_json(raw)
    assert legacy.sent['at'] == 'r' and legacy.sent['plan'] == legacy.sent_plan() and dp.unsent(legacy) is None
    for junk in ('x', 1, {'at': 1, 'plan': {}}, {'at': 'x'}, {'at': 'x', 'plan': []}):
        got = dp.Draft.from_json({**raw, 'sent': junk})
        assert got.sent['at'] == 'r' and dp.unsent(got) is None, junk
    # не выпущен — снимок в черновике ничего не значит
    assert dp.Draft.from_json({**draft.to_json(), 'sent': {'at': 'x', 'by': None, 'plan': {}}}).sent is None


def test_rebuild_and_undo_keep_sent_snapshot():
    ctx, base, orders, _, draft = _base((FORD, HOWO))
    dp.approve(draft, 'a', 'u')
    sent = draft.sent
    dp.unapprove(draft)
    rebuilt = dp.build(ctx, base, draft, [FORD.car_code], 'now')
    assert rebuilt.sent == sent
    rebuilt.undo = {k: v for k, v in rebuilt.to_json().items() if k not in ('sent', 'prediction', 'undo')}
    assert dp.apply_edit(ctx, base, rebuilt, {'action': 'undo'}, set()).sent == sent


# ============================== страница и терминалы ==============================

def test_terminal_sees_sent_plan_not_draft_until_send(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    assert 'sent' not in d and 'unsent' not in d
    d = _edit(client, d, action='approve')
    assert d['sent'] == {'at': '2026-10-01T08:00:00', 'by': d['sent']['by']} and d['unsent'] is None
    sent_view = _terminal(state)
    assert sum(map(len, sent_view.values())) == 3
    owner_before = _plan_stops(d)
    d = _edit(client, d, **_move_one(d))
    assert _plan_stops(d) != owner_before or d['unsent'] is not None
    assert d['unsent'] is not None and d['unsent']['trucks']
    assert _terminal(state) == sent_view                     # правка водителям не ушла
    s = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert s['unsent'] == d['unsent']
    d = _edit(client, d, action='send')
    assert d['unsent'] is None
    owner = _plan_stops(d)
    expected = {car: [o.isn for o in ORDERS if owner.get(o.customer_id) == car] for car in ('CAR1', 'CAR2')}
    assert _terminal(state) == expected


def test_send_refused_before_approval_and_on_past_day(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'send'})
    assert r.status_code == 400 and 'հաստատեք' in r.get_json()['errors']['_']
    stale = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'] - 1, 'action': 'send'})
    assert stale.status_code == 409
    d = _edit(client, d, action='approve')
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 2, 9, 0, tzinfo=ac.YEREVAN))
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 2, 9, 0))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'send'})
    assert r.status_code == 400 and r.get_json()['errors']['_'] == views.PAST_DAY_APPROVE


def test_unapprove_rebuild_reapprove_sends_new_plan(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    d = _edit(client, d, action='approve')
    first = _terminal(state)
    d = _edit(client, d, action='unapprove')
    d = _build(client, ('CAR2',))
    assert _terminal(state) == first                         # пересборка — правка, водители ждут отправки
    assert d['sent']['at'] == '2026-10-01T08:00:00'
    d = _edit(client, d, action='approve')
    owner = _plan_stops(d)
    assert set(owner.values()) == {'CAR2'} and d['unsent'] is None
    assert _terminal(state) == {car: [o.isn for o in ORDERS if owner.get(o.customer_id) == car] for car in ('CAR1', 'CAR2')}


def test_live_map_follows_sent_plan(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    d = _edit(client, d, action='approve')

    def plans():
        ctx = views._live_context(state, D)
        return {car: [list(p.customers) for p in trips] for car, trips in ctx.plans.items()}
    before = plans()
    d = _edit(client, d, **_move_one(d))
    assert plans() == before
    _edit(client, d, action='send')
    assert plans() != before

# ============================== следующий день и рейсы в пути (ревью №81) ==============================

def test_next_day_reads_sent_plan_same_day_order_not_lost(client, monkeypatch):
    """Заказ дня взят в план после отправки, но не отправлен: водители его не везут — значит, и следующий день не должен
    считать его уже отвезённым (иначе заказ не уехал бы ни сегодня, ни завтра)."""
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client, ('CAR1', 'CAR2'))
    d = _edit(client, d, action='approve')
    res = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10)]}).get_json()
    d = _edit(client, d, action='same_day', orders=[_isn(10)], option=res['options'][0]['key'])
    assert d['unsent'] is not None and d['unsent']['orders']
    nxt = date(2026, 10, 2)
    wd = (1, 2, 3, 4, 5, 6)
    assert _isn(10) not in rl._taken(state, nxt, wd)                       # терминал завтра: заказ ещё не отвезён
    assert _isn(10) not in views._same_day_taken(state, nxt, wd)[0]        # страница завтра — так же
    _edit(client, d, action='send')
    assert _isn(10) in rl._taken(state, nxt, wd) and _isn(10) in views._same_day_taken(state, nxt, wd)[0]


def test_next_day_carries_deferred_only_after_send():
    """«Везти завтра» без отправки: сегодня водители его ещё везут (снимок) — завтра его не ждёт; отправили — ждёт."""
    *_, draft = _base((FORD,))
    dp.approve(draft, 'a', 'u')
    draft.deferred = {_isn(77)}
    assert dp.Draft.from_json(draft.to_json()).for_drivers().deferred == set()
    dp.send(draft, 'b', 'u')
    assert dp.Draft.from_json(draft.to_json()).for_drivers().deferred == {_isn(77)}


def test_started_trips_include_trips_started_in_sent_plan():
    """Машина едет по отправленному плану: его начатый рейс — начатый, даже если в черновике рейс переложен."""
    ctx, base, _, _, draft = _base((FORD,))
    dp.approve(draft, 'a', 'u')
    first = draft.trips[0].id
    late = 24 * 60.0
    assert first in dp.started_trips(ctx, base, draft, late)
    draft.trips = [dp.DraftTrip(99, FORD.car_code, list(draft.trips[0].stops))]   # черновик: тот же рейс под новым id
    started = dp.started_trips(ctx, base, draft, late)
    assert {first, 99} <= started


def test_started_customers_follow_the_sent_trip():
    """Магазин переложен в черновике из начатого рейса в новый — водитель всё равно везёт его по снимку: он «в пути»."""
    ctx, base, _, _, draft = _base((FORD,))
    dp.approve(draft, 'a', 'u')
    first = draft.trips[0]
    cid = first.stops[0]
    late = 24 * 60.0
    draft.trips = [dp.DraftTrip(first.id, FORD.car_code, [c for c in first.stops if c != cid]),
                   dp.DraftTrip(99, FORD.car_code, [cid])]
    assert cid in dp.started_customers(ctx, base, draft, late)
    assert cid in dp.started_customers(ctx, base, draft, late) - {c for t in draft.trips if t.id == first.id for c in t.stops}


def test_discard_returns_to_sent_plan(client, monkeypatch):
    """«Չեղարկել փոփոխությունները»: черновик — снова отправленный план; до выпуска и в прошлом дне — нельзя."""
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'discard'})
    assert r.status_code == 400 and 'չեղարկելու' in r.get_json()['errors']['_']
    d = _edit(client, d, action='approve')

    def trips(day):
        return [(t['car_code'], [s['customer_id'] for s in tr['stops']]) for t in day['plan']['trucks'] for tr in t['trips']]
    sent = trips(d)
    d = _edit(client, d, **_move_one(d))
    assert d['unsent'] is not None and trips(d) != sent
    d = _edit(client, d, action='discard')
    assert d['unsent'] is None and trips(d) == sent and 'approved' in d
    assert d['sent']['at'] == '2026-10-01T08:00:00'
    assert all(tr['pinned'] for t in d['plan']['trucks'] for tr in t['trips'])
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 2, 9, 0, tzinfo=ac.YEREVAN))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'discard'})
    assert r.status_code == 400


def test_unsent_on_road_lists_changed_trucks_already_loading(client, monkeypatch):
    """Машина с неотправленной правкой, чей рейс по отправленному плану уже грузится, — в on_road (предупреждение)."""
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    d = _edit(client, d, action='approve')
    d = _edit(client, d, **_move_one(d))
    assert d['unsent']['on_road'] == []                        # 08:00 — ещё никто не грузится
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 1, 17, 0, tzinfo=ac.YEREVAN))
    u = client.get('/api/routes/dispatch?date=' + DAY).get_json()['unsent']
    assert u['on_road'] and set(u['on_road']) <= set(u['trucks'])


def test_sent_json_and_snapshot_without_trips():
    *_, draft = _base((FORD,))
    raw = draft.to_json()
    assert dp.sent_json(raw) is raw                                        # не выпущен — сам черновик
    dp.approve(draft, 'a', 'u')
    draft.agents_off = {5}
    raw = draft.to_json()
    assert dp.sent_json(raw)['agents_off'] == [] and raw['agents_off'] == [5]
    broken = {**raw, 'sent': {'at': 'a', 'by': 'u', 'plan': {}}}            # снимок без рейсов — битый
    assert dp.sent_json(broken) is broken
    assert dp.Draft.from_json(broken).sent['plan']['agents_off'] == [5]     # выпущен — отправлен сам черновик

def test_discard_keeps_next_id_growing():
    """Номера рейсов, выброшенных «Չեղարկել փոփոխությունները», не достаются новым рейсам."""
    *_, draft = _base((FORD,))
    dp.approve(draft, 'a', 'u')
    draft.next_id += 5
    assert dp.discard(draft).next_id == draft.next_id

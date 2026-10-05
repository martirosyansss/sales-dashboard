# -*- coding: utf-8 -*-
"""«Развоз»: утверждение плана дня (ответ владельца №73, «Հաստատել օրվա պլանը»). Одна кнопка закрепляет все рейсы и
отмечает план утверждённым; пересборка и «Ջնջել երթերը» запрещены, пока утверждение не снято; ручные правки и новые
заказы дня (№72) — можно, новые рейсы при этом тоже закреплены. «Չեղարկել հաստատումը» открепляет ровно то, что закрепило
утверждение. Никогда не утверждённый план и черновик — прежние до байта. Синтетические данные, без ERP.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_approve.py -q
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_dispatch_same_day import (DAY, FORD, HOWO, _base, _build, _page_setup, _plan_stops,  # noqa: E402,F401
                                          _with, client)
from test_route_optimizer import _isn  # noqa: E402

APPROVED = 'Պլանը հաստատված է։ Ամբողջական վերակազմման համար նախ չեղարկեք հաստատումը։'


def _two_trips():
    """Рейс FORD (логист закрепил), рейс HOWO (не закреплён) и рейс взятия заказа дня (закреплён взятием)."""
    ctx, base, orders, new, draft = _base((FORD, HOWO))
    draft = dp.Draft([FORD.car_code, HOWO.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101], pinned=True),
                                                            dp.DraftTrip(2, HOWO.car_code, [102, 103]),
                                                            dp.DraftTrip(3, FORD.car_code, [201], pinned=True)],
                     next_id=4, same_day={_isn(50)}, same_day_trips={3})
    return ctx, base, orders, new, draft


# ============================== черновик ==============================

def test_approve_unapprove_round_trip_restores_pins_exactly():
    *_, draft = _two_trips()
    before = draft.to_json()
    dp.approve(draft, '2026-10-01T08:30:00', 'logist')
    assert all(t.pinned for t in draft.trips)
    assert draft.approved == {'at': '2026-10-01T08:30:00', 'by': 'logist', 'pinned': [2]}
    raw = json.loads(json.dumps(draft.to_json()))
    assert dp.Draft.from_json(raw) == draft
    with pytest.raises(dp.DispatchError, match='արդեն հաստատված'):
        dp.approve(draft, 'x', None)
    dp.unapprove(draft)
    assert draft.to_json() == before                       # закрепление логиста и взятия — как было, отметки нет
    with pytest.raises(dp.DispatchError, match='հաստատված չէ'):
        dp.unapprove(draft)
    with pytest.raises(dp.DispatchError, match='նախ կազմեք'):
        dp.approve(dp.Draft(), 'x', None)


def test_never_approved_draft_byte_identical_and_garbage():
    *_, draft = _two_trips()
    assert 'approved' not in draft.to_json()
    for junk in ('x', {'at': 1, 'pinned': []}, {'at': 'x'}, {'pinned': [1]}, [1]):
        assert dp.Draft.from_json({'approved': junk}).approved is None
    assert dp.Draft.from_json({'approved': {'at': 'x', 'by': 5, 'pinned': [3, 'z', True, 3, 1]}}).approved == \
        {'at': 'x', 'by': None, 'pinned': [1, 3]}


def test_new_trips_while_approved_are_pinned_and_unpinned_with_approval():
    ctx, base, orders, new, draft = _two_trips()
    dp.approve(draft, 'at', 'u')
    before = {t.id for t in draft.trips}
    draft = dp.apply_edit(ctx, base, draft, {'action': 'move', 'customer_id': 103, 'from_trip': 2, 'to_trip': None,
                                             'truck': HOWO.car_code}, {o.isn for o in orders})
    added = [t for t in draft.trips if t.id not in before]
    assert added and not added[0].pinned
    dp.keep_approved(draft, before)
    assert added[0].pinned and draft.approved['pinned'] == [2, added[0].id]
    # логист сам закрепил рейс утверждения — он уже его: снятие утверждения рейс не открепит
    draft = dp.apply_edit(ctx, base, draft, {'action': 'pin', 'trip': 2, 'truck': HOWO.car_code}, {o.isn for o in orders})
    assert draft.approved['pinned'] == [added[0].id]
    dp.unapprove(draft)
    # рейс 3 (магазин 201 без заказа в точках base) ушёл при правке; остальные: логиста — закреплены, новый — нет
    assert {t.id: t.pinned for t in draft.trips} == {1: True, 2: True, added[0].id: False}
    dp.keep_approved(draft, set())                          # не утверждён — ничего не делает
    assert not next(t for t in draft.trips if t.id == added[0].id).pinned


def test_prune_drops_gone_trips_from_approval():
    ctx, base, orders, new, draft = _two_trips()
    dp.approve(draft, 'at', 'u')
    dp.prune(draft, [s for s in base if s.customer_id != 102 and s.customer_id != 103])
    assert draft.approved['pinned'] == []


def test_same_day_insert_into_approved_trip_but_not_logist_pin():
    ctx, base, orders, new, _ = _base((FORD, HOWO))
    draft = dp.Draft([FORD.car_code, HOWO.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101, 102], pinned=True),
                                                            dp.DraftTrip(2, HOWO.car_code, [103])], next_id=3)
    dp.approve(draft, 'at', 'u')
    stops = _with(orders, new, {_isn(50)})
    keys = [o['key'] for o in dp.same_day_options(ctx, base, stops, draft, {201}, -30.0)['options']]
    assert 'insert:2' in keys and 'insert:1' not in keys
    out = dp.take_same_day(ctx, base, stops, dp.Draft.from_json(draft.to_json()), {201}, {_isn(50)},
                           f'trip:{FORD.car_code}', -30.0)
    assert out.trips[-1].pinned and out.same_day_trips == {out.trips[-1].id}   # рейс взятия — его закрепление
    dp.unapprove(out)
    assert out.trips[-1].pinned and not next(t for t in out.trips if t.id == 2).pinned


# ============================== страница ==============================

def test_api_approve_blocks_rebuild_and_reset_until_unapproved(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client, ('CAR1', 'CAR2'))
    plain_plan = d['plan']
    assert 'approved' not in d and 'approved' not in state.store.load_dispatch(DAY)[0]
    stale = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'] - 1, 'action': 'approve'})
    assert stale.status_code == 409
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d['approved']['at'] == '2026-10-01T08:00:00' and all(tr['pinned'] for t in d['plan']['trucks'] for tr in t['trips'])
    stored = state.store.load_dispatch(DAY)[0]
    assert stored['approved']['pinned'] == sorted(tr['id'] for t in plain_plan['trucks'] for tr in t['trips'])
    for url, body in (('build', {'trucks': ['CAR1', 'CAR2']}), ('reset', {})):
        r = client.post('/api/routes/dispatch/' + url, json={'date': DAY, **body})
        assert r.status_code == 400 and r.get_json()['errors']['_'] == APPROVED, url
    # ручная правка — можно; новые заказы дня — тоже (вставка в рейс, закреплённый утверждением)
    res = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10)]}).get_json()
    assert any(o['kind'] == 'insert' for o in res['options'])
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'same_day',
                                                       'orders': [_isn(10)], 'option': res['options'][0]['key']})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert 103 in _plan_stops(d) and 'approved' in d
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'unapprove'})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert 'approved' not in d and 'approved' not in state.store.load_dispatch(DAY)[0]
    assert not any(tr['pinned'] for t in d['plan']['trucks'] for tr in t['trips']
                   if all(s['customer_id'] != 103 for s in tr['stops']))
    assert client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).status_code == 200


def test_api_approve_refused_on_past_day(client, monkeypatch):
    _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client)
    later = datetime(2026, 10, 2, 8, 0, tzinfo=ac.YEREVAN)
    monkeypatch.setattr(views, '_yerevan_now', lambda: later)
    monkeypatch.setattr(views, '_clock', lambda: later.replace(tzinfo=None))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'})
    assert r.status_code == 400 and 'Անցած օրվա' in r.get_json()['error']


def test_plan_byte_identical_when_never_approved(client, monkeypatch):
    """Утверждение не трогает расчёт: план утверждённого дня — тот же, кроме закреплений."""
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client, ('CAR1', 'CAR2'))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    strip = lambda p: json.dumps([[{k: v for k, v in tr.items() if k != 'pinned'} for tr in t['trips']]   # noqa: E731
                                  for t in p['trucks']], sort_keys=True)
    assert strip(r['plan']) == strip(d['plan']) and r['plan']['summary'] == d['plan']['summary']


def test_api_new_trip_by_edit_while_approved_is_pinned(client, monkeypatch):
    _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client, ('CAR1', 'CAR2'))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    trip = next(tr for t in d['plan']['trucks'] for tr in t['trips'] if any(s['customer_id'] == 101 for s in tr['stops']))
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'move', 'customer_id': 101,
                                                       'from_trip': trip['id'], 'to_trip': None, 'truck': 'CAR1'})
    assert r.status_code == 200, r.get_json()
    solo = [tr for t in r.get_json()['plan']['trucks'] for tr in t['trips'] if [s['customer_id'] for s in tr['stops']] == [101]]
    assert solo and solo[0]['pinned'] is True
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': r.get_json()['rev'], 'action': 'unapprove'})
    solo = [tr for t in r.get_json()['plan']['trucks'] for tr in t['trips'] if [s['customer_id'] for s in tr['stops']] == [101]]
    assert solo[0]['pinned'] is False                                           # закрепило утверждение — открепило снятие

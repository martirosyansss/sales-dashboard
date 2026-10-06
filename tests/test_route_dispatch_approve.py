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
from courier import routes_link as rl  # noqa: E402
from test_route_dispatch_same_day import (DAY, FORD, HOWO, D, _base, _build, _page_setup, _plan_stops,  # noqa: E402,F401
                                          _with, client)
from test_route_optimizer import _dorder, _isn  # noqa: E402

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
    # закрепление логиста и взятия — как было, отметки утверждения нет; план остаётся выпущенным на терминалы (№80)
    assert {k: v for k, v in draft.to_json().items() if k not in ('released', 'sent')} == before   # №81: снимок водителей
    assert draft.released == {'at': '2026-10-01T08:30:00', 'by': 'logist'}
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


# ============================== ревью №73: вставка в рейс утверждения, правка конца рейса, AI, прошедший день ==============

def test_insert_into_approved_trip_survives_unapproval():
    ctx, base, orders, new, _ = _base((FORD, HOWO))
    draft = dp.Draft([FORD.car_code, HOWO.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101, 102]),
                                                            dp.DraftTrip(2, HOWO.car_code, [103])], next_id=3)
    dp.approve(draft, 'at', 'u')
    stops = _with(orders, new, {_isn(50)})
    out = dp.take_same_day(ctx, base, stops, dp.Draft.from_json(draft.to_json()), {201}, {_isn(50)}, 'insert:1', -30.0)
    assert out.same_day_trips == {1} and out.approved['pinned'] == [2]
    dp.unapprove(out)
    assert {t.id: t.pinned for t in out.trips} == {1: True, 2: False}          # рейс с новым заказом дня остался закреплён


def _resize_setup(approve):
    from test_route_dispatch_resize import _setup
    ctx, stops, draft, ids = _setup()
    if approve:
        dp.approve(draft, 'at', 'u')
    return ctx, stops, draft, ids


def _ret(ctx, stops, draft, tid):
    from test_route_dispatch_resize import _ret as ret
    return ret(ctx, stops, draft, tid)


def test_approved_shrink_uses_existing_trips_like_unapproved():
    plain = _resize_setup(False)
    ctx, stops, draft, ids = _resize_setup(True)
    target = _ret(ctx, stops, draft, 1) - 30
    a = dp.apply_edit(*plain[:3], {'action': 'resize', 'trip': 1, 'return': target}, plain[3])
    b = dp.apply_edit(ctx, stops, draft, {'action': 'resize', 'trip': 1, 'return': target}, ids)
    # утверждение не мешает: магазины ушли в существующий рейс FORD, лишних рейсов со склада нет
    assert [(t.id, t.truck, t.stops) for t in b.trips] == [(t.id, t.truck, t.stops) for t in a.trips]
    assert len([t for t in b.trips if t.truck == FORD.car_code]) == 1


def test_approved_grow_takes_from_existing_trips_like_unapproved():
    plain = _resize_setup(False)
    ctx, stops, draft, ids = _resize_setup(True)
    target = _ret(ctx, stops, draft, 2) + 40
    a = dp.apply_edit(*plain[:3], {'action': 'resize', 'trip': 2, 'return': target}, plain[3])
    b = dp.apply_edit(ctx, stops, draft, {'action': 'resize', 'trip': 2, 'return': target}, ids)
    assert [(t.id, t.stops) for t in b.trips] == [(t.id, t.stops) for t in a.trips] and len(b.trips[1].stops) > 3


def test_resize_never_touches_started_trips_or_logist_pins():
    ctx, stops, draft, ids = _resize_setup(True)
    target = _ret(ctx, stops, draft, 1) - 30
    # рейс FORD уже грузится (сейчас — начало дня): магазины уходят новым рейсом, а не в него
    out = dp.apply_edit(ctx, stops, dp.Draft.from_json(draft.to_json()), {'action': 'resize', 'trip': 1, 'return': target},
                        ids, now_min=0.0)
    assert next(t for t in out.trips if t.id == 2).stops == [301, 302, 303]
    assert len([t for t in out.trips if t.truck == FORD.car_code]) == 2
    # рейс FORD закрепил сам логист (до утверждения) — тоже не трогается
    from test_route_dispatch_resize import _setup
    ctx, stops, draft, ids = _setup(pinned=True)
    dp.approve(draft, 'at', 'u')
    out = dp.apply_edit(ctx, stops, draft, {'action': 'resize', 'trip': 1, 'return': target}, ids)
    assert next(t for t in out.trips if t.id == 2).stops == [301, 302, 303]


def test_approver_name_goes_to_page_but_not_to_ai(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client)
    with client.session_transaction() as s:
        s['username'] = 'Արամ'
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    assert d['approved'] == {'at': '2026-10-01T08:00:00', 'by': 'Արամ'}
    with client.application.test_request_context():
        dd = views._load_day(state, state.store.load(), datetime(2026, 10, 1).date())
        body = views._dispatch_body(dd)
    assert body['approved'] == {'at': '2026-10-01T08:00:00'} and 'Արամ' not in json.dumps(body, ensure_ascii=False)


def test_approve_past_day_by_yerevan_date(client, monkeypatch):
    """Сервер ещё 30.09 (часы сервера), а в Ереване уже 01.10: прошедший день 30.09 — по Еревану."""
    _page_setup(client, monkeypatch, now=datetime(2026, 9, 30, 8, 0, tzinfo=ac.YEREVAN))
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-09-30', 'trucks': ['CAR2']}).get_json()
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 1, 0, 30, tzinfo=ac.YEREVAN))
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 20, 30))
    r = client.post('/api/routes/dispatch/edit', json={'date': '2026-09-30', 'rev': d['rev'], 'action': 'approve'})
    assert r.status_code == 400 and 'Անցած օրվա' in r.get_json()['error']


# ============================== план выпущен на терминалы (№80) ==============================

def test_released_is_sticky_through_unapprove_rebuild_and_undo():
    """Первое утверждение выпускает план на терминалы; снятие, повторное утверждение, пересборка и «Չեղարկել» отметку не
    снимают и не подменяют (машины уже в пути). «Չեղարկել» и не воскрешает её у невыпущенного плана."""
    ctx, base, *_, draft = _two_trips()
    assert draft.released is None and 'released' not in draft.to_json()
    dp.approve(draft, '2026-10-01T08:30:00', 'logist')
    first = {'at': '2026-10-01T08:30:00', 'by': 'logist'}
    assert draft.released == first and draft.to_json()['released'] == first
    dp.unapprove(draft)
    assert draft.approved is None and draft.released == first
    dp.approve(draft, '2026-10-01T12:00:00', 'other')
    dp.unapprove(draft)
    assert draft.released == first                                  # первое утверждение — не последнее
    rebuilt = dp.build(ctx, base, draft, [FORD.car_code, HOWO.car_code], 'now')
    assert rebuilt.released == first and rebuilt.approved is None
    # снимок «Չեղարկել» без отметки (сделан до выпуска) — отметка остаётся
    rebuilt.undo = {k: v for k, v in rebuilt.to_json().items() if k not in ('released', 'prediction', 'undo')}
    assert dp.apply_edit(ctx, base, rebuilt, {'action': 'undo'}, set()).released == first
    # и наоборот: план не выпущен — снимок с отметкой её не воскрешает
    plain = dp.build(ctx, base, None, [FORD.car_code], 'now')
    plain.undo = {**plain.to_json(), 'released': first}
    assert dp.apply_edit(ctx, base, plain, {'action': 'undo'}, set()).released is None


def test_released_from_json_garbage_and_legacy_approval():
    for junk in ('x', 1, [1], {'at': 1}, {'by': 'u'}, {'at': None}):
        assert dp.Draft.from_json({'released': junk}).released is None
    assert dp.Draft.from_json({'released': {'at': 'x', 'by': 5, 'extra': 1}}).released == {'at': 'x', 'by': None}
    # утверждён до №80 (отметки нет) — выпущен этим утверждением
    legacy = dp.Draft.from_json({'approved': {'at': 'a', 'by': 'u', 'pinned': [1]}})
    assert legacy.released == {'at': 'a', 'by': 'u'}
    raw = json.loads(json.dumps(legacy.to_json()))
    assert dp.Draft.from_json(raw) == legacy


def test_terminal_gets_plan_only_after_first_approval(client, monkeypatch):
    """«Развоз» → приложение водителя (courier.routes_link): собранный план не идёт на терминал, пока его не утвердили;
    после снятия утверждения и пересборки — идёт; стереть выпущенный план («Ջնջել երթերը») нельзя."""
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    orders = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 600.0, agent=2)]

    def terminal():
        view = rl.routes_view(state, D)
        return view, {car: [o.isn for o in rl.pick_orders(orders, D, view, car)] for car in ('CAR1', 'CAR2')}

    d = _build(client, ('CAR1', 'CAR2'))
    view, got = terminal()
    assert view.plan_exists and not view.released and got == {'CAR1': [], 'CAR2': []}
    assert not any(rl.invoice_owner(view, car)(cid) for car in ('CAR1', 'CAR2') for cid in (101, 102, 104))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    view, got = terminal()
    owner = _plan_stops(d)
    expected = {car: [o.isn for o in orders if owner.get(o.customer_id) == car] for car in ('CAR1', 'CAR2')}
    assert view.released and got == expected and sum(map(len, got.values())) == 3
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'unapprove'}).get_json()
    assert 'approved' not in d and state.store.load_dispatch(DAY)[0]['released']['at'] == '2026-10-01T08:00:00'
    assert terminal()[1] == expected                                 # снятие утверждения — машины уже в пути
    assert client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).status_code == 200
    view, got = terminal()
    assert view.released and sorted(sum(got.values(), [])) == sorted(o.isn for o in orders)
    assert client.get('/api/routes/dispatch?date=' + DAY).get_json()['released'] is True   # странице: «Ջնջել» не показывать
    r = client.post('/api/routes/dispatch/reset', json={'date': DAY})
    assert r.status_code == 400 and r.get_json()['errors']['_'] == views.PLAN_RELEASED
    view, got = terminal()
    assert view.released and sorted(sum(got.values(), [])) == sorted(o.isn for o in orders)


def test_reset_only_while_never_released(client, monkeypatch):
    """«Ջնջել երթերը»: не утверждённый ни разу план стирается, как раньше; страница прячет кнопку у выпущенного."""
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client, ('CAR1', 'CAR2'))
    assert 'released' not in d
    assert client.post('/api/routes/dispatch/reset', json={'date': DAY}).status_code == 200
    assert state.store.load_dispatch(DAY) is None
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert "$('dpReset').hidden = !plan || !!d.approved || !!d.released;" in js


def test_build_does_not_overwrite_approval_made_meanwhile(client, monkeypatch):
    """Сборка идёт секунды: другая вкладка тем временем утвердила план (№80: выпустила его на терминалы) — сборка не
    затирает черновик старой основой, а отвечает 409, как правки."""
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    _build(client, ('CAR1', 'CAR2'))
    real = dp.build_crewed

    def slow(*args, **kw):
        out = real(*args, **kw)
        raw, rev = state.store.load_dispatch(DAY)                     # вкладка Б: «Հաստատել օրվա պլանը»
        other = dp.approve(dp.Draft.from_json(raw), '2026-10-01T08:00:00', 'b')
        assert state.store.save_dispatch(DAY, other.to_json(), 'b', expected_rev=rev) is not None
        return out
    monkeypatch.setattr(views.dp, 'build_crewed', slow)
    r = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 409 and r.get_json()['conflict'] is True
    stored = state.store.load_dispatch(DAY)[0]
    assert stored['approved']['by'] == 'b' and stored['released']['by'] == 'b'

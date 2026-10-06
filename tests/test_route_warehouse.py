# -*- coding: utf-8 -*-
"""«Բեռնված է» со склада (ответ владельца №78, 9–12, 15; docs/plans/loading-season-plan.md, п. 3).

- черновик: отметка рейса {'at', 'by', 'pin'}; только по утверждённому плану; загруженный рейс закреплён — пересборка его
  не трогает, снятие утверждения не открепляет (держит отметка), логист не открепляет кнопкой, новые заказы дня в него не
  вставляются; ручная правка — только с подтверждением; снятие отметки открепляет, если держала только она;
- страница склада (API): дни — сегодня и следующий рабочий; без утверждения — без машин, отметка — 409; rev плана — как у
  правок «Развоза» (409 «թարմացրեք էջը»); снять со склада — только свою и в течение 10 минут; логист — всегда;
- «Развоз»: пересборка с загруженным рейсом его не меняет, машина загруженного рейса обязана остаться, план с отметками
  не стирается; правка загруженного рейса — 400 с loaded_confirm, с confirm_loaded — проходит;
- черновик без отметок — прежний до байта.

Синтетические данные, без ERP.  Запуск:  python -m pytest tests/test_route_warehouse.py -q
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer import waybill as wb  # noqa: E402
from test_route_dispatch_approve import _two_trips  # noqa: E402
from test_route_dispatch_same_day import DAY, FORD, HOWO, _build, _page_setup, _plan_stops, client  # noqa: E402,F401
from test_route_optimizer import _isn  # noqa: E402

AT = '2026-10-01T18:42:05'


# ============================== черновик ==============================

def test_mark_only_when_approved_and_roundtrip():
    *_, draft = _two_trips()
    with pytest.raises(dp.DispatchError, match='հաստատված չէ'):
        dp.mark_loaded(draft, 2, AT, 'sklad')
    dp.approve(draft, 'at', 'logist')
    dp.mark_loaded(draft, 2, AT, 'sklad')
    trip = dp.trip_of(draft, 2)
    assert trip.pinned and trip.loaded == {'at': AT, 'by': 'sklad', 'pin': False}
    with pytest.raises(dp.DispatchError, match='արդեն նշված'):
        dp.mark_loaded(draft, 2, AT, 'sklad')
    with pytest.raises(dp.DispatchError):
        dp.mark_loaded(draft, 99, AT, 'sklad')
    raw = json.loads(json.dumps(draft.to_json()))
    assert raw['trips'][1]['loaded'] == {'at': AT, 'by': 'sklad', 'pin': False}
    assert dp.Draft.from_json(raw) == draft
    # без отметок — поля нет; битая отметка — не загружен; отметка без «pinned» — всё равно закреплён (инвариант)
    assert all('loaded' not in t for t in _two_trips()[-1].to_json()['trips'])
    for junk in ('x', 5, {'at': 1}, {'by': 'x'}, [AT]):
        assert dp.Draft.from_json({'trips': [{'id': 1, 'truck': 'A', 'stops': [1], 'loaded': junk}]}).trips[0].loaded is None
    t = dp.Draft.from_json({'trips': [{'id': 1, 'truck': 'A', 'stops': [1], 'pinned': False, 'loaded': {'at': AT}}]}).trips[0]
    assert t.pinned and t.loaded == {'at': AT, 'by': None, 'pin': False}


def test_unapprove_keeps_loaded_pinned_unmark_releases():
    *_, draft = _two_trips()                     # 1 — закрепил логист, 2 — свободный, 3 — взятие заказа дня
    dp.approve(draft, 'at', 'logist')
    dp.mark_loaded(draft, 2, AT, 'sklad')
    dp.unapprove(draft)
    assert dp.trip_of(draft, 2).pinned and dp.trip_of(draft, 2).loaded['pin'] is True   # держит отметка
    dp.unmark_loaded(draft, 2)
    assert not dp.trip_of(draft, 2).pinned and dp.trip_of(draft, 2).loaded is None
    with pytest.raises(dp.DispatchError, match='նշված չէ'):
        dp.unmark_loaded(draft, 2)
    # снятие отметки при утверждённом плане: закрепление переходит утверждению — снятие утверждения откроет рейс
    dp.approve(draft, 'at', 'logist')
    dp.mark_loaded(draft, 2, AT, 'sklad')
    dp.unapprove(draft)
    dp.approve(draft, 'at2', 'logist')
    assert 2 not in draft.approved['pinned']
    dp.unmark_loaded(draft, 2)
    assert dp.trip_of(draft, 2).pinned and 2 in draft.approved['pinned']
    dp.unapprove(draft)
    assert not dp.trip_of(draft, 2).pinned
    # рейс, закреплённый логистом, после снятия отметки остаётся закреплённым
    dp.approve(draft, 'at', 'logist')
    dp.mark_loaded(draft, 1, AT, 'sklad')
    dp.unapprove(draft)
    dp.unmark_loaded(draft, 1)
    assert dp.trip_of(draft, 1).pinned


def test_loaded_trip_edits_need_confirmation_and_unpin_refused():
    ctx, base, orders, new, draft = _two_trips()
    ids = {o.isn for o in orders}
    dp.approve(draft, 'at', 'logist')
    dp.mark_loaded(draft, 2, AT, 'sklad')
    with pytest.raises(dp.DispatchError, match='նախ հանեք'):
        dp.apply_edit(ctx, base, draft, {'action': 'unpin', 'trip': 2}, ids)
    for edit in ({'action': 'move', 'customer_id': 103, 'from_trip': 2, 'to_trip': None, 'truck': FORD.car_code},
                 {'action': 'move', 'customer_id': 101, 'from_trip': 1, 'to_trip': 2},
                 {'action': 'defer_trip', 'trip': 2},
                 {'action': 'pin', 'trip': 2, 'truck': FORD.car_code},
                 {'action': 'exclude', 'order': orders[1].isn}):
        with pytest.raises(dp.LoadedEdit):
            dp.apply_edit(ctx, base, dp.Draft.from_json(draft.to_json()), edit, ids)
    # правка чужого рейса и закрепление той же машиной — без вопроса; закрепил логист — отметка больше не держит рейс
    dp.apply_edit(ctx, base, dp.Draft.from_json(draft.to_json()), {'action': 'unpin', 'trip': 1}, ids)
    got = dp.apply_edit(ctx, base, dp.Draft.from_json(draft.to_json()), {'action': 'pin', 'trip': 2, 'truck': HOWO.car_code}, ids)
    assert dp.trip_of(got, 2).loaded['pin'] is False
    # с подтверждением — правка проходит, отметка остаётся
    got = dp.apply_edit(ctx, base, dp.Draft.from_json(draft.to_json()),
                        {'action': 'move', 'customer_id': 103, 'from_trip': 2, 'to_trip': None, 'truck': FORD.car_code},
                        ids, loaded_ok=True)
    assert 103 not in dp.trip_of(got, 2).stops and dp.trip_of(got, 2).loaded is not None
    # новые заказы дня и перенос конца рейса в загруженный рейс не вставляют
    assert not dp._insertable(draft, dp.trip_of(draft, 2)) and not dp._movable(draft, dp.trip_of(draft, 2), ())


def test_rebuild_keeps_loaded_trip_and_mark():
    ctx, base, orders, new, draft = _two_trips()
    dp.approve(draft, 'at', 'logist')
    dp.mark_loaded(draft, 2, AT, 'sklad')
    dp.unapprove(draft)
    rebuilt = dp.build(ctx, base, draft, [FORD.car_code, HOWO.car_code], 'now2')
    t = dp.trip_of(rebuilt, 2)
    assert (t.truck, t.stops, t.pinned, t.loaded) == (HOWO.car_code, [102, 103], True, {'at': AT, 'by': 'sklad', 'pin': True})
    assert sum(x.stops.count(102) for x in rebuilt.trips) == 1


# ============================== страница склада и «Развоз» (API) ==============================

def _setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN), user='sklad'):
    state, _ = _page_setup(client, monkeypatch, now=now)
    with client.session_transaction() as s:
        s['username'] = user
    return state


def _as(client, user):
    with client.session_transaction() as s:
        s['username'] = user


def test_warehouse_flow_approval_rev_and_unmark_any_time(client, monkeypatch):
    state = _setup(client, monkeypatch)
    w = client.get('/api/routes/warehouse').get_json()
    assert (w['day'], w['days'], w['approved'], w['planned'], w['trucks']) == (DAY, [DAY, '2026-10-02'], False, False, [])
    assert client.get('/api/routes/warehouse?date=2026-10-05').status_code == 400        # не сегодня и не следующий
    d = _build(client, ('CAR1', 'CAR2'))
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert (w['approved'], w['planned'], w['trucks']) == (False, True, [])               # не утверждён — машин нет
    trip = d['plan']['trucks'][0]['trips'][0]['id']
    r = client.post('/api/routes/warehouse/loaded', json={'date': DAY, 'rev': d['rev'], 'trip': trip, 'loaded': True})
    assert r.status_code == 409 and 'հաստատված չէ' in r.get_json()['error']             # ответ 15
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    w = client.get('/api/routes/warehouse?date=' + DAY).get_json()
    assert w['approved'] and w['rev'] == d['rev']
    first = w['trucks'][0]['trips'][0]
    assert first['loaded'] is None and {'id', 'no', 'of', 'kg', 'stops', 'loading_start', 'depart'} <= set(first)
    stale = client.post('/api/routes/warehouse/loaded', json={'date': DAY, 'rev': w['rev'] - 1, 'trip': first['id'],
                                                              'loaded': True})
    assert stale.status_code == 409 and stale.get_json()['error'] == views.WAREHOUSE_STALE
    r = client.post('/api/routes/warehouse/loaded', json={'date': DAY, 'rev': w['rev'], 'trip': first['id'], 'loaded': True})
    assert r.status_code == 200, r.get_json()
    w = r.get_json()
    got = w['trucks'][0]['trips'][0]['loaded']
    assert got == {'at': '08:00', 'by': 'sklad'}
    assert state.store.load_dispatch(DAY)[0]['trips'][0]['loaded']['by'] == 'sklad'
    # «Развоз»: значок «Բեռնված է ժ. 08:00 (sklad)» — кто — только странице (loaded_by), в плане и в AI его нет
    d2 = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    tr = next(x for t in d2['plan']['trucks'] for x in t['trips'] if x['id'] == first['id'])
    assert tr['loaded'] == {'at': '08:00'} and tr['pinned'] and d2['loaded_by'] == {str(first['id']): 'sklad'}
    from route_optimizer import ai_chat
    with client.application.test_request_context():
        body = views._dispatch_body(views._load_day(state, views._bundle(state), date.fromisoformat(DAY)))
    assert 'sklad' not in json.dumps(ai_chat._prune(body), ensure_ascii=False)
    # правка с прежним rev — 409
    assert client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'unapprove'}).status_code == 409
    # ответ 19: снять — в любое время и любой пользователь склада (не только тот, кто отметил)
    _as(client, 'other')
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 1, 17, 30, tzinfo=ac.YEREVAN))
    r = client.post('/api/routes/warehouse/loaded', json={'date': DAY, 'rev': w['rev'], 'trip': first['id'], 'loaded': False})
    assert r.status_code == 200 and r.get_json()['trucks'][0]['trips'][0]['loaded'] is None
    trip_now = next(t for t in state.store.load_dispatch(DAY)[0]['trips'] if t['id'] == first['id'])
    assert trip_now['pinned'] is True                                  # держит утверждение — закреплён, как и было
    assert 'loaded' not in state.store.load_dispatch(DAY)[0]['trips'][0]
    # следующий рабочий день — плана нет
    w = client.get('/api/routes/warehouse?date=2026-10-02').get_json()
    assert w['planned'] is False
    assert client.post('/api/routes/warehouse/loaded', json={'date': '2026-10-02', 'rev': 0, 'trip': 1,
                                                             'loaded': True}).status_code == 409
    for bad in ({'date': DAY, 'rev': w['rev'], 'trip': 1}, {'date': DAY, 'rev': 1, 'trip': 1, 'loaded': 'yes'},
                {'rev': 1, 'trip': 1, 'loaded': True}, {'date': '2026-10-05', 'rev': 1, 'trip': 1, 'loaded': True}):
        assert client.post('/api/routes/warehouse/loaded', json=bad).status_code == 400, bad


def test_dispatch_logist_marks_unmarks_and_loaded_trip_survives_rebuild(client, monkeypatch):
    state = _setup(client, monkeypatch, user='logist')
    d = _build(client, ('CAR1', 'CAR2'))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    trucks = {t['car_code']: t for t in d['plan']['trucks']}
    loaded_truck = sorted(trucks)[0]
    trip = trucks[loaded_truck]['trips'][0]
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'loaded', 'trip': trip['id']})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'unapprove'}).get_json()
    # пересборка: загруженный рейс — тот же состав, та же машина, отметка на месте
    other = [c for c in ('CAR1', 'CAR2') if c != loaded_truck]
    r = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': other})
    assert r.status_code == 400 and views.LOADED_TRUCK_OFF in r.get_json()['error']
    d = _build(client, ('CAR1', 'CAR2'))
    tr = next(x for t in d['plan']['trucks'] for x in t['trips'] if x['id'] == trip['id'])
    assert [s['customer_id'] for s in tr['stops']] == [s['customer_id'] for s in trip['stops']]
    assert tr['truck'] == loaded_truck and tr['pinned'] and d['loaded_by'][str(tr['id'])] == 'logist'
    # «Ջնջել երթերը» — нет, пока есть отметки
    r = client.post('/api/routes/dispatch/reset', json={'date': DAY})
    assert r.status_code == 400 and r.get_json()['errors']['_'] == views.PLAN_LOADED
    # правка загруженного рейса: сначала вопрос, с подтверждением — проходит
    cid = tr['stops'][0]['customer_id']
    body = {'date': DAY, 'rev': d['rev'], 'action': 'move', 'customer_id': cid, 'from_trip': tr['id'], 'to_trip': None,
            'truck': None}
    r = client.post('/api/routes/dispatch/edit', json=body)
    assert r.status_code == 400 and r.get_json()['loaded_confirm'] is True
    r = client.post('/api/routes/dispatch/edit', json={**body, 'confirm_loaded': True})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    # логист снимает отметку — без срока; рейс открепляется (держала только отметка)
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'unloaded', 'trip': tr['id']})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    left = [x for t in d['plan']['trucks'] for x in t['trips'] if x['id'] == tr['id']]
    assert all('loaded' not in x and not x['pinned'] for x in left)
    # отметок нет — «Ջնջել երթերը» держит уже не склад, а выпуск плана водителям (№80: план утверждали)
    r = client.post('/api/routes/dispatch/reset', json={'date': DAY})
    assert r.status_code == 400 and r.get_json()['errors']['_'] == views.PLAN_RELEASED


def test_warehouse_goods_rows_of_trip(client, monkeypatch):
    state = _setup(client, monkeypatch)
    d = _build(client, ('CAR1', 'CAR2'))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    products = {10: wb.Product(10, '0101', 'Կաթ 1լ', 'հատ', 1.05, 12)}
    state.waybill_loader = lambda isns: wb.Lines({i.upper(): ((10, 30.0),) for i in isns}, frozenset(), products)
    t = d['plan']['trucks'][0]
    q = f"?date={DAY}&truck={t['car_code']}&trip={t['trips'][0]['id']}&rev={d['rev']}"
    g = client.get('/api/routes/warehouse/goods' + q).get_json()
    n = sum(len(s['orders']) for s in t['trips'][0]['stops'])
    assert g['rows'] == [{'code': '0101', 'name': 'Կաթ 1լ', 'unit': 'հատ', 'qty': 30 * n, 'pack': 12, 'packs': 30 * n // 12,
                          'loose': 30 * n % 12, 'kg': round(30 * n * 1.05, 1), 'unknown': False}]
    assert client.get(f"/api/routes/warehouse/goods?date={DAY}&truck={t['car_code']}&trip={t['trips'][0]['id']}"
                      f"&rev={d['rev'] - 1}").status_code == 409
    assert client.get(f"/api/routes/warehouse/goods?date={DAY}&truck=ZZZ&trip=1&rev={d['rev']}").status_code == 409
    assert client.get(f"/api/routes/warehouse/goods?date={DAY}&truck=CAR1&trip=x&rev=1").status_code == 400


def test_warehouse_waybill_is_dispatch_waybill_of_approved_plan(client, monkeypatch):
    """«Տպել բեռնագիրը» склада: ровно ответ Բեռնագիր «Развоза» той же машины и дня (общий _waybill_body; страница печатает
    тем же рендером) плюс день недели; только утверждённый план (ответ 15), rev плана на странице; ERP до проверок не
    читается."""
    state = _setup(client, monkeypatch)
    asked = []
    products = {10: wb.Product(10, '0101', 'Կաթ 1լ', 'հատ', 1.05, 12)}

    def loader(isns):
        asked.append(sorted(isns))
        return wb.Lines({i.upper(): ((10, 30.0),) for i in isns}, frozenset(), products)

    state.waybill_loader = loader
    d = _build(client, ('CAR1', 'CAR2'))
    code = d['plan']['trucks'][0]['car_code']
    get = lambda **q: client.get('/api/routes/warehouse/waybill', query_string={'date': DAY, 'truck': code, **q})  # noqa
    r = get(rev=d['rev'])
    assert r.status_code == 409 and r.get_json()['error'] == views.WAREHOUSE_STALE      # не утверждён
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    r = get(rev=d['rev'])
    assert r.status_code == 200, r.get_json()
    got = r.get_json()
    same = client.get('/api/routes/dispatch/waybill', query_string={'date': DAY, 'truck': code, 'rev': d['rev']}).get_json()
    assert got == {**same, 'weekday': date.fromisoformat(DAY).isoweekday()}
    truck = next(t for t in d['plan']['trucks'] if t['car_code'] == code)
    assert (got['rev'], got['day'], [x['id'] for x in got['trips']]) == (d['rev'], DAY, [x['id'] for x in truck['trips']])
    assert got['trips'][0]['rows'] and got['trips'][0]['rows'][0]['code'] == '0101'
    asked.clear()
    for q, status in (({'rev': d['rev'] - 1}, 409), ({'rev': d['rev'] + 1}, 409), ({'rev': d['rev'], 'truck': 'ZZZ'}, 409),
                      ({}, 400), ({'rev': 'x'}, 400), ({'rev': '-1'}, 400), ({'rev': d['rev'], 'truck': ' '}, 400),
                      ({'rev': d['rev'], 'truck': 'X' * 65}, 400), ({'rev': d['rev'], 'date': '2026-10-05'}, 400),
                      ({'rev': d['rev'], 'date': 'x'}, 400)):
        r = get(**q)
        assert r.status_code == status, (q, r.get_json())
        if status == 409:
            assert r.get_json()['error'] == views.WAREHOUSE_STALE, q
    assert asked == []                                                  # отказы — без чтения ERP
    # утверждение сняли после открытия страницы — тот же rev уже не тот, а и с новым rev — 409 (план не утверждён)
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'unapprove'}).get_json()
    assert get(rev=d['rev']).status_code == 409 and asked == []


def test_reset_race_with_warehouse_mark_is_409(client, monkeypatch):
    """«Ջնջել երթերը» стирает ровно прочитанный черновик: склад успел отметить — 409, отметка цела."""
    state = _setup(client, monkeypatch)
    _build(client, ('CAR1', 'CAR2'))
    real = state.store.delete_dispatch
    monkeypatch.setattr(state.store, 'delete_dispatch', lambda day, expected_rev=None: real(day, expected_rev - 1))
    r = client.post('/api/routes/dispatch/reset', json={'date': DAY})
    assert r.status_code == 409 and state.store.load_dispatch(DAY) is not None


# ============================== ревью: любая правка, меняющая груз загруженного рейса, — с подтверждением ==============


def _loaded_day(client, monkeypatch):
    """План 01.10 двумя машинами, утверждён, первый рейс CAR1 отмечен «Բեռնված է»; (state, ответ дня, рейс)."""
    state = _setup(client, monkeypatch, user='logist')
    d = _build(client, ('CAR1', 'CAR2'))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    tr = next(x for t in d['plan']['trucks'] for x in t['trips'])
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'loaded',
                                                       'trip': tr['id']}).get_json()
    return state, d, tr


def _edit(client, d, **body):
    return client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], **body})


def test_agents_filter_that_drops_loaded_stop_needs_confirmation(client, monkeypatch):
    state, d, tr = _loaded_day(client, monkeypatch)
    before = state.store.load_dispatch(DAY)
    r = _edit(client, d, action='agents', off=[1, 2])
    assert r.status_code == 400 and r.get_json()['loaded_confirm'] is True and r.get_json()['error'] == dp.LOADED_EDIT
    assert state.store.load_dispatch(DAY) == before                    # ничего не сохранено
    r = _edit(client, d, action='agents', off=[1, 2], confirm_loaded=True)
    assert r.status_code == 200, r.get_json()


def test_apply_settings_and_same_day_paths_check_loaded_cargo(client, monkeypatch):
    state, d, tr = _loaded_day(client, monkeypatch)
    # настройки дня: менеджеры 1 и 2 сняты правилом — «Կիրառել կարգավորումները» снимет точки загруженного рейса
    state.store.save(st.Changes({**state.store.load().settings, 'dispatch_agents_off': [1, 2]}, False, None, (), ()),
                     'qa')
    before = state.store.load_dispatch(DAY)
    r = _edit(client, d, action='apply_settings')
    assert r.status_code == 400 and r.get_json()['loaded_confirm'] is True
    assert state.store.load_dispatch(DAY) == before
    assert _edit(client, d, action='apply_settings', confirm_loaded=True).status_code == 200


def test_exclude_order_and_defer_neighbour_with_heavy_order_need_confirmation(client, monkeypatch):
    state, d, tr = _loaded_day(client, monkeypatch)
    order = tr['stops'][0]['orders'][0]['isn']
    r = _edit(client, d, action='exclude', order=order)
    assert r.status_code == 400 and r.get_json()['loaded_confirm'] is True
    # «везти завтра» другого рейса, где тот же клиент (тяжёлый заказ на два рейса), снял бы точку и с загруженного
    ctx, base, orders, new, draft = _two_trips()
    dp.approve(draft, 'at', 'logist')
    draft.trips.append(dp.DraftTrip(4, HOWO.car_code, [102], pinned=True))
    dp.mark_loaded(draft, 4, AT, 'sklad')
    cargo = dp.loaded_cargo(draft, base)
    got = dp.apply_edit(ctx, base, dp.Draft.from_json(draft.to_json()), {'action': 'defer_trip', 'trip': 2},
                        {o.isn for o in orders})
    assert dp.loaded_cargo(got, base, cargo) != cargo                 # views ответит loaded_confirm


def test_same_day_drop_of_order_in_loaded_trip_needs_confirmation(client, monkeypatch):
    state = _setup(client, monkeypatch, user='logist')
    d = _build(client, ('CAR1', 'CAR2'))
    res = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10)]}).get_json()
    d = _edit(client, d, action='same_day', orders=[_isn(10)], option=res['options'][0]['key']).get_json()
    d = _edit(client, d, action='approve').get_json()
    tr = next(x for t in d['plan']['trucks'] for x in t['trips'] if any(s['customer_id'] == 103 for s in x['stops']))
    d = _edit(client, d, action='loaded', trip=tr['id']).get_json()
    r = _edit(client, d, action='same_day_drop', orders=[_isn(10)])
    assert r.status_code == 400 and r.get_json()['loaded_confirm'] is True
    assert _edit(client, d, action='same_day_drop', orders=[_isn(10)], confirm_loaded=True).status_code == 200


def test_bad_mark_time_and_build_without_loaded_truck():
    t = dp.Draft.from_json({'trips': [{'id': 1, 'truck': 'A', 'stops': [1], 'loaded': {'at': 'вчера'}}]}).trips[0]
    assert t.loaded is None                                            # не ISO — не загружен (страница склада не падает)
    ctx, base, orders, new, draft = _two_trips()
    dp.approve(draft, 'at', 'logist')
    dp.mark_loaded(draft, 2, AT, 'sklad')
    with pytest.raises(dp.DispatchError, match='Բեռնված երթերի մեքենաները'):
        dp.build(ctx, base, draft, [FORD.car_code], 'now')             # и путь «что если» AI — тот же отказ


def test_build_with_new_manager_filter_dropping_loaded_stop_needs_confirmation(client, monkeypatch):
    """«Վերակազմել» с изменённым на странице фильтром менеджеров (agents_off сборки): точки загруженного рейса — только с
    подтверждением; без него ничего не сохраняется (ревью: probe_build_cargo)."""
    state, d, tr = _loaded_day(client, monkeypatch)
    d = _edit(client, d, action='unapprove').get_json()
    before = state.store.load_dispatch(DAY)
    body = {'date': DAY, 'trucks': ['CAR1', 'CAR2'], 'agents_off': [1, 2]}
    r = client.post('/api/routes/dispatch/build', json=body)
    assert r.status_code == 400 and r.get_json()['loaded_confirm'] is True
    assert state.store.load_dispatch(DAY) == before
    r = client.post('/api/routes/dispatch/build', json={**body, 'confirm_loaded': True})
    assert r.status_code == 200, r.get_json()
    # тот же фильтр, что у плана, — без вопроса
    assert client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).status_code == 200

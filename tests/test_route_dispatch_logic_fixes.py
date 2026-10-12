# -*- coding: utf-8 -*-
"""«Развоз»: правки по логическому ревью 12.10 (Codex, P1/P2). Каждый тест — сценарий ревью: до правки он падал.

1. ручной перенос магазина в другую машину переживает пересборку (Draft.locks);
2. магазин, разделённый между машинами, — каждый заказ ERP только одной машине (dp.order_trucks);
3. накладная одного заказа не прячет другой заказ того же клиента (courier.erp_day.pending_orders);
4. отправленный снимок фиксирует заказы (Draft.sent['orders']): заказ, пришедший после отправки, — не у водителя, а у
   логиста как «не отправлено»;
5. пересборка из устаревшей вкладки — 409 (rev);
6. разбиение отправленного рейса на два — «не отправлено»;
8. «сегодня» — одно, по Еревану;
9. ERP не ответила при обновлении — явный флаг erp_ok;
10. экипаж: перед утверждением и отправкой — проверка, после смены водителя отправленного плана — «не отправлено».
Синтетические данные, без ERP.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_logic_fixes.py -q
"""
import json
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from test_route_optimizer import EAST, WEST, _dorder, _dp_ctx, _dp_stops, _info, _isn, client  # noqa: E402,F401

TRUCKS = ['991AT61', '333DO33']


def _where(draft, cid):
    return sorted({t.truck for t in draft.trips if cid in t.stops})


# ============================== 1. ручной перенос переживает пересборку ==============================

def test_manual_move_survives_rebuild_until_explicit_unlock():
    ctx = _dp_ctx()
    stops, orders = _dp_stops(EAST + WEST)
    ids = {o.isn for o in orders}
    draft = dp.build(ctx, stops, None, TRUCKS, 'now')
    src = next(t for t in draft.trips if 104 in t.stops)
    natural, other = src.truck, next(c for c in TRUCKS if c != src.truck)
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'move', 'customer_id': 104, 'from_trip': src.id,
                                              'to_trip': None, 'truck': other}, ids)
    dst = next(t for t in draft.trips if 104 in t.stops)
    assert _where(draft, 104) == [other] and draft.locks == {104: other}
    # пересборка — магазин остаётся у машины, куда его перенёс логист (до правки — возвращался в natural)
    rebuilt = dp.build(ctx, stops, draft, TRUCKS, 'now2')
    assert _where(rebuilt, 104) == [dst.truck] and rebuilt.locks == {104: dst.truck}
    view = dp.plan_view(ctx, stops, rebuilt, _info)
    marks = {s['customer_id']: s.get('locked') for t in view['trucks'] for tr in t['trips'] for s in tr['stops']}
    assert marks[104] is True and not any(v for c, v in marks.items() if c != 104)
    # переживает и сохранение (JSON)
    assert dp.Draft.from_json(json.loads(json.dumps(rebuilt.to_json()))).locks == {104: dst.truck}
    # снимает только явное действие — тогда пересборка снова решает сама
    copy = dp.Draft.from_json(rebuilt.to_json())
    cleared = dp.apply_edit(ctx, stops, rebuilt, {'action': 'unlock_all'}, ids)
    assert cleared.locks == {}
    assert _where(dp.build(ctx, stops, cleared, TRUCKS, 'now3'), 104) == [natural]
    one = dp.apply_edit(ctx, stops, copy, {'action': 'unlock', 'customer_id': 104}, ids)
    assert one.locks == {}


def test_lock_dropped_when_store_removed_or_truck_not_in_build():
    ctx = _dp_ctx()
    stops, orders = _dp_stops(EAST + WEST)
    ids = {o.isn for o in orders}
    draft = dp.build(ctx, stops, None, TRUCKS, 'now')
    src = next(t for t in draft.trips if 101 in t.stops)
    other = next(c for c in TRUCKS if c != src.truck)
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'move', 'customer_id': 101, 'from_trip': src.id,
                                              'to_trip': None, 'truck': other}, ids)
    assert draft.locks == {101: other}
    # пересборка без этой машины — решения нет (машину убрал сам логист)
    assert dp.build(ctx, stops, draft, [src.truck], 'now2').locks == {}
    # убран из рейсов — решения нет
    trip = next(t for t in draft.trips if 101 in t.stops)
    out = dp.apply_edit(ctx, stops, draft, {'action': 'move', 'customer_id': 101, 'from_trip': trip.id,
                                            'to_trip': None, 'truck': None}, ids)
    assert out.locks == {}
    # старый черновик без поля — прежний до байта
    assert 'locks' not in dp.Draft(['A'], trips=[dp.DraftTrip(1, 'A', [5])]).to_json()
    assert dp.Draft.from_json({'locks': {'7': 'A', 'x': 'B', '8': 3, '9': ''}}).locks == {7: 'A'}


# ============================== 6. границы рейсов отправленного плана ==============================

def test_split_of_sent_trip_into_two_trips_is_unsent():
    draft = dp.Draft(['A'], trips=[dp.DraftTrip(1, 'A', [101, 102])], next_id=2)
    dp.approve(draft, 'a', 'u')
    assert dp.unsent(draft) is None
    draft.trips = [dp.DraftTrip(1, 'A', [101]), dp.DraftTrip(2, 'A', [102])]
    assert dp.unsent(draft) == {'trucks': ['A'], 'orders': False}
    dp.send(draft, 'b', 'u')
    assert dp.unsent(draft) is None
    # и обратно: два рейса слили в один
    draft.trips = [dp.DraftTrip(1, 'A', [101, 102])]
    assert dp.unsent(draft) == {'trucks': ['A'], 'orders': False}


# ============================== 2. магазин нескольких машин — заказ одной машине ==============================

def _view(trips):
    from courier import routes_link as rl
    return rl.RoutesView(plan_exists=True, released=True, trips=trips)


def _terminal_of(orders, view, cars=('A', 'B')):
    from courier import routes_link as rl
    return {car: [o.isn for o in rl.pick_orders(orders, D, view, car)] for car in cars}


D = date(2026, 10, 1)


def test_split_store_two_orders_each_truck_gets_one():
    orders = [_dorder(1, 101, 600.0), _dorder(2, 101, 500.0)]
    trucks, warn = dp.order_trucks([('A', [101]), ('B', [101])], orders)
    assert trucks == {_isn(1): 'A', _isn(2): 'B'} and warn == []
    # терминалы (снимок без заказов — отбор по плану): каждый заказ — одной машине (до правки обе получали оба)
    got = _terminal_of(orders, _view((('A', (101,)), ('B', (101,)))))
    assert got == {'A': [_isn(1)], 'B': [_isn(2)]}


def test_split_store_single_order_goes_wholly_to_bigger_share_with_warning():
    orders = [_dorder(1, 101, 900.0)]
    # у B два рейса к магазину из трёх — его доля больше
    trucks, warn = dp.order_trucks([('A', [101]), ('B', [101]), ('B', [102, 101])], orders)
    assert trucks == {_isn(1): 'B'} and warn == [{'customer_id': 101, 'trucks': ['A', 'B'], 'empty': ['A']}]
    got = _terminal_of(orders, _view((('A', (101,)), ('B', (101,)), ('B', (102, 101)))))
    assert got == {'A': [], 'B': [_isn(1)]}
    # равные доли — машина, что раньше в плане; детерминированно
    assert dp.order_trucks([('B', [101]), ('A', [101])], orders)[0] == {_isn(1): 'B'}


def test_split_store_warning_on_page_and_in_published_snapshot():
    ctx = _dp_ctx()
    stops, orders = _dp_stops([(101, (40.200, 44.640), 900.0), (102, (40.205, 44.650), 200.0)])
    draft = dp.Draft(TRUCKS, trips=[dp.DraftTrip(1, TRUCKS[0], [101]), dp.DraftTrip(2, TRUCKS[1], [101, 102])], next_id=3)
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    assert view['order_split'] == [{'customer_id': 101, 'trucks': sorted(TRUCKS), 'empty': [TRUCKS[1]]}]
    pub = dp.published_orders(draft, stops)
    assert pub == {orders[0].isn: [TRUCKS[0], 900.0, 10000.0], orders[1].isn: [TRUCKS[1], 200.0, 10000.0]}
    assert len({isn for isn in pub}) == len(orders)                                  # инвариант: заказ — одной машине
    # без разделённых магазинов ключа нет — план прежний до байта
    one = dp.Draft(TRUCKS, trips=[dp.DraftTrip(1, TRUCKS[0], [101, 102])], next_id=2)
    assert 'order_split' not in dp.plan_view(ctx, stops, one, _info, explain=False)


# ============================== 4. снимок отправки фиксирует заказы ==============================

def _page(client, monkeypatch):
    from test_route_dispatch_send import NOW
    from test_route_dispatch_same_day import _page_setup
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    return state


def _post(client, url, body):
    return client.post(url, json={'date': '2026-10-01', **body})


def _ok(r):
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _erp_orders(state, change):
    """ERP изменилась: загрузчик заказов отдаёт change(заказы окна); кэш заказов дня сброшен (как через TTL)."""
    base = state.dispatch_loader
    state.dispatch_loader = lambda since, until, day: (lambda d: replace(d, orders=tuple(change(d.orders, since, until))))(
        base(since, until, day))
    state.dispatch_cache.clear()


def test_order_added_in_erp_after_send_not_on_terminal_until_resent(client, monkeypatch):
    from courier import routes_link as rl
    from test_route_dispatch_send import ORDERS
    state = _page(client, monkeypatch)
    d = _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2']}))
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'}))
    assert d['unsent'] is None
    owner = {s['customer_id']: tr['truck'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}
    late = _dorder(40, 101, 120.0)                                   # магазин 101 уже в плане; заказ — после отправки
    _erp_orders(state, lambda orders, since, until: [*orders, *(o for o in [late] if since <= o.order_date < until)])
    every = [*ORDERS, late]

    def terminal():
        view = rl.routes_view(state, D)
        return {car: sorted(o.isn for o in rl.pick_orders(every, D, view, car)) for car in ('CAR1', 'CAR2')}
    # водитель его не видит (до правки — видел сразу: терминал перечитывал ERP по клиентам рейсов)
    assert late.isn not in sum(terminal().values(), [])
    # логист видит: план не отправлен — у машины магазина изменились заказы
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    assert d['unsent']['trucks'] == [owner[101]] and d['unsent']['orders_changed'] == 1
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'send'}))
    assert d['unsent'] is None and late.isn in terminal()[owner[101]]
    # изменился вес отправленного заказа — снова «не отправлено»
    heavier = _dorder(40, 101, 300.0)
    _erp_orders(state, lambda orders, since, until: [heavier if o.isn == heavier.isn else o for o in orders])
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    assert d['unsent']['orders_changed'] == 1


# ============================== 10. экипаж перед отправкой и после неё ==============================

def test_send_asks_confirmation_on_crew_problems_and_crew_change_marks_unsent(client, monkeypatch):
    state = _page(client, monkeypatch)
    state.store.save_truck_driver('CAR1', '2026-10-01', 'Արամ', 'qa')
    state.store.save_truck_driver('CAR2', '2026-10-01', 'Կարեն', 'qa')
    d = _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2']}))
    car = sorted({t['car_code'] for t in d['plan']['trucks'] if t['trips']})[0]
    name = d['crew']['trucks'][car]['name']
    # водитель машины плана не вышел до утверждения — утверждение спрашивает, со списком (не запрещает)
    state.store.save_driver_absence(name, '2026-10-01', None, 'qa')
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    assert {'truck': car, 'kind': 'absent'} in d['crew_problems']
    r = _post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'})
    assert r.status_code == 400 and r.get_json()['crew_confirm'] is True
    assert {'truck': car, 'kind': 'absent'} in r.get_json()['problems']
    assert 'sent' not in _ok(client.get('/api/routes/dispatch?date=2026-10-01'))      # без подтверждения — не сохранено
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve', 'confirm_crew': True}))
    assert d['sent'] and d['unsent'] is None
    # вышел снова — экипаж отправленного плана другой: «не отправлено» по экипажу машины, пока логист не отправит
    state.store.save_driver_present(name, '2026-10-01', 'qa')
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    assert d['unsent']['crew'] == [car] and 'crew_problems' not in d
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'send'}))
    assert d['unsent'] is None
    # машину сняли в настройках после отправки — план требует внимания, отправка — с подтверждением
    trucks = [{'car_code': 'CAR1', 'capacity_kg': 10000, 'fuel_l_per_100km': 30, 'active': car != 'CAR1', 'center_ok': True},
              {'car_code': 'CAR2', 'capacity_kg': 3500, 'fuel_l_per_100km': 16, 'active': car != 'CAR2'}]
    _ok(client.post('/api/routes/settings', json={'trucks': trucks}))
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    assert car in d['unsent']['crew'] and {'truck': car, 'kind': 'off'} in d['crew_problems']
    r = _post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'send'})
    assert r.status_code == 400 and r.get_json()['crew_confirm'] is True


def test_crew_not_tracked_means_no_driver_check(client, monkeypatch):
    """Водителей дня не записано ни у одной машины — экипаж не ведут: машина без водителя едет, как до №77, без вопроса."""
    _page(client, monkeypatch)
    d = _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2']}))
    assert 'crew_problems' not in d
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'}))
    assert d['unsent'] is None


# ============================== 3. накладная одного заказа не прячет другой ==============================

def _sale(isn_i, cid, replaces=()):
    from courier.erp_day import Doc
    return Doc(f'S:{_isn(isn_i)}', 'invoice', _isn(isn_i), f'S{isn_i}', cid, 1, '1', 100.0, tuple(replaces))


def test_invoice_of_one_order_keeps_other_order_of_same_customer():
    from courier.erp_day import pending_orders
    invoiced = _dorder(1, 101, 100.0, day=D, shipped=D)       # из него сделана накладная машины
    second = _dorder(2, 101, 50.0, day=D)                       # второй заказ того же магазина — ещё без накладной
    other = _dorder(3, 102, 70.0, day=D)
    sale = _sale(90, 101, [f'O:{invoiced.isn}'])
    # до правки: накладная клиента 101 прятала и second (отбор по клиенту) — водитель его не видел
    assert pending_orders([invoiced, second, other], [sale]) == [second, other]
    # накладная без заказа-родителя (не из заказа) — что она заменила, неизвестно: у клиента заказов O: нет
    assert pending_orders([second, other], [_sale(91, 101)]) == [other]
    # накладная заменила заказ, по которому shipped ещё не отметился — тоже не точка O:
    fresh = _dorder(4, 102, 70.0, day=D)
    assert pending_orders([fresh], [_sale(92, 102, [f'O:{fresh.isn}'])]) == []


# ============================== 5. пересборка из устаревшей вкладки ==============================

def test_rebuild_from_stale_tab_is_conflict(client, monkeypatch):
    _page(client, monkeypatch)
    a = _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2'], 'rev': 0}))
    # вкладка B правит план (rev растёт), вкладка A держит прежний rev
    trip = next(tr for t in a['plan']['trucks'] for tr in t['trips'])
    b = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': a['rev'], 'action': 'pin', 'trip': trip['id'],
                                                         'truck': trip['truck']}))
    r = _post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2'], 'rev': a['rev']})
    assert r.status_code == 409 and r.get_json()['conflict'] is True
    # правка B цела
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    assert d['rev'] == b['rev'] and any(tr['pinned'] for t in d['plan']['trucks'] for tr in t['trips'])
    assert _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2'], 'rev': b['rev']}))['rev'] > b['rev']


# ============================== 8. одно «сегодня» — по Еревану ==============================

def test_one_today_by_yerevan_when_server_clock_is_utc(client, monkeypatch):
    """00:30 01.10 в Ереване = 20:30 30.09 по UTC (часы сервера). До правки страница считала сегодняшним 30.09 (часы
    сервера), а утверждение — уже прошедшим (Ереван): страница предлагала то, что сервер отказывал."""
    from datetime import timezone
    from route_optimizer import views
    _page(client, monkeypatch)
    d = _ok(_post(client, '/api/routes/dispatch/build', {'date': '2026-09-30', 'trucks': ['CAR2']}))

    class ServerUtc(datetime):
        @classmethod
        def now(cls, tz=None):
            at = datetime(2026, 9, 30, 20, 30, tzinfo=timezone.utc)
            return at.replace(tzinfo=None) if tz is None else at.astimezone(tz)
    monkeypatch.setattr(views, 'datetime', ServerUtc)
    monkeypatch.setattr(views, '_clock', REAL['_clock'])
    monkeypatch.setattr(views, '_yerevan_now', REAL['_yerevan_now'])
    monkeypatch.setattr(views, '_same_day_now', REAL['_same_day_now'])
    page = _ok(client.get('/api/routes/dispatch?date=2026-09-30'))
    assert page['is_past'] is True and page['today'] == '2026-10-01'
    r = _post(client, '/api/routes/dispatch/edit', {'date': '2026-09-30', 'rev': d['rev'], 'action': 'approve'})
    assert r.status_code == 400 and r.get_json()['errors']['_'] == views.PAST_DAY_APPROVE
    # день по умолчанию — следующий рабочий после сегодняшнего по Еревану (01.10 → 02.10), не после 30.09
    assert _ok(client.get('/api/routes/dispatch'))['day'] == '2026-10-02'


def _real_clocks():
    from route_optimizer import views
    return {k: getattr(views, k) for k in ('_clock', '_yerevan_now', '_same_day_now')}


REAL = _real_clocks()   # до подмен тестов


# ============================== 9. ERP не ответила при обновлении ==============================

def test_same_day_erp_failure_is_explicit_on_page_and_status(client, monkeypatch):
    import time as _time
    from route_optimizer.erp import ErpError
    state = _page(client, monkeypatch)
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    assert 'erp_ok' not in d and d['same_day']['count'] == 3                      # прочитано — ответ прежний
    good = state.same_day_cache[D][1]

    def down(day):
        raise ErpError('ERP недоступна')
    state.same_day_loader = down
    state.same_day_cache[D] = (_time.monotonic() - 3600, good)                    # кэш устарел — читать снова
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    # до правки: ответ без same_day и без признака — страница не отличала «новых нет» от «не прочитаны»
    assert d['erp_ok'] is False and d['erp_last_ok'] == '2026-10-01T12:00:00' and 'same_day' not in d
    s = _ok(client.get('/api/routes/dispatch/status?date=2026-10-01'))
    assert s['erp_ok'] is False and s['erp_last_ok'] == '2026-10-01T12:00:00'


# ============================== 7. бумага — из отправленного плана ==============================

def test_waybill_of_unsent_plan_is_marked_draft(client, monkeypatch):
    from route_optimizer import waybill as wb
    state = _page(client, monkeypatch)
    state.waybill_loader = lambda isns: wb.Lines({}, frozenset(), {})
    d = _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2']}))
    car = next(t['car_code'] for t in d['plan']['trucks'] if t['trips'])

    def waybill(d):
        return _ok(client.get('/api/routes/dispatch/waybill', query_string={'date': '2026-10-01', 'truck': car, 'rev': d['rev']}))
    # первый черновик (до правки печатался как рабочий документ — водитель при этом плана ещё не видел)
    assert waybill(d)['draft'] is True
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'}))
    assert 'draft' not in waybill(d)                                        # отправлен — бумага рабочая
    trip = next(tr for t in d['plan']['trucks'] for tr in t['trips'] if tr['truck'] == car)
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'move', 'customer_id': trip['stops'][0]['customer_id'],
                                                        'from_trip': trip['id'], 'to_trip': None, 'truck': None}))
    assert car in d['unsent']['trucks'] and waybill(d)['draft'] is True     # правка не отправлена — снова черновик

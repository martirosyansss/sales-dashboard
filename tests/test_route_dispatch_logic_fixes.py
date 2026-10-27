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
    assert pub == {orders[0].isn: [TRUCKS[0], 900.0, 10000.0, 101], orders[1].isn: [TRUCKS[1], 200.0, 10000.0, 102]}
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
    # Правило 27.10: до 16:00 открывается сегодняшний день по Еревану (01.10), а не дата сервера UTC (30.09).
    assert _ok(client.get('/api/routes/dispatch'))['day'] == '2026-10-01'


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


# ============================== склад — те же заказы, что у терминала (ревью ветки, P1) ==============================

def test_order_added_after_send_not_loaded_by_warehouse_and_truck_changing(client, monkeypatch):
    """Заказ магазина плана пришёл в ERP после отправки: терминал его не видит (снимок заказов) — и склад не грузит и не
    печатает его, а машина этого магазина у склада «меняется», пока логист не отправит план снова."""
    from test_route_warehouse import _sent_day, _wb
    state, d, asked = _sent_day(client, monkeypatch)
    owner = {s['customer_id']: tr['truck'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}
    car = owner[101]
    late = _dorder(40, 101, 120.0)
    _erp_orders(state, lambda orders, since, until: [*orders, *(o for o in [late] if since <= o.order_date < until)])
    w = _ok(client.get('/api/routes/warehouse?date=2026-10-01'))
    trucks = {t['car_code']: t for t in w['trucks']}
    assert trucks[car].get('changing') is True and all('changing' not in t for c, t in trucks.items() if c != car)
    # склад грузит отправленное: в строках рейса и в Բեռնագիր других машин нового заказа нет
    asked.clear()
    for t in w['trucks']:
        for tr in t['trips']:
            _ok(client.get('/api/routes/warehouse/goods', query_string={'date': '2026-10-01', 'truck': t['car_code'],
                                                                       'trip': tr['id'], 'rev': w['rev']}))
    assert asked and all(late.isn not in isns for isns in asked)
    r = _wb(client, car, w['rev'])
    assert r.status_code == 409 and r.get_json()['changing'] is True
    # отправили — склад грузит и его
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'send'}))
    w = _ok(client.get('/api/routes/warehouse?date=2026-10-01'))
    assert all('changing' not in t for t in w['trucks'])
    asked.clear()
    assert _wb(client, car, w['rev']).status_code == 200 and any(late.isn in isns for isns in asked)


# ============================== ручное решение и автоматический выбор машин (ревью ветки, P2) ==============================

def test_lock_survives_automatic_fleet_choice_by_crew():
    """Магазин перенесён логистом в машину, чей водитель не вышел: подбор машин по водителям (build_crewed, №77) машину
    не выбрасывает молча вместе с решением логиста (до правки — пробная сборка без неё стирала locks). Машина остаётся, как
    машина с закреплённым рейсом (страница предупредит об экипаже); снять решение — только логисту."""
    ctx = _dp_ctx()
    stops, orders = _dp_stops(EAST + WEST)
    ids = {o.isn for o in orders}
    draft = dp.build(ctx, stops, None, TRUCKS, 'now')
    src = next(t for t in draft.trips if 104 in t.stops)
    other = next(c for c in TRUCKS if c != src.truck)
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'move', 'customer_id': 104, 'from_trip': src.id,
                                              'to_trip': None, 'truck': other}, ids)
    crew = dp.Crew({src.truck: 'Արամ', other: 'Կարեն'}, frozenset(), frozenset({'Կարեն'}))   # водитель other не вышел
    got = dp.build_crewed(ctx, stops, draft, TRUCKS, 'now2', crew)
    assert got.locks == {104: other} and _where(got, 104) == [other]
    # без решения логиста та же сборка машину без водителя не берёт — правило №77 не изменилось
    plain = dp.build_crewed(ctx, stops, dp.apply_edit(ctx, stops, dp.Draft.from_json(draft.to_json()),
                                                      {'action': 'unlock_all'}, ids), TRUCKS, 'now3', crew)
    assert {t.truck for t in plain.trips} == {src.truck} and plain.locks == {}


# ============================== один заказ ERP — одна машина (решение владельца 12.10 «Запрещать») ==============================

def _split_store(client, state, cid):
    """План с магазином cid в рейсах обеих машин (как делит тяжёлый магазин сборка): CAR1 — его рейс, CAR2 — тоже."""
    _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2']}))
    raw, rev = state.store.load_dispatch('2026-10-01')
    trips = raw['trips']
    for car in ('CAR1', 'CAR2'):
        mine = [t for t in trips if t['truck'] == car]
        if mine:
            if cid not in mine[0]['stops']:
                mine[0]['stops'].append(cid)
        else:
            trips.append({'id': raw['next_id'], 'truck': car, 'stops': [cid], 'pinned': False})
            raw['next_id'] += 1
    assert state.store.save_dispatch('2026-10-01', raw, 'qa', expected_rev=rev) is not None
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    on = {tr['truck'] for t in d['plan']['trucks'] for tr in t['trips'] if any(s['customer_id'] == cid for s in tr['stops'])}
    assert on == {'CAR1', 'CAR2'}
    return d


def test_store_on_two_trucks_with_two_orders_is_blocked_too(client, monkeypatch):
    """Решение владельца 12.10 «Запретить целиком»: магазин в рейсах двух машин не утверждается и не отправляется, сколько бы
    у него ни было заказов (раньше — только при одном заказе); после «Տանել ամբողջը» — у одной машины, план уходит; в
    отправленном плане магазина на двух машинах нет, терминал и Բեռնագիր — те же заказы."""
    from courier import routes_link as rl
    from route_optimizer import waybill as wb
    from test_route_dispatch_send import ORDERS
    state = _page(client, monkeypatch)
    second = _dorder(41, 101, 300.0)
    _erp_orders(state, lambda orders, since, until: [*orders, *(o for o in [second] if since <= o.order_date < until)])
    products = {10: wb.Product(10, '0101', 'Կաթ 1լ', 'հատ', 1.0, None)}
    state.waybill_loader = lambda isns: wb.Lines({i.upper(): ((10, 1.0),) for i in isns}, frozenset(), products)
    d = _split_store(client, state, 101)
    assert [x['customer_id'] for x in d['plan']['order_split']] == [101] and d['plan']['order_split'][0]['empty'] == []
    for action in ('approve',):
        r = _post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': action})
        assert r.status_code == 400 and [x['customer_id'] for x in r.get_json()['split_block']] == [101]
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'whole_store', 'customer_id': 101,
                                                        'truck': 'CAR2'}))
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'}))
    sent = dp.Draft.from_json(state.store.load_dispatch('2026-10-01')[0]).for_drivers()
    assert dp.multi_truck(sent.trips) == []                                   # отправленный план — магазин одной машине
    view = rl.routes_view(state, D)
    every = [*ORDERS, second]
    apk = {car: sorted(o.isn for o in rl.pick_orders(every, D, view, car)) for car in ('CAR1', 'CAR2')}
    assert {_isn(1), second.isn} <= set(apk['CAR2']) and not {_isn(1), second.isn} & set(apk['CAR1'])
    for car in ('CAR1', 'CAR2'):
        got = _ok(client.get('/api/routes/dispatch/waybill', query_string={'date': '2026-10-01', 'truck': car, 'rev': d['rev']}))
        assert sum(r['qty'] for tr in got['trips'] for r in tr['rows']) == len(apk[car])


def test_sent_plan_never_has_store_on_two_trucks(client, monkeypatch):
    """Решение владельца 12.10 «Запретить целиком»: ни утверждение, ни отправка, ни переутверждение не публикуют магазин в
    рейсах двух машин; несколько рейсов ОДНОЙ машины к магазину — можно."""
    state = _page(client, monkeypatch)
    d = _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2']}))
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'}))
    # после отправки магазин 101 оказался в рейсе и второй машины (правка рейса) — «Ուղարկել» закрыта
    raw, rev = state.store.load_dispatch('2026-10-01')
    car = next(t['truck'] for t in raw['trips'] if 101 in t['stops'])
    other = next(c for c in ('CAR1', 'CAR2') if c != car)
    raw['trips'].append({'id': raw['next_id'], 'truck': other, 'stops': [101], 'pinned': True})
    raw['next_id'] += 1
    assert state.store.save_dispatch('2026-10-01', raw, 'qa', expected_rev=rev) is not None
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    for action in ('send', 'approve'):
        if action == 'approve':
            d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'unapprove'}))
        r = _post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': action, 'confirm_crew': True,
                                                        'confirm_loaded': True})
        assert r.status_code == 400 and r.get_json()['split_block'], action
    sent = dp.Draft.from_json(state.store.load_dispatch('2026-10-01')[0]).for_drivers()
    assert dp.multi_truck(sent.trips) == []
    # два рейса ОДНОЙ машины к магазину — отправляется
    raw, rev = state.store.load_dispatch('2026-10-01')
    raw['trips'] = [{**t, 'truck': car} if t['stops'] == [101] and t['truck'] == other else t for t in raw['trips']]
    assert state.store.save_dispatch('2026-10-01', raw, 'qa', expected_rev=rev) is not None
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'}))
    assert dp.multi_truck(dp.Draft.from_json(state.store.load_dispatch('2026-10-01')[0]).for_drivers().trips) == []


def test_build_gives_heavy_store_trips_to_one_truck():
    """Решение владельца 12.10 «Запретить целиком», сборка: магазин 12 т (больше любой машины) — две поездки по 6 т; решатель
    дал их двум HOWO, _one_truck отдаёт обе одной машине, её день это вмещает (иначе — запрет утверждения и «Տանել
    ամբողջը»)."""
    import random
    from route_optimizer import fleet as fl
    from test_route_optimizer import FORD, HOWO
    rng = random.Random(7)
    spec = [(100 + i, (40.15 + rng.random() * 0.08, 44.45 + rng.random() * 0.15), rng.choice([100.0, 300.0, 600.0]))
            for i in range(25)] + [(200, (40.23, 44.55), 12000.0)]
    stops, _ = _dp_stops(spec)
    howo2 = fl.FleetTruck('123AV61', 'HOWO', 10000.0, 30.0)
    ctx = _dp_ctx((HOWO, howo2, FORD))
    codes = [HOWO.car_code, FORD.car_code, howo2.car_code]
    draft = dp.build(ctx, stops, None, codes, 'now')
    assert len([t for t in draft.trips if 200 in t.stops]) == 2 and dp.multi_truck(draft.trips) == []
    real = dp._one_truck
    try:
        dp._one_truck = lambda *a: None                          # без него решатель кладёт поездки на две машины
        assert [x['customer_id'] for x in dp.multi_truck(dp.build(ctx, stops, None, codes, 'now').trips)] == [200]
    finally:
        dp._one_truck = real


def test_single_order_split_blocks_send_until_whole_store(client, monkeypatch):
    """Магазин двух машин с одним заказом: утвердить и отправить нельзя (не подтверждение — запрет), магазин назван; после
    «Տանել ամբողջը» он у одной машины (ручное решение) и план отправляется."""
    state = _page(client, monkeypatch)
    d = _split_store(client, state, 101)                                         # у 101 один заказ (400 кг)
    split = d['plan']['order_split']
    assert [x['customer_id'] for x in split] == [101]
    r = _post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve', 'confirm_crew': True})
    body = r.get_json()
    assert r.status_code == 400 and [x['customer_id'] for x in body['split_block']] == [101]
    assert 'Клиент <101>' in body['error'] and 'sent' not in _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    keep = next(c for c in body['split_block'][0]['trucks'] if c not in split[0]['empty'])
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'whole_store', 'customer_id': 101,
                                                        'truck': keep}))
    on = {tr['truck'] for t in d['plan']['trucks'] for tr in t['trips'] if any(s['customer_id'] == 101 for s in tr['stops'])}
    assert on == {keep} and 'order_split' not in d['plan']
    assert any(s.get('locked') for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops'] if s['customer_id'] == 101)
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'}))
    assert d['sent'] and d['unsent'] is None


def test_capacity_of_split_store_trip_uses_assigned_orders():
    """Кг и перегруз рейса — по заказам, которые машина везёт на самом деле (до решения — поровну между машинами)."""
    ctx = _dp_ctx()
    ford, howo = '333DO33', '991AT61'                                            # FORD 3500 кг
    stops, orders = _dp_stops([(101, (40.200, 44.640), 3400.0), (101, (40.200, 44.640), 100.0),
                               (102, (40.205, 44.650), 200.0)])
    draft = dp.Draft([ford, howo], trips=[dp.DraftTrip(1, ford, [101, 102]), dp.DraftTrip(2, howo, [101])], next_id=3)
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    trips = {tr['truck']: tr for t in view['trucks'] for tr in t['trips']}
    assert trips[ford]['kg'] == 3600 and trips[ford]['over_capacity'] is True        # заказ 3400 целиком у FORD
    assert trips[howo]['kg'] == 100
    shop = {tr['truck']: s for t in view['trucks'] for tr in t['trips'] for s in tr['stops'] if s['customer_id'] == 101}
    assert (shop[ford]['kg'], shop[ford]['share'], shop[howo]['kg']) == (3400, 1, 100)
    assert dp.published_orders(draft, stops) == {orders[0].isn: [ford, 3400.0, 10000.0, 101],
                                                 orders[1].isn: [howo, 100.0, 10000.0, 101],
                                                 orders[2].isn: [ford, 200.0, 10000.0, 102]}


# ============================== итоговое ревью ветки 12.10 ==============================

def test_loaded_truck_keeps_its_orders_when_other_trips_change():
    """P1: машины A, B, C у одного магазина, заказы 600/500/400 кг; рейс C загружен. Перенос рейса B на машину A менял
    распределение — C получала заказ 500 вместо погруженного 400, а проверка груза сравнивала заказы магазина целиком и не
    видела разницы. Теперь заказы загруженной машины закреплены (assign), а проверка груза — по заказам машины."""
    from route_optimizer import fleet as fl
    from test_route_optimizer import FORD, HOWO
    howo2 = fl.FleetTruck('123AV61', 'HOWO', 10000.0, 30.0)
    ctx = _dp_ctx((HOWO, howo2, FORD))
    a, b, c = HOWO.car_code, howo2.car_code, FORD.car_code
    p = (40.200, 44.640)
    stops, orders = _dp_stops([(101, p, 600.0), (101, p, 500.0), (101, p, 400.0)])
    draft = dp.Draft([a, b, c], trips=[dp.DraftTrip(1, a, [101]), dp.DraftTrip(2, b, [101]), dp.DraftTrip(3, c, [101])],
                     next_id=4)
    dp.approve(draft, 'at', 'u')
    dp.send(draft, 'at', 'u', orders=dp.published_orders(draft, stops))
    assert {isn: v[0] for isn, v in draft.sent['orders'].items()} == {orders[0].isn: a, orders[1].isn: b, orders[2].isn: c}
    dp.mark_loaded(draft, 3, '2026-10-01T08:00:00', 'sklad')
    cargo = dp.loaded_cargo(draft, stops)
    legacy = dp.Draft.from_json(json.loads(json.dumps(draft.to_json())))
    del legacy.sent['orders']                                       # снимок без заказов — закреплять не по чему
    for d in (draft, legacy):
        dp.apply_edit(ctx, stops, d, {'action': 'pin', 'trip': 2, 'truck': a}, {o.isn for o in orders})
    # закреплено: у C — тот же погруженный заказ 400, груз не изменился, отправка не спрашивает
    assert dp.published_orders(draft, stops)[orders[2].isn][0] == c
    assert dp.loaded_cargo(draft, stops, cargo) == cargo and not dp.loaded_changed(draft, stops)
    # без закрепления распределение у C другое — и проверка груза это видит (правка — только с подтверждением)
    assert dp.published_orders(legacy, stops)[orders[2].isn][0] != c
    assert dp.loaded_cargo(legacy, stops, cargo) != cargo


def test_split_store_timeline_uses_assigned_weight():
    """P2: время рейса — по настоящему грузу: точка не раньше выезда, разгрузка каждой машины — по её заказам (до правки
    выезд 3400/100 кг считался по настоящему весу, а прибытие — по долям поровну: прибытие раньше выезда)."""
    ctx = _dp_ctx()
    ford, howo = '333DO33', '991AT61'
    stops, _ = _dp_stops([(101, (40.200, 44.640), 3400.0), (101, (40.200, 44.640), 100.0), (102, (40.205, 44.650), 200.0)])
    draft = dp.Draft([ford, howo], trips=[dp.DraftTrip(1, ford, [101, 102]), dp.DraftTrip(2, howo, [101])], next_id=3)
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    shop = {}
    for t in view['trucks']:
        for tr in t['trips']:
            assert all(s['arrive'] >= tr['depart'] for s in tr['stops']) and tr['return'] >= tr['depart']
            shop.update({tr['truck']: s for s in tr['stops'] if s['customer_id'] == 101})
    tn = ctx.tn
    assert shop[ford]['unload_min'] > shop[howo]['unload_min']
    assert shop[howo]['unload_min'] < tn.unload_min_per_stop + tn.unload_min_per_tonne * 1.0   # 100 кг, а не 1750


def test_unassigned_store_keeps_its_lock_visible():
    """P2: сборка не вместила магазин в машину ручного решения — он «ещё не в рейсах», но решение видно (машина) и снимается;
    число решений плана — все сохранённые, не только у точек рейсов."""
    ctx = _dp_ctx()
    stops, _ = _dp_stops(EAST)
    draft = dp.Draft(TRUCKS, trips=[dp.DraftTrip(1, TRUCKS[0], [101, 102])], next_id=2, locks={103: TRUCKS[1]})
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    (u,) = view['unassigned']
    assert (u['customer_id'], u['locked'], u['lock_truck']) == (103, True, TRUCKS[1]) and view['locks'] == 1
    assert dp.apply_edit(ctx, stops, draft, {'action': 'unlock', 'customer_id': 103}, set()).locks == {}


def test_driver_change_returns_publication_status_and_crew_sig(client, monkeypatch):
    """P2: смена водителя отправленного плана: ответ /dispatch/driver несёт статус отправки (unsent с экипажем) — страница
    применяет его; отпечаток экипажа crew_sig другой — другие вкладки перечитают день (/status)."""
    state = _page(client, monkeypatch)
    state.store.save_truck_driver('CAR1', '2026-10-01', 'Արամ', 'qa')
    state.store.save_truck_driver('CAR2', '2026-10-01', 'Կարեն', 'qa')
    d = _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2']}))
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'}))
    car = sorted({t['car_code'] for t in d['plan']['trucks'] if t['trips']})[0]
    sig = d['crew_sig']
    assert _ok(client.get('/api/routes/dispatch/status?date=2026-10-01'))['crew_sig'] == sig
    r = _ok(_post(client, '/api/routes/dispatch/driver', {'car_code': car, 'name': 'Նոր Վարորդ', 'only_day': True}))
    assert r['publication']['unsent']['crew'] == [car] and r['crew_sig'] != sig
    s = _ok(client.get('/api/routes/dispatch/status?date=2026-10-01'))
    assert s['crew_sig'] == r['crew_sig'] and s['rev'] == d['rev']                 # план не тронут, экипаж — другой
    assert _ok(client.get('/api/routes/dispatch?date=2026-10-01'))['unsent']['crew'] == [car]


# ============================== ревью ветки, раунд 3 ==============================

def test_orders_of_one_invoice_go_to_one_truck():
    """P1: два заказа магазина двух машин сделаны одной накладной ERP — одна единица распределения (иначе накладная целиком
    печаталась бы в Բեռնագիր обеих машин)."""
    o1 = replace(_dorder(1, 101, 600.0), invoice='S1')
    o2 = replace(_dorder(2, 101, 500.0), invoice='S1')
    o3 = _dorder(3, 101, 400.0)
    trucks, warn = dp.order_trucks([('A', [101]), ('B', [101])], [o1, o2, o3])
    assert trucks[o1.isn] == trucks[o2.isn] != trucks[o3.isn] and warn == []
    # без накладной — по одному (как раньше)
    plain, _ = dp.order_trucks([('A', [101]), ('B', [101])], [_dorder(1, 101, 600.0), _dorder(2, 101, 500.0)])
    assert len(set(plain.values())) == 2


def test_shared_invoice_printed_by_exactly_one_truck(client, monkeypatch):
    """P1 (защита Բեռնագիր): накладная двух заказов разных машин, о которой план не знал, — её товар на листе ровно одной
    машины (строки читаются для заказов всего плана, накладная — у наименьшего из них), а не целиком у обеих."""
    from route_optimizer import waybill as wb
    state = _page(client, monkeypatch)
    second = _dorder(41, 101, 300.0)
    _erp_orders(state, lambda orders, since, until: [*orders, *(o for o in [second] if since <= o.order_date < until)])
    shared = {_isn(1), second.isn}
    products = {10: wb.Product(10, '0101', 'Կաթ 1լ', 'հատ', 1.0, None)}

    def loader(isns):                      # как waybill.load_lines: накладная — у наименьшего запрошенного её заказа
        asked = {i.upper() for i in isns}
        owner = min(shared & asked) if shared & asked else None
        return wb.Lines({i: (((10, 10.0),) if i == owner else ()) if i in shared else ((10, 1.0),) for i in asked},
                        frozenset(shared & asked), products)
    state.waybill_loader = loader
    d = _split_store(client, state, 101)
    on = {tr['truck']: [o['isn'] for s in tr['stops'] if s['customer_id'] == 101 for o in s['orders']]
          for t in d['plan']['trucks'] for tr in t['trips']}
    assert sorted(i for v in on.values() for i in v) == sorted(shared)     # заказы — у разных машин
    shop = 0
    for car in ('CAR1', 'CAR2'):
        got = _ok(client.get('/api/routes/dispatch/waybill', query_string={'date': '2026-10-01', 'truck': car, 'rev': d['rev']}))
        shop += sum(r['qty'] for tr in got['trips'] for r in tr['rows']) - sum(
            1 for t in d['plan']['trucks'] if t['car_code'] == car for tr in t['trips'] for s in tr['stops']
            for o in s['orders'] if o['isn'] not in shared)
    assert shop == 10                                                     # накладная 10 штук — один раз на две машины


def test_loaded_baseline_is_sent_snapshot_not_current_erp():
    """P1: после отправки в ERP пришёл заказ магазина загруженного рейса — у машины груз больше отправленного: «Ուղարկել»
    спрашивает подтверждение (до правки обе стороны сравнения брали текущие заказы ERP — новый заказ был «и раньше»)."""
    stops, orders = _dp_stops([(101, (40.200, 44.640), 300.0), (102, (40.205, 44.650), 200.0)])
    draft = dp.Draft(TRUCKS, trips=[dp.DraftTrip(1, TRUCKS[0], [101]), dp.DraftTrip(2, TRUCKS[1], [102])], next_id=3)
    dp.approve(draft, 'at', 'u')
    dp.send(draft, 'at', 'u', orders=dp.published_orders(draft, stops))
    dp.mark_loaded(draft, 1, '2026-10-01T08:00:00', 'sklad')
    assert not dp.loaded_changed(draft, stops)
    from test_route_optimizer import _coord_of
    coord = _coord_of({101: (40.200, 44.640), 102: (40.205, 44.650)})
    more = dp.build_stops([*orders, _dorder(9, 101, 120.0)], coord)
    assert dp.loaded_changed(draft, more)
    # заказ незагруженной машины пришёл — груз загруженной не меняется
    other = dp.build_stops([*orders, _dorder(10, 102, 50.0)], coord)
    assert not dp.loaded_changed(draft, other)


def test_operational_timeline_uses_assigned_weight():
    """P2: «уже грузится» и «опаздывает» считаются тем же временем, что план на странице и у водителей (trip_kgs): 3400/100 кг
    магазина двух машин — второй рейс лёгкой машины начинается, как в плане, а не по долям поровну."""
    ctx = _dp_ctx()
    ford, howo = '333DO33', '991AT61'
    stops, _ = _dp_stops([(101, (40.200, 44.640), 3400.0), (101, (40.200, 44.640), 100.0), (102, (40.205, 44.650), 200.0),
                          (103, (40.210, 44.645), 200.0)])
    draft = dp.Draft([ford, howo], trips=[dp.DraftTrip(1, ford, [101, 102]), dp.DraftTrip(2, howo, [101]),
                                          dp.DraftTrip(3, howo, [103])], next_id=4)
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    shown = {tr['id']: tr['loading_start'] for t in view['trucks'] for tr in t['trips']}
    *_, tl0, _ = dp._same_day_base(ctx, stops, draft, 0.0)
    assert {tid: dp._hhmm(ctx.work_start_min + dep) for tid, (dep, _, _) in tl0.items()} == shown
    start3 = tl0[3][0]
    assert 3 in dp.started_trips(ctx, stops, draft, start3 + 1) and 3 not in dp.started_trips(ctx, stops, draft, start3 - 1)


def test_rebuilt_driver_seat_is_unsent_and_paper_is_draft(client, monkeypatch):
    """P2: пересборка пересадила водителя (Draft.seats) при тех же рейсах — экипаж черновика другой, чем отправленный:
    «не отправлено» по экипажу, Բեռնագիր — черновик (до правки сравнивался экипаж отправленного плана с ним же)."""
    from route_optimizer import waybill as wb
    state = _page(client, monkeypatch)
    state.waybill_loader = lambda isns: wb.Lines({}, frozenset(), {})
    d = _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2']}))
    d = _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'approve'}))
    car = sorted({t['car_code'] for t in d['plan']['trucks'] if t['trips']})[0]
    raw, rev = state.store.load_dispatch('2026-10-01')
    raw['seats'] = {car: 'Կարեն'}                                         # сборка посадила водителя (рейсы те же)
    assert state.store.save_dispatch('2026-10-01', raw, 'qa', expected_rev=rev) is not None
    d = _ok(client.get('/api/routes/dispatch?date=2026-10-01'))
    assert d['unsent']['crew'] == [car]
    wbill = _ok(client.get('/api/routes/dispatch/waybill', query_string={'date': '2026-10-01', 'truck': car, 'rev': d['rev']}))
    assert wbill['draft'] is True and wbill['driver'] == 'Կարեն'


def test_courier_shared_invoice_without_car_belongs_to_one_truck():
    """Класс P1 (терминал): накладная без машины из заказов двух машин — у одной (машина её наименьшего отправленного заказа),
    а не у обеих; накладная заказа машины у клиента нескольких машин — её (раньше — ничья: заказ пропадал у обеих)."""
    from courier import routes_link as rl
    a, b = _isn(1), _isn(2)
    view = rl.RoutesView(plan_exists=True, released=True, trips=(('A', (101,)), ('B', (101,))), published={a: 'A', b: 'B'})
    assert rl.invoice_owner(view, 'A')(101, [a, b]) and not rl.invoice_owner(view, 'B')(101, [a, b])
    assert rl.invoice_owner(view, 'B')(101, [b]) and not rl.invoice_owner(view, 'A')(101, [b])
    assert rl.invoice_owner(view, 'A')(101) and rl.invoice_owner(view, 'B')(101)     # до чтения накладных — заказы машины
    legacy = rl.RoutesView(plan_exists=True, released=True, trips=view.trips)
    assert not rl.invoice_owner(legacy, 'A')(101, [a]) and not rl.invoice_owner(legacy, 'B')(101)   # как раньше — ничья


# ============================== ревью ветки, раунд 4 ==============================

def test_order_taken_today_not_on_tomorrows_published_terminal(client, monkeypatch):
    """P1: план 02.10 отправлен с заказом 103; 01.10 его взяли в развоз дня приёма (№72) и везут сегодня — терминал 02.10 его
    уже не показывает (до правки показывал по снимку, пока нет накладной), логист 02.10 видит «не отправлено»."""
    from courier import routes_link as rl
    from test_route_dispatch_same_day import NEXT
    state = _page(client, monkeypatch)                                  # сейчас 01.10 08:00
    n = _ok(client.post('/api/routes/dispatch/build', json={'date': NEXT, 'trucks': ['CAR1', 'CAR2']}))
    n = _ok(client.post('/api/routes/dispatch/edit', json={'date': NEXT, 'rev': n['rev'], 'action': 'approve'}))
    owner = {s['customer_id']: tr['truck'] for t in n['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}
    assert 103 in owner and n['unsent'] is None
    every = state.dispatch_loader(date(2026, 9, 1), date(2026, 10, 3), D).orders
    nxt = date(2026, 10, 2)

    def terminal():
        view = rl.routes_view(state, nxt)
        return sorted(o.isn for o in rl.pick_orders(every, nxt, view, owner[103]))
    assert _isn(10) in terminal()
    d = _ok(_post(client, '/api/routes/dispatch/build', {'trucks': ['CAR1', 'CAR2']}))
    key = _ok(client.post('/api/routes/dispatch/same-day', json={'date': '2026-10-01', 'orders': [_isn(10)]}))['options'][0]['key']
    _ok(_post(client, '/api/routes/dispatch/edit', {'rev': d['rev'], 'action': 'same_day', 'orders': [_isn(10)], 'option': key}))
    assert _isn(10) not in terminal()                                   # взят сегодня — завтра его нет
    n = _ok(client.get('/api/routes/dispatch?date=' + NEXT))
    assert owner[103] in n['unsent']['trucks'] and n['unsent']['orders_changed'] >= 1


def test_reapproval_keeps_loaded_truck_orders():
    """P2: 600/500/400 у A/B/C, рейс C загружен; снятие утверждения, рейс B → машина A, утверждение снова: у C тот же
    погруженный заказ 400 (до правки approve() отправлял снимок без заказов — закрепление терялось, C получала 500)."""
    from route_optimizer import fleet as fl
    from test_route_optimizer import FORD, HOWO
    howo2 = fl.FleetTruck('123AV61', 'HOWO', 10000.0, 30.0)
    ctx = _dp_ctx((HOWO, howo2, FORD))
    a, b, c = HOWO.car_code, howo2.car_code, FORD.car_code
    stops, orders = _dp_stops([(101, (40.200, 44.640), 600.0), (101, (40.200, 44.640), 500.0), (101, (40.200, 44.640), 400.0)])
    draft = dp.Draft([a, b, c], trips=[dp.DraftTrip(1, a, [101]), dp.DraftTrip(2, b, [101]), dp.DraftTrip(3, c, [101])],
                     next_id=4)
    dp.approve(draft, 'at', 'u')
    dp.send(draft, 'at', 'u', orders=dp.published_orders(draft, stops))      # как views после approve
    dp.mark_loaded(draft, 3, '2026-10-01T08:00:00', 'sklad')
    dp.unapprove(draft)
    dp.apply_edit(ctx, stops, draft, {'action': 'pin', 'trip': 2, 'truck': a}, {o.isn for o in orders})
    dp.approve(draft, 'at2', 'u')
    assert draft.sent['orders'] is not None                                  # прежний снимок — до итогового
    assert not dp.loaded_changed(draft, stops)
    dp.send(draft, 'at2', 'u', orders=dp.published_orders(draft, stops))
    assert draft.sent['orders'][orders[2].isn][0] == c                       # у C — тот же погруженный 400


def test_overtime_and_resize_and_add_respect_manual_locks():
    """P2: «Везти после конца дня», «Տանել այսօր» и растягивание рейса на шкале не отдают магазин ручного решения чужой машине."""
    ctx = _dp_ctx()
    stops, orders = _dp_stops(EAST + WEST)
    ford, howo = '333DO33', '991AT61'
    # «Везти после конца дня»: 104 не поместился (no_room) — встаёт к машине своего ручного решения (до правки — к HOWO,
    # даже закреплённый за FORD)
    near = [s for s in stops if s.customer_id not in (105, 106)]
    for lock in (howo, ford):
        draft = dp.Draft(TRUCKS, trips=[dp.DraftTrip(1, ford, [101, 102, 103])], next_id=2, no_room={104}, locks={104: lock})
        assert _where(dp.overtime(ctx, near, draft), 104) == [lock]
    # «Տանել այսօր»: магазин ручного решения — только в рейс своей машины
    d2 = dp.Draft(TRUCKS, trips=[dp.DraftTrip(1, ford, [101, 102]), dp.DraftTrip(2, howo, [104])], next_id=3,
                  locks={103: howo})
    placed = dp.place_added(ctx, stops, d2, [103])
    assert placed and dp.trip_of(d2, placed[103]).truck == howo
    # растягивание рейса FORD на шкале не забирает магазин HOWO с ручным решением
    d3 = dp.Draft(TRUCKS, trips=[dp.DraftTrip(1, ford, [101]), dp.DraftTrip(2, howo, [104, 105, 106])], next_id=3,
                  locks={104: howo, 105: howo, 106: howo})
    grown = dp.apply_edit(ctx, stops, d3, {'action': 'resize', 'trip': 1, 'return': 18 * 60}, {o.isn for o in orders})
    assert all(_where(grown, c) == [howo] for c in (104, 105, 106))


# ============================== ревью ветки, раунд 5 ==============================

def test_warehouse_plan_keeps_published_order_mapping():
    """P1: 600/500/400 отправлены A/B/C; после отправки заказ A в ERP стал 300 кг. Склад (план водителей, published) держит
    отправленное распределение как есть — у C тот же 400, что у её терминала (до правки склад перераспределял по нынешним
    весам: C получала 300 и печатала его Բեռնագիր)."""
    from route_optimizer import fleet as fl
    from test_route_optimizer import FORD, HOWO, _coord_of
    howo2 = fl.FleetTruck('123AV61', 'HOWO', 10000.0, 30.0)
    ctx = _dp_ctx((HOWO, howo2, FORD))
    a, b, c = HOWO.car_code, howo2.car_code, FORD.car_code
    stops, orders = _dp_stops([(101, (40.200, 44.640), 600.0), (101, (40.200, 44.640), 500.0), (101, (40.200, 44.640), 400.0)])
    draft = dp.Draft([a, b, c], trips=[dp.DraftTrip(1, a, [101]), dp.DraftTrip(2, b, [101]), dp.DraftTrip(3, c, [101])],
                     next_id=4)
    dp.approve(draft, 'at', 'u')
    dp.send(draft, 'at', 'u', orders=dp.published_orders(draft, stops))
    dp.mark_loaded(draft, 3, '2026-10-01T08:00:00', 'sklad')
    sent = {i: v[0] for i, v in draft.sent['orders'].items()}
    lighter = dp.build_stops([replace(orders[0], kg=300.0), *orders[1:]], _coord_of({101: (40.200, 44.640)}))
    info = lambda st: {'customer_id': st.customer_id, 'orders': [{'isn': o.isn} for o in st.orders]}   # noqa: E731
    view = dp.plan_view(ctx, lighter, draft.for_drivers(), info, explain=False, published=True)
    got = {tr['truck']: [o['isn'] for s in tr['stops'] for o in s['orders']] for t in view['trucks'] for tr in t['trips']}
    assert {t: isns for t, isns in got.items()} == {t: [i for i, x in sent.items() if x == t] for t in (a, b, c)}
    # без published (черновик логиста) распределение по нынешним весам — другое: отличие видно логисту и складу
    plain = dp.plan_view(ctx, lighter, draft.for_drivers(), info, explain=False)
    assert {tr['truck']: [o['isn'] for s in tr['stops'] for o in s['orders']] for t in plain['trucks'] for tr in t['trips']} != got
    moved, _ = dp.orders_unsent(draft.sent['orders'], dp.published_orders(draft.for_drivers(), lighter))
    assert a in moved


def test_deadline_check_uses_assigned_weight():
    """P2: «Մինչև ժամը» (fit_until) считает прибытие тем же весом, что план: 3400/100 кг магазина двух машин — точка после
    него у FORD приезжает позже срока, и fit_until это видит (до правки — по долям поровну: «успевает», а план — опоздание)."""
    ctx = _dp_ctx()
    ford, howo = '333DO33', '991AT61'
    stops, _ = _dp_stops([(101, (40.200, 44.640), 3400.0), (101, (40.200, 44.640), 100.0), (102, (40.205, 44.650), 200.0)])
    draft = dp.Draft([ford, howo], trips=[dp.DraftTrip(1, ford, [101, 102], pinned=True), dp.DraftTrip(2, howo, [101])],
                     next_id=3)
    routable = {s.customer_id: s for s in stops}
    real = dp._timeline(ctx, draft.trips, routable, dp._shares(draft.trips), kg_of=dp.trip_kgs(draft, routable)[0])[1][2][1]
    equal = dp._timeline(ctx, draft.trips, routable, dp._shares(draft.trips), kg_of={})[1][2][1]
    assert real > equal + 5                                              # 3400 кг разгружают дольше 1750
    hi = ctx.work_start_min + (real + equal) / 2                          # срок 102 — между «поровну» и настоящим
    ctx = replace(ctx, windows={102: (0.0, hi)})
    got = dp.fit_until(ctx, stops, dp.Draft.from_json(draft.to_json()), 102)
    assert got['late'] is True and got['kept'] == [1]
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    assert next(s for t in view['trucks'] for tr in t['trips'] for s in tr['stops'] if s['customer_id'] == 102)['window_miss']


# ============================== ревью ветки, раунд 6 ==============================

def test_pin_trip_to_other_truck_moves_store_locks_with_it():
    """P2: магазин перенесён в A (решение), потом весь рейс закреплён за B («pin») — решение магазина теперь B: после снятия
    закрепления пересборка держит его у B (до правки решение указывало на A — магазин молча возвращался туда)."""
    ctx = _dp_ctx()
    stops, orders = _dp_stops(EAST + WEST)
    ids = {o.isn for o in orders}
    draft = dp.build(ctx, stops, None, TRUCKS, 'now')
    src = next(t for t in draft.trips if 104 in t.stops)
    a = src.truck
    b = next(c for c in TRUCKS if c != a)
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'move', 'customer_id': 104, 'from_trip': src.id,
                                              'to_trip': None, 'truck': a}, ids)
    trip = next(t for t in draft.trips if 104 in t.stops)
    assert draft.locks == {104: a}
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'pin', 'trip': trip.id, 'truck': b}, ids)
    assert draft.locks == {104: b}
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'unpin', 'trip': trip.id}, ids)
    assert _where(dp.build(ctx, stops, draft, TRUCKS, 'now2'), 104) == [b]


def test_whole_store_refuses_truck_prohibited_for_store():
    """P2: «Տանել ամբողջը» машине, которой магазин запрещён (допуск машин), — отказ до правки (как move и pin); разрешённой —
    можно."""
    from route_optimizer.vehicle_access import VehicleAccess
    ford, howo = '333DO33', '991AT61'
    stops, orders = _dp_stops(EAST)
    ctx = replace(_dp_ctx(), vehicle_access={101: VehicleAccess('allow', (howo,))})
    draft = dp.Draft(TRUCKS, trips=[dp.DraftTrip(1, ford, [101, 102]), dp.DraftTrip(2, howo, [101, 103])], next_id=3)
    before = draft.to_json()
    try:
        dp.apply_edit(ctx, stops, draft, {'action': 'whole_store', 'customer_id': 101, 'truck': ford}, set())
        raise AssertionError('ожидался отказ')
    except dp.DispatchError:
        pass
    assert draft.to_json() == before                                       # ничего не изменилось
    got = dp.apply_edit(ctx, stops, draft, {'action': 'whole_store', 'customer_id': 101, 'truck': howo}, set())
    assert _where(got, 101) == [howo] and got.locks == {101: howo}

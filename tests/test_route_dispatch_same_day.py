# -*- coding: utf-8 -*-
"""«Развоз»: новые заказы дня (ответ владельца №72). Заказ с датой D — развоз D+1, но логист может взять его в развоз D:
страница D (только сегодня по Еревану) показывает новые заказы дня и предлагает, как их везти — вставкой в рейс, который
по плану ещё не грузится, новым рейсом машины после возвращения или машиной, которая сегодня не выезжала; рейс, чья
загрузка началась, не меняется (машина в рейсе новый заказ не берёт — ответ «Բ»). Взятые в D заказы D+1 не везёт, а
приложение водителя показывает их машине D. Без новых заказов план и черновик — прежние до байта. Синтетические данные,
без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_same_day.py -q
"""
import json
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import routes_link as rl  # noqa: E402
from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_optimizer import (EAST, FORD, HOWO, TN, _coord_of, _dispatch_setup, _dorder, _dp_ctx,  # noqa: E402,F401
                                  _isn, client)

D = date(2026, 10, 1)            # четверг — «сегодня»; заказы среды 30.09 — его развоз, заказы 01.10 — развоз пятницы
DAY, NEXT = '2026-10-01', '2026-10-02'
POINTS = {**{cid: p for cid, p, _ in EAST}, 201: (40.215, 44.660), 202: (40.220, 44.665), 203: None}


def _base(trucks=(FORD,)):
    """Рейс FORD 101 → 102 → 103 (заказы 30.09) и новые заказы дня: 201 (300 кг), 202 (150 кг), 203 — без точки."""
    orders = [_dorder(i + 1, cid, kg) for i, (cid, _, kg) in enumerate(EAST)]
    base = dp.build_stops(orders, _coord_of(POINTS))
    ctx = _dp_ctx()
    draft = dp.build(ctx, base, None, [t.car_code for t in trucks], 'now')
    new = [_dorder(50, 201, 300.0, day=D), _dorder(51, 202, 150.0, day=D), _dorder(52, 203, 50.0, day=D)]
    return ctx, base, orders, new, draft


def _with(orders, new, isns):
    return dp.build_stops([*orders, *(o for o in new if o.isn in isns)], _coord_of(POINTS))


# ============================== отбор новых заказов ==============================

def test_candidates_are_orders_of_the_day_not_shipped_before_not_self_delivery():
    orders = [_dorder(1, 101, 10.0, day=D), _dorder(2, 102, 10.0, day=date(2026, 9, 30)),
              _dorder(3, 103, 10.0, day=date(2026, 10, 2)), _dorder(4, 104, 10.0, day=D, shipped=date(2026, 9, 30)),
              _dorder(5, 105, 10.0, day=D, shipped=D), _dorder(6, 106, 10.0, day=D, agent=3, van=3),
              _dorder(7, 107, 10.0, day=D, agent=3, van=4)]
    assert [o.customer_id for o in dp.same_day_candidates(orders, D)] == [101, 105, 107]


def test_draft_same_day_roundtrip_and_old_draft_byte_identical():
    d = dp.Draft(['CAR1'], trips=[dp.DraftTrip(1, 'CAR1', [5]), dp.DraftTrip(2, 'CAR1', [6], not_before=13 * 60.0)],
                 next_id=3, same_day={_isn(9)})
    raw = json.loads(json.dumps(d.to_json()))
    assert raw['same_day'] == [_isn(9)] and raw['trips'][1]['not_before'] == 780.0 and 'not_before' not in raw['trips'][0]
    assert dp.Draft.from_json(raw) == d
    # без новых заказов дня — ни полей, ни их следов: черновик прежний до байта
    plain = dp.Draft(['CAR1'], trips=[dp.DraftTrip(1, 'CAR1', [5])], next_id=2)
    assert set(plain.to_json()) == set(dp.Draft().to_json()) and 'same_day' not in plain.to_json()
    assert set(plain.to_json()['trips'][0]) == {'id', 'truck', 'stops', 'pinned'}
    assert dp.Draft.from_json({'trucks': []}).same_day == set()
    for junk in ('x', -5, 3 * 24 * 60, True, 'nan'):
        assert dp.Draft.from_json({'trips': [{'id': 1, 'truck': 'A', 'stops': [1], 'not_before': junk}]}).trips[0].not_before is None
    assert dp.Draft.from_json({'same_day': ['bad', _isn(1), 7]}).same_day == {_isn(1)}


def test_not_before_delays_trip_loading():
    ctx, base, _, _, draft = _base()
    routable = {s.customer_id: s for s in base}
    t0 = dp._timeline(ctx, draft.trips, routable, dp._shares(draft.trips))
    late = [replace(t, not_before=ctx.work_start_min + 120.0) for t in draft.trips]
    t1 = dp._timeline(ctx, late, routable, dp._shares(late))
    assert t0[1][0] == 0.0 and t1[1][0] == 120.0 and t1[1][1] == pytest.approx(t0[1][1])


def test_rebuild_and_moves_keep_same_day_and_not_before():
    ctx, base, _, _, draft = _base()
    draft.same_day = {_isn(50)}
    draft.trips[0].not_before = 600.0
    draft.trips[0].pinned = True
    again = dp.build(ctx, base, draft, [FORD.car_code], 'now')
    assert again.same_day == {_isn(50)} and again.trips[0].not_before == 600.0
    moved = dp._moved(ctx, draft.trips, {s.customer_id: s for s in base}, 101, draft.trips[0].id, draft.trips[0])
    assert moved[0].not_before == 600.0


# ============================== варианты ==============================

def test_options_ranked_cheapest_first_with_insert_new_trip_and_extra_truck():
    ctx, base, orders, new, draft = _base()
    stops = _with(orders, new, {_isn(50), _isn(51)})
    res = dp.same_day_options(ctx, base, stops, draft, {201, 202}, -30.0)      # до начала дня: рейс FORD ещё не грузится
    opts = res['options']
    assert res['blocked'] == [] and [o['key'] for o in opts] == ['insert:1', f'trip:{FORD.car_code}', f'extra:{HOWO.car_code}']
    assert [o['amd'] for o in opts] == sorted(o['amd'] for o in opts)
    ins, trip, extra = opts
    assert (ins['kind'], ins['trip'], trip['kind'], trip['trip'], extra['kind']) == ('insert', 1, 'trip', None, 'extra')
    assert {s['customer_id'] for o in opts for s in o['stops']} == {201, 202}
    assert all(o['km'] > 0 and o['minutes'] > 0 and o['amd'] > 0 for o in opts)
    # новый рейс FORD — после возвращения из первого; доп. машина — с начала дня
    assert trip['loading_start'] == dp._hhmm(9 * 60 + dp._timeline(ctx, draft.trips, {s.customer_id: s for s in base},
                                                                    dp._shares(draft.trips))[1][1])
    assert extra['loading_start'] == '09:00'


def test_together_cheaper_than_one_by_one():
    ctx, base, orders, new, draft = _base()
    now = 30.0          # рейс FORD уже грузится: только новые рейсы
    both = dp.same_day_options(ctx, base, _with(orders, new, {_isn(50), _isn(51)}), draft, {201, 202}, now)['options'][0]
    one = [dp.same_day_options(ctx, base, _with(orders, new, {isn}), draft, {cid}, now)['options'][0]
           for isn, cid in ((_isn(50), 201), (_isn(51), 202))]
    assert both['key'] == f'trip:{FORD.car_code}' and both['amd'] < sum(o['amd'] for o in one)


def test_started_trip_is_frozen_and_new_trip_waits_for_now():
    ctx, base, orders, new, draft = _base()
    stops = _with(orders, new, {_isn(50)})
    res = dp.same_day_options(ctx, base, stops, draft, {201}, 0.0)              # 09:00 — рейс FORD начал грузиться
    assert all(o['kind'] != 'insert' for o in res['options'])
    res = dp.same_day_options(ctx, base, stops, draft, {201}, 200.0)            # 12:20 — FORD давно вернулся
    trip = next(o for o in res['options'] if o['kind'] == 'trip')
    assert trip['loading_start'] == '12:20'                                     # не раньше «сейчас»


def test_customer_in_started_trip_blocked_not_started_goes_to_same_stop():
    ctx, base, orders, _, draft = _base()
    extra = [_dorder(60, 102, 500.0, day=D)]                                     # магазин 102 уже в рейсе FORD
    stops = dp.build_stops([*orders, *extra], _coord_of(POINTS))
    res = dp.same_day_options(ctx, base, stops, draft, {102}, 10.0)
    assert res == {'options': [], 'blocked': [{'customer_id': 102, 'reason': 'started'}]}
    res = dp.same_day_options(ctx, base, stops, draft, {102}, -30.0)
    assert [o['key'] for o in res['options']] == ['same_stop'] and res['options'][0]['km'] == 0.0
    assert (res['options'][0]['minutes'], res['options'][0]['amd']) == (3, 0)   # +0,5 т разгрузки; км те же
    out = dp.take_same_day(ctx, base, stops, dp.Draft.from_json(draft.to_json()), {102}, {_isn(60)}, 'same_stop', -30.0)
    assert [t.stops for t in out.trips] == [t.stops for t in draft.trips] and out.same_day == {_isn(60)}


def test_no_coords_blocked():
    ctx, base, orders, new, draft = _base()
    res = dp.same_day_options(ctx, base, _with(orders, new, {_isn(52)}), draft, {203}, -30.0)
    assert res == {'options': [], 'blocked': [{'customer_id': 203, 'reason': 'no_coords'}]}


def test_capacity_and_work_end_respected():
    ctx, base, orders, _, draft = _base()
    heavy = [_dorder(70, 201, 3600.0, day=D)]                                   # тяжелее FORD (3,5 т)
    stops = dp.build_stops([*orders, *heavy], _coord_of(POINTS))
    keys = [o['key'] for o in dp.same_day_options(ctx, base, stops, draft, {201}, -30.0)['options']]
    assert keys == [f'extra:{HOWO.car_code}']
    light = dp.build_stops([*orders, _dorder(71, 201, 100.0, day=D)], _coord_of(POINTS))
    late = 540.0 - 15.0                                                          # 17:45: рейс до 18:00 не успеть
    assert dp.same_day_options(ctx, base, light, draft, {201}, late)['options'] == []
    routable = {s.customer_id: s for s in base}
    end = dp._truck_day(ctx, draft.trips, routable, dp._shares(draft.trips), FORD.car_code)[1]
    short = replace(ctx, tn=replace(TN, work_minutes=end + 5.0))                 # рейс FORD — впритык к концу дня
    keys = [o['key'] for o in dp.same_day_options(short, base, light, draft, {201}, -30.0)['options']]
    assert 'insert:1' not in keys and f'trip:{FORD.car_code}' not in keys and f'extra:{HOWO.car_code}' in keys


def test_window_misses_not_increased():
    ctx, base, orders, _, draft = _base()
    # окно 103 — до 09:25: вставка 201 перед ним опоздала бы, после — нет; вставка не нарушает окно ни так, ни так
    routable = {s.customer_id: s for s in base}
    at = dp._timeline(ctx, draft.trips, routable, dp._shares(draft.trips))[1][2]
    last = draft.trips[0].stops[-1]
    win = replace(ctx, windows={last: (0.0, 9 * 60 + at[-1] + 1.0)})
    stops = dp.build_stops([*orders, _dorder(72, 201, 100.0, day=D)], _coord_of(POINTS))
    for o in dp.same_day_options(win, base, stops, draft, {201}, -30.0)['options']:
        trial = dp.take_same_day(win, base, stops, dp.Draft.from_json(draft.to_json()), {201}, {_isn(72)}, o['key'], -30.0)
        assert dp._truck_day(win, trial.trips, {s.customer_id: s for s in stops}, dp._shares(trial.trips),
                             trial.trips[0].truck)[2] == 0


def test_take_applies_option_and_rejects_stale_or_unknown():
    ctx, base, orders, new, draft = _base()
    stops = _with(orders, new, {_isn(50), _isn(51)})
    out = dp.take_same_day(ctx, base, stops, dp.Draft.from_json(draft.to_json()), {201, 202}, {_isn(50), _isn(51)},
                           f'extra:{HOWO.car_code}', -30.0)
    assert out.trucks == sorted([FORD.car_code, HOWO.car_code]) and out.same_day == {_isn(50), _isn(51)}
    assert out.trips[0].stops == draft.trips[0].stops                              # рейсы плана не переставлены
    assert out.trips[-1].truck == HOWO.car_code and set(out.trips[-1].stops) == {201, 202}
    assert out.trips[-1].not_before == 9 * 60 - 30.0 and out.next_id == out.trips[-1].id + 1
    ins = dp.take_same_day(ctx, base, stops, dp.Draft.from_json(draft.to_json()), {201, 202}, {_isn(50), _isn(51)},
                           'insert:1', -30.0)
    old = draft.trips[0].stops
    assert [c for c in ins.trips[0].stops if c in old] == old and len(ins.trips) == 1   # вставка, без перестановки
    with pytest.raises(dp.DispatchError, match='այլևս հնարավոր չէ'):
        dp.take_same_day(ctx, base, stops, dp.Draft.from_json(draft.to_json()), {201, 202}, {_isn(50)}, 'insert:1', 10.0)
    with pytest.raises(dp.DispatchError, match='այլևս հնարավոր չէ'):
        dp.take_same_day(ctx, base, stops, dp.Draft.from_json(draft.to_json()), {201}, {_isn(50)}, 'nope', -30.0)
    with pytest.raises(dp.DispatchError, match='քարտեզում չկա'):
        dp.take_same_day(ctx, base, _with(orders, new, {_isn(52)}), dp.Draft.from_json(draft.to_json()), {203},
                         {_isn(52)}, 'insert:1', -30.0)


def test_drop_same_day_and_defer_trip_release_orders():
    d = dp.Draft(same_day={_isn(1), _isn(2)})
    assert dp.drop_same_day(d, {_isn(1)}).same_day == {_isn(2)}
    with pytest.raises(dp.DispatchError):
        dp.drop_same_day(d, {_isn(9)})
    ctx, base, orders, new, draft = _base()
    stops = _with(orders, new, {_isn(50)})
    out = dp.take_same_day(ctx, base, stops, dp.Draft.from_json(draft.to_json()), {201}, {_isn(50)}, 'insert:1', -30.0)
    out = dp.apply_edit(ctx, stops, out, {'action': 'defer_trip', 'trip': 1}, {o.isn for o in orders})
    assert out.same_day == set() and _isn(50) in out.deferred                       # «везти завтра» — снова заказ завтра


# ============================== страница «Развоз» ==============================

NOW = datetime(2026, 10, 1, 13, 0, tzinfo=ac.YEREVAN)


def _page_setup(client, monkeypatch, *, now=NOW, same=True, extra_orders=()):
    """Заказы 30.09 (101, 102, 104 — развоз 01.10) и новые заказы 01.10: 103 (точка GPS снимка), 999 (без точки),
    104 менеджера 2 и самовывоз; загрузчики отдают заказы окна, как ERP. «Сейчас» — now по Еревану."""
    old = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 600.0, agent=2)]
    new = [_dorder(10, 103, 250.0, day=D, rev=7000.0), _dorder(11, 999, 40.0, day=D),
           _dorder(12, 104, 90.0, day=D, agent=2, rev=3000.0), _dorder(13, 101, 30.0, day=D, agent=2, van=2),
           *extra_orders]
    _dispatch_setup(client, old)
    state = client.application.extensions['route_optimizer']
    every = [*old, *new]
    data = state.dispatch_loader(None, None, None)
    state.dispatch_loader = lambda since, until, day: replace(
        data, orders=tuple(o for o in every if since <= o.order_date < until))
    loads = []
    if same:
        def same_loader(day):
            loads.append(day)
            return dp.SameDayData(tuple(o for o in every if o.order_date == day),
                                  {_isn(10): datetime(2026, 10, 1, 8, 41), _isn(12): datetime(2026, 10, 1, 9, 5)},
                                  {103: ('C103', 'Клиент 103')}, {103: 'Ереван, 3'}, datetime(2026, 10, 1, 12, 0))
        state.same_day_loader = same_loader
    else:
        state.same_day_loader = None
    monkeypatch.setattr(views, '_yerevan_now', lambda: now)
    monkeypatch.setattr(views, '_clock', lambda: now.replace(tzinfo=None))
    return state, loads


def _build(client, trucks=('CAR2',)):
    r = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': list(trucks)})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _plan_stops(d):
    return {s['customer_id']: tr['truck'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}


def test_page_lists_new_orders_of_today_with_time_and_manager_filter(client, monkeypatch):
    _page_setup(client, monkeypatch)
    d = _build(client)
    sd = d['same_day']
    assert (sd['today'], sd['now'], sd['count'], sd['kg'], sd['revenue']) == (True, '13:00', 3, 380, 20000)
    rows = {o['customer_id']: o for o in sd['orders']}
    assert set(rows) == {103, 999, 104}                                         # самовывоз — не для машин
    assert (rows[103]['created'], rows[103]['name'], rows[103]['taken'], rows[103]['no_coords']) == \
        ('08:41', 'Клиент 103', False, False)
    assert rows[999]['no_coords'] is True and rows[999]['created'] is None and rows[104]['agent_code'] == 'A002'
    assert 103 not in _plan_stops(d) and d['orders']['count'] == 3                # в плане их нет, пока логист не взял
    # фильтр «Մենեջերներ»: заказы менеджера 2 — ни в развозе, ни в новых заказах
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'agents', 'off': [2]}).get_json()
    assert {o['customer_id'] for o in d['same_day']['orders']} == {103, 999}
    s = client.get('/api/routes/dispatch/status?date=' + DAY).get_json()
    assert {k: s['same_day'][k] for k in ('count', 'kg', 'revenue')} == {'count': 2, 'kg': 290, 'revenue': 17000}
    assert s['same_day']['sig'] == d['same_day']['sig']


def test_other_days_and_no_loader_have_no_block(client, monkeypatch):
    _page_setup(client, monkeypatch, same=False)
    d = _build(client)
    assert 'same_day' not in d and 'same_day' not in client.get('/api/routes/dispatch/status?date=' + DAY).get_json()
    _page_setup(client, monkeypatch, now=datetime(2026, 9, 30, 16, 0, tzinfo=ac.YEREVAN))
    assert 'same_day' not in client.get('/api/routes/dispatch?date=' + DAY).get_json()   # завтрашний день — без плашки
    r = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10)]})
    assert r.status_code == 400 and 'միայն այսօրվա' in r.get_json()['error']


def test_erp_down_hides_block_but_page_works(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch)

    def down(day):
        raise views.ErpError('нет связи')
    state.same_day_loader = down
    d = _build(client)
    assert 'same_day' not in d and d['plan'] is not None


def test_plan_byte_identical_without_action(client, monkeypatch):
    """Новые заказы есть, но логист ничего не выбрал — план и сохранённый черновик те же, что без загрузчика."""
    state, _ = _page_setup(client, monkeypatch, same=False)
    plain = _build(client)
    stored_plain = state.store.load_dispatch(DAY)[0]
    client.post('/api/routes/dispatch/reset', json={'date': DAY})
    _page_setup(client, monkeypatch)
    with_new = _build(client)
    stored = state.store.load_dispatch(DAY)[0]
    strip = lambda raw: {k: v for k, v in raw.items() if k not in ('built_at', 'prediction')}   # noqa: E731
    assert json.dumps(with_new['plan'] | {'built_at': None}, sort_keys=True) == \
        json.dumps(plain['plan'] | {'built_at': None}, sort_keys=True)
    assert strip(stored) == strip(stored_plain) and 'same_day' not in stored


def test_take_option_saves_plan_and_next_day_excludes_order(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client, ('CAR1', 'CAR2'))
    r = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10).lower()]})
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    assert res['rev'] == d['rev'] and res['orders'] == [_isn(10)] and res['blocked'] == [] and res['options']
    first = res['options'][0]
    assert first['amd'] == min(o['amd'] for o in res['options']) and first['stops'][0]['customer_id'] == 103
    body = {'date': DAY, 'action': 'same_day', 'orders': [_isn(10)], 'option': first['key']}
    stale = client.post('/api/routes/dispatch/edit', json={**body, 'rev': d['rev'] - 1})
    assert stale.status_code == 409 and stale.get_json()['conflict'] is True
    r = client.post('/api/routes/dispatch/edit', json={**body, 'rev': d['rev']})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert _plan_stops(d)[103] == first['truck'] and d['rev'] == res['rev'] + 1
    taken = next(o for o in d['same_day']['orders'] if o['isn'] == _isn(10))
    assert taken['taken'] is True and taken['trucks'] == [first['truck']] and d['same_day']['count'] == 2
    assert d['orders']['count'] == 4 and _isn(10) in state.store.load_dispatch(DAY)[0]['same_day']
    # повтор того же выбора — заказ уже не новый
    again = client.post('/api/routes/dispatch/edit', json={**body, 'rev': d['rev']})
    assert again.status_code == 400 and 'արդեն վերցված' in again.get_json()['error']
    # пятница 02.10: заказ 01.10 уже везли в четверг — в развозе его нет, даже без накладной; остальные заказы 01.10 — есть
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 2, 8, 0, tzinfo=ac.YEREVAN))
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 2, 8, 0))
    nxt = client.get('/api/routes/dispatch?date=' + NEXT).get_json()
    assert nxt['orders']['same_day_taken'] == 1 and nxt['orders']['count'] == 2      # 999 и 104 менеджера 2
    assert nxt['same_day']['count'] == 0 and nxt['same_day']['orders'] == []   # заказов 02.10 нет
    # четверг теперь прошедший день: взятый заказ остаётся в его плане
    past = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert _plan_stops(past)[103] == first['truck'] and past['same_day']['today'] is False
    assert [o['isn'] for o in past['same_day']['orders']] == [_isn(10)]


def test_drop_returns_order_to_next_day(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client, ('CAR1', 'CAR2'))
    key = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10)]}).get_json()['options'][0]['key']
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'same_day',
                                                       'orders': [_isn(10)], 'option': key}).get_json()
    trips = sum(len(t['trips']) for t in d['plan']['trucks'])
    # «не везём сегодня» у точки взятого заказа — он снова заказ пятницы
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'exclude', 'order': _isn(10)})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert 103 not in _plan_stops(d) and d['same_day']['count'] == 3 and 'same_day' not in state.store.load_dispatch(DAY)[0]
    assert sum(len(t['trips']) for t in d['plan']['trucks']) <= trips
    bad = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'same_day_drop',
                                                         'orders': [_isn(10)]})
    assert bad.status_code == 400
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 2, 8, 0, tzinfo=ac.YEREVAN))
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 2, 8, 0))
    nxt = client.get('/api/routes/dispatch?date=' + NEXT).get_json()
    assert 'same_day_taken' not in nxt['orders'] and nxt['orders']['count'] == 3


def test_options_request_validation(client, monkeypatch):
    _page_setup(client, monkeypatch)
    assert client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10)]}).status_code == 409
    _build(client)
    for bad in (None, [], 'x', [1], ['not-an-isn'], [_isn(13)], [_isn(1)], [_isn(10)] * 0):
        r = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': bad})
        assert r.status_code == 400, bad
    r = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(11)]})   # без точки
    assert r.status_code == 200 and r.get_json()['blocked'] == [{'customer_id': 999, 'reason': 'no_coords'}]


# ============================== приложение водителя ==============================

def test_driver_app_gets_taken_orders_and_next_day_skips_them(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    d = _build(client, ('CAR1', 'CAR2'))
    key = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10)]}).get_json()['options'][0]
    client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'same_day', 'orders': [_isn(10)],
                                                   'option': key['key']})
    every = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 600.0, agent=2),
             _dorder(10, 103, 250.0, day=D), _dorder(11, 999, 40.0, day=D), _dorder(12, 104, 90.0, day=D, agent=2)]
    view = rl.routes_view(state, D)
    assert view.same_day == frozenset({_isn(10)})
    lo, hi = rl.orders_window(D, view)
    assert hi == date(2026, 10, 2)                                               # окно ERP — и с заказами самого дня
    window = [o for o in every if lo <= o.order_date < hi]
    mine = rl.pick_orders(window, D, view, key['truck'])
    assert _isn(10) in {o.isn for o in mine} and not {_isn(11), _isn(12)} & {o.isn for o in mine}
    # без плана — по машине заказа: из заказов самого дня — только взятый
    by_car = {o.isn for o in rl.pick_orders(window, D, replace(view, plan_exists=False), '')}
    assert _isn(10) in by_car and not {_isn(11), _isn(12)} & by_car
    assert rl.invoice_owner(view, key['truck'])(103) is True
    nxt = rl.routes_view(state, date(2026, 10, 2))
    assert nxt.taken == frozenset({_isn(10)}) and rl.orders_window(date(2026, 10, 2), nxt)[1] == date(2026, 10, 2)
    picked = rl.pick_orders(every, date(2026, 10, 2), replace(nxt, plan_exists=False), '')
    assert _isn(10) not in {o.isn for o in picked}
    # без взятых заказов дня окно и отбор — прежние
    plain = rl.RoutesView()
    assert rl.orders_window(D, plain)[1] == D

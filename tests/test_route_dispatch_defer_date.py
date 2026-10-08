# -*- coding: utf-8 -*-
"""«Развоз»: «Այսօր չենք տանում» — «Երբ տանել» (владелец 08.10): логист выбирает день, когда везти заказы магазина
(рабочий день после дня плана, не дальше недели, Draft.later); в тот день они сами входят в развоз как перенесённые —
на странице и на терминале водителя; «Չգիտեմ» — как прежнее «не везём сегодня». Синтетические данные, без ERP; база
«Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_defer_date.py -q
"""
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import routes_link as rl  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_optimizer import SIX, _dispatch_setup, _dorder, _dp_ctx, _isn, client  # noqa: E402,F401

DAY = '2026-10-01'                                  # чт
THU, FRI, MON, TUE = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)
WED = date(2026, 9, 30)
OLD = date(2026, 9, 28)                             # заказ прошлых дней для развоза 01.10


@pytest.fixture(autouse=True)
def _before_day(monkeypatch):
    """Сейчас — вечер 30.09: развоз 01.10 и следующие дни — не прошедшие."""
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 18, 0))
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 9, 30, 18, 0))


def _orders():
    # 101, 102 — заказы дня 01.10; 104 — заказ дня и заказ прошлых дней (28.09)
    return [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 100.0),
            _dorder(6, 104, 70.0, day=OLD)]


def _build(client, day=DAY):
    d = client.post('/api/routes/dispatch/build', json={'date': day, 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['plan'] is not None, d
    return d


def _edit(client, d, day=DAY, **body):
    r = client.post('/api/routes/dispatch/edit', json={'date': day, 'rev': d['rev'], **body})
    return r.status_code, r.get_json()


def _get(client, day):
    return client.get('/api/routes/dispatch?date=' + day).get_json()


def _stored(client, day=DAY):
    state = client.application.extensions['route_optimizer']
    return dp.Draft.from_json(state.store.load_dispatch(day)[0])


def _in_trips(d):
    return {x['customer_id'] for tr in d['plan']['trucks'] for t in tr['trips'] for x in t['stops']}


def _row(rows, i):
    return next((o for o in rows if o['isn'] == _isn(i)), None)


# ============================== черновик и правило ==============================

def test_draft_json_later_round_trip_and_validation():
    assert 'later' not in dp.Draft().to_json()                              # пусто — ключа нет (старые черновики до байта)
    d = dp.Draft(later={_isn(1): (TUE, WED)})
    raw = d.to_json()
    assert raw['later'] == {_isn(1): ['2026-10-06', '2026-09-30']}
    assert dp.Draft.from_json(raw).later == {_isn(1): (TUE, WED)}
    assert dp.Draft.from_json(raw).to_json() == raw
    bad = {**raw, 'later': {_isn(1): ['2026-10-06', '2026-09-30'], 'x': ['2026-10-06', '2026-09-30'],
                            _isn(2): ['2026-13-01', '2026-09-30'], _isn(3): ['2026-10-06'], _isn(4): 'x',
                            _isn(5): [20261006, '2026-09-30'], _isn(7): ['2026-W41-1', '2026-09-30']}}
    assert dp.Draft.from_json(bad).later == {_isn(1): (TUE, WED)}             # битые строки — мимо
    assert dp.Draft.from_json({**raw, 'later': ['x']}).later == {}
    # снимок для водителей (№81) и пересборка его не теряют
    assert dp.Draft.from_json({**raw, 'released': {'at': '2026-10-01T08:00:00'}}).for_drivers().later == {_isn(1): (TUE, WED)}
    assert dp.PlanSeen.of(d).orders == frozenset({_isn(1)})                  # №79: решён планом дня — «видел»


def test_defer_days_are_workdays_within_a_week():
    assert dp.defer_days(THU, SIX) == [FRI, date(2026, 10, 3), MON, TUE, date(2026, 10, 7), date(2026, 10, 8)]
    assert dp.defer_days(THU, SIX, {MON}) == [FRI, date(2026, 10, 3), TUE, date(2026, 10, 7), date(2026, 10, 8)]
    assert dp.defer_days(THU, (1, 2, 3, 4, 5))[-1] == date(2026, 10, 8) and date(2026, 10, 3) not in dp.defer_days(THU, (1, 2, 3, 4, 5))


def test_later_carried_latest_wins_and_take_today_cancels():
    a, b, c = _isn(1), _isn(2), _isn(3)
    thu = dp.Draft(later={a: (TUE, WED), b: (TUE, WED), c: (FRI, WED)})
    fri = dp.Draft(added={b})                                                 # b взяли «Տանել այսօր» в пятницу — везли там
    sat = dp.Draft(later={a: (date(2026, 10, 7), WED)})                       # a перенесли снова — на среду
    out = dp.later_carried([(THU, thu), (FRI, fri)], TUE)
    assert out == {a: (TUE, WED, THU)}                                        # c — на пятницу: уже прошёл
    assert dp.later_carried([(THU, thu), (FRI, fri), (date(2026, 10, 3), sat)], TUE) == {a: (date(2026, 10, 7), WED, date(2026, 10, 3))}
    mon = dp.Draft(later={a: (MON, WED)})                                     # перенесли на раньше дня — не этому дню
    assert dp.later_carried([(THU, thu), (date(2026, 10, 3), mon)], TUE) == {b: (TUE, WED, THU)}


def test_apply_edit_defer_store_classifies_orders():
    """Заказ дня — в excluded, прошлых дней — из added (перенесённый сюда — и в dropped), взятый сегодня новый заказ дня
    (№72) — только из same_day; to — в later, null — «Չգիտեմ» (прошлых дней — dismissed)."""
    day_o, old_o, carried_o, same_o = (_dorder(1, 101, 10.0), _dorder(2, 101, 10.0, day=OLD),
                                       _dorder(3, 101, 10.0, day=OLD), _dorder(4, 101, 10.0, day=THU))
    other = _dorder(5, 102, 10.0)
    stops = dp.build_stops([day_o, old_o, carried_o, same_o, other], lambda c: dp.Coord(40.18, 44.51 + c / 1000, 'erp'))
    ctx = _dp_ctx()

    def draft():
        return dp.Draft(added={old_o.isn}, same_day={same_o.isn}, deferred={day_o.isn}, dismissed=set())
    args = ({day_o.isn, other.isn}, {old_o.isn, carried_o.isn})
    out = dp.apply_edit(ctx, stops, draft(), {'action': 'defer_store', 'customer_id': 101, 'to': '2026-10-06'}, *args,
                        carried={carried_o.isn}, defer_to=[TUE])
    assert out.excluded == {day_o.isn} and out.added == set() and out.dropped == {carried_o.isn}
    assert out.same_day == set() and out.deferred == set() and out.dismissed == set()
    assert out.later == {day_o.isn: (TUE, WED), old_o.isn: (TUE, OLD), carried_o.isn: (TUE, OLD)}   # не новый заказ дня
    out = dp.apply_edit(ctx, stops, out, {'action': 'defer_store', 'customer_id': 101, 'to': None}, *args,
                        carried={carried_o.isn}, defer_to=[TUE])
    assert out.later == {} and out.dismissed == {old_o.isn, carried_o.isn} and out.excluded == {day_o.isn}
    for bad in ({'customer_id': 101, 'to': '2026-10-05'}, {'customer_id': 101, 'to': 'x'}, {'customer_id': 101, 'to': 5},
                {'customer_id': 555, 'to': None}, {'customer_id': True, 'to': None}, {'to': None}):
        with pytest.raises(dp.DispatchError):
            dp.apply_edit(ctx, stops, draft(), {'action': 'defer_store', **bad}, *args, defer_to=[TUE])
    # перенос рейса «Վաղը» и «Տանել բոլորը» снимают «Երբ տանել»
    d = dp.Draft(later={old_o.isn: (TUE, OLD)})
    assert dp.apply_edit(ctx, stops, d, {'action': 'include', 'orders': [old_o.isn]}, *args).later == {}


def test_defer_store_refused_for_loaded_trip_without_confirm():
    o = _dorder(1, 101, 10.0)
    stops = dp.build_stops([o], lambda c: dp.Coord(40.18, 44.51, 'erp'))
    ctx = _dp_ctx()
    draft = dp.Draft(trucks=['991AT61'], trips=[dp.DraftTrip(1, '991AT61', [101], True, None, {'at': '2026-10-01T08:00:00', 'by': 'x', 'pin': True})])
    edit = {'action': 'defer_store', 'customer_id': 101, 'to': None}
    with pytest.raises(dp.LoadedEdit):
        dp.apply_edit(ctx, stops, draft, edit, {o.isn}, defer_to=[TUE])
    assert dp.apply_edit(ctx, stops, draft, edit, {o.isn}, defer_to=[TUE], loaded_ok=True).excluded == {o.isn}


# ============================== страница «Развоза» ==============================

def test_defer_store_day_and_backlog_orders_in_one_edit(client):
    calls = _dispatch_setup(client, _orders())
    d = _build(client)
    assert d['defer_days'] == ['2026-10-02', '2026-10-03', '2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08']
    code, d = _edit(client, d, action='include', order=_isn(6))               # 104: заказ дня + взятый прошлых дней
    assert code == 200 and 104 in _in_trips(d)
    code, d = _edit(client, d, action='defer_store', customer_id=104, to='2026-10-06')
    assert code == 200, d
    assert 104 not in _in_trips(d)
    st = _stored(client)
    assert st.excluded == {_isn(3)} and st.added == set() and st.dismissed == set()
    assert st.later == {_isn(3): (TUE, WED), _isn(6): (TUE, OLD)}
    assert _row(d['excluded'], 3)['later_to'] == '2026-10-06'
    row = _row(d['backlog'], 6)
    assert row['later_to'] == '2026-10-06' and not row['taken'] and not row['dismissed']
    code, body = _edit(client, d, action='defer_store', customer_id=104, to=None)   # магазина в развозе уже нет
    assert code == 400 and 'թարմացրեք' in body['errors']['_']
    # вернули оба заказа («Վերադարձնել», «Տանել այսօր») — перенос снят; «Չգիտեմ»: заказ прошлых дней — «Չտանել»
    code, d = _edit(client, d, action='include', order=_isn(3))
    code, d = _edit(client, d, action='include', orders=[_isn(6)])
    assert code == 200 and _stored(client).later == {} and 104 in _in_trips(d)
    code, d = _edit(client, d, action='defer_store', customer_id=104, to=None)
    st = _stored(client)
    assert code == 200 and st.later == {} and st.dismissed == {_isn(6)} and st.excluded == {_isn(3)}
    assert 'later' not in st.to_json()
    assert calls[0][0] == OLD                                                 # окно ERP дня — прежнее


def test_defer_store_bad_targets_are_refused(client):
    _dispatch_setup(client, _orders())
    d = _build(client)
    for to in ('2026-10-04', DAY, '2026-09-30', '2026-10-09', '2026-10-6', 'завтра', 7):   # вс, сам день, прошлое, > 7 дней
        code, body = _edit(client, d, action='defer_store', customer_id=101, to=to)
        assert code == 400 and 'աշխատանքային' in body['errors']['_'], (to, body)
    code, body = _edit(client, d, action='defer_store', customer_id=555, to='2026-10-02')
    assert code == 400
    assert _stored(client).later == {} and _stored(client).excluded == set()


def test_include_undoes_later_and_past_day_refuses(client, monkeypatch):
    _dispatch_setup(client, _orders())
    d = _build(client)
    code, d = _edit(client, d, action='defer_store', customer_id=101, to='2026-10-06')
    assert code == 200 and _stored(client).later == {_isn(1): (TUE, WED)}
    code, d = _edit(client, d, action='include', order=_isn(1))               # «Վերադարձնել» — перенос снят
    assert code == 200 and _stored(client).later == {} and _isn(1) not in _stored(client).excluded
    code, d = _edit(client, d, action='defer_store', customer_id=101, to='2026-10-06')
    assert code == 200
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 2, 10, 0))   # день прошёл
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 10, 2, 10, 0))
    d = _get(client, DAY)
    assert d['defer_days'] == []
    code, body = _edit(client, d, action='include', order=_isn(1))            # задним числом перенос не снимается
    assert code == 400 and 'Прошедший день' in body['errors']['_']
    code, body = _edit(client, d, action='defer_store', customer_id=102, to='2026-10-06')
    assert code == 400                                                        # прошедшему дню переносить некуда
    code, d = _edit(client, d, action='defer_store', customer_id=102, to=None)   # «Չգիտեմ» — можно, как exclude
    assert code == 200 and _isn(2) in _stored(client).excluded


def test_deferred_order_comes_on_its_day_only(client):
    """Чт → вт: во вторник заказ сам в развозе (ERP читается с его даты), в пятницу — только строка «Նախորդ օրերից» с
    «կտանենք», в понедельник его не видно; лишние старые заказы вторник не показывает."""
    calls = _dispatch_setup(client, _orders())
    d = _build(client)
    code, d = _edit(client, d, action='defer_store', customer_id=101, to='2026-10-06')
    assert code == 200
    tue = _get(client, '2026-10-06')
    assert calls[-1][0] == WED                                                # окно ERP — с даты заказа (обычное — с 02.10)
    assert [o['isn'] for o in tue['backlog']] == [_isn(1)]                    # заказы 28.09–30.09 не просочились
    row = tue['backlog'][0]
    assert row['taken'] and row['carried'] and row['carried_from'] == DAY and 'later_to' not in row
    assert tue['orders']['count'] == 1 and tue['orders']['kg'] == 400
    st = client.get('/api/routes/dispatch/status?date=2026-10-06').get_json()
    assert st['orders']['count'] == 1                                         # автообновление — тот же отбор
    fri = _get(client, '2026-10-02')
    row = _row(fri['backlog'], 1)
    assert row is not None and not row['taken'] and not row['carried'] and row['later_to'] == '2026-10-06'
    assert _isn(1) not in {o['isn'] for o in fri['excluded']}
    mon = _get(client, '2026-10-05')
    assert _row(mon['backlog'], 1) is None and mon['orders']['count'] == 0
    # во вторник логист собирает план — магазин в рейсах; «Հանել» — как с переносом «Վաղը»
    d = _build(client, '2026-10-06')
    assert 101 in _in_trips(d)
    code, d = _edit(client, d, day='2026-10-06', action='exclude', order=_isn(1))
    assert code == 200 and 101 not in _in_trips(d) and _stored(client, '2026-10-06').dropped == {_isn(1)}


def test_take_today_on_intermediate_day_cancels_later(client):
    _dispatch_setup(client, _orders())
    d = _build(client)
    _edit(client, d, action='defer_store', customer_id=101, to='2026-10-06')
    fri = _build(client, '2026-10-02')
    code, fri = _edit(client, fri, day='2026-10-02', action='include', order=_isn(1))
    assert code == 200 and _row(fri['backlog'], 1)['taken']
    tue = _get(client, '2026-10-06')
    assert tue['orders']['count'] == 0 and _row(tue['backlog'], 1) is None


def test_released_plan_carries_only_what_was_sent(client):
    """№81: вторник берёт перенос из отправленного водителям плана — до «Ուղարկել» его нет (как «Везти завтра»)."""
    _dispatch_setup(client, _orders())
    d = _build(client)
    code, d = _edit(client, d, action='approve')
    assert code == 200
    code, d = _edit(client, d, action='defer_store', customer_id=101, to='2026-10-06')
    assert code == 200 and d['unsent'] is not None and d['unsent']['orders']
    assert _get(client, '2026-10-06')['orders']['count'] == 0
    code, d = _edit(client, d, action='send')
    assert code == 200 and d['unsent'] is None
    assert _get(client, '2026-10-06')['orders']['count'] == 1


# ============================== терминал водителя ==============================

def test_driver_gets_deferred_order_on_target_day(client):
    _dispatch_setup(client, _orders())
    state = client.application.extensions['route_optimizer']
    d = _build(client)
    _edit(client, d, action='defer_store', customer_id=101, to='2026-10-06')
    fri = rl.routes_view(state, FRI)
    assert _isn(1) not in fri.carried and fri.carried_since is None
    tue = rl.routes_view(state, TUE)
    assert _isn(1) in tue.carried and tue.carried_since == WED and not tue.released
    lo, hi = rl.orders_window(TUE, tue)
    assert (lo, hi) == (WED, date(2026, 10, 7))                               # ERP — и с даты перенесённого заказа
    assert rl.orders_window(TUE, replace(tue, carried_since=None))[0] == FRI  # без переноса — обычное окно
    every = _orders()
    assert rl.pick_orders(every, TUE, tue, 'CAR1') == [] == rl.pick_orders(every, TUE, tue, 'CAR2')   # №80: не выпущен
    d = _build(client, '2026-10-06')
    code, d = _edit(client, d, day='2026-10-06', action='approve')
    assert code == 200
    truck = next(t['car_code'] for t in d['plan']['trucks'] for tr in t['trips'] if any(s['customer_id'] == 101 for s in tr['stops']))
    other = next(c for c in ('CAR1', 'CAR2') if c != truck)
    tue = rl.routes_view(state, TUE)
    assert tue.released
    window = [o for o in every if lo <= o.order_date < hi]
    assert [o.isn for o in rl.pick_orders(window, TUE, tue, truck)] == [_isn(1)]   # старые 28.09 — не его
    assert rl.pick_orders(window, TUE, tue, other) == []


def test_defer_store_with_taken_same_day_order(client, monkeypatch):
    """Новый заказ дня (№72), взятый сегодня в рейс магазина, — снова заказ следующего дня, не «Երբ տանել»; рейс с ним уже
    грузится — магазин целиком не переносится (его отвезли бы дважды)."""
    from route_optimizer import actuals as ac
    from test_route_dispatch_same_day import _page_setup

    def at(h):
        now = datetime(2026, 10, 1, h, 0, tzinfo=ac.YEREVAN)
        monkeypatch.setattr(views, '_yerevan_now', lambda: now)
        monkeypatch.setattr(views, '_same_day_now', lambda: now.replace(tzinfo=None))
        monkeypatch.setattr(views, '_clock', lambda: now.replace(tzinfo=None))
    _page_setup(client, monkeypatch, now=datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN))
    at(8)
    d = _build(client)
    key = client.post('/api/routes/dispatch/same-day', json={'date': DAY, 'orders': [_isn(10)]}).get_json()['options'][0]
    code, d = _edit(client, d, action='same_day', orders=[_isn(10)], option=key['key'])
    assert code == 200 and _stored(client).same_day == {_isn(10)}
    cid = 103
    at(17)                                                                    # рейс с 103 уже грузится / в пути
    d = _get(client, DAY)
    code, body = _edit(client, d, action='defer_store', customer_id=cid, to='2026-10-06')
    assert code == 400 and 'վաղվան թողնել չի կարելի' in body['errors']['_']
    assert _stored(client).same_day == {_isn(10)}
    at(8)
    d = _get(client, DAY)
    code, d = _edit(client, d, action='defer_store', customer_id=cid, to='2026-10-06')
    assert code == 200, d
    st = _stored(client)
    assert st.same_day == set() and st.later == {} and st.excluded == set()

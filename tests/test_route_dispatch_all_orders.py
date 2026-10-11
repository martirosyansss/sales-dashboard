# -*- coding: utf-8 -*-
"""«Развоз» №98 (docs/plans/dispatch-all-orders-plan.md, «как у больших компаний», экран прежний — только логика):
A — сегодня и дальше по Еревану менеджеров снимает только правило настроек (dp.day_agents_off): выбор дня в плане
(Draft.agents_off) не действует и правкой «agents» не меняется, прошедший день — как был; терминал — тот же отбор.
B — недовезённое вчера (dp.auto_carried): заказ отправленного плана прошлого рабочего дня, чью точку O: терминал не
закрыл, сам входит в развоз следующего (как «Վաղը»), с плашкой «Երեկվանից»; не отгружен; решение логиста того дня
(«Այսօր չենք տանում», «Երբ տանել») — уважается; цепочка — дальше; не всё помещается — вне рейсов сначала новые
(Stop.priority, fleet._prioritize). Синтетические данные, без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_all_orders.py -q
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import clock  # noqa: E402
from courier import routes_link as rl  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import geo  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_optimizer import FORD, SIX, _dispatch_setup, _dorder, _dp_ctx, _isn, client  # noqa: E402,F401

WED, THU, FRI, SAT = date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
YEREVAN = timezone(timedelta(hours=4))


def _now(monkeypatch, at):
    """Сейчас по Еревану — и для «Развоза», и для терминалов «Առաքիչ»."""
    monkeypatch.setattr(views, '_clock', lambda: at)
    monkeypatch.setattr(views, '_same_day_now', lambda: at)
    monkeypatch.setattr(views, '_yerevan_now', lambda: at.replace(tzinfo=YEREVAN))
    monkeypatch.setattr(clock, 'now', lambda: at.replace(tzinfo=YEREVAN))


def _state(client):
    return client.application.extensions['route_optimizer']


def _build(client, day, **extra):
    r = client.post('/api/routes/dispatch/build', json={'date': day.isoformat(), 'trucks': ['CAR1', 'CAR2'], **extra})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _edit(client, d, day, **body):
    r = client.post('/api/routes/dispatch/edit', json={'date': day.isoformat(), 'rev': d['rev'], **body})
    return r.status_code, r.get_json()


def _get(client, day):
    return client.get('/api/routes/dispatch?date=' + day.isoformat()).get_json()


def _stored(client, day):
    return dp.Draft.from_json(_state(client).store.load_dispatch(day.isoformat())[0])


def _row(rows, i):
    return next((o for o in rows if o['isn'] == _isn(i)), None)


def _stop_orders(plan):
    """fISN → заказ точки плана (в рейсах и «ещё не в рейсах»)."""
    stops = [s for t in plan['trucks'] for tr in t['trips'] for s in tr['stops']] + plan['unassigned']
    return {o['isn']: o for s in stops for o in s['orders']}


class Facts:
    """Факт терминалов (live.LiveFacts): день → машина → точки /day."""

    def __init__(self):
        self.days = {}

    def stop(self, day, car, i, cid, status='pending', closed=None):
        row = self.days.setdefault(day.isoformat(), {}).setdefault(car, {'stops': [], 'closed_at': None})
        row['stops'].append({'stop_id': 'O:' + _isn(i), 'customer_id': cid, 'status': status})
        if closed is not None:
            row['closed_at'] = closed

    def fleet(self, day):
        return self.days.get(day, {})


# ============================== A. все заказы дня ==============================

def test_day_agents_off_rule():
    settings = {'dispatch_agents_off': [3]}
    draft = dp.Draft(agents_off={2})
    assert dp.day_agents_off(draft, settings, THU, THU) == {3}       # сегодня — только правило настроек
    assert dp.day_agents_off(draft, settings, FRI, THU) == {3}       # и дальше
    assert dp.day_agents_off(draft, settings, WED, THU) == {2}       # прошедший день — выбор его плана
    assert dp.day_agents_off(None, settings, WED, THU) == {3}        # без плана — правило, как раньше


def test_future_day_ignores_plan_agents_past_day_keeps_them(client, monkeypatch):
    orders = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 500.0, agent=2)]
    _dispatch_setup(client, orders)
    # 02.10: день 01.10 прошёл — фильтр страницы, как был (история плана)
    _now(monkeypatch, datetime(2026, 10, 2, 10, 0))
    d = _build(client, THU, agents_off=[2])
    assert d['agents_off'] == [2] and d['orders']['count'] == 1 and 'agents_rule' not in d
    assert rl.routes_view(_state(client), THU).agents_off == {2}
    # тот же план, но сегодня 30.09: 01.10 — будущий день, менеджеров снимает только правило настроек (пусто)
    _now(monkeypatch, datetime(2026, 9, 30, 18, 0))
    d = _get(client, THU)
    assert d['agents_off'] == [] and d['orders']['count'] == 3 and d['agents_rule'] is True
    assert [a['off'] for a in d['agents']] == [False, False] and d['orders']['agents_off'] == 0
    assert 'settings_differ' not in d                                # выбор плана не повод «Կիրառել կարգավորումները»
    assert _stored(client, THU).agents_off == {2}                    # в черновике остаётся — история
    assert rl.routes_view(_state(client), THU).agents_off == frozenset()   # терминал — тот же отбор
    st = client.get('/api/routes/dispatch/status?date=' + THU.isoformat()).get_json()
    assert st['orders']['count'] == 3
    # правка «agents» будущего дня — 400 с армянским текстом; план не меняется
    code, body = _edit(client, d, THU, action='agents', off=[1])
    assert code == 400 and body['error'] == views.AGENTS_RULE_ONLY
    assert _stored(client, THU).agents_off == {2}
    # сборка будущего дня выбор страницы не принимает и не сохраняет
    d = _build(client, THU, agents_off=[1, 2])
    assert d['agents_off'] == [] and d['orders']['count'] == 3 and _stored(client, THU).agents_off == {2}
    bad = client.post('/api/routes/dispatch/build', json={'date': THU.isoformat(), 'trucks': ['CAR1'], 'agents_off': 'x'})
    assert bad.status_code == 400                                    # битое поле — как раньше
    # правило настроек действует и на будущий день с планом — и на терминале
    r = client.post('/api/routes/settings', json={'settings': {'dispatch_agents_off': [2]}})
    assert r.status_code == 200, r.get_json()
    d = _get(client, THU)
    assert d['agents_off'] == [2] and d['orders']['count'] == 1 and [a['off'] for a in d['agents']] == [False, True]
    assert rl.routes_view(_state(client), THU).agents_off == {2}


# ============================== B. недовезённое вчера ==============================

def _thursday_sent(client, monkeypatch, orders, edits=()):
    """План 01.10 собран вечером 30.09, правки edits, утверждён (отправлен водителям); затем «сейчас» — утро 02.10."""
    _dispatch_setup(client, orders)
    _now(monkeypatch, datetime(2026, 9, 30, 18, 0))
    d = _build(client, THU)
    for body in edits:
        code, d = _edit(client, d, THU, **body)
        assert code == 200, d
    code, d = _edit(client, d, THU, action='approve')
    assert code == 200, d
    _now(monkeypatch, datetime(2026, 10, 2, 8, 0))
    facts = Facts()
    _state(client).live_facts = facts
    return facts


# заказы 30.09 — развоз 01.10: 101 (менеджер 1), 102 (менеджер 2)
ORDERS = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)]


def test_auto_carried_rule():
    plan = dp.Draft(trips=[dp.DraftTrip(1, 'CAR1', [101, 102, 104])], excluded={_isn(3)}, released={'at': 'x', 'by': None})
    fleet = {'CAR1': {'stops': [{'stop_id': 'O:' + _isn(1), 'customer_id': 101, 'status': 'pending'},
                                {'stop_id': 'O:' + _isn(2), 'customer_id': 102, 'status': 'full'},
                                {'stop_id': 'O:' + _isn(3), 'customer_id': 104, 'status': 'pending'},
                                {'stop_id': 'O:' + _isn(4), 'customer_id': 999, 'status': 'pending'},
                                {'stop_id': 'S:' + _isn(5), 'customer_id': 101, 'status': 'pending'},
                                {'stop_id': 'O:bad', 'customer_id': 101, 'status': 'pending'}],
                     'closed_at': None}}
    # день прошёл: не закрыта — переносится; доставлена, исключена логистом, магазин не в рейсах, накладная — нет
    assert dp.auto_carried([(plan, fleet, True)]) == {_isn(1)}
    assert dp.auto_carried([(plan, fleet, False)]) == set()           # день идёт, машина день не закрыла — ещё везут
    closed = {'CAR1': {**fleet['CAR1'], 'closed_at': '2026-10-01T18:00:00+04:00'}}
    assert dp.auto_carried([(plan, closed, False)]) == {_isn(1)}      # закрыла день — недовезённое известно
    unreleased = dp.Draft(trips=plan.trips)
    assert dp.auto_carried([(unreleased, fleet, True)]) == set()      # план не выпущен — терминал его не получал
    assert dp.auto_carried([(None, fleet, True)]) == set()
    # довезли в нерабочий день между ними — не переносится
    sunday = {'CAR2': {'stops': [{'stop_id': 'O:' + _isn(1), 'customer_id': 101, 'status': 'partial'}]}}
    assert dp.auto_carried([(plan, fleet, True), (None, sunday, True)]) == set()
    for decided in ({'deferred': {_isn(1)}}, {'dismissed': {_isn(1)}}, {'dropped': {_isn(1)}},
                    {'later': {_isn(1): (date(2026, 10, 6), WED)}}):
        other = dp.Draft(trips=plan.trips, released=plan.released, excluded=plan.excluded, **decided)
        assert dp.auto_carried([(other, fleet, True)]) == set(), decided


def test_late_days():
    o = _dorder(1, 101, 1.0, day=WED)
    assert [dp.late_days(o, d, SIX) for d in (THU, FRI, SAT, date(2026, 10, 5))] == [0, 1, 2, 3]
    assert dp.late_days(o, FRI, SIX, {THU}) == 0                     # праздник — не день ожидания
    assert dp.late_days(_dorder(2, 101, 1.0, day=SAT), date(2026, 10, 5), SIX) == 0   # субботний — в понедельник
    assert dp.late_days(_dorder(3, 101, 1.0, day=FRI), FRI, SIX) == 0   # новый заказ дня (№72)


def test_undelivered_order_comes_next_day_with_badge(client, monkeypatch):
    facts = _thursday_sent(client, monkeypatch, ORDERS)
    facts.stop(THU, 'CAR1', 1, 101)                                   # 101 не довезли
    facts.stop(THU, 'CAR1', 2, 102, 'full')                           # 102 довезли (накладной ещё нет)
    d = _get(client, FRI)
    row = _row(d['backlog'], 1)
    assert row['carried'] and row['taken']                            # сам, без «Տանել այսօր»
    assert _stored(client, THU).added == set() and _state(client).store.load_dispatch(FRI.isoformat()) is None
    assert not _row(d['backlog'], 2)['taken']                         # доставлен по терминалу — не везём второй раз
    assert d['orders']['count'] == 1 and d['orders']['kg'] == 400
    st = client.get('/api/routes/dispatch/status?date=' + FRI.isoformat()).get_json()
    assert st['orders']['count'] == 1                                 # автообновление — тот же отбор
    d = _build(client, FRI)
    orders = _stop_orders(d['plan'])
    assert orders[_isn(1)]['carried_days'] == 1 and _isn(2) not in orders   # плашка «Երեկվանից»
    # терминал водителя — тот же перенос
    code, d = _edit(client, d, FRI, action='approve')
    assert code == 200
    view = rl.routes_view(_state(client), FRI)
    assert _isn(1) in view.carried and _isn(2) not in view.carried
    truck = next(t['car_code'] for t in d['plan']['trucks'] for tr in t['trips'] if any(s['customer_id'] == 101 for s in tr['stops']))
    assert [o.isn for o in rl.pick_orders(ORDERS, FRI, view, truck)] == [_isn(1)]
    # раздела «Առաքիչ» нет — довезли ли, неизвестно: сам не переносится (виден в «Նախորդ օրերից»)
    _state(client).live_facts = None
    assert not _row(_get(client, date(2026, 10, 2))['backlog'], 1)['carried']


def test_shipped_order_is_not_carried(client, monkeypatch):
    orders = [_dorder(1, 101, 400.0, shipped=THU), _dorder(2, 102, 300.0, agent=2)]
    facts = _thursday_sent(client, monkeypatch, orders)
    facts.stop(THU, 'CAR1', 1, 101)                                   # точку не закрыли, но накладная проведена 01.10
    d = _get(client, FRI)
    assert _row(d['backlog'], 1) is None and d['orders']['count'] == 0


def test_logist_decisions_of_that_day_are_respected(client, monkeypatch):
    facts = _thursday_sent(client, monkeypatch, [*ORDERS, _dorder(3, 104, 200.0)], edits=[
        {'action': 'exclude', 'order': _isn(1)},                                   # «Այսօր չենք տանում»
        {'action': 'defer_store', 'customer_id': 102, 'to': '2026-10-06'}])        # «Երբ տանել» — вторник
    facts.stop(THU, 'CAR1', 1, 101)
    facts.stop(THU, 'CAR1', 2, 102)
    d = _get(client, FRI)
    assert not _row(d['backlog'], 1)['carried'] and not _row(d['backlog'], 2)['carried']
    assert d['orders']['count'] == 0


def test_not_carried_while_truck_still_on_the_road(client, monkeypatch):
    """План на завтра собирают, пока машины сегодня в пути: недовезённое известно, когда машина закрыла день."""
    facts = _thursday_sent(client, monkeypatch, ORDERS)
    _now(monkeypatch, datetime(2026, 10, 1, 16, 0))                   # сегодня 01.10, смотрим 02.10
    facts.stop(THU, 'CAR1', 1, 101)
    assert not _row(_get(client, FRI)['backlog'], 1)['carried']
    facts.days[THU.isoformat()]['CAR1']['closed_at'] = '2026-10-01T17:40:00+04:00'
    assert _row(_get(client, FRI)['backlog'], 1)['carried']
    _now(monkeypatch, datetime(2026, 10, 3, 9, 0))                    # прошедший день 02.10 — история как была
    assert not _row(_get(client, FRI)['backlog'], 1)['carried']


def test_chain_moves_on_until_delivered(client, monkeypatch):
    facts = _thursday_sent(client, monkeypatch, ORDERS)
    facts.stop(THU, 'CAR1', 1, 101)
    facts.stop(THU, 'CAR1', 2, 102, 'full')
    d = _build(client, FRI)
    code, d = _edit(client, d, FRI, action='approve')
    assert code == 200
    facts.stop(FRI, 'CAR1', 1, 101)                                   # и 02.10 не довезли
    _now(monkeypatch, datetime(2026, 10, 3, 8, 0))
    d = _build(client, SAT)
    assert _stop_orders(d['plan'])[_isn(1)]['carried_days'] == 2      # «2 օր»
    assert _isn(1) in rl.routes_view(_state(client), SAT).carried
    facts.days[FRI.isoformat()]['CAR1']['stops'][0]['status'] = 'full'   # довезли — дальше не идёт
    assert not _row(_get(client, SAT)['backlog'], 1)['carried']


# ============================== приоритет при сборке ==============================

def test_build_leaves_new_orders_out_before_carried():
    """Машина за день успевает один рейс — в нём перенесённый заказ, новый — «ещё не в рейсах»."""
    tn = fl.TruckNorms(work_minutes=60.0, unload_min_per_stop=8.0, unload_min_per_tonne=6.0)
    ctx = _dp_ctx(trucks=(FORD,), tn=tn)
    points = {101: (40.200, 44.640), 102: (40.205, 44.650)}
    coord = lambda c: geo.Coord(*points[c], 'erp')   # noqa: E731
    for old_cid, new_cid in ((101, 102), (102, 101)):
        carried = _dorder(1, old_cid, 3000.0, day=date(2026, 9, 29))
        fresh = _dorder(2, new_cid, 3000.0)
        stops = dp.build_stops([carried, fresh], coord, lambda o: dp.late_days(o, ctx.day, SIX))
        assert {s.customer_id: s.priority for s in stops} == {old_cid: 1, new_cid: 0}
        draft = dp.build(ctx, stops, None, [FORD.car_code], 'now')
        assert [t.stops for t in draft.trips] == [[old_cid]], (old_cid, draft.trips)
        assert draft.no_room == {new_cid}
        # без приоритета (как до №98) — тот же день помещает один из двух, какой — решает расчёт
        plain = dp.build(ctx, dp.build_stops([carried, fresh], coord), None, [FORD.car_code], 'now')
        assert len(plain.trips) == 1 and len(plain.no_room) == 1


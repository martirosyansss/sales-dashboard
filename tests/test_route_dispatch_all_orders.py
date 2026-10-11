# -*- coding: utf-8 -*-
"""«Развоз» №98 (docs/plans/dispatch-all-orders-plan.md, «как у больших компаний», экран прежний — только логика):
A — сегодня и дальше по Еревану менеджеров снимает только правило настроек: сборка пишет его в план (Draft.agents_off —
правило, с которым план сделан), правка «agents» не принимается, новое правило входит в собранный день только
«Կիրառել կարգավորումները» (settings_differ), терминал — по отправленному плану; прошедший день — как был.
B — недовезённое вчера (dp.auto_carried): заказ отправленного плана прошлого рабочего дня, не довезённый по терминалу,
сам входит в развоз следующего (как «Վաղը»), с плашкой «Երեկվանից»; снимок в плане (Draft.auto_carried) — терминалу
(только после отправки) и следующему дню (цепочка, и для не поставленного в рейс); не отгружен; решение логиста того дня
уважается; результат не зависит от часов. Не всё помещается — вне рейсов сначала новые (Stop.priority,
fleet._prioritize: окна, центр, допуск машин, отдельный рейс, тяжёлый магазин, закреплённые — соблюдаются). Синтетические
данные, без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_all_orders.py -q
"""
import sys
from dataclasses import replace
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
from test_route_optimizer import (DP_DEPOT, DP_NORMS, FORD, SIX, _dispatch_setup, _dorder, _dp_ctx,  # noqa: E402,F401
                                  _isn, client)

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

def test_today_plan_keeps_the_rule_it_was_made_with(client, monkeypatch):
    orders = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 500.0, agent=2)]
    _dispatch_setup(client, orders)
    _now(monkeypatch, datetime(2026, 9, 30, 18, 0))                  # 01.10 — завтра
    # сборка выбор страницы не принимает — пишет в план правило настроек (пусто)
    d = _build(client, THU, agents_off=[2])
    assert d['agents_off'] == [] and d['orders']['count'] == 3 and d['agents_rule'] is True
    assert _stored(client, THU).agents_off == set() and 'settings_differ' not in d
    bad = client.post('/api/routes/dispatch/build', json={'date': THU.isoformat(), 'trucks': ['CAR1'], 'agents_off': 'x'})
    assert bad.status_code == 400                                    # битое поле — как раньше
    # правка «agents» — 400 с армянским текстом; план не меняется
    code, body = _edit(client, d, THU, action='agents', off=[1])
    assert code == 400 and body['error'] == views.AGENTS_RULE_ONLY and _stored(client, THU).agents_off == set()
    code, d = _edit(client, d, THU, action='approve')
    assert code == 200
    # правило поменяли — собранный день его сам не берёт: «Կիրառել կարգավորումները» (как №74)
    r = client.post('/api/routes/settings', json={'settings': {'dispatch_agents_off': [2]}})
    assert r.status_code == 200, r.get_json()
    d = _get(client, THU)
    assert d['agents_off'] == [] and d['orders']['count'] == 3 and d['settings_differ'] == {'agents': True}
    assert rl.routes_view(_state(client), THU).agents_off == frozenset()
    code, d = _edit(client, d, THU, action='apply_settings')
    assert code == 200 and d['agents_off'] == [2] and d['orders']['count'] == 1 and 'settings_differ' not in d
    # терминал — только после «Ուղարկել» (№81)
    assert rl.routes_view(_state(client), THU).agents_off == frozenset()
    code, d = _edit(client, d, THU, action='send')
    assert code == 200 and rl.routes_view(_state(client), THU).agents_off == {2}
    # день без плана — правило настроек сразу, и на терминале
    assert _get(client, FRI)['agents_off'] == [2] and rl.routes_view(_state(client), FRI).agents_off == {2}
    # прошедший день — как был, часы его не меняют
    _now(monkeypatch, datetime(2026, 10, 5, 9, 0))
    r = client.post('/api/routes/settings', json={'settings': {'dispatch_agents_off': []}})
    assert r.status_code == 200
    d = _get(client, THU)
    assert d['agents_off'] == [2] and 'agents_rule' not in d and 'settings_differ' not in d
    assert rl.routes_view(_state(client), THU).agents_off == {2}


def test_past_day_keeps_page_filter(client, monkeypatch):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    _now(monkeypatch, datetime(2026, 10, 2, 10, 0))                  # 01.10 прошёл — фильтр страницы, как раньше
    d = _build(client, THU, agents_off=[2])
    assert d['agents_off'] == [2] and d['orders']['count'] == 1 and 'agents_rule' not in d
    code, d = _edit(client, d, THU, action='agents', off=[])
    assert code == 200 and d['orders']['count'] == 2


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


def _truck_of(d, cid):
    return next(t['car_code'] for t in d['plan']['trucks'] for tr in t['trips'] if any(s['customer_id'] == cid for s in tr['stops']))


# заказы 30.09 — развоз 01.10: 101 (менеджер 1), 102 (менеджер 2)
ORDERS = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)]


def test_auto_carried_rule():
    released = {'at': 'x', 'by': None}
    plan = dp.Draft(trips=[dp.DraftTrip(1, 'CAR1', [101, 102, 104])], excluded={_isn(3)}, released=released)
    fleet = {'CAR1': {'stops': [{'stop_id': 'O:' + _isn(1), 'customer_id': 101, 'status': 'pending'},
                                {'stop_id': 'O:' + _isn(2), 'customer_id': 102, 'status': 'full'},
                                {'stop_id': 'O:' + _isn(3), 'customer_id': 104, 'status': 'pending'},
                                {'stop_id': 'O:' + _isn(4), 'customer_id': 999, 'status': 'pending'},
                                {'stop_id': 'S:' + _isn(5), 'customer_id': 101, 'status': 'pending'},
                                {'stop_id': 'O:bad', 'customer_id': 101, 'status': 'pending'}],
                     'closed_at': None}}
    # первый шаг — по терминалу: не закрыта — переносится; доставлена, исключена, магазин не в рейсах, накладная — нет
    assert dp.auto_carried([(plan, fleet, True)]) == {_isn(1)}
    assert dp.auto_carried([(plan, fleet, False)]) == set()           # день идёт, машина день не закрыла — ещё везут
    closed = {'CAR1': {**fleet['CAR1'], 'closed_at': '2026-10-01T18:00:00+04:00'}}
    assert dp.auto_carried([(plan, closed, False)]) == {_isn(1)}      # закрыла день — недовезённое известно
    assert dp.auto_carried([(dp.Draft(trips=plan.trips), fleet, True)]) == set()   # план не выпущен
    assert dp.auto_carried([(None, fleet, True)]) == set()
    # довезли в нерабочий день между ними — не переносится
    sunday = {'CAR2': {'stops': [{'stop_id': 'O:' + _isn(1), 'customer_id': 101, 'status': 'partial'}]}}
    assert dp.auto_carried([(plan, fleet, True), (None, sunday, True)]) == set()
    for decided in ({'deferred': {_isn(1)}}, {'dismissed': {_isn(1)}}, {'dropped': {_isn(1)}},
                    {'later': {_isn(1): (date(2026, 10, 6), WED)}}):
        other = dp.Draft(trips=plan.trips, released=released, excluded=plan.excluded, **decided)
        assert dp.auto_carried([(other, fleet, True)]) == set(), decided
    # следующий шаг — по снимку (M4): не поставлен в рейс (106) — не довезён и без точки; поставлен (101) — когда день его
    # машины кончился; закрыт хоть где-то — нет
    chain = dp.Draft(trips=plan.trips, released=released, auto_carried={_isn(6): 106, _isn(7): 101, _isn(8): 104})
    shut = {'CAR1': {'stops': [{'stop_id': 'O:' + _isn(8), 'customer_id': 104, 'status': 'full'}], 'closed_at': None}}
    assert dp.auto_carried([(chain, shut, False)]) == {_isn(6)}
    assert dp.auto_carried([(chain, shut, True)]) == {_isn(6), _isn(7)}
    assert dp.auto_carried([(chain, {}, True)]) == {_isn(6), _isn(7), _isn(8)}
    raw = chain.to_json()
    assert raw['auto_carried'] == {_isn(6): 106, _isn(7): 101, _isn(8): 104}
    assert dp.Draft.from_json(raw).auto_carried == chain.auto_carried
    assert dp.Draft.from_json({'auto_carried': {'x': 1, _isn(1): 'a', _isn(2): 5}}).auto_carried == {_isn(2): 5}
    assert 'auto_carried' not in dp.Draft().to_json() and 'yielded' not in dp.Draft().to_json()


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
    assert row['carried'] and row['taken'] and _state(client).store.load_dispatch(FRI.isoformat()) is None   # сам
    assert not _row(d['backlog'], 2)['taken']                         # доставлен по терминалу — не везём второй раз
    assert d['orders']['count'] == 1 and d['orders']['kg'] == 400
    st = client.get('/api/routes/dispatch/status?date=' + FRI.isoformat()).get_json()
    assert st['orders']['count'] == 1                                 # автообновление — тот же отбор
    d = _build(client, FRI)
    orders = _stop_orders(d['plan'])
    assert orders[_isn(1)]['carried_days'] == 1 and _isn(2) not in orders   # плашка «Երեկվանից»
    assert _stored(client, FRI).auto_carried == {_isn(1): 101}        # снимок — в плане
    # терминал — по отправленному плану
    code, d = _edit(client, d, FRI, action='approve')
    assert code == 200
    view = rl.routes_view(_state(client), FRI)
    assert _isn(1) in view.carried and _isn(2) not in view.carried
    assert [o.isn for o in rl.pick_orders(ORDERS, FRI, view, _truck_of(d, 101))] == [_isn(1)]
    # факт терминалов больше не читается (раздела «Առաքիչ» нет) — снимок держит перенос и на странице, и у водителя
    _state(client).live_facts = None
    assert _row(_get(client, FRI)['backlog'], 1)['carried'] and _isn(1) in rl.routes_view(_state(client), FRI).carried


def test_snapshot_survives_midnight_and_late_facts(client, monkeypatch):
    """Ревью H2: прошедший день читает снимок — не пересчитывается от «сегодня» и от поздних событий терминала."""
    facts = _thursday_sent(client, monkeypatch, ORDERS)
    facts.stop(THU, 'CAR1', 1, 101)
    facts.stop(THU, 'CAR1', 2, 102, 'full')
    d = _build(client, FRI)
    code, d = _edit(client, d, FRI, action='approve')
    truck = _truck_of(d, 101)
    before = [o.isn for o in rl.pick_orders(ORDERS, FRI, rl.routes_view(_state(client), FRI), truck)]
    _now(monkeypatch, datetime(2026, 10, 3, 0, 30))                   # полночь прошла: 02.10 — вчера
    facts.days[THU.isoformat()]['CAR1']['stops'][0]['status'] = 'full'   # поздняя пачка APK: 01.10 «довезли»
    after = [o.isn for o in rl.pick_orders(ORDERS, FRI, rl.routes_view(_state(client), FRI), truck)]
    assert before == after == [_isn(1)]
    page = _get(client, FRI)
    assert _isn(1) in _stop_orders(page['plan']) and _row(page['backlog'], 1)['carried']


def test_new_carry_reaches_driver_only_after_send(client, monkeypatch):
    """План на завтра собран и отправлен, пока машины в пути; машина закрыла день — перенос на странице («новые после
    сборки»), водителю — после «Ուղարկել»."""
    orders = [*ORDERS, _dorder(4, 101, 100.0, day=THU)]               # у 101 и заказ 01.10 — магазин в плане 02.10
    facts = _thursday_sent(client, monkeypatch, orders)
    _now(monkeypatch, datetime(2026, 10, 1, 16, 0))                   # сегодня 01.10, собираем 02.10
    facts.stop(THU, 'CAR1', 1, 101)
    facts.stop(THU, 'CAR1', 2, 102, 'full')
    d = _build(client, FRI)
    assert _isn(1) not in _stop_orders(d['plan']) and not _row(d['backlog'], 1)['carried']   # машина ещё в пути
    code, d = _edit(client, d, FRI, action='approve')
    assert code == 200
    truck = _truck_of(d, 101)
    facts.days[THU.isoformat()]['CAR1']['closed_at'] = '2026-10-01T17:40:00+04:00'
    d = _get(client, FRI)
    assert _row(d['backlog'], 1)['carried'] and d['new_since_build']['count'] == 1
    assert _stop_orders(d['plan'])[_isn(1)]['carried_days'] == 1     # магазин в рейсе — заказ в его точке
    view = rl.routes_view(_state(client), FRI)
    assert [o.isn for o in rl.pick_orders(orders, FRI, view, truck)] == [_isn(4)]   # до отправки — без него
    code, d = _edit(client, d, FRI, action='send')
    assert code == 200
    view = rl.routes_view(_state(client), FRI)
    assert sorted(o.isn for o in rl.pick_orders(orders, FRI, view, truck)) == [_isn(1), _isn(4)]


def test_shipped_order_is_not_carried(client, monkeypatch):
    """Отгружен до доставки (накладная проведена — офис решил) — не переносится никогда."""
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
    """План на завтра собирают, пока машины сегодня в пути: недовезённое известно, когда машина закрыла день; после
    полуночи день читает свой снимок (ревью: результат не зависит от часов)."""
    facts = _thursday_sent(client, monkeypatch, ORDERS)
    _now(monkeypatch, datetime(2026, 10, 1, 16, 0))                   # сегодня 01.10, смотрим 02.10
    facts.stop(THU, 'CAR1', 1, 101)
    assert not _row(_get(client, FRI)['backlog'], 1)['carried']
    facts.days[THU.isoformat()]['CAR1']['closed_at'] = '2026-10-01T17:40:00+04:00'
    assert _row(_get(client, FRI)['backlog'], 1)['carried']
    _build(client, FRI)
    _now(monkeypatch, datetime(2026, 10, 3, 9, 0))                    # 02.10 прошёл — снимок его плана
    assert _row(_get(client, FRI)['backlog'], 1)['carried']


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
    code, d = _edit(client, d, SAT, action='approve')
    assert _isn(1) in rl.routes_view(_state(client), SAT).carried
    facts.stop(SAT, 'CAR1', 1, 101, 'full')                          # 03.10 довезли — дальше не идёт
    _now(monkeypatch, datetime(2026, 10, 5, 8, 0))
    row = _row(_get(client, date(2026, 10, 5))['backlog'], 1)
    assert row is None or not row['carried']                          # старше окна «Նախորդ օրերից» — и не виден


def test_chain_goes_on_for_carried_order_left_out_of_trips(client, monkeypatch):
    """Ревью M4: перенесённый, но не поставленный в рейс (на терминале его нет), — по снимку переносится дальше."""
    orders = [*ORDERS, _dorder(4, 104, 100.0, day=THU)]               # 104 — заказ 01.10: в плане 02.10 есть рейс
    facts = _thursday_sent(client, monkeypatch, orders)
    facts.stop(THU, 'CAR1', 1, 101)
    facts.stop(THU, 'CAR1', 2, 102, 'full')
    d = _build(client, FRI)
    trip = next(tr for t in d['plan']['trucks'] for tr in t['trips'] if any(s['customer_id'] == 101 for s in tr['stops']))
    code, d = _edit(client, d, FRI, action='move', customer_id=101, from_trip=trip['id'], to_trip=None, truck=None)
    assert code == 200 and 101 in {s['customer_id'] for s in d['plan']['unassigned']}
    code, d = _edit(client, d, FRI, action='approve')
    assert code == 200 and _stored(client, FRI).for_drivers().auto_carried == {_isn(1): 101}
    # на терминале 02.10 точки 101 нет — а 03.10 заказ всё равно сам в развозе
    _now(monkeypatch, datetime(2026, 10, 3, 8, 0))
    d = _get(client, SAT)
    assert _row(d['backlog'], 1)['carried'] and _row(d['backlog'], 1)['taken']


def test_same_day_order_taken_and_not_delivered_page_equals_terminal(client, monkeypatch):
    """Ревью H1: новый заказ дня, взятый в развоз дня его приёма (№72) и не довезённый, — снова заказ следующего дня: и на
    странице (с плашкой), и у водителя."""
    orders = [*ORDERS, _dorder(3, 101, 150.0, day=THU)]
    facts = _thursday_sent(client, monkeypatch, orders)
    st = _state(client).store
    data, _ = st.load_dispatch(THU.isoformat())                       # взят в развоз 01.10 (как правка same_day)
    data = {**data, 'same_day': [_isn(3)]}
    data['sent'] = {**data['sent'], 'plan': {**data['sent']['plan'], 'same_day': [_isn(3)]}}
    st.save_dispatch(THU.isoformat(), data, 'qa')
    facts.stop(THU, 'CAR1', 1, 101, 'full')
    facts.stop(THU, 'CAR1', 3, 101)                                   # заказ дня не довезли
    d = _build(client, FRI)
    page = _stop_orders(d['plan'])
    assert set(page) == {_isn(3)} and page[_isn(3)]['carried_days'] == 1
    code, d = _edit(client, d, FRI, action='approve')
    assert code == 200
    view = rl.routes_view(_state(client), FRI)
    assert [o.isn for o in rl.pick_orders(orders, FRI, view, _truck_of(d, 101))] == [_isn(3)]


# ============================== приоритет при сборке ==============================

P1, P2, P3 = (40.200, 44.640), (40.205, 44.650), (40.210, 44.645)
SMALL = fl.FleetTruck('S1', 'SMALL', 1500.0, 12.0)


def _norms(work):
    return fl.TruckNorms(work_minutes=work, unload_min_per_stop=8.0, unload_min_per_tonne=6.0)


def _day(spec, trucks=(FORD,), work=60.0, **kw):
    """spec: (точка, кг, приоритет) → (рейсы: [(машина, [номера])], причины вне рейсов)."""
    reasons = {}
    trips = fl.route_day([p for p, _, _ in spec], [kg for _, kg, _ in spec], [1000.0] * len(spec), DP_DEPOT, list(trucks),
                         DP_NORMS, _norms(work), overflow=False, reasons=reasons, balance=True, solver=True,
                         priority=[pr for _, _, pr in spec], report=False, **kw)
    return [(t.truck, sorted(t.items)) for t in trips], reasons


def _placed(trips):
    return {i for _, items in trips for i in items}


def test_build_leaves_new_orders_out_before_carried():
    """Машина за день успевает один рейс — в нём перенесённый заказ, новый — «ещё не в рейсах» с причиной «уступил»."""
    ctx = _dp_ctx(trucks=(FORD,), tn=_norms(60.0))
    points = {101: P1, 102: P2}
    coord = lambda c: geo.Coord(*points[c], 'erp')   # noqa: E731
    swapped = 0
    for old_cid, new_cid in ((101, 102), (102, 101)):
        carried = _dorder(1, old_cid, 3000.0, day=date(2026, 9, 29))
        fresh = _dorder(2, new_cid, 3000.0)
        stops = dp.build_stops([carried, fresh], coord, lambda o: dp.late_days(o, ctx.day, SIX))
        assert {s.customer_id: s.priority for s in stops} == {old_cid: 1, new_cid: 0}
        draft = dp.build(ctx, stops, None, [FORD.car_code], 'now')
        assert [t.stops for t in draft.trips] == [[old_cid]], (old_cid, draft.trips)
        assert draft.no_room == {new_cid} and draft.yielded <= {new_cid}   # уступил — если сборка сначала поставила его
        swapped += bool(draft.yielded)
        view = dp.plan_view(ctx, stops, draft, lambda s: {'customer_id': s.customer_id})
        assert [(u['customer_id'], u.get('yielded', False)) for u in view['unassigned']] == [(new_cid, bool(draft.yielded))]
        # повтор — тот же план (детерминизм)
        again = dp.build(ctx, stops, None, [FORD.car_code], 'now')
        assert again.to_json() == {**draft.to_json(), 'built_at': again.built_at}
        # без приоритета (как до №98) — тот же день помещает один из двух, какой — решает расчёт; «уступил» нет
        plain = dp.build(ctx, dp.build_stops([carried, fresh], coord), None, [FORD.car_code], 'now')
        assert len(plain.trips) == 1 and len(plain.no_room) == 1 and not plain.yielded
    assert swapped == 1                                               # в одном из порядков сборка поставила новый — замена


def test_several_younger_orders_leave_one_trip():
    """Ревью M3: старшему не хватает места, пока в рейсе два младших, — снимаются оба."""
    trips, why = _day([(P1, 1500.0, 0), (P2, 1500.0, 0), (P3, 3000.0, 1)])
    assert trips == [(FORD.car_code, [2])] and why == {0: 'yield', 1: 'yield'}


def test_displaced_waiting_order_is_requeued():
    """Ревью M3: снятый, который сам ждёт, снова в очереди — встаёт на другую машину вместо ещё более младшего (день — один
    рейс машины; FORD: [B], SMALL: [C], ждёт A, возит его только FORD)."""
    tn = _norms(45.0)
    d, m = fl._matrices([P1, P2, P3], DP_DEPOT, DP_NORMS, tn)
    stops = [fl._Stop(1, 3000.0, 1.0, tn.unload(3000.0), priority=2), fl._Stop(2, 1200.0, 1.0, tn.unload(1200.0), priority=1),
             fl._Stop(3, 1200.0, 1.0, tn.unload(1200.0))]
    trips = [fl.Trip(FORD.car_code, 1, 1200.0, 1.0, 0.0, 0.0, FORD.capacity_kg, 0.0, False, (1,)),
             fl.Trip(SMALL.car_code, 1, 1200.0, 1.0, 0.0, 0.0, SMALL.capacity_kg, 0.0, False, (2,))]
    out, gave = fl._prioritize(trips, stops, d, m, [FORD, SMALL], tn, None, None, (), fl.LOAD_CAP)
    assert sorted((t.truck, t.items) for t in out) == [(FORD.car_code, (0,)), (SMALL.car_code, (1,))] and gave == {2}
    again, _ = fl._prioritize(trips, stops, d, m, [FORD, SMALL], tn, None, None, (), fl.LOAD_CAP)
    assert again == out                                               # детерминизм


def test_prioritize_keeps_windows_center_access_and_solo():
    """Ревью M5: старший не встаёт туда, куда сборка его не пустила бы, — младший остаётся."""
    spec = [(P1, 3000.0, 1), (P2, 3000.0, 0)]
    for kw in ({'windows': [(0.0, 1.0), (0.0, float('inf'))]},          # окно старшего — не успеть
               {'center': [True, False]},                              # старший в центре, машина без права въезда
               {'allowed_trucks': [{'OTHER'}, None]}):                 # старшего возит только другая машина
        trips, why = _day(spec, **kw)
        assert trips == [(FORD.car_code, [1])] and why.get(0) != 'yield' and 1 not in why, kw
    # отдельный рейс (№78): старший «только один в рейсе» — снимаются оба младших, вместе с ним никто не едет
    trips, why = _day([(P1, 1000.0, 0), (P2, 1000.0, 0), (P3, 1000.0, 1)], solo=[False, False, True])
    assert trips == [(FORD.car_code, [2])] and why == {0: 'yield', 1: 'yield'}


def test_prioritize_never_splits_heavy_store_or_moves_pinned():
    # тяжёлый младший магазин на два рейса (полдоставки не бывает) — не снимается, старший ждёт
    trips, why = _day([(P1, 3000.0, 1), (P2, 6000.0, 0)], work=100.0)
    assert sorted(trips) == [(FORD.car_code, [1]), (FORD.car_code, [1])] and why.get(0) == 'time'
    # закреплённый рейс с младшим — как есть
    trips, why = _day([(P1, 3000.0, 1), (P2, 3000.0, 0)], fixed=[(FORD.car_code, [1], 0.0)])
    assert trips == [(FORD.car_code, [1])] and why.get(0) != 'yield'


def test_pinned_and_loaded_trips_untouched_in_build():
    """dp.build: закреплённый и загруженный рейсы с младшим не трогаются; старший — «ещё не в рейсах»."""
    ctx = _dp_ctx(trucks=(FORD,), tn=_norms(60.0))
    points = {101: P1, 102: P2}
    coord = lambda c: geo.Coord(*points[c], 'erp')   # noqa: E731
    stops = dp.build_stops([_dorder(1, 101, 3000.0, day=date(2026, 9, 29)), _dorder(2, 102, 3000.0)], coord,
                           lambda o: dp.late_days(o, ctx.day, SIX))
    for trip in (dp.DraftTrip(1, FORD.car_code, [102], pinned=True),
                 dp.DraftTrip(1, FORD.car_code, [102], pinned=True, loaded={'at': '2026-10-01T08:00:00', 'by': 'x',
                                                                            'pin': True})):
        draft = dp.build(ctx, stops, dp.Draft(trucks=[FORD.car_code], trips=[trip], next_id=2), [FORD.car_code], 'now')
        assert [(t.id, t.stops) for t in draft.trips] == [(1, [102])] and draft.no_room == {101} and not draft.yielded


def test_plan_around_path_prioritizes():
    """С окнами и закреплённым рейсом сборка идёт через _plan_around — приоритет там тот же."""
    points = {101: P1, 102: P2, 104: P3}
    ctx = replace(_dp_ctx(trucks=(FORD,), tn=_norms(100.0)), windows={c: (0.0, 24 * 60.0) for c in points})
    coord = lambda c: geo.Coord(*points[c], 'erp')   # noqa: E731
    stops = dp.build_stops([_dorder(1, 101, 3000.0, day=date(2026, 9, 29)), _dorder(2, 102, 3000.0),
                            _dorder(3, 104, 3000.0)], coord, lambda o: dp.late_days(o, ctx.day, SIX))
    old = dp.Draft(trucks=[FORD.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [104], pinned=True)], next_id=2)
    draft = dp.build(ctx, stops, old, [FORD.car_code], 'now')
    assert sorted(t.stops for t in draft.trips) == [[101], [104]] and draft.yielded == {102}

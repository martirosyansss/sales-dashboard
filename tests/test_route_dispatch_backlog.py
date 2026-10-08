# -*- coding: utf-8 -*-
"""«Развоз»: заказы прошлых дней (владелец 08.10 — «не понятно и не функционально»). «Տանել այսօր» сразу ставит заказ в
самый дешёвый подходящий рейс (dispatch.place_added), в том числе заказ менеджера, снятого фильтром «Մենեջերներ» (явный
выбор сильнее фильтра); «Չտանել» сворачивает заказ (Draft.dismissed), «Տանել բոլորը» — все сразу. Синтетические данные,
без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_backlog.py -q
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _isn, client  # noqa: E402,F401

DAY = '2026-10-01'
OLD = date(2026, 9, 28)          # заказ прошлых дней для развоза 01.10


@pytest.fixture(autouse=True)
def _before_day(monkeypatch):
    """Сейчас — вечер 30.09: развоз 01.10 — завтрашний день (прошедший день заказ в рейсы не ставит)."""
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 18, 0))
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 9, 30, 18, 0))


def _orders(old_kg=70.0, old_agent=2):
    return [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2),
            _dorder(6, 104, old_kg, agent=old_agent, day=OLD)]


def _build(client):
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['plan'] is not None, d
    return d


def _edit(client, d, **body):
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], **body})
    return r.status_code, r.get_json()


def _row(d, i):
    return next(o for o in d['backlog'] if o['isn'] == _isn(i))


def _in_trips(d):
    return {x['customer_id'] for tr in d['plan']['trucks'] for t in tr['trips'] for x in t['stops']}


def _stored(client):
    state = client.application.extensions['route_optimizer']
    return dp.Draft.from_json(state.store.load_dispatch(DAY)[0])


def test_take_today_puts_order_straight_into_a_trip(client):
    _dispatch_setup(client, _orders())
    d = _build(client)
    row = _row(d, 6)
    assert (row['taken'], row['dismissed'], row['trip']) == (False, False, None)
    assert 104 not in _in_trips(d)
    code, d = _edit(client, d, action='include', order=_isn(6))
    assert code == 200, d
    row = _row(d, 6)
    assert row['taken'] and row['trip'] is not None                       # сразу в рейсе, не в «Դեռ երթերում չեն»
    assert 104 in _in_trips(d) and 104 not in {s['customer_id'] for s in d['plan']['unassigned']}
    trip = next(t for tr in d['plan']['trucks'] for t in tr['trips'] if t['id'] == row['trip']['id'])
    assert any(x['customer_id'] == 104 for x in trip['stops'])
    assert isinstance(d['delta_km'], float) and d['delta_km'] > 0
    # «Հանել» — из развоза и из рейса
    code, d = _edit(client, d, action='exclude', order=_isn(6))
    assert code == 200 and not _row(d, 6)['taken'] and 104 not in _in_trips(d)


def test_dont_take_folds_order_and_survives_rebuild(client):
    _dispatch_setup(client, _orders())
    d = _build(client)
    code, d = _edit(client, d, action='exclude', order=_isn(6))
    assert code == 200
    row = _row(d, 6)
    assert row['dismissed'] and not row['taken'] and 104 not in _in_trips(d)
    assert _stored(client).dismissed == {_isn(6)}
    d = _build(client)                                                     # пересборка решение не забывает
    assert _row(d, 6)['dismissed'] and 104 not in _in_trips(d)
    code, d = _edit(client, d, action='include', order=_isn(6))            # передумали — «Տանել այսօր» из свёрнутых
    row = _row(d, 6)
    assert code == 200 and row['taken'] and not row['dismissed'] and 104 in _in_trips(d)
    assert 'dismissed' not in _stored(client).to_json()                   # пусто — ключа нет (старые черновики до байта)


def test_take_today_wins_over_managers_filter(client):
    """Заказ менеджера, снятого фильтром «Մենեջերներ»: раньше кнопка писала его в added, а фильтр тут же выкидывал."""
    _dispatch_setup(client, _orders())
    d = _build(client)
    code, d = _edit(client, d, action='agents', off=[2])
    assert code == 200 and 102 not in _in_trips(d)
    row = _row(d, 6)
    assert row['agent_off'] and not row['taken']
    code, d = _edit(client, d, action='include', order=_isn(6))
    row = _row(d, 6)
    assert code == 200 and row['taken'] and row['trip'] is not None and 104 in _in_trips(d)
    assert 102 not in _in_trips(d)                                         # остальные заказы менеджера — по-прежнему нет
    assert d['orders']['agents_off'] == 1                                  # «не везём» — только заказ 102
    assert next(a for a in d['agents'] if a['agent_id'] == 2)['count'] == 1  # и у менеджера в списке — тоже


def test_take_all_and_bad_requests(client):
    orders = [*_orders(), _dorder(7, 104, 50.0, day=OLD)]
    _dispatch_setup(client, orders)
    d = _build(client)
    code, body = _edit(client, d, action='include', orders=[_isn(6), _isn(1)])   # заказ дня — не из прошлых дней
    assert code == 400
    code, body = _edit(client, d, action='include', orders=[])
    assert code == 400
    code, d = _edit(client, d, action='include', orders=[_isn(6).lower(), _isn(7)])
    assert code == 200, d
    assert _row(d, 6)['taken'] and _row(d, 7)['taken'] and 104 in _in_trips(d)
    assert _stored(client).added == {_isn(6), _isn(7)}


def test_no_room_keeps_order_taken_but_unplaced(client):
    """Тяжелее любой машины — встать некуда: заказ в развозе, точка — «ещё не в рейсах»."""
    _dispatch_setup(client, _orders(old_kg=12000.0))
    d = _build(client)
    code, d = _edit(client, d, action='include', order=_isn(6))
    row = _row(d, 6)
    assert code == 200 and row['taken'] and row['trip'] is None
    assert 104 in {s['customer_id'] for s in d['plan']['unassigned']}


def test_page_without_plan_shows_backlog_rows(client):
    _dispatch_setup(client, _orders())
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    assert d['plan'] is None
    row = _row(d, 6)
    assert (row['taken'], row['dismissed'], row['trip'], row['order_date']) == (False, False, None, OLD.isoformat())
    assert 'agent_name' in row


def test_store_in_started_trip_is_refused(client, monkeypatch):
    """Сегодня 15:00: магазин уже в рейсе, который грузится или в пути, — заказ к нему не прилипнет (товара в машине нет)."""
    _dispatch_setup(client, [*_orders(), _dorder(7, 101, 40.0, day=OLD)])
    d = _build(client)
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 1, 15, 0))
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 10, 1, 15, 0))
    d = client.get('/api/routes/dispatch?date=' + DAY).get_json()
    code, body = _edit(client, d, action='include', order=_isn(7))
    assert code == 400 and 'ճանապարհին' in body['errors']['_'] and 'Клиент <101>' in body['errors']['_']
    code, body = _edit(client, d, action='include', orders=[_isn(6), _isn(7)])     # «Տանել բոլորը» — тоже
    assert code == 400
    assert _stored(client).added == set()                                  # ничего не сохранено


def test_past_day_include_does_not_place(client, monkeypatch):
    """Прошедший день: рейсы уже проехали — «Տանել այսօր» только отмечает заказ (как было), в рейсы не ставит."""
    _dispatch_setup(client, _orders())
    d = _build(client)
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 5, 10, 0))
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 10, 5, 10, 0))
    code, d = _edit(client, d, action='include', order=_isn(6))
    row = _row(d, 6)
    assert code == 200 and row['taken'] and row['trip'] is None and 104 not in _in_trips(d)

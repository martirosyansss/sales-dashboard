# -*- coding: utf-8 -*-
"""Нерабочие даты в настройках «Маршрутов» (ответ владельца №64): праздники и прочие выходные компании
вдобавок к рабочим дням недели. «Развоз» пропускает их, как воскресенье."""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from test_route_optimizer import REF, _dispatch_setup, _dorder, _no_road_map, client, store  # noqa: E402,F401

SIX = [1, 2, 3, 4, 5, 6]


def test_holidays_skipped_like_sunday():
    wed, thu, fri, sat, mon = (date(2026, 12, 30), date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 2),
                               date(2027, 1, 4))
    off = frozenset({thu, fri, sat})
    assert dp.is_workday(wed, SIX, off) and not dp.is_workday(fri, SIX, off)
    assert not dp.is_workday(date(2027, 1, 3), SIX, off)                       # воскресенье — по дням недели
    assert dp.next_workday(wed, SIX, off) == mon                               # 31.12–03.01 не работают
    assert dp.previous_workday(mon, SIX, off) == wed
    assert dp.order_window(mon, SIX, off) == (wed, mon)                        # заказы 30.12 и праздников — в пн
    assert dp.backlog_since(wed, SIX, holidays=off) == date(2026, 12, 28)
    # без нерабочих дат — как раньше
    assert dp.next_workday(wed, SIX) == thu and dp.order_window(mon, SIX) == (sat, mon)
    # приём заказов на пн идёт в последний рабочий день перед ним (30.12), а не в субботу-праздник
    assert dp.orders_still_coming(mon, SIX, datetime(2026, 12, 30, 11, 0), '17:00', off)
    assert not dp.orders_still_coming(mon, SIX, datetime(2027, 1, 2, 11, 0), '17:00', off)


def test_holidays_of_settings():
    assert dp.holidays_of({}) == frozenset()
    assert dp.holidays_of({'holidays': ['2027-01-06', '2027-01-01']}) == {date(2027, 1, 1), date(2027, 1, 6)}


def test_store_holidays_default_roundtrip_and_validation(store):
    assert st.DEFAULT_SETTINGS['holidays'] == [] and store.load().settings['holidays'] == []
    changes, errors = st.validate_payload({'settings': {'holidays': ['2027-01-06', '2026-12-31']}}, store.load(), REF)
    assert not errors
    store.save(changes, 'qa')
    assert store.load().settings['holidays'] == ['2026-12-31', '2027-01-06']           # по возрастанию
    # другие настройки сохраняются без поля holidays — даты остаются
    changes, errors = st.validate_payload({'settings': {'min_day_revenue': 90000}}, store.load(), REF)
    store.save(changes, 'qa')
    assert store.load().settings['holidays'] == ['2026-12-31', '2027-01-06']
    for bad in ('2027-01-06', ['2027-02-30'], ['06.01.2027'], [20270106], ['2027-01-06', '2027-01-06'],
                [f'{y}-{m:02d}-{d:02d}' for y in (2027, 2028) for m in range(1, 13) for d in range(1, 29)]):   # > 400
        _, errors = st.validate_payload({'settings': {'holidays': bad}}, store.load(), REF)
        assert set(errors) == {'settings.holidays'}, (bad, errors)
    assert store.load().settings['holidays'] == ['2026-12-31', '2027-01-06']
    # пустой список — все даты сняты
    changes, errors = st.validate_payload({'settings': {'holidays': []}}, store.load(), REF)
    store.save(changes, 'qa')
    assert store.load().settings['holidays'] == []


def test_api_dispatch_skips_holiday(client, monkeypatch):
    """Завтра (02.10, пт) нерабочее: «Развоз» по умолчанию открывает субботу 03.10, её окно заказов — с 01.10;
    сам праздник открыть можно — со знаком day_off."""
    from route_optimizer import views
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 1, 10, 0))
    calls = _dispatch_setup(client, [_dorder(1, 101, 400.0, day=date(2026, 10, 1))])
    r = client.post('/api/routes/settings', json={'settings': {'holidays': ['2026-10-02']}})
    assert r.status_code == 200, r.get_json()
    assert client.get('/api/routes/settings').get_json()['settings']['holidays'] == ['2026-10-02']
    d = client.get('/api/routes/dispatch').get_json()
    assert d['day'] == '2026-10-03' and d['default_day'] == '2026-10-03' and d['day_off'] is False
    assert d['order_dates'] == {'since': '2026-10-01', 'until': '2026-10-02'}       # заказы чт и пт-праздника
    assert d['orders']['count'] == 1
    assert calls[-1][1:] == (date(2026, 10, 3), date(2026, 10, 3))
    h = client.get('/api/routes/dispatch?date=2026-10-02').get_json()
    assert h['day_off'] is True
    assert client.get('/api/routes/dispatch?date=2026-10-04').get_json()['day_off'] is True   # воскресенье


def test_api_dispatch_holidays_with_agents_filter(client, monkeypatch):
    """Праздник и фильтр «Մենեջերներ» вместе: день, окно, «с прошлых дней» и «везти завтра» — по нерабочим датам,
    а не по снятым менеджерам (в ответе дня это две разные вещи)."""
    from route_optimizer import views
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 1, 10, 0))
    _dispatch_setup(client, [_dorder(1, 101, 400.0, day=date(2026, 10, 1)),
                             _dorder(2, 102, 300.0, day=date(2026, 10, 1), agent=2)])
    assert client.post('/api/routes/settings', json={'settings': {'holidays': ['2026-10-02', '2026-10-05']}}).status_code == 200
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-03', 'trucks': ['CAR1', 'CAR2'], 'agents_off': [2]})
    assert r.status_code == 200 and r.get_json()['agents_off'] == [2], r.get_json()
    d = client.get('/api/routes/dispatch?date=2026-10-03').get_json()
    off = frozenset({date(2026, 10, 2), date(2026, 10, 5)})
    assert d['agents_off'] == [2] and d['orders']['count'] == 1
    assert d['default_day'] == '2026-10-03' and d['day_off'] is False
    assert d['backlog_since'] == dp.backlog_since(date(2026, 10, 1), SIX, holidays=off).isoformat()
    assert d['defer_to'] == '2026-10-06'                       # пн 05.10 — праздник


def test_courier_orders_window_skips_holidays():
    """Приложение водителя берёт те же заказы, что «Развоз»: окно понедельника после пятницы-праздника — с четверга."""
    from courier import routes_link as rl
    thu, fri, mon = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)
    view = rl.RoutesView(workdays=(1, 2, 3, 4, 5), holidays=frozenset({fri}))
    since, until = rl.orders_window(mon, view)
    assert since == dp.backlog_since(thu, (1, 2, 3, 4, 5), holidays={fri}) and until == mon
    assert rl.orders_window(mon, rl.RoutesView(workdays=(1, 2, 3, 4, 5)))[0] == dp.backlog_since(fri, (1, 2, 3, 4, 5))
    picked = rl.pick_orders([_dorder(1, 101, 10.0, day=thu, car='C1'), _dorder(2, 102, 10.0, day=fri, car='C1')],
                            mon, view, 'C1')
    assert {o.isn for o in picked} == {_dorder(1, 101, 10.0).isn, _dorder(2, 102, 10.0).isn}   # чт и пт-праздник

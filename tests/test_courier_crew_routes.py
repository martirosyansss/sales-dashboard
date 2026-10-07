# -*- coding: utf-8 -*-
"""Экипаж машины из «Առաքիչ» — в «Маршруты» (ответ владельца №84): вход водителя по PIN — водитель машины терминала,
решение экипажа — её առաքիչ («один» — только на этот день), свежее событие главнее (вход новее записи логиста — его,
правка логиста после входа — снова логиста), один человек — одна машина, сбой «Маршрутов» вход не ломает
(courier/api.py → routes_link.record_crew → route_optimizer Store.save_apk_crew).

Синтетические данные, без ERP; courier.db и база «Маршрутов» — временные.
Запуск из корня проекта:  python -m pytest tests/test_courier_crew_routes.py -q
"""
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import store as rst  # noqa: E402
from test_courier import NOW, _fresh_ref_cache, _pin_env, app, client, make_terminal, now, st  # noqa: E402,F401

DAY = NOW.date().isoformat()                       # 2026-10-02 — рабочий день входа (часы тестов)
NEXT = (NOW.date() + timedelta(days=1)).isoformat()
API = '/api/courier/v1'


@pytest.fixture
def rs(app, tmp_path):
    """«Маршруты» с машинами TEST (терминал) и CAR2; OTHER — не машина настроек."""
    s = rst.Store(str(tmp_path / 'routes.db'))
    s.load()
    with closing(sqlite3.connect(s.path)) as conn:
        conn.executemany('INSERT INTO trucks(car_code, updated_at) VALUES(?, ?)', [('TEST', 'x'), ('CAR2', 'x')])
        conn.commit()
    app.extensions['route_optimizer'] = SimpleNamespace(store=s)
    app.extensions['courier'].crew_plan.clear()
    return s


def rows(s, table='truck_driver'):
    with closing(sqlite3.connect(s.path)) as conn:
        return conn.execute(f'SELECT car_code, from_day, only_day, name, updated_by, updated_at FROM {table} '
                            'ORDER BY car_code, from_day, only_day').fetchall()


def crew(s, day=DAY):
    return s.truck_drivers(day)[0], s.truck_drivers(day, 'helper')[0]


def do_login(client, h, pin='1234'):
    r = client.post(f'{API}/login', json={'pin': pin}, headers=h)
    assert r.status_code == 200, r.get_json()
    return {**h, 'X-Courier-Session': r.get_json()['session']}, r.get_json()


def test_login_writes_driver_once(rs, st, client):
    _, terminal, h = make_terminal(st)
    _, body = do_login(client, h)
    assert crew(rs) == ({'TEST': 'Արամ'}, {}) and crew(rs, NEXT) == ({'TEST': 'Արամ'}, {})
    assert crew(rs, '2026-10-01') == ({}, {})                                       # прошлые дни — как были
    assert [r[:5] for r in rows(rs)] == [('TEST', DAY, 0, 'Արամ', f'apk:{terminal.id}')]
    assert rs.crew_sources(DAY) == {'TEST': 'apk'}
    assert body['planned'] == {'driver': 'Արամ', 'helper': None}                     # экипаж плана — уже с ним
    before = rows(rs)
    do_login(client, h)                                                              # тот же — строки не меняются
    assert rows(rs) == before


def test_login_newer_than_manual_wins_and_manual_after_login_wins(rs, st, client):
    rs.save_truck_crew('TEST', '2026-09-01', {'driver': ('Կարեն', False)}, 'logist')
    rs.save_truck_crew('TEST', DAY, {'driver': ('Լևոն', True)}, 'logist')            # подмена на сегодня — тоже старее
    _, _, h = make_terminal(st)
    do_login(client, h)
    assert crew(rs)[0] == {'TEST': 'Արամ'} and rs.truck_drivers(DAY)[1] == frozenset()
    assert crew(rs, '2026-10-01')[0] == {'TEST': 'Կարեն'}
    # логист после входа поставил другого — его правка главнее (та же строка дня), пометки APK нет
    rs.save_truck_crew('TEST', DAY, {'driver': ('Կարեն', False)}, 'logist')
    assert crew(rs)[0] == {'TEST': 'Կարեն'} and rs.crew_sources(DAY) == {}
    # новый вход позже — снова водитель терминала
    do_login(client, h)
    assert crew(rs)[0] == {'TEST': 'Արամ'} and rs.crew_sources(DAY) == {'TEST': 'apk'}


def test_driver_moving_truck_leaves_old_truck(rs, st, client):
    """Արամ — постоянный водитель CAR2 и առաքիչ TEST по записям логиста: вошёл водителем TEST — CAR2 с сегодня без
    водителя, TEST без առաքիչ (одно место); прошлые дни и чужая подмена дня — как были."""
    rs.save_truck_crew('CAR2', '2026-09-01', {'driver': ('Արամ', False)}, 'logist')
    rs.save_truck_crew('TEST', '2026-09-01', {'driver': ('Կարեն', False), 'helper': ('Արամ', False)}, 'logist')
    rs.save_truck_crew('CAR2', NEXT, {'driver': ('Գոռ', True)}, 'logist')             # подмена завтра — не трогается
    _, _, h = make_terminal(st)
    do_login(client, h)
    assert crew(rs) == ({'TEST': 'Արամ'}, {})
    assert crew(rs, '2026-10-01') == ({'CAR2': 'Արամ', 'TEST': 'Կարեն'}, {'TEST': 'Արամ'})
    assert crew(rs, NEXT) == ({'CAR2': 'Գոռ', 'TEST': 'Արամ'}, {})


def test_helper_confirm_and_alone_only_that_day(rs, st, client):
    rs.save_truck_crew('TEST', '2026-09-01', {'helper': ('Գուրգեն', False)}, 'logist')
    _, _, h = make_terminal(st)
    hid = st.store.save_driver(None, 'Բաբկեն', True, '5678', 'admin')
    s, _ = do_login(client, h)
    r = client.post(f'{API}/crew', json={'alone': True}, headers=s)
    assert r.status_code == 200, r.get_json()
    assert crew(rs)[1] == {} and rs.truck_drivers(DAY, 'helper')[1] == frozenset({'TEST'})   # «никого» — на день
    assert crew(rs, NEXT)[1] == {'TEST': 'Գուրգեն'}                                  # обычный առաքիչ завтра — тот же
    r = client.post(f'{API}/crew', json={'helper_pin': '5678'}, headers=s)
    assert r.status_code == 200 and r.get_json()['helper'] == {'id': hid, 'name': 'Բաբկեն'}
    assert crew(rs)[1] == crew(rs, NEXT)[1] == {'TEST': 'Բաբկեն'}                   # подтвердил — с сегодня
    assert rs.truck_drivers(DAY, 'helper')[1] == frozenset() and rs.crew_sources(DAY, 'helper') == {'TEST': 'apk'}
    before = rows(rs, 'truck_helper')
    assert client.post(f'{API}/crew', json={'helper_pin': '5678'}, headers=s).status_code == 200
    assert rows(rs, 'truck_helper') == before                                        # тот же — строки не меняются


def test_alone_without_usual_helper_writes_nothing(rs, st, client):
    _, _, h = make_terminal(st)
    s, _ = do_login(client, h)
    assert client.post(f'{API}/crew', json={'alone': True}, headers=s).status_code == 200
    assert rows(rs, 'truck_helper') == []


def test_not_a_routes_truck_is_noop(rs, st, client):
    _, _, h = make_terminal(st, car='OTHER')
    do_login(client, h)
    assert rows(rs) == [] and rows(rs, 'truck_helper') == []


def test_routes_failure_does_not_break_login(rs, st, client, monkeypatch, caplog):
    def broken(*a, **k):
        raise rst.StoreError('битая база')
    monkeypatch.setattr(rs, 'save_apk_crew', broken)
    _, _, h = make_terminal(st)
    with caplog.at_level(logging.WARNING):
        s, body = do_login(client, h)
    assert body['driver']['name'] == 'Արամ' and 'не передан в «Маршруты»' in caplog.text
    assert client.post(f'{API}/crew', json={'alone': True}, headers=s).status_code == 200


def test_no_routes_section_login_works(app, st, client):
    app.extensions.pop('route_optimizer', None)
    _, _, h = make_terminal(st)
    do_login(client, h)


def test_bad_courier_name_is_skipped(rs, st, client):
    _, _, h = make_terminal(st, name='Արամ​')                                 # невидимый знак — не в накладную
    do_login(client, h)
    assert rows(rs) == []


def test_save_apk_crew_rejects_non_apk_label(rs):
    with pytest.raises(ValueError):
        rs.save_apk_crew('TEST', DAY, 'driver', 'Արամ', False, 'logist')


# ============================== подсказка имени в офисе (ревью 83478e6) ==============================

def test_office_driver_list_has_routes_name_hints(app, st, client, rs, monkeypatch):
    """Имена водителей «Развоза» — ERP (кэш списка «Վարորդ») и свои за 90 дней — подсказкой в /courier#drivers; ERP здесь
    не ждём: кэша нет — пусто сразу, список перечитывается в фоне."""
    import threading
    from route_optimizer import views as rviews
    monkeypatch.setattr(rviews, '_clock', lambda: rviews.datetime(2026, 10, 2, 9, 0))
    rs.save_truck_crew('CAR2', '2026-09-20', {'driver': ('Կարեն', False)}, 'logist')
    loaded = threading.Event()

    def loader(since, until):
        loaded.set()
        return ['Վարդանյան Գարիկ']
    routes = SimpleNamespace(store=rs, driver_list_cache=None, driver_list_loader=loader, driver_list_lock=threading.Lock())
    app.extensions['route_optimizer'] = routes
    assert client.get('/api/courier/admin/drivers').get_json()['name_hints'] == ['Կարեն']
    assert loaded.wait(5)
    for _ in range(50):                                   # фоновый поток записывает кэш после загрузчика
        if routes.driver_list_cache is not None:
            break
        loaded.wait(0.05)
    assert client.get('/api/courier/admin/drivers').get_json()['name_hints'] == ['Կարեն', 'Վարդանյան Գարիկ']
    app.extensions['route_optimizer'] = SimpleNamespace(store=rs)          # не «Маршруты» — без подсказки, не 500
    r = client.get('/api/courier/admin/drivers')
    assert r.status_code == 200 and r.get_json()['name_hints'] == []
    app.extensions.pop('route_optimizer')
    assert client.get('/api/courier/admin/drivers').get_json()['name_hints'] == []

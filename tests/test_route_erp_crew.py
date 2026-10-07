# -*- coding: utf-8 -*-
"""Экипаж машин по ERP (ответ владельца №84): подбор waybill.pick_car_crews (кто больше дней возил машину за 30 дней —
Վարորդ, больше всех общих с ним дней — Առաքիչ; один человек — одна машина), запись store.fill_erp_crew — только машинам
без записей логиста и «Առաքիչ», и подбор при открытии дня «Развоза» (views._erp_crew). Синтетические данные (по форме —
как в ERP 07.10.2026), без ERP; база «Маршрутов» — временная.

Запуск из корня проекта:  python -m pytest tests/test_route_erp_crew.py -q
"""
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer import waybill as wb  # noqa: E402
from route_optimizer.erp import ErpError, check_sql  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401

D0 = date(2026, 9, 7)
TODAY = '2026-09-30'


def _days(car, name, first, n, step=1):
    """(машина, имя, день) — n дней с first через step."""
    return [(car, name, first + timedelta(days=i * step)) for i in range(n)]


# ============================== подбор (чистая функция) ==============================

def real_shape():
    """По форме ERP за 30 дней до 07.10.2026 (см. отчёт №84)."""
    rows = []
    rows += _days('333NO33', 'Վարդանյան Գարիկ', D0, 25)
    rows += _days('333NO33', 'Սեփյան Արմեն', D0 + timedelta(days=3), 2)                  # 2 общих дня — не առաքիչ
    rows += _days('2660062', 'Համբարձումյան Աղվան', D0, 24)
    rows += _days('504 CQ 61', 'Թովմասյան Հարություն', D0, 18)                           # в ERP — с пробелами
    rows += _days('504 CQ 61', 'Հայրապետյան Արծրուն', date(2026, 9, 24), 3, 4)          # замена: 1 общий день
    rows += _days('333DO33', 'Համբարձումյան Արման', D0, 20)
    rows += _days('333DO33', 'Հակոբյան Կարապետ', D0, 18)
    rows += _days('333DO33', 'Հարությունյան Արմեն', date(2026, 10, 1), 5)
    rows += _days('333DO33', 'Սարգսյան Աշոտ', date(2026, 10, 1), 5)
    rows += _days('991AT61', 'Իսկանդարյան Կորյուն', D0, 24)
    rows += _days('991AT61', 'Ավակիմյան Արթուր', D0, 20)
    rows += _days('991AT61', 'Հարությունյան Արմեն', D0, 14)
    rows += _days('991AT61', 'Հարությունյան Եգոր', D0 + timedelta(days=20), 4)
    return rows


TRUCKS = ['333NO33', '2660062', '504CQ61', '333DO33', '991AT61', '123AV61', '306CU61']


def test_pick_real_shape():
    assert wb.pick_car_crews(real_shape(), TRUCKS) == {
        '333NO33': ('Վարդանյան Գարիկ', None),
        '2660062': ('Համբարձումյան Աղվան', None),
        '504CQ61': ('Թովմասյան Հարություն', None),                     # замена с 1 общим днём — не առաքիչ
        '333DO33': ('Համբարձումյան Արման', 'Հակոբյան Կարապետ'),
        '991AT61': ('Իսկանդարյան Կորյուն', 'Ավակիմյան Արթուր'),         # третий и дальше — нет
    }


def test_pick_conflict_stays_where_last_day_is_latest():
    """Հարությունյան Արմեն — первый на обеих машинах: остаётся на 333DO33 (его последний день там позже), 991AT61 берёт
    следующего; её առաքիչ — заново, по общим дням с новым водителем."""
    rows = _days('991AT61', 'Հարությունյան Արմեն', D0, 17) + _days('991AT61', 'Իսկանդարյան Կորյուն', D0, 15) \
        + _days('991AT61', 'Ավակիմյան Արթուր', D0 + timedelta(days=5), 10) \
        + _days('333DO33', 'Հարությունյան Արմեն', D0 + timedelta(days=17), 6) \
        + _days('333DO33', 'Սարգսյան Աշոտ', D0 + timedelta(days=18), 5)
    assert wb.pick_car_crews(rows, TRUCKS) == {'333DO33': ('Հարությունյան Արմեն', 'Սարգսյան Աշոտ'),
                                               '991AT61': ('Իսկանդարյան Կորյուն', 'Ավակիմյան Արթուր')}


def test_pick_conflict_helper_vs_driver_and_tie_on_last_day():
    """Один человек — одно место и в разных ролях: առաքիչ одной машины и водитель другой с тем же последним днём —
    остаётся там, где дней больше."""
    rows = _days('A', 'Ա', D0, 10) + _days('A', 'Բ', D0, 10) + _days('B', 'Բ', D0 + timedelta(days=5), 5) \
        + _days('B', 'Գ', D0 + timedelta(days=6), 4)
    assert wb.pick_car_crews(rows, ['A', 'B']) == {'A': ('Ա', 'Բ'), 'B': ('Գ', None)}


def test_pick_ties_and_helper_threshold():
    # дней поровну — позже последний день; и он тот же — по имени
    rows = _days('A', 'Բ', D0, 5) + _days('A', 'Ա', D0 + timedelta(days=1), 5)
    assert wb.pick_car_crews(rows, ['A']) == {'A': ('Ա', 'Բ')}
    rows = _days('A', 'Բ', D0, 5) + _days('A', 'Ա', D0, 5)
    assert wb.pick_car_crews(rows, ['A']) == {'A': ('Ա', 'Բ')}
    # առաքիչ — от CREW_HELPER_MIN_DAYS общих дней
    assert wb.CREW_HELPER_MIN_DAYS == 3
    rows = _days('A', 'Ա', D0, 6) + _days('A', 'Բ', D0 + timedelta(days=3), 3)
    assert wb.pick_car_crews(rows, ['A']) == {'A': ('Ա', 'Բ')}
    rows = _days('A', 'Ա', D0, 6) + _days('A', 'Բ', D0 + timedelta(days=4), 3)          # общих 2
    assert wb.pick_car_crews(rows, ['A']) == {'A': ('Ա', None)}


def test_pick_trucks_filter_and_taken_names():
    rows = real_shape()
    got = wb.pick_car_crews(rows, ['333DO33', '504CQ61'])
    assert set(got) == {'333DO33', '504CQ61'}                                        # не машины настроек — нет
    got = wb.pick_car_crews(rows, ['333DO33', '991AT61'], {'Համբարձումյան Արման', 'Ավակիմյան Արթուր'})
    # Արման и Արթուր уже на машинах по записям логиста: водитель 333DO33 — Կարապետ (с ним никто не возил 3 дня),
    # առաքիչ 991AT61 — следующий по общим дням с Կորյուն
    assert got == {'333DO33': ('Հակոբյան Կարապետ', None),
                   '991AT61': ('Իսկանդարյան Կորյուն', 'Հարությունյան Արմեն')}
    assert wb.pick_car_crews([], TRUCKS) == {} and wb.pick_car_crews(rows, []) == {}


def test_crew_days_sql_is_select_only():
    check_sql(wb.SQL_CAR_CREW_DAYS)
    assert 'fSTATE = 2' in wb.SQL_CAR_CREW_DAYS and 'fCLOSED' in wb.SQL_CAR_CREW_DAYS


# ============================== запись по ERP (store.fill_erp_crew) ==============================

@pytest.fixture
def rs(tmp_path):
    s = st.Store(str(tmp_path / 'routes.db'))
    s.load()
    with closing(sqlite3.connect(s.path)) as conn:
        conn.executemany('INSERT INTO trucks(car_code, updated_at) VALUES(?, ?)', [(c, 'x') for c in ('A', 'B', 'C')])
        conn.commit()
    return s


def rows_of(s, table='truck_driver'):
    with closing(sqlite3.connect(s.path)) as conn:
        return conn.execute(f'SELECT car_code, from_day, only_day, name, updated_by FROM {table} '
                            'ORDER BY car_code, from_day, only_day').fetchall()


def crew_of(s, day=TODAY):
    return s.truck_drivers(day)[0], s.truck_drivers(day, 'helper')[0]


def test_fill_empty_trucks_only(rs):
    seen = []

    def choose(taken):
        seen.append(taken)
        return {'A': ('Ա', 'Բ'), 'B': ('Գ', None), 'X': ('Դ', None)}   # X — не машина настроек
    assert rs.fill_erp_crew(TODAY, choose) == [('A', 'driver', 'Ա'), ('A', 'helper', 'Բ'), ('B', 'driver', 'Գ')]
    assert seen == [frozenset()]
    assert crew_of(rs) == ({'A': 'Ա', 'B': 'Գ'}, {'A': 'Բ'})
    assert rows_of(rs) == [('A', TODAY, 0, 'Ա', st.CREW_BY_ERP), ('B', TODAY, 0, 'Գ', st.CREW_BY_ERP)]
    assert rs.crew_sources(TODAY) == {'A': 'erp', 'B': 'erp'} and rs.crew_sources('2026-09-29') == {}
    # то же — ничего не пишется
    assert rs.fill_erp_crew(TODAY, choose) == [] and rs.fill_erp_crew('2026-10-01', choose) == []
    assert len(rows_of(rs)) == 2 and len(rows_of(rs, 'truck_helper')) == 1


def test_fill_never_overwrites_manual_or_apk(rs):
    rs.save_truck_crew('A', '2026-09-01', {'driver': ('Լոգիստ', False)}, 'logist')      # только водитель — вся машина его
    rs.save_truck_crew('B', '2026-09-01', {'driver': ('', False)}, 'logist')            # «никого» — тоже запись
    rs.save_apk_crew('C', '2026-09-29', 'driver', 'Ապկ', False, 'apk:7')
    rs.save_truck_crew('C', TODAY, {'helper': ('Փոխ', True)}, 'logist')                # подмена дня — тоже занят
    seen = []

    def choose(taken):
        seen.append(taken)
        return {'A': ('Ա', 'Բ'), 'B': ('Գ', None), 'C': ('Դ', 'Ե')}
    assert rs.fill_erp_crew(TODAY, choose) == []
    assert seen == [frozenset({'Լոգիստ', 'Ապկ', 'Փոխ'})]
    assert crew_of(rs) == ({'A': 'Լոգիստ', 'C': 'Ապկ'}, {'C': 'Փոխ'})


def test_fill_replaces_own_row_when_pick_changes_and_keeps_when_drained(rs):
    assert rs.fill_erp_crew('2026-09-20', lambda taken: {'A': ('Ա', 'Բ')})
    assert rs.fill_erp_crew(TODAY, lambda taken: {'A': ('Գ', None)}) == [('A', 'driver', 'Գ'), ('A', 'helper', '')]
    assert crew_of(rs) == ({'A': 'Գ'}, {}) and crew_of(rs, '2026-09-25') == ({'A': 'Ա'}, {'A': 'Բ'})   # прошлое — как было
    # ERP «опустела» (с 03.10 в накладных нет машины) — прежнее остаётся
    assert rs.fill_erp_crew('2026-10-05', lambda taken: {}) == []
    assert crew_of(rs, '2026-10-05') == ({'A': 'Գ'}, {})
    # человек прежней записи ERP подобран на другую машину — с прежней снимается (одно место)
    assert rs.fill_erp_crew('2026-10-06', lambda taken: {'B': ('Գ', None)}) == [('A', 'driver', ''), ('B', 'driver', 'Գ')]
    assert crew_of(rs, '2026-10-06') == ({'B': 'Գ'}, {})
    # логист поправил запись ERP — дальше она его
    rs.save_truck_crew('B', '2026-10-07', {'driver': ('Լոգիստ', False)}, 'logist')
    assert rs.fill_erp_crew('2026-10-07', lambda taken: {'B': ('Ե', None)}) == []
    assert crew_of(rs, '2026-10-07') == ({'B': 'Լոգիստ'}, {}) and rs.crew_sources('2026-10-07') == {'A': 'erp'}


# ============================== открытие дня «Развоза» ==============================

@pytest.fixture
def erp_day(client, monkeypatch):
    """День 01.10 с CAR1 и CAR2; сейчас 30.09 18:00; ERP: CAR1 — Գարիկ с Արթուր, CAR2 — Աղվան."""
    monkeypatch.setattr(views, '_clock', lambda: views.datetime(2026, 9, 30, 18, 0))
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    calls = []

    def loader(since, until):
        calls.append((since, until))
        if state.erp_fail:
            raise ErpError('нет связи')
        return _days('CAR1', 'Վարդանյան Գարիկ', D0, 20) + _days('CAR1', 'Ավակիմյան Արթուր', D0, 18) \
            + _days('CAR2', 'Համբարձումյան Աղվան', D0, 15)
    state.erp_fail = False
    state.crew_days_loader = loader
    state.calls = calls
    return state


def test_dispatch_get_fills_empty_trucks(client, erp_day):
    page = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert erp_day.calls == [(date(2026, 8, 31), date(2026, 10, 1))]                   # 30 дней и сегодня
    assert page['drivers'] == {'CAR1': 'Վարդանյան Գարիկ', 'CAR2': 'Համբարձումյան Աղվան'}
    assert page['helpers'] == {'CAR1': 'Ավակիմյան Արթուր'}
    assert page['crew']['drivers'] == [{'name': 'Համբարձումյան Աղվան', 'trucks': ['CAR2'], 'absent': False, 'source': 'erp'},
                                       {'name': 'Վարդանյան Գարիկ', 'trucks': ['CAR1'], 'absent': False, 'source': 'erp'}]
    client.get('/api/routes/dispatch?date=2026-10-01')
    assert len(erp_day.calls) == 1                                                     # не чаще раза в час
    # водителя по ERP можно отметить «չի եկել» (№77)
    r = client.post('/api/routes/dispatch/absence', json={'date': '2026-10-01', 'name': 'Վարդանյան Գարիկ', 'absent': True})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['crew']['drivers'][1] == {'name': 'Վարդանյան Գարիկ', 'trucks': ['CAR1'], 'absent': True,
                                                  'until': '2026-10-01', 'source': 'erp'}
    # правка логиста в «Վարորդ» — запись его: пометки нет, следующий подбор её не трогает
    r = client.post('/api/routes/dispatch/driver', json={'date': '2026-10-01', 'car_code': 'CAR1', 'name': 'Լոգիստ',
                                                         'helper': 'Ավակիմյան Արթուր'})
    assert r.status_code == 200, r.get_json()
    assert [(x['name'], x.get('source')) for x in r.get_json()['crew']['drivers']] ==         [('Լոգիստ', None), ('Համբարձումյան Աղվան', 'erp')]
    assert len(erp_day.calls) == 1                                                     # POST ERP не спрашивает
    erp_day.erp_crew_next = 0.0
    page = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert len(erp_day.calls) == 2 and page['drivers'] == {'CAR1': 'Լոգիստ', 'CAR2': 'Համբարձումյան Աղվան'}


def test_dispatch_get_erp_down_still_200(client, erp_day, monkeypatch):
    erp_day.erp_fail = True
    tick = {'t': 1000.0}
    monkeypatch.setattr(views.time, 'monotonic', lambda: tick['t'])
    r = client.get('/api/routes/dispatch?date=2026-10-01')
    assert r.status_code == 200 and r.get_json()['drivers'] == {} and r.get_json()['crew']['drivers'] == []
    client.get('/api/routes/dispatch?date=2026-10-01')
    assert len(erp_day.calls) == 1
    tick['t'] += views.ERP_CREW_RETRY_S                                                # повтор через минуту
    erp_day.erp_fail = False
    assert client.get('/api/routes/dispatch?date=2026-10-01').get_json()['drivers']['CAR2'] == 'Համբարձումյան Աղվան'
    assert len(erp_day.calls) == 2


def test_dispatch_store_failure_in_fill_still_200(client, erp_day, monkeypatch):
    def broken(*a, **k):
        raise st.StoreError('битая база')
    monkeypatch.setattr(erp_day.store, 'fill_erp_crew', broken)
    r = client.get('/api/routes/dispatch?date=2026-10-01')
    assert r.status_code == 200 and r.get_json()['drivers'] == {}


def test_no_loader_no_fill(client, erp_day):
    erp_day.crew_days_loader = None
    assert client.get('/api/routes/dispatch?date=2026-10-01').get_json()['drivers'] == {}


# ============================== ревью 83478e6 (probe84) ==============================

def test_fill_clears_erp_row_when_logist_put_person_elsewhere(rs):
    """ERP поставила X на A, логист постоянно перевёл X на B: следующий подбор снимает X с A (иначе он на двух машинах и
    №77 считал бы лишнего водителя). Подмена дня — не повод: её разводит рассадка №77."""
    rs.fill_erp_crew('2026-10-01', lambda taken: {'A': ('X', None)})
    rs.save_truck_crew('B', '2026-10-02', {'driver': ('X', False)}, 'logist')
    choose = lambda taken: {} if 'X' in taken else {'A': ('X', None)}             # noqa: E731
    assert rs.fill_erp_crew('2026-10-02', choose) == [('A', 'driver', '')]
    assert crew_of(rs, '2026-10-02')[0] == {'B': 'X'} and crew_of(rs, '2026-10-01')[0] == {'A': 'X'}
    assert rs.fill_erp_crew('2026-10-02', choose) == []
    rs.fill_erp_crew('2026-10-03', lambda taken: {'C': ('Y', None)})
    rs.save_truck_crew('B', '2026-10-04', {'driver': ('Y', True)}, 'logist')          # только на день
    assert rs.fill_erp_crew('2026-10-04', lambda taken: {}) == []
    assert crew_of(rs, '2026-10-04')[0] == {'B': 'Y', 'C': 'Y'}


def test_apk_login_keeps_logist_planned_move(rs):
    """Логист запланировал Y на A с завтра (постоянно или подменой на день), а сегодня Y вошёл на B: до перехода он на B,
    в день перехода — только на A (у B — «никого» той же записью)."""
    rs.save_truck_crew('A', '2026-10-05', {'driver': ('Y', False)}, 'logist')
    assert rs.save_apk_crew('B', '2026-10-02', 'driver', 'Y', False, 'apk:1')
    assert crew_of(rs, '2026-10-04')[0] == {'B': 'Y'} and crew_of(rs, '2026-10-05')[0] == {'A': 'Y'}
    assert crew_of(rs, '2026-10-09')[0] == {'A': 'Y'}
    assert ('B', '2026-10-05', 0, '', 'apk:1') in rows_of(rs)
    assert not rs.save_apk_crew('B', '2026-10-02', 'driver', 'Y', False, 'apk:1')     # повторный вход — без строк


def test_apk_login_keeps_logist_planned_substitute(rs):
    rs.save_truck_crew('A', '2026-10-03', {'helper': ('Y', True)}, 'logist')           # առաքիչ A только 03.10
    rs.save_apk_crew('B', '2026-10-02', 'driver', 'Y', False, 'apk:1')
    assert crew_of(rs, '2026-10-03') == ({}, {'A': 'Y'})
    assert crew_of(rs, '2026-10-04') == ({'B': 'Y'}, {})                               # после подмены — снова на B
    assert ('B', '2026-10-03', 1, '', 'apk:1') in rows_of(rs)


def test_apk_login_planned_move_leaves_other_people_rows(rs):
    """У B на день перехода уже другой (логист поставил Z с 04.10) или своя запись того же ключа — не трогается."""
    rs.save_truck_crew('A', '2026-10-05', {'driver': ('Y', False)}, 'logist')
    rs.save_truck_crew('B', '2026-10-04', {'driver': ('Z', False)}, 'logist')
    rs.save_apk_crew('B', '2026-10-02', 'driver', 'Y', False, 'apk:1')
    assert crew_of(rs, '2026-10-03')[0] == {'B': 'Y'} and crew_of(rs, '2026-10-05')[0] == {'A': 'Y', 'B': 'Z'}
    # переход на ту же машину позже, чем вход на неё, — запланированная смена логиста: остаётся (probe 3)
    rs.save_truck_crew('C', '2026-10-05', {'driver': ('X', False)}, 'logist')
    rs.save_apk_crew('C', '2026-10-02', 'driver', 'W', False, 'apk:2')
    assert crew_of(rs, '2026-10-04')[0]['C'] == 'W' and crew_of(rs, '2026-10-05')[0]['C'] == 'X'


def test_probe_edge_cases_documented(rs):
    # вход переводит X с машины ERP: у A — «никого» от «Առաքիչ», ERP её больше не заполняет (запись есть)
    rs.fill_erp_crew('2026-10-01', lambda taken: {'A': ('X', None)})
    rs.save_apk_crew('B', '2026-10-02', 'driver', 'X', False, 'apk:1')
    assert rs.fill_erp_crew('2026-10-03', lambda taken: {'A': ('Z', None)}) == []
    assert crew_of(rs, '2026-10-03')[0] == {'B': 'X'}
    # «один» при подмене логиста на сегодня (постоянного нет) — подмена снимается: сегодня без առաքիչ
    rs.save_truck_crew('C', '2026-10-02', {'helper': ('H', True)}, 'logist')
    assert rs.save_apk_crew('C', '2026-10-02', 'helper', '', True, 'apk:3')
    assert rs.truck_drivers('2026-10-02', 'helper') == ({}, frozenset())


def test_probe_logist_substitute_only_car_gets_erp_permanent(tmp_path):
    """У машины только подмена логиста на день (постоянной записи нет) — ERP пишет постоянную: в этот день — подмена."""
    s = st.Store(str(tmp_path / 'r.db'))
    s.load()
    with closing(sqlite3.connect(s.path)) as conn:
        conn.execute("INSERT INTO trucks(car_code, updated_at) VALUES('A', 'x')")
        conn.commit()
    s.save_truck_crew('A', '2026-10-02', {'driver': ('S', True)}, 'logist')
    assert s.fill_erp_crew('2026-10-02', lambda taken: {'A': ('E', None)}) == [('A', 'driver', 'E')]
    assert crew_of(s, '2026-10-02')[0] == {'A': 'S'} and crew_of(s, '2026-10-03')[0] == {'A': 'E'}


def test_fill_clears_fixed_person_on_locked_car_erp_row_only(rs):
    """Водитель A — ERP (X), առաքիչ A — логиста (машина «закрыта» для подбора); X постоянно на B у логиста: с A он
    снимается (только его запись ERP), запись логиста на A остаётся (probe84b c)."""
    rs.fill_erp_crew('2026-10-01', lambda taken: {'A': ('X', None)})
    rs.save_truck_crew('A', '2026-10-02', {'helper': ('H', False)}, 'logist')
    rs.save_truck_crew('B', '2026-10-02', {'driver': ('X', False)}, 'logist')
    assert rs.fill_erp_crew('2026-10-02', lambda taken: {}) == [('A', 'driver', '')]
    assert crew_of(rs, '2026-10-02') == ({'B': 'X'}, {'A': 'H'})
    assert rs.fill_erp_crew('2026-10-02', lambda taken: {}) == []


def test_driver_name_hints_cache_on_any_failure_and_thread_start_failure(monkeypatch):
    """Сбой загрузчика не-ErpError — кэш всё равно пишется (повтор через DRIVER_LIST_RETRY_S, а не на каждый запрос);
    поток не запустился — замок свободен."""
    import threading
    from types import SimpleNamespace
    calls = []

    def loader(since, until):
        calls.append(1)
        raise RuntimeError('сбой')
    state = SimpleNamespace(store=SimpleNamespace(driver_names=lambda since: ['Ա']), driver_list_cache=None,
                            driver_list_loader=loader, driver_list_lock=threading.Lock())
    assert views.driver_name_hints(state) == ['Ա']
    for _ in range(100):
        if state.driver_list_cache is not None and not state.driver_list_lock.locked():
            break
        threading.Event().wait(0.02)
    expires, names = state.driver_list_cache
    assert names == [] and calls == [1] and not state.driver_list_lock.locked()
    assert expires - views.time.monotonic() <= views.DRIVER_LIST_RETRY_S
    views.driver_name_hints(state)
    assert calls == [1]                                                               # повтор — не сразу

    class NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(views.threading, 'Thread', NoThread)
    state.driver_list_cache = None
    assert views.driver_name_hints(state) == ['Ա'] and not state.driver_list_lock.locked()

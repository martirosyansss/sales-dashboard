# -*- coding: utf-8 -*-
"""«Մինչև ժամը» в строке магазина «Развоза» (владелец 08.10): привезти магазин не позже HH:MM — только в этот день или
всегда. Программа сама переставляет магазины рейса, чтобы машина успела; не успеть в этом рейсе — красная пометка
(window_miss) и подсказка машины, которая успела бы; закреплённый логистом рейс не переставляется — только пометка.

- хранение: срок дня — store.customer_day_until (схема 25, миграция 24 → 25 — только новая таблица), «Միշտ» — окно
  вида before (store.customer_window); окно дня = срок дня, если есть, иначе постоянное (Bundle.windows_on);
- dispatch.fit_until / _fit_windows / _until_hint и перенос по подсказке (move с "fit");
- правка «Развоза» {"action": "until", "customer_id", "time", "scope"}: проверки, day / always, снять, прошедший день,
  чужая вкладка, закреплённый рейс, утверждённый и отправленный план, доступ ролей;
- страница: кнопка в строке магазина, диалог dpUntilDlg, плашка срока дня.

Синтетические данные, без ERP и без карты дорог (км по прямой).  Запуск:  python -m pytest tests/test_route_dispatch_until.py -q
"""
import math
import random
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from test_route_optimizer import (DP_DEPOT, FORD, HOWO, _dispatch_setup, _dorder, _dp_ctx, _dp_stops, _info,  # noqa: E402,F401
                                  _no_road_map, client)

INF = math.inf
D1, D2 = date(2026, 10, 1), date(2026, 10, 2)
DAY = D1.isoformat()
LAT, LON = DP_DEPOT
WEST = [(101 + i, (LAT + 0.004 * i, LON - 0.05)) for i in range(4)]
EAST = [(201 + i, (LAT + 0.004 * i, LON + 0.05)) for i in range(3)]


# ============================== хранение ==============================

def _put(s, day, cid, t1, user='qa'):
    s._transaction(lambda conn: st.Store._write_customer_day_until(conn, day, cid, t1, user), 'test')


def test_store_day_until_roundtrip_and_effective_windows(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    s.save_customer_window(101, st.CustomerWindow('between', 600, 720), 'qa')
    s.save_customer_window(102, st.CustomerWindow('before', 900), 'qa')
    s.save_customer_window(104, st.CustomerWindow('at', 660, tol=15), 'qa')
    assert s.load().day_until == {}
    _put(s, D1, 101, 700)
    _put(s, D1, 103, 660)
    _put(s, D1, 104, 700)
    _put(s, D2, 101, 540)
    _put(s, D2, 102, 960)
    _put(s, D1, 101, 690, 'logist')                                        # тот же день и магазин — заменяет
    b = s.load()
    assert b.day_until == {D1: {101: 690, 103: 660, 104: 700}, D2: {101: 540, 102: 960}}
    assert 101 in b.windows and 104 in b.windows and 103 not in b.windows
    # день с ними — срок дня: конец окна; начало постоянного окна остаётся («10:00–12:00» + 11:30 → 10:00–11:30,
    # «11:00 ±15» + 11:40 → 10:45–11:40); окно «до» — заменяется (срок дня — решение дня: и позже, и раньше)
    assert b.windows_on(D1) == {101: st.CustomerWindow('between', 600, 690), 102: st.CustomerWindow('before', 900),
                                103: st.CustomerWindow('before', 660), 104: st.CustomerWindow('between', 645, 700)}
    assert b.windows_on(D2)[102] == st.CustomerWindow('before', 960)
    # срок не позже начала (окно сменили уже после срока) — решение дня: «до срока»
    assert b.windows_on(D2)[101] == st.CustomerWindow('before', 540)
    assert b.windows_on(date(2026, 10, 3)) is b.windows
    with closing(sqlite3.connect(s.path)) as conn:
        assert conn.execute("SELECT updated_by FROM customer_day_until WHERE day = '2026-10-01' AND customer_id = 101"
                            ).fetchone() == ('logist',)
    _put(s, D1, 101, None)                                                 # снять — снова постоянное окно
    assert s.load().windows_on(D1)[101] == st.CustomerWindow('between', 600, 720)


@pytest.mark.parametrize('window, floor', [(None, None), (st.CustomerWindow('before', 600), None),
                                           (st.CustomerWindow('after', 600), 600),
                                           (st.CustomerWindow('between', 600, 720), 600),
                                           (st.CustomerWindow('at', 660, tol=15), 645),
                                           (st.CustomerWindow('at', 10, tol=30), None),
                                           (st.CustomerWindow('after', 0), None)])
def test_until_floor(window, floor):
    assert st.until_floor(window) == floor


def test_store_prunes_old_day_deadlines(tmp_path):
    """Сроки дней, чьих планов уже нет (DISPATCH_KEPT_DAYS), удаляются при записи — таблица не растёт без конца."""
    s = st.Store(str(tmp_path / 'r.db'))
    _put(s, date(2026, 5, 1), 101, 600)
    _put(s, date(2026, 6, 3), 101, 600)
    _put(s, D1, 102, 600)                                                  # 01.10 − 120 дней = 03.06
    assert set(s.load().day_until) == {date(2026, 6, 3), D1}


@pytest.mark.parametrize('cid, t1', [(101, 1440), (101, -1), (101, True), (101, 600.5), (101, '600'),
                                     (0, 600), (True, 600), (2 ** 31, 600)])
def test_store_day_until_rejects_bad_input(tmp_path, cid, t1):
    s = st.Store(str(tmp_path / 'r.db'))
    with pytest.raises(ValueError):
        _put(s, D1, cid, t1)
    assert s.load().day_until == {}


def test_store_broken_day_until_row_is_store_error(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    s.load()
    with closing(sqlite3.connect(s.path)) as conn:
        conn.execute("INSERT INTO customer_day_until VALUES('2026-W40-1', 101, 600, 'x', NULL)")
        conn.commit()
    with pytest.raises(st.StoreError, match='Մինչև ժամը'):
        s.load()


def test_migration_24_to_25_only_adds_table(tmp_path):
    path = str(tmp_path / 'r.db')
    s = st.Store(path)
    s.save_customer_window(101, st.CustomerWindow('before', 720), 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE customer_day_until')
        conn.execute("UPDATE meta SET value = '24' WHERE key = 'schema_version'")
        conn.commit()
        before = conn.execute('SELECT * FROM customer_window').fetchall()
    b = st.Store(path).load()
    assert b.day_until == {} and b.windows[101] == st.CustomerWindow('before', 720)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('25',)
        assert conn.execute('SELECT * FROM customer_window').fetchall() == before
        assert conn.execute('SELECT COUNT(*) FROM customer_day_until').fetchone() == (0,)
    assert st.SCHEMA_VERSION == 25 and st._MIGRATIONS[24] == (st._CUSTOMER_DAY_UNTIL_TABLE,)


# ============================== расчёт: перестановка рейса и подсказка ==============================

def _setup(kg_west=200.0, kg_east=200.0):
    stops, orders = _dp_stops([(c, p, kg_west) for c, p in WEST] + [(c, p, kg_east) for c, p in EAST])
    draft = dp.Draft(trucks=sorted([HOWO.car_code, FORD.car_code]), next_id=3, trips=[
        dp.DraftTrip(1, HOWO.car_code, [c for c, _ in WEST]),
        dp.DraftTrip(2, FORD.car_code, [c for c, _ in EAST])])
    return _dp_ctx(), stops, draft, {o.isn for o in orders}


def _before(ctx, **deadlines):
    """Окна «до HH:MM» (ключ — c101 …) поверх контекста дня."""
    return replace(ctx, windows={int(k[1:]): (-INF, float(int(v[:2]) * 60 + int(v[3:]))) for k, v in deadlines.items()})


def _stops_of(view):
    return {s['customer_id']: (tr['id'], s) for t in view['trucks'] for tr in t['trips'] for s in tr['stops']}


def test_reorder_meets_deadline_when_feasible():
    """Последний магазин рейса (≈ 09:45) — «մինչև 09:20»: программа ставит его раньше, и машина успевает."""
    ctx, stops, draft, _ = _setup()
    assert _stops_of(dp.plan_view(ctx, stops, draft, _info, explain=False))[104][1]['eta'] > '09:20'
    ctx = _before(ctx, c104='09:20')
    got = dp.fit_until(ctx, stops, draft, 104)
    assert got == {'late': False, 'reordered': [1], 'kept': [], 'split': False, 'hint': None}
    assert sorted(draft.trips[0].stops) == [101, 102, 103, 104] and draft.trips[1].stops == [201, 202, 203]
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    assert view['summary']['window_miss'] == 0 and _stops_of(view)[104][1]['eta'] <= '09:20'


def test_nothing_to_do_when_already_in_time():
    ctx, stops, draft, _ = _setup()
    ctx = _before(ctx, c104='12:00')
    assert dp.fit_until(ctx, stops, draft, 104) == {'late': False, 'reordered': [], 'kept': [], 'split': False, 'hint': None}
    assert draft.trips[0].stops == [101, 102, 103, 104]


def test_locked_trip_is_not_reordered_only_marked():
    """«Ամրացնել երթը» логистом: порядок прежний, магазин опаздывает (window_miss), подсказки нет — рейс решает логист."""
    ctx, stops, draft, _ = _setup()
    draft.trips[0].pinned = True
    ctx = _before(ctx, c104='09:20')
    assert dp.fit_until(ctx, stops, draft, 104) == {'late': True, 'reordered': [], 'kept': [1], 'split': False, 'hint': None}
    assert draft.trips[0].stops == [101, 102, 103, 104]
    assert _stops_of(dp.plan_view(ctx, stops, draft, _info, explain=False))[104][1]['window_miss'] is True


def test_loaded_and_started_trips_are_kept():
    ctx, stops, draft, _ = _setup()
    ctx = _before(ctx, c104='09:20')
    loaded = dp.Draft.from_json(draft.to_json())
    loaded.approved = {'at': '2026-10-01T07:00:00', 'by': 'qa', 'pinned': [1, 2]}
    for t in loaded.trips:
        t.pinned = True
    loaded.trips[0].loaded = {'at': '2026-10-01T08:00:00', 'by': 'qa', 'pin': False}
    assert dp.fit_until(ctx, stops, loaded, 104)['kept'] == [1] and loaded.trips[0].stops == [101, 102, 103, 104]
    started = dp.Draft.from_json(draft.to_json())                          # сегодня 09:05 — рейс уже грузится
    assert dp.fit_until(ctx, stops, started, 104, now_min=5.0)['kept'] == [1]
    assert started.trips[0].stops == [101, 102, 103, 104]


def test_trip_pinned_by_approval_is_reordered():
    """Утверждение плана закрепляет все рейсы, но это не «Ամրացնել երթը» логиста: рейс переставляется (как правки №73)."""
    ctx, stops, draft, _ = _setup()
    for t in draft.trips:
        t.pinned = True
    draft.approved = {'at': '2026-10-01T07:00:00', 'by': 'qa', 'pinned': [1, 2]}
    got = dp.fit_until(_before(ctx, c104='09:20'), stops, draft, 104)
    assert got['reordered'] == [1] and not got['late'] and draft.trips[0].pinned


def test_infeasible_in_trip_gives_hint_and_move_with_fit_meets_it():
    """Два срока в рейсе HOWO, обоим в нём не успеть: подсказка — FORD (её рейс); перенос по подсказке (move с fit)
    ставит магазин в рейс FORD так, что она успевает; простой перенос (без fit) — в конец рейса, опаздывает."""
    ctx, stops, draft, ids = _setup(kg_west=1500.0, kg_east=100.0)
    ctx = _before(ctx, c101='09:15', c104='09:15')
    got = dp.fit_until(ctx, stops, draft, 104)
    assert got['late'] is True and got['kept'] == [] and draft.trips[0].stops == [101, 102, 103, 104]
    assert got['hint'] == {'truck': FORD.car_code, 'name': 'FORD', 'trip': 2, 'eta': got['hint']['eta']}
    assert got['hint']['eta'] <= '09:15'
    move = {'action': 'move', 'customer_id': 104, 'from_trip': 1, 'to_trip': 2}
    fitted = dp.apply_edit(ctx, stops, dp.Draft.from_json(draft.to_json()), {**move, 'fit': True}, ids)
    view = dp.plan_view(ctx, stops, fitted, _info, explain=False)
    assert view['summary']['window_miss'] == 0 and _stops_of(view)[104][0] == 2
    plain = dp.apply_edit(ctx, stops, dp.Draft.from_json(draft.to_json()), move, ids)
    assert dp.plan_view(ctx, stops, plain, _info, explain=False)['summary']['window_miss'] == 1


def test_impossible_everywhere_no_hint():
    ctx, stops, draft, _ = _setup()
    got = dp.fit_until(_before(ctx, c104='09:01'), stops, draft, 104)
    assert got == {'late': True, 'reordered': [], 'kept': [], 'split': False, 'hint': None}
    assert draft.trips[0].stops == [101, 102, 103, 104]


def _late(ctx, stops, draft):
    """Магазины, к которым машина приезжает позже окна (по виду плана — как видит логист)."""
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    return {s['customer_id'] for t in view['trucks'] for tr in t['trips'] for s in tr['stops'] if s['window_miss']}


def test_fit_never_makes_on_time_store_late():
    """Срок 104 нельзя выполнить, не опоздав к 101 (у неё свой срок): опоздания после — часть прежних, а не другие."""
    ctx, stops, draft, _ = _setup(kg_west=1500.0)
    ctx = _before(ctx, c101='09:15', c104='09:15')
    before = _late(ctx, stops, draft)
    assert before == {104}
    dp.fit_until(ctx, stops, draft, 104)
    assert _late(ctx, stops, draft) <= before


def _random_day(seed):
    """Случайный рейс HOWO (5–9 магазинов) со сроками у 1–3 магазинов около их ETA и сроком раньше ETA у target."""
    rnd = random.Random(seed)
    spec = [(100 + i, (LAT + rnd.uniform(-.06, .06), LON + rnd.uniform(-.06, .06)), 150.0)
            for i in range(rnd.randint(5, 9))]
    stops, orders = _dp_stops(spec)
    order = [c for c, _, _ in spec]
    draft = dp.Draft(trucks=[HOWO.car_code, FORD.car_code], next_id=2, trips=[dp.DraftTrip(1, HOWO.car_code, order[:])])
    ctx = _dp_ctx()
    arrivals = dp._timeline(ctx, draft.trips, {s.customer_id: s for s in stops}, dp._shares(draft.trips))[1][2]
    eta = dict(zip(order, arrivals))
    wins = {c: (-INF, 540 + eta[c] + rnd.uniform(-25, 10)) for c in rnd.sample(order, rnd.randint(1, 3))}
    target = rnd.choice(order[2:])
    wins[target] = (-INF, 540 + eta[target] - rnd.uniform(5, 40))
    return replace(ctx, windows=wins), stops, draft, {o.isn for o in orders}, target


@pytest.mark.parametrize('seed', range(300))
def test_fit_property_late_set_only_shrinks(seed):
    """Свойство (ревью: 27 из 1500 случайных рейсов делали опоздавшим магазин, который успевал): после fit_until
    опоздания — часть прежних; рейс переставлен — значит, сам магазин успевает; ответ late — правда."""
    ctx, stops, draft, _, target = _random_day(seed)
    before = _late(ctx, stops, draft)
    was = list(draft.trips[0].stops)
    got = dp.fit_until(ctx, stops, draft, target)
    after = _late(ctx, stops, draft)
    assert after <= before, (seed, before, after)
    assert got['late'] == (target in after)
    assert (draft.trips[0].stops != was) == bool(got['reordered'])
    if got['reordered']:
        assert target not in after


@pytest.mark.parametrize('seed', range(120))
def test_hint_eta_is_what_the_move_delivers(seed):
    """Подсказка и перенос по ней (move с fit) идут одним путём: прибытие — обещанное, машина — та."""
    ctx, stops, draft, ids, target = _random_day(seed)
    h = dp.fit_until(ctx, stops, draft, target)['hint']
    if h is None:
        return
    edit = {'action': 'move', 'customer_id': target, 'from_trip': 1, 'to_trip': h['trip'],
            'truck': h['truck'] if h['trip'] is None else None, 'fit': True}
    moved = dp.apply_edit(ctx, stops, dp.Draft.from_json(draft.to_json()), edit, ids)
    trip, s = _stops_of(dp.plan_view(ctx, stops, moved, _info, explain=False))[target]
    assert s['eta'] == h['eta'] and s['window_miss'] is False, (seed, h, s['eta'])
    assert next(t.truck for t in moved.trips if t.id == trip) == h['truck']


def test_hint_cases_exist_in_random_days():
    """Проверка выше не пустая: в случайных днях подсказка бывает."""
    hints = 0
    for seed in range(120):
        ctx, stops, draft, _, target = _random_day(seed)
        hints += dp.fit_until(ctx, stops, draft, target)['hint'] is not None
    assert hints >= 10, hints


def test_store_on_time_keeps_manual_order_even_if_others_late():
    """Магазин со сроком и так успевает, опаздывает другой: рейс не переставляется (порядок логиста)."""
    ctx, stops, draft, _ = _setup()
    ctx = _before(ctx, c101='12:00', c104='09:20')
    assert _late(ctx, stops, draft) == {104}
    assert dp.fit_until(ctx, stops, draft, 101) == {'late': False, 'reordered': [], 'kept': [], 'split': False,
                                                    'hint': None}
    assert draft.trips[0].stops == [101, 102, 103, 104]


def test_hint_new_trip_not_before_now():
    """Сегодня в 10:30: новый рейс подсказки — не раньше «сейчас», прибытие не раньше 10:30 (без «сейчас» было бы 09:49);
    перенос по подсказке ставит новому рейсу not_before — прибытие то же; к 11:10 уже никто не успевает к 10:50."""
    west2 = [(111 + i, (LAT + 0.004 * i, LON - 0.06)) for i in range(4)]
    east2 = [(201 + i, (LAT + 0.004 * i, LON + 0.05)) for i in range(2)]
    stops, orders = _dp_stops([(c, p, 1500.0) for c, p in WEST + west2] + [(c, p, 100.0) for c, p in east2])
    draft = dp.Draft(trucks=sorted([HOWO.car_code, FORD.car_code]), next_id=4, trips=[
        dp.DraftTrip(1, HOWO.car_code, [c for c, _ in WEST]), dp.DraftTrip(2, HOWO.car_code, [c for c, _ in west2]),
        dp.DraftTrip(3, FORD.car_code, [c for c, _ in east2])])
    ctx = replace(_dp_ctx(), windows={114: (-INF, 650.0)})                  # до 10:50
    early = dp.fit_until(ctx, stops, dp.Draft.from_json(draft.to_json()), 114)
    assert early['hint'] is not None and early['hint']['eta'] < '10:30'
    h = dp.fit_until(ctx, stops, draft, 114, now_min=90.0)['hint']         # 10:30
    assert h is not None and h['trip'] is None and '10:30' <= h['eta'] <= '10:50', h
    edit = {'action': 'move', 'customer_id': 114, 'from_trip': 2, 'to_trip': None, 'truck': h['truck'], 'fit': True}
    moved = dp.apply_edit(ctx, stops, dp.Draft.from_json(draft.to_json()), edit, {o.isn for o in orders}, now_min=90.0)
    new = next(t for t in moved.trips if 114 in t.stops)
    assert new.not_before == 540 + 90.0 and new.truck == h['truck']
    assert _stops_of(dp.plan_view(ctx, stops, moved, _info, explain=False))[114][1]['eta'] == h['eta']
    assert dp.fit_until(ctx, stops, dp.Draft.from_json(draft.to_json()), 114, now_min=130.0)['hint'] is None


def test_frozen_given_by_caller_is_kept():
    """Начатые рейсы даёт вызывающий (views: по плану до правки, старые окна) — они не переставляются."""
    ctx, stops, draft, _ = _setup()
    got = dp.fit_until(_before(ctx, c104='09:20'), stops, draft, 104, frozen={1})
    assert got['kept'] == [1] and got['reordered'] == [] and draft.trips[0].stops == [101, 102, 103, 104]


def test_split_store_reports_split_without_hint():
    """Тяжёлый заказ в двух рейсах опаздывает: подсказки нет, причина — split (переносят вручную)."""
    ctx, stops, draft, _ = _setup()
    draft.trips[0].pinned = True
    draft.trips[1].stops.append(104)
    got = dp.fit_until(_before(ctx, c104='09:01'), stops, draft, 104)
    assert got['late'] and got['split'] and got['hint'] is None


# ============================== правка «Развоза» {"action": "until"} ==============================

def _day_setup(client, monkeypatch, trucks=('CAR1',)):
    from route_optimizer import views
    from route_optimizer.actuals import YEREVAN
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 9, 30, 18, 0, tzinfo=YEREVAN))   # накануне дня
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 18, 0))
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2)])
    body = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': list(trucks)}).get_json()
    assert body['success'], body
    return body


def _until(client, body, cid=104, time='09:40', scope='day', **kw):
    return client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': body['rev'], 'action': 'until',
                                                          'customer_id': cid, 'time': time, 'scope': scope, **kw})


def _bundle(client):
    return client.application.extensions['route_optimizer'].store.load()


def _trip_of(body, cid):
    return next(tr for t in body['plan']['trucks'] for tr in t['trips'] if any(s['customer_id'] == cid for s in tr['stops']))


def _stop(body, cid):
    return next(s for s in _trip_of(body, cid)['stops'] if s['customer_id'] == cid)


def test_api_day_scope_reorders_saves_only_this_day(client, monkeypatch):
    body = _day_setup(client, monkeypatch)
    assert [s['customer_id'] for s in _trip_of(body, 104)['stops']][-1] == 104 and _stop(body, 104)['eta'] > '09:40'
    r = _until(client, body)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d['until'] == {'late': False, 'reordered': [_trip_of(body, 104)['id']], 'kept': [], 'split': False, 'hint': None}
    s = _stop(d, 104)
    assert s['window'] == {'kind': 'before', 't1': 580, 't2': None, 'tol': None} and s['until_day'] == 580
    assert s['eta'] <= '09:40' and s['window_miss'] is False and d['plan']['summary']['window_miss'] == 0
    assert 'until_day' not in _stop(d, 101)
    b = _bundle(client)
    assert b.day_until == {D1: {104: 580}} and 104 not in b.windows               # постоянное окно не тронуто
    # сохранённый план — уже с новым порядком (его видят водители и обучение), прогноз — с новыми ETA
    again = client.get(f'/api/routes/dispatch?date={DAY}').get_json()
    assert _stop(again, 104)['until_day'] == 580 and _trip_of(again, 104)['stops'][0]['customer_id'] == 104
    assert d['rev'] == body['rev'] + 1


def test_api_always_scope_replaces_permanent_window_and_drops_day(client, monkeypatch):
    body = _day_setup(client, monkeypatch)
    state = client.application.extensions['route_optimizer']
    state.store.save_customer_window(104, st.CustomerWindow('between', 600, 720), 'qa')
    body = _until(client, client.get(f'/api/routes/dispatch?date={DAY}').get_json(), time='11:00').get_json()
    assert _bundle(client).day_until == {D1: {104: 660}}
    r = _until(client, body, time='09:40', scope='always')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    b = _bundle(client)
    assert b.windows[104] == st.CustomerWindow('before', 580) and b.day_until == {}   # срок дня снят — действует новое окно
    assert 'until_day' not in _stop(d, 104) and _stop(d, 104)['window']['t1'] == 580 and not d['until']['late']


def test_api_clear(client, monkeypatch):
    body = _day_setup(client, monkeypatch)
    state = client.application.extensions['route_optimizer']
    r = _until(client, body, time=None)                                     # снимать нечего
    assert r.status_code == 400 and r.get_json()['errors']['_'] == 'Այս ժամն արդեն նշված չէ — թարմացրեք էջը'
    body = _until(client, body).get_json()
    d = _until(client, body, time=None).get_json()
    assert d['success'] and _bundle(client).day_until == {} and _stop(d, 104)['window'] is None
    # «Միշտ» снимает только окно «մինչև»; другое — в «Առաքման պայմաններ»
    state.store.save_customer_window(104, st.CustomerWindow('after', 600), 'qa')
    d = client.get(f'/api/routes/dispatch?date={DAY}').get_json()
    r = _until(client, d, time=None, scope='always')
    assert r.status_code == 400 and '«Առաքման պայմաններ»' in r.get_json()['errors']['_']
    assert _bundle(client).windows[104] == st.CustomerWindow('after', 600)
    state.store.save_customer_window(104, st.CustomerWindow('before', 700), 'qa')
    d = _until(client, d, time=None, scope='always').get_json()
    assert d['success'] and 104 not in _bundle(client).windows


@pytest.mark.parametrize('kw, text', [
    ({'scope': 'week'}, 'Սերվերը չընդունեց հարցումը — թարմացրեք էջը'),
    ({'scope': None}, 'Սերվերը չընդունեց հարցումը — թարմացրեք էջը'),
    ({'cid': True}, 'Սերվերը չընդունեց հարցումը — թարմացրեք էջը'),
    ({'cid': '104'}, 'Սերվերը չընդունեց հարցումը — թարմացրեք էջը'),
    ({'time': 580}, 'Սերվերը չընդունեց հարցումը — թարմացրեք էջը'),
    ({'cid': 999999}, 'Խանութն այս օրվա պատվերներում չէ — թարմացրեք էջը'),
    ({'time': '25:00'}, 'Ժամը պետք է լինի 00:00-ից մինչև 23:59'),
    ({'time': '9:40'}, 'Ժամը պետք է լինի 00:00-ից մինչև 23:59'),
    ({'time': '09:60'}, 'Ժամը պետք է լինի 00:00-ից մինչև 23:59'),
    ({'time': ''}, 'Ժամը պետք է լինի 00:00-ից մինչև 23:59'),
])
def test_api_bad_requests_save_nothing(client, monkeypatch, kw, text):
    body = _day_setup(client, monkeypatch)
    r = _until(client, body, **kw)
    assert r.status_code == 400 and r.get_json()['errors'] == {'_': text}
    b = _bundle(client)
    assert b.day_until == {} and b.windows == {}
    assert client.get(f'/api/routes/dispatch?date={DAY}').get_json()['rev'] == body['rev']


def test_api_past_day_and_stale_rev_save_nothing(client, monkeypatch):
    body = _day_setup(client, monkeypatch)
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': body['rev'] - 1, 'action': 'until',
                                                       'customer_id': 104, 'time': '09:40', 'scope': 'always'})
    assert r.status_code == 409 and _bundle(client).windows == {}
    from route_optimizer import views
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 2, 9, 0))
    r = _until(client, body)
    assert r.status_code == 400 and r.get_json()['errors']['_'] == 'Անցած օրվա պլանում ժամը չի փոխվում'
    assert _bundle(client).day_until == {}


def test_api_day_deadline_keeps_permanent_start(client, monkeypatch):
    """Постоянное «10:00–12:00» + срок дня 11:00 → в этот день окно 10:00–11:00 (магазин не принимает раньше 10:00);
    срок не позже начала — отказ с понятным текстом, ничего не сохранено."""
    body = _day_setup(client, monkeypatch)
    state = client.application.extensions['route_optimizer']
    state.store.save_customer_window(104, st.CustomerWindow('between', 600, 720), 'qa')
    body = client.get(f'/api/routes/dispatch?date={DAY}').get_json()
    r = _until(client, body, time='10:00')
    assert r.status_code == 400 and r.get_json()['errors']['_'].startswith('Խանութն ընդունում է 10:00-ից ոչ շուտ')
    assert _bundle(client).day_until == {}
    d = _until(client, body, time='11:00').get_json()
    s = _stop(d, 104)
    assert s['window'] == {'kind': 'between', 't1': 600, 't2': 660, 'tol': None} and s['until_day'] == 660
    assert '10:00' <= s['eta'] <= '11:00' and s['window_miss'] is False


def test_api_window_changed_meanwhile_is_not_overwritten(client, monkeypatch):
    """«Միշտ»: окно магазина сменили в «Առաքման պայմաններ» уже после чтения настроек — ни окно, ни план не записаны."""
    body = _day_setup(client, monkeypatch)
    state = client.application.extensions['route_optimizer']
    real = state.store.save_dispatch

    def racing(day, data, user, expected_rev=None, also=None):
        state.store.save_customer_window(104, st.CustomerWindow('after', 900), 'other')
        return real(day, data, user, expected_rev=expected_rev, also=also)
    monkeypatch.setattr(state.store, 'save_dispatch', racing)
    r = _until(client, body, scope='always')
    assert r.status_code == 400 and 'հենց նոր փոխվել է' in r.get_json()['errors']['_']
    monkeypatch.setattr(state.store, 'save_dispatch', real)
    assert _bundle(client).windows[104] == st.CustomerWindow('after', 900)
    assert client.get(f'/api/routes/dispatch?date={DAY}').get_json()['rev'] == body['rev']


def test_api_locked_trip_not_reordered_marked_red(client, monkeypatch):
    body = _day_setup(client, monkeypatch)
    trip = _trip_of(body, 104)
    body = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': body['rev'], 'action': 'pin',
                                                          'trip': trip['id'], 'truck': 'CAR1'}).get_json()
    order = [s['customer_id'] for s in _trip_of(body, 104)['stops']]
    d = _until(client, body).get_json()
    assert d['until'] == {'late': True, 'reordered': [], 'kept': [trip['id']], 'split': False, 'hint': None}
    assert [s['customer_id'] for s in _trip_of(d, 104)['stops']] == order
    assert _stop(d, 104)['window_miss'] is True and _bundle(client).day_until == {D1: {104: 580}}   # срок сохранён


def test_api_sent_plan_reorder_waits_for_send(client, monkeypatch):
    """Утверждённый и отправленный план: рейс переставляется (закрепление утверждения — не замок логиста), водители видят
    новый порядок после «Ուղարկել» — как у любой правки после отправки (№81)."""
    body = _day_setup(client, monkeypatch)
    body = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': body['rev'], 'action': 'approve'}).get_json()
    assert body['success'] and body['unsent'] is None
    d = _until(client, body).get_json()
    assert d['success'] and d['until']['reordered'] and not d['until']['late']
    assert (d['unsent']['trucks'], d['unsent']['orders']) == (['CAR1'], False) and d['approved']
    assert all(tr['pinned'] for t in d['plan']['trucks'] for tr in t['trips'])


def test_api_effective_window_reaches_live_context(client, monkeypatch):
    """Срок дня — и в ETA / опозданиях карты машин этого дня (late_forecast), другой день — постоянное окно."""
    from route_optimizer import views
    _day_setup(client, monkeypatch)
    state = client.application.extensions['route_optimizer']
    state.store.save_customer_window(104, st.CustomerWindow('before', 900), 'qa')
    assert _until(client, client.get(f'/api/routes/dispatch?date={DAY}').get_json()).status_code == 200
    assert views._live_context(state, D1).windows[104] == (-INF, 580.0)
    assert views._live_context(state, D2).windows[104] == (-INF, 900.0)


def test_api_roles_without_dispatch_cannot_set_deadline(monkeypatch):
    """Правка идёт тем же /api/routes/dispatch/edit: «Склад», «Гараж» и территориальная роль — 403, без входа — не пускает."""
    import app_v2
    users = {r: {'role': r, 'areas': ['01'], 'password_hash': 'x'} for r in ('user', 'warehouse', 'garage')}
    monkeypatch.setattr(app_v2, 'load_users', lambda: users)
    c = app_v2.app.test_client()
    payload = {'date': DAY, 'rev': 1, 'action': 'until', 'customer_id': 104, 'time': '09:40', 'scope': 'always'}
    r = c.post('/api/routes/dispatch/edit', json=payload, headers={'Accept': 'application/json'})
    assert r.status_code in (302, 401)
    for name in users:
        with c.session_transaction() as sess:
            app_v2._stamp_session(sess, name, users[name])
        r = c.post('/api/routes/dispatch/edit', json=payload, headers={'Accept': 'application/json'})
        assert r.status_code == 403, name


# ============================== страница ==============================

def _js() -> str:
    return (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')


def test_page_has_until_dialog_button_and_tag():
    html = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    for piece in ('id="dpUntilDlg"', 'id="dpUntilTime"', 'type="time"', 'id="dpUntilDay"', 'id="dpUntilAlways"',
                  'id="dpUntilDayT"', 'id="dpUntilHint"', 'id="dpUntilErr"', 'id="dpUntilSave"', 'id="dpUntilClear"',
                  'id="dpUntilCancel"', 'Մինչև ժամը', 'Միայն այսօր', 'Միշտ', 'Հանել', 'Չեղարկել', 'Պահպանել'):
        assert piece in html, piece
    dlg = html[html.index('id="dpUntilDlg"'):html.index('</dialog>', html.index('id="dpUntilDlg"'))]
    assert dlg.index('id="dpUntilDay"') < dlg.index('id="dpUntilAlways"') and 'id="dpUntilDay" checked' in dlg
    assert html.index('id="dpCondDlg"') < html.index('id="dpUntilDlg"') < html.index('</div>\n{% endblock %}')
    js = _js()
    row = js[js.index('function stopItem('):js.index('const finePointer')]
    assert "tb.innerHTML = '<i class=\"fas fa-clock\" aria-hidden=\"true\"></i><span>Մինչև ժամը</span>'" in row
    assert "tb.addEventListener('click', () => openUntil(stop))" in row and 'acts.append(moveSelect(stop, tripId), ex, tb, vb, ub, gb)' in row
    assert "isMin(stop.until_day)" in row and "'dp-b-until'" in row and 'untilDayWord()' in row
    assert "$('dpUntilDlg').open" in js[js.index('const interacting'):js.index('async function poll')]
    save = js[js.index('async function saveUntil('):js.index('function untilToast(')]
    assert "edit({ action: 'until', customer_id: stop.customer_id, time, scope }, null)" in save
    toast = js[js.index('function untilToast('):js.index('// ---------- Заказы прошлых дней и исключённые')]
    assert 'fit: true' in toast and "label: 'Տեղափոխել'" in toast
    assert 'u.split' in toast and '«Տեղափոխել այլ երթ…»-ով' in toast      # тяжёлый заказ — своя причина, не «никто»
    # «Առաքման պայմաններ»: в день со сроком — заметка, что в этот день действует «Մինչև ժամը»
    assert 'id="dpCondDayNote"' in html
    cond = js[js.index('async function openCond('):js.index('async function saveCond(')]
    assert "$('dpCondDayNote').hidden = !isMin(stop.until_day)" in cond and '«Մինչև ժամը»՝ մինչև' in cond
    assert 'function untilFloor(' in js and 'մշտական ժամը) և մինչև ձեր նշած ժամը' in js
    opener = js[js.index('async function openUntil('):js.index('async function saveUntil(')]
    assert "api('GET', '/api/routes/customer-vehicles?customer_id=' + encodeURIComponent(stop.customer_id))" in opener
    assert '.innerHTML' not in opener + save + toast                         # данные ERP — только textContent
    css = (ROOT / 'static' / 'css' / 'routes_dispatch.css').read_text(encoding='utf-8')
    assert '.dp-b-until' in css and '.dp-until-dlg' in css and '.dp-until-opts' in css

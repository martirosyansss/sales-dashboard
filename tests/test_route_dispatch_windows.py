# -*- coding: utf-8 -*-
"""«Развоз»: окна приёма магазинов и малый центр (docs/plans/windows-center-plan.md, ответы владельца №34–41).

Синтетические данные, без БД ERP; база настроек — только временная (и копия базы владельца).
Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_windows.py -q
"""
import hashlib
import json
import math
import random
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import geo  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from test_route_optimizer import (DP_DAY, DP_DEPOT, DP_NORMS, EAST, FORD, HOWO, OWNER_DB, TN,  # noqa: E402,F401
                                  _dispatch_setup, _dorder, _dp_stops, _info, _no_road_map, _trip_of, client)

JAC = fl.FleetTruck('475DD61', 'JAC', 2200.0, 12.0, center_ok=True)
ZONE = tuple((lat, lon) for lat, lon in st.DEFAULT_SETTINGS['center_zone'])
CENTER = [(201, (40.1777, 44.5126), 300.0), (202, (40.1840, 44.5150), 300.0), (203, (40.1810, 44.5200), 300.0)]
START = 9 * 60                       # начало дня машины в тестах — 09:00
INF = math.inf


def _ctx(trucks, windows=None, zone=ZONE, tn=TN):
    return dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in trucks}, DP_NORMS, tn, START,
                         windows=windows or {}, center_zone=zone)


def _span(kind, t1, t2=None, tol=None):
    """Окно приёма → (не раньше, не позже) в минутах от начала дня машины."""
    lo, hi = st.CustomerWindow(kind, t1, t2, tol).span()
    return lo - START, hi - START


def _hm(text):
    h, m = map(int, text.split(':'))
    return h * 60 + m


# ============================== геометрия центра ==============================

def test_in_polygon_default_zone():
    assert geo.in_polygon((40.1777, 44.5126), ZONE)              # площадь Республики — в центре
    assert all(geo.in_polygon(p, ZONE) for _, p, _ in CENTER)
    assert not geo.in_polygon(DP_DEPOT, ZONE) and not geo.in_polygon((40.20, 44.64), ZONE)
    square = [(40.0, 44.0), (40.0, 45.0), (41.0, 45.0), (41.0, 44.0)]
    assert geo.in_polygon((40.5, 44.5), square) and not geo.in_polygon((41.5, 44.5), square)
    assert not geo.in_polygon((40.5, 44.5), square[:2])         # меньше трёх вершин — центра нет


# ============================== окно приёма: _schedule ==============================

def test_schedule_four_window_kinds():
    """Один магазин: 30 мин езды от склада, разгрузка 10 мин. Окно — прибытие (начало разгрузки); раньше
    окна машина ждёт, ожидание — её время."""
    m = [[0.0, 30.0], [30.0, 0.0]]

    def run(span, start=0.0):
        arr = []
        minutes, ok = fl._schedule([0], [fl._Stop(1, 100.0, 1.0, 10.0, *span)], m, start, arr)
        return round(minutes, 6), ok, [round(a, 6) for a in arr]

    assert run((0.0, INF)) == (70.0, True, [30.0])                                  # без окна
    assert run(_span('before', _hm('09:30'))) == (70.0, True, [30.0])               # «մինչև 09:30» — успели ровно
    assert run(_span('before', _hm('09:20')))[1] is False                            # «մինչև 09:20» — опоздали
    assert run(_span('after', _hm('10:40'))) == (140.0, True, [100.0])              # «10:40-ից հետո» — ждём 70 мин
    assert run(_span('between', _hm('09:40'), _hm('10:00'))) == (80.0, True, [40.0])  # «09:40–10:00» — ждём 10 мин
    assert run(_span('between', _hm('09:10'), _hm('09:20')))[1] is False
    assert run(_span('at', _hm('09:50'), tol=15)) == (75.0, True, [35.0])           # «09:50 ±15» — с 09:35
    assert run(_span('at', _hm('09:20'), tol=5))[1] is False                         # ±5 — к 09:25 не успеть
    assert run(_span('before', _hm('11:00')), start=100.0) == (70.0, False, [130.0])  # выезд позже — опоздание
    assert run(_span('after', _hm('10:00')), start=100.0) == (70.0, True, [130.0])    # приехали позже начала — без ожидания


def test_customer_window_check_and_span():
    ok = {'kind': 'at', 't1': 660}
    w, err = st.check_window(ok)
    assert err is None and w == st.CustomerWindow('at', 660, None, st.DEFAULT_WINDOW_TOL) and w.span() == (645.0, 675.0)
    assert st.check_window({'kind': 'before', 't1': 720, 't2': None})[0].span() == (-INF, 720.0)
    assert st.check_window({'kind': 'after', 't1': 840})[0].span() == (840.0, INF)
    assert st.check_window({'kind': 'between', 't1': 600, 't2': 840})[0].span() == (600.0, 840.0)
    assert st.check_window({'kind': 'at', 't1': 660, 'tol': 0})[0].span() == (660.0, 660.0)
    extra = 'Սերվերը չընդունեց հարցումը՝ ընդունման ժամի այս տեսակի համար ավելորդ դաշտ'
    shape = 'Սերվերը չընդունեց հարցումը՝ սպասվում էր {"kind", "t1", "t2", "tol"}'
    for bad, text in (({'kind': 'soon', 't1': 600}, 'Ընդունման ժամի տեսակը'),
                      ({'kind': 'before', 't1': 1440}, '00:00-ից մինչև 23:59'),
                      ({'kind': 'before', 't1': True}, '00:00-ից մինչև 23:59'),
                      ({'kind': 'before', 't1': 600.5}, '00:00-ից մինչև 23:59'),
                      ({'kind': 'between', 't1': 600, 't2': 600}, 'Միջակայքի վերջը'),
                      ({'kind': 'between', 't1': 600}, '00:00-ից մինչև 23:59'),
                      ({'kind': 'at', 't1': 600, 'tol': 121}, 'Թույլատրելի շեղումը'),
                      ({'kind': 'before', 't1': 600, 'tol': 5}, extra),
                      ({'kind': 'after', 't1': 600, 't2': 700}, extra),
                      ({'kind': 'before', 't1': 600, 'x': 1}, shape),
                      ([600], shape)):
        w, err = st.check_window(bad)
        assert w is None and text in err, bad


# ============================== без окон и центра — рейсы те же ==============================

GOLDEN_FLEET = [HOWO, FORD, fl.FleetTruck('475DD61', 'JAC', 2200.0, 12.0)]


def _golden_days():
    """Случайные дни (seed 0..59): точки, кг, выручка, машины, занятое время."""
    for seed in range(60):
        rng = random.Random(seed)
        n = rng.randint(1, 45)
        pts = [(40.05 + rng.random() * 0.3, 44.30 + rng.random() * 0.45) for _ in range(n)]
        kgs = [rng.choice([50.0, 150.0, 300.0, 700.0, 1500.0, 4000.0, 12000.0]) for _ in range(n)]
        revs = [rng.random() * 300000.0 for _ in range(n)]
        trucks = rng.sample(GOLDEN_FLEET, rng.randint(1, 3))
        used = {t.car_code: rng.choice([0.0, 0.0, 120.0, 400.0, 530.0]) for t in trucks}
        yield seed, pts, kgs, revs, trucks, used


def test_no_windows_trips_identical_to_before_change():
    """Регресс: без окон и центра plan_trips / route_day дают ровно те рейсы, что до изменения (модель парка в
    оптимизаторе и проверка дизеля №33 от них зависят). Отпечаток посчитан кодом до изменения (01.10.2026)
    на тех же днях: машина, точки, кг, км, минуты, литры — побайтно."""
    out = []
    for seed, pts, kgs, revs, trucks, used in _golden_days():
        for overflow in (True, False):
            for earliest in (False, True):
                trips = fl.route_day(pts, kgs, revs, DP_DEPOT, trucks, DP_NORMS, TN, used, overflow, earliest)
                fields = ('truck', 'stops', 'kg', 'revenue', 'km', 'minutes', 'capacity_kg', 'liters', 'extra', 'items')
                out.append((seed, overflow, earliest, [tuple(getattr(t, key) for key in fields) for t in trips]))
    assert hashlib.sha256(repr(out).encode()).hexdigest() == \
        'a0a3a6f6f018b9a7d84cdbbd337c0eb7d870a301d4c993b2955afe2a85a8887e'


def test_windows_that_never_bind_give_the_same_trips():
    """Путь с окнами (_plan_timed) с окнами, которые ничего не ограничивают, раскладывает так же, как прежний:
    у каждой машины — те же рейсы в том же порядке (рейсы разных машин выдаются по времени выезда)."""
    def per_truck(trips):
        out = {}
        for t in trips:
            out.setdefault(t.truck, []).append(t.items)
        return out

    for seed, pts, kgs, revs, trucks, used in _golden_days():
        for overflow in (True, False):
            for earliest in (False, True):
                plain = fl.route_day(pts, kgs, revs, DP_DEPOT, trucks, DP_NORMS, TN, used, overflow, earliest)
                timed = fl.route_day(pts, kgs, revs, DP_DEPOT, trucks, DP_NORMS, TN, used, overflow, earliest,
                                     windows=[(0.0, 1e9)] * len(pts))
                assert per_truck(timed) == per_truck(plain), (seed, overflow, earliest)


# ============================== сборка с окнами и центром ==============================

def _check_day(pts, kgs, windows, center, trucks, used, trips, reasons, tn=TN, departs=None):
    """Инварианты разложенного дня: каждый заказ — в рейсах или с причиной; рейсы машины подряд с её занятого
    времени соблюдают окна и рабочий день; точки центра — только у машин с правом въезда. departs — выезды из
    расчёта с окнами: рейс, показанный подряд (dispatch._timeline), выезжает ровно тогда же."""
    placed = [i for t in trips for i in t.items]
    assert len(placed) == len(set(placed)), 'лёгкий заказ — в одном рейсе'
    assert set(placed) | set(reasons) == set(range(len(pts))) and not set(placed) & set(reasons)
    assert set(reasons.values()) <= {'window', 'center', 'time'}
    if any(t.center_ok for t in trucks):
        assert 'center' not in reasons.values()
    else:
        assert {i for i, r in reasons.items() if r == 'center'} == {i for i in range(len(pts)) if center[i]}
    # рейсы машины — подряд в порядке выдачи, как их считает dispatch._timeline; окна и конец дня держатся
    busy = {t.car_code: used.get(t.car_code, 0.0) for t in trucks}
    by_code = {t.car_code: t for t in trucks}
    for k, t in enumerate(trips):
        items = list(t.items)
        dep, arr, minutes = fl.trip_schedule([pts[i] for i in items], [kgs[i] for i in items], DP_DEPOT, DP_NORMS, tn,
                                             busy[t.truck], [windows[i] for i in items])
        if departs:
            assert dep == pytest.approx(departs[k], abs=1e-6) and minutes == pytest.approx(t.minutes, abs=1e-6)
        else:
            assert minutes == pytest.approx(t.minutes)
        assert all(windows[i][0] - 1e-6 <= a <= windows[i][1] + 1e-6 for i, a in zip(items, arr)), (t, arr)
        busy[t.truck] = dep + minutes
        assert busy[t.truck] <= tn.work_minutes + 1e-6
        assert t.kg <= by_code[t.truck].capacity_kg + 1e-6
        assert by_code[t.truck].center_ok or not any(center[i] for i in items)


def _random_window(rng):
    kind = rng.choice(['before', 'after', 'between', 'at'])
    t1 = rng.randrange(9 * 60, 17 * 60, 5)
    if kind == 'between':
        return _span(kind, t1, min(t1 + rng.choice([30, 60, 120, 240]), 1439))
    return _span(kind, t1, tol=rng.choice([0, 15, 30]) if kind == 'at' else None)


@pytest.mark.parametrize('seed', range(30))
def test_plan_with_windows_and_center_keeps_rules(seed):
    """Случайные дни с окнами (≈ 40 % магазинов) и центром: всё разложенное соблюдает окна, рабочий день и
    центр; что не разложено — с причиной; цикл заканчивается (каждый шаг укорачивает рейс)."""
    rng = random.Random(seed)
    n = rng.randint(1, 35)
    pts = [(40.17 + rng.random() * 0.025, 44.50 + rng.random() * 0.03) if rng.random() < 0.3
           else (40.10 + rng.random() * 0.15, 44.40 + rng.random() * 0.3) for _ in range(n)]
    kgs = [rng.choice([50.0, 150.0, 300.0, 600.0, 1200.0, 2000.0]) for _ in range(n)]
    windows = [_random_window(rng) if rng.random() < 0.4 else (-INF, INF) for _ in range(n)]
    center = [geo.in_polygon(p, ZONE) for p in pts]
    trucks = rng.sample([HOWO, FORD, JAC], rng.randint(1, 3))
    used = {t.car_code: rng.choice([0.0, 0.0, 200.0, 450.0]) for t in trucks}
    reasons, departs = {}, []
    trips = fl.route_day(pts, kgs, [1.0] * n, DP_DEPOT, trucks, DP_NORMS, TN, used, overflow=False,
                         windows=windows, center=center, reasons=reasons, departs=departs)
    assert len(departs) in (0, len(trips))
    _check_day(pts, kgs, windows, center, trucks, used, trips, reasons, departs=departs)


def test_reasons_window_center_time():
    near, far = (40.20, 44.62), (40.21, 44.64)
    # окно: «մինչև 09:01» — за минуту от склада не доехать; другой магазин без окна едет
    reasons = {}
    trips = fl.route_day([near, far], [100.0, 100.0], [1.0, 1.0], DP_DEPOT, [FORD], DP_NORMS, TN, overflow=False,
                         windows=[_span('before', _hm('09:01')), (-INF, INF)], reasons=reasons)
    assert reasons == {0: 'window'} and [t.items for t in trips] == [(1,)]
    # центр: машины с правом въезда сегодня нет; с JAC — едет на JAC
    reasons = {}
    trips = fl.route_day([CENTER[0][1], near], [300.0, 300.0], [1.0, 1.0], DP_DEPOT, [FORD, HOWO], DP_NORMS, TN,
                         overflow=False, center=[True, False], reasons=reasons)
    assert reasons == {0: 'center'} and [t.items for t in trips] == [(1,)]
    reasons = {}
    trips = fl.route_day([CENTER[0][1], near], [300.0, 300.0], [1.0, 1.0], DP_DEPOT, [FORD, JAC], DP_NORMS, TN,
                         overflow=False, center=[True, False], reasons=reasons)
    assert reasons == {} and {t.truck for t in trips if 0 in t.items} == {JAC.car_code}
    # время: рабочий день 10 минут — не успевает никто (и в пути с окнами, и в прежнем)
    short = fl.TruckNorms(10.0, 8.0, 6.0)
    for windows in (None, [_span('after', _hm('09:00')), _span('before', _hm('17:00'))]):
        reasons = {}
        assert fl.route_day([near, far], [100.0, 100.0], [1.0, 1.0], DP_DEPOT, [FORD], DP_NORMS, short,
                            overflow=False, windows=windows, reasons=reasons) == []
        assert reasons == {0: 'time', 1: 'time'}


def test_deadline_trips_go_first():
    """EDF: рейс с более ранним крайним сроком едет первым, хотя по старому правилу первым шёл бы тяжёлый."""
    x, y = (40.20, 44.64), (40.17, 44.45)
    t_x = fl.route_day([x], [3000.0], [1.0], DP_DEPOT, [FORD], DP_NORMS, TN)[0].minutes
    t_y = fl.route_day([y], [3400.0], [1.0], DP_DEPOT, [FORD], DP_NORMS, TN)[0].minutes
    late_x = t_y                                         # Y первым — к X уже не успеть; X первым — успеваем
    assert t_x < late_x
    windows = [(0.0, late_x), (0.0, 480.0)]
    reasons = {}
    trips = fl.route_day([x, y], [3000.0, 3400.0], [1.0, 1.0], DP_DEPOT, [FORD], DP_NORMS, TN, overflow=False,
                         windows=windows, reasons=reasons)
    assert reasons == {} and [t.items for t in trips] == [(0,), (1,)]
    _check_day([x, y], [3000.0, 3400.0], windows, [False, False], [FORD], {}, trips, reasons)


def _review_points(n, seed=5):
    rng = random.Random(seed)
    return [(40.15 + rng.random() * 0.08, 44.45 + rng.random() * 0.15) for _ in range(n)]


def test_release_time_trip_waits_for_its_slot_not_the_morning():
    """Ревью: одна FORD, 12 магазинов × 1100 кг, №0 «после 14:00». Раньше рейс с №0 уходил первым и машина ждала
    часами — 5 магазинов «не успели». Теперь рейс встаёт к 14:00, утро — остальным: везут все."""
    pts = _review_points(12)
    kgs = [1100.0] * 12
    windows = [_span('after', _hm('14:00'))] + [(-INF, INF)] * 11
    reasons = {}
    trips = fl.route_day(pts, kgs, [1.0] * 12, DP_DEPOT, [FORD], DP_NORMS, TN, overflow=False,
                         windows=windows, reasons=reasons)
    assert reasons == {} and sorted(i for t in trips for i in t.items) == list(range(12))
    assert 0 in trips[-1].items                                         # рейс «после 14:00» — последним
    _check_day(pts, kgs, windows, [False] * 12, [FORD], {}, trips, reasons)
    # «после 15:00» + 5 других: утро не простаивает — без ожидания внутри рейсов, рейс с №0 последним
    pts, kgs = pts[:6], [600.0] * 6
    windows = [_span('after', _hm('15:00'))] + [(-INF, INF)] * 5
    reasons = {}
    trips = fl.route_day(pts, kgs, [1.0] * 6, DP_DEPOT, [FORD], DP_NORMS, TN, overflow=False,
                         windows=windows, reasons=reasons)
    assert reasons == {} and 0 in trips[-1].items and 0 not in [i for t in trips[:-1] for i in t.items]
    assert sum(t.minutes for t in trips) < 4 * 60                       # раньше один рейс — 491 мин, в основном ожидание
    _check_day(pts, kgs, windows, [False] * 6, [FORD], {}, trips, reasons)
    # то же в плане развоза: время машины подряд — окно соблюдено, день не превышен
    stops, _ = _dp_stops([(100 + i, p, kg) for i, (p, kg) in enumerate(zip(_review_points(12), [1100.0] * 12))])
    ctx = _ctx([FORD], {100: st.CustomerWindow('after', _hm('14:00')).span()}, zone=())
    draft = dp.build(ctx, stops, None, [FORD.car_code], 'now')
    view = dp.plan_view(ctx, stops, draft, _info)
    assert view['unassigned'] == [] and view['summary']['window_miss'] == 0 and not dp.runs_late(ctx, stops, draft)
    eta = [s['eta'] for t in view['trucks'][0]['trips'] for s in t['stops'] if s['customer_id'] == 100]
    assert len(eta) == 1 and eta[0] >= '14:00'


def test_view_departs_late_instead_of_waiting_at_first_stop():
    """Ревью (probe8 a): рейс к магазину «после 14:00» показывался с выездом 09:40 и 5,5 ч ожидания; теперь вид
    выезжает так же, как поставил расчёт, — к окну, без ожидания у первой точки (лист водителя, «Մեկնում»)."""
    stops, _ = _dp_stops([(1, (40.20, 44.64), 3400.0), (2, (40.30, 44.30), 3400.0)])
    ctx = _ctx([FORD], {2: st.CustomerWindow('after', _hm('14:00')).span()}, zone=())
    view = dp.plan_view(ctx, stops, dp.build(ctx, stops, None, [FORD.car_code], 'now'), _info)
    late = _trip_of(view, 2)[0]
    assert late['stops'][0]['eta'] == '14:00' and late['depart'] >= '13:00' and late['minutes'] < 150
    assert _trip_of(view, 1)[0]['depart'] == '09:00'


def test_pinned_after_window_trip_keeps_its_slot_on_rebuild():
    """Ревью (probe8 b): FORD, 5 утренних магазинов × 3400 кг и №1 «после 15:00». Свежая сборка везёт все шесть, №1
    последним; логист закрепил рейс №1 и пересобрал — раньше закреплённый рейс вставал 09:00→16:17 с ожиданием и 4
    магазина «не поместились». Теперь он остаётся на своём месте в конце дня, остальные — как были."""
    spec = [(1, (40.30, 44.30), 3400.0)] + [(10 + i, (40.10 + 0.02 * i, 44.75), 3400.0) for i in range(5)]
    stops, _ = _dp_stops(spec)
    ctx = _ctx([FORD], {1: st.CustomerWindow('after', _hm('15:00')).span()}, zone=())
    fresh = dp.build(ctx, stops, None, [FORD.car_code], 'now')
    assert not fresh.no_room and fresh.trips[-1].stops == [1]
    for t in fresh.trips:
        t.pinned = 1 in t.stops
    again = dp.build(ctx, stops, fresh, [FORD.car_code], 'now')
    view = dp.plan_view(ctx, stops, again, _info)
    assert again.no_room == set() and view['unassigned'] == [] and again.trips[-1].stops == [1] and again.trips[-1].pinned
    assert view['summary']['window_miss'] == 0 and not dp.runs_late(ctx, stops, again)
    assert [t['depart'] for t in view['trucks'][0]['trips']] == \
        [t['depart'] for t in dp.plan_view(ctx, stops, fresh, _info)['trucks'][0]['trips']]


@pytest.mark.parametrize('seed', range(40))
def test_pin_random_trips_and_rebuild(seed):
    """Случайные дни с окнами: свежая сборка и пересборка с закреплёнными случайными рейсами — без нарушений окон и
    переработки; закреплённые рейсы те же (остальное раскладывается заново)."""
    rng = random.Random(seed)
    n = rng.randint(3, 25)
    spec = [(100 + i, (40.10 + rng.random() * 0.15, 44.40 + rng.random() * 0.3), rng.choice([150.0, 600.0, 1500.0, 3000.0]))
            for i in range(n)]
    stops, _ = _dp_stops(spec)
    windows = {}
    for cid, _, _ in spec:
        if rng.random() < 0.4:
            kind = rng.choice(['before', 'after', 'between', 'at'])
            t1 = rng.randrange(600, 1020, 5)
            windows[cid] = st.CustomerWindow(kind, t1, t1 + 120 if kind == 'between' else None,
                                             15 if kind == 'at' else None).span()
    trucks = rng.sample([HOWO, FORD, JAC], rng.randint(1, 3))
    ctx = _ctx(trucks, windows)
    codes = [t.car_code for t in trucks]
    fresh = dp.build(ctx, stops, None, codes, 'now')
    v1 = dp.plan_view(ctx, stops, fresh, _info)
    assert v1['summary']['window_miss'] == 0 and not dp.runs_late(ctx, stops, fresh)
    for t in fresh.trips:
        t.pinned = rng.random() < 0.5
    pinned = sorted((t.id, t.truck, tuple(t.stops)) for t in fresh.trips if t.pinned)
    again = dp.build(ctx, stops, fresh, codes, 'now')
    v2 = dp.plan_view(ctx, stops, again, _info)
    assert v2['summary']['window_miss'] == 0 and not dp.runs_late(ctx, stops, again)
    assert sorted((t.id, t.truck, tuple(t.stops)) for t in again.trips if t.pinned) == pinned


@pytest.mark.parametrize('seed', range(0, 60, 3))
def test_rebuild_replans_unpinned_trips_after_changes(seed):
    """Ревью (probe9): один рейс закреплён; магазину из незакреплённого рейса прежнего плана поставили окно «до 09:15»
    или пришёл новый заказ на 9 т — пересборка раскладывает незакреплённые рейсы заново: окна не нарушаются, рейсы не
    перегружены и укладываются в день (старые незакреплённые рейсы не держатся на месте)."""
    rng = random.Random(seed)
    n = rng.randint(6, 30)
    spec = [(100 + i, (40.10 + rng.random() * 0.15, 44.40 + rng.random() * 0.3), rng.choice([150.0, 300.0, 600.0, 1200.0]))
            for i in range(n)]
    stops, _ = _dp_stops(spec)
    windows = {100: st.CustomerWindow('after', _hm('15:00')).span()}
    codes = [FORD.car_code, HOWO.car_code]
    fresh = dp.build(_ctx([FORD, HOWO], windows, zone=()), stops, None, codes, 'now')
    if len(fresh.trips) < 2:
        return
    fresh.trips[0].pinned = True
    victim = fresh.trips[-1].stops[-1]
    if fresh.trips[-1].pinned:
        return

    def clean(ctx, stops_, draft):
        view = dp.plan_view(ctx, stops_, draft, _info)
        trips = [tr for t in view['trucks'] for tr in t['trips']]
        assert view['summary']['window_miss'] == 0 and not any(t['late'] for t in view['trucks'])
        assert not any(tr['over_capacity'] or tr['over_time'] for tr in trips)

    tight = _ctx([FORD, HOWO], {**windows, victim: st.CustomerWindow('before', _hm('09:15')).span()}, zone=())
    clean(tight, stops, dp.build(tight, stops, dp.Draft.from_json(fresh.to_json()), codes, 'now'))
    heavy, _ = _dp_stops([(c, p, kg + (9000.0 if c == victim else 0.0)) for c, p, kg in spec])
    ctx = _ctx([FORD, HOWO], windows, zone=())
    clean(ctx, heavy, dp.build(ctx, heavy, dp.Draft.from_json(fresh.to_json()), codes, 'now'))


def test_center_truck_kept_for_center_trips():
    """Ревью: FORD + JAC (самая экономичная и единственная с правом въезда), 10 дальних магазинов вне центра и 5 в
    центре × 2000 кг. Раньше JAC забирала дальние рейсы, центр оставался «не успели». Теперь центр — на JAC."""
    rng = random.Random(5)
    far =[(40.05 + rng.random() * 0.3, 44.30 + rng.random() * 0.45) for _ in range(10)]
    cen = [p for _, p, _ in CENTER] + [(40.1830, 44.5100), (40.1760, 44.5150)]
    pts = far + cen
    center = [geo.in_polygon(p, ZONE) for p in pts]
    assert center == [False] * 10 + [True] * 5
    reasons = {}
    trips = fl.route_day(pts, [2000.0] * 15, [1.0] * 15, DP_DEPOT, [FORD, JAC], DP_NORMS, TN, overflow=False,
                         center=center, reasons=reasons)
    on_jac = {i for t in trips if t.truck == JAC.car_code for i in t.items}
    assert set(range(10, 15)) <= on_jac and not set(range(10, 15)) & set(reasons)
    _check_day(pts, [2000.0] * 15, [(-INF, INF)] * 15, center, [FORD, JAC], {}, trips, reasons)


def test_heavy_center_order_split_by_center_truck():
    """Заказ в центре тяжелее JAC — несколько поездок JAC поровну; не влезают все — снимается целиком."""
    p = CENTER[0][1]
    trips = fl.route_day([p], [5000.0], [1.0], DP_DEPOT, [HOWO, JAC], DP_NORMS, TN, overflow=False, center=[True])
    assert [(t.truck, t.items) for t in trips] == [(JAC.car_code, (0,))] * 3
    assert all(t.kg == pytest.approx(5000.0 / 3) for t in trips)
    one = trips[0].minutes
    reasons = {}
    assert fl.route_day([p], [5000.0], [1.0], DP_DEPOT, [HOWO, JAC], DP_NORMS, TN, {JAC.car_code: TN.work_minutes - one * 2.5},
                        overflow=False, center=[True], reasons=reasons) == []
    assert reasons == {0: 'time'}


# ============================== план развоза ==============================

def test_dispatch_build_view_and_edits_with_windows_and_center():
    stops, orders = _dp_stops(CENTER + EAST)
    ids = {o.isn for o in orders}
    windows = {101: st.CustomerWindow('after', _hm('13:00')).span(),
               102: st.CustomerWindow('between', _hm('09:30'), _hm('10:30')).span()}
    ctx = _ctx([FORD, JAC], windows)
    draft = dp.build(ctx, stops, None, [FORD.car_code, JAC.car_code], 'now')
    assert (draft.no_room, draft.no_window, draft.no_center) == (set(), set(), set())
    view = dp.plan_view(ctx, stops, draft, _info)
    for cid, _, _ in CENTER:                                           # центр — только JAC
        [trip] = _trip_of(view, cid)
        assert trip['truck'] == JAC.car_code
    stops_json = {s['customer_id']: s for tr in view['trucks'] for t in tr['trips'] for s in t['stops']}
    assert all(stops_json[c]['center'] for c, _, _ in CENTER) and not any(stops_json[c]['center'] for c, _, _ in EAST)
    assert _hm(stops_json[101]['eta']) >= _hm('13:00')                  # «13:00-ից հետո» — ждёт
    assert _hm('09:30') <= _hm(stops_json[102]['eta']) <= _hm('10:30')
    assert view['summary']['window_miss'] == 0 and view['summary']['center_miss'] == 0
    assert all(t['center_ok'] == (t['car_code'] == JAC.car_code) for t in view['trucks'])
    assert not dp.runs_late(ctx, stops, draft)
    # без JAC: центр — «сегодня нет машины», логист всё равно может поставить точку в рейс (нарушение видно)
    only = _ctx([FORD], windows)
    d2 = dp.build(only, stops, None, [FORD.car_code], 'now')
    assert d2.no_center == {201, 202, 203}
    v2 = dp.plan_view(only, stops, d2, _info)
    assert {u['customer_id'] for u in v2['unassigned'] if u['no_center']} == {201, 202, 203}
    trip = next(t for t in d2.trips)
    d2 = dp.apply_edit(only, stops, d2, {'action': 'move', 'customer_id': 201, 'from_trip': None,
                                         'to_trip': trip.id}, ids)
    assert 201 not in d2.no_center and 201 in trip.stops
    v2 = dp.plan_view(only, stops, d2, _info)
    assert [s['center_miss'] for s in _trip_of(v2, 201)[0]['stops'] if s['customer_id'] == 201] == [True]
    assert v2['summary']['center_miss'] == 1
    # окно, в которое не успеть: сборка — no_window; ручной рейс — пометка window_miss
    tight = _ctx([FORD], {101: st.CustomerWindow('before', _hm('09:01')).span()}, zone=())
    d3 = dp.build(tight, stops, None, [FORD.car_code], 'now')
    assert d3.no_window == {101} and not d3.no_room and not d3.no_center
    v3 = dp.plan_view(tight, stops, d3, _info)
    assert [u['customer_id'] for u in v3['unassigned'] if u['no_window']] == [101]
    manual = dp.Draft([FORD.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101, 102])], next_id=2)
    v4 = dp.plan_view(tight, stops, manual, _info)
    assert v4['summary']['window_miss'] == 1 and v4['trucks'][0]['trips'][0]['window_miss'] == 1
    d3 = dp.apply_edit(tight, stops, d3, {'action': 'move', 'customer_id': 101, 'from_trip': None,
                                          'to_trip': d3.trips[0].id}, ids)
    assert d3.no_window == set()


def test_dispatch_wait_counts_in_truck_time_and_runs_late():
    """Ожидание у окна — время машины: «вернётся в …» позже, а ожидание до конца дня — переработка."""
    stops, _ = _dp_stops(EAST[:1])
    plain = _ctx([FORD], zone=())
    draft = dp.Draft([FORD.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101])], next_id=2)
    back = dp.plan_view(plain, stops, draft, _info)['trucks'][0]['trips'][0]['return']
    waits = _ctx([FORD], {101: st.CustomerWindow('after', _hm('16:00')).span()}, zone=())
    trip = dp.plan_view(waits, stops, draft, _info)['trucks'][0]['trips'][0]
    assert trip['stops'][0]['eta'] == '16:00' and _hm(trip['return']) > _hm(back) and _hm(trip['return']) > _hm('16:00')
    assert not dp.runs_late(waits, stops, draft)
    late = _ctx([FORD], {101: st.CustomerWindow('after', _hm('17:58')).span()}, zone=())
    assert dp.runs_late(late, stops, draft)


def test_dispatch_overtime_retries_window_stores():
    """«Везти после конца дня»: магазин «после 17:55» до 18:00 не помещается, с переработкой — едет."""
    stops, _ = _dp_stops(EAST[:1])
    windows = {101: st.CustomerWindow('after', _hm('17:55')).span()}
    ctx = _ctx([FORD], windows, zone=())
    draft = dp.build(ctx, stops, None, [FORD.car_code], 'now')
    assert draft.trips == [] and draft.no_window | draft.no_room == {101}
    capped = replace(ctx, overtime_minutes=TN.work_minutes + 120.0)
    after = dp.overtime(capped, stops, draft)
    assert [t.stops for t in after.trips] == [[101]] and not after.no_window and not after.no_room
    view = dp.plan_view(capped, stops, after, _info)
    assert view['trucks'][0]['trips'][0]['stops'][0]['eta'] == '17:55' and view['summary']['window_miss'] == 0
    # и с переработкой не успеть в окно — «не успеваем в окно», а не «не поместились»; центр без машины — no_center
    tight = replace(capped, windows={101: st.CustomerWindow('before', _hm('09:01')).span()})
    again = dp.overtime(tight, stops, dp.Draft([FORD.car_code], no_room={101}))
    assert (again.trips, again.no_room, again.no_window) == ([], set(), {101})
    cstops, _ = _dp_stops(CENTER[:1])
    again = dp.overtime(replace(capped, center_zone=ZONE), cstops, dp.Draft([FORD.car_code], no_room={201}))
    assert (again.trips, again.no_room, again.no_center) == ([], set(), {201})


def test_draft_json_old_format_and_new_sets():
    old = {'trucks': ['A'], 'trips': [{'id': 1, 'truck': 'A', 'stops': [5]}], 'no_room': [7], 'next_id': 2}
    d = dp.Draft.from_json(old)                                         # черновик до окон — читается
    assert (d.no_room, d.no_window, d.no_center) == ({7}, set(), set())
    d.no_window, d.no_center = {8, 9}, {10}
    back = dp.Draft.from_json(json.loads(json.dumps(d.to_json())))
    assert back == d and back.to_json()['no_window'] == [8, 9]
    junk = dp.Draft.from_json({**old, 'no_window': [1, 'x', True, 2.5], 'no_center': 'bad'})
    assert (junk.no_window, junk.no_center) == ({1}, set())


# ============================== база настроек: схема 9 ==============================

def _v8_db(path, broken=False):
    """База схемы 8 (как до окон): машины без center_ok, таблицы окон нет."""
    with closing(sqlite3.connect(path)) as conn:
        for sql in st._SCHEMA:
            if sql == st._CUSTOMER_WINDOW_TABLE:
                continue
            conn.execute(f'CREATE TABLE IF NOT EXISTS trucks({st._TRUCKS_COLUMNS_V8})' if sql == st._TRUCKS_TABLE else sql)
        conn.execute("INSERT INTO meta VALUES('schema_version', '8')")
        conn.execute("INSERT INTO settings VALUES('penalty_change', '450')")
        conn.executemany('INSERT INTO trucks VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                         [('475DD61', 2200, 12, None, 1, 0, None, None, 'x', 'owner'),
                          ('CAR2', 3500, 16, None, None, 0, None, None, 'x', 'owner'),
                          ('M1', 1500, 10, None, 1, 1, 'JAC 2', 7, 'x', 'owner')])
        conn.execute("INSERT INTO dispatch_plan VALUES('2026-10-01', '{\"no_room\": [5]}', 3, 'x', 'owner')")
        if broken:
            conn.execute('ALTER TABLE trucks ADD COLUMN center_ok INTEGER')   # столбец уже есть — миграция падает
        conn.commit()


def test_store_migrates_schema_8_to_9_additive(tmp_path):
    """8 → 9: только ALTER TABLE ADD COLUMN и CREATE TABLE — всё прежнее как было, «можно в центр» у всех «авто»,
    граница центра — стартовая (ключа в базе нет). Сбой — база остаётся версии 8."""
    path = str(tmp_path / 'v8.db')
    _v8_db(path)
    s = st.Store(path)
    b = s.load()
    assert b.trucks['475DD61'] == st.Truck('475DD61', 2200, 12, None, True, 'x', 'owner')
    assert b.trucks['M1'].manual and b.trucks['M1'].center_ok is None and b.windows == {}
    assert b.settings['penalty_change'] == 450 and b.settings['center_zone'] == st.DEFAULT_SETTINGS['center_zone']
    assert s.load_dispatch('2026-10-01') == ({'no_room': [5]}, 3)
    # «авто»: JAC по названию — ERP CARS.fNAME, у ручной — её название
    assert b.truck_center_ok('475DD61', 'JAC') and not b.truck_center_ok('CAR2', 'FORD')
    assert b.truck_center_ok('M1', None) and b.truck_center_ok('NEW', 'jac') and not b.truck_center_ok('NEW', None)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == (str(st.SCHEMA_VERSION),)
        cols = [r[1] for r in conn.execute('PRAGMA table_info(trucks)')]
        # схема 22 (№68) пересобрала таблицу машин: столбцы — в порядке новой базы, «большая машина» — последней
        assert 'center_ok' in cols and cols[-5:] == [*st.LOAD_COST_FIELDS, 'big'] and 'customer_window' in {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    s.save_customer_window(101, st.CustomerWindow('between', 600, 720), 'qa')
    s.save_customer_window(102, st.CustomerWindow('at', 660, None, 15), 'qa')
    s.save_customer_window(103, st.CustomerWindow('before', 700), 'qa')
    s.save_customer_window(103, None, 'qa')
    assert st.Store(path).load().windows == {101: st.CustomerWindow('between', 600, 720),
                                              102: st.CustomerWindow('at', 660, None, 15)}
    for bad in ((0, st.CustomerWindow('before', 600)), (5, st.CustomerWindow('between', 700, 600)),
                (True, None), (5, st.CustomerWindow('at', 600, None, 500))):
        with pytest.raises(ValueError):
            s.save_customer_window(*bad, 'qa')
    broken = str(tmp_path / 'v8-broken.db')
    _v8_db(broken, broken=True)
    with pytest.raises(st.StoreError):
        st.Store(broken).load()
    with closing(sqlite3.connect(broken)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('8',)


def test_store_rejects_broken_window_rows(tmp_path):
    path = str(tmp_path / 'w.db')
    s = st.Store(path)
    s.load()
    for row in ("1, 'soon', 600, NULL, NULL", "1, 'between', 700, 600, NULL", "1, 'at', 600, NULL, NULL",
                "1, 'before', 2000, NULL, NULL"):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('DELETE FROM customer_window')
            conn.execute(f"INSERT INTO customer_window VALUES({row}, 'x', NULL)")
            conn.commit()
        with pytest.raises(st.StoreError, match='ընդունման ժամը'):
            s.load()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DELETE FROM customer_window')
        conn.execute("INSERT INTO trucks(car_code, active, center_ok, updated_at) VALUES('A', 1, 5, 'x')")
        conn.commit()
    with pytest.raises(st.StoreError, match='կարող է մտնել կենտրոն'):
        s.load()


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы маршрутов владельца')
def test_store_owner_copy_migrates_to_9(tmp_path):
    """КОПИЯ базы владельца: машины и настройки — те же, JAC 475DD61 по умолчанию въезжает в центр; запись —
    только в копию, файл владельца не меняется."""
    owner_bytes = OWNER_DB.read_bytes()
    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro&immutable=1', uri=True)) as conn:
        before = sorted(conn.execute('SELECT car_code, capacity_kg, fuel_l_per_100km, active FROM trucks').fetchall())
    copy = tmp_path / 'owner.db'
    copy.write_bytes(owner_bytes)
    s = st.Store(str(copy))
    b = s.load()
    assert sorted((t.car_code, t.capacity_kg, t.fuel_l_per_100km, None if t.active is None else int(t.active))
                  for t in b.trucks.values()) == before
    assert all(t.center_ok is None for t in b.trucks.values()) and b.windows == {}
    assert b.truck_center_ok('475DD61', 'JAC')
    s.save_customer_window(123, st.CustomerWindow('after', 840), 'qa')
    assert s.load().windows == {123: st.CustomerWindow('after', 840)}
    assert OWNER_DB.read_bytes() == owner_bytes


def test_settings_center_zone_and_center_ok_validation(tmp_path):
    s = st.Store(str(tmp_path / 's.db'))
    b = s.load()
    ref = st.RefData(frozenset({'CAR1', 'CAR2'}), frozenset(), frozenset())
    zone = [[40.18, 44.50], [40.19, 44.52], [40.17, 44.53]]
    changes, errors = st.validate_payload({'settings': {'center_zone': zone},
                                           'trucks': [{'car_code': 'CAR1', 'center_ok': True},
                                                      {'car_code': 'CAR2', 'center_ok': None}]}, b, ref)
    assert errors == {}
    s.save(changes, 'qa')
    b = s.load()
    assert b.settings['center_zone'] == zone and b.trucks['CAR1'].center_ok is True and b.trucks['CAR2'].center_ok is None
    assert b.truck_center_ok('CAR1', 'FORD') and not b.truck_center_ok('CAR2', 'FORD')
    for bad in ([[40.18, 44.50], [40.19, 44.52]], [[40.18, 44.50]] * 201, [[55.0, 44.5]] * 3,
                [[40.18, 44.50, 1]] * 3, [[True, 44.5]] * 3, 'x', None):
        _, errors = st.validate_payload({'settings': {'center_zone': bad}}, b, ref)
        assert 'settings.center_zone' in errors, bad
    _, errors = st.validate_payload({'trucks': [{'car_code': 'CAR1', 'center_ok': 'yes'}]}, b, ref)
    assert 'trucks.0.center_ok' in errors
    manual = {'car_code': 'B22', 'name': 'JAC', 'capacity_kg': 2500, 'fuel_l_per_100km': 12, 'active': True}
    changes, errors = st.validate_payload({'manual_trucks': [manual]}, b, ref)
    assert errors == {}
    s.save(changes, 'qa')
    assert s.load().truck_center_ok('B22', None)                       # ручная JAC — «авто»: можно
    changes, errors = st.validate_payload({'manual_trucks': [{**manual, 'center_ok': False}]}, s.load(), ref)
    assert errors == {}
    s.save(changes, 'qa')
    assert not s.load().truck_center_ok('B22', None)
    _, errors = st.validate_payload({'manual_trucks': [{**manual, 'center_ok': 1}]}, s.load(), ref)
    assert 'manual_trucks.0.center_ok' in errors


# ============================== API ==============================

def test_api_settings_center_zone_and_trucks_center_ok(client):
    d = client.get('/api/routes/settings').get_json()
    assert d['settings']['center_zone'] == st.DEFAULT_SETTINGS['center_zone'] == d['center_zone_default']
    cars = {t['car_code']: t for t in d['trucks']}
    assert (cars['CAR1']['center_ok'], cars['CAR1']['center_ok_source'], cars['CAR1']['auto_center_ok']) == \
        (False, 'auto', False)                                          # HOWO — не JAC
    zone = [[40.18, 44.50], [40.19, 44.52], [40.17, 44.53], [40.175, 44.505]]
    r = client.post('/api/routes/settings', json={'settings': {'center_zone': zone},
                                                  'trucks': [{'car_code': 'CAR1', 'center_ok': True}]})
    assert r.status_code == 200, r.get_json()
    d = client.get('/api/routes/settings').get_json()
    assert d['settings']['center_zone'] == zone
    car1 = next(t for t in d['trucks'] if t['car_code'] == 'CAR1')
    assert (car1['center_ok'], car1['center_ok_source']) == (True, 'manual')
    r = client.post('/api/routes/settings', json={'settings': {'center_zone': zone[:2]}})
    assert r.status_code == 400 and 'settings.center_zone' in r.get_json()['errors']
    r = client.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR1', 'center_ok': 'да'}]})
    assert r.status_code == 400 and 'trucks.0.center_ok' in r.get_json()['errors']


def test_api_customer_window_and_dispatch_marks(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 200.0, agent=2)])
    ok = client.post('/api/routes/customer-window', json={'customer_id': 101, 'window': {'kind': 'at', 't1': 660}})
    assert ok.status_code == 200 and ok.get_json() == {'success': True}
    assert client.post('/api/routes/customer-window',
                       json={'customer_id': 104, 'window': {'kind': 'before', 't1': 541}}).status_code == 200
    for body in ({'customer_id': 101, 'window': {'kind': 'soon', 't1': 600}},
                 {'customer_id': 101, 'window': {'kind': 'between', 't1': 700, 't2': 600}},
                 {'customer_id': 101, 'window': {'kind': 'at', 't1': 600, 'tol': 200}},
                 {'customer_id': 101, 'window': {'kind': 'before', 't1': -1}},
                 {'customer_id': 'x', 'window': None}, {'customer_id': 0, 'window': None},
                 {'customer_id': 101}, [1]):
        r = client.post('/api/routes/customer-window', json=body)
        assert r.status_code == 400 and r.get_json()['success'] is False, body
    r = client.post('/api/routes/customer-window', json={'customer_id': 101, 'window': {'kind': 'between', 't1': 700, 't2': 600}})
    assert r.get_json()['error'] == 'Միջակայքի վերջը պետք է լինի սկզբից ուշ'
    assert client.post('/api/routes/customer-window', data='x', content_type='text/plain').status_code == 415
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert {t['car_code']: t['center_ok'] for t in d['trucks']} == {'CAR1': True, 'CAR2': False}
    plan = d['plan']
    stops = {s['customer_id']: s for tr in plan['trucks'] for t in tr['trips'] for s in t['stops']}
    assert stops[101]['window'] == {'kind': 'at', 't1': 660, 't2': None, 'tol': 15}
    assert '10:45' <= stops[101]['eta'] <= '11:15' and stops[101]['window_miss'] is False
    assert stops[102]['window'] is None and stops[102]['center'] is geo.in_polygon((40.19, 44.52), ZONE)
    assert all({'eta', 'window', 'window_miss', 'center', 'center_miss'} <= set(s) for s in stops.values())
    assert all(not s['center_miss'] for s in stops.values()) and plan['summary']['center_miss'] == 0
    assert plan['summary']['window_miss'] == 0
    # 104 «մինչև 09:01» — не успеть: не в рейсах, «не успеваем в окно»
    [u] = plan['unassigned']
    assert (u['customer_id'], u['no_window'], u['no_room'], u['no_center']) == (104, True, False, False)
    assert u['window'] == {'kind': 'before', 't1': 541, 't2': None, 'tol': None}
    # окно сняли — следующая сборка его везёт
    assert client.post('/api/routes/customer-window', json={'customer_id': 104, 'window': None}).status_code == 200
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['plan']['unassigned'] == []

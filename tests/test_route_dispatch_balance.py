# -*- coding: utf-8 -*-
"""«Развоз»: ровная загрузка рейсов (ответ владельца №44: 5 т + 3 т → 4 т + 4 т, если км растут не больше 3%).

Синтетические данные, без БД ERP и без карты дорог (км по прямой).
Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_balance.py -q
"""
import math
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from test_route_optimizer import DP_DAY, DP_DEPOT, DP_NORMS, TN, _dp_ctx, _dp_stops, _info, _no_road_map  # noqa: E402,F401

HOWO = fl.FleetTruck('123AV61', 'HOWO', 5000.0, 18.0)
HOWO2 = fl.FleetTruck('124AV61', 'HOWO', 5000.0, 18.0)
FORD = fl.FleetTruck('333DO33', 'FORD', 2500.0, 15.5)
JAC = fl.FleetTruck('475DD61', 'JAC', 2200.0, 10.0, center_ok=True)
# пример владельца: четыре магазина рядом, 2,5 + 2,5 + 1,5 + 1,5 т, одна HOWO на 5 т — сборка даёт 5 т + 3 т
OWNER = [(40.2072, 44.6512), (40.2081, 44.6417), (40.2043, 44.6331), (40.2066, 44.6418)]
OWNER_KG = [2500.0, 2500.0, 1500.0, 1500.0]
# те же грузы, магазины дальше друг от друга: ровнее выходит только дороже 3% км
SPREAD = [(40.2027, 44.6488), (40.2005, 44.6458), (40.2034, 44.6295), (40.2052, 44.6422)]


def _day(pts, kgs, trucks, **kw):
    return fl.route_day(pts, kgs, [1.0] * len(pts), DP_DEPOT, trucks, DP_NORMS, TN, overflow=False, **kw)


def _loads(trips):
    return sorted(round(t.kg) for t in trips)


def _replay(trips, pts, kgs, windows, used=None):
    """Рейсы машин подряд, как их повезёт «Развоз» (dispatch._timeline): машина → (окна соблюдены, конец дня)."""
    share = Counter(i for t in trips for i in t.items)
    free, ok = dict(used or {}), {}
    for t in trips:
        idx = list(t.items)
        win = [windows[i] for i in idx] if windows is not None else None
        depart, arrivals, minutes = fl.trip_schedule([pts[i] for i in idx], [kgs[i] / share[i] for i in idx],
                                                     DP_DEPOT, DP_NORMS, TN, free.get(t.truck, 0.0), win)
        free[t.truck] = depart + minutes
        hit = win is None or all(a <= w[1] + 1e-6 for a, w in zip(arrivals, win))
        ok[t.truck] = ok.get(t.truck, True) and hit
    return {code: (ok[code], free[code]) for code in ok}


# ============================== пример владельца ==============================

def test_owner_example_5_plus_3_becomes_4_plus_4():
    before = _day(OWNER, OWNER_KG, [HOWO])
    after = _day(OWNER, OWNER_KG, [HOWO], balance=True)
    assert _loads(before) == [3000, 5000]
    assert _loads(after) == [4000, 4000]
    assert [t.truck for t in after] == [t.truck for t in before]                 # машина и число рейсов те же
    assert sorted(i for t in after for i in t.items) == [0, 1, 2, 3]
    km0, km1 = math.fsum(t.km for t in before), math.fsum(t.km for t in after)
    assert km1 <= km0 * (1 + fl.BALANCE_SLACK) + 1e-9
    for t in after:                                                               # цифры рейса пересчитаны
        assert t.stops == len(t.items) and t.kg == pytest.approx(sum(OWNER_KG[i] for i in t.items))
        assert t.liters == pytest.approx(t.km * HOWO.l100 / 100.0)
        _, km, minutes = fl.route_trip([OWNER[i] for i in t.items], [OWNER_KG[i] for i in t.items], DP_DEPOT,
                                       DP_NORMS, TN, reorder=False)
        assert (t.km, t.minutes) == pytest.approx((km, minutes))
    assert all(ok and end <= TN.work_minutes for ok, end in _replay(after, OWNER, OWNER_KG, None).values())


def test_two_trucks_each_one_trip():
    """Машина успевает один рейс — 5 т и 3 т везут две машины; выравнивание — между машинами."""
    short = 70.0
    used = {HOWO.car_code: TN.work_minutes - short, HOWO2.car_code: TN.work_minutes - short}
    before = _day(OWNER, OWNER_KG, [HOWO, HOWO2], used=used)
    after = _day(OWNER, OWNER_KG, [HOWO, HOWO2], used=used, balance=True)
    assert _loads(before) == [3000, 5000] and {t.truck for t in before} == {HOWO.car_code, HOWO2.car_code}
    assert _loads(after) == [4000, 4000] and [t.truck for t in after] == [t.truck for t in before]
    assert all(ok and end <= TN.work_minutes + 1e-6 for ok, end in _replay(after, OWNER, OWNER_KG, None, used).values())


def test_only_trips_above_90_percent_are_unloaded():
    """4,5 т из 5 (90%) — не перегруз: рейсы как были; 4,6 т (92%) — уже разгружается."""
    at_90 = [2250.0, 2250.0, 1500.0, 1500.0]
    before = _day(OWNER, at_90, [HOWO])
    assert _loads(before) == [3000, 4500]
    assert [(t.items, t.km) for t in _day(OWNER, at_90, [HOWO], balance=True)] == [(t.items, t.km) for t in before]
    assert _loads(_day(OWNER, [2300.0, 2300.0, 1500.0, 1500.0], [HOWO], balance=True)) == [3800, 3800]


def test_no_balance_when_it_costs_more_than_3_percent_km(monkeypatch):
    before = _day(SPREAD, OWNER_KG, [HOWO])
    assert _loads(before) == [3000, 5000]
    after = _day(SPREAD, OWNER_KG, [HOWO], balance=True)
    assert [(t.truck, t.items, t.km) for t in after] == [(t.truck, t.items, t.km) for t in before]
    monkeypatch.setattr(fl, 'BALANCE_SLACK', 1.0)       # без предела км — выровнял бы: держит именно предел
    assert _loads(_day(SPREAD, OWNER_KG, [HOWO], balance=True)) != [3000, 5000]


def test_default_and_fleet_model_do_not_balance():
    assert _loads(_day(OWNER, OWNER_KG, [HOWO])) == [3000, 5000]
    with pytest.raises(ValueError):
        fl.route_day(OWNER, OWNER_KG, [1.0] * 4, DP_DEPOT, [HOWO], DP_NORMS, TN, overflow=True, balance=True)
    with pytest.raises(ValueError):
        _day(OWNER, OWNER_KG, [HOWO], busy={}, balance=True)


# ============================== окна, центр, закреплённые ==============================

def test_window_blocks_the_move():
    """Магазины на 2,5 т принимают только до 09:40 — их везёт первый рейс; во второй (позже) их не перенести:
    без окон те же заказы выравниваются (пример владельца), с окнами — остаются 5 т + 3 т."""
    wins = [(-math.inf, 40.0), (-math.inf, 40.0), (-math.inf, math.inf), (-math.inf, math.inf)]
    after = _day(OWNER, OWNER_KG, [HOWO], windows=wins, balance=True)
    assert [(round(t.kg), set(t.items)) for t in after] == [(5000, {0, 1}), (3000, {2, 3})]
    assert all(ok for ok, _ in _replay(after, OWNER, OWNER_KG, wins).values())


def test_center_store_stays_on_center_truck():
    """JAC (центр) успевает один рейс: две точки по 1,1 т = 2,2 т (100%); FORD везёт 1 т (40%). Точку центра FORD
    не возьмёт — выравнивать нечем; та же точка вне центра переходит к FORD."""
    pts = [(40.1777, 44.5126), (40.1790, 44.5140), (40.1800, 44.5160)]
    kgs = [1100.0, 1100.0, 1000.0]
    used = {JAC.car_code: TN.work_minutes - 80.0}

    def run(center):
        trips = _day(pts, kgs, [JAC, FORD], used=used, center=center, balance=True)
        return {t.truck: (round(t.kg), set(t.items)) for t in trips}

    assert run([True, True, False]) == {JAC.car_code: (2200, {0, 1}), FORD.car_code: (1000, {2})}
    assert run([True, False, False]) == {JAC.car_code: (1100, {0}), FORD.car_code: (2100, {1, 2})}


def test_swap_never_puts_center_store_on_non_center_truck():
    """HOWO везёт 5 т (100%), JAC — точку центра 1,6 т. Единственный ход ровнее — обмен 2 т ↔ 1,6 т, но тогда точка
    центра уехала бы на HOWO — нельзя: рейсы как были."""
    kgs = [2000.0, 2000.0, 1000.0, 1600.0]
    center = [False, False, False, True]
    before = _day(OWNER, kgs, [HOWO, JAC], center=center)
    assert [(t.truck, set(t.items)) for t in before] == [(JAC.car_code, {3}), (HOWO.car_code, {0, 1, 2})]
    after = _day(OWNER, kgs, [HOWO, JAC], center=center, balance=True)
    assert [(t.truck, t.items) for t in after] == [(t.truck, t.items) for t in before]


def test_liters_budget_with_different_fuel_use():
    """HOWO успевает один рейс, второй везёт машина на 30 л/100 км. 4 т + 4 т — +2% км, но +3,8% литров: не
    выравниваем; та же машина на 18 л — выравниваем (держит именно предел по литрам)."""
    pts = [(40.2046, 44.6528), (40.2142, 44.655), (40.2078, 44.6555), (40.1973, 44.6489), (40.19, 44.6479),
           (40.1945, 44.6381)]
    kgs = [1000.0, 2000.0, 1000.0, 1000.0, 500.0, 2500.0]
    used = {HOWO.car_code: TN.work_minutes - 100.0}
    thirsty = fl.FleetTruck('G1', 'GAZ', 5000.0, 30.0)
    before = _day(pts, kgs, [HOWO, thirsty], used=used)
    assert _loads(before) == [3000, 5000] and {t.truck for t in before} == {HOWO.car_code, 'G1'}
    after = _day(pts, kgs, [HOWO, thirsty], used=used, balance=True)
    assert [(t.truck, t.items) for t in after] == [(t.truck, t.items) for t in before]
    same = fl.FleetTruck('G1', 'GAZ', 5000.0, HOWO.l100)
    assert _loads(_day(pts, kgs, [HOWO, same], used=used, balance=True)) == [4000, 4000]


def test_build_balances_but_keeps_pinned_trips():
    spec = [(301 + i, p, kg) for i, (p, kg) in enumerate(zip(OWNER, OWNER_KG))]
    stops, _ = _dp_stops(spec)
    ctx = _dp_ctx((HOWO,))
    view = dp.plan_view(ctx, stops, dp.build(ctx, stops, None, [HOWO.car_code], 'now'), _info)
    assert sorted(t['load_pct'] for tr in view['trucks'] for t in tr['trips']) == [80, 80]
    # логист закрепил рейс 5 т — он остаётся как есть, остальное — вторым рейсом; и без окон (закреплённый рейс —
    # занятое время машины), и с окном (закреплённый рейс идёт в расчёт как fixed — _plan_around)
    old = dp.Draft(trucks=[HOWO.car_code], trips=[dp.DraftTrip(1, HOWO.car_code, [301, 302], pinned=True)], next_id=2)
    timed = dp.DayContext(ctx.day, ctx.depot, ctx.trucks, ctx.norms, ctx.tn, ctx.work_start_min,
                          windows={303: (0.0, 24 * 60.0)})
    for c in (ctx, timed):
        draft = dp.build(c, stops, old, [HOWO.car_code], 'now')
        assert [(t.id, sorted(t.stops), t.pinned) for t in draft.trips] == [(1, [301, 302], True), (2, [303, 304], False)]


# ============================== случайные дни: инварианты ==============================

def _random_days():
    for seed in range(40):
        rng = random.Random(seed)
        n = rng.randint(6, 40)
        pts = [(round(40.12 + rng.random() * 0.14, 4), round(44.42 + rng.random() * 0.25, 4)) for _ in range(n)]
        kgs = [rng.choice([80.0, 200.0, 450.0, 900.0, 1500.0, 2500.0, 6000.0]) for _ in range(n)]
        trucks = rng.sample([HOWO, HOWO2, FORD, JAC], rng.randint(1, 4))
        wins = [rng.choice([(-math.inf, math.inf)] * 6 + [(-math.inf, 120.0), (240.0, math.inf), (60.0, 240.0)])
                for _ in range(n)]
        center = [rng.random() < 0.15 for _ in range(n)]
        used = {t.car_code: rng.choice([0.0, 0.0, 200.0]) for t in trucks}
        yield seed, pts, kgs, trucks, (wins if seed % 2 else None), (center if seed % 3 == 0 else None), used


def test_random_days_invariants():
    """Выравнивание ничего не ломает: те же заказы, машины и число рейсов; тоннаж, центр, окна и конец дня
    соблюдаются; км и литры — не больше +3%; самый загруженный рейс не тяжелее, чем был."""
    balanced = 0
    for seed, pts, kgs, trucks, wins, center, used in _random_days():
        kw = dict(windows=wins, center=center, used=used)
        before = _day(pts, kgs, trucks, **kw)
        after = _day(pts, kgs, trucks, balance=True, **kw)
        assert [t.truck for t in after] == [t.truck for t in before], seed
        assert Counter(i for t in after for i in t.items) == Counter(i for t in before for i in t.items), seed
        caps = {t.car_code: t for t in trucks}
        assert all(t.kg <= caps[t.truck].capacity_kg + 1e-6 for t in after), seed
        if center is not None:
            assert all(caps[t.truck].center_ok for t in after for i in t.items if center[i]), seed
        was = _replay(before, pts, kgs, wins, used)
        now = _replay(after, pts, kgs, wins, used)
        for code, (ok, end) in was.items():
            if ok and end <= TN.work_minutes + 1e-6:
                assert now[code][0] and now[code][1] <= TN.work_minutes + 1e-6, (seed, code)
        km0, km1 = math.fsum(t.km for t in before), math.fsum(t.km for t in after)
        l0, l1 = math.fsum(t.liters for t in before), math.fsum(t.liters for t in after)
        assert km1 <= km0 * (1 + fl.BALANCE_SLACK) + 1e-6 and l1 <= l0 * (1 + fl.BALANCE_SLACK) + 1e-6, seed
        load = lambda ts: sorted((t.kg / t.capacity_kg for t in ts), reverse=True)   # noqa: E731
        assert load(after) <= load(before), seed
        balanced += load(after) != load(before)
    assert balanced >= 5          # на случайных днях выравнивание действительно срабатывает


def _plain_minutes(ctx, cids, routable, shares):
    """Езда + разгрузка рейса без ожидания у окон."""
    return dp._route(ctx, cids, routable, shares, reorder=False)[2]


def test_random_builds_with_windows_and_pinned_trips(monkeypatch):
    """Сборка «Развоза» со случайными окнами и закреплёнными рейсами (путь _plan_around): выравнивание не добавляет
    опозданий в окно и работы после конца дня, ожидание у окон внутри рейса растёт не больше WAIT_MERGE_SLACK,
    закреплённый рейс выезжает не позже, чем без выравнивания, и сам не меняется."""
    fleet = [HOWO, HOWO2, FORD, JAC, fl.FleetTruck('G1', 'GAZ', 3500.0, 16.0)]
    # Здесь сравнивается именно выравнивание №44. Решатель №45 может менять число
    # незакреплённых рейсов; его совместимость с окнами/закреплениями проверяется отдельно.
    route_day = fl.route_day
    monkeypatch.setattr(fl, 'route_day', lambda *args, **kwargs: route_day(*args, **dict(kwargs, solver=False)))
    unbalanced = lambda trips, *a, **k: trips   # noqa: E731
    balanced = 0
    for seed in range(60):
        rng = random.Random(seed)
        n = rng.randint(10, 45)
        spec = [(1000 + i, (round(40.12 + rng.random() * 0.14, 4), round(44.42 + rng.random() * 0.25, 4)),
                 rng.choice([80.0, 200.0, 450.0, 900.0, 1500.0, 2500.0])) for i in range(n)]
        stops, _ = _dp_stops(spec)
        trucks = rng.sample(fleet, rng.randint(1, 4))
        wins = {}
        for s in stops:
            r = rng.random()
            if r < 0.1:
                wins[s.customer_id] = (-math.inf, 9 * 60 + rng.choice([120, 180, 300]))
            elif r < 0.2:
                wins[s.customer_id] = (9 * 60 + rng.choice([180, 300]), math.inf)
        ctx = dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in trucks}, DP_NORMS, TN, 9 * 60,
                            windows=wins)
        codes = [t.car_code for t in trucks]
        old = dp.build(ctx, stops, None, codes, 'now')
        for t in old.trips:
            t.pinned = rng.random() < 0.3
        routable = {s.customer_id: s for s in stops}
        orig = fl._balance
        fl._balance = unbalanced
        try:
            d0 = dp.build(ctx, stops, old, codes, 'now')
        finally:
            fl._balance = orig
        d1 = dp.build(ctx, stops, old, codes, 'now')
        assert [(t.id, t.truck) for t in d1.trips] == [(t.id, t.truck) for t in d0.trips], seed
        assert [(t.id, t.stops) for t in d1.trips if t.pinned] == [(t.id, t.stops) for t in d0.trips if t.pinned], seed
        assert sorted(c for t in d1.trips for c in t.stops) == sorted(c for t in d0.trips for c in t.stops), seed
        v0, v1 = dp.plan_view(ctx, stops, d0, _info), dp.plan_view(ctx, stops, d1, _info)
        ok0 = {tr['car_code']: not tr['late'] and not any(t['window_miss'] for t in tr['trips']) for tr in v0['trucks']}
        for tr in v1['trucks']:
            if ok0[tr['car_code']]:
                assert not tr['late'] and not any(t['window_miss'] for t in tr['trips']), (seed, tr['car_code'])
        sh0, sh1 = dp._shares(d0.trips), dp._shares(d1.trips)
        t0, t1 = dp._timeline(ctx, d0.trips, routable, sh0), dp._timeline(ctx, d1.trips, routable, sh1)
        for a, b in zip(d0.trips, d1.trips):
            wait0 = t0[a.id][1] - _plain_minutes(ctx, a.stops, routable, sh0)
            wait1 = t1[b.id][1] - _plain_minutes(ctx, b.stops, routable, sh1)
            assert wait1 <= wait0 + fl.WAIT_MERGE_SLACK + 1e-6, (seed, b.id)
            if b.pinned:
                assert t1[b.id][0] <= t0[a.id][0] + 1e-6, (seed, b.id)
        assert v1['summary']['km'] <= v0['summary']['km'] * (1 + fl.BALANCE_SLACK) + 0.2, seed
        balanced += [t.stops for t in d1.trips] != [t.stops for t in d0.trips]
    assert balanced >= 5

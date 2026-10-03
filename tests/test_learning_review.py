# -*- coding: utf-8 -*-
"""Регрессии независимой проверки обучения «Развоза» (learning-loop, ревью ed98611..98a815c): H1, M1–M6, L1–L11 и
пробелы тестов. Сценарии — из проверочных скриптов ревьюера (scratchpad/review-tmp), только синтетические треки.
Запуск из корня проекта:  python -m pytest tests/test_learning_review.py -q
"""
import random
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import route_optimizer as ro  # noqa: E402
from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.geo import haversine_km  # noqa: E402
from route_optimizer.traffic_validation import TrafficProfile  # noqa: E402
from test_learning_loop import (BASE, DEPOT, REF, TODAY, A, B, C, Track, X, _days, _learning_client,  # noqa: E402
                                _stops, _t, _two_trips)
from test_route_optimizer import _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401

TZ = ac.YEREVAN
D = date(2026, 9, 29)


# ============================== H1: загрузка на складе ==============================

def _reload_day(ret, stay, kg=2000.0, d=D):
    """Два рейса: первый (клиент 101) возвращается в ret (минуты дня), второй (клиент 103) выезжает после стоянки stay."""
    at = lambda m: datetime(d.year, d.month, d.day, tzinfo=TZ) + timedelta(minutes=m)   # noqa: E731
    v0 = ac.Visit(('A',), at(9 * 60 + 40), at(9 * 60 + 55), 0, False)
    v1 = ac.Visit(('C',), at(ret + stay + 30), at(ret + stay + 45), 1, False)
    t1 = ac.Trip(at(9 * 60 + 20), at(ret), None, (0,), 1000.0, 20.0)
    t2 = ac.Trip(at(ret + stay), at(ret + stay + 75), stay, (1,), kg, 20.0)
    return ac.DayActual(100, 40.0, at(9 * 60), at(18 * 60), (), (v0, v1), (t1, t2), (), 0, (), (('A', 0), ('C', 1)))


RELOAD_STOPS = [ac.PlanStop('A', 101, (40.18, 44.5), 1000.0, 1000.0), ac.PlanStop('C', 103, (40.20, 44.55), 2000.0, 2000.0)]


def _plan(ret_plan, wait, load=20.0, d=D):
    at = lambda m: datetime(d.year, d.month, d.day, tzinfo=TZ) + timedelta(minutes=m)   # noqa: E731
    return [lr.PlanTrip(at(9 * 60), None, at(9 * 60 + 20), frozenset({101})),
            lr.PlanTrip(at(ret_plan + wait), at(ret_plan), at(ret_plan + wait + load), frozenset({103}))]


@pytest.mark.parametrize('ret, stay, wait, expected', [
    (11 * 60 - 12, 20.5, 0, 20.5),        # плана ожидания нет, машина раньше на 12 мин — стоянка как есть
    (11 * 60 - 25, 20.5, 0, 20.5),        # …на 25 мин раньше — тоже
    (11 * 60, 35.0, 15, 20.0),            # план ждёт 15 мин, машина вовремя — вычесть 15
    (11 * 60 + 10, 25.0, 15, 20.0),       # план ждёт 15 мин, машина опоздала на 10 — вычесть остаток 5
    (11 * 60 + 20, 20.0, 15, 20.0),       # опоздала на 20 — от планового ожидания ничего не осталось
    (11 * 60 - 10, 45.0, 15, 30.0),       # раньше на 10: лишнее время план не задумывал — вычесть только 15
    (11 * 60, 12.0, 15, 12.0),            # уехала раньше планового начала загрузки — ждать не стала, вычитать нечего
])
def test_h1_planned_wait_is_plan_own_wait_only(ret, stay, wait, expected):
    day = _reload_day(ret, stay)
    got = lr.load_obs(D, day, RELOAD_STOPS, _plan(11 * 60, wait))
    assert [o.minutes for o in got] == [pytest.approx(expected)]
    assert [o.minutes for o in lr.load_obs(D, day, RELOAD_STOPS)] == [pytest.approx(stay)]   # без плана — стоянка


def test_h1_planned_wait_never_exceeds_dwell():
    assert lr.planned_wait(_plan(11 * 60, 30)[1], datetime(2026, 9, 29, 11, 0, tzinfo=TZ),
                           datetime(2026, 9, 29, 11, 31, tzinfo=TZ)) == pytest.approx(30)
    assert lr.planned_wait(_plan(11 * 60, 30)[1], datetime(2026, 9, 29, 11, 25, tzinfo=TZ),
                           datetime(2026, 9, 29, 11, 45, tzinfo=TZ)) == pytest.approx(5)
    assert lr.planned_wait(_plan(11 * 60, 0)[1], datetime(2026, 9, 29, 10, 0, tzinfo=TZ),
                           datetime(2026, 9, 29, 11, 45, tzinfo=TZ)) == 0.0
    assert lr.planned_wait(None, datetime(2026, 9, 29, 10, 0, tzinfo=TZ), datetime(2026, 9, 29, 11, 0, tzinfo=TZ)) == 0.0


def test_h1_early_returns_do_not_bias_loading():
    """Сценарий проверяющего (probe_h1_bias_early): плана ожидания нет (окон нет), машина возвращается на 5–25 мин раньше
    плана, склад грузит сразу: наблюдения — истинная загрузка, выученная норма — около истины (15 + 5·т)."""
    rnd = random.Random(7)
    obs = []
    for i in range(60):
        d = TODAY - timedelta(days=60 - i)
        tonnes = rnd.choice([1.0, 2.0, 3.0])
        true = 15 + 5 * tonnes + rnd.uniform(-1, 1)
        ret = 11 * 60 + rnd.uniform(-25, -5)
        obs += lr.load_obs(d, _reload_day(ret, true, tonnes * 1000, d), RELOAD_STOPS, _plan(11 * 60, 0, d=d))
    assert len(obs) == 60
    assert abs(sum(o.minutes - (15 + 5 * o.tonnes) for o in obs) / len(obs)) < 0.5
    o = lr.fit_loading(obs, TODAY, (15.0, 5.0))
    assert o.params['fixed_min'] == pytest.approx(15, abs=1.0) and o.params['per_tonne_min'] == pytest.approx(5, abs=0.5)


def test_h1_split_order_matched_to_nearest_planned_departure():
    """Тяжёлый заказ в двух плановых рейсах (те же клиенты): плановый рейс — с выездом ближе к фактическому."""
    at = lambda m: datetime(2026, 9, 29, tzinfo=TZ) + timedelta(minutes=m)   # noqa: E731
    plan = [lr.PlanTrip(at(540), None, at(560), frozenset({101})),
            lr.PlanTrip(at(700), at(680), at(720), frozenset({101})),     # ждёт 20 мин
            lr.PlanTrip(at(860), at(800), at(880), frozenset({101}))]     # ждёт 60 мин
    assert lr._planned_trip(plan, 1, {101}, at(722)) is plan[1]
    assert lr._planned_trip(plan, 1, {101}, at(875)) is plan[2]
    assert lr._planned_trip(plan, 1, {999}, at(875)) is plan[1]          # общих клиентов нет — по номеру рейса
    assert lr._planned_trip(plan, 5, {999}, at(875)) is None


def test_h1_plan_trips_from_prediction():
    pred = {'trips': [{'loading_start': '09:00', 'depart': '09:20', 'return': '11:00', 'stops': [[101, '09:40']]},
                      {'loading_start': '11:30', 'depart': '11:50', 'return': '13:10', 'stops': [[103, '12:20']]}]}
    trips = lr.plan_trips(pred, D)
    assert [t.prev_return for t in trips] == [None, datetime(2026, 9, 29, 11, 0, tzinfo=TZ)]
    assert trips[1].wait == (datetime(2026, 9, 29, 11, 0, tzinfo=TZ), datetime(2026, 9, 29, 11, 30, tzinfo=TZ))
    assert trips[0].wait is None and trips[1].customers == frozenset({103}) and lr.plan_trips(None, D) == []


def test_h1_lunch_at_depot_is_not_loading():
    tr = Track(DEPOT, _t(8)).stay(20).drive(A, 30).stay(10).drive(DEPOT, 30).stay(25 + 60)
    tr.drive(C, 30).stay(10).drive(DEPOT, 30).stay(5)
    lunch = ac.reconstruct(tr.fixes, _stops(), DEPOT)
    assert lunch.trips[1].load_min is None and lr.load_obs(D, lunch, _stops()) == []


def _loads(cur_noise=12.0, lunch=True, seed=8):
    rnd = random.Random(seed)
    out = []
    for d in _days(40):
        wait = rnd.uniform(0, cur_noise)                                         # ожидание, которого план не знает
        for t in (1.0, 3.0):
            out.append(lr.LoadObs(d, t, 10 + 5 * t + wait))
        if lunch:
            out.append(lr.LoadObs(d, 2.0, 55.0))                                  # обед на складе
    return out


def test_h1_fit_loading_low_quantile_and_cap():
    cur = (10.0, 5.0)
    o = lr.fit_loading(_loads(), TODAY, cur)
    a, b = o.params['fixed_min'], o.params['per_tonne_min']
    train_days = len([d for d in _days(40) if d < TODAY - timedelta(days=lr.HOLDOUT_DAYS)])
    assert o.n_obs == 2 * train_days                         # обед (55 > 2 × 20) не учтён
    assert b == pytest.approx(5, abs=0.8)
    assert 10 + 0.35 * 12 - 2 < a < 10 + 0.5 * 12            # нижний квантиль (≈ 14), не среднее (16)
    mean_fit = lr.huber_fit([(1.0, x.tonnes, x.minutes) for x in _loads(lunch=False)])
    assert a < mean_fit[0]


def test_h1_fit_loading_step_limited_to_30_percent():
    cur = (4.0, 2.0)          # действующая норма сильно занижена: отсечение «2 × нормы» убрало бы всё — не применяется
    o = lr.fit_loading(_loads(lunch=False), TODAY, cur)
    a, b = o.params['fixed_min'], o.params['per_tonne_min']
    tonnes = [1.0, 3.0]
    preds = [(a + b * t) / (cur[0] + cur[1] * t) for t in tonnes]
    assert max(preds) == pytest.approx(1.3, abs=0.01) and all(p <= 1.3 + 1e-3 for p in preds)


def test_h1_empty_manual_norm_uses_median_baseline_bounds_and_step():
    """В настройках загрузки нет (как в базе владельца): опора — медиана стоянок обучения; первое принятие — тоже не
    дальше ±30% от неё; выученное — в пределах LOAD_BOUNDS."""
    obs = _loads(lunch=False)
    train = [x for x in obs if x.day < TODAY - timedelta(days=lr.HOLDOUT_DAYS)]
    med = sorted(x.minutes for x in train)[len(train) // 2 - 1: len(train) // 2 + 1]
    o = lr.fit_loading(obs, TODAY, None)
    a, b = o.params['fixed_min'], o.params['per_tonne_min']
    base = sum(med) / 2
    assert all(0.7 * base - 1e-3 <= a + b * t <= 1.3 * base + 1e-3 for t in (1.0, 3.0))
    assert 'медиана' in o.reason
    test = [x for x in obs if x.day >= TODAY - timedelta(days=lr.HOLDOUT_DAYS)]
    assert o.mae_before == pytest.approx(sum(abs(base - x.minutes) for x in test) / len(test), abs=1e-3)
    huge = [lr.LoadObs(x.day, x.tonnes, 60 + 25 * x.tonnes) for x in obs]     # неправдоподобно: 60 мин + 25 мин/т
    h = lr.fit_loading(huge, TODAY, (40.0, 15.0))
    lo, hi = lr.LOAD_BOUNDS
    assert lo[0] <= h.params['fixed_min'] <= lo[1] and hi[0] <= h.params['per_tonne_min'] <= hi[1]
    assert lr.valid_params('loading', h.params) and not lr.valid_params('loading', {'fixed_min': 50, 'per_tonne_min': 4})


def test_h1_loading_auto_learning_off_by_default(client, monkeypatch):
    state = _learning_client(client, monkeypatch)
    views.run_learning(state, TODAY)
    st = {s['kind']: s for s in client.get('/api/routes/learning/status').get_json()['status']}
    assert (st['loading']['auto'], st['loading']['auto_chosen'], st['loading']['default_auto']) == (False, False, False)
    assert st['loading']['in_effect'] is None and st['loading']['last']['params'] is not None   # выучено, не действует
    assert st['unload']['auto'] is True
    r = client.post('/api/routes/learning/auto', json={'kind': 'loading', 'auto': True}).get_json()
    loading = next(s for s in r['status'] if s['kind'] == 'loading')
    assert loading['auto'] is True and (loading['in_effect'] is not None) == loading['last']['accepted']


# ============================== M1: соседние магазины в разное время ==============================

def _two_close_shops(with_delivery=True):
    s2 = (A[0] + 50 / 111195, A[1])                          # соседний магазин в 50 м
    tr = Track(DEPOT, _t(8)).stay(15).drive(A, 30).stay(8)
    t1 = tr.t - timedelta(minutes=3)
    tr.drive(C, 30).stay(10).drive(s2, 30).stay(12)
    t2 = tr.t - timedelta(minutes=2)
    tr.drive(DEPOT, 30).stay(5)
    stops = [ac.PlanStop('S1', 101, A, 600.0, 600.0, None, 0, t1 if with_delivery else None),
             ac.PlanStop('S2', 102, s2, 900.0, 900.0, (0, 15 * 60), 2, t2 if with_delivery else None),
             ac.PlanStop('C', 103, C, 400.0, 400.0, None, 1)]
    return tr, stops


@pytest.mark.parametrize('with_delivery', [True, False])
def test_m1_close_shops_served_at_different_times(with_delivery):
    tr, stops = _two_close_shops(with_delivery)
    day = ac.reconstruct(tr.fixes, stops, DEPOT)
    assert [(v.keys, v.repeat) for v in day.visits] == [(('S1',), False), (('C',), False), (('S2',), False)]
    obs = lr.unload_obs(D, day, stops)
    assert [(o.n, o.tonnes, o.customers) for o in obs] == [(1, 0.6, (101,)), (1, 0.4, (103,)), (1, 0.9, (102,))]
    assert day.visited['S2'] == day.visits[2].arrive                       # окно S2 — по её визиту, а не по S1
    assert ac.visit_metrics(day, stops, D).order_changes == 0


def test_m1_delivery_marked_on_later_stay_is_the_service_visit():
    tr = Track(DEPOT, _t(8)).stay(10).drive(A, 30).stay(5)              # магазин закрыт — без доставки
    tr.drive(C, 30).stay(10).drive(A, 30).stay(9)
    done = tr.t - timedelta(minutes=1)                                  # доставка — на втором заезде
    tr.drive(DEPOT, 30).stay(5)
    stops = _stops(A={'delivered_at': done})
    day = ac.reconstruct(tr.fixes, stops, DEPOT)
    assert [(v.keys, v.repeat) for v in day.visits] == [(('A',), True), (('C',), False), (('A',), False)]
    assert [round(o.minutes) for o in lr.unload_obs(D, day, stops)] == [10, 9]
    assert day.visited['A'] == day.visits[2].arrive


# ============================== M2: разгрузка без ожидания окна ==============================

def test_m2_unload_obs_subtracts_window_wait_and_caps():
    stops = _stops(A={'window': (9 * 60, 18 * 60)})                    # «после 9:00»
    tr = Track(DEPOT, _t(8)).stay(10).drive(A, 30).stay(60).drive(DEPOT, 30).stay(5)   # приехал до 9:00 и ждал
    day = ac.reconstruct(tr.fixes, stops, DEPOT)
    v = day.visits[0]
    wait = 9 * 60 - ac.day_minutes(D, v.arrive)
    assert 20 < wait < v.minutes - 5
    assert [o.minutes for o in lr.unload_obs(D, day, stops)] == [pytest.approx(v.minutes - wait)]
    long = Track(DEPOT, _t(8)).stay(10).drive(A, 30).stay(100).drive(DEPOT, 30).stay(5)
    assert lr.unload_obs(D, ac.reconstruct(long.fixes, _stops(), DEPOT), _stops()) == []   # > 90 мин — не разгрузка
    rnd = random.Random(3)
    obs = [lr.UnloadObs(d, 1, 0.5, 4 + 6 + rnd.uniform(-0.5, 0.5), (100 + i,)) for d in _days(40) for i in range(4)]
    obs += [lr.UnloadObs(d, 1, 0.5, 70.0, (200,)) for d in _days(40)]   # обед у магазина: > 3 × нормы
    o = lr.fit_unload(obs, lambda x: 8 + 6 * x.tonnes, TODAY)
    assert o.params['per_stop_min'] + 0.5 * o.params['per_tonne_min'] == pytest.approx(10, abs=0.6)
    assert '200' not in o.params['store_offsets']


# ============================== M3: дрожание GPS на месте ==============================

@pytest.mark.parametrize('jitter', [60.0, 80.0])
def test_m3_standstill_jitter_adds_no_km(jitter):
    clean = ac.reconstruct(_two_trips().fixes, _stops(), DEPOT)
    noisy = ac.reconstruct(_two_trips(jitter=jitter).fixes, _stops(), DEPOT)
    assert [v.keys for v in noisy.visits] == [('A',), ('B',), ('C',)]
    assert noisy.km_gps == pytest.approx(clean.km_gps, rel=0.03)
    assert sum(t.km_gps for t in noisy.trips) == pytest.approx(clean.km_gps, rel=0.03)


def test_m3_terminal_speed_gates_unplanned_standstill():
    tr = Track(DEPOT, _t(8)).stay(10).drive(X, 30)
    stand = (tr.t, tr.t + timedelta(minutes=4.5))
    tr.stay(4.5, step=15, jitter_m=80).drive(DEPOT, 30).stay(5)     # 4,5 мин стоит не у точки плана — не «стоянка»
    still = [ac.TrackFix(f.at, f.lat, f.lon, f.accuracy, 0.1 if stand[0] < f.at <= stand[1] else 8.0) for f in tr.fixes]
    no_spd = ac.reconstruct(tr.fixes, _stops(), DEPOT).km_gps
    gated = ac.reconstruct(still, _stops(), DEPOT).km_gps
    path = 2 * haversine_km(DEPOT, X)
    assert no_spd > path * 1.05 and gated == pytest.approx(path, rel=0.03)


def test_m3_track_fixes_keep_terminal_speed():
    fx = lr.track_fixes([(1_000_000_000_000, 40.18, 44.5, 5.0, 7.5), (1_000_000_060_000, 40.18, 44.5, 5.0),
                         (1_000_000_120_000, 40.18, 44.5, 5.0, None)])
    assert [f.spd for f in fx] == [7.5, None, None]


# ============================== M4: отчёт не пересчитывает неизменённые дни ==============================

def test_m4_actuals_cached_until_facts_change(client, monkeypatch):
    state = _learning_client(client, monkeypatch, days=5)
    calls = []
    real = state.fleet_facts.day
    state.fleet_facts.day = lambda car, ds: calls.append(ds) or real(car, ds)
    bundle = views._bundle(state)
    span = (TODAY - timedelta(days=5), TODAY - timedelta(days=1))
    first = views._learning_days(state, bundle, *span)
    assert len(calls) == 5
    again = views._learning_days(state, bundle, *span)
    assert len(calls) == 5 and [x[3] for x in again] == [x[3] for x in first]
    changed = (TODAY - timedelta(days=2)).isoformat()
    real_version = state.fleet_facts.version
    state.fleet_facts.version = lambda car, ds: (ds, 'new') if ds == changed else real_version(car, ds)
    views._learning_days(state, bundle, *span)
    assert calls[5:] == [changed]                                          # пересчитан только изменённый день
    st = client.get('/api/routes/learning/status').get_json()
    assert st['success'] and 'days' not in st and {'status', 'job', 'warning'} <= set(st)


# ============================== M5: проверяется применяемый профиль ==============================

def _hour_legs(per_hour, days=40, depart=None):
    """Участки по 8 км (19,2 мин модели) с выездом в начале часа; per_hour — множитель факта по часу."""
    out = []
    for d in _days(days):
        for hour, r in per_hour.items():
            start = depart if depart is not None else hour * 60.0
            model = 8.0 / 25.0 * 60
            out.append(lr.LegObs(d, True, d.weekday() >= 5, int(start // 60), model * r, model, model, 8.0, 25.0,
                                 d.weekday(), start))
    return out


def test_m5_travel_scored_with_applied_profile_across_hours():
    train = [o for o in _hour_legs({9: 1.0, 10: 2.0}) if o.day < TODAY - timedelta(days=lr.HOLDOUT_DAYS)]
    # проверка: выезд в 9:50 — участок уходит в 10:00, где машины едут вдвое медленнее; факт = как у профиля
    profile_true = TrafficProfile({(True, 0, 9): 1.0, (True, 1, 9): 1.0, (True, 0, 10): 0.5, (True, 1, 10): 0.5}, {})
    test = []
    for d in _days(7, start=TODAY - timedelta(days=7)):
        for _ in range(4):
            actual = profile_true.travel(8.0, 25.0, True, d.weekday(), 9 * 60 + 50)
            test.append(lr.LegObs(d, True, d.weekday() >= 5, 9, actual, 19.2, 19.2, 8.0, 25.0, d.weekday(), 590.0))
    o = lr.fit_travel(train + test, TODAY, 'straight', REF, BASE)
    applied = lr.travel_profile(BASE, o.params)
    expected = sum(abs(applied.travel(8.0, 25.0, True, x.weekday, x.start) - x.minutes) for x in test) / len(test)
    assert o.mae_after == pytest.approx(expected, abs=1e-3)
    assert o.mae_after < 0.5 and o.accepted                    # 9:50 → через границу часа: 10 мин × 1 + остальное × 2
    naive = sum(abs(x.model * 1.0 - x.minutes) for x in test) / len(test)
    assert naive > 5                                            # прежняя проверка (модель × множитель часа выезда)


def test_m5_previous_hours_carried_forward():
    prev = {'factors': [[1, 0, 15, 1.4], [1, 0, 9, 2.5]], 'ref': {'speed_city_kmh': 20.0, 'speed_region_kmh': 45.0,
                                                                  'detour': 1.3}, 'model_id': 'straight'}
    o = lr.fit_travel(_hour_legs({9: 1.2, 10: 1.2, 11: 1.2}), TODAY, 'straight', REF, BASE, prev)
    factors = {(c, w, h): r for c, w, h, r in o.params['factors']}
    assert factors[(1, 0, 9)] == 1.2                                         # новый час заменяет прежний
    assert factors[(1, 0, 15)] == pytest.approx(1.4 * (1.3 / 20) / (1.3 / 25), abs=1e-3)   # перенесён к новым скоростям
    assert lr.valid_params('travel', o.params)


# ============================== M6: карта «план — факт» ==============================

def test_m6_simplify_track():
    line = [(40.18 + i * 1e-5, 44.5 + (i % 2) * 1e-7) for i in range(5000)]   # почти прямая
    s = ac.simplify(line, 1500)
    assert s[0] == line[0] and s[-1] == line[-1] and len(s) <= 1500 and len(s) < 50
    zig = [(40.18 + i * 1e-4, 44.5 + (i % 2) * 1e-3) for i in range(3000)]
    s2 = ac.simplify(zig, 1500)
    assert len(s2) <= 1500 and s2[0] == zig[0] and s2[-1] == zig[-1]
    assert ac.simplify(line[:10], 1500) == line[:10]


def test_m6_day_map_api(client, monkeypatch):
    state = _learning_client(client, monkeypatch, days=3)
    ds = (TODAY - timedelta(days=1)).isoformat()
    state.store.save_dispatch(ds, {'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [101, 102]},
                                             {'id': 2, 'truck': 'CAR1', 'stops': [104]}],
                                   'prediction': {'trucks': {'CAR1': {'trips': [
                                       {'loading_start': '08:30', 'depart': '08:50', 'return': '10:00',
                                        'stops': [[101, '09:05'], [102, '09:30']]},
                                       {'loading_start': '10:00', 'depart': '10:20', 'return': '11:30',
                                        'stops': [[104, '10:45']]}]}}}}, 'qa')
    d = client.get(f'/api/routes/learning/day?date={ds}&car=CAR1').get_json()
    assert d['success'] and len(d['track']) <= views.MAP_TRACK_POINTS and d['track_points'] > 100
    assert d['depot'] == list(DEPOT) and len(d['planned']) == 2 and d['planned'][0][0] == list(DEPOT)
    by = {s['customer_id']: s for s in d['stops']}
    assert by[101]['planned_eta'] == '09:05' and by[101]['arrive'] and by[101]['late_min'] == 0
    assert len(d['trips']) == 2 and d['trips'][1]['load_min'] is not None
    assert client.get('/api/routes/learning/day?date=bad&car=CAR1').status_code == 400
    assert client.get(f'/api/routes/learning/day?date={ds}').status_code == 400


# ============================== L1–L4: восстановление факта ==============================

def test_l1_short_visit_to_neighbour_not_swallowed():
    b2 = (A[0] + 120 / 111195, A[1])
    stops = [ac.PlanStop('A', 101, A, 500.0, 500.0, None, 0), ac.PlanStop('B', 102, b2, 200.0, 200.0, None, 1)]
    tr = Track(DEPOT, _t(8)).stay(10).drive(A, 30).stay(10)
    tr.drive(b2, 25, step=5).stay(2.2, step=10).drive(A, 25, step=5).stay(3)
    tr.drive(DEPOT, 30).stay(5)
    day = ac.reconstruct(tr.fixes, stops, DEPOT)
    assert [v.keys for v in day.visits] == [('A',), ('B',), ('A',)]
    assert [v.repeat for v in day.visits] == [False, False, True]


def test_l2_midnight_arrival_is_late():
    stops = [ac.PlanStop('A', 101, A, 500.0, 500.0, (540, 1080), 0)]
    tr = Track(DEPOT, _t(23, 50)).stay(5).drive(A, 30).stay(10).drive(DEPOT, 30).stay(5)
    day = ac.reconstruct(tr.fixes, stops, DEPOT)
    assert day.visits[0].arrive.date() == D + timedelta(days=1)
    m = ac.visit_metrics(day, stops, D)
    assert (m.with_window, m.on_time) == (1, 0) and m.late_minutes > 6 * 60


def test_l3_early_arrival_for_hard_window_is_a_miss():
    tr = Track(DEPOT, _t(13)).stay(10).drive(A, 30).stay(20).drive(DEPOT, 30).stay(5)   # у A ≈ 13:20–13:40
    after = _stops(A={'window': (14 * 60, float('inf'))})
    m = ac.visit_metrics(ac.reconstruct(tr.fixes, after, DEPOT), after, D)
    assert (m.with_window, m.on_time, m.early) == (1, 0, 1)
    # водитель ждал у магазина и отметил доставку после 14:00 — вовремя
    waited = _stops(A={'window': (14 * 60, float('inf')), 'delivered_at': _t(14, 2)})
    tr = Track(DEPOT, _t(13)).stay(10).drive(A, 30).stay(50).drive(DEPOT, 30).stay(5)
    m = ac.visit_metrics(ac.reconstruct(tr.fixes, waited, DEPOT), waited, D)
    assert (m.on_time, m.early) == (1, 0)
    at = _stops(A={'window': (13 * 60 + 45, 14 * 60 + 15)})                            # «в 14:00 ± 15»
    tr = Track(DEPOT, _t(13)).stay(10).drive(A, 30).stay(10).drive(DEPOT, 30).stay(5)
    assert ac.visit_metrics(ac.reconstruct(tr.fixes, at, DEPOT), at, D).early == 1


def test_l4_stop_without_gps_fix_stays_in_load():
    tr = Track(DEPOT, _t(8, 30)).stay(20).drive(A, 30).stay(10).drive(B, 30, gap=(0, 10 ** 6)).stay(8, gap=(0, 10 ** 6))
    tr.drive(C, 30).stay(10).drive(DEPOT, 30).stay(5)
    day = ac.reconstruct(tr.fixes, _stops(), DEPOT)
    assert [v.keys for v in day.visits] == [('A',), ('C',)]
    assert [t.loaded_kg for t in day.trips] == [3500.0] and day.trips[0].unseen == ('B',)
    assert [round(kg) for _, _, kg in ac.load_profile(day, _stops())] == [3500, 2500, 0]
    # отметка доставки во время рейса — тоже в груз (место в плане неизвестно)
    no_rank = _stops(B={'rank': None, 'delivered_at': _t(9, 0)})
    assert ac.reconstruct(tr.fixes, no_rank, DEPOT).trips[0].loaded_kg == 3500.0


# ============================== L5: ночной прогон и часы Еревана ==============================

def test_l5_missed_run_rule():
    at = lambda h, m=0, d=3: datetime(2026, 10, d, h, m, tzinfo=TZ)   # noqa: E731
    assert lr.missed_run(at(10), None)
    assert lr.missed_run(at(10), '2026-10-02') and not lr.missed_run(at(10), '2026-10-03')
    assert not lr.missed_run(at(2, 59), '2026-10-02') and lr.missed_run(at(2, 59), '2026-10-01')
    assert not lr.missed_run(at(3, 0), '2026-10-03')


@pytest.mark.filterwarnings('ignore::pytest.PytestUnhandledThreadExceptionWarning')   # поток теста гасим SystemExit
def test_l5_scheduler_catches_up_missed_run(client, monkeypatch):
    app = client.application
    state = app.extensions['route_optimizer']
    calls, waits = [], []

    def fake_sleep(seconds):
        waits.append(seconds)
        if len(waits) > 1:
            raise SystemExit
    monkeypatch.setenv('ROUTES_LEARNING_NIGHTLY', '1')
    monkeypatch.setattr(ro.time, 'sleep', fake_sleep)
    monkeypatch.setattr(ro, 'run_learning_job', lambda st, day, user: calls.append(user) or True)
    thread = ro.start_learning_scheduler(app)                         # прогонов ещё не было — догнать
    thread.join(5)
    assert waits[0] == ro.CATCHUP_DELAY_S and calls == ['nightly']
    state.store.save_learning_last_run(datetime.now(TZ).date().isoformat())
    calls.clear()
    waits.clear()
    if datetime.now(TZ).hour >= 3:
        thread = ro.start_learning_scheduler(app)                     # сегодняшний прогон уже был — ждать 03:00
        thread.join(5)
        assert waits[0] == pytest.approx((lr.next_run(datetime.now(TZ)) - datetime.now(TZ)).total_seconds(), abs=5)
        assert waits[0] > ro.CATCHUP_DELAY_S


def test_l5_run_button_uses_yerevan_date(client, monkeypatch):
    state = _learning_client(client, monkeypatch, days=3)
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 2, 22, 30))          # часы сервера — ещё вчера
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 3, 1, 30, tzinfo=TZ))
    seen = []
    monkeypatch.setattr(views, 'run_learning', lambda st, today: seen.append(today) or [])
    assert client.post('/api/routes/learning/run', json={}).status_code == 200
    for _ in range(100):
        if state.learning_job.get('status') == 'done':
            break
        __import__('time').sleep(0.02)
    assert seen == [date(2026, 10, 3)] and state.store.learning_last_run() == '2026-10-03'


# ============================== L7, L8, пробел: «Развоз» и выученные нормы ==============================

def test_l7_bad_learned_row_does_not_break_dispatch(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    state.store.save_learned('2026-09-01', [lr.Outcome('unload', '', True, 'да', {'per_stop_min': 'x',
                                                                                  'per_tonne_min': 6})])
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200                                             # битые параметры — строка не действует
    with closing(sqlite3.connect(state.store.path)) as conn:                # битый JSON журнала — StoreError чтения
        conn.execute("UPDATE learned_norms SET params = '{oops'")
        conn.commit()
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200
    assert state.learning_warning and 'настроек' in state.learning_warning['text']
    page = client.get('/api/routes/learning/status')                         # страница показывает, что журнал битый
    assert page.status_code == 500 and 'повреждена' in page.get_json()['error']


def _offset_row(cid, minutes):
    return lr.Outcome('unload', '', True, 'да', {'per_stop_min': 8.0, 'per_tonne_min': 6.0,
                                                 'store_offsets': {str(cid): minutes}})


def test_gap_load_day_passes_customer_points_to_learned_offsets(client, monkeypatch):
    """Настоящий путь «Собрать рейсы» (_load_day → _dispatch_ctx → _with_learned): поправка магазина доходит до рейса."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    base = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1']}).get_json()
    seen = []
    real = lr.apply_learned
    monkeypatch.setattr(lr, 'apply_learned', lambda *a: seen.append(a[4]) or real(*a))
    state.store.save_learned('2026-09-01', [_offset_row(101, 25.0)])
    got = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1']}).get_json()
    assert seen and 101 in seen[-1]
    minutes = lambda p: sum(tr['minutes'] for t in p['plan']['trucks'] for tr in t['trips'])   # noqa: E731
    assert minutes(got) == pytest.approx(minutes(base) + 25, abs=1)


def test_l8_past_day_fact_passes_customer_points(client, monkeypatch):
    from route_optimizer.dispatch import ShippedDoc
    _dispatch_setup(client, [_dorder(1, 101, 400.0)], docs=[ShippedDoc(101, 1, 'CAR1', 1000.0, 300.0),
                                                           ShippedDoc(102, 2, 'CAR2', 500.0, 200.0)])
    state = client.application.extensions['route_optimizer']
    state.store.save_learned('2026-09-01', [_offset_row(101, 10.0)])
    seen = []
    real = lr.apply_learned
    monkeypatch.setattr(lr, 'apply_learned', lambda *a: seen.append(a[4]) or real(*a))
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 3, 10, 0))
    r = client.get('/api/routes/dispatch/fact?date=2026-10-01')
    assert r.status_code == 200, r.get_json()
    assert seen and {101, 102} <= set(seen[-1])


def test_gap_split_unload_keeps_store_offset_per_trip():
    """Тяжёлый заказ на несколько поездок: поправка магазина — в каждой поездке (fleet._split_unload)."""
    norms = BASE
    tn = fl.TruckNorms(work_minutes=540.0, unload_min_per_stop=8.0, unload_min_per_tonne=6.0)
    trucks = [fl.FleetTruck('T1', 'HOWO', 5000.0, 28.0)]
    pts, kgs, revs = [A, B], [9000.0, 300.0], [9000.0 * 120, 300.0 * 120]          # A — две поездки
    plain = fl.route_day(pts, kgs, revs, DEPOT, trucks, norms, tn)
    extra = fl.route_day(pts, kgs, revs, DEPOT, trucks, norms, replace(tn, unload_extra={A: 7.0}))
    visits_a = sum(1 for t in plain for i in t.items if i == 0)
    assert visits_a >= 2 and [t.items for t in extra] == [t.items for t in plain]
    assert sum(t.minutes for t in extra) == pytest.approx(sum(t.minutes for t in plain) + 7.0 * visits_a)


# ============================== L10, L11, утечка обучения в проверку ==============================

def test_l11_fuel_form_chosen_on_train_only():
    """Обучение — зависимость от загрузки, проверка — другой процесс (плоский 40 л): форма и параметры — по обучению."""
    rnd = random.Random(5)
    loads = [0.1, 0.6, 0.35, 0.8] * 6
    obs = [lr.FuelObs(d, u, 20 + 12 * u + rnd.uniform(-0.3, 0.3)) for d, u in zip(_days(20), loads)]
    obs += [lr.FuelObs(d, u, 40.0) for d, u in zip(_days(4, start=TODAY - timedelta(days=4)), loads)]
    o = lr.fit_fuel(obs, lambda u: 30.0, 'CAR1')
    assert o.params == {'empty_l100': pytest.approx(20, abs=1), 'full_l100': pytest.approx(32, abs=1)}
    assert not o.accepted and o.test_from == (TODAY - timedelta(days=4)).isoformat()


def test_leakage_holdout_never_changes_learned_params():
    """Проверочная неделя — другой процесс: выученные параметры те же, что без неё (утечки нет)."""
    test_from = TODAY - timedelta(days=lr.HOLDOUT_DAYS)
    rnd = random.Random(2)
    train_u = [lr.UnloadObs(d, 1, t, 4 + 12 * t + rnd.uniform(-0.3, 0.3), (100 + i,))
               for d in _days(40) if d < test_from for i, t in enumerate((0.2, 0.5, 0.9, 1.4))]
    test_u = [lr.UnloadObs(d, 1, t, 25.0, (100 + i,)) for d in _days(7, start=test_from)
              for i, t in enumerate((0.2, 0.5, 0.9, 1.4))]
    cur = lambda x: 8 + 6 * x.tonnes   # noqa: E731
    with_test = lr.fit_unload(train_u + test_u, cur, TODAY)
    assert with_test.params == lr.fit_unload(train_u + [replace(o, minutes=10.0) for o in test_u], cur, TODAY).params
    assert with_test.params['per_stop_min'] == pytest.approx(4, abs=0.5)
    train_l = [lr.LoadObs(d, t, 10 + 5 * t) for d in _days(40) if d < test_from for t in (1.0, 3.0)]
    test_l = [lr.LoadObs(d, t, 25.0) for d in _days(7, start=test_from) for t in (1.0, 3.0)]   # другой процесс
    a = lr.fit_loading(train_l + test_l, TODAY, (10.0, 5.0))
    b = lr.fit_loading(train_l + [replace(o, minutes=6.0) for o in test_l], TODAY, (10.0, 5.0))
    assert a.params == b.params and a.params['fixed_min'] == pytest.approx(10, abs=0.5)
    c = lr.fit_loading(train_l + test_l, TODAY)                            # опора-медиана — тоже только по обучению
    assert c.params == lr.fit_loading(train_l + [replace(o, minutes=6.0) for o in test_l], TODAY).params
    legs = _hour_legs({9: 1.3}, days=40)
    other = [replace(o, minutes=o.model * 2.5) if o.day >= test_from else o for o in legs]
    assert lr.fit_travel(legs, TODAY, 'straight', REF, BASE).params == lr.fit_travel(other, TODAY, 'straight', REF,
                                                                                         BASE).params


def test_l7_valid_params_rejects_broken_rows():
    assert lr.valid_params('unload', {'per_stop_min': 8, 'per_tonne_min': 6, 'store_offsets': {'101': 5.0}})
    assert not lr.valid_params('unload', {'per_stop_min': 8, 'per_tonne_min': 6, 'store_offsets': {'x': 5.0}})
    assert not lr.valid_params('loading', {'fixed_min': -1, 'per_tonne_min': 4})
    assert not lr.valid_params('fuel', {'empty_l100': 30, 'full_l100': 20})
    assert not lr.valid_params('travel', {'factors': [[1, 0, 25, 1.2]], 'ref': REF})
    assert not lr.valid_params('travel', {'factors': [], 'ref': REF})
    assert lr.valid_params('travel', {'factors': [[1, 0, 9, 1.2]], 'ref': REF})
    assert not lr.valid_params('unload', None) and not lr.valid_params('nope', {})

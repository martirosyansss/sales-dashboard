# -*- coding: utf-8 -*-
"""Обучение «как у профессионалов» (ответ владельца №66, docs/plans/learning-pro-plan.md, части 1 и 3).

- запас на рейс (вид buffer): правило шкалы времени (fleet._schedule) — рейс занимает D + c·√D (≤ 40% D), прибытия к
  точкам — по медиане, следующий рейс и конец дня — с запасом; plan_view показывает запас; сборка и решатель не
  планируют за конец дня; обучение — q-квантиль (факт − D)/√D, проверка — pinball-потеря и покрытие, правило принятия
  с бутстрепом; действует только при том q настроек, при котором проверен; q = 50 — без запаса;
- темп машины (виды truck_unload, truck_travel): множители exp(сглаженного среднего log(факт / прогноз)) по
  машино-дням, empirical Bayes к 1; применение — разгрузка и минуты участков машины в шкале (и профилем PyVRP);
- синтетические GPS-дни с известной правдой (tests/learning_pro_sim.py: медленный экипаж CAR4, шум со связью внутри
  дня): покрытие запаса ≈ 80% на проверке и на новых днях, медленная машина найдена, у остальных ≈ 1, проверка
  принимает оба вида;
- без выученных строк — «Развоз» прежний до байта; схема маршрутов 20 → 21 (виды журнала) — строки переносятся.

Синтетические данные; ERP не читается; базы — временные.  Запуск:  python -m pytest tests/test_learning_pro.py -q
"""
import math
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import learning_pro_sim as sim  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import vrp  # noqa: E402
from test_route_dispatch_lunch import JAC, _ctx, _random_day, _two_trips, _violations  # noqa: E402
from test_route_optimizer import client, DP_DAY, DP_DEPOT, DP_NORMS, EAST, FORD, HOWO, TN, WEST, _dp_stops, _info  # noqa: E402

TODAY = date(2026, 10, 3)
INF = float('inf')


# ============================== правило шкалы времени ==============================

def _line(drives, unload=10.0, windows=None):
    pos = [0.0]
    for x in drives:
        pos.append(pos[-1] + x)
    m = [[abs(a - b) for b in pos] for a in pos]
    stops = [fl._Stop(k + 1, 0.0, 0.0, unload, *((windows or {}).get(k, (0.0, INF)))) for k in range(len(drives))]
    return list(range(len(drives))), stops, m


def test_trip_reserve_rule():
    assert fl.trip_reserve(0.0, 200.0) == 0.0 and fl.trip_reserve(-1.0, 200.0) == 0.0 and fl.trip_reserve(2.0, 0.0) == 0.0
    assert fl.trip_reserve(2.0, 100.0) == pytest.approx(20.0)                       # c·√D
    assert fl.trip_reserve(2.0, 400.0) == pytest.approx(40.0)                       # √: вдвое длиннее рейс — запас ×√4
    assert fl.trip_reserve(20.0, 100.0) == pytest.approx(fl.BUFFER_CAP_REL * 100)   # не больше 40% D
    assert fl.TruckNorms(540.0, 8.0, 6.0, buffer_c=3.0).reserve(100.0) == pytest.approx(30.0)


def test_schedule_buffer_at_trip_end_arrivals_median():
    seq, stops, m = _line([30, 20, 40], windows={1: (70.0, INF)})
    a0, a1, p1 = [], [], {}
    plain, ok0 = fl._schedule(seq, stops, m, 0.0, a0)
    buffered, ok1 = fl._schedule(seq, stops, m, 0.0, a1, p1, buffer=3.0)
    assert ok0 and ok1 and a0 == a1                                                 # прибытия — по медиане
    assert p1['buffer'] == pytest.approx(3.0 * math.sqrt(plain)) and buffered == pytest.approx(plain + p1['buffer'])
    assert fl._schedule(seq, stops, m, 0.0, buffer=0.0) == (plain, ok0)             # c = 0 — тот же расчёт


def test_schedule_pace_scales_drive_and_unload():
    seq, stops, m = _line([30, 20, 40])
    parts, arr = {}, []
    minutes, _ = fl._schedule(seq, stops, m, 0.0, arr, parts, pace=(1.5, 1.2))
    assert [x for x, _, _ in parts['legs']] == pytest.approx([36.0, 24.0, 48.0, 108.0])   # участки × 1,2
    assert [u for _, _, u in parts['legs'][:-1]] == pytest.approx([15.0] * 3)               # разгрузка × 1,5
    assert minutes == pytest.approx(36 + 24 + 48 + 108 + 45) and arr == pytest.approx([36.0, 75.0, 138.0])
    assert fl._schedule(seq, stops, m, 0.0, pace=fl.NO_PACE) == fl._schedule(seq, stops, m, 0.0)


def test_savings_merge_respects_reserve():
    """Ревью L6: Кларк–Райт сливает рейсы без окон, только если езда + разгрузка + запас укладываются в день."""
    seq, stops, m = _line([30, 5])                                                  # слитый рейс: 30 + 5 + 35 + 20 = 90
    assert fl._savings(seq, stops, m, m, 1e9, 95.0) == [[0, 1]]
    tn = replace(TN, buffer_c=1.0)                                                  # запас √90 ≈ 9,5 → 99,5 > 95
    assert fl._savings(seq, stops, m, m, 1e9, 95.0, reserve=tn.reserve) == [[0], [1]]
    assert fl._savings(seq, stops, m, m, 1e9, 95.0, reserve=TN.reserve) == [[0, 1]]


def test_lunch_trip_carries_buffer_and_pace_of_truck():
    seq, stops, m = _line([60, 60, 60, 60])
    tn = replace(TN, lunch_minutes=30.0, lunch_from=210.0, lunch_to=330.0, buffer_c=2.0, pace={'SLOW': (1.0, 1.25)})
    _, base, _, _ = fl._lunch_trip(seq, stops, m, 0.0, replace(tn, buffer_c=0.0, pace={}), True, False)
    _, buffered, _, brk = fl._lunch_trip(seq, stops, m, 0.0, replace(tn, pace={}), True, False)
    assert brk is not None and buffered == pytest.approx(base + fl.trip_reserve(2.0, base))
    _, slow, _, _ = fl._lunch_trip(seq, stops, m, 0.0, tn, True, False, code='SLOW')
    _, other, _, _ = fl._lunch_trip(seq, stops, m, 0.0, tn, True, False, code='FAST')
    assert other == buffered and slow > other


def test_timeline_buffer_pushes_next_trip_and_plan_view_shows_it():
    stops, draft = _two_trips()
    routable = {s.customer_id: s for s in stops}
    tn = replace(TN, buffer_c=3.0)
    base = dp._timeline(_ctx(TN), draft.trips, routable, dp._shares(draft.trips))
    parts = {}
    tl = dp._timeline(_ctx(tn), draft.trips, routable, dp._shares(draft.trips), parts)
    r1 = parts[1]['buffer']
    assert r1 == pytest.approx(fl.trip_reserve(3.0, base[1][1])) and r1 > 5
    assert tl[1][2] == base[1][2] and tl[1][1] == pytest.approx(base[1][1] + r1)   # точки — по медиане, рейс — с запасом
    assert tl[2][0] == pytest.approx(base[2][0] + r1)                               # второй рейс — позже на запас
    view = dp.plan_view(_ctx(tn), stops, draft, _info)
    t1, t2 = view['trucks'][0]['trips']
    assert t1['buffer'] == {'minutes': round(r1, 1), 'start': dp._hhmm(9 * 60 + base[1][0] + base[1][1])}
    assert t1['return'] == dp._hhmm(9 * 60 + tl[1][0] + tl[1][1])
    x = t1['explain']
    assert x['buffer_min'] == round(r1, 1)
    assert x['loading_min'] + x['drive_min'] + x['unload_min'] + x['wait_min'] + x['buffer_min'] == \
        pytest.approx(t1['minutes'], abs=1.0)
    slow = dp._timeline(_ctx(replace(TN, pace={HOWO.car_code: (1.2, 1.3)})), draft.trips, routable,
                        dp._shares(draft.trips))
    assert slow[1][1] > base[1][1] * 1.19 and slow[1][2][0] > base[1][2][0]        # темп машины — в шкале «Развоза»
    end = tl[2][0] + tl[2][1]
    tight = replace(tn, work_minutes=end - 1.0)
    assert dp.runs_late(_ctx(tight), stops, draft) and not dp.runs_late(_ctx(replace(TN, work_minutes=end - 1.0)),
                                                                        stops, draft)


def test_prediction_saves_median_return_and_buffer(monkeypatch):
    """Ревью M1: в прогнозе для «план — факт» возвращение — по медиане, запас — отдельно: обучение загрузки (плановое
    ожидание на складе — от медианного возвращения) и «время работы» плана запаса не видят."""
    from types import SimpleNamespace
    from route_optimizer import views
    stops, draft = _two_trips()
    monkeypatch.setattr(views, '_stop_info', lambda dd: _info)
    preds = {}
    for c in (0.0, 3.0):
        dd = SimpleNamespace(ctx=_ctx(replace(TN, buffer_c=c)), stops=stops, day=DP_DAY,
                             bundle=SimpleNamespace(settings={'truck_work_start': '09:00'}))
        d = dp.Draft(trucks=draft.trucks, trips=[dp.DraftTrip(t.id, t.truck, list(t.stops)) for t in draft.trips])
        views._capture_prediction(dd, d)
        preds[c] = d.prediction['trucks'][HOWO.car_code]
    view = dp.plan_view(_ctx(replace(TN, buffer_c=3.0)), stops, draft, _info)
    t1 = view['trucks'][0]['trips'][0]
    got = preds[3.0]
    assert got['trips'][0]['return'] == t1['buffer']['start'] != t1['return'] and got['trips'][0]['buffer'] == t1['buffer']['minutes']
    assert got['return'] == view['trucks'][0]['trips'][-1]['buffer']['start']
    assert 'buffer' not in preds[0.0]['trips'][0] and preds[0.0]['trips'][0]['return'] == preds[0.0]['trips'][0]['return']
    # второй рейс грузится после возвращения с запасом: плановое ожидание на складе — от медианного возвращения
    plan = lr.plan_trips(got, DP_DAY)
    assert plan[1].wait is not None and (plan[1].wait[1] - plan[1].wait[0]).total_seconds() / 60 == pytest.approx(
        t1['buffer']['minutes'], abs=1.0)
    assert lr._plan_minutes(got) < lr._plan_minutes({**got, 'return': view['trucks'][0]['return']})


def test_route_trip_window_check_with_truck_pace(monkeypatch):
    """Ревью L1: ручная перестановка (route_trip с окнами) проверяет окна с темпом машины рейса."""
    seen = []
    real = fl._schedule
    monkeypatch.setattr(fl, '_schedule', lambda *a, **k: seen.append(k.get('pace')) or real(*a, **k))
    pts = [p for _, p, _ in EAST]
    tn = replace(TN, pace={HOWO.car_code: (1.3, 1.2)})
    fl.route_trip(pts, [200.0] * 3, DP_DEPOT, DP_NORMS, tn, windows=[(0.0, INF)] * 3, truck=HOWO.car_code)
    assert seen and all(p == (1.3, 1.2) for p in seen)
    seen.clear()
    fl.route_trip(pts, [200.0] * 3, DP_DEPOT, DP_NORMS, tn, windows=[(0.0, INF)] * 3, truck=FORD.car_code)
    assert seen and all(p == fl.NO_PACE for p in seen)


def test_no_buffer_no_pace_plan_identical():
    """Без выученных строк: процентиль в настройках (80) без c и пустой темп — план до байта прежний."""
    stops, draft = _two_trips()
    base = dp.plan_view(_ctx(TN), stops, draft, _info)
    same = replace(TN, buffer_pct=80.0, buffer_c=0.0, pace={'OTHER': (1.3, 1.2)})
    assert dp.plan_view(_ctx(same), stops, draft, _info) == base
    for seed in range(3):
        s, windows, trucks = _random_day(seed)
        a = dp.build(_ctx(TN, trucks, windows), s, None, [t.car_code for t in trucks], 'now')
        b = dp.build(_ctx(same, trucks, windows), s, None, [t.car_code for t in trucks], 'now')
        assert a.to_json() == b.to_json(), seed


def test_build_with_buffer_and_pace_never_violates():
    """Сборка (с решателем, выравниванием и доводкой) с запасом и темпом: ни одного рейса позже конца дня и мимо окна по
    точной шкале; не поместилось — «не поместились»."""
    slow = {HOWO.car_code: (1.3, 1.25), JAC.car_code: (0.9, 1.0)}
    for seed in range(5):
        stops, windows, trucks = _random_day(seed)
        for tn in (replace(TN, buffer_c=2.5, pace=slow),
                   replace(TN, buffer_c=2.5, lunch_minutes=30.0, lunch_from=210.0, lunch_to=330.0, pace=slow)):
            ctx = _ctx(tn, trucks, windows, overtime=660.0)
            draft = dp.build(ctx, stops, None, [t.car_code for t in trucks], 'now')
            late, miss, _ = _violations(dp.plan_view(ctx, stops, draft, _info))
            assert (late, miss) == (0, 0) and dp.runs_late(ctx, stops, draft) is False, seed


def test_buffer_needs_more_room():
    """Без запаса всё помещается в день одной машины, с запасом — нет: остальное не планируется (no_room)."""
    stops, _ = _dp_stops(EAST + WEST)
    plain = dp.build(_ctx(TN), stops, None, [HOWO.car_code], 'now')
    end = dp.plan_view(_ctx(TN), stops, plain, _info)['trucks'][0]['minutes']
    tight = replace(TN, work_minutes=end + 5.0)
    assert not dp.build(_ctx(tight), stops, None, [HOWO.car_code], 'now').no_room
    draft = dp.build(_ctx(replace(tight, buffer_c=4.0)), stops, None, [HOWO.car_code], 'now')
    view = dp.plan_view(_ctx(replace(tight, buffer_c=4.0)), stops, draft, _info)
    assert draft.no_room and view['trucks'][0]['minutes'] <= tight.work_minutes


def test_plan_trips_plain_path_uses_buffer_and_pace():
    """Прежний путь plan_trips (без окон и часовой модели): рейс машины — её темп и запас; день не переполняется."""
    pts = [p for _, p, _ in EAST + WEST]
    kgs = [200.0] * 6
    trucks = [replace(FORD, car_code='F1'), replace(FORD, car_code='F2')]
    base = fl.route_day(pts, kgs, [1.0] * 6, DP_DEPOT, trucks, DP_NORMS, TN, overflow=False)
    tn = replace(TN, buffer_c=3.0, pace={'F1': (1.5, 1.5)})
    trips = fl.route_day(pts, kgs, [1.0] * 6, DP_DEPOT, trucks, DP_NORMS, tn, overflow=False)
    assert sorted(i for t in trips for i in t.items) == sorted(i for t in base for i in t.items)
    used: dict[str, float] = {}
    for t in trips:
        used[t.truck] = used.get(t.truck, 0.0) + t.minutes
    assert all(v <= tn.work_minutes + 1e-6 for v in used.values())
    for t in trips:
        seq = list(t.items)
        drive = fl.route_trip([pts[i] for i in seq], [kgs[i] for i in seq], DP_DEPOT, DP_NORMS, TN, reorder=False)[2]
        if t.truck == 'F2':
            assert t.minutes == pytest.approx(drive + fl.trip_reserve(3.0, drive))


@pytest.mark.skipif(not vrp.available(), reason='нет PyVRP')
def test_vrp_profiles_and_reserve_edges():
    """PyVRP: запас типичного рейса — на рёбрах «заказ → склад», темп машины — её профилем; без них — прежние рёбра."""
    pieces = [vrp.Piece(1, 100.0, 10.0, None, None, False, True), vrp.Piece(2, 100.0, 10.0, None, None, False, True)]
    km = [[0, 5, 5], [5, 0, 3], [5, 3, 0]]
    mins = [[0, 10, 10], [10, 0, 6], [10, 6, 0]]
    shifts = [vrp.Shift('A', 0.0, 100.0)]
    fast = [vrp.Vehicle('A', 1000.0, 10.0, False)]
    assert vrp.solve(pieces, km, mins, fast, shifts, [], None) is not None          # 10+10+6+10+10 = 46 ≤ 100
    assert vrp.solve(pieces, km, mins, fast, shifts, [], None, trip_reserve_min=60.0) is None   # 46 + 60 > 100
    slow = [vrp.Vehicle('A', 1000.0, 10.0, False, pace=(3.0, 2.0))]                 # 2·26 + 3·20 = 112 > 100
    assert vrp.solve(pieces, km, mins, slow, shifts, [], None) is None
    # касательная: езда и разгрузка × (1 + наклон) — 46 · 2 = 92 ≤ 100, 46 · 2,2 = 101,2 > 100
    assert vrp.solve(pieces, km, mins, fast, shifts, [], None, reserve_slope=1.0) is not None
    assert vrp.solve(pieces, km, mins, fast, shifts, [], None, reserve_slope=1.2) is None
    assert vrp.solve(pieces, km, mins, fast, shifts, [], None, reserve_slope=1.0, trip_reserve_min=9.0) is None


def test_tangent_is_upper_bound_of_reserve():
    """Линейная оценка запаса для PyVRP (_solver): c·√D0 / 2 + c / (2√D0) · D ≥ запас(D) при любом D, равна в D0."""
    for c in (0.5, 1.5, 2.36, 5.0):
        for d0 in (30.0, 150.0, 400.0):
            b, a = c * math.sqrt(d0) / 2, c / (2 * math.sqrt(d0))
            assert b + a * d0 == pytest.approx(c * math.sqrt(d0))
            assert all(b + a * d >= fl.trip_reserve(c, d) - 1e-9 for d in range(0, 800, 7))


@pytest.mark.skipif(not vrp.available(), reason='нет PyVRP')
def test_solver_with_buffer_tangent_and_retry(monkeypatch):
    """С запасом решатель получает касательную (наклон и полкасательной на рейс) по типичному рейсу сборки, не
    уложился по времени — вторая попытка с запасом × RESERVE_RETRY; по ожиданию у окон — без повтора."""
    calls = []
    real = fl._solver_try

    def spy(*a, **k):
        calls.append(dict(a[12]))
        got, why = real(*a, **k)
        return (None, 'time') if len(calls) == 1 else (got, why)
    monkeypatch.setattr(fl, '_solver_try', spy)
    stops, windows, trucks = _random_day(1)
    dp.build(_ctx(replace(TN, buffer_c=2.0), trucks, windows), stops, None, [t.car_code for t in trucks], 'now')
    assert len(calls) == 2 and calls[0]['reserve_slope'] > 0 and calls[0]['trip_reserve_min'] > 0
    assert calls[1]['reserve_slope'] == pytest.approx(fl.RESERVE_RETRY * calls[0]['reserve_slope'])
    d0 = (calls[0]['trip_reserve_min'] * 2 / 2.0) ** 2                              # c·√D0 / 2 → D0
    assert calls[0]['reserve_slope'] == pytest.approx(2.0 / (2 * math.sqrt(d0)))
    calls.clear()
    monkeypatch.setattr(fl, '_solver_try', lambda *a, **k: calls.append(1) or (None, 'wait'))
    dp.build(_ctx(replace(TN, buffer_c=2.0), trucks, windows), stops, None, [t.car_code for t in trucks], 'now')
    assert len(calls) == 1
    calls.clear()
    dp.build(_ctx(TN, trucks, windows), stops, None, [t.car_code for t in trucks], 'now')
    assert len(calls) == 1                                                          # без запаса — одна попытка


@pytest.mark.skipif(not vrp.available(), reason='нет PyVRP')
def test_solver_accepts_with_buffer_on_random_days():
    """Ревью H1: с запасом решатель принимается не реже, чем без него (на 10 случайных днях c = 2,36 — не меньше 3;
    без запаса — 4), и «не поместились» почти не растёт (до касательной — 38 против 18)."""
    accepted, unplaced = 0, 0
    real = fl._solver

    def count(*a, **k):
        nonlocal accepted
        got = real(*a, **k)
        accepted += got is not None
        return got
    fl._solver, saved = count, fl._solver
    try:
        for seed in range(10):
            stops, windows, trucks = _random_day(seed)
            ctx = _ctx(replace(TN, buffer_c=2.36, lunch_minutes=30.0, lunch_from=210.0, lunch_to=330.0), trucks, windows)
            d = dp.build(ctx, stops, None, [t.car_code for t in trucks], 'now')
            unplaced += len(d.no_room) + len(d.no_window)
    finally:
        fl._solver = saved
    assert accepted >= 3 and unplaced <= 30


# ============================== обучение запаса и темпа: синтетика ==============================

@pytest.fixture(scope='module')
def world():
    days = [TODAY - timedelta(days=i) for i in range(60, 0, -1)]
    cds = sim.simulate(days)
    tn = sim.truck_norms()
    trips, visits, legs = sim.observations(cds, tn)
    future = sim.simulate([TODAY + timedelta(days=i) for i in range(30)], seed=99)
    return cds, tn, trips, visits, legs, future


def test_simulation_buffer_coverage_and_gate(world):
    """(а) запас P80: c > 0, покрытие на отложенной неделе и на 30 новых днях ≈ 80%, pinball меньше, проверка
    принимает (бутстреп — устойчиво)."""
    cds, tn, trips, _, _, future = world
    assert len(trips) == 2 * len(cds)                                               # каждый рейс факта — наблюдение
    out = lr.fit_buffer(trips, TODAY, 80, 0.0)
    assert out.accepted and out.confidence >= lr.BOOT_SHARE and out.mae_after < 0.7 * out.mae_before
    p = out.params
    assert p['c'] > 1.0 and p['q'] == 80 and 0.72 <= p['coverage'] <= 0.88 and p['coverage_before'] < 0.62   # без запаса — около медианы
    assert 'նպատակը՝ 80%' in out.reason
    ahead, _, _ = sim.observations(future, tn)
    assert abs(sim.coverage(ahead, p['c']) - 0.80) <= 0.04 and sim.coverage(ahead, 0.0) < 0.62
    again = lr.fit_buffer(trips, TODAY, 80, p['c'])                                 # тот же запас уже действует
    assert not again.accepted and again.params['c'] == p['c']
    q90 = lr.fit_buffer(trips, TODAY, 90, 0.0).params
    assert q90['c'] > p['c'] and q90['coverage'] >= p['coverage']


def test_simulation_pace_finds_slow_crew_and_gate(world):
    """(б) темп: у медленного экипажа (правда — разгрузка ×1,30, путь ×1,25) — множители рядом с правдой, у остальных ≈ 1;
    проверка принимает оба вида; с темпом запас нужен меньше, покрытие — то же ≈ 80%."""
    _, tn, _, visits, legs, future = world
    u = lr.fit_pace('truck_unload', sim.unload_rows(visits, tn), TODAY)
    t = lr.fit_pace('truck_travel', sim.leg_rows(legs), TODAY, model_id='straight')
    assert u.accepted and t.accepted and u.confidence >= lr.BOOT_SHARE and t.confidence >= lr.BOOT_SHARE
    fu, ft = u.params['factors'], t.params['factors']
    (tu, tt), (ou, ot) = sim.truth(sim.SLOW), sim.truth('CAR1')
    assert abs(fu[sim.SLOW] - tu) <= 0.06 and abs(ft[sim.SLOW] - tt) <= 0.05
    others = [c for c in sim.CARS if c != sim.SLOW]
    assert all(abs(fu.get(c, 1.0) - ou) <= 0.05 and abs(ft.get(c, 1.0) - ot) <= 0.04 for c in others)
    # множители относительные: среднее логарифмов по машинам (дни у всех равны) — 0
    assert abs(sum(math.log(fu.get(c, 1.0)) for c in sim.CARS)) <= 0.02 * len(sim.CARS)
    assert lr.valid_params('truck_unload', u.params) and lr.valid_params('truck_travel', t.params)
    paced = sim.with_pace(tn, u, t)
    assert paced.pace_of(sim.SLOW) == (fu[sim.SLOW], ft[sim.SLOW]) and paced.pace_of('NEW') == fl.NO_PACE
    trips2, _, _ = sim.observations(world[0], paced)
    b = lr.fit_buffer(trips2, TODAY, 80, 0.0)
    assert b.accepted and b.params['c'] < lr.fit_buffer(world[2], TODAY, 80, 0.0).params['c']
    ahead, _, _ = sim.observations(future, paced)
    assert abs(sim.coverage(ahead, b.params['c']) - 0.80) <= 0.04
    again = lr.fit_pace('truck_unload', sim.unload_rows(visits, tn), TODAY, fu)     # те же множители уже действуют
    assert not again.accepted


def test_pace_factors_shrink_and_limits():
    d0 = TODAY - timedelta(days=40)

    def rows(car, days, ratio):
        return [lr.PaceObs(d0 + timedelta(days=i), car, 10.0, 10.0 * ratio * (1.04 if i % 2 else 0.96))
                for i in range(days)]
    train = rows('A', 30, 1.0) + rows('B', 30, 1.0) + rows('C', 30, 1.2) + rows('D', 2, 1.2)
    factors, k, days = lr.pace_factors(train)
    assert lr.PACE_K_BOUNDS[0] <= k <= lr.PACE_K_BOUNDS[1] and days == {'A': 30, 'B': 30, 'C': 30, 'D': 2}
    # относительно уровня парка: у A и C по 30 дней — их отношение ≈ exp(30 / (30 + k) · log 1,2)
    assert factors['C'] / factors['A'] == pytest.approx(math.exp(30 / (30 + k) * math.log(1.2)), abs=0.01)
    assert factors['A'] < 1.0 < factors['C'] and factors['A'] < factors['D'] < factors['C']   # 2 дня — ближе к 1
    assert factors['A'] == pytest.approx(factors.get('B', 1.0), abs=0.005)
    same = rows('A', 30, 1.0) + rows('B', 30, 1.0) + rows('C', 30, 1.0)
    assert lr.pace_factors(same)[1] == lr.PACE_K_BOUNDS[1]                          # τ² ≤ 0 — наибольшее сглаживание
    assert lr.pace_factors(rows('A', 30, 1.0) + rows('B', 30, 1.0)) is None          # две машины — k не оценить
    wild = rows('A', 30, 1.0) + rows('B', 30, 1.0) + rows('C', 30, 9.0)
    assert lr.pace_factors(wild)[0]['C'] == lr.PACE_RATIO[1]


def test_pace_factors_relative_to_fleet_level():
    """Шесть одинаковых машин, весь парк на 10,5% медленнее модели: это уровень парка (его учат unload / travel той же
    ночью), а не темп машин — множителей нет (иначе двойной счёт); медленная машина на фоне — относительно парка."""
    d0 = TODAY - timedelta(days=40)
    rows = [lr.PaceObs(d0 + timedelta(days=i), c, 10.0, 10.0 * 1.105 * (1.04 if (i + j) % 2 else 0.96))
            for j, c in enumerate(('A', 'B', 'C', 'D', 'E', 'F')) for i in range(30)]
    factors, _, _ = lr.pace_factors(rows)
    assert all(abs(f - 1.0) < 0.01 for f in factors.values())
    slow = [replace(o, minutes=o.minutes * 1.3) if o.car == 'F' else o for o in rows]
    f2, _, _ = lr.pace_factors(slow)
    level = math.exp(math.log(1.3) / 6)
    assert f2['F'] == pytest.approx(1.3 / level, abs=0.02) and f2['A'] == pytest.approx(1 / level, abs=0.02)


def test_buffer_c_for_q_change():
    """Ревью L2: сменили q — сразу c тех же рейсов обучения при новом q (c_by_q), а не ноль до ночи; q ≤ 50 — без
    запаса; у строки без таблицы (до ревью) другой q — без запаса."""
    d0 = TODAY - timedelta(days=40)
    obs = [lr.TripObs(d0 + timedelta(days=i % 40), 'A', 100.0, 100.0 + (i % 10) * 3) for i in range(200)]
    p = lr.fit_buffer(obs, TODAY, 80, 0.0).params
    assert p['c_by_q']['80'] == p['c'] and p['c_by_q']['95'] >= p['c'] >= p['c_by_q']['60'] and lr.valid_params('buffer', p)
    assert lr.buffer_c_for(p, 80.0) == p['c'] and lr.buffer_c_for(p, 90.0) == p['c_by_q']['90']
    assert lr.buffer_c_for(p, 50.0) is None and lr.buffer_c_for(None, 80.0) is None
    assert lr.buffer_c_for({'c': 2.0, 'q': 80}, 90.0) is None and lr.buffer_c_for(p, 85.5) is None
    assert not lr.valid_params('buffer', {**p, 'c_by_q': {'80': -1.0}})
    _, t2, _ = lr.apply_learned(DP_NORMS, replace(TN, buffer_pct=90.0), {}, lr.InEffect(buffer=p), {})
    assert t2.buffer_c == p['c_by_q']['90']


def test_fit_buffer_after_q_change_must_beat_no_buffer():
    """Ночь после смены q (строки при этом q нет, действует непроверенный c_by_q): новый c принимается, только если
    лучше и непроверенного, и «без запаса» — опора — лучшее из двух."""
    d0 = TODAY - timedelta(days=40)
    obs = [lr.TripObs(d0 + timedelta(days=i % 40), 'A', 100.0, 100.0 + (i % 10) * 3) for i in range(200)]
    good = lr.fit_buffer(obs, TODAY, 80, 0.0)
    assert good.accepted
    near = lr.fit_buffer(obs, TODAY, 80, good.params['c'], unchecked=True)         # непроверенный хорош — опора он
    assert near.mae_before == near.mae_after and not near.accepted
    early = [replace(o, minutes=200.0 - o.minutes) for o in obs]                    # рейсы раньше модели: запас 0
    wild = 15.0                                                                     # непроверенный — хуже, чем без запаса
    trusted = lr.fit_buffer(early, TODAY, 80, wild)
    gated = lr.fit_buffer(early, TODAY, 80, wild, unchecked=True)
    assert trusted.params['c'] == gated.params['c'] == 0.0
    assert trusted.accepted and trusted.mae_before > gated.mae_before                # было бы «лучше непроверенного»…
    assert not gated.accepted and gated.mae_before == gated.mae_after                # …но не лучше «без запаса»


def test_run_learning_gates_unchecked_buffer_after_q_change(client, monkeypatch):
    """Ночной прогон после смены q передаёт fit_buffer действующий c нового q (c_by_q) как непроверенный."""
    from route_optimizer import views
    from test_learning_loop import _learning_client
    state = _learning_client(client, monkeypatch)
    d0 = TODAY - timedelta(days=40)
    obs = [lr.TripObs(d0 + timedelta(days=i % 40), 'A', 100.0, 100.0 + (i % 10) * 3) for i in range(200)]
    row = lr.fit_buffer(obs, TODAY, 80, 0.0)
    state.store.save_learned('2026-10-01', [row])
    seen = []
    monkeypatch.setattr(views.learning, 'fit_buffer', lambda trips, today, q, cur=0.0, unchecked=False:
                        seen.append((q, cur, unchecked)) or lr.Outcome('buffer', '', False, 'x'))
    real = views._bundle
    for q in (80, 90):
        monkeypatch.setattr(views, '_bundle', lambda st, q=q: replace(real(st), settings={
            **real(st).settings, 'dispatch_buffer_pct': q}))
        views.run_learning(state, TODAY)
    assert seen == [(80.0, row.params['c'], False), (90.0, row.params['c_by_q']['90'], True)]


def test_learning_status_shows_buffer_used_after_q_change(client):
    """Сменили q: страница «Обучение» показывает c, которым считает «Развоз» сейчас (c_by_q нового q), этот q и запас
    типичного рейса — без покрытия и с пометкой «не проверен» (unchecked)."""
    from route_optimizer import views
    state = client.application.extensions['route_optimizer']
    d0 = TODAY - timedelta(days=40)
    obs = [lr.TripObs(d0 + timedelta(days=i % 40), 'A', 100.0, 100.0 + (i % 10) * 3) for i in range(200)]
    out = lr.fit_buffer(obs, TODAY, 80, 0.0)
    state.store.save_learned('2026-10-02', [out])
    bundle = state.store.load()
    same = next(x for x in views._learning_status(state, bundle) if x['kind'] == 'buffer')
    assert same['in_effect']['params'] == out.params
    other = replace(bundle, settings={**bundle.settings, 'dispatch_buffer_pct': 90})
    row = next(x for x in views._learning_status(state, other) if x['kind'] == 'buffer')
    p = row['in_effect']['params']
    assert p['unchecked'] and p['q'] == 90.0 and p['c'] == out.params['c_by_q']['90'] and 'coverage' not in p
    assert p['typical_reserve_min'] == round(fl.trip_reserve(p['c'], out.params['typical_min']), 1)
    assert row['last']['params'] == out.params                                      # прогон — как был
    off = replace(bundle, settings={**bundle.settings, 'dispatch_buffer_pct': 50})
    assert next(x for x in views._learning_status(state, off) if x['kind'] == 'buffer')['in_effect'] is None
    js = (ROOT / 'static' / 'js' / 'routes_learning.js').read_text(encoding='utf-8')
    assert "p.unchecked ? '․ չստուգված, կստուգվի գիշերը'" in js


def test_fit_buffer_rules():
    d0 = TODAY - timedelta(days=40)
    obs = [lr.TripObs(d0 + timedelta(days=i % 40), 'A', 100.0, 100.0 + (i % 10) * 3) for i in range(200)]
    off = lr.fit_buffer(obs, TODAY, 50, 0.0)
    assert not off.accepted and off.params is None and '50' in off.reason
    short = lr.fit_buffer(obs[:20], TODAY, 80, 0.0)
    assert not short.accepted and short.reason.startswith('քիչ տվյալներ')
    junk = [lr.TripObs(o.day, 'A', 100.0, 400.0) for o in obs] + [lr.TripObs(o.day, 'A', 5.0, 50.0) for o in obs]
    assert lr.fit_buffer(junk, TODAY, 80, 0.0).n_obs == 0                           # не рейсы — не в счёт
    early = [lr.TripObs(o.day, 'A', 100.0, 80.0) for o in obs]                      # всегда раньше модели — запас 0
    assert lr.fit_buffer(early, TODAY, 80, 0.0).params['c'] == 0.0
    assert lr.pinball(110.0, 100.0, 0.8) == pytest.approx(8.0) and lr.pinball(90.0, 100.0, 0.8) == pytest.approx(2.0)


def test_trip_obs_lunch_rule_and_skips():
    """Обед в прогнозе рейса — по правилу плана: выезд до начала окна — обед в рейсе (если рейс туда доходит); следующий
    рейс уже без обеда."""
    cds = sim.simulate([TODAY - timedelta(days=3)], seed=5, cars=('CAR1',))
    cd = cds[0]
    tn = replace(sim.truck_norms(), lunch_minutes=30.0, lunch_from=60.0, lunch_to=180.0)
    plain = lr.trip_obs(cd.day, cd.car, cd.actual, cd.stops, sim.norms(), replace(tn, lunch_minutes=0.0), sim.DEPOT,
                        sim.WORK_START)
    fed = lr.trip_obs(cd.day, cd.car, cd.actual, cd.stops, sim.norms(), tn, sim.DEPOT, sim.WORK_START)
    assert len(plain) == len(fed) == 2
    assert fed[0].predicted == pytest.approx(plain[0].predicted + 30.0, abs=0.05)   # обед — в первом рейсе
    assert fed[1].predicted == pytest.approx(plain[1].predicted)                    # во втором — уже нет
    assert [o.minutes for o in fed] == [o.minutes for o in plain]
    depot = replace(tn, lunch_from=-5.0)                                            # выезд после начала окна — обед на складе
    assert [o.predicted for o in lr.trip_obs(cd.day, cd.car, cd.actual, cd.stops, sim.norms(), depot, sim.DEPOT,
                                             sim.WORK_START)] == [o.predicted for o in plain]
    broken = replace(cd.actual, trips=(replace(cd.actual.trips[0], ret=None), *cd.actual.trips[1:]))
    assert len(lr.trip_obs(cd.day, cd.car, broken, cd.stops, sim.norms(), tn, sim.DEPOT, sim.WORK_START)) == 1


# ============================== журнал, действующие нормы, применение ==============================

def _row(kind, params, day='2026-10-01', model_id=None, accepted=True):
    return {'kind': kind, 'scope': '', 'run_day': day, 'params': params, 'model_id': model_id, 'accepted': accepted}


def test_in_effect_and_apply_buffer_only_at_same_q_and_pace_by_model():
    rows = [_row('buffer', {'c': 2.0, 'q': 80}),
            _row('truck_unload', {'factors': {'CAR4': 1.3}, 'k': 2.0}),
            _row('truck_travel', {'factors': {'CAR4': 1.2, 'CAR1': 0.95}, 'k': 2.0}, model_id='straight')]
    eff = lr.in_effect(rows, {}, 'straight')
    assert eff and eff.buffer == {'c': 2.0, 'q': 80} and eff.truck_travel['model_id'] == 'straight'
    tn = replace(TN, buffer_pct=80.0)
    _, t2, _ = lr.apply_learned(DP_NORMS, tn, {}, eff, {})
    assert t2.buffer_c == 2.0 and t2.pace == {'CAR1': (1.0, 0.95), 'CAR4': (1.3, 1.2)}
    assert lr.apply_learned(DP_NORMS, replace(tn, buffer_pct=90.0), {}, eff, {})[1].buffer_c == 0.0   # другой q
    assert lr.apply_learned(DP_NORMS, replace(tn, buffer_pct=50.0), {}, eff, {})[1].buffer_c == 0.0   # без запаса
    other = lr.in_effect(rows, {}, 'osm:1')                                         # другая дорожная модель
    assert other.truck_travel is None and lr.truck_pace(other, 'osm:1') == {'CAR4': (1.3, 1.0)}
    assert not lr.in_effect(rows, {'buffer': False, 'truck_unload': False, 'truck_travel': False}, 'straight')
    assert lr.apply_learned(DP_NORMS, TN, {}, lr.InEffect(), {})[1] is TN           # без строк — те же объекты
    for kind, bad in (('buffer', {'c': -1.0, 'q': 80}), ('buffer', {'c': 2.0, 'q': 40}), ('buffer', {'c': 2.0}),
                      ('truck_unload', {'factors': {'CAR4': 9.0}}), ('truck_travel', {'factors': {'': 1.1}}),
                      ('truck_unload', {'factors': [1.1]})):
        assert not lr.valid_params(kind, bad), (kind, bad)
    assert lr.in_effect([_row('buffer', {'c': 2.0, 'q': 40})], {}, None).buffer is None


def test_settings_buffer_pct_and_from_settings():
    s = dict(st.DEFAULT_SETTINGS)
    assert s['dispatch_buffer_pct'] == 80
    assert fl.TruckNorms.from_settings(s, lunch=True).buffer_pct == 80.0
    assert fl.TruckNorms.from_settings(s).buffer_pct == 0.0                         # модель менеджеров запаса не знает
    assert fl.TruckNorms.from_settings(s) == fl.TruckNorms.from_settings({**s, 'dispatch_buffer_pct': 50})
    assert st._NUMERIC['dispatch_buffer_pct'] == (50, 95, False)
    js = (ROOT / 'static' / 'js' / 'routes_settings.js').read_text(encoding='utf-8')
    assert "key: 'dispatch_buffer_pct'" in js and 'min: 50, max: 95' in js
    assert "routes_settings.js') }}?v=25" in (ROOT / 'templates' / 'routes_settings.html').read_text(encoding='utf-8')


def test_store_migrates_20_to_21_keeps_rows_and_allows_new_kinds(tmp_path):
    path = str(tmp_path / 'v20.db')
    s = st.Store(path)
    s.save_learned('2026-10-01', [lr.Outcome('lunch', '', True, 'да', {'minutes': 25.0}, confidence=0.95),
                                  lr.Outcome('fuel', 'CAR1', False, 'мало данных', n_obs=3)])
    with closing(sqlite3.connect(path)) as conn:                                    # база схемы 20
        conn.execute('ALTER TABLE learned_norms RENAME TO learned_new')
        conn.execute(f'CREATE TABLE learned_norms({st._LEARNED_COLUMNS_V20})')
        conn.execute(f'INSERT INTO learned_norms({st._LEARNED_COPY_V20}) SELECT {st._LEARNED_COPY_V20} FROM learned_new')
        conn.execute('DROP TABLE learned_new')
        conn.execute("UPDATE sqlite_sequence SET seq = 40 WHERE name = 'learned_norms'")
        conn.execute("UPDATE meta SET value = '20' WHERE key = 'schema_version'")
        conn.commit()
        before = conn.execute('SELECT * FROM learned_norms ORDER BY id').fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO learned_norms(kind, scope, run_day, n_obs, n_test, accepted, reason, created_at) "
                         "VALUES('buffer', '', '2026-10-02', 0, 0, 0, 'x', 'now')")
    s2 = st.Store(path)
    s2.save_learned('2026-10-02', [lr.Outcome('buffer', '', True, 'да', {'c': 2.0, 'q': 80}, confidence=0.99),
                                   lr.Outcome('truck_unload', '', False, 'нет'),
                                   lr.Outcome('truck_travel', '', False, 'нет', model_id='straight')])
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('21',) == \
            (str(st.SCHEMA_VERSION),)
        rows = conn.execute('SELECT * FROM learned_norms ORDER BY id').fetchall()
        assert rows[:len(before)] == before and rows[len(before)][0] == 41
        assert conn.execute("SELECT name FROM sqlite_sequence WHERE name LIKE 'learned_norms%'").fetchall() == \
            [('learned_norms',)]
    got = {r['kind']: r for r in s2.learned()}
    assert got['buffer']['params'] == {'c': 2.0, 'q': 80} and got['lunch']['confidence'] == 0.95


def test_learning_page_texts_armenian():
    js = (ROOT / 'static' / 'js' / 'routes_learning.js').read_text(encoding='utf-8')
    assert "kind === 'buffer'" in js and "kind === 'truck_unload' || kind === 'truck_travel'" in js
    html = (ROOT / 'templates' / 'routes_learning.html').read_text(encoding='utf-8')
    assert 'Ժամանակի պաշար երթի վերջում' in html and 'Մեքենայի գործակիցները' in html
    assert "routes_learning.js') }}?v=17" in html
    assert lr.KIND_TITLES['buffer'] == 'Ժամանակի պաշար երթի վերջում'
    assert all(lr.DEFAULT_AUTO[k] for k in ('buffer', 'truck_unload', 'truck_travel'))
    djs = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert 'dp-buffer-mark' in djs and 'ժամանակի պաշար երթի վերջում՝' in djs
    page = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    assert "routes_dispatch.js') }}?v=68" in page and "routes_dispatch.css') }}?v=39" in page

# -*- coding: utf-8 -*-
"""Обучение «как у профессионалов» (ответ владельца №66, docs/plans/learning-pro-plan.md, часть 1 — запас на рейс).

- запас на рейс (вид buffer): правило шкалы времени (fleet._schedule) — рейс занимает D + c·√D (≤ 40% D), прибытия к
  точкам — по медиане, следующий рейс и конец дня — с запасом; plan_view показывает запас; сборка и решатель не
  планируют за конец дня; обучение — q-квантиль (факт − D)/√D, проверка — pinball-потеря и покрытие, правило принятия
  с бутстрепом; действует только при том q настроек, при котором проверен; q = 50 — без запаса;
- синтетические GPS-дни с известной правдой (tests/learning_pro_sim.py: медленный экипаж CAR4, шум со связью внутри
  дня): покрытие запаса ≈ 80% на проверке и на новых днях, проверка принимает;
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
from test_route_dispatch_lunch import _ctx, _random_day, _two_trips, _violations  # noqa: E402
from test_route_optimizer import DP_DEPOT, DP_NORMS, EAST, FORD, HOWO, TN, WEST, _dp_stops, _info  # noqa: E402

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


def test_lunch_trip_carries_buffer():
    seq, stops, m = _line([60, 60, 60, 60])
    tn = replace(TN, lunch_minutes=30.0, lunch_from=210.0, lunch_to=330.0, buffer_c=2.0)
    _, base, _, _ = fl._lunch_trip(seq, stops, m, 0.0, replace(tn, buffer_c=0.0), True, False)
    _, buffered, _, brk = fl._lunch_trip(seq, stops, m, 0.0, tn, True, False)
    assert brk is not None and buffered == pytest.approx(base + fl.trip_reserve(2.0, base))


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
    end = tl[2][0] + tl[2][1]
    tight = replace(tn, work_minutes=end - 1.0)
    assert dp.runs_late(_ctx(tight), stops, draft) and not dp.runs_late(_ctx(replace(TN, work_minutes=end - 1.0)),
                                                                        stops, draft)


def test_no_buffer_no_pace_plan_identical():
    """Без выученных строк: процентиль в настройках (80) без c — план до байта прежний."""
    stops, draft = _two_trips()
    base = dp.plan_view(_ctx(TN), stops, draft, _info)
    same = replace(TN, buffer_pct=80.0, buffer_c=0.0)
    assert dp.plan_view(_ctx(same), stops, draft, _info) == base
    for seed in range(3):
        s, windows, trucks = _random_day(seed)
        a = dp.build(_ctx(TN, trucks, windows), s, None, [t.car_code for t in trucks], 'now')
        b = dp.build(_ctx(same, trucks, windows), s, None, [t.car_code for t in trucks], 'now')
        assert a.to_json() == b.to_json(), seed


def test_build_with_buffer_never_violates():
    """Сборка (с решателем, выравниванием и доводкой) с запасом: ни одного рейса позже конца дня и мимо окна по точной
    шкале; не поместилось — «не поместились»."""
    for seed in range(5):
        stops, windows, trucks = _random_day(seed)
        for tn in (replace(TN, buffer_c=2.5),
                   replace(TN, buffer_c=2.5, lunch_minutes=30.0, lunch_from=210.0, lunch_to=330.0)):
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


def test_plan_trips_plain_path_uses_buffer():
    """Прежний путь plan_trips (без окон и часовой модели): рейс занимает минуты с запасом; день не переполняется."""
    pts = [p for _, p, _ in EAST + WEST]
    kgs = [200.0] * 6
    trucks = [replace(FORD, car_code='F1'), replace(FORD, car_code='F2')]
    base = fl.route_day(pts, kgs, [1.0] * 6, DP_DEPOT, trucks, DP_NORMS, TN, overflow=False)
    tn = replace(TN, buffer_c=3.0)
    trips = fl.route_day(pts, kgs, [1.0] * 6, DP_DEPOT, trucks, DP_NORMS, tn, overflow=False)
    assert sorted(i for t in trips for i in t.items) == sorted(i for t in base for i in t.items)
    used: dict[str, float] = {}
    for t in trips:
        used[t.truck] = used.get(t.truck, 0.0) + t.minutes
    assert all(v <= tn.work_minutes + 1e-6 for v in used.values())
    for t in trips:
        seq = list(t.items)
        drive = fl.route_trip([pts[i] for i in seq], [kgs[i] for i in seq], DP_DEPOT, DP_NORMS, TN, reorder=False)[2]
        assert t.minutes == pytest.approx(drive + fl.trip_reserve(3.0, drive))


@pytest.mark.skipif(not vrp.available(), reason='нет PyVRP')
def test_vrp_profiles_and_reserve_edges():
    """PyVRP: запас типичного рейса — на рёбрах «заказ → склад»; без него — прежние рёбра."""
    pieces = [vrp.Piece(1, 100.0, 10.0, None, None, False, True), vrp.Piece(2, 100.0, 10.0, None, None, False, True)]
    km = [[0, 5, 5], [5, 0, 3], [5, 3, 0]]
    mins = [[0, 10, 10], [10, 0, 6], [10, 6, 0]]
    shifts = [vrp.Shift('A', 0.0, 100.0)]
    fast = [vrp.Vehicle('A', 1000.0, 10.0, False)]
    assert vrp.solve(pieces, km, mins, fast, shifts, [], None) is not None          # 10+10+6+10+10 = 46 ≤ 100
    assert vrp.solve(pieces, km, mins, fast, shifts, [], None, trip_reserve_min=60.0) is None   # 46 + 60 > 100


# ============================== обучение запаса: синтетика ==============================

@pytest.fixture(scope='module')
def world():
    days = [TODAY - timedelta(days=i) for i in range(60, 0, -1)]
    cds = sim.simulate(days)
    tn = sim.truck_norms()
    trips = sim.observations(cds, tn)
    future = sim.simulate([TODAY + timedelta(days=i) for i in range(30)], seed=99)
    return cds, tn, trips, future


def test_simulation_buffer_coverage_and_gate(world):
    """(а) запас P80: c > 0, покрытие на отложенной неделе и на 30 новых днях ≈ 80%, pinball меньше, проверка
    принимает (бутстреп — устойчиво)."""
    cds, tn, trips, future = world
    assert len(trips) == 2 * len(cds)                                               # каждый рейс факта — наблюдение
    out = lr.fit_buffer(trips, TODAY, 80, 0.0)
    assert out.accepted and out.confidence >= lr.BOOT_SHARE and out.mae_after < 0.7 * out.mae_before
    p = out.params
    assert p['c'] > 1.0 and p['q'] == 80 and 0.72 <= p['coverage'] <= 0.88 and p['coverage_before'] < 0.5
    assert 'նպատակը՝ 80%' in out.reason
    ahead = sim.observations(future, tn)
    assert abs(sim.coverage(ahead, p['c']) - 0.80) <= 0.04 and sim.coverage(ahead, 0.0) < 0.5
    again = lr.fit_buffer(trips, TODAY, 80, p['c'])                                 # тот же запас уже действует
    assert not again.accepted and again.params['c'] == p['c']
    q90 = lr.fit_buffer(trips, TODAY, 90, 0.0).params
    assert q90['c'] > p['c'] and q90['coverage'] >= p['coverage']



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


def test_in_effect_and_apply_buffer_only_at_same_q():
    rows = [_row('buffer', {'c': 2.0, 'q': 80})]
    eff = lr.in_effect(rows, {}, 'straight')
    assert eff and eff.buffer == {'c': 2.0, 'q': 80}
    tn = replace(TN, buffer_pct=80.0)
    _, t2, _ = lr.apply_learned(DP_NORMS, tn, {}, eff, {})
    assert t2.buffer_c == 2.0
    assert lr.apply_learned(DP_NORMS, replace(tn, buffer_pct=90.0), {}, eff, {})[1].buffer_c == 0.0   # другой q
    assert lr.apply_learned(DP_NORMS, replace(tn, buffer_pct=50.0), {}, eff, {})[1].buffer_c == 0.0   # без запаса
    assert not lr.in_effect(rows, {'buffer': False}, 'straight')
    assert lr.apply_learned(DP_NORMS, TN, {}, lr.InEffect(), {})[1] is TN           # без строк — те же объекты
    for kind, bad in (('buffer', {'c': -1.0, 'q': 80}), ('buffer', {'c': 2.0, 'q': 40}), ('buffer', {'c': 2.0})):
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
    assert "routes_settings.js') }}?v=24" in (ROOT / 'templates' / 'routes_settings.html').read_text(encoding='utf-8')


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
    s2.save_learned('2026-10-02', [lr.Outcome('buffer', '', True, 'да', {'c': 2.0, 'q': 80}, confidence=0.99)])
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
    assert "kind === 'buffer'" in js
    html = (ROOT / 'templates' / 'routes_learning.html').read_text(encoding='utf-8')
    assert 'Ժամանակի պաշար երթի վերջում' in html
    assert "routes_learning.js') }}?v=14" in html
    assert lr.KIND_TITLES['buffer'] == 'Ժամանակի պաշար երթի վերջում'
    assert lr.DEFAULT_AUTO['buffer']
    djs = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert 'dp-buffer-mark' in djs and 'ժամանակի պաշար երթի վերջում՝' in djs
    page = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    assert "routes_dispatch.js') }}?v=63" in page and "routes_dispatch.css') }}?v=34" in page

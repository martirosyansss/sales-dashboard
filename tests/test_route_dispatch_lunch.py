# -*- coding: utf-8 -*-
"""«Развоз»: обед водителей в пути (ответ владельца №61).

- правило (fleet: _lunch_trip / _schedule) — одно для всех расчётов дня машины: обед — на первой границе не раньше
  начала окна (после разгрузки у магазина или на складе между рейсами), к концу окна границы нет и машина в пути — в
  дороге в конец окна, в конце окна машина у магазина — сразу после его разгрузки,
  ожидание окна приёма после начала окна обеда идёт в его счёт, всё после него сдвигается; день кончился раньше окна —
  обеда нет; последняя точка — только если после неё есть работа или она сама началась после начала окна;
- план (plan_view): где обед (время, сколько добавил, после какой точки), минуты рейса и машины — с ним, пояснение
  «почему так» в сумме — те же минуты; без обеда — прежний план до байта;
- сборка не планирует за конец дня и мимо окон и с обедом (решатель PyVRP пауз не знает: проверка — точным расчётом),
  «Везти после конца дня» — тоже;
- настройки: минуты (0 — без обеда), окно начала обеда; модель парка менеджеров обеда не знает;
- обучение (вид lunch): обед по факту — там, где он по плану (излишек стоянки у магазина над нормой разгрузки, на
  складе — над плановой загрузкой), или стоянка не по плану в окне ± 30 мин, больший; медиана, шаг, пределы, принятие
  по общему правилу (с бутстрепом), автообучение по умолчанию выключено; визит и стоянка с обедом по плану — не в
  разгрузку и загрузку; пять ночей подряд при обеде там, где план, — обед остаётся ≈ 30, нормы разгрузки не растут.

Синтетические данные; ERP не читается; базы — временные.  Запуск:  python -m pytest tests/test_route_dispatch_lunch.py -q
"""
import random
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_learning_loop import DEPOT, S101, S102, S104, TZ, X, FakeFacts, Track  # noqa: E402
from test_route_optimizer import (DP_DAY, DP_DEPOT, DP_NORMS, EAST, FORD, HOWO, REF, TN, WEST,  # noqa: E402,F401
                                  _dispatch_setup, _dorder, _dp_stops, _info, _save, client, store)

L0, D = 210.0, 30.0                  # окно обеда с 12:30 (день машины — с 09:00), 30 минут
LTN = replace(TN, lunch_minutes=D, lunch_from=L0, lunch_to=L0 + 120)
JAC = fl.FleetTruck('475DD61', 'JAC', 2200.0, 12.0, center_ok=True)
INF = float('inf')


# ============================== правило обеда на одном рейсе ==============================

def _line(drives, unload=10.0, windows=None):
    """Точки на прямой: склад — 0, к точке k — drives[k] мин от предыдущей; матрица минут; разгрузка unload."""
    pos = [0.0]
    for x in drives:
        pos.append(pos[-1] + x)
    m = [[abs(a - b) for b in pos] for a in pos]
    stops = [fl._Stop(k + 1, 0.0, 0.0, unload, *((windows or {}).get(k, (0.0, INF)))) for k in range(len(drives))]
    return list(range(len(drives))), stops, m


def _trip(drives, free=0.0, pending=True, more=False, tn=LTN, **kw):
    seq, stops, m = _line(drives, **kw)
    arr = []
    depart, minutes, ok, brk = fl._lunch_trip(seq, stops, m, free, tn, pending, more, arr)
    return depart, minutes, ok, brk, arr


def test_break_at_first_store_finishing_after_window_start_and_later_etas_shift():
    """Разгрузки кончаются в 70, 140, 210, 280: обед — после третьей точки (210 = начало окна), четвёртая — на 30 мин
    позже, рейс — на 30 мин длиннее."""
    depart, minutes, ok, brk, arr = _trip([60, 60, 60, 60])
    assert (depart, ok, brk) == (0.0, True, fl.Break(210.0, 30.0, 2))
    assert arr == [60.0, 130.0, 200.0, 300.0] and minutes == 520.0 + 30.0
    plain = _trip([60, 60, 60, 60], pending=False)                                  # обед уже был — как без обеда
    assert plain[:4] == (0.0, 520.0, True, None) and plain[4] == [60.0, 130.0, 200.0, 270.0]
    seq, stops, m = _line([60, 60, 60, 60])
    assert plain[1] == fl._schedule(seq, stops, m, 0.0)[0]                          # без обеда — ровно прежний расчёт
    assert _trip([60, 60, 60, 60], tn=TN)[3] is None                                # обед не задан


def test_long_leg_break_at_first_boundary_after_window_and_last_stop_rule():
    # перегон 70 → 270 (через всё окно): обед — после разгрузки на первой точке после окна
    assert _trip([60, 200])[3] == fl.Break(280.0, 30.0, 1)                          # точка началась после 12:30
    # последняя точка началась до начала окна, кончилась после: работы после неё нет — обеда нет; есть рейс — есть
    assert _trip([200], unload=20.0)[3] is None
    assert _trip([200], unload=20.0, more=True)[3] == fl.Break(220.0, 30.0, 0)
    # день кончился раньше окна — обеда нет (и следующего рейса нет)
    depart, minutes, _, brk, _ = _trip([30, 30])
    assert brk is None and depart + minutes < L0


def test_depot_boundary_between_trips_and_idle_counts():
    # машина вернулась в 215 (после начала окна): обед на складе с 215, загрузка — с 245
    depart, _, _, brk, arr = _trip([60], free=215.0)
    assert (depart, brk, arr) == (245.0, fl.Break(215.0, 30.0, None), [305.0])
    # вернулась в 180, но выезжает под окно первой точки (не раньше 290 у магазина) — в 230: на складе в 12:30 — обед
    # с 210; простой до 230 идёт в его счёт, выезд — на 10 мин позже
    depart, _, _, brk, arr = _trip([60], free=180.0, windows={0: (290.0, INF)})
    assert (depart, brk, arr) == (240.0, fl.Break(210.0, 10.0, None), [300.0])
    # простоя на складе хватает на весь обед — ничего не сдвигается
    depart, _, _, brk, arr = _trip([60], free=180.0, windows={0: (360.0, INF)})
    assert (depart, brk, arr) == (300.0, fl.Break(210.0, 0.0, None), [360.0])
    # ушёл со склада до начала окна — обед уже в рейсе: первая точка кончилась в 170, вторая — в 240
    assert _trip([60, 60], free=100.0)[3] == fl.Break(240.0, 30.0, 1)


def test_receiving_window_wait_counts_toward_the_break():
    """Приехал в 200, магазин принимает с 230: 20 мин ожидания после 12:30 — уже обед, после разгрузки (240) добавляется
    10 мин; следующий магазин — на 10 мин позже."""
    _, minutes, ok, brk, arr = _trip([60, 130, 60], windows={1: (230.0, INF)})
    assert brk == fl.Break(240.0, 10.0, 1) and arr == [60.0, 230.0, 310.0] and ok
    no_lunch = _trip([60, 130, 60], windows={1: (230.0, INF)}, tn=TN)
    assert no_lunch[4] == [60.0, 230.0, 300.0] and minutes == no_lunch[1] + 10.0
    # ожидание целиком в окне обеда и не короче обеда — обед ничего не добавляет
    assert _trip([60, 160, 60], windows={1: (270.0, INF)})[3] == fl.Break(280.0, 0.0, 1)


def test_latest_start_on_the_road_or_right_after_unloading():
    """Конец окна обеда — самое позднее начало: к нему границы не было, а машина в пути — обед в дороге в конец окна
    (перегон длиннее на обед, дальше всё сдвинуто); обратно на склад — только если после рейса есть работа; в конце окна
    машина у магазина — сразу после его разгрузки."""
    depart, minutes, ok, brk, arr = _trip([60, 300])              # перегон 70 → 370 через конец окна (330)
    assert brk == fl.Break(330.0, 30.0, 1, True) and arr == [60.0, 400.0] and minutes == 60 + 10 + 300 + 30 + 10 + 360
    late = replace(LTN, lunch_from=150.0, lunch_to=160.0)
    assert _trip([100], tn=late)[3] is None                        # обратно на склад, работы после нет — без обеда
    _, minutes, _, brk, _ = _trip([100], tn=late, more=True)       # есть — обед в дороге на склад
    assert brk == fl.Break(160.0, 30.0, 1, True) and minutes == 100 + 10 + 100 + 30
    _, _, _, brk, _ = _trip([155], unload=20.0, tn=late)          # в 160 разгружается — обед после разгрузки (175)
    assert brk == fl.Break(175.0, 30.0, 0)
    assert _trip([60, 300], tn=replace(LTN, lunch_to=0.0))[3] == fl.Break(380.0, 30.0, 1)   # конец не задан — как раньше


def test_receiving_window_checked_on_shifted_time():
    """Без обеда третья точка успевает ровно к концу окна (310), с обедом после второй (кончилась в 240) — опаздывает."""
    ok_plain = _trip([100, 120, 70], windows={2: (0.0, 310.0)}, tn=TN)[2]
    ok_lunch = _trip([100, 120, 70], windows={2: (0.0, 310.0)})[2]
    assert ok_plain and not ok_lunch


def test_day_of_truck_with_lunch_days_and_balance():
    """Дни машин в проверках сборки (_days: выравнивание, доводка, решатель) — по тому же правилу: обед один, следующий
    рейс сдвигается; выравнивание (_balance) пересчитывает минуты рейсов с обедом."""
    seq, stops, m = _line([10.0, 0.5, -0.3], unload=10.0)          # A — 10, B — 10,5, C — 10,2 мин (км) от склада
    stops = [replace(stops[0], kg=500.0), replace(stops[1], kg=450.0), replace(stops[2], kg=300.0)]
    truck = fl.FleetTruck('T1', 'T', 1000.0, 10.0)
    tn = replace(TN, lunch_minutes=30.0, lunch_from=5.0, lunch_to=60.0)        # обед — после первой точки

    def trip(items):
        return fl.Trip('T1', len(items), sum(stops[i].kg for i in items), 0.0, fl._closed(items, stops, m), 0.0,
                       1000.0, 0.0, False, tuple(items))
    trips = [trip([0, 1]), trip([2])]
    plain = fl._days(trips, stops, m, {'T1': 0.0})['T1']
    days = fl._days(trips, stops, m, {'T1': 0.0}, tn)['T1']
    assert days[0][2] == pytest.approx(plain[0][2] + 30.0) and days[1][1] == pytest.approx(plain[1][1] + 30.0)
    assert days[0][3] == plain[0][3] == 0.0                                          # ожидание окон — без обеда
    assert fl._days(trips, stops, m, {'T1': 0.0}, tn, fed={'T1'})['T1'] == plain    # обед уже был
    out = fl._balance(trips, stops, m, m, [truck], tn, None, None)
    assert sorted(t.kg for t in out) == [500.0, 750.0]                                # 95% + 30% → 75% + 50%
    exact = fl._days(out, stops, m, {'T1': 0.0}, tn)['T1']
    assert [t.minutes for t in out] == pytest.approx([x[2] for x in exact])
    assert out[0].minutes == pytest.approx(fl._days(out, stops, m, {'T1': 0.0})['T1'][0][2] + 30.0)   # с обедом


def test_occupied_assumes_more_trips_after_pinned_work():
    """Закреплённый рейс, чья последняя точка началась до начала окна обеда и кончилась после: в точном плане обеда нет
    (работы после нет), а для раскладки вокруг (за ним встанут новые рейсы) — занятое время с обедом и машина «поела»."""
    stops, _ = _dp_stops(EAST)
    routable = {s.customer_id: s for s in stops}
    pinned = [dp.DraftTrip(1, HOWO.car_code, [101, 102, 103], True)]
    plain = dp._timeline(_ctx(TN), pinned, routable, {})[1]
    last_end = plain[2][-1] + 9.2                                   # разгрузка 8 + 6 × 0,2 т
    tn = replace(TN, lunch_minutes=30.0, lunch_from=last_end - 1.0, lunch_to=last_end + 100)
    assert dp._timeline(_ctx(tn), pinned, routable, {})[1] == plain                # точный план — без обеда
    fed = set()
    used, _, _ = dp._occupied(_ctx(tn), pinned, routable, {}, fed)
    assert used[HOWO.car_code] == pytest.approx(plain[0] + plain[1] + 30.0) and fed == {HOWO.car_code}


# ============================== план дня: plan_view, пояснение ==============================

def _ctx(tn, trucks=(HOWO,), windows=None, overtime=None):
    return dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in trucks}, DP_NORMS, tn, 9 * 60, overtime,
                         windows=windows or {})


def _two_trips():
    stops, _ = _dp_stops(EAST + WEST)
    draft = dp.Draft(trucks=[HOWO.car_code], trips=[dp.DraftTrip(1, HOWO.car_code, [101, 102, 103]),
                                                    dp.DraftTrip(2, HOWO.car_code, [104, 105, 106])])
    return stops, draft


def test_plan_view_shows_lunch_and_includes_it_in_minutes():
    stops, draft = _two_trips()
    routable = {s.customer_id: s for s in stops}
    # окно обеда — через 20 мин после выезда: обед — в первом рейсе, после второй точки (кончилась в 26 мин)
    tn = replace(TN, lunch_minutes=30.0, lunch_from=20.0, lunch_to=140.0)
    plain_ctx, ctx = _ctx(TN), _ctx(tn)
    base = dp._timeline(plain_ctx, draft.trips, routable, dp._shares(draft.trips))
    parts = {}
    tl = dp._timeline(ctx, draft.trips, routable, dp._shares(draft.trips), parts)
    brk = parts[1]['lunch']
    assert brk is not None and brk.stop == 1 and parts[2]['lunch'] is None          # обед — один на день машины
    k = brk.stop
    assert tl[1][2][:k + 1] == base[1][2][:k + 1]                                   # до обеда — те же прибытия
    assert all(a == pytest.approx(b + brk.added) for a, b in zip(tl[1][2][k + 1:], base[1][2][k + 1:]))
    assert tl[1][1] == pytest.approx(base[1][1] + brk.added)                       # рейс длиннее на обед
    assert tl[2][0] == pytest.approx(base[2][0] + brk.added)                       # второй рейс — позже на обед
    view = dp.plan_view(ctx, stops, draft, _info)
    t1, t2 = view['trucks'][0]['trips']
    assert 'lunch' not in t2 and t1['lunch'] == {
        'start': dp._hhmm(9 * 60 + brk.at), 'end': dp._hhmm(9 * 60 + brk.at + brk.added), 'minutes': 30.0,
        'added_min': round(brk.added, 1), 'after_stop': k, 'where': 'store'}
    plain = dp.plan_view(plain_ctx, stops, draft, _info)
    assert view['trucks'][0]['minutes'] == round(base[2][0] + base[2][1] + brk.added) != plain['trucks'][0]['minutes']
    x = t1['explain']
    assert x['lunch_min'] == round(brk.added, 1) and 'lunch_min' not in t2['explain']
    total = x['loading_min'] + x['drive_min'] + x['unload_min'] + x['wait_min'] + x['lunch_min']
    assert total == pytest.approx(t1['minutes'], abs=1.0)
    assert dp.runs_late(ctx, stops, draft) is False


def test_plan_view_depot_lunch_between_trips():
    """Первый рейс кончается до начала окна обеда, второй выезжает после — обед на складе до его загрузки."""
    stops, draft = _two_trips()
    routable = {s.customer_id: s for s in stops}
    first = dp._timeline(_ctx(TN), draft.trips, routable, dp._shares(draft.trips))[1]
    back = first[0] + first[1]
    tn = replace(TN, lunch_minutes=30.0, lunch_from=back - 5.0, lunch_to=back + 100)
    view = dp.plan_view(_ctx(tn), stops, draft, _info)
    t1, t2 = view['trucks'][0]['trips']
    assert 'lunch' not in t1 and t2['lunch']['after_stop'] is None and t2['lunch']['added_min'] == 30.0
    assert t2['lunch']['where'] == 'depot'                                         # пояснение — свой текст, не «простой»
    assert t2['lunch']['start'] == dp._hhmm(9 * 60 + back) and t2['loading_start'] == dp._hhmm(9 * 60 + back + 30)
    assert t2['explain']['idle_before_min'] == 0.0 and t2['explain']['lunch_min'] == 0.0


def test_plan_view_road_lunch_and_runs_late_exact():
    """Конец окна обеда приходится на первый перегон — обед в дороге (where road, до первой точки), прибытия позже на
    обед. runs_late — по точному плану: последняя точка началась до начала окна обеда, кончилась после, работы после нет
    — обеда нет и машина укладывается; занятое время для раскладки (обед после рейса с запасом) — её не «опаздывает»."""
    stops, draft = _two_trips()
    routable = {s.customer_id: s for s in stops}
    base = dp._timeline(_ctx(TN), draft.trips, routable, dp._shares(draft.trips))
    road = replace(TN, lunch_minutes=30.0, lunch_from=1.0, lunch_to=3.0)
    view = dp.plan_view(_ctx(road), stops, draft, _info)
    t1 = view['trucks'][0]['trips'][0]
    assert t1['lunch'] == {'start': '09:03', 'end': '09:33', 'minutes': 30.0, 'added_min': 30.0, 'after_stop': None,
                           'where': 'road'}
    tl = dp._timeline(_ctx(road), draft.trips, routable, dp._shares(draft.trips))
    assert all(a == pytest.approx(b + 30.0) for a, b in zip(tl[1][2], base[1][2]))
    assert t1['explain']['lunch_min'] == 30.0 and t1['explain']['idle_before_min'] == 0.0
    one = dp.Draft(trucks=[HOWO.car_code], trips=[dp.DraftTrip(1, HOWO.car_code, [101, 102, 103])])
    plain = dp._timeline(_ctx(TN), one.trips, routable, {})[1]
    last_end = plain[2][-1] + 9.2
    tight = replace(TN, work_minutes=plain[0] + plain[1] + 10.0, lunch_minutes=30.0, lunch_from=last_end - 1.0,
                    lunch_to=last_end + 100.0)
    assert dp.runs_late(_ctx(tight), stops, one) is False
    assert not any(t['over_time'] or 'lunch' in t for t in dp.plan_view(_ctx(tight), stops, one, _info)['trucks'][0]['trips'])
    used, _, _ = dp._occupied(_ctx(tight), one.trips, routable, {})
    assert used[HOWO.car_code] > tight.work_minutes                                  # с запасом — дольше дня


def test_lunch_zero_or_out_of_reach_plan_identical():
    stops, draft = _two_trips()
    base = dp.plan_view(_ctx(TN), stops, draft, _info)
    assert dp.plan_view(_ctx(replace(TN, lunch_minutes=0.0, lunch_from=40.0)), stops, draft, _info) == base
    assert dp.plan_view(_ctx(replace(LTN, lunch_from=2000.0)), stops, draft, _info) == base   # день до окна не дошёл


# ============================== сборка: за конец дня и мимо окон не планирует ==============================

def _random_day(seed):
    rng = random.Random(seed)
    n = rng.randint(14, 34)
    spec = [(300 + i, (40.10 + rng.random() * 0.20, 44.40 + rng.random() * 0.30),
             rng.choice([80.0, 150.0, 300.0, 600.0, 1200.0])) for i in range(n)]
    windows = {}
    for cid, _, _ in spec:
        if rng.random() < 0.3:
            a = 9 * 60 + rng.choice([60, 120, 180, 240, 300, 360])
            windows[cid] = (float(a), float(a + rng.choice([60, 120, 180])))
    trucks = rng.sample([HOWO, FORD, JAC], rng.randint(1, 3))
    stops, _ = _dp_stops(spec)
    return stops, windows, trucks


def _violations(view):
    trips = [t for tr in view['trucks'] for t in tr['trips']]
    return sum(1 for t in trips if t['over_time']), view['summary']['window_miss'], sum(1 for t in trips if 'lunch' in t)


def test_build_and_overtime_never_violate_with_lunch():
    """Инвариант сборки и с обедом: ни одного рейса позже конца дня (у «Везти после конца дня» — позже предела) и ни
    одной точки мимо окна приёма; не поместилось — «не поместились» / «не успеваем в окно», как без обеда."""
    lunches = 0
    for seed in range(10):
        stops, windows, trucks = _random_day(seed)
        ctx = _ctx(LTN, trucks, windows, overtime=660.0)
        draft = dp.build(ctx, stops, None, [t.car_code for t in trucks], 'now')
        late, miss, n = _violations(dp.plan_view(ctx, stops, draft, _info))
        assert (late, miss) == (0, 0), seed
        assert dp.runs_late(ctx, stops, draft) is False, seed
        lunches += n
        if draft.no_room or draft.no_window:
            draft = dp.overtime(ctx, stops, draft)
            late, miss, _ = _violations(dp.plan_view(ctx, stops, draft, _info))
            assert (late, miss) == (0, 0), ('overtime', seed)
    assert lunches >= 5                                                             # обед в сборках действительно есть


def test_build_does_not_plan_past_work_end_because_of_lunch():
    """Без обеда всё помещается в день одной машины, с обедом — нет: последнее не планируется (no_room), а не
    «опаздывает»."""
    stops, _ = _dp_stops(EAST + WEST)
    plain = dp.build(_ctx(TN), stops, None, [HOWO.car_code], 'now')
    view = dp.plan_view(_ctx(TN), stops, plain, _info)
    end = view['trucks'][0]['minutes']
    tight = replace(TN, work_minutes=end + 10.0)
    ok = dp.build(_ctx(tight), stops, None, [HOWO.car_code], 'now')
    assert not ok.no_room
    hungry = replace(tight, lunch_minutes=30.0, lunch_from=30.0, lunch_to=150.0)
    draft = dp.build(_ctx(hungry), stops, None, [HOWO.car_code], 'now')
    view = dp.plan_view(_ctx(hungry), stops, draft, _info)
    assert draft.no_room and not any(t['over_time'] for t in view['trucks'][0]['trips'])
    assert view['trucks'][0]['minutes'] <= hungry.work_minutes


def test_fleet_model_ignores_lunch():
    """Модель парка менеджеров (TruckNorms.from_settings без lunch) обеда не знает: настройки с обедом — те же нормы."""
    s = dict(st.DEFAULT_SETTINGS)
    assert s['truck_lunch_min'] == 30 and (s['truck_lunch_from'], s['truck_lunch_to']) == ('12:30', '14:30')
    assert fl.TruckNorms.from_settings(s) == fl.TruckNorms.from_settings({**s, 'truck_lunch_min': 0})
    assert fl.TruckNorms.from_settings(s).lunch_minutes == 0.0
    tn = fl.TruckNorms.from_settings(s, lunch=True)
    assert (tn.lunch_minutes, tn.lunch_from, tn.lunch_to) == (30.0, 210.0, 330.0)
    assert fl.TruckNorms.from_settings({**s, 'truck_lunch_min': 0}, lunch=True).lunch_minutes == 0.0


# ============================== настройки ==============================

def test_lunch_settings_validation(store):
    assert store.load().settings['truck_lunch_min'] == 30
    for key, bad in (('truck_lunch_min', -1), ('truck_lunch_min', 121), ('truck_lunch_min', 'x'),
                     ('truck_lunch_from', '12.30'), ('truck_lunch_to', '25:00')):
        _, errors = st.validate_payload({'settings': {key: bad}}, store.load(), REF)
        assert f'settings.{key}' in errors, (key, bad)
    _, errors = st.validate_payload({'settings': {'truck_lunch_from': '14:30', 'truck_lunch_to': '14:30'}},
                                    store.load(), REF)
    assert errors['settings.truck_lunch_to'] == 'Միջակայքի վերջը պետք է լինի սկզբից ուշ'
    # окно начала обеда — внутри рабочего дня машины (обед включён)
    _, errors = st.validate_payload({'settings': {'truck_lunch_from': '08:30', 'truck_lunch_to': '18:30'}},
                                    store.load(), REF)
    assert errors == {'settings.truck_lunch_from': 'ոչ շուտ, քան մեքենայի աշխատանքային օրվա սկիզբը',
                      'settings.truck_lunch_to': 'ոչ ուշ, քան մեքենայի աշխատանքային օրվա ավարտը'}
    _, errors = st.validate_payload({'settings': {'truck_work_end': '14:00'}}, store.load(), REF)
    assert errors == {'settings.truck_lunch_to': 'ոչ ուշ, քան մեքենայի աշխատանքային օրվա ավարտը'}
    _, errors = st.validate_payload({'settings': {'truck_work_end': '14:00', 'truck_lunch_min': 0}}, store.load(), REF)
    assert errors == {}                                                             # обед выключен — окно не мешает
    _save(store, {'settings': {'truck_lunch_min': 0, 'truck_lunch_from': '13:00', 'truck_lunch_to': '15:00'}})
    s = store.load().settings
    assert (s['truck_lunch_min'], s['truck_lunch_from'], s['truck_lunch_to']) == (0, '13:00', '15:00')


def test_settings_page_has_lunch_fields():
    js = (ROOT / 'static' / 'js' / 'routes_settings.js').read_text(encoding='utf-8')
    assert all(f"key: '{k}'" in js for k in ('truck_lunch_min', 'truck_lunch_from', 'truck_lunch_to'))
    assert "routes_settings.js') }}?v=36" in (ROOT / 'templates' / 'routes_settings.html').read_text(encoding='utf-8')
    page = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    assert "routes_dispatch.js') }}?v=100" in page and "routes_dispatch.css') }}?v=59" in page
    djs = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert 'function lunchItem' in djs and 'dp-lunch-mark' in djs


def test_dispatch_api_uses_lunch_setting(client):
    """«Собрать рейсы» через API: обед из настроек (0 — без обеда), ответ — с полем lunch у рейса."""
    _dispatch_setup(client, [_dorder(i + 1, cid, kg) for i, (cid, _, kg) in enumerate(EAST + WEST)])
    state = client.application.extensions['route_optimizer']
    _save(state.store, {'settings': {'truck_lunch_from': '09:30', 'truck_lunch_to': '11:30'}})

    def build():
        r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1']})
        assert r.status_code == 200, r.get_json()
        return [t for tr in r.get_json()['plan']['trucks'] for t in tr['trips']]
    with_lunch = build()
    assert sum(1 for t in with_lunch if 'lunch' in t) == 1
    _save(state.store, {'settings': {'truck_lunch_min': 0}})
    assert not any('lunch' in t for t in build())


# ============================== обучение: вид lunch ==============================

DAY = date(2026, 9, 29)


def _at(h, m=0):
    return datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=TZ)


def _actual(stays, ret):
    trips = (ac.Trip(_at(9), ret, None, (0,), 100.0, 10.0),)
    visits = (ac.Visit(('S:1',), _at(10), _at(10, 10), 0, False),)
    return ac.DayActual(100, 10.0, _at(9), ret, tuple(ac.Stay('other', a, b) for a, b in stays), visits, trips)


ROAD = [lr.PlanTrip(None, None, None, frozenset({1}), lr.PlanLunch('road', None, 30.0))]   # обед по плану — в дороге


def test_lunch_obs_longest_off_plan_stay_in_window():
    window = (12 * 60 + 30.0, 14 * 60 + 30.0)
    day = _actual([(_at(12, 50), _at(13, 10)), (_at(13, 40), _at(14, 15)), (_at(16, 0), _at(17, 0)),
                   (_at(11, 0), _at(12, 30))], ret=_at(16, 30))
    assert lr.lunch_obs(DAY, day, window, plan=ROAD) == lr.LunchObs(DAY, 35.0)   # 16:00 и 11:00 — вне окна ± 30 мин
    assert lr.lunch_obs(DAY, _actual([], _at(16, 30)), window, plan=ROAD) == lr.LunchObs(DAY, 0.0)   # не обедал — 0
    edge = _actual([(_at(12, 0), _at(12, 25)), (_at(15, 0), _at(15, 40))], _at(16, 30))
    assert lr.lunch_obs(DAY, edge, window, plan=ROAD).minutes == 40.0                # 12:00 и 15:00 — на краях ± 30
    assert lr.lunch_obs(DAY, _actual([(_at(12, 50), _at(13, 10))], _at(14, 20)), window, plan=ROAD) is None   # до 14:30
    # в плане обеда нет (прогноз до №61, обед выключен, план кончился до окна) — где искать, неизвестно: день не идёт
    assert lr.lunch_obs(DAY, day, window) is None
    assert lr.lunch_obs(DAY, day, window, plan=[lr.PlanTrip(None, None, None, frozenset({1}))]) is None


WINDOW = (12 * 60 + 30.0, 14 * 60 + 30.0)


def _norm(o):
    return 8.0 * o.n + 6.0 * o.tonnes                                               # действующая норма разгрузки


def _store_day(arrive, leave, opens=None, others=()):
    """Обед по плану — после разгрузки магазина 101 (500 кг): визит [arrive, leave], окно приёма с opens."""
    stops = [ac.PlanStop('S:1', 101, (40.2, 44.5), 500.0, 500.0, (opens, 1e9) if opens is not None else None)]
    trips = (ac.Trip(_at(9), _at(16, 30), None, (0,), 500.0, 10.0),)
    visits = (ac.Visit(('S:1',), arrive, leave, 0, False),)
    actual = ac.DayActual(100, 10.0, _at(9), _at(16, 30), tuple(ac.Stay('other', a, b) for a, b in others), visits,
                          trips, served=(('S:1', 0),))
    plan = [lr.PlanTrip(None, None, None, frozenset({101}), lr.PlanLunch('store', 101, 30.0))]
    return actual, stops, plan


def test_lunch_obs_where_planned_store():
    """Обед там, где план: стоянка у магазина сверх разгрузки по действующей норме (8 + 6 × 0,5 = 11 мин) — это обед,
    а не 0; ожидание окна приёма до начала окна обеда — не обед, после — обед (как в плане). Свернул поесть в сторону —
    стоянка не по плану, больший из двух."""
    actual, stops, plan = _store_day(_at(12, 40), _at(13, 21))                     # 41 мин: 11 разгрузка + 30 обед
    assert lr.lunch_obs(DAY, actual, WINDOW, stops, plan, _norm) == lr.LunchObs(DAY, 30.0)
    actual, stops, plan = _store_day(_at(12, 0), _at(13, 21), opens=12 * 60 + 50)   # ждал 12:00–12:50: 30 мин — до 12:30
    assert lr.lunch_obs(DAY, actual, WINDOW, stops, plan, _norm).minutes == 81 - 11 - 30
    actual, stops, plan = _store_day(_at(12, 40), _at(12, 51), others=[(_at(13, 0), _at(13, 27))])
    assert lr.lunch_obs(DAY, actual, WINDOW, stops, plan, _norm).minutes == 27.0    # поел в сторону: стоянка не по плану
    actual, stops, plan = _store_day(_at(12, 40), _at(12, 46))                     # быстрее нормы — не меньше 0
    assert lr.lunch_obs(DAY, actual, WINDOW, stops, plan, _norm).minutes == 0.0


def test_lunch_obs_first_trip_depot_lunch_counts_from_planned_start():
    """Ревью: первый рейс дня с обедом на складе — прошлого возвращения нет. Машина на складе с 09:00 до 13:15, по плану
    обед 12:30–13:00, загрузка 13:00–13:15: обед — 30 мин, считая с планового начала обеда (утро на складе — не обед),
    а не 240. Начала обеда в прогнозе нет и прошлого рейса нет — где он, неизвестно: склад не считается."""
    stops = [ac.PlanStop('S:1', 101, (40.2, 44.5), 500.0, 500.0)]
    visits = (ac.Visit(('S:1',), _at(13, 40), _at(13, 55), 0, False),)
    trips = (ac.Trip(_at(13, 15), _at(15), None, (0,), 500.0, 10.0),)
    stays = (ac.Stay('depot', _at(9), _at(13, 15), (), False),)
    actual = ac.DayActual(100, 10.0, _at(9), _at(15), stays, visits, trips, served=(('S:1', 0),))
    raw = {'trips': [{'loading_start': '13:00', 'depart': '13:15', 'return': '15:00', 'stops': [[101, '13:40']],
                      'lunch': {'where': 'depot', 'customer': None, 'start': '12:30', 'minutes': 30.0, 'added': 30.0}}]}
    plan = lr.plan_trips(raw, DAY)
    assert plan[0].lunch == lr.PlanLunch('depot', None, 30.0, _at(12, 30))
    assert lr.lunch_obs(DAY, actual, WINDOW, stops, plan, _norm) == lr.LunchObs(DAY, 30.0)
    late = replace(actual, stays=(ac.Stay('depot', _at(12, 40), _at(13, 15)),))    # приехал после начала обеда
    assert lr.lunch_obs(DAY, late, WINDOW, stops, plan, _norm).minutes == 20.0
    unknown = [replace(plan[0], lunch=replace(plan[0].lunch, start=None))]
    assert lr.lunch_obs(DAY, actual, WINDOW, stops, unknown, _norm).minutes == 0.0


def test_model_note_learned_lunch_only_while_in_effect(client):
    """Пояснение дня: выученный обед — в model.learned.lunch (минуты), только пока он действует (принят, автообучение
    обеда включено, обед в настройках не 0); иначе ключа нет — пояснение прежнее до байта."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    snap, _ = state.snapshots.cached()

    def learned():
        bundle = views._bundle(state)
        ctx = views._dispatch_ctx(state, snap, bundle, date(2026, 10, 1), views._ready_trucks(snap, bundle), [S101],
                                  {101: S101})
        return ctx.model['learned']
    assert 'lunch' not in learned()
    state.store.save_learned('2026-09-30', [lr.Outcome('lunch', '', True, 'x', {'minutes': 25.0})])
    assert 'lunch' not in learned()                                                  # автообучение обеда выключено
    state.store.save_learning_auto('lunch', True, 'qa')
    assert learned()['lunch'] == 25.0
    _save(state.store, {'settings': {'truck_lunch_min': 0}})
    assert 'lunch' not in learned()                                                  # обед выключен в настройках


def test_lunch_place_unload_ends_at_tap():
    """Отметка «закончил» (№65, разгрузка — не дольше 10 мин после неё) и обед по плану у магазина вместе: визит магазина с
    обедом по плану идёт в разгрузку до самой отметки, без хвоста в 10 мин (после отметки здесь обед — хвост добавил бы
    к разгрузке часть обеда), — магазин, где план регулярно ставит обед, получает своё время по GPS (№60); без отметки
    визит не идёт. Соседний магазин — по правилу отметки как есть; обед по факту у магазина виден и с отметкой."""
    tap = _at(12, 51)                                                               # разгрузка 12:40–12:51, обед до 13:21
    stops = [ac.PlanStop('S:1', 101, (40.2, 44.5), 500.0, 500.0, None, None, tap),
             ac.PlanStop('S:2', 102, (40.3, 44.6), 300.0, 300.0, None, None, _at(14, 5))]
    visits = (ac.Visit(('S:1',), _at(12, 40), _at(13, 21), 0, False), ac.Visit(('S:2',), _at(14), _at(14, 40), 0, False))
    trips = (ac.Trip(_at(9), _at(16, 30), None, (0, 1), 800.0, 10.0),)
    actual = ac.DayActual(100, 10.0, _at(9), _at(16, 30), (), visits, trips, served=(('S:1', 0), ('S:2', 1)))
    plan = [lr.PlanTrip(None, None, None, frozenset({101, 102}), lr.PlanLunch('store', 101, 30.0))]
    plain = lr.unload_obs(DAY, actual, stops)
    assert [(o.customers, o.minutes) for o in plain] == [((101,), 21.0), ((102,), 15.0)]   # хвост отметки — как у №65
    got = lr.unload_obs(DAY, actual, stops, lr.lunch_customers(plan))
    assert [(o.customers, o.minutes) for o in got] == [((101,), 11.0), ((102,), 15.0)]   # визит с обедом — до отметки
    assert lr.lunch_obs(DAY, actual, WINDOW, stops, plan, _norm).minutes == 41 - 11   # обед виден по стоянке
    untapped = [replace(stops[0], delivered_at=None), stops[1]]
    assert [o.customers for o in lr.unload_obs(DAY, actual, untapped, lr.lunch_customers(plan))] == [(102,)]   # без отметки — мимо
    assert [o.customers for o in lr.unload_obs(DAY, actual, untapped)] == [(101,), (102,)]


def test_lunch_obs_where_planned_depot_and_other_observations_skip_it():
    """Обед по плану на складе перед вторым рейсом: стоянка сверх плановой загрузки (15 мин) и планового простоя без
    обеда (0) — обед. Визит магазина и стоянка на складе с обедом по плану — не в обучение разгрузки и загрузки."""
    stops = [ac.PlanStop('S:1', 101, (40.2, 44.5), 500.0, 500.0), ac.PlanStop('S:2', 102, (40.3, 44.6), 800.0, 800.0)]
    visits = (ac.Visit(('S:1',), _at(10), _at(10, 12), 0, False), ac.Visit(('S:2',), _at(14, 10), _at(14, 25), 1, False))
    trips = (ac.Trip(_at(9, 30), _at(13, 5), 20.0, (0,), 500.0, 10.0), ac.Trip(_at(13, 50), _at(15), 45.0, (1,), 800.0, 9.0))
    stays = (ac.Stay('depot', _at(9, 10), _at(9, 30)), ac.Stay('depot', _at(13, 5), _at(13, 50)))
    actual = ac.DayActual(100, 19.0, _at(9), _at(15), stays, visits, trips, served=(('S:1', 0), ('S:2', 1)))
    t = lambda h, m=0: _at(h, m)  # noqa: E731
    plan = [lr.PlanTrip(t(9, 15), None, t(9, 30), frozenset({101})),
            lr.PlanTrip(t(13, 30), t(13, 0), t(13, 45), frozenset({102}), lr.PlanLunch('depot', None, 30.0))]
    assert lr.lunch_obs(DAY, actual, WINDOW, stops, plan, _norm) == lr.LunchObs(DAY, 45.0 - 15.0 - 0.0)
    assert [o.tonnes for o in lr.load_obs(DAY, actual, stops, plan)] == [0.5]      # рейс с обедом на складе — мимо
    assert len(lr.load_obs(DAY, actual, stops, [replace(p, lunch=None) for p in plan])) == 2
    store_plan = [plan[0], replace(plan[1], lunch=lr.PlanLunch('store', 102, 30.0))]
    assert lr.lunch_customers(store_plan) == {102} and lr.lunch_customers(plan) == frozenset()
    assert [o.customers for o in lr.unload_obs(DAY, actual, stops, lr.lunch_customers(store_plan))] == [(101,)]
    assert [o.customers for o in lr.unload_obs(DAY, actual, stops)] == [(101,), (102,)]
    raw = {'trips': [{'stops': [[101, '10:00']]}, {'loading_start': '13:30', 'depart': '13:45', 'return': '15:00',
                                                  'stops': [[102, '14:10']],
                                                  'lunch': {'where': 'store', 'customer': 102, 'minutes': 30.0}}]}
    assert lr.plan_trips(raw, DAY)[1].lunch == lr.PlanLunch('store', 102, 30.0)
    assert lr.plan_trips({'trips': [{'lunch': {'where': 'store', 'customer': True, 'minutes': 30}}]}, DAY)[0].lunch is None


def _lunch_obs(values_by_day):
    return [lr.LunchObs(d, v) for d, vs in values_by_day for v in vs]


TODAY = date(2026, 10, 3)
TEST_FROM = TODAY - timedelta(days=lr.HOLDOUT_DAYS)


def _days(n, minutes, test_minutes=None):
    """n дней обучения перед проверкой (по 3 машино-дня, обед minutes) и 7 дней проверки (test_minutes, по 2)."""
    train = [(TEST_FROM - timedelta(days=i + 1), [minutes] * 3) for i in range(n)]
    test = [(TEST_FROM + timedelta(days=i), [test_minutes if test_minutes is not None else minutes] * 2) for i in range(7)]
    return _lunch_obs(train + test)


def test_fit_lunch_median_step_bounds_and_acceptance():
    o = lr.fit_lunch(_days(10, 22.0), 30.0, TODAY, 30)
    assert o.accepted and o.params == {'minutes': 22.0} and o.confidence == 1.0 and 'հուսալի է՝' in o.reason
    assert (o.mae_before, o.mae_after, o.n_obs, o.n_test) == (8.0, 0.0, 30, 14)
    step = lr.fit_lunch(_days(10, 10.0), 30.0, TODAY, 30)                          # не дальше ±30% за прогон
    assert step.params == {'minutes': 21.0} and step.accepted
    assert lr.fit_lunch(_days(10, 200.0), 80.0, TODAY, 80).params == {'minutes': 90.0}   # верхний предел
    assert lr.fit_lunch(_days(10, 0.0), 5.0, TODAY, 30).params == {'minutes': 0.0}       # до 0 дойти можно
    assert lr.fit_lunch(_days(10, 30.0), 0.0, TODAY, 30).params == {'minutes': 5.0}      # и подняться с 0
    same = lr.fit_lunch(_days(10, 30.0), 30.0, TODAY, 30)
    assert not same.accepted and same.reason.startswith('գործող նորմից առնվազն 2%-ով ավելի լավ չէ')


def test_fit_lunch_too_little_data_setting_off_and_noise():
    few = lr.fit_lunch(_days(3, 22.0), 30.0, TODAY, 30)
    assert not few.accepted and few.params is None and few.reason.startswith('քիչ տվյալներ․ ուսուցում՝ 9 / 15')
    off = lr.fit_lunch(_days(10, 22.0), 0.0, TODAY, 0)
    assert not off.accepted and off.reason == 'ճաշն անջատված է կարգավորումներում (0 րոպե)․ ծրագիրն այն չի սովորում'
    # шум: в один день проверки обедали 20 мин (лучше новое 22), в другой — 40 (лучше прежние 30)
    obs = [o for o in _days(10, 22.0) if o.day < TEST_FROM]
    obs += [lr.LunchObs(TEST_FROM, 20.0)] * 6 + [lr.LunchObs(TEST_FROM + timedelta(days=1), 40.0)] * 5
    noisy = lr.fit_lunch(obs, 30.0, TODAY, 30)
    assert noisy.mae_after < 0.98 * noisy.mae_before and not noisy.accepted and noisy.reason.startswith('ոչ հուսալի')


def test_lunch_in_effect_applied_only_when_on():
    row = {'kind': 'lunch', 'scope': '', 'run_day': '2026-09-30', 'accepted': True, 'params': {'minutes': 25.0},
           'model_id': None}
    assert lr.DEFAULT_AUTO['lunch'] is False and lr.in_effect([row], {}, None).lunch is None   # по умолчанию выключено
    eff = lr.in_effect([row], {'lunch': True}, None)
    assert eff.lunch == {'minutes': 25.0} and bool(eff)
    assert lr.in_effect([row], {'lunch': False}, None).lunch is None                 # автообучение выключено
    assert lr.in_effect([{**row, 'params': {'minutes': 91.0}}], {'lunch': True}, None).lunch is None   # вне пределов
    assert lr.valid_params('lunch', {'minutes': 0.0}) and not lr.valid_params('lunch', {'minutes': True})
    _, tn, _ = lr.apply_learned(DP_NORMS, LTN, {}, eff, {})
    assert tn.lunch_minutes == 25.0
    _, off, _ = lr.apply_learned(DP_NORMS, TN, {}, eff, {})
    assert off.lunch_minutes == 0.0                                                 # обед выключен в настройках


def _plans(state, days, where='store'):
    """Черновики «Развоза» на дни facts с прогнозом сборки (views._capture_prediction): CAR1 — 101, 102 → склад → 104,
    обед по плану — после разгрузки 101 (where store) или в дороге (road)."""
    for d in days:
        lunch = {'where': where, 'customer': 101 if where == 'store' else None, 'start': '12:45', 'minutes': 30.0,
                 'added': 30.0}
        state.store.save_dispatch(d.isoformat(), {
            'trucks': ['CAR1'], 'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [101, 102]},
                                          {'id': 2, 'truck': 'CAR1', 'stops': [104]}],
            'prediction': {'trucks': {'CAR1': {'trips': [{'stops': [[101, '12:33'], [102, '13:30']], 'lunch': lunch},
                                                          {'stops': [[104, '14:30']]}]}}}}, 'qa')


class LunchFacts(FakeFacts):
    """Как FakeFacts, но день — с 12:00: после первого магазина водитель обедает не по плану (точка X) 24–26 мин,
    возвращается после 14:30."""

    def day(self, car, ds):
        d = date.fromisoformat(ds)
        kg = self.kg(d)
        tr = Track(DEPOT, datetime(d.year, d.month, d.day, 12, 0, tzinfo=TZ))
        tr.stay(20).drive(S101, 15).stay(4 + 12 * kg[101] / 1000).drive(X, 15).stay(24 + d.day % 3)
        tr.drive(S102, 15).stay(4 + 12 * kg[102] / 1000).drive(DEPOT, 15).stay(10 + 8 * kg[104] / 1000)
        tr.drive(S104, 15).stay(4 + 12 * kg[104] / 1000).drive(DEPOT, 15).stay(5)
        track = [(int(f.at.timestamp() * 1000), f.lat, f.lon, f.accuracy, None) for f in tr.fixes]
        stops = [{'stop_id': f'S:{cid}', 'customer_id': cid, 'lat': p[0], 'lon': p[1], 'weight_kg': kg[cid], 'seq': i,
                  'delivered_share': 1.0} for i, (cid, p) in enumerate(((101, S101), (102, S102), (104, S104)), 1)]
        return {'track': track, 'stops': stops}


def test_nightly_learns_lunch_and_dispatch_applies_it(client, monkeypatch):
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    state.fleet_facts = LunchFacts([TODAY - timedelta(days=i) for i in range(30, 0, -1)])
    _plans(state, state.fleet_facts.days)                     # план: обед у 101, а водитель свернул поесть в сторону
    state.store.save_learning_auto('lunch', True, 'qa')       # по умолчанию выключено — включает владелец
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 3, 10, 0))
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 3, 10, 0, tzinfo=TZ))
    lunch = next(o for o in views.run_learning(state, TODAY) if o.kind == 'lunch')
    assert lunch.accepted and lunch.params == {'minutes': 25.0} and lunch.n_test == 7, lunch.reason
    bundle = views._bundle(state)
    snap, _ = state.snapshots.cached()
    ctx = views._dispatch_ctx(state, snap, bundle, date(2026, 10, 4), views._ready_trucks(snap, bundle), [S101],
                              {101: S101})
    assert ctx.tn.lunch_minutes == 25.0 and ctx.tn.lunch_from == 210.0
    st_ = {s['kind']: s for s in client.get('/api/routes/learning/status').get_json()['status']}
    assert st_['lunch']['in_effect']['params'] == {'minutes': 25.0} and st_['lunch']['title'] == 'Ճաշ ճանապարհին'
    assert st_['lunch']['manual'] == {'minutes': 30, 'from': '12:30', 'to': '14:30'} and not st_['lunch']['default_auto']
    # стоянка-обед не попала ни в разгрузку, ни в чистые участки
    d = TODAY - timedelta(days=3)
    facts = state.fleet_facts.day('CAR1', d.isoformat())
    stops = lr.plan_stops(facts['stops'], {}, {})
    actual = ac.reconstruct(lr.track_fixes(facts['track']), stops, bundle.depot)
    other = [s for s in actual.stays if s.kind == 'other']
    assert len(other) == 1 and all(not (g.clean and g.depart < other[0].leave and g.arrive > other[0].arrive)
                                   for g in actual.legs)
    assert all(v.arrive >= other[0].leave or v.leave <= other[0].arrive for v in actual.visits)
    # обед в настройках выключен — выученный не действует
    _save(state.store, {'settings': {'truck_lunch_min': 0}})
    ctx = views._dispatch_ctx(state, snap, views._bundle(state), date(2026, 10, 4), views._ready_trucks(snap, bundle),
                              [S101], {101: S101})
    assert ctx.tn.lunch_minutes == 0.0


def test_learning_page_shows_lunch():
    js = (ROOT / 'static' / 'js' / 'routes_learning.js').read_text(encoding='utf-8')
    assert "if (kind === 'lunch') return 'ճաշ՝ '" in js and "kind === 'lunch' && m" in js
    assert "routes_learning.js') }}?v=17" in (ROOT / 'templates' / 'routes_learning.html').read_text(encoding='utf-8')


class CabLunchFacts(FakeFacts):
    """Водитель ест там, где план: после разгрузки 101 — 30 мин в кабине у магазина (стоянка у 101 = разгрузка + 30).
    Разгрузка — 4 мин + 12 мин/т (в настройках 8 + 6), день — с 12:00, после 14:30."""

    def day(self, car, ds):
        d = date.fromisoformat(ds)
        kg = self.kg(d)
        tr = Track(DEPOT, datetime(d.year, d.month, d.day, 12, 0, tzinfo=TZ))
        tr.stay(20).drive(S101, 15).stay(4 + 12 * kg[101] / 1000 + 30)
        tr.drive(S102, 15).stay(4 + 12 * kg[102] / 1000).drive(DEPOT, 15).stay(10 + 8 * kg[104] / 1000)
        tr.drive(S104, 15).stay(4 + 12 * kg[104] / 1000).drive(DEPOT, 15).stay(5)
        track = [(int(f.at.timestamp() * 1000), f.lat, f.lon, f.accuracy, None) for f in tr.fixes]
        stops = [{'stop_id': f'S:{cid}', 'customer_id': cid, 'lat': p[0], 'lon': p[1], 'weight_kg': kg[cid], 'seq': i,
                  'delivered_share': 1.0} for i, (cid, p) in enumerate(((101, S101), (102, S102), (104, S104)), 1)]
        return {'track': track, 'stops': stops}


def test_five_nights_eating_where_planned_keeps_lunch_and_unload_honest(client, monkeypatch):
    """Ревью H1: водитель обедает там, где план (у магазина, в кабине). Прежде обед по факту видел только стоянки не по
    плану — 0 — и за пять ночей сводил обед 30 → 21 → 15 → 10 → 5 → 0, а стоянка с обедом раздувала разгрузку. Теперь
    обед виден у магазина (стоянка сверх нормы разгрузки), за пять ночей остаётся ≈ 30; визит с обедом в разгрузку не
    идёт — норма разгрузки выучивается настоящая (4 + 12 мин/т), а не раздутая обедом."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    # факт — по вчерашний день последней ночи: у каждой ночи — полная неделя проверки
    state.fleet_facts = CabLunchFacts([TODAY - timedelta(days=i) for i in range(30, -4, -1)])
    _plans(state, state.fleet_facts.days)
    state.store.save_learning_auto('lunch', True, 'qa')
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 3, 10, 0))
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 3, 10, 0, tzinfo=TZ))
    bundle = views._bundle(state)
    snap, _ = state.snapshots.cached()
    lunches, unloads = [], []
    for night in range(5):
        today = TODAY + timedelta(days=night)
        by = {o.kind: o for o in views.run_learning(state, today)}
        assert by['lunch'].params is not None and 27.0 <= by['lunch'].params['minutes'] <= 33.0, by['lunch']
        ctx = views._dispatch_ctx(state, snap, bundle, today, views._ready_trucks(snap, bundle), [S101], {101: S101})
        lunches.append(ctx.tn.lunch_minutes)
        unloads.append((ctx.tn.unload_min_per_stop, ctx.tn.unload_min_per_tonne))
        u = by['unload']
        assert u.params is not None and '101' not in u.params['store_stats'], u.reason   # визит с обедом — не в учёт
        assert u.params['per_stop_min'] == pytest.approx(4, abs=1.0) and u.params['per_tonne_min'] == pytest.approx(12, abs=1.0)
    assert all(27.0 <= x <= 33.0 for x in lunches), lunches                          # не сходит на нет
    assert unloads[-1] == (pytest.approx(4, abs=1.0), pytest.approx(12, abs=1.0)), unloads


def test_capture_prediction_records_planned_lunch(client):
    """Прогноз сборки помнит обед по плану: где (магазин — после разгрузки которого, склад, дорога), начало, минуты."""
    _dispatch_setup(client, [_dorder(i + 1, cid, kg) for i, (cid, _, kg) in enumerate(EAST + WEST)])
    state = client.application.extensions['route_optimizer']
    _save(state.store, {'settings': {'truck_lunch_from': '09:30', 'truck_lunch_to': '11:30'}})
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1']})
    assert r.status_code == 200, r.get_json()
    view = [t for tr in r.get_json()['plan']['trucks'] for t in tr['trips'] if 'lunch' in t][0]
    raw, _ = state.store.load_dispatch('2026-10-01')
    trips = raw['prediction']['trucks']['CAR1']['trips']
    meal = [t['lunch'] for t in trips if 'lunch' in t]
    assert len(meal) == 1 and meal[0]['where'] == view['lunch']['where'] and meal[0]['start'] == view['lunch']['start']
    assert meal[0]['minutes'] == 30.0 and meal[0]['added'] == view['lunch']['added_min']
    if meal[0]['where'] == 'store':
        assert meal[0]['customer'] == view['stops'][view['lunch']['after_stop']]['customer_id']
    assert lr.plan_trips(raw['prediction']['trucks']['CAR1'], date(2026, 10, 1))[trips.index(
        next(t for t in trips if 'lunch' in t))].lunch is not None

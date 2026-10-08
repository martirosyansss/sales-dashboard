# -*- coding: utf-8 -*-
"""«Մեքենաները առցանց» профессионально (владелец 08.10): порядок объезда (live.sequence_check), перепробег по участкам
фактического порядка (detour_legs, ֏ — формула running_costs), следование плану (adherence), объяснения диспетчера
(apply_explanations, схема 26, API «Բացատրել» / «Չեղարկել»), малые отклонения (live_detour_min_km), «Երթուղի» в
«Վարորդներ» и Telegram.

Синтетические данные, без ERP; базы — временные. Запуск из корня проекта:  python -m pytest tests/test_route_live_pro.py -q
"""
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import live  # noqa: E402
from route_optimizer import scorecard as sc  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer.geo import haversine_km  # noqa: E402
from route_optimizer.running_costs import route_cost  # noqa: E402
from test_garage_public import LAN, _session_as, app_v2, client  # noqa: E402,F401
from test_route_live import (A, B, C, DAY, DEPOT, MID_AB, ROAD, RULES, T0, TRUCK, Track, _off, _route, facts,  # noqa: E402
                             live_app, stop)  # noqa: F401


def T(m):
    return T0 + timedelta(minutes=m)


# ============================== порядок объезда ==============================

def _seq_stops(statuses):
    pts = [A, B, C, (40.19, 44.52)]
    return [stop(f'S{i}', 10 + i, pts[i], 100.0, status, seq=i + 1) for i, status in enumerate(statuses)]


NOS = {10: 1, 11: 2, 12: 3, 13: 4}


def test_sequence_skipped_while_open_then_recovered_with_pair():
    plan = [live.PlanTrip((10, 11, 12, 13), {})]
    stops = _seq_stops(['full', 'full', 'pending', 'full'])
    trips = live.trip_of(stops, plan)
    touches = {'S0': T(10), 'S1': T(20), 'S3': T(30)}
    alerts, summary = live.sequence_check(stops, trips, plan, touches, NOS, True)
    assert summary == {'skipped': [{'stop_id': 'S2', 'name': 'Խանութ 12', 'no': 3}], 'pairs': []}
    assert len(alerts) == 1
    a = alerts[0]
    assert (a['kind'], a['active'], a['from'], a['to']) == ('sequence', True, T(30).isoformat(), None)
    assert a['jump']['no'] == 4 and a['skipped'] == [{'stop_id': 'S2', 'name': 'Խանութ 12', 'no': 3, 'open': True}]
    assert (a['lat'], a['lon']) == C                                   # где пропущенный магазин
    assert live.sequence_check(stops, trips, plan, touches, NOS, False)[0][0]['active'] is False   # прошлый день
    # пропущенный обслужен позже — эпизод закрыт (в журнале остаётся), осталась пара «№4 раньше №3»
    stops[2]['status'] = 'full'
    alerts, summary = live.sequence_check(stops, trips, plan, {**touches, 'S2': T(40)}, NOS, True)
    assert summary['skipped'] == [] and [(p['first']['no'], p['then']['no']) for p in summary['pairs']] == [(4, 3)]
    assert (alerts[0]['active'], alerts[0]['to'], alerts[0]['skipped'][0]['open']) == (False, T(40).isoformat(), False)
    # два магазина одного места — одно касание: в любом порядке id — не пара
    swapped = [live.PlanTrip((10, 11, 13, 12), {})]   # S3 в плане раньше S2, а по id — позже
    assert live.sequence_check(stops, live.trip_of(stops, swapped), swapped,
                               {'S0': T(1), 'S1': T(2), 'S2': T(3), 'S3': T(3)}, NOS, True)[1] == {'skipped': [], 'pairs': []}
    # по порядку — ни тревоги, ни пар
    assert live.sequence_check(stops, trips, plan, {f'S{i}': T(i) for i in range(4)}, NOS, True) == \
        ([], {'skipped': [], 'pairs': []})


def test_sequence_only_inside_trip_two_trips_one_episode_and_untimed_closed():
    plan = [live.PlanTrip((10, 11), {}), live.PlanTrip((12, 13), {})]
    stops = _seq_stops(['pending', 'full', 'pending', 'full'])
    trips = live.trip_of(stops, plan)
    # рейс 1: №2 раньше №1; рейс 2: №4 раньше №3 — один эпизод, пропущены оба
    alerts, summary = live.sequence_check(stops, trips, plan, {'S1': T(10), 'S3': T(50)}, NOS, True)
    assert [x['stop_id'] for x in summary['skipped']] == ['S0', 'S2']
    assert len(alerts) == 1 and [x['stop_id'] for x in alerts[0]['skipped']] == ['S0', 'S2']
    # рейс 2 целиком раньше рейса 1 — не нарушение (рейсы разные); закрытая без момента (S1) — не «перепрыгнула»
    alerts, summary = live.sequence_check(stops, trips, plan, {'S2': T(5), 'S3': T(6)}, NOS, True)
    assert alerts == [] and summary == {'skipped': [], 'pairs': []}
    # магазин вне плана — не оценивается
    extra = stops + [stop('S9', 99, A, 10.0, 'full')]
    assert live.sequence_check(extra, live.trip_of(extra, plan), plan, {'S9': T(1), 'S0': T(2), 'S1': T(3)}, NOS,
                               True)[1] == {'skipped': [], 'pairs': []}


# ============================== перепробег по участкам ==============================

class _Cost:   # машина для running_costs.route_cost — те же нормы, что TruckSpec ниже
    capacity_kg, l100, fuel_empty_l_per_100km, fuel_full_l_per_100km = 2000.0, 25.0, 20.0, 30.0
    wear_amd_per_km, wear_load_amd_per_km = 50.0, 20.0


def _legs_day():
    tr = Track().park(DEPOT, 2).drive(A)
    t_a = tr.t
    tr.park(A, 5).drive(C).drive(B)        # A → B через C: крюк
    t_b = tr.t
    tr.park(B, 5)
    dep = live.Anchor(T0, T0 + timedelta(minutes=2), DEPOT, frozenset({live.DEPOT_NODE}))
    a = live.Anchor(t_a, t_a + timedelta(minutes=5), A, frozenset({7}))
    b = live.Anchor(t_b, None, B, frozenset({8}))
    return tr, live.track_fixes(tr.pts), dep, a, b


def test_detour_legs_plan_from_plan_line_road_model_or_straight_and_cost():
    tr, moving, dep, a, b = _legs_day()
    load = live.Load(((T0, 1000.0),), 1000.0, 0.0, 0)
    truck = live.TruckSpec(2000.0, 25.0, 20.0, 30.0, wear_amd_per_km=50.0, wear_load_amd_per_km=20.0)
    rules = live.Rules(fuel_price=600.0)
    # склад → A соседние в плане: км — по линии плана; A → B в плане не соседние: дорожная модель «Развоза»
    road = replace(ROAD, pair=lambda p, q, m: (2.0, 6.0) if (p, q) == (A, B) else None)
    d0, d1 = live.detour_legs(moving, [dep, a, b], {(live.DEPOT_NODE, 7): 3.5}, road, truck, load, rules, DAY, T0, None)
    assert (d0.consecutive, d0.plan_km, d0.approx) == (True, 3.5, False)
    assert d0.km == pytest.approx(haversine_km(DEPOT, A), rel=0.02) and d0.start == dep.leave and d0.end == a.arrive
    assert (d1.consecutive, d1.plan_km, d1.plan_min, d1.approx) == (False, 2.0, 6.0, False)
    fact = haversine_km(A, C) + haversine_km(C, B)
    assert d1.km == pytest.approx(fact, rel=0.02) and d1.excess_km == pytest.approx(d1.km - 2.0)
    assert d1.minutes == pytest.approx((b.arrive - a.leave).total_seconds() / 60.0)
    # ֏ — как «Развоз»: running_costs.route_cost (топливо по грузу на борту × цена + износ) за лишние км
    assert d1.excess_km > 0 and d1.cost_amd == pytest.approx(
        route_cost([d1.excess_km, 0.0], [1000.0], _Cost()).total_amd(600.0), rel=1e-9)
    assert d0.excess_km < 0 and d0.cost_amd == 0.0                    # короче плана — не перепробег, 0 ֏
    # ни линии плана, ни дорожной модели — по прямой × извилистость, approx
    plain = live.detour_legs(moving, [dep, a, b], {}, ROAD, truck, load, rules, DAY, T0, None)
    assert all(x.approx and not x.consecutive for x in plain)
    assert plain[1].plan_km == pytest.approx(haversine_km(A, B) * ROAD.detour)
    assert plain[1].plan_min == pytest.approx(ROAD.minutes(A, B))
    # расхода машины нет — ֏ неизвестно (не «0»)
    assert live.detour_legs(moving, [dep, a, b], {}, ROAD, live.TruckSpec(2000.0), load, rules, DAY, T0,
                            None)[1].cost_amd is None
    # счёт дня: участок после его конца не считается, до первого выезда — тоже
    assert len(live.detour_legs(moving, [dep, a, b], {}, ROAD, truck, load, rules, DAY, T0, a.leave)) == 1
    assert len(live.detour_legs(moving, [dep, a, b], {}, ROAD, truck, load, rules, DAY, a.leave, None)) == 1
    assert live.detour_legs(moving, [dep, a, b], {}, ROAD, truck, load, rules, DAY, None, None) == []
    # обед в пути — его минуты не перепробег
    lunch = (a.leave + timedelta(minutes=1), a.leave + timedelta(minutes=4))
    assert live.detour_legs(moving, [dep, a, b], {}, ROAD, truck, load, rules, DAY, T0, None,
                            lunch)[1].minutes == pytest.approx(plain[1].minutes - 3.0)
    # идущий участок: от последнего отъезда до последней точки — к следующему магазину
    run = live.detour_legs(moving, [dep, a], {}, ROAD, truck, load, rules, DAY, T0, None,
                           target=live.Anchor(tr.t, None, B, frozenset({8})))
    assert run[-1].ongoing and run[-1].a is a and run[-1].end == tr.t and run[-1].km == pytest.approx(d1.km, rel=0.01)


def test_truck_cost_mirrors_route_cost():
    spec = live.TruckSpec(2000.0, 25.0, 20.0, 30.0, wear_amd_per_km=50.0, wear_load_amd_per_km=20.0)
    for kg in (0.0, 700.0, 2000.0):
        assert spec.cost_amd(4.2, kg, 610.0) == pytest.approx(route_cost([4.2, 0.0], [kg], _Cost()).total_amd(610.0))
    assert live.TruckSpec(None, 25.0).cost_amd(10.0, 500.0, 600.0) == pytest.approx(10.0 * 0.25 * 600.0)
    assert live.TruckSpec(2000.0).cost_amd(10.0, 0.0, 600.0) is None


def test_rules_detour_threshold_and_fuel_price_from_settings():
    vals = dict(st.DEFAULT_SETTINGS)
    r = live.Rules.from_settings(vals)
    assert r.detour_min_km == 1.0 and r.fuel_price_estimated is (vals.get('fuel_price_diesel') is None)
    r = live.Rules.from_settings({**vals, 'live_detour_min_km': 2.5, 'fuel_price_diesel': 640})
    assert (r.detour_min_km, r.fuel_price, r.fuel_price_estimated) == (2.5, 640.0, False)
    out, errors = st.validate_settings(vals, None)
    assert not errors and out['live_detour_min_km'] == 1.0
    for bad in (0.05, 21, None, True, '1'):
        assert 'live_detour_min_km' in st.validate_settings({**vals, 'live_detour_min_km': bad}, None)[1], bad
    # порядок объезда — в Telegram только по галочке владельца: сохранённый раньше список его не получает
    assert 'sequence' in st.LIVE_ALERT_KINDS and 'sequence' in st.LIVE_KINDS_OPT_IN
    assert 'sequence' not in out['live_alert_kinds']
    known = '["speed", "stop", "no_contact", "gps", "center", "late", "deviation"]'   # сохранено до 08.10
    assert st._loaded_alert_kinds(['speed', 'deviation'], known) == ['speed', 'deviation']


# ============================== малые отклонения, следование плану, объяснения ==============================

PLAN = [live.PlanTrip((7, 8), {})]


def _planned_detour(off_m):
    """Склад → A → в сторону на off_m от середины A–B → B → склад; A и B доставлены (план: A, затем B)."""
    tr = Track().park(DEPOT, 5).drive(A)
    at_a = tr.t
    tr.park(A, 6).drive(_off(MID_AB, off_m)).drive(B)
    at_b = tr.t
    tr.park(B, 6).drive(DEPOT).park(DEPOT, 2)
    stops = [stop('S:A', 7, A, 300.0, 'full', 1.0, at_a + timedelta(minutes=2)),
             stop('S:B', 8, B, 200.0, 'full', 1.0, at_b + timedelta(minutes=2), seq=2)]
    return tr, stops


def _view(tr, stops, rules=RULES, explained=(), now=None, detail=True):
    return live.car_view(DAY, now or tr.t + timedelta(minutes=1), facts(tr.pts, stops, [T0, tr.t]), PLAN, TRUCK, DEPOT,
                         rules, ROAD, detail, None, _route(), None, explained)


def _dev(card):
    return [a for a in card['alerts_log'] if a['kind'] == 'deviation']


def test_small_deviation_is_minor_big_one_is_alert_with_leg_excess():
    small = _view(*_planned_detour(700.0))
    (d,) = _dev(small)
    assert d['minor'] is True and d['excess_km'] < 1.0 and d['active'] is False
    assert small['deviation']['minor'] == 1 and small['deviation']['alerts'] == 0 and small['alerts']['count'] == 0
    assert small['deviation']['runs'][0]['minor'] is True and small['detour']['legs_over'] == 0
    big = _view(*_planned_detour(3000.0))
    (d,) = _dev(big)
    assert d['minor'] is False and d['excess_km'] >= 1.0 and big['deviation']['alerts'] == 1 and big['alerts']['count'] == 1
    det = big['detour']
    assert det['legs'] == 3 and det['legs_over'] == 1 and det['excess_km'] == d['excess_km'] and det['cost_amd'] > 0
    (item,) = [x for x in det['items'] if x['over']]
    assert (item['from']['kind'], item['from']['no'], item['to']['no'], item['consecutive']) == ('stop', 1, 2, True)
    assert item['line'] and all(x['line'] is None for x in det['items'] if not x['over'])
    # порог — из настроек: 10 км — та же поездка лишь «փոքր շեղում»
    assert _dev(_view(*_planned_detour(3000.0), rules=live.Rules(detour_min_km=10.0)))[0]['minor'] is True
    # карточка флота (без деталей) — только сводка
    brief = _view(*_planned_detour(3000.0), detail=False)
    assert 'items' not in brief['detour'] and brief['detour']['excess_km'] == det['excess_km']


def test_ongoing_deviation_judged_by_projected_leg_excess():
    """Идущий участок: перепробег — прогноз (проехано + от последней точки до цели по прямой × извилистость − план);
    в итог дня и ֏ он не входит."""
    def going(off_m, detail=False):
        tr = Track().park(DEPOT, 5).drive(A)
        at_a = tr.t
        tr.park(A, 6).drive(_off(MID_AB, off_m))
        stops = [stop('S:A', 7, A, 300.0, 'full', 1.0, at_a + timedelta(minutes=2)), stop('S:B', 8, B, 200.0, seq=2)]
        return _view(tr, stops, now=tr.t + timedelta(seconds=20), detail=detail)
    near = going(900.0)           # в стороне 900 м: прогноз — меньше порога, «փոքր շեղում»
    assert near['deviation']['count'] == 1 and near['deviation']['active'] is False and _dev(near)[0]['minor'] is True
    assert 'deviation' not in near['alerts']['active'] and near['explainable'] == []
    far = going(2500.0, detail=True)   # в 2,5 км от линии: тревога сразу, не после «плана + порога» проеханного
    (d,) = _dev(far)
    assert d['minor'] is False and d['projected'] is True and d['excess_km'] >= 1.0
    assert far['deviation']['active'] is True and 'deviation' in far['alerts']['active'] and far['state'] == 'alert'
    assert far['explainable'] == [{'kind': 'deviation', 'from': d['from']}]
    (leg,) = [x for x in far['detour']['items'] if x['ongoing']]
    assert leg['over'] is True and leg['projected_excess_km'] == d['excess_km'] and leg['excess_km'] < 1.0
    assert far['detour']['legs_over'] == 0 and far['detour']['excess_km'] == 0.0 and far['detour']['cost_amd'] == 0


def test_ongoing_far_off_route_on_long_leg_alerts_early():
    """Пример ревью: дальний магазин (~21 км), машина ушла от линии на 2,2 км и едет параллельно — тревога сразу."""
    far_store = (40.17, 44.70)
    mid = ((DEPOT[0] + far_store[0]) / 2, (DEPOT[1] + far_store[1]) / 2)
    off1 = (mid[0] + 0.02, mid[1])
    off2 = (off1[0], off1[1] + 0.03)
    route = _route(lines=((DEPOT, far_store, DEPOT),), stops=((9, far_store),))
    tr = Track().park(DEPOT, 10).drive(mid).drive(off1).drive(off2)
    road = replace(ROAD, detour=1.0)   # синтетический трек — по прямой: и план по прямой
    card = live.car_view(DAY, tr.t, facts(tr.pts, [stop('S:F', 9, far_store, 500.0)], [T0, tr.t]),
                         [live.PlanTrip((9,), {})], TRUCK, DEPOT, RULES, road, False, None, route)
    (d,) = _dev(card)
    assert d['active'] is True and d['minor'] is False and 'deviation' in card['alerts']['active']


def test_done_stop_without_gps_visit_is_a_via_point_no_fake_excess():
    """Закрытый водителем магазин B, у которого координата в 400 м от места разгрузки (визита по GPS нет): участок A → C —
    через B по плану, перепробега и ֏ нет (без этого — +3,7 км и ֏ в идеальный день)."""
    c_pt = (40.16, 44.47)
    road = replace(ROAD, detour=1.0)
    for b_term in (B, (B[0] + 0.0036, B[1])):
        route = _route(lines=((DEPOT, A, b_term, c_pt, DEPOT),), stops=((7, A), (8, b_term), (10, c_pt)))
        tr = Track().park(DEPOT, 10).drive(A)
        ta = tr.t
        tr.park(A, 8).drive(B)
        tb = tr.t
        tr.park(B, 8).drive(c_pt)
        tc = tr.t
        tr.park(c_pt, 8)
        stops = [stop('S:A', 7, A, 300.0, 'full', 1.0, ta + timedelta(minutes=3)),
                 stop('S:B', 8, b_term, 300.0, 'full', 1.0, tb + timedelta(minutes=3), seq=2),
                 stop('S:C', 10, c_pt, 300.0, 'full', 1.0, tc + timedelta(minutes=3), seq=3)]
        card = live.car_view(DAY, tr.t, facts(tr.pts, stops, [T0, tr.t]), [live.PlanTrip((7, 8, 10), {})], TRUCK, DEPOT,
                             RULES, road, True, None, route)
        det = card['detour']
        assert det['legs_over'] == 0 and det['excess_km'] == 0.0 and det['cost_amd'] == 0, b_term
        assert all(x['excess_km'] < 0.5 and x['consecutive'] for x in det['items']), det['items']
    # отметка B вне участка A → C (позже прибытия в C) — не промежуточная: участок без неё
    stops[1]['delivered_at'] = (tc + timedelta(minutes=30)).isoformat()
    card = live.car_view(DAY, tr.t + timedelta(minutes=40), facts(tr.pts, stops, [T0, tr.t]),
                         [live.PlanTrip((7, 8, 10), {})], TRUCK, DEPOT, RULES, road, True, None, route)
    (ac_leg,) = [x for x in card['detour']['items'] if x['from']['no'] == 1]
    assert ac_leg['consecutive'] is False and ac_leg['excess_km'] > 1.0


def test_short_unmarked_delivery_is_visited_not_skipped():
    """Разгрузка 4 мин (короче засчитанного GPS-визита) без отметки, потом обслужен следующий — не «пропущен»."""
    c_pt = (40.19, 44.51)
    tr = Track().park(DEPOT, 10).drive(A)
    ta = tr.t
    tr.park(A, 8).drive(B).park(B, 4).drive(c_pt)
    tc = tr.t
    tr.park(c_pt, 8).drive(((c_pt[0] + DEPOT[0]) / 2, (c_pt[1] + DEPOT[1]) / 2))
    route = _route(lines=((DEPOT, A, B, c_pt, DEPOT),), stops=((7, A), (8, B), (10, c_pt)))
    stops = [stop('S:A', 7, A, 300.0, 'full', 1.0, ta + timedelta(minutes=3)), stop('S:B', 8, B, 300.0, seq=2),
             stop('S:C', 10, c_pt, 300.0, 'full', 1.0, tc + timedelta(minutes=3), seq=3)]
    card = live.car_view(DAY, tr.t, facts(tr.pts, stops, [T0, tr.t]), [live.PlanTrip((7, 8, 10), {})], TRUCK, DEPOT,
                         RULES, ROAD, False, None, route)
    assert card['sequence'] == {'skipped': [], 'pairs': []} and 'sequence' not in card['alerts']['active']
    # стоянка у B короче ac.MIN_DWELL (проезд) — B пропущен
    tr = Track().park(DEPOT, 10).drive(A)
    ta = tr.t
    tr.park(A, 8).drive(B).park(B, 1).drive(c_pt)
    tc = tr.t
    tr.park(c_pt, 8).drive(((c_pt[0] + DEPOT[0]) / 2, (c_pt[1] + DEPOT[1]) / 2))
    stops[0]['delivered_at'] = (ta + timedelta(minutes=3)).isoformat()
    stops[2]['delivered_at'] = (tc + timedelta(minutes=3)).isoformat()
    card = live.car_view(DAY, tr.t, facts(tr.pts, stops, [T0, tr.t]), [live.PlanTrip((7, 8, 10), {})], TRUCK, DEPOT,
                         RULES, ROAD, False, None, route)
    assert [x['stop_id'] for x in card['sequence']['skipped']] == ['S:B']


def test_adherence_and_explained_deviation_not_counted_and_matched_by_start():
    tr, stops = _planned_detour(3000.0)
    card = _view(tr, stops)
    (d,) = _dev(card)
    dev = card['deviation']
    assert dev['adherence_pct'] == pytest.approx(100.0 * (1 - dev['off_km'] / dev['counted_km']), abs=0.2)
    assert dev['adherence_pct'] < 90 and card['stats']['adherence_pct'] == dev['adherence_pct']
    # объяснение записано по тревоге, которую видел диспетчер; к пересчёту отклонение сдвинулось на несколько точек
    a_from, a_to = datetime.fromisoformat(d['from']), datetime.fromisoformat(d['to'])
    shifted = {'id': 5, 'kind': 'deviation', 'from': (a_from - timedelta(seconds=45)).isoformat(),
               'to': (a_to - timedelta(seconds=30)).isoformat(), 'reason': 'road', 'note': 'փակ էր',
               'at': '2026-10-05T12:00:00', 'by': 'boss'}
    ex = _view(tr, stops, explained=[shifted])
    (e,) = _dev(ex)
    assert e['explained'] == {'id': 5, 'reason': 'road', 'note': 'փակ էր', 'at': '2026-10-05T12:00:00', 'by': 'boss'}
    assert e['active'] is False and ex['deviation']['explained'] == 1 and ex['deviation']['alerts'] == 0
    assert ex['deviation']['off_km'] == 0.0 and ex['deviation']['adherence_pct'] == 100.0
    assert ex['deviation']['runs'][0]['explained']['reason'] == 'road'
    # другой вид, начало дальше допуска (другой случай) — не объясняет
    later = (a_from + live.EXPLAIN_FROM_TOL + timedelta(seconds=30)).isoformat()
    for other in ({**shifted, 'kind': 'sequence'}, {**shifted, 'from': later, 'to': later}):
        assert 'explained' not in _dev(_view(tr, stops, explained=[other]))[0]


def test_apply_explanations_by_start_last_written_wins_later_incident_not_covered():
    alerts = [{'kind': 'deviation', 'from': T(40).isoformat(), 'to': None, 'active': True},
              {'kind': 'speed', 'from': T(41).isoformat(), 'to': T(42).isoformat(), 'active': False},
              {'kind': 'deviation', 'from': T(48).isoformat(), 'to': T(55).isoformat(), 'active': False}]
    expl = [{'id': 1, 'kind': 'deviation', 'from': T(39).isoformat(), 'to': T(44).isoformat(), 'reason': 'refuel'},
            {'id': 2, 'kind': 'deviation', 'from': T(41).isoformat(), 'to': T(44).isoformat(), 'reason': 'other'},
            {'id': 3, 'kind': 'speed', 'from': T(41).isoformat(), 'to': T(42).isoformat(), 'reason': 'other'}]
    live.apply_explanations(alerts, expl)
    assert alerts[0]['explained']['id'] == 2 and alerts[0]['active'] is False   # идущая — тот же случай, последняя запись
    assert 'explained' not in alerts[1]                                            # скорость не объясняется
    assert 'explained' not in alerts[2]   # новое отклонение (начало через 8 мин) — другой случай, хоть и пересекается
    assert live.adherence([], None, None, 0.0) == (None, 0.0)


def test_sequence_explanation_does_not_cover_episode_grown_with_new_skips():
    """Пример ревью: в 10:35 объяснён пропуск №2; к 11:30 тот же эпизод пропустил и №4 — объяснять заново."""
    stops = _seq_stops(['full', 'pending', 'full', 'pending']) + [stop('S4', 14, (40.20, 44.53), 100.0, 'full', seq=5)]
    plan = [live.PlanTrip((10, 11, 12, 13, 14), {})]
    trips = live.trip_of(stops, plan)
    nos = {**NOS, 14: 5}
    (early,), _ = live.sequence_check(stops, trips, plan, {'S0': T(10), 'S2': T(30)}, nos, True)
    expl = [{'id': 1, 'kind': 'sequence', 'from': early['from'], 'to': T(35).isoformat(), 'reason': 'customer',
             'stops': [x['stop_id'] for x in early['skipped']]}]
    assert expl[0]['stops'] == ['S1']
    live.apply_explanations([early], expl)
    assert early['explained']['id'] == 1
    (grown,), _ = live.sequence_check(stops, trips, plan, {'S0': T(10), 'S2': T(30), 'S4': T(90)}, nos, True)
    assert [x['no'] for x in grown['skipped']] == [2, 4] and grown['from'] == early['from']
    live.apply_explanations([grown], expl)
    assert 'explained' not in grown and grown['active'] is True


def test_sequence_alert_in_card_and_explained():
    tr = Track().park(DEPOT, 5).drive(B)
    at_b = tr.t
    tr.park(B, 8).drive(A).park(A, 8).drive(DEPOT).park(DEPOT, 2)
    stops = [stop('S:A', 7, A, 300.0), stop('S:B', 8, B, 200.0, 'full', 1.0, at_b + timedelta(minutes=2), seq=2)]
    card = _view(tr, stops, now=tr.t + timedelta(days=1))   # прошлый день: тревога в журнале, не «сейчас»
    (s,) = [a for a in card['alerts_log'] if a['kind'] == 'sequence']
    assert s['skipped'] == [{'stop_id': 'S:A', 'name': 'Խանութ 7', 'no': 1, 'open': False}] and s['active'] is False
    assert s['jump']['stop_id'] == 'S:B' and s['to'] is not None              # A потом посещён по GPS — эпизод закрыт
    seq = card['sequence']
    assert seq['skipped'] == [] and [(p['first']['no'], p['then']['no']) for p in seq['pairs']] == [(2, 1)]
    ex = _view(tr, stops, now=tr.t + timedelta(days=1), explained=[
        {'id': 9, 'kind': 'sequence', 'from': s['from'], 'to': s['from'], 'reason': 'customer', 'note': '',
         'stops': ['S:A']}])
    assert [a for a in ex['alerts_log'] if a['kind'] == 'sequence'][0]['explained']['reason'] == 'customer'


# ============================== база: схема 26 ==============================

def test_schema_26_migrates_from_24_and_from_25_and_explanations_roundtrip(tmp_path, caplog):
    path = str(tmp_path / 'r.db')
    s = st.Store(path)
    s.save_truck_driver('CAR1', '2026-10-01', 'Արամ', 'qa')

    def version_and_tables():
        with closing(sqlite3.connect(path)) as conn:
            v = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
        return v, names
    v, names = version_and_tables()
    # версия — текущая: дальше 26 — свои шаги (27 — Telegram-бот, №91)
    assert v == str(st.SCHEMA_VERSION) and {'customer_day_until', 'live_explain', 'live_explain_day'} <= names
    # 24 → 26: база до обеих веток (нет ни срока магазина, ни объяснений)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE live_explain')
        conn.execute('DROP TABLE customer_day_until')
        conn.execute("UPDATE meta SET value = '24' WHERE key = 'schema_version'")
        conn.commit()
    assert st.Store(path).truck_drivers('2026-10-02')[0] == {'CAR1': 'Արամ'}
    v, names = version_and_tables()
    assert v == str(st.SCHEMA_VERSION) and {'customer_day_until', 'live_explain', 'live_explain_day'} <= names
    # 25 → 26: база, которую уже мигрировала ветка until-time (её таблица со строкой остаётся как была)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE live_explain')
        conn.execute("INSERT INTO customer_day_until VALUES('2026-10-03', 501, 660, 'x', 'qa')")
        conn.execute("UPDATE meta SET value = '25' WHERE key = 'schema_version'")
        conn.commit()
    s = st.Store(path)
    assert s.live_explanations('2026-10-03') == {}
    v, names = version_and_tables()
    with closing(sqlite3.connect(path)) as conn:
        assert v == str(st.SCHEMA_VERSION) and conn.execute('SELECT * FROM customer_day_until').fetchall() == \
            [('2026-10-03', 501, 660, 'x', 'qa')]
    assert st._MIGRATIONS[24] == (st._CUSTOMER_DAY_UNTIL_TABLE,)
    assert st._MIGRATIONS[25] == (st._LIVE_EXPLAIN_TABLE, st._LIVE_EXPLAIN_INDEX)
    # шаг 24 → 25 — байт в байт строка DDL ветки until-time (2eec5b5): иначе база, мигрированная одной из веток, разойдётся
    assert st._CUSTOMER_DAY_UNTIL_TABLE == (
        "CREATE TABLE IF NOT EXISTS customer_day_until(day TEXT NOT NULL, "
        "customer_id INTEGER NOT NULL CHECK (customer_id > 0), t1 INTEGER NOT NULL CHECK (t1 BETWEEN 0 AND 1439), "
        "updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (day, customer_id))")
    # запись, чтение, «Չեղարկել»
    a, b = T0.isoformat(), (T0 + timedelta(minutes=12)).isoformat()
    new = s.add_live_explanation('2026-10-03', 'CAR1', 'deviation', a, b, 'refuel', 'ԱԳԼՑ', 'boss')
    seq = s.add_live_explanation('2026-10-03', 'CAR1', 'sequence', a, b, 'customer', '', 'boss', ['S:1', 'S:2'])
    got = s.live_explanations('2026-10-03')
    assert list(got) == ['CAR1'] and got['CAR1'][0] == {'id': new, 'kind': 'deviation', 'from': a, 'to': b, 'stops': [],
                                                        'reason': 'refuel', 'note': 'ԱԳԼՑ', 'at': got['CAR1'][0]['at'],
                                                        'by': 'boss'}
    assert got['CAR1'][1]['id'] == seq and got['CAR1'][1]['stops'] == ['S:1', 'S:2']
    for bad in (dict(kind='speed'), dict(reason='lunch'), dict(note='x' * 201), dict(note='a\x07b'), dict(note=' x'),
                dict(t_to=(T0 - timedelta(minutes=1)).isoformat()), dict(t_from='09:00'), dict(day='03.10.2026'),
                dict(car_code=''), dict(stops='S:1'), dict(stops=['']), dict(stops=['x' * 65]),
                dict(stops=['s'] * 201)):
        args = {'day': '2026-10-03', 'car_code': 'CAR1', 'kind': 'deviation', 't_from': a, 't_to': b,
                'reason': 'refuel', 'note': '', **bad}
        with pytest.raises(ValueError):
            s.add_live_explanation(user='boss', **args)
    assert st.check_live_explanation('sequence', 'other', '  ok ') == ('ok', {})
    assert set(st.check_live_explanation('x', 'y', 5)[1]) == {'kind', 'reason', 'note'}
    assert s.delete_live_explanation(new) == ('2026-10-03', 'CAR1') and s.delete_live_explanation(new) is None
    with closing(sqlite3.connect(path)) as conn:   # битые строки — мимо, с предупреждением: карта от них не падает
        conn.execute("INSERT INTO live_explain(day, car_code, kind, t_from, t_to, reason, note, created_at) "
                     "VALUES('2026-10-03', 'CAR1', 'deviation', 'x', 'y', 'other', '', 'z')")
        conn.execute("INSERT INTO live_explain(day, car_code, kind, t_from, t_to, reason, note, stops, created_at) "
                     f"VALUES('2026-10-03', 'CAR1', 'deviation', '{a}', '{b}', 'other', '', 'nope', 'z')")
        conn.commit()
    with caplog.at_level('WARNING'):
        assert [e['id'] for e in s.live_explanations('2026-10-03')['CAR1']] == [seq]
    assert sum('не читается' in r.getMessage() for r in caplog.records) == 2


# ============================== API «Բացատրել» ==============================

def _skip_plan(state):
    """План машины 1 фикстуры live_app — сначала B, потом A: машина стоит у A (GPS-визит засчитан), B ещё открыт — B
    пропущен (тревога sequence идёт)."""
    from route_optimizer import dispatch as dp
    draft = dp.Draft.from_json(state.store.load_dispatch('2026-10-03')[0])
    draft.trips[0].stops = [8, 7]
    state.store.save_dispatch('2026-10-03', draft.to_json(), 'qa')
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])   # новый факт — без кэша карточек


def _car1(client):
    return {t['car_code']: t for t in client.get('/api/routes/live', base_url=LAN).get_json()['trucks']}['CAR1']


def test_api_explain_admin_only_json_csrf_and_undo(client, live_app):
    state = live_app.app.extensions['route_optimizer']
    _skip_plan(state)
    h = _session_as(client, 'boss', base=LAN)
    assert client.get('/api/routes/live', base_url=LAN).get_json()['can_explain'] is True
    car = _car1(client)
    assert 'sequence' in car['alerts']['active'] and car['state'] == 'alert'
    assert car['sequence']['skipped'][0]['stop_id'] == 'S:B'
    (ex,) = car['explainable']
    body = {'date': '2026-10-03', 'car': 'CAR1', 'kind': 'sequence',   # начало — сдвинуто на минуту: в допуске
            'from': (datetime.fromisoformat(ex['from']) + timedelta(minutes=1)).isoformat(),
            'reason': 'customer', 'note': '  խնդրեց  '}
    url = '/api/routes/live/explain'
    # «Гараж»: карта только на чтение — POST закрыт гейтом, кнопки нет
    hg = _session_as(client, 'garage1', base=LAN)
    assert client.get('/api/routes/live', base_url=LAN).get_json()['can_explain'] is False
    assert client.post(url, json=body, base_url=LAN, headers=hg).status_code == 403
    assert client.post('/api/routes/live/unexplain', json={'id': 1}, base_url=LAN, headers=hg).status_code == 403
    h = _session_as(client, 'boss', base=LAN)
    r = client.post(url, json=body, base_url=LAN)                              # без токена формы
    assert r.status_code == 403 and r.get_json()['error'] == 'csrf'
    assert client.post(url, data='x', content_type='text/plain', base_url=LAN, headers=h).status_code == 415
    for bad, field in (({'reason': 'x'}, 'reason'), ({'note': 'ա' * 201}, 'note'), ({'kind': 'speed'}, 'kind'),
                       ({'date': '2026-10-04'}, 'date'), ({'car': ''}, 'car'), ({'from': 'x'}, 'from')):
        r = client.post(url, json={**body, **bad}, base_url=LAN, headers=h)
        assert r.status_code == 400 and field in r.get_json()['errors'], bad
    gone = {**body, 'from': '2026-10-03T07:00:00+04:00'}
    assert client.post(url, json=gone, base_url=LAN, headers=h).status_code == 404
    assert client.post(url, json={**body, 'car': 'NOPE'}, base_url=LAN, headers=h).status_code == 404
    r = client.post(url, json=body, base_url=LAN, headers=h)
    assert r.status_code == 200, r.get_json()
    eid = r.get_json()['id']
    (saved,) = state.store.live_explanations('2026-10-03')['CAR1']
    assert (saved['id'], saved['kind'], saved['from'], saved['reason'], saved['note'], saved['by'], saved['stops']) == \
        (eid, 'sequence', ex['from'], 'customer', 'խնդրեց', 'boss', ['S:B'])
    # идущая тревога — до последней точки GPS (не «сейчас»): позднее пришедший случай этим не объясняется
    assert saved['to'] == car['position']['at']
    # сразу: тревога объяснена — не «сейчас», без «Բացատրել»; в журнале — серой с причиной
    car = _car1(client)
    assert 'sequence' not in car['alerts']['active'] and car['explainable'] == [] and car['state'] != 'alert'
    truck = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    (s,) = [a for a in truck['alerts_log'] if a['kind'] == 'sequence']
    assert s['explained']['reason'] == 'customer' and s['explained']['id'] == eid
    assert client.post(url, json=body, base_url=LAN, headers=h).status_code == 409   # уже объяснена
    # «Չեղարկել»
    un = '/api/routes/live/unexplain'
    for bad in ({}, {'id': 'x'}, {'id': True}, {'id': 0}):
        assert client.post(un, json=bad, base_url=LAN, headers=h).status_code == 400, bad
    assert client.post(un, json={'id': eid}, base_url=LAN, headers=h).status_code == 200
    assert client.post(un, json={'id': eid}, base_url=LAN, headers=h).status_code == 404
    assert 'sequence' in _car1(client)['alerts']['active']


# ============================== «Վարորդներ»: Երթուղի ==============================

def _cstop(sid, car, driver):
    return {'stop_id': sid, 'car_code': car, 'status': 'full', 'driver_id': driver, 'helper_id': None,
            'customer_id': None, 'name': sid}


def test_scorecard_route_metric_in_score_none_without_data():
    days = []
    for i in range(3):
        crew = {'stops': [_cstop(f'S:{i}:1', 'CAR1', 1), _cstop(f'S:{i}:2', 'CAR2', 2)]}
        days.append((date(2026, 10, 1 + i), sc.day_summary(crew, {})))
    # у машины 1 есть трек и линия плана (2 дня из 3), у машины 2 — нет: у водителя 2 значения нет, это не штраф
    route = {('2026-10-01', 'CAR1'): (40.0, 2.0), ('2026-10-02', 'CAR1'): (60.0, 18.0)}
    out = {r['key']: r for r in sc.period(days, {1: 'Արամ', 2: 'Գոռ'}, None, route)['drivers']}
    r1, r2 = out['driver:1'], out['driver:2']
    assert r1['route_pct'] == pytest.approx(100 * (1 - 20 / 100), abs=0.05) and r1['route_km'] == 100.0
    assert r1['route_days'] == 2 and r2['route_days'] == 0
    # в балле с весом 15 (владелец 08.10): у водителя 1 других показателей нет — балл только по маршруту (80 % → 40);
    # у водителя 2 маршрута нет — показатель выпадает, не штраф
    assert sc.WEIGHTS['route'] == 15 and r1['score'] == 40.0 and set(r1['parts']) == {'route'}
    assert r2['route_pct'] is None and r2['parts'] == {} and r2['score'] is None and r2['enough_data'] is False
    det = {d['date']: d['route_pct'] for d in r1['detail']}
    assert det == {'2026-10-03': None, '2026-10-02': 70.0, '2026-10-01': 95.0}
    total, parts = sc.score({'order': 90, 'route': 82.5})   # route 82.5 → 50 (шкала 95…70)
    assert total == pytest.approx((100 * 10 + 50 * 15) / 25, abs=0.05) and set(parts) == {'order', 'route'}


def test_score_routes_cache_pending_and_failures_not_cached(monkeypatch):
    from route_optimizer import views
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', False)
    calls = []

    def compute(ds):
        calls.append(ds)
        return None if ds == '2026-10-02' else {'CAR1': (10.0, 1.0)}
    cache = views._ScoreRoutes()
    ready, todo = cache.get({'2026-10-01': 'a', '2026-10-02': 'b'}, compute)
    assert ready == {'2026-10-01': {'CAR1': (10.0, 1.0)}} and todo == ['2026-10-02'] and calls == ['2026-10-01', '2026-10-02']
    ready, todo = cache.get({'2026-10-01': 'a'}, compute)                     # тот же отпечаток — без расчёта
    assert ready == {'2026-10-01': {'CAR1': (10.0, 1.0)}} and todo == [] and len(calls) == 2
    cache.get({'2026-10-01': 'a2'}, compute)                                   # отпечаток сменился — пересчёт
    assert calls[-1] == '2026-10-01' and len(calls) == 3


# ============================== Telegram ==============================

def test_telegram_skips_minor_and_explained_and_texts_sequence():
    from route_optimizer import live_alerts as la
    rules = replace(RULES, alert_kinds=('deviation', 'sequence'), quiet=None)
    now = T(30)
    base = {'kind': 'deviation', 'from': T(25).isoformat(), 'to': None, 'active': True, 'km': 2.0, 'lat': A[0],
            'lon': A[1], 'minor': False, 'excess_km': 3.4}
    seq = {'kind': 'sequence', 'from': T(20).isoformat(), 'to': None, 'active': True, 'lat': A[0], 'lon': A[1],
           'skipped': [{'stop_id': 'S1', 'name': 'Արարատ', 'no': 11, 'open': True}],
           'jump': {'stop_id': 'S3', 'name': 'Նոր', 'no': 13}}
    tg = la.TgRules()   # бот №91: решения — la.plan (записей нет), тексты — HTML, уровень ⚪ (по умолчанию)
    cards = {'CAR1': {'car_code': 'CAR1', 'alerts_log': [{**base, 'minor': True, 'active': False},
                                                         {**seq, 'active': False, 'explained': {'id': 1}}]}}
    got = la.plan(cards, rules, tg, now, {})
    assert got.actions == [] and got.active == frozenset()                 # не отмечены: вырастет в тревогу — уйдёт
    cards['CAR1']['alerts_log'] = [base, seq]
    msgs = la.plan(cards, rules, tg, now, {}).actions
    assert [m.kind for m in msgs] == ['deviation', 'sequence']
    assert 'Ավելորդ վազք հատվածում՝ ≈ 3,4 կմ' in msgs[0].body
    lines = msgs[1].body.splitlines()
    assert lines[0] == '⚪ <b>Խանութներ բաց են թողնված (հերթականություն)</b>' and 'Բաց թողնված՝ №11 Արարատ։' in lines
    assert 'Նախքան դրանք սպասարկվել է՝ №13 Նոր։' in lines
    assert [m.kind for m in la.plan(cards, replace(rules, alert_kinds=('deviation',)), tg, now, {}).actions] == \
        ['deviation']


# ============================== ревью 2: извилистость идущего участка, открытая водителем, столбец stops ==============================

FAR = (40.17, 44.70)   # дальний магазин: ~21 км по прямой от склада


def _far_view(path, leg_ratio):
    """Машина едет к дальнему магазину; линия плана — по дорогам с км = прямая × leg_ratio (RouteGeometry.leg_km), у
    Road — глобальная извилистость 1,3: прогноз остатка — по извилистости самого плана участка."""
    lines = ((DEPOT, FAR, DEPOT),)
    geo = live.RouteGeometry(lines, lines, (), leg_km={(DEPOT, FAR): haversine_km(DEPOT, FAR) * leg_ratio})
    tr = Track().park(DEPOT, 10)
    for p in path:
        tr.drive(p)
    return live.car_view(DAY, tr.t, facts(tr.pts, [stop('S:F', 9, FAR, 500.0)], [T0, tr.t]), [live.PlanTrip((9,), {})],
                         TRUCK, DEPOT, RULES, ROAD, True, None, live.PlanRoute(geo, ((9, FAR),), None))


def _frac(f, north=0.0):
    return (DEPOT[0] + (FAR[0] - DEPOT[0]) * f + north, DEPOT[1] + (FAR[1] - DEPOT[1]) * f)


def test_ongoing_projection_uses_leg_own_ratio_sidestep_minor_big_detour_alert():
    # заезд в сторону на 450 м (заправка у дороги) и обратно на линию: план участка ×1,05 — не тревога
    side = _far_view([_frac(0.10), _frac(0.12, 0.004), _frac(0.14, 0.004), _frac(0.16), _frac(0.20)], 1.05)
    (d,) = _dev(side)
    assert d['minor'] is True and d['projected'] is True and d['excess_km'] < 1.0 and 'deviation' not in side['alerts']['active']
    (leg,) = side['detour']['items']
    assert leg['plan_km'] == pytest.approx(haversine_km(DEPOT, FAR) * 1.05, abs=0.05) and leg['over'] is False
    # настоящий объезд: ушёл на 3 км от линии и едет параллельно — тревога сразу, не после 22 км
    far = _far_view([_frac(0.10), _frac(0.12, 0.027), _frac(0.25, 0.027)], 1.05)
    (d,) = _dev(far)
    assert d['minor'] is False and d['excess_km'] >= 1.0 and d['active'] is True and far['state'] == 'alert'
    # прямая A → B короче LEG_RATIO_MIN_KM — извилистость модели; длинный план — не больше LEG_RATIO_MAX
    assert live.LEG_RATIO_MIN_KM == 0.2 and live.LEG_RATIO_MAX == 3.0


def test_in_progress_with_short_finished_visit_is_visited_not_skipped():
    """Водитель открыл магазин (in_progress), разгрузился 4 мин и уехал, не закрыв, — не «пропущен»."""
    c_pt = (40.19, 44.51)
    tr = Track().park(DEPOT, 10).drive(A)
    ta = tr.t
    tr.park(A, 8).drive(B).park(B, 4).drive(c_pt)
    tc = tr.t
    tr.park(c_pt, 8).drive(((c_pt[0] + DEPOT[0]) / 2, (c_pt[1] + DEPOT[1]) / 2))
    route = _route(lines=((DEPOT, A, B, c_pt, DEPOT),), stops=((7, A), (8, B), (10, c_pt)))
    stops = [stop('S:A', 7, A, 300.0, 'full', 1.0, ta + timedelta(minutes=3)),
             stop('S:B', 8, B, 300.0, 'in_progress', seq=2),
             stop('S:C', 10, c_pt, 300.0, 'full', 1.0, tc + timedelta(minutes=3), seq=3)]
    card = live.car_view(DAY, tr.t, facts(tr.pts, stops, [T0, tr.t]), [live.PlanTrip((7, 8, 10), {})], TRUCK, DEPOT,
                         RULES, ROAD, False, None, route)
    assert card['sequence'] == {'skipped': [], 'pairs': []} and 'sequence' not in card['alerts']['active']


def test_store_adds_stops_column_to_first_version_live_explain(tmp_path):
    """База схемы 26 первой версии ветки (live_explain без stops) — столбец добавляется при открытии, строки целы."""
    path = str(tmp_path / 'r.db')
    st.Store(path).load()
    a, b = T0.isoformat(), (T0 + timedelta(minutes=5)).isoformat()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE live_explain')
        conn.execute(
            "CREATE TABLE live_explain(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, "
            "car_code TEXT NOT NULL CHECK (length(car_code) BETWEEN 1 AND 64), "
            "kind TEXT NOT NULL CHECK (kind IN ('deviation', 'sequence')), t_from TEXT NOT NULL, t_to TEXT NOT NULL, "
            "reason TEXT NOT NULL CHECK (reason IN ('refuel', 'repair', 'customer', 'road', 'other')), "
            "note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 200), created_at TEXT NOT NULL, created_by TEXT)")
        conn.execute("INSERT INTO live_explain(day, car_code, kind, t_from, t_to, reason, note, created_at, created_by) "
                     f"VALUES('2026-10-03', 'CAR1', 'deviation', '{a}', '{b}', 'road', 'հին', 'x', 'boss')")
        conn.commit()
    s = st.Store(path)
    (row,) = s.live_explanations('2026-10-03')['CAR1']
    assert (row['note'], row['stops']) == ('հին', [])
    s.add_live_explanation('2026-10-03', 'CAR1', 'sequence', a, b, 'other', '', 'boss', ['S:1'])
    assert s.live_explanations('2026-10-03')['CAR1'][1]['stops'] == ['S:1']
    with closing(sqlite3.connect(path)) as conn:
        assert 'stops' in {r[1] for r in conn.execute('PRAGMA table_info(live_explain)')}

# -*- coding: utf-8 -*-
"""«Մեքենաները առցանց» (ответ владельца №76): расчёты карточки машины (route_optimizer.live) — км и топливо по загрузке,
остаток груза при нескольких рейсах, следующий магазин и ETA, все тревоги, пустые данные и старый APK без `device`;
факт терминала (courier.live.LiveSource); API, доступ (администратор, «Гараж», в т.ч. из интернета; 401/403/404) и пороги
в настройках.

Синтетические данные, без ERP; базы — временные. Запуск из корня проекта:  python -m pytest tests/test_route_live.py -q
"""
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import live  # noqa: E402
from route_optimizer.geo import Fix, haversine_km, track_km, track_steps  # noqa: E402

Y = ac.YEREVAN
DAY = date(2026, 10, 5)
DEPOT = (40.1500, 44.4500)
A = (40.1700, 44.4700)
B = (40.1800, 44.4900)
C = (40.1600, 44.5100)
T0 = datetime(2026, 10, 5, 9, 0, tzinfo=Y)
ROAD = live.Road(1.3, 25.0, 45.0, (40.1792, 44.4991), 12.0)
RULES = live.Rules()
TRUCK = live.TruckSpec(capacity_kg=2000.0, l100=25.0, empty_l100=20.0, full_l100=30.0)


def ms(t):
    return round(t.timestamp() * 1000)


class Track:
    """Синтетический трек терминала: стоянки (точка раз в 60 с, скорость 0) и езда по прямой (раз в 15 с, 10 м/с)."""

    def __init__(self, start=T0):
        self.t = start
        self.pts = []
        self.pos = None

    def park(self, p, minutes):
        n = int(minutes)
        for i in range(n + 1):
            self.pts.append((ms(self.t + timedelta(minutes=i)), p[0], p[1], 8.0, 0.0, None))
        self.t += timedelta(minutes=n)
        self.pos = p
        return self

    def drive(self, b, speed_ms=10.0, step_s=15):
        a = self.pos
        km = haversine_km(a, b)
        steps = max(1, math.ceil(km * 1000 / (speed_ms * step_s)))
        for i in range(1, steps + 1):
            f = i / steps
            self.t += timedelta(seconds=step_s)
            self.pts.append((ms(self.t), a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, 8.0, speed_ms, 90.0))
        self.pos = b
        return self


def stop(sid, cid, p, kg, status='pending', share=None, at=None, unweighed=0, seq=1):
    return {'stop_id': sid, 'customer_id': cid, 'name': f'Խանութ {cid}', 'lat': p[0], 'lon': p[1], 'seq': seq,
            'weight_kg': kg, 'unweighed': unweighed, 'status': status, 'share': share,
            'delivered_at': at.isoformat() if at else None}


def facts(track=(), stops=(), contacts=(), device=None, devices=(), closed_at=None, last_contact=None):
    contacts = [c.isoformat() for c in contacts]
    return {'stops': list(stops), 'track': list(track), 'drivers': ['Արամ'], 'contacts': contacts,
            'last_contact': last_contact.isoformat() if last_contact else (contacts[-1] if contacts else None),
            'device': device, 'devices': [(a.isoformat(), g) for a, g in devices],
            'closed_at': closed_at.isoformat() if closed_at else None}


def view(f, now, plan=(), truck=TRUCK, rules=RULES, depot=DEPOT, detail=False):
    return live.car_view(DAY, now, f, list(plan), truck, depot, rules, ROAD, detail)


# ============================== км и топливо по загрузке ==============================

def test_track_steps_sum_is_track_km():
    tr = Track().park(DEPOT, 3).drive(A).park(A, 4).drive(B)
    fixes = [Fix(datetime.fromtimestamp(p[0] / 1000, Y), p[1], p[2], p[3]) for p in tr.pts]
    assert math.isclose(sum(d for _, d in track_steps(fixes)), track_km(fixes), rel_tol=0, abs_tol=1e-12)


def test_fuel_formula_by_load_segments():
    """running_costs: л = км × (пусто + (полно − пусто) × груз / грузоподъёмность) / 100 — груз в конце сегмента."""
    t = T0
    moving = [Fix(t, *DEPOT, 1.0), Fix(t + timedelta(minutes=10), *A, 1.0), Fix(t + timedelta(minutes=20), *B, 1.0)]
    load = live.Load(((t, 1000.0), (t + timedelta(minutes=10), -600.0)), 1000.0, 600.0, 0)
    got = live.fuel_liters(moving, load, TRUCK)
    want = haversine_km(DEPOT, A) * (20 + 10 * 1000 / 2000) / 100 + haversine_km(A, B) * (20 + 10 * 400 / 2000) / 100
    assert math.isclose(got, want, rel_tol=1e-12)
    # нормы по загрузке нет — расход машины; нет никакой — неизвестно
    assert math.isclose(live.fuel_liters(moving, load, live.TruckSpec(2000.0, 25.0)),
                        (haversine_km(DEPOT, A) + haversine_km(A, B)) * 0.25, rel_tol=1e-12)
    assert live.fuel_liters(moving, load, live.TruckSpec(2000.0)) is None


def test_truck_rate_mirrors_route_cost():
    from route_optimizer.running_costs import route_cost
    spec = live.TruckSpec(capacity_kg=3000.0, l100=22.0, empty_l100=18.0, full_l100=27.0)

    class T:
        capacity_kg, l100, fuel_empty_l_per_100km, fuel_full_l_per_100km = 3000.0, 22.0, 18.0, 27.0
    cost = route_cost([10.0, 0.0], [1200.0], T())
    assert math.isclose(cost.liters, 10.0 * spec.rate(1200.0) / 100, rel_tol=1e-12)
    assert spec.rate(0) == 18.0 and spec.rate(3000) == 27.0
    assert live.TruckSpec(None, 22.0, 18.0, 27.0).rate(1000) == 18.0   # грузоподъёмности нет — без загрузки


def test_one_trip_full_day_km_fuel_load():
    """Склад → A (600 кг, доставлено всё) → B (400 кг, половина) → склад. Топливо — по грузу на каждом участке."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(B)
    at_b = tr.t
    tr.park(B, 8).drive(DEPOT).park(DEPOT, 3)
    stops = [stop('S:A', 1, A, 600.0, 'full', 1.0, at_a + timedelta(minutes=2), seq=1),
             stop('S:B', 2, B, 400.0, 'partial', 0.5, at_b + timedelta(minutes=2), seq=2)]
    plan = [live.PlanTrip((1, 2), {1: at_a, 2: at_b}, T0 + timedelta(minutes=10))]
    now = tr.t + timedelta(minutes=1)
    card = view(facts(tr.pts, stops, [T0, now]), now, plan)
    km = haversine_km(DEPOT, A) + haversine_km(A, B) + haversine_km(B, DEPOT)
    assert card['km'] == pytest.approx(km, abs=0.1)
    want = (haversine_km(DEPOT, A) * (20 + 10 * 1000 / 2000) + haversine_km(A, B) * (20 + 10 * 400 / 2000)
            + haversine_km(B, DEPOT) * (20 + 10 * 200 / 2000)) / 100   # половина B (200 кг) едет назад
    assert card['fuel_l'] == pytest.approx(want, abs=0.05)
    assert card['load'] == {'remaining_kg': 200, 'loaded_kg': 1000, 'delivered_kg': 800, 'unweighed_lines': 0,
                            'trips_gone': 1, 'trips': 1}
    assert card['stores'] == {'done': 2, 'total': 2, 'in_progress': 0}
    assert card['closed'] is True and card['state'] == 'closed' and card['next'] is None


def _two_trips():
    """Рейс 1: A (600, всё) и B (400, половина) → склад; рейс 2: C (300). Возвращает (трек до склада, точки, план)."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(B)
    at_b = tr.t
    tr.park(B, 8).drive(DEPOT)
    stops = [stop('S:A', 1, A, 600.0, 'full', 1.0, at_a + timedelta(minutes=2), seq=1),
             stop('S:B', 2, B, 400.0, 'partial', 0.5, at_b + timedelta(minutes=2), seq=2),
             stop('S:C', 3, C, 300.0, unweighed=2, seq=3)]
    plan = [live.PlanTrip((1, 2), {1: at_a, 2: at_b}, T0 + timedelta(minutes=10)),
            live.PlanTrip((3,), {3: tr.t + timedelta(minutes=40)}, tr.t + timedelta(minutes=20))]
    return tr, stops, plan


def test_multi_trip_second_trip_counts_only_after_departure():
    tr, stops, plan = _two_trips()
    tr.park(DEPOT, 15)                                              # загружается на складе — рейс 2 ещё не уехал
    now = tr.t
    card = view(facts(tr.pts, stops, [T0, now]), now, plan)
    assert card['load']['trips_gone'] == 1 and card['load']['remaining_kg'] == 200   # остаток рейса 1 (отказ B)
    assert card['load']['unweighed_lines'] == 0                     # строки без веса — у точки рейса 2, не на борту
    assert card['state'] != 'closed' and card['next']['stop_id'] == 'S:C'
    tr.drive(((DEPOT[0] + C[0]) / 2, (DEPOT[1] + C[1]) / 2))       # выехал с рейсом 2, на полпути к C
    now = tr.t
    card = view(facts(tr.pts, stops, [T0, now]), now, plan)
    assert card['load'] == {'remaining_kg': 500, 'loaded_kg': 1300, 'delivered_kg': 800, 'unweighed_lines': 2,
                            'trips_gone': 2, 'trips': 2}


def test_depot_visit_mid_trip_is_not_a_new_trip():
    """Заехал на склад, не закрыв рейс 1 (B не отмечен), — рейс 2 не уехал, его груз не считается."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(DEPOT).park(DEPOT, 5).drive(B)
    stops = [stop('S:A', 1, A, 600.0, 'full', 1.0, at_a + timedelta(minutes=2), seq=1),
             stop('S:B', 2, B, 400.0, seq=2), stop('S:C', 3, C, 300.0, seq=3)]
    plan = [live.PlanTrip((1, 2), {}), live.PlanTrip((3,), {})]
    card = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)
    assert card['load']['trips_gone'] == 1 and card['load']['remaining_kg'] == 400


def test_departed_trips_rules():
    t = lambda m: T0 + timedelta(minutes=m)   # noqa: E731
    trips = {'a': 0, 'b': 0, 'c': 1}
    # нет касаний и выездов — ничего не уехало; выезд был — первый рейс (последний выезд)
    assert live.departed_trips(trips, {}, [], None) == {}
    assert live.departed_trips(trips, {}, [t(5), t(30)], t(0), {'a', 'b', 'c'}) == {0: t(30)}
    # касание рейса 1: выезд — последний не позже касания; после касаний — выезд, а точки рейса закрыты → рейс 2
    assert live.departed_trips(trips, {'a': t(20), 'b': t(40)}, [t(10), t(15), t(60)], t(0), {'c'}) \
        == {0: t(15), 1: t(60)}
    # точки рейса 1 не закрыты — рейс 2 не уехал, хоть машина и выезжала со склада
    assert live.departed_trips(trips, {'a': t(20)}, [t(10), t(60)], t(0), {'b', 'c'}) == {0: t(10)}
    # рейсы поменяли местами: касание только у рейса 2 — рейс 1 не уехал (пропущен)
    assert live.departed_trips(trips, {'c': t(20)}, [t(10)], t(0), {'a', 'b'}) == {1: t(10)}
    # касание без выезда до него (трек начался в пути) — от начала трека
    assert live.departed_trips(trips, {'a': t(20)}, [], t(3), {'b', 'c'}) == {0: t(3)}


def test_load_never_negative_and_delivery_not_before_departure():
    t = lambda m: T0 + timedelta(minutes=m)   # noqa: E731
    stops = [stop('a', 1, A, 100.0, 'full', 1.0), stop('b', 2, B, 50.0, 'refused', 0.0)]
    load = live.load_of(stops, {'a': 0, 'b': 0}, {0: t(10)}, {'a': t(5)})   # отметка раньше выезда — с выезда
    assert load.loaded_kg == 150 and load.delivered_kg == 100 and load.remaining_kg == 50   # отказ остаётся на борту
    assert load.at(t(10)) == 0 and load.at(t(11)) == 50
    over = live.load_of([stop('a', 1, A, 100.0, 'full', 1.3)], {'a': 0}, {0: t(10)}, {})
    assert over.remaining_kg == 0 and over.delivered_kg == 100


# ============================== следующий магазин, ETA, опоздание, возвращение ==============================

def test_next_store_eta_delay_and_return():
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(((A[0] + B[0]) / 2, (A[1] + B[1]) / 2))
    now = tr.t
    here = tr.pos
    planned_b = now + timedelta(minutes=5)
    stops = [stop('S:A', 1, A, 600.0, 'full', 1.0, at_a + timedelta(minutes=2), seq=1),
             stop('S:B', 2, B, 400.0, seq=2), stop('S:C', 3, C, 300.0, seq=3)]
    plan = [live.PlanTrip((1, 3, 2), {1: at_a, 2: planned_b, 3: now + timedelta(minutes=30)})]
    card = view(facts(tr.pts, stops, [T0, now]), now, plan)
    # по порядку плана C раньше B: следующий — C
    assert card['next']['stop_id'] == 'S:C'
    plan = [live.PlanTrip((1, 2, 3), {1: at_a, 2: planned_b, 3: now + timedelta(minutes=30)})]
    card = view(facts(tr.pts, stops, [T0, now]), now, plan)
    nxt = card['next']
    leg = ROAD.minutes(here, B)
    eta = now + timedelta(minutes=leg)
    assert nxt['stop_id'] == 'S:B' and nxt['here'] is False
    assert nxt['eta'] == eta.isoformat(timespec='seconds')
    assert nxt['delay_min'] == round((eta - planned_b).total_seconds() / 60)
    ret = (now + timedelta(minutes=leg + 8 + 6 * 0.4 + ROAD.minutes(B, C) + 8 + 6 * 0.3 + ROAD.minutes(C, DEPOT)))
    assert card['return_eta'] == ret.isoformat(timespec='seconds')


def test_road_minutes_city_and_region():
    city = ROAD.minutes(A, B)
    assert city == pytest.approx(haversine_km(A, B) * 1.3 / 25 * 60)
    far = (40.80, 44.49)                                         # Ванадзор — вне радиуса города
    assert ROAD.minutes(A, far) == pytest.approx(haversine_km(A, far) * 1.3 / 45 * 60)


def test_at_store_is_here_and_next_trip_goes_via_depot():
    tr = Track().park(DEPOT, 10).drive(A).park(A, 3)
    now = tr.t
    stops = [stop('S:A', 1, A, 600.0, 'in_progress', seq=1)]
    card = view(facts(tr.pts, stops, [T0, now]), now, [live.PlanTrip((1,), {1: now - timedelta(minutes=4)})])
    assert card['next']['here'] is True and card['next']['eta'] == now.isoformat() and card['next']['delay_min'] == 4
    # на складе, рейс ещё не уехал: ETA = не раньше планового выезда + путь склад → магазин
    tr2 = Track().park(DEPOT, 5)
    now = tr2.t
    dep = now + timedelta(minutes=30)
    card = view(facts(tr2.pts, [stop('S:A', 1, A, 600.0)], [T0, now]), now, [live.PlanTrip((1,), {}, dep)])
    assert card['next']['eta'] == (dep + timedelta(minutes=ROAD.minutes(DEPOT, A))).isoformat(timespec='seconds')
    assert card['next']['planned_eta'] is None and card['next']['delay_min'] is None
    assert card['load']['trips_gone'] == 0 and card['load']['remaining_kg'] == 0


def test_plan_trips_from_draft_and_prediction():
    pred = {'trips': [{'depart': '09:30', 'stops': [[1, '10:05'], [2, '10:40']]},
                      {'depart': '13:00', 'stops': [[3, '13:45'], [1, '14:10']]}]}
    trips = live.plan_trips([[1, 2], [3]], pred, DAY)
    assert trips[0].depart == datetime(2026, 10, 5, 9, 30, tzinfo=Y) and trips[1].customers == (3,)
    assert trips[0].etas == {1: datetime(2026, 10, 5, 10, 5, tzinfo=Y), 2: datetime(2026, 10, 5, 10, 40, tzinfo=Y)}
    assert live.plan_trips([[1, 2, 3]], pred, DAY)[0].depart is None     # рейсы изменили после сборки
    assert live.plan_trips([[1]], None, DAY)[0].etas == {}
    late = live.plan_trips([[5]], {'trips': [{'depart': '23:00', 'stops': [[5, '00:30 (+1)']]}]}, DAY)
    assert late[0].etas[5] == datetime(2026, 10, 6, 0, 30, tzinfo=Y)


# ============================== тревоги ==============================

def _fixes(points):
    return [ac.TrackFix(datetime.fromtimestamp(p[0] / 1000, Y), p[1], p[2], p[3], p[4]) for p in points]


def _speed_track(kmh_list, step_s=15, start=T0):
    return [(ms(start + timedelta(seconds=i * step_s)), A[0] + i * 0.002, A[1], 8.0,
             None if v is None else v / 3.6, 0.0) for i, v in enumerate(kmh_list)]


def test_speed_alert_needs_30_seconds_over_limit():
    assert live.speed_alerts(_fixes(_speed_track([60, 95, 96, 60])), RULES, True) == []           # 15 с — нет
    al = live.speed_alerts(_fixes(_speed_track([60, 95, 101, 97, 60])), RULES, True)               # 30 с — да
    assert len(al) == 1 and al[0]['max_kmh'] == 101 and al[0]['active'] is False
    assert al[0]['from'] == (T0 + timedelta(seconds=15)).isoformat()
    assert live.speed_alerts(_fixes(_speed_track([95, 95, 90])), RULES, True) == []                # 90 — не больше 90
    # точка без скорости и перерыв трека прерывают
    assert live.speed_alerts(_fixes(_speed_track([95, 95, None, 95, 95])), RULES, True) == []
    gap = _speed_track([95, 95]) + _speed_track([95, 95], start=T0 + timedelta(seconds=120))
    assert live.speed_alerts(_fixes(gap), RULES, True) == []
    # идёт сейчас — активна
    now_run = live.speed_alerts(_fixes(_speed_track([60, 95, 95, 95])), RULES, True)
    assert now_run[0]['active'] is True and now_run[0]['to'] is not None
    assert live.speed_alerts(_fixes(_speed_track([95, 95, 95])), live.Rules(speed_sec=45), True) == []


def _stay_day(minutes, at_hhmm=(11, 0)):
    """Склад → стоянка не по плану minutes мин в точке X (не магазин, не склад; около at_hhmm) → магазин A."""
    tr = Track(datetime(2026, 10, 5, *at_hhmm, tzinfo=Y) - timedelta(minutes=10)).park(DEPOT, 5)
    tr.drive((40.1650, 44.4600)).park((40.1650, 44.4600), minutes).drive(A).park(A, 3)
    return tr, tr.pts[0]


@pytest.mark.parametrize('minutes, alert', [(10, False), (16, True)])
def test_stop_alert_over_threshold_outside_store_and_depot(minutes, alert):
    tr, _ = _stay_day(minutes)
    stops = [stop('S:A', 1, A, 100.0)]
    card = view(facts(tr.pts, stops, [tr.t]), tr.t, detail=True)
    found = [a for a in card['alerts_log'] if a['kind'] == 'stop']
    assert bool(found) is alert
    if alert:
        assert found[0]['minutes'] == minutes and found[0]['lunch'] is False and found[0]['active'] is False


@pytest.mark.parametrize('minutes, alert', [(40, False), (44, False), (46, True)])
def test_lunch_in_window_does_not_count(minutes, alert):
    """Обед 30 мин (настройка №61) в окне 12:30–14:30: тревога — только если стоянка длиннее 30 + 15 мин."""
    tr, _ = _stay_day(minutes, (13, 0))
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], [tr.t]), tr.t, detail=True)
    found = [a for a in card['alerts_log'] if a['kind'] == 'stop']
    assert bool(found) is alert
    if alert:
        assert found[0]['lunch'] is True


def test_stop_at_store_or_depot_is_not_an_alert():
    tr = Track().park(DEPOT, 40).drive(A).park(A, 40).drive(DEPOT)
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], [T0, tr.t]), tr.t, detail=True)
    assert [a for a in card['alerts_log'] if a['kind'] == 'stop'] == []


def test_ongoing_stop_is_active():
    tr = Track().park(DEPOT, 5).drive((40.1650, 44.4600)).park((40.1650, 44.4600), 20)
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], [T0, tr.t]), tr.t + timedelta(seconds=30), detail=True)
    found = [a for a in card['alerts_log'] if a['kind'] == 'stop']
    assert found and found[0]['active'] is True and found[0]['to'] is None
    assert 'stop' in card['alerts']['active'] and card['state'] == 'alert'


def test_no_contact_current_and_journal():
    tr = Track().park(DEPOT, 5).drive(A)
    contacts = [T0, T0 + timedelta(minutes=3), T0 + timedelta(minutes=11), T0 + timedelta(minutes=12)]
    now = T0 + timedelta(minutes=18)
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], contacts), now, detail=True)
    nc = [a for a in card['alerts_log'] if a['kind'] == 'no_contact']
    assert [(a['minutes'], a['active']) for a in nc] == [(8, False), (6, True)]
    assert card['state'] == 'offline' and card['contact_age_s'] == 360
    # 5 мин ровно — ещё на связи
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], contacts), T0 + timedelta(minutes=17), detail=True)
    assert 'no_contact' not in card['alerts']['active']
    # день закрыт (day_closed) — «нет связи» после закрытия не тревога
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], contacts, closed_at=T0 + timedelta(minutes=12)), now)
    assert card['alerts']['active'] == [] and card['state'] == 'closed'
    # прошлый день — тревог «сейчас» нет
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], contacts), now + timedelta(days=1))
    assert 'no_contact' not in card['alerts']['active'] and card['next'] is None


def test_gps_off_alert_from_device_and_old_apk_without_device():
    tr = Track().park(DEPOT, 5)
    d = lambda m: T0 + timedelta(minutes=m)   # noqa: E731
    devices = [(d(1), 'on'), (d(2), 'off'), (d(3), 'off'), (d(6), 'on'), (d(8), 'no_permission')]
    device = {'battery': 41, 'charging': False, 'gps': 'no_permission', 'net': 'cell', 'app': '2.2.0',
              'at': d(8).isoformat()}
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], [T0, d(8)], device, devices), d(9), detail=True)
    gps = [a for a in card['alerts_log'] if a['kind'] == 'gps']
    assert [(a['from'], a['to'], a['gps'], a['active']) for a in gps] == [
        (d(2).isoformat(), d(6).isoformat(), 'off', False), (d(8).isoformat(), None, 'no_permission', True)]
    assert card['device']['battery'] == 41 and card['state'] == 'alert'
    old = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], [T0, d(8)]), d(9), detail=True)   # APK без device
    assert old['device'] is None and not [a for a in old['alerts_log'] if a['kind'] == 'gps']


def test_center_zone_alert_only_for_trucks_without_access():
    zone = ((40.175, 44.505), (40.175, 44.525), (40.190, 44.525), (40.190, 44.505))
    rules = live.Rules(center_zone=zone)
    inside = (40.182, 44.515)
    tr = Track().park(DEPOT, 5).drive(inside, step_s=60).park(inside, 2).drive(A)
    banned = live.TruckSpec(2000.0, 25.0, center_ok=False)
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], [T0, tr.t]), tr.t, truck=banned, rules=rules, detail=True)
    found = [a for a in card['alerts_log'] if a['kind'] == 'center']
    assert len(found) == 1 and found[0]['active'] is False
    for ok in (True, None):   # можно в центр или неизвестно (ERP не в памяти) — тревоги нет
        card = view(facts(tr.pts, [], [T0, tr.t]), tr.t, truck=live.TruckSpec(2000.0, 25.0, center_ok=ok),
                    rules=rules, detail=True)
        assert not [a for a in card['alerts_log'] if a['kind'] == 'center']
    one = [(ms(T0), *inside, 8.0, 5.0, 0.0), (ms(T0 + timedelta(seconds=15)), *A, 8.0, 5.0, 0.0)]
    assert live.center_alerts(ac.clean_track(_fixes(one)), rules, banned, True) == []   # одна точка — не тревога


# ============================== пустые данные ==============================

def test_empty_facts_and_unknown_truck():
    card = view({}, T0)
    assert card['state'] == 'nodata' and card['position'] is None and card['km'] == 0.0 and card['fuel_l'] == 0.0
    assert card['next'] is None and card['alerts'] == {'active': [], 'count': 0} and card['device'] is None
    assert view({}, T0, truck=live.TruckSpec())['fuel_l'] is None
    card = view(facts([], [stop('S:A', 1, A, 100.0)], [T0]), T0 + timedelta(minutes=2), depot=None, detail=True)
    assert card['state'] == 'standing' and card['stores'] == {'done': 0, 'total': 1, 'in_progress': 0}
    assert card['track'] == [] and card['stops'][0]['status'] == 'pending'
    # только сигнал «на связи» без GPS (points: []) — позиции нет, связь есть
    card = view(facts([], [], [T0, T0 + timedelta(minutes=1)]), T0 + timedelta(minutes=10))
    assert card['position'] is None and card['state'] == 'offline'


def test_card_position_heading_age_and_moving_state():
    tr = Track().park(DEPOT, 3).drive(A)
    now = tr.t + timedelta(seconds=20)
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], [T0, tr.t]), now)
    p = card['position']
    assert (p['lat'], p['lon']) == (round(A[0], 6), round(A[1], 6)) and p['speed_kmh'] == 36 and p['heading'] == 90
    assert p['age_s'] == 20 and card['state'] == 'moving'
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], [T0, tr.t]), tr.t + timedelta(minutes=3))
    assert card['state'] == 'standing'          # точка старше 2 мин — не «едет»


# ============================== факт терминала: courier.live.LiveSource ==============================

LIVE_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=Y)
SID1 = 'S:11111111-1111-4111-8111-111111111111'
SID2 = 'S:22222222-2222-4222-8222-222222222222'
SID3 = 'S:33333333-3333-4333-8333-333333333333'


def _day_stop(sid, cid, p, lines, seq):
    return {'stop_id': sid, 'seq': seq, 'collect': 'none', 'doc_number': str(seq), 'lat': p[0], 'lon': p[1],
            'customer': {'id': cid, 'name': f'Խանութ {cid}'}, 'amount_due': 0,
            'weight_kg': sum(q * w for _, q, w in lines if w),
            'lines': [{'line_id': lid, 'qty': q, 'price': 100, 'product_id': 1, 'marked': False,
                       'weight_kg': q * w if w is not None else None} for lid, q, w in lines]}


@pytest.fixture
def courier_app(tmp_path, monkeypatch):
    import courier
    from courier import clock
    from flask import Flask

    class Db:
        connection_string = 'DRIVER={none};'
    monkeypatch.setattr(clock, 'now', lambda: LIVE_NOW)
    monkeypatch.delenv('COURIER_DEMO', raising=False)
    app = Flask(__name__)
    courier.init_app(app, Db(), db_path=str(tmp_path / 'courier.db'))
    return app


def _who(store, car, name, pin):
    from courier import events as ev
    did = store.save_driver(None, name, True, pin, 'admin')
    terminal, _ = store.create_terminal('U-' + car, car, 'admin')
    return ev.Who(terminal.id, car, did, name)


def _ev(etype, stop_id, payload, at):
    import uuid
    return {'id': str(uuid.uuid4()), 'type': etype, 'stop_id': stop_id, 'date': DAY.isoformat(),
            'at': at.isoformat(), 'payload': payload}


def test_live_source_fleet(courier_app):
    from courier import events as ev
    from courier.live import LiveSource
    store = courier_app.extensions['courier'].store
    ds = DAY.isoformat()
    # вес строки = кол-во × вес единицы; l4 — товара нет в ERP (null), l5 — вес 0: «строки без веса»
    store.save_day(ds, 'CAR1', [_day_stop(SID1, 1, A, [('l1', 10, 12.0), ('l2', 5, 6.0)], 1),
                                _day_stop(SID2, 2, B, [('l3', 4, 20.0), ('l4', 2, None), ('l5', 1, 0)], 2)],
                   'v1', LIVE_NOW.isoformat())
    store.save_day(ds, 'CAR2', [_day_stop(SID3, 3, C, [('l6', 1, 10.0)], 1)], 'v1', LIVE_NOW.isoformat())
    who = _who(store, 'CAR1', 'Արամ', '1111')
    t = lambda m: LIVE_NOW - timedelta(minutes=m)   # noqa: E731
    pts = [{'at': t(30 - i).isoformat(), 'lat': A[0], 'lon': A[1] + i * 1e-4, 'acc': 8.0, 'spd': 0.0, 'brg': None}
           for i in range(3)]
    device = {'battery': 80, 'charging': False, 'gps': 'on', 'net': 'wifi', 'app': '2.2.0'}
    events = [_ev('track', None, {'points': pts, 'device': device}, t(27)),
              _ev('delivery', SID1, {'lines': [{'line_id': 'l1', 'qty': 10}, {'line_id': 'l2', 'qty': 2.5}]}, t(26)),
              _ev('track', None, {'points': [], 'device': {**device, 'gps': 'off', 'battery': 79}}, t(20))]
    r = ev.ingest(store, who, events).json()
    assert len(r['accepted']) == 3, r
    with courier_app.app_context():
        fleet = LiveSource(store).fleet(ds)
    car1, car2 = fleet['CAR1'], fleet['CAR2']
    assert [(s['stop_id'], s['status'], s['customer_id'], s['weight_kg'], s['unweighed']) for s in car1['stops']] == [
        (SID1, 'partial', 1, 150.0, 0), (SID2, 'pending', 2, 80.0, 2)]
    assert car1['stops'][0]['share'] == pytest.approx((120 + 30 * 0.5) / 150)   # по весу строк (№65)
    assert car1['stops'][0]['delivered_at'] == t(26).isoformat() and car1['stops'][1]['share'] is None
    assert len(car1['track']) == 3 and car1['drivers'] == ['Արամ'] and car1['closed_at'] is None
    assert car1['device'] == {**device, 'gps': 'off', 'battery': 79, 'at': t(20).isoformat()}
    assert car1['devices'] == [(t(27).isoformat(), 'on'), (t(20).isoformat(), 'off')]
    assert len(car1['contacts']) == 3 and car1['last_contact'] == LIVE_NOW.isoformat()
    assert car2['track'] == [] and car2['device'] is None and car2['contacts'] == [] and car2['last_contact'] is None
    # старый APK (без device) и закрытие дня
    r = ev.ingest(store, who, [_ev('track', None, {'points': [{**pts[0], 'at': t(10).isoformat()}]}, t(10)),
                               _ev('day_closed', None, {'summary': {}}, t(5))]).json()
    assert len(r['accepted']) == 2, r
    with courier_app.app_context():
        car1 = LiveSource(store).fleet(ds)['CAR1']
    assert car1['device']['at'] == t(20).isoformat() and car1['closed_at'] == t(5).isoformat()


def test_live_source_no_db(tmp_path):
    from courier.live import LiveSource
    from courier.store import Store
    path = tmp_path / 'none.db'
    store = Store.__new__(Store)
    store.path = str(path)
    assert LiveSource(store).fleet('2026-10-05') == {} and not path.exists()


def test_live_source_share_via_absorbed_order_and_status():
    from courier.live import _share
    order = {'stop_id': 'O:1', 'weight_kg': 300.0, 'lines': [{'line_id': 'o1', 'qty': 10, 'weight_kg': 300.0}]}
    invoice = {'stop_id': 'S:1', 'weight_kg': 200.0, 'lines': [{'line_id': 's1', 'qty': 4, 'weight_kg': 200.0}]}
    d = {'O:1': {'id': 'e1', 'type': 'delivery', 'stop_id': 'O:1', 'at': LIVE_NOW,
                 'payload': {'lines': [{'line_id': 'o1', 'qty': 5}]}}}
    share, when = _share('S:1', invoice, 'partial', d, {'S:1': ['O:1']}, {'O:1': order})
    assert share == pytest.approx(150 / 200) and when == LIVE_NOW    # 150 кг заказа / 200 кг накладной
    assert _share('S:1', invoice, 'covered', {}, {}, {}) == (1.0, None)
    assert _share('S:1', invoice, 'refused', {}, {}, {}) == (0.0, None)
    assert _share('S:1', invoice, 'pending', {}, {}, {}) == (None, None)


# ============================== API и доступ ==============================

from test_garage_public import LAN, PUBLIC, _session_as, app_v2, client  # noqa: E402,F401

API_NOW = datetime(2026, 10, 3, 11, 0, tzinfo=Y)


class FakeLive:
    def __init__(self):
        t = lambda m: API_NOW - timedelta(minutes=m)   # noqa: E731
        tr = Track(t(40)).park(DEPOT, 5).drive(A)
        tr.park(A, int((API_NOW - tr.t).total_seconds() // 60))
        device = {'battery': 64, 'charging': True, 'gps': 'on', 'net': 'cell', 'app': '2.2.0', 'at': t(1).isoformat()}
        self.data = {'2026-10-03': {'CAR1': facts(tr.pts, [stop('S:A', 7, A, 500.0), stop('S:B', 8, B, 200.0, seq=2)],
                                                  [t(40), t(1)], device, [(t(1), 'on')])}}

    def fleet(self, day):
        return self.data.get(day, {})


@pytest.fixture
def live_app(app_v2, monkeypatch):
    from route_optimizer import dispatch as dp
    from route_optimizer import views
    state = app_v2.app.extensions['route_optimizer']
    monkeypatch.setattr(state, 'live_facts', FakeLive())
    monkeypatch.setattr(views, '_yerevan_now', lambda: API_NOW)
    draft = dp.Draft(trucks=['CAR1', 'CAR9'], trips=[dp.DraftTrip(1, 'CAR1', [7, 8]), dp.DraftTrip(2, 'CAR9', [5])])
    draft.prediction = {'trucks': {'CAR1': {'trips': [{'depart': '10:25', 'stops': [[7, '10:40'], [8, '11:20']]}]}}}
    state.store.save_dispatch('2026-10-03', draft.to_json(), 'qa')
    state.store.save_truck_driver('CAR1', '2026-10-03', 'Արամ', 'qa')
    return app_v2


def test_api_live_fleet_and_truck_for_admin(client, live_app):
    _session_as(client, 'boss', base=LAN)
    r = client.get('/api/routes/live', base_url=LAN)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['date'] == '2026-10-03' and body['thresholds']['speed_kmh'] == 90
    cars = {t['car_code']: t for t in body['trucks']}
    assert set(cars) == {'CAR1', 'CAR9'}                              # с данными терминала и машина плана без них
    assert cars['CAR9']['state'] == 'nodata' and cars['CAR9']['planned'] is True
    car1 = cars['CAR1']
    assert car1['driver'] == 'Արամ' and car1['stores'] == {'done': 0, 'total': 2, 'in_progress': 0}
    assert car1['next']['stop_id'] == 'S:A' and car1['next']['planned_eta'] == '2026-10-03T10:40:00+04:00'
    assert car1['next']['here'] is True and car1['next']['delay_min'] == 20
    assert car1['device']['battery'] == 64 and 'track' not in car1
    assert car1['fuel_l'] is not None
    r = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN)
    truck = r.get_json()['truck']
    assert r.status_code == 200 and truck['track'] and [s['stop_id'] for s in truck['stops']] == ['S:A', 'S:B']
    assert truck['stops'][0]['planned_eta'] == '2026-10-03T10:40:00+04:00'
    assert client.get('/api/routes/live?date=2026-10-02', base_url=LAN).get_json()['trucks'] == []
    assert client.get('/api/routes/live?date=03.10.2026', base_url=LAN).status_code == 400
    assert client.get('/api/routes/live/truck', base_url=LAN).status_code == 400
    assert client.get('/api/routes/live/truck?car=' + 'X' * 21, base_url=LAN).status_code == 400
    assert client.get('/api/routes/live/truck?car=NOPE', base_url=LAN).status_code == 404
    page = client.get('/routes/live', base_url=LAN)
    assert page.status_code == 200 and 'js/routes_live.js' in page.get_data(as_text=True)


def test_api_live_without_courier_section(client, live_app, monkeypatch):
    monkeypatch.setattr(live_app.app.extensions['route_optimizer'], 'live_facts', None)
    _session_as(client, 'boss', base=LAN)
    assert client.get('/api/routes/live', base_url=LAN).status_code == 400


def test_live_access_garage_user_anonymous(client, live_app):
    # без входа: API — 401, страница — на вход
    assert client.get('/api/routes/live', base_url=LAN).status_code == 401
    r = client.get('/routes/live', base_url=LAN)
    assert r.status_code == 302 and '/login' in r.headers['Location']
    # пользователь по территориям — 403
    _session_as(client, 'u', base=LAN)
    assert client.get('/api/routes/live', base_url=LAN).status_code == 403
    # «Гараж» в офисе: страница и API (только чтение), остальное «Маршрутов» — нет
    h = _session_as(client, 'garage1', base=LAN)
    assert client.get('/routes/live', base_url=LAN).status_code == 200
    assert client.get('/api/routes/live', base_url=LAN).status_code == 200
    assert client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).status_code == 200
    assert client.post('/api/routes/live', json={}, base_url=LAN, headers=h).status_code == 403
    for path in ('/api/routes/live-x', '/api/routes/dispatch', '/api/routes/settings'):
        assert client.get(path, base_url=LAN).status_code == 403, path
    assert client.get('/routes/livex', base_url=LAN).status_code == 302


def test_live_from_internet_only_garage(client, live_app):
    assert live_app._public_path_allowed('/routes/live', 'GET') is True
    assert live_app._public_path_allowed('/api/routes/live/truck', 'GET') is True
    for path in ('/routes/live/', '/routes/live-x', '/api/routes/live-x', '/api/routes/live/../garage',
                 '/api/routes/Live'):
        assert live_app._public_path_allowed(path, 'GET') is False, path
    assert live_app._public_path_allowed('/routes/live', 'POST') is False
    assert live_app._garage_path_allowed('/api/routes/live/truck', 'POST') is False
    _session_as(client, 'garage1')
    page = client.get('/routes/live', base_url=PUBLIC)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for path in ('/static/css/routes_live.css', '/static/js/routes_live.js', '/static/js/routes_basemap.js'):
        assert path in html and path in live_app._PUBLIC_STATIC
        with client.get(path, base_url=PUBLIC) as r:
            assert r.status_code == 200, path
    import re
    for m in re.findall(r'<(?:script|link)[^>]+(?:src|href)="(/static/[^"?]+)', html):
        assert m in live_app._PUBLIC_STATIC, m                         # вся своя статика страницы открыта снаружи
    assert 'data-yandex-key' not in html                               # подложка без ключа Яндекса (условия Tiles API)
    assert client.get('/api/routes/live', base_url=PUBLIC).status_code == 200
    for who in ('boss', 'u'):
        _session_as(client, who)
        assert client.get('/routes/live', base_url=PUBLIC).status_code == 404
        assert client.get('/api/routes/live', base_url=PUBLIC).status_code == 404


def test_live_thresholds_in_settings():
    from route_optimizer import store as st
    vals = dict(st.DEFAULT_SETTINGS)
    out, errors = st.validate_settings(vals, None)
    assert not errors and (out['live_speed_kmh'], out['live_speed_sec'], out['live_stop_min'],
                           out['live_no_contact_min']) == (90, 30, 15, 5)
    for key, bad in (('live_speed_kmh', 20), ('live_speed_sec', 0), ('live_stop_min', 500),
                     ('live_no_contact_min', None), ('live_speed_kmh', True)):
        _, errors = st.validate_settings({**vals, key: bad}, None)
        assert key in errors, key
    rules = live.Rules.from_settings({**vals, 'live_speed_kmh': 70, 'live_no_contact_min': 10,
                                      'truck_lunch_from': '13:00', 'truck_lunch_min': 45})
    assert (rules.speed_kmh, rules.no_contact_min, rules.lunch_window[0], rules.lunch_min) == (70, 10, 780, 45)

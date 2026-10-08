# -*- coding: utf-8 -*-
"""«Մեքենաները առցանց» (ответ владельца №76): расчёты карточки машины (route_optimizer.live) — км и топливо по загрузке,
остаток груза при нескольких рейсах, следующий магазин и ETA, все тревоги, пустые данные и старый APK без `device`;
факт терминала (courier.live.LiveSource); API, доступ (администратор, «Гараж», в т.ч. из интернета; 401/403/404) и пороги
в настройках.

Синтетические данные, без ERP; базы — временные. Запуск из корня проекта:  python -m pytest tests/test_route_live.py -q
"""
import math
import re
import sqlite3
import sys
import threading
import time
from contextlib import closing
from dataclasses import replace
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
    # недовезённые 200 кг B сданы на склад (вход в его зону после последнего магазина рейса) — в машине пусто
    assert card['load'] == {'remaining_kg': 0, 'loaded_kg': 1000, 'delivered_kg': 800, 'unweighed_lines': 0,
                            'trip_kg': 1000, 'refused_kg': 0, 'returns_kg': 0, 'returns_unweighed': 0,
                            'unloaded_kg': 200, 'trips_gone': 1, 'trips': 1, 'planned_kg': None}
    assert card['stores'] == {'done': 2, 'total': 2, 'in_progress': 0, 'gps_visited': 2, 'unmarked': 0}
    assert card['closed'] is True and card['state'] == 'closed' and card['next'] is None


# ============================== этап 2: недовезённое, возвраты, перезагрузка, сверка топлива ==============================

def _refused_day(back):
    """Склад → A (600 кг, отказ) → B (400 кг, всё доставлено) [→ склад, если back]. Возвращает (трек, точки, план)."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(B)
    at_b = tr.t
    tr.park(B, 8)
    if back:
        tr.drive(DEPOT).park(DEPOT, 3)
    stops = [stop('S:A', 1, A, 600.0, 'refused', 0.0, at_a + timedelta(minutes=2), seq=1),
             stop('S:B', 2, B, 400.0, 'full', 1.0, at_b + timedelta(minutes=2), seq=2)]
    return tr, stops, [live.PlanTrip((1, 2), {}, T0 + timedelta(minutes=10))]


def test_refused_weight_stays_on_board_until_depot_entry():
    tr, stops, plan = _refused_day(back=False)
    now = tr.t
    ld = view(facts(tr.pts, stops, [T0, now]), now, plan)['load']
    # все точки закрыты, но склада не было — отказ A (600 кг) всё ещё в машине
    assert ld['remaining_kg'] == 600 and ld['refused_kg'] == 600 and ld['unloaded_kg'] == 0 and ld['trip_kg'] == 1000
    tr, stops, plan = _refused_day(back=True)
    ld = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)['load']
    assert ld['remaining_kg'] == 0 and ld['refused_kg'] == 0 and ld['unloaded_kg'] == 600   # вошла в зону склада — выгружено


def test_refused_weight_unloads_only_after_last_store_of_trip():
    """Заехала на склад, не закрыв рейс (B ещё впереди), — недовезённое A остаётся: склад «после последнего магазина»."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(DEPOT).park(DEPOT, 5).drive(B)
    stops = [stop('S:A', 1, A, 600.0, 'refused', 0.0, at_a + timedelta(minutes=2), seq=1), stop('S:B', 2, B, 400.0, seq=2)]
    plan = [live.PlanTrip((1, 2), {})]
    ld = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)['load']
    assert ld['remaining_kg'] == 1000 and ld['refused_kg'] == 600 and ld['unloaded_kg'] == 0


def test_partial_delivery_remainder_unloads_at_depot():
    tr, stops, plan = _refused_day(back=True)
    stops[0] = stop('S:A', 1, A, 600.0, 'partial', 0.25, T0 + timedelta(minutes=12), seq=1)
    ld = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)['load']
    assert ld['delivered_kg'] == 550 and ld['unloaded_kg'] == 450 and ld['remaining_kg'] == 0   # 150 + 400 доставлено


def test_returns_add_weight_until_depot():
    tr, stops, plan = _refused_day(back=False)
    ret = [{'at': (tr.t - timedelta(minutes=3)).isoformat(), 'kg': 45.5, 'stop_id': 'S:B'},
           {'at': (tr.t - timedelta(minutes=2)).isoformat(), 'kg': None, 'stop_id': 'S:B'}]   # вес неизвестен
    f = facts(tr.pts, stops, [T0, tr.t])
    f['returns'] = ret
    ld = view(f, tr.t, plan)['load']
    assert ld['remaining_kg'] == 646 and ld['returns_kg'] == 46 and ld['returns_unweighed'] == 1   # 600 отказ + 45,5
    tr.drive(DEPOT).park(DEPOT, 2)                                  # на склад — возврат и отказ сданы
    f = facts(tr.pts, stops, [T0, tr.t])
    f['returns'] = ret
    ld = view(f, tr.t, plan)['load']
    assert ld['remaining_kg'] == 0 and ld['returns_kg'] == 0 and ld['unloaded_kg'] == 646


def test_return_weight_changes_fuel_after_it():
    """Возврат тяжелит машину на участках после него: топливо больше, чем без возврата."""
    tr, stops, plan = _refused_day(back=True)
    plain = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)['fuel_l']
    f = facts(tr.pts, stops, [T0, tr.t])
    f['returns'] = [{'at': (T0 + timedelta(minutes=40)).isoformat(), 'kg': 500.0, 'stop_id': 'S:B'}]
    # B дошёл до склада позже 09:40? момент возврата — за 1 мин до выезда на склад
    f['returns'][0]['at'] = (tr.t - timedelta(minutes=14)).isoformat()
    assert view(f, tr.t, plan)['fuel_l'] > plain


def test_reload_between_trips_trip_kg_is_current_trip():
    tr, stops, plan = _two_trips()
    tr.park(DEPOT, 15).drive(((DEPOT[0] + C[0]) / 2, (DEPOT[1] + C[1]) / 2))
    ld = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)['load']
    assert ld['trip_kg'] == 300 and ld['loaded_kg'] == 1300 and ld['remaining_kg'] == 300   # остаток рейса 1 сдан, загружен рейс 2


def _refuel(rid, at, liters, odo, full=True, superseded=False):
    return {'id': rid, 'car_code': 'CAR1', 'date': at.date().isoformat(), 'at_utc': at.astimezone(Y).isoformat(),
            'payload': {'liters': liters, 'odometer_km': odo, 'full_tank': full}, 'flags': [], 'superseded': superseded,
            'eff_at_utc': at.astimezone(Y).isoformat()}


def test_refuel_check_interval_and_since_refuel():
    tr, stops, plan = _refused_day(back=True)
    f = facts(tr.pts, stops, [T0, tr.t])
    # два полных бака: 400 км одометра, залито 110 л (27,5 л/100); норма машины — середина 20…30 = 25 → расчёт 100 л
    f['refuels'] = [_refuel('r1', datetime(2026, 10, 1, 8, 0, tzinfo=Y), 40.0, 10000),
                    _refuel('r2', datetime(2026, 10, 3, 8, 0, tzinfo=Y), 110.0, 10400)]
    chk = view(f, tr.t, plan)['fuel_check']
    assert chk['last'] == {'at': '2026-10-03T08:00:00+04:00', 'liters': 110.0, 'full': True, 'today': False}
    assert chk['interval']['km'] == 400 and chk['interval']['liters'] == 110.0
    assert chk['interval']['calc_l'] == 100.0 and chk['interval']['delta_pct'] == 10.0 and chk['since_l'] is None
    # заправка сегодня в 09:30 — расчёт с её момента: только участки после
    f['refuels'].append(_refuel('r3', T0 + timedelta(minutes=30), 30.0, 10500, full=False))
    card = view(f, tr.t, plan)
    chk = card['fuel_check']
    assert chk['last']['today'] is True and chk['last']['full'] is False and 0 < chk['since_l'] < card['fuel_l']
    # исправленная заправка не участвует; заправок нет — None, расчёт как раньше
    f['refuels'] = [_refuel('r3', T0, 30.0, 10500, superseded=True)]
    assert view(f, tr.t, plan)['fuel_check'] is None
    assert view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)['fuel_check'] is None
    # нормы машины нет — интервал есть, расчёта нет
    f['refuels'] = [_refuel('r1', datetime(2026, 10, 1, 8, 0, tzinfo=Y), 40.0, 10000),
                    _refuel('r2', datetime(2026, 10, 3, 8, 0, tzinfo=Y), 110.0, 10400)]
    iv = view(f, tr.t, plan, truck=live.TruckSpec(2000.0))['fuel_check']['interval']
    assert iv['calc_l'] is None and iv['delta_pct'] is None and iv['liters'] == 110.0


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
    assert card['load']['trips_gone'] == 1 and card['load']['remaining_kg'] == 0   # остаток рейса 1 сдан на склад
    assert card['load']['unweighed_lines'] == 0                     # строки без веса — у точки рейса 2, не на борту
    assert card['state'] != 'closed' and card['next']['stop_id'] == 'S:C'
    tr.drive(((DEPOT[0] + C[0]) / 2, (DEPOT[1] + C[1]) / 2))       # выехал с рейсом 2, на полпути к C
    now = tr.t
    card = view(facts(tr.pts, stops, [T0, now]), now, plan)
    assert {k: card['load'][k] for k in ('remaining_kg', 'loaded_kg', 'delivered_kg', 'unweighed_lines', 'trip_kg',
                                         'trips_gone')}         == {'remaining_kg': 300, 'loaded_kg': 1300, 'delivered_kg': 800, 'unweighed_lines': 2, 'trip_kg': 300,
            'trips_gone': 2}   # на борту — только рейс 2: «загружено в этом рейсе» 300


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


# ============================== этап 2: точное ETA (дороги, время у магазина, обед, склад) ==============================

def _road(legs=None, unload=None):
    return live.Road(1.3, 25.0, 45.0, (40.1792, 44.4991), 12.0, legs=legs, unload=unload)


def _view_eta(tr, stops, road, rules=RULES, plan=None):
    plan = plan if plan is not None else [live.PlanTrip(tuple(s['customer_id'] for s in stops), {})]
    return live.car_view(DAY, tr.t, facts(tr.pts, stops, [T0, tr.t]), plan, TRUCK, DEPOT, rules, road, True)


def _mid(a, b):
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


def test_eta_chain_uses_road_legs_and_store_times():
    """Остаток разгрузки у A + участок + время у B + участок + время у C + участок до склада — по дорожной модели и
    времени магазинов (№50/№60)."""
    tr = Track().park(DEPOT, 5).drive(A).park(A, 3)
    stops = [stop('S:A', 1, A, 500.0, 'in_progress', seq=1), stop('S:B', 2, B, 300.0, seq=2),
             stop('S:C', 3, C, 200.0, seq=3)]
    road = _road(lambda a, b, minute, here: 7.0, unload=lambda p, kg: {A: 12.0, B: 20.0, C: 9.0}[p])
    card = _view_eta(tr, stops, road)
    st = {x['stop_id']: x for x in card['stops']}
    stayed = (tr.t - datetime.fromisoformat(st['S:A']['arrive'])).total_seconds() / 60
    assert card['next']['here'] is True and card['next']['stop_id'] == 'S:A' and card['next']['eta_source'] is None
    t_b = tr.t + timedelta(minutes=12 - stayed + 7)
    t_c = t_b + timedelta(minutes=20 + 7)
    back = t_c + timedelta(minutes=9 + 7)
    for sid, want in (('S:B', t_b), ('S:C', t_c)):
        assert abs((datetime.fromisoformat(st[sid]['eta']) - want).total_seconds()) < 1.5
        assert st[sid]['eta_source'] == 'road'
    assert abs((datetime.fromisoformat(card['return_eta']) - back).total_seconds()) < 1.5
    assert card['return_source'] == 'road'


def test_eta_first_leg_from_position_is_asked_with_here_flag():
    tr = Track().park(DEPOT, 5).drive(_mid(DEPOT, A))
    stops = [stop('S:A', 1, A, 500.0, seq=1), stop('S:B', 2, B, 300.0, seq=2)]
    seen = []

    def legs(a, b, minute, here):
        seen.append(here)
        return 5.0
    card = _view_eta(tr, stops, _road(legs))
    assert card['next']['stop_id'] == 'S:A' and card['next']['eta_source'] == 'road'
    assert seen[0] is True and not any(seen[1:])   # только участок от положения машины — «живой»
    # движок не знает положения машины (None) — ETA следующего «запасное», остальные участки — дороги, но источник
    # накопительный: прибытие после запасного участка тоже «model»
    card = _view_eta(tr, stops, _road(lambda a, b, minute, here: None if here else 5.0))
    assert card['next']['eta_source'] == 'model'
    assert [x['eta_source'] for x in card['stops']] == ['model', 'model']


def test_eta_fallback_when_engine_unavailable():
    """Дорог нет (нет карты, Valhalla не готов) или пары нет — запасная модель: прямая × извилистость, скорость зоны."""
    tr = Track().park(DEPOT, 5).drive(A)
    stops = [stop('S:A', 1, A, 400.0, 'full', 1.0, tr.t, seq=1), stop('S:B', 2, B, 300.0, seq=2)]
    card = _view_eta(tr, stops, ROAD)
    assert card['next']['stop_id'] == 'S:B' and card['next']['eta_source'] == 'model'
    got = (datetime.fromisoformat(card['next']['eta']) - tr.t).total_seconds() / 60
    assert got == pytest.approx(ROAD.minutes(tr.pos, B), abs=0.1)
    assert _view_eta(tr, stops, _road(lambda *a: None))['next']['eta'] == card['next']['eta']
    assert card['return_source'] == 'model'


def test_eta_store_time_defaults_to_norms_without_road_model():
    tr = Track().park(DEPOT, 5).drive(A)
    stops = [stop('S:A', 1, A, 400.0, 'full', 1.0, tr.t, seq=1), stop('S:B', 2, B, 1000.0, seq=2),
             stop('S:C', 3, C, 100.0, seq=3)]
    st = {x['stop_id']: x for x in _view_eta(tr, stops, ROAD)['stops']}
    gap = (datetime.fromisoformat(st['S:C']['eta']) - datetime.fromisoformat(st['S:B']['eta'])).total_seconds() / 60
    assert gap == pytest.approx(8.0 + 6.0 * 1.0 + ROAD.minutes(B, C), abs=0.1)   # 8 мин на точку + 6 на тонну


def _lunch_view(start, lunch_min, window=(750.0, 870.0), legs_min=5.0, park_min=0):
    road = _road(lambda a, b, minute, here: legs_min, unload=lambda p, kg: 10.0)
    tr = Track(start).park(DEPOT, 2)
    if park_min:
        tr.drive((40.1600, 44.4600)).park((40.1600, 44.4600), park_min)   # стоянка не по плану (обед) до выезда к A
    tr.drive(_mid(DEPOT, A))
    stops = [stop('S:A', 1, A, 100.0, seq=1), stop('S:B', 2, B, 100.0, seq=2)]
    rules = live.Rules(lunch_min=lunch_min, lunch_window=window)
    return {x['stop_id']: datetime.fromisoformat(x['eta']) for x in _view_eta(tr, stops, road, rules)['stops']}


def test_eta_lunch_after_unload_in_window_shifts_following_stops():
    start = datetime(2026, 10, 5, 12, 40, tzinfo=Y)
    plain, lunch = _lunch_view(start, 0.0), _lunch_view(start, 30.0)
    assert lunch['S:A'] == plain['S:A']                                  # до A обеда ещё нет
    assert lunch['S:B'] - plain['S:B'] == timedelta(minutes=30)          # после разгрузки у A (окно уже открыто)
    early = datetime(2026, 10, 5, 9, 0, tzinfo=Y)                        # утром окно ещё не началось — обеда в пути нет
    assert _lunch_view(early, 30.0) == _lunch_view(early, 0.0)


def test_eta_lunch_not_repeated_when_already_taken():
    start = datetime(2026, 10, 5, 12, 20, tzinfo=Y)
    assert _lunch_view(start, 30.0, park_min=31) == _lunch_view(start, 0.0, park_min=31)   # стоял 31 мин в окне — обед был


def test_eta_lunch_on_the_road_when_window_ends_before_arrival():
    start = datetime(2026, 10, 5, 12, 40, tzinfo=Y)
    window = (750.0, 780.0)   # 12:30–13:00; перегон 50 мин кончается в 13:30 — обед в дороге
    plain, lunch = _lunch_view(start, 0.0, window, 50.0), _lunch_view(start, 30.0, window, 50.0)
    assert lunch['S:A'] - plain['S:A'] == timedelta(minutes=30)


def test_eta_next_trip_goes_via_depot_load_and_planned_departure():
    tr, stops, plan = _two_trips()
    tr.park(DEPOT, 1)
    now = tr.t
    plan[1] = live.PlanTrip((3,), {3: now + timedelta(minutes=70)}, now + timedelta(minutes=40))   # плановый выезд — через 40 мин
    road = _road(lambda a, b, minute, here: 6.0, unload=lambda p, kg: 10.0)

    def eta_c(rules):
        card = live.car_view(DAY, now, facts(tr.pts, stops, [T0, now]), plan, TRUCK, DEPOT, rules, road, True)
        return card, datetime.fromisoformat(next(x for x in card['stops'] if x['stop_id'] == 'S:C')['eta'])
    # загрузка 15 + 10 × 0,3 т = 18 мин < 40 мин до планового выезда → выезд по плану, затем участок 6 мин
    card, eta = eta_c(live.Rules(load_min=15.0, load_min_per_tonne=10.0))
    assert abs((eta - (now + timedelta(minutes=46))).total_seconds()) < 1.5
    assert card['next']['stop_id'] == 'S:C' and card['next']['delay_min'] == 46 - 70
    # загрузка дольше планового выезда — выезд после загрузки
    _, eta = eta_c(live.Rules(load_min=60.0))
    assert abs((eta - (now + timedelta(minutes=66))).total_seconds()) < 1.5


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
    # начало идущей стоянки — для «Տեսա» страницы (случай, не вид)
    assert card['alerts']['since'] == {'stop': found[0]['from']}


def test_no_contact_current_and_journal():
    tr = Track().park(DEPOT, 5).drive(A)
    contacts = [T0, T0 + timedelta(minutes=3), T0 + timedelta(minutes=11), T0 + timedelta(minutes=12)]
    now = T0 + timedelta(minutes=18)
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], contacts, NEW_APK), now, detail=True)
    nc = [a for a in card['alerts_log'] if a['kind'] == 'no_contact']
    assert [(a['minutes'], a['active']) for a in nc] == [(8, False), (6, True)]
    assert card['state'] == 'offline' and card['contact_age_s'] == 360
    # 5 мин ровно — ещё на связи
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], contacts, NEW_APK), T0 + timedelta(minutes=17), detail=True)
    assert 'no_contact' not in card['alerts']['active']
    # день закрыт (day_closed) — «нет связи» после закрытия не тревога
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], contacts, NEW_APK, closed_at=T0 + timedelta(minutes=12)), now)
    assert card['alerts']['active'] == [] and card['state'] == 'closed'
    # прошлый день — тревог «сейчас» нет
    card = view(facts(tr.pts, [stop('S:A', 1, A, 100.0)], contacts, NEW_APK), now + timedelta(days=1))
    assert 'no_contact' not in card['alerts']['active'] and card['next'] is None


NEW_APK = {'battery': 50, 'charging': False, 'gps': 'on', 'net': 'cell', 'app': '2.2.0'}


def test_offline_reason_only_when_offline_and_latest_state_has_exit():
    tr = Track().park(DEPOT, 5).drive(A)
    st = [stop('S:A', 1, A, 100.0)]
    contacts = [T0, T0 + timedelta(minutes=3)]
    quit_ = {**NEW_APK, 'exit': 'closed'}
    # офлайн + последнее состояние с exit — причина есть
    card = view(facts(tr.pts, st, contacts, quit_), T0 + timedelta(minutes=13))
    assert card['state'] == 'offline' and card['offline_reason'] == 'closed'
    card = view(facts(tr.pts, st, contacts, {**NEW_APK, 'exit': 'shutdown'}), T0 + timedelta(minutes=13))
    assert card['offline_reason'] == 'shutdown'
    # офлайн, но exit в последнем состоянии нет (приложение жило дальше) — причины нет
    card = view(facts(tr.pts, st, contacts, NEW_APK), T0 + timedelta(minutes=13))
    assert card['state'] == 'offline' and card['offline_reason'] is None
    # не офлайн (свежая связь) при exit — причины нет
    card = view(facts(tr.pts, st, contacts, quit_), T0 + timedelta(minutes=5))
    assert card['state'] != 'offline' and card['offline_reason'] is None


def test_no_contact_alarm_only_for_new_apk_before_20_and_within_3h():
    tr = Track().park(DEPOT, 5).drive(A)
    st = [stop('S:A', 1, A, 100.0)]
    contacts = [T0, T0 + timedelta(minutes=3)]
    t = lambda m: T0 + timedelta(minutes=m)   # noqa: E731
    # APK 2.2.0 (есть device): 10 мин тишины — тревога
    card = view(facts(tr.pts, st, contacts, NEW_APK), t(13))
    assert 'no_contact' in card['alerts']['active'] and card['state'] == 'offline'
    # старый APK (пачки раз в 2-17 мин): состояние «կապ չկա», но ни тревоги, ни записей перерывов в журнале
    old = view(facts(tr.pts, st, [T0, t(3), t(20), t(22)]), t(45), detail=True)
    assert old['state'] == 'offline' and old['alerts']['active'] == []
    assert not [a for a in old['alerts_log'] if a['kind'] == 'no_contact']
    # больше 3 ч без связи — «կապ չկա» без тревоги
    card = view(facts(tr.pts, st, contacts, NEW_APK), t(3 + 181))
    assert card['state'] == 'offline' and 'no_contact' not in card['alerts']['active']
    assert view(facts(tr.pts, st, contacts, NEW_APK), t(3 + 179))['alerts']['active'].count('no_contact') == 1
    # после 20:00 по Еревану — без тревоги (APK остановил запись)
    late = datetime(2026, 10, 5, 20, 10, tzinfo=Y)
    f = facts(tr.pts, st, [late - timedelta(minutes=30)], NEW_APK)
    card = view(f, late)
    assert card['state'] == 'offline' and card['alerts']['active'] == []
    f = facts(tr.pts, st, [datetime(2026, 10, 5, 19, 40, tzinfo=Y)], NEW_APK)
    assert 'no_contact' in view(f, datetime(2026, 10, 5, 19, 50, tzinfo=Y))['alerts']['active']


def test_state_alert_over_offline_and_old_apk_silent_threshold():
    tr = Track().park(DEPOT, 5)
    st = [stop('S:A', 1, A, 100.0)]
    t = lambda m: T0 + timedelta(minutes=m)   # noqa: E731
    # APK 2.2.0: GPS выключен и связи нет — другая активная тревога («ահազանգ») важнее «կապ չկա»
    gps_off = [(t(2), 'off')]
    card = view(facts(tr.pts, st, [T0, t(2)], {**NEW_APK, 'gps': 'off'}, gps_off), t(12))
    assert {'gps', 'no_contact'} <= set(card['alerts']['active']) and card['state'] == 'alert'
    assert set(card['alerts']['since']) == set(card['alerts']['active'])   # у каждой идущей — её начало
    # старый APK: до 20 мин тишины — не «կապ չկա», после — да (тревоги нет в обоих случаях)
    old = facts(tr.pts, st, [T0, t(3)])
    assert view(old, t(3 + 19))['state'] != 'offline'
    card = view(old, t(3 + 21))
    assert card['state'] == 'offline' and card['alerts']['active'] == []


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
    assert card['next'] is None and card['alerts'] == {'active': [], 'count': 0, 'since': {}} and card['device'] is None
    assert view({}, T0, truck=live.TruckSpec())['fuel_l'] is None
    card = view(facts([], [stop('S:A', 1, A, 100.0)], [T0]), T0 + timedelta(minutes=2), depot=None, detail=True)
    assert card['state'] == 'standing' and card['stores'] == {'done': 0, 'total': 1, 'in_progress': 0,
                                                              'gps_visited': 0, 'unmarked': 0}
    assert card['track'] == [] and card['stops'][0]['status'] == 'pending'
    # только сигнал «на связи» без GPS (points: []) — позиции нет, связь есть
    card = view(facts([], [], [T0, T0 + timedelta(minutes=1)], NEW_APK), T0 + timedelta(minutes=10))
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


def test_live_source_later_heartbeat_without_exit_clears_reason(courier_app):
    """courier/live.py: device — последнее по времени состояние; exit не «залипает» после heartbeat без него."""
    from courier import events as ev
    from courier.live import LiveSource
    store = courier_app.extensions['courier'].store
    ds = DAY.isoformat()
    store.save_day(ds, 'CAR1', [_day_stop(SID1, 1, A, [('l1', 10, 12.0)], 1)], 'v1', LIVE_NOW.isoformat())
    who = _who(store, 'CAR1', 'Արամ', '1111')
    t = lambda m: LIVE_NOW - timedelta(minutes=m)   # noqa: E731
    dev = {'battery': 80, 'charging': False, 'gps': 'on', 'net': 'wifi', 'app': '2.2.5'}

    def car_device(events):
        assert ev.ingest(store, who, events).json()['rejected'] == []
        with courier_app.app_context():
            return LiveSource(store).fleet(ds)['CAR1']['device']
    assert car_device([_ev('track', None, {'points': [], 'device': dev}, t(30)),
                       _ev('track', None, {'points': [], 'device': {**dev, 'exit': 'closed'}}, t(20))])['exit'] == 'closed'
    later = car_device([_ev('track', None, {'points': [], 'device': dev}, t(5))])   # приложение запущено снова
    assert 'exit' not in later and later['at'] == t(5).isoformat()


def test_build_text_no_contact_reason_from_device_state():
    from route_optimizer import live_alerts as la
    a = {'kind': 'no_contact', 'from': T0.isoformat(), 'to': None, 'active': True, 'minutes': 7, 'lat': None, 'lon': None}

    def text(device, state='offline', reason=None):
        card = {'car_code': 'X1', 'name': 'N', 'drivers': ['Արամ'], 'device': device, 'state': state,
                'offline_reason': reason, 'position': {'lat': 40.1, 'lon': 44.5}}
        return la.build_text(card, a, 'start', RULES)
    assert 'Հավելվածը փակվել է' in text({**NEW_APK, 'exit': 'closed'}, reason='closed')
    assert 'Հեռախոսն անջատվել է' in text({**NEW_APK, 'exit': 'shutdown'})
    # другая активная тревога: state='alert', offline_reason пуст — причина всё равно в тексте
    assert 'Հավելվածը փակվել է' in text({**NEW_APK, 'exit': 'closed'}, state='alert', reason=None)
    for device in (NEW_APK, None, {**NEW_APK, 'exit': 'weird'}):
        out = text(device)
        assert 'փակվել է' not in out and 'անջատվել է' not in out and 'Կապ չկա՝ 7 րոպե' in out


def test_live_source_returns_weight_and_refuels(courier_app):
    """Возврат: вес = кол-во × вес единицы товара по строкам точки (нет там — по любой точке машины, нет нигде — None);
    заправки машины за последние дни — для сверки топлива."""
    from courier import events as ev
    from courier.live import LiveSource
    store = courier_app.extensions['courier'].store
    ds = DAY.isoformat()
    s1 = _day_stop(SID1, 1, A, [('l1', 10, 12.0), ('l2', 5, 6.0)], 1)
    s1['lines'][0]['product_id'], s1['lines'][1]['product_id'] = 11, 12
    s2 = _day_stop(SID2, 2, B, [('l3', 4, 20.0)], 2)
    s2['lines'][0]['product_id'] = 13
    store.save_day(ds, 'CAR1', [s1, s2], 'v1', LIVE_NOW.isoformat())
    who = _who(store, 'CAR1', 'Արամ', '1111')
    t = lambda m: LIVE_NOW - timedelta(minutes=m)   # noqa: E731
    r = ev.ingest(store, who, [
        _ev('return', SID1, {'product_id': 12, 'qty': 3}, t(30)),     # 3 × 6 = 18 кг (строка этой точки)
        _ev('return', SID1, {'product_id': 13, 'qty': 2}, t(20)),     # товар другой точки машины: 2 × 20 = 40 кг
        _ev('return', SID1, {'product_id': 99, 'qty': 1}, t(10)),     # нигде нет — вес неизвестен
        _ev('refuel', None, {'liters': 55.5, 'odometer_km': 10400, 'full_tank': True}, t(5))]).json()
    assert len(r['accepted']) == 4, r
    with courier_app.app_context():
        car1 = LiveSource(store).fleet(ds)['CAR1']
    assert [(x['stop_id'], x['kg']) for x in car1['returns']] == [(SID1, 18.0), (SID1, 40.0), (SID1, None)]
    assert [x['at'] for x in car1['returns']] == [t(30).isoformat(), t(20).isoformat(), t(10).isoformat()]
    assert [(x['car_code'], x['payload']['liters']) for x in car1['refuels']] == [('CAR1', 55.5)]


def test_live_source_cache_ttl_and_fingerprint(courier_app, monkeypatch):
    """Опрос карты не пересчитывает флот: внутри TTL — без обращения к базе; после TTL без новых данных — только отпечаток;
    новое событие после TTL — пересчёт."""
    import threading
    from courier import events as ev
    from courier import live as cl
    from courier.live import LiveSource
    store = courier_app.extensions['courier'].store
    ds = DAY.isoformat()
    store.save_day(ds, 'CAR1', [_day_stop(SID1, 1, A, [('l1', 10, 12.0)], 1)], 'v1', LIVE_NOW.isoformat())
    who = _who(store, 'CAR1', 'Արամ', '1111')
    pt = {'at': (LIVE_NOW - timedelta(minutes=3)).isoformat(), 'lat': A[0], 'lon': A[1], 'acc': 8.0, 'spd': 0.0,
          'brg': None}
    assert ev.ingest(store, who, [_ev('track', None, {'points': [pt], 'device': NEW_APK},
                                      LIVE_NOW - timedelta(minutes=3))]).json()['accepted']
    clock = [1000.0]
    monkeypatch.setattr(cl, '_monotonic', lambda: clock[0])
    calls = []
    real = LiveSource._compute
    monkeypatch.setattr(LiveSource, '_compute', lambda self, day: calls.append(day) or real(self, day))
    source = LiveSource(store)
    with courier_app.app_context():
        first = source.fleet(ds)
        assert source.fleet(ds) is first and calls == [ds]                    # внутри TTL — тот же расчёт
        ev.ingest(store, who, [_ev('track', None, {'points': [], 'device': NEW_APK}, LIVE_NOW - timedelta(minutes=1))])
        clock[0] += cl.TTL_TODAY_S - 1
        assert source.fleet(ds) is first and calls == [ds]                    # новое событие, но TTL не вышел
        clock[0] += 2
        second = source.fleet(ds)
        assert calls == [ds, ds] and len(second['CAR1']['contacts']) == 2     # после TTL новое событие — пересчёт
        clock[0] += cl.TTL_TODAY_S + 1
        assert source.fleet(ds) is second and calls == [ds, ds]               # данных нет — отпечаток тот же, без расчёта
        # параллельные зрители: пересчёт один
        ev.ingest(store, who, [_ev('track', None, {'points': [], 'device': NEW_APK}, LIVE_NOW - timedelta(seconds=5))])
        clock[0] += cl.TTL_TODAY_S + 1
        got = []

        def viewer():
            with courier_app.app_context():
                got.append(source.fleet(ds))
        threads = [threading.Thread(target=viewer) for _ in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert len(calls) == 3 and all(g is got[0] for g in got)


def test_live_source_last_contact_only_within_requested_day(courier_app):
    from courier.live import LiveSource
    store = courier_app.extensions['courier'].store
    who = _who(store, 'CAR1', 'Արամ', '1111')
    store.save_day('2026-10-04', 'CAR1', [_day_stop(SID1, 1, A, [('l1', 1, 1.0)], 1)], 'v1', LIVE_NOW.isoformat())
    store.save_day(DAY.isoformat(), 'CAR1', [_day_stop(SID1, 1, A, [('l1', 1, 1.0)], 1)], 'v1', LIVE_NOW.isoformat())
    seen = '2026-10-05T11:00:00+04:00'
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute('UPDATE terminals SET last_seen_at = ? WHERE id = ?', (seen, who.terminal_id))
    with courier_app.app_context():
        today = LiveSource(store).fleet(DAY.isoformat())['CAR1']
        past = LiveSource(store).fleet('2026-10-04')['CAR1']
    assert past['last_contact'] is None   # терминал был на связи сегодня, вчерашний день его последней связью не считает
    assert today['last_contact'] == seen


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
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', False)   # дорожная модель ETA — сразу, без фонового потока
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
    assert car1['driver'] == 'Արամ' and car1['stores'] == {'done': 0, 'total': 2, 'in_progress': 0,
                                                                    'gps_visited': 1, 'unmarked': 0}
    assert car1['next']['stop_id'] == 'S:A' and car1['next']['planned_eta'] == '2026-10-03T10:40:00+04:00'
    assert car1['next']['here'] is True and car1['next']['delay_min'] == 20
    assert car1['device']['battery'] == 64 and 'track' not in car1
    assert set(car1['alerts']['since']) == set(car1['alerts']['active']) - {'late'}   # API флота отдаёт начало тревог
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


def test_live_access_garage_user_anonymous(client, live_app, monkeypatch):
    monkeypatch.delenv('ROUTES_YANDEX_TILES_KEY', raising=False)
    monkeypatch.delenv('ROUTES_YANDEX_LIVE_KEY', raising=False)
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


def test_live_from_internet_only_garage(client, live_app, monkeypatch):
    for env in ('ROUTES_YANDEX_TILES_KEY', 'ROUTES_YANDEX_LIVE_KEY'):   # ключи из .env ПК — проверка «без ключа»
        monkeypatch.delenv(env, raising=False)
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
    assert 'data-yandex-key=""' in html                               # без ключа в окружении — OpenStreetMap
    with client.get('/static/img/yandex_maps_logo_ru.svg', base_url=PUBLIC) as r:   # логотип грузит routes_basemap.js
        assert r.status_code == 200
    assert client.get('/api/routes/live', base_url=PUBLIC).status_code == 200
    for who in ('boss', 'u'):
        _session_as(client, who)
        assert client.get('/routes/live', base_url=PUBLIC).status_code == 404
        assert client.get('/api/routes/live', base_url=PUBLIC).status_code == 404


def test_live_page_yandex_key_own_first(client, live_app, monkeypatch):
    # владелец 06.10: подложка — Яндекс; свой ключ страницы важнее общего, неверный свой — общий
    main, own = 'a' * 36, 'b' * 36
    _session_as(client, 'boss', base=LAN)
    monkeypatch.delenv('ROUTES_YANDEX_LIVE_KEY', raising=False)
    monkeypatch.setenv('ROUTES_YANDEX_TILES_KEY', main)
    assert f'data-yandex-key="{main}"' in client.get('/routes/live', base_url=LAN).get_data(as_text=True)
    monkeypatch.setenv('ROUTES_YANDEX_LIVE_KEY', own)
    assert f'data-yandex-key="{own}"' in client.get('/routes/live', base_url=LAN).get_data(as_text=True)
    monkeypatch.setenv('ROUTES_YANDEX_LIVE_KEY', 'not a key!')
    assert f'data-yandex-key="{main}"' in client.get('/routes/live', base_url=LAN).get_data(as_text=True)
    monkeypatch.setenv('ROUTES_YANDEX_TILES_KEY', 'bad key?')
    assert 'data-yandex-key=""' in client.get('/routes/live', base_url=LAN).get_data(as_text=True)


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


# ============================== этап 2: дорожная модель ETA в views, кэш карточек ==============================

class FakeRoads:
    """Дороги без Valhalla: км по таблице, пары нет — None (запасная модель)."""
    failed = False
    map_path = None
    version = 'fake'
    km_source = 'osm'

    def __init__(self, km=10.0):
        self._km = km
        self.ensured = []

    def ensure(self, points):
        self.ensured.append(list(points))

    def km(self, a, b):
        return self._km

    def minutes(self, a, b, city):
        return None

    def truck(self, truck_time=None):
        return self


def test_live_road_build_store_time_roads_and_fallback(live_app, monkeypatch):
    """№50/№60: время у магазина — введённое логистом (+ норма на тонну), без него — норма; legs — дорожная модель."""
    from route_optimizer import views
    state = live_app.app.extensions['route_optimizer']
    state.store.save_customer_unload(7, 20, 'qa')
    bundle = replace(state.store.load(), depot=DEPOT)
    base = live.Road(1.3, 25.0, 45.0, (40.1792, 44.4991), 12.0)
    customers = {7: A, 8: B}
    # дорог нет (снимка ERP нет) — только время магазина; legs нет → запасная модель
    road = views._live_road_build(state, bundle, None, None, DAY, base, customers)
    assert road.legs is None
    assert road.unload(A, 1000.0) == pytest.approx(20 + 6.0)       # введённые 20 мин + 6 мин на тонну
    assert road.unload(B, 1000.0) == pytest.approx(8 + 6.0)        # нет введённого — норма на точку + на тонну
    # дороги есть: участки между известными точками — км дорог / скорость зоны (пока Valhalla нет — «model»)
    fake = FakeRoads(10.0)
    asked = []
    monkeypatch.setattr(views, '_roads', lambda *a, **k: asked.append((a, k)) or fake)
    road = views._live_road_build(state, bundle, object(), None, DAY, base, customers)
    assert road.legs(A, B, 600.0, False) == pytest.approx(10.0 / 25.0 * 60.0)    # оба конца в городе
    points = asked[0][0][3]   # точки дорог: магазины дня и склад — как в расчёте «Развоза»
    assert A in points and B in points and DEPOT in points and asked[0][1]['center_zone']
    assert road.legs(A, B, 600.0, True) is None            # положение машины без Valhalla — запасная модель
    fake._km = None
    assert road.legs(A, B, 600.0, False) is None           # пары в дорогах нет — не выдаём прямую за «дороги»


def test_live_roads_builds_in_background_and_serves_fallback(monkeypatch):
    from route_optimizer import views
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', True)
    cache = views._LiveRoads()
    base = live.Road()
    built = live.Road(detour=2.0)
    release, done = threading.Event(), threading.Event()
    calls = []

    def build():
        calls.append(1)
        release.wait(5)
        done.set()
        return built
    assert cache.get('k', build, base) is base            # собирается в фоне — опрос не ждёт: запасная модель
    assert cache.get('k', build, base) is base and calls == [1]   # второй опрос поток не плодит
    release.set()
    assert done.wait(5)
    for _ in range(100):
        got = cache.get('k', build, base)
        if got is built:
            break
        time.sleep(0.02)
    assert got is built and calls == [1]                  # готово — из кэша, без пересборки
    # сбой сборки не роняет опрос: запасная модель, поток освобождён
    cache2 = views._LiveRoads()
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', False)
    assert cache2.get('x', lambda: 1 / 0, base) is base
    assert cache2.get('x', lambda: built, base) is base   # сбой помнится LIVE_ROAD_FAIL_TTL_S — не повторяется на каждом опросе


def test_live_cards_cached_per_fleet_for_ten_seconds(client, live_app, monkeypatch):
    """Опрос нескольких зрителей, детали машины и Telegram — один расчёт флота на 10 с; новый факт — новый расчёт."""
    from route_optimizer import views
    state = live_app.app.extensions['route_optimizer']
    calls = []
    real = live.car_view
    monkeypatch.setattr(live, 'car_view', lambda *a, **k: calls.append(1) or real(*a, **k))
    _session_as(client, 'boss', base=LAN)
    for _ in range(3):
        assert client.get('/api/routes/live', base_url=LAN).status_code == 200
    first = len(calls)
    assert first == 2                                      # CAR1 и CAR9 — по разу
    clock = [time.monotonic()]
    monkeypatch.setattr(views, '_monotonic', lambda: clock[0])
    client.get('/api/routes/live', base_url=LAN)
    clock[0] += views.LIVE_CARDS_TTL_S + 1
    client.get('/api/routes/live', base_url=LAN)
    assert len(calls) == first + 2                         # TTL вышел — пересчёт
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])   # другой объект факта — пересчёт
    client.get('/api/routes/live', base_url=LAN)
    assert len(calls) == first + 4
    body = client.get('/api/routes/live', base_url=LAN).get_json()
    assert all('alerts_log' not in t for t in body['trucks'])   # журнал тревог — только в деталях и для Telegram


# ============================== по ревью этапа 2 ==============================

class FakeValhallaRoads(FakeRoads):
    """Подмена views.ValhallaRoads: route — отдельный запрос Valhalla (считает вызовы)."""

    def __init__(self, km=10.0):
        super().__init__(km)
        self.calls = []
        self.answer = (5.0, 6.0)

    def route(self, a, b, city):
        self.calls.append((a, b))
        return self.answer


def _here_road(live_app, monkeypatch, fake):
    from route_optimizer import views
    state = live_app.app.extensions['route_optimizer']
    bundle = replace(state.store.load(), depot=DEPOT)
    base = live.Road(1.3, 25.0, 45.0, (40.1792, 44.4991), 12.0)
    monkeypatch.setattr(views, '_roads', lambda *a, **k: fake)
    monkeypatch.setattr(views, 'ValhallaRoads', FakeValhallaRoads)
    return views._live_road_build(state, bundle, object(), None, DAY, base, {7: A, 8: B})


def test_here_leg_cached_by_rounded_position_and_failures_briefly(live_app, monkeypatch):
    from route_optimizer import views
    fake = FakeValhallaRoads()
    road = _here_road(live_app, monkeypatch, fake)
    clock = [1000.0]
    monkeypatch.setattr(views, '_monotonic', lambda: clock[0])
    here = (40.1600, 44.4600)
    assert road.legs(here, B, 600.0, True) == pytest.approx(5.0 / 25.0 * 60.0)   # км Valhalla / скорость города (модель)
    near = (here[0] + 0.0002, here[1] + 0.0002)                                    # ~25 м — тот же ключ (~100 м)
    assert road.legs(near, B, 600.0, True) == pytest.approx(12.0) and len(fake.calls) == 1
    assert road.legs((40.1700, 44.4700), B, 600.0, True) is not None and len(fake.calls) == 2   # другое место — запрос
    clock[0] += views.LIVE_HERE_TTL_S + 1
    road.legs(here, B, 600.0, True)
    assert len(fake.calls) == 3                                                    # через 60 с — заново
    fake.answer = None                                                             # занят пул — запасная модель
    other = (40.1650, 44.4650)
    assert road.legs(other, B, 600.0, True) is None and road.legs(other, B, 600.0, True) is None
    assert len(fake.calls) == 4                                                    # «не получилось» помнится недолго
    clock[0] += views.LIVE_FAIL_TTL_S + 1
    road.legs(other, B, 600.0, True)
    assert len(fake.calls) == 5


def test_here_leg_engine_exception_falls_back_not_fails(live_app, monkeypatch):
    fake = FakeValhallaRoads()
    road = _here_road(live_app, monkeypatch, fake)

    def boom(a, b, city):
        raise MemoryError('tiles')
    fake.route = boom
    assert road.legs((40.1600, 44.4600), B, 600.0, True) is None           # запасная модель, исключение наружу не идёт


def test_live_flight_locks_trimmed_with_cards_cache(live_app):
    from route_optimizer import views
    state = live_app.app.extensions['route_optimizer']
    state.live_cards.clear()
    state.live_flight.clear()
    for n in range(10):
        state.live_flight[date(2026, 1, 1 + n)] = threading.Lock()
    views._live_cards(state, API_NOW.date())
    assert set(state.live_flight) <= set(state.live_cards) | {d for d, lk in state.live_flight.items() if lk.locked()}
    assert len(state.live_flight) <= 4


def test_here_leg_over_budget_does_not_ask_valhalla(live_app, monkeypatch):
    from route_optimizer import views
    fake = FakeValhallaRoads()
    road = _here_road(live_app, monkeypatch, fake)
    views._budget.until = time.monotonic() - 1                                     # бюджет пересчёта флота вышел
    try:
        assert road.legs((40.1600, 44.4600), B, 600.0, True) is None and fake.calls == []
        assert road.legs(A, B, 600.0, False) == pytest.approx(10.0 / 25.0 * 60.0)   # участки между магазинами — не затронуты
    finally:
        views._budget.until = float('inf')


def test_live_cards_single_flight_never_waits_on_slow_recompute(live_app, monkeypatch):
    """Пока один поток пересчитывает флот (хоть и долго), остальные берут прежний результат, а не встают в очередь."""
    from route_optimizer import views
    state = live_app.app.extensions['route_optimizer']
    day = API_NOW.date()
    first = views._live_cards(state, day)
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])   # новый факт — нужен пересчёт
    started, release = threading.Event(), threading.Event()
    real = views._live_context

    def slow(*a, **k):
        started.set()
        release.wait(10)
        return real(*a, **k)
    monkeypatch.setattr(views, '_live_context', slow)
    owner = threading.Thread(target=lambda: views._live_cards(state, day))
    owner.start()
    assert started.wait(5)
    t0 = time.monotonic()
    again = views._live_cards(state, day)
    assert time.monotonic() - t0 < 1.0 and again[3] is first[3]                    # прежние карточки, мгновенно
    release.set()
    owner.join(5)
    assert views._live_cards(state, day)[3] is not first[3]                        # владелец закончил — новый расчёт
    # прежнего результата нет — ждём чужой пересчёт и берём его, а не считаем второй раз
    state.live_cards.clear()
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])
    started.clear()
    release.clear()
    calls = []
    monkeypatch.setattr(views, '_live_context', lambda *a, **k: calls.append(1) or slow(*a, **k))
    got = []
    t1 = threading.Thread(target=lambda: got.append(views._live_cards(state, day)))
    t1.start()
    assert started.wait(5)
    t2 = threading.Thread(target=lambda: got.append(views._live_cards(state, day)))
    t2.start()
    time.sleep(0.2)
    release.set()
    t1.join(5)
    t2.join(5)
    assert len(got) == 2 and got[0][3] is got[1][3] and len(calls) == 1


def test_live_roads_failed_build_not_retried_every_poll_and_past_days_skipped(live_app, monkeypatch):
    from route_optimizer import views
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', False)
    cache = views._LiveRoads()
    base = live.Road()
    clock = [100.0]
    monkeypatch.setattr(views, '_monotonic', lambda: clock[0])
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError('graph is broken')
    for _ in range(3):
        assert cache.get('k', boom, base) is base
    assert len(calls) == 1                                                         # сбой не повторяется на каждом опросе
    clock[0] += views.LIVE_ROAD_FAIL_TTL_S + 1
    cache.get('k', boom, base)
    assert len(calls) == 2
    # прошлый день: ETA нет — дорожную модель не собираем; сегодня — собираем
    state = live_app.app.extensions['route_optimizer']
    built = []
    state.live_roads = views._LiveRoads()   # кэш общий у процесса — чистый
    monkeypatch.setattr(views, '_live_road_build', lambda *a, **k: built.append(a[4]) or live.Road())
    fleet = state.live_facts.data['2026-10-03']
    views._live_context(state, date(2026, 10, 2), fleet)
    assert built == []
    views._live_context(state, API_NOW.date(), fleet)
    assert built == [API_NOW.date()]


def test_eta_lunch_not_inserted_after_window_end_and_ongoing_stay_counts():
    road = _road(lambda a, b, minute, here: 5.0, unload=lambda p, kg: 10.0)
    rules = live.Rules(lunch_min=30.0)    # окно 12:30–14:30
    late = datetime(2026, 10, 5, 15, 0, tzinfo=Y)
    assert _lunch_eta(late, road, rules) == _lunch_eta(late, road, live.Rules(lunch_min=0.0))   # окно кончилось — обеда нет
    # идущая стоянка не по плану, начавшаяся в окне: простояла ≥ половины обеда — обед идёт, второй не добавляем;
    # короткая остановка (6 мин) — ещё не обед, он впереди
    start = datetime(2026, 10, 5, 13, 0, tzinfo=Y)
    stops = [stop('S:A', 1, A, 100.0, seq=1), stop('S:B', 2, B, 100.0, seq=2)]

    def etas(minutes, r):
        return {x['stop_id']: x['eta'] for x in _view_eta(Track(start).park((40.1600, 44.4600), minutes), stops, road, r)['stops']}
    assert etas(16, rules) == etas(16, live.Rules(lunch_min=0.0))
    assert etas(6, rules) != etas(6, live.Rules(lunch_min=0.0))
    # та же стоянка, но короткая и уже закончилась — обеда не было, он ещё впереди
    tr2 = Track(start).park((40.1600, 44.4600), 3).drive(_mid(DEPOT, A))
    a = {x['stop_id']: x['eta'] for x in _view_eta(tr2, stops, road, rules)['stops']}
    b = {x['stop_id']: x['eta'] for x in _view_eta(tr2, stops, road, live.Rules(lunch_min=0.0))['stops']}
    assert a != b


def _lunch_eta(start, road, rules):
    tr = Track(start).park(DEPOT, 2).drive(_mid(DEPOT, A))
    stops = [stop('S:A', 1, A, 100.0, seq=1), stop('S:B', 2, B, 100.0, seq=2)]
    return [x['eta'] for x in _view_eta(tr, stops, road, rules)['stops']]


def test_unknown_delivered_share_is_not_counted_as_aboard():
    """Закрытая точка, а сколько отдали неизвестно (share None): неизвестное ≠ 0 — её вес не «в машине» и не «выгружено»."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(B)
    tr.park(B, 8)
    stops = [stop('S:A', 1, A, 600.0, 'partial', None, at_a + timedelta(minutes=2), seq=1),
             stop('S:B', 2, B, 400.0, 'full', 1.0, tr.t - timedelta(minutes=6), seq=2)]
    ld = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, [live.PlanTrip((1, 2), {})])['load']
    assert ld['remaining_kg'] == 0 and ld['refused_kg'] == 0 and ld['loaded_kg'] == 1000 and ld['trip_kg'] == 1000
    assert ld['delivered_kg'] == 400 and ld['unloaded_kg'] == 0


# ============================== «как телематика»: прогноз только при связи, груз по плану, GPS-визиты, воспроизведение ==

def _to_a(stops=None, planned_a=None):
    """Склад → на полпути к A; точки A и B ожидают. Возвращает (трек, точки, план)."""
    tr = Track().park(DEPOT, 5).drive(_mid(DEPOT, A))
    stops = stops or [stop('S:A', 1, A, 600.0, seq=1), stop('S:B', 2, B, 400.0, seq=2)]
    plan = [live.PlanTrip((1, 2), {1: planned_a or tr.t + timedelta(minutes=10), 2: tr.t + timedelta(minutes=40)})]
    return tr, stops, plan


def test_forecast_suppressed_when_contact_stale():
    """Связи нет дольше no_contact_min (APK с device): магазин и план — да, ETA, опоздание, возврат и «не успеет» — нет."""
    tr, stops, plan = _to_a()
    f = facts(tr.pts, stops, [T0, tr.t], NEW_APK)
    card = view(f, tr.t + timedelta(minutes=30), plan, detail=True)
    nxt = card['next']
    assert card['forecast'] is False and card['state'] == 'offline' and card['data_until'] == tr.t.isoformat()
    assert nxt['stop_id'] == 'S:A' and nxt['planned_eta'] == (tr.t + timedelta(minutes=10)).isoformat()
    assert (nxt['eta'], nxt['delay_min'], nxt['eta_source'], nxt['eta_unknown'], nxt['here']) == (None, None, None,
                                                                                                    True, False)
    assert [x['eta'] for x in card['stops']] == [None, None] and card['return_eta'] is None and card['late'] == []
    # свежая связь — прогноз как раньше
    card = view(f, tr.t + timedelta(minutes=2), plan, detail=True)
    assert card['forecast'] is True and card['next']['eta'] is not None and card['next']['eta_unknown'] is False
    assert all(x['eta'] for x in card['stops'])


def test_forecast_old_apk_threshold_and_late_needs_contact():
    tr, stops, plan = _to_a(planned_a=T0 - timedelta(hours=1))    # план A час назад — «не успеет» при свежей связи
    old = facts(tr.pts, stops, [T0, tr.t])                         # старый APK (без device): 20 мин тишины — ещё связь
    assert view(old, tr.t + timedelta(minutes=19), plan)['forecast'] is True
    assert view(old, tr.t + timedelta(minutes=21), plan)['forecast'] is False
    new = facts(tr.pts, stops, [T0, tr.t], NEW_APK)
    late = view(new, tr.t + timedelta(minutes=1), plan)
    assert [x['stop_id'] for x in late['late']] == ['S:A']
    # «не успеет» — без начала (её from — момент расчёта): «Տեսա» страницы держит её по ключу важности
    assert 'late' in late['alerts']['active'] and 'late' not in late['alerts']['since']
    # точка 10 мин назад (меньше LATE_FIX_MAX), но связи нет 10 мин — прогноза «не успеет» нет
    card = view(new, tr.t + timedelta(minutes=10), plan)
    assert card['late'] == [] and 'late' not in card['alerts']['active']


def test_forecast_kept_for_standing_truck_with_old_fix_but_fresh_contact():
    """Стоит (новых точек нет 30 мин), а терминал на связи — прогноз есть: связь, а не возраст точки."""
    tr, stops, plan = _to_a()
    now = tr.t + timedelta(minutes=31)
    card = view(facts(tr.pts, stops, [T0, now - timedelta(minutes=1)], NEW_APK), now, plan)
    assert card['forecast'] is True and card['next']['eta'] is not None and card['next']['eta_unknown'] is False
    assert card['data_until'] == (now - timedelta(minutes=1)).isoformat()


def test_planned_kg_is_next_trip_to_load():
    tr = Track().park(DEPOT, 5)
    stops = [stop('S:A', 1, A, 600.0, seq=1), stop('S:B', 2, B, 400.0, seq=2), stop('S:C', 3, C, 300.0, seq=3)]
    plan = [live.PlanTrip((1, 2), {}), live.PlanTrip((3,), {})]
    ld = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)['load']
    assert ld['trips_gone'] == 0 and ld['remaining_kg'] == 0 and ld['planned_kg'] == 1000   # до выезда — рейс 1
    tr, stops, plan = _two_trips()
    tr.park(DEPOT, 10)
    assert view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)['load']['planned_kg'] == 300   # между рейсами — рейс 2
    tr.drive(_mid(DEPOT, C))
    assert view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)['load']['planned_kg'] is None  # все рейсы уехали
    assert view(facts(), T0)['load']['planned_kg'] is None


def test_gps_visit_unmarked_is_skipped_for_next_store_and_eta():
    """Постоял у A и уехал, а водитель A не отметил: A — «GPS-ով այցելած, չնշված», следующий — B, ETA у A нет."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(_mid(A, B))
    stops = [stop('S:A', 1, A, 600.0, seq=1), stop('S:B', 2, B, 400.0, seq=2)]
    plan = [live.PlanTrip((1, 2), {})]
    card = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan, detail=True)
    st = {x['stop_id']: x for x in card['stops']}
    g = st['S:A']['gps']
    assert g['arrive'] == (at_a + timedelta(minutes=1)).isoformat() and g['minutes'] == 7   # стоянка — с 1-й точки стоя
    assert g['leave'] is not None and g['here'] is False
    assert st['S:A']['unmarked'] is True and st['S:B']['gps'] is None and st['S:B']['unmarked'] is False
    assert card['next']['stop_id'] == 'S:B' and st['S:A']['eta'] is None and st['S:B']['eta'] is not None
    assert card['stores'] == {'done': 0, 'total': 2, 'in_progress': 0, 'gps_visited': 1, 'unmarked': 1}
    # отмеченная доставка — уже не «չնշված»
    stops[0] = stop('S:A', 1, A, 600.0, 'full', 1.0, at_a + timedelta(minutes=3), seq=1)
    card = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)
    assert card['stores']['gps_visited'] == 1 and card['stores']['unmarked'] == 0


def test_gps_visit_here_while_standing_and_finished_on_past_day():
    tr = Track().park(DEPOT, 10).drive(A).park(A, 6)
    stops = [stop('S:A', 1, A, 600.0, seq=1), stop('S:B', 2, B, 400.0, seq=2)]
    plan = [live.PlanTrip((1, 2), {})]
    f = facts(tr.pts, stops, [T0, tr.t])
    card = view(f, tr.t, plan, detail=True)
    g = card['stops'][0]['gps']
    assert g['here'] is True and g['leave'] is None and g['minutes'] == 5
    assert card['stops'][0]['unmarked'] is False and card['next']['stop_id'] == 'S:A' and card['next']['here'] is True
    past = view(f, tr.t + timedelta(days=1), plan, detail=True)   # прошлый день: визит кончился, точка не отмечена
    assert past['stops'][0]['gps']['here'] is False and past['stops'][0]['unmarked'] is True
    assert past['stores']['unmarked'] == 1


def test_unmarked_stop_does_not_hold_next_trip_departure():
    """Рейс 1: A отмечен, B посещён по GPS, но не отмечен; машина сдала груз и уехала с рейсом 2 — он уехал."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(B).park(B, 8).drive(DEPOT).park(DEPOT, 15).drive(_mid(DEPOT, C))
    stops = [stop('S:A', 1, A, 600.0, 'full', 1.0, at_a + timedelta(minutes=2), seq=1),
             stop('S:B', 2, B, 400.0, seq=2), stop('S:C', 3, C, 300.0, seq=3)]
    plan = [live.PlanTrip((1, 2), {}), live.PlanTrip((3,), {})]
    card = view(facts(tr.pts, stops, [T0, tr.t]), tr.t, plan)
    assert card['load']['trips_gone'] == 2 and card['next']['stop_id'] == 'S:C'
    assert card['stores']['unmarked'] == 1


def test_track_t_aligned_with_simplified_track(monkeypatch):
    """track_t — 1:1 с track. Точка линии — точка трека в свой момент или середина стоянки в момент прибытия или
    отъезда (линия трека 08.10: стоянка — одна точка, track_line)."""
    tr = Track().park(DEPOT, 5).drive(A).park(A, 6).drive(B)
    raw = {round(p[0] / 1000): [round(p[1], 6), round(p[2], 6)] for p in tr.pts}
    stays = ac.reconstruct(live.track_fixes(tr.pts), [], DEPOT).stays
    at_stay = {round(t.timestamp()): [round(s.center[0], 6), round(s.center[1], 6)]
               for s in stays for t in (s.arrive, s.leave)}
    assert len(stays) == 2
    for limit in (live.TRACK_LINE_POINTS, 12):     # без упрощения и с упрощением линии
        monkeypatch.setattr(live, 'TRACK_LINE_POINTS', limit)
        card = view(facts(tr.pts, [], [T0, tr.t]), tr.t, detail=True)
        assert len(card['track_t']) == len(card['track']) and len(card['track']) <= max(limit, 2)
        assert card['track_t'] == sorted(card['track_t'])
        assert card['track_t'][0] == round(T0.timestamp()) and card['track_t'][-1] == round(tr.t.timestamp())
        assert all(at_stay.get(t) == p or raw.get(t) == p for t, p in zip(card['track_t'], card['track']))
        assert all(t in card['track_t'] for t in at_stay)   # стоянки остаются и при упрощении
    assert view(facts(), T0, detail=True)['track_t'] == []


def test_api_live_carries_contact_gps_and_replay_fields(client, live_app):
    _session_as(client, 'boss', base=LAN)
    car1 = {t['car_code']: t for t in client.get('/api/routes/live', base_url=LAN).get_json()['trucks']}['CAR1']
    assert car1['forecast'] is True and car1['data_until'] == car1['position']['at']   # точка позже связи
    assert car1['stores']['gps_visited'] == 1 and car1['stores']['unmarked'] == 0 and car1['contact_age_s'] == 60
    assert car1['next']['eta_unknown'] is False and car1['load']['planned_kg'] is None
    truck = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    assert len(truck['track_t']) == len(truck['track']) > 1
    assert truck['stops'][0]['gps']['here'] is True and truck['stops'][0]['unmarked'] is False
    assert truck['stops'][1]['gps'] is None


def test_api_live_truck_track_by_roads_only_on_truck_card(client, live_app, monkeypatch):
    """Линия трека по дорогам (08.10): привязку заказывает только карточка машины (/truck) — флот и ход дня «Развоза»
    нет; привязанный кусок — в линии; Valhalla нет — линия без привязки, ответ тот же по составу."""
    from route_optimizer import views
    asked = []

    def matcher(state, capacity_kg):
        def match(c):
            asked.append((c.key, capacity_kg))
            return [(p[0] + 0.001, p[1], p[2]) for p in c.raw()], True
        return match
    monkeypatch.setattr(views, '_track_matcher', matcher)
    _session_as(client, 'boss', base=LAN)
    assert client.get('/api/routes/live', base_url=LAN).status_code == 200
    assert client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).status_code == 200
    assert asked == []
    truck = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    assert asked and all(cap == 10000.0 for _, cap in asked)       # тоннаж машины — из настроек (CAR1 — 10 т)
    plain = live.car_view(DAY, API_NOW, live_app.app.extensions['route_optimizer'].live_facts.fleet('2026-10-03')['CAR1'],
                          [], TRUCK, DEPOT, RULES, ROAD, True)
    assert len(truck['track']) == len(truck['track_t']) and truck['track'] != plain['track']
    monkeypatch.setattr(views, '_track_matcher', lambda state, capacity_kg: None)   # Valhalla нет
    monkeypatch.setattr(live_app.app.extensions['route_optimizer'], 'live_tracks', views._LiveTracks())
    bare = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    assert set(bare) == set(truck) and len(bare['track']) == len(bare['track_t']) > 1
    assert truck['track_pending'] is False and bare['track_pending'] is False   # привязано / привязывать нечем


def test_api_live_truck_waits_for_its_pieces_and_flags_pending(client, live_app, monkeypatch):
    """Карточка машины ждёт привязку своих кусков не дольше LIVE_TRUCK_WAIT_S (фон, без замков); не дождалась или сбой
    движка (не кэшируется) — track_pending: страница переспросит; дождалась — линия уже по дорогам с первого ответа."""
    import threading as th
    from route_optimizer import views
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', True)
    state = live_app.app.extensions['route_optimizer']
    gate = th.Event()

    def matcher(state_, capacity_kg):
        def match(c):
            gate.wait(10)
            return [(p[0] + 0.001, p[1], p[2]) for p in c.raw()], True
        return match
    monkeypatch.setattr(views, '_track_matcher', matcher)
    monkeypatch.setattr(views, 'LIVE_TRUCK_WAIT_S', 0.3)
    monkeypatch.setattr(state, 'live_tracks', views._LiveTracks())
    _session_as(client, 'boss', base=LAN)
    started = time.monotonic()
    slow = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    assert slow['track_pending'] is True and time.monotonic() - started < 5.0      # не дождалась — без привязки
    gate.set()
    monkeypatch.setattr(views, 'LIVE_TRUCK_WAIT_S', 5.0)
    done = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    assert done['track_pending'] is False and done['track'] != slow['track']

    def broken(state_, capacity_kg):
        def match(c):
            raise OSError('engine down')
        return match
    monkeypatch.setattr(views, '_track_matcher', broken)
    monkeypatch.setattr(state, 'live_tracks', views._LiveTracks())
    failed = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    assert failed['track_pending'] is True and failed['track'] == slow['track']
    assert 'track_pending' not in client.get('/api/routes/live', base_url=LAN).get_json()['trucks'][0]


def test_live_page_refetches_card_while_track_pending():
    """Страница: track_pending — карточка ещё раз через 3 с, не больше двух раз на машину и день, не при воспроизведении
    (прошлый день не опрашивается). ?v= поднят."""
    js = (ROOT / 'static' / 'js' / 'routes_live.js').read_text(encoding='utf-8')
    assert 'retrack(one.truck)' in js and 't.track_pending' in js and 'r.tries >= 2' in js and '!state.replay.on' in js
    v = re.search(r"js/routes_live\.js'\) }}\?v=(\d+)", (ROOT / 'templates' / 'routes_live.html').read_text(encoding='utf-8'))
    assert v and int(v.group(1)) >= 14   # не ниже версии с track_pending: следующие поднимают его дальше


# ============================== плановая линия, отклонение от неё, показатели дня (владелец 08.10) ==============================

def _off(p, north_m):
    """Точка north_m метров севернее p (равнопромежуточная проекция live)."""
    return (p[0] + north_m / live.M_PER_DEG_LAT, p[1])


PLAN_LINE = ((DEPOT, A, B, DEPOT),)
MID_AB = ((A[0] + B[0]) / 2, (A[1] + B[1]) / 2)


def _route(lines=PLAN_LINE, road=True, stops=((7, A), (8, B)), km=None, parts=None, straight=()):
    lines = tuple(tuple(x) for x in lines)
    geo = live.RouteGeometry(lines, (lines if parts is None else parts) if road else (), straight)
    return live.PlanRoute(geo, tuple(stops), km)


def _route_view(tr, now, route, rules=RULES, detail=True):
    stops = [stop('S:A', 7, A, 100.0), stop('S:B', 8, B, 100.0, seq=2)]
    return live.car_view(DAY, now, facts(tr.pts, stops, [T0, tr.t]), [], TRUCK, DEPOT, rules, ROAD, detail, None, route)


def test_segment_and_polyline_distance_in_metres():
    a, b = (40.18, 44.50), (40.18, 44.52)
    assert live.segment_m(_off(a, 250.0), a, b) == pytest.approx(250.0, abs=0.01)        # над отрезком — перпендикуляр
    beyond = (40.18, 44.53)                                                              # за концом — до конца
    assert live.segment_m(beyond, a, b) == pytest.approx(haversine_km(beyond, b) * 1000, rel=0.01)
    assert live.segment_m(_off(a, 80.0), a, a) == pytest.approx(80.0, abs=0.01)           # вырожденный отрезок — точка
    line = [a, b, (40.20, 44.52)]
    assert live.polyline_m((40.19, 44.525), line) == pytest.approx(
        min(live.segment_m((40.19, 44.525), x, y) for x, y in zip(line, line[1:])))
    assert live.polyline_m(a, [a]) == 0.0 and live.polyline_m(a, []) == math.inf


def test_route_index_matches_brute_force():
    import random
    rnd = random.Random(8)
    lines = [[(40.15 + 0.002 * i, 44.45 + 0.003 * math.sin(i / 3)) for i in range(60)],
             [(40.20, 44.40), (40.20, 44.60)]]                                           # и длинный прямой участок
    index = live.RouteIndex(lines, 300.0)
    for _ in range(600):
        p = (40.14 + rnd.random() * 0.14, 44.38 + rnd.random() * 0.25)
        for r in (100.0, 300.0):
            assert index.near(p, r) == (min(live.polyline_m(p, x) for x in lines) <= r), (p, r)
    assert live.RouteIndex([], 300.0).near(A, 300.0) is False


def _detour_track(off_m, at=T0, park_min=0):
    """Склад → A → в сторону от линии A–B на off_m метров (у середины A–B; park_min — стоянка там) → B → склад."""
    side = _off(MID_AB, off_m)
    tr = Track(at).park(DEPOT, 5).drive(A).park(A, 3).drive(side)
    if park_min:
        tr.park(side, park_min)
    return tr.drive(B).park(B, 3).drive(DEPOT).park(DEPOT, 2)


def test_deviation_counted_only_when_far_long_and_by_road():
    tr = _detour_track(1500.0)
    card = _route_view(tr, tr.t, _route())
    dev = card['deviation']
    assert dev['count'] == 1 and dev['threshold_m'] == 300 and dev['active'] is False
    assert 1.5 < dev['km'] < 3.5                                       # туда и обратно дальше 300 м от линии
    al = [a for a in card['alerts_log'] if a['kind'] == 'deviation']
    assert len(al) == 1 and al[0]['km'] == dev['km'] and al[0]['to'] is not None and al[0]['lat'] is not None
    run = dev['runs'][0]
    assert run['from'] == al[0]['from'] and len(run['line']) > 2
    assert all(live.polyline_m(tuple(p), PLAN_LINE[0]) > 300 for p in run['line'])
    # рядом с линией (200 м) — не отклонение; порог из настроек — 100 м: уже отклонение
    near = _detour_track(200.0)
    assert _route_view(near, near.t, _route())['deviation']['count'] == 0
    assert _route_view(near, near.t, _route(), rules=live.Rules(deviation_m=100.0))['deviation']['count'] == 1
    # линия по прямой (дорог нет) — отклонение не считается: нет ни сводки, ни тревоги
    card = _route_view(tr, tr.t, _route(road=False))
    assert card['deviation'] is None and not [a for a in card['alerts_log'] if a['kind'] == 'deviation']
    assert card['route']['road'] is False and card['route']['lines']   # но линия на карте есть
    # плана нет — ни линии, ни отклонения
    card = _route_view(tr, tr.t, None)
    assert card['route'] is None and card['deviation'] is None


def test_deviation_needs_half_km_and_a_minute():
    """Короткий заезд: дальше порога меньше 0,5 км пути — не отклонение."""
    tr = _detour_track(450.0)          # вне 300 м — ~2 × 150 м
    assert _route_view(tr, tr.t, _route())['deviation']['count'] == 0
    side = _off(MID_AB, 1500.0)
    fixes = [Fix(T0, *side, 5.0), Fix(T0 + timedelta(seconds=20), *_off(side, 600.0), 5.0)]   # 0,6 км, но 20 с
    index = live.RouteIndex(PLAN_LINE, 300.0)
    assert live.deviation_runs(fixes, index, 300.0, [], T0, None) == []
    fixes[1] = Fix(T0 + timedelta(seconds=90), *_off(side, 600.0), 5.0)
    assert len(live.deviation_runs(fixes, index, 300.0, [], T0, None)) == 1
    assert live.deviation_runs(fixes, None, 300.0, [], T0, None) == []                      # линий нет
    assert live.deviation_runs(fixes, index, 300.0, [], None, None) == []                   # не выезжала
    assert live.deviation_runs(fixes, index, 300.0, [(side, 800.0)], T0, None) == []         # у магазина / склада
    assert live.deviation_runs(fixes, index, 300.0, [], T0, T0 + timedelta(seconds=30)) == []   # после закрытия дня


def test_deviation_ongoing_is_active_alert():
    side = _off(MID_AB, 1500.0)
    tr = Track().park(DEPOT, 5).drive(A).park(A, 3).drive(side)
    card = _route_view(tr, tr.t + timedelta(seconds=20), _route(), detail=False)
    assert card['deviation']['active'] is True and 'deviation' in card['alerts']['active'] and card['state'] == 'alert'
    al = [a for a in card['alerts_log'] if a['kind'] == 'deviation']
    assert al[0]['active'] is True and al[0]['to'] is None
    assert 'runs' not in card['deviation'] and 'lines' not in card['route']   # линии — только в деталях
    # точка давняя (связи нет) — не «сейчас»
    old = _route_view(tr, tr.t + timedelta(minutes=10), _route(), detail=False)
    assert old['deviation']['count'] == 1 and old['deviation']['active'] is False


LUNCH_AT = datetime(2026, 10, 5, 12, 20, tzinfo=Y)


def test_deviation_lunch_stay_itself_is_not_counted():
    """Стоянка обеда (та же, что у тревоги «стоянка») — не отклонение, и заезд к ней и отъезд не длиннее 3 км — тоже:
    кафе в 1,5 км от линии — машина не «красная» весь день; тот же заезд не в обед — одно отклонение, со стоянкой."""
    lunch = _detour_track(1500.0, LUNCH_AT, park_min=30)
    assert _route_view(lunch, lunch.t, _route())['deviation']['count'] == 0
    near = _detour_track(450.0, LUNCH_AT, park_min=30)          # кафе у дороги
    assert _route_view(near, near.t, _route())['deviation']['count'] == 0
    morning = _detour_track(1500.0, datetime(2026, 10, 5, 9, 0, tzinfo=Y), park_min=30)
    assert _route_view(morning, morning.t, _route())['deviation']['count'] == 1


def test_track_line_changes_nothing_but_the_line():
    """Линия трека (стоянки — точкой, езда — по дорогам) — только отображение: км, топливо, визиты, тревоги,
    отклонение, показатели дня — те же, привязана линия или нет; км — reconstruct по тем же точкам."""
    tr = _detour_track(1500.0)
    stops = [stop('S:A', 7, A, 100.0), stop('S:B', 8, B, 100.0, seq=2)]
    f = facts(tr.pts, stops, [T0, tr.t])

    def snap(parts):   # «привязка» всех кусков езды: на 40 м севернее
        return {c.key: [(p[0] + 40.0 / live.M_PER_DEG_LAT, p[1], p[2]) for p in c.raw()] for c in parts if not c.stay}
    for now in (tr.t, tr.t + timedelta(minutes=1), tr.t + timedelta(days=1)):
        plain = live.car_view(DAY, now, f, [], TRUCK, DEPOT, RULES, ROAD, True, None, _route())
        snapped = live.car_view(DAY, now, f, [], TRUCK, DEPOT, RULES, ROAD, True, None, _route(), snap)
        assert snapped['track'] != plain['track'] and len(snapped['track']) == len(snapped['track_t'])
        rest = ('track', 'track_t', 'track_v', 'track_km', 'track_dev_m', 'track_gaps')   # линия и подсказка её точек
        assert {k: v for k, v in snapped.items() if k not in rest} == {k: v for k, v in plain.items() if k not in rest}
        brief = live.car_view(DAY, now, f, [], TRUCK, DEPOT, RULES, ROAD, False, None, _route())
        rest = ('deviation', 'route')   # у деталей — ещё линии плана и отклонений
        assert {k: v for k, v in brief.items() if k not in rest} == {k: v for k, v in plain.items() if k in brief and k not in rest}
    fixes = live.track_fixes(tr.pts)
    actual = ac.reconstruct(fixes, [ac.PlanStop('S:A', 7, A, 100.0), ac.PlanStop('S:B', 8, B, 100.0)], DEPOT)
    assert plain['km'] == round(actual.km_gps, 1) and plain['deviation']['count'] == 1
    assert plain['stats'] == {**live.day_stats(ac.clean_track(fixes)), 'overspeed': plain['stats']['overspeed'],
                              'adherence_pct': plain['deviation']['adherence_pct']}


def test_deviation_long_detour_around_lunch_still_counts():
    """Дальний объезд вокруг обеда (заезд и отъезд дальше 3 км вне линии) — отклонения, но без самой стоянки обеда."""
    far = _detour_track(6000.0, LUNCH_AT, park_min=30)
    runs = _route_view(far, far.t, _route())['deviation']['runs']
    stay = [p for p in far.pts if p[4] == 0.0 and haversine_km((p[1], p[2]), _off(MID_AB, 6000.0)) < 0.01]
    lo, hi = stay[0][0] / 1000, stay[-1][0] / 1000
    assert len(runs) == 2 and all(r['km'] > live.LUNCH_DETOUR_MAX_KM for r in runs)
    assert all(datetime.fromisoformat(r['to']).timestamp() <= lo or datetime.fromisoformat(r['from']).timestamp() >= hi
               for r in runs)
    # отклонение не у обеда (не кончилось прямо перед ним и не началось сразу после) — правило обеда его не трогает
    side = _off(MID_AB, 1500.0)
    index = live.RouteIndex(PLAN_LINE, 300.0)
    run = [Fix(T0 + timedelta(seconds=30 * i), side[0], side[1] + 0.003 * i, 5.0) for i in range(5)]   # ~1 км
    back = [Fix(T0 + timedelta(minutes=5), *A, 5.0)]
    lunch = (T0 + timedelta(minutes=30), T0 + timedelta(minutes=60))
    assert len(live.deviation_runs(run + back, index, 300.0, [], T0, None, lunch)) == 1
    stay = [Fix(lunch[0], *side, 1.0), Fix(lunch[1], *side, 1.0)]
    late = [Fix(lunch[1] + timedelta(seconds=30 * (i + 1)), side[0], side[1] + 0.003 * i, 5.0) for i in range(5)]
    assert live.deviation_runs(stay + late, index, 300.0, [], T0, None, lunch) == []   # сразу после обеда, ~1 км


def test_plan_numbers_on_stops_and_route_stops():
    tr = _detour_track(0.0)
    route = _route(lines=((DEPOT, B, DEPOT), (DEPOT, A, B, DEPOT)), stops=((8, B), (7, A), (8, B)), km=12.34)
    card = _route_view(tr, tr.t, route)
    assert card['route']['km'] == 12.3 and card['route']['trips'] == 2 and card['route']['stops'] == 2
    assert [(s['customer_id'], s['no']) for s in card['route']['points']] == [(8, 1), (7, 2)]
    assert {s['customer_id']: s['plan_no'] for s in card['stops']} == {7: 2, 8: 1}
    assert len(card['route']['lines']) == 2
    assert all(s['plan_no'] is None for s in _route_view(tr, tr.t, None)['stops'])


def test_day_stats_speed_times_and_no_data():
    t = T0
    pts = [ac.TrackFix(t + timedelta(seconds=s), A[0] + i * 0.001, A[1], 8.0, v)
           for i, (s, v) in enumerate([(0, 0.0), (60, 0.0), (75, 12.0), (90, 25.0), (105, 300 / 3.6), (120, 10.0),
                                       (720, 10.0), (735, 0.0)])]
    st = live.day_stats(pts)
    assert st['max_speed']['kmh'] == 90 and st['max_speed']['at'] == (t + timedelta(seconds=90)).isoformat()
    assert (st['max_speed']['lat'], st['max_speed']['lon']) == (round(pts[3].lat, 6), round(pts[3].lon, 6))
    assert (st['nodata_min'], st['moving_min'], st['stopped_min']) == (10, 1, 1)     # 600 с без данных; 60 с езды
    assert st['avg_kmh'] is None                                                         # езды меньше 5 минут
    tr = Track().park(DEPOT, 5).drive(B)
    st = live.day_stats(ac.clean_track(_fixes(tr.pts)))
    assert st['avg_kmh'] == 36 and st['moving_min'] >= 5 and st['stopped_min'] == 5    # 10 м/с
    empty = live.day_stats([])
    assert empty == {'max_speed': None, 'moving_min': None, 'stopped_min': None, 'nodata_min': None, 'avg_kmh': None}


def test_card_stats_overspeed_and_honest_without_track():
    pts = _speed_track([60, 95, 101, 97, 60, 95, 99, 98, 60])
    card = view(facts(pts, [], [T0]), T0 + timedelta(minutes=5))
    assert card['stats']['overspeed'] == {'count': 2, 'minutes': 1} and card['stats']['max_speed']['kmh'] == 101
    card = view(facts(), T0)
    assert card['stats']['overspeed'] is None and card['stats']['max_speed'] is None
    assert card['stats']['moving_min'] is None and card['route'] is None and card['deviation'] is None


def test_live_deviation_setting():
    from route_optimizer import store as st
    vals = dict(st.DEFAULT_SETTINGS)
    out, errors = st.validate_settings(vals, None)
    assert not errors and out['live_deviation_m'] == 300 and 'deviation' not in out['live_alert_kinds']
    for bad in (99, 2001, None, True, '300'):
        _, errors = st.validate_settings({**vals, 'live_deviation_m': bad}, None)
        assert 'live_deviation_m' in errors, bad
    assert live.Rules.from_settings({**vals, 'live_deviation_m': 500}).deviation_m == 500
    assert live.Rules.from_settings(vals).deviation_m == 300


def test_build_text_deviation():
    from route_optimizer import live_alerts as la
    a = {'kind': 'deviation', 'from': T0.isoformat(), 'to': None, 'active': True, 'km': 1.2, 'lat': A[0], 'lon': A[1]}
    text = la.build_text({'car_code': 'CAR1', 'driver': 'Արամ'}, a, 'start', RULES)
    assert text.splitlines()[0] == 'Շեղում երթուղուց' and '300 մ' in text and '1,2 կմ' in text and 'yandex' in text
    assert 'արդեն' in text
    ended = la.build_text({'car_code': 'CAR1'}, {**a, 'active': False, 'to': (T0 + timedelta(minutes=7)).isoformat()},
                          'start', RULES)
    assert 'արդեն' not in ended and 'շեղվել էր' in ended and '09:00–09:07' in ended


class FakeLineRoads:
    """Дороги для линий карты: линия «по дорогам» — та же ломаная с серединами участков (лежит на прямой); None — не
    построились."""
    failed = False
    version = 'map-1'

    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def leg_lines(self, legs):
        self.calls.append(list(legs))
        if not self.ok:
            return None
        return [([a, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), b], True) for a, b in legs]


class FakeRoadProvider:
    def __init__(self, roads):
        self.roads = roads
        self.zones = []

    def get(self):
        return self.roads

    def bypass(self, base, zone):
        self.zones.append(zone)
        return base


def _send_plan(state, km=None, released=True):
    """Черновик фикстуры live_app — отправлен водителям (№80/№81); km — км машины 1 в прогнозе сборки."""
    from route_optimizer import dispatch as dp
    draft = dp.Draft.from_json(state.store.load_dispatch('2026-10-03')[0])
    draft.released = {'at': '2026-10-03T08:00:00+04:00', 'by': 'qa'} if released else None
    if km is not None:
        draft.prediction['trucks']['CAR1']['km'] = km
    state.store.save_dispatch('2026-10-03', draft.to_json(), 'qa')


def _lines_app(live_app, monkeypatch, ok=True):
    from route_optimizer import views
    state = live_app.app.extensions['route_optimizer']
    roads = FakeLineRoads(ok)
    provider = FakeRoadProvider(roads)
    monkeypatch.setattr(state, 'roads', provider)
    monkeypatch.setattr(state, 'live_lines', views._LivePlanLines())
    from route_optimizer import store as st
    state.store.save(st.Changes(dict(state.store.load().settings), True, DEPOT, (), ()), 'qa')   # склад — у трека FakeLive
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])   # новый факт — без кэша карточек
    return state, roads, provider


def test_api_live_plan_route_by_roads_once_and_prediction_km(client, live_app, monkeypatch):
    state, roads, provider = _lines_app(live_app, monkeypatch)
    _send_plan(state, km=21.4)
    depot = state.store.load().depot
    _session_as(client, 'boss', base=LAN)
    body = client.get('/api/routes/live', base_url=LAN).get_json()
    assert body['thresholds']['deviation_m'] == 300
    car1 = {t['car_code']: t for t in body['trucks']}['CAR1']
    assert car1['plan_sent'] is True and car1['route'] == {'road': True, 'km': 21.4, 'trips': 1, 'stops': 2,
                                                            'straight': 0}
    assert car1['deviation'] == {'threshold_m': 300, 'count': 0, 'km': 0.0, 'active': False, 'alerts': 0, 'minor': 0,
                                 'explained': 0, 'detour_min_km': 1.0, 'off_km': 0.0,
                                 'counted_km': car1['deviation']['counted_km'],
                                 'adherence_pct': car1['deviation']['adherence_pct']}
    assert car1['stats']['max_speed']['kmh'] == 36 and car1['stats']['overspeed'] == {'count': 0, 'minutes': 0}
    truck = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    # линия — склад → A → B → склад, как «Развоз»: по дорогам (здесь — с серединами участков), в объезд малого центра
    line = truck['route']['lines'][0]
    assert line[0] == line[-1] == [round(depot[0], 6), round(depot[1], 6)] and len(line) == 7
    assert [round(x, 6) for x in A] in line and [round(x, 6) for x in B] in line
    assert [(s['customer_id'], s['no']) for s in truck['route']['points']] == [(7, 1), (8, 2)]
    assert [s['plan_no'] for s in truck['stops']] == [1, 2] and truck['deviation']['runs'] == []
    assert provider.zones and len(provider.zones[0]) >= 3            # граница малого центра из настроек
    # CAR9: точки её магазина неизвестны (терминала нет, снимка ERP в памяти нет) — линии нет
    car9 = {t['car_code']: t for t in body['trucks']}['CAR9']
    assert car9['route'] is None and car9['plan_sent'] is True
    # линии — один раз на набор линий и версию карты: следующий пересчёт флота их не строит
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])
    client.get('/api/routes/live', base_url=LAN)
    assert len(roads.calls) == 1


def test_api_live_plan_route_km_from_lines_when_trips_changed_and_straight_fallback(client, live_app, monkeypatch):
    from route_optimizer import dispatch as dp
    state, roads, _ = _lines_app(live_app, monkeypatch)
    _send_plan(state, km=21.4)
    raw = state.store.load_dispatch('2026-10-03')[0]
    draft = dp.Draft.from_json(raw)
    draft.trips[0].stops = [8, 7]          # логист поменял порядок после сборки — км прогноза уже не того плана
    draft.sent = None
    state.store.save_dispatch('2026-10-03', draft.to_json(), 'qa')
    _session_as(client, 'boss', base=LAN)
    truck = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    depot = state.store.load().depot
    want = haversine_km(depot, B) + haversine_km(B, A) + haversine_km(A, depot)
    assert truck['route']['road'] is True and truck['route']['km'] == pytest.approx(want, abs=0.1)
    assert [s['customer_id'] for s in truck['route']['points']] == [8, 7]
    # дороги не построились — по прямой: линия есть, км и отклонения нет; сбой не повторяется на каждом опросе
    state, roads, _ = _lines_app(live_app, monkeypatch, ok=False)
    truck = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    assert truck['route']['road'] is False and truck['route']['km'] is None and truck['deviation'] is None
    assert len(truck['route']['lines'][0]) == 4
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])
    client.get('/api/routes/live/truck?car=CAR1', base_url=LAN)
    assert len(roads.calls) == 1


def test_api_live_no_plan_route_until_sent_or_without_map(client, live_app, monkeypatch):
    state, roads, _ = _lines_app(live_app, monkeypatch)
    _session_as(client, 'boss', base=LAN)
    car1 = {t['car_code']: t for t in client.get('/api/routes/live', base_url=LAN).get_json()['trucks']}['CAR1']
    assert car1['plan_sent'] is False and car1['route'] is None and car1['deviation'] is None and roads.calls == []
    # карты дорог нет — линия по прямой
    monkeypatch.setattr(state, 'roads', None)
    _send_plan(state)
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])
    truck = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']
    assert truck['route']['road'] is False and truck['route']['km'] is None and truck['deviation'] is None


# ============================== по ревью плановой линии (08.10) ==============================

HOME = _off(MID_AB, -4000.0)   # дом водителя — далеко от линии плана


def _done_stops(tr):
    """Обе точки плана закрыты водителем (доставлены)."""
    return [stop('S:A', 7, A, 100.0, 'full', 1.0, tr.t), stop('S:B', 8, B, 100.0, 'full', 1.0, tr.t, seq=2)]


def test_deviation_not_counted_driving_from_home_to_depot():
    """Трек начался дома: дорога дом → склад до первого настоящего выезда (конца стоянки на складе) — не отклонение."""
    tr = Track().park(HOME, 2).drive(DEPOT).park(DEPOT, 10).drive(A).park(A, 3).drive(B).park(B, 3)
    card = _route_view(tr, tr.t, _route())
    assert card['deviation']['count'] == 0
    # тот же путь, но трек начался на складе и машина поехала «через дом» — отклонение
    tr = Track().park(DEPOT, 10).drive(HOME).drive(A).park(A, 3).drive(B).park(B, 3)
    assert _route_view(tr, tr.t, _route())['deviation']['count'] == 1
    # склада в начале нет вовсе (из дома сразу к магазину) — счёт с первого магазина
    tr = Track().park(HOME, 2).drive(A).park(A, 3).drive(B).park(B, 3)
    assert _route_view(tr, tr.t, _route())['deviation']['count'] == 0


def test_deviation_not_counted_driving_home_after_last_trip_returned():
    """Все точки закрыты, машина вернулась на склад — дорога домой без закрытия дня не отклонение; точка ещё открыта —
    отклонение (рейс не кончился)."""
    tr = Track().park(DEPOT, 5).drive(A).park(A, 3).drive(B).park(B, 3).drive(DEPOT).park(DEPOT, 5).drive(HOME)
    card = live.car_view(DAY, tr.t, facts(tr.pts, _done_stops(tr), [T0, tr.t]), [], TRUCK, DEPOT, RULES, ROAD, True,
                         None, _route())
    assert card['deviation']['count'] == 0 and card['deviation']['active'] is False
    open_b = [stop('S:A', 7, A, 100.0, 'full', 1.0, tr.t), stop('S:B', 8, B, 100.0, 'pending', seq=2)]
    open_b[1]['lat'], open_b[1]['lon'] = _off(B, 5000.0)    # магазин B ещё не посещён (его точка — в стороне)
    card = live.car_view(DAY, tr.t, facts(tr.pts, open_b, [T0, tr.t]), [], TRUCK, DEPOT, RULES, ROAD, True, None, _route())
    assert card['deviation']['count'] == 1


def test_deviation_broken_by_track_gap():
    """Перерыв трека дольше STATS_GAP со сменой места прерывает отклонение: что было между точками — неизвестно."""
    side = _off(MID_AB, 1500.0)
    index = live.RouteIndex(PLAN_LINE, 300.0)
    a = [Fix(T0 + timedelta(seconds=30 * i), side[0], side[1] + 0.001 * i, 5.0) for i in range(3)]       # ~0,17 км
    b = [Fix(T0 + timedelta(minutes=20, seconds=30 * i), side[0], side[1] + 0.003 + 0.001 * i, 5.0) for i in range(3)]
    assert live.deviation_runs(a + b, index, 300.0, [], T0, None) == []   # вместе 0,5 км, но через 20 мин без данных
    stay = [Fix(T0 + timedelta(minutes=3), *a[-1].point, 1.0), Fix(T0 + timedelta(minutes=30), *a[-1].point, 1.0)]
    more = [Fix(T0 + timedelta(minutes=30, seconds=30 * i), side[0], side[1] + 0.002 + 0.001 * i, 5.0) for i in range(1, 5)]
    assert len(live.deviation_runs(a + stay + more, index, 300.0, [], T0, None)) == 1   # стоянка — не перерыв


def test_straight_leg_is_not_judged():
    """Участок плана без дороги (по прямой) — вдоль него отклонение не считается; тот же объезд у участка по дорогам —
    отклонение. Без единого участка по дорогам — не считается вовсе."""
    tr = _detour_track(1200.0)
    parts = ((DEPOT, A), (B, DEPOT))
    card = _route_view(tr, tr.t, _route(parts=parts, straight=((A, B),)))
    assert card['deviation']['count'] == 0 and card['route']['straight'] == 1 and card['route']['road'] is True
    assert _route_view(tr, tr.t, _route())['deviation']['count'] == 1
    far = Track().park(DEPOT, 5).drive(A).park(A, 3).drive(_off(MID_AB, 5000.0)).drive(B).park(B, 3)
    assert _route_view(far, far.t, _route(parts=parts, straight=((A, B),)))['deviation']['count'] == 1   # не «по пути»
    assert live.RouteGeometry(PLAN_LINE, (), ((A, B),)).road is False


def test_plan_geometry_marks_long_straight_leg_inside_road_line():
    """roads.draw рисует участок без пути двумя точками (магазин дальше SNAP_MAX_KM от дороги): он — по прямой, остальные —
    по дорогам; км линии — неизвестны (есть участок по прямой)."""
    from route_optimizer import views
    far = (40.30, 44.70)

    class Roads:   # путь (A → far) не найден; склад → A — найден, прямая дорога (две точки после упрощения)
        def leg_lines(self, legs):
            return [([a, b], (a, b) != (A, far)) if (a, b) in ((A, far), (DEPOT, A))
                    else ([a, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.001), b], True) for a, b in legs]
    key = (((DEPOT, A, far, DEPOT),), (), 'v')
    geo = views._plan_geometry(Roads(), {'CAR1': key})['CAR1']
    assert geo.straight == ((A, far),) and len(geo.road_parts) == 2 and geo.road and geo.km is None
    assert geo.road_parts[0] == (DEPOT, A)                             # прямая дорога — по дорогам, не «ճանապարհ չգտնվեց»
    whole = views._plan_geometry(Roads(), {'CAR1': (((DEPOT, A, DEPOT),), (), 'v')})['CAR1']
    assert whole.straight == () and whole.km is not None
    assert geo.trips[0][0] == DEPOT and geo.trips[0][-1] == DEPOT and A in geo.trips[0] and far in geo.trips[0]
    assert not geo.index(300.0).near(_off(((A[0] + far[0]) / 2, (A[1] + far[1]) / 2), 0.0), 300.0)   # прямой нет в индексе

    class Broken:
        def leg_lines(self, legs):
            return None
    assert views._plan_geometry(Broken(), {'CAR1': key}) == {'CAR1': None}


def test_route_geometry_index_and_map_lines_built_once(monkeypatch):
    geo = live.RouteGeometry(PLAN_LINE, PLAN_LINE)
    calls = []
    from route_optimizer import track_line as tl
    real = tl._fit   # упрощение линий для карты — с обязательными концами участков (shown_cuts)
    monkeypatch.setattr(tl, '_fit', lambda *a, **k: calls.append(1) or real(*a, **k))
    assert geo.index(300.0) is geo.index(300.0) and geo.index(100.0) is not geo.index(300.0)
    assert geo.shown() is geo.shown() and geo.shown_cuts() is geo.shown_cuts() and len(calls) == 1
    tr = _detour_track(1500.0)
    route = live.PlanRoute(geo, ((7, A), (8, B)))
    first = _route_view(tr, tr.t, route)
    built = dict(geo._index)
    _route_view(tr, tr.t, route)
    assert geo._index == built and first['route']['lines'] is geo.shown()


def _plan_cache(monkeypatch, background):
    from route_optimizer import views
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', background)
    return views._LivePlanLines()


def test_plan_lines_cache_per_truck_key_and_failures(monkeypatch):
    cache = _plan_cache(monkeypatch, False)
    asked = []

    def build(todo):
        asked.append(dict(todo))
        return {car: (None if key == 'bad' else live.RouteGeometry(PLAN_LINE, PLAN_LINE)) for car, key in todo.items()}
    got = cache.get(DAY, {'CAR1': 'k1', 'CAR2': 'k2'}, build)
    assert set(got) == {'CAR1', 'CAR2'} and asked == [{'CAR1': 'k1', 'CAR2': 'k2'}]
    old = got['CAR1']
    got = cache.get(DAY, {'CAR1': 'k1b', 'CAR2': 'k2'}, build)     # сменился план одной машины — строится только она
    assert asked[-1] == {'CAR1': 'k1b'} and got['CAR1'] is not old and got['CAR2'] is not None
    got = cache.get(DAY, {'CAR1': 'bad'}, build)                    # не построились — по прямой (нет в ответе)
    assert 'CAR1' not in got
    n = len(asked)
    cache.get(DAY, {'CAR1': 'bad'}, build)                          # сбой не повторяется на каждом опросе
    assert len(asked) == n
    boom = cache.get(DAY, {'CAR3': 'x'}, lambda todo: 1 / 0)       # исключение сборки не роняет карту
    assert boom == {}


def test_plan_lines_cache_serves_previous_while_rebuilding_and_one_build_at_a_time(monkeypatch):
    cache = _plan_cache(monkeypatch, False)
    first = live.RouteGeometry(PLAN_LINE, PLAN_LINE)
    cache.get(DAY, {'CAR1': 'k1'}, lambda todo: {'CAR1': first})
    from route_optimizer import views
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', True)
    release, started = threading.Event(), []
    second = live.RouteGeometry(PLAN_LINE, PLAN_LINE)

    def slow(todo):
        started.append(dict(todo))
        release.wait(5)
        return {car: second for car in todo}
    assert cache.get(DAY, {'CAR1': 'k2'}, slow)['CAR1'] is first    # строится новая — прежняя линия, не «по прямой»
    assert cache.get(DAY, {'CAR1': 'k2', 'CAR2': 'z'}, slow)['CAR1'] is first
    assert len(started) <= 1                                         # вторая сборка не запускается, пока идёт первая
    release.set()
    for _ in range(200):
        got = cache.get(DAY, {'CAR1': 'k2'}, slow)
        if got.get('CAR1') is second:
            break
        time.sleep(0.02)
    assert got['CAR1'] is second and len(started) == 1
    # другой день — прежних линий нет; память — не больше LIVE_LINES_DAYS дней
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', False)
    for k in range(views.LIVE_LINES_DAYS + 2):
        cache.get(DAY + timedelta(days=k + 1), {'CAR1': 'k'}, lambda todo: {'CAR1': first})
    assert len({d for d, _ in cache._slots}) == views.LIVE_LINES_DAYS


# ---- ревью 08.10: все заезды, а не только обслуживающий; рейс точки; без связи — не «на месте» ----

AB = [live.PlanTrip((1, 2), {})]


def _ab():
    return [stop('S:A', 1, A, 600.0, seq=1), stop('S:B', 2, B, 400.0, seq=2)]


def test_revisit_store_is_here_and_unload_counts_from_this_visit():
    """Постоял у A, уехал, вернулся и стоит у A сейчас: A «на месте» (не «посещён и не отмечен»), остаток разгрузки — от
    прибытия этого заезда."""
    tr = Track().park(DEPOT, 10).drive(A).park(A, 5).drive(_mid(A, B)).drive(A).park(A, 6)
    card = view(facts(tr.pts, _ab(), [T0, tr.t], NEW_APK), tr.t, AB, detail=True)
    st = {x['stop_id']: x for x in card['stops']}
    g = st['S:A']['gps']
    assert g['here'] is True and g['leave'] is None and st['S:A']['unmarked'] is False
    assert card['next']['stop_id'] == 'S:A' and card['next']['here'] is True and card['stores']['unmarked'] == 0
    stayed = (tr.t - datetime.fromisoformat(g['arrive'])).total_seconds() / 60
    want = tr.t + timedelta(minutes=max(0.0, 8 + 6 * 0.6 - stayed) + ROAD.minutes(A, B))
    assert abs((datetime.fromisoformat(st['S:B']['eta']) - want).total_seconds()) < 1.5


def test_jitter_split_stay_still_ongoing_is_not_unmarked():
    """Дыра в треке дольше JITTER_BREAK рвёт стоянку у A на два заезда — второй идёт сейчас: A на месте."""
    tr = Track().park(DEPOT, 10).drive(A).park(A, 4)
    t = tr.t + timedelta(seconds=30)
    tr.pts.append((ms(t), A[0] + 0.0003, A[1], 8.0, 3.0, 90.0))
    tr.t, tr.pos = t + timedelta(seconds=200), (A[0] + 0.0006, A[1])
    tr.park(tr.pos, 6)
    card = view(facts(tr.pts, _ab(), [T0, tr.t], NEW_APK), tr.t, AB, detail=True)
    a = card['stops'][0]
    assert a['unmarked'] is False and a['gps']['here'] is True and a['gps']['leave'] is None
    assert card['next']['stop_id'] == 'S:A'


def test_two_stores_one_site_not_unmarked_while_truck_is_there():
    """Два магазина в 60 м (одно место): стоял у A, переставил машину к A2 и стоит — A не «посещён и не отмечен», пока
    машина в STOP_RADIUS_M; уехал — оба не отмечены, следующий — B."""
    a2 = (A[0], A[1] + 0.0007)
    stops = [stop('S:A', 1, A, 300.0, seq=1), stop('S:A2', 3, a2, 200.0, seq=2), stop('S:B', 2, B, 400.0, seq=3)]
    plan = [live.PlanTrip((1, 3, 2), {})]
    tr = Track().park(DEPOT, 10).drive(A).park(A, 6).drive(a2).park(a2, 6)
    card = view(facts(tr.pts, stops, [T0, tr.t], NEW_APK), tr.t, plan, detail=True)
    st = {x['stop_id']: x for x in card['stops']}
    assert card['stores']['unmarked'] == 0 and st['S:A2']['gps']['here'] is True
    assert st['S:A']['gps']['here'] is False and st['S:A']['gps']['leave'] is not None
    tr.drive(_mid(a2, B))
    card = view(facts(tr.pts, stops, [T0, tr.t], NEW_APK), tr.t, plan)
    assert card['stores']['unmarked'] == 2 and card['next']['stop_id'] == 'S:B'


def test_still_inside_radius_after_stay_is_not_unmarked():
    """Стоянка у A кончилась, машина отъезжает, но ещё в STOP_RADIUS_M — не «уехал, не отметив», а «на месте»."""
    tr = Track().park(DEPOT, 10).drive(A).park(A, 6).drive((A[0], A[1] + 0.0008))   # ~68 м
    card = view(facts(tr.pts, _ab(), [T0, tr.t], NEW_APK), tr.t, AB, detail=True)
    assert card['stops'][0]['unmarked'] is False and card['stores']['unmarked'] == 0
    assert card['stops'][0]['gps']['here'] is True and card['stops'][0]['gps']['leave'] is None
    assert card['next']['stop_id'] == 'S:A' and card['next']['here'] is True


def test_stop_near_later_trip_store_or_short_stop_is_not_a_visit():
    """Рейс 1 — A, B; рейс 2 — C. По пути к A машина простояла 8 мин у C (пробка, обед) и 3 мин у B: это не посещения —
    рейс 2 не уехал, C и B не «посещены, не отмечены», груз по плану — рейс 2 (300 кг) ещё впереди."""
    stops = _ab() + [stop('S:C', 3, C, 300.0, seq=3)]
    plan = [live.PlanTrip((1, 2), {}), live.PlanTrip((3,), {})]
    tr = Track().park(DEPOT, 10).drive(C).park(C, 8).drive(B).park(B, 3).drive(_mid(B, A))
    card = view(facts(tr.pts, stops, [T0, tr.t], NEW_APK), tr.t, plan, detail=True)
    st = {x['stop_id']: x for x in card['stops']}
    assert st['S:C']['gps'] is None and st['S:B']['gps'] is None and card['stores']['unmarked'] == 0
    assert card['load']['trips_gone'] == 1 and card['load']['planned_kg'] == 300 and card['next']['stop_id'] == 'S:A'


def test_visit_before_mid_trip_depot_return_still_counts():
    """Был у A (не отметил), заехал на склад посреди рейса и поехал к B — визит у A засчитан."""
    tr = Track().park(DEPOT, 10).drive(A).park(A, 8).drive(DEPOT).park(DEPOT, 5).drive(_mid(DEPOT, B))
    card = view(facts(tr.pts, _ab(), [T0, tr.t], NEW_APK), tr.t, AB, detail=True)
    assert card['stops'][0]['unmarked'] is True and card['next']['stop_id'] == 'S:B'


def test_offline_truck_at_store_is_neither_here_nor_unmarked():
    tr = Track().park(DEPOT, 10).drive(A).park(A, 6)
    card = view(facts(tr.pts, _ab(), [T0, tr.t], NEW_APK), tr.t + timedelta(minutes=90), AB, detail=True)
    g = card['stops'][0]['gps']
    assert card['state'] == 'offline' and card['forecast'] is False
    assert g['here'] is False and g['leave'] is None and card['stops'][0]['unmarked'] is False
    assert card['next']['stop_id'] == 'S:A' and card['next']['here'] is False and card['next']['eta_unknown'] is True


def test_next_store_named_without_gps_fix_and_no_forecast_when_gps_off():
    card = view(facts([], _ab(), [T0], NEW_APK), T0 + timedelta(minutes=1), AB)   # связь есть, точки GPS ещё нет
    assert card['next']['stop_id'] == 'S:A' and card['next']['eta_unknown'] is True and card['next']['eta'] is None
    tr, stops, plan = _to_a()
    off = {**NEW_APK, 'gps': 'off'}
    card = view(facts(tr.pts, stops, [T0, tr.t], off, [(tr.t, 'off')]), tr.t + timedelta(minutes=1), plan, detail=True)
    assert card['forecast'] is False and card['next']['eta_unknown'] is True and card['return_eta'] is None
    assert all(x['eta'] is None for x in card['stops']) and card['late'] == []
    card = view(facts(tr.pts, stops, [T0, tr.t], {**NEW_APK, 'gps': 'no_permission'}), tr.t + timedelta(minutes=1), plan)
    assert card['forecast'] is False


def test_short_marked_delivery_keeps_gps_visit():
    """Доставка отмечена после стоянки короче GPS_VISIT_MIN — визит закрытой точки показывается как есть (CT115, 333DN33)."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 3).drive(_mid(A, B))
    stops = [stop('S:A', 1, A, 600.0, 'full', 1.0, at_a + timedelta(minutes=2), seq=1), stop('S:B', 2, B, 400.0, seq=2)]
    card = view(facts(tr.pts, stops, [T0, tr.t], NEW_APK), tr.t, AB, detail=True)
    g = card['stops'][0]['gps']
    assert g is not None and g['here'] is False and g['leave'] is not None and g['minutes'] < live.GPS_VISIT_MIN
    assert card['stores']['gps_visited'] == 1 and card['stores']['unmarked'] == 0


def test_stay_cut_by_speed_jitter_truck_still_there_is_here_in_card_and_table():
    """Стоянку у A оборвал всплеск скорости, машина стоит в STOP_RADIUS_M (CT115, 333DN33 12:48): карточка и таблица
    согласны — «на месте», отъезда нет, минуты — от прибытия."""
    tr = Track().park(DEPOT, 10).drive(A).park(A, 8)
    t = tr.t + timedelta(seconds=20)
    tr.pts.append((ms(t), A[0] + 0.0004, A[1], 8.0, 3.0, 90.0))   # мигнула скорость: стоянка кончилась
    tr.t, tr.pos = t + timedelta(seconds=40), (A[0] + 0.0007, A[1])   # ~78 м: дальше REPOSITION_M, в STOP_RADIUS_M
    tr.park(tr.pos, 1)
    card = view(facts(tr.pts, _ab(), [T0, tr.t], NEW_APK), tr.t, AB, detail=True)
    g = card['stops'][0]['gps']
    assert card['next']['stop_id'] == 'S:A' and card['next']['here'] is True
    assert g['here'] is True and g['leave'] is None and card['stops'][0]['unmarked'] is False
    assert g['minutes'] == round((tr.t - datetime.fromisoformat(g['arrive'])).total_seconds() / 60)


def test_plan_lines_cache_keeps_recently_opened_old_day(monkeypatch):
    """Прошлый день, открытый после трёх более поздних, не вытесняется сразу после постройки (иначе — пересборка на
    каждом пересчёте и линии «по прямой» в истории): уходит тот день, который дольше всего не спрашивали."""
    from route_optimizer import views
    cache = _plan_cache(monkeypatch, False)
    clock = [100.0]
    monkeypatch.setattr(views, '_monotonic', lambda: clock[0])
    geo = live.RouteGeometry(PLAN_LINE, PLAN_LINE)
    built = []

    def build(todo):
        built.append(1)
        return {car: geo for car in todo}
    days = [DAY + timedelta(days=k) for k in (3, 2, 1)]
    for d in days:
        clock[0] += 1
        cache.get(d, {'CAR1': 'k'}, build)
    old = DAY - timedelta(days=4)
    clock[0] += 1
    assert cache.get(old, {'CAR1': 'k'}, build) == {'CAR1': geo}
    n = len(built)
    for _ in range(3):
        clock[0] += 1
        assert cache.get(old, {'CAR1': 'k'}, build) == {'CAR1': geo}   # уже построен — без пересборки
    assert len(built) == n
    kept = {d for d, _ in cache._slots}
    assert old in kept and days[0] not in kept and len(kept) == views.LIVE_LINES_DAYS   # ушёл давно не открытый
    clock[0] += 1
    cache.get(days[1], {'CAR1': 'k'}, build)                           # спросили — «свежий», не уходит
    clock[0] += 1
    cache.get(days[0], {'CAR1': 'k'}, build)                           # пересобран; уходит самый давний — days[2]
    assert {d for d, _ in cache._slots} == {old, days[1], days[0]}


# ============================== подсказка точки линии трека (владелец 08.10: «при наведении на линию…») ==============================

def _stay_idx(card):
    """Индексы вершин стоянок: середина стоянки в линии — дважды подряд (track_line)."""
    tr = card['track']
    return {k for i in range(len(tr) - 1) if tr[i] == tr[i + 1] for k in (i, i + 1)}


def test_track_hover_speed_km_one_to_one_and_stays_zero():
    """track_v / track_km — 1:1 с track: стоянка (точка дважды) — 0 км/ч, езда — скорость терминала; км дня не убывают,
    у первой вершины 0, у последней — км карточки; плановой линии нет — track_dev_m None."""
    tr = Track().park(DEPOT, 5).drive(A, speed_ms=10.0).park(A, 6).drive(B, speed_ms=15.0)
    card = view(facts(tr.pts, [], [T0, tr.t]), tr.t, detail=True)
    n = len(card['track'])
    assert n > 4 and len(card['track_v']) == len(card['track_km']) == len(card['track_t']) == n
    stays = _stay_idx(card)
    assert len(stays) == 4 and all(card['track_v'][i] == 0 for i in stays)
    a_leave = max(t for i, t in enumerate(card['track_t']) if i in stays)
    moving = [(t, v) for i, (t, v) in enumerate(zip(card['track_t'], card['track_v'])) if i not in stays]
    assert moving and all(v == (36 if t < a_leave else 54) for t, v in moving)
    km = card['track_km']
    assert km == sorted(km) and km[0] == 0.0 and km[-1] == card['km'] > 0
    assert card['track_dev_m'] is None
    empty = view(facts(), T0, detail=True)
    assert empty['track_v'] == [] and empty['track_km'] == [] and empty['track_dev_m'] is None


def test_track_hover_snapped_vertices_take_nearest_fix_or_step_speed():
    """Вершины линии по дорогам — с интерполированными моментами (не точки GPS): скорость — ближайшей по времени точки
    терминала; терминал скорость не шлёт — по смещению соседних точек; км — по времени между сегментами км дня."""
    tr = Track().park(DEPOT, 3).drive(A, speed_ms=12.0).park(A, 4)

    def snap(parts):   # «дорога»: между соседними точками куска — ещё вершина посередине (момент — середина)
        out = {}
        for c in parts:
            if c.stay:
                continue
            raw = c.raw()
            line = [raw[0]]
            for p, q in zip(raw, raw[1:]):
                line += [((p[0] + q[0]) / 2 + 0.0001, (p[1] + q[1]) / 2, (p[2] + q[2]) / 2), q]
            out[c.key] = line
        return out
    card = live.car_view(DAY, tr.t, facts(tr.pts, [], [T0, tr.t]), [], TRUCK, DEPOT, RULES, ROAD, True, None, None, snap)
    stays = _stay_idx(card)
    # кусок езды начинается в середине стоянки (track_line 08.10): вершина сразу после неё — посередине между точкой «стоит»
    # и первой точкой езды, скорость там любая из двух
    driving = [i for i in range(len(card['track'])) if i not in stays and i - 1 not in stays]
    assert len(driving) > 10 and all(card['track_v'][i] == round(12.0 * 3.6) for i in driving)
    assert card['track_km'] == sorted(card['track_km']) and card['track_km'][-1] == card['km']
    no_spd = [p[:4] + (None,) + p[5:] for p in tr.pts]   # старый терминал: скорости нет
    bare = live.car_view(DAY, tr.t, facts(no_spd, [], [T0, tr.t]), [], TRUCK, DEPOT, RULES, ROAD, True, None, None, snap)
    inner = [v for i, v in enumerate(bare['track_v']) if i not in _stay_idx(bare)][1:-1]
    assert inner and all(v is not None and abs(v - 43) <= 2 for v in inner)


def test_track_hover_no_data_in_gap_is_none():
    """Вершина в перерыве трека (нет точки ближе HOVER_SPEED_S, соседние дальше HOVER_STEP_S) — скорость None, а не 0;
    км до первого сегмента — 0, после последнего — итог."""
    tr = Track()
    tr.pos = DEPOT
    tr.drive(A, speed_ms=10.0)
    pts = ac.clean_track(live.track_fixes(tr.pts))
    t0, t1 = pts[0].at.timestamp(), pts[-1].at.timestamp()
    line = [(A[0], A[1], t0 - 600), (A[0], A[1], t0 + 30), (A[0], A[1], t1 + 600)]
    got = live.track_hover(line, [], pts, pts, None, [])
    assert got['track_v'] == [None, 36, None] and got['track_dev_m'] is None
    km = sum(k for _, k in track_steps(pts))
    assert got['track_km'][0] == 0.0 and got['track_km'][2] == round(km, 1) and 0 < got['track_km'][1] < got['track_km'][2]
    bare = [replace(p, spd=None) for p in pts]   # скорости нет — по смещению соседних точек
    v = live.track_hover(line, [], bare, bare, None, [])['track_v']
    assert v[0] is None and v[2] is None and abs(v[1] - 36) <= 1


def test_route_index_distance_matches_brute_force_and_limit():
    lines = [[DEPOT, A, B, DEPOT], [C, _off(C, 2000.0)]]
    index = live.RouteIndex(lines, 300.0)
    for p in (MID_AB, _off(MID_AB, 120.0), _off(MID_AB, 1500.0), _off(C, -700.0), (40.20, 44.40), A):
        want = min(live.polyline_m(p, line) for line in lines)
        assert index.distance(p, 10000.0) == pytest.approx(want, rel=0.003, abs=0.5)
    assert index.distance((40.20, 44.40), 5000.0) is None   # 6,7 км — дальше предела
    assert index.distance((40.40, 44.90), 5000.0) is None and live.RouteIndex([], 300.0).distance(A, 5000.0) is None


def test_track_hover_distance_to_plan_only_inside_deviation():
    """track_dev_m — [индекс, м] только у вершин внутри отклонения (по моменту), метры — до участков плана по дорогам;
    отклонений нет — пусто; линии по дорогам нет — None."""
    tr = _detour_track(1500.0)
    card = _route_view(tr, tr.t, _route())
    run = card['deviation']['runs'][0]
    lo, hi = datetime.fromisoformat(run['from']).timestamp(), datetime.fromisoformat(run['to']).timestamp()
    dev = card['track_dev_m']
    assert dev and [i for i, _ in dev] == [i for i, t in enumerate(card['track_t']) if lo <= t <= hi]
    for i, m in dev:
        p = tuple(card['track'][i])
        assert m == pytest.approx(min(live.polyline_m(p, x) for x in PLAN_LINE), abs=2)
    side = _off(MID_AB, 1500.0)                                   # дальняя точка объезда — в линии трека
    assert max(m for _, m in dev) == pytest.approx(min(live.polyline_m(side, x) for x in PLAN_LINE), abs=2)
    assert _route_view(tr, tr.t, _route(road=False))['track_dev_m'] is None
    calm = Track().park(DEPOT, 5).drive(A).park(A, 3).drive(B).park(B, 3)
    assert _route_view(calm, calm.t, _route())['track_dev_m'] == []


def test_route_trip_nos_for_leg_hover():
    """Номера магазинов каждой линии рейса (подсказка «Երթ N · A → B»): по trip_stops; не сходится с линиями — пусто."""
    tr = Track().park(DEPOT, 5).drive(A)
    lines = ((DEPOT, A, DEPOT), (DEPOT, B, DEPOT))
    two = live.PlanRoute(live.RouteGeometry(lines, lines), ((7, A), (8, B)), None, ((7,), (8,)))
    assert _route_view(tr, tr.t, two)['route']['trip_nos'] == [[1], [2]]
    assert _route_view(tr, tr.t, _route())['route']['trip_nos'] == []          # trip_stops неизвестны


def test_api_live_truck_hover_arrays_and_radii(client, live_app, monkeypatch):
    state, _, _ = _lines_app(live_app, monkeypatch)
    _send_plan(state, km=21.4)
    _session_as(client, 'boss', base=LAN)
    body = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()
    truck = body['truck']
    assert body['thresholds']['stop_radius_m'] == ac.STOP_RADIUS_M and body['thresholds']['depot_radius_m'] == ac.DEPOT_RADIUS_M
    assert len(truck['track_v']) == len(truck['track_km']) == len(truck['track']) > 1
    assert truck['route']['trip_nos'] == [[1, 2]] and truck['track_dev_m'] == []
    fleet = client.get('/api/routes/live', base_url=LAN).get_json()['trucks']
    assert all('track_v' not in t and 'track_km' not in t for t in fleet)      # флот — без трека и подсказки


class _FixedLines:
    """Кэш плановых линий, который отдаёт заданные линии (как прежние, пока новые строятся)."""

    def __init__(self, geo):
        self.geo = geo

    def get(self, day, wanted, build):
        return {car: self.geo for car in wanted}


def test_api_live_stale_plan_lines_have_no_leg_stores(client, live_app, monkeypatch):
    """План поменяли, линии по дорогам ещё строятся — отдаются прежние (другого порядка магазинов): участки «A → B» по
    новому плану к ним не приписываются (trip_nos пусто, подсказка — только «Երթ N»); линии того же плана — с ними."""
    from route_optimizer import dispatch as dp
    from route_optimizer import views
    state, roads, _ = _lines_app(live_app, monkeypatch)
    _send_plan(state, km=21.4)
    old = views._plan_geometry(roads, {'CAR1': (((DEPOT, A, B, DEPOT),), (), 'map-1')})['CAR1']
    assert old.source == ((DEPOT, A, B, DEPOT),)
    draft = dp.Draft.from_json(state.store.load_dispatch('2026-10-03')[0])
    draft.trips[0].stops = [8, 7]                      # логист поменял порядок: новые линии — B, затем A
    draft.sent = None
    state.store.save_dispatch('2026-10-03', draft.to_json(), 'qa')
    monkeypatch.setattr(state, 'live_lines', _FixedLines(old))
    _session_as(client, 'boss', base=LAN)
    route = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']['route']
    assert route['road'] is True and route['trip_nos'] == [] and len(route['trip_cuts']) == len(route['lines']) == 1
    fresh = views._plan_geometry(roads, {'CAR1': (((DEPOT, B, A, DEPOT),), (), 'map-1')})['CAR1']
    monkeypatch.setattr(state, 'live_lines', _FixedLines(fresh))
    state.live_facts.data['2026-10-03'] = dict(state.live_facts.data['2026-10-03'])   # без кэша карточек
    route = client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['truck']['route']
    assert route['trip_nos'] == [[1, 2]] and [p['customer_id'] for p in route['points']] == [8, 7]


class _DenseRoads(FakeLineRoads):
    """Участок «по дорогам» — 60 точек на прямой: упрощение для карты оставило бы от рейса два конца."""

    def leg_lines(self, legs):
        return [([(a[0] + (b[0] - a[0]) * k / 59, a[1] + (b[1] - a[1]) * k / 59) for k in range(60)], True)
                for a, b in legs]


def test_plan_geometry_cuts_survive_simplification(monkeypatch):
    """Концы участков (склад, магазины, склад) — обязательные точки линии для карты: trip_cuts указывает ровно на них,
    даже когда магазин лежит на прямой между соседями и упрощение его бы выбросило."""
    from route_optimizer import views
    monkeypatch.setattr(live, 'PLAN_LINE_POINTS', 10)
    mid = ((A[0] + B[0]) / 2, (A[1] + B[1]) / 2)              # магазин на прямой A–B
    lines = ((DEPOT, A, mid, B, DEPOT), (DEPOT, C, DEPOT))
    geo = views._plan_geometry(_DenseRoads(), {'CAR1': (lines, (), 'map-1')})['CAR1']
    assert geo.source == lines and [len(c) for c in geo.cuts] == [5, 3] and len(geo.trips[0]) == 4 * 59 + 1
    shown, cuts = geo.shown(), geo.shown_cuts()
    assert len(shown) == len(cuts) == 2 and sum(len(x) for x in shown) <= 10 + 2 * 5
    for line, cut, want in zip(shown, cuts, lines):
        assert cut == sorted(cut) and [line[i] for i in cut] == [[round(p[0], 6), round(p[1], 6)] for p in want]
    straight = live.RouteGeometry(lines, (), (), lines)       # по прямой — каждая точка конец участка
    assert straight.shown_cuts() == [[0, 1, 2, 3, 4], [0, 1, 2]]


def test_track_hover_distance_index_cell_not_below_500(monkeypatch):
    """Порог отклонения 100 м: расстояние до плана для подсказки — по своему индексу с ячейкой 500 м (поиск до 5 км не
    перебирает тысячи ячеек на точку); отклонение — по индексу порога."""
    tr = _detour_track(1500.0)
    route = _route()
    rules = replace(RULES, deviation_m=100.0)
    card = _route_view(tr, tr.t, route, rules)
    assert set(route.geo._index) == {100.0, live.HOVER_DEV_CELL_M} and card['track_dev_m']
    for i, m in card['track_dev_m']:
        assert m == pytest.approx(min(live.polyline_m(tuple(card['track'][i]), x) for x in PLAN_LINE), abs=2)


def test_track_gaps_only_without_fixes_inside():
    """track_gaps — участки линии дольше GAP_S без единой точки трека внутри («տվյալ չկա»); машина стояла 4 мин в пробке
    (точки есть, в линию не попали) — не перерыв, хотя участок линии тоже дольше GAP_S."""
    jam = Track().park(DEPOT, 5).drive(A)
    jam.park(A, 4)                                   # стоит в пробке: точка раз в минуту, скорость 0 (не стоянка дня)
    jam.drive(B)
    card = view(facts(jam.pts, [], [T0, jam.t]), jam.t, detail=True)
    tt = card['track_t']
    assert any(b - a > live.GAP_S for a, b in zip(tt, tt[1:])) and card['track_gaps'] == []
    gap = Track().park(DEPOT, 5).drive(A)
    gap.t += timedelta(minutes=10)                   # 10 минут без данных
    gap.drive(B)
    card = view(facts(gap.pts, [], [T0, gap.t]), gap.t, detail=True)
    tt = card['track_t']
    assert card['track_gaps'] and all(tt[i + 1] - tt[i] > live.GAP_S for i in card['track_gaps'])
    assert any(tt[i + 1] - tt[i] >= 600 for i in card['track_gaps'])
    assert view(facts(), T0, detail=True)['track_gaps'] == []


def test_plan_cuts_keep_order_and_repeats():
    """Концы участков — по порядку и с повторами: два магазина в одной точке не выбрасывают подсказку участков рейса
    (число концов = магазины + 2 склада)."""
    from route_optimizer import views
    lines = ((DEPOT, A, A, B, DEPOT),)               # два магазина в одной точке
    geo = views._plan_geometry(FakeLineRoads(), {'CAR1': (lines, (), 'map-1')})['CAR1']
    cuts = geo.shown_cuts()[0]
    assert len(cuts) == 5 and [geo.shown()[0][i] for i in cuts] == [[round(p[0], 6), round(p[1], 6)] for p in lines[0]]
    rep = live.RouteGeometry(((DEPOT, A, B, DEPOT),), (), (), cuts=((0, 1, 1, 2, 3),))   # конец участка дважды
    assert rep.shown_cuts() == [[0, 1, 1, 2, 3]]

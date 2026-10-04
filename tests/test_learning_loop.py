# -*- coding: utf-8 -*-
"""Обучение «Развоза» по факту машин (docs/plans/learning-loop-plan.md, этапы 4–5): восстановление факта по треку,
правила принятия норм, применение в расчёте, журнал в route_optimizer.db, страница «Обучение и факт».

Только синтетические треки (настоящего трека APK пока нет); ERP не читается; базы — временные.
Запуск из корня проекта:  python -m pytest tests/test_learning_loop.py -q
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

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import evaluate as evm  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import store as rst  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.geo import Fix, haversine_km  # noqa: E402
from route_optimizer.traffic_validation import TrafficProfile  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401

TZ = ac.YEREVAN
DEPOT = (40.19462, 44.6004)
A = (40.2100, 44.5600)
B = (40.2000, 44.5300)
C = (40.1800, 44.5200)
X = (40.2300, 44.5900)          # не точка плана — обед/заправка


# ============================== синтетический трек ==============================

class Track:
    """Трек машины: езда по прямой с заданной скоростью (точка раз в 15 с) и стоянки (раз в 60 с)."""

    def __init__(self, start, at, acc=8.0, seed=1):
        self.pos, self.t, self.acc = start, at, acc
        self.fixes = [Fix(at, *start, acc)]
        self.rnd = random.Random(seed)

    def drive(self, to, kmh, step=15, gap=None):
        """gap — (с, по) секунды от начала участка без точек (тоннель)."""
        secs = haversine_km(self.pos, to) / kmh * 3600
        n = max(1, int(secs // step))
        a = self.pos
        for i in range(1, n + 1):
            k = i / n
            dt = secs * k
            if gap and gap[0] <= dt < gap[1]:
                continue
            self.fixes.append(Fix(self.t + timedelta(seconds=dt), a[0] + (to[0] - a[0]) * k, a[1] + (to[1] - a[1]) * k,
                                  self.acc))
        self.t += timedelta(seconds=secs)
        self.pos = to
        return self

    def stay(self, minutes, step=60, jitter_m=0.0, spike=False, gap=None):
        n = int(minutes * 60 // step)
        for i in range(1, n + 1):
            if gap and gap[0] <= i * step < gap[1]:
                continue
            j = jitter_m / 111195
            p = (self.pos[0] + self.rnd.uniform(-j, j), self.pos[1] + self.rnd.uniform(-j, j))
            if spike and i == n // 2:
                p = (self.pos[0] + 400 / 111195, self.pos[1])            # одиночный скачок GPS на 400 м
            self.fixes.append(Fix(self.t + timedelta(seconds=i * step), *p, self.acc))
        self.t += timedelta(seconds=minutes * 60)
        return self


def _t(h, m=0, d=date(2026, 9, 29)):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def _stops(**kw):
    base = {'A': (A, 101, 1000.0, 0), 'B': (B, 102, 500.0, 1), 'C': (C, 103, 2000.0, 2)}
    out = []
    for key, (p, cid, kg, rank) in base.items():
        extra = kw.get(key, {})
        out.append(ac.PlanStop(key, cid, extra.get('point', p), kg, extra.get('delivered', kg), extra.get('window'),
                               extra.get('rank', rank), extra.get('delivered_at')))
    return out


def _two_trips(jitter=0.0, spike=False):
    """Склад 08:00 → A (10 мин) → B (6 мин) → склад (загрузка 25 мин) → C (20 мин) → склад."""
    tr = Track(DEPOT, _t(8))
    tr.stay(30, jitter_m=jitter).drive(A, 30).stay(10, jitter_m=jitter, spike=spike).drive(B, 30).stay(6, jitter_m=jitter)
    tr.drive(DEPOT, 30).stay(25, jitter_m=jitter).drive(C, 30).stay(20, jitter_m=jitter).drive(DEPOT, 30).stay(5)
    return tr


# ============================== восстановление факта ==============================

def test_reconstruct_two_trips():
    tr = _two_trips()
    day = ac.reconstruct(tr.fixes, _stops(), DEPOT)
    assert [v.keys for v in day.visits] == [('A',), ('B',), ('C',)]
    assert [v.trip for v in day.visits] == [0, 0, 1]
    assert [round(v.minutes) for v in day.visits] == [10, 6, 20]
    t1, t2 = day.trips
    assert t1.load_min is None                       # трек начался на складе — прибытие не видно, загрузка неизвестна
    assert t2.load_min == pytest.approx(25, abs=1.5)
    assert (t1.loaded_kg, t2.loaded_kg) == (1500.0, 2000.0)
    assert t1.depart is not None and t1.ret is not None and t2.ret is not None
    assert [(g.a, g.b) for g in day.legs] == [('depot', 'A'), ('A', 'B'), ('B', 'depot'), ('depot', 'C'), ('C', 'depot')]
    assert all(g.clean for g in day.legs) and day.unplanned_stays == 0
    path = (haversine_km(DEPOT, A) + haversine_km(A, B) + haversine_km(B, DEPOT) + 2 * haversine_km(DEPOT, C))
    assert day.km_gps == pytest.approx(path, rel=0.02)
    for g in day.legs:
        assert g.minutes == pytest.approx(haversine_km(g.pa, g.pb) / 30 * 60, abs=1.2)


def test_reconstruct_gps_jitter_and_spike_do_not_split_or_inflate():
    clean = ac.reconstruct(_two_trips().fixes, _stops(), DEPOT)
    noisy = ac.reconstruct(_two_trips(jitter=30.0, spike=True).fixes, _stops(), DEPOT)
    assert [v.keys for v in noisy.visits] == [('A',), ('B',), ('C',)]
    assert [round(v.minutes) for v in noisy.visits] == [round(v.minutes) for v in clean.visits]
    assert noisy.km_gps == pytest.approx(clean.km_gps, rel=0.03)     # джиттер ±30 м на стоянках км почти не добавляет
    assert noisy.unplanned_stays == 0


def test_reconstruct_bad_accuracy_ignored_and_deterministic():
    tr = _two_trips()
    bad = [replace(f, accuracy=150.0, lat=f.lat + 0.01) for f in tr.fixes[::7]]   # грубые точки — мимо
    fixes = tr.fixes + bad
    shuffled = list(fixes)
    random.Random(5).shuffle(shuffled)
    a, b = ac.reconstruct(fixes, _stops(), DEPOT), ac.reconstruct(shuffled, list(reversed(_stops())), DEPOT)
    assert a == b and a == ac.reconstruct(tr.fixes, _stops(), DEPOT)


def test_reconstruct_tunnel_gap_and_no_fix_while_parked():
    tr = Track(DEPOT, _t(8)).stay(10)
    tr.drive(A, 10, gap=(60, 60 + 12 * 60))                          # 12 мин без точек в пути — участок не чистый
    tr.stay(15, gap=(120, 480))                                       # без фикса 6 мин на стоянке — визит цел
    tr.drive(B, 30, gap=(30, 30 + 4 * 60)).stay(5).drive(DEPOT, 30).stay(5)   # 4 мин тоннель — участок чистый
    day = ac.reconstruct(tr.fixes, _stops(), DEPOT)
    assert [v.keys for v in day.visits] == [('A',), ('B',)]
    assert day.visits[0].minutes == pytest.approx(15, abs=0.01)     # от остановки до движения (№60): подъезд — езда
    legs = {(g.a, g.b): g for g in day.legs}
    assert legs[('depot', 'A')].clean is False and legs[('A', 'B')].clean is True


def test_reconstruct_repeat_visit_unplanned_stop_and_short_pass():
    tr = Track(DEPOT, _t(8)).stay(10).drive(A, 30).stay(8).drive(B, 30).stay(5)
    tr.drive(X, 30).stay(30)                                          # обед вне плана
    tr.drive(A, 30).stay(4)                                           # снова A — повторный заезд
    tr.drive(C, 30).stay(1.5)                                         # 1,5 мин у C — не визит
    tr.drive(DEPOT, 30).stay(5)
    day = ac.reconstruct(tr.fixes, _stops(), DEPOT)
    assert [(v.keys, v.repeat) for v in day.visits] == [(('A',), False), (('B',), False), (('A',), True)]
    assert day.unplanned_stays == 1
    legs = {(g.a, g.b): g for g in day.legs}
    assert legs[('B', 'A')].clean is False and legs[('A', 'B')].clean is True
    obs = lr.unload_obs(date(2026, 9, 29), day, _stops())
    assert [o.customers for o in obs] == [(101,), (102,)]            # повторный заезд в обучение не идёт


def test_reconstruct_two_stops_close_together_one_visit():
    a2 = (A[0] + 60 / 111195, A[1])                                   # соседний магазин в 60 м
    mid = (A[0] + 30 / 111195, A[1])
    tr = Track(DEPOT, _t(8)).stay(10).drive(mid, 30).stay(14, jitter_m=10).drive(DEPOT, 30).stay(5)
    visit = next(f.at for f in tr.fixes if haversine_km(f.point, mid) < 0.05)
    both = _stops(A={'delivered_at': visit + timedelta(minutes=5)}, B={'point': a2, 'delivered_at': visit + timedelta(minutes=9)})
    day = ac.reconstruct(tr.fixes, both, DEPOT)                       # обе доставки отмечены на этой стоянке
    assert [v.keys for v in day.visits] == [('A', 'B')] and not day.visits[0].repeat
    obs = lr.unload_obs(date(2026, 9, 29), day, both)
    assert [(o.n, o.tonnes) for o in obs] == [(2, 1.5)]
    # без отметок: середина стоянки — ровно посередине между точками (неоднозначно) — общий визит
    tr = Track(DEPOT, _t(8)).stay(10).drive(mid, 30).stay(14).drive(DEPOT, 30).stay(5)
    assert [v.keys for v in ac.reconstruct(tr.fixes, _stops(B={'point': a2}), DEPOT).visits] == [('A', 'B')]


def test_visit_metrics_windows_and_order():
    stops = _stops(A={'rank': 1, 'window': (8 * 60, 8 * 60 + 30)}, B={'rank': 0, 'window': (0, 12 * 60)})
    day = ac.reconstruct(_two_trips().fixes, stops, DEPOT)
    m = ac.visit_metrics(day, stops, date(2026, 9, 29))
    arrive_a = ac.local_minutes(day.visits[0].arrive)
    assert (m.planned, m.visited, m.with_window, m.on_time) == (3, 3, 2, 1)
    assert m.late_minutes == pytest.approx(arrive_a - (8 * 60 + 30), abs=0.1)
    assert (m.order_changes, m.ordered) == (1, 3)                     # A и B — наоборот
    assert ac.order_changes([0, 1, 2, 3]) == 0 and ac.order_changes([3, 2, 1, 0]) == 3 and ac.order_changes([]) == 0


def test_load_profile_on_board():
    stops = _stops(A={'delivered': 800.0})
    day = ac.reconstruct(_two_trips().fixes, stops, DEPOT)
    prof = ac.load_profile(day, stops)
    assert [round(kg) for _, _, kg in prof] == [1500, 700, 200, 2000, 0]   # после A — минус доставленные 800


def test_reconstruct_empty_and_no_point():
    day = ac.reconstruct([], _stops(C={'point': None}), DEPOT)
    assert (day.points, day.km_gps, day.trips, day.no_point) == (0, 0.0, (), ('C',))


# ============================== правила принятия ==============================

TODAY = date(2026, 10, 3)


def _days(n, start=TODAY - timedelta(days=40)):
    return [start + timedelta(days=i) for i in range(n)]


def test_huber_fit_robust_and_nonnegative():
    rnd = random.Random(3)
    rows = [(1.0, t, 4 + 12 * t + rnd.uniform(-0.5, 0.5)) for t in [i / 10 for i in range(40)]]
    rows += [(1.0, 1.0, 200.0), (1.0, 2.0, 300.0)]                    # выбросы (обед у магазина)
    a, b = lr.huber_fit(rows)
    assert a == pytest.approx(4, abs=0.6) and b == pytest.approx(12, abs=0.6)
    a, b = lr.huber_fit([(1.0, t, 10 - 3 * t) for t in (0.1, 0.5, 1.0, 2.0)])
    assert b == 0.0 and a > 0                                         # отрицательный наклон не выучивается


def _unload_obs(a=4.0, b=12.0, per_day=6, days=40, store_extra=None, noise=0.5, seed=7):
    rnd = random.Random(seed)
    out = []
    for d in _days(days):
        for i in range(per_day):
            cid = 100 + i
            t = 0.2 + 0.3 * ((i + d.day) % 7)
            y = a + b * t + (store_extra or {}).get(cid, 0.0) + rnd.uniform(-noise, noise)
            out.append(lr.UnloadObs(d, 1, t, y, (cid,)))
    return out


def test_fit_unload_accepts_when_better():
    obs = _unload_obs()
    o = lr.fit_unload(obs, lambda x: 8 * x.n + 6 * x.tonnes, TODAY)
    assert o.accepted and o.mae_after < o.mae_before * 0.98
    assert o.params['per_stop_min'] == pytest.approx(4, abs=0.5) and o.params['per_tonne_min'] == pytest.approx(12, abs=0.5)
    assert (o.test_from, o.test_to) == ('2026-09-26', '2026-10-02') and o.train_to == '2026-09-25'
    assert o.n_test == 6 * 7


def test_fit_unload_rejects_when_not_better_and_when_short():
    obs = _unload_obs()
    truth = lr.fit_unload(obs, lambda x: 4 * x.n + 12 * x.tonnes, TODAY)   # действующая норма уже верная
    assert not truth.accepted and 'ավելի լավ չէ' in truth.reason
    few = lr.fit_unload(obs[-40:], lambda x: 8 * x.n + 6 * x.tonnes, TODAY)
    assert not few.accepted and few.reason.startswith('քիչ տվյալներ') and few.params is None


def test_fit_unload_store_offset_only_from_2_visits_without_shrinkage():
    obs = _unload_obs(store_extra={101: 10.0}, per_day=6)
    # магазин 106 бывает 1 раз — поправки нет, хотя он медленный; 105 — 4 раза: поправка уже есть — сам факт (№60:
    # без стягивания к 0, как у 101 со множеством визитов)
    obs = [o for o in obs if o.customers != (105,)] + [lr.UnloadObs(d, 1, 0.5, 4 + 6 + 10, (105,)) for d in _days(4)]
    obs += [lr.UnloadObs(_days(1)[0], 1, 0.5, 4 + 6 + 10, (106,))]
    o = lr.fit_unload(obs, lambda x: 8 * x.n + 6 * x.tonnes, TODAY)
    offs = o.params['store_offsets']
    assert '106' not in offs and float(offs['101']) == pytest.approx(10, abs=0.6)
    assert float(offs['105']) == pytest.approx(10, abs=0.6)
    assert o.accepted


def test_fit_loading():
    rnd = random.Random(2)
    obs = [lr.LoadObs(d, t, 10 + 8 * t + rnd.uniform(-1, 1)) for d in _days(40) for t in (1.0, 3.5)]
    cur = (10.0, 6.0)                                                  # действующая норма ниже факта при 3,5 т
    ok = lr.fit_loading(obs, TODAY, cur)
    assert ok.accepted and ok.params == {'fixed_min': pytest.approx(10, abs=1), 'per_tonne_min': pytest.approx(8, abs=0.5)}
    assert not lr.fit_loading(obs[:10], TODAY, cur).accepted


BASE = evm.Norms.from_settings(rst.DEFAULT_SETTINGS)     # без карты дорог: 25 / 45 км/ч, извилистость 1,3
REF = lr.model_ref(BASE)


def _legs(ratio=1.3, current=None, days=40, per_day=6, ratio_test=None):
    """Участки в городе: выезд в 9:00, 10:00 или 11:00 (на 5 мин раньше часа конца — без перехода через час)."""
    out = []
    test_from = TODAY - timedelta(days=lr.HOLDOUT_DAYS)
    for d in _days(days):
        r = ratio_test if ratio_test is not None and d >= test_from else ratio
        for i in range(per_day):
            km = 2.0 + 0.5 * i                                         # 2–4,5 км: 4,8–10,8 мин модели
            model = km / 25.0 * 60
            hour = 9 + i % 3
            out.append(lr.LegObs(d, True, d.weekday() >= 5, hour, model * r, model,
                                 model if current is None else current(model), km, 25.0, d.weekday(), hour * 60.0))
    return out


def test_fit_travel_accepts_and_rejects():
    o = lr.fit_travel(_legs(), TODAY, 'straight', REF, BASE)
    assert o.accepted and o.model_id == 'straight'
    assert {f[3] for f in o.params['factors']} == {1.3}
    same = lr.fit_travel(_legs(current=lambda m: m * 1.3), TODAY, 'straight', REF, BASE)
    assert not same.accepted
    short = lr.fit_travel(_legs(days=12), TODAY, 'straight', REF, BASE)
    assert not short.accepted and short.reason.startswith('քիչ տվյալներ')


def test_fuel_intervals_and_fit():
    def rf(i, day, odo, liters, full=True, flags=(), superseded=False, car='CAR1'):
        return {'id': f'r{i:03d}', 'car_code': car, 'at_utc': f'{day}T06:00:00.000000+00:00',
                'payload': {'odometer_km': odo, 'liters': liters, 'full_tank': full}, 'flags': list(flags),
                'superseded': superseded}
    rows = [rf(1, '2026-09-01', 1000, 50), rf(2, '2026-09-02', 1100, 10, full=False), rf(3, '2026-09-03', 1300, 50),
            rf(4, '2026-09-04', 1250, 40, flags=['odometer_suspicious']),            # опечатка (флаг при приёме)
            rf(5, '2026-09-05', 1500, 60), rf(6, '2026-09-06', 1530, 9),             # 30 км — короткий интервал
            rf(7, '2026-09-07', 1700, 99, superseded=True), rf(8, '2026-09-07', 1700, 30)]
    ivs = lr.fuel_intervals(rows)
    # r3 (1300) и r4 (1250) друг с другом несовместимы, а с остальными — оба: какая опечатка — неизвестно, обе
    # не границы; их литры в баке — интервал r1 → r5: 500 км, 10 + 50 + 40 + 60 л
    assert [(iv.km, iv.liters) for iv in ivs] == [(500.0, 160.0), (170.0, 30.0)]
    assert ivs[1].l100 == pytest.approx(30 / 170 * 100)
    # одометр меньше прежнего — флаг при приёме не нужен: цепочка r0 → r1 → r3 решает, что ошибся r2
    got = lr.fuel_intervals([rf(0, '2026-08-31', 950, 50), rf(1, '2026-09-01', 1000, 10), rf(2, '2026-09-02', 900, 50),
                             rf(3, '2026-09-03', 1200, 40)])
    assert [(iv.km, iv.liters) for iv in got] == [(50.0, 10.0), (200.0, 90.0)]
    assert lr.fuel_intervals([rf(1, '2026-09-01', 1000, 50), {**rf(2, '2026-09-02', 1100, 50), 'payload': {
        'odometer_km': 1100, 'liters': None}}, rf(3, '2026-09-03', 1300, 40)]) == []   # литры неизвестны — обрыв
    rnd = random.Random(4)
    obs = [lr.FuelObs(d, u, 20 + 12 * u + rnd.uniform(-0.3, 0.3)) for d, u in zip(_days(24), [0.1, 0.6, 0.35, 0.8] * 6)]
    ok = lr.fit_fuel(obs, lambda u: 30.0, 'CAR1')
    assert ok.accepted and ok.params['empty_l100'] == pytest.approx(20, abs=1) and ok.params['full_l100'] == pytest.approx(32, abs=1)
    flat = lr.fit_fuel([replace(o, l100=25 + rnd.uniform(-0.2, 0.2)) for o in obs], lambda u: 30.0, 'CAR1')
    assert flat.accepted and flat.params['empty_l100'] == flat.params['full_l100']   # от загрузки не зависит — один расход
    assert not lr.fit_fuel(obs, lambda u: 20 + 12 * u, 'CAR1').accepted
    assert not lr.fit_fuel(obs[:15], lambda u: 30.0, 'CAR1').accepted          # 12 на выбор формы + 4 проверки


def test_fuel_obs_needs_track_coverage():
    start = datetime(2026, 9, 1, 8, tzinfo=TZ)
    iv = lr.Interval('CAR1', start, start + timedelta(days=2), 60.0, 300.0)
    prof = {'CAR1': [(start + timedelta(hours=2), 100.0, 5000.0), (start + timedelta(hours=3), 50.0, 0.0),
                     (start - timedelta(hours=1), 500.0, 10000.0)]}          # до интервала — не считается
    assert lr.fuel_obs([iv], prof, {'CAR1': 10000.0}) == {}            # трек покрывает 150 из 300 км — меньше 60%
    prof['CAR1'].append((start + timedelta(days=1), 50.0, 10000.0))
    got = lr.fuel_obs([iv], prof, {'CAR1': 10000.0})['CAR1'][0]
    assert got.load == pytest.approx((100 * 0.5 + 50 * 1.0) / 300) and got.l100 == 20.0
    assert lr.fuel_obs([iv], prof, {}) == {}                            # тоннаж не задан


# ============================== действующие нормы и применение ==============================

def _row(kind, run_day, accepted=True, params=None, scope='', model_id=None):
    return {'kind': kind, 'scope': scope, 'run_day': run_day, 'accepted': accepted, 'params': params, 'model_id': model_id}


def test_in_effect_rules():
    u1, u2 = {'per_stop_min': 5, 'per_tonne_min': 9}, {'per_stop_min': 6, 'per_tonne_min': 9}
    tr = {'factors': [[1, 0, 9, 1.3]], 'ref': {'speed_city_kmh': 25.0, 'speed_region_kmh': 45.0, 'detour': 1.3}}
    rows = [_row('unload', '2026-09-01', params=u1), _row('unload', '2026-09-02', params=u2),
            _row('unload', '2026-09-03', accepted=False, params={'per_stop_min': 1, 'per_tonne_min': 1}),
            _row('travel', '2026-09-01', params=tr, model_id='roads:v1'),
            _row('fuel', '2026-09-01', params={'empty_l100': 20, 'full_l100': 30}, scope='CAR1')]
    eff = lr.in_effect(rows, {}, 'roads:v1')
    assert eff.unload == u2 and eff.travel['model_id'] == 'roads:v1' and eff.fuel == {'CAR1': {'empty_l100': 20, 'full_l100': 30}}
    assert lr.in_effect(rows, {}, 'roads:v2').travel is None                    # дорожная модель сменилась
    assert lr.in_effect(rows, {}, None).travel is None                          # Яндекс — поправка не применяется
    off = lr.in_effect(rows, {'unload': False}, 'roads:v1')
    assert off.unload is None and off.fuel
    assert not lr.in_effect([], {}, 'straight')
    load = [_row('loading', '2026-09-01', params={'fixed_min': 12, 'per_tonne_min': 4})]
    assert lr.in_effect(load, {}, 'straight').loading is None                   # загрузка — по умолчанию выключена
    assert lr.in_effect(load, {'loading': True}, 'straight').loading == {'fixed_min': 12, 'per_tonne_min': 4}


def test_apply_learned_changes_norms_and_trip_minutes():
    norms = evm.Norms.from_settings(rst.DEFAULT_SETTINGS)
    tn = fl.TruckNorms(work_minutes=540.0, unload_min_per_stop=8.0, unload_min_per_tonne=6.0)
    trucks = {'CAR1': fl.FleetTruck('CAR1', 'HOWO', 10000.0, 30.0)}
    same = lr.apply_learned(norms, tn, trucks, lr.InEffect(), {})
    assert same[0] is norms and same[1] is tn and same[2] == trucks
    eff = lr.InEffect(unload={'per_stop_min': 5.0, 'per_tonne_min': 10.0, 'store_offsets': {'101': 7.0, '999': 3.0}},
                      loading={'fixed_min': 12.0, 'per_tonne_min': 4.0},
                      travel={'factors': [[1, 0, 9, 1.5]], 'ref': {'speed_city_kmh': 20.0, 'speed_region_kmh': 45.0,
                                                                   'detour': 1.3}, 'model_id': 'straight'},
                      fuel={'CAR1': {'empty_l100': 22.0, 'full_l100': 35.0}})
    n2, t2, tr2 = lr.apply_learned(norms, tn, trucks, eff, {101: A})
    assert (t2.unload_min_per_stop, t2.unload_min_per_tonne, t2.unload_extra) == (5.0, 10.0, {A: 7.0})
    assert (t2.warehouse_load_fixed_min, t2.warehouse_load_min_per_tonne, t2.loading_configured) == (12.0, 4.0, True)
    assert (tr2['CAR1'].fuel_empty_l_per_100km, tr2['CAR1'].fuel_full_l_per_100km, tr2['CAR1'].l100) == (22.0, 35.0, 28.5)
    # время = км / (v · f) = 1,5 × км_обуч / v_обуч: при скорости настроек 25 и обучении на 20 → f = 20 / (25 · 1,5)
    assert n2.traffic.factor(True, 1, 9 * 60) == pytest.approx(20 / (25 * 1.5))
    assert n2.traffic.factor(True, 1, 10 * 60) == 1.0 and norms.traffic is None
    km = 3.0
    assert n2.traffic.travel(km, 25.0, True, 1, 9 * 60) == pytest.approx(1.5 * km / 20 * 60)
    # поправка магазина — в минутах рейса, у других точек — нет
    base = fl.route_trip([A, B], [1000.0, 500.0], DEPOT, norms, tn, reorder=False)
    with_eff = fl.route_trip([A, B], [1000.0, 500.0], DEPOT, norms, t2, reorder=False)
    plain = fl.route_trip([A, B], [1000.0, 500.0], DEPOT, norms, replace(t2, unload_extra={}), reorder=False)
    assert with_eff[2] == pytest.approx(plain[2] + 7.0) and base[2] != plain[2]
    # без выученных поправок — те же числа до бита (разность поправки — ровно 0.0)
    assert fl.route_trip([A, B], [1000.0, 500.0], DEPOT, norms, replace(tn, unload_extra={}), reorder=False) == base


def test_apply_learned_traffic_merges_over_manager_profile_and_detour_ratio():
    norms = replace(evm.Norms.from_settings(rst.DEFAULT_SETTINGS), traffic=TrafficProfile({(True, 0, 8): 0.8}, {}))
    eff = lr.InEffect(travel={'factors': [[1, 0, 9, 2.0]], 'ref': {'speed_city_kmh': 25.0, 'speed_region_kmh': 45.0,
                                                                   'detour': 1.3 * 2}, 'model_id': 'straight'})
    n2, _, _ = lr.apply_learned(norms, fl.TruckNorms(540.0, 8.0, 6.0), {}, eff, {})
    assert n2.traffic.factors[(True, 0, 8)] == 0.8                              # час без данных грузовиков — как был
    assert n2.traffic.factors[(True, 0, 9)] == pytest.approx(25 * (1.3 / 2.6) / (25 * 2.0))
    roads = replace(norms, provider=object())
    assert lr.apply_learned(roads, fl.TruckNorms(540.0, 8.0, 6.0), {}, eff, {})[0] is roads   # Яндекс — без поправки


def test_day_report_plan_and_fact():
    stops = _stops(A={'window': (0, 9 * 60)})
    day = ac.reconstruct(_two_trips().fixes, stops, DEPOT)
    rep = lr.day_report('CAR1', date(2026, 9, 29), day, stops, {'km': 30.04, 'minutes': 400, 'liters': 9.0,
                                                                'loading_minutes': 50, 'depart': '09:30',
                                                                'return': '12:00 (+1)'},
                        2, 3, 10000.0, 30.0)
    assert rep['plan'] == {'km': 30.0, 'minutes': 1590, 'liters': 9.0, 'loading_minutes': 50, 'trips': 2, 'stops': 3}
    f, k = rep['fact'], rep['kpi']
    first, last = day.trips[0].depart, day.trips[-1].ret
    assert f['minutes'] == round((last - first).total_seconds() / 60) and f['trips'] == 2 and f['stops'] == 3
    assert f['liters'] == round(day.km_gps * 0.3, 1) and f['loading_minutes'] == round(day.trips[1].load_min)
    assert k['km_per_stop'] == round(day.km_gps / 3, 2) and k['load_pct'] == round(100 * 3500 / 20000, 1)
    assert (k['with_window'], k['on_time_pct']) == (1, 100.0)
    old = lr.day_report('CAR1', date(2026, 9, 29), day, stops, {'minutes': 400}, 0, 0, None, None)
    assert old['plan']['minutes'] == 400 and old['plan']['trips'] is None and old['fact']['liters'] is None
    assert old['kpi']['load_pct'] is None and old['kpi']['liters_per_stop'] is None
    assert lr._hhmm('9:30') is None and lr._hhmm('09:3x') is None and lr._hhmm('23:59') == 1439


def test_next_run_is_3am_yerevan():
    assert lr.next_run(datetime(2026, 10, 3, 2, 59, tzinfo=TZ)) == datetime(2026, 10, 3, 3, 0, tzinfo=TZ)
    assert lr.next_run(datetime(2026, 10, 3, 3, 0, tzinfo=TZ)) == datetime(2026, 10, 4, 3, 0, tzinfo=TZ)


# ============================== журнал в route_optimizer.db ==============================

def test_store_learned_upsert_and_switch(tmp_path):
    s = rst.Store(str(tmp_path / 'r.db'))
    o = lr.Outcome('unload', '', True, 'принято', {'per_stop_min': 5.0, 'per_tonne_min': 9.0, 'store_offsets': {}},
                   n_obs=40, n_test=10, mae_before=3.0, mae_after=1.0)
    s.save_learned('2026-10-02', [o, lr.Outcome('fuel', 'CAR1', False, 'мало данных')])
    s.save_learned('2026-10-03', [o])
    s.save_learned('2026-10-03', [replace(o, accepted=False, reason='нет')])   # повтор того же дня — заменяет
    rows = s.learned()
    assert [(r['kind'], r['scope'], r['run_day'], r['accepted']) for r in rows] == [
        ('fuel', 'CAR1', '2026-10-02', False), ('unload', '', '2026-10-02', True), ('unload', '', '2026-10-03', False)]
    assert [r['run_day'] for r in s.learned('2026-10-03', accepted_only=True)] == ['2026-10-02']
    assert s.learning_auto() == {}
    s.save_learning_auto('travel', False, 'qa')
    assert s.learning_auto() == {'travel': False}
    with pytest.raises(rst.StoreError), closing(sqlite3.connect(s.path)) as conn:
        conn.execute("UPDATE learned_norms SET params = '{bad' WHERE kind = 'fuel'")
        conn.commit()
        s.learned()


def test_store_migrates_12_to_13_keeps_data(tmp_path):
    path = str(tmp_path / 'v12.db')
    s = rst.Store(path)
    s.save_dispatch('2026-10-01', {'x': 1}, 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE learned_norms')
        conn.execute('DROP TABLE learning_switch')
        conn.execute("UPDATE meta SET value = '12' WHERE key = 'schema_version'")
        conn.commit()
    s2 = rst.Store(path)
    assert s2.load_dispatch('2026-10-01') == ({'x': 1}, 1)
    assert s2.learned() == [] and s2.learning_auto() == {}
    with closing(sqlite3.connect(path)) as conn:   # 12 → 13 → … → текущая
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(rst.SCHEMA_VERSION),)


OWNER_ROUTES = ROOT / 'route_optimizer.db'


@pytest.mark.skipif(not OWNER_ROUTES.exists(), reason='нет базы маршрутов владельца')
def test_owner_routes_copy_migrates_to_13(tmp_path):
    copy = tmp_path / 'owner_routes.db'
    with closing(sqlite3.connect(f'file:{OWNER_ROUTES.as_posix()}?mode=ro', uri=True)) as src, \
            closing(sqlite3.connect(str(copy))) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(str(copy))) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'sqlite_sequence'")]
        before = {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in tables if t != 'meta'}
    s = rst.Store(str(copy))
    s.load()
    with closing(sqlite3.connect(str(copy))) as conn:
        assert {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in before} == before
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(rst.SCHEMA_VERSION),)
    assert s.learned() == []


# ============================== прогон обучения и страница (синтетический факт) ==============================

S101, S102, S104 = (40.2050, 44.5650), (40.1990, 44.5500), (40.2120, 44.5800)


class FakeFacts:
    """Факт машин для «Маршрутов»: CAR1 каждый день — склад → 101, 102 → склад (загрузка) → 104 → склад.
    Разгрузка = 4 мин + 12 мин/т, загрузка = 10 + 8 мин/т, скорость 15 км/ч (модель по прямой: 25 км/ч × 1,3)."""

    def __init__(self, days):
        self.days = days

    def car_days(self, since, until):
        return [('CAR1', d.isoformat()) for d in self.days if since <= d.isoformat() <= until]

    @staticmethod
    def kg(d):
        return {101: 300.0 + 100 * (d.day % 5), 102: 800.0, 104: 1500.0 + 200 * (d.day % 4)}

    def day(self, car, ds):
        d = date.fromisoformat(ds)
        kg = self.kg(d)
        tr = Track(DEPOT, datetime(d.year, d.month, d.day, 8, 30, tzinfo=TZ))
        tr.stay(20).drive(S101, 15).stay(4 + 12 * kg[101] / 1000).drive(S102, 15).stay(4 + 12 * kg[102] / 1000)
        tr.drive(DEPOT, 15).stay(10 + 8 * kg[104] / 1000).drive(S104, 15).stay(4 + 12 * kg[104] / 1000)
        tr.drive(DEPOT, 15).stay(5)
        track = [(int(f.at.timestamp() * 1000), f.lat, f.lon, f.accuracy, None) for f in tr.fixes]
        stops = [{'stop_id': f'S:{cid}', 'customer_id': cid, 'lat': p[0], 'lon': p[1], 'weight_kg': kg[cid], 'seq': i,
                  'delivered_share': 1.0} for i, (cid, p) in enumerate(((101, S101), (102, S102), (104, S104)), 1)]
        return {'track': track, 'stops': stops}

    def refuels(self):
        return []

    def version(self, car, ds):
        self.versions = getattr(self, 'versions', 0) + 1
        return (ds,)


def _learning_client(client, monkeypatch, days=30):
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    state.fleet_facts = FakeFacts([TODAY - timedelta(days=i) for i in range(days, 0, -1)])
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 3, 10, 0))
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 3, 10, 0, tzinfo=TZ))
    return state


def test_run_learning_end_to_end_idempotent_and_applied(client, monkeypatch):
    state = _learning_client(client, monkeypatch)
    out = views.run_learning(state, TODAY)
    by = {o.kind: o for o in out}
    assert set(by) == {'unload', 'loading', 'travel', 'truck_time'}
    assert not by['truck_time'].accepted and by['truck_time'].reason.startswith('Valhalla-ն հասանելի չէ')   # нет карты
    assert by['unload'].accepted and by['unload'].params['per_stop_min'] == pytest.approx(4, abs=0.6)
    assert by['unload'].params['per_tonne_min'] == pytest.approx(12, abs=0.6)
    assert by['loading'].accepted and by['loading'].params['fixed_min'] == pytest.approx(10, abs=1.5)
    assert by['travel'].accepted and by['travel'].model_id == 'straight'
    assert all(f[3] == pytest.approx(25 / (1.3 * 15), rel=0.08) for f in by['travel'].params['factors'])
    rows = state.store.learned()
    again = views.run_learning(state, TODAY)                                    # повтор того же дня — тот же итог
    assert again == out and [(r['kind'], r['params']) for r in state.store.learned()] == \
        [(r['kind'], r['params']) for r in rows]
    # в расчёте «Развоза» — выученные нормы
    bundle = views._bundle(state)
    snap, _ = state.snapshots.cached()
    ready = views._ready_trucks(snap, bundle)
    ctx = views._dispatch_ctx(state, snap, bundle, date(2026, 10, 4), ready, [S101], {101: S101})
    assert (ctx.tn.unload_min_per_stop, ctx.tn.unload_min_per_tonne) == (by['unload'].params['per_stop_min'],
                                                                         by['unload'].params['per_tonne_min'])
    assert not ctx.tn.loading_configured                                       # загрузка — по умолчанию выключена
    assert ctx.norms.traffic is not None and ctx.norms.traffic.report['trucks'] == 'learned'
    # следующий день: действующая норма уже выученная — новая не лучше её, остаётся прежняя
    nxt = {o.kind: o for o in views.run_learning(state, TODAY + timedelta(days=1))}
    assert not nxt['unload'].accepted and 'ավելի լավ չէ' in nxt['unload'].reason
    ctx2 = views._dispatch_ctx(state, snap, bundle, date(2026, 10, 5), ready, [S101], {101: S101})
    assert ctx2.tn.unload_min_per_stop == ctx.tn.unload_min_per_stop
    # автообучение разгрузки выключено — снова ручные 8 + 6
    r = client.post('/api/routes/learning/auto', json={'kind': 'unload', 'auto': False})
    assert r.status_code == 200 and next(x for x in r.get_json()['status'] if x['kind'] == 'unload')['in_effect'] is None
    ctx3 = views._dispatch_ctx(state, snap, bundle, date(2026, 10, 5), ready, [S101], {101: S101})
    assert (ctx3.tn.unload_min_per_stop, ctx3.tn.unload_min_per_tonne) == (8.0, 6.0)


def test_learning_api_report_and_errors(client, monkeypatch):
    state = _learning_client(client, monkeypatch, days=10)
    views.run_learning(state, TODAY)
    d = client.get('/api/routes/learning').get_json()
    assert d['success'] and (d['from'], d['to']) == ('2026-09-19', '2026-10-02') and d['connected'] and d['depot']
    assert len(d['days']) == 10 and d['days'][0]['day'] == '2026-10-02'
    row = d['days'][0]
    assert row['fact']['trips'] == 2 and row['fact']['stops'] == 3 and row['plan']['trips'] is None
    assert row['kpi']['km_per_stop'] > 0 and row['kpi']['stops_per_hour'] > 0 and row['kpi']['load_pct'] is not None
    kinds = [(s['kind'], s['in_effect']) for s in d['status']]
    assert [k for k, _ in kinds] == ['unload', 'loading', 'travel', 'truck_time'] and kinds[0][1] is None \
        and kinds[1][1] is None
    assert d['status'][0]['last']['reason'].startswith('քիչ տվյալներ')
    assert client.get('/api/routes/learning?from=2026-08-01&to=2026-10-02').status_code == 400
    assert client.get('/api/routes/learning?from=2026-10-02&to=2026-10-01').status_code == 400
    assert client.post('/api/routes/learning/auto', json={'kind': 'x', 'auto': True}).status_code == 400
    assert client.post('/api/routes/learning/auto', json={'kind': 'fuel', 'auto': 'no'}).status_code == 400


def test_learning_page_renders_and_linked():
    """Страница — шаблон настоящего приложения (base_v2), ссылка «Обучение» — в разделах «Маршрутов»."""
    import app_v2
    from flask import render_template
    with app_v2.app.test_request_context('/routes/learning'):
        html = render_template('routes_learning.html')
    assert 'Ուսուցում և փաստ' in html and 'js/routes_learning.js' in html and 'aria-current="page">Ուսուցում' in html
    for name in ('routes_overview.html', 'routes_optimize.html', 'routes_settings.html', 'routes_dispatch.html'):
        assert 'href="/routes/learning"' in (ROOT / 'templates' / name).read_text(encoding='utf-8'), name


def test_learning_run_button_background_and_lock(client, monkeypatch):
    state = _learning_client(client, monkeypatch, days=3)
    with state.learning_lock:
        assert client.post('/api/routes/learning/run', json={}).status_code == 409
    assert views.run_learning_job(state, TODAY, 'qa') is True and state.learning_job['status'] == 'done'
    state.fleet_facts = FakeFacts([])                                           # трека ещё нет — ERP не читается
    state.snapshots = None
    assert views.run_learning(state, TODAY) == [] and state.store.learned()[-1]['run_day'] == TODAY.isoformat()
    state.fleet_facts = None
    assert client.post('/api/routes/learning/run', json={}).status_code == 400
    assert views.run_learning(state, TODAY) == []


def test_learning_job_reports_errors(client, monkeypatch):
    state = _learning_client(client, monkeypatch, days=3)

    def boom(*a, **k):
        raise RuntimeError('x')
    monkeypatch.setattr(views, 'run_learning', boom)
    assert views.run_learning_job(state, TODAY, 'qa') is True
    assert state.learning_job['status'] == 'error' and state.learning_job['error'] == 'Սերվերի ներքին սխալ'


def test_dispatch_unchanged_without_learned_norms(client):
    """Без выученных норм «Развоз» считает как раньше: те же рейсы и минуты."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    a = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    state.store.save_learned('2026-09-01', [lr.Outcome('unload', '', False, 'нет', {'per_stop_min': 1.0,
                                                                                      'per_tonne_min': 1.0})])
    b = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    strip = lambda p: [[(tr['truck'], [s['customer_id'] for s in tr['stops']], tr.get('minutes'), tr.get('km'))  # noqa: E731
                        for tr in t['trips']] for t in p['plan']['trucks']]
    assert strip(a) == strip(b)
    state.store.save_learned('2026-09-02', [lr.Outcome('unload', '', True, 'да', {'per_stop_min': 30.0,
                                                                                    'per_tonne_min': 20.0,
                                                                                    'store_offsets': {}})])
    c = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert strip(c) != strip(a)                                                 # выученная разгрузка — дольше рейсы


@pytest.mark.filterwarnings('ignore::pytest.PytestUnhandledThreadExceptionWarning')   # поток теста гасим SystemExit
def test_nightly_scheduler_runs_job_and_can_be_disabled(client, monkeypatch):
    import route_optimizer as ro
    app = client.application
    monkeypatch.setenv('ROUTES_LEARNING_NIGHTLY', '0')
    assert ro.start_learning_scheduler(app) is None
    monkeypatch.setenv('ROUTES_LEARNING_NIGHTLY', '1')
    calls, waits = [], []

    def fake_sleep(seconds):
        waits.append(seconds)
        if len(waits) > 1:
            raise SystemExit                                                    # второй круг — конец потока теста

    monkeypatch.setattr(ro.time, 'sleep', fake_sleep)
    monkeypatch.setattr(ro, 'run_learning_job', lambda state, day, user: calls.append((day, user)) or True)
    thread = ro.start_learning_scheduler(app)
    thread.join(5)
    assert not thread.is_alive() and thread.daemon
    assert len(calls) == 1 and calls[0][1] == 'nightly' and 0 < waits[0] <= 86400

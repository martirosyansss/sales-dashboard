# -*- coding: utf-8 -*-
"""Стоянка у магазина — от остановки до начала движения (ответ владельца №60; route_optimizer.actuals).

Синтетические треки терминала «Առաքիչ» (контракт §7.1: в пути — точка раз в 15 с, стоя — первые 2 мин раз в 15 с,
потом раз в 60 с; скорость — м/с или null). Запуск из корня проекта:  python -m pytest tests/test_store_stay_gps.py -q
"""
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer.geo import haversine_km  # noqa: E402
from test_learning_loop import DEPOT, A, B, _stops, _t  # noqa: E402

DAY = date(2026, 9, 29)


def _north(p, m):
    return p[0] + m / 111195, p[1]


SOUTH = _north(A, -1000)          # подъезд к A с юга
EDGE = _north(A, -99)             # въезд в зону A (STOP_RADIUS_M — 100 м)
PARK = _north(A, 20)              # где встал: в 20 м за точкой A (искал место)
OUT = _north(A, 120)              # уехал из зоны на север


class Path:
    """Трек терминала: drive — точка раз в 15 с (не меньше двух на перегон) со скоростью перегона; stand — стоит
    (скорость 0; точка остановки — тоже 0), первые 2 мин раз в 15 с, потом раз в 60 с. spd=False — терминал скорость
    не шлёт (None)."""

    def __init__(self, start, at, spd=True):
        self.pos, self.t, self.spd = start, at, spd
        self.fixes = [ac.TrackFix(at, start[0], start[1], 8.0, 0.0 if spd else None)]

    def _add(self, t, p, v):
        self.fixes.append(ac.TrackFix(t, p[0], p[1], 8.0, v if self.spd else None))

    def drive(self, to, kmh):
        secs = haversine_km(self.pos, to) / kmh * 3600
        n = max(2, round(secs / 15))
        a = self.pos
        for i in range(1, n + 1):
            k = i / n
            self._add(self.t + timedelta(seconds=secs * k), (a[0] + (to[0] - a[0]) * k, a[1] + (to[1] - a[1]) * k),
                      kmh / 3.6)
        self.t += timedelta(seconds=secs)
        self.pos = to
        return self

    def stand(self, minutes):
        if self.spd:
            self.fixes[-1] = replace(self.fixes[-1], spd=0.0)
        start, t = self.t, self.t
        while True:
            t += timedelta(seconds=15 if t - start < timedelta(minutes=2) else 60)
            if t > start + timedelta(minutes=minutes, seconds=1):
                break
            self._add(t, self.pos, 0.0)
        self.t = start + timedelta(minutes=minutes)
        return self


def _to_a(spd=True):
    """Склад → подъезд с юга → медленно (6 км/ч) через зону A к месту стоянки."""
    return Path(DEPOT, _t(8), spd).stand(5).drive(SOUTH, 30).drive(EDGE, 30).drive(PARK, 6)


def _zone_minutes(fixes, p=A):
    """Сколько машина была в зоне точки (первая — последняя точка в STOP_RADIUS_M) — стоянка по прежнему правилу."""
    inside = [f.at for f in fixes if haversine_km(f.point, p) * 1000 <= ac.STOP_RADIUS_M]
    return (inside[-1] - inside[0]).total_seconds() / 60


# ============================== от остановки до начала движения ==============================

@pytest.mark.parametrize('spd', [True, False])
def test_slow_approach_stop_unload_leave_counts_stop_to_move(spd):
    """Подъезд по зоне 6 км/ч, 12 мин стоит, медленно уезжает: в зоне ≈ 14 мин (прежняя стоянка), визит — ровно 12:
    от остановки до начала движения. Без скорости терминала (spd=False) — то же по шагу и смещению точек."""
    p = _to_a(spd)
    stop = p.t
    p.stand(12).drive(OUT, 6).drive(DEPOT, 30).stand(5)
    stops = _stops()
    day = ac.reconstruct(p.fixes, stops, DEPOT)
    assert _zone_minutes(p.fixes) > 13.9
    (v,) = day.visits
    assert v.keys == ('A',) and not v.repeat
    assert v.arrive == stop and v.minutes == pytest.approx(12, abs=1e-3)
    assert [o.minutes for o in lr.unload_obs(DAY, day, stops)] == [pytest.approx(12, abs=1e-3)]
    # дальше всё — от того же прибытия и отъезда: участки (подъезд в зоне — уже езда), отметки, км
    legs = {(g.a, g.b): g for g in day.legs}
    assert legs[('depot', 'A')].arrive == v.arrive and legs[('A', 'depot')].depart == v.leave
    assert ac.stop_marks(day, stops, DAY)['A']['arrive'] == stop
    path = sum(haversine_km(a, b) for a, b in ((DEPOT, SOUTH), (SOUTH, EDGE), (EDGE, PARK), (PARK, OUT), (OUT, DEPOT)))
    assert day.km_gps == pytest.approx(path, rel=0.005)


def test_lateness_counts_from_stop_not_zone_entry():
    """Окно приёма кончилось в момент въезда в зону: по прежнему правилу — вовремя, теперь — опоздание на подъезд
    (≈ 70 с по зоне до места стоянки)."""
    p = _to_a()
    stop = p.t
    p.stand(10).drive(OUT, 6).drive(DEPOT, 30).stand(5)
    entry = next(f.at for f in p.fixes if haversine_km(f.point, A) * 1000 <= ac.STOP_RADIUS_M)
    hi = ac.day_minutes(DAY, entry)
    stops = _stops(A={'window': (0.0, hi)})
    mark = ac.stop_marks(ac.reconstruct(p.fixes, stops, DEPOT), stops, DAY)['A']
    assert mark['arrive'] == stop and mark['late_min'] == pytest.approx((stop - entry).total_seconds() / 60, abs=0.1)
    assert mark['late_min'] >= 1.1


@pytest.mark.parametrize('spd', [True, False])
def test_jam_through_zone_without_stop_is_not_a_visit(spd):
    """Пробка: 4,5 км/ч (1,25 м/с) через всю зону A — в зоне ≥ 2 мин (по прежнему правилу — визит), но машина не
    останавливалась: визита нет; путь через зону — в км. B — настоящий визит."""
    p = Path(DEPOT, _t(8), spd).stand(5).drive(SOUTH, 30).drive(EDGE, 30).drive(OUT, 4.5)
    p.drive(B, 30).stand(8).drive(DEPOT, 30).stand(5)
    assert _zone_minutes(p.fixes) >= ac.MIN_DWELL.total_seconds() / 60
    day = ac.reconstruct(p.fixes, _stops(), DEPOT)
    assert [v.keys for v in day.visits] == [('B',)] and day.unplanned_stays == 0
    path = sum(haversine_km(a, b) for a, b in ((DEPOT, SOUTH), (SOUTH, EDGE), (EDGE, OUT), (OUT, B), (B, DEPOT)))
    assert day.km_gps == pytest.approx(path, rel=0.005)


def test_short_stop_in_jam_next_to_store_is_not_a_visit():
    """В пробке у магазина постоял 1,5 мин и поехал дальше — остановки ≥ MIN_DWELL нет: не визит."""
    p = Path(DEPOT, _t(8)).stand(5).drive(SOUTH, 30).drive(EDGE, 30).drive(PARK, 4.5).stand(1.5).drive(OUT, 4.5)
    p.drive(B, 30).stand(8).drive(DEPOT, 30).stand(5)
    assert [v.keys for v in ac.reconstruct(p.fixes, _stops(), DEPOT).visits] == [('B',)]


def test_reposition_inside_zone_keeps_one_stay():
    """Постоял 5 мин, переставил машину на 20 м, ещё 6 мин — одна стоянка (обслуживание магазина), от первой остановки
    до движения после второй. Ждал у ворот 3 мин и заехал во двор за 70 м — тоже одна, и в обучение идёт она целиком:
    у магазина, который в своём месте один, две остановки ≥ MIN_DWELL до JITTER_BREAK друг от друга — одна стоянка.
    Разгрузился и постоял 45 с в пробке на углу за 90 м — это уже не магазин: визит — сама разгрузка. Снова встал позже
    JITTER_BREAK (круг за парковкой 4 мин) — новая стоянка, повторный заезд."""
    p = _to_a()
    stop = p.t
    p.stand(5).drive(_north(PARK, 20), 5).stand(6)
    end = p.t
    p.drive(OUT, 6).drive(DEPOT, 30).stand(5)
    day = ac.reconstruct(p.fixes, _stops(), DEPOT)
    assert [(v.keys, v.arrive, v.leave) for v in day.visits] == [(('A',), stop, end)]
    assert day.visits[0].minutes == pytest.approx(5 + 20 / (5 / 3.6) / 60 + 6, abs=1e-3)
    gate = _to_a()
    stop = gate.t
    gate.stand(3).drive(_north(PARK, -70), 5).stand(12).drive(OUT, 6).drive(DEPOT, 30).stand(5)
    stops = _stops()
    got = ac.reconstruct(gate.fixes, stops, DEPOT)
    assert [(v.keys, v.repeat, v.arrive) for v in got.visits] == [(('A',), False, stop)]
    assert [o.minutes for o in lr.unload_obs(DAY, got, stops)] == \
        [pytest.approx(3 + 70 / (5 / 3.6) / 60 + 12, abs=1e-3)]
    corner = _to_a()
    stop = corner.t
    corner.stand(10)
    end = corner.t
    corner.drive(_north(A, -70), 6).stand(0.75).drive(_north(A, -200), 6).drive(DEPOT, 30).stand(5)
    got = ac.reconstruct(corner.fixes, _stops(), DEPOT)
    assert [(v.keys, v.arrive, v.leave) for v in got.visits] == [(('A',), stop, end)]
    lap = _to_a().stand(5).drive(_north(A, -90), 5).drive(_north(A, 90), 5).drive(_north(A, 30), 5).stand(6)
    lap.drive(OUT, 6).drive(DEPOT, 30).stand(5)
    got = ac.reconstruct(lap.fixes, _stops(), DEPOT)
    assert [(v.keys, v.repeat, round(v.minutes, 2)) for v in got.visits] == [(('A',), False, 5.0), (('A',), True, 6.0)]


def test_stop_outside_zone_is_not_a_store_stay():
    """Встал в 115 м от A (за краем зоны) на 2 мин и вернулся в зону раньше JITTER_BREAK — отрезок зоны «склеен» через
    выход, но остановка — не у магазина, а в зоне машина не стояла: визита нет (по прежнему правилу — был)."""
    p = Path(DEPOT, _t(8)).stand(5).drive(SOUTH, 30).drive(EDGE, 30).drive(_north(A, 50), 30)
    p.drive(_north(A, 115), 6).stand(2.1).drive(_north(A, -150), 30).drive(B, 30).stand(8).drive(DEPOT, 30).stand(5)
    assert [v.keys for v in ac.reconstruct(p.fixes, _stops(), DEPOT).visits] == [('B',)]


# ============================== момент отъезда ==============================

def _gap_leave(first_spd, km_h_after=30.0):
    """Стоит 10 мин (последние точки — раз в 60 с), следующая точка — через 60 с в 100 м, со скоростью first_spd."""
    p = _to_a().stand(10)
    last = p.fixes[-1].at
    nxt = _north(PARK, 100)
    p.fixes.append(ac.TrackFix(last + timedelta(seconds=60), nxt[0], nxt[1], 8.0, first_spd))
    p.t, p.pos = last + timedelta(seconds=60), nxt
    p.drive(DEPOT, km_h_after).stand(5)
    (v,) = ac.reconstruct(p.fixes, _stops(), DEPOT).visits
    return v, last


def test_leave_estimated_inside_60s_gap():
    """Отъезд — между последней точкой стоянки и первой точкой движения: первая точка движения минус путь до неё по её
    скорости (100 м при 5 м/с — за 20 с до неё), в пределах промежутка; без скорости — скорость следующего перегона."""
    def after(first_spd):
        v, last = _gap_leave(first_spd)
        return (v.leave - last).total_seconds()
    assert after(5.0) == pytest.approx(40, abs=1e-3)
    assert after(1.2) == 0.0                                       # 100 м при 1,2 м/с — 83 с: не раньше последней точки
    assert after(None) == pytest.approx(60 - 100 / (30 / 3.6), abs=0.01)   # без скорости — перегон дальше (30 км/ч)


def test_gps_hole_after_stop_ends_stay_at_last_standing_fix():
    """Стоял 10 мин, потом 15 мин без точек и точка в 3 км на 10 м/с: отъезд в дыре не виден — стоянка до последней
    точки стоянки (а не «по скорости» за 5 мин до точки после дыры), участок с дырой — не чистый."""
    p = _to_a().stand(10)
    last = p.fixes[-1].at
    far = _north(PARK, 3000)
    p.fixes.append(ac.TrackFix(last + timedelta(minutes=15), far[0], far[1], 8.0, 10.0))
    p.t, p.pos = last + timedelta(minutes=15), far
    p.drive(DEPOT, 30).stand(5)
    day = ac.reconstruct(p.fixes, _stops(), DEPOT)
    (v,) = day.visits
    assert v.leave == last and v.minutes == pytest.approx(10, abs=1e-3)
    assert {(g.a, g.b): g.clean for g in day.legs}[('A', 'depot')] is False


# ============================== несколько магазинов в одном месте ==============================

def test_shared_site_assigned_as_before():
    """Магазины A и B в 60 м (одно «место»), машина подъезжает через точку A. По отметкам доставки — обе на этой стоянке;
    без отметок — по середине стоянки (теперь — по точкам остановки): встала у B — только B, посередине — общая."""
    b_point = _north(A, 60)

    def track(park):
        p = Path(DEPOT, _t(8)).stand(5).drive(SOUTH, 30).drive(EDGE, 30).drive(park, 6)
        stop = p.t
        p.stand(14).drive(_north(A, 200), 6).drive(DEPOT, 30).stand(5)
        return p.fixes, stop
    fixes, stop = track(b_point)
    taps = _stops(A={'delivered_at': stop + timedelta(minutes=3)},
                  B={'point': b_point, 'delivered_at': stop + timedelta(minutes=8)})
    (v,) = ac.reconstruct(fixes, taps, DEPOT).visits
    assert v.keys == ('A', 'B') and not v.repeat and v.arrive == stop
    assert [v.keys for v in ac.reconstruct(fixes, _stops(B={'point': b_point}), DEPOT).visits] == [('B',)]
    fixes, _ = track(_north(A, 30))
    assert [v.keys for v in ac.reconstruct(fixes, _stops(B={'point': b_point}), DEPOT).visits] == [('A', 'B')]


def test_shared_site_stops_at_each_store_stay_apart():
    """Место из двух магазинов (A и B в 60 м): постоял 6 мин у A, переехал к B и постоял 7 мин — две стоянки (дальше
    REPOSITION_M), каждая — своему магазину, обе обслуживающие; перестановка на 20 м у A — одна стоянка."""
    b_point = _north(A, 60)
    stops = _stops(B={'point': b_point})
    p = Path(DEPOT, _t(8)).stand(5).drive(SOUTH, 30).drive(EDGE, 30).drive(A, 6)
    p.stand(6).drive(b_point, 5).stand(7).drive(_north(A, 200), 6).drive(DEPOT, 30).stand(5)
    day = ac.reconstruct(p.fixes, stops, DEPOT)
    assert [(v.keys, v.repeat, round(v.minutes, 2)) for v in day.visits] == [(('A',), False, 6.0), (('B',), False, 7.0)]
    p = Path(DEPOT, _t(8)).stand(5).drive(SOUTH, 30).drive(EDGE, 30).drive(A, 6)
    p.stand(6).drive(_north(A, -20), 5).stand(7).drive(_north(A, 200), 6).drive(DEPOT, 30).stand(5)
    assert [(v.keys, v.repeat) for v in ac.reconstruct(p.fixes, stops, DEPOT).visits] == [(('A',), False)]

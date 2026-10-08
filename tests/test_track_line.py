# -*- coding: utf-8 -*-
"""Линия трека машины на карте (владелец 08.10: петли у магазина и прямые сквозь дома — «не профессионально»):
route_optimizer.track_line и кэш привязки views._LiveTracks.

- стоянка — одна точка (середина) в моменты прибытия и отъезда, точек изнутри нет; между стоянками — без дрожания на
  месте (скорость, смещение, без скорости — по смещению) и без маленьких «ёжиков»;
- куски привязки: разрыв по перерыву трека, предел точек, соседние делят край, у растущего хвоста готовые куски те же;
- привязка Valhalla: моменты вершин по привязанным точкам, не убывают; неправдоподобная (объезд, далеко от трека, много
  непривязанных), ошибка запроса — следующий профиль, затем точки куска; Valhalla нет — None (не кэшируется);
- линия: track/track_t 1:1, не длиннее предела, стоянки остаются при упрощении;
- _LiveTracks: кусок привязывается один раз, растущий хвост — только новый кусок, память ограничена, один фоновый поток;
- настоящий Valhalla (если на машине есть тайлы сервера — иначе пропуск): шумный трек вдоль маршрута привязывается.

Синтетические данные, без ERP и без сервера. Запуск из корня проекта:  python -m pytest tests/test_track_line.py -q
"""
import math
import os
import random
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import track_line as tl  # noqa: E402
from route_optimizer.geo import haversine_km  # noqa: E402

Y = ac.YEREVAN
T0 = datetime(2026, 10, 8, 9, 0, tzinfo=Y)
DEPOT = (40.1500, 44.4500)
A = (40.1700, 44.4700)
B = (40.1800, 44.4900)
M_LAT = 110540.0


def north(p, m):
    return (p[0] + m / M_LAT, p[1])


def east(p, m):
    return (p[0], p[1] + m / (111320.0 * math.cos(math.radians(p[0]))))


class Trip:
    """Синтетический трек: park — стоянка (раз в 60 с, дрожание GPS до jitter_m, скорость spd), drive — езда по прямой
    (раз в 15 с, 10 м/с), stand — постоял в пробке (раз в 15 с, дрожание)."""

    def __init__(self, start=T0, seed=1):
        self.t = start
        self.fixes = []
        self.pos = None
        self.rnd = random.Random(seed)

    def _add(self, p, spd, acc=8.0):
        self.fixes.append(ac.TrackFix(self.t, p[0], p[1], acc, spd))

    def park(self, p, minutes, jitter_m=0.0, spd=0.0, step_s=60):
        for i in range(int(minutes * 60 // step_s) + 1):
            if i:
                self.t += timedelta(seconds=step_s)
            q = east(north(p, self.rnd.uniform(-jitter_m, jitter_m)), self.rnd.uniform(-jitter_m, jitter_m))
            self._add(q, spd)
        self.pos = p
        return self

    def drive(self, b, speed_ms=10.0, step_s=15):
        a = self.pos
        steps = max(1, math.ceil(haversine_km(a, b) * 1000 / (speed_ms * step_s)))
        for i in range(1, steps + 1):
            f = i / steps
            self.t += timedelta(seconds=step_s)
            self._add((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), speed_ms)
        self.pos = b
        return self


def _line(fixes, stops=(), depot=DEPOT, matched=None, cap=1500):
    pts = ac.clean_track(fixes)
    actual = ac.reconstruct(fixes, list(stops), depot)
    parts = tl.chunks(pts, actual.stays)
    return tl.line(parts, matched or {}, cap), actual, parts


# ============================== стоянки и дрожание ==============================

def test_stay_is_one_point_at_arrive_and_leave_nothing_inside():
    """Склад и магазин A с дрожанием GPS до 40 м («ёжики» и петли у магазина) — по одной точке на стоянку, дважды."""
    tr = Trip().park(DEPOT, 6, jitter_m=30.0, spd=0.3).drive(A).park(A, 10, jitter_m=40.0, spd=None).drive(B)
    line, actual, parts = _line(tr.fixes, [ac.PlanStop('S:A', 1, A, 100.0)])
    assert [s.kind for s in actual.stays] == ['depot', 'site']
    for s in actual.stays:
        a, b = s.arrive.timestamp(), s.leave.timestamp()
        at_stay = [p for p in line if a <= p[2] <= b]
        assert at_stay == [(s.center[0], s.center[1], a), (s.center[0], s.center[1], b)]
    near_a = [p for p in line if haversine_km(p[:2], A) * 1000 < ac.STOP_RADIUS_M]
    raw_near_a = [f for f in tr.fixes if haversine_km(f.point, A) * 1000 < ac.STOP_RADIUS_M]
    assert len(raw_near_a) > 10 and len(near_a) <= 4          # центр дважды + подъезд/отъезд, не 10+ точек дрожания
    assert [c.stay for c in parts] == [True, False, True, False]
    assert all(x[2] <= y[2] for x, y in zip(line, line[1:]))


def test_overlapping_stays_are_one_point():
    s1 = ac.Stay('site', T0, T0 + timedelta(minutes=5), center=A)
    s2 = ac.Stay('other', T0 + timedelta(minutes=4), T0 + timedelta(minutes=9), center=north(A, 30))
    parts = tl.chunks([], [s2, s1])
    assert len(parts) == 1 and parts[0].stay and parts[0].raw() == [
        (A[0], A[1], T0.timestamp()), (A[0], A[1], (T0 + timedelta(minutes=9)).timestamp())]


def test_jitter_between_stays_dropped_by_speed_and_displacement():
    """Пробка 3 мин (меньше стоянки «не по плану»): со скоростью ниже STILL_MS, без скорости и мелким смещением —
    точек в линии нет; езда — есть."""
    for spd, jitter in ((0.2, 25.0), (None, 20.0), (3.0, 5.0)):
        tr = Trip().park(DEPOT, 5).drive(A)
        stand_from = tr.t
        tr.park(A, 3, jitter_m=jitter, spd=spd, step_s=15)
        stand_to = tr.t
        tr.drive(B)
        line, actual, _ = _line(tr.fixes)
        assert [s.kind for s in actual.stays] == ['depot']
        inside = [p for p in line if stand_from.timestamp() < p[2] <= stand_to.timestamp()]
        assert inside == [], (spd, jitter, inside)
        after = [f for f in tr.fixes if f.at > stand_to]
        assert len([p for p in line if p[2] > stand_to.timestamp()]) == len(after) > 10    # езда к B — вся


def test_small_spike_dropped_real_turn_kept():
    tr = Trip().park(DEPOT, 5).drive(A)
    base = len(tr.fixes)
    tr.drive(B)
    k = base + 10
    f = tr.fixes[k]
    spiked = list(tr.fixes)
    spiked[k] = ac.TrackFix(f.at, *east(tr.fixes[k - 1].point, 80.0), f.accuracy, f.spd)   # 80 м вбок — и назад
    spiked[k + 1] = ac.TrackFix(tr.fixes[k + 1].at, *north(tr.fixes[k - 1].point, 5.0), 8.0, 10.0)
    line, _, _ = _line(spiked)
    assert f.at.timestamp() not in [p[2] for p in line]
    # настоящий поворот (трек дальше уходит от прежней точки) — остаётся
    turn = Trip().park(DEPOT, 5).drive(A).drive(east(A, 400.0))
    line, _, _ = _line(turn.fixes)
    assert any(abs(p[0] - A[0]) < 1e-9 and abs(p[1] - A[1]) < 1e-9 for p in line)


# ============================== куски привязки ==============================

def _run(n, start=T0, step_s=10.0, p0=A):
    return [ac.TrackFix(start + timedelta(seconds=step_s * i), *east(p0, 100.0 * i), 8.0, 10.0) for i in range(n)]


def test_chunks_split_by_gap_and_size_share_edges_and_prefix_is_stable():
    fixes = _run(300)
    parts = tl.chunks(fixes, [])
    assert [len(c.points) for c in parts] == [120, 120, 62]
    assert parts[0].points[-1] == parts[1].points[0] and parts[1].points[-1] == parts[2].points[0]
    grown = tl.chunks(_run(330), [])
    assert [c.key for c in grown[:2]] == [c.key for c in parts[:2]] and grown[2].key != parts[2].key
    gap = _run(50) + _run(50, T0 + timedelta(seconds=10 * 49 + tl.GAP_S + 1), p0=east(A, 6000.0))
    parts = tl.chunks(gap, [])
    assert [len(c.points) for c in parts] == [50, 50] and parts[0].points[-1] != parts[1].points[0]


# ============================== привязка к дорогам ==============================

def _enc(values):
    out = []
    for v in values:
        v = ~(v << 1) if v < 0 else v << 1
        while v >= 0x20:
            out.append(chr((0x20 | (v & 0x1F)) + 63))
            v >>= 5
        out.append(chr(v + 63))
    return ''.join(out)


def encode6(points):
    prev, out = (0, 0), []
    for lat, lon in points:
        q = (round(lat * 1e6), round(lon * 1e6))
        out.append(_enc([q[0] - prev[0], q[1] - prev[1]]))
        prev = q
    return ''.join(out)


def fake_response(raw, shape=None, unmatched=()):
    """Ответ trace_attributes: линия shape (по умолчанию — точки трека), по ребру на отрезок; точка i — на ребре своего
    отрезка в его начале (последняя — в конце предпоследнего); unmatched — непривязанные."""
    shape = shape or [(p[0], p[1]) for p in raw]
    idx = [min(range(len(shape)), key=lambda j: haversine_km(shape[j], p[:2])) for p in raw]
    edges = [{'begin_shape_index': j, 'end_shape_index': j + 1} for j in range(len(shape) - 1)]
    mps = []
    for i, j in enumerate(idx):
        if i in unmatched:
            mps.append({'type': 'unmatched', 'edge_index': 4294967295})
        elif j < len(edges):
            mps.append({'type': 'matched', 'edge_index': j, 'distance_along_edge': 0.0})
        else:
            mps.append({'type': 'matched', 'edge_index': j - 1, 'distance_along_edge': 1.0})
    return {'shape': encode6(shape), 'edges': edges, 'matched_points': mps}


def test_decode6_roundtrip():
    pts = [(40.181234, 44.512345), (40.18, 44.5), (39.9, 45.1)]
    assert tl._decode6(encode6(pts)) == pts


TRIES = (('truck', {'weight': 7.0}), ('auto', {}))


def test_match_takes_road_shape_and_times_follow_matched_points():
    chunk = tl.chunks(_run(10, step_s=10.0), [])[0]
    raw = chunk.raw()
    bend = north(((raw[4][0] + raw[5][0]) / 2, (raw[4][1] + raw[5][1]) / 2), 20.0)   # дорога изгибается между 4 и 5
    shape = [p[:2] for p in raw[:5]] + [bend] + [p[:2] for p in raw[5:]]
    bodies = []

    def trace(body):
        bodies.append(body)
        return fake_response(raw, shape)
    line, ok = tl.match_chunk(chunk, trace, TRIES)
    assert ok and len(line) == 11 and (line[5][0], line[5][1]) == pytest.approx(bend)
    assert raw[4][2] < line[5][2] < raw[5][2] and line[0][2] == raw[0][2] and line[-1][2] == raw[-1][2]
    assert all(x[2] <= y[2] for x, y in zip(line, line[1:]))
    b = bodies[0]
    assert len(bodies) == 1 and b['costing'] == 'truck' and b['costing_options'] == {'truck': {'weight': 7.0}}
    assert b['shape_match'] == 'map_snap' and [s['time'] for s in b['shape']] == [int(p[2]) for p in raw]
    assert b['trace_options'] == {'gps_accuracy': 8.0, 'search_radius': 30.0}


def test_implausible_match_rejected_then_next_profile_then_raw():
    chunk = tl.chunks(_run(10, step_s=10.0), [])[0]
    raw = chunk.raw()
    mid = [p[:2] for p in raw]
    detour = mid[:5] + [north(mid[4], 900.0), north(mid[5], 900.0)] + mid[5:]   # объезд на 1,8 км: длиннее 1,5× + 300 м
    off = mid[:5] + [north(mid[4], 200.0)] + mid[5:]                             # вершина в 200 м от трека: шаг 100 м
    for shape, unmatched in ((detour, ()), (off, ()), (mid, (1, 2, 3, 4))):
        assert tl.match_chunk(chunk, lambda body: fake_response(raw, shape, unmatched), TRIES) == (raw, False)
    # грузовик — неправдоподобно, легковой — хорошо
    asked = []

    def by_profile(body):
        asked.append(body['costing'])
        return fake_response(raw, detour if body['costing'] == 'truck' else mid)
    line, ok = tl.match_chunk(chunk, by_profile, TRIES)
    assert ok and asked == ['truck', 'auto'] and len(line) == len(mid)
    assert all(p[:2] == pytest.approx(q, abs=1e-6) for p, q in zip(line, mid))

    def broken(body):
        raise RuntimeError('No suitable edges near location')
    assert tl.match_chunk(chunk, broken, TRIES) == (raw, False)
    assert tl.match_chunk(chunk, lambda body: {'shape': ''}, TRIES) == (raw, False)    # ответ не разобрать


def test_no_valhalla_gives_none_and_short_chunks_stay_raw():
    chunk = tl.chunks(_run(10), [])[0]
    assert tl.match_chunk(chunk, lambda body: None, TRIES) is None
    short = tl.chunks(_run(2), [])[0]
    assert tl.match_chunk(short, lambda body: pytest.fail('не спрашивать'), TRIES) == (short.raw(), False)


# ============================== линия ==============================

def test_line_uses_matched_where_ready_and_cap_keeps_stays():
    tr = Trip().park(DEPOT, 5).drive(A, step_s=5).park(A, 8).drive(B, step_s=5).park(B, 8).drive(DEPOT, step_s=5)
    stops = [ac.PlanStop('S:A', 1, A, 1.0), ac.PlanStop('S:B', 2, B, 1.0)]
    full, actual, parts = _line(tr.fixes, stops)
    moves = [c for c in parts if not c.stay]
    shifted = {moves[0].key: [(p[0] + 0.0003, p[1], p[2]) for p in moves[0].raw()]}
    line, _, _ = _line(tr.fixes, stops, matched=shifted)
    assert len(line) == len(full) and sum(1 for x, y in zip(line, full) if x != y) == len(moves[0].points)
    small, _, _ = _line(tr.fixes, stops, cap=10)
    assert 2 * len(actual.stays) <= len(small) <= 10
    for s in actual.stays:
        assert (s.center[0], s.center[1], s.arrive.timestamp()) in small
        assert (s.center[0], s.center[1], s.leave.timestamp()) in small
    assert all(x[2] <= y[2] for x, y in zip(small, small[1:]))
    assert tl.line([], {}, 10) == []


# ============================== кэш привязки (views) ==============================

@pytest.fixture
def views_mod(monkeypatch):
    from route_optimizer import views
    monkeypatch.setattr(views, 'LIVE_ROAD_BACKGROUND', False)
    return views


DAY = T0.date()


def test_live_tracks_match_once_grow_tail_only_and_bounded(views_mod, monkeypatch):
    cache = views_mod._LiveTracks()
    calls = []

    def match(c):
        calls.append(c.key)
        return [(p[0] + 0.001, p[1], p[2]) for p in c.raw()], True
    parts = tl.chunks(_run(250), [])
    got = cache.get(DAY, 'CAR1', parts, match)
    assert len(calls) == 3 and set(got) == {c.key for c in parts}
    assert cache.get(DAY, 'CAR1', parts, match) == got and len(calls) == 3           # готовое — без привязки
    grown = tl.chunks(_run(300), [])
    calls.clear()
    cache.get(DAY, 'CAR1', grown, match)
    assert calls == [grown[2].key]                                                   # растущий хвост — только он
    assert len(cache.get(DAY, 'CAR2', grown, match)) == 3                             # другая машина — свои куски
    # память: вершин не больше предела — уходят давно не спрошенные
    monkeypatch.setattr(views_mod, 'LIVE_TRACK_POINTS', 250)
    cache.get(DAY, 'CAR3', tl.chunks(_run(200), [], ), match)
    assert cache._size <= 250 and cache._size == sum(len(v) for v in cache._items.values())


def test_live_tracks_unavailable_not_cached_and_no_matcher(views_mod):
    cache = views_mod._LiveTracks()
    parts = tl.chunks(_run(50), [])
    assert cache.get(DAY, 'CAR1', parts, lambda c: None) == {} and not cache._items   # Valhalla нет — не кэшируем
    assert cache.get(DAY, 'CAR1', parts, None) == {}
    assert cache.get(DAY, 'CAR1', parts, lambda c: (c.raw(), False)) == {parts[0].key: parts[0].raw()}
    state = type('S', (), {'valhalla': None})()
    assert views_mod._track_matcher(state, 3000.0) is None


def test_live_tracks_one_background_worker_and_never_waits(views_mod, monkeypatch):
    monkeypatch.setattr(views_mod, 'LIVE_ROAD_BACKGROUND', True)
    cache = views_mod._LiveTracks()
    gate, started = threading.Event(), []

    def slow(c):
        started.append(c.key)
        gate.wait(10)
        return c.raw(), True
    parts = tl.chunks(_run(50), [])
    other = tl.chunks(_run(50, p0=B), [])
    t = time.monotonic()
    assert cache.get(DAY, 'CAR1', parts, slow) == {}
    assert cache.get(DAY, 'CAR2', other, slow) == {}                                 # занят — следующий опрос
    assert time.monotonic() - t < 1.0
    gate.set()
    end = time.monotonic() + 5
    while cache._busy and time.monotonic() < end:
        time.sleep(0.01)
    assert started == [parts[0].key] and set(cache.get(DAY, 'CAR1', parts, slow)) == {parts[0].key}


def test_live_tracks_matcher_exception_is_logged_not_raised(views_mod):
    cache = views_mod._LiveTracks()

    def boom(c):
        raise ValueError('boom')
    assert cache.get(DAY, 'CAR1', tl.chunks(_run(50), []), boom) == {} and not cache._busy


# ============================== настоящий Valhalla (тайлы сервера, если есть) ==============================

def _server_build():
    from route_optimizer import valhalla_engine as ve
    if not ve.valhalla_supported():
        return None
    root = os.environ.get('PROGRAMDATA') or os.path.join(os.path.expanduser('~'), '.cache')
    return ve.current_build(os.path.join(root, 'route_optimizer', 'valhalla'))


def test_real_valhalla_snaps_noisy_track_to_the_road():
    """Маршрут по дорогам Еревана (Actor.route) → точки вдоль него с шумом GPS 15 м раз в ~10 с → привязка
    правдоподобна, линия — рядом с маршрутом, моменты не убывают. Тайлов нет — пропуск."""
    build = _server_build()
    if build is None:
        pytest.skip('тайлов Valhalla на этой машине нет')
    from route_optimizer import valhalla_engine as ve
    engine = ve._Engine(build)

    def trace(body):
        with engine.actor() as actor:
            return actor.trace_attributes(body)
    with engine.actor() as actor:
        res = actor.route({'locations': [{'lat': 40.1776, 'lon': 44.5126}, {'lat': 40.1860, 'lon': 44.5150}],
                           'costing': 'auto'})
    road = tl._decode6(res['trip']['legs'][0]['shape'])
    cum = [0.0]
    for a, b in zip(road, road[1:]):
        cum.append(cum[-1] + haversine_km(a, b) * 1000)
    rnd = random.Random(3)
    fixes, s = [], 0.0
    while s <= cum[-1]:
        j = max(0, min(len(road) - 2, next((k for k in range(len(cum) - 1) if cum[k + 1] >= s), len(road) - 2)))
        f = (s - cum[j]) / ((cum[j + 1] - cum[j]) or 1)
        p = (road[j][0] + (road[j + 1][0] - road[j][0]) * f, road[j][1] + (road[j + 1][1] - road[j][1]) * f)
        p = east(north(p, rnd.gauss(0, 8)), rnd.gauss(0, 8))
        fixes.append(ac.TrackFix(T0 + timedelta(seconds=len(fixes) * 10), p[0], p[1], 10.0, 10.0))
        s += 100.0
    chunk = tl.chunks(fixes, [])[0]
    line, ok = tl.match_chunk(chunk, trace, (('truck', ve.truck_costing(3000.0)), ('auto', ve.CAR_COSTING)))
    assert ok and len(line) >= 2
    assert all(x[2] <= y[2] for x, y in zip(line, line[1:]))
    assert max(min(tl._seg_m(p, a, b) for a, b in zip(road, road[1:])) for p in line) < 40.0

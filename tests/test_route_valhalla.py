# -*- coding: utf-8 -*-
"""Дороги Valhalla и направленные матрицы (план learning-loop, этап 2).

- valhalla_engine: матрицы км и минут, кэш (повторное чтение, дозаполнение = полный расчёт, чужая сборка), пары без
  пути и непривязанные точки — запасной путь, режим valhalla_time, профиль грузовика, сборка тайлов с отпечатком,
  нет pyvalhalla — граф OSM. Движок — подделка FakeActor с направленной метрикой: ни pyvalhalla, ни карта не нужны;
- направленные матрицы: 2-opt (tsp, search, fleet) не удлиняет тур и даёт локальный оптимум по полному пересчёту
  всех разворотов; симметричная матрица — те же туры, что прежний расчёт; вставка и удаление в туре — как полный
  пересчёт; Кларк–Райт не сливает рейсы длиннее дня; матрицы оптимизатора и парка — в обе стороны.
Запуск из корня проекта:  python -m pytest tests/test_route_valhalla.py -q
"""
import json
import os
import random
import sys
import types
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip('numpy')

from route_optimizer import evaluate as ev  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import geo  # noqa: E402
from route_optimizer import optimize as opt  # noqa: E402
from route_optimizer import search as sr  # noqa: E402
from route_optimizer import tsp  # noqa: E402
from route_optimizer import valhalla_engine as ve  # noqa: E402
from route_optimizer import views  # noqa: E402

CENTER = (40.18, 44.51)
NORMS = ev.Norms(work_minutes=480.0, detour=1.3, speed_city_kmh=20.0, speed_region_kmh=40.0, city_center=CENTER,
                 city_radius_km=12.0, min_day_revenue=0.0, min_trip_revenue=0.0)
# город (до 12 км от центра) и область; FAR — дальше 0,5 км от дороги, ISLAND — «другой регион» без путей
P = [(40.18, 44.50), (40.20, 44.52), (40.17, 44.55), (40.22, 44.48), (40.40, 44.70), (40.15, 44.45)]
FAR = (40.50, 44.90)
ISLAND = (40.60, 45.00)
SNAP_DLAT = 0.0001   # подделка привязывает точку к дороге в ~11 м севернее


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Ни карты, ни тайлов на диске: тесты не зависят от машины."""
    monkeypatch.setenv('ROUTES_OSM_PATH', str(tmp_path / 'no-map.osm.pbf'))
    monkeypatch.setenv('ROUTES_VALHALLA_DIR', str(tmp_path / 'valhalla'))
    monkeypatch.delenv('ROUTES_ROAD_ENGINE', raising=False)


# --- Подделка движка ---

def _metric(a, b, costing='auto'):
    """Направленная метрика подделки: на север — объезд ×1,5 и 20 км/ч, на юг — ×1,1 и 30 км/ч; грузовик на 25%
    медленнее. (км, секунды)."""
    d = geo.haversine_km(a, b)
    north = b[0] > a[0]
    km = d * (1.5 if north else 1.1)
    sec = km / (20.0 if north else 30.0) * 3600.0 * (1.25 if costing == 'truck' else 1.0)
    return km, sec


class FakeActor:
    log: list = []

    def __init__(self, config):
        self.config = config

    def locate(self, body):
        out = []
        for loc in body['locations']:
            p = (loc['lat'], loc['lon'])
            q = (p[0] + 0.02, p[1]) if p == FAR else (p[0] + SNAP_DLAT, p[1])
            out.append({'edges': [{'correlated_lat': q[0], 'correlated_lon': q[1]}]})
        return out

    def matrix(self, body):
        src = [(x['lat'], x['lon']) for x in body['sources']]
        dst = [(x['lat'], x['lon']) for x in body['targets']]
        FakeActor.log.append((body['costing'], len(src), len(dst)))
        if ISLAND in src + dst and len(src) * len(dst) > 1:
            raise RuntimeError('Locations are in unconnected regions')
        dist, dur = [], []
        for a in src:
            km_row, sec_row = [], []
            for b in dst:
                if ISLAND in (a, b) and a != b:
                    km_row.append(None)
                    sec_row.append(None)
                    continue
                km, sec = _metric(a, b, body['costing'])
                km_row.append(round(km, 3))
                sec_row.append(round(sec))
            dist.append(km_row)
            dur.append(sec_row)
        return {'sources_to_targets': {'distances': dist, 'durations': dur}, 'units': 'kilometers'}


def _fake_config(tile_extract='', tile_dir=''):
    return {'mjolnir': {'tile_dir': tile_dir}, 'thor': {}, 'service_limits': {'auto': {}, 'truck': {}}}


FAKE = types.SimpleNamespace(Actor=FakeActor, __version__='9.9.9-test', get_config=_fake_config)


@pytest.fixture
def fake(monkeypatch):
    FakeActor.log = []
    monkeypatch.setattr(ve, 'valhalla_module', lambda: FAKE)
    return FakeActor


def _matrices(folder, name='tiles-test'):
    os.makedirs(folder, exist_ok=True)
    engine = ve._Engine(ve.Build('test', name, os.path.join(folder, 'valhalla.json'), {}))
    return {p: ve._ProfileMatrix(engine, p, os.path.join(folder, f'matrix-{name}-{p}.npz')) for p in ve.COSTING}


def _snap(p):
    return geo.haversine_km(p, (p[0] + SNAP_DLAT, p[1]))


def _city(a, b):
    return geo.in_city(a, CENTER, 12.0) and geo.in_city(b, CENTER, 12.0)


class FakeOsm:
    """Граф OSM для проверки запасного пути: км = по прямой × 1,2; точек missing в графе нет (None)."""
    version, failed = 'osm-test', False

    def __init__(self, missing=(FAR,)):
        self.ensured = []
        self.missing = set(missing)

    def ensure(self, points):
        self.ensured.append(list(points))

    def km(self, a, b):
        return None if self.missing & {a, b} else geo.haversine_km(a, b) * 1.2

    def minutes(self, a, b, city):
        return None

    def truck(self):
        return self

    def unsnapped(self, points):
        return 0


# --- Valhalla: матрицы и кэш ---

def test_valhalla_km_and_minutes_are_directed(tmp_path, fake):
    r = ve.ValhallaRoads(_matrices(str(tmp_path)), ve.PROFILE_CAR)
    r.ensure(P)
    a, b = P[0], P[1]                                       # b севернее a
    there, back = _metric(a, b), _metric(b, a)
    assert r.km(a, b) == pytest.approx(there[0] + _snap(a) + _snap(b), abs=2e-3)
    assert r.km(b, a) == pytest.approx(back[0] + _snap(a) + _snap(b), abs=2e-3)
    assert r.km(a, b) > r.km(b, a)
    assert r.minutes(a, b, True) == pytest.approx(there[1] / 60 * ve.TIME_FACTOR[True], abs=0.05)
    assert r.minutes(b, a, False) == pytest.approx(back[1] / 60 * ve.TIME_FACTOR[False], abs=0.05)
    assert r.km(a, a) == 0.0 and r.minutes(a, a, True) is None
    assert r.unsnapped(P) == 0 and r.size == (len(P), len(P))


def test_valhalla_cache_reread_incremental_equals_full_and_other_build_recomputed(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(ve, 'BATCH', 2)                     # несколько блоков — расчёт в потоках
    full = ve.ValhallaRoads(_matrices(str(tmp_path / 'full')), ve.PROFILE_CAR)
    full.ensure(P)
    folder = str(tmp_path / 'inc')
    inc = ve.ValhallaRoads(_matrices(folder), ve.PROFILE_CAR)
    inc.ensure(P[:2])
    inc.ensure(P[2:])
    for a in P:
        for b in P:
            assert inc.km(a, b) == pytest.approx(full.km(a, b), abs=1e-6)
            assert (inc.minutes(a, b, True) is None) == (full.minutes(a, b, True) is None)
    assert os.path.exists(os.path.join(folder, 'matrix-tiles-test-auto.npz'))
    FakeActor.log = []
    again = ve.ValhallaRoads(_matrices(folder), ve.PROFILE_CAR)   # «перезапуск сервера»: всё из кэша
    again.ensure(P)
    assert FakeActor.log == []
    assert again.km(P[3], P[4]) == pytest.approx(full.km(P[3], P[4]), abs=1e-6)
    other = _matrices(folder, 'tiles-other')                # другая сборка — свой файл и пересчёт
    other[ve.PROFILE_CAR]._cache_path = os.path.join(folder, 'matrix-tiles-test-auto.npz')   # даже чужой файл
    ve.ValhallaRoads(other, ve.PROFILE_CAR).ensure(P)
    assert FakeActor.log, 'кэш другой сборки не годится'


def test_valhalla_corrupt_cache_is_recomputed(tmp_path, fake):
    folder = str(tmp_path)
    ve.ValhallaRoads(_matrices(folder), ve.PROFILE_CAR).ensure(P[:3])
    Path(folder, 'matrix-tiles-test-auto.npz').write_bytes(b'not a zip')
    FakeActor.log = []
    r = ve.ValhallaRoads(_matrices(folder), ve.PROFILE_CAR)
    r.ensure(P[:3])
    assert FakeActor.log and r.km(P[0], P[1]) is not None


def test_valhalla_unsnapped_and_unreachable_fall_back(tmp_path, fake):
    pts = [*P, FAR, ISLAND]
    alone = ve.ValhallaRoads(_matrices(str(tmp_path / 'a')), ve.PROFILE_CAR)
    alone.ensure(pts)
    assert alone.unsnapped(pts) == 1                        # FAR: дорога дальше 0,5 км
    assert alone.km(FAR, P[0]) is None and alone.km(ISLAND, P[0]) is None
    assert alone.km(P[0], P[1]) is not None                 # сбой блока с ISLAND не задел остальные пары
    assert ('auto', 1, 1) in FakeActor.log                  # блок делился до пары
    assert alone.minutes(ISLAND, P[0], True) is None
    norms = replace(NORMS, roads=alone)
    assert norms.km(ISLAND, P[0]) == pytest.approx(geo.haversine_km(ISLAND, P[0]) * 1.3)   # по прямой × извилистость
    assert norms.leg_speed(ISLAND, P[0], 5.0, False) == NORMS.speed_region_kmh
    osm = FakeOsm()
    backed = ve.ValhallaRoads(_matrices(str(tmp_path / 'b')), ve.PROFILE_CAR, osm)
    backed.ensure(pts)
    assert backed.km(ISLAND, P[0]) == pytest.approx(geo.haversine_km(ISLAND, P[0]) * 1.2)   # граф OSM
    assert osm.ensured == []                                # км — Valhalla: граф всем набором не нужен


def test_valhalla_time_only_takes_osm_km_and_valhalla_minutes(tmp_path, fake):
    gap = P[5]                                              # точки нет в графе OSM
    osm = FakeOsm(missing=(gap,))
    hybrid = ve.ValhallaRoads(_matrices(str(tmp_path)), ve.PROFILE_CAR, osm, time_only=True)
    hybrid.ensure(P)
    assert osm.ensured and set(osm.ensured[0]) == set(P)    # граф — одним набором
    a, b = P[0], P[1]
    assert hybrid.km(a, b) == pytest.approx(osm.km(a, b))
    assert hybrid.km(gap, a) == pytest.approx(_metric(gap, a)[0] + _snap(gap) + _snap(a), abs=2e-3)   # км Valhalla
    norms = replace(NORMS, roads=hybrid)
    km, minutes = ev.route_metrics(P[:4], None, norms)
    want = sum(hybrid.minutes(x, y, _city(x, y)) for x, y in zip(P[:4], P[1:4]))
    assert km == pytest.approx(sum(osm.km(x, y) for x, y in zip(P[:4], P[1:4])))
    assert minutes == pytest.approx(want)                   # время — Valhalla × поправка, не км / скорость
    assert 'valhalla_time' in hybrid.version and hybrid.truck().time_only


def test_norms_for_trucks_and_fleet_matrices_use_truck_profile(tmp_path, fake):
    car = ve.ValhallaRoads(_matrices(str(tmp_path)), ve.PROFILE_CAR)
    norms = replace(NORMS, roads=car)
    trucks = norms.for_trucks()
    assert trucks.roads.profile == ve.PROFILE_TRUCK and norms.roads.profile == ve.PROFILE_CAR
    assert NORMS.for_trucks() is NORMS                      # без дорог — те же нормы
    depot, pts = P[0], P[1:5]
    d, m = fl._matrices(pts, depot, norms)                  # сам переходит на профиль грузовика
    allp = [depot, *pts]
    for i in range(len(allp)):
        for j in range(len(allp)):
            if i == j:
                continue
            a, b = allp[i], allp[j]
            assert d[i][j] == pytest.approx(trucks.roads.km(a, b))
            want = trucks.roads.minutes(a, b, _city(a, b))
            assert m[i][j] == pytest.approx(want)
            assert m[i][j] > car.minutes(a, b, _city(a, b))  # грузовик медленнее машины менеджера
    assert d[0][1] != d[1][0]


# --- Valhalla: сборка тайлов, режимы, нет пакета ---

def test_build_tiles_idempotent_versioned_and_retried(tmp_path, monkeypatch, fake):
    runs = []

    def run(tool, config, pbf):
        runs.append(tool)
        Path(json.loads(Path(config).read_text(encoding='utf-8'))['mjolnir']['tile_dir'], 'tile.gph').write_text('x')

    monkeypatch.setattr(ve, '_run', run)
    pbf, base = tmp_path / 'map.osm.pbf', str(tmp_path / 'v')
    pbf.write_bytes(b'map-1')
    first = ve.build_tiles(str(pbf), base)
    assert runs == ['valhalla_build_admins', 'valhalla_build_tiles']
    info = json.loads(Path(base, 'current.json').read_text(encoding='utf-8'))
    assert info['pbf_signature'] == ve.map_signature(str(pbf)) and info['valhalla'] == '9.9.9-test'
    assert info['format'] == ve.BUILD_FORMAT and ve.current_build(base).name == first.name
    assert ve.build_tiles(str(pbf), base).name == first.name and len(runs) == 2   # та же сборка — ничего
    Path(base, f'matrix-{first.name}-auto.npz').write_bytes(b'old')
    forced = ve.build_tiles(str(pbf), base, force=True)
    assert forced.name != first.name and forced.id == first.id
    assert not Path(base, first.name).exists() and not Path(base, f'matrix-{first.name}-auto.npz').exists()
    pbf.write_bytes(b'map-2, other size')
    assert ve.build_tiles(str(pbf), base).id != first.id   # новая карта — новая сборка
    # падение сборщика: повтор; все попытки упали — прежняя сборка остаётся действующей
    current = ve.current_build(base).name
    fails = iter([True, False, False])

    def flaky(tool, config, pbf_path):
        if tool == 'valhalla_build_tiles' and next(fails):
            raise RuntimeError('crash')
        run(tool, config, pbf_path)

    monkeypatch.setattr(ve, '_run', flaky)
    assert ve.build_tiles(str(pbf), base, force=True).name != current

    def broken(tool, config, pbf_path):
        raise RuntimeError('crash')

    current = ve.current_build(base).name
    monkeypatch.setattr(ve, '_run', broken)
    with pytest.raises(RuntimeError):
        ve.build_tiles(str(pbf), base, force=True)
    assert ve.current_build(base).name == current
    assert sorted(p.name for p in Path(base).iterdir() if p.name.startswith('tiles-')) == [current]


def test_provider_modes_autobuild_and_no_map(tmp_path, monkeypatch, fake):
    builds = []
    monkeypatch.setattr(ve, '_run', lambda tool, config, pbf: builds.append(tool))
    pbf, base = tmp_path / 'map.osm.pbf', str(tmp_path / 'v')
    provider = ve.ValhallaProvider(base, str(pbf))
    assert ve.engine_mode() == ve.DEFAULT_ENGINE
    assert provider.get() is None                           # карты нет — и Valhalla нет
    pbf.write_bytes(b'map')
    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'osm')
    assert provider.get() is None and builds == []
    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'nonsense')
    assert ve.engine_mode() == 'osm' and provider.get() is None
    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'valhalla')
    roads = provider.get()
    assert roads is not None and not roads.time_only and len(builds) == 2   # тайлы собраны при первом обращении
    assert provider.get() is not None and len(builds) == 2
    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'valhalla_time')
    assert provider.get(FakeOsm()).time_only


def test_missing_pyvalhalla_falls_back_to_osm_graph(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'valhalla', None)     # import valhalla → ImportError
    assert ve.valhalla_module() is None and not ve.valhalla_supported()
    pbf = tmp_path / 'map.osm.pbf'
    pbf.write_bytes(b'map')
    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'valhalla')
    provider = ve.ValhallaProvider(str(tmp_path / 'v'), str(pbf))
    assert provider.get() is None
    assert ve.open_valhalla() is None
    osm = FakeOsm()
    monkeypatch.setattr(views.evaluate, 'plan_points', lambda *args: [P[0]])
    state = NS(roads=NS(get=lambda: osm), valhalla=provider)
    assert views._roads(state, None, None) is osm and osm.ensured == [[P[0]]]
    with pytest.raises(RuntimeError, match='pyvalhalla'):
        ve.build_tiles(str(pbf), str(tmp_path / 'v'))


# --- Направленные матрицы: 2-opt ---

def _length(t, d):
    return sum(d[a][b] for a, b in zip(t, [*t[1:], t[0]]))


def _matrix(n, rng, symmetric=False):
    d = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                d[i][j] = rng.uniform(1.0, 10.0)
    if symmetric:
        for i in range(n):
            for j in range(i):
                d[i][j] = d[j][i]
    return d


def _old_two_opt(tour, dist, max_passes=100):
    """Прежний 2-opt (до этапа 2): Δ только по четырём концевым рёбрам — верно лишь для симметричной матрицы.
    На направленной матрице он может ходить по кругу без конца — проходов не больше max_passes."""
    t = list(tour)
    n = len(t)
    if n < 4:
        return t
    improved = True
    while improved and max_passes:
        max_passes -= 1
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b = t[i - 1], t[i]
                c, d = t[j], t[(j + 1) % n]
                if dist[a][c] + dist[b][d] - dist[a][b] - dist[c][d] < -1e-12:
                    t[i:j + 1] = t[i:j + 1][::-1]
                    improved = True
    return t


def _no_better_reversal(t, d):
    """Перебор всех разворотов кусков t[i..j] (t[0] на месте) с полным пересчётом длины."""
    base = _length(t, d)
    return all(_length(t[:i] + t[i:j + 1][::-1] + t[j + 1:], d) >= base - 1e-9
               for i in range(1, len(t) - 1) for j in range(i + 1, len(t)))


@pytest.mark.parametrize('two_opt', [tsp.two_opt, lambda t, d: sr.two_opt(list(t), d)], ids=['tsp', 'search'])
def test_two_opt_directed_never_longer_and_locally_optimal(two_opt):
    rng = random.Random(7)
    longer_before = 0
    for _ in range(300):
        n = rng.randint(4, 8)
        d = _matrix(n, rng)
        start = [0, *rng.sample(range(1, n), n - 1)]
        t = two_opt(start, d)
        assert t[0] == 0 and sorted(t) == list(range(n))
        assert _length(t, d) <= _length(start, d) + 1e-9
        assert _no_better_reversal(t, d)
        longer_before += _length(_old_two_opt(start, d), d) > _length(start, d) + 1e-9
    assert longer_before > 0                                # прежний расчёт на таких матрицах удлинял туры


@pytest.mark.parametrize('two_opt', [tsp.two_opt, lambda t, d: sr.two_opt(list(t), d)], ids=['tsp', 'search'])
def test_two_opt_symmetric_matrix_same_as_before(two_opt):
    rng = random.Random(11)
    for _ in range(200):
        n = rng.randint(4, 9)
        d = _matrix(n, rng, symmetric=True)
        start = [0, *rng.sample(range(1, n), n - 1)]
        assert two_opt(start, d) == _old_two_opt(start, d)


def test_fleet_two_opt_directed_and_window_guard():
    rng = random.Random(3)
    for _ in range(200):
        n = rng.randint(3, 7)
        d = _matrix(n + 1, rng)
        stops = [fl._Stop(k + 1, 10.0, 0.0, 5.0) for k in range(n)]
        seq = rng.sample(range(n), n)
        out = fl._two_opt(list(seq), stops, d)
        assert sorted(out) == list(range(n))
        assert fl._closed(out, stops, d) <= fl._closed(seq, stops, d) + 1e-9
        tour = [0, *(stops[v].node for v in out)]
        assert _no_better_reversal(tour, d)
        guarded = fl._two_opt(list(seq), stops, d, lambda s2: s2[0] == seq[0])   # ход — только если допустим
        assert guarded[0] == seq[0] and fl._closed(guarded, stops, d) <= fl._closed(seq, stops, d) + 1e-9


def test_insertion_and_removal_match_full_recount():
    rng = random.Random(5)
    for _ in range(300):
        n = rng.randint(3, 8)
        d = _matrix(n + 1, rng)
        t = [0, *rng.sample(range(1, n + 1), n - 1)]
        x = next(v for v in range(1, n + 1) if v not in t)
        pos, delta, a, b = sr._insertion(t, x, d)
        best = min(_length(t[:p] + [x] + t[p:], d) for p in range(1, len(t) + 1)) - _length(t, d)
        assert delta == pytest.approx(best)
        assert _length(t[:pos] + [x] + t[pos:], d) - _length(t, d) == pytest.approx(delta)
        y = t[rng.randrange(1, len(t))]
        pos, delta, a, b = sr._removal(t, y, d)
        assert delta == pytest.approx(_length([v for v in t if v != y], d) - _length(t, d))


def test_savings_never_merges_beyond_window_on_directed_minutes():
    rng = random.Random(9)
    for _ in range(200):
        n = rng.randint(4, 9)
        m = _matrix(n + 1, rng)
        stops = [fl._Stop(k + 1, 10.0, 0.0, 1.0) for k in range(n)]
        window = rng.uniform(15.0, 35.0)
        routes = fl._savings(list(range(n)), stops, m, m, 1e9, window)
        assert sorted(v for r in routes for v in r) == list(range(n))
        for r in routes:
            if len(r) > 1:                                  # слитый рейс укладывается в окно точно
                assert fl._closed(r, stops, m) + sum(stops[v].unload for v in r) <= window + 1e-9


# --- Направленные матрицы оптимизатора, парка и порядка объезда ---

class NorthRoads:
    """Направленный «граф»: на север — ×1,5 к прямой, на юг — ×1,0; времени не знает."""
    version, failed = 'north', False

    def ensure(self, points):
        pass

    def km(self, a, b):
        return geo.haversine_km(a, b) * (1.5 if b[0] > a[0] else 1.0)

    def minutes(self, a, b, city):
        return None

    def truck(self):
        return self

    def unsnapped(self, points):
        return 0


def test_optimizer_fleet_and_tsp_matrices_are_directed():
    norms = replace(NORMS, roads=NorthRoads())
    home, depot, pts = (40.19, 44.53), P[0], [P[1], P[2], P[3], P[4]]
    km, mins = opt._matrices(home, pts, norms)
    allp = [home, *pts]
    for i in range(len(allp)):
        for j in range(len(allp)):
            if i != j:
                a, b = allp[i], allp[j]
                assert km[i][j] == pytest.approx(norms.km(a, b))
                speed = NORMS.speed_city_kmh if _city(a, b) else NORMS.speed_region_kmh
                assert mins[i][j] == pytest.approx(norms.km(a, b) / speed * 60)
    assert km[0][1] != km[1][0]
    dist = opt._Distances(pts, norms)
    km2, mins2 = dist.matrices(home, pts)                    # «то же, что _matrices»
    assert [x for r in km2 for x in r] == pytest.approx([x for r in km for x in r])
    assert [x for r in mins2 for x in r] == pytest.approx([x for r in mins for x in r])
    rows = dist.rows(depot, pts)
    for j, p in enumerate(pts, 1):
        assert rows[0][j] == pytest.approx(norms.km(depot, p)) and rows[j][0] == pytest.approx(norms.km(p, depot))
    km0, _ = opt._matrices(None, pts, norms)                 # без дома — открытый путь: нули
    assert all(km0[0][j] == 0.0 and km0[j][0] == 0.0 for j in range(len(km0)))
    d, _ = fl._matrices(pts, depot, norms)
    assert d[0][1] == pytest.approx(norms.km(depot, pts[0])) and d[1][0] == pytest.approx(norms.km(pts[0], depot))
    dm = tsp.distance_matrix(pts, norms.km)
    assert dm[0][1] == pytest.approx(norms.km(pts[0], pts[1])) and dm[1][0] == pytest.approx(norms.km(pts[1], pts[0]))
    heavy = tsp.delivery_km(depot, [(P[1], 250.0)], 100.0, norms.km)
    assert heavy.km == pytest.approx(3 * (norms.km(depot, P[1]) + norms.km(P[1], depot)))


def test_plan_export_shift_gate_uses_road_minutes():
    """Выгрузка плана проверяет смену тем же временем в пути, что и расчёт (Valhalla — его минуты)."""
    from test_route_optimizer import _bundle, make_snapshot

    class SlowRoads(NorthRoads):
        def minutes(self, a, b, city):
            return 600.0                                     # 10 часов на участок

    bundle = _bundle()
    bundle = replace(bundle, settings=dict(bundle.settings, work_start='09:00', work_end='20:00',
                                           visit_min_small=1, visit_min_medium=1, visit_min_large=1))
    distance = lambda a, b: 0.0 if a == b else 10.0          # noqa: E731
    assert opt.plan_export(make_snapshot(), bundle, [], distance=distance)['time_gate']['ok']
    slow = opt.plan_export(make_snapshot(), bundle, [], distance=distance, roads=SlowRoads())
    assert not slow['time_gate']['ok'] and slow['time_gate']['days']

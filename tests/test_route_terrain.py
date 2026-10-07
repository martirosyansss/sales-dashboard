# -*- coding: utf-8 -*-
"""Рельеф в расходе дизеля (ответ владельца №85): высоты SRTM (terrain), подъёмы участков (roads.RoadDistances.climb),
литры подъёма (running_costs.route_cost), порядок объезда и PyVRP (fleet, vrp), рейс «Развоза» (dispatch.plan_view).

Синтетические данные: сетка дорог из test_route_center_bypass с высотами узлов, тайлы высот — маленькие файлы во
временной папке; карты, ERP и сети не нужно.
Запуск из корня проекта:  python -m pytest tests/test_route_terrain.py -q
"""
import gzip
import math
import os
import random
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip('numpy')
pytest.importorskip('scipy')

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import roads as rd  # noqa: E402
from route_optimizer import running_costs as rc  # noqa: E402
from route_optimizer import terrain  # noqa: E402
from route_optimizer import vrp  # noqa: E402
from route_optimizer.running_costs import (TERRAIN_K, TERRAIN_U_BAR, curb_tonnes, mean_climb, route_cost,  # noqa: E402
                                           terrain_liters)
from test_route_center_bypass import GRAPH, _at, _map  # noqa: E402
from test_route_optimizer import DP_DAY, DP_NORMS, TN, _dp_stops, _info, client  # noqa: E402,F401

RISE = 50.0   # м на шаг сетки к северу (0,4 км: 12,5% — круче CLIMB_C)
Z = np.array([RISE * round((lat - _at(0, 0)[0]) / (_at(1, 0)[0] - _at(0, 0)[0])) for lat in GRAPH.lat])
CAP_M = terrain.CLIMB_C * 1000.0 * 0.4   # спуск на шаге сетки «экономит» не больше 6 м
PLAIN = fl.FleetTruck('P', 'plain', 2500.0, 10.0)                              # без норм по загрузке
LOADED = fl.FleetTruck('L', 'loaded', 2500.0, 15.0, fuel_empty_l_per_100km=10.0, fuel_full_l_per_100km=17.0)


def _hilly(elevation=Z):
    return rd.RoadDistances.for_graph(GRAPH, elevation=elevation)


# ============================== литры подъёма ==============================

def test_route_cost_without_climbs_is_unchanged():
    """Без подъёмов — прежний расчёт до байта (те же числа и порядок арифметики), полей рельефа нет (None)."""
    for truck in (PLAIN, LOADED):
        got = route_cost([3.0, 4.5, 2.25], [700.0, 300.0], truck)
        assert got == route_cost([3.0, 4.5, 2.25], [700.0, 300.0], truck, None)
        assert got.terrain_liters is None and got.climb_m is None
    assert route_cost([3.0, 4.5, 2.25], [700.0, 300.0], PLAIN).liters == 9.75 * 10.0 / 100.0
    loaded = route_cost([3.0, 4.5, 2.25], [700.0, 300.0], LOADED).liters
    assert loaded == 3.0 * (10.0 + 7.0 * 0.4) / 100.0 + 4.5 * (10.0 + 7.0 * 0.12) / 100.0 + 2.25 * 10.0 / 100.0


def test_uphill_costs_more_and_downhill_never_refunds_more_than_flat(monkeypatch):
    """Подъём — дороже ровной дороги на k·m·U; спуск дешевле, но участок не уходит ниже нуля литров."""
    monkeypatch.setattr(rc, 'TERRAIN_U_BAR', 0.0)
    flat = route_cost([10.0], [], PLAIN)
    up = route_cost([10.0], [], PLAIN, [100.0])
    assert up.terrain_liters == pytest.approx(TERRAIN_K * curb_tonnes(2500.0) * 100.0)
    assert up.liters == pytest.approx(flat.liters + up.terrain_liters) and up.liters > flat.liters
    assert up.climb_m == 100.0
    down = route_cost([10.0], [], PLAIN, [-50.0])
    assert flat.liters > down.liters > 0
    cliff = route_cost([1.0, 1.0], [2000.0], LOADED, [-1e6, 0.0])   # невозможный спуск — участок не ниже нуля
    leg = 1.0 * (10.0 + 7.0 * 0.8) / 100.0
    assert cliff.terrain_liters == pytest.approx(-leg)
    assert cliff.liters == pytest.approx(route_cost([1.0, 1.0], [2000.0], LOADED).liters - leg)
    assert cliff.liters >= 0
    # участок без подъёма (None) — без поправки; ни одного известного — рейс без рельефа (не «Վերելք՝ 0 մ»)
    assert route_cost([10.0, 5.0], [100.0], PLAIN, [None, None]) == route_cost([10.0, 5.0], [100.0], PLAIN)
    part = route_cost([10.0, 5.0], [100.0], PLAIN, [None, 0.0])
    assert part.climb_m == 0.0 and part.terrain_liters is not None
    with pytest.raises(ValueError):
        route_cost([10.0, 5.0], [100.0], PLAIN, [1.0])
    with pytest.raises(ValueError):
        route_cost([10.0], [], PLAIN, [math.nan])


def test_average_climb_is_in_the_norm(monkeypatch):
    """Нормы л/100 содержат средний подъём ū: участок с подъёмом ū · км — ровно по норме, ровный — дешевле нормы."""
    monkeypatch.setattr(rc, 'TERRAIN_U_BAR', 8.0)
    flat = route_cost([10.0], [], PLAIN)
    assert route_cost([10.0], [], PLAIN, [80.0]).liters == pytest.approx(flat.liters)
    assert route_cost([10.0], [], PLAIN, [0.0]).liters < flat.liters


def test_curb_mass_by_class():
    assert [curb_tonnes(c) for c in (1000.0, 2200.0, 2300.0, 2500.0, 2600.0, 5000.0, 10000.0)] == pytest.approx(
        [2.3, 2.3, 2.3, 2.5, 2.6, 4.5, 9.0])
    assert 2.6 < curb_tonnes(3500.0) < 4.5


def test_heavy_load_downhill_first_is_cheaper(monkeypatch):
    """Склад наверху, B — посередине склона (тяжёлый заказ), A — внизу: «B, затем A» спускается с грузом и поднимается
    пустой, «A, затем B» везёт груз B вверх. Ровные литры одинаковы; 2-opt по стоимости выбирает спуск с грузом."""
    monkeypatch.setattr(rc, 'TERRAIN_U_BAR', 0.0)
    # узлы: 0 склад, 1 — A (внизу), 2 — B (середина); км везде 5, подъёмы — эффективные (спуск ≤ 75 м)
    km = [[0.0, 5.0, 5.0], [5.0, 0.0, 5.0], [5.0, 5.0, 0.0]]
    climb = [[0.0, -75.0, -75.0], [300.0, 0.0, 200.0], [100.0, -75.0, 0.0]]
    d = fl.KmMatrix(km)
    d.climb = climb
    stops = [fl._Stop(1, 200.0, 0.0, 0.0), fl._Stop(2, 1800.0, 0.0, 0.0)]
    ab, ba = fl._sequence_cost([0, 1], stops, d, PLAIN), fl._sequence_cost([1, 0], stops, d, PLAIN)
    assert ba.liters < ab.liters
    assert fl._sequence_cost([0, 1], stops, km, PLAIN).liters == fl._sequence_cost([1, 0], stops, km, PLAIN).liters
    assert fl._cost_order([0, 1], stops, d, PLAIN, TN, lambda s: True) == [1, 0]
    assert fl._cost_order([0, 1], stops, km, PLAIN, TN, lambda s: True) == [0, 1]   # без рельефа — как раньше


def test_centering_is_zero_on_history(monkeypatch):
    """ū по истории (mean_climb: Σ m·U / Σ m·км) — сумма поправок рельефа по тем же рейсам ≈ 0."""
    rnd = random.Random(85)
    trips = []
    for _ in range(40):
        n = rnd.randint(1, 6)
        kms = [rnd.uniform(1.0, 30.0) for _ in range(n + 1)]
        trips.append((kms, [rnd.uniform(50.0, 400.0) for _ in range(n)],
                      [k * rnd.uniform(4.0, 12.0) for k in kms], rnd.choice([PLAIN, LOADED])))
    legs = []
    for kms, kgs, ups, truck in trips:
        rest = math.fsum(kgs)
        for i, (k, u) in enumerate(zip(kms, ups)):
            legs.append((curb_tonnes(truck.capacity_kg) + rest / 1000.0, u, k))
            if i < len(kgs):
                rest = max(0.0, rest - kgs[i])
    monkeypatch.setattr(rc, 'TERRAIN_U_BAR', mean_climb(legs))
    total = math.fsum(route_cost(kms, kgs, t, ups).terrain_liters for kms, kgs, ups, t in trips)
    flat = math.fsum(route_cost(kms, kgs, t).liters for kms, kgs, ups, t in trips)
    assert abs(total) < 1e-9 * flat
    assert 4.0 < mean_climb(legs) < 12.0
    assert mean_climb([]) == 0.0


# ============================== подъёмы по графу ==============================

def test_tree_sums_equals_walking_each_path():
    rnd = random.Random(1)
    n = 500
    pred = np.array([-9999] + [rnd.randrange(i) for i in range(1, n)])
    w = np.array([0.0] + [rnd.uniform(-5, 5) for _ in range(1, n)])
    want = np.zeros(n)
    for v in range(1, n):
        u = v
        while pred[u] >= 0:
            want[v] += w[u]
            u = pred[u]
    assert np.allclose(rd._tree_sums(pred, w), want)


def test_climb_along_the_shortest_path_both_directions():
    """Вверх на 6 шагов — +300 м; вниз — по 6 м экономии на шаг (CLIMB_C): −36. Восток — ровно. Один узел — 0."""
    r = _hilly()
    south, north, ne = _at(0, 0), _at(6, 0), _at(6, 6)
    near = (south[0] + 1e-6, south[1])
    r.ensure_climb([south, north, ne, near])
    assert r.terrain
    assert r.climb(south, north) == pytest.approx(6 * RISE)
    assert r.climb(north, south) == pytest.approx(-6 * CAP_M)
    assert r.climb(south, ne) == pytest.approx(6 * RISE)           # любой кратчайший путь — те же 6 подъёмов
    assert r.climb(ne, south) == pytest.approx(-6 * CAP_M)
    assert r.climb(north, ne) == pytest.approx(0.0)
    assert r.climb(south, near) == 0.0
    # точка, добавленная позже, — со всеми прежними в обе стороны
    mid = _at(3, 3)
    r.ensure_climb([mid])
    assert r.climb(mid, north) == pytest.approx(3 * RISE) and r.climb(north, mid) == pytest.approx(-3 * CAP_M)
    assert r.climb(south, mid) == pytest.approx(3 * RISE)
    assert r.climb((45.0, 50.0), north) is None                     # не привязана


def test_no_elevation_means_no_terrain():
    """Без высот: terrain False, climb None, матрицы «Развоза» — прежний list без подъёмов."""
    r = rd.RoadDistances.for_graph(GRAPH)
    r.ensure_climb([_at(0, 0), _at(6, 0)])
    assert not r.terrain and r.climb(_at(0, 0), _at(6, 0)) is None
    norms = replace(DP_NORMS, roads=r)
    d, _ = fl._matrices([_at(6, 0)], _at(0, 0), norms, TN, True)
    assert type(d) is list and not hasattr(d, 'climb')
    d2, _ = fl._matrices([_at(6, 0)], _at(0, 0), replace(DP_NORMS, roads=_hilly()), TN, True)
    assert d2 == d and d2.climb[0][1] == pytest.approx(6 * RISE)
    d3, _ = fl._matrices([_at(6, 0)], _at(0, 0), replace(DP_NORMS, roads=_hilly()), TN)   # модель парка — без рельефа
    assert type(d3) is list


def test_bypass_climbs_follow_the_bypass_path():
    """Объезд центра: подъём — по пути объезда (тот же путь, что км)."""
    from test_route_center_bypass import ZONE
    base = _hilly()
    zone = ZONE
    r = rd.CenterBypassRoads(base, rd.RoadDistances.around_zone(lambda: GRAPH, zone, 'memory',
                                                                 elevation=base.elevation), zone)
    west, east = _at(3, 0), _at(3, 6)
    r.ensure_climb([west, east])
    assert r.terrain
    # объезд узла (3, 3) — через ряд 2 или 4: подъём +50 и спуск −6 (или наоборот), путь длиннее прямого
    assert r.km(west, east) > base.km(west, east)
    assert r.climb(west, east) == pytest.approx(RISE - CAP_M)
    assert base.climb(west, east) == pytest.approx(0.0)


def test_climb_cache_on_disk_and_invalidation(tmp_path):
    """Кэш подъёмов — <карта>.climb.npz; сменились высоты — пересчёт; сменилась карта — рельефа нет до команды dem."""
    pbf = _map(tmp_path)
    version = rd.map_signature(pbf)
    identity = rd.RoadGraph.stored_identity(rd._cache_path(pbf, 'graph'), version)
    elev = rd._cache_path(pbf, 'elev')
    terrain.save_elevation(elev, terrain.elev_key(identity), 'test', Z.astype(np.float32))
    south, north = _at(0, 0), _at(6, 0)
    r = rd.RoadDistances.for_map(pbf, version)
    assert r.background
    r.ensure_climb([south, north], wait=True)
    assert r.terrain and r.climb(south, north) == pytest.approx(300.0)
    assert os.path.exists(rd._cache_path(pbf, 'climb'))
    again = rd.RoadDistances.for_map(pbf, version)        # из файла, граф не нужен
    again._load_network = None
    assert again.ensure_climb([south, north], wait=True)
    assert again.climb(south, north) == pytest.approx(300.0)
    # докачаны тайлы (другой отпечаток), высоты вдвое круче — кэш подъёмов не годится, пересчёт
    terrain.save_elevation(elev, terrain.elev_key(identity), 'test-2', (2 * Z).astype(np.float32))
    st = os.stat(elev)
    os.utime(elev, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
    r.ensure_climb([south, north], wait=True)
    assert r.climb(south, north) == pytest.approx(600.0)
    # параметры DEM другие (ключ не тот) — рельефа нет
    terrain.save_elevation(elev, terrain.elev_key(identity, sigma_m=999.0), 'test', Z.astype(np.float32))
    assert not rd.RoadDistances.for_map(pbf, version).terrain
    terrain.save_elevation(elev, terrain.elev_key(identity), 'test', Z.astype(np.float32))
    assert rd.RoadDistances.for_map(pbf, version).terrain
    # новая карта: граф и высоты — от прежней, рельефа нет
    Path(pbf).write_bytes(b'grid, new version')
    fresh = rd.RoadDistances.for_map(pbf, rd.map_signature(pbf))
    assert not fresh.terrain



def _disk_map(tmp_path):
    """Карта сетки на диске с кэшем высот (как после команды dem)."""
    pbf = _map(tmp_path)
    version = rd.map_signature(pbf)
    identity = rd.RoadGraph.stored_identity(rd._cache_path(pbf, 'graph'), version)
    terrain.save_elevation(rd._cache_path(pbf, 'elev'), terrain.elev_key(identity), 'test', Z.astype(np.float32))
    return pbf, version


def test_cold_climbs_never_block_the_request(tmp_path, monkeypatch):
    """Сервер (карта на диске): новые точки — в очередь фонового потока, ответ сразу (climb None, рейс без рельефа);
    расчёт держит свою блокировку, не блокировку км; точки, что уже в кэше, — без блокировок вовсе."""
    pbf, version = _disk_map(tmp_path)
    r = rd.RoadDistances.for_map(pbf, version)
    south, north, mid = _at(0, 0), _at(6, 0), _at(3, 3)
    gate = threading.Event()
    real = rd.RoadNetwork.climbs

    def slow(self, *args, **kwargs):
        gate.wait(10)
        return real(self, *args, **kwargs)
    monkeypatch.setattr(rd.RoadNetwork, 'climbs', slow)
    started = time.perf_counter()
    assert r.ensure_climb([south, north]) is False                   # фон считает — запрос не ждёт
    worker = r._climb_thread
    assert time.perf_counter() - started < 2.0 and r.climb(south, north) is None
    norms = replace(DP_NORMS, roads=r)
    assert fl.trip_running_cost([north], [100.0], south, norms, PLAIN).terrain_liters is None
    r.ensure([mid])                                                   # км новой точки — не ждут подъёмов
    assert r.km(south, mid) is not None
    gate.set()
    worker.join(10)
    assert r.ensure_climb([south, north]) is True and r.climb(south, north) == pytest.approx(300.0)
    with r._climb_lock:                                               # быстрый путь — без блокировок
        got = []
        t = threading.Thread(target=lambda: got.append(r.ensure_climb([south, north])))
        t.start()
        t.join(2)
        assert got == [True]
    assert fl.trip_running_cost([north], [100.0], south, norms, PLAIN).terrain_liters is not None


def test_warm_if_stale_sees_climb_cache_and_refreshes_elevation(tmp_path, monkeypatch):
    """warm --if-stale: высоты есть, а кэша подъёмов нет (или он от других высот) — не годится; после прогрева — годится.
    Кэш высот от прежней карты — пересобирается из уже скачанных тайлов (без сети)."""
    pbf, version = _disk_map(tmp_path)
    r = rd.RoadDistances.for_map(pbf, version)
    r.ensure([_at(0, 0), _at(6, 0)])
    assert rd._dist_cache_stale(pbf)                                  # подъёмов нет
    r.ensure_climb([_at(0, 0), _at(6, 0)], wait=True)
    assert not rd._dist_cache_stale(pbf)
    os.remove(rd._cache_path(pbf, 'elev'))
    assert not rd._dist_cache_stale(pbf)                              # рельеф не включён — подъёмы не нужны
    # кэш высот от другого графа + тайл в папке — пересборка без сети
    terrain.save_elevation(rd._cache_path(pbf, 'elev'), 'old-graph|x', 'test', Z.astype(np.float32))
    assert rd.elevation_loader(pbf, version)() is None
    dem_dir = tmp_path / 'dem'
    dem_dir.mkdir()
    _tile(dem_dir, 'N40E044', 121, lambda lat, lon: 1000 + 0 * lat)
    monkeypatch.setenv('ROUTES_DEM_DIR', str(dem_dir))
    monkeypatch.setattr(terrain, 'download_tiles', lambda *a, **k: pytest.fail('без сети'))
    rd._refresh_elevation(pbf)
    got = rd.elevation_loader(pbf, version)()
    assert got is not None and np.allclose(got[1], 1000.0)


# ============================== тайлы высот ==============================

def _tile(folder, name, side, fn, void=()):
    la, lo = terrain._tile_origin(name)
    r, c = np.mgrid[0:side, 0:side]
    z = np.round(fn(la + 1 - r / (side - 1), lo + c / (side - 1))).astype('>i2')
    for i, j in void:
        z[i, j] = terrain.DEM_VOID
    with gzip.open(os.path.join(folder, f'{name}.hgt.gz'), 'wb') as f:
        f.write(z.tobytes())


def test_dem_sampling_across_tile_seam_and_noise(tmp_path):
    """Плоскость через шов тайлов — без ступеньки (поля соседа); одиночный «дом» гаусс сглаживает; пустой пиксель — не
    дыра; вне тайлов — NaN."""
    plane = lambda lat, lon: 1000 + 300 * (lon - 44) + 200 * (lat - 40)   # noqa: E731
    _tile(tmp_path, 'N40E044', 121, plane)
    _tile(tmp_path, 'N40E045', 121, plane)
    lon = np.linspace(44.9, 45.1, 41)
    lat = np.full(41, 40.5)
    z = terrain.sample(lat, lon, str(tmp_path), sigma_m=3000.0)
    assert np.allclose(z, plane(lat, lon), atol=1.0)
    assert np.isnan(terrain.sample([39.5], [44.5], str(tmp_path))[0])
    flat = lambda lat, lon: np.full(np.shape(lat), 1500.0)   # noqa: E731
    _tile(tmp_path, 'N41E044', 121, lambda la, lo: flat(la, lo) + 40 * ((np.abs(la - 41.5) < 1e-6) & (np.abs(lo - 44.5) < 1e-6)),
          void=[(10, 10)])
    raw = terrain.sample([41.5], [44.5], str(tmp_path), sigma_m=0.0)[0]
    smooth = terrain.sample([41.5], [44.5], str(tmp_path), sigma_m=6000.0)[0]
    assert raw == pytest.approx(1540.0) and 1500.0 <= smooth < 1515.0
    hole = 41 + 1 - 10 / 120, 44 + 10 / 120
    assert terrain.sample([hole[0]], [hole[1]], str(tmp_path), sigma_m=3000.0)[0] == pytest.approx(1500.0, abs=0.5)
    assert terrain.tiles_for([40.5, 40.7, 41.2], [44.1, 45.9, 44.0]) == ['N40E044', 'N40E045', 'N41E044']


def test_track_climbs_from_dem_at_track_points(tmp_path):
    """Трек на восток по склону 3,5% — подъём целиком; обратно — экономия не больше CLIMB_C · длина; точка вне тайлов
    пропускается; трек без высот или из одной точки — None. Все треки — одним проходом."""
    _tile(tmp_path, 'N40E044', 121, lambda lat, lon: 1000 + 3000 * (lon - 44))
    east = [(40.5, x) for x in np.linspace(44.2, 44.3, 30)]
    (up, km), (down, km2), (gap, _), short, none = terrain.track_climbs(
        [east, east[::-1], [east[0], (39.5, 44.25), east[-1]], east[:1], [(39.5, 44.0), (39.6, 44.0)]],
        str(tmp_path), sigma_m=0.0)
    assert up == pytest.approx(300.0, abs=1.0) and km == pytest.approx(8.47, abs=0.05)
    assert down == pytest.approx(-terrain.CLIMB_C * 1000.0 * km2, abs=0.5)
    assert gap == pytest.approx(300.0, abs=1.0)
    assert short is None and none is None


def test_track_norm_centering_is_unbiased_with_constant_mass():
    """Норма трека — постоянная масса: центрирование без веса массы (ū = Σ U / Σ км) даёт ≈ 0 по истории; ū по массе
    плана (груз вниз — меньше) дал бы систематический сдвиг вверх."""
    rnd = random.Random(7)
    legs = [(k, k * rnd.uniform(3.0, 11.0)) for k in (rnd.uniform(1.0, 25.0) for _ in range(300))]
    mass = curb_tonnes(5000.0) + 2.5
    u_track = mean_climb((mass, u, k) for k, u in legs)
    assert u_track == pytest.approx(math.fsum(u for _, u in legs) / math.fsum(k for k, _ in legs))
    bias = math.fsum(terrain_liters(mass, u, k, u_bar=u_track) for k, u in legs)
    assert abs(bias) < 1e-9
    biased = math.fsum(terrain_liters(mass, u, k, u_bar=TERRAIN_U_BAR) for k, u in legs)
    km = math.fsum(k for k, _ in legs)
    assert biased == pytest.approx(TERRAIN_K * mass * (u_track - TERRAIN_U_BAR) * km) and biased > 0


# ============================== PyVRP и «Развоз» ==============================

@pytest.mark.skipif(not vrp.available(), reason='нет PyVRP')
def test_vrp_terrain_term_picks_descending_order():
    """Одинаковые км в обе стороны; подъём сверх среднего на ребре 1 → 2 — PyVRP объезжает 2, затем 1."""
    pieces = [vrp.Piece(1, 100.0, 1.0, None, None, False, True), vrp.Piece(2, 100.0, 1.0, None, None, False, True)]
    km = [[0, 1, 1], [1, 0, 1], [1, 1, 0]]
    mins = [[0, 2, 2], [2, 0, 2], [2, 2, 0]]
    climb = [[0, 0, 0], [0, 0, 300.0], [0, 0, 0]]
    shifts = [vrp.Shift('A', 0.0, 100.0)]
    hill = [vrp.Vehicle('A', 1000.0, 10.0, False, terrain_l_per_m=TERRAIN_K * 3.0)]
    assert vrp.solve(pieces, km, mins, hill, shifts, [(0, [[0, 1]])], None, climb=climb) == [(0, [[1, 0]])]
    assert vrp.solve(pieces, km, mins, hill, shifts, [(0, [[1, 0]])], None, climb=climb) == [(0, [[1, 0]])]
    # без рельефа (climb None) — рёбра прежние: оба порядка равны, решение допустимо
    flat = vrp.solve(pieces, km, mins, hill, shifts, [(0, [[0, 1]])], None)
    assert flat is not None and sorted(flat[0][1][0]) == [0, 1]


@pytest.mark.parametrize('solver', [False, True])
def test_route_day_descends_loaded_when_terrain_in_plan(monkeypatch, solver):
    """terrain.IN_PLAN включён: склад наверху сетки (ряд 6), тяжёлый B — на середине склона, лёгкий A — внизу: рейс
    «B, затем A»; литры рейса — точный route_cost с подъёмами."""
    if solver and not vrp.available():
        pytest.skip('нет PyVRP')
    monkeypatch.setattr(terrain, 'IN_PLAN', True)
    monkeypatch.setattr(rc, 'TERRAIN_U_BAR', 0.0)
    monkeypatch.setattr(fl, 'TERRAIN_U_BAR', 0.0)
    depot, a, b = _at(6, 3), _at(0, 0), _at(3, 0)
    norms = replace(DP_NORMS, roads=_hilly())
    tn = replace(TN, unload_min_per_stop=1.0, unload_min_per_tonne=0.0)
    trips = fl.route_day([a, b], [200.0, 1800.0], [1.0, 1.0], depot, [PLAIN], norms, tn, overflow=False, solver=solver)
    assert [t.items for t in trips] == [(1, 0)]
    exact = fl.trip_running_cost([b, a], [1800.0, 200.0], depot, norms, PLAIN)
    assert trips[0].liters == pytest.approx(exact.liters)
    assert exact.terrain_liters < fl.trip_running_cost([a, b], [200.0, 1800.0], depot, norms, PLAIN).terrain_liters
    flat = fl.route_day([a, b], [200.0, 1800.0], [1.0, 1.0], depot, [PLAIN], replace(DP_NORMS, roads=rd.RoadDistances.for_graph(GRAPH)),
                        tn, overflow=False, solver=solver)
    assert flat[0].liters == pytest.approx(trips[0].km * PLAIN.l100 / 100.0)


@pytest.mark.parametrize('solver', [False, True])
@pytest.mark.parametrize('overflow', [False, True])
def test_route_day_plan_is_flat_by_default(monkeypatch, solver, overflow):
    """По умолчанию (terrain.IN_PLAN выключен, ответ владельца по №85) план с высотами — тот же, что без них: машины, состав,
    порядок, км и минуты; рельеф только добавлен к литрам рейсов (точный route_cost с подъёмами того же порядка)."""
    if solver and (overflow or not vrp.available()):
        pytest.skip('решатель — только для сборки «Развоза»')
    assert terrain.IN_PLAN is False
    depot = _at(6, 3)
    pts = [_at(0, 0), _at(3, 0), _at(1, 5), _at(4, 6), _at(2, 2)]
    kgs = [200.0, 1800.0, 700.0, 400.0, 900.0]
    tn = replace(TN, unload_min_per_stop=1.0, unload_min_per_tonne=0.0)
    run = lambda roads: fl.route_day(pts, kgs, [1.0] * 5, depot, [PLAIN, LOADED], replace(DP_NORMS, roads=roads),   # noqa: E731
                                     tn, overflow=overflow, solver=solver)
    hilly, plain = run(_hilly()), run(rd.RoadDistances.for_graph(GRAPH))
    assert [replace(t, liters=0.0) for t in hilly] == [replace(t, liters=0.0) for t in plain]
    norms = replace(DP_NORMS, roads=_hilly())
    for t in hilly:
        exact = fl.trip_running_cost([pts[i] for i in t.items], [kgs[i] for i in t.items], depot, norms,
                                     PLAIN if t.truck == 'P' else LOADED)
        assert t.liters == pytest.approx(exact.liters) and exact.terrain_liters is not None
        flat = fl.trip_running_cost([pts[i] for i in t.items], [kgs[i] for i in t.items], depot, norms,
                                    PLAIN if t.truck == 'P' else LOADED, terrain_on=False)
        assert flat.terrain_liters is None
    assert any(abs(a.liters - b.liters) > 1e-6 for a, b in zip(hilly, plain))
    if not overflow:   # сборка «Развоза» (report=False): литры рейсов — ровные, рельеф покажет plan_view
        quiet = fl.route_day(pts, kgs, [1.0] * 5, depot, [PLAIN, LOADED], replace(DP_NORMS, roads=_hilly()), tn,
                             overflow=False, solver=solver, report=False)
        assert quiet == plain


def test_plan_view_shows_climb_and_terrain_liters(monkeypatch):
    """Рейс «Развоза»: climb_m и terrain_l (уже в liters); без высот — ключей нет, литры прежние."""
    monkeypatch.setattr(rc, 'TERRAIN_U_BAR', 0.0)
    depot = _at(6, 3)
    stops, _ = _dp_stops([(1, _at(0, 0), 300.0), (2, _at(3, 0), 900.0)])

    def view(roads):
        ctx = dp.DayContext(DP_DAY, depot, {PLAIN.car_code: PLAIN}, replace(DP_NORMS, roads=roads), TN, 9 * 60)
        draft = dp.Draft([PLAIN.car_code], trips=[dp.DraftTrip(1, PLAIN.car_code, [2, 1], False)], next_id=2)
        return dp.plan_view(ctx, stops, draft, _info, explain=False)['trucks'][0]['trips'][0]

    hilly, plain = view(_hilly()), view(rd.RoadDistances.for_graph(GRAPH))
    assert 'climb_m' not in plain and 'terrain_l' not in plain
    # вниз 6 + 3 шагов (по 6 м экономии), вверх с пустой машиной — 6 шагов на 50 м
    assert hilly['climb_m'] == round(6 * RISE - 6 * CAP_M)
    assert hilly['terrain_l'] == pytest.approx(hilly['liters'] - plain['liters'], abs=0.11)
    assert hilly['terrain_l'] > 0 and hilly['km'] == plain['km']



def test_dispatch_build_same_plan_and_flat_variant_costs(monkeypatch):
    """«Развоз» с высотами: сборка — те же рейсы (машина, магазины по порядку), что без них; ֏ вариантов (_trip_amd: наборы
    машин, новые заказы дня) — ровные; в рейсе плана — литры с рельефом. Рельеф в плане (IN_PLAN) — ֏ вариантов с ним."""
    depot = _at(6, 3)
    stops, _ = _dp_stops([(1, _at(0, 0), 300.0), (2, _at(3, 0), 900.0), (3, _at(1, 5), 500.0), (4, _at(4, 6), 400.0)])

    def ctx(roads):
        return dp.DayContext(DP_DAY, depot, {t.car_code: t for t in (PLAIN, LOADED)}, replace(DP_NORMS, roads=roads),
                             TN, 9 * 60)

    hilly, plain = ctx(_hilly()), ctx(rd.RoadDistances.for_graph(GRAPH))
    a = dp.build(hilly, stops, None, ['L', 'P'], '2026-10-01T08:00:00')
    b = dp.build(plain, stops, None, ['L', 'P'], '2026-10-01T08:00:00')
    assert [(t.truck, t.stops) for t in a.trips] == [(t.truck, t.stops) for t in b.trips]
    routable = {s.customer_id: s for s in stops}
    t = a.trips[0]
    shares = dp._shares(a.trips)
    flat = dp._trip_amd(plain, t.stops, routable, shares, t.truck)
    assert dp._trip_amd(hilly, t.stops, routable, shares, t.truck) == flat
    view = dp.plan_view(hilly, stops, a, _info, explain=False)['trucks']
    assert all('terrain_l' in tr for x in view for tr in x['trips'])
    monkeypatch.setattr(terrain, 'IN_PLAN', True)
    assert dp._trip_amd(hilly, t.stops, routable, shares, t.truck) != flat

# ============================== «Նորմ և փաստ» ==============================

def test_garage_norm_flags_against_terrain_norm(client, monkeypatch):
    """Норма дня по треку: км GPS × норма + литры подъёма трека (масса — собственная + полгруза, ū трека); норма месяца с
    рельефом — по ней красный флаг (решение владельца); тайлов нет — прежняя норма и почему (basis, terrain_missing); сбой
    рельефа — страница без него, не ошибка; треки не пересчитываются из кэша."""
    from test_garage_norm import _norm_client, _truck
    from route_optimizer import views
    state = _norm_client(client, monkeypatch)
    monkeypatch.setattr(terrain, 'dem_signature', lambda folder=None: None)
    flat = _truck(client.get('/api/routes/garage/norm?month=2026-09').get_json(), 'CAR1')
    assert flat['norm']['basis'] == 'flat' and flat['norm']['terrain_missing'] == 'no_dem'
    assert 'terrain_l100' not in flat['norm'] and not any('terrain_l' in d for d in flat['days'])
    assert flat['fuel']['delta_pct'] == round((31.125 - 30) / 30 * 100, 1) and not flat['fuel']['over']
    calls = []

    def hills(tracks, folder=None, sigma_m=terrain.DEM_SIGMA_M):
        calls.append(len(tracks))
        return [(-120.0, 30.0) for _ in tracks]   # ровнее среднего — норма с рельефом ниже, флаг загорается
    monkeypatch.setattr(terrain, 'dem_signature', lambda folder=None: 'tiles')
    monkeypatch.setattr(terrain, 'track_climbs', hills)
    state.track_climbs.clear()
    hill = _truck(client.get('/api/routes/garage/norm?month=2026-09').get_json(), 'CAR1')
    cap = state.store.load().truck_capacity('CAR1', views._peek_car_capacity(state))
    extra = terrain_liters(curb_tonnes(cap) + cap / 2000.0, -120.0, 30.0, u_bar=rc.TERRAIN_U_BAR_TRACK)
    day = hill['days'][0]
    assert day['climb_m'] == -120 and day['terrain_l'] == round(extra, 1)
    assert day['norm_l'] == round(day['fact_km'] * 30.0 / 100.0 + extra, 1)
    norm_l = [d['fact_km'] * 30.0 / 100.0 + extra for d in hill['days']]
    terrain_l100 = 100.0 * math.fsum(norm_l) / math.fsum(d['fact_km'] for d in hill['days'])
    assert hill['norm']['basis'] == 'terrain' and hill['norm']['terrain_days'] == len(hill['days'])
    assert hill['norm']['terrain_l100'] == round(terrain_l100, 1) and terrain_l100 < 30.0
    assert hill['fuel']['delta_pct'] == round((31.125 - terrain_l100) / terrain_l100 * 100, 1)
    assert hill['fuel']['over'] == (round((31.125 - terrain_l100) / terrain_l100 * 100, 6) > 10)
    assert hill['norm']['l100'] == 30.0
    n = len(calls)
    client.get('/api/routes/garage/norm?month=2026-09')
    assert len(calls) == n                                      # кэш: треки не пересчитываются
    # сбой рельефа — страница прежняя (без рельефа), не 500
    state.track_climbs.clear()
    monkeypatch.setattr(terrain, 'track_climbs', lambda *a, **k: 1 / 0)
    broken = client.get('/api/routes/garage/norm?month=2026-09')
    assert broken.status_code == 200
    body = _truck(broken.get_json(), 'CAR1')
    assert body['norm']['basis'] == 'flat' and body['norm']['terrain_missing'] == 'no_track'

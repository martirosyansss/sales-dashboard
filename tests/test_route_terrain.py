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
from route_optimizer.running_costs import TERRAIN_K, curb_tonnes, mean_climb, route_cost  # noqa: E402
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
    # участок без подъёма (None) — без поправки
    assert route_cost([10.0, 5.0], [100.0], PLAIN, [None, None]).liters == pytest.approx(
        route_cost([10.0, 5.0], [100.0], PLAIN).liters)
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
    r.ensure_climb([south, north])
    assert r.terrain and r.climb(south, north) == pytest.approx(300.0)
    assert os.path.exists(rd._cache_path(pbf, 'climb'))
    again = rd.RoadDistances.for_map(pbf, version)        # из файла, граф не нужен
    again._load_network = None
    again.ensure_climb([south, north])
    assert again.climb(south, north) == pytest.approx(300.0)
    # докачаны тайлы (другой отпечаток), высоты вдвое круче — кэш подъёмов не годится, пересчёт
    terrain.save_elevation(elev, terrain.elev_key(identity), 'test-2', (2 * Z).astype(np.float32))
    st = os.stat(elev)
    os.utime(elev, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
    r.ensure_climb([south, north])
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


def test_track_climbs_by_graph_node_heights():
    """Трек по сетке на север — +300 м; обратно — экономия не больше CLIMB_C · длина; точка дальше 100 м от узла — без
    высоты (участки к ней ровные); без высот — None."""
    r = _hilly()
    up = [_at(i / 4, 0) for i in range(25)]           # точки между узлами (дальше 100 м) — без высоты, пропускаются
    nodes = [_at(i, 0) for i in range(6, -1, -1)]
    (climb, km), (down, km2), (off, _), short = r.track_climbs(
        [up, nodes, [_at(0, 0), _at(0, 0.5), (40.0, 40.0)], up[:1]])
    assert climb == pytest.approx(300.0) and km == pytest.approx(6 * 0.4, rel=1e-3)
    assert down == pytest.approx(-terrain.CLIMB_C * 1000.0 * km2) and short is None
    assert off == pytest.approx(0.0)
    assert rd.RoadDistances.for_graph(GRAPH).track_climbs([up]) == [None]


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
def test_route_day_descends_loaded(monkeypatch, solver):
    """Склад наверху сетки (ряд 6), тяжёлый B — на середине склона, лёгкий A — внизу: рейс «B, затем A»; литры рейса —
    точный route_cost с подъёмами."""
    if solver and not vrp.available():
        pytest.skip('нет PyVRP')
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


# ============================== «Նորմ և փաստ» ==============================

class _HillRoads:
    """Дороги с высотами для «Նորմ և փաստ»: каждый трек — подъём 120 м на 30 км."""
    terrain = True
    calls = 0

    def elevation(self):
        return 'hills', None

    def track_climbs(self, tracks):
        _HillRoads.calls += 1
        return [(120.0, 30.0) for _ in tracks]


def test_garage_norm_day_with_track_terrain(client, monkeypatch):
    """Норма дня по треку: км GPS × норма + литры подъёма трека (масса — собственная + полгруза); норма месяца с рельефом —
    рядом с ручной (флаги — по ручной). Высот нет — строки и норма прежние (без ключей рельефа)."""
    from test_garage_norm import _norm_client, _truck
    from route_optimizer import views
    state = _norm_client(client, monkeypatch)
    flat = _truck(client.get('/api/routes/garage/norm?month=2026-09').get_json(), 'CAR1')
    assert 'terrain_l100' not in flat['norm'] and not any('terrain_l' in d for d in flat['days'])
    monkeypatch.setattr(rc, 'TERRAIN_U_BAR', 2.0)
    state.roads = type('P', (), {'get': lambda self: _HillRoads()})()
    hill = _truck(client.get('/api/routes/garage/norm?month=2026-09').get_json(), 'CAR1')
    cap = state.store.load().truck_capacity('CAR1', views._peek_car_capacity(state))
    extra = TERRAIN_K * (curb_tonnes(cap) + cap / 2000.0) * (120.0 - 2.0 * 30.0)
    day = hill['days'][0]
    assert day['climb_m'] == 120 and day['terrain_l'] == round(extra, 1)
    assert day['norm_l'] == round(day['fact_km'] * 30.0 / 100.0 + extra, 1)
    km = math.fsum(d['fact_km'] for d in hill['days'])
    assert hill['norm']['terrain_l100'] == pytest.approx(
        100.0 * math.fsum(d['norm_l'] for d in hill['days']) / km, abs=0.06)
    assert hill['norm']['l100'] == 30.0 and hill['fuel']['over'] == flat['fuel']['over']
    calls = _HillRoads.calls
    client.get('/api/routes/garage/norm?month=2026-09')
    assert _HillRoads.calls == calls                     # кэш: треки не пересчитываются

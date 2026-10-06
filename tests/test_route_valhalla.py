# -*- coding: utf-8 -*-
"""Дороги Valhalla и направленные матрицы (план learning-loop, этап 2; замечания проверки ветки valhalla-roads).

- valhalla_engine: матрицы км и минут, кэш (повторное чтение, дозаполнение = полный расчёт, чужая сборка и стоимость),
  блоки по меньшей стороне и пул Actor, предел точек (новая таблица — в стороне); пары без пути и непривязанные точки —
  запасной путь; сбой движка не кэшируется и не плодит запросов; режим valhalla_time (км грузовика — только граф OSM);
  грузовик — стоимость по тоннажу (в реестре — одна на профиль), минуты по ROUTES_TRUCK_TIME, обе модели для сравнения
  (truck_leg_minutes); road_model_id; сборка тайлов (отпечаток содержимого, метка, повтор, блокировка ОС, уборка только
  своего, тайм-аут); команды build и warm (ожидаемые сбои — одной строкой, warm --if-stale, без фона сервера, база
  маршрутов не меняется, .env сервера); сервер не ждёт тяжёлой работы (фоновый поток, срез матриц); нет pyvalhalla —
  граф OSM. Движок — подделка FakeActor с направленной метрикой: ни pyvalhalla, ни карта не нужны;
- направленные матрицы: 2-opt (tsp, search, fleet) не удлиняет тур, даёт локальный оптимум по полному перебору
  разворотов, разворачивает и тур из трёх вершин, кончается на ошибке округления (повтор из проверки); у симметричной
  матрицы — те же туры, что прежний расчёт; вставка и удаление — как полный пересчёт; Кларк–Райт по направленным
  экономиям соединяет конец рейса с началом другого, у симметричной — те же рейсы, что прежде; матрицы оптимизатора и
  парка — в обе стороны; быстрая оценка парка берёт км грузовиков; выгрузка плана — минуты дорог.
Запуск из корня проекта:  python -m pytest tests/test_route_valhalla.py -q
"""
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
import types
from contextlib import closing
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
from route_optimizer import roads as rd  # noqa: E402
from route_optimizer import search as sr  # noqa: E402
from route_optimizer import tsp  # noqa: E402
from route_optimizer import valhalla_engine as ve  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.traffic_validation import TrafficProfile  # noqa: E402

CENTER = (40.18, 44.51)
NORMS = ev.Norms(work_minutes=480.0, detour=1.3, speed_city_kmh=20.0, speed_region_kmh=40.0, city_center=CENTER,
                 city_radius_km=12.0, min_day_revenue=0.0, min_trip_revenue=0.0)
# город (до 12 км от центра) и область; FAR — дальше 0,5 км от дороги, ISLAND — путей к ней нет
P = [(40.18, 44.50), (40.20, 44.52), (40.17, 44.55), (40.22, 44.48), (40.40, 44.70), (40.15, 44.45)]
FAR = (40.50, 44.90)
ISLAND = (40.60, 45.00)
NEW = (40.19, 44.47)        # точка, которой не было в расчёте
SNAP_DLAT = 0.0001   # подделка привязывает точку к дороге в ~11 м севернее
BUILD_NAME = 'tiles-0123456789ab-20261003120000-abcd_123'


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Ни карты, ни тайлов на диске; переключатели — по умолчанию."""
    monkeypatch.setenv('ROUTES_OSM_PATH', str(tmp_path / 'no-map.osm.pbf'))
    monkeypatch.setenv('ROUTES_VALHALLA_DIR', str(tmp_path / 'valhalla'))
    monkeypatch.delenv('ROUTES_ROAD_ENGINE', raising=False)
    monkeypatch.delenv('ROUTES_TRUCK_TIME', raising=False)


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
    """Подделка valhalla.Actor. log — (стоимость, источников, целей) запросов матрицы; bodies — тела запросов;
    created — сколько Actor создано; fail — каждый запрос матрицы падает; gate — запрос матрицы ждёт это событие."""
    log: list = []
    bodies: list = []
    created = 0
    fail = False
    gate: threading.Event | None = None
    lock = threading.Lock()

    def __init__(self, config):
        with FakeActor.lock:
            FakeActor.created += 1

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
        with FakeActor.lock:
            FakeActor.log.append((body['costing'], len(src), len(dst)))
            FakeActor.bodies.append(body)
        if FakeActor.gate is not None:
            FakeActor.gate.wait(10)
        if FakeActor.fail:
            raise RuntimeError('engine is broken')
        dist, dur = [], []
        for a in src:
            km_row, sec_row = [], []
            for b in dst:
                if ISLAND in (a, b) and a != b:   # пути нет — null, как у timedistancematrix
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


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeActor.log, FakeActor.bodies, FakeActor.created, FakeActor.fail, FakeActor.gate = [], [], 0, False, None
    module = types.SimpleNamespace(Actor=FakeActor, __version__='9.9.9-test', get_config=_fake_config,
                                   __file__=str(tmp_path / 'pkg' / 'valhalla' / '__init__.py'))
    monkeypatch.setattr(ve, 'valhalla_module', lambda: module)
    return FakeActor


def _registry(folder, name=BUILD_NAME, bid='test'):
    os.makedirs(folder, exist_ok=True)
    return ve._Registry(ve.Build(bid, name, os.path.join(folder, 'valhalla.json'), {}), str(folder))


def _view(folder, fallback=None, name=BUILD_NAME, **kw):
    return ve.ValhallaRoads(_registry(folder, name), ve.PROFILE_CAR, fallback, compute=True, **kw)


def _snap(p):
    return geo.haversine_km(p, (p[0] + SNAP_DLAT, p[1]))


def _city(a, b):
    return geo.in_city(a, CENTER, 12.0) and geo.in_city(b, CENTER, 12.0)


def _speed(a, b):
    return NORMS.speed_city_kmh if _city(a, b) else NORMS.speed_region_kmh


class FakeOsm:
    """Граф OSM для проверки запасного пути: км = по прямой × 1,2; точек missing в графе нет (None)."""
    version, failed, km_source, map_path = 'osm-test', False, 'osm', None

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


# --- Valhalla: матрицы, кэш, сбои ---

def test_valhalla_km_and_minutes_are_directed(tmp_path, fake):
    r = _view(tmp_path)
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


def test_route_single_leg_without_growing_the_table(tmp_path, fake):
    """Положение машины для ETA карты (№76): участок отдельным запросом, точка в таблицу не попадает; пути нет, сбой
    движка и движок выключен — None."""
    r = _view(tmp_path)
    r.ensure(P)
    here = (40.1912, 44.5133)                                 # точки нет в таблице
    km, minutes = r.route(here, P[1], True)
    want = _metric(here, P[1])
    assert km == pytest.approx(want[0], abs=2e-3) and minutes == pytest.approx(want[1] / 60 * ve.TIME_FACTOR[True], abs=0.05)
    assert r.size == (len(P), len(P)) and r.km(here, P[1]) is None   # таблица не выросла, пары в ней нет
    assert r.route(P[0], ISLAND, False) is None               # пути нет
    fake.fail = True
    assert r.route(here, P[1], True) is None                  # сбой движка — запасная модель, не исключение
    fake.fail = False
    r.active = False
    assert r.route(here, P[1], True) is None


def test_valhalla_cache_reread_incremental_equals_full_other_build_and_costing(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(ve, 'BATCH', 2)                     # несколько блоков — расчёт в потоках
    full = _view(tmp_path / 'full')
    full.ensure(P)
    folder = tmp_path / 'inc'
    inc = _view(folder)
    inc.ensure(P[:2])
    inc.ensure(P[2:])
    for a in P:
        for b in P:
            assert inc.km(a, b) == pytest.approx(full.km(a, b), abs=1e-6)
            assert (inc.minutes(a, b, True) is None) == (full.minutes(a, b, True) is None)
    car_file = folder / f'matrix-{BUILD_NAME}-auto-{ve.costing_key(ve.CAR_COSTING)}.npz'
    assert car_file.exists()
    FakeActor.log = []
    again = _view(folder)                                   # «перезапуск сервера»: всё из кэша
    again.ensure(P)
    assert FakeActor.log == []
    assert again.km(P[3], P[4]) == pytest.approx(full.km(P[3], P[4]), abs=1e-6)
    other = _view(folder, name='tiles-0123456789ab-20261003130000-zzzz_999')   # другая сборка — пересчёт
    other.ensure(P)
    assert FakeActor.log
    FakeActor.log = []                                      # другой парк — своя матрица грузовика, свой файл
    small, big = (ve.ValhallaRoads(_registry(folder), ve.PROFILE_TRUCK, compute=True, truck_cost=ve.truck_costing(c))
                  for c in (2000, 5000))
    small.ensure(P)
    big.ensure(P)
    assert [c for c, _, _ in FakeActor.log] and {c for c, _, _ in FakeActor.log} == {'truck'}
    files = sorted(p.name for p in folder.iterdir() if p.name.startswith(f'matrix-{BUILD_NAME}-truck-'))
    assert files == [f'matrix-{BUILD_NAME}-truck-{ve.costing_key(ve.truck_costing(5000))}.npz']   # прежний — убран
    assert any(b.get('costing_options') == {'truck': ve.truck_costing(5000)} for b in FakeActor.bodies)


def test_valhalla_corrupt_cache_is_recomputed(tmp_path, fake):
    _view(tmp_path).ensure(P[:3])
    path = tmp_path / f'matrix-{BUILD_NAME}-auto-{ve.costing_key(ve.CAR_COSTING)}.npz'
    path.write_bytes(b'not a zip')
    FakeActor.log = []
    r = _view(tmp_path)
    r.ensure(P[:3])
    assert FakeActor.log and r.km(P[0], P[1]) is not None


def test_valhalla_unsnapped_and_unreachable_fall_back(tmp_path, fake):
    pts = [*P, FAR, ISLAND]
    alone = _view(tmp_path / 'a')
    alone.ensure(pts)
    assert alone.unsnapped(pts) == 1                        # FAR: дорога дальше 0,5 км
    assert alone.km(FAR, P[0]) is None and alone.km(ISLAND, P[0]) is None
    assert alone.km(P[0], P[1]) is not None and alone.active  # пары без пути — данные, а не сбой движка
    assert alone.minutes(ISLAND, P[0], True) is None
    norms = replace(NORMS, roads=alone)
    assert norms.km(ISLAND, P[0]) == pytest.approx(geo.haversine_km(ISLAND, P[0]) * 1.3)   # по прямой × извилистость
    assert norms.leg_speed(ISLAND, P[0], 5.0, False) == NORMS.speed_region_kmh
    osm = FakeOsm()
    backed = _view(tmp_path / 'b', osm)
    backed.ensure(pts)
    assert backed.km(ISLAND, P[0]) == pytest.approx(geo.haversine_km(ISLAND, P[0]) * 1.2)   # граф OSM
    assert osm.ensured == []                                # км — Valhalla: граф всем набором не нужен


def test_broken_engine_is_not_cached_and_falls_back(tmp_path, fake):
    """Ошибка движка — не «пути нет»: профиль выключен, на диск ничего, запросов — не лавина; дальше граф OSM."""
    FakeActor.fail = True
    osm = FakeOsm(missing=())
    r = _view(tmp_path, osm)
    r.ensure(P)
    assert not r.active and not r.failed                    # граф OSM есть — дороги не сломаны
    assert len(FakeActor.log) <= 2 * math.ceil(math.log2(len(P))) + 1
    assert not list(tmp_path.glob('matrix-*.npz'))
    assert r.km(P[0], P[1]) == pytest.approx(osm.km(P[0], P[1])) and r.minutes(P[0], P[1], True) is None
    assert r.km_source == 'osm' and ve.road_model_id(r) == ve.road_model_id(osm)
    FakeActor.fail, FakeActor.log = False, []
    r.ensure(P)                                             # до перезапуска не пробуем снова
    assert FakeActor.log == [] and not r.active


def test_mostly_unroutable_extension_is_rejected(tmp_path, fake, monkeypatch):
    """Больше половины новых пар без пути — тайлы неисправны: таблица и кэш не меняются."""
    monkeypatch.setattr(ve, 'MIN_POINTS_FOR_SHARE', 3)
    island = [(40.60 + 0.001 * k, 45.00) for k in range(4)]
    monkeypatch.setattr(sys.modules[__name__], 'ISLAND', island[0])
    real = FakeActor.matrix

    def nulls(self, body):   # все пары с «островными» точками — без пути
        res = real(self, body)
        src = [(x['lat'], x['lon']) for x in body['sources']]
        dst = [(x['lat'], x['lon']) for x in body['targets']]
        d, t = res['sources_to_targets']['distances'], res['sources_to_targets']['durations']
        for i, a in enumerate(src):
            for j, b in enumerate(dst):
                if a != b and (a in island or b in island):
                    d[i][j] = t[i][j] = None
        return res

    monkeypatch.setattr(FakeActor, 'matrix', nulls)
    r = _view(tmp_path)
    r.ensure([P[0], *island])
    assert not r.active and r.size == (0, 0)
    assert not list(tmp_path.glob('matrix-*.npz'))


def test_point_cap_rebuilds_matrix_for_needed_points(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(ve, 'MAX_POINTS', 4)
    r = _view(tmp_path)
    r.ensure(P[:3])
    r.ensure(P[3:5])                                        # 3 + 2 > 4 — заново только нужные
    assert r.size == (2, 2) and r.km(P[3], P[4]) is not None and r.km(P[0], P[3]) is None


def test_point_cap_keeps_previous_table_until_new_one_is_ready(tmp_path, fake, monkeypatch):
    """Матрица заново (точек больше MAX_POINTS) считается в стороне: пока она считается, читатели видят прежнюю, а не
    пустую таблицу; готова — подменяет её целиком."""
    monkeypatch.setattr(ve, 'MAX_POINTS', 4)
    m = _registry(tmp_path).matrix(ve.PROFILE_CAR, ve.CAR_COSTING)
    m.ensure(P[:3])
    old = m.table
    FakeActor.log, FakeActor.gate = [], threading.Event()
    worker = threading.Thread(target=m.ensure, args=(P[3:5],))
    worker.start()
    try:
        assert _wait(lambda: FakeActor.log)                 # новая таблица считается
        assert m.table is old and set(m.table.index) == set(P[:3])
    finally:
        FakeActor.gate.set()
        worker.join(10)
    assert set(m.table.index) == set(P[3:5]) and not m.failed


def test_registry_keeps_one_costing_per_profile(tmp_path, fake):
    """Сменился парк — матрица прежней стоимости грузовика уходит из реестра (память не копится); срез, снятый до
    этого, дочитывает свою."""
    reg = _registry(tmp_path)
    before = ve.ValhallaRoads(reg, ve.PROFILE_TRUCK, compute=True, truck_cost=ve.truck_costing(2000))
    before.ensure(P)
    car = reg.matrix(ve.PROFILE_CAR, ve.CAR_COSTING)
    big = reg.matrix(ve.PROFILE_TRUCK, ve.truck_costing(5000))
    assert set(reg._matrices) == {(ve.PROFILE_CAR, ve.costing_key(ve.CAR_COSTING)),
                                  (ve.PROFILE_TRUCK, ve.costing_key(ve.truck_costing(5000)))}
    assert reg.matrix(ve.PROFILE_TRUCK, ve.truck_costing(5000)) is big
    assert reg.matrix(ve.PROFILE_CAR, ve.CAR_COSTING) is car
    assert before.km(P[0], P[1]) is not None                # прежний срез — своя матрица


def test_rows_chunk_by_small_side_and_actor_pool_is_bounded(tmp_path, fake, monkeypatch):
    rng = random.Random(3)
    pts = [(40.15 + rng.random() * 0.1, 44.45 + rng.random() * 0.1) for _ in range(30)]
    r = _view(tmp_path)
    r.ensure(pts)
    assert sorted(FakeActor.log) == [('auto', 5, 30), ('auto', 25, 30)]
    FakeActor.log = []
    r.ensure([P[0]])                                        # одна новая точка: строка и столбец — по запросу
    assert sorted(FakeActor.log) == [('auto', 1, 31), ('auto', 30, 1)]
    monkeypatch.setattr(ve, 'BATCH', 1)
    monkeypatch.setattr(ve, 'WORKERS', 2)
    FakeActor.created = 0
    _view(tmp_path / 'pool').ensure(pts)                    # 60 блоков в 2 потоках
    assert 1 <= FakeActor.created <= 2


def test_time_only_takes_osm_km_and_valhalla_minutes(tmp_path, fake):
    gap = P[5]                                              # точки нет в графе OSM
    osm = FakeOsm(missing=(gap,))
    hybrid = _view(tmp_path, osm, time_only=True)
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


def test_time_only_truck_km_only_from_graph_whatever_truck_matrix(tmp_path, fake):
    """valhalla_time: км грузовика — только граф OSM, точки вне графа — по прямой × извилистость, как до этапа 2. Готова
    ли матрица грузовика (её готовность не входит ни в срез, ни в ключи кэша), км не меняет. У машин менеджеров вне
    графа — км Valhalla, как прежде."""
    gap = P[5]                                              # точки нет в графе OSM
    osm = FakeOsm(missing=(gap,))
    reg, cost = _registry(tmp_path), ve.truck_costing(3000)
    reg.matrix(ve.PROFILE_CAR, ve.CAR_COSTING).ensure(P)
    cold = ve.ValhallaRoads(reg, ve.PROFILE_CAR, osm, time_only=True, truck_cost=cost)
    reg.matrix(ve.PROFILE_TRUCK, cost).ensure(P)            # фон досчитал матрицу грузовика
    warm = ve.ValhallaRoads(reg, ve.PROFILE_CAR, osm, time_only=True, truck_cost=cost)
    assert warm.truck()._valhalla_km(gap, P[0]) is not None and cold.truck()._valhalla_km(gap, P[0]) is None
    for view in (cold, warm):
        truck = view.truck()
        assert truck.km(gap, P[0]) is None and truck.km(P[0], P[1]) == pytest.approx(osm.km(P[0], P[1]))
        tn = replace(NORMS, roads=view).for_trucks()
        assert tn.km(gap, P[0]) == pytest.approx(geo.haversine_km(gap, P[0]) * 1.3)   # по прямой × извилистость
        assert truck.km_source == 'osm' and ve.road_model_id(truck) == ve.road_model_id(osm)
        assert view.km(gap, P[0]) == pytest.approx(_metric(gap, P[0])[0] + _snap(gap) + _snap(P[0]), abs=2e-3)
    assert cold.version == warm.version
    blind = ve.ValhallaRoads(reg, ve.PROFILE_CAR, None, time_only=True, truck_cost=cost)   # графа нет
    assert blind.truck().km_source == 'straight' and blind.truck().km(P[0], P[1]) is None
    assert ve.road_model_id(blind.truck()) == 'straight' and blind.truck().unsnapped(P) == len(P)
    assert blind.km(P[0], P[1]) is not None                 # машины менеджеров — км Valhalla
    timed = ve.ValhallaRoads(reg, ve.PROFILE_CAR, None, time_only=True, truck_time=True, truck_cost=cost)
    assert ve.road_model_id(timed.truck()).startswith('straight+valhalla-time:test:truck:')


# --- Грузовик: стоимость, минуты, две модели ---

def test_truck_costing_from_payload():
    assert ve.truck_costing(3000) == {'weight': 6.0, 'height': 3.2, 'width': 2.4, 'length': 7.0}
    assert ve.truck_costing(1000)['weight'] == 3.5           # не легче 3,5 т
    assert ve.truck_costing(20000)['weight'] == 26.0         # не тяжелее 26 т
    assert ve.truck_costing(None)['weight'] == ve.truck_costing(0)['weight'] == 10.0   # не задан — 5 т груза
    assert ve.costing_key(ve.truck_costing(3000)) != ve.costing_key(ve.truck_costing(5000))


def test_truck_minutes_model_by_default_valhalla_on_switch(tmp_path, fake):
    """Грузовики: по умолчанию — прежняя модель (км / скорость зоны), ROUTES_TRUCK_TIME=valhalla — Valhalla-грузовик."""
    depot, pts = P[0], P[1:5]
    allp = [depot, *pts]
    osm = FakeOsm(missing=())
    for truck_time in (False, True):
        car = _view(tmp_path / str(truck_time), osm, time_only=True, truck_time=truck_time,
                    truck_cost=ve.truck_costing(3000))
        car.ensure(allp)
        norms = replace(NORMS, roads=car)
        d, m = fl._matrices(pts, depot, norms)
        truck = norms.for_trucks().roads
        assert truck.profile == ve.PROFILE_TRUCK and truck.costing == ve.truck_costing(3000)
        for i in range(len(allp)):
            for j in range(len(allp)):
                if i == j:
                    continue
                a, b = allp[i], allp[j]
                assert d[i][j] == pytest.approx(osm.km(a, b))                # км — граф OSM
                want = (truck.valhalla_minutes(a, b, _city(a, b)) if truck_time else
                        osm.km(a, b) / _speed(a, b) * 60.0)
                assert m[i][j] == pytest.approx(want)
        assert truck.valhalla_minutes(depot, pts[0], True) > car.minutes(depot, pts[0], True)   # грузовик медленнее


def test_truck_leg_minutes_returns_both_candidates(tmp_path, fake):
    osm = FakeOsm(missing=())
    car = _view(tmp_path, osm, time_only=True, truck_cost=ve.truck_costing(3000))
    car.truck().ensure(P)
    traffic = TrafficProfile({(True, 0, 9): 0.5, (False, 0, 9): 0.5})   # пн 9:00–10:00 — вдвое медленнее
    norms = replace(NORMS, roads=car, traffic=traffic)
    a, b = P[0], P[1]
    leg = ve.truck_leg_minutes(norms, a, b, 9 * 60, weekday=0)
    km = osm.km(a, b)
    raw = car.truck().valhalla_minutes(a, b, True)
    assert leg.km == pytest.approx(km)
    assert leg.model == pytest.approx(traffic.travel(km, _speed(a, b), True, 0, 540))
    assert leg.valhalla == pytest.approx(traffic.travel(km, km / raw * 60.0, True, 0, 540))
    assert leg.model == pytest.approx(km / _speed(a, b) * 60.0 * 2)          # час пик — вдвое
    assert ve.truck_leg_minutes(replace(NORMS, roads=osm), a, b, 600).valhalla is None   # Valhalla нет
    assert ve.truck_leg_minutes(NORMS, a, b, 600).model == pytest.approx(
        geo.haversine_km(a, b) * 1.3 / _speed(a, b) * 60.0)


def test_view_captures_car_and_truck_state_together(tmp_path, fake):
    """Срез грузовика снят вместе со срезом машины: сбой фона после этого расчёт и его id не меняет."""
    reg, cost = _registry(tmp_path), ve.truck_costing(3000)
    reg.matrix(ve.PROFILE_TRUCK, cost).ensure(P)
    view = ve.ValhallaRoads(reg, ve.PROFILE_CAR, FakeOsm(missing=()), truck_time=True, truck_cost=cost)
    reg.matrix(ve.PROFILE_TRUCK, cost).failed = True       # фон сломался уже после начала расчёта
    truck = view.truck()
    assert truck is view.truck() and replace(NORMS, roads=view).for_trucks().roads is truck
    assert truck.active and truck.minutes(P[0], P[1], True) is not None
    assert '+valhalla-time:' in ve.road_model_id(truck)
    fresh = ve.ValhallaRoads(reg, ve.PROFILE_CAR, FakeOsm(missing=()), truck_time=True, truck_cost=cost)
    assert not fresh.truck().active and fresh.truck().minutes(P[0], P[1], True) is None
    assert ve.road_model_id(fresh.truck()) == ve.road_model_id(FakeOsm())


def test_road_model_id_names_the_model_and_follows_content(tmp_path, fake):
    assert ve.road_model_id(None) == 'straight'
    one, two, other = tmp_path / 'a.osm.pbf', tmp_path / 'b.osm.pbf', tmp_path / 'c.osm.pbf'
    one.write_bytes(b'same map')
    two.write_bytes(b'same map')
    other.write_bytes(b'other map')
    os.utime(two, (time.time() - 3600, time.time() - 3600))  # копия: другое время изменения
    osm1, osm2, osm3 = (rd.RoadDistances.for_map(str(p), rd.map_signature(str(p))) for p in (one, two, other))
    tail = f'|r{rd.RULES_VERSION}|d{rd.DIST_FORMAT}'
    assert ve.road_model_id(osm1) == ve.road_model_id(osm2) == f'osm-dijkstra:{ve.map_fingerprint(str(one))}' + tail
    assert ve.road_model_id(osm3) != ve.road_model_id(osm1)
    tf = f'|tf{ve.TIME_FACTOR[True]:g}/{ve.TIME_FACTOR[False]:g}'
    car = ve.ValhallaRoads(_registry(tmp_path / 'v1'), ve.PROFILE_CAR, osm1, time_only=True,
                           truck_cost=ve.truck_costing(3000))
    again = ve.ValhallaRoads(_registry(tmp_path / 'v2', 'tiles-0123456789ab-20261003130000-zzzz_999'),
                             ve.PROFILE_CAR, osm1, time_only=True, truck_cost=ve.truck_costing(3000))
    car_id = ve.road_model_id(osm1) + f'+valhalla-time:test:auto:{ve.costing_key(ve.CAR_COSTING)}' + tf
    assert ve.road_model_id(car) == ve.road_model_id(again) == car_id   # пересборка тех же тайлов — тот же id
    assert ve.road_model_id(car.truck()) == ve.road_model_id(osm1)      # грузовик — прежняя модель времени
    switched = ve.ValhallaRoads(_registry(tmp_path / 'v1'), ve.PROFILE_CAR, osm1, time_only=True, truck_time=True,
                                truck_cost=ve.truck_costing(3000))
    tk = ve.costing_key(ve.truck_costing(3000))
    assert ve.road_model_id(switched.truck()) == ve.road_model_id(osm1) + f'+valhalla-time:test:truck:{tk}' + tf
    full = ve.ValhallaRoads(_registry(tmp_path / 'v1'), ve.PROFILE_CAR, osm1, truck_cost=ve.truck_costing(5000))
    assert ve.road_model_id(full.truck()).startswith(f'valhalla:test:truck:{ve.costing_key(ve.truck_costing(5000))}')
    FakeActor.fail = True                                   # сбой в расчёте: id и минуты — из графа OSM, вместе
    graph = FakeOsm(missing=())
    broken = ve.ValhallaRoads(_registry(tmp_path / 'v3'), ve.PROFILE_CAR, graph, time_only=True, compute=True)
    assert ve.road_model_id(broken).endswith(tf)
    broken.ensure(P)
    assert broken.minutes(P[0], P[1], True) is None and ve.road_model_id(broken) == ve.road_model_id(graph)


# --- Сервер: фоновая подготовка ---

def _crash(*args, **kwargs):
    raise RuntimeError('crash')


def _provider(tmp_path, monkeypatch, run=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    pbf = tmp_path / 'map.osm.pbf'
    pbf.write_bytes(b'map')
    runs = []

    def default_run(tool, config, pbf_path, timeout):
        runs.append(tool)

    monkeypatch.setattr(ve, '_run', run or default_run)
    return ve.ValhallaProvider(str(tmp_path / 'v'), str(pbf)), runs


def _wait(predicate, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    return predicate()


def _preparers():
    return [t for t in threading.enumerate() if t.name == 'valhalla-prepare']


def test_provider_serves_osm_until_ready_and_never_blocks(tmp_path, fake, monkeypatch):
    provider, runs = _provider(tmp_path, monkeypatch)
    osm = FakeOsm(missing=())
    FakeActor.gate = threading.Event()                     # матрицы «считаются», пока не отпустим
    provider.start()
    started = time.monotonic()
    assert all(provider.get(osm, P, 3000) is None for _ in range(20))   # пока не готово — граф OSM
    assert time.monotonic() - started < 2.0 and len(_preparers()) <= 1   # не ждём и не плодим потоков
    assert _wait(lambda: FakeActor.log)                   # фон считает
    FakeActor.gate.set()
    roads = _wait(lambda: provider.get(osm, P, 3000))
    assert isinstance(roads, ve.ValhallaRoads) and roads.time_only and not roads.compute
    assert runs == ['valhalla_build_admins', 'valhalla_build_tiles']
    assert roads.km(P[0], P[1]) == pytest.approx(osm.km(P[0], P[1])) and roads.minutes(P[0], P[1], True) is not None
    assert rd.roads_version(roads) != rd.roads_version(osm)   # ключ кэша оценки меняется — пересчёт с Valhalla
    assert _wait(lambda: not _preparers())                 # всё готово — поток кончился
    FakeActor.log = []
    assert provider.get(osm, [*P, NEW], 3000) is None      # новая точка — опять граф OSM, пока фон не досчитает
    assert _wait(lambda: provider.get(osm, [*P, NEW], 3000)) and FakeActor.log
    assert _wait(lambda: not _preparers())
    asked = len(FakeActor.log)
    roads.ensure([ISLAND])                                 # срез сервера сам не считает
    assert len(FakeActor.log) == asked and roads.km(ISLAND, P[0]) == pytest.approx(osm.km(ISLAND, P[0]))


def test_provider_modes_gate_and_failed_build(tmp_path, fake, monkeypatch):
    provider, runs = _provider(tmp_path, monkeypatch)
    osm = FakeOsm(missing=())
    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'osm')       # откат: ни сборки, ни потоков
    provider.start()
    assert provider.get(osm, P, 3000) is None and not _preparers() and runs == []
    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'nonsense')
    assert ve.engine_mode() == 'osm' and provider.get(osm, P, 3000) is None
    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'valhalla')  # км из Valhalla — грузовику нужна своя матрица
    monkeypatch.setenv('ROUTES_TRUCK_TIME', 'valhalla')
    roads = _wait(lambda: provider.get(osm, P, 3000))
    assert roads is not None and not roads.time_only and roads.truck_time
    truck = roads.truck()
    assert truck.km(P[0], P[1]) == pytest.approx(_metric(P[0], P[1], 'truck')[0] + 2 * _snap(P[0]), abs=2e-3)
    assert truck.minutes(P[0], P[1], True) == pytest.approx(truck.valhalla_minutes(P[0], P[1], True))
    assert _wait(lambda: not _preparers())
    # сборка падает — фон не повторяет её на каждый запрос
    broken, _ = _provider(tmp_path / 'b', monkeypatch, run=_crash)
    broken.start()
    assert _wait(lambda: broken._failed_for is not None and not _preparers())
    assert broken.get(osm, P, 3000) is None and not _preparers()


def test_missing_pyvalhalla_falls_back_to_osm_graph(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'valhalla', None)     # import valhalla → ImportError
    assert ve.valhalla_module() is None and not ve.valhalla_supported()
    pbf = tmp_path / 'map.osm.pbf'
    pbf.write_bytes(b'map')
    provider = ve.ValhallaProvider(str(tmp_path / 'v'), str(pbf))
    provider.start()
    assert provider.get(None, P) is None and ve.open_valhalla() is None and not _preparers()
    osm = FakeOsm()
    monkeypatch.setattr(views.evaluate, 'plan_points', lambda *args: [P[0]])
    monkeypatch.setattr(views, '_ready_trucks', lambda snap, bundle: {})
    state = NS(roads=NS(get=lambda: osm), valhalla=provider)
    assert views._roads(state, None, None, [P[1]]) is osm and osm.ensured == [[P[0], P[1]]]
    with pytest.raises(RuntimeError, match='pyvalhalla'):
        ve.build_tiles(str(pbf), str(tmp_path / 'v'))


# --- Сборка тайлов ---

def _builder(record=None):
    def run(tool, config, pbf, timeout):
        if record is not None:
            record.append(tool)
        Path(json.loads(Path(config).read_text(encoding='utf-8'))['mjolnir']['tile_dir'], 'tile.gph').write_text('x')
    return run


def test_build_tiles_idempotent_by_content_retried_with_marker(tmp_path, monkeypatch, fake):
    runs = []
    monkeypatch.setattr(ve, '_run', _builder(runs))
    pbf, base = tmp_path / 'map.osm.pbf', str(tmp_path / 'v')
    pbf.write_bytes(b'map-1')
    first = ve.build_tiles(str(pbf), base)
    assert runs == ['valhalla_build_admins', 'valhalla_build_tiles']
    info = json.loads(Path(base, 'current.json').read_text(encoding='utf-8'))
    assert info['map_sha256'] == ve.map_fingerprint(str(pbf)) and info['valhalla'] == '9.9.9-test'
    assert json.loads(Path(base, first.name, ve.MARKER).read_text(encoding='utf-8'))['id'] == first.id
    assert ve.current_build(base).name == first.name and _lock_free(base)
    copy = tmp_path / 'copy.osm.pbf'                      # та же карта в другом файле, с другим временем
    copy.write_bytes(b'map-1')
    os.utime(copy, (time.time() - 7200, time.time() - 7200))
    assert ve.build_tiles(str(copy), base).name == first.name and len(runs) == 2
    Path(base, f'matrix-{first.name}-auto-{ve.costing_key({})}.npz').write_bytes(b'old')
    forced = ve.build_tiles(str(pbf), base, force=True)
    assert forced.name != first.name and forced.id == first.id
    assert not Path(base, first.name).exists() and not list(Path(base).glob(f'matrix-{first.name}-*'))
    pbf.write_bytes(b'map-2, another one')
    assert ve.build_tiles(str(pbf), base).id != first.id   # новая карта — новая сборка
    current = ve.current_build(base).name
    fails = iter([True, False, False])
    builder = _builder()

    def flaky(tool, config, pbf_path, timeout):
        if tool == 'valhalla_build_tiles' and next(fails):
            raise RuntimeError('crash')
        builder(tool, config, pbf_path, timeout)

    monkeypatch.setattr(ve, '_run', flaky)
    assert ve.build_tiles(str(pbf), base, force=True).name != current
    current = ve.current_build(base).name
    monkeypatch.setattr(ve, '_run', _crash)
    with pytest.raises(RuntimeError):
        ve.build_tiles(str(pbf), base, force=True)
    assert ve.current_build(base).name == current
    assert sorted(p.name for p in Path(base).iterdir() if p.name.startswith('tiles-')) == [current]


def test_current_build_requires_completion_marker(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(ve, '_run', _builder())
    pbf, base = tmp_path / 'map.osm.pbf', str(tmp_path / 'v')
    pbf.write_bytes(b'map')
    build = ve.build_tiles(str(pbf), base)
    marker = Path(base, build.name, ve.MARKER)
    marker.write_text(json.dumps({'id': 'other'}), encoding='utf-8')
    assert ve.current_build(base) is None
    marker.unlink()
    assert ve.current_build(base) is None


def _lock_free(base):
    """Блокировку сборки можно взять сразу — её никто не держит."""
    try:
        with ve._build_lock(str(base), wait_s=0):
            return True
    except ve.BuildBusy:
        return False


def _hold_lock(base):
    """Держать блокировку сборки в другом потоке, пока не отпустят: (событие «отпустить», поток)."""
    taken, release = threading.Event(), threading.Event()

    def hold():
        with ve._build_lock(str(base), wait_s=0):
            taken.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    assert taken.wait(5)
    return release, holder


def test_build_lock_is_an_os_lock_and_cleanup_only_owned(tmp_path, monkeypatch, fake):
    """Блокировка сборки — средствами ОС: занята — BuildBusy; владелец умер, не отпустив, — ОС сняла её сама (нечего
    «взламывать» по возрасту); файл блокировки сам по себе не мешает и не удаляется. Уборка — только своё."""
    monkeypatch.setattr(ve, '_run', _builder())
    pbf, base = tmp_path / 'map.osm.pbf', tmp_path / 'v'
    pbf.write_bytes(b'map')
    base.mkdir()
    release, holder = _hold_lock(base)                      # другой сборщик
    try:
        with pytest.raises(ve.BuildBusy):
            ve.build_tiles(str(pbf), str(base), max_seconds=0.5)
    finally:
        release.set()
        holder.join(5)
    assert ve.current_build(str(base)) is None and _lock_free(base)
    code = ('import sys, time\nsys.path.insert(0, sys.argv[1])\nfrom route_optimizer import valhalla_engine as ve\n'
            'with ve._build_lock(sys.argv[2], 0):\n    print("held", flush=True)\n    time.sleep(60)\n')
    proc = subprocess.Popen([sys.executable, '-c', code, str(ROOT), str(base)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == 'held'    # держит другой процесс
        assert not _lock_free(base)
    finally:
        proc.kill()                                         # и умирает, не отпустив
        proc.wait(10)
        proc.stdout.close()
    assert _wait(lambda: _lock_free(base), 5)               # ОС сняла блокировку
    old = time.time() - ve.LOCK_STALE_S - 60
    foreign = base / 'notes'
    foreign.mkdir()
    building = base / 'tiles-0123456789ab-20261003120000-build_no'   # недостроенная, свежая — чужая сборка идёт
    building.mkdir()
    dead = base / 'tiles-0123456789ab-20261003110000-dead_123'       # брошенная недостроенная
    dead.mkdir()
    os.utime(dead, (old, old))
    finished = base / 'tiles-0123456789ab-20261003100000-done_123'   # прежняя завершённая
    finished.mkdir()
    (finished / ve.MARKER).write_text('{"id": "x"}', encoding='utf-8')
    stale_cache = base / f'matrix-{finished.name}-auto-{ve.costing_key({})}.npz'
    stale_cache.write_bytes(b'old')
    build = ve.build_tiles(str(pbf), str(base))
    assert build.name and (base / ve.LOCK_NAME).exists() and _lock_free(base)   # файл остаётся, блокировки нет
    assert foreign.exists() and building.exists()           # чужое и недостроенное в работе — не трогаем
    assert not dead.exists() and not finished.exists() and not stale_cache.exists()


def test_build_lock_mutual_exclusion_under_race(tmp_path):
    """Гонка за блокировку (повтор проверки ветки): держит всегда один."""
    holders, peak, guard = [0], [0], threading.Lock()
    start = threading.Barrier(4)

    def worker():
        start.wait()
        with ve._build_lock(str(tmp_path), wait_s=10):
            with guard:
                holders[0] += 1
                peak[0] = max(peak[0], holders[0])
            time.sleep(0.05)
            with guard:
                holders[0] -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert peak[0] == 1 and not any(t.is_alive() for t in threads)


def test_run_timeout_is_a_clean_failure(tmp_path, monkeypatch, fake):
    def slow(*args, **kwargs):
        raise ve.subprocess.TimeoutExpired(args[0], kwargs['timeout'])

    monkeypatch.setattr(ve.subprocess, 'run', slow)
    with pytest.raises(RuntimeError, match='остановлен'):
        ve._run('valhalla_build_tiles', 'cfg.json', 'map.osm.pbf', timeout=5)


# --- Команды и обновление сервера ---

def test_build_command_reports_expected_failures_in_one_line(tmp_path, monkeypatch, fake, capsys):
    """Команда build (задача обновления пишет её вывод в журнал): нет карты, сборка занята, нет pyvalhalla — одна
    строка в stderr и свой код выхода, без трассировки; собрано — сведения о сборке."""
    monkeypatch.setattr(ve, '_run', _builder())
    monkeypatch.setattr(ve, 'REPO_ROOT', str(tmp_path))     # без .env
    assert ve.main(['build']) == 4                          # карты нет
    pbf = tmp_path / 'map.osm.pbf'
    pbf.write_bytes(b'map')
    monkeypatch.setenv('ROUTES_OSM_PATH', str(pbf))
    base = Path(ve.base_dir())
    base.mkdir(parents=True)
    release, holder = _hold_lock(base)
    try:
        assert ve.main(['build', '--max-seconds', '0.5']) == 3   # тайлы собирает другой процесс
    finally:
        release.set()
        holder.join(5)
    assert ve.main(['build']) == 0
    monkeypatch.setattr(ve, 'valhalla_module', lambda: None)
    assert ve.main(['build']) == 4                          # pyvalhalla нет
    out = capsys.readouterr()
    assert json.loads(out.out)['map_sha256'] == ve.map_fingerprint(str(pbf))
    assert len([line for line in out.err.splitlines() if line.startswith('Тайлы не собраны: ')]) == 3
    assert 'Traceback' not in out.err


def test_roads_warm_if_stale_only_when_cache_does_not_fit(tmp_path, monkeypatch):
    """roads warm --if-stale (задача обновления): кэш расстояний и кэш объезда малого центра подходят к графу, формату
    и границе центра — ничего (ERP не нужен); нет графа, кэша, объезда или сменились формат (как DIST_FORMAT 2 → 3),
    граница — прогрев; карты нет — греть нечего."""
    from route_optimizer import store as st
    pbf = tmp_path / 'map.osm.pbf'
    monkeypatch.setenv('ROUTES_OSM_PATH', str(pbf))
    monkeypatch.setattr(rd, 'REPO_ROOT', str(tmp_path))     # без .env
    warmed = []
    monkeypatch.setattr(rd, '_warm', warmed.append)         # прогрев читает ERP — здесь только отметка
    assert rd.main(['warm', '--if-stale']) == 0 and warmed == []   # карты нет
    pbf.write_bytes(b'map')
    version = rd.map_signature(str(pbf))
    assert rd._dist_cache_stale(str(pbf))                   # ни графа, ни кэша
    graph = rd.RoadGraph.from_ways({0: P[0], 1: P[1], 2: P[2]}, [([0, 1, 2, 0], 0)], source=version)
    graph.save(rd._cache_path(str(pbf), 'graph'))
    assert rd._dist_cache_stale(str(pbf))                   # граф есть, кэша расстояний нет
    roads = rd.RoadDistances.for_map(str(pbf), version)
    roads.ensure(P[:3])
    zone = [tuple(p) for p in st.DEFAULT_SETTINGS['center_zone']]   # базы маршрутов нет — граница по умолчанию
    assert not roads.failed and not rd._dist_cache_stale(str(pbf))  # без границы — только кэш расстояний
    assert rd._dist_cache_stale(str(pbf), zone)                     # объезда центра ещё нет
    assert rd.main(['warm', '--if-stale']) == 0 and warmed == [str(pbf)]
    rd.RoadProvider(str(pbf)).bypass(roads, zone).ensure(P[:3])
    assert not rd._dist_cache_stale(str(pbf), zone)
    assert rd.main(['warm', '--if-stale']) == 0 and warmed == [str(pbf)]   # оба годятся
    assert rd._dist_cache_stale(str(pbf), [(lat + 0.001, lon) for lat, lon in zone])   # граница сдвинулась
    monkeypatch.setattr(rd, 'DIST_FORMAT', rd.DIST_FORMAT + 1)     # формат сменился
    assert rd._dist_cache_stale(str(pbf))
    assert rd.main(['warm', '--if-stale']) == 0 and len(warmed) == 2
    assert rd.main(['warm']) == 0 and len(warmed) == 3      # без флага — всегда


def test_roads_command_reads_env_file_first(tmp_path, monkeypatch):
    """Команды roads берут карту (и базу) из .env сервера, как app_v2 и команды valhalla_engine: задача обновления
    греет кэш той карты, с которой работает сервер."""
    pbf = tmp_path / 'from-env.osm.pbf'
    pbf.write_bytes(b'map')
    (tmp_path / '.env').write_text(f'ROUTES_OSM_PATH={pbf.as_posix()}\n', encoding='utf-8')
    monkeypatch.setattr(rd, 'REPO_ROOT', str(tmp_path))
    monkeypatch.delenv('ROUTES_OSM_PATH')                   # значение фикстуры вернётся после теста
    warmed = []
    monkeypatch.setattr(rd, '_warm', warmed.append)
    assert rd.main(['warm', '--if-stale']) == 0 and warmed == [pbf.as_posix()]


def test_roads_warm_never_changes_the_routes_db(tmp_path, monkeypatch, capsys):
    """warm до перезапуска сервера (задача обновления): база маршрутов не меняется ни байтом, даже когда новой версии
    нужна миграция схемы — иначе ещё работающий сервер прежней версии не открыл бы её («создана более новой версией»).
    Настройки — из временной копии, приведённой к схеме программы; базы нет — настройки по умолчанию, файл не
    создаётся."""
    from route_optimizer import store as st
    from test_route_optimizer import _SCHEMA_V1

    db = tmp_path / 'routes-v1.db'
    with closing(sqlite3.connect(db)) as conn:              # схема 1: программе нужна миграция
        for sql in _SCHEMA_V1:
            conn.execute(sql)
        conn.execute("INSERT INTO settings VALUES('min_day_revenue', '120000')")
        conn.commit()
    before = db.read_bytes()
    pbf = tmp_path / 'map.osm.pbf'
    pbf.write_bytes(b'map')                                 # кэша нет — прогрев нужен
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(rd, 'REPO_ROOT', str(tmp_path))
    monkeypatch.setenv('ROUTES_OSM_PATH', str(pbf))
    monkeypatch.setenv('ROUTES_DB_PATH', str(db))
    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'valhalla_time')
    monkeypatch.setitem(sys.modules, 'app_v2', NS(db=NS(connection_string='dummy')))
    monkeypatch.setattr('route_optimizer.snapshot.load_snapshot', lambda connection_string: 'snapshot')
    seen = []
    monkeypatch.setattr(ev, 'plan_points', lambda snap, bundle, coords: seen.append(bundle) or [P[0]])
    monkeypatch.setattr(rd.RoadDistances, 'ensure', lambda self, points: None)   # сам расчёт здесь не нужен
    assert rd.main(['warm', '--if-stale']) == 0
    assert seen and seen[0].settings['min_day_revenue'] == 120000
    assert db.read_bytes() == before                        # ни миграции, ни записи
    assert 'схема 1, у программы' in capsys.readouterr().out
    current = tmp_path / 'routes.db'                        # схема программы: те же настройки, что у Store
    st.Store(str(current)).load()
    data = current.read_bytes()
    assert rd._load_bundle_readonly(str(current)).settings == st.Store(str(current)).load().settings
    assert current.read_bytes() == data
    missing = tmp_path / 'none.db'
    assert rd._load_bundle_readonly(str(missing)).settings == st.Store(str(tmp_path / 'fresh.db')).load().settings
    assert not missing.exists()


@pytest.mark.parametrize('command', ['roads', 'valhalla'])
def test_warm_commands_do_not_start_server_background(tmp_path, monkeypatch, command):
    """warm импортирует app_v2 ради строки подключения к ERP, а тот вызывает init_app: фоновая подготовка Valhalla
    сервера в команде не нужна (считала бы те же матрицы ещё раз) — до импорта режим osm."""
    seen = []

    class Stop(Exception):
        pass

    def snapshot(connection_string):
        seen.append(os.environ.get('ROUTES_ROAD_ENGINE'))
        raise Stop   # дальше — ERP: не идём

    monkeypatch.setenv('ROUTES_ROAD_ENGINE', 'valhalla_time')
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setitem(sys.modules, 'app_v2', NS(db=NS(connection_string='dummy')))
    monkeypatch.setattr('route_optimizer.snapshot.load_snapshot', snapshot)
    pbf = tmp_path / 'map.osm.pbf'
    pbf.write_bytes(b'map')
    with pytest.raises(Stop):
        rd._warm(str(pbf)) if command == 'roads' else ve._warm()
    assert seen == ['osm']


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


def _no_better_reversal(t, d, tol=1e-9):
    """Перебор всех разворотов кусков t[i..j] (t[0] на месте) с полным пересчётом длины."""
    base = _length(t, d)
    return all(_length(t[:i] + t[i:j + 1][::-1] + t[j + 1:], d) >= base - tol * max(1.0, base)
               for i in range(1, len(t) - 1) for j in range(i + 1, len(t)))


TWO_OPTS = [tsp.two_opt, lambda t, d: sr.two_opt(list(t), d)]


@pytest.mark.parametrize('two_opt', TWO_OPTS, ids=['tsp', 'search'])
def test_two_opt_directed_never_longer_and_locally_optimal(two_opt):
    rng = random.Random(7)
    longer_before = 0
    for _ in range(300):
        n = rng.randint(3, 8)
        d = _matrix(n, rng)
        start = [0, *rng.sample(range(1, n), n - 1)]
        t = two_opt(start, d)
        assert t[0] == 0 and sorted(t) == list(range(n))
        assert _length(t, d) <= _length(start, d) + 1e-9
        assert _no_better_reversal(t, d)
        longer_before += _length(_old_two_opt(start, d), d) > _length(start, d) + 1e-9
    assert longer_before > 0                                # прежний расчёт на таких матрицах удлинял туры


@pytest.mark.parametrize('two_opt', TWO_OPTS, ids=['tsp', 'search'])
def test_two_opt_symmetric_matrix_same_as_before(two_opt):
    rng = random.Random(11)
    for _ in range(200):
        n = rng.randint(3, 9)
        d = _matrix(n, rng, symmetric=True)
        start = [0, *rng.sample(range(1, n), n - 1)]
        assert two_opt(start, d) == _old_two_opt(start, d)


def test_two_opt_three_nodes_directed_takes_shorter_direction():
    """Тур из трёх вершин и рейс из двух точек: в направленной матрице один из двух порядков короче."""
    rng = random.Random(5)
    for _ in range(100):
        d = _matrix(3, rng)
        best = min(_length([0, 1, 2], d), _length([0, 2, 1], d))
        assert _length(tsp.two_opt([0, 1, 2], d), d) == pytest.approx(best)
        assert _length(sr.two_opt([0, 1, 2], d), d) == pytest.approx(best)
        stops = [fl._Stop(1, 10.0, 0.0, 5.0), fl._Stop(2, 10.0, 0.0, 5.0)]
        assert fl._closed(fl._two_opt([0, 1], stops, d), stops, d) == pytest.approx(best)
    assert tsp.route_order([(40.18, 44.50), (40.20, 44.52)], None,
                           lambda a, b: 3.0 if b[0] > a[0] else 1.0) == [1, 0]   # без дома: путь — на юг, он короче


# Повтор из проверки ветки (review_tmp/term_find.py, seed 7, случай 192): разность ходов куска даёт «выигрыш»
# −1,8e-12 при истинном 0 — с порогом 1e-12 тур разворачивался туда-обратно без конца.
CYCLE_D = [[0.0, 7063.654, 11685.129, 11685.129, 2770.915], [6894.519, 0.0, 7964.378, 7964.378, 4426.146],
           [11515.993, 7795.242, 0.0, 0.0, 9263.681], [11515.993, 7795.242, 0.0, 0.0, 9263.681],
           [2601.779, 4595.282, 9432.817, 9432.817, 0.0]]
CYCLE_START = [0, 3, 4, 1, 2]


def _finishes(fn, seconds=10.0):
    box = {}
    worker = threading.Thread(target=lambda: box.setdefault('out', fn()), daemon=True)
    worker.start()
    worker.join(seconds)
    return not worker.is_alive(), box.get('out')


def test_two_opt_terminates_on_rounding_cycle_and_stress():
    for name, fn in (('tsp', lambda: tsp.two_opt(CYCLE_START, CYCLE_D)),
                     ('search', lambda: sr.two_opt(list(CYCLE_START), CYCLE_D))):
        done, t = _finishes(fn)
        assert done, f'{name}: 2-opt не кончился'
        assert _length(t, CYCLE_D) <= _length(CYCLE_START, CYCLE_D) + 1e-9
    rng = random.Random(1)                                  # как review_tmp/term_stress.py, коротко
    for _ in range(150):
        n = rng.randint(4, 40)
        scale = rng.choice([1.0, 37.3, 1000.0, 12345.678])
        base = [(rng.random(), rng.random()) for _ in range(n)]
        for i in range(2, n):
            if rng.random() < 0.3:
                base[i] = base[rng.randrange(1, i)]
        d = [[0.0 if base[i] == base[j] else round(math.hypot(base[j][0] - base[i][0], base[j][1] - base[i][1])
                                                   * scale + 0.0137 * scale * (base[j][0] > base[i][0]), 3)
              for j in range(n)] for i in range(n)]
        start = [0, *rng.sample(range(1, n), n - 1)]
        for fn in (lambda: tsp.two_opt(start, d), lambda: sr.two_opt(list(start), d)):
            done, t = _finishes(fn)
            assert done and _length(t, d) <= _length(start, d) * (1 + 1e-9) + 1e-9


def test_fleet_two_opt_directed_and_window_guard():
    rng = random.Random(3)
    for _ in range(200):
        n = rng.randint(2, 7)
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


# --- Кларк–Райт ---

def _old_savings(light, stops, d, m, cap, window):
    """Прежний Кларк–Райт (до этапа 2, без fits) — образец для симметричных матриц."""
    eps = 1e-9
    route = {v: v for v in light}
    members = {v: [v] for v in light}
    load = {v: stops[v].kg for v in light}
    time_ = {v: m[0][stops[v].node] + m[stops[v].node][0] + stops[v].unload for v in light}
    d0, m0 = d[0], m[0]
    pairs = []
    for x, i in enumerate(light):
        a = stops[i].node
        da, d0a = d[a], d0[a]
        for j in light[x + 1:]:
            b = stops[j].node
            s = d0a + d0[b] - da[b]
            if s > eps:
                pairs.append((-s, i, j))
    pairs.sort()
    for _, i, j in pairs:
        ri, rj = route[i], route[j]
        if ri == rj or load[ri] + load[rj] > cap + eps:
            continue
        A, B = members[ri], members[rj]
        if (A[-1] != i and A[0] != i) or (B[0] != j and B[-1] != j):
            continue
        a, b = stops[i].node, stops[j].node
        t = time_[ri] + time_[rj] - m[a][0] - m0[b] + m[a][b]
        if t > window + eps:
            continue
        if A[-1] != i:
            A.reverse()
        if B[0] != j:
            B.reverse()
        A.extend(B)
        for v in B:
            route[v] = ri
        load[ri] += load.pop(rj)
        time_[ri] = t
        del members[rj], time_[rj]
    return sorted(members.values(), key=min)


def test_savings_symmetric_same_as_before():
    rng = random.Random(21)
    for _ in range(200):
        n = rng.randint(2, 12)
        d = _matrix(n + 1, rng, symmetric=True)
        m = [[x * 2.0 for x in row] for row in d]
        stops = [fl._Stop(k + 1, rng.uniform(10, 900), 0.0, rng.uniform(1, 9)) for k in range(n)]
        cap, window = rng.uniform(500, 3000), rng.uniform(20, 120)
        light = list(range(n))
        assert fl._savings(light, stops, d, m, cap, window) == _old_savings(light, stops, d, m, cap, window)


def test_savings_directed_joins_end_to_start_without_reversal():
    """Дёшево только по кругу склад → 2 → 1 → склад: прежний расчёт слил бы 1 → 2 (по кругу в дорогую сторону)."""
    d = [[0.0, 5.0, 1.0], [1.0, 0.0, 5.0], [5.0, 1.0, 0.0]]
    stops = [fl._Stop(1, 10.0, 0.0, 1.0), fl._Stop(2, 10.0, 0.0, 1.0)]
    assert _old_savings([0, 1], stops, d, d, 100.0, 100.0) == [[0, 1]]           # 0 → 1 → 2 → 0: 15
    assert fl._savings([0, 1], stops, d, d, 100.0, 100.0) == [[1, 0]]            # 0 → 2 → 1 → 0: 3
    rng = random.Random(9)
    for _ in range(200):
        n = rng.randint(3, 9)
        dd = _matrix(n + 1, rng)
        stops = [fl._Stop(k + 1, 10.0, 0.0, 1.0) for k in range(n)]
        routes = fl._savings(list(range(n)), stops, dd, dd, 1e9, 1e9)
        assert sorted(v for r in routes for v in r) == list(range(n))
        for r in routes:   # каждое слияние — экономия по направленной формуле: рейс не длиннее, чем каждый отдельно
            assert fl._closed(r, stops, dd) <= sum(fl._closed([v], stops, dd) for v in r) + 1e-9


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
    version, failed, km_source, map_path = 'north', False, 'osm', None

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
                assert mins[i][j] == pytest.approx(norms.km(a, b) / _speed(a, b) * 60)
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


class SplitRoads(NorthRoads):
    """Км машин менеджеров ×2 к прямой; truck — свои дороги (другой km_source — режим valhalla)."""

    def __init__(self, factor, source='osm', truck=None):
        self.factor, self.km_source, self._truck = factor, source, truck

    def km(self, a, b):
        return geo.haversine_km(a, b) * self.factor

    def truck(self):
        return self._truck or self


@pytest.mark.parametrize('source', ['osm', 'valhalla:truck:x'])
def test_fleet_estimate_uses_truck_km_when_they_differ(monkeypatch, source):
    from test_route_optimizer import _truck_bundle, make_snapshot
    truck = SplitRoads(3.0, source)
    car = SplitRoads(2.0, 'osm', truck)
    used = []
    real = opt._Distances

    class Spy(real):
        def __init__(self, points, norms):
            used.append(norms.roads)
            super().__init__(points, norms)

    monkeypatch.setattr(opt, '_Distances', Spy)
    opt.run_optimization(make_snapshot(), _truck_bundle(), None, [], {}, roads=car)
    assert used and (truck in used) == (source != 'osm')


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


def test_route_never_waits_for_busy_actor_pool(tmp_path, fake):
    """Все Actor заняты (фоновая сборка матриц) — опрос карты не встаёт в очередь: route отвечает None сразу."""
    r = _view(tmp_path)
    r.ensure(P)
    engine = r._m.engine
    held = []
    ctxs = [engine.actor() for _ in range(ve.WORKERS)]
    for c in ctxs:
        held.append(c.__enter__())
    try:
        out = []
        th = threading.Thread(target=lambda: out.append(r.route((40.1912, 44.5133), P[1], True)))
        th.start()
        th.join(2.0)
        assert not th.is_alive() and out == [None]            # не ждёт, запасная модель
        with pytest.raises(ve.EngineBusy):
            with engine.actor(wait=False):
                pass
    finally:
        for c in ctxs:
            c.__exit__(None, None, None)
    assert r.route((40.1912, 44.5133), P[1], True) is not None   # освободились — снова отвечает

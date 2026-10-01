# -*- coding: utf-8 -*-
"""Линии рейсов вдоль дорог для карты: roads.RoadNetwork.lines и POST /api/routes/road-lines.

Запуск из корня проекта:  python -m pytest tests/test_route_road_lines.py -q
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip('numpy')
pytest.importorskip('scipy')

from route_optimizer import roads as rd  # noqa: E402

# Тот же синтетический граф, что в test_route_optimizer.py: 0 → 1 — одностороннее (≈ 1 км на север),
# 1 ↔ 2 ↔ 0 — объезд через 2 в обе стороны; 3 ↔ 4 — отдельный островок (в привязку не входит).
RN = {0: (40.30, 44.30), 1: (40.309, 44.30), 2: (40.3045, 44.33), 3: (40.40, 44.40), 4: (40.401, 44.40)}
R_WAYS = [([1, 0], -1), ([0, 1], 1), ([1, 2], 0), ([2, 0], 0), ([3, 4], 0)]


@pytest.fixture(autouse=True)
def _no_road_map(monkeypatch, tmp_path):
    monkeypatch.setenv('ROUTES_OSM_PATH', str(tmp_path / 'no-map.osm.pbf'))


def _roads():
    return rd.RoadDistances.for_graph(rd.RoadGraph.from_ways(RN, R_WAYS))


def _near(a, b, tol=1e-5):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def test_lines_follow_directed_roads():
    there, back = _roads().lines([[RN[0], RN[1]], [RN[1], RN[0]]])
    assert _near(there[0], RN[0]) and _near(there[-1], RN[1])
    assert not any(_near(p, RN[2]) for p in there)          # 0 → 1 напрямую
    assert _near(back[0], RN[1]) and _near(back[-1], RN[0])
    assert any(_near(p, RN[2]) for p in back)               # 1 → 0 — против одностороннего: объезд через 2


def test_lines_fall_back_to_straight_segment():
    far = (40.50, 44.60)   # дальше SNAP_MAX_KM от любой дороги
    no_path, unsnapped, single = _roads().lines([[RN[0], RN[3]], [RN[0], far, RN[1]], [RN[0]]])
    assert len(no_path) == 2 and _near(no_path[1], RN[3])   # островок не привязан — по прямой
    assert _near(unsnapped[1], far) and _near(unsnapped[-1], RN[1])
    assert len(single) == 1


def test_lines_closed_trip_keeps_order():
    trip = [RN[0], RN[2], RN[1], RN[0]]                     # склад → 2 → 1 → склад
    (line,) = _roads().lines([trip])
    assert _near(line[0], RN[0]) and _near(line[-1], RN[0])
    i2 = next(i for i, p in enumerate(line) if _near(p, RN[2]))
    i1 = next(i for i, p in enumerate(line) if _near(p, RN[1]))
    assert 0 < i2 < i1 < len(line) - 1


def test_lines_none_when_roads_failed():
    roads = _roads()
    roads.failed = True
    assert roads.lines([[RN[0], RN[1]]]) is None


def test_simplify_drops_collinear_keeps_corners():
    xy = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.001], [3.0, 0.0], [3.0, 1.0], [0.0, 0.0]])
    assert rd._simplify(xy, 0.005).tolist() == [0, 3, 4, 5]
    assert rd._simplify(xy[:2], 0.005).tolist() == [0, 1]
    loop = np.array([[0.0, 0.0], [1.0, 1.0], [0.0, 0.0]])   # начало = конец
    assert rd._simplify(loop, 0.005).tolist() == [0, 1, 2]


@pytest.fixture
def client(tmp_path):
    from flask import Flask
    import route_optimizer

    class FakeDb:
        connection_string = 'DRIVER={none};'

    app = Flask(__name__)
    app.secret_key = 'test'
    route_optimizer.init_app(app, FakeDb(), db_path=str(tmp_path / 'routes.db'))
    return app.test_client()


def test_api_road_lines(client):
    state = client.application.extensions['route_optimizer']
    body = {'lines': [[list(RN[1]), list(RN[0])]]}
    r = client.post('/api/routes/road-lines', json=body)
    assert r.status_code == 200 and r.get_json()['lines'] is None       # карты дорог нет — по прямой
    state.roads = NS(get=_roads)
    d = client.post('/api/routes/road-lines', json=body).get_json()
    assert d['success'] and any(_near(p, RN[2]) for p in d['lines'][0])
    for bad in ({}, {'lines': 1}, {'lines': [1]}, {'lines': [[[40.3]]]}, {'lines': [[[True, 44.3]]]},
                {'lines': [[[10.0, 10.0]]]}, {'lines': [[list(RN[0])] * (2 * 3000)]}):
        assert client.post('/api/routes/road-lines', json=bad).status_code == 400, bad
    assert client.post('/api/routes/road-lines', data='x', content_type='text/plain').status_code == 415

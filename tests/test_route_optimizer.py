# -*- coding: utf-8 -*-
"""Юнит-тесты раздела «Маршруты» (этап 1): синтетические данные, без БД ERP.

Запуск из корня проекта:  python -m pytest tests/test_route_optimizer.py -q
"""
import ast
import json
import math
import random
import re
import sqlite3
import sys
import threading
import time
import uuid
from collections import Counter
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import fmean, median

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'route_optimizer'
sys.path.insert(0, str(ROOT))

from route_optimizer import demand as dm  # noqa: E402
from route_optimizer import erp, geo, tsp  # noqa: E402
from route_optimizer import evaluate as ev  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import frequency as fq  # noqa: E402
from route_optimizer import optimize as opt  # noqa: E402
from route_optimizer import patterns as pt  # noqa: E402
from route_optimizer import plan as pl  # noqa: E402
from route_optimizer import search as sr  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import transfer as tr  # noqa: E402
from route_optimizer.snapshot import Snapshot, SnapshotCache  # noqa: E402

TODAY = date(2026, 9, 30)
YEREVAN = (40.1792, 44.4991)
GYUMRI = (40.7894, 43.8475)


@pytest.fixture(autouse=True)
def _no_road_map(monkeypatch, tmp_path):
    """Тесты не зависят от карты дорог на диске: по умолчанию карты нет (км по прямой)."""
    monkeypatch.setenv('ROUTES_OSM_PATH', str(tmp_path / 'no-map.osm.pbf'))


# ============================== geo ==============================

def test_haversine_known_distances():
    # 1° широты по меридиану = π·R/180 при R = 6371.0088 км
    assert geo.haversine_km((40.0, 44.0), (41.0, 44.0)) == pytest.approx(111.195, abs=0.01)
    # Ереван–Гюмри по прямой ≈ 87–88 км (дорогой ≈ 120 км)
    assert 85.0 < geo.haversine_km(YEREVAN, GYUMRI) < 95.0
    assert geo.haversine_km(YEREVAN, YEREVAN) == 0.0
    assert geo.haversine_km(YEREVAN, GYUMRI) == pytest.approx(geo.haversine_km(GYUMRI, YEREVAN))


def test_point_validity():
    assert geo.is_valid_point(40.18, 44.5)
    assert geo.is_valid_point(38.8, 43.4) and geo.is_valid_point(41.4, 46.7)  # границы включены
    assert not geo.is_valid_point(42.0, 44.5)
    assert not geo.is_valid_point(40.18, 43.0)
    assert not geo.is_valid_point(0.0, 0.0)
    assert not geo.is_valid_point(None, 44.5)
    assert not geo.is_valid_point(float('nan'), 44.5)


def test_default_point_detection():
    same = [(1, (40.123451, 44.5)), (2, (40.123449, 44.5)), (3, (40.12345, 44.500001))]
    pair = [(4, (40.2, 44.6)), (5, (40.2, 44.6))]
    one_customer = [(6, (40.3, 44.7))] * 5  # один клиент трижды — не «дефолтная»
    keys = geo.default_point_keys(same + pair + one_customer)
    assert keys == {geo.point_key((40.12345, 44.5))}


def test_median_point():
    assert geo.median_point([(40.0, 44.0), (40.2, 44.4), (40.1, 44.1)]) == (40.1, 44.1)
    assert geo.median_point([(40.0, 44.0), (40.2, 44.2)]) == pytest.approx((40.1, 44.1))
    # выброс не тянет медиану
    assert geo.median_point([(40.1, 44.5)] * 4 + [(41.3, 46.6)]) == (40.1, 44.5)
    with pytest.raises(ValueError):
        geo.median_point([])


def test_track_km_jitter_is_zero():
    t0 = datetime(2026, 9, 1, 10, 0)
    fixes = [geo.Fix(t0 + timedelta(minutes=i), 40.1 + 0.0001 * ((i % 3) - 1),
                     44.5 + 0.0001 * ((i % 2) - 0.5), 10.0) for i in range(60)]
    assert geo.track_km(fixes) == pytest.approx(0.0, abs=1e-9)


def test_track_km_drops_jump_and_inaccurate_points():
    t0 = datetime(2026, 9, 1, 10, 0)
    fixes = [geo.Fix(t0 + timedelta(minutes=i), 40.10 + i * 0.0045, 44.50, 10.0) for i in range(11)]
    expected = geo.haversine_km(fixes[0].point, fixes[-1].point)  # ≈ 5 км по прямой
    spike = geo.Fix(t0 + timedelta(minutes=5, seconds=30), 40.30, 44.50, 10.0)  # 20 км за 30 с
    inaccurate = geo.Fix(t0 + timedelta(minutes=7, seconds=30), 40.9, 45.5, 500.0)
    assert geo.track_km(fixes + [spike, inaccurate]) == pytest.approx(expected, abs=1e-9)
    assert geo.track_km(list(reversed(fixes))) == pytest.approx(expected, abs=1e-9)  # сортировка


def test_track_km_spike_after_long_stop_is_dropped():
    # 30 минут стоянки у магазина, затем одна ложная точка в 30 км и снова магазин:
    # скорость меряется от предыдущей точки, а не от якоря получасовой давности
    t0 = datetime(2026, 9, 1, 10, 0)
    shop = [geo.Fix(t0 + timedelta(minutes=i), 40.1 + 0.00005 * (i % 2), 44.5, 10.0)
            for i in range(31)]
    spike = geo.Fix(t0 + timedelta(minutes=30, seconds=30), 40.37, 44.5, 10.0)
    back = geo.Fix(t0 + timedelta(minutes=31), 40.1, 44.5, 10.0)
    assert geo.track_km(shop + [spike, back]) == pytest.approx(0.0, abs=1e-9)


def test_resolve_coord_priority():
    erp_pt, near, far = (40.18, 44.50), (40.181, 44.501), (40.20, 44.50)
    assert geo.resolve_coord(None, erp_pt, near).source == 'erp'
    c = geo.resolve_coord(None, erp_pt, far)          # ERP дальше 2 км от GPS
    assert c.source == 'gps' and c.point == far
    assert geo.resolve_coord(None, erp_pt, None).source == 'erp'
    assert geo.resolve_coord(None, None, near).source == 'gps'
    assert geo.resolve_coord(None, None, None) == geo.NO_COORD
    assert geo.resolve_coord((40.3, 44.3), erp_pt, far).source == 'manual'


def test_guess_home_methods():
    home, shop = (40.10, 44.50), (40.20, 44.60)
    night = [geo.Fix(datetime(2026, 9, d, h, 0), home[0], home[1], 20.0)
             for d in range(1, 7) for h in (2, 4, 6, 7)]
    day = [geo.Fix(datetime(2026, 9, d, 13, 0), shop[0], shop[1], 20.0) for d in range(1, 7)]
    g = geo.guess_home(night + day)
    assert (g.method, g.days) == ('night', 6) and (g.lat, g.lon) == home

    mornings = [geo.Fix(datetime(2026, 9, d, 8, 40), home[0], home[1], 20.0) for d in range(1, 7)]
    g = geo.guess_home(mornings + day)
    assert (g.method, g.days) == ('morning', 6) and (g.lat, g.lon) == home

    few = [geo.Fix(datetime(2026, 9, d, 8, 40), home[0], home[1], 20.0) for d in range(1, 4)]
    assert geo.guess_home(few + day) is None


# ============================== tsp ==============================

def test_two_opt_not_worse_than_nearest_neighbor():
    rng = random.Random(7)
    improved = 0
    for _ in range(30):
        pts = [(40.0 + rng.random() * 0.3, 44.3 + rng.random() * 0.4) for _ in range(12)]
        dist = tsp.distance_matrix(pts)
        nn = tsp.nearest_neighbor(dist, 0)
        opt = tsp.two_opt(nn, dist)
        assert opt[0] == 0 and sorted(opt) == list(range(12))
        assert tsp.closed_length(opt, dist) <= tsp.closed_length(nn, dist) + 1e-9
        improved += tsp.closed_length(opt, dist) < tsp.closed_length(nn, dist) - 1e-9
    assert improved > 0


def test_solve_tour_visits_every_point_once():
    rng = random.Random(3)
    pts = [(40.0 + rng.random() * 0.2, 44.4 + rng.random() * 0.2) for _ in range(9)]
    assert sorted(tsp.solve_tour((40.1, 44.5), pts)) == list(range(9))
    assert tsp.solve_tour((40.1, 44.5), []) == []


def test_route_order_open_path_without_home():
    line = [(40.0 + 0.1 * k, 44.5) for k in range(5)]
    shuffled = [line[i] for i in (3, 0, 4, 1, 2)]
    order = tsp.route_order(shuffled, None)
    path = [shuffled[i] for i in order]
    assert sum(geo.haversine_km(a, b) for a, b in zip(path, path[1:])) == \
        pytest.approx(geo.haversine_km(line[0], line[-1]), rel=1e-9)


def test_split_by_capacity():
    assert tsp.split_by_capacity([60, 50, 30, 80], 100) == ([[0], [1, 2], [3]], [])
    assert tsp.split_by_capacity([60, 50, 30, 80], None) == ([[0, 1, 2, 3]], [])
    assert tsp.split_by_capacity([250, 40], 100) == ([[1]], [(0, 3)])  # тяжелее машины: 3 поездки
    with pytest.raises(ValueError):
        tsp.split_by_capacity([1.0], 0)


def test_delivery_km_trips():
    depot, a, b = (40.0, 44.0), (40.1, 44.0), (40.2, 44.0)
    d_a, d_b = geo.haversine_km(depot, a), geo.haversine_km(depot, b)
    one = tsp.delivery_km(depot, [(a, 10), (b, 10)], 100)
    assert (one.trips, one.km) == (1, pytest.approx(2 * d_b))
    two = tsp.delivery_km(depot, [(b, 10), (a, 10)], 15)  # второй клиент переполнил бы машину
    assert (two.trips, two.km) == (2, pytest.approx(2 * d_a + 2 * d_b))
    heavy = tsp.delivery_km(depot, [(a, 250)], 100)
    assert (heavy.trips, heavy.km) == (3, pytest.approx(6 * d_a))
    assert tsp.delivery_km(depot, [], 100) == tsp.Delivery(0.0, 0)


# ============================== demand ==============================

def test_group_orders_by_customer_and_order_date():
    docs = [
        dm.SaleDoc(1, 7, date(2026, 9, 2), date(2026, 9, 1), 1000.0, 10.0),
        dm.SaleDoc(1, 8, date(2026, 9, 3), date(2026, 9, 1), 5000.0, 50.0),
        dm.SaleDoc(1, 7, date(2026, 9, 5), None, 300.0, 3.0),   # без заказа — дата продажи
        dm.SaleDoc(2, 9, date(2026, 9, 2), date(2026, 9, 1), 700.0, 7.0),
    ]
    orders = dm.group_orders(docs)
    assert orders == [
        dm.Order(1, date(2026, 9, 1), 8, 6000.0, 60.0),  # агент самого крупного документа
        dm.Order(2, date(2026, 9, 1), 9, 700.0, 7.0),
        dm.Order(1, date(2026, 9, 5), 7, 300.0, 3.0),
    ]


def _every(cid, start, step, count, revenue=10000.0, kg=20.0, agent=1):
    return [dm.Order(cid, start + timedelta(days=step * i), agent, revenue, kg) for i in range(count)]


def test_lambda_and_probability():
    end = TODAY
    start = end - timedelta(days=364)  # ровно 52 недели
    orders = _every(1, start, 14, 26)
    d = dm.window_demand(orders, [(start, end)], start - timedelta(days=100), end)
    assert d.lam == pytest.approx(0.5) and d.exposure_days == 364 and len(d.values) == 26
    assert dm.visit_probability(d.lam, 1.0) == pytest.approx(0.5)
    assert dm.visit_probability(d.lam, 0.25) == 1.0   # визитов реже, чем заказов — p ограничена 1
    assert dm.visit_probability(d.lam, 0.0) == 0.0
    assert d.mean_revenue == 10000.0 and d.mean_kg == 20.0

    first = end - timedelta(days=70)                   # экспозиция — с первой покупки
    d = dm.window_demand(_every(2, first, 7, 10), [(start, end)], first, end)
    assert d.exposure_days == 70 and d.lam == pytest.approx(1.0)

    first = end - timedelta(days=3)                    # экспозиция < 21 дня — снизу 21 день
    d = dm.window_demand([dm.Order(3, first, 1, 5.0, 1.0)], [(start, end)], first, end)
    assert d.lam == pytest.approx(1 / 3)

    assert dm.window_demand([], [(start, end)], None, end) == dm.Demand(0.0, (), 0)


def test_season_demand_and_fallback():
    end = TODAY
    start = end - timedelta(days=365)
    low_w = dm.month_windows([1, 2, 3], start, end)
    assert low_w[0][0] == date(2026, 1, 1) and low_w[-1][1] == date(2026, 4, 1)
    assert dm.window_days(low_w) == 90
    # клиент с историей зимой: λ по зимним заказам
    winter = _every(1, date(2026, 1, 5), 14, 6, revenue=8000.0)
    summer = _every(1, date(2026, 6, 1), 7, 10, revenue=20000.0)
    first = start - timedelta(days=400)
    year = dm.window_demand(winter + summer, [(start, end)], first, end)
    low = dm.season_demand(winter + summer, low_w, first, end, year, rate_ratio=0.6)
    assert not low.fallback and low.lam == pytest.approx(6 / (90 / 7))
    assert {v[0] for v in low.values} == {8000.0}
    # новый клиент (с мая) — зимой не существовал: фолбэк на год × отношение темпов компании
    new = _every(2, date(2026, 5, 1), 7, 20)
    year = dm.window_demand(new, [(start, end)], date(2026, 5, 1), end)
    low = dm.season_demand(new, low_w, date(2026, 5, 1), end, year, rate_ratio=0.6)
    assert low.fallback and low.lam == pytest.approx(year.lam * 0.6) and low.values == year.values


def test_scale_multiplies_lambda():
    end = TODAY
    start = end - timedelta(days=364)
    orders = _every(1, start, 14, 26)
    base = dm.window_demand(orders, [(start, end)], start, end)
    scaled = dm.window_demand(orders, [(start, end)], start, end, scale=1.5)
    assert scaled.lam == pytest.approx(base.lam * 1.5)
    fb = dm.season_demand(orders, [], start, end, scaled, rate_ratio=0.5)  # scale не применяется дважды
    assert fb.fallback and fb.lam == pytest.approx(base.lam * 1.5 * 0.5)


def test_company_rate():
    by_day = {date(2026, 1, 5): 10, date(2026, 1, 6): 4, date(2026, 5, 1): 100}
    assert dm.company_rate(by_day, [(date(2026, 1, 1), date(2026, 1, 15))]) == pytest.approx(7.0)
    assert dm.company_rate(by_day, []) is None


def test_seasonal_index_on_synthetic_series():
    end_month = date(2026, 9, 1)
    monthly = {}
    for i in range(36):
        m = dm.month_add(end_month, -36 + i)
        monthly[(m.year, m.month)] = {1: 50.0, 7: 150.0}.get(m.month, 100.0)
    idx = dm.seasonal_index(monthly, end_month)
    assert idx[1] == pytest.approx(0.5) and idx[7] == pytest.approx(1.5)
    assert all(idx[m] == pytest.approx(1.0) for m in range(1, 13) if m not in (1, 7))
    assert dm.classify_months(idx, 0.8, 1.2) == ([1], [7])
    assert dm.seasonal_index({}, end_month) is None
    assert dm.classify_months(None, 0.8, 1.2) == ([], [])


def test_month_windows_and_days():
    w = dm.month_windows([1, 2], date(2025, 12, 15), date(2026, 3, 10))
    assert w == [(date(2026, 1, 1), date(2026, 2, 1)), (date(2026, 2, 1), date(2026, 3, 1))]
    assert dm.window_days(w) == 59
    assert dm.month_windows([12], date(2025, 12, 15), date(2026, 3, 10)) == \
        [(date(2025, 12, 15), date(2026, 1, 1))]
    assert dm.exposure_days(w, date(2026, 1, 20), date(2026, 3, 10)) == 40


def test_size_class():
    args = ((), 60, 250)
    assert dm.size_class(50, '036', *args) == 'small'
    assert dm.size_class(60, '036', *args) == 'small'
    assert dm.size_class(61, '036', *args) == 'medium'
    assert dm.size_class(250, '036', *args) == 'medium'
    assert dm.size_class(251, '036', *args) == 'large'
    assert dm.size_class(None, '036', *args) == 'small'          # нет заказов
    assert dm.size_class(5, '017', ('017',), 60, 250) == 'large'  # сеть


# ============================== plan ==============================

def _row(agent, tid, week, weekday, period, cid, rownum, addr=0):
    return pl.TemplateRow(agent, tid, week, weekday, period, cid, rownum, addr)


def test_plan_cycle_unrolling():
    p = pl.build_plan([
        _row(1, 1, 1, 1, 2, 10, 1),   # раз в 2 недели, неделя 1
        _row(1, 2, 2, 1, 2, 11, 1),   # раз в 2 недели, неделя 2
        _row(1, 3, 1, 3, 1, 12, 1),   # еженедельно
    ])
    assert p.cycle_weeks == 2 and p.multiweek
    days = {(d.week, d.weekday): [v.customer_id for v in d.visits] for d in p.days}
    assert days == {(1, 1): [10], (2, 1): [11], (1, 3): [12], (2, 3): [12]}
    assert p.visits_per_week == {10: 0.5, 11: 0.5, 12: 1.0}


def test_plan_frequency_and_duplicates():
    p = pl.build_plan([
        _row(1, 1, 1, 1, 1, 10, 2), _row(1, 1, 1, 1, 1, 20, 1),
        _row(1, 1, 1, 1, 1, 10, 5),                             # дубль в том же дне
        _row(1, 2, 1, 4, 1, 10, 1),                             # тот же клиент в четверг
        _row(2, 3, 1, 1, 1, 10, 1),                             # и у другого агента
    ])
    assert p.cycle_weeks == 1 and not p.multiweek
    monday = p.days_of(1)[0]
    assert [v.customer_id for v in monday.visits] == [20, 10]   # по fROWNUM, без дубля
    assert p.visits_per_week[10] == 3.0 and p.visits_per_week[20] == 1.0
    assert p.agent_ids == [1, 2] and p.customers_of(2) == {10}
    assert pl.build_plan([]).days == ()


def test_frequency_among_included_managers():
    p = pl.build_plan([
        _row(1, 1, 1, 1, 1, 10, 0), _row(1, 1, 1, 1, 1, 20, 1),
        _row(2, 2, 1, 1, 1, 10, 0), _row(2, 2, 1, 1, 1, 20, 1),   # дубль шаблона агента 1
        _row(2, 3, 1, 4, 1, 30, 0),                             # клиент только у агента 2
    ])
    assert p.visits_per_week == {10: 2.0, 20: 2.0, 30: 1.0}
    assert p.visits_per_week_among({1}) == {10: 1.0, 20: 1.0, 30: 1.0}   # 30 — по всем агентам
    assert p.visits_per_week_among({1, 2}) == p.visits_per_week
    assert p.visits_per_week_among(()) == p.visits_per_week


def test_delivery_weekday():
    assert pl.delivery_weekday(1) == (2, False)
    assert pl.delivery_weekday(5) == (6, False)
    assert pl.delivery_weekday(6) == (1, False)   # суббота → понедельник
    assert pl.delivery_weekday(7) == (1, True)    # воскресенье → понедельник с пометкой


# ============================== evaluate ==============================

NORMS = ev.Norms(work_minutes=540.0, detour=1.3, speed_city_kmh=25.0, speed_region_kmh=45.0,
                 city_center=YEREVAN, city_radius_km=12.0, min_day_revenue=100000.0,
                 min_trip_revenue=150000.0)


def _visit(cid, point=None, p=1.0, revenue=60000.0, kg=10.0, minutes=10.0):
    draw = ev.Draw(p, ((revenue, kg),))
    return ev.VisitModel(cid, point, minutes, draw, draw, draw)


def test_route_metrics_city_and_region():
    near = (YEREVAN[0] + 0.02, YEREVAN[1])                     # ≈ 2.2 км, в городе
    leg = geo.haversine_km(YEREVAN, near)
    km, minutes = ev.route_metrics([near], YEREVAN, NORMS)
    assert km == pytest.approx(2 * leg * 1.3)
    assert minutes == pytest.approx(2 * leg * 1.3 / 25.0 * 60)
    far = (40.5, 44.5)                                          # ≈ 36 км, область
    km, minutes = ev.route_metrics([far], YEREVAN, NORMS)
    assert minutes == pytest.approx(km / 45.0 * 60)
    km, _ = ev.route_metrics([near, far], None, NORMS)          # без дома — от первого до последнего
    assert km == pytest.approx(geo.haversine_km(near, far) * 1.3)


def test_p_day_ge_min_known_answers():
    day = pl.PlanDay(1, 1, 1, ())
    kw = dict(home=YEREVAN, norms=NORMS, manager_l100=9.0, workday=True)
    sure = ev.evaluate_day(day, [_visit(1, revenue=60000), _visit(2, revenue=60000)], **kw)
    assert sure.low.p_ge_min == 1.0 and sure.low.p10 == sure.low.p90 == sure.low.expected == 120000
    short = ev.evaluate_day(day, [_visit(1, revenue=40000), _visit(2, revenue=40000)], **kw)
    assert short.low.p_ge_min == 0.0 and short.low.p_poor_trip == 1.0
    # два визита с p = 0.5 и заказом 60 000: P(≥ 100 000) = P(оба) = 0.25
    coin = ev.evaluate_day(day, [_visit(1, p=0.5), _visit(2, p=0.5)], **kw)
    assert coin.low.expected == pytest.approx(60000.0)
    assert coin.low.p_ge_min == pytest.approx(0.25, abs=0.07)
    assert coin.low.p_no_orders == pytest.approx(0.25, abs=0.07)
    assert coin.low.p_poor_trip == pytest.approx(0.75, abs=0.07)   # есть заказы, но < 150 000


def test_evaluate_day_deterministic_and_route():
    day = pl.PlanDay(5, 1, 2, ())
    rng = random.Random(1)
    visits = [_visit(i, (40.1 + rng.random() * 0.2, 44.4 + rng.random() * 0.2), p=0.4)
              for i in range(15)] + [_visit(99, None, p=0.4)]
    kw = dict(home=YEREVAN, norms=NORMS, manager_l100=9.0, workday=True)
    a = ev.evaluate_day(day, visits, **kw)
    b = ev.evaluate_day(day, visits, **kw)
    assert a == b                                        # тот же seed — тот же результат
    assert a.km <= a.km_rownum + 1e-9                    # NN+2-opt не хуже порядка fROWNUM
    assert a.visits == 16 and a.visits_no_coords == 1 and a.customer_ids[-1] == 99
    assert sorted(a.customer_ids) == sorted(v.customer_id for v in visits)
    assert a.liters == pytest.approx(a.km * 9.0 / 100)
    assert a.plan_min == pytest.approx(a.drive_min + 160.0)
    other = ev.evaluate_day(pl.PlanDay(6, 1, 2, ()), visits, **kw)   # другой менеджер — те же числа визитов
    assert other.low == a.low


# --- парк машин: точная оценка дня доставки (fleet-plan §2) ---
# Было: «рейс машины менеджера» (truck_day — машина одного менеджера, правило №3). Отменено ответом
# владельца №29: машина закреплена за водителем, заказы дня всех менеджеров распределяются по парку.

TN = fl.TruckNorms(work_minutes=540.0, unload_min_per_stop=8.0, unload_min_per_tonne=6.0)
FORD = fl.FleetTruck('333DO33', 'FORD', 3500.0, 16.0)
HOWO = fl.FleetTruck('991AT61', 'HOWO', 10000.0, 30.0)


def _line_matrix(xs):
    """Точки на прямой: склад — 0, км = |x_i − x_j|; минуты = км (60 км/ч)."""
    pts = [0.0, *xs]
    d = [[abs(a - b) for b in pts] for a in pts]
    return d, [row[:] for row in d]


def _stops(kgs, rev=50000.0):
    return [fl._Stop(i + 1, kg, rev, TN.unload(kg)) for i, kg in enumerate(kgs)]


def test_fleet_savings_known_optimum_two_sides():
    d, m = _line_matrix([-10, -11, 10, 11])
    trips = fl.plan_trips(_stops([500] * 4), d, m, [HOWO], TN)
    assert len(trips) == 2 and sum(t.km for t in trips) == pytest.approx(44.0)   # по рейсу на сторону
    assert all(t.truck == HOWO.car_code and not t.extra and t.stops == 2 for t in trips)
    assert sum(t.liters for t in trips) == pytest.approx(44.0 * 30.0 / 100)
    assert fl.plan_trips([], d, m, [HOWO], TN) == [] and fl.plan_trips(_stops([1]), d, m, [], TN) == []


def test_fleet_capacity_several_trips_of_one_truck():
    d, m = _line_matrix([5, 6, 7, 8])
    trips = fl.plan_trips(_stops([1500] * 4), d, m, [FORD], TN)
    # по двое в рейс (3 000 ≤ 3 500, третий переполнил бы): {5, 6} и {7, 8} — оптимум 12 + 16 км
    assert sorted(t.km for t in trips) == pytest.approx([12.0, 16.0])
    assert all(t.kg <= FORD.capacity_kg and t.truck == FORD.car_code and not t.extra for t in trips)
    heavy = fl.plan_trips(_stops([25000]), d, m, [HOWO], TN)            # тяжелее машины — 3 поездки поровну
    assert len(heavy) == 3 and all(t.kg == pytest.approx(25000 / 3) for t in heavy)
    assert sum(t.km for t in heavy) == pytest.approx(3 * 10.0)


def test_fleet_time_window_and_shortage():
    d, m = _line_matrix([30, -30, 31])
    short = fl.TruckNorms(work_minutes=100.0, unload_min_per_stop=8.0, unload_min_per_tonne=6.0)
    stops = [fl._Stop(i + 1, 100.0, 50000.0, short.unload(100.0)) for i in range(3)]
    one = fl.plan_trips(stops, d, m, [FORD], short)
    # {30, 31}: 62 км + 2 разгрузки ≈ 79 мин; {−30}: 60 + 8,6 — вместе больше 100 мин: второй — за пределами дня
    assert sorted(t.extra for t in one) == [False, True]
    assert all(t.minutes <= 100.0 for t in one)                          # рейс не длиннее рабочего дня
    two = fl.plan_trips(stops, d, m, [FORD, fl.FleetTruck('X', None, 3500.0, 16.0)], short)
    assert not any(t.extra for t in two) and {t.truck for t in two} == {'333DO33', 'X'}
    used = Counter()
    for t in two:
        used[t.truck] += t.minutes
    assert max(used.values()) <= 100.0                                   # время машины соблюдено


def test_fleet_heavy_trip_to_big_truck_light_to_cheapest():
    d, m = _line_matrix([10, -10])
    trips = fl.plan_trips(_stops([5000, 500]), d, m, [HOWO, FORD], TN)
    by_kg = {t.kg: t for t in trips}
    assert by_kg[5000].truck == HOWO.car_code and by_kg[500].truck == FORD.car_code   # 16 л < 30 л
    assert by_kg[5000].liters == pytest.approx(20.0 * 30.0 / 100)


def test_fleet_trip_recut_for_smaller_truck_with_time():
    d, m = _line_matrix([1, 2, 3, 4, 5, 6, -50])
    tn = fl.TruckNorms(work_minutes=200.0, unload_min_per_stop=8.0, unload_min_per_tonne=6.0)
    stops = [fl._Stop(i + 1, 1000.0, 50000.0, tn.unload(1000.0)) for i in range(6)]
    stops.append(fl._Stop(7, 9000.0, 50000.0, tn.unload(9000.0)))
    trips = fl.plan_trips(stops, d, m, [HOWO, FORD], tn)
    # 9 т (162 мин) занимает HOWO; рейс 6 т ему уже не успеть, а FORD не поднимет — он режется по 3,5 т
    assert not any(t.extra for t in trips)
    ford = [t for t in trips if t.truck == FORD.car_code]
    assert len(ford) == 2 and all(t.kg <= 3500 for t in ford)
    assert sum(t.kg for t in trips) == pytest.approx(15000.0)


def test_fleet_day_deterministic_and_order_free():
    rng = random.Random(4)
    vs = [fl.DeliveryVisit(i, 1 + i % 3, (40.1 + rng.random() * 0.2, 44.4 + rng.random() * 0.3),
                           ev.Draw(0.6, ((60000.0, 300.0), (20000.0, 80.0))), ev.Draw(0.8, ((90000.0, 600.0),)))
          for i in range(40)]
    a = fl.fleet_day(1, 2, vs, (40.15, 44.46), [FORD, HOWO], NORMS, TN, 150000.0)
    b = fl.fleet_day(1, 2, list(reversed(vs)), (40.15, 44.46), [FORD, HOWO], NORMS, TN, 150000.0)
    assert a == b and a.trips > 0 and a.kg_peak > a.kg
    assert a.liters == pytest.approx(sum(t.liters for t in a.trucks))
    assert a.km == pytest.approx(sum(t.km for t in a.trucks))
    assert all(t.load_pct <= 100.0 + 1e-9 for t in a.trucks)            # тоннаж не превышен
    assert a.trip_revenue / a.trips_total == pytest.approx(
        sum(v.year.p * fmean(x[0] for x in v.year.values) for v in vs) / a.trips, rel=0.25)


def test_fleet_delivery_key():
    assert fl.delivery_key(1, 3, 2) == (1, 4)
    assert fl.delivery_key(1, 6, 2) == (2, 1) and fl.delivery_key(2, 6, 2) == (1, 1)   # сб → пн
    assert fl.delivery_key(1, 7, 1) == (1, 1) and fl.delivery_key(1, 5, 1) == (1, 6)


def test_fleet_trucks_only_active_and_complete():
    trucks = {'B': st.Truck('B', 3500.0, 16.0, 3, True), 'A': st.Truck('A', None, 30.0, None, True),
              'C': st.Truck('C', 10000.0, 30.0, None, False), 'D': st.Truck('D', 10000.0, None)}
    fleet, incomplete = fl.fleet_trucks(trucks, {'B': 'FORD'})
    assert fleet == [fl.FleetTruck('B', 'FORD', 3500.0, 16.0)] and incomplete == ['A', 'D']


def test_weak_day_uses_displayed_probability():
    from types import SimpleNamespace as NS
    assert not ev._is_weak_day(NS(low=NS(p_ge_min=0.496)))   # на экране 0.50 — не «слабый»
    assert ev._is_weak_day(NS(low=NS(p_ge_min=0.494)))       # на экране 0.49 — «слабый»


def test_overtime_and_flags():
    day = pl.PlanDay(1, 1, 7, ())
    visits = [_visit(i, minutes=20.0) for i in range(30)]    # 600 мин визитов > 540
    r = ev.evaluate_day(day, visits, home=None, norms=NORMS, manager_l100=9.0, workday=False)
    assert r.overtime_min == pytest.approx(60.0)
    assert set(r.flags) == {'overtime', 'no_home', 'off_day', 'sunday_order'}


def test_resolve_season_manual_overrides_auto():
    idx = {m: 1.0 for m in range(1, 13)}
    idx.update({1: 0.5, 2: 0.7, 7: 1.5, 8: 1.3})
    base = dict(st.DEFAULT_SETTINGS)
    auto = ev.resolve_season(idx, base)
    assert (auto.low, auto.peak, auto.source) == ([1, 2], [7, 8], 'auto')
    mixed = ev.resolve_season(idx, dict(base, low_months=[8, 12]))   # ручной низкий важнее авто-пика
    assert (mixed.low, mixed.peak, mixed.source) == ([8, 12], [7], 'mixed')
    assert (mixed.detected_low, mixed.detected_peak) == ([1, 2], [7, 8])
    manual = ev.resolve_season(None, dict(base, low_months=[1], peak_months=[7]))
    assert (manual.low, manual.peak, manual.source) == ([1], [7], 'manual')


def test_visit_coord_priority_chain():
    snap = make_snapshot()
    assert ev.visit_coord(snap, 101, 1001) == geo.Coord(40.18, 44.50, 'erp')    # адрес шаблона
    assert ev.visit_coord(snap, 101, 0) == geo.Coord(40.18, 44.50, 'erp')       # адрес по умолчанию
    assert ev.visit_coord(snap, 101, 1002).source == 'erp'   # чужой адрес не берём → адрес клиента
    assert ev.visit_coord(snap, 103, 0) == geo.Coord(40.17, 44.49, 'gps')       # только GPS
    assert ev.visit_coord(snap, 999, 0) == geo.NO_COORD


def _av(agent, day, hh, mm, result='01', lat=None, lon=None, dur=5):
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=hh, minutes=mm)
    return ev.ActualVisit(1, agent, day, start, start + timedelta(minutes=dur), lat, lon, 10.0, result)


def test_manager_facts():
    d1, d2 = date(2026, 9, 1), date(2026, 9, 2)
    visits = [_av(7, d1, 10, 0), _av(7, d1, 11, 0, '03'), _av(7, d1, 12, 0), _av(7, d1, 13, 0, '03'),
              _av(7, d1, 14, 0), _av(7, d1, 15, 55)]
    visits += [_av(7, d2, 9, 0) for _ in range(4)]           # < 5 визитов — день не в факте
    f = ev.manager_facts(visits, date(2026, 8, 1), date(2026, 9, 30))[7]
    assert (f.days, f.visits_per_day) == (1, 6.0)
    assert f.day_start_min == 600.0 and f.day_end_min == 16 * 60
    assert f.hours == pytest.approx(6.0) and f.productive_share == pytest.approx(4 / 6)
    assert (f.work_hours, f.pause_hours, f.track_days) == (None, None, 0)   # трека не дали


def _gap_day_fixes(day):
    """Трек дня с разрывом в середине (окно визитов 10:00–14:05, 245 мин):
    9:55 и 10:05 — у магазина (сегмент 10 мин пересекает начало окна);
    10:05–11:10 — едет «туда-обратно» 30 км/ч (точка в минуту);
    11:10–12:50 — точек нет 100 мин: разрыв > 15 мин;
    12:50–14:00 — стоит у магазинов, без точек 13:20–13:30 (10 мин ≤ 15 — не разрыв);
    14:12 — сегмент 12 мин пересекает конец окна."""
    def t(hh, mm):
        return datetime.combine(day, datetime.min.time()) + timedelta(hours=hh, minutes=mm)

    shop = (40.15, 44.50)
    drive = _zigzag(t(10, 5), shop, 0.5, 65)
    stand = [geo.Fix(t(12, 50) + timedelta(minutes=m), *shop, 10.0) for m in range(71)
             if not 30 < m < 40]
    return [geo.Fix(t(9, 55), *shop, 10.0), *drive, *stand, geo.Fix(t(14, 12), *shop, 10.0)]


def test_work_pause_split_with_gap_in_the_middle_of_the_day():
    day = date(2026, 9, 1)
    fixes = _gap_day_fixes(day)
    start, end = datetime(2026, 9, 1, 10, 0), datetime(2026, 9, 1, 14, 5)
    work, pauses = ev.work_pause_minutes(fixes, start, end)
    # 10:00–10:05 (обрезок сегмента 9:55–10:05) + 65 мин езды + 70 мин у магазинов + 14:00–14:05
    assert work == pytest.approx(5 + 65 + 70 + 5)
    assert pauses == pytest.approx(100.0) and work + pauses == pytest.approx(245.0)
    # работа = езда + стоянки: стоянки внутри окна — те же, что считает stationary_minutes (T2)
    assert ev.stationary_minutes([f for f in fixes if start <= f.at <= end]) == pytest.approx(70.0)
    # трека в окне нет — всё окно пауза; пустое окно — ноль
    assert ev.work_pause_minutes(fixes, datetime(2026, 9, 1, 11, 20),
                                 datetime(2026, 9, 1, 12, 40)) == (0.0, 80.0)
    assert ev.work_pause_minutes([], start, end) == (0.0, 245.0)
    assert ev.work_pause_minutes(fixes, start, start) == (0.0, 0.0)


def test_manager_facts_split_work_and_pauses_by_track_days():
    d1, d2, d3 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)
    starts = [(10, 0), (10, 30), (11, 0), (13, 0), (13, 30), (14, 0)]
    visits = [_av(7, d, hh, mm) for d in (d1, d2, d3) for hh, mm in starts]   # окно 10:00–14:05
    visits += [_av(8, d1, 9 + k, 0) for k in range(5)]
    few = _gap_day_fixes(d3)[:29]                                  # < 30 точек — дня нет в работе/паузах
    day_fixes = {(7, d1): _gap_day_fixes(d1), (7, d3): few}        # d2 — без трека
    facts = ev.manager_facts(visits, d1, date(2026, 9, 30), day_fixes=day_fixes)
    f = facts[7]
    # окно и дни — по всем дням факта, работа и паузы — только по дню с треком
    assert (f.days, f.track_days) == (3, 1) and f.hours == pytest.approx(245 / 60)
    assert f.work_hours == pytest.approx(145 / 60) and f.pause_hours == pytest.approx(100 / 60)
    assert (facts[8].work_hours, facts[8].pause_hours, facts[8].track_days) == (None, None, 0)
    assert ev.manager_facts(visits, d1, date(2026, 9, 30))[7].track_days == 0   # без day_fixes


def test_detour_calibration():
    day = date(2026, 9, 1)
    visits = [_av(3, day, 10, 10 * k, lat=40.10 + 0.009 * k, lon=44.50) for k in range(8)]
    t0 = visits[0].start
    straight = [geo.Fix(t0 + timedelta(seconds=40 * i), 40.10 + 0.0009 * i, 44.50, 10.0)
                for i in range(71)]
    days = ev.day_tracks(visits, {(3, day): straight}, day, day + timedelta(days=1))
    assert len(days) == 1
    detour, used = ev.calibrate_detour(days)
    assert used == 1 and detour == pytest.approx(1.0, abs=0.01)
    assert ev.day_tracks(visits, {(3, day): straight[:20]}, day, day + timedelta(days=1)) == []


KM_PER_DEG_LAT = geo.haversine_km((40.0, 44.5), (41.0, 44.5))   # по меридиану расстояние ∝ Δшироты


def _zigzag(t0, start, km_step, segments, every_s=60):
    """Трек «туда-обратно» по меридиану: segments отрезков по km_step км, точка каждые every_s с.
    Чётное число отрезков — трек кончается в start."""
    dlat = km_step / KM_PER_DEG_LAT
    return [geo.Fix(t0 + timedelta(seconds=every_s * i), start[0] + dlat * (i % 2), start[1], 10.0)
            for i in range(segments + 1)]


def _day_track(fixes, day=date(2026, 9, 1)):
    return ev.DayTrack(3, day, 10, 0.0, 0.0, tuple(fixes), 10, None)


def test_speed_calibration_is_time_weighted_moving_speed():
    shop = (40.15, 44.50)                                        # ≈ 3 км от центра — город
    t = datetime(2026, 9, 1, 9, 0)
    slow = _zigzag(t, shop, 0.5, 70)                             # 70 мин по 0.5 км/мин — 30 км/ч
    fast = _zigzag(slow[-1].at + timedelta(minutes=1), shop, 5.0, 14, every_s=300)   # 70 мин, 60 км/ч
    drive = slow + fast
    # время-взвешенно: (35 + 70) км / (70 + 70) мин = 45 км/ч (медиана сегментов дала бы 30)
    city, region = ev.calibrate_speeds([_day_track(drive)], YEREVAN, 12.0)
    assert city == pytest.approx(45.0, rel=1e-9) and region is None
    t = drive[-1].at + timedelta(minutes=1)
    walk = _zigzag(t, shop, 0.08, 30)                            # ходьба 4.8 км/ч
    shuffle = _zigzag(walk[-1].at + timedelta(seconds=20), shop, 0.04, 30, every_s=20)  # 7.2 км/ч, но < 50 м
    t = shuffle[-1].at
    jump = [geo.Fix(t + timedelta(minutes=1), shop[0] + 0.045, shop[1], 10.0),     # 5 км за минуту — скачок
            geo.Fix(t + timedelta(minutes=2), shop[0], shop[1], 10.0)]
    gap = [geo.Fix(t + timedelta(minutes=14), shop[0] + 0.054, shop[1], 10.0)]     # 6 км за 12 мин — разрыв
    noisy = _day_track(drive + walk + shuffle + jump + gap)
    assert ev.calibrate_speeds([noisy], YEREVAN, 12.0) == (city, None)   # ходьба и шум не занижают


def test_speed_calibration_needs_an_hour_of_moving():
    region = (40.50, 44.50)                                      # ≈ 36 км от центра — область
    t = datetime(2026, 9, 1, 9, 0)
    short = _day_track(_zigzag(t, region, 0.75, 50))             # 50 мин по 45 км/ч
    assert ev.calibrate_speeds([short], YEREVAN, 12.0) == (None, None)
    other_day = _day_track(_zigzag(t, region, 0.75, 20), day=date(2026, 9, 2))   # ещё 20 мин
    city, speed = ev.calibrate_speeds([short, other_day], YEREVAN, 12.0)
    assert city is None and speed == pytest.approx(45.0, rel=1e-9)


def test_stationary_minutes_rules():
    t0 = datetime(2026, 9, 1, 10, 0)

    def at(minutes, km):
        return geo.Fix(t0 + timedelta(minutes=minutes), 40.10 + km / KM_PER_DEG_LAT, 44.5, 10.0)

    fixes = [at(0, 0.0), at(3, 0.01),      # стоит у магазина 3 мин (джиттер 10 м)
             at(5, 0.17),                  # ходьба 160 м за 2 мин (4.8 км/ч) — тоже стоянка
             at(6, 0.67),                  # едет 30 км/ч — не стоянка
             at(26, 0.67),                 # разрыв трека 20 мин — не считается
             at(27, 10.67)]                # скачок GPS 600 км/ч — не движение (у магазина GPS «прыгает»)
    assert ev.stationary_minutes(fixes) == pytest.approx(6.0)
    assert ev.stationary_minutes([at(0, 0.0), at(20, 0.0)]) is None   # только разрыв — трека нет
    assert ev.stationary_minutes([at(0, 0.0)]) is None and ev.stationary_minutes([]) is None


def _visit_day(day, agent=3, no_gps=(9,)):
    """10 визитов с 10:00: 5 мин у клиента, затем 7 мин дороги по 0.5 км/мин; трек — точка
    в минуту с 9:30 до 12:30 (до первого и после последнего визита — вне окна)."""
    base = datetime.combine(day, datetime.min.time()) + timedelta(hours=10)

    def lat(minute):
        k, r = divmod(max(0, minute), 12)
        return 40.10 + (3.5 * k + 0.5 * max(0, r - 5)) / KM_PER_DEG_LAT

    visits = [ev.ActualVisit(100 + k, agent, day, base + timedelta(minutes=12 * k),
                             base + timedelta(minutes=12 * k + 5),
                             None if k in no_gps else lat(12 * k), None if k in no_gps else 44.5,
                             10.0, '01') for k in range(10)]
    fixes = [geo.Fix(base + timedelta(minutes=m), lat(m), 44.5, 10.0) for m in range(-30, 150)]
    return visits, fixes


def test_visit_minutes_calibrated_from_gps_stops():
    visits, day_fixes = [], {}
    for i in range(5):
        d = date(2026, 9, 1) + timedelta(days=i)
        vs, fixes = _visit_day(d)
        visits += vs
        day_fixes[(3, d)] = fixes
    tracks = ev.day_tracks(visits, day_fixes, date(2026, 9, 1), date(2026, 9, 6))
    # визит без GPS-точки не мешает дню попасть в калибровку и считается в визитах дня
    assert len(tracks) == 5 and all((t.visits, t.visits_all) == (9, 10) for t in tracks)
    # 10 визитов × 5 мин; стоянка до 10:00 и после конца последнего визита — вне окна
    assert all(t.stationary_min == pytest.approx(50.0) for t in tracks)
    assert ev.calibrate_visit_minutes(tracks) == pytest.approx(5.0)
    assert ev.calibrate_visit_minutes(tracks[:4]) is None                 # меньше 5 дней
    center = (40.10, 44.5)
    calib = ev.calibrate(visits, {3: [f for fs in day_fixes.values() for f in fs]},
                         date(2026, 9, 1), date(2026, 9, 6), center, 5.0)
    assert calib.visit_min_avg == pytest.approx(5.0)


def test_visit_norms_gps_manual_default():
    s = dict(st.DEFAULT_SETTINGS)
    shares = {'small': 0.5, 'medium': 0.3, 'large': 0.2}
    assert ev.visit_norms(s, None, shares) == {'visit_min_small': (7.0, 'default'),
                                               'visit_min_medium': (10.0, 'default'),
                                               'visit_min_large': (20.0, 'default')}
    # m = 5 / (0.7·0.5 + 1·0.3 + 2·0.2) = 4.76: мелкий 3.33 → 3.5, средний 4.76 → 5, крупный 9.52 → 9.5
    gps = ev.visit_norms(s, 5.0, shares)
    assert gps == {'visit_min_small': (3.5, 'gps'), 'visit_min_medium': (5.0, 'gps'),
                   'visit_min_large': (9.5, 'gps')}
    assert sum(shares[k] * gps[f'visit_min_{k}'][0] for k in shares) == pytest.approx(5.0, abs=0.25)
    # ручное число перекрывает только свой класс
    assert ev.visit_norms(dict(s, visit_min_large=15), 5.0, shares) == {
        'visit_min_small': (3.5, 'gps'), 'visit_min_medium': (5.0, 'gps'),
        'visit_min_large': (15.0, 'manual')}
    # пределы 1..120, пустой план — m = среднему
    assert ev.visit_norms(s, 0.5, shares)['visit_min_small'] == (1.0, 'gps')
    assert ev.visit_norms(s, 100.0, {'small': 1.0})['visit_min_large'] == (120.0, 'gps')
    assert ev.visit_norms(s, 6.0, {})['visit_min_medium'] == (6.0, 'gps')
    assert (ev._half_up(3.25), ev._half_up(3.24), ev._half_up(3.75)) == (3.5, 3.0, 4.0)   # не «к чётному»


def test_day_time_split_work_and_commute():
    day = pl.PlanDay(1, 1, 1, ())
    a, b = (YEREVAN[0] + 0.02, YEREVAN[1]), (YEREVAN[0] + 0.04, YEREVAN[1])   # в городе
    visits = [_visit(1, a, minutes=5.0), _visit(2, b, minutes=5.0), _visit(3, None, minutes=5.0)]
    kw = dict(norms=NORMS, manager_l100=9.0, workday=True)

    def leg(p, q):   # все участки в городе: 25 км/ч
        return geo.haversine_km(p, q) * 1.3 / 25.0 * 60

    r = ev.evaluate_day(day, visits, home=YEREVAN, **kw)
    assert r.work_min == pytest.approx(15.0 + leg(a, b))                  # визиты + дорога между ними
    assert r.commute_min == pytest.approx(leg(YEREVAN, a) + leg(b, YEREVAN))
    assert r.plan_min == pytest.approx(r.work_min + r.commute_min)
    no_home = ev.evaluate_day(day, visits, home=None, **kw)
    assert no_home.commute_min == 0.0 and no_home.work_min == pytest.approx(no_home.plan_min)
    # переработка — по всему дню: окно 9:00–18:00 включает дорогу из дома
    far_home = ev.evaluate_day(day, [_visit(i, a, minutes=20.0) for i in range(26)],
                               home=(40.5, 44.5), **kw)
    assert far_home.work_min == pytest.approx(520.0) and far_home.plan_min > 540.0
    assert far_home.overtime_min == pytest.approx(far_home.plan_min - 540.0)


def test_active_agents_orders_or_visits_in_window():
    since, until = date(2026, 8, 5), date(2026, 9, 30)
    orders = [dm.Order(1, date(2026, 9, 1), 7, 100.0, 1.0),
              dm.Order(1, date(2026, 8, 4), 8, 100.0, 1.0)]                  # агент 8 — до окна
    visits = [_av(9, date(2026, 8, 5), 10, 0), _av(10, date(2026, 9, 30), 10, 0)]   # 10 — после
    assert ev.active_agents(orders, visits, since, until) == frozenset({7, 9})


def test_season_empty_falls_back_to_extreme_months():
    idx = {m: 1.0 + 0.01 * m for m in range(1, 13)}   # пороги 0.8 / 1.2 не проходит ни один месяц
    idx.update({2: 0.85, 7: 1.15})
    se = ev.resolve_season(idx, dict(st.DEFAULT_SETTINGS))
    assert (se.detected_low, se.detected_peak) == ([], [])
    assert (se.low, se.peak, se.fallback, se.source) == ([1, 2, 3], [7, 11, 12], ('low', 'peak'), 'auto')
    # ручной низкий сезон забрал все пиковые месяцы — пик из 3 самых высоких среди остальных
    idx = {m: 1.0 for m in range(1, 13)}
    idx.update({1: 0.5, 2: 0.7, 6: 1.3, 7: 1.5, 8: 1.4, 9: 1.1, 10: 1.05})
    mixed = ev.resolve_season(idx, dict(st.DEFAULT_SETTINGS, low_months=[6, 7, 8]))
    assert (mixed.low, mixed.peak, mixed.fallback) == ([6, 7, 8], [3, 9, 10], ('peak',))
    assert ev.resolve_season(idx, dict(st.DEFAULT_SETTINGS)).fallback == ()   # пороги сработали
    assert ev.resolve_season(None, dict(st.DEFAULT_SETTINGS)).fallback == ()  # индекса нет


def test_road_norms_manual_then_gps_then_default():
    s = dict(st.DEFAULT_SETTINGS)
    assert all(s[k] is None for k in ev.ROAD_NORMS)                  # по умолчанию — «авто»
    assert ev.road_norms(s, None) == {'detour_factor': (1.3, 'default'),
                                      'speed_city_kmh': (25.0, 'default'),
                                      'speed_region_kmh': (45.0, 'default')}
    calib = ev.Calibration(1.4049, 22.14, None, 12)
    assert ev.road_norms(dict(s, speed_city_kmh=30), calib) == {
        'detour_factor': (1.4, 'gps'), 'speed_city_kmh': (30.0, 'manual'),
        'speed_region_kmh': (45.0, 'default')}
    # калибровка вне допустимого диапазона — ближайшее допустимое (его и закрепит «Применить»)
    assert ev.road_norms(s, ev.Calibration(0.93, 3.0, 150.0, 5)) == {
        'detour_factor': (1.0, 'gps'), 'speed_city_kmh': (5.0, 'gps'), 'speed_region_kmh': (120.0, 'gps')}
    n = ev.Norms.from_settings(s, calib)
    assert (n.detour, n.speed_city_kmh, n.speed_region_kmh) == (1.4, 22.1, 45.0)


# ============================== store ==============================

REF = st.RefData(car_codes=frozenset({'CAR1', 'CAR2'}), agent_ids=frozenset({1, 2}),
                 group_codes=frozenset({'017', '036'}))


@pytest.fixture
def store(tmp_path):
    return st.Store(str(tmp_path / 'routes.db'))


def _save(store, payload):
    changes, errors = st.validate_payload(payload, store.load(), REF)
    assert errors == {}
    store.save(changes, 'qa')


def test_store_defaults_on_new_db(store, tmp_path):
    b = store.load()
    assert b.settings == st.DEFAULT_SETTINGS
    assert (b.depot, b.trucks, b.managers) == (None, {}, {})
    assert b.profile(5) == st.ManagerProfile(5)
    with sqlite3.connect(str(tmp_path / 'routes.db')) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)
        assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'


def test_store_rejects_invalid_values_with_messages(store):
    payload = {
        'settings': {'detour_factor': 5, 'work_start': '9:00', 'workdays': [], 'visit_min_small': True,
                     'low_months': [1, 2], 'peak_months': [2, 7], 'chain_groups': ['999'],
                     'fuel_price_diesel': 0, 'city_center_lat': 45.0, 'bogus': 1},
        'depot': {'lat': 50.0, 'lon': 44.0},
        'trucks': [{'car_code': 'NOPE'}, {'car_code': 'CAR1', 'capacity_kg': 50, 'agent_id': 99}],
        'managers': [{'agent_id': 1, 'car_fuel_type': 'kerosene', 'home_lat': 40.1},
                     {'agent_id': 99}],
        'extra': {},
    }
    changes, errors = st.validate_payload(payload, store.load(), REF)
    assert changes is None
    assert set(errors) == {
        '_', 'settings.detour_factor', 'settings.work_start', 'settings.workdays',
        'settings.visit_min_small', 'settings.peak_months', 'settings.chain_groups',
        'settings.fuel_price_diesel', 'settings.city_center_lat', 'settings.bogus', 'depot',
        'trucks.0.car_code', 'trucks.1.capacity_kg', 'trucks.1.agent_id',
        'managers.0.car_fuel_type', 'managers.0.home', 'managers.1.agent_id'}
    assert errors['settings.detour_factor'] == 'թույլատրելի է՝ 1-ից մինչև 3'
    assert errors['settings.city_center_lat'] == 'թույլատրելի է՝ 38.8-ից մինչև 41.4'   # координаты — с точкой
    assert errors['depot'] == 'կետը Հայաստանից դուրս է'
    # тексты для страницы настроек — армянские (№58), без кириллицы
    assert all(re.search('[Ա-֏]', msg) and not re.search('[а-яА-ЯёЁ]', msg) for msg in errors.values())


def test_store_cross_field_rules(store):
    cases = [
        ({'work_start': '18:00', 'work_end': '09:00'}, 'settings.work_end'),
        ({'size_small_max_kg': 300, 'size_medium_max_kg': 250}, 'settings.size_medium_max_kg'),
        ({'size_small_max_kg': 0}, 'settings.size_small_max_kg'),
        ({'low_months': []}, 'settings.low_months'),
        ({'workdays': [1, 1]}, 'settings.workdays'),
    ]
    for patch, key in cases:
        _, errors = st.validate_payload({'settings': patch}, store.load(), REF)
        assert set(errors) == {key}, (patch, errors)


def test_store_validation_error_writes_nothing(store):
    before = store.load()
    payload = {'settings': {'detour_factor': 1.5}, 'depot': {'lat': 40.15, 'lon': 44.46},
               'trucks': [{'car_code': 'CAR1', 'capacity_kg': 3000}],
               'managers': [{'agent_id': 1, 'car_fuel_type': 'coal'}]}
    changes, errors = st.validate_payload(payload, before, REF)
    assert changes is None and list(errors) == ['managers.0.car_fuel_type']
    assert store.load() == before


def test_store_save_is_atomic_on_failure(store, monkeypatch):
    before = store.load()
    changes, errors = st.validate_payload(
        {'settings': {'detour_factor': 1.5}, 'depot': {'lat': 40.15, 'lon': 44.46},
         'trucks': [{'car_code': 'CAR1', 'capacity_kg': 3000}]}, before, REF)
    assert not errors
    original = st.Store._write

    def failing_write(conn, ch, now, user):
        original(conn, ch, now, user)          # всё записали внутри транзакции…
        raise RuntimeError('сбой посреди сохранения')

    monkeypatch.setattr(st.Store, '_write', staticmethod(failing_write))
    with pytest.raises(RuntimeError):
        store.save(changes, 'qa')
    assert store.load() == before              # …и ничего не осталось


def test_store_roundtrip_and_merge(store):
    defaults_fp = store.load().fingerprint()
    _save(store, {
        'settings': {'detour_factor': 1.45, 'fuel_price_diesel': 490, 'chain_groups': ['017'],
                     'low_months': [1, 2], 'workdays': [6, 1, 2]},
        'depot': {'lat': 40.15, 'lon': 44.46},
        'trucks': [{'car_code': 'CAR1', 'capacity_kg': 3000, 'fuel_l_per_100km': 18,
                    'agent_id': 1, 'active': True}],
        'managers': [{'agent_id': 1, 'included': False, 'home_lat': 40.1, 'home_lon': 44.5,
                      'car_fuel_l_per_100km': 9.5, 'car_fuel_type': 'lpg'}],
    })
    b = store.load()
    assert b.settings['detour_factor'] == 1.45 and b.settings['fuel_price_diesel'] == 490
    assert b.settings['chain_groups'] == ['017'] and b.settings['workdays'] == [1, 2, 6]
    assert b.settings['low_months'] == [1, 2] and b.settings['peak_months'] is None
    assert b.depot == (40.15, 44.46)
    t = b.trucks['CAR1']
    assert (t.capacity_kg, t.fuel_l_per_100km, t.agent_id, t.active, t.updated_by) == \
        (3000, 18, 1, True, 'qa')
    m = b.managers[1]
    assert (m.included, m.home, m.car_fuel_l_per_100km, m.car_fuel_type) == \
        (False, (40.1, 44.5), 9.5, 'lpg')
    assert b.fingerprint() != defaults_fp
    # upsert: не переданные поля сохраняются; depot: null — склад убран
    _save(store, {'trucks': [{'car_code': 'CAR1', 'active': False}], 'depot': None,
                  'managers': [{'agent_id': 1, 'included': True}]})
    b = store.load()
    assert (b.trucks['CAR1'].capacity_kg, b.trucks['CAR1'].active) == (3000, False)
    assert b.depot is None and b.managers[1].home == (40.1, 44.5) and b.managers[1].included


def test_store_corruption_is_explicit(tmp_path):
    garbage = tmp_path / 'garbage.db'
    garbage.write_bytes(b'this is not a sqlite database' * 100)
    with pytest.raises(st.StoreError):
        st.Store(str(garbage)).load()

    foreign = tmp_path / 'foreign.db'
    with sqlite3.connect(str(foreign)) as conn:
        conn.execute('CREATE TABLE other(x)')
    with pytest.raises(st.StoreError):
        st.Store(str(foreign)).load()

    s = st.Store(str(tmp_path / 'ok.db'))
    s.load()
    with sqlite3.connect(str(tmp_path / 'ok.db')) as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES('detour_factor', '{broken')")
    with pytest.raises(st.StoreError, match='detour_factor'):
        s.load()
    with sqlite3.connect(str(tmp_path / 'ok.db')) as conn:
        conn.execute("UPDATE settings SET value = '7' WHERE key = 'detour_factor'")
    with pytest.raises(st.StoreError, match='detour_factor'):   # вне диапазона — не молчим
        s.load()


def test_store_corrupted_rows_are_explicit(store, tmp_path):
    _save(store, {'trucks': [{'car_code': 'CAR1', 'capacity_kg': 3000, 'agent_id': 1}],
                  'managers': [{'agent_id': 1, 'car_fuel_type': 'diesel'}]})
    path = str(tmp_path / 'routes.db')
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE trucks SET active = 'no' WHERE car_code = 'CAR1'")
    with pytest.raises(st.StoreError, match='CAR1'):
        store.load()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE trucks SET active = 1, capacity_kg = 0 WHERE car_code = 'CAR1'")
    with pytest.raises(st.StoreError, match='տոննաժ'):
        store.load()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE trucks SET capacity_kg = 3000 WHERE car_code = 'CAR1'")
        conn.execute("UPDATE manager_profile SET car_fuel_type = 'coal', home_lat = 40.1")
    with pytest.raises(st.StoreError, match='վառելիքի տեսակ'):
        store.load()


def test_store_load_is_one_read_transaction(store, tmp_path, monkeypatch):
    """Чужое сохранение между чтением настроек и склада не даёт «смешанный» набор."""
    other = st.Store(str(tmp_path / 'routes.db'))
    changes, errors = st.validate_payload(
        {'settings': {'min_day_revenue': 120000}, 'depot': {'lat': 40.15, 'lon': 44.46}},
        other.load(), REF)
    assert not errors
    real_connect = store._connect
    fired = []

    class RacingConn:   # перед чтением склада другое соединение успевает сохранить всё
        def __init__(self):
            self.conn = real_connect()

        def execute(self, sql, *args):
            if sql.startswith('SELECT lat, lon FROM depot') and not fired:
                fired.append(sql)
                other.save(changes, 'other')
            return self.conn.execute(sql, *args)

        @property
        def in_transaction(self):
            return self.conn.in_transaction

        def close(self):
            self.conn.close()

    monkeypatch.setattr(store, '_connect', RacingConn)
    b = store.load()
    assert fired
    assert b.settings['min_day_revenue'] == 100000 and b.depot is None   # всё — до чужого сохранения
    after = store.load()
    assert after.settings['min_day_revenue'] == 120000 and after.depot == (40.15, 44.46)


def test_huge_numbers_are_validation_errors(store):
    huge = 10 ** 400                     # JSON-целое больше предела float: float(huge) — OverflowError
    assert st._check_number(huge, 1, 3) == (None, 'թույլատրելի է՝ 1-ից մինչև 3')
    assert st._check_number(-huge, 1, 10000, nullable=True)[1] == 'թույլատրելի է՝ 1-ից մինչև 10 000'
    assert not geo.is_valid_point(huge, 44.5) and not geo.is_valid_point(40.18, -huge)
    payload = {'settings': {'detour_factor': huge, 'min_day_revenue': -huge},
               'depot': {'lat': huge, 'lon': 44.46},
               'trucks': [{'car_code': 'CAR1', 'capacity_kg': huge, 'agent_id': huge}],
               'managers': [{'agent_id': 1, 'home_lat': huge, 'home_lon': 44.5,
                             'car_fuel_l_per_100km': huge}]}
    changes, errors = st.validate_payload(payload, store.load(), REF)
    assert changes is None and set(errors) == {
        'settings.detour_factor', 'settings.min_day_revenue', 'depot', 'trucks.0.capacity_kg',
        'trucks.0.agent_id', 'managers.0.home', 'managers.0.car_fuel_l_per_100km'}


def test_store_included_is_tristate(store):
    # новая запись без ключа included — «авто» (дом не включает менеджера в расчёт)
    _save(store, {'managers': [{'agent_id': 1, 'home_lat': 40.1, 'home_lon': 44.5}]})
    b = store.load()
    assert b.managers[1].included is None and b.included_source(1) == 'auto'
    assert b.included(1, {1}) is True and b.included(1, set()) is False   # решает работа за 8 недель
    _save(store, {'managers': [{'agent_id': 1, 'included': False}]})
    b = store.load()
    assert (b.managers[1].included, b.included_source(1), b.included(1, {1})) == (False, 'manual', False)
    assert b.managers[1].home == (40.1, 44.5)
    _save(store, {'managers': [{'agent_id': 1, 'car_fuel_type': 'lpg'}]})   # без ключа — прежний выбор
    assert store.load().managers[1].included is False
    _save(store, {'managers': [{'agent_id': 1, 'included': None}]})         # «вернуть авто»
    b = store.load()
    assert (b.managers[1].included, b.included_source(1)) == (None, 'auto')
    assert b.managers[1].car_fuel_type == 'lpg'
    _, errors = st.validate_payload({'managers': [{'agent_id': 1, 'included': 'yes'}]}, b, REF)
    assert set(errors) == {'managers.0.included'}


_SCHEMA_V1 = (
    'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE depot(id INTEGER PRIMARY KEY CHECK (id = 1), lat REAL NOT NULL, lon REAL NOT NULL, '
    'updated_at TEXT NOT NULL, updated_by TEXT)',
    'CREATE TABLE trucks(car_code TEXT PRIMARY KEY, capacity_kg REAL, fuel_l_per_100km REAL, '
    'agent_id INTEGER, active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL, updated_by TEXT)',
    'CREATE TABLE manager_profile(agent_id INTEGER PRIMARY KEY, included INTEGER NOT NULL DEFAULT 1, '
    'home_lat REAL, home_lon REAL, car_fuel_l_per_100km REAL, car_fuel_type TEXT, '
    'updated_at TEXT NOT NULL, updated_by TEXT)',
    "INSERT INTO meta VALUES('schema_version', '1')",
)


def test_store_migrates_schema_1(tmp_path):
    path = str(tmp_path / 'v1.db')
    with sqlite3.connect(path) as conn:   # база первой версии: included NOT NULL
        for sql in _SCHEMA_V1:
            conn.execute(sql)
        conn.execute("INSERT INTO manager_profile VALUES(1, 0, 40.1, 44.5, 9.5, 'lpg', "
                     "'2026-09-01T10:00:00', 'owner')")
        conn.execute("INSERT INTO settings VALUES('min_day_revenue', '120000')")
    store = st.Store(path)
    b = store.load()
    # сохранённые 1/0 остаются явным выбором владельца, остальное не тронуто
    assert b.managers[1] == st.ManagerProfile(1, False, 40.1, 44.5, 9.5, 'lpg',
                                              '2026-09-01T10:00:00', 'owner')
    assert b.settings['min_day_revenue'] == 120000
    _save(store, {'managers': [{'agent_id': 1, 'included': None},
                               {'agent_id': 2, 'home_lat': 40.2, 'home_lon': 44.6}]})
    b = store.load()
    assert b.managers[1].included is None and b.managers[2].included is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)
        assert {r[1]: r[3] for r in conn.execute('PRAGMA table_info(manager_profile)')}['included'] == 0

    # миграция упала посреди (нет таблицы менеджеров) — база осталась версии 1, без мусора
    broken = str(tmp_path / 'v1-broken.db')
    with sqlite3.connect(broken) as conn:
        for sql in _SCHEMA_V1:
            conn.execute(sql)
        conn.execute('DROP TABLE manager_profile')
    with pytest.raises(st.StoreError):
        st.Store(broken).load()
    with sqlite3.connect(broken) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('1',)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert 'manager_profile_v2' not in tables


# ============================== guard ==============================

class _FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, *args):
        self.log.append((sql, args))

    def fetchall(self):
        return [(1,)]

    def close(self):
        pass


class _FakeConn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return _FakeCursor(self.log)


@pytest.mark.parametrize('sql', [
    "INSERT INTO SALES(fID) VALUES (1)",
    "UPDATE SALES SET fSTATE = 1",
    "DELETE FROM SALES",
    "SELECT * INTO #tmp FROM SALES WITH (NOLOCK)",
    "SELECT fID INTO NEWTABLE FROM SALES WITH (NOLOCK)",
    "EXEC sp_who",
    "SELECT 1 AS x WHERE 1 = 1 exec xp_cmdshell 'dir'",
    "SELECT 1; DROP TABLE SALES",
    "WITH x AS (SELECT 1 AS a) DELETE FROM SALES",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'y')",
    "SELECT * FROM sys.objects WHERE name = sp_help",
    "MERGE SALES AS t USING x ON 1 = 1",
    "TRUNCATE TABLE SALES",
    "   ",
    # T-SQL: «1DELETE» читается как «1» + «DELETE»; «;» между операторами не обязателен
    "SELECT 1DELETE FROM SALES",
    "SELECT 2UPDATE SALES SET fSTATE = 0",
    "SELECT 1 SHUTDOWN",
    "SELECT 1 KILL 55",
    "SELECT 1 WAITFOR DELAY '00:00:10'",
    "SELECT 1 BEGIN TRAN",
    "SELECT 1 DECLARE @x int",
    "SELECT * FROM SALES s WITH (TABLOCKX)",
    # триггеры, контрольная точка, смена пользователя, text/image, Service Broker, SEQUENCE
    "SELECT 1 DISABLE TRIGGER ALL ON SALES",
    "SELECT 1 enable trigger ALL ON SALES",
    "SELECT 1 CHECKPOINT",
    "SELECT 1 SETUSER 'dbo'",
    "SELECT 1 WRITETEXT SALES.fNOTE @ptr 'x'",
    "SELECT 1 UPDATETEXT SALES.fNOTE @ptr 0 NULL 'x'",
    "SELECT 1 RECEIVE TOP (1) * FROM q",
    "SELECT 1 SEND ON CONVERSATION @h",
    "SELECT NEXT VALUE FOR dbo.seq",
    "select next\n  value\tfor dbo.seq",
    "SELECT NEXT/**/VALUE -- комментарий\n FOR dbo.seq",
    # вложенный комментарий T-SQL; маркер комментария внутри строки не прячет код за ним
    "SELECT NEXT /* a /* b */ c */ VALUE FOR dbo.seq",
    "SELECT '/*', NEXT/**/VALUE FOR dbo.seq, '*/'",
    "SELECT [x/*], NEXT/**/VALUE FOR dbo.seq, [*/]",
    "SELECT 1 /* DELETE */",               # слово в комментарии — как и раньше, отказ
])
def test_select_guard_rejects_writes(sql):
    conn = _FakeConn()
    with pytest.raises(erp.UnsafeSqlError):
        erp._select(conn, sql)
    assert conn.log == []   # до базы запрос не дошёл


@pytest.mark.parametrize('sql', [
    "SELECT a.fID FROM SALESAGENTS a WITH (NOLOCK) WHERE a.fID = ?",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "\n   select s.fUPDATEDATE, s.fDELIVERYCAR FROM SALES s WITH (NOLOCK) WHERE s.fISN = ?",
    "SELECT a.fSETTING, a.fUSER, a.fOFFSET FROM T a WITH (NOLOCK) WHERE a.fID = ?",
    # слова внутри имён колонок — не операторы
    "SELECT a.fENABLED, a.fSENDDATE, a.fNEXTVALUE, a.fRECEIVED FROM T a WITH (NOLOCK) WHERE a.fID = ?",
    "SELECT a.fNEXT AS next_value, a.fVALUE FROM T a WITH (NOLOCK) WHERE a.fID = ?",
])
def test_select_guard_allows_reads(sql):
    conn = _FakeConn()
    assert erp._select(conn, sql, (5,)) == [(1,)]
    assert conn.log == [(sql, ([5],))]


def test_select_guard_is_linear_on_comment_runs():
    # прежняя регулярка с комментариями между словами фразы разбирала такое экспоненциально
    rejected = 'SELECT NEXT ' + '/* a */' * 40 + ' VALUE FOR x'
    allowed = 'SELECT NEXT ' + '/* a */' * 40 + ' VALUE x'          # фразы нет — запрос чтения
    started = time.perf_counter()
    with pytest.raises(erp.UnsafeSqlError):
        erp.check_sql(rejected)
    assert time.perf_counter() - started < 0.05
    started = time.perf_counter()
    erp.check_sql(allowed)
    assert time.perf_counter() - started < 0.05


def test_strip_comments_nesting_and_quotes():
    assert erp._strip_comments('SELECT 1 /* a /* b */ c */ AS x -- хвост\n FROM t') == \
        'SELECT 1   AS x  \n FROM t'
    # внутри строк и [имён] маркеры комментариев — текст; '' и ]] — экранирование
    assert erp._strip_comments("SELECT '/*', [a--b], 'it''s', [x]]y] /* z */") == \
        "SELECT '/*', [a--b], 'it''s', [x]]y]  "
    assert erp._strip_comments('SELECT 1 /* не закрыт') == 'SELECT 1  '


# ============================== статика ==============================

def _module_trees():
    return {p.name: ast.parse(p.read_text(encoding='utf-8')) for p in PKG.glob('*.py')}


def _imports(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split('.')[0])
    return names


_SQL_WRITE_RE = re.compile(
    r'\bINSERT\s+INTO\b|\bUPDATE\s+\S+\s+SET\b|\bDELETE\s+FROM\b|\bMERGE\s+\S+|\bCREATE\s+TABLE\b'
    r'|\bALTER\s+TABLE\b|\bDROP\s+TABLE\b|\bTRUNCATE\s+TABLE\b|\bEXEC(UTE)?\s+\w|\bSELECT\b.*\bINTO\b',
    re.IGNORECASE | re.DOTALL)


def test_static_erp_access_only_through_guard():
    trees = _module_trees()
    assert {'__init__.py', 'views.py', 'store.py', 'erp.py', 'geo.py', 'demand.py', 'plan.py',
            'tsp.py', 'evaluate.py', 'snapshot.py'} <= set(trees)
    for name, tree in trees.items():
        imports = _imports(tree)
        assert ('pyodbc' in imports) == (name == 'erp.py'), f'{name}: pyodbc только в erp.py'
        if name != 'store.py':
            assert 'sqlite3' not in imports, f'{name}: sqlite3 только в store.py'
    # в erp.py execute вызывается только внутри _select
    for node in ast.walk(trees['erp.py']):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != '_select':
            calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Attribute) and c.func.attr in ('execute', 'executemany')]
            assert not calls, f'erp.{node.name}: SQL мимо _select'


def test_static_no_sql_writes_outside_guard_list():
    for name, tree in _module_trees().items():
        if name == 'store.py':     # своё хранилище SQLite, не ERP
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not _SQL_WRITE_RE.search(node.value), f'{name}: {node.value[:60]!r}'


def test_static_erp_queries_pass_guard_and_use_nolock():
    sql_consts = {n: getattr(erp, n) for n in dir(erp) if n.startswith('SQL_')}
    assert len(sql_consts) >= 11
    for name, template in sql_consts.items():
        sql = template.format(ph='?')
        erp.check_sql(sql)
        tables = re.findall(r'\b(?:FROM|JOIN)\s+([A-Za-z_]+)\s+\w+\s*(WITH\s*\(NOLOCK\))?', sql)
        assert tables, name
        assert all(hint for _, hint in tables), f'{name}: таблица без WITH (NOLOCK)'
    # весь SQL в erp.py — только в константах SQL_*
    selects = [n.value for n in ast.walk(_module_trees()['erp.py'])
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and re.match(r'\s*(SELECT|WITH)\s', n.value)]
    assert sorted(selects) == sorted(sql_consts.values())


# ============================== API без БД ==============================

def _orders_every(cid, agent, step, revenue, kg):
    start = TODAY - timedelta(days=365)
    return tuple(dm.Order(cid, start + timedelta(days=i), agent, revenue, kg)
                 for i in range(0, 365, step))


def make_snapshot(snapshot_id='syn-1'):
    rows = [
        _row(1, 11, 1, 1, 1, 101, 1, 1001), _row(1, 11, 1, 1, 1, 102, 2, 1002),
        _row(1, 12, 1, 2, 1, 103, 1, 0), _row(1, 13, 1, 7, 1, 104, 1, 1004),
        _row(2, 21, 1, 1, 1, 104, 1, 1004), _row(2, 21, 1, 1, 1, 101, 2, 1001),
    ]
    orders = {101: _orders_every(101, 1, 7, 60000.0, 80.0),
              102: _orders_every(102, 1, 14, 30000.0, 40.0),
              104: _orders_every(104, 2, 7, 150000.0, 400.0)}
    customers = {c: erp.Customer(c, f'C{c}', f'Клиент {c}', '036', 'Այլ', False, '101')
                 for c in (101, 102, 103, 104)}
    season = {m: 1.0 for m in range(1, 13)}
    season.update({1: 0.5, 2: 0.7, 3: 0.8, 6: 1.3, 7: 1.5, 8: 1.4})
    return Snapshot(
        id=snapshot_id, data_as_of=datetime(2026, 9, 30, 12, 0), today=TODAY,
        window_start=TODAY - timedelta(days=365),
        agents={1: erp.Agent(1, 'A001', 'Менеджер 1', False),
                2: erp.Agent(2, 'A002', 'Менеджер 2', False)},
        plan=pl.build_plan(rows), customers=customers,
        customer_groups={'036': 'Այլ', '017': 'Երևան Սիթի'},
        erp_points={1001: (101, (40.18, 44.50)), 1002: (102, (40.19, 44.52)),
                    1004: (104, (40.20, 44.55))},
        default_address={101: 1001, 102: 1002, 104: 1004},
        gps_points={103: (40.17, 44.49)},
        orders_by_customer=orders,
        company_orders_by_day=dict(Counter(o.date for os in orders.values() for o in os)),
        first_order={c: TODAY - timedelta(days=600) for c in orders},
        season_index=season,
        auto_homes={1: geo.HomeGuess(40.18, 44.51, 'night', 20)},
        facts={1: ev.Fact(40, 30.0, 640.0, 1000.0, 6.0, 0.45)},
        active_agents=frozenset({1, 2}),
        recent_visits=(), fixes_by_agent={},
        cars={'CAR1': erp.Car('CAR1', 'HOWO', False), 'CAR2': erp.Car('CAR2', 'FORD', True)},
        car_days={'CAR1': (40, 8100.0, 16800.0)},
        car_last_used={'CAR1': TODAY - timedelta(days=1)},   # CAR2 закрыта в ERP и в накладных не была
    )


@pytest.fixture
def client(tmp_path):
    from flask import Flask
    import route_optimizer

    class FakeDb:
        connection_string = 'DRIVER={none};'

    app = Flask(__name__)
    app.secret_key = 'test'
    route_optimizer.init_app(app, FakeDb(), db_path=str(tmp_path / 'routes.db'))
    app.extensions['route_optimizer'].snapshots = SnapshotCache(lambda: make_snapshot())
    return app.test_client()


OVERVIEW_TOTALS = {'managers', 'days_total', 'days_below_min', 'avg_p_day_ge_min', 'trips_poor_week',
                   'truck_km_week', 'truck_liters_week', 'truck_amd_week', 'manager_km_week',
                   'manager_liters_week', 'manager_amd_week', 'avg_load_pct_peak', 'avg_plan_hours',
                   'avg_plan_work_hours', 'avg_fact_hours', 'avg_fact_work_hours',
                   'avg_fact_pause_hours', 'revenue_week_low', 'revenue_week_year', 'coords',
                   'trips_week', 'trips_per_day', 'truck_kg_per_day', 'truck_hours_week',
                   'truck_days_short_week', 'avg_load_pct', 'avg_trip_revenue', 'poor_trip_share'}
FACT_KEYS = {'visits_per_day', 'day_start', 'day_end', 'hours', 'work_hours', 'pause_hours',
             'productive_share', 'days', 'track_days'}
DAY_KEYS = {'week', 'weekday', 'label', 'delivery_label', 'visits', 'visits_no_coords', 'flags',
            'plan_minutes', 'drive_minutes', 'visit_minutes', 'overtime_minutes', 'work_minutes',
            'commute_minutes', 'manager_km', 'manager_km_rownum', 'manager_liters', 'revenue_low_exp',
            'revenue_low_p10', 'revenue_low_p90', 'p_day_ge_min', 'customer_ids', 'stops'}
STOP_KEYS = {'customer_id', 'erp_rownum', 'lat', 'lon', 'coord_source'}
DEFAULT_NORMS = {'detour_factor': {'value': 1.3, 'source': 'default'},
                 'speed_city_kmh': {'value': 25.0, 'source': 'default'},
                 'speed_region_kmh': {'value': 45.0, 'source': 'default'},
                 'visit_min_small': {'value': 7.0, 'source': 'default'},
                 'visit_min_medium': {'value': 10.0, 'source': 'default'},
                 'visit_min_large': {'value': 20.0, 'source': 'default'}}
CUSTOMER_KEYS = {'code', 'name', 'lat', 'lon', 'coord_source', 'size', 'group', 'area',
                 'orders_per_week_year', 'avg_order_amd', 'avg_order_kg', 'rev_week_low',
                 'rev_week_year', 'p_low'}


def test_api_overview_contract(client):
    r = client.get('/api/routes/overview')
    assert r.status_code == 200 and r.headers['Cache-Control'].startswith('no-cache')
    d = r.get_json()
    assert d['success'] and d['from_cache'] is False
    assert {'generated_at', 'data_as_of', 'warnings', 'season', 'cycle_weeks', 'weekdays', 'depot',
            'norms', 'totals', 'managers', 'customers', 'fleet'} <= set(d)
    assert OVERVIEW_TOTALS <= set(d['totals'])
    assert d['norms'] == DEFAULT_NORMS                  # треков нет — нормы дорог по умолчанию
    assert d['season'] == {'low_months': [1, 2, 3], 'peak_months': [6, 7, 8], 'source': 'auto',
                           'index': {str(m): v for m, v in sorted(make_snapshot().season_index.items())}}
    assert d['weekdays'] == [1, 2, 3, 4, 5, 6, 7] and d['depot'] is None and d['fleet'] is None
    assert {'no_depot', 'no_home', 'off_days'} <= {w['code'] for w in d['warnings']}
    assert all(set(w) == {'code', 'text', 'link'} for w in d['warnings'])
    m1 = next(m for m in d['managers'] if m['agent_id'] == 1)
    assert m1['home'] == {'lat': 40.18, 'lon': 44.51, 'source': 'gps_auto'} and 'truck' not in m1
    assert m1['fact']['day_start'] == '10:40' and m1['fact']['days'] == 40
    assert all(set(m['fact']) == FACT_KEYS for m in d['managers'])   # и у менеджера без факта
    assert (d['totals']['avg_fact_work_hours'], d['totals']['avg_fact_pause_hours']) == (None, None)
    assert all(DAY_KEYS <= set(day) for m in d['managers'] for day in m['days'])
    # весь день = у клиентов + дорога из дома и домой — и в округлённых минутах тоже
    assert all(day['work_minutes'] + day['commute_minutes'] == day['plan_minutes']
               and day['commute_minutes'] >= 0 for m in d['managers'] for day in m['days'])
    assert all(m['week']['avg_work_hours'] <= m['week']['avg_plan_hours'] for m in d['managers'])
    assert d['totals']['avg_plan_work_hours'] <= d['totals']['avg_plan_hours']
    assert all(set(stop) == STOP_KEYS and [x['customer_id'] for x in day['stops']] == day['customer_ids']
               for m in d['managers'] for day in m['days'] for stop in day['stops'])
    sunday = next(day for day in m1['days'] if day['weekday'] == 7)
    assert {'off_day', 'sunday_order'} <= set(sunday['flags']) and sunday['delivery_label'] == 'Երկ'
    assert set(d['customers']) == {'101', '102', '103', '104'}
    assert all(CUSTOMER_KEYS <= set(c) for c in d['customers'].values())
    assert d['customers']['103']['coord_source'] == 'gps'
    assert d['totals']['days_total'] == 3              # воскресенье — вне итогов дней
    assert d['totals']['coords']['visits_total'] == 6
    assert client.get('/api/routes/overview').get_json()['from_cache'] is True


def test_api_settings_get_and_post(client):
    r = client.get('/api/routes/settings')
    assert r.status_code == 200
    d = r.get_json()
    assert d['settings'] == st.DEFAULT_SETTINGS and d['depot'] is None
    car1 = next(t for t in d['trucks'] if t['car_code'] == 'CAR1')
    car2 = next(t for t in d['trucks'] if t['car_code'] == 'CAR2')
    # машина закреплена за водителем: вместо «чей менеджер» — сколько возит в день по ERP
    assert (car1['erp_days'], car1['erp_kg_day'], car1['erp_kg_day_max']) == (40, 8100, 16800)
    assert car1['active'] is True and 'agent_id' not in car1 and car2['erp_kg_day'] is None
    assert car2['erp_closed'] is True and car2['active'] is False
    m1 = next(m for m in d['managers'] if m['agent_id'] == 1)
    assert m1['home_suggestion'] == {'lat': 40.18, 'lon': 44.51, 'method': 'night', 'days': 20}
    assert m1['car_fuel_type'] == 'petrol'
    assert {'code': '036', 'name': 'Այլ', 'customers': 4} in d['customer_groups']
    assert d['season']['detected_low'] == [1, 2, 3]
    assert set(d['calibration']) == {'detour_factor', 'speed_city_kmh', 'speed_region_kmh',
                                     'visit_min_small', 'visit_min_medium', 'visit_min_large',
                                     'visit_min_avg', 'days_used', 'traffic'}
    assert d['calibration']['traffic']['live'] is False
    assert all(d['settings'][k] is None for k in ev.ROAD_NORMS)          # нормы дорог — «авто»
    assert all(d['settings'][f'visit_min_{k}'] is None for k in ev.VISIT_NORMS)   # визиты — «авто»
    assert all(m['included'] is True and m['inactive'] is False and m['included_source'] == 'auto'
               for m in d['managers'])

    body = {'settings': {'fuel_price_diesel': 490}, 'depot': {'lat': 40.15, 'lon': 44.46},
            'trucks': [{'car_code': 'CAR1', 'capacity_kg': 3000, 'fuel_l_per_100km': 18,
                        'agent_id': 1, 'active': True}]}
    r = client.post('/api/routes/settings', json=body)
    assert r.status_code == 200 and r.get_json() == {'success': True}
    ov = client.get('/api/routes/overview').get_json()
    assert ov['from_cache'] is False and ov['depot'] == {'lat': 40.15, 'lon': 44.46}
    assert ov['fleet']['trucks'] == [{'car_code': 'CAR1', 'name': 'HOWO', 'capacity_kg': 3000,
                                      'fuel_l_per_100km': 18}]
    assert ov['totals']['truck_km_week'] > 0 and ov['totals']['truck_amd_week'] > 0
    assert ov['totals']['trips_week'] > 0 and ov['fleet']['days']
    day = ov['fleet']['days'][0]
    assert day['trucks'][0]['car_code'] == 'CAR1' and day['label'] in ('Երկ', 'Երք', 'Չրք', 'Հնգ', 'Ուրբ', 'Շբթ')

    r = client.post('/api/routes/settings', json={'settings': {'detour_factor': 9}})
    assert r.status_code == 400 and 'settings.detour_factor' in r.get_json()['errors']
    def detour():
        return client.get('/api/routes/settings').get_json()['settings']['detour_factor']

    r = client.post('/api/routes/settings', json={'settings': {'detour_factor': 1.45}})
    assert r.status_code == 200 and detour() == 1.45
    assert client.get('/api/routes/overview').get_json()['norms']['detour_factor'] == \
        {'value': 1.45, 'source': 'manual'}
    r = client.post('/api/routes/settings', json={'settings': {'detour_factor': None}})   # снова «авто»
    assert r.status_code == 200 and detour() is None
    r = client.post('/api/routes/settings', data='detour=1', content_type='text/plain')
    assert r.status_code == 415
    r = client.post('/api/routes/settings', data='{bad json', content_type='application/json')
    assert r.status_code == 400


def test_api_errors_do_not_leak_details(client, tmp_path):
    app = client.application
    state = app.extensions['route_optimizer']

    def down():
        raise erp.ErpError('Login failed for user sa at 192.168.1.4')

    state.snapshots = SnapshotCache(down)
    r = client.get('/api/routes/overview')
    assert r.status_code == 503 and r.get_json() == {'success': False,
                                                       'error': 'ERP տվյալների բազան հասանելի չէ'}

    broken = tmp_path / 'broken.db'
    broken.write_bytes(b'garbage' * 200)
    state.snapshots = SnapshotCache(lambda: make_snapshot())
    state.store = st.Store(str(broken))
    d = client.get('/api/routes/overview').get_json()
    assert d['success'] is False and d['store_error'] is True


def test_api_huge_numbers_are_400_not_500(client):
    r = client.post('/api/routes/settings', json={'settings': {'detour_factor': 10 ** 400},
                                                   'depot': {'lat': 10 ** 400, 'lon': 44.5}})
    assert r.status_code == 400
    assert set(r.get_json()['errors']) == {'settings.detour_factor', 'depot'}


# ============================== кэш снимка ==============================

class _Clock:
    """Часы SnapshotCache, которые двигает тест."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_snapshot_cache_min_refresh_age_and_ttl():
    clock, built = _Clock(), []

    def loader():
        built.append(clock.t)
        return make_snapshot(f'snap-{len(built)}')

    cache = SnapshotCache(loader, clock=clock)

    def get(**kw):
        snap, stale = cache.get(**kw)
        return snap.id, stale

    assert get() == ('snap-1', False)
    clock.t += 30
    assert get(refresh=True) == ('snap-1', False)     # моложе минуты — ERP заново не читаем
    clock.t += 31
    assert get(refresh=True) == ('snap-2', False)     # старше минуты — пересобран
    clock.t += 599
    assert get() == ('snap-2', False)                 # TTL 10 минут не истёк
    clock.t += 2
    assert get() == ('snap-3', False)                 # истёк — пересборка и без refresh
    assert len(built) == 3


def test_snapshot_cache_stale_fallback_when_erp_down():
    clock, calls, down = _Clock(), [], []

    def loader():
        calls.append(clock.t)
        if down:
            raise erp.ErpError('ERP недоступна')
        return make_snapshot(f'snap-{len(calls)}')

    cache = SnapshotCache(loader, clock=clock)
    assert cache.get()[0].id == 'snap-1'
    down.append(True)
    clock.t += 700                                                # TTL истёк, ERP лежит
    snap, stale = cache.get(allow_stale=True)
    assert (snap.id, stale, len(calls)) == ('snap-1', True, 2)    # прежний снимок с пометкой
    snap, stale = cache.get(refresh=True, allow_stale=True)
    assert (snap.id, stale, len(calls)) == ('snap-1', True, 2)    # минуту ERP не дёргаем
    with pytest.raises(erp.ErpError):                             # без allow_stale — ошибка
        cache.get()
    clock.t += 61
    down.clear()
    snap, stale = cache.get(allow_stale=True)
    assert (snap.id, stale, len(calls)) == ('snap-4', False, 4)   # ERP вернулась — новый снимок


def test_snapshot_cache_without_previous_snapshot_raises():
    def loader():
        raise erp.ErpError('ERP недоступна')

    with pytest.raises(erp.ErpError):
        SnapshotCache(loader).get(allow_stale=True)


def test_snapshot_cache_backoff_without_previous_snapshot():
    clock, calls, down = _Clock(), [], [True]

    def loader():
        calls.append(clock.t)
        if down:
            raise erp.ErpError('ERP недоступна')
        return make_snapshot()

    cache = SnapshotCache(loader, clock=clock)
    with pytest.raises(erp.ErpError):
        cache.get(allow_stale=True)
    clock.t += 30
    for kw in ({}, {'allow_stale': True}, {'refresh': True, 'allow_stale': True}):
        with pytest.raises(erp.ErpError):         # минуту после сбоя — сразу, без подключения к ERP
            cache.get(**kw)
    assert len(calls) == 1
    clock.t += 31
    with pytest.raises(erp.ErpError):             # минута прошла — новая попытка
        cache.get(allow_stale=True)
    assert len(calls) == 2
    clock.t += 61
    down.clear()
    snap, stale = cache.get(allow_stale=True)
    assert (snap.id, stale, len(calls)) == ('syn-1', False, 3)


def test_api_serves_stale_snapshot_when_erp_is_down(client):
    state = client.application.extensions['route_optimizer']
    clock, down = _Clock(), []

    def loader():
        if down:
            raise erp.ErpError('Login failed for user sa at 192.168.1.4')
        return make_snapshot()

    def codes(d):
        return [w['code'] for w in d['warnings']]

    state.snapshots = SnapshotCache(loader, clock=clock)
    assert 'erp_stale' not in codes(client.get('/api/routes/overview').get_json())
    down.append(True)
    clock.t += 700                                                # TTL истёк, ERP лежит
    for _ in range(2):
        r = client.get('/api/routes/overview?refresh=1')
        d = r.get_json()
        assert r.status_code == 200 and d['success'] and d['from_cache'] is True
        assert d['warnings'][0] == {'code': 'erp_stale', 'link': None,
                                    'text': 'ERP-ն հիմա հասանելի չէ — ցույց են տրված տվյալները 30.09, 12:00 դրությամբ'}
        assert codes(d).count('erp_stale') == 1                   # кэш оценки не накапливает
    assert client.get('/api/routes/settings').status_code == 200
    down.clear()
    clock.t += 61
    d = client.get('/api/routes/overview').get_json()
    assert 'erp_stale' not in codes(d) and d['from_cache'] is True
    down.append(True)
    clock.t += 700
    r = client.post('/api/routes/settings', json={'settings': {'min_day_revenue': 90000}})
    assert r.status_code == 200 and r.get_json() == {'success': True}


# ============================== порядок, частоты, шаблоны, нормы ==============================

def _bundle(profiles=(), **settings):
    return st.Bundle(dict(st.DEFAULT_SETTINGS, **settings), None, {},
                     {p.agent_id: p for p in profiles})


def _days(ov):
    return {(m['agent_id'], d['weekday']): d for m in ov['managers'] for d in m['days']}


def test_day_stops_use_visit_coordinate_and_erp_order():
    base = make_snapshot()
    rows = [
        # ERP-порядок агента 1 в понедельник: 102, 105 (без координат), 101; fROWNUM с 0 и с пропуском
        _row(1, 11, 1, 1, 1, 102, 0, 1002), _row(1, 11, 1, 1, 1, 105, 1, 0),
        _row(1, 11, 1, 1, 1, 101, 3, 1001),
        # агент 2 ездит к клиенту 101 на ДРУГОЙ адрес шаблона
        _row(2, 21, 1, 1, 1, 104, 0, 1004), _row(2, 21, 1, 1, 1, 101, 1, 1005),
    ]
    snap = replace(base, plan=pl.build_plan(rows),
                   erp_points={**base.erp_points, 1005: (101, (40.30, 44.70))})
    ov = ev.build_overview(snap, _bundle())
    mon1, mon2 = _days(ov)[(1, 1)], _days(ov)[(2, 1)]
    # № объезда — самый короткий порядок от дома (40.18, 44.51): сначала 101, без координат — в конце
    assert [s['customer_id'] for s in mon1['stops']] == mon1['customer_ids'] == [101, 102, 105]
    assert [s['erp_rownum'] for s in mon1['stops']] == [3, 1, 2]     # № в ERP — позиция по fROWNUM
    erp_order = [v.customer_id for v in snap.plan.days_of(1)[0].visits]
    assert [s['customer_id'] for s in sorted(mon1['stops'], key=lambda s: s['erp_rownum'])] == erp_order
    assert mon1['stops'][-1] == {'customer_id': 105, 'erp_rownum': 2, 'lat': None, 'lon': None,
                                 'coord_source': 'none'}
    assert mon1['stops'][0] == {'customer_id': 101, 'erp_rownum': 3, 'lat': 40.18, 'lon': 44.50,
                                'coord_source': 'erp'}
    # у агента 2 точка клиента 101 — его адрес из шаблона, а не «первый визит клиента»
    s101 = next(s for s in mon2['stops'] if s['customer_id'] == 101)
    assert (s101['lat'], s101['lon'], s101['coord_source']) == (40.30, 44.70, 'erp')
    assert (ov['customers']['101']['lat'], ov['customers']['101']['lon']) == (40.18, 44.50)
    assert mon1['manager_km'] <= mon1['manager_km_rownum']


def test_frequency_counts_only_included_managers():
    base = make_snapshot()
    rows = [_row(1, 11, 1, 1, 1, 101, 0, 1001), _row(1, 11, 1, 1, 1, 102, 1, 1002),
            _row(2, 21, 1, 1, 1, 101, 0, 1001), _row(2, 21, 1, 1, 1, 102, 1, 1002),  # дубль шаблона
            _row(2, 22, 1, 3, 1, 104, 0, 1004)]                                     # только у дубля
    both = ev.build_overview(replace(base, plan=pl.build_plan(rows)), _bundle())
    twin_off = ev.build_overview(replace(base, plan=pl.build_plan(rows)),
                                 _bundle([st.ManagerProfile(2, included=False)]))
    alone = ev.build_overview(replace(base, plan=pl.build_plan(rows[:2])), _bundle())

    def p_low(ov):
        return {c: ov['customers'][str(c)]['p_low'] for c in (101, 102)}

    assert both['customers']['101']['visits_per_week'] == 2.0      # оба в расчёте: p делится на 2
    assert p_low(both)[101] < p_low(alone)[101] and p_low(both)[102] < p_low(alone)[102]
    # дубль исключён — у двойника вероятности и выручка как без дубля
    assert twin_off['customers']['101']['visits_per_week'] == 1.0
    assert p_low(twin_off) == p_low(alone)
    assert _days(twin_off)[(1, 1)]['revenue_low_exp'] == _days(alone)[(1, 1)]['revenue_low_exp']
    assert twin_off['totals']['revenue_week_low'] == alone['totals']['revenue_week_low']
    # к клиенту 104 ездит только исключённый — f по всем менеджерам, цифры осмысленные
    assert twin_off['customers']['104']['visits_per_week'] == 1.0
    assert twin_off['customers']['104']['p_low'] == 1.0


def test_inactive_templates_excluded_by_default():
    snap = replace(make_snapshot(), active_agents=frozenset({1}))   # у агента 2 нет работы 8 недель

    def manager(ov, agent_id):
        return next(m for m in ov['managers'] if m['agent_id'] == agent_id)

    def codes(ov):
        return [w['code'] for w in ov['warnings']]

    ov = ev.build_overview(snap, _bundle())
    assert manager(ov, 1)['included'] is True and 'inactive' not in manager(ov, 1)['flags']
    assert manager(ov, 2)['included'] is False and 'inactive' in manager(ov, 2)['flags']
    assert ov['totals']['managers'] == 1
    w = next(w for w in ov['warnings'] if w['code'] == 'inactive_templates')
    assert w == {'code': 'inactive_templates', 'link': '/routes/settings#managers',
                 'text': '8 շաբաթ առանց աշխատանքի (պատվերներ և այցեր չկան)՝ A002 — հաշվարկում չեն։ '
                         'Կարելի է միացնել կարգավորումներում։'}
    # владелец включил явно — в расчёте; отметка «без работы» остаётся, предупреждения нет
    on = ev.build_overview(snap, _bundle([st.ManagerProfile(2, included=True)]))
    assert manager(on, 2)['included'] is True and 'inactive' in manager(on, 2)['flags']
    assert 'inactive_templates' not in codes(on) and on['totals']['managers'] == 2
    # владелец выключил явно — это его выбор, а не умолчание: предупреждения нет
    off = ev.build_overview(snap, _bundle([st.ManagerProfile(2, included=False)]))
    assert 'inactive_templates' not in codes(off) and off['totals']['managers'] == 1


def test_api_settings_show_effective_inclusion(client):
    state = client.application.extensions['route_optimizer']
    state.snapshots = SnapshotCache(lambda: replace(make_snapshot(), active_agents=frozenset({1})))
    managers = {m['agent_id']: m for m in client.get('/api/routes/settings').get_json()['managers']}
    assert (managers[1]['included'], managers[1]['inactive']) == (True, False)
    assert (managers[2]['included'], managers[2]['inactive']) == (False, True)
    assert client.post('/api/routes/settings',
                       json={'managers': [{'agent_id': 2, 'included': True}]}).status_code == 200
    managers = {m['agent_id']: m for m in client.get('/api/routes/settings').get_json()['managers']}
    assert (managers[2]['included'], managers[2]['inactive']) == (True, True)


def test_season_empty_warning_names_months():
    snap = replace(make_snapshot(), season_index={m: 1.0 + 0.01 * m for m in range(1, 13)})
    ov = ev.build_overview(snap, _bundle())
    assert (ov['season']['low_months'], ov['season']['peak_months']) == ([1, 2, 3], [10, 11, 12])
    w = next(w for w in ov['warnings'] if w['code'] == 'season_empty')
    assert 'հնվ, փտվ, մրտ' in w['text'] and 'հոկ, նոյ, դեկ' in w['text']
    assert 'վերցված են ամենացածր և ամենաբարձր վաճառքով 3-ական ամիսները՝ ' in w['text']
    assert w['link'] == '/routes/settings#season'
    peak_only = ev.build_overview(snap, _bundle(low_months=[1, 2, 3]))
    w = next(w for w in peak_only['warnings'] if w['code'] == 'season_empty')
    assert 'վերցված են ամենաբարձր վաճառքով 3 ամիսները՝ բարձր սեզոն՝ հոկ, նոյ, դեկ' in w['text'] and 'ցածր սեզոն' not in w['text']
    plain = ev.build_overview(make_snapshot(), _bundle())
    assert 'season_empty' not in [w['code'] for w in plain['warnings']]


def test_overview_uses_effective_road_norms():
    snap = make_snapshot()
    calib = ev.Calibration(1.56, None, None, 4)
    auto = ev.build_overview(snap, _bundle())
    gps = ev.build_overview(snap, _bundle(), calib)
    pinned = ev.build_overview(snap, _bundle(detour_factor=1.3), calib)
    assert auto['norms'] == DEFAULT_NORMS
    assert gps['norms']['detour_factor'] == {'value': 1.56, 'source': 'gps'}
    assert pinned['norms']['detour_factor'] == {'value': 1.3, 'source': 'manual'}

    def km(ov):
        return [d['manager_km'] for m in ov['managers'] for d in m['days']]

    assert km(pinned) == km(auto)                                   # закреплённое число важнее GPS
    assert any(k > 0 for k in km(auto))
    for a, b in zip(km(auto), km(gps)):                             # км дня пропорциональны извилистости
        assert b == pytest.approx(a * 1.56 / 1.3, abs=0.11)


def _visit_values(ov):
    return [ov['norms'][f'visit_min_{k}']['value'] for k in ('small', 'medium', 'large')]


def test_overview_visit_minutes_from_gps_calibration():
    snap = make_snapshot()
    calib = ev.Calibration(None, None, None, 0, visit_min_avg=5.2)
    ov = ev.build_overview(snap, _bundle(), calib)
    # визиты плана в рабочие дни у менеджеров в расчёте: мелких 2 (102, 103), средних 2 (101 × 2),
    # крупный 1 (104; воскресный визит агента 1 не считается): m = 5.2 / 1.08 = 4.81 →
    # мелкий 3.37 → 3.5, средний 4.81 → 5, крупный 9.63 → 9.5
    assert {k: v for k, v in ov['norms'].items() if k.startswith('visit_min_')} == {
        'visit_min_small': {'value': 3.5, 'source': 'gps'},
        'visit_min_medium': {'value': 5.0, 'source': 'gps'},
        'visit_min_large': {'value': 9.5, 'source': 'gps'}}
    assert _days(ov)[(1, 1)]['visit_minutes'] == round(5.0 + 3.5)     # 101 средний + 102 мелкий
    pinned = ev.build_overview(snap, _bundle(visit_min_small=8), calib)   # число — только свой класс
    assert pinned['norms']['visit_min_small'] == {'value': 8.0, 'source': 'manual'}
    assert _visit_values(pinned)[1:] == [5.0, 9.5]
    # доли — только у менеджеров в расчёте: без агента 2 крупных визитов нет, m = 5.2 / 0.8 = 6.5
    alone = ev.build_overview(snap, _bundle([st.ManagerProfile(2, included=False)]), calib)
    assert _visit_values(alone) == [4.5, 6.5, 13.0]


def test_overview_fact_work_and_pauses_weighted_by_track_days():
    base = make_snapshot()

    def fact_totals(facts, bundle=None):
        ov = ev.build_overview(replace(base, facts=facts), bundle or _bundle())
        t = ov['totals']
        return ov, (t['avg_fact_hours'], t['avg_fact_work_hours'], t['avg_fact_pause_hours'])

    # окно — по дням факта: (6·40 + 7·10) / 50 = 6.2; работа и паузы — по дням трека:
    # (4·30 + 6·10) / 40 = 4.5 и (2·30 + 0.5·10) / 40 = 1.625 → 1.6 (по дням факта было бы 4.4 и 1.7)
    tracked = {1: ev.Fact(40, 30.0, 640.0, 1000.0, 6.0, 0.45, 4.0, 2.0, 30),
               2: ev.Fact(10, 20.0, 600.0, 1020.0, 7.0, 0.50, 6.0, 0.5, 10)}
    ov, totals = fact_totals(tracked)
    assert totals == (6.2, 4.5, 1.6)
    m1 = next(m for m in ov['managers'] if m['agent_id'] == 1)
    assert m1['fact'] == {'visits_per_day': 30.0, 'day_start': '10:40', 'day_end': '16:40',
                          'hours': 6.0, 'work_hours': 4.0, 'pause_hours': 2.0,
                          'productive_share': 0.45, 'days': 40, 'track_days': 30}
    # у агента 2 трека нет: работа и паузы — только по агенту 1, окно — по обоим
    untracked = {**tracked, 2: ev.Fact(10, 20.0, 600.0, 1020.0, 7.0, 0.50)}
    ov, totals = fact_totals(untracked)
    assert totals == (6.2, 4.0, 2.0)
    m2 = next(m for m in ov['managers'] if m['agent_id'] == 2)
    assert (m2['fact']['work_hours'], m2['fact']['pause_hours'], m2['fact']['track_days']) == \
        (None, None, 0)
    # агент 1 не в расчёте — в итогах только агент 2 без трека; фактов нет вовсе — null
    assert fact_totals(untracked, _bundle([st.ManagerProfile(1, included=False)]))[1] == \
        (7.0, None, None)
    assert fact_totals({})[1] == (None, None, None)


def _use_snapshot(client, snapshot_id='syn-1', **changes):
    state = client.application.extensions['route_optimizer']
    snap = replace(make_snapshot(snapshot_id), **changes)
    state.snapshots = SnapshotCache(lambda: snap)


def test_api_included_auto_follows_activity_after_save(client):
    def managers():
        return {m['agent_id']: m for m in client.get('/api/routes/settings').get_json()['managers']}

    def warnings():
        return [w['code'] for w in client.get('/api/routes/overview').get_json()['warnings']]

    _use_snapshot(client, active_agents=frozenset({1}))             # у агента 2 нет работы 8 недель
    assert (managers()[2]['included'], managers()[2]['included_source']) == (False, 'auto')
    # «Сохранить» без касания галочек: форма шлёт дом и топливо, но не included (G1);
    # новая запись с одним домом не включает агента в расчёт (G2)
    body = {'managers': [{'agent_id': 1, 'home_lat': 40.1, 'home_lon': 44.5,
                          'car_fuel_l_per_100km': None, 'car_fuel_type': 'petrol'},
                         {'agent_id': 2, 'home_lat': 40.2, 'home_lon': 44.6}]}
    assert client.post('/api/routes/settings', json=body).status_code == 200
    m = managers()
    assert (m[1]['included'], m[1]['included_source']) == (True, 'auto')
    assert (m[2]['included'], m[2]['included_source']) == (False, 'auto')
    assert 'inactive_templates' in warnings()
    # у агента появилась работа — он в расчёте сам, без правки настроек
    _use_snapshot(client, 'syn-2', active_agents=frozenset({1, 2}))
    assert (managers()[2]['included'], managers()[2]['included_source']) == (True, 'auto')
    ov = client.get('/api/routes/overview').get_json()
    assert next(x for x in ov['managers'] if x['agent_id'] == 2)['included'] is True
    # явный выбор держится при любой активности; null — вернуть «авто»
    assert client.post('/api/routes/settings',
                       json={'managers': [{'agent_id': 2, 'included': False}]}).status_code == 200
    assert (managers()[2]['included'], managers()[2]['included_source']) == (False, 'manual')
    assert 'inactive_templates' not in warnings()                  # выключен владельцем — не «без работы»
    assert client.post('/api/routes/settings',
                       json={'managers': [{'agent_id': 2, 'included': None}]}).status_code == 200
    assert (managers()[2]['included'], managers()[2]['included_source']) == (True, 'auto')


def test_api_settings_season_is_effective_with_fallback(client):
    _use_snapshot(client, season_index={m: 1.0 + 0.01 * m for m in range(1, 13)})   # пороги не прошёл никто
    se = client.get('/api/routes/settings').get_json()['season']
    assert (se['detected_low'], se['detected_peak']) == ([], [])
    assert (se['low_months'], se['peak_months']) == ([1, 2, 3], [10, 11, 12])
    assert (se['fallback_low'], se['fallback_peak']) == (True, True)
    _use_snapshot(client, 'syn-2')
    se = client.get('/api/routes/settings').get_json()['season']
    assert (se['low_months'], se['peak_months'], se['fallback_low'], se['fallback_peak']) == \
        ([1, 2, 3], [6, 7, 8], False, False)


def test_api_settings_visit_suggestions(client, monkeypatch):
    monkeypatch.setattr(ev, 'calibrate', lambda *args: ev.Calibration(1.4, 31.0, 38.0, 20, 5.2))
    d = client.get('/api/routes/settings').get_json()
    c = d['calibration']
    assert (c['visit_min_avg'], c['visit_min_small'], c['visit_min_medium'], c['visit_min_large']) == \
        (5.2, 3.5, 5.0, 9.5)
    assert (c['speed_city_kmh'], c['days_used']) == (31.0, 20)
    # подсказка — всегда «авто», даже если в поле закреплено своё число
    assert client.post('/api/routes/settings',
                       json={'settings': {'visit_min_small': 8}}).status_code == 200
    d = client.get('/api/routes/settings').get_json()
    assert d['settings']['visit_min_small'] == 8 and d['calibration']['visit_min_small'] == 3.5
    ov = client.get('/api/routes/overview').get_json()
    assert ov['norms']['visit_min_small'] == {'value': 8.0, 'source': 'manual'}
    assert ov['norms']['visit_min_large'] == {'value': 9.5, 'source': 'gps'}
    assert ov['norms']['speed_city_kmh'] == {'value': 31.0, 'source': 'gps'}


def test_api_deeply_nested_json_is_400(client):
    body = '[' * 100000 + ']' * 100000                   # парсер падает с RecursionError, не ValueError
    r = client.post('/api/routes/settings', data=body, content_type='application/json')
    assert r.status_code == 400
    assert r.get_json() == {'success': False, 'errors': {'_': 'Սերվերը չընդունեց հարցումը'}}


# ============================== этап 3: частоты ==============================

def _wk(*days):
    return pt.weekly(days)


def test_abc_classes_thresholds():
    assert fq.abc_classes({1: 50.0, 2: 30.0, 3: 10.0, 4: 10.0, 5: 0.0}, 0.5, 0.3) == \
        {1: 'A', 2: 'B', 3: 'C', 4: 'C', 5: 'C'}
    # клиент, на котором накопленная доля переходит порог, остаётся в старшем классе
    assert fq.abc_classes({1: 40.0, 2: 30.0, 3: 20.0, 4: 10.0}, 0.5, 0.3) == \
        {1: 'A', 2: 'A', 3: 'B', 4: 'C'}
    assert fq.abc_classes({7: 5.0, 3: 5.0}, 0.5, 0.3) == {3: 'A', 7: 'B'}      # ничья — по id
    assert fq.abc_classes({1: 0.0, 2: 0.0}, 0.5, 0.3) == {1: 'C', 2: 'C'}      # выручки нет


def test_sales_frequency_rule():
    assert fq.sales_frequency(0.25, 1.0) == 1.0               # №30: раз в 4 недели — всё равно каждую неделю
    assert fq.sales_frequency(0.6, 1.0) == 1.0
    assert fq.sales_frequency(1.6, 1.0) == 2.0
    assert fq.sales_frequency(2.5, 1.0) == 3.0
    assert fq.sales_frequency(1.2, 2.0) == 3.0                # запас: 2.4 → 3
    assert fq.sales_frequency(0.0, 1.0) == 1.0                # нет заказов — минимум
    assert fq.sales_frequency(3.5, 1.0) is None               # спрос больше 3 визитов — не снижаем


def test_target_frequency_only_decreases():
    assert fq.target_frequency(2.0, 1.0, 'sales') == (1.0, 'sales')
    assert fq.target_frequency(1.0, 2.0, 'sales') == (1.0, 'current')      # рост — только подсказка
    assert fq.target_frequency(4.0, None, 'sales') == (4.0, 'current')
    assert fq.target_frequency(2.0, 1.0, 'current') == (2.0, 'current')
    assert fq.target_frequency(2.0, 1.0, 'sales', accepted=0.5) == (0.5, 'manual')   # ручная важнее
    assert fq.target_frequency(2.0, 1.0, 'sales', rejected=[1.0]) == (2.0, 'current')


def test_target_frequency_raises_to_weekly():
    """№30: каждый магазин — каждую неделю; «раз в 2 недели» поднимается до 1 только по продажам."""
    assert fq.target_frequency(0.5, 1.0, 'sales') == (1.0, 'rule')
    assert fq.target_frequency(0.5, None, 'sales') == (1.0, 'rule')
    assert fq.target_frequency(0.5, 1.0, 'current') == (0.5, 'current')
    assert fq.target_frequency(0.5, 1.0, 'sales', rejected=[1.0]) == (0.5, 'current')  # владелец отклонил
    assert fq.target_frequency(0.5, 1.0, 'sales', accepted=0.5) == (0.5, 'manual')


def test_optimizer_raises_biweekly_store_to_weekly():
    """№30: 102 заказывает раз в 2 недели и посещается раз в 2 недели — предложение «каждую неделю»
    с причиной-правилом; «как сейчас» частоту не трогает."""
    rows = [_row(1, 11, 1, 1, 1, 101, 1, 1001), _row(1, 11, 1, 1, 2, 102, 2, 1002),
            _row(1, 12, 1, 2, 1, 103, 1, 0), _row(1, 13, 1, 7, 1, 104, 1, 1004),
            _row(2, 21, 1, 1, 1, 104, 1, 1004), _row(2, 21, 1, 1, 1, 101, 2, 1001)]
    snap = replace(make_snapshot(), plan=pl.build_plan(rows))
    c102 = _change(_manager(opt.run_optimization(snap, _bundle(), None, [], {}).result, 1), 102)
    assert (c102['from']['freq'], c102['to']['freq'], c102['reason']) == (0.5, 1, fq.RULE_TEXT)
    as_is = opt.run_optimization(snap, _bundle(), None, [], {'frequencies': 'current'}).result
    c = _change(_manager(as_is, 1), 102)
    assert c is None or c['to']['freq'] == 0.5


def test_weekly_rule_counts_store_total_and_adds_no_revenue():
    """№30 — про магазин, а не про пару менеджер–магазин: 101 у двух менеджеров раз в 2 недели (в сумме
    раз в неделю) не поднимается. 102 заказывает чаще визитов (≈ 0,9/нед при визите раз в 2 недели):
    подъём до раза в неделю выручку и кг модели не меняет — эти заказы уже приходят; подсказки
    «можно посещать каждую неделю» нет — это уже предложение."""
    rows = [_row(1, 11, 1, 1, 2, 101, 1, 1001), _row(2, 21, 2, 1, 2, 101, 1, 1001),
            _row(1, 11, 1, 1, 2, 102, 2, 1002), _row(1, 12, 1, 2, 1, 103, 1, 0),
            _row(1, 13, 1, 7, 1, 104, 1, 1004), _row(2, 21, 1, 1, 1, 104, 1, 1004)]
    base = make_snapshot()
    orders = {**base.orders_by_customer, 102: _orders_every(102, 1, 8, 30000.0, 40.0)}
    snap = replace(base, plan=pl.build_plan(rows), orders_by_customer=orders,
                   company_orders_by_day=dict(Counter(o.date for os in orders.values() for o in os)))
    res = opt.run_optimization(snap, _bundle(), None, [], {}).result
    m1, m2 = _manager(res, 1), _manager(res, 2)
    assert res['customers']['102']['lam_year'] > 0.8
    assert all(_change(m, 101) is None or _change(m, 101)['to']['freq'] == 0.5 for m in (m1, m2))
    c102 = _change(m1, 102)
    assert (c102['from']['freq'], c102['to']['freq'], c102['reason']) == (0.5, 1, fq.RULE_TEXT)
    for key in ('revenue_week_low', 'revenue_week_peak', 'revenue_week_year'):
        assert abs(res['after'][key] - res['before'][key]) <= 1, key
    assert not [h for h in m1['hints'] if h['customer_id'] == 102 and h['kind'] == 'freq_up']
    assert fq.frequency_hints(0.9, 0.5, 1.0, freq_after=1.0) == []
    assert fq.frequency_hints(0.9, 0.5, 1.0, rejected=[1.0]) == []
    assert fq.frequency_hints(0.9, 0.5, 1.0)[0][0] == 'freq_up'


def test_frequency_hints_and_texts():
    assert fq.frequency_hints(1.6, 1.0, 1.0) == [
        ('freq_up', 'պատվիրում է շաբաթը 1,6 անգամ, իսկ այցելում ենք շաբաթը 1 անգամ — կարելի է այցելել շաբաթը 2 անգամ')]
    assert fq.frequency_hints(0.8, 0.5, 1.0) == [
        ('freq_up', 'պատվիրում է շաբաթը 0,8 անգամ, իսկ այցելում ենք 2 շաբաթը մեկ — կարելի է այցելել ամեն շաբաթ')]
    assert fq.frequency_hints(0.0, 1.0, 1.0) == [('no_orders', 'վերջին տարում ոչ մի պատվեր')]
    assert fq.frequency_hints(0.33, 1.0, 1.0) == []
    assert fq.frequency_hints(4.2, 3.0, 1.0) == []            # чаще 3 раз в неделю не предлагаем
    assert fq.order_rate_text(0.33) == 'պատվիրում է 3 շաբաթը մեկ (0,33 պատվեր շաբաթում)'
    assert fq.order_rate_text(0.2) == 'պատվիրում է 5 շաբաթը մեկ (0,2 պատվեր շաբաթում)'
    assert fq.order_rate_text(2.0) == 'պատվիրում է շաբաթը 2 անգամ'
    assert fq.order_rate_text(0.0) == 'վերջին տարում ոչ մի պատվեր'


# ============================== этап 3: шаблоны и решения ==============================

def test_standard_patterns_follow_workdays():
    six = range(1, 7)
    assert len(pt.standard_patterns(0.5, six)) == 12 and len(pt.standard_patterns(1, six)) == 6
    assert pt.standard_patterns(2, six) == sorted(_wk(*days) for days in pt.TWICE_DAYS)
    assert pt.standard_patterns(3, six) == [_wk(1, 3, 5), _wk(2, 4, 6)]
    five = range(1, 6)                                      # без субботы
    assert pt.standard_patterns(2, five) == sorted([_wk(1, 4), _wk(2, 5), _wk(1, 3), _wk(2, 4)])
    assert pt.standard_patterns(3, five) == [_wk(1, 3, 5)]
    assert all(day in five for f in pt.FREQUENCIES for p in pt.standard_patterns(f, five) for _, day in p)
    assert pt.standard_patterns(0.5, [2]) == [((1, 2),), ((2, 2),)]
    assert pt.standard_patterns(1.5, six) == []


def test_allowed_patterns_current_nonstandard_and_forbidden():
    six = range(1, 7)
    mon_tue = _wk(1, 2)                                     # 2 раза в неделю, нестандартный
    assert mon_tue in pt.allowed_patterns(mon_tue, 2.0, six)      # частота та же — допустим
    assert mon_tue not in pt.allowed_patterns(_wk(1, 4), 2.0, six)
    sunday = _wk(7)
    # Р3-9: воскресенье (нерабочий день) не допускается и при той же частоте — раньше текущий шаблон
    # с ним оставался допустимым; вместо него — суббота той же недели, она и так среди стандартных
    assert pt.allowed_patterns(sunday, 1.0, six) == pt.standard_patterns(1.0, six)
    # частота ниже — воскресенье (нерабочий день) не сохраняется: только рабочие дни
    assert pt.allowed_patterns(sunday, 0.5, six) == pt.standard_patterns(0.5, six)
    assert pt.allowed_patterns(_wk(1, 4), 1.0, six) == pt.standard_patterns(1.0, six)
    assert _wk(3) not in pt.allowed_patterns(_wk(2), 1.0, six, forbidden=[_wk(3)])
    assert pt.change_type(_wk(2), _wk(4)) == 'move'
    assert pt.change_type(_wk(2), ((1, 2),)) == 'frequency'
    assert pt.change_type(_wk(2), ((1, 4),)) == 'both'
    assert pt.change_type(_wk(2), _wk(2)) is None
    assert pt.pattern_text(_wk(2)) == 'երք, ամեն շաբաթ'
    assert pt.pattern_text(((1, 4),)) == 'հնգ, 2 շաբաթից 1-ինը'
    assert pt.pattern_text(((2, 4),)) == 'հնգ, 2 շաբաթից 2-րդը'
    assert pt.pattern_text(_wk(1, 4)) == 'երկ և հնգ, ամեն շաբաթ'
    assert pt.pattern_text(_wk(1, 3, 5)) == 'երկ, չրք և ուրբ, ամեն շաբաթ'
    assert pt.pattern_text(((1, 1), (2, 6))) == 'երկ — 1-ին շաբաթ, շբթ — 2-րդ շաբաթ'
    assert pt.pattern_text(()) == 'առանց այցերի'


def test_decision_values_are_canonical():
    assert pt.pattern_key(pt.parse_pattern([[2, 2], [1, 2]])) == '[[1,2],[2,2]]'
    assert pt.parse_pattern([[1, 2], [1, 2]]) is None and pt.parse_pattern([[3, 1]]) is None
    assert pt.parse_pattern([[1, True]]) is None and pt.parse_pattern([]) is None
    assert pt.freq_key(pt.parse_freq(1)) == '1' and pt.parse_freq(0.75) is None
    assert pt.parse_freq(True) is None
    assert pt.parse_pattern_key('[[1,2],[2,2]]') == ((1, 2), (2, 2))
    assert pt.parse_freq_key('0.5') == 0.5 and pt.parse_freq_key('x') is None


def test_pair_spec_locks_and_forbids():
    six = list(range(1, 7))
    book = opt.DecisionBook.from_rows([
        st.Decision(10, 1, 'pattern', pt.pattern_key(_wk(4)), 'accepted'),
        st.Decision(11, 1, 'pattern', pt.pattern_key(((1, 2),)), 'rejected'),
        st.Decision(12, 1, 'freq', '2', 'accepted'),
        st.Decision(13, 1, 'freq', '0.5', 'rejected'),
    ])
    locked = opt.pair_spec((1, 10), _wk(2), 0.5, 'sales', six, book)
    assert locked.locked and locked.allowed == (_wk(4),) and locked.target == 1.0
    forbid = opt.pair_spec((1, 11), _wk(2), 0.5, 'sales', six, book)
    assert forbid.target == 0.5 and ((1, 2),) not in forbid.allowed and ((2, 2),) in forbid.allowed
    manual = opt.pair_spec((1, 12), _wk(2), 0.5, 'sales', six, book)
    assert (manual.target, manual.source) == (2.0, 'manual')
    assert manual.allowed == tuple(pt.standard_patterns(2.0, six))
    kept = opt.pair_spec((1, 13), _wk(2), 0.5, 'sales', six, book)   # отклонённая частота
    assert kept.target == 1.0 and _wk(2) in kept.allowed
    other = opt.pair_spec((2, 10), _wk(2), 0.5, 'current', six, book)  # решение другого менеджера
    assert other.target == 1.0 and not other.locked


def test_plan_pairs_cycle_and_limits():
    p = pl.build_plan([_row(1, 1, 1, 1, 1, 10, 0, 5), _row(1, 1, 1, 4, 1, 10, 0, 6)])
    assert opt.plan_pairs(p) == {1: {10: opt.PairInfo(_wk(1, 4), 5)}}   # адрес первого визита
    with pytest.raises(opt.OptimizeError, match='1 կամ 2 շաբաթվա ցիկլ'):
        opt.plan_pairs(pl.build_plan([_row(1, 1, 1, 1, 3, 10, 0)]))


# ============================== этап 3: быстрая оценка и поиск ==============================

def _slots(p):
    return tuple(sorted(sr.slot_of_day(w, d) for w, d in p))


def _search_problem(points, current, allowed, *, home=YEREVAN, workdays=range(1, 7), locked=(),
                    minutes=10.0, fleet_nodes=False):
    """Задача менеджера на синтетике: points — точки клиентов (None — без координат). fleet_nodes —
    вершины парка: те же номера, что у менеджера (матрица парка — _fleet_estimate по тем же точкам)."""
    located = [p for p in points if p is not None]
    km, mins = opt._matrices(home, located, NORMS)
    nodes, n = [], 0
    for p in points:
        n += p is not None
        nodes.append(n if p is not None else 0)
    lines = [sr.Line(customer_id=100 + i, node=nodes[i], minutes=minutes, current=_slots(current[i]),
                     allowed=tuple(_slots(a) for a in allowed[i]), locked=i in locked,
                     u=sr.make_u(random.Random(i)), gnode=nodes[i] if fleet_nodes else 0)
             for i in range(len(points))]
    wd = set(workdays)
    workday = tuple(sr.day_of_slot(j)[1] in wd for j in range(sr.SLOTS))
    base = tuple(workday[j] and any(j in line.current for line in lines) for j in range(sr.SLOTS))
    weights = sr.Weights(manager_per_km=45.0, weak_day=20000.0, poor_trip=3000.0, overtime_per_min=500.0,
                         window_min=540.0, min_day_revenue=100000.0, min_trip_revenue=150000.0)
    return sr.Problem(agent_id=7, lines=lines, km=km, mins=mins, weights=weights,
                      workday=workday, base=base, neighbors=sr.nearest_lines(nodes, km), seed=7)


DEPOT = (40.15, 44.46)


def _fleet_estimate(points, kg, *, depot=DEPOT, capacity=3000.0, trucks=1, per_km=150.0, trip_minutes=540.0):
    """Быстрая оценка парка по точкам задачи (вершины — как у _search_problem с fleet_nodes)."""
    located = [p for p in points if p is not None]
    km, _ = opt._matrices(depot, located, NORMS)                 # вершина 0 — склад
    city = [geo.in_city(p, YEREVAN, 12.0) for p in [depot, *located]]
    kgs = [0.0] + [kg[i] for i, p in enumerate(points) if p is not None]
    return sr.FleetEstimate(km, city, kgs, capacity_kg=capacity, per_km=per_km, overtime_per_min=500.0,
                            capacity_min=540.0 * trucks, unload_stop=8.0, unload_tonne=6.0,
                            speed_city_kmh=25.0, speed_region_kmh=45.0, trip_minutes=trip_minutes)


def _with_fleet(state, fleet, static=()):
    fleet.build(state.fleet_entries(), static)
    state.attach(fleet)
    return state


def test_incremental_state_matches_recomputation_after_1000_moves():
    rng = random.Random(11)
    six = range(1, 7)
    points = [(40.05 + rng.random() * 0.3, 44.35 + rng.random() * 0.35) for _ in range(36)] + [None] * 3
    freqs = [rng.choice((0.5, 1.0, 1.0, 2.0)) for _ in points]
    allowed = [pt.standard_patterns(f, six) for f in freqs]
    current = [rng.choice(a) for a in allowed]
    params = []
    for _ in points:
        p = rng.choice((0.0, 0.3, 0.7, 1.0))              # p = 1 — «заказ наверняка», p = 0 — нет
        m = rng.choice((20000.0, 50000.0, 90000.0))
        params.append(sr.VisitParams(mu=p * m, var=p * m * m * 1.3 - (p * m) ** 2, p_low=p,
                                     p_year=rng.random(), kg=rng.random() * 900.0))
    prob = _search_problem(points, current, allowed, fleet_nodes=True)
    state = sr.State(prob, [line.current for line in prob.lines], params, change_penalty=300.0)
    # парк: тоннаж 1,5 т — рейсы режутся, 1 машина — бывают минуты сверх дня; чужие визиты (static)
    fleet = _fleet_estimate(points, [pr.kg for pr in params], capacity=1500.0)
    static = [(j, g, (True, False, True, True)) for j, g in ((0, 1), (2, 5), (8, 7))]
    _with_fleet(state, fleet, static)
    assert state.consistency_error() < 1e-9 and fleet.consistency_error(state.fleet_entries()) < 1e-9
    assert fleet.total > 0 and state.total == pytest.approx(state.own_total() + fleet.total)
    by_size: dict[int, list[int]] = {}
    for i, line in enumerate(prob.lines):
        by_size.setdefault(len(line.current), []).append(i)
    moves = 0
    while moves < 1000:
        i = rng.randrange(len(points))
        if rng.random() < 0.5:
            alts = [p for p in prob.lines[i].allowed if p != state.pattern[i]]
            if not alts:
                continue
            delta, move = state.eval_relocate(i, rng.choice(alts))
        else:
            j = rng.choice(by_size[len(state.pattern[i])])
            if state.pattern[j] == state.pattern[i]:
                continue
            delta, move = state.eval_swap(i, j)
        before = state.total
        state.apply(move)
        assert state.total - before == pytest.approx(delta, abs=1e-6)   # оценка хода = факт
        moves += 1
        if moves % 250 == 0:
            fleet.polish()
            state.refresh()
    # туры, км, минуты, μ, σ² и стоимость дней; туры парка, км и минуты рейсов — как при расчёте с нуля
    assert state.consistency_error() < 1e-6
    assert fleet.consistency_error(state.fleet_entries()) < 1e-6


def _three_clusters():
    """Три кучки клиентов по разные стороны от дома; в каждом дне по одному «чужому» клиенту."""
    def cluster(lat, lon):
        return [(lat + 0.004 * (k % 3 - 1), lon + 0.004 * (k // 3)) for k in range(6)]

    points = cluster(40.18, 44.26) + cluster(40.18, 44.74) + cluster(40.40, 44.50)
    days = [1] * 5 + [2] + [2] * 5 + [3] + [3] * 5 + [1]
    three = [1, 2, 3]
    return points, [_wk(d) for d in days], [pt.standard_patterns(1.0, three)] * len(points), three


def test_search_makes_mixed_days_compact_and_is_deterministic():
    points, current, allowed, three = _three_clusters()
    runs = []
    for _ in range(2):
        prob = _search_problem(points, current, allowed, workdays=three)
        state = sr.State(prob, [line.current for line in prob.lines], [sr.NO_VISIT] * len(points),
                         change_penalty=300.0)
        km_before, cost_before = sum(state.km), state.total
        stats = sr.search(state, seconds=30)
        assert not stats.time_capped and state.total < cost_before
        groups = [set(range(0, 6)), set(range(6, 12)), set(range(12, 18))]
        for j in range(sr.SLOTS):
            if state.members[j]:
                assert any(state.members[j] <= g for g in groups), (j, state.members[j])
        assert sum(state.km) < km_before - 100
        runs.append((list(state.pattern), state.total))
    assert runs[0] == runs[1]                                    # тот же seed — те же шаблоны


def test_search_keeps_locked_customers():
    points, current, allowed, three = _three_clusters()
    allowed = list(allowed)
    allowed[5] = [current[5]]                                    # «чужой» во вторнике закреплён
    prob = _search_problem(points, current, allowed, workdays=three, locked={5})
    state = sr.State(prob, [line.current for line in prob.lines], [sr.NO_VISIT] * len(points),
                     change_penalty=300.0)
    sr.search(state, seconds=30)
    assert state.pattern[5] == _slots(_wk(2))
    assert state.pattern[11] == _slots(_wk(2)) and state.pattern[17] == _slots(_wk(3))   # остальные — к своим


def test_search_time_cap_is_flagged_and_state_stays_valid():
    points, current, allowed, three = _three_clusters()
    prob = _search_problem(points, current, allowed, workdays=three)
    state = sr.State(prob, [line.current for line in prob.lines], [sr.NO_VISIT] * len(points),
                     change_penalty=300.0)
    ticks = iter(range(0, 10 ** 7, 100))                     # каждый взгляд на часы — «+100 с»
    stats = sr.search(state, seconds=8, clock=lambda: next(ticks))
    assert stats.time_capped and stats.perturbations == 0
    assert state.consistency_error() < 1e-9
    assert all(p in line.allowed for p, line in zip(state.pattern, prob.lines))
    ticks = iter(range(0, 10 ** 7, 100))
    res = opt.run_optimization(make_snapshot(), _bundle(), None, [], {}, clock=lambda: next(ticks)).result
    assert all(m['time_capped'] for m in res['managers'])     # пометка у менеджера в результате


def test_current_start_brings_patterns_to_target_frequency():
    six = range(1, 7)
    points = [(40.18, 44.50), (40.19, 44.51), (40.20, 44.52), (40.17, 44.49)]
    current = [_wk(2), ((1, 2),), _wk(1, 4), _wk(4)]
    allowed = [pt.allowed_patterns(_wk(2), 0.5, six),            # 1/нед → 0.5: тот же день
               [((1, 2),)],                                      # нагружает вторник 1-й недели
               pt.allowed_patterns(_wk(1, 4), 1.0, six),         # 2/нед → 1: день из пары
               [_wk(4)]]                                         # нагружает четверг
    prob = _search_problem(points, current, allowed)
    assert sr.current_start(prob) == [_slots(((2, 2),)), _slots(((1, 2),)), _slots(_wk(1)),
                                      _slots(_wk(4))]


def test_change_effect_of_single_move():
    points, current, allowed, three = _three_clusters()
    prob = _search_problem(points, current, allowed, workdays=three)
    before = sr.State(prob, [line.current for line in prob.lines], [sr.NO_VISIT] * len(points),
                      change_penalty=0.0)
    effect = sr.change_effect(before, 5, _slots(_wk(1)), sr.NO_VISIT)   # «чужой» — к своим, в пн
    assert effect['km'] < -40 and effect['minutes'] < -40
    assert effect['weak'] == 0.0 and effect['truck_km'] == 0.0           # парка нет
    assert sr.change_effect(before, 5, before.pattern[5], sr.NO_VISIT) == \
        {'km': 0.0, 'minutes': 0.0, 'weak': 0.0, 'truck_km': 0.0}


def test_normal_approximation_vs_monte_carlo():
    rng = random.Random(5)
    values = tuple((float(rng.choice((4000, 6000, 9000, 12000, 20000))), 10.0) for _ in range(8))
    draws = [ev.Draw(0.4, values)] * 30
    mc = ev.revenue_stats(draws, list(range(1, 31)), 1, 20000, 100000.0, 150000.0)
    mean = fmean(v[0] for v in values)
    square = fmean(v[0] ** 2 for v in values)
    mu, var = 30 * 0.4 * mean, 30 * (0.4 * square - (0.4 * mean) ** 2)
    assert abs(sr.p_at_least(mu, var, 100000.0) - mc.p_ge_min) < 0.05
    assert abs(sr.p_poor(mu, var, 0, 30 * math.log1p(-0.4), 150000.0) - mc.p_poor_trip) < 0.05
    assert sr.p_at_least(120000.0, 0.0, 100000.0) == 1.0 and sr.p_at_least(90000.0, 0.0, 100000.0) == 0.0


# ============================== этап 3: оценка «стало» в неделю ==============================

def test_totals_are_per_week_for_two_week_cycle():
    rows_w1 = [_row(1, 11, 1, 1, 1, 101, 0, 1001), _row(1, 11, 1, 1, 1, 104, 1, 1004),
               _row(1, 12, 1, 2, 1, 103, 0, 0)]
    rows_w2 = [_row(1, 20 + w, w, r.weekday, 2, r.customer_id, r.rownum, r.address_id)
               for w in (1, 2) for r in rows_w1]
    base = make_snapshot()
    assert pl.build_plan(rows_w2).cycle_weeks == 2
    one = ev.build_overview(replace(base, plan=pl.build_plan(rows_w1)), _bundle())['totals']
    two = ev.build_overview(replace(base, plan=pl.build_plan(rows_w2)), _bundle())['totals']
    assert (one['days_total'], one['days_below_min']) == (2, 1)    # вторник без заказов — слабый
    for key in ('days_total', 'days_below_min', 'manager_km_week', 'revenue_week_low',
                'revenue_week_year', 'avg_plan_hours', 'avg_plan_work_hours'):
        assert two[key] == one[key], key
    assert two['coords']['visits_total'] == one['coords']['visits_total'] == 3


def test_coords_missing_warning_counts_half_visits_with_decimal_comma():
    """Цикл 2 недели: визитов в неделю — дробное число; в тексте «0,5 / 1,5», не «0.5 / 1.5» (числа из текста достаёт
    todoOf в routes_overview.js)."""
    rows = [_row(1, 21, 1, 1, 2, 101, 0, 1001), _row(1, 22, 2, 1, 2, 101, 0, 1001),
            _row(1, 21, 1, 1, 2, 103, 1, 0)]                    # 103 — только в 1-ю неделю и без координат
    snap = replace(make_snapshot(), plan=pl.build_plan(rows), gps_points={})
    ov = ev.build_overview(snap, _bundle())
    coords = ov['totals']['coords']
    assert (coords['visits_total'], coords['visits_with_coords']) == (1.5, 1)
    w = next(w for w in ov['warnings'] if w['code'] == 'coords_missing')
    assert w['text'].startswith('Կոորդինատներ չկան պլանի 0,5 / 1,5 այցի համար')


# ============================== этап 3: расчёт целиком ==============================

def test_optimization_is_deterministic_and_respects_frequencies():
    snap = make_snapshot()
    a = opt.run_optimization(snap, _bundle(), None, [], {})
    b = opt.run_optimization(snap, _bundle(), None, [], {})

    def strip(r):
        return {k: v for k, v in r.items() if k not in ('generated_at', 'seconds')}

    assert strip(a.result) == strip(b.result)
    for o in a.managers:
        for spec, final in zip(o.specs, o.final):
            assert final in spec.allowed and len(final) == 2 * spec.target
    as_is = opt.run_optimization(snap, _bundle(), None, [], {'frequencies': 'current'}).result
    assert all(ch['type'] == 'move' for m in as_is['managers'] for ch in m['changes'])
    fresh = opt.run_optimization(snap, _bundle(), None, [], {'start': 'fresh'})
    assert all(final in spec.allowed for o in fresh.managers for spec, final in zip(o.specs, o.final))


def test_plan_export_frequency_only_decision():
    snap, decisions = make_snapshot(), [st.Decision(103, 1, 'freq', '0.5', 'accepted')]

    def rows(**kw):
        ex = opt.plan_export(snap, _bundle(), decisions, **kw)
        return [(r['week'], r['weekday'], r['mark']) for r in ex['rows'] if r['customer_id'] == 103]

    tue = _wk(2)                                                          # текущий шаблон 103
    assert rows() == [(1, 2, 'հաճախականություն')]                                  # тот же день, меньшая загрузка
    assert rows(proposals={(1, 103): (tue, ((2, 2),))}) == [(2, 2, 'հաճախականություն')]   # как в предложении
    assert rows(proposals={(1, 103): (tue, _wk(3))}) == [(1, 2, 'հաճախականություն')]     # другой частоты — нет
    # предложение сделано от другого «было» (план клиента с тех пор изменился) — не берётся
    assert rows(proposals={(1, 103): (_wk(5), ((2, 2),))}) == [(1, 2, 'հաճախականություն')]
    result = {'managers': [{'agent_id': 1, 'changes': [
        {'customer_id': 103, 'from': {'pattern': [[1, 2], [2, 2]]}, 'to': {'pattern': [[2, 2]]}}]}]}
    assert opt.proposals_of(result) == {(1, 103): (tue, ((2, 2),))} and opt.proposals_of(None) == {}


def test_parse_params_and_decisions():
    assert opt.parse_params({}) == ({'agent_ids': None, 'start': 'current', 'frequencies': 'sales'}, {})
    _, errors = opt.parse_params({'agent_ids': [1, 1], 'start': 'x', 'frequencies': 'y', 'z': 1})
    assert set(errors) == {'agent_ids', 'start', 'frequencies', '_'}
    assert set(opt.parse_params({'agent_ids': []})[1]) == {'agent_ids'}
    d, errors = opt.parse_decision({'customer_id': 5, 'agent_id': 1, 'kind': 'pattern',
                                    'value': [[2, 4], [1, 4]], 'action': 'accept'})
    assert not errors and d.value == '[[1,4],[2,4]]'
    _, errors = opt.parse_decision({'customer_id': True, 'agent_id': 0, 'kind': 'x', 'action': 'y'})
    assert set(errors) == {'customer_id', 'agent_id', 'kind', 'action'}


# ============================== этап 3: хранилище ==============================

def test_store_optimizer_settings_validation(store):
    _, errors = st.validate_payload({'settings': {'abc_a_share': 0.7, 'abc_b_share': 0.3}},
                                    store.load(), REF)
    assert set(errors) == {'settings.abc_b_share'}
    _, errors = st.validate_payload({'settings': {'optimizer_seconds_per_manager': 0, 'freq_safety': 5,
                                                  'penalty_weak_day': -1}}, store.load(), REF)
    assert set(errors) == {'settings.optimizer_seconds_per_manager', 'settings.freq_safety',
                           'settings.penalty_weak_day'}
    _save(store, {'settings': {'penalty_change': 0, 'abc_a_share': 0.6, 'abc_b_share': 0.25}})
    s = store.load().settings
    assert (s['penalty_change'], s['abc_a_share'], s['abc_b_share'], s['fuel_price_fallback']) == \
        (0, 0.6, 0.25, 500)


def test_store_decisions_one_accepted_per_kind(store, tmp_path):
    k1, k2, k3 = '[[1,2]]', '[[2,2]]', '[[1,4],[2,4]]'
    store.save_decision(103, 1, 'pattern', k1, 'accept', 'qa')
    store.save_decision(103, 1, 'pattern', k3, 'reject', 'qa')
    store.save_decision(103, 1, 'pattern', k2, 'accept', 'qa')    # прежнее принятое снимается
    store.save_decision(103, 1, 'freq', '0.5', 'accept', 'qa')
    store.save_decision(103, 2, 'pattern', k1, 'accept', 'qa')    # у другого менеджера — своё
    got = {(d.agent_id, d.kind, d.value): d.status for d in store.load_decisions()}
    assert got == {(1, 'pattern', k2): 'accepted', (1, 'pattern', k3): 'rejected',
                   (1, 'freq', '0.5'): 'accepted', (2, 'pattern', k1): 'accepted'}
    store.save_decision(103, 1, 'pattern', k3, 'reset', 'qa')
    assert (1, 'pattern', k3) not in {(d.agent_id, d.kind, d.value) for d in store.load_decisions()}
    # частичный уникальный индекс: второе принятое того же вида база не примет даже в обход кода
    with sqlite3.connect(str(tmp_path / 'routes.db')) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO decision(customer_id, agent_id, kind, value, status, updated_at) "
                     "VALUES(103, 1, 'pattern', '[[1,6]]', 'accepted', 'x')")
    with sqlite3.connect(str(tmp_path / 'routes.db')) as conn:
        conn.execute("INSERT INTO decision(customer_id, agent_id, kind, value, status, updated_at) "
                     "VALUES(5, 1, 'freq', '0.7', 'accepted', 'x')")
    with pytest.raises(st.StoreError, match='որոշումը'):
        store.load_decisions()


def test_store_keeps_last_five_scenarios(store):
    for n in range(7):
        store.save_scenario(f'{n:032x}', 'qa', {'n': n}, {'n': n})
    assert store.scenario_ids() == [f'{n:032x}' for n in range(6, 1, -1)]
    assert store.last_scenario().result == {'n': 6}
    assert store.get_scenario(f'{0:032x}') is None and store.get_scenario(f'{3:032x}').params == {'n': 3}


def test_store_migrates_schema_2_to_current(tmp_path):
    path = str(tmp_path / 'v2.db')
    with sqlite3.connect(path) as conn:
        for sql in st._SCHEMA[:5]:                               # таблицы версии 2
            conn.execute(sql)
        conn.execute("INSERT INTO meta VALUES('schema_version', '2')")
        conn.execute("INSERT INTO settings VALUES('min_day_revenue', '120000')")
    s = st.Store(path)
    assert s.load().settings['min_day_revenue'] == 120000 and s.load_decisions() == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        columns = [r[1] for r in conn.execute('PRAGMA table_info(decision)')]
    assert {'decision', 'scenario'} <= tables and 'decision_one_accepted' in indexes
    assert 'from_value' in columns


def test_store_migrates_schema_3_to_4_keeps_decisions(tmp_path):
    """Решения схемы 3 остаются (from_value неизвестен — NULL); дубли принятых (если были) — кроме
    последнего; дальше уникальность держит индекс. Сбой миграции — база остаётся версии 3."""
    def v3(path, broken=False):
        with sqlite3.connect(path) as conn:
            for sql in st._SCHEMA[:5]:
                conn.execute(sql)
            conn.execute(st._DECISION_TABLE_V3)
            conn.execute(st._SCENARIO_TABLE)
            conn.execute("INSERT INTO meta VALUES('schema_version', '3')")
            conn.execute("INSERT INTO settings VALUES('penalty_change', '700')")
            rows = [(103, 1, 'pattern', '[[1,2]]', 'accepted', '2026-09-01T10:00:00', 'owner'),
                    (103, 1, 'pattern', '[[2,2]]', 'accepted', '2026-09-02T10:00:00', 'owner'),
                    (103, 1, 'freq', '0.5', 'accepted', '2026-09-01T10:00:00', 'owner'),
                    (104, 1, 'pattern', '[[1,3],[2,3]]', 'rejected', '2026-09-01T10:00:00', 'owner')]
            conn.executemany('INSERT INTO decision VALUES(?, ?, ?, ?, ?, ?, ?)', rows)
            if broken:
                conn.execute('DROP TABLE scenario')
                conn.execute("CREATE TABLE decision_v4(x)")          # мешает пересборке

    path = str(tmp_path / 'v3.db')
    v3(path)
    s = st.Store(path)
    assert s.load().settings['penalty_change'] == 700
    got = {(d.customer_id, d.kind, d.value): (d.status, d.from_value, d.updated_by)
           for d in s.load_decisions()}
    assert got == {(103, 'pattern', '[[2,2]]'): ('accepted', None, 'owner'),     # последнее по времени
                   (103, 'freq', '0.5'): ('accepted', None, 'owner'),
                   (104, 'pattern', '[[1,3],[2,3]]'): ('rejected', None, 'owner')}
    with sqlite3.connect(path) as conn:   # 3 → 4 → 5 (§15: вид решения remove) одной транзакцией
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)
    broken = str(tmp_path / 'v3-broken.db')
    v3(broken, broken=True)
    with pytest.raises(st.StoreError):
        st.Store(broken).load()
    with sqlite3.connect(broken) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('3',)
        assert conn.execute('SELECT COUNT(*) FROM decision').fetchone() == (4,)


OWNER_DB = ROOT / 'route_optimizer.db'
# Сохранённые значения, которые миграции не меняют (у машин схема 8 добавила столбцы — сравниваем прежние).
_KEPT_COLUMNS = {'trucks': 'car_code, capacity_kg, fuel_l_per_100km, agent_id, active, updated_at, updated_by'}


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы маршрутов владельца')
def test_store_migrates_copy_of_owner_db(tmp_path):
    """Настоящая база владельца — только КОПИЯ во временной папке: после миграции все сохранённые
    значения те же, что читались из исходного файла (открытого только на чтение)."""
    def dump(conn):
        return {table: sorted(conn.execute(f'SELECT {_KEPT_COLUMNS.get(table, "*")} FROM {table}').fetchall())
                for table in ('settings', 'depot', 'trucks', 'manager_profile')}

    # immutable: ни блокировок, ни файлов -wal/-shm рядом с базой владельца
    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro&immutable=1', uri=True)) as conn:
        before = dump(conn)
    copy = tmp_path / 'owner.db'
    copy.write_bytes(OWNER_DB.read_bytes())
    store = st.Store(str(copy))
    bundle = store.load()
    assert store.load_decisions() == []
    with closing(sqlite3.connect(str(copy))) as conn:
        assert dump(conn) == before
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)
    assert bundle.settings['workdays'] == json.loads(dict(before['settings'])['workdays'])


# ============================== этап 3: API ==============================

RESULT_KEYS = {'cycle_weeks', 'fleet_gate', 'params', 'generated_at', 'seconds', 'snapshot_as_of', 'fuel_price_used',
               'fuel_price_source', 'truck_costs', 'before', 'after', 'managers', 'customers',
               'stale_decisions', 'time_gate', 'forecast_validation'}
MANAGER_RESULT_KEYS = {'agent_id', 'code', 'name', 'before', 'after', 'feasibility', 'time_capped',
                       'days_before', 'days_after', 'changes', 'hints'}
# км и литры грузовиков у менеджера больше нет: парк общий (ответ №29) — они в before/after компании
WEEK_RESULT_KEYS = {'visits', 'revenue_low', 'revenue_peak', 'revenue_year', 'manager_km',
                    'days_below_min', 'avg_work_hours', 'avg_plan_hours', 'cost'}
DECISION_ITEM_KEYS = {'customer_id', 'customer_code', 'customer_name', 'agent_id', 'agent_code',
                      'agent_name', 'manager_included', 'kind', 'status', 'stale', 'value', 'from',
                      'to_text', 'from_text', 'current_text', 'updated_at', 'updated_by'}
CHANGE_KEYS = {'customer_id', 'type', 'from', 'to', 'reason', 'effect', 'decision'}
EFFECT_KEYS = {'manager_km_week', 'truck_km_week', 'weak_days_week', 'minutes_week'}
OPT_CUSTOMER_KEYS = {'code', 'name', 'lat', 'lon', 'coord_source', 'size', 'abc', 'lam_year',
                     'freq_current', 'freq_target', 'status', 'silent_days'}


def _wait_job(client, job_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while True:
        d = client.get(f'/api/routes/optimize/{job_id}').get_json()
        if d['job']['status'] != 'running' or time.monotonic() > deadline:
            return d
        time.sleep(0.02)


def _start(client, body=None):
    r = client.post('/api/routes/optimize', json=body or {})
    assert r.status_code == 200 and r.get_json()['success'], r.get_json()
    return r.get_json()['job_id']


def _manager(result, agent_id):
    return next(m for m in result['managers'] if m['agent_id'] == agent_id)


def _change(manager, customer_id):
    return next((ch for ch in manager['changes'] if ch['customer_id'] == customer_id), None)


def test_api_optimize_contract_and_last(client):
    assert '/routes/optimize' in {r.rule for r in client.application.url_map.iter_rules()}
    missing = client.get('/api/routes/optimize/last')
    assert missing.status_code == 404 and missing.get_json()['success'] is False
    job_id = _start(client, {'agent_ids': None, 'start': 'current', 'frequencies': 'sales'})
    d = _wait_job(client, job_id)
    assert d['success'] and d['job']['status'] == 'done' and d['job']['error'] is None
    assert d['progress'] == {'done': 2, 'total': 2, 'agent_code': None}
    res = d['result']
    assert set(res) == RESULT_KEYS and res['cycle_weeks'] == 2
    assert res['params'] == {'agent_ids': [1, 2], 'start': 'current', 'frequencies': 'sales'}
    assert (res['fuel_price_used'], res['fuel_price_source'], res['truck_costs']) == (500, 'fallback', False)
    assert OVERVIEW_TOTALS | {'visits_week', 'revenue_week_peak'} <= set(res['before'])
    assert set(res['after']) == set(res['before']) and res['stale_decisions'] == []
    assert (res['before']['visits_week'], res['after']['visits_week']) == (6, 5)   # 103 убран (§15)
    for m in res['managers']:
        assert set(m) == MANAGER_RESULT_KEYS and m['time_capped'] is False
        assert set(m['before']) == WEEK_RESULT_KEYS == set(m['after'])
        assert set(m['feasibility']) == {'revenue_week_low', 'max_days_ge_min', 'workdays', 'reachable'}
        assert all(set(day) == set(opt.DAY_KEYS) for day in m['days_before'] + m['days_after'])
        assert {day['week'] for day in m['days_before']} == {1}
        assert {day['week'] for day in m['days_after']} == {1, 2}
        for ch in m['changes']:
            assert set(ch) == CHANGE_KEYS and set(ch['effect']) == EFFECT_KEYS
            assert ch['decision'] == ({'remove': None} if ch['type'] == 'remove'
                                      else {'pattern': None, 'freq': None})
    m1 = _manager(res, 1)
    # 103 — ни одного заказа за год (§15): предложение «убрать из маршрута» вместо частоты
    c103 = _change(m1, 103)
    assert c103['type'] == 'remove' and c103['from']['text'] == 'երք, ամեն շաբաթ'
    assert c103['to'] == {'freq': 0, 'pattern': [], 'text': 'հանել երթուղուց'}
    assert c103['reason'] == 'վերջին տարում ոչ մի պատվեր'
    assert c103['effect']['manager_km_week'] < 0 and c103['effect']['minutes_week'] < 0
    assert not [x for x in m1['hints'] if x['customer_id'] == 103]
    assert set(res['customers']) == {'101', '102', '103', '104'}
    assert all(set(c) == OPT_CUSTOMER_KEYS for c in res['customers'].values())
    assert res['customers']['103']['freq_target'] == 0 and res['customers']['104']['abc'] == 'A'
    assert res['customers']['103']['status'] == 'never' and res['customers']['101']['status'] == 'active'
    last = client.get('/api/routes/optimize/last').get_json()
    assert last['job']['id'] == job_id and last['result']['managers'] == res['managers']


def test_api_optimize_one_job_at_a_time(client, monkeypatch):
    release = threading.Event()
    real = opt.run_optimization

    def slow(*args, **kwargs):
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(opt, 'run_optimization', slow)
    job_id = _start(client)
    busy = client.post('/api/routes/optimize', json={})
    # 409 несёт id идущей задачи — страница подключается к её ходу
    assert busy.status_code == 409
    assert busy.get_json() == {'success': False, 'error': 'Հաշվարկն արդեն ընթանում է', 'job_id': job_id}
    running = client.get(f'/api/routes/optimize/{job_id}').get_json()
    assert running['job']['status'] == 'running' and running['result'] is None
    release.set()
    assert _wait_job(client, job_id)['job']['status'] == 'done'
    assert _wait_job(client, _start(client))['job']['status'] == 'done'   # после завершения — можно


def test_api_optimize_errors_are_russian_without_details(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('Login failed for user sa at 192.168.1.4')

    monkeypatch.setattr(opt, 'run_optimization', boom)
    d = _wait_job(client, _start(client))
    assert d['job']['status'] == 'error' and d['result'] is None
    assert d['job']['error'] == 'Հաշվարկի ներքին սխալ — մանրամասները սերվերի մատյանում են'
    assert '192.168' not in json.dumps(d)

    def erp_down(*args, **kwargs):
        raise erp.ErpError('Login failed for user sa at 192.168.1.4')

    monkeypatch.setattr(opt, 'run_optimization', erp_down)
    assert _wait_job(client, _start(client))['job']['error'] == 'ERP տվյալների բազան հասանելի չէ'
    assert client.get('/api/routes/optimize/last').status_code == 404    # успешных расчётов нет


def test_api_optimize_validation(client):
    for url in ('/api/routes/optimize', '/api/routes/decisions'):
        assert client.post(url, data='x', content_type='text/plain').status_code == 415
        assert client.post(url, data='{bad', content_type='application/json').status_code == 400
    r = client.post('/api/routes/optimize', json={'start': 'magic', 'extra': 1})
    assert r.status_code == 400 and set(r.get_json()['errors']) == {'start', '_'}
    r = client.post('/api/routes/optimize', json={'agent_ids': [1, 99]})
    assert r.status_code == 400 and 'agent_ids' in r.get_json()['errors']
    assert client.get('/api/routes/optimize/' + 'f' * 32).status_code == 404
    assert client.get('/api/routes/optimize/nope').status_code == 404
    base = {'customer_id': 103, 'agent_id': 1, 'kind': 'freq', 'value': 0.5, 'action': 'accept'}
    for patch in ({'kind': 'pattern', 'value': [[3, 1]]}, {'value': 0.7}, {'action': 'maybe'},
                  {'customer_id': 999}, {'agent_id': 2}):
        r = client.post('/api/routes/decisions', json={**base, **patch})
        assert r.status_code == 400 and r.get_json()['success'] is False, patch


def test_api_decisions_lock_forbid_and_export(client):
    def decide(cid, kind, value, action):
        r = client.post('/api/routes/decisions', json={'customer_id': cid, 'agent_id': 1, 'kind': kind,
                                                       'value': value, 'action': action})
        assert r.status_code == 200 and r.get_json() == {'success': True}

    _use_snapshot(client, plan=_plan_rows(twice_103=True))
    decide(103, 'remove', [], 'reject')    # §15: 103 без заказов владелец оставил — дальше частота
    res = _wait_job(client, _start(client))['result']
    m1 = _manager(res, 1)
    c103, c104 = _change(m1, 103), _change(m1, 104)
    assert c103 is not None and c104 is not None and c104['type'] == 'move'
    assert c103['type'] == 'frequency' and (c103['from']['freq'], c103['to']['freq']) == (2, 1)

    decide(103, 'pattern', c103['to']['pattern'], 'accept')
    decide(103, 'freq', c103['to']['freq'], 'accept')
    decide(104, 'pattern', c104['to']['pattern'], 'reject')
    # статусы сразу видны в последнем результате
    last = _manager(client.get('/api/routes/optimize/last').get_json()['result'], 1)
    assert _change(last, 103)['decision'] == {'pattern': 'accepted', 'freq': 'accepted'}
    assert _change(last, 104)['decision'] == {'pattern': 'rejected', 'freq': None}

    # выгрузка для ERP: текущий план + только принятое изменение, обе недели цикла
    ex = client.get('/api/routes/plan-export').get_json()
    assert ex['success'] and ex['cycle_weeks'] == 2
    assert [(c['agent_id'], c['customer_id'], c['type']) for c in ex['changes']] == [(1, 103, 'frequency')]
    assert [[r['week'], r['weekday']] for r in ex['rows'] if r['customer_id'] == 103] == c103['to']['pattern']
    assert all(r['mark'] == ('հաճախականություն' if r['customer_id'] == 103 else '') for r in ex['rows'])
    assert sorted((r['agent_id'], r['week'], r['weekday']) for r in ex['rows'] if r['customer_id'] == 104) \
        == [(1, 1, 7), (1, 2, 7), (2, 1, 1), (2, 2, 1)]                 # отклонённый перенос не выгружен
    monday = [r for r in ex['rows'] if (r['agent_id'], r['week'], r['weekday']) == (1, 1, 1)]
    assert [r['no'] for r in monday] == [1, 2] and {r['customer_id'] for r in monday} == {101, 102}
    assert {m['agent_id']: m['changes'] for m in ex['managers']} == {1: 1, 2: 0}

    # следующий расчёт: принятое закреплено, отклонённое не предлагается
    res2 = _wait_job(client, _start(client))['result']
    m1b = _manager(res2, 1)
    c103b = _change(m1b, 103)
    assert c103b['to']['pattern'] == c103['to']['pattern'] and c103b['reason'] == 'օրերն ընդունված են ձեր որոշմամբ'
    assert c103b['decision'] == {'pattern': 'accepted', 'freq': 'accepted'}
    c104b = _change(m1b, 104)
    assert c104b is None or c104b['to']['pattern'] != c104['to']['pattern']
    # «сброс» снимает решение
    decide(104, 'pattern', c104['to']['pattern'], 'reset')
    assert _change(_manager(client.get('/api/routes/optimize/last').get_json()['result'], 1),
                   104) == c104b


def test_api_last_result_survives_restart(client, tmp_path):
    job_id = _start(client)
    assert _wait_job(client, job_id)['job']['status'] == 'done'
    from flask import Flask
    import route_optimizer

    class FakeDb:
        connection_string = 'DRIVER={none};'

    app = Flask(__name__)
    route_optimizer.init_app(app, FakeDb(), db_path=str(tmp_path / 'routes.db'))   # «перезапуск»
    app.extensions['route_optimizer'].snapshots = SnapshotCache(lambda: make_snapshot())
    fresh = app.test_client()
    last = fresh.get('/api/routes/optimize/last').get_json()
    assert last['job']['id'] == job_id and last['job']['status'] == 'done'
    assert len(last['result']['managers']) == 2
    assert fresh.get(f'/api/routes/optimize/{job_id}').get_json()['job']['id'] == job_id


# ============================== этап 3: правки ревью ==============================
# H1 — общие случайные числа; M2/M3 — решения привязаны к плану и не вечны; S1 — частота по сезонам;
# S2 — стоп ILS; S4 — без нерабочих дней при снижении частоты; S5 — решения пачкой; L — мелочи.

def _big_snapshot(seed=1, n_agents=2, per_agent=160):
    """Синтетика размером с жизнь: ≈ 30 визитов в день у менеджера, часть клиентов 2 раза в неделю,
    половина заказывает раз в 2–4 недели."""
    rng = random.Random(seed)
    rows, orders, customers, points, default_addr = [], {}, {}, {}, {}
    start = TODAY - timedelta(days=365)
    cid = 1000
    for a in range(1, n_agents + 1):
        clat, clon = 40.1 + 0.05 * a, 44.4 + 0.08 * a
        for k in range(per_agent):
            cid += 1
            addr = cid + 50000
            points[addr] = (cid, (clat + rng.gauss(0, 0.03), clon + rng.gauss(0, 0.04)))
            default_addr[cid] = addr
            days = rng.sample(range(1, 7), 2) if rng.random() < 0.15 else [rng.randint(1, 6)]
            for d in days:
                rows.append(_row(a, a * 10 + d, 1, d, 1, cid, k, addr))
            step = rng.choice((14, 21, 28)) if rng.random() < 0.55 else rng.choice((7, 7, 5))
            rev = float(rng.choice((15000, 30000, 60000, 90000)))
            kg = float(rng.choice((30, 80, 200)))
            orders[cid] = tuple(dm.Order(cid, start + timedelta(days=i), a, rev * rng.uniform(0.7, 1.3), kg)
                                for i in range(rng.randint(0, 6), 365, step))
            customers[cid] = erp.Customer(cid, f'C{cid}', f'Клиент {cid}', '036', 'Այլ', False, '101')
    agents = range(1, n_agents + 1)
    return replace(
        make_snapshot(), id=f'big-{seed}',
        agents={a: erp.Agent(a, f'A00{a}', f'Менеджер {a}', False) for a in agents},
        plan=pl.build_plan(rows), customers=customers, erp_points=points,
        default_address=default_addr, gps_points={}, orders_by_customer=orders,
        company_orders_by_day=dict(Counter(o.date for os in orders.values() for o in os)),
        first_order={c: TODAY - timedelta(days=600) for c in orders},
        auto_homes={a: geo.HomeGuess(40.18, 44.51, 'night', 20) for a in agents}, facts={},
        active_agents=frozenset(agents),
        cars={f'CAR{a}': erp.Car(f'CAR{a}', 'HOWO', False) for a in agents}, car_days={})


def _truck_bundle(n_agents=2, **settings):
    """Склад, машина у каждого менеджера — с тоннажем и расходом."""
    trucks = {f'CAR{a}': st.Truck(f'CAR{a}', 3000.0, 25.0, a, True) for a in range(1, n_agents + 1)}
    return st.Bundle(dict(st.DEFAULT_SETTINGS, **settings), (40.15, 44.46), trucks, {})


def _unroll_w2(plan):
    """Тот же план в цикле 2 недели: каждый день — в обеих неделях."""
    days = sorted((pl.PlanDay(d.agent_id, w, d.weekday, d.visits) for d in plan.days for w in (1, 2)),
                  key=lambda x: (x.agent_id, x.week, x.weekday))
    counts = Counter(v.customer_id for d in days for v in d.visits)
    return pl.CurrentPlan(2, tuple(days), {c: n / 2 for c, n in counts.items()}, False)


def test_unchanged_plan_w1_and_w2_give_identical_monte_carlo():
    """H1: неизменный план (W = 1) и он же в цикле 2 недели — одни и те же цифры Монте-Карло по
    каждому дню и в неделю: слабые дни, P(день ≥ минимума), бедные рейсы, км грузовиков."""
    snap = _big_snapshot()
    pe0 = ev.evaluate_plan(snap, _truck_bundle())
    days0 = [r for me in pe0.evals for r in me.days]
    assert 25 <= fmean(r.visits for r in days0) <= 35                 # ≈ 30 визитов в день
    # порог дня — медиана ожидаемой выручки дня: много дней у P ≈ 0,5, где шум виден сильнее всего
    b = _truck_bundle(min_day_revenue=round(median(r.low.expected for r in days0)))
    one = ev.evaluate_plan(snap, b)
    snap2 = replace(snap, plan=_unroll_w2(snap.plan))
    two = ev.evaluate_plan(snap2, b, visit_minutes=one.visit_minutes, models=one.models)
    t1, t2 = ev.plan_totals(snap, b, one), ev.plan_totals(snap2, b, two)
    assert 0 < t1['days_below_min'] < t1['days_total'] and t1['truck_km_week'] > 0
    for key in ('days_below_min', 'avg_p_day_ge_min', 'trips_poor_week', 'truck_km_week',
                'truck_liters_week', 'manager_km_week', 'revenue_week_low', 'revenue_week_peak',
                'revenue_week_year', 'avg_load_pct_peak', 'trips_week', 'trips_per_day', 'truck_hours_week',
                'truck_days_short_week', 'avg_load_pct', 'avg_trip_revenue', 'poor_trip_share'):
        assert t2[key] == t1[key], key
    assert one.fleet.week() == two.fleet.week()                     # парк в неделю — до бита
    assert len(two.fleet.days) == 2 * len(one.fleet.days)
    assert all(two.weeks[a] == one.weeks[a] for a in one.weeks)     # все поля недели — до бита
    by_day = {(me.agent_id, r.day.weekday): r for me in one.evals for r in me.days}
    for me in two.evals:
        for r in me.days:
            r1 = by_day[(me.agent_id, r.day.weekday)]
            assert r.low == r1.low, (me.agent_id, r.day.week, r.day.weekday)


def test_day_monte_carlo_is_keyed_by_visit():
    """H1: поток случайных чисел — у визита (клиент, день недели, цель), а не у дня: неделя цикла,
    менеджер и порядок визитов на цифры не влияют; убрали визит — меняется только его вклад."""
    assert len({ev.visit_seed(5, 3, 'low'), ev.visit_seed(5, 4, 'low'), ev.visit_seed(6, 4, 'low'),
                ev.visit_seed(5, 3, 'peak')}) == 4
    rng = random.Random(3)
    visits = [_visit(i, (40.1 + rng.random() * 0.2, 44.4 + rng.random() * 0.2), p=0.35)
              for i in range(30)]
    kw = dict(home=YEREVAN, norms=NORMS, manager_l100=9.0, workday=True)
    a = ev.evaluate_day(pl.PlanDay(1, 1, 3, ()), visits, **kw)
    b = ev.evaluate_day(pl.PlanDay(9, 2, 3, ()), list(reversed(visits)), **kw)
    assert a.low == b.low
    draws, ids = [v.low for v in visits], [v.customer_id for v in visits]
    full, _, _ = ev._outcomes(draws, ids, 3, 'low', 300)
    part, _, _ = ev._outcomes(draws[:-1], ids[:-1], 3, 'low', 300)
    assert {round(x - y, 6) for x, y in zip(full, part)} == {0.0, 60000.0}


def test_sales_frequency_is_season_safe():
    """S1: частота по продажам — по max(λ_год, λ_низкий сезон, λ_пик): летний клиент не теряет
    заказы. Ни в один сезон выручка «стало» не меньше «было» — по компании и по менеджерам."""
    model = ev.CustomerModel(1, dm.Demand(0.25, ((1.0, 1.0),), 365), dm.Demand(0.0, (), 90),
                             dm.Demand(0.99, ((1.0, 1.0),), 92), 'small', 1.0)
    assert opt.season_lam(model) == 0.99
    assert fq.sales_frequency(0.25, 1.0) == 1.0 and fq.sales_frequency(1.01, 1.0) == 2.0
    assert fq.order_rate_text(0.25, 0.99) == \
        'պատվիրում է 4 շաբաթը մեկ (0,25 պատվեր շաբաթում); սեզոնին՝ մինչև 0,99 պատվեր շաբաթում'
    assert fq.order_rate_text(0.33, 0.33) == 'պատվիրում է 3 շաբաթը մեկ (0,33 պատվեր շաբաթում)'
    # 102 заказывает только летом (июнь–август), 2 раза в неделю: по году — раз в 2 недели;
    # посещают 2 раза в неделю (пн + чт)
    base = make_snapshot()
    summer = tuple(dm.Order(102, date(2026, 6, 1) + timedelta(days=7 * k + shift), 1, 30000.0, 40.0)
                   for k in range(13) for shift in (0, 3))
    orders = {**base.orders_by_customer, 102: summer}
    rows = [_row(1, 11, 1, 1, 1, 101, 1, 1001), _row(1, 11, 1, 1, 1, 102, 2, 1002),
            _row(1, 14, 1, 4, 1, 102, 1, 1002),
            _row(1, 12, 1, 2, 1, 103, 1, 0), _row(1, 13, 1, 7, 1, 104, 1, 1004),
            _row(2, 21, 1, 1, 1, 104, 1, 1004), _row(2, 21, 1, 1, 1, 101, 2, 1001)]
    snap = replace(base, orders_by_customer=orders, plan=pl.build_plan(rows),
                   company_orders_by_day=dict(Counter(o.date for os in orders.values() for o in os)))

    def run():
        return opt.run_optimization(snap, _bundle(), None, [], {}).result

    res = run()
    assert _change(_manager(res, 1), 102) is None                      # частоту не снижаем
    seasons = ('revenue_week_low', 'revenue_week_peak', 'revenue_week_year')
    assert all(res['after'][k] >= res['before'][k] - 1 for k in seasons)
    assert all(m['after'][k] >= m['before'][k] - 1 for m in res['managers']
               for k in ('revenue_low', 'revenue_peak', 'revenue_year'))
    assert res['before']['revenue_week_peak'] > 0
    # правило «по году» сократило бы 102 до раза в неделю — и летом модель потеряла бы выручку
    real = opt.season_lam
    try:
        opt.season_lam = lambda m: m.year.lam
        old = run()
    finally:
        opt.season_lam = real
    assert _change(_manager(old, 1), 102)['to']['freq'] == 1
    assert old['after']['revenue_week_peak'] < old['before']['revenue_week_peak']


def test_ils_stops_after_ten_idle_perturbations():
    """S2: ранний стоп ILS — после 10 возмущений подряд без улучшения (было 3); предел 30."""
    assert (sr.MAX_IDLE_PERTURBATIONS, sr.MAX_PERTURBATIONS) == (10, 30)
    points, current, allowed, three = _three_clusters()
    prob = _search_problem(points, current, allowed, workdays=three)
    state = sr.State(prob, [line.current for line in prob.lines], [sr.NO_VISIT] * len(points),
                     change_penalty=300.0)
    stats = sr.search(state, seconds=30)
    assert stats.perturbations == 10 and not stats.time_capped


def test_frequency_drop_moves_sunday_customer_to_a_workday():
    """S4: при снижении частоты новый шаблон — только из рабочих дней. Р3-9: и при той же частоте
    воскресенье больше не сохраняется (раньше текущий шаблон с ним был допустим) — вместо него
    суббота той же недели."""
    six = range(1, 7)
    mon_sun = _wk(1, 7)
    assert mon_sun not in pt.allowed_patterns(mon_sun, 2.0, six)
    assert _wk(1, 6) in pt.allowed_patterns(mon_sun, 2.0, six)         # пн + сб: нестандартный, но допустим
    for f in (2.0, 1.0, 0.5):
        assert all(d in six for p in pt.allowed_patterns(mon_sun, f, six) for _, d in p)
    assert _wk(1) in pt.allowed_patterns(mon_sun, 1.0, six)            # «тот же день» — рабочий
    rows = [_row(1, 11, 1, 1, 1, 101, 1, 1001), _row(1, 11, 1, 1, 1, 102, 2, 1002),
            _row(1, 13, 1, 7, 1, 103, 1, 0),                         # 103: вс, заказов нет → раз в неделю (№30)
            _row(1, 13, 1, 7, 1, 104, 2, 1004), _row(2, 21, 1, 1, 1, 104, 1, 1004)]
    keep = [st.Decision(103, 1, 'remove', st.REMOVE_VALUE, 'rejected')]   # §15: владелец оставил 103
    out = opt.run_optimization(replace(make_snapshot(), plan=pl.build_plan(rows)), _bundle(), None, keep, {})
    o = next(o for o in out.managers if o.agent_id == 1)
    spec, final = dict(zip(o.customers, o.specs)), dict(zip(o.customers, o.final))
    assert len(final[103]) == 2 and all(d in six for _, d in final[103])
    assert all(d in six for p in spec[103].allowed for _, d in p)
    assert spec[104].target == 1.0 and _wk(7) not in spec[104].allowed and _wk(6) in spec[104].allowed
    assert len(final[104]) == 2 and all(d in six for _, d in final[104])


# Р3-9 (ответ владельца №26): воскресенье — нерабочий день, воскресные визиты в шаблонах ERP — случайность.

def test_non_workday_pattern_is_never_allowed():
    """Шаблон с нерабочим днём не допускается даже текущий при той же частоте; вместо него — он же, где
    визит перенесён на субботу той же недели (суббота не рабочая или занята — ближайший рабочий день
    перед ней), частота та же. Нестандартный шаблон из рабочих дней по-прежнему допустим."""
    six, five = range(1, 7), range(1, 6)
    assert pt.workday_pattern(_wk(7), six) == _wk(6)
    assert pt.workday_pattern(((2, 7),), six) == ((2, 6),)                # неделя цикла — та же
    assert pt.workday_pattern(_wk(6, 7), six) == _wk(5, 6)                 # суббота занята — пятница
    assert pt.workday_pattern(_wk(7), five) == _wk(5)                      # суббота не рабочая — пятница
    assert pt.workday_pattern(_wk(1, 2), six) == _wk(1, 2)                 # рабочие дни — как есть
    for current, f in ((_wk(7), 1.0), (((1, 7),), 0.5), (_wk(1, 7), 2.0), (_wk(6, 7), 2.0),
                       (_wk(2, 4, 7), 3.0), (_wk(2, 4, 7), 2.0), (_wk(7), 0.5)):
        allowed = pt.allowed_patterns(current, f, six)
        assert current not in allowed and all(d in six for p in allowed for _, d in p), current
        assert all(len(p) == 2 * f for p in allowed), current
    assert _wk(5, 6) in pt.allowed_patterns(_wk(6, 7), 2.0, six)
    assert _wk(2, 4, 6) in pt.allowed_patterns(_wk(2, 4, 7), 3.0, six)
    assert _wk(1, 2) in pt.allowed_patterns(_wk(1, 2), 2.0, six)          # нестандартный рабочий — как раньше
    assert pt.off_days_text(_wk(1, 7), six) == 'կիրակի՝ ոչ աշխատանքային օր'
    assert pt.off_days_text(_wk(6, 7), five) == 'շաբաթ և կիրակի՝ ոչ աշխատանքային օրեր'
    assert pt.off_days_text(_wk(1, 6), six) is None


def test_current_start_moves_sunday_visits_to_saturday_of_same_week():
    """Старт «current»: визит в воскресенье — на субботу той же недели цикла, частота та же, даже если
    суббота загружена; при снижении частоты — «тот же день» от субботы, неделя — с меньшей нагрузкой."""
    six = range(1, 7)
    points = [(40.18, 44.50), (40.19, 44.51), (40.20, 44.52), (40.17, 44.49)]
    current = [_wk(7), ((2, 7),), _wk(1, 7), _wk(7)]
    targets = [1.0, 0.5, 2.0, 0.5]                                   # 4-й: вс каждую неделю → раз в 2 недели
    allowed = [pt.allowed_patterns(c, f, six) for c, f in zip(current, targets)]
    prob = _search_problem(points, current, allowed)
    base = [_slots(pt.workday_pattern(c, six)) for c in current]
    # суббота 2-й недели загружена сильнее (1-й, 2-й и 3-й клиенты) — 4-й клиент встаёт в субботу 1-й
    assert sr.current_start(prob, base) == [_slots(_wk(6)), _slots(((2, 6),)), _slots(_wk(1, 6)),
                                            _slots(((1, 6),))]


def test_optimization_starts_sunday_customer_on_saturday(monkeypatch):
    """Расчёт целиком: старт поиска — 104 из воскресенья на субботе той же недели (поиск отключён, чтобы
    увидеть сам старт); предложение — перенос с причиной «воскресенье — нерабочий день»."""
    starts = {}

    def no_search(state, **kwargs):
        starts[state.prob.agent_id] = list(state.pattern)
        return sr.SearchStats(cost_start=state.total, cost_end=state.total)

    monkeypatch.setattr(sr, 'search', no_search)
    out = opt.run_optimization(make_snapshot(), _bundle(), None, [], {})
    o = next(o for o in out.managers if o.agent_id == 1)
    assert dict(zip(o.customers, starts[1]))[104] == _slots(_wk(6))
    c104 = _change(_manager(out.result, 1), 104)
    assert (c104['type'], c104['from']['text'], c104['to']['text'], c104['reason']) == \
        ('move', 'կիր, ամեն շաբաթ', 'շբթ, ամեն շաբաթ', 'կիրակի՝ ոչ աշխատանքային օր')


def test_sunday_move_is_mandatory_even_if_change_penalty_exceeds_saving():
    """Перенос с воскресенья предлагается всегда — даже при штрафе за изменение 1 млн драм, который
    больше любого выигрыша: прочие клиенты при таком штрафе дни не меняют (только частоту)."""
    out = opt.run_optimization(make_snapshot(), _bundle(penalty_change=1_000_000), None, [], {})
    m1 = _manager(out.result, 1)
    c104 = _change(m1, 104)
    assert c104['type'] == 'move' and c104['reason'] == 'կիրակի՝ ոչ աշխատանքային օր'
    assert c104['from']['text'] == 'կիր, ամեն շաբաթ' and all(1 <= d <= 6 for _, d in c104['to']['pattern'])
    # прочие — только частота или «убрать из маршрута» (103 без заказов, §15), дни не меняются
    assert all(ch['type'] in ('frequency', 'remove') for ch in m1['changes'] if ch['customer_id'] != 104)
    o = next(o for o in out.managers if o.agent_id == 1)
    assert o.stats.cost_start >= 1_000_000 and o.cost_after <= o.stats.cost_start   # штраф — как обычно


def test_manager_with_whole_template_on_sunday_ends_on_workdays():
    """Весь шаблон менеджера — воскресенье: после расчёта все визиты в рабочие дни, частоты — целевые,
    у каждого клиента — предложение с причиной «воскресенье — нерабочий день»; 103 без заказов за
    год — «убрать из маршрута» (§15): переносить его некуда."""
    six = range(1, 7)
    rows = [_row(1, 13, 1, 7, 1, c, n, addr)
            for n, (c, addr) in enumerate(((101, 1001), (102, 1002), (103, 0), (104, 1004)), 1)]
    rows += [_row(2, 21, 1, 1, 1, 104, 1, 1004), _row(2, 21, 1, 1, 1, 101, 2, 1001)]
    out = opt.run_optimization(replace(make_snapshot(), plan=pl.build_plan(rows)), _bundle(), None, [], {})
    o = next(o for o in out.managers if o.agent_id == 1)
    assert all(d in six for p in o.final for _, d in p)
    assert all(p in s.allowed and len(p) == 2 * s.target for p, s in zip(o.final, o.specs))
    m1 = _manager(out.result, 1)
    assert sorted(ch['customer_id'] for ch in m1['changes']) == [101, 102, 103, 104]
    assert _change(m1, 103)['type'] == 'remove'
    assert all(ch['reason'].startswith('կիրակի՝ ոչ աշխատանքային օր') for ch in m1['changes']
               if ch['type'] != 'remove')
    assert m1['days_after'] and all(day['weekday'] in six for day in m1['days_after'])


# §15 (ответ владельца №27): статус клиента по давности последнего заказа — затихшие и потерянные.

from route_optimizer import status as cst  # noqa: E402

STATUS_S = dict(st.DEFAULT_SETTINGS)
PEAK = [6, 7, 8]


def _weekly_until(cid, silent_days, agent=1, revenue=30000.0, kg=40.0, since_days=365):
    """Заказы раз в неделю с since_days дней назад; последний — silent_days дней назад."""
    last = TODAY - timedelta(days=silent_days)
    n = (since_days - silent_days) // 7 + 1
    return tuple(dm.Order(cid, last - timedelta(days=7 * k), agent, revenue, kg) for k in reversed(range(n)))


def _dates(cid, *days_ago, revenue=10000.0):
    return tuple(dm.Order(cid, TODAY - timedelta(days=d), 1, revenue, 5.0)
                 for d in sorted(days_ago, reverse=True))


def _status(orders, first=TODAY - timedelta(days=600), as_of=TODAY, peak=PEAK, **settings):
    return cst.customer_status(orders, first, as_of, peak, {**STATUS_S, **settings})


def _status_snapshot(orders_by_cid, snapshot_id='syn-risk'):
    """make_snapshot, где у клиентов свои заказы (затихшие, потерянные)."""
    base = make_snapshot(snapshot_id)
    orders = {**base.orders_by_customer, **orders_by_cid}
    return replace(base, orders_by_customer=orders,
                   company_orders_by_day=dict(Counter(o.date for os in orders.values() for o in os)))


def test_customer_status_rules():
    """Таблица §15: новый, без заказов, сезонный, затих, потерян, активный; ≥ 3 заказов — по обычному
    интервалу, 1–2 — по 120 / 240 дн; пороги — из настроек."""
    active = _status(_weekly_until(1, 3))
    assert (active.status, active.silent_days, active.usual_interval_days) == ('active', 3, 7.0)
    assert (active.orders, active.revenue, active.last_order) == (52, 52 * 30000.0, TODAY - timedelta(days=3))
    dormant = _status(_weekly_until(1, 50))                       # 50 > max(45, 3 × 7)
    assert (dormant.status, dormant.silent_days, dormant.silent) == ('dormant', 50, True)
    assert _status(_weekly_until(1, 45)).status == 'active'        # ровно порог — ещё не затих
    lost = _status(_weekly_until(1, 130))                         # 130 > max(120, 6 × 7)
    assert lost.status == 'lost' and lost.orders == 34
    # редкий клиент — порог по его интервалу: раз в 30 дней, 80 дн тишины — норма (порог 90)
    monthly = [TODAY - timedelta(days=d) for d in range(80, 365, 30)]
    orders = tuple(dm.Order(1, day, 1, 10000.0, 5.0) for day in monthly)
    assert (_status(orders).status, _status(orders).usual_interval_days) == ('active', 30.0)
    later = tuple(dm.Order(1, day - timedelta(days=20), 1, 10000.0, 5.0) for day in monthly)   # тишина 100
    assert _status(later).status == 'dormant'                      # 100 > 90, но меньше max(120, 180)
    # 1–2 заказа: затих — больше 120 дн тишины, потерян — больше 240 (без пика: единственный заказ
    # летом сделал бы клиента сезонным)
    assert [_status(_dates(1, d), peak=[]).status for d in (120, 121, 240, 241)] == \
        ['active', 'dormant', 'dormant', 'lost']
    assert _status(_dates(1, 30, 130)).status == 'active'
    assert _status(_dates(1, 130, 250)).usual_interval_days is None
    # нет заказов за 12 месяцев — «без заказов» (и когда первой покупки нет вовсе)
    never = _status(())
    assert (never.status, never.silent_days, never.orders, never.last_order) == ('never', None, 0, None)
    assert _status((), first=None).status == 'never' and _status(_dates(1, 400)).status == 'never'
    # новый: первый заказ меньше 60 дн назад — не трогаем, хоть и молчит
    assert _status(_dates(1, 59), first=TODAY - timedelta(days=59), peak=[]).status == 'new'
    assert _status(_dates(1, 59), first=TODAY - timedelta(days=60), peak=[]).status == 'active'
    assert _status(_dates(1, 59), first=None, peak=[]).status == 'new'   # первая покупка — из окна
    # пороги — из настроек
    assert _status(_weekly_until(1, 50), dormant_min_days=60).status == 'active'
    assert _status(_weekly_until(1, 50), dormant_min_days=30, lost_min_days=40).status == 'lost'
    assert _status(_weekly_until(1, 30), dormant_mult=5, dormant_min_days=1).status == 'active'   # 30 < 5 × 7
    assert _status(_dates(1, 30), status_new_days=31, first=TODAY - timedelta(days=30)).status == 'new'


def test_customer_status_seasonal():
    """≥ 80% заказов окна — в пиковых месяцах, а сейчас не пик: не затих, даже если молчит. В пик —
    обычные правила; меньше 80% в пике — тоже."""
    summer = tuple(dm.Order(1, date(2026, 6, 1) + timedelta(days=7 * k), 1, 30000.0, 40.0) for k in range(13))
    st_off = _status(summer)                                        # сентябрь 2026 — не пик
    assert st_off.status == 'seasonal' and st_off.silent_days == 37 and not st_off.silent
    assert _status(summer, as_of=date(2026, 11, 20)).status == 'seasonal'   # 88 дн тишины, всё ещё сезонный
    assert _status(summer, peak=[6, 7, 8, 9]).status == 'active'   # сентябрь — пик: 37 < max(45, 21)
    assert _status(summer, peak=[6, 7, 8, 11], as_of=date(2026, 11, 20)).status == 'dormant'
    assert _status(summer, peak=[]).status == 'active'             # пика нет — сезонных нет
    mixed = summer + _dates(1, 300, 290, 280, 270)                  # 13 из 17 в пике (76%) — не сезонный
    assert _status(mixed, as_of=date(2026, 11, 20)).status == 'dormant'


def test_customer_status_as_of_has_no_future_leakage():
    """Статус на дату as_of — только по заказам до неё: будущие заказы и первая покупка после as_of
    не учитываются."""
    start = TODAY - timedelta(days=200)
    orders = tuple(dm.Order(1, start + timedelta(days=7 * k), 1, 20000.0, 5.0) for k in range(28))
    first = start
    assert _status(orders, first=first, as_of=TODAY - timedelta(days=250)).status == 'never'  # ещё не покупал
    assert _status(orders, first=first, as_of=TODAY - timedelta(days=180)).status == 'new'
    assert _status(orders, first=first).status == 'active'
    # перерыв: последний заказ до перерыва — 102 дня назад, потом снова — 11 дней назад
    gap = tuple(o for o in orders if not TODAY - timedelta(days=100) < o.date < TODAY - timedelta(days=15))
    then = _status(gap, first=first, as_of=TODAY - timedelta(days=40))
    assert (then.status, then.silent_days, then.orders) == ('dormant', 62, 15)
    assert then.last_order == TODAY - timedelta(days=102)
    now = _status(gap, first=first)                                 # а сегодня — снова покупает
    assert (now.status, now.silent_days, now.orders) == ('active', 11, 16)


def test_status_texts_and_risk_summary():
    assert cst.silence_text(_status(())) == 'վերջին տարում ոչ մի պատվեր'
    assert cst.silence_text(_status(_weekly_until(1, 60))) == 'չի գնում 60 օր (սովորաբար՝ 7 օրը մեկ)'
    assert cst.silence_text(_status(_dates(1, 130))) == 'չի գնում 130 օր'
    assert cst.win_back_text(_status(_weekly_until(1, 60))) == \
        'չի գնում 60 օր (սովորաբար՝ 7 օրը մեկ) — այց ամեն շաբաթ՝ փորձել վերադարձնել'
    summary = cst.risk_summary([_status(_weekly_until(1, 60)), _status(_weekly_until(1, 130)),
                                _status(()), _status(_weekly_until(1, 2))])
    assert summary == {'dormant': 1, 'dormant_rev_year': 44 * 30000, 'lost': 1, 'lost_rev_year': 34 * 30000,
                       'never': 1}


def test_silent_customers_have_zero_demand_but_keep_history():
    """Затих, потерян, без заказов — λ = 0 во всех сезонах: ожидаемая выручка дня и недели без них;
    прошлые заказы (средний заказ, кг, класс размера) и выручка за год — остаются для показа."""
    snap = _status_snapshot({102: _weekly_until(102, 60)})
    ov = ev.build_overview(snap, _bundle())
    calm = ev.build_overview(snap, _bundle(dormant_min_days=90, lost_min_days=200))   # тот же — не затих
    c, c0 = ov['customers']['102'], calm['customers']['102']
    assert (c['status'], c['silent_days'], c['usual_interval_days'], c['orders_year']) == \
        ('dormant', 60, 7.0, 44)
    assert c0['status'] == 'active' and c0['rev_week_low'] > 0 and c0['p_low'] > 0
    assert [c[k] for k in ('rev_week_low', 'rev_week_year', 'p_low', 'p_year', 'rev_visit_low')] == [0] * 5
    assert c['orders_per_week_year'] == c0['orders_per_week_year'] > 0.5          # по истории
    assert c['rev_week_year_hist'] == c0['rev_week_year'] and c['rev_year_hist'] == 44 * 30000
    assert c['last_order_date'] == (TODAY - timedelta(days=60)).isoformat()
    assert all(c[k] == c0[k] for k in ('avg_order_amd', 'avg_order_kg', 'size'))
    # понедельник менеджера 1 (101 и 102): выручка дня меньше ровно на вклад 102
    def monday(o):
        return next(d for m in o['managers'] if m['agent_id'] == 1 for d in m['days'] if d['weekday'] == 1)
    assert monday(calm)['revenue_low_exp'] - monday(ov)['revenue_low_exp'] == \
        pytest.approx(c0['rev_visit_low'], abs=1)
    assert monday(ov)['p_day_ge_min'] <= monday(calm)['p_day_ge_min']
    for key in ('revenue_week_low', 'revenue_week_peak', 'revenue_week_year'):
        assert ov['totals'][key] < calm['totals'][key], key
    models = ev.evaluate_plan(snap, _bundle()).models
    m = models[102]
    assert (m.year.lam, m.low.lam, m.peak.lam) == (0.0, 0.0, 0.0) and m.hist.lam > 0.5 and m.year.values
    assert models[101].hist is models[101].year and models[101].status.status == 'active'


def test_overview_at_risk_totals_and_list():
    """Итог «под риском» и список: затихшие, потерянные и без заказов у менеджеров в расчёте, по
    убыванию выручки за год; у клиента нескольких менеджеров — все они."""
    snap = _status_snapshot({102: _weekly_until(102, 60), 101: _weekly_until(101, 150, revenue=60000.0)})
    ov = ev.build_overview(snap, _bundle())
    assert ov['totals']['at_risk'] == {'dormant': 1, 'dormant_rev_year': 44 * 30000,
                                       'lost': 1, 'lost_rev_year': 31 * 60000, 'never': 1}
    rows = ov['at_risk_customers']
    assert [(r['customer_id'], r['status']) for r in rows] == \
        [(101, 'lost'), (102, 'dormant'), (103, 'never')]
    assert all(set(r) == {'customer_id', 'code', 'name', 'status', 'silent_days', 'usual_interval_days',
                          'orders_year', 'rev_year_hist', 'last_order_date', 'managers'} for r in rows)
    assert [x['code'] for x in rows[0]['managers']] == ['A001', 'A002']
    assert (rows[0]['silent_days'], rows[0]['orders_year'], rows[0]['usual_interval_days']) == (150, 31, 7.0)
    assert (rows[2]['silent_days'], rows[2]['rev_year_hist'], rows[2]['last_order_date']) == (None, 0, None)
    # менеджер 1 вне расчёта — его клиенты (кроме 101 у менеджера 2) не в итоге и не в списке
    off = ev.build_overview(snap, _bundle([st.ManagerProfile(1, included=False)]))
    assert [r['customer_id'] for r in off['at_risk_customers']] == [101]
    assert off['totals']['at_risk'] == {'dormant': 0, 'dormant_rev_year': 0, 'lost': 1,
                                        'lost_rev_year': 31 * 60000, 'never': 0}
    assert off['customers']['102']['status'] == 'dormant'           # статус клиента — у всех клиентов плана


def test_api_overview_status_fields(client):
    snap = _status_snapshot({102: _weekly_until(102, 60)})
    client.application.extensions['route_optimizer'].snapshots = SnapshotCache(lambda: snap)
    d = client.get('/api/routes/overview').get_json()
    assert d['success'] and OVERVIEW_TOTALS <= set(d['totals'])
    assert set(d['totals']['at_risk']) == {'dormant', 'dormant_rev_year', 'lost', 'lost_rev_year', 'never'}
    assert (d['totals']['at_risk']['dormant'], d['totals']['at_risk']['never']) == (1, 1)
    status_keys = {'status', 'silent_days', 'usual_interval_days', 'orders_year', 'rev_year_hist',
                   'rev_week_year_hist', 'last_order_date'}
    assert all(CUSTOMER_KEYS | status_keys <= set(c) for c in d['customers'].values())
    assert {k: c['status'] for k, c in d['customers'].items()} == \
        {'101': 'active', '102': 'dormant', '103': 'never', '104': 'active'}
    assert [r['customer_id'] for r in d['at_risk_customers']] == [102, 103]


def test_pair_spec_status_removal_and_owner_decisions():
    """Потерянный и без заказов — «убрать» только в режиме «по продажам» и если владелец не решил
    иначе: оставил (reject), закрепил шаблон, задал частоту. Принятое «убрать» — в любом режиме."""
    six = list(range(1, 7))
    empty = opt.DecisionBook.from_rows([])
    lost = opt.pair_spec((1, 10), _wk(2), 0.5, 'sales', six, empty, 'lost')
    assert (lost.removed, lost.target, lost.allowed, lost.source, lost.locked) == \
        (True, 0.0, ((),), 'status', False)
    assert opt.pair_spec((1, 10), _wk(2), 0.5, 'sales', six, empty, 'never').removed
    for status in ('dormant', 'seasonal', 'new', 'active', None):
        spec = opt.pair_spec((1, 10), _wk(2), 0.5, 'sales', six, empty, status)
        assert not spec.removed and spec.target == 0.5, status
    assert not opt.pair_spec((1, 10), _wk(2), 0.5, 'current', six, empty, 'lost').removed
    book = opt.DecisionBook.from_rows([
        st.Decision(10, 1, 'remove', st.REMOVE_VALUE, 'rejected'),
        st.Decision(11, 1, 'pattern', pt.pattern_key(_wk(4)), 'accepted'),
        st.Decision(12, 1, 'freq', '1', 'accepted'),
        st.Decision(13, 1, 'remove', st.REMOVE_VALUE, 'accepted'),
        st.Decision(13, 1, 'pattern', pt.pattern_key(_wk(4)), 'accepted'),
    ])
    assert (book.accepted_remove, book.rejected_remove) == (frozenset({(1, 13)}), frozenset({(1, 10)}))
    kept = opt.pair_spec((1, 10), _wk(2), 0.5, 'sales', six, book, 'never')
    assert not kept.removed and kept.target == 0.5                 # оставлен — раз в 2 недели
    assert opt.pair_spec((1, 11), _wk(2), 0.5, 'sales', six, book, 'lost').locked
    assert opt.pair_spec((1, 12), _wk(2), 0.5, 'sales', six, book, 'lost').target == 1.0
    for mode in ('sales', 'current'):                              # принятое «убрать» важнее шаблона
        spec = opt.pair_spec((1, 13), _wk(2), 0.5, mode, six, book, 'active')
        assert (spec.removed, spec.source) == (True, 'manual')


def test_remove_decision_state_parse_and_status():
    """Решение remove привязано к шаблону, от которого принято: клиента у менеджера нет — исполнено;
    шаблон в ERP другой — принятое устарело, отклонённое отработало."""
    pairs = {1: {10: opt.PairInfo(_wk(2), 0)}}
    tue, thu = pt.pattern_key(_wk(2)), pt.pattern_key(_wk(4))

    def state(status, frm, cid=10):
        d = st.Decision(cid, 1, 'remove', st.REMOVE_VALUE, status, from_value=frm)
        return opt.decision_state(d, pairs)

    assert [state('accepted', tue), state('accepted', None), state('accepted', thu)] == \
        ['active', 'active', 'stale']
    assert state('accepted', tue, cid=11) == 'retired'
    assert [state('rejected', tue), state('rejected', thu), state('rejected', tue, cid=11)] == \
        ['active', 'retired', 'retired']
    base = {'customer_id': 5, 'agent_id': 1, 'kind': 'remove', 'action': 'accept'}
    for extra in ({}, {'value': None}, {'value': []}):
        d, errors = opt.parse_decision({**base, **extra})
        assert not errors and (d.kind, d.value, d.from_value) == ('remove', '[]', None), extra
    d, errors = opt.parse_decision({**base, 'from': [[2, 2], [1, 2]]})
    assert not errors and d.from_value == '[[1,2],[2,2]]'
    for bad in ({'value': [[1, 2]]}, {'value': 0}, {'value': True}, {'from': [[3, 1]]}, {'from': 1}):
        assert opt.parse_decision({**base, **bad})[1], bad
    book = opt.DecisionBook.from_rows([st.Decision(10, 1, 'remove', st.REMOVE_VALUE, 'accepted',
                                                   from_value=tue)])
    ch = {'customer_id': 10, 'type': 'remove', 'from': {'freq': 1, 'pattern': [[1, 2], [2, 2]]},
          'to': {'freq': 0, 'pattern': []}}
    assert book.change_status(1, ch) == {'remove': 'accepted'}
    assert book.change_status(1, {**ch, 'from': {'freq': 1, 'pattern': [[1, 4], [2, 4]]}}) == {'remove': None}


def test_optimizer_dormant_weekly_and_lost_removed():
    """Режим «по продажам»: затихшему — раз в неделю (№30) с причиной «попробовать вернуть», потерянному и
    без заказов — «убрать из маршрута» у каждого его менеджера (в «стало» его нет), эффект — км и минуты
    со знаком минус; выручка модели «было → стало» не меняется (у них λ = 0). «Как сейчас» — только дни."""
    snap = _status_snapshot({102: _weekly_until(102, 60), 101: _weekly_until(101, 150, revenue=60000.0)})
    rows = [_row(1, 11, 1, 1, 1, 101, 1, 1001), _row(1, 11, 1, 1, 1, 102, 2, 1002),
            _row(1, 14, 1, 4, 1, 102, 1, 1002),                      # 102 — 2 раза в неделю (пн + чт)
            _row(1, 12, 1, 2, 1, 103, 1, 0), _row(1, 13, 1, 7, 1, 104, 1, 1004),
            _row(2, 21, 1, 1, 1, 104, 1, 1004), _row(2, 21, 1, 1, 1, 101, 2, 1001)]
    snap = replace(snap, plan=pl.build_plan(rows))
    out = opt.run_optimization(snap, _bundle(), None, [], {})
    res = out.result
    m1, m2 = _manager(res, 1), _manager(res, 2)
    c101, c102, c103 = _change(m1, 101), _change(m1, 102), _change(m1, 103)
    assert (c102['type'], c102['from']['freq'], c102['to']['freq']) == ('frequency', 2, 1)
    assert c102['reason'] == \
        'չի գնում 60 օր (սովորաբար՝ 7 օրը մեկ) — այց ամեն շաբաթ՝ փորձել վերադարձնել'
    assert (c101['type'], c101['reason'], c101['to']['text']) == \
        ('remove', 'չի գնում 150 օր (սովորաբար՝ 7 օրը մեկ)', 'հանել երթուղուց')
    assert (c103['type'], c103['reason']) == ('remove', 'վերջին տարում ոչ մի պատվեր')
    assert _change(m2, 101)['type'] == 'remove'
    for ch in (c101, c103, _change(m2, 101)):
        e = ch['effect']
        assert e['manager_km_week'] < 0 and e['minutes_week'] < 0 and ch['decision'] == {'remove': None}
    after_ids = {s['customer_id'] for m in res['managers'] for d in m['days_after'] for s in d['stops']}
    assert 101 not in after_ids and 103 not in after_ids and 102 in after_ids
    assert not [x for m in res['managers'] for x in m['hints'] if x['customer_id'] in (101, 102, 103)]
    assert {c: res['customers'][str(c)]['status'] for c in (101, 102, 103, 104)} == \
        {101: 'lost', 102: 'dormant', 103: 'never', 104: 'active'}
    assert res['customers']['102']['lam_year'] > 0.5                 # по истории — сколько заказывал
    assert (res['customers']['101']['freq_target'], res['customers']['102']['freq_target']) == (0, 1)
    for key in ('revenue_week_low', 'revenue_week_peak', 'revenue_week_year'):
        assert abs(res['after'][key] - res['before'][key]) <= 1, key
    assert res['before']['at_risk']['lost'] == 1 and res['after']['at_risk']['lost'] == 0
    for o in out.managers:                                          # частоты и шаблоны — как у всех
        for spec, final in zip(o.specs, o.final):
            assert final in spec.allowed and len(final) == 2 * spec.target
    as_is = opt.run_optimization(snap, _bundle(), None, [], {'frequencies': 'current'}).result
    assert all(ch['type'] == 'move' for m in as_is['managers'] for ch in m['changes'])


def test_manager_left_without_days_after_removals():
    """Весь шаблон менеджера — воскресенье, и все его клиенты без заказов: в «стало» у него нет ни
    одного дня — неделя без визитов, а не сбой расчёта (живые данные: A007/4)."""
    rows = [_row(1, 11, 1, 1, 1, 101, 1, 1001), _row(1, 11, 1, 1, 1, 102, 2, 1002),
            _row(1, 13, 1, 7, 1, 104, 1, 1004), _row(2, 21, 1, 7, 1, 103, 1, 0)]
    snap = replace(make_snapshot(), plan=pl.build_plan(rows))
    res = opt.run_optimization(snap, _bundle(), None, [], {}).result
    m2 = _manager(res, 2)
    assert [(c['customer_id'], c['type']) for c in m2['changes']] == [(103, 'remove')]
    assert (m2['before']['visits'], m2['after']['visits'], m2['days_after']) == (1, 0, [])
    assert m2['after']['avg_work_hours'] is None and m2['after']['manager_km'] == 0
    assert res['after']['managers'] == 1 and res['before']['managers'] == 2


def test_api_remove_decisions_export_and_keep(client):
    """«Убрать» принято — в выгрузке для ERP клиента нет, в изменениях — «убрать»; действует в
    следующем расчёте в любом режиме. «Оставить» — удаление больше не предлагается: раз в неделю (№30)."""
    res = _wait_job(client, _start(client))['result']
    c103 = _change(_manager(res, 1), 103)
    item = {'customer_id': 103, 'agent_id': 1, 'kind': 'remove', 'value': c103['to']['pattern'],
            'from': c103['from']['pattern'], 'action': 'accept'}
    assert client.post('/api/routes/decisions', json=item).get_json() == {'success': True}
    last = client.get('/api/routes/optimize/last').get_json()['result']
    assert _change(_manager(last, 1), 103)['decision'] == {'remove': 'accepted'}
    d = client.get('/api/routes/decisions').get_json()
    [x] = d['decisions']
    assert set(x) == DECISION_ITEM_KEYS
    assert (x['kind'], x['status'], x['stale'], x['value'], x['from']) == \
        ('remove', 'accepted', False, [], [[1, 2], [2, 2]])
    assert (x['to_text'], x['from_text'], x['current_text']) == ('հանել երթուղուց', 'երք, ամեն շաբաթ',
                                                                  'երք, ամեն շաբաթ')
    assert d['summary'] == {'accepted': 1, 'rejected': 0, 'stale': 0, 'export_changes': 1}
    ex = client.get('/api/routes/plan-export').get_json()
    assert [(c['agent_id'], c['customer_id'], c['type'], c['to']) for c in ex['changes']] == \
        [(1, 103, 'remove', {'freq': 0, 'pattern': [], 'text': 'հանել երթուղուց'})]
    assert not [r for r in ex['rows'] if r['customer_id'] == 103] and {r['mark'] for r in ex['rows']} == {''}
    assert {m['agent_id']: m['changes'] for m in ex['managers']} == {1: 1, 2: 0}
    res2 = _wait_job(client, _start(client, {'frequencies': 'current'}))['result']   # «как сейчас» — тоже
    c = _change(_manager(res2, 1), 103)
    assert (c['type'], c['reason'], c['decision']) == \
        ('remove', 'հեռացումն ընդունված է ձեր որոշմամբ', {'remove': 'accepted'})
    assert all(s['customer_id'] != 103 for day in _manager(res2, 1)['days_after'] for s in day['stops'])
    # «Оставить»: то же решение — отклонено
    assert client.post('/api/routes/decisions', json={**item, 'action': 'reject'}).status_code == 200
    res3 = _wait_job(client, _start(client))['result']
    assert _change(_manager(res3, 1), 103) is None                # был раз в неделю — так и остаётся
    assert {'customer_id': 103, 'kind': 'no_orders', 'text': 'վերջին տարում ոչ մի պատվեր'} in \
        _manager(res3, 1)['hints']
    ex = client.get('/api/routes/plan-export').get_json()
    assert ex['changes'] == [] and len([r for r in ex['rows'] if r['customer_id'] == 103]) == 2
    assert client.get('/api/routes/decisions').get_json()['summary']['rejected'] == 1


def test_api_remove_decision_retired_or_stale_with_erp(client, tmp_path):
    """Принятое «убрать»: ERP убрал клиента у менеджера — решение отработало; ERP перенёс его на
    другой день — решение устарело: не применяется в выгрузке и видно в списке."""
    item = {'customer_id': 103, 'agent_id': 1, 'kind': 'remove', 'action': 'accept'}
    assert client.post('/api/routes/decisions', json=item).status_code == 200
    _use_snapshot(client, 'syn-thu', plan=_plan_rows(day_103=4))           # 103 теперь в четверг
    d = client.get('/api/routes/decisions').get_json()
    [x] = d['decisions']
    assert x['stale'] and x['current_text'] == 'հնգ, ամեն շաբաթ' and d['summary']['export_changes'] == 0
    ex = client.get('/api/routes/plan-export').get_json()
    assert ex['changes'] == [] and [s['customer_id'] for s in ex['stale_decisions']] == [103]
    assert [(r['week'], r['weekday']) for r in ex['rows'] if r['customer_id'] == 103] == [(1, 4), (2, 4)]
    _use_snapshot(client, 'syn-tue')                                         # как было — снова действует
    assert client.get('/api/routes/decisions').get_json()['summary']['export_changes'] == 1
    rows = [_row(1, 11, 1, 1, 1, 101, 1, 1001), _row(1, 11, 1, 1, 1, 102, 2, 1002),
            _row(2, 21, 1, 1, 1, 104, 1, 1004), _row(2, 21, 1, 1, 1, 101, 2, 1001),
            _row(1, 13, 1, 7, 1, 104, 1, 1004)]
    _use_snapshot(client, 'syn-gone', plan=pl.build_plan(rows))            # ERP: 103 у менеджера 1 убран
    assert client.get('/api/routes/decisions').get_json()['decisions'] == []
    with closing(sqlite3.connect(str(tmp_path / 'routes.db'))) as conn:
        assert conn.execute('SELECT customer_id, kind, status FROM decision').fetchall() == \
            [(103, 'remove', 'retired')]


def test_store_remove_decisions_and_migration_4_to_5(tmp_path):
    """Схема 4 → 5: вид решения remove (CHECK пересобран), все решения и значения — как были, частичный
    уникальный индекс снова на месте: одно принятое решение каждого вида, и remove тоже."""
    path = str(tmp_path / 'v4.db')
    rows = [(103, 1, 'pattern', '[[2,2]]', '[[1,2],[2,2]]', 'accepted', '2026-09-01T10:00:00', 'owner'),
            (103, 1, 'freq', '0.5', '1', 'accepted', '2026-09-01T10:00:01', 'owner'),
            (104, 1, 'pattern', '[[1,3],[2,3]]', '[[1,7],[2,7]]', 'rejected', '2026-09-02T10:00:00', None),
            (105, 2, 'freq', '1', None, 'retired', '2026-09-03T10:00:00', 'owner')]
    cols = 'customer_id, agent_id, kind, value, from_value, status, updated_at, updated_by'

    def insert(conn, cid, kind, value, status='accepted'):
        conn.execute(f'INSERT INTO decision({cols}) VALUES(?, 1, ?, ?, NULL, ?, ?, NULL)',
                     (cid, kind, value, status, 'x'))

    with closing(sqlite3.connect(path)) as conn:
        for sql in st._SCHEMA[:5]:
            conn.execute(sql)
        conn.execute(f'CREATE TABLE decision({st._DECISION_COLUMNS_V4})')
        conn.execute(st._SCENARIO_TABLE)
        conn.execute(st._DECISION_ONE_ACCEPTED)
        conn.execute("INSERT INTO meta VALUES('schema_version', '4')")
        conn.execute("INSERT INTO settings VALUES('lost_mult', '8')")
        conn.executemany(f'INSERT INTO decision({cols}) VALUES(?, ?, ?, ?, ?, ?, ?, ?)', rows)
        conn.execute("INSERT INTO scenario VALUES('s1', 'x', 'qa', '{}', '{\"n\": 1}')")
        with pytest.raises(sqlite3.IntegrityError):                          # в схеме 4 remove нет
            insert(conn, 1, 'remove', '[]')
        conn.commit()
    s = st.Store(path)
    assert s.load().settings['lost_mult'] == 8 and s.last_scenario().result == {'n': 1}
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)                                        # 4 → 5 → … → текущая
        got = conn.execute(f'SELECT {cols} FROM decision').fetchall()
        assert sorted(got, key=str) == sorted(rows, key=str)                 # все значения — как были
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert 'decision_one_accepted' in indexes
    assert {(d.customer_id, d.kind) for d in s.load_decisions()} == \
        {(103, 'pattern'), (103, 'freq'), (104, 'pattern')}
    s.save_decision(103, 1, 'remove', st.REMOVE_VALUE, 'accept', 'qa', '[[1,2],[2,2]]')
    d = next(x for x in s.load_decisions() if x.kind == 'remove')
    assert (d.value, d.from_value, d.status, d.updated_by) == ('[]', '[[1,2],[2,2]]', 'accepted', 'qa')
    with closing(sqlite3.connect(path)) as conn, pytest.raises(sqlite3.IntegrityError):
        insert(conn, 103, 'remove', '[0]')                                   # второе принятое remove
    with closing(sqlite3.connect(path)) as conn, pytest.raises(sqlite3.IntegrityError):
        insert(conn, 1, 'drop', '[]')                                        # неизвестный вид
    with closing(sqlite3.connect(path)) as conn:                             # битое значение — явная ошибка
        insert(conn, 7, 'remove', '[[1,2]]', 'rejected')
        conn.commit()
    with pytest.raises(st.StoreError, match='որոշումը'):
        s.load_decisions()


STATUS_KEYS = ('status_new_days', 'dormant_min_days', 'dormant_mult', 'lost_min_days', 'lost_mult')


def test_store_status_settings_validation(store):
    assert [store.load().settings[k] for k in STATUS_KEYS] == [60, 45, 3, 120, 6]
    _, errors = st.validate_payload({'settings': {'status_new_days': 0, 'dormant_mult': 0.5,
                                                  'lost_min_days': 1000, 'dormant_min_days': None}},
                                    store.load(), REF)
    assert set(errors) == {'settings.status_new_days', 'settings.dormant_mult', 'settings.lost_min_days',
                           'settings.dormant_min_days'}
    _, errors = st.validate_payload({'settings': {'dormant_min_days': 150, 'lost_mult': 2}},
                                    store.load(), REF)
    assert set(errors) == {'settings.lost_min_days', 'settings.lost_mult'}   # «потерян» мягче «затих»
    _save(store, {'settings': dict(zip(STATUS_KEYS, (30, 30, 2, 90, 4)))})
    assert [store.load().settings[k] for k in STATUS_KEYS] == [30, 30, 2, 90, 4]


def test_decision_state_against_plan():
    """M2/M3: решение сверяется с планом снимка по from_value."""
    pairs = {1: {10: opt.PairInfo(_wk(2), 0)}}
    tue, thu, fri = (pt.pattern_key(_wk(d)) for d in (2, 4, 5))

    def state(status, value, frm, kind='pattern', cid=10):
        return opt.decision_state(st.Decision(cid, 1, kind, value, status, from_value=frm), pairs)

    assert state('accepted', thu, tue) == 'active'
    assert state('accepted', tue, fri) == 'retired'          # в ERP уже так — план применён
    assert state('accepted', thu, fri) == 'stale'            # принято на другом плане
    assert state('accepted', thu, None) == 'active'          # решение схемы 3: from неизвестен
    assert state('accepted', thu, tue, cid=11) == 'stale'    # клиента у менеджера больше нет
    assert state('rejected', thu, tue) == 'active'
    assert state('rejected', thu, fri) == 'retired'          # запрет относился к прежнему плану
    assert state('accepted', '0.5', '1', kind='freq') == 'active'
    assert state('accepted', '1', '2', kind='freq') == 'retired'
    assert state('accepted', '0.5', '2', kind='freq') == 'stale'
    # статус в строке предложения — только если решение принималось от того же «было»
    book = opt.DecisionBook.from_rows([st.Decision(103, 1, 'pattern', '[[2,2]]', 'accepted',
                                                   from_value='[[1,2],[2,2]]')])
    ch = {'customer_id': 103, 'type': 'frequency', 'from': {'freq': 1, 'pattern': [[1, 2], [2, 2]]},
          'to': {'freq': 0.5, 'pattern': [[2, 2]]}}
    assert book.change_status(1, ch) == {'pattern': 'accepted', 'freq': None}
    other = {**ch, 'from': {'freq': 1, 'pattern': [[1, 3], [2, 3]]}}
    assert book.change_status(1, other)['pattern'] is None


def test_stale_decisions_are_not_applied():
    """M2: принятое на другом плане — не закрепляет в расчёте и не попадает в выгрузку; оба отдают
    stale_decisions: что решено и что в плане сейчас. Отметка «оба» — «перенос, частота»."""
    snap = make_snapshot()
    wed, fri = pt.pattern_key(_wk(3)), pt.pattern_key(_wk(5))
    stale = st.Decision(104, 1, 'pattern', wed, 'accepted', from_value=fri)   # 104 был в пятницу
    both = st.Decision(103, 1, 'pattern', '[[1,4]]', 'accepted', from_value=pt.pattern_key(_wk(2)))
    out = opt.run_optimization(snap, _bundle(), None, [stale, both], {})
    o = next(o for o in out.managers if o.agent_id == 1)
    spec = dict(zip(o.customers, o.specs))
    assert not spec[104].locked and spec[103].locked and spec[103].allowed == (((1, 4),),)
    [s] = out.result['stale_decisions']
    assert {k: s[k] for k in ('customer_id', 'agent_id', 'kind', 'stale', 'from_text', 'to_text',
                              'current_text')} == \
        {'customer_id': 104, 'agent_id': 1, 'kind': 'pattern', 'stale': True,
         'from_text': 'ուրբ, ամեն շաբաթ', 'to_text': 'չրք, ամեն շաբաթ', 'current_text': 'կիր, ամեն շաբաթ'}
    ex = opt.plan_export(snap, _bundle(), [stale, both])
    assert [(c['customer_id'], c['type']) for c in ex['changes']] == [(103, 'both')]
    assert [r['mark'] for r in ex['rows'] if r['customer_id'] == 103] == ['տեղափոխում, հաճախականություն']
    assert [s['customer_id'] for s in ex['stale_decisions']] == [104]


def test_parse_decision_from_batch_and_reset_all():
    d, errors = opt.parse_decision({'customer_id': 5, 'agent_id': 1, 'kind': 'freq', 'value': 0.5,
                                    'from': 1.5, 'action': 'accept'})
    assert not errors and (d.value, d.from_value) == ('0.5', '1.5')
    d, errors = opt.parse_decision({'customer_id': 5, 'agent_id': 1, 'kind': 'pattern', 'value': [[1, 4]],
                                    'from': [[2, 2], [1, 2]], 'action': 'reject'})
    assert not errors and d.from_value == '[[1,2],[2,2]]'
    for bad in (0.7, 8, True, 'x', [[1, 2]]):
        _, errors = opt.parse_decision({'customer_id': 5, 'agent_id': 1, 'kind': 'freq', 'value': 0.5,
                                        'from': bad, 'action': 'accept'})
        assert set(errors) == {'from'}, bad
    one = {'customer_id': 5, 'agent_id': 1, 'kind': 'freq', 'value': 0.5, 'action': 'accept'}
    req, errors = opt.parse_decisions({'items': [one, {**one, 'kind': 'pattern', 'value': [[1, 1]]}]})
    assert not errors and req.mode == 'batch' and len(req.items) == 2
    _, errors = opt.parse_decisions({'items': [one, {**one, 'value': 0.7}, {**one, 'x': 1}]})
    assert set(errors) == {'items.1.value', 'items.2'}
    assert set(opt.parse_decisions({'items': []})[1]) == {'items'}
    assert set(opt.parse_decisions({'items': [one] * (opt.MAX_DECISION_ITEMS + 1)})[1]) == {'items'}
    assert opt.parse_decisions({'action': 'reset_all'}) == (opt.DecisionRequest('reset_all'), {})
    assert set(opt.parse_decisions({'action': 'reset_all', 'customer_id': 1})[1]) == {'_'}


def test_store_save_decisions_is_one_transaction(store):
    """S5: пачка решений — одной транзакцией: сбой на второй записи — не сохранено ничего.
    retire_decisions меняет запись, только если её не переписали после чтения."""
    good = st.DecisionInput(103, 1, 'freq', '0.5', 'accept', '1')
    bad = st.DecisionInput(None, 1, 'freq', '0.5', 'accept', '1')        # NOT NULL — сбой в базе
    with pytest.raises(st.StoreError):
        store.save_decisions([good, bad], 'qa')
    assert store.load_decisions() == []
    rej = st.DecisionInput(104, 1, 'pattern', '[[1,3],[2,3]]', 'reject', '[[1,7],[2,7]]')
    store.save_decisions([good, rej], 'qa')
    loaded = {d.customer_id: d for d in store.load_decisions()}
    assert {(c, d.status, d.from_value) for c, d in loaded.items()} == \
        {(103, 'accepted', '1'), (104, 'rejected', '[[1,7],[2,7]]')}
    store.save_decision(103, 1, 'freq', '0.5', 'accept', 'qa', '2')      # переписано после чтения
    assert store.retire_decisions(list(loaded.values())) == 1            # 103 не тронут
    assert [(d.customer_id, d.from_value) for d in store.load_decisions()] == [(103, '2')]
    assert store.reset_decisions() == 1 and store.load_decisions() == []


def _item(ch, kind, action='accept', agent_id=1):
    side = 'pattern' if kind == 'pattern' else 'freq'
    return {'customer_id': ch['customer_id'], 'agent_id': agent_id, 'kind': kind, 'value': ch['to'][side],
            'from': ch['from'][side], 'action': action}


def _plan_rows(day_103=2, day_104=7, with_104=True, twice_103=False):
    """План make_snapshot, где дни 103 и 104 у менеджера 1 можно поменять («ERP изменился»);
    twice_103 — 103 ещё и в пятницу: 2 раза в неделю, есть куда снижать частоту (№30: не реже 1/нед)."""
    rows = [_row(1, 11, 1, 1, 1, 101, 1, 1001), _row(1, 11, 1, 1, 1, 102, 2, 1002),
            _row(1, 12, 1, day_103, 1, 103, 1, 0),
            _row(2, 21, 1, 1, 1, 104, 1, 1004), _row(2, 21, 1, 1, 1, 101, 2, 1001)]
    if twice_103:
        rows.append(_row(1, 14, 1, 5, 1, 103, 1, 0))
    if with_104:
        rows.append(_row(1, 13, 1, day_104, 1, 104, 1, 1004))
    return pl.build_plan(rows)


def test_api_decisions_batch_list_and_stale(client):
    """S5 + M2: «Принять все» — одним запросом; список «Ваши решения»; ERP изменился — решение
    устарело: в выгрузку не идёт, видно в списке, в выгрузке и в следующем расчёте."""
    _use_snapshot(client, plan=_plan_rows(twice_103=True))
    keep = {'customer_id': 103, 'agent_id': 1, 'kind': 'remove', 'action': 'reject'}   # §15: оставить
    assert client.post('/api/routes/decisions', json=keep).status_code == 200
    res = _wait_job(client, _start(client))['result']
    m1 = _manager(res, 1)
    c103, c104 = _change(m1, 103), _change(m1, 104)
    batch = [_item(c103, 'pattern'), _item(c103, 'freq'), _item(c104, 'pattern')]
    # одна ошибка — не сохраняется ничего (все или ни одного), ошибка — по строке
    r = client.post('/api/routes/decisions', json={'items': [*batch, {**batch[2], 'customer_id': 999}]})
    assert r.status_code == 400 and set(r.get_json()['errors']) == {'items.3.customer_id'}
    listed = client.get('/api/routes/decisions').get_json()['decisions']
    assert [(x['customer_id'], x['kind'], x['status']) for x in listed] == [(103, 'remove', 'rejected')]
    r = client.post('/api/routes/decisions', json={'items': batch})
    assert r.status_code == 200 and r.get_json() == {'success': True, 'saved': 3}
    d = client.get('/api/routes/decisions').get_json()
    assert d['success'] and d['data_as_of'] == res['snapshot_as_of']
    assert d['summary'] == {'accepted': 3, 'rejected': 1, 'stale': 0, 'export_changes': 2}
    assert all(set(x) == DECISION_ITEM_KEYS for x in d['decisions'])
    x = next(x for x in d['decisions'] if (x['customer_id'], x['kind']) == (104, 'pattern'))
    assert (x['agent_code'], x['customer_name'], x['status'], x['stale']) == \
        ('A001', 'Клиент 104', 'accepted', False)
    assert (x['from_text'], x['to_text'], x['current_text']) == \
        ('կիր, ամեն շաբաթ', c104['to']['text'], 'կիր, ամեն շաբաթ')
    f = next(x for x in d['decisions'] if (x['customer_id'], x['kind']) == (103, 'freq'))
    assert (f['from_text'], f['to_text'], f['value'], f['from']) == ('շաբաթը 2 անգամ', 'շաբաթը մեկ անգամ', 1, 2)
    ex = client.get('/api/routes/plan-export').get_json()
    assert sorted(c['customer_id'] for c in ex['changes']) == [103, 104] and ex['stale_decisions'] == []

    _use_snapshot(client, 'syn-2', plan=_plan_rows(day_104=3, twice_103=True))   # ERP: 104 у менеджера 1 — в среду
    ex = client.get('/api/routes/plan-export').get_json()
    assert [c['customer_id'] for c in ex['changes']] == [103]
    assert [(s['customer_id'], s['current_text']) for s in ex['stale_decisions']] == \
        [(104, 'չրք, ամեն շաբաթ')]
    d = client.get('/api/routes/decisions').get_json()
    assert d['summary'] == {'accepted': 2, 'rejected': 1, 'stale': 1, 'export_changes': 1}
    assert [x['customer_id'] for x in d['decisions'] if x['stale']] == [104]
    res2 = _wait_job(client, _start(client))['result']
    assert [(s['customer_id'], s['kind']) for s in res2['stale_decisions']] == [(104, 'pattern')]
    c104b = _change(_manager(res2, 1), 104)       # к новому предложению (от среды) не приклеивается
    assert c104b is None or c104b['decision'] == {'pattern': None, 'freq': None}


def test_api_decisions_retire_reset_and_reset_all(client, tmp_path):
    """M3: исполненное в ERP принятое решение и отклонённое на прежнем плане — отработали (retired):
    не действуют, не видны и не возвращаются. «Сбросить все» и сброс одного — в том числе решения
    по клиенту, которого у менеджера уже нет."""
    _use_snapshot(client, plan=_plan_rows(twice_103=True))
    keep = {'customer_id': 103, 'agent_id': 1, 'kind': 'remove', 'action': 'reject'}   # §15: оставить
    assert client.post('/api/routes/decisions', json=keep).status_code == 200
    res = _wait_job(client, _start(client))['result']
    m1 = _manager(res, 1)
    c103, c104 = _change(m1, 103), _change(m1, 104)
    body = {'items': [_item(c104, 'pattern'), _item(c103, 'pattern', 'reject')]}
    assert client.post('/api/routes/decisions', json=body).status_code == 200
    moved_to = c104['to']['pattern'][0][1]
    assert c104['to']['pattern'] == [[1, moved_to], [2, moved_to]]
    # ERP: перенос 104 применён, а 103 кто-то перенёс на четверг
    _use_snapshot(client, 'syn-3', plan=_plan_rows(day_103=4, day_104=moved_to, twice_103=True))
    d = client.get('/api/routes/decisions').get_json()
    assert d['decisions'] == []
    assert d['summary'] == {'accepted': 0, 'rejected': 0, 'stale': 0, 'export_changes': 0}
    with closing(sqlite3.connect(str(tmp_path / 'routes.db'))) as conn:   # «оставить» 103 — тоже
        assert conn.execute('SELECT customer_id, kind, status FROM decision ORDER BY customer_id, kind'
                            ).fetchall() == [(103, 'pattern', 'retired'), (103, 'remove', 'retired'),
                                             (104, 'pattern', 'retired')]
    _use_snapshot(client, 'syn-4', plan=_plan_rows(twice_103=True))   # план вернули — решения не оживают
    assert client.get('/api/routes/decisions').get_json()['decisions'] == []

    # сброс одного: клиента 104 у менеджера 1 больше нет — решение устарело, но снять его можно
    one = _item(c104, 'pattern')
    assert client.post('/api/routes/decisions', json=one).status_code == 200
    _use_snapshot(client, 'syn-5', plan=_plan_rows(with_104=False, twice_103=True))
    [x] = client.get('/api/routes/decisions').get_json()['decisions']
    assert x['stale'] and x['current_text'] == 'հաճախորդը մենեջերի պլանում չէ'
    assert client.post('/api/routes/decisions', json={**one, 'action': 'accept'}).status_code == 400
    r = client.post('/api/routes/decisions', json={**one, 'action': 'reset'})
    assert r.status_code == 200 and client.get('/api/routes/decisions').get_json()['decisions'] == []
    # «Сбросить все»
    _use_snapshot(client, 'syn-6')
    freq = {'customer_id': 103, 'agent_id': 1, 'kind': 'freq', 'value': 0.5, 'action': 'accept'}
    r = client.post('/api/routes/decisions', json={'items': [freq, {**one, 'action': 'reject'}]})
    assert r.status_code == 200
    assert client.post('/api/routes/decisions', json={'action': 'reset_all', 'x': 1}).status_code == 400
    r = client.post('/api/routes/decisions', json={'action': 'reset_all'})
    assert r.get_json() == {'success': True, 'deleted': 2}
    assert client.get('/api/routes/decisions').get_json()['decisions'] == []


def test_api_decisions_and_export_use_cached_snapshot(client):
    """M2: решения и выгрузка — по снимку из кэша: ERP не перечитывается, даже если TTL истёк."""
    clock, built = _Clock(), []

    def loader():
        built.append(clock.t)
        return make_snapshot(f'snap-{len(built)}')

    client.application.extensions['route_optimizer'].snapshots = SnapshotCache(loader, clock=clock)
    assert client.get('/api/routes/decisions').status_code == 200 and len(built) == 1   # кэш был пуст
    clock.t += 3600                                                     # TTL давно истёк
    body = {'customer_id': 103, 'agent_id': 1, 'kind': 'freq', 'value': 0.5, 'action': 'accept'}
    assert client.post('/api/routes/decisions', json=body).status_code == 200
    assert client.get('/api/routes/decisions').status_code == 200
    assert client.get('/api/routes/plan-export').status_code == 200
    assert len(built) == 1
    assert client.get('/api/routes/overview').status_code == 200         # обзор — по-прежнему свежий
    assert len(built) == 2


def test_api_result_kept_when_scenario_not_saved(client, monkeypatch):
    """L: не сохранился расчёт в таблицу scenario — задача всё равно завершается с результатом."""
    state = client.application.extensions['route_optimizer']

    def disk_full(*args, **kwargs):
        raise st.StoreError('База настроек маршрутов: не удалось сохранить расчёт')

    monkeypatch.setattr(state.store, 'save_scenario', disk_full)
    d = _wait_job(client, _start(client))
    assert d['job']['status'] == 'done' and d['job']['error'] is None and d['result'] is not None
    assert client.get('/api/routes/optimize/last').get_json()['job']['id'] == d['job']['id']


def test_api_keeps_only_last_result_in_memory(client):
    """L: в памяти — результат только последнего расчёта; прежний отдаётся из таблицы scenario."""
    first = _start(client)
    _wait_job(client, first)
    second = _start(client)
    _wait_job(client, second)
    jobs = client.application.extensions['route_optimizer'].jobs
    assert jobs.jobs[first].result is None and jobs.jobs[second].result is not None
    d = client.get(f'/api/routes/optimize/{first}').get_json()
    assert d['job']['status'] == 'done' and len(d['result']['managers']) == 2


# ============================== этап 5: расстояния по дорогам ==============================

from route_optimizer import roads as rd  # noqa: E402

# Синтетический граф (без карты): 0 → 1 — односторонняя (≈ 1 км на север; задана как oneway=-1 от
# 1 к 0 и продублирована), 1 ↔ 2 ↔ 0 — объезд ≈ 5 км в обе стороны; 3 ↔ 4 — отдельный островок.
RN = {0: (40.30, 44.30), 1: (40.309, 44.30), 2: (40.3045, 44.33), 3: (40.40, 44.40), 4: (40.401, 44.40)}
R_WAYS = [([1, 0], -1), ([0, 1], 1), ([1, 2], 0), ([2, 0], 0), ([3, 4], 0)]
KM_PER_DEG_LON = geo.haversine_km((40.30, 44.30), (40.30, 45.30))


def _road_graph():
    return rd.RoadGraph.from_ways(RN, R_WAYS)


def _roads():
    return rd.RoadDistances.for_graph(_road_graph())


def _west(p, km):
    return (p[0], p[1] - km / KM_PER_DEG_LON)


def _north(p, km):
    return (p[0] + km / KM_PER_DEG_LAT, p[1])


def test_roads_way_direction_for_car():
    assert rd.way_direction({'highway': 'residential'}) == 0
    assert rd.way_direction({'highway': 'primary', 'oneway': 'yes'}) == 1
    assert rd.way_direction({'highway': 'primary', 'oneway': 'true'}) == 1
    assert rd.way_direction({'highway': 'primary', 'oneway': '-1'}) == -1
    assert rd.way_direction({'highway': 'tertiary', 'junction': 'roundabout'}) == 1
    assert rd.way_direction({'highway': 'residential', 'junction': 'circular'}) == 1
    assert rd.way_direction({'highway': 'residential', 'junction': 'circular', 'oneway': 'no'}) == 0
    assert rd.way_direction({'highway': 'motorway'}) == 1
    assert rd.way_direction({'highway': 'motorway', 'oneway': 'no'}) == 0
    assert rd.way_direction({'highway': 'secondary_link'}) == 0
    assert rd.way_direction({'highway': 'footway'}) is None
    assert rd.way_direction({'highway': 'service', 'access': 'private'}) is None
    assert rd.way_direction({'highway': 'track'}) is None
    assert rd.way_direction({'highway': 'residential', 'motor_vehicle': 'no'}) is None
    assert rd.way_direction({'highway': 'residential', 'motorcar': 'private'}) is None
    assert rd.way_direction({'highway': 'residential', 'motorcar': 'no'}) is None
    assert rd.way_direction({'highway': 'residential', 'motorcar': 'yes'}) == 0


def test_roads_oneway_respected_directed():
    net = rd.RoadNetwork(_road_graph())
    h = geo.haversine_km
    d01, d10 = h(RN[0], RN[1]), h(RN[1], RN[2]) + h(RN[2], RN[0])
    (n0, n1), snap = net.snap([RN[0], RN[1]])                   # индексы узлов графа ≠ id OSM
    assert list(snap) == [0.0, 0.0]
    there = net.distances(rd.np.array([n0]), rd.np.array([n1]))[0, 0]
    back = net.distances(rd.np.array([n1]), rd.np.array([n0]))[0, 0]
    assert there == pytest.approx(d01)        # повторная линия не складывается в двойной вес
    assert back == pytest.approx(d10)         # против одностороннего — только объездом
    assert net.distances(rd.np.array([n0]), rd.np.array([n1]), reverse=True)[0, 0] == pytest.approx(d10)
    r = _roads()
    r.ensure([RN[0], RN[1]])
    # матрица направленная (план learning-loop, этап 2): туда — по одностороннему, обратно — объездом
    assert r.km(RN[0], RN[1]) == pytest.approx(d01, rel=1e-6)
    assert r.km(RN[1], RN[0]) == pytest.approx(d10, rel=1e-6)


def test_roads_snap_offset_and_fallback_beyond_half_km():
    r = _roads()
    near, far = _west(RN[0], 0.3), _west(RN[0], 0.7)
    r.ensure([near, far, RN[1]])
    base = r.km(RN[0], RN[1])
    # ключ точки — 6 знаков (~0,1 м): привязка считается от округлённой точки
    assert r.km(near, RN[1]) == pytest.approx(geo.haversine_km(near, RN[0]) + base, abs=1e-3)
    assert r.km(far, RN[1]) is None                               # дальше 0,5 км — не привязана
    norms = replace(NORMS, roads=r)
    assert norms.km(far, RN[1]) == pytest.approx(geo.haversine_km(far, RN[1]) * 1.3)
    assert norms.km(near, RN[1]) == pytest.approx(r.km(near, RN[1]))   # по дорогам — без извилистости
    assert r.unsnapped([near, far, RN[1], far]) == 1


def test_roads_unreachable_island_falls_back():
    r = _roads()
    r.ensure([RN[3], RN[0]])
    assert r.km(RN[3], RN[0]) is None                  # островок вне сильно связной компоненты
    assert replace(NORMS, roads=r).km(RN[3], RN[0]) == pytest.approx(
        geo.haversine_km(RN[3], RN[0]) * 1.3)


def test_roads_same_node_is_straight_line():
    a = _north(RN[2], 0.1)
    b = (RN[2][0], RN[2][1] + 0.1 / KM_PER_DEG_LON)
    r = _roads()
    r.ensure([a, b])
    assert r.km(a, b) == pytest.approx(geo.haversine_km(a, b))    # не 0,1 + 0,1 через узел
    assert r.km(a, a) == 0.0


def _grid(n=6, step_km=0.5):
    """Сетка n × n узлов с шагом ≈ step_km: чётные улицы-строки — односторонние на восток,
    нечётные — на запад, столбцы — в обе стороны (граф сильно связный)."""
    nodes = {r * n + c: (40.30 + r * step_km / KM_PER_DEG_LAT, 44.30 + c * step_km / KM_PER_DEG_LON)
             for r in range(n) for c in range(n)}
    ways = [([r * n + c for c in range(n)], 1 if r % 2 == 0 else -1) for r in range(n)]
    ways += [([r * n + c for r in range(n)], 0) for c in range(n)]
    return nodes, rd.RoadGraph.from_ways(nodes, ways, source='grid')


def _grid_batches():
    """Три порции точек: каждая приносит новые узлы; во второй — ещё и точки у старых узлов,
    в третьей — точка дальше 0,5 км от дорог."""
    nodes, _ = _grid()
    rows = [[nodes[r * 6 + c] for c in range(6)] for r in range(6)]
    return [rows[0] + rows[1],
            rows[2] + rows[3] + [_north(rows[0][2], 0.1), _west(rows[1][4], 0.05)],
            rows[4] + rows[5] + [_west(rows[4][0], 0.8)]]


def _assert_same_km(pts, want, *got):
    for a in pts:
        for b in pts:
            w = want.km(a, b)
            for r in got:
                g = r.km(a, b)
                assert (g is None) == (w is None), (a, b)
                assert w is None or g == pytest.approx(w, rel=1e-6), (a, b)


def test_roads_incremental_cache_equals_full(tmp_path):
    _, graph = _grid()
    batches = _grid_batches()
    pts = [p for b in batches for p in b]
    full = rd.RoadDistances.for_graph(graph)
    full.ensure(pts)
    calls = []

    def loader():
        calls.append(1)
        return rd.RoadNetwork(graph)

    path = str(tmp_path / 'grid.dist.npz')
    step = rd.RoadDistances('v1', loader, path, lambda: graph.identity)
    sizes = []
    for batch in batches:
        step.ensure(batch)
        sizes.append(step.size)
    assert sizes[0][1] < sizes[1][1] < sizes[2][1] == full.size[1]   # каждая порция — новые узлы
    assert step.size == full.size and step.unsnapped(pts) == 1
    step.ensure(batches[0])                                        # новых точек нет — граф не нужен
    assert len(calls) == 3
    reread = rd.RoadDistances('v1', loader, path, lambda: graph.identity)
    reread.ensure(pts)                                             # всё в файле — граф не нужен
    assert len(calls) == 3 and reread.size == full.size
    _assert_same_km(pts, full, step, reread)
    # другой граф (другие индексы узлов) — кэш не годится, хотя версия карты та же
    _, other_graph = _grid(step_km=0.6)
    other = rd.RoadDistances.for_graph(other_graph, 'v1', path)
    other.ensure(pts[:1])
    assert other.size == (1, 1)


def test_roads_corrupt_dist_cache_is_recomputed(tmp_path):
    _, graph = _grid()
    pts = [p for b in _grid_batches() for p in b]
    path = tmp_path / 'grid.dist.npz'
    first = rd.RoadDistances.for_graph(graph, 'v1', str(path))
    first.ensure(pts)
    data = path.read_bytes()
    for broken in (data[:len(data) // 2], b'', b'not a zip'):     # обрыв записи, пустой, мусор
        path.write_bytes(broken)
        again = rd.RoadDistances.for_graph(graph, 'v1', str(path))
        again.ensure(pts)                                          # без исключения — пересчёт
        assert not again.failed and again.size == first.size
        _assert_same_km(pts, first, again)


def _write_pbf(path, nodes, ways):
    osmium = pytest.importorskip('osmium')
    from osmium.osm.mutable import Node, Way
    w = osmium.SimpleWriter(str(path))
    for i, (lat, lon) in sorted(nodes.items()):
        w.add_node(Node(id=i + 1, location=(lon, lat)))
    for k, (refs, tags) in enumerate(ways):
        w.add_way(Way(id=100 + k, nodes=[r + 1 for r in refs], tags=tags))
    w.close()


def test_roads_graph_from_map_and_corrupt_graph_cache(tmp_path, monkeypatch):
    path = tmp_path / 'tiny.osm.pbf'
    _write_pbf(path, RN, [([1, 0], {'highway': 'primary', 'oneway': '-1'}),
                          ([1, 2], {'highway': 'residential'}),
                          ([2, 0], {'highway': 'residential'}),
                          ([0, 2], {'highway': 'footway'}),             # не для машины
                          ([3, 4], {'highway': 'service', 'motorcar': 'no'})])
    version = rd.map_signature(str(path))
    graph = rd.load_graph(str(path), version)
    assert graph.n_nodes == 3 and len(graph.src) == 5             # 0 → 1, 1 ↔ 2, 2 ↔ 0
    graph_path = tmp_path / 'tiny.graph.npz'
    cached = rd.RoadGraph.load(str(graph_path), version)
    assert cached is not None and cached.identity == graph.identity
    assert rd.RoadGraph.stored_identity(str(graph_path), version) == graph.identity
    assert rd.RoadGraph.load(str(graph_path), 'other-map') is None
    graph_path.write_bytes(graph_path.read_bytes()[:1000])        # обрыв записи
    assert rd.RoadGraph.load(str(graph_path), version) is None
    assert rd.RoadGraph.stored_identity(str(graph_path), version) is None
    assert rd.load_graph(str(path), version).identity == graph.identity   # пересобран из карты
    assert rd.RoadGraph.load(str(graph_path), version) is not None

    def disk_full(*args, **kwargs):
        raise OSError('нет места')

    monkeypatch.setattr(rd, '_save_npz', disk_full)               # кэш не записался — граф в памяти
    assert rd.load_graph(str(path), version, rebuild=True).identity == graph.identity
    roads = rd.RoadProvider(str(path)).get()
    roads.ensure([RN[0], RN[1]])
    assert not roads.failed and roads.km(RN[0], RN[1]) is not None


def test_route_metrics_and_order_use_road_km_without_detour():
    r = _roads()
    norms = replace(NORMS, roads=r)
    km, minutes = ev.route_metrics([RN[1], RN[2]], RN[0], norms)
    legs = r.km(RN[0], RN[1]) + r.km(RN[1], RN[2]) + r.km(RN[2], RN[0])
    assert km == pytest.approx(legs)                              # ×1,3 не применяется
    assert minutes == pytest.approx(legs / 45.0 * 60)             # вне радиуса города — область
    assert norms.distance == norms.km and NORMS.distance is None
    assert sorted(tsp.route_order([RN[1], RN[2]], RN[0], norms.distance)) == [0, 1]


def test_truck_km_by_roads_no_double_detour():
    r = _roads()
    draw = ev.Draw(1.0, ((60000.0, 10.0),))
    visits = [fl.DeliveryVisit(1, 1, RN[2], draw, draw)]

    def day(norms, depot):
        return fl.fleet_day(1, 2, visits, depot, [FORD], norms, TN, 150000.0)

    road = day(replace(NORMS, roads=r), RN[0])
    assert road.km == pytest.approx(2 * r.km(RN[0], RN[2]))
    far_depot = _west(RN[0], 0.7)                                 # склад не привязан — участки по прямой
    assert day(replace(NORMS, roads=r), far_depot).km == pytest.approx(2 * geo.haversine_km(far_depot, RN[2]) * 1.3)
    assert day(NORMS, RN[0]).km == pytest.approx(2 * geo.haversine_km(RN[0], RN[2]) * 1.3)
    one = tsp.delivery_km(RN[0], [(RN[1], 10.0), (RN[2], 10.0)], None, r.km)
    assert one.km == pytest.approx(r.km(RN[0], RN[1]) + r.km(RN[1], RN[2]) + r.km(RN[2], RN[0]))


def test_overview_without_roads_unchanged_and_warns():
    snap, bundle = make_snapshot(), _truck_bundle()
    ov = ev.build_overview(snap, bundle)
    assert ov['distance_source'] == 'straight'
    assert [w for w in ov['warnings'] if w['code'].startswith('roads')] == [
        {'code': 'roads_off', 'link': '/routes/settings#norms',
         'text': 'Ճանապարհային քարտեզը միացված չէ — կմ-ները հաշվվում են ուղիղ գծով՝ ոլորունության ճշգրտումով։'}]
    # все точки плана дальше 0,5 км от дорог — те же км, что по прямой × извилистость
    far = ev.build_overview(snap, bundle, roads=_roads())
    assert far['distance_source'] == 'roads'
    n = len({rd.point_key(p) for p in ev.plan_points(snap, bundle, {})})
    assert [w['text'] for w in far['warnings'] if w['code'] == 'roads_unsnapped'] == [
        f'Պլանի {n} կետ քարտեզի ճանապարհներից 0,5 կմ-ից ավելի հեռու է — դրանց հասնող կմ-ները հաշվվում են '
        f'ուղիղ գծով՝ ոլորունության ճշգրտումով։']
    assert far['totals'] == ov['totals'] and far['managers'] == ov['managers']


def _double_roads(snap, bundle):
    """«Дороги» ровно вдвое длиннее прямой: полный граф на точках плана с весом 2 × haversine."""
    pts = sorted({rd.point_key(p) for p in ev.plan_points(snap, bundle, {})})
    pairs = [(i, j) for i in range(len(pts)) for j in range(len(pts)) if i != j]
    graph = rd.RoadGraph.from_arrays([p[0] for p in pts], [p[1] for p in pts],
                                     [i for i, _ in pairs], [j for _, j in pairs],
                                     [2 * geo.haversine_km(pts[i], pts[j]) for i, j in pairs])
    return rd.RoadDistances.for_graph(graph)


def test_overview_uses_road_km_for_managers_and_trucks():
    """Дороги ровно вдвое длиннее прямой: км менеджеров и машин — ×2 вместо ×1,3 (без двойной
    извилистости), порядок объезда тот же."""
    snap, bundle = make_snapshot(), _truck_bundle()
    roads = _double_roads(snap, bundle)
    straight = ev.evaluate_plan(snap, bundle)
    road = ev.evaluate_plan(snap, bundle, roads=roads)
    detour = straight.norms.detour
    assert road.norms.roads is roads and straight.norms.roads is None
    for a, wk in straight.weeks.items():
        assert road.weeks[a].manager_km == pytest.approx(wk.manager_km * 2 / detour, rel=1e-6)
    assert road.fleet.week()['km'] == pytest.approx(straight.fleet.week()['km'] * 2 / detour, rel=1e-6)
    ov = ev.build_overview(snap, bundle, roads=roads)
    assert ov['distance_source'] == 'roads'
    assert not [w for w in ov['warnings'] if w['code'].startswith('roads')]


def test_api_overview_cache_key_includes_road_map(client):
    from types import SimpleNamespace as NS
    d = client.get('/api/routes/overview').get_json()
    assert d['distance_source'] == 'straight' and 'roads_off' in {w['code'] for w in d['warnings']}
    state = client.application.extensions['route_optimizer']
    state.roads = NS(get=_roads)                                  # «подложили карту»
    d = client.get('/api/routes/overview').get_json()
    assert d['from_cache'] is False and d['distance_source'] == 'roads'
    assert client.get('/api/routes/overview').get_json()['from_cache'] is True
    assert client.get('/api/routes/plan-export').status_code == 200


def test_road_provider_missing_or_broken_map(tmp_path, client):
    path = tmp_path / 'armenia.osm.pbf'
    provider = rd.RoadProvider(str(path))
    assert provider.get() is None                                 # карты нет — по прямой
    path.write_bytes(b'not a pbf')
    roads = provider.get()
    assert roads is not None and roads.version == rd.map_signature(str(path))
    assert rd.roads_version(roads) == roads.version
    roads.ensure([RN[0], RN[1]])                                  # граф не собрался — не падаем
    assert roads.failed and roads.km(RN[0], RN[1]) is None
    assert provider.get() is roads                                # до смены файла карты — тот же сбой
    assert rd.roads_version(None) == 'off' and rd.roads_version(roads) == roads.version + ':failed'
    # карта есть, но не загрузилась — по прямой, и это не «карта не подключена»
    ov = ev.build_overview(make_snapshot(), _bundle(), roads=roads)
    codes = {w['code']: w['text'] for w in ov['warnings']}
    assert ov['distance_source'] == 'straight' and 'roads_off' not in codes
    assert codes['roads_failed'].startswith('Ճանապարհային քարտեզը չհաջողվեց բեռնել')
    client.application.extensions['route_optimizer'].roads = provider
    d = client.get('/api/routes/overview').get_json()
    assert d['success'] and d['distance_source'] == 'straight'
    assert 'roads_failed' in {w['code'] for w in d['warnings']}
    assert client.get('/api/routes/plan-export').status_code == 200


def test_delivery_heavy_round_trips_use_distance_function():
    depot, a = (40.15, 44.46), (40.20, 44.50)
    heavy = tsp.delivery_km(depot, [(a, 250.0)], 100.0, lambda p, q: 7.0)
    assert heavy == tsp.Delivery(2 * 7.0 * 3, 3)                  # ceil(250 / 100) = 3 поездки туда-обратно


def test_optimizer_matrices_and_run_use_road_km():
    snap, bundle = make_snapshot(), _truck_bundle()
    roads = _double_roads(snap, bundle)
    norms = replace(ev.Norms.from_settings(bundle.settings), roads=roads)
    home, depot = (40.18, 44.51), bundle.depot
    pts = [(40.18, 44.50), (40.19, 44.52), (40.20, 44.55)]
    km, mins = opt._matrices(home, pts, norms)
    allp = [home, *pts]
    for i in range(4):
        for j in range(4):
            if i != j:
                assert km[i][j] == pytest.approx(2 * geo.haversine_km(allp[i], allp[j]), rel=1e-6)
    tkm = opt._Distances(pts, norms).rows(depot, pts)                # матрица парка: вершина 0 — склад
    for j in range(1, 4):
        assert tkm[0][j] == pytest.approx(2 * geo.haversine_km(depot, pts[j - 1]), rel=1e-6)
    straight = opt.run_optimization(snap, bundle, None, [], {})
    road = opt.run_optimization(snap, bundle, None, [], {}, roads=roads)
    detour = straight.before.norms.detour
    assert road.before.norms.roads is roads and road.after.norms.roads is roads
    for a, wk in straight.before.weeks.items():
        assert road.before.weeks[a].manager_km == pytest.approx(wk.manager_km * 2 / detour, rel=1e-6)
    assert road.before.fleet.week()['km'] == pytest.approx(straight.before.fleet.week()['km'] * 2 / detour,
                                                           rel=1e-6)


# ============================== этап 4: передача магазинов между менеджерами (режим Б) ==============================

W_CLUSTER, E_CLUSTER = (40.18, 44.30), (40.18, 44.80)      # районы двух менеджеров, ≈ 42 км друг от друга


def _overlap_rows(own=24, cross=3, seed=4):
    """Строки шаблонов: у каждого менеджера own магазинов в своём районе и cross — в районе другого
    (туда ездят оба: районы пересекаются). День — по кругу пн–сб."""
    rng = random.Random(seed)
    rows, points, cross_ids = [], {}, {1: [], 2: []}
    cid = 2000
    for a, mine, other in ((1, W_CLUSTER, E_CLUSTER), (2, E_CLUSTER, W_CLUSTER)):
        for k in range(own + cross):
            cid += 1
            center = mine if k < own else other
            if k >= own:
                cross_ids[a].append(cid)
            points[cid + 50000] = (cid, (center[0] + rng.uniform(-0.01, 0.01),
                                         center[1] + rng.uniform(-0.012, 0.012)))
            day = 1 + k % 6
            rows.append(_row(a, a * 10 + day, 1, day, 1, cid, k, cid + 50000))
    return rows, points, cross_ids


def _overlap_snapshot(rows=None, debts=None, snapshot_id='overlap'):
    """Два менеджера с пересекающимися районами; все магазины заказывают раз в неделю по 60 000 драм
    (слабых дней нет — передача выгодна только километрами)."""
    base_rows, points, _ = _overlap_rows()
    rows = base_rows if rows is None else rows
    start = TODAY - timedelta(days=365)
    cids = sorted({r.customer_id for r in rows})
    orders = {c: tuple(dm.Order(c, start + timedelta(days=i), 1, 60000.0, 50.0) for i in range(c % 7, 365, 7))
              for c in cids}
    return replace(
        make_snapshot(), id=snapshot_id,
        agents={1: erp.Agent(1, 'A001', 'Арман', False), 2: erp.Agent(2, 'A002', 'Гор', False)},
        plan=pl.build_plan(rows),
        customers={c: erp.Customer(c, f'C{c}', f'Клиент {c}', '036', 'Այլ', False, '101') for c in cids},
        erp_points=points, default_address={p[0]: addr for addr, p in points.items()}, gps_points={},
        orders_by_customer=orders,
        company_orders_by_day=dict(Counter(o.date for os in orders.values() for o in os)),
        first_order={c: TODAY - timedelta(days=600) for c in cids},
        auto_homes={1: geo.HomeGuess(W_CLUSTER[0] + 0.02, W_CLUSTER[1], 'night', 20),
                    2: geo.HomeGuess(E_CLUSTER[0] + 0.02, E_CLUSTER[1], 'night', 20)},
        facts={}, active_agents=frozenset({1, 2}), cars={}, car_days={}, debts=debts or {})


def _strip_run(r):
    out = {k: v for k, v in r.items() if k not in ('generated_at', 'seconds')}
    if 'transfers' in out:
        out['transfers'] = {k: v for k, v in out['transfers'].items() if k != 'seconds'}
    return out


def test_transfer_keys_params_and_decision_parsing():
    assert pt.transfer_key(2, _wk(4)) == '{"agent_id":2,"pattern":[[1,4],[2,4]]}'
    assert pt.parse_transfer_key(pt.transfer_key(2, _wk(4))) == (2, _wk(4))
    assert pt.parse_transfer_key('[[1,4]]') is None and pt.parse_transfer_key('x') is None
    assert opt.parse_params({'mode': 'transfer'})[0]['mode'] == 'transfer'
    assert opt.parse_params({'mode': 'days'})[0] == opt.parse_params({})[0]     # режим А — как на этапе 3
    assert set(opt.parse_params({'mode': 'both'})[1]) == {'mode'}
    base = {'customer_id': 5, 'agent_id': 1, 'kind': 'transfer', 'from': [[1, 2], [2, 2]], 'action': 'accept'}
    d, errors = opt.parse_decision({**base, 'value': {'agent_id': 2, 'pattern': [[2, 4], [1, 4]]}})
    assert not errors and d.value == '{"agent_id":2,"pattern":[[1,4],[2,4]]}' and d.from_value == '[[1,2],[2,2]]'
    for bad in ({'agent_id': 1, 'pattern': [[1, 4]]},           # самому себе
                {'agent_id': 2}, {'agent_id': 2, 'pattern': [[3, 1]]}, {'agent_id': True, 'pattern': [[1, 4]]},
                {'agent_id': 2, 'pattern': [[1, 4]], 'x': 1}, [[1, 4]], None):
        assert 'value' in opt.parse_decision({**base, 'value': bad})[1], bad


def test_transfer_decision_state_and_book():
    p10, p_new = _wk(2), _wk(4)
    value = pt.transfer_key(2, p_new)

    def state(pairs, status='accepted', frm=pt.pattern_key(p10)):
        return opt.decision_state(st.Decision(10, 1, 'transfer', value, status, from_value=frm), pairs)

    info = opt.PairInfo
    assert state({1: {10: info(p10, 0)}, 2: {11: info(_wk(3), 0)}}) == opt.DECISION_ACTIVE
    assert state({1: {10: info(_wk(5), 0)}, 2: {}}) == opt.DECISION_STALE        # дни в ERP изменились
    assert state({1: {}, 2: {}}) == opt.DECISION_STALE                           # клиента нет ни у кого
    assert state({1: {}, 2: {10: info(_wk(6), 0)}}) == opt.DECISION_RETIRED      # в ERP уже передан
    assert state({1: {10: info(_wk(5), 0)}, 2: {}}, 'rejected') == opt.DECISION_RETIRED
    book = opt.DecisionBook.from_rows([
        st.Decision(10, 1, 'transfer', value, 'accepted', from_value=pt.pattern_key(p10)),
        st.Decision(12, 1, 'transfer', pt.transfer_key(2, _wk(1)), 'rejected')])
    assert book.accepted_transfer == {(1, 10): (2, p_new)}
    assert book.rejected_transfer == {(1, 12): frozenset({2})}
    change = {'customer_id': 10, 'type': 'transfer', 'to_agent': 2, 'from': {'pattern': pt.pattern_json(p10)},
              'to': {'pattern': pt.pattern_json(p_new)}}
    assert book.change_status(1, change) == {'transfer': 'accepted'}
    assert book.change_status(1, {**change, 'to_agent': 3}) == {'transfer': None}    # другому — не это решение


def test_customer_debts_use_dashboard_formula():
    class Cursor:
        def __init__(self):
            self.sql = ''

        def execute(self, sql, params):
            self.sql = sql

        def fetchall(self):
            if 'HICUSTOMERSDEBT' in self.sql:     # дебет: D − C по документам клиента
                return [(1, 1000.0), (2, -50.0)]
            return [(1, -100.0, 200.0), (3, 30.0, None)]   # Type01, Type02 — вычитаются по модулю

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

    assert erp.customer_debts(Conn(), [1, 2, 3]) == {1: 700.0, 2: -50.0, 3: -30.0}
    for name in ('SQL_CUSTOMER_DEBIT', 'SQL_CUSTOMER_REST'):
        erp.check_sql(getattr(erp, name).format(ph='?'))


def test_revenue_month_by_order_history():
    def model(values, exposure):
        dem = dm.Demand(1.0, values, exposure)
        return ev.CustomerModel(1, dem, dem, dem, 'small', 1.0)

    assert opt.revenue_month(model(((60000.0, 1.0), (40000.0, 1.0)), 365)) == \
        pytest.approx(100000 / 365 * 365.25 / 12)
    assert opt.revenue_month(model(((30000.0, 1.0),), 7)) == pytest.approx(30000 / 21 * 365.25 / 12)  # ≥ 3 нед.
    assert opt.revenue_month(model((), 365)) == 0.0


def test_distances_matrices_equal_optimizer_matrices():
    norms = ev.Norms.from_settings(st.DEFAULT_SETTINGS)
    rng = random.Random(3)
    pts = [(40.1 + rng.random() * 0.2, 44.4 + rng.random() * 0.3) for _ in range(12)]
    dist = opt._Distances(pts + [(40.3, 44.9)], norms)
    for home, sub in (((40.18, 44.51), pts[:7]), (None, pts[3:]), ((40.2, 44.5), pts[:1])):
        want = opt._matrices(home, sub, norms)
        got = dist.matrices(home, sub)
        for w, g in zip(want, got):
            assert [list(r) for r in g] == [pytest.approx(r, abs=1e-9) for r in w]
        depot = (40.15, 44.46)                                       # матрица парка = матрица «дома» на складе
        assert [list(r) for r in dist.rows(depot, sub)] == \
            [pytest.approx(r, abs=1e-9) for r in opt._matrices(depot, sub, norms)[0]]


def _market(seed=1, n=24, trucks=True):
    """Два менеджера, у каждого все n клиентов: свои (с визитами) и гости (без визитов)."""
    rng = random.Random(seed)
    pts = [(40.1 + rng.random() * 0.25, 44.35 + rng.random() * 0.35) for _ in range(n)]
    norms = ev.Norms.from_settings(st.DEFAULT_SETTINGS)
    six = range(1, 7)
    freqs = [rng.choice((0.5, 1.0, 1.0, 2.0)) for _ in pts]
    allowed = [tuple(_slots(p) for p in pt.standard_patterns(f, six)) for f in freqs]
    params = []
    for _ in pts:
        p, m = rng.choice((0.3, 0.7, 1.0)), rng.choice((20000.0, 50000.0, 90000.0))
        params.append(sr.VisitParams(mu=p * m, var=p * m * m * 1.3 - (p * m) ** 2, p_low=p, p_year=rng.random(),
                                     kg=rng.random() * 900.0))
    owner = [1 if i < n // 2 else 2 for i in range(n)]
    states = {}
    for a, home in ((1, (40.12, 44.40)), (2, (40.30, 44.62))):
        km, mins = opt._matrices(home, pts, norms)
        # числа «заказал» — по клиенту (как opt.truck_u): у обоих менеджеров одни и те же
        lines = [sr.Line(customer_id=100 + i, node=i + 1, minutes=10.0,
                         current=rng.choice(allowed[i]) if owner[i] == a else (), allowed=allowed[i],
                         locked=False, u=sr.make_u(random.Random(i)), gnode=i + 1 if trucks else 0)
                 for i in range(n)]
        base = tuple(sr.day_of_slot(j)[1] in six and any(j in ln.current for ln in lines) for j in range(sr.SLOTS))
        weights = sr.Weights(manager_per_km=45.0, weak_day=20000.0, poor_trip=3000.0, overtime_per_min=500.0,
                             window_min=540.0, min_day_revenue=100000.0, min_trip_revenue=150000.0)
        prob = sr.Problem(agent_id=a, lines=lines, km=km, mins=mins, weights=weights,
                          workday=tuple(sr.day_of_slot(j)[1] in six for j in range(sr.SLOTS)), base=base,
                          neighbors=sr.nearest_lines([ln.node for ln in lines], km), seed=a)
        states[a] = sr.State(prob, [ln.current for ln in lines], params, change_penalty=300.0)
    if trucks:   # общий парк обоих менеджеров: тоннаж 2 т — рейсы режутся
        fleet = _fleet_estimate(pts, [pr.kg for pr in params], capacity=2000.0)
        fleet.build([e for st_ in states.values() for e in st_.fleet_entries()])
        for st_ in states.values():
            st_.attach(fleet)
    clients = [tr.Client(100 + i, owner[i], {1: i, 2: i}) for i in range(n)]
    neighbors = {100 + i: [100 + j for j in range(n) if j != i][:8] for i in range(n)}
    return tr.Market(states, clients, neighbors, 2000.0)


def test_market_incremental_equals_scratch_after_random_moves():
    """Передачи (и группой), обмены и переносы внутри менеджера вперемешку: оценка хода = факт, а после
    300 ходов туры, км, μ/σ² и стоимость каждого менеджера и туры парка — как с нуля (§5), клиента
    посещает ровно один менеджер, стоимость компании = Σ менеджеров + парк + плата за передачи."""
    market = _market()
    assert market.fleet is not None and market.fleet.total > 0
    assert market.consistency_error() < 1e-9
    rng = random.Random(9)
    cids = sorted(market.clients)
    kinds = Counter()
    while sum(kinds.values()) < 300:
        roll = rng.random()
        before = market.total
        if roll < 0.3:
            cid = rng.choice(cids)
            delta, move = market.eval_transfer(cid, 3 - market.owner[cid])
            market.apply(move)
            kinds['transfer'] += 1
        elif roll < 0.45:
            cid = rng.choice(cids)
            hit = market.eval_group(cid, 3 - market.owner[cid])
            if hit is None:
                continue
            delta, move = hit
            market.apply(move)
            kinds['group'] += 1
        elif roll < 0.7:
            c1 = rng.choice(cids)
            hits = [h for h in (market.eval_swap(c1, c2) for c2 in cids if c2 != c1) if h is not None]
            if not hits:
                continue
            delta, move = rng.choice(hits)
            market.apply(move)
            kinds['swap'] += 1
        else:
            cid = rng.choice(cids)
            a = market.owner[cid]
            state, i = market.states[a], market.clients[cid].lines[a]
            alts = [p for p in state.lines[i].allowed if p != state.pattern[i]]
            delta, move = state.eval_relocate(i, rng.choice(alts))
            state.apply(move)
            market.total = market._total()
            kinds['relocate'] += 1
        assert market.total - before == pytest.approx(delta, abs=1e-6)
        if not sum(kinds.values()) % 50:
            market.polish()
    assert min(kinds.values()) > 20 and len(kinds) == 4
    assert market.consistency_error() < 1e-6
    assert market.n_moved == sum(1 for cid, c in market.clients.items() if market.owner[cid] != c.origin)


def test_market_search_is_deterministic_and_never_worse():
    runs = []
    for _ in range(2):
        market = _market(seed=5, trucks=False)
        start = market.total
        stats = tr.search(market, seconds=30, seed=11)
        assert not stats.time_capped and market.total <= start + 1e-6
        assert market.consistency_error() < 1e-6
        runs.append((dict(market.owner), [list(market.states[a].pattern) for a in (1, 2)], market.total))
    assert runs[0] == runs[1]


def test_transfers_remove_overlap_of_districts():
    """Два менеджера ездят в районы друг друга: режим Б передаёт «чужие» магазины тому, у кого они
    рядом, — у каждого остаётся свой район, км меньше, чем в режиме А; передача — с балансом.
    Режим А собирает 3 «чужих» магазина в один день — поездка ≈ 84 км в неделю (≈ 3 800 драм);
    по одному передавать невыгодно (поездка остаётся), выгодно только группой — и только если передача
    дешевле: 3 × 500 драм (при 2 000 по умолчанию — 6 000, больше выгоды)."""
    rows, _, cross = _overlap_rows()
    debts = {c: 1000.0 * (c % 5) for c in cross[1] + cross[2]}
    snap = _overlap_snapshot(debts=debts)
    days = opt.run_optimization(snap, _bundle(), None, [], {})
    out = opt.run_optimization(snap, _bundle(penalty_transfer=500), None, [], {'mode': 'transfer'})
    res = out.result
    assert res['params']['mode'] == 'transfer' and 'transfers' not in days.result
    assert all('balance' not in m for m in days.result['managers'])
    moved = {(ch['from_agent'], ch['to_agent'], ch['customer_id'])
             for m in res['managers'] for ch in m['changes'] if ch['type'] == 'transfer'}
    assert moved == {(1, 2, c) for c in cross[1]} | {(2, 1, c) for c in cross[2]}
    assert res['transfers']['count'] == 6
    # у каждого — только свой район
    for m in res['managers']:
        lons = [s['lon'] for d in m['days_after'] for s in d['stops']]
        assert lons and all((lon < 44.55) == (m['agent_id'] == 1) for lon in lons)
    # км меньше, чем в режиме А; старт поиска — итог режима А, и стоимость не хуже его
    assert res['after']['manager_km_week'] < days.result['after']['manager_km_week'] - 150   # 2 поездки по 84 км
    assert out.transfer.cost_start == pytest.approx(sum(o.cost_after for o in days.managers), abs=1e-6)
    assert out.transfer.cost_end < out.transfer.cost_start
    assert res['after']['revenue_week_low'] == pytest.approx(days.result['after']['revenue_week_low'], abs=1)
    # предложение «передать»: поля, эффект по обоим менеджерам, выручка и долг
    m1 = _manager(res, 1)
    ch = next(c for c in m1['changes'] if c['type'] == 'transfer')
    assert {'from_agent', 'to_agent', 'revenue_month', 'debt', 'effect_from', 'effect_to', 'reason_kind'} <= set(ch)
    assert ch['decision'] == {'transfer': None} and ch['reason_kind'] == 'km'
    assert ch['effect_from']['manager_km_week'] < -50 and ch['effect']['manager_km_week'] < -50
    assert ch['effect']['manager_km_week'] == pytest.approx(
        ch['effect_from']['manager_km_week'] + ch['effect_to']['manager_km_week'], abs=0.11)
    assert ch['debt'] == round(debts[ch['customer_id']])
    assert ch['revenue_month'] == round(opt.revenue_month(out.before.models[ch['customer_id']]))
    assert ch['reason'].startswith('Гор՝ արդեն աշխատում է այս տարածքում')
    # баланс: у каждого «отдаёт» = сумма его передач, по компании «отдано» = «получено»
    for m in res['managers']:
        mine = [c for c in m['changes'] if c['type'] == 'transfer']
        assert m['balance']['given']['stores'] == len(mine) == 3
        assert abs(m['balance']['given']['revenue_month'] - sum(c['revenue_month'] for c in mine)) <= len(mine)
        assert abs(m['balance']['given']['debt'] - sum(c['debt'] for c in mine)) <= len(mine)
    for key in ('stores', 'revenue_month', 'debt'):
        assert sum(m['balance']['given'][key] for m in res['managers']) == \
            sum(m['balance']['received'][key] for m in res['managers'])
    assert res['transfers']['revenue_month'] == sum(m['balance']['given']['revenue_month'] for m in res['managers'])
    o1 = next(o for o in out.managers if o.agent_id == 1)
    assert o1.moved_to == {c: 2 for c in cross[1]}
    # повторяемость
    again = opt.run_optimization(snap, _bundle(penalty_transfer=500), None, [], {'mode': 'transfer'})
    assert _strip_run(again.result) == _strip_run(res)


def test_transfer_penalty_and_owner_decisions():
    rows, _, cross = _overlap_rows()
    snap = _overlap_snapshot()
    # передача дороже выгоды (по умолчанию 2 000: группа из 3 — 6 000 при выгоде ≈ 3 800) — ничего
    # не передаётся, «стало» как в режиме А
    none = opt.run_optimization(snap, _bundle(), None, [], {'mode': 'transfer'})
    assert none.result['transfers']['count'] == 0
    days = opt.run_optimization(snap, _bundle(), None, [], {})
    for mb, ma in zip(none.result['managers'], days.result['managers']):   # туры могли стать короче
        same = {k: v for k, v in ma['after'].items() if k != 'cost'}
        assert {k: v for k, v in mb['after'].items() if k != 'cost'} == same
        assert mb['after']['cost'] <= ma['after']['cost']
    # принятая передача закреплена в принятых днях, отклонённая (клиент → менеджер) запрещена
    c_acc, c_rej = cross[1][0], cross[1][1]
    pairs = opt.plan_pairs(snap.plan)
    fixed_days = _wk(6)
    decisions = [st.Decision(c_acc, 1, 'transfer', pt.transfer_key(2, fixed_days), 'accepted',
                             from_value=pt.pattern_key(pairs[1][c_acc].pattern)),
                 st.Decision(c_rej, 1, 'transfer', pt.transfer_key(2, _wk(1)), 'rejected',
                             from_value=pt.pattern_key(pairs[1][c_rej].pattern))]
    res = opt.run_optimization(snap, _bundle(penalty_transfer=500), None, decisions, {'mode': 'transfer'}).result
    m1 = _manager(res, 1)
    acc = _change(m1, c_acc)
    assert acc['type'] == 'transfer' and acc['to']['pattern'] == pt.pattern_json(fixed_days)
    assert acc['decision'] == {'transfer': 'accepted'} and acc['reason_kind'] == 'owner'
    assert res['transfers']['accepted_fixed'] == 1
    rej = _change(m1, c_rej)
    assert rej is None or rej['type'] != 'transfer'          # отклонённому менеджеру не передаётся


def _use_overlap(client, snap):
    client.application.extensions['route_optimizer'].snapshots = SnapshotCache(lambda: snap)


def test_api_transfer_decision_export_and_stale(client):
    rows, _, cross = _overlap_rows()
    _use_overlap(client, _overlap_snapshot(debts={c: 5000.0 for c in cross[1]}))
    assert client.post('/api/routes/settings', json={'settings': {'penalty_transfer': 500}}).status_code == 200
    res = _wait_job(client, _start(client, {'mode': 'transfer'}))['result']
    assert res['params']['mode'] == 'transfer' and res['transfers']['count'] == 6
    assert all(set(m['balance']) == {'given', 'received'} for m in res['managers'])
    m1 = _manager(res, 1)
    ch, ch2 = [c for c in m1['changes'] if c['type'] == 'transfer'][:2]
    c = ch['customer_id']

    def decide(change, action):
        return client.post('/api/routes/decisions', json={
            'customer_id': change['customer_id'], 'agent_id': 1, 'kind': 'transfer', 'action': action,
            'value': {'agent_id': change['to_agent'], 'pattern': change['to']['pattern']},
            'from': change['from']['pattern']})

    assert decide(ch, 'accept').get_json() == {'success': True}
    bad = client.post('/api/routes/decisions', json={'customer_id': c, 'agent_id': 1, 'kind': 'transfer',
                                                      'action': 'accept',
                                                      'value': {'agent_id': 99, 'pattern': [[1, 1]]}})
    assert bad.status_code == 400 and 'value' in bad.get_json()['errors']     # кому — нет маршрутов в ERP
    last = _manager(client.get('/api/routes/optimize/last').get_json()['result'], 1)
    assert _change(last, c)['decision'] == {'transfer': 'accepted'}

    # план для ERP: клиент — у нового менеджера с отметкой «передать», в изменениях — «передать: от → кому»
    ex = client.get('/api/routes/plan-export').get_json()
    mine = [r for r in ex['rows'] if r['customer_id'] == c]
    assert {r['agent_id'] for r in mine} == {2} and {r['mark'] for r in mine} == {'փոխանցել'}
    assert sorted([r['week'], r['weekday']] for r in mine) == ch['to']['pattern']
    assert mine[0]['address_id'] == c + 50000                               # адрес — из шаблона прежнего
    tch = [x for x in ex['changes'] if x['customer_id'] == c]
    assert len(tch) == 1 and tch[0]['type'] == 'transfer' and tch[0]['agent_id'] == 1
    assert (tch[0]['from_agent_code'], tch[0]['to_agent_code']) == ('A001', 'A002')
    assert tch[0]['from']['text'].startswith('A001: ') and tch[0]['to']['text'].startswith('A002: ')
    assert {m['agent_id']: m['changes'] for m in ex['managers']} == {1: 1, 2: 0}
    view = client.get('/api/routes/decisions').get_json()
    assert view['summary']['export_changes'] == 1 and view['summary']['accepted'] == 1
    item = view['decisions'][0]
    assert item['kind'] == 'transfer' and item['value']['agent_code'] == 'A002'
    assert item['to_text'].startswith('փոխանցել A002-ին՝ ') and item['stale'] is False

    # отклонённая передача в следующем расчёте этому менеджеру не предлагается; принятая — закреплена
    assert decide(ch2, 'reject').get_json() == {'success': True}
    res2 = _wait_job(client, _start(client, {'mode': 'transfer'}))['result']
    m1b = _manager(res2, 1)
    assert _change(m1b, c)['decision'] == {'transfer': 'accepted'}
    c2 = _change(m1b, ch2['customer_id'])
    assert c2 is None or c2['type'] != 'transfer'

    # дни клиента в ERP изменились — решение устарело: не применяется и показывается
    changed = [replace(r, weekday=r.weekday % 6 + 1) if r.customer_id == c else r for r in rows]
    _use_overlap(client, _overlap_snapshot(rows=changed, snapshot_id='overlap-2'))
    ex2 = client.get('/api/routes/plan-export').get_json()
    assert {r['agent_id'] for r in ex2['rows'] if r['customer_id'] == c} == {1}
    assert [x['customer_id'] for x in ex2['stale_decisions']] == [c]
    # в ERP уже передали — решение отработало и уходит из списка
    done = [replace(r, agent_id=2, template_id=99) if r.customer_id == c else r for r in rows]
    _use_overlap(client, _overlap_snapshot(rows=done, snapshot_id='overlap-3'))
    view = client.get('/api/routes/decisions').get_json()
    assert c not in [x['customer_id'] for x in view['decisions'] if x['kind'] == 'transfer']


def test_api_optimize_mode_validation(client):
    r = client.post('/api/routes/optimize', json={'mode': 'magic'})
    assert r.status_code == 400 and set(r.get_json()['errors']) == {'mode'}
    d = _wait_job(client, _start(client, {'mode': 'days'}))
    assert d['result']['params'] == {'agent_ids': [1, 2], 'start': 'current', 'frequencies': 'sales'}
    assert set(d['result']) == RESULT_KEYS                                   # режим А — без новых полей


def test_store_migrates_schema_5_to_6_keeps_values(tmp_path):
    """Схема 5 → 6: вид решения transfer (CHECK пересобран), все решения, настройки и расчёты — как были;
    новые настройки — по умолчанию; одно принятое решение каждого вида — и у передачи."""
    path = str(tmp_path / 'v5.db')
    rows = [(103, 1, 'pattern', '[[2,2]]', '[[1,2],[2,2]]', 'accepted', '2026-09-01T10:00:00', 'owner'),
            (103, 1, 'remove', '[]', '[[1,2],[2,2]]', 'rejected', '2026-09-01T10:00:01', 'owner'),
            (105, 2, 'freq', '1', None, 'retired', '2026-09-03T10:00:00', 'owner')]
    cols = 'customer_id, agent_id, kind, value, from_value, status, updated_at, updated_by'
    transfer_row = f'INSERT INTO decision({cols}) VALUES(?, 1, ?, ?, NULL, ?, ?, NULL)'
    with closing(sqlite3.connect(path)) as conn:
        for sql in st._SCHEMA[:5]:
            conn.execute(sql)
        conn.execute(f'CREATE TABLE decision({st._DECISION_COLUMNS_V5})')
        conn.execute(st._SCENARIO_TABLE)
        conn.execute(st._DECISION_ONE_ACCEPTED)
        conn.execute("INSERT INTO meta VALUES('schema_version', '5')")
        conn.execute("INSERT INTO settings VALUES('penalty_change', '450')")
        conn.executemany(f'INSERT INTO decision({cols}) VALUES(?, ?, ?, ?, ?, ?, ?, ?)', rows)
        conn.execute("INSERT INTO scenario VALUES('s1', 'x', 'qa', '{}', ?)", (json.dumps({'n': 1}),))
        with pytest.raises(sqlite3.IntegrityError):                          # в схеме 5 transfer нет
            conn.execute(transfer_row, (1, 'transfer', pt.transfer_key(2, _wk(1)), 'accepted', 'x'))
        conn.commit()
    s = st.Store(path)
    bundle = s.load()
    assert bundle.settings['penalty_change'] == 450
    assert (bundle.settings['penalty_transfer'], bundle.settings['transfer_radius_km']) == (2000, 1.5)
    assert s.last_scenario().result == {'n': 1}
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() ==             (str(st.SCHEMA_VERSION),)                                       # 5 → 6 → … → текущая
        assert sorted(conn.execute(f'SELECT {cols} FROM decision').fetchall(), key=str) == sorted(rows, key=str)
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert 'decision_one_accepted' in indexes
    s.save_decision(103, 1, 'transfer', pt.transfer_key(2, _wk(4)), 'accept', 'qa', '[[1,2],[2,2]]')
    s.save_decision(103, 1, 'transfer', pt.transfer_key(3, _wk(5)), 'accept', 'qa', '[[1,2],[2,2]]')
    got = [d for d in s.load_decisions() if d.kind == 'transfer']
    assert [(d.value, d.status) for d in got] == [(pt.transfer_key(3, _wk(5)), 'accepted')]   # одно принятое
    with closing(sqlite3.connect(path)) as conn:                             # передача самому себе — битая
        conn.execute(transfer_row, (7, 'transfer', pt.transfer_key(1, _wk(1)), 'rejected', 'x'))
        conn.commit()
    with pytest.raises(st.StoreError, match='որոշումը'):
        s.load_decisions()


def test_store_transfer_settings_validation(store):
    for key, bad in (('penalty_transfer', -1), ('penalty_transfer', 2e6), ('transfer_radius_km', 0),
                     ('transfer_radius_km', 50)):
        _, errors = st.validate_payload({'settings': {key: bad}}, store.load(), REF)
        assert f'settings.{key}' in errors
    _save(store, {'settings': {'penalty_transfer': 3500, 'transfer_radius_km': 2.5}})
    s = store.load().settings
    assert (s['penalty_transfer'], s['transfer_radius_km']) == (3500, 2.5)


# ============================== парк машин: оценка, оптимизатор, настройки (fleet-plan) ==============================

def test_fleet_mixes_orders_of_managers_in_one_trip():
    """Правило «одна машина = один менеджер» отменено (№29): заказы двух менеджеров одного дня
    доставки, которые лежат рядом, едут одним рейсом одной машины."""
    rows = [_row(1, 11, 1, 1, 1, 201, 1, 2001), _row(2, 21, 1, 1, 1, 202, 1, 2002)]
    orders = {c: _orders_every(c, a, 7, 60000.0, 300.0) for a, c in ((1, 201), (2, 202))}
    snap = replace(make_snapshot(), plan=pl.build_plan(rows),
                   customers={c: erp.Customer(c, f'C{c}', f'Клиент {c}', '036', 'Այլ', False, '101')
                              for c in (201, 202)},
                   erp_points={2001: (201, (40.40, 44.50)), 2002: (202, (40.401, 44.501))},
                   default_address={201: 2001, 202: 2002}, gps_points={}, orders_by_customer=orders,
                   first_order={c: TODAY - timedelta(days=600) for c in orders})
    bundle = st.Bundle(dict(st.DEFAULT_SETTINGS), (40.15, 44.46),
                       {'CAR1': st.Truck('CAR1', 3000.0, 25.0, 1, True)}, {})
    pe = ev.evaluate_plan(snap, bundle)
    (day,) = pe.fleet.days
    assert (day.week, day.weekday) == (1, 2)                          # визиты пн → доставка вт
    assert day.trips == pytest.approx(1.0) and day.orders == pytest.approx(2.0)   # p = 1: оба заказа, один рейс
    t = ev.plan_totals(snap, bundle, pe)
    assert t['truck_liters_week'] == pytest.approx(t['truck_km_week'] * 25.0 / 100, abs=0.06)
    assert t['trips_week'] == pytest.approx(1.0) and t['avg_load_pct'] == pytest.approx(20.0)
    no_fleet = ev.evaluate_plan(snap, replace(bundle, trucks={'CAR1': st.Truck('CAR1', None, 25.0)}))
    assert no_fleet.fleet is None and no_fleet.trucks_incomplete == ('CAR1',)
    assert ev.plan_totals(snap, bundle, no_fleet)['truck_km_week'] is None


def test_overview_fleet_block_and_warnings():
    snap = make_snapshot()
    bundle = st.Bundle(dict(st.DEFAULT_SETTINGS), (40.15, 44.46),
                       {'CAR1': st.Truck('CAR1', 3000.0, 25.0), 'CAR2': st.Truck('CAR2', None, None)}, {})
    ov = ev.build_overview(snap, bundle)
    codes = {w['code'] for w in ov['warnings']}
    assert 'truck_incomplete' in codes and 'no_fleet' not in codes and 'no_depot' not in codes
    f = ov['fleet']
    assert f['trucks'][0]['car_code'] == 'CAR1' and (f['work_start'], f['work_end']) == ('09:00', '18:00')
    assert [d['label'] for d in f['days']] == ['Երկ', 'Երք', 'Չրք']      # вс → пн, пн → вт, вт → ср
    assert all(d['trips'] >= 0 and set(d) >= {'kg', 'km', 'liters', 'load_pct', 'trucks', 'p_short_peak'}
               for d in f['days'])
    none = ev.build_overview(snap, replace(bundle, trucks={}))
    assert none['fleet'] is None and 'no_fleet' in {w['code'] for w in none['warnings']}


def test_fleet_estimate_matches_split_and_cost():
    """Быстрая оценка парка: км тура, нарезанного по тоннажу, и машино-минуты сверх дня — как по
    определению; ход «уйти + прийти» в тот же день той же точки тур не меняет."""
    pts = [(40.20, 44.50), (40.21, 44.51), (40.40, 44.80)]
    fleet = _fleet_estimate(pts, [1000.0, 1500.0, 900.0], capacity=2000.0)
    fleet.build([(0, 1, (True,) * 4), (0, 2, (True,) * 4), (0, 3, (True, False, True, False))])
    d = fleet.d
    t = fleet.tours[0][0]
    assert sorted(t[1:]) == [1, 2, 3]
    km, mins = fleet.split(t)
    legs, prev, load = 0.0, 0, 0.0
    for x in t[1:]:                                                   # по определению: новый рейс, если переполнит
        if prev and load + fleet.kg[x] > 2000.0:
            legs += d[prev][0]
            prev, load = 0, 0.0
        legs += d[prev][x]
        prev, load = x, load + fleet.kg[x]
    legs += d[prev][0]
    assert km == pytest.approx(legs) and mins > km                   # минуты: езда + разгрузка
    assert fleet.cost[0] == pytest.approx(fleet.cost_of(fleet.km[0], fleet.mins[0]))
    assert fleet.effect_km([(0, 3, (True,) * 4)], [(0, 3, (True,) * 4)]) == 0.0
    gone = fleet.effect_km([(0, 3, (True, False, True, False))], [])
    assert gone < 0                                                   # дальняя точка ушла — км меньше
    snap = fleet.snapshot()
    rec = fleet.apply(0, [(0, *fleet.eval(0, 0, 3, 0)[:2], 3, 0, *fleet.eval(0, 0, 3, 0)[2:])], 0.0)
    fleet.revert(rec)
    assert fleet.snapshot()[:5] == snap[:5]                           # откат хода — ровно как было


def test_search_gauss_seidel_aligns_far_orders_with_other_manager():
    """Менеджеры связаны через дни доставки: у B дальний магазин во вторник, а парк и так ездит в тот
    же район (дом напротив) в понедельник — с заказами менеджера A. Для B дни почти равноценны, а парку
    выгоднее везти всё одним рейсом — поиск переносит магазин B на понедельник. Без парка — нет."""
    near = [(40.181 + 0.002 * k, 44.512) for k in range(4)]
    points = near + [(40.45, 44.80), (40.4501, 44.8001)]          # 5 — магазин B, 6 — магазин A рядом
    current = [_wk(1), _wk(2), _wk(1), _wk(2), _wk(2)]
    allowed = [[c] for c in current[:4]] + [[_wk(1), _wk(2)]]
    prob = _search_problem(points[:5], current, allowed, workdays=(1, 2), fleet_nodes=True)
    params = [sr.VisitParams(mu=0.0, var=0.0, p_low=0.0, p_year=1.0, kg=200.0)] * 5
    state = sr.State(prob, [line.current for line in prob.lines], params, change_penalty=300.0)
    fleet = _fleet_estimate(points, [200.0] * len(points))
    _with_fleet(state, fleet, [(j, 6, (True,) * 4) for j in (0, 7)])   # A: по понедельникам обеих недель
    own_before, fleet_before = state.own_total(), fleet.total
    sr.search(state, seconds=30)
    assert state.pattern[4] == _slots(_wk(1))
    assert fleet.total < fleet_before - 1000 and state.own_total() < own_before + 300 + 500
    assert fleet.consistency_error(state.fleet_entries()) < 1e-9
    alone = sr.State(_search_problem(points[:5], current, allowed, workdays=(1, 2)),
                     [line.current for line in prob.lines], params, change_penalty=300.0)
    sr.search(alone, seconds=30)
    assert alone.pattern[4] == _slots(_wk(2))                      # без парка переносить незачем


def _fake_full_fleet(snap, penalty):
    """Подмена полной модели парка: литры дня доставки = визиты + penalty за визит не в свой день ERP."""
    erp_days = {(a, c): {d for _, d in info.pattern} for a, cs in opt.plan_pairs(snap.plan).items()
                for c, info in cs.items()}

    def days(self, plan, models):
        out = Counter()
        for day in plan.days:
            for v in day.visits:
                key = fl.delivery_key(day.week, day.weekday, plan.cycle_weeks)
                out[key] += 1.0 + (penalty if day.weekday not in erp_days.get((day.agent_id, v.customer_id), ()) else 0.0)
        return dict(out)
    return days


def test_fleet_gate_reverts_moves_that_add_full_model_diesel(monkeypatch):
    """№33: полная модель говорит, что перенос визита на другой день добавляет дизель — проверка
    возвращает такие переносы на дни ERP, пока «стало» не станет не больше «было»; предложения и
    состояния поиска — по итогу. Частоты и обязательное не трогаются."""
    snap, bundle = _big_snapshot(n_agents=2, per_agent=60), _truck_bundle()
    plain = opt.run_optimization(snap, bundle, None, [], {})
    g0 = plain.result['fleet_gate']
    assert g0 is not None and g0['ok'] and g0['reason'] == opt.GATE_OK
    # проверка считает ровно то, что потом показывает итоговая оценка «стало»
    assert g0['liters_after'] == round(plain.after.fleet.week()['liters'], 1)
    assert g0['liters_before'] == round(plain.before.fleet.week()['liters'], 1)
    moves = sum(1 for m in plain.result['managers'] for ch in m['changes'] if ch['type'] in ('move', 'both'))
    assert moves > 0
    monkeypatch.setattr(opt._FullFleet, 'days', _fake_full_fleet(snap, 50.0))
    out = opt.run_optimization(snap, bundle, None, [], {})
    g = out.result['fleet_gate']
    assert g['reverted'] > 0 and g['liters_search'] > g['liters_before'] and g['ok']
    assert g['liters_after'] <= g['liters_before']
    assert out.cost_after == pytest.approx(sum(o.cost_after for o in out.managers) + out.fleet_after)
    for o in out.managers:
        for spec, final in zip(o.specs, o.final):
            assert final in spec.allowed and len(final) == 2 * spec.target     # частоты — как были
    left = sum(1 for m in out.result['managers'] for ch in m['changes'] if ch['type'] in ('move', 'both'))
    assert left < moves
    # ничего не растёт — проверка ничего не возвращает
    monkeypatch.setattr(opt._FullFleet, 'days', _fake_full_fleet(snap, 0.0))
    same = opt.run_optimization(snap, bundle, None, [], {})
    assert same.result['fleet_gate']['reverted'] == 0
    assert [o.final for o in same.managers] == [o.final for o in plain.managers]


def test_day_liters_equals_fleet_day_liters():
    """Проверка дизеля считает только литры года — ровно как fleet_day (те же пробы)."""
    rng = random.Random(1)
    draw = ev.Draw(0.6, ((30000.0, 200.0), (20000.0, 120.0)))
    vs = [fl.DeliveryVisit(i, rng.choice([1, 2, 3]), (40.1 + rng.random() * 0.2, 44.4 + rng.random() * 0.3),
                           draw, draw) for i in range(40)]
    trucks = [HOWO, FORD]
    liters = fl.fleet_day(1, 2, vs, DP_DEPOT, trucks, DP_NORMS, TN, 150000.0).liters
    assert fl.day_liters(vs, DP_DEPOT, trucks, DP_NORMS, TN) == liters


def test_fleet_gate_mode_b_reverts_transfers_that_add_diesel(monkeypatch):
    """№33 в режиме Б: полная модель говорит, что визит не «своего» менеджера (передача) добавляет дизель —
    передачи откатываются (или режим Б остаётся без них), «стало» не больше «было»."""
    _, _, cross = _overlap_rows()
    snap = _overlap_snapshot(debts={c: 0.0 for c in cross[1] + cross[2]})
    bundle = _truck_bundle(penalty_transfer=500)
    monkeypatch.setattr(opt._FullFleet, 'days', _fake_full_fleet(snap, 0.0))
    free = opt.run_optimization(snap, bundle, None, [], {'mode': 'transfer'}).result
    assert any(ch['type'] == 'transfer' for m in free['managers'] for ch in m['changes'])
    monkeypatch.setattr(opt._FullFleet, 'days', _fake_full_fleet(snap, 50.0))
    res = opt.run_optimization(snap, bundle, None, [], {'mode': 'transfer'}).result
    g = res['fleet_gate']
    assert g['ok'] and g['liters_after'] <= g['liters_before']
    assert not any(ch['type'] == 'transfer' for m in res['managers'] for ch in m['changes'])
    out = opt.run_optimization(snap, bundle, None, [], {'mode': 'transfer'})
    # стоимость режима Б — итога после проверки (передач нет — Σ менеджеров + парк)
    assert out.cost_after == pytest.approx(sum(o.cost_after for o in out.managers) + out.fleet_after)


def test_fleet_gate_reports_honestly_when_diesel_cannot_be_held(monkeypatch):
    """Полная модель «стало» всегда хуже (подмена) — проверка откатывает что может, но честно сообщает:
    ok = False и причину; расчёт не падает."""
    snap, bundle = _big_snapshot(n_agents=2, per_agent=60), _truck_bundle()

    def days(self, plan, models):
        return {(1, 1): 100.0 + (1000.0 if plan.cycle_weeks == 2 else 0.0)}
    monkeypatch.setattr(opt._FullFleet, 'days', days)
    g = opt.run_optimization(snap, bundle, None, [], {}).result['fleet_gate']
    assert not g['ok'] and g['reason'] in (opt.GATE_NO_CANDIDATES, opt.GATE_ATTEMPTS)
    assert g['liters_after'] > g['liters_before']


def test_gate_target_is_closest_allowed_to_erp_days():
    spec = opt.PairSpec(_wk(7), 1.0, 'sales', tuple(pt.allowed_patterns(_wk(7), 1.0, SIX)), False, frozenset())
    assert opt.gate_target(spec, SIX) == _wk(6)                           # воскресенье — на субботе
    spec = opt.PairSpec(_wk(1, 4), 1.0, 'sales', tuple(pt.allowed_patterns(_wk(1, 4), 1.0, SIX)), False, frozenset())
    assert opt.gate_target(spec, SIX) in (_wk(1), _wk(4))                  # реже — из своих дней


def test_optimizer_with_fleet_deterministic_and_not_worse():
    snap, bundle = _big_snapshot(n_agents=2, per_agent=60), _truck_bundle()
    a = opt.run_optimization(snap, bundle, None, [], {})
    b = opt.run_optimization(snap, bundle, None, [], {})
    assert [o.final for o in a.managers] == [o.final for o in b.managers]
    assert a.result['truck_costs'] is True and a.fleet_before is not None and a.fleet_after is not None
    assert a.cost_after <= a.cost_before + 1e-6                      # компания с парком — не хуже
    assert a.cost_after == pytest.approx(sum(o.cost_after for o in a.managers) + a.fleet_after)
    tb, ta = a.result['before'], a.result['after']
    assert tb['truck_liters_week'] > 0 and ta['truck_liters_week'] > 0 and ta['trips_week'] > 0
    effects = [ch['effect']['truck_km_week'] for m in a.result['managers'] for ch in m['changes']]
    assert effects and all(e is not None for e in effects)
    t = opt.run_optimization(snap, bundle, None, [], {'mode': 'transfer'})
    assert t.transfer.cost_end <= t.transfer.cost_start + 1e-6
    assert t.transfer.cost_start == pytest.approx(a.cost_after, rel=1e-9)   # старт Б = итог А (с парком)
    moved = [ch for m in t.result['managers'] for ch in m['changes'] if ch['type'] == 'transfer']
    assert all(ch['effect']['truck_km_week'] is not None and ch['effect_from']['truck_km_week'] is None
               for ch in moved)
    no_fleet = opt.run_optimization(snap, _bundle(), None, [], {})
    assert no_fleet.result['truck_costs'] is False and no_fleet.fleet_after is None
    assert all(ch['effect']['truck_km_week'] is None for m in no_fleet.result['managers'] for ch in m['changes'])


def test_store_fleet_settings_defaults_and_validation(store):
    s = store.load().settings
    assert (s['truck_work_start'], s['truck_work_end'], s['unload_min_per_stop'], s['unload_min_per_tonne']) == \
        ('09:00', '18:00', 8, 6)
    for key, bad in (('truck_work_end', '08:00'), ('truck_work_start', '9:00'), ('unload_min_per_stop', -1),
                     ('unload_min_per_tonne', 500)):
        _, errors = st.validate_payload({'settings': {key: bad}}, store.load(), REF)
        assert f'settings.{key}' in errors, key
    _save(store, {'settings': {'truck_work_start': '08:00', 'truck_work_end': '20:00', 'unload_min_per_stop': 5}})
    tn = fl.TruckNorms.from_settings(store.load().settings)
    assert (tn.work_minutes, tn.unload(1000.0)) == (720.0, 11.0)


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы маршрутов владельца')
def test_store_fleet_keeps_owner_trucks_on_copy(tmp_path):
    """Новые нормы машин — значения по умолчанию, машины владельца (тоннаж, расход, «чей менеджер» для
    справки, «активна») на КОПИИ базы — как были; тестовые машины пишутся только в копию."""
    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro&immutable=1', uri=True)) as conn:
        trucks = sorted(conn.execute('SELECT car_code, capacity_kg, fuel_l_per_100km, agent_id, active '
                                     'FROM trucks').fetchall())
    copy = tmp_path / 'owner.db'
    copy.write_bytes(OWNER_DB.read_bytes())
    store = st.Store(str(copy))
    bundle = store.load()
    assert bundle.settings['truck_work_start'] == '09:00' and st.SCHEMA_VERSION >= 6
    assert sorted((t.car_code, t.capacity_kg, t.fuel_l_per_100km, t.agent_id, t.active)
                  for t in bundle.trucks.values()) == [(*row[:4], None if row[4] is None else bool(row[4])) for row in trucks]
    if trucks:
        code = trucks[0][0]
        ref = st.RefData(frozenset(bundle.trucks), frozenset(), frozenset(bundle.settings['chain_groups']))
        changes, errors = st.validate_payload({'trucks': [{'car_code': code, 'capacity_kg': 3500,
                                                           'fuel_l_per_100km': 16}]}, bundle, ref)
        assert errors == {}
        store.save(changes, 'qa')
        after = store.load().trucks
        assert (after[code].capacity_kg, after[code].fuel_l_per_100km) == (3500, 16)
        assert after[code].agent_id == trucks[0][3]                    # справочное поле не теряется
        assert all(after[c] == bundle.trucks[c] for c in bundle.trucks if c != code)


def test_fleet_estimate_eval_resume_equals_split_from_scratch():
    """Оценка хода в парке продолжает нарезку с места изменения и выходит, когда рейсы совпали с прежними:
    км и минуты — как нарезка нового тура с нуля (тяжёлые заказы, разный тоннаж и длина дня машины,
    удаление и вставка)."""
    rng = random.Random(1)
    checked = 0
    for _ in range(200):
        n = rng.randint(1, 40)
        pts = [(40.0 + rng.random() * 0.5, 44.2 + rng.random() * 0.6) for _ in range(n)]
        kg = [rng.choice((50.0, 300.0, 900.0, 1500.0, 4000.0)) for _ in range(n)]
        fleet = _fleet_estimate(pts, kg, capacity=rng.choice((1000.0, 3000.0, 20000.0)),
                                trip_minutes=rng.choice((90.0, 540.0)))   # 90 мин — рейсы режет и время
        fleet.build([(0, rng.randint(1, n), (True,) * 4) for _ in range(rng.randint(0, 30))])
        t = fleet.tours[0][0]
        for _ in range(10):
            rem = rng.choice(t[1:]) if len(t) > 1 and rng.random() < 0.7 else 0
            ins = rng.randint(1, n) if rng.random() < 0.7 else 0
            if not (rem or ins):
                continue
            r_pos, i_pos, km, mins = fleet.eval(0, 0, rem, ins)
            new = list(t)
            if r_pos >= 0:
                del new[r_pos]
            if i_pos >= 0:
                new.insert(i_pos, ins)
            assert (km, mins) == pytest.approx(fleet.split(new), rel=1e-12, abs=1e-9)
            assert fleet.eval(0, 0, rem, ins) == (r_pos, i_pos, km, mins)   # из памяти — то же
            checked += 1
    assert checked > 1000


def test_fleet_sunday_orders_share_saturday_delivery_tour():
    """Заказы сб и вс одной недели везут вместе в пн: у слота вс тот же тур парка, что у сб. Если вс —
    рабочий день и ход двигает визиты между сб и вс, оба дня оцениваются по одному туру по очереди —
    оценка хода = факт, туры = визиты (и после 400 случайных ходов)."""
    rng = random.Random(21)
    seven = range(1, 8)
    points = [(40.05 + rng.random() * 0.3, 44.35 + rng.random() * 0.35) for _ in range(24)]
    freqs = [rng.choice((0.5, 1.0, 2.0)) for _ in points]
    allowed = [pt.standard_patterns(f, seven) for f in freqs]
    current = [rng.choice(a) for a in allowed]
    params = [sr.VisitParams(mu=30000.0, var=1e8, p_low=0.5, p_year=rng.random(), kg=300.0) for _ in points]
    prob = _search_problem(points, current, allowed, workdays=seven, fleet_nodes=True)
    state = sr.State(prob, [line.current for line in prob.lines], params, change_penalty=300.0)
    fleet = _fleet_estimate(points, [300.0] * len(points), capacity=1500.0, trip_minutes=240.0)
    _with_fleet(state, fleet)
    sat, sun = sr.slot_of_day(1, 6), sr.slot_of_day(1, 7)
    assert fleet.group[sun] == sat and fleet.group[sat] == sat
    assert all(t == [0] for t in fleet.tours[sun]) and fleet.consistency_error(state.fleet_entries()) < 1e-9
    moved_pairs = 0
    for _ in range(400):
        i = rng.randrange(len(points))
        alts = [p for p in prob.lines[i].allowed if p != state.pattern[i]]
        if not alts:
            continue
        new = rng.choice(alts)
        days = set(state.pattern[i]) ^ set(new)
        moved_pairs += any(fleet.group[j] == fleet.group[k] for j in days for k in days if j != k)
        before = state.total
        delta, move = state.eval_relocate(i, new)
        state.apply(move)
        assert state.total - before == pytest.approx(delta, abs=1e-6)
    assert moved_pairs > 5                                           # ходы сб ↔ вс были
    assert state.consistency_error() < 1e-6 and fleet.consistency_error(state.fleet_entries()) < 1e-6
    # эффект: два визита уходят с сб и вс (один тур), третий приходит на сб — туры потом как были
    gone = [e for e in state.fleet_entries() if fleet.group[e[0]] == sat][:2]
    snap = fleet.snapshot()
    km = fleet.effect_km(gone, [(sun, 1, (True,) * 4)])
    assert fleet.snapshot()[:5] == snap[:5] and len(gone) == 2 and km != 0.0


def test_fleet_split_cuts_trip_by_truck_day():
    """Рейс не длиннее рабочего дня машины (как в точной оценке): две далёкие точки в разные стороны
    при коротком дне — два рейса, при длинном — один."""
    pts = [(40.45, 44.60), (40.46, 44.61)]
    for minutes, trips in ((100.0, 2), (1000.0, 1)):
        fleet = _fleet_estimate(pts, [100.0, 100.0], capacity=3000.0, trip_minutes=minutes)
        fleet.build([(0, 1, (True,) * 4), (0, 2, (True,) * 4)])
        d = fleet.d
        one = d[0][1] + d[1][2] + d[2][0]
        two = 2 * d[0][1] + 2 * d[0][2]
        km, _ = fleet.split(fleet.tours[0][0])
        assert km == pytest.approx(one if trips == 1 else two)


# ============================== план развоза (dispatch-plan.md) ==============================

from route_optimizer import dispatch as dp  # noqa: E402

DP_DAY = date(2026, 10, 1)                 # четверг: везём заказы среды 30.09
DP_DEPOT = (40.19462, 44.6004)
DP_NORMS = ev.Norms.from_settings(st.DEFAULT_SETTINGS)
SIX = [1, 2, 3, 4, 5, 6]


def _isn(i):
    return str(uuid.UUID(int=i)).upper()


def _dorder(i, cid, kg, *, day=date(2026, 9, 30), agent=1, van=0, shipped=None, rev=10000.0, car=''):
    return dp.DispatchOrder(_isn(i), f'N{i}', day, cid, agent, car, rev, kg, shipped, van)


def _dp_ctx(trucks=(HOWO, FORD), tn=TN):
    return dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in trucks}, DP_NORMS, tn, 9 * 60)


def _coord_of(points):
    return lambda c: geo.Coord(*points[c], 'erp') if points.get(c) else geo.NO_COORD


def _dp_stops(spec, excluded=()):
    """spec: (клиент, точка | None, кг); заказ i — i-я строка."""
    orders = [_dorder(i + 1, cid, kg) for i, (cid, _, kg) in enumerate(spec)]
    points = {cid: p for cid, p, _ in spec}
    return dp.build_stops([o for o in orders if o.isn not in excluded], _coord_of(points)), orders


def _info(s):
    return {'customer_id': s.customer_id}


def _trip_of(view, cid):
    return [t for tr in view['trucks'] for t in tr['trips'] if any(s['customer_id'] == cid for s in t['stops'])]


def test_dispatch_days_and_order_window():
    sat, mon, tue = date(2026, 10, 3), date(2026, 10, 5), date(2026, 10, 6)
    assert dp.next_workday(date(2026, 10, 1), SIX) == date(2026, 10, 2)
    assert dp.next_workday(sat, SIX) == mon                                       # субботние — в понедельник
    assert dp.order_window(tue, SIX) == (mon, tue)                                # заказы понедельника
    assert dp.order_window(mon, SIX) == (sat, mon)                                # сб и вс → пн
    assert dp.order_window(mon, [1, 2, 3, 4, 5]) == (date(2026, 10, 2), mon)      # пятидневка: пт, сб, вс
    assert dp.backlog_since(sat, SIX) == date(2026, 10, 1)                        # ещё 2 рабочих дня раньше окна
    assert dp.previous_workday(mon, []) == date(2026, 10, 4)                      # без рабочих дней — любой


def test_dispatch_selection_shipped_cancelled_self_and_backlog():
    since = date(2026, 9, 30)
    orders = [
        _dorder(1, 101, 100.0),                                            # не отгружен — везём
        _dorder(2, 102, 50.0, shipped=date(2026, 9, 30)),                   # отгружен в тот же день — уже нет
        _dorder(3, 103, 70.0, shipped=DP_DAY),                              # прошедшая дата: везли в D — в отборе
        _dorder(4, 104, 80.0, shipped=date(2026, 10, 2)),                   # отгрузили позже — тоже в отборе
        _dorder(5, 105, 900.0, agent=7, van=7),                             # менеджер развозит сам
        _dorder(6, 106, 60.0, day=date(2026, 9, 28)),                       # прошлых дней, не отгружен
        _dorder(7, 107, 60.0, day=date(2026, 9, 28), shipped=date(2026, 9, 29)),
    ]
    sel = dp.to_deliver(orders, DP_DAY, since)
    assert [o.customer_id for o in sel.main] == [101, 103, 104]
    assert [o.customer_id for o in sel.backlog] == [106]
    assert [o.customer_id for o in sel.self_delivery] == [105]
    assert sel.shipped_before == 1
    stops = dp.build_stops(sel.main + [_dorder(8, 101, 25.0, agent=2)],
                           _coord_of({101: (40.18, 44.5), 103: (40.2, 44.52)}))
    s101 = stops[0]
    assert (s101.customer_id, s101.kg, len(s101.orders), s101.agent_id) == (101, 125.0, 2, 1)  # главный — самый тяжёлый
    assert [s.coord_source for s in stops] == ['erp', 'erp', 'none']


def test_route_day_and_route_trip_keep_order_and_items():
    pts = [(40.20, 44.62), (40.21, 44.63), (40.17, 44.45), (40.16, 44.44)]
    trips = fl.route_day(pts, [300.0] * 4, [1.0] * 4, DP_DEPOT, [FORD], DP_NORMS, TN)
    assert sorted(i for t in trips for i in t.items) == [0, 1, 2, 3]
    assert all(t.stops == len(t.items) for t in trips)
    heavy = fl.route_day([pts[0]], [12000.0], [1.0], DP_DEPOT, [HOWO, FORD], DP_NORMS, TN)
    assert [t.items for t in heavy] == [(0,), (0,)] and all(t.kg == pytest.approx(6000.0) for t in heavy)
    # 2-opt: «восьмёрка» по квадрату распутывается, длина не растёт
    sq = [(40.20, 44.60), (40.22, 44.62), (40.22, 44.60), (40.20, 44.62)]
    seq, km, minutes = fl.route_trip(sq, [100.0] * 4, DP_DEPOT, DP_NORMS, TN)
    _, km_raw, _ = fl.route_trip(sq, [100.0] * 4, DP_DEPOT, DP_NORMS, TN, reorder=False)
    assert sorted(seq) == [0, 1, 2, 3] and km < km_raw - 0.5
    assert minutes > 4 * TN.unload(100.0)
    assert fl.route_trip([], [], DP_DEPOT, DP_NORMS, TN) == ([], 0.0, 0.0)
    # закреплённые рейсы уже заняли машину: при used = рабочий день FORD новый рейс берёт HOWO
    t2 = fl.route_day(pts[:1], [300.0], [1.0], DP_DEPOT, [HOWO, FORD], DP_NORMS, TN, used={FORD.car_code: 540.0})
    assert [t.truck for t in t2] == [HOWO.car_code]


def test_dispatch_build_capacity_time_all_stops_deterministic():
    rng = random.Random(7)
    spec = [(100 + i, (40.15 + rng.random() * 0.08, 44.45 + rng.random() * 0.15), rng.choice([100.0, 300.0, 600.0]))
            for i in range(25)]
    spec += [(200, (40.23, 44.55), 12000.0), (201, None, 50.0)]
    stops, _ = _dp_stops(spec)
    howo2 = fl.FleetTruck('123AV61', 'HOWO', 10000.0, 30.0)
    ctx = _dp_ctx((HOWO, howo2, FORD))
    draft = dp.build(ctx, stops, None, ['991AT61', '333DO33', '123AV61', 'NOPE'], 'now')
    assert draft.trucks == ['123AV61', '333DO33', '991AT61']          # неизвестная машина отброшена
    assert dp.build(ctx, list(reversed(stops)), None, ['333DO33', '123AV61', '991AT61'], 'now').to_json()['trips'] == \
        draft.to_json()['trips']                                       # детерминизм
    view = dp.plan_view(ctx, stops, draft, _info)
    assert view['unassigned'] == [] and view['overflow']['trips'] == 0
    caps = {t.car_code: t.capacity_kg for t in ctx.trucks.values()}
    trips = [t for tr in view['trucks'] for t in tr['trips']]
    assert all(t['kg'] <= caps[t['truck']] + 0.5 and not t['over_time'] for t in trips)
    for tr in view['trucks']:                                           # время машины — последовательно, в пределах дня
        assert tr['minutes'] <= TN.work_minutes and tr['trips'][0]['depart'] == '09:00'
        assert all(a['return'] == b['depart'] for a, b in zip(tr['trips'], tr['trips'][1:]))
    heavy = _trip_of(view, 200)                                         # 12 т > 10 т — две поездки по 6 т
    assert len(heavy) == 2 and all(s['kg'] == 6000 and s['share'] == 2
                                  for t in heavy for s in t['stops'] if s['customer_id'] == 200)
    kg = Counter()
    for t in trips:
        for s in t['stops']:
            kg[s['customer_id']] += s['kg']
    assert kg == Counter({s.customer_id: round(s.kg) for s in stops if s.point is not None})
    assert 201 not in kg                                                # без точки — не в рейсах
    assert view['summary']['stops'] == 26 and view['summary']['km'] == pytest.approx(
        sum(t['km'] for t in trips), abs=0.05)


EAST = [(101, (40.200, 44.640), 200.0), (102, (40.205, 44.650), 200.0), (103, (40.210, 44.645), 200.0)]
WEST = [(104, (40.170, 44.450), 200.0), (105, (40.165, 44.440), 200.0), (106, (40.175, 44.445), 200.0)]


def test_dispatch_edits_move_pin_exclude_include_and_rebuild():
    ctx = _dp_ctx()
    stops, orders = _dp_stops(EAST + WEST)
    draft = dp.build(ctx, stops, None, ['991AT61', '333DO33'], 'now')
    km0 = dp.plan_view(ctx, stops, draft, _info)['summary']['km']
    ids = {o.isn for o in orders}
    src = next(t for t in draft.trips if 104 in t.stops)
    # перенести 104 в новый рейс HOWO — лишняя поездка туда и обратно
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'move', 'customer_id': 104, 'from_trip': src.id,
                                              'to_trip': None, 'truck': '991AT61'}, ids)
    view = dp.plan_view(ctx, stops, draft, _info)
    solo = _trip_of(view, 104)[0]
    assert solo['truck'] == '991AT61' and [s['customer_id'] for s in solo['stops']] == [104]
    assert view['summary']['km'] > km0
    # закрепить одиночный рейс; новая сборка его не трогает, 104 больше никуда не кладётся
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'pin', 'trip': solo['id'], 'truck': '991AT61'}, ids)
    rebuilt = dp.build(ctx, stops, draft, ['991AT61', '333DO33'], 'now2')
    pinned = [t for t in rebuilt.trips if t.pinned]
    assert [(t.id, t.truck, t.stops) for t in pinned] == [(solo['id'], '991AT61', [104])]
    assert sum(t.stops.count(104) for t in rebuilt.trips) == 1
    assert rebuilt.next_id > max(t.id for t in rebuilt.trips)
    # вернуть 104 в рейс запада: вставка + 2-opt; пустой рейс исчезает, км — как до переноса
    west = next(t for t in rebuilt.trips if 105 in t.stops)
    back = dp.apply_edit(ctx, stops, rebuilt, {'action': 'move', 'customer_id': 104, 'from_trip': solo['id'],
                                               'to_trip': west.id}, ids)
    assert solo['id'] not in {t.id for t in back.trips} and 104 in west.stops
    assert dp.plan_view(ctx, stops, back, _info)['summary']['km'] == pytest.approx(km0, abs=0.2)
    # закрепить рейс за другой машиной — рейс уходит к ней последним
    east = next(t for t in back.trips if 101 in t.stops)
    other = '991AT61' if east.truck == '333DO33' else '333DO33'
    back = dp.apply_edit(ctx, stops, back, {'action': 'pin', 'trip': east.id, 'truck': other}, ids)
    assert back.trips[-1] is east and (east.truck, east.pinned) == (other, True)
    back = dp.apply_edit(ctx, stops, back, {'action': 'unpin', 'trip': east.id}, ids)
    assert east.pinned is False
    # «не везём сегодня»: заказ 102 исключён — точки нет в рейсах; вернули — точка «ещё не в рейсах»
    isn102 = orders[1].isn
    back = dp.apply_edit(ctx, stops, back, {'action': 'exclude', 'order': isn102.lower()}, ids)
    assert back.excluded == {isn102}
    stops_ex, _ = _dp_stops(EAST + WEST, back.excluded)
    assert _trip_of(dp.plan_view(ctx, stops_ex, back, _info), 102) == []
    back = dp.apply_edit(ctx, stops_ex, back, {'action': 'include', 'order': isn102}, ids)
    assert back.excluded == set()
    view = dp.plan_view(ctx, stops, back, _info)
    assert [u['customer_id'] for u in view['unassigned']] == [102]
    back = dp.apply_edit(ctx, stops, back, {'action': 'move', 'customer_id': 102, 'from_trip': None,
                                            'to_trip': east.id}, ids)
    assert 102 in east.stops and dp.plan_view(ctx, stops, back, _info)['unassigned'] == []
    # заказы прошлых дней: добавить в развоз / убрать
    old = _isn(99)
    back = dp.apply_edit(ctx, stops, back, {'action': 'include', 'order': old}, ids, {old})
    assert back.added == {old}
    back = dp.apply_edit(ctx, stops, back, {'action': 'exclude', 'order': old}, ids, {old})
    assert back.added == set()


@pytest.mark.parametrize('edit, text', [
    ({'action': 'move', 'customer_id': 999, 'from_trip': None}, 'Точка не найдена'),
    ({'action': 'move', 'customer_id': 101, 'from_trip': None, 'to_trip': None, 'truck': '991AT61'}, 'уже в рейсе'),
    ({'action': 'move', 'customer_id': 101, 'from_trip': 12345}, 'Рейс не найден'),
    ({'action': 'move', 'customer_id': 101, 'from_trip': 1, 'truck': 'XXX'}, 'не работает'),
    ({'action': 'pin', 'trip': 1, 'truck': 'XXX'}, 'не работает'),
    ({'action': 'exclude', 'order': 'nope'}, 'Заказ не найден'),
    ({'action': 'drop'}, 'Неизвестное действие'),
])
def test_dispatch_edit_errors(edit, text):
    ctx = _dp_ctx()
    stops, orders = _dp_stops(EAST)
    draft = dp.build(ctx, stops, None, ['333DO33', '991AT61'], 'now')
    assert [t.id for t in draft.trips] == [1]
    with pytest.raises(dp.DispatchError, match=text):
        dp.apply_edit(ctx, stops, draft, edit, {o.isn for o in orders})


def test_dispatch_build_never_plans_past_end_of_day():
    """Живой случай 30.09: логист выбрал одну машину, а груза — на несколько; рейсы шли до 04:06 следующих
    суток. Теперь за конец рабочего дня рейсы не планируются: что машина не успевает — «не поместились»
    (no_room), время рейсов честное. Модель парка (overflow=True) по-прежнему везёт всё — считает нехватку."""
    rng = random.Random(3)
    spec = [(100 + i, (40.05 + rng.random() * 0.3, 44.30 + rng.random() * 0.4), rng.choice([300.0, 600.0, 900.0]))
            for i in range(60)]
    spec += [(300, (40.10, 44.35), 7000.0)]                            # 7 т > 3,5 т FORD — две поездки
    stops, _ = _dp_stops(spec)
    ctx = _dp_ctx((FORD,))
    draft = dp.build(ctx, stops, None, [FORD.car_code], 'now')
    view = dp.plan_view(ctx, stops, draft, _info)
    [truck] = view['trucks']
    assert truck['minutes'] <= TN.work_minutes and not any(t['over_time'] for t in truck['trips'])
    assert '(+' not in truck['return']
    left = {u['customer_id'] for u in view['unassigned']}
    assert left and left == draft.no_room and all(u['no_room'] for u in view['unassigned'])
    placed = {s['customer_id'] for t in truck['trips'] for s in t['stops']}
    assert placed | left == {cid for cid, p, _ in spec} and not placed & left
    heavy = _trip_of(view, 300)                                        # тяжёлый — либо целиком, либо нигде
    assert len(heavy) in (0, 2) and (len(heavy) == 2) == (300 not in left)
    assert dp.Draft.from_json(json.loads(json.dumps(draft.to_json()))).no_room == draft.no_room
    everything = fl.route_day([s.point for s in stops], [s.kg for s in stops], [s.revenue for s in stops],
                              DP_DEPOT, [FORD], DP_NORMS, TN)
    assert sorted({i for t in everything for i in t.items}) == list(range(len(stops)))
    assert dp._hhmm(17 * 60 + 32) == '17:32' and dp._hhmm(28 * 60 + 6) == '04:06 (+1)'


def test_dispatch_day_end_cuts_trip_and_drops_heavy_whole():
    """Ревью: рейс, который целиком не успевает, режется — начало по ходу объезда везёт машина, что ещё
    успевает; тяжёлый заказ, у которого влезли не все поездки, снимается целиком, а его время не
    пропадает — мелкие заказы рядом едут."""
    near = [(40.20 + 0.002 * i, 44.61 + 0.002 * i) for i in range(10)]
    whole = fl.route_day(near, [100.0] * 10, [1.0] * 10, DP_DEPOT, [FORD], DP_NORMS, TN, overflow=False)
    [trip] = whole
    left = trip.minutes - 1.0                                         # на минуту меньше, чем нужно рейсу
    part = fl.route_day(near, [100.0] * 10, [1.0] * 10, DP_DEPOT, [FORD], DP_NORMS, TN,
                        used={FORD.car_code: TN.work_minutes - left}, overflow=False)
    assert 0 < sum(t.stops for t in part) < 10 and sum(t.minutes for t in part) <= left + 1e-6
    # 7 т = две поездки FORD по 3,5 т, а времени — на одну: тяжёлый не едет вовсе, мелкие — едут
    pts = [(40.20, 44.62)] + near[:6]
    kgs = [7000.0] + [200.0] * 6
    one_way = fl.route_day(pts[:1], [3500.0], [1.0], DP_DEPOT, [FORD], DP_NORMS, TN)[0].minutes
    used = {FORD.car_code: TN.work_minutes - one_way * 1.5}
    trips = fl.route_day(pts, kgs, [1.0] * 7, DP_DEPOT, [FORD], DP_NORMS, TN, used=used, overflow=False)
    items = [i for t in trips for i in t.items]
    assert 0 not in items and items and sum(t.minutes for t in trips) <= one_way * 1.5 + 1e-6
    # дальняя точка первой по ходу объезда не мешает ближней: ближняя едет, дальняя — нет
    far, close = (40.40, 44.95), (40.196, 44.602)
    t_far = fl.route_day([far], [100.0], [1.0], DP_DEPOT, [FORD], DP_NORMS, TN)[0].minutes
    t_close = fl.route_day([close], [100.0], [1.0], DP_DEPOT, [FORD], DP_NORMS, TN)[0].minutes
    assert t_close * 2 < t_far
    used = {FORD.car_code: TN.work_minutes - (t_close + t_far) / 2}
    for pts2 in ([far, close], [close, far]):
        got = fl.route_day(pts2, [100.0, 100.0], [1.0, 1.0], DP_DEPOT, [FORD], DP_NORMS, TN, used=used, overflow=False)
        assert [pts2[i] for t in got for i in t.items] == [close], pts2


def test_dispatch_overtime_and_baseline_same_stores():
    """«Везти после конца дня» (№32): не поместившиеся — по машинам дня с переработкой, отметка дня;
    рейсы дня не меняются. Сравнение «как обычно» — по тем же магазинам, что в рейсах."""
    rng = random.Random(3)
    spec = [(100 + i, (40.05 + rng.random() * 0.3, 44.30 + rng.random() * 0.4), rng.choice([300.0, 600.0, 900.0]))
            for i in range(60)]
    stops, _ = _dp_stops(spec)
    ctx = _dp_ctx((FORD,))
    draft = dp.build(ctx, stops, None, [FORD.car_code], 'now')
    assert draft.no_room and not draft.overtime
    day_trips = [(t.id, list(t.stops)) for t in draft.trips]
    base = dp.baseline(ctx, stops, draft, {1: (FORD.car_code,)})
    view = dp.plan_view(ctx, stops, draft, _info)
    assert base['km'] == pytest.approx(view['summary']['km'], rel=0.5)  # те же магазины, а не все 60
    assert not dp.runs_late(ctx, stops, draft)
    after = dp.overtime(ctx, stops, dp.Draft.from_json(draft.to_json()))
    assert [(t.id, t.stops) for t in after.trips[:len(day_trips)]] == day_trips
    assert dp.runs_late(ctx, stops, after) and after.no_room == set()   # предела нет — везут всё
    v2 = dp.plan_view(ctx, stops, after, _info)
    assert v2['unassigned'] == [] and after.overtime_ok and v2['trucks'][0]['late']
    assert v2['trucks'][0]['over_time']                                 # предела нет (ctx без него) — окно дня
    after.overtime = True
    assert dp.Draft.from_json(json.loads(json.dumps(after.to_json()))).overtime is True
    # предел форс-мажора (truck_overtime_end, по умолчанию 20:00): позже машина не работает, остальное — «не поместились»
    capped = replace(ctx, overtime_minutes=TN.work_minutes + 120.0)
    late = dp.overtime(capped, stops, dp.Draft.from_json(draft.to_json()))
    v3 = dp.plan_view(capped, stops, late, _info)
    t3 = v3['trucks'][0]
    assert t3['minutes'] <= TN.work_minutes + 120.0 + 1e-6 and t3['late'] and not t3['over_time']  # принятая — не ошибка
    assert v3['overflow']['trips'] == 0
    assert dp.plan_view(capped, stops, replace(late, overtime_ok=False), _info)['trucks'][0]['over_time']  # не принята — ошибка
    assert late.no_room and all(u['no_room'] for u in v3['unassigned'])
    assert len(late.trips) > len(draft.trips)
    again = dp.overtime(ctx, stops, after)                             # повтор — ничего не добавляет
    assert len(again.trips) == len(after.trips)
    # перенос «не поместившегося» в рейс вручную — он больше не «не поместился»
    d3 = dp.Draft.from_json(draft.to_json())
    cid = min(d3.no_room)
    dp.apply_edit(ctx, stops, d3, {'action': 'move', 'customer_id': cid, 'from_trip': None,
                                   'to_trip': d3.trips[0].id, 'truck': None}, set())
    assert cid not in d3.no_room


def test_dispatch_overtime_goes_to_truck_that_finishes_first_and_settings_load():
    """Переработка — машине, что раньше закончит (часы сверх дня короче), а не самой экономичной;
    база без ключа предела, где день машин кончается позже 20:00, — открывается (предел = конец дня)."""
    cheap = fl.FleetTruck('C1', 'cheap', 5000.0, 10.0)
    dear = fl.FleetTruck('D1', 'dear', 5000.0, 30.0)
    pt_ = [(40.21, 44.63)]
    used = {'C1': 600.0, 'D1': 300.0}
    [t] = fl.route_day(pt_, [100.0], [1.0], DP_DEPOT, [cheap, dear], DP_NORMS,
                       replace(TN, work_minutes=720.0), used, overflow=False, earliest=True)
    assert t.truck == 'D1'
    [t] = fl.route_day(pt_, [100.0], [1.0], DP_DEPOT, [cheap, dear], DP_NORMS,
                       replace(TN, work_minutes=720.0), used, overflow=False)
    assert t.truck == 'C1'


def test_store_overtime_end_default_follows_late_work_end(tmp_path):
    path = str(tmp_path / 'r.db')
    s = st.Store(path)
    s.load()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('truck_work_end', '\"21:00\"')")
        conn.commit()
    assert s.load().settings['truck_overtime_end'] == '21:00'
    changes, errors = st.validate_payload({'settings': {'truck_overtime_end': '20:00'}}, s.load(), REF)
    assert 'settings.truck_overtime_end' in errors


def test_dispatch_poor_trip_and_defer_to_next_day():
    """№25: рейс дешевле min_trip_revenue — «бедный»; «Везти завтра» снимает его заказы с сегодняшнего
    развоза (excluded) и помечает перенос (deferred); тяжёлый заказ в двух рейсах переносится целиком;
    «вернуть» заказ — перенос снимается."""
    spec = EAST + WEST + [(200, (40.23, 44.55), 12000.0)]
    stops, orders = _dp_stops(spec)
    ctx = replace(_dp_ctx((HOWO, FORD)), min_trip_revenue=150000.0)
    draft = dp.build(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now')
    view = dp.plan_view(ctx, stops, draft, _info)
    trips = [t for tr in view['trucks'] for t in tr['trips']]
    assert all(t['poor'] for t in trips) and view['summary']['poor_trips'] == len(trips)   # заказы по 10 000 драм
    assert not any(t['poor'] for tr in dp.plan_view(_dp_ctx((HOWO, FORD)), stops, draft, _info)['trucks']
                   for t in tr['trips'])                                    # порог 0 — бедных нет
    ids = {o.isn for o in orders}
    heavy = [t for t in draft.trips if 200 in t.stops]
    assert len(heavy) == 2
    d = dp.apply_edit(ctx, stops, draft, {'action': 'defer_trip', 'trip': heavy[0].id}, ids)
    isn200 = {o.isn for o in orders if o.customer_id == 200}
    assert isn200 <= d.excluded and isn200 <= d.deferred
    assert all(200 not in t.stops for t in d.trips)                         # и вторая поездка — тоже
    assert dp.Draft.from_json(json.loads(json.dumps(d.to_json()))).deferred == d.deferred
    one = next(iter(isn200))
    d = dp.apply_edit(ctx, stops, d, {'action': 'include', 'order': one}, ids)
    assert one not in d.deferred and one not in d.excluded


def test_dispatch_split_heavy_order_is_not_poor():
    """Тяжёлый заказ на две поездки: на поездку — половина выручки, но перенос ничего не объединит —
    «бедный» считается по полной выручке точки."""
    orders = [_dorder(1, 200, 12000.0, rev=20000.0)]
    stops = dp.build_stops(orders, _coord_of({200: (40.23, 44.55)}))
    ctx = replace(_dp_ctx((HOWO, FORD)), min_trip_revenue=15000.0)
    draft = dp.build(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now')
    trips = [t for tr in dp.plan_view(ctx, stops, draft, _info)['trucks'] for t in tr['trips']]
    assert len(trips) == 2 and all(t['revenue'] == 10000 for t in trips) and not any(t['poor'] for t in trips)


def test_dispatch_carried_from_non_workday_draft(client):
    """Перенос, сделанный в плане нерабочего дня (вс), доходит до следующего дня доставки (пн)."""
    import route_optimizer.views as rv
    state = client.application.extensions['route_optimizer']
    isn = _isn(77)
    state.store.save_dispatch('2026-10-04', dp.Draft(deferred={isn}).to_json(), 'qa')     # воскресенье
    backlog = [_dorder(77, 101, 100.0, day=date(2026, 10, 2))]
    assert rv._carried(state, date(2026, 10, 5), SIX, backlog) == {isn}
    assert rv._carried(state, date(2026, 10, 5), SIX, []) == set()          # уже отгружен — не несём


def test_dispatch_draft_json_roundtrip_and_garbage():
    d = dp.Draft(['CAR1'], {_isn(1)}, {_isn(2)}, [dp.DraftTrip(3, 'CAR1', [101, 102], True)], 4, 'x')
    assert dp.Draft.from_json(json.loads(json.dumps(d.to_json()))) == d
    junk = dp.Draft.from_json({'trucks': ['A', 5], 'excluded': ['bad', _isn(1)], 'next_id': 'x',
                               'trips': [{'id': 2, 'truck': 'A', 'stops': [1, 'x', True]}, {'id': 2, 'truck': 'B', 'stops': []},
                                         'junk', {'id': True, 'truck': 'A', 'stops': []}]})
    assert (junk.trucks, junk.excluded, [(t.id, t.stops) for t in junk.trips], junk.next_id) == \
        (['A'], {_isn(1)}, [(2, [1])], 3)
    assert dp.Draft.from_json(None) == dp.Draft()


def test_dispatch_baseline_by_manager_cars_and_plan_vs_fact():
    ctx = _dp_ctx()
    # у обоих менеджеров магазины и на востоке, и на западе: «по менеджерам» обе машины едут в оба конца
    orders = [_dorder(1, 101, 300.0, agent=1), _dorder(2, 104, 300.0, agent=1), _dorder(3, 102, 300.0, agent=2),
              _dorder(4, 105, 300.0, agent=2), _dorder(5, 106, 300.0, agent=3)]
    pts = {c: p for c, p, _ in EAST + WEST}
    stops = dp.build_stops(orders, _coord_of(pts))
    groups = dp.manager_trucks(stops, {1: ('XXX', '333DO33'), 2: ('991AT61',)}, [HOWO, FORD])
    assert {k: [s.customer_id for s in v] for k, v in groups.items()} == \
        {'333DO33': [101, 104], '991AT61': [102, 105, 106]}            # нет машины в истории — самая большая
    draft = dp.build(ctx, stops, None, ['333DO33', '991AT61'], 'now')
    base = dp.baseline(ctx, stops, draft, {1: ('333DO33',), 2: ('991AT61',)})
    assert base['trips'] == 2 and base['km'] > dp.plan_view(ctx, stops, draft, _info)['summary']['km'] + 10
    assert dp.baseline(ctx, stops, dp.Draft(), {}) is None             # машин дня нет — сравнивать не с чем
    # план и факт: восток и запад развезли две машины «вперемешку» — по плану короче; чужая машина и
    # магазин без точки — в skipped
    docs = [dp.ShippedDoc(101, 1, '333DO33', 1.0, 200.0), dp.ShippedDoc(104, 1, '333DO33', 1.0, 200.0),
            dp.ShippedDoc(102, 2, '991AT61', 1.0, 200.0), dp.ShippedDoc(105, 2, '991AT61', 1.0, 200.0),
            dp.ShippedDoc(103, 3, 'ZZZ', 1.0, 50.0), dp.ShippedDoc(999, 3, '333DO33', 1.0, 70.0)]
    r = dp.plan_vs_fact(ctx, docs, _coord_of(pts))
    assert (r['stops'], r['kg'], r['trucks']) == (4, 800, ['333DO33', '991AT61'])
    assert r['fact']['trips'] == 2 and r['plan']['km'] < r['fact']['km'] and r['saved_km'] > 0
    assert r['skipped'] == {'docs': 2, 'kg': 120, 'cars_not_set': ['ZZZ']}


def test_store_dispatch_drafts_and_geo_overrides(store):
    assert store.load_dispatch('2026-10-01') is None
    assert store.save_dispatch('2026-10-01', {'a': 1}, 'qa') == 1
    assert store.save_dispatch('2026-10-01', {'a': 2}, 'qa', expected_rev=1) == 2
    assert store.save_dispatch('2026-10-01', {'a': 3}, 'qa', expected_rev=1) is None    # другая вкладка
    assert store.load_dispatch('2026-10-01') == ({'a': 2}, 2)
    store.save_dispatch('2026-05-01', {'old': 1}, 'qa')
    store.save_dispatch('2026-10-02', {'b': 1}, 'qa')                   # старше 120 дней — удаляется
    assert store.load_dispatch('2026-05-01') is None and store.load_dispatch('2026-10-02') == ({'b': 1}, 1)
    store.delete_dispatch('2026-10-01')
    assert store.load_dispatch('2026-10-01') is None
    store.save_dispatch('2026-10-05', {'overtime': True}, 'qa')
    store.save_dispatch('2026-10-06', {'overtime': False}, 'qa')
    store.save_dispatch('2026-11-02', {'overtime': True}, 'qa')
    assert store.count_dispatch_overtime('2026-10-01', '2026-10-31') == 1
    assert store.count_dispatch_overtime('2026-10-01', '2026-11-30') == 2
    store.save_geo_override(999, (40.2, 44.5), 'qa')
    store.save_geo_override(998, (40.3, 44.6), 'qa')
    store.save_geo_override(998, None, 'qa')
    b = store.load()
    assert b.geo_overrides == {999: (40.2, 44.5)}
    fp = b.fingerprint()
    store.save_geo_override(999, (40.21, 44.5), 'qa')
    assert store.load().fingerprint() != fp                             # кэш оценки пересчитается
    for bad in ((0, (40.2, 44.5)), (5, (55.0, 44.5)), (True, (40.2, 44.5))):
        with pytest.raises(ValueError):
            store.save_geo_override(*bad, 'qa')


def test_store_migrates_schema_6_to_7_keeps_values(tmp_path):
    """6 → 7: новые таблицы customer_geo_override и dispatch_plan; всё прежнее — как было."""
    path = str(tmp_path / 'v6.db')
    with closing(sqlite3.connect(path)) as conn:
        for sql in st._SCHEMA[:-3]:
            conn.execute(st._TRUCKS_TABLE_V1 if sql == st._TRUCKS_TABLE else sql)
        conn.execute("INSERT INTO meta VALUES('schema_version', '6')")
        conn.execute("INSERT INTO settings VALUES('penalty_change', '450')")
        conn.execute("INSERT INTO depot VALUES(1, 40.19, 44.6, 'x', 'owner')")
        conn.execute("INSERT INTO trucks VALUES('CAR1', 10000, 30, NULL, 1, 'x', 'owner')")
        conn.execute("INSERT INTO scenario VALUES('s1', 'x', 'qa', '{}', '{\"n\": 1}')")
        conn.commit()
        before = {t: sorted(conn.execute(f'SELECT {_KEPT_COLUMNS.get(t, "*")} FROM {t}').fetchall())
                  for t in ('settings', 'depot', 'trucks', 'manager_profile', 'decision', 'scenario')}
    s = st.Store(path)
    b = s.load()
    assert (b.settings['penalty_change'], b.depot, b.trucks['CAR1'].capacity_kg, b.geo_overrides) == \
        (450, (40.19, 44.6), 10000, {})
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)                                       # 6 → 7 → … → текущая
        assert {t: sorted(conn.execute(f'SELECT {_KEPT_COLUMNS.get(t, "*")} FROM {t}').fetchall())
                for t in before} == before
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {'customer_geo_override', 'dispatch_plan'} <= tables
    s.save_geo_override(5, (40.2, 44.5), 'qa')
    assert s.save_dispatch('2026-10-01', {'x': 1}, 'qa') == 1


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы маршрутов владельца')
def test_store_owner_copy_gets_dispatch_tables(tmp_path):
    """КОПИЯ базы владельца: новые таблицы появляются, сохранённое — как было; запись — только в копию."""
    def dump(conn):
        return {t: sorted(conn.execute(f'SELECT {_KEPT_COLUMNS.get(t, "*")} FROM {t}').fetchall())
                for t in ('settings', 'depot', 'trucks', 'manager_profile')}

    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro&immutable=1', uri=True)) as conn:
        before = dump(conn)
    copy = tmp_path / 'owner.db'
    copy.write_bytes(OWNER_DB.read_bytes())
    s = st.Store(str(copy))
    s.load()
    s.save_geo_override(123, (40.2, 44.5), 'qa')
    with closing(sqlite3.connect(str(copy))) as conn:
        assert dump(conn) == before
        assert conn.execute('SELECT COUNT(*) FROM customer_geo_override').fetchone() == (1,)


def test_geo_override_used_across_section(client):
    snap = make_snapshot()
    assert ev.visit_coord(snap, 103, 0).source == 'gps'
    assert ev.visit_coord(snap, 103, 0, {103: (40.21, 44.51)}) == geo.Coord(40.21, 44.51, 'manual')
    assert ev.visit_coord(snap, 999, 0, {103: (40.21, 44.51)}) == geo.NO_COORD
    r = client.post('/api/routes/geo-override', json={'customer_id': 103, 'lat': 40.21, 'lon': 44.51})
    assert r.status_code == 200 and r.get_json() == {'success': True}
    c103 = client.get('/api/routes/overview').get_json()['customers']['103']
    assert (c103['coord_source'], c103['lat'], c103['lon']) == ('manual', 40.21, 44.51)
    assert client.post('/api/routes/geo-override', json={'customer_id': 103, 'lat': None, 'lon': None}).status_code == 200
    assert client.get('/api/routes/overview').get_json()['customers']['103']['coord_source'] == 'gps'
    for body in ({'customer_id': 103, 'lat': 55.0, 'lon': 44.5}, {'customer_id': 103, 'lat': 40.2},
                 {'customer_id': 'x', 'lat': 40.2, 'lon': 44.5}, {'customer_id': 103, 'lat': True, 'lon': 44.5},
                 {'customer_id': 103, 'lat': 40.2, 'lon': None}, [1]):
        assert client.post('/api/routes/geo-override', json=body).status_code == 400, body
    assert client.post('/api/routes/geo-override', data='x', content_type='text/plain').status_code == 415


def _dispatch_setup(client, orders, docs=()):
    state = client.application.extensions['route_optimizer']
    calls = []
    data = dp.DispatchData(tuple(orders), {101: ('C101', 'Клиент <101>'), 102: ('C102', 'Клиент 102'),
                                           104: ('C104', 'Клиент 104'), 999: ('C999', 'Новый')},
                           {101: 'Ереван, <b>1</b>'}, {1: ('CAR1',), 2: ('CAR2',)}, datetime(2026, 9, 30, 18, 0))

    def loader(since, until, day):
        calls.append((since, until, day))
        return data

    state.dispatch_loader = loader
    state.fact_loader = lambda day: dp.FactData(tuple(docs), {})
    # магазины 101–104 — в центре Еревана (стартовая граница малого центра): CAR1 туда въезжает
    r = client.post('/api/routes/settings', json={
        'depot': {'lat': DP_DEPOT[0], 'lon': DP_DEPOT[1]},
        'trucks': [{'car_code': 'CAR1', 'capacity_kg': 10000, 'fuel_l_per_100km': 30, 'active': True, 'center_ok': True},
                   {'car_code': 'CAR2', 'capacity_kg': 3500, 'fuel_l_per_100km': 16, 'active': True}]})
    assert r.status_code == 200, r.get_json()
    return calls


def test_api_dispatch_flow(client):
    orders = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2),
              _dorder(4, 999, 50.0), _dorder(5, 104, 100.0, agent=2, van=2),
              _dorder(6, 102, 70.0, day=date(2026, 9, 28))]
    calls = _dispatch_setup(client, orders)
    r = client.get('/api/routes/dispatch?date=2026-10-01')
    assert r.status_code == 200 and r.headers['Cache-Control'].startswith('no-cache')
    d = r.get_json()
    assert calls == [(date(2026, 9, 28), date(2026, 10, 1), DP_DAY)]     # окно с запасом на прошлые дни
    assert (d['day'], d['weekday'], d['order_dates'], d['plan'], d['rev']) == \
        ('2026-10-01', 4, {'since': '2026-09-30', 'until': '2026-09-30'}, None, 0)
    assert d['orders'] == {'count': 4, 'kg': 1950, 'revenue': 40000, 'customers': 4, 'shipped_before': 0,
                           'excluded': 0, 'self_delivery': 1, 'self_delivery_kg': 100, 'no_coords': 1, 'no_coords_kg': 50}
    assert [s['customer_id'] for s in d['stops_no_coords']] == [999]
    assert [o['customer_id'] for o in d['backlog']] == [102] and d['backlog'][0]['added'] is False
    assert [(t['car_code'], t['ready'], t['selected']) for t in d['trucks']] == [('CAR1', True, True), ('CAR2', True, True)]
    assert d['problems'] == []
    client.get('/api/routes/dispatch?date=2026-10-01')
    assert len(calls) == 1                                              # заказы — из кэша
    assert client.get('/api/routes/dispatch?date=2026-13-01').status_code == 400
    # «Собрать рейсы»
    assert client.post('/api/routes/dispatch/build', data='x', content_type='text/plain').status_code == 415
    assert client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['ZZZ']}).status_code == 400
    assert client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': []}).status_code == 400
    assert client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': 0, 'action': 'unpin', 'trip': 1}).status_code == 409
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    plan = d['plan']
    assert d['rev'] == 1 and plan['summary']['stops'] == 3 and plan['unassigned'] == []
    assert {'trucks', 'unassigned', 'overflow', 'summary', 'built_at', 'baseline'} <= set(plan)
    stop = next(s for t in plan['trucks'] for tr in t['trips'] for s in tr['stops'] if s['customer_id'] == 101)
    assert (stop['name'], stop['address'], stop['agent_code'], stop['coord_source']) == \
        ('Клиент <101>', 'Ереван, <b>1</b>', 'A001', 'erp')            # экранирует страница, сервер отдаёт как есть
    assert [o['isn'] for o in stop['orders']] == [_isn(1)]
    trip_id = next(tr['id'] for t in plan['trucks'] for tr in t['trips'] if stop in tr['stops'])
    # правка со старым номером — 409; с актуальным — перенос в новый рейс CAR1
    body = {'date': '2026-10-01', 'action': 'move', 'customer_id': 101, 'from_trip': trip_id, 'to_trip': None, 'truck': 'CAR1'}
    stale = client.post('/api/routes/dispatch/edit', json={**body, 'rev': 0})
    assert stale.status_code == 409 and stale.get_json()['conflict'] is True
    r = client.post('/api/routes/dispatch/edit', json={**body, 'rev': 1})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d['rev'] == 2 and isinstance(d['delta_km'], float)
    solo = [tr for t in d['plan']['trucks'] for tr in t['trips'] if [s['customer_id'] for s in tr['stops']] == [101]]
    assert len(solo) == 1 and solo[0]['truck'] == 'CAR1'
    bad = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': 2, 'action': 'pin', 'trip': solo[0]['id'], 'truck': 'ZZZ'})
    assert bad.status_code == 400 and 'не работает' in bad.get_json()['error']
    # не везём 104 → в «не везём сегодня»; заказ прошлых дней — добавить
    r = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': 2, 'action': 'exclude', 'order': _isn(3)})
    d = r.get_json()
    assert d['orders']['excluded'] == 1 and [o['customer_id'] for o in d['excluded']] == [104]
    assert all(s['customer_id'] != 104 for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops'])
    r = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': 3, 'action': 'include', 'order': _isn(6)})
    d = r.get_json()
    assert d['backlog'][0]['added'] is True and d['orders']['count'] == 4     # 3 дня − 1 исключённый + 1 прошлых дней
    # ручная точка магазина без координат → он «ещё не в рейсах»
    assert client.post('/api/routes/geo-override', json={'customer_id': 999, 'lat': 40.2, 'lon': 44.55}).status_code == 200
    d = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert d['stops_no_coords'] == [] and [u['customer_id'] for u in d['plan']['unassigned']] == [999]
    assert d['plan']['unassigned'][0]['coord_source'] == 'manual'
    # «Начать заново»
    d = client.post('/api/routes/dispatch/reset', json={'date': '2026-10-01'}).get_json()
    assert d['plan'] is None and d['rev'] == 0 and d['orders']['excluded'] == 0


def test_api_dispatch_overtime_button(client):
    """Кнопка «Везти после 18:00»: день машины 10 минут — ничего не помещается; после кнопки все в
    рейсах с переработкой, отметка дня и счётчик дней с переработкой за месяц."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    r = client.post('/api/routes/settings', json={'settings': {'truck_work_start': '09:00', 'truck_work_end': '09:10'}})
    assert r.status_code == 200, r.get_json()
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['plan']['summary']['trips'] == 0 and all(u['no_room'] for u in d['plan']['unassigned'])
    assert (d['overtime'], d['overtime_days_month']) == (False, 0)
    assert client.post('/api/routes/dispatch/overtime', json={'date': '2026-10-01', 'rev': 0}).status_code == 409
    r = client.post('/api/routes/dispatch/overtime', json={'date': '2026-10-01', 'rev': d['rev']})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d['plan']['unassigned'] == [] and d['overtime'] is True and d['overtime_days_month'] == 1
    assert d['overtime_ok'] is True and any(t['late'] for t in d['plan']['trucks'])
    assert not any(t['over_time'] for t in d['plan']['trucks'])        # до 20:00 — принятая переработка
    # «не везём сегодня» все заказы: машины больше не работают после конца дня — отметка снимается сразу
    for isn in (_isn(1), _isn(2)):
        d = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': d['rev'], 'action': 'exclude',
                                                           'order': isn}).get_json()
    assert d['overtime'] is False and d['overtime_days_month'] == 0
    assert client.get('/api/routes/dispatch?date=2026-10-02').get_json()['overtime_days_month'] == 0


def test_api_dispatch_defer_trip_goes_to_next_day(client, monkeypatch):
    """«Везти завтра» на 01.10 — заказы сразу в развозе 02.10 (до первой сборки и в ней), без ручного
    «добавить» среди заказов прошлых дней."""
    from route_optimizer import views
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 1, 10, 0))   # 01.10 — сегодня, 02.10 — завтра
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    assert client.post('/api/routes/settings', json={'settings': {'min_trip_revenue': 150000}}).status_code == 200
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['min_trip_revenue'] == 150000 and d['plan']['summary']['poor_trips'] >= 1
    trip = next(t for tr in d['plan']['trucks'] for t in tr['trips'] if any(x['customer_id'] == 101 for x in t['stops']))
    assert trip['poor']
    moved = {x['customer_id'] for x in trip['stops']}
    r = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': d['rev'], 'action': 'defer_trip',
                                                       'trip': trip['id']})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert {o['customer_id'] for o in d['excluded']} == moved
    assert not {x['customer_id'] for tr in d['plan']['trucks'] for t in tr['trips'] for x in t['stops']} & moved
    nxt = client.get('/api/routes/dispatch?date=2026-10-02').get_json()     # следующий день, рейсов ещё нет
    carried = {o['customer_id'] for o in nxt['backlog'] if o['added']}
    assert carried == moved and nxt['orders']['count'] >= len(moved)
    b = client.post('/api/routes/dispatch/build', json={'date': '2026-10-02', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert moved <= {x['customer_id'] for tr in b['plan']['trucks'] for t in tr['trips'] for x in t['stops']}


def test_api_dispatch_defer_when_next_day_already_built_and_undo(client, monkeypatch):
    """План следующего дня уже собран: перенос он видит сам при загрузке (его черновик не меняется) —
    заказы в развозе и подсказка «новые с последней сборки»; отмена («вернуть») — убираются оттуда,
    чтобы не повезли дважды. Логист следующего дня может убрать перенесённый заказ — это запоминается.
    Слишком давний заказ на завтра не переносится; прошедший день перенос не меняет."""
    from route_optimizer import views
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 1, 10, 0))   # 01.10 — сегодня, 02.10 — завтра
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2),
                             _dorder(6, 104, 70.0, agent=2, day=date(2026, 9, 28))])
    assert client.post('/api/routes/settings', json={'settings': {'min_trip_revenue': 150000}}).status_code == 200
    nxt = client.post('/api/routes/dispatch/build', json={'date': '2026-10-02', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert nxt['plan'] is not None
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    trip = next(t for tr in d['plan']['trucks'] for t in tr['trips'] if any(x['customer_id'] == 101 for x in t['stops']))
    isns = {o['isn'] for x in trip['stops'] for o in x['orders']}
    d = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': d['rev'], 'action': 'defer_trip',
                                                       'trip': trip['id']}).get_json()
    assert all(o['deferred'] for o in d['excluded'] if o['isn'] in isns)
    n2 = client.get('/api/routes/dispatch?date=2026-10-02').get_json()
    assert isns <= {o['isn'] for o in n2['backlog'] if o['added'] and o['carried']}
    assert n2['rev'] == nxt['rev']                                          # чужой черновик не трогаем
    assert n2['new_since_build']['count'] == len(isns)                      # → «пересоберите рейсы»
    # логист 02.10 убирает один перенесённый заказ — запоминается, не возвращается
    one = sorted(isns)[0]
    n2 = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-02', 'rev': n2['rev'], 'action': 'exclude',
                                                        'order': one}).get_json()
    assert one not in {o['isn'] for o in n2['backlog'] if o['added']}
    assert one not in {o['isn'] for o in client.get('/api/routes/dispatch?date=2026-10-02').get_json()['backlog']
                       if o['added']}
    for isn in isns:                                                         # «вернуть» на сегодня
        d = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': d['rev'], 'action': 'include',
                                                           'order': isn}).get_json()
    n3 = client.get('/api/routes/dispatch?date=2026-10-02').get_json()
    assert not isns & {o['isn'] for o in n3['backlog'] if o['added']}
    # заказ 28.09 (прошлые дни, добавлен на 01.10): 02.10 его уже не увидит — переносить нельзя
    d = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': d['rev'], 'action': 'include',
                                                       'order': _isn(6)}).get_json()
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    old = next(t for tr in d['plan']['trucks'] for t in tr['trips'] if any(x['customer_id'] == 104 for x in t['stops']))
    r = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': d['rev'], 'action': 'defer_trip',
                                                       'trip': old['id']})
    assert r.status_code == 400 and 'слишком давние' in r.get_json()['errors']['_']


def test_api_dispatch_defer_refused_on_past_day(client, monkeypatch):
    import route_optimizer.views as rv
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    trip = d['plan']['trucks'][0]['trips'][0]

    class Later(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 5)
    monkeypatch.setattr(rv, 'date', Later)
    r = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': d['rev'], 'action': 'defer_trip',
                                                       'trip': trip['id']})
    assert r.status_code == 400 and 'Прошедший день' in r.get_json()['errors']['_']


def test_api_dispatch_problems_and_fact(client):
    state = client.application.extensions['route_optimizer']
    state.dispatch_loader = lambda since, until, day: dp.DispatchData((_dorder(1, 101, 10.0),), {}, {}, {}, datetime(2026, 9, 30))
    d = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert {p['code'] for p in d['problems']} == {'no_depot', 'no_trucks'}
    assert all(p['link'].startswith('/routes/settings#') for p in d['problems'])
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1']})
    assert r.status_code == 400
    docs = [dp.ShippedDoc(101, 1, 'CAR1', 1.0, 300.0), dp.ShippedDoc(104, 2, 'CAR2', 1.0, 300.0),
            dp.ShippedDoc(102, 1, '', 1.0, 10.0)]
    _dispatch_setup(client, [_dorder(1, 101, 10.0)], docs)
    assert client.get('/api/routes/dispatch/fact?date=2999-01-01').status_code == 400
    r = client.get('/api/routes/dispatch/fact?date=2026-09-29')
    assert r.status_code == 200
    f = r.get_json()['fact']
    assert (f['stops'], f['trucks'], f['skipped']['docs']) == (2, ['CAR1', 'CAR2'], 1)
    assert {'km', 'liters', 'trips', 'trips_over_time'} <= set(f['fact']) and f['plan']['trips'] >= 1


def test_api_dispatch_erp_down_is_503(client):
    state = client.application.extensions['route_optimizer']

    def down(since, until, day):
        raise erp.ErpError('нет связи')

    state.dispatch_loader = down
    r = client.get('/api/routes/dispatch?date=2026-10-01')
    assert r.status_code == 503 and r.get_json()['error'] == 'ERP տվյալների բազան հասանելի չէ'


# ============================== развоз: свежесть заказов ==============================

@pytest.mark.parametrize('day, now, ready, workdays, expected', [
    (date(2026, 10, 2), datetime(2026, 10, 1, 11, 27), '17:00', SIX, True),     # пт: заказы четверга ещё идут
    (date(2026, 10, 2), datetime(2026, 10, 1, 16, 59), '17:00', SIX, True),
    (date(2026, 10, 2), datetime(2026, 10, 1, 17, 0), '17:00', SIX, False),     # ровно в 17:00 — уже можно
    (date(2026, 10, 2), datetime(2026, 10, 1, 17, 30), '18:30', SIX, True),     # время — из настроек
    (date(2026, 10, 2), datetime(2026, 9, 30, 11, 0), '17:00', SIX, False),     # дата через день — приём не начался
    (date(2026, 10, 2), datetime(2026, 10, 2, 8, 0), '17:00', SIX, False),      # день развоза уже настал
    (date(2026, 10, 5), datetime(2026, 10, 3, 11, 0), '17:00', SIX, True),      # пн: заказы субботы
    (date(2026, 10, 5), datetime(2026, 10, 4, 11, 0), '17:00', SIX, False),     # воскресенье — не рабочий день
    (date(2026, 10, 5), datetime(2026, 10, 2, 11, 0), '17:00', SIX, False),     # пятница — ещё не тот день
    (date(2026, 10, 5), datetime(2026, 10, 2, 11, 0), '17:00', [1, 2, 3, 4, 5], True),   # пятидневка: пт → пн
    (date(2026, 10, 3), datetime(2026, 10, 2, 9, 0), '17:00', SIX, True),       # сб: заказы пятницы
    (date(2026, 9, 30), datetime(2026, 10, 1, 10, 0), '17:00', SIX, False),     # прошедшая дата
])
def test_dispatch_orders_still_coming(day, now, ready, workdays, expected):
    assert dp.orders_still_coming(day, workdays, now, ready) is expected


def test_dispatch_since_build_new_removed_and_draft_marks():
    o1, o2, o3 = _dorder(1, 101, 100.0), _dorder(2, 102, 200.0, rev=5000.0), _dorder(3, 103, 50.0)
    old = _dorder(9, 109, 70.0, day=date(2026, 9, 28))                          # прошлых дней, добавлен логистом
    built = dp.order_marks([o1, o2, o3, old])
    assert dp.since_build(built, [o1, o2, o3], [o1, o2, o3, old], set()) == \
        {'new': {'count': 0, 'kg': 0, 'revenue': 0}, 'removed': {'count': 0, 'kg': 0, 'revenue': 0}}
    # пришли 4 и 5 (5 логист уже исключил — не «новый»), 2 отменили, заказ прошлых дней отгрузили
    o4, o5 = _dorder(4, 104, 1200.0, rev=30000.0), _dorder(5, 105, 40.0)
    r = dp.since_build(built, [o1, o3, o4, o5], [o1, o3, o4, o5], {o5.isn})
    assert r == {'new': {'count': 1, 'kg': 1200, 'revenue': 30000},
                 'removed': {'count': 2, 'kg': 270, 'revenue': 15000}}
    assert dp.since_build(None, [o1], [o1], set()) is None                      # старый черновик — не с чем сравнить
    # отметка живёт в черновике: туда и обратно через JSON; битые строки отбрасываются
    d = dp.Draft(['CAR1'], set(), set(), [], 1, '2026-10-01T15:20:00', built)
    back = dp.Draft.from_json(json.loads(json.dumps(d.to_json())))
    assert back == d and back.built_orders[o2.isn] == (200.0, 5000.0)
    junk = dp.Draft.from_json({'built_orders': {o1.isn: [1, 2], 'bad': [1, 2], o2.isn: [True, 1], o3.isn: [1],
                                                _isn(7): ['x', 1], _isn(8): [float('nan'), 1]}})
    assert junk.built_orders == {o1.isn: (1.0, 2.0)}
    assert dp.Draft.from_json({'built_orders': 'x'}).built_orders is None
    legacy = {k: v for k, v in d.to_json().items() if k != 'built_orders'}      # черновик до этой правки
    assert dp.Draft.from_json(legacy).built_orders is None and dp.Draft.from_json(legacy).trucks == ['CAR1']


def test_store_dispatch_ready_time_setting(store):
    assert store.load().settings['dispatch_ready_time'] == '17:00'
    for bad in ('25:00', '5pm', '', None, 1700):
        _, errors = st.validate_payload({'settings': {'dispatch_ready_time': bad}}, store.load(), REF)
        assert 'settings.dispatch_ready_time' in errors, bad
    _save(store, {'settings': {'dispatch_ready_time': '18:30'}})
    assert store.load().settings['dispatch_ready_time'] == '18:30'


def test_api_dispatch_freshness_hint_and_new_since_build(client, monkeypatch):
    from route_optimizer import views
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 11, 27))   # среда, заказы на чт ещё идут
    orders = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2),
              _dorder(6, 102, 70.0, day=date(2026, 9, 28))]
    _dispatch_setup(client, orders)
    state = client.application.extensions['route_optimizer']
    data0 = state.dispatch_loader(None, None, None)
    calls = []

    def set_orders(new):
        data = replace(data0, orders=tuple(new))
        state.dispatch_loader = lambda since, until, day: (calls.append(day), data)[1]
        state.dispatch_cache.clear()                                    # как будто прошло 5 минут (TTL кэша)

    set_orders(orders)
    d = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert (d['orders_still_coming'], d['ready_time'], d['built_at'], d['new_since_build'], d['removed_since_build']) == \
        (True, '17:00', None, None, None)
    assert d['orders']['count'] == 3 and isinstance(d['orders_sig'], str)
    s = client.get('/api/routes/dispatch/status?date=2026-10-01').get_json()
    assert (s['orders_still_coming'], s['orders'], s['rev'], s['orders_sig']) == \
        (True, {'count': 3, 'kg': 1900, 'revenue': 30000}, 0, d['orders_sig'])
    assert len(calls) == 1                                              # статус — из того же кэша, без ERP
    assert 'plan' not in s and 'trucks' not in s                        # лёгкий ответ: рейсы не считаются
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 17, 5))
    assert client.get('/api/routes/dispatch/status?date=2026-10-01').get_json()['orders_still_coming'] is False
    assert client.get('/api/routes/dispatch/status?date=2026-10-02').get_json()['orders_still_coming'] is False
    assert client.get('/api/routes/dispatch/status?date=x').status_code == 400
    assert client.get('/api/routes/dispatch/status').status_code == 400
    # сборка: отметка — заказы дня; заказ прошлых дней добавлен логистом — тоже в отметке
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['built_at'] and d['new_since_build'] == {'count': 0, 'kg': 0, 'revenue': 0}
    d = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': 1, 'action': 'include',
                                                       'order': _isn(6)}).get_json()
    assert d['new_since_build']['count'] == 0                           # правка логиста — не «новый заказ»
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    built_at, rev = d['built_at'], d['rev']
    stored = dp.Draft.from_json(state.store.load_dispatch('2026-10-01')[0])
    assert set(stored.built_orders) == {_isn(1), _isn(2), _isn(3), _isn(6)}
    # пришли новые заказы (один — новому магазину 999 без точки), заказ 2 отменили, заказ 3 отгрузили раньше дня
    set_orders([orders[0], replace(orders[2], shipped=date(2026, 9, 30)), orders[3],
                _dorder(7, 101, 150.0), _dorder(8, 999, 2000.0, rev=40000.0)])
    s = client.get('/api/routes/dispatch/status?date=2026-10-01').get_json()
    assert s['new_since_build'] == {'count': 2, 'kg': 2150, 'revenue': 50000}
    assert s['removed_since_build'] == {'count': 2, 'kg': 1500, 'revenue': 20000}
    assert s['orders_sig'] != d['orders_sig'] and s['rev'] == rev and s['built_at'] == built_at
    d = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert d['new_since_build']['count'] == 2 and d['plan'] is not None   # рейсы без клика не пересобираются
    assert state.store.load_dispatch('2026-10-01')[1] == rev
    # «Пересобрать» — новые учтены, закреплённое и правки остаются
    trip = d['plan']['trucks'][0]['trips'][0]
    d = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': rev, 'action': 'pin',
                                                       'trip': trip['id'], 'truck': trip['truck']}).get_json()
    assert d['new_since_build']['count'] == 2                           # правка отметку не трогает
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['new_since_build'] == {'count': 0, 'kg': 0, 'revenue': 0} and d['removed_since_build']['count'] == 0
    assert any(t['pinned'] and t['id'] == trip['id'] for tr in d['plan']['trucks'] for t in tr['trips'])
    assert d['backlog'][0]['added'] is True
    # «Начать заново» — отметки нет
    d = client.post('/api/routes/dispatch/reset', json={'date': '2026-10-01'}).get_json()
    assert d['new_since_build'] is None and d['built_at'] is None


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы маршрутов владельца')
def test_store_owner_copy_ready_time_default_and_legacy_draft(tmp_path):
    """КОПИЯ базы владельца: dispatch_ready_time — по умолчанию (настройка без миграции), черновик прежнего
    формата (без отметки сборки) читается, сравнения «с последней сборки» нет; запись — только в копию."""
    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro&immutable=1', uri=True)) as conn:
        before = {t: sorted(conn.execute(f'SELECT * FROM {t}').fetchall()) for t in ('settings', 'dispatch_plan')}
    copy = tmp_path / 'owner.db'
    copy.write_bytes(OWNER_DB.read_bytes())
    s = st.Store(str(copy))
    assert s.load().settings['dispatch_ready_time'] == '17:00'
    legacy = {'trucks': ['CAR1'], 'excluded': [], 'added': [], 'next_id': 2, 'built_at': '2026-10-01T15:20:00',
              'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [101], 'pinned': True}]}
    s.save_dispatch('2026-10-02', legacy, 'qa')
    draft = dp.Draft.from_json(s.load_dispatch('2026-10-02')[0])
    assert draft.built_orders is None and draft.trips[0].pinned is True
    assert dp.since_build(draft.built_orders, [], [], set()) is None
    with closing(sqlite3.connect(str(copy))) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)
        assert sorted(conn.execute('SELECT * FROM settings').fetchall()) == before['settings']
    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro&immutable=1', uri=True)) as conn:
        assert {t: sorted(conn.execute(f'SELECT * FROM {t}').fetchall()) for t in before} == before


# ============================== ручные машины и «авто» для «активна» ==============================

MANUAL_REF = replace(REF, van_agent_ids=frozenset({7, 8}))


def _manual(code='35 XY 123', **kw):
    return {'car_code': code, 'name': 'Газель', 'capacity_kg': 2000, 'fuel_l_per_100km': 14, 'active': True,
            'van_agent_id': 7, **kw}


def _save_ref(store, payload, ref=MANUAL_REF):
    changes, errors = st.validate_payload(payload, store.load(), ref)
    assert errors == {}, errors
    store.save(changes, 'qa')


def test_manual_trucks_crud_and_validation(store):
    _save_ref(store, {'trucks': [{'car_code': 'CAR1', 'capacity_kg': 10000}],
                      'manual_trucks': [_manual(), _manual('77 AA 001', name=' JAC  N80 ', van_agent_id=None)]})
    b = store.load()
    m = b.trucks['35 XY 123']
    assert (m.manual, m.name, m.capacity_kg, m.fuel_l_per_100km, m.active, m.van_agent_id) == \
        (True, 'Газель', 2000, 14, True, 7)
    assert b.trucks['77 AA 001'].name == 'JAC N80' and b.trucks['77 AA 001'].van_agent_id is None
    assert b.trucks['CAR1'].manual is False and b.van_trucks() == {7: '35 XY 123'}
    # ошибки — по путям полей, ничего не записывается
    bad = [_manual('car 1'), _manual('Car-2'), _manual('!!'), _manual('X' * 21), _manual('A1'),
           _manual('a 1', van_agent_id=None), _manual('A2', name='я' * 61, van_agent_id=None),
           _manual('A3', van_agent_id=99), _manual('A4', van_agent_id=8), _manual('A5', van_agent_id=8),
           _manual('A6', capacity_kg=50, van_agent_id=None), _manual('A7', active='да', van_agent_id=None),
           _manual('A8', bogus=1), 'x']
    changes, errors = st.validate_payload({'manual_trucks': bad}, b, MANUAL_REF)
    assert changes is None
    assert set(errors) == {'manual_trucks.0.car_code', 'manual_trucks.1.car_code', 'manual_trucks.2.car_code',
                           'manual_trucks.3.car_code', 'manual_trucks.5.car_code', 'manual_trucks.6.name',
                           'manual_trucks.7.van_agent_id', 'manual_trucks.9.van_agent_id',
                           'manual_trucks.10.capacity_kg', 'manual_trucks.11.active', 'manual_trucks.12',
                           'manual_trucks.13'}
    assert 'արդեն կա ERP-ում' in errors['manual_trucks.1.car_code']
    assert 'երկու անգամ' in errors['manual_trucks.5.car_code']
    assert 'A4' in errors['manual_trucks.9.van_agent_id']                   # экспедитор у двух машин
    assert st.validate_payload({'manual_trucks': [_manual()] * 51}, b, MANUAL_REF)[1].keys() == {'manual_trucks'}
    # ручную машину нельзя прислать как машину ERP
    assert 'trucks.0.car_code' in st.validate_payload({'trucks': [{'car_code': '35 XY 123'}]}, b, MANUAL_REF)[1]
    assert store.load() == b
    # правка: экспедиторы меняются местами в одном сохранении; 7 больше не в списке ERP — у своей машины остаётся
    _save_ref(store, {'manual_trucks': [_manual(van_agent_id=8, active=False), _manual('77 AA 001', van_agent_id=7)]})
    b = store.load()
    assert b.van_trucks() == {8: '35 XY 123', 7: '77 AA 001'} and b.trucks['35 XY 123'].active is False
    _save_ref(store, {'manual_trucks': [_manual('77 AA 001', van_agent_id=7, capacity_kg=2500)]},
              ref=replace(REF, van_agent_ids=frozenset()))
    b = store.load()
    assert '35 XY 123' not in b.trucks and b.trucks['77 AA 001'].capacity_kg == 2500   # удалена — нет в списке
    # без раздела manual_trucks ручные машины не трогаются; пустой список — удалить все
    _save_ref(store, {'trucks': [{'car_code': 'CAR1', 'fuel_l_per_100km': 30}]})
    assert '77 AA 001' in store.load().trucks
    _save_ref(store, {'manual_trucks': []})
    assert [c for c, t in store.load().trucks.items() if t.manual] == [] and 'CAR1' in store.load().trucks


def test_manual_truck_takes_part_in_fleet_and_dispatch(client):
    state = client.application.extensions['route_optimizer']
    state.snapshots = SnapshotCache(lambda: replace(make_snapshot(), expeditors={7: (40, TODAY)}))
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 104, 300.0, agent=2)])
    r = client.post('/api/routes/settings', json={'manual_trucks': [_manual()]})
    assert r.status_code == 200, r.get_json()
    b = state.store.load()
    snap = make_snapshot()
    fleet, _ = fl.fleet_trucks(b.resolved_trucks(snap.active_cars), {c: car.name for c, car in snap.cars.items()})
    assert fl.FleetTruck('35 XY 123', 'Газель', 2000.0, 14.0) in fleet         # модель парка: машина в расчёте
    d = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    mine = next(t for t in d['trucks'] if t['car_code'] == '35 XY 123')
    assert (mine['name'], mine['manual'], mine['ready'], mine['selected']) == ('Газель', True, True, True)
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['35 XY 123']}).get_json()
    assert [t['car_code'] for t in d['plan']['trucks']] == ['35 XY 123'] and d['plan']['summary']['stops'] == 2
    # выключили — в развозе её нет
    assert client.post('/api/routes/settings', json={'manual_trucks': [_manual(active=False)]}).status_code == 200
    d = client.get('/api/routes/dispatch?date=2026-10-02').get_json()
    assert '35 XY 123' not in [t['car_code'] for t in d['trucks']]


def test_fact_counts_expeditor_docs_as_manual_truck():
    ctx = _dp_ctx(trucks=(HOWO, FORD, fl.FleetTruck('VAN1', 'Газель', 2000.0, 14.0)))
    pts = {c: p for c, p, _ in EAST + WEST}
    vans = {7: 'VAN1'}
    docs = [dp.ShippedDoc(101, 1, '991AT61', 1.0, 200.0),
            dp.ShippedDoc(104, 2, '', 1.0, 300.0, van_agent_id=7),      # экспедитор без машины — его машина
            dp.ShippedDoc(105, 2, '', 1.0, 100.0, van_agent_id=7),
            dp.ShippedDoc(102, 3, '', 1.0, 50.0, van_agent_id=3),       # менеджер развозит сам — не в сравнении
            dp.ShippedDoc(103, 1, '', 1.0, 40.0, van_agent_id=8)]       # экспедитор без ручной машины
    assert [dp.fact_car(d, vans) for d in docs] == ['991AT61', 'VAN1', 'VAN1', '', '']
    r = dp.plan_vs_fact(ctx, docs, _coord_of(pts), vans)
    assert r['trucks'] == ['991AT61', 'VAN1'] and (r['stops'], r['kg']) == (3, 600)
    assert {t['car_code']: t['kg'] for t in r['fact']['trucks']} == {'991AT61': 200, 'VAN1': 400}
    assert r['skipped'] == {'docs': 2, 'kg': 90, 'cars_not_set': []}
    assert dp.plan_vs_fact(ctx, docs, _coord_of(pts))['trucks'] == ['991AT61']   # без привязки — как раньше
    # «как обычно»: менеджер, чьи заказы возил экспедитор, — на его ручной машине
    hist = dp.history_cars({1: ('991AT61', 7, 'XXX'), 2: (7, 8, '333DO33'), 3: (8,)}, vans)
    assert hist == {1: ('991AT61', 'VAN1', 'XXX'), 2: ('VAN1', '333DO33'), 3: ()}


def test_api_fact_attributes_expeditor_to_manual_truck(client):
    state = client.application.extensions['route_optimizer']
    state.snapshots = SnapshotCache(lambda: replace(make_snapshot(), expeditors={7: (40, TODAY)}))
    docs = [dp.ShippedDoc(101, 1, 'CAR1', 1.0, 300.0), dp.ShippedDoc(104, 2, '', 1.0, 300.0, van_agent_id=7),
            dp.ShippedDoc(102, 2, '', 1.0, 30.0, van_agent_id=2)]
    _dispatch_setup(client, [_dorder(1, 101, 10.0)], docs)
    f = client.get('/api/routes/dispatch/fact?date=2026-09-29').get_json()['fact']
    assert f['trucks'] == ['CAR1'] and f['skipped']['docs'] == 2
    assert client.post('/api/routes/settings', json={'manual_trucks': [_manual()]}).status_code == 200
    f = client.get('/api/routes/dispatch/fact?date=2026-09-29').get_json()['fact']
    assert f['trucks'] == ['35 XY 123', 'CAR1'] and f['skipped']['docs'] == 1      # сам менеджер — по-прежнему мимо


def test_erp_agent_cars_keeps_expeditors_without_car():
    class Cur:
        def execute(self, sql, *args):
            pass

        def fetchall(self):
            return [(1, '991AT61', 0, 5), (1, '', 7, 9), (1, 'XXX', 0, 5), (2, '', 8, 3), (2, '', 7, 3)]

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cur()

    assert erp.agent_cars(Conn(), date(2026, 7, 1), date(2026, 10, 1)) == {1: (7, '991AT61', 'XXX'), 2: (7, 8)}


def _car_snapshot(today=TODAY):
    cars = {c: erp.Car(c, 'HOWO', closed) for c, closed in
            (('USED', False), ('IDLE', False), ('NEVER', False), ('CLOSED', True), ('EDGE', False))}
    last = {'USED': today - timedelta(days=3), 'IDLE': date(2025, 4, 3), 'CLOSED': today - timedelta(days=1),
            'EDGE': today - timedelta(days=60)}
    return replace(make_snapshot(), cars=cars, car_last_used=last)


def test_truck_active_auto_by_erp_usage_keeps_owner_choice():
    snap = _car_snapshot()
    assert snap.active_cars == frozenset({'USED', 'EDGE'})              # 60 дней — ещё «работает», закрытая — нет
    b = st.Bundle(st.DEFAULT_SETTINGS, None, {
        'USED': st.Truck('USED', 5000.0, 20.0, active=None), 'IDLE': st.Truck('IDLE', 5000.0, 20.0, active=None),
        'NEVER': st.Truck('NEVER', 2200.0, 10.0, active=True),          # владелец включил сам — включена
        'EDGE': st.Truck('EDGE', 2200.0, 10.0, active=False)}, {})      # выключил сам — выключена
    assert [b.truck_active(c, snap.active_cars) for c in ('USED', 'IDLE', 'NEVER', 'EDGE', 'CLOSED')] == \
        [True, False, True, False, False]
    fleet, incomplete = fl.fleet_trucks(b.resolved_trucks(snap.active_cars), {})
    assert [t.car_code for t in fleet] == ['NEVER', 'USED'] and incomplete == []
    assert fl.fleet_trucks(b.trucks, {})[0] == [fl.FleetTruck('NEVER', None, 2200.0, 10.0)]   # «авто» не разрешено — мимо


def test_api_settings_trucks_hints_and_auto_active(client):
    state = client.application.extensions['route_optimizer']
    snap = replace(_car_snapshot(), agents={**make_snapshot().agents, 7: erp.Agent(7, 'B006/22', 'Экспедитор', False)},
                   expeditors={7: (1127, TODAY - timedelta(days=28)), 8: (503, TODAY)})
    state.snapshots = SnapshotCache(lambda: snap)
    d = client.get('/api/routes/settings').get_json()
    by = {t['car_code']: t for t in d['trucks']}
    assert (by['IDLE']['active'], by['IDLE']['active_source'], by['IDLE']['auto_active'], by['IDLE']['last_used']) == \
        (False, 'auto', False, '2025-04-03')
    assert (by['USED']['active'], by['NEVER']['last_used'], by['CLOSED']['active']) == (True, None, False)
    assert d['car_idle_days'] == 60
    assert [(e['code'], e['docs'], e['truck']) for e in d['expeditors']] == [('B006/22', 1127, None), ('8', 503, None)]
    # явный выбор владельца держится, null — снова «авто»
    r = client.post('/api/routes/settings', json={'trucks': [{'car_code': 'IDLE', 'active': True},
                                                             {'car_code': 'USED', 'capacity_kg': 5000}]})
    assert r.status_code == 200, r.get_json()
    by = {t['car_code']: t for t in client.get('/api/routes/settings').get_json()['trucks']}
    assert (by['IDLE']['active'], by['IDLE']['active_source']) == (True, 'manual')
    assert (by['USED']['active'], by['USED']['active_source']) == (True, 'auto')     # тоннаж — не выбор «активна»
    assert client.post('/api/routes/settings', json={'trucks': [{'car_code': 'IDLE', 'active': None}]}).status_code == 200
    assert {t['car_code']: t for t in client.get('/api/routes/settings').get_json()['trucks']}['IDLE']['active'] is False
    bad = client.post('/api/routes/settings', json={'trucks': [{'car_code': 'IDLE', 'active': 'да'}]})
    assert bad.status_code == 400 and 'trucks.0.active' in bad.get_json()['errors']
    # ручная машина с экспедитором — в списке машин и у экспедитора
    r = client.post('/api/routes/settings', json={'manual_trucks': [_manual('B22', van_agent_id=7)]})
    assert r.status_code == 200, r.get_json()
    d = client.get('/api/routes/settings').get_json()
    mine = next(t for t in d['trucks'] if t['car_code'] == 'B22')
    assert (mine['manual'], mine['active_source'], mine['van']['code'], mine['van']['docs']) == (True, 'manual', 'B006/22', 1127)
    assert d['expeditors'][0]['truck'] == 'B22'
    clash = client.post('/api/routes/settings', json={'manual_trucks': [_manual('used')]})
    assert clash.status_code == 400 and 'manual_trucks.0.car_code' in clash.get_json()['errors']


def test_store_migrates_schema_7_to_8_truck_active_and_manual(tmp_path):
    """7 → 8: сохранённая «активна» (1 и 0) — выбор владельца, как была; «авто» — только у машин без записи;
    остальные значения машин — как были; ручные машины и экспедитор — новые столбцы. Сбой — база версии 7."""
    def v7(path, broken=False):
        with closing(sqlite3.connect(path)) as conn:
            for sql in st._SCHEMA[:-1]:
                conn.execute(st._TRUCKS_TABLE_V1 if sql == st._TRUCKS_TABLE else sql)
            conn.execute("INSERT INTO meta VALUES('schema_version', '7')")
            conn.executemany('INSERT INTO trucks VALUES(?, ?, ?, ?, ?, ?, ?)',
                             [('CAR1', 10000, 30, 3160, 1, 'x', 'owner'), ('CAR2', 100, None, None, 0, 'x', 'owner')])
            if broken:
                conn.execute('CREATE TABLE trucks_v8(x)')               # мешает пересборке
            conn.commit()

    path = str(tmp_path / 'v7.db')
    v7(path)
    s = st.Store(path)
    b = s.load()
    assert b.trucks['CAR1'] == st.Truck('CAR1', 10000, 30, 3160, True, 'x', 'owner')
    assert b.trucks['CAR2'] == st.Truck('CAR2', 100, None, None, False, 'x', 'owner')
    assert b.truck_active('CAR1', frozenset()) is True and b.truck_active('NEW', {'NEW'}) is True   # «авто» — без записи
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)                                       # 7 → 8 → … → текущая
        indexes ={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert 'trucks_one_van' in indexes
        with pytest.raises(sqlite3.IntegrityError):                      # один экспедитор — одна машина
            conn.executemany("INSERT INTO trucks(car_code, manual, van_agent_id, active, updated_at) "
                             "VALUES(?, 1, 7, 1, 'x')", [('M1',), ('M2',)])
    broken = str(tmp_path / 'v7-broken.db')
    v7(broken, broken=True)
    with pytest.raises(st.StoreError):
        st.Store(broken).load()
    with closing(sqlite3.connect(broken)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('7',)
        assert conn.execute('SELECT active FROM trucks ORDER BY car_code').fetchall() == [(1,), (0,)]


def test_store_rejects_broken_truck_rows(tmp_path):
    path = str(tmp_path / 'r.db')
    s = st.Store(path)
    s.load()
    for row in (("'A', NULL, NULL, NULL, NULL, 1, 'x', NULL", '«աշխատում է»'),     # «авто» у ручной машины
                ("'A', NULL, NULL, NULL, 1, 0, 'x', NULL", 'անվանում'),            # название у машины ERP
                ("'A', NULL, NULL, NULL, 1, 0, NULL, 7", 'առաքիչ'),
                ("'A', NULL, NULL, NULL, 1, 2, NULL, NULL", 'ձեռքով')):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('DELETE FROM trucks')
            conn.execute(f"INSERT INTO trucks(car_code, capacity_kg, fuel_l_per_100km, agent_id, active, manual, name, "
                         f"van_agent_id, updated_at) VALUES({row[0]}, 'x')")
            conn.commit()
        with pytest.raises(st.StoreError, match=row[1]):
            s.load()


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы маршрутов владельца')
def test_store_owner_copy_migrates_to_8_and_takes_manual_truck(tmp_path):
    """КОПИЯ базы владельца: машины — те же числа и «активна» (выбор владельца: 124AV61 и 475DD61 работают,
    хотя давно не возили — «авто» их не выключает); ручная машина пишется только в копию, файл владельца
    не меняется."""
    owner_bytes = OWNER_DB.read_bytes()
    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro&immutable=1', uri=True)) as conn:
        rows = sorted(conn.execute('SELECT car_code, capacity_kg, fuel_l_per_100km, active FROM trucks').fetchall())
    copy = tmp_path / 'owner.db'
    copy.write_bytes(owner_bytes)
    s = st.Store(str(copy))
    b = s.load()
    expected = [(c, cap, fuel, None if a is None else bool(a)) for c, cap, fuel, a in rows]
    assert sorted((t.car_code, t.capacity_kg, t.fuel_l_per_100km, t.active) for t in b.trucks.values()
                  if not t.manual) == expected
    saved = {r[0]: r[3] for r in rows}
    for code in ('124AV61', '475DD61'):
        if saved.get(code) == 1:
            assert b.truck_active(code, frozenset()) is True    # давно не возила, но выбор владельца держится
    ref = st.RefData(frozenset(c for c, t in b.trucks.items() if not t.manual), frozenset(),
                     frozenset(b.settings['chain_groups']),
                     frozenset({3191}))
    changes, errors = st.validate_payload({'manual_trucks': [_manual('B006 22', van_agent_id=3191)]}, b, ref)
    assert errors == {}
    s.save(changes, 'qa')
    assert s.load().van_trucks() == {3191: 'B006 22'}
    assert OWNER_DB.read_bytes() == owner_bytes

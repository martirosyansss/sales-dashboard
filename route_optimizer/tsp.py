# -*- coding: utf-8 -*-
"""Порядок объезда: nearest-neighbor + 2-opt; нарезка тура машины на рейсы по тоннажу.

Чистая логика — без Flask и без БД. Расстояние — функция dist(a, b), км: по умолчанию по прямой
(haversine), и тогда поправку на извилистость дорог (detour_factor) применяет вызывающий код;
оценка передаёт Norms.km — по дорогам (этап 5), с извилистостью только у участков по прямой.
Матрица по дорогам направленная (одностороннее движение, Valhalla): d[a][b] ≠ d[b][a]; 2-opt это учитывает.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

from .geo import Point, haversine_km

Matrix = list[list[float]]
Distance = Callable[[Point, Point], float]   # км от точки до точки (по дорогам — направленно)

_EPS = 1e-12
# 2-opt по направленной матрице: ход — при выигрыше больше TWO_OPT_REL_EPS × длины тура. Порог 1e-12 там не годится:
# выигрыш копится разностью ходов куска, и на больших числах ошибка округления (~1e-12) выглядела выигрышем — кусок
# разворачивался туда-обратно без конца. TWO_OPT_MAX_PASSES — жёсткий предел проходов (обе матрицы; обычно их < 20).
TWO_OPT_REL_EPS = 1e-9
TWO_OPT_MAX_PASSES = 200


def distance_matrix(points: Sequence[Point], dist: Distance | None = None) -> Matrix:
    """Матрица км out[i][j] — от i до j; dist=None — по прямой (симметрична: считается половина)."""
    n = len(points)
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if dist is None:
                out[i][j] = out[j][i] = haversine_km(points[i], points[j])
            else:
                out[i][j] = dist(points[i], points[j])
                out[j][i] = dist(points[j], points[i])
    return out


def nearest_neighbor(dist: Matrix, start: int = 0) -> list[int]:
    """Жадный тур: из текущей точки — в ближайшую непосещённую (ничьи — по индексу)."""
    left = set(range(len(dist))) - {start}
    tour = [start]
    cur = start
    while left:
        cur = min(left, key=lambda j: (dist[cur][j], j))
        tour.append(cur)
        left.remove(cur)
    return tour


def closed_length(tour: Sequence[int], dist: Matrix) -> float:
    """Длина замкнутого тура (с возвратом в tour[0])."""
    if len(tour) < 2:
        return 0.0
    return sum(dist[a][b] for a, b in zip(tour, list(tour[1:]) + [tour[0]]))


def is_symmetric(dist: Matrix, nodes: Sequence[int]) -> bool:
    """dist[a][b] == dist[b][a] для всех вершин nodes."""
    return all(dist[a][b] == dist[b][a] for k, a in enumerate(nodes) for b in nodes[k + 1:])


def two_opt(tour: Sequence[int], dist: Matrix) -> list[int]:
    """2-opt для замкнутого тура; tour[0] (склад) остаётся первым. Длина не растёт.

    Ход разворачивает t[i..j]. В направленной матрице у развёрнутого куска меняются и внутренние рёбра:
    Δ = d(a, c) + d(b, e) − d(a, b) − d(c, e) + (обратный ход куска − прямой); оба хода копятся по j за O(1), ход —
    при Δ < −TWO_OPT_REL_EPS × длина тура, и тур из трёх вершин тоже разворачивается (в одну сторону короче).
    Симметричная матрица — прежний расчёт (те же ходы, тот же результат). Проходов — не больше TWO_OPT_MAX_PASSES."""
    t = list(tour)
    n = len(t)
    if n < 3:
        return t
    directed = not is_symmetric(dist, t)
    if n < 4 and not directed:
        return t
    eps = _EPS
    improved, passes = True, 0
    while improved and passes < TWO_OPT_MAX_PASSES:
        improved, passes = False, passes + 1
        if directed:
            eps = TWO_OPT_REL_EPS * max(1.0, closed_length(t, dist))
        for i in range(1, n - 1):
            fwd = back = 0.0   # ход куска t[i..j] вперёд и назад
            for j in range(i + 1, n):
                a, b = t[i - 1], t[i]
                c, d = t[j], t[(j + 1) % n]
                delta = dist[a][c] + dist[b][d] - dist[a][b] - dist[c][d]
                if directed:
                    fwd += dist[t[j - 1]][c]
                    back += dist[c][t[j - 1]]
                    delta += back - fwd
                if delta < -eps:
                    t[i:j + 1] = t[i:j + 1][::-1]
                    fwd, back = back, fwd
                    improved = True
    return t


def solve_tour(start: Point, points: Sequence[Point], dist_fn: Distance | None = None) -> list[int]:
    """Замкнутый тур start → points → start. Возвращает индексы points в порядке объезда."""
    if not points:
        return []
    dist = distance_matrix([start, *points], dist_fn)
    tour = two_opt(nearest_neighbor(dist, 0), dist)
    return [i - 1 for i in tour[1:]]


def route_order(points: Sequence[Point], home: Point | None,
                dist_fn: Distance | None = None) -> list[int]:
    """Порядок объезда точек менеджером (NN + 2-opt): замкнутый тур от дома;
    без дома — открытый путь со свободными концами (фиктивная вершина с нулевыми расстояниями)."""
    if len(points) <= 1:
        return list(range(len(points)))
    if home is not None:
        return solve_tour(home, points, dist_fn)
    dist = distance_matrix(points, dist_fn)
    full = [[0.0] * (len(points) + 1)] + [[0.0, *row] for row in dist]
    tour = two_opt(nearest_neighbor(full, 0), full)
    return [i - 1 for i in tour[1:]]


def split_by_capacity(kgs: Sequence[float],
                      capacity: float | None) -> tuple[list[list[int]], list[tuple[int, int]]]:
    """Нарезка тура по тоннажу по ходу объезда.

    kgs — вес заказов в порядке тура. Новый рейс начинается, если следующий клиент переполнит
    машину. Клиент тяжелее машины обслуживается отдельно: ceil(kg / тоннаж) поездок
    «склад → клиент → склад». capacity=None — тоннаж не задан, резки нет (один рейс).

    Возвращает (рейсы — списки позиций в туре, тяжёлые — пары (позиция, число поездок)).
    """
    if capacity is not None and capacity <= 0:
        raise ValueError('тоннаж машины должен быть > 0')
    trips: list[list[int]] = []
    heavy: list[tuple[int, int]] = []
    cur: list[int] = []
    load = 0.0
    for i, kg in enumerate(kgs):
        if capacity is not None and kg > capacity:
            heavy.append((i, math.ceil(kg / capacity)))
            continue
        if capacity is not None and cur and load + kg > capacity:
            trips.append(cur)
            cur, load = [], 0.0
        cur.append(i)
        load += kg
    if cur:
        trips.append(cur)
    return trips, heavy


@dataclass(frozen=True)
class Delivery:
    km: float   # по dist; dist=None — по прямой, без извилистости
    trips: int


def delivery_km(depot: Point, stops: Sequence[tuple[Point, float]],
                capacity: float | None, dist: Distance | None = None) -> Delivery:
    """Км и число рейсов машины за день: тур NN + 2-opt от склада, затем нарезка по тоннажу.

    stops — (точка клиента, кг заказа). Каждый рейс: склад → клиенты рейса → склад.
    dist — функция расстояния (None — по прямой).
    """
    if not stops:
        return Delivery(0.0, 0)
    d = haversine_km if dist is None else dist
    order = solve_tour(depot, [p for p, _ in stops], dist)
    points = [stops[i][0] for i in order]
    kgs = [stops[i][1] for i in order]
    trips, heavy = split_by_capacity(kgs, capacity)
    km = 0.0
    for trip in trips:
        path = [depot, *(points[i] for i in trip), depot]
        km += sum(d(a, b) for a, b in zip(path, path[1:]))
    for i, n in heavy:
        km += (d(depot, points[i]) + d(points[i], depot)) * n
    return Delivery(km, len(trips) + sum(n for _, n in heavy))

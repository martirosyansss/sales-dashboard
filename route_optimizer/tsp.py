# -*- coding: utf-8 -*-
"""Порядок объезда: nearest-neighbor + 2-opt; нарезка тура машины на рейсы по тоннажу.

Чистая логика — без Flask и без БД. Расстояния — км по прямой (haversine);
поправку на извилистость дорог (detour_factor) применяет вызывающий код.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .geo import Point, haversine_km

Matrix = list[list[float]]

_EPS = 1e-12


def distance_matrix(points: Sequence[Point]) -> Matrix:
    n = len(points)
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            dist[i][j] = dist[j][i] = haversine_km(points[i], points[j])
    return dist


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


def two_opt(tour: Sequence[int], dist: Matrix) -> list[int]:
    """2-opt для замкнутого тура; tour[0] (склад) остаётся первым. Длина не растёт."""
    t = list(tour)
    n = len(t)
    if n < 4:
        return t
    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b = t[i - 1], t[i]
                c, d = t[j], t[(j + 1) % n]
                if dist[a][c] + dist[b][d] - dist[a][b] - dist[c][d] < -_EPS:
                    t[i:j + 1] = t[i:j + 1][::-1]
                    improved = True
    return t


def solve_tour(start: Point, points: Sequence[Point]) -> list[int]:
    """Замкнутый тур start → points → start. Возвращает индексы points в порядке объезда."""
    if not points:
        return []
    dist = distance_matrix([start, *points])
    tour = two_opt(nearest_neighbor(dist, 0), dist)
    return [i - 1 for i in tour[1:]]


def route_order(points: Sequence[Point], home: Point | None) -> list[int]:
    """Порядок объезда точек менеджером (NN + 2-opt): замкнутый тур от дома;
    без дома — открытый путь со свободными концами (фиктивная вершина с нулевыми расстояниями)."""
    if len(points) <= 1:
        return list(range(len(points)))
    if home is not None:
        return solve_tour(home, points)
    dist = distance_matrix(points)
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
    km: float   # по прямой, без извилистости
    trips: int


def delivery_km(depot: Point, stops: Sequence[tuple[Point, float]],
                capacity: float | None) -> Delivery:
    """Км и число рейсов машины за день: тур NN + 2-opt от склада, затем нарезка по тоннажу.

    stops — (точка клиента, кг заказа). Каждый рейс: склад → клиенты рейса → склад.
    """
    if not stops:
        return Delivery(0.0, 0)
    order = solve_tour(depot, [p for p, _ in stops])
    points = [stops[i][0] for i in order]
    kgs = [stops[i][1] for i in order]
    trips, heavy = split_by_capacity(kgs, capacity)
    km = 0.0
    for trip in trips:
        path = [depot, *(points[i] for i in trip), depot]
        km += sum(haversine_km(a, b) for a, b in zip(path, path[1:]))
    for i, n in heavy:
        km += 2.0 * haversine_km(depot, points[i]) * n
    return Delivery(km, len(trips) + sum(n for _, n in heavy))

# -*- coding: utf-8 -*-
"""Порядок объезда точек машины (контракт §2 /day: seq, order_source).

- план «Развоза» на дату отдал машине клиентов → их порядок из плана (`dispatch`); клиенты накладных,
  которых нет в плане машины, — следом, маршрутом от последней точки плана;
- плана нет → маршрут от склада: nearest-neighbour + 2-opt (route_optimizer.tsp), по дорогам, если есть
  карта (`auto`); склада нет — открытый путь;
- точки без координат — в конце, по коду клиента.
Единица порядка — клиент (несколько накладных клиента — одна остановка подряд). Детерминизм: вход
сортируется по id клиента, ничьи tsp разрешает по индексу.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from route_optimizer import tsp
from route_optimizer.geo import Point
from route_optimizer.tsp import Distance


def order_customers(points: Mapping[int, Point | None], depot: Point | None, plan: Sequence[int],
                    dist: Distance | None = None) -> tuple[list[int], str]:
    """(клиенты по порядку объезда, order_source). plan — клиенты машины по плану «Развоза» (пусто — плана нет)."""
    with_pt = sorted(c for c, p in points.items() if p is not None)
    no_pt = sorted(c for c, p in points.items() if p is None)
    has_point = set(with_pt)
    planned = [c for c in dict.fromkeys(plan) if c in has_point]
    if planned:
        rest = [c for c in with_pt if c not in set(planned)]
        start = points[planned[-1]]
        assert start is not None
        tail = [rest[i] for i in tsp.solve_tour(start, [points[c] for c in rest], dist)] if rest else []
        return planned + tail + no_pt, 'dispatch'
    pts = [points[c] for c in with_pt]
    if depot is not None:
        idx = tsp.solve_tour(depot, pts, dist)
    else:
        idx = tsp.route_order(pts, None, dist)
    return [with_pt[i] for i in idx] + no_pt, 'auto'

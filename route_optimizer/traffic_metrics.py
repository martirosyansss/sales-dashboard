"""Время объезда менеджера по историческим скоростям и времени визитов."""
from __future__ import annotations

from typing import Callable, Sequence, TYPE_CHECKING

from .geo import Point, in_city

if TYPE_CHECKING:
    from .evaluate import Norms


def route_metrics(points: Sequence[Point], home: Point | None, norms: Norms,
                  weekday: int | None = None, services: Sequence[float] | None = None,
                  start_minute: float | None = None,
                  distance: Callable[[Point, Point], float] | None = None) -> tuple[float, float]:
    path = [home, *points, home] if home is not None else list(points)
    weekday0 = norms.traffic_weekday if weekday is None else weekday - 1
    clock = norms.traffic_start_min if start_minute is None else start_minute
    if services and home is None:
        clock += services[0]
    km = drive = 0.0
    for i, (a, b) in enumerate(zip(path, path[1:])):
        leg = distance(a, b) if distance is not None else norms.km(a, b)
        city = in_city(a, norms.city_center, norms.city_radius_km) and in_city(b, norms.city_center, norms.city_radius_km)
        speed = norms.speed_city_kmh if city else norms.speed_region_kmh
        minutes = (norms.traffic.travel(leg, speed, city, weekday0, clock)
                   if norms.traffic is not None else leg / speed * 60)
        km += leg
        drive += minutes
        clock += minutes
        stop = i if home is not None else i + 1
        if services and stop < len(points):
            clock += services[stop]
        if services and home is not None and stop == len(points) - 1:
            clock += sum(services[len(points):])
    return km, drive

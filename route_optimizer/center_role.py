"""Постоянная роль машины центра: центр ей, остальные точки — только в городе.

Городская зона совпадает с заданными центром/радиусом расчёта дорог. Правило
пересекается с допуском магазина и действует с указанной даты, включая новые
магазины. Отсутствие выбранной машины не разрешает автоматическую подмену.
"""
from __future__ import annotations

from datetime import date
import re
from typing import Any, Mapping, Sequence

from .geo import Point, in_city, in_polygon
from .vehicle_access import VehicleAccess


KEY = 'dispatch_center_role'


def parse(value: Any) -> tuple[dict[str, str] | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict) or set(value) != {'truck', 'from'}:
        return None, 'Նշեք կենտրոնի մեքենան և կանոնի մեկնարկի օրը'
    truck, since = value['truck'], value['from']
    if not isinstance(truck, str) or not truck.strip() or truck != truck.strip() or len(truck) > 64 \
            or any(ord(c) < 32 for c in truck):
        return None, 'Ընտրեք կենտրոնի մեքենան'
    try:
        if not isinstance(since, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', since):
            raise ValueError
        date.fromisoformat(since)
    except ValueError:
        return None, 'Նշեք կանոնի մեկնարկի օրը՝ ՏՏՏՏ-ԱԱ-ՕՕ'
    return {'truck': truck, 'from': since}, None


def active(rule: Mapping[str, str] | None, day: date) -> bool:
    return bool(rule and day.isoformat() >= rule['from'])


def apply(access: Mapping[int, VehicleAccess], customers: Mapping[int, Point],
          rule: Mapping[str, str] | None, day: date, center: Sequence[Point],
          city_center: Point, city_radius_km: float) -> dict[int, VehicleAccess]:
    out = dict(access)
    if not active(rule, day):
        return out
    code = rule['truck']
    for cid, point in customers.items():
        if point is None:
            continue
        previous = access.get(cid)
        if in_polygon(point, center):
            allowed = (code,) if previous is None or previous.allows(code) else ()
            out[cid] = VehicleAccess('allow', allowed)
        elif not in_city(point, city_center, city_radius_km):
            if previous is not None and previous.mode == 'allow':
                out[cid] = VehicleAccess('allow', tuple(c for c in previous.trucks if c != code))
            else:
                denied = set(previous.trucks if previous is not None else ()) | {code}
                out[cid] = VehicleAccess('deny', tuple(sorted(denied)))
    return out

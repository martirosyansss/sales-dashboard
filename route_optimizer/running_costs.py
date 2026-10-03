"""Топливо и стоимость износа по остаточному грузу на каждом участке рейса.

Незаданные нормы сохраняют прежний расход. Износ — настраиваемая стоимость,
а не вероятность поломки; дополнительная нагрузочная часть растёт как u².
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

LOAD_COST_FIELDS = ('fuel_empty_l_per_100km', 'fuel_full_l_per_100km',
                    'wear_amd_per_km', 'wear_load_amd_per_km')


def profile_fields(truck: Any) -> dict[str, float | None]:
    return {key: getattr(truck, key, None) for key in LOAD_COST_FIELDS}


def configured(truck: Any) -> bool:
    return (getattr(truck, 'fuel_empty_l_per_100km', None) is not None
            or any(getattr(truck, key, None) for key in LOAD_COST_FIELDS[2:]))


@dataclass(frozen=True)
class RunningCost:
    liters: float
    wear_amd: float
    payload_tonne_km: float
    fuel_load_configured: bool
    wear_configured: bool

    def total_amd(self, fuel_price: float) -> float:
        return self.liters * fuel_price + self.wear_amd


def route_cost(distances: Sequence[float], deliveries: Sequence[float], truck: Any) -> RunningCost:
    """n доставок, n+1 участков, включая возврат. Вес — фактическая доля заказа."""
    if len(distances) != len(deliveries) + 1:
        raise ValueError('число участков должно включать порожний возврат')
    if any(not math.isfinite(v) or v < 0 for v in (*distances, *deliveries)):
        raise ValueError('вес и расстояния должны быть конечными и неотрицательными')
    cap = float(truck.capacity_kg)
    if not math.isfinite(cap) or cap <= 0:
        raise ValueError('грузоподъёмность должна быть положительной')
    empty = getattr(truck, 'fuel_empty_l_per_100km', None)
    full = getattr(truck, 'fuel_full_l_per_100km', None)
    if (empty is None) != (full is None):
        raise ValueError('расход пустой и полной машины задаётся вместе')
    base = float(truck.l100) if empty is None else float(empty)
    slope = 0.0 if full is None else float(full) - base
    wear_base = getattr(truck, 'wear_amd_per_km', None)
    wear_load = getattr(truck, 'wear_load_amd_per_km', None)
    wear0, wear1 = float(wear_base or 0), float(wear_load or 0)
    if not all(math.isfinite(v) and v >= 0 for v in (base, slope, wear0, wear1)):
        raise ValueError('нормы топлива и износа должны быть конечными и неотрицательными')
    remaining = math.fsum(deliveries)
    fuel = wear = payload = distance = 0.0
    for i, km in enumerate(distances):
        distance += km
        u = remaining / cap
        fuel += km * (base + slope * u) / 100.0
        wear += km * (wear0 + wear1 * u * u)
        payload += km * remaining / 1000.0
        if i < len(deliveries):
            remaining = max(0.0, remaining - deliveries[i])
    if empty is None:
        fuel = distance * base / 100.0  # прежний порядок арифметики для незаданных норм
    return RunningCost(fuel, wear, payload, empty is not None,
                       wear_base is not None or wear_load is not None)

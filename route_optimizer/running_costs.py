"""Топливо и стоимость износа по остаточному грузу на каждом участке рейса.

Незаданные нормы сохраняют прежний расход. Износ — настраиваемая стоимость,
а не вероятность поломки; дополнительная нагрузочная часть растёт как u².

Рельеф (ответ владельца №85, docs/plans/terrain-fuel-plan.md): подъёмы участков (climbs, м — roads.RoadDistances.climb)
добавляют к литрам участка TERRAIN_K · m · (U − TERRAIN_U_BAR · км), m — собственная масса машины (curb_tonnes) + остаток
груза на участке, т. Нормы л/100 уже содержат средний рельеф наших дорог — вычитается средний подъём TERRAIN_U_BAR:
холмистый рейс дороже нормы, ровный — дешевле. Поправка не уводит участок ниже нуля литров. Без подъёмов (climbs None) —
расчёт байт в байт прежний. Выученные по заправкам нормы (learning) уже содержат рельеф своих рейсов: вместе с поправкой он
учтён бы дважды — пока рельеф только в показанных литрах (terrain.IN_PLAN), обучение его не вычитает (план №85, «Потом»).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

# л на тонну·метр подъёма: g / (КПД двигателя 0,33 · теплота сгорания дизеля 35,8 МДж/л) = 9,81 · 1000 / 11,8·10⁶
TERRAIN_K = 9.81 * 1000.0 / (0.33 * 35.8e6)
# Средний эффективный подъём наших дорог, м/км (центрирование: нормы л/100 его уже содержат). Посчитан один раз 07.10.2026:
# планы «Развоза» 31 рабочего дня 01.09–06.10.2026, пересобранные без рельефа (копия базы маршрутов, заказы ERP — только
# чтение, все готовые машины), 3539 участков, 16 086 км по графу OSM с высотами SRTM (terrain.DEM_SIGMA_M 100 м,
# CLIMB_C 0,015), подъёмы — roads.RoadDistances.climb; ū = mean_climb: Σ m·U / Σ m·км, m — curb_tonnes + остаток груза
# участка (как в route_cost) = 4,746 м/км (без веса массы — 7,28: склад наверху, груз едет вниз, вверх — пустая машина);
# сумма поправок по этой истории — 0,0 л. Пересчитать — при смене σ, CLIMB_C, норм л/100 или географии развоза: те же
# участки свежей истории → mean_climb.
TERRAIN_U_BAR = 4.746
# Средний подъём для нормы по GPS-треку («Նորմ և փաստ»): там масса постоянная (собственная + полгруза — груз по участкам
# трека не известен), поэтому центрирование — без веса массы: Σ U / Σ км тех же участков истории (тот же расчёт, что
# TERRAIN_U_BAR, mean_climb с одинаковой массой) = 7,275 м/км. С ū по массе (4,746) норма трека была бы систематически
# выше нормы на 2,5 м/км подъёма (~+2% литров у HOWO).
TERRAIN_U_BAR_TRACK = 7.275
CURB_T = ((2300.0, 2.3), (2600.0, 2.6), (5000.0, 4.5))   # тоннаж, кг → собственная масса, т (по классам парка)

LOAD_COST_FIELDS = ('fuel_empty_l_per_100km', 'fuel_full_l_per_100km',
                    'wear_amd_per_km', 'wear_load_amd_per_km')


def profile_fields(truck: Any) -> dict[str, float | None]:
    return {key: getattr(truck, key, None) for key in LOAD_COST_FIELDS}


def configured(truck: Any) -> bool:
    return (getattr(truck, 'fuel_empty_l_per_100km', None) is not None
            or any(getattr(truck, key, None) for key in LOAD_COST_FIELDS[2:]))


def curb_tonnes(capacity_kg: float) -> float:
    """Собственная масса машины, т, по тоннажу (CURB_T): до 2,3 т — 2,3 т; между классами — линейно; больше 5 т —
    пропорционально (4,5 т на 5 т)."""
    (c0, m0), *rest = CURB_T
    if capacity_kg <= c0:
        return m0
    for c1, m1 in rest:
        if capacity_kg <= c1:
            return m0 + (m1 - m0) * (capacity_kg - c0) / (c1 - c0)
        c0, m0 = c1, m1
    return m0 * capacity_kg / c0


def mean_climb(legs: Iterable[tuple[float, float, float]]) -> float:
    """Средний подъём для TERRAIN_U_BAR: участки истории (масса, т; подъём, м; км) → Σ m·U / Σ m·км, м/км. С ним сумма
    поправок рельефа по этим участкам — 0 (пока не срабатывает «участок не ниже нуля литров»)."""
    legs = list(legs)
    km = math.fsum(m * k for m, _, k in legs)
    return math.fsum(m * u for m, u, _ in legs) / km if km > 0 else 0.0


def terrain_liters(mass_t: float, climb_m: float, km: float, u_bar: float | None = None) -> float:
    """Литры подъёма участка (№85) сверх среднего, уже входящего в нормы: TERRAIN_K · m · (U − ū · км); ū — TERRAIN_U_BAR
    (участки рейсов с остатком груза), у трека с постоянной массой — TERRAIN_U_BAR_TRACK."""
    return TERRAIN_K * mass_t * (climb_m - (TERRAIN_U_BAR if u_bar is None else u_bar) * km)


@dataclass(frozen=True)
class RunningCost:
    liters: float
    wear_amd: float
    payload_tonne_km: float
    fuel_load_configured: bool
    wear_configured: bool
    terrain_liters: float | None = None   # №85: поправка рельефа, л (уже в liters); None — без рельефа
    climb_m: float | None = None          # №85: эффективный подъём рейса, м (участки с известным подъёмом)

    def total_amd(self, fuel_price: float) -> float:
        return self.liters * fuel_price + self.wear_amd


def route_cost(distances: Sequence[float], deliveries: Sequence[float], truck: Any,
               climbs: Sequence[float | None] | None = None) -> RunningCost:
    """n доставок, n+1 участков, включая возврат. Вес — фактическая доля заказа. climbs — эффективный подъём участков, м
    (рельеф, №85; None у участка — без поправки), None — без рельефа."""
    if len(distances) != len(deliveries) + 1:
        raise ValueError('число участков должно включать порожний возврат')
    if any(not math.isfinite(v) or v < 0 for v in (*distances, *deliveries)):
        raise ValueError('вес и расстояния должны быть конечными и неотрицательными')
    if climbs is not None and (len(climbs) != len(distances)
                               or any(u is not None and not math.isfinite(u) for u in climbs)):
        raise ValueError('подъёмы — по одному конечному числу на участок')
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
    hills: list[float] = []
    up: list[float] = []
    curb = curb_tonnes(cap) if climbs is not None else 0.0
    for i, km in enumerate(distances):
        distance += km
        u = remaining / cap
        leg = km * (base + slope * u) / 100.0
        fuel += leg
        wear += km * (wear0 + wear1 * u * u)
        payload += km * remaining / 1000.0
        if climbs is not None and climbs[i] is not None:
            extra = terrain_liters(curb + remaining / 1000.0, climbs[i], km)
            hills.append(max(extra, -(km * base / 100.0 if empty is None else leg)))   # участок — не ниже нуля литров
            up.append(climbs[i])
        if i < len(deliveries):
            remaining = max(0.0, remaining - deliveries[i])
    if empty is None:
        fuel = distance * base / 100.0  # прежний порядок арифметики для незаданных норм
    if climbs is None or not up:   # нет ни одного известного подъёма — рейс без рельефа (не «0 м»)
        return RunningCost(fuel, wear, payload, empty is not None,
                           wear_base is not None or wear_load is not None)
    terrain = math.fsum(hills)
    return RunningCost(fuel + terrain, wear, payload, empty is not None,
                       wear_base is not None or wear_load is not None, terrain, math.fsum(up))

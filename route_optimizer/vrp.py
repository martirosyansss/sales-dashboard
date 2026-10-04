# -*- coding: utf-8 -*-
"""Рейсы дня развоза решателем PyVRP (ответ владельца №45; https://github.com/PyVRP/PyVRP, MIT).

Чистая постановка без Flask, БД и модулей пакета: точки — номера в матрицах дня, машины — свободные промежутки
дня. Что взять — решает fleet.route_day: решение PyVRP проверяется там правилами «Развоза» и без них не
принимается. PyVRP не установлен — available() False, «Развоз» строит рейсы своим расчётом.

Постановка:
  - заказ (кусок тяжёлого заказа — отдельно) — клиент: груз, разгрузка, окно приёма; заказы, что уже в рейсах
    сборки, обязательны, остальные — необязательные с огромным призом (не помещаются — остаются вне рейсов);
  - машина — один «тип» PyVRP на каждый свободный промежуток её дня [начало, конец]: несколько рейсов подряд с
    возвратом на склад (reload), рейсы и разгрузка укладываются в промежуток;
  - стоимость метра — расход машины (л/100 км × 10): цель — литры дизеля; износ машины («Износ, драм/км», у машины с
    журналом гаража — его ремонт ֏/км, №53) — в литрах той же цены: (л/100 км + ֏/км × 100 / цена литра) × 10; без
    износа — ровно прежнее round(л/100 км × 10);
  - малый центр — профиль машины без права въезда: рёбра к точкам центра «бесконечные», max_distance их не
    пускает;
  - предел загрузки (load_cap, ответ №45: 0,9) — второе измерение вместимости: в нём груз заказа считается, если
    заказ можно увезти машиной не тяжелее предела, — тяжелее предела только заказ, который иначе не увезти.
    Такой заказ едет один: рёбра между ним и другими клиентами запрещены, допускается возврат на склад;
  - запас на рейс (№66, fleet.trip_reserve) — пауз PyVRP не знает: запас типичного рейса (trip_reserve_min) — на рёбрах
    «заказ → склад» (каждый рейс кончается таким ребром); темп машины (Vehicle.pace: множитель разгрузки, множитель
    пути) — её профилем: минуты рёбер × множитель пути, лишняя разгрузка (множитель − 1) × разгрузка заказа — на рёбрах
    из него (прибытие к следующему точно). Профили по темпу — только если у какой-то машины он не (1, 1). Точный расчёт
    «Развоза» (fleet._days) решение всё равно проверяет.
Минуты и километры — вверх до целых секунд и метров с запасом (время — вверх, окна — внутрь): решение,
допустимое для PyVRP, допустимо и для расчёта «Развоза» в float.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Sequence

try:   # необязательная зависимость: без неё — свой расчёт рейсов
    import pyvrp
    from pyvrp import Activity, ActivityType, Model, Route, Solution
    from pyvrp.stop import MaxIterations
except ImportError:   # pragma: no cover — сервер без pyvrp
    pyvrp = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Итераций поиска на день: число, а не время — план воспроизводим. Замер 03.10.2026 на 5 загруженных днях
# (docs/research/solver-budget): 2000 → 20 000 итераций — дизель −4,85% за ~10 с на день вместо ~1,5 с.
ITERATIONS = 20000
SEED = 1
MIN_ITERATIONS = 2000  # маленький день — меньше итераций (PER_ORDER на заказ), но не меньше этого
PER_ORDER = 500
FORBIDDEN_M = 10 ** 9  # ребро «машина без права въезда → точка центра», метров
MAX_DISTANCE_M = 10 ** 8   # max_distance каждого промежутка: запрещённое ребро недопустимо
PRIZE = 10 ** 13       # приз необязательного заказа: не везти его — дороже любого объезда


def available() -> bool:
    return pyvrp is not None


@dataclass(frozen=True)
class Piece:
    """Заказ или кусок тяжёлого заказа: узел матриц (0 — склад), кг, минуты разгрузки, окно (минуты от начала дня
    машины, None — без границы), в центре, обязателен (уже в рейсах сборки)."""
    node: int
    kg: float
    unload: float
    early: float | None
    late: float | None
    center: bool
    required: bool
    allowed_trucks: frozenset[str] | None = None


@dataclass(frozen=True)
class Shift:
    """Свободный промежуток дня машины, минуты от начала её дня."""
    truck: str
    start: float
    end: float


@dataclass(frozen=True)
class Vehicle:
    code: str
    capacity_kg: float
    l100: float
    center_ok: bool
    wear_amd_per_km: float | None = None   # износ, ֏/км; None или 0 — стоимость метра только литры
    fuel_price: float = 500.0              # цена литра, ֏ — перевод износа в литры (fleet.TruckNorms.fuel_price)
    pace: tuple[float, float] = (1.0, 1.0)   # темп машины (№66): множитель разгрузки, множитель пути


def unit_cost(v: Vehicle) -> int:
    """Стоимость метра машины для PyVRP — литры на 100 км × 10; износ — в литрах той же цены. Без износа — ровно
    прежнее round(л/100 км × 10)."""
    if not v.wear_amd_per_km:
        return int(round(v.l100 * 10))
    return int(round((v.l100 + v.wear_amd_per_km * 100.0 / v.fuel_price) * 10))


def _sec_up(minutes: float) -> int:
    return int(math.ceil(minutes * 60.0 - 1e-6))


def _sec_down(minutes: float) -> int:
    return int(math.floor(minutes * 60.0 + 1e-6))


def solve(pieces: Sequence[Piece], km: Sequence[Sequence[float]], minutes: Sequence[Sequence[float]],
          vehicles: Sequence[Vehicle], shifts: Sequence[Shift], start: Sequence[tuple[int, list[list[int]]]],
          load_cap: float | None, iterations: int = ITERATIONS, seed: int = SEED,
          load_fixed_min: float = 0.0, load_tonne_min: float = 0.0, trip_reserve_min: float = 0.0
          ) -> list[tuple[int, list[list[int]]]] | None:
    """Рейсы промежутков: [(номер промежутка в shifts, рейсы — номера pieces по порядку объезда)] или None —
    PyVRP нет, решение недопустимо или обязательный заказ не поставлен. start — план сборки в том же виде
    (стартовое решение). trip_reserve_min — запас типичного рейса (№66), минут на рейс."""
    if pyvrp is None or not pieces or not shifts:
        return None
    try:
        return _solve(pieces, km, minutes, vehicles, shifts, start, load_cap, iterations, seed,
                      load_fixed_min, load_tonne_min, trip_reserve_min)
    except Exception:   # noqa: BLE001 — сбой решателя не должен ломать «Развоз»: свой расчёт
        logger.exception('[Routes] PyVRP: сбой, рейсы — своим расчётом')
        return None


def _solve(pieces, km, minutes, vehicles, shifts, start, load_cap, iterations, seed, load_fixed_min=0., load_tonne_min=0.,
           trip_reserve_min=0.):
    by_code = {v.code: v for v in vehicles}
    model = Model()
    loc0 = model.add_location(0.0, 0.0)
    horizon = max(_sec_down(s.end) for s in shifts)
    depot = model.add_depot(loc0, tw_early=0, tw_late=horizon)
    locs = [model.add_location(0.0, 0.0) for _ in pieces]

    def top(p: Piece) -> float:
        """Самая большая машина, что может везти заказ (тоннаж, центр)."""
        return max((v.capacity_kg for v in vehicles if v.capacity_kg >= p.kg - 1e-9 and (v.center_ok or not p.center)
                    and (p.allowed_trucks is None or v.code in p.allowed_trucks)),
                   default=0.0)

    for p, loc in zip(pieces, locs):
        grams = int(math.ceil(p.kg * 1000.0 - 1e-6))
        delivery = [grams]
        if load_cap is not None:
            delivery.append(grams if p.kg <= load_cap * top(p) + 1e-9 else 0)
        early = 0 if p.early is None else max(0, _sec_up(p.early))
        late = horizon if p.late is None else min(horizon, _sec_down(p.late))
        model.add_client(loc, delivery=delivery, service_duration=_sec_up(p.unload), tw_early=early,
                         tw_late=max(early, late), required=p.required, prize=0 if p.required else PRIZE)
    nodes = [0, *(p.node for p in pieces)]
    central = [False, *(p.center for p in pieces)]
    # Тяжёлое исключение не превращает весь рейс в разрешённую загрузку 100%:
    # такой заказ едет один, соседние клиенты отделены возвратом на склад.
    solo = [False, *(load_cap is not None and p.kg > load_cap * top(p) + 1e-9 for p in pieces)]
    every = [loc0, *locs]
    # Профиль объединяет машины с одинаковым набором запрещённых магазинов.
    masks = {v.code: (False, *(bool(p.center and not v.center_ok) or
                              (p.allowed_trucks is not None and v.code not in p.allowed_trucks)
                              for p in pieces)) for v in vehicles}
    open_mask = (False,) * (len(pieces) + 1)
    # …и одинаковым темпом (№66; у всех (1, 1) — профили те же, что без темпа)
    keys = {v.code: (masks[v.code], tuple(v.pace)) for v in vehicles}
    profiles = {key: model.add_profile(name=f'access-{i}')
                for i, key in enumerate(dict.fromkeys([(open_mask, (1.0, 1.0)), *keys.values()]))}
    for a, la in enumerate(every):
        ka, ma = km[nodes[a]], minutes[nodes[a]]
        for b, lb in enumerate(every):
            if a == b:
                continue
            dist = int(math.ceil(ka[nodes[b]] * 1000.0 - 1e-6))
            if a and b and (solo[a] or solo[b]):
                dist = FORBIDDEN_M
            # Генератор распределяет погрузку тонн по входящим дугам клиентов.
            # Загрузка всего рейса до выезда перепроверяется точным расписанием fleet.
            loading = (load_fixed_min if a == 0 else 0.) + (load_tonne_min * pieces[b-1].kg/1000 if b else 0.)
            dur = _sec_up(ma[nodes[b]] + loading)
            back = trip_reserve_min if a and not b else 0.0      # рейс кончается ребром «заказ → склад»
            for (mask, (mu, mt)), profile in profiles.items():
                edge = dur
                if back or (mu, mt) != (1.0, 1.0):
                    extra = (mu - 1.0) * pieces[a - 1].unload if a else 0.0
                    edge = _sec_up(max(0.0, ma[nodes[b]] * mt + loading + extra) + back)
                model.add_edge(la, lb, distance=FORBIDDEN_M if mask[a] or mask[b] else dist, duration=edge,
                               profile=profile)
    for s in shifts:
        v = by_code[s.truck]
        cap = [int(math.floor(v.capacity_kg * 1000.0 + 1e-6))]
        if load_cap is not None:
            cap.append(int(math.floor(v.capacity_kg * 1000.0 * load_cap + 1e-6)))
        t0, t1 = _sec_up(s.start), _sec_down(s.end)
        model.add_vehicle_type(1, capacity=cap, start_depot=depot, end_depot=depot, tw_early=t0, tw_late=max(t0, t1),
                               shift_duration=max(0, t1 - t0), unit_distance_cost=unit_cost(v),
                               profile=profiles[keys[v.code]], reload_depots=[depot],
                               max_distance=MAX_DISTANCE_M, name=s.truck)
    data = model.data()
    routes = []
    for k, trips in start:
        acts: list = []
        for n, seq in enumerate(t for t in trips if t):
            if n:
                acts.append(Activity(ActivityType.DEPOT, 0))
            acts += [Activity(ActivityType.CLIENT, c) for c in seq]
        if acts:
            routes.append(Route(data, acts, k))
    initial = Solution(data, routes) if routes else None
    n = max(MIN_ITERATIONS, min(iterations, PER_ORDER * len(pieces)))
    res = model.solve(MaxIterations(n), seed=seed, display=False, collect_stats=False, initial_solution=initial)
    best = res.best
    if not best.is_feasible():
        return None
    out: list[tuple[int, list[list[int]]]] = []
    for r in best.routes():
        trips: list[list[int]] = [[]]
        for act in r.schedule():
            if act.is_depot():
                if trips[-1]:
                    trips.append([])
            elif act.is_client():
                trips[-1].append(act.idx)
            else:   # pragma: no cover — других действий в постановке нет
                return None
        out.append((r.vehicle_type(), [t for t in trips if t]))
    logger.info('[Routes] PyVRP: %d заказов, %d промежутков, %d итераций, %.1f с', len(pieces), len(shifts), n,
                res.runtime)
    return out

# -*- coding: utf-8 -*-
"""«Առաքման արժեք» — стоимость обслуживания магазина (ответ владельца №87, п. 6; docs/plans/pro-features-87-plan.md §6).

Чистый расчёт без Flask, БД и ERP: рейсы планов дней + накладные ERP + модель расхода (функция cost) → ֏ доставки каждого
магазина за период против его продаж. Себестоимости в ERP нет — владелец сравнивает доставку с продажами и своей средней
наценкой (настройка cts_margin_pct): магазин, где доставка съедает больше наценки, — красный.

Источник рейсов (views._cost_plans): за день — план, отправленный водителям (Draft.sent, №81: что водители получили);
день без отправленного плана — сохранённый черновик дня. Так же читают план обучение и замеры (dispatch.sent_json:
выпущенный — отправленный снимок, иначе сам черновик): до №73/№80 (05–06.10) планы не утверждались, без черновиков
история была бы пустой. Источник каждого дня — в ответе (sources: sent / draft / broken). Цены — сегодняшние (дизель
настроек, износ гаража на сегодня, ставки «Աշխատավարձ»): отчёт отвечает «сколько стоит обслуживать магазин сейчас».

Рейс — машина и магазины по порядку объезда; магазин без координаты в путь не входит (его доля дизеля и износа — 0, экипаж —
как у всех). Груз точки — кг накладных экспедиторов магазину за день (deliveries) / k, k — в скольких рейсах дня магазин
(тяжёлый заказ — несколько поездок поровну, как dispatch.plan_view).

    C        = дизель ֏ + износ ֏ рейса «склад → магазины → склад» — модель plan_view (fl.trip_running_cost: литры с
               рельефом №85, как показанные; цена дизеля — настройки; износ ֏/км — гараж); считает функция cost
    T        = money(C) — целые драмы рейса
    C₋ᵢ      = то же без магазина i (порядок остальных тот же, груза i нет)
    wᵢ       = max(C − C₋ᵢ, SAVING_FLOOR · C / n)   — экономия от удаления магазина; ноль и минус (магазин по пути, в том же
               доме, что соседний) — не ноль, а малый пол: магазин рейса не бывает бесплатным, деления на ноль нет
    fuelᵢ    = наибольшие остатки (T, w)            — Σ fuelᵢ = T ровно
    detourᵢ  = max(0, км − км₋ᵢ)                    — крюк ради магазина (для страницы)
Экипаж — ставки «Աշխատավարձ» (crew_pay.Params: за точку и за тонну), прямо магазину за день:
    crew(d, c) = money(RATE_POINT + RATE_TONNE · кг / 1000), если у магазина в день d есть учтённая накладная экспедитора
               (как точка crew_pay: кг > KEEP_KG или сумма > KEEP_SUM; без линий excluded_lines и накладных, которые вёз
               сам менеджер), иначе 0; в k рейсах дня — поровну наибольшими остатками: Σ по рейсам = crew(d, c).
    Фикс и минимум месяца не распределяются: они не зависят от магазинов (сноска на странице).
Магазин за период:
    visits  = дней, когда магазин в рейсах (тяжёлый магазин в двух рейсах дня — 1 визит)
    cost    = Σ fuel + Σ crew        — инвариант: Σ cost всех магазинов = Σ T рейсов + Σ crew(d, c), без двойного счёта
    sales   = Σ SALES.fTOTALSUM магазину за дни периода, у которых есть план с рейсами (все проведённые накладные — и те,
              что вёз менеджер): доставку дней без плана не знаем — их продажи занизили бы % (накладная — в день доставки)
    pct     = cost / sales · 100; sales ≤ 0 — процента нет
    red     = наценка задана и (pct > наценки или (sales ≤ 0 и cost > 0))
Итог: % от продаж — от продаж обслуженных магазинов (в рейсах периода), а не всей компании.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Collection, Mapping, Sequence

from .crew_pay import KEEP_KG, KEEP_SUM, Params, money
from .geo import Point

PRESET_DAYS = (30, 90)     # кнопки периода
MAX_DAYS = 92              # период «с — по» не длиннее (≈ квартал)
SAVING_FLOOR = 0.01        # пол экономии магазина: 1 % средней доли рейса (C / n)


# --- Данные ERP ---

@dataclass(frozen=True)
class Sale:
    """Накладные магазину за день одной линии (менеджера) — строка erp.SQL_COST_SALES."""
    day: date
    customer_id: int
    line_id: int        # SALES.fSALESAGENTID
    crew: bool          # вёз экспедитор (fVANAGENTID ≠ 0 и ≠ менеджер) — как SQL_CREW_PAY
    total: float        # Σ fTOTALSUM
    kg: float


@dataclass(frozen=True)
class SalesData:
    sales: tuple[Sale, ...]
    agents: Mapping[int, str]                     # fID → код менеджера (линии excluded_lines)
    customers: Mapping[int, tuple[str, str]]      # клиент → (код, имя)


@dataclass(frozen=True)
class Delivery:
    kg: float           # кг накладных экспедиторов магазину за день
    point: bool         # учтённая точка crew_pay — экипажу за неё платят


def deliveries(data: SalesData, excluded_lines: Collection[str]) -> dict[tuple[date, int], Delivery]:
    """(день, клиент) → груз и точка экипажа: накладные экспедиторов без линий excluded_lines (коды без учёта регистра)."""
    off = {c.strip().upper() for c in excluded_lines}
    lines = {aid for aid, code in data.agents.items() if code.strip().upper() in off}
    kg: dict[tuple[date, int], list[float]] = defaultdict(list)
    total: dict[tuple[date, int], list[float]] = defaultdict(list)
    for s in data.sales:
        if s.crew and s.line_id not in lines:
            kg[(s.day, s.customer_id)].append(s.kg)
            total[(s.day, s.customer_id)].append(s.total)
    out = {}
    for key in kg:
        w, t = math.fsum(kg[key]), math.fsum(total[key])
        out[key] = Delivery(max(0.0, w), w > KEEP_KG or t > KEEP_SUM)
    return out


def sales_by_customer(data: SalesData, days: Collection[date] | None = None) -> dict[int, float]:
    """Клиент → Σ продаж; days — только за эти дни (дни с планом), None — за все."""
    out: dict[int, list[float]] = defaultdict(list)
    for s in data.sales:
        if days is None or s.day in days:
            out[s.customer_id].append(s.total)
    return {c: math.fsum(v) for c, v in out.items()}


# --- Деньги ---

def largest_remainder(total: int, weights: Sequence[float]) -> list[int]:
    """total целых драмов по весам: доли округляются вниз, остаток — по драму наибольшим дробным частям (при равенстве — раньше
    в списке). Σ = total ровно. Все веса нулевые — поровну."""
    n = len(weights)
    if total < 0 or any(not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError('сумма и веса должны быть неотрицательными')
    if n == 0:
        if total:
            raise ValueError('некому распределить сумму')
        return []
    s = math.fsum(weights)
    if s <= 0:
        weights, s = [1.0] * n, float(n)
    quotas = [total * w / s for w in weights]
    out = [min(total, math.floor(q)) for q in quotas]
    left = total - sum(out)
    for i in sorted(range(n), key=lambda i: (-(quotas[i] - out[i]), i))[:max(0, left)]:
        out[i] += 1
    if sum(out) != total:   # инвариант денег: не теряем и не добавляем драм
        raise ArithmeticError('распределение не сошлось')
    return out


# --- Рейсы ---

@dataclass(frozen=True)
class Trip:
    day: date
    trip_id: int
    truck: str
    stops: tuple[int, ...]   # клиенты по порядку объезда


@dataclass(frozen=True)
class Leg:
    """Расход рейса по модели plan_view: ֏ (дизель + износ) и км."""
    amd: float
    km: float


# машина, точки по порядку, кг точек → расход; None — машины нет среди готовых к расчёту (норм нет)
CostFn = Callable[[str, Sequence[Point], Sequence[float]], Leg | None]


@dataclass(frozen=True)
class StopShare:
    customer_id: int
    fuel: int            # дизель + износ, ֏
    crew: int            # экипаж, ֏
    detour_km: float
    routed: bool         # в пути (есть координата)

    @property
    def total(self) -> int:
        return self.fuel + self.crew


@dataclass(frozen=True)
class TripCost:
    day: date
    trip_id: int
    truck: str
    km: float
    fuel: int            # T — Σ fuel магазинов
    crew: int            # Σ crew магазинов
    priced: bool         # машина с нормами; нет — дизеля и износа нет (0), только экипаж
    stops: tuple[StopShare, ...]


def trip_costs(trips: Sequence[Trip], delivered: Mapping[tuple[date, int], Delivery],
               coords: Mapping[int, Point | None], cost: CostFn, params: Params) -> list[TripCost]:
    """Рейсы → доли магазинов (формулы — в шапке модуля). Повтор магазина в рейсе схлопывается, пустой рейс пропускается."""
    trips = [Trip(t.day, t.trip_id, t.truck, tuple(dict.fromkeys(t.stops))) for t in trips]
    trips = [t for t in trips if t.stops]
    where: dict[tuple[date, int], list[int]] = defaultdict(list)   # (день, клиент) → номера рейсов
    for i, t in enumerate(trips):
        for c in t.stops:
            where[(t.day, c)].append(i)
    crew: dict[tuple[int, date, int], int] = {}
    for (day, c), idx in where.items():
        d = delivered.get((day, c))
        amount = money(params.rate_point + params.rate_tonne * d.kg / 1000.0) if d is not None and d.point else 0
        for i, part in zip(idx, largest_remainder(amount, [1.0] * len(idx))):
            crew[(i, day, c)] = part
    out = []
    for i, t in enumerate(trips):
        routed = [c for c in t.stops if coords.get(c) is not None]
        pts = [coords[c] for c in routed]   # type: ignore[misc]
        kgs = [delivered[(t.day, c)].kg / len(where[(t.day, c)]) if (t.day, c) in delivered else 0.0 for c in routed]
        full = cost(t.truck, pts, kgs)
        fuel: dict[int, int] = {}
        detour: dict[int, float] = {}
        if full is not None and routed:
            total = money(full.amd)
            without = [cost(t.truck, pts[:j] + pts[j + 1:], kgs[:j] + kgs[j + 1:]) for j in range(len(routed))]
            floor = SAVING_FLOOR * full.amd / len(routed)
            weights = [max(full.amd - w.amd, floor) if w is not None else floor for w in without]
            fuel = dict(zip(routed, largest_remainder(total, weights)))
            detour = {c: max(0.0, full.km - w.km) if w is not None else 0.0 for c, w in zip(routed, without)}
        on_road = set(routed)
        shares = tuple(StopShare(c, fuel.get(c, 0), crew[(i, t.day, c)], detour.get(c, 0.0), c in on_road)
                       for c in t.stops)
        out.append(TripCost(t.day, t.trip_id, t.truck, full.km if full is not None else 0.0,
                            sum(s.fuel for s in shares), sum(s.crew for s in shares), full is not None, shares))
    return out


# --- Отчёт ---

@dataclass(frozen=True)
class StoreVisit:
    day: date
    trip_id: int
    truck: str
    fuel: int
    crew: int
    detour_km: float
    trip_fuel: int        # T рейса — для сравнения доли
    trip_stops: int


@dataclass(frozen=True)
class StoreRow:
    customer_id: int
    visits: int
    fuel: int
    crew: int
    sales: float
    pct: float | None
    red: bool
    trips: tuple[StoreVisit, ...]
    unrouted: bool        # хоть раз без координаты — дизель и износ ему не начислены

    @property
    def cost(self) -> int:
        return self.fuel + self.crew

    @property
    def per_visit(self) -> float:
        return self.cost / self.visits if self.visits else 0.0


@dataclass(frozen=True)
class Report:
    rows: tuple[StoreRow, ...]     # по ֏ доставки, дорогие первыми
    fuel: int
    crew: int
    sales: float                   # продажи обслуженных магазинов
    pct: float | None
    red: int
    trips: int
    unpriced_trips: int            # машины без норм — их дизеля и износа нет в итоге


def flag(cost: int, sales: float, margin: float | None) -> tuple[float | None, bool]:
    """(% доставки от продаж, красный ли): без наценки — без красного."""
    pct = cost / sales * 100.0 if sales > 0 else None
    if margin is None:
        return pct, False
    return pct, (pct > margin) if pct is not None else cost > 0


def report(trips: Sequence[TripCost], sales: Mapping[int, float], margin: float | None) -> Report:
    visits: dict[int, list[StoreVisit]] = defaultdict(list)
    unrouted: set[int] = set()
    for t in trips:
        for s in t.stops:
            visits[s.customer_id].append(StoreVisit(t.day, t.trip_id, t.truck, s.fuel, s.crew, s.detour_km, t.fuel,
                                                    len(t.stops)))
            if not s.routed:
                unrouted.add(s.customer_id)
    rows = []
    for c, vs in visits.items():
        fuel, crew = sum(v.fuel for v in vs), sum(v.crew for v in vs)
        pct, red = flag(fuel + crew, sales.get(c, 0.0), margin)
        rows.append(StoreRow(c, len({v.day for v in vs}), fuel, crew, sales.get(c, 0.0), pct, red,
                             tuple(sorted(vs, key=lambda v: (v.day, v.trip_id))), c in unrouted))
    rows.sort(key=lambda r: (-r.cost, r.customer_id))
    fuel, crew = sum(r.fuel for r in rows), sum(r.crew for r in rows)
    served = math.fsum(r.sales for r in rows)
    if fuel != sum(t.fuel for t in trips) or crew != sum(t.crew for t in trips):   # без двойного счёта и потерь
        raise ArithmeticError('доли магазинов не сходятся с рейсами')
    return Report(tuple(rows), fuel, crew, served, (fuel + crew) / served * 100.0 if served > 0 else None,
                  sum(1 for r in rows if r.red), len(trips), sum(1 for t in trips if not t.priced))


# --- Проверка ввода ---

def check_margin(raw: Any) -> tuple[float | None, str | None]:
    """Средняя наценка, %: пусто (None, '') — красного нет; иначе число 0–100."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    if isinstance(raw, bool):
        return None, 'Լրացրեք թիվը'
    try:
        x = float(raw.strip().replace(',', '.')) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError, OverflowError):
        return None, 'Լրացրեք թիվը'
    if not math.isfinite(x) or not 0 <= x <= 100:
        return None, 'Թույլատրելի է 0-ից 100'
    return x, None


def period(days: str | None, since: str | None, until: str | None, today: date) -> tuple[tuple[date, date] | None, str | None]:
    """Период отчёта (включительно): «с — по» (since, until: ГГГГ-ММ-ДД; по — не позже сегодня, не длиннее MAX_DAYS) или
    days из PRESET_DAYS — столько полных дней по вчера; ничего — PRESET_DAYS[0]."""
    if since or until:
        try:
            a, b = date.fromisoformat(since or ''), date.fromisoformat(until or '')
        except ValueError:
            return None, 'Ամսաթվերը՝ ՏՏՏՏ-ԱԱ-ՕՕ'
        if a > b:
            return None, 'Սկիզբը վերջից հետո է'
        if b > today:
            return None, 'Վերջը չի կարող լինել այսօրվանից հետո'
        if (b - a).days + 1 > MAX_DAYS:
            return None, f'Ոչ ավելի, քան {MAX_DAYS} օր'
        return (a, b), None
    n = PRESET_DAYS[0] if not days else int(days) if days.isdigit() else 0
    if n not in PRESET_DAYS:
        return None, 'Ընտրեք 30 կամ 90 օր'
    return (today - timedelta(days=n), today - timedelta(days=1)), None

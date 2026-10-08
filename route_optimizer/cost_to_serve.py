# -*- coding: utf-8 -*-
"""«Առաքման արժեք» — стоимость обслуживания магазина (ответ владельца №87, п. 6; docs/plans/pro-features-87-plan.md §6).

Чистый расчёт без Flask, БД и ERP: рейсы планов дней + накладные ERP + модель расхода (функция cost) → ֏ доставки каждого
магазина за период против его продаж. Себестоимости в ERP нет — владелец сравнивает доставку с продажами и своей средней
наценкой (настройка cts_margin_pct): магазин, где доставка съедает больше наценки, — красный.

Источник рейсов (views._cost_plans, plan_source): за день — план, отправленный водителям (Draft.sent, №81: что водители
получили). Сохранённый черновик — только за прошедшие дни РАНЬШЕ первого дня, когда хоть один план был отправлен: до
№73/№80/№81 (05–06.10) планы не утверждались и не отправлялись, без черновиков история была бы пустой; с первого
отправленного дня неотправленный черновик — не доставка (план могли не исполнить), такой день не считается (unsent).
Сегодня и будущее — только отправленный план. Источник каждого дня — в ответе (sources: sent / draft / unsent / broken).
Цены — сегодняшние (дизель настроек, износ гаража на сегодня, ставки «Աշխատավարձ»): отчёт отвечает «сколько стоит
обслуживать магазин сейчас».

Рейс — машина и магазины по порядку объезда; магазин без координаты в путь не входит (его доля дизеля и износа — 0, экипаж —
как у всех). Координата — как у «Развоза» (dispatch.build_stops ← views._load_day: evaluate.visit_coord с адресом 0 —
ручная точка, точка водителей, адрес клиента по умолчанию, GPS): адреса заказа в плане нет, а у одного клиента в одном
рейсе — одна точка. Повтор магазина в рейсе (A → B → A) схлопывается до первого, как dispatch._clean перед plan_view: план
«Развоза» таких рейсов не содержит.
Груз точки — кг проведённых накладных экспедиторов магазину за день (deliveries; без линий excluded_lines) / k, k — в
скольких рейсах дня магазин (тяжёлый заказ — несколько поездок поровну, как dispatch.plan_view). Не кг заказов плана: план
хранит только клиентов рейса, а заказы прошлого дня пришлось бы читать из ERP заново по дню (≈90 запросов) и они не
равны отгруженному; накладная проведена в день доставки и это то, что машина везла на самом деле.

    C        = дизель ֏ + износ ֏ рейса «склад → магазины → склад» — модель plan_view (fl.trip_running_cost: литры с
               рельефом №85, как показанные; цена дизеля — настройки; износ ֏/км — гараж); считает функция cost
    T        = money(C) — целые драмы рейса
    C₋ᵢ      = то же без магазина i (порядок остальных тот же, груза i нет)
    wᵢ       = max(C − C₋ᵢ, SAVING_FLOOR · C / n)   — экономия от удаления магазина; ноль и минус (магазин по пути, в том же
               доме, что соседний) — не ноль, а малый пол: магазин рейса не бывает бесплатным, деления на ноль нет
    fuelᵢ    = наибольшие остатки (T, w)            — Σ fuelᵢ = T ровно
    detourᵢ  = max(0, км − км₋ᵢ)                    — крюк ради магазина (для страницы)
Экипаж — ставки «Աշխատավարձ» (crew_pay.Params: за точку, тонну и км), магазину за день, по правилам crew_pay.compute:
    ячейка  = (экспедитор, день, магазин) — накладные, которые вёз экспедитор (fVANAGENTID ≠ 0 и ≠ менеджер), без линий
              excluded_lines и экспедиторов excluded_people (все fID кода — код в SALESAGENTS может повторяться);
              учтена, если кг > KEEP_KG или сумма > KEEP_SUM
    crew(d, c) = max(0, money(RATE_POINT · точек + RATE_TONNE · Σ кг учтённых ячеек / 1000)) + km(d, c), точек —
              экспедиторов с учтённой ячейкой (двое вёзли одному магазину в день — две точки, как в их зарплате);
    km(d, c) — плата за км (crew_km): зарплата платит RATE_KM × км плановых туров — за день человека (экспедитора) один
              замкнутый тур склад → его магазины дня (учтённые ячейки, с координатой) → склад, порядок tsp.solve_tour,
              км по дорогам раздела (без них — по прямой × crew_pay.KM_DETOUR): ровно crew_pay.tour_km. Не км рейсов плана:
              платят за тур человека, а не за рейсы машины. Тур дня делится по точкам так же, как дизель рейса:
                  K      = money(RATE_KM × км тура)
                  wⱼ     = max(км − км₋ⱼ, SAVING_FLOOR · км / n)   — км, которые точка j добавляет к туру (порядок тот же)
                  точкаⱼ = наибольшие остатки (K, w), Σ = K ровно; магазины одной точки (один дом) — поровну
              магазин без координаты — 0 км (как в зарплате). Σ km(d, c) по магазинам = Σ K туров — равно км-части зарплаты
              в пределах округления (зарплата округляет месяц человека, здесь — тур дня); доля магазина, которого нет в
              рейсах плана этого дня, в отчёт не попадает (как и его точки и тонны);
    в k рейсах дня — поровну наибольшими остатками: Σ по рейсам = crew(d, c).
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

from . import tsp
from .crew_pay import KEEP_KG, KEEP_SUM, KM_DETOUR, Params, money
from .geo import Point, haversine_km

PRESET_DAYS = (30, 90)     # кнопки периода
MAX_DAYS = 92              # период «с — по» не длиннее (≈ квартал)
SAVING_FLOOR = 0.01        # пол экономии магазина: 1 % средней доли рейса (C / n)


# --- Данные ERP ---

@dataclass(frozen=True)
class Sale:
    """Накладные магазину за день одной линии (менеджера) и одного экспедитора — строка erp.SQL_COST_SALES."""
    day: date
    customer_id: int
    line_id: int        # SALES.fSALESAGENTID (0 — нет)
    van_id: int         # SALES.fVANAGENTID (0 — нет)
    total: float        # Σ fTOTALSUM
    kg: float

    @property
    def crew(self) -> bool:
        """Вёз экспедитор, а не сам менеджер — как SQL_CREW_PAY."""
        return self.van_id != 0 and self.van_id != self.line_id


@dataclass(frozen=True)
class SalesData:
    sales: tuple[Sale, ...]
    agents: Mapping[int, str]                     # fID → код SALESAGENTS (линии excluded_lines, люди excluded_people)
    customers: Mapping[int, tuple[str, str]]      # клиент → (код, имя)


@dataclass(frozen=True)
class Delivery:
    kg: float               # груз: кг накладных экспедиторов магазину за день (без линий excluded_lines), не меньше 0
    points: int = 0         # точек crew_pay: экспедиторов с учтённой ячейкой (без excluded_people)
    crew_kg: float = 0.0    # кг учтённых ячеек — тонны зарплаты
    km_amd: int = 0         # доля магазина в плате за км туров дня (crew_km), целые ֏

    def crew_amd(self, p: Params) -> int:
        """Экипаж за магазин в этот день, целые ֏: точки и тонны (минус — возвраты — не доплачиваем: 0) + доля км."""
        piece = max(0, money(p.rate_point * self.points + p.rate_tonne * self.crew_kg / 1000.0)) if self.points else 0
        return piece + self.km_amd


def _codes(data: SalesData, codes: Collection[str]) -> set[int]:
    """fID кодов SALESAGENTS (без учёта регистра и пробелов по краям; код может повторяться — все его fID)."""
    by_code: dict[str, set[int]] = defaultdict(set)
    for aid, code in data.agents.items():
        by_code[code.strip().upper()].add(aid)
    return {aid for c in codes for aid in by_code.get(c.strip().upper(), ())}


def _crew_cells(data: SalesData, excluded_lines: Collection[str], excluded_people: Collection[str]
                ) -> tuple[dict[tuple[date, int], list[float]], dict[tuple[date, int], dict[int, tuple[float, float]]]]:
    """(груз: (день, клиент) → кг накладных экспедиторов без линий excluded_lines; учтённые ячейки crew_pay: (день,
    клиент) → экспедитор → (кг, сумма), без excluded_people, с порогами KEEP)."""
    lines, people = _codes(data, excluded_lines), _codes(data, excluded_people)
    cargo: dict[tuple[date, int], list[float]] = defaultdict(list)
    raw: dict[tuple[date, int], dict[int, list[list[float]]]] = defaultdict(lambda: defaultdict(lambda: [[], []]))
    for s in data.sales:
        if not s.crew or s.line_id in lines:
            continue
        cargo[(s.day, s.customer_id)].append(s.kg)
        if s.van_id not in people:
            cell = raw[(s.day, s.customer_id)][s.van_id]
            cell[0].append(s.kg)
            cell[1].append(s.total)
    kept: dict[tuple[date, int], dict[int, tuple[float, float]]] = {}
    for key, per in raw.items():
        sums = {van: (math.fsum(w), math.fsum(t)) for van, (w, t) in per.items()}
        sums = {van: v for van, v in sums.items() if v[0] > KEEP_KG or v[1] > KEEP_SUM}
        if sums:
            kept[key] = sums
    return cargo, kept


def deliveries(data: SalesData, excluded_lines: Collection[str], excluded_people: Collection[str] = (),
               km: Mapping[tuple[date, int], int] | None = None) -> dict[tuple[date, int], Delivery]:
    """(день, клиент) → груз, точки экипажа по правилам crew_pay.compute и доля платы за км (km — crew_km) — шапка модуля."""
    cargo, kept = _crew_cells(data, excluded_lines, excluded_people)
    out = {}
    for key, kgs in cargo.items():
        cells = kept.get(key, {})
        out[key] = Delivery(max(0.0, math.fsum(kgs)), len(cells), math.fsum(w for w, _ in cells.values()),
                            (km or {}).get(key, 0))
    return out


# одинаковые точки тура → (км, порядок объезда): туры повторяются из запроса в запрос (CSV, другой период)
TourMemo = dict[tuple[Point, ...], tuple[float, tuple[int, ...]]]


def crew_km(data: SalesData, excluded_lines: Collection[str], excluded_people: Collection[str], rate_km: float,
            depot: Point, coords: Mapping[int, Point | None], road_km: Callable[[Point, Point], float | None],
            memo: TourMemo | None = None, days: Collection[date] | None = None) -> dict[tuple[date, int], int]:
    """(день, клиент) → доля платы за км туров экспедиторов этого дня, целые ֏ (формулы — в шапке модуля). Тур — как
    crew_pay.tour_km: точки дня человека без повторов, отсортированы, tsp.solve_tour, км по road_km, без него — по прямой ×
    KM_DETOUR. rate_km ≤ 0 — пусто (км не платят). days — только эти дни (дни с планом: другие отчёт не показывает)."""
    if rate_km <= 0:
        return {}

    def dist(a: Point, b: Point) -> float:
        v = road_km(a, b)
        return v if v is not None else haversine_km(a, b) * KM_DETOUR

    tours: dict[tuple[int, date], set[int]] = defaultdict(set)
    for (day, c), per in _crew_cells(data, excluded_lines, excluded_people)[1].items():
        if days is not None and day not in days:
            continue
        for van in per:
            tours[(van, day)].add(c)
    out: dict[tuple[date, int], int] = defaultdict(int)
    for (_, day), stores in sorted(tours.items()):
        at: dict[Point, list[int]] = defaultdict(list)
        for c in sorted(stores):
            if coords.get(c) is not None:
                at[coords[c]].append(c)   # type: ignore[index]
        pts = tuple(sorted(at))
        if not pts:
            continue
        hit = memo.get(pts) if memo is not None else None
        if hit is None:
            order = tsp.solve_tour(depot, list(pts), dist)
            path = [depot, *(pts[i] for i in order), depot]
            hit = (math.fsum(dist(a, b) for a, b in zip(path, path[1:])), tuple(order))
            if memo is not None:
                memo[pts] = hit
        km, order = hit
        path = [depot, *(pts[i] for i in order), depot]
        floor = SAVING_FLOOR * km / len(order)
        weights = [max(dist(path[j], path[j + 1]) + dist(path[j + 1], path[j + 2]) - dist(path[j], path[j + 2]), floor)
                   for j in range(len(order))]
        for j, amd in zip(order, largest_remainder(money(rate_km * km), weights)):
            for c, part in zip(at[pts[j]], largest_remainder(amd, [1.0] * len(at[pts[j]]))):
                out[(day, c)] += part
    return dict(out)


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
        amount = d.crew_amd(params) if d is not None else 0
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


def plan_source(day: date, sent: bool, first_sent: date | None, today: date) -> str | None:
    """Чем считать день (шапка модуля): 'sent' — отправленный план; 'draft' — черновик прошедшего дня раньше первого
    отправленного (first_sent; None — ни одного ещё не было); None — не считать (неотправленный черновик с первого
    отправленного дня, сегодня или будущего)."""
    if sent:
        return 'sent'
    return 'draft' if day < today and (first_sent is None or day < first_sent) else None


def period(days: str | None, since: str | None, until: str | None, today: date) -> tuple[tuple[date, date] | None, str | None]:
    """Период отчёта (включительно): «с — по» (since, until: ГГГГ-ММ-ДД; по — не позже сегодня, не длиннее MAX_DAYS) или
    days из PRESET_DAYS — столько полных дней по вчера; ничего — PRESET_DAYS[0]."""
    if since or until:
        try:
            if not (since or '').isascii() or not (until or '').isascii():
                raise ValueError
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
    # только ASCII-цифры: «²» и «٣» — isdigit(), но int() их не берёт (было 500)
    n = PRESET_DAYS[0] if not days else int(days) if days.isascii() and days.isdecimal() and len(days) < 4 else 0
    if n not in PRESET_DAYS:
        return None, 'Ընտրեք 30 կամ 90 օր'
    return (today - timedelta(days=n), today - timedelta(days=1)), None

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
Экипаж — ставки «Աշխատավարձ» (crew_pay.Params: за точку и за тонну), прямо магазину за день, по правилам crew_pay.compute:
    ячейка  = (экспедитор, день, магазин) — накладные, которые вёз экспедитор (fVANAGENTID ≠ 0 и ≠ менеджер), без линий
              excluded_lines и экспедиторов excluded_people (все fID кода — код в SALESAGENTS может повторяться);
              учтена, если кг > KEEP_KG или сумма > KEEP_SUM
    crew(d, c) = max(0, money(RATE_POINT · точек + RATE_TONNE · Σ кг учтённых ячеек / 1000)), точек — экспедиторов с
              учтённой ячейкой (двое вёзли одному магазину в день — две точки, как в их зарплате);
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

from .crew_pay import KEEP_KG, KEEP_SUM, Params, money
from .geo import Point

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

    def crew_amd(self, p: Params) -> int:
        """Сдельная часть экипажа за магазин в этот день, целые ֏ (минус — возвраты — не доплачиваем: 0)."""
        return max(0, money(p.rate_point * self.points + p.rate_tonne * self.crew_kg / 1000.0)) if self.points else 0


def deliveries(data: SalesData, excluded_lines: Collection[str],
               excluded_people: Collection[str] = ()) -> dict[tuple[date, int], Delivery]:
    """(день, клиент) → груз и точки экипажа по правилам crew_pay.compute (шапка модуля). Коды — без учёта регистра и
    пробелов по краям, каждый — все свои fID."""
    by_code: dict[str, set[int]] = defaultdict(set)
    for aid, code in data.agents.items():
        by_code[code.strip().upper()].add(aid)
    lines = {aid for c in excluded_lines for aid in by_code.get(c.strip().upper(), ())}
    people = {aid for c in excluded_people for aid in by_code.get(c.strip().upper(), ())}
    cargo: dict[tuple[date, int], list[float]] = defaultdict(list)
    cells: dict[tuple[date, int], dict[int, list[list[float]]]] = defaultdict(lambda: defaultdict(lambda: [[], []]))
    for s in data.sales:
        if not s.crew or s.line_id in lines:
            continue
        cargo[(s.day, s.customer_id)].append(s.kg)
        if s.van_id not in people:
            cell = cells[(s.day, s.customer_id)][s.van_id]
            cell[0].append(s.kg)
            cell[1].append(s.total)
    out = {}
    for key, kgs in cargo.items():
        kept = [(math.fsum(w), math.fsum(t)) for w, t in cells.get(key, {}).values()]
        kept = [w for w, t in kept if w > KEEP_KG or t > KEEP_SUM]
        out[key] = Delivery(max(0.0, math.fsum(kgs)), len(kept), math.fsum(kept))
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

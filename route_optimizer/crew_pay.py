# -*- coding: utf-8 -*-
"""«Աշխատավարձ» — зарплата առաքիչ (экспедиторов ERP) за месяц по «дешёвой» формуле владельца 07.10
(docs/research/09-crew-pay.md, раздел «Это много»; эталон — crew-pay/cheap.py, people.py).

Чистый расчёт без ввода-вывода: строки ERP (erp.load_crew_pay) + параметры → люди месяца.

Экспедитор — SALES.fVANAGENTID накладной, если он не сам менеджер накладной; накладные — проведённые за месяц.
Человек — код экспедитора. Коды с одинаковым именем (без учёта регистра и лишних пробелов) — одна строка, только если
их рабочие дни в месяце не пересекаются: человек сменил код (Հակոբյան Կարապետ — B004/19, потом B003/24). Если пересекаются —
это, скорее всего, разные люди-тёзки: строки раздельные, а в предупреждении — их коды (overlapping_codes).
Клиенто-день кода учитывается, только если в нём есть вес или сумма (kg > KEEP_KG или Σ fTOTALSUM > KEEP_SUM — не шум
float): документ из одних нулей или минусов (возврат) не создаёт ни точки, ни рабочего дня, ни тонн, ни кода в строке.
Тонны — с подарками ERP (ответ владельца №90: груз), но клиенто-день из одних подарков не учитывается (не продажа).
Отдельные документы возврата ERP не читаются — возвраты пока не вычитаются (минусовые строки внутри накладной — как в ERP).
    days    — разных дней с учтёнными клиенто-днями (по всем кодам человека);
    points  — разных (день, клиент): несколько накладных одному магазину в день — 1 точка;
    tonnes  — Σ количество × вес товара / 1000;
    D       — рабочих дней компании: разных дней с любой учтённой накладной экспедитора в месяце (в текущем — по сегодня);
    D_month — рабочих дней всего месяца: у прошлого месяца — D, у текущего — |дни с доставкой по сегодня ∪ рабочие дни
              календаря «Маршрутов» с сегодня до конца месяца| (дни недели и нерабочие даты №64; считает views):
              прошлое — по факту (доставка в воскресенье — рабочий день), будущее — по календарю. В последний день
              месяца с доставками это ровно D закрытого месяца. Без D_month за неделю работы платили бы полный фикс
              и полный минимум: текущий месяц показывает начисленное по сегодня.
Не учитываются накладные линий excluded_lines (по коду менеджера; по умолчанию линия 19 л) и экспедиторы excluded_people.

    fix     = FIX × min(1, days / D_month)
    km      — плановые км (docs/research/09-crew-pay.md, «Км в зарплату?»): за каждый день человека — замкнутый тур
              склад → магазины дня (клиенты с координатами) → склад, порядок tsp.solve_tour (NN + 2-opt), км по дорогам
              (участок без км по дорогам — по прямой × KM_DETOUR); один тур в день, без деления на рейсы по тоннажу.
              Именно плановые, а не по GPS или одометру: ездить длиннее невыгодно. Магазин без координат — 0 км (Row.no_coords).
              Дороги и координаты — у views (plan_tours); rate_km = 0 — км не считаются вовсе.
    piece   = RATE_POINT × points + RATE_TONNE × tonnes + RATE_KM × km
    minimum = MIN × min(1, points / (NORM_PER_DAY × D_month))
    pay     = max(fix + piece, minimum)
    old     = OLD_FIX × min(1, days / D_month) + OLD_PCT % × продажи      — нынешняя схема, только для сравнения

Деньги — целые драмы: fix, piece, minimum округляются по отдельности (половина — вверх), pay и old считаются из
округлённых частей, чтобы строка таблицы сходилась до драма (pay = fix + piece или ровно minimum).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, fields
from datetime import date
from typing import Any, Callable, Collection, Iterable, Mapping, MutableMapping, Sequence

from . import tsp
from .geo import Point, haversine_km


@dataclass(frozen=True)
class Params:
    fix: float = 100_000            # ֏ в месяц за полный месяц
    rate_point: float = 210         # ֏ за точку (владелец 07.10 с км; было 250, до того 175)
    rate_tonne: float = 1_500       # ֏ за тонну (владелец 07.10 с км; было 1 750, до того 1 200)
    rate_km: float = 15             # ֏ за плановый км (владелец 07.10); 0 — км не считаются
    minimum: float = 250_000        # ֏ в месяц при выполненной норме
    norm_per_day: float = 12        # точек в рабочий день — норма для минимума
    old_fix: float = 100_000        # нынешняя схема: фикс
    old_pct: float = 2.0            # нынешняя схема: % от продаж
    excluded_lines: tuple[str, ...] = ('A008/3', 'A008/6')              # линия 19 л (коды менеджеров)
    excluded_people: tuple[str, ...] = ('B008/10', 'B008/3', 'B008/4', 'A008/6')   # коды экспедиторов (как people.py)

    def json(self) -> dict[str, Any]:
        return {f.name: list(v) if isinstance(v := getattr(self, f.name), tuple) else v for f in fields(self)}


# Пределы параметров: (наименьшее, наибольшее); норма 0 запрещена — минимум делился бы на ноль
_BOUNDS: dict[str, tuple[float, float]] = {
    'fix': (0, 5_000_000), 'rate_point': (0, 100_000), 'rate_tonne': (0, 1_000_000), 'rate_km': (0, 10_000),
    'minimum': (0, 5_000_000), 'norm_per_day': (1, 200), 'old_fix': (0, 5_000_000), 'old_pct': (0, 100),
}
# Поля, добавленные позже, → значение, при котором формула прежней версии не меняется. Записи, сохранённой прежней
# версией, их нет: владелец подбирал те ставки без км — rate_km = 0, а не 15 по умолчанию (15 — только без записи)
_LEGACY = {'rate_km': 0.0}
_CODE_RE = re.compile(r'[A-Za-z0-9/._-]{1,20}')
MAX_CODES = 50


def _codes(raw: Any) -> tuple[tuple[str, ...] | None, str | None]:
    """Коды ERP списком или строкой через запятую → кортеж без повторов (верхний регистр) или ошибка."""
    if isinstance(raw, str):
        raw = raw.split(',')
    if not isinstance(raw, (list, tuple)) or not all(isinstance(c, str) for c in raw):
        return None, 'Նշեք կոդերը ստորակետով'
    out = tuple(dict.fromkeys(c.strip().upper() for c in raw if c.strip()))
    bad = [c for c in out if not _CODE_RE.fullmatch(c)]
    if bad:
        return None, f'Սխալ կոդ՝ {bad[0][:20]}'
    if len(out) > MAX_CODES:
        return None, f'Ոչ ավելի, քան {MAX_CODES} կոդ'
    return out, None


def check_params(raw: Any) -> tuple[Params | None, dict[str, str]]:
    """Параметры из формы или базы → (Params, {}) или (None, {поле: ошибка по-армянски}). Все поля обязательны, кроме
    _LEGACY: их нет (запись или форма прежней версии) — значение прежней формулы."""
    if not isinstance(raw, Mapping):
        return None, {'_': 'Պարամետրերը սխալ են'}
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, (lo, hi) in _BOUNDS.items():
        v = raw.get(name, _LEGACY.get(name))
        try:   # огромное целое из JSON во float не влезает (OverflowError) — это ошибка поля, а не 500
            x = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else math.nan
        except OverflowError:
            x = math.inf
        if math.isnan(x):
            errors[name] = 'Լրացրեք թիվը'
        elif not lo <= x <= hi:
            errors[name] = f'Թույլատրելի է {lo:g}-ից {hi:g}'
        else:
            values[name] = x
    for name in ('excluded_lines', 'excluded_people'):
        codes, err = _codes(raw.get(name))
        if err:
            errors[name] = err
        else:
            values[name] = codes
    return (None, errors) if errors else (Params(**values), {})


@dataclass(frozen=True)
class Invoice:
    """Накладные экспедитора одному клиенту за день по одной линии (менеджеру) — строка erp.SQL_CREW_PAY."""
    van_id: int
    day: date
    customer_id: int
    line_id: int        # SALES.fSALESAGENTID — менеджер (линия) накладной
    total: float        # Σ SALES.fTOTALSUM
    kg: float           # груз: строки и подарки (erp._KG_APPLY)
    gift_kg: float = 0.0   # из kg — подарки SALEDOCGIFTS (№90): клиенто-день из одних подарков — не точка оплаты


@dataclass(frozen=True)
class CrewData:
    invoices: tuple[Invoice, ...]
    agents: Mapping[int, tuple[str, str]]   # fID → (код, имя) из SALESAGENTS


@dataclass(frozen=True)
class Day:
    day: date
    points: int
    tonnes: float
    piece: int
    km: float = 0.0
    no_coords: int = 0      # точек дня без координат — их км не посчитаны


@dataclass(frozen=True)
class Tours:
    """Плановые км дней (plan_tours): магазины дня → км тура склад → магазины → склад."""
    km: Mapping[frozenset[int], float]
    no_coords: frozenset[int] = frozenset()   # клиенты без координат: 0 км


@dataclass(frozen=True)
class Row:
    agent_ids: tuple[int, ...]  # все коды ERP человека
    code: str                   # их коды через запятую
    name: str
    days: int
    points: int
    tonnes: float
    sales: int
    fix: int
    piece: int
    minimum: int
    pay: int
    old: int
    min_applied: bool       # минимум больше фикса и сдельной части — платим минимум
    by_day: tuple[Day, ...]
    km: float = 0.0         # плановые км месяца (Σ дней)
    no_coords: int = 0      # точек без координат — км по ним не посчитаны (меньше, чем на деле)

    @property
    def diff(self) -> int:
        return self.pay - self.old


@dataclass(frozen=True)
class Result:
    workdays: int                       # D; 0 — данных за месяц нет
    rows: tuple[Row, ...]
    unknown_codes: tuple[str, ...]      # коды исключений, которых нет в ERP (опечатка) — показать владельцу
    overlapping_codes: tuple[tuple[str, ...], ...] = ()   # тёзки с общими рабочими днями — раздельные строки, проверить
    excluded_kin: tuple[str, ...] = ()  # учтённые коды с тем же именем, что у исключённого: исключён не каждый код человека?
    workdays_month: int = 0             # D_month ≥ D — знаменатель фикса и нормы; у прошлого месяца = D
    km_counted: bool = False            # км в сдельной части: rate_km > 0 и туры посчитаны


def money(x: float) -> int:
    """Целые драмы, половина — вверх (round() Python округляет к чётному)."""
    return math.floor(x + 0.5)


def person_key(name: str, agent_id: int) -> str:
    """Один человек — одно имя без учёта регистра и лишних пробелов; без имени — сам код."""
    norm = ' '.join(name.split()).casefold()
    return norm or f'#{agent_id}'


KEEP_KG = 0.0005     # кг: меньше — шум float, а не груз
KEEP_SUM = 0.5       # ֏: меньше — шум float, а не продажа


Cells = dict[int, dict[tuple[date, int], list[float]]]   # код экспедитора → (день, клиент) → [кг, сумма]


def _kept(data: CrewData, p: Params) -> tuple[Cells, set[int], tuple[str, ...]]:
    """(учтённые клиенто-дни кодов, fID исключённых людей, коды исключений, которых нет в ERP)."""
    # коды ERP — без учёта регистра и пробелов по краям; в SALESAGENTS код может повторяться — исключаем все его fID
    by_code: dict[str, set[int]] = defaultdict(set)
    for aid, (code, _) in data.agents.items():
        by_code[code.strip().upper()].add(aid)
    excl_lines = [c.strip().upper() for c in p.excluded_lines]
    excl_people = [c.strip().upper() for c in p.excluded_people]
    lines = {aid for c in excl_lines for aid in by_code.get(c, ())}
    people = {aid for c in excl_people for aid in by_code.get(c, ())}
    unknown = tuple(c for c in (*excl_lines, *excl_people) if c not in by_code)

    # клиенто-день кода: [кг, сумма, кг подарков] по всем линиям; дальше — только с весом проданного или суммой: одни
    # подарки (№90; за 01.09–08.10.2026 таких накладных не было) — не продажа и не точка оплаты
    cells: dict[int, dict[tuple[date, int], list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
    for inv in data.invoices:
        if not inv.van_id or inv.van_id == inv.line_id or inv.line_id in lines or inv.van_id in people:
            continue
        cell = cells[inv.van_id][(inv.day, inv.customer_id)]
        cell[0] += inv.kg
        cell[1] += inv.total
        cell[2] += inv.gift_kg
    kept = {aid: {dc: c[:2] for dc, c in per.items() if c[0] - c[2] > KEEP_KG or c[1] > KEEP_SUM}
            for aid, per in cells.items()}
    return {aid: per for aid, per in kept.items() if per}, people, unknown


def day_stores(data: CrewData, p: Params) -> set[frozenset[int]]:
    """Магазины (клиенты) каждого рабочего дня каждого человека — то, для чего views считает туры (plan_tours). Дни кодов
    одного человека не пересекаются (иначе это тёзки — раздельные строки), поэтому день человека — день одного кода."""
    by_day: dict[tuple[int, date], set[int]] = defaultdict(set)
    for aid, per in _kept(data, p)[0].items():
        for day, customer in per:
            by_day[(aid, day)].add(customer)
    return {frozenset(c) for c in by_day.values()}


def compute(data: CrewData, p: Params, calendar_rest: Collection[date] | None = None,
            tours: Tours | None = None) -> Result:
    """calendar_rest — рабочие дни календаря с сегодня до конца месяца (только текущий месяц): D_month = |дни с доставкой
    ∪ calendar_rest|; None — месяц закрыт (или календарь не прочитан), D_month = D. tours — плановые км дней
    (plan_tours по day_stores); None или rate_km = 0 — без км (km_counted = False)."""
    def code_of(aid: int) -> str:
        return data.agents.get(aid, (str(aid), ''))[0]

    def key_of(aid: int) -> str:
        return person_key(data.agents.get(aid, ('', ''))[1], aid)

    kept, people, unknown = _kept(data, p)
    use_km = tours is not None and p.rate_km > 0
    worked = {day for per in kept.values() for day, _ in per}
    d = len(worked)
    excluded_keys = {key_of(aid) for aid in people}
    kin = tuple(sorted(code_of(aid) for aid in kept if key_of(aid) in excluded_keys))
    dm = d if calendar_rest is None else len(worked | set(calendar_rest))
    if not d:
        return Result(0, (), unknown, (), kin, dm, use_km)

    # коды одного имени: без общих рабочих дней — один человек (одна строка), с общими — тёзки (по строке на код)
    groups: dict[str, list[int]] = defaultdict(list)
    for aid in kept:
        groups[key_of(aid)].append(aid)
    people_ids: list[tuple[int, ...]] = []
    overlapping: list[tuple[str, ...]] = []
    for aids in groups.values():
        days = [{day for day, _ in kept[a]} for a in aids]
        if any(days[x] & days[y] for x in range(len(aids)) for y in range(x + 1, len(aids))):
            overlapping.append(tuple(sorted(code_of(a) for a in aids)))
            people_ids += [(a,) for a in aids]
        else:
            people_ids.append(tuple(aids))

    rows = []
    for aids in people_ids:
        per = {dc: c for a in aids for dc, c in kept[a].items()}   # дни кодов не пересекаются — ключи не совпадают
        by_date: dict[date, list[float]] = defaultdict(lambda: [0, 0.0])   # точки, кг
        stores: dict[date, set[int]] = defaultdict(set)
        for (day, customer), (kg, _) in per.items():
            by_date[day][0] += 1
            by_date[day][1] += kg
            stores[day].add(customer)
        day_km = {day: (tours.km.get(frozenset(c), 0.0), len(c & tours.no_coords)) if use_km and tours else (0.0, 0)
                  for day, c in stores.items()}
        n_days, n_points = len(by_date), len(per)
        tonnes = sum(kg for kg, _ in per.values()) / 1000
        km = sum(k for k, _ in day_km.values())
        sales = sum(total for _, total in per.values())
        share = min(1.0, n_days / dm)
        fix = money(p.fix * share)
        piece = money(p.rate_point * n_points + p.rate_tonne * tonnes + p.rate_km * km)
        minimum = money(p.minimum * min(1.0, n_points / (p.norm_per_day * dm)))
        agent_ids = tuple(sorted(aids, key=code_of))
        main = max(agent_ids, key=lambda a: len(kept[a]))   # имя — по основному коду (больше всего точек; ничья — первый)
        name = ' '.join(data.agents.get(main, ('', ''))[1].split())
        by_day = tuple(Day(day, int(n), kg / 1000,
                           money(p.rate_point * n + p.rate_tonne * kg / 1000 + p.rate_km * day_km[day][0]), *day_km[day])
                       for day, (n, kg) in sorted(by_date.items()))
        rows.append(Row(agent_ids, ', '.join(code_of(a) for a in agent_ids), name, n_days, n_points, tonnes,
                        money(sales), fix, piece, minimum, max(fix + piece, minimum),
                        money(p.old_fix * share) + money(p.old_pct / 100 * sales), minimum > fix + piece, by_day,
                        km, sum(n for _, n in day_km.values())))
    return Result(d, tuple(sorted(rows, key=lambda r: (r.name, r.code))), unknown, tuple(sorted(overlapping)), kin, dm,
                  use_km)


KM_DETOUR = 1.3   # участок без км по дорогам (карты нет, точка не привязана, пути нет) — по прямой × 1,3


def tour_km(depot: Point, points: Sequence[Point], road_km: Callable[[Point, Point], float | None]) -> tuple[float, int]:
    """Замкнутый тур depot → points → depot (tsp.solve_tour, NN + 2-opt; точки — в порядке сортировки, чтобы км не
    зависели от порядка клиентов) → (км, участков тура по прямой × KM_DETOUR)."""
    def dist(a: Point, b: Point) -> float:
        v = road_km(a, b)
        return v if v is not None else haversine_km(a, b) * KM_DETOUR

    pts = sorted(points)
    if not pts:
        return 0.0, 0
    order = tsp.solve_tour(depot, pts, dist)
    path = [depot, *(pts[i] for i in order), depot]
    legs = list(zip(path, path[1:]))
    return sum(dist(a, b) for a, b in legs), sum(1 for a, b in legs if road_km(a, b) is None)


def plan_tours(depot: Point, coords: Mapping[int, Point], days: Iterable[frozenset[int]],
               road_km: Callable[[Point, Point], float | None],
               memo: MutableMapping[tuple[Point, ...], tuple[float, int]] | None = None) -> tuple[Tours, int]:
    """Туры дней days (day_stores): клиенты без точки в coords — 0 км и в Tours.no_coords. memo — посчитанные туры
    (отсортированные точки → (км, участков по прямой)) при тех же складе и дорогах: CSV и правка ставок не пересчитывают.
    → (Tours, участков по прямой во всех турах)."""
    km: dict[frozenset[int], float] = {}
    straight = 0
    missing: set[int] = set()
    for stores in days:
        pts = tuple(sorted({coords[c] for c in stores if c in coords}))
        missing |= {c for c in stores if c not in coords}
        hit = memo.get(pts) if memo is not None else None
        if hit is None:
            hit = tour_km(depot, pts, road_km)
            if memo is not None:
                memo[pts] = hit
        km[stores] = hit[0]
        straight += hit[1]
    return Tours(km, frozenset(missing)), straight


def totals(rows: Sequence[Row]) -> dict[str, Any]:
    """Итог по людям (дни и минимум не складываются — их нет)."""
    return {'points': sum(r.points for r in rows), 'tonnes': sum(r.tonnes for r in rows),
            'km': sum(r.km for r in rows), 'no_coords': sum(r.no_coords for r in rows),
            'sales': sum(r.sales for r in rows), 'fix': sum(r.fix for r in rows), 'piece': sum(r.piece for r in rows),
            'pay': sum(r.pay for r in rows), 'old': sum(r.old for r in rows), 'diff': sum(r.diff for r in rows)}

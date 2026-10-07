# -*- coding: utf-8 -*-
"""«Աշխատավարձ» — зарплата առաքիչ (экспедиторов ERP) за месяц по «дешёвой» формуле владельца 07.10
(docs/research/09-crew-pay.md, раздел «Это много»; эталон — crew-pay/cheap.py, people.py).

Чистый расчёт без ввода-вывода: строки ERP (erp.load_crew_pay) + параметры → люди месяца.

Человек — экспедитор накладной SALES.fVANAGENTID, если он не сам менеджер накладной; накладные — проведённые за месяц.
    days    — разных дней с его накладными;
    points  — разных (день, клиент): несколько накладных одному магазину в день — 1 точка;
    tonnes  — Σ количество × вес товара / 1000 (возвраты вычитаются, как в ERP);
    D       — рабочих дней компании: разных дней с любой учтённой накладной экспедитора в месяце.
Не учитываются накладные линий excluded_lines (по коду менеджера; по умолчанию линия 19 л) и люди excluded_people.

    fix     = FIX × min(1, days / D)
    piece   = RATE_POINT × points + RATE_TONNE × tonnes
    minimum = MIN × min(1, points / (NORM_PER_DAY × D))
    pay     = max(fix + piece, minimum)
    old     = OLD_FIX × min(1, days / D) + OLD_PCT % × продажи      — нынешняя схема, только для сравнения

Деньги — целые драмы: fix, piece, minimum округляются по отдельности (половина — вверх), pay и old считаются из
округлённых частей, чтобы строка таблицы сходилась до драма (pay = fix + piece или ровно minimum).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, fields
from datetime import date
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class Params:
    fix: float = 100_000            # ֏ в месяц за полный месяц
    rate_point: float = 175         # ֏ за точку
    rate_tonne: float = 1_200       # ֏ за тонну
    minimum: float = 250_000        # ֏ в месяц при выполненной норме
    norm_per_day: float = 12        # точек в рабочий день — норма для минимума
    old_fix: float = 100_000        # нынешняя схема: фикс
    old_pct: float = 2.0            # нынешняя схема: % от продаж
    excluded_lines: tuple[str, ...] = ('A008/3', 'A008/6')              # линия 19 л (коды менеджеров)
    excluded_people: tuple[str, ...] = ('B008/10', 'B008/3', 'B008/4')  # коды экспедиторов

    def json(self) -> dict[str, Any]:
        return {f.name: list(v) if isinstance(v := getattr(self, f.name), tuple) else v for f in fields(self)}


# Пределы параметров: (наименьшее, наибольшее); норма 0 запрещена — минимум делился бы на ноль
_BOUNDS: dict[str, tuple[float, float]] = {
    'fix': (0, 5_000_000), 'rate_point': (0, 100_000), 'rate_tonne': (0, 1_000_000), 'minimum': (0, 5_000_000),
    'norm_per_day': (1, 200), 'old_fix': (0, 5_000_000), 'old_pct': (0, 100),
}
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
    """Параметры из формы или базы → (Params, {}) или (None, {поле: ошибка по-армянски}). Все поля обязательны."""
    if not isinstance(raw, Mapping):
        return None, {'_': 'Պարամետրերը սխալ են'}
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, (lo, hi) in _BOUNDS.items():
        v = raw.get(name)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            errors[name] = 'Լրացրեք թիվը'
        elif not lo <= v <= hi:
            errors[name] = f'Թույլատրելի է {lo:g}-ից {hi:g}'
        else:
            values[name] = float(v)
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
    kg: float


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


@dataclass(frozen=True)
class Row:
    agent_id: int
    code: str
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

    @property
    def diff(self) -> int:
        return self.pay - self.old


@dataclass(frozen=True)
class Result:
    workdays: int                       # D; 0 — данных за месяц нет
    rows: tuple[Row, ...]
    unknown_codes: tuple[str, ...]      # коды исключений, которых нет в ERP (опечатка) — показать владельцу


def money(x: float) -> int:
    """Целые драмы, половина — вверх (round() Python округляет к чётному)."""
    return math.floor(x + 0.5)


def compute(data: CrewData, p: Params) -> Result:
    # коды ERP — без учёта регистра и пробелов по краям
    by_code = {code.strip().upper(): aid for aid, (code, _) in data.agents.items()}
    excl_lines = [c.strip().upper() for c in p.excluded_lines]
    excl_people = [c.strip().upper() for c in p.excluded_people]
    lines = {by_code[c] for c in excl_lines if c in by_code}
    people = {by_code[c] for c in excl_people if c in by_code}
    unknown = tuple(c for c in (*excl_lines, *excl_people) if c not in by_code)

    workdays: set[date] = set()
    points: dict[int, dict[date, set[int]]] = defaultdict(lambda: defaultdict(set))
    kg: dict[int, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    sales: dict[int, float] = defaultdict(float)
    for inv in data.invoices:
        if not inv.van_id or inv.van_id == inv.line_id or inv.line_id in lines or inv.van_id in people:
            continue
        workdays.add(inv.day)
        points[inv.van_id][inv.day].add(inv.customer_id)
        kg[inv.van_id][inv.day] += inv.kg
        sales[inv.van_id] += inv.total
    d = len(workdays)
    if not d:
        return Result(0, (), unknown)

    rows = []
    for aid, per_day in points.items():
        n_days = len(per_day)
        n_points = sum(len(c) for c in per_day.values())
        tonnes = sum(kg[aid].values()) / 1000
        share = min(1.0, n_days / d)
        fix = money(p.fix * share)
        piece = money(p.rate_point * n_points + p.rate_tonne * tonnes)
        minimum = money(p.minimum * min(1.0, n_points / (p.norm_per_day * d)))
        code, name = data.agents.get(aid, (str(aid), ''))
        by_day = tuple(Day(day, len(c), kg[aid][day] / 1000,
                           money(p.rate_point * len(c) + p.rate_tonne * kg[aid][day] / 1000))
                       for day, c in sorted(per_day.items()))
        rows.append(Row(aid, code, name, n_days, n_points, tonnes, money(sales[aid]), fix, piece, minimum,
                        max(fix + piece, minimum), money(p.old_fix * share) + money(p.old_pct / 100 * sales[aid]),
                        minimum > fix + piece, by_day))
    return Result(d, tuple(sorted(rows, key=lambda r: (r.name, r.code))), unknown)


def totals(rows: Sequence[Row]) -> dict[str, Any]:
    """Итог по людям (дни и минимум не складываются — их нет)."""
    return {'points': sum(r.points for r in rows), 'tonnes': sum(r.tonnes for r in rows),
            'sales': sum(r.sales for r in rows), 'fix': sum(r.fix for r in rows), 'piece': sum(r.piece for r in rows),
            'pay': sum(r.pay for r in rows), 'old': sum(r.old for r in rows), 'diff': sum(r.diff for r in rows)}

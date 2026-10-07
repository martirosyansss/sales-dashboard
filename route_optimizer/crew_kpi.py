# -*- coding: utf-8 -*-
"""«Առաքիչների KPI» — показатели экспедиторов (առաքիչ) за месяц и тренд за полгода.

Чистый расчёт без ввода-вывода. Вход — месяцы, уже посчитанные «Աշխատավարձ» (crew_pay.compute: те же накладные ERP,
те же исключённые линии и люди, та же склейка кодов одного человека), поэтому KPI и зарплата всегда сходятся.

Показатели человека за месяц (days — дни с доставкой, D — рабочие дни компании в месяце, по сегодня в текущем):
    attendance   = days / D                         — явка: доля рабочих дней компании, когда он возил
    points_day   = points / days                    — магазинов (клиенто-дней) в рабочий день
    tonnes_day   = tonnes / days
    kg_point     = tonnes × 1000 / points           — средняя выгрузка в магазине
    sales_day    = sales / days                     — продажи по его накладным в рабочий день, ֏
    norm         = points / (norm_per_day × days)   — выполнение нормы «Աշխատավարձ» (кетов в рабочий день)
    cost_tonne   = pay / tonnes                     — зарплата по формуле на тонну, ֏ (текущий месяц — начислено по сегодня)
    vs_median    = points_day / медиана points_day команды
Медиана и оценка (good / bad) — только по людям с days ≥ MIN_DAYS: человек, вышедший на 1–2 дня, не сдвигает медиану и
не получает оценку (fewdays). good — points_day ≥ GOOD × медиана, bad — ≤ BAD × медиана.
Один человек в разных месяцах — по имени (crew_pay.person_key); тёзки, посчитанные в месяце раздельно, — по имени и кодам,
а если в другом месяце ключ иной (там тёзки не разделены или разделены, а здесь нет) — по общему коду ERP (agent_ids).
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from . import crew_pay as cp

TREND_MONTHS = 6     # тренд: выбранный месяц и 5 до него
MIN_DAYS = 3         # меньше дней в месяце — не оцениваем и не учитываем в медиане
GOOD = 1.10          # points_day ≥ 110% медианы — сильный
BAD = 0.80           # points_day ≤ 80% медианы — отстаёт


@dataclass(frozen=True)
class Month:
    """Месяц «Աշխատավարձ»: первый день, норма кетов в день из параметров формулы, расчёт crew_pay.compute."""
    first: date
    norm_per_day: float
    result: cp.Result


@dataclass(frozen=True)
class Stats:
    days: int
    points: int
    tonnes: float
    sales: int
    pay: int
    attendance: float | None
    points_day: float
    tonnes_day: float
    kg_point: float | None
    sales_day: float
    norm: float | None
    cost_tonne: float | None


@dataclass(frozen=True)
class Person:
    key: str
    code: str
    name: str
    now: Stats
    prev: Stats | None                       # тот же человек в предыдущем месяце
    vs_median: float | None
    grade: str                               # good | bad | mid | fewdays
    trend: tuple[float | None, ...]          # points_day по месяцам тренда, от старого к выбранному; None — не возил
    by_day: tuple[cp.Day, ...]


@dataclass(frozen=True)
class Team:
    people: int
    workdays: int
    points: int
    tonnes: float
    sales: int
    pay: int
    median_points_day: float | None
    tonnes_day: float | None                 # Σ тонн / Σ человеко-дней
    norm: float | None                       # Σ кетов / (норма × Σ человеко-дней)
    cost_tonne: float | None


@dataclass(frozen=True)
class Report:
    month: date
    trend_months: tuple[date, ...]
    people: tuple[Person, ...]
    team: Team
    prev_team: Team | None


def _ratio(a: float, b: float) -> float | None:
    return a / b if b > 0 else None


def _stats(r: cp.Row, workdays: int, norm_per_day: float) -> Stats:
    return Stats(r.days, r.points, r.tonnes, r.sales, r.pay, _ratio(r.days, workdays),
                 r.points / r.days, r.tonnes / r.days, _ratio(r.tonnes * 1000, r.points), r.sales / r.days,
                 _ratio(r.points, norm_per_day * r.days), _ratio(r.pay, r.tonnes))


def _keys(rows: Sequence[cp.Row]) -> list[str]:
    """Ключ человека по имени; тёзки, которых месяц посчитал раздельно (общие рабочие дни), — по имени и кодам."""
    base = [cp.person_key(r.name, r.agent_ids[0]) for r in rows]
    twice = {k for k, n in Counter(base).items() if n > 1}
    return [f'{k}|{r.code}' if k in twice else k for k, r in zip(base, rows)]


def _match(rows: dict[str, cp.Row], key: str, row: cp.Row) -> cp.Row | None:
    """Строка того же человека в другом месяце: тот же ключ, иначе — общий код ERP."""
    hit = rows.get(key)
    if hit is not None:
        return hit
    ids = set(row.agent_ids)
    return next((r for r in rows.values() if ids & set(r.agent_ids)), None)


def _team(m: Month) -> Team:
    rows = m.result.rows
    days = sum(r.days for r in rows)
    points = sum(r.points for r in rows)
    tonnes = sum(r.tonnes for r in rows)
    pay = sum(r.pay for r in rows)
    rated = [r.points / r.days for r in rows if r.days >= MIN_DAYS]
    return Team(len(rows), m.result.workdays, points, tonnes, sum(r.sales for r in rows), pay,
                statistics.median(rated) if rated else None, _ratio(tonnes, days),
                _ratio(points, m.norm_per_day * days), _ratio(pay, tonnes))


def report(months: Sequence[Month]) -> Report:
    """months — месяцы тренда от старого к выбранному (последний); пустых месяцев может быть сколько угодно."""
    if not months:
        raise ValueError('нет месяцев')
    cur, prev = months[-1], (months[-2] if len(months) > 1 else None)
    team = _team(cur)
    per_month = [dict(zip(_keys(m.result.rows), m.result.rows)) for m in months]
    people = []
    for key, r in per_month[-1].items():
        now = _stats(r, cur.result.workdays, cur.norm_per_day)
        p = _match(per_month[-2], key, r) if prev is not None else None
        median = team.median_points_day
        if r.days < MIN_DAYS or not median:
            vs, grade = (now.points_day / median if median else None), 'fewdays'
        else:
            vs = now.points_day / median
            grade = 'good' if vs >= GOOD else 'bad' if vs <= BAD else 'mid'
        trend = tuple((x.points / x.days) if (x := _match(pm, key, r)) is not None else None for pm in per_month)
        people.append(Person(key, r.code, r.name, now,
                             _stats(p, prev.result.workdays, prev.norm_per_day) if p is not None and prev else None,
                             vs, grade, trend, r.by_day))
    people.sort(key=lambda x: (x.grade == 'fewdays', -x.now.points_day, x.name))
    return Report(cur.first, tuple(m.first for m in months), tuple(people), team,
                  _team(prev) if prev is not None and prev.result.rows else None)

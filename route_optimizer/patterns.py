# -*- coding: utf-8 -*-
"""Шаблоны дней визита в цикле 2 недели (этап 3, §3).

Шаблон — множество пар (неделя цикла 1|2, день недели 1..7); хранится отсортированным кортежем.
Частота шаблона = визитов за цикл / 2. Чистая логика — без Flask и без БД.
"""
from __future__ import annotations

import json
import math
from itertools import combinations
from typing import Any, Collection, Iterable, Mapping

CYCLE_WEEKS = 2

Slot = tuple[int, int]          # (неделя 1|2, день недели 1..7)
Pattern = tuple[Slot, ...]      # отсортированный, без повторов

FREQUENCIES = (0.5, 1.0, 2.0, 3.0)
# 2 раза в неделю: пн+чт, вт+пт, ср+сб, пн+ср, вт+чт, чт+сб; 3 раза: пн+ср+пт, вт+чт+сб
TWICE_DAYS = ((1, 4), (2, 5), (3, 6), (1, 3), (2, 4), (4, 6))
THRICE_DAYS = ((1, 3, 5), (2, 4, 6))
DAY_SHORT = {1: 'пн', 2: 'вт', 3: 'ср', 4: 'чт', 5: 'пт', 6: 'сб', 7: 'вс'}
DAY_NAMES = {1: 'понедельник', 2: 'вторник', 3: 'среда', 4: 'четверг', 5: 'пятница', 6: 'суббота',
             7: 'воскресенье'}
SATURDAY = 6
_EPS = 1e-9


def make_pattern(slots: Iterable[Slot]) -> Pattern:
    return tuple(sorted(set(slots)))


def weekly(days: Iterable[int]) -> Pattern:
    """Одни и те же дни в обе недели цикла."""
    return make_pattern((w, d) for w in (1, 2) for d in days)


def pattern_freq(p: Pattern) -> float:
    return len(p) / CYCLE_WEEKS


def same_freq(a: float, b: float) -> bool:
    return abs(a - b) < _EPS


def standard_patterns(freq: float, workdays: Collection[int]) -> list[Pattern]:
    """Допустимые шаблоны частоты по таблице §3 — только из рабочих дней."""
    wd = sorted(set(workdays))
    if same_freq(freq, 0.5):
        out = [((w, d),) for w in (1, 2) for d in wd]
    elif same_freq(freq, 1.0):
        out = [weekly((d,)) for d in wd]
    elif same_freq(freq, 2.0):
        out = [weekly(days) for days in TWICE_DAYS if set(days) <= set(wd)]
    elif same_freq(freq, 3.0):
        out = [weekly(days) for days in THRICE_DAYS if set(days) <= set(wd)]
    else:
        out = []
    return sorted(out)


def sub_patterns(current: Pattern, freq: float) -> list[Pattern]:
    """Шаблоны частоты freq из дней текущего шаблона (частота снижается, день остаётся):
    0.5 — любой визит текущего шаблона; n раз в неделю — n дней, что есть в обеих неделях."""
    if same_freq(freq, 0.5):
        return sorted((slot,) for slot in current)
    n = round(freq)
    if not same_freq(freq, n) or n < 1:
        return []
    cur = set(current)
    both = sorted(d for w, d in cur if w == 1 and (2, d) in cur)
    return sorted(weekly(days) for days in combinations(both, n))


def workday_pattern(p: Pattern, workdays: Collection[int]) -> Pattern:
    """Шаблон, где визиты нерабочих дней перенесены на рабочие (Р3-9: воскресенье — нерабочий день):
    на субботу той же недели цикла, а если суббота не рабочая или в ней уже есть визит — на
    ближайший свободный рабочий день перед ней. Частота та же; свободного рабочего дня в неделе
    нет — визит выпадает."""
    wd = set(workdays)
    out = {(w, d) for w, d in p if d in wd}
    for w, d in sorted(p):
        if d not in wd:
            day = next((x for x in range(SATURDAY, 0, -1) if x in wd and (w, x) not in out), None)
            if day is not None:
                out.add((w, day))
    return make_pattern(out)


def allowed_patterns(current: Pattern, target: float, workdays: Collection[int],
                     forbidden: Collection[Pattern] = ()) -> list[Pattern]:
    """Шаблоны, из которых выбирает поиск (§3, Р3-9):
    - стандартные для целевой частоты, только рабочие дни;
    - шаблон с нерабочим днём (воскресенье) — никогда, даже текущий; вместо текущего — он же,
      где визиты нерабочих дней перенесены на субботу (workday_pattern);
    - текущий шаблон (так перенесённый) — пока частота не меняется, даже нестандартный;
    - при снижении частоты — и шаблоны из дней текущего («тот же день», §6);
    - без запрещённых владельцем."""
    out = set(standard_patterns(target, workdays))
    base = workday_pattern(current, workdays)
    cur_freq = pattern_freq(current)
    if same_freq(target, cur_freq):
        if len(base) == len(current):
            out.add(base)
    elif target < cur_freq:
        out.update(sub_patterns(base, target))
    out.difference_update(forbidden)
    return sorted(out)


def change_type(before: Pattern, after: Pattern) -> str | None:
    """move — другие дни при той же частоте; frequency — частота другая, дни из прежних
    (или прежние — среди новых); both — и частота, и дни; None — без изменений."""
    if before == after:
        return None
    if len(before) == len(after):
        return 'move'
    b, a = set(before), set(after)
    return 'frequency' if a <= b or b <= a else 'both'


def _days_text(days: Iterable[int], labels: Mapping[int, str] = DAY_SHORT) -> str:
    names = [labels.get(d, str(d)) for d in sorted(days)]
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' и ' + names[-1]


def off_days_text(p: Pattern, workdays: Collection[int]) -> str | None:
    """Причина обязательного переноса (Р3-9): «воскресенье — нерабочий день»; None — в шаблоне
    только рабочие дни."""
    wd = set(workdays)
    off = {d for _, d in p if d not in wd}
    if not off:
        return None
    return _days_text(off, DAY_NAMES) + (' — нерабочий день' if len(off) == 1 else ' — нерабочие дни')


def pattern_text(p: Pattern) -> str:
    """«вт, каждую неделю», «чт, 1-я неделя из 2», «пн и чт, каждую неделю»."""
    w1 = {d for w, d in p if w == 1}
    w2 = {d for w, d in p if w == 2}
    if not p:
        return 'без визитов'
    if w1 == w2:
        return f'{_days_text(w1)}, каждую неделю'
    if not w2:
        return f'{_days_text(w1)}, 1-я неделя из 2'
    if not w1:
        return f'{_days_text(w2)}, 2-я неделя из 2'
    return f'{_days_text(w1)} — 1-я неделя, {_days_text(w2)} — 2-я неделя'


def pattern_json(p: Pattern) -> list[list[int]]:
    return [[w, d] for w, d in p]


def pattern_key(p: Pattern) -> str:
    """Канонический текст шаблона для таблицы решений: «[[1,2],[2,2]]»."""
    return json.dumps(pattern_json(p), separators=(',', ':'))


def freq_key(f: float) -> str:
    """Канонический текст частоты для таблицы решений: «0.5», «1», «2», «3»."""
    return f'{f:g}'


def parse_pattern(value: Any) -> Pattern | None:
    """Шаблон из JSON [[неделя, день], …]: недели 1..2, дни 1..7, без повторов. Иначе None."""
    if not isinstance(value, list) or not 1 <= len(value) <= 2 * 7:
        return None
    slots = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            return None
        week, day = item
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (week, day)):
            return None
        if week not in (1, 2) or not 1 <= day <= 7:
            return None
        slots.append((week, day))
    if len(set(slots)) != len(slots):
        return None
    return tuple(sorted(slots))


def parse_freq(value: Any) -> float | None:
    """Частота из JSON: одна из 0.5, 1, 2, 3. Иначе None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return next((f for f in FREQUENCIES if value == f), None)


def parse_plan_freq(value: Any) -> float | None:
    """Частота текущего плана из JSON (от чего принималось решение): n/2 визита в неделю,
    n = 1..14 — в плане ERP бывает и 1,5, и 6 раз в неделю. Иначе None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    n = value * CYCLE_WEEKS
    if not isinstance(n, int) and not (math.isfinite(n) and n.is_integer()):
        return None
    return float(value) if 1 <= n <= CYCLE_WEEKS * 7 else None


MAX_AGENT_ID = 2 ** 31 - 1


def transfer_key(agent_id: int, p: Pattern) -> str:
    """Канонический текст решения «передать» (этап 4): кому и в какие дни —
    «{"agent_id":3144,"pattern":[[1,2],[2,2]]}»."""
    return json.dumps({'agent_id': agent_id, 'pattern': pattern_json(p)}, separators=(',', ':'),
                      sort_keys=True)


def parse_transfer(value: Any) -> tuple[int, Pattern] | None:
    """{"agent_id": id менеджера, "pattern": [[неделя, день], …]} → (id, шаблон). Иначе None."""
    if not isinstance(value, dict) or set(value) != {'agent_id', 'pattern'}:
        return None
    agent_id = value['agent_id']
    if isinstance(agent_id, bool) or not isinstance(agent_id, int) or not 0 < agent_id <= MAX_AGENT_ID:
        return None
    p = parse_pattern(value['pattern'])
    return None if p is None else (agent_id, p)


def parse_transfer_key(text: Any) -> tuple[int, Pattern] | None:
    try:
        return parse_transfer(json.loads(text))
    except (TypeError, ValueError, RecursionError):
        return None


def parse_pattern_key(text: Any) -> Pattern | None:
    try:
        return parse_pattern(json.loads(text))
    except (TypeError, ValueError, RecursionError):
        return None


def parse_freq_key(text: Any) -> float | None:
    try:
        return parse_freq(json.loads(text))
    except (TypeError, ValueError, RecursionError):
        return None


def parse_plan_freq_key(text: Any) -> float | None:
    try:
        return parse_plan_freq(json.loads(text))
    except (TypeError, ValueError, RecursionError):
        return None

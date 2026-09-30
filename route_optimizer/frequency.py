# -*- coding: utf-8 -*-
"""Частота визитов (этап 3, §2): ABC по выручке, частота по продажам, целевая частота, подсказки.

Чистая логика — без Flask и без БД. Частоты — визитов в неделю, λ — заказов в неделю за год,
выручка — драм в неделю.

Почему частота автоматически только снижается: если клиент заказывает чаще, чем его посещают
(λ > f), лишние заказы уже приходят (по телефону или через других агентов), и модель
p = min(1, λ/f) показала бы от повышения частоты выдуманный рост выручки. Повышение — подсказка.
"""
from __future__ import annotations

from typing import Collection, Mapping

from .patterns import FREQUENCIES, same_freq

CLASS_MIN_FREQ = {'A': 1.0, 'B': 1.0, 'C': 0.5}
_EPS = 1e-9

SOURCE_MANUAL = 'manual'     # частоту принял владелец
SOURCE_SALES = 'sales'       # снижена по продажам
SOURCE_CURRENT = 'current'   # как сейчас


def abc_classes(revenue_week: Mapping[int, float], a_share: float, b_share: float) -> dict[int, str]:
    """ABC по выручке в неделю: по убыванию; A — первые a_share накопленной выручки, B — следующие
    b_share, C — остальные. Клиент, на котором накопленная доля переходит порог, остаётся в
    старшем классе (доля считается ДО него). Ничьи — по id клиента; выручки нет — все C."""
    total = sum(max(0.0, r) for r in revenue_week.values())
    out: dict[int, str] = {}
    cum = 0.0
    for cid, rev in sorted(revenue_week.items(), key=lambda kv: (-kv[1], kv[0])):
        share = cum / total if total > 0 else 1.0
        if share < a_share - _EPS:
            out[cid] = 'A'
        elif share < a_share + b_share - _EPS:
            out[cid] = 'B'
        else:
            out[cid] = 'C'
        cum += max(0.0, rev)
    return out


def sales_frequency(lam: float, abc: str, safety: float) -> float | None:
    """f_sales = max(минимум класса, min{f ∈ {0.5, 1, 2, 3} : f ≥ λ × запас}).

    λ — наибольшая из λ_год, λ_низкий сезон, λ_пик (season_lam): частота, подобранная по году,
    летом недодаёт заказов (p = min(1, λ/f) упирается в 1), и модель теряет выручку сезона.
    None — ни одна частота не покрывает спрос (λ × запас > 3): снижать нельзя."""
    need = max(0.0, lam) * safety
    fits = [f for f in FREQUENCIES if f >= need - _EPS]
    if not fits:
        return None
    return max(CLASS_MIN_FREQ[abc], fits[0])


def target_frequency(current: float, f_sales: float | None, mode: str, accepted: float | None = None,
                     rejected: Collection[float] = ()) -> tuple[float, str]:
    """(целевая частота, источник). Ручная частота владельца важнее всего; в режиме «по продажам» —
    min(текущая, f_sales), т.е. только снижение; отклонённая владельцем частота не предлагается
    (остаётся текущая); в режиме «как сейчас» — текущая."""
    if accepted is not None:
        return accepted, SOURCE_MANUAL
    if mode == 'sales' and f_sales is not None and f_sales < current - _EPS:
        if any(same_freq(f_sales, r) for r in rejected):
            return current, SOURCE_CURRENT
        return f_sales, SOURCE_SALES
    return current, SOURCE_CURRENT


# --- Тексты ---

def fmt_decimal(x: float, digits: int = 2) -> str:
    """1.6 → «1,6», 0.333 → «0,33», 2.0 → «2»."""
    text = f'{x:.{digits}f}'.rstrip('0').rstrip('.')
    return (text or '0').replace('.', ',')


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    last = n % 10
    return one if last == 1 else (few if 2 <= last <= 4 else many)


def _times(x: float) -> str:
    """«раз» / «раза» после числа: 1 раз, 2 раза, 5 раз, 1,6 раза."""
    if not same_freq(x, round(x)):
        return 'раза'
    return plural(round(x), 'раз', 'раза', 'раз')


def order_rate_text(lam: float, lam_season: float | None = None) -> str:
    """Причина снижения частоты: «заказывает раз в 3 недели (0,33 заказа/нед)»; lam_season —
    наибольшая λ сезонов, если она выше годовой (частота подобрана по ней):
    «…; в сезон — до 0,45 заказа/нед»."""
    if lam <= 0:
        text = 'за год ни одного заказа'
    elif lam >= 1 - _EPS:
        text = f'заказывает {fmt_decimal(lam, 1)} {_times(round(lam, 1))} в неделю'
    else:
        weeks = round(1 / lam)
        rate = f'{fmt_decimal(lam, 2)} заказа/нед'
        text = (f'заказывает почти каждую неделю ({rate})' if weeks <= 1 else
                f'заказывает раз в {weeks} {plural(weeks, "неделю", "недели", "недель")} ({rate})')
    if lam_season is not None and round(lam_season, 2) > round(lam, 2):
        text += f'; в сезон — до {fmt_decimal(lam_season, 2)} заказа/нед'
    return text


def freq_text(f: float) -> str:
    """Частота словами: «раз в 2 недели», «раз в неделю», «2 раза в неделю», «1,5 раза в неделю»."""
    if same_freq(f, 0.5):
        return 'раз в 2 недели'
    if same_freq(f, 1.0):
        return 'раз в неделю'
    return f'{fmt_decimal(f, 1)} {_times(f)} в неделю'


def visits_text(f: float) -> str:
    """«при 1 визите», «при 2 визитах», «при визите раз в 2 недели»."""
    if same_freq(f, 0.5):
        return 'при визите раз в 2 недели'
    if same_freq(f, round(f)):
        n = round(f)
        return f'при {n} ' + plural(n, 'визите', 'визитах', 'визитах')
    return f'при {fmt_decimal(f, 1)} визита'


def can_visit_text(f: float) -> str:
    """«можно посещать 2 раза», «можно посещать каждую неделю»."""
    if same_freq(f, 1.0):
        return 'можно посещать каждую неделю'
    return f'можно посещать {fmt_decimal(f, 1)} {_times(f)}'


def frequency_hints(lam_year: float, freq_now: float, safety: float) -> list[tuple[str, str]]:
    """Подсказки, которые не применяются автоматически: [(вид, текст)].
    no_orders — за год ни одного заказа; freq_up — заказывает чаще, чем его посещают (λ > f)."""
    if lam_year <= 0:
        return [('no_orders', 'за год ни одного заказа')]
    if lam_year <= freq_now + _EPS:
        return []
    need = lam_year * safety
    f_up = next((f for f in FREQUENCIES if f >= need - _EPS), FREQUENCIES[-1])
    if f_up <= freq_now + _EPS:
        return []
    rate = f'{fmt_decimal(lam_year, 1)} {_times(round(lam_year, 1))} в неделю'
    return [('freq_up', f'заказывает {rate} {visits_text(freq_now)} — {can_visit_text(f_up)}')]

# -*- coding: utf-8 -*-
"""Частота визитов (этап 3, §2): ABC по выручке, частота по продажам, целевая частота, подсказки.

Чистая логика — без Flask и без БД. Частоты — визитов в неделю, λ — заказов в неделю за год,
выручка — драм в неделю.

Почему частота по продажам только снижается: если клиент заказывает чаще, чем его посещают
(λ > f), лишние заказы уже приходят (по телефону или через других агентов), и модель
p = min(1, λ/f) показала бы от повышения частоты выдуманный рост выручки. Повышение — подсказка.
Исключение — правило владельца (ответ №30): каждый магазин — не реже раза в неделю; визит
«раз в 2 недели» поднимается до еженедельного.
"""
from __future__ import annotations

from typing import Collection, Mapping

from .patterns import FREQUENCIES, same_freq

MIN_FREQ = 1.0   # №30: менеджер заходит в каждый магазин каждую неделю, как бы редко тот ни заказывал
_EPS = 1e-9

SOURCE_MANUAL = 'manual'     # частоту принял владелец
SOURCE_SALES = 'sales'       # снижена по продажам
SOURCE_CURRENT = 'current'   # как сейчас
SOURCE_RULE = 'rule'         # поднята до минимума MIN_FREQ (№30)
RULE_TEXT = 'յուրաքանչյուր խանութ՝ առնվազն շաբաթը մեկ անգամ'


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


def sales_frequency(lam: float, safety: float) -> float | None:
    """f_sales = max(MIN_FREQ, min{f ∈ {0.5, 1, 2, 3} : f ≥ λ × запас}).

    λ — наибольшая из λ_год, λ_низкий сезон, λ_пик (season_lam): частота, подобранная по году,
    летом недодаёт заказов (p = min(1, λ/f) упирается в 1), и модель теряет выручку сезона.
    None — ни одна частота не покрывает спрос (λ × запас > 3): снижать нельзя."""
    need = max(0.0, lam) * safety
    fits = [f for f in FREQUENCIES if f >= need - _EPS]
    if not fits:
        return None
    return max(MIN_FREQ, fits[0])


def target_frequency(current: float, f_sales: float | None, mode: str, accepted: float | None = None,
                     rejected: Collection[float] = (), store_freq: float | None = None) -> tuple[float, str]:
    """(целевая частота пары менеджер–магазин, источник). Ручная частота владельца важнее всего; в
    режиме «по продажам» магазин, который все менеджеры вместе посещают реже раза в неделю
    (store_freq; None — только этот менеджер, = current), — MIN_FREQ (№30), иначе min(текущая,
    f_sales), т.е. только снижение; отклонённая владельцем частота не предлагается (остаётся
    текущая); в режиме «как сейчас» — текущая."""
    if accepted is not None:
        return accepted, SOURCE_MANUAL
    total = current if store_freq is None else store_freq
    if mode == 'sales' and total < MIN_FREQ - _EPS and current < MIN_FREQ - _EPS:
        if any(same_freq(MIN_FREQ, r) for r in rejected):
            return current, SOURCE_CURRENT
        return MIN_FREQ, SOURCE_RULE
    if mode == 'sales' and f_sales is not None and f_sales < current - _EPS:
        if any(same_freq(f_sales, r) for r in rejected):
            return current, SOURCE_CURRENT
        return f_sales, SOURCE_SALES
    return current, SOURCE_CURRENT


# --- Тексты (для пользователя — по-армянски, решение владельца №58; после числа существительное в ед. ч.) ---

def fmt_decimal(x: float, digits: int = 2) -> str:
    """1.6 → «1,6», 0.333 → «0,33», 2.0 → «2»."""
    text = f'{x:.{digits}f}'.rstrip('0').rstrip('.')
    return (text or '0').replace('.', ',')


def order_rate_text(lam: float, lam_season: float | None = None) -> str:
    """Причина снижения частоты: «պատվիրում է 3 շաբաթը մեկ (0,33 պատվեր շաբաթում)»; lam_season —
    наибольшая λ сезонов, если она выше годовой (частота подобрана по ней):
    «…; սեզոնին՝ մինչև 0,45 պատվեր շաբաթում» (по «սեզոնին» его узнаёт rowReason в routes_optimize.js)."""
    if lam <= 0:
        text = 'վերջին տարում ոչ մի պատվեր'
    elif lam >= 1 - _EPS:
        text = f'պատվիրում է շաբաթը {fmt_decimal(lam, 1)} անգամ'
    else:
        weeks = round(1 / lam)
        rate = f'{fmt_decimal(lam, 2)} պատվեր շաբաթում'
        text = (f'պատվիրում է գրեթե ամեն շաբաթ ({rate})' if weeks <= 1 else
                f'պատվիրում է {weeks} շաբաթը մեկ ({rate})')
    if lam_season is not None and round(lam_season, 2) > round(lam, 2):
        text += f'; սեզոնին՝ մինչև {fmt_decimal(lam_season, 2)} պատվեր շաբաթում'
    return text


def freq_text(f: float) -> str:
    """Частота словами: «2 շաբաթը մեկ», «շաբաթը մեկ անգամ», «շաբաթը 2 անգամ», «շաբաթը 1,5 անգամ»."""
    if same_freq(f, 0.5):
        return '2 շաբաթը մեկ'
    if same_freq(f, 1.0):
        return 'շաբաթը մեկ անգամ'
    return f'շաբաթը {fmt_decimal(f, 1)} անգամ'


def visits_text(f: float) -> str:
    """Сколько посещают сейчас: «իսկ այցելում ենք շաբաթը 1 անգամ», «իսկ այցելում ենք 2 շաբաթը մեկ»."""
    if same_freq(f, 0.5):
        return 'իսկ այցելում ենք 2 շաբաթը մեկ'
    return f'իսկ այցելում ենք շաբաթը {fmt_decimal(f, 1)} անգամ'


def can_visit_text(f: float) -> str:
    """«կարելի է այցելել շաբաթը 2 անգամ», «կարելի է այցելել ամեն շաբաթ»."""
    if same_freq(f, 1.0):
        return 'կարելի է այցելել ամեն շաբաթ'
    return f'կարելի է այցելել շաբաթը {fmt_decimal(f, 1)} անգամ'


def frequency_hints(lam_year: float, freq_now: float, safety: float, freq_after: float = 0.0,
                    rejected: Collection[float] = ()) -> list[tuple[str, str]]:
    """Подсказки, которые не применяются автоматически: [(вид, текст)].
    no_orders — за год ни одного заказа; freq_up — заказывает чаще, чем его посещают (λ > f); её нет,
    если предложение уже даёт столько визитов (freq_after) или владелец эту частоту отклонил."""
    if lam_year <= 0:
        return [('no_orders', 'վերջին տարում ոչ մի պատվեր')]
    if lam_year <= freq_now + _EPS:
        return []
    need = lam_year * safety
    f_up = next((f for f in FREQUENCIES if f >= need - _EPS), FREQUENCIES[-1])
    if f_up <= max(freq_now, freq_after) + _EPS or any(same_freq(f_up, r) for r in rejected):
        return []
    rate = f'շաբաթը {fmt_decimal(lam_year, 1)} անգամ'
    return [('freq_up', f'պատվիրում է {rate}, {visits_text(freq_now)} — {can_visit_text(f_up)}')]

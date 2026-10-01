# -*- coding: utf-8 -*-
"""Время раздела «Առաքիչ»: рабочий день и моменты — по Еревану.

Армения не переводит часы с 2012 года — постоянный UTC+4, без tzdata (на сервере её может не быть).
Модули вызывают clock.now() через модуль: тесты подменяют его monkeypatch'ем.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

YEREVAN = timezone(timedelta(hours=4), 'Asia/Yerevan')
SESSION_END = time(4, 0)   # сессия водителя живёт до 04:00 следующего дня (контракт §2 /login)
MOMENT_YEARS = (1990, 2100)   # момент терминала вне этих лет — ошибка часов, а не дата


def now() -> datetime:
    """Текущий момент по Еревану (aware)."""
    return datetime.now(YEREVAN).replace(microsecond=0)


def today() -> date:
    return now().date()


def iso(dt: datetime) -> str:
    """ISO-8601 с зоной, до секунд: 2026-10-02T10:15:03+04:00."""
    return dt.astimezone(YEREVAN).isoformat(timespec='seconds')


def session_expiry(at: datetime) -> datetime:
    """Конец сессии: 04:00 следующего календарного дня (по Еревану) после входа."""
    local = at.astimezone(YEREVAN)
    return datetime.combine(local.date() + timedelta(days=1), SESSION_END, YEREVAN)


def parse_moment(raw: object) -> datetime | None:
    """Момент ISO-8601 с зоной → aware datetime; без зоны или не строка — None."""
    if not isinstance(raw, str) or not 10 <= len(raw) <= 40:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None or not MOMENT_YEARS[0] <= dt.year <= MOMENT_YEARS[1]:
            return None
        dt.astimezone(timezone.utc)   # 0001-01-01T00:00+04:00 и подобные переполняются — это не момент
    except (ValueError, OverflowError):
        return None
    return dt


def parse_day(raw: object) -> date | None:
    """Дата YYYY-MM-DD (строго этот формат) → date; иначе None."""
    if not isinstance(raw, str) or len(raw) != 10 or raw[4] != '-' or raw[7] != '-':
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def utc_key(dt: datetime) -> str:
    """Ключ сортировки моментов с разными зонами: UTC ISO с микросекундами."""
    return dt.astimezone(timezone.utc).isoformat(timespec='microseconds')

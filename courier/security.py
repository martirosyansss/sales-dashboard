# -*- coding: utf-8 -*-
"""Токены терминалов, сессии водителей и PIN.

Инварианты:
- токен терминала и сессии — 32 случайных байта (base64url); в базе хранится ТОЛЬКО sha256 (hex);
  поиск — по хешу, затем сравнение hmac.compare_digest (постоянное время);
- PIN — 4–6 цифр, хранится хешем pbkdf2 (werkzeug, соль на запись). Число итераций снижено
  против дефолта werkzeug: при входе PIN сверяется со всеми активными водителями (PIN определяет
  водителя), а перебор 10^6 PIN всё равно отсекает блокировка терминала после 5 ошибок;
- pin_tag — тот же pbkdf2, но с одной солью на базу (meta.pin_salt, случайная): детерминирован, поэтому
  одинаковые PIN дают одинаковый tag. Нужен, чтобы проверить «PIN уже у другого активного водителя», не зная
  PIN (повторное включение водителя), и чтобы вход считал один хеш, а не по хешу на водителя. Офлайн-перебор
  4–6 цифр по украденной базе это не усложняет и не упрощает заметно: защита PIN — блокировка терминала.
- перец (необязательно): переменная среды COURIER_PIN_PEPPER задана — tag = 'p1:' + HMAC-SHA256(перец, tag без
  перца). Перца нет в базе, поэтому по украденной courier.db без среды сервера tag не перебрать. Включение перца
  переводит сохранённые tag сразу (HMAC от прежнего tag, PIN не нужен); смена или снятие перца — tag сбрасываются,
  и вход водителя пересчитывает его (store.Store._pin_keys, match_pin).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

PIN_RE = re.compile(r'^\d{4,6}$')
PIN_METHOD = 'pbkdf2:sha256:60000'
PIN_TAG_ITERATIONS = 60000
TOKEN_BYTES = 32
PEPPER_ENV = 'COURIER_PIN_PEPPER'
PEPPER_PREFIX = 'p1:'
_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{20,128}$')


def new_token() -> str:
    """32 случайных байта в base64url без «=»."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def token_shape_ok(token: object) -> bool:
    """Строка похожа на наш токен (до обращения к базе: мусор не ищем)."""
    return isinstance(token, str) and bool(_TOKEN_RE.match(token))


def same_hash(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode('ascii'), b.encode('ascii'))


def valid_pin(pin: object) -> bool:
    return isinstance(pin, str) and bool(PIN_RE.match(pin))


def hash_pin(pin: str) -> str:
    if not valid_pin(pin):
        raise ValueError('PIN — 4–6 цифр')
    return generate_password_hash(pin, method=PIN_METHOD)


def check_pin(pin_hash: str | None, pin: str) -> bool:
    if not pin_hash or not valid_pin(pin):
        return False
    try:
        return check_password_hash(pin_hash, pin)
    except (ValueError, TypeError):   # битый хеш в базе — не совпадение, а не падение входа
        return False


def pin_tag(pin: str, salt_hex: str, pepper: bytes | None = None) -> str:
    """Детерминированный хеш PIN с солью базы и (если задан) перцем (см. docstring модуля)."""
    if not valid_pin(pin):
        raise ValueError('PIN — 4–6 цифр')
    tag = hashlib.pbkdf2_hmac('sha256', pin.encode('ascii'), bytes.fromhex(salt_hex), PIN_TAG_ITERATIONS).hex()
    return pepper_tag(tag, pepper) if pepper else tag


def pepper_tag(tag: str, pepper: bytes) -> str:
    """tag без перца → tag с перцем (без PIN: так сохранённые tag переводятся при включении перца)."""
    return PEPPER_PREFIX + hmac.new(pepper, tag.encode('ascii'), hashlib.sha256).hexdigest()


def pin_pepper() -> bytes | None:
    """Перец из среды (COURIER_PIN_PEPPER); не задан или пустой — None (tag как раньше)."""
    value = os.environ.get(PEPPER_ENV, '').strip()
    return value.encode('utf-8') if value else None


def pepper_id(pepper: bytes | None) -> str:
    """Отпечаток перца для meta (сам перец в базу не пишется): понять, что перец включили, сменили или сняли."""
    return hmac.new(pepper, b'courier-pin-pepper-id', hashlib.sha256).hexdigest()[:16] if pepper else 'none'

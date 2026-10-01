# -*- coding: utf-8 -*-
"""Токены терминалов, сессии водителей и PIN.

Инварианты:
- токен терминала и сессии — 32 случайных байта (base64url); в базе хранится ТОЛЬКО sha256 (hex);
  поиск — по хешу, затем сравнение hmac.compare_digest (постоянное время);
- PIN — 4–6 цифр. У водителя два производных значения, оба зависят от «перца» (COURIER_PIN_PEPPER), если он задан:
  - pin_hash — werkzeug pbkdf2 (соль на запись, число итераций библиотеки по умолчанию) от секрета PIN:
    без перца секрет — сам PIN (схема 'plain'), с перцем — HMAC-SHA256(перец, PIN) (схема 'pepper:<id>', id — 8 hex
    отпечатка перца; сам перец в базу не пишется). Схема хранится в drivers.pin_scheme;
  - pin_tag — детерминированный хеш: pbkdf2 (PIN_TAG_ITERATIONS) с одной солью на базу (meta.pin_salt); с перцем —
    'p2:<id>:' + HMAC-SHA256(перец, tag). Одинаковые PIN дают одинаковый tag: проверка «PIN уже у другого водителя»
    без самого PIN и вход за один pbkdf2 (tag) вместо проверки хеша каждого водителя;
- что даёт перец: по украденной courier.db без среды сервера (.env) PIN не перебрать офлайн — ни по pin_hash, ни по
  pin_tag (ключ HMAC неизвестен). Без перца 10^4–10^6 вариантов PIN перебираются по любому из них за часы, сколько бы
  ни было итераций; от перебора на самом сервере защищает только блокировка терминала (5 ошибок → 15 минут);
- смена перца: COURIER_PIN_PEPPER — новый, COURIER_PIN_PEPPER_OLD — прежний; водитель с хешем и tag прежнего перца
  входит как обычно и сразу получает новые (Store.match_pin). Перца схемы нет ни в одной переменной — PIN такого
  водителя не проверить: вход отвечает «PIN задать заново в офисе», офис задаёт новый PIN.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass

import werkzeug.security as wz

PIN_RE = re.compile(r'^\d{4,6}$')
PIN_HASH_METHOD = 'pbkdf2'          # werkzeug: pbkdf2:sha256 с числом итераций библиотеки по умолчанию
PIN_TAG_ITERATIONS = 60000
TOKEN_BYTES = 32
PEPPER_ENV = 'COURIER_PIN_PEPPER'
PEPPER_OLD_ENV = 'COURIER_PIN_PEPPER_OLD'
SCHEME_PLAIN = 'plain'
TAG_PREFIX = 'p2:'
_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{20,128}$')
_TAG_RE = re.compile(r'^(?:[0-9a-f]{64}|p2:[0-9a-f]{8}:[0-9a-f]{64})$')


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


# --- перец ---

@dataclass(frozen=True)
class Pepper:
    key: bytes
    id: str                           # 8 hex: отпечаток для схемы хеша и tag (сам перец в базу не пишется)

    @property
    def scheme(self) -> str:
        return f'pepper:{self.id}'


def pepper_from_env(name: str = PEPPER_ENV) -> Pepper | None:
    """Перец из переменной среды; не задан или пустой — None."""
    value = os.environ.get(name, '').strip()
    if not value:
        return None
    key = value.encode('utf-8')
    return Pepper(key, hmac.new(key, b'courier-pin-pepper-id', hashlib.sha256).hexdigest()[:8])


def scheme_of(pepper: Pepper | None) -> str:
    return pepper.scheme if pepper is not None else SCHEME_PLAIN


def _secret(pin: str, pepper: Pepper | None) -> str:
    return pin if pepper is None else hmac.new(pepper.key, pin.encode('ascii'), hashlib.sha256).hexdigest()


# --- pin_hash (pbkdf2 werkzeug от секрета PIN) ---

def hash_pin(pin: str, pepper: Pepper | None = None) -> str:
    """Хеш PIN текущим методом (pbkdf2 с итерациями werkzeug по умолчанию); с перцем — от HMAC(перец, PIN)."""
    if not valid_pin(pin):
        raise ValueError('PIN — 4–6 цифр')
    return wz.generate_password_hash(_secret(pin, pepper), method=PIN_HASH_METHOD)


def check_pin(pin_hash: str | None, pin: str, pepper: Pepper | None = None) -> bool:
    if not pin_hash or not valid_pin(pin):
        return False
    try:
        return wz.check_password_hash(pin_hash, _secret(pin, pepper))
    except (ValueError, TypeError):   # битый хеш в базе — не совпадение, а не падение входа
        return False


def hash_outdated(pin_hash: str) -> bool:
    """Хеш сделан не текущим методом (например, pbkdf2:sha256:60000 до схемы 5) — пересчитать при входе."""
    return pin_hash.split('$', 1)[0] != f'pbkdf2:sha256:{wz.DEFAULT_PBKDF2_ITERATIONS}'


# --- pin_tag (детерминированный: уникальность PIN и быстрый вход) ---

def tag_base(pin: str, salt_hex: str) -> str:
    """tag без перца: pbkdf2 PIN с солью базы."""
    if not valid_pin(pin):
        raise ValueError('PIN — 4–6 цифр')
    return hashlib.pbkdf2_hmac('sha256', pin.encode('ascii'), bytes.fromhex(salt_hex), PIN_TAG_ITERATIONS).hex()


def tag_for(base: str, pepper: Pepper | None) -> str:
    """tag с перцем (или без): перевод tag без перца не требует PIN."""
    if pepper is None:
        return base
    return f'{TAG_PREFIX}{pepper.id}:' + hmac.new(pepper.key, base.encode('ascii'), hashlib.sha256).hexdigest()


def tag_pepper_id(tag: str) -> str | None:
    """Перец tag: None — без перца; '' — неизвестный формат (битый); иначе id перца."""
    if not _TAG_RE.match(tag):
        return ''
    return tag[3:11] if tag.startswith(TAG_PREFIX) else None

# -*- coding: utf-8 -*-
"""Хранилище раздела «Առաքիչ» — SQLite (courier.db), отдельно от route_optimizer.db.

ERP только читаем, поэтому всё состояние терминалов живёт здесь: водители, терминалы, сессии,
события, сканы, фото, сдача денег, настройки маркировки, причины, тара, APK.
Дисциплина — как у route_optimizer.store:
- соединение на операцию, WAL, busy_timeout;
- схема создаётся при первом обращении, версия — meta.schema_version; база старой версии
  мигрирует одной транзакцией (_MIGRATIONS);
- точки дня — append-only снимки (day_snapshots + snapshot_stops): новая версия /day добавляет снимок,
  прежние не перезаписываются — по ним офис видит прошлые даты, а события терминала, сделанные офлайн
  по старой версии накладной, проверяются по всем версиям (контракт §5 п. 3–4). Содержимое точки хранится
  один раз (stop_data, ключ — sha256 JSON): новая версия /day обычно меняет одну накладную из многих, и снимок
  ссылается на прежние строки. Хранение: снимки, на которые ссылаются события, и последний снимок каждой
  (дата, машина) — всегда; прочие промежуточные — SNAPSHOT_KEEP_DAYS дней (_purge_snapshots). Наибольшее qty
  каждой строки точки по всем версиям и её товар (line_max) пишутся вместе со снимком и НЕ удаляются (контракт
  §5 п. 13): проверка количества опоздавшего события не зависит от хранения снимков;
- запись — одна транзакция: всё или ничего;
- битая БД — явная StoreError, а НЕ тихий откат на дефолты.
Все моменты сервера пишутся clock.iso() — всегда со смещением +04:00, поэтому строки сравнимы
лексикографически. Момент терминала (at) может прийти с любой зоной — для порядка есть at_utc.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from . import clock
from .security import (PEPPER_ENV, PEPPER_OLD_ENV, SCHEME_PLAIN, Pepper, check_pin, hash_outdated, hash_pin, new_token,
                       pepper_from_env, same_hash, scheme_of, tag_base, tag_for, tag_pepper_id, token_hash, valid_pin)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 6

PIN_MAX_FAILS = 5
PIN_LOCK = timedelta(minutes=15)
NAME_MAX = 60
TEXT_MAX = 200
TOUCH_TIMEOUT_S = 0.5     # «последняя связь» — best-effort: занятая база не задерживает запрос терминала
SNAPSHOT_KEEP_DAYS = 7    # промежуточные снимки /day без событий хранятся столько дней (по saved_at)

# Пределы на терминал за день (Ереван, по времени получения) — контракт §5 п. 10
PHOTOS_PER_DAY = 300
PHOTO_BYTES_PER_DAY = 300 * 1024 * 1024
REJECTED_PER_DAY = 1000

TRACK_KEEP_DAYS = 400     # трек машины хранится 400 дней (сезонность), затем удаляется — контракт §7 п. 1

SUPERSEDED_BY = 'auto: superseded'   # decided_by предложений водителя, закрытых принятием другого по тому же клиенту

DEFAULT_REASONS: dict[str, tuple[tuple[str, str], ...]] = {
    'refuse': (
        ('closed', 'Խանութը փակ է'),
        ('no_money', 'Գումար չունեն'),
        ('not_ordered', 'Չեն պատվիրել'),
        ('no_space', 'Տեղ չկա'),
        ('price', 'Համաձայն չեն գնի հետ'),
        ('other', 'Այլ պատճառ'),
    ),
    'return': (
        ('defect', 'Խոտան'),
        ('expired', 'Ժամկետանց'),
        ('damaged', 'Վնասված փաթեթավորում'),
        ('other', 'Այլ պատճառ'),
    ),
}
REASON_KINDS = tuple(DEFAULT_REASONS)

# Трек машины (контракт §7 п. 1): точка — одна на (машина, момент at в мс UTC) — повтор с тем же at той же машины
# не дублируется (первая принятая остаётся). date — рабочий день события track. Без rowid: ключ и есть порядок трека
# машины; индекс (машина, день, момент) — чтение дня машины. Событие track в events хранит только счётчики точек.
_TRACK_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS track_points(car_code TEXT NOT NULL, at_ms INTEGER NOT NULL, date TEXT NOT NULL, "
    "lat REAL NOT NULL, lon REAL NOT NULL, acc REAL NOT NULL, spd REAL, brg REAL, PRIMARY KEY (car_code, at_ms)) "
    "WITHOUT ROWID",
    "CREATE INDEX IF NOT EXISTS track_points_day ON track_points(car_code, date, at_ms)",
    "CREATE INDEX IF NOT EXISTS events_car_type ON events(car_code, type, at_utc)",
)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    # pin_hash — хеш секрета PIN схемы pin_scheme ('plain' | 'pepper:<id>'); pin_tag — детерминированный хеш PIN
    # (уникальность PIN без самого PIN, вход за один pbkdf2) — security
    "CREATE TABLE IF NOT EXISTS drivers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
    "pin_hash TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)), "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT, pin_tag TEXT, pin_scheme TEXT)",
    "CREATE INDEX IF NOT EXISTS drivers_pin_tag ON drivers(pin_tag)",
    # failed_pin_count / locked_until — блокировка входа по терминалу (за туннелем у всех один IP)
    "CREATE TABLE IF NOT EXISTS terminals(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
    "car_code TEXT NOT NULL, token_sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, created_by TEXT, "
    "revoked_at TEXT, revoked_by TEXT, failed_pin_count INTEGER NOT NULL DEFAULT 0, pin_window_start TEXT, "
    "locked_until TEXT, last_seen_at TEXT, admin_pin_hash TEXT)",
    # одна действующая сессия на терминал (новая отменяет старую — open_session)
    "CREATE TABLE IF NOT EXISTS sessions(token_sha256 TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, "
    "driver_id INTEGER NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS sessions_terminal ON sessions(terminal_id)",
    # события терминала: id — uuid4 от терминала (идемпотентность); только принятые.
    # snapshot_id — снимок дня, по версии точки в котором событие проверено (цены для «Գումար»)
    "CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, "
    "driver_id INTEGER NOT NULL, car_code TEXT NOT NULL, date TEXT NOT NULL, stop_id TEXT, type TEXT NOT NULL, "
    "at_device TEXT NOT NULL, at_utc TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL, "
    "flags TEXT NOT NULL DEFAULT '[]', snapshot_id INTEGER)",
    "CREATE INDEX IF NOT EXISTS events_day ON events(date, car_code)",
    "CREATE INDEX IF NOT EXISTS events_stop ON events(stop_id, type)",
    "CREATE INDEX IF NOT EXISTS events_driver ON events(driver_id, date)",
    "CREATE INDEX IF NOT EXISTS events_snapshot ON events(snapshot_id)",
    "CREATE INDEX IF NOT EXISTS events_type ON events(type, date)",
    # решение логиста по предложению водителя geo_suggest (driver-geo-plan.md §4); нет строки — предложение открыто
    "CREATE TABLE IF NOT EXISTS geo_suggest_decision(event_id TEXT PRIMARY KEY, decision TEXT NOT NULL "
    "CHECK (decision IN ('accepted','rejected')), decided_at TEXT NOT NULL, decided_by TEXT)",
    # отклонённые события — для «Конца дня» (/status) и офиса; принятое позже с тем же id — удаляется отсюда
    "CREATE TABLE IF NOT EXISTS rejected_events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, "
    "driver_id INTEGER NOT NULL, date TEXT, type TEXT, received_at TEXT NOT NULL, error TEXT NOT NULL, "
    "message TEXT NOT NULL, body TEXT)",
    "CREATE INDEX IF NOT EXISTS rejected_driver ON rejected_events(driver_id, date)",
    "CREATE INDEX IF NOT EXISTS rejected_terminal ON rejected_events(terminal_id, received_at)",
    # сканы маркировки — денормализованы из событий scan для поиска и выгрузки
    "CREATE TABLE IF NOT EXISTS scans(event_id TEXT PRIMARY KEY, raw TEXT NOT NULL, gtin TEXT, serial TEXT, "
    "is_group INTEGER NOT NULL, units REAL NOT NULL, kind TEXT NOT NULL, stop_id TEXT, line_id TEXT, "
    "customer_id INTEGER, customer_code TEXT, customer_name TEXT, tax_id TEXT, doc_number TEXT, "
    "product_id INTEGER, product_code TEXT, product_name TEXT, date TEXT NOT NULL, at_device TEXT NOT NULL, "
    "driver_id INTEGER NOT NULL, driver_name TEXT, car_code TEXT NOT NULL, counted INTEGER NOT NULL, "
    "duplicate_elsewhere INTEGER NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0, cancel_event_id TEXT)",
    "CREATE INDEX IF NOT EXISTS scans_raw ON scans(raw)",
    "CREATE INDEX IF NOT EXISTS scans_date ON scans(date)",
    "CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY, event_id TEXT NOT NULL, kind TEXT NOT NULL, "
    "path TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, terminal_id INTEGER NOT NULL, "
    "received_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS photos_event ON photos(event_id)",
    "CREATE INDEX IF NOT EXISTS photos_terminal ON photos(terminal_id, received_at)",
    # точки дня, как их получал терминал (/day) — append-only снимки: проверка событий и офис
    "CREATE TABLE IF NOT EXISTS day_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, "
    "car_code TEXT NOT NULL, version TEXT NOT NULL, loaded_at TEXT NOT NULL, saved_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS day_snapshots_day ON day_snapshots(date, car_code, id)",
    # содержимое точки — один раз на sha256 JSON (32 байта); снимок ссылается на него
    "CREATE TABLE IF NOT EXISTS stop_data(hash BLOB PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS snapshot_stops(snapshot_id INTEGER NOT NULL, stop_id TEXT NOT NULL, "
    "date TEXT NOT NULL, car_code TEXT NOT NULL, seq INTEGER NOT NULL, data_hash BLOB NOT NULL, "
    "PRIMARY KEY (snapshot_id, stop_id))",
    "CREATE INDEX IF NOT EXISTS snapshot_stops_stop ON snapshot_stops(stop_id, snapshot_id)",
    "CREATE INDEX IF NOT EXISTS snapshot_stops_day ON snapshot_stops(date, snapshot_id)",
    # наибольшее qty строки точки по всем версиям /day и её товар — НЕ удаляется со снимками (контракт §5 п. 13):
    # проверка количества и «код не читается» по товару работают и для опоздавших событий
    "CREATE TABLE IF NOT EXISTS line_max(stop_id TEXT NOT NULL, line_id TEXT NOT NULL, max_qty REAL NOT NULL, "
    "product_id INTEGER, PRIMARY KEY (stop_id, line_id))",
    "CREATE TABLE IF NOT EXISTS cash_handover(date TEXT NOT NULL, driver_id INTEGER NOT NULL, handed REAL NOT NULL, "
    "comment TEXT, updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (date, driver_id))",
    # маркировка: нет строки — стартовое состояние из ERP (PRODUCTS.fMARKABLE, доп. единица)
    "CREATE TABLE IF NOT EXISTS marked_products(product_id INTEGER PRIMARY KEY, marked INTEGER NOT NULL, "
    "pack_qty REAL, updated_at TEXT NOT NULL, updated_by TEXT)",
    "CREATE TABLE IF NOT EXISTS tare_custom(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
    "active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL, updated_by TEXT)",
    "CREATE TABLE IF NOT EXISTS reasons(kind TEXT NOT NULL CHECK (kind IN ('refuse', 'return')), id TEXT NOT NULL, "
    "text TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, "
    "PRIMARY KEY (kind, id))",
    "CREATE TABLE IF NOT EXISTS app_release(version_code INTEGER PRIMARY KEY, version_name TEXT NOT NULL, "
    "sha256 TEXT NOT NULL, size INTEGER NOT NULL, path TEXT NOT NULL, uploaded_at TEXT NOT NULL, uploaded_by TEXT)",
    *_TRACK_SCHEMA,
)

_SEED = (
    # соль pin_tag — случайная на базу (security.pin_tag)
    "INSERT OR IGNORE INTO meta(key, value) VALUES('pin_salt', lower(hex(randomblob(16))))",
)

# Миграции: версия → DDL/DML перехода на следующую (выполняются одной транзакцией с записью версии).
_MIGRATIONS: dict[int, tuple[str | Callable[[sqlite3.Connection], None], ...]] = {
    # v1 → v2: точки дня — снимки вместо перезаписи (каждая прежняя версия (дата, машина) — свой снимок,
    # по времени загрузки); pin_tag; snapshot_id события; O: → S:; индексы пределов на терминал.
    1: (
        "ALTER TABLE drivers ADD COLUMN pin_tag TEXT",
        "CREATE INDEX IF NOT EXISTS drivers_pin_tag ON drivers(pin_tag)",
        "ALTER TABLE events ADD COLUMN snapshot_id INTEGER",
        "CREATE INDEX IF NOT EXISTS rejected_terminal ON rejected_events(terminal_id, received_at)",
        "CREATE INDEX IF NOT EXISTS photos_terminal ON photos(terminal_id, received_at)",
        "CREATE TABLE day_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, "
        "car_code TEXT NOT NULL, version TEXT NOT NULL, loaded_at TEXT NOT NULL, saved_at TEXT NOT NULL)",
        "CREATE INDEX day_snapshots_day ON day_snapshots(date, car_code, id)",
        "CREATE TABLE snapshot_stops(snapshot_id INTEGER NOT NULL, stop_id TEXT NOT NULL, date TEXT NOT NULL, "
        "car_code TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (snapshot_id, stop_id))",
        "CREATE INDEX snapshot_stops_stop ON snapshot_stops(stop_id, snapshot_id)",
        "CREATE INDEX snapshot_stops_day ON snapshot_stops(date, snapshot_id)",
        "CREATE TABLE stop_replaces(order_stop_id TEXT NOT NULL, invoice_stop_id TEXT NOT NULL, date TEXT NOT NULL, "
        "PRIMARY KEY (order_stop_id, invoice_stop_id))",
        "INSERT INTO day_snapshots(date, car_code, version, loaded_at, saved_at) "
        "SELECT date, car_code, version, MAX(loaded_at), MAX(loaded_at) FROM day_stops "
        "GROUP BY date, car_code, version ORDER BY date, car_code, MAX(loaded_at)",
        "INSERT INTO snapshot_stops(snapshot_id, stop_id, date, car_code, seq, data) "
        "SELECT x.id, d.stop_id, d.date, d.car_code, d.seq, d.data FROM day_stops d "
        "JOIN day_snapshots x ON x.date = d.date AND x.car_code = d.car_code AND x.version = d.version",
        "DROP TABLE day_stops",
        *_SEED,
    ),
    # v2 → v3: содержимое точек — один раз (stop_data по sha256), снимки ссылаются; snapshot_id связи O: → S:
    # (по самому новому снимку, где она есть); хеш PIN настроек терминала; индекс снимка событий (хранение).
    2: (
        "ALTER TABLE terminals ADD COLUMN admin_pin_hash TEXT",
        "ALTER TABLE stop_replaces ADD COLUMN snapshot_id INTEGER",
        "UPDATE stop_replaces SET snapshot_id = (SELECT MAX(s.snapshot_id) FROM snapshot_stops s "
        "WHERE s.stop_id = stop_replaces.invoice_stop_id AND EXISTS (SELECT 1 FROM json_each(s.data, '$.replaces') j "
        "WHERE j.value = stop_replaces.order_stop_id))",
        "CREATE TABLE stop_data(hash BLOB PRIMARY KEY, data TEXT NOT NULL)",
        "CREATE TABLE snapshot_stops_v3(snapshot_id INTEGER NOT NULL, stop_id TEXT NOT NULL, date TEXT NOT NULL, "
        "car_code TEXT NOT NULL, seq INTEGER NOT NULL, data_hash BLOB NOT NULL, PRIMARY KEY (snapshot_id, stop_id))",
        lambda conn: _dedup_stop_data(conn),
        "DROP TABLE snapshot_stops",
        "ALTER TABLE snapshot_stops_v3 RENAME TO snapshot_stops",
        "CREATE INDEX snapshot_stops_stop ON snapshot_stops(stop_id, snapshot_id)",
        "CREATE INDEX snapshot_stops_day ON snapshot_stops(date, snapshot_id)",
        "CREATE INDEX IF NOT EXISTS events_snapshot ON events(snapshot_id)",
    ),
    # v3 → v4 (только добавляет): решения по предложениям водителей geo_suggest; индекс событий по типу —
    # точки водителей (arrived) и предложения читаются без полного перебора событий.
    3: (
        "CREATE INDEX IF NOT EXISTS events_type ON events(type, date)",
        "CREATE TABLE IF NOT EXISTS geo_suggest_decision(event_id TEXT PRIMARY KEY, decision TEXT NOT NULL "
        "CHECK (decision IN ('accepted','rejected')), decided_at TEXT NOT NULL, decided_by TEXT)",
    ),
    # v4 → v5: схема хеша PIN (pin_scheme; хеши до v5 — без перца, 60 000 итераций: пересчитываются при входе; tag
    # с перцем несёт id перца — meta.pin_pepper_id больше не нужен); наибольшее qty строк (line_max, не удаляется) —
    # из всех сохранённых снимков; связи заказ → накладная офис берёт из снимков той же даты (stop_replaces больше
    # нет). Шаги повторяемы: таблицы и столбцы, которые уже есть, не мешают.
    4: (
        lambda conn: _add_column(conn, 'drivers', 'pin_scheme', 'TEXT'),
        f"UPDATE drivers SET pin_scheme = '{SCHEME_PLAIN}' WHERE pin_hash IS NOT NULL AND pin_scheme IS NULL",
        lambda conn: _tags_v5(conn),
        "CREATE TABLE IF NOT EXISTS line_max(stop_id TEXT NOT NULL, line_id TEXT NOT NULL, max_qty REAL NOT NULL, "
        "product_id INTEGER, PRIMARY KEY (stop_id, line_id))",
        lambda conn: _fill_line_max(conn),
        "DROP TABLE IF EXISTS stop_replaces",
    ),
    # v5 → v6 (только добавляет, контракт v1.3 §7): трек машины и индекс событий по машине и типу (заправки машины
    # по порядку — проверка одометра, обучение расхода).
    5: _TRACK_SCHEMA,
}


def _stop_hash(text: str) -> bytes:
    """Ключ содержимого точки в stop_data: sha256 JSON (32 байта)."""
    return hashlib.sha256(text.encode('utf-8')).digest()


def _dedup_stop_data(conn: sqlite3.Connection) -> None:
    """Миграция v2 → v3: строки snapshot_stops (data) → stop_data (одна строка на содержимое) + ссылки."""
    rows = conn.execute('SELECT snapshot_id, stop_id, date, car_code, seq, data FROM snapshot_stops')
    for snapshot_id, stop_id, day, car, seq, data in rows.fetchall():
        h = _stop_hash(data)
        conn.execute('INSERT OR IGNORE INTO stop_data(hash, data) VALUES(?, ?)', (h, data))
        conn.execute('INSERT INTO snapshot_stops_v3(snapshot_id, stop_id, date, car_code, seq, data_hash) '
                     'VALUES(?, ?, ?, ?, ?, ?)', (snapshot_id, stop_id, day, car, seq, h))


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {decl}')


def _tags_v5(conn: sqlite3.Connection) -> None:
    """Миграция v4 → v5: tag с перцем 'p1:<hmac>' (перец — meta.pin_pepper_id, 16 hex) → 'p2:<id>:<hmac>' (id —
    первые 8 hex того же отпечатка, security.Pepper.id). Отпечатка в meta нет — tag сбрасывается: вход узнает водителя
    по хешу PIN (хеши до v5 — без перца)."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'pin_pepper_id'").fetchone()
    pid = row[0][:8] if row and re.fullmatch(r'[0-9a-f]{16}', str(row[0])) else None
    for did, tag in conn.execute("SELECT id, pin_tag FROM drivers WHERE pin_tag LIKE 'p1:%'").fetchall():
        conn.execute('UPDATE drivers SET pin_tag = ? WHERE id = ?', (f'p2:{pid}:{tag[3:]}' if pid else None, did))
    conn.execute("DELETE FROM meta WHERE key = 'pin_pepper_id'")


def _line_max_rows(stop: Mapping[str, Any]) -> list[tuple[str, str, float, int | None]]:
    """Строки точки для line_max: (stop_id, line_id, qty, товар); строки без line_id или с нечисловым qty — нет."""
    out = []
    for ln in stop.get('lines') or ():
        if not isinstance(ln, dict) or ln.get('line_id') is None:
            continue
        qty, pid = ln.get('qty'), ln.get('product_id')
        if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty):
            continue
        out.append((str(stop.get('stop_id')), str(ln['line_id']), float(qty),
                    pid if isinstance(pid, int) and not isinstance(pid, bool) else None))
    return out


_LINE_MAX_UPSERT = ('INSERT INTO line_max(stop_id, line_id, max_qty, product_id) VALUES(?, ?, ?, ?) '
                    'ON CONFLICT(stop_id, line_id) DO UPDATE SET max_qty = MAX(max_qty, excluded.max_qty), '
                    'product_id = COALESCE(excluded.product_id, product_id)')


def _fill_line_max(conn: sqlite3.Connection) -> None:
    """Миграция v4 → v5: line_max из всех сохранённых снимков (по возрастанию снимка: товар — из самой новой версии;
    битая точка пропускается)."""
    parsed: dict[bytes, Any] = {}
    for h, raw in conn.execute('SELECT s.data_hash, d.data FROM snapshot_stops s JOIN stop_data d '
                               'ON d.hash = s.data_hash ORDER BY s.snapshot_id, s.stop_id').fetchall():
        if h not in parsed:
            try:
                parsed[h] = json.loads(raw)
            except (TypeError, ValueError):
                parsed[h] = None
        if isinstance(parsed[h], dict):
            conn.executemany(_LINE_MAX_UPSERT, _line_max_rows(parsed[h]))

_FIX_HINT = ' — исправьте или удалите файл; значения по умолчанию молча не подставляются'


class StoreError(RuntimeError):
    """courier.db недоступна или повреждена. Текст — для лога и офиса."""


class PinConflict(ValueError):
    """Такой PIN уже у другого активного водителя: PIN определяет водителя при входе."""


class PinUnverifiable(ValueError):
    """Включить водителя без нового PIN нельзя: у него или у другого активного водителя PIN сохранён до
    схемы 2 (нет pin_tag) — сравнить PIN, не зная его, невозможно; нужен новый PIN."""


class PinPepperMissing(PinConflict):
    """Новый PIN (или включение водителя со старым) не проверить на повтор: у других активных водителей PIN с перцем,
    которого нет в среде, — их PIN неизвестен и может совпасть (вернут перец — у двух водителей один PIN, вход обоих
    отклоняется). Офис восстанавливает перец или явно сбрасывает PIN этих водителей (save_driver(reset_unverifiable=
    True)). drivers — [(id, имя)] таких водителей."""

    def __init__(self, drivers: Sequence[tuple[int, str]]):
        super().__init__('PIN не проверить на повтор: нет перца PIN других водителей')
        self.drivers = list(drivers)


class PinReset(Exception):
    """PIN не узнан, а у активного водителя PIN не проверить — tag (или хеш, если tag нет) с перцем, которого нет в
    среде (COURIER_PIN_PEPPER и COURIER_PIN_PEPPER_OLD): это может быть и опечатка, и его PIN — вход отвечает «неверный
    PIN или PIN задать заново в офисе»."""


class PhotoLimit(Exception):
    """Предел фото терминала за день: kind = 'count' | 'bytes'."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class PinAttempt:
    """Попытка входа, зарезервированная до проверки PIN (Store.pin_attempt): чем откатить её при верном PIN."""
    count_before: int             # ошибок в окне до этой попытки
    window_start: str             # начало окна после резерва
    locked_until: str | None      # блокировку включила именно эта попытка (PIN_MAX_FAILS-я в окне)


@dataclass(frozen=True)
class Driver:
    id: int
    name: str
    active: bool
    has_pin: bool
    updated_at: str | None = None
    pin_reset: bool = False       # хеш PIN с перцем, которого нет в среде: PIN задать заново


@dataclass(frozen=True)
class PinKeys:
    """Соль pin_tag базы и перцы среды: текущий (новые хеши и tag) и прежний (COURIER_PIN_PEPPER_OLD — только
    чтобы узнать водителя и перевести его хеш и tag на текущий)."""
    salt: str
    current: Pepper | None
    old: Pepper | None

    @property
    def scheme(self) -> str:
        return scheme_of(self.current)

    def pepper(self, scheme: str | None) -> tuple[bool, Pepper | None]:
        """Перец схемы хеша: (можно проверить, перец). Без перца — (True, None); перца нет в среде — (False, None)."""
        if scheme in (None, SCHEME_PLAIN):
            return True, None
        for p in (self.current, self.old):
            if p is not None and scheme == p.scheme:
                return True, p
        return False, None

    def tag_known(self, tag: str | None) -> bool:
        """tag сравним с tags(pin): без перца или перца среды (tag перца, которого нет в среде, или битый — нет)."""
        if tag is None:
            return False
        pid = tag_pepper_id(tag)
        return pid is None or pid in {p.id for p in (self.current, self.old) if p is not None}

    def verifiable(self, tag: str | None, scheme: str | None) -> bool:
        """PIN водителя проверяется в этой среде: по tag, а если tag нет или его перца нет в среде — по хешу своей
        схемы (например, хеш без перца после миграции v4 → v5, а tag — с перцем, которого больше нет)."""
        return self.tag_known(tag) or self.pepper(scheme)[0]

    def missing(self, tag: str | None, scheme: str | None) -> set[str]:
        """Отпечатки перцев, на которые ссылаются tag и схема хеша водителя, но которых нет в среде."""
        out = set()
        if tag is not None and not self.tag_known(tag):
            out.add(tag_pepper_id(tag) or '?')
        if not self.pepper(scheme)[0]:
            out.add(str(scheme).split(':', 1)[-1])
        return out

    def tags(self, pin: str) -> tuple[str, set[str]]:
        """(tag текущей схемы, все tag, по которым узнаётся PIN: без перца, текущего и прежнего перца) — один pbkdf2."""
        base = tag_base(pin, self.salt)
        current = tag_for(base, self.current)
        return current, {base, current} | ({tag_for(base, self.old)} if self.old is not None else set())


@dataclass(frozen=True)
class Terminal:
    id: int
    name: str
    car_code: str
    created_at: str
    revoked_at: str | None
    failed_pin_count: int
    locked_until: str | None
    last_seen_at: str | None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


@dataclass(frozen=True)
class Session:
    terminal_id: int
    driver_id: int
    driver_name: str
    expires_at: str


@dataclass(frozen=True)
class Release:
    version_code: int
    version_name: str
    sha256: str
    size: int
    path: str
    uploaded_at: str


@dataclass(frozen=True)
class MarkSetting:
    product_id: int
    marked: bool
    pack_qty: float | None


@dataclass(frozen=True)
class DaySnapshots:
    """Снимки /day одной даты: последний снимок каждой машины (и пустой) и версии точек всех снимков даты."""
    latest: dict[str, int]                                   # машина → id последнего снимка
    stops: tuple[tuple[int, str, dict[str, Any]], ...]       # (снимок, машина, точка с car_code) — снимки по возрастанию


def _now() -> str:
    return clock.iso(clock.now())


def _clean_name(name: Any, what: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f'{what}: пусто')
    name = ' '.join(name.split())
    if len(name) > NAME_MAX:
        raise ValueError(f'{what}: не длиннее {NAME_MAX} символов')
    return name


def _terminal(row: Sequence[Any]) -> Terminal:
    return Terminal(int(row[0]), row[1], row[2], row[3], row[4], int(row[5] or 0), row[6], row[7])


_TERMINAL_COLS = 'id, name, car_code, created_at, revoked_at, failed_pin_count, locked_until, last_seen_at'


def _driver(r: Sequence[Any], keys: PinKeys) -> Driver:
    """Строка (id, name, active, has_pin, updated_at, pin_scheme, pin_tag) → Driver."""
    return Driver(int(r[0]), r[1], bool(r[2]), bool(r[3]), r[4],
                  pin_reset=bool(r[3]) and not keys.verifiable(r[6], r[5]))


class Store:
    """Доступ к courier.db. Соединение открывается на каждую операцию."""

    def __init__(self, path: str):
        self.path = path

    @property
    def photos_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.path)), 'courier_photos')

    @property
    def apk_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.path)), 'courier_apk')

    def _name(self) -> str:
        return f'База «Առաքիչ» {os.path.basename(self.path)}'

    # --- соединение и схема ---

    def _connect(self, timeout: float = 5.0) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=timeout, isolation_level=None)
        try:
            conn.execute(f'PRAGMA busy_timeout = {int(timeout * 1000)}')
            conn.execute('PRAGMA journal_mode = WAL')
            self._ensure_schema(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None or not str(row[0]).isdigit():
            raise StoreError('В базе «Առաքիչ» нет версии схемы')
        return int(row[0])

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'meta' in tables:
            version = Store._schema_version(conn)
            if version > SCHEMA_VERSION:
                raise StoreError(f'База «Առաքիչ» создана более новой версией программы '
                                 f'(схема {version}, поддерживается {SCHEMA_VERSION})')
            if version < SCHEMA_VERSION:
                Store._migrate(conn)
            return
        if tables - {'sqlite_sequence'}:
            raise StoreError('Файл не является базой «Առաքիչ» (есть чужие таблицы)')
        conn.execute('BEGIN IMMEDIATE')
        try:
            for ddl in (*_SCHEMA, *_SEED):
                conn.execute(ddl)
            for kind, items in DEFAULT_REASONS.items():
                for sort, (rid, text) in enumerate(items):
                    conn.execute('INSERT OR IGNORE INTO reasons(kind, id, text, sort, active) VALUES(?, ?, ?, ?, 1)',
                                 (kind, rid, text, sort))
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                         (str(SCHEMA_VERSION),))
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Схема старой версии → SCHEMA_VERSION одной транзакцией: сбой оставляет прежнюю версию."""
        conn.execute('BEGIN IMMEDIATE')
        try:
            version = Store._schema_version(conn)
            while version < SCHEMA_VERSION:
                steps = _MIGRATIONS.get(version)
                if steps is None:
                    raise StoreError(f'Нет миграции схемы базы «Առաքիչ» с версии {version}')
                for ddl in steps:
                    if callable(ddl):
                        ddl(conn)
                    else:
                        conn.execute(ddl)
                version += 1
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(version),))
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise

    def _read(self, query: Callable[[sqlite3.Connection], Any]) -> Any:
        try:
            conn = self._connect()
            try:
                return query(conn)
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise StoreError(f'{self._name()} повреждена или недоступна{_FIX_HINT}') from e

    def _transaction(self, write: Callable[[sqlite3.Connection], Any], failure: str) -> Any:
        """Запись одной транзакцией: всё или ничего. Возвращает то, что вернула write."""
        try:
            conn = self._connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                try:
                    out = write(conn)
                    conn.execute('COMMIT')
                except BaseException:
                    conn.execute('ROLLBACK')
                    raise
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise StoreError(f'{self._name()}: {failure}') from e
        return out

    @contextmanager
    def batch(self) -> Iterator[sqlite3.Connection]:
        """Пачка событий терминала — одна транзакция (BEGIN IMMEDIATE): проверки видят уже принятые
        события этой же пачки, а параллельная пачка того же терминала ждёт."""
        try:
            conn = self._connect()
        except sqlite3.Error as e:
            raise StoreError(f'{self._name()} повреждена или недоступна{_FIX_HINT}') from e
        try:
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise
        except sqlite3.Error as e:
            raise StoreError(f'{self._name()}: не удалось сохранить события') from e
        finally:
            conn.close()

    # --- водители ---

    def list_drivers(self) -> list[Driver]:
        keys = self._pin_keys()
        rows = self._read(lambda c: c.execute(
            'SELECT id, name, active, pin_hash IS NOT NULL, updated_at, pin_scheme, pin_tag FROM drivers '
            'ORDER BY active DESC, name, id').fetchall())
        return [_driver(r, keys) for r in rows]

    def driver(self, driver_id: int) -> Driver | None:
        keys = self._pin_keys()
        r = self._read(lambda c: c.execute(
            'SELECT id, name, active, pin_hash IS NOT NULL, updated_at, pin_scheme, pin_tag FROM drivers WHERE id = ?',
            (driver_id,)).fetchone())
        return _driver(r, keys) if r else None

    def _pin_keys(self) -> PinKeys:
        """Соль pin_tag базы и перцы среды (COURIER_PIN_PEPPER, COURIER_PIN_PEPPER_OLD). Первый вызов (после запуска)
        приводит tag в соответствие со средой: tag без перца при заданном перце переводятся сразу (HMAC от tag, PIN не
        нужен). tag перца, которого нет в среде, НЕ сбрасываются: перец могли не загрузить по ошибке (сервер запущен
        без .env) — вернут перец, и водители входят как раньше; пока его нет, их PIN не проверить (вход — PinReset,
        новый PIN другому водителю — PinPepperMissing). Такие активные водители — WARNING в лог."""
        keys = getattr(self, '_keys', None)
        if keys is not None:
            return keys
        row = self._read(lambda c: c.execute("SELECT value FROM meta WHERE key = 'pin_salt'").fetchone())
        if row is None or not row[0]:
            raise StoreError(f'{self._name()}: нет соли PIN{_FIX_HINT}')
        keys = PinKeys(str(row[0]), pepper_from_env(PEPPER_ENV), pepper_from_env(PEPPER_OLD_ENV))

        def sync(conn: sqlite3.Connection) -> list[tuple[int, str | None, str | None]]:
            if keys.current is not None:
                for did, tag in conn.execute('SELECT id, pin_tag FROM drivers WHERE pin_tag IS NOT NULL').fetchall():
                    if tag_pepper_id(tag) is None:
                        conn.execute('UPDATE drivers SET pin_tag = ? WHERE id = ?', (tag_for(tag, keys.current), did))
            return conn.execute('SELECT id, pin_tag, pin_scheme FROM drivers WHERE active = 1 '
                                'AND pin_hash IS NOT NULL ORDER BY id').fetchall()

        active = self._transaction(sync, 'не удалось обновить pin_tag')
        refs = {int(did): keys.missing(tag, scheme) for did, tag, scheme in active}
        refs = {did: m for did, m in refs.items() if m}
        if refs:
            lost = [int(did) for did, tag, scheme in active if not keys.verifiable(tag, scheme)]
            note = (f'PIN не проверить у {lost} (вход — «PIN задать заново», новый PIN другим водителям — только после '
                    'явного сброса их PIN) — восстановите перец' if lost else
                    'их PIN пока проверяется по хешу без этого перца')
            logger.warning('[Courier] Перца %s нет в среде (%s / %s), а на него ссылаются PIN активных водителей '
                           '%s; %s', sorted(set().union(*refs.values())), PEPPER_ENV, PEPPER_OLD_ENV, sorted(refs), note)
        self._keys = keys
        return keys

    def save_driver(self, driver_id: int | None, name: Any, active: bool, pin: str | None,
                    user: str | None, reset_unverifiable: bool = False) -> int:
        """Создать (driver_id None) или изменить водителя. pin None — не менять PIN.
        PIN определяет водителя при входе, поэтому у активных водителей он уникален (PinConflict) — проверка
        и при новом PIN, и при повторном включении водителя со старым PIN (по pin_tag; PinUnverifiable, если
        сравнить нечем). PIN другого активного водителя не проверить (перца его tag или хеша нет в среде) — он может
        совпасть: PinPepperMissing, пока офис не восстановит перец или явно не сбросит PIN таких водителей
        (reset_unverifiable: их PIN и сессии удаляются в той же транзакции, офис задаёт им новый PIN). Новый PIN — хеш
        и tag текущей схемы. Новый PIN или выключение отменяют сессии водителя."""
        name = _clean_name(name, 'Имя водителя')
        if pin is not None and not valid_pin(pin):
            raise ValueError('PIN — 4–6 цифр')
        keys = self._pin_keys()
        pin_hash = hash_pin(pin, keys.current) if pin is not None else None
        tag, tags = keys.tags(pin) if pin is not None else (None, None)
        now = _now()

        def conflict(conn: sqlite3.Connection, own_tag: str | None) -> None:
            """own_tag — сохранённый tag включаемого водителя (без нового PIN)."""
            others = [r for r in conn.execute('SELECT id, name, pin_hash, pin_tag, pin_scheme FROM drivers '
                                              'WHERE active = 1 AND pin_hash IS NOT NULL ORDER BY id').fetchall()
                      if r[0] != driver_id]
            lost = [(int(r[0]), r[1]) for r in others if not keys.verifiable(r[3], r[4])]
            if lost and not reset_unverifiable:
                raise PinPepperMissing(lost)
            for oid, _ in lost:                  # явный сброс офиса: PIN не проверить — задаётся заново
                conn.execute('UPDATE drivers SET pin_hash = NULL, pin_tag = NULL, pin_scheme = NULL, updated_at = ?, '
                             'updated_by = ? WHERE id = ?', (now, user, oid))
                conn.execute('DELETE FROM sessions WHERE driver_id = ?', (oid,))
            if lost:
                logger.warning('[Courier] PIN водителей %s сброшен офисом (%s): перца их PIN нет в среде',
                               [d for d, _ in lost], user)
            gone = {d for d, _ in lost}
            for oid, _, h, other_tag, other_scheme in others:
                if oid in gone:
                    continue
                if tags is not None:             # новый PIN: tag всех схем среды известен, иначе — по хешу
                    same = (other_tag in tags if keys.tag_known(other_tag)
                            else check_pin(h, pin, keys.pepper(other_scheme)[1]))
                elif other_tag is not None and own_tag is not None \
                        and tag_pepper_id(other_tag) == tag_pepper_id(own_tag):
                    same = other_tag == own_tag
                else:                            # PIN до схемы 2 или tag разных перцев — сравнить нечем
                    raise PinUnverifiable('PIN не проверить — задайте новый')
                if same:
                    raise PinConflict('Такой PIN уже у другого водителя')

        def write(conn: sqlite3.Connection) -> int:
            if active and pin is not None:
                conflict(conn, None)
            elif active and driver_id is not None:
                cur = conn.execute('SELECT active, pin_hash, pin_tag FROM drivers WHERE id = ?', (driver_id,)).fetchone()
                if cur is not None and not cur[0] and cur[1] is not None:   # повторное включение со старым PIN
                    if cur[2] is None:
                        raise PinUnverifiable('PIN не проверить — задайте новый')
                    conflict(conn, cur[2])
            scheme = keys.scheme if pin is not None else None
            if driver_id is None:
                cur = conn.execute('INSERT INTO drivers(name, pin_hash, pin_tag, pin_scheme, active, created_at, '
                                   'updated_at, updated_by) VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
                                   (name, pin_hash, tag, scheme, int(active), now, now, user))
                return int(cur.lastrowid)
            sets, args = 'name = ?, active = ?, updated_at = ?, updated_by = ?', [name, int(active), now, user]
            if pin_hash is not None:
                sets += ', pin_hash = ?, pin_tag = ?, pin_scheme = ?'
                args += [pin_hash, tag, scheme]
            if conn.execute(f'UPDATE drivers SET {sets} WHERE id = ?', (*args, driver_id)).rowcount != 1:
                raise LookupError('Водитель не найден')
            if not active or pin_hash is not None:   # выключен или сменён PIN — прежние сессии недействительны
                conn.execute('DELETE FROM sessions WHERE driver_id = ?', (driver_id,))
            return driver_id

        return self._transaction(write, 'не удалось сохранить водителя')

    def match_pin(self, pin: str) -> list[Driver]:
        """Активные водители с этим PIN (ожидается один; несколько — конфликт старых данных).
        Один pbkdf2 на вход: водитель узнаётся по pin_tag (без перца, текущего или прежнего перца); водитель без tag
        (PIN до схемы 2 или tag, сброшенный прежней версией при смене перца) или с tag перца, которого нет в среде, —
        по хешу своей схемы. Узнанный водитель с хешем или tag не текущей схемы (другой перец, хеш с прежним числом
        итераций) сразу получает новые — один раз.
        Никто не узнан, а у активного водителя PIN не проверить (tag или хеш перца, которого нет в среде) — PinReset:
        неверный PIN или PIN этого водителя (его задаёт офис) — не различить."""
        if not valid_pin(pin):
            return []
        keys = self._pin_keys()
        tag, tags = keys.tags(pin)
        rows = self._read(lambda c: c.execute(
            'SELECT id, name, pin_hash, updated_at, pin_tag, pin_scheme FROM drivers WHERE active = 1 '
            'AND pin_hash IS NOT NULL ORDER BY id').fetchall())
        out, stale, lost = [], [], False
        for did, name, h, updated, row_tag, scheme in rows:
            if keys.tag_known(row_tag):
                if row_tag not in tags:
                    continue
            else:                                # tag нет или его перца нет в среде — по хешу своей схемы
                verifiable, pepper = keys.pepper(scheme)
                if not verifiable:
                    lost = True
                    continue
                if not check_pin(h, pin, pepper):
                    continue
            out.append(Driver(int(did), name, True, True, updated))
            if row_tag != tag or scheme != keys.scheme or hash_outdated(h):
                stale.append((int(did), h))
        if stale:
            self._rehash(pin, keys, tag, stale)
        if not out and lost:
            raise PinReset('PIN водителя не проверить: нет перца его хеша')
        return out

    def _rehash(self, pin: str, keys: PinKeys, tag: str, stale: Sequence[tuple[int, str]]) -> None:
        """Хеш и tag текущей схемы водителям, узнанным при входе (PIN известен). Хеш сменили в офисе за это время —
        не трогаем (AND pin_hash = прежний). Сбой записи — только в лог: вход не падает, перевод — при следующем."""
        new = [(hash_pin(pin, keys.current), did, old) for did, old in stale]

        def write(conn: sqlite3.Connection) -> None:
            for h, did, old in new:
                conn.execute('UPDATE drivers SET pin_hash = ?, pin_tag = ?, pin_scheme = ? WHERE id = ? AND pin_hash = ?',
                             (h, tag, keys.scheme, did, old))
        try:
            self._transaction(write, 'не удалось обновить хеш PIN')
        except StoreError:
            logger.warning('[Courier] Хеш PIN водителей %s не обновлён', [d for d, _ in stale], exc_info=True)

    # --- терминалы ---

    def create_terminal(self, name: Any, car_code: Any, user: str | None,
                        admin_pin: str | None = None) -> tuple[Terminal, str]:
        """Новый терминал; токен возвращается ОДИН раз (в базе — только sha256). admin_pin — PIN скрытых
        настроек терминала (контракт §5 п. 11): в базе только хеш."""
        name = _clean_name(name, 'Имя терминала')
        if not isinstance(car_code, str) or not car_code.strip() or len(car_code.strip()) > 20:
            raise ValueError('Машина не выбрана')
        admin_hash = hash_pin(admin_pin) if admin_pin is not None else None
        token = new_token()
        now = _now()

        def write(conn: sqlite3.Connection) -> int:
            cur = conn.execute('INSERT INTO terminals(name, car_code, token_sha256, created_at, created_by, '
                               'admin_pin_hash) VALUES(?, ?, ?, ?, ?, ?)',
                               (name, car_code.strip(), token_hash(token), now, user, admin_hash))
            return int(cur.lastrowid)

        tid = self._transaction(write, 'не удалось создать терминал')
        terminal = self.terminal(tid)
        assert terminal is not None
        return terminal, token

    def list_terminals(self) -> list[Terminal]:
        rows = self._read(lambda c: c.execute(
            f'SELECT {_TERMINAL_COLS} FROM terminals ORDER BY revoked_at IS NOT NULL, name, id').fetchall())
        return [_terminal(r) for r in rows]

    def terminal(self, terminal_id: int) -> Terminal | None:
        r = self._read(lambda c: c.execute(f'SELECT {_TERMINAL_COLS} FROM terminals WHERE id = ?',
                                           (terminal_id,)).fetchone())
        return _terminal(r) if r else None

    def terminal_by_token_hash(self, digest: str) -> Terminal | None:
        """Терминал по sha256 токена (отозванный — тоже: решает вызывающий)."""
        r = self._read(lambda c: c.execute(f'SELECT {_TERMINAL_COLS}, token_sha256 FROM terminals '
                                           'WHERE token_sha256 = ?', (digest,)).fetchone())
        return _terminal(r) if r is not None and same_hash(r[8], digest) else None

    def revoke_terminal(self, terminal_id: int, user: str | None) -> bool:
        now = _now()

        def write(conn: sqlite3.Connection) -> bool:
            n = conn.execute('UPDATE terminals SET revoked_at = ?, revoked_by = ? WHERE id = ? AND revoked_at IS NULL',
                             (now, user, terminal_id)).rowcount
            conn.execute('DELETE FROM sessions WHERE terminal_id = ?', (terminal_id,))
            return n == 1

        return self._transaction(write, 'не удалось отозвать терминал')

    def touch_terminal(self, terminal_id: int) -> bool:
        """Последняя связь терминала (для «Առաքում այսօր») — best-effort: короткое ожидание блокировки, любая
        ошибка базы только в лог (запрос терминала из-за неё не падает). True — записано."""
        now = _now()
        try:
            conn = self._connect(timeout=TOUCH_TIMEOUT_S)
            try:
                conn.execute('UPDATE terminals SET last_seen_at = ? WHERE id = ?', (now, terminal_id))
            finally:
                conn.close()
        except (sqlite3.Error, StoreError) as e:   # ожидаемо при нагрузке — без трассировки
            logger.warning('[Courier] Последняя связь терминала %s не записана: %s', terminal_id, e)
            return False
        return True

    # --- вход по PIN ---

    def pin_attempt(self, terminal_id: int) -> PinAttempt | str:
        """Атомарно (BEGIN IMMEDIATE): блокировка терминала → её locked_until (str); иначе попытка входа
        РЕЗЕРВИРУЕТСЯ как неверная ещё до проверки PIN — параллельные входы не проверят больше PIN_MAX_FAILS
        PIN. Верный PIN откатывает резерв (pin_release). Ошибки считаются в окне PIN_LOCK от первой ошибки окна,
        и успешный вход окно НЕ сбрасывает: иначе водитель со своим PIN перебирал бы чужой (4 попытки, вход,
        снова 4…). PIN_MAX_FAILS-я попытка окна сразу включает блокировку на PIN_LOCK (окно обнуляется —
        после блокировки снова PIN_MAX_FAILS попыток)."""
        now = clock.now()
        now_s, window_from = clock.iso(now), clock.iso(now - PIN_LOCK)

        def write(conn: sqlite3.Connection) -> PinAttempt | str:
            row = conn.execute('SELECT failed_pin_count, pin_window_start, locked_until FROM terminals WHERE id = ?',
                               (terminal_id,)).fetchone()
            if row is None:
                raise LookupError('Терминал не найден')
            count, window, locked = int(row[0] or 0), row[1], row[2]
            if locked and locked > now_s:
                return str(locked)
            if window is None or window <= window_from:
                count, window = 0, now_s
            if count + 1 >= PIN_MAX_FAILS:
                until = clock.iso(now + PIN_LOCK)
                conn.execute('UPDATE terminals SET failed_pin_count = 0, pin_window_start = NULL, locked_until = ? '
                             'WHERE id = ?', (until, terminal_id))
                return PinAttempt(count, window, until)
            conn.execute('UPDATE terminals SET failed_pin_count = ?, pin_window_start = ? WHERE id = ?',
                         (count + 1, window, terminal_id))
            return PinAttempt(count, window, None)

        return self._transaction(write, 'не удалось записать попытку входа')

    def pin_release(self, terminal_id: int, attempt: PinAttempt) -> None:
        """PIN верный: снять резерв попытки (счётчик — как до неё; блокировку, включённую ею, — снять)."""
        def write(conn: sqlite3.Connection) -> None:
            conn.execute('UPDATE terminals SET failed_pin_count = ?, pin_window_start = ?, locked_until = CASE '
                         'WHEN locked_until = ? THEN NULL ELSE locked_until END WHERE id = ?',
                         (attempt.count_before, attempt.window_start, attempt.locked_until, terminal_id))

        self._transaction(write, 'не удалось записать вход')

    def open_session(self, terminal_id: int, driver_id: int, expires_at: datetime) -> str:
        """Новая сессия водителя на терминале: прежние сессии терминала отменяются, просроченные сессии всех
        терминалов удаляются. Счётчик ошибок PIN НЕ сбрасывается (см. pin_attempt). Токен — один раз."""
        token = new_token()
        now = _now()

        def write(conn: sqlite3.Connection) -> None:
            conn.execute('DELETE FROM sessions WHERE terminal_id = ? OR expires_at <= ?', (terminal_id, now))
            conn.execute('INSERT INTO sessions(token_sha256, terminal_id, driver_id, created_at, expires_at) '
                         'VALUES(?, ?, ?, ?, ?)', (token_hash(token), terminal_id, driver_id, now, clock.iso(expires_at)))

        self._transaction(write, 'не удалось открыть сессию')
        return token

    def session(self, digest: str, terminal_id: int) -> Session | None:
        """Действующая сессия этого терминала с активным водителем; чужая, просроченная — None."""
        now = _now()
        r = self._read(lambda c: c.execute(
            'SELECT s.terminal_id, s.driver_id, d.name, s.expires_at, s.token_sha256 FROM sessions s '
            'JOIN drivers d ON d.id = s.driver_id WHERE s.token_sha256 = ? AND d.active = 1',
            (digest,)).fetchone())
        if r is None or not same_hash(r[4], digest) or int(r[0]) != terminal_id or r[3] <= now:
            return None
        return Session(int(r[0]), int(r[1]), r[2], r[3])

    def close_session(self, digest: str) -> None:
        self._transaction(lambda c: c.execute('DELETE FROM sessions WHERE token_sha256 = ?', (digest,)),
                          'не удалось закрыть сессию')

    # --- точки дня (как их получил терминал) ---

    def save_day(self, day: str, car_code: str, stops: Sequence[Mapping[str, Any]], version: str,
                 loaded_at: str) -> int:
        """Точки машины на дату — новый снимок, если версия изменилась (прежние снимки не трогаются: события
        по исчезнувшим точкам и прошлые версии накладных остаются проверяемыми). Возвращает id действующего
        снимка. Содержимое точки пишется в stop_data один раз (тот же JSON — та же строка); наибольшее qty строк и
        их товар — в line_max (не удаляется). Новый снимок заодно убирает старые промежуточные (_purge_snapshots)."""
        saved_at = _now()

        def write(conn: sqlite3.Connection) -> int:
            row = conn.execute('SELECT id, version FROM day_snapshots WHERE date = ? AND car_code = ? '
                               'ORDER BY id DESC LIMIT 1', (day, car_code)).fetchone()
            if row is not None and row[1] == version:
                return int(row[0])
            sid = int(conn.execute('INSERT INTO day_snapshots(date, car_code, version, loaded_at, saved_at) '
                                   'VALUES(?, ?, ?, ?, ?)', (day, car_code, version, loaded_at, saved_at)).lastrowid)
            for s in stops:
                text = json.dumps(s, ensure_ascii=False, sort_keys=True)
                h = _stop_hash(text)
                conn.execute('INSERT OR IGNORE INTO stop_data(hash, data) VALUES(?, ?)', (h, text))
                conn.execute('INSERT INTO snapshot_stops(snapshot_id, stop_id, date, car_code, seq, data_hash) '
                             'VALUES(?, ?, ?, ?, ?, ?)', (sid, s['stop_id'], day, car_code, int(s['seq']), h))
                conn.executemany(_LINE_MAX_UPSERT, _line_max_rows(s))
            _purge_snapshots(conn)
            return sid

        return self._transaction(write, 'не удалось сохранить точки дня')

    def day_stops(self, day: str, car_code: str | None = None) -> list[dict[str, Any]]:
        """Действующие точки на дату (последний снимок каждой машины или одной): данные стопа + car_code,
        по машине и seq."""
        car_sql, args = ('', (day,)) if car_code is None else (' AND car_code = ?', (day, car_code))
        rows = self._read(lambda c: c.execute(
            'SELECT s.car_code, d.data FROM snapshot_stops s JOIN (SELECT MAX(id) AS id FROM day_snapshots '
            f'WHERE date = ?{car_sql} GROUP BY car_code) x ON x.id = s.snapshot_id '
            'JOIN stop_data d ON d.hash = s.data_hash ORDER BY s.car_code, s.seq',
            args).fetchall())
        return [_stop_json(r[0], r[1], self._name()) for r in rows]

    def stop_versions(self, stop_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        """Все сохранённые версии точек: stop_id → [{snapshot_id, date, car_code, data}] по возрастанию снимка."""
        out: dict[str, list[dict[str, Any]]] = {}
        ids = sorted({s for s in stop_ids if s})
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = self._read(lambda c: c.execute(
                'SELECT s.stop_id, s.snapshot_id, s.date, s.car_code, d.data FROM snapshot_stops s '
                'JOIN stop_data d ON d.hash = s.data_hash '
                f"WHERE s.stop_id IN ({','.join('?' * len(chunk))}) ORDER BY s.snapshot_id", chunk).fetchall())
            for r in rows:
                out.setdefault(r[0], []).append({'snapshot_id': int(r[1]), 'date': r[2], 'car_code': r[3],
                                                 'data': _stop_json(r[3], r[4], self._name())})
        return out

    def day_snapshots(self, day: str) -> DaySnapshots:
        """Все снимки /day на дату: последний снимок каждой машины и версии точек всех снимков (по возрастанию снимка,
        по seq). Офис строит из них точки (машина, дата) для правила §5 п. 12: снимки других дат день не меняют."""
        def query(c: sqlite3.Connection) -> tuple[list[Any], list[Any]]:
            latest = c.execute('SELECT car_code, MAX(id) FROM day_snapshots WHERE date = ? GROUP BY car_code',
                               (day,)).fetchall()
            rows = c.execute('SELECT s.snapshot_id, s.car_code, s.data_hash, d.data FROM snapshot_stops s '
                             'JOIN stop_data d ON d.hash = s.data_hash WHERE s.date = ? ORDER BY s.snapshot_id, s.seq',
                             (day,)).fetchall()
            return latest, rows

        latest, rows = self._read(query)
        parsed: dict[bytes, dict[str, Any]] = {}
        stops = []
        for sid, car, h, raw in rows:
            if h not in parsed:
                parsed[h] = _stop_json(car, raw, self._name())
            stops.append((int(sid), car, {**parsed[h], 'car_code': car}))
        return DaySnapshots({car: int(i) for car, i in latest}, tuple(stops))

    # --- события (чтение для офиса и /status) ---

    def events_for_day(self, day: str, etype: str | None = None) -> list[dict[str, Any]]:
        """События даты (etype — только этого типа, по индексу events_type)."""
        rows = self._read(lambda c: c.execute(
            'SELECT e.id, e.terminal_id, e.driver_id, d.name, e.car_code, e.date, e.stop_id, e.type, e.at_device, '
            'e.at_utc, e.received_at, e.payload, e.flags, e.snapshot_id FROM events e '
            'LEFT JOIN drivers d ON d.id = e.driver_id '
            'WHERE e.date = ?' + (' AND e.type = ?' if etype else '') + ' ORDER BY e.at_utc, e.received_at, e.id',
            (day, etype) if etype else (day,)).fetchall())
        return [_event_json(r, self._name()) for r in rows]

    # --- трек и заправки машин (контракт v1.3 §7): офис и обучение «Развоза» ---

    def track(self, car_code: str, day: str) -> list[tuple[int, float, float, float, float | None, float | None]]:
        """Точки трека машины за рабочий день: (at_ms, lat, lon, acc, spd, brg) по возрастанию момента."""
        return [tuple(r) for r in self._read(lambda c: c.execute(
            'SELECT at_ms, lat, lon, acc, spd, brg FROM track_points WHERE car_code = ? AND date = ? ORDER BY at_ms',
            (car_code, day)).fetchall())]

    def track_days(self, since: str, until: str) -> list[tuple[str, str]]:
        """(день, машина) с принятым треком за даты since…until включительно — по событиям track (индекс по типу)."""
        return [(r[0], r[1]) for r in self._read(lambda c: c.execute(
            "SELECT DISTINCT date, car_code FROM events WHERE type = 'track' AND date >= ? AND date <= ? "
            'ORDER BY date, car_code', (since, until)).fetchall())]

    def day_version(self, car_code: str, day: str) -> tuple[Any, ...]:
        """Отпечаток данных машины за рабочий день для кэша факта: (точек трека, последний момент; доставок даты,
        последнее получение; последний снимок /day машины). Не изменился — трек, точки и доставки те же."""
        def query(c: sqlite3.Connection) -> tuple[Any, ...]:
            track = c.execute('SELECT COUNT(*), MAX(at_ms) FROM track_points WHERE car_code = ? AND date = ?',
                              (car_code, day)).fetchone()
            deliv = c.execute("SELECT COUNT(*), MAX(received_at) FROM events WHERE type = 'delivery' AND date = ?",
                              (day,)).fetchone()
            snap = c.execute('SELECT MAX(id) FROM day_snapshots WHERE date = ? AND car_code = ?', (day, car_code)).fetchone()
            return (*track, *deliv, snap[0])
        return self._read(query)

    def refuels(self) -> list[dict[str, Any]]:
        """Все принятые заправки: [{id, car_code, date, at, at_utc, driver_name, payload, flags, superseded, eff_at_utc,
        eff_at, eff_date}] по машине и моменту. superseded — на событие ссылается `supersedes` другой заправки той же
        машины (исправлено водителем): в расчёты не идёт. eff_* — момент и день исходной заправки цепочки исправлений
        (исправление пришло позже — заправка всё равно была тогда); исходной ещё нет на сервере — свои. Флаг
        odometer_suspicious — как поставлен при приёме; читатели пересчитывают его сами (learning.odometer_plausible).
        Заправок немного (единицы в день) — читаются целиком."""
        rows = self._read(lambda c: c.execute(
            "SELECT e.id, e.car_code, e.date, e.at_device, e.at_utc, d.name, e.payload, e.flags FROM events e "
            "LEFT JOIN drivers d ON d.id = e.driver_id WHERE e.type = 'refuel' ORDER BY e.car_code, e.at_utc, e.id"
        ).fetchall())
        out = []
        for eid, car, day, at, at_utc, name, raw, flags in rows:
            try:
                payload, fl = json.loads(raw), json.loads(flags)
            except (TypeError, ValueError) as e:
                raise StoreError(f'{self._name()}: повреждено событие {eid!r}{_FIX_HINT}') from e
            out.append({'id': eid, 'car_code': car, 'date': day, 'at': at, 'at_utc': at_utc, 'driver_name': name,
                        'payload': payload if isinstance(payload, dict) else {},
                        'flags': fl if isinstance(fl, list) else []})
        targets = {(r['car_code'], r['payload'].get('supersedes')) for r in out}
        by_id = {r['id']: r for r in out}
        for r in out:
            r['superseded'] = (r['car_code'], r['id']) in targets
            root, seen = r, {r['id']}
            while True:   # к исходной заправке цепочки исправлений (циклы отклоняются при приёме — защита всё равно)
                nxt = by_id.get(root['payload'].get('supersedes'))
                if nxt is None or nxt['car_code'] != r['car_code'] or nxt['id'] in seen:
                    break
                seen.add(nxt['id'])
                root = nxt
            r['eff_at_utc'], r['eff_at'], r['eff_date'] = root['at_utc'], root['at'], root['date']
        return out

    # --- точки и предложения водителей (driver-geo-plan.md §4): клиент точки — по самой новой версии в снимках /day ---

    def arrived_fixes(self, since: str, until: str) -> list[tuple[int, str, Any, Any, Any]]:
        """Отметки arrived за даты since…until (включительно): (клиент, дата, lat, lon, accuracy) как прислал терминал.
        Не учитываются: точка, которую /day никому не выдавал (unknown_stop, клиента нет), и событие с флагом
        date_suspicious (дата не сходится с моментом терминала — день отметки неизвестен)."""
        rows = self._read(lambda c: c.execute(
            f"SELECT {_stop_customer_sql('$.customer.id')}, e.date, json_extract(e.payload, '$.lat'), "
            "json_extract(e.payload, '$.lon'), json_extract(e.payload, '$.accuracy') FROM events e "
            "WHERE e.type = 'arrived' AND e.date >= ? AND e.date <= ? AND e.stop_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM json_each(e.flags) f WHERE f.value = 'date_suspicious')",
            (since, until)).fetchall())
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows if isinstance(r[0], int) and not isinstance(r[0], bool)]

    def open_suggestions(self) -> list[dict[str, Any]]:
        """Предложения водителей geo_suggest без решения, новые первыми (по моменту терминала)."""
        rows = self._read(lambda c: c.execute(_SUGGEST_SQL + ' ORDER BY e.at_utc DESC, e.received_at DESC, e.id DESC').fetchall())
        return [x for r in rows if (x := _suggestion(r)) is not None]

    def decide_suggestion(self, event_id: str, decision: str, user: str | None,
                          apply: Callable[[dict[str, Any]], None]) -> dict[str, Any] | None:
        """Решение по открытому предложению. Решение пишется в транзакции courier.db, apply(предложение) вызывается
        внутри неё до COMMIT: исключение в apply — решения нет, предложение остаётся открытым. apply пишет в другую
        базу (ручная точка — route_optimizer.db) и фиксирует её сам, поэтому это не одна атомарная транзакция: если
        после успешного apply не удастся COMMIT courier.db, точка уже сохранена, а предложение останется открытым
        (повторное «принять» безопасно — та же точка). Параллельное решение ждёт (BEGIN IMMEDIATE) и получает None.
        accepted — остальные открытые предложения того же клиента закрываются как rejected с decided_by
        SUPERSEDED_BY (точка уже выбрана); их id — в поле superseded результата.
        None — предложения нет, клиент неизвестен или решение уже есть."""
        if decision not in ('accepted', 'rejected'):
            raise ValueError('decision: accepted или rejected')

        def write(conn: sqlite3.Connection) -> dict[str, Any] | None:
            r = conn.execute(_SUGGEST_SQL + ' AND e.id = ?', (event_id,)).fetchone()
            sug = _suggestion(r) if r is not None else None
            if sug is None:
                return None
            now = _now()
            conn.execute('INSERT INTO geo_suggest_decision(event_id, decision, decided_at, decided_by) '
                         'VALUES(?, ?, ?, ?)', (event_id, decision, now, user))
            sug['superseded'] = []
            if decision == 'accepted':
                for r in conn.execute(_SUGGEST_SQL).fetchall():
                    other = _suggestion(r)
                    if other is not None and other['customer_id'] == sug['customer_id']:
                        conn.execute('INSERT INTO geo_suggest_decision(event_id, decision, decided_at, decided_by) '
                                     "VALUES(?, 'rejected', ?, ?)", (other['event_id'], now, SUPERSEDED_BY))
                        sug['superseded'].append(other['event_id'])
            apply(sug)
            return sug

        return self._transaction(write, 'не удалось сохранить решение по предложению водителя')

    def rejected_for(self, driver_id: int, day: str) -> list[dict[str, Any]]:
        rows = self._read(lambda c: c.execute(
            'SELECT id, message, type, received_at FROM rejected_events WHERE driver_id = ? '
            'AND (date = ? OR (date IS NULL AND substr(received_at, 1, 10) = ?)) ORDER BY received_at, id',
            (driver_id, day, day)).fetchall())
        return [{'id': r[0], 'message': r[1], 'type': r[2], 'received_at': r[3]} for r in rows]

    def rejected_for_day(self, day: str) -> list[dict[str, Any]]:
        rows = self._read(lambda c: c.execute(
            'SELECT r.id, r.message, r.type, r.received_at, r.driver_id, d.name, r.terminal_id FROM rejected_events r '
            'LEFT JOIN drivers d ON d.id = r.driver_id '
            'WHERE r.date = ? OR (r.date IS NULL AND substr(r.received_at, 1, 10) = ?) ORDER BY r.received_at',
            (day, day)).fetchall())
        return [{'id': r[0], 'message': r[1], 'type': r[2], 'received_at': r[3], 'driver_id': r[4],
                 'driver_name': r[5], 'terminal_id': r[6]} for r in rows]

    def photos_for_events(self, event_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        ids = list(dict.fromkeys(event_ids))
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = self._read(lambda c: c.execute(
                f"SELECT id, event_id, kind, size FROM photos WHERE event_id IN ({','.join('?' * len(chunk))})",
                chunk).fetchall())
            for r in rows:
                out.setdefault(r[1], []).append({'id': r[0], 'kind': r[2], 'size': r[3]})
        return out

    def photo(self, photo_id: str) -> dict[str, Any] | None:
        r = self._read(lambda c: c.execute('SELECT id, event_id, kind, path, size FROM photos WHERE id = ?',
                                           (photo_id,)).fetchone())
        return {'id': r[0], 'event_id': r[1], 'kind': r[2], 'path': r[3], 'size': r[4]} if r else None

    def save_photo(self, photo_id: str, event_id: str, kind: str, path: str, size: int, sha256: str,
                   terminal_id: int) -> bool:
        """Фото идемпотентно по id: True — записано, False — такое id уже было. Пределы терминала за день
        (PHOTOS_PER_DAY, PHOTO_BYTES_PER_DAY) проверяются в той же транзакции — PhotoLimit."""
        now = _now()
        since = _day_start(now)

        def write(conn: sqlite3.Connection) -> bool:
            if conn.execute('SELECT 1 FROM photos WHERE id = ?', (photo_id,)).fetchone() is not None:
                return False
            n, total = conn.execute('SELECT COUNT(*), COALESCE(SUM(size), 0) FROM photos WHERE terminal_id = ? '
                                    'AND received_at >= ?', (terminal_id, since)).fetchone()
            if int(n) >= PHOTOS_PER_DAY:
                raise PhotoLimit('count')
            if int(total) + size > PHOTO_BYTES_PER_DAY:
                raise PhotoLimit('bytes')
            return conn.execute(
                'INSERT OR IGNORE INTO photos(id, event_id, kind, path, size, sha256, terminal_id, received_at) '
                'VALUES(?, ?, ?, ?, ?, ?, ?, ?)', (photo_id, event_id, kind, path, size, sha256, terminal_id, now)
            ).rowcount == 1

        return self._transaction(write, 'не удалось сохранить фото')

    def photo_usage(self, terminal_id: int) -> tuple[int, int]:
        """Фото терминала за сегодня (Ереван): (штук, байт) — быстрый отказ до чтения файла."""
        since = _day_start(_now())
        n, total = self._read(lambda c: c.execute(
            'SELECT COUNT(*), COALESCE(SUM(size), 0) FROM photos WHERE terminal_id = ? AND received_at >= ?',
            (terminal_id, since)).fetchone())
        return int(n), int(total)

    def has_photo(self, photo_id: str) -> bool:
        return self._read(lambda c: c.execute('SELECT 1 FROM photos WHERE id = ?', (photo_id,)).fetchone()) is not None

    # --- сканы (поиск и выгрузка «Մակնշում») ---

    def search_scans(self, query: str = '', date_from: str | None = None, date_to: str | None = None,
                     limit: int = 5000) -> list[dict[str, Any]]:
        where, args = ['1 = 1'], []
        if date_from:
            where.append('s.date >= ?')
            args.append(date_from)
        if date_to:
            where.append('s.date <= ?')
            args.append(date_to)
        q = query.strip()
        if q:
            like = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            where.append("(s.raw LIKE ? ESCAPE '\\' OR s.gtin LIKE ? ESCAPE '\\' OR s.serial LIKE ? ESCAPE '\\' "
                         "OR s.customer_name LIKE ? ESCAPE '\\' OR s.customer_code LIKE ? ESCAPE '\\' "
                         "OR s.tax_id LIKE ? ESCAPE '\\' OR s.doc_number LIKE ? ESCAPE '\\')")
            args.extend([like] * 7)
        rows = self._read(lambda c: c.execute(
            'SELECT s.event_id, s.raw, s.gtin, s.serial, s.is_group, s.units, s.kind, s.stop_id, s.customer_id, '
            's.customer_code, s.customer_name, s.tax_id, s.doc_number, s.product_id, s.product_code, s.product_name, '
            's.date, s.at_device, s.driver_name, s.car_code, s.counted, s.duplicate_elsewhere, s.cancelled '
            f'FROM scans s WHERE {" AND ".join(where)} ORDER BY s.date DESC, s.at_device DESC, s.event_id LIMIT ?',
            (*args, limit)).fetchall())
        keys = ('event_id', 'raw', 'gtin', 'serial', 'is_group', 'units', 'kind', 'stop_id', 'customer_id',
                'customer_code', 'customer_name', 'tax_id', 'doc_number', 'product_id', 'product_code',
                'product_name', 'date', 'at', 'driver_name', 'car_code', 'counted', 'duplicate_elsewhere',
                'cancelled')
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            for k in ('is_group', 'counted', 'duplicate_elsewhere', 'cancelled'):
                d[k] = bool(d[k])
            out.append(d)
        return out

    # --- сдача денег ---

    def handovers(self, day: str) -> dict[int, dict[str, Any]]:
        rows = self._read(lambda c: c.execute(
            'SELECT driver_id, handed, comment, updated_at, updated_by FROM cash_handover WHERE date = ?',
            (day,)).fetchall())
        return {int(r[0]): {'handed': float(r[1]), 'comment': r[2], 'handed_at': r[3], 'handed_by': r[4]}
                for r in rows}

    def save_handover(self, day: str, driver_id: int, handed: float | None, comment: str | None,
                      user: str | None) -> None:
        """Кассир отметил «сдал фактически» (handed None — снять отметку). Сумма ≥ 0, 2 знака."""
        now = _now()

        def write(conn: sqlite3.Connection) -> None:
            if handed is None:
                conn.execute('DELETE FROM cash_handover WHERE date = ? AND driver_id = ?', (day, driver_id))
                return
            conn.execute('INSERT INTO cash_handover(date, driver_id, handed, comment, updated_at, updated_by) '
                         'VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(date, driver_id) DO UPDATE SET '
                         'handed = excluded.handed, comment = excluded.comment, updated_at = excluded.updated_at, '
                         'updated_by = excluded.updated_by', (day, driver_id, round(handed, 2), comment, now, user))

        self._transaction(write, 'не удалось сохранить сдачу денег')

    # --- настройки: маркировка, тара, причины ---

    def mark_settings(self) -> dict[int, MarkSetting]:
        rows = self._read(lambda c: c.execute('SELECT product_id, marked, pack_qty FROM marked_products').fetchall())
        return {int(r[0]): MarkSetting(int(r[0]), bool(r[1]), None if r[2] is None else float(r[2])) for r in rows}

    def save_mark_settings(self, items: Sequence[MarkSetting], user: str | None) -> None:
        now = _now()

        def write(conn: sqlite3.Connection) -> None:
            for m in items:
                conn.execute('INSERT INTO marked_products(product_id, marked, pack_qty, updated_at, updated_by) '
                             'VALUES(?, ?, ?, ?, ?) ON CONFLICT(product_id) DO UPDATE SET marked = excluded.marked, '
                             'pack_qty = excluded.pack_qty, updated_at = excluded.updated_at, '
                             'updated_by = excluded.updated_by', (m.product_id, int(m.marked), m.pack_qty, now, user))

        self._transaction(write, 'не удалось сохранить маркировку')

    def tare_custom(self, active_only: bool = True) -> list[dict[str, Any]]:
        rows = self._read(lambda c: c.execute(
            'SELECT id, name, active FROM tare_custom' + (' WHERE active = 1' if active_only else '') + ' ORDER BY id'
        ).fetchall())
        return [{'id': int(r[0]), 'name': r[1], 'active': bool(r[2])} for r in rows]

    def save_tare_custom(self, tare_id: int | None, name: Any, active: bool, user: str | None) -> int:
        name = _clean_name(name, 'Название тары')
        now = _now()

        def write(conn: sqlite3.Connection) -> int:
            if tare_id is None:
                return int(conn.execute('INSERT INTO tare_custom(name, active, updated_at, updated_by) VALUES(?, ?, ?, ?)',
                                        (name, int(active), now, user)).lastrowid)
            if conn.execute('UPDATE tare_custom SET name = ?, active = ?, updated_at = ?, updated_by = ? WHERE id = ?',
                            (name, int(active), now, user, tare_id)).rowcount != 1:
                raise LookupError('Тара не найдена')
            return tare_id

        return self._transaction(write, 'не удалось сохранить тару')

    def reasons(self, kind: str, active_only: bool = True) -> list[dict[str, Any]]:
        rows = self._read(lambda c: c.execute(
            'SELECT id, text, active FROM reasons WHERE kind = ?' + (' AND active = 1' if active_only else '')
            + ' ORDER BY sort, id', (kind,)).fetchall())
        return [{'id': r[0], 'text': r[1], 'active': bool(r[2])} for r in rows]

    def save_reason(self, kind: str, reason_id: str, text: Any, active: bool) -> None:
        if kind not in REASON_KINDS:
            raise ValueError('Неизвестный вид причины')
        if not isinstance(reason_id, str) or not reason_id.strip() or len(reason_id) > 40:
            raise ValueError('Код причины: 1–40 символов')
        text = _clean_name(text, 'Текст причины')

        def write(conn: sqlite3.Connection) -> None:
            sort = conn.execute('SELECT COALESCE(MAX(sort), -1) + 1 FROM reasons WHERE kind = ?', (kind,)).fetchone()[0]
            conn.execute('INSERT INTO reasons(kind, id, text, sort, active) VALUES(?, ?, ?, ?, ?) '
                         'ON CONFLICT(kind, id) DO UPDATE SET text = excluded.text, active = excluded.active',
                         (kind, reason_id.strip(), text, sort, int(active)))

        self._transaction(write, 'не удалось сохранить причину')

    # --- APK ---

    def latest_release(self) -> Release | None:
        r = self._read(lambda c: c.execute(
            'SELECT version_code, version_name, sha256, size, path, uploaded_at FROM app_release '
            'ORDER BY version_code DESC LIMIT 1').fetchone())
        return Release(int(r[0]), r[1], r[2], int(r[3]), r[4], r[5]) if r else None

    def save_release(self, release: Release, user: str | None) -> None:
        def write(conn: sqlite3.Connection) -> None:
            top = conn.execute('SELECT MAX(version_code) FROM app_release').fetchone()[0]
            if top is not None and release.version_code <= int(top):
                raise ValueError(f'version_code должен быть больше {top}')
            conn.execute('INSERT INTO app_release(version_code, version_name, sha256, size, path, uploaded_at, uploaded_by) '
                         'VALUES(?, ?, ?, ?, ?, ?, ?)', (release.version_code, release.version_name, release.sha256,
                                                         release.size, release.path, release.uploaded_at, user))

        self._transaction(write, 'не удалось сохранить APK')


def _day_start(now_iso: str) -> str:
    """Начало дня (Ереван) момента сервера: моменты сервера всегда +04:00 — сравнимы как строки."""
    return now_iso[:10] + 'T00:00:00+04:00'


def _purge_snapshots(conn: sqlite3.Connection) -> int:
    """Хранение снимков /day: удаляются промежуточные снимки старше SNAPSHOT_KEEP_DAYS (по saved_at), на которые
    не ссылается ни одно событие и которые не последние для своей (дата, машина); затем — их точки и содержимое,
    на которое больше никто не ссылается. line_max не трогается (контракт §5 п. 13). Возвращает число удалённых
    снимков."""
    cutoff = clock.iso(clock.now() - timedelta(days=SNAPSHOT_KEEP_DAYS))
    n = conn.execute('DELETE FROM day_snapshots WHERE saved_at < ? '
                     'AND id NOT IN (SELECT MAX(id) FROM day_snapshots GROUP BY date, car_code) '
                     'AND id NOT IN (SELECT snapshot_id FROM events WHERE snapshot_id IS NOT NULL)', (cutoff,)).rowcount
    if n:
        conn.execute('DELETE FROM snapshot_stops WHERE snapshot_id NOT IN (SELECT id FROM day_snapshots)')
        conn.execute('DELETE FROM stop_data WHERE hash NOT IN (SELECT data_hash FROM snapshot_stops)')
    return n


def _stop_json(car_code: str, raw: str, name: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise StoreError(f'{name}: повреждена точка дня{_FIX_HINT}') from e
    if not isinstance(data, dict):
        raise StoreError(f'{name}: повреждена точка дня{_FIX_HINT}')
    data['car_code'] = car_code
    return data


def _stop_customer_sql(path: str) -> str:
    """Подзапрос: поле path клиента точки события e (самая новая версия точки в снимках /day)."""
    return (f"(SELECT json_extract(d.data, '{path}') FROM snapshot_stops s JOIN stop_data d ON d.hash = s.data_hash "
            "WHERE s.stop_id = e.stop_id ORDER BY s.snapshot_id DESC LIMIT 1)")


# открытые предложения водителей: событие geo_suggest без строки в geo_suggest_decision
_SUGGEST_SQL = (f"SELECT e.id, e.date, e.at_device, dr.name, e.payload, {_stop_customer_sql('$.customer')} "
                "FROM events e LEFT JOIN drivers dr ON dr.id = e.driver_id WHERE e.type = 'geo_suggest' "
                "AND NOT EXISTS (SELECT 1 FROM geo_suggest_decision g WHERE g.event_id = e.id)")


def _suggestion(r: Sequence[Any]) -> dict[str, Any] | None:
    """Строка _SUGGEST_SQL → предложение; клиент точки неизвестен или данные битые — None (не показывается)."""
    try:
        payload, customer = json.loads(r[4]), json.loads(r[5]) if r[5] else None
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(customer, dict):
        return None
    cid = customer.get('id')
    if isinstance(cid, bool) or not isinstance(cid, int):
        return None
    return {'event_id': r[0], 'customer_id': cid, 'code': customer.get('code'), 'name': customer.get('name'),
            'lat': payload.get('lat'), 'lon': payload.get('lon'), 'accuracy': payload.get('accuracy'),
            'note': payload.get('note'), 'driver_name': r[3], 'date': r[1], 'at': r[2]}


def _event_json(r: Sequence[Any], name: str) -> dict[str, Any]:
    try:
        payload, flags = json.loads(r[11]), json.loads(r[12])
    except (TypeError, ValueError) as e:
        raise StoreError(f'{name}: повреждено событие {r[0]!r}{_FIX_HINT}') from e
    return {'id': r[0], 'terminal_id': r[1], 'driver_id': r[2], 'driver_name': r[3], 'car_code': r[4],
            'date': r[5], 'stop_id': r[6], 'type': r[7], 'at': r[8], 'at_utc': r[9], 'received_at': r[10],
            'payload': payload if isinstance(payload, dict) else {}, 'flags': flags if isinstance(flags, list) else [],
            'snapshot_id': r[13] if len(r) > 13 else None}


class EventTx:
    """Операции над пачкой событий внутри Store.batch() (одна транзакция). Логика правил — в events.py."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def accepted(self, event_id: str) -> bool:
        return self.conn.execute('SELECT 1 FROM events WHERE id = ?', (event_id,)).fetchone() is not None

    def event(self, event_id: str) -> dict[str, Any] | None:
        r = self.conn.execute('SELECT id, type, stop_id, date, driver_id, payload FROM events WHERE id = ?',
                              (event_id,)).fetchone()
        if r is None:
            return None
        try:
            payload = json.loads(r[5])
        except (TypeError, ValueError):
            payload = {}
        return {'id': r[0], 'type': r[1], 'stop_id': r[2], 'date': r[3], 'driver_id': r[4],
                'payload': payload if isinstance(payload, dict) else {}}

    def insert_event(self, row: Mapping[str, Any]) -> bool:
        """Принятое событие; False — id уже был (гонка двух пачек). Отказ с тем же id удаляется."""
        n = self.conn.execute(
            'INSERT OR IGNORE INTO events(id, terminal_id, driver_id, car_code, date, stop_id, type, at_device, at_utc, '
            'received_at, payload, flags, snapshot_id) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (row['id'], row['terminal_id'], row['driver_id'], row['car_code'], row['date'], row['stop_id'],
             row['type'], row['at_device'], row['at_utc'], row['received_at'],
             json.dumps(row['payload'], ensure_ascii=False, sort_keys=True),
             json.dumps(sorted(set(row['flags'])), ensure_ascii=False), row.get('snapshot_id'))).rowcount
        if n:
            self.conn.execute('DELETE FROM rejected_events WHERE id = ?', (row['id'],))
        return n == 1

    def rejected_today(self, terminal_id: int) -> int:
        """Сохранённых отказов терминала за сегодня (Ереван) — предел REJECTED_PER_DAY."""
        return int(self.conn.execute('SELECT COUNT(*) FROM rejected_events WHERE terminal_id = ? AND received_at >= ?',
                                     (terminal_id, _day_start(_now()))).fetchone()[0])

    def insert_rejected(self, event_id: str, terminal_id: int, driver_id: int, day: str | None, etype: str | None,
                        error: str, message: str, body: str) -> None:
        self.conn.execute(
            'INSERT INTO rejected_events(id, terminal_id, driver_id, date, type, received_at, error, message, body) '
            'VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET received_at = excluded.received_at, '
            'error = excluded.error, message = excluded.message, body = excluded.body',
            (event_id, terminal_id, driver_id, day, etype, _now(), error, message, body[:4000]))

    def stop_rows(self, stop_id: str) -> list[dict[str, Any]]:
        """Все версии точки в выдачах /day: [{snapshot_id, date, car_code, version, data}] по возрастанию снимка
        (version — версия /day снимка, §5 п. 16)."""
        rows = self.conn.execute('SELECT s.snapshot_id, s.date, s.car_code, d.data, x.version FROM snapshot_stops s '
                                 'JOIN stop_data d ON d.hash = s.data_hash '
                                 'LEFT JOIN day_snapshots x ON x.id = s.snapshot_id WHERE s.stop_id = ? '
                                 'ORDER BY s.snapshot_id', (stop_id,)).fetchall()
        out = []
        for sid, d, car, raw, version in rows:
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                data = {}
            out.append({'snapshot_id': int(sid), 'date': d, 'car_code': car, 'version': version,
                        'data': data if isinstance(data, dict) else {}})
        return out

    def payment_cancelled(self, payment_id: str, stop_id: str | None) -> bool:
        """Отмена этого платежа уже принята. Отмена — только той же точки (events._payment), поэтому поиск идёт
        по индексу events_stop (stop_id, type), а не полным перебором событий с json_extract."""
        return self.conn.execute(
            "SELECT 1 FROM events WHERE stop_id = ? AND type = 'payment' AND json_extract(payload, '$.cancel_of') = ?",
            (stop_id, payment_id)).fetchone() is not None

    def scans_with_raw(self, raw: str, kind: str) -> list[tuple[str | None, str, str]]:
        """Действующие (не отменённые) сканы того же кода и вида: (stop_id, date, event_id)."""
        return [(r[0], r[1], r[2]) for r in self.conn.execute(
            'SELECT stop_id, date, event_id FROM scans WHERE raw = ? AND kind = ? AND cancelled = 0',
            (raw, kind)).fetchall()]

    def insert_scan(self, scan: Mapping[str, Any]) -> None:
        cols = ('event_id', 'raw', 'gtin', 'serial', 'is_group', 'units', 'kind', 'stop_id', 'line_id', 'customer_id',
                'customer_code', 'customer_name', 'tax_id', 'doc_number', 'product_id', 'product_code',
                'product_name', 'date', 'at_device', 'driver_id', 'driver_name', 'car_code', 'counted',
                'duplicate_elsewhere', 'cancelled', 'cancel_event_id')
        self.conn.execute(f'INSERT OR IGNORE INTO scans({", ".join(cols)}) VALUES({", ".join("?" * len(cols))})',
                          tuple(scan.get(c) for c in cols))

    def scan_driver(self, scan_event_id: str) -> int | None:
        """Чей скан (водитель); скана нет — None."""
        r = self.conn.execute('SELECT driver_id FROM scans WHERE event_id = ?', (scan_event_id,)).fetchone()
        return int(r[0]) if r else None

    def cancel_scan(self, scan_event_id: str, cancel_event_id: str, driver_id: int) -> bool:
        """Отменить свой скан (водитель — тот же); True — отменён сейчас."""
        return self.conn.execute('UPDATE scans SET cancelled = 1, cancel_event_id = ? WHERE event_id = ? '
                                 'AND driver_id = ? AND cancelled = 0',
                                 (cancel_event_id, scan_event_id, driver_id)).rowcount == 1

    def pending_cancel(self, scan_event_id: str, driver_id: int) -> str | None:
        """Отмена скана тем же водителем, пришедшая раньше самого скана: id события scan_cancel."""
        r = self.conn.execute("SELECT id FROM events WHERE type = 'scan_cancel' AND driver_id = ? "
                              "AND json_extract(payload, '$.scan_event_id') = ? LIMIT 1",
                              (driver_id, scan_event_id)).fetchone()
        return r[0] if r else None

    def line_max(self, stop_id: str) -> dict[str, tuple[float, int | None]]:
        """Строки точки по всем версиям, и удалённым (контракт §5 п. 13): line_id → (наибольшее qty, товар)."""
        return {str(r[0]): (float(r[1]), r[2]) for r in self.conn.execute(
            'SELECT line_id, max_qty, product_id FROM line_max WHERE stop_id = ?', (stop_id,)).fetchall()}

    def covered_by_product(self, day: str, stop_ids: Sequence[str], product_id: int) -> float:
        """Сколько штук товара закрыто на точках за день: засчитанные сканы продажи (не отменённые) этого товара +
        «код не читается» по строкам этого товара (строка → товар — line_max: и строки удалённых версий)."""
        ids = [s for s in stop_ids if isinstance(s, str)]
        if not ids:
            return 0.0
        marks = ','.join('?' * len(ids))
        scanned = self.conn.execute(
            f"SELECT COALESCE(SUM(units), 0) FROM scans WHERE date = ? AND stop_id IN ({marks}) AND product_id = ? "
            "AND kind = 'sale' AND counted = 1 AND cancelled = 0", (day, *ids, product_id)).fetchone()[0]
        unreadable = self.conn.execute(
            "SELECT COALESCE(SUM(json_extract(e.payload, '$.qty')), 0) FROM events e JOIN line_max m "
            "ON m.stop_id = e.stop_id AND m.line_id = json_extract(e.payload, '$.line_id') "
            f"WHERE e.date = ? AND e.type = 'unreadable' AND e.stop_id IN ({marks}) AND m.product_id = ?",
            (day, *ids, product_id)).fetchone()[0]
        return float(scanned or 0) + float(unreadable or 0)

    def driver_name(self, driver_id: int) -> str | None:
        r = self.conn.execute('SELECT name FROM drivers WHERE id = ?', (driver_id,)).fetchone()
        return r[0] if r else None

    # --- трек и заправки (контракт v1.3 §7) ---

    def insert_track(self, car_code: str, day: str,
                     points: Sequence[tuple[int, float, float, float, float | None, float | None]]) -> int:
        """Точки трека (at_ms, lat, lon, acc, spd, brg) машины за рабочий день day. Точка с тем же at_ms этой машины уже
        есть (из этого или другого события, любой даты) — не пишется. Возвращает число новых точек."""
        before = self.conn.total_changes
        self.conn.executemany('INSERT OR IGNORE INTO track_points(car_code, at_ms, date, lat, lon, acc, spd, brg) '
                              'VALUES(?, ?, ?, ?, ?, ?, ?, ?)', [(car_code, p[0], day, *p[1:]) for p in points])
        return self.conn.total_changes - before

    def purge_track(self, today: str) -> int:
        """Хранение трека — TRACK_KEEP_DAYS (контракт §7 п. 1): точки любой машины с моментом раньше полуночи (Ереван)
        дня today − TRACK_KEEP_DAYS (по моменту точки, не по дате события) и события track рабочих дней раньше него
        удаляются. Не чаще раза в день (meta track_purged_on): вызывается при приёме трека. Возвращает число удалённых
        точек."""
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'track_purged_on'").fetchone()
        if row is not None and row[0] == today:
            return 0
        cutoff = (datetime.fromisoformat(today) - timedelta(days=TRACK_KEEP_DAYS)).date()
        cutoff_ms = int(datetime.combine(cutoff, datetime.min.time(), clock.YEREVAN).timestamp() * 1000)
        n = 0
        car = self.conn.execute('SELECT MIN(car_code) FROM track_points').fetchone()[0]
        while car is not None:   # машины — из самих точек (по ключу, без перебора): и тех, чьих терминалов уже нет
            n += self.conn.execute('DELETE FROM track_points WHERE car_code = ? AND at_ms < ?', (car, cutoff_ms)).rowcount
            car = self.conn.execute('SELECT MIN(car_code) FROM track_points WHERE car_code > ?', (car,)).fetchone()[0]
        # только события трека (в них лишь счётчики точек); доставки, оплаты и прочее не удаляются
        self.conn.execute("DELETE FROM events WHERE type = 'track' AND date < ?", (cutoff.isoformat(),))
        self.conn.execute("INSERT INTO meta(key, value) VALUES('track_purged_on', ?) "
                          'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (today,))
        return n

    def car_refuels(self, car_code: str) -> list[dict[str, Any]]:
        """Принятые заправки машины (и этой пачки): [{id, at_utc, payload, flags}] по моменту, затем id."""
        out = []
        for eid, at_utc, raw, flags in self.conn.execute(
                "SELECT id, at_utc, payload, flags FROM events WHERE car_code = ? AND type = 'refuel' "
                'ORDER BY at_utc, id', (car_code,)).fetchall():
            try:
                payload, fl = json.loads(raw), json.loads(flags)
            except (TypeError, ValueError):
                continue
            out.append({'id': eid, 'at_utc': at_utc, 'payload': payload if isinstance(payload, dict) else {},
                        'flags': fl if isinstance(fl, list) else []})
        return out

# -*- coding: utf-8 -*-
"""Хранилище раздела «Առաքիչ» — SQLite (courier.db), отдельно от route_optimizer.db.

ERP только читаем, поэтому всё состояние терминалов живёт здесь: водители, терминалы, сессии,
события, сканы, фото, сдача денег, настройки маркировки, причины, тара, APK.
Дисциплина — как у route_optimizer.store:
- соединение на операцию, WAL, busy_timeout;
- схема создаётся при первом обращении, версия — meta.schema_version; база старой версии
  мигрирует одной транзакцией (_MIGRATIONS);
- запись — одна транзакция: всё или ничего;
- битая БД — явная StoreError, а НЕ тихий откат на дефолты.
Все моменты сервера пишутся clock.iso() — всегда со смещением +04:00, поэтому строки сравнимы
лексикографически. Момент терминала (at) может прийти с любой зоной — для порядка есть at_utc.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from . import clock
from .security import check_pin, hash_pin, new_token, same_hash, token_hash, valid_pin

SCHEMA_VERSION = 1

PIN_MAX_FAILS = 5
PIN_LOCK = timedelta(minutes=15)
NAME_MAX = 60
TEXT_MAX = 200

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

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS drivers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
    "pin_hash TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)), "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT)",
    # failed_pin_count / locked_until — блокировка входа по терминалу (за туннелем у всех один IP)
    "CREATE TABLE IF NOT EXISTS terminals(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
    "car_code TEXT NOT NULL, token_sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, created_by TEXT, "
    "revoked_at TEXT, revoked_by TEXT, failed_pin_count INTEGER NOT NULL DEFAULT 0, pin_window_start TEXT, "
    "locked_until TEXT, last_seen_at TEXT)",
    # одна действующая сессия на терминал (новая отменяет старую — open_session)
    "CREATE TABLE IF NOT EXISTS sessions(token_sha256 TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, "
    "driver_id INTEGER NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS sessions_terminal ON sessions(terminal_id)",
    # события терминала: id — uuid4 от терминала (идемпотентность); только принятые
    "CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, "
    "driver_id INTEGER NOT NULL, car_code TEXT NOT NULL, date TEXT NOT NULL, stop_id TEXT, type TEXT NOT NULL, "
    "at_device TEXT NOT NULL, at_utc TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL, "
    "flags TEXT NOT NULL DEFAULT '[]')",
    "CREATE INDEX IF NOT EXISTS events_day ON events(date, car_code)",
    "CREATE INDEX IF NOT EXISTS events_stop ON events(stop_id, type)",
    "CREATE INDEX IF NOT EXISTS events_driver ON events(driver_id, date)",
    # отклонённые события — для «Конца дня» (/status) и офиса; принятое позже с тем же id — удаляется отсюда
    "CREATE TABLE IF NOT EXISTS rejected_events(id TEXT PRIMARY KEY, terminal_id INTEGER NOT NULL, "
    "driver_id INTEGER NOT NULL, date TEXT, type TEXT, received_at TEXT NOT NULL, error TEXT NOT NULL, "
    "message TEXT NOT NULL, body TEXT)",
    "CREATE INDEX IF NOT EXISTS rejected_driver ON rejected_events(driver_id, date)",
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
    # точки дня, как их получил терминал (/day): проверка событий (чужая точка, qty по накладной) и офис
    "CREATE TABLE IF NOT EXISTS day_stops(date TEXT NOT NULL, stop_id TEXT NOT NULL, car_code TEXT NOT NULL, "
    "seq INTEGER NOT NULL, data TEXT NOT NULL, version TEXT NOT NULL, loaded_at TEXT NOT NULL, "
    "PRIMARY KEY (date, stop_id))",
    "CREATE INDEX IF NOT EXISTS day_stops_stop ON day_stops(stop_id)",
    "CREATE INDEX IF NOT EXISTS day_stops_car ON day_stops(date, car_code)",
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
)

# Миграции: версия → DDL перехода на следующую (выполняются одной транзакцией с записью версии).
_MIGRATIONS: dict[int, tuple[str, ...]] = {}

_FIX_HINT = ' — исправьте или удалите файл; значения по умолчанию молча не подставляются'


class StoreError(RuntimeError):
    """courier.db недоступна или повреждена. Текст — для лога и офиса."""


class PinConflict(ValueError):
    """Такой PIN уже у другого активного водителя: PIN определяет водителя при входе."""


@dataclass(frozen=True)
class Driver:
    id: int
    name: str
    active: bool
    has_pin: bool
    updated_at: str | None = None


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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            conn.execute('PRAGMA busy_timeout = 5000')
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
            for ddl in _SCHEMA:
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
        rows = self._read(lambda c: c.execute(
            'SELECT id, name, active, pin_hash IS NOT NULL, updated_at FROM drivers ORDER BY active DESC, name, id'
        ).fetchall())
        return [Driver(int(r[0]), r[1], bool(r[2]), bool(r[3]), r[4]) for r in rows]

    def driver(self, driver_id: int) -> Driver | None:
        r = self._read(lambda c: c.execute(
            'SELECT id, name, active, pin_hash IS NOT NULL, updated_at FROM drivers WHERE id = ?',
            (driver_id,)).fetchone())
        return Driver(int(r[0]), r[1], bool(r[2]), bool(r[3]), r[4]) if r else None

    def save_driver(self, driver_id: int | None, name: Any, active: bool, pin: str | None,
                    user: str | None) -> int:
        """Создать (driver_id None) или изменить водителя. pin None — не менять PIN.
        PIN определяет водителя при входе, поэтому у активных водителей он уникален (PinConflict)."""
        name = _clean_name(name, 'Имя водителя')
        if pin is not None and not valid_pin(pin):
            raise ValueError('PIN — 4–6 цифр')
        pin_hash = hash_pin(pin) if pin is not None else None
        now = _now()

        def write(conn: sqlite3.Connection) -> int:
            if pin is not None and active:
                for oid, h in conn.execute('SELECT id, pin_hash FROM drivers WHERE active = 1 AND pin_hash IS NOT NULL'):
                    if oid != driver_id and check_pin(h, pin):
                        raise PinConflict('Такой PIN уже у другого водителя')
            if driver_id is None:
                cur = conn.execute('INSERT INTO drivers(name, pin_hash, active, created_at, updated_at, updated_by) '
                                   'VALUES(?, ?, ?, ?, ?, ?)', (name, pin_hash, int(active), now, now, user))
                return int(cur.lastrowid)
            sets, args = 'name = ?, active = ?, updated_at = ?, updated_by = ?', [name, int(active), now, user]
            if pin_hash is not None:
                sets += ', pin_hash = ?'
                args.append(pin_hash)
            if conn.execute(f'UPDATE drivers SET {sets} WHERE id = ?', (*args, driver_id)).rowcount != 1:
                raise LookupError('Водитель не найден')
            if not active:   # выключенный водитель сразу теряет сессии
                conn.execute('DELETE FROM sessions WHERE driver_id = ?', (driver_id,))
            return driver_id

        return self._transaction(write, 'не удалось сохранить водителя')

    def match_pin(self, pin: str) -> list[Driver]:
        """Активные водители с этим PIN (ожидается один; несколько — конфликт старых данных)."""
        if not valid_pin(pin):
            return []
        rows = self._read(lambda c: c.execute(
            'SELECT id, name, pin_hash, updated_at FROM drivers WHERE active = 1 AND pin_hash IS NOT NULL ORDER BY id'
        ).fetchall())
        return [Driver(int(r[0]), r[1], True, True, r[3]) for r in rows if check_pin(r[2], pin)]

    # --- терминалы ---

    def create_terminal(self, name: Any, car_code: Any, user: str | None) -> tuple[Terminal, str]:
        """Новый терминал; токен возвращается ОДИН раз (в базе — только sha256)."""
        name = _clean_name(name, 'Имя терминала')
        if not isinstance(car_code, str) or not car_code.strip() or len(car_code.strip()) > 20:
            raise ValueError('Машина не выбрана')
        token = new_token()
        now = _now()

        def write(conn: sqlite3.Connection) -> int:
            cur = conn.execute('INSERT INTO terminals(name, car_code, token_sha256, created_at, created_by) '
                               'VALUES(?, ?, ?, ?, ?)', (name, car_code.strip(), token_hash(token), now, user))
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

    def touch_terminal(self, terminal_id: int) -> None:
        """Последняя связь терминала (для «Առաքում այսօր»)."""
        now = _now()
        self._transaction(lambda c: c.execute('UPDATE terminals SET last_seen_at = ? WHERE id = ?', (now, terminal_id)),
                          'не удалось отметить связь терминала')

    # --- вход по PIN ---

    def pin_failed(self, terminal_id: int) -> str | None:
        """Неверный PIN на терминале. Ошибки считаются в окне PIN_LOCK от первой ошибки окна, и успешный вход
        окно НЕ сбрасывает: иначе водитель со своим PIN перебирал бы чужой (4 попытки, вход, снова 4…).
        Возвращает locked_until, если эта ошибка включила блокировку (PIN_MAX_FAILS в окне → PIN_LOCK);
        окно при этом обнуляется — после блокировки снова 5 попыток."""
        now = clock.now()
        now_s, window_from = clock.iso(now), clock.iso(now - PIN_LOCK)

        def write(conn: sqlite3.Connection) -> str | None:
            conn.execute('UPDATE terminals SET failed_pin_count = CASE WHEN pin_window_start IS NULL '
                         'OR pin_window_start <= ? THEN 1 ELSE failed_pin_count + 1 END, '
                         'pin_window_start = CASE WHEN pin_window_start IS NULL OR pin_window_start <= ? '
                         'THEN ? ELSE pin_window_start END WHERE id = ?',
                         (window_from, window_from, now_s, terminal_id))
            row = conn.execute('SELECT failed_pin_count FROM terminals WHERE id = ?', (terminal_id,)).fetchone()
            if row is None or int(row[0]) < PIN_MAX_FAILS:
                return None
            until = clock.iso(now + PIN_LOCK)
            conn.execute('UPDATE terminals SET failed_pin_count = 0, pin_window_start = NULL, locked_until = ? '
                         'WHERE id = ?', (until, terminal_id))
            return until

        return self._transaction(write, 'не удалось записать попытку входа')

    def open_session(self, terminal_id: int, driver_id: int, expires_at: datetime) -> str:
        """Новая сессия водителя на терминале: прежние сессии терминала отменяются, просроченные сессии всех
        терминалов удаляются. Счётчик ошибок PIN НЕ сбрасывается (см. pin_failed). Токен — один раз."""
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
                 loaded_at: str) -> None:
        """Точки машины на дату — заменить набор: исчезнувшие из ERP удаляются (события по ним остаются).
        Та же версия — без записи."""
        def write(conn: sqlite3.Connection) -> None:
            row = conn.execute('SELECT version FROM day_stops WHERE date = ? AND car_code = ? LIMIT 1',
                               (day, car_code)).fetchone()
            count = conn.execute('SELECT COUNT(*) FROM day_stops WHERE date = ? AND car_code = ?',
                                 (day, car_code)).fetchone()[0]
            if row is not None and row[0] == version and count == len(stops):
                return
            conn.execute('DELETE FROM day_stops WHERE date = ? AND car_code = ?', (day, car_code))
            for s in stops:
                conn.execute('INSERT INTO day_stops(date, stop_id, car_code, seq, data, version, loaded_at) '
                             'VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(date, stop_id) DO UPDATE SET '
                             'car_code = excluded.car_code, seq = excluded.seq, data = excluded.data, '
                             'version = excluded.version, loaded_at = excluded.loaded_at',
                             (day, s['stop_id'], car_code, int(s['seq']),
                              json.dumps(s, ensure_ascii=False, sort_keys=True), version, loaded_at))

        self._transaction(write, 'не удалось сохранить точки дня')

    def day_stops(self, day: str, car_code: str | None = None) -> list[dict[str, Any]]:
        """Точки на дату (одной машины или всех): данные стопа + car_code, по машине и seq."""
        if car_code is None:
            rows = self._read(lambda c: c.execute(
                'SELECT car_code, data FROM day_stops WHERE date = ? ORDER BY car_code, seq', (day,)).fetchall())
        else:
            rows = self._read(lambda c: c.execute(
                'SELECT car_code, data FROM day_stops WHERE date = ? AND car_code = ? ORDER BY seq',
                (day, car_code)).fetchall())
        return [_stop_json(r[0], r[1], self._name()) for r in rows]

    # --- события (чтение для офиса и /status) ---

    def events_for_day(self, day: str) -> list[dict[str, Any]]:
        rows = self._read(lambda c: c.execute(
            'SELECT e.id, e.terminal_id, e.driver_id, d.name, e.car_code, e.date, e.stop_id, e.type, e.at_device, '
            'e.at_utc, e.received_at, e.payload, e.flags FROM events e LEFT JOIN drivers d ON d.id = e.driver_id '
            'WHERE e.date = ? ORDER BY e.at_utc, e.received_at, e.id', (day,)).fetchall())
        return [_event_json(r, self._name()) for r in rows]

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
        """Фото идемпотентно по id: True — записано, False — такое id уже было."""
        now = _now()
        return self._transaction(lambda c: c.execute(
            'INSERT OR IGNORE INTO photos(id, event_id, kind, path, size, sha256, terminal_id, received_at) '
            'VALUES(?, ?, ?, ?, ?, ?, ?, ?)', (photo_id, event_id, kind, path, size, sha256, terminal_id, now)
        ).rowcount == 1, 'не удалось сохранить фото')

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


def _stop_json(car_code: str, raw: str, name: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise StoreError(f'{name}: повреждена точка дня{_FIX_HINT}') from e
    if not isinstance(data, dict):
        raise StoreError(f'{name}: повреждена точка дня{_FIX_HINT}')
    data['car_code'] = car_code
    return data


def _event_json(r: Sequence[Any], name: str) -> dict[str, Any]:
    try:
        payload, flags = json.loads(r[11]), json.loads(r[12])
    except (TypeError, ValueError) as e:
        raise StoreError(f'{name}: повреждено событие {r[0]!r}{_FIX_HINT}') from e
    return {'id': r[0], 'terminal_id': r[1], 'driver_id': r[2], 'driver_name': r[3], 'car_code': r[4],
            'date': r[5], 'stop_id': r[6], 'type': r[7], 'at': r[8], 'at_utc': r[9], 'received_at': r[10],
            'payload': payload if isinstance(payload, dict) else {}, 'flags': flags if isinstance(flags, list) else []}


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
            'received_at, payload, flags) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (row['id'], row['terminal_id'], row['driver_id'], row['car_code'], row['date'], row['stop_id'],
             row['type'], row['at_device'], row['at_utc'], row['received_at'],
             json.dumps(row['payload'], ensure_ascii=False, sort_keys=True),
             json.dumps(sorted(set(row['flags'])), ensure_ascii=False))).rowcount
        if n:
            self.conn.execute('DELETE FROM rejected_events WHERE id = ?', (row['id'],))
        return n == 1

    def insert_rejected(self, event_id: str, terminal_id: int, driver_id: int, day: str | None, etype: str | None,
                        error: str, message: str, body: str) -> None:
        self.conn.execute(
            'INSERT INTO rejected_events(id, terminal_id, driver_id, date, type, received_at, error, message, body) '
            'VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET received_at = excluded.received_at, '
            'error = excluded.error, message = excluded.message, body = excluded.body',
            (event_id, terminal_id, driver_id, day, etype, _now(), error, message, body[:4000]))

    def stop_rows(self, stop_id: str) -> list[dict[str, Any]]:
        """Где эта точка была в выдаче /day: [{date, car_code, data}]."""
        rows = self.conn.execute('SELECT date, car_code, data FROM day_stops WHERE stop_id = ?', (stop_id,)).fetchall()
        out = []
        for d, car, raw in rows:
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                data = {}
            out.append({'date': d, 'car_code': car, 'data': data if isinstance(data, dict) else {}})
        return out

    def payment_cancelled(self, payment_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM events WHERE type = 'payment' AND json_extract(payload, '$.cancel_of') = ?",
            (payment_id,)).fetchone() is not None

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

    def covered_units(self, day: str, stop_id: str, line_id: str) -> float:
        """Сколько штук строки закрыто: засчитанные сканы продажи + «код не читается»."""
        scanned = self.conn.execute(
            "SELECT COALESCE(SUM(units), 0) FROM scans WHERE date = ? AND stop_id = ? AND line_id = ? "
            "AND kind = 'sale' AND counted = 1 AND cancelled = 0", (day, stop_id, line_id)).fetchone()[0]
        unreadable = self.conn.execute(
            "SELECT COALESCE(SUM(json_extract(payload, '$.qty')), 0) FROM events WHERE date = ? AND stop_id = ? "
            "AND type = 'unreadable' AND json_extract(payload, '$.line_id') = ?", (day, stop_id, line_id)).fetchone()[0]
        return float(scanned or 0) + float(unreadable or 0)

    def driver_name(self, driver_id: int) -> str | None:
        r = self.conn.execute('SELECT name FROM drivers WHERE id = ?', (driver_id,)).fetchone()
        return r[0] if r else None

"""Долговечный замок опубликованного плана; все проверки выполняет SQLite в транзакции записи."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


class PlanLocked(RuntimeError):
    def __init__(self):
        super().__init__('Այս պլանով ERP գրանցում կա․ նախ ստուգեք գրանցման արդյունքը։ Պլանն ու անձնակազմը ժամանակավորապես չեն փոխվում։')


TABLE = '''CREATE TABLE IF NOT EXISTS erp_chain_plan_lease(
    day TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE,
    cars TEXT NOT NULL, people TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)'''


def _trigger(table: str, action: str, condition: str, changed: str = '1') -> str:
    return (f'CREATE TRIGGER IF NOT EXISTS erp_chain_guard_{table}_{action.lower()} '
            f'AFTER {action} ON {table} WHEN ({changed}) AND EXISTS('
            f'SELECT 1 FROM erp_chain_plan_lease AS lease WHERE {condition}) '
            "BEGIN SELECT RAISE(ABORT, 'erp_chain_plan_locked'); END")


def _schema() -> tuple[str, ...]:
    statements = [TABLE]
    for table in ('dispatch_plan', 'truck_driver', 'truck_helper', 'driver_absence', 'trucks'):
        for action in ('INSERT', 'UPDATE', 'DELETE'):
            refs = ('OLD', 'NEW') if action == 'UPDATE' else (('OLD',) if action == 'DELETE' else ('NEW',))
            conditions = []
            for ref in refs:
                if table == 'dispatch_plan':
                    condition = f'lease.day={ref}.day'
                    if action == 'DELETE':  # забытый замок не должен мешать очистке старых планов при каждом сохранении
                        condition += " AND OLD.day >= date('now','+4 hours','-100 days')"
                elif table in ('truck_driver', 'truck_helper'):
                    condition = (f'{ref}.from_day<=lease.day AND ({ref}.only_day=0 OR {ref}.from_day=lease.day) AND '
                                 f'({ref}.car_code IN (SELECT value FROM json_each(lease.cars)) OR '
                                 f'{ref}.name IN (SELECT value FROM json_each(lease.people)))')
                elif table == 'driver_absence':
                    condition = (f'lease.day BETWEEN {ref}.from_day AND {ref}.to_day AND '
                                 f'{ref}.name IN (SELECT value FROM json_each(lease.people))')
                else:
                    # Прошедший день не держит машину: забытый замок не должен навсегда запрещать правку парка.
                    condition = (f"{ref}.car_code IN (SELECT value FROM json_each(lease.cars)) "
                                 "AND lease.day >= date('now','+4 hours')")
                conditions.append(f'({condition})')
            # Обновление кэша экипажа с тем же именем не меняет назначение.
            changed = '1'
            if action == 'UPDATE' and table in ('truck_driver', 'truck_helper'):
                changed = ' OR '.join(f'OLD.{key} IS NOT NEW.{key}' for key in ('car_code', 'from_day', 'only_day', 'name'))
            # Тела триггеров менялись: IF NOT EXISTS сам по себе оставил бы старые.
            statements.append(f'DROP TRIGGER IF EXISTS erp_chain_guard_{table}_{action.lower()}')
            statements.append(_trigger(table, action, ' OR '.join(conditions), changed))
    return tuple(statements)


SCHEMA = _schema()


def acquire(store: Any, plan: dict[str, Any], token: str, *, keep_existing: bool = False) -> bool:
    """Номер плана и замок проверяются одной транзакцией. Таймера снятия замка нет."""
    def write(conn: sqlite3.Connection) -> bool:
        # База уже на текущей схеме не проходит миграцию: актуальные триггеры ставятся здесь, в этой же транзакции.
        for statement in SCHEMA:
            conn.execute(statement)
        day = plan['day']
        row = conn.execute('SELECT data,rev FROM dispatch_plan WHERE day=?', (day,)).fetchone()
        if row is None or row[1] != plan['revision']:
            return False
        held = conn.execute('SELECT operation_id FROM erp_chain_plan_lease WHERE day=?', (day,)).fetchone()
        if held is not None:
            if keep_existing and held[0] == token:
                return True
            raise PlanLocked()
        cars = sorted({load['car'] for load in plan['loads']})
        sent_crew = (json.loads(row[0]).get('sent') or {}).get('crew') or {}
        people = sorted({sent_crew[car] for car in cars if sent_crew.get(car)})
        conn.execute('INSERT INTO erp_chain_plan_lease(day,operation_id,cars,people) VALUES(?,?,?,?)',
                     (day, token, json.dumps(cars), json.dumps(people)))
        return True
    return store._transaction(write, 'не удалось защитить план на время ERP-регистрации')


def release(store: Any, token: str) -> None:
    """Только подтверждённое завершение или доказанный отказ до native-записи."""
    def write(conn: sqlite3.Connection) -> None:
        # Таблица появляется при первом acquire: до него снимать нечего.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_chain_plan_lease'").fetchone() is not None:
            conn.execute('DELETE FROM erp_chain_plan_lease WHERE operation_id=?', (token,))
    store._transaction(write, 'не удалось снять замок завершённой ERP-регистрации')


def held_operations(store: Any) -> list[str]:
    """Операции, всё ещё держащие замок; таблицы может ещё не быть, чтение её не создаёт."""
    def read(conn: sqlite3.Connection) -> list[str]:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_chain_plan_lease'").fetchone() is None:
            return []
        return [row[0] for row in conn.execute('SELECT operation_id FROM erp_chain_plan_lease')]
    return store._read(read)


def lease_age_seconds(store: Any, token: str) -> float | None:
    """Сколько секунд держится замок операции; None — замка нет."""
    def read(conn: sqlite3.Connection) -> float | None:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_chain_plan_lease'").fetchone() is None:
            return None
        row = conn.execute("SELECT (julianday('now')-julianday(created_at))*86400 FROM erp_chain_plan_lease WHERE operation_id=?",
                           (token,)).fetchone()
        return None if row is None else float(row[0])
    return store._read(read)

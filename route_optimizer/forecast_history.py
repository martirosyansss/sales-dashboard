"""Неизменяемые версии прогнозов для честной проверки «план — факт».

Запись вызывается внутри транзакции сохранения развоза после проверки ревизии.
Это добавочная таблица собственной SQLite, совместимая с прежней версией схемы.
ERP здесь не используется. Время наличия прогноза всегда сохраняется в UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Mapping


RETENTION_DAYS = 180
TABLE = """CREATE TABLE IF NOT EXISTS dispatch_forecast_history(
    day TEXT NOT NULL, rev INTEGER NOT NULL, captured_at_utc TEXT NOT NULL,
    prediction_created_at TEXT, published INTEGER NOT NULL, digest TEXT NOT NULL,
    data TEXT NOT NULL, PRIMARY KEY(day,rev))"""


def comparable(captured_at: datetime, eta: datetime, actual: datetime) -> bool:
    """Прогноз существовал до события и не предсказывал уже прошедший момент."""
    if any(value.tzinfo is None for value in (captured_at, eta, actual)):
        return False
    return captured_at <= eta and captured_at <= actual


def append(conn, day: str, rev: int, data: Mapping[str, Any], captured_at: datetime | None = None) -> None:
    prediction = data.get('prediction')
    if not isinstance(prediction, Mapping) or not isinstance(prediction.get('created_at'), str):
        return
    if not isinstance(prediction.get('trucks'), Mapping):
        return
    now = captured_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('captured_at must include a time zone')
    now = now.astimezone(timezone.utc)
    sent = data.get('sent') or {}
    published = isinstance(sent, Mapping) and isinstance(sent.get('plan'), Mapping) and \
        sent['plan'].get('prediction') == prediction
    payload = {'prediction': prediction, 'trips': data.get('trips', []),
               'trucks': data.get('trucks', []), 'locks': data.get('locks', {}),
               'approved': bool(data.get('approved') or data.get('held_approval')), 'sent_at': sent.get('at') if published else None}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    conn.execute(TABLE)
    conn.execute('INSERT OR IGNORE INTO dispatch_forecast_history '
                 '(day,rev,captured_at_utc,prediction_created_at,published,digest,data) VALUES(?,?,?,?,?,?,?)',
                 (day, rev, now.isoformat(), prediction['created_at'], int(published),
                  hashlib.sha256(raw.encode()).hexdigest(), raw))
    # По моменту записи, а не дню плана: просмотр далёкой будущей даты не
    # должен убирать историю текущей работы.
    conn.execute('DELETE FROM dispatch_forecast_history WHERE captured_at_utc < ?',
                 ((now - timedelta(days=RETENTION_DAYS)).isoformat(),))


def read(conn, day: str, before: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dispatch_forecast_history'").fetchone():
        return []
    limit = max(1, min(1000, int(limit)))
    sql = 'SELECT rev,captured_at_utc,published,digest,data FROM dispatch_forecast_history WHERE day=?'
    args: list[Any] = [day]
    if before is not None:
        if before.tzinfo is None:
            raise ValueError('before must include a time zone')
        sql += ' AND captured_at_utc <= ?'
        args.append(before.astimezone(timezone.utc).isoformat())
    sql += ' ORDER BY captured_at_utc DESC,rev DESC LIMIT ?'
    args.append(limit)
    return [{'rev': rev, 'captured_at_utc': at, 'published': bool(published), 'digest': digest,
             **json.loads(raw)} for rev, at, published, digest, raw in conn.execute(sql, args)]

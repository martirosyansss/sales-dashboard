"""Безопасное использование прежнего состава рейсов без возврата старых правил и статусов."""
from __future__ import annotations

from collections import defaultdict
import json


def read(conn, day, max_rev, limit=1000):
    """Только чтение собственной истории; повреждённая версия не мешает новой сборке."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dispatch_forecast_history'").fetchone():
        return []
    rows = conn.execute('SELECT rev,data FROM dispatch_forecast_history WHERE day=? AND rev<=? '
                        'ORDER BY rev DESC LIMIT ?', (day, max_rev, max(1, min(1000, int(limit))))).fetchall()
    out = []
    for rev, raw in rows:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get('trips'), list):
            out.append({'rev': rev, 'trips': payload['trips']})
    return out


def route_key(trips):
    """Номера рейсов и утверждение не отличают одинаковые варианты объезда."""
    by = defaultdict(list)
    for trip in trips:
        by[trip.truck].append(tuple(trip.stops))
    return tuple((code, tuple(rows)) for code, rows in sorted(by.items()))


def restore(record, current):
    """Берём только состав и порядок. Текущие закрепления/погрузка остаются источником истины."""
    from . import dispatch as dp
    raw = record.get('trips')
    if not isinstance(raw, list) or not raw or len(raw) > dp.MAX_TRIPS:
        return None
    parsed = dp.Draft.from_json({'trips': raw, 'trucks': current.trucks})
    if len(parsed.trips) != len(raw) or any(not t.stops for t in parsed.trips):
        return None
    protected = [t for t in current.trips if t.pinned or t.loaded is not None or t.not_before is not None]
    remaining = list(protected)
    next_id = current.next_id
    trips = []
    for archived in parsed.trips:
        match = next((t for t in remaining if t.truck == archived.truck and t.stops == archived.stops), None)
        if match is not None:
            trips.append(match)
            remaining.remove(match)
        else:
            # Старую погрузку, время взятия заказа и положение машины не восстанавливаем.
            if archived.loaded is not None or archived.not_before is not None or archived.origin is not None:
                return None
            trips.append(dp.DraftTrip(next_id, archived.truck, list(archived.stops)))
            next_id += 1
    return None if remaining else trips

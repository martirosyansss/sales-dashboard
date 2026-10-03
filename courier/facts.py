# -*- coding: utf-8 -*-
"""Факт развоза по данным терминала (контракт v1.3 §7): трек машины, точки дня, доставки, заправки.

- gps_summary — сводка трека машины за день для офиса «Առաքում այսօր» (км по треку — тем же фильтром, что у GPS
  менеджеров: route_optimizer.geo.track_km);
- FactsSource — источник факта для обучения «Развоза» (протокол route_optimizer.learning.FleetFacts). Подключает app_v2
  (route_optimizer.attach_fleet_facts): пакет route_optimizer не импортирует courier. Нет courier.db — пусто, файл не
  создаётся.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from route_optimizer.geo import Fix, track_km

from . import clock, merge as mg
from .store import Store


def _fixes(points: Sequence[Sequence[Any]]) -> list[Fix]:
    """(at_ms, lat, lon, acc, …) → Fix (момент — Ереван)."""
    return [Fix(datetime.fromtimestamp(p[0] / 1000.0, timezone.utc).astimezone(clock.YEREVAN), p[1], p[2], p[3])
            for p in points]


def gps_summary(points: Sequence[Sequence[Any]]) -> dict[str, Any] | None:
    """Трек машины за день → {points, km, first, last} (моменты — ISO Еревана); точек нет — None."""
    if not points:
        return None
    fixes = _fixes(points)
    return {'points': len(points), 'km': round(track_km(fixes), 1), 'first': clock.iso(fixes[0].at),
            'last': clock.iso(fixes[-1].at)}


def delivered_share(stop: Mapping[str, Any], delivery: Mapping[str, Any] | None) -> float | None:
    """Доля доставленного по точке: Σ qty действующей доставки / Σ qty строк точки (0…1). Доставки нет или строк нет —
    None (неизвестно: такая точка не идёт в обучение разгрузки)."""
    if delivery is None:
        return None
    total = sum(float(ln.get('qty') or 0) for ln in stop.get('lines') or () if isinstance(ln, dict)
                and isinstance(ln.get('qty'), (int, float)) and not isinstance(ln.get('qty'), bool))
    if total <= 0:
        return None
    done = sum(float(i.get('qty') or 0) for i in (delivery.get('payload') or {}).get('lines') or ()
               if isinstance(i, dict) and isinstance(i.get('qty'), (int, float)) and not isinstance(i.get('qty'), bool))
    return max(0.0, min(1.0, done / total))


class FactsSource:
    """Факт машин для «Маршрутов» (route_optimizer.learning.FleetFacts)."""

    def __init__(self, store: Store):
        self.store = store

    def _exists(self) -> bool:
        return os.path.exists(self.store.path)   # нет базы — не создаём её чтением

    def car_days(self, since: str, until: str) -> list[tuple[str, str]]:
        """(машина, день) с треком за since…until."""
        if not self._exists():
            return []
        return [(car, day) for day, car in self.store.track_days(since, until)]

    def day(self, car_code: str, day: str) -> dict[str, Any]:
        """Трек и точки машины за день: track — [(at_ms, lat, lon, acc, spd)]; stops — точки последнего снимка /day
        машины на эту дату: stop_id, customer_id, lat, lon, weight_kg, seq, delivered_share (доля доставленного по
        действующей доставке точки, правило §5 п. 12/14; доставки нет — None)."""
        if not self._exists():
            return {'track': [], 'stops': []}
        track = [tuple(p[:5]) for p in self.store.track(car_code, day)]
        stops = self.store.day_stops(day, car_code)
        deliveries = mg.latest_by_stop(self.store.events_for_day(day, 'delivery'), 'delivery')
        out = []
        for s in stops:
            cust = s.get('customer') or {}
            cid = cust.get('id')
            out.append({'stop_id': s.get('stop_id'), 'customer_id': cid if isinstance(cid, int) and not
                        isinstance(cid, bool) else None, 'lat': s.get('lat'), 'lon': s.get('lon'),
                        'weight_kg': s.get('weight_kg'), 'seq': s.get('seq'),
                        'delivered_share': delivered_share(s, deliveries.get(s.get('stop_id')))})
        return {'track': track, 'stops': out}

    def refuels(self) -> list[dict[str, Any]]:
        """Все заправки (Store.refuels): с флагами и признаком superseded."""
        return self.store.refuels() if self._exists() else []

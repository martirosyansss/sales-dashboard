# -*- coding: utf-8 -*-
"""Факт развоза по данным терминала (контракт v1.3 §7): трек машины, точки дня, доставки, заправки.

- gps_summary — сводка трека машины за день для офиса «Առաքում այսօր»: км движения — тем же расчётом, что в отчёте
  «план — факт» (route_optimizer.actuals.reconstruct: стоянки у точек дня и склада, стоянка по скорости терминала —
  0 км, дрожание GPS на месте км не добавляет);
- refuel_flags — флаг odometer_suspicious заправок, пересчитанный по всем действующим заправкам машины (сохранённый
  при приёме — не источник истины: исправления и опоздавшие события меняют вывод);
- FactsSource — источник факта для обучения «Развоза» (протокол route_optimizer.learning.FleetFacts). Подключает app_v2
  (route_optimizer.attach_fleet_facts): пакет route_optimizer не импортирует courier. Нет courier.db — пусто, файл не
  создаётся.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Mapping, Sequence

from route_optimizer import actuals as ac
from route_optimizer.geo import Point
from route_optimizer.learning import effective_refuels, odometer_plausible, track_fixes

from . import clock, merge as mg
from .store import Store

SUSPICIOUS = 'odometer_suspicious'


def gps_summary(points: Sequence[Sequence[Any]], stops: Sequence[Mapping[str, Any]] = (),
                depot: Point | None = None) -> dict[str, Any] | None:
    """Трек машины за день → {points, km, first, last} (моменты — ISO Еревана); точек нет — None. stops — точки /day
    машины (stop_id, lat, lon), depot — склад: их стоянки дают 0 км."""
    if not points:
        return None
    plan = [ac.PlanStop(str(s.get('stop_id')), None, (float(s['lat']), float(s['lon'])), 0.0)
            for s in stops if isinstance(s.get('lat'), (int, float)) and isinstance(s.get('lon'), (int, float))]
    fixes = track_fixes(points)
    day = ac.reconstruct(fixes, plan, depot)
    return {'points': len(points), 'km': round(day.km_gps, 1), 'first': clock.iso(fixes[0].at),
            'last': clock.iso(fixes[-1].at)}


def refuel_flags(refuels: Sequence[Mapping[str, Any]], until: datetime | None = None) -> dict[str, list[str]]:
    """id заправки → флаги: сохранённые при приёме без odometer_suspicious + odometer_suspicious, если одометр не входит
    в самую длинную согласованную цепочку действующих заправок машины (route_optimizer.learning.odometer_plausible;
    момент исправления — момент исходной заправки). Цепочка — по окну заправок до until (effective_refuels: последние
    REFUEL_WINDOW_DAYS дней, не больше REFUEL_WINDOW_MAX); вне окна флаг не пересчитывается. У вытесненных флага нет."""
    out = {r['id']: [f for f in r.get('flags') or () if f != SUSPICIOUS] for r in refuels}
    for items in effective_refuels(refuels, until).values():
        for (_, eid, _), ok in zip(items, odometer_plausible([(at, p.get('odometer_km')) for at, _, p in items])):
            if not ok:
                out[eid] = sorted({*out[eid], SUSPICIOUS})
    return out


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

    def version(self, car_code: str, day: str) -> tuple[Any, ...]:
        """Отпечаток данных машины за день (Store.day_version): тот же — день() вернёт то же."""
        return self.store.day_version(car_code, day) if self._exists() else ()

    def day(self, car_code: str, day: str) -> dict[str, Any]:
        """Трек и точки машины за день: track — [(at_ms, lat, lon, acc, spd)]; stops — точки последнего снимка /day
        машины на эту дату: stop_id, customer_id, name, lat, lon, weight_kg, seq, delivered_share (доля доставленного по
        действующей доставке точки, правило §5 п. 12/14; доставки нет — None), delivered_at (момент отметки)."""
        if not self._exists():
            return {'track': [], 'stops': []}
        track = [tuple(p[:5]) for p in self.store.track(car_code, day)]
        stops = self.store.day_stops(day, car_code)
        deliveries = mg.latest_by_stop(self.store.events_for_day(day, 'delivery'), 'delivery')
        out = []
        for s in stops:
            cust = s.get('customer') or {}
            cid = cust.get('id')
            delivery = deliveries.get(s.get('stop_id'))
            out.append({'stop_id': s.get('stop_id'), 'customer_id': cid if isinstance(cid, int) and not
                        isinstance(cid, bool) else None, 'name': cust.get('name'), 'lat': s.get('lat'), 'lon': s.get('lon'),
                        'weight_kg': s.get('weight_kg'), 'seq': s.get('seq'),
                        'delivered_share': delivered_share(s, delivery),
                        'delivered_at': delivery.get('at') if delivery is not None else None})
        return {'track': track, 'stops': out}

    def refuels(self) -> list[dict[str, Any]]:
        """Все заправки (Store.refuels): с флагами, признаком superseded и моментом исходной заправки (eff_*)."""
        return self.store.refuels() if self._exists() else []

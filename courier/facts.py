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

import math
import os
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from route_optimizer import actuals as ac
from route_optimizer.geo import Point
from route_optimizer.learning import REFUEL_WINDOW_DAYS, effective_refuels, odometer_plausible, track_fixes

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


def office_window(day: date) -> tuple[datetime, datetime]:
    """Окно пересчёта флага одометра для офиса: ± REFUEL_WINDOW_DAYS / 2 вокруг показываемого дня (Ереван) — и более
    поздние заправки, которые разрешают неоднозначность, и ограниченное время."""
    half = timedelta(days=REFUEL_WINDOW_DAYS // 2)
    return (datetime.combine(day, datetime.min.time(), clock.YEREVAN) - half,
            datetime.combine(day, datetime.max.time(), clock.YEREVAN) + half)


def refuel_flags(refuels: Sequence[Mapping[str, Any]], since: datetime | None = None,
                 until: datetime | None = None) -> dict[str, list[str]]:
    """id заправки → флаги: сохранённые при приёме без odometer_suspicious + odometer_suspicious, если одометр не входит
    в самую длинную согласованную цепочку действующих заправок машины (route_optimizer.learning.odometer_plausible;
    момент исправления — момент исходной заправки). Цепочка — по заправкам с моментом в [since, until] (офис — вокруг
    показываемого дня); вне окна флаг не пересчитывается. У вытесненных флага нет."""
    out = {r['id']: [f for f in r.get('flags') or () if f != SUSPICIOUS] for r in refuels}
    for items in effective_refuels(refuels, since, until).values():
        for (_, eid, _), ok in zip(items, odometer_plausible([(at, p.get('odometer_km')) for at, _, p in items])):
            if not ok:
                out[eid] = sorted({*out[eid], SUSPICIOUS})
    return out


def _number(x: Any) -> float:
    """Число JSON (не bool) — float, иначе 0."""
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def delivered_share(stop: Mapping[str, Any], delivery: Mapping[str, Any] | None) -> float | None:
    """Доля доставленного по точке (0…1); learning.plan_stops умножает её на вес накладной — доставлено, кг.

    Правило №65 — по весу товаров: Σ по строкам min(доставлено, qty строки) / qty строки × weight_kg строки, делённое на
    Σ weight_kg строк. Строки доставки — по line_id строк точки (неизвестная строка не считается); сверх qty строки —
    прижимается к нему по каждой строке (лишние пачки 0,5 л не покрывают недовезённые бутыли 19 л); строка без веса
    (товара нет в ERP — null) — 0 кг, как в weight_kg точки. Полная доставка — ровно 1: доставлено = вес накладной.
    Снимок /day до №65 (у строк нет weight_kg) или вес строк 0 — прежняя доля штук: Σ qty доставки / Σ qty строк,
    прижатая к 0…1 в целом. Доставки нет или строк нет — None (неизвестно: такая точка не идёт в обучение разгрузки)."""
    if delivery is None:
        return None
    lines = [ln for ln in stop.get('lines') or () if isinstance(ln, dict)]
    total = sum(_number(ln.get('qty')) for ln in lines)
    if total <= 0:
        return None
    items = [i for i in (delivery.get('payload') or {}).get('lines') or () if isinstance(i, dict)]
    if any('weight_kg' in ln for ln in lines):
        done = {i['line_id']: _number(i.get('qty')) for i in items if isinstance(i.get('line_id'), str)}
        weighed = [(q, w, done.get(ln.get('line_id'), 0.0)) for ln in lines
                   if (q := _number(ln.get('qty'))) > 0 and (w := _number(ln.get('weight_kg'))) > 0]
        kg = math.fsum(w for _, w, _ in weighed)
        if kg > 0:
            return max(0.0, min(1.0, math.fsum(w * max(0.0, min(d, q)) / q for q, w, d in weighed) / kg))
    done_qty = sum(_number(i.get('qty')) for i in items)
    return max(0.0, min(1.0, done_qty / total))


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
        действующей доставке точки, правило §5 п. 12/14, по весу товаров — №65; доставки нет — None), delivered_at
        (момент отметки)."""
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

    def refuels(self, since: str = '') -> list[dict[str, Any]]:
        """Заправки (Store.refuels; since — с этого дня, пусто — все): с флагами, признаком superseded и моментом
        исходной заправки (eff_*)."""
        return self.store.refuels(since) if self._exists() else []

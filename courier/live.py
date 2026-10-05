# -*- coding: utf-8 -*-
"""Факт машин за день для карты «Մեքենաները առցանց» (№76, docs/plans/live-map-plan.md).

LiveSource.fleet(день) — сырые данные терминалов по машинам, без расчётов (их делает route_optimizer.live): точки /day
со статусом по правилу §5 п. 12 (views.day_model — как «Առաքում այսօր») и долей доставленного, трек, водители,
последняя связь, состояние терминала (track.device, APK 2.2.0), закрытие дня. Подключает app_v2
(route_optimizer.attach_live_facts): пакет route_optimizer не импортирует courier. Только чтение; нет courier.db — пусто,
файл не создаётся. Вызывается в запросе Flask (day_model берёт базу из состояния раздела).

Доля доставленного точки (delivered_share, №65 — по весу строк): своя действующая доставка; нет своей, а точка
поглотила другие (заказ, из которого сделана накладная, §5 п. 12) — доставленные кг поглощённых / вес точки (не больше
1); иначе по статусу правила: full и covered (товар отдан по заказу) — 1, refused — 0, прочее — None (не доставлено).
"""
from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime
from typing import Any, Mapping

from . import clock, merge as mg
from .facts import delivered_share
from .store import Store

TTL_TODAY_S = 10.0     # расчёт флота за сегодня переиспользуется столько секунд (опрос карты — раз в 15 с на зрителя)
TTL_PAST_S = 600.0     # за прошлый день данные почти не меняются (доезжают пачки старых APK)
CACHE_DAYS = 8         # дней в кэше (дальше вытесняется самый старый)
_monotonic = time.monotonic   # подмена в тестах

DONE = ('full', 'partial', 'refused', 'covered')   # точка закрыта водителем (N из «N/M»)


def _number(x: Any) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def _unweighed(stop: Mapping[str, Any]) -> int:
    """Строк точки с кол-вом > 0 без веса (товара нет в ERP или вес 0): их кг неизвестны — в весе точки 0."""
    return sum(1 for ln in stop.get('lines') or () if isinstance(ln, dict) and _number(ln.get('qty')) > 0
               and _number(ln.get('weight_kg')) <= 0)


def _point(stop: Mapping[str, Any]) -> tuple[float, float] | None:
    lat, lon = stop.get('lat'), stop.get('lon')
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not isinstance(lat, bool) \
            and not isinstance(lon, bool):
        return float(lat), float(lon)
    return None


def _share(sid: str, stop: Mapping[str, Any], status: str, deliveries: Mapping[str, Mapping[str, Any]],
           absorbed: Mapping[str, list[str]], data: Mapping[str, Mapping[str, Any]]
           ) -> tuple[float | None, datetime | None]:
    """(доля доставленного 0…1 или None, момент отметки) — правило в описании модуля."""
    own = deliveries.get(sid)
    if own is not None:
        return delivered_share(stop, own), own['at']
    kg = _number(stop.get('weight_kg'))
    parts = [(x, deliveries[x]) for x in absorbed.get(sid, ()) if x in deliveries]
    if parts and kg > 0:
        done = math.fsum(_number(data[x].get('weight_kg')) * (delivered_share(data[x], d) or 0.0) for x, d in parts)
        return min(1.0, done / kg), max(d['at'] for _, d in parts)
    if status in ('full', 'covered'):
        return 1.0, None
    if status == 'refused':
        return 0.0, None
    return None, None


class _Entry:
    """Расчёт флота за один день: свой замок — одно вычисление на день, остальные ждут и берут его результат."""
    __slots__ = ('lock', 'value', 'fingerprint', 'expires')

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.value: dict[str, dict[str, Any]] | None = None
        self.fingerprint: tuple[Any, ...] | None = None
        self.expires = 0.0


class LiveSource:
    """Факт машин за день для «Маршрутов» (протокол route_optimizer.live.LiveFacts). Расчёт дня кэшируется в процессе
    (опрос карты каждые 15 с у каждого зрителя + /truck): TTL_TODAY_S / TTL_PAST_S; после TTL — отпечаток данных
    (Store.day_fingerprint): не менялся — тот же расчёт, иначе пересчёт. Возвращаемое — только для чтения."""

    def __init__(self, store: Store):
        self.store = store
        self._guard = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def _exists(self) -> bool:
        return os.path.exists(self.store.path)   # нет базы — не создаём её чтением

    def fleet(self, day: str) -> dict[str, dict[str, Any]]:
        """Машина → факт дня (с кэшем — см. класс; состав — _compute)."""
        if not self._exists():
            return {}
        with self._guard:
            entry = self._entries.get(day)
            if entry is None:
                if len(self._entries) >= CACHE_DAYS:   # вытесняется самый старый, но не сегодняшний
                    today = clock.today().isoformat()
                    self._entries.pop(next(k for k in self._entries if k != today))
                entry = self._entries[day] = _Entry()
        with entry.lock:
            if entry.value is not None and _monotonic() < entry.expires:
                return entry.value
            fingerprint = self.store.day_fingerprint(day)   # до расчёта: изменение во время него поймает следующий вызов
            if entry.value is None or fingerprint != entry.fingerprint:
                entry.value = self._compute(day)
                entry.fingerprint = fingerprint
            entry.expires = _monotonic() + (TTL_TODAY_S if day >= clock.today().isoformat() else TTL_PAST_S)
            return entry.value

    def _compute(self, day: str) -> dict[str, dict[str, Any]]:
        """Машина → факт дня (рабочий день day, YYYY-MM-DD):
        - stops — точки последнего /day машины: stop_id, customer_id, name, lat, lon, seq, weight_kg, unweighed (строк
          без веса), status (§5 п. 12), share (доля доставленного или None), delivered_at (ISO или None);
        - track — [(at_ms, lat, lon, acc, spd, brg)] по возрастанию момента;
        - drivers — водители, входившие на терминал машины (по последнему событию, последний — первым);
        - last_contact — последняя связь терминала машины (получение события или любой запрос терминала), ISO;
        - contacts — моменты получения событий машины (ISO, по возрастанию): перерывы связи за день;
        - device — последнее состояние терминала (track.device) + at — момент события; None — терминал не присылает;
        - devices — [(at ISO, gps)] всех состояний по порядку: когда GPS был выключен;
        - closed_at — момент последнего day_closed (ISO) или None.
        Машины — с событиями или точками /day на этот день."""
        from .views import _moment, day_model   # views импортирует facts; здесь — только в запросе (без цикла при импорте)
        events = self.store.events_for_day(day, skip_track=True)   # heartbeat-ы трека правилу дня не нужны
        beats = self.store.heartbeats_for_day(day)
        model = day_model(day, events)
        inputs_by_id = {e['id']: e for e in model.inputs}
        deliveries = mg.latest_by_stop(model.inputs, 'delivery')
        absorbed: dict[str, list[str]] = {}
        for x, owner in model.absorbed_by.items():
            absorbed.setdefault(owner, []).append(x)
        cars = sorted(set(model.current) | {e['car_code'] for e in events + beats})
        out: dict[str, dict[str, Any]] = {}
        for car in cars:
            stops = []
            for s in model.current.get(car, []):
                sid = s['stop_id']
                status = model.views[sid].status if sid in model.views else 'pending'
                share, at = _share(sid, s, status, deliveries, absorbed, model.data)
                cust = s.get('customer') or {}
                cid = cust.get('id')
                point = _point(s)
                stops.append({'stop_id': sid, 'customer_id': cid if isinstance(cid, int) and not isinstance(cid, bool)
                              else None, 'name': cust.get('name'), 'lat': point[0] if point else None,
                              'lon': point[1] if point else None, 'seq': s.get('seq'),
                              'weight_kg': _number(s.get('weight_kg')), 'unweighed': _unweighed(s), 'status': status,
                              'share': share, 'delivered_at': clock.iso(at) if at is not None else None})
            out[car] = {'stops': stops, 'track': self.store.track(car, day), 'drivers': [], 'last_contact': None,
                        'contacts': [], 'device': None, 'devices': [], 'closed_at': None}
        for e in sorted(events + beats, key=lambda e: e['received_at']):
            row = out[e['car_code']]
            row['contacts'].append(e['received_at'])
            name = e['driver_name'] or f'#{e["driver_id"]}'
            row['drivers'] = [name] + [n for n in row['drivers'] if n != name]
        for e in events:   # по моменту события (at_utc)
            if e['type'] == 'day_closed' and e['id'] in inputs_by_id:
                out[e['car_code']]['closed_at'] = clock.iso(inputs_by_id[e['id']]['at'])
        for e in beats:
            at = _moment(e)
            if e['device'] is not None and at is not None:
                row = out[e['car_code']]
                row['device'] = {**e['device'], 'at': clock.iso(at)}
                row['devices'].append((clock.iso(at), e['device'].get('gps')))
        for row in out.values():
            row['last_contact'] = row['contacts'][-1] if row['contacts'] else None
        for t in self.store.list_terminals():
            row = out.get(t.car_code)
            if row is not None and not t.revoked and t.last_seen_at and t.last_seen_at[:10] == day \
                    and (row['last_contact'] is None or t.last_seen_at > row['last_contact']):
                row['last_contact'] = t.last_seen_at
        return out

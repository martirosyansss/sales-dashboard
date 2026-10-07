# -*- coding: utf-8 -*-
"""Факт дня по людям для «Վարորդներ» (Маршруты): кто закрыл каждую точку, деньги и тара по водителю.

CrewSource — протокол route_optimizer.scorecard.CrewFacts; подключает app_v2 (route_optimizer.attach_crew_facts):
пакет route_optimizer не импортирует courier. Только чтение; нет courier.db — пусто, файл не создаётся. day()
вызывается в запросе Flask (правило дня и «Գումար» берут базу из состояния раздела).

- точки — последнего снимка /day каждой машины (как «Առաքում այսօր» и карта машин), статус — по правилу §5 п. 12
  (views.day_model); в ответе только закрытые (full / partial / refused / covered). Водитель точки — автор
  «заявления» (StopView.statement: действующая доставка, от которой статус), առաքիչ — helper_id того же события.
  covered (товар отдан по заказу) — автор последней доставки точек, поглощённых владельцем её компоненты; таких
  доставок нет — водителя нет (route_optimizer отдаёт точку водителю машины дня);
- деньги — ровно «Գումար» (views.money_drivers): expected (надо взять наличными по заявлениям водителя), short
  (expected − взято всеми водителями по этим накладным), collected (взято им: накладные + долги), handed / diff
  («сдал фактически» и сдал − взял; нет отметки — None);
- тара — штук по действующей отметке tare каждой точки (последняя, без вытесненных `supersedes`), по её автору.
"""
from __future__ import annotations

import math
import os
from typing import Any

from . import merge as mg
from .store import Store

DONE = ('full', 'partial', 'refused', 'covered')


def _number(x: Any) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else 0.0


class CrewSource:
    """Факт дня по людям для «Վարորդներ» (route_optimizer.scorecard.CrewFacts)."""

    def __init__(self, store: Store):
        self.store = store

    def _exists(self) -> bool:
        return os.path.exists(self.store.path)   # нет базы — не создаём её чтением

    def versions(self, since: str, until: str) -> dict[str, tuple[Any, ...]]:
        return self.store.crew_versions(since, until) if self._exists() else {}

    def names(self) -> dict[int, str]:
        return self.store.driver_names() if self._exists() else {}

    def day(self, day: str) -> dict[str, Any]:
        """{'stops': закрытые точки дня с водителем и առաքիչ, 'cash': водитель → деньги, 'tare': водитель → штук}."""
        if not self._exists():
            return {'stops': [], 'cash': {}, 'tare': {}}
        from .views import day_model, money_drivers   # views импортирует facts; здесь — только в запросе
        events = self.store.events_for_day(day, skip_track=True)   # heartbeat-ы трека не нужны ни правилу, ни деньгам
        model = day_model(day, events)
        by_id = {e['id']: e for e in events}
        deliveries = mg.latest_by_stop(model.inputs, 'delivery')
        stops = []
        for car, items in sorted(model.current.items()):
            for s in items:
                sid = s['stop_id']
                v = model.views.get(sid)
                if v is None or v.status not in DONE:
                    continue
                author = by_id.get(v.statement or '')
                if author is None and v.status == 'covered':
                    owner = model.paid_to.get(sid)
                    got = [deliveries[x] for x, o in model.absorbed_by.items() if o == owner and x in deliveries]
                    author = by_id.get(max(got, key=lambda d: (d['at'], d['id']))['id']) if got else None
                cust = s.get('customer') or {}
                cid = cust.get('id')
                stops.append({'stop_id': sid, 'car_code': car, 'status': v.status,
                              'customer_id': cid if isinstance(cid, int) and not isinstance(cid, bool) else None,
                              'name': cust.get('name') if isinstance(cust.get('name'), str) else None,
                              'driver_id': author['driver_id'] if author is not None else None,
                              'helper_id': author.get('helper_id') if author is not None else None})
        cash = {r['driver_id']: {'expected': r['expected'], 'short': r['expected_short'], 'collected': r['collected'],
                                 'handed': r['handed'], 'diff': r['diff']}
                for r in money_drivers(day, events, model)}
        tare: dict[int, float] = {}
        for e in mg.latest_by_stop(model.inputs, 'tare').values():
            src = by_id.get(e['id'])
            if src is None:
                continue
            qty = math.fsum(_number(i.get('qty')) for i in e['payload'].get('items') or () if isinstance(i, dict))
            tare[src['driver_id']] = tare.get(src['driver_id'], 0.0) + qty
        return {'stops': stops, 'cash': cash, 'tare': tare}

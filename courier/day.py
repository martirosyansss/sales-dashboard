# -*- coding: utf-8 -*-
"""Ответ GET /day (контракт §2): точки машины на дату по порядку объезда.

- данные ERP — erp_day.load_day (только чтение), порядок — order.order_customers, план и склад —
  routes_link (раздел «Маршруты»);
- координата клиента — как в «Маршрутах»: ручная точка логиста → адрес по умолчанию ERP (валидный, не
  «дефолтный») → медиана GPS визитов (route_optimizer.geo.resolve_coord);
- version — sha1 канонического JSON stops: тот же ERP и те же настройки → тот же version;
- кэш DAY_TTL_SECONDS на (машина, дата): терминалы перезапрашивают /day каждые 15 мин, а офис — чаще,
  ERP боевая; параллельные запросы одной машины ждут одну загрузку;
- каждая новая версия выдачи сохраняется в courier.db снимком (day_snapshots, append-only) — по всем версиям
  проверяются события терминала, по ним офис смотрит прошлые даты (ERP за прошлое не перечитывается);
- точка S: может нести `replaces` — точки O: заказов, из которых сделана накладная (контракт §5 п. 2);
  поле есть только у таких точек;
- строка точки несёт `weight_kg` — вес строки (№65; добавочное поле, как `gtin_units`: приложение любой версии
  неизвестные ключи игнорирует — Json { ignoreUnknownKeys = true }); по нему обучение «Развоза» считает доставленные кг;
- план «Развоза» — только выпущенный (ответ владельца №80, routes_link.RoutesView.released): до первого утверждения дня
  точки — лишь накладные ERP с машиной, порядок — не по плану; `plan` ответа — 'approved' | 'pending' (добавочное поле,
  как `weight_kg`: будущий APK покажет «Պլանը դեռ հաստատված չէ»). Утверждение доходит до терминала не позже
  DAY_TTL_SECONDS кэша + его опрос /day.
- подарки ERP (SALEDOCGIFTS, ответ владельца №90; контракт §11) — отдельные строки точки после строк документа: тот же
  product_id, line_id «<fISN>:G<fROWNUM>», цена и сумма 0, название с «(նվեր)», `gift: true` (поле только у них),
  weight_kg — их вес; amount_due не меняется (сумма документа). Ожидаемая тара — и по подаркам: бутыль-подарок едет в
  своей таре (товар 200 «19լ» с тарой 202 — среди подарков сентября). У подарка, который акция ERP воспроизводит точно,
  — `gift_rule` {base_product_ids, per, qty} (erp_day.gift_rules): терминал пересчитывает подарок по доставленному
  (ответ владельца №90 «APK сам пересчитывает»); нет правила — подарок ручной.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Mapping

from route_optimizer.erp import ErpError
from route_optimizer.geo import Point, resolve_coord

from . import clock
from .erp_day import (ContainerLink, CustomerInfo, DayData, Doc, GiftRule, Line, OrdersPick, Product, collect_for,
                      expected_tare)
from .order import order_customers
from .routes_link import RoutesView, distance_fn, invoice_owner, orders_window, pick_orders
from .store import MarkSetting, Store

GIFT_LABEL = 'նվեր'   # к названию строки-подарка (№90): «Գառնի կրիստալլայն 6լ (նվեր)»

DAY_TTL_SECONDS = 60
DAY_CACHE_MAX = 64

DEMO_CAR = 'TEST'
DEMO_DAY = date(2000, 1, 1)

DayLoader = Callable[[str, date, tuple[date, date], OrdersPick, Callable[[int], bool]], DayData]


def _num(x: float) -> float | int:
    """pack_qty в JSON: целое — int (6), дробное — float."""
    return int(x) if float(x).is_integer() else round(float(x), 3)


def _resolve_points(data: DayData, view: RoutesView) -> dict[int, Point | None]:
    out: dict[int, Point | None] = {}
    for cid in sorted({d.customer_id for d in data.docs}):
        info = data.customers.get(cid)
        coord = resolve_coord(view.geo_overrides.get(cid), info.erp_point if info else None, data.gps.get(cid))
        out[cid] = coord.point
    return out


def _line_json(line: Line, product: Product | None, gtins: tuple[str, ...], mark: MarkSetting | None,
               gtin_units: Mapping[str, float | None] | None = None, rule: GiftRule | None = None) -> dict[str, Any]:
    marked = mark.marked if mark is not None else bool(product and product.markable)
    pack = mark.pack_qty if mark is not None else (product.pack_qty_erp if product else None)
    name = product.name if product else ''
    out = {
        # подарок (№90): свой счёт fROWNUM — «G» не даёт совпасть с line_id строки документа
        'line_id': f'{line.isn}:G{line.rownum}' if line.gift else f'{line.isn}:{line.rownum}',
        'product_id': line.product_id,
        'code': product.code if product else '',
        # APK 2.3.0 цену строки не показывает: подарок водитель узнаёт по названию (контракт §11)
        'name': f'{name} ({GIFT_LABEL})'.strip() if line.gift else name,
        'qty': round(line.qty, 3),
        'unit': product.unit if product else '',
        'price': round(line.price, 2),
        'sum': round(line.sum, 2),
        'marked': marked,
        'pack_qty': _num(pack) if pack is not None else None,
        'gtins': list(gtins),
        'gtin_units': {code: _num(qty) if qty is not None else None for code, qty in (gtin_units or {}).items()},
        # вес строки, кг (№65: доставлено по весу товаров — courier.facts.delivered_share); товара нет в ERP — null
        # (в weight_kg точки такая строка — 0 кг)
        'weight_kg': round(line.qty * product.weight, 3) if product else None,
    }
    if line.gift:
        out['gift'] = True   # поле только у подарков: содержимое точек без подарков — как до №90 (version дня машины
        #                      меняется, если подарок есть хоть у одной её точки)
        if rule is not None:  # правило акции ERP (контракт §11 п. 5): терминал пересчитывает подарок по доставленному
            out['gift_rule'] = {'base_product_ids': list(rule.base_product_ids), 'per': _num(rule.per),
                                'qty': _num(rule.qty)}
    return out


def build_stops(data: DayData, order: list[int], points: Mapping[int, Point | None],
                marks: Mapping[int, MarkSetting]) -> list[dict[str, Any]]:
    """Точки в порядке объезда (клиенты order; у клиента — накладные по номеру), seq с 1."""
    by_customer: dict[int, list[Doc]] = {}
    for d in data.docs:
        by_customer.setdefault(d.customer_id, []).append(d)
    stops: list[dict[str, Any]] = []
    for cid in order:
        info = data.customers.get(cid) or CustomerInfo(cid, str(cid), '', '', None, None, None)
        point = points.get(cid)
        for doc in sorted(by_customer.get(cid, ()), key=lambda d: (d.doc_number, d.stop_id)):
            lines = data.lines.get(doc.isn, ())
            products = data.products
            tare = expected_tare([(ln.product_id, ln.qty) for ln in lines], data.containers)
            stops.append({
                'stop_id': doc.stop_id,
                'seq': len(stops) + 1,
                'source': doc.source,
                'doc_number': doc.doc_number,
                'customer': {'id': cid, 'code': info.code, 'name': info.name, 'address': info.address,
                             'phone': info.phone, 'tax_id': info.tax_id},
                'lat': round(point[0], 6) if point else None,
                'lon': round(point[1], 6) if point else None,
                'agent_name': data.agents.get(doc.agent_id, ''),
                'pay_type': doc.pay_type or None,
                'collect': collect_for(doc.pay_type),
                'amount_due': round(doc.amount, 2),
                'debt': None if data.debts is None else round(data.debts.get(cid, 0.0), 2),
                'weight_kg': round(sum(ln.qty * (products[ln.product_id].weight if ln.product_id in products else 0)
                                       for ln in lines), 1),
                'lines': [_line_json(ln, products.get(ln.product_id), data.gtins.get(ln.product_id, ()),
                                     marks.get(ln.product_id), data.gtin_units.get(ln.product_id),
                                     data.gift_rules.get((doc.isn, ln.rownum)) if ln.gift else None) for ln in lines],
                'tare_expected': [{'tare_id': f'erp:{tid}', 'name': data.tare_names.get(tid, ''), 'qty': round(q, 2)}
                                  for tid, q in sorted(tare.items())],
            })
            if doc.replaces:
                stops[-1]['replaces'] = sorted(doc.replaces)
    return stops


def stops_version(stops: list[dict[str, Any]]) -> str:
    raw = json.dumps(stops, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def day_payload(data: DayData, view: RoutesView, store: Store, loaded_at: datetime) -> dict[str, Any]:
    points = _resolve_points(data, view)
    dist = distance_fn(view, [p for p in points.values() if p is not None] + ([view.depot] if view.depot else []))
    # №80: порядок плана — только выпущенного (до утверждения — как без плана)
    order, source = order_customers(points, view.depot, view.car_customers(data.car_code) if view.released else [], dist)
    stops = build_stops(data, order, points, store.mark_settings())
    tare_types = [{'tare_id': f'erp:{tid}', 'name': name} for tid, name in sorted(data.tare_names.items())]
    tare_types += [{'tare_id': f'custom:{t["id"]}', 'name': t['name']} for t in store.tare_custom()]
    return {
        'date': data.day.isoformat(),
        'version': stops_version(stops),
        'loaded_at': clock.iso(loaded_at),
        'car': {'code': data.car_code, 'name': data.car_name},
        'depot': {'lat': view.depot[0], 'lon': view.depot[1]} if view.depot else None,
        'order_source': source,
        'stops': stops,
        'tare_types': tare_types,
        'refuse_reasons': [{'id': r['id'], 'text': r['text']} for r in store.reasons('refuse')],
        'return_reasons': [{'id': r['id'], 'text': r['text']} for r in store.reasons('return')],
        'plan': 'approved' if view.released else 'pending',   # №80: план дня ещё не утверждён — точек из плана нет
    }


# --- Тестовые данные (контракт §4): машина TEST, дата 2000-01-01, только при COURIER_DEMO=1 ---

_DEMO_ISN = '00000000-0000-4000-8000-00000000000{}'


def demo_data() -> tuple[DayData, RoutesView, datetime]:
    """Три точки: cash / none / cash_ecr; одна маркируемая строка с pack_qty 6."""
    isn = [_DEMO_ISN.format(i) for i in (1, 2, 3)]
    docs = (
        Doc(f'S:{isn[0]}', 'invoice', isn[0], 'DEMO-0001', 900001, 1, '1', 12000.0),
        Doc(f'S:{isn[1]}', 'invoice', isn[1], 'DEMO-0002', 900002, 1, '2', 21600.0),
        Doc(f'S:{isn[2]}', 'invoice', isn[2], 'DEMO-0003', 900003, 1, '5', 8400.0),
    )
    lines = {
        isn[0]: (Line(isn[0], 1, 990019, 10.0, 1200.0, 12000.0),),
        isn[1]: (Line(isn[1], 1, 990033, 24.0, 150.0, 3600.0), Line(isn[1], 2, 990019, 15.0, 1200.0, 18000.0)),
        isn[2]: (Line(isn[2], 1, 990050, 12.0, 700.0, 8400.0),),
    }
    products = {
        990019: Product(990019, 'D2901', 'Գառնի կրիստալլայն 19լ', 'հատ', 19.5, False, None),
        990033: Product(990033, 'D2101', 'Գառնի կրիստալլայն 0.33լ', 'հատ', 0.35, True, 6.0),
        990050: Product(990050, 'D1001', 'Գառնի կոլա 0.5լ', 'հատ', 0.55, False, 12.0),
        990202: Product(990202, 'D4003', 'Պոլիմերային տարա 20լ', 'հատ', 1.0, False, None, True),
    }
    customers = {
        900001: CustomerInfo(900001, 'T001', 'Թեստ խանութ 1', 'Երևան, Աբովյան 1', '+37410000001', '00000001',
                             (40.1830, 44.5150)),
        900002: CustomerInfo(900002, 'T002', 'Թեստ խանութ 2', 'Երևան, Կոմիտաս 10', None, '00000002',
                             (40.2050, 44.5000)),
        900003: CustomerInfo(900003, 'T003', 'Թեստ խանութ 3', 'Երևան, Բաղրամյան 5', '+37410000003', None,
                             (40.1900, 44.5050)),
    }
    data = DayData(car_code=DEMO_CAR, day=DEMO_DAY, car_name='Թեստ', docs=docs, lines=lines, products=products,
                   gtins={990033: ('04850002370146',), 990019: ('04850002370122',)},
                   customers=customers, agents={1: 'Թեստ մենեջեր'},
                   containers=(ContainerLink(990019, 990202, 1.0, 1.0),), tare_names={990202: 'Պոլիմերային տարա 20լ'},
                   gps={}, debts={900001: 5000.0, 900002: 0.0, 900003: 15000.0})
    # демо-день — обычный рабочий день: «план утверждён» (№80), точки — накладные демо, плана нет
    return data, RoutesView(depot=(40.18, 44.51), released=True), datetime(2000, 1, 1, 8, 0, tzinfo=clock.YEREVAN)


# --- Сервис с кэшем ---

@dataclass
class _Slot:
    lock: threading.Lock
    at: float = 0.0
    payload: dict[str, Any] | None = None


class DayService:
    """/day с кэшем на (машина, дата). loader — чтение ERP (None — ERP не подключена); routes — вид на «Маршруты»."""

    def __init__(self, store: Store, loader: DayLoader | None, routes: Callable[[date], RoutesView],
                 demo: bool = False, ttl: float = DAY_TTL_SECONDS):
        self.store = store
        self.loader = loader
        self.routes = routes
        self.demo = demo
        self.ttl = ttl
        self._lock = threading.Lock()
        self._slots: dict[tuple[str, date], _Slot] = {}

    def invalidate(self) -> None:
        """Настройки маркировки, тары или причин изменились — следующий /day собирается заново."""
        with self._lock:
            self._slots.clear()

    def _slot(self, key: tuple[str, date]) -> _Slot:
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                while len(self._slots) >= DAY_CACHE_MAX:
                    self._slots.pop(next(iter(self._slots)))
                slot = self._slots[key] = _Slot(threading.Lock())
            return slot

    def get(self, car_code: str, day: date) -> dict[str, Any]:
        slot = self._slot((car_code, day))
        with slot.lock:
            if slot.payload is not None and time.monotonic() - slot.at < self.ttl:
                return slot.payload
            payload = self._build(car_code, day)
            self.store.save_day(day.isoformat(), car_code, payload['stops'], payload['version'], payload['loaded_at'])
            slot.payload, slot.at = payload, time.monotonic()
            return payload

    def _build(self, car_code: str, day: date) -> dict[str, Any]:
        if self.demo and car_code == DEMO_CAR and day == DEMO_DAY:
            data, view, loaded = demo_data()
            return day_payload(data, view, self.store, loaded)
        if self.loader is None:
            raise ErpError('ERP не подключена')
        view = self.routes(day)
        loaded = clock.now()
        data = self.loader(car_code, day, orders_window(day, view),
                           lambda orders, places: pick_orders(orders, day, view, car_code, places),
                           invoice_owner(view, car_code))
        return day_payload(data, view, self.store, loaded)

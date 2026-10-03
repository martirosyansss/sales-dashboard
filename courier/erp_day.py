# -*- coding: utf-8 -*-
"""Чтение ERP для /day и страниц офиса «Առաքիչ» — СТРОГО ТОЛЬКО ЧТЕНИЕ.

Все запросы идут через route_optimizer.erp._select (read-only guard: только SELECT/WITH, без слов
записи; соединение ApplicationIntent=ReadOnly; в каждой таблице WITH (NOLOCK); значения — `?`).
Никаких INSERT/UPDATE/DELETE/DDL/EXEC и временных таблиц.

Проверено по данным ERP (01.10.2026, только SELECT):
- SALEDOCDETAILS: fSUM = fQUANTITY × fDISCOUNTEDPRICE, Σ fSUM = SALES.fTOTALSUM (4573 накладных сентября);
  SALES.fDELIVERYADDRESSID не заполняется — адрес и точка берутся у адреса клиента по умолчанию;
- BARCODES — EAN-13 (8–13 цифр), по одному на товар; GTIN = код, дополненный нулями до 14 знаков;
- PRODUCTS.fADDITIONALUNITUSED = 1 у 79 товаров: «փաթեթ» = fBASEUNITQUANTITY / fADDITIONALUNITQUANTITY
  штук (12 у 0,5 л, 6 у 1,5 л) — это и есть «штук в упаковке» для предзаполнения;
- PRODUCTCONTAINERS: 17 связей, 10 товаров, 7 видов тары (fPRODUCTQUANTITY : fCONTAINERQUANTITY);
- TREES PayType: 1 Կանխիկ, 2 Անկանխիկ (բանկ), 3 Կրեդիտ, 4 Պարտքի ճշտում, 5 Կանխիկ ՀԴՄ, 6 Անկանխիկ ՀԴՄ;
- ДОЛГ И СЕГОДНЯШНЯЯ НАКЛАДНАЯ: каждая проведённая накладная (fSTATE = 2) СРАЗУ попадает в HICUSTOMERSDEBT
  строкой D (fOP = 'RLZ', fDEBTDOCISN = SALES.fISN, fDATE = дата накладной, сумма = fTOTALSUM) —
  на 01.10.2026: 185 из 185 накладных дня; у части накладных с типом 1 в тот же день уже есть строка C
  (fOP = 'PAY', оплата, внесённая офисом). Поэтому формула дашборда «на сейчас» уже содержит сегодняшнюю
  накладную, и водитель взял бы её дважды (amount_due + debt). Долг на терминале — «на утро» дня D:
  та же формула (Σ D − C по DOCUMENTS.fCUSTOMERID − |HIRESTCUSTOMERSSUM 01| − |02|), но движения
  HICUSTOMERSDEBT только с fDATE < D — как «долг на дату» в app_v2 (/api/managers: d.fDATE < дата + 1).
  HIRESTCUSTOMERSSUM без даты — текущий остаток, как и в дашборде.
- DOCPARENTS (проверено 01.10.2026, только SELECT): столбцы fISN, fDOCTYPE, fPARENTISN, fPARENTDOCTYPE.
  fISN — дочерний документ (у реализаций SALES fDOCTYPE = 2), fPARENTISN — родитель; fPARENTDOCTYPE = 1 —
  заказ ORDERS (705 тыс. строк; 2 и 35 — редкие другие документы). За 24–30.09.2026: у 894 из 901 накладной с
  машиной ровно один родительский заказ, клиент накладной = клиент заказа; обратного направления (накладная —
  родитель заказа) нет ни одного; заказов с несколькими накладными нет. Отсюда `replaces` точки S: (контракт
  §5 п. 2) — SQL_SALE_PARENTS.

Точки /day — ПО КЛИЕНТУ (контракт §5 п. 1): у клиента есть накладная машины на дату — точки S: (её заказы,
из которых сделана накладная, — в `replaces`); у клиента накладной нет — точки O: по заказам, которые «Развоз»
отдал машине (routes_link.pick_orders) и по которым накладной ещё нет нигде (DispatchOrder.shipped пуст:
накладная на другой машине — это точка той машины).

Справочники (тара, «дефолтные» точки, менеджеры, машины) кэшируются на REF_TTL_SECONDS — они меняются редко,
а /day каждой машины перечитывался бы каждую минуту.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence

from route_optimizer import erp
from route_optimizer.dispatch import DispatchOrder
from route_optimizer.geo import Point, is_valid_point, median_point, point_key
from route_optimizer.geo import GPS_MAX_ACCURACY_M, GPS_MIN_VISITS

logger = logging.getLogger(__name__)

_select = erp._select   # единственная точка выполнения SQL (read-only guard) — общая с разделом «Маршруты»
_chunks = erp._chunks
_ph = erp._placeholders
_str = erp._str

GPS_WINDOW_DAYS = 365
SOLD_WINDOW_DAYS = 90
CARS_WINDOW_DAYS = 90
REF_TTL_SECONDS = 600

# --- Способ оплаты (контракт §2, решение владельца №12) ---

COLLECT_BY_PAYTYPE = {'1': 'cash', '5': 'cash_ecr', '2': 'none', '3': 'none', '6': 'none'}


def collect_for(pay_type: str | None) -> str:
    """fPAYTYPE → что делает водитель: 1 — cash, 5 — cash_ecr, 2/3/6 — none, пусто и прочее — ask."""
    return COLLECT_BY_PAYTYPE.get((pay_type or '').strip(), 'ask')


# --- Чистые функции над строками ERP ---

def to_gtin14(barcode: str | None) -> str | None:
    """Штрихкод ERP → GTIN-14 (EAN-8/UPC-12/EAN-13/GTIN-14, дополнение нулями слева); иное — None."""
    code = (barcode or '').strip()
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return None
    return code.zfill(14)


def pack_qty_from_units(used: Any, base_qty: Any, add_qty: Any) -> float | None:
    """«Штук в упаковке» из доп. единицы ERP: base / add, если единица используется и это > 1 штуки."""
    try:
        base, add = float(base_qty or 0), float(add_qty or 0)
    except (TypeError, ValueError):
        return None
    if not used or base <= 0 or add <= 0:
        return None
    ratio = base / add
    return ratio if ratio > 1 else None


@dataclass(frozen=True)
class ContainerLink:
    product_id: int
    container_id: int
    product_qty: float
    container_qty: float


def expected_tare(lines: Sequence[tuple[int, float]], links: Sequence[ContainerLink]) -> dict[int, float]:
    """Ожидаемая тара точки: Σ qty строки × fCONTAINERQUANTITY / fPRODUCTQUANTITY по таре товара.
    lines — (товар, количество); связи с fPRODUCTQUANTITY ≤ 0 пропускаются (битая запись ERP)."""
    by_product: dict[int, list[ContainerLink]] = {}
    for link in links:
        if link.product_qty > 0 and link.container_qty > 0:
            by_product.setdefault(link.product_id, []).append(link)
    out: dict[int, float] = {}
    for product_id, qty in lines:
        for link in by_product.get(product_id, ()):
            out[link.container_id] = out.get(link.container_id, 0.0) + qty * link.container_qty / link.product_qty
    return {k: v for k, v in out.items() if v > 0}


def compose_debts(ids: Sequence[int], debit: Mapping[int, float],
                  rest: Mapping[int, tuple[float, float]]) -> dict[int, float]:
    """Долг клиента по формуле дашборда: дебет (D − C) − |Type01| − |Type02|; нет записей — 0."""
    out = {}
    for cid in ids:
        t1, t2 = rest.get(cid, (0.0, 0.0))
        out[cid] = round(debit.get(cid, 0.0) - abs(t1) - abs(t2), 2)
    return out


# --- Данные дня ---

@dataclass(frozen=True)
class Doc:
    """Документ точки: накладная (SALES) или заказ (ORDERS, если накладных ещё нет)."""
    stop_id: str
    source: str            # invoice | order
    isn: str
    doc_number: str
    customer_id: int
    agent_id: int
    pay_type: str
    amount: float
    replaces: tuple[str, ...] = ()   # S: — точки O: заказов, из которых сделана накладная (DOCPARENTS)


@dataclass(frozen=True)
class Line:
    isn: str
    rownum: int
    product_id: int
    qty: float
    price: float
    sum: float


@dataclass(frozen=True)
class Product:
    id: int
    code: str
    name: str
    unit: str
    weight: float
    markable: bool
    pack_qty_erp: float | None
    is_container: bool = False


@dataclass(frozen=True)
class CustomerInfo:
    id: int
    code: str
    name: str
    address: str
    phone: str | None
    tax_id: str | None
    erp_point: Point | None    # адрес по умолчанию: валидная и не «дефолтная» точка


@dataclass(frozen=True)
class DayData:
    car_code: str
    day: date
    car_name: str
    docs: tuple[Doc, ...]
    lines: dict[str, tuple[Line, ...]]
    products: dict[int, Product]
    gtins: dict[int, tuple[str, ...]]
    customers: dict[int, CustomerInfo]
    agents: dict[int, str]
    containers: tuple[ContainerLink, ...]
    tare_names: dict[int, str]           # вид тары ERP (товар-тара) → название
    gps: dict[int, Point]                # медиана GPS визитов клиента (≥ 3 точных визита за год)
    debts: dict[int, float] | None       # None — посчитать не удалось
    gtin_units: dict[int, dict[str, float | None]] = field(default_factory=dict)


# --- Запросы (все — SELECT с WITH (NOLOCK); {ph} — только плейсхолдеры `?`) ---

SQL_DAY_SALES = """
SELECT CAST(s.fISN AS nvarchar(36)), RTRIM(s.fDOCNUM), s.fCUSTOMERID, s.fSALESAGENTID,
       LTRIM(RTRIM(ISNULL(s.fPAYTYPE, ''))), s.fTOTALSUM
FROM SALES s WITH (NOLOCK)
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ? AND LTRIM(RTRIM(ISNULL(s.fDELIVERYCAR, ''))) = ?
"""

# Заказы, из которых сделаны накладные (см. docstring модуля: fISN — накладная, fPARENTISN — заказ)
SQL_SALE_PARENTS = """
SELECT CAST(p.fISN AS nvarchar(36)), CAST(p.fPARENTISN AS nvarchar(36))
FROM DOCPARENTS p WITH (NOLOCK)
WHERE p.fPARENTDOCTYPE = 1 AND p.fISN IN ({ph})
"""

SQL_ORDER_PAYTYPES = """
SELECT CAST(o.fISN AS nvarchar(36)), LTRIM(RTRIM(ISNULL(o.fPAYTYPE, '')))
FROM ORDERS o WITH (NOLOCK)
WHERE o.fISN IN ({ph})
"""

SQL_DOC_LINES = """
SELECT CAST(d.fISN AS nvarchar(36)), d.fROWNUM, d.fPRODUCTID, d.fQUANTITY, d.fDISCOUNTEDPRICE, d.fSUM
FROM SALEDOCDETAILS d WITH (NOLOCK)
WHERE d.fISN IN ({ph})
"""

SQL_PRODUCTS = """
SELECT p.fID, RTRIM(p.fCODE), p.fNAME, RTRIM(ISNULL(p.fMEASUREUNIT, '')), ISNULL(p.fWEIGHT, 0), p.fMARKABLE,
       p.fADDITIONALUNITUSED, p.fBASEUNITQUANTITY, p.fADDITIONALUNITQUANTITY, p.fCONTAINER
FROM PRODUCTS p WITH (NOLOCK)
WHERE p.fID IN ({ph})
"""

SQL_BARCODES = """
SELECT b.fPRODUCTID, RTRIM(b.fBARCODE), b.fBASEUNITQUANTITY, b.fMEASUREUNITQUANTITY
FROM BARCODES b WITH (NOLOCK)
WHERE b.fPRODUCTID IN ({ph})
"""

SQL_CONTAINERS = """
SELECT pc.fPRODUCTID, pc.fCONTAINERID, pc.fPRODUCTQUANTITY, pc.fCONTAINERQUANTITY, c.fNAME
FROM PRODUCTCONTAINERS pc WITH (NOLOCK)
JOIN PRODUCTS c WITH (NOLOCK) ON c.fID = pc.fCONTAINERID
"""

# Клиент: телефон, ИНН (fTAXCODE), адрес по умолчанию (текст и точка) — как default_address в «Маршрутах»
SQL_CUSTOMER_INFO = """
SELECT c.fID, RTRIM(c.fCODE), c.fNAME, c.fPHONE, c.fTAXCODE, c.fADDRESS, a.fADDRESS, a.fLATITUDE, a.fLONGITUDE
FROM CUSTOMERS c WITH (NOLOCK)
OUTER APPLY (SELECT TOP 1 x.fADDRESS, x.fLATITUDE, x.fLONGITUDE
             FROM CUSTOMERDELIVERYADDRESSES x WITH (NOLOCK)
             WHERE x.fCUSTOMERID = c.fID AND x.fDEFAULT = 1 AND ISNULL(x.fCLOSED, 0) = 0
             ORDER BY x.fID) a
WHERE c.fID IN ({ph})
"""

# «Дефолтные» точки: одна координата (5 знаков) у ≥ 3 разных клиентов — невалидны (geo.default_point_keys)
SQL_DEFAULT_POINTS = """
SELECT ROUND(a.fLATITUDE, 5), ROUND(a.fLONGITUDE, 5)
FROM CUSTOMERDELIVERYADDRESSES a WITH (NOLOCK)
WHERE a.fLATITUDE IS NOT NULL AND a.fLONGITUDE IS NOT NULL
GROUP BY ROUND(a.fLATITUDE, 5), ROUND(a.fLONGITUDE, 5)
HAVING COUNT(DISTINCT a.fCUSTOMERID) >= 3
"""

SQL_GPS_VISITS = """
SELECT v.fCUSTOMERID, v.fLATITUDE, v.fLONGITUDE, v.fACCURACY
FROM ACTUALROUTES v WITH (NOLOCK)
WHERE v.fDATE >= ? AND v.fDATE < ? AND v.fLATITUDE IS NOT NULL AND v.fCUSTOMERID IN ({ph})
"""

# Долг «на утро» дня D: движения HICUSTOMERSDEBT до D (см. docstring модуля)
SQL_DEBIT_BEFORE = """
SELECT doc.fCUSTOMERID, SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END)
FROM HICUSTOMERSDEBT d WITH (NOLOCK)
JOIN DOCUMENTS doc WITH (NOLOCK) ON doc.fISN = d.fDEBTDOCISN
WHERE doc.fCUSTOMERID IN ({ph}) AND d.fDATE < ?
GROUP BY doc.fCUSTOMERID
"""

SQL_PRODUCT_CATALOG = """
SELECT p.fID, RTRIM(p.fCODE), p.fNAME, RTRIM(ISNULL(p.fMEASUREUNIT, '')), p.fMARKABLE, p.fADDITIONALUNITUSED,
       p.fBASEUNITQUANTITY, p.fADDITIONALUNITQUANTITY, p.fCLOSED, p.fCONTAINER
FROM PRODUCTS p WITH (NOLOCK)
"""

SQL_SOLD_PRODUCTS = """
SELECT d.fPRODUCTID, SUM(d.fQUANTITY)
FROM SALEDOCDETAILS d WITH (NOLOCK)
JOIN SALES s WITH (NOLOCK) ON s.fISN = d.fISN
WHERE s.fSTATE = 2 AND s.fDATE >= ?
GROUP BY d.fPRODUCTID
"""

SQL_CARS_SEEN = """
SELECT LTRIM(RTRIM(s.fDELIVERYCAR)), COUNT(*), MAX(CAST(s.fDATE AS date))
FROM SALES s WITH (NOLOCK)
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND LTRIM(RTRIM(ISNULL(s.fDELIVERYCAR, ''))) <> ''
GROUP BY LTRIM(RTRIM(s.fDELIVERYCAR))
"""

SQL_INVOICE_CARS = """
SELECT CAST(s.fISN AS nvarchar(36)), RTRIM(s.fDOCNUM), s.fCUSTOMERID, LTRIM(RTRIM(ISNULL(s.fDELIVERYCAR, '')))
FROM SALES s WITH (NOLOCK)
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ?
"""


# --- Загрузка ---

def _f(x: Any) -> float:
    return float(x or 0)


def day_sales(conn: Any, car_code: str, day: date) -> list[Doc]:
    out = []
    for r in _select(conn, SQL_DAY_SALES, (day, day + timedelta(days=1), car_code)):
        isn = _str(r[0]).upper()
        out.append(Doc(f'S:{isn}', 'invoice', isn, _str(r[1]), int(r[2]), int(r[3] or 0), _str(r[4]), _f(r[5])))
    return out


def sale_parents(conn: Any, isns: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Накладная → точки O: заказов, из которых она сделана (DOCPARENTS, fPARENTDOCTYPE = 1)."""
    out: dict[str, set[str]] = {}
    for chunk in _chunks(sorted(set(isns))):
        for r in _select(conn, SQL_SALE_PARENTS.format(ph=_ph(len(chunk))), chunk):
            out.setdefault(_str(r[0]).upper(), set()).add(f'O:{_str(r[1]).upper()}')
    return {k: tuple(sorted(v)) for k, v in out.items()}


# --- Кэш справочников ERP (REF_TTL_SECONDS) ---

_refs: dict[tuple[str, str], tuple[float, Any]] = {}
_refs_lock = threading.Lock()


def _ref(connection_string: str, name: str, load: Callable[[], Any]) -> Any:
    """Справочник из кэша (ключ — строка подключения и имя); устарел или нет — load() и в кэш."""
    key = (connection_string, name)
    with _refs_lock:
        hit = _refs.get(key)
        if hit is not None and time.monotonic() - hit[0] < REF_TTL_SECONDS:
            return hit[1]
    value = load()
    with _refs_lock:
        _refs[key] = (time.monotonic(), value)
    return value


def clear_ref_cache() -> None:
    with _refs_lock:
        _refs.clear()


def order_docs(conn: Any, orders: Sequence[DispatchOrder]) -> list[Doc]:
    """Заказы «Развоза» → документы точек (способ оплаты — из ORDERS.fPAYTYPE)."""
    pay: dict[str, str] = {}
    for chunk in _chunks(sorted({o.isn for o in orders})):
        for r in _select(conn, SQL_ORDER_PAYTYPES.format(ph=_ph(len(chunk))), chunk):
            pay[_str(r[0]).upper()] = _str(r[1])
    return [Doc(f'O:{o.isn}', 'order', o.isn, o.doc_num, o.customer_id, o.agent_id, pay.get(o.isn, ''),
                float(o.revenue)) for o in orders]


def doc_lines(conn: Any, isns: Sequence[str]) -> dict[str, tuple[Line, ...]]:
    out: dict[str, list[Line]] = {}
    for chunk in _chunks(sorted(set(isns))):
        for r in _select(conn, SQL_DOC_LINES.format(ph=_ph(len(chunk))), chunk):
            isn = _str(r[0]).upper()
            out.setdefault(isn, []).append(Line(isn, int(r[1] or 0), int(r[2]), _f(r[3]), _f(r[4]), _f(r[5])))
    return {k: tuple(sorted(v, key=lambda x: x.rownum)) for k, v in out.items()}


def products(conn: Any, ids: Sequence[int]) -> dict[int, Product]:
    out: dict[int, Product] = {}
    for chunk in _chunks(sorted(set(ids))):
        for r in _select(conn, SQL_PRODUCTS.format(ph=_ph(len(chunk))), chunk):
            out[int(r[0])] = Product(int(r[0]), _str(r[1]), _str(r[2]), _str(r[3]), _f(r[4]), bool(r[5]),
                                     pack_qty_from_units(r[6], r[7], r[8]), bool(r[9]))
    return out


def barcode_data(conn: Any, ids: Sequence[int]) -> tuple[dict[int, tuple[str, ...]], dict[int, dict[str, float | None]]]:
    """GTIN и количество базовых единиц по каждому штрихкоду ERP; неверный коэффициент — None."""
    out: dict[int, set[str]] = {}
    units: dict[int, dict[str, float | None]] = {}
    for chunk in _chunks(sorted(set(ids))):
        for r in _select(conn, SQL_BARCODES.format(ph=_ph(len(chunk))), chunk):
            g = to_gtin14(r[1])
            if g:
                pid = int(r[0])
                out.setdefault(pid, set()).add(g)
                try:
                    base, measure = float(r[2]), float(r[3])
                    qty = base / measure if base > 0 and measure > 0 else None
                    if qty is not None and (not math.isfinite(qty) or qty <= 0):
                        qty = None
                except (TypeError, ValueError, ZeroDivisionError):
                    qty = None
                product = units.setdefault(pid, {})
                # Разные коэффициенты у двух представлений одного GTIN не позволяют угадать количество.
                product[g] = None if g in product and product[g] != qty else qty
    return {k: tuple(sorted(v)) for k, v in out.items()}, units


def gtins(conn: Any, ids: Sequence[int]) -> dict[int, tuple[str, ...]]:
    return barcode_data(conn, ids)[0]


def containers(conn: Any) -> tuple[tuple[ContainerLink, ...], dict[int, str]]:
    links, names = [], {}
    for r in _select(conn, SQL_CONTAINERS):
        links.append(ContainerLink(int(r[0]), int(r[1]), _f(r[2]), _f(r[3])))
        names[int(r[1])] = _str(r[4])
    return tuple(links), names


def default_point_keys(conn: Any) -> set[Point]:
    return {(round(float(r[0]), 5), round(float(r[1]), 5)) for r in _select(conn, SQL_DEFAULT_POINTS)}


def customer_info(conn: Any, ids: Sequence[int], defaults: set[Point]) -> dict[int, CustomerInfo]:
    out: dict[int, CustomerInfo] = {}
    for chunk in _chunks(sorted(set(ids))):
        for r in _select(conn, SQL_CUSTOMER_INFO.format(ph=_ph(len(chunk))), chunk):
            lat, lon = r[7], r[8]
            point = None
            if is_valid_point(lat, lon):
                p = (float(lat), float(lon))
                point = None if point_key(p) in defaults else p
            out[int(r[0])] = CustomerInfo(int(r[0]), _str(r[1]), _str(r[2]), _str(r[6]) or _str(r[5]),
                                          _str(r[3]) or None, _str(r[4]) or None, point)
    return out


def gps_points(conn: Any, ids: Sequence[int], day: date) -> dict[int, Point]:
    """Медиана GPS визитов клиента за год до дня (как Snapshot.gps_points: точные, ≥ GPS_MIN_VISITS)."""
    raw: dict[int, list[Point]] = {}
    for chunk in _chunks(sorted(set(ids))):
        for r in _select(conn, SQL_GPS_VISITS.format(ph=_ph(len(chunk))),
                         (day - timedelta(days=GPS_WINDOW_DAYS), day, *chunk)):
            acc = None if r[3] is None else float(r[3])
            if is_valid_point(r[1], r[2]) and (acc is None or acc <= GPS_MAX_ACCURACY_M):
                raw.setdefault(int(r[0]), []).append((float(r[1]), float(r[2])))
    return {c: median_point(ps) for c, ps in raw.items() if len(ps) >= GPS_MIN_VISITS}


def morning_debts(conn: Any, ids: Sequence[int], day: date) -> dict[int, float]:
    """Долг клиентов на утро дня (см. docstring модуля): накладные дня в долг не входят."""
    debit: dict[int, float] = {}
    rest: dict[int, tuple[float, float]] = {}
    for chunk in _chunks(sorted(set(ids))):
        ph = _ph(len(chunk))
        for r in _select(conn, SQL_DEBIT_BEFORE.format(ph=ph), (*chunk, day)):
            debit[int(r[0])] = _f(r[1])
        for r in _select(conn, erp.SQL_CUSTOMER_REST.format(ph=ph), chunk):
            rest[int(r[0])] = (_f(r[1]), _f(r[2]))
    return compose_debts(sorted(set(ids)), debit, rest)


OrdersPick = Callable[[Sequence[DispatchOrder]], list[DispatchOrder]]


def load_day(connection_string: str, car_code: str, day: date, orders_window: tuple[date, date],
             pick_orders: OrdersPick) -> DayData:
    """Всё для /day машины на дату — одним read-only соединением.

    Точки — по клиенту (docstring модуля): проведённые накладные машины на дату (с `replaces` — их заказами) +
    заказы окна orders_window, которые pick_orders отдаёт этой машине, у клиентов без накладной машины и без
    накладной вообще."""
    conn = erp.connect(connection_string)
    try:
        sales = day_sales(conn, car_code, day)
        parents = sale_parents(conn, [d.isn for d in sales]) if sales else {}
        sales = [Doc(d.stop_id, d.source, d.isn, d.doc_number, d.customer_id, d.agent_id, d.pay_type, d.amount,
                     parents.get(d.isn, ())) for d in sales]
        invoiced = {d.customer_id for d in sales}
        picked = pick_orders(erp.dispatch_orders(conn, *orders_window))
        pending = [o for o in picked if o.shipped is None and o.customer_id not in invoiced]
        docs = sales + (order_docs(conn, pending) if pending else [])
        lines = doc_lines(conn, [d.isn for d in docs]) if docs else {}
        links, tare_names = _ref(connection_string, 'containers', lambda: containers(conn))
        product_ids = {ln.product_id for ls in lines.values() for ln in ls}
        prods = products(conn, sorted(product_ids | set(tare_names))) if product_ids or tare_names else {}
        codes, code_units = barcode_data(conn, sorted(product_ids)) if product_ids else ({}, {})
        cids = sorted({d.customer_id for d in docs})
        infos = customer_info(conn, cids, _ref(connection_string, 'default_points', lambda: default_point_keys(conn))) \
            if cids else {}
        gps = gps_points(conn, cids, day) if cids else {}
        agents = _ref(connection_string, 'agents', lambda: {a.id: a.name for a in erp.agents(conn).values()})
        car = _ref(connection_string, 'cars', lambda: erp.cars(conn)).get(car_code)
        debts: dict[int, float] | None
        try:
            debts = morning_debts(conn, cids, day) if cids else {}
        except erp.ErpError:
            logger.warning('[Courier] Долг клиентов не посчитан (%s, %s)', car_code, day, exc_info=True)
            debts = None
        return DayData(car_code=car_code, day=day, car_name=car.name if car else '', docs=tuple(docs), lines=lines,
                       products=prods, gtins=codes, customers=infos, agents=agents, containers=links,
                       tare_names=tare_names, gps=gps, debts=debts, gtin_units=code_units)
    finally:
        erp.close_quietly(conn)


# --- Справочники для офиса ---

@dataclass(frozen=True)
class CatalogItem:
    id: int
    code: str
    name: str
    unit: str
    markable_erp: bool
    pack_qty_erp: float | None
    closed: bool
    is_container: bool
    sold_qty_90: float


def product_catalog(connection_string: str, today: date) -> list[CatalogItem]:
    """Все товары ERP и продажи за 90 дней — для «Կարգավորումներ» (маркировка)."""
    conn = erp.connect(connection_string)
    try:
        sold = {int(r[0]): _f(r[1]) for r in _select(conn, SQL_SOLD_PRODUCTS, (today - timedelta(days=SOLD_WINDOW_DAYS),))}
        rows = _select(conn, SQL_PRODUCT_CATALOG)
    finally:
        erp.close_quietly(conn)
    return sorted((CatalogItem(int(r[0]), _str(r[1]), _str(r[2]), _str(r[3]), bool(r[4]),
                               pack_qty_from_units(r[5], r[6], r[7]), bool(r[8]), bool(r[9]), sold.get(int(r[0]), 0.0))
                   for r in rows), key=lambda x: (x.code, x.id))


def cars_seen(connection_string: str, today: date) -> list[dict[str, Any]]:
    """Машины из SALES.fDELIVERYCAR за 90 дней (для регистрации терминала): код, название, накладных, последний день."""
    conn = erp.connect(connection_string)
    try:
        rows = _select(conn, SQL_CARS_SEEN, (today - timedelta(days=CARS_WINDOW_DAYS),))
        names = erp.cars(conn)
    finally:
        erp.close_quietly(conn)
    return sorted(({'code': _str(r[0]), 'name': names[_str(r[0])].name if _str(r[0]) in names else '',
                    'docs': int(r[1]), 'last': erp._day(r[2]).isoformat()} for r in rows), key=lambda c: c['code'])


@dataclass(frozen=True)
class InvoiceCar:
    isn: str
    doc_number: str
    customer_id: int
    car_code: str


def invoice_cars(connection_string: str, day: date) -> tuple[list[InvoiceCar], dict[int, tuple[str, str]]]:
    """Накладные дня с машинами и (код, название) клиентов — для сравнения с планом «Развоза»."""
    conn = erp.connect(connection_string)
    try:
        rows = [InvoiceCar(_str(r[0]).upper(), _str(r[1]), int(r[2]), _str(r[3]))
                for r in _select(conn, SQL_INVOICE_CARS, (day, day + timedelta(days=1)))]
        names = {c.id: (c.code, c.name) for c in erp.customers(conn, sorted({r.customer_id for r in rows})).values()}
    finally:
        erp.close_quietly(conn)
    return rows, names

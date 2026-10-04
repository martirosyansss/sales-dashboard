# -*- coding: utf-8 -*-
"""Բեռնագիր — что машина грузит на складе в каждом рейсе плана «Развоза» (ответ владельца №57): товар, код, единица,
количество, упаковки, кг. Разбивки по магазинам нет (решение владельца).

Количество по заказу точки рейса:
- по заказу уже есть проведённая накладная (SALES с fSTATE = 2, заказ — родитель в DOCPARENTS с fPARENTDOCTYPE = 1) —
  строки накладной: склад отпускает по ней, та же основа у терминала водителя (courier/erp_day.py). Замер 01–02.10.2026:
  накладная есть у 304 из 319 заказов, у 15 из них количество не как в заказе; заказов с несколькими накладными нет
  (были бы — их сумма). Накладная из нескольких заказов (у 7 из 901 накладных 24–30.09 не ровно один заказ-родитель)
  считается один раз — у её заказа с наименьшим fISN среди запрошенных; если среди её заказов есть не запрошенный (не в
  этих рейсах, напр. «не везём сегодня»), её товар всё равно весь здесь — такой заказ в mixed, накладная предупреждает;
- накладной ещё нет — строки самого заказа (SALEDOCDETAILS по fISN заказа, из них же план считает кг).

Тяжёлый магазин план везёт несколькими рейсами поровну (dispatch._shares, у точки share; рейсы бывают на разных машинах).
Товары магазина делит split_stop: каждый товар (сумма по заказам магазина) — поровну ЦЕЛЫМИ упаковками «փաթեթ», затем
целыми штуками; что поровну не делится, по одной упаковке / штуке получает самый лёгкий на тот момент рейс, товары — от
тяжёлой упаковки к лёгкой: рейсы ровные по кг, как их считает план, и сумма по всем рейсам равна документам точно. Дробное
или отрицательное количество (за 25.09–03.10.2026 таких строк не было) — поровну с точностью ERP (split_qty, 4 знака).

ERP — только чтение: route_optimizer.erp._select (SELECT, WITH (NOLOCK), значения `?`). Пакет courier не импортируется.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from . import erp
from .store import check_driver_name

QTY_SCALE = 10_000   # дробное количество ERP (money) — 4 знака: делим целые десятитысячные

# Проведённые накладные заказов (fISN заказа → fISN накладной)
SQL_ORDER_INVOICES = """
SELECT CAST(p.fPARENTISN AS nvarchar(36)), CAST(s.fISN AS nvarchar(36))
FROM DOCPARENTS p WITH (NOLOCK)
JOIN SALES s WITH (NOLOCK) ON s.fISN = p.fISN
WHERE p.fPARENTDOCTYPE = 1 AND s.fSTATE = 2 AND p.fPARENTISN IN ({ph})
"""

# Все заказы-родители найденных накладных: накладная из нескольких заказов считается один раз
SQL_INVOICE_PARENTS = """
SELECT CAST(p.fISN AS nvarchar(36)), CAST(p.fPARENTISN AS nvarchar(36))
FROM DOCPARENTS p WITH (NOLOCK)
WHERE p.fPARENTDOCTYPE = 1 AND p.fISN IN ({ph})
"""

# Строки документов (заказов и накладных): товар и количество в базовой единице товара
SQL_DOC_LINES = """
SELECT CAST(d.fISN AS nvarchar(36)), d.fPRODUCTID, d.fQUANTITY
FROM SALEDOCDETAILS d WITH (NOLOCK)
WHERE d.fISN IN ({ph})
"""

# Товары: базовая единица (код ERP, напр. «հատ»), вес единицы; упаковка «փաթեթ» = fBASEUNITQUANTITY / fADDITIONALUNITQUANTITY
SQL_PRODUCTS = """
SELECT p.fID, RTRIM(p.fCODE), p.fNAME, RTRIM(ISNULL(p.fMEASUREUNIT, '')), ISNULL(p.fWEIGHT, 0),
       p.fADDITIONALUNITUSED, p.fBASEUNITQUANTITY, p.fADDITIONALUNITQUANTITY
FROM PRODUCTS p WITH (NOLOCK)
WHERE p.fID IN ({ph})
"""


# Водители для выбора в «Վարորդ» (№62, ответ владельца: «из ERP + добавить своих»): кто развозил проведённые накладные
# за [since, until) — экспедитор накладной (fVANAGENTID), не сам менеджер; закрытые агенты ERP — нет. 04.10.2026 за 90 дней —
# 22 имени (агенты «B…»); одно имя у двух агентов бывает — в списке одно.
SQL_DRIVERS = """
SELECT a.fNAME
FROM SALES s WITH (NOLOCK)
JOIN SALESAGENTS a WITH (NOLOCK) ON a.fID = s.fVANAGENTID
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ? AND s.fVANAGENTID <> ISNULL(s.fSALESAGENTID, 0)
  AND ISNULL(a.fCLOSED, 0) = 0
GROUP BY a.fNAME
"""


@dataclass(frozen=True)
class Product:
    id: int
    code: str
    name: str
    unit: str
    kg: float               # вес единицы (PRODUCTS.fWEIGHT)
    pack: int | None        # штук в упаковке «փաթեթ» — целое > 1; None — упаковки нет


@dataclass(frozen=True)
class Lines:
    """Строки заказов точек: заказ (fISN, верхний регистр) → ((товар, количество), …) — из проведённых накладных заказа,
    если они есть, иначе из самого заказа (накладная из нескольких заказов — у одного из них, у прочих пусто); invoiced —
    заказы с накладной; mixed — заказы, чья накладная сделана и из заказа вне запроса (её товар весь здесь)."""
    by_order: Mapping[str, tuple[tuple[int, float], ...]]
    invoiced: frozenset[str]
    products: Mapping[int, Product]
    mixed: frozenset[str] = frozenset()


def pack_size(used: Any, base_qty: Any, add_qty: Any) -> int | None:
    """Штук в упаковке из доп. единицы ERP: base / add, если единица используется и это целое больше 1."""
    try:
        base, add = float(base_qty or 0), float(add_qty or 0)
    except (TypeError, ValueError):
        return None
    if not used or base <= 0 or add <= 0:
        return None
    ratio = base / add
    return int(ratio) if ratio > 1 and ratio.is_integer() else None


def load_lines(connection_string: str, isns: Sequence[str]) -> Lines:
    """Строки заказов isns и товары к ним — одним соединением (четыре запроса чанками)."""
    want = sorted({i.upper() for i in isns})
    conn = erp.connect(connection_string)
    try:
        invoices: dict[str, set[str]] = {}          # заказ → его проведённые накладные
        for chunk in erp._chunks(want):
            for r in erp._select(conn, SQL_ORDER_INVOICES.format(ph=erp._placeholders(len(chunk))), chunk):
                invoices.setdefault(erp._str(r[0]).upper(), set()).add(erp._str(r[1]).upper())
        found = sorted({s for v in invoices.values() for s in v})
        parents: dict[str, set[str]] = {s: {o for o, v in invoices.items() if s in v} for s in found}
        for chunk in erp._chunks(found):
            for r in erp._select(conn, SQL_INVOICE_PARENTS.format(ph=erp._placeholders(len(chunk))), chunk):
                if (known := parents.get(erp._str(r[0]).upper())) is not None:
                    known.add(erp._str(r[1]).upper())
        owned: dict[str, list[str]] = {}            # заказ → накладные, которые считаются у него
        mixed: set[str] = set()
        asked = set(want)
        for s in found:
            owner = min(parents[s] & asked)
            owned.setdefault(owner, []).append(s)
            if not parents[s] <= asked:
                mixed.add(owner)
        docs = sorted({i for i in want if i not in invoices} | set(found))
        raw: dict[str, list[tuple[int, float]]] = {}
        for chunk in erp._chunks(docs):
            for r in erp._select(conn, SQL_DOC_LINES.format(ph=erp._placeholders(len(chunk))), chunk):
                raw.setdefault(erp._str(r[0]).upper(), []).append((int(r[1]), float(r[2] or 0)))
        by_order = {i: tuple(x for doc in owned.get(i, ()) for x in raw.get(doc, ())) if i in invoices
                    else tuple(raw.get(i, ())) for i in want}
        products: dict[int, Product] = {}
        ids = sorted({pid for v in by_order.values() for pid, _ in v})
        for chunk in erp._chunks(ids):
            for r in erp._select(conn, SQL_PRODUCTS.format(ph=erp._placeholders(len(chunk))), chunk):
                products[int(r[0])] = Product(int(r[0]), erp._str(r[1]), erp._str(r[2]), erp._str(r[3]),
                                              float(r[4] or 0), pack_size(r[5], r[6], r[7]))
        return Lines(by_order, frozenset(invoices), products, frozenset(mixed))
    finally:
        erp.close_quietly(conn)


def load_drivers(connection_string: str, since: date, until: date) -> list[str]:
    """Имена водителей-экспедиторов ERP за [since, until) — как их напечатает накладная (check_driver_name: пробелы
    схлопнуты; негодное имя пропускается), без повторов, по алфавиту. Список для выбора необязателен — короткие
    таймауты: недоступная ERP не держит страницу."""
    conn = erp.connect(connection_string, login_timeout=3, query_timeout=10)
    try:
        rows = erp._select(conn, SQL_DRIVERS, (since, until))
    finally:
        erp.close_quietly(conn)
    names = {check_driver_name(erp._str(r[0]))[0] for r in rows}
    return sorted(n for n in names if n)


def _even(n: int, parts: int, index: int) -> int:
    base, rest = divmod(n, parts)
    return base + (1 if index < rest else 0)


def split_qty(qty: float, parts: int, index: int, pack: int | None = None) -> float:
    """Доля рейса index (0 … parts − 1) в количестве qty, которое магазин получает parts рейсами поровну: сначала поровну
    целые упаковки pack, затем поровну оставшиеся штуки; остаток деления — первым рейсам. Σ по index = qty точно. Для
    дробного и отрицательного количества в split_stop (там остатки — самому лёгкому рейсу)."""
    if parts < 1 or not 0 <= index < parts:
        raise ValueError(f'рейс {index} из {parts}')
    if parts == 1:
        return float(qty)
    sign = -1.0 if qty < 0 else 1.0
    q = abs(float(qty))
    if not q.is_integer():
        return sign * _even(round(q * QTY_SCALE), parts, index) / QTY_SCALE
    packs, loose = divmod(int(q), pack if pack and pack > 1 else 1)
    return sign * float(_even(packs, parts, index) * (pack if pack and pack > 1 else 1) + _even(loose, parts, index))


def split_stop(lines: Sequence[tuple[int, float]], parts: int, products: Mapping[int, Product]) -> list[dict[int, float]]:
    """Товары магазина (строки всех его заказов), который план везёт parts рейсами поровну, — по рейсам магазина в порядке
    плана: см. docstring модуля. Σ по рейсам каждого товара = сумме строк точно; при целых количествах рейсы по кг
    расходятся не больше чем на самую тяжёлую упаковку (или штуку без упаковки)."""
    if parts < 1:
        raise ValueError(f'рейсов {parts}')
    scaled: dict[int, int] = {}             # сумма в десятитысячных (точность ERP): не зависит от порядка строк
    for pid, q in lines:
        scaled[pid] = scaled.get(pid, 0) + round(q * QTY_SCALE)
    total = {pid: n / QTY_SCALE for pid, n in scaled.items()}
    out: list[dict[int, float]] = [{} for _ in range(parts)]
    kg = [0.0] * parts

    def give(i: int, pid: int, q: float, w: float) -> None:
        if q:
            out[i][pid] = out[i].get(pid, 0.0) + q
            kg[i] += q * w

    def unit_kg(pid: int) -> float:
        p = products.get(pid)
        return p.kg * (p.pack or 1) if p else 0.0

    for pid in sorted(total, key=lambda x: (-unit_kg(x), x)):
        q = total[pid]
        p = products.get(pid)
        w = p.kg if p else 0.0
        if q < 0 or not float(q).is_integer():
            for i in range(parts):
                give(i, pid, split_qty(q, parts, i), w)
            continue
        pack = p.pack if p and p.pack else 1
        packs, loose = divmod(int(q), pack)
        for size, count in ((pack, packs), (1, loose)):
            base, rest = divmod(count, parts)
            for i in range(parts):
                give(i, pid, float(base * size), w)
            for _ in range(rest):           # остаток — по одному самому лёгкому рейсу (при равенстве — раньше по плану)
                give(min(range(parts), key=lambda j: (kg[j], j)), pid, float(size), w)
    return out


def _code_key(p: Product) -> tuple[Any, ...]:
    return (0, int(p.code), p.name) if p.code.isdecimal() else (1, p.code, p.name)


def _row(p: Product, qty: float, known: bool) -> dict[str, Any]:
    whole = qty.is_integer() and qty >= 0 and p.pack is not None
    packs, loose = divmod(int(qty), p.pack) if whole else (None, None)
    return {'product_id': p.id, 'code': p.code, 'name': p.name, 'unit': p.unit, 'unknown': not known,
            'qty': int(qty) if qty.is_integer() else round(qty, 4),
            'pack': p.pack, 'packs': packs, 'loose': loose, 'kg': round(qty * p.kg, 1)}


def truck_waybill(plan: Mapping[str, Any], car_code: str, lines: Lines) -> dict[str, Any] | None:
    """Накладная (Բեռնագիր) машины car_code по плану дня plan (dispatch.plan_view): её рейсы по порядку, в каждом — товары
    рейса (по коду; товар, которого нет в ERP, — unknown, без кода), кг, сколько заказов с накладной (invoiced), с накладной
    и из заказа вне рейсов (mixed) и сколько магазинов везутся несколькими рейсами (split). basis — состав рейса
    [[клиент, share, [fISN заказов]], …]: страница сверяет его со своим планом. Машины нет в плане — None."""
    truck = next((t for t in plan['trucks'] if t['car_code'] == car_code), None)
    if truck is None:
        return None
    visits: dict[int, list[int]] = {}       # клиент → рейсы плана с ним, по порядку плана (как dispatch._shares)
    for t in plan['trucks']:
        for tr in t['trips']:
            for s in tr['stops']:
                visits.setdefault(s['customer_id'], []).append(tr['id'])
    trips = []
    for no, tr in enumerate(truck['trips'], 1):
        qty: dict[int, float] = {}
        orders = invoiced = mixed = split = 0
        for s in tr['stops']:
            seen = visits[s['customer_id']]
            parts, index = len(seen), seen.index(tr['id'])
            split += parts > 1
            isns = [o['isn'].upper() for o in s['orders']]
            orders += len(isns)
            invoiced += sum(i in lines.invoiced for i in isns)
            mixed += sum(i in lines.mixed for i in isns)
            share = split_stop([x for i in isns for x in lines.by_order.get(i, ())], parts, lines.products)[index]
            for pid, q in share.items():
                qty[pid] = qty.get(pid, 0.0) + q
        items = [(lines.products.get(pid) or Product(pid, '', '', '', 0.0, None), q)
                 for pid, q in qty.items() if abs(q) > 1e-9]
        rows = [_row(p, round(q, 4), p.id in lines.products) for p, q in sorted(items, key=lambda x: _code_key(x[0]))]
        trips.append({'id': tr['id'], 'no': no, 'loading_start': tr['loading_start'], 'depart': tr['depart'],
                      'return': tr['return'], 'stops': len(tr['stops']), 'orders': orders, 'invoiced': invoiced,
                      'mixed': mixed, 'split': split, 'rows': rows, 'kg': round(math.fsum(q * p.kg for p, q in items)),
                      'unknown': sum(1 for p, _ in items if p.id not in lines.products),
                      'basis': [[s['customer_id'], s['share'], sorted(o['isn'] for o in s['orders'])] for s in tr['stops']]})
    return {'car_code': car_code, 'name': truck.get('name'), 'trips': trips}

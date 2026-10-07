# -*- coding: utf-8 -*-
"""Баланс тары магазинов (ответ владельца №87 п. 9): сколько тары каждого вида сейчас у магазина.

Тары в ERP нет (ни остатков, ни движений) — баланс считается по терминалам «Առաքիչ» (ответ владельца: «с нуля +
начальный остаток»):
    баланс = начальный остаток (на конец дня as_of) + ушло − забрано за дни ПОСЛЕ as_of;
    начального остатка нет — ушло − забрано за все дни работы APK.
Остаток ведётся по паре (магазин, вид тары): у каждой пары свой as_of.

День — по правилу дня офиса (views.day_model, контракт §5 п. 12), как «Առաքում այսօր». По каждой показываемой точке:
- ушло (went) — tare_expected точки (тара товаров документа: erp_day.expected_tare по PRODUCTCONTAINERS) в версии /day,
  по которой записана действующая доставка точки (та же версия, что строки правила), — по статусу правила:
  · full — вся;
  · covered — сестра разделённого заказа (заказ O: стал несколькими накладными, её товар отдан по заявлениям заказа,
    paid_to правила): у неё самой — ничего, её тара — у владельца группы. Действующее заявление владельца — на заказе:
    строки и tare_expected владельца и сестёр вместе, по статусу и заявлениям владельца (заказ 6 из 10 — ушло 6, отказ —
    0); на накладной: владелец — по своему заявлению на своих строках, сестра — по долям заявлений поглощённых заказов
    на своих строках (заказ 10 из 10, затем накладная 3 из 6 — ушло 3 + 4 = 7);
  · partial и in_progress — по доставленному товару: у товара p доставлено d_p (Σ по заявлениям правила, строка — не
    больше своего количества в версии заявления) из q_p (строки точки), f_p = min(1, d_p / q_p); тара вида t =
    tare_expected[t] × Σ f_p·q_p·k_p(t) / Σ q_p·k_p(t), k_p(t) — тара t на единицу товара p (связи ERP). Ни один товар
    точки с тарой t не связан (связи ERP поменялись или ERP недоступна) — доля веса строк, как №65
    (facts.delivered_share): Σ f_p·кг_p / Σ кг_p (без весов — по штукам); такая точка — approx;
  · refused, pending — ничего: товар не ушёл.
- забрано (returned) — действующие отметки tare (views._tare: по каждой точке зоны владельца последняя отметка без
  вытесненных `supersedes`, сумма по виду) — у магазина точки. Отметка точки, которой нет в правиле даты (точка из
  снимка другой даты), — у магазина самой новой версии точки.
Количества — с точностью 0,01 (как tare_expected).

Без I/O: данные дня дают views (tare_view); здесь — правила, баланс, история магазина и проверка начальных остатков.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import merge as mg
from .erp_day import ContainerLink
from .events import TARE_RE
from .store import TareOpening

FULL = ('full',)                         # товар ушёл целиком (covered — у владельца группы, day_moves)
SHARE = ('partial', 'in_progress')       # ушла часть товара
QTY_MAX = 1e6
IMPORT_ROWS_MAX = 5000
CODE_MAX = 40
FIRST_DAY = date(2000, 1, 1)
_DMY = re.compile(r'^(\d{1,2})[./](\d{1,2})[./](\d{4})$')


@dataclass(frozen=True)
class Move:
    """Тара точки за день: что ушло магазину и что водитель забрал (вид тары → количество)."""
    day: str
    customer_id: int
    code: str
    name: str
    agent: str
    car_code: str
    doc: str
    status: str | None                   # статус правила; None — точки нет в правиле даты (только отметка тары)
    went: Mapping[str, float]
    returned: Mapping[str, float]
    approx: bool = False                 # частичная доставка посчитана по доле веса (связей тары нет)


@dataclass(frozen=True)
class DayTare:
    """Движение тары даты: точки с тарой, названия видов тары из tare_expected, отметки без магазина (lost)."""
    day: str
    moves: tuple[Move, ...]
    names: Mapping[str, str] = field(default_factory=dict)
    lost: int = 0


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else 0.0


def links_by_product(links: Iterable[ContainerLink]) -> dict[int, dict[str, float]]:
    """Связи ERP → товар → {'erp:N': тары на единицу товара} (битые связи с количеством ≤ 0 — пропуск, как
    erp_day.expected_tare)."""
    out: dict[int, dict[str, float]] = {}
    for link in links:
        if link.product_qty > 0 and link.container_qty > 0:
            per = out.setdefault(link.product_id, {})
            key = f'erp:{link.container_id}'
            per[key] = per.get(key, 0.0) + link.container_qty / link.product_qty
    return out


def expected(version: Mapping[str, Any]) -> dict[str, float]:
    """tare_expected версии точки: вид → количество (> 0)."""
    out: dict[str, float] = {}
    for t in version.get('tare_expected') or ():
        if isinstance(t, dict) and isinstance(t.get('tare_id'), str) and _num(t.get('qty')) > 0:
            out[t['tare_id']] = out.get(t['tare_id'], 0.0) + _num(t.get('qty'))
    return out


def went(status: str | None, basis: Mapping[str, Any], delivered: Mapping[int, float],
         links: Mapping[int, Mapping[str, float]]) -> tuple[dict[str, float], bool]:
    """Тара, ушедшая магазину по точке (docstring модуля): basis — версия точки правила, delivered — товар →
    доставлено по заявлениям правила. Второе значение — посчитано по доле веса (approx)."""
    exp = expected(basis)
    if status in FULL:
        return {t: round(q, 2) for t, q in exp.items()}, False
    if status not in SHARE or not exp:
        return {}, False
    own: dict[int, float] = {}
    kg: dict[int, float] = {}
    for ln in basis.get('lines') or ():
        if not isinstance(ln, dict) or not isinstance(ln.get('product_id'), int) or _num(ln.get('qty')) <= 0:
            continue
        pid = ln['product_id']
        own[pid] = own.get(pid, 0.0) + _num(ln.get('qty'))
        kg[pid] = kg.get(pid, 0.0) + max(0.0, _num(ln.get('weight_kg')))
    frac = {p: min(1.0, max(0.0, delivered.get(p, 0.0)) / q) for p, q in own.items()}
    out: dict[str, float] = {}
    approx = False
    for t, qty in exp.items():
        weight = {p: q * links.get(p, {}).get(t, 0.0) for p, q in own.items()}
        total = math.fsum(weight.values())
        if total <= 0:
            approx = True
            weight = kg if math.fsum(kg.values()) > 0 else own
            total = math.fsum(weight.values())
        share = math.fsum(frac[p] * w for p, w in weight.items()) / total if total > 0 else 0.0
        v = round(qty * share, 2)
        if v > 0:
            out[t] = v
    return out, approx


def delivered_by_product(model: Any, statements: Iterable[str]) -> dict[int, float]:
    """Доставлено по товарам по заявлениям правила (id событий delivery): строка заявления — по line_id версии точки
    события (снимок события, иначе версия для показа), не больше своего количества; неизвестная строка не считается."""
    by_id = {e['id']: e for e in model.inputs}
    out: dict[int, float] = {}
    for eid in statements:
        e = by_id.get(eid)
        if e is None:
            continue
        version = (model.versions.get(e['stop_id']) or {}).get(e.get('snapshot_id')) or model.data.get(e['stop_id']) or {}
        lines = {ln.get('line_id'): ln for ln in version.get('lines') or () if isinstance(ln, dict)}
        for item in (e.get('payload') or {}).get('lines') or ():
            ln = lines.get(item.get('line_id')) if isinstance(item, dict) else None
            if ln is None or not isinstance(ln.get('product_id'), int):
                continue
            done = max(0.0, min(_num(item.get('qty')), _num(ln.get('qty'))))
            out[ln['product_id']] = out.get(ln['product_id'], 0.0) + done
    return out


def _move(day: str, d: Mapping[str, Any], status: str | None, went_: Mapping[str, float],
          returned: Mapping[str, float], approx: bool) -> Move | None:
    customer = d.get('customer') or {}
    cid = customer.get('id')
    if not isinstance(cid, int) or isinstance(cid, bool):
        return None
    return Move(day, cid, str(customer.get('code') or ''), str(customer.get('name') or ''), str(d.get('agent_name') or ''),
                str(d.get('car_code') or ''), str(d.get('doc_number') or ''), status, dict(went_), dict(returned), approx)


def _order_share(model: Any, statements: Sequence[Mapping[str, Any]]) -> dict[int, float]:
    """Доля доставленного по товарам в заявлениях заказов: товар → min(1, доставлено / количество в строках заказа)."""
    ordered: dict[int, float] = {}
    for e in statements:
        version = (model.versions.get(e['stop_id']) or {}).get(e.get('snapshot_id')) or model.data.get(e['stop_id']) or {}
        for ln in version.get('lines') or ():
            if isinstance(ln, dict) and isinstance(ln.get('product_id'), int) and _num(ln.get('qty')) > 0:
                ordered[ln['product_id']] = ordered.get(ln['product_id'], 0.0) + _num(ln.get('qty'))
    done = delivered_by_product(model, [e['id'] for e in statements])
    return {p: min(1.0, done.get(p, 0.0) / q) for p, q in ordered.items()}


def _group(owner: Mapping[str, Any], sisters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Владелец разделённого заказа вместе с covered-сёстрами — одна «точка» для тары: строки и tare_expected всех."""
    lines = [ln for x in (owner, *sisters) for ln in x.get('lines') or ()]
    total: dict[str, float] = {}
    for x in (owner, *sisters):
        for t, q in expected(x).items():
            total[t] = total.get(t, 0.0) + q
    return {**owner, 'lines': lines, 'tare_expected': [{'tare_id': t, 'qty': q} for t, q in sorted(total.items())]}


def day_moves(day: str, model: Any, marks: Mapping[str, Sequence[Mapping[str, Any]]],
              links: Mapping[int, Mapping[str, float]], foreign: Mapping[str, Mapping[str, Any]]) -> DayTare:
    """Движение тары даты day по правилу дня model (views.DayModel): marks — действующая тара показываемых точек
    (views._tare), foreign — версии точек отметок, которых нет в правиле даты (stop_id → данные точки)."""
    known = [e for e in model.inputs if e['stop_id'] in model.data]
    stated = mg.latest_by_stop(known, 'delivery')   # версия правила точки — версия её действующей доставки (day_model)
    by_id = {e['id']: e for e in model.inputs}
    covered: dict[str, list[str]] = {}               # владелец → его covered-сёстры (заказ разделён на накладные)
    for sister, owner in sorted(model.paid_to.items()):
        covered.setdefault(owner, []).append(sister)
    names: dict[str, str] = {}
    moves: list[Move] = []
    lost = 0
    for x, v in sorted(model.views.items()):
        d = model.data[x]
        stmt = stated.get(x)
        basis = ((model.versions.get(x) or {}).get(stmt['snapshot_id']) if stmt is not None else None) or d
        for s in [x, *covered.get(x, ())]:
            for t in (basis if s == x else model.data[s]).get('tare_expected') or ():
                if isinstance(t, dict) and isinstance(t.get('tare_id'), str) and t.get('name'):
                    names[t['tare_id']] = str(t['name'])
        last = by_id.get(v.statement or '')
        split_by_invoice = x in covered and last is not None and str(last['stop_id']).startswith('S:')
        if x in covered and not split_by_invoice:   # заявления заказа — на всю группу: тара сестёр считается здесь
            basis = _group(basis, [model.data[s] for s in covered[x]])
        delivered = delivered_by_product(model, v.statements) if v.status in SHARE else {}
        w, approx = went(v.status, basis, delivered, links)
        if split_by_invoice:
            # действующее заявление — на накладной: владелец — по нему на своих строках (выше); сёстры — по долям
            # заявлений поглощённых заказов на своих строках (их товар отдан по заказу)
            share = _order_share(model, [stated[o] for o, owner in sorted(model.absorbed_by.items())
                                         if owner == x and o.startswith('O:') and o in stated])
            for s in covered[x]:
                sister = model.data[s]
                done = {ln['product_id']: share.get(ln['product_id'], 0.0) * _num(ln.get('qty'))
                        for ln in sister.get('lines') or () if isinstance(ln, dict) and isinstance(ln.get('product_id'), int)}
                sw, sa = went('partial', sister, done, links)
                w = {t: round(w.get(t, 0.0) + sw.get(t, 0.0), 2) for t in {*w, *sw}}
                approx = approx or sa
        r = {i['tare_id']: round(_num(i.get('qty')), 2) for i in marks.get(x, ()) if _num(i.get('qty')) > 0}
        if w or r:
            m = _move(day, d, v.status, w, r, approx)
            if m is None:
                lost += 1
            else:
                moves.append(m)
    for x in sorted(set(marks) - set(model.views)):
        r = {i['tare_id']: round(_num(i.get('qty')), 2) for i in marks[x] if _num(i.get('qty')) > 0}
        if not r:
            continue
        m = _move(day, foreign[x], None, {}, r, False) if x in foreign else None
        if m is None:
            lost += 1
        else:
            moves.append(m)
    return DayTare(day, tuple(moves), names, lost)


def _counted(day: str, opening: Mapping[str, Any] | None) -> bool:
    """День входит в баланс пары: начального остатка нет или день позже его as_of."""
    return opening is None or day > opening['as_of']


def balances(moves: Iterable[Move], openings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Строка на пару (магазин, вид тары): начальный остаток и его as_of, ушло и забрано за учитываемые дни, баланс;
    магазин — код, название, менеджер и машина последнего дня с тарой. Сортировка — больший долг первым."""
    opened = {(o['customer_id'], o['tare_id']): o for o in openings}
    acc: dict[tuple[int, str], dict[str, Any]] = {}
    meta: dict[int, dict[str, Any]] = {}
    for m in sorted(moves, key=lambda x: x.day):
        meta[m.customer_id] = {'code': m.code or meta.get(m.customer_id, {}).get('code', ''),
                               'name': m.name or meta.get(m.customer_id, {}).get('name', ''),
                               'agent': m.agent, 'car_code': m.car_code, 'last_day': m.day}
        for t in {*m.went, *m.returned}:
            if not _counted(m.day, opened.get((m.customer_id, t))):
                continue
            a = acc.setdefault((m.customer_id, t), {'went': [], 'returned': [], 'approx': False})
            a['went'].append(m.went.get(t, 0.0))
            a['returned'].append(m.returned.get(t, 0.0))
            a['approx'] = a['approx'] or (m.approx and t in m.went)
    for key in opened:
        acc.setdefault(key, {'went': [], 'returned': [], 'approx': False})
    rows = []
    for (cid, t), a in acc.items():
        o = opened.get((cid, t))
        info = meta.get(cid) or {'code': o['code'] if o else '', 'name': o['name'] if o else '', 'agent': '',
                                 'car_code': '', 'last_day': None}
        w, r = round(math.fsum(a['went']), 2), round(math.fsum(a['returned']), 2)
        start = o['qty'] if o else 0.0
        rows.append({'customer_id': cid, **info, 'tare_id': t, 'opening': o['qty'] if o else None,
                     'as_of': o['as_of'] if o else None, 'went': w, 'returned': r,
                     'balance': round(start + w - r, 2), 'approx': a['approx']})
    rows.sort(key=lambda x: (-x['balance'], x['code'], x['customer_id'], x['tare_id']))
    return rows


def history(moves: Iterable[Move], openings: Sequence[Mapping[str, Any]], customer_id: int) -> dict[str, Any]:
    """История магазина по дням (новые первыми): машины, документы, ушло и забрано по видам, учитывается ли день в
    балансе вида (counted) и баланс вида после дня (balance) — тот же расчёт, что balances."""
    opened = {o['tare_id']: o for o in openings if o['customer_id'] == customer_id}
    bal = {t: o['qty'] for t, o in opened.items()}
    days: dict[str, dict[str, Any]] = {}
    for m in sorted((m for m in moves if m.customer_id == customer_id), key=lambda x: (x.day, x.car_code, x.doc)):
        d = days.setdefault(m.day, {'date': m.day, 'cars': [], 'docs': [], 'went': {}, 'returned': {}, 'approx': False})
        if m.car_code and m.car_code not in d['cars']:
            d['cars'].append(m.car_code)
        if m.doc and m.doc not in d['docs']:
            d['docs'].append(m.doc)
        for t, q in m.went.items():
            d['went'][t] = round(d['went'].get(t, 0.0) + q, 2)
        for t, q in m.returned.items():
            d['returned'][t] = round(d['returned'].get(t, 0.0) + q, 2)
        d['approx'] = d['approx'] or m.approx
    out = []
    for ds in sorted(days):
        d = days[ds]
        d['counted'], d['balance'] = {}, {}
        for t in sorted({*d['went'], *d['returned']}):
            d['counted'][t] = _counted(ds, opened.get(t))
            if d['counted'][t]:
                bal[t] = round(bal.get(t, 0.0) + d['went'].get(t, 0.0) - d['returned'].get(t, 0.0), 2)
                d['balance'][t] = bal[t]
        out.append(d)
    return {'customer_id': customer_id, 'openings': sorted(opened.values(), key=lambda o: o['tare_id']),
            'balance': {t: round(q, 2) for t, q in sorted(bal.items())}, 'days': out[::-1]}


# --- начальные остатки: ввод на странице и импорт CSV/Excel ---

def parse_qty(v: Any) -> float | None:
    """Количество тары: число или строка («12», «12,5»), 0 … QTY_MAX, до 0,01; иначе None."""
    if isinstance(v, str):
        v = v.strip().replace(' ', '').replace(' ', '').replace(',', '.')
        try:
            v = float(v) if v else None
        except ValueError:
            return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= QTY_MAX:
        return None
    return round(float(v), 2)


def parse_day(v: Any, today: date) -> str | None:
    """Дата остатка: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ, не раньше 2000 года и не позже today; иначе None."""
    if not isinstance(v, str):
        return None
    s = v.strip()
    m = _DMY.match(s)
    try:
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return None
    return d.isoformat() if FIRST_DAY <= d <= today else None


def _key(name: str) -> str:
    return ' '.join(name.split()).casefold()


def resolve_kind(v: Any, kinds: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Вид тары из файла: 'erp:N' / 'custom:N' или название (без регистра и лишних пробелов) среди известных видов
    kinds (id → название). (id, None) или (None, текст ошибки)."""
    if not isinstance(v, str) or not v.strip():
        return None, 'Տարայի տեսակը նշված չէ'
    s = v.strip()
    if TARE_RE.match(s):
        return (s, None) if s in kinds else (None, 'Տարայի այդպիսի տեսակ չկա')
    found = [t for t, name in kinds.items() if _key(name) == _key(s)]
    if len(found) > 1:
        return None, 'Այդ անունով մի քանի տարա կա — նշեք կոդը (erp:N)'
    return (found[0], None) if found else (None, 'Տարայի այդպիսի տեսակ չկա')


def check_import(rows: Sequence[Any], customers: Callable[[list[str]], Mapping[str, tuple[int, str]]],
                 kinds: Mapping[str, str], today: date) -> tuple[list[TareOpening], list[dict[str, Any]]]:
    """Строки файла {code, tare, qty, as_of} (row — номер строки в файле, иначе порядковый) → начальные остатки и
    ошибки [{row, field, error}] (по-армянски). customers — код → (customer_id, название) для кодов файла. Пара
    (магазин, тара) — одна на файл. Ошибка в любой строке — импорт не применяется (решает вызывающий)."""
    errors: list[dict[str, Any]] = []
    parsed: list[tuple[int, str, str | None, float | None, str | None]] = []
    for i, raw in enumerate(rows, 1):
        row = raw if isinstance(raw, dict) else {}
        no = row.get('row') if isinstance(row.get('row'), int) and not isinstance(row.get('row'), bool) else i
        code = row.get('code')
        # код — как в файле (страница берёт столбец A текстом, «0123» остаётся «0123»); целое — его запись; иное — нет кода
        code = code.strip() if isinstance(code, str) else str(code) if isinstance(code, int) and not isinstance(code, bool) \
            else ''
        if not code or len(code) > CODE_MAX:
            errors.append({'row': no, 'field': 'code', 'error': 'Խանութի կոդը նշված չէ'})
        tare_id, err = resolve_kind(row.get('tare'), kinds)
        if err:
            errors.append({'row': no, 'field': 'tare', 'error': err})
        qty = parse_qty(row.get('qty'))
        if qty is None:
            errors.append({'row': no, 'field': 'qty', 'error': f'Քանակը՝ թիվ 0-ից {QTY_MAX:,.0f}'.replace(',', ' ')})
        as_of = parse_day(row.get('as_of'), today)
        if as_of is None:
            errors.append({'row': no, 'field': 'as_of', 'error': 'Ամսաթիվը՝ ՕՕ.ԱԱ.ՏՏՏՏ, ոչ ուշ քան այսօր'})
        parsed.append((no, code, tare_id, qty, as_of))
    found = customers(sorted({code for _, code, *_ in parsed if code})) if parsed else {}
    out: list[TareOpening] = []
    seen: dict[tuple[int, str], int] = {}
    for no, code, tare_id, qty, as_of in parsed:
        hit = found.get(code) if code else None
        if code and hit is None:
            errors.append({'row': no, 'field': 'code', 'error': 'Այդ կոդով խանութ չի գտնվել'})
        if hit is None or tare_id is None or qty is None or as_of is None:
            continue
        if (hit[0], tare_id) in seen:
            errors.append({'row': no, 'field': 'tare', 'error': f'Կրկնվում է {seen[(hit[0], tare_id)]}-րդ տողի հետ'})
            continue
        seen[(hit[0], tare_id)] = no
        out.append(TareOpening(hit[0], tare_id, qty, as_of, code, hit[1]))
    errors.sort(key=lambda e: e['row'])
    return out, errors

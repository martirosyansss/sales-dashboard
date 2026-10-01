# -*- coding: utf-8 -*-
"""POST /events (контракт §2): приём пачки событий терминала.

Инварианты:
- идемпотентность: id (uuid, хранится в нижнем регистре) уже принят → `duplicates`, тело не сравнивается
  и ничего не меняется; повтор внутри одной пачки — тоже `duplicates`;
- пачка — одна транзакция (Store.batch): проверки видят события этой же пачки (payment → его отмена,
  scan → scan_cancel), а повтор той же пачки параллельно ждёт и получает `duplicates`;
- `rejected` — только нарушения формы и правил таблицы контракта (тип, дата, момент с зоной, qty < 0 или
  сверх накладной, amount ≤ 0, неизвестная строка накладной…); отказ хранится в rejected_events (не больше
  store.REJECTED_PER_DAY на терминал в день — дальше только в ответе) — его видят /status и офис;
- версии точки (контракт §5 п. 4): терминал работает офлайн по версии /day, которую успел получить, а офис мог
  изменить накладную. Поэтому строка ищется во ВСЕХ сохранённых версиях точки, и qty отклоняется, только если
  больше максимума строки по всем версиям; больше действующей версии, но не больше прежней — принимается с
  флагом `qty_over_invoice` (строка, убранная из новой версии, — тоже: в действующей её qty = 0);
- мягкие правила v1.1: нет причины при «частично»/«отказ» — флаг `no_reason` (§5 п. 5); `date` дальше чем на
  2 дня от даты `at` (Ереван) — флаг `date_suspicious` (§5 п. 6);
- правила §3 (обязательный скан маркировки, номер чека ՀԴՄ, фото) сервер НЕ блокирует — событие
  принимается с флагом (данные терминала важнее), флаги видны в офисе;
- `foreign` — точка в выдаче /day другой машины или даты; `unknown_stop` — точку /day не выдавал никому
  (проверить qty нечем — принимается как есть); snapshot_id события — снимок действующей версии точки, по
  которой оно проверено (цены для «Գումար»);
- повторная delivery/tare по точке не удаляет прежние — «действующая» считается при чтении: последняя
  по моменту терминала (at_utc), затем по received_at и id (effective()).
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from . import clock
from .store import REJECTED_PER_DAY, EventTx, Store

logger = logging.getLogger(__name__)

MAX_BATCH = 200
MAX_PAYLOAD_BYTES = 20_000
EVENT_TYPES = ('delivery', 'payment', 'tare', 'return', 'scan', 'scan_cancel', 'unreadable', 'arrived', 'day_closed')
STOPLESS_TYPES = ('day_closed', 'scan_cancel')   # stop_id не обязателен
MONEY_MAX = 1e9
QTY_MAX = 1e6
DATE_SUSPICIOUS_DAYS = 2
EPS = 1e-9

UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
STOP_RE = re.compile(r'^[SO]:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$')
TARE_RE = re.compile(r'^(erp|custom):\d{1,9}$')


class Reject(Exception):
    """Событие не принято: текст — по-армянски, для «Конца дня» на терминале."""


@dataclass(frozen=True)
class Who:
    """Кто прислал: терминал (и его машина) и водитель сессии."""
    terminal_id: int
    car_code: str
    driver_id: int
    driver_name: str


@dataclass
class Result:
    accepted: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)

    def json(self) -> dict[str, Any]:
        return {'accepted': self.accepted, 'duplicates': self.duplicates, 'rejected': self.rejected}


# --- проверки значений ---

def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _text(v: Any, limit: int, what: str, required: bool = False) -> str | None:
    """Строка до limit символов; None и пустая — None (или Reject, если required)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise Reject(f'{what}՝ պարտադիր է')
        return None
    if not isinstance(v, str):
        raise Reject(f'{what}՝ սխալ արժեք')
    if len(v) > limit:
        raise Reject(f'{what}՝ չափազանց երկար')
    return v


def _qty(v: Any, what: str, positive: bool = False) -> float:
    q = _num(v)
    if q is None or q < 0 or q > QTY_MAX or (positive and q <= 0):
        raise Reject(f'{what}՝ սխալ քանակ')
    return q


def _uuid(v: Any, what: str) -> str:
    if not isinstance(v, str) or not UUID_RE.match(v):
        raise Reject(f'{what}՝ սխալ id')
    return v.lower()


# --- контекст точки ---

def stop_lines(data: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    """Строки версии точки по line_id."""
    if not data:
        return {}
    return {str(ln.get('line_id')): ln for ln in data.get('lines') or [] if isinstance(ln, dict)}


@dataclass(frozen=True)
class StopCtx:
    data: Mapping[str, Any] | None   # действующая версия точки из выдачи /day (своя или чужая); None — неизвестна
    foreign: bool
    snapshot_id: int | None = None
    versions: tuple[Mapping[str, Any], ...] = ()   # все сохранённые версии точки, от старой к новой

    def lines(self) -> dict[str, Mapping[str, Any]]:
        """Строки действующей версии."""
        return stop_lines(self.data)

    def any_lines(self) -> dict[str, Mapping[str, Any]]:
        """Строки всех версий (строка — из самой новой версии, где она есть)."""
        out: dict[str, Mapping[str, Any]] = {}
        for v in (*self.versions, *([self.data] if self.data else [])):
            out.update(stop_lines(v))
        return out

    def max_qty(self, line_id: str) -> float | None:
        """Наибольшее qty строки по всем версиям; строки нет ни в одной — None."""
        qtys = [float(ln.get('qty') or 0) for v in (*self.versions, *([self.data] if self.data else []))
                for lid, ln in stop_lines(v).items() if lid == line_id]
        return max(qtys) if qtys else None


def _stop_ctx(tx: EventTx, stop_id: str | None, day: str, car_code: str) -> tuple[StopCtx, list[str]]:
    """Действующая версия — последняя версия точки в снимках своей машины и даты (точка убрана из нового
    снимка — её последняя версия); своих нет — последняя чужая (`foreign`)."""
    if stop_id is None:
        return StopCtx(None, False), []
    rows = tx.stop_rows(stop_id)
    if not rows:
        return StopCtx(None, False), ['unknown_stop']
    own = [r for r in rows if r['date'] == day and r['car_code'] == car_code]
    cur = (own or rows)[-1]
    return StopCtx(cur['data'], not own, cur['snapshot_id'], tuple(r['data'] for r in rows)), ([] if own else ['foreign'])


# --- правила по типам: нарушение формы или таблицы контракта — Reject, нарушение §3 — флаг ---

def _delivery(p: Mapping[str, Any], stop: StopCtx) -> list[str]:
    raw = p.get('lines')
    if not isinstance(raw, list) or not raw or len(raw) > 500:
        raise Reject('lines՝ պետք է լինի ոչ դատարկ ցուցակ')
    invoice = stop.lines()
    seen: dict[str, float] = {}
    flags = []
    for item in raw:
        if not isinstance(item, dict):
            raise Reject('lines՝ սխալ տող')
        lid = _text(item.get('line_id'), 80, 'line_id', required=True)
        assert lid is not None
        if lid in seen:
            raise Reject(f'Տողը կրկնվում է՝ {lid}')
        q = _qty(item.get('qty'), 'qty')
        if stop.data is not None:
            top = stop.max_qty(lid)
            if top is None:
                raise Reject(f'Անհայտ տող՝ {lid}')
            if q > top + EPS:
                raise Reject('Քանակը չի կարող գերազանցել ապրանքագրի քանակը')
            if q > float((invoice.get(lid) or {}).get('qty') or 0) + EPS and 'qty_over_invoice' not in flags:
                flags.append('qty_over_invoice')   # больше действующей версии, но не больше прежней (§5 п. 4)
        seen[lid] = q
    status = delivery_status(seen, invoice) if stop.data is not None else ('refuse' if not any(seen.values()) else None)
    if stop.data is not None and set(invoice) - set(seen):
        flags.append('lines_incomplete')
    if status in ('partial', 'refuse') and not _text(p.get('reason_id'), 40, 'reason_id'):
        flags.append('no_reason')   # v1.1 §5 п. 5: принимается, офис видит
    _text(p.get('comment'), 500, 'comment')
    return flags


def delivery_status(delivered: Mapping[str, float], invoice: Mapping[str, Mapping[str, Any]]) -> str:
    """full — все строки = накладной; refuse — все 0; иначе partial. Строка, не указанная в delivery, — 0."""
    qtys = [(delivered.get(lid, 0.0), float(ln.get('qty') or 0)) for lid, ln in invoice.items()]
    if not qtys:
        return 'refuse' if not any(delivered.values()) else 'partial'
    if all(d <= 1e-9 for d, _ in qtys):
        return 'refuse'
    if all(abs(d - q) <= 1e-9 for d, q in qtys):
        return 'full'
    return 'partial'


def _scan_shortfall(tx: EventTx, day: str, stop_id: str, p: Mapping[str, Any], stop: StopCtx) -> list[str]:
    """§3: маркируемая строка с qty > 0 — сканов (sale) + «не читается» должно хватать (флаг, не отказ)."""
    invoice = stop.lines()
    for item in p.get('lines') or []:
        ln = invoice.get(str(item.get('line_id')))
        q = _num(item.get('qty')) or 0.0
        if ln and ln.get('marked') and q > 0 and tx.covered_units(day, stop_id, str(item['line_id'])) + EPS < q:
            return ['scan_short']
    return []


def _payment(tx: EventTx, ev: Mapping[str, Any], p: Mapping[str, Any], stop: StopCtx,
             who: Who) -> tuple[dict[str, Any], list[str]]:
    amount = _num(p.get('amount'))
    if amount is None or amount <= 0 or amount > MONEY_MAX:
        raise Reject('Գումարը պետք է լինի 0-ից մեծ')
    kind = p.get('kind')
    if kind not in ('invoice', 'debt'):
        raise Reject('kind՝ invoice կամ debt')
    receipt = _text(p.get('ecr_receipt'), 40, 'ecr_receipt')
    out = dict(p)
    flags: list[str] = []
    if p.get('cancel_of') is not None:
        target_id = _uuid(p.get('cancel_of'), 'cancel_of')
        target = tx.event(target_id)
        if target is None or target['type'] != 'payment' or target['payload'].get('cancel_of') is not None:
            raise Reject('Չեղարկվող վճարումը չի գտնվել')
        # деньги сверяются по водителю и дню: отменить можно только свой платёж той же точки и даты
        if (target['driver_id'], target['date'], target['stop_id']) != (who.driver_id, ev['date'], ev['stop_id']):
            raise Reject('Կարելի է չեղարկել միայն նույն կետի և օրվա ձեր վճարումը')
        if _num(target['payload'].get('amount')) != amount or target['payload'].get('kind') != kind:
            raise Reject('Չեղարկման գումարը պետք է համընկնի վճարման գումարի հետ')
        if tx.payment_cancelled(target_id, ev['stop_id']):
            raise Reject('Վճարումն արդեն չեղարկված է')
        out['cancel_of'] = target_id
        return out, flags
    collect = stop.data.get('collect') if stop.data is not None else None
    if collect == 'cash_ecr' and kind == 'invoice' and not receipt:
        flags.append('no_ecr_receipt')
    if collect == 'none' and kind == 'invoice':
        flags.append('paid_collect_none')
    return out, flags


def _tare(p: Mapping[str, Any]) -> None:
    items = p.get('items')
    if not isinstance(items, list) or len(items) > 100:
        raise Reject('items՝ պետք է լինի ցուցակ')
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('tare_id'), str) or not TARE_RE.match(item['tare_id']):
            raise Reject('tare_id՝ սխալ արժեք')
        if item['tare_id'] in seen:
            raise Reject(f'Տարան կրկնվում է՝ {item["tare_id"]}')
        seen.add(item['tare_id'])
        _qty(item.get('qty'), 'qty')


def _gs1_check_digit(body: str) -> str:
    """Контрольная цифра GS1 (mod 10) для строки цифр без неё."""
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)


def unit_gtin(gtin: str | None) -> str | None:
    """GTIN-14 штуки (индикатор 0) для GTIN-14 упаковки (индикатор 1–8); иначе None."""
    if not gtin or len(gtin) != 14 or not gtin.isdigit() or gtin[0] not in '12345678':
        return None
    body = '0' + gtin[1:13]
    return body + _gs1_check_digit(body)


def _return(p: Mapping[str, Any]) -> None:
    pid = p.get('product_id')
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid < 2 ** 31:
        raise Reject('product_id՝ սխալ արժեք')
    _qty(p.get('qty'), 'qty', positive=True)
    # причина необязательна: если офис не завёл причин возврата, терминал отправляет без неё
    _text(p.get('reason_id'), 40, 'reason_id')
    _text(p.get('comment'), 500, 'comment')


def _scan(tx: EventTx, ev: Mapping[str, Any], p: Mapping[str, Any], stop: StopCtx, who: Who,
          stop_id: str | None) -> tuple[list[str], dict[str, Any]]:
    raw = p.get('raw')
    if not isinstance(raw, str) or not raw or len(raw) > 1000:
        raise Reject('raw՝ պարտադիր է')
    kind = p.get('kind')
    if kind not in ('sale', 'return'):
        raise Reject('kind՝ sale կամ return')
    line_id = _text(p.get('line_id'), 80, 'line_id')
    gtin = _text(p.get('gtin'), 20, 'gtin')
    serial = _text(p.get('serial'), 100, 'serial')
    if not isinstance(p.get('is_group'), bool):
        raise Reject('is_group՝ true կամ false')
    units = _qty(p.get('units'), 'units', positive=True)
    flags: list[str] = []
    others = tx.scans_with_raw(raw, kind)
    repeat = any(s == stop_id and d == ev['date'] for s, d, _ in others)
    if repeat:
        flags.append('repeat')
    if any(s != stop_id or d != ev['date'] for s, d, _ in others):
        flags.append('duplicate_elsewhere')
    invoice = stop.any_lines()   # строка — во всех версиях точки (накладную могли изменить, пока терминал офлайн)
    line = invoice.get(line_id) if line_id else None
    if stop.data is not None and line_id and line is None:
        flags.append('unknown_line')
    # /day отдаёт штучные GTIN; упаковка (индикатор 1–8) сверяется по GTIN своей штуки — как в терминале
    gtin_keys = {gtin, unit_gtin(gtin)} - {None} if gtin else set()
    if line is None and gtin_keys:
        line = next((ln for ln in stop.lines().values() if gtin_keys & set(ln.get('gtins') or [])), None) \
            or next((ln for ln in invoice.values() if gtin_keys & set(ln.get('gtins') or [])), None)
        if line is not None and not line_id:
            line_id = str(line.get('line_id'))   # найдена по GTIN — скан засчитывается строке (covered_units)
    if stop.data is not None and gtin_keys and not any(gtin_keys & set(ln.get('gtins') or [])
                                                       for ln in invoice.values()):
        flags.append('gtin_not_in_invoice')
    if p['is_group'] and line is not None:
        pack = _num(line.get('pack_qty'))
        if pack is None:
            flags.append('group_no_pack')
        elif abs(pack - units) > 1e-9:
            flags.append('units_mismatch')
    customer = (stop.data or {}).get('customer') or {}
    cancel = tx.pending_cancel(ev['id'], who.driver_id)
    row = {
        'event_id': ev['id'], 'raw': raw, 'gtin': gtin, 'serial': serial, 'is_group': int(p['is_group']),
        'units': units, 'kind': kind, 'stop_id': stop_id, 'line_id': line_id,
        'customer_id': customer.get('id'), 'customer_code': customer.get('code'),
        'customer_name': customer.get('name'), 'tax_id': customer.get('tax_id'),
        'doc_number': (stop.data or {}).get('doc_number'),
        'product_id': line.get('product_id') if line else None, 'product_code': line.get('code') if line else None,
        'product_name': line.get('name') if line else None, 'date': ev['date'], 'at_device': ev['at'],
        'driver_id': who.driver_id, 'driver_name': who.driver_name, 'car_code': who.car_code,
        'counted': 0 if repeat else 1, 'duplicate_elsewhere': int('duplicate_elsewhere' in flags),
        'cancelled': int(cancel is not None), 'cancel_event_id': cancel,
    }
    return flags, row


def _arrived(p: Mapping[str, Any]) -> None:
    lat, lon = _num(p.get('lat')), _num(p.get('lon'))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise Reject('lat/lon՝ սխալ կոորդինատներ')
    if p.get('accuracy') is not None and (_num(p.get('accuracy')) is None or _num(p.get('accuracy')) < 0):
        raise Reject('accuracy՝ սխալ արժեք')


# --- одна запись ---

def _check(tx: EventTx, raw: Mapping[str, Any], event_id: str, who: Who) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Проверить событие: (строка events, строка scans или None) или Reject."""
    etype = raw.get('type')
    if etype not in EVENT_TYPES:
        raise Reject('Անհայտ իրադարձության տեսակ')
    day = clock.parse_day(raw.get('date'))
    if day is None:
        raise Reject('date՝ ՏՏՏՏ-ԱԱ-ՕՕ ձևաչափով')
    at = clock.parse_moment(raw.get('at'))
    if at is None:
        raise Reject('at՝ ISO-8601 ժամային գոտիով')
    payload = raw.get('payload')
    if not isinstance(payload, dict):
        raise Reject('payload՝ պետք է լինի օբյեկտ')
    if len(json.dumps(payload, ensure_ascii=False)) > MAX_PAYLOAD_BYTES:
        raise Reject('payload՝ չափազանց մեծ')
    stop_id = raw.get('stop_id')
    if stop_id is not None:
        if not isinstance(stop_id, str) or not STOP_RE.match(stop_id):
            raise Reject('stop_id՝ սխալ արժեք')
        stop_id = stop_id[:2] + stop_id[2:].upper()
    elif etype not in STOPLESS_TYPES:
        raise Reject('stop_id՝ պարտադիր է')
    ev = {'id': event_id, 'date': day.isoformat(), 'at': raw['at']}
    stop, flags = _stop_ctx(tx, stop_id, ev['date'], who.car_code)
    if abs((day - at.astimezone(clock.YEREVAN).date()).days) > DATE_SUSPICIOUS_DAYS:
        flags.append('date_suspicious')
    stored = dict(payload)
    scan_row = None
    if etype == 'delivery':
        flags += _delivery(payload, stop)
        if stop_id and stop.data is not None:
            flags += _scan_shortfall(tx, ev['date'], stop_id, payload, stop)
    elif etype == 'payment':
        stored, more = _payment(tx, {**ev, 'stop_id': stop_id}, payload, stop, who)
        flags += more
    elif etype == 'tare':
        _tare(payload)
    elif etype == 'return':
        _return(payload)
    elif etype == 'scan':
        more, scan_row = _scan(tx, ev, payload, stop, who, stop_id)
        flags += more
    elif etype == 'scan_cancel':
        stored['scan_event_id'] = _uuid(payload.get('scan_event_id'), 'scan_event_id')
        owner = tx.scan_driver(stored['scan_event_id'])
        if owner is not None and owner != who.driver_id:
            raise Reject('Կարելի է չեղարկել միայն ձեր սկանը')
        if not tx.cancel_scan(stored['scan_event_id'], event_id, who.driver_id):
            flags.append('unknown_scan')
    elif etype == 'unreadable':
        _text(payload.get('line_id'), 80, 'line_id', required=True)
        _qty(payload.get('qty'), 'qty', positive=True)
        _text(payload.get('reason'), 500, 'reason', required=True)
        if stop.data is not None and payload['line_id'] not in stop.any_lines():
            flags.append('unknown_line')
    elif etype == 'arrived':
        _arrived(payload)
    elif etype == 'day_closed':
        if not isinstance(payload.get('summary'), dict):
            raise Reject('summary՝ պետք է լինի օբյեկտ')
    row = {'id': event_id, 'terminal_id': who.terminal_id, 'driver_id': who.driver_id, 'car_code': who.car_code,
           'date': ev['date'], 'stop_id': stop_id, 'type': etype, 'at_device': raw['at'], 'at_utc': clock.utc_key(at),
           'received_at': clock.iso(clock.now()), 'payload': stored, 'flags': flags, 'snapshot_id': stop.snapshot_id}
    return row, scan_row


def ingest(store: Store, who: Who, events: Sequence[Any]) -> Result:
    """Принять пачку (≤ MAX_BATCH — проверяет вызывающий). Порядок в ответе — порядок пачки."""
    result = Result()
    with store.batch() as conn:
        tx = EventTx(conn)
        rejected_room: int | None = None   # сколько отказов ещё можно сохранить сегодня (считается при первом)
        for raw in events:
            raw_id = raw.get('id') if isinstance(raw, dict) else None
            if not isinstance(raw_id, str) or not UUID_RE.match(raw_id):
                result.rejected.append({'id': str(raw_id)[:64] if raw_id is not None else '',
                                        'error': 'bad_request', 'message': 'id՝ պետք է լինի uuid'})
                continue
            event_id = raw_id.lower()
            if tx.accepted(event_id):
                result.duplicates.append(raw_id)
                continue
            try:
                try:
                    row, scan_row = _check(tx, raw, event_id, who)
                except (TypeError, ValueError, OverflowError, KeyError) as e:   # странное значение — отказ
                    raise Reject('Սխալ տվյալներ') from e                         # события, а не 500 на всю пачку
            except Reject as e:
                if rejected_room is None:
                    rejected_room = REJECTED_PER_DAY - tx.rejected_today(who.terminal_id)
                if rejected_room > 0:
                    rejected_room -= 1
                    tx.insert_rejected(event_id, who.terminal_id, who.driver_id,
                                       d.isoformat() if (d := clock.parse_day(raw.get('date'))) else None,
                                       raw.get('type') if isinstance(raw.get('type'), str) else None,
                                       'bad_request', str(e), json.dumps(raw, ensure_ascii=False, default=str))
                elif rejected_room == 0:
                    rejected_room = -1
                    logger.warning('[Courier] Терминал %s: предел %s отказов за день — дальше не сохраняются',
                                   who.terminal_id, REJECTED_PER_DAY)
                result.rejected.append({'id': raw_id, 'error': 'bad_request', 'message': str(e)})
                continue
            if not tx.insert_event(row):
                result.duplicates.append(raw_id)
                continue
            if scan_row is not None:
                tx.insert_scan(scan_row)
            result.accepted.append(raw_id)
    return result


# --- чтение: действующие события ---

def effective(events: Iterable[Mapping[str, Any]], etype: str) -> dict[str, Mapping[str, Any]]:
    """Последнее событие типа по точке (delivery, tare заменяют прежние): по at_utc, received_at, id."""
    out: dict[str, Mapping[str, Any]] = {}
    for e in sorted((e for e in events if e['type'] == etype and e['stop_id']),
                    key=lambda e: (e['at_utc'], e['received_at'], e['id'])):
        out[e['stop_id']] = e
    return out


def payments_total(events: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Деньги по событиям payment: {'invoice': …, 'debt': …}. Отмена (cancel_of) вычитает свою сумму.
    Инвариант: отмена принимается, только если отменяемый платёж есть, той же суммы и вида и ещё не отменён
    (events._payment) — поэтому сумма по виду не уходит ниже нуля."""
    total = {'invoice': 0.0, 'debt': 0.0}
    for e in events:
        if e['type'] != 'payment':
            continue
        p = e['payload']
        amount = _num(p.get('amount')) or 0.0
        kind = p.get('kind') if p.get('kind') in total else None
        if kind is None:
            continue
        total[kind] += -amount if p.get('cancel_of') else amount
    return {k: round(v, 2) for k, v in total.items()}


def delivery_amount(delivery: Mapping[str, Any], versions: Sequence[Mapping[str, Any]]) -> float:
    """Сколько стоит доставленное (для «Գումար»): Σ qty × цена по строкам delivery. Цена — из версии точки, по
    которой доставка проверена (snapshot_id события); строки там нет — из самой новой версии, где она есть;
    неизвестная строка — 0. Отказ (все qty 0) — 0. versions — store.stop_versions() этой точки."""
    by_snapshot = {v['snapshot_id']: v['data'] for v in versions}
    base = stop_lines(by_snapshot.get(delivery.get('snapshot_id')))
    known: dict[str, Mapping[str, Any]] = {}
    for v in versions:
        known.update(stop_lines(v['data']))
    total = 0.0
    for item in delivery['payload'].get('lines') or []:
        if not isinstance(item, dict):
            continue
        lid = str(item.get('line_id'))
        line = base.get(lid) or known.get(lid)
        total += (_num(item.get('qty')) or 0.0) * ((_num(line.get('price')) or 0.0) if line else 0.0)
    return round(total, 2)

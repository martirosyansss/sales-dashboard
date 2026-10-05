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
  изменить накладную. Поэтому строка ищется среди строк ВСЕХ версий точки — по line_max (наибольшее qty строки,
  не удаляется вместе со старыми снимками, §5 п. 13), и qty отклоняется, только если больше этого максимума;
  больше действующей версии, но не больше прежней — принимается с флагом `qty_over_invoice` (строка, убранная из
  новой версии, — тоже: в действующей её qty = 0);
- мягкие правила v1.1: нет причины при «частично»/«отказ» — флаг `no_reason` (§5 п. 5); `date` дальше чем на
  2 дня от даты `at` (Ереван) — флаг `date_suspicious` (§5 п. 6);
- правила §3 (обязательный скан маркировки, номер чека ՀԴՄ, фото) сервер НЕ блокирует — событие
  принимается с флагом (данные терминала важнее), флаги видны в офисе;
- `foreign` — точка в выдаче /day другой машины или даты; `unknown_stop` — точку /day не выдавал никому
  (проверить qty нечем — принимается как есть); snapshot_id события — снимок версии точки, по которой событие
  оценивается: версия /day, которую видел водитель (`day_version` delivery и tare, §5 п. 16: снимок той же машины и
  даты с этой версией, где есть точка), иначе действующая при получении. Такой снимок хранится всегда
  (store._purge_snapshots), офис берёт из него строки, цены и collect заявления (views.day_model). Проверка qty —
  по line_max, как раньше; флаг `qty_over_invoice` — от действующей версии (офис изменил накладную), статус для
  `no_reason` и `lines_incomplete` — от версии водителя;
- `supersedes` (§5 п. 14, delivery и tare): строка id прежнего события, до SUPERSEDES_MAX символов; может ссылаться
  на ещё не полученное событие; хранится в нижнем регистре, как id событий. Цикл (цепочка по уже принятым
  событиям того же типа той же точки возвращается к самому событию, §5 п. 18) — отказ;
- повторная delivery/tare по точке не удаляет прежние — «действующая» считается при чтении по правилу §5 п. 12
  (merge: последняя по at, затем по id, без вытесненных `supersedes`);
- `track` (v1.3 §7 п. 1, без stop_id): 0–TRACK_MAX_POINTS точек; точка с ошибкой (поля, вне Армении, не по
  возрастанию at, at позже «сейчас + сутки» или старше срока хранения) отбрасывается, остальные принимаются; число
  отброшенных по причинам — в payload события и в журнале; нет ни одной годной точки и нет `device` хотя бы с одним верным полем — отказ. Точки —
  в track_points (повтор того же at той же машины не пишется); в events — только счётчики (points, kept, new, dropped)
  и `device`. Payload трека — до MAX_TRACK_PAYLOAD_BYTES (100 точек с полной точностью double ≈ 19 КБ: у обычного
  предела нет запаса). Приём трека раз в день удаляет трек старше store.TRACK_KEEP_DAYS;
- `track.device` (№76, APK 2.2.0, необязательно): состояние терминала — battery 0…100, charging, gps (on | off |
  no_permission), net (wifi | cell | none), app (версия APK). Неверное или неизвестное значение поля — null, лишние
  ключи не хранятся, не объект — `device` нет (событие от этого не отклоняется: состояние — справка для офиса). Пустой
  `points: []` с верным `device` — сигнал «на связи» без GPS-фикса (GPS выключен, нет разрешения, нет спутников);
- `refuel` (§7 п. 2, без stop_id): литры, одометр (целое), «до полного бака», сумма, место; исправление — `supersedes`
  (как у delivery; цикл — отказ). Момент заправки — момент исходной заправки цепочки исправлений (исправление пришло
  позже — заправка была тогда; исходной ещё нет — свой). Флаг `odometer_suspicious` — одометр новой заправки не входит
  в самую длинную согласованную цепочку заправок машины (learning.odometer_plausible: не убывает, прирост не больше
  learning.REFUEL_KM_PER_DAY км за сутки); заправка принимается. Флаг ставится один раз при приёме и потом не меняется — офис и
  обучение пересчитывают правило сами по всем действующим заправкам машины (опоздавшее событие или исправление меняют
  их вывод, а не сохранённый флаг);
- `helper_id` (v1.4 §8, необязательное поле события): второй человек в машине. Кто был помощником, решает терминал в
  момент события; сервер только проверяет подтверждение PIN на этом терминале (_helper). Не подтверждён — событие
  принимается без помощника с флагом `helper_unconfirmed`. Деньги, сканы и их отмена — по-прежнему по водителю сессии.
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from route_optimizer.geo import is_valid_point
from route_optimizer.learning import REFUEL_WINDOW_DAYS, REFUEL_WINDOW_MAX, odometer_plausible

from . import clock
from .merge import statement_status
from .store import REJECTED_PER_DAY, TRACK_KEEP_DAYS, EventTx, Store

logger = logging.getLogger(__name__)

MAX_BATCH = 200
SQLITE_INT_MAX = 2 ** 63 - 1   # helper_id больше — не id человека (и не влез бы в запрос SQLite)
MAX_PAYLOAD_BYTES = 20_000
MAX_TRACK_PAYLOAD_BYTES = 40_000   # track: 100 точек с полной точностью double ≈ 19 КБ — запас вдвое (§7 п. 1)
EVENT_TYPES = ('delivery', 'payment', 'tare', 'return', 'scan', 'scan_cancel', 'unreadable', 'arrived', 'day_closed',
               'geo_suggest', 'track', 'refuel')
STOPLESS_TYPES = ('day_closed', 'scan_cancel', 'track', 'refuel')   # stop_id не обязателен
SUPERSEDABLE = ('delivery', 'tare', 'refuel')   # `supersedes` — исправление прежнего события того же типа
MONEY_MAX = 1e9
QTY_MAX = 1e6
DATE_SUSPICIOUS_DAYS = 2
SUGGEST_MAX_ACCURACY_M = 100.0   # geo_suggest: точность обязательна и не хуже 100 м
SUGGEST_NOTE_MAX = 200
SUPERSEDES_MAX = 64
DAY_VERSION_MAX = 64
EPS = 1e-9
TRACK_MAX_POINTS = 100
TRACK_MAX_ACC_M = 200.0        # acc точки трека: 0 < acc ≤ 200 м
TRACK_MAX_SPEED_MS = 60.0      # spd: 0…60 м/с или null
HEARTBEAT_GAP = timedelta(seconds=20)   # пустой heartbeat трека чаще — принимается без записи (№76)
TRACK_FUTURE = timedelta(days=1)   # точка позже «сейчас + сутки» — часы терминала сбиты
DEVICE_GPS = ('on', 'off', 'no_permission')    # track.device.gps (№76)
DEVICE_NET = ('wifi', 'cell', 'none')           # track.device.net
DEVICE_APP_RE = re.compile(r'^[0-9A-Za-z.+-]{1,20}$')   # track.device.app — версия APK («2.2.0»)
REFUEL_MAX_LITERS = 400.0
REFUEL_MAX_ODOMETER = 2_000_000

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
    snapshot_id: int | None = None   # снимок версии, по которой событие оценивается (seen, иначе действующей)
    versions: tuple[Mapping[str, Any], ...] = ()   # сохранённые версии точки, от старой к новой
    line_max: Mapping[str, tuple[float, int | None]] = field(default_factory=dict)   # строки всех версий (§5 п. 13)
    seen: Mapping[str, Any] | None = None   # версия, которую видел водитель (day_version, §5 п. 16); None — не пришла

    @property
    def known(self) -> bool:
        """Точку выдавал /day: строки проверяются (по line_max — и строки удалённых версий)."""
        return self.data is not None or bool(self.line_max)

    def lines(self) -> dict[str, Mapping[str, Any]]:
        """Строки действующей версии."""
        return stop_lines(self.data)

    def any_lines(self) -> dict[str, Mapping[str, Any]]:
        """Строки сохранённых версий (строка — из самой новой версии, где она есть)."""
        out: dict[str, Mapping[str, Any]] = {}
        for v in (*self.versions, *([self.data] if self.data else [])):
            out.update(stop_lines(v))
        return out


def _stop_ctx(tx: EventTx, stop_id: str | None, day: str, car_code: str,
              day_version: str | None = None) -> tuple[StopCtx, list[str]]:
    """Действующая версия — последняя версия точки в снимках своей машины и даты (точка убрана из нового
    снимка — её последняя версия); своих нет — последняя чужая (`foreign`). Версий не осталось (удалены вместе со
    старыми снимками), а строки известны (line_max) — точка известна, проверка qty та же. day_version — версия /day,
    которую видел водитель (§5 п. 16): самый новый снимок своей машины и даты с этой версией, где есть точка (нет
    такого — seen None, событие оценивается по действующей версии)."""
    if stop_id is None:
        return StopCtx(None, False), []
    rows, lmax = tx.stop_rows(stop_id), tx.line_max(stop_id)
    if not rows:
        return StopCtx(None, False, line_max=lmax), ([] if lmax else ['unknown_stop'])
    own = [r for r in rows if r['date'] == day and r['car_code'] == car_code]
    cur = (own or rows)[-1]
    seen = next((r for r in reversed(own) if r['version'] == day_version), None) if day_version is not None else None
    return (StopCtx(cur['data'], not own, (seen or cur)['snapshot_id'], tuple(r['data'] for r in rows), lmax,
                    seen['data'] if seen is not None else None),
            [] if own else ['foreign'])


# --- правила по типам: нарушение формы или таблицы контракта — Reject, нарушение §3 — флаг ---

def _supersedes_cycle(tx: EventTx, event_id: str, etype: str, stop_id: str | None, target: str) -> bool:
    """§5 п. 18: цепочка `supersedes` от target по уже принятым событиям (и событиям этой же пачки) того же типа той
    же точки возвращается к event_id. Неизвестное событие (ещё не получено) или событие другого типа или точки —
    конец цепочки: вытеснение действует только внутри (тип, точка), там же и цикл."""
    seen: set[str] = set()
    while target not in seen:
        if target == event_id:
            return True
        seen.add(target)
        e = tx.event(target)
        nxt = e['payload'].get('supersedes') if e is not None else None
        if e is None or e['type'] != etype or e['stop_id'] != stop_id or not isinstance(nxt, str):
            return False
        target = nxt.lower()
    return False


def _supersedes(p: Mapping[str, Any], event_id: str) -> str | None:
    """§5 п. 14: id прежнего события того же типа той же точки (может быть ещё не получено) — строка до
    SUPERSEDES_MAX символов, не само событие; хранится в нижнем регистре (id событий хранятся так же)."""
    v = p.get('supersedes')
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > SUPERSEDES_MAX:
        raise Reject('supersedes՝ սխալ արժեք')
    if v.lower() == event_id:
        raise Reject('supersedes՝ չի կարող հղվել նույն իրադարձությանը')
    return v.lower()


def _delivery(p: Mapping[str, Any], stop: StopCtx) -> list[str]:
    raw = p.get('lines')
    if not isinstance(raw, list) or not raw or len(raw) > 500:
        raise Reject('lines՝ պետք է լինի ոչ դատարկ ցուցակ')
    invoice = stop.lines()
    basis = stop_lines(stop.seen) if stop.seen is not None else invoice   # что видел водитель (§5 п. 16)
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
        if stop.known:
            top = stop.line_max.get(lid)
            if top is None:
                raise Reject(f'Անհայտ տող՝ {lid}')
            if q > top[0] + EPS:
                raise Reject('Քանակը չի կարող գերազանցել ապրանքագրի քանակը')
            if stop.data is not None and q > float((invoice.get(lid) or {}).get('qty') or 0) + EPS \
                    and 'qty_over_invoice' not in flags:
                flags.append('qty_over_invoice')   # больше действующей версии, но не больше прежней (§5 п. 4)
        seen[lid] = q
    if stop.data is not None:
        status = statement_status(seen, {lid: _num(ln.get('qty')) or 0.0 for lid, ln in basis.items()})
    else:
        status = 'refused' if not any(seen.values()) else None
    if stop.data is not None and set(basis) - set(seen):
        flags.append('lines_incomplete')
    if status in ('partial', 'refused') and not _text(p.get('reason_id'), 40, 'reason_id'):
        flags.append('no_reason')   # v1.1 §5 п. 5: принимается, офис видит
    _text(p.get('comment'), 500, 'comment')
    return flags


def _scan_shortfall(tx: EventTx, day: str, stop_id: str, p: Mapping[str, Any], stop: StopCtx) -> list[str]:
    """§3: маркируемый товар с qty > 0 — сканов (sale) + «не читается» должно хватать (флаг, не отказ). Сравнение —
    по товару на всю доставку: нужно Σ qty строк этого товара, закрыто — сканы и «не читается» этого товара по точке
    (на какую строку ни отсканировано). Накладная S:, сделанная из заказов (replaces), засчитывает и сканы этого
    товара, сделанные по заказам O:."""
    invoice = stop.lines()
    replaced = [o for o in (stop.data or {}).get('replaces') or [] if isinstance(o, str)]
    need: dict[int, float] = {}
    for item in p.get('lines') or []:
        ln = invoice.get(str(item.get('line_id')))
        q = _num(item.get('qty')) or 0.0
        pid = ln.get('product_id') if ln else None
        if ln and ln.get('marked') and q > 0 and isinstance(pid, int) and not isinstance(pid, bool):
            need[pid] = need.get(pid, 0.0) + q
    for pid, q in sorted(need.items()):
        covered = tx.covered_by_product(day, [stop_id], pid)
        if covered + EPS < q and replaced:
            covered += tx.covered_by_product(day, replaced, pid)
        if covered + EPS < q:
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
    """Тара точки (заменяет прежнюю; исправление — `supersedes`, проверяет _check)."""
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
    if stop.known and line_id and line is None and line_id not in stop.line_max:
        flags.append('unknown_line')
    # Конкретный GTIN из справочника или базовый GTIN структурной упаковки — как в терминале.
    gtin_keys = {gtin, unit_gtin(gtin)} - {None} if gtin else set()
    def identifiers(ln: Mapping[str, Any]) -> set[str]:
        return set(ln.get('gtins') or []) | set(ln.get('gtin_units') or {})
    if line is None and gtin_keys:
        line = next((ln for ln in stop.lines().values() if gtin_keys & identifiers(ln)), None) \
            or next((ln for ln in invoice.values() if gtin_keys & identifiers(ln)), None)
        if line is not None and not line_id:
            line_id = str(line.get('line_id'))   # найдена по GTIN — скан засчитывается строке и её товару
    if stop.data is not None and gtin_keys and not any(gtin_keys & identifiers(ln)
                                                       for ln in invoice.values()):
        flags.append('gtin_not_in_invoice')
    quantities = (line or {}).get('gtin_units') or {}
    if gtin in quantities:
        expected = _num(quantities[gtin])
        if expected is None or expected <= 0 or abs(expected - units) > EPS:
            flags.append('units_mismatch')
    elif p['is_group'] and line is not None:
        pack = _num(line.get('pack_qty'))
        if pack is None:
            flags.append('group_no_pack')
        elif abs(pack - units) > 1e-9:
            flags.append('units_mismatch')
    customer = (stop.data or {}).get('customer') or {}
    cancel = tx.pending_cancel(ev['id'], who.driver_id)
    # товар скана — для «сканов хватает» по товару (_scan_shortfall); строка удалённой версии — по line_max
    product_id = line.get('product_id') if line is not None else stop.line_max.get(line_id or '', (0.0, None))[1]
    row = {
        'event_id': ev['id'], 'raw': raw, 'gtin': gtin, 'serial': serial, 'is_group': int(p['is_group']),
        'units': units, 'kind': kind, 'stop_id': stop_id, 'line_id': line_id,
        'customer_id': customer.get('id'), 'customer_code': customer.get('code'),
        'customer_name': customer.get('name'), 'tax_id': customer.get('tax_id'),
        'doc_number': (stop.data or {}).get('doc_number'),
        'product_id': product_id, 'product_code': line.get('code') if line else None,
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


def _geo_suggest(p: Mapping[str, Any]) -> None:
    """Предложение водителя «точка неверная — здесь» (контракт v1.2): как arrived, но точка в Армении, точность
    обязательна (0 < accuracy ≤ SUGGEST_MAX_ACCURACY_M), комментарий — до SUGGEST_NOTE_MAX символов."""
    _arrived(p)
    if not is_valid_point(p.get('lat'), p.get('lon')):
        raise Reject('Կետը Հայաստանից դուրս է')
    accuracy = _num(p.get('accuracy'))
    if accuracy is None or not 0 < accuracy <= SUGGEST_MAX_ACCURACY_M:
        raise Reject(f'accuracy՝ պարտադիր է, 0-ից մինչև {SUGGEST_MAX_ACCURACY_M:g} մ')
    _text(p.get('note'), SUGGEST_NOTE_MAX, 'note')


TrackPoint = tuple[int, float, float, float, 'float | None', 'float | None']   # (at_ms, lat, lon, acc, spd, brg)


def _track_point(raw: Any, now: datetime) -> tuple[TrackPoint | None, str | None]:
    """Точка трека → ((at_ms, lat, lon, acc, spd, brg), None) или (None, причина отказа точки)."""
    if not isinstance(raw, dict):
        return None, 'bad'
    at = clock.parse_moment(raw.get('at'))
    if at is None:
        return None, 'at'
    lat, lon = _num(raw.get('lat')), _num(raw.get('lon'))
    if lat is None or lon is None or not is_valid_point(lat, lon):
        return None, 'place'
    acc = _num(raw.get('acc'))
    if acc is None or not 0 < acc <= TRACK_MAX_ACC_M:
        return None, 'acc'
    spd, brg = raw.get('spd'), raw.get('brg')
    if spd is not None and ((spd := _num(spd)) is None or not 0 <= spd <= TRACK_MAX_SPEED_MS):
        return None, 'spd'
    if brg is not None and ((brg := _num(brg)) is None or not 0 <= brg <= 360):
        return None, 'brg'
    if at > now + TRACK_FUTURE:
        return None, 'future'
    if at < now - timedelta(days=TRACK_KEEP_DAYS):
        return None, 'old'
    return (round(at.timestamp() * 1000), lat, lon, acc, spd, brg), None


def track_device(raw: Any) -> dict[str, Any] | None:
    """№76: состояние терминала из track.device → {battery, charging, gps, net, app}; неверное значение поля — None,
    лишние ключи отбрасываются; не объект — None (состояния нет)."""
    if not isinstance(raw, dict):
        return None
    battery = _num(raw.get('battery'))
    charging, gps, net, app = raw.get('charging'), raw.get('gps'), raw.get('net'), raw.get('app')
    return {'battery': round(battery) if battery is not None and 0 <= battery <= 100 else None,
            'charging': charging if isinstance(charging, bool) else None,
            'gps': gps if gps in DEVICE_GPS else None,
            'net': net if net in DEVICE_NET else None,
            'app': app if isinstance(app, str) and DEVICE_APP_RE.match(app) else None}


def track_points(p: Mapping[str, Any], now: datetime) -> tuple[list[TrackPoint], dict[str, int]]:
    """§7 п. 1: точки события track → (годные точки по возрастанию at, {причина: отброшено}). Точка не позже
    предыдущей годной отбрасывается ('duplicate' — тот же at, 'order' — раньше). Нет списка из 0–TRACK_MAX_POINTS
    точек — Reject; нет ни одной годной точки и нет состояния терминала (`device`, №76, хотя бы одно верное поле) — Reject. Пустой список
    (APK 2.2.0 без GPS-фикса) — принимается только с таким device."""
    raw = p.get('points')
    if not isinstance(raw, list) or len(raw) > TRACK_MAX_POINTS:
        raise Reject(f'points՝ 0-ից {TRACK_MAX_POINTS} կետ')
    keep: list[TrackPoint] = []
    dropped: Counter[str] = Counter()
    for item in raw:
        point, why = _track_point(item, now)
        if point is not None and keep and point[0] <= keep[-1][0]:
            point, why = None, 'duplicate' if point[0] == keep[-1][0] else 'order'
        if point is None:
            dropped[why or 'bad'] += 1
        else:
            keep.append(point)
    if not keep and not any(v is not None for v in (track_device(p.get('device')) or {}).values()):
        raise Reject('Ոչ մի ճիշտ GPS կետ')   # пустой/негодный трек — только с проверенным device (№76, ревью)
    return keep, dict(sorted(dropped.items()))


def _refuel(tx: EventTx, p: Mapping[str, Any], at: datetime, car_code: str, supersedes: str | None,
            event_id: str) -> list[str]:
    """§7 п. 2: заправка. Нарушение формы — Reject; одометр вне самой длинной согласованной цепочки действующих заправок
    машины вместе с этой (odometer_plausible; момент исправления — момент исходной заправки) — флаг. Цепочка — по
    заправкам машины в окне ± REFUEL_WINDOW_DAYS от этой и не больше REFUEL_WINDOW_MAX (время приёма ограничено)."""
    liters = _num(p.get('liters'))
    if liters is None or not 0 < liters <= REFUEL_MAX_LITERS:
        raise Reject(f'liters՝ 0-ից մինչև {REFUEL_MAX_LITERS:g}')
    odo = _num(p.get('odometer_km'))
    if odo is None or odo != int(odo) or not 0 <= odo <= REFUEL_MAX_ODOMETER:
        raise Reject('odometer_km՝ ամբողջ թիվ 0-ից մինչև 2 000 000')
    if 'full_tank' in p and not isinstance(p.get('full_tank'), bool):
        raise Reject('full_tank՝ true կամ false')
    amount = p.get('amount_amd')
    if amount is not None and ((amount := _num(amount)) is None or not 0 <= amount <= MONEY_MAX):
        raise Reject('amount_amd՝ սխալ գումար')
    lat, lon = p.get('lat'), p.get('lon')
    if (lat is None) != (lon is None):
        raise Reject('lat/lon՝ երկուսն էլ կամ ոչ մեկը')
    if lat is not None:
        _arrived({'lat': lat, 'lon': lon})
    own = tx.car_refuels(car_code, clock.utc_key(at - timedelta(days=REFUEL_WINDOW_DAYS + 30)))
    by_id = {r['id']: r for r in own}

    def moment(at_utc: str, target: Any) -> datetime:
        """Момент заправки: исходной в цепочке исправлений (известной серверу), иначе свой."""
        seen: set[str] = set()
        while isinstance(target, str) and target in by_id and target not in seen:
            seen.add(target)
            at_utc, target = by_id[target]['at_utc'], by_id[target]['payload'].get('supersedes')
        return datetime.fromisoformat(at_utc)

    gone = {r['payload'].get('supersedes') for r in own} | {supersedes}
    items = sorted([(moment(r['at_utc'], r['payload'].get('supersedes')), r['id'], r['payload'].get('odometer_km'))
                    for r in own if r['id'] not in gone]
                   + [(moment(clock.utc_key(at), supersedes), event_id, odo)], key=lambda x: (x[0], x[1]))
    me = next(t for t, eid, _ in items if eid == event_id)
    items = [x for x in items if abs(x[0] - me) <= timedelta(days=REFUEL_WINDOW_DAYS)]
    k = next(i for i, x in enumerate(items) if x[1] == event_id)
    start = max(0, min(k - REFUEL_WINDOW_MAX // 2, len(items) - REFUEL_WINDOW_MAX))
    items = items[start:start + REFUEL_WINDOW_MAX]
    plausible = odometer_plausible([(t, o) for t, _, o in items])
    return [] if next(ok for (_, eid, _), ok in zip(items, plausible) if eid == event_id) else ['odometer_suspicious']


# --- одна запись ---

def _check(tx: EventTx, raw: Mapping[str, Any], event_id: str, who: Who
           ) -> tuple[dict[str, Any], dict[str, Any] | None, list[TrackPoint] | None]:
    """Проверить событие: (строка events, строка scans или None, точки трека или None) или Reject."""
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
    if len(json.dumps(payload, ensure_ascii=False)) > (MAX_TRACK_PAYLOAD_BYTES if etype == 'track'
                                                        else MAX_PAYLOAD_BYTES):
        raise Reject('payload՝ չափազանց մեծ')
    stop_id = raw.get('stop_id')
    if stop_id is not None:
        if not isinstance(stop_id, str) or not STOP_RE.match(stop_id):
            raise Reject('stop_id՝ սխալ արժեք')
        stop_id = stop_id[:2] + stop_id[2:].upper()
    elif etype not in STOPLESS_TYPES:
        raise Reject('stop_id՝ պարտադիր է')
    ev = {'id': event_id, 'date': day.isoformat(), 'at': raw['at']}
    # версия /day, по которой водитель записал доставку или тару (§5 п. 16) — необязательна
    day_version = _text(payload.get('day_version'), DAY_VERSION_MAX, 'day_version') \
        if etype in ('delivery', 'tare') else None
    stop, flags = _stop_ctx(tx, stop_id, ev['date'], who.car_code, day_version)
    if abs((day - at.astimezone(clock.YEREVAN).date()).days) > DATE_SUSPICIOUS_DAYS:
        flags.append('date_suspicious')
    stored = dict(payload)
    scan_row = None
    track = None
    supersedes = None
    if etype in SUPERSEDABLE and (supersedes := _supersedes(payload, event_id)) is not None:
        if _supersedes_cycle(tx, event_id, etype, stop_id, supersedes):
            raise Reject('supersedes-ը շրջան է կազմում')
        stored['supersedes'] = supersedes
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
        if stop.known and payload['line_id'] not in stop.any_lines() and payload['line_id'] not in stop.line_max:
            flags.append('unknown_line')
    elif etype == 'arrived':
        _arrived(payload)
    elif etype == 'geo_suggest':
        _geo_suggest(payload)
    elif etype == 'day_closed':
        if not isinstance(payload.get('summary'), dict):
            raise Reject('summary՝ պետք է լինի օբյեկտ')
    elif etype == 'track':
        track, dropped = track_points(payload, clock.now())
        stored = {'points': len(payload['points']), 'kept': len(track), 'dropped': dropped}   # точки — в track_points
        if (device := track_device(payload.get('device'))) is not None:
            stored['device'] = device   # №76: состояние терминала — в событии (без новой таблицы и схемы courier.db)
    elif etype == 'refuel':
        flags += _refuel(tx, payload, at, who.car_code, supersedes, event_id)
        stored.setdefault('full_tank', True)   # §7 п. 2: по умолчанию «до полного бака»
    helper_id = _helper(tx, raw, ev['date'], clock.utc_key(at), who, flags)
    row = {'id': event_id, 'terminal_id': who.terminal_id, 'driver_id': who.driver_id, 'car_code': who.car_code,
           'date': ev['date'], 'stop_id': stop_id, 'type': etype, 'at_device': raw['at'], 'at_utc': clock.utc_key(at),
           'received_at': clock.iso(clock.now()), 'payload': stored, 'flags': flags, 'snapshot_id': stop.snapshot_id,
           'helper_id': helper_id}
    return row, scan_row, track


def _helper(tx: EventTx, raw: Mapping[str, Any], day: str, at_utc: str, who: Who, flags: list[str]) -> int | None:
    """helper_id события (v1.4 §8): нет поля или null — водитель один (старый APK); подтверждён (crew_log kind='helper'
    того же терминала и водителя сессии, решение — день события ±1) и не снят офисом к моменту события at_utc — он;
    иначе (не подтверждён, снят, не целое или вне INTEGER SQLite) — None и флаг helper_unconfirmed: событие не
    отклоняется, доставка важнее."""
    helper_id = raw.get('helper_id')
    if helper_id is None:
        return None
    if isinstance(helper_id, int) and not isinstance(helper_id, bool) and 0 < helper_id <= SQLITE_INT_MAX \
            and tx.helper_confirmed(who.terminal_id, who.driver_id, helper_id, day, at_utc):
        return helper_id
    flags.append('helper_unconfirmed')
    return None


def _same_state(new: Mapping[str, Any] | None, last: Mapping[str, Any] | None) -> bool:
    """№76: состояние терминала то же, что в последнем событии (gps, net, charging, app; заряд батареи не в счёт).
    Любая смена (в первую очередь gps) или отсутствие device у одного из них — не то же."""
    keys = ('gps', 'net', 'charging', 'app')
    return new is not None and last is not None and all(new.get(k) == last.get(k) for k in keys)


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
                    row, scan_row, track = _check(tx, raw, event_id, who)
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
            if track is not None and not track and _same_state(
                    row['payload'].get('device'), tx.last_track_device(
                        who.terminal_id, row['date'],
                        clock.utc_key(datetime.fromisoformat(row['at_utc']) - HEARTBEAT_GAP), row['at_utc'])):
                result.accepted.append(raw_id)   # №76: пустой heartbeat с тем же состоянием не позже HEARTBEAT_GAP после
                continue                          # предыдущего события track — принят (APK не повторяет), но не пишется
            if track is not None:   # точки — до события: в нём число новых (пачка — одна транзакция)
                row['payload']['new'] = tx.insert_track(who.car_code, row['date'], track)
                tx.purge_track(clock.today().isoformat())
                if row['payload']['dropped']:
                    logger.info('[Courier] Трек %s машины %s: отброшено точек %s', event_id, who.car_code,
                                row['payload']['dropped'])
            if not tx.insert_event(row):
                result.duplicates.append(raw_id)
                continue
            if scan_row is not None:
                tx.insert_scan(scan_row)
            result.accepted.append(raw_id)
    return result


# --- чтение ---

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

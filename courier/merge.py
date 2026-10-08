# -*- coding: utf-8 -*-
"""Правило объединения и разделения точек дня (контракт §5 п. 12, v1.2; `supersedes` — п. 14).

Чистая функция без I/O: на входе — все известные точки одной (машина, дата) и принятые события, на выходе — что
показать по каждой точке (статус, к оплате, оплачено, флаги, подсказка водителю), какие точки поглощены и какое
заявление (событие delivery) определило статус. Одно правило на сервере и в приложении: обе стороны обязаны давать
РОВНО ответы контрольных примеров courier-merge-vectors.json (версия 1.2.3; копия —
tests/fixtures/courier_merge_vectors.json; менять только синхронно с приложением).

Обозначения:
- порядок событий — по `at` (момент времени, с зоной), при равенстве — по `id` (строковое сравнение);
- latest(X) — последняя доставка точки X среди не вытесненных: доставка, на которую ссылается `supersedes` другой
  доставки той же точки, в выборе не участвует, даже если её `at` больше (часы терминала могли уйти вперёд);
- «заявление» — latest(X) вместе со строками точки X. amount заявления = Σ доставлено × цена строк ТОЙ точки, где
  оно записано (заказ — цены заказа, накладная — цены накладной); строка, которой нет в заявлении, = 0; строка без
  цены — цена 0. Статус заявления: все строки полностью → full, все 0 → refused, иначе partial.
  total(X) = Σ кол-во × цена строк X. «Товар есть в заявлении» — товар строки, названной в заявлении, даже с кол-вом 0;
- paid — только payment с kind=invoice; отменённые (на них ссылается `cancel_of`) и сами отмены не считаются
  (поэтому отмена отмены ничего не восстанавливает); долг (kind=debt) — отдельно, здесь не считается.

Компоненты. Точки связаны рёбрами S:–O: по `replaces` каждой накладной, которая есть в текущем /day или имеет свои
события; учитываются только ссылки на известные точки-ЗАКАЗЫ (ссылки на накладные и неизвестные точки игнорируются,
v1.2.3). «Свои события» — любые принятые события точки (доставка,
оплата, тара, скан, возврат…), КРОМЕ событий GPS arrived и geo_suggest (§6): они не держат пропавшую точку в списке
и не связывают её; в расчёте денег и статуса участвуют только delivery и payment. current — точки
текущего /day. Владелец — среди current сначала накладные, затем наименьший stop_id. Владелец поглощает все
не текущие точки компоненты (их события относятся к нему, сами они не показываются). Остальные текущие точки —
сёстры, у них только свои события. Текущих ≥ 2 — флаг split_order у всех текущих точек компоненты. Текущих нет —
каждая точка компоненты со своими событиями показывается отдельно с removed: true и считается сама по себе.

Владелец. Заявления = latest владельца и поглощённых точек. Нет заявлений → pending, due = total(владельца).
Иначе L — последнее заявление. Конфликт (merge_conflict), если:
  (а) у сестры есть своё заявление, а у поглощённых точек — тоже;
  (б) L записано на заказе, а среди заявлений есть заявление накладной;
  (в) L записано на накладной, а другое заявление содержит товар, которого нет в L;
  (г) L записано на поглощённой накладной, а у владельца есть товар (кол-во > 0), которого нет в L.
При конфликте статус и due — от последнего заявления накладной, если оно есть, иначе по «сумме заказов». Без
конфликта: L на накладной → статус и due от L (оно действует целиком); L на заказе → «сумма заказов»:
«покрытые» товары = товары, названные хотя бы в одном заявлении заказа (с любым кол-вом, включая 0); «остаток» =
строки владельца с кол-вом > 0, чей товар не покрыт; due = Σ amount заявлений заказов + Σ кол-во × цена строк
остатка; есть остаток → in_progress; нет — все заявления full → full, все refused → refused, иначе partial.
(Остаток — по строкам владельца, а не по списку заказов: сервер и терминал получают одно и то же, даже если
какой-то заказ из replaces терминал никогда не видел.) paid = оплаты владельца, поглощённых точек и covered-сестёр.

Сестра. Конфликт по (а) → merge_conflict, статус и due от своего заявления. Своё заявление без конфликта — как у
обычной точки. Нет своего заявления, но у поглощённых точек есть → covered, due = 0, paid = 0 (товар уже отдан по
заказу; оплаты такой сестры засчитываются ВЛАДЕЛЬЦУ, v1.2.3 — иначе водителю предложили бы взять деньги второй раз,
если вторая накладная заказа появилась позже первой). Иначе pending, due = total, paid — свои оплаты.

Подсказка водителю (instruction): removed → none; конфликт → check («Ստուգել գրասենյակի հետ», никаких «взять/
вернуть»); covered → covered; collect=none → none; ask и любое неизвестное значение → ask; cash/cash_ecr:
diff = due − paid, округлённый до 0,01 (половина — вверх); для full/partial/refused: > 0 → take, < 0 → refund,
0 → paid; для pending/in_progress: > 0 → take, иначе paid (возврат до завершения точки не показывается).
due и paid не округляются.

Уточнения реализации (контрольные примеры их не различают):
- деньги и кол-во — Decimal (float → Decimal через кратчайшую запись str, без двоичного шума); «полностью» и «0»
  сравниваются точно, без допуска;
- «половина — вверх» = ROUND_HALF_UP (от нуля, как java.math.RoundingMode.HALF_UP): diff −0,005 → −0,01 (refund 0,01);
- рёбра S:–O: — только к известным точкам source=order; строки заявления, неизвестные точке, не учитываются нигде.
Нарушение формы входа (повтор stop_id или id события, момент без зоны у delivery/payment, неизвестный source, не
число вместо денег или кол-ва) — MergeInputError (ValueError): сервер сам строит вход из проверенных данных, молча
чинить здесь нечего.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Literal, Mapping

Status = Literal['pending', 'in_progress', 'full', 'partial', 'refused', 'covered']
Kind = Literal['take', 'refund', 'paid', 'none', 'ask', 'check', 'covered']

SOURCES = ('invoice', 'order')
CASH_COLLECTS = ('cash', 'cash_ecr')   # водитель берёт деньги; none — не берёт; прочее — ask
MONEY_TYPES = ('delivery', 'payment')  # только они участвуют в деньгах и статусе
GPS_TYPES = ('arrived', 'geo_suggest')  # §6: не делают точку «со своими событиями»
FINAL: frozenset[str] = frozenset({'full', 'partial', 'refused'})
MERGE_CONFLICT = 'merge_conflict'
SPLIT_ORDER = 'split_order'
ZERO = Decimal(0)
CENT = Decimal('0.01')


class MergeInputError(ValueError):
    """Вход нарушает форму (повтор id, момент без зоны, неизвестный source, не число…)."""


# --- результат ---

def _money(d: Decimal) -> float:
    return float(d)


@dataclass(frozen=True)
class Instruction:
    """Подсказка водителю: kind и сумма (только у take/refund, округлена до 0,01)."""
    kind: Kind
    amount: Decimal | None = None

    def json(self) -> dict[str, Any]:
        return {'kind': self.kind, 'amount': None if self.amount is None else _money(self.amount)}


@dataclass(frozen=True)
class StopView:
    """Показываемая точка. due и paid — точные Decimal (не округляются).
    statements — id событий delivery, из которых взяты status и due, по порядку п. 12 (от раннего к позднему):
    одно заявление или все заявления заказов в «сумме заказов»; пусто — pending/covered (заявления нет)."""
    stop_id: str
    removed: bool
    status: Status
    due: Decimal
    paid: Decimal
    flags: tuple[str, ...]
    instruction: Instruction
    statements: tuple[str, ...] = ()

    @property
    def statement(self) -> str | None:
        """Авторитетное заявление (последнее из statements): его водитель — «кто доставил» для офиса."""
        return self.statements[-1] if self.statements else None

    def json(self) -> dict[str, Any]:
        """Форма контрольных примеров (expected): деньги — float, flags — список."""
        return {'removed': self.removed, 'status': self.status, 'due': _money(self.due), 'paid': _money(self.paid),
                'flags': list(self.flags), 'instruction': self.instruction.json()}


@dataclass(frozen=True)
class MergeResult:
    """stops — ровно показываемые точки (по stop_id); absorbed_by — поглощённая точка → её владелец
    (события поглощённой точки показываются у владельца); groups — владелец → все текущие точки его компоненты
    (владелец и сёстры, по stop_id): больше одной — заказ разделён на несколько накладных; paid_to — covered-сестра →
    владелец: её оплаты входят в paid владельца (v1.2.3), сама сестра показывается с paid = 0."""
    stops: Mapping[str, StopView] = field(default_factory=dict)
    absorbed_by: Mapping[str, str] = field(default_factory=dict)
    groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    paid_to: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> dict[str, dict[str, Any]]:
        return {sid: v.json() for sid, v in self.stops.items()}


# --- вход ---

def _dec(v: Any, what: str) -> Decimal:
    """Число входа → Decimal без двоичного шума (float — через кратчайшую запись str). Нет значения — 0."""
    if v is None:
        return ZERO
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
        raise MergeInputError(f'{what}: ожидается число, получено {v!r}')
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    if not d.is_finite():
        raise MergeInputError(f'{what}: не конечное число {v!r}')
    return d


def _moment(v: Any, what: str) -> datetime:
    """Момент `at`: ISO-8601 с зоной (строка) или datetime с зоной."""
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v)
        except ValueError as e:
            raise MergeInputError(f'{what}: неверный момент {v!r}') from e
    elif isinstance(v, datetime):
        dt = v
    else:
        raise MergeInputError(f'{what}: неверный момент {v!r}')
    if dt.utcoffset() is None:
        raise MergeInputError(f'{what}: момент без зоны {v!r}')
    return dt


@dataclass(frozen=True)
class _Line:
    line_id: str
    product_id: Any
    qty: Decimal
    price: Decimal
    gift: bool = False   # v1.2.4: строка-подарок ERP (`gift: true`, цена 0) — не «товар» правил (в), (г) и остатка


@dataclass(frozen=True)
class _Stop:
    stop_id: str
    source: str
    collect: str
    current: bool
    replaces: tuple[str, ...]
    lines: tuple[_Line, ...]

    @property
    def is_invoice(self) -> bool:
        return self.source == 'invoice'

    def total(self) -> Decimal:
        return sum((ln.qty * ln.price for ln in self.lines), ZERO)


@dataclass(frozen=True)
class _Event:
    """Событие delivery или payment (остальные типы в деньгах и статусе не участвуют)."""
    id: str
    type: str
    stop_id: str | None
    at: datetime
    payload: Mapping[str, Any]


def _key(e: _Event) -> tuple[datetime, str]:
    """Порядок событий п. 12: момент `at`, при равенстве — id."""
    return e.at, e.id


def _parse_stop(m: Mapping[str, Any]) -> _Stop:
    sid = m.get('stop_id')
    if not isinstance(sid, str) or not sid:
        raise MergeInputError(f'точка без stop_id: {m!r}')
    source, collect, current = m.get('source'), m.get('collect'), m.get('current')
    if source not in SOURCES:
        raise MergeInputError(f'{sid}: неизвестный source {source!r}')
    if not isinstance(current, bool):
        raise MergeInputError(f'{sid}: current должен быть bool, получено {current!r}')
    replaces: tuple[str, ...] = ()
    if source == 'invoice':   # replaces бывает только у накладной
        replaces = tuple(m.get('replaces') or ())
        if not all(isinstance(o, str) for o in replaces):
            raise MergeInputError(f'{sid}: replaces — только строки stop_id')
    lines: list[_Line] = []
    for ln in m.get('lines') or ():
        if not isinstance(ln, Mapping) or ln.get('line_id') is None:
            raise MergeInputError(f'{sid}: неверная строка {ln!r}')
        lid = str(ln['line_id'])
        if any(x.line_id == lid for x in lines):
            raise MergeInputError(f'{sid}: строка {lid} повторяется')
        price = _dec(ln.get('price'), f'{sid}/{lid}.price')
        lines.append(_Line(lid, ln.get('product_id'), _dec(ln.get('qty'), f'{sid}/{lid}.qty'), price,
                           ln.get('gift') is True and price == 0))
    # неизвестный collect — подсказка ask (п. 12), поэтому хранится как есть
    return _Stop(sid, source, collect if isinstance(collect, str) else '', current, replaces, tuple(lines))


def _event_ref(m: Mapping[str, Any]) -> tuple[str, str | None]:
    """(id, stop_id) события любого типа."""
    eid, stop_id = m.get('id'), m.get('stop_id')
    if not isinstance(eid, str) or not eid:
        raise MergeInputError(f'событие без id: {m!r}')
    if stop_id is not None and not isinstance(stop_id, str):
        raise MergeInputError(f'{eid}: неверный stop_id {stop_id!r}')
    return eid, stop_id


def _parse_money_event(m: Mapping[str, Any], eid: str, stop_id: str | None) -> _Event:
    payload = m.get('payload')
    if not isinstance(payload, Mapping):
        raise MergeInputError(f'{eid}: payload должен быть объектом')
    return _Event(eid, str(m['type']), stop_id, _moment(m.get('at'), eid), payload)


# --- заявления и оплаты ---

@dataclass(frozen=True)
class _Statement:
    """latest(X) вместе со строками точки X."""
    event: _Event
    stop: _Stop
    delivered: Mapping[str, Decimal]   # строка точки → доставлено (нет в заявлении — 0)
    products: frozenset[Any]           # товары строк, названных в заявлении (любое кол-во, в том числе 0), кроме подарков

    @property
    def source(self) -> str:
        return self.stop.source

    def amount(self) -> Decimal:
        return sum((self.delivered[ln.line_id] * ln.price for ln in self.stop.lines), ZERO)

    def status(self) -> Status:
        return _status(self.delivered, {ln.line_id: ln.qty for ln in self.stop.lines})


def _status(delivered: Mapping[str, Decimal], lines: Mapping[str, Decimal]) -> Status:
    if all(delivered.get(lid, ZERO) == qty for lid, qty in lines.items()):
        return 'full'
    if all(delivered.get(lid, ZERO) == 0 for lid in lines):
        return 'refused'
    return 'partial'


def statement_status(delivered: Mapping[str, Any], lines: Mapping[str, Any]) -> Status:
    """Статус одной доставки по строкам её точки (то же правило, что у заявления): delivered — line_id → доставлено
    (строка точки, которой нет в доставке, — 0; строки, которых нет у точки, не учитываются), lines — line_id → qty
    строки точки. Все строки полностью → full, все 0 → refused, иначе partial; количества сравниваются точно."""
    return _status({str(k): _dec(v, f'{k}.qty') for k, v in delivered.items()},
                   {str(k): _dec(v, f'{k}.qty') for k, v in lines.items()})


def _statement(e: _Event, stop: _Stop) -> _Statement:
    qty: dict[str, Decimal] = {}
    for item in e.payload.get('lines') or ():
        if not isinstance(item, Mapping):
            raise MergeInputError(f'{e.id}: неверная строка доставки {item!r}')
        qty[str(item.get('line_id'))] = _dec(item.get('qty'), f'{e.id}.qty')
    return _Statement(e, stop, {ln.line_id: qty.get(ln.line_id, ZERO) for ln in stop.lines},
                      frozenset(ln.product_id for ln in stop.lines if ln.line_id in qty and not ln.gift))


def _latest(stops: Mapping[str, _Stop], events: list[_Event]) -> dict[str, _Statement]:
    """latest(X) по каждой известной точке с доставками; вытесненные (`supersedes` той же точки) не участвуют."""
    by_stop: dict[str, list[_Event]] = {}
    for e in events:
        if e.type == 'delivery' and e.stop_id in stops:
            by_stop.setdefault(e.stop_id, []).append(e)
    out: dict[str, _Statement] = {}
    for sid, ds in by_stop.items():
        superseded = {e.payload.get('supersedes') for e in ds}
        live = [e for e in ds if e.id not in superseded]
        if live:
            out[sid] = _statement(max(live, key=_key), stops[sid])
    return out


def latest_by_stop(events: Iterable[Mapping[str, Any]], etype: str) -> dict[str, Mapping[str, Any]]:
    """Последнее событие типа etype по каждой точке — тем же правилом, что latest(X) (п. 12, 14): по `at`, затем по
    id; событие, на которое ссылается `supersedes` события того же типа той же точки, не участвует. Для чтения
    тары и версии точки, по которой записана действующая доставка. events — {id, type, stop_id, at, payload}."""
    by_stop: dict[str, list[tuple[tuple[datetime, str], Mapping[str, Any]]]] = {}
    for m in events:
        if m.get('type') != etype or not isinstance(m.get('stop_id'), str):
            continue
        eid, _ = _event_ref(m)
        by_stop.setdefault(m['stop_id'], []).append(((_moment(m.get('at'), eid), eid), m))
    out: dict[str, Mapping[str, Any]] = {}
    for sid, items in by_stop.items():
        superseded = {(m.get('payload') or {}).get('supersedes') for _, m in items}
        live = [(k, m) for k, m in items if k[1] not in superseded]
        if live:
            out[sid] = max(live, key=lambda x: x[0])[1]
    return out


def _cancel_of(e: _Event) -> Any:
    return e.payload.get('cancel_of') or None


def _paid_by_stop(stops: Mapping[str, _Stop], events: list[_Event]) -> dict[str, Decimal]:
    """Оплаты kind=invoice по точке: без отменённых (на них ссылается cancel_of любого payment) и без самих отмен."""
    payments = [e for e in events if e.type == 'payment']
    cancelled = {c for c in (_cancel_of(e) for e in payments) if c is not None}
    out: dict[str, Decimal] = {}
    for e in payments:
        if (e.stop_id not in stops or e.payload.get('kind') != 'invoice' or _cancel_of(e) is not None
                or e.id in cancelled):
            continue
        assert e.stop_id is not None
        out[e.stop_id] = out.get(e.stop_id, ZERO) + _dec(e.payload.get('amount'), f'{e.id}.amount')
    return out


def _components(stops: Mapping[str, _Stop], active: set[str]) -> list[list[str]]:
    """Компоненты связности по рёбрам S:–O: из replaces активных накладных (в текущем /day или со своими событиями)."""
    adj: dict[str, set[str]] = {sid: set() for sid in stops}
    for sid in active:
        s = stops[sid]
        if not s.is_invoice:
            continue
        for o in s.replaces:
            target = stops.get(o)
            if target is not None and not target.is_invoice:
                adj[sid].add(o)
                adj[o].add(sid)
    seen: set[str] = set()
    out: list[list[str]] = []
    for start in sorted(stops):
        if start in seen:
            continue
        seen.add(start)
        comp, todo = [], [start]
        while todo:
            x = todo.pop()
            comp.append(x)
            for y in adj[x] - seen:
                seen.add(y)
                todo.append(y)
        out.append(sorted(comp))
    return out


# --- правило ---

def _instruction(collect: str, status: Status, due: Decimal, paid: Decimal, flags: tuple[str, ...],
                 removed: bool) -> Instruction:
    if removed:
        return Instruction('none')
    if MERGE_CONFLICT in flags:
        return Instruction('check')
    if status == 'covered':
        return Instruction('covered')
    if collect == 'none':
        return Instruction('none')
    if collect not in CASH_COLLECTS:   # ask и любое неизвестное значение
        return Instruction('ask')
    diff = (due - paid).quantize(CENT, rounding=ROUND_HALF_UP)
    if diff > 0:
        return Instruction('take', diff)
    if diff < 0 and status in FINAL:   # возврат — только когда точка завершена
        return Instruction('refund', -diff)
    return Instruction('paid')


def _view(stop: _Stop, status: Status, due: Decimal, paid: Decimal, flags: Iterable[str],
          used: Iterable[_Statement], removed: bool = False) -> StopView:
    fl = tuple(sorted(set(flags)))
    return StopView(stop.stop_id, removed, status, due, paid, fl,
                    _instruction(stop.collect, status, due, paid, fl, removed),
                    tuple(s.event.id for s in sorted(used, key=lambda s: _key(s.event))))


def _sum_of_orders(owner: _Stop, stmts: list[_Statement]) -> tuple[Status, Decimal, list[_Statement]]:
    """«Сумма заказов»: заявления заказов + «остаток» — строки владельца с кол-вом > 0, чей товар не назван ни в одном
    заявлении заказа; строка-подарок (v1.2.4) — не остаток: подарок только накладной точку открытой не держит."""
    orders = [s for s in stmts if s.source == 'order']
    covered = frozenset().union(*(s.products for s in orders))
    rest = [ln for ln in owner.lines if ln.qty > 0 and not ln.gift and ln.product_id not in covered]
    due = sum((s.amount() for s in orders), ZERO) + sum((ln.qty * ln.price for ln in rest), ZERO)
    if rest:
        return 'in_progress', due, orders
    statuses = {s.status() for s in orders}
    if statuses == {'full'}:
        return 'full', due, orders
    if statuses == {'refused'}:
        return 'refused', due, orders
    return 'partial', due, orders


def merge(stops: Iterable[Mapping[str, Any]], events: Iterable[Mapping[str, Any]]) -> MergeResult:
    """Правило п. 12 для одной (машина, дата).

    stops — все известные точки дня: {stop_id, source: invoice|order, collect (cash|cash_ecr|none|ask, прочее = ask),
    current: bool (есть ли в последнем /day), replaces: [stop_id] (только у накладной), lines: [{line_id,
    product_id, qty, price, gift?}] — строки той версии, по которой записаны события}; лишние поля игнорируются.
    v1.2.4 (подарки ERP, контракт §11): строка с gift = true и ценой 0 — подарок: её товар не считается товаром
    заявления (правила (в), «покрытые товары»), строкой остатка и товаром владельца в (г); в статусе и деньгах —
    обычная строка (цена 0).
    events — все принятые события точек дня, любого типа: {id, type, stop_id, at, payload}. Любое событие, кроме
    arrived и geo_suggest, делает точку «со своими событиями»; в деньгах и статусе участвуют только delivery
    ({lines: [{line_id, qty}], supersedes?}) и payment ({amount, kind: invoice|debt, cancel_of?}) — у них at
    (ISO-8601 с зоной или datetime с зоной) и payload обязательны. События неизвестных точек не показываются (но их
    cancel_of действует).
    Детерминирован: порядок stops и events на результат не влияет."""
    known: dict[str, _Stop] = {}
    for m in stops:
        s = _parse_stop(m)
        if s.stop_id in known:
            raise MergeInputError(f'точка {s.stop_id} повторяется')
        known[s.stop_id] = s
    evs: list[_Event] = []
    ids: set[str] = set()
    with_events: set[str] = set()
    for m in events:
        eid, stop_id = _event_ref(m)
        if eid in ids:
            raise MergeInputError(f'событие {eid} повторяется')
        ids.add(eid)
        if stop_id in known and m.get('type') not in GPS_TYPES:
            with_events.add(stop_id)
        if m.get('type') in MONEY_TYPES:
            evs.append(_parse_money_event(m, eid, stop_id))

    latest = _latest(known, evs)
    paid_by = _paid_by_stop(known, evs)
    active = {sid for sid, s in known.items() if s.current} | with_events

    def paid(xs: Iterable[str]) -> Decimal:
        return sum((paid_by.get(x, ZERO) for x in xs), ZERO)

    views: dict[str, StopView] = {}
    absorbed_by: dict[str, str] = {}
    groups: dict[str, tuple[str, ...]] = {}
    paid_to: dict[str, str] = {}
    for members in _components(known, active):
        cur = [x for x in members if known[x].current]
        if not cur:   # точки нет в текущем /day: каждая со своими событиями — отдельно, «убрана»
            for x in members:
                if x not in with_events:
                    continue
                s = latest.get(x)
                if s is None:
                    views[x] = _view(known[x], 'pending', known[x].total(), paid([x]), (), (), removed=True)
                else:
                    views[x] = _view(known[x], s.status(), s.amount(), paid([x]), (), (s,), removed=True)
            continue

        owner = known[min(cur, key=lambda x: (not known[x].is_invoice, x))]
        siblings = [x for x in cur if x != owner.stop_id]
        absorbed = [x for x in members if not known[x].current]
        absorbed_by.update((x, owner.stop_id) for x in absorbed)
        groups[owner.stop_id] = tuple(sorted(cur))
        split = (SPLIT_ORDER,) if len(cur) >= 2 else ()
        absorbed_stmts = [latest[x] for x in absorbed if x in latest]
        sib_conflict = {x for x in siblings if x in latest and absorbed_stmts}   # (а)
        covered = [x for x in siblings if x not in latest and absorbed_stmts]     # их оплаты — у владельца
        paid_to.update((x, owner.stop_id) for x in covered)

        # владелец
        scope = [owner.stop_id, *absorbed]
        stmts = [latest[x] for x in scope if x in latest]
        flags = list(split)
        if not stmts:
            views[owner.stop_id] = _view(owner, 'pending', owner.total(), paid(scope + covered), flags, ())
        else:
            last = max(stmts, key=lambda s: _key(s.event))
            invoice_stmts = [s for s in stmts if s.source == 'invoice']
            conflict = bool(sib_conflict)
            if last.source == 'order':
                conflict = conflict or bool(invoice_stmts)                                         # (б)
            else:
                conflict = conflict or any(not s.products <= last.products for s in stmts if s is not last)   # (в)
                if last.stop.stop_id != owner.stop_id:                                             # (г)
                    own = {ln.product_id for ln in owner.lines if ln.qty > 0 and not ln.gift}
                    conflict = conflict or not own <= last.products
            status: Status
            if conflict:
                flags.append(MERGE_CONFLICT)
            if conflict and invoice_stmts:
                auth = max(invoice_stmts, key=lambda s: _key(s.event))
                status, due, used = auth.status(), auth.amount(), [auth]
            elif not conflict and last.source == 'invoice':
                status, due, used = last.status(), last.amount(), [last]
            else:
                status, due, used = _sum_of_orders(owner, stmts)
            views[owner.stop_id] = _view(owner, status, due, paid(scope + covered), flags, used)

        # сёстры
        for x in siblings:
            stop, own_stmt = known[x], latest.get(x)
            sflags = [*split, *([MERGE_CONFLICT] if x in sib_conflict else [])]
            if own_stmt is not None:   # с конфликтом (а) или без — статус и due от своего заявления
                views[x] = _view(stop, own_stmt.status(), own_stmt.amount(), paid([x]), sflags, (own_stmt,))
            elif absorbed_stmts:   # covered: оплаты — у владельца (paid_to)
                views[x] = _view(stop, 'covered', ZERO, ZERO, sflags, ())
            else:
                views[x] = _view(stop, 'pending', stop.total(), paid([x]), sflags, ())

    return MergeResult(dict(sorted(views.items())), dict(sorted(absorbed_by.items())), dict(sorted(groups.items())),
                       dict(sorted(paid_to.items())))

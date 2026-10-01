# -*- coding: utf-8 -*-
"""Оптимизация дней визитов внутри менеджера — режим А (этап 3).

- задачи по менеджерам собираются из снимка ERP этапа 1 и настроек (новых запросов к ERP нет);
- частоты — frequency.py, шаблоны — patterns.py, поиск — search.py (seed = crc32(агент));
- «было / стало» — полным оценщиком этапа 1 (evaluate.evaluate_plan): «было» — текущий план, W = 1;
  «стало» — предложенный, W = 2, f_i — из предложенного плана; всё — в неделю;
- предложения — по клиентам с эффектом каждого изменения, применённого к ТЕКУЩЕМУ плану;
- решения владельца: принятый шаблон — закреплён, принятая частота — целевая, отклонённые — запрещены;
  решение привязано к плану, на котором принималось (from_value): план клиента в ERP изменился —
  принятое решение устарело (не применяется, показывается), отклонённое — отработало (retired);
  принятое, которое в ERP уже исполнено, — тоже отработало;
- статус клиента (§15, status.py): у затихших, потерянных и без заказов λ = 0 (модели этапа 1);
  в режиме «по продажам» затихшему — раз в 2 недели, потерянному и без заказов — предложение
  «убрать из маршрута» (remove): в плане «стало» его нет; решение remove принято — убран в любом
  режиме, отклонено («оставить») — дальше как обычный клиент без продаж (раз в 2 недели);
- выгрузка для ERP — текущий план + только принятые изменения, порядок дня — NN + 2-opt от дома.

Чистая логика — без Flask и без БД: настройки и решения передаёт вызывающий.
"""
from __future__ import annotations

import logging
import math
import random
import time
import zlib
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from operator import itemgetter
from statistics import fmean
from typing import TYPE_CHECKING, Any, Callable, Collection, Iterable, Mapping, Sequence

from . import demand as dm
from . import evaluate as ev
from . import fleet as fl
from . import frequency as fq
from . import patterns as pt
from . import search as sr
from . import status as cst
from . import transfer as tr
from .evaluate import _day_json, _i, _num, _r
from .geo import Coord, Point, haversine_km, in_city
from .plan import CurrentPlan, PlanDay, PlanVisit, WEEKDAY_LABELS
from .store import REMOVE_VALUE, DecisionInput
from .tsp import Distance, route_order

if TYPE_CHECKING:
    from .roads import RoadDistances
    from .snapshot import Snapshot
    from .store import Bundle, Decision

logger = logging.getLogger(__name__)

START_MODES = ('current', 'fresh')
FREQUENCY_MODES = ('sales', 'current')
DEFAULT_PARAMS: dict[str, Any] = {'agent_ids': None, 'start': 'current', 'frequencies': 'sales'}
# Режим расчёта (этап 4): days — только дни внутри менеджера (режим А, по умолчанию; в params не
# пишется — расчёт и результат как на этапе 3), transfer — разрешить передавать магазины (режим Б)
MODE_DAYS, MODE_TRANSFER = 'days', 'transfer'
MODES = (MODE_DAYS, MODE_TRANSFER)
DECISION_FIELDS = ('customer_id', 'agent_id', 'kind', 'value', 'action', 'from')
MAX_DECISION_ITEMS = 2000                 # решений в одном запросе («Принять все у менеджера»)
MAX_AGENTS = 500
MAX_ID = 2 ** 31 - 1
# Поля дня в результате (§10.1) — подмножество дня обзора этапа 1
DAY_KEYS = ('week', 'weekday', 'label', 'visits', 'revenue_low_exp', 'p_day_ge_min', 'work_minutes',
            'commute_minutes', 'plan_minutes', 'manager_km', 'stops')
MARKS = {'move': 'перенос', 'both': 'перенос, частота', 'frequency': 'частота', 'remove': 'убрать',
         'transfer': 'передать'}
SOURCE_STATUS = 'status'   # убрать из маршрута по статусу клиента (§15): потерян или без заказов
# Решение относительно плана снимка (decision_state)
DECISION_ACTIVE, DECISION_STALE, DECISION_RETIRED = 'active', 'stale', 'retired'
NOT_IN_PLAN_TEXT = 'клиента нет в плане менеджера'

ProgressFn = Callable[[int, int, 'str | None'], None]


class OptimizeError(RuntimeError):
    """Расчёт невозможен; текст — для пользователя."""


# --- Параметры и решения из API ---

def parse_params(payload: Any) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Тело POST /api/routes/optimize: {"agent_ids": [..] | null, "start", "frequencies", "mode"}.
    Отсутствующие поля — по умолчанию; mode = transfer попадает в params (days — режим по умолчанию,
    его в params нет). Ошибки — {поле: текст}."""
    if not isinstance(payload, dict):
        return None, {'_': 'ожидался JSON-объект'}
    errors: dict[str, str] = {}
    unknown = sorted(str(k) for k in set(payload) - set(DEFAULT_PARAMS) - {'mode'})
    if unknown:
        errors['_'] = 'неизвестные параметры: ' + ', '.join(unknown)
    params = dict(DEFAULT_PARAMS)
    ids = payload.get('agent_ids')
    if ids is not None:
        if (not isinstance(ids, list) or not ids or len(ids) > MAX_AGENTS
                or any(isinstance(a, bool) or not isinstance(a, int) for a in ids)):
            errors['agent_ids'] = 'ожидался непустой список id менеджеров или null (все в расчёте)'
        elif len(set(ids)) != len(ids):
            errors['agent_ids'] = 'менеджер указан дважды'
        else:
            params['agent_ids'] = list(ids)
    start = payload.get('start', DEFAULT_PARAMS['start'])
    if start not in START_MODES:
        errors['start'] = 'старт: current (улучшить текущий план) или fresh (построить с нуля)'
    else:
        params['start'] = start
    freq = payload.get('frequencies', DEFAULT_PARAMS['frequencies'])
    if freq not in FREQUENCY_MODES:
        errors['frequencies'] = 'частота: sales (по продажам) или current (как сейчас)'
    else:
        params['frequencies'] = freq
    mode = payload.get('mode', MODE_DAYS)
    if mode not in MODES:
        errors['mode'] = 'режим: days (только дни внутри менеджера) или transfer (передавать магазины)'
    elif mode == MODE_TRANSFER:
        params['mode'] = MODE_TRANSFER
    return (None, errors) if errors else (params, {})


def parse_decision(payload: Any) -> tuple[DecisionInput | None, dict[str, str]]:
    """Одно решение: {"customer_id", "agent_id", "kind": "pattern"|"freq"|"remove"|"transfer", "value":
    [[неделя, день], …] | 0.5 | [] (remove: значение можно не присылать) | {"agent_id": кому, "pattern":
    [[неделя, день], …]} (transfer), "action": "accept"|"reject"|"reset", "from": шаблон | частота
    клиента в предложении}. remove: accept — убрать из маршрута, reject — оставить; transfer: accept —
    передать менеджеру value.agent_id в дни value.pattern, reject — этому менеджеру не передавать;
    у remove и transfer from — шаблон. from — от чего принимается решение (строка «было» предложения);
    нет — возьмётся план снимка (bind_decisions); для reset не нужен."""
    if not isinstance(payload, dict):
        return None, {'_': 'ожидался JSON-объект'}
    errors: dict[str, str] = {}
    unknown = sorted(str(k) for k in set(payload) - set(DECISION_FIELDS))
    if unknown:
        errors['_'] = 'неизвестные поля: ' + ', '.join(unknown)
    for key in ('customer_id', 'agent_id'):
        v = payload.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= MAX_ID:
            errors[key] = 'ожидался id — целое число'
    kind = payload.get('kind')
    action = payload.get('action')
    raw_from = payload.get('from')
    value = from_value = None
    if kind in ('pattern', 'remove', 'transfer'):
        if kind == 'remove':
            raw = payload.get('value')
            if raw is None or (isinstance(raw, list) and not raw):
                value = REMOVE_VALUE
            else:
                errors['value'] = 'убрать из маршрута — значение не нужно (или пустой список [])'
        elif kind == 'transfer':
            hit = pt.parse_transfer(payload.get('value'))
            if hit is None:
                errors['value'] = ('передать — {"agent_id": id менеджера, "pattern": [[неделя 1–2, '
                                   'день недели 1–7], …]}')
            elif hit[0] == payload.get('agent_id'):
                errors['value'] = 'передать можно только другому менеджеру'
            else:
                value = pt.transfer_key(*hit)
        else:
            p = pt.parse_pattern(payload.get('value'))
            if p is None:
                errors['value'] = 'шаблон — список пар [неделя 1–2, день недели 1–7] без повторов'
            else:
                value = pt.pattern_key(p)
        if raw_from is not None and action != 'reset':
            p = pt.parse_pattern(raw_from)
            if p is None:
                errors['from'] = 'было — список пар [неделя 1–2, день недели 1–7] без повторов'
            else:
                from_value = pt.pattern_key(p)
    elif kind == 'freq':
        f = pt.parse_freq(payload.get('value'))
        if f is None:
            errors['value'] = 'частота — 0.5, 1, 2 или 3 визита в неделю'
        else:
            value = pt.freq_key(f)
        if raw_from is not None and action != 'reset':
            f = pt.parse_plan_freq(raw_from)
            if f is None:
                errors['from'] = 'было — визитов в неделю: 0.5, 1, 1.5 … 7'
            else:
                from_value = pt.freq_key(f)
    else:
        errors['kind'] = ('вид решения: pattern (шаблон), freq (частота), remove (убрать из маршрута) '
                          'или transfer (передать другому менеджеру)')
    if action not in ('accept', 'reject', 'reset'):
        errors['action'] = 'действие: accept, reject или reset'
    if errors or value is None:
        return None, errors
    return DecisionInput(payload['customer_id'], payload['agent_id'], kind, value, action,
                         from_value), {}


@dataclass(frozen=True)
class DecisionRequest:
    """Тело POST /api/routes/decisions: одно решение (one), пачка одной транзакцией (batch) или
    «Сбросить все» (reset_all)."""
    mode: str
    items: tuple[DecisionInput, ...] = ()


def parse_decisions(payload: Any) -> tuple[DecisionRequest | None, dict[str, str]]:
    """{"items": [решение, …]} — все или ни одного, ошибки по строкам: items.<№>.<поле>;
    {"action": "reset_all"}; иначе — одно решение (parse_decision)."""
    if isinstance(payload, dict) and 'items' in payload:
        errors: dict[str, str] = {}
        unknown = sorted(str(k) for k in set(payload) - {'items'})
        if unknown:
            errors['_'] = 'неизвестные поля: ' + ', '.join(unknown)
        items = payload['items']
        if not isinstance(items, list) or not items or len(items) > MAX_DECISION_ITEMS:
            errors['items'] = f'ожидался непустой список решений (не больше {MAX_DECISION_ITEMS})'
            return None, errors
        out = []
        for i, item in enumerate(items):
            d, errs = parse_decision(item)
            errors.update({(f'items.{i}' if k == '_' else f'items.{i}.{k}'): v for k, v in errs.items()})
            if d is not None:
                out.append(d)
        return (None, errors) if errors else (DecisionRequest('batch', tuple(out)), {})
    if isinstance(payload, dict) and payload.get('action') == 'reset_all':
        unknown = sorted(str(k) for k in set(payload) - {'action'})
        if unknown:
            return None, {'_': 'неизвестные поля: ' + ', '.join(unknown)}
        return DecisionRequest('reset_all'), {}
    d, errors = parse_decision(payload)
    return (None, errors) if d is None else (DecisionRequest('one', (d,)), {})


# --- Решения относительно плана снимка ---

def current_value(pairs: Mapping[int, Mapping[int, PairInfo]], agent_id: int, customer_id: int,
                  kind: str) -> str | None:
    """Шаблон (pattern_key; и для remove и transfer — решение принимается от шаблона) или частота
    (freq_key) клиента у менеджера в плане снимка; None — клиента нет в плане менеджера."""
    info = pairs.get(agent_id, {}).get(customer_id)
    if info is None:
        return None
    return pt.freq_key(pt.pattern_freq(info.pattern)) if kind == 'freq' else pt.pattern_key(info.pattern)


def decision_state(d: Decision, pairs: Mapping[int, Mapping[int, PairInfo]]) -> str:
    """Решение относительно плана снимка (текущий шаблон или частота клиента у менеджера):
    - принятое, и в ERP уже так (value = текущему; remove — клиента у менеджера больше нет; transfer —
      клиента у менеджера больше нет, а у того, кому передать, есть) — retired: план применён;
    - принятое на другом плане (from_value ≠ текущему) или клиента у менеджера больше нет —
      stale: не применяется, владелец видит его в списке устаревших;
    - отклонённое на другом плане (или клиента нет) — retired: запрет относился к прежнему плану;
    - иначе — active. from_value неизвестен (решение схемы 3) — план считается тем же."""
    cur = current_value(pairs, d.agent_id, d.customer_id, d.kind)
    moved_on = cur is None or (d.from_value is not None and d.from_value != cur)
    if d.status == 'accepted':
        if d.kind == 'remove':
            done = cur is None
        elif d.kind == 'transfer':
            hit = pt.parse_transfer_key(d.value)
            done = cur is None and hit is not None and d.customer_id in pairs.get(hit[0], {})
        else:
            done = cur is not None and d.value == cur
        if done:
            return DECISION_RETIRED
        return DECISION_STALE if moved_on else DECISION_ACTIVE
    return DECISION_RETIRED if moved_on else DECISION_ACTIVE


def bind_decisions(request: DecisionRequest, pairs: Mapping[int, Mapping[int, PairInfo]]
                   ) -> tuple[list[DecisionInput], dict[str, str]]:
    """Решения запроса, привязанные к плану снимка: клиент должен быть в плане менеджера (снять
    решение — можно и без этого); from не прислан — текущий шаблон или частота клиента."""
    out: list[DecisionInput] = []
    errors: dict[str, str] = {}
    for i, d in enumerate(request.items):
        if d.action == 'reset':
            out.append(d)
            continue
        cur = current_value(pairs, d.agent_id, d.customer_id, d.kind)
        prefix = f'items.{i}.' if request.mode == 'batch' else ''
        if cur is None:
            errors[prefix + 'customer_id'] = 'У этого менеджера нет такого клиента в плане ERP'
            continue
        if d.kind == 'transfer':
            hit = pt.parse_transfer_key(d.value)
            if hit is None or hit[0] not in pairs:
                errors[prefix + 'value'] = 'У менеджера, которому передать, нет маршрутов в ERP'
                continue
        out.append(d if d.from_value is not None else replace(d, from_value=cur))
    return out, errors


@dataclass(frozen=True)
class DecisionBook:
    """Решения владельца для расчёта. Ключи — (менеджер, клиент). stale — принятые, но устаревшие
    (приняты на другом плане): не закрепляют ничего. accepted_remove — убрать из маршрута,
    rejected_remove — оставить (удаление больше не предлагается). accepted_transfer — передать
    (кому, в какие дни); rejected_transfer — каким менеджерам этого клиента не передавать (этап 4)."""
    accepted_pattern: dict[tuple[int, int], pt.Pattern]
    accepted_freq: dict[tuple[int, int], float]
    rejected_pattern: dict[tuple[int, int], frozenset[pt.Pattern]]
    rejected_freq: dict[tuple[int, int], frozenset[float]]
    # (клиент, менеджер, вид, значение) → (статус, от чего принималось)
    status: dict[tuple[int, int, str, str], tuple[str, str | None]]
    stale: tuple[Decision, ...] = ()
    accepted_remove: frozenset[tuple[int, int]] = frozenset()
    rejected_remove: frozenset[tuple[int, int]] = frozenset()
    accepted_transfer: Mapping[tuple[int, int], tuple[int, pt.Pattern]] = field(default_factory=dict)
    rejected_transfer: Mapping[tuple[int, int], frozenset[int]] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: Sequence[Decision],
                  pairs: Mapping[int, Mapping[int, PairInfo]] | None = None) -> DecisionBook:
        """pairs — план снимка: устаревшие и отработавшие решения не действуют (decision_state);
        None — все решения действуют (статусы для показа в сохранённом результате)."""
        acc_p: dict[tuple[int, int], pt.Pattern] = {}
        acc_f: dict[tuple[int, int], float] = {}
        rej_p: dict[tuple[int, int], set[pt.Pattern]] = defaultdict(set)
        rej_f: dict[tuple[int, int], set[float]] = defaultdict(set)
        acc_r: set[tuple[int, int]] = set()
        rej_r: set[tuple[int, int]] = set()
        acc_t: dict[tuple[int, int], tuple[int, pt.Pattern]] = {}
        rej_t: dict[tuple[int, int], set[int]] = defaultdict(set)
        status = {}
        stale = []
        for d in rows:
            state = decision_state(d, pairs) if pairs is not None else DECISION_ACTIVE
            if state == DECISION_STALE:
                stale.append(d)
            if state != DECISION_ACTIVE:
                continue
            key = (d.agent_id, d.customer_id)
            status[(d.customer_id, d.agent_id, d.kind, d.value)] = (d.status, d.from_value)
            if d.kind == 'remove':
                (acc_r if d.status == 'accepted' else rej_r).add(key)
            elif d.kind == 'transfer':
                hit = pt.parse_transfer_key(d.value)
                if hit is None:
                    continue
                if d.status == 'accepted':
                    acc_t[key] = hit
                else:
                    rej_t[key].add(hit[0])
            elif d.kind == 'pattern':
                p = pt.parse_pattern_key(d.value)
                if p is None:
                    continue
                if d.status == 'accepted':
                    acc_p[key] = p
                else:
                    rej_p[key].add(p)
            else:
                f = pt.parse_freq_key(d.value)
                if f is None:
                    continue
                if d.status == 'accepted':
                    acc_f[key] = f
                else:
                    rej_f[key].add(f)
        return cls(acc_p, acc_f, {k: frozenset(v) for k, v in rej_p.items()},
                   {k: frozenset(v) for k, v in rej_f.items()}, status, tuple(stale),
                   accepted_remove=frozenset(acc_r), rejected_remove=frozenset(rej_r),
                   accepted_transfer=acc_t,
                   rejected_transfer={k: frozenset(v) for k, v in rej_t.items()})

    def _status(self, customer_id: int, agent_id: int, kind: str, value: str,
                from_key: str) -> str | None:
        hit = self.status.get((customer_id, agent_id, kind, value))
        if hit is None:
            return None
        status, from_value = hit
        return status if from_value is None or from_value == from_key else None

    def change_status(self, agent_id: int, change: Mapping[str, Any]) -> dict[str, str | None]:
        """Статусы решений для строки предложения: по шаблону «стало» и (если частота меняется)
        по частоте «стало»; у «убрать из маршрута» — решение remove. Решение относится к строке,
        только если принималось от того же «было»: решение по прежнему плану клиента к новому
        предложению не приклеивается."""
        cid, frm, to = change['customer_id'], change['from'], change['to']
        if change['type'] == 'transfer':
            return {'transfer': self._status(cid, agent_id, 'transfer',
                                             pt.transfer_key(change['to_agent'], to['pattern']),
                                             pt.pattern_key(frm['pattern']))}
        if change['type'] == 'remove':
            return {'remove': self._status(cid, agent_id, 'remove', REMOVE_VALUE,
                                           pt.pattern_key(frm['pattern']))}
        freq = None
        if change['type'] != 'move':
            freq = self._status(cid, agent_id, 'freq', pt.freq_key(to['freq']), pt.freq_key(frm['freq']))
        return {'pattern': self._status(cid, agent_id, 'pattern', pt.pattern_key(to['pattern']),
                                        pt.pattern_key(frm['pattern'])),
                'freq': freq}


def with_decisions(result: Mapping[str, Any], decisions: Sequence[Decision]) -> dict[str, Any]:
    """Результат с актуальными статусами решений (таблица decision — источник правды): копия,
    исходный результат не меняется."""
    book = DecisionBook.from_rows(decisions)
    return {**result, 'managers': [
        {**m, 'changes': [{**ch, 'decision': book.change_status(m['agent_id'], ch)}
                          for ch in m['changes']]}
        for m in result['managers']]}


def _side_text(kind: str, key: str | None) -> tuple[Any, str | None]:
    """(значение для JSON, текст) шаблона или частоты по каноническому тексту; remove — «убрать из
    маршрута» (исходное значение и план сейчас у remove — шаблон: kind 'pattern')."""
    if key is None:
        return None, None
    if kind == 'remove':
        return [], cst.REMOVE_TEXT
    if kind == 'pattern':
        p = pt.parse_pattern_key(key)
        return (pt.pattern_json(p), pt.pattern_text(p)) if p is not None else (None, None)
    f = pt.parse_plan_freq_key(key)
    return (_num(f), fq.freq_text(f)) if f is not None else (None, None)


def decision_json(snap: Snapshot, d: Decision, pairs: Mapping[int, Mapping[int, PairInfo]],
                  included: bool) -> dict[str, Any]:
    """Решение для владельца: клиент, менеджер, что решено («было → стало»), что в плане сейчас."""
    agent, cust = snap.agents.get(d.agent_id), snap.customers.get(d.customer_id)
    cur = current_value(pairs, d.agent_id, d.customer_id, d.kind)
    # «было» и «сейчас» у remove и transfer — шаблон
    plan_kind = 'pattern' if d.kind in ('remove', 'transfer') else d.kind
    if d.kind == 'transfer':   # кому и в какие дни: {"agent_id", "agent_code", "agent_name", "pattern"}
        hit = pt.parse_transfer_key(d.value)
        value, to_text = None, None
        if hit is not None:
            to_agent = snap.agents.get(hit[0])
            value = {'agent_id': hit[0], 'agent_code': _code(snap, hit[0]),
                     'agent_name': to_agent.name if to_agent else '', 'pattern': pt.pattern_json(hit[1])}
            to_text = f'передать {_code(snap, hit[0])}: {pt.pattern_text(hit[1])}'
    else:
        value, to_text = _side_text(d.kind, d.value)
    frm, from_text = _side_text(plan_kind, d.from_value)
    _, current_text = _side_text(plan_kind, cur)
    return {'customer_id': d.customer_id, 'customer_code': cust.code if cust else '',
            'customer_name': cust.name if cust else '',
            'agent_id': d.agent_id, 'agent_code': _code(snap, d.agent_id),
            'agent_name': agent.name if agent else '', 'manager_included': included,
            'kind': d.kind, 'status': d.status, 'stale': decision_state(d, pairs) == DECISION_STALE,
            'value': value, 'from': frm, 'to_text': to_text, 'from_text': from_text,
            'current_text': current_text or NOT_IN_PLAN_TEXT,
            'updated_at': d.updated_at, 'updated_by': d.updated_by}


def stale_json(snap: Snapshot, bundle: Bundle, book: DecisionBook,
               pairs: Mapping[int, Mapping[int, PairInfo]]) -> list[dict[str, Any]]:
    return [decision_json(snap, d, pairs, bundle.included(d.agent_id, snap.active_agents))
            for d in book.stale]


# --- Текущий план в цикле 2 недели ---

@dataclass(frozen=True)
class PairInfo:
    """Клиент у менеджера: текущий шаблон (цикл 2 недели) и адрес первого визита."""
    pattern: pt.Pattern
    address_id: int


def plan_pairs(plan: CurrentPlan) -> dict[int, dict[int, PairInfo]]:
    """агент → клиент → текущий шаблон. Недельный план (W = 1) действует в обе недели цикла."""
    if plan.cycle_weeks not in (1, 2):
        raise OptimizeError(f'Цикл шаблонов ERP — {plan.cycle_weeks} нед.; оптимизация поддерживает '
                            f'цикл 1 или 2 недели')
    raw: dict[int, dict[int, list]] = {}
    for d in sorted(plan.days, key=lambda x: (x.agent_id, x.week, x.weekday)):
        weeks = (1, 2) if plan.cycle_weeks == 1 else (d.week,)
        for v in d.visits:
            entry = raw.setdefault(d.agent_id, {}).setdefault(v.customer_id, [set(), v.address_id])
            entry[0].update((w, d.weekday) for w in weeks)
    return {a: {c: PairInfo(pt.make_pattern(slots), addr) for c, (slots, addr) in cs.items()}
            for a, cs in raw.items()}


def _agent_order(snap: Snapshot) -> list[int]:
    return sorted(snap.plan.agent_ids,
                  key=lambda a: (snap.agents[a].code if a in snap.agents else '', a))


def _code(snap: Snapshot, agent_id: int) -> str:
    agent = snap.agents.get(agent_id)
    return agent.code if agent else str(agent_id)


def resolve_agents(snap: Snapshot, bundle: Bundle,
                   agent_ids: Sequence[int] | None) -> tuple[list[int], str | None]:
    """Менеджеры расчёта (по коду): null — все в расчёте; иначе — только менеджеры в расчёте."""
    order = _agent_order(snap)
    included = [a for a in order if bundle.included(a, snap.active_agents)]
    if agent_ids is None:
        return (included, None) if included else ([], 'Нет менеджеров в расчёте — включите их в настройках')
    allowed = set(included)
    bad = [a for a in agent_ids if a not in allowed]
    if bad:
        return [], ('Не в расчёте или без маршрутов: ' + ', '.join(_code(snap, a) for a in bad)
                    + ' — включить менеджера можно в настройках')
    wanted = set(agent_ids)
    return [a for a in order if a in wanted], None


# --- Частота и допустимые шаблоны клиента ---

@dataclass(frozen=True)
class PairSpec:
    current: pt.Pattern
    target: float
    source: str                        # manual | sales | current | locked | status
    allowed: tuple[pt.Pattern, ...]
    locked: bool
    forbidden: frozenset[pt.Pattern]
    removed: bool = False              # убрать из маршрута (§15): частота 0, единственный шаблон ()


def pair_spec(key: tuple[int, int], current: pt.Pattern, f_sales: float | None, mode: str,
              workdays: Collection[int], book: DecisionBook, status: str | None = None) -> PairSpec:
    """Целевая частота и допустимые шаблоны: принятое «убрать из маршрута» — клиент убран; принятый
    шаблон — закреплён (единственный); в режиме «по продажам» потерянный и без заказов за год
    (status, §15) — убран, если владелец не оставил его, не закрепил шаблон и не задал частоту;
    иначе частота по §2 и шаблоны по §3 без запрещённых. Нет ни одного — частота остаётся
    текущей; запрещено всё — клиент остаётся как есть."""
    forbidden = book.rejected_pattern.get(key, frozenset())
    if key in book.accepted_remove:
        return PairSpec(current, 0.0, fq.SOURCE_MANUAL, ((),), False, forbidden, removed=True)
    lock = book.accepted_pattern.get(key)
    if lock is not None:
        return PairSpec(current, pt.pattern_freq(lock), 'locked', (lock,), True, forbidden)
    if (mode == 'sales' and status in cst.REMOVABLE and key not in book.rejected_remove
            and key not in book.accepted_freq):
        return PairSpec(current, 0.0, SOURCE_STATUS, ((),), False, forbidden, removed=True)
    f_cur = pt.pattern_freq(current)
    target, source = fq.target_frequency(f_cur, f_sales, mode, book.accepted_freq.get(key),
                                         book.rejected_freq.get(key, ()))
    allowed = pt.allowed_patterns(current, target, workdays, forbidden)
    if not allowed and not pt.same_freq(target, f_cur):
        target, source = f_cur, fq.SOURCE_CURRENT
        allowed = pt.allowed_patterns(current, target, workdays, forbidden)
    return PairSpec(current, target, source, tuple(allowed or [current]), False, forbidden)


def _status_code(model: ev.CustomerModel) -> str | None:
    return model.status.status if model.status is not None else None


def season_lam(model: ev.CustomerModel) -> float:
    """Спрос для частоты по продажам: max(λ_год, λ_низкий сезон, λ_пик). Выручка клиента в сезоне
    s по модели — min(f, λ_s) × средний заказ: при f ≥ max λ_s снижение частоты ни в каком сезоне
    выручку не уменьшает (частота по одному году летом «съедала» заказы)."""
    return max(model.year.lam, model.low.lam, model.peak.lam)


def visit_params(model: ev.CustomerModel, f_total: float) -> sr.VisitParams:
    """Параметры визита при частоте f_total (все визиты клиента у менеджеров в расчёте / нед):
    p = min(1, λ/f) по сезону, как Draw этапа 1; kg — кг заказа, если клиент заказал (средний за год):
    по нему быстрая оценка парка режет рейсы по тоннажу."""
    low, year = model.low, model.year
    p_low = dm.visit_probability(low.lam, f_total) if low.values else 0.0
    mean = low.mean_revenue
    square = fmean(v[0] * v[0] for v in low.values) if low.values else 0.0
    mu = p_low * mean
    p_year = dm.visit_probability(year.lam, f_total) if year.values else 0.0
    return sr.VisitParams(mu=mu, var=max(0.0, p_low * square - mu * mu), p_low=p_low,
                          p_year=p_year, kg=year.mean_kg if year.values else 0.0)


def _slots(p: pt.Pattern) -> sr.SlotPattern:
    return tuple(sorted(sr.slot_of_day(w, d) for w, d in p))


def _pairs_of(slots: Sequence[int]) -> pt.Pattern:
    return tuple(sorted(sr.day_of_slot(j) for j in slots))


def _matrices(home: Point | None, points: Sequence[Point],
              norms: ev.Norms) -> tuple[sr.Matrix, sr.Matrix]:
    """Матрицы менеджера: вершина 0 — дом (без дома — нули: открытый путь, как на этапе 1). Км —
    norms.km (по дорогам, иначе по прямой × извилистость); минуты — км / скорость участка (город,
    если оба конца в городе)."""
    pts: list[Point | None] = [home, *points]
    n = len(pts)
    city = [p is not None and in_city(p, norms.city_center, norms.city_radius_km) for p in pts]
    km = [[0.0] * n for _ in range(n)]
    mins = [[0.0] * n for _ in range(n)]
    for a in range(n):
        pa = pts[a]
        if pa is None:
            continue
        for b in range(a + 1, n):
            d = norms.km(pa, pts[b])
            speed = norms.speed_city_kmh if city[a] and city[b] else norms.speed_region_kmh
            km[a][b] = km[b][a] = d
            mins[a][b] = mins[b][a] = d / speed * 60.0
    return km, mins


def truck_u(customer_id: int) -> tuple[tuple[float, ...], ...]:
    """Числа «заказал» визитов клиента для парка (визит k, сценарий s). Seed — клиент, а не менеджер:
    у кого бы ни был клиент, флаги те же (общие случайные числа «было → стало» и при передаче)."""
    return sr.make_u(random.Random(zlib.crc32(f'{customer_id}|truck'.encode('utf-8'))))


# --- Парк машин (fleet-plan §3) ---

@dataclass
class _Fleet:
    """Парк расчёта: машины, вершины быстрой оценки (клиент и его точка; 0 — склад), веса и визиты
    менеджеров в расчёте, которых поиск не меняет (static: клиент, вершина, слоты)."""
    trucks: list[fl.FleetTruck]
    node: dict[tuple[int, Point], int]
    km: sr.Matrix
    city: list[bool]
    kg: list[float]
    kw: dict[str, float]
    static: list[tuple[int, int, sr.SlotPattern]]

    def gnode(self, customer_id: int, point: Point | None) -> int:
        return self.node.get((customer_id, point), 0) if point is not None else 0

    def estimate(self, entries: Iterable[tuple[int, int, Sequence[bool]]],
                 visit: Callable[[int], sr.VisitParams]) -> sr.FleetEstimate:
        """Быстрая оценка парка с визитами entries и static (параметры static — visit(клиент))."""
        static = []
        for c, g, slots in self.static:
            u, p = truck_u(c), visit(c).p_year
            static += [(j, g, tuple(x < p for x in u[k])) for k, j in enumerate(slots)]
        est = sr.FleetEstimate(self.km, self.city, self.kg, **self.kw)
        est.build(entries, static)
        return est


def _fleet_setup(ctx: _Ctx, run_ids: Sequence[int]) -> _Fleet | None:
    """Парк для поиска: нужны склад и хотя бы одна активная машина с тоннажем и расходом (иначе
    дизель грузовиков в стоимость не входит). Вершины — клиенты всех менеджеров в расчёта: их заказы
    везут те же машины."""
    snap, bundle, before = ctx.snap, ctx.bundle, ctx.before
    s = bundle.settings
    norms = before.norms
    trucks, _ = fl.fleet_trucks(bundle.trucks, {code: car.name for code, car in snap.cars.items()})
    if bundle.depot is None or not trucks:
        return None
    keys: set[tuple[int, Point]] = set()
    visits: dict[int, list[tuple[int, Point | None, pt.Pattern]]] = {}
    for a in sorted(before.included_ids):
        for c, info in sorted(ctx.pairs.get(a, {}).items()):
            point = ctx.coord(c, info.address_id).point
            visits.setdefault(a, []).append((c, point, info.pattern))
            if point is not None:
                keys.add((c, point))
    order = sorted(keys)
    node = {key: n + 1 for n, key in enumerate(order)}
    points = [p for _, p in order]
    if ctx.dist is None:
        ctx.dist = _Distances(points, norms)
    km = ctx.dist.rows(bundle.depot, points)
    depot_city = in_city(bundle.depot, norms.city_center, norms.city_radius_km)
    city = [depot_city] + [ctx.dist.city[ctx.dist.index[p]] for p in points]
    kg = [0.0] + [before.models[c].year.mean_kg if before.models[c].year.values else 0.0 for c, _ in order]
    tn = fl.TruckNorms.from_settings(s)
    l100 = fmean(t.l100 for t in trucks)
    kw = dict(capacity_kg=max(t.capacity_kg for t in trucks),
              per_km=float(s['truck_priority']) * _fuel_price(ctx, 'diesel') * l100 / 100.0,
              overtime_per_min=float(s['penalty_overtime_per_min']),
              capacity_min=len(trucks) * tn.work_minutes, unload_stop=tn.unload_min_per_stop,
              unload_tonne=tn.unload_min_per_tonne, speed_city_kmh=norms.speed_city_kmh,
              speed_region_kmh=norms.speed_region_kmh, trip_minutes=tn.work_minutes)
    run = set(run_ids)
    static = [(c, node[(c, p)], _slots(pattern)) for a, vs in sorted(visits.items()) if a not in run
              for c, p, pattern in vs if p is not None]
    return _Fleet(trucks, node, km, city, kg, kw, static)


# --- Расчёт ---

@dataclass
class ManagerOutcome:
    """Итог по менеджеру для проверок (tests/routes_optimize_check.py). cost_before / cost_after —
    свои слагаемые менеджера (без дизеля парка: он общий — RunOutcome)."""
    agent_id: int
    code: str
    customers: list[int]
    specs: list[PairSpec]
    final: list[pt.Pattern]
    cost_before: float
    cost_after: float
    stats: sr.SearchStats
    moved_to: dict[int, int] = field(default_factory=dict)   # режим Б: клиент → кому передан


@dataclass
class RunOutcome:
    """cost_before / cost_after — быстрая оценка компании, драм в неделю: Σ менеджеров + дизель парка
    (fleet_before / fleet_after; None — парк не в стоимости)."""
    result: dict[str, Any]
    managers: list[ManagerOutcome]
    before: ev.PlanEvaluation
    after: ev.PlanEvaluation
    transfer: tr.TransferStats | None = None                 # режим Б: поиск передач
    cost_before: float = 0.0
    cost_after: float = 0.0
    fleet_before: float | None = None
    fleet_after: float | None = None


@dataclass
class _Ctx:
    snap: Snapshot
    bundle: Bundle
    params: dict[str, Any]
    before: ev.PlanEvaluation
    book: DecisionBook
    pairs: dict[int, dict[int, PairInfo]]
    specs: dict[tuple[int, int], PairSpec]
    freq_before: dict[int, float]
    freq_after: dict[int, float]
    lam: dict[int, float]
    lam_season: dict[int, float]      # max(λ_год, λ_низкий сезон, λ_пик) — по ней f_sales
    abc: dict[int, str]
    clock: Callable[[], float]
    params_cache: dict[tuple[int, float], sr.VisitParams]
    fuel_sources: list[tuple[str, float, str]]
    fleet: _Fleet | None = None                  # парк в стоимости (склад и машины заданы)
    dist: _Distances | None = None               # расстояния между клиентами расчёта (парк, режим Б)
    fleet_before: sr.FleetEstimate | None = None  # парк текущего плана — эффект изменений
    fleet_a: sr.FleetEstimate | None = None       # парк итога режима А — старт режима Б

    def visit(self, cid: int, f_total: float) -> sr.VisitParams:
        key = (cid, round(f_total, 9))
        hit = self.params_cache.get(key)
        if hit is None:
            hit = self.params_cache[key] = visit_params(self.before.models[cid], f_total)
        return hit

    def coord(self, cid: int, address_id: int) -> Coord:
        return self.before.coords.get((cid, address_id)) or ev.visit_coord(self.snap, cid, address_id, self.bundle.geo_overrides)


def run_optimization(snap: Snapshot, bundle: Bundle, calib: ev.Calibration | None,
                     decisions: Sequence[Decision], params: Mapping[str, Any], *,
                     progress: ProgressFn | None = None,
                     clock: Callable[[], float] = time.perf_counter,
                     roads: RoadDistances | None = None) -> RunOutcome:
    """Режим А по выбранным менеджерам → результат §10.1 (статусы решений — на момент расчёта);
    params['mode'] = 'transfer' — затем режим Б (передача магазинов, _run_transfer). roads — расстояния
    по дорогам (None — по прямой × извилистость). Принятые передачи режим А не учитывает: магазин
    считается у прежнего менеджера (в план для ERP передача входит).

    Дизель парка общий: менеджеры связаны через дни доставки. Поиск — по менеджерам по очереди
    (Гаусс–Зейдель): каждый ищет при текущих днях остальных (уже найденных — их итог, ещё нет —
    старт), после него туры парка проходят 2-opt."""
    started = clock()
    s = bundle.settings
    params = {**DEFAULT_PARAMS, **params}
    pairs = plan_pairs(snap.plan)
    run_ids, error = resolve_agents(snap, bundle, params['agent_ids'])
    if error:
        raise OptimizeError(error)
    book = DecisionBook.from_rows(decisions, pairs)   # устаревшие — не закрепляют, а показываются
    before = ev.evaluate_plan(snap, bundle, calib, run_ids, roads=roads)
    roads = before.norms.roads   # None, если граф не собрался
    included = before.included_ids
    models = before.models

    # Частоты (§2): ABC — по клиентам планов всех менеджеров в расчёте; частота по продажам —
    # по самому высокому спросу из года, низкого сезона и пика: ни в один сезон заказы не теряются.
    # У затихших, потерянных и без заказов λ = 0 (§15): класс C, по продажам — раз в 2 недели
    inc_customers = sorted({c for a in included for c in pairs.get(a, {})})
    lam = {c: models[c].year.lam for c in inc_customers}
    lam_season = {c: season_lam(models[c]) for c in inc_customers}
    abc = fq.abc_classes({c: lam[c] * models[c].year.mean_revenue for c in inc_customers},
                         s['abc_a_share'], s['abc_b_share'])
    f_sales = {c: fq.sales_frequency(lam_season[c], abc[c], s['freq_safety']) for c in inc_customers}
    specs = {(a, c): pair_spec((a, c), info.pattern, f_sales[c], params['frequencies'],
                               s['workdays'], book, _status_code(models[c]))
             for a in run_ids for c, info in pairs.get(a, {}).items()}
    freq_before = snap.plan.visits_per_week_among(included)
    freq_after: dict[int, float] = Counter()
    for a in included:
        for c, info in pairs.get(a, {}).items():
            spec = specs.get((a, c))
            freq_after[c] += len(spec.allowed[0] if spec else info.pattern) / pt.CYCLE_WEEKS
    ctx = _Ctx(snap, bundle, params, before, book, pairs, specs, freq_before, dict(freq_after),
               lam, lam_season, abc, clock, {}, [])
    ctx.fleet = _fleet_setup(ctx, run_ids)

    evals = {me.agent_id: me for me in before.evals}
    tasks = [_manager_task(ctx, a, evals[a]) for a in run_ids]
    fleet = None
    if ctx.fleet is not None:
        ctx.fleet_before = ctx.fleet.estimate([e for t in tasks for e in t.before_state.fleet_entries()],
                                              lambda c: ctx.visit(c, ctx.freq_before[c]))
        fleet = ctx.fleet.estimate([e for t in tasks for e in t.state.fleet_entries()],
                                   lambda c: ctx.visit(c, ctx.freq_after[c]))
        for t in tasks:
            t.before_state.attach(ctx.fleet_before)
            t.state.attach(fleet)
    outcomes: list[ManagerOutcome] = []
    parts: dict[int, dict[str, Any]] = {}
    for n, t in enumerate(tasks):
        if progress:
            progress(n, len(run_ids), _code(snap, t.agent_id))
        outcome, part = _search_manager(ctx, t)
        outcomes.append(outcome)
        parts[t.agent_id] = part
    if progress:
        progress(len(run_ids), len(run_ids), None)
    ctx.fleet_a = fleet
    fleet_before = ctx.fleet_before.total if ctx.fleet_before is not None else None
    fleet_after = fleet.total if fleet is not None else None
    if fleet is not None:
        logger.info('[Routes] Парк (быстрая оценка): %.0f → %.0f драм/нед', fleet_before, fleet_after)
    if params.get('mode') == MODE_TRANSFER:   # режим Б: старт — результат режима А
        return _run_transfer(ctx, calib, run_ids, evals, outcomes, parts, started)

    # «Стало»: предложенный план (W = 2) тем же полным оценщиком; остальные менеджеры — как есть
    after_plan = _after_plan(snap.plan, {o.agent_id: o for o in outcomes}, pairs, s['workdays'])
    after_snap = replace(snap, plan=after_plan)
    f_after_inc = after_plan.visits_per_week_among(included)
    after_models = {c: replace(m, visits_per_week=f_after_inc.get(c, 0.0)) for c, m in models.items()}
    after = ev.evaluate_plan(after_snap, bundle, calib, run_ids,
                             visit_minutes=before.visit_minutes, models=after_models, roads=roads)
    result = _result(ctx, run_ids, parts, after_snap, after, f_after_inc)
    result['seconds'] = round(clock() - started, 1)
    logger.info('[Routes] Оптимизация: менеджеров %d, изменений %d, %.1f с', len(run_ids),
                sum(len(m['changes']) for m in result['managers']), result['seconds'])
    return RunOutcome(result, outcomes, before, after,
                      cost_before=sum(o.cost_before for o in outcomes) + (fleet_before or 0.0),
                      cost_after=sum(o.cost_after for o in outcomes) + (fleet_after or 0.0),
                      fleet_before=fleet_before, fleet_after=fleet_after)


@dataclass
class _Task:
    """Задача менеджера режима А: состояние текущего плана (эффект изменений) и старт поиска."""
    agent_id: int
    me: ev.ManagerEval
    cids: list[int]
    specs: list[PairSpec]
    points: list[Point | None]
    prob: sr.Problem
    before_state: sr.State
    state: sr.State
    fresh: bool


def _manager_task(ctx: _Ctx, agent_id: int, me: ev.ManagerEval) -> _Task:
    s = ctx.bundle.settings
    norms = ctx.before.norms
    info = ctx.pairs.get(agent_id, {})
    cids = sorted(info)
    specs = [ctx.specs[(agent_id, c)] for c in cids]
    points = [ctx.coord(c, info[c].address_id).point for c in cids]
    located = [i for i, p in enumerate(points) if p is not None]
    node_of = {i: n + 1 for n, i in enumerate(located)}
    km, mins = _matrices(me.home, [points[i] for i in located], norms)
    weights = _weights(ctx, agent_id, me)

    workdays = set(s['workdays'])
    base_slots = {sr.slot_of_day(w, d) for spec in specs for w, d in spec.current if d in workdays}
    lines = [sr.Line(customer_id=c, node=node_of.get(i, 0),
                     minutes=ctx.before.visit_minutes[ctx.before.models[c].size],
                     current=_slots(spec.current), allowed=tuple(_slots(p) for p in spec.allowed),
                     locked=spec.locked, u=truck_u(c),
                     gnode=ctx.fleet.gnode(c, points[i]) if ctx.fleet is not None else 0)
             for i, (c, spec) in enumerate(zip(cids, specs))]
    prob = sr.Problem(
        agent_id=agent_id, lines=lines, km=km, mins=mins, weights=weights,
        workday=tuple(sr.day_of_slot(j)[1] in workdays for j in range(sr.SLOTS)),
        base=tuple(j in base_slots for j in range(sr.SLOTS)),
        neighbors=sr.nearest_lines([line.node for line in lines], km),
        seed=zlib.crc32(str(agent_id).encode('utf-8')))

    before_params = [ctx.visit(c, ctx.freq_before[c]) for c in cids]
    target_params = [ctx.visit(c, ctx.freq_after[c]) for c in cids]
    before_state = sr.State(prob, [line.current for line in lines], before_params, change_penalty=0.0)
    fresh = ctx.params['start'] == 'fresh'
    penalty = 0.0 if fresh else float(s['penalty_change'])
    if fresh:   # жадная вставка — в свою очередь поиска (парк уже с днями остальных)
        start = [line.allowed[0] if line.locked else () for line in lines]
    else:
        # старт — текущие шаблоны, визиты нерабочих дней — на субботе той же недели (Р3-9)
        start = sr.current_start(prob, [_slots(pt.workday_pattern(spec.current, workdays)) for spec in specs])
    state = sr.State(prob, start, target_params, change_penalty=penalty)
    return _Task(agent_id, me, cids, specs, points, prob, before_state, state, fresh)


def _search_manager(ctx: _Ctx, task: _Task) -> tuple[ManagerOutcome, dict[str, Any]]:
    """Поиск менеджера при текущих днях остальных (парк общий), затем 2-opt туров парка."""
    s = ctx.bundle.settings
    agent_id, state, before_state = task.agent_id, task.state, task.before_state
    state.refresh()
    if task.fresh:
        sr.greedy_fill(state, sr.fresh_order(task.prob, task.me.home, task.points))
    stats = sr.search(state, seconds=float(s['optimizer_seconds_per_manager']), clock=ctx.clock)
    if state.fleet is not None:
        state.fleet.polish()
        state.refresh()
    final = [_pairs_of(p) for p in state.pattern]

    changes, hints = _day_changes(ctx, agent_id, task.cids, task.specs, final, state.pattern, before_state,
                                  ctx.fleet is not None)
    _sort_changes(changes)

    code = _code(ctx.snap, agent_id)
    logger.info('[Routes] Оптимизация %s: клиентов %d, C %.0f → %.0f драм/нед (старт %.0f; с парком), '
                'ходов %d, возмущений %d, %.1f с%s', code, len(task.cids), before_state.total,
                state.total, stats.cost_start, stats.accepted, stats.perturbations, stats.seconds,
                ' — СТОП ПО ВРЕМЕНИ' if stats.time_capped else '')
    outcome = ManagerOutcome(agent_id, code, task.cids, task.specs, final, before_state.own_total(),
                             state.own_total(), stats)
    info = ctx.pairs.get(agent_id, {})
    part = {'changes': changes, 'hints': hints, 'time_capped': stats.time_capped,
            'cost_before': before_state.own_total(), 'cost_after': state.own_total(),
            'final': dict(zip(task.cids, final)), 'addresses': {c: info[c].address_id for c in task.cids},
            'state': state}   # режим Б начинает с туров этого состояния (в результат не попадает)
    return outcome, part


def _weights(ctx: _Ctx, agent_id: int, me: ev.ManagerEval) -> sr.Weights:
    """Веса стоимости менеджера (§4): топливо менеджера — по настройкам или запасной цене. Дизель
    парка — общий (_fleet_setup)."""
    s = ctx.bundle.settings
    norms = ctx.before.norms
    profile = ctx.bundle.profile(agent_id)
    l100 = (profile.car_fuel_l_per_100km if profile.car_fuel_l_per_100km is not None
            else float(s['manager_car_default_l_per_100km']))
    price = _fuel_price(ctx, me.fuel_type)
    return sr.Weights(
        manager_per_km=price * l100 / 100.0,
        weak_day=float(s['penalty_weak_day']), poor_trip=float(s['penalty_poor_trip']),
        overtime_per_min=float(s['penalty_overtime_per_min']), window_min=norms.work_minutes,
        min_day_revenue=norms.min_day_revenue, min_trip_revenue=norms.min_trip_revenue)


def _sort_changes(changes: list[dict[str, Any]]) -> None:
    """По первому дню «стало»; «убрать из маршрута» (дней нет) — в конце."""
    changes.sort(key=lambda ch: (ch['to']['pattern'][0] if ch['to']['pattern'] else [pt.CYCLE_WEEKS + 1, 0],
                                 ch['customer_id']))


def _day_changes(ctx: _Ctx, agent_id: int, cids: Sequence[int], specs: Sequence[PairSpec],
                 final: Sequence[pt.Pattern], final_slots: Sequence[sr.SlotPattern],
                 before_state: sr.State, truck_known: bool,
                 skip: Collection[int] = ()) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Предложения менеджера по дням и частоте (§8) и подсказки: i-я строка before_state — клиент
    cids[i], final — его шаблон «стало». skip — клиенты, у которых предложение другое (передача)."""
    s = ctx.bundle.settings
    before = ctx.before
    workdays = set(s['workdays'])
    changes = []
    hints = []
    for i, c in enumerate(cids):
        spec = specs[i]
        status = before.models[c].status
        # подсказки по продажам — не у затихших и потерянных; «за год ни одного заказа» — если
        # клиента не предлагается убрать (режим «как сейчас» или владелец оставил его)
        if status is None or not status.silent or (status.status == cst.NEVER and not spec.removed):
            for kind, text in fq.frequency_hints(ctx.lam[c], ctx.freq_before[c], s['freq_safety']):
                hints.append({'customer_id': c, 'kind': kind, 'text': text})
        if c in skip:
            continue
        ctype = 'remove' if spec.removed else pt.change_type(spec.current, final[i])
        if ctype is None:
            continue
        f_cur, f_new = pt.pattern_freq(spec.current), pt.pattern_freq(final[i])
        # эффект — этого изменения, применённого к текущему плану: частота клиента меняется только у него
        f_total = max(0.0, ctx.freq_before[c] - f_cur + f_new)
        effect = sr.change_effect(before_state, i, final_slots[i], ctx.visit(c, f_total))
        change = {
            'customer_id': c, 'type': ctype,
            'from': {'freq': _num(f_cur), 'pattern': pt.pattern_json(spec.current),
                     'text': pt.pattern_text(spec.current)},
            'to': {'freq': _num(f_new), 'pattern': pt.pattern_json(final[i]),
                   'text': cst.REMOVE_TEXT if spec.removed else pt.pattern_text(final[i])},
            'reason': _reason(spec, f_cur, f_new, ctx.lam[c], ctx.lam_season[c], workdays, status),
            'effect': {
                'manager_km_week': round(effect['km'], 1),
                'truck_km_week': round(effect['truck_km'], 1) if truck_known else None,
                'weak_days_week': round(effect['weak'], 2),
                'minutes_week': int(round(effect['minutes'])),
            },
        }
        change['decision'] = ctx.book.change_status(agent_id, change)
        changes.append(change)
    return changes, hints


# --- Режим Б: передача магазинов между менеджерами (этап 4) ---

TRANSFER_MAX_SECONDS = 60.0      # страховочный предел поиска передач: весь расчёт — не больше 2 мин
TRANSFER_HOME_CANDIDATES = 3     # кандидаты передачи: + 3 менеджера, чей дом ближе всего к клиенту
TRANSFER_NEIGHBORS = 10          # ближайшие клиенты в радиусе — партнёры обмена
TRANSFER_GROUP_RADIUS = 3.0      # передача группой: клиенты того же менеджера в те же дни — в 3 радиусах
MONTH_DAYS = 365.25 / 12
TRANSFER_REASONS = {
    'km': 'в этот район уже ездит {to} — меньше км',
    'weak': 'у {to} в этот день не хватает заказов — день станет сильнее',
    'overload': 'у {frm} перегружен день — станет короче',
    None: 'отдельно почти ничего не меняет — выгода вместе с другими изменениями',
    'owner': 'передача принята владельцем',
}


class _Grid:
    """Точки по клеткам не меньше радиуса: соседи в радиусе (по прямой) — из 3 × 3 клеток."""

    def __init__(self, items: Iterable[tuple[Any, Point]], radius_km: float):
        self.radius = radius_km
        self.dlat = radius_km / 111.0
        self.dlon = radius_km / 80.0   # 1° долготы в Армении (широта 38,8–41,4) — больше 83 км
        self.cells: dict[tuple[int, int], list[tuple[Any, Point]]] = defaultdict(list)
        for key, p in items:
            self.cells[self._cell(p)].append((key, p))

    def _cell(self, p: Point) -> tuple[int, int]:
        return math.floor(p[0] / self.dlat), math.floor(p[1] / self.dlon)

    def near(self, p: Point) -> list[tuple[float, Any]]:
        ci, cj = self._cell(p)
        out = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for key, q in self.cells.get((ci + di, cj + dj), ()):
                    d = haversine_km(p, q)
                    if d < self.radius:
                        out.append((d, key))
        return out


class _Distances:
    """Км и минуты между всеми точками клиентов расчёта (norms.km — по дорогам или по прямой ×
    извилистость, как _matrices) — один раз; матрицы менеджеров режима Б (вершина 0 — дом) и матрица
    парка (вершина 0 — склад) собираются из них построчно. Строки — array('d'): у менеджера до тысячи
    гостей, и списки чисел заняли бы в несколько раз больше памяти."""

    def __init__(self, points: Iterable[Point], norms: ev.Norms):
        self.norms = norms
        pts = sorted(set(points))
        self.index = {p: i for i, p in enumerate(pts)}
        n = len(pts)
        self.city = [in_city(p, norms.city_center, norms.city_radius_km) for p in pts]
        zero = bytes(8 * n)
        self.km = [array('d', zero) for _ in range(n)]
        self.mins = [array('d', zero) for _ in range(n)]
        for a in range(n):
            pa, ka, ma, ca = pts[a], self.km[a], self.mins[a], self.city[a]
            for b in range(a + 1, n):
                d = norms.km(pa, pts[b])
                m = d / (norms.speed_city_kmh if ca and self.city[b] else norms.speed_region_kmh) * 60.0
                ka[b] = self.km[b][a] = d
                ma[b] = self.mins[b][a] = m

    def _get(self, points: Sequence[Point]) -> tuple[list[int], Callable[[array], tuple[float, ...]]]:
        gi = [self.index[p] for p in points]
        if len(gi) == 1:
            def get(row: array) -> tuple[float, ...]:
                return (row[gi[0]],)
            return gi, get
        return gi, (itemgetter(*gi) if gi else (lambda row: ()))

    def rows(self, start: Point, points: Sequence[Point]) -> sr.Matrix:
        """Км между start (вершина 0) и точками points (1..n) — матрица парка от склада."""
        gi, get = self._get(points)
        dk = [self.norms.km(start, p) for p in points]
        return [array('d', [0.0, *dk])] + [array('d', (dk[r], *get(self.km[g]))) for r, g in enumerate(gi)]

    def matrices(self, home: Point | None, points: Sequence[Point]) -> tuple[sr.Matrix, sr.Matrix]:
        """То же, что _matrices(home, points, norms), из готовых расстояний."""
        norms = self.norms
        gi = [self.index[p] for p in points]
        if home is not None:
            hk = [norms.km(home, p) for p in points]
            home_city = in_city(home, norms.city_center, norms.city_radius_km)
            hm = [d / (norms.speed_city_kmh if home_city and self.city[g] else norms.speed_region_kmh) * 60.0
                  for d, g in zip(hk, gi)]
        else:   # без дома — нули: открытый путь, как на этапе 1
            hk = hm = [0.0] * len(gi)
        km = [array('d', [0.0, *hk])]
        mins = [array('d', [0.0, *hm])]
        _, get = self._get(points)
        for r, g in enumerate(gi):
            km.append(array('d', (hk[r], *get(self.km[g]))))
            mins.append(array('d', (hm[r], *get(self.mins[g]))))
        return km, mins


def revenue_month(model: ev.CustomerModel) -> float:
    """Выручка клиента в среднем за месяц — по истории заказов за 12 месяцев (с первого заказа, если
    он был позже), как λ: экспозиция — не меньше 3 недель. У затихших — сколько брали, пока покупали."""
    dem = model.year_hist
    if not dem.values:
        return 0.0
    return sum(v[0] for v in dem.values) / max(dem.exposure_days, dm.MIN_EXPOSURE_DAYS) * MONTH_DAYS


def _transfer_effect_json(e: Mapping[str, float], truck_km: float | None) -> dict[str, Any]:
    """Эффект передачи; truck_km — Δ км парка (общий для обоих менеджеров; None — не посчитан)."""
    # + 0.0: «−0.0» после округления — просто 0
    return {'manager_km_week': round(e['km'], 1) + 0.0,
            'truck_km_week': round(truck_km, 1) + 0.0 if truck_km is not None else None,
            'weak_days_week': round(e['weak'], 2) + 0.0, 'minutes_week': int(round(e['minutes']))}


def _run_transfer(ctx: _Ctx, calib: ev.Calibration | None, run_ids: Sequence[int],
                  evals: Mapping[int, ev.ManagerEval], outcomes_a: Sequence[ManagerOutcome],
                  parts_a: Mapping[int, Mapping[str, Any]], started: float) -> RunOutcome:
    """Режим Б (план этапа 4, §3–§5): старт — результат режима А; клиентов можно передавать
    менеджерам-кандидатам (у кандидата в каком-то дне есть клиент ближе transfer_radius_km или его дом
    среди 3 ближайших), не передаются закреплённые, убираемые из маршрута и клиенты нескольких
    менеджеров; отклонённая передача (клиент → менеджер) запрещена, принятая — закреплена. «Было →
    стало» — полным оценщиком, как в режиме А; эффект передачи — к текущему плану, по обоим менеджерам."""
    snap, bundle, before, book = ctx.snap, ctx.bundle, ctx.before, ctx.book
    s = bundle.settings
    norms = before.norms
    run_set = set(run_ids)
    radius = float(s['transfer_radius_km'])
    t0 = ctx.clock()

    # точка клиента у менеджера — координата адреса шаблона ERP (как в режиме А)
    point: dict[tuple[int, int], Point | None] = {}
    for a in run_ids:
        for c, info in ctx.pairs.get(a, {}).items():
            key = (c, info.address_id)
            point[(a, c)] = (before.coords.get(key) or ev.visit_coord(snap, *key, bundle.geo_overrides)).point
    owners = Counter(c for a in before.included_ids for c in ctx.pairs.get(a, {}))

    def eligible(a: int, c: int) -> bool:
        spec = ctx.specs[(a, c)]
        return owners[c] == 1 and point[(a, c)] is not None and not spec.removed

    # принятые передачи: клиент закреплён у нового менеджера в принятых днях
    fixed: dict[int, tuple[int, int, pt.Pattern]] = {}
    for (a, c), (b, p) in sorted(book.accepted_transfer.items()):
        if a in run_set and b in run_set and b != a and (a, c) in point and eligible(a, c):
            fixed[c] = (a, b, p)
    movable = [(a, c) for a in run_ids for c in sorted(ctx.pairs.get(a, {}))
               if c not in fixed and eligible(a, c) and not ctx.specs[(a, c)].locked]

    # кандидаты: менеджеры с клиентом в радиусе + 3 ближайших по дому, без отклонённых владельцем
    grid = _Grid(((a, p) for (a, _), p in sorted(point.items()) if p is not None), radius)
    homes = {a: evals[a].home for a in run_ids if evals[a].home is not None}
    cand: dict[int, list[int]] = {}
    near_of: dict[int, frozenset[int]] = {}   # кандидаты по соседям — для передачи группой
    for a, c in movable:
        p = point[(a, c)]
        rejected = book.rejected_transfer.get((a, c), frozenset())
        near = {b for _, b in grid.near(p) if b != a} - rejected
        by_home = sorted((haversine_km(p, h), b) for b, h in homes.items() if b != a)
        mgrs = (near | {b for _, b in by_home[:TRANSFER_HOME_CANDIDATES]}) - rejected
        if mgrs:
            cand[c] = sorted(mgrs)
            near_of[c] = frozenset(near)
    origin = {c: a for a, c in movable if c in cand}
    origin.update({c: v[0] for c, v in fixed.items()})
    guests: dict[int, set[int]] = defaultdict(set)
    for c, mgrs in cand.items():
        for b in mgrs:
            guests[b].add(c)
    for c, (_, b, _) in fixed.items():
        guests[b].add(c)
    client_grid = _Grid(((c, point[(origin[c], c)]) for c in sorted(cand)), radius)
    neighbors = {c: [x for _, x in sorted(client_grid.near(point[(origin[c], c)])) if x != c][:TRANSFER_NEIGHBORS]
                 for c in sorted(cand)}
    group_grid = _Grid(((c, point[(origin[c], c)]) for c in sorted(cand)), radius * TRANSFER_GROUP_RADIUS)
    around = {c: [x for _, x in sorted(group_grid.near(point[(origin[c], c)])) if x != c] for c in sorted(cand)}

    # задачи менеджеров: свои клиенты (как в режиме А) + гости — пока без визитов
    fresh = ctx.params['start'] == 'fresh'
    change_penalty = 0.0 if fresh else float(s['penalty_change'])
    workdays = set(s['workdays'])
    if ctx.dist is None:   # без парка расстояния ещё не считались
        ctx.dist = _Distances((p for p in point.values() if p is not None), norms)
    dist = ctx.dist
    final_a = {o.agent_id: dict(zip(o.customers, o.final)) for o in outcomes_a}
    own_of: dict[int, list[int]] = {}
    index_of: dict[int, dict[int, int]] = {}
    weights_of: dict[int, sr.Weights] = {}
    before_states: dict[int, sr.State] = {}
    states: dict[int, sr.State] = {}
    for a in run_ids:
        me = evals[a]
        own = sorted(ctx.pairs.get(a, {}))
        gs = sorted(guests.get(a, ()))
        cids = own + gs
        specs = [ctx.specs[(a, c)] for c in own] + [ctx.specs[(origin[c], c)] for c in gs]
        pts = [point[(a, c)] for c in own] + [point[(origin[c], c)] for c in gs]
        located = [i for i, p in enumerate(pts) if p is not None]
        node_of = {i: n + 1 for n, i in enumerate(located)}
        km, mins = dist.matrices(me.home, [pts[i] for i in located])
        weights = _weights(ctx, a, me)
        base_slots = {sr.slot_of_day(w, d) for spec in specs[:len(own)] for w, d in spec.current
                      if d in workdays}
        lines = []
        for i, (c, spec) in enumerate(zip(cids, specs)):
            guest = i >= len(own)
            if guest and c in fixed:   # принятая передача: у нового менеджера — принятые дни
                allowed, locked = (_slots(fixed[c][2]),), True
            else:
                allowed, locked = tuple(_slots(x) for x in spec.allowed), spec.locked
            lines.append(sr.Line(
                customer_id=c, node=node_of.get(i, 0), minutes=before.visit_minutes[before.models[c].size],
                current=() if guest else _slots(spec.current), allowed=allowed, locked=locked,
                u=truck_u(c), gnode=ctx.fleet.gnode(c, pts[i]) if ctx.fleet is not None else 0))
        prob = sr.Problem(
            agent_id=a, lines=lines, km=km, mins=mins, weights=weights,
            workday=tuple(sr.day_of_slot(j)[1] in workdays for j in range(sr.SLOTS)),
            base=tuple(j in base_slots for j in range(sr.SLOTS)),
            neighbors=sr.nearest_lines([line.node for line in lines], km),
            seed=zlib.crc32(str(a).encode('utf-8')))
        # парк «было» — тот же, что в режиме А: гости без визитов, свои — текущие дни
        before_states[a] = sr.State(prob, [line.current for line in lines],
                                    [ctx.visit(c, ctx.freq_before[c]) for c in cids],
                                    change_penalty=0.0, fleet=ctx.fleet_before)
        start = [() if c in fixed else _slots(final_a[a][c]) for c in own]
        start += [_slots(fixed[c][2]) if c in fixed else () for c in gs]
        # частота клиента одна у любого менеджера; у принятой передачи — по принятым дням
        target = [ctx.visit(c, pt.pattern_freq(fixed[c][2]) if c in fixed else ctx.freq_after[c])
                  for c in cids]
        states[a] = sr.State(prob, start, target, change_penalty=change_penalty)
        states[a].adopt_tours(parts_a[a]['state'])   # своя нумерация та же: старт = итог режима А
        own_of[a] = own
        index_of[a] = {c: i for i, c in enumerate(cids)}
        weights_of[a] = weights
    if ctx.fleet is not None:   # парк старта — по дням старта (с принятыми передачами); туры — режима А
        fleet = ctx.fleet.estimate([e for a in run_ids for e in states[a].fleet_entries()],
                                   lambda c: ctx.visit(c, ctx.freq_after[c]))
        fleet.adopt(ctx.fleet_a)
        for a in run_ids:
            states[a].attach(fleet)

    clients = []
    for c in sorted(origin):
        a = origin[c]
        if c in fixed:
            mgrs, fx = [fixed[c][1]], fixed[c][1]
        else:
            mgrs, fx = cand[c], None
        clients.append(tr.Client(c, a, {m: index_of[m][c] for m in (a, *mgrs)}, fx, near_of.get(c, frozenset())))
    market = tr.Market(states, clients, neighbors, float(s['penalty_transfer']), around)
    budget = min(float(s['optimizer_seconds_per_manager']) * len(run_ids), TRANSFER_MAX_SECONDS)
    seed = zlib.crc32(('transfer|' + ','.join(map(str, run_ids))).encode('utf-8'))
    setup_seconds = ctx.clock() - t0
    tstats = tr.search(market, seconds=budget, seed=seed, clock=ctx.clock)

    # итог по менеджерам: свои клиенты — предложения по дням; переданные — предложение «передать»
    finals = {a: {c: _pairs_of(states[a].pattern[i]) for c, i in index_of[a].items() if states[a].pattern[i]}
              for a in run_ids}
    balance = {a: {'given': [0, 0.0, 0.0], 'received': [0, 0.0, 0.0]} for a in run_ids}
    outcomes: list[ManagerOutcome] = []
    parts: dict[int, dict[str, Any]] = {}
    by_agent_a = {o.agent_id: o for o in outcomes_a}
    for a in run_ids:
        own = own_of[a]
        specs = [ctx.specs[(a, c)] for c in own]
        moved = {c: market.owner[c] for c in own if market.owner.get(c, a) != a}
        final = [finals[moved.get(c, a)].get(c, ()) for c in own]
        changes, hints = _day_changes(ctx, a, own, specs, final, states[a].pattern[:len(own)],
                                      before_states[a], ctx.fleet is not None, skip=moved)
        for c, b in sorted(moved.items()):
            change = _transfer_change(ctx, c, a, b, before_states, states, index_of, weights_of, fixed)
            changes.append(change)
            for side, agent in (('given', a), ('received', b)):
                row = balance[agent][side]
                row[0] += 1
                row[1] += change['_revenue']
                row[2] += change['_debt']
            del change['_revenue'], change['_debt']
        _sort_changes(changes)
        o = by_agent_a[a]
        outcomes.append(ManagerOutcome(a, o.code, own, specs, final, before_states[a].own_total(),
                                       states[a].own_total(), o.stats, moved_to=moved))
        parts[a] = {'changes': changes, 'hints': hints,
                    'time_capped': parts_a[a]['time_capped'] or tstats.time_capped,
                    'cost_before': before_states[a].own_total(), 'cost_after': states[a].own_total()}

    # «Стало»: у каждого менеджера — кто у него сейчас (адрес — из шаблона ERP прежнего менеджера)
    address = {(a, c): info.address_id for a in run_ids for c, info in ctx.pairs.get(a, {}).items()}
    for c, a in origin.items():
        for b in run_ids:
            address.setdefault((b, c), address[(a, c)])
    base_days = {a: {(w, d) for c in own_of[a] for w, d in ctx.specs[(a, c)].current if d in workdays}
                 for a in run_ids}
    after_plan = _after_plan_moved(snap.plan, finals, address, base_days)
    after_snap = replace(snap, plan=after_plan)
    included = before.included_ids
    f_after_inc = after_plan.visits_per_week_among(included)
    after_models = {c: replace(m, visits_per_week=f_after_inc.get(c, 0.0)) for c, m in before.models.items()}
    after = ev.evaluate_plan(after_snap, bundle, calib, run_ids, visit_minutes=before.visit_minutes,
                             models=after_models, roads=norms.roads)
    result = _result(ctx, run_ids, parts, after_snap, after, f_after_inc)
    result['params']['mode'] = MODE_TRANSFER

    def side(v: list) -> dict[str, Any]:
        return {'stores': v[0], 'revenue_month': _i(v[1]), 'debt': _i(v[2])}

    for m in result['managers']:
        m['balance'] = {k: side(v) for k, v in balance[m['agent_id']].items()}
    given = [balance[a]['given'] for a in run_ids]
    result['transfers'] = {
        'count': sum(v[0] for v in given), 'accepted_fixed': len(fixed),
        'revenue_month': _i(sum(v[1] for v in given)), 'debt': _i(sum(v[2] for v in given)),
        'candidates': len(clients), 'radius_km': radius, 'penalty_week': _num(float(s['penalty_transfer'])),
        'cost_days_only': _i(tstats.cost_start), 'cost': _i(tstats.cost_end),
        'time_capped': tstats.time_capped, 'seconds': round(tstats.seconds + setup_seconds, 1),
    }
    result['seconds'] = round(ctx.clock() - started, 1)
    logger.info('[Routes] Передачи: кандидатов %d, передач %d, C %.0f → %.0f драм/нед, подготовка %.1f с, '
                'поиск %.1f с (ходов %d, внутри менеджеров %d, возмущений %d)%s', len(clients),
                result['transfers']['count'], tstats.cost_start, tstats.cost_end, setup_seconds,
                tstats.seconds, tstats.accepted, tstats.intra_accepted, tstats.perturbations,
                ' — СТОП ПО ВРЕМЕНИ' if tstats.time_capped else '')
    fleet_before = ctx.fleet_before.total if ctx.fleet_before is not None else None
    fleet_after = market.fleet.total if market.fleet is not None else None
    return RunOutcome(result, outcomes, before, after, transfer=tstats,
                      cost_before=sum(o.cost_before for o in outcomes) + (fleet_before or 0.0),
                      cost_after=tstats.cost_end, fleet_before=fleet_before, fleet_after=fleet_after)


def _transfer_change(ctx: _Ctx, c: int, a: int, b: int, before_states: Mapping[int, sr.State],
                     states: Mapping[int, sr.State], index_of: Mapping[int, Mapping[int, int]],
                     weights_of: Mapping[int, sr.Weights], fixed: Mapping[int, Any]) -> dict[str, Any]:
    """Предложение «передать клиента c от a к b» (план §5): эффект — этой передачи к текущему плану по
    обоим менеджерам (Δ км, слабых дней, минут; км парка — общие), выручка в месяц и долг, которые
    переходят, причина простыми словами по главному эффекту. _revenue и _debt — неокруглённые, для
    баланса."""
    snap, s = ctx.snap, ctx.bundle.settings
    spec = ctx.specs[(a, c)]
    new_slots = states[b].pattern[index_of[b][c]]
    new = _pairs_of(new_slots)
    f_cur, f_new = pt.pattern_freq(spec.current), pt.pattern_freq(new)
    params = ctx.visit(c, max(0.0, ctx.freq_before[c] - f_cur + f_new))
    give = tr.side_effect(before_states[a], index_of[a][c], (), params)
    take = tr.side_effect(before_states[b], index_of[b][c], new_slots, params)
    total = {k: give[k] + take[k] for k in give}
    truck_km = tr.transfer_fleet_km(before_states[a], index_of[a][c], before_states[b], index_of[b][c],
                                    new_slots, params)
    kind = 'owner' if c in fixed else tr.main_effect(
        give, take, weights_of[a].manager_per_km, weights_of[b].manager_per_km,
        float(s['penalty_weak_day']), float(s['penalty_overtime_per_min']))

    def name(agent_id: int) -> str:
        agent = snap.agents.get(agent_id)
        return agent.name if agent and agent.name else _code(snap, agent_id)

    reason = TRANSFER_REASONS[kind].format(frm=name(a), to=name(b))
    freq_reason = _reason(spec, f_cur, f_new, ctx.lam[c], ctx.lam_season[c], set(s['workdays']),
                          ctx.before.models[c].status) if not spec.locked else None
    revenue = revenue_month(ctx.before.models[c])
    debt = snap.debts.get(c, 0.0)
    change = {
        'customer_id': c, 'type': 'transfer', 'from_agent': a, 'to_agent': b,
        'from': {'freq': _num(f_cur), 'pattern': pt.pattern_json(spec.current), 'text': pt.pattern_text(spec.current)},
        'to': {'freq': _num(f_new), 'pattern': pt.pattern_json(new), 'text': pt.pattern_text(new)},
        'reason': f'{reason}; {freq_reason}' if freq_reason and kind != 'owner' else reason,
        'reason_kind': kind,
        'revenue_month': _i(revenue), 'debt': _i(debt),
        'effect': _transfer_effect_json(total, truck_km),
        'effect_from': _transfer_effect_json(give, None),   # км парка — общие, в effect
        'effect_to': _transfer_effect_json(take, None),
        '_revenue': revenue, '_debt': debt,
    }
    change['decision'] = ctx.book.change_status(a, change)
    return change


def _after_plan_moved(plan: CurrentPlan, finals: Mapping[int, Mapping[int, pt.Pattern]],
                      address: Mapping[tuple[int, int], int],
                      base_days: Mapping[int, Collection[pt.Slot]]) -> CurrentPlan:
    """Предложенный план режима Б (цикл 2 недели): у менеджеров расчёта — те, кто у них «стало»
    (переданные — с адресом из шаблона прежнего менеджера), плюс пустые рабочие дни, в которые менеджер
    работает сейчас (такой день — слабый); остальные менеджеры — текущий план в обе недели."""
    days: list[PlanDay] = []
    for d in plan.days:
        if d.agent_id in finals:
            continue
        weeks = (1, 2) if plan.cycle_weeks == 1 else (d.week,)
        days.extend(PlanDay(d.agent_id, w, d.weekday, d.visits) for w in weeks)
    for agent_id, final in finals.items():
        by_slot: dict[pt.Slot, list[int]] = defaultdict(list)
        for c, p in final.items():
            for slot in p:
                by_slot[slot].append(c)
        for slot in base_days.get(agent_id, ()):
            by_slot.setdefault(slot, [])
        for (w, d), cs in by_slot.items():
            days.append(PlanDay(agent_id, w, d, tuple(PlanVisit(c, address[(agent_id, c)], n)
                                                      for n, c in enumerate(sorted(cs)))))
    days.sort(key=lambda x: (x.agent_id, x.week, x.weekday))
    counts = Counter(v.customer_id for d in days for v in d.visits)
    return CurrentPlan(pt.CYCLE_WEEKS, tuple(days),
                       {c: n / pt.CYCLE_WEEKS for c, n in counts.items()}, False)


def _fuel_price(ctx: _Ctx, fuel: str) -> float:
    """Цена топлива для веса в поиске: из настроек, иначе запасная (fuel_price_fallback)."""
    s = ctx.bundle.settings
    price = s.get(f'fuel_price_{fuel}')
    source = 'settings' if price is not None else 'fallback'
    value = float(price if price is not None else s['fuel_price_fallback'])
    ctx.fuel_sources.append((fuel, value, source))
    return value


def _reason(spec: PairSpec, f_cur: float, f_new: float, lam: float, lam_season: float,
            workdays: Collection[int], status: cst.CustomerStatus | None = None) -> str | None:
    silent = status is not None and status.silent
    if spec.removed:   # §15: «не покупает 150 дн (обычно раз в 14 дн)» / «ни одного заказа за год»
        if spec.source == fq.SOURCE_MANUAL:
            return 'удаление принято владельцем'
        return cst.silence_text(status) if silent else None
    if spec.locked:
        return 'шаблон принят владельцем'
    # визиты в нерабочий день переносятся всегда (Р3-9) — причина видна и при смене частоты
    off = pt.off_days_text(spec.current, workdays)
    if pt.same_freq(f_cur, f_new):
        return off
    if spec.source == fq.SOURCE_MANUAL:
        why = 'частота принята владельцем'
    elif silent:   # затих (или владелец оставил потерянного): раз в 2 недели — попробовать вернуть
        why = cst.win_back_text(status)
    else:
        why = fq.order_rate_text(lam, lam_season)
    return f'{off}; {why}' if off else why


def _after_plan(plan: CurrentPlan, outcomes: Mapping[int, ManagerOutcome],
                pairs: Mapping[int, Mapping[int, PairInfo]], workdays: Collection[int]) -> CurrentPlan:
    """Предложенный план, цикл 2 недели: у оптимизированных — новые шаблоны (плюс пустые рабочие
    дни, в которые менеджер работает сейчас: такой день — слабый); остальные — текущий план в обе
    недели. Визиты дня — по id клиента."""
    days: list[PlanDay] = []
    for d in plan.days:
        if d.agent_id in outcomes:
            continue
        weeks = (1, 2) if plan.cycle_weeks == 1 else (d.week,)
        days.extend(PlanDay(d.agent_id, w, d.weekday, d.visits) for w in weeks)
    wd = set(workdays)
    for agent_id, o in outcomes.items():
        info = pairs.get(agent_id, {})
        by_slot: dict[pt.Slot, list[int]] = defaultdict(list)
        for c, p in zip(o.customers, o.final):
            for slot in p:
                by_slot[slot].append(c)
        for spec in o.specs:
            for w, d in spec.current:
                if d in wd:
                    by_slot.setdefault((w, d), [])
        for (w, d), cs in by_slot.items():
            days.append(PlanDay(agent_id, w, d, tuple(PlanVisit(c, info[c].address_id, n)
                                                      for n, c in enumerate(sorted(cs)))))
    days.sort(key=lambda x: (x.agent_id, x.week, x.weekday))
    counts = Counter(v.customer_id for d in days for v in d.visits)
    return CurrentPlan(pt.CYCLE_WEEKS, tuple(days),
                       {c: n / pt.CYCLE_WEEKS for c, n in counts.items()}, False)


def _days_json(me: ev.ManagerEval, renumber: bool) -> list[dict[str, Any]]:
    """Дни менеджера (§10.1). renumber — «стало»: № визита = позиция в объезде (NN + 2-opt от дома),
    как в выгрузке для ERP."""
    out = []
    for r in me.days:
        full = _day_json(r)
        day = {key: full[key] for key in DAY_KEYS}
        if renumber:
            day['stops'] = [{**stop, 'erp_rownum': n} for n, stop in enumerate(full['stops'], 1)]
        out.append(day)
    return out


def _week_json(wk: ev.WeekTotals, cost: float) -> dict[str, Any]:
    return {'visits': _num(wk.visits), 'revenue_low': _i(wk.revenue_low),
            'revenue_peak': _i(wk.revenue_peak), 'revenue_year': _i(wk.revenue_year),
            'manager_km': _r(wk.manager_km, 1),
            'days_below_min': _num(wk.days_below_min), 'avg_work_hours': _r(wk.avg_work_hours, 1),
            'avg_plan_hours': _r(wk.avg_plan_hours, 1), 'cost': _i(cost)}


def _empty_week() -> ev.WeekTotals:
    """Неделя менеджера без дней в плане: всё по нулям."""
    return ev.WeekTotals(visits=0.0, revenue_low=0.0, revenue_year=0.0, revenue_peak=0.0, manager_km=0.0,
                         manager_liters=0.0, days_below_min=0.0, avg_plan_hours=None, avg_work_hours=None)


def _company(snap: Snapshot, bundle: Bundle, pe: ev.PlanEvaluation) -> dict[str, Any]:
    totals = ev.plan_totals(snap, bundle, pe)
    totals['visits_week'] = _num(sum(pe.weeks[me.agent_id].visits for me in pe.evals if me.included))
    return totals


def feasibility(revenue_week_low: float, min_day_revenue: float, workdays: int) -> dict[str, Any]:
    """Верхняя граница числа дней ≥ минимума: floor(выручка недели зимой / минимум) из рабочих дней."""
    most = workdays if min_day_revenue <= 0 else min(workdays, int(revenue_week_low // min_day_revenue))
    return {'revenue_week_low': _i(revenue_week_low), 'max_days_ge_min': most, 'workdays': workdays,
            'reachable': most >= workdays}


def _result(ctx: _Ctx, run_ids: Sequence[int], parts: Mapping[int, Mapping[str, Any]],
            after_snap: Snapshot, after: ev.PlanEvaluation,
            f_after_inc: Mapping[int, float]) -> dict[str, Any]:
    snap, bundle, before = ctx.snap, ctx.bundle, ctx.before
    s = bundle.settings
    before_evals = {me.agent_id: me for me in before.evals}
    after_evals = {me.agent_id: me for me in after.evals}
    managers = []
    for a in run_ids:
        part = parts[a]
        agent = snap.agents.get(a)
        wk_before = before.weeks[a]
        # все клиенты менеджера убраны из маршрута (§15), а работал он только в нерабочий день — в
        # «стало» у него нет ни одного дня: неделя без визитов
        wk_after = after.weeks.get(a) or _empty_week()
        managers.append({
            'agent_id': a, 'code': _code(snap, a), 'name': agent.name if agent else '',
            'before': _week_json(wk_before, part['cost_before']),
            'after': _week_json(wk_after, part['cost_after']),
            'feasibility': feasibility(wk_before.revenue_low, float(s['min_day_revenue']),
                                       len(s['workdays'])),
            'time_capped': part['time_capped'],
            'days_before': _days_json(before_evals[a], renumber=False),
            'days_after': _days_json(after_evals[a], renumber=True) if a in after_evals else [],
            'changes': part['changes'],
            'hints': part['hints'],
        })

    # Клиенты менеджеров расчёта; координата — первого визита клиента в текущем плане (как в обзоре)
    first: dict[int, Coord] = {}
    for d in snap.plan.days:
        for v in d.visits:
            if v.customer_id not in first:
                key = (v.customer_id, v.address_id)
                first[v.customer_id] = before.coords.get(key) or ev.visit_coord(snap, *key, bundle.geo_overrides)
    run_customers = sorted({c for a in run_ids for c in ctx.pairs.get(a, {})})
    customers = {}
    for c in run_customers:
        cust = snap.customers.get(c)
        coord = first.get(c)
        status = before.models[c].status
        customers[str(c)] = {
            'code': cust.code if cust else '', 'name': cust.name if cust else '',
            'lat': coord.lat if coord else None, 'lon': coord.lon if coord else None,
            'coord_source': coord.source if coord else 'none',
            'size': before.models[c].size, 'abc': ctx.abc.get(c),
            'lam_year': _r(before.models[c].year_hist.lam, 2),   # по истории заказов, и у затихших
            'freq_current': _r(ctx.freq_before.get(c, 0.0), 2),
            'freq_target': _r(f_after_inc.get(c, 0.0), 2),
            # §15: статус по давности последнего заказа (у затихших и потерянных в расчёте λ = 0)
            'status': status.status if status is not None else cst.ACTIVE,
            'silent_days': status.silent_days if status is not None else None,
        }

    fallback = [(fuel, price) for fuel, price, source in ctx.fuel_sources if source == 'fallback']
    if fallback or not ctx.fuel_sources:
        fuel_used, fuel_source = float(s['fuel_price_fallback']), 'fallback'
    else:
        common = Counter(fuel for fuel, _, _ in ctx.fuel_sources if fuel != 'diesel') or \
            Counter(fuel for fuel, _, _ in ctx.fuel_sources)
        fuel = sorted(common.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        fuel_used, fuel_source = float(s[f'fuel_price_{fuel}']), 'settings'
    return {
        'cycle_weeks': pt.CYCLE_WEEKS,
        'params': {'agent_ids': list(run_ids), 'start': ctx.params['start'],
                   'frequencies': ctx.params['frequencies']},
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'seconds': 0.0,
        'snapshot_as_of': snap.data_as_of.isoformat(timespec='seconds'),
        'fuel_price_used': _num(fuel_used),
        'fuel_price_source': fuel_source,
        'truck_costs': ctx.fleet is not None,   # дизель парка в стоимости поиска
        'before': _company(snap, bundle, before),
        'after': _company(after_snap, bundle, after),
        'managers': managers,
        'customers': customers,
        # принятые решения, которые в этом расчёте не применены: план клиента в ERP изменился
        'stale_decisions': stale_json(snap, bundle, ctx.book, ctx.pairs),
    }


# --- Выгрузка плана для ERP (§9) ---

Proposal = tuple[pt.Pattern, pt.Pattern]   # («было», «стало») предложения расчёта


def proposals_of(result: Mapping[str, Any] | None) -> dict[tuple[int, int], Proposal]:
    """(менеджер, клиент) → предложение последнего расчёта: шаблоны «было» и «стало» (без передач)."""
    out: dict[tuple[int, int], Proposal] = {}
    for m in (result or {}).get('managers', []):
        for ch in m.get('changes', []):
            if ch.get('type') == 'transfer':   # дни у другого менеджера — не шаблон этого
                continue
            frm = pt.parse_pattern(ch.get('from', {}).get('pattern'))
            to = pt.parse_pattern(ch.get('to', {}).get('pattern'))
            if frm is not None and to is not None:
                out[(m['agent_id'], ch['customer_id'])] = (frm, to)
    return out


def export_transfers(snap: Snapshot, bundle: Bundle, book: DecisionBook,
                     pairs: Mapping[int, Mapping[int, PairInfo]]) -> dict[tuple[int, int], tuple[int, pt.Pattern]]:
    """Принятые передачи, которые входят в план для ERP: (от кого, клиент) → (кому, дни). Оба
    менеджера в расчёте, у получателя клиента ещё нет, «убрать из маршрута» не принято (иначе клиент
    убирается, а не передаётся)."""
    out = {}
    for (a, c), (b, p) in sorted(book.accepted_transfer.items()):
        if (bundle.included(a, snap.active_agents) and bundle.included(b, snap.active_agents)
                and c in pairs.get(a, {}) and c not in pairs.get(b, {}) and (a, c) not in book.accepted_remove):
            out[(a, c)] = (b, p)
    return out


def export_patterns(snap: Snapshot, bundle: Bundle, book: DecisionBook,
                    pairs: Mapping[int, Mapping[int, PairInfo]],
                    proposals: Mapping[tuple[int, int], Proposal]) -> dict[int, dict[int, pt.Pattern]]:
    """Шаблоны плана для ERP по менеджерам в расчёте: текущие + действующие принятые решения.
    Принято «убрать из маршрута» — шаблон пустой (в плане клиента нет); принятый шаблон — как есть;
    принята только частота — шаблон из предложения последнего расчёта, если оно сделано от того же
    «было», этой частоты и допустимо, иначе из текущих дней (наибольшее совпадение, затем меньшая
    загрузка дня). Принята передача (export_transfers) — клиента у прежнего менеджера нет, у нового —
    принятые дни (решения прежнего менеджера по дням и частоте этого клиента не действуют)."""
    workdays = bundle.settings['workdays']
    moved = export_transfers(snap, bundle, book, pairs)
    out: dict[int, dict[int, pt.Pattern]] = {}
    for a in _agent_order(snap):
        if not bundle.included(a, snap.active_agents):
            continue
        info = pairs.get(a, {})
        new: dict[int, pt.Pattern] = {}
        load: Counter = Counter()
        pending = []
        for c in sorted(info):
            key, cur = (a, c), info[c].pattern
            if key in moved:
                continue
            lock = book.accepted_pattern.get(key)
            freq = book.accepted_freq.get(key)
            if key in book.accepted_remove:
                new[c] = ()
            elif lock is not None:
                new[c] = lock
            elif freq is not None and not pt.same_freq(freq, pt.pattern_freq(cur)):
                pending.append(c)
                continue
            else:
                new[c] = cur
            load.update(new[c])
        for c in pending:
            cur = info[c].pattern
            cands = pt.allowed_patterns(cur, book.accepted_freq[(a, c)], workdays,
                                        book.rejected_pattern.get((a, c), ())) or [cur]
            proposal = proposals.get((a, c))
            if proposal is not None and proposal[0] == cur and proposal[1] in cands:
                new[c] = proposal[1]
            else:
                new[c] = min(cands, key=lambda p: (-len(set(cur) & set(p)), sum(load[x] for x in p), p))
            load.update(new[c])
        out[a] = new
    for (_, c), (b, p) in moved.items():
        out[b][c] = p
    return out


def plan_export(snap: Snapshot, bundle: Bundle, decisions: Sequence[Decision],
                proposals: Mapping[tuple[int, int], Proposal] | None = None,
                distance: Distance | None = None) -> dict[str, Any]:
    """Текущий план + только принятые изменения (export_patterns), по каждому менеджеру в расчёте,
    цикл 2 недели. Принято «убрать из маршрута» — клиента нет в строках плана, в изменениях —
    type 'remove'; принята передача — клиент в строках нового менеджера (отметка «передать», адрес —
    из шаблона прежнего), в изменениях — type 'transfer' строкой прежнего менеджера. Устаревшие решения
    (приняты на другом плане клиента) не применяются — они в stale_decisions. Порядок внутри дня —
    NN + 2-opt от дома (этап 1); distance — функция расстояния оценки (Norms.distance, None — по прямой)."""
    pairs = plan_pairs(snap.plan)
    book = DecisionBook.from_rows(decisions, pairs)
    planned = export_patterns(snap, bundle, book, pairs, proposals or {})
    moved_in = {(b, c): a for (a, c), (b, _) in export_transfers(snap, bundle, book, pairs).items()}
    coords: dict[tuple[int, int], Coord] = {}

    def coord(c: int, addr: int) -> Coord:
        if (c, addr) not in coords:
            coords[(c, addr)] = ev.visit_coord(snap, c, addr, bundle.geo_overrides)
        return coords[(c, addr)]

    rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    managers: list[dict[str, Any]] = []
    n_changes: Counter = Counter()
    for a, new in planned.items():
        # переданные этому менеджеру — с адресом и «было» из шаблона прежнего менеджера
        info = {**pairs.get(a, {}), **{c: pairs[frm][c] for (b, c), frm in moved_in.items() if b == a}}
        agent = snap.agents.get(a)
        code, name = _code(snap, a), agent.name if agent else ''
        home, _ = ev.manager_home(snap, bundle, a)
        by_slot: dict[pt.Slot, list[int]] = defaultdict(list)
        for c, p in new.items():
            for slot in p:
                by_slot[slot].append(c)
        for (week, weekday) in sorted(by_slot):
            cs = sorted(by_slot[(week, weekday)])
            located = [c for c in cs if coord(c, info[c].address_id).point is not None]
            order = route_order([coord(c, info[c].address_id).point for c in located], home, distance)
            seq = [located[k] for k in order] + [c for c in cs if coord(c, info[c].address_id).point is None]
            for no, c in enumerate(seq, 1):
                cust = snap.customers.get(c)
                rows.append({'agent_id': a, 'agent_code': code, 'agent_name': name, 'week': week,
                             'weekday': weekday, 'label': WEEKDAY_LABELS.get(weekday, str(weekday)),
                             'no': no, 'customer_id': c, 'customer_code': cust.code if cust else '',
                             'customer_name': cust.name if cust else '',
                             'address_id': info[c].address_id or None,
                             'mark': MARKS['transfer'] if (a, c) in moved_in
                             else MARKS.get(pt.change_type(info[c].pattern, new[c]) or '', '')})
        for c in sorted(new):
            removed = not new[c]   # принято «убрать из маршрута»: в строках плана клиента нет
            frm = moved_in.get((a, c))
            if frm is not None:
                ctype = 'transfer'
            else:
                ctype = 'remove' if removed else pt.change_type(info[c].pattern, new[c])
            if ctype is None:
                continue
            cust = snap.customers.get(c)
            cur = info[c].pattern
            row = {'agent_id': a, 'agent_code': code, 'agent_name': name, 'customer_id': c,
                   'customer_code': cust.code if cust else '',
                   'customer_name': cust.name if cust else '', 'type': ctype,
                   'from': {'freq': _num(pt.pattern_freq(cur)), 'pattern': pt.pattern_json(cur),
                            'text': pt.pattern_text(cur)},
                   'to': {'freq': _num(pt.pattern_freq(new[c])), 'pattern': pt.pattern_json(new[c]),
                          'text': cst.REMOVE_TEXT if removed else pt.pattern_text(new[c])}}
            if frm is not None:   # передача — строкой прежнего менеджера: «передать: от → кому»
                frm_agent = snap.agents.get(frm)
                row.update({'agent_id': frm, 'agent_code': _code(snap, frm),
                            'agent_name': frm_agent.name if frm_agent else '',
                            'from_agent': frm, 'to_agent': a, 'from_agent_code': _code(snap, frm),
                            'to_agent_code': code})
                row['from']['text'] = f"{_code(snap, frm)}: {row['from']['text']}"
                row['to']['text'] = f"{code}: {row['to']['text']}"
            n_changes[row['agent_id']] += 1
            changes.append(row)
        managers.append({'agent_id': a, 'code': code, 'name': name})
    for m in managers:
        m['changes'] = n_changes[m['agent_id']]
    if moved_in:   # строка передачи — у прежнего менеджера: изменения снова по менеджерам
        order = {m['agent_id']: i for i, m in enumerate(managers)}
        changes.sort(key=lambda x: order.get(x['agent_id'], len(order)))
    return {'cycle_weeks': pt.CYCLE_WEEKS, 'data_as_of': snap.data_as_of.isoformat(timespec='seconds'),
            'managers': managers, 'rows': rows, 'changes': changes,
            'stale_decisions': stale_json(snap, bundle, book, pairs)}


def decisions_view(snap: Snapshot, bundle: Bundle, decisions: Sequence[Decision],
                   proposals: Mapping[tuple[int, int], Proposal] | None = None) -> dict[str, Any]:
    """Тело GET /api/routes/decisions: действующие решения владельца (устаревшие — с пометкой
    stale), итоги и сколько изменений войдёт в план для ERP (как посчитает plan_export)."""
    pairs = plan_pairs(snap.plan)
    book = DecisionBook.from_rows(decisions, pairs)
    items = [decision_json(snap, d, pairs, bundle.included(d.agent_id, snap.active_agents))
             for d in decisions if decision_state(d, pairs) != DECISION_RETIRED]
    items.sort(key=lambda x: (x['agent_code'], x['customer_name'], x['customer_id'], x['kind']))
    planned = export_patterns(snap, bundle, book, pairs, proposals or {})
    export_changes = sum(1 for a, new in planned.items() for c, p in new.items()
                         if c not in pairs[a] or p != pairs[a][c].pattern)   # нет в плане — передан ему
    return {
        'data_as_of': snap.data_as_of.isoformat(timespec='seconds'),
        'summary': {
            'accepted': sum(1 for x in items if x['status'] == 'accepted' and not x['stale']),
            'rejected': sum(1 for x in items if x['status'] == 'rejected'),
            'stale': sum(1 for x in items if x['stale']),
            'export_changes': export_changes,
        },
        'decisions': items,
    }

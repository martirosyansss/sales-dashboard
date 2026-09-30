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
- выгрузка для ERP — текущий план + только принятые изменения, порядок дня — NN + 2-opt от дома.

Чистая логика — без Flask и без БД: настройки и решения передаёт вызывающий.
"""
from __future__ import annotations

import logging
import random
import time
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from statistics import fmean
from typing import TYPE_CHECKING, Any, Callable, Collection, Mapping, Sequence

from . import demand as dm
from . import evaluate as ev
from . import frequency as fq
from . import patterns as pt
from . import search as sr
from .evaluate import _day_json, _i, _num, _r
from .geo import Coord, Point, haversine_km, in_city
from .plan import CurrentPlan, PlanDay, PlanVisit, WEEKDAY_LABELS
from .store import DecisionInput
from .tsp import route_order

if TYPE_CHECKING:
    from .snapshot import Snapshot
    from .store import Bundle, Decision

logger = logging.getLogger(__name__)

START_MODES = ('current', 'fresh')
FREQUENCY_MODES = ('sales', 'current')
DEFAULT_PARAMS: dict[str, Any] = {'agent_ids': None, 'start': 'current', 'frequencies': 'sales'}
DECISION_FIELDS = ('customer_id', 'agent_id', 'kind', 'value', 'action', 'from')
MAX_DECISION_ITEMS = 2000                 # решений в одном запросе («Принять все у менеджера»)
MAX_AGENTS = 500
MAX_ID = 2 ** 31 - 1
# Поля дня в результате (§10.1) — подмножество дня обзора этапа 1
DAY_KEYS = ('week', 'weekday', 'label', 'visits', 'revenue_low_exp', 'p_day_ge_min', 'work_minutes',
            'commute_minutes', 'plan_minutes', 'manager_km', 'truck', 'stops')
MARKS = {'move': 'перенос', 'both': 'перенос, частота', 'frequency': 'частота'}
# Решение относительно плана снимка (decision_state)
DECISION_ACTIVE, DECISION_STALE, DECISION_RETIRED = 'active', 'stale', 'retired'
NOT_IN_PLAN_TEXT = 'клиента нет в плане менеджера'

ProgressFn = Callable[[int, int, 'str | None'], None]


class OptimizeError(RuntimeError):
    """Расчёт невозможен; текст — для пользователя."""


# --- Параметры и решения из API ---

def parse_params(payload: Any) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Тело POST /api/routes/optimize: {"agent_ids": [..] | null, "start", "frequencies"}.
    Отсутствующие поля — по умолчанию. Ошибки — {поле: текст}."""
    if not isinstance(payload, dict):
        return None, {'_': 'ожидался JSON-объект'}
    errors: dict[str, str] = {}
    unknown = sorted(str(k) for k in set(payload) - set(DEFAULT_PARAMS))
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
    return (None, errors) if errors else (params, {})


def parse_decision(payload: Any) -> tuple[DecisionInput | None, dict[str, str]]:
    """Одно решение: {"customer_id", "agent_id", "kind": "pattern"|"freq", "value": [[неделя, день], …]
    | 0.5, "action": "accept"|"reject"|"reset", "from": шаблон | частота клиента в предложении}.
    from — от чего принимается решение (строка «было» предложения); нет — возьмётся план снимка
    (bind_decisions); для reset не нужен."""
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
    if kind == 'pattern':
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
        errors['kind'] = 'вид решения: pattern (шаблон) или freq (частота)'
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
    """Шаблон (pattern_key) или частота (freq_key) клиента у менеджера в плане снимка;
    None — клиента нет в плане менеджера."""
    info = pairs.get(agent_id, {}).get(customer_id)
    if info is None:
        return None
    return pt.pattern_key(info.pattern) if kind == 'pattern' else pt.freq_key(pt.pattern_freq(info.pattern))


def decision_state(d: Decision, pairs: Mapping[int, Mapping[int, PairInfo]]) -> str:
    """Решение относительно плана снимка (текущий шаблон или частота клиента у менеджера):
    - принятое, и в ERP уже так (value = текущему) — retired: план применён, замка больше нет;
    - принятое на другом плане (from_value ≠ текущему) или клиента у менеджера больше нет —
      stale: не применяется, владелец видит его в списке устаревших;
    - отклонённое на другом плане (или клиента нет) — retired: запрет относился к прежнему плану;
    - иначе — active. from_value неизвестен (решение схемы 3) — план считается тем же."""
    cur = current_value(pairs, d.agent_id, d.customer_id, d.kind)
    moved_on = cur is None or (d.from_value is not None and d.from_value != cur)
    if d.status == 'accepted':
        if cur is not None and d.value == cur:
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
        if cur is None:
            prefix = f'items.{i}.' if request.mode == 'batch' else ''
            errors[prefix + 'customer_id'] = 'У этого менеджера нет такого клиента в плане ERP'
            continue
        out.append(d if d.from_value is not None else replace(d, from_value=cur))
    return out, errors


@dataclass(frozen=True)
class DecisionBook:
    """Решения владельца для расчёта. Ключи — (менеджер, клиент). stale — принятые, но устаревшие
    (приняты на другом плане): не закрепляют ничего."""
    accepted_pattern: dict[tuple[int, int], pt.Pattern]
    accepted_freq: dict[tuple[int, int], float]
    rejected_pattern: dict[tuple[int, int], frozenset[pt.Pattern]]
    rejected_freq: dict[tuple[int, int], frozenset[float]]
    # (клиент, менеджер, вид, значение) → (статус, от чего принималось)
    status: dict[tuple[int, int, str, str], tuple[str, str | None]]
    stale: tuple[Decision, ...] = ()

    @classmethod
    def from_rows(cls, rows: Sequence[Decision],
                  pairs: Mapping[int, Mapping[int, PairInfo]] | None = None) -> DecisionBook:
        """pairs — план снимка: устаревшие и отработавшие решения не действуют (decision_state);
        None — все решения действуют (статусы для показа в сохранённом результате)."""
        acc_p: dict[tuple[int, int], pt.Pattern] = {}
        acc_f: dict[tuple[int, int], float] = {}
        rej_p: dict[tuple[int, int], set[pt.Pattern]] = defaultdict(set)
        rej_f: dict[tuple[int, int], set[float]] = defaultdict(set)
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
            if d.kind == 'pattern':
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
                   {k: frozenset(v) for k, v in rej_f.items()}, status, tuple(stale))

    def _status(self, customer_id: int, agent_id: int, kind: str, value: str,
                from_key: str) -> str | None:
        hit = self.status.get((customer_id, agent_id, kind, value))
        if hit is None:
            return None
        status, from_value = hit
        return status if from_value is None or from_value == from_key else None

    def change_status(self, agent_id: int, change: Mapping[str, Any]) -> dict[str, str | None]:
        """Статусы решений для строки предложения: по шаблону «стало» и (если частота меняется)
        по частоте «стало». Решение относится к строке, только если принималось от того же «было»:
        решение по прежнему плану клиента к новому предложению не приклеивается."""
        cid, frm, to = change['customer_id'], change['from'], change['to']
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
    """(значение для JSON, текст) шаблона или частоты по каноническому тексту."""
    if key is None:
        return None, None
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
    value, to_text = _side_text(d.kind, d.value)
    frm, from_text = _side_text(d.kind, d.from_value)
    _, current_text = _side_text(d.kind, cur)
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
    source: str                        # manual | sales | current | locked
    allowed: tuple[pt.Pattern, ...]
    locked: bool
    forbidden: frozenset[pt.Pattern]


def pair_spec(key: tuple[int, int], current: pt.Pattern, f_sales: float | None, mode: str,
              workdays: Collection[int], book: DecisionBook) -> PairSpec:
    """Целевая частота и допустимые шаблоны: принятый шаблон — закреплён (единственный);
    иначе частота по §2 и шаблоны по §3 без запрещённых. Нет ни одного — частота остаётся
    текущей; запрещено всё — клиент остаётся как есть."""
    forbidden = book.rejected_pattern.get(key, frozenset())
    lock = book.accepted_pattern.get(key)
    if lock is not None:
        return PairSpec(current, pt.pattern_freq(lock), 'locked', (lock,), True, forbidden)
    f_cur = pt.pattern_freq(current)
    target, source = fq.target_frequency(f_cur, f_sales, mode, book.accepted_freq.get(key),
                                         book.rejected_freq.get(key, ()))
    allowed = pt.allowed_patterns(current, target, workdays, forbidden)
    if not allowed and not pt.same_freq(target, f_cur):
        target, source = f_cur, fq.SOURCE_CURRENT
        allowed = pt.allowed_patterns(current, target, workdays, forbidden)
    return PairSpec(current, target, source, tuple(allowed or [current]), False, forbidden)


def season_lam(model: ev.CustomerModel) -> float:
    """Спрос для частоты по продажам: max(λ_год, λ_низкий сезон, λ_пик). Выручка клиента в сезоне
    s по модели — min(f, λ_s) × средний заказ: при f ≥ max λ_s снижение частоты ни в каком сезоне
    выручку не уменьшает (частота по одному году летом «съедала» заказы)."""
    return max(model.year.lam, model.low.lam, model.peak.lam)


def visit_params(model: ev.CustomerModel, f_total: float) -> sr.VisitParams:
    """Параметры визита при частоте f_total (все визиты клиента у менеджеров в расчёте / нед):
    p = min(1, λ/f) по сезону, как Draw этапа 1."""
    low, year, peak = model.low, model.year, model.peak
    p_low = dm.visit_probability(low.lam, f_total) if low.values else 0.0
    mean = low.mean_revenue
    square = fmean(v[0] * v[0] for v in low.values) if low.values else 0.0
    mu = p_low * mean
    p_year = dm.visit_probability(year.lam, f_total) if year.values else 0.0
    p_peak = dm.visit_probability(peak.lam, f_total) if peak.values else 0.0
    return sr.VisitParams(mu=mu, var=max(0.0, p_low * square - mu * mu), p_low=p_low,
                          p_year=p_year, kg=p_peak * peak.mean_kg if peak.values else 0.0)


def _slots(p: pt.Pattern) -> sr.SlotPattern:
    return tuple(sorted(sr.slot_of_day(w, d) for w, d in p))


def _pairs_of(slots: Sequence[int]) -> pt.Pattern:
    return tuple(sorted(sr.day_of_slot(j) for j in slots))


def _matrices(home: Point | None, points: Sequence[Point], depot: Point | None,
              norms: ev.Norms) -> tuple[sr.Matrix, sr.Matrix, sr.Matrix | None]:
    """Матрицы менеджера (вершина 0 — дом; без дома — нули: открытый путь, как на этапе 1) и
    грузовика (вершина 0 — склад). Км — по прямой × извилистость; минуты — км / скорость участка
    (город, если оба конца в городе)."""
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
            d = haversine_km(pa, pts[b]) * norms.detour
            speed = norms.speed_city_kmh if city[a] and city[b] else norms.speed_region_kmh
            km[a][b] = km[b][a] = d
            mins[a][b] = mins[b][a] = d / speed * 60.0
    tkm = None
    if depot is not None:
        tkm = [list(row) for row in km]
        tkm[0] = [0.0] * n
        for b in range(1, n):
            d = haversine_km(depot, pts[b]) * norms.detour
            tkm[0][b] = tkm[b][0] = d
    return km, mins, tkm


# --- Расчёт ---

@dataclass
class ManagerOutcome:
    """Итог по менеджеру для проверок (tests/routes_optimize_check.py)."""
    agent_id: int
    code: str
    customers: list[int]
    specs: list[PairSpec]
    final: list[pt.Pattern]
    cost_before: float
    cost_after: float
    stats: sr.SearchStats


@dataclass
class RunOutcome:
    result: dict[str, Any]
    managers: list[ManagerOutcome]
    before: ev.PlanEvaluation
    after: ev.PlanEvaluation


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

    def visit(self, cid: int, f_total: float) -> sr.VisitParams:
        key = (cid, round(f_total, 9))
        hit = self.params_cache.get(key)
        if hit is None:
            hit = self.params_cache[key] = visit_params(self.before.models[cid], f_total)
        return hit


def run_optimization(snap: Snapshot, bundle: Bundle, calib: ev.Calibration | None,
                     decisions: Sequence[Decision], params: Mapping[str, Any], *,
                     progress: ProgressFn | None = None,
                     clock: Callable[[], float] = time.perf_counter) -> RunOutcome:
    """Режим А по выбранным менеджерам → результат §10.1 (статусы решений — на момент расчёта)."""
    started = clock()
    s = bundle.settings
    params = {**DEFAULT_PARAMS, **params}
    pairs = plan_pairs(snap.plan)
    run_ids, error = resolve_agents(snap, bundle, params['agent_ids'])
    if error:
        raise OptimizeError(error)
    book = DecisionBook.from_rows(decisions, pairs)   # устаревшие — не закрепляют, а показываются
    before = ev.evaluate_plan(snap, bundle, calib, run_ids)
    included = before.included_ids
    models = before.models

    # Частоты (§2): ABC — по клиентам планов всех менеджеров в расчёте; частота по продажам —
    # по самому высокому спросу из года, низкого сезона и пика: ни в один сезон заказы не теряются
    inc_customers = sorted({c for a in included for c in pairs.get(a, {})})
    lam = {c: models[c].year.lam for c in inc_customers}
    lam_season = {c: season_lam(models[c]) for c in inc_customers}
    abc = fq.abc_classes({c: lam[c] * models[c].year.mean_revenue for c in inc_customers},
                         s['abc_a_share'], s['abc_b_share'])
    f_sales = {c: fq.sales_frequency(lam_season[c], abc[c], s['freq_safety']) for c in inc_customers}
    specs = {(a, c): pair_spec((a, c), info.pattern, f_sales[c], params['frequencies'],
                               s['workdays'], book)
             for a in run_ids for c, info in pairs.get(a, {}).items()}
    freq_before = snap.plan.visits_per_week_among(included)
    freq_after: dict[int, float] = Counter()
    for a in included:
        for c, info in pairs.get(a, {}).items():
            spec = specs.get((a, c))
            freq_after[c] += len(spec.allowed[0] if spec else info.pattern) / pt.CYCLE_WEEKS
    ctx = _Ctx(snap, bundle, params, before, book, pairs, specs, freq_before, dict(freq_after),
               lam, lam_season, abc, clock, {}, [])

    evals = {me.agent_id: me for me in before.evals}
    outcomes: list[ManagerOutcome] = []
    parts: dict[int, dict[str, Any]] = {}
    for n, a in enumerate(run_ids):
        if progress:
            progress(n, len(run_ids), _code(snap, a))
        outcome, part = _optimize_manager(ctx, a, evals[a])
        outcomes.append(outcome)
        parts[a] = part
    if progress:
        progress(len(run_ids), len(run_ids), None)

    # «Стало»: предложенный план (W = 2) тем же полным оценщиком; остальные менеджеры — как есть
    after_plan = _after_plan(snap.plan, {o.agent_id: o for o in outcomes}, pairs, s['workdays'])
    after_snap = replace(snap, plan=after_plan)
    f_after_inc = after_plan.visits_per_week_among(included)
    after_models = {c: replace(m, visits_per_week=f_after_inc.get(c, 0.0)) for c, m in models.items()}
    after = ev.evaluate_plan(after_snap, bundle, calib, run_ids,
                             visit_minutes=before.visit_minutes, models=after_models)
    result = _result(ctx, run_ids, parts, after_snap, after, f_after_inc)
    result['seconds'] = round(clock() - started, 1)
    logger.info('[Routes] Оптимизация: менеджеров %d, изменений %d, %.1f с', len(run_ids),
                sum(len(m['changes']) for m in result['managers']), result['seconds'])
    return RunOutcome(result, outcomes, before, after)


def _optimize_manager(ctx: _Ctx, agent_id: int,
                      me: ev.ManagerEval) -> tuple[ManagerOutcome, dict[str, Any]]:
    snap, bundle, before = ctx.snap, ctx.bundle, ctx.before
    s = bundle.settings
    norms = before.norms
    info = ctx.pairs.get(agent_id, {})
    cids = sorted(info)
    specs = [ctx.specs[(agent_id, c)] for c in cids]
    coords: list[Coord] = []
    for c in cids:
        key = (c, info[c].address_id)
        coords.append(before.coords.get(key) or ev.visit_coord(snap, *key))
    points = [co.point for co in coords]
    located = [i for i, p in enumerate(points) if p is not None]
    node_of = {i: n + 1 for n, i in enumerate(located)}
    truck = me.truck
    depot = bundle.depot if truck is not None else None
    km, mins, tkm = _matrices(me.home, [points[i] for i in located], depot, norms)

    # Веса стоимости (§4): топливо менеджера и дизель — по настройкам или запасной цене
    profile = bundle.profile(agent_id)
    l100 = (profile.car_fuel_l_per_100km if profile.car_fuel_l_per_100km is not None
            else float(s['manager_car_default_l_per_100km']))
    price = _fuel_price(ctx, me.fuel_type)
    truck_cost = tkm is not None and truck is not None and truck.fuel_l_per_100km is not None
    truck_per_km = (float(s['truck_priority']) * _fuel_price(ctx, 'diesel')
                    * truck.fuel_l_per_100km / 100.0) if truck_cost else 0.0
    weights = sr.Weights(
        manager_per_km=price * l100 / 100.0, truck_per_km=truck_per_km,
        weak_day=float(s['penalty_weak_day']), poor_trip=float(s['penalty_poor_trip']),
        overtime_per_min=float(s['penalty_overtime_per_min']), window_min=norms.work_minutes,
        min_day_revenue=norms.min_day_revenue, min_trip_revenue=norms.min_trip_revenue,
        truck_capacity_kg=truck.capacity_kg if truck is not None else None)

    workdays = set(s['workdays'])
    base_slots = {sr.slot_of_day(w, d) for spec in specs for w, d in spec.current if d in workdays}
    lines = [sr.Line(customer_id=c, node=node_of.get(i, 0),
                     minutes=before.visit_minutes[before.models[c].size],
                     current=_slots(spec.current), allowed=tuple(_slots(p) for p in spec.allowed),
                     locked=spec.locked,
                     u=sr.make_u(random.Random(zlib.crc32(f'{agent_id}|{c}|truck'.encode('utf-8')))))
             for i, (c, spec) in enumerate(zip(cids, specs))]
    prob = sr.Problem(
        agent_id=agent_id, lines=lines, km=km, mins=mins, tkm=tkm, weights=weights,
        workday=tuple(sr.day_of_slot(j)[1] in workdays for j in range(sr.SLOTS)),
        base=tuple(j in base_slots for j in range(sr.SLOTS)),
        neighbors=sr.nearest_lines([line.node for line in lines], km),
        seed=zlib.crc32(str(agent_id).encode('utf-8')))

    before_params = [ctx.visit(c, ctx.freq_before[c]) for c in cids]
    target_params = [ctx.visit(c, ctx.freq_after[c]) for c in cids]
    before_state = sr.State(prob, [line.current for line in lines], before_params,
                            change_penalty=0.0, trucks=True)
    fresh = ctx.params['start'] == 'fresh'
    penalty = 0.0 if fresh else float(s['penalty_change'])
    if fresh:
        state = sr.State(prob, [line.allowed[0] if line.locked else () for line in lines],
                         target_params, change_penalty=penalty, trucks=truck_cost)
        sr.greedy_fill(state, sr.fresh_order(prob, me.home, points))
    else:
        state = sr.State(prob, sr.current_start(prob), target_params, change_penalty=penalty,
                         trucks=truck_cost)
    stats = sr.search(state, seconds=float(s['optimizer_seconds_per_manager']), clock=ctx.clock)
    final = [_pairs_of(p) for p in state.pattern]

    changes = []
    hints = []
    for i, c in enumerate(cids):
        spec = specs[i]
        for kind, text in fq.frequency_hints(ctx.lam[c], ctx.freq_before[c], s['freq_safety']):
            hints.append({'customer_id': c, 'kind': kind, 'text': text})
        ctype = pt.change_type(spec.current, final[i])
        if ctype is None:
            continue
        f_cur, f_new = pt.pattern_freq(spec.current), pt.pattern_freq(final[i])
        # эффект — этого изменения, применённого к текущему плану: частота клиента меняется только у него
        f_total = max(0.0, ctx.freq_before[c] - f_cur + f_new)
        effect = sr.change_effect(before_state, i, state.pattern[i], ctx.visit(c, f_total))
        change = {
            'customer_id': c, 'type': ctype,
            'from': {'freq': _num(f_cur), 'pattern': pt.pattern_json(spec.current),
                     'text': pt.pattern_text(spec.current)},
            'to': {'freq': _num(f_new), 'pattern': pt.pattern_json(final[i]),
                   'text': pt.pattern_text(final[i])},
            'reason': _reason(spec, f_cur, f_new, ctx.lam[c], ctx.lam_season[c]),
            'effect': {
                'manager_km_week': round(effect['km'], 1),
                'truck_km_week': round(effect['truck_km'], 1) if tkm is not None else None,
                'weak_days_week': round(effect['weak'], 2),
                'minutes_week': int(round(effect['minutes'])),
            },
        }
        change['decision'] = ctx.book.change_status(agent_id, change)
        changes.append(change)
    changes.sort(key=lambda ch: (ch['to']['pattern'][0], ch['customer_id']))

    code = _code(snap, agent_id)
    logger.info('[Routes] Оптимизация %s: клиентов %d, C %.0f → %.0f драм/нед (старт %.0f), '
                'ходов %d, возмущений %d, %.1f с%s', code, len(lines), before_state.total,
                state.total, stats.cost_start, stats.accepted, stats.perturbations, stats.seconds,
                ' — СТОП ПО ВРЕМЕНИ' if stats.time_capped else '')
    outcome = ManagerOutcome(agent_id, code, cids, specs, final, before_state.total, state.total, stats)
    part = {'changes': changes, 'hints': hints, 'time_capped': stats.time_capped,
            'cost_before': before_state.total, 'cost_after': state.total, 'truck_cost': truck_cost,
            'final': dict(zip(cids, final)), 'addresses': {c: info[c].address_id for c in cids}}
    return outcome, part


def _fuel_price(ctx: _Ctx, fuel: str) -> float:
    """Цена топлива для веса в поиске: из настроек, иначе запасная (fuel_price_fallback)."""
    s = ctx.bundle.settings
    price = s.get(f'fuel_price_{fuel}')
    source = 'settings' if price is not None else 'fallback'
    value = float(price if price is not None else s['fuel_price_fallback'])
    ctx.fuel_sources.append((fuel, value, source))
    return value


def _reason(spec: PairSpec, f_cur: float, f_new: float, lam: float, lam_season: float) -> str | None:
    if spec.locked:
        return 'шаблон принят владельцем'
    if pt.same_freq(f_cur, f_new):
        return None
    if spec.source == fq.SOURCE_MANUAL:
        return 'частота принята владельцем'
    return fq.order_rate_text(lam, lam_season)


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
            'truck_km': _r(wk.truck_km, 1), 'truck_liters': _r(wk.truck_liters, 1),
            'days_below_min': _num(wk.days_below_min), 'avg_work_hours': _r(wk.avg_work_hours, 1),
            'avg_plan_hours': _r(wk.avg_plan_hours, 1), 'cost': _i(cost)}


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
        managers.append({
            'agent_id': a, 'code': _code(snap, a), 'name': agent.name if agent else '',
            'before': _week_json(wk_before, part['cost_before']),
            'after': _week_json(after.weeks[a], part['cost_after']),
            'feasibility': feasibility(wk_before.revenue_low, float(s['min_day_revenue']),
                                       len(s['workdays'])),
            'time_capped': part['time_capped'],
            'days_before': _days_json(before_evals[a], renumber=False),
            'days_after': _days_json(after_evals[a], renumber=True),
            'changes': part['changes'],
            'hints': part['hints'],
        })

    # Клиенты менеджеров расчёта; координата — первого визита клиента в текущем плане (как в обзоре)
    first: dict[int, Coord] = {}
    for d in snap.plan.days:
        for v in d.visits:
            if v.customer_id not in first:
                key = (v.customer_id, v.address_id)
                first[v.customer_id] = before.coords.get(key) or ev.visit_coord(snap, *key)
    run_customers = sorted({c for a in run_ids for c in ctx.pairs.get(a, {})})
    customers = {}
    for c in run_customers:
        cust = snap.customers.get(c)
        coord = first.get(c)
        customers[str(c)] = {
            'code': cust.code if cust else '', 'name': cust.name if cust else '',
            'lat': coord.lat if coord else None, 'lon': coord.lon if coord else None,
            'coord_source': coord.source if coord else 'none',
            'size': before.models[c].size, 'abc': ctx.abc.get(c),
            'lam_year': _r(ctx.lam.get(c, before.models[c].year.lam), 2),
            'freq_current': _r(ctx.freq_before.get(c, 0.0), 2),
            'freq_target': _r(f_after_inc.get(c, 0.0), 2),
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
        'truck_costs': any(parts[a]['truck_cost'] for a in run_ids),
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
    """(менеджер, клиент) → предложение последнего расчёта: шаблоны «было» и «стало»."""
    out: dict[tuple[int, int], Proposal] = {}
    for m in (result or {}).get('managers', []):
        for ch in m.get('changes', []):
            frm = pt.parse_pattern(ch.get('from', {}).get('pattern'))
            to = pt.parse_pattern(ch.get('to', {}).get('pattern'))
            if frm is not None and to is not None:
                out[(m['agent_id'], ch['customer_id'])] = (frm, to)
    return out


def export_patterns(snap: Snapshot, bundle: Bundle, book: DecisionBook,
                    pairs: Mapping[int, Mapping[int, PairInfo]],
                    proposals: Mapping[tuple[int, int], Proposal]) -> dict[int, dict[int, pt.Pattern]]:
    """Шаблоны плана для ERP по менеджерам в расчёте: текущие + действующие принятые решения.
    Принятый шаблон — как есть; принята только частота — шаблон из предложения последнего расчёта,
    если оно сделано от того же «было», этой частоты и допустимо, иначе из текущих дней
    (наибольшее совпадение, затем меньшая загрузка дня)."""
    workdays = bundle.settings['workdays']
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
            lock = book.accepted_pattern.get(key)
            freq = book.accepted_freq.get(key)
            if lock is not None:
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
    return out


def plan_export(snap: Snapshot, bundle: Bundle, decisions: Sequence[Decision],
                proposals: Mapping[tuple[int, int], Proposal] | None = None) -> dict[str, Any]:
    """Текущий план + только принятые изменения (export_patterns), по каждому менеджеру в расчёте,
    цикл 2 недели. Устаревшие решения (приняты на другом плане клиента) не применяются —
    они в stale_decisions. Порядок внутри дня — NN + 2-opt от дома (этап 1)."""
    pairs = plan_pairs(snap.plan)
    book = DecisionBook.from_rows(decisions, pairs)
    planned = export_patterns(snap, bundle, book, pairs, proposals or {})
    coords: dict[tuple[int, int], Coord] = {}

    def coord(c: int, addr: int) -> Coord:
        if (c, addr) not in coords:
            coords[(c, addr)] = ev.visit_coord(snap, c, addr)
        return coords[(c, addr)]

    rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    managers: list[dict[str, Any]] = []
    for a, new in planned.items():
        info = pairs.get(a, {})
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
            order = route_order([coord(c, info[c].address_id).point for c in located], home)
            seq = [located[k] for k in order] + [c for c in cs if coord(c, info[c].address_id).point is None]
            for no, c in enumerate(seq, 1):
                cust = snap.customers.get(c)
                rows.append({'agent_id': a, 'agent_code': code, 'agent_name': name, 'week': week,
                             'weekday': weekday, 'label': WEEKDAY_LABELS.get(weekday, str(weekday)),
                             'no': no, 'customer_id': c, 'customer_code': cust.code if cust else '',
                             'customer_name': cust.name if cust else '',
                             'address_id': info[c].address_id or None,
                             'mark': MARKS.get(pt.change_type(info[c].pattern, new[c]) or '', '')})
        n_changes = 0
        for c in sorted(new):
            ctype = pt.change_type(info[c].pattern, new[c])
            if ctype is None:
                continue
            n_changes += 1
            cust = snap.customers.get(c)
            cur = info[c].pattern
            changes.append({'agent_id': a, 'agent_code': code, 'agent_name': name, 'customer_id': c,
                            'customer_code': cust.code if cust else '',
                            'customer_name': cust.name if cust else '', 'type': ctype,
                            'from': {'freq': _num(pt.pattern_freq(cur)), 'pattern': pt.pattern_json(cur),
                                     'text': pt.pattern_text(cur)},
                            'to': {'freq': _num(pt.pattern_freq(new[c])),
                                   'pattern': pt.pattern_json(new[c]), 'text': pt.pattern_text(new[c])}})
        managers.append({'agent_id': a, 'code': code, 'name': name, 'changes': n_changes})
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
                         if p != pairs[a][c].pattern)
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

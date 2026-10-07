# -*- coding: utf-8 -*-
"""«Վարորդներ» — показатели водителей за период (как driver analytics Omnitracs / Routific).

Чистые функции — без Flask, БД и ERP. Вход — факт дня раздела «Առաքիչ» (CrewFacts: кто закрыл каждую точку, деньги и
тара по водителю) и GPS-факт машин дня из обучения «Развоза» (CarDay: км, прибытие по GPS и окно приёма точек —
actuals.stop_marks, плановое ETA клиентов — прогноз сборки отправленного водителям плана).

Правила:
- человек дня — водитель, чьё «заявление» (действующая доставка, правило §5 п. 12) определило статус точки; covered
  (товар отдан по заказу) — автор последней доставки поглощённых владельцем точек; автора нет — водитель машины дня
  (больше всех закрытых точек машины). Առաքիչ (helper_id того же события) — отдельной строкой «помощник»;
- Օրեր — дни, где человек закрыл хотя бы одну точку; Խանութներ — закрытые точки (full / partial / refused / covered);
- Ժամանակին — точка с плановым ETA и прибытием по GPS: прибытие ≤ ETA + LATE_SLACK_MIN и, если у магазина есть окно
  приёма, не позже конца окна и обслуживание не раньше начала (actuals.stop_marks). n/N — вовремя / оценено;
- опоздание точки — max(прибытие − ETA, если больше допуска; прибытие − конец окна); Միջին ուշացում — среднее по
  опоздавшим (раньше окна — «рано», в среднее не входит);
- Կմ — км машины дня по GPS (actuals.reconstruct), делённые между людьми одной роли по доле закрытых ими точек машины;
- Կանխիկ — по «Գումար» (courier.views.money_drivers): надо взять наличными − взято всеми водителями по накладным
  (short: больше 0 — не взято); сдал − взял (diff) — только в днях с отметкой «сдал фактически»;
- Տարա — штук тары по действующим отметкам tare точек (последняя по точке, без вытесненных), по автору отметки.
Неполные данные не выдумываются: нет GPS — км и «вовремя» нет; нет плана — точка не оценивается; счётчики покрытия
(coverage) показывают, сколько точек оценено и почему остальные — нет.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Protocol, Sequence

from .actuals import YEREVAN

LATE_SLACK_MIN = 15.0          # прибытие позже ETA не больше чем на столько — ещё вовремя
MAX_DAYS = 92                  # период запроса — не длиннее
DONE = ('full', 'partial', 'refused', 'covered')
ROLES = ('driver', 'helper')


class CrewFacts(Protocol):
    """Факт дня раздела «Առաքիչ» для «Վարորդներ» (courier.scorecard.CrewSource; подключает app_v2 —
    route_optimizer.attach_crew_facts)."""

    def versions(self, since: str, until: str) -> dict[str, tuple[Any, ...]]:
        """День → отпечаток данных дня (события, снимки /day, отметки «сдал»): не изменился — day() тот же."""
        ...

    def day(self, day: str) -> dict[str, Any]:
        """{'stops': [{stop_id, car_code, customer_id, name, status, driver_id, helper_id}] — закрытые точки дня;
        'cash': {водитель: {expected, short, collected, handed, diff}}; 'tare': {водитель: штук}}."""
        ...

    def names(self) -> dict[int, str]:
        """Человек (id водителя «Առաքիչ») → имя."""
        ...


@dataclass(frozen=True)
class CarDay:
    """GPS-факт и план машины за день: км по GPS; точка → отметка обслуживающего визита (actuals.stop_marks: arrive,
    late_min — позже конца окна, early — обслуживание раньше начала окна, window — у точки есть окно); клиент →
    плановое ETA."""
    km: float
    marks: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    eta: Mapping[int, datetime] = field(default_factory=dict)


def _hm(t: datetime | None) -> str | None:
    return t.astimezone(YEREVAN).strftime('%H:%M') if t is not None else None


def timing(mark: Mapping[str, Any], eta: datetime, slack: float = LATE_SLACK_MIN) -> tuple[bool, float, str | None]:
    """(вовремя, опоздание в минутах, причина: 'eta' | 'window' | 'early' | None) точки с плановым ETA и прибытием."""
    late_eta = (mark['arrive'] - eta).total_seconds() / 60.0
    late_window = float(mark.get('late_min') or 0.0) if mark.get('window') else 0.0
    delay, reason = 0.0, None
    if late_eta > slack:
        delay, reason = late_eta, 'eta'
    if late_window > 0 and late_window > delay:
        delay, reason = late_window, 'window'
    if reason is None and mark.get('window') and mark.get('early'):
        reason = 'early'
    return reason is None, round(delay, 1), reason


def _entry() -> dict[str, Any]:
    return {'cars': [], 'stops': 0, 'partial': 0, 'refused': 0, 'rated': 0, 'on_time': 0, 'late_n': 0,
            'delay_sum': 0.0, 'late': [], 'km': None, 'cash': None, 'tare': 0.0}


def day_summary(crew: Mapping[str, Any], cars: Mapping[str, CarDay], slack: float = LATE_SLACK_MIN) -> dict[str, Any]:
    """Факт дня → {'people': {'роль:id': показатели дня}, 'coverage': счётчики покрытия} (правила — в описании
    модуля). Результат — только из входа: для прошлых дней его можно хранить (кэш по отпечатку данных дня)."""
    closed = [s for s in crew.get('stops') or () if s.get('status') in DONE]
    counts: dict[str, dict[int, int]] = {}
    for s in closed:
        if s.get('driver_id') is not None:
            mine = counts.setdefault(s['car_code'], {})
            mine[s['driver_id']] = mine.get(s['driver_id'], 0) + 1
    main = {car: min(c, key=lambda d: (-c[d], d)) for car, c in counts.items()}
    people: dict[str, dict[str, Any]] = {}
    share: dict[tuple[str, str], dict[str, int]] = {}   # (машина, роль) → человек → закрытых точек
    cov = {'closed': len(closed), 'unattributed': 0, 'rated': 0, 'no_eta': 0, 'no_gps': 0, 'car_days': 0,
           'car_days_gps': 0, 'km_unassigned': 0.0}

    def person(key: str) -> dict[str, Any]:
        return people.setdefault(key, _entry())

    for s in closed:
        car = s['car_code']
        did = s.get('driver_id') if s.get('driver_id') is not None else main.get(car)
        if did is None:
            cov['unattributed'] += 1
            continue
        keys = [f'driver:{did}']
        if s.get('helper_id') is not None and s['helper_id'] != did:
            keys.append(f'helper:{s["helper_id"]}')
        g = cars.get(car)
        mark = g.marks.get(s['stop_id']) if g is not None else None
        eta = g.eta.get(s.get('customer_id')) if g is not None and s.get('customer_id') is not None else None
        rated = None
        if mark is None:
            cov['no_gps'] += 1
        elif eta is None:
            cov['no_eta'] += 1
        else:
            cov['rated'] += 1
            rated = timing(mark, eta, slack)
        for key in keys:
            e = person(key)
            if car not in e['cars']:
                e['cars'].append(car)
            e['stops'] += 1
            e['partial'] += int(s['status'] == 'partial')
            e['refused'] += int(s['status'] == 'refused')
            role = key.split(':', 1)[0]
            by = share.setdefault((car, role), {})
            by[key] = by.get(key, 0) + 1
            if rated is None:
                continue
            ok, delay, reason = rated
            e['rated'] += 1
            e['on_time'] += int(ok)
            if reason in ('eta', 'window'):
                e['late_n'] += 1
                e['delay_sum'] += delay
            if not ok:
                e['late'].append({'stop_id': s['stop_id'], 'name': s.get('name'), 'car': car, 'planned': _hm(eta),
                                  'arrive': _hm(mark['arrive']), 'delay_min': delay, 'reason': reason})
    with_stops = {s['car_code'] for s in closed}
    cov['car_days'] = len(with_stops)
    cov['car_days_gps'] = sum(1 for c in with_stops if c in cars)
    for car, g in sorted(cars.items()):
        driven = share.get((car, 'driver'))
        if not driven:
            cov['km_unassigned'] += g.km
        for role in ROLES:
            by = share.get((car, role)) or {}
            total = sum(by.values())
            for key, n in by.items():
                e = people[key]
                e['km'] = (e['km'] or 0.0) + g.km * n / total
    for did, cash in (crew.get('cash') or {}).items():
        person(f'driver:{did}')['cash'] = dict(cash)
    for did, qty in (crew.get('tare') or {}).items():
        person(f'driver:{did}')['tare'] += float(qty)
    for e in people.values():
        e['cars'].sort()
        e['late'].sort(key=lambda x: (x['arrive'] or '', x['stop_id']))
    cov['km_unassigned'] = round(cov['km_unassigned'], 1)
    return {'people': people, 'coverage': cov}


CASH_KEYS = ('expected', 'short', 'collected')


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def _round(x: float | None, nd: int = 1) -> float | None:
    return round(x, nd) if x is not None else None


def _cash_add(acc: dict[str, Any] | None, c: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if c is None:
        return acc
    acc = acc or {'expected': 0.0, 'short': 0.0, 'collected': 0.0, 'handed': None, 'diff': None, 'handed_days': 0}
    for k in CASH_KEYS:
        acc[k] += float(c.get(k) or 0.0)
    if c.get('handed') is not None:
        acc['handed'] = (acc['handed'] or 0.0) + float(c['handed'])
        acc['diff'] = (acc['diff'] or 0.0) + float(c.get('diff') or 0.0)
        acc['handed_days'] += 1
    return acc


def _cash_json(c: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {**{k: round(float(c[k]), 2) for k in CASH_KEYS},
            'handed': round(c['handed'], 2) if c.get('handed') is not None else None,
            'diff': round(c['diff'], 2) if c.get('diff') is not None else None,
            'handed_days': c.get('handed_days', 1 if c.get('handed') is not None else 0)}


def period(days: Sequence[tuple[date, Mapping[str, Any]]], names: Mapping[int, str]) -> dict[str, Any]:
    """Сводки дней (day_summary) за период → строки людей (показатели за период и по дням, новые дни первыми) и
    покрытие за период."""
    rows: dict[str, dict[str, Any]] = {}
    cov: dict[str, float] = {}
    for day, summary in sorted(days, key=lambda x: x[0]):
        for k, v in summary['coverage'].items():
            cov[k] = cov.get(k, 0) + v
        for key, e in summary['people'].items():
            role, pid = key.split(':', 1)
            r = rows.setdefault(key, {'key': key, 'id': int(pid), 'role': role, 'name': names.get(int(pid)) or f'#{pid}',
                                      'days': 0, 'stops': 0, 'partial': 0, 'refused': 0, 'rated': 0, 'on_time': 0,
                                      'late_n': 0, 'delay_sum': 0.0, 'km': None, 'km_days': 0, 'cash': None,
                                      'tare': 0.0, 'detail': []})
            r['days'] += int(e['stops'] > 0)
            for k in ('stops', 'partial', 'refused', 'rated', 'on_time', 'late_n', 'delay_sum', 'tare'):
                r[k] += e[k]
            if e['km'] is not None:
                r['km'] = (r['km'] or 0.0) + e['km']
                r['km_days'] += 1
            r['cash'] = _cash_add(r['cash'], e['cash'])
            r['detail'].append({'date': day.isoformat(), 'cars': list(e['cars']), 'stops': e['stops'],
                                'partial': e['partial'], 'refused': e['refused'], 'rated': e['rated'],
                                'on_time': e['on_time'], 'on_time_pct': _pct(e['on_time'], e['rated']),
                                'late_mean_min': _round(e['delay_sum'] / e['late_n']) if e['late_n'] else None,
                                'km': _round(e['km']), 'cash': _cash_json(e['cash']), 'tare': round(e['tare'], 2),
                                'late': list(e['late'])})
    out = []
    for r in rows.values():
        r['detail'].reverse()
        late_n, delay_sum = r.pop('late_n'), r.pop('delay_sum')
        out.append({**r, 'on_time_pct': _pct(r['on_time'], r['rated']), 'late': late_n,
                    'late_mean_min': _round(delay_sum / late_n) if late_n else None, 'km': _round(r['km']),
                    'cash': _cash_json(r['cash']), 'tare': round(r['tare'], 2)})
    out.sort(key=lambda r: (r['role'] != 'driver', -r['stops'], r['name'], r['id']))
    coverage = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in cov.items()}
    return {'drivers': out, 'coverage': coverage}

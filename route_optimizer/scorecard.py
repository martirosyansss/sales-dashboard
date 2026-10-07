# -*- coding: utf-8 -*-
"""«Վարորդներ» — показатели водителей за период (как driver analytics Omnitracs / Routific), №83 и №87 п. 3 и 7.

Чистые функции — без Flask, БД и ERP. Вход — факт дня раздела «Առաքիչ» (CrewFacts: кто закрыл каждую точку, деньги и
тара по водителю) и GPS-факт машин дня из обучения «Развоза» (CarDay: км, прибытие по GPS и окно приёма точек —
actuals.stop_marks, плановое ETA клиентов — прогноз сборки отправленного водителям плана, превышения скорости и
стоянки вне магазинов — правила карты машин live.speed_alerts / live.stop_alerts, порядок объезда —
actuals.visit_metrics); литры к норме машино-дня считает views (_scorecard_fuel) и передаёт в period().

Правила:
- человек дня — водитель, чьё «заявление» (действующая доставка, правило §5 п. 12) определило статус точки; covered
  (товар отдан по заказу) — автор последней доставки поглощённых владельцем точек; автора нет — водитель машины дня
  (больше всех закрытых точек машины; ничья — меньший id). Առաքիչ (helper_id того же события) — отдельной строкой;
- Օրեր — дни, где человек закрыл хотя бы одну точку; Խանութներ — закрытые точки (full / partial / refused / covered);
- Ժամանակին — точка с плановым ETA и прибытием по GPS: прибытие ≤ ETA + LATE_SLACK_MIN и, если у магазина есть окно
  приёма, не позже конца окна и обслуживание не раньше начала (actuals.stop_marks). n/N — вовремя / оценено;
- опоздание точки — max(прибытие − ETA, если больше допуска; прибытие − конец окна); Միջին ուշացում — среднее по
  опоздавшим (раньше окна — «рано», в среднее не входит);
- Կմ — км машины дня по GPS (actuals.reconstruct) × доля человека в закрытых точках машины за день (с известным
  водителем): два водителя одной машины делят км; առաքիչ, бывший на 2 точках из 3, получает 2/3 км машины;
- показатели машины за день (скорость, стоянки, порядок, литры) — водителю машины дня (правило выше), не առաքիչ:
  - Արագություն — превышения скорости (дольше live_speed_sec выше live_speed_kmh подряд), раз и раз на 100 км GPS
    машино-дней с треком;
  - Կանգառ խանութից դուրս — минуты стоянок не у магазина и не на складе длиннее live_stop_min (обед — сверх обеда)
    от первого выезда со склада до возвращения последнего рейса (не вернулся — до ухода от последнего магазина).
    Зажигания в GPS нет: это не «простой с мотором», а стоянки вне плана;
  - Հերթականություն — доля обслуженных точек с плановым местом, объехавших в плановом порядке
    (1 − actuals.order_changes / ordered);
  - Վառելիք — (литры по заправкам − норма) / норма за машино-дни с покрытыми треком заправками (views._scorecard_fuel);
- Միավոր (0–100) — средневзвешенное подоценок WEIGHTS, каждая — линейно по SCALE; показатель без данных выпадает, веса
  остальных перенормируются; меньше MIN_DAYS дней или ни одного показателя с данными — «քիչ տվյալ» (enough_data
  false): без балла и места (только водители);
- точность ETA (п. 7) — по точкам с плановым ETA и прибытием по GPS: |прибытие − ETA| ≤ ETA_OK_MIN — точно, медиана и
  P80 абсолютной ошибки, раньше и позже допуска.
Неполные данные не выдумываются: нет GPS — км, «вовремя», скорость, стоянки нет; нет плана — точка не оценивается;
счётчики покрытия (coverage) показывают, сколько точек оценено и почему остальные — нет.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Protocol, Sequence

from .actuals import YEREVAN
from .learning import quantile

LATE_SLACK_MIN = 15.0          # прибытие позже ETA не больше чем на столько — ещё вовремя
ETA_OK_MIN = 15.0              # точность ETA (п. 7): |прибытие − ETA| не больше — прогноз точный
MAX_DAYS = 92                  # период запроса — не длиннее
MIN_DAYS = 3                   # меньше рабочих дней за период — «քիչ տվյալ»: без балла и места
DONE = ('full', 'partial', 'refused', 'covered')
ROLES = ('driver', 'helper')

# Итоговый балл 0–100 (решение №87 п. 3): вес показателя — в баллах из 100.
WEIGHTS = {'on_time': 35, 'order': 15, 'speed': 20, 'stops': 15, 'liters': 15}
# Подоценка 0–100: линейно между (значение «100 баллов», значение «0 баллов»), за пределами — 100 или 0.
SCALE = {
    'on_time': (95.0, 50.0),   # % вовремя: 95 и выше — 100, 50 и ниже — 0
    'order': (90.0, 50.0),     # % точек в плановом порядке: 90 и выше — 100, 50 и ниже — 0
    'speed': (0.0, 2.0),       # превышений на 100 км: ни одного — 100, 2 и больше — 0
    'stops': (0.0, 60.0),      # минут стоянок вне магазинов в среднем за машино-день: 0 — 100, 60 и больше — 0
    'liters': (5.0, 25.0),     # % литров сверх нормы: до +5 % (и экономия) — 100, +25 % и больше — 0
}


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
    плановое ETA (первое появление клиента в плане); превышений скорости и минут стоянок вне магазинов (None — трека
    нет); порядок объезда — (точек не в плановом порядке, обслуженных с плановым местом) или None (сравнивать не с
    чем); stop_eta — плановое ETA точки в её рейсе (тяжёлый заказ в нескольких рейсах — своё ETA у каждого рейса),
    главнее eta клиента."""
    km: float
    marks: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    eta: Mapping[int, datetime] = field(default_factory=dict)
    speed_events: int | None = None
    offroute_min: float | None = None
    order: tuple[int, int] | None = None
    stop_eta: Mapping[str, datetime] = field(default_factory=dict)


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


def eta_error(mark: Mapping[str, Any], eta: datetime) -> float:
    """Ошибка планового ETA точки, минуты: прибытие по GPS − ETA (плюс — позже плана)."""
    return round((mark['arrive'] - eta).total_seconds() / 60.0, 1)


def _entry() -> dict[str, Any]:
    return {'cars': [], 'stops': 0, 'partial': 0, 'refused': 0, 'rated': 0, 'on_time': 0, 'late_n': 0,
            'delay_sum': 0.0, 'late': [], 'km': None, 'cash': None, 'tare': 0.0,
            # показатели машин, где человек — водитель машины дня
            'speed_days': 0, 'speed_events': 0, 'speed_km': 0.0, 'stop_days': 0, 'offroute_min': 0.0,
            'order_changes': 0, 'ordered': 0}


def day_summary(crew: Mapping[str, Any], cars: Mapping[str, CarDay], slack: float = LATE_SLACK_MIN) -> dict[str, Any]:
    """Факт дня → {'people': {'роль:id': показатели дня}, 'mains': {машина: водитель машины дня}, 'eta_errors': ошибки
    ETA точек (eta_error), 'coverage': счётчики покрытия} (правила — в описании модуля). Результат — только из входа:
    для прошлых дней его можно хранить (кэш по отпечатку данных дня)."""
    closed = [s for s in crew.get('stops') or () if s.get('status') in DONE]
    counts: dict[str, dict[int, int]] = {}
    for s in closed:
        if s.get('driver_id') is not None:
            mine = counts.setdefault(s['car_code'], {})
            mine[s['driver_id']] = mine.get(s['driver_id'], 0) + 1
    main = {car: min(c, key=lambda d: (-c[d], d)) for car, c in counts.items()}
    people: dict[str, dict[str, Any]] = {}
    share: dict[tuple[str, str], dict[str, int]] = {}   # (машина, роль) → человек → закрытых точек
    errors: list[float] = []
    cov = {'closed': len(closed), 'unattributed': 0, 'rated': 0, 'no_eta': 0, 'no_gps': 0, 'car_days': 0,
           'car_days_gps': 0, 'km_unassigned': 0.0}

    def person(key: str) -> dict[str, Any]:
        return people.setdefault(key, _entry())

    for s in closed:
        car = s['car_code']
        g = cars.get(car)
        mark = g.marks.get(s['stop_id']) if g is not None else None
        eta = None
        if g is not None:
            eta = g.stop_eta.get(s['stop_id'])
            if eta is None and s.get('customer_id') is not None:
                eta = g.eta.get(s['customer_id'])
        if mark is not None and eta is not None:
            errors.append(eta_error(mark, eta))   # точность плана — по всем точкам, и без известного водителя
        did = s.get('driver_id') if s.get('driver_id') is not None else main.get(car)
        if did is None:
            cov['unattributed'] += 1
            continue
        keys = [f'driver:{did}']
        if s.get('helper_id') is not None and s['helper_id'] != did:
            keys.append(f'helper:{s["helper_id"]}')
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
            continue   # закрытых точек с известным водителем нет — ни км, ни показатели машины никому
        total = sum(driven.values())   # все закрытые точки машины с известным водителем: доля и у առաքիչ — от них
        for role in ROLES:
            for key, n in (share.get((car, role)) or {}).items():
                e = people[key]
                e['km'] = (e['km'] or 0.0) + g.km * n / total
        e = people[f'driver:{main[car]}']
        if g.speed_events is not None:
            e['speed_days'] += 1
            e['speed_events'] += g.speed_events
            e['speed_km'] += g.km
        if g.offroute_min is not None:
            e['stop_days'] += 1
            e['offroute_min'] += g.offroute_min
        if g.order is not None:
            e['order_changes'] += g.order[0]
            e['ordered'] += g.order[1]
    for did, cash in (crew.get('cash') or {}).items():
        person(f'driver:{did}')['cash'] = dict(cash)
    for did, qty in (crew.get('tare') or {}).items():
        person(f'driver:{did}')['tare'] += float(qty)
    for e in people.values():
        e['cars'].sort()
        e['late'].sort(key=lambda x: (x['arrive'] or '', x['stop_id']))
    cov['km_unassigned'] = round(cov['km_unassigned'], 1)
    return {'people': people, 'mains': dict(sorted(main.items())), 'eta_errors': errors, 'coverage': cov}


CASH_KEYS = ('expected', 'short', 'collected')
SUMS = ('stops', 'partial', 'refused', 'rated', 'on_time', 'late_n', 'delay_sum', 'tare', 'speed_days', 'speed_events',
        'speed_km', 'stop_days', 'offroute_min', 'order_changes', 'ordered')


def _pct(n: float, d: float) -> float | None:
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


def sub_score(metric: str, value: float) -> float:
    """Подоценка 0–100 показателя: линейно между SCALE[metric] = (значение «100», значение «0»), с отсечкой."""
    full, zero = SCALE[metric]
    t = (value - zero) / (full - zero)
    return 100.0 * min(1.0, max(0.0, t))


def score(values: Mapping[str, float | None]) -> tuple[float | None, dict[str, dict[str, float]]]:
    """(балл 0–100 или None — ни одного показателя, разбивка: показатель → {value, score, weight, share}). Показатель
    без значения (None) выпадает, веса остальных перенормируются: share — доля в балле, %."""
    have = {k: float(v) for k, v in values.items() if k in WEIGHTS and v is not None}
    total = sum(WEIGHTS[k] for k in have)
    if not total:
        return None, {}
    raw = {k: sub_score(k, v) for k, v in have.items()}
    parts = {k: {'value': round(have[k], 2), 'score': round(raw[k], 1), 'weight': WEIGHTS[k],
                 'share': round(100.0 * WEIGHTS[k] / total, 1)} for k in WEIGHTS if k in have}
    return round(sum(raw[k] * WEIGHTS[k] for k in have) / total, 1), parts


def eta_accuracy(errors: Sequence[float], ok_min: float = ETA_OK_MIN) -> dict[str, Any]:
    """Точность планового ETA (п. 7) по ошибкам точек (eta_error, минуты): n, в пределах ±ok_min (n и %), раньше и
    позже допуска (n и %), медиана и P80 абсолютной ошибки (квантиль с линейной интерполяцией), минуты."""
    n = len(errors)
    within = sum(1 for e in errors if abs(e) <= ok_min)
    early = sum(1 for e in errors if e < -ok_min)
    late = n - within - early
    absolute = sorted(abs(e) for e in errors)
    return {'n': n, 'ok_min': ok_min, 'within_n': within, 'within_pct': _pct(within, n),
            'early_n': early, 'early_pct': _pct(early, n), 'late_n': late, 'late_pct': _pct(late, n),
            'median_abs_min': round(quantile(absolute, 0.5), 1) if n else None,
            'p80_abs_min': round(quantile(absolute, 0.8), 1) if n else None}


def _detail(day: date, e: Mapping[str, Any]) -> dict[str, Any]:
    return {'date': day.isoformat(), 'cars': list(e['cars']), 'stops': e['stops'], 'partial': e['partial'],
            'refused': e['refused'], 'rated': e['rated'], 'on_time': e['on_time'],
            'on_time_pct': _pct(e['on_time'], e['rated']),
            'late_mean_min': _round(e['delay_sum'] / e['late_n']) if e['late_n'] else None,
            'km': _round(e['km']), 'cash': _cash_json(e['cash']), 'tare': round(e['tare'], 2),
            'speed_events': e['speed_events'] if e['speed_days'] else None,
            'offroute_min': round(e['offroute_min']) if e['stop_days'] else None,
            'order_pct': _pct(e['ordered'] - e['order_changes'], e['ordered']), 'ordered': e['ordered'],
            'liters_vs_norm_pct': None, 'late': list(e['late'])}


def _metrics(r: Mapping[str, Any]) -> dict[str, Any]:
    """Показатели строки за период из сумм (SUMS и литры)."""
    fuel_norm = r['fuel_norm_l']
    return {'on_time_pct': _pct(r['on_time'], r['rated']),
            'late_mean_min': _round(r['delay_sum'] / r['late_n']) if r['late_n'] else None,
            'order_pct': _pct(r['ordered'] - r['order_changes'], r['ordered']),
            'speed_events': r['speed_events'] if r['speed_days'] else None,
            'speed_per_100km': round(100.0 * r['speed_events'] / r['speed_km'], 2) if r['speed_km'] > 0 else None,
            'offroute_min': round(r['offroute_min']) if r['stop_days'] else None,
            'offroute_min_per_day': round(r['offroute_min'] / r['stop_days'], 1) if r['stop_days'] else None,
            'liters_vs_norm_pct': (round(100.0 * (r['fuel_fact_l'] - fuel_norm) / fuel_norm, 1)
                                   if fuel_norm > 0 else None)}


def period(days: Sequence[tuple[date, Mapping[str, Any]]], names: Mapping[int, str],
           fuel: Mapping[tuple[str, str], tuple[float, float]] | None = None) -> dict[str, Any]:
    """Сводки дней (day_summary) за период → строки людей (показатели, балл и место за период, по дням — новые первыми),
    точность ETA и покрытие за период. fuel — (день, машина) → (литры по заправкам, норма) машино-дней с покрытыми
    треком заправками: водителю машины дня (mains)."""
    rows: dict[str, dict[str, Any]] = {}
    cov: dict[str, float] = {}
    errors: list[float] = []
    for day, summary in sorted(days, key=lambda x: x[0]):
        ds = day.isoformat()
        for k, v in summary['coverage'].items():
            cov[k] = cov.get(k, 0) + v
        errors.extend(summary.get('eta_errors') or ())
        for key, e in summary['people'].items():
            role, pid = key.split(':', 1)
            r = rows.setdefault(key, {'key': key, 'id': int(pid), 'role': role,
                                      'name': names.get(int(pid)) or f'#{pid}',
                                      'days': 0, **{k: 0 for k in SUMS}, 'delay_sum': 0.0, 'km': None, 'km_days': 0,
                                      'cash': None, 'tare': 0.0, 'fuel_days': 0, 'fuel_fact_l': 0.0,
                                      'fuel_norm_l': 0.0, 'detail': []})
            r['days'] += int(e['stops'] > 0)
            for k in SUMS:
                r[k] += e[k]
            if e['km'] is not None:
                r['km'] = (r['km'] or 0.0) + e['km']
                r['km_days'] += 1
            r['cash'] = _cash_add(r['cash'], e['cash'])
            r['detail'].append(_detail(day, e))
        for car, did in (summary.get('mains') or {}).items():
            got = (fuel or {}).get((ds, car))
            r = rows.get(f'driver:{did}')
            if got is None or r is None or got[1] <= 0:
                continue
            r['fuel_days'] += 1
            r['fuel_fact_l'] += got[0]
            r['fuel_norm_l'] += got[1]
            d = r['detail'][-1]   # строка этого дня — только что добавлена (водитель машины закрыл её точки)
            fact, norm = d.get('fuel') or (0.0, 0.0)
            d['fuel'] = (fact + got[0], norm + got[1])
    out = []
    for r in rows.values():
        r['detail'].reverse()
        for d in r['detail']:
            if 'fuel' in d:   # литры дня (по заправкам, норма) — сумма машин дня, где он водитель
                fact, norm = d.pop('fuel')
                d.update(fuel_fact_l=round(fact, 1), fuel_norm_l=round(norm, 1),
                         liters_vs_norm_pct=_pct(fact - norm, norm))
        m = _metrics(r)
        total, parts = score({'on_time': m['on_time_pct'], 'order': m['order_pct'], 'speed': m['speed_per_100km'],
                              'stops': m['offroute_min_per_day'], 'liters': m['liters_vs_norm_pct']}) \
            if r['role'] == 'driver' else (None, {})
        # хватает данных: не меньше MIN_DAYS дней и (у водителя) хотя бы один показатель балла с данными
        enough = r['days'] >= MIN_DAYS and (r['role'] != 'driver' or total is not None)
        late_n = r.pop('late_n')
        r.pop('delay_sum')
        out.append({**r, **m, 'late': late_n, 'km': _round(r['km']), 'cash': _cash_json(r['cash']),
                    'tare': round(r['tare'], 2), 'speed_km': round(r['speed_km'], 1),
                    'offroute_min': m['offroute_min'], 'speed_events': m['speed_events'],
                    'fuel_fact_l': round(r['fuel_fact_l'], 1), 'fuel_norm_l': round(r['fuel_norm_l'], 1),
                    'enough_data': enough, 'score': total if enough else None, 'parts': parts, 'rank': None})
    ranked = [r for r in out if r['role'] == 'driver' and r['score'] is not None]
    for r in ranked:
        r['rank'] = 1 + sum(1 for x in ranked if x['score'] > r['score'])   # равный балл — одно место
    out.sort(key=lambda r: (r['role'] != 'driver', -r['stops'], r['name'], r['id']))
    coverage = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in cov.items()}
    return {'drivers': out, 'ranked': len(ranked), 'eta': eta_accuracy(errors), 'coverage': coverage}


def rules() -> dict[str, Any]:
    """Правила для страницы и APK: допуски, пороги и веса балла."""
    return {'late_slack_min': LATE_SLACK_MIN, 'max_days': MAX_DAYS, 'min_days': MIN_DAYS, 'eta_ok_min': ETA_OK_MIN,
            'weights': dict(WEIGHTS), 'scale': {k: list(v) for k, v in SCALE.items()}}

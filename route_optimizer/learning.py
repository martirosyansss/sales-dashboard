# -*- coding: utf-8 -*-
"""Обучение «Развоза» по факту машин (learning-loop-plan.md, этапы 4–5) — чистые функции, без Flask, БД и ERP.

Что учится (каждую ночь и по кнопке «Пересчитать»), из факта actuals.reconstruct по треку APK:
- unload — разгрузка на точке = a·точек + b·тонн доставлено (+ поправка магазина) → TruckNorms.unload_min_per_stop /
  unload_min_per_tonne (+ unload_extra по точке магазина);
- loading — загрузка на складе = a + b·тонн рейса → TruckNorms.warehouse_load_fixed_min / warehouse_load_min_per_tonne;
- travel — время в пути грузовиков: множитель «факт / модель» по (город|область, будни|выходные, час) → поверх
  norms.traffic (TrafficProfile); модель — дорожная модель расчёта (road_model_id), сменилась модель — профиль не
  действует;
- fuel — расход машины л/100 км пустой/полной по заправкам «до полного бака» (литры / км одометра, загрузка — по
  участкам трека) → FleetTruck.fuel_empty_l_per_100km / fuel_full_l_per_100km.

Правило принятия (одно для всех): обучение — на днях до отложенной недели (TRAIN_DAYS дней), проверка — на последних
HOLDOUT_DAYS днях (до вчера включительно; у расхода — последние 4 интервала заправок, как measurements._fit); новая
норма принимается, только если средняя абсолютная ошибка на проверке меньше, чем у действующей нормы, не меньше чем на
MIN_GAIN (2%), и данных не меньше порогов (константы ниже). Иначе действует прежняя (выученная раньше или ручная из
настроек). Применяется норма, обученная на днях до отложенной недели, — ровно та, что прошла проверку.
Результат каждого прогона — строка learned_norms (store.save_learned): что выучено, данных, ошибка до/после, принято.
Действующая норма вида — последняя принятая (travel — ещё и той же дорожной модели); автообучение вида выключено —
действуют ручные настройки.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from . import actuals as ac
from .geo import Fix, Point, in_city
from .measurements import _fit
from .traffic_validation import TrafficProfile

KINDS = ('unload', 'loading', 'travel', 'fuel')
KIND_TITLES = {'unload': 'Разгрузка у магазина', 'loading': 'Загрузка на складе', 'travel': 'Скорость машин по часам',
               'fuel': 'Расход топлива'}
HOLDOUT_DAYS = 7
TRAIN_DAYS = 120
MIN_GAIN = 0.02
# пороги данных: (наблюдений в обучении, дней в обучении, наблюдений в проверке, дней в проверке)
UNLOAD_MIN = (30, 5, 10, 2)
LOADING_MIN = (15, 5, 5, 2)
TRAVEL_MIN_TEST = (20, 3)            # участков и дней проверки в ячейках профиля
TRAVEL_BUCKET = (3, 45.0)            # ячейка профиля: дней и модельных минут в обучении
TRAVEL_RATIO = (0.5, 3.0)            # множитель времени в пути — в этих пределах
LEG_RATIO_OUTLIER = (0.2, 5.0)       # участок «факт / модель» вне — не езда (заезд без стоянки 2 мин и т. п.)
LEG_MIN_KM = 0.2
STORE_MIN_OBS = 5                    # поправка магазина — от 5 визитов
STORE_SHRINK = 5.0                   # и стягивается к 0: × n / (n + 5)
STORE_OFFSET_MAX = 60.0
UNLOAD_MAX_MIN = 180.0               # стоянка у магазина дольше — не разгрузка
FUEL_MIN_INTERVALS = 12              # как measurements._fit
FUEL_MIN_KM = 50.0                   # интервал заправок короче — не считается
FUEL_L100 = (3.0, 80.0)
FUEL_TRACK_COVER = 0.6               # участки трека покрывают не меньше 60% км одометра интервала
REFUEL_KM_PER_DAY = 1500.0           # как courier.events: прирост одометра больше — сомнителен
NIGHTLY_AT = (3, 0)                  # ночной прогон — 03:00 Еревана


# --- наблюдения ---

@dataclass(frozen=True)
class UnloadObs:
    day: date
    n: int                    # точек плана в этой стоянке (общая разгрузка — несколько)
    tonnes: float             # доставлено, т
    minutes: float
    customers: tuple[int, ...]


@dataclass(frozen=True)
class LoadObs:
    day: date
    tonnes: float             # вес накладных рейса, т
    minutes: float


@dataclass(frozen=True)
class LegObs:
    day: date
    city: bool
    weekend: bool
    hour: int
    minutes: float            # факт
    model: float              # модель без поправок по часам: км модели / скорость зоны
    current: float            # действующий прогноз (с действующим профилем часов)


@dataclass(frozen=True)
class FuelObs:
    day: date                 # дата заправки, закрывающей интервал
    load: float               # средняя загрузка по км одометра (доля тоннажа)
    l100: float


@dataclass(frozen=True)
class Outcome:
    kind: str
    scope: str                # '' — весь парк; машина — для fuel
    accepted: bool
    reason: str
    params: dict[str, Any] | None = None
    model_id: str | None = None
    n_obs: int = 0
    n_test: int = 0
    train_from: str | None = None
    train_to: str | None = None
    test_from: str | None = None
    test_to: str | None = None
    mae_before: float | None = None
    mae_after: float | None = None


def windows(today: date) -> tuple[date, date]:
    """(начало обучения, начало проверки): обучение — [train_from, test_from), проверка — [test_from, today)."""
    test_from = today - timedelta(days=HOLDOUT_DAYS)
    return test_from - timedelta(days=TRAIN_DAYS), test_from


def _split(obs: Sequence[Any], today: date) -> tuple[list[Any], list[Any]]:
    train_from, test_from = windows(today)
    return ([o for o in obs if train_from <= o.day < test_from], [o for o in obs if test_from <= o.day < today])


def _enough(train: Sequence[Any], test: Sequence[Any], need: tuple[int, int, int, int]) -> str | None:
    n1, d1, n2, d2 = need
    got = (len(train), len({o.day for o in train}), len(test), len({o.day for o in test}))
    if got[0] < n1 or got[1] < d1 or got[2] < n2 or got[3] < d2:
        return (f'мало данных: обучение {got[0]} из {n1} (дней {got[1]} из {d1}), '
                f'проверка {got[2]} из {n2} (дней {got[3]} из {d2})')
    return None


def _mae(pairs: Iterable[tuple[float, float]]) -> float:
    pairs = list(pairs)
    return math.fsum(abs(p - y) for p, y in pairs) / len(pairs)


def _span(obs: Sequence[Any]) -> tuple[str | None, str | None]:
    days = sorted(o.day for o in obs)
    return (days[0].isoformat(), days[-1].isoformat()) if days else (None, None)


def _verdict(before: float, after: float) -> tuple[bool, str]:
    if before > 0 and after <= before * (1 - MIN_GAIN):
        return True, f'принято: ошибка {before:.2f} → {after:.2f}'
    return False, f'не лучше действующей нормы хотя бы на {MIN_GAIN:.0%}: ошибка {before:.2f} → {after:.2f}'


def huber_fit(rows: Sequence[tuple[float, float, float]], iterations: int = 50) -> tuple[float, float]:
    """Устойчивая (Хьюбер, IRLS) оценка y ≈ a·x1 + b·x2 без свободного члена, a, b ≥ 0. Детерминирована: фиксированное
    число итераций, порог Хьюбера — 1,345 × устойчивый разброс (1,4826 × MAD остатков)."""
    w = [1.0] * len(rows)
    a = b = 0.0
    for _ in range(iterations):
        s11 = math.fsum(wi * x1 * x1 for wi, (x1, _, _) in zip(w, rows))
        s12 = math.fsum(wi * x1 * x2 for wi, (x1, x2, _) in zip(w, rows))
        s22 = math.fsum(wi * x2 * x2 for wi, (_, x2, _) in zip(w, rows))
        t1 = math.fsum(wi * x1 * y for wi, (x1, _, y) in zip(w, rows))
        t2 = math.fsum(wi * x2 * y for wi, (_, x2, y) in zip(w, rows))
        det = s11 * s22 - s12 * s12
        na, nb = ((t1 * s22 - t2 * s12) / det, (s11 * t2 - s12 * t1) / det) if abs(det) > 1e-12 * max(1.0, s11 * s22) \
            else (t1 / s11 if s11 > 0 else 0.0, 0.0)
        if nb < 0:
            na, nb = (t1 / s11 if s11 > 0 else 0.0), 0.0
        if na < 0:
            na, nb = 0.0, (t2 / s22 if s22 > 0 else 0.0)
        na, nb = max(0.0, na), max(0.0, nb)
        done = abs(na - a) < 1e-9 and abs(nb - b) < 1e-9
        a, b = na, nb
        res = [y - a * x1 - b * x2 for x1, x2, y in rows]
        scale = 1.4826 * median(abs(r) for r in res) if res else 0.0
        c = 1.345 * scale
        w = [1.0 if c <= 0 or abs(r) <= c else c / abs(r) for r in res]
        if done:
            break
    return a, b


# --- обучение по видам ---

def fit_unload(obs: Sequence[UnloadObs], current: Callable[[UnloadObs], float], today: date) -> Outcome:
    """Разгрузка = a·точек + b·тонн (+ поправка магазина, только при ≥ STORE_MIN_OBS одиночных визитах, со стягиванием
    к 0). current — прогноз действующей нормы для наблюдения."""
    train, test = _split(obs, today)
    span = dict(zip(('train_from', 'train_to'), _span(train)), **dict(zip(('test_from', 'test_to'), _span(test))))
    short = _enough(train, test, UNLOAD_MIN)
    if short:
        return Outcome('unload', '', False, short, n_obs=len(train), n_test=len(test), **span)
    a, b = huber_fit([(float(o.n), o.tonnes, o.minutes) for o in train])
    a, b = round(min(a, 120.0), 2), round(min(b, 120.0), 2)
    residuals: dict[int, list[float]] = {}
    for o in train:
        if o.n == 1 and len(o.customers) == 1:
            residuals.setdefault(o.customers[0], []).append(o.minutes - a - b * o.tonnes)
    offsets: dict[int, float] = {}
    for cid, rs in sorted(residuals.items()):
        if len(rs) >= STORE_MIN_OBS:
            off = round(max(-a, min(STORE_OFFSET_MAX, median(rs) * len(rs) / (len(rs) + STORE_SHRINK))), 1)
            if abs(off) >= 0.5:
                offsets[cid] = off

    def predict(o: UnloadObs) -> float:
        return a * o.n + b * o.tonnes + math.fsum(offsets.get(c, 0.0) for c in o.customers)
    before, after = _mae((current(o), o.minutes) for o in test), _mae((predict(o), o.minutes) for o in test)
    ok, why = _verdict(before, after)
    return Outcome('unload', '', ok, why, {'per_stop_min': a, 'per_tonne_min': b,
                                           'store_offsets': {str(c): v for c, v in offsets.items()}},
                   n_obs=len(train), n_test=len(test), mae_before=round(before, 3), mae_after=round(after, 3), **span)


def fit_loading(obs: Sequence[LoadObs], current: Callable[[LoadObs], float], today: date) -> Outcome:
    """Загрузка рейса на складе = a + b·тонн."""
    train, test = _split(obs, today)
    span = dict(zip(('train_from', 'train_to'), _span(train)), **dict(zip(('test_from', 'test_to'), _span(test))))
    short = _enough(train, test, LOADING_MIN)
    if short:
        return Outcome('loading', '', False, short, n_obs=len(train), n_test=len(test), **span)
    a, b = huber_fit([(1.0, o.tonnes, o.minutes) for o in train])
    a, b = round(min(a, 240.0), 2), round(min(b, 120.0), 2)
    before = _mae((current(o), o.minutes) for o in test)
    after = _mae((a + b * o.tonnes, o.minutes) for o in test)
    ok, why = _verdict(before, after)
    return Outcome('loading', '', ok, why, {'fixed_min': a, 'per_tonne_min': b}, n_obs=len(train), n_test=len(test),
                   mae_before=round(before, 3), mae_after=round(after, 3), **span)


def _bucket(o: LegObs) -> tuple[bool, int, int]:
    return o.city, int(o.weekend), o.hour


def fit_travel(obs: Sequence[LegObs], today: date, model_id: str, ref: Mapping[str, Any]) -> Outcome:
    """Множитель времени в пути «факт / модель» по ячейкам (город|область, будни|выходные, час) — ячейка при ≥ 3 днях и
    ≥ 45 модельных минутах обучения; проверка — участки отложенной недели в ячейках профиля, действующий прогноз
    против модель × множитель. ref — скорости (и извилистость без карты дорог), с которыми считалась модель."""
    train, test = _split(obs, today)
    span = dict(zip(('train_from', 'train_to'), _span(train)), **dict(zip(('test_from', 'test_to'), _span(test))))
    sums: dict[tuple[bool, int, int], list[float]] = {}
    days: dict[tuple[bool, int, int], set[date]] = {}
    for o in train:
        k = _bucket(o)
        acc = sums.setdefault(k, [0.0, 0.0])
        acc[0] += o.minutes
        acc[1] += o.model
        days.setdefault(k, set()).add(o.day)
    lo, hi = TRAVEL_RATIO
    ratio = {k: round(max(lo, min(hi, a / m)), 3) for k, (a, m) in sorted(sums.items())
             if len(days[k]) >= TRAVEL_BUCKET[0] and m >= TRAVEL_BUCKET[1] and m > 0}
    held = [o for o in test if _bucket(o) in ratio]
    if not ratio or len(held) < TRAVEL_MIN_TEST[0] or len({o.day for o in held}) < TRAVEL_MIN_TEST[1]:
        return Outcome('travel', '', False,
                       f'мало данных: ячеек профиля {len(ratio)}, участков проверки в них {len(held)} из '
                       f'{TRAVEL_MIN_TEST[0]} (дней {len({o.day for o in held})} из {TRAVEL_MIN_TEST[1]})',
                       model_id=model_id, n_obs=len(train), n_test=len(held), **span)
    before = _mae((o.current, o.minutes) for o in held)
    after = _mae((o.model * ratio[_bucket(o)], o.minutes) for o in held)
    ok, why = _verdict(before, after)
    return Outcome('travel', '', ok, why,
                   {'factors': [[int(c), w, h, r] for (c, w, h), r in sorted(ratio.items())], 'ref': dict(ref)},
                   model_id, len(train), len(held), mae_before=round(before, 3), mae_after=round(after, 3), **span)


def fit_fuel(obs: Sequence[FuelObs], current: Callable[[float], float], car: str) -> Outcome:
    """Расход л/100 км = пустой + (полный − пустой) × загрузка — measurements._fit (≥ 12 интервалов, разброс загрузки ≥
    20%, проверка — последние 4 интервала, модель лучше среднего и каждый интервал проверки в пределах 20%); зависимость
    от загрузки не подтвердилась — один расход (медиана обучения). Принимается при ошибке на проверке на ≥ 2% меньше,
    чем у действующей нормы машины (current(загрузка) → л/100 км)."""
    rows = sorted(({'day': o.day.isoformat(), 'load': o.load, 'l100': o.l100} for o in obs),
                  key=lambda r: (r['day'], r['load'], r['l100']))
    if len(rows) < FUEL_MIN_INTERVALS or len({r['day'] for r in rows}) < FUEL_MIN_INTERVALS:
        return Outcome('fuel', car, False, f'мало данных: интервалов между полными баками {len(rows)} из '
                       f'{FUEL_MIN_INTERVALS}', n_obs=len(rows))
    train, test = rows[:-4], rows[-4:]
    fit = _fit(rows, 'load', 'l100')
    if fit is not None and 1 <= fit['base'] <= fit['base'] + fit['slope'] <= 80:
        empty, full = fit['base'], round(fit['base'] + fit['slope'], 2)
    else:
        empty = full = round(median(r['l100'] for r in train), 2)

    def predict(load: float) -> float:
        return empty + (full - empty) * load
    before = _mae((current(r['load']), r['l100']) for r in test)
    after = _mae((predict(r['load']), r['l100']) for r in test)
    ok, why = _verdict(before, after)
    return Outcome('fuel', car, ok, why, {'empty_l100': empty, 'full_l100': full}, n_obs=len(train), n_test=len(test),
                   train_from=train[0]['day'], train_to=train[-1]['day'], test_from=test[0]['day'],
                   test_to=test[-1]['day'], mae_before=round(before, 3), mae_after=round(after, 3))


# --- действующие нормы и применение в расчёте ---

@dataclass(frozen=True)
class InEffect:
    """Выученные нормы, действующие в расчёте (None / пусто — ручные настройки)."""
    unload: Mapping[str, Any] | None = None
    loading: Mapping[str, Any] | None = None
    travel: Mapping[str, Any] | None = None        # params + 'model_id'
    fuel: Mapping[str, Mapping[str, Any]] | None = None   # машина → params

    def __bool__(self) -> bool:
        return bool(self.unload or self.loading or self.travel or self.fuel)


def in_effect(rows: Sequence[Mapping[str, Any]], auto: Mapping[str, bool], model_id: str | None) -> InEffect:
    """Строки learned_norms (по возрастанию run_day) → действующие нормы: по (вид, машина) — последняя принятая;
    travel — последняя принятая той же дорожной модели (model_id; None — дорожная модель не поддерживается); вид с
    выключенным автообучением — не действует."""
    last: dict[tuple[str, str], Mapping[str, Any]] = {}
    for r in rows:
        if not r['accepted'] or not auto.get(r['kind'], True) or r['params'] is None:
            continue
        if r['kind'] == 'travel' and (model_id is None or r['model_id'] != model_id):
            continue
        last[(r['kind'], r['scope'])] = r
    fuel = {scope: r['params'] for (kind, scope), r in sorted(last.items()) if kind == 'fuel'}
    travel = last.get(('travel', ''))
    return InEffect(last[('unload', '')]['params'] if ('unload', '') in last else None,
                    last[('loading', '')]['params'] if ('loading', '') in last else None,
                    {**travel['params'], 'model_id': travel['model_id']} if travel is not None else None,
                    fuel or None)


def road_model_id(norms: Any) -> str | None:
    """Дорожная модель расчёта времени в пути: карта дорог (её версия) или по прямой × извилистость. Внешний поставщик
    (Яндекс) считает время сам — поправка по часам к нему не применяется (None)."""
    if getattr(norms, 'provider', None) is not None:
        return None
    roads = getattr(norms, 'roads', None)
    return f'roads:{roads.version}' if roads is not None else 'straight'


def _speed(norms: Any, city: bool) -> float:
    return float(norms.speed_city_kmh if city else norms.speed_region_kmh)


def model_ref(norms: Any) -> dict[str, Any]:
    """С какими скоростями (и извилистостью без карты) считалась модель обучения — чтобы применить множитель при
    других скоростях настроек: время модели = км / скорость."""
    return {'speed_city_kmh': norms.speed_city_kmh, 'speed_region_kmh': norms.speed_region_kmh,
            'detour': norms.detour if getattr(norms, 'roads', None) is None else None}


def speed_factors(travel: Mapping[str, Any], norms: Any) -> dict[tuple[bool, int, int], float]:
    """Множители «факт / модель» времени → множители скорости TrafficProfile при текущих скоростях: время на участке
    = км_тек / (v_тек · f) должно равняться r · км_обуч / v_обуч, км_тек / км_обуч = извилистость_тек / извилистость_обуч
    (по карте дорог — 1)."""
    ref = travel.get('ref') or {}
    km_ratio = (float(norms.detour) / float(ref['detour'])) if ref.get('detour') else 1.0
    out = {}
    for c, w, h, r in travel.get('factors') or ():
        city = bool(c)
        v_ref = float(ref.get('speed_city_kmh' if city else 'speed_region_kmh') or _speed(norms, city))
        out[(city, int(w), int(h))] = v_ref * km_ratio / (_speed(norms, city) * float(r))
    return out


def apply_learned(norms: Any, tn: Any, trucks: Mapping[str, Any], eff: InEffect,
                  customer_points: Mapping[int, Point]) -> tuple[Any, Any, dict[str, Any]]:
    """Действующие выученные нормы → (norms, нормы машин, машины) расчёта «Развоза». Без выученных — те же объекты.
    customer_points — клиент → точка дня (поправка разгрузки магазина — по точке, как её видит fleet)."""
    trucks = dict(trucks)
    if eff.unload:
        p = eff.unload
        extra = {customer_points[int(c)]: float(v) for c, v in sorted((p.get('store_offsets') or {}).items())
                 if int(c) in customer_points}
        tn = replace(tn, unload_min_per_stop=float(p['per_stop_min']), unload_min_per_tonne=float(p['per_tonne_min']),
                     unload_extra=extra)
    if eff.loading:
        tn = replace(tn, warehouse_load_fixed_min=float(eff.loading['fixed_min']),
                     warehouse_load_min_per_tonne=float(eff.loading['per_tonne_min']), loading_configured=True)
    if eff.travel and road_model_id(norms) == eff.travel.get('model_id'):
        factors = dict(norms.traffic.factors) if norms.traffic is not None else {}
        factors.update(speed_factors(eff.travel, norms))
        report = dict(norms.traffic.report) if norms.traffic is not None else {}
        report.update({'trucks': 'learned', 'truck_hours': len(eff.travel.get('factors') or ())})
        norms = replace(norms, traffic=TrafficProfile(factors, report))
    for code, p in (eff.fuel or {}).items():
        if code in trucks:
            trucks[code] = replace(trucks[code], fuel_empty_l_per_100km=float(p['empty_l100']),
                                   fuel_full_l_per_100km=float(p['full_l100']))
    return norms, tn, trucks


# --- наблюдения из факта ---

class FleetFacts(Protocol):
    """Факт машин из «Առաքիչ» (courier.facts.FactsSource); подключает app_v2 (route_optimizer.attach_fleet_facts)."""

    def car_days(self, since: str, until: str) -> list[tuple[str, str]]:
        """(машина, день) с треком."""
        ...

    def day(self, car_code: str, day: str) -> dict[str, Any]:
        """{'track': [(at_ms, lat, lon, acc, spd)], 'stops': [{stop_id, customer_id, lat, lon, weight_kg, seq,
        delivered_share}]}."""
        ...

    def refuels(self) -> list[dict[str, Any]]:
        """Заправки: id, car_code, date, at, at_utc, payload, flags, superseded."""
        ...


def track_fixes(track: Iterable[Sequence[Any]]) -> list[Fix]:
    """(at_ms, lat, lon, acc, …) → geo.Fix (момент — Ереван)."""
    return [Fix(datetime.fromtimestamp(p[0] / 1000.0, ac.YEREVAN), float(p[1]), float(p[2]), float(p[3]))
            for p in track]


def plan_stops(stops: Sequence[Mapping[str, Any]], ranks: Mapping[int, int],
               windows_by_customer: Mapping[int, tuple[float, float]]) -> list[ac.PlanStop]:
    """Точки /day машины → PlanStop: место в плане — по плану «Развоза» (ranks: клиент → место), без плана — seq /day;
    окно приёма — из настроек магазина; доставлено — доля по отметке водителя × вес накладной."""
    out = []
    for s in stops:
        lat, lon, cid = s.get('lat'), s.get('lon'), s.get('customer_id')
        point = (float(lat), float(lon)) if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else None
        kg = float(s.get('weight_kg') or 0.0)
        share = s.get('delivered_share')
        rank = ranks.get(cid) if ranks else s.get('seq')
        out.append(ac.PlanStop(str(s.get('stop_id')), cid, point, kg, kg * share if share is not None else None,
                               windows_by_customer.get(cid) if cid is not None else None,
                               rank if isinstance(rank, int) else None))
    return out


def draft_ranks(draft: Mapping[str, Any] | None, truck: str) -> tuple[dict[int, int], int]:
    """План «Развоза» дня (черновик) → (клиент → место в объезде машины за день, рейсов машины)."""
    ranks: dict[int, int] = {}
    trips = 0
    for t in (draft or {}).get('trips') or ():
        if not isinstance(t, dict) or t.get('truck') != truck:
            continue
        trips += 1
        for c in t.get('stops') or ():
            if isinstance(c, int) and not isinstance(c, bool):
                ranks.setdefault(c, len(ranks))
    return ranks, trips


def unload_obs(day: date, actual: ac.DayActual, stops: Sequence[ac.PlanStop]) -> list[UnloadObs]:
    """Визиты без повторного заезда, у всех точек которых известно доставленное."""
    by_key = {s.key: s for s in stops}
    out = []
    for v in actual.visits:
        ss = [by_key[k] for k in v.keys]
        if v.repeat or v.minutes > UNLOAD_MAX_MIN or any(s.delivered_kg is None for s in ss):
            continue
        out.append(UnloadObs(day, len(ss), math.fsum(s.delivered_kg for s in ss) / 1000.0, v.minutes,   # type: ignore[misc]
                             tuple(sorted(s.customer_id for s in ss if s.customer_id is not None))))
    return out


def load_obs(day: date, actual: ac.DayActual) -> list[LoadObs]:
    return [LoadObs(day, t.loaded_kg / 1000.0, t.load_min) for t in actual.trips
            if t.load_min is not None and t.loaded_kg > 0]


def leg_obs(day: date, actual: ac.DayActual, norms: Any) -> list[LegObs]:
    """Чистые участки → факт, модель без поправок по часам и действующий прогноз (norms.traffic — действующий профиль
    часов; без него — модель)."""
    out = []
    for g in actual.legs:
        if not g.clean or g.minutes <= 0:
            continue
        km = norms.km(g.pa, g.pb)
        if km < LEG_MIN_KM:
            continue
        city = in_city(g.pa, norms.city_center, norms.city_radius_km) and in_city(g.pb, norms.city_center,
                                                                                    norms.city_radius_km)
        speed = _speed(norms, city)
        model = km / speed * 60.0
        if not LEG_RATIO_OUTLIER[0] <= g.minutes / model <= LEG_RATIO_OUTLIER[1]:
            continue
        local = g.depart.astimezone(ac.YEREVAN)
        current = (norms.traffic.travel(km, speed, city, local.weekday(), ac.local_minutes(g.depart))
                   if norms.traffic is not None else model)
        out.append(LegObs(day, city, local.weekday() >= 5, local.hour, g.minutes, model, current))
    return out


@dataclass(frozen=True)
class Interval:
    """Интервал между заправками «до полного бака» одной машины."""
    car_code: str
    start: datetime
    end: datetime
    liters: float
    km: float

    @property
    def l100(self) -> float:
        return self.liters / self.km * 100.0


def fuel_intervals(refuels: Sequence[Mapping[str, Any]]) -> list[Interval]:
    """Заправки → интервалы «полный бак → полный бак» по каждой машине (по моменту): литры — все заправки после
    первого полного бака до следующего включительно, км — разница их одометров. Исправленные (superseded) не считаются
    вовсе. Сомнительный одометр (флаг odometer_suspicious или здесь же пересчитанное правило по всем заправкам машины:
    меньше прежнего хорошего или прирост больше REFUEL_KM_PER_DAY за сутки) — литры заправки считаются (топливо в бак
    попало), но границей интервала она не служит. Литры не числом — интервал обрывается. Интервал короче FUEL_MIN_KM
    или с расходом вне FUEL_L100 — не считается."""
    by_car: dict[str, list[tuple[datetime, str, Mapping[str, Any], bool]]] = {}
    for r in refuels:
        if r.get('superseded'):
            continue
        try:
            at = datetime.fromisoformat(r['at_utc'])
        except (TypeError, ValueError, KeyError):
            continue
        by_car.setdefault(r['car_code'], []).append((at, r['id'], r.get('payload') or {},
                                                     'odometer_suspicious' in (r.get('flags') or ())))
    out: list[Interval] = []
    for car in sorted(by_car):
        start: tuple[datetime, float] | None = None   # последний полный бак с хорошим одометром
        good: tuple[datetime, float] | None = None    # последний хороший одометр
        liters = 0.0
        for at, _, p, flagged in sorted(by_car[car], key=lambda x: (x[0], x[1])):
            odo, lit = p.get('odometer_km'), p.get('liters')
            if isinstance(lit, bool) or not isinstance(lit, (int, float)):
                start, liters = None, 0.0
                continue
            if start is not None:
                liters += float(lit)
            ok = isinstance(odo, (int, float)) and not isinstance(odo, bool) and not flagged
            if ok and good is not None:
                days = max(1.0, (at - good[0]).total_seconds() / 86400.0)
                ok = good[1] <= odo <= good[1] + REFUEL_KM_PER_DAY * days
            if not ok:
                continue
            good = (at, float(odo))
            if p.get('full_tank', True) is not False:
                if start is not None:
                    km = float(odo) - start[1]
                    if km >= FUEL_MIN_KM and FUEL_L100[0] <= liters / km * 100.0 <= FUEL_L100[1]:
                        out.append(Interval(car, start[0], at, liters, km))
                start, liters = (at, float(odo)), 0.0
    return out


def fuel_obs(intervals: Sequence[Interval], profiles: Mapping[str, Sequence[tuple[datetime, float, float]]],
             capacity: Mapping[str, float]) -> dict[str, list[FuelObs]]:
    """Интервалы заправок + груз на борту по участкам трека (actuals.load_profile, по машине) → наблюдения расхода:
    загрузка = Σ км·(кг / тоннаж) участков интервала / км одометра (вне участков — порожняком). Участки покрывают меньше
    FUEL_TRACK_COVER км одометра — трека нет за часть интервала, интервал не используется."""
    out: dict[str, list[FuelObs]] = {}
    for iv in intervals:
        cap = capacity.get(iv.car_code)
        if not cap:
            continue
        legs = [(km, kg) for at, km, kg in profiles.get(iv.car_code, ()) if iv.start < at <= iv.end]
        covered = math.fsum(km for km, _ in legs)
        if covered < FUEL_TRACK_COVER * iv.km:
            continue
        load = min(1.0, math.fsum(km * kg / cap for km, kg in legs) / iv.km)
        out.setdefault(iv.car_code, []).append(FuelObs(iv.end.astimezone(ac.YEREVAN).date(), load, iv.l100))
    return out


def daily_l100(intervals: Sequence[Interval], car: str, day: date) -> float | None:
    """Расход машины за день по заправкам: интервал «полный бак → полный бак», в который попадает день (по Еревану)."""
    for iv in intervals:
        if iv.car_code == car and iv.start.astimezone(ac.YEREVAN).date() <= day <= iv.end.astimezone(ac.YEREVAN).date():
            return iv.l100
    return None


# --- отчёт «план — факт» (этап 5) ---

def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


def _hhmm(text: Any) -> int | None:
    """«17:32» или «04:06 (+1)» (plan_view) → минуты от полуночи дня доставки; иначе None."""
    if not isinstance(text, str) or len(text) < 5 or text[2] != ':' or not (text[:2] + text[3:5]).isdigit():
        return None
    plus = text[5:].strip()
    days = int(plus[2:-1]) if plus.startswith('(+') and plus.endswith(')') and plus[2:-1].isdigit() else 0
    return days * 1440 + int(text[:2]) * 60 + int(text[3:5])


def _plan_minutes(plan: Mapping[str, Any]) -> float | None:
    """Время работы по плану — от выезда первого рейса до возвращения последнего (как у факта); у прогноза без этих
    отметок (собран до learning-loop) — минуты дня машины с загрузкой."""
    a, b = _hhmm(plan.get('depart')), _hhmm(plan.get('return'))
    return float(b - a) if a is not None and b is not None and b >= a else plan.get('minutes')


def day_report(car: str, day: date, actual: ac.DayActual, stops: Sequence[ac.PlanStop],
               prediction: Mapping[str, Any] | None, plan_trips: int, plan_stops: int, capacity_kg: float | None,
               l100: float | None) -> dict[str, Any]:
    """План и факт машины за день и KPI: км, минуты (выезд первого рейса → возвращение последнего), рейсы, литры
    (по заправкам), доля в окне, точек в час, км и литров на точку, загрузка по весу, отклонения от порядка."""
    m = ac.visit_metrics(actual, stops)
    trips = actual.trips
    starts = [t.depart for t in trips if t.depart is not None]
    ends = [t.ret for t in trips if t.ret is not None]
    minutes = (max(ends) - min(starts)).total_seconds() / 60.0 if starts and ends and max(ends) > min(starts) else None
    liters = actual.km_gps * l100 / 100.0 if l100 is not None and actual.km_gps > 0 else None
    loads = [t.load_min for t in trips if t.load_min is not None]
    plan = prediction or {}
    return {
        'car_code': car, 'day': day.isoformat(),
        'plan': {'km': _r(plan.get('km')), 'minutes': _r(_plan_minutes(plan), 0), 'liters': _r(plan.get('liters')),
                 'loading_minutes': _r(plan.get('loading_minutes'), 0), 'trips': plan_trips or None,
                 'stops': plan_stops or None},
        'fact': {'km': _r(actual.km_gps), 'minutes': _r(minutes, 0), 'liters': _r(liters),
                 'loading_minutes': _r(math.fsum(loads), 0) if loads else None, 'trips': len(trips),
                 'stops': m.visited, 'points': actual.points, 'unplanned_stays': actual.unplanned_stays},
        'kpi': {
            'on_time_pct': _r(100.0 * m.on_time / m.with_window) if m.with_window else None,
            'with_window': m.with_window, 'late_minutes': m.late_minutes,
            'stops_per_hour': _r(m.visited / (minutes / 60.0), 2) if minutes and m.visited else None,
            'km_per_stop': _r(actual.km_gps / m.visited, 2) if m.visited else None,
            'liters_per_stop': _r(liters / m.visited, 2) if liters is not None and m.visited else None,
            'load_pct': _r(100.0 * math.fsum(t.loaded_kg for t in trips) / (len(trips) * capacity_kg))
            if trips and capacity_kg else None,
            'order_changes': m.order_changes, 'ordered': m.ordered,
        },
    }


def next_run(now: datetime) -> datetime:
    """Следующий ночной прогон (NIGHTLY_AT по Еревану) после now."""
    local = now.astimezone(ac.YEREVAN)
    run = local.replace(hour=NIGHTLY_AT[0], minute=NIGHTLY_AT[1], second=0, microsecond=0)
    return run if run > local else run + timedelta(days=1)

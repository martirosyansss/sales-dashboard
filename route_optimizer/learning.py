# -*- coding: utf-8 -*-
"""Обучение «Развоза» по факту машин (learning-loop-plan.md, этапы 4–5) — чистые функции, без Flask, БД и ERP.

Что учится (каждую ночь и по кнопке «Пересчитать»), из факта actuals.reconstruct по треку APK:
- unload — разгрузка на точке = a·точек + b·тонн доставлено (+ поправка магазина) → TruckNorms.unload_min_per_stop /
  unload_min_per_tonne (+ unload_extra по точке магазина). Из стоянки вычитается ожидание открытия окна приёма;
  стоянка дольше UNLOAD_MAX_MIN или больше UNLOAD_CAP_REL × действующей нормы — не разгрузка;
- loading — загрузка на складе = a + b·тонн рейса → TruckNorms.warehouse_load_fixed_min / warehouse_load_min_per_tonne.
  Стоянка на складе — не только загрузка (обед, бумаги, ожидание выезда под окно первой точки), поэтому: из неё
  вычитается только собственное ожидание плана — пересечение стоянки с [плановое возвращение предыдущего рейса,
  плановое начало загрузки] (planned_wait; раннее возвращение машины планом не задумано и не вычитается), стоянки
  длиннее actuals.MAX_LOAD_MIN или 2 × опоры не учитываются, свободный член — нижний квантиль LOAD_QUANTILE (не
  среднее), выученное — в пределах LOAD_BOUNDS, шаг за прогон — не больше ±LOAD_STEP от опоры. Опора — действующая
  норма, в настройках пусто — медиана стоянок обучения (с ней же сравнивается ошибка). Автообучение загрузки по
  умолчанию ВЫКЛЮЧЕНО: владелец включает его, проверив выученное на странице;
- travel — время в пути грузовиков: множитель «факт / модель» по (город|область, будни|выходные, час) → поверх
  norms.traffic (TrafficProfile); модель — дорожная модель расчёта (road_model_id), сменилась модель — профиль не
  действует. Проверяется ровно тот профиль, который применится (travel_profile → TrafficProfile.travel по участку,
  через границы часов); часы прежнего выученного профиля, которых нет в новом, переносятся в него;
- fuel — расход машины л/100 км пустой/полной по заправкам «до полного бака» (литры / км одометра, загрузка — по
  участкам трека) → FleetTruck.fuel_empty_l_per_100km / fuel_full_l_per_100km, а l100 машины (стоимость PyVRP,
  сравнения «Развоза») — расход при половинной загрузке. Момент заправки — момент исходной заправки цепочки
  исправлений (supersedes); одометр — по самой длинной согласованной цепочке заправок машины (флаг при приёме не
  учитывается: опечатка в первой заправке не портит остальные). Ограничение: соседние заправки цепочки — не дальше
  REFUEL_LOOKBACK позиций: серия из ≥ 50 сомнительных одометров подряд (например, сломанный счётчик) разрывает цепочку.

Правило принятия (одно для всех): обучение — на днях до отложенной недели (TRAIN_DAYS дней), проверка — на последних
HOLDOUT_DAYS днях (до вчера включительно; у расхода — последние FUEL_TEST интервала заправок, а форма модели
выбирается только по обучающим интервалам); новая норма принимается, только если средняя абсолютная ошибка на
проверке меньше, чем у действующей нормы, не меньше чем на MIN_GAIN (2%), и данных не меньше порогов (константы ниже).
Иначе действует прежняя (выученная раньше или ручная из настроек). Применяется норма, обученная на днях до отложенной
недели, — ровно та, что прошла проверку. Результат каждого прогона — строка learned_norms (store.save_learned).
Действующая норма вида — последняя принятая с корректными параметрами (travel — ещё и той же дорожной модели);
автообучение вида выключено (DEFAULT_AUTO — по умолчанию) — действуют ручные настройки.

Где действует: только «Развоз» (views._dispatch_ctx → apply_learned: сборка, правки, «план — факт» прошлого дня).
Модель парка менеджеров (обзор, оптимизация календаря визитов — evaluate/optimize) выученных норм не получает: она
сравнивает два календаря одними и теми же нормами (уровень норм на выбор почти не влияет), её кэши оценки ключуются
отпечатком настроек (Bundle.fingerprint), где выученных норм нет, — их ночная смена давала бы устаревшие и несравнимые
результаты; подмешать их в настройки нельзя — страница «Настройки» сохранила бы их как ручные.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from . import actuals as ac
from .geo import Point, in_city
from .measurements import _fit
from .traffic_validation import TrafficProfile

KINDS = ('unload', 'loading', 'travel', 'fuel')
KIND_TITLES = {'unload': 'Разгрузка у магазина', 'loading': 'Загрузка на складе', 'travel': 'Скорость машин по часам',
               'fuel': 'Расход топлива'}
DEFAULT_AUTO = {'unload': True, 'loading': False, 'travel': True, 'fuel': True}   # нет переключателя в базе
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
UNLOAD_MAX_MIN = 90.0                # стоянка у магазина дольше (после вычета ожидания окна) — не разгрузка
UNLOAD_CAP_REL = 3.0                 # …или дольше 3 × действующей нормы
LOAD_CAP_REL = 2.0                   # стоянка на складе дольше 2 × действующей нормы загрузки — не загрузка
LOAD_QUANTILE = 0.35                 # загрузка — нижний квантиль: в стоянке есть ожидание, которого план не знает
LOAD_STEP = 0.30                     # норма загрузки за прогон меняется не больше чем на ±30%
LOAD_BOUNDS = ((0.0, 45.0), (0.0, 20.0))   # правдоподобная загрузка: мин на рейс и мин на тонну — выученная норма вне
                                           # этих пределов прижимается к ним (и такая строка журнала не действует)
FUEL_TEST = 4                        # отложенные интервалы заправок
FUEL_MIN_INTERVALS = 16              # 12 на выбор формы (measurements._fit со своей проверкой) + 4 отложенных
FUEL_MIN_KM = 50.0                   # интервал заправок короче — не считается
FUEL_L100 = (3.0, 80.0)
FUEL_TRACK_COVER = 0.6               # участки трека покрывают не меньше 60% км одометра интервала
REFUEL_KM_PER_DAY = 1500.0           # как courier.events: прирост одометра больше — несогласован
REFUEL_WINDOW_DAYS = 400             # приём заправки: цепочка одометра — по заправкам ± 400 дней от неё…
REFUEL_WINDOW_MAX = 300              # …и не больше 300 (время приёма ограничено); офис — ± 200 дней вокруг дня
REFUEL_LOOKBACK = 50                 # в цепочке соседние заправки — не дальше 50 позиций (пропущено подряд ≤ 49 сомнительных)
NIGHTLY_AT = (3, 0)                  # ночной прогон — 03:00 Еревана


def auto_on(auto: Mapping[str, bool], kind: str) -> bool:
    """Автообучение вида: выбор владельца, нет выбора — DEFAULT_AUTO."""
    return auto.get(kind, DEFAULT_AUTO.get(kind, True))


# --- наблюдения ---

@dataclass(frozen=True)
class UnloadObs:
    day: date
    n: int                    # точек плана в этой стоянке (общая разгрузка — несколько)
    tonnes: float             # доставлено, т
    minutes: float            # стоянка без ожидания открытия окна
    customers: tuple[int, ...]


@dataclass(frozen=True)
class LoadObs:
    day: date
    tonnes: float             # вес накладных рейса, т
    minutes: float            # стоянка на складе без планового ожидания


@dataclass(frozen=True)
class LegObs:
    day: date
    city: bool
    weekend: bool
    hour: int
    minutes: float            # факт
    model: float              # модель без поправок по часам: км модели / скорость зоны
    current: float            # действующий прогноз (с действующим профилем часов)
    km: float = 0.0           # км модели и скорость зоны — для TrafficProfile.travel проверяемого профиля
    speed: float = 0.0
    weekday: int = 0
    start: float = 0.0        # выезд, минуты от полуночи (Ереван)


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


def _spans(train: Sequence[Any], test: Sequence[Any]) -> dict[str, str | None]:
    return dict(zip(('train_from', 'train_to'), _span(train)), **dict(zip(('test_from', 'test_to'), _span(test))))


def _verdict(before: float, after: float) -> tuple[bool, str]:
    if before > 0 and after <= before * (1 - MIN_GAIN):
        return True, f'принято: ошибка {before:.2f} → {after:.2f}'
    return False, f'не лучше действующей нормы хотя бы на {MIN_GAIN:.0%}: ошибка {before:.2f} → {after:.2f}'


def quantile(values: Sequence[float], q: float) -> float:
    """Квантиль с линейной интерполяцией (как numpy 'linear'); детерминирован."""
    xs = sorted(values)
    pos = q * (len(xs) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


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


CAP_MIN_KEEP = 0.5                   # отсечение «дольше k × нормы» убрало бы больше половины — не выбросы, а неверная
                                     # действующая норма: тогда без отсечения (шаг всё равно ограничен / квантиль)


def _capped(obs: Sequence[Any], today: date, current: Callable[[Any], float], rel: float
            ) -> tuple[list[Any], list[Any]]:
    """(обучение, проверка) без стоянок дольше rel × прогноз действующей нормы (норма 0 — не задана, без отсечения).
    Отсекать или нет — решается только по обучению: отсечение убрало бы больше CAP_MIN_KEEP обучающих наблюдений —
    действующая норма сама неверна, отсекать нечем (ни в обучении, ни в проверке)."""
    train, test = _split(obs, today)

    def keep(o: Any) -> bool:
        return (p := current(o)) <= 0 or o.minutes <= rel * p
    if sum(map(keep, train)) < CAP_MIN_KEEP * len(train):
        return train, test
    return [o for o in train if keep(o)], [o for o in test if keep(o)]


# --- обучение по видам ---

def fit_unload(obs: Sequence[UnloadObs], current: Callable[[UnloadObs], float], today: date) -> Outcome:
    """Разгрузка = a·точек + b·тонн (+ поправка магазина, только при ≥ STORE_MIN_OBS одиночных визитах, со стягиванием
    к 0). current — прогноз действующей нормы для наблюдения; стоянки дольше UNLOAD_CAP_REL × current не учитываются ни
    в обучении, ни в проверке (отсечение не зависит от проверяемой нормы)."""
    train, test = _capped(obs, today, current, UNLOAD_CAP_REL)
    short = _enough(train, test, UNLOAD_MIN)
    if short:
        return Outcome('unload', '', False, short, n_obs=len(train), n_test=len(test), **_spans(train, test))
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
                   n_obs=len(train), n_test=len(test), mae_before=round(before, 3), mae_after=round(after, 3),
                   **_spans(train, test))


def limit_step(cur: tuple[float, float], new: tuple[float, float], tonnes: Sequence[float],
               step: float = LOAD_STEP) -> tuple[float, float]:
    """Новая прямая a + b·т не дальше ±step от действующей при типичной загрузке (квартили тонн обучения): прямая
    сдвигается к действующей на долю λ ≤ 1 — наибольшую, при которой обе точки укладываются. Параметры остаются ≥ 0
    (выпуклая смесь неотрицательных)."""
    lam = 1.0
    for t in {quantile(tonnes, 0.25), quantile(tonnes, 0.75)}:
        c, n = cur[0] + cur[1] * t, new[0] + new[1] * t
        if c > 0 and n > c * (1 + step):
            lam = min(lam, c * step / (n - c))
        elif c > 0 and n < c * (1 - step):
            lam = min(lam, c * step / (c - n))
    return cur[0] + lam * (new[0] - cur[0]), cur[1] + lam * (new[1] - cur[1])


def _bounded(a: float, b: float) -> tuple[float, float]:
    (a_lo, a_hi), (b_lo, b_hi) = LOAD_BOUNDS
    return min(a_hi, max(a_lo, a)), min(b_hi, max(b_lo, b))


def fit_loading(obs: Sequence[LoadObs], today: date, cur: tuple[float, float] | None = None) -> Outcome:
    """Загрузка рейса на складе = a + b·тонн: наклон — устойчивый (Хьюбер), свободный член — нижний квантиль
    LOAD_QUANTILE остатков (стоянка — загрузка плюс ожидание, которого план не знает), обе величины — в пределах
    LOAD_BOUNDS. Опора — действующая норма cur (a, b); не задана (в настройках пусто) — постоянная: медиана стоянок
    обучения. Относительно опоры: стоянки дольше LOAD_CAP_REL × её прогноза не учитываются (_capped), шаг за прогон —
    limit_step (и при первом принятии), проверка — ошибка опоры против выученной нормы на отложенной неделе."""
    if cur is not None and cur[0] + cur[1] <= 0:
        cur = None
    train0, _ = _split(obs, today)
    ref = cur if cur is not None else ((median(o.minutes for o in train0), 0.0) if train0 else (0.0, 0.0))

    def baseline(o: LoadObs) -> float:
        return ref[0] + ref[1] * o.tonnes
    train, test = _capped(obs, today, baseline, LOAD_CAP_REL)
    short = _enough(train, test, LOADING_MIN)
    if short:
        return Outcome('loading', '', False, short, n_obs=len(train), n_test=len(test), **_spans(train, test))
    _, b = huber_fit([(1.0, o.tonnes, o.minutes) for o in train])
    a, b = _bounded(quantile([o.minutes - b * o.tonnes for o in train], LOAD_QUANTILE), b)
    a, b = _bounded(*limit_step(ref, (a, b), [o.tonnes for o in train]))
    a, b = round(a, 2), round(b, 2)
    before = _mae((baseline(o), o.minutes) for o in test)
    after = _mae((a + b * o.tonnes, o.minutes) for o in test)
    ok, why = _verdict(before, after)
    if cur is None:
        why += f' (опора — медиана стоянок {ref[0]:.1f} мин: в настройках загрузки нет)'
    return Outcome('loading', '', ok, why, {'fixed_min': a, 'per_tonne_min': b}, n_obs=len(train), n_test=len(test),
                   mae_before=round(before, 3), mae_after=round(after, 3), **_spans(train, test))


def _bucket(o: LegObs) -> tuple[bool, int, int]:
    return o.city, int(o.weekend), o.hour


def _time_per_km(ref: Mapping[str, Any], city: bool) -> float:
    """Минут модели на км по прямой (с извилистостью без карты дорог) при скоростях ref."""
    v = float(ref.get('speed_city_kmh' if city else 'speed_region_kmh') or 1.0)
    return float(ref.get('detour') or 1.0) / v


def fit_travel(obs: Sequence[LegObs], today: date, model_id: str, ref: Mapping[str, Any], base_norms: Any,
               prev: Mapping[str, Any] | None = None) -> Outcome:
    """Множитель времени в пути «факт / модель» по ячейкам (город|область, будни|выходные, час) — ячейка при ≥ 3 днях и
    ≥ 45 модельных минутах обучения. ref — скорости (и извилистость без карты дорог), с которыми считалась модель;
    base_norms — нормы без выученного профиля (как их увидит apply_learned); prev — действующий выученный профиль той
    же дорожной модели: его часы, которых нет в новом, переносятся (пересчитанные к ref). Проверка — участки отложенной
    недели, выехавшие в часы нового профиля: действующий прогноз против travel_profile(base_norms, новый профиль).travel
    — ровно то, что применится (через границы часов)."""
    train, test = _split(obs, today)
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
                       model_id=model_id, n_obs=len(train), n_test=len(held), **_spans(train, test))
    merged = dict(ratio)
    for c, w, h, r in (prev or {}).get('factors') or ():
        key = (bool(c), int(w), int(h))
        if key not in merged:
            pref = prev.get('ref') or {}   # type: ignore[union-attr]
            merged[key] = round(max(lo, min(hi, float(r) * _time_per_km(pref, bool(c)) / _time_per_km(ref, bool(c)))), 3)
    params = {'factors': [[int(c), w, h, r] for (c, w, h), r in sorted(merged.items())], 'ref': dict(ref)}
    profile = travel_profile(base_norms, params)
    before = _mae((o.current, o.minutes) for o in held)
    after = _mae((profile.travel(o.km, o.speed, o.city, o.weekday, o.start), o.minutes) for o in held)
    ok, why = _verdict(before, after)
    return Outcome('travel', '', ok, why, params, model_id, len(train), len(held), mae_before=round(before, 3),
                   mae_after=round(after, 3), **_spans(train, test))


def fit_fuel(obs: Sequence[FuelObs], current: Callable[[float], float], car: str) -> Outcome:
    """Расход л/100 км = пустой + (полный − пустой) × загрузка. Форма выбирается только по обучающим интервалам
    (все, кроме последних FUEL_TEST): measurements._fit (≥ 12 интервалов, разброс загрузки ≥ 20%, своя проверка на
    последних 4 обучающих) — зависимость от загрузки; не подтвердилась — один расход (медиана обучения). Последние
    FUEL_TEST интервалов — только проверка: принимается при ошибке на ≥ 2% меньше, чем у действующей нормы машины
    (current(загрузка) → л/100 км)."""
    rows = sorted(({'day': o.day.isoformat(), 'load': o.load, 'l100': o.l100} for o in obs),
                  key=lambda r: (r['day'], r['load'], r['l100']))
    if len(rows) < FUEL_MIN_INTERVALS or len({r['day'] for r in rows}) < FUEL_MIN_INTERVALS:
        return Outcome('fuel', car, False, f'мало данных: интервалов между полными баками {len(rows)} из '
                       f'{FUEL_MIN_INTERVALS}', n_obs=len(rows))
    train, test = rows[:-FUEL_TEST], rows[-FUEL_TEST:]
    fit = _fit(train, 'load', 'l100')
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


def _num(x: Any, lo: float, hi: float) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and lo <= x <= hi


def valid_params(kind: str, p: Any) -> bool:
    """Параметры строки журнала годятся для расчёта (битая строка — не действует, «Развоз» — на прежней норме)."""
    if not isinstance(p, Mapping):
        return False
    if kind == 'unload':
        offsets = p.get('store_offsets', {})
        return (_num(p.get('per_stop_min'), 0, 120) and _num(p.get('per_tonne_min'), 0, 120)
                and isinstance(offsets, Mapping)
                and all(isinstance(c, str) and c.isdigit() and _num(v, -120, STORE_OFFSET_MAX) for c, v in offsets.items()))
    if kind == 'loading':
        return _num(p.get('fixed_min'), *LOAD_BOUNDS[0]) and _num(p.get('per_tonne_min'), *LOAD_BOUNDS[1])
    if kind == 'travel':
        ref, factors = p.get('ref'), p.get('factors')
        return (isinstance(ref, Mapping) and _num(ref.get('speed_city_kmh'), 1, 200)
                and _num(ref.get('speed_region_kmh'), 1, 200)
                and (ref.get('detour') is None or _num(ref.get('detour'), 1, 5))
                and isinstance(factors, list) and bool(factors)
                and all(isinstance(f, list) and len(f) == 4 and f[0] in (0, 1) and f[1] in (0, 1)
                        and isinstance(f[2], int) and not isinstance(f[2], bool) and 0 <= f[2] <= 23
                        and _num(f[3], *TRAVEL_RATIO) for f in factors))
    if kind == 'fuel':
        return (_num(p.get('empty_l100'), 1, 80) and _num(p.get('full_l100'), 1, 80)
                and p['empty_l100'] <= p['full_l100'])
    return False


def in_effect(rows: Sequence[Mapping[str, Any]], auto: Mapping[str, bool], model_id: str | None) -> InEffect:
    """Строки learned_norms (по возрастанию run_day) → действующие нормы: по (вид, машина) — последняя принятая с
    корректными параметрами (valid_params); travel — последняя принятая той же дорожной модели (model_id; None —
    дорожная модель не поддерживается); вид с выключенным автообучением (auto_on) — не действует."""
    last: dict[tuple[str, str], Mapping[str, Any]] = {}
    for r in rows:
        if not r['accepted'] or not auto_on(auto, r['kind']) or not valid_params(r['kind'], r['params']):
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


def travel_profile(norms: Any, travel: Mapping[str, Any]) -> TrafficProfile:
    """Профиль часов расчёта с выученным: множители грузовиков поверх действующего профиля norms (часы без данных
    грузовиков — как были)."""
    factors = dict(norms.traffic.factors) if norms.traffic is not None else {}
    factors.update(speed_factors(travel, norms))
    report = dict(norms.traffic.report) if norms.traffic is not None else {}
    report.update({'trucks': 'learned', 'truck_hours': len(travel.get('factors') or ())})
    return TrafficProfile(factors, report)


def apply_learned(norms: Any, tn: Any, trucks: Mapping[str, Any], eff: InEffect,
                  customer_points: Mapping[int, Point]) -> tuple[Any, Any, dict[str, Any]]:
    """Действующие выученные нормы → (norms, нормы машин, машины) расчёта «Развоза». Без выученных — те же объекты.
    customer_points — клиент → точка дня (поправка разгрузки магазина — по точке, как её видит fleet). Расход машины:
    пустой/полный — в расчёт по остаточному грузу (running_costs.route_cost), а единый l100 машины (стоимость км в
    PyVRP, выбор машины и проверка «км × л/100» при выравнивании) — расход при половинной загрузке: рейс выезжает
    загруженным и возвращается пустым, средний груз на борту — около половины загрузки выезда."""
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
        norms = replace(norms, traffic=travel_profile(norms, eff.travel))
    for code, p in (eff.fuel or {}).items():
        if code in trucks:
            empty, full = float(p['empty_l100']), float(p['full_l100'])
            trucks[code] = replace(trucks[code], fuel_empty_l_per_100km=empty, fuel_full_l_per_100km=full,
                                   l100=(empty + full) / 2)
    return norms, tn, trucks


# --- наблюдения из факта ---

class FleetFacts(Protocol):
    """Факт машин из «Առաքիչ» (courier.facts.FactsSource); подключает app_v2 (route_optimizer.attach_fleet_facts)."""

    def car_days(self, since: str, until: str) -> list[tuple[str, str]]:
        """(машина, день) с треком."""
        ...

    def day(self, car_code: str, day: str) -> dict[str, Any]:
        """{'track': [(at_ms, lat, lon, acc, spd)], 'stops': [{stop_id, customer_id, lat, lon, weight_kg, seq,
        delivered_share, delivered_at}]}."""
        ...

    def version(self, car_code: str, day: str) -> tuple:
        """Отпечаток данных машины за день (трек, доставки, снимок /day): не изменился — факт тот же (кэш)."""
        ...

    def refuels(self) -> list[dict[str, Any]]:
        """Заправки: id, car_code, date, at, at_utc, eff_at_utc, eff_date, payload, flags, superseded."""
        ...


def track_fixes(track: Iterable[Sequence[Any]]) -> list[ac.TrackFix]:
    """(at_ms, lat, lon, acc, spd?) → actuals.TrackFix (момент — Ереван; скорость терминала — если прислана)."""
    return [ac.TrackFix(datetime.fromtimestamp(p[0] / 1000.0, ac.YEREVAN), float(p[1]), float(p[2]), float(p[3]),
                        float(p[4]) if len(p) > 4 and isinstance(p[4], (int, float)) else None)
            for p in track]


def _moment(raw: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    return dt if dt is not None and dt.utcoffset() is not None else None


def plan_stops(stops: Sequence[Mapping[str, Any]], ranks: Mapping[int, int],
               windows_by_customer: Mapping[int, tuple[float, float]]) -> list[ac.PlanStop]:
    """Точки /day машины → PlanStop: место в плане — по плану «Развоза» (ranks: клиент → место), без плана — seq /day;
    окно приёма — из настроек магазина; доставлено — доля по отметке водителя × вес накладной, момент отметки."""
    out = []
    for s in stops:
        lat, lon, cid = s.get('lat'), s.get('lon'), s.get('customer_id')
        point = (float(lat), float(lon)) if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else None
        kg = float(s.get('weight_kg') or 0.0)
        share = s.get('delivered_share')
        rank = ranks.get(cid) if ranks else s.get('seq')
        out.append(ac.PlanStop(str(s.get('stop_id')), cid, point, kg, kg * share if share is not None else None,
                               windows_by_customer.get(cid) if cid is not None else None,
                               rank if isinstance(rank, int) else None, _moment(s.get('delivered_at'))))
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


@dataclass(frozen=True)
class PlanTrip:
    """Плановый рейс машины (прогноз сборки): начало загрузки, плановое возвращение предыдущего рейса (None — первый
    рейс дня), плановый выезд, клиенты."""
    loading_start: datetime | None
    prev_return: datetime | None
    depart: datetime | None
    customers: frozenset[int]

    @property
    def wait(self) -> tuple[datetime, datetime] | None:
        """Собственное ожидание плана на складе: [возвращение предыдущего рейса, начало загрузки], если план нарочно
        держит машину (выезд позже под окно первой точки); иначе None."""
        if self.prev_return is None or self.loading_start is None or self.loading_start <= self.prev_return:
            return None
        return self.prev_return, self.loading_start


def plan_trips(prediction: Mapping[str, Any] | None, day: date) -> list[PlanTrip]:
    """Прогноз сборки машины (views._capture_prediction: trips — loading_start, depart, return «HH:MM», stops —
    [[клиент, ETA]]) → плановые рейсы по порядку. Прогноз старый (без рейсов) — пусто."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=ac.YEREVAN)

    def at(text: Any) -> datetime | None:
        m = _hhmm(text)
        return midnight + timedelta(minutes=m) if m is not None else None
    trips = [t for t in (prediction or {}).get('trips') or () if isinstance(t, Mapping)]
    return [PlanTrip(at(t.get('loading_start')), at(trips[j - 1].get('return')) if j else None, at(t.get('depart')),
                     frozenset(c[0] for c in t.get('stops') or () if isinstance(c, list) and c and isinstance(c[0], int)))
            for j, t in enumerate(trips)]


def _planned_trip(plan: Sequence[PlanTrip], n: int, cids: set[int], depart: datetime) -> PlanTrip | None:
    """Плановый рейс фактического рейса n: с наибольшим числом общих клиентов; несколько таких (тяжёлый заказ на
    несколько поездок) — с плановым выездом ближе всего к фактическому; общих нет — n-й по порядку."""
    best = max((len(t.customers & cids) for t in plan), default=0)
    if best > 0:
        same = [j for j, t in enumerate(plan) if len(t.customers & cids) == best]
        return plan[min(same, key=lambda j: (abs((plan[j].depart - depart).total_seconds())
                                             if plan[j].depart is not None else math.inf, abs(j - n), j))]
    return plan[n] if n < len(plan) else None


def planned_wait(trip: PlanTrip | None, arrive: datetime, depart: datetime) -> float:
    """Минуты собственного ожидания плана, пришедшиеся на фактическую стоянку [arrive, depart]: пересечение стоянки с
    плановым ожиданием [плановое возвращение предыдущего рейса, плановое начало загрузки]. Машина вернулась раньше
    плана — её лишнее время не вычитается (план его не задумывал); позже — вычитается только остаток планового
    ожидания; уехала раньше планового начала загрузки — ждать не стала, вычитать нечего. Не больше стоянки."""
    w = trip.wait if trip is not None else None
    if w is None or depart <= w[1]:
        return 0.0
    return max(0.0, (min(depart, w[1]) - max(arrive, w[0])).total_seconds() / 60.0)


def unload_obs(day: date, actual: ac.DayActual, stops: Sequence[ac.PlanStop]) -> list[UnloadObs]:
    """Обслуживающие визиты (не повторные), у всех точек которых известно доставленное. Из стоянки вычитается ожидание
    открытия окна приёма (начало окна позже прибытия — машина ждёт: это не разгрузка); стоянка дольше UNLOAD_MAX_MIN —
    не учитывается."""
    by_key = {s.key: s for s in stops}
    out = []
    for v in actual.visits:
        ss = [by_key[k] for k in v.keys]
        if v.repeat or any(s.delivered_kg is None for s in ss):
            continue
        opens = max((s.window[0] for s in ss if s.window is not None and math.isfinite(s.window[0])), default=None)
        wait = max(0.0, opens - ac.day_minutes(day, v.arrive)) if opens is not None else 0.0
        minutes = v.minutes - wait
        if not 0.5 <= minutes <= UNLOAD_MAX_MIN:
            continue
        out.append(UnloadObs(day, len(ss), math.fsum(s.delivered_kg for s in ss) / 1000.0, minutes,   # type: ignore[misc]
                             tuple(sorted(s.customer_id for s in ss if s.customer_id is not None))))
    return out


def load_obs(day: date, actual: ac.DayActual, stops: Sequence[ac.PlanStop] = (),
             plan: Sequence[PlanTrip] = ()) -> list[LoadObs]:
    """Стоянка на складе перед рейсом (видно прибытие, не дольше actuals.MAX_LOAD_MIN) без собственного ожидания плана
    (planned_wait по плановому рейсу _planned_trip)."""
    by_key = {s.key: s for s in stops}
    served = dict(actual.served)
    out = []
    for n, t in enumerate(actual.trips):
        arrive = t.arrive_depot
        if arrive is None or t.depart is None or t.loaded_kg <= 0:
            continue
        cids = {by_key[k].customer_id for k, i in served.items() if i in t.visits and k in by_key
                and by_key[k].customer_id is not None}
        minutes = t.load_min - planned_wait(_planned_trip(plan, n, cids, t.depart), arrive, t.depart)   # type: ignore[operator]
        if minutes >= 1.0:
            out.append(LoadObs(day, t.loaded_kg / 1000.0, minutes))
    return out


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
        start = ac.local_minutes(g.depart)
        current = (norms.traffic.travel(km, speed, city, local.weekday(), start)
                   if norms.traffic is not None else model)
        out.append(LegObs(day, city, local.weekday() >= 5, local.hour, g.minutes, model, current, km, speed,
                          local.weekday(), start))
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


def odometer_plausible(items: Sequence[tuple[datetime, Any]]) -> list[bool]:
    """Одометры заправок машины (по моменту) → согласован ли каждый: самая длинная цепочка заправок, где одометр не
    убывает и прирастает не больше REFUEL_KM_PER_DAY × суток (не меньше 1) между соседними в цепочке; согласован —
    одометр, входящий во ВСЕ самые длинные цепочки (неоднозначная пара — оба сомнительны, пока следующие заправки не
    разрешат). Опечатка вверх или вниз — вне цепочки; опечатка в первой заправке не портит остальные. Не число —
    сомнителен. Соседние в цепочке — не дальше REFUEL_LOOKBACK позиций (O(n·k)): ≥ REFUEL_LOOKBACK сомнительных
    одометров подряд цепочка не перешагнёт — хорошие заправки по разные стороны такой серии считаются раздельно (меньшая
    часть — сомнительной). Вызывающие дают заправки за окно по времени (effective_refuels); приём заправки — окно ±
    REFUEL_WINDOW_DAYS и не больше REFUEL_WINDOW_MAX (courier.events)."""
    ok = [isinstance(o, (int, float)) and not isinstance(o, bool) and math.isfinite(o) for _, o in items]
    idx = [i for i, good in enumerate(ok) if good]

    def fits(i: int, j: int) -> bool:
        (ti, oi), (tj, oj) = items[i], items[j]
        days = max(1.0, (tj - ti).total_seconds() / 86400.0)
        return oi <= oj <= oi + REFUEL_KM_PER_DAY * days

    k = REFUEL_LOOKBACK                        # соседние в цепочке — не дальше k позиций: O(n·k), а не O(n²)
    fwd = {i: (1, 1) for i in idx}             # (длина лучшей цепочки, оканчивающейся на i; их число)
    for n, j in enumerate(idx):
        for i in idx[max(0, n - k):n]:
            if fits(i, j):
                ln, cnt = fwd[i][0] + 1, fwd[i][1]
                if ln > fwd[j][0]:
                    fwd[j] = (ln, cnt)
                elif ln == fwd[j][0] and ln > 1:
                    fwd[j] = (ln, fwd[j][1] + cnt)
    bwd = {i: (1, 1) for i in idx}             # то же, начинающейся на i
    for n in range(len(idx) - 1, -1, -1):
        i = idx[n]
        for j in idx[n + 1:n + 1 + k]:
            if fits(i, j):
                ln, cnt = bwd[j][0] + 1, bwd[j][1]
                if ln > bwd[i][0]:
                    bwd[i] = (ln, cnt)
                elif ln == bwd[i][0] and ln > 1:
                    bwd[i] = (ln, bwd[i][1] + cnt)
    best = max((fwd[i][0] for i in idx), default=0)
    total = sum(fwd[i][1] for i in idx if fwd[i][0] == best)
    return [good and fwd[i][0] + bwd[i][0] - 1 == best and fwd[i][1] * bwd[i][1] == total
            for i, good in enumerate(ok)]


def effective_refuels(refuels: Sequence[Mapping[str, Any]], since: datetime | None = None,
                      until: datetime | None = None) -> dict[str, list[tuple[datetime, str, Mapping[str, Any]]]]:
    """Действующие (не вытесненные) заправки по машине: [(момент, id, payload)] по моменту. Момент — исходной заправки
    цепочки исправлений (eff_at_utc от courier.store.Store.refuels), без него — свой. since/until — окно по моменту,
    которое задаёт вызывающий (отчёт и обучение — свой период с запасом, офис — вокруг показываемого дня); без числового
    предела: время проверки одометра ограничивает REFUEL_LOOKBACK (O(n·k))."""
    by_car: dict[str, list[tuple[datetime, str, Mapping[str, Any]]]] = {}
    for r in refuels:
        if r.get('superseded'):
            continue
        try:
            at = datetime.fromisoformat(r.get('eff_at_utc') or r['at_utc'])
        except (TypeError, ValueError, KeyError):
            continue
        if (since is not None and at < since) or (until is not None and at > until):
            continue
        by_car.setdefault(r['car_code'], []).append((at, r['id'], r.get('payload') or {}))
    return {car: sorted(items, key=lambda x: (x[0], x[1])) for car, items in by_car.items()}


def fuel_intervals(refuels: Sequence[Mapping[str, Any]]) -> list[Interval]:
    """Заправки → интервалы «полный бак → полный бак» по каждой машине: литры — все заправки после первого полного бака
    до следующего включительно, км — разница их одометров. Исправленные (superseded) не считаются вовсе; момент
    исправления — момент исходной заправки (effective_refuels). Одометр несогласованный (odometer_plausible; флаг при
    приёме не учитывается) — литры заправки считаются (топливо в бак попало), но границей интервала она не служит.
    Литры не числом — интервал обрывается. Интервал короче FUEL_MIN_KM или с расходом вне FUEL_L100 — не считается."""
    out: list[Interval] = []
    for car, items in sorted(effective_refuels(refuels).items()):
        plausible = odometer_plausible([(at, p.get('odometer_km')) for at, _, p in items])
        start: tuple[datetime, float] | None = None   # последний полный бак с согласованным одометром
        liters = 0.0
        for (at, _, p), good in zip(items, plausible):
            lit = p.get('liters')
            if isinstance(lit, bool) or not isinstance(lit, (int, float)):
                start, liters = None, 0.0
                continue
            if start is not None:
                liters += float(lit)
            if not good or p.get('full_tank', True) is False:
                continue
            odo = float(p['odometer_km'])
            if start is not None:
                km = odo - start[1]
                if km >= FUEL_MIN_KM and FUEL_L100[0] <= liters / km * 100.0 <= FUEL_L100[1]:
                    out.append(Interval(car, start[0], at, liters, km))
            start, liters = (at, odo), 0.0
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
    """План и факт машины за день и KPI: км (движения, без дрожания на стоянках), минуты (выезд первого рейса →
    возвращение последнего), рейсы, литры (по заправкам), доля в окне, точек в час, км и литров на точку, загрузка по
    весу, отклонения от порядка."""
    m = ac.visit_metrics(actual, stops, day)
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
            'with_window': m.with_window, 'late_minutes': m.late_minutes, 'early': m.early,
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


def missed_run(now: datetime, last_run: str | None) -> bool:
    """Ночной прогон пропущен (сервер был выключен или перезапускался в 03:00): последнего прогона нет или он раньше
    дня последнего наступившего NIGHTLY_AT по Еревану."""
    local = now.astimezone(ac.YEREVAN)
    slot = local.date() if (local.hour, local.minute) >= NIGHTLY_AT else local.date() - timedelta(days=1)
    return last_run is None or last_run < slot.isoformat()

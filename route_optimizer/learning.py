# -*- coding: utf-8 -*-
"""Обучение «Развоза» по факту машин (learning-loop-plan.md, этапы 4–5) — чистые функции, без Flask, БД и ERP.

Что учится (каждую ночь и по кнопке «Пересчитать»), из факта actuals.reconstruct по треку APK:
- unload — разгрузка на точке = a·точек + b·тонн доставлено (+ поправка магазина) → TruckNorms.unload_min_per_stop /
  unload_min_per_tonne (+ unload_extra по точке магазина). Стоянка — от остановки у магазина до начала движения
  (actuals, №60), но не дольше TAP_TAIL после отметки доставки водителя (№65; unload_obs); доставлено — по весу
  товаров (courier.facts.delivered_share, №65); из стоянки вычитается ожидание открытия окна приёма; стоянка
  дольше UNLOAD_CAP_REL × действующей нормы (и
  её же без своего времени магазинов) — не разгрузка. Своё время магазина (ответ владельца №50: постоянная часть его
  разгрузки — парковка, приёмка, документы — вместо a; время на груз b·т — как у всех): по факту — a + медиана остатков
  одиночных визитов магазина (store_stats строки: визитов и минуты). Правило №60: пока у магазина меньше STORE_MIN_OBS
  (2) визитов по GPS, действует введённое логистом (store.Bundle.unload_min, абсолютные минуты; в «Развозе» — всегда, и
  без выученных строк, и с выключенным автообучением), нет введённого — a; со STORE_MIN_OBS — время по факту, без смеси
  с введённым и нормой; ровно два визита, которые сильно расходятся (store_waits: больше STORE_SPLIT_MIN и больше
  STORE_SPLIT_REL от большего), — ждём третий (ответ владельца «ждать 3-ю»), а пока — как без факта. Считается при
  применении (store_times): изменённое или убранное введённое действует сразу. У строк до №50 (без store_stats) и у
  битой записи — введённое, иначе поправка строки (store_offsets — время по факту на день прогона). Проверка и
  «действующая норма» для сравнения — та же store_times, ровно то, что применится. Магазины в одной точке — среднее их
  поправок на каждую стоянку точки (unload_extra). Соперник №60 (ответ владельца №66) — сглаживание к группе
  (shrink_times: t = (n·факт + k·опора) / (n + k), опора — введённое, иначе медиана группы размера / сети, иначе a):
  действует, только если проверка выбрала его (_fit_store_rule, гистерезис как у truck_time; store_rule строки);
- loading — загрузка на складе = a + b·тонн рейса → TruckNorms.warehouse_load_fixed_min / warehouse_load_min_per_tonne.
  Стоянка на складе — не только загрузка (обед, бумаги, ожидание выезда под окно первой точки), поэтому: из неё
  вычитается только собственное ожидание плана — пересечение стоянки с [плановое возвращение предыдущего рейса,
  плановое начало загрузки] (planned_wait; раннее возвращение машины планом не задумано и не вычитается), стоянки
  длиннее actuals.MAX_LOAD_MIN или 2 × опоры и короче LOAD_MIN_STAY (проезд через склад) не учитываются, свободный
  член — нижний квантиль LOAD_QUANTILE (не среднее), выученное — в пределах LOAD_BOUNDS (не меньше LOAD_MIN_STAY на
  рейс), шаг за прогон — не больше ±LOAD_STEP от опоры. Опора — действующая норма, в настройках пусто — медиана стоянок
  обучения (с ней же сравнивается ошибка). Автообучение загрузки по умолчанию ВЫКЛЮЧЕНО: владелец включает его,
  проверив выученное на странице. Ограничение: плановое ожидание бывает только при окнах приёма (сейчас окон нет);
  если водитель загрузился во время планового ожидания и уехал без простоя, вычтется и настоящая загрузка — до
  длины планового ожидания (наблюдение занижено; нижний предел LOAD_MIN_STAY и шаг ±30% это сдерживают);
- travel — время в пути грузовиков: множитель «факт / модель» по (город|область, будни|выходные, час) → поверх
  norms.traffic (TrafficProfile); модель — дорожная модель расчёта (road_model_id), сменилась модель — профиль не
  действует. Проверяется ровно тот профиль, который применится (travel_profile → TrafficProfile.travel по участку,
  через границы часов); часы прежнего выученного профиля, которых нет в новом, переносятся в него. Минуты модели —
  как в расчёте «Развоза» (Norms.leg_speed): км / скорость зоны или, когда грузовики считаются по Valhalla, его время
  (тогда множитель от скоростей зон в настройках не зависит — _own_minutes; scope строки — travel_scope: '' или
  'valhalla', поправки обеих моделей хранятся рядом и не путаются);
- truck_time — какой моделью считать время в пути грузовиков: прежней (км / скорость зоны) или Valhalla-грузовиком
  (× поправка зоны). Обе — на одних и тех же чистых участках факта (truck_time_obs через
  valhalla_engine.truck_leg_minutes; участок без минут Valhalla не идёт ни в одну), у каждой — своя поправка по часам,
  выученная на днях обучения ровно по правилу travel (fit_travel), обе проверяются на одних и тех же участках
  отложенной недели той поправкой, которая применится. Выбранная модель (сначала — прежняя) меняется на
  другую, только если та точнее хотя бы на MIN_GAIN и общих участков не меньше TRUCK_TIME_MIN — в обе стороны
  (гистерезис: от шума модель не переключается туда-сюда). Применяет выбор views: env ROUTES_TRUCK_TIME, если задана,
  > выбор обучения (автообучение вида включено) > прежняя модель. Пока выбранная модель — не та, которой считает
  «Развоз» (только что переключились, env, галочка), её поправка по часам (travel её scope) пишется каждую
  ночь — перейдёт на неё «Развоз», поправка уже есть. Автообучение travel выключено — модели сравниваются «как есть»;
- fuel — расход машины л/100 км пустой/полной по заправкам «до полного бака» (литры / км одометра, загрузка — по
  участкам трека) → FleetTruck.fuel_empty_l_per_100km / fuel_full_l_per_100km, а l100 машины (стоимость PyVRP,
  сравнения «Развоза») — расход при половинной загрузке. Момент заправки — момент исходной заправки цепочки
  исправлений (supersedes); одометр — по самой длинной согласованной цепочке заправок машины (флаг при приёме не
  учитывается: опечатка в первой заправке не портит остальные). Ограничение: соседние заправки цепочки — не дальше
  REFUEL_LOOKBACK позиций: серия из ≥ 50 сомнительных одометров подряд (например, сломанный счётчик) разрывает цепочку;
- lunch — обед водителя в пути (№61) → TruckNorms.lunch_minutes «Развоза» (окно начала обеда — из настроек). Обед,
  который водитель взял на самом деле (lunch_obs), — по машино-дню, где план поставил обед (прогноз сборки помнит, где:
  после разгрузки какого магазина, на складе до загрузки какого рейса или в дороге — views._capture_prediction): больший
  из двух — самая длинная стоянка не по плану ('other': не склад и не точка плана), начавшаяся в окне начала обеда ±
  LUNCH_SLACK (свернул поесть в сторону), и излишек стоянки там, где обед по плану: у магазина — стоянка сверх его
  разгрузки по действующей норме (как в расчёте, со своим временем магазина) и ожидания окна приёма до начала окна обеда
  (ожидание после него — уже обед, как в плане), на складе — стоянка перед рейсом сверх плановой загрузки и планового
  простоя без обеда. Водитель поел там, где план, — так и видно (а не 0: иначе обед за несколько ночей сошёл бы на нет);
  поел в сторону — видно стоянку. Дни без обеда в плане (прогноз до №61, обед выключен, план машины кончился до окна) и
  дни, когда машина не работала дольше конца окна, не учитываются. Выученное — медиана обучения в пределах LUNCH_BOUNDS,
  шаг за прогон — не больше ±max(LUNCH_STEP × действующего, LUNCH_STEP_MIN) (до 0 дойти можно). Ошибка — минут на
  машино-день: действующий обед против выученного. Обед в настройках 0 — выключен: не учится и не включается выученным.
  Автообучение обеда по умолчанию ВЫКЛЮЧЕНО (как у загрузки): программа учит и показывает, включает владелец.
  Обед не попадает в другие наблюдения: стоянка не по плану — ни в разгрузку, ни в чистые участки (так устроен факт);
  визит магазина, после которого по плану обед, не идёт в обучение разгрузки и своего времени магазинов, а стоянка на
  складе перед рейсом с обедом по плану — в обучение загрузки (unload_obs / load_obs: skip). Исключение, а не вычитание
  обеда: вычитать нечего надёжно (наблюдаемый обед — сам излишек над нормой разгрузки, вычет вернул бы норму), а какой
  визит после обеда — решает время по плану, не длина стоянки: выборка визитов не смещена, их только меньше.

Правило принятия (одно для всех): обучение — на днях до отложенной недели (TRAIN_DAYS дней), проверка — на последних
HOLDOUT_DAYS днях (до вчера включительно; у расхода — последние FUEL_TEST интервала заправок, а форма модели
выбирается только по обучающим интервалам); новая норма принимается, только если средняя абсолютная ошибка на
проверке меньше, чем у действующей нормы, не меньше чем на MIN_GAIN (2%), выигрыш устойчив — новая норма точнее
действующей не меньше чем в BOOT_SHARE (90%) повторных выборок дней проверки (у расхода — интервалов; парный бутстреп
bootstrap_share: на двух днях выигрыш в 2–3% бывает и шумом), и данных не меньше порогов (константы ниже); доля —
в причине строки и в её столбце confidence. У truck_time — так же поверх гистерезиса. Иначе действует прежняя
(выученная раньше или ручная из настроек). Применяется норма, обученная на днях до отложенной недели, — ровно та, что
прошла проверку. Результат каждого прогона — строка learned_norms (store.save_learned).
Действующая норма вида — последняя принятая с корректными параметрами (travel — ещё и той же дорожной модели и scope;
truck_time — truck_time_learned); автообучение вида выключено (DEFAULT_AUTO — по умолчанию) — действуют ручные
настройки (у truck_time — прежняя модель или ROUTES_TRUCK_TIME).

Где действует: только «Развоз» (views._dispatch_ctx: модель времени грузовиков — до среза дорог, затем apply_learned:
сборка, правки, «план — факт» прошлого дня, прогноз для отчёта «план — факт», модель участков обучения).
Модель парка менеджеров (обзор, оптимизация календаря визитов — evaluate/optimize) выученных норм не получает: она
сравнивает два календаря одними и теми же нормами (уровень норм на выбор почти не влияет), её кэши оценки ключуются
отпечатком настроек (Bundle.fingerprint), где выученных норм нет, — их ночная смена давала бы устаревшие и несравнимые
результаты; подмешать их в настройки нельзя — страница «Настройки» сохранила бы их как ручные.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Callable, Collection, Iterable, Mapping, Protocol, Sequence

from . import actuals as ac
from . import demand as dm
from . import valhalla_engine
from .frequency import fmt_decimal
from .garage import KM_PER_DAY_MAX
from .geo import Point, in_city
from .measurements import _fit
from .traffic_validation import TrafficProfile

KINDS = ('unload', 'loading', 'travel', 'truck_time', 'lunch', 'fuel')
# Заголовки и причины журнала — по-армянски (решение владельца №58): их показывает страница «Обучение» как есть
KIND_TITLES = {'unload': 'Բեռնաթափում խանութում', 'loading': 'Բեռնում պահեստում', 'travel': 'Մեքենաների արագությունն ըստ ժամերի',
               'truck_time': 'Բեռնատարների ճանապարհի ժամանակը՝ մոդել', 'lunch': 'Ճաշ ճանապարհին',
               'fuel': 'Վառելիքի ծախս'}
DEFAULT_AUTO = {'unload': True, 'loading': False, 'travel': True, 'truck_time': True, 'lunch': False,
                'fuel': True}   # нет переключателя в базе
HOLDOUT_DAYS = 7
TRAIN_DAYS = 120
MIN_GAIN = 0.02
BOOT_RESAMPLES = 2000                # устойчивость выигрыша: повторных выборок групп проверки (bootstrap_share)…
BOOT_SHARE = 0.90                    # …и в скольких из них новая норма должна быть точнее
BOOT_SEED = 61                       # своё зерно: итог прогона воспроизводим
# пороги данных: (наблюдений в обучении, дней в обучении, наблюдений в проверке, дней в проверке)
UNLOAD_MIN = (30, 5, 10, 2)
LOADING_MIN = (15, 5, 5, 2)
TRAVEL_MIN_TEST = (20, 3)            # участков и дней проверки в ячейках профиля
TRAVEL_BUCKET = (3, 45.0)            # ячейка профиля: дней и модельных минут в обучении
TRAVEL_RATIO = (0.5, 3.0)            # множитель времени в пути — в этих пределах
LEG_RATIO_OUTLIER = (0.2, 5.0)       # участок «факт / модель» вне — не езда (заезд без стоянки 2 мин и т. п.)
LEG_MIN_KM = 0.2
# выбор модели времени грузовиков: общих участков и их дней в обучении, в проверке (как _enough)
TRUCK_TIME_MIN = (200, 7, 60, 3)
TRUCK_TIME_SOURCES = (valhalla_engine.TRUCK_TIME_MODEL, valhalla_engine.TRUCK_TIME_VALHALLA)   # model, valhalla
TRUCK_TIME_TITLES = {'model': 'նախկին մոդել', 'valhalla': 'Valhalla'}
# в причинах выбора модели — с артиклем; подлежащее стоит перед гласной («ավելի», «առնվազն») — артикль «-ն»
TRUCK_TIME_DEF = {'model': 'նախկին մոդելը', 'valhalla': 'Valhalla-ն'}
TRUCK_TIME_SUBJ = {'model': 'նախկին մոդելն', 'valhalla': 'Valhalla-ն'}
STORE_MIN_OBS = 2                    # своё время магазина по факту — со 2-го одиночного визита (один — не в счёт), №60;
STORE_SPLIT_MIN = 5.0                # но ровно 2 визита, чьё время по факту разнится больше чем на 5 мин…
STORE_SPLIT_REL = 0.30               # …и больше чем на 30% большего из двух, — ждём 3-й (ответ владельца)
STORE_OFFSET_MAX = 120.0             # время магазина a + поправка — не больше 120 мин (как введённое)
# правило времени магазина (№66): n60 — №60 (выше), shrink — сглаживание к группе t = (n·факт + k·опора) / (n + k);
# действует shrink, только если проверка выбрала его (fit_unload → store_rule строки); нет выбора — n60
STORE_RULES = ('n60', 'shrink')
STORE_RULE_TITLES = {'n60': 'ըստ GPS-ի՝ 2-րդ բեռնաթափումից', 'shrink': 'GPS-ը՝ հարթեցված դեպի նման խանութները'}
SHRINK_K_BOUNDS = (1.0, 20.0)        # k = σ²_внутри / τ²_между — прижимается к этим пределам
SHRINK_K_MIN = (8, 20)               # оценка k: магазинов с ≥ 2 визитами и степеней свободы внутри них (Σ(n − 1)) не меньше
SHRINK_GROUP_MIN = 3                 # опора группы — медиана её магазинов с ≥ 2 визитами, если их не меньше; иначе a
SHRINK_MIN_TEST = (30, 3)            # смена правила: визитов проверки у магазинов с визитами в обучении и их дней
UNLOAD_MAX_MIN = 90.0                # стоянка у магазина дольше (после TAP_TAIL и вычета ожидания окна) — не разгрузка
UNLOAD_CAP_REL = 3.0                 # …или дольше 3 × действующей нормы
TAP_TAIL = timedelta(minutes=10)     # разгрузка — не дольше 10 мин после отметки доставки «закончил» (ответ владельца
                                     # №65): стоянка дольше — обед, отдых, не время магазина (unload_obs)
STORE_FACT_BOUNDS = (-STORE_OFFSET_MAX, UNLOAD_MAX_MIN)   # своё время магазина по факту в store_stats, мин: выше 90 не
                                     # бывает (стоянка не дольше 90), ниже −120 прижимается (факт — медиана «стоянка −
                                     # b·т», на деле не ниже −b·т точки; время магазина в расчёте и так не меньше 0)
LOAD_CAP_REL = 2.0                   # стоянка на складе дольше 2 × действующей нормы загрузки — не загрузка
LOAD_QUANTILE = 0.35                 # загрузка — нижний квантиль: в стоянке есть ожидание, которого план не знает
LOAD_STEP = 0.30                     # норма загрузки за прогон меняется не больше чем на ±30%
LOAD_MIN_STAY = 5.0                  # стоянка на складе короче 5 мин — проезд (развернулся, отметился), а не загрузка
LOAD_BOUNDS = ((LOAD_MIN_STAY, 45.0), (0.0, 20.0))   # правдоподобная загрузка: мин на рейс (не меньше LOAD_MIN_STAY) и
                                           # мин на тонну — выученная норма вне пределов прижимается к ним (и такая строка
                                           # журнала не действует)
FUEL_TEST = 4                        # отложенные интервалы заправок
FUEL_MIN_INTERVALS = 16              # 12 на выбор формы (measurements._fit со своей проверкой) + 4 отложенных
FUEL_MIN_KM = 50.0                   # интервал заправок короче — не считается
FUEL_L100 = (3.0, 80.0)
FUEL_TRACK_COVER = 0.6               # участки трека покрывают не меньше 60% км одометра интервала
REFUEL_KM_PER_DAY = KM_PER_DAY_MAX   # как courier.events и журнал гаража: прирост одометра больше — несогласован
REFUEL_WINDOW_DAYS = 400             # приём заправки: цепочка одометра — по заправкам ± 400 дней от неё…
REFUEL_WINDOW_MAX = 300              # …и не больше 300 (время приёма ограничено); офис — ± 200 дней вокруг дня
REFUEL_LOOKBACK = 50                 # в цепочке соседние заправки — не дальше 50 позиций (пропущено подряд ≤ 49 сомнительных)
LUNCH_MIN = (15, 5, 5, 2)            # обед: машино-дней и их дней в обучении, в проверке (как загрузка)
LUNCH_BOUNDS = (0.0, 90.0)           # выученный обед, мин
LUNCH_STEP = 0.30                    # за прогон — не больше ±30% от действующего…
LUNCH_STEP_MIN = 5.0                 # …но хотя бы на 5 мин: до 0 дойти можно (и подняться с него)
LUNCH_SLACK = 30.0                   # обед — стоянка не по плану, начавшаяся в окне начала обеда ± 30 мин
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
class LunchObs:
    day: date
    minutes: float            # обед машино-дня по треку (lunch_obs); 0 — стоянки не по плану в окне не было


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
    confidence: float | None = None   # доля повторных выборок проверки, где новая норма точнее (_accept); None — не считали


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
        return (f'քիչ տվյալներ․ ուսուցում՝ {got[0]} / {n1} ({got[1]} / {d1} օր), '
                f'ստուգում՝ {got[2]} / {n2} ({got[3]} / {d2} օր)')
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
        return True, f'ընդունված է․ սխալ {fmt_decimal(before)} → {fmt_decimal(after)}'
    return False, f'գործող նորմից առնվազն {MIN_GAIN:.0%}-ով ավելի լավ չէ․ սխալ {fmt_decimal(before)} → {fmt_decimal(after)}'


def bootstrap_share(gains: Sequence[float], resamples: int = BOOT_RESAMPLES, seed: int = BOOT_SEED) -> float:
    """Парный бутстреп по группам проверки (дни; у расхода — интервалы заправок). gains — выигрыш новой нормы в группе:
    Σ |действующая − факт| − Σ |новая − факт| на одних и тех же наблюдениях. Группы выбираются с возвращением (столько
    же, сколько их есть), одни и те же для обеих норм; итог — доля выборок, где новая норма точнее (сумма выигрышей > 0:
    знаменатель средней ошибки у обеих один). Детерминирован (своё зерно, не общий random); групп нет — 0."""
    if not gains:
        return 0.0
    rnd, n = random.Random(seed), len(gains)
    better = sum(1 for _ in range(resamples) if math.fsum(gains[rnd.randrange(n)] for _ in range(n)) > 1e-9)
    return better / resamples


def _gains(rows: Iterable[tuple[Any, float, float, float]]) -> list[float]:
    """(группа, прогноз действующей нормы, прогноз новой, факт) → выигрыш новой нормы по группам (bootstrap_share), по
    возрастанию группы."""
    acc: dict[Any, list[float]] = {}
    for g, cur, new, y in rows:
        acc.setdefault(g, []).append(abs(cur - y) - abs(new - y))
    return [math.fsum(acc[g]) for g in sorted(acc)]


GROUPS_HY = {'days': 'ստուգման օրերի', 'intervals': 'ստուգման միջակայքերի (լրիվ բաքերի միջև)'}


def _pct(share: float) -> str:
    """Доля → «93,5» (вниз до десятой: 89,96% — не «90%»)."""
    v = math.floor(share * 1000 + 1e-9) / 10
    return (f'{v:.0f}' if v == int(v) else f'{v:.1f}').replace('.', ',')


def _robust(share: float, groups: str, who: str = 'նոր նորմն') -> str:
    return f'{GROUPS_HY[groups]} {BOOT_RESAMPLES} պատահական համադրությունից {_pct(share)}%-ում {who} ավելի ճշգրիտ է'


def _accept(before: float, after: float, gains: Sequence[float], groups: str = 'days') -> tuple[bool, str, float]:
    """Правило принятия: _verdict (ошибка меньше хотя бы на MIN_GAIN) и устойчивость выигрыша — новая норма точнее
    действующей не меньше чем в BOOT_SHARE повторных выборок групп проверки (bootstrap_share по gains; groups — days |
    intervals, для текста). (принята, причина, доля выборок — Outcome.confidence)."""
    ok, why = _verdict(before, after)
    share = round(bootstrap_share(gains), 4)
    if not ok:
        return False, why, share
    if share < BOOT_SHARE:
        return False, (f'ոչ հուսալի․ սխալ {fmt_decimal(before)} → {fmt_decimal(after)}, բայց միայն '
                       f'{_robust(share, groups)} (պետք է առնվազն {_pct(BOOT_SHARE)}%)'), share
    return True, f'{why}․ հուսալի է՝ {_robust(share, groups)}', share


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

def fit_unload(obs: Sequence[UnloadObs], current: Callable[[UnloadObs], float], today: date,
               manual: Mapping[int, float] | None = None,
               plain: Callable[[UnloadObs], float] | None = None, rule: str = 'n60',
               chains: Mapping[int, str] | None = None,
               size_kg: tuple[float, float] = (60.0, 250.0)) -> Outcome:
    """Разгрузка = a·точек + b·тонн (+ своё время магазина). current — прогноз действующей нормы для наблюдения (со
    своим временем магазинов, как в расчёте: store_extras), plain — она же без своего времени магазинов. Стоянки дольше
    UNLOAD_CAP_REL × max(current, plain) не учитываются ни в обучении, ни в проверке: отсечение не зависит от
    проверяемой нормы и не строже общей нормы — введённое меньше настоящего (5 мин при 25) визиты магазина не
    выбрасывает, факт его заменит. Своё время магазина — store_stats: одиночных визитов и время по факту (a + медиана
    остатков, в пределах STORE_FACT_BOUNDS) у магазинов с ≥ STORE_MIN_OBS визитами или с введённым временем (manual:
    клиент → мин, №50; у них визиты видны на странице), у двух визитов — ещё их разброс (|разница| их времени по факту,
    для store_waits); в расчёт идёт store_times — со STORE_MIN_OBS визитов время по факту, меньше или два сильно
    расходящихся — введённое (№60). store_offsets — время по факту на день прогона (для страницы и как запас: строки до
    №50, битая запись store_stats). Проверка — ровно то, что применится: store_extras по параметрам строки и
    введённому.

    Правило времени магазина (№66, _fit_store_rule): rule — действующее (store_rule действующей строки), chains —
    клиент → код группы-сети (settings chain_groups), size_kg — пороги малый / средний магазин, кг (settings
    size_small_max_kg, size_medium_max_kg). Данных хватает для оценки k — в строку пишутся store_shrink и store_rule
    (выбор проверки с гистерезисом), store_extras строки — по выбранному правилу; не хватает — строка как до №66."""
    manual = manual or {}
    cap = current if plain is None else (lambda o: max(current(o), plain(o)))
    train, test = _capped(obs, today, cap, UNLOAD_CAP_REL)
    short = _enough(train, test, UNLOAD_MIN)
    if short:
        return Outcome('unload', '', False, short, n_obs=len(train), n_test=len(test), **_spans(train, test))
    a, b = huber_fit([(float(o.n), o.tonnes, o.minutes) for o in train])
    a, b = round(min(a, 120.0), 2), round(min(b, 120.0), 2)
    residuals: dict[int, list[float]] = {}
    tonnes: dict[int, list[float]] = {}
    for o in train:
        if o.n == 1 and len(o.customers) == 1:
            residuals.setdefault(o.customers[0], []).append(o.minutes - a - b * o.tonnes)
            tonnes.setdefault(o.customers[0], []).append(o.tonnes)
    lo, hi = STORE_FACT_BOUNDS
    stats = {str(cid): [len(rs), round(max(lo, min(hi, a + median(rs))), 1)]
             + ([round(abs(rs[0] - rs[1]), 1)] if len(rs) == 2 else [])
             for cid, rs in sorted(residuals.items()) if len(rs) >= STORE_MIN_OBS or cid in manual}
    times = store_times(a, manual, {'store_stats': stats})
    params = {'per_stop_min': a, 'per_tonne_min': b,
              'store_offsets': {str(c): e for c, (e, src) in times.items() if src == 'learned' and abs(e) >= 0.5},
              'store_stats': stats}
    # группа магазина для опоры (№66): сеть — своя группа (не «крупный»), иначе размер по среднему весу визита
    chains = chains or {}
    groups = {c: f'chain:{chains[c]}' if c in chains
              else dm.size_class(1000.0 * math.fsum(tonnes[c]) / len(tonnes[c]), None, (), *size_kg) for c in residuals}
    chosen = _fit_store_rule(test, a, b, residuals, groups, manual, params, rule)
    if chosen is not None:
        params.update(chosen[0])
    extras = store_extras(a, manual, params)

    def predict(o: UnloadObs) -> float:
        return a * o.n + b * o.tonnes + math.fsum(extras.get(c, 0.0) for c in o.customers)
    before, after = _mae((current(o), o.minutes) for o in test), _mae((predict(o), o.minutes) for o in test)
    ok, why, conf = _accept(before, after, _gains((o.day, current(o), predict(o), o.minutes) for o in test))
    if chosen is not None:
        why += f'․ խանութի ժամանակը՝ {chosen[1]}'
    return Outcome('unload', '', ok, why, params, n_obs=len(train), n_test=len(test), mae_before=round(before, 3),
                   mae_after=round(after, 3), confidence=conf, **_spans(train, test))


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
    obs = [o for o in obs if o.minutes >= LOAD_MIN_STAY]   # проезд через склад — не загрузка (load_obs уже отсеял)
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
    ok, why, conf = _accept(before, after, _gains((o.day, baseline(o), a + b * o.tonnes, o.minutes) for o in test))
    if cur is None:
        why += f' (հենակետը՝ կանգառների մեդիանը, {fmt_decimal(ref[0], 1)} րոպե․ կարգավորումներում բեռնում նշված չէ)'
    return Outcome('loading', '', ok, why, {'fixed_min': a, 'per_tonne_min': b}, n_obs=len(train), n_test=len(test),
                   mae_before=round(before, 3), mae_after=round(after, 3), confidence=conf, **_spans(train, test))


def _bucket(o: LegObs) -> tuple[bool, int, int]:
    return o.city, int(o.weekend), o.hour


def _time_per_km(ref: Mapping[str, Any], city: bool) -> float:
    """Минут модели на км по прямой (с извилистостью без карты дорог) при скоростях ref."""
    v = float(ref.get('speed_city_kmh' if city else 'speed_region_kmh') or 1.0)
    return float(ref.get('detour') or 1.0) / v


def _own_minutes(norms: Any) -> bool:
    """Время в пути норм — из дорог (Valhalla), а не км / скорость зоны: множитель по часам к нему от скоростей зон в
    настройках (их меняет и калибровка по GPS менеджеров) не зависит."""
    return bool(getattr(getattr(norms, 'roads', None), 'serves_minutes', False))


def travel_scope(norms: Any) -> str:
    """scope строки travel — к каким минутам выучена поправка: '' — прежняя модель (км / скорость зоны), 'valhalla' —
    время Valhalla (_own_minutes). Поправки обеих моделей одного дня хранятся рядом (журнал уникален по виду, scope и
    дню), а действует только своя: строки travel с id минут Valhalla и scope '' (до выбора модели учились по скорости
    зоны) к Valhalla не применяются."""
    return valhalla_engine.TRUCK_TIME_VALHALLA if _own_minutes(norms) else ''


def _travel_ratio(train: Sequence[LegObs]) -> dict[tuple[bool, int, int], float]:
    """Множитель «факт / модель» по ячейкам (город|область, будни|выходные, час) обучения: Σ факта / Σ модели ячейки
    при ≥ TRAVEL_BUCKET[0] днях и ≥ TRAVEL_BUCKET[1] модельных минутах, в пределах TRAVEL_RATIO."""
    sums: dict[tuple[bool, int, int], list[float]] = {}
    days: dict[tuple[bool, int, int], set[date]] = {}
    for o in train:
        k = _bucket(o)
        acc = sums.setdefault(k, [0.0, 0.0])
        acc[0] += o.minutes
        acc[1] += o.model
        days.setdefault(k, set()).add(o.day)
    lo, hi = TRAVEL_RATIO
    return {k: round(max(lo, min(hi, a / m)), 3) for k, (a, m) in sorted(sums.items())
            if len(days[k]) >= TRAVEL_BUCKET[0] and m >= TRAVEL_BUCKET[1] and m > 0}


def _travel_params(ratio: Mapping[tuple[bool, int, int], float], ref: Mapping[str, Any],
                   prev: Mapping[str, Any] | None, own_minutes: bool) -> dict[str, Any]:
    """Параметры профиля travel: ячейки ratio и часы prev (действующий профиль той же дорожной модели), которых в ratio
    нет, — пересчитанные к скоростям ref (own_minutes — время из дорог: от скоростей зон не зависит, как есть)."""
    lo, hi = TRAVEL_RATIO
    merged = dict(ratio)
    for c, w, h, r in (prev or {}).get('factors') or ():
        key = (bool(c), int(w), int(h))
        if key not in merged:
            pref = prev.get('ref') or {}   # type: ignore[union-attr]
            value = float(r) if own_minutes else float(r) * _time_per_km(pref, bool(c)) / _time_per_km(ref, bool(c))
            merged[key] = round(max(lo, min(hi, value)), 3)
    return {'factors': [[int(c), w, h, r] for (c, w, h), r in sorted(merged.items())], 'ref': dict(ref)}


def fit_travel(obs: Sequence[LegObs], today: date, model_id: str, ref: Mapping[str, Any], base_norms: Any,
               prev: Mapping[str, Any] | None = None) -> Outcome:
    """Множитель времени в пути «факт / модель» по ячейкам (город|область, будни|выходные, час) — ячейка при ≥ 3 днях и
    ≥ 45 модельных минутах обучения (_travel_ratio). ref — скорости (и извилистость без карты дорог), с которыми
    считалась модель; base_norms — нормы без выученного профиля (как их увидит apply_learned); prev — действующий
    выученный профиль той же дорожной модели: его часы, которых нет в новом, переносятся (_travel_params). Проверка —
    участки отложенной недели, выехавшие в часы нового профиля: действующий прогноз против travel_profile(base_norms,
    новый профиль).travel — ровно то, что применится (через границы часов). Строка журнала — scope travel_scope."""
    train, test = _split(obs, today)
    ratio = _travel_ratio(train)
    held = [o for o in test if _bucket(o) in ratio]
    scope = travel_scope(base_norms)
    if not ratio or len(held) < TRAVEL_MIN_TEST[0] or len({o.day for o in held}) < TRAVEL_MIN_TEST[1]:
        return Outcome('travel', scope, False,
                       f'քիչ տվյալներ․ պրոֆիլի վանդակներ՝ {len(ratio)}, դրանցում ստուգման հատվածներ՝ {len(held)} / '
                       f'{TRAVEL_MIN_TEST[0]} ({len({o.day for o in held})} / {TRAVEL_MIN_TEST[1]} օր)',
                       model_id=model_id, n_obs=len(train), n_test=len(held), **_spans(train, test))
    params = _travel_params(ratio, ref, prev, _own_minutes(base_norms))
    profile = travel_profile(base_norms, params)
    new = [profile.travel(o.km, o.speed, o.city, o.weekday, o.start) for o in held]
    before = _mae((o.current, o.minutes) for o in held)
    after = _mae((p, o.minutes) for p, o in zip(new, held))
    ok, why, conf = _accept(before, after, _gains((o.day, o.current, p, o.minutes) for p, o in zip(new, held)))
    return Outcome('travel', scope, ok, why, params, model_id, len(train), len(held), mae_before=round(before, 3),
                   mae_after=round(after, 3), confidence=conf, **_spans(train, test))


def _errors(pairs: Sequence[tuple[float, float]]) -> dict[str, float]:
    """Средняя абсолютная ошибка и смещение (прогноз − факт: плюс — модель считает дольше), мин на участок."""
    return {'mae': round(_mae(pairs), 3), 'bias': round(math.fsum(p - y for p, y in pairs) / len(pairs), 3)}


def _forecast(base: Any, params: Mapping[str, Any] | None, legs: Sequence[LegObs]) -> list[tuple[float, float]]:
    """(прогноз, факт) участков с профилем params поверх норм base — ровно то, что применится (travel_profile →
    .travel через границы часов); params нет — без выученной поправки (current)."""
    if not params:
        return [(o.current, o.minutes) for o in legs]
    profile = travel_profile(base, params)
    return [(profile.travel(o.km, o.speed, o.city, o.weekday, o.start), o.minutes) for o in legs]


@dataclass(frozen=True)
class _Dated:
    day: date


def truck_time_short(days: Sequence[date], today: date) -> str | None:
    """Хватает ли чистых участков факта (по дню каждого) на выбор модели времени грузовиков — пороги TRUCK_TIME_MIN, как
    _enough: текст «мало данных» или None."""
    train, test = _split([_Dated(d) for d in days], today)
    return _enough(train, test, TRUCK_TIME_MIN)


def fit_truck_time(pairs: Sequence[tuple[LegObs, LegObs]], today: date, incumbent: str, bases: Mapping[str, Any],
                   prev: Mapping[str, Mapping[str, Any] | None], no_valhalla: int = 0, correct: bool = True,
                   kept: Mapping[str, Outcome] | None = None, fact: Sequence[Any] | None = None,
                   preparing: bool = False) -> tuple[Outcome, dict[str, Outcome]]:
    """Выбор модели времени грузовиков (вид truck_time) по парам truck_time_obs — (прежняя модель, Valhalla) одного и
    того же участка: сравнение — только на общих участках. Каждая модель проверяется на одних и тех же участках
    отложенной недели с той поправкой по часам, которая у неё применится: своя, выученная на днях обучения ровно по
    правилу travel (fit_travel; «действующий прогноз» — с поправкой, что сейчас действует у её дорожной модели,
    prev[модель]), если та принята, иначе действующая prev (её нет — «как есть»). kept — модель → обычная строка
    travel этого прогона (учится на всех участках, а не только на общих): у этой модели применится она (принята) или
    prev — с ней модель и сравнивается. correct=False — поправки по часам не применяются (автообучение travel
    выключено): модели сравниваются «как есть», поправки не учатся. bases — нормы грузовиков каждой модели без
    выученного профиля (Norms.for_trucks(truck_time=…)); Valhalla недоступен — без 'valhalla'. Выбранная модель
    incumbent меняется на другую, только если ошибка той меньше хотя бы на MIN_GAIN и устойчиво (_accept: по
    повторным выборкам дней проверки) и общих участков не меньше TRUCK_TIME_MIN; иначе остаётся — в обе стороны
    (гистерезис). no_valhalla — участков без минут Valhalla (отчёт). Итог — (строка truck_time: params — выбор после
    прогона, ошибка и смещение обеих моделей «как есть» и с поправкой, участки и дни; строки travel моделей не из kept
    (fit_travel) — чтобы поправка выбранной модели была в журнале и тогда, когда «Развоз» перейдёт на неё позже: env,
    галочка, Valhalla готов).
    Valhalla недоступен (в bases нет 'valhalla') — причина по правде: fact (чистые участки факта прогона; None —
    неизвестны) меньше порогов TRUCK_TIME_MIN — «мало данных» (сравнивать и с Valhalla было бы не на чем); иначе
    preparing — Valhalla включён, но матрица грузовика для точек факта ещё считается (прогон её не дождался); иначе —
    Valhalla недоступен."""
    other = TRUCK_TIME_SOURCES[1] if incumbent == TRUCK_TIME_SOURCES[0] else TRUCK_TIME_SOURCES[0]
    stays = f'մնում է {TRUCK_TIME_DEF[incumbent]}'
    if bases.get(valhalla_engine.TRUCK_TIME_VALHALLA) is None:
        seen, held = _split(fact or (), today)
        short = _enough(seen, held, TRUCK_TIME_MIN) if fact is not None else None
        if short is not None:
            reason = short
        elif preparing:
            reason = ('Valhalla-ն միացված է, բայց փաստի կետերի համար բեռնատարի ժամանակները դեռ հաշվվում են․ '
                      'համեմատությունը կլինի հաջորդ վերահաշվարկին')
        else:
            reason = ('Valhalla-ն հասանելի չէ (անջատված է, չկա փաթեթը կամ սալիկները, կամ բեռնատարի մատրիցը փաստի '
                      'կետերի համար դեռ հաշվվում է)')
        return Outcome('truck_time', '', False, f'{reason} — {stays}', n_obs=len(seen), n_test=len(held),
                       **_spans(seen, held)), {}
    kept = kept or {}
    obs = {src: [p[k] for p in pairs] for k, src in enumerate(TRUCK_TIME_SOURCES)}
    train, test = _split(obs[incumbent], today)              # участки — одни и те же у обеих моделей
    errors: dict[str, float] = {}                             # модель → ошибка на проверке с её поправкой
    candidates: dict[str, dict[str, dict[str, float]]] = {}
    predicted: dict[str, list[float]] = {}                    # модель → прогноз участков проверки с её поправкой
    fitted: dict[str, Outcome] = {}
    for src in TRUCK_TIME_SOURCES if test else ():
        base, old, applied = bases[src], prev.get(src) if correct else None, None
        if correct and src in kept:                          # поправку этой модели прогон уже учил на всех участках
            applied = kept[src].params if kept[src].accepted else old
        elif correct:
            now = _forecast(base, old, obs[src])             # «действующий прогноз» модели — с её поправкой
            fitted[src] = fit_travel([replace(o, current=p) for o, (p, _) in zip(obs[src], now)], today,
                                     road_model_id(base) or 'straight', model_ref(base), base, old)
            applied = fitted[src].params if fitted[src].accepted else old
        _, te = _split(obs[src], today)
        raw, learned = _forecast(base, None, te), _forecast(base, applied, te)
        errors[src] = _mae(learned)
        candidates[src] = {'raw': _errors(raw), 'learned': _errors(learned)}
        predicted[src] = [p for p, _ in learned]
    counts = {'legs': {'train': len(train), 'test': len(test), 'no_valhalla': no_valhalla},
              'days': {'train': len({o.day for o in train}), 'test': len({o.day for o in test})},
              'corrected': correct}
    before, after = errors.get(incumbent), errors.get(other)   # None — проверки нет
    short = _enough(train, test, TRUCK_TIME_MIN)
    better, robust, conf = False, False, None
    if short is None and before is not None and after is not None:   # участки проверки у обеих моделей — одни и те же
        better = _verdict(before, after)[0]
        robust, _, conf = _accept(before, after, _gains((o.day, a, b, o.minutes) for o, a, b in
                                                        zip(test, predicted[incumbent], predicted[other])))
    ok = better and robust
    if short is not None:
        reason = f'{short} — {stays}'
    elif ok:
        reason = (f'{TRUCK_TIME_SUBJ[other]} ավելի ճշգրիտ է, քան {TRUCK_TIME_DEF[incumbent]}․ սխալ {fmt_decimal(before)} → '
                  f'{fmt_decimal(after)} րոպե մեկ հատվածի համար․ հուսալի է՝ '
                  f'{_robust(conf, "days", TRUCK_TIME_SUBJ[other])}')
    elif better:
        reason = (f'ոչ հուսալի․ սխալ {fmt_decimal(before)} → {fmt_decimal(after)} րոպե մեկ հատվածի համար, բայց միայն '
                  f'{_robust(conf, "days", TRUCK_TIME_SUBJ[other])} (պետք է առնվազն {_pct(BOOT_SHARE)}%) — {stays}')
    else:
        reason = (f'{TRUCK_TIME_SUBJ[other]} առնվազն {MIN_GAIN:.0%}-ով ավելի ճշգրիտ չէ, քան '
                  f'{TRUCK_TIME_DEF[incumbent]}․ սխալ {fmt_decimal(before)} → {fmt_decimal(after)} րոպե մեկ հատվածի համար — {stays}')
    params = {'source': other if ok else incumbent, 'challenger': other, **counts, 'candidates': candidates}
    return Outcome('truck_time', '', ok, reason, params, None, len(train), len(test),
                   mae_before=round(before, 3) if before is not None else None,
                   mae_after=round(after, 3) if after is not None else None, confidence=conf,
                   **_spans(train, test)), fitted


def fit_lunch(obs: Sequence[LunchObs], current: float, today: date, setting: float) -> Outcome:
    """Обед в пути (№61): выученное — медиана обеда машино-дней обучения (целые минуты, в пределах LUNCH_BOUNDS), шаг за
    прогон от действующего current — не больше ±max(LUNCH_STEP × current, LUNCH_STEP_MIN): до 0 дойти можно. Проверка
    — минут на машино-день отложенной недели: current против выученного, правило принятия — _accept. setting — обед в
    настройках: 0 — обед выключен, не учится."""
    if setting <= 0:
        return Outcome('lunch', '', False, 'ճաշն անջատված է կարգավորումներում (0 րոպե)․ ծրագիրն այն չի սովորում')
    train, test = _split(obs, today)
    short = _enough(train, test, LUNCH_MIN)
    if short:
        return Outcome('lunch', '', False, short, n_obs=len(train), n_test=len(test), **_spans(train, test))
    step = max(LUNCH_STEP * current, LUNCH_STEP_MIN)
    value = min(current + step, max(current - step, median(o.minutes for o in train)))
    value = float(round(min(LUNCH_BOUNDS[1], max(LUNCH_BOUNDS[0], value))))
    before = _mae((current, o.minutes) for o in test)
    after = _mae((value, o.minutes) for o in test)
    ok, why, conf = _accept(before, after, _gains((o.day, current, value, o.minutes) for o in test))
    return Outcome('lunch', '', ok, why, {'minutes': value}, n_obs=len(train), n_test=len(test),
                   mae_before=round(before, 3), mae_after=round(after, 3), confidence=conf, **_spans(train, test))


def fit_fuel(obs: Sequence[FuelObs], current: Callable[[float], float], car: str) -> Outcome:
    """Расход л/100 км = пустой + (полный − пустой) × загрузка. Форма выбирается только по обучающим интервалам
    (все, кроме последних FUEL_TEST): measurements._fit (≥ 12 интервалов, разброс загрузки ≥ 20%, своя проверка на
    последних 4 обучающих) — зависимость от загрузки; не подтвердилась — один расход (медиана обучения). Последние
    FUEL_TEST интервалов — только проверка: принимается при ошибке на ≥ 2% меньше, чем у действующей нормы машины
    (current(загрузка) → л/100 км), и устойчиво — по повторным выборкам интервалов проверки (_accept)."""
    rows = sorted(({'day': o.day.isoformat(), 'load': o.load, 'l100': o.l100} for o in obs),
                  key=lambda r: (r['day'], r['load'], r['l100']))
    if len(rows) < FUEL_MIN_INTERVALS or len({r['day'] for r in rows}) < FUEL_MIN_INTERVALS:
        return Outcome('fuel', car, False, f'քիչ տվյալներ․ լրիվ բաքերի միջև միջակայքեր՝ {len(rows)} / '
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
    ok, why, conf = _accept(before, after, _gains((k, current(r['load']), predict(r['load']), r['l100'])
                                                  for k, r in enumerate(test)), 'intervals')
    return Outcome('fuel', car, ok, why, {'empty_l100': empty, 'full_l100': full}, n_obs=len(train), n_test=len(test),
                   train_from=train[0]['day'], train_to=train[-1]['day'], test_from=test[0]['day'],
                   test_to=test[-1]['day'], mae_before=round(before, 3), mae_after=round(after, 3), confidence=conf)


# --- действующие нормы и применение в расчёте ---

@dataclass(frozen=True)
class InEffect:
    """Выученные нормы, действующие в расчёте (None / пусто — ручные настройки)."""
    unload: Mapping[str, Any] | None = None
    loading: Mapping[str, Any] | None = None
    travel: Mapping[str, Any] | None = None        # params + 'model_id'
    fuel: Mapping[str, Mapping[str, Any]] | None = None   # машина → params
    lunch: Mapping[str, Any] | None = None

    def __bool__(self) -> bool:
        return bool(self.unload or self.loading or self.travel or self.fuel or self.lunch)


def _num(x: Any, lo: float, hi: float) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and lo <= x <= hi


def valid_params(kind: str, p: Any) -> bool:
    """Параметры строки журнала годятся для расчёта (битая строка — не действует, «Развоз» — на прежней норме)."""
    if not isinstance(p, Mapping):
        return False
    if kind == 'unload':   # store_stats на строку не влияют: битая запись — только у её магазина (store_stats())
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
    if kind == 'truck_time':   # в расчёт идёт только источник; сравнение — для страницы
        return p.get('source') in TRUCK_TIME_SOURCES
    if kind == 'lunch':
        return _num(p.get('minutes'), *LUNCH_BOUNDS)
    return False


def in_effect(rows: Sequence[Mapping[str, Any]], auto: Mapping[str, bool], model_id: str | None,
              scope: str = '') -> InEffect:
    """Строки learned_norms (по возрастанию run_day) → действующие нормы: по (вид, машина) — последняя принятая с
    корректными параметрами (valid_params); travel — последняя принятая той же дорожной модели (model_id; None —
    дорожная модель не поддерживается) и тех же минут (scope — travel_scope норм); вид с выключенным автообучением
    (auto_on) — не действует. Модель времени грузовиков (truck_time) выбирается до среза дорог — truck_time_learned,
    здесь её нет."""
    last: dict[tuple[str, str], Mapping[str, Any]] = {}
    for r in rows:
        if not r['accepted'] or not auto_on(auto, r['kind']) or not valid_params(r['kind'], r['params']):
            continue
        if r['kind'] == 'travel' and (model_id is None or r['model_id'] != model_id):
            continue
        last[(r['kind'], r['scope'])] = r   # travel — по scope: действует строка тех же минут (ниже)
    fuel = {car: r['params'] for (kind, car), r in sorted(last.items()) if kind == 'fuel'}
    travel = last.get(('travel', scope))
    return InEffect(last[('unload', '')]['params'] if ('unload', '') in last else None,
                    last[('loading', '')]['params'] if ('loading', '') in last else None,
                    {**travel['params'], 'model_id': travel['model_id']} if travel is not None else None,
                    fuel or None, last[('lunch', '')]['params'] if ('lunch', '') in last else None)


def truck_time_learned(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """Модель времени грузовиков, выбранная обучением: источник последней принятой строки truck_time с корректными
    параметрами (строки — по возрастанию run_day); выбора ещё не было — None. Переключатель автообучения здесь не
    учитывается: от этого выбора идёт следующее сравнение (гистерезис) — применяет его views: env ROUTES_TRUCK_TIME >
    этот выбор (если автообучение вида включено) > прежняя модель (valhalla_engine.truck_time_source)."""
    out = None
    for r in rows:
        if r['kind'] == 'truck_time' and r['accepted'] and valid_params('truck_time', r['params']):
            out = r['params']['source']
    return out


def road_model_id(norms: Any) -> str | None:
    """Дорожная модель расчёта времени в пути — id из valhalla_engine.road_model_id (граф OSM, Valhalla или по прямой
    × извилистость; грузовики — norms.for_trucks()). Внешний поставщик (Яндекс) считает время сам — поправка по часам
    к нему не применяется (None)."""
    if getattr(norms, 'provider', None) is not None:
        return None
    return valhalla_engine.road_model_id(getattr(norms, 'roads', None))


def _speed(norms: Any, city: bool) -> float:
    return float(norms.speed_city_kmh if city else norms.speed_region_kmh)


def model_ref(norms: Any) -> dict[str, Any]:
    """С какими скоростями (и извилистостью без карты) считалась модель обучения — чтобы применить множитель при
    других скоростях настроек: время модели = км / скорость (у времени из дорог — справочно: _own_minutes)."""
    return {'speed_city_kmh': norms.speed_city_kmh, 'speed_region_kmh': norms.speed_region_kmh,
            'detour': norms.detour if getattr(norms, 'roads', None) is None else None}


def speed_factors(travel: Mapping[str, Any], norms: Any) -> dict[tuple[bool, int, int], float]:
    """Множители «факт / модель» времени → множители скорости TrafficProfile при текущих скоростях: время на участке
    = км_тек / (v_тек · f) должно равняться r · км_обуч / v_обуч, км_тек / км_обуч = извилистость_тек / извилистость_обуч
    (по карте дорог — 1). Время из дорог (_own_minutes: Valhalla) от скоростей зон не зависит: f = 1 / r."""
    ref = travel.get('ref') or {}
    own = _own_minutes(norms)
    km_ratio = (float(norms.detour) / float(ref['detour'])) if ref.get('detour') else 1.0
    out = {}
    for c, w, h, r in travel.get('factors') or ():
        city = bool(c)
        if own:
            out[(city, int(w), int(h))] = 1.0 / float(r)
            continue
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


def _store_entries(unload: Mapping[str, Any] | None) -> dict[int, tuple[int, float, float | None]]:
    """store_stats строки → клиент → (одиночных визитов, своё время по факту, мин; разброс двух визитов, мин, —
    третий элемент записи, у строк до «ждать 3-ю» его нет: None). Битая запись пропускается: у её магазина — правило
    без факта (store_times), сама строка от неё не портится."""
    raw = (unload or {}).get('store_stats')
    out: dict[int, tuple[int, float, float | None]] = {}
    for c, v in raw.items() if isinstance(raw, Mapping) else ():
        if (isinstance(c, str) and c.isdigit() and isinstance(v, (list, tuple)) and len(v) in (2, 3)
                and isinstance(v[0], int) and not isinstance(v[0], bool) and v[0] >= 1
                and _num(v[1], *STORE_FACT_BOUNDS) and (len(v) == 2 or _num(v[2], 0.0, math.inf))):
            out[int(c)] = (v[0], float(v[1]), float(v[2]) if len(v) == 3 else None)
    return out


def store_stats(unload: Mapping[str, Any] | None) -> dict[int, tuple[int, float]]:
    """store_stats строки → клиент → (одиночных визитов, своё время по факту, мин); битая запись пропускается."""
    return {c: (n, fact) for c, (n, fact, _) in _store_entries(unload).items()}


def store_waits(unload: Mapping[str, Any] | None) -> set[int]:
    """Магазины, чьё время по GPS ждёт третьего визита (ответ владельца «ждать 3-ю»): ровно два визита, и их время по
    факту (минуты − b·т) разнится больше чем на STORE_SPLIT_MIN и больше чем на STORE_SPLIT_REL от большего из двух
    (= середина + разброс / 2). Запись без разброса (строка до этого правила) — как раньше: время по факту."""
    return {c for c, (n, fact, spread) in _store_entries(unload).items() if n == 2 and spread is not None
            and spread > STORE_SPLIT_MIN and spread > STORE_SPLIT_REL * (fact + spread / 2)}


def store_times(per_stop: float, manual: Mapping[int, float],
                unload: Mapping[str, Any] | None) -> dict[int, tuple[float, str]]:
    """Своё время магазинов в расчёте → клиент → (поправка к норме на точку per_stop, мин на визит; learned | manual), у
    кого его нет — норма. per_stop — норма на точку в расчёте (действует строка unload — её per_stop_min). Правило №60:
    с фактом (store_stats строки, ≥ STORE_MIN_OBS визитов; два сильно расходящихся — store_waits — ещё не факт) — время
    по факту, без смеси с введённым (manual: клиент → абсолютные минуты) и нормой; время магазина не больше
    STORE_OFFSET_MAX само собой (факт ≤ UNLOAD_MAX_MIN), поправка — до 0,1 мин, меньше 0,5 мин — ноль (как не хранили
    строки до №50). Без факта (мало визитов, два расходятся, строка до №50, битая запись): введённое (manual −
    per_stop), иначе поправка строки (store_offsets). Разгрузка не бывает отрицательной: поправка не меньше −per_stop
    (время магазина не меньше 0). Строка, где проверка выбрала сглаживание к группе (store_rule, №66), — shrink_times."""
    if store_rule(unload) == 'shrink':
        return shrink_times(per_stop, manual, unload)
    stats, waits = store_stats(unload), store_waits(unload)
    offsets = {int(c): float(v) for c, v in ((unload or {}).get('store_offsets') or {}).items()}
    out: dict[int, tuple[float, str]] = {}
    for c in sorted(set(manual) | set(offsets) | set(stats)):
        n, fact = stats.get(c, (0, 0.0))
        if n >= STORE_MIN_OBS and c not in waits:
            extra = round(fact - per_stop, 1)
            out[c] = (0.0 if abs(extra) < 0.5 else extra, 'learned')
        elif c in manual:
            out[c] = (float(manual[c]) - per_stop, 'manual')
        elif c in offsets:
            out[c] = (offsets[c], 'learned')
    return {c: (max(-per_stop, e), src) for c, (e, src) in out.items()}


def store_extras(per_stop: float, manual: Mapping[int, float], unload: Mapping[str, Any] | None) -> dict[int, float]:
    """store_times без источника: клиент → поправка к норме на точку, мин на визит."""
    return {c: e for c, (e, _) in store_times(per_stop, manual, unload).items()}


# --- время магазина: сглаживание к группе (№66) ---

def shrink_k(residuals: Mapping[int, Sequence[float]], groups: Mapping[int, str]) -> float | None:
    """k = σ²_внутри / τ²_между методом моментов по магазинам с ≥ 2 визитами, один на все группы: σ² — объединённая
    дисперсия визитов около среднего своего магазина (Σ(n − 1) степеней свободы), τ² — дисперсия средних магазинов около
    среднего их группы (магазинов − групп степеней свободы) за вычетом шума среднего σ² · ср.(1/n). Прижат к
    SHRINK_K_BOUNDS: τ² ≤ 0 (различие магазинов не больше шума) — верхний предел. Магазинов или степеней свободы меньше
    SHRINK_K_MIN (или все магазины — по одному в группе) — None: сглаживание не участвует."""
    many = {c: rs for c, rs in residuals.items() if len(rs) >= 2}
    df_within = sum(len(rs) - 1 for rs in many.values())
    by_group: dict[str, list[int]] = {}
    for c in sorted(many):
        by_group.setdefault(groups[c], []).append(c)
    df_between = len(many) - len(by_group)
    if len(many) < SHRINK_K_MIN[0] or df_within < SHRINK_K_MIN[1] or df_between < 1:
        return None
    means = {c: math.fsum(rs) / len(rs) for c, rs in many.items()}
    sigma2 = math.fsum((r - means[c]) ** 2 for c, rs in many.items() for r in rs) / df_within
    centre = {g: math.fsum(means[c] for c in cs) / len(cs) for g, cs in by_group.items()}
    between = math.fsum((means[c] - centre[g]) ** 2 for g, cs in by_group.items() for c in cs) / df_between
    tau2 = between - sigma2 * math.fsum(1.0 / len(rs) for rs in many.values()) / len(many)
    lo, hi = SHRINK_K_BOUNDS
    return hi if tau2 <= 0 else round(min(hi, max(lo, sigma2 / tau2)), 2)


def shrink_entries(residuals: Mapping[int, Sequence[float]], a: float,
                   groups: Mapping[int, str]) -> dict[str, list[float]]:
    """Записи store_shrink строки: клиент → [одиночных визитов n, своё время по факту (a + медиана остатков, как №60, в
    пределах STORE_FACT_BOUNDS), опора группы]. Опора — медиана времени по факту магазинов той же группы с ≥
    STORE_MIN_OBS визитами, если таких не меньше SHRINK_GROUP_MIN, иначе a; в пределах [0, STORE_OFFSET_MAX]. Введённое
    логистом — опора сильнее группы, но берётся при применении (shrink_times): изменённое действует сразу."""
    lo, hi = STORE_FACT_BOUNDS
    fact = {c: max(lo, min(hi, a + median(rs))) for c, rs in residuals.items()}
    members: dict[str, list[float]] = {}
    for c, rs in sorted(residuals.items()):
        if len(rs) >= STORE_MIN_OBS:
            members.setdefault(groups[c], []).append(fact[c])
    prior = {g: median(fs) for g, fs in members.items() if len(fs) >= SHRINK_GROUP_MIN}
    return {str(c): [len(rs), round(fact[c], 1), round(min(STORE_OFFSET_MAX, max(0.0, prior.get(groups[c], a))), 1)]
            for c, rs in sorted(residuals.items())}


def store_rule(unload: Mapping[str, Any] | None) -> str:
    """Правило времени магазина строки unload: 'shrink' — проверка выбрала сглаживание к группе (store_rule.rule) и k
    корректен; иначе (строки до №66, выбор №60, битая запись) — 'n60'."""
    r = (unload or {}).get('store_rule')
    return 'shrink' if (isinstance(r, Mapping) and r.get('rule') == 'shrink' and _num(r.get('k'), *SHRINK_K_BOUNDS)
                        and isinstance(unload.get('store_shrink'), Mapping)) else 'n60'   # type: ignore[union-attr]


def store_shrink(unload: Mapping[str, Any] | None) -> dict[int, tuple[int, float, float]]:
    """store_shrink строки → клиент → (визитов, своё время по факту, опора группы), мин; битая запись пропускается (у её
    магазина — введённое или норма)."""
    raw = (unload or {}).get('store_shrink')
    out: dict[int, tuple[int, float, float]] = {}
    for c, v in raw.items() if isinstance(raw, Mapping) else ():
        if (isinstance(c, str) and c.isdigit() and isinstance(v, (list, tuple)) and len(v) == 3
                and isinstance(v[0], int) and not isinstance(v[0], bool) and v[0] >= 1
                and _num(v[1], *STORE_FACT_BOUNDS) and _num(v[2], 0.0, STORE_OFFSET_MAX)):
            out[int(c)] = (v[0], float(v[1]), float(v[2]))
    return out


def shrink_times(per_stop: float, manual: Mapping[int, float],
                 unload: Mapping[str, Any] | None) -> dict[int, tuple[float, str]]:
    """store_times по сглаживанию к группе: у магазина с визитами (store_shrink) t = (n·факт + k·опора) / (n + k), опора —
    введённое (manual), без него — опора группы строки; источник 'shrink_manual' | 'shrink'. Без визитов — введённое
    ('manual'), без него — норма (нет в ответе). Поправка t − per_stop — до 0,1 мин, меньше 0,5 — ноль, не меньше
    −per_stop (как store_times)."""
    k = float(unload['store_rule']['k'])   # type: ignore[index]
    entries = store_shrink(unload)
    out: dict[int, tuple[float, str]] = {}
    for c in sorted(set(manual) | set(entries)):
        if c in entries:
            n, fact, group = entries[c]
            prior = float(manual[c]) if c in manual else group
            extra = round((n * fact + k * prior) / (n + k) - per_stop, 1)
            out[c] = (0.0 if abs(extra) < 0.5 else extra, 'shrink_manual' if c in manual else 'shrink')
        else:
            out[c] = (float(manual[c]) - per_stop, 'manual')
    return {c: (max(-per_stop, e), src) for c, (e, src) in out.items()}


def _fit_store_rule(test: Sequence[UnloadObs], a: float, b: float, residuals: Mapping[int, Sequence[float]],
                    groups: Mapping[int, str], manual: Mapping[int, float], params: Mapping[str, Any],
                    incumbent: str) -> tuple[dict[str, Any], str] | None:
    """Выбор правила времени магазина (№66) — как truck_time: оба правила (params строки по №60 и он же со
    сглаживанием к группе) с одними a, b предсказывают визиты отложенной недели, в которых есть магазин с визитами в
    обучении (у остальных прогнозы одинаковы); действующее правило incumbent меняется на другое, только если ошибка того
    меньше хотя бы на MIN_GAIN (_verdict) и таких визитов и дней не меньше SHRINK_MIN_TEST — в обе стороны (гистерезис).
    k не оценить (shrink_k) — None: сглаживание не участвует, строка — как до №66. Итог — (store_shrink и store_rule
    строки: выбранное правило, k, ошибки обоих, визиты и дни проверки; причина по-армянски)."""
    k = shrink_k(residuals, groups)
    if k is None:
        return None
    incumbent = incumbent if incumbent in STORE_RULES else 'n60'
    other = 'shrink' if incumbent == 'n60' else 'n60'
    entries = shrink_entries(residuals, a, groups)
    variants = {'n60': params, 'shrink': {**params, 'store_rule': {'rule': 'shrink', 'k': k}, 'store_shrink': entries}}
    held = [o for o in test if any(c in residuals for c in o.customers)]
    days = len({o.day for o in held})
    errors: dict[str, float] = {}
    for name, p in variants.items() if held else ():
        extras = store_extras(a, manual, p)
        errors[name] = round(_mae((a * o.n + b * o.tonnes + math.fsum(extras.get(c, 0.0) for c in o.customers),
                                   o.minutes) for o in held), 3)
    enough = len(held) >= SHRINK_MIN_TEST[0] and days >= SHRINK_MIN_TEST[1]
    switch = enough and _verdict(errors[incumbent], errors[other])[0]
    chosen = other if switch else incumbent
    stays = f'մնում է «{STORE_RULE_TITLES[incumbent]}» կանոնը'
    if not enough:
        reason = (f'կանոնները համեմատելու համար ստուգման բեռնաթափումները քիչ են՝ {len(held)} / {SHRINK_MIN_TEST[0]} '
                  f'({days} / {SHRINK_MIN_TEST[1]} օր) — {stays}')
    else:
        gap = f'սխալ {fmt_decimal(errors[incumbent])} → {fmt_decimal(errors[other])} րոպե'
        reason = (f'«{STORE_RULE_TITLES[other]}» կանոնն ավելի ճշգրիտ է․ {gap}' if switch else
                  f'«{STORE_RULE_TITLES[other]}» կանոնն առնվազն {MIN_GAIN:.0%}-ով ավելի ճշգրիտ չէ․ {gap} — {stays}')
    return {'store_shrink': entries, 'store_rule': {'rule': chosen, 'k': k, 'mae': errors, 'n_test': len(held),
                                                    'days_test': days}}, reason


def unload_extra(per_stop: float, manual: Mapping[int, float], unload: Mapping[str, Any] | None,
                 customer_points: Mapping[int, Point]) -> dict[Point, float]:
    """store_extras → по точке дня (TruckNorms.unload_extra: fleet видит точки, а не клиентов). Клиенты дня в одной
    точке (одни координаты) — отдельные стоянки fleet, а поправка у точки одна: среднее своих поправок этих клиентов
    (без своего времени — 0). Разгрузка точки в сумме — своё время каждого магазина, как считает обучение (Σ по
    клиентам стоянки) и показывает страница; время одного магазина на соседа не переносится. Не зависит от порядка
    клиентов. Ограничение (принято по данным): если магазины точки попали в разные рейсы, у каждого рейса — среднее, и
    медленный магазин недосчитан, а соседний пересчитан (101 — 40 мин, 102 — обычные 8: в разных рейсах у обоих по 24),
    сумма за день та же. У владельца это редкость: за 28 дней заказов в общей точке 2 из 2 393 стоянок дня, в рейсах
    логиста (6 дней) и в пересборках разделений нет. Станет частым — своё время по стоянке (route_day по индексам)."""
    extras = store_extras(per_stop, manual, unload)
    groups: dict[Point, list[int]] = {}
    for c, point in customer_points.items():
        groups.setdefault(point, []).append(c)
    return {p: math.fsum(extras.get(c, 0.0) for c in cs) / len(cs) for p, cs in groups.items()
            if any(c in extras for c in cs)}


def apply_learned(norms: Any, tn: Any, trucks: Mapping[str, Any], eff: InEffect,
                  customer_points: Mapping[int, Point],
                  manual: Mapping[int, float] | None = None) -> tuple[Any, Any, dict[str, Any]]:
    """Действующие выученные нормы и введённое время магазинов (manual: клиент → мин, №50 — действует и без выученных)
    → (norms, нормы машин, машины) расчёта «Развоза». Без выученных и без введённого — те же объекты. customer_points —
    клиент → точка дня (своё время магазина — по точке, как её видит fleet: unload_extra; считается заново, от нормы на
    точку после выученного). Расход машины: пустой/полный — в расчёт по остаточному грузу
    (running_costs.route_cost), а единый l100 машины (стоимость км в PyVRP, выбор машины и проверка «км × л/100» при
    выравнивании) — расход при половинной загрузке: рейс выезжает загруженным и возвращается пустым, средний груз на
    борту — около половины загрузки выезда. Обед (№61) — выученные минуты, если обед включён в настройках (tn с обедом)."""
    trucks = dict(trucks)
    if eff.unload:
        p = eff.unload
        tn = replace(tn, unload_min_per_stop=float(p['per_stop_min']), unload_min_per_tonne=float(p['per_tonne_min']))
    if eff.unload or manual:
        tn = replace(tn, unload_extra=unload_extra(tn.unload_min_per_stop, manual or {}, eff.unload, customer_points))
    if eff.loading:
        tn = replace(tn, warehouse_load_fixed_min=float(eff.loading['fixed_min']),
                     warehouse_load_min_per_tonne=float(eff.loading['per_tonne_min']), loading_configured=True)
    if eff.travel and road_model_id(norms) == eff.travel.get('model_id'):
        norms = replace(norms, traffic=travel_profile(norms, eff.travel))
    if eff.lunch and tn.lunch_minutes > 0:
        tn = replace(tn, lunch_minutes=float(eff.lunch['minutes']))
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

    def refuels(self, since: str = '') -> list[dict[str, Any]]:
        """Заправки (since — с этого дня, YYYY-MM-DD; пусто — все): id, car_code, date, at, at_utc, eff_at_utc, eff_date,
        payload, flags, superseded."""
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
    окно приёма — из настроек магазина; доставлено — доля по отметке водителя (по весу товаров, №65:
    courier.facts.delivered_share) × вес накладной, момент отметки."""
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
class PlanLunch:
    """Обед по плану в рейсе (прогноз сборки, №61): где — store (после разгрузки магазина customer), depot (на складе до
    загрузки рейса), road (в дороге); минуты обеда."""
    where: str
    customer: int | None
    minutes: float


@dataclass(frozen=True)
class PlanTrip:
    """Плановый рейс машины (прогноз сборки): начало загрузки, плановое возвращение предыдущего рейса (None — первый
    рейс дня), плановый выезд, клиенты, обед по плану в этом рейсе (None — нет; прогноз до №61 — тоже)."""
    loading_start: datetime | None
    prev_return: datetime | None
    depart: datetime | None
    customers: frozenset[int]
    lunch: PlanLunch | None = None

    @property
    def wait(self) -> tuple[datetime, datetime] | None:
        """Собственное ожидание плана на складе: [возвращение предыдущего рейса, начало загрузки], если план нарочно
        держит машину (выезд позже под окно первой точки); иначе None."""
        if self.prev_return is None or self.loading_start is None or self.loading_start <= self.prev_return:
            return None
        return self.prev_return, self.loading_start


def _plan_lunch(raw: Any) -> PlanLunch | None:
    """Обед рейса из прогноза ({where, customer, minutes}); битый — None."""
    if not isinstance(raw, Mapping) or raw.get('where') not in ('store', 'depot', 'road') \
            or not _num(raw.get('minutes'), 0, 240):
        return None
    cid = raw.get('customer')
    if raw['where'] == 'store' and (not isinstance(cid, int) or isinstance(cid, bool)):
        return None
    return PlanLunch(raw['where'], cid if raw['where'] == 'store' else None, float(raw['minutes']))


def plan_trips(prediction: Mapping[str, Any] | None, day: date) -> list[PlanTrip]:
    """Прогноз сборки машины (views._capture_prediction: trips — loading_start, depart, return «HH:MM», stops —
    [[клиент, ETA]], lunch — обед по плану) → плановые рейсы по порядку. Прогноз старый (без рейсов) — пусто."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=ac.YEREVAN)

    def at(text: Any) -> datetime | None:
        m = _hhmm(text)
        return midnight + timedelta(minutes=m) if m is not None else None
    trips = [t for t in (prediction or {}).get('trips') or () if isinstance(t, Mapping)]
    return [PlanTrip(at(t.get('loading_start')), at(trips[j - 1].get('return')) if j else None, at(t.get('depart')),
                     frozenset(c[0] for c in t.get('stops') or () if isinstance(c, list) and c and isinstance(c[0], int)),
                     _plan_lunch(t.get('lunch')))
            for j, t in enumerate(trips)]


def lunch_customers(plan: Sequence[PlanTrip]) -> frozenset[int]:
    """Магазины, после разгрузки которых по плану обед: их визит — не в обучение разгрузки (unload_obs, skip)."""
    return frozenset(t.lunch.customer for t in plan if t.lunch is not None and t.lunch.where == 'store'
                     and t.lunch.customer is not None)


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


def _trip_cids(actual: ac.DayActual, stops: Sequence[ac.PlanStop], t: ac.Trip) -> set[int]:
    by_key = {s.key: s for s in stops}
    return {by_key[k].customer_id for k, i in actual.served if i in t.visits and k in by_key   # type: ignore[misc]
            and by_key[k].customer_id is not None}


def lunch_obs(day: date, actual: ac.DayActual, window: tuple[float, float], stops: Sequence[ac.PlanStop] = (),
              plan: Sequence[PlanTrip] = (), expected: Callable[[UnloadObs], float] | None = None) -> LunchObs | None:
    """Обед, который машина взяла за день (№61, правило — в шапке модуля): больший из двух — самая длинная стоянка не по
    плану, начавшаяся в окне начала обеда window (минуты от полуночи рабочего дня) ± LUNCH_SLACK, и излишек стоянки там,
    где обед по плану (plan — плановые рейсы с обедом, plan_trips): у магазина — стоянка его обслуживающего визита без
    разгрузки по действующей норме (expected — прогноз нормы для визита, как в обучении разгрузки; доставлено
    неизвестно — вес накладной) и без ожидания окна приёма до начала окна обеда; на складе — стоянка перед рейсом сверх
    плановой загрузки и планового простоя без обеда; в дороге — только стоянка не по плану. Обеда в плане нет — None
    (где искать, неизвестно); машина работала не дольше конца окна — None (такой день план и не кормит)."""
    meal = next(((n, t) for n, t in enumerate(plan) if t.lunch is not None), None)
    if meal is None:
        return None
    ends = [t.ret for t in actual.trips if t.ret is not None] + [v.leave for v in actual.visits]
    if not ends or ac.day_minutes(day, max(ends)) <= window[1]:
        return None
    lo, hi = window[0] - LUNCH_SLACK, window[1] + LUNCH_SLACK
    stays = [s.minutes for s in actual.stays if s.kind == 'other' and lo <= ac.day_minutes(day, s.arrive) <= hi]
    place = 0.0
    _, trip = meal
    lunch = trip.lunch
    if lunch.where == 'store':   # type: ignore[union-attr]
        by_key = {s.key: s for s in stops}
        vi = next((i for k, i in actual.served if k in by_key and by_key[k].customer_id == lunch.customer), None)  # type: ignore[union-attr]
        if vi is not None:
            v = actual.visits[vi]
            ss = [by_key[k] for k in v.keys if k in by_key]
            tonnes = math.fsum(s.delivered_kg if s.delivered_kg is not None else s.kg for s in ss) / 1000.0
            norm = expected(UnloadObs(day, len(ss), tonnes, 0.0, tuple(sorted(s.customer_id for s in ss
                                                                                if s.customer_id is not None)))) \
                if expected is not None else 0.0
            arrive = ac.day_minutes(day, v.arrive)
            opens = max((s.window[0] for s in ss if s.window is not None and math.isfinite(s.window[0])), default=None)
            early = max(0.0, min(opens, window[0]) - arrive) if opens is not None else 0.0
            place = v.minutes - norm - early
    elif lunch.where == 'depot' and trip.loading_start is not None and trip.depart is not None:   # type: ignore[union-attr]
        for n, t in enumerate(actual.trips):
            if t.depart is None or _planned_trip(plan, n, _trip_cids(actual, stops, t), t.depart) is not trip:
                continue
            stay = next((s for s in actual.stays if s.kind == 'depot' and s.leave == t.depart), None)
            if stay is None:
                break
            loading = (trip.depart - trip.loading_start).total_seconds() / 60.0
            gap = ((trip.loading_start - trip.prev_return).total_seconds() / 60.0 if trip.prev_return is not None
                   else lunch.minutes)   # type: ignore[union-attr]
            place = stay.minutes - loading - max(0.0, gap - lunch.minutes)   # type: ignore[union-attr]
            break
    return LunchObs(day, round(max(0.0, place, *stays), 1))


def unload_obs(day: date, actual: ac.DayActual, stops: Sequence[ac.PlanStop],
               skip: Collection[int] = ()) -> list[UnloadObs]:
    """Обслуживающие визиты (не повторные), у всех точек которых известно доставленное. Разгрузка — по порядку:
    1) конец — начало движения, но не позже TAP_TAIL после отметки доставки водителя (№65): последней из отметок точек
       визита, сделанных не раньше прибытия и не позже отъезда + actuals.DELIVERY_SLACK (общая стоянка — до последнего
       «закончил»); отметки нет, она до прибытия (отметил заранее — «закончил» не здесь) или после отъезда — до движения;
    2) вычитается ожидание открытия окна приёма (начало окна позже прибытия — машина ждёт: это не разгрузка);
    3) остаток вне [0,5; UNLOAD_MAX_MIN] не учитывается (дальше fit_unload отсекает дольше UNLOAD_CAP_REL × нормы).
    Хвост — до отсечений: они судят о времени магазина, а не о стоянке с обедом после отметки (2 ч стоянки с отметкой на
    12-й минуте — наблюдение 22 мин, а не «не разгрузка»). Ожидание окна — в начале стоянки, хвост — в конце; отметка
    за TAP_TAIL и раньше до открытия окна оставляет ≤ 0 — не учитывается. Факт визита (Visit.leave: участки, км, «план — факт»,
    опоздания) не меняется — только наблюдение разгрузки.
    skip — магазины, после разгрузки которых по плану обед (№61, lunch_customers): их визит не идёт вовсе (обед —
    в его стоянке; и без отметки «закончил», и с ней)."""
    by_key = {s.key: s for s in stops}
    out = []
    for v in actual.visits:
        ss = [by_key[k] for k in v.keys]
        if v.repeat or any(s.delivered_kg is None for s in ss) or any(s.customer_id in skip for s in ss):
            continue
        taps = [s.delivered_at for s in ss if s.delivered_at is not None
                and v.arrive <= s.delivered_at <= v.leave + ac.DELIVERY_SLACK]
        end = min(v.leave, max(taps) + TAP_TAIL) if taps else v.leave
        opens = max((s.window[0] for s in ss if s.window is not None and math.isfinite(s.window[0])), default=None)
        wait = max(0.0, opens - ac.day_minutes(day, v.arrive)) if opens is not None else 0.0
        minutes = (end - v.arrive).total_seconds() / 60.0 - wait
        if not 0.5 <= minutes <= UNLOAD_MAX_MIN:
            continue
        out.append(UnloadObs(day, len(ss), math.fsum(s.delivered_kg for s in ss) / 1000.0, minutes,   # type: ignore[misc]
                             tuple(sorted(s.customer_id for s in ss if s.customer_id is not None))))
    return out


def load_obs(day: date, actual: ac.DayActual, stops: Sequence[ac.PlanStop] = (),
             plan: Sequence[PlanTrip] = ()) -> list[LoadObs]:
    """Стоянка на складе перед рейсом (видно прибытие, не дольше actuals.MAX_LOAD_MIN и не короче LOAD_MIN_STAY — проезд
    через склад) без собственного ожидания плана (planned_wait по плановому рейсу _planned_trip); после вычета — тоже
    не короче LOAD_MIN_STAY. Рейс, перед загрузкой которого по плану обед на складе, не учитывается (обед — в стоянке)."""
    by_key = {s.key: s for s in stops}
    served = dict(actual.served)
    out = []
    for n, t in enumerate(actual.trips):
        arrive = t.arrive_depot
        if arrive is None or t.depart is None or t.loaded_kg <= 0 or t.load_min < LOAD_MIN_STAY:   # type: ignore[operator]
            continue
        cids = {by_key[k].customer_id for k, i in served.items() if i in t.visits and k in by_key
                and by_key[k].customer_id is not None}
        trip = _planned_trip(plan, n, cids, t.depart)
        if trip is not None and trip.lunch is not None and trip.lunch.where == 'depot':
            continue
        minutes = t.load_min - planned_wait(trip, arrive, t.depart)   # type: ignore[operator]
        if minutes >= LOAD_MIN_STAY:
            out.append(LoadObs(day, t.loaded_kg / 1000.0, minutes))
    return out


def leg_obs(day: date, actual: ac.DayActual, norms: Any) -> list[LegObs]:
    """Чистые участки → факт, модель без поправок по часам и действующий прогноз (norms.traffic — действующий профиль
    часов; без него — модель). Модель — как в расчёте «Развоза»: км / скорость участка (Norms.leg_speed — время
    Valhalla, когда грузовики считаются по нему; иначе скорость зоны)."""
    out = []
    for g in actual.legs:
        if not g.clean or g.minutes <= 0:
            continue
        km = norms.km(g.pa, g.pb)
        if km < LEG_MIN_KM:
            continue
        city = in_city(g.pa, norms.city_center, norms.city_radius_km) and in_city(g.pb, norms.city_center,
                                                                                    norms.city_radius_km)
        speed = float(norms.leg_speed(g.pa, g.pb, km, city))
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


def truck_time_obs(day: date, actual: ac.DayActual, norms: Any) -> tuple[list[tuple[LegObs, LegObs]], int]:
    """Чистые участки дня → пары наблюдений (прежняя модель, Valhalla) одного и того же участка для fit_truck_time и
    число участков без минут Valhalla (пара точек не посчитана — участок не идёт ни в одну модель). Минуты —
    valhalla_engine.truck_leg_minutes по нормам грузовиков norms без выученного профиля (срез Valhalla): model и speed —
    без часового профиля (по ним учится поправка, как в fit_travel), current — с часовым профилем norms (модель «как
    есть»). Участок берётся, если «факт / модель» в LEG_RATIO_OUTLIER у обеих моделей: отбор не зависит от того, какая
    из них точнее."""
    flat = replace(norms, traffic=None)
    pairs: list[tuple[LegObs, LegObs]] = []
    missing = 0
    for g in actual.legs:
        if not g.clean or g.minutes <= 0:
            continue
        local = g.depart.astimezone(ac.YEREVAN)
        start, weekday = ac.local_minutes(g.depart), local.weekday()
        raw = valhalla_engine.truck_leg_minutes(flat, g.pa, g.pb, start, weekday)
        if raw.km < LEG_MIN_KM:
            continue
        if raw.valhalla is None:
            missing += 1
            continue
        if not all(LEG_RATIO_OUTLIER[0] <= g.minutes / m <= LEG_RATIO_OUTLIER[1] for m in (raw.model, raw.valhalla)):
            continue
        now = valhalla_engine.truck_leg_minutes(norms, g.pa, g.pb, start, weekday) if norms.traffic is not None else raw
        city = in_city(g.pa, norms.city_center, norms.city_radius_km) and in_city(g.pb, norms.city_center,
                                                                                    norms.city_radius_km)
        model, valhalla = (LegObs(day, city, weekday >= 5, local.hour, g.minutes, m, cur, raw.km, raw.km / m * 60.0,
                                  weekday, start)
                           for m, cur in ((raw.model, now.model), (raw.valhalla, now.valhalla)))
        pairs.append((model, valhalla))
    return pairs, missing


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

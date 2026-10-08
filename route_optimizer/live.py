# -*- coding: utf-8 -*-
"""«Մեքենաները առցանց» — машины на карте в реальном времени (ответ владельца №76, docs/plans/live-map-plan.md).

Чистые функции — без Flask, БД и ERP: факт терминала машины за день (LiveFacts, раздел «Առաքիչ») + план «Развоза» +
нормы машины → карточка машины. Время — по Еревану; now — момент расчёта (у прошлого дня «сейчас» нет: без ETA и без
тревог «сейчас»).

Расчёты (№76, «≈» — оценка, не измерение):
- км сегодня — км по GPS тем же правилом, что «Առաքում այսօր» и «план — факт» (actuals.reconstruct: стоянки у точек дня
  и склада — 0 км, дрожание на месте км не добавляет);
- рейсы. Точка дня относится к рейсу плана, где есть её клиент (первое появление; нет в плане или плана нет — к
  первому рейсу). «Касание» закрытой точки — прибытие обслуживающего визита по GPS, без него — момент отметки доставки;
  открытой водителем (in_progress) — первого долгого заезда; ожидающей — засчитанного заезда (см. «GPS-визит точки»
  ниже: долгий, после выезда её рейса). Выезды
  со склада — концы стоянок на складе, после которых машина была вне склада, и начало трека, если он начался вне
  склада. Рейс уехал, если у его точек есть касание (выезд — последний выезд не позже первого касания, нет такого —
  начало трека) или, когда касаний нет, после последнего касания предыдущего рейса (у первого — когда угодно) был
  выезд (берётся последний такой). Рейсы — по порядку: не уехавший рейс останавливает счёт;
- груз на борту в момент t = Σ вес накладных уехавших рейсов (с момента их выезда) − Σ доставлено (вес × доля
  доставленного, №65) по точкам с моментом выгрузки до t (выгрузка — касание точки, не раньше выезда её рейса; доля
  известна без момента — с выезда) + Σ возвратов товара (событие return, вес по строкам накладных) с их момента −
  Σ выгруженного на складе. Недовезённое (отказ, частичная доставка) и возвраты остаются в машине до входа в зону
  склада (DEPOT_RADIUS_M) после последнего магазина рейса (возврата) — тогда считаются выгруженными (этап 2). Рейс
  «загружен» при выезде со склада после стоянки на нём (departed_trips): «загружено в этом рейсе» — вес накладных
  последнего уехавшего рейса. Не меньше 0. Остаток груза — груз сейчас; строки без веса — отдельным числом;
- топливо ≈ Σ по сегментам км дня (geo.track_steps по тем же точкам, что км) км × (пусто + (полно − пусто) × груз в
  конце сегмента / грузоподъёмность) / 100 — формула running_costs.route_cost; расход по загрузке не задан — расход
  машины (l/100), без загрузки; нет ни того, ни другого — неизвестно. Сверка с заправками (этап 2, refuel_check —
  «Նորմ և փաստ»): интервал «полный бак → полный бак» (learning.fuel_intervals) — залито фактически против км одометра ×
  норма машины / 100 (норма — середина «пустой — полный», нет — расход машины); заправка сегодня — расчёт с её момента;
- следующий магазин — первая незакрытая (pending/in_progress) точка с координатой последнего уехавшего рейса (нет
  уехавших — первого) по порядку плана (вне плана — по порядку терминала), затем следующих рейсов, затем пропущенные
  раньше. Точка, у которой GPS-визит уже кончился (unmarked — машина там была и уехала, водитель не отметил), — уже
  посещена: в следующий магазин и в очередь ETA не идёт, в «точки предыдущего рейса закрыты» (departed_trips) считается
  закрытой (её касание — прибытие визита — и так есть). ETA (этап 2, eta_plan) — путь по очереди оставшихся точек: участок «машина → магазин» (положение машины в
  таблицы дорог не кладётся — Road.legs(here=True) спрашивает движок отдельно) и дальше участки между магазинами и
  складом — дорожная модель «Развоза» (Road.legs: Valhalla или граф OSM, часовой профиль пробок, выученные поправки;
  нет — запасная по прямой × извилистость, и ETA помечен eta_source «model»); у каждого магазина — время №50/№60
  (Road.stay: введённое/выученное по GPS, иначе норма на точку + на тонну; у магазина, где машина уже стоит, — остаток);
  приехала раньше начала окна приёма магазина — ждёт его начала, потом разгрузка (как «Развоз»; прибытие — без ожидания);
  обед (№61), если ещё не был: после разгрузки не раньше начала окна, а если перегон кончается позже конца окна —
  в дороге; рейс ещё не уехал — через склад, загрузка (настройки; первый рейс вне сезона утренней погрузки, №78, — без
  неё: загружен с вечера) и не раньше планового выезда. Машина в STOP_RADIUS_M от
  магазина — «на месте», ETA = сейчас. Опоздание = ETA − плановое ETA (прогноз сборки «Развоза»);
- возвращение на склад — ETA прихода на склад после оставшихся точек рейса, на котором машина сейчас (тот же расчёт);
- «не успеет» (№87, late_forecast) — по тем же ETA: магазин с окном приёма — позже конца окна, без окна — позже плана на
  late_nowin_min и больше; машина — возвращение после всех рейсов позже конца рабочего дня. Только сегодня, только от
  положения не старше LATE_FIX_MAX; в журнале тревог — вид late (активен, пока прогноз такой; состояние машины не меняет);
- прогноз (ETA следующего и каждого магазина, опоздание, возвращение, «не успеет») — только при свежей связи (forecast):
  последняя связь (позже из last_contact и последней точки GPS — data_until) не старше no_contact_min у APK с device и
  OLD_APK_SILENT_MIN у старого APK, т.е. не «կապ չկա», и GPS терминала не выключен (device.gps off / no_permission —
  положение неизвестно). Стоящая машина со старой точкой, но свежей связью — прогноз есть. Прогноза нет (нет связи, GPS
  выключен, точки GPS ещё нет) — ETA «как будто машина ещё там, где её видели» вводит в заблуждение: следующий магазин
  называется с плановым ETA, eta/delay_min/eta_source — None, eta_unknown — true, here — false (где машина сейчас,
  неизвестно); ETA точек и возвращения — None, late — пусто. Шкала хода дня «Развоза» (views._progress_fill) берёт ETA и
  «на месте» из той же карточки: у такой машины и у unmarked-точек кружок — «ожидает» без времени;
- груз по плану (planned_kg) — вес накладных рейса, который машина повезёт следующим: первый рейс с точками после
  последнего уехавшего (ничего не уехало — первый рейс с точками); все уехали — None. До выезда карточка показывает его
  вместо «0 кг»;
- GPS-визит точки (gps) — из всех заездов actuals к ней (визиты с точкой в keys: повторный заезд, общее место двух
  магазинов, стоянка, разорванная дрожанием скорости, — каждый отдельно). Считаются только заезды не раньше выезда её
  рейса (стоянка у магазина следующего рейса по пути — не посещение) и не короче GPS_VISIT_MIN, кроме идущего сейчас.
  Рейсы — по порядку: заезды ожидающей точки засчитываются, когда её рейс уехал (departed_trips по отметкам водителя,
  точкам in_progress и уже засчитанным заездам прежних рейсов), не раньше первого выезда со склада после последнего
  касания прежних рейсов; засчитанный заезд — её касание. Показывается идущий сейчас заезд (сегодня: стоянка до
  последней точки трека; отъезд — None), иначе первый. Закрытая точка — обслуживающий визит (нет — первый заезд) без
  порога и без рейса: доставка отмечена, визит — факт. here («на месте», то же правило, что next.here и «стоит у
  магазина» в ETA) — есть прогноз (без связи «տեղում է» — не «сейчас»), последняя точка в STOP_RADIUS_M точки и её
  последний заезд идёт или кончился не раньше JITTER_BREAK до последней точки (стоянку оборвало дрожание скорости —
  машина там же); тогда показывается этот заезд, отъезд — None, минуты — от его прибытия до сейчас.
  unmarked — точка не закрыта водителем (pending/in_progress), заезд был, ни один не идёт сейчас и последняя точка
  сегодня не в STOP_RADIUS_M точки: «GPS-ով այցելած, տերմինալում չնշված». Касание незакрытой точки (рейсы, груз) — тоже
  только такой долгий заезд. Вес unmarked-точки остаётся в грузе и в расходе топлива (сколько отдали — неизвестно, пока
  водитель не отметит). В сводке магазинов — gps_visited (точек с таким заездом) и unmarked;
- воспроизведение дня: track_t — момент (секунды эпохи, UTC) каждой точки линии track, 1:1 с ней (те же упрощённые точки);

Тревоги (пороги — в настройках «Маршрутов», №76):
- скорость: скорость терминала > live_speed_kmh подряд не меньше live_speed_sec (от первой до последней точки подряд;
  перерыв трека дольше SPEED_GAP или точка без скорости прерывают);
- стоянка не по плану (actuals: ≥ 5 мин в 50 м вне склада и вне STOP_RADIUS_M точек дня) дольше live_stop_min — после
  первого выезда и до закрытия дня. Обед: самая длинная такая стоянка, начавшаяся в окне начала обеда
  (truck_lunch_from…truck_lunch_to), — тревога, только если длиннее обеда (truck_lunch_min) + live_stop_min;
- нет связи: «день открыт» (с первой связи за день до day_closed или до возвращения на склад со всеми закрытыми
  точками) и с последней связи прошло больше live_no_contact_min; в журнале — и перерывы между получениями событий;
- GPS выключен или нет разрешения (track.device, APK 2.2.0): от такого состояния до следующего «on»;
- машина без права въезда в малый центр (настройки машины) — в его границе: подряд не меньше CENTER_MIN_POINTS точек.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Collection, Mapping, Protocol, Sequence

from . import actuals as ac
from .geo import Fix, Point, haversine_km, in_city, in_polygon, track_steps
from .learning import _hhmm, effective_refuels, fuel_intervals, track_fixes
from .store import DEFAULT_SETTINGS, LIVE_ALERT_KINDS

YEREVAN = ac.YEREVAN
DONE = ('full', 'partial', 'refused', 'covered')   # точка закрыта водителем
OPEN = ('pending', 'in_progress')
STALE_S = 120                       # последняя точка старше — «давно» (серая)
MOVING_MS = ac.STOP_MS              # скорость терминала не ниже — машина едет (как стоянка у магазина, №60)
SPEED_GAP = timedelta(seconds=60)   # перерыв трека дольше — превышение скорости прерывается
CENTER_MIN_POINTS = 2               # в малом центре — не меньше 2 точек подряд (одна — может быть погрешность GPS)
NO_CONTACT_END_H = 20               # APK останавливает запись в 20:00 — после этого «нет связи» не тревога (№76, ревью)
OLD_APK_SILENT_MIN = 20              # старый APK (без device) шлёт пачками раз в 2–17 мин: «կապ չկա» — после 20 мин
NO_CONTACT_MAX = timedelta(hours=3)  # связи нет дольше — машина закончила день: состояние «կապ չկա», без тревоги
# «не успеет» (№87) — только от свежего положения: старый APK шлёт пачками до 20 мин; давнее — прогноз от места, где машины
# уже нет (телефон выключен), и ложные тревоги
LATE_FIX_MAX = timedelta(minutes=OLD_APK_SILENT_MIN)
# заезд к незакрытой точке короче — не посещение (пробка, светофор у магазина): как стоянка «не по плану» actuals; разгрузка
# по нормам — от 8 мин на точку
GPS_VISIT_MIN = ac.OTHER_DWELL.total_seconds() / 60.0
TRACK_LINE_POINTS = 1500            # линия трека на карте (actuals.simplify)



class LiveFacts(Protocol):
    """Факт терминалов за день (courier.live.LiveSource; подключает app_v2 — attach_live_facts)."""

    def fleet(self, day: str) -> dict[str, dict[str, Any]]:
        """Машина → stops, track, drivers, last_contact, contacts, device, devices, closed_at (см. LiveSource.fleet)."""
        ...


@dataclass(frozen=True)
class Rules:
    """Пороги тревог (по умолчанию — ответ владельца №76: «Да, так»), обед (№61), малый центр, нормы разгрузки."""
    speed_kmh: float = 90.0
    speed_sec: float = 30.0
    stop_min: float = 15.0
    no_contact_min: float = 5.0
    lunch_min: float = 30.0
    lunch_window: tuple[float, float] = (750.0, 870.0)   # начало обеда, минуты от полуночи (12:30–14:30)
    center_zone: tuple[Point, ...] = ()
    unload_min_per_stop: float = 8.0
    unload_min_per_tonne: float = 6.0
    load_min: float = 0.0                # загрузка на складе перед рейсом: фиксированные минуты и на тонну (настройки)
    load_min_per_tonne: float = 0.0
    # тревоги в Telegram (live_alerts): какие виды слать, тихие часы (минуты от полуночи: с — до; None — нет), повтор
    alert_kinds: tuple[str, ...] = LIVE_ALERT_KINDS
    quiet: tuple[float, float] | None = (1200.0, 480.0)
    repeat_min: float = 30.0
    # «не успеет» (№87): магазин без окна — прогноз позже плана не меньше чем на late_nowin_min; машина — возврат на склад
    # позже work_end (конец рабочего дня машины, минуты от полуночи; принята переработка №32 — её предел, views)
    late_nowin_min: float = 30.0
    work_end: float = 1080.0

    @classmethod
    def from_settings(cls, s: Mapping[str, Any]) -> Rules:
        def hm(key: str, default: float) -> float:
            m = _hhmm(s.get(key))
            return float(m) if m is not None else default

        def num(key: str) -> float:
            v = s.get(key)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float(DEFAULT_SETTINGS[key])
        quiet = (hm('live_quiet_from', 1200.0), hm('live_quiet_to', 480.0))

        def opt(key: str) -> float:   # необязательная (null — «ещё не известно») норма — 0
            v = s.get(key)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
        return cls(num('live_speed_kmh'), num('live_speed_sec'), num('live_stop_min'), num('live_no_contact_min'),
                   num('truck_lunch_min'), (hm('truck_lunch_from', 750.0), hm('truck_lunch_to', 870.0)),
                   tuple((float(p[0]), float(p[1])) for p in s.get('center_zone') or ()),
                   num('unload_min_per_stop'), num('unload_min_per_tonne'),
                   opt('warehouse_load_fixed_min'), opt('warehouse_load_min_per_tonne'),
                   tuple(k for k in LIVE_ALERT_KINDS if k in (s.get('live_alert_kinds', LIVE_ALERT_KINDS))),
                   quiet if quiet[0] != quiet[1] else None, num('live_repeat_min'),
                   num('late_nowin_min'), hm('truck_work_end', 1080.0))


@dataclass(frozen=True)
class TruckSpec:
    """Нормы машины: грузоподъёмность, расход (л/100 км) пустой/полной (running_costs) и общий, право въезда в центр
    (None — неизвестно: тревоги «центр» нет)."""
    capacity_kg: float | None = None
    l100: float | None = None
    empty_l100: float | None = None
    full_l100: float | None = None
    center_ok: bool | None = None
    # магазины плана этой машины, куда она въезжает в малый центр по правилу магазина (№78): у них тревоги «центр» нет
    center_customers: frozenset[int] = frozenset()

    @property
    def norm_l100(self) -> float | None:
        """Норма для сверки с заправками — как «Նորմ և փաստ» (views._fuel_norms): середина «пустой — полный», нет —
        расход машины."""
        if self.empty_l100 is not None and self.full_l100 is not None:
            return (self.empty_l100 + self.full_l100) / 2
        return self.empty_l100 if self.empty_l100 is not None else self.l100

    def rate(self, load_kg: float) -> float | None:
        """Л/100 км при грузе load_kg — как running_costs.route_cost: base + (full − base) × груз / грузоподъёмность;
        расход по загрузке не задан — l100; нормы нет — None."""
        if self.empty_l100 is None:
            return self.l100
        slope = (self.full_l100 - self.empty_l100) if self.full_l100 is not None else 0.0
        if slope and self.capacity_kg and self.capacity_kg > 0:
            return self.empty_l100 + slope * load_kg / self.capacity_kg
        return self.empty_l100


@dataclass(frozen=True)
class Road:
    """Модель пути: по прямой × извилистость, скорость города (оба конца в радиусе города) или области — запасная.
    Дорожная модель «Развоза» (legs — Valhalla или граф OSM с часовым профилем пробок и выученными нормами) и время у
    магазина (unload — введённое, выученное по GPS, иначе норма №50/№60) подключает views._live_road; нет их (карты нет,
    движок не готов) — запасная модель и нормы настроек."""
    detour: float = 1.3
    city_kmh: float = 25.0
    region_kmh: float = 45.0
    center: Point = (40.1792, 44.4991)
    radius_km: float = 12.0
    # (a, b, минута суток выезда, a — положение машины сейчас) → минуты или None (пары нет); положение машины в таблицы
    # дорог не кладут (меняется каждые 30 с), его участок считается отдельно
    legs: Callable[[Point, Point, float, bool], float | None] | None = field(default=None, compare=False, repr=False)
    unload: Callable[[Point, float], float] | None = field(default=None, compare=False, repr=False)   # (точка, кг) → мин

    def minutes(self, a: Point, b: Point) -> float:
        km = haversine_km(a, b) * self.detour
        city = in_city(a, self.center, self.radius_km) and in_city(b, self.center, self.radius_km)
        return km / (self.city_kmh if city else self.region_kmh) * 60.0

    def leg(self, a: Point, b: Point, depart_min: float, here: bool = False) -> tuple[float, bool]:
        """(минуты, по дорожной модели?): дорожная модель, нет — запасная."""
        if self.legs is not None:
            m = self.legs(a, b, depart_min, here)
            if m is not None:
                return m, True
        return self.minutes(a, b), False

    def stay(self, p: Point, kg: float, rules: Rules) -> float:
        """Минуты у магазина p с грузом kg (№50/№60), без обеда."""
        if self.unload is not None:
            return self.unload(p, kg)
        return rules.unload_min_per_stop + rules.unload_min_per_tonne * kg / 1000.0


@dataclass(frozen=True)
class PlanTrip:
    """Рейс плана машины: клиенты по порядку объезда, плановые ETA клиентов, плановый выезд."""
    customers: tuple[int, ...]
    etas: Mapping[int, datetime]
    depart: datetime | None = None
    preloaded: bool = False   # №78: загружен с вечера (вне сезона первый рейс машины, не рейс заказов дня) — без загрузки


def plan_trips(draft_trips: Sequence[Sequence[int]], prediction: Mapping[str, Any] | None, day: date,
               preloaded: bool = False) -> list[PlanTrip]:
    """Рейсы машины из черновика «Развоза» (клиенты по порядку) и прогноза сборки (prediction['trucks'][машина]:
    trips — depart «HH:MM», stops — [[клиент, ETA]]). ETA клиента — из прогноза (первое появление); выезд рейса — из
    прогноза, если число рейсов то же (логист не менял рейсы после сборки), иначе неизвестен. preloaded — первый рейс
    загружен с вечера (№78, правило — dispatch._timeline)."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=YEREVAN)

    def at(text: Any) -> datetime | None:
        m = _hhmm(text)
        return midnight + timedelta(minutes=m) if m is not None else None
    pred = [t for t in (prediction or {}).get('trips') or () if isinstance(t, Mapping)]
    etas: dict[int, datetime] = {}
    for t in pred:
        for c in t.get('stops') or ():
            if isinstance(c, list) and len(c) == 2 and isinstance(c[0], int) and c[0] not in etas \
                    and (e := at(c[1])) is not None:
                etas[c[0]] = e
    same = len(pred) == len(draft_trips)
    return [PlanTrip(tuple(stops), {c: etas[c] for c in stops if c in etas}, at(pred[j].get('depart')) if same else None,
                     preloaded and j == 0)
            for j, stops in enumerate(draft_trips)]


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(YEREVAN).isoformat(timespec='seconds') if t is not None else None


def _moment(raw: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    return t if t is not None and t.utcoffset() is not None else None


def _near(a: Point, b: Point | None, radius_m: float) -> bool:
    return b is not None and haversine_km(a, b) * 1000.0 <= radius_m


# --- рейсы и груз ---

def departures(pts: Sequence[Fix], actual: ac.DayActual, depot: Point | None) -> list[datetime]:
    """Выезды со склада по порядку: конец стоянки на складе, после которой есть точки (машина уехала), и начало трека,
    если он начался вне склада (склада нет — начало трека)."""
    if not pts:
        return []
    out = [s.leave for s in actual.stays if s.kind == 'depot' and s.leave < pts[-1].at]
    if depot is None or not _near(pts[0].point, depot, ac.DEPOT_RADIUS_M):
        out.append(pts[0].at)
    return sorted(set(out))


def trip_of(stops: Sequence[Mapping[str, Any]], plan: Sequence[PlanTrip]) -> dict[str, int]:
    """Точка → номер рейса плана (по клиенту, первое появление); вне плана и без плана — 0."""
    first: dict[int, int] = {}
    for j, t in enumerate(plan):
        for c in t.customers:
            first.setdefault(c, j)
    return {s['stop_id']: first.get(s.get('customer_id'), 0) for s in stops}   # type: ignore[arg-type]


def departed_trips(trips: Mapping[str, int], touches: Mapping[str, datetime], deps: Sequence[datetime],
                   start: datetime | None, open_stops: Collection[str] = ()) -> dict[int, datetime]:
    """Уехавшие рейсы → момент выезда (правило — в описании модуля). trips — точка → рейс, touches — точка → касание,
    deps — выезды по порядку, start — начало трека, open_stops — незакрытые точки. Рейс без касаний уехал, только если
    все точки предыдущего рейса закрыты (заезд на склад посреди рейса — не новый рейс) и позже рейса с касаниями нет
    (машина уже на другом рейсе — этот пропущен)."""
    n = max(trips.values(), default=-1) + 1
    touched: dict[int, list[datetime]] = {}
    for sid, k in trips.items():
        if sid in touches:
            touched.setdefault(k, []).append(touches[sid])
    last_touched = max(touched, default=-1)
    out: dict[int, datetime] = {}
    prev_last: datetime | None = None
    for k in range(n):
        if k in touched:
            first = min(touched[k])
            before = [d for d in deps if d <= first]
            out[k] = before[-1] if before else min(start or first, first)
            prev_last = max(touched[k])
            continue
        if k < last_touched:
            continue
        if k > 0 and any(trips[sid] == k - 1 for sid in open_stops if sid in trips):
            break
        after = [d for d in deps if prev_last is None or d > prev_last]
        if not after:
            break
        out[k] = after[-1]
        prev_last = after[-1]
    return out


@dataclass(frozen=True)
class Load:
    events: tuple[tuple[datetime, float], ...]   # (момент, ± кг) по возрастанию момента
    loaded_kg: float          # вес накладных уехавших рейсов
    delivered_kg: float       # доставлено по ним
    unweighed: int            # строк без веса в точках уехавших рейсов, ещё не доставленных полностью
    returned_kg: float = 0.0       # принято возвратов (все)
    unloaded_kg: float = 0.0       # выгружено на складе: недовезённое и возвраты
    trip_kg: float = 0.0           # загружено в последнем уехавшем рейсе
    refused_kg: float = 0.0        # недовезённое закрытых точек, ещё в машине
    returns_aboard_kg: float = 0.0  # возвраты, ещё не сданные на склад
    returns_unweighed: int = 0     # возвратов без известного веса

    def at(self, t: datetime) -> float:
        """Груз на борту в момент t (события строго до t), не меньше 0."""
        k = bisect.bisect_left([e[0] for e in self.events], t)
        return max(0.0, math.fsum(d for _, d in self.events[:k]))

    @property
    def remaining_kg(self) -> float:
        return max(0.0, math.fsum(d for _, d in self.events))


def depot_entries(pts: Sequence[Fix], depot: Point | None) -> list[datetime]:
    """Моменты входа в зону склада: первая точка трека в DEPOT_RADIUS_M после точки вне зоны (и первая точка, если трек
    начался в зоне). Нет склада — пусто."""
    if depot is None:
        return []
    out: list[datetime] = []
    inside = False
    for f in pts:
        now = _near(f.point, depot, ac.DEPOT_RADIUS_M)
        if now and not inside:
            out.append(f.at)
        inside = now
    return out


def _entry_after(entries: Sequence[datetime], t: datetime) -> datetime | None:
    """Первый вход в зону склада строго после t."""
    k = bisect.bisect_right(entries, t)
    return entries[k] if k < len(entries) else None


def load_of(stops: Sequence[Mapping[str, Any]], trips: Mapping[str, int], gone: Mapping[int, datetime],
            touches: Mapping[str, datetime], returns: Sequence[Mapping[str, Any]] = (),
            entries: Sequence[datetime] = ()) -> Load:
    """Груз по уехавшим рейсам gone (рейс → выезд): + вес рейса в момент выезда, − доставлено в момент выгрузки точки.
    Недовезённое закрытого рейса (все его точки закрыты) — − в момент первого входа в зону склада (entries) после его
    последнего касания; возврат (returns: at, kg) — + в его момент и − при первом входе в зону склада после него.
    Нет входа — груз остаётся в машине. Точка, посещённая по GPS, но не отмеченная (unmarked), — её вес тоже в машине
    (и в расходе топлива): сколько отдали, неизвестно, пока водитель не отметит."""
    events: list[tuple[datetime, float]] = []
    loaded = delivered = unloaded = returned = refused = 0.0
    unweighed = 0
    per_trip: dict[int, list[float]] = {}   # рейс → [вес, доставлено, недовезённое закрытых точек]
    closed = {k: True for k in gone}
    for s in stops:
        k = trips[s['stop_id']]
        if k not in gone:
            continue
        kg = float(s.get('weight_kg') or 0.0)
        loaded += kg
        events.append((gone[k], kg))
        share = s.get('share')
        if share is None or share < 1.0:
            unweighed += int(s.get('unweighed') or 0)
        done = kg * min(1.0, float(share)) if share is not None and share > 0 else 0.0
        unknown = False
        if done > 0:
            delivered += done
            events.append((max(touches.get(s['stop_id'], gone[k]), gone[k]), -done))
        elif share is None and s.get('status') not in OPEN:
            # закрыта, а сколько отдали — неизвестно (неизвестное ≠ 0): не считаем весь вес точки лежащим в машине
            events.append((max(touches.get(s['stop_id'], gone[k]), gone[k]), -kg))
            unknown = True
        if s.get('status') in OPEN:
            closed[k] = False
        acc = per_trip.setdefault(k, [0.0, 0.0, 0.0])
        acc[0] += kg
        acc[1] += kg if unknown else done
        if s.get('status') not in OPEN and not unknown:
            acc[2] += kg - done
    touched: dict[int, datetime] = {}
    for s in stops:
        k = trips[s['stop_id']]
        if k in gone and s['stop_id'] in touches:
            touched[k] = max(touched.get(k, touches[s['stop_id']]), touches[s['stop_id']])
    for k, (kg, done, left) in per_trip.items():
        if left <= 0:
            continue
        back = _entry_after(entries, touched[k]) if closed[k] and k in touched else None
        if back is not None:
            events.append((back, -left))
            unloaded += left
        else:
            refused += left
    aboard = 0.0
    no_weight = 0
    for r in returns:
        at, kg = _moment(r.get('at')), r.get('kg')
        if at is None:
            continue
        if not isinstance(kg, (int, float)) or kg <= 0:
            no_weight += 1
            continue
        returned += kg
        events.append((at, float(kg)))
        back = _entry_after(entries, at)
        if back is not None:
            events.append((back, -float(kg)))
            unloaded += kg
        else:
            aboard += kg
    events.sort(key=lambda e: e[0])
    last = max(gone, default=None)
    trip_kg = per_trip[last][0] if last in per_trip else 0.0
    return Load(tuple(events), loaded, delivered, unweighed, returned, unloaded, trip_kg, refused, aboard, no_weight)


def fuel_liters(moving: Sequence[Fix], load: Load, truck: TruckSpec) -> float | None:
    """Топливо ≈ Σ сегментов км дня км × расход при грузе в конце сегмента / 100; нормы нет — None."""
    if truck.rate(0.0) is None:
        return None
    total = onboard = 0.0
    i = 0
    for f, km in track_steps(moving):   # сегменты — по времени: груз сдвигается вместе с ними
        while i < len(load.events) and load.events[i][0] < f.at:
            onboard += load.events[i][1]
            i += 1
        total += km * truck.rate(max(0.0, onboard)) / 100.0   # type: ignore[operator]
    return total


def refuel_check(refuels: Sequence[Mapping[str, Any]], truck: TruckSpec, moving: Sequence[Fix], load: Load,
                 day: date) -> dict[str, Any] | None:
    """Сверка расчёта топлива с заправками машины (refuels — Store.refuels одной машины): последняя заправка; последний
    интервал «полный бак → полный бак» — залито фактически против км одометра × норма / 100 (calc_l, delta_pct — на
    сколько % факт выше расчёта); заправка сегодня — расчёт с её момента (since_l). Заправок нет — None."""
    items = next(iter(effective_refuels(refuels).values()), [])
    if not items:
        return None
    at, _, p = items[-1]
    lit = p.get('liters')
    out: dict[str, Any] = {
        'last': {'at': _iso(at), 'liters': float(lit) if isinstance(lit, (int, float)) and not isinstance(lit, bool)
                 else None, 'full': p.get('full_tank', True) is not False,
                 'today': at.astimezone(YEREVAN).date() == day},
        'interval': None, 'since_l': None}
    ivs = fuel_intervals(refuels)
    if ivs:
        iv, norm = ivs[-1], truck.norm_l100
        calc = iv.km * norm / 100.0 if norm is not None else None
        out['interval'] = {'from': _iso(iv.start), 'to': _iso(iv.end), 'km': round(iv.km), 'liters': round(iv.liters, 1),
                           'calc_l': round(calc, 1) if calc is not None else None,
                           'delta_pct': round((iv.liters / calc - 1.0) * 100.0, 1) if calc else None}
    if out['last']['today']:
        since = fuel_liters([f for f in moving if f.at >= at], load, truck)
        out['since_l'] = round(since, 1) if since is not None else None
    return out


# --- тревоги ---

def _alert(kind: str, start: datetime, end: datetime | None, active: bool, **extra: Any) -> dict[str, Any]:
    return {'kind': kind, 'from': _iso(start), 'to': _iso(end), 'active': active, **extra}


def speed_alerts(pts: Sequence[ac.TrackFix], rules: Rules, live: bool) -> list[dict[str, Any]]:
    """Превышения: подряд точки со скоростью терминала > speed_kmh, от первой до последней не меньше speed_sec."""
    out: list[dict[str, Any]] = []
    run: list[ac.TrackFix] = []

    def close(is_last: bool) -> None:
        if run and (run[-1].at - run[0].at).total_seconds() >= rules.speed_sec:
            top = max(run, key=lambda f: f.spd or 0.0)
            out.append(_alert('speed', run[0].at, run[-1].at, live and is_last, max_kmh=round((top.spd or 0) * 3.6),
                              lat=top.lat, lon=top.lon))
    for f in pts:
        fast = f.spd is not None and f.spd * 3.6 > rules.speed_kmh
        if run and (not fast or f.at - run[-1].at > SPEED_GAP):
            close(False)
            run = []
        if fast:
            run.append(f)
    close(True)
    return out


def stop_alerts(actual: ac.DayActual, day: date, rules: Rules, since: datetime | None, until: datetime | None,
                last_at: datetime | None, live: bool) -> list[dict[str, Any]]:
    """Стоянки не по плану длиннее stop_min после первого выезда (since) и до закрытия дня (until); обед — самая
    длинная из начавшихся в окне обеда: тревога, только если длиннее обеда + stop_min."""
    if since is None:
        return []
    stays = [s for s in actual.stays if s.kind == 'other' and s.arrive >= since and (until is None or s.arrive < until)]
    lo, hi = rules.lunch_window
    window = [s for s in stays if lo <= ac.day_minutes(day, s.arrive) <= hi]
    lunch = max(window, key=lambda s: (s.minutes, s.arrive)) if window and rules.lunch_min > 0 else None
    out = []
    for s in stays:
        limit = rules.stop_min + (rules.lunch_min if s is lunch else 0.0)
        if s.minutes > limit:
            ongoing = live and last_at is not None and s.leave >= last_at
            out.append(_alert('stop', s.arrive, None if ongoing else s.leave, ongoing, minutes=round(s.minutes),
                              lunch=s is lunch, lat=s.center[0] if s.center else None,
                              lon=s.center[1] if s.center else None))
    return out


def contact_alerts(contacts: Sequence[datetime], last_contact: datetime | None, now: datetime | None,
                   start: datetime | None, end: datetime | None, rules: Rules) -> list[dict[str, Any]]:
    """Нет связи: перерывы между получениями событий длиннее no_contact_min в открытом дне [start, end]; now (день
    открыт сейчас) — и с последней связи до сейчас, но не после 20:00 по Еревану и не дольше NO_CONTACT_MAX."""
    if start is None:
        return []
    limit = timedelta(minutes=rules.no_contact_min)
    inside = sorted(t for t in contacts if t >= start and (end is None or t <= end))
    out = [_alert('no_contact', a, b, False, minutes=round((b - a).total_seconds() / 60))
           for a, b in zip(inside, inside[1:]) if b - a > limit]
    if (now is not None and last_contact is not None and limit < now - last_contact <= NO_CONTACT_MAX
            and now.astimezone(YEREVAN).hour < NO_CONTACT_END_H):
        out.append(_alert('no_contact', last_contact, None, True,
                          minutes=round((now - last_contact).total_seconds() / 60)))
    return out


def gps_alerts(devices: Sequence[tuple[datetime, Any]], live: bool) -> list[dict[str, Any]]:
    """GPS выключен / нет разрешения (track.device): от такого состояния до следующего «on»; не закончилось — сейчас."""
    out: list[dict[str, Any]] = []
    cur: tuple[datetime, str] | None = None
    for at, gps in devices:
        if gps in ('off', 'no_permission') and cur is None:
            cur = (at, gps)
        elif gps == 'on' and cur is not None:
            out.append(_alert('gps', cur[0], at, False, gps=cur[1]))
            cur = None
    if cur is not None:
        out.append(_alert('gps', cur[0], None, live, gps=cur[1]))
    return out


def center_alerts(pts: Sequence[Fix], rules: Rules, truck: TruckSpec, live: bool,
                  stops: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Машина без права въезда (center_ok False) в границе малого центра: подряд не меньше CENTER_MIN_POINTS точек. Заезд,
    в котором машина была у магазина с правилом «в центр машинам допуска» (№78, truck.center_customers; в STOP_RADIUS_M его
    точки), — по правилу, без тревоги: заезд — все точки подряд в зоне, т.е. и подъезд по центру к магазину, и выезд из
    него; другие заезды в центр в тот же день — тревога."""
    if truck.center_ok is not False or len(rules.center_zone) < 3:
        return []
    ok = [(s['lat'], s['lon']) for s in stops if s.get('customer_id') in truck.center_customers
          and s.get('lat') is not None and s.get('lon') is not None]
    out = []
    run: list[Fix] = []
    for i, f in enumerate(pts + [None]):   # type: ignore[operator]
        if f is not None and in_polygon(f.point, rules.center_zone):
            run.append(f)
            continue
        if ok and any(_near(x.point, p, ac.STOP_RADIUS_M) for x in run for p in ok):
            run = []
            continue
        if len(run) >= CENTER_MIN_POINTS:
            out.append(_alert('center', run[0].at, run[-1].at, live and f is None, lat=run[0].lat, lon=run[0].lon))
        run = []
    return out


# --- карточка машины ---

def _left(stops: Sequence[Mapping[str, Any]], visited: Collection[str]) -> list[Mapping[str, Any]]:
    """Оставшиеся точки: незакрытые, с координатой, без кончившегося GPS-визита (visited — unmarked)."""
    return [s for s in stops if s.get('status') in OPEN and s.get('lat') is not None and s.get('lon') is not None
            and s['stop_id'] not in visited]


def _next_stop(stops: Sequence[Mapping[str, Any]], trips: Mapping[str, int], plan: Sequence[PlanTrip],
               current: int, visited: Collection[str] = ()) -> Mapping[str, Any] | None:
    """Следующий магазин: незакрытая точка с координатой рейса current, затем следующих рейсов, затем прежних; внутри —
    in_progress, затем порядок плана (вне плана — порядок терминала). visited — точки с кончившимся GPS-визитом: уже
    посещены, не следующие."""
    pos = {c: i for t in plan for i, c in enumerate(t.customers)}
    left = _left(stops, visited)

    def key(s: Mapping[str, Any]) -> tuple[Any, ...]:
        k = trips[s['stop_id']]
        group = 0 if k == current else (1 if k > current else 2)
        return (group, k, s.get('status') != 'in_progress', pos.get(s.get('customer_id'), math.inf),
                s.get('seq') if isinstance(s.get('seq'), int) else math.inf, s['stop_id'])
    return min(left, key=key) if left else None


@dataclass(frozen=True)
class EtaPlan:
    """ETA по очереди: точка → (прибытие, участки до неё — все по дорожной модели), возвращение на склад после рейса, на
    котором машина сейчас (так же), и когда вставлен обед; end — возвращение на склад после всех оставшихся рейсов
    (конец дня машины, №87; склада нет — None)."""
    arrive: Mapping[str, tuple[datetime, bool]]
    back: tuple[datetime, bool] | None
    lunch_at: datetime | None = None
    end: datetime | None = None


def lunch_taken(actual: ac.DayActual, day: date, rules: Rules) -> bool:
    """Обед уже был: стоянка не по плану не короче половины обеда, начавшаяся в окне начала обеда ± час (обед могли
    взять раньше или позже — окно только правило сборки). Идущая сейчас стоянка считается так же — когда простояла не меньше
    половины обеда (короткая остановка в окне — ещё не обед). Обеда в настройках нет — считается взятым."""
    if rules.lunch_min <= 0:
        return True
    lo, hi = rules.lunch_window
    for s in actual.stays:
        if s.kind != 'other':
            continue
        m = ac.day_minutes(day, s.arrive)
        if s.minutes >= rules.lunch_min / 2 and lo - 60 <= m <= hi + 60:
            return True
    return False


def eta_plan(day: date, now: datetime, pos: Point, queue: Sequence[tuple[int, Sequence[Mapping[str, Any]]]],
             gone: Collection[int], current: int | None, plan: Sequence[PlanTrip], depot: Point | None,
             road: Road, rules: Rules, lunch_pending: bool, at_depot: bool,
             here: tuple[Mapping[str, Any], float] | None = None,
             windows: Mapping[int, tuple[float, float]] | None = None) -> EtaPlan:
    """Прибытия к оставшимся точкам по очереди queue (рейс, точки по порядку; рейс, где машина сейчас, — первым, даже
    пустой) и возвращение на склад после него — правило в описании модуля. here — (точка, минут уже стоит): разгрузка
    идёт, её ETA — сейчас, дальше — после остатка стоянки. windows — окна приёма клиентов (не раньше, не позже; минуты
    от полуночи): у следующих магазинов разгрузка — не раньше начала окна."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=YEREVAN)
    t, live_pos, by_road = now, True, True
    arrive: dict[str, tuple[datetime, bool]] = {}
    back: tuple[datetime, bool] | None = None
    lo, hi = rules.lunch_window
    pending, lunch_at = lunch_pending and ac.day_minutes(day, now) <= hi, None   # окно кончилось — обеда уже не будет
    left_at = ac.day_minutes(day, now)   # когда выехали на текущий участок

    def move(a: Point, b: Point) -> None:
        nonlocal t, live_pos, by_road, left_at
        left_at = ac.day_minutes(day, t)
        m, ok = road.leg(a, b, ac.day_minutes(day, t) % 1440.0, live_pos)
        by_road = by_road and ok
        live_pos = False
        t += timedelta(minutes=m)

    def rest(boundary: bool) -> None:
        """Обед: после разгрузки/на складе не раньше начала окна; в дороге — если прибытие позже конца окна."""
        nonlocal t, pending, lunch_at
        if not pending:
            return
        m = ac.day_minutes(day, t)
        if (lo <= m <= hi) if boundary else (m > hi and left_at <= hi):   # в дороге — только если выехали до конца окна
            lunch_at, pending = t, False
            t += timedelta(minutes=rules.lunch_min)

    if here is not None:
        s, stayed = here
        p = (s['lat'], s['lon'])
        arrive[s['stop_id']] = (now, True)
        stay = road.stay(p, float(s.get('weight_kg') or 0.0), rules)
        opens = (windows or {}).get(s.get('customer_id'), (-math.inf, math.inf))[0]
        if math.isfinite(opens) and midnight + timedelta(minutes=opens) > now:   # ждёт окна: разгрузка — с его начала
            t = midnight + timedelta(minutes=opens + stay)
        else:
            t = now + timedelta(minutes=max(0.0, stay - stayed))
        rest(True)
        pos, live_pos, at_depot = p, False, False
    for k, stops in queue:
        if k not in gone and depot is not None:   # рейс не уехал: на склад, загрузка, не раньше планового выезда
            if not at_depot and not _near(pos, depot, ac.DEPOT_RADIUS_M):
                move(pos, depot)
            pos, at_depot = depot, True
            kg = math.fsum(float(x.get('weight_kg') or 0.0) for x in stops)
            # №78: рейс, загруженный с вечера (вне сезона первый рейс машины), — без загрузки
            load = 0.0 if k < len(plan) and plan[k].preloaded else rules.load_min + rules.load_min_per_tonne * kg / 1000.0
            ready = t + timedelta(minutes=load)
            dep = plan[k].depart if k < len(plan) else None
            t = max(ready, dep) if dep is not None else ready
            rest(True)
        for x in stops:
            p = (x['lat'], x['lon'])
            move(pos, p)
            rest(False)
            arrive[x['stop_id']] = (t, by_road)
            opens = (windows or {}).get(x.get('customer_id'), (-math.inf, math.inf))[0]
            if math.isfinite(opens):   # окно ещё не открылось — ждёт (прибытие то же)
                t = max(t, midnight + timedelta(minutes=opens))
            t += timedelta(minutes=road.stay(p, float(x.get('weight_kg') or 0.0), rules))
            rest(True)
            pos, at_depot = p, False
        if depot is None:
            continue
        if not at_depot and not _near(pos, depot, ac.DEPOT_RADIUS_M):
            move(pos, depot)
        if k == current and back is None and not (at_depot and not stops):
            back = (t, by_road)
        pos, at_depot = depot, True
    return EtaPlan(arrive, back, lunch_at, t if depot is not None and queue else None)


def _queue(stops: Sequence[Mapping[str, Any]], trips: Mapping[str, int], plan: Sequence[PlanTrip], current: int,
           nxt: Mapping[str, Any] | None, departed: bool,
           visited: Collection[str] = ()) -> list[tuple[int, list[Mapping[str, Any]]]]:
    """Оставшиеся точки по очереди объезда: рейс, где машина сейчас (первым, даже без точек — домой), затем следующие
    рейсы, затем пропущенные раньше; внутри — следующий магазин (nxt), затем порядок плана (вне плана — терминала).
    visited — точки с кончившимся GPS-визитом: в очередь не идут."""
    pos = {c: i for t in plan for i, c in enumerate(t.customers)}
    left = _left(stops, visited)
    by_trip: dict[int, list[Mapping[str, Any]]] = {}
    for s in left:
        by_trip.setdefault(trips[s['stop_id']], []).append(s)
    for xs in by_trip.values():
        xs.sort(key=lambda s: (nxt is None or s['stop_id'] != nxt['stop_id'], pos.get(s.get('customer_id'), math.inf),
                               s.get('seq') if isinstance(s.get('seq'), int) else math.inf, s['stop_id']))
    first = [current] if departed or current in by_trip else []
    later = sorted((k for k in by_trip if k != current), key=lambda k: (k < current, k))
    return [(k, by_trip.get(k, [])) for k in [*first, *later]]


def late_forecast(day: date, stops: Sequence[Mapping[str, Any]], arrive: Mapping[str, tuple[datetime, bool]],
                  planned: Mapping[str, datetime], windows: Mapping[int, tuple[float, float]], end: datetime | None,
                  rules: Rules, here: str | None = None) -> list[dict[str, Any]]:
    """«Не успеет» (ответ владельца №87, п.2) по прогнозу eta_plan: arrive — точка → (прибытие, по дорогам), planned —
    точка → плановое ETA её клиента в её рейсе, windows — клиент → окно приёма (не раньше, не позже; минуты от полуночи,
    store.CustomerWindow.span), end — возвращение на склад после всех оставшихся рейсов, here — точка, у которой машина
    стоит (её прибытие — факт, не прогноз). Только ожидающие точки (pending) с прогнозом — in_progress водитель открывает
    у магазина, прибытие уже было:
    - магазин с окном — прибытие позже конца окна (как window_miss «Развоза»: раньше начала — машина ждёт, не
      опоздание); late_kind window, over_min — на сколько позже конца;
    - без окна и с окном без конца («не раньше HH:MM») — прибытие позже планового ETA не меньше чем на
      rules.late_nowin_min; late_kind plan, over_min — насколько позже плана; нет планового ETA — не оценивается;
    - машина — возвращение на склад позже rules.work_end (конец рабочего дня; возврат в запасе конца дня №78 — не
      опоздание, как в «Развозе»); late_kind return.
    Опоздание, округлённое до минут, меньше 1 — не опоздание. Магазин с несколькими накладными — одна строка (самое
    большое опоздание); target — ключ строки на день (cКЛИЕНТ, без клиента — точка; return). Магазины — по прибытию,
    машина — последней."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=YEREVAN)
    worst: dict[str, dict[str, Any]] = {}
    for s in stops:
        sid, cid = s['stop_id'], s.get('customer_id')
        if s.get('status') != 'pending' or sid == here or sid not in arrive:
            continue
        at = arrive[sid][0]
        span = windows.get(cid) if isinstance(cid, int) else None
        if span is not None and math.isfinite(span[1]):
            limit = midnight + timedelta(minutes=span[1])
            kind = 'window'
        else:
            limit = planned.get(sid)   # type: ignore[assignment]
            if limit is None or (at - limit).total_seconds() / 60.0 < rules.late_nowin_min:
                continue
            kind = 'plan'
        over = round((at - limit).total_seconds() / 60.0)
        target = f'c{cid}' if isinstance(cid, int) else str(sid)
        if over >= 1 and (target not in worst or over > worst[target]['over_min']):
            worst[target] = {'target': target, 'late_kind': kind, 'stop_id': sid, 'customer_id': cid,
                             'name': s.get('name'), 'eta': _iso(at), 'limit': _iso(limit), 'over_min': over}
    out = sorted(worst.values(), key=lambda x: (x['eta'], x['target']))
    if end is not None:
        limit = midnight + timedelta(minutes=rules.work_end)
        over = round((end - limit).total_seconds() / 60.0)
        if over >= 1:
            out.append({'target': 'return', 'late_kind': 'return', 'stop_id': None, 'customer_id': None, 'name': None,
                        'eta': _iso(end), 'limit': _iso(limit), 'over_min': over})
    return out


def car_view(day: date, now: datetime, facts: Mapping[str, Any], plan: Sequence[PlanTrip], truck: TruckSpec,
             depot: Point | None, rules: Rules, road: Road, detail: bool = False,
             windows: Mapping[int, tuple[float, float]] | None = None) -> dict[str, Any]:
    """Карточка машины (detail — ещё линия трека, точки дня и журнал тревог). now — сейчас (Ереван); день не сегодня —
    без ETA и тревог «сейчас». windows — окна приёма клиентов (late_forecast)."""
    live = now.astimezone(YEREVAN).date() == day
    stops = list(facts.get('stops') or ())
    raw = list(facts.get('track') or ())
    fixes = track_fixes(raw)
    pts = ac.clean_track(fixes)
    plan_stops = [ac.PlanStop(s['stop_id'], s.get('customer_id'),
                              (s['lat'], s['lon']) if s.get('lat') is not None and s.get('lon') is not None else None,
                              float(s.get('weight_kg') or 0.0), delivered_at=_moment(s.get('delivered_at')))
                  for s in stops]
    actual = ac.reconstruct(fixes, plan_stops, depot)
    visited = actual.visited
    last = pts[-1] if pts else None
    last_contact = _moment(facts.get('last_contact'))
    device = facts.get('device')
    # данные до: последняя связь или последняя точка GPS, что позже; прогноз — только при свежей связи и включённом GPS
    data_until = max((t for t in (last_contact, last.at if last is not None else None) if t is not None), default=None)
    silent = timedelta(minutes=rules.no_contact_min if device is not None else OLD_APK_SILENT_MIN)
    gps_off = isinstance(device, Mapping) and device.get('gps') in ('off', 'no_permission')
    forecast = live and data_until is not None and now - data_until <= silent and not gps_off

    # визиты точки по GPS (все заезды, не только обслуживающий): идёт сейчас — стоянка до последней точки трека (сегодня);
    # долгий — не короче GPS_VISIT_MIN (короче — пробка, светофор у магазина) или идёт сейчас
    visits: dict[str, list[ac.Visit]] = {}
    for v in actual.visits:
        for k in v.keys:
            visits.setdefault(k, []).append(v)
    ongoing = {k: v for k, vs in visits.items() for v in vs if live and last is not None and v.leave >= last.at}
    long = {k: [v for v in vs if v.minutes >= GPS_VISIT_MIN or ongoing.get(k) is v] for k, vs in visits.items()}
    # касания: закрытые — визит или отметка доставки; открытые водителем у магазина (in_progress) — долгий визит;
    # ожидающие — долгий визит после выезда своего рейса (ниже, по рейсам)
    touches: dict[str, datetime] = {}
    for s in stops:
        sid = s['stop_id']
        if s.get('status') in DONE:
            t = visited.get(sid) or _moment(s.get('delivered_at'))
        elif s.get('status') == 'in_progress':
            t = min((v.arrive for v in long.get(sid, ())), default=None)
        else:
            continue
        if t is not None:
            touches[sid] = t
    trips = trip_of(stops, plan)
    deps = departures(pts, actual, depot)
    start = pts[0].at if pts else None
    open_ids = {s['stop_id'] for s in stops if s.get('status') in OPEN}
    seen: dict[str, list[ac.Visit]] = {}   # точка → засчитанные заезды
    unmarked: set[str] = set()
    for k in sorted(set(trips.values())):   # рейс уехал — с тем, что известно о прежних рейсах (их визиты уже засчитаны)
        gone = departed_trips(trips, touches, deps, start, open_ids - unmarked)
        if k not in gone:
            continue
        # заезд засчитывается не раньше первого выезда после последнего касания прежних рейсов (выезд рейса — последний
        # такой: заезд на склад посреди рейса не отменяет визиты до него)
        prev = max((t for sid, t in touches.items() if trips[sid] < k), default=None)
        bound = min([d for d in deps if prev is None or d > prev][:1] + [gone[k]])
        for s in stops:
            sid = s['stop_id']
            if trips[sid] != k or s.get('status') not in OPEN or s.get('lat') is None or s.get('lon') is None:
                continue   # закрытая точка — визит показывается как есть (ниже), правило заездов — только для незакрытых
            mine = [v for v in long.get(sid, ()) if v.arrive >= bound]
            if not mine:
                continue
            seen[sid] = mine
            if s.get('status') == 'pending':
                touches.setdefault(sid, mine[0].arrive)
            inside = live and last is not None and _near(last.point, (s['lat'], s['lon']), ac.STOP_RADIUS_M)
            if sid not in ongoing and not inside:
                unmarked.add(sid)   # машина была и уехала, водитель не отметил — точка посещена
    gone = departed_trips(trips, touches, deps, start, open_ids - unmarked)   # посещённые по GPS не держат следующий рейс
    # «на месте» — одно правило для карточки (next.here) и таблицы: прогноз есть, последняя точка в STOP_RADIUS_M точки и её
    # последний заезд идёт или кончился не раньше JITTER_BREAK до неё (стоянку оборвало дрожание скорости — машина там же)
    served = dict(actual.served)

    def at_stop(x: Mapping[str, Any]) -> ac.Visit | None:
        vs = visits.get(x['stop_id'])
        if not (forecast and last is not None and vs and x.get('lat') is not None and x.get('lon') is not None
                and _near(last.point, (x['lat'], x['lon']), ac.STOP_RADIUS_M)):   # type: ignore[union-attr]
            return None
        return vs[-1] if vs[-1].leave >= last.at - ac.JITTER_BREAK else None   # type: ignore[union-attr]
    gps: dict[str, dict[str, Any]] = {}
    for s in stops:
        sid = s['stop_id']
        cur = at_stop(s)
        if cur is not None:
            gps[sid] = {'arrive': _iso(cur.arrive), 'leave': None, 'here': True,
                        'minutes': round(max(0.0, (now - cur.arrive).total_seconds() / 60.0))}
            continue
        if s.get('status') in OPEN:   # незакрытая — засчитанный заезд (долгий, после выезда её рейса)
            v = (ongoing.get(sid) if ongoing.get(sid) in seen.get(sid, ()) else seen[sid][0]) if sid in seen else None
        else:   # закрытая — обслуживающий визит, нет — первый заезд, как бы короток ни был
            v = actual.visits[served[sid]] if sid in served else next(iter(visits.get(sid, ())), None)
        if v is not None:
            gps[sid] = {'arrive': _iso(v.arrive), 'leave': None if v is ongoing.get(sid) else _iso(v.leave),
                        'minutes': round(v.minutes), 'here': False}
    load = load_of(stops, trips, gone, touches, facts.get('returns') or (), depot_entries(pts, depot))
    moving = ac.moving_track(fixes, actual)
    fuel = fuel_liters(moving, load, truck) if pts else (0.0 if truck.rate(0) is not None else None)
    refuel = refuel_check(facts.get('refuels') or (), truck, moving, load, day)

    brg = next((p[5] for p in reversed(raw) if last is not None and p[0] == round(last.at.timestamp() * 1000)
                and len(p) > 5), None)
    contacts = sorted(t for t in (_moment(x) for x in facts.get('contacts') or ()) if t is not None)
    closed_at = _moment(facts.get('closed_at'))
    done = sum(1 for s in stops if s.get('status') in DONE)
    at_depot = last is not None and _near(last.point, depot, ac.DEPOT_RADIUS_M)
    finished = closed_at is not None or (bool(stops) and done == len(stops) and at_depot)
    started = contacts[0] if contacts else (pts[0].at if pts else None)
    end = closed_at or (last.at if finished and last is not None else None)
    open_now = live and started is not None and not finished
    age = (now - last.at).total_seconds() if last is not None else None

    # следующий магазин, ETA, опоздание, возвращение
    current = max(gone) if gone else 0
    nxt = _next_stop(stops, trips, plan, current, unmarked) if live and stops else None
    next_out = None
    return_eta = None
    return_source = None
    etas: dict[str, tuple[datetime, bool]] = {}
    late: list[dict[str, Any]] = []
    own = {s['stop_id']: e for s in stops if trips[s['stop_id']] < len(plan)
           and (e := plan[trips[s['stop_id']]].etas.get(s.get('customer_id'))) is not None}   # плановое ETA её рейса
    if nxt is not None and not (forecast and last is not None) and not finished:   # прогноза нет: магазин и план — да
        k = trips[nxt['stop_id']]
        planned = plan[k].etas.get(nxt.get('customer_id')) if k < len(plan) else None   # type: ignore[arg-type]
        next_out = {'stop_id': nxt['stop_id'], 'name': nxt.get('name'), 'here': False, 'eta': None, 'eta_source': None,
                    'planned_eta': _iso(planned), 'delay_min': None, 'eta_unknown': True}
    if (forecast and last is not None and not finished
            and (nxt is not None or (gone and not at_depot and depot is not None))):
        queue = _queue(stops, trips, plan, current, nxt, bool(gone), unmarked)
        here = None
        if nxt is not None and _near(last.point, (nxt['lat'], nxt['lon']), ac.STOP_RADIUS_M):
            cur = at_stop(nxt)   # стоит у него — разгрузка идёт с прибытия этого заезда (только подъехал — с нуля)
            here = (nxt, max(0.0, (now - cur.arrive).total_seconds() / 60.0) if cur is not None else 0.0)
            queue = [(k, [x for x in xs if x['stop_id'] != nxt['stop_id']]) for k, xs in queue]
        eta = eta_plan(day, now, last.point, queue, gone, current if gone else None, plan, depot, road, rules,
                       not lunch_taken(actual, day, rules), at_depot, here, windows)
        etas = dict(eta.arrive)
        if now - last.at <= LATE_FIX_MAX:   # давнее положение — прогноз «не успеет» не строится (нет GPS — нет тревоги)
            late = late_forecast(day, stops, etas, own, windows or {}, eta.end, rules,
                                 nxt['stop_id'] if here is not None else None)   # type: ignore[index]
        if eta.back is not None and gone and not at_depot:
            return_eta, return_source = eta.back[0], 'road' if eta.back[1] else 'model'
        if nxt is not None:
            k = trips[nxt['stop_id']]
            arrival, by_road = etas[nxt['stop_id']]
            planned = plan[k].etas.get(nxt.get('customer_id')) if k < len(plan) else None   # type: ignore[arg-type]
            next_out = {'stop_id': nxt['stop_id'], 'name': nxt.get('name'), 'here': here is not None,
                        'eta': _iso(arrival), 'eta_source': None if here is not None else ('road' if by_road else 'model'),
                        'planned_eta': _iso(planned),
                        'delay_min': round((arrival - planned).total_seconds() / 60) if planned is not None else None,
                        'eta_unknown': False}

    # тревоги
    devices = [(t, gps) for t, gps in ((_moment(a), g) for a, g in facts.get('devices') or ()) if t is not None]
    first_dep = deps[0] if deps else None
    alerts = (speed_alerts(pts, rules, live and age is not None and age <= STALE_S)   # type: ignore[arg-type]
              + stop_alerts(actual, day, rules, first_dep, end, last.at if last else None, open_now)
              + (contact_alerts(contacts, last_contact, now if open_now else None, started, end, rules)
                 if facts.get('device') is not None else [])   # старый APK (<2.2.0) шлёт пачками раз в 2–17 мин
              + gps_alerts(devices, open_now)
              + center_alerts(pts, rules, truck, live and age is not None and age <= STALE_S, stops)
              + [_alert('late', now, None, True, **x) for x in late])   # прогноз «не успеет» (№87) — пока он такой
    alerts.sort(key=lambda a: a['from'] or '')
    active = sorted({a['kind'] for a in alerts if a['active']})

    if not pts and not contacts:
        state = 'nodata'
    elif finished:
        state = 'closed'
    elif any(k not in ('no_contact', 'late') for k in active):
        state = 'alert'   # другая активная тревога важнее «կապ չկա»; «не успеет» — прогноз, не событие машины: своя строка
    elif 'no_contact' in active or (open_now and last_contact is not None and now - last_contact > timedelta(
            minutes=rules.no_contact_min if facts.get('device') is not None else OLD_APK_SILENT_MIN)):
        state = 'offline'   # «կապ չկա»; тревогой — только у APK 2.2.0 и в пределах дня (contact_alerts)
    elif last is not None and age is not None and age <= STALE_S and last.spd is not None \
            and last.spd >= MOVING_MS:   # type: ignore[attr-defined]
        state = 'moving'
    else:
        state = 'standing'

    # груз по плану: рейс, который машина повезёт следующим (первый с точками после последнего уехавшего)
    ahead = sorted(k for k in set(trips.values()) if not gone or k > max(gone))
    planned_kg = (math.fsum(float(s.get('weight_kg') or 0.0) for s in stops if trips[s['stop_id']] == ahead[0])
                  if ahead else None)

    out: dict[str, Any] = {
        'position': ({'lat': round(last.lat, 6), 'lon': round(last.lon, 6), 'at': _iso(last.at),
                      'age_s': round(age) if age is not None else None,
                      'speed_kmh': round(last.spd * 3.6) if last.spd is not None else None,   # type: ignore[attr-defined]
                      'heading': round(brg) if isinstance(brg, (int, float)) else None,
                      'acc': round(last.accuracy) if last.accuracy is not None else None}
                     if last is not None else None),
        'state': state,
        'stores': {'done': done, 'total': len(stops),
                   'in_progress': sum(1 for s in stops if s.get('status') == 'in_progress'),
                   'gps_visited': len(gps), 'unmarked': len(unmarked)},
        'km': round(actual.km_gps, 1),
        'fuel_l': round(fuel, 1) if fuel is not None else None,
        'fuel_check': refuel,
        'load': {'remaining_kg': round(load.remaining_kg), 'loaded_kg': round(load.loaded_kg),
                 'delivered_kg': round(load.delivered_kg), 'unweighed_lines': load.unweighed,
                 'trip_kg': round(load.trip_kg), 'refused_kg': round(load.refused_kg),
                 'returns_kg': round(load.returns_aboard_kg), 'returns_unweighed': load.returns_unweighed,
                 'unloaded_kg': round(load.unloaded_kg),
                 'trips_gone': len(gone), 'trips': max(len(plan), 1 if stops else 0),
                 'planned_kg': round(planned_kg) if planned_kg is not None else None},
        'next': next_out,
        'forecast': forecast,
        'data_until': _iso(data_until),
        'return_eta': _iso(return_eta),
        'return_source': return_source,
        'device': dict(device) if isinstance(device, Mapping) else None,
        'offline_reason': device.get('exit') if state == 'offline' and isinstance(device, Mapping) else None,   # 'closed' | 'shutdown' (APK 2.2.5)
        'drivers': list(facts.get('drivers') or ()),
        'last_contact': _iso(last_contact),
        'contact_age_s': round((now - last_contact).total_seconds()) if last_contact is not None and live else None,
        'closed': finished,
        'alerts': {'active': active, 'count': len(alerts)},
        'late': late,   # «не успеет» (№87, late_forecast): магазины и возврат на склад
        'alerts_log': alerts,   # журнал тревог дня: API флота его не отдаёт (views), Telegram и карточка машины — да
    }
    if detail:
        # simplify смотрит только на широту и долготу: момент точки едет вместе с ней (track_t — 1:1 с track)
        line = ac.simplify([(f.lat, f.lon, f.at.timestamp()) for f in pts], TRACK_LINE_POINTS)   # type: ignore[misc]
        marks = {k: v for k, v in visited.items()}
        out.update({
            'track': [[round(p[0], 6), round(p[1], 6)] for p in line],
            'track_t': [round(p[2]) for p in line],   # type: ignore[misc]
            'stops': [{'stop_id': s['stop_id'], 'customer_id': s.get('customer_id'), 'name': s.get('name'),
                       'lat': s.get('lat'), 'lon': s.get('lon'),
                       'status': s.get('status'), 'seq': s.get('seq'), 'trip': trips[s['stop_id']] + 1,
                       'weight_kg': round(float(s.get('weight_kg') or 0.0), 1),
                       'planned_eta': _iso(own.get(s['stop_id'])),
                       'eta': _iso(etas[s['stop_id']][0]) if s['stop_id'] in etas else None,
                       'eta_source': ('road' if etas[s['stop_id']][1] else 'model') if s['stop_id'] in etas else None,
                       'arrive': _iso(marks.get(s['stop_id'])), 'delivered_at': s.get('delivered_at'),
                       'gps': gps.get(s['stop_id']), 'unmarked': s['stop_id'] in unmarked}
                      for s in sorted(stops, key=lambda s: (trips[s['stop_id']],
                                                            s.get('seq') if isinstance(s.get('seq'), int) else 0))],
        })
    return out

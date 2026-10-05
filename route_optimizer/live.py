# -*- coding: utf-8 -*-
"""«Մեքենաները առցանց» — машины на карте в реальном времени (ответ владельца №76, docs/plans/live-map-plan.md).

Чистые функции — без Flask, БД и ERP: факт терминала машины за день (LiveFacts, раздел «Առաքիչ») + план «Развоза» +
нормы машины → карточка машины. Время — по Еревану; now — момент расчёта (у прошлого дня «сейчас» нет: без ETA и без
тревог «сейчас»).

Расчёты (№76, «≈» — оценка, не измерение):
- км сегодня — км по GPS тем же правилом, что «Առաքում այսօր» и «план — факт» (actuals.reconstruct: стоянки у точек дня
  и склада — 0 км, дрожание на месте км не добавляет);
- рейсы. Точка дня относится к рейсу плана, где есть её клиент (первое появление; нет в плане или плана нет — к
  первому рейсу). «Касание» точки — прибытие обслуживающего визита по GPS, без него — момент отметки доставки. Выезды
  со склада — концы стоянок на складе, после которых машина была вне склада, и начало трека, если он начался вне
  склада. Рейс уехал, если у его точек есть касание (выезд — последний выезд не позже первого касания, нет такого —
  начало трека) или, когда касаний нет, после последнего касания предыдущего рейса (у первого — когда угодно) был
  выезд (берётся последний такой). Рейсы — по порядку: не уехавший рейс останавливает счёт;
- груз на борту в момент t = Σ вес накладных уехавших рейсов (с момента их выезда) − Σ доставлено (вес × доля
  доставленного, №65) по точкам с моментом выгрузки до t (выгрузка — касание точки, не раньше выезда её рейса; доля
  известна без момента — с выезда). Не меньше 0. Остаток груза — груз сейчас; строки без веса — отдельным числом;
- топливо ≈ Σ по сегментам км дня (geo.track_steps по тем же точкам, что км) км × (пусто + (полно − пусто) × груз в
  конце сегмента / грузоподъёмность) / 100 — формула running_costs.route_cost; расход по загрузке не задан — расход
  машины (l/100), без загрузки; нет ни того, ни другого — неизвестно;
- следующий магазин — первая незакрытая (pending/in_progress) точка с координатой последнего уехавшего рейса (нет
  уехавших — первого) по порядку плана (вне плана — по порядку терминала), затем следующих рейсов, затем пропущенные
  раньше. ETA — сейчас + путь от текущей точки (машина в STOP_RADIUS_M от магазина — «на месте», ETA = сейчас); рейс
  магазина ещё не уехал — путь через склад и не раньше планового выезда. Опоздание = ETA − плановое ETA (прогноз
  сборки «Развоза»). Путь — модель дорог «Развоза» без карты: по прямой × извилистость, скорость города или области
  (evaluate.road_norms — калибровка по GPS, без неё 1.3 / 25 / 45 км/ч): текущая точка каждые 30 с новая, в кэш
  расстояний по графу её класть нельзя;
- возвращение на склад ≈ сейчас + путь через оставшиеся точки рейса по порядку + разгрузка на каждой (нормы
  unload_min_per_stop + unload_min_per_tonne × т) + путь до склада.

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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Collection, Mapping, Protocol, Sequence

from . import actuals as ac
from .geo import Fix, Point, haversine_km, in_city, in_polygon, track_steps
from .learning import _hhmm, track_fixes
from .store import DEFAULT_SETTINGS

YEREVAN = ac.YEREVAN
DONE = ('full', 'partial', 'refused', 'covered')   # точка закрыта водителем
OPEN = ('pending', 'in_progress')
STALE_S = 120                       # последняя точка старше — «давно» (серая)
MOVING_MS = ac.STOP_MS              # скорость терминала не ниже — машина едет (как стоянка у магазина, №60)
SPEED_GAP = timedelta(seconds=60)   # перерыв трека дольше — превышение скорости прерывается
CENTER_MIN_POINTS = 2               # в малом центре — не меньше 2 точек подряд (одна — может быть погрешность GPS)
NO_CONTACT_END_H = 20               # APK останавливает запись в 20:00 — после этого «нет связи» не тревога (№76, ревью)
NO_CONTACT_MAX = timedelta(hours=3)  # связи нет дольше — машина закончила день: состояние «կապ չկա», без тревоги
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

    @classmethod
    def from_settings(cls, s: Mapping[str, Any]) -> Rules:
        def hm(key: str, default: float) -> float:
            m = _hhmm(s.get(key))
            return float(m) if m is not None else default

        def num(key: str) -> float:
            v = s.get(key)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float(DEFAULT_SETTINGS[key])
        return cls(num('live_speed_kmh'), num('live_speed_sec'), num('live_stop_min'), num('live_no_contact_min'),
                   num('truck_lunch_min'), (hm('truck_lunch_from', 750.0), hm('truck_lunch_to', 870.0)),
                   tuple((float(p[0]), float(p[1])) for p in s.get('center_zone') or ()),
                   num('unload_min_per_stop'), num('unload_min_per_tonne'))


@dataclass(frozen=True)
class TruckSpec:
    """Нормы машины: грузоподъёмность, расход (л/100 км) пустой/полной (running_costs) и общий, право въезда в центр
    (None — неизвестно: тревоги «центр» нет)."""
    capacity_kg: float | None = None
    l100: float | None = None
    empty_l100: float | None = None
    full_l100: float | None = None
    center_ok: bool | None = None

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
    """Модель пути без графа дорог: по прямой × извилистость, скорость города (оба конца в радиусе города) или области."""
    detour: float = 1.3
    city_kmh: float = 25.0
    region_kmh: float = 45.0
    center: Point = (40.1792, 44.4991)
    radius_km: float = 12.0

    def minutes(self, a: Point, b: Point) -> float:
        km = haversine_km(a, b) * self.detour
        city = in_city(a, self.center, self.radius_km) and in_city(b, self.center, self.radius_km)
        return km / (self.city_kmh if city else self.region_kmh) * 60.0


@dataclass(frozen=True)
class PlanTrip:
    """Рейс плана машины: клиенты по порядку объезда, плановые ETA клиентов, плановый выезд."""
    customers: tuple[int, ...]
    etas: Mapping[int, datetime]
    depart: datetime | None = None


def plan_trips(draft_trips: Sequence[Sequence[int]], prediction: Mapping[str, Any] | None, day: date) -> list[PlanTrip]:
    """Рейсы машины из черновика «Развоза» (клиенты по порядку) и прогноза сборки (prediction['trucks'][машина]:
    trips — depart «HH:MM», stops — [[клиент, ETA]]). ETA клиента — из прогноза (первое появление); выезд рейса — из
    прогноза, если число рейсов то же (логист не менял рейсы после сборки), иначе неизвестен."""
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
    return [PlanTrip(tuple(stops), {c: etas[c] for c in stops if c in etas}, at(pred[j].get('depart')) if same else None)
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

    def at(self, t: datetime) -> float:
        """Груз на борту в момент t (события строго до t), не меньше 0."""
        k = bisect.bisect_left([e[0] for e in self.events], t)
        return max(0.0, math.fsum(d for _, d in self.events[:k]))

    @property
    def remaining_kg(self) -> float:
        return max(0.0, self.loaded_kg - self.delivered_kg)


def load_of(stops: Sequence[Mapping[str, Any]], trips: Mapping[str, int], gone: Mapping[int, datetime],
            touches: Mapping[str, datetime]) -> Load:
    """Груз по уехавшим рейсам gone (рейс → выезд): + вес рейса в момент выезда, − доставлено в момент выгрузки точки."""
    events: list[tuple[datetime, float]] = []
    loaded = delivered = 0.0
    unweighed = 0
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
        if share is not None and share > 0:
            done = kg * min(1.0, float(share))
            delivered += done
            events.append((max(touches.get(s['stop_id'], gone[k]), gone[k]), -done))
    events.sort(key=lambda e: e[0])
    return Load(tuple(events), loaded, delivered, unweighed)


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
    if now is not None and last_contact is not None and limit < now - last_contact <= NO_CONTACT_MAX             and now.astimezone(YEREVAN).hour < NO_CONTACT_END_H:
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


def center_alerts(pts: Sequence[Fix], rules: Rules, truck: TruckSpec, live: bool) -> list[dict[str, Any]]:
    """Машина без права въезда (center_ok False) в границе малого центра: подряд не меньше CENTER_MIN_POINTS точек."""
    if truck.center_ok is not False or len(rules.center_zone) < 3:
        return []
    out = []
    run: list[Fix] = []
    for i, f in enumerate(pts + [None]):   # type: ignore[operator]
        if f is not None and in_polygon(f.point, rules.center_zone):
            run.append(f)
            continue
        if len(run) >= CENTER_MIN_POINTS:
            out.append(_alert('center', run[0].at, run[-1].at, live and f is None, lat=run[0].lat, lon=run[0].lon))
        run = []
    return out


# --- карточка машины ---

def _next_stop(stops: Sequence[Mapping[str, Any]], trips: Mapping[str, int], plan: Sequence[PlanTrip],
               current: int) -> Mapping[str, Any] | None:
    """Следующий магазин: незакрытая точка с координатой рейса current, затем следующих рейсов, затем прежних; внутри —
    in_progress, затем порядок плана (вне плана — порядок терминала)."""
    pos = {c: i for t in plan for i, c in enumerate(t.customers)}
    left = [s for s in stops if s.get('status') in OPEN and s.get('lat') is not None and s.get('lon') is not None]

    def key(s: Mapping[str, Any]) -> tuple[Any, ...]:
        k = trips[s['stop_id']]
        group = 0 if k == current else (1 if k > current else 2)
        return (group, k, s.get('status') != 'in_progress', pos.get(s.get('customer_id'), math.inf),
                s.get('seq') if isinstance(s.get('seq'), int) else math.inf, s['stop_id'])
    return min(left, key=key) if left else None


def car_view(day: date, now: datetime, facts: Mapping[str, Any], plan: Sequence[PlanTrip], truck: TruckSpec,
             depot: Point | None, rules: Rules, road: Road, detail: bool = False) -> dict[str, Any]:
    """Карточка машины (detail — ещё линия трека, точки дня и журнал тревог). now — сейчас (Ереван); день не сегодня —
    без ETA и тревог «сейчас»."""
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
    touches: dict[str, datetime] = {}
    for s in stops:
        t = visited.get(s['stop_id']) or (_moment(s.get('delivered_at')) if s.get('status') in DONE else None)
        if t is not None:
            touches[s['stop_id']] = t
    trips = trip_of(stops, plan)
    deps = departures(pts, actual, depot)
    gone = departed_trips(trips, touches, deps, pts[0].at if pts else None,
                          {s['stop_id'] for s in stops if s.get('status') in OPEN})
    load = load_of(stops, trips, gone, touches)
    fuel = fuel_liters(ac.moving_track(fixes, actual), load, truck) if pts else (0.0 if truck.rate(0) is not None
                                                                                  else None)

    last = pts[-1] if pts else None
    brg = next((p[5] for p in reversed(raw) if last is not None and p[0] == round(last.at.timestamp() * 1000)
                and len(p) > 5), None)
    contacts = sorted(t for t in (_moment(x) for x in facts.get('contacts') or ()) if t is not None)
    last_contact = _moment(facts.get('last_contact'))
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
    nxt = _next_stop(stops, trips, plan, current) if live and stops else None
    next_out = None
    return_eta = None
    if nxt is not None and last is not None and not finished:
        k = trips[nxt['stop_id']]
        point = (nxt['lat'], nxt['lon'])
        here = _near(last.point, point, ac.STOP_RADIUS_M)
        if here:
            eta = now
        elif k in gone or depot is None:
            eta = now + timedelta(minutes=road.minutes(last.point, point))
        else:   # рейс ещё не уехал: через склад, не раньше планового выезда
            at_load = now + timedelta(minutes=0 if at_depot else road.minutes(last.point, depot))
            dep = plan[k].depart if k < len(plan) else None
            eta = max(at_load, dep or at_load) + timedelta(minutes=road.minutes(depot, point))
        planned = plan[k].etas.get(nxt.get('customer_id')) if k < len(plan) else None   # type: ignore[arg-type]
        next_out = {'stop_id': nxt['stop_id'], 'name': nxt.get('name'), 'here': here, 'eta': _iso(eta),
                    'planned_eta': _iso(planned),
                    'delay_min': round((eta - planned).total_seconds() / 60) if planned is not None else None}
        if k in gone and depot is not None:
            pos = {c: i for i, c in enumerate(plan[k].customers)} if k < len(plan) else {}
            rest = sorted((s for s in stops if trips[s['stop_id']] == k and s.get('status') in OPEN
                           and s.get('lat') is not None and s.get('lon') is not None),
                          key=lambda s: (s['stop_id'] != nxt['stop_id'], pos.get(s.get('customer_id'), math.inf),
                                         s.get('seq') if isinstance(s.get('seq'), int) else math.inf, s['stop_id']))
            t, here_p = now, last.point
            for s in rest:
                p = (s['lat'], s['lon'])
                t += timedelta(minutes=road.minutes(here_p, p) + rules.unload_min_per_stop
                               + rules.unload_min_per_tonne * float(s.get('weight_kg') or 0.0) / 1000.0)
                here_p = p
            return_eta = t + timedelta(minutes=road.minutes(here_p, depot))
        elif not at_depot and depot is not None and gone:   # рейс развезён, следующий — после склада
            return_eta = now + timedelta(minutes=road.minutes(last.point, depot))
    elif live and last is not None and gone and not finished and not at_depot and depot is not None:
        return_eta = now + timedelta(minutes=road.minutes(last.point, depot))   # всё развезено — домой

    # тревоги
    devices = [(t, gps) for t, gps in ((_moment(a), g) for a, g in facts.get('devices') or ()) if t is not None]
    first_dep = deps[0] if deps else None
    alerts = (speed_alerts(pts, rules, live and age is not None and age <= STALE_S)   # type: ignore[arg-type]
              + stop_alerts(actual, day, rules, first_dep, end, last.at if last else None, open_now)
              + (contact_alerts(contacts, last_contact, now if open_now else None, started, end, rules)
                 if facts.get('device') is not None else [])   # старый APK (<2.2.0) шлёт пачками раз в 2–17 мин
              + gps_alerts(devices, open_now)
              + center_alerts(pts, rules, truck, live and age is not None and age <= STALE_S))
    alerts.sort(key=lambda a: a['from'] or '')
    active = sorted({a['kind'] for a in alerts if a['active']})

    if not pts and not contacts:
        state = 'nodata'
    elif finished:
        state = 'closed'
    elif 'no_contact' in active or (open_now and last_contact is not None
                                    and now - last_contact > timedelta(minutes=rules.no_contact_min)):
        state = 'offline'   # «կապ չկա»; тревогой — только у APK 2.2.0 и в пределах дня (contact_alerts)
    elif active:
        state = 'alert'
    elif last is not None and age is not None and age <= STALE_S and last.spd is not None \
            and last.spd >= MOVING_MS:   # type: ignore[attr-defined]
        state = 'moving'
    else:
        state = 'standing'

    device = facts.get('device')
    out: dict[str, Any] = {
        'position': ({'lat': round(last.lat, 6), 'lon': round(last.lon, 6), 'at': _iso(last.at),
                      'age_s': round(age) if age is not None else None,
                      'speed_kmh': round(last.spd * 3.6) if last.spd is not None else None,   # type: ignore[attr-defined]
                      'heading': round(brg) if isinstance(brg, (int, float)) else None,
                      'acc': round(last.accuracy) if last.accuracy is not None else None}
                     if last is not None else None),
        'state': state,
        'stores': {'done': done, 'total': len(stops),
                   'in_progress': sum(1 for s in stops if s.get('status') == 'in_progress')},
        'km': round(actual.km_gps, 1),
        'fuel_l': round(fuel, 1) if fuel is not None else None,
        'load': {'remaining_kg': round(load.remaining_kg), 'loaded_kg': round(load.loaded_kg),
                 'delivered_kg': round(load.delivered_kg), 'unweighed_lines': load.unweighed,
                 'trips_gone': len(gone), 'trips': max(len(plan), 1 if stops else 0)},
        'next': next_out,
        'return_eta': _iso(return_eta),
        'device': dict(device) if isinstance(device, Mapping) else None,
        'drivers': list(facts.get('drivers') or ()),
        'last_contact': _iso(last_contact),
        'contact_age_s': round((now - last_contact).total_seconds()) if last_contact is not None and live else None,
        'closed': finished,
        'alerts': {'active': active, 'count': len(alerts)},
    }
    if detail:
        line = ac.simplify([f.point for f in pts], TRACK_LINE_POINTS)
        etas = {c: e for t in plan for c, e in t.etas.items()}
        marks = {k: v for k, v in visited.items()}
        out.update({
            'track': [[round(p[0], 6), round(p[1], 6)] for p in line],
            'stops': [{'stop_id': s['stop_id'], 'name': s.get('name'), 'lat': s.get('lat'), 'lon': s.get('lon'),
                       'status': s.get('status'), 'seq': s.get('seq'), 'trip': trips[s['stop_id']] + 1,
                       'weight_kg': round(float(s.get('weight_kg') or 0.0), 1),
                       'planned_eta': _iso(etas.get(s.get('customer_id'))),   # type: ignore[arg-type]
                       'arrive': _iso(marks.get(s['stop_id'])), 'delivered_at': s.get('delivered_at')}
                      for s in sorted(stops, key=lambda s: (trips[s['stop_id']],
                                                            s.get('seq') if isinstance(s.get('seq'), int) else 0))],
            'alerts_log': alerts,
        })
    return out

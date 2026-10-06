# -*- coding: utf-8 -*-
"""План развоза на завтра (docs/plans/dispatch-plan.md): заказы дня → рейсы машин, правки логиста,
сравнение «по менеджерам» и «план и факт».

Чистая логика — без Flask, БД и ERP. Единицы: км, минуты, кг, драмы.
- Заказы к доставке в день D — проведённые заказы ERP с датой от предыдущего рабочего дня до D
  (правило владельца №5/№19: заказы дня везут на следующий рабочий день, субботние и воскресные —
  в понедельник), кроме уже отгруженных до D (реализация SALES с датой раньше D). Заказ, заведённый в ERP раньше
  своей даты (ответ владельца №79, DispatchOrder.predated), — заказ и на эту дату: его везут уже в неё, не отгрузили — на
  следующий рабочий день, как обычный, если план его даты его не видел (завели после сборки — не теряется); видел —
  решён там (отвезли без накладной, исключили, перенесли): «не отгружены с прошлых дней» (settle_predated, PlanSeen).
  В «новые заказы дня» он не входит.
- «Не отгружены с прошлых дней» — заказы ещё BACKLOG_WORKDAYS рабочих дней раньше, без реализации
  до D: по данным сентября около половины их везут в D, остальные не везут вовсе — поэтому в план
  они не входят, пока логист не добавит их сам (Draft.added).
- Остановка — клиент: все его заказы дня одной точкой. Координата — ручная точка логиста → адрес
  клиента в ERP → медиана GPS визитов (evaluate.visit_coord).
- Рейсы — fleet.route_day (Кларк–Райт, 2-opt, тоннаж, рабочий день машины, разгрузка); тяжёлый
  заказ — несколько поездок поровну: клиент встречается в k рейсах — в каждом 1/k его кг. Сборка разгружает
  рейсы тяжелее 90% тоннажа (5 т + 3 т → 4 т + 4 т, ответ владельца №44), если км и литры растут не больше 3%.
- Черновик плана (store.dispatch_plan) — машины дня, исключённые заказы и рейсы (клиенты по порядку
  объезда, машина, «закреплён»). Цифры рейсов всегда пересчитываются по текущим заказам: новые
  заказы попадают в «ещё не в рейсах», исчезнувшие — убираются из рейсов.
- Свежесть заказов: до dispatch_ready_time в последний день приёма заказов на D они ещё поступают
  (orders_still_coming); черновик помнит заказы последней сборки (Draft.built_orders) — новые и
  отменённые/отгруженные с тех пор считает since_build.
- Окна приёма магазинов и малый центр (windows-center-plan.md): окно — прибытие к магазину (раньше — машина
  ждёт, ожидание — её время), центр — только машины с правом въезда. Сборка и «Везти после конца дня» их
  соблюдают (fleet._plan_timed); что не помещается в окно или без машины для центра — no_window / no_center.
  Правки логиста не запрещаются: нарушение видно в плане (window_miss, center_miss).
- Большая машина в Ереване (ответ владельца №68, правило — fleet): зона Еревана и надбавка — в ctx.tn (views._dispatch_ctx);
  у точки в зоне на большой машине план показывает надбавку (yerevan_min) — она уже в её разгрузке и во времени рейса.
- Новые заказы дня (ответ владельца №72): заказы с датой D — развоз D+1, но логист может взять их в развоз D
  (Draft.same_day) — вставкой в рейс, который по плану ещё не грузится, новым рейсом машины после её возвращения или
  машиной, которая сегодня не выезжала (same_day_options; машина в рейсе новый заказ не берёт — ответ «Բ»). Рейс, чья
  загрузка по плану уже началась, не меняется; прочие рейсы и точки плана не переставляются. Новый рейс не грузится
  раньше «сейчас» (DraftTrip.not_before). Взятые в D заказы D+1 не везёт (views._same_day_taken).
- Чьи заказы везут машины парка (ответ владельца №74, FleetRule): «везёт сам» (экспедитор = менеджер) — не для машин,
  кроме менеджеров правила (Rocarm A000), чьи такие заказы везут машины, если клиент не в городе-исключении («գնում է
  այլ մեքենայով»); клиенты из списка настроек — никогда. Пустое правило — прежний отбор до байта.
- Утверждение плана дня (ответ владельца №73, «Հաստատել օրվա պլանը»): все рейсы закреплены (approve), пересборка
  запрещена (views), ручные правки и новые заказы дня — можно; новые рейсы, пока план утверждён, тоже закреплены
  (keep_approved). Снятие (unapprove) открепляет ровно то, что закрепило утверждение.
- Водителей меньше, чем машин (ответ владельца №77, build_crewed): машин в рейсах не больше, чем вышло водителей; свой
  водитель — на своей машине, свободные — на машинах невышедших, лишние машины выбирает сборка по ֏ дня. Машины без
  водителя (Draft.unmanned) — не машины дня: ни правки, ни новые заказы дня (№72), ни совет (№54) их не берут.
- Запас в конце дня (ответ владельца №78, DayContext.end_reserve_min): сборка (build, новые заказы дня) планирует возврат
  не позже truck_work_end − запас (_horizon); рейс, вернувшийся в запасе (закреплён, правлен вручную), не опаздывает —
  мягкая пометка in_reserve; опоздание и переработка (№32) — как раньше, от truck_work_end. Запас на рейс (№66) — в
  возвращении рейса, запас дня — от него.
- Погрузка по сезону (ответ владельца №78, morning_loading, fl.TruckNorms.preload): вне сезона утренней погрузки первый
  рейс машины загружен с вечера и выезжает в начале дня (_timeline, правило — fleet); второй и следующие рейсы, новый
  рейс с заказами дня (№72) — с загрузкой. На странице у такого рейса loading_minutes 0 и preloaded.
- «Բեռնված է» (ответ владельца №78, 9–12, 15; DraftTrip.loaded): склад или логист отмечает рейс загруженным — только по
  утверждённому плану (mark_loaded). Инвариант: загруженный рейс закреплён — пересборка его не трогает (ни состав, ни
  машину), снятие утверждения его не открепляет (держит отметка: loaded['pin']), логист не открепляет его кнопкой,
  новые заказы дня (№72) и перенос конца рейса (№59) в него не вставляют; ручная правка, меняющая его состав, — только с
  подтверждением (LoadedEdit). Снятие отметки (unmark_loaded) открепляет рейс, если его держала только она; держит
  утверждение — закрепление переходит утверждению (снятие утверждения откроет).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from functools import cached_property
from datetime import date, datetime, time, timedelta
from itertools import combinations
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable, Collection, Mapping, Sequence

from . import fleet as fl
from . import vrp
from .geo import ERP_GPS_MAX_GAP_KM, Coord, Point, in_polygon
from .running_costs import configured
from .vehicle_access import VehicleAccess

if TYPE_CHECKING:
    from .evaluate import Norms

ISN_RE = re.compile(r'^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$')
_EPS = 1e-6
MAX_TRIPS = 500           # защита от битого черновика
MAX_BUILT_ORDERS = 20000  # заказов в отметке сборки — тоже защита от битого черновика
MAX_AGENTS = 500          # менеджеров в «чьи заказы не везём» — защита от битого черновика и запроса
BACKLOG_WORKDAYS = 2      # «не отгружены с прошлых дней» — заказы ещё двух рабочих дней раньше окна
BYPASS_MIN_KM = 0.01      # пояснение рейса: участок длиннее из-за объезда центра хотя бы на 10 м — «в объезд»
WEEKDAY_FULL = {1: 'понедельник', 2: 'вторник', 3: 'среда', 4: 'четверг', 5: 'пятница', 6: 'суббота',
                7: 'воскресенье'}


class DispatchError(ValueError):
    """Правка логиста не применима (текст — для пользователя)."""


class LoadedEdit(DispatchError):
    """Правка меняет загруженный рейс (№78): товар уже в машине — повторить с подтверждением логиста."""


# --- Данные ERP ---

@dataclass(frozen=True)
class DispatchOrder:
    """Заказ ERP (ORDERS): кг — строки заказа × вес товара; shipped — дата первой проведённой
    реализации по заказу (None — ещё не отгружен); entered — день ввода в ERP (DOCUMENTS.fCREATIONDATE; None — нет)."""
    isn: str
    doc_num: str
    order_date: date
    customer_id: int
    agent_id: int
    car_code: str          # машина в заказе (ORDERS.fDELIVERYCAR), '' — не указана
    revenue: float
    kg: float
    shipped: date | None
    van_agent_id: int = 0  # кто везёт (ORDERS.fVANAGENTID): сам менеджер — не для машин парка
    entered: date | None = None

    @property
    def self_delivery(self) -> bool:
        return self.van_agent_id != 0 and self.van_agent_id == self.agent_id

    @property
    def predated(self) -> bool:
        """Заведён раньше своей даты — заказ на эту дату (ответ владельца №79): 8 недель до 06.10.2026 таких 305 из 8018
        (292 — линия A008), у прочих менеджеров с июня 20 из 31 отгружены в саму дату."""
        return self.entered is not None and self.entered < self.order_date


@dataclass(frozen=True)
class ShippedDoc:
    """Реализация за прошедший день (SALES): кто и на какой машине вёз. van_agent_id — кто вёз
    (SALES.fVANAGENTID): экспедиторы возят без машины в накладной — тогда car_code пуст."""
    customer_id: int
    agent_id: int
    car_code: str
    revenue: float
    kg: float
    van_agent_id: int = 0


def fact_car(d: ShippedDoc, van_trucks: Mapping[int, str]) -> str:
    """Машина, которая фактически везла накладную: машина в накладной; без неё — ручная машина экспедитора
    (store.Bundle.van_trucks). Менеджер развозит сам (fVANAGENTID = fSALESAGENTID) — не машина парка: ''."""
    if d.car_code:
        return d.car_code
    if d.van_agent_id and d.van_agent_id != d.agent_id:
        return van_trucks.get(d.van_agent_id, '')
    return ''


@dataclass(frozen=True)
class DispatchData:
    orders: tuple[DispatchOrder, ...]
    customers: dict[int, tuple[str, str]]          # клиент → (код, название)
    addresses: dict[int, str]                      # клиент → адрес текстом
    # менеджер → кто возил за 90 дней, от самого частого: код машины ERP или id экспедитора без машины (int)
    agent_cars: dict[int, tuple[str | int, ...]]
    loaded_at: datetime


@dataclass(frozen=True)
class FactData:
    docs: tuple[ShippedDoc, ...]
    customers: dict[int, tuple[str, str]]


# --- Дни ---

def holidays_of(settings: Mapping[str, Any]) -> frozenset[date]:
    """Нерабочие даты из настроек (праздники и прочие выходные компании, №64): ISO-строки → даты."""
    return frozenset(date.fromisoformat(d) for d in settings.get('holidays') or ())


def agents_off_of(draft: Draft | None, settings: Mapping[str, Any]) -> set[int]:
    """Менеджеры, чьи заказы дня не везём (№69): у дня есть черновик — его выбор (Draft.agents_off, меняется на
    «Развозе»), нет — правило настроек dispatch_agents_off: с него начинается и первый черновик дня (сборка)."""
    return set(draft.agents_off) if draft is not None else set(settings.get('dispatch_agents_off') or ())


def is_workday(day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> bool:
    """Рабочий день: день недели (1 = пн … 7 = вс) отмечен рабочим и даты нет среди нерабочих (№64)."""
    return day.isoweekday() in (set(workdays) or set(range(1, 8))) and day not in holidays


def previous_workday(day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> date:
    """Последний рабочий день строго раньше day. Цикл конечен: нерабочих дат не больше
    store.MAX_HOLIDAYS, а хотя бы один день недели рабочий."""
    d = day - timedelta(days=1)
    while not is_workday(d, workdays, holidays):
        d -= timedelta(days=1)
    return d


def next_workday(day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> date:
    """Первый рабочий день строго позже day — «завтра» для логиста (в субботу — понедельник)."""
    d = day + timedelta(days=1)
    while not is_workday(d, workdays, holidays):
        d += timedelta(days=1)
    return d


def order_window(day: date, workdays: Sequence[int], holidays: Collection[date] = ()) -> tuple[date, date]:
    """Даты заказов, которые везут в day: [предыдущий рабочий день, day). В понедельник — заказы
    субботы и воскресенья; после праздника — и заказы праздника. Заказы, заведённые заранее (№79), — и заказы самого
    day: of_day."""
    return previous_workday(day, workdays, holidays), day


def of_day(o: DispatchOrder, since: date, day: date) -> bool:
    """Заказ дня day (since — начало окна order_window): дата в [since, day); заведённый заранее (№79) — и с датой day.
    Он заказ и своей даты, и следующего рабочего дня: не отгружен в свою дату — едет на следующий, как обычный (заведён
    после сборки плана и в рейс не попал — не теряется), отгружен — ни там, ни дальше (shipped)."""
    return since <= o.order_date <= day if o.predated else since <= o.order_date < day


def before_day(o: DispatchOrder, since: date) -> bool:
    """Заказ везли (должны были везти) раньше дня с окном от since: «не отгружены с прошлых дней», если не отгружен."""
    return o.order_date < since


def backlog_since(since: date, workdays: Sequence[int], n: int = BACKLOG_WORKDAYS,
                  holidays: Collection[date] = ()) -> date:
    """Начало окна «не отгружены с прошлых дней»: n рабочих дней раньше окна заказов дня."""
    for _ in range(n):
        since = previous_workday(since, workdays, holidays)
    return since


# --- Чьи заказы везут машины парка (ответ владельца №74) ---

FLEET, SELF_DELIVERY, OTHER_VEHICLE, CUSTOMER_OFF = 'fleet', 'self_delivery', 'other_vehicle', 'customers_off'
# Другие написания городов-исключений (№74): ключ — город настроек по-армянски в нижнем регистре (fold_text), значения —
# по-русски, латиницей, старое название и дореформенное написание. Город не из словаря — только как его ввели
CITY_ALIASES: dict[str, tuple[str, ...]] = {
    'գյումրի': ('գիւմրի', 'լենինական', 'гюмри', 'ленинакан', 'gyumri', 'gumri', 'leninakan'),
    'կապան': ('ղափան', 'капан', 'kapan', 'ghapan'),
    'գորիս': ('горис', 'goris'),
    'վանաձոր': ('կիրովական', 'ванадзор', 'кировакан', 'vanadzor', 'kirovakan'),
}
_YO = str.maketrans('ё', 'е')
# Марзы Армении — первое поле адреса ERP «Марз, город, улица, дом» (fold_text); «Սյունիքի մարզ» — по слову «մարզ»
MARZES = frozenset({'արագածոտն', 'արարատ', 'արմավիր', 'գեղարքունիք', 'կոտայք', 'լոռի', 'շիրակ', 'սյունիք', 'վայոց ձոր',
                    'տավուշ', 'арагацотн', 'гегаркуник', 'котайк', 'лори', 'ширак', 'сюник', 'вайоц дзор', 'тавуш'})
_REGION_WORDS = frozenset({'մարզ', 'մարզի', 'область', 'обл', 'марз'})
_COUNTRY = frozenset({'հհ', 'հայաստան', 'հայաստանի հանրապետություն', 'армения', 'ра', 'armenia'})
# Слова улиц и дорог: «Վանաձոր փողոց 4», «Գյումրու խճ.» — это не город
STREET_WORDS = frozenset({'փ', 'փող', 'փողոց', 'պող', 'պողոտա', 'խճ', 'խճղ', 'խճուղի', 'մայրուղի', 'նրբ', 'նրբանցք',
                          'թաղ', 'թաղամաս', 'ул', 'улица', 'пр', 'проспект', 'пер', 'переулок', 'шоссе', 'трасса',
                          'st', 'street', 'str', 'ave', 'avenue', 'road', 'rd', 'highway'})
# «ք. Կապան», «ք․Գորիս», «г. Капан», «քաղաք Գյումրի» — город назван явно
_CITY_MARK = re.compile(r'(?<!\w)(?:(?:ք|г)\s*[.․]|(?:քաղաք|город)(?!\w))\s*')


def fold_text(text: str) -> str:
    """Текст для сравнения городов: без регистра (и армянские прописные: «ԿԱՊԱՆ» = «Կապան»), «ё» = «е», «և»/«եւ»/«ԵՎ»
    = «եվ», пробелы схлопнуты."""
    return ' '.join(text.casefold().translate(_YO).replace('եւ', 'եվ').split())


def city_field(address: str) -> str | None:
    """Поле города адреса ERP (fold_text) — с него начинается название города, дальше может идти улица: после «ք.»/«г.»
    («Սյունիքի մարզ, ք․ Կապան Շինարարների 1» → «կապան շինարարների 1»), иначе второе поле после марза («Շիրակ,
    Գյումրի, …»), иначе первое поле («Երևան, Աջափնյակ, …»; «ՀՀ»/«Հայաստան» впереди пропускаются). Адрес из одного поля
    без «ք.» («Բաբայան 2/17») или пустой — None: города в нём нет. Адрес в несколько строк — последняя непустая строка
    (в первых бывает название: «Կապան Մարկետ ՍՊԸ\nԵրևան, Կոմիտաս 1» — Ереван)."""
    lines = [x for x in address.splitlines() if x.strip()]
    t = fold_text(lines[-1] if lines else '')
    m = _CITY_MARK.search(t)
    if m:
        return t[m.end():].split(',')[0].strip() or None
    segs = [x.strip() for x in t.split(',') if x.strip()]
    while segs and segs[0] in _COUNTRY:
        segs.pop(0)
    if len(segs) < 2:
        return None
    if segs[0] in MARZES or _REGION_WORDS & set(segs[0].split()):
        return segs[1]
    return segs[0]


def _starts_city(text: str, key: str) -> bool:
    """text начинается городом key целым словом, и это не улица/дорога его имени («վանաձոր փողոց», «գյումրի-երևան խճղ.»)."""
    if text == key:
        return True
    if not text.startswith(key + ' '):
        return False
    return text[len(key) + 1:].split(' ', 1)[0].strip('.,․') not in STREET_WORDS


Place = tuple[str, str]   # клиент: (адрес по умолчанию ERP, название клиента)


def place_of(customers: Mapping[int, tuple[str, str]], addresses: Mapping[int, str]) -> Callable[[int], Place]:
    """Клиент → (адрес, название) по справочникам дня (DispatchData.customers и addresses)."""
    return lambda cid: (addresses.get(cid, ''), (customers.get(cid) or ('', ''))[1])


def _no_place(cid: int) -> Place:
    return '', ''


@dataclass(frozen=True)
class FleetRule:
    """Чьи заказы дня везут машины парка (№74, настройки «Маршрутов»). customers_off — клиенты, чьи заказы машины не везут
    никогда (внутренние счета, экспорт). Заказ «везёт сам» (DispatchOrder.self_delivery) не для машин, кроме менеджеров
    agents: их такие заказы везут машины (Rocarm A000 — в ERP экспедитор он сам), если клиент не в городе из cities —
    тогда «գնում է այլ մեքենայով». Город — поле города адреса по умолчанию (city_field; адрес говорит «Երևան» — значит
    Ереван, что бы ни было в названии); нет его — часть названия клиента после «/» («Տռովիքս ՍՊԸ/ԳՅՈՒՄՐԻ»). Без регистра,
    с другими написаниями (CITY_ALIASES). Пустое правило (нет ни менеджеров, ни клиентов) — отбор как до него."""
    agents: frozenset[int] = frozenset()
    cities: tuple[str, ...] = ()
    customers_off: frozenset[int] = frozenset()

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> FleetRule:
        return cls(frozenset(settings.get('dispatch_fleet_agents') or ()),
                   tuple(settings.get('dispatch_other_cities') or ()),
                   frozenset(settings.get('dispatch_customers_off') or ()))

    @classmethod
    def from_json(cls, raw: Any) -> FleetRule:
        """Правило, сохранённое в черновике дня (Draft.fleet); нет его или битое — пустое правило."""
        if not isinstance(raw, dict):
            return NO_RULE
        ids = lambda key: frozenset(x for x in (raw.get(key) or [])[:MAX_FLEET_IDS] if _is_int(x))   # noqa: E731
        return cls(ids('agents'), tuple(c for c in (raw.get('cities') or [])[:MAX_FLEET_IDS] if isinstance(c, str)),
                   ids('customers_off'))

    @property
    def active(self) -> bool:
        return bool(self.agents or self.customers_off)

    def to_json(self) -> dict[str, Any] | None:
        """Для черновика дня: пустое правило — None (поля в черновике нет)."""
        if not self.active:
            return None
        return {'agents': sorted(self.agents), 'cities': list(self.cities), 'customers_off': sorted(self.customers_off)}

    def same_as(self, other: FleetRule) -> bool:
        """Правила отбирают заказы одинаково (города — без регистра и порядка; у пустых правил города не важны)."""
        if not (self.active or other.active):
            return True
        return (self.agents, self.customers_off, frozenset(map(fold_text, self.cities))) == \
            (other.agents, other.customers_off, frozenset(map(fold_text, other.cities)))

    @cached_property
    def _keys(self) -> tuple[tuple[str, str], ...]:
        """(написание, город настроек) — длинные первыми."""
        out: dict[str, str] = {}
        for city in self.cities:
            k = fold_text(city)
            for v in (k, *CITY_ALIASES.get(k, ())):
                if v:
                    out.setdefault(v, city)
        return tuple(sorted(out.items(), key=lambda kv: -len(kv[0])))

    def matched_city(self, place: Place) -> str | None:
        """Город из cities, где клиент (адрес, название), или None."""
        address, name = place
        field_ = city_field(address)
        texts = [field_] if field_ is not None else [fold_text(x).strip(' .,-') for x in name.split('/')[1:]]
        return next((city for key, city in self._keys for t in texts if _starts_city(t, key)), None)

    def needs_place(self, o: DispatchOrder) -> bool:
        """Решению по заказу нужно, где клиент (город-исключение)."""
        return bool(self.cities) and o.self_delivery and o.agent_id in self.agents \
            and o.customer_id not in self.customers_off

    def kind(self, o: DispatchOrder, place: Callable[[int], Place] = _no_place) -> str:
        """FLEET — заказ для машин парка; SELF_DELIVERY — менеджер везёт сам; OTHER_VEHICLE — менеджер правила, город-
        исключение; CUSTOMER_OFF — клиент из списка «машины не везут»."""
        if o.customer_id in self.customers_off:
            return CUSTOMER_OFF
        if not o.self_delivery:
            return FLEET
        if o.agent_id not in self.agents:
            return SELF_DELIVERY
        return OTHER_VEHICLE if self.needs_place(o) and self.matched_city(place(o.customer_id)) else FLEET


MAX_FLEET_IDS = 5000   # элементов в списках правила черновика — защита от битого черновика


def fleet_rule_of(draft: Draft | None, settings: Mapping[str, Any]) -> FleetRule:
    """Правило «чьи заказы везут машины» дня (№74, как agents_off_of): у дня есть черновик — правило, с которым его
    собрали (Draft.fleet: смена настроек не переписывает собранные дни), нет — из настроек."""
    return FleetRule.from_json(draft.fleet) if draft is not None else FleetRule.from_settings(settings)


NO_RULE = FleetRule()

# Подсказка к списку «машины не везут» (№74): клиенты без адреса или вне Армении. Рамка — Армения с запасом (широта,
# долгота от–до); адрес вне Армении — «ՌԴ»/«Россия» словом или кириллица без армянских букв («г. Краснодар, …»)
ARMENIA_BOX = ((38.8, 41.4), (43.4, 46.7))
_ABROAD_WORDS = re.compile(r'(?<!\w)(?:ռդ|ռուսաստան\w*|россия|рф)(?!\w)')
# адрес по-русски, но в Армении: «г. Ереван, …», «Армения, Армавир» — не «за границей»
_ARMENIA_RU = re.compile(r'(?<!\w)(?:ереван\w*|армени\w*|армавир\w*|гюмри|ванадзор|капан|горис|абовян|эчмиадзин|'
                         r'вагаршапат|раздан|масис|арташат|аштарак|севан|дилижан|иджеван|ехегнадзор|сисиан|степанаван|'
                         r'арарат|чаренцаван|гавар|алаверди|спитак|артик|мегри|каджаран|джермук|вайк|ноемберян)(?!\w)')
_CYRILLIC = re.compile('[а-я]')
_ARMENIAN = re.compile('[ա-և]')
NO_ADDRESS, ABROAD = 'no_address', 'abroad'


def hint_reason(addresses: Sequence[str], points: Sequence[tuple[float, float]]) -> str | None:
    """Почему подсказать клиента (адреса по умолчанию ERP: тексты и точки; точка 0,0 — не точка): ABROAD — точка вне
    ARMENIA_BOX или адрес за границей; NO_ADDRESS — ни текста, ни точки; иначе None."""
    (lat_lo, lat_hi), (lon_lo, lon_hi) = ARMENIA_BOX
    texts = [fold_text(a) for a in addresses if a.strip()]
    pts = [(lat, lon) for lat, lon in points if (lat, lon) != (0, 0)]
    if any(not (lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi) for lat, lon in pts) \
            or any(_ABROAD_WORDS.search(t) or (_CYRILLIC.search(t) and not _ARMENIAN.search(t)
                                               and not _ARMENIA_RU.search(t)) for t in texts):
        return ABROAD
    return None if texts or pts else NO_ADDRESS


@dataclass(frozen=True)
class Selection:
    main: list[DispatchOrder]            # заказы дня к доставке
    backlog: list[DispatchOrder]         # не отгружены с прошлых дней (в план — только по выбору логиста)
    shipped_before: int                  # заказов дня уже отгружено раньше дня развоза
    self_delivery: list[DispatchOrder]   # заказы дня, которые менеджер развозит сам
    same_day_taken: int = 0              # заказов дня, взятых в развоз дня их приёма (№72, views._day_orders)
    same_day_unread: bool = False        # план прошлого дня не прочитан: взятые в нём заказы могут прийти сюда повторно
    # №74: заказы дня менеджеров правила в городах-исключениях («այլ մեքենայով») и клиентов «машины не везут»
    other_vehicle: list[DispatchOrder] = field(default_factory=list)
    customers_off: list[DispatchOrder] = field(default_factory=list)


def to_deliver(orders: Sequence[DispatchOrder], day: date, since: date, rule: FleetRule = NO_RULE,
               place: Callable[[int], str] = _no_place) -> Selection:
    """Заказы к доставке в day: заказы дня (of_day: с даты since, заведённые заранее — в свою дату, №79) и раньше —
    «не отгружены с прошлых дней» (before_day); заказы дат позже (сам day — кроме заведённых заранее) — не этого дня.
    Отгруженные в day и позже — к доставке: для прошедшей даты это и есть то, что везли (или должны
    были везти). Машинам парка — только заказы FLEET правила rule (№74; place — клиент → place_text): заказы, которые
    менеджер развозит сам, везут другие машины или не везём вовсе, — отдельными списками (только заказы дня)."""
    key = lambda o: (o.customer_id, o.order_date, o.isn)   # noqa: E731
    pending = [o for o in orders if o.shipped is None or o.shipped >= day]
    kinds = [(o, rule.kind(o, place)) for o in pending]
    trucks = [o for o, k in kinds if k == FLEET]

    def of_kind(kind: str) -> list[DispatchOrder]:
        return sorted((o for o, k in kinds if k == kind and of_day(o, since, day)), key=key)
    return Selection(main=sorted((o for o in trucks if of_day(o, since, day)), key=key),
                     backlog=sorted((o for o in trucks if before_day(o, since)), key=key),
                     shipped_before=sum(1 for o in orders if of_day(o, since, day)) - len(
                         [o for o in pending if of_day(o, since, day)]),
                     self_delivery=of_kind(SELF_DELIVERY), other_vehicle=of_kind(OTHER_VEHICLE),
                     customers_off=of_kind(CUSTOMER_OFF))


@dataclass(frozen=True)
class PlanSeen:
    """Что видел план прошлого рабочего дня (№79): заказы его сборки, исключённые и перенесённые логистом, клиенты его
    рейсов (остановка — все заказы клиента дня)."""
    orders: frozenset[str] = frozenset()
    customers: frozenset[int] = frozenset()

    @classmethod
    def of(cls, draft: Draft | None) -> PlanSeen:
        if draft is None:
            return cls()
        return cls(frozenset(draft.built_orders or ()) | frozenset(draft.excluded) | frozenset(draft.deferred),
                   frozenset(c for t in draft.trips for c in t.stops))

    def has(self, o: DispatchOrder) -> bool:
        return o.isn in self.orders or o.customer_id in self.customers


def settle_predated(sel: Selection, since: date, seen: PlanSeen) -> Selection:
    """Не отгруженный заказ, заведённый заранее на since (прошлый рабочий день, №79), который план since видел (seen), —
    решён там: отвезли без накладной, исключили или перенесли («Везти завтра» вернёт его переносом) — в «не отгружены с
    прошлых дней», а не в развоз дня: иначе его повезли бы второй раз, пока нет накладной. Не видел — едет (of_day)."""
    done = [o for o in sel.main if o.predated and o.order_date == since and seen.has(o)]
    if not done:
        return sel
    ids = {o.isn for o in done}
    key = lambda o: (o.customer_id, o.order_date, o.isn)   # noqa: E731
    return replace(sel, main=[o for o in sel.main if o.isn not in ids], backlog=sorted([*sel.backlog, *done], key=key))


def same_day_candidates(orders: Sequence[DispatchOrder], day: date, rule: FleetRule = NO_RULE,
                        place: Callable[[int], str] = _no_place) -> list[DispatchOrder]:
    """Новые заказы дня day (№72): с датой day, не отгруженные раньше day, только для машин парка (FleetRule.kind, №74:
    не «везёт сам», не город-исключение, не клиент «машины не везут»). Это заказы развоза следующего рабочего дня — в
    развоз day только по выбору логиста (Draft.same_day). Заведённые заранее (№79) — не новые: они и так заказы day."""
    key = lambda o: (o.customer_id, o.order_date, o.isn)   # noqa: E731
    return sorted((o for o in orders if o.order_date == day and not o.predated
                   and (o.shipped is None or o.shipped >= day) and rule.kind(o, place) == FLEET), key=key)


@dataclass(frozen=True)
class SameDayData:
    """Заказы ERP с датой дня (№72) и когда их завели (DOCUMENTS.fCREATIONDATE: fISN → время; нет — не в словаре)."""
    orders: tuple[DispatchOrder, ...]
    created: dict[str, datetime]
    customers: dict[int, tuple[str, str]]          # клиент → (код, название)
    addresses: dict[int, str]
    loaded_at: datetime


# --- Свежесть заказов ---

def orders_still_coming(day: date, workdays: Sequence[int], now: datetime, ready_time: str,
                        holidays: Collection[date] = ()) -> bool:
    """Заказы на day ещё поступают: сегодня — последний день приёма заказов на day (предыдущий рабочий
    день, для понедельника — суббота) и сейчас раньше ready_time (ЧЧ:ММ). Прошедшая дата, выходной
    перед днём развоза или дата через несколько дней — False."""
    if now.date() != previous_workday(day, workdays, holidays):
        return False
    h, m = map(int, ready_time.split(':'))
    return now.time() < time(h, m)


def order_marks(orders: Sequence[DispatchOrder]) -> dict[str, tuple[float, float]]:
    """Отметка сборки: какие заказы (кг, сумма) логист видел, когда собирал рейсы."""
    return {o.isn: (o.kg, o.revenue) for o in orders}


def _totals(items: Sequence[tuple[float, float]]) -> dict[str, Any]:
    return {'count': len(items), 'kg': round(math.fsum(kg for kg, _ in items)),
            'revenue': round(math.fsum(rev for _, rev in items))}


def since_build(built: Mapping[str, tuple[float, float]] | None, main: Sequence[DispatchOrder],
                pending: Sequence[DispatchOrder], excluded: set[str]) -> dict[str, Any] | None:
    """Что изменилось с последней сборки. new — заказы дня (и перенесённые сюда из прошлого дня), которых
    при сборке не было (кроме тех, что логист уже исключил); removed — заказы сборки, которых больше нет
    в развозе дня (отменили, уже отгрузили, сняли перенос; их кг и сумма — из отметки). pending — заказы
    развоза дня: заказы дня и прошлых дней, что в нём. Отметки нет — None."""
    if built is None:
        return None
    new = [(o.kg, o.revenue) for o in main if o.isn not in built and o.isn not in excluded]
    alive = {o.isn for o in pending}
    removed = [v for k, v in sorted(built.items()) if k not in alive]
    return {'new': _totals(new), 'removed': _totals(removed)}


# --- Остановки ---

@dataclass(frozen=True)
class Stop:
    customer_id: int
    point: Point | None
    coord_source: str
    kg: float
    revenue: float
    orders: tuple[DispatchOrder, ...]
    agent_id: int            # менеджер самого тяжёлого заказа клиента


def build_stops(orders: Sequence[DispatchOrder], coord: Callable[[int], Coord]) -> list[Stop]:
    """Клиент с заказами дня — одна остановка; порядок — по коду клиента (детерминизм)."""
    by: dict[int, list[DispatchOrder]] = {}
    for o in orders:
        by.setdefault(o.customer_id, []).append(o)
    out = []
    for cid in sorted(by):
        os = by[cid]
        c = coord(cid)
        main = max(os, key=lambda o: (o.kg, o.revenue, o.isn))
        out.append(Stop(cid, c.point, c.source, math.fsum(o.kg for o in os), math.fsum(o.revenue for o in os),
                        tuple(os), main.agent_id))
    return out


# --- Черновик ---

@dataclass
class DraftTrip:
    id: int
    truck: str
    stops: list[int]          # клиенты по порядку объезда
    pinned: bool = False
    # новый рейс с заказами дня (№72): загрузка не раньше этого времени (минуты от полуночи); None — как обычно
    not_before: float | None = None
    # «Բեռնված է» (№78): {'at': ISO, 'by': логин, 'pin': закрепление держит отметка (не логист и не утверждение)};
    # None — не загружен
    loaded: dict[str, Any] | None = None


@dataclass
class Draft:
    trucks: list[str] = field(default_factory=list)       # машины дня (выбраны логистом)
    excluded: set[str] = field(default_factory=set)       # заказы «не везём сегодня» (fISN)
    added: set[str] = field(default_factory=set)          # заказы прошлых дней, добавленные логистом
    trips: list[DraftTrip] = field(default_factory=list)
    next_id: int = 1
    built_at: str | None = None
    # заказы последней сборки: fISN → (кг, сумма); None — черновик собран до этой отметки (сравнивать не с чем)
    built_orders: dict[str, tuple[float, float]] | None = None
    # клиенты, которых сборка не успела развезти до конца рабочего дня выбранными машинами
    no_room: set[int] = field(default_factory=set)
    # хоть одна машина в плане работает дольше рабочего дня (форс-мажор, ответ владельца №32) — runs_late;
    # пересчитывается при каждом сохранении (сборка, правка, «Везти после конца дня»); по ней — счётчик дней
    overtime: bool = False
    # логист нажал «Везти после конца дня»: рейсы до предела (truck_overtime_end) — принятая переработка,
    # а не ошибка; новая сборка сбрасывает
    overtime_ok: bool = False
    # заказы (fISN), перенесённые на следующий день доставки («Везти завтра», №25): следующий день сам
    # берёт их в развоз при загрузке (carried), искать их среди «не отгружены с прошлых дней» не нужно
    deferred: set[str] = field(default_factory=set)
    # перенесённые сюда из прошлого дня (carried), которые логист этого дня убрал из развоза
    dropped: set[str] = field(default_factory=set)
    # клиенты, которых сборка не поставила в рейс: не успеваем в окно приёма / в центре, а машины с правом
    # въезда сегодня нет (no_room — только «не успели до конца дня»); перенос в рейс убирает из всех трёх
    no_window: set[int] = field(default_factory=set)
    no_center: set[int] = field(default_factory=set)
    prediction: dict[str, Any] | None = None
    no_vehicle: set[int] = field(default_factory=set)
    # менеджеры (agent_id), чьи заказы сегодня не везём: фильтр «Մենեջերներ» — все их заказы дня и прошлых дней
    # вне развоза, пока менеджера не вернут (новые заказы этих менеджеров тоже); «не везём сегодня» — отдельно
    agents_off: set[int] = field(default_factory=set)
    # черновик до последнего «resize» (to_json без prediction и undo): «Չեղարկել» возвращает его (apply_edit «undo»);
    # любая другая правка, сборка и «Везти после конца дня» его сбрасывают — отменить можно только сам перенос
    undo: dict[str, Any] | None = None
    # заказы (fISN) с датой этого дня, которые логист взял в развоз этого же дня (№72); следующий день их не везёт
    same_day: set[str] = field(default_factory=set)
    # рейсы, закреплённые взятием заказов дня (не логистом): в них можно добавить и следующие новые заказы, пока не грузятся
    same_day_trips: set[int] = field(default_factory=set)
    # машины не из шага 1, отмеченные взятием заказов дня: без рейсов — снова не отмечены (release_same_day_trucks)
    same_day_trucks: set[str] = field(default_factory=set)
    # план дня утверждён (№73): {'at': когда (ISO), 'by': кто, 'pinned': [рейсы, которые закрепило утверждение —
    # до него они не были закреплены]}; None — не утверждён
    approved: dict[str, Any] | None = None
    # правило «чьи заказы везут машины», с которым день собран (№74, FleetRule.to_json); None — пустое правило
    fleet: dict[str, Any] | None = None
    # водителей меньше, чем машин (№77, build_crewed): seats — машина → водитель, которого сборка посадила вместо её
    # водителя (свой водитель машины — не здесь, его берут из «Վարորդ»); unmanned — отмеченные машины, которые сегодня не
    # выходят без водителя: машина → почему (UNMANNED: не вышел, ведёт другую машину, пересажен сборкой); absent — кто из
    # водителей дня не вышел при сборке. Имена людей — только странице, не в AI (как №62)
    seats: dict[str, str] = field(default_factory=dict)
    unmanned: dict[str, str] = field(default_factory=dict)
    absent: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        built = None if self.built_orders is None else {
            k: [round(kg, 3), round(rev, 2)] for k, (kg, rev) in sorted(self.built_orders.items())}
        return {'trucks': list(self.trucks), 'excluded': sorted(self.excluded), 'added': sorted(self.added),
                'trips': [{'id': t.id, 'truck': t.truck, 'stops': list(t.stops), 'pinned': t.pinned,
                           **({'not_before': t.not_before} if t.not_before is not None else {}),
                           **({'loaded': dict(t.loaded)} if t.loaded is not None else {})}
                          for t in self.trips],
                'next_id': self.next_id, 'built_at': self.built_at, 'built_orders': built,
                'no_room': sorted(self.no_room), 'overtime': self.overtime, 'overtime_ok': self.overtime_ok,
                'deferred': sorted(self.deferred), 'dropped': sorted(self.dropped),
                'no_window': sorted(self.no_window), 'no_center': sorted(self.no_center), 'prediction': self.prediction,
                'no_vehicle': sorted(self.no_vehicle), 'agents_off': sorted(self.agents_off),
                **({'undo': self.undo} if self.undo is not None else {}),
                **({'same_day': sorted(self.same_day)} if self.same_day else {}),
                **({'same_day_trips': sorted(self.same_day_trips)} if self.same_day_trips else {}),
                **({'same_day_trucks': sorted(self.same_day_trucks)} if self.same_day_trucks else {}),
                **({'approved': dict(self.approved)} if self.approved is not None else {}),
                **({'fleet': dict(self.fleet)} if self.fleet is not None else {}),
                **({'seats': dict(sorted(self.seats.items()))} if self.seats else {}),
                **({'unmanned': dict(sorted(self.unmanned.items()))} if self.unmanned else {}),
                **({'absent': sorted(self.absent)} if self.absent else {})}

    @classmethod
    def from_json(cls, raw: Any) -> Draft:
        """Черновик из базы; битые элементы пропускаются (черновик — не источник истины)."""
        if not isinstance(raw, dict):
            return cls()
        trucks = [t for t in raw.get('trucks') or [] if isinstance(t, str)]
        excluded = {x for x in raw.get('excluded') or [] if isinstance(x, str) and ISN_RE.match(x)}
        added = {x for x in raw.get('added') or [] if isinstance(x, str) and ISN_RE.match(x)}
        trips: list[DraftTrip] = []
        seen: set[int] = set()
        for t in (raw.get('trips') or [])[:MAX_TRIPS]:
            if not isinstance(t, dict):
                continue
            tid, truck, stops = t.get('id'), t.get('truck'), t.get('stops')
            if not _is_int(tid) or tid in seen or not isinstance(truck, str) or not isinstance(stops, list):
                continue
            seen.add(tid)
            nb = t.get('not_before')
            loaded = _loaded(t.get('loaded'))
            trips.append(DraftTrip(tid, truck, [c for c in stops if _is_int(c)], t.get('pinned') is True or loaded is not None,
                                   float(nb) if _is_num(nb) and 0 <= nb <= 2 * 24 * 60 else None, loaded))
        next_id = raw.get('next_id')
        next_id = max([next_id if _is_int(next_id) else 1, *(t.id + 1 for t in trips)])
        built = raw.get('built_at') if isinstance(raw.get('built_at'), str) else None
        def cids(key: str) -> set[int]:
            return {c for c in (raw.get(key) or [])[:MAX_BUILT_ORDERS] if _is_int(c)}

        def isns(key: str) -> set[str]:
            return {x for x in (raw.get(key) or [])[:MAX_BUILT_ORDERS] if isinstance(x, str) and ISN_RE.match(x)}
        return cls(trucks, excluded, added, trips, next_id, built, _built_orders(raw.get('built_orders')), cids('no_room'),
                   raw.get('overtime') is True, raw.get('overtime_ok') is True, isns('deferred'), isns('dropped'),
                   cids('no_window'), cids('no_center'), raw.get('prediction') if isinstance(raw.get('prediction'), dict) else None,
                   no_vehicle=cids('no_vehicle'),
                   agents_off={x for x in (raw.get('agents_off') or [])[:MAX_AGENTS] if _is_int(x)}
                   if isinstance(raw.get('agents_off'), list) else set(),
                   undo=raw.get('undo') if isinstance(raw.get('undo'), dict) else None, same_day=isns('same_day'),
                   same_day_trips=cids('same_day_trips'),
                   same_day_trucks={x for x in (raw.get('same_day_trucks') or [])[:MAX_TRIPS] if isinstance(x, str)}
                   if isinstance(raw.get('same_day_trucks'), list) else set(),
                   approved=_approved(raw.get('approved')), fleet=FleetRule.from_json(raw.get('fleet')).to_json(),
                   seats=_str_map(raw.get('seats')),
                   unmanned={k: v for k, v in _str_map(raw.get('unmanned')).items() if v in UNMANNED},
                   absent=sorted({x for x in (raw.get('absent') or [])[:MAX_TRIPS] if isinstance(x, str) and x})
                   if isinstance(raw.get('absent'), list) else [])


def _built_orders(raw: Any) -> dict[str, tuple[float, float]] | None:
    """Отметка сборки из черновика; нет её (старый черновик) или не словарь — None, битые строки — мимо."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, tuple[float, float]] = {}
    for k, v in list(raw.items())[:MAX_BUILT_ORDERS]:
        if isinstance(k, str) and ISN_RE.match(k) and isinstance(v, list) and len(v) == 2 \
                and all(_is_num(x) for x in v):
            out[k] = (float(v[0]), float(v[1]))
    return out


def _approved(raw: Any) -> dict[str, Any] | None:
    """Отметка утверждения плана из черновика (№73); битая — не утверждён."""
    if not isinstance(raw, dict) or not isinstance(raw.get('at'), str) or not isinstance(raw.get('pinned'), list):
        return None
    by = raw.get('by')
    return {'at': raw['at'], 'by': by if isinstance(by, str) else None,
            'pinned': sorted({x for x in raw['pinned'][:MAX_TRIPS] if _is_int(x)})}


def _loaded(raw: Any) -> dict[str, Any] | None:
    """Отметка «Բեռնված է» рейса из черновика (№78); битая — не загружен."""
    if not isinstance(raw, dict) or not isinstance(raw.get('at'), str):
        return None
    by = raw.get('by')
    return {'at': raw['at'], 'by': by if isinstance(by, str) else None, 'pin': raw.get('pin') is True}


def _str_map(raw: Any) -> dict[str, str]:
    """Словарь «код машины → строка» из черновика (№77: seats, unmanned); битые пары — мимо."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in list(raw.items())[:MAX_TRIPS] if isinstance(k, str) and isinstance(v, str) and v}


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_agents(raw: Any) -> set[int] | None:
    """Список менеджеров (agent_id) из запроса или черновика; не список целых, длиннее MAX_AGENTS — None."""
    if not isinstance(raw, list) or len(raw) > MAX_AGENTS or not all(_is_int(x) for x in raw):
        return None
    return set(raw)


# --- Контекст расчёта ---

@dataclass(frozen=True)
class DayContext:
    day: date
    depot: Point
    trucks: dict[str, fl.FleetTruck]     # машины, готовые к расчёту (активны, тоннаж и расход заданы)
    norms: Norms
    tn: fl.TruckNorms
    work_start_min: int                  # начало рабочего дня машины, минут от полуночи
    overtime_minutes: float | None = None   # форс-мажор: длина дня машины до truck_overtime_end; None — без предела
    min_trip_revenue: float = 0.0        # рейс дешевле — «бедный» (ответ владельца №25: везти сейчас или завтра)
    # окна приёма: клиент → (прибыть не раньше, не позже), минуты от полуночи (store.CustomerWindow.span)
    windows: Mapping[int, tuple[float, float]] = field(default_factory=dict)
    center_zone: tuple[Point, ...] = ()  # граница малого центра; пусто — центра нет
    vehicle_access: Mapping[int, VehicleAccess] = field(default_factory=dict)
    # что учитывает расчёт (views._dispatch_ctx: откуда км и минуты, выученные нормы) — только для пояснения на странице
    model: Mapping[str, Any] = field(default_factory=dict)
    end_reserve_min: float = 0.0         # запас в конце дня (№78): сборка — возврат не позже конца дня минус он


def morning_loading(day: date, settings: Mapping[str, Any]) -> bool:
    """День в сезоне утренней погрузки (ответы владельца №78, 6–7): ММ-ДД дня в отрезке [morning_loading_from,
    morning_loading_to] включительно; отрезок может переходить через Новый год (15.11–15.03). Вне сезона машины загружены с
    вечера (fl.TruckNorms.preload)."""
    lo, hi = settings['morning_loading_from'], settings['morning_loading_to']
    md = day.strftime('%m-%d')
    return lo <= md <= hi if lo <= hi else (md >= lo or md <= hi)


def _horizon(ctx: DayContext) -> fl.TruckNorms:
    """Нормы сборки (№78): рабочий день машины короче на запас в конце дня; без запаса — ctx.tn."""
    return replace(ctx.tn, work_minutes=ctx.tn.work_minutes - ctx.end_reserve_min) if ctx.end_reserve_min > 0 else ctx.tn


def _hhmm(minutes: float) -> str:
    """Минуты от полуночи дня доставки → «17:32»; следующие сутки — «04:06 (+1)»."""
    m = int(round(minutes))
    days = m // (24 * 60)
    text = f'{m // 60 % 24:02d}:{m % 60:02d}'
    return f'{text} (+{days})' if days else text


def _selected(ctx: DayContext, codes: Sequence[str]) -> list[fl.FleetTruck]:
    return [ctx.trucks[c] for c in sorted(set(codes)) if c in ctx.trucks]


def _shares(trips: Sequence[DraftTrip]) -> dict[int, int]:
    """Клиент → в скольких рейсах он встречается (тяжёлый заказ — несколько поездок поровну)."""
    n: dict[int, int] = {}
    for t in trips:
        for c in t.stops:
            n[c] = n.get(c, 0) + 1
    return n


def prune(draft: Draft, stops: Sequence[Stop]) -> None:
    """Рейсы черновика — по точкам дня stops (после правки, меняющей заказы дня): клиент без заказов или без точки
    уходит из рейсов, пустой рейс — тоже (как _clean перед расчётом, но для сохраняемого черновика)."""
    _clean(draft, {s.customer_id: s for s in stops if s.point is not None})
    draft.same_day_trips &= {t.id for t in draft.trips}   # отметки рейсов, которых больше нет, — не хранятся (№72)
    if draft.approved is not None:                        # и рейсов, закреплённых утверждением (№73)
        draft.approved = {**draft.approved, 'pinned': [i for i in draft.approved['pinned']
                                                       if any(t.id == i for t in draft.trips)]}


def _clean(draft: Draft, routable: Mapping[int, Stop]) -> None:
    """Из рейсов уходят клиенты, которых нет среди заказов с координатами; пустые рейсы — тоже.
    Повтор клиента в одном рейсе схлопывается."""
    for t in draft.trips:
        seen: set[int] = set()
        t.stops = [c for c in t.stops if c in routable and not (c in seen or seen.add(c))]
    draft.trips = [t for t in draft.trips if t.stops]


def _span(ctx: DayContext, cid: int) -> fl.Window:
    """Окно приёма клиента в минутах от начала дня машины; без окна — (−∞, +∞)."""
    lo, hi = ctx.windows.get(cid, (-math.inf, math.inf))
    return lo - ctx.work_start_min, hi - ctx.work_start_min


def _central(ctx: DayContext, s: Stop) -> bool:
    """Точка в малом центре (везёт только машина с правом въезда)."""
    return bool(ctx.center_zone) and s.point is not None and in_polygon(s.point, ctx.center_zone)


def _yerevan(ctx: DayContext, s: Stop) -> bool:
    """Точка в зоне Еревана (№68: большая машина везёт её после малых и дольше)."""
    return s.point is not None and ctx.tn.in_yerevan(s.point)


def big_shown(ctx: DayContext, truck: fl.FleetTruck | None) -> bool:
    """Показывать ли «большая машина» (№68): машина большая и зона Еревана задана (пустая — правила нет, план прежний)."""
    return truck is not None and truck.big and len(ctx.tn.yerevan_zone) >= 3


def _yerevan_km(ctx: DayContext, code: str, s: Stop) -> float:
    """Плата за точку s на машине code (№68, приоритет малых машин): точка в зоне Еревана на большой машине — как
    ctx.tn.yerevan_km км пути (как в сборке, fleet._yerevan_bias); иначе 0.0."""
    return ctx.tn.yerevan_km if big_shown(ctx, ctx.trucks.get(code)) and _yerevan(ctx, s) else 0.0


def _allowed_trucks(ctx: DayContext, cid: int) -> frozenset[str] | None:
    rule = ctx.vehicle_access.get(cid)
    return None if rule is None else frozenset(code for code in ctx.trucks if rule.allows(code))


def _vehicle_ok(ctx: DayContext, cid: int, code: str) -> bool:
    rule = ctx.vehicle_access.get(cid)
    return rule is None or rule.allows(code)


def _require_vehicle(ctx: DayContext, cids: Sequence[int], code: str) -> None:
    if any(not _vehicle_ok(ctx, cid, code) for cid in cids):
        raise DispatchError('Эта машина не может обслуживать магазин: выберите разрешённую машину. '
                            'Для закреплённого рейса сначала измените машину или снимите закрепление.')


def _route(ctx: DayContext, cids: Sequence[int], stops: Mapping[int, Stop], shares: Mapping[int, int],
           reorder: bool, start: float = 0.0, truck: str | None = None) -> tuple[list[int], float, float, float]:
    """(порядок клиентов, км, минуты, кг) рейса. reorder с выезда start: если порядок соблюдает окна приёма,
    2-opt их не нарушит (окна — с темпом машины truck, №66)."""
    kgs = [stops[c].kg / shares.get(c, 1) for c in cids]
    windows = [_span(ctx, c) for c in cids] if reorder and any(c in ctx.windows for c in cids) else None
    seq, km, minutes = fl.route_trip([stops[c].point for c in cids], kgs, ctx.depot, ctx.norms, ctx.tn,
                                     reorder=reorder, windows=windows, start=start, truck=truck)
    return [cids[i] for i in seq], km, minutes, math.fsum(kgs)


def _timeline(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop],
              shares: Mapping[int, int], parts: dict[int, dict[str, Any]] | None = None, open_end: bool = False
              ) -> dict[int, tuple[float, float, list[float]]]:
    """Рейсы машин подряд, с ожиданием у окон приёма: рейс → (выезд, минуты рейса от выезда, прибытия к точкам),
    время — минуты от начала дня машины. Рейс выезжает, как только машина вернулась, но не раньше, чем нужно к
    окну первой точки (fl.trip_schedule) — как его поставил fleet._plan_timed. Без окон — подряд, минуты те же,
    что у _route. parts — рейс → слагаемые его минут (fl.trip_schedule: загрузка, езда, ожидание, разгрузка; при обеде —
    и 'lunch': fl.Break или None). Обед в пути (№61, ctx.tn.lunch_minutes) — по правилу fleet (шапка модуля): один на
    день машины; open_end — за последним рейсом машины будут ещё рейсы (занятое время для раскладки вокруг: обед может
    встать и после его последней точки). Запас на рейс и темп машины (№66, ctx.tn) — по правилу fleet: минуты рейса — с
    запасом в конце (parts['buffer']), прибытия — без него."""
    used: dict[str, float] = {}
    out: dict[int, tuple[float, float, list[float]]] = {}
    lunch = ctx.tn.lunch_minutes > 0
    last = {t.truck: t.id for t in trips if any(c in stops for c in t.stops)}
    eaten: set[str] = set()
    for t in trips:
        cids = [c for c in t.stops if c in stops]
        if not cids:
            continue
        got: dict[str, Any] | None = {} if parts is not None or lunch else None
        free = used.get(t.truck, 0.0)
        kgs = [stops[c].kg / shares.get(c, 1) for c in cids]
        pre = t.not_before is None and t.truck not in used and ctx.tn.preload
        if t.not_before is not None:   # новый рейс с заказами дня (№72) — не раньше «сейчас»
            free = max(free, t.not_before - ctx.work_start_min)
        elif pre:   # №78: первый рейс машины вне сезона загружен с вечера — его загрузка до начала дня
            free = 0.0 - ctx.tn.load(math.fsum(kgs))
        depart, arrivals, minutes = fl.trip_schedule(
            [stops[c].point for c in cids], kgs, ctx.depot, ctx.norms,
            ctx.tn, free, [_span(ctx, c) for c in cids], got,
            (t.truck not in eaten, open_end or last[t.truck] != t.id) if lunch else None, t.truck)
        if pre and got is not None:
            got['preloaded'] = True
        if lunch and got.get('lunch') is not None:   # type: ignore[union-attr]
            eaten.add(t.truck)
        used[t.truck] = depart + minutes
        out[t.id] = (depart, minutes, arrivals)
        if parts is not None:
            parts[t.id] = got
    return out


def _occupied(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop], shares: Mapping[int, int],
              fed: set[str] | None = None
              ) -> tuple[dict[str, float], dict[str, list[tuple[float, float]]] | None, dict[int, float]]:
    """Чем машины уже заняты (рейсы trips по _timeline): (конец последнего рейса машины; занятые отрезки
    [выезд, возвращение] — только если между ними есть промежуток (рейс выезжает позже под окно первой точки),
    иначе None — день занят одним куском, как раньше; выезд каждого рейса). За рейсами встанут новые — обед (№61) по
    правилу с запасом (open_end); fed — сюда машины, чей обед уже в этих рейсах."""
    found: dict[int, dict[str, Any]] = {}
    tl = _timeline(ctx, trips, stops, shares, found, open_end=True)
    used: dict[str, float] = {}
    spans: dict[str, list[tuple[float, float]]] = {}
    gap = False
    for t in trips:
        if t.id in tl:
            depart, minutes, _ = tl[t.id]
            gap = gap or depart > used.get(t.truck, 0.0) + _EPS
            used[t.truck] = depart + minutes
            spans.setdefault(t.truck, []).append((depart, depart + minutes))
    if fed is not None:
        fed.update(t.truck for t in trips if (found.get(t.id) or {}).get('lunch') is not None)
    return used, (spans if gap else None), {tid: v[0] for tid, v in tl.items()}


def _by_departure(old: list[DraftTrip], old_departs: Mapping[int, float], new: list[DraftTrip],
                  new_departs: Sequence[float]) -> list[DraftTrip]:
    """Рейсы черновика: прежние + новые. Расчёт с окнами (new_departs есть) мог поставить новый рейс в промежуток
    до прежнего — рейсы машины идут по времени выезда, иначе _timeline повёз бы их не в том порядке."""
    if not new_departs:
        return old + new
    when = {**{t.id: old_departs[t.id] for t in old}, **{t.id: dep for t, dep in zip(new, new_departs)}}
    return sorted(old + new, key=lambda t: when[t.id])


# --- Сборка рейсов ---

def _plan_around(ctx: DayContext, sel: Sequence[fl.FleetTruck], routable: Mapping[int, Stop], keep: list[DraftTrip],
                 was: Mapping[int, tuple[float, float, list[float]]], next_id: int, iterations: int = vrp.ITERATIONS
                 ) -> tuple[list[DraftTrip], list[Stop], dict[int, str], int]:
    """Раскладка с окнами и центром вокруг готовых рейсов keep (fleet.route_day, fixed): их машина и состав не
    меняются, время — их выезд в прежнем плане (was), если там свободно; остальные магазины раскладываются вокруг.
    (рейсы по времени выезда, магазины вне keep, причины неназначенных — номер в них, следующий id). iterations — как у build."""
    shares = _shares(keep)
    rest = [routable[c] for c in sorted(routable) if c not in shares]
    pts, kgs, revs = [s.point for s in rest], [s.kg for s in rest], [s.revenue for s in rest]
    wins, cen = [_span(ctx, s.customer_id) for s in rest], [_central(ctx, s) for s in rest]
    access = [_allowed_trucks(ctx, s.customer_id) for s in rest]
    fixed: list[tuple[str, list[int], float]] = []
    for t in keep:
        fixed.append((t.truck, list(range(len(pts), len(pts) + len(t.stops))), was[t.id][0] if t.id in was else 0.0))
        for c in t.stops:
            s = routable[c]
            pts.append(s.point)
            kgs.append(s.kg / shares[c])
            revs.append(s.revenue / shares[c])
            wins.append(_span(ctx, c))
            cen.append(_central(ctx, s))
            access.append(_allowed_trucks(ctx, c))
    reasons: dict[int, str] = {}
    trips = fl.route_day(pts, kgs, revs, ctx.depot, sel, ctx.norms, _horizon(ctx), overflow=False, windows=wins, center=cen,
                         reasons=reasons, fixed=fixed, balance=True, solver=True, allowed_trucks=access, iterations=iterations)
    own = {tuple(idx): t for t, (_, idx, _) in zip(keep, fixed)}
    out: list[DraftTrip] = []
    for t in trips:
        if t.items in own:
            out.append(own[t.items])
        else:
            out.append(DraftTrip(next_id, t.truck, [rest[i].customer_id for i in t.items]))
            next_id += 1
    return out, rest, reasons, next_id


def build(ctx: DayContext, stops: Sequence[Stop], old: Draft | None, trucks: Sequence[str],
          now: str, iterations: int = vrp.ITERATIONS) -> Draft:
    """«Собрать рейсы»: заказы с координатами (кроме исключённых) → рейсы выбранных машин.
    Закреплённые логистом рейсы прежнего черновика остаются как есть (если их машина работает), их время
    машина уже занята; остальное раскладывается заново. За конец рабочего дня машин рейсы не планируются:
    что не успевают выбранные машины, остаётся вне рейсов (no_room) — логист добавляет машину или
    решает сам. Окна приёма и малый центр соблюдаются: не помещается в окно — no_window, в центре без
    машины с правом въезда — no_center. С окнами или центром закреплённые рейсы остаются и на своём прежнем
    времени, если там свободно и окна соблюдаются («после 15:00» — в конце дня, утро — другим рейсам), иначе — в
    самый ранний промежуток, где укладываются; остальное раскладывается заново вокруг них.
    Как и раньше: закреплён один рейс тяжёлого заказа на несколько поездок — в его рейсе заказ показывается
    целиком (доля считается по рейсам черновика, остальные поездки при пересборке не закреплены).
    iterations — итераций решателя PyVRP: меньше vrp.ITERATIONS — быстрая проба набора машин (build_crewed, №77)."""
    old = old or Draft()
    routable = {s.customer_id: s for s in stops if s.point is not None}
    sel = _selected(ctx, trucks)
    codes = {t.car_code for t in sel}
    draft = Draft(trucks=sorted(codes), excluded=set(old.excluded), added=set(old.added), next_id=old.next_id,
                  built_at=now, deferred=set(old.deferred), dropped=set(old.dropped), agents_off=set(old.agents_off),
                  same_day=set(old.same_day), same_day_trips=set(old.same_day_trips),
                  fleet=dict(old.fleet) if old.fleet is not None else None)
    pinned = [DraftTrip(t.id, t.truck, list(t.stops), True, t.not_before, t.loaded)
              for t in old.trips if t.pinned and t.truck in codes]
    tmp = Draft(trips=pinned)
    _clean(tmp, routable)
    pinned = tmp.trips
    for trip in pinned:
        _require_vehicle(ctx, trip.stops, trip.truck)
    timed = any(c in ctx.windows or _central(ctx, s) for c, s in routable.items())
    # закреплённый рейс с заказами дня (№72) грузится не раньше «сейчас»: машина свободна и до него — раскладка вокруг его
    # времени (иначе машина была бы занята с утра до его конца и утренние рейсы ушли бы за него)
    timed = timed or any(t.not_before is not None for t in pinned)
    if timed and pinned and sel:
        was = _timeline(ctx, old.trips, routable, _shares(old.trips))
        draft.trips, rest, reasons, draft.next_id = _plan_around(ctx, sel, routable, pinned, was, draft.next_id, iterations)
    else:
        shares = _shares(pinned)
        rest = [routable[c] for c in sorted(routable) if c not in shares]
        reasons = {}
        fed: set[str] = set()
        used, _, _ = _occupied(ctx, pinned, routable, shares, fed)
        trips = fl.route_day([s.point for s in rest], [s.kg for s in rest], [s.revenue for s in rest],
                             ctx.depot, sel, ctx.norms, _horizon(ctx), used, overflow=False,
                             windows=[_span(ctx, s.customer_id) for s in rest], center=[_central(ctx, s) for s in rest],
                             reasons=reasons, balance=True, solver=True,
                             allowed_trucks=[_allowed_trucks(ctx, s.customer_id) for s in rest], fed=fed,
                             iterations=iterations) if rest and sel else []
        draft.trips = list(pinned)
        for t in trips:
            draft.trips.append(DraftTrip(draft.next_id, t.truck, [rest[i].customer_id for i in t.items]))
            draft.next_id += 1
    placed = {c for t in draft.trips for c in t.stops}
    draft.same_day_trips &= {t.id for t in draft.trips}   # рейс взятия, ушедший при пересборке, — без отметки (№72)
    if sel:
        for i, s in enumerate(rest):
            if s.customer_id not in placed:
                {'window': draft.no_window, 'center': draft.no_center, 'vehicle': draft.no_vehicle}.get(
                    reasons.get(i), draft.no_room).add(s.customer_id)
    return draft


# --- Водителей меньше, чем машин (ответ владельца №77) ---

# почему отмеченная машина сегодня без водителя (Draft.unmanned): её водитель не вышел; ведёт другую отмеченную машину
# (логист поставил его туда на этот день); пересажен сборкой на другую машину — так день лучше (_gain)
UNMANNED = ('absent', 'busy', 'moved')
# Проба набора машин — сборка с коротким решателем (итераций как у самого малого дня, vrp.MIN_ITERATIONS). Замер на копии
# базы (01–05.10.2026, 4 дня × 7 наборов): проба 1,2–3,4 с против 10–90 с итога; магазинов вне рейсов — как у итога во
# всех 28 наборах (сборка без решателя теряла 6–33 магазина, которые итог развозит, и выбирала машину до +11,9% ֏ дня).
SEAT_TRIAL_ITERATIONS = vrp.MIN_ITERATIONS
# Наборов машин для свободных водителей не больше — перебор всех, больше — по одной машине (жадно).
SEAT_EXHAUSTIVE_MAX = 20
# Проб на всю рассадку (выбор машин и пересадки) не больше: при пробе до 3,4 с — не дольше ~1,5 мин сверх итоговой
# сборки. Число, а не секунды: рассадка воспроизводима (тот же день — те же машины), как и решатель (vrp.ITERATIONS).
SEAT_TRIALS_MAX = 25
# Водитель пересаживается со своей машины на машину без водителя, только если день (֏ расхода по пробе) дешевле хотя бы
# на столько. Тот же замер: разница ֏ двух наборов по пробе и по итогу расходится в медиане на 2,9% (шум решателя), в
# 90% пар — не больше 7,6%, максимум 9,2%; меньший выигрыш — шум, а пересадка — неудобство людям.
SWAP_MIN_GAIN = 0.08


@dataclass(frozen=True)
class Crew:
    """Водители дня для сборки (№77). own — машина → её водитель на день (store.truck_drivers: постоянный или подмена дня);
    manual — машины, чей водитель на этот день выбран логистом (подмена в «Վարորդ»): сборка его оттуда не снимает;
    absent — водители дня, которые не вышли (store.driver_absences). Машина без водителя в own — едет, как до №77."""
    own: Mapping[str, str] = field(default_factory=dict)
    manual: frozenset[str] = frozenset()
    absent: frozenset[str] = frozenset()


def seating(crew: Crew, codes: Collection[str]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Кто за рулём машин codes без пересадок: (машина → свой водитель; машины без своего водителя → почему: 'absent' — не
    вышел, 'busy' — уже за рулём другой из codes; свободные водители — вышли, но ни одной их машины среди codes нет).
    Человек ведёт одну машину: первой — та, куда его поставил логист (manual), затем по коду. Машина без водителя в own —
    ни там, ни там: едет."""
    own: dict[str, str] = {}
    orphans: dict[str, str] = {}
    for code in sorted(codes, key=lambda c: (c not in crew.manual, c)):
        name = crew.own.get(code)
        if not name:
            continue
        if name in crew.absent or name in own.values():
            orphans[code] = 'absent' if name in crew.absent else 'busy'
        else:
            own[code] = name
    free = sorted({n for n in crew.own.values() if n and n not in crew.absent} - set(own.values()))
    return own, dict(sorted(orphans.items())), free


def _shortfall(ctx: DayContext, routable: Mapping[int, Stop], draft: Draft) -> tuple[int, float, float]:
    """Чем build_crewed сравнивает наборы машин: (магазинов вне рейсов, их кг до 0,5 кг, ֏ расхода рейсов — дизель и
    износ, как operating_cost_amd плана). Меньше — лучше: сначала всё увезти, потом дешевле."""
    left = draft.no_room | draft.no_window | draft.no_center | draft.no_vehicle
    shares = _shares(draft.trips)
    amd = math.fsum(_trip_amd(ctx, t.stops, routable, shares, t.truck) for t in draft.trips if t.truck in ctx.trucks)
    return len(left), round(math.fsum(routable[c].kg for c in left) * 2) / 2, amd


def _profile(ctx: DayContext, routable: Mapping[int, Stop], code: str) -> tuple[Any, ...]:
    """Всё, чем машина code отличается в сборке, кроме кода: тоннаж, расход, износ, центр, «большая» (№68), выученный темп
    (№66), надбавка в Ереване и допуск к магазинам дня. Одинаковые машины build_crewed пробует один раз."""
    t = ctx.trucks[code]
    return (replace(t, car_code='', name=None), ctx.tn.pace_of(code), ctx.tn.yerevan_of(code),
            tuple(_vehicle_ok(ctx, c, code) for c in sorted(routable) if c in ctx.vehicle_access))


def _gain(new: tuple[int, float, float], cur: tuple[int, float, float]) -> bool:
    """Пересадка выгодна: увозит больше (магазинов, затем кг) или столько же, а ֏ дня меньше хотя бы на SWAP_MIN_GAIN."""
    return new[:2] < cur[:2] if new[:2] != cur[:2] else new[2] <= cur[2] * (1.0 - SWAP_MIN_GAIN)


def build_crewed(ctx: DayContext, stops: Sequence[Stop], old: Draft | None, trucks: Sequence[str], now: str,
                 crew: Crew, timing: dict[str, Any] | None = None) -> Draft:
    """«Собрать рейсы» с водителями дня (ответ владельца №77: «Система сама», «Своя машина + подмена»): машин в рейсах не
    больше, чем вышло водителей. trucks — отмеченные машины (исправны и могут выйти, №70–71), crew — водители дня.
    - отмеченная машина, чей водитель вышел, едет с ним (seating); машина без водителя в «Վարորդ» — едет, как до №77;
    - свободные водители (вышли, а их машины не отмечены) садятся на отмеченные машины без своего водителя: сначала на
      машины с закреплёнными рейсами (рейсы остаются; водителя не хватило — машина едет, страница предупреждает), затем на
      лучшие для заказов дня — по пробной сборке с коротким решателем (SEAT_TRIAL_ITERATIONS): меньше магазинов вне
      рейсов, затем меньше ֏ расхода (_shortfall). Наборов (одинаковые машины — один, _profile) не больше
      SEAT_EXHAUSTIVE_MAX — перебор всех, иначе по одной машине; водители по именам садятся на выбранные машины по кодам;
    - «выгоднее оставить свою»: водитель своей машины (не поставленный логистом, без закреплённых рейсов) пересаживается на
      отмеченную машину без водителя, только если день так заметно лучше (_gain); его машина — 'moved';
    - проб на всё не больше SEAT_TRIALS_MAX: кончились — берётся лучшее из опробованного (недобранные машины выбора — по
      коду, пересадки — лучшая найденная выгодная);
    - итог — сборка (build) выбранными машинами с полным решателем. Draft.seats — пересаженные, Draft.unmanned — отмеченные
      машины без водителя, Draft.absent — кто не вышел. Машин без водителя нет — сборка ровно отмеченными, как build, до
      байта. timing — сюда {'trials': проб, 'seconds': время до итоговой сборки} (замер).
    Детерминированно: кандидаты, ничьи и рассадка — по коду и имени."""
    started = perf_counter()
    old = old or Draft()
    codes = sorted(set(trucks) & set(ctx.trucks))
    own, orphans, free = seating(crew, codes)
    pinned = {t.truck for t in old.trips if t.pinned}
    forced = [c for c in orphans if c in pinned]
    rest = [c for c in orphans if c not in pinned]
    seats = dict(zip(forced, free))
    pool = free[len(forced):]
    fleet = [c for c in codes if c not in orphans] + forced
    routable = {s.customer_id: s for s in stops if s.point is not None}
    tried: dict[tuple[str, ...], tuple[int, float, float]] = {}

    def trial(fleet_: Sequence[str]) -> tuple[int, float, float] | None:
        """Проба набора; проб больше нет (SEAT_TRIALS_MAX) и набор не опробован — None."""
        key = tuple(sorted(fleet_))
        if key not in tried and len(tried) < SEAT_TRIALS_MAX:
            tried[key] = _shortfall(ctx, routable, build(ctx, stops, old, key, now, SEAT_TRIAL_ITERATIONS))
        return tried.get(key)

    def best(options: Sequence[Any], fleet_of: Callable[[Any], Sequence[str]]) -> Any:
        """Вариант с лучшей пробой (ничья — меньший); опробовать нечего — первый."""
        got = [(r, o) for o in options if (r := trial(fleet_of(o))) is not None]
        return min(got)[1] if got else options[0]

    k = min(len(pool), len(rest))
    chosen = list(rest) if k == len(rest) else []
    if 0 < k < len(rest):
        kinds: dict[tuple[Any, ...], list[str]] = {}
        for c in rest:
            kinds.setdefault(_profile(ctx, routable, c), []).append(c)
        groups = list(kinds.values())

        def canon(pick: Sequence[str]) -> tuple[str, ...]:   # из одинаковых машин — первые по коду
            return tuple(sorted(c for g in groups for c in g[:sum(1 for x in pick if x in g)]))

        sets = sorted({canon(pick) for pick in combinations(rest, k)})
        if len(sets) == 1:                   # выбирать не из чего — без пробы
            chosen = list(sets[0])
        elif len(sets) <= min(SEAT_EXHAUSTIVE_MAX, SEAT_TRIALS_MAX):
            chosen = list(best(sets, lambda pick: [*fleet, *pick]))
        else:
            for _ in range(k):
                heads = [g[0] for g in ([c for c in g if c not in chosen] for g in groups) if g]
                chosen.append(best(heads, lambda c: [*fleet, *chosen, c]))
    seats.update(zip(sorted(chosen), pool))
    fleet += chosen
    unmanned = {c: orphans[c] for c in rest if c not in chosen}
    movable = [c for c in own if c not in crew.manual and c not in pinned]
    cur = trial(fleet) if unmanned and movable else None
    while cur is not None and movable:
        pairs: dict[tuple[Any, ...], tuple[str, str]] = {}
        for a in movable:
            for b in sorted(unmanned):
                pa, pb = _profile(ctx, routable, a), _profile(ctx, routable, b)
                if pa != pb:                 # одинаковые машины — пересадка ничего не меняет
                    pairs.setdefault((pa, pb), (a, b))
        found = None
        for a, b in sorted(pairs.values()):
            got = trial([b if c == a else c for c in fleet])
            if got is not None and _gain(got, cur) and (found is None or (got, a, b) < found):
                found = (got, a, b)
        if found is None:
            break
        cur, a, b = found
        fleet = [b if c == a else c for c in fleet]
        seats[b] = own.pop(a)
        movable.remove(a)
        del unmanned[b]
        unmanned[a] = 'moved'
    if timing is not None:
        timing.update(trials=len(tried), seconds=perf_counter() - started)
    draft = build(ctx, stops, old, fleet if orphans else trucks, now)
    draft.seats, draft.unmanned, draft.absent = seats, unmanned, sorted(crew.absent)
    return draft


def crew_view(draft: Draft, crew: Crew) -> dict[str, dict[str, Any]]:
    """Кто ведёт машины дня (№77) — только странице и Բեռնագիր: машина → {'name': водитель или None, 'seat': его посадила
    сборка вместо водителя машины («փոխարինում»), 'warn': None | 'none' — водитель не указан | 'absent' — водитель не вышел
    (закреплённые рейсы машины остались; в Բեռնագիր — пустая строка) | 'twice' — этот человек ведёт и другую машину дня,
    'stale': посадка сборки уже не нужна — у машины снова свой водитель (его сменили в «Վարորդ» после сборки), пересобрать}.
    Посадка сборки действует, пока свой водитель машины не вышел или ведёт другую машину дня; выбор логиста на этот день
    (crew.manual) главнее её."""
    codes = sorted({*draft.trucks, *(t.truck for t in draft.trips)})
    own_driving = {crew.own[c] for c in codes if crew.own.get(c) and c not in draft.seats}
    out: dict[str, dict[str, Any]] = {}
    for code in codes:
        name = crew.own.get(code)
        needed = not name or name in crew.absent or name in own_driving or name in draft.seats.values()
        seat = code in draft.seats and needed and not (code in crew.manual and name and name not in crew.absent)
        out[code] = {'name': (draft.seats[code] if seat else name) or None, 'seat': seat,
                     'stale': code in draft.seats and not needed and code not in crew.manual}
    count: dict[str, int] = {}
    for v in out.values():
        if v['name']:
            count[v['name']] = count.get(v['name'], 0) + 1
    for v in out.values():
        v['warn'] = ('none' if not v['name'] else 'absent' if v['name'] in crew.absent
                     else 'twice' if count[v['name']] > 1 else None)
    return out


def spare_trucks(ctx: DayContext, draft: Draft, crew: Crew | None) -> list[str]:
    """Готовые машины не из плана дня, которые могут выйти (№77) — для совета «добавить машину» (№54) и новых заказов дня
    (№72): не машина дня, не отмеченная без водителя (unmanned), и её водитель (если указан) вышел и не ведёт машину дня —
    своей или посаженный сборкой. crew нет (водители не известны) — только первые два условия."""
    driving = {v['name'] for v in crew_view(draft, crew).values()} if crew is not None else set()
    out = []
    for code in sorted(ctx.trucks):
        name = crew.own.get(code) if crew is not None else None
        if code not in draft.trucks and code not in draft.unmanned and not (
                name and (name in crew.absent or name in driving)):
            out.append(code)
    return out


def overtime(ctx: DayContext, stops: Sequence[Stop], draft: Draft) -> Draft:
    """«Везти после конца дня» (форс-мажор, ответ владельца №32): магазины, не поместившиеся до конца
    рабочего дня (no_room) и всё ещё вне рейсов, раскладываются по машинам дня поверх их рейсов — с
    переработкой, но не позже предела (ctx.overtime_minutes, настройка truck_overtime_end); что не
    успевает и к пределу — остаётся «не поместились». Рейсы дня не меняются. Окна приёма и центр — как при
    сборке; не помещавшиеся в окно тоже пробуются: «после 17:30» могло не поместиться только до конца дня."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    _clean(draft, routable)
    draft.undo = None
    shares = _shares(draft.trips)
    sel = _selected(ctx, draft.trucks)
    rest = [routable[c] for c in sorted(draft.no_room | draft.no_window | draft.no_vehicle) if c in routable and c not in shares]
    draft.overtime_ok = True
    if not rest or not sel:
        return draft
    fed: set[str] = set()
    used, busy, day_departs = _occupied(ctx, draft.trips, routable, shares, fed)
    # предел переработки; конец обычного дня — для приоритета малых машин в Ереване (№68, fleet._earliest_key)
    limit = (replace(ctx.tn, work_minutes=ctx.overtime_minutes, normal_minutes=ctx.tn.work_minutes)
             if ctx.overtime_minutes is not None else ctx.tn)
    reasons: dict[int, str] = {}
    departs: list[float] = []
    trips = fl.route_day([s.point for s in rest], [s.kg for s in rest], [s.revenue for s in rest],
                         ctx.depot, sel, ctx.norms, limit, used, overflow=ctx.overtime_minutes is None,
                         earliest=True, windows=[_span(ctx, s.customer_id) for s in rest],
                         center=[_central(ctx, s) for s in rest], reasons=reasons, busy=busy, departs=departs,
                         load_cap=fl.LOAD_CAP, allowed_trucks=[_allowed_trucks(ctx, s.customer_id) for s in rest], fed=fed)
    new = []
    for t in trips:
        new.append(DraftTrip(draft.next_id, t.truck, [rest[i].customer_id for i in t.items]))
        draft.next_id += 1
    draft.trips = _by_departure(draft.trips, day_departs, new, departs)
    placed = {c for t in draft.trips for c in t.stops}
    # что не поместилось и с переработкой — по причине этой раскладки: окно, центр или время
    for i, s in enumerate(rest):
        cid = s.customer_id
        for left in (draft.no_room, draft.no_window, draft.no_center, draft.no_vehicle):
            left.discard(cid)
        if cid not in placed:
            {'window': draft.no_window, 'center': draft.no_center, 'vehicle': draft.no_vehicle}.get(
                reasons.get(i), draft.no_room).add(cid)
    return draft


def runs_late(ctx: DayContext, stops: Sequence[Stop], draft: Draft) -> bool:
    """Хоть одна машина плана работает дольше рабочего дня (рейсы подряд с ожиданием у окон и обедом, как в plan_view)."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    tl = _timeline(ctx, draft.trips, routable, _shares(draft.trips))
    return any(depart + minutes > ctx.tn.work_minutes + _EPS for depart, minutes, _ in tl.values())


# --- Правки логиста ---

def _trip(draft: Draft, tid: Any) -> DraftTrip:
    for t in draft.trips:
        if t.id == tid:
            return t
    raise DispatchError('Рейс не найден — обновите страницу')


trip_of = _trip   # рейс черновика по id (views: склад, №78); нет — DispatchError


def _insert_cheapest(ctx: DayContext, cids: list[int], cid: int, stops: Mapping[int, Stop]) -> list[int]:
    """Вставить клиента в рейс там, где объезд удлиняется меньше всего."""
    km = ctx.norms.km
    pts = [ctx.depot, *(stops[c].point for c in cids), ctx.depot]
    p = stops[cid].point
    best = min(range(len(pts) - 1), key=lambda i: (km(pts[i], p) + km(p, pts[i + 1]) - km(pts[i], pts[i + 1]), i))
    return cids[:best] + [cid] + cids[best:]


# --- Конец рейса тянут мышью на шкале дня (ответ владельца №59) ---

def _closed_km(ctx: DayContext, cids: Sequence[int], stops: Mapping[int, Stop]) -> float:
    """Км рейса «склад → cids по порядку → склад» (без рейса — 0)."""
    if not cids:
        return 0.0
    pts = [ctx.depot, *(stops[c].point for c in cids), ctx.depot]
    return math.fsum(ctx.norms.km(a, b) for a, b in zip(pts, pts[1:]))


def _truck_day(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop], shares: Mapping[int, int],
               code: str) -> tuple[dict[int, float], float, int]:
    """Рейсы машины code подряд (_timeline): (возвращение каждого рейса, конец дня машины, точек позже окна приёма) —
    минуты от начала дня машины."""
    mine = [t for t in trips if t.truck == code]
    tl = _timeline(ctx, mine, stops, shares)
    ends: dict[int, float] = {}
    misses = 0
    for t in mine:
        if t.id in tl:
            depart, minutes, arrivals = tl[t.id]
            ends[t.id] = depart + minutes
            misses += sum(1 for c, at in zip([c for c in t.stops if c in stops], arrivals) if at > _span(ctx, c)[1] + _EPS)
    return ends, max(ends.values(), default=0.0), misses


def _can_carry(ctx: DayContext, sel: Sequence[fl.FleetTruck], code: str, cids: Sequence[int],
               stops: Mapping[int, Stop], shares: Mapping[int, int]) -> bool:
    """Рейс cids по силам машине code: право въезда (машина, центр) и груз не тяжелее предела сборки (fl.load_limit,
    №45: 90% тоннажа)."""
    truck = ctx.trucks[code]
    if any(not _vehicle_ok(ctx, c, code) or (_central(ctx, stops[c]) and not truck.center_ok) for c in cids):
        return False
    kgs = [stops[c].kg / shares.get(c, 1) for c in cids]
    lim = fl.load_limit(kgs, [_central(ctx, stops[c]) for c in cids], [_allowed_trucks(ctx, c) for c in cids], truck, sel)
    return math.fsum(kgs) <= lim + 0.5


def _moved(ctx: DayContext, trips: Sequence[DraftTrip], stops: Mapping[int, Stop], cid: int, src: int | None,
           dst: DraftTrip) -> list[DraftTrip]:
    """Копия рейсов, где клиент cid перешёл из рейса src (None — «ещё не в рейсах») в рейс dst (новый рейс — dst, которого
    нет среди trips: он встаёт последним) на место с наименьшим объездом; опустевший рейс уходит."""
    out = [replace(t, stops=list(t.stops)) for t in trips]
    if all(t.id != dst.id for t in out):
        out.append(replace(dst, stops=list(dst.stops)))
    for t in out:
        if t.id == src:
            t.stops.remove(cid)
        if t.id == dst.id:
            t.stops = _insert_cheapest(ctx, t.stops, cid, stops)
    return [t for t in out if t.stops]


def _take(ctx: DayContext, draft: Draft, stops: Mapping[int, Stop], trips: list[DraftTrip], cid: int,
          touched: Sequence[int]) -> None:
    """Принять перенос: рейсы trips — в черновик, у затронутых рейсов порядок заново (2-opt с их выезда, как у move) —
    если рейс с ним возвращается не позже и окна приёма машины не нарушаются чаще (2-opt укорачивает км, а минуты с
    пробками и окнами могут вырасти; окна он бережёт, только если порядок их уже соблюдал)."""
    draft.trips = trips
    if any(t.id >= draft.next_id for t in trips):
        draft.next_id = max(t.id for t in trips) + 1
    for left in (draft.no_room, draft.no_window, draft.no_center, draft.no_vehicle):
        left.discard(cid)
    shares = _shares(trips)
    for t in trips:
        if t.id in touched:
            start = _timeline(ctx, trips, stops, shares)[t.id][0]
            was = t.stops
            ends, _, misses = _truck_day(ctx, trips, stops, shares, t.truck)
            t.stops, *_ = _route(ctx, was, stops, shares, reorder=True, start=start)
            ends2, _, misses2 = _truck_day(ctx, trips, stops, shares, t.truck)
            if ends2[t.id] > ends[t.id] + _EPS or misses2 > misses:
                t.stops = was


def _movable(draft: Draft, t: DraftTrip, frozen: Collection[int]) -> bool:
    """Рейс, который правка конца другого рейса может менять (_shrink, _grow): не закреплён логистом — закреплённый
    взятием заказов дня или утверждением плана (№72–73, _insertable) — да, и ещё не грузится (frozen — начатые рейсы)."""
    return _insertable(draft, t) and t.id not in frozen


def _shrink(ctx: DayContext, draft: Draft, stops: Mapping[int, Stop], trip: DraftTrip, target: float,
            frozen: Collection[int] = ()) -> None:
    """Рейс trip должен вернуться к target: его магазины по одному уходят в рейсы других машин (или новым рейсом
    отмеченной машине), пока не вернётся. Каждый раз — перенос с наименьшим ростом км всего плана на минуту, которую рейс
    выигрывает (выигрыш сверх нужного до target не в счёт; переносы без роста км — первыми). Принимающая машина
    не позже конца дня (или своего прежнего конца), окна приёма у неё не нарушаются, груз — в пределе сборки;
    закреплённые логистом и начатые (frozen) рейсы не трогаются (_movable). Некуда — остаётся сколько успели. Км — с платой за
    точки Еревана на больших машинах (№68, _yerevan_km: как в сборке, малые машины — первыми)."""
    sel = _selected(ctx, draft.trucks)
    limit = ctx.overtime_minutes if draft.overtime_ok and ctx.overtime_minutes is not None else ctx.tn.work_minutes
    others = [t.car_code for t in sel if t.car_code != trip.truck]
    tid = trip.id
    for _ in range(len(trip.stops)):
        trip = next((t for t in draft.trips if t.id == tid), None)   # _take кладёт в черновик копии рейсов
        if trip is None:
            return
        shares = _shares(draft.trips)
        ends, _, _ = _truck_day(ctx, draft.trips, stops, shares, trip.truck)
        now = ends.get(trip.id)
        if now is None or now <= target + _EPS:
            return
        receivers = [t for t in draft.trips if t.truck in others and _movable(draft, t, frozen)]
        base_km = _closed_km(ctx, trip.stops, stops)
        cands = []
        for cid in trip.stops:
            if shares[cid] != 1:          # тяжёлый заказ на несколько поездок — переносят целиком вручную
                continue
            rest = [c for c in trip.stops if c != cid]
            saved_km = base_km - _closed_km(ctx, rest, stops)
            if rest:
                kept = [replace(t, stops=rest if t.id == trip.id else t.stops) for t in draft.trips]
                saved = now - _truck_day(ctx, kept, stops, shares, trip.truck)[0][trip.id]
            else:
                saved = math.inf
            if saved <= _EPS:
                continue
            useful = min(saved, now - target)   # сверх нужного выигрыш не в счёт
            options = [*receivers, *(DraftTrip(draft.next_id, code, []) for code in others)]
            for r in options:
                if not _can_carry(ctx, sel, r.truck, [*r.stops, cid], stops, shares):
                    continue
                net = (_closed_km(ctx, _insert_cheapest(ctx, r.stops, cid, stops), stops)
                       - _closed_km(ctx, r.stops, stops) - saved_km
                       + _yerevan_km(ctx, r.truck, stops[cid]) - _yerevan_km(ctx, trip.truck, stops[cid]))
                cands.append(((0, -useful) if net <= 0 else (1, net / useful), cid, r.id, r))
        state: dict[str, tuple[float, int]] = {}
        for _, cid, _, r in sorted(cands, key=lambda x: (x[0], x[1], x[2])):
            if r.truck not in state:
                _, end0, miss0 = _truck_day(ctx, draft.trips, stops, shares, r.truck)
                state[r.truck] = (end0, miss0)
            end0, miss0 = state[r.truck]
            trial = _moved(ctx, draft.trips, stops, cid, trip.id, r)
            _, end1, miss1 = _truck_day(ctx, trial, stops, _shares(trial), r.truck)
            if end1 <= max(limit, end0) + _EPS and miss1 <= miss0:
                _take(ctx, draft, stops, trial, cid, (trip.id, r.id))
                break
        else:
            return


def _grow(ctx: DayContext, draft: Draft, stops: Mapping[int, Stop], trip: DraftTrip, target: float,
          frozen: Collection[int] = ()) -> None:
    """Машина рейса trip работает до target: в рейс берутся магазины «ещё не в рейсах» (сначала — их надо везти), затем
    из рейсов других машин — каждый раз тот, с которым км всего плана растут меньше всего, пока рейс успевает к target.
    Рейс полон (предел сборки) и он у машины последний — после него новый рейс этой машины. Окна приёма машины не
    нарушаются; позже конца дня — только если сам конец рейса тянут за него (не позже предела переработки); закреплённые
    логистом и начатые (frozen) рейсы других машин не трогаются (_movable). Км — с платой за точки Еревана на больших машинах (№68, _yerevan_km, как в _shrink)."""
    sel = _selected(ctx, draft.trucks)
    code = trip.truck
    limit = ctx.overtime_minutes if draft.overtime_ok and ctx.overtime_minutes is not None else ctx.tn.work_minutes
    shares = _shares(draft.trips)
    ends, end, _ = _truck_day(ctx, draft.trips, stops, shares, code)
    if trip.id not in ends:
        return
    last = [t for t in draft.trips if t.truck == code][-1].id == trip.id
    # за конец дня — только если туда тянут сам последний рейс; следующие рейсы машины сдвигаются не дальше конца дня
    cap = max(limit, target) if last else max(limit, end)
    if ctx.overtime_minutes is not None:
        cap = min(cap, max(limit, ctx.overtime_minutes))
    follow: int | None = None
    for _ in range(len(stops)):
        shares = _shares(draft.trips)
        ends, _, miss = _truck_day(ctx, draft.trips, stops, shares, code)
        lead = follow if follow in ends else trip.id
        if ends[lead] >= target - _EPS:
            return
        by_id = {t.id: t for t in draft.trips}
        dests = [by_id[i] for i in (trip.id, follow) if i in by_id]
        if last and follow not in by_id:
            dests.append(DraftTrip(draft.next_id, code, []))
        pool = [(cid, None) for cid in sorted(stops) if cid not in shares]
        pool += [(cid, t) for t in draft.trips if t.truck != code and _movable(draft, t, frozen)
                 for cid in t.stops if shares[cid] == 1]
        base = {t.id: _closed_km(ctx, t.stops, stops) for t in draft.trips if t.truck != code}
        cands = []
        for cid, src in pool:
            saved_km = 0.0 if src is None else base[src.id] - _closed_km(ctx, [c for c in src.stops if c != cid], stops)
            saved_km -= _yerevan_km(ctx, code, stops[cid]) - (0.0 if src is None else _yerevan_km(ctx, src.truck, stops[cid]))
            for r in dests:
                if not _can_carry(ctx, sel, code, [*r.stops, cid], stops, shares):
                    continue
                net = (_closed_km(ctx, _insert_cheapest(ctx, r.stops, cid, stops), stops)
                       - _closed_km(ctx, r.stops, stops) - saved_km)
                cands.append(((src is not None, net, cid, r.id), cid, src, r))
        for _, cid, src, r in sorted(cands, key=lambda x: x[0]):
            trial = _moved(ctx, draft.trips, stops, cid, None if src is None else src.id, r)
            t_ends, t_end, t_miss = _truck_day(ctx, trial, stops, _shares(trial), code)
            at = t_ends[r.id if r.id != trip.id else (follow if follow in t_ends else trip.id)]
            if at <= target + _EPS and t_end <= cap + _EPS and t_miss <= miss:
                _take(ctx, draft, stops, trial, cid, (r.id, *(() if src is None else (src.id,))))
                if r.id != trip.id:
                    follow = r.id
                break
        else:
            return


def _resize(ctx: DayContext, draft: Draft, stops: Mapping[int, Stop], edit: Mapping[str, Any],
            now_min: float | None = None) -> Draft:
    """{"action": "resize", "trip": id, "return": минут от полуночи} — конец полосы рейса на шкале дня потянули мышью:
    раньше — магазины уходят другим машинам (_shrink), позже — машина берёт магазины других и «ещё не в рейсах» (_grow).
    now_min — сейчас (минуты от начала дня машины; сегодняшний день): рейсы, которые уже грузятся, не меняются."""
    trip = _trip(draft, edit.get('trip'))
    at = edit.get('return')
    if not _is_num(at) or not 0 <= at <= 2 * 24 * 60:
        raise DispatchError('Նշեք, թե երբ պետք է վերադառնա երթը')
    if trip.truck not in ctx.trucks or trip.truck not in draft.trucks:
        raise DispatchError('Эта машина сегодня не работает — отметьте её в шаге 1')
    target = at - ctx.work_start_min
    ends, _, _ = _truck_day(ctx, draft.trips, stops, _shares(draft.trips), trip.truck)
    if trip.id not in ends:
        return draft
    frozen = started_trips(ctx, list(stops.values()), draft, now_min) if now_min is not None else set()
    if target < ends[trip.id] - _EPS:
        _shrink(ctx, draft, stops, trip, target, frozen)
    elif target > ends[trip.id] + _EPS:
        _grow(ctx, draft, stops, trip, target, frozen)
    return draft


LOADED_EDIT = 'Այս երթի ապրանքն արդեն բեռնված է մեքենայում։ Միևնույն է փոխե՞լ երթը։'   # №78: правка загруженного рейса
LOADED_UNPIN = 'Երթը բեռնված է — նախ հանեք «Բեռնված է» նշումը'


def _guard_loaded(draft: Draft, edit: Mapping[str, Any], routable: Mapping[int, Stop], ok: bool) -> None:
    """Правка, меняющая состав или машину загруженного рейса (№78): без подтверждения логиста (ok) — LoadedEdit;
    открепить загруженный рейс нельзя — сначала снять отметку."""
    loaded = {t.id: t for t in draft.trips if t.loaded is not None}
    if not loaded:
        return
    action = edit.get('action')
    if action == 'unpin' and edit.get('trip') in loaded:
        raise DispatchError(LOADED_UNPIN)
    if ok:
        return
    touched = False
    if action in ('defer_trip', 'resize'):
        touched = edit.get('trip') in loaded
    elif action == 'pin':
        touched = edit.get('trip') in loaded and edit.get('truck') != loaded[edit.get('trip')].truck
    elif action == 'move':
        touched = edit.get('from_trip') in loaded or edit.get('to_trip') in loaded
    elif action == 'exclude':
        isn = edit.get('order')
        isn = isn.upper() if isinstance(isn, str) else None
        cids = {c for c, s in routable.items() if any(o.isn == isn for o in s.orders)}
        touched = any(cids & set(t.stops) for t in loaded.values())
    if touched:
        raise LoadedEdit(LOADED_EDIT)


def apply_edit(ctx: DayContext, stops: Sequence[Stop], draft: Draft, edit: Mapping[str, Any],
               order_ids: set[str], backlog_ids: set[str] = frozenset(), defer_since: date | None = None,
               carried: Collection[str] = (), now_min: float | None = None, loaded_ok: bool = False) -> Draft:
    """Правка логиста (§3) поверх черновика; затронутые рейсы пересчитываются (порядок — 2-opt). now_min — сейчас (минуты от
    начала дня машины; только сегодня): resize не меняет рейсы, которые уже грузятся. Загруженный рейс (№78, _guard_loaded)
    меняется только с loaded_ok — логист подтвердил, что товар уже в машине.
    edit:
      {"action": "move", "customer_id", "from_trip": id|null, "to_trip": id|null, "truck": код|null}
          — перенести клиента в другой рейс; to_trip = null и truck — новый рейс этой машины;
            to_trip = null и truck = null — убрать из рейсов («ещё не в рейсах»);
      {"action": "pin", "trip": id, "truck": код} — закрепить машину за рейсом (рейс уходит к ней);
      {"action": "unpin", "trip": id};
      {"action": "resize", "trip": id, "return": минут от полуночи} — конец рейса потянули на шкале дня (_resize);
      {"action": "undo"} — «Չեղարկել»: черновик до последнего resize (Draft.undo);
      {"action": "exclude" | "include", "order": fISN} — «не везём сегодня» / вернуть; заказ прошлых
          дней (backlog_ids) — убрать из развоза / добавить в развоз (перенесённый сюда — carried —
          убранный запоминается в dropped); перенос на завтра снимается;
      {"action": "agents", "off": [agent_id, …]} — фильтр «Մենեջերներ»: заказы этих менеджеров не везём
          (остальные — везём); точки, где заказов не осталось, уходят из рейсов, вернувшиеся — «ещё не в рейсах»;
      {"action": "defer_trip", "trip": id} — «везти завтра» (№25: рейс дешевле min_trip_revenue): заказы
          рейса — не сегодня (excluded / из added) и в deferred — следующий день доставки возьмёт их сам;
          заказ старше defer_since (вне окна «не отгружены с прошлых дней» следующего дня) — ошибка:
          завтра его не будет видно, решать надо сегодня.
    Ошибка — DispatchError с текстом для логиста."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    _clean(draft, routable)
    _guard_loaded(draft, edit, routable, loaded_ok)
    action = edit.get('action')
    if action == 'undo':
        if draft.undo is None:
            raise DispatchError('Չեղարկելու բան չկա՝ պլանը դրանից հետո արդեն փոխվել է')
        return Draft.from_json(draft.undo)
    draft.undo = None
    if action in ('exclude', 'include'):
        isn = edit.get('order')
        isn = isn.upper() if isinstance(isn, str) else None
        if isn in backlog_ids:
            if action == 'exclude':
                draft.added.discard(isn)
                if isn in carried:
                    draft.dropped.add(isn)
            else:
                draft.added.add(isn)
                draft.dropped.discard(isn)
            draft.deferred.discard(isn)
            return draft
        if isn not in order_ids:
            raise DispatchError('Заказ не найден среди заказов дня — обновите страницу')
        (draft.excluded.add if action == 'exclude' else draft.excluded.discard)(isn)
        draft.deferred.discard(isn)
        return draft
    if action == 'agents':
        off = parse_agents(edit.get('off'))
        if off is None:
            raise DispatchError('Список менеджеров не принят — обновите страницу')
        draft.agents_off = off
        return draft
    if action == 'defer_trip':
        trip = _trip(draft, edit.get('trip'))
        if defer_since is not None and any(o.order_date < defer_since for c in trip.stops for o in routable[c].orders):
            raise DispatchError('Заказы этого рейса слишком давние для переноса на завтра — решите сегодня')
        for cid in trip.stops:
            for o in routable[cid].orders:
                if o.isn in order_ids:
                    draft.excluded.add(o.isn)
                else:
                    draft.added.discard(o.isn)
                draft.same_day.discard(o.isn)    # заказ дня, взятый сегодня (№72), — снова заказ следующего дня
                draft.deferred.add(o.isn)
        moved = set(trip.stops)
        for t in draft.trips:                    # тяжёлый заказ в нескольких рейсах — переносится целиком
            t.stops = [c for c in t.stops if c not in moved]
        draft.trips = [t for t in draft.trips if t.stops]
        return draft
    if action in ('pin', 'unpin'):
        trip = _trip(draft, edit.get('trip'))
        if action == 'unpin':
            trip.pinned = False
            return draft
        truck = edit.get('truck')
        if truck not in ctx.trucks or truck not in draft.trucks:
            raise DispatchError('Эта машина сегодня не работает — отметьте её в шаге 1')
        _require_vehicle(ctx, trip.stops, truck)
        if truck != trip.truck:   # рейс уходит последним к выбранной машине
            draft.trips.remove(trip)
            trip.truck = truck
            draft.trips.append(trip)
        trip.pinned = True
        if trip.loaded is not None:   # №78: закрепление теперь держит логист — снятие отметки рейс не открепит
            trip.loaded = {**trip.loaded, 'pin': False}
        draft.same_day_trips.discard(trip.id)   # закрепил сам логист — состав решён (№72: в него не вставляются)
        if draft.approved is not None and trip.id in draft.approved['pinned']:   # №73: и снятие утверждения его не открепит
            draft.approved = {**draft.approved, 'pinned': [i for i in draft.approved['pinned'] if i != trip.id]}
        return draft
    if action == 'resize':
        before = {k: v for k, v in draft.to_json().items() if k not in ('prediction', 'undo')}
        draft = _resize(ctx, draft, routable, edit, now_min)
        draft.undo = before
        return draft
    if action != 'move':
        raise DispatchError('Неизвестное действие')
    cid = edit.get('customer_id')
    if not _is_int(cid) or cid not in routable:
        raise DispatchError('Точка не найдена — обновите страницу')
    shares = _shares(draft.trips)
    src_id, dst_id, truck = edit.get('from_trip'), edit.get('to_trip'), edit.get('truck')
    src = _trip(draft, src_id) if src_id is not None else None
    if src is None and cid in shares:
        raise DispatchError('Точка уже в рейсе — укажите, из какого')
    if src is not None and cid not in src.stops:
        raise DispatchError('Этой точки нет в рейсе — обновите страницу')
    dst = _trip(draft, dst_id) if dst_id is not None else None
    if dst is None and truck is not None and (truck not in ctx.trucks or truck not in draft.trucks):
        raise DispatchError('Эта машина сегодня не работает — отметьте её в шаге 1')
    target_truck = dst.truck if dst is not None else truck
    if target_truck is not None:
        _require_vehicle(ctx, [cid], target_truck)
    if dst is not None and dst is src:
        return draft
    if src is not None:
        src.stops.remove(cid)
    if dst is None and truck is not None:
        dst = DraftTrip(draft.next_id, truck, [])
        draft.next_id += 1
        draft.trips.append(dst)
    if dst is not None and cid not in dst.stops:
        dst.stops = _insert_cheapest(ctx, dst.stops, cid, routable)
        for left in (draft.no_room, draft.no_window, draft.no_center, draft.no_vehicle):
            left.discard(cid)
    draft.trips = [t for t in draft.trips if t.stops]
    shares = _shares(draft.trips)
    for t in (src, dst):
        if t is not None and t.stops:
            # выезд рейса — для окон приёма при 2-opt; заново после перестановки src (та же машина — другой выезд)
            start = _timeline(ctx, draft.trips, routable, shares)[t.id][0]
            t.stops, *_ = _route(ctx, t.stops, routable, shares, reorder=True, start=start, truck=t.truck)
    return draft


# --- Новые заказы дня: взять в сегодняшний развоз (ответ владельца №72) ---

SAME_DAY_SHOWN = 8     # вариантов в ответе — самые дешёвые
# вид варианта при равной цене: в ту же точку / в рейс, затем новый рейс отмеченной машины, затем машина не из шага 1
_SAME_DAY_RANK = {'same_stop': 0, 'insert': 0, 'trip': 1, 'idle': 1, 'extra': 2}
# машина не из шага 1 (extra) — после всех вариантов отмеченных машин, при любой цене: «все работающие машины всегда
# отмечены» (№71) — неотмеченная, скорее всего, сегодня не выходит. Пока владелец не решил (№72); False — по цене со всеми
SAME_DAY_EXTRA_LAST = True


@dataclass
class _SameDayPlan:
    key: str                     # 'same_stop' | 'insert:<рейс>' | 'trip:<машина>' | 'extra:<машина>'
    kind: str
    truck: str
    trips: list[DraftTrip]       # рейсы черновика с новыми заказами
    changed: set[int]            # рейсы, которые вариант меняет или добавляет
    view: dict[str, Any]


def _trip_amd(ctx: DayContext, cids: Sequence[int], routable: Mapping[int, Stop], shares: Mapping[int, int],
              code: str) -> float:
    """Расход рейса в драмах — как operating_cost_amd плана (топливо по цене дня + износ)."""
    kgs = [routable[c].kg / shares.get(c, 1) for c in cids]
    cost = fl.trip_running_cost([routable[c].point for c in cids], kgs, ctx.depot, ctx.norms, ctx.trucks[code])
    return cost.total_amd(ctx.tn.fuel_price)


def _same_day_base(ctx: DayContext, base: Sequence[Stop], draft: Draft, now_min: float
                   ) -> tuple[list[DraftTrip], dict[int, Stop], dict[int, int], dict[int, tuple[float, float, list[float]]],
                              set[int]]:
    """План дня без новых заказов: (копии рейсов по точкам base, точки, доли, время рейсов, начатые рейсы — начало загрузки
    по плану не позже now_min, минуты от начала дня машины)."""
    routable0 = {s.customer_id: s for s in base if s.point is not None}
    tmp = Draft(trips=[replace(t, stops=list(t.stops)) for t in draft.trips])
    _clean(tmp, routable0)
    shares0 = _shares(tmp.trips)
    tl0 = _timeline(ctx, tmp.trips, routable0, shares0)
    return tmp.trips, routable0, shares0, tl0, {tid for tid, (depart, _, _) in tl0.items() if depart <= now_min + _EPS}


def _insertable(draft: Draft, t: DraftTrip) -> bool:
    """В рейс можно вставить новые заказы дня: не загружен (№78) и не закреплён или закреплён не логистом — взятием
    заказов дня (same_day_trips) или утверждением плана (№73, approved['pinned'])."""
    return t.loaded is None and (not t.pinned or t.id in draft.same_day_trips
                                 or (draft.approved is not None and t.id in draft.approved['pinned']))


def started_trips(ctx: DayContext, base: Sequence[Stop], draft: Draft, now_min: float) -> set[int]:
    """Рейсы, чья загрузка по плану уже началась (начало загрузки не позже now_min): машина в рейсе новый заказ не берёт и
    взятый заказ из него уже не вернуть на завтра (№72)."""
    return _same_day_base(ctx, base, draft, now_min)[4]


def same_day_blocked(ctx: DayContext, base: Sequence[Stop], stops: Sequence[Stop], draft: Draft, cids: Collection[int],
                     now_min: float) -> dict[int, str]:
    """Клиенты cids, чьи новые заказы сегодня не взять: 'no_coords' — нет точки (stops — точки дня с новыми заказами);
    'started' — клиент уже в рейсе, чья загрузка началась (base — точки дня без новых заказов)."""
    trips0, _, _, _, started = _same_day_base(ctx, base, draft, now_min)
    routable = {s.customer_id for s in stops if s.point is not None}
    blocked: dict[int, str] = {}
    for c in sorted(set(cids)):
        if c not in routable:
            blocked[c] = 'no_coords'
        elif any(c in t.stops for t in trips0 if t.id in started):
            blocked[c] = 'started'
    return blocked


def _same_day_plans(ctx: DayContext, base: Sequence[Stop], stops: Sequence[Stop], draft: Draft, cids: Collection[int],
                    now_min: float, crew: Crew | None = None) -> tuple[list[_SameDayPlan], dict[int, str]]:
    """Как взять новые заказы дня клиентов cids в развоз сегодня — все варианты, самые дешёвые первыми, и клиенты, которых
    взять нельзя: {клиент: 'no_coords' — нет точки | 'started' — он уже в рейсе, чья загрузка по плану началась}.
    base — точки дня без этих заказов, stops — с ними (у клиента, который уже в развозе, точка тяжелее); now_min — сейчас,
    минуты от начала дня машины. Рейс «начат», если начало его загрузки по плану (_timeline) не позже now_min: его машина
    и точки не меняются — машина в рейсе новый заказ не берёт (ответ «Բ»). Клиент уже в рейсе, который ещё не грузится, —
    заказ едет в ту же точку того же рейса; остальные (новые точки) — все вместе одним из способов:
    - insert — вставкой (по одной, на место с наименьшим объездом) в рейс отмеченной машины, чья загрузка не началась
      (закреплённый логистом — нет: его состав решён; закреплённый взятием новых заказов или утверждением плана (№73) —
      да: _insertable);
    - trip / idle — новым рейсом отмеченной машины после её последнего рейса (idle — сегодня рейсов у неё нет);
    - extra — новым рейсом готовой машины, не отмеченной в шаге 1.
    Новый рейс грузится не раньше «сейчас» (not_before), порядок его точек — 2-opt с окнами приёма. Вариант годится, если
    груз изменённого рейса — в пределе сборки (_can_carry: тоннаж, центр, допуск машин), машина возвращается не позже
    конца рабочего дня (принятая переработка — её предела; машина и так позже — не позже, чем сейчас) и окна приёма у неё
    не нарушаются чаще, чем сейчас. Порядок точек плана не переставляется. Цена — рост расхода в драмах (как
    operating_cost_amd плана), км и минут работы изменённых и новых рейсов; extra — после остальных (SAME_DAY_EXTRA_LAST)."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    trips0, routable0, shares0, tl0, started = _same_day_base(ctx, base, draft, now_min)
    blocked = same_day_blocked(ctx, base, stops, draft, cids, now_min)
    want = [c for c in sorted(set(cids)) if c not in blocked]
    if not want:
        return [], blocked
    same = [c for c in want if c in shares0]
    free = [c for c in want if c not in shares0]
    same_trips = {t.id for t in trips0 if any(c in t.stops for c in same)}
    sel = _selected(ctx, draft.trucks)
    # конец дня — с запасом (№78), как у сборки; принятая переработка — её предел
    limit = ctx.overtime_minutes if draft.overtime_ok and ctx.overtime_minutes is not None else _horizon(ctx).work_minutes
    day0: dict[str, tuple[float, int]] = {}
    old = {t.id: t for t in trips0}
    out: list[_SameDayPlan] = []

    def try_plan(key: str, kind: str, truck: str, trip: int, trial: list[DraftTrip],
                 fleet: Sequence[fl.FleetTruck]) -> None:
        ids = same_trips | {trip}
        shares = _shares(trial)
        if any(t.id in ids and (t.truck not in ctx.trucks or not _can_carry(ctx, fleet, t.truck, t.stops, routable, shares))
               for t in trial):
            return
        codes = {t.truck for t in trial if t.id in ids}
        for code in codes:
            if code not in day0:
                day0[code] = _truck_day(ctx, trips0, routable0, shares0, code)[1:]
            end0, miss0 = day0[code]
            _, end1, miss1 = _truck_day(ctx, trial, routable, shares, code)
            if end1 > max(limit, end0) + _EPS or miss1 > miss0:
                return
        tl = _timeline(ctx, [t for t in trial if t.truck in codes], routable, shares)
        km = amd = minutes = 0.0
        etas = []
        for t in trial:
            if t.id not in ids:
                continue
            km += _closed_km(ctx, t.stops, routable)
            amd += _trip_amd(ctx, t.stops, routable, shares, t.truck)
            minutes += tl[t.id][1]
            if t.id in old:
                km -= _closed_km(ctx, old[t.id].stops, routable0)
                amd -= _trip_amd(ctx, old[t.id].stops, routable0, shares0, t.truck)
                minutes -= tl0[t.id][1]
            etas += [{'customer_id': c, 'eta': _hhmm(ctx.work_start_min + at)}
                     for c, at in zip(t.stops, tl[t.id][2]) if c in want]
        depart, trip_min, _ = tl[trip]
        carried = next(t for t in trial if t.id == trip).stops
        load = ctx.tn.load(math.fsum(routable[c].kg / shares[c] for c in carried))
        out.append(_SameDayPlan(key, kind, truck, trial, ids, {
            'key': key, 'kind': kind, 'truck': truck, 'name': ctx.trucks[truck].name, 'trip': trip if trip in old else None,
            'km': _r(km), 'minutes': round(minutes), 'amd': round(amd),
            'loading_start': _hhmm(ctx.work_start_min + depart), 'depart': _hhmm(ctx.work_start_min + depart + load),
            'return': _hhmm(ctx.work_start_min + depart + trip_min), 'stops': etas}))

    def copy() -> list[DraftTrip]:
        return [replace(t, stops=list(t.stops)) for t in trips0]

    if not free:
        first = next(t for t in trips0 if t.id in same_trips)
        try_plan('same_stop', 'same_stop', first.truck, first.id, copy(), sel)
    else:
        for t in trips0:
            if t.id in started or not _insertable(draft, t) or t.truck not in draft.trucks or t.truck not in ctx.trucks:
                continue
            trial = copy()
            dst = next(x for x in trial if x.id == t.id)
            for c in free:
                dst.stops = _insert_cheapest(ctx, dst.stops, c, routable)
            try_plan(f'insert:{t.id}', 'insert', t.truck, t.id, trial, sel)
        ones = {c: 1 for c in free}
        working = sorted(set(draft.trucks) & set(ctx.trucks))
        for code in working + spare_trucks(ctx, draft, crew):   # не из шага 1 — только с вышедшим свободным водителем (№77)
            extra = code not in draft.trucks
            busy = any(t.truck == code for t in trips0)
            start = max(now_min, _truck_day(ctx, trips0, routable0, shares0, code)[1])
            order, *_ = _route(ctx, free, routable, ones, reorder=True, start=start, truck=code)
            trial = [*copy(), DraftTrip(draft.next_id, code, order, not_before=float(ctx.work_start_min + now_min))]
            try_plan(f'{"extra" if extra else "trip"}:{code}', 'extra' if extra else 'trip' if busy else 'idle', code,
                     draft.next_id, trial, [*sel, ctx.trucks[code]] if extra else sel)
    out.sort(key=lambda p: (SAME_DAY_EXTRA_LAST and p.kind == 'extra', p.view['amd'], p.view['km'],
                            _SAME_DAY_RANK[p.kind], p.key))
    return out, blocked


def same_day_options(ctx: DayContext, base: Sequence[Stop], stops: Sequence[Stop], draft: Draft, cids: Collection[int],
                     now_min: float, crew: Crew | None = None) -> dict[str, Any]:
    """Предложения странице (№72): {'options': до SAME_DAY_SHOWN самых дешёвых вариантов _same_day_plans — key (его шлёт
    «Ընտրել»), вид, машина, меняемый рейс (новый — None), +км, +минуты работы, +֏, начало загрузки, выезд и возвращение
    рейса, прибытие к клиентам cids; 'blocked': клиенты, которых сегодня взять нельзя, и почему}. «Թողնել վաղվան» можно
    всегда, без расчёта: ничего не меняется. crew — водители дня (№77, spare_trucks)."""
    plans, blocked = _same_day_plans(ctx, base, stops, draft, cids, now_min, crew)
    return {'options': [p.view for p in plans[:SAME_DAY_SHOWN]],
            'blocked': [{'customer_id': c, 'reason': r} for c, r in sorted(blocked.items())]}


def take_same_day(ctx: DayContext, base: Sequence[Stop], stops: Sequence[Stop], draft: Draft, cids: Collection[int],
                  isns: Collection[str], key: Any, now_min: float, crew: Crew | None = None) -> Draft:
    """Взять новые заказы дня isns (их клиенты — cids) в развоз сегодня вариантом key (_same_day_plans). Варианты считаются
    заново на «сейчас»: пока логист выбирал, рейс мог начать грузиться. Варианта нет или клиента взять нельзя —
    DispatchError. Рейсы черновика — рейсы варианта; изменённые и новые рейсы закрепляются (пересборка их не трогает и
    новый рейс не уйдёт раньше «сейчас»); машина не из шага 1 (extra) становится машиной дня."""
    plans, blocked = _same_day_plans(ctx, base, stops, draft, cids, now_min, crew)
    if blocked:
        raise DispatchError('Այս պատվերներից մեկի մեքենան արդեն բեռնվում է կամ խանութի տեղը քարտեզում չկա — '
                            'թարմացրեք առաջարկները')
    plan = next((p for p in plans if p.key == key), None)
    if plan is None:
        raise DispatchError('Այս առաջարկն այլևս հնարավոր չէ — թարմացրեք առաջարկները')
    draft.undo = None
    draft.trips = plan.trips
    approved = set(draft.approved['pinned']) if draft.approved is not None else set()
    for t in draft.trips:
        if t.id not in plan.changed:
            continue
        if not t.pinned:
            t.pinned = True
            draft.same_day_trips.add(t.id)
        elif t.id in approved:   # №73: рейс утверждения с новым заказом дня — теперь рейс взятия: снятие его не открепит
            draft.same_day_trips.add(t.id)
            draft.approved = {**draft.approved, 'pinned': [i for i in draft.approved['pinned'] if i != t.id]}
    draft.next_id = max(draft.next_id, *(t.id + 1 for t in plan.trips))
    if plan.kind == 'extra':
        draft.trucks = sorted({*draft.trucks, plan.truck})
        draft.same_day_trucks.add(plan.truck)
    draft.same_day |= set(isns)
    for left in (draft.no_room, draft.no_window, draft.no_center, draft.no_vehicle):
        left.difference_update(cids)
    return draft


# --- Утверждение плана дня (ответ владельца №73) ---

def approve(draft: Draft, at: str, by: str | None) -> Draft:
    """«Հաստատել օրվա պլանը»: все рейсы закрепляются, утверждение помнит, какие из них закрепило само (не были
    закреплены) — снятие открепит ровно их. Уже утверждён или рейсов нет — DispatchError."""
    if draft.approved is not None:
        raise DispatchError('Պլանն արդեն հաստատված է — թարմացրեք էջը')
    if not draft.trips:
        raise DispatchError('Հաստատելու երթ չկա — նախ կազմեք երթերը')
    ids = sorted(t.id for t in draft.trips if not t.pinned)
    for t in draft.trips:
        t.pinned = True
    draft.undo = None
    draft.approved = {'at': at, 'by': by, 'pinned': ids}
    return draft


def unapprove(draft: Draft) -> Draft:
    """«Չեղարկել հաստատումը»: открепляются рейсы, закреплённые утверждением (и новые рейсы, закреплённые, пока план был
    утверждён); закрепления логиста и взятия заказов дня остаются. Не утверждён — DispatchError."""
    if draft.approved is None:
        raise DispatchError('Պլանը հաստատված չէ — թարմացրեք էջը')
    mine = set(draft.approved['pinned'])
    for t in draft.trips:
        if t.id in mine:
            if t.loaded is not None:   # №78: загруженный не открепляется — закрепление переходит отметке
                t.loaded = {**t.loaded, 'pin': True}
            else:
                t.pinned = False
    draft.undo = None
    draft.approved = None
    return draft


# --- «Բեռնված է» (ответ владельца №78) ---

NOT_APPROVED_LOAD = 'Պլանը դեռ հաստատված չէ — բեռնված նշել կարելի է միայն «Հաստատել»-ից հետո'
ALREADY_LOADED = 'Երթն արդեն նշված է բեռնված — թարմացրեք էջը'
NOT_LOADED = 'Երթը նշված չէ բեռնված — թարմացրեք էջը'


def mark_loaded(draft: Draft, tid: Any, at: str, by: str | None) -> Draft:
    """«Բեռնված է»: рейс tid загружен (at — когда, ISO; by — кто). Только по утверждённому плану (ответ 15) — иначе
    DispatchError; рейс уже закреплён утверждением или логистом, отметка закрепления не берёт (pin False)."""
    if draft.approved is None:
        raise DispatchError(NOT_APPROVED_LOAD)
    trip = _trip(draft, tid)
    if trip.loaded is not None:
        raise DispatchError(ALREADY_LOADED)
    trip.pinned = True
    trip.loaded = {'at': at, 'by': by, 'pin': False}
    draft.undo = None
    return draft


def unmark_loaded(draft: Draft, tid: Any) -> Draft:
    """«Հանել բեռնված նշումը»: отметка снимается; закрепление, которое держала она (pin), — открепляется, а если план
    утверждён — переходит утверждению (снятие утверждения откроет рейс; утверждённый план закреплён целиком)."""
    trip = _trip(draft, tid)
    if trip.loaded is None:
        raise DispatchError(NOT_LOADED)
    if trip.loaded['pin']:
        if draft.approved is not None:
            draft.approved = {**draft.approved, 'pinned': sorted({*draft.approved['pinned'], trip.id})}
        else:
            trip.pinned = False
    trip.loaded = None
    draft.undo = None
    return draft


def keep_approved(draft: Draft, before: Collection[int]) -> None:
    """Пока план утверждён, день остаётся закреплённым: рейсы, появившиеся при правке (их не было среди before), и не
    закреплённые — закрепляются, как утверждением (снятие утверждения их открепит)."""
    if draft.approved is None:
        return
    new = [t for t in draft.trips if t.id not in before and not t.pinned]
    for t in new:
        t.pinned = True
    if new:
        draft.approved = {**draft.approved, 'pinned': sorted({*draft.approved['pinned'], *(t.id for t in new)})}


def release_same_day_trucks(draft: Draft) -> None:
    """Машины не из шага 1, отмеченные взятием заказов дня, без рейсов — снова не отмечены (после правки и prune)."""
    idle = {c for c in draft.same_day_trucks if all(t.truck != c for t in draft.trips)}
    if idle:
        draft.trucks = [c for c in draft.trucks if c not in idle]
        draft.same_day_trucks -= idle


def drop_same_day(draft: Draft, isns: Collection[str]) -> Draft:
    """Вернуть взятые заказы дня isns в развоз следующего дня: точки без заказов уходят из рейсов (views — prune по точкам
    дня), опустевший рейс — тоже."""
    if not isns or not set(isns) <= draft.same_day:
        raise DispatchError('Պատվերն այսօրվա առաքման մեջ չէ — թարմացրեք էջը')
    draft.undo = None
    draft.same_day -= set(isns)
    return draft


# --- Вид страницы ---

def _r(x: float, nd: int = 1) -> float:
    return round(x, nd)


def _bypass_km(ctx: DayContext, points: Sequence[Point]) -> tuple[float, int]:
    """(на сколько км объезд малого центра удлинил рейс «склад → points → склад», участков в объезд). Участок — км
    расчёта (Norms.km грузовика, как у км рейса) против того же участка без объезда: км / во сколько раз объезд длиннее
    (CenterBypassRoads.detour; тем же множителем Valhalla и Яндекс растягивают свой путь). Длиннее меньше чем на
    BYPASS_MIN_KM — расхождение двух графов (привязка точки, округление км), а не объезд. Объезда нет (нет дорог или
    границы центра) — (0, 0). Типы дорог здесь не импортируются: объезд — сами дороги или запасной путь Valhalla."""
    norms = ctx.norms.for_trucks()
    roads = getattr(norms.roads, 'fallback', norms.roads)
    if not hasattr(roads, 'detour'):
        return 0.0, 0
    nodes = [ctx.depot, *points, ctx.depot]
    extra: list[float] = []
    for a, b in zip(nodes, nodes[1:]):
        k = roads.detour(a, b)
        if k > 1.0:
            km = norms.km(a, b)
            if km - km / k >= BYPASS_MIN_KM:
                extra.append(km - km / k)
    return math.fsum(extra), len(extra)


def _alternatives(ctx: DayContext, sel: Sequence[fl.FleetTruck], code: str, cids: Sequence[int],
                  routable: Mapping[int, Stop], kgs: Sequence[float]) -> list[dict[str, Any]]:
    """Другие машины дня на этот же рейс (те же точки, тот же порядок): могла бы она его везти по правилам сборки —
    тоннаж и предел загрузки (fl.load_limit), центр, допуск магазина — и сколько литров у неё вышло бы на тех же км.
    Занятость машины своими рейсами здесь не учитывается: сборка ищет меньше дизеля за весь день, а не за один рейс.
    Большая машина (№68) — big; с точками Еревана — плата приоритета малых машин yerevan_km (км её пути, как в сборке)."""
    kg = math.fsum(kgs)
    pts = [routable[c].point for c in cids]
    center = [_central(ctx, routable[c]) for c in cids]
    allowed = [_allowed_trucks(ctx, c) for c in cids]
    out = []
    for o in sel:
        if o.car_code == code:
            continue
        why = []
        if kg > o.capacity_kg + _EPS:
            why.append('capacity')
        elif kg > fl.load_limit(kgs, center, allowed, o, sel) + _EPS:
            why.append('load_cap')
        if any(center) and not o.center_ok:
            why.append('center')
        denied = sum(1 for c in cids if not _vehicle_ok(ctx, c, o.car_code))
        if denied:
            why.append('vehicle')
        penalty = math.fsum(_yerevan_km(ctx, o.car_code, routable[c]) for c in cids)
        out.append({'car_code': o.car_code, 'name': o.name, 'capacity_kg': o.capacity_kg, 'center_ok': o.center_ok,
                    'load_pct': round(kg / o.capacity_kg * 100.0), 'reasons': why, 'vehicle_denied': denied,
                    'liters': _r(fl.trip_running_cost(pts, kgs, ctx.depot, ctx.norms, o).liters),
                    **({'big': True} if big_shown(ctx, o) else {}), **({'yerevan_km': _r(penalty)} if penalty else {})})
    return out


def _trip_explain(ctx: DayContext, sel: Sequence[fl.FleetTruck], code: str, truck: fl.FleetTruck | None,
                  cids: Sequence[int], routable: Mapping[int, Stop], kgs: Sequence[float], parts: Mapping[str, Any],
                  free: float, depart: float, minutes: float) -> dict[str, Any]:
    """Почему рейс такой («Ինչու է այս երթը այսպես» на странице) — только цифры этого же расчёта: слагаемые минут рейса
    (из _timeline, в сумме — его минуты; обед в рейсе или в дороге — lunch_min), простой на складе до выезда (выезд
    позже, чтобы не ждать у окна первой точки; обед на складе до загрузки — не в нём), запас до конца рабочего дня, предел
    загрузки, расход, объезд центра и другие машины дня на этот рейс."""
    legs = parts['legs']
    brk = parts.get('lunch')
    kg = math.fsum(kgs)
    limit = (fl.load_limit(kgs, [_central(ctx, routable[c]) for c in cids], [_allowed_trucks(ctx, c) for c in cids],
                           truck, sel) if truck is not None else None)
    bypass_km, bypass_legs = _bypass_km(ctx, [routable[c].point for c in cids])
    heavy = (truck is not None and limit is not None and len(cids) == 1
             and limit > fl.LOAD_CAP * truck.capacity_kg + _EPS and kg > fl.LOAD_CAP * truck.capacity_kg + _EPS)
    city = ctx.tn.yerevan_of(code) * sum(1 for c in cids if _yerevan(ctx, routable[c]))   # №68: уже в unload_min
    penalty = math.fsum(_yerevan_km(ctx, code, routable[c]) for c in cids)                  # №68: плата приоритета, км
    return {
        'loading_min': _r(parts['loading']), 'drive_min': _r(math.fsum(x for x, _, _ in legs)),
        'unload_min': _r(math.fsum(x for _, _, x in legs)), 'wait_min': _r(math.fsum(x for _, x, _ in legs)),
        'back_min': _r(legs[-1][0]),
        # простой до выезда без обеда на складе (его показывает строка обеда; простой из-за окна первой точки — свой текст)
        'idle_before_min': _r(max(0.0, depart - free - (ctx.tn.lunch_minutes if brk is not None and brk.stop is None
                                                         else 0.0))),
        'end_slack_min': _r(ctx.tn.work_minutes - (depart + minutes)),
        **({'end_reserve_min': _r(ctx.end_reserve_min)} if ctx.end_reserve_min > 0 else {}),   # №78
        **({'preloaded': True} if parts.get('preloaded') else {}),   # №78: загружен с вечера (loading_min — 0)
        'load_cap_pct': round(fl.LOAD_CAP * 100), 'load_limit_kg': round(limit) if limit is not None else None,
        'over_limit': limit is not None and kg > limit + _EPS, 'heavy_alone': heavy,
        'l100': _r(truck.l100) if truck else None,
        'fuel_empty_l100': truck.fuel_empty_l_per_100km if truck else None,
        'fuel_full_l100': truck.fuel_full_l_per_100km if truck else None,
        'bypass_km': _r(bypass_km), 'bypass_legs': bypass_legs,
        'others': _alternatives(ctx, sel, code, cids, routable, kgs),
        **({'lunch_min': _r(brk.added if brk.stop is not None else 0.0)} if brk is not None else {}),
        **({'buffer_min': _r(parts['buffer'])} if 'buffer' in parts else {}),   # запас на рейс (№66)
        **({'yerevan_min': _r(city)} if city else {}),   # большая машина в Ереване (№68)
        **({'yerevan_km': _r(penalty)} if penalty else {}),
    }


def _day_explain(ctx: DayContext, routable: Mapping[int, Stop], draft: Draft, sel: Sequence[fl.FleetTruck],
                 trips_of: Mapping[str, int]) -> dict[str, Any]:
    """Что учитывает расчёт дня («Ինչ է հաշվի առել հաշվարկը» на странице): машины дня (с действующим расходом),
    магазины в центре, с окном приёма и с допуском машин, предел загрузки и выравнивание загрузки (fleet._balance),
    закреплённые рейсы, нормы загрузки и разгрузки (загрузка — те же числа, что у tn.load), правило точки магазина
    (ERP дальше ERP_GPS_MAX_GAP_KM от GPS менеджера — GPS), решатель и ctx.model (дороги, минуты, выученные нормы — views._dispatch_ctx)."""
    tn = ctx.tn
    # большая машина в Ереване (№68) — только когда правило в деле: среди машин дня есть большая и в зоне есть магазины
    city = sum(1 for s in routable.values() if _yerevan(ctx, s))
    big = [x.car_code for x in sel if big_shown(ctx, x)]
    return {
        'trucks': [{'car_code': x.car_code, 'name': x.name, 'capacity_kg': x.capacity_kg, 'l100': _r(x.l100),
                    'fuel_empty_l100': x.fuel_empty_l_per_100km, 'fuel_full_l100': x.fuel_full_l_per_100km,
                    'wear': x.wear_amd_per_km is not None or x.wear_load_amd_per_km is not None,
                    'center_ok': x.center_ok, 'trips': trips_of.get(x.car_code, 0),
                    **({'big': True} if big_shown(ctx, x) else {})} for x in sel],
        **({'yerevan': {'stores': city, 'big': big, 'minutes': max(tn.yerevan_of(c) for c in big),
                        'penalty_km': tn.yerevan_km}} if city and big else {}),
        'zone': len(ctx.center_zone) >= 3,
        'center_stores': sum(1 for s in routable.values() if _central(ctx, s)),
        'window_stores': sum(1 for c in routable if c in ctx.windows),
        'access_stores': sum(1 for c in routable if c in ctx.vehicle_access),
        'load_cap_pct': round(fl.LOAD_CAP * 100),
        'balance_from_pct': round(fl.BALANCE_FROM * 100), 'balance_slack_pct': round(fl.BALANCE_SLACK * 100),
        'balance_load_aware': any(configured(x) for x in sel),
        'pinned_trips': sum(1 for t in draft.trips if t.pinned),
        'unload_min_per_stop': tn.unload_min_per_stop, 'unload_min_per_tonne': tn.unload_min_per_tonne,
        'unload_stores': sum(1 for s in routable.values() if s.point in tn.unload_extra),
        'loading_fixed_min': tn.warehouse_load_fixed_min, 'loading_min_per_tonne': tn.warehouse_load_min_per_tonne,
        'erp_gps_gap_km': ERP_GPS_MAX_GAP_KM,
        'solver': vrp.available(), 'solver_iterations': vrp.ITERATIONS,
        'model': dict(ctx.model),
    }


def _advice(ctx: DayContext, draft: Draft, trips_json: Sequence[Mapping[str, Any]],
            unassigned: Sequence[Mapping[str, Any]], crew: Crew | None = None) -> dict[str, Any] | None:
    """Совет, когда не всё поместилось (ответ владельца №54): рейсы позже конца дня (over_time; с принятой переработкой —
    позже её предела) или магазины вне рейсов «не успели» / «центр без машины» (no_room, no_center; кроме тех, кому
    среди отмеченных машин нет допущенной, — no_vehicle, у них своя карточка). Ничего такого — None.
    Сама сборка машин не добавляет (№32): совет — логисту, решает он одной кнопкой на странице.
    - pinned_late — опаздывающие закреплённые рейсы (id): пересборка их не меняет, ни она, ни ещё машина им не помогут —
      страница советует снять закрепление или перенести магазины; дальше в совете они не участвуют;
    - rebuild — отмеченные машины дня без рейсов. Свежая сборка за конец дня не планирует, пока отмеченные машины
      свободны: опаздывающие рейсы рядом со свободными машинами — план собран при других настройках или правлен
      вручную, обычная пересборка загрузит и их;
    - add — иначе одна готовая, но не отмеченная машина. Нужен центр (магазин no_center или опаздывающий рейс машины с
      правом въезда, в котором есть точки центра) — из машин с правом въезда (таких нет — из всех, for_center: false);
      из них — допущенные ко всем этим магазинам (допуск магазина, VehicleAccess), если такие есть.
      Груз need_kg — самый тяжёлый опаздывающий рейс или все магазины вне рейсов вместе: из машин, что его берут, —
      с меньшим расходом (затем вместительнее, затем код), иначе самая вместительная — need_kg в совете: тоннаж меньше —
      страница пишет «возьмёт часть груза». Свободной машины нет (или опаздывают только закреплённые) — None;
    - no_free — беда есть и сверх закреплённых рейсов, а свободной машины нет: страница пишет «все машины уже отмечены»
      (только закреплённые — False: прежние тексты страницы); no_drivers — машины есть, но без водителей (№77: предлагаются
      только spare_trucks).
    Это оценка без расчёта рейсов (дёшево и детерминированно): что на самом деле поместится, покажет пересборка."""
    late = [t for t in trips_json if t['over_time']]
    pinned_late = [t['id'] for t in late if t['pinned']]
    late = [t for t in late if not t['pinned']]
    left = [u for u in unassigned if (u['no_room'] or u['no_center']) and not u['no_vehicle']]
    if not late and not left and not pinned_late:
        return None
    busy = {t['truck'] for t in trips_json}
    idle = [c for c in sorted(set(draft.trucks)) if c in ctx.trucks and c not in busy]
    if late and idle:
        return {'rebuild': idle, 'add': None, 'pinned_late': pinned_late, 'no_free': False}
    if not late and not left:                   # опаздывают только закреплённые рейсы
        return {'rebuild': [], 'add': None, 'pinned_late': pinned_late, 'no_free': False}
    # машина, чей водитель не вышел или уже ведёт машину дня, не предлагается (№77)
    spare = spare_trucks(ctx, draft, crew)
    free = [ctx.trucks[c] for c in spare]
    center_ok = {c for c, t in ctx.trucks.items() if t.center_ok}
    need_center = any(u['no_center'] for u in left) or any(
        t['truck'] in center_ok and any(s['center'] for s in t['stops']) for t in late)
    pool = [t for t in free if t.center_ok] if need_center else []
    for_center = bool(pool)
    pool = pool or free
    cids = [u['customer_id'] for u in left] + [s['customer_id'] for t in late for s in t['stops']]
    pool = [t for t in pool if all(_vehicle_ok(ctx, c, t.car_code) for c in cids)] or pool
    if not pool:
        return {'rebuild': [], 'add': None, 'pinned_late': pinned_late, 'no_free': True,
                **({'no_drivers': True} if any(c not in draft.trucks for c in ctx.trucks if c not in spare) else {})}
    need_kg = max([t['kg'] for t in late] + [sum(u['kg'] for u in left)])
    fits = [t for t in pool if t.capacity_kg >= need_kg]
    pick = (min(fits, key=lambda t: (t.l100, -t.capacity_kg, t.car_code)) if fits
            else min(pool, key=lambda t: (-t.capacity_kg, t.l100, t.car_code)))
    return {'rebuild': [], 'pinned_late': pinned_late, 'no_free': False,
            'add': {'car_code': pick.car_code, 'name': pick.name, 'capacity_kg': pick.capacity_kg, 'l100': pick.l100,
                    'center_ok': pick.center_ok, 'for_center': for_center, 'need_kg': int(need_kg)}}


def _in_reserve(ctx: DayContext, end: float) -> bool:
    """Машина возвращается в запасе конца дня (№78): позже конца дня минус запас, но не позже конца дня — не опоздание,
    мягкая пометка."""
    return ctx.end_reserve_min > 0 and ctx.tn.work_minutes - ctx.end_reserve_min + _EPS < end <= ctx.tn.work_minutes + _EPS


def plan_view(ctx: DayContext, stops: Sequence[Stop], draft: Draft,
              info: Callable[[Stop], dict[str, Any]], explain: bool = True, crew: Crew | None = None) -> dict[str, Any]:
    """Рейсы черновика с цифрами по текущим заказам: машины → рейсы по порядку (выезд, возвращение,
    км, литры, загрузка), точки по порядку; «ещё не в рейсах»; «не помещается» и совет, что с этим делать (advice —
    _advice). Время — рейсы машины подряд с ожиданием у окон приёма; у точки — прибытие (eta), вне окна (window_miss —
    бывает после правки логиста), в центре (center) и в центре на машине без права въезда (center_miss). Пояснение
    «почему так» — у рейса explain (_trip_explain), у дня — explain (_day_explain): только цифры того же расчёта, текст
    строит страница.
    explain=False — без них (км до правки, прогноз для «план — факт»: литры других машин и объезд не нужны).
    crew — водители дня (№77): совет «добавить машину» — только из машин с вышедшим свободным водителем (spare_trucks).
    Обед в пути (№61): у рейса, где он есть, — lunch: где (where: store — после разгрузки у магазина, depot — на складе
    до загрузки, road — в дороге в конце окна обеда), начало и конец (HH:MM; на складе и в дороге — весь обед, у магазина
    — сколько добавилось после разгрузки), минут обеда, добавлено к рейсу, после какой точки (номер в stops; None — до
    первой); время рейса и машины — с ним. Без обеда план — прежний до байта.
    Запас на рейс (№66): у рейса с запасом — buffer: минуты и с какого времени (возвращение по медиане; return — с
    запасом). Без выученного запаса — прежний до байта."""
    routable = {s.customer_id: s for s in stops if s.point is not None}
    _clean(draft, routable)
    shares = _shares(draft.trips)
    window = ctx.tn.work_minutes
    # принятая переработка: «опаздывает» — только позже предела; позже конца дня — пометка late
    limit = ctx.overtime_minutes if draft.overtime_ok and ctx.overtime_minutes is not None else window
    parts: dict[int, dict[str, Any]] = {}
    times = _timeline(ctx, draft.trips, routable, shares, parts)
    sel = _selected(ctx, draft.trucks)
    per_truck: dict[str, dict[str, Any]] = {}
    trips_json = []
    for t in draft.trips:
        cids, km, _, kg = _route(ctx, t.stops, routable, shares, reorder=False)
        truck = ctx.trucks.get(t.truck)
        cap = truck.capacity_kg if truck else None
        kgs = [routable[c].kg / shares[c] for c in cids]
        cost = fl.trip_running_cost([routable[c].point for c in cids], kgs, ctx.depot, ctx.norms, truck) if truck else None
        slot = per_truck.setdefault(t.truck, {'used': 0.0, 'trips': []})
        free = slot['used']
        depart, minutes, arrivals = times[t.id]
        slot['used'] = depart + minutes
        load_min = ctx.tn.load(kg)
        preloaded = parts[t.id].get('preloaded', False)
        if preloaded:   # №78: загружен с вечера — выезд там, где по расчёту кончилась бы загрузка; в дне машины её нет
            depart, minutes, load_min = depart + load_min, minutes - load_min, 0.0
        revenue = math.fsum(routable[c].revenue / shares[c] for c in cids)
        marks = []
        # у точки — и слагаемые её времени (езда от предыдущей точки, ожидание окна, разгрузка), приезд (eta минус
        # ожидание окна) и запас до конца окна
        for c, at, (drive, wait, unload) in zip(cids, arrivals, parts[t.id]['legs']):
            central = _central(ctx, routable[c])
            late = _span(ctx, c)[1]
            city = ctx.tn.yerevan_of(t.truck) if _yerevan(ctx, routable[c]) else 0.0   # №68: уже в unload_min
            marks.append({'eta': _hhmm(ctx.work_start_min + at), 'window_miss': at > late + _EPS,
                          'center': central, 'center_miss': central and not (truck is not None and truck.center_ok),
                          'vehicle_miss': not _vehicle_ok(ctx, c, t.truck),
                          'arrive': _hhmm(ctx.work_start_min + at - wait),   # приехал; eta — начало разгрузки
                          'drive_min': _r(drive), 'wait_min': _r(wait), 'unload_min': _r(unload),
                          'margin_min': _r(late - at) if math.isfinite(late) else None,
                          **({'yerevan_min': _r(city)} if city else {})})
        tj = {
            'id': t.id, 'truck': t.truck, 'pinned': t.pinned,
            'km': _r(km), 'minutes': round(minutes), 'kg': round(kg),
            'revenue': round(revenue),
            'liters': _r(cost.liters) if cost else None,
            'wear_amd': round(cost.wear_amd) if cost else None,
            'operating_cost_amd': round(cost.total_amd(ctx.tn.fuel_price)) if cost else None,
            'payload_tonne_km': _r(cost.payload_tonne_km) if cost else None,
            'fuel_load_configured': cost.fuel_load_configured if cost else False,
            'wear_configured': cost.wear_configured if cost else False,
            'load_pct': round(kg / cap * 100.0) if cap else None,
            'loading_start': _hhmm(ctx.work_start_min + depart),
            'loading_minutes': _r(load_min),
            'depart': _hhmm(ctx.work_start_min + depart + load_min), 'return': _hhmm(ctx.work_start_min + slot['used']),
            'over_time': slot['used'] > limit + _EPS, 'late': slot['used'] > window + _EPS,
            **({'preloaded': True} if preloaded else {}),
            # №78: «Բեռնված է ժ. HH:MM (ով)» — время по Еревану из отметки, кто — логин (только странице, не в AI)
            **({'loaded': {'at': t.loaded['at'][11:16], 'by': t.loaded['by']}} if t.loaded is not None else {}),
            **({'in_reserve': True} if _in_reserve(ctx, slot['used']) else {}),
            # бедный — по полной выручке точек: тяжёлый заказ на несколько поездок перенос не объединит
            'poor': ctx.min_trip_revenue > 0
                    and math.fsum(routable[c].revenue for c in cids) < ctx.min_trip_revenue,
            'over_capacity': cap is not None and kg > cap + 0.5,
            'no_truck': truck is None,
            'stops': [{**info(routable[c]), 'kg': round(routable[c].kg / shares[c]),
                       'share': shares[c], **mark} for c, mark in zip(cids, marks)],
            'window_miss': sum(1 for x in marks if x['window_miss']),
            'center_miss': sum(1 for x in marks if x['center_miss']),
            'vehicle_miss': sum(1 for x in marks if x['vehicle_miss']),
        }
        brk = parts[t.id].get('lunch')
        if brk is not None:
            meal = ctx.tn.lunch_minutes if brk.stop is None else brk.added
            after = (brk.stop - 1 if brk.stop else None) if brk.road else brk.stop
            tj['lunch'] = {'start': _hhmm(ctx.work_start_min + brk.at), 'end': _hhmm(ctx.work_start_min + brk.at + meal),
                           'minutes': _r(ctx.tn.lunch_minutes), 'added_min': _r(brk.added), 'after_stop': after,
                           'where': 'road' if brk.road else 'depot' if brk.stop is None else 'store'}
        if parts[t.id].get('buffer'):
            tj['buffer'] = {'minutes': _r(parts[t.id]['buffer']),
                            'start': _hhmm(ctx.work_start_min + slot['used'] - parts[t.id]['buffer'])}
        if explain:
            got = {**parts[t.id], 'loading': 0.0} if preloaded else parts[t.id]
            tj['explain'] = _trip_explain(ctx, sel, t.truck, truck, cids, routable, kgs, got, free, depart, minutes)
        slot['trips'].append(tj)
        trips_json.append(tj)
    trucks_json = []
    for code in sorted(per_truck, key=lambda c: (c not in draft.trucks, c)):
        slot = per_truck[code]
        truck = ctx.trucks.get(code)
        ts = slot['trips']
        trucks_json.append({
            'car_code': code, 'name': truck.name if truck else None,
            'capacity_kg': truck.capacity_kg if truck else None, 'l100': truck.l100 if truck else None,
            'center_ok': truck.center_ok if truck else None, **({'big': True} if big_shown(ctx, truck) else {}),
            'trips': ts, 'km': _r(math.fsum(t['km'] for t in ts)),
            'liters': _r(math.fsum(t['liters'] or 0.0 for t in ts)), 'kg': sum(t['kg'] for t in ts),
            'wear_amd': sum(t['wear_amd'] or 0 for t in ts),
            'operating_cost_amd': sum(t['operating_cost_amd'] or 0 for t in ts),
            'fuel_load_configured': all(t['fuel_load_configured'] for t in ts),
            'wear_configured': all(t['wear_configured'] for t in ts),
            'stops': sum(len(t['stops']) for t in ts), 'minutes': round(slot['used']),
            'loading_minutes': _r(sum(t['loading_minutes'] for t in ts)),
            'payload_tonne_km': _r(sum(t['payload_tonne_km'] or 0 for t in ts)),
            'return': ts[-1]['return'], 'over_time': slot['used'] > limit + _EPS,
            'late': slot['used'] > window + _EPS, **({'in_reserve': True} if _in_reserve(ctx, slot['used']) else {}),
        })
    in_trips = set(shares)
    missing_coords = [s for s in stops if s.point is None]
    assigned_kg = math.fsum(s.kg for s in stops if s.customer_id in in_trips)
    unassigned = [info(s) | {'kg': round(s.kg), 'no_room': s.customer_id in draft.no_room,
                             'no_window': s.customer_id in draft.no_window,
                             'no_center': s.customer_id in draft.no_center, 'center': _central(ctx, s),
                             'no_vehicle': s.customer_id in ctx.vehicle_access and not any(
                                 _vehicle_ok(ctx, s.customer_id, code) for code in draft.trucks if code in ctx.trucks)}
                  for s in stops if s.point is not None and s.customer_id not in in_trips]
    over = [t for t in trips_json if t['over_time'] or t['over_capacity'] or t['no_truck'] or t['vehicle_miss']]
    km_total = math.fsum(t['km'] for t in trips_json)
    return {
        'trucks': trucks_json,
        'unassigned': unassigned,
        'overflow': {'trips': len(over), 'kg': sum(t['kg'] for t in over),
                     'unassigned_kg': sum(u['kg'] for u in unassigned)},
        'advice': _advice(ctx, draft, trips_json, unassigned, crew),
        'summary': {'trips': len(trips_json), 'trucks': len(trucks_json), 'km': _r(km_total),
                    'poor_trips': sum(1 for t in trips_json if t['poor']),
                    'liters': _r(math.fsum(t['liters'] or 0.0 for t in trips_json)),
                    'wear_amd': sum(t['wear_amd'] or 0 for t in trips_json),
                    'operating_cost_amd': sum(t['operating_cost_amd'] or 0 for t in trips_json),
                    'fuel_load_unconfigured': sum(not t['fuel_load_configured'] for t in trips_json),
                    'wear_unconfigured': sum(not t['wear_configured'] for t in trips_json),
                    'fuel_price_amd': ctx.tn.fuel_price,
                    'fuel_price_estimated': ctx.tn.fuel_price_estimated,
                    'loading_minutes': _r(sum(t['loading_minutes'] for t in trips_json)),
                    'loading_configured': ctx.tn.loading_configured,
                    # №78: вне сезона первые рейсы загружены с вечера; запас в конце дня, мин
                    'preload': ctx.tn.preload, 'end_reserve_min': ctx.end_reserve_min,
                    'traffic': (ctx.norms.traffic_status or (ctx.norms.traffic.report if ctx.norms.traffic is not None else
                                {'status': 'static', 'live': False})),
                    'kg': round(assigned_kg), 'stops': len(in_trips),
                    'window_miss': sum(t['window_miss'] for t in trips_json),
                    'center_miss': sum(t['center_miss'] for t in trips_json),
                    'vehicle_miss': sum(t['vehicle_miss'] for t in trips_json)},
        'coverage': {'stops_total': len(stops), 'stops_assigned': len(in_trips),
                     'stops_no_coords': len(missing_coords), 'stops_unassigned': len(unassigned),
                     'kg_total': round(math.fsum(s.kg for s in stops)), 'kg_assigned': round(assigned_kg),
                     'kg_no_coords': round(math.fsum(s.kg for s in missing_coords)),
                     'revenue_no_coords': round(math.fsum(s.revenue for s in missing_coords)),
                     'complete': not missing_coords and not unassigned},
        **({'explain': _day_explain(ctx, routable, draft, sel, {c: len(s['trips']) for c, s in per_truck.items()})}
           if explain else {}),
    }


# --- Сравнение «по менеджерам» ---

def history_cars(agent_cars: Mapping[int, Sequence[str | int]], van_trucks: Mapping[int, str]) -> dict[int, tuple[str, ...]]:
    """Машины менеджера по истории: экспедитор без машины (int) → закреплённая за ним ручная машина; не
    закреплена — пропускается. Порядок (от самой частой) и без повторов."""
    out: dict[int, tuple[str, ...]] = {}
    for agent, cars in agent_cars.items():
        codes = [c if isinstance(c, str) else van_trucks.get(c, '') for c in cars]
        out[agent] = tuple(dict.fromkeys(c for c in codes if c))
    return out


def manager_trucks(stops: Sequence[Stop], agent_cars: Mapping[int, Sequence[str]],
                   trucks: Sequence[fl.FleetTruck]) -> dict[str, list[Stop]]:
    """Привычная схема: заказы менеджера везёт его машина по истории (SALES.fDELIVERYCAR за 90 дней) —
    самая частая из работающих сегодня; у менеджера таких нет — самая большая машина дня."""
    codes = {t.car_code for t in trucks}
    if not codes:
        return {}
    fallback = max(trucks, key=lambda t: (t.capacity_kg, -t.l100, t.car_code)).car_code
    out: dict[str, list[Stop]] = {}
    for s in stops:
        car = next((c for c in agent_cars.get(s.agent_id, ()) if c in codes), fallback)
        out.setdefault(car, []).append(s)
    return out


def _group_km(ctx: DayContext, groups: Mapping[str, Sequence[Stop]]) -> dict[str, Any]:
    """Каждая машина везёт свои точки лучшим для неё маршрутом (рейсы по тоннажу, 2-opt)."""
    km = liters = wear = 0.0
    trips = extra = 0
    per = []
    for code in sorted(groups):
        truck = ctx.trucks[code]
        ss = [s for s in groups[code] if s.point is not None]
        if not ss:
            continue
        ts = fl.route_day([s.point for s in ss], [s.kg for s in ss], [s.revenue for s in ss], ctx.depot,
                          [truck], ctx.norms, ctx.tn)
        k = math.fsum(t.km for t in ts)
        km += k
        liters += math.fsum(t.liters for t in ts)
        wear += math.fsum(t.wear_amd for t in ts)
        trips += len(ts)
        extra += sum(1 for t in ts if t.extra)
        per.append({'car_code': code, 'stops': len(ss), 'kg': round(math.fsum(s.kg for s in ss)),
                    'trips': len(ts), 'km': _r(k), 'over_time': any(t.extra for t in ts)})
    return {'km': _r(km), 'liters': _r(liters), 'wear_amd': round(wear),
            'operating_cost_amd': round(liters * ctx.tn.fuel_price + wear),
            'trips': trips, 'trips_over_time': extra, 'trucks': per}


def baseline(ctx: DayContext, stops: Sequence[Stop], draft: Draft,
             agent_cars: Mapping[int, Sequence[str]]) -> dict[str, Any] | None:
    """«Как обычно»: те же заказы, что в рейсах плана (без исключённых и не поместившихся), разложенные
    по машинам менеджеров."""
    in_trips = {c for t in draft.trips for c in t.stops}
    routable = [s for s in stops if s.point is not None and s.customer_id in in_trips]
    sel = _selected(ctx, draft.trucks)
    if not routable or not sel:
        return None
    return _group_km(ctx, manager_trucks(routable, agent_cars, sel))


# --- План и факт (прошедшая дата) ---

def plan_vs_fact(ctx: DayContext, docs: Sequence[ShippedDoc], coord: Callable[[int], Coord],
                 van_trucks: Mapping[int, str] | None = None) -> dict[str, Any]:
    """Те же доставки дня: как их фактически развезли машины ERP (каждая — лучшим для неё маршрутом)
    против рейсов программы на тех же машинах. Накладная без машины, которую вёз экспедитор, — рейс его
    ручной машины (van_trucks: экспедитор → машина). Машины без тоннажа и расхода в настройках и клиенты
    без координат в сравнение не входят (их число — в skipped)."""
    vans = van_trucks or {}
    groups: dict[str, dict[int, list[ShippedDoc]]] = {}
    skipped_docs = skipped_kg = 0.0
    no_car: set[str] = set()
    for d in docs:
        car = fact_car(d, vans)
        if car not in ctx.trucks or coord(d.customer_id).point is None:
            skipped_docs += 1
            skipped_kg += d.kg
            if car and car not in ctx.trucks:
                no_car.add(car)
            continue
        groups.setdefault(car, {}).setdefault(d.customer_id, []).append(d)

    def stop(cid: int, ds: Sequence[ShippedDoc]) -> Stop:
        c = coord(cid)
        return Stop(cid, c.point, c.source, math.fsum(x.kg for x in ds), math.fsum(x.revenue for x in ds), (),
                    ds[0].agent_id)

    fact = _group_km(ctx, {car: [stop(c, ds) for c, ds in sorted(cs.items())] for car, cs in groups.items()})
    merged: dict[int, list[ShippedDoc]] = {}
    for cs in groups.values():
        for c, ds in cs.items():
            merged.setdefault(c, []).extend(ds)
    all_stops = [stop(c, merged[c]) for c in sorted(merged)]
    trucks = [ctx.trucks[c] for c in sorted(groups)]
    trips = fl.route_day([s.point for s in all_stops], [s.kg for s in all_stops], [s.revenue for s in all_stops],
                         ctx.depot, trucks, ctx.norms, ctx.tn) if all_stops else []
    plan = {'km': _r(math.fsum(t.km for t in trips)), 'liters': _r(math.fsum(t.liters for t in trips)),
            'trips': len(trips), 'trips_over_time': sum(1 for t in trips if t.extra),
            'max_load_pct': round(max((t.kg / t.capacity_kg * 100.0 for t in trips), default=0.0))}
    return {'day': ctx.day.isoformat(), 'stops': len(all_stops), 'kg': round(math.fsum(s.kg for s in all_stops)),
            'trucks': sorted(groups), 'fact': fact, 'plan': plan,
            'saved_km': _r(fact['km'] - plan['km']),
            'skipped': {'docs': int(skipped_docs), 'kg': round(skipped_kg), 'cars_not_set': sorted(no_car)}}

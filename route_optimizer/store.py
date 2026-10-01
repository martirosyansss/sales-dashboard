# -*- coding: utf-8 -*-
"""Собственное хранилище раздела «Маршруты» — SQLite (route_optimizer.db).

ERP только читаем, поэтому настройки, склад, машины и профили менеджеров живут здесь.
- соединение на операцию, WAL, busy_timeout;
- схема создаётся при первом обращении, версия — meta.schema_version; база старой версии
  мигрирует одной транзакцией (_MIGRATIONS);
- сохранение — одна транзакция: всё или ничего;
- битая БД или битое значение — явная StoreError, а НЕ тихий откат на дефолты.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Collection, Mapping

from .geo import ARMENIA_LAT, ARMENIA_LON, Point, is_valid_point
from .patterns import parse_freq_key, parse_pattern_key, parse_plan_freq_key, parse_transfer_key

SCHEMA_VERSION = 8

# manager_profile.included: 1/0 — выбор владельца, NULL — «авто» (в расчёте, если есть работа за 8 недель)
_MANAGER_PROFILE_COLUMNS = (
    "agent_id INTEGER PRIMARY KEY, included INTEGER, home_lat REAL, home_lon REAL, "
    "car_fuel_l_per_100km REAL, car_fuel_type TEXT, updated_at TEXT NOT NULL, updated_by TEXT")

# Этап 3: решения владельца по предложениям (value — канонический JSON шаблона или частоты) и
# последние расчёты оптимизации (params, result — JSON).
# Схема 3 — как была создана миграцией 2 → 3 (история миграций не меняется).
_DECISION_TABLE_V3 = (
    "CREATE TABLE IF NOT EXISTS decision(customer_id INTEGER NOT NULL, agent_id INTEGER NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('pattern', 'freq')), value TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected')), "
    "updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (customer_id, agent_id, kind, value))")
# Схема 4: from_value — шаблон или частота клиента, от которых принималось решение (решение
# привязано к плану: план в ERP изменился — решение устарело); retired — решение отработало
# (исполнено в ERP или отклонённое предложение потеряло смысл) и больше не действует.
# Как была создана миграцией 3 → 4 (история миграций не меняется).
_DECISION_COLUMNS_V4 = (
    "customer_id INTEGER NOT NULL, agent_id INTEGER NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('pattern', 'freq')), value TEXT NOT NULL, from_value TEXT, "
    "status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'retired')), "
    "updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (customer_id, agent_id, kind, value)")
# Схема 5 (§15): вид решения remove — убрать клиента из маршрута менеджера (value — REMOVE_VALUE,
# from_value — шаблон клиента, от которого принято решение).
# Как была создана миграцией 4 → 5 (история миграций не меняется).
_DECISION_COLUMNS_V5 = (
    "customer_id INTEGER NOT NULL, agent_id INTEGER NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('pattern', 'freq', 'remove')), value TEXT NOT NULL, "
    "from_value TEXT, status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'retired')), "
    "updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (customer_id, agent_id, kind, value)")
# Схема 6 (этап 4): вид решения transfer — передать клиента от менеджера agent_id другому менеджеру
# (value — patterns.transfer_key: кому и в какие дни; from_value — шаблон клиента у agent_id).
_DECISION_COLUMNS = (
    "customer_id INTEGER NOT NULL, agent_id INTEGER NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('pattern', 'freq', 'remove', 'transfer')), value TEXT NOT NULL, "
    "from_value TEXT, status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'retired')), "
    "updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (customer_id, agent_id, kind, value)")
_DECISION_COPY = 'customer_id, agent_id, kind, value, from_value, status, updated_at, updated_by'
_DECISION_TABLE = f"CREATE TABLE IF NOT EXISTS decision({_DECISION_COLUMNS})"
# на клиента у менеджера — не больше одного принятого решения каждого вида
_DECISION_ONE_ACCEPTED = (
    "CREATE UNIQUE INDEX IF NOT EXISTS decision_one_accepted ON decision(customer_id, agent_id, kind) "
    "WHERE status = 'accepted'")
_SCENARIO_TABLE = (
    "CREATE TABLE IF NOT EXISTS scenario(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
    "created_by TEXT, params TEXT NOT NULL, result TEXT NOT NULL)")
SCENARIOS_KEPT = 5
# Схема 7 (план развоза): ручная точка клиента — перекрывает ERP и GPS во всём разделе; черновик плана
# развоза на дату (data — JSON: машины дня, исключённые заказы, рейсы; rev — номер правки).
_GEO_OVERRIDE_TABLE = (
    "CREATE TABLE IF NOT EXISTS customer_geo_override(customer_id INTEGER PRIMARY KEY, "
    "lat REAL NOT NULL, lon REAL NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT)")
_DISPATCH_TABLE = (
    "CREATE TABLE IF NOT EXISTS dispatch_plan(day TEXT PRIMARY KEY, data TEXT NOT NULL, "
    "rev INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL, updated_by TEXT)")
DISPATCH_KEPT_DAYS = 120    # черновики старше — удаляются при сохранении
# Машины. Схема 1–7 — как была создана (история миграций не меняется).
_TRUCKS_TABLE_V1 = (
    "CREATE TABLE IF NOT EXISTS trucks(car_code TEXT PRIMARY KEY, capacity_kg REAL, "
    "fuel_l_per_100km REAL, agent_id INTEGER, active INTEGER NOT NULL DEFAULT 1, "
    "updated_at TEXT NOT NULL, updated_by TEXT)")
# Схема 8: active NULL — «авто» (машина ERP возила за CAR_IDLE_DAYS дней и не закрыта); 1/0 — выбор владельца.
# manual = 1 — машина, которой нет в ERP (владелец добавил сам: экспедиторы возят без машины в накладных):
# name — марка/название, van_agent_id — экспедитор ERP (SALES.fVANAGENTID), чьи накладные без машины
# считаются её рейсами; один экспедитор — не больше одной машины.
_TRUCKS_COLUMNS = (
    "car_code TEXT PRIMARY KEY, capacity_kg REAL, fuel_l_per_100km REAL, agent_id INTEGER, active INTEGER, "
    "manual INTEGER NOT NULL DEFAULT 0, name TEXT, van_agent_id INTEGER, updated_at TEXT NOT NULL, updated_by TEXT")
_TRUCKS_TABLE = f"CREATE TABLE IF NOT EXISTS trucks({_TRUCKS_COLUMNS})"
_TRUCKS_ONE_VAN = (
    "CREATE UNIQUE INDEX IF NOT EXISTS trucks_one_van ON trucks(van_agent_id) WHERE van_agent_id IS NOT NULL")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS depot(id INTEGER PRIMARY KEY CHECK (id = 1), "
    "lat REAL NOT NULL, lon REAL NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT)",
    _TRUCKS_TABLE,
    f"CREATE TABLE IF NOT EXISTS manager_profile({_MANAGER_PROFILE_COLUMNS})",
    _DECISION_TABLE,
    _SCENARIO_TABLE,
    _DECISION_ONE_ACCEPTED,
    _GEO_OVERRIDE_TABLE,
    _DISPATCH_TABLE,
    _TRUCKS_ONE_VAN,
)

# Миграции: версия → DDL перехода на следующую. Выполняются в одной транзакции с записью версии.
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    # 1 → 2: included становится NULL-абельным («авто»). SQLite не меняет ограничения столбца —
    # пересобираем таблицу; сохранённые 1/0 остаются явным выбором владельца (что из них было
    # «по умолчанию», по старой базе не отличить).
    1: (
        f"CREATE TABLE manager_profile_v2({_MANAGER_PROFILE_COLUMNS})",
        "INSERT INTO manager_profile_v2(agent_id, included, home_lat, home_lon, car_fuel_l_per_100km, "
        "car_fuel_type, updated_at, updated_by) SELECT agent_id, included, home_lat, home_lon, "
        "car_fuel_l_per_100km, car_fuel_type, updated_at, updated_by FROM manager_profile",
        "DROP TABLE manager_profile",
        "ALTER TABLE manager_profile_v2 RENAME TO manager_profile",
    ),
    # 2 → 3 (этап 3): решения владельца и сохранённые расчёты; прежние таблицы не меняются
    2: (_DECISION_TABLE_V3, _SCENARIO_TABLE),
    # 3 → 4: решения — с from_value (прежние — NULL: от чего принимались, неизвестно) и статусом
    # retired. SQLite не меняет CHECK столбца — пересобираем таблицу. Затем — не больше одного
    # принятого решения вида на клиента у менеджера (дубли, если есть, — кроме последнего
    # по времени) и частичный уникальный индекс, который это гарантирует дальше.
    3: (
        f"CREATE TABLE decision_v4({_DECISION_COLUMNS_V4})",
        "INSERT INTO decision_v4(customer_id, agent_id, kind, value, from_value, status, updated_at, "
        "updated_by) SELECT customer_id, agent_id, kind, value, NULL, status, updated_at, updated_by "
        "FROM decision",
        "DROP TABLE decision",
        "ALTER TABLE decision_v4 RENAME TO decision",
        "DELETE FROM decision WHERE status = 'accepted' AND EXISTS (SELECT 1 FROM decision AS d2 "
        "WHERE d2.status = 'accepted' AND d2.customer_id = decision.customer_id "
        "AND d2.agent_id = decision.agent_id AND d2.kind = decision.kind "
        "AND (d2.updated_at > decision.updated_at "
        "OR (d2.updated_at = decision.updated_at AND d2.value > decision.value)))",
        _DECISION_ONE_ACCEPTED,
    ),
    # 4 → 5 (§15): вид решения remove. SQLite не меняет CHECK столбца — пересобираем таблицу: все
    # строки переносятся как есть; частичный уникальный индекс уходит вместе со старой таблицей и
    # создаётся заново (на клиента у менеджера — одно принятое решение каждого вида, и remove тоже).
    4: (
        f"CREATE TABLE decision_v5({_DECISION_COLUMNS_V5})",
        f"INSERT INTO decision_v5({_DECISION_COPY}) SELECT {_DECISION_COPY} FROM decision",
        "DROP TABLE decision",
        "ALTER TABLE decision_v5 RENAME TO decision",
        _DECISION_ONE_ACCEPTED,
    ),
    # 5 → 6 (этап 4): вид решения transfer — так же, как 4 → 5: таблица пересобирается, строки
    # переносятся как есть, частичный уникальный индекс создаётся заново. Настройки (penalty_transfer,
    # transfer_radius_km) миграции не требуют: нет ключа — значение по умолчанию.
    5: (
        f"CREATE TABLE decision_v6({_DECISION_COLUMNS})",
        f"INSERT INTO decision_v6({_DECISION_COPY}) SELECT {_DECISION_COPY} FROM decision",
        "DROP TABLE decision",
        "ALTER TABLE decision_v6 RENAME TO decision",
        _DECISION_ONE_ACCEPTED,
    ),
    # 6 → 7 (план развоза): две новые таблицы; прежние таблицы и значения не меняются
    6: (_GEO_OVERRIDE_TABLE, _DISPATCH_TABLE),
    # 7 → 8 (ручные машины и «авто» для «активна»): active становится NULL-абельным — пересобираем таблицу.
    # Сохранённые 1 и 0 — выбор владельца (решение владельца: он заполнял машины как рабочие), переносятся
    # как есть; «авто» — только у машин без записи и у новых машин ERP. Остальные значения — как были.
    7: (
        f"CREATE TABLE trucks_v8({_TRUCKS_COLUMNS})",
        "INSERT INTO trucks_v8(car_code, capacity_kg, fuel_l_per_100km, agent_id, active, manual, name, "
        "van_agent_id, updated_at, updated_by) SELECT car_code, capacity_kg, fuel_l_per_100km, agent_id, "
        "active, 0, NULL, NULL, updated_at, updated_by FROM trucks",
        "DROP TABLE trucks",
        "ALTER TABLE trucks_v8 RENAME TO trucks",
        _TRUCKS_ONE_VAN,
    ),
}

FUEL_TYPES = ('diesel', 'petrol', 'lpg')
DEFAULT_MANAGER_FUEL = 'petrol'

DEFAULT_SETTINGS: dict[str, Any] = {
    'work_start': '09:00',
    'work_end': '18:00',
    'workdays': [1, 2, 3, 4, 5, 6],
    # Длительность визита, мин: None — «авто» по GPS-стоянкам, без калибровки — 7 / 10 / 20
    # (evaluate.visit_norms); число перекрывает только свой класс
    'visit_min_small': None,
    'visit_min_medium': None,
    'visit_min_large': None,
    'size_small_max_kg': 60,
    'size_medium_max_kg': 250,
    'chain_groups': [],
    'min_day_revenue': 100000,
    'min_trip_revenue': 150000,
    'truck_priority': 1.5,
    # Нормы дорог: None — «авто»: калибровка по GPS-трекам, без неё — 1.3 / 25 / 45 (evaluate.road_norms)
    'detour_factor': None,
    'speed_city_kmh': None,
    'speed_region_kmh': None,
    'city_center_lat': 40.1792,
    'city_center_lon': 44.4991,
    'city_radius_km': 12,
    'fuel_price_diesel': None,
    'fuel_price_petrol': None,
    'fuel_price_lpg': None,
    'manager_car_default_l_per_100km': 9,
    'low_season_index_max': 0.80,
    'peak_season_index_min': 1.20,
    'low_months': None,
    'peak_months': None,
    # Этап 3 — оптимизация дней визитов (§4): штрафы в драмах в неделю, ABC, запас частоты
    'penalty_weak_day': 20000,
    'penalty_poor_trip': 3000,
    'penalty_overtime_per_min': 500,
    'penalty_change': 300,
    'fuel_price_fallback': 500,          # вес топлива в поиске, пока цена не задана
    'optimizer_seconds_per_manager': 8,  # страховочный предел времени поиска
    'abc_a_share': 0.5,
    'abc_b_share': 0.3,
    'freq_safety': 1.0,
    # §15 — статус клиента по давности последнего заказа (status.customer_status), дни и множители
    # обычного интервала между заказами
    'status_new_days': 60,     # первый заказ позже — «новый», не трогаем
    'dormant_min_days': 45,    # «затих»: не покупает дольше max(45 дн, 3 × интервал)
    'dormant_mult': 3,
    'lost_min_days': 120,      # «потерян»: дольше max(120 дн, 6 × интервал)
    'lost_mult': 6,
    # Этап 4 — передача магазинов между менеджерами (режим Б): плата за передачу, драм в неделю
    # (передача должна окупаться заметно), и радиус «у менеджера есть клиент рядом», км
    'penalty_transfer': 2000,
    'transfer_radius_km': 1.5,
    # Парк машин (fleet-plan §2): рабочий день машины и разгрузка на точке — 8 мин + 6 мин на тонну.
    # Нет ключа в базе — значение по умолчанию: миграция не нужна
    'truck_work_start': '09:00',
    'truck_work_end': '18:00',
    'unload_min_per_stop': 8,
    'unload_min_per_tonne': 6,
    # «Развоз»: до этого времени менеджеры ещё принимают заказы на следующий рабочий день (заканчивают
    # ≈ 16:40) — страница подсказывает собирать рейсы позже. Нет ключа в базе — значение по умолчанию
    'dispatch_ready_time': '17:00',
}

# Числовые настройки: ключ -> (мин, макс, допускается null)
_NUMERIC: dict[str, tuple[float, float, bool]] = {
    'visit_min_small': (1, 120, True),
    'visit_min_medium': (1, 120, True),
    'visit_min_large': (1, 120, True),
    'size_small_max_kg': (0, 100000, False),     # строго > 0 — проверяется отдельно
    'size_medium_max_kg': (0, 100000, False),
    'min_day_revenue': (0, 1e8, False),
    'min_trip_revenue': (0, 1e8, False),
    'truck_priority': (1, 10, False),
    'detour_factor': (1.0, 3.0, True),
    'speed_city_kmh': (5, 120, True),
    'speed_region_kmh': (5, 120, True),
    'city_center_lat': (ARMENIA_LAT[0], ARMENIA_LAT[1], False),
    'city_center_lon': (ARMENIA_LON[0], ARMENIA_LON[1], False),
    'city_radius_km': (1, 50, False),
    'fuel_price_diesel': (1, 10000, True),
    'fuel_price_petrol': (1, 10000, True),
    'fuel_price_lpg': (1, 10000, True),
    'manager_car_default_l_per_100km': (1, 40, False),
    'low_season_index_max': (0.1, 1.0, False),
    'peak_season_index_min': (1.0, 3.0, False),
    'penalty_weak_day': (0, 1e7, False),
    'penalty_poor_trip': (0, 1e7, False),
    'penalty_overtime_per_min': (0, 1e6, False),
    'penalty_change': (0, 1e6, False),
    'fuel_price_fallback': (1, 10000, False),
    'optimizer_seconds_per_manager': (1, 120, False),
    'abc_a_share': (0.05, 0.95, False),
    'abc_b_share': (0.05, 0.95, False),
    'freq_safety': (0.5, 3.0, False),
    'status_new_days': (1, 365, False),
    'dormant_min_days': (1, 365, False),
    'dormant_mult': (1, 20, False),
    'lost_min_days': (1, 730, False),
    'lost_mult': (1, 50, False),
    'penalty_transfer': (0, 1e6, False),
    'transfer_radius_km': (0.1, 20, False),
    'unload_min_per_stop': (0, 120, False),
    'unload_min_per_tonne': (0, 120, False),
}

TRUCK_CAPACITY_KG = (100, 30000)
TRUCK_FUEL_L100 = (1, 80)
MANUAL_TRUCKS_MAX = 50
MANUAL_CODE_RE = re.compile(r'^[\w\- ]{1,20}$')   # номер машины: буквы, цифры, пробел, дефис
MANUAL_NAME_MAX = 60
MANAGER_FUEL_L100 = (1, 40)
_MAX_LIST = 500

_HHMM_RE = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')


class StoreError(RuntimeError):
    """БД настроек маршрутов недоступна или повреждена. Текст — для пользователя."""


@dataclass(frozen=True)
class Truck:
    """Машина парка. active None — «авто» (только у машин ERP: Bundle.resolved_trucks); manual — машины нет
    в ERP, владелец добавил её сам (name — марка/название, van_agent_id — экспедитор ERP)."""
    car_code: str
    capacity_kg: float | None = None
    fuel_l_per_100km: float | None = None
    agent_id: int | None = None
    active: bool | None = True
    updated_at: str | None = None
    updated_by: str | None = None
    manual: bool = False
    name: str | None = None
    van_agent_id: int | None = None


@dataclass(frozen=True)
class ManagerProfile:
    agent_id: int
    included: bool | None = None   # None — «авто»: в расчёте, если у агента есть работа за 8 недель
    home_lat: float | None = None
    home_lon: float | None = None
    car_fuel_l_per_100km: float | None = None
    car_fuel_type: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None

    @property
    def home(self) -> Point | None:
        if self.home_lat is None or self.home_lon is None:
            return None
        return (self.home_lat, self.home_lon)


@dataclass(frozen=True)
class Bundle:
    """Все настройки раздела одним снимком."""
    settings: dict[str, Any]
    depot: Point | None
    trucks: dict[str, Truck]
    managers: dict[int, ManagerProfile]
    # ручные точки клиентов (customer_geo_override): перекрывают ERP и GPS во всём разделе
    geo_overrides: dict[int, Point] = field(default_factory=dict)

    def profile(self, agent_id: int) -> ManagerProfile:
        return self.managers.get(agent_id) or ManagerProfile(agent_id)

    def included(self, agent_id: int, active_agents: Collection[int]) -> bool:
        """В расчёте ли менеджер: явный выбор владельца; «авто» (записи нет или included NULL) —
        есть ли у агента работа (заказы или визиты за 8 недель, Snapshot.active_agents)."""
        profile = self.managers.get(agent_id)
        if profile is None or profile.included is None:
            return agent_id in active_agents
        return profile.included

    def included_source(self, agent_id: int) -> str:
        """manual — владелец отметил «В расчёте» явно; auto — решает работа за 8 недель."""
        profile = self.managers.get(agent_id)
        return 'auto' if profile is None or profile.included is None else 'manual'

    def truck_active(self, code: str, active_cars: Collection[str]) -> bool:
        """Работает ли машина: явный выбор владельца; «авто» (записи нет или active NULL) — машина ERP
        возила за последние CAR_IDLE_DAYS дней и не закрыта (Snapshot.active_cars)."""
        t = self.trucks.get(code)
        if t is None or t.active is None:
            return code in active_cars
        return t.active

    def resolved_trucks(self, active_cars: Collection[str]) -> dict[str, Truck]:
        """Машины с действующим «активна» (bool) — для расчёта парка и развоза."""
        return {code: t if t.active is not None else replace(t, active=code in active_cars)
                for code, t in self.trucks.items()}

    def van_trucks(self) -> dict[int, str]:
        """Экспедитор → ручная машина: его накладные без машины в ERP — рейсы этой машины."""
        return {t.van_agent_id: code for code, t in sorted(self.trucks.items())
                if t.manual and t.van_agent_id is not None}

    def fingerprint(self) -> str:
        """Отпечаток для кэша оценки: меняется при любом сохранении."""
        data = {
            'settings': self.settings,
            'depot': self.depot,
            'trucks': [asdict(t) for _, t in sorted(self.trucks.items())],
            'managers': [asdict(m) for _, m in sorted(self.managers.items())],
            'geo': sorted(self.geo_overrides.items()),
        }
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


@dataclass(frozen=True)
class RefData:
    """Справочники ERP для проверки ссылок в настройках."""
    car_codes: frozenset[str]
    agent_ids: frozenset[int]      # агенты с шаблонами маршрутов
    group_codes: frozenset[str]    # группы клиентов (CustGrp)
    van_agent_ids: frozenset[int] = frozenset()   # экспедиторы: возили без машины в накладных (90 дней)


@dataclass(frozen=True)
class Changes:
    """Проверенные изменения для одной транзакции."""
    settings: dict[str, Any]       # полный набор после слияния с текущими
    depot_set: bool                # False — склад не трогаем
    depot: Point | None
    trucks: tuple[Truck, ...]
    managers: tuple[ManagerProfile, ...]
    manual_trucks: tuple[Truck, ...] | None = None   # None — ручные машины не трогаем; иначе — полный список


DECISION_KINDS = ('pattern', 'freq', 'remove', 'transfer')
DECISION_ACTIONS = ('accept', 'reject', 'reset')
REMOVE_VALUE = '[]'   # value решения remove: шаблон «без визитов» (patterns.pattern_key(()))


@dataclass(frozen=True)
class Decision:
    """Решение владельца по предложению (этап 3): accepted — закрепить, rejected — запретить
    шаблон (kind='pattern') или частоту (kind='freq') клиента у менеджера; remove (§15) —
    accepted: убрать клиента из маршрута менеджера, rejected: оставить; transfer (этап 4) —
    accepted: передать клиента другому менеджеру в указанные дни, rejected: этому менеджеру не передавать.
    value — канонический JSON: patterns.pattern_key / patterns.freq_key / REMOVE_VALUE /
    patterns.transfer_key; from_value —
    шаблон (pattern_key; у remove — тоже шаблон) или частота (freq_key) клиента, от которых
    принималось решение; None — неизвестно (решение схемы 3)."""
    customer_id: int
    agent_id: int
    kind: str
    value: str
    status: str
    updated_at: str | None = None
    updated_by: str | None = None
    from_value: str | None = None


@dataclass(frozen=True)
class DecisionInput:
    """Проверенное действие владельца для записи: accept | reject | reset (optimize.parse_decision)."""
    customer_id: int
    agent_id: int
    kind: str
    value: str
    action: str
    from_value: str | None = None


@dataclass(frozen=True)
class Scenario:
    """Сохранённый расчёт оптимизации (этап 3)."""
    id: str
    created_at: str
    created_by: str | None
    params: dict[str, Any]
    result: dict[str, Any]


# --- Проверка значений ---

def _fmt(x: float) -> str:
    return f'{x:g}'


def _check_number(value: Any, lo: float, hi: float, nullable: bool = False,
                  lo_exclusive: bool = False) -> tuple[Any, str | None]:
    if value is None:
        return (None, None) if nullable else (None, 'обязательное число')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, 'ожидалось число'
    try:
        if not math.isfinite(value):
            return None, 'ожидалось конечное число'
    except OverflowError:
        pass   # целое больше предела float (10**400 из JSON) — конечное; отсечёт диапазон ниже
    if value < lo or value > hi or (lo_exclusive and value == lo):
        low = f'больше {_fmt(lo)}' if lo_exclusive else f'от {_fmt(lo)}'
        return None, f'допустимо {low} до {_fmt(hi)}'
    return value, None


def _check_int_set(value: Any, lo: int, hi: int, what: str) -> tuple[list[int] | None, str | None]:
    if not isinstance(value, list) or not value:
        return None, f'непустой список {what}'
    out = []
    for x in value:
        if isinstance(x, bool) or not isinstance(x, int) or not lo <= x <= hi:
            return None, f'{what}: числа от {lo} до {hi}'
        if x in out:
            return None, f'{what}: повтор {x}'
        out.append(x)
    return sorted(out), None


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def validate_settings(values: Mapping[str, Any],
                      known_groups: frozenset[str] | None) -> tuple[dict[str, Any], dict[str, str]]:
    """Проверка ПОЛНОГО набора настроек. Возвращает (нормализованные, ошибки {ключ: текст}).

    known_groups=None — коды групп не сверяются (чтение из БД без доступа к ERP).
    """
    out: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for key in ('work_start', 'work_end', 'truck_work_start', 'truck_work_end', 'dispatch_ready_time'):
        v = values.get(key)
        if not isinstance(v, str) or not _HHMM_RE.match(v):
            errors[key] = 'время в формате ЧЧ:ММ'
        else:
            out[key] = v
    for start, end in (('work_start', 'work_end'), ('truck_work_start', 'truck_work_end')):
        if start in out and end in out and _minutes(out[end]) <= _minutes(out[start]):
            errors[end] = 'конец рабочего дня должен быть позже начала'

    days, err = _check_int_set(values.get('workdays'), 1, 7, 'дней недели')
    if err:
        errors['workdays'] = err
    else:
        out['workdays'] = days

    for key, (lo, hi, nullable) in _NUMERIC.items():
        v, err = _check_number(values.get(key), lo, hi, nullable,
                               lo_exclusive=(key == 'size_small_max_kg'))
        if err:
            errors[key] = err
        else:
            out[key] = v
    if 'size_small_max_kg' in out and 'size_medium_max_kg' in out \
            and out['size_small_max_kg'] >= out['size_medium_max_kg']:
        errors['size_medium_max_kg'] = 'порог средних должен быть больше порога мелких'
    if 'abc_a_share' in out and 'abc_b_share' in out \
            and out['abc_a_share'] + out['abc_b_share'] >= 1:
        errors['abc_b_share'] = 'доли классов A и B вместе должны быть меньше 1'
    # «потерян» проверяется раньше «затих» — его порог не может быть мягче
    if 'dormant_min_days' in out and 'lost_min_days' in out \
            and out['lost_min_days'] < out['dormant_min_days']:
        errors['lost_min_days'] = 'порог «потерян» не может быть меньше порога «затих»'
    if 'dormant_mult' in out and 'lost_mult' in out and out['lost_mult'] < out['dormant_mult']:
        errors['lost_mult'] = 'множитель «потерян» не может быть меньше множителя «затих»'

    groups = values.get('chain_groups')
    if not isinstance(groups, list) or len(groups) > _MAX_LIST \
            or not all(isinstance(g, str) and g.strip() for g in groups):
        errors['chain_groups'] = 'ожидался список кодов групп клиентов'
    else:
        codes = sorted({g.strip() for g in groups})
        unknown = [g for g in codes if known_groups is not None and g not in known_groups]
        if unknown:
            errors['chain_groups'] = 'неизвестные группы клиентов: ' + ', '.join(unknown)
        else:
            out['chain_groups'] = codes

    for key in ('low_months', 'peak_months'):
        v = values.get(key)
        if v is None:
            out[key] = None
            continue
        months, err = _check_int_set(v, 1, 12, 'месяцев')
        if err:
            errors[key] = err + ' (или null — определять автоматически)'
        else:
            out[key] = months
    if out.get('low_months') and out.get('peak_months'):
        both = sorted(set(out['low_months']) & set(out['peak_months']))
        if both:
            errors['peak_months'] = 'месяцы не могут быть одновременно низкими и пиковыми: ' \
                                    + ', '.join(map(str, both))
    return out, errors


def _check_point(lat: Any, lon: Any) -> tuple[Point | None, str | None]:
    """Пара «широта, долгота»: обе null — нет точки; иначе обе числа и точка в Армении."""
    if lat is None and lon is None:
        return None, None
    for v in (lat, lon):
        if v is None:
            return None, 'укажите и широту, и долготу'
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None, 'ожидались числа'
    if not is_valid_point(lat, lon):
        return None, 'точка вне Армении'
    return (float(lat), float(lon)), None


def _check_bool(v: Any) -> str | None:
    return None if isinstance(v, bool) else 'ожидалось true/false'


def _validate_trucks(raw: Any, current: Mapping[str, Truck], ref: RefData,
                     errors: dict[str, str]) -> list[Truck]:
    if not isinstance(raw, list) or len(raw) > _MAX_LIST:
        errors['trucks'] = 'ожидался список машин'
        return []
    out: list[Truck] = []
    seen: set[str] = set()
    allowed = {'car_code', 'capacity_kg', 'fuel_l_per_100km', 'agent_id', 'active'}
    for i, item in enumerate(raw):
        path = f'trucks.{i}'
        if not isinstance(item, dict):
            errors[path] = 'ожидался объект'
            continue
        extra = sorted(set(item) - allowed)
        if extra:
            errors[path] = 'неизвестные поля: ' + ', '.join(extra)
            continue
        code = item.get('car_code')
        if not isinstance(code, str) or not code.strip():
            errors[f'{path}.car_code'] = 'не указан код машины'
            continue
        code = code.strip()
        if code not in ref.car_codes:
            errors[f'{path}.car_code'] = 'машины нет в ERP'
            continue
        if code in seen:
            errors[f'{path}.car_code'] = 'машина указана дважды'
            continue
        seen.add(code)
        base = current.get(code) or Truck(code, active=None)   # новая запись — «активна» авто
        fields: dict[str, Any] = {}
        ok = True
        for name, (lo, hi) in (('capacity_kg', TRUCK_CAPACITY_KG),
                               ('fuel_l_per_100km', TRUCK_FUEL_L100)):
            if name in item:
                v, err = _check_number(item[name], lo, hi, nullable=True)
                if err:
                    errors[f'{path}.{name}'] = err
                    ok = False
                fields[name] = v
        if 'agent_id' in item:
            a = item['agent_id']
            if a is not None and (isinstance(a, bool) or not isinstance(a, int)
                                  or a not in ref.agent_ids):
                errors[f'{path}.agent_id'] = 'менеджер без маршрутов в ERP'
                ok = False
            fields['agent_id'] = a
        if 'active' in item:
            # null — вернуть «авто»: решают накладные ERP (машина возила за CAR_IDLE_DAYS дней)
            if item['active'] is not None and _check_bool(item['active']):
                errors[f'{path}.active'] = 'ожидалось true/false или null («авто»)'
                ok = False
            fields['active'] = item['active']
        if ok:
            out.append(Truck(
                car_code=code,
                capacity_kg=fields.get('capacity_kg', base.capacity_kg),
                fuel_l_per_100km=fields.get('fuel_l_per_100km', base.fuel_l_per_100km),
                agent_id=fields.get('agent_id', base.agent_id),
                active=fields.get('active', base.active),
            ))
    return out


def code_key(code: str) -> str:
    """Номер машины для сравнения: без пробелов и дефисов, заглавными («504 CQ 61» = «504-cq61»)."""
    return re.sub(r'[\s\-]+', '', code).upper()


def _validate_manual_trucks(raw: Any, current: Mapping[str, Truck], ref: RefData,
                            errors: dict[str, str]) -> list[Truck]:
    """Ручные машины — ПОЛНЫЙ список (кого нет — удаляется). Номер не совпадает с машинами ERP (и с
    записями машин ERP в базе) и не повторяется; экспедитор — из возивших без машины за 90 дней (или уже
    закреплённый за этой машиной), у одного экспедитора — одна машина."""
    if not isinstance(raw, list) or len(raw) > MANUAL_TRUCKS_MAX:
        errors['manual_trucks'] = f'ожидался список машин (не больше {MANUAL_TRUCKS_MAX})'
        return []
    taken = {code_key(c) for c in ref.car_codes} | {code_key(c) for c, t in current.items() if not t.manual}
    allowed = {'car_code', 'name', 'capacity_kg', 'fuel_l_per_100km', 'active', 'van_agent_id'}
    out: list[Truck] = []
    seen: set[str] = set()
    vans: dict[int, str] = {}
    for i, item in enumerate(raw):
        path = f'manual_trucks.{i}'
        if not isinstance(item, dict):
            errors[path] = 'ожидался объект'
            continue
        extra = sorted(set(item) - allowed)
        if extra:
            errors[path] = 'неизвестные поля: ' + ', '.join(extra)
            continue
        code = item.get('car_code')
        code = ' '.join(code.split()) if isinstance(code, str) else ''
        if not MANUAL_CODE_RE.match(code):
            errors[f'{path}.car_code'] = 'номер машины: до 20 букв и цифр (можно пробел и дефис)'
            continue
        key = code_key(code)
        if key in taken:
            errors[f'{path}.car_code'] = 'машина с таким номером уже есть в ERP — она в списке выше'
            continue
        if key in seen:
            errors[f'{path}.car_code'] = 'машина с таким номером указана дважды'
            continue
        seen.add(key)
        base = current.get(code)
        base = base if base is not None and base.manual else Truck(code, manual=True)
        ok = True
        name = item.get('name', base.name)
        if name is not None and not isinstance(name, str):
            errors[f'{path}.name'] = 'ожидался текст'
            ok = False
            name = None
        name = ' '.join(name.split()) or None if name else None
        if name and len(name) > MANUAL_NAME_MAX:
            errors[f'{path}.name'] = f'название — не длиннее {MANUAL_NAME_MAX} символов'
            ok = False
        nums: dict[str, Any] = {}
        for key_name, (lo, hi) in (('capacity_kg', TRUCK_CAPACITY_KG), ('fuel_l_per_100km', TRUCK_FUEL_L100)):
            v, err = _check_number(item.get(key_name, getattr(base, key_name)), lo, hi, nullable=True)
            if err:
                errors[f'{path}.{key_name}'] = err
                ok = False
            nums[key_name] = v
        active = item.get('active', True if base.active is None else base.active)
        if _check_bool(active):
            errors[f'{path}.active'] = 'ожидалось true/false'
            ok = False
        van = item.get('van_agent_id', base.van_agent_id)
        if van is not None:
            if not _is_int(van) or (van not in ref.van_agent_ids and van != base.van_agent_id):
                errors[f'{path}.van_agent_id'] = 'этот агент не возил заказы без машины за 3 месяца'
                ok = False
            elif van in vans:
                errors[f'{path}.van_agent_id'] = f'этот экспедитор уже закреплён за машиной {vans[van]}'
                ok = False
            else:
                vans[van] = code
        if ok:
            out.append(Truck(code, nums['capacity_kg'], nums['fuel_l_per_100km'], None, active,
                             manual=True, name=name, van_agent_id=van))
    return out


def _validate_managers(raw: Any, current: Mapping[int, ManagerProfile], ref: RefData,
                       errors: dict[str, str]) -> list[ManagerProfile]:
    if not isinstance(raw, list) or len(raw) > _MAX_LIST:
        errors['managers'] = 'ожидался список менеджеров'
        return []
    out: list[ManagerProfile] = []
    seen: set[int] = set()
    allowed = {'agent_id', 'included', 'home_lat', 'home_lon', 'car_fuel_l_per_100km',
               'car_fuel_type'}
    for i, item in enumerate(raw):
        path = f'managers.{i}'
        if not isinstance(item, dict):
            errors[path] = 'ожидался объект'
            continue
        extra = sorted(set(item) - allowed)
        if extra:
            errors[path] = 'неизвестные поля: ' + ', '.join(extra)
            continue
        agent_id = item.get('agent_id')
        if isinstance(agent_id, bool) or not isinstance(agent_id, int) \
                or agent_id not in ref.agent_ids:
            errors[f'{path}.agent_id'] = 'менеджер без маршрутов в ERP'
            continue
        if agent_id in seen:
            errors[f'{path}.agent_id'] = 'менеджер указан дважды'
            continue
        seen.add(agent_id)
        base = current.get(agent_id) or ManagerProfile(agent_id)   # новая запись — included «авто»
        ok = True
        # нет ключа — прежнее значение; null — вернуть «авто» (решает работа за 8 недель)
        included = item.get('included', base.included)
        if included is not None and _check_bool(included):
            errors[f'{path}.included'] = 'ожидалось true/false или null («авто»)'
            ok = False
        home: Point | None = base.home
        if 'home_lat' in item or 'home_lon' in item:
            home, err = _check_point(item.get('home_lat'), item.get('home_lon'))
            if err:
                errors[f'{path}.home'] = err
                ok = False
        l100 = base.car_fuel_l_per_100km
        if 'car_fuel_l_per_100km' in item:
            l100, err = _check_number(item['car_fuel_l_per_100km'], *MANAGER_FUEL_L100, nullable=True)
            if err:
                errors[f'{path}.car_fuel_l_per_100km'] = err
                ok = False
        fuel = item.get('car_fuel_type', base.car_fuel_type)
        if fuel is not None and fuel not in FUEL_TYPES:
            errors[f'{path}.car_fuel_type'] = 'вид топлива: diesel, petrol или lpg'
            ok = False
        if ok:
            out.append(ManagerProfile(
                agent_id=agent_id, included=included,
                home_lat=home[0] if home else None, home_lon=home[1] if home else None,
                car_fuel_l_per_100km=l100, car_fuel_type=fuel,
            ))
    return out


def validate_payload(payload: Any, current: Bundle,
                     ref: RefData) -> tuple[Changes | None, dict[str, str]]:
    """Проверка тела POST /api/routes/settings (§10.3).

    Каждый раздел необязателен. settings сливаются с текущими и проверяются целиком
    (перекрёстные правила — по итоговым значениям); машины и менеджеры — upsert по ключу,
    отсутствующие в записи поля сохраняют прежние значения; depot: null — убрать склад;
    manual_trucks — полный список ручных машин (кого в нём нет — удаляется).
    Ошибки — {"путь.поля": "сообщение"}; при любой ошибке изменения не возвращаются.
    """
    if not isinstance(payload, dict):
        return None, {'_': 'ожидался JSON-объект'}
    errors: dict[str, str] = {}
    unknown = sorted(set(payload) - {'settings', 'depot', 'trucks', 'managers', 'manual_trucks'})
    if unknown:
        errors['_'] = 'неизвестные разделы: ' + ', '.join(unknown)

    merged = dict(current.settings)
    raw_settings = payload.get('settings', {})
    if not isinstance(raw_settings, dict):
        errors['settings'] = 'ожидался объект настроек'
    else:
        for key in sorted(set(raw_settings) - set(DEFAULT_SETTINGS)):
            errors[f'settings.{key}'] = 'неизвестная настройка'
        merged.update({k: v for k, v in raw_settings.items() if k in DEFAULT_SETTINGS})
    settings, setting_errors = validate_settings(merged, ref.group_codes)
    errors.update({f'settings.{k}': v for k, v in setting_errors.items()})

    depot_set = 'depot' in payload
    depot: Point | None = current.depot
    if depot_set:
        raw_depot = payload['depot']
        if raw_depot is None:
            depot = None
        elif not isinstance(raw_depot, dict) or set(raw_depot) != {'lat', 'lon'}:
            errors['depot'] = 'ожидалось {"lat": …, "lon": …} или null'
        else:
            depot, err = _check_point(raw_depot['lat'], raw_depot['lon'])
            if err or depot is None:
                errors['depot'] = err or 'укажите широту и долготу'

    trucks = _validate_trucks(payload['trucks'], current.trucks, ref, errors) \
        if 'trucks' in payload else []
    managers = _validate_managers(payload['managers'], current.managers, ref, errors) \
        if 'managers' in payload else []
    manual = tuple(_validate_manual_trucks(payload['manual_trucks'], current.trucks, ref, errors)) \
        if 'manual_trucks' in payload else None

    if errors:
        return None, errors
    return Changes(settings, depot_set, depot, tuple(trucks), tuple(managers), manual), {}


# --- SQLite ---

def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _loaded_truck(row: tuple) -> tuple[Truck, list[str]]:
    """Строка trucks из БД → Truck и список нарушений (те же правила, что при сохранении)."""
    code, capacity, fuel, agent_id, active, manual, name, van, updated_at, updated_by = row
    problems = []
    if not isinstance(code, str) or not code.strip():
        problems.append('код машины')
    if _check_number(capacity, *TRUCK_CAPACITY_KG, nullable=True)[1]:
        problems.append('тоннаж')
    if _check_number(fuel, *TRUCK_FUEL_L100, nullable=True)[1]:
        problems.append('расход')
    if agent_id is not None and not _is_int(agent_id):
        problems.append('менеджер')
    if manual not in (0, 1):
        problems.append('признак «вручную»')
    if active not in (0, 1) and not (active is None and manual == 0):   # «авто» — только у машин ERP
        problems.append('признак «активна»')
    if name is not None and (manual != 1 or not isinstance(name, str) or len(name) > MANUAL_NAME_MAX):
        problems.append('название')
    if van is not None and (manual != 1 or not _is_int(van)):
        problems.append('экспедитор')
    return Truck(code, capacity, fuel, agent_id, None if active is None else bool(active), updated_at,
                 updated_by, manual == 1, name, van), problems


def _loaded_manager(row: tuple) -> tuple[ManagerProfile, list[str]]:
    """Строка manager_profile из БД → ManagerProfile и список нарушений."""
    agent_id, included, home_lat, home_lon, l100, fuel, updated_at, updated_by = row
    problems = []
    if not _is_int(agent_id):
        problems.append('id менеджера')
    if included is not None and included not in (0, 1):
        problems.append('признак «в расчёте»')
    if _check_point(home_lat, home_lon)[1]:
        problems.append('дом')
    if _check_number(l100, *MANAGER_FUEL_L100, nullable=True)[1]:
        problems.append('расход')
    if fuel is not None and fuel not in FUEL_TYPES:
        problems.append('вид топлива')
    return ManagerProfile(agent_id, None if included is None else bool(included), home_lat, home_lon,
                          l100, fuel, updated_at, updated_by), problems


def _loaded_decision(row: tuple) -> tuple[Decision, list[str]]:
    """Строка decision из БД → Decision и список нарушений."""
    customer_id, agent_id, kind, value, status, updated_at, updated_by, from_value = row
    problems = []
    if not _is_int(customer_id) or not _is_int(agent_id):
        problems.append('id клиента или менеджера')
    if kind not in DECISION_KINDS:
        problems.append('вид решения')
    else:
        if kind == 'remove':
            value_ok = value == REMOVE_VALUE
        elif kind == 'transfer':
            hit = parse_transfer_key(value)
            value_ok = hit is not None and hit[0] != agent_id
        else:
            value_ok = (parse_pattern_key(value) if kind == 'pattern' else parse_freq_key(value)) is not None
        if not value_ok:
            problems.append('значение')
        if from_value is not None and (parse_plan_freq_key(from_value) if kind == 'freq'
                                       else parse_pattern_key(from_value)) is None:
            problems.append('исходное значение')
    if status not in ('accepted', 'rejected'):
        problems.append('статус')
    return Decision(customer_id, agent_id, kind, value, status, updated_at, updated_by,
                    from_value), problems


_FIX_HINT = ' — исправьте или удалите файл; значения по умолчанию молча не подставляются'


class Store:
    """Доступ к route_optimizer.db. Соединение открывается на каждую операцию."""

    def __init__(self, path: str):
        self.path = path

    def _name(self) -> str:
        return f'База настроек маршрутов {os.path.basename(self.path)}'

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            conn.execute('PRAGMA busy_timeout = 5000')
            conn.execute('PRAGMA journal_mode = WAL')
            self._ensure_schema(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None or not str(row[0]).isdigit():
            raise StoreError('В базе маршрутов нет версии схемы')
        return int(row[0])

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'meta' in tables:
            version = Store._schema_version(conn)
            if version > SCHEMA_VERSION:
                raise StoreError(f'База маршрутов создана более новой версией программы '
                                 f'(схема {version}, поддерживается {SCHEMA_VERSION})')
            if version < SCHEMA_VERSION:
                Store._migrate(conn)
            return
        if tables:
            raise StoreError('Файл не является базой маршрутов (есть чужие таблицы)')
        conn.execute('BEGIN IMMEDIATE')
        try:
            for ddl in _SCHEMA:
                conn.execute(ddl)
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                         (str(SCHEMA_VERSION),))
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Схема старой версии → SCHEMA_VERSION одной транзакцией (DDL в SQLite транзакционен):
        сбой посреди миграции оставляет базу в прежней версии."""
        conn.execute('BEGIN IMMEDIATE')
        try:
            version = Store._schema_version(conn)   # под блокировкой: другое соединение могло успеть
            while version < SCHEMA_VERSION:
                steps = _MIGRATIONS.get(version)
                if steps is None:
                    raise StoreError(f'Нет миграции схемы базы маршрутов с версии {version}')
                for ddl in steps:
                    conn.execute(ddl)
                version += 1
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(version),))
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise

    def load(self) -> Bundle:
        """Текущие настройки. Отсутствующие ключи — дефолты; битые значения — StoreError.

        Все таблицы читаются одной транзакцией чтения: параллельное сохранение не даст
        «смешанный» набор (настройки до сохранения, склад — после).
        """
        try:
            conn = self._connect()
            try:
                conn.execute('BEGIN')
                try:
                    setting_rows = conn.execute('SELECT key, value FROM settings').fetchall()
                    depot_row = conn.execute('SELECT lat, lon FROM depot WHERE id = 1').fetchone()
                    truck_rows = conn.execute(
                        'SELECT car_code, capacity_kg, fuel_l_per_100km, agent_id, active, manual, name, '
                        'van_agent_id, updated_at, updated_by FROM trucks').fetchall()
                    manager_rows = conn.execute(
                        'SELECT agent_id, included, home_lat, home_lon, car_fuel_l_per_100km, '
                        'car_fuel_type, updated_at, updated_by FROM manager_profile').fetchall()
                    geo_rows = conn.execute(
                        'SELECT customer_id, lat, lon FROM customer_geo_override').fetchall()
                    conn.execute('COMMIT')
                except BaseException:
                    if conn.in_transaction:
                        conn.execute('ROLLBACK')
                    raise
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise StoreError(f'{self._name()} повреждена или недоступна{_FIX_HINT}') from e

        raw = dict(DEFAULT_SETTINGS)
        for key, value in setting_rows:
            if key not in DEFAULT_SETTINGS:
                continue  # ключ из будущей версии — игнорируем
            try:
                raw[key] = json.loads(value)
            except (TypeError, ValueError) as e:
                raise StoreError(f'{self._name()}: повреждена настройка «{key}»{_FIX_HINT}') from e
        settings, errors = validate_settings(raw, known_groups=None)
        if errors:
            raise StoreError(f'{self._name()}: повреждены настройки ('
                             + '; '.join(f'{k}: {v}' for k, v in sorted(errors.items()))
                             + f'){_FIX_HINT}')

        depot = None
        if depot_row is not None:
            if not is_valid_point(depot_row[0], depot_row[1]):
                raise StoreError(f'{self._name()}: повреждены координаты склада{_FIX_HINT}')
            depot = (float(depot_row[0]), float(depot_row[1]))
        trucks: dict[str, Truck] = {}
        for row in truck_rows:
            truck, problems = _loaded_truck(row)
            if problems:
                raise StoreError(f'{self._name()}: повреждена запись машины {row[0]!r} '
                                 f'({", ".join(problems)}){_FIX_HINT}')
            trucks[truck.car_code] = truck
        managers: dict[int, ManagerProfile] = {}
        for row in manager_rows:
            profile, problems = _loaded_manager(row)
            if problems:
                raise StoreError(f'{self._name()}: повреждена запись менеджера {row[0]!r} '
                                 f'({", ".join(problems)}){_FIX_HINT}')
            managers[profile.agent_id] = profile
        geo: dict[int, Point] = {}
        for customer_id, lat, lon in geo_rows:
            if not _is_int(customer_id) or not is_valid_point(lat, lon):
                raise StoreError(f'{self._name()}: повреждена ручная точка клиента {customer_id!r}{_FIX_HINT}')
            geo[customer_id] = (float(lat), float(lon))
        return Bundle(settings, depot, trucks, managers, geo)

    def save(self, changes: Changes, user: str | None) -> None:
        """Записать проверенные изменения одной транзакцией (всё или ничего)."""
        now = _now()
        try:
            conn = self._connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                try:
                    self._write(conn, changes, now, user)
                    conn.execute('COMMIT')
                except BaseException:
                    conn.execute('ROLLBACK')
                    raise
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise StoreError(f'{self._name()}: не удалось сохранить настройки') from e

    @staticmethod
    def _write(conn: sqlite3.Connection, changes: Changes, now: str, user: str | None) -> None:
        for key, value in changes.settings.items():
            conn.execute('INSERT INTO settings(key, value) VALUES(?, ?) '
                         'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                         (key, json.dumps(value, ensure_ascii=False)))
        if changes.depot_set:
            if changes.depot is None:
                conn.execute('DELETE FROM depot WHERE id = 1')
            else:
                conn.execute('INSERT INTO depot(id, lat, lon, updated_at, updated_by) '
                             'VALUES(1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET '
                             'lat = excluded.lat, lon = excluded.lon, '
                             'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                             (changes.depot[0], changes.depot[1], now, user))
        for t in changes.trucks:
            conn.execute('INSERT INTO trucks(car_code, capacity_kg, fuel_l_per_100km, agent_id, '
                         'active, updated_at, updated_by) VALUES(?, ?, ?, ?, ?, ?, ?) '
                         'ON CONFLICT(car_code) DO UPDATE SET capacity_kg = excluded.capacity_kg, '
                         'fuel_l_per_100km = excluded.fuel_l_per_100km, agent_id = excluded.agent_id, '
                         'active = excluded.active, updated_at = excluded.updated_at, '
                         'updated_by = excluded.updated_by',
                         (t.car_code, t.capacity_kg, t.fuel_l_per_100km, t.agent_id,
                          None if t.active is None else int(t.active), now, user))
        if changes.manual_trucks is not None:
            keep = [t.car_code for t in changes.manual_trucks]
            conn.execute(f'DELETE FROM trucks WHERE manual = 1 AND car_code NOT IN ({",".join("?" * len(keep))})'
                         if keep else 'DELETE FROM trucks WHERE manual = 1', keep)
            # экспедитор переходит от машины к машине в одном сохранении: сначала снимаем, потом ставим
            # (уникальный индекс trucks_one_van проверяется на каждой строке)
            conn.execute('UPDATE trucks SET van_agent_id = NULL WHERE manual = 1')
            for t in changes.manual_trucks:
                conn.execute('INSERT INTO trucks(car_code, capacity_kg, fuel_l_per_100km, agent_id, active, manual, '
                             'name, van_agent_id, updated_at, updated_by) VALUES(?, ?, ?, NULL, ?, 1, ?, ?, ?, ?) '
                             'ON CONFLICT(car_code) DO UPDATE SET capacity_kg = excluded.capacity_kg, '
                             'fuel_l_per_100km = excluded.fuel_l_per_100km, active = excluded.active, '
                             'name = excluded.name, van_agent_id = excluded.van_agent_id, '
                             'updated_at = excluded.updated_at, updated_by = excluded.updated_by '
                             'WHERE trucks.manual = 1',
                             (t.car_code, t.capacity_kg, t.fuel_l_per_100km, int(bool(t.active)), t.name,
                              t.van_agent_id, now, user))
        for m in changes.managers:
            conn.execute('INSERT INTO manager_profile(agent_id, included, home_lat, home_lon, '
                         'car_fuel_l_per_100km, car_fuel_type, updated_at, updated_by) '
                         'VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(agent_id) DO UPDATE SET '
                         'included = excluded.included, home_lat = excluded.home_lat, '
                         'home_lon = excluded.home_lon, '
                         'car_fuel_l_per_100km = excluded.car_fuel_l_per_100km, '
                         'car_fuel_type = excluded.car_fuel_type, '
                         'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                         (m.agent_id, None if m.included is None else int(m.included),
                          m.home_lat, m.home_lon, m.car_fuel_l_per_100km, m.car_fuel_type, now, user))

    # --- Этап 3: решения владельца и сохранённые расчёты ---

    def _read(self, query: Callable[[sqlite3.Connection], Any]) -> Any:
        try:
            conn = self._connect()
            try:
                return query(conn)
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise StoreError(f'{self._name()} повреждена или недоступна{_FIX_HINT}') from e

    def _transaction(self, write: Callable[[sqlite3.Connection], Any], failure: str) -> Any:
        """Запись одной транзакцией: всё или ничего. Возвращает то, что вернула write."""
        try:
            conn = self._connect()
            try:
                conn.execute('BEGIN IMMEDIATE')
                try:
                    out = write(conn)
                    conn.execute('COMMIT')
                except BaseException:
                    conn.execute('ROLLBACK')
                    raise
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise StoreError(f'{self._name()}: {failure}') from e
        return out

    def load_decisions(self) -> list[Decision]:
        """Действующие решения владельца (без retired); битая строка — StoreError."""
        rows = self._read(lambda conn: conn.execute(
            'SELECT customer_id, agent_id, kind, value, status, updated_at, updated_by, from_value '
            "FROM decision WHERE status <> 'retired' ORDER BY agent_id, customer_id, kind, value"
        ).fetchall())
        out = []
        for row in rows:
            decision, problems = _loaded_decision(row)
            if problems:
                raise StoreError(f'{self._name()}: повреждено решение по клиенту {row[0]!r} '
                                 f'({", ".join(problems)}){_FIX_HINT}')
            out.append(decision)
        return out

    def save_decision(self, customer_id: int, agent_id: int, kind: str, value: str, action: str,
                      user: str | None, from_value: str | None = None) -> None:
        """Одно решение — см. save_decisions."""
        self.save_decisions([DecisionInput(customer_id, agent_id, kind, value, action, from_value)],
                            user)

    def save_decisions(self, items: Collection[DecisionInput], user: str | None) -> None:
        """Решения одной транзакцией — все или ни одного, по порядку:
        accept — закрепить (прежнее принятое решение того же вида у клиента удаляется в той же
        транзакции: на клиента у менеджера одно принятое на вид); reject — запретить; reset — снять.
        value и from_value — уже проверенный канонический текст (patterns.pattern_key / freq_key)."""
        for d in items:
            if d.kind not in DECISION_KINDS or d.action not in DECISION_ACTIONS:
                raise ValueError(f'неизвестное решение: {d.kind}/{d.action}')
        now = _now()

        def write(conn: sqlite3.Connection) -> None:
            for d in items:
                key = (d.customer_id, d.agent_id, d.kind)
                if d.action == 'reset':
                    conn.execute('DELETE FROM decision WHERE customer_id = ? AND agent_id = ? '
                                 'AND kind = ? AND value = ?', (*key, d.value))
                    continue
                status = 'accepted' if d.action == 'accept' else 'rejected'
                if status == 'accepted':
                    conn.execute("DELETE FROM decision WHERE customer_id = ? AND agent_id = ? "
                                 "AND kind = ? AND status = 'accepted' AND value <> ?", (*key, d.value))
                conn.execute('INSERT INTO decision(customer_id, agent_id, kind, value, from_value, status, '
                             'updated_at, updated_by) VALUES(?, ?, ?, ?, ?, ?, ?, ?) '
                             'ON CONFLICT(customer_id, agent_id, kind, value) DO UPDATE SET '
                             'from_value = excluded.from_value, status = excluded.status, '
                             'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                             (*key, d.value, d.from_value, status, now, user))

        self._transaction(write, 'не удалось сохранить решения')

    def reset_decisions(self) -> int:
        """«Сбросить все»: удалить все решения (и отработавшие). Возвращает, сколько было действующих."""
        def write(conn: sqlite3.Connection) -> int:
            active = conn.execute("SELECT COUNT(*) FROM decision WHERE status <> 'retired'").fetchone()[0]
            conn.execute('DELETE FROM decision')
            return active

        return self._transaction(write, 'не удалось сбросить решения')

    def retire_decisions(self, decisions: Collection[Decision]) -> int:
        """Отметить решения отработавшими (retired): больше не закрепляют и не запрещают.
        Запись меняется, только если она та же, что была прочитана (статус, from_value, время):
        решение, которое владелец за это время принял заново, не трогается. Возвращает, сколько отмечено."""
        now = _now()

        def write(conn: sqlite3.Connection) -> int:
            n = 0
            for d in decisions:
                n += conn.execute(
                    "UPDATE decision SET status = 'retired', updated_at = ? WHERE customer_id = ? "
                    'AND agent_id = ? AND kind = ? AND value = ? AND status = ? AND from_value IS ? '
                    'AND updated_at IS ?', (now, d.customer_id, d.agent_id, d.kind, d.value, d.status,
                                            d.from_value, d.updated_at)).rowcount
            return n

        return self._transaction(write, 'не удалось обновить решения')

    def save_scenario(self, scenario_id: str, created_by: str | None, params: Mapping[str, Any],
                      result: Mapping[str, Any]) -> None:
        """Сохранить расчёт; хранятся последние SCENARIOS_KEPT."""
        now = _now()
        params_json = json.dumps(params, ensure_ascii=False)
        result_json = json.dumps(result, ensure_ascii=False)

        def write(conn: sqlite3.Connection) -> None:
            conn.execute('INSERT INTO scenario(id, created_at, created_by, params, result) '
                         'VALUES(?, ?, ?, ?, ?)', (scenario_id, now, created_by, params_json, result_json))
            conn.execute('DELETE FROM scenario WHERE rowid NOT IN '
                         '(SELECT rowid FROM scenario ORDER BY rowid DESC LIMIT ?)', (SCENARIOS_KEPT,))

        self._transaction(write, 'не удалось сохранить расчёт')

    def _scenario(self, row: tuple | None) -> Scenario | None:
        if row is None:
            return None
        scenario_id, created_at, created_by, params, result = row
        try:
            return Scenario(scenario_id, created_at, created_by, json.loads(params), json.loads(result))
        except (TypeError, ValueError, RecursionError) as e:
            raise StoreError(f'{self._name()}: повреждён сохранённый расчёт {scenario_id!r}'
                             f'{_FIX_HINT}') from e

    def last_scenario(self) -> Scenario | None:
        return self._scenario(self._read(lambda conn: conn.execute(
            'SELECT id, created_at, created_by, params, result FROM scenario '
            'ORDER BY rowid DESC LIMIT 1').fetchone()))

    def get_scenario(self, scenario_id: str) -> Scenario | None:
        return self._scenario(self._read(lambda conn: conn.execute(
            'SELECT id, created_at, created_by, params, result FROM scenario WHERE id = ?',
            (scenario_id,)).fetchone()))

    def scenario_ids(self) -> list[str]:
        """id сохранённых расчётов, новые первыми."""
        return [r[0] for r in self._read(lambda conn: conn.execute(
            'SELECT id FROM scenario ORDER BY rowid DESC').fetchall())]

    # --- План развоза: ручные точки клиентов и черновики плана на дату ---

    def save_geo_override(self, customer_id: int, point: Point | None, user: str | None) -> None:
        """Ручная точка клиента (проверенная: в Армении); None — убрать, снова ERP/GPS."""
        if not _is_int(customer_id) or not 0 < customer_id < 2 ** 31:
            raise ValueError('customer_id: положительное целое')
        if point is not None and not is_valid_point(*point):
            raise ValueError('точка вне Армении')

        def write(conn: sqlite3.Connection) -> None:
            if point is None:
                conn.execute('DELETE FROM customer_geo_override WHERE customer_id = ?', (customer_id,))
            else:
                conn.execute('INSERT INTO customer_geo_override(customer_id, lat, lon, updated_at, updated_by) '
                             'VALUES(?, ?, ?, ?, ?) ON CONFLICT(customer_id) DO UPDATE SET lat = excluded.lat, '
                             'lon = excluded.lon, updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                             (customer_id, float(point[0]), float(point[1]), _now(), user))

        self._transaction(write, 'не удалось сохранить точку клиента')

    def load_dispatch(self, day: str) -> tuple[dict[str, Any], int] | None:
        """Черновик плана развоза на дату (YYYY-MM-DD): (данные, номер правки) или None."""
        row = self._read(lambda conn: conn.execute(
            'SELECT data, rev FROM dispatch_plan WHERE day = ?', (day,)).fetchone())
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError, RecursionError) as e:
            raise StoreError(f'{self._name()}: повреждён план развоза на {day}{_FIX_HINT}') from e
        if not isinstance(data, dict) or not _is_int(row[1]):
            raise StoreError(f'{self._name()}: повреждён план развоза на {day}{_FIX_HINT}')
        return data, row[1]

    def save_dispatch(self, day: str, data: Mapping[str, Any], user: str | None,
                      expected_rev: int | None = None) -> int | None:
        """Сохранить черновик на дату; возвращает новый номер правки. expected_rev — номер, от которого
        делалась правка: черновик с тех пор изменён (другая вкладка) — None, ничего не записано.
        Черновики старше DISPATCH_KEPT_DAYS от этой даты удаляются."""
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
        now = _now()

        def write(conn: sqlite3.Connection) -> int | None:
            row = conn.execute('SELECT rev FROM dispatch_plan WHERE day = ?', (day,)).fetchone()
            current = row[0] if row is not None else 0
            if expected_rev is not None and expected_rev != current:
                return None
            rev = current + 1
            conn.execute('INSERT INTO dispatch_plan(day, data, rev, updated_at, updated_by) VALUES(?, ?, ?, ?, ?) '
                         'ON CONFLICT(day) DO UPDATE SET data = excluded.data, rev = excluded.rev, '
                         'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                         (day, raw, rev, now, user))
            conn.execute('DELETE FROM dispatch_plan WHERE day < date(?, ?)', (day, f'-{DISPATCH_KEPT_DAYS} days'))
            return rev

        return self._transaction(write, 'не удалось сохранить план развоза')

    def delete_dispatch(self, day: str) -> None:
        """«Начать заново»: черновик на дату удаляется."""
        self._transaction(lambda conn: conn.execute('DELETE FROM dispatch_plan WHERE day = ?', (day,)),
                          'не удалось удалить план развоза')

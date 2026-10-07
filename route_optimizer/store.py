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
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Collection, Literal, Mapping, Sequence

from . import crew_pay
from .geo import ARMENIA_LAT, ARMENIA_LON, Point, is_valid_point
from .garage import KM_PER_DAY_MAX, SPREAD_MONTHS
from .patterns import parse_freq_key, parse_pattern_key, parse_plan_freq_key, parse_transfer_key
from .running_costs import LOAD_COST_FIELDS, profile_fields
from .vehicle_access import VehicleAccess, check_access

SCHEMA_VERSION = 24
CREW_PAY_KEY = 'crew_pay'   # строка settings с параметрами «Աշխատավարձ» (Store.crew_pay_params); не ключ DEFAULT_SETTINGS

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
# Машины. Схема 1–7 и 8 — как были созданы (история миграций не меняется).
_TRUCKS_TABLE_V1 = (
    "CREATE TABLE IF NOT EXISTS trucks(car_code TEXT PRIMARY KEY, capacity_kg REAL, "
    "fuel_l_per_100km REAL, agent_id INTEGER, active INTEGER NOT NULL DEFAULT 1, "
    "updated_at TEXT NOT NULL, updated_by TEXT)")
# Схема 8: active NULL — «авто» (машина ERP возила за CAR_IDLE_DAYS дней и не закрыта); 1/0 — выбор владельца.
# manual = 1 — машина, которой нет в ERP (владелец добавил сам: экспедиторы возят без машины в накладных):
# name — марка/название, van_agent_id — экспедитор ERP (SALES.fVANAGENTID), чьи накладные без машины
# считаются её рейсами; один экспедитор — не больше одной машины.
_TRUCKS_COLUMNS_V8 = (
    "car_code TEXT PRIMARY KEY, capacity_kg REAL, fuel_l_per_100km REAL, agent_id INTEGER, active INTEGER, "
    "manual INTEGER NOT NULL DEFAULT 0, name TEXT, van_agent_id INTEGER, updated_at TEXT NOT NULL, updated_by TEXT")
# Схема 9 (окна приёма и малый центр, ответы владельца №39–41): center_ok NULL — «авто» (в названии машины есть
# «JAC»: паспорт 1,5 т, реально до 2,5 т — въезжает в центр); 1/0 — выбор владельца.
# Схема 22 (ответ владельца №68): big — «большая машина» (в Ереване — после малых и дольше): NULL — «авто» (тоннаж от
# BIG_TRUCK_AUTO_KG), 1/0 — выбор владельца.
_TRUCKS_COLUMNS = (
    "car_code TEXT PRIMARY KEY, capacity_kg REAL, fuel_l_per_100km REAL, agent_id INTEGER, active INTEGER, "
    "manual INTEGER NOT NULL DEFAULT 0, name TEXT, van_agent_id INTEGER, center_ok INTEGER, "
    "updated_at TEXT NOT NULL, updated_by TEXT, fuel_empty_l_per_100km REAL, fuel_full_l_per_100km REAL, "
    "wear_amd_per_km REAL, wear_load_amd_per_km REAL, big INTEGER")
_TRUCKS_TABLE = f"CREATE TABLE IF NOT EXISTS trucks({_TRUCKS_COLUMNS})"
# столбцы машин схемы 21 — что переносит пересборка 21 → 22
_TRUCKS_COPY_V21 = ("car_code, capacity_kg, fuel_l_per_100km, agent_id, active, manual, name, van_agent_id, center_ok, "
                    "updated_at, updated_by, fuel_empty_l_per_100km, fuel_full_l_per_100km, wear_amd_per_km, "
                    "wear_load_amd_per_km")
_TRUCKS_ONE_VAN = (
    "CREATE UNIQUE INDEX IF NOT EXISTS trucks_one_van ON trucks(van_agent_id) WHERE van_agent_id IS NOT NULL")
# Схема 9: окно приёма клиента — одно на все дни (№35–38). kind: before | after | between | at; t1, t2 — минуты от
# полуночи; tol — допуск «в T ± tol» (только at). Действует только в «Развозе».
_CUSTOMER_WINDOW_TABLE = (
    "CREATE TABLE IF NOT EXISTS customer_window(customer_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
    "t1 INTEGER NOT NULL, t2 INTEGER, tol INTEGER, updated_at TEXT NOT NULL, updated_by TEXT)")

# Схема 15 (ответ владельца №50): время у магазина — постоянная часть разгрузки этого магазина (парковка, приёмка,
# документы) вместо общей нормы unload_min_per_stop, мин; время на груз (unload_min_per_tonne) — как у всех. Хранится
# абсолютным значением, не поправкой: сменилась общая норма — значение магазина то же. Действует только в «Развозе».
_CUSTOMER_UNLOAD_TABLE = (
    "CREATE TABLE IF NOT EXISTS customer_unload(customer_id INTEGER PRIMARY KEY, "
    "fixed_min REAL NOT NULL CHECK (fixed_min BETWEEN 1 AND 120), updated_at TEXT NOT NULL, updated_by TEXT)")

# Схема 16 (ответ владельца №53): журнал гаража — строка на запись: repair (ремонт / запчасть / ТО — в «ремонт ֏/км»),
# accident (ДТП) и fixed (страховка / техосмотр / налог) — только итоги, odometer — только показание пробега (сумма 0).
# Спидометр — в каждой записи. Удаление мягкое (deleted_at): запись денег не теряется, в расчётах не участвует. Правка
# не теряет прежних значений: прежняя версия остаётся удалённой строкой с replaced_by — номером живой записи (её номер не
# меняется). Живое показание «только пробег» у машины на день — одно (повторное сохранение дня обновляет его).
# Как была создана миграцией 15 → 16 (история миграций не меняется); в схеме 17 — та же.
_GARAGE_FIELDS_V16 = (
    "id INTEGER PRIMARY KEY, car_code TEXT NOT NULL, day TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('repair', 'accident', 'fixed', 'odometer')), what TEXT, "
    "amount_amd INTEGER NOT NULL CHECK (amount_amd BETWEEN 0 AND 100000000), "
    "odometer_km INTEGER NOT NULL CHECK (odometer_km BETWEEN 0 AND 2000000), note TEXT, "
    "created_at TEXT NOT NULL, created_by TEXT, updated_at TEXT, updated_by TEXT, deleted_at TEXT, deleted_by TEXT, "
    "replaced_by INTEGER")
_GARAGE_CHECKS_V16 = (
    "CHECK ((kind = 'odometer') = (amount_amd = 0)), CHECK (kind = 'odometer' OR what IS NOT NULL), "
    "CHECK (replaced_by IS NULL OR deleted_at IS NOT NULL)")
_GARAGE_COLUMNS_V16 = f"{_GARAGE_FIELDS_V16}, {_GARAGE_CHECKS_V16}"
_GARAGE_TABLE_V16 = f"CREATE TABLE IF NOT EXISTS garage_entry({_GARAGE_COLUMNS_V16})"
_GARAGE_COPY_V16 = ('id, car_code, day, kind, what, amount_amd, odometer_km, note, created_at, created_by, updated_at, '
                    'updated_by, deleted_at, deleted_by, replaced_by')
# Схема 18 (доработка №53): крупный ремонт растягивается на 24 или 36 месяцев (spread_months; NULL — обычный, у других
# видов — всегда NULL).
_GARAGE_COLUMNS = (
    f"{_GARAGE_FIELDS_V16}, spread_months INTEGER, {_GARAGE_CHECKS_V16}, "
    "CHECK (spread_months IS NULL OR (spread_months IN (24, 36) AND kind = 'repair'))")
_GARAGE_TABLE = f"CREATE TABLE IF NOT EXISTS garage_entry({_GARAGE_COLUMNS})"
_GARAGE_ONE_ODOMETER = (
    "CREATE UNIQUE INDEX IF NOT EXISTS garage_one_odometer ON garage_entry(car_code, day) "
    "WHERE kind = 'odometer' AND deleted_at IS NULL")

# Схема 17 (ответ владельца №62): водитель машины — для «Բեռնագիր». Закреплён за машиной, но меняется часто. only_day = 0 —
# постоянный водитель с from_day и до следующей такой строки (смена не переписывает прошлые дни); only_day = 1 — подмена
# ровно на день from_day. Водитель дня: подмена этого дня, иначе постоянный с наибольшим from_day ≤ дня. Пустое имя —
# «водителя нет» (в накладной — строка, вписать от руки). Только для печати, в расчёты не входит.
_TRUCK_DRIVER_TABLE = (
    "CREATE TABLE IF NOT EXISTS truck_driver(car_code TEXT NOT NULL, from_day TEXT NOT NULL, "
    "only_day INTEGER NOT NULL DEFAULT 0 CHECK (only_day IN (0, 1)), name TEXT NOT NULL CHECK (length(name) <= 60), "
    "updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (car_code, from_day, only_day))")
# Схема 19 (ответ владельца к №62: «двое в машине» → «Վարորդ + առաքիչ»): второй человек в машине — առաքիչ, необязательный;
# та же структура и те же правила срока, что у водителя.
_TRUCK_HELPER_TABLE = _TRUCK_DRIVER_TABLE.replace('truck_driver(', 'truck_helper(', 1)
CREW_TABLES = {'driver': 'truck_driver', 'helper': 'truck_helper'}   # роль → таблица (только эти имена идут в SQL)
# Схема 23 (ответ владельца №77): водитель не вышел — дни с from_day по to_day включительно («только сегодня» — from_day =
# to_day). Имя — как в truck_driver (check_driver_name, непустое). Только для «Развоза»: сколько машин выходит в день.
_DRIVER_ABSENCE_TABLE = (
    "CREATE TABLE IF NOT EXISTS driver_absence(name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 60), "
    "from_day TEXT NOT NULL, to_day TEXT NOT NULL CHECK (to_day >= from_day), updated_at TEXT NOT NULL, updated_by TEXT, "
    "PRIMARY KEY (name, from_day))")

_CUSTOMER_VEHICLES_TABLE = (
    "CREATE TABLE IF NOT EXISTS customer_vehicle_access(customer_id INTEGER PRIMARY KEY, "
    "mode TEXT NOT NULL CHECK(mode IN ('allow', 'deny')), trucks TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, updated_by TEXT)")

# Схема 24 (ответ владельца №78, 16–17): правила магазина «Развоза» — solo: везут отдельным рейсом, без других магазинов
# («Ռամադա» — всегда отдельно); center: машины его допуска «только выбранные» въезжают в малый центр ради него. Строка — хоть
# одно правило; оба сняты — строки нет (dispatch.DayContext.solo, center_allow).
_CUSTOMER_RULE_TABLE = (
    "CREATE TABLE IF NOT EXISTS customer_rule(customer_id INTEGER PRIMARY KEY CHECK (customer_id > 0), "
    "solo INTEGER NOT NULL CHECK (solo IN (0, 1)), center INTEGER NOT NULL CHECK (center IN (0, 1)), "
    "updated_at TEXT NOT NULL, updated_by TEXT)")

_MEASUREMENT_TABLE = (
    'CREATE TABLE IF NOT EXISTS route_measurement(day TEXT NOT NULL, car_code TEXT NOT NULL, '
    'data TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY(day, car_code))')

# Схема 13 (learning-loop-plan.md, этап 4): журнал выученных норм — строка на (вид, машина, день прогона); повторный
# прогон того же дня заменяет её (идемпотентно). Действует последняя принятая (learning.in_effect). Переключатель
# автообучения по виду: нет строки — включено. Как была создана миграцией 12 → 13 (история миграций не меняется).
_LEARNED_TABLE_V13 = (
    "CREATE TABLE IF NOT EXISTS learned_norms(id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "kind TEXT NOT NULL CHECK (kind IN ('unload', 'loading', 'travel', 'fuel')), scope TEXT NOT NULL DEFAULT '', "
    "run_day TEXT NOT NULL, params TEXT, model_id TEXT, n_obs INTEGER NOT NULL, n_test INTEGER NOT NULL, "
    "train_from TEXT, train_to TEXT, test_from TEXT, test_to TEXT, mae_before REAL, mae_after REAL, "
    "accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)), reason TEXT NOT NULL, created_at TEXT NOT NULL, "
    "UNIQUE (kind, scope, run_day))")
# Схема 14: вид truck_time — выбор модели времени в пути грузовиков (learning.fit_truck_time). Столбцы те же; SQLite не
# меняет CHECK столбца — таблица пересобирается, строки переносятся как есть (с id). scope у travel — к каким минутам
# выучена поправка (learning.travel_scope): '' — прежняя модель, 'valhalla' — время Valhalla. Как была создана миграцией
# 13 → 14 (история миграций не меняется).
_LEARNED_COLUMNS_V14 = (
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "kind TEXT NOT NULL CHECK (kind IN ('unload', 'loading', 'travel', 'truck_time', 'fuel')), "
    "scope TEXT NOT NULL DEFAULT '', "
    "run_day TEXT NOT NULL, params TEXT, model_id TEXT, n_obs INTEGER NOT NULL, n_test INTEGER NOT NULL, "
    "train_from TEXT, train_to TEXT, test_from TEXT, test_to TEXT, mae_before REAL, mae_after REAL, "
    "accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)), reason TEXT NOT NULL, created_at TEXT NOT NULL, "
    "UNIQUE (kind, scope, run_day)")
# Схема 20 (№61): вид lunch — обед в пути (learning.fit_lunch) — и confidence: доля повторных выборок проверки, где новая
# норма точнее (learning._accept; NULL — не считали, и у строк до схемы 20). Таблица пересобирается так же, как 13 → 14.
# Как была создана миграцией 19 → 20 (история миграций не меняется).
_LEARNED_COLUMNS_V20 = (
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "kind TEXT NOT NULL CHECK (kind IN ('unload', 'loading', 'travel', 'truck_time', 'fuel', 'lunch')), "
    "scope TEXT NOT NULL DEFAULT '', "
    "run_day TEXT NOT NULL, params TEXT, model_id TEXT, n_obs INTEGER NOT NULL, n_test INTEGER NOT NULL, "
    "train_from TEXT, train_to TEXT, test_from TEXT, test_to TEXT, mae_before REAL, mae_after REAL, "
    "accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)), reason TEXT NOT NULL, created_at TEXT NOT NULL, "
    "confidence REAL, UNIQUE (kind, scope, run_day)")
# Схема 21 (№66): виды buffer (запас на рейс), truck_unload и truck_travel (темп машины) — CHECK вида шире, столбцы те
# же; таблица пересобирается так же, как 19 → 20 (строки, id, confidence и счётчик — как есть).
_LEARNED_COLUMNS = (
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "kind TEXT NOT NULL CHECK (kind IN ('unload', 'loading', 'travel', 'truck_time', 'fuel', 'lunch', 'buffer', "
    "'truck_unload', 'truck_travel')), "
    "scope TEXT NOT NULL DEFAULT '', "
    "run_day TEXT NOT NULL, params TEXT, model_id TEXT, n_obs INTEGER NOT NULL, n_test INTEGER NOT NULL, "
    "train_from TEXT, train_to TEXT, test_from TEXT, test_to TEXT, mae_before REAL, mae_after REAL, "
    "accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)), reason TEXT NOT NULL, created_at TEXT NOT NULL, "
    "confidence REAL, UNIQUE (kind, scope, run_day)")
_LEARNED_COPY = ('id, kind, scope, run_day, params, model_id, n_obs, n_test, train_from, train_to, test_from, test_to, '
                 'mae_before, mae_after, accepted, reason, created_at')
_LEARNED_COPY_V20 = f'{_LEARNED_COPY}, confidence'
_LEARNED_TABLE = f"CREATE TABLE IF NOT EXISTS learned_norms({_LEARNED_COLUMNS})"
_LEARNING_SWITCH_TABLE = (
    "CREATE TABLE IF NOT EXISTS learning_switch(kind TEXT PRIMARY KEY, auto INTEGER NOT NULL CHECK (auto IN (0, 1)), "
    "updated_at TEXT NOT NULL, updated_by TEXT)")

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
    _MEASUREMENT_TABLE,
    _CUSTOMER_VEHICLES_TABLE,
    _LEARNED_TABLE,
    _LEARNING_SWITCH_TABLE,
    _CUSTOMER_WINDOW_TABLE,   # перед таблицами схемы 7: базы прежних версий в тестах — срез _SCHEMA с конца
    _CUSTOMER_UNLOAD_TABLE,
    _GARAGE_TABLE,
    _GARAGE_ONE_ODOMETER,
    _TRUCK_DRIVER_TABLE,
    _TRUCK_HELPER_TABLE,
    _DRIVER_ABSENCE_TABLE,
    _CUSTOMER_RULE_TABLE,
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
        f"CREATE TABLE trucks_v8({_TRUCKS_COLUMNS_V8})",
        "INSERT INTO trucks_v8(car_code, capacity_kg, fuel_l_per_100km, agent_id, active, manual, name, "
        "van_agent_id, updated_at, updated_by) SELECT car_code, capacity_kg, fuel_l_per_100km, agent_id, "
        "active, 0, NULL, NULL, updated_at, updated_by FROM trucks",
        "DROP TABLE trucks",
        "ALTER TABLE trucks_v8 RENAME TO trucks",
        _TRUCKS_ONE_VAN,
    ),
    # 8 → 9 (окна приёма и малый центр): только добавляем — столбец «можно в центр» (у всех «авто») и таблица окон.
    # Граница центра (center_zone) миграции не требует: нет ключа — стартовая граница.
    8: ("ALTER TABLE trucks ADD COLUMN center_ok INTEGER", _CUSTOMER_WINDOW_TABLE),
    # 9 → 10: необязательные нормы нагрузки. NULL сохраняет прежнюю модель без выдуманных коэффициентов.
    9: tuple(f'ALTER TABLE trucks ADD COLUMN {key} REAL' for key in LOAD_COST_FIELDS),
    10: (_MEASUREMENT_TABLE,),
    11: (_CUSTOMER_VEHICLES_TABLE,),
    # 12 → 13: только добавляем — журнал выученных норм и переключатели автообучения.
    12: (_LEARNED_TABLE_V13, _LEARNING_SWITCH_TABLE),
    # 13 → 14: вид выученной нормы truck_time — журнал пересобирается с новым CHECK, строки (и id) переносятся как
    # есть, счётчик AUTOINCREMENT — прежний (повторный прогон дня расходует номера: id не повторяются);
    # переключатели автообучения (без CHECK вида) не меняются.
    13: (
        f"CREATE TABLE learned_norms_v14({_LEARNED_COLUMNS_V14})",
        f"INSERT INTO learned_norms_v14({_LEARNED_COPY}) SELECT {_LEARNED_COPY} FROM learned_norms",
        "DELETE FROM sqlite_sequence WHERE name = 'learned_norms_v14'",
        "INSERT INTO sqlite_sequence(name, seq) SELECT 'learned_norms_v14', seq FROM sqlite_sequence "
        "WHERE name = 'learned_norms'",
        "DROP TABLE learned_norms",
        "ALTER TABLE learned_norms_v14 RENAME TO learned_norms",
    ),
    # 14 → 15 (№50): только добавляем — таблица времени у магазина; прежние таблицы и значения не меняются.
    14: (_CUSTOMER_UNLOAD_TABLE,),
    # 15 → 16 (№53): только добавляем — журнал гаража и его индекс; прежние таблицы и значения не меняются.
    15: (_GARAGE_TABLE_V16, _GARAGE_ONE_ODOMETER),
    # 16 → 17 (№62): только добавляем — водители машин; прежние таблицы и значения не меняются.
    16: (_TRUCK_DRIVER_TABLE,),
    # 17 → 18 (доработка №53): срок растяжения крупного ремонта. Журнал пересобирается с новым столбцом и его CHECK —
    # строки (и номера: на них ссылается replaced_by) переносятся как есть, у всех «не растянут»; частичный уникальный
    # индекс уходит вместе со старой таблицей и создаётся заново. Журнал в схемах 16 и 17 одинаков (_GARAGE_TABLE_V16).
    17: (
        f"CREATE TABLE garage_entry_v18({_GARAGE_COLUMNS})",
        f"INSERT INTO garage_entry_v18({_GARAGE_COPY_V16}) SELECT {_GARAGE_COPY_V16} FROM garage_entry",
        "DROP TABLE garage_entry",
        "ALTER TABLE garage_entry_v18 RENAME TO garage_entry",
        _GARAGE_ONE_ODOMETER,
    ),
    # 18 → 19 (к №62, «Վարորդ + առաքիչ»): только добавляем — таблица առաքիչ; прежние таблицы и значения не меняются.
    18: (_TRUCK_HELPER_TABLE,),
    # 19 → 20 (№61): вид выученной нормы lunch и столбец confidence — журнал пересобирается, как 13 → 14: строки (и id)
    # переносятся как есть (confidence — NULL), счётчик AUTOINCREMENT — прежний; переключатели не меняются.
    19: (
        f"CREATE TABLE learned_norms_v20({_LEARNED_COLUMNS_V20})",
        f"INSERT INTO learned_norms_v20({_LEARNED_COPY}) SELECT {_LEARNED_COPY} FROM learned_norms",
        "DELETE FROM sqlite_sequence WHERE name = 'learned_norms_v20'",
        "INSERT INTO sqlite_sequence(name, seq) SELECT 'learned_norms_v20', seq FROM sqlite_sequence "
        "WHERE name = 'learned_norms'",
        "DROP TABLE learned_norms",
        "ALTER TABLE learned_norms_v20 RENAME TO learned_norms",
    ),
    # 20 → 21 (№66): виды buffer, truck_unload, truck_travel — журнал пересобирается, как 19 → 20: строки (и id,
    # confidence) переносятся как есть, счётчик AUTOINCREMENT — прежний; переключатели не меняются.
    20: (
        f"CREATE TABLE learned_norms_v21({_LEARNED_COLUMNS})",
        f"INSERT INTO learned_norms_v21({_LEARNED_COPY_V20}) SELECT {_LEARNED_COPY_V20} FROM learned_norms",
        "DELETE FROM sqlite_sequence WHERE name = 'learned_norms_v21'",
        "INSERT INTO sqlite_sequence(name, seq) SELECT 'learned_norms_v21', seq FROM sqlite_sequence "
        "WHERE name = 'learned_norms'",
        "DROP TABLE learned_norms",
        "ALTER TABLE learned_norms_v21 RENAME TO learned_norms",
    ),
    # 21 → 22 (№68): столбец «большая машина» (у всех «авто») — таблица машин пересобирается, как 7 → 8: строки
    # переносятся как есть по именам столбцов (у старых баз center_ok и нормы нагрузки — в конце), уникальный индекс
    # экспедитора создаётся заново. Зона Еревана и надбавка (yerevan_zone, big_truck_yerevan_min) миграции не требуют:
    # нет ключа — значение по умолчанию.
    21: (
        f"CREATE TABLE trucks_v22({_TRUCKS_COLUMNS})",
        f"INSERT INTO trucks_v22({_TRUCKS_COPY_V21}) SELECT {_TRUCKS_COPY_V21} FROM trucks",
        "DROP TABLE trucks",
        "ALTER TABLE trucks_v22 RENAME TO trucks",
        _TRUCKS_ONE_VAN,
    ),
    # 22 → 23 (№77): только добавляем — отсутствие водителей; прежние таблицы и значения не меняются.
    22: (_DRIVER_ABSENCE_TABLE,),
    # 23 → 24 (№78): только добавляем — магазины «отдельным рейсом»; прежние таблицы и значения не меняются.
    23: (_CUSTOMER_RULE_TABLE,),
}

FUEL_TYPES = ('diesel', 'petrol', 'lpg')
DEFAULT_MANAGER_FUEL = 'petrol'

DEFAULT_SETTINGS: dict[str, Any] = {
    'work_start': '09:00',
    'work_end': '18:00',
    'workdays': [1, 2, 3, 4, 5, 6],
    # Нерабочие даты (праздники и прочие выходные компании, №64): ISO-строки ГГГГ-ММ-ДД по возрастанию
    'holidays': [],
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
    # Форс-мажор (ответ владельца №32): «Везти после конца дня» в «Развозе» — машины возвращаются не позже
    'truck_overtime_end': '20:00',
    # Запас в конце дня (ответ владельца №78): сборка «Развоза» возвращает машины не позже truck_work_end минус столько
    # минут; рейс в запасе не опаздывает. 0 — без запаса (как до №78). Нет ключа — значение по умолчанию
    'truck_end_reserve_min': 30,
    # Машина отдельного рейса (ответы владельца №78, 18 и 20): лишнюю машину снимаем, чтобы она везла и обычные магазины,
    # только если дизель + износ дня растут не больше чем на столько %; иначе лишняя машина остаётся
    'solo_spare_max_pct': 5,
    # Погрузка по сезону (ответ владельца №78): с morning_loading_from по morning_loading_to (ММ-ДД, включительно, может
    # переходить через Новый год) машины грузят утром — первый рейс с загрузкой; вне сезона загружены с вечера и выезжают
    # в начале дня. 01-01 … 12-31 — всегда утром (как до №78)
    'morning_loading_from': '11-15',
    'morning_loading_to': '03-15',
    # Обед водителей (ответ владельца №61): в пути, гибко — «Развоз» сам вставляет паузу в рейс; truck_lunch_from …
    # truck_lunch_to — когда обед начинается; 0 минут — без обеда. Модель парка менеджеров его не знает
    'truck_lunch_min': 30,
    'truck_lunch_from': '12:30',
    'truck_lunch_to': '14:30',
    # Запас на рейс (ответ владельца №66): «Развоз» планирует рейс так, чтобы он укладывался в срок в q случаях из 100
    # (запас в конце рейса учит обучение, вид buffer); 50 — без запаса (по медиане). Нет ключа — значение по умолчанию
    'dispatch_buffer_pct': 80,
    'unload_min_per_stop': 8,
    'unload_min_per_tonne': 6,
    'warehouse_load_fixed_min': None,
    'warehouse_load_min_per_tonne': None,
    'traffic_mode': 'gps',
    # «Развоз»: до этого времени менеджеры ещё принимают заказы на следующий рабочий день (заканчивают
    # ≈ 16:40) — страница подсказывает собирать рейсы позже. Нет ключа в базе — значение по умолчанию
    'dispatch_ready_time': '17:00',
    # «Развоз» (№69): менеджеры (agent_id ERP), чьи заказы не везём, — правило для дней без плана: новый день
    # начинает с него, у дня с планом — свой выбор (Draft.agents_off). Список снятых, а не выбранных: новый менеджер
    # ERP по умолчанию в развозе — заказы молча не теряются. Нет ключа — пусто (везём всех)
    'dispatch_agents_off': [],
    # «Развоз» (№74): менеджеры (agent_id), чьи заказы машины парка везут, хотя в ERP «везёт сам» (экспедитор = менеджер,
    # Rocarm A000), — кроме клиентов в городах dispatch_other_cities (адрес по умолчанию или название клиента): их везут
    # другие машины. Пусто — правила нет: «везёт сам» не для машин, как раньше. Города — стартовые ответа владельца
    'dispatch_fleet_agents': [],
    'dispatch_other_cities': ['Գյումրի', 'Կապան', 'Գորիս', 'Վանաձոր'],
    # «Развоз» (№74): клиенты (customer_id ERP), чьи заказы машины не везут никогда (внутренние счета, экспорт) —
    # отмечает владелец. Пусто — везём всех
    'dispatch_customers_off': [],
    # «Развоз»: граница малого центра (№39–41) — вершины [широта, долгота]; туда въезжают только машины с правом
    # въезда. Стартовая — примерно кольцо бульваров Кентрона, владелец правит на карте. Нет ключа — она
    'center_zone': [[40.1915, 44.5070], [40.1925, 44.5170], [40.1890, 44.5245], [40.1800, 44.5265],
                    [40.1715, 44.5205], [40.1705, 44.5100], [40.1760, 44.5030], [40.1850, 44.5015]],
    # «Развоз»: большая машина в Ереване (ответ владельца №68, «приоритет + время»): точки в зоне Еревана сначала везут
    # малые машины, большая — когда малым не хватает тоннажа или времени; на её точке в зоне — ещё столько минут.
    # Стартовая граница — административная граница Еревана OSM (отношение 364087, главный контур без анклава аэропорта
    # «Звартноц», упрощён Дугласом–Пекером до 150 м), владелец правит на карте; пустая — правило выключено
    'big_truck_yerevan_min': 10,
    # сила приоритета малых машин: точка зоны на большой машине стоит как столько км её пути (ползунок: 0 — только
    # минуты, 1 — слабо, 3 — средне, 10 — сильно; замер 01.09–03.10: дизель +1,6 / +2,2 / +2,4 / +4,6%)
    'big_truck_yerevan_km': 3,
    # «Մեքենաները առցանց» (ответ владельца №76): пороги тревог — скорость выше столько км/ч дольше столько секунд,
    # стоянка не у магазина и не на складе дольше столько минут (обед — сверх своей длительности), нет связи дольше
    'live_speed_kmh': 90,
    'live_speed_sec': 30,
    'live_stop_min': 15,
    'live_no_contact_min': 5,
    # тревоги карты в Telegram-группу (этап 2): какие слать, тихие часы (с — до, по Еревану; одинаковые — без тихих
    # часов), не чаще раза в столько минут на тревогу того же вида у машины
    'live_alert_kinds': ['speed', 'stop', 'no_contact', 'gps', 'center'],
    'live_quiet_from': '20:00',
    'live_quiet_to': '08:00',
    'live_repeat_min': 30,
    'yerevan_zone': [[40.2173, 44.3948], [40.2129, 44.4031], [40.2021, 44.4052], [40.1964, 44.4102], [40.1936, 44.4029],
                    [40.1912, 44.4067], [40.19, 44.4042], [40.1853, 44.4078], [40.1748, 44.4063], [40.17, 44.4111],
                    [40.1671, 44.4196], [40.1694, 44.4271], [40.1675, 44.4303], [40.1599, 44.429], [40.1578, 44.4373],
                    [40.1597, 44.4425], [40.1552, 44.4449], [40.1448, 44.4352], [40.1367, 44.4337], [40.137, 44.4308],
                    [40.1309, 44.4283], [40.131, 44.4251], [40.1281, 44.4268], [40.1282, 44.4354], [40.1231, 44.4304],
                    [40.1215, 44.433], [40.1201, 44.4296], [40.1146, 44.4304], [40.1185, 44.4355], [40.1094, 44.4447],
                    [40.1092, 44.4538], [40.1042, 44.4509], [40.1091, 44.4551], [40.1086, 44.4591], [40.1056, 44.4562],
                    [40.1047, 44.4578], [40.1059, 44.4599], [40.1044, 44.4631], [40.1071, 44.466], [40.1049, 44.4918],
                    [40.0963, 44.489], [40.0963, 44.4932], [40.0909, 44.4895], [40.0859, 44.4918], [40.0843, 44.4937],
                    [40.0884, 44.507], [40.0859, 44.5083], [40.0797, 44.5037], [40.0789, 44.5063], [40.0717, 44.5029],
                    [40.0728, 44.5077], [40.0698, 44.5099], [40.0693, 44.5155], [40.0738, 44.5242], [40.0659, 44.5303],
                    [40.0762, 44.5459], [40.0724, 44.5491], [40.0756, 44.5534], [40.082, 44.5542], [40.0931, 44.5652],
                    [40.0949, 44.5706], [40.1021, 44.5756], [40.1086, 44.5948], [40.1374, 44.6153], [40.1431, 44.6218],
                    [40.1479, 44.603], [40.1579, 44.5978], [40.1531, 44.5867], [40.1532, 44.5826], [40.1557, 44.5815],
                    [40.1524, 44.5646], [40.1563, 44.5679], [40.165, 44.569], [40.1627, 44.5702], [40.1648, 44.5748],
                    [40.1667, 44.5734], [40.1765, 44.5867], [40.1832, 44.5819], [40.1845, 44.5773], [40.1897, 44.5892],
                    [40.1867, 44.5908], [40.1861, 44.5946], [40.1839, 44.5937], [40.1847, 44.597], [40.1939, 44.6014],
                    [40.1944, 44.6044], [40.1945, 44.596], [40.1902, 44.5888], [40.2058, 44.5789], [40.2089, 44.5874],
                    [40.2126, 44.5837], [40.2171, 44.5881], [40.2213, 44.5873], [40.2205, 44.5763], [40.2268, 44.5742],
                    [40.2272, 44.5676], [40.233, 44.5587], [40.2376, 44.5651], [40.2387, 44.5613], [40.2418, 44.5632],
                    [40.2382, 44.5579], [40.2361, 44.5466], [40.2405, 44.5442], [40.2403, 44.5405], [40.2322, 44.5278],
                    [40.2328, 44.5256], [40.2285, 44.5217], [40.2301, 44.5165], [40.2269, 44.5125], [40.2308, 44.5001],
                    [40.2282, 44.4986], [40.2257, 44.5016], [40.2293, 44.491], [40.2327, 44.49], [40.2332, 44.4871],
                    [40.2202, 44.473], [40.223, 44.4682], [40.2237, 44.4584], [40.2275, 44.4607], [40.23, 44.4578],
                    [40.23, 44.4546], [40.2244, 44.4525], [40.234, 44.4396], [40.2294, 44.4366], [40.2271, 44.4224],
                    [40.2288, 44.4153], [40.2267, 44.4128], [40.2214, 44.4139], [40.2248, 44.4088], [40.2252, 44.3976]],
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
    'truck_lunch_min': (0, 120, False),
    'truck_end_reserve_min': (0, 120, False),
    'solo_spare_max_pct': (0, 100, False),
    'dispatch_buffer_pct': (50, 95, False),
    'big_truck_yerevan_min': (0, 120, False),
    'big_truck_yerevan_km': (0, 10, False),     # и только ступени YEREVAN_KM_STEPS
    'warehouse_load_fixed_min': (0, 240, True),
    'warehouse_load_min_per_tonne': (0, 120, True),
    'live_speed_kmh': (30, 200, False),
    'live_speed_sec': (5, 600, False),
    'live_stop_min': (1, 240, False),
    'live_no_contact_min': (1, 120, False),
    'live_repeat_min': (1, 1440, False),
}

TRUCK_CAPACITY_KG = (100, 30000)
TRUCK_FUEL_L100 = (1, 80)
MANUAL_TRUCKS_MAX = 50
MANUAL_CODE_RE = re.compile(r'^[\w\- ]{1,20}$')   # номер машины: буквы, цифры, пробел, дефис
MANUAL_NAME_MAX = 60
MANAGER_FUEL_L100 = (1, 40)
_MAX_LIST = 500
MAX_HOLIDAYS = 400      # нерабочих дат в настройках: с запасом на год вперёд и прошлый (№64)
MAX_AGENTS_OFF = 500    # менеджеров в правиле «чьи заказы не везём» (№69) — как dispatch.MAX_AGENTS
MAX_FLEET_AGENTS = 500  # менеджеров, чьи заказы «везёт сам» всё равно везут машины (№74)
MAX_OTHER_CITIES = 50   # городов-исключений этого правила (№74)
CITY_NAME_MAX = 40      # символов в названии города
MAX_CUSTOMERS_OFF = 2000   # клиентов «машины не везут» (№74)
CENTER_ZONE_VERTICES = (3, 200)      # и у зоны Еревана (№68; она ещё может быть пустой — правило выключено)
BIG_TRUCK_AUTO_KG = 5000             # «большая машина» по умолчанию (№68): тоннаж от 5 т
YEREVAN_KM_STEPS = (0, 1, 3, 10)     # сила приоритета малых машин в Ереване (№68) — ступени ползунка страницы
WINDOW_KINDS = ('before', 'after', 'between', 'at')
WINDOW_TOL_MAX = 120
DEFAULT_WINDOW_TOL = 15     # «в 11:00 ± 15 мин» — допуск по умолчанию (№37)
UNLOAD_MIN_RANGE = (1, 120)  # время у магазина (№50), целые минуты
LIVE_ALERT_KINDS = ('speed', 'stop', 'no_contact', 'gps', 'center')   # виды тревог карты (live.py) — переключатели настроек
GARAGE_KINDS = ('repair', 'accident', 'fixed', 'odometer')   # журнал гаража (№53), как garage.KINDS
GARAGE_TEXT_MAX = 300
GARAGE_AMOUNT_MAX = 100_000_000
GARAGE_ODOMETER_MAX = 2_000_000
GARAGE_PAST_YEARS = 3
GARAGE_KM_PER_DAY = KM_PER_DAY_MAX   # спидометр не прирастает быстрее (как одометр заправок APK, odometer_plausible)
_DAY_MINUTES = 24 * 60

_HHMM_RE = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
_MMDD_RE = re.compile(r'^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')   # день года ММ-ДД (сезон погрузки, №78)
_ISO_DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


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
    center_ok: bool | None = None   # можно в малый центр: None — «авто» (Bundle.truck_center_ok)
    fuel_empty_l_per_100km: float | None = None
    fuel_full_l_per_100km: float | None = None
    wear_amd_per_km: float | None = None
    wear_load_amd_per_km: float | None = None
    big: bool | None = None         # большая машина (№68): None — «авто» (Bundle.truck_big)


def center_auto(name: str | None) -> bool:
    """«Можно в центр» по умолчанию: в названии машины (ERP CARS.fNAME, у ручной — её название) есть «JAC»."""
    return 'JAC' in (name or '').upper()


def big_auto(capacity_kg: float | None) -> bool:
    """«Большая машина» по умолчанию (№68): тоннаж задан и не меньше BIG_TRUCK_AUTO_KG."""
    return capacity_kg is not None and capacity_kg >= BIG_TRUCK_AUTO_KG


@dataclass(frozen=True)
class CustomerWindow:
    """Окно приёма клиента (№35–38), одно на все дни; время — минуты от полуночи.
    before — прибыть не позже t1; after — не раньше t1 (раньше — машина ждёт); between — от t1 до t2;
    at — в t1 ± tol."""
    kind: str
    t1: int
    t2: int | None = None
    tol: int | None = None

    def span(self) -> tuple[float, float]:
        """Прибытие к магазину — (не раньше, не позже), минуты от полуночи; открытая сторона — ±бесконечность."""
        if self.kind == 'before':
            return -math.inf, float(self.t1)
        if self.kind == 'after':
            return float(self.t1), math.inf
        if self.kind == 'between':
            return float(self.t1), float(self.t2)
        return float(self.t1 - self.tol), float(self.t1 + self.tol)

    def to_json(self) -> dict[str, Any]:
        return {'kind': self.kind, 't1': self.t1, 't2': self.t2, 'tol': self.tol}


def check_window(raw: Any) -> tuple[CustomerWindow | None, str | None]:
    """Окно приёма из запроса или из базы: {"kind", "t1", "t2", "tol"} → (окно, None) или (None, ошибка).
    У at без допуска — DEFAULT_WINDOW_TOL; поля, которых у вида нет, — null или нет ключа."""
    if not isinstance(raw, dict) or not set(raw) <= {'kind', 't1', 't2', 'tol'}:
        return None, 'Սերվերը չընդունեց հարցումը՝ սպասվում էր {"kind", "t1", "t2", "tol"}'
    kind = raw.get('kind')
    if kind not in WINDOW_KINDS:
        return None, 'Ընդունման ժամի տեսակը սխալ է'
    t1, t2, tol = raw.get('t1'), raw.get('t2'), raw.get('tol')
    if kind == 'at' and tol is None:
        tol = DEFAULT_WINDOW_TOL
    for v in (t1, t2) if kind == 'between' else (t1,):
        if not _is_int(v) or not 0 <= v < _DAY_MINUTES:
            return None, 'Ժամը պետք է լինի 00:00-ից մինչև 23:59'
    if (kind != 'between' and t2 is not None) or (kind != 'at' and tol is not None):
        return None, 'Սերվերը չընդունեց հարցումը՝ ընդունման ժամի այս տեսակի համար ավելորդ դաշտ'
    if kind == 'between' and t2 <= t1:
        return None, 'Միջակայքի վերջը պետք է լինի սկզբից ուշ'
    if kind == 'at' and (not _is_int(tol) or not 0 <= tol <= WINDOW_TOL_MAX):
        return None, f'Թույլատրելի շեղումը՝ 0-ից մինչև {WINDOW_TOL_MAX} րոպե'
    return CustomerWindow(kind, t1, t2, tol), None


def check_unload_min(raw: Any) -> tuple[float | None, str | None]:
    """Время у магазина (№50) из запроса или из базы → (минуты, None) или (None, ошибка). Целое число минут от 1 до
    120: логист вводит минуты, дробные не нужны; 40.0 (REAL из базы) — то же, что 40. Логическое — не число. Предел
    проверяется до перевода в float: огромное целое (10**400) — ошибка ввода, а не OverflowError; NaN и ∞ — вне
    предела."""
    lo, hi = UNLOAD_MIN_RANGE
    if (isinstance(raw, bool) or not isinstance(raw, (int, float)) or not lo <= raw <= hi
            or not float(raw).is_integer()):
        return None, f'Ժամանակ խանութում՝ ամբողջ թիվ {lo}-ից մինչև {hi} րոպե'
    return float(raw), None


DRIVER_NAME_MAX = 60


def _crew_table(role: str) -> str:
    """Таблица роли (CREW_TABLES); другое значение — ValueError: в SQL идут только эти имена."""
    if role not in CREW_TABLES:
        raise ValueError(f'роль: {", ".join(CREW_TABLES)}')
    return CREW_TABLES[role]

# управляющие, форматные (направление текста, мягкий перенос, нулевой ширины), частные, несуществующие и разделители
# строк — в печатаемом имени не нужны и могут переставить текст накладной
_INVISIBLE_CATEGORIES = frozenset({'Cc', 'Cf', 'Co', 'Cs', 'Cn', 'Zl', 'Zp'})


def check_driver_name(raw: Any) -> tuple[str | None, str | None]:
    """Имя водителя машины (№62) → (имя, None) или (None, ошибка по-армянски — раздел только на армянском, №58). Пробелы
    по краям убираются, подряд — один; пустая строка — «водителя нет»; не длиннее DRIVER_NAME_MAX; управляющие и
    невидимые символы не принимаются (имя печатается в накладной)."""
    if not isinstance(raw, str):
        return None, 'Վարորդի անունը պետք է լինի տեքստ'
    if any(unicodedata.category(c) in _INVISIBLE_CATEGORIES for c in raw):
        return None, 'Վարորդի անվան մեջ կան անթույլատրելի նշաններ'
    name = ' '.join(raw.split())
    if len(name) > DRIVER_NAME_MAX:
        return None, f'Վարորդի անունը՝ ոչ ավելի, քան {DRIVER_NAME_MAX} նիշ'
    return name, None


class GarageError(ValueError):
    """Запись журнала гаража не принята: {поле: причина по-армянски} — для страницы начальника гаража."""

    def __init__(self, errors: Mapping[str, str]):
        super().__init__(next(iter(errors.values())))
        self.errors = dict(errors)


@dataclass(frozen=True)
class GarageInput:
    """Проверенная запись журнала гаража (check_garage_entry) — для сохранения."""
    car_code: str
    day: date
    kind: str
    what: str | None
    amount_amd: int
    odometer_km: int
    note: str | None = None
    spread_months: int | None = None   # ремонт растянут на столько месяцев (garage.SPREAD_MONTHS); None — обычный


@dataclass(frozen=True)
class GarageEntry:
    """Запись журнала гаража из базы; deleted_at — удалена (мягко): в расчётах не участвует; replaced_by — это прежняя
    версия записи с этим номером (изменена)."""
    id: int
    car_code: str
    day: date
    kind: str
    what: str | None
    amount_amd: int
    odometer_km: int
    note: str | None
    created_at: str
    created_by: str | None
    updated_at: str | None
    updated_by: str | None
    deleted_at: str | None
    deleted_by: str | None
    replaced_by: int | None = None
    spread_months: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), 'day': self.day.isoformat()}


def _whole(v: Any, lo: int, hi: int) -> int | None:
    """Целое lo…hi (40.0 из JSON — то же, что 40); логическое и дробное — нет. Предел — до перевода в float."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi or not float(v).is_integer():
        return None
    return int(v)


def _garage_text(v: Any) -> tuple[str | None, bool]:
    """Текст поля журнала: (без крайних пробелов или None, годится ли — строка не длиннее GARAGE_TEXT_MAX)."""
    if v is None:
        return None, True
    if not isinstance(v, str):
        return None, False
    v = v.strip()
    return v or None, len(v) <= GARAGE_TEXT_MAX


def _iso_day(v: Any) -> date | None:
    """День 'YYYY-MM-DD' (и только так: fromisoformat принимает и '2026-W40-1') → date; иначе None."""
    if not isinstance(v, str) or not _ISO_DAY_RE.match(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _years_back(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:   # 29 февраля
        return d.replace(year=d.year - years, day=28)


def check_garage_entry(raw: Any, cars: Collection[str], today: date) -> tuple[GarageInput | None, dict[str, str]]:
    """Запись журнала гаража из запроса → (запись, {}) или (None, {поле: причина по-армянски}). Машина — из машин раздела
    (таблица trucks); дата — не в будущем и не старше GARAGE_PAST_YEARS лет; вид — GARAGE_KINDS; «что сделано» —
    обязательно, кроме «только пробег»; сумма — целые драмы 1…GARAGE_AMOUNT_MAX, у «только пробег» — 0 (или нет поля);
    спидометр — целые км 0…GARAGE_ODOMETER_MAX. Неубывание спидометра — Store.save_garage_entry (в транзакции записи)."""
    if not isinstance(raw, dict):
        return None, {'_': 'Սպասվում էր JSON օբյեկտ'}
    errors: dict[str, str] = {}
    extra = sorted(set(raw) - {'car_code', 'day', 'kind', 'what', 'amount_amd', 'odometer_km', 'note', 'spread_months'})
    if extra:
        errors['_'] = 'Անհայտ դաշտեր՝ ' + ', '.join(map(str, extra))
    code = raw.get('car_code')
    if not isinstance(code, str) or not code:
        errors['car_code'] = 'Ընտրեք մեքենան'
    elif code not in cars:
        errors['car_code'] = 'Այս մեքենան ցուցակում չկա'
    day = _iso_day(raw.get('day'))
    if day is None:
        errors['day'] = 'Նշեք ամսաթիվը'
    elif day > today:
        errors['day'] = 'Ամսաթիվը չի կարող լինել ապագայում'
    elif day < _years_back(today, GARAGE_PAST_YEARS):
        errors['day'] = f'Ամսաթիվը {GARAGE_PAST_YEARS} տարուց ավելի հին է'
    kind = raw.get('kind')
    if kind not in GARAGE_KINDS:
        errors['kind'] = 'Ընտրեք տեսակը'
    what, ok = _garage_text(raw.get('what'))
    if not ok:
        errors['what'] = f'Առավելագույնը {GARAGE_TEXT_MAX} նիշ'
    elif what is None and kind in GARAGE_KINDS and kind != 'odometer':
        errors['what'] = 'Գրեք, թե ինչ է արվել'
    note, ok = _garage_text(raw.get('note'))
    if not ok:
        errors['note'] = f'Առավելագույնը {GARAGE_TEXT_MAX} նիշ'
    raw_amount = raw.get('amount_amd')
    if kind == 'odometer':
        amount = 0 if raw_amount is None else _whole(raw_amount, 0, 0)
        if amount is None:
            errors['amount_amd'] = 'Վազքի գրառման մեջ գումար չի լինում'
    else:
        amount = _whole(raw_amount, 1, GARAGE_AMOUNT_MAX)
        if amount is None:
            errors['amount_amd'] = f'Գումարը՝ ամբողջ թիվ 1-ից {_km_text(GARAGE_AMOUNT_MAX)} ֏'
    km = _whole(raw.get('odometer_km'), 0, GARAGE_ODOMETER_MAX)
    if km is None:
        errors['odometer_km'] = f'Սպիդոմետրը՝ ամբողջ թիվ 0-ից {_km_text(GARAGE_ODOMETER_MAX)} կմ'
    spread = raw.get('spread_months')
    if spread is not None and kind != 'repair':
        errors['spread_months'] = 'Բաշխում լինում է միայն վերանորոգման համար'
    elif spread is not None and (isinstance(spread, bool) or spread not in SPREAD_MONTHS):
        errors['spread_months'] = 'Բաշխումը՝ 24 կամ 36 ամիս'
    if errors:
        return None, errors
    return GarageInput(code, day, kind, what, amount, km, note, None if spread is None else int(spread)), {}


def _loaded_garage(row: tuple) -> tuple[GarageEntry | None, list[str]]:
    """Строка garage_entry из базы → GarageEntry и нарушения (те же правила, что при записи, кроме дат «сегодня»)."""
    entry_id, code, day, kind, what, amount, km, note, *stamps, replaced_by, spread = row
    problems = []
    parsed = _iso_day(day)
    if not _is_int(entry_id) or not isinstance(code, str) or not code:
        problems.append('համար կամ մեքենա')
    if parsed is None:
        problems.append('ամսաթիվ')
    if kind not in GARAGE_KINDS:
        problems.append('տեսակ')
    elif _whole(amount, *((0, 0) if kind == 'odometer' else (1, GARAGE_AMOUNT_MAX))) is None or not _is_int(amount):
        problems.append('գումար')
    if not _is_int(km) or _whole(km, 0, GARAGE_ODOMETER_MAX) is None:
        problems.append('սպիդոմետր')
    if replaced_by is not None and not _is_int(replaced_by):
        problems.append('փոփոխության հղում')
    if spread is not None and (not _is_int(spread) or spread not in SPREAD_MONTHS or kind != 'repair'):
        problems.append('բաշխում')
    if problems:
        return None, problems
    return GarageEntry(entry_id, code, parsed, kind, what, amount, km, note, *stamps, replaced_by, spread), []


def _ru_day(iso: str) -> str:
    return f'{iso[8:10]}.{iso[5:7]}.{iso[:4]}'


def _km_text(km: int) -> str:
    return f'{km:,}'.replace(',', ' ')


class _Keep(Enum):
    KEEP = 'keep'


# сохранение без этого поля — значение в базе не меняется (None — убрать)
KEEP: Literal[_Keep.KEEP] = _Keep.KEEP


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
    # окна приёма клиентов (customer_window) — только «Развоз»; в отпечаток не входят: обзор и оптимизация их не знают
    windows: dict[int, CustomerWindow] = field(default_factory=dict)
    # точки водителей (courier.db, driver-geo-plan.md §2): не из этой базы — их добавляет views._bundle;
    # перекрывают ERP и GPS, уступают ручной точке
    driver_points: dict[int, Point] = field(default_factory=dict)
    vehicle_access: dict[int, VehicleAccess] = field(default_factory=dict)
    # правила магазинов «Развоза» (№78, customer_rule): отдельный рейс; въезд в центр машинам допуска allow ради магазина
    solo: frozenset[int] = frozenset()
    center_allow: frozenset[int] = frozenset()
    # время у магазина (customer_unload, №50): клиент → постоянная часть разгрузки, мин — только «Развоз»; в отпечаток
    # не входит, как и окна: модель парка менеджеров его не знает
    unload_min: dict[int, float] = field(default_factory=dict)
    # ремонт ֏/км по журналу гаража (№53): машина → готовая цена (garage.effective). Не из этой таблицы машин — её
    # добавляет views._bundle (на сегодня; «Развоз» — на свой день). В расчёте перекрывает ручное «Износ, драм/км»
    # (resolved_trucks); в trucks и в настройки не попадает. В отпечатке — только когда есть: без журнала он прежний
    garage_wear: dict[str, float] = field(default_factory=dict)
    # средняя модели или парка (garage.priors) машинам без своей готовой цены: в расчёте — только при пустом ручном
    # «Износ, драм/км» (resolved_trucks; 0 — задано). Добавляет views._bundle вместе с garage_wear; в отпечатке — так же
    garage_prior: dict[str, float] = field(default_factory=dict)

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

    def resolved_trucks(self, active_cars: Collection[str],
                        erp_capacity: Mapping[str, float] | None = None) -> dict[str, Truck]:
        """Машины расчёта парка и развоза — единственная точка, где запись машины становится нормами расчёта:
        действующее «активна» (bool), тоннаж — из настроек, а пустой (None) у машины ERP — из её карточки ERP
        (erp_capacity, Snapshot.car_capacity), и «Износ, драм/км» — по журналу гаража, если его цена готова
        (garage_wear), иначе ручной из настроек, а пустой ручной (None; 0 — задано) — средняя модели или парка
        (garage_prior)."""
        out = {}
        for code, t in self.trucks.items():
            if t.active is None:
                t = replace(t, active=code in active_cars)
            if t.capacity_kg is None:
                t = replace(t, capacity_kg=self.truck_capacity(code, erp_capacity))
            if code in self.garage_wear:
                t = replace(t, wear_amd_per_km=self.garage_wear[code])
            elif t.wear_amd_per_km is None and code in self.garage_prior:
                t = replace(t, wear_amd_per_km=self.garage_prior[code])
            out[code] = t
        return out

    def wear_source(self, code: str) -> str | None:
        """Откуда «Износ, драм/км» машины в расчёте: garage — своя цена журнала гаража, manual — настройки, garage_avg —
        средняя модели или парка по журналу (ручное пусто), None — не задан."""
        if code in self.garage_wear:
            return 'garage'
        t = self.trucks.get(code)
        if t is not None and t.wear_amd_per_km is not None:
            return 'manual'
        return 'garage_avg' if code in self.garage_prior else None

    def truck_center_ok(self, code: str, name: str | None) -> bool:
        """Можно ли машине в малый центр: выбор владельца; «авто» (записи нет или center_ok NULL) — center_auto
        по названию (name — ERP CARS.fNAME; у ручной машины — её название)."""
        t = self.trucks.get(code)
        if t is None or t.center_ok is None:
            return center_auto(name if name is not None or t is None else t.name)
        return t.center_ok

    def truck_big(self, code: str, erp_capacity: Mapping[str, float] | None = None) -> bool:
        """Большая ли машина (№68): выбор владельца; «авто» (записи нет или big NULL) — big_auto по тоннажу в расчёте
        (truck_capacity: из настроек, пустой у машины ERP — из её карточки ERP)."""
        t = self.trucks.get(code)
        if t is None:
            return False
        return big_auto(self.truck_capacity(code, erp_capacity)) if t.big is None else t.big

    def truck_capacity(self, code: str, erp_capacity: Mapping[str, float] | None = None) -> float | None:
        """Тоннаж машины в расчёте, кг: из настроек, а пустой у машины ERP — из её карточки ERP (erp_capacity,
        Snapshot.car_capacity); у ручной машины карточки ERP нет. Записи нет — None."""
        t = self.trucks.get(code)
        if t is None:
            return None
        if t.capacity_kg is None and not t.manual and erp_capacity:
            return erp_capacity.get(code)
        return t.capacity_kg

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
        if self.driver_points:   # без точек водителей отпечаток прежний
            data['driver'] = sorted(self.driver_points.items())
        if self.garage_wear:     # без готовых цен журнала гаража — тоже
            data['garage'] = sorted(self.garage_wear.items())
        if self.garage_prior:    # без средних журнала (готовых машин меньше двух) — тоже
            data['garage_prior'] = sorted(self.garage_prior.items())
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
    """Число для сообщения: разряды — пробел, дробь — запятая (1 000 000, 0,1), без «1e+06»."""
    return f'{x:,.10g}'.replace(',', ' ').replace('.', ',')


def _check_number(value: Any, lo: float, hi: float, nullable: bool = False,
                  lo_exclusive: bool = False, coord: bool = False) -> tuple[Any, str | None]:
    if value is None:
        return (None, None) if nullable else (None, 'պարտադիր թիվ')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, 'սպասվում էր թիվ'
    try:
        if not math.isfinite(value):
            return None, 'սպասվում էր վերջավոր թիվ'
    except OverflowError:
        pass   # целое больше предела float (10**400 из JSON) — конечное; отсечёт диапазон ниже
    if value < lo or value > hi or (lo_exclusive and value == lo):
        text = '{:g}'.format if coord else _fmt   # координаты — с точкой: «38.8-ից մինչև 41.4» (глоссарий §1.4)
        low = f'{text(lo)}-ից մեծ և' if lo_exclusive else f'{text(lo)}-ից'
        return None, f'թույլատրելի է՝ {low} մինչև {text(hi)}'
    return value, None


def _check_int_set(value: Any, lo: int, hi: int, what: str) -> tuple[list[int] | None, str | None]:
    if not isinstance(value, list) or not value:
        return None, f'սպասվում էր {what} ոչ դատարկ ցուցակ'
    out = []
    for x in value:
        if isinstance(x, bool) or not isinstance(x, int) or not lo <= x <= hi:
            return None, f'{what} համարները՝ {lo}-ից մինչև {hi}'
        if x in out:
            return None, f'{what} ցուցակում {x}-ը կրկնվում է'
        out.append(x)
    return sorted(out), None


def _check_dates(value: Any) -> tuple[list[str] | None, str | None]:
    """Список дат ГГГГ-ММ-ДД без повторов → по возрастанию (нерабочие дни, №64)."""
    if not isinstance(value, list) or len(value) > MAX_HOLIDAYS:
        return None, f'սպասվում էր ամսաթվերի ցուցակ (ոչ ավելի, քան {MAX_HOLIDAYS})'
    out: set[str] = set()
    for x in value:
        try:
            if not isinstance(x, str) or not _ISO_DAY_RE.match(x):
                raise ValueError
            date.fromisoformat(x)
        except ValueError:
            return None, f'սխալ ամսաթիվ՝ {str(x)[:20]} (սպասվում էր ՏՏՏՏ-ԱԱ-ՕՕ)'
        if x in out:
            return None, f'{x} ամսաթիվը կրկնվում է'
        out.add(x)
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

    mode = values.get('traffic_mode', 'gps')
    if mode not in ('gps', 'static', 'yandex'):
        errors['traffic_mode'] = 'ընտրեք պատմական GPS-ը կամ մշտական արագությունը'
    else:
        out['traffic_mode'] = mode

    for key in ('work_start', 'work_end', 'truck_work_start', 'truck_work_end', 'truck_overtime_end',
                'dispatch_ready_time', 'truck_lunch_from', 'truck_lunch_to', 'live_quiet_from', 'live_quiet_to'):
        v = values.get(key)
        if not isinstance(v, str) or not _HHMM_RE.match(v):
            errors[key] = 'ժամը՝ ԺԺ:ՐՐ ձևաչափով'
        else:
            out[key] = v
    # сезон утренней погрузки (№78): ММ-ДД существующего дня (29.02 — да, год високосный)
    for key in ('morning_loading_from', 'morning_loading_to'):
        v = values.get(key, DEFAULT_SETTINGS[key])
        try:
            if not isinstance(v, str) or not _MMDD_RE.match(v):
                raise ValueError
            date(2024, int(v[:2]), int(v[3:]))
        except ValueError:
            errors[key] = 'ամսաթիվը՝ ՕՕ.ԱԱ ձևաչափով, օրինակ՝ 15.11'
        else:
            out[key] = v
    for start, end in (('work_start', 'work_end'), ('truck_work_start', 'truck_work_end')):
        if start in out and end in out and _minutes(out[end]) <= _minutes(out[start]):
            errors[end] = 'աշխատանքային օրվա ավարտը պետք է լինի սկզբից ուշ'
    if ('truck_work_end' in out and 'truck_overtime_end' in out
            and _minutes(out['truck_overtime_end']) < _minutes(out['truck_work_end'])):
        errors['truck_overtime_end'] = 'ոչ շուտ, քան մեքենայի աշխատանքային օրվա ավարտը'
    # окно начала обеда (№61) — конец позже начала; обед включён — внутри рабочего дня машины
    lunch_on = bool(values.get('truck_lunch_min'))
    if lunch_on and 'truck_lunch_from' in out and 'truck_work_start' in out \
            and _minutes(out['truck_lunch_from']) < _minutes(out['truck_work_start']):
        errors['truck_lunch_from'] = 'ոչ շուտ, քան մեքենայի աշխատանքային օրվա սկիզբը'
    if lunch_on and 'truck_lunch_to' in out and 'truck_work_end' in out \
            and _minutes(out['truck_lunch_to']) > _minutes(out['truck_work_end']):
        errors['truck_lunch_to'] = 'ոչ ուշ, քան մեքենայի աշխատանքային օրվա ավարտը'
    elif ('truck_lunch_from' in out and 'truck_lunch_to' in out
            and _minutes(out['truck_lunch_to']) <= _minutes(out['truck_lunch_from'])):
        errors['truck_lunch_to'] = 'Միջակայքի վերջը պետք է լինի սկզբից ուշ'

    # какие тревоги карты слать в Telegram: только известные виды; пустой список — не слать ничего; порядок — канонический
    kinds = values.get('live_alert_kinds')
    if not isinstance(kinds, list) or not all(isinstance(k, str) for k in kinds):
        errors['live_alert_kinds'] = 'սպասվում էր ահազանգերի տեսակների ցուցակ'
    elif any(k not in LIVE_ALERT_KINDS for k in kinds):
        errors['live_alert_kinds'] = 'ահազանգի անհայտ տեսակ՝ ' + ', '.join(k for k in kinds if k not in LIVE_ALERT_KINDS)
    else:
        out['live_alert_kinds'] = [k for k in LIVE_ALERT_KINDS if k in kinds]

    days, err = _check_int_set(values.get('workdays'), 1, 7, 'շաբաթվա օրերի')
    if err:
        errors['workdays'] = err
    else:
        out['workdays'] = days

    holidays, err = _check_dates(values.get('holidays', []))
    if err:
        errors['holidays'] = err
    else:
        out['holidays'] = holidays

    # «Развоз» (№69): agent_id менеджеров, чьи заказы не везём (int ERP: 1 … 2³¹−1); повторы схлопываются, порядок — по
    # возрастанию
    off = values.get('dispatch_agents_off', [])
    if not isinstance(off, list) or len(off) > MAX_AGENTS_OFF:
        errors['dispatch_agents_off'] = f'սպասվում էր մենեջերների ցուցակ (ոչ ավելի, քան {MAX_AGENTS_OFF})'
    elif not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x < 2 ** 31 for x in off):
        errors['dispatch_agents_off'] = 'մենեջերների համարները՝ դրական ամբողջ թվեր'
    else:
        out['dispatch_agents_off'] = sorted(set(off))

    # «Развоз» (№74): менеджеры, чьи заказы «везёт сам» везут машины, и клиенты «машины не везут» — id ERP, как у №69
    for key, limit, what in (('dispatch_fleet_agents', MAX_FLEET_AGENTS, 'մենեջերների'),
                             ('dispatch_customers_off', MAX_CUSTOMERS_OFF, 'հաճախորդների')):
        ids = values.get(key, [])
        if not isinstance(ids, list) or len(ids) > limit:
            errors[key] = f'սպասվում էր {what} ցուցակ (ոչ ավելի, քան {limit})'
        elif not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x < 2 ** 31 for x in ids):
            errors[key] = f'{what} համարները՝ դրական ամբողջ թվեր'
        else:
            out[key] = sorted(set(ids))
    # города-исключения: названия как ввели (пробелы по краям — мимо), повтор без учёта регистра — один раз
    cities = values.get('dispatch_other_cities', DEFAULT_SETTINGS['dispatch_other_cities'])
    if not isinstance(cities, list) or len(cities) > MAX_OTHER_CITIES:
        errors['dispatch_other_cities'] = f'սպասվում էր քաղաքների ցուցակ (ոչ ավելի, քան {MAX_OTHER_CITIES})'
    elif not all(isinstance(c, str) and 0 < len(c.strip()) <= CITY_NAME_MAX and any(ch.isalpha() for ch in c)
                 and not any(ch in c for ch in ',;\n') for c in cities):
        errors['dispatch_other_cities'] = f'քաղաքի անունը՝ տառերով, ոչ ավելի, քան {CITY_NAME_MAX} նիշ'
    else:
        seen: set[str] = set()
        out['dispatch_other_cities'] = []
        for c in cities:
            name = ' '.join(c.split())
            if name.casefold() not in seen:
                seen.add(name.casefold())
                out['dispatch_other_cities'].append(name)

    for key, (lo, hi, nullable) in _NUMERIC.items():
        v, err = _check_number(values.get(key), lo, hi, nullable,
                               lo_exclusive=(key == 'size_small_max_kg'),
                               coord=key in ('city_center_lat', 'city_center_lon'))
        if err:
            errors[key] = err
        else:
            out[key] = v
    if 'size_small_max_kg' in out and 'size_medium_max_kg' in out \
            and out['size_small_max_kg'] >= out['size_medium_max_kg']:
        errors['size_medium_max_kg'] = 'միջին խանութների շեմը պետք է մեծ լինի փոքր խանութների շեմից'
    if 'abc_a_share' in out and 'abc_b_share' in out \
            and out['abc_a_share'] + out['abc_b_share'] >= 1:
        errors['abc_b_share'] = 'A և B դասերի բաժինները միասին պետք է 1-ից պակաս լինեն'
    # «потерян» проверяется раньше «затих» — его порог не может быть мягче
    if 'dormant_min_days' in out and 'lost_min_days' in out \
            and out['lost_min_days'] < out['dormant_min_days']:
        errors['lost_min_days'] = '«վաղուց չի գնում» շեմը չի կարող պակաս լինել «դադարել է գնել» շեմից'
    if 'dormant_mult' in out and 'lost_mult' in out and out['lost_mult'] < out['dormant_mult']:
        errors['lost_mult'] = '«վաղուց չի գնում» գործակիցը չի կարող պակաս լինել «դադարել է գնել» գործակցից'

    groups = values.get('chain_groups')
    if not isinstance(groups, list) or len(groups) > _MAX_LIST \
            or not all(isinstance(g, str) and g.strip() for g in groups):
        errors['chain_groups'] = 'սպասվում էր հաճախորդների խմբերի կոդերի ցուցակ'
    else:
        codes = sorted({g.strip() for g in groups})
        unknown = [g for g in codes if known_groups is not None and g not in known_groups]
        if unknown:
            errors['chain_groups'] = 'հաճախորդների անհայտ խմբեր՝ ' + ', '.join(unknown)
        else:
            out['chain_groups'] = codes

    for key in ('low_months', 'peak_months'):
        v = values.get(key)
        if v is None:
            out[key] = None
            continue
        months, err = _check_int_set(v, 1, 12, 'ամիսների')
        if err:
            errors[key] = err + ' (կամ null՝ որոշել ավտոմատ)'
        else:
            out[key] = months
    if out.get('low_months') and out.get('peak_months'):
        both = sorted(set(out['low_months']) & set(out['peak_months']))
        if both:
            errors['peak_months'] = 'ամիսները չեն կարող միաժամանակ լինել և՛ ցածր, և՛ բարձր սեզոնում՝ ' \
                                    + ', '.join(map(str, both))

    zone = values.get('center_zone')
    lo, hi = CENTER_ZONE_VERTICES
    if not isinstance(zone, list) or not lo <= len(zone) <= hi:
        errors['center_zone'] = f'կենտրոնի սահմանը՝ {lo}-ից մինչև {hi} կետ [լայնություն, երկայնություն]'
    elif not all(isinstance(p, list) and len(p) == 2 and _check_point(p[0], p[1])[0] is not None for p in zone):
        errors['center_zone'] = 'կենտրոնի սահմանի կետերը՝ [լայնություն, երկայնություն] Հայաստանում'
    else:
        out['center_zone'] = [[float(p[0]), float(p[1])] for p in zone]
    # сила приоритета (№68): только ступени ползунка — значение не на ступени страница не показала бы и сменила бы при
    # любом сохранении
    if 'big_truck_yerevan_km' in out and out['big_truck_yerevan_km'] not in YEREVAN_KM_STEPS:
        errors['big_truck_yerevan_km'] = 'ընտրեք ' + ', '.join(map(str, YEREVAN_KM_STEPS[:-1])) + f' կամ {YEREVAN_KM_STEPS[-1]}'
        del out['big_truck_yerevan_km']
    # зона Еревана (№68): как граница центра, но может быть пустой — правило «большая машина в Ереване» выключено
    zone = values.get('yerevan_zone')
    if not isinstance(zone, list) or not (not zone or lo <= len(zone) <= hi):
        errors['yerevan_zone'] = f'Երևանի գոտու սահմանը՝ {lo}-ից մինչև {hi} կետ [լայնություն, երկայնություն] կամ դատարկ'
    elif not all(isinstance(p, list) and len(p) == 2 and _check_point(p[0], p[1])[0] is not None for p in zone):
        errors['yerevan_zone'] = 'Երևանի գոտու սահմանի կետերը՝ [լայնություն, երկայնություն] Հայաստանում'
    else:
        out['yerevan_zone'] = [[float(p[0]), float(p[1])] for p in zone]
    return out, errors


def _check_point(lat: Any, lon: Any) -> tuple[Point | None, str | None]:
    """Пара «широта, долгота»: обе null — нет точки; иначе обе числа и точка в Армении."""
    if lat is None and lon is None:
        return None, None
    for v in (lat, lon):
        if v is None:
            return None, 'նշեք և՛ լայնությունը, և՛ երկայնությունը'
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None, 'սպասվում էին թվեր'
    if not is_valid_point(lat, lon):
        return None, 'կետը Հայաստանից դուրս է'
    return (float(lat), float(lon)), None


def _check_bool(v: Any) -> str | None:
    return None if isinstance(v, bool) else 'սպասվում էր true/false'


def _validate_load_costs(item: Mapping[str, Any], base: Truck, path: str,
                         errors: dict[str, str]) -> dict[str, float | None]:
    values = profile_fields(base)
    for key in LOAD_COST_FIELDS:
        if key not in item:
            continue
        bounds = TRUCK_FUEL_L100 if key.startswith('fuel_') else (0, 1e6)
        value, error = _check_number(item[key], *bounds, nullable=True)
        if error:
            errors[f'{path}.{key}'] = error
        values[key] = value
    empty, full = (values[key] for key in LOAD_COST_FIELDS[:2])
    if (empty is None) != (full is None):
        errors[f'{path}.{LOAD_COST_FIELDS[0]}'] = 'նշեք դատարկ և լրիվ բեռնված մեքենայի ծախսը միասին'
        errors[f'{path}.{LOAD_COST_FIELDS[1]}'] = 'նշեք դատարկ և լրիվ բեռնված մեքենայի ծախսը միասին'
    elif empty is not None and full is not None and full < empty:
        errors[f'{path}.{LOAD_COST_FIELDS[1]}'] = ('լրիվ բեռնված մեքենայի ծախսը չի կարող պակաս լինել '
                                                    'դատարկ մեքենայի ծախսից')
    return values


def _validate_trucks(raw: Any, current: Mapping[str, Truck], ref: RefData,
                     errors: dict[str, str]) -> list[Truck]:
    if not isinstance(raw, list) or len(raw) > _MAX_LIST:
        errors['trucks'] = 'սպասվում էր մեքենաների ցուցակ'
        return []
    out: list[Truck] = []
    seen: set[str] = set()
    allowed = {'car_code', 'capacity_kg', 'fuel_l_per_100km', 'agent_id', 'active', 'center_ok', 'big', *LOAD_COST_FIELDS}
    for i, item in enumerate(raw):
        path = f'trucks.{i}'
        if not isinstance(item, dict):
            errors[path] = 'սպասվում էր օբյեկտ'
            continue
        extra = sorted(set(item) - allowed)
        if extra:
            errors[path] = 'անհայտ դաշտեր՝ ' + ', '.join(extra)
            continue
        code = item.get('car_code')
        if not isinstance(code, str) or not code.strip():
            errors[f'{path}.car_code'] = 'մեքենայի կոդը նշված չէ'
            continue
        code = code.strip()
        if code not in ref.car_codes:
            errors[f'{path}.car_code'] = 'մեքենան ERP-ում չկա'
            continue
        if code in seen:
            errors[f'{path}.car_code'] = 'մեքենան նշված է երկու անգամ'
            continue
        seen.add(code)
        base = current.get(code) or Truck(code, active=None)   # новая запись — «активна» авто
        load_costs = _validate_load_costs(item, base, path, errors)
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
                errors[f'{path}.agent_id'] = 'մենեջերը ERP-ում երթուղիներ չունի'
                ok = False
            fields['agent_id'] = a
        if 'active' in item:
            # null — вернуть «авто»: решают накладные ERP (машина возила за CAR_IDLE_DAYS дней)
            if item['active'] is not None and _check_bool(item['active']):
                errors[f'{path}.active'] = 'սպասվում էր true/false կամ null («ավտոմատ»)'
                ok = False
            fields['active'] = item['active']
        if 'center_ok' in item:
            # null — вернуть «авто»: в центр — машины JAC (center_auto)
            if item['center_ok'] is not None and _check_bool(item['center_ok']):
                errors[f'{path}.center_ok'] = 'սպասվում էր true/false կամ null («ավտոմատ»)'
                ok = False
            fields['center_ok'] = item['center_ok']
        if 'big' in item:
            # null — вернуть «авто»: большая — от BIG_TRUCK_AUTO_KG (big_auto)
            if item['big'] is not None and _check_bool(item['big']):
                errors[f'{path}.big'] = 'սպասվում էր true/false կամ null («ավտոմատ»)'
                ok = False
            fields['big'] = item['big']
        if ok:
            out.append(Truck(
                car_code=code,
                capacity_kg=fields.get('capacity_kg', base.capacity_kg),
                fuel_l_per_100km=fields.get('fuel_l_per_100km', base.fuel_l_per_100km),
                agent_id=fields.get('agent_id', base.agent_id),
                active=fields.get('active', base.active),
                center_ok=fields.get('center_ok', base.center_ok),
                big=fields.get('big', base.big),
                **load_costs,
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
        errors['manual_trucks'] = f'սպասվում էր մեքենաների ցուցակ (առավելագույնը {MANUAL_TRUCKS_MAX})'
        return []
    taken = {code_key(c) for c in ref.car_codes} | {code_key(c) for c, t in current.items() if not t.manual}
    allowed = {'car_code', 'name', 'capacity_kg', 'fuel_l_per_100km', 'active', 'van_agent_id', 'center_ok', 'big',
               *LOAD_COST_FIELDS}
    out: list[Truck] = []
    seen: set[str] = set()
    vans: dict[int, str] = {}
    for i, item in enumerate(raw):
        path = f'manual_trucks.{i}'
        if not isinstance(item, dict):
            errors[path] = 'սպասվում էր օբյեկտ'
            continue
        extra = sorted(set(item) - allowed)
        if extra:
            errors[path] = 'անհայտ դաշտեր՝ ' + ', '.join(extra)
            continue
        code = item.get('car_code')
        code = ' '.join(code.split()) if isinstance(code, str) else ''
        if not MANUAL_CODE_RE.match(code):
            errors[f'{path}.car_code'] = 'համարանիշը՝ մինչև 20 տառ և թվանշան (կարելի է բացատ և գծիկ)'
            continue
        key = code_key(code)
        if key in taken:
            errors[f'{path}.car_code'] = 'այս համարանիշով մեքենան արդեն կա ERP-ում — այն վերևի ցուցակում է'
            continue
        if key in seen:
            errors[f'{path}.car_code'] = 'այս համարանիշով մեքենան նշված է երկու անգամ'
            continue
        seen.add(key)
        base = current.get(code)
        base = base if base is not None and base.manual else Truck(code, manual=True)
        load_costs = _validate_load_costs(item, base, path, errors)
        ok = True
        name = item.get('name', base.name)
        if name is not None and not isinstance(name, str):
            errors[f'{path}.name'] = 'սպասվում էր տեքստ'
            ok = False
            name = None
        name = ' '.join(name.split()) or None if name else None
        if name and len(name) > MANUAL_NAME_MAX:
            errors[f'{path}.name'] = f'անվանումը՝ առավելագույնը {MANUAL_NAME_MAX} նիշ'
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
            errors[f'{path}.active'] = 'սպասվում էր true/false'
            ok = False
        center_ok = item.get('center_ok', base.center_ok)
        if center_ok is not None and _check_bool(center_ok):
            errors[f'{path}.center_ok'] = 'սպասվում էր true/false կամ null («ավտոմատ»)'
            ok = False
        big = item.get('big', base.big)
        if big is not None and _check_bool(big):
            errors[f'{path}.big'] = 'սպասվում էր true/false կամ null («ավտոմատ»)'
            ok = False
        van = item.get('van_agent_id', base.van_agent_id)
        if van is not None:
            if not _is_int(van) or (van not in ref.van_agent_ids and van != base.van_agent_id):
                errors[f'{path}.van_agent_id'] = 'այս առաքիչը վերջին 3 ամսում առանց մեքենայի պատվերներ չի տարել'
                ok = False
            elif van in vans:
                errors[f'{path}.van_agent_id'] = f'այս առաքիչն արդեն ամրացված է {vans[van]} մեքենային'
                ok = False
            else:
                vans[van] = code
        if ok:
            out.append(Truck(code, nums['capacity_kg'], nums['fuel_l_per_100km'], None, active,
                             manual=True, name=name, van_agent_id=van, center_ok=center_ok, big=big, **load_costs))
    return out


def _validate_managers(raw: Any, current: Mapping[int, ManagerProfile], ref: RefData,
                       errors: dict[str, str]) -> list[ManagerProfile]:
    if not isinstance(raw, list) or len(raw) > _MAX_LIST:
        errors['managers'] = 'սպասվում էր մենեջերների ցուցակ'
        return []
    out: list[ManagerProfile] = []
    seen: set[int] = set()
    allowed = {'agent_id', 'included', 'home_lat', 'home_lon', 'car_fuel_l_per_100km',
               'car_fuel_type'}
    for i, item in enumerate(raw):
        path = f'managers.{i}'
        if not isinstance(item, dict):
            errors[path] = 'սպասվում էր օբյեկտ'
            continue
        extra = sorted(set(item) - allowed)
        if extra:
            errors[path] = 'անհայտ դաշտեր՝ ' + ', '.join(extra)
            continue
        agent_id = item.get('agent_id')
        if isinstance(agent_id, bool) or not isinstance(agent_id, int) \
                or agent_id not in ref.agent_ids:
            errors[f'{path}.agent_id'] = 'մենեջերը ERP-ում երթուղիներ չունի'
            continue
        if agent_id in seen:
            errors[f'{path}.agent_id'] = 'մենեջերը նշված է երկու անգամ'
            continue
        seen.add(agent_id)
        base = current.get(agent_id) or ManagerProfile(agent_id)   # новая запись — included «авто»
        ok = True
        # нет ключа — прежнее значение; null — вернуть «авто» (решает работа за 8 недель)
        included = item.get('included', base.included)
        if included is not None and _check_bool(included):
            errors[f'{path}.included'] = 'սպասվում էր true/false կամ null («ավտոմատ»)'
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
            errors[f'{path}.car_fuel_type'] = 'վառելիքի տեսակը՝ diesel, petrol կամ lpg'
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
        return None, {'_': 'սպասվում էր JSON օբյեկտ'}
    errors: dict[str, str] = {}
    unknown = sorted(set(payload) - {'settings', 'depot', 'trucks', 'managers', 'manual_trucks'})
    if unknown:
        errors['_'] = 'անհայտ բաժիններ՝ ' + ', '.join(unknown)

    merged = dict(current.settings)
    raw_settings = payload.get('settings', {})
    if not isinstance(raw_settings, dict):
        errors['settings'] = 'սպասվում էր կարգավորումների օբյեկտ'
    else:
        for key in sorted(set(raw_settings) - set(DEFAULT_SETTINGS)):
            errors[f'settings.{key}'] = 'անհայտ կարգավորում'
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
            errors['depot'] = 'սպասվում էր {"lat": …, "lon": …} կամ null'
        else:
            depot, err = _check_point(raw_depot['lat'], raw_depot['lon'])
            if err or depot is None:
                errors['depot'] = err or 'նշեք լայնությունը և երկայնությունը'

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
    code, capacity, fuel, agent_id, active, manual, name, van, center_ok, updated_at, updated_by, *costs, big = row
    problems = []
    if not isinstance(code, str) or not code.strip():
        problems.append('մեքենայի կոդ')
    if _check_number(capacity, *TRUCK_CAPACITY_KG, nullable=True)[1]:
        problems.append('տոննաժ')
    if _check_number(fuel, *TRUCK_FUEL_L100, nullable=True)[1]:
        problems.append('ծախս')
    if agent_id is not None and not _is_int(agent_id):
        problems.append('մենեջեր')
    if manual not in (0, 1):
        problems.append('«ձեռքով» հատկանիշ')
    if active not in (0, 1) and not (active is None and manual == 0):   # «авто» — только у машин ERP
        problems.append('«աշխատում է» հատկանիշ')
    if name is not None and (manual != 1 or not isinstance(name, str) or len(name) > MANUAL_NAME_MAX):
        problems.append('անվանում')
    if van is not None and (manual != 1 or not _is_int(van)):
        problems.append('առաքիչ')
    if center_ok not in (None, 0, 1):
        problems.append('«կարող է մտնել կենտրոն» հատկանիշ')
    if big not in (None, 0, 1):
        problems.append('«մեծ մեքենա» հատկանիշ')
    extra = dict(zip(LOAD_COST_FIELDS, costs))
    cost_errors: dict[str, str] = {}
    _validate_load_costs(extra, Truck(code), 'costs', cost_errors)
    problems.extend(cost_errors)
    return Truck(code, capacity, fuel, agent_id, None if active is None else bool(active), updated_at,
                 updated_by, manual == 1, name, van, None if center_ok is None else bool(center_ok), **extra,
                 big=None if big is None else bool(big)), problems


def _loaded_manager(row: tuple) -> tuple[ManagerProfile, list[str]]:
    """Строка manager_profile из БД → ManagerProfile и список нарушений."""
    agent_id, included, home_lat, home_lon, l100, fuel, updated_at, updated_by = row
    problems = []
    if not _is_int(agent_id):
        problems.append('մենեջերի id')
    if included is not None and included not in (0, 1):
        problems.append('«հաշվարկում» հատկանիշ')
    if _check_point(home_lat, home_lon)[1]:
        problems.append('տուն')
    if _check_number(l100, *MANAGER_FUEL_L100, nullable=True)[1]:
        problems.append('ծախս')
    if fuel is not None and fuel not in FUEL_TYPES:
        problems.append('վառելիքի տեսակ')
    return ManagerProfile(agent_id, None if included is None else bool(included), home_lat, home_lon,
                          l100, fuel, updated_at, updated_by), problems


def _loaded_decision(row: tuple) -> tuple[Decision, list[str]]:
    """Строка decision из БД → Decision и список нарушений."""
    customer_id, agent_id, kind, value, status, updated_at, updated_by, from_value = row
    problems = []
    if not _is_int(customer_id) or not _is_int(agent_id):
        problems.append('հաճախորդի կամ մենեջերի id')
    if kind not in DECISION_KINDS:
        problems.append('որոշման տեսակ')
    else:
        if kind == 'remove':
            value_ok = value == REMOVE_VALUE
        elif kind == 'transfer':
            hit = parse_transfer_key(value)
            value_ok = hit is not None and hit[0] != agent_id
        else:
            value_ok = (parse_pattern_key(value) if kind == 'pattern' else parse_freq_key(value)) is not None
        if not value_ok:
            problems.append('արժեք')
        if from_value is not None and (parse_plan_freq_key(from_value) if kind == 'freq'
                                       else parse_pattern_key(from_value)) is None:
            problems.append('սկզբնական արժեք')
    if status not in ('accepted', 'rejected'):
        problems.append('կարգավիճակ')
    return Decision(customer_id, agent_id, kind, value, status, updated_at, updated_by,
                    from_value), problems


_FIX_HINT = ' — ուղղեք կամ ջնջեք ֆայլը։ Ծրագիրը լռելյայն արժեքներ ինքնուրույն չի դնում'


class Store:
    """Доступ к route_optimizer.db. Соединение открывается на каждую операцию."""

    def __init__(self, path: str):
        self.path = path

    def _name(self) -> str:
        return f'Երթուղիների կարգավորումների բազա {os.path.basename(self.path)}'

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
            raise StoreError('Երթուղիների բազայում սխեմայի տարբերակը նշված չէ')
        return int(row[0])

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'meta' in tables:
            version = Store._schema_version(conn)
            if version > SCHEMA_VERSION:
                raise StoreError(f'Երթուղիների բազան ստեղծվել է ծրագրի ավելի նոր տարբերակով '
                                 f'(սխեմա {version}, աջակցվում է {SCHEMA_VERSION})')
            if version < SCHEMA_VERSION:
                Store._migrate(conn)
            return
        if tables:
            raise StoreError('Ֆայլը երթուղիների բազա չէ (կան օտար աղյուսակներ)')
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
                    raise StoreError(f'Երթուղիների բազայի սխեման հնարավոր չէ թարմացնել {version} տարբերակից')
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
                        'van_agent_id, center_ok, updated_at, updated_by, fuel_empty_l_per_100km, '
                        'fuel_full_l_per_100km, wear_amd_per_km, wear_load_amd_per_km, big FROM trucks').fetchall()
                    manager_rows = conn.execute(
                        'SELECT agent_id, included, home_lat, home_lon, car_fuel_l_per_100km, '
                        'car_fuel_type, updated_at, updated_by FROM manager_profile').fetchall()
                    geo_rows = conn.execute(
                        'SELECT customer_id, lat, lon FROM customer_geo_override').fetchall()
                    window_rows = conn.execute(
                        'SELECT customer_id, kind, t1, t2, tol FROM customer_window').fetchall()
                    access_rows = conn.execute(
                        'SELECT customer_id, mode, trucks FROM customer_vehicle_access').fetchall()
                    unload_rows = conn.execute('SELECT customer_id, fixed_min FROM customer_unload').fetchall()
                    rule_rows = conn.execute('SELECT customer_id, solo, center FROM customer_rule').fetchall()
                    conn.execute('COMMIT')
                except BaseException:
                    if conn.in_transaction:
                        conn.execute('ROLLBACK')
                    raise
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise StoreError(f'{self._name()}: վնասված է կամ հասանելի չէ{_FIX_HINT}') from e

        raw = dict(DEFAULT_SETTINGS)
        for key, value in setting_rows:
            if key not in DEFAULT_SETTINGS:
                continue  # ключ из будущей версии — игнорируем
            try:
                raw[key] = json.loads(value)
            except (TypeError, ValueError) as e:
                raise StoreError(f'{self._name()}: վնասված է «{key}» կարգավորումը{_FIX_HINT}') from e
        # предела форс-мажора в базе ещё нет (база до этой настройки), а день машин кончается позже 20:00 —
        # предел = конец дня: значение по умолчанию не должно делать базу «повреждённой»
        work_end = raw.get('truck_work_end')
        if ('truck_overtime_end' not in {k for k, _ in setting_rows} and isinstance(work_end, str)
                and _HHMM_RE.match(work_end) and _minutes(work_end) > _minutes(raw['truck_overtime_end'])):
            raw['truck_overtime_end'] = work_end
        settings, errors = validate_settings(raw, known_groups=None)
        if errors:
            raise StoreError(f'{self._name()}: վնասված են կարգավորումները ('
                             + '; '.join(f'{k}: {v}' for k, v in sorted(errors.items()))
                             + f'){_FIX_HINT}')

        depot = None
        if depot_row is not None:
            if not is_valid_point(depot_row[0], depot_row[1]):
                raise StoreError(f'{self._name()}: վնասված են պահեստի կոորդինատները{_FIX_HINT}')
            depot = (float(depot_row[0]), float(depot_row[1]))
        trucks: dict[str, Truck] = {}
        for row in truck_rows:
            truck, problems = _loaded_truck(row)
            if problems:
                raise StoreError(f'{self._name()}: վնասված է {row[0]!r} մեքենայի գրառումը '
                                 f'({", ".join(problems)}){_FIX_HINT}')
            trucks[truck.car_code] = truck
        managers: dict[int, ManagerProfile] = {}
        for row in manager_rows:
            profile, problems = _loaded_manager(row)
            if problems:
                raise StoreError(f'{self._name()}: վնասված է {row[0]!r} մենեջերի գրառումը '
                                 f'({", ".join(problems)}){_FIX_HINT}')
            managers[profile.agent_id] = profile
        geo: dict[int, Point] = {}
        for customer_id, lat, lon in geo_rows:
            if not _is_int(customer_id) or not is_valid_point(lat, lon):
                raise StoreError(f'{self._name()}: վնասված է {customer_id!r} հաճախորդի ձեռքով նշված կետը{_FIX_HINT}')
            geo[customer_id] = (float(lat), float(lon))
        windows: dict[int, CustomerWindow] = {}
        for customer_id, kind, t1, t2, tol in window_rows:
            # в базе допуск у at записан всегда: пустой — битая строка, а не «по умолчанию»
            w, err = check_window({'kind': kind, 't1': t1, 't2': t2, 'tol': tol})
            if err or not _is_int(customer_id) or (kind == 'at' and tol is None):
                raise StoreError(f'{self._name()}: վնասված է {customer_id!r} հաճախորդի ընդունման ժամը{_FIX_HINT}')
            windows[customer_id] = w
        access: dict[int, VehicleAccess] = {}
        for customer_id, mode, encoded in access_rows:
            try:
                rule, err = check_access({'mode': mode, 'trucks': json.loads(encoded)})
            except (TypeError, ValueError):
                rule, err = None, 'повреждён список машин'
            if err or not _is_int(customer_id) or not 0 < customer_id < 2 ** 31:
                raise StoreError(f'{self._name()}: վնասված է {customer_id!r} հաճախորդի մեքենաների '
                                 f'սահմանափակումը{_FIX_HINT}')
            access[customer_id] = rule
        unload: dict[int, float] = {}
        for customer_id, fixed in unload_rows:
            minutes, _ = check_unload_min(fixed)
            if minutes is None or not _is_int(customer_id) or not 0 < customer_id < 2 ** 31:
                raise StoreError(f'{self._name()}: վնասված է {customer_id!r} խանութում ժամանակի արժեքը{_FIX_HINT}')
            unload[customer_id] = minutes
        if any(not _is_int(c) or not 0 < c < 2 ** 31 or a not in (0, 1) or b not in (0, 1) for c, a, b in rule_rows):
            raise StoreError(f'{self._name()}: վնասված են խանութների առաքման կանոնները{_FIX_HINT}')
        return Bundle(settings, depot, trucks, managers, geo, windows, vehicle_access=access, unload_min=unload,
                      solo=frozenset(c for c, a, _ in rule_rows if a), center_allow=frozenset(c for c, _, b in rule_rows if b))

    def load_copy(self) -> tuple[Bundle, int | None]:
        """Настройки, не меняя саму базу: её копия (sqlite backup из соединения только на чтение) во временной папке,
        приведённая к схеме программы. Для команд вне сервера: warm задачи обновления идёт до перезапуска, и миграция
        самой базы сломала бы каждое обращение к ней ещё работающему серверу прежней версии. Вторым значением — версия
        схемы самой базы; базы нет — настройки по умолчанию и None (файл не создаётся)."""
        import shutil
        import tempfile
        from pathlib import Path

        folder = tempfile.mkdtemp(prefix='routes-copy-')
        try:
            copy = Store(os.path.join(folder, 'route_optimizer.db'))
            version = None
            if os.path.exists(self.path):
                source = sqlite3.connect(Path(os.path.abspath(self.path)).as_uri() + '?mode=ro', uri=True)
                try:
                    target = sqlite3.connect(copy.path)
                    try:
                        source.backup(target)   # не читается — исключение: настройки по умолчанию молча не подставим
                        try:
                            version = self._schema_version(target)
                        except (sqlite3.Error, StoreError):
                            version = None   # не база маршрутов — это скажет load() копии
                    finally:
                        target.close()
                finally:
                    source.close()
            return copy.load(), version
        finally:
            shutil.rmtree(folder, ignore_errors=True)

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
            raise StoreError(f'{self._name()}: չհաջողվեց պահպանել կարգավորումները') from e

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
                         'active, center_ok, big, updated_at, updated_by) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) '
                         'ON CONFLICT(car_code) DO UPDATE SET capacity_kg = excluded.capacity_kg, '
                         'fuel_l_per_100km = excluded.fuel_l_per_100km, agent_id = excluded.agent_id, '
                         'active = excluded.active, center_ok = excluded.center_ok, big = excluded.big, '
                         'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                         (t.car_code, t.capacity_kg, t.fuel_l_per_100km, t.agent_id,
                          None if t.active is None else int(t.active),
                          None if t.center_ok is None else int(t.center_ok),
                          None if t.big is None else int(t.big), now, user))
        if changes.manual_trucks is not None:
            keep = [t.car_code for t in changes.manual_trucks]
            conn.execute(f'DELETE FROM trucks WHERE manual = 1 AND car_code NOT IN ({",".join("?" * len(keep))})'
                         if keep else 'DELETE FROM trucks WHERE manual = 1', keep)
            # экспедитор переходит от машины к машине в одном сохранении: сначала снимаем, потом ставим
            # (уникальный индекс trucks_one_van проверяется на каждой строке)
            conn.execute('UPDATE trucks SET van_agent_id = NULL WHERE manual = 1')
            for t in changes.manual_trucks:
                conn.execute('INSERT INTO trucks(car_code, capacity_kg, fuel_l_per_100km, agent_id, active, manual, '
                             'name, van_agent_id, center_ok, big, updated_at, updated_by) '
                             'VALUES(?, ?, ?, NULL, ?, 1, ?, ?, ?, ?, ?, ?) '
                             'ON CONFLICT(car_code) DO UPDATE SET capacity_kg = excluded.capacity_kg, '
                             'fuel_l_per_100km = excluded.fuel_l_per_100km, active = excluded.active, '
                             'name = excluded.name, van_agent_id = excluded.van_agent_id, '
                             'center_ok = excluded.center_ok, big = excluded.big, '
                             'updated_at = excluded.updated_at, updated_by = excluded.updated_by '
                             'WHERE trucks.manual = 1',
                             (t.car_code, t.capacity_kg, t.fuel_l_per_100km, int(bool(t.active)), t.name,
                              t.van_agent_id, None if t.center_ok is None else int(t.center_ok),
                              None if t.big is None else int(t.big), now, user))
        # Новые нормы записываются только в собственную SQLite, в той же транзакции настроек.
        for t in (*changes.trucks, *(changes.manual_trucks or ())):
            conn.execute('UPDATE trucks SET fuel_empty_l_per_100km = ?, fuel_full_l_per_100km = ?, '
                         'wear_amd_per_km = ?, wear_load_amd_per_km = ? WHERE car_code = ?',
                         (*(getattr(t, key) for key in LOAD_COST_FIELDS), t.car_code))
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
            raise StoreError(f'{self._name()}: վնասված է կամ հասանելի չէ{_FIX_HINT}') from e

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
                raise StoreError(f'{self._name()}: վնասված է {row[0]!r} հաճախորդի որոշումը '
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

        self._transaction(write, 'չհաջողվեց պահպանել որոշումները')

    def reset_decisions(self) -> int:
        """«Сбросить все»: удалить все решения (и отработавшие). Возвращает, сколько было действующих."""
        def write(conn: sqlite3.Connection) -> int:
            active = conn.execute("SELECT COUNT(*) FROM decision WHERE status <> 'retired'").fetchone()[0]
            conn.execute('DELETE FROM decision')
            return active

        return self._transaction(write, 'չհաջողվեց չեղարկել որոշումները')

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

        return self._transaction(write, 'չհաջողվեց թարմացնել որոշումները')

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

        self._transaction(write, 'չհաջողվեց պահպանել հաշվարկը')

    def _scenario(self, row: tuple | None) -> Scenario | None:
        if row is None:
            return None
        scenario_id, created_at, created_by, params, result = row
        try:
            return Scenario(scenario_id, created_at, created_by, json.loads(params), json.loads(result))
        except (TypeError, ValueError, RecursionError) as e:
            raise StoreError(f'{self._name()}: վնասված է {scenario_id!r} պահպանված հաշվարկը'
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

    def save_customer_window(self, customer_id: int, window: CustomerWindow | None, user: str | None) -> None:
        """Окно приёма клиента (проверенное check_window); None — убрать: клиент принимает в любое время."""
        if not _is_int(customer_id) or not 0 < customer_id < 2 ** 31:
            raise ValueError('customer_id: положительное целое')
        if window is not None and check_window(window.to_json())[1]:
            raise ValueError('окно приёма не прошло проверку')

        self._transaction(lambda conn: self._write_customer_window(conn, customer_id, window, user),
                          'не удалось сохранить окно приёма клиента')

    @staticmethod
    def _write_customer_window(conn: sqlite3.Connection, customer_id: int,
                               window: CustomerWindow | None, user: str | None) -> None:
        if window is None:
            conn.execute('DELETE FROM customer_window WHERE customer_id = ?', (customer_id,))
        else:
            conn.execute('INSERT INTO customer_window(customer_id, kind, t1, t2, tol, updated_at, updated_by) '
                         'VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(customer_id) DO UPDATE SET kind = excluded.kind, '
                         't1 = excluded.t1, t2 = excluded.t2, tol = excluded.tol, '
                         'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                         (customer_id, window.kind, window.t1, window.t2, window.tol, _now(), user))

    def save_customer_vehicles(self, customer_id: int, access: VehicleAccess | None, user: str | None) -> None:
        """Сохраняет проверенный допуск магазина в собственной базе; None снимает ограничение."""
        if not _is_int(customer_id) or not 0 < customer_id < 2 ** 31:
            raise ValueError('customer_id: положительное целое')
        if access is not None and (not isinstance(access, VehicleAccess) or check_access(access.to_json())[1]):
            raise ValueError('ограничение машин не прошло проверку')

        self._transaction(lambda conn: self._write_customer_vehicles(conn, customer_id, access, user),
                          'չհաջողվեց պահպանել խանութի մեքենաները')

    @staticmethod
    def _write_customer_vehicles(conn: sqlite3.Connection, customer_id: int,
                                 access: VehicleAccess | None, user: str | None) -> None:
        if access is None:
            conn.execute('DELETE FROM customer_vehicle_access WHERE customer_id = ?', (customer_id,))
        else:
            conn.execute('INSERT INTO customer_vehicle_access(customer_id, mode, trucks, updated_at, updated_by) '
                         'VALUES(?, ?, ?, ?, ?) ON CONFLICT(customer_id) DO UPDATE SET mode = excluded.mode, '
                         'trucks = excluded.trucks, updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                         (customer_id, access.mode, json.dumps(list(access.trucks), ensure_ascii=False), _now(), user))

    def save_customer_constraints(self, customer_id: int, access: VehicleAccess | None,
                                  window: CustomerWindow | None, user: str | None,
                                  unload_min: float | None | Literal[_Keep.KEEP] = KEEP,
                                  solo: bool | Literal[_Keep.KEEP] = KEEP,
                                  center: bool | Literal[_Keep.KEEP] = KEEP) -> None:
        """Машины, время доставки, время у магазина (unload_min, №50; KEEP — не менять, None — убрать), «отдельный рейс» и
        «в центр машинам допуска» (solo, center, №78; KEEP — не менять) из одной карточки: единая транзакция, без
        частичного сохранения."""
        if not _is_int(customer_id) or not 0 < customer_id < 2 ** 31:
            raise ValueError('customer_id: положительное целое')
        if access is not None and (not isinstance(access, VehicleAccess) or check_access(access.to_json())[1]):
            raise ValueError('ограничение машин не прошло проверку')
        if window is not None and (not isinstance(window, CustomerWindow) or check_window(window.to_json())[1]):
            raise ValueError('окно приёма не прошло проверку')
        if unload_min is not KEEP and unload_min is not None and check_unload_min(unload_min)[1]:
            raise ValueError('время у магазина не прошло проверку')

        if any(f is not KEEP and not isinstance(f, bool) for f in (solo, center)):
            raise ValueError('solo, center: true или false')

        def write(conn: sqlite3.Connection) -> None:
            self._write_customer_vehicles(conn, customer_id, access, user)
            self._write_customer_window(conn, customer_id, window, user)
            if unload_min is not KEEP:
                self._write_customer_unload(conn, customer_id, unload_min, user)
            if solo is not KEEP or center is not KEEP:
                self._write_customer_rule(conn, customer_id, solo, center, user)

        self._transaction(write, 'չհաջողվեց պահպանել խանութի առաքման պայմանները')

    @staticmethod
    def _write_customer_rule(conn: sqlite3.Connection, customer_id: int, solo: bool | Literal[_Keep.KEEP],
                             center: bool | Literal[_Keep.KEEP], user: str | None) -> None:
        """Правила магазина (№78): KEEP — как было; оба сняты — строки нет."""
        row = conn.execute('SELECT solo, center FROM customer_rule WHERE customer_id = ?', (customer_id,)).fetchone()
        was = (bool(row[0]), bool(row[1])) if row is not None else (False, False)
        new = (was[0] if solo is KEEP else solo, was[1] if center is KEEP else center)
        if not any(new):
            conn.execute('DELETE FROM customer_rule WHERE customer_id = ?', (customer_id,))
            return
        conn.execute('INSERT INTO customer_rule(customer_id, solo, center, updated_at, updated_by) VALUES(?, ?, ?, ?, ?) '
                     'ON CONFLICT(customer_id) DO UPDATE SET solo = excluded.solo, center = excluded.center, '
                     'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                     (customer_id, int(new[0]), int(new[1]), _now(), user))

    def save_customer_unload(self, customer_id: int, unload_min: float | None, user: str | None) -> None:
        """Только время у магазина (№50; проверенное check_unload_min, None — убрать: по норме) своей транзакцией —
        для «Развоза»: допуск и окно приёма не перезаписываются, поэтому правка не затрёт параллельную правку условий."""
        if not _is_int(customer_id) or not 0 < customer_id < 2 ** 31:
            raise ValueError('customer_id: положительное целое')
        if unload_min is not None and check_unload_min(unload_min)[1]:
            raise ValueError('время у магазина не прошло проверку')

        self._transaction(lambda conn: self._write_customer_unload(conn, customer_id, unload_min, user),
                          'не удалось сохранить время у магазина')

    @staticmethod
    def _write_customer_unload(conn: sqlite3.Connection, customer_id: int,
                               unload_min: float | None, user: str | None) -> None:
        if unload_min is None:
            conn.execute('DELETE FROM customer_unload WHERE customer_id = ?', (customer_id,))
        else:
            conn.execute('INSERT INTO customer_unload(customer_id, fixed_min, updated_at, updated_by) '
                         'VALUES(?, ?, ?, ?) ON CONFLICT(customer_id) DO UPDATE SET fixed_min = excluded.fixed_min, '
                         'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                         (customer_id, float(unload_min), _now(), user))

    def truck_drivers(self, day: str, role: str = 'driver') -> tuple[dict[str, str], frozenset[str]]:
        """Водители (role='driver') или առաքիչ (role='helper') машин на день YYYY-MM-DD (№62): (машина → имя, машины с
        подменой на этот день). Человек дня — подмена этого дня, если есть, иначе постоянный: запись с наибольшим
        from_day ≤ day. Пустое имя («никого») — не в словаре (подмена «никого» — во втором множестве)."""
        table = _crew_table(role)

        def query(conn: sqlite3.Connection) -> tuple[list[Any], list[Any]]:
            permanent = conn.execute(
                f'SELECT d.car_code, d.name FROM {table} d WHERE d.only_day = 0 AND d.from_day = '
                f'(SELECT MAX(x.from_day) FROM {table} x WHERE x.car_code = d.car_code AND x.only_day = 0 '
                'AND x.from_day <= ?)', (day,)).fetchall()
            subs = conn.execute(f'SELECT car_code, name FROM {table} WHERE only_day = 1 AND from_day = ?',
                                (day,)).fetchall()
            return permanent, subs

        permanent, subs = self._read(query)
        names = dict(permanent) | dict(subs)
        return {code: name for code, name in names.items() if name}, frozenset(code for code, _ in subs)

    def driver_names(self, since: str = '') -> list[str]:
        """Имена водителей и առաքիչ из записей (№62) с днём записи не раньше since (YYYY-MM-DD; '' — все), по алфавиту:
        «свои» для выбора — давно не встречавшиеся из списка уходят сами (опечатку не нужно удалять)."""
        rows = self._read(lambda conn: conn.execute(
            "SELECT name FROM truck_driver WHERE name <> '' AND from_day >= ? "
            "UNION SELECT name FROM truck_helper WHERE name <> '' AND from_day >= ?", (since, since)).fetchall())
        return sorted(r[0] for r in rows)

    def save_truck_driver(self, car_code: str, day: str, name: str, user: str | None, only_day: bool = False,
                          role: str = 'driver') -> None:
        """Один человек машины — save_truck_crew({role: (name, only_day)})."""
        self.save_truck_crew(car_code, day, {role: (name, only_day)}, user)

    def save_truck_crew(self, car_code: str, day: str, people: Mapping[str, tuple[str, bool]], user: str | None) -> None:
        """Водитель и/или առաքիչ машины одной транзакцией (№62; people — роль → (проверенное check_driver_name имя, ''
        — никого; only_day)). only_day=False — постоянный с day и до следующей смены (подмена этого дня снимается,
        подмены других дней остаются); only_day=True — подмена только на day (прошедший день — всегда так), а подмена тем
        же, кто там постоянный (или «никого», если постоянного нет), — просто снимает подмену дня: иначе строка дня
        закрепила бы имя и пережила бы следующую постоянную смену. Запись того же дня и вида заменяется — ошибку
        исправляют, выбрав верного человека."""
        if not isinstance(car_code, str) or not car_code or len(car_code) > 64:
            raise ValueError('car_code: непустая строка до 64 символов')
        if not isinstance(day, str) or not _ISO_DAY_RE.match(day):
            raise ValueError('day: дата YYYY-MM-DD')
        if not people:
            raise ValueError('people: хотя бы одна роль')
        for role, (name, only_day) in people.items():
            _crew_table(role)
            if check_driver_name(name) != (name, None) or not isinstance(only_day, bool):
                raise ValueError('имя или only_day не прошли проверку')

        def write(conn: sqlite3.Connection) -> None:
            for role, (name, only_day) in people.items():
                table = _crew_table(role)
                drop_sub = f'DELETE FROM {table} WHERE car_code = ? AND from_day = ? AND only_day = 1'
                if only_day:
                    row = conn.execute(f'SELECT name FROM {table} WHERE car_code = ? AND only_day = 0 AND from_day <= ? '
                                       'ORDER BY from_day DESC LIMIT 1', (car_code, day)).fetchone()
                    if name == (row[0] if row else ''):     # подмена тем же, кто там и так, — подмены нет
                        conn.execute(drop_sub, (car_code, day))
                        continue
                conn.execute(f'INSERT INTO {table}(car_code, from_day, only_day, name, updated_at, updated_by) '
                             'VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(car_code, from_day, only_day) DO UPDATE SET '
                             'name = excluded.name, updated_at = excluded.updated_at, updated_by = excluded.updated_by',
                             (car_code, day, 1 if only_day else 0, name, _now(), user))
                if not only_day:
                    conn.execute(drop_sub, (car_code, day))

        self._transaction(write, 'не удалось сохранить водителя машины')

    def driver_absences(self, day: str) -> dict[str, str]:
        """Водители, которые не вышли в день YYYY-MM-DD (№77): имя → по какой день (включительно) их нет — самый поздний
        конец среди записей, куда попадает day."""
        rows = self._read(lambda conn: conn.execute(
            'SELECT name, MAX(to_day) FROM driver_absence WHERE from_day <= ? AND to_day >= ? GROUP BY name',
            (day, day)).fetchall())
        return dict(rows)

    def save_driver_absence(self, name: str, day: str, until: str | None, user: str | None) -> None:
        """Водитель name не выходит (№77): until=None — только в day, иначе с day по until включительно (отпуск, болезнь).
        Запись с тем же началом заменяется (исправить срок)."""
        until = day if until is None else until
        if check_driver_name(name) != (name, None) or not name:
            raise ValueError('имя водителя не прошло проверку')
        if not all(isinstance(d, str) and _ISO_DAY_RE.match(d) for d in (day, until)) or until < day:
            raise ValueError('day, until: даты YYYY-MM-DD, until не раньше day')
        self._transaction(lambda conn: conn.execute(
            'INSERT INTO driver_absence(name, from_day, to_day, updated_at, updated_by) VALUES(?, ?, ?, ?, ?) '
            'ON CONFLICT(name, from_day) DO UPDATE SET to_day = excluded.to_day, updated_at = excluded.updated_at, '
            'updated_by = excluded.updated_by', (name, day, until, _now(), user)), 'не удалось сохранить отсутствие водителя')

    def save_driver_present(self, name: str, day: str, user: str | None) -> None:
        """Водитель name вышел с дня day (№77). Отсутствие, которое накрывает day: начатое раньше — кончается накануне
        (прошлые дни не переписываются), начатое в day — удаляется; отдельные будущие отсутствия остаются."""
        if not isinstance(day, str) or not _ISO_DAY_RE.match(day):
            raise ValueError('day: дата YYYY-MM-DD')
        before = (date.fromisoformat(day) - timedelta(days=1)).isoformat()

        def write(conn: sqlite3.Connection) -> None:
            cover = 'name = ? AND from_day <= ? AND to_day >= ?'
            conn.execute(f'DELETE FROM driver_absence WHERE {cover} AND from_day = ?', (name, day, day, day))
            conn.execute(f'UPDATE driver_absence SET to_day = ?, updated_at = ?, updated_by = ? WHERE {cover}',
                         (before, _now(), user, name, day, day))

        self._transaction(write, 'не удалось сохранить отсутствие водителя')

    def load_dispatch(self, day: str) -> tuple[dict[str, Any], int] | None:
        """Черновик плана развоза на дату (YYYY-MM-DD): (данные, номер правки) или None."""
        row = self._read(lambda conn: conn.execute(
            'SELECT data, rev FROM dispatch_plan WHERE day = ?', (day,)).fetchone())
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError, RecursionError) as e:
            raise StoreError(f'{self._name()}: վնասված է {day} օրվա առաքման պլանը{_FIX_HINT}') from e
        if not isinstance(data, dict) or not _is_int(row[1]):
            raise StoreError(f'{self._name()}: վնասված է {day} օրվա առաքման պլանը{_FIX_HINT}')
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

    def count_dispatch_overtime(self, since: str, until: str) -> int:
        """Дней в [since, until] (YYYY-MM-DD), когда машины по плану развоза работали дольше дня
        (черновик с "overtime": true). Битый JSON черновика не считается и не роняет запрос."""
        row = self._read(lambda conn: conn.execute(
            "SELECT COUNT(*) FROM dispatch_plan WHERE day BETWEEN ? AND ? "
            "AND CASE WHEN json_valid(data) THEN json_extract(data, '$.overtime') END = 1",
            (since, until)).fetchone())
        return int(row[0]) if row else 0

    def delete_dispatch(self, day: str, expected_rev: int | None = None) -> bool:
        """«Начать заново»: черновик на дату удаляется. expected_rev — номер прочитанного черновика: с тех пор изменён
        (склад отметил погрузку, №78; другая вкладка) — False, ничего не удалено."""
        def drop(conn: sqlite3.Connection) -> bool:
            if expected_rev is not None:
                row = conn.execute('SELECT rev FROM dispatch_plan WHERE day = ?', (day,)).fetchone()
                if (row[0] if row is not None else 0) != expected_rev:
                    return False
            conn.execute('DELETE FROM dispatch_plan WHERE day = ?', (day,))
            return True
        return self._transaction(drop, 'не удалось удалить план развоза')

    # --- обучение по факту машин (learning-loop-plan.md, этап 4) ---

    def save_learned(self, run_day: str, outcomes: Collection[Any]) -> None:
        """Итоги прогона обучения (learning.Outcome) за день run_day — одной транзакцией; повторный прогон того же дня
        заменяет строки своих (вид, машина)."""
        now = _now()

        def write(conn: sqlite3.Connection) -> None:
            for o in outcomes:
                conn.execute(
                    'INSERT INTO learned_norms(kind, scope, run_day, params, model_id, n_obs, n_test, train_from, '
                    'train_to, test_from, test_to, mae_before, mae_after, accepted, reason, created_at, confidence) '
                    'VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(kind, scope, run_day) DO UPDATE '
                    'SET params = excluded.params, model_id = excluded.model_id, n_obs = excluded.n_obs, '
                    'n_test = excluded.n_test, train_from = excluded.train_from, train_to = excluded.train_to, '
                    'test_from = excluded.test_from, test_to = excluded.test_to, mae_before = excluded.mae_before, '
                    'mae_after = excluded.mae_after, accepted = excluded.accepted, reason = excluded.reason, '
                    'created_at = excluded.created_at, confidence = excluded.confidence',
                    (o.kind, o.scope, run_day,
                     json.dumps(o.params, ensure_ascii=False, sort_keys=True, allow_nan=False)
                     if o.params is not None else None, o.model_id, o.n_obs, o.n_test, o.train_from, o.train_to,
                     o.test_from, o.test_to, o.mae_before, o.mae_after, int(o.accepted), o.reason, now,
                     getattr(o, 'confidence', None)))

        self._transaction(write, 'չհաջողվեց պահպանել սովորած նորմերը')

    def learned(self, before: str | None = None, accepted_only: bool = False) -> list[dict[str, Any]]:
        """Журнал выученных норм по возрастанию дня прогона (before — только прогоны раньше этого дня; accepted_only —
        только принятые: из них learning.in_effect выбирает действующие)."""
        where = [w for w, on in (('run_day < ?', before is not None), ('accepted = 1', accepted_only)) if on]
        rows = self._read(lambda conn: conn.execute(
            'SELECT id, kind, scope, run_day, params, model_id, n_obs, n_test, train_from, train_to, test_from, '
            'test_to, mae_before, mae_after, accepted, reason, created_at, confidence FROM learned_norms '
            + (f'WHERE {" AND ".join(where)} ' if where else '') + 'ORDER BY run_day, kind, scope',
            (before,) if before is not None else ()).fetchall())
        keys = ('id', 'kind', 'scope', 'run_day', 'params', 'model_id', 'n_obs', 'n_test', 'train_from', 'train_to',
                'test_from', 'test_to', 'mae_before', 'mae_after', 'accepted', 'reason', 'created_at', 'confidence')
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            try:
                d['params'] = json.loads(d['params']) if d['params'] is not None else None
            except (TypeError, ValueError, RecursionError) as e:
                raise StoreError(f'{self._name()}: վնասված է սովորած նորմի {d["id"]} գրառումը{_FIX_HINT}') from e
            d['accepted'] = bool(d['accepted'])
            out.append(d)
        return out

    def learning_last_run(self) -> str | None:
        """День последнего удачного прогона обучения (meta learning_last_run) — ночной поток догоняет пропущенный."""
        row = self._read(lambda conn: conn.execute("SELECT value FROM meta WHERE key = 'learning_last_run'").fetchone())
        return row[0] if row else None

    def save_learning_last_run(self, day: str) -> None:
        self._transaction(lambda conn: conn.execute(
            "INSERT INTO meta(key, value) VALUES('learning_last_run', ?) ON CONFLICT(key) DO UPDATE SET value = "
            'excluded.value', (day,)), 'չհաջողվեց գրանցել ուսուցման վերահաշվարկի օրը')

    # Параметры «Աշխատավարձ» (crew_pay) — строка CREW_PAY_KEY таблицы settings без смены схемы: load() читает только ключи
    # DEFAULT_SETTINGS, поэтому формула зарплат не попадает ни в настройки маршрутов, ни в их отпечаток. Кто и когда менял —
    # в той же JSON-строке (updated_at, updated_by).
    def crew_pay_params(self) -> tuple[crew_pay.Params, str | None, str | None]:
        """(параметры формулы зарплат, когда и кем сохранены); не сохраняли — по умолчанию; битая запись — StoreError."""
        row = self._read(lambda conn: conn.execute('SELECT value FROM settings WHERE key = ?', (CREW_PAY_KEY,)).fetchone())
        if row is None:
            return crew_pay.Params(), None, None
        try:
            raw = json.loads(row[0])
            params, errors = crew_pay.check_params(raw)
        except (TypeError, ValueError, RecursionError, OverflowError):
            raw, params, errors = None, None, {'_': 'JSON'}
        if params is None:
            raise StoreError(f'{self._name()}: վնասված են աշխատավարձի պարամետրերը ({", ".join(sorted(errors))}){_FIX_HINT}')
        at, by = raw.get('updated_at'), raw.get('updated_by')
        return params, at if isinstance(at, str) else None, by if isinstance(by, str) else None

    def save_crew_pay_params(self, params: crew_pay.Params, user: str | None) -> None:
        value = {**params.json(), 'updated_at': _now(), 'updated_by': user}
        self._transaction(lambda conn: conn.execute(
            'INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (CREW_PAY_KEY, json.dumps(value, ensure_ascii=False))), 'չհաջողվեց պահպանել աշխատավարձի պարամետրերը')

    def learning_auto(self) -> dict[str, bool]:
        """Автообучение по виду, выбранное владельцем; нет строки — learning.DEFAULT_AUTO."""
        rows = self._read(lambda conn: conn.execute('SELECT kind, auto FROM learning_switch').fetchall())
        return {k: bool(v) for k, v in rows}

    def save_learning_auto(self, kind: str, auto: bool, user: str | None) -> None:
        self._transaction(lambda conn: conn.execute(
            'INSERT INTO learning_switch(kind, auto, updated_at, updated_by) VALUES(?, ?, ?, ?) '
            'ON CONFLICT(kind) DO UPDATE SET auto = excluded.auto, updated_at = excluded.updated_at, '
            'updated_by = excluded.updated_by', (kind, int(auto), _now(), user)),
            'չհաջողվեց պահպանել ավտոմատ ուսուցման փոխանջատիչը')

    def measurements(self) -> list[dict[str, Any]]:
        rows = self._read(lambda conn: conn.execute(
            'SELECT day, car_code, data, updated_at FROM route_measurement ORDER BY day, car_code').fetchall())
        return [dict(json.loads(raw), day=day, car_code=code, updated_at=at) for day, code, raw, at in rows]

    def save_measurement(self, day: str, code: str, data: Mapping[str, Any], user: str | None) -> None:
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False)
        self._transaction(lambda conn: conn.execute(
            'INSERT INTO route_measurement(day, car_code, data, updated_at, updated_by) VALUES(?,?,?,?,?) '
            'ON CONFLICT(day, car_code) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at, '
            'updated_by=excluded.updated_by', (day, code, raw, _now(), user)), 'չհաջողվեց պահպանել չափումը')

    # --- журнал гаража (№53): ремонты, ДТП, страховка и налоги, пробег ---

    _GARAGE_COLUMNS = ('id, car_code, day, kind, what, amount_amd, odometer_km, note, created_at, created_by, '
                       'updated_at, updated_by, deleted_at, deleted_by, replaced_by, spread_months')

    def garage_entries(self, deleted: bool = False) -> list[GarageEntry]:
        """Записи журнала по дню и номеру: живые; deleted — и удалённые («показать удалённые» администратора). Битая
        строка — StoreError: расчёт не должен молча считать без неё."""
        rows = self._read(lambda conn: conn.execute(
            f'SELECT {self._GARAGE_COLUMNS} FROM garage_entry'
            + ('' if deleted else ' WHERE deleted_at IS NULL') + ' ORDER BY day, id').fetchall())
        out = []
        for row in rows:
            entry, problems = _loaded_garage(row)
            if problems:
                # текст доходит до любой страницы раздела (журнал читают все расчёты) — по-армянски, как у прочих (№58)
                raise StoreError(f'{self._name()}: վնասված է ավտոտնակի №{row[0]} գրառումը '
                                 f'({", ".join(problems)}){_FIX_HINT}')
            out.append(entry)
        return out

    @staticmethod
    def _garage_conflict(conn: sqlite3.Connection, item: GarageInput, exclude: int | None) -> str | None:
        """Спидометр машины не убывает во времени: показание не меньше любого живого показания более раннего дня и не
        больше любого более позднего. И правдоподобен: от ближайшего показания раньше, позже и того же дня прирост не
        больше GARAGE_KM_PER_DAY × max(1, дней) — опечатка в разряде (1 305 000 вместо 130 500) не принимается и не
        запирает следующие верные показания. Нарушение — причина с соседней записью (день, км; у темпа — км в сутки)."""
        day = item.day.isoformat()
        base = 'SELECT day, odometer_km FROM garage_entry WHERE car_code = ? AND deleted_at IS NULL AND id IS NOT ? AND '
        row = conn.execute(base + 'day < ? AND odometer_km > ? ORDER BY odometer_km DESC, day DESC LIMIT 1',
                           (item.car_code, exclude, day, item.odometer_km)).fetchone()
        if row is not None:
            return f'Սպիդոմետրը չի կարող նվազել. {_ru_day(row[0])}-ին արդեն գրանցված է {_km_text(row[1])} կմ'
        row = conn.execute(base + 'day > ? AND odometer_km < ? ORDER BY odometer_km, day LIMIT 1',
                           (item.car_code, exclude, day, item.odometer_km)).fetchone()
        if row is not None:
            return (f'Սպիդոմետրը չի կարող նվազել. ավելի ուշ՝ {_ru_day(row[0])}-ին, գրանցված է '
                    f'{_km_text(row[1])} կմ')
        for where, args in (('day = ? ORDER BY abs(odometer_km - ?) DESC LIMIT 1', (day, item.odometer_km)),
                            ('day < ? ORDER BY day DESC, odometer_km DESC LIMIT 1', (day,)),
                            ('day > ? ORDER BY day, odometer_km LIMIT 1', (day,))):
            row = conn.execute(base + where, (item.car_code, exclude, *args)).fetchone()
            if row is None:
                continue
            rate = abs(item.odometer_km - row[1]) / max(1, abs((item.day - date.fromisoformat(row[0])).days))
            if rate > GARAGE_KM_PER_DAY:
                return (f'Ստուգեք սպիդոմետրը. {_ru_day(row[0])}-ի {_km_text(row[1])} կմ-ի համեմատ սա օրական '
                        f'{_km_text(round(rate))} կմ է (առավելագույնը՝ {_km_text(round(GARAGE_KM_PER_DAY))} կմ)')
        return None

    @staticmethod
    def _garage_odometer_id(conn: sqlite3.Connection, item: GarageInput) -> int | None:
        row = conn.execute("SELECT id FROM garage_entry WHERE car_code = ? AND day = ? AND kind = 'odometer' "
                           'AND deleted_at IS NULL', (item.car_code, item.day.isoformat())).fetchone()
        return row[0] if row is not None else None

    @staticmethod
    def _garage_write(conn: sqlite3.Connection, item: GarageInput, target: int | None, user: str | None) -> int:
        """Новая запись или правка живой target. У правки прежние значения не теряются: если что-то изменилось, прежняя
        версия копируется удалённой строкой с replaced_by = target (номер живой записи тот же)."""
        now = _now()
        values = (item.car_code, item.day.isoformat(), item.kind, item.what, item.amount_amd, item.odometer_km,
                  item.note, item.spread_months)
        if target is None:
            return conn.execute('INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, note, '
                                'spread_months, created_at, created_by) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                                (*values, now, user)).lastrowid
        old = conn.execute('SELECT car_code, day, kind, what, amount_amd, odometer_km, note, spread_months '
                           'FROM garage_entry WHERE id = ?', (target,)).fetchone()
        if tuple(old) != values:
            conn.execute('INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, note, spread_months, '
                         'created_at, created_by, updated_at, updated_by, deleted_at, deleted_by, replaced_by) SELECT '
                         'car_code, day, kind, what, amount_amd, odometer_km, note, spread_months, created_at, created_by, '
                         'updated_at, updated_by, ?, ?, id FROM garage_entry WHERE id = ?', (now, user, target))
        conn.execute('UPDATE garage_entry SET car_code = ?, day = ?, kind = ?, what = ?, amount_amd = ?, '
                     'odometer_km = ?, note = ?, spread_months = ?, updated_at = ?, updated_by = ? WHERE id = ?',
                     (*values, now, user, target))
        return target

    def save_garage_entry(self, item: GarageInput, user: str | None, entry_id: int | None = None) -> int:
        """Новая запись (entry_id None) или правка живой; возвращает номер записи. «Только пробег» у машины на день —
        одна: новая на тот же день обновляет прежнюю, правка в день, где она уже есть, — ошибка. Спидометр не убывает
        во времени и правдоподобен (_garage_conflict). Правка хранит прежнюю версию (_garage_write). Всё — в одной
        транзакции: проверка и запись не разойдутся с параллельной записью. GarageError — не принято (ничего не записано)."""
        def write(conn: sqlite3.Connection) -> int:
            target = self._garage_odometer_id(conn, item) if item.kind == 'odometer' else None
            if entry_id is not None:
                if conn.execute('SELECT 1 FROM garage_entry WHERE id = ? AND deleted_at IS NULL',
                                (entry_id,)).fetchone() is None:
                    raise GarageError({'_': 'Գրառումը չի գտնվել կամ ջնջված է'})
                if target is not None and target != entry_id:
                    raise GarageError({'day': 'Այս օրվա վազքն արդեն գրանցված է՝ փոխեք այն գրառումը'})
                target = entry_id
            problem = self._garage_conflict(conn, item, target)
            if problem:
                raise GarageError({'odometer_km': problem})
            return self._garage_write(conn, item, target, user)

        return self._transaction(write, 'չհաջողվեց պահպանել ավտոտնակի գրառումը')

    def save_garage_odometers(self, items: Sequence[GarageInput], user: str | None) -> dict[int, str]:
        """Пробег машин одной таблицей (раз в месяц): каждая строка — «только пробег» своей машины на свой день (тот же
        день — обновляет). Одна транзакция; строка, у которой спидометр убывает во времени или неправдоподобен
        (_garage_conflict), не пишется — {номер строки: причина}, остальные записываются."""
        if any(item.kind != 'odometer' for item in items):
            raise ValueError('в таблице пробега — только показания пробега')

        def write(conn: sqlite3.Connection) -> dict[int, str]:
            errors = {}
            for i, item in enumerate(items):
                target = self._garage_odometer_id(conn, item)
                problem = self._garage_conflict(conn, item, target)
                if problem:
                    errors[i] = problem
                else:
                    self._garage_write(conn, item, target, user)
            return errors

        return self._transaction(write, 'չհաջողվեց պահպանել վազքը')

    def delete_garage_entry(self, entry_id: int, user: str | None) -> bool:
        """Мягкое удаление: запись остаётся (её видит администратор), в расчётах не участвует. False — живой нет."""
        return self._transaction(lambda conn: conn.execute(
            'UPDATE garage_entry SET deleted_at = ?, deleted_by = ? WHERE id = ? AND deleted_at IS NULL',
            (_now(), user, entry_id)).rowcount == 1, 'չհաջողվեց ջնջել ավտոտնակի գրառումը')

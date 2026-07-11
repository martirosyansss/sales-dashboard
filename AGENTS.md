<!-- Generated: 2026-07-08 | Updated: 2026-07-08 -->

# Sales Dashboard

## Назначение
READ-ONLY аналитическая веб-панель поверх production ERP-системы **AS-Sales Management 7**
(Microsoft SQL Server). Приложение читает реальные данные о продажах, клиентах, менеджерах,
территориях и задолженностях, визуализирует их (Chart.js, AG-Grid) и предоставляет
конструктор дашбордов, генерацию планов и AI-ассистента на базе Anthropic Claude.
Весь UI и документация — на русском языке.

## 🚫 КРИТИЧЕСКОЕ ПРАВИЛО: База данных — ТОЛЬКО ЧТЕНИЕ
БД `SalesManagement` — это **боевая ERP-система**, в которой работают реальные пользователи.
- ✅ Разрешено: `SELECT`, `JOIN`, `WHERE`, `GROUP BY`, агрегатные функции.
- ❌ ЗАПРЕЩЕНО: `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`, `DROP`, `TRUNCATE`.
- Класс `DatabaseConnection` в [app_v2.py](app_v2.py) намеренно предоставляет только
  `execute_query()` (SELECT) — не добавляйте методов записи.
- Подробности: [⚠️_НЕ_ТРОГАТЬ_БАЗУ_ДАННЫХ.md](⚠️_НЕ_ТРОГАТЬ_БАЗУ_ДАННЫХ.md),
  [doc/ВАЖНО_READ_ONLY.md](doc/ВАЖНО_READ_ONLY.md).

## Две версии приложения (важно не путать)
| Версия | Файлы | БД | Статус |
|--------|-------|-----|--------|
| **v2 (актуальная)** | `app_v2.py`, `templates/*_v2.html` и остальные | Боевая ERP на `192.168.1.4`, `SalesManagement`, только SELECT | **Используется** |
| v1 (легаси/демо) | `app.py`, `database.py`, `sales_management.py` | Демо-схема на `localhost`, умеет CREATE/INSERT | Устарела, не запускать против боевой БД |

⚠️ `database.py` содержит `initialize_tables()` с `CREATE TABLE` — это часть **старой демо-схемы**
(4 таблицы: Customers/Products/Sales/SaleDetails), НЕ связанной с боевым ERP. Боевая БД v2
использует другие таблицы с префиксом `f` (см. ниже). Не запускайте v1-код против production.

## Ключевые файлы
| Файл | Описание |
|------|----------|
| `app_v2.py` | **Ядро (~7400 строк).** Монолитное Flask-приложение v2: ~90 маршрутов, вся бизнес-логика, SQL-запросы к боевой ERP, AI-ассистент |
| `app.py` | Легаси-приложение v1 (~1600 строк), демо-CRM |
| `database.py` | DAO старой демо-схемы (localhost, умеет запись) — только для v1 |
| `sales_management.py` | Бизнес-логика v1 (легаси) |
| `requirements_web.txt` | Зависимости: Flask 3.0, pyodbc, Werkzeug |
| `.env.example` | Шаблон переменных окружения (ANTHROPIC_API_KEY, DB_*, FLASK_*) |
| `start_web.bat` | Запуск v2: ставит зависимости и `python app_v2.py` (→ http://localhost:5000) |
| `start_dashboard.bat`, `start_server.bat` | Альтернативные скрипты запуска |
| `DEBT_CALCULATION_FORMULA.md` | **Обязательно к прочтению** перед правкой расчёта долга (см. ниже) |
| `*.json` (в корне) | Файлы-настройки — слой персистентности приложения (см. ниже) |

## Подкаталоги
| Каталог | Назначение |
|---------|------------|
| `templates/` | HTML-шаблоны Jinja2 + весь фронтенд-JS (инлайн). См. `templates/AGENTS.md` |
| `static/` | CSS и favicon (JS в основном инлайн в шаблонах). См. `static/AGENTS.md` |
| `doc/` | Документация схемы БД. ⚠️ Описывает СТАРУЮ демо-схему. См. `doc/AGENTS.md` |

## Реальная схема БД (production ERP, используется в app_v2.py)
Боевые таблицы (префикс `f` в колонках, армянская/f-нотация — НЕ то, что в `doc/`):
| Таблица | Роль | Ключевые колонки |
|---------|------|------------------|
| `CUSTOMERS` | Клиенты (~1809) | `fID`, `fCODE`, `fGROUP` |
| `SALES` | Продажи (~388k) | суммы, даты, менеджер, товар |
| `SALESAGENTS` | Менеджеры (~19) | id, имя |
| `SALEDOCDETAILS` | Позиции документов продаж | |
| `HICUSTOMERSDEBT` | Движения по долгу | `fCUSTOMERID`, `fDBCR` (D/C), `fSUM`, `fDATE`, `fDEBTDOCISN` |
| `HIRESTCUSTOMERSSUM` | Остатки: возвраты/предоплаты | `fCUSTOMERID`, `fTYPE` ('01'/'02'), `fSUM` |
| `CUSTOMERSALESAREAS` | Связь клиент↔территория↔группа | `fCUSTOMERID`, `fSALESAREA`, `fGROUP` |
| `SArea` | Территории продаж (дерево, `fTREEID='SArea'`) | |
| `DOCUMENTS` | Документы (шапки) | `fISN`, `fCUSTOMERID` |

## Формула расчёта долга (критичный инвариант бизнес-логики)
```
ДОЛГ = ДЕБЕТ(из HICUSTOMERSDEBT) − |Type01(возвраты)| − |Type02(предоплата)|
```
- Дебет: кумулятивный баланс `SUM(fDBCR='D' ? fSUM : −fSUM)` с `fDATE < конец_периода`.
- Type01/Type02: из `HIRESTCUSTOMERSSUM` по `fTYPE`.
- Графики долга показывают **накопленный** (кумулятивный) баланс, не изменения за день.
- ⚠️ Перед любой правкой расчёта долга читать [DEBT_CALCULATION_FORMULA.md](DEBT_CALCULATION_FORMULA.md).

## Слой настроек (JSON-файлы в корне)
Настройки приложения хранятся в JSON-файлах рядом с `app_v2.py` (константы `*_FILE` ~строка 4611).
Каждому — пара `load_*()` / `save_*()`:
| Файл | Содержимое |
|------|------------|
| `excluded_customers.json` / `excluded_groups.json` | Исключения из аналитики |
| `group_manager_assignments.json` | Ответственные менеджеры по группам `{"код_группы": [id,...]}` |
| `sales_area_group_assignments.json` | Привязка групп к территориям |
| `distributor_groups.json` | Группы-дистрибьюторы (создаётся при сохранении) |
| `selected_product_groups.json` | Выбранные товарные группы |
| `dashboard_builder_layout.json` (+ `_old`/`_restored`) | Раскладка конструктора дашбордов |
| `dashboard_widgets.json`, `dashboard_selected_areas.json`, `dashboard_selected_groups.json` | Состояние главного дашборда |
| `ai_selected_groups.json`, `ai_selected_areas.json`, `ai_analysis_settings.json` | Настройки AI-ассистента |

## AI-ассистент
Эндпоинты `/api/ai-*` используют `anthropic.Anthropic` (модель в коде: `claude-sonnet-4-20250514`,
[app_v2.py:3005](app_v2.py#L3005)). Ключ — из `ANTHROPIC_API_KEY` (`.env`). Работает поверх тех же
READ-ONLY данных; фильтруется по выбранным группам/территориям.

## Диагностические скрипты в корне (~230 файлов)
Это **одноразовые исследовательские/проверочные скрипты**, не часть рантайма приложения.
Категории по префиксу: `check_*` (91), `test_*` (44), `verify_*` (21), `find_*` (21),
`analyze_*` (13), `inspect_*` (8), `list_*` (8), `compare_*` (7), `debug_*` (5), `search_*` (4),
`show_*` (4), плюс `calc_*`, `investigate_*`, `add_*`, `final_*`.
- Многие обращаются к боевой БД напрямую — помнить про READ-ONLY.
- Не импортируются приложением; можно удалять/архивировать без влияния на рантайм.
- При создании нового такого скрипта — только SELECT-запросы.

## Для AI-агентов

### Работа в этом каталоге
- **Никаких операций записи в БД.** Только SELECT. Это боевой ERP.
- Правишь v2 → работай в `app_v2.py` и `templates/*` (особенно `_v2`, `dashboard_builder`,
  `areas`, `plans`, `customers_aggrid`). Не трогай v1 (`app.py`/`database.py`), если явно не просят.
- Затрагиваешь долги/планы → сначала прочитай `DEBT_CALCULATION_FORMULA.md`, сохраняй формулу.
- Изменения настроек делай через `save_*()`-функции, не редактируй JSON вручную в рантайме.
- Весь текст интерфейса и комментарии — на русском.

### Как проверять изменения
- Запуск: `python app_v2.py` (или `start_web.bat`) → http://localhost:5000. Debug-режим включён.
- Требуется доступ к SQL Server `192.168.1.4` (боевая БД) и драйвер `ODBC Driver 17 for SQL Server`.
- Быстрая проверка соединения: маршрут `/test-db`.
- Для сверки бизнес-цифр использовать существующие `verify_*`/`check_*` скрипты как образец SQL.

### Частые паттерны
- SQL-запросы формируются как f-строки с подстановкой фильтров (`{excluded_filter}`,
  `{group_clause}`, `{area_filter}`) + параметризованные `?` для значений (защита от инъекций).
- В запросах к боевой БД используются хинты `WITH (NOLOCK)` для скорости.
- Ответы API — `jsonify(...)`; фронтенд рисует Chart.js/AG-Grid.

## Зависимости

### Внешние
- Python 3.12, Flask 3.0, pyodbc (SQL Server), python-dotenv, anthropic (Claude API).
- Frontend (CDN): Bootstrap 5.3, Chart.js 4.4, Alpine.js 3.x, HTMX 1.9, AG-Grid, SortableJS 1.15, Font Awesome 6.4.

### Внутренние
- `app_v2.py` — самодостаточен (свой класс `DatabaseConnection`), читает JSON-настройки из корня, рендерит `templates/`.

<!-- MANUAL: Заметки, добавленные вручную ниже этой строки, сохраняются при регенерации -->

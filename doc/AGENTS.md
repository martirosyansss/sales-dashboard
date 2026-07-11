<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-08 | Updated: 2026-07-08 -->

# doc

## Назначение
Документация по базе данных и правила работы с ней.

## ⚠️ Важное предупреждение о расхождении схем
`DATABASE_STRUCTURE.md` и `DATABASE_DIAGRAM.txt` описывают **СТАРУЮ демо-схему** из 4 таблиц
(`Customers`, `Products`, `Sales`, `SaleDetails` с англоязычными PascalCase-колонками), которую
создаёт `../database.py` (`initialize_tables()`) для легаси-приложения `../app.py`.

**Это НЕ боевая схема.** Актуальное приложение `../app_v2.py` работает с production ERP
**AS-Sales Management 7**, где таблицы называются `CUSTOMERS`, `SALES`, `HICUSTOMERSDEBT`,
`HIRESTCUSTOMERSSUM`, `CUSTOMERSALESAREAS`, `SALESAGENTS`, `SArea`, `DOCUMENTS` и т.д. с
колонками-префиксами `f` (`fID`, `fSUM`, `fDBCR`, `fCUSTOMERID`, `fSALESAREA`, `fGROUP`).
Реальную схему см. в `../AGENTS.md` и в SQL-запросах внутри `../app_v2.py`.

## Ключевые файлы
| Файл | Описание |
|------|----------|
| `ВАЖНО_READ_ONLY.md` | Правило: боевая БД — только чтение. Обязательно к соблюдению |
| `DATABASE_STRUCTURE.md` | Подробная документация СТАРОЙ демо-схемы (4 таблицы). Историческая |
| `DATABASE_DIAGRAM.txt` | ERD старой демо-схемы |
| `SQL_SCRIPTS.sql` | Вспомогательные SQL-скрипты (демо-схема) |
| `ФИЛЬТРЫ_ПО_ДАТАМ.md` | Заметки по фильтрации данных по датам |

## Для AI-агентов

### Работа в этом каталоге
- Не полагайся на `DATABASE_STRUCTURE.md` при написании запросов к боевой БД — схема другая.
- Любой SQL против production — **только SELECT** (см. `ВАЖНО_READ_ONLY.md`).
- При обновлении документации фиксируй, что относится к v1-демо, а что — к боевому ERP.

## Зависимости

### Внутренние
- Описывает схему, создаваемую `../database.py` (легаси v1).
- Реальные запросы v2 — в `../app_v2.py`.

<!-- MANUAL: -->

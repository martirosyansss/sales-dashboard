# Документация: Расчёт данных на странице Территорий (Areas)

## Обзор

Страница `/areas` отображает данные по торговым территориям (Sales Areas). Данные загружаются через API endpoint `/api/sales-areas`.

---

## Источники данных

### Основные таблицы базы данных:

| Таблица | Описание |
|---------|----------|
| `TREES` | Справочник территорий (`fTREEID = 'SArea'`) |
| `SALES` | Документы продаж |
| `CUSTOMERS` | Справочник клиентов |
| `CUSTOMERSALESAREAS` | Привязка клиентов к территориям |
| `HICUSTOMERSDEBT` | История движения долгов клиентов |
| `HIRESTCUSTOMERSSUM` | Остатки клиентов (предоплаты Type01, Type02) |
| `DOCUMENTS` | Документы (для связи с долгами) |
| `SALESAGENTAREAS` | Привязка менеджеров к территориям |
| `SALESAGENTS` | Справочник менеджеров |

---

## Формулы расчёта

### 1. ПРОДАЖИ (TotalSales)

**Источник:** таблица `SALES`

```sql
SELECT SUM(s.fTOTALSUM) AS TotalSales
FROM SALES s
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = ?         -- Территория
    AND s.fSALESAREA = ?          -- Территория в документе
    AND s.fDATE >= ? AND s.fDATE <= ?  -- Период
    AND s.fSTATE = 2              -- Проведённые документы
```

**Условия фильтрации:**
- `fSTATE = 2` — только проведённые документы
- Клиент привязан к территории через `CUSTOMERSALESAREAS`
- Территория документа совпадает с территорией клиента

**Дополнительные метрики:**
- `CustomerCount` — количество уникальных клиентов
- `SalesCount` — количество документов продаж
- `AvgSale` — средняя сумма продажи
- `CreditSales` — продажи в кредит (`fPAYTYPE = 2`)

---

### 2. ПЛАТЕЖИ (Payments)

**Источник:** таблица `HICUSTOMERSDEBT`

```sql
SELECT SUM(CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END) AS TotalPayments
FROM HICUSTOMERSDEBT h
INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
INNER JOIN CUSTOMERS c ON d.fCUSTOMERID = c.fID
WHERE d.fSALESAREA = ?            -- Территория документа
    AND h.fDATE >= ? AND h.fDATE <= ?  -- Период
    AND h.fOP = 'PAY'             -- Операция "Платёж"
```

**Условия:**
- `fOP = 'PAY'` — только платёжные операции
- `fDBCR = 'C'` — кредитовые записи (уменьшение долга = приход денег)
- Территория берётся из связанного документа `DOCUMENTS.fSALESAREA`

---

### 3. ДОЛГ (Debt)

**Формула:**
```
Долг = ДолгИзДокументов - |Type01| - |Type02|
```

#### 3.1. Долг из документов (DebtFromDocs)

**Источник:** таблица `HICUSTOMERSDEBT`

```sql
SELECT SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) AS DebtFromDocs
FROM HICUSTOMERSDEBT d
INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = ?          -- Территория клиента
```

**Логика:**
- `fDBCR = 'D'` (Дебет) — увеличение долга (продажа в кредит)
- `fDBCR = 'C'` (Кредит) — уменьшение долга (оплата)

#### 3.2. Предоплаты/Авансы (Type01, Type02)

**Источник:** таблица `HIRESTCUSTOMERSSUM`

```sql
SELECT 
    SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END) AS Type01,
    SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END) AS Type02
FROM HIRESTCUSTOMERSSUM r
INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = ?
```

**Описание типов:**
| Тип | Описание |
|-----|----------|
| `Type01` | Предоплата/Аванс клиента (отрицательное значение = переплата) |
| `Type02` | Дополнительный остаток (обычно отрицательный) |

**Финальный расчёт:**
```python
final_debt = debt_from_docs - abs(type01) - abs(type02)
```

---

### 4. НАЧАЛЬНЫЙ ДОЛГ (InitialDebt)

**Формула:**
```
НачальныйДолг = ТекущийДолг - Продажи + Платежи
```

Это долг на начало выбранного периода.

---

## Схема связей таблиц

```
SALES (продажи)
    ├── fCUSTOMERID → CUSTOMERS.fID
    ├── fSALESAREA → TREES.fCODE (территория)
    └── fSALESAGENTID → SALESAGENTS.fID (менеджер)

HICUSTOMERSDEBT (история долгов)
    ├── fDEBTDOCISN → DOCUMENTS.fISN
    ├── fDBCR = 'D' (дебет/долг)
    ├── fDBCR = 'C' (кредит/оплата)
    └── fOP = 'PAY' (платёжная операция)

HIRESTCUSTOMERSSUM (остатки)
    ├── fCUSTOMERID → CUSTOMERS.fID
    ├── fTYPE = '01' (предоплата)
    └── fTYPE = '02' (доп. остаток)

CUSTOMERSALESAREAS (привязка клиентов к территориям)
    ├── fCUSTOMERID → CUSTOMERS.fID
    └── fSALESAREA → TREES.fCODE
```

---

## Фильтры

### Группы клиентов (groups)
- Фильтр по полю `CUSTOMERS.fGROUP`
- Применяется к: **Долгам**, **Платежам**
- НЕ применяется к продажам (используется `sales_groups`)

### Товарные группы/Дивизионы (divisions)
- Фильтр по `SALESAGENTDIVISIONS.fDIVISION`
- Применяется к: **Продажам**
- НЕ применяется к долгам и платежам

### Группы продаж (sales_groups)
- Отдельный фильтр для продаж по группам клиентов
- Параметр: `sales_groups=002,036`

---

## Пример API запроса

```
GET /api/sales-areas?date_from=2025-11-01&date_to=2025-11-25&groups=002,036
```

### Параметры:

| Параметр | Описание | Пример |
|----------|----------|--------|
| `date_from` | Начало периода | `2025-11-01` |
| `date_to` | Конец периода | `2025-11-25` |
| `groups` | Группы клиентов (для долгов) | `002,036` |
| `sales_groups` | Группы клиентов (для продаж) | `002,036` |
| `divisions` | Товарные группы | `000001,000003` |

---

## Ответ API

```json
{
    "success": true,
    "data": [
        {
            "code": "101",
            "name": "Ереван-Центр",
            "TotalSales": 5000000,
            "CustomerCount": 45,
            "SalesCount": 120,
            "AvgSale": 41666.67,
            "CreditSales": 3500000,
            "Payments": 4200000,
            "Debt": 2300000,
            "InitialDebt": 1500000,
            "PrevMonthSales": 4800000,
            "LastYearSales": 4500000,
            "Managers": [
                {"id": 1, "code": "M001", "name": "Иванов И.И.", "is_default": true}
            ],
            "MonthlyHistory": [
                {"month": "2025-01", "totalSales": 4000000, "totalPayments": 3800000, "totalDebt": 1200000},
                ...
            ]
        }
    ],
    "period": {
        "from": "2025-11-01",
        "to": "2025-11-25"
    }
}
```

---

## Важные замечания

1. **Долг кумулятивный** — не зависит от выбранного периода, показывает актуальный баланс

2. **Платежи за период** — суммируются только за выбранный период

3. **Продажи привязаны** к территории двумя способами:
   - Территория клиента (`CUSTOMERSALESAREAS`)
   - Территория документа (`SALES.fSALESAREA`)
   - Оба условия должны совпадать

4. **Type01 и Type02 вычитаются** из долга, так как это предоплаты клиентов

---

## Логирование

Для территории `105` включено детальное логирование:
```
[AREA 105] debt_from_docs: 10,000,000, type01: -500,000, type02: -200,000, final_debt: 9,300,000
```

---

*Документация создана: 25.11.2025*

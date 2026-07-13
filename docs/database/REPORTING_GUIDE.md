# Руководство по построению отчётов (Sales Dashboard)

> База: **SalesManagement** (боевая ERP AS‑Sales Management 7, MS SQL Server, van‑sales/дистрибуция).
> Режим доступа — **СТРОГО ТОЛЬКО ЧТЕНИЕ**. В этом руководстве все примеры — только `SELECT` с `WITH (NOLOCK)`.
> Никаких `INSERT/UPDATE/DELETE/DDL`. Все запросы ниже извлечены из реальной логики дашборда (`app_v2.py`) и
> из [`DEBT_CALCULATION_FORMULA.md`](../../DEBT_CALCULATION_FORMULA.md).

Документ описывает, как считаются отчётные метрики в дашборде, чтобы новые отчёты давали **те же цифры**, что и UI.

---

## 1. Обзор ключевых таблиц для отчётов

Явных внешних ключей почти нет — связи неявные, по колонкам с префиксом `f...`. Ключевые таблицы:

| Таблица | Назначение | Ключевые колонки | Связи |
|---|---|---|---|
| **SALES** | Шапки документов продаж (реализация). Источник **выручки**. | `fISN` (uniqueidentifier, PK документа), `fDATE` (smalldatetime), `fCUSTOMERID`→CUSTOMERS.fID, `fSALESAGENTID`→SALESAGENTS.fID, `fSALESAREA` (код территории), `fDIVISION`, `fPAYTYPE`, `fTOTALSUM` (money), `fSTATE` (tinyint), `fADDITIONALDISCOUNT`, `fUNDISCOUNTEDSUM` | `fCUSTOMERID`, `fSALESAGENTID`, `fSALESAREA` |
| **SALEDOCDETAILS** | Строки документов продаж (товарные позиции). | `fISN`→SALES.fISN, `fPRODUCTID`→PRODUCTS.fID, `fQUANTITY`, `fPRICE`, `fDISCOUNT`, `fDISCOUNTEDPRICE`, `fSUM`, `fROWNUM` (все суммы — `money`) | `fISN` (к шапке), `fPRODUCTID` |
| **HICUSTOMERSDEBT** | **Регистр движения долгов** (History). Каждая строка — дебет/кредит по документу. Источник **долга** и **платежей**. | `fDATE` (smalldatetime), `fDEBTDOCISN` (uniqueidentifier)→DOCUMENTS.fISN, `fSUM` (money), `fOP` (varchar(3): `PAY` и др.), `fDBCR` (varchar(1): `D`/`C`) | `fDEBTDOCISN`→DOCUMENTS.fISN |
| **HIRESTCUSTOMERSSUM** | **Регистр остатков** (Rest): предоплаты/возвраты по клиентам. | `fCUSTOMERID`→CUSTOMERS.fID, `fTYPE` (varchar(2): `01`/`02`), `fSUM` (money), `fDIVISION` | `fCUSTOMERID` |
| **PAYMENTS** | Документы оплат (шапки). В дашборде платежи обычно берут из HICUSTOMERSDEBT, а не отсюда. | `fISN`, `fDATE`, `fCUSTOMERID`, `fSUM` (money), `fPREPAYMENT`, `fSTATE`, `fSALESAREA` | `fCUSTOMERID`, `fSALESAREA` |
| **CUSTOMERS** | Справочник клиентов (торговых точек). | `fID` (int, PK), `fCODE`, `fNAME`, `fGROUP` (nvarchar(6) — группа клиента), `fREGION` | — |
| **CUSTOMERSALESAREAS** | Привязка клиента к территории продаж (M:N). Основной путь «клиент → территория». | `fCUSTOMERID`→CUSTOMERS.fID, `fSALESAREA`, `fDEFAULT` (bit) | `fCUSTOMERID` |
| **PRODUCTS** | Справочник товаров. | `fID` (int, PK), `fCODE`, `fNAME`, `fGROUP` (товарная группа) | — |
| **SALESAGENTS** | Справочник агентов/менеджеров. | `fID` (int, PK), `fCODE`, `fNAME`, `fCLOSED` (bit) | — |
| **SALESAGENTAREAS** | Привязка агента к территориям. | `fSALESAGENTID`, `fSALESAREA`, `fDEFAULT` | `fSALESAGENTID` |
| **SALESAGENTDIVISIONS** | Привязка агента к дивизионам (для фильтра по товарным группам). | `fSALESAGENTID`, `fDIVISION` | `fSALESAGENTID` |
| **DOCUMENTS** | Общая таблица шапок документов (для связки долг → клиент/территория). | `fISN` (PK), `fDOCTYPE` (tinyint), `fDATE`, `fCUSTOMERID`, `fSALESAREA`, `fSALESAGENTID`, `fSUMM` | — |
| **TREES** | Иерархические справочники. Названия территорий, дивизионов и т.п. | `fTREEID` (например `'SArea'`, `'Division'`), `fCODE`, `fCAPTION`, `fCLOSED` | по `fCODE` |

### Важнейшие связи (запомнить)
- **Продажи → клиент → территория:** `SALES.fCUSTOMERID = CUSTOMERS.fID`, затем `CUSTOMERS.fID = CUSTOMERSALESAREAS.fCUSTOMERID`, фильтр по `CUSTOMERSALESAREAS.fSALESAREA`. У самой SALES есть `fSALESAREA`, но канонический путь территории в дашборде — через `CUSTOMERSALESAREAS`.
- **Долг → клиент/территория:** `HICUSTOMERSDEBT.fDEBTDOCISN = DOCUMENTS.fISN`, затем `DOCUMENTS.fCUSTOMERID = CUSTOMERS.fID` и далее `CUSTOMERSALESAREAS`. Территорию платежей берут из `DOCUMENTS.fSALESAREA`.
- **Название территории:** `TREES` где `fTREEID = 'SArea'`, join по `fCODE = fSALESAREA`.
- **Название дивизиона:** `TREES` где `fTREEID = 'Division'`.

---

## 2. Обязательные фильтры (иначе цифры не сойдутся)

1. **`s.fSTATE = 2`** — только проведённые/подтверждённые продажи. **Каждый** запрос по SALES обязан иметь этот фильтр. Без него в выручку попадут черновики/удалённые.
2. **Исключённые клиенты** — дашборд хранит список «служебных» клиентов, которых надо убрать из аналитики (`load_excluded_customers`). В SQL это `AND c.fID NOT IN (...)`. Хелпер `get_excluded_filter_sql()`.
3. **Группы клиентов** — фильтр `AND c.fGROUP IN (...)` (`CUSTOMERS.fGROUP`).
4. **Дивизионы (товарные группы)** — фильтруются не напрямую, а через агентов:
   ```sql
   AND s.fSALESAGENTID IN (
       SELECT DISTINCT fSALESAGENTID FROM SALESAGENTDIVISIONS WHERE fDIVISION IN (?, ?)
   )
   ```
   Хелпер `get_product_groups_filter_sql()`.

> В коде эти фрагменты подставляются как `{excluded_filter}`, `{product_groups_filter}`, `{group_clause}` с соответствующими параметрами — при написании нового отчёта повторяйте тот же набор.

---

## 3. Типовые рецепты «как посчитать X»

### 3.1. Выручка за период (итог/KPI‑карточка)
Источник: `dashboard_stats()`.
```sql
SELECT ISNULL(SUM(s.fTOTALSUM), 0) AS TotalRevenue
FROM SALES s WITH (NOLOCK)
INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
WHERE s.fDATE >= ?          -- начало периода (включительно)
  AND s.fDATE <  ?          -- конец периода (ИСКЛЮЧИТЕЛЬНО, первый день след. месяца)
  AND s.fSTATE = 2;
```
Сопутствующие метрики того же периода:
- **Кол-во продаж:** `COUNT(s.fISN)`
- **Активные клиенты:** `COUNT(DISTINCT s.fCUSTOMERID)`
- **Средний чек:** `TotalRevenue / SalesCount` (считается в Python, не в SQL, с защитой от деления на 0).

> Прирост (MoM/YoY/10y) считается в Python сравнением двух периодов, а не в SQL.

### 3.2. Продажи по территориям (Areas)
Источник: `get_sales_areas()`. Путь территории — через `CUSTOMERSALESAREAS`:
```sql
SELECT
    COUNT(DISTINCT s.fCUSTOMERID) AS CustomerCount,
    COUNT(s.fISN)                 AS SalesCount,
    ISNULL(SUM(s.fTOTALSUM), 0)   AS TotalSales,
    ISNULL(SUM(CASE WHEN s.fPAYTYPE = 2 THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales,
    ISNULL(AVG(s.fTOTALSUM), 0)   AS AvgSale,
    ISNULL(SUM(d.DiscountAmount), 0) AS TotalDiscount
FROM SALES s WITH (NOLOCK)
INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON c.fID = csa.fCUSTOMERID
OUTER APPLY (
    SELECT SUM(sd.fPRICE * sd.fQUANTITY - sd.fSUM) AS DiscountAmount
    FROM SALEDOCDETAILS sd WITH (NOLOCK)
    WHERE sd.fISN = s.fISN
) d
WHERE csa.fSALESAREA = ?
  AND s.fDATE >= ?
  AND s.fDATE < DATEADD(day, 1, CAST(? AS DATE))   -- date_to включительно
  AND s.fSTATE = 2;
```
- **Сумма скидки** по документу = `Σ(fPRICE * fQUANTITY - fSUM)` по строкам (`SALEDOCDETAILS`).
- **% скидки** = `TotalDiscount / (TotalSales + TotalDiscount) * 100`.
- **Название территории:** `SELECT fCODE, fCAPTION FROM TREES WHERE fTREEID = 'SArea'`.

### 3.3. Продажи по группам клиентов
Источник: `get_groups()`. Группа — `CUSTOMERS.fGROUP`:
```sql
SELECT
    c.fGROUP                    AS GroupCode,
    COUNT(DISTINCT c.fID)       AS CustomerCount,
    COUNT(s.fISN)               AS SalesCount,
    ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
FROM CUSTOMERS c WITH (NOLOCK)
LEFT JOIN SALES s WITH (NOLOCK) ON c.fID = s.fCUSTOMERID
    AND s.fDATE >= ?
    AND s.fSTATE = 2
WHERE c.fGROUP IS NOT NULL AND c.fGROUP <> ''
GROUP BY c.fGROUP
ORDER BY TotalSales DESC;
```
> `LEFT JOIN` + условия периода **в ON** (а не в WHERE) — чтобы группы без продаж тоже попали в результат с нулём.

### 3.4. Продажи по агентам/менеджерам
Источник: `get_managers()`. Обратите внимание — период здесь в `ON` при `LEFT JOIN`:
```sql
SELECT
    sa.fID, sa.fCODE, sa.fNAME, sa.fCLOSED,
    COUNT(DISTINCT s.fCUSTOMERID) AS CustomerCount,
    COUNT(s.fISN)                 AS SalesCount,
    ISNULL(SUM(s.fTOTALSUM), 0)   AS TotalSales,
    ISNULL(AVG(s.fTOTALSUM), 0)   AS AvgSale
FROM SALESAGENTS sa WITH (NOLOCK)
LEFT JOIN SALES s WITH (NOLOCK) ON s.fSALESAGENTID = sa.fID
    AND s.fDATE >= ? AND s.fDATE <= ?
    AND s.fSTATE = 2
LEFT JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
WHERE sa.fCLOSED = 0
GROUP BY sa.fID, sa.fCODE, sa.fNAME, sa.fCLOSED
ORDER BY sa.fNAME;
```
- `sa.fCLOSED = 0` — только действующие агенты.
- Топ‑менеджер периода: тот же join с `TOP 1 ... ORDER BY SUM(s.fTOTALSUM) DESC`.

### 3.5. Дебиторская задолженность (ДОЛГ) — главная формула

> **ДОЛГ = ДЕБЕТ − |Type01| − |Type02|**
> Полностью описано в [`DEBT_CALCULATION_FORMULA.md`](../../DEBT_CALCULATION_FORMULA.md). Это **кумулятивный баланс**, а не сумма за период.

**Шаг 1. Дебет из движения долгов** (`HICUSTOMERSDEBT`, `D` минус `C`):
```sql
SELECT
    csa.fSALESAREA AS area_code,
    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS DebtFromDocs
FROM HICUSTOMERSDEBT d WITH (NOLOCK)
INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON d.fDEBTDOCISN = doc.fISN
INNER JOIN CUSTOMERS c WITH (NOLOCK) ON doc.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = ?
  -- для баланса на дату: AND d.fDATE < ?   (первый день следующего периода)
GROUP BY csa.fSALESAREA;
```

**Шаг 2. Остатки Type01 (возвраты) и Type02 (предоплаты)** (`HIRESTCUSTOMERSSUM`):
```sql
SELECT
    csa.fSALESAREA AS area_code,
    ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) AS Type01,
    ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) AS Type02
FROM HIRESTCUSTOMERSSUM r WITH (NOLOCK)
INNER JOIN CUSTOMERS c WITH (NOLOCK) ON r.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = ?
GROUP BY csa.fSALESAREA;
```

**Шаг 3. Итог (в коде, Python):**
```python
current_debt = debt_from_docs - abs(type01) - abs(type02)
```
- `HIRESTCUSTOMERSSUM` — это **остаток на текущий момент**, истории по датам у него нет. Поэтому для долга «на прошлую дату» (история/YoY) Type01/Type02 не вычитают, а берут только `DebtFromDocs` с `d.fDATE <= ?`.

### 3.6. Платежи от клиентов за период
Источник: `get_sales_areas()` / `get_distributors()`. Платёж = кредитовая операция типа `PAY` в `HICUSTOMERSDEBT`:
```sql
SELECT ISNULL(SUM(CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END), 0) AS TotalPayments
FROM HICUSTOMERSDEBT h WITH (NOLOCK)
INNER JOIN DOCUMENTS d WITH (NOLOCK) ON h.fDEBTDOCISN = d.fISN
INNER JOIN CUSTOMERS c WITH (NOLOCK) ON d.fCUSTOMERID = c.fID
WHERE d.fSALESAREA = ?
  AND h.fDATE >= ? AND h.fDATE <= ?
  AND h.fOP = 'PAY';
```
> Территория платежей берётся из `DOCUMENTS.fSALESAREA` (не через CUSTOMERSALESAREAS). Альтернативно (отчёт дистрибьюторов) платёж = `SUM(ABS(h.fSUM))` при `h.fOP='PAY' AND h.fDBCR='C'`.

### 3.7. Неоплаченные документы (детализация долга по документам)
Источник: `get_unpaid_documents()`. По каждому дебетовому документу вычитаем сумму оплат:
```sql
SELECT
    c.fCODE AS CustomerCode, c.fNAME AS CustomerName,
    debt.fDEBTDOCISN AS DocNumber,
    doc.fDATE        AS DocDate,
    debt.fSUM        AS DocSum,
    ISNULL(payments.PaidAmount, 0) AS PaidAmount,
    debt.fSUM - ISNULL(payments.PaidAmount, 0) AS UnpaidAmount
FROM HICUSTOMERSDEBT debt WITH (NOLOCK)
INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON debt.fDEBTDOCISN = doc.fISN
INNER JOIN CUSTOMERS c WITH (NOLOCK) ON doc.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON c.fID = csa.fCUSTOMERID
OUTER APPLY (
    SELECT SUM(p.fSUM) AS PaidAmount
    FROM HICUSTOMERSDEBT p WITH (NOLOCK)
    WHERE p.fDEBTDOCISN = doc.fISN AND p.fDBCR = 'C'
) payments
WHERE debt.fDBCR = 'D'
  AND csa.fSALESAREA = ?
  AND (debt.fSUM - ISNULL(payments.PaidAmount, 0)) > 0
ORDER BY c.fNAME, doc.fDATE DESC;
```

### 3.8. Помесячная динамика (12/24 месяца)
Источник: `sales_chart()` / история в `get_sales_areas()`. Группировка по `FORMAT(fDATE,'yyyy-MM')`:
```sql
SELECT
    FORMAT(s.fDATE, 'yyyy-MM') AS Month,
    COUNT(s.fISN)              AS SalesCount,
    ISNULL(SUM(s.fTOTALSUM),0) AS TotalSum
FROM SALES s WITH (NOLOCK)
WHERE s.fDATE >= DATEADD(MONTH, -12, GETDATE())
  AND s.fSTATE = 2
GROUP BY FORMAT(s.fDATE, 'yyyy-MM')
ORDER BY Month;
```
**История долга по месяцам** строится иначе (кумулятивно): берут стартовый баланс `d.fDATE < начало_истории`, затем помесячные изменения `SUM(D − C)` и накапливают — потому что долг это баланс, а не оборот (см. п. 4.2 и `DEBT_CALCULATION_FORMULA.md`).

### 3.9. Динамика за 10 лет (тот же месяц)
Источник: `ten_years_chart()`. Цикл по годам в Python, для каждого — узкий запрос по месяцу:
```sql
SELECT COUNT(*) AS SalesCount, ISNULL(SUM(fTOTALSUM),0) AS TotalSales
FROM SALES WITH (NOLOCK)
WHERE fDATE >= ? AND fDATE < ?   -- [начало месяца; начало следующего)
  AND fSTATE = 2;
```

### 3.10. Сезонность (коэффициенты по месяцам)
Источник: `get_area_seasonality()` / `generate_plans()`. Берут 24 месяца, суммируют по номеру месяца:
```sql
SELECT
    csa.fSALESAREA AS area_code,
    MONTH(s.fDATE) AS month,
    SUM(s.fTOTALSUM) AS sales
FROM SALES s WITH (NOLOCK)
INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON c.fID = csa.fCUSTOMERID
WHERE s.fDATE >= DATEADD(MONTH, -24, GETDATE())
  AND s.fSTATE = 2
GROUP BY csa.fSALESAREA, MONTH(s.fDATE);
```
Коэффициент месяца (в Python): `coeff = (sales_месяца * 12) / total_sales_area`. Значение `1.0` = «средний месяц», `>1` — высокий сезон, `<1` — низкий. Есть глобальный дефолт (июль пик `1.49`, январь дно `0.53`), используемый если у территории нет своих данных.

### 3.11. Планы продаж и кредитов
Источник: `generate_plans()`. Алгоритм:
1. **Средние месячные продажи** за 12 мес: `SUM(s.fTOTALSUM) / 12.0` по территории (тот же join через `CUSTOMERSALESAREAS`, `fSTATE = 2`).
2. **Индивидуальная сезонность** территории (п. 3.10).
3. **План продаж** месяца = `avg_monthly_sales * seasonality[target_month] * (1 + growth%)`.
4. **Средний долг** за 12 мес: текущий баланс (п. 3.5) + помесячные изменения `SUM(D − C)` из `HICUSTOMERSDEBT` (за `DATEADD(MONTH,-13,GETDATE())`), минус `|Type01|`, `|Type02|`.

Отдельные фильтры групп: `groups` — для продаж, `debt_groups` — для долгов (если не заданы, для долга берут `groups`).

### 3.12. Покупки клиента с товарными позициями
Источник: `customer_purchases()`. Шапки → строки → товары:
```sql
-- Шапки продаж клиента
SELECT s.fISN AS SaleId, s.fDATE, s.fTOTALSUM, s.fPAYTYPE, s.fSALESAREA,
       sa.fCODE AS ManagerCode, sa.fNAME AS ManagerName
FROM SALES s WITH (NOLOCK)
INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
LEFT JOIN SALESAGENTS sa WITH (NOLOCK) ON s.fSALESAGENTID = sa.fID
WHERE s.fCUSTOMERID = ? AND s.fSTATE = 2
  AND s.fDATE >= ? AND s.fDATE <= ?
ORDER BY s.fDATE DESC, s.fISN DESC;

-- Строки конкретного документа (по fISN)
SELECT sd.fROWNUM, p.fCODE AS ProductCode, p.fNAME AS ProductName,
       sd.fQUANTITY, sd.fPRICE AS OriginalPrice, sd.fDISCOUNT,
       sd.fDISCOUNTEDPRICE AS Price, sd.fSUM AS LineTotal
FROM SALEDOCDETAILS sd WITH (NOLOCK)
LEFT JOIN PRODUCTS p WITH (NOLOCK) ON sd.fPRODUCTID = p.fID
WHERE sd.fISN = ?
ORDER BY sd.fROWNUM;
```
Типы оплаты `fPAYTYPE`: `1`=наличные, `2`=банк, `3`=кредит/долг, `5`=прочее, `6`=смешанный.

### 3.13. Дневные продажи
Источник: `reports_daily_sales()`:
```sql
SELECT CAST(fDATE AS DATE) AS SaleDate,
       COALESCE(SUM(fTOTALSUM), 0) AS TotalSales,
       COUNT(*) AS SalesCount
FROM SALES WITH (NOLOCK)
WHERE fDATE >= ? AND fDATE <= ? AND fSTATE = 2
GROUP BY CAST(fDATE AS DATE)
ORDER BY SaleDate;
```

### 3.14. Выручка‑нетто = продажи − возвраты (кросс‑доменный)
Возвраты лежат в отдельной шапке `RETURNS` (домен «Заказы, возвраты и резервирование»), с той же конвенцией `fSTATE = 2` и `fTOTALSUM`. Чистая выручка за период:
```sql
SELECT
    (SELECT ISNULL(SUM(s.fTOTALSUM),0) FROM SALES s WITH (NOLOCK)
       WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ?)  AS GrossRevenue,
    (SELECT ISNULL(SUM(r.fTOTALSUM),0) FROM RETURNS r WITH (NOLOCK)
       WHERE r.fSTATE = 2 AND r.fDATE >= ? AND r.fDATE < ?)  AS ReturnsSum;
-- NetRevenue = GrossRevenue - ReturnsSum (считается в Python)
```
> Проверено на боевой БД: за Q1‑2025 GrossRevenue ≈ 121.65 млн, ReturnsSum ≈ 0.86 млн, NetRevenue ≈ 120.79 млн. Оба фильтра `fSTATE=2` и полуинтервал дат обязательны (см. п. 4.1, 4.4).

### 3.15. Оборот покупателей = отчёт ERP «Գնորդների շրջանառություն» (выверено до копейки)
Проверено на живом отчёте ERP (дивизион 000000, группа 200, зона 101, июль-2026). Соответствие колонок:

| Колонка ERP | Источник | Нюанс |
|---|---|---|
| Վաճառք (продажи) | `SALES` (fSTATE=2, `s.fDIVISION`) | в регистре это D-движения `fOP='RLZ'` |
| Վճարում (оплаты) | **`PAYMENTS` (документы, fSTATE=2, `p.fDIVISION`)** | НЕ регистр `PAY/C`! В регистр попадает только часть, закрывающая долг; излишек уходит в переплату Type02 |
| Վաճառքի վերադարձ (возвраты) | `RETURNS` (fSTATE=2, `rt.fDIVISION`) | в регистр НЕ пишутся — оседают остатком Type01 |
| Մնացորդ (остаток на дату) | `SUM(D−C по HICUSTOMERSDEBT, fDATE<дата)` − \|Type01\| − \|Type02\| | полная формула долга; `HIRESTCUSTOMERSSUM` имеет `fDIVISION` — фильтруется |

Обязательно: **фильтр групп — иерархический**: узел «200» — родитель, клиенты хранят коды подгрупп →
`c.fGROUP IN (SELECT fCODE FROM TREES WHERE fTREEID='CustGrp' AND (fCODE='200' OR fPARENT='200'))`.
Плоское `fGROUP='200'` даёт 0 строк. Дивизион у балансов берётся с документа (`DOCUMENTS.fDIVISION`).

---

## 4. Подводные камни

### 4.1. Всегда фильтруйте `fSTATE = 2`
Без него в SALES попадают черновики/отменённые → завышенная выручка. Это главная причина расхождений с UI.

### 4.2. Регистры HI... — это движения/остатки, а не «сумма за месяц»
- **`HICUSTOMERSDEBT`** — регистр **движения**. Долг = **накопленный баланс** `SUM(D − C)` с начала времён до даты (`d.fDATE < конец_периода`). Нельзя брать `D − C` только за месяц и называть это «долгом» — это лишь *изменение* долга за месяц.
- **`HIRESTCUSTOMERSSUM`** — регистр **остатка** на текущий момент. Истории по датам нет → для долга на прошлую дату Type01/Type02 не применяют.
- Для графика долга накапливают кумулятив: последняя точка графика обязана равняться значению карточки долга.

### 4.3. Полная формула долга, а не просто дебет
`ДОЛГ = DebtFromDocs − |Type01| − |Type02|`. Забыть вычесть возвраты/предоплаты — типовая ошибка (см. `DEBT_CALCULATION_FORMULA.md`, раздел «Частые ошибки»).

### 4.4. Границы периода: `< следующий_день`, не `<= дата`
`smalldatetime` содержит время. `fDATE <= '2025-10-31'` теряет продажи, сделанные 31‑го после полуночи. В дашборде применяют `s.fDATE < DATEADD(day, 1, CAST(? AS DATE))` либо `fDATE < первый_день_след_месяца`. KPI‑карточки используют полуинтервал `>= начало AND < конец`.

### 4.5. Путь «клиент → территория» — через CUSTOMERSALESAREAS
Канонический путь территории для продаж/долга — `CUSTOMERS → CUSTOMERSALESAREAS.fSALESAREA`, **не** `SALES.fSALESAREA`. У клиента может быть несколько территорий (`fDEFAULT` помечает основную) — при join без учёта этого возможно дублирование строк по одному клиенту. Платежи же территорию берут из `DOCUMENTS.fSALESAREA`.

### 4.6. Долг связывают через DOCUMENTS, но НЕ для продаж
Для долга: `HICUSTOMERSDEBT.fDEBTDOCISN → DOCUMENTS.fISN → CUSTOMERS`. При этом соединять `HICUSTOMERSDEBT` с `DOCUMENTS` по `fCUSTOMER = fCODE` (строкой) — медленно и неверно; используйте `doc.fCUSTOMERID = c.fID`.

### 4.7. Типы `money` → приводите к float в Python
Все суммы (`fTOTALSUM`, `fSUM`, `fPRICE`, ...) имеют тип `money`/`decimal`. Драйвер отдаёт `Decimal`. Средние/проценты/деления считайте в Python, обязательно защищая от деления на 0 (в SQL для этого `ISNULL(...,0)`, `NULLIF`).

### 4.8. Демо‑таблицы `dbo.Sales/Customers/Products/SaleDetails` — НЕ боевые
В БД есть коллизия регистра: `dbo.SALES` (боевая, колонки `f...`, 370 572 строк) и `dbo.Sales` (демо, колонки `SaleID/TotalAmount/...`, 0 строк). Аналогично `CUSTOMERS`/`Customers`, `PRODUCTS`/`Products`. Строить отчёты можно **только** по боевым UPPERCASE‑таблицам. `doc/DATABASE_STRUCTURE.md` описывает выдуманную демо‑схему и к боевым отчётам отношения не имеет.
> Примечание по артефактам схемы: из‑за регистронезависимости ФС Windows файлы `docs/database/schema/tables/dbo.SALES.json`, `dbo.CUSTOMERS.json`, `dbo.PRODUCTS.json` изначально перезаписывались демо‑версией. Это **исправлено**: канонические файлы теперь содержат боевые таблицы, а демо вынесены в `dbo.Sales__legacydemo.json` и т.п. Эталон в любом случае — `schema_raw.json`.

### 4.9. `NOLOCK` обязателен
БД боевая, с активными пользователями. Все читающие запросы — с `WITH (NOLOCK)`, чтобы не блокировать ERP. (Помните про риск «грязного чтения» — для аналитики приемлемо.)

### 4.10. Фильтр дивизионов — косвенный
Товарные группы (`fDIVISION`) фильтруют через агентов (`SALESAGENTDIVISIONS`), а не по строкам продаж напрямую. Прямой `s.fDIVISION IN (...)` применяется только в отчёте дистрибьюторов (`get_distributors()`), где логика другая.

### 4.11. `LEFT JOIN` + период в `ON`
Чтобы показать сущности без продаж (агенты/группы с нулём), условия периода и `fSTATE=2` кладут в `ON` соединения с SALES, а не в `WHERE`. Перенос их в `WHERE` превращает `LEFT JOIN` в `INNER` и «теряет» пустые строки.

### 4.12. Исключённые клиенты и настройки — из JSON, не из БД
Списки исключённых клиентов, назначения групп менеджерам, выбранные территории/дивизионы хранятся в JSON‑файлах в корне проекта (`*_FILE` константы) и подставляются в SQL как `IN (...)`. Новый отчёт должен применять те же фильтры (`get_excluded_filter_sql()` и т.п.), иначе цифры разойдутся с UI.

---

## 5. Быстрый чек‑лист перед запуском нового отчёта
- [ ] `s.fSTATE = 2` для всех запросов по SALES.
- [ ] Границы периода полуинтервалом (`>= start AND < end_next_day`).
- [ ] Территория продаж/долга — через `CUSTOMERSALESAREAS` (продажи) / `DOCUMENTS.fSALESAREA` (платежи).
- [ ] Долг = `DebtFromDocs − |Type01| − |Type02|`, кумулятивно.
- [ ] Применены фильтры: исключённые клиенты, группы, дивизионы (через агентов).
- [ ] Все `money` приведены к float, деления защищены от нуля.
- [ ] Только боевые UPPERCASE‑таблицы, `WITH (NOLOCK)`.
- [ ] Никаких операций записи.

---
*Источники истины:* `app_v2.py` (эндпоинты `/api/dashboard/stats`, `/api/sales-areas`, `/api/managers`, `/api/groups`, `/api/generate-plans`, `/api/area-seasonality`, `/api/distributors`, `/api/reports/daily-sales`), [`DEBT_CALCULATION_FORMULA.md`](../../DEBT_CALCULATION_FORMULA.md), `docs/database/schema/schema_raw.json`.

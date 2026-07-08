# ФОРМУЛА РАСЧЕТА ДОЛГА (DEBT CALCULATION FORMULA)

## ⚠️ ВАЖНО: Правильный метод расчета долга

### Основная формула:
```
ДОЛГ = ДЕБЕТ - |Type01| - |Type02|
```

### Компоненты формулы:

#### 1. ДЕБЕТ (Debit from HICUSTOMERSDEBT)
```sql
SELECT 
    SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as total_debt
FROM HICUSTOMERSDEBT d
WHERE d.fDATE < @target_date
```
- **fDBCR = 'D'** → Дебет (добавляем к долгу)
- **fDBCR = 'C'** → Кредит (вычитаем из долга)
- **Важно**: Используется кумулятивный баланс на конец периода (`d.fDATE < target_date`)

#### 2. Type01 - ВОЗВРАТЫ (RETURN)
```sql
SELECT 
    SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END) as Type01
FROM HIRESTCUSTOMERSSUM r
```
- **Таблица**: HIRESTCUSTOMERSSUM
- **Связь**: r.fCUSTOMERID → CUSTOMERS.fID
- **Назначение**: Возвращенные товары клиентом
- **Действие**: Вычитаем абсолютное значение из долга

#### 3. Type02 - ПРЕДОПЛАТА/ԿԱՆԽԱՎՃԱՐ (Prepayment)
```sql
SELECT 
    SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END) as Type02
FROM HIRESTCUSTOMERSSUM r
```
- **Таблица**: HIRESTCUSTOMERSSUM
- **Связь**: r.fCUSTOMERID → CUSTOMERS.fID
- **Назначение**: Предоплаты/авансы от клиента
- **Действие**: Вычитаем абсолютное значение из долга

---

## Структура таблиц

### HICUSTOMERSDEBT
```
Колонки: fDEBTDOCISN, fCUSTOMERID, fDBCR, fSUM, fDATE, ...
```
- **fDBCR**: 'D' = Debit (дебет), 'C' = Credit (кредит)
- **fDATE**: Дата транзакции долга
- **fSUM**: Сумма транзакции

### HIRESTCUSTOMERSSUM
```
Колонки: fDIVISION, fCUSTOMERID, fTYPE, fSUM
```
- **fTYPE**: '01' = Возвраты, '02' = Предоплата
- **fSUM**: Сумма остатка

### CUSTOMERS
```
Связующая таблица через fID
```

### CUSTOMERSALESAREAS
```
Связь клиентов с территориями продаж
Колонки: fCUSTOMERID, fSALESAREA, fGROUP
```

---

## Примеры расчета

### Пример 1: Area 105, Groups 002+036 (Октябрь 2025)
```
ДЕБЕТ (из HICUSTOMERSDEBT):     2,593,250.47
Type01 (Возвраты):                 -75,317.95
Type02 (Предоплата):              -108,644.86
───────────────────────────────────────────
ДОЛГ = 2,593,250.47 - |-75,317.95| - |-108,644.86|
ДОЛГ = 2,593,250.47 - 75,317.95 - 108,644.86
ДОЛГ = 2,409,287.66
```

---

## Реализация в коде (app_v2.py)

### 1. В `/api/generate-plans` (строки ~2095-2130)
```python
# Получить долг из HICUSTOMERSDEBT
query_debt = f"""
SELECT 
    csa.fSALESAREA as area_code,
    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as total_debt
FROM HICUSTOMERSDEBT d WITH (NOLOCK)
INNER JOIN CUSTOMERS c WITH (NOLOCK) ON d.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON c.fID = csa.fCUSTOMERID
WHERE d.fDATE < ?
    {excluded_filter}
    {group_clause}
GROUP BY csa.fSALESAREA
"""

# Получить Type01 и Type02
query_rest = f"""
SELECT 
    csa.fSALESAREA as area_code,
    ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
    ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
FROM HIRESTCUSTOMERSSUM r
INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE 1=1
    {excluded_filter}
    {group_clause}
GROUP BY csa.fSALESAREA
"""

# Применяем формулу
debt_from_docs = stats['total_debt']
type01 = stats['type01']
type02 = stats['type02']
current_debt = debt_from_docs - abs(type01) - abs(type02)
```

### 2. В `/api/sales-areas` - История долга (строки ~1059-1080)
```python
# ВАЖНО: Используем кумулятивный баланс, а не транзакции за месяц
WITH MonthList AS (
    SELECT CAST(? AS DATE) as month_start
    UNION ALL
    SELECT DATEADD(MONTH, 1, month_start)
    FROM MonthList
    WHERE month_start < ?
)
SELECT 
    FORMAT(ml.month_start, 'yyyy-MM') as month,
    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as debt
FROM MonthList ml
LEFT JOIN HICUSTOMERSDEBT d ON d.fDATE < DATEADD(MONTH, 1, ml.month_start)
LEFT JOIN CUSTOMERS c ON d.fCUSTOMERID = c.fID
LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA IN (...)
GROUP BY ml.month_start
```

---

## ❗ Частые ошибки

### ❌ НЕПРАВИЛЬНО:
1. **Использование DOCUMENTS вместо прямой связи**
   ```sql
   -- МЕДЛЕННО И НЕПРАВИЛЬНО
   FROM HICUSTOMERSDEBT d
   INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
   INNER JOIN CUSTOMERS c ON doc.fCUSTOMER = c.fCODE
   ```

2. **Группировка по месяцу транзакции вместо кумулятивного баланса**
   ```sql
   -- НЕПРАВИЛЬНО - показывает изменения ЗА месяц
   FORMAT(d.fDATE, 'yyyy-MM') as month
   ```

3. **Забыть вычесть Type01 и Type02**
   ```python
   # НЕПРАВИЛЬНО
   current_debt = debt_from_docs
   ```

### ✅ ПРАВИЛЬНО:
1. **Прямая связь через fCUSTOMERID**
   ```sql
   FROM HICUSTOMERSDEBT d
   INNER JOIN CUSTOMERS c ON d.fCUSTOMERID = c.fID
   ```

2. **Кумулятивный баланс до конца периода**
   ```sql
   WHERE d.fDATE < DATEADD(MONTH, 1, @period_start)
   ```

3. **Полная формула с вычетами**
   ```python
   current_debt = debt_from_docs - abs(type01) - abs(type02)
   ```

---

## Проверка правильности

### Тестовый запрос для Area 105, Groups 002+036:
```sql
-- 1. Дебет
SELECT 
    SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as Debet
FROM HICUSTOMERSDEBT d
INNER JOIN CUSTOMERS c ON d.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = '105'
AND csa.fGROUP IN ('002', '036')
AND d.fDATE < '2025-11-01'

-- 2. Type01 и Type02
SELECT 
    SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END) as Type01,
    SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END) as Type02
FROM HIRESTCUSTOMERSSUM r
INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = '105'
AND csa.fGROUP IN ('002', '036')

-- 3. Итоговый долг
-- ДОЛГ = Debet - |Type01| - |Type02|
```

---

## История изменений
- **27.11.2025**: Добавлен расчёт долга для Dashboard Builder (карточки и графики)
- **27.11.2025**: Графики долга показывают накопленный (кумулятивный) долг
- **22.11.2025**: Реализована полная формула с Type01/Type02
- **22.11.2025**: Исправлена история долга с транзакционной на кумулятивную
- **22.11.2025**: Оптимизирован запрос долга (убран JOIN через DOCUMENTS)
- **22.11.2025**: Добавлены NOLOCK hints для ускорения запросов

---

## Dashboard Builder - Расчёт долга

### Карточка долга (`/api/dashboard-builder/card-data?metric=total_debt`)

```python
# Используем 3 отдельных подзапроса:

# 1. DebtFromDocs из HICUSTOMERSDEBT
debt_query = """
    SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0)
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE 1=1
    {area_filter}  -- AND csa.fSALESAREA IN (...)
    {group_filter} -- AND c.fGROUP IN (...)
"""

# 2. Type01 из HIRESTCUSTOMERSSUM
type01_query = """
    SELECT ISNULL(SUM(r.fSUM), 0)
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE r.fTYPE = '01'
    {area_filter}
    {group_filter}
"""

# 3. Type02 из HIRESTCUSTOMERSSUM  
type02_query = """
    SELECT ISNULL(SUM(r.fSUM), 0)
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE r.fTYPE = '02'
    {area_filter}
    {group_filter}
"""

# Итоговый долг
total_debt = debt_from_docs - type01 - type02
```

### График долга (`/api/dashboard-builder/chart-data?metrics=debt`)

Для графика используем **НАКОПЛЕННЫЙ (кумулятивный)** подход:

```python
# 1. Базовый долг ДО начала периода
base_debt_query = """
    SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0)
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE d.fDATE < @period_start  -- ДО начала периода!
    {area_filter}
    {group_filter}
"""

# 2. Type01 и Type02 (остатки, не зависят от даты)
rest_query = """
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE 1=1
    {area_filter}
    {group_filter}
"""

# 3. Изменения долга по дням в периоде
daily_changes_query = """
    SELECT 
        CONVERT(VARCHAR(10), d.fDATE, 120) as period_label,
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as value
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE d.fDATE BETWEEN @start AND @end
    {area_filter}
    {group_filter}
    GROUP BY CONVERT(VARCHAR(10), d.fDATE, 120)
    ORDER BY period_label
"""

# 4. Вычисляем кумулятивный долг для каждого дня
cumulative_debt = base_debt - abs(type01) - abs(type02)
chart_values = []

for day, daily_change in daily_changes:
    cumulative_debt += daily_change
    chart_values.append(cumulative_debt)

# Последнее значение на графике = текущий итоговый долг
```

### Важно для графиков:
- ✅ Показываем **накопленный** долг, а не изменения за день
- ✅ Последняя точка графика = значению карточки долга
- ✅ График показывает динамику роста/уменьшения долга
- ❌ НЕ показываем просто D-C за каждый день (это было бы изменение, не баланс)

---

## Контакты для вопросов
- Файл: app_v2.py
- Эндпоинты: `/api/generate-plans`, `/api/sales-areas`
- Проверочные скрипты: `verify_debt_formula.py`, `check_history_debt_method.py`

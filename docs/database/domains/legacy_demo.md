# Legacy/демо-таблицы

Домен объединяет четыре демонстрационные таблицы в mixed-case (`Customers`, `Products`, `Sales`, `SaleDetails`), которые представляют собой упрощённую учебную модель «клиенты → заказы → строки заказа → товары». Это НЕ боевые таблицы ERP AS-Sales Management: реальная van-sales-логика работает через таблицы в UPPER-CASE (`CUSTOMERS`, `PRODUCTS`, `SALEDOCDETAILS`, регистры `HI...`/`HIREST...`). Все четыре таблицы пусты (`row_count = 0`) и на момент документирования обращения к ним встречаются только в легаси-модуле `app.py` (эндпоинты каталога товаров, списка клиентов и демо-рекомендаций); актуальный `app_v2.py` их не использует. Схема классическая реляционная с явными внешними ключами (в отличие от боевой ERP, где связи неявные через `fISN`/`fID`).

## dbo.Customers  (0 строк)

- Назначение: демо-справочник клиентов с базовой контактной информацией; родительская таблица для `dbo.Sales`.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| CustomerID | int | NOT NULL | Первичный ключ клиента (идентити/суррогат) |
| CustomerName | nvarchar(100) | NOT NULL | Наименование/имя клиента |
| Email | nvarchar(100) | NULL | Электронная почта клиента |
| Phone | nvarchar(20) | NULL | Телефон клиента |
| Address | nvarchar(255) | NULL | Почтовый адрес |
| City | nvarchar(50) | NULL | Город |
| Country | nvarchar(50) | NULL | Страна |
| CreatedDate | datetime | NULL | Дата создания записи (default `getdate()`) |

- Ключи и связи: PK `CustomerID` (кластерный индекс `PK__Customer__A4AE64B8DD986889`). Внешних ключей нет. На таблицу ссылается `dbo.Sales.CustomerID` (referenced_by).

## dbo.Products  (0 строк)

- Назначение: демо-справочник товаров с ценой, категорией и складским остатком; родительская таблица для строк заказа `dbo.SaleDetails`.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| ProductID | int | NOT NULL | Первичный ключ товара |
| ProductName | nvarchar(100) | NOT NULL | Наименование товара |
| Category | nvarchar(50) | NULL | Категория товара |
| UnitPrice | decimal(10,2) | NOT NULL | Цена за единицу |
| StockQuantity | int | NULL | Остаток на складе (default `0`) |
| Description | nvarchar(500) | NULL | Описание товара |
| CreatedDate | datetime | NULL | Дата создания записи (default `getdate()`) |

- Ключи и связи: PK `ProductID` (кластерный индекс `PK__Products__B40CC6ED0932F1C5`). Внешних ключей нет. На таблицу ссылается `dbo.SaleDetails.ProductID` (referenced_by).

## dbo.Sales  (0 строк)

- Назначение: демо-заказы (шапки продаж) с итоговой суммой и статусом; связывает клиента с набором строк заказа. В коде `app.py` фильтруется по `Status='Completed'` при расчёте выручки.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| SaleID | int | NOT NULL | Первичный ключ заказа |
| CustomerID | int | NULL | Клиент заказа → `dbo.Customers.CustomerID` |
| SaleDate | datetime | NULL | Дата продажи (default `getdate()`) |
| TotalAmount | decimal(10,2) | NOT NULL | Итоговая сумма заказа |
| Status | nvarchar(20) | NULL | Статус заказа (default `'Completed'`) |
| Notes | nvarchar(500) | NULL | Произвольные примечания |

- Ключи и связи: PK `SaleID` (кластерный индекс `PK__Sales__1EE3C41FD3D12905`). FK `CustomerID → dbo.Customers.CustomerID` (`FK__Sales__CustomerI__396FF562`). На таблицу ссылается `dbo.SaleDetails.SaleID` (referenced_by).

## dbo.SaleDetails  (0 строк)

- Назначение: демо-строки заказа (позиции продаж) — количество, цена и подытог по каждому товару в рамках заказа; дочерняя таблица `dbo.Sales` и `dbo.Products`.
- Таблица колонок:

| Колонка | Тип | Null | Назначение |
|---|---|---|---|
| SaleDetailID | int | NOT NULL | Первичный ключ строки заказа |
| SaleID | int | NULL | Заказ-владелец → `dbo.Sales.SaleID` |
| ProductID | int | NULL | Товар позиции → `dbo.Products.ProductID` |
| Quantity | int | NOT NULL | Количество единиц |
| UnitPrice | decimal(10,2) | NOT NULL | Цена за единицу в строке |
| Subtotal | decimal(10,2) | NOT NULL | Подытог по строке (Quantity × UnitPrice) |

- Ключи и связи: PK `SaleDetailID` (кластерный индекс `PK__SaleDeta__70DB141E09D8B983`). FK `SaleID → dbo.Sales.SaleID` (`FK__SaleDetai__SaleI__3E34AA7F`) и FK `ProductID → dbo.Products.ProductID` (`FK__SaleDetai__Produ__3F28CEB8`). Referenced_by отсутствует.

## Связи домена

Классическая нормализованная схема «звезда заказов» с явными внешними ключами:

```
Customers (1) ──< Sales (1) ──< SaleDetails >── (1) Products
   CustomerID       SaleID          SaleID/ProductID     ProductID
```

- `Sales.CustomerID → Customers.CustomerID` — каждый заказ принадлежит одному клиенту.
- `SaleDetails.SaleID → Sales.SaleID` — строки заказа принадлежат одной шапке продажи.
- `SaleDetails.ProductID → Products.ProductID` — каждая строка ссылается на один товар.

Связей с соседними (боевыми) доменами НЕТ: демо-таблицы полностью изолированы от реальной ERP-модели. Они не связаны с UPPER-CASE `CUSTOMERS`/`PRODUCTS`, документами `SALEDOCDETAILS`/`DOCUMENTS` или регистрами долга `HICUSTOMERSDEBT`/`HIRESTCUSTOMERSSUM`. Совпадение имён (`Customers` vs `CUSTOMERS`, `Products` vs `PRODUCTS`, `Sales` vs `SALES`) чисто номинальное и обусловлено учебным происхождением демо-набора; при выборке важно учитывать регистр, чтобы не спутать демо-таблицу с боевой.

## Примеры отчётных запросов

Запросы приведены по реальным колонкам демо-таблиц (все — только на чтение). Учтите, что таблицы пусты, поэтому запросы вернут 0 строк до наполнения данными.

### 1. Каталог товаров с суммарными продажами (по мотивам `app.py`)

```sql
SELECT
    p.ProductID,
    p.ProductName,
    p.Category,
    p.UnitPrice,
    p.StockQuantity,
    ISNULL(SUM(sd.Quantity), 0) AS TotalSold
FROM Products p
LEFT JOIN SaleDetails sd ON p.ProductID = sd.ProductID
LEFT JOIN Sales s ON sd.SaleID = s.SaleID AND s.Status = 'Completed'
GROUP BY p.ProductID, p.ProductName, p.Category, p.UnitPrice, p.StockQuantity
ORDER BY TotalSold DESC;
```

### 2. Выручка по клиентам (только завершённые заказы)

```sql
SELECT
    c.CustomerID,
    c.CustomerName,
    COUNT(s.SaleID)      AS OrdersCount,
    ISNULL(SUM(s.TotalAmount), 0) AS Revenue
FROM Customers c
LEFT JOIN Sales s ON c.CustomerID = s.CustomerID AND s.Status = 'Completed'
GROUP BY c.CustomerID, c.CustomerName
ORDER BY Revenue DESC;
```

### 3. Клиенты без покупок (по мотивам демо-рекомендаций `app.py`)

```sql
SELECT c.CustomerID, c.CustomerName, c.Email, c.City
FROM Customers c
LEFT JOIN Sales s ON c.CustomerID = s.CustomerID
WHERE s.SaleID IS NULL;
```

### 4. Детализация заказов: клиент → заказ → позиции → товар

```sql
SELECT
    s.SaleID,
    s.SaleDate,
    c.CustomerName,
    p.ProductName,
    sd.Quantity,
    sd.UnitPrice,
    sd.Subtotal
FROM Sales s
INNER JOIN Customers c   ON s.CustomerID = c.CustomerID
INNER JOIN SaleDetails sd ON sd.SaleID = s.SaleID
INNER JOIN Products p     ON sd.ProductID = p.ProductID
WHERE s.Status = 'Completed'
ORDER BY s.SaleDate DESC, s.SaleID;
```


---

## См. также
- [← Индекс документации БД](../README.md)
- [Руководство по отчётам (обязательные фильтры, готовые SELECT)](../REPORTING_GUIDE.md)

# ПОЛНАЯ ДОКУМЕНТАЦИЯ СТРУКТУРЫ БАЗЫ ДАННЫХ
## Sales Management System Database

**Дата создания документа:** 13 ноября 2025  
**Имя базы данных:** `SalesManagement`  
**Сервер:** localhost  
**СУБД:** Microsoft SQL Server

---

## СОДЕРЖАНИЕ

1. [Обзор базы данных](#обзор-базы-данных)
2. [Диаграмма связей (ERD)](#диаграмма-связей-erd)
3. [Детальное описание таблиц](#детальное-описание-таблиц)
   - [Customers (Клиенты)](#1-customers-клиенты)
   - [Products (Продукты)](#2-products-продукты)
   - [Sales (Продажи)](#3-sales-продажи)
   - [SaleDetails (Детали продаж)](#4-saledetails-детали-продаж)
4. [Связи между таблицами](#связи-между-таблицами)
5. [Индексы и ограничения](#индексы-и-ограничения)
6. [Бизнес-правила](#бизнес-правила)
7. [Триггеры и хранимые процедуры](#триггеры-и-хранимые-процедуры)

---

## ОБЗОР БАЗЫ ДАННЫХ

База данных **SalesManagement** предназначена для управления продажами, включая:
- Управление клиентами (CRM функциональность)
- Управление каталогом продуктов и складскими запасами
- Обработка транзакций продаж
- Формирование отчетов по продажам

### Архитектура базы данных

- **Количество таблиц:** 4
- **Тип связей:** Иерархические (One-to-Many)
- **Целостность данных:** Обеспечивается через PRIMARY KEY и FOREIGN KEY ограничения
- **Каскадное удаление:** Реализовано для SaleDetails

---

## ДИАГРАММА СВЯЗЕЙ (ERD)

```
┌─────────────────────────┐
│      CUSTOMERS          │
│─────────────────────────│
│ * CustomerID (PK)       │
│   CustomerName          │
│   Email                 │
│   Phone                 │
│   Address               │
│   City                  │
│   Country               │
│   CreatedDate           │
└────────────┬────────────┘
             │ 1
             │
             │ N
┌────────────▼────────────┐         ┌─────────────────────────┐
│       SALES             │         │      PRODUCTS           │
│─────────────────────────│         │─────────────────────────│
│ * SaleID (PK)           │         │ * ProductID (PK)        │
│ # CustomerID (FK)       │         │   ProductName           │
│   SaleDate              │         │   Category              │
│   TotalAmount           │         │   UnitPrice             │
│   Status                │         │   StockQuantity         │
│   Notes                 │         │   Description           │
└────────────┬────────────┘         │   CreatedDate           │
             │ 1                    └────────────┬────────────┘
             │                                   │ 1
             │ N                                 │
┌────────────▼────────────┐                     │ N
│    SALEDETAILS          │                     │
│─────────────────────────│◄────────────────────┘
│ * SaleDetailID (PK)     │
│ # SaleID (FK)           │
│ # ProductID (FK)        │
│   Quantity              │
│   UnitPrice             │
│   Subtotal              │
└─────────────────────────┘

ЛЕГЕНДА:
* = PRIMARY KEY
# = FOREIGN KEY
1 = Один
N = Много
```

---

## ДЕТАЛЬНОЕ ОПИСАНИЕ ТАБЛИЦ

---

### 1. CUSTOMERS (Клиенты)

**Назначение:** Хранение информации о клиентах компании.

**Описание:** Таблица содержит полную контактную информацию клиентов для CRM и обработки заказов.

#### Структура таблицы

| № | Имя столбца | Тип данных | Размер | NULL | Значение по умолчанию | Описание |
|---|-------------|------------|--------|------|-----------------------|----------|
| 1 | **CustomerID** | INT | 4 байта | NOT NULL | IDENTITY(1,1) | Уникальный идентификатор клиента (PRIMARY KEY) |
| 2 | CustomerName | NVARCHAR | 100 символов | NOT NULL | - | Полное имя клиента или название компании |
| 3 | Email | NVARCHAR | 100 символов | NULL | - | Адрес электронной почты клиента |
| 4 | Phone | NVARCHAR | 20 символов | NULL | - | Контактный телефонный номер |
| 5 | Address | NVARCHAR | 255 символов | NULL | - | Физический адрес клиента (улица, дом) |
| 6 | City | NVARCHAR | 50 символов | NULL | - | Город проживания/регистрации |
| 7 | Country | NVARCHAR | 50 символов | NULL | - | Страна |
| 8 | CreatedDate | DATETIME | 8 байт | NULL | GETDATE() | Дата и время регистрации клиента в системе |

#### Ключи и ограничения

**PRIMARY KEY:**
- `CustomerID` - автоинкрементируемый идентификатор (IDENTITY)

**UNIQUE CONSTRAINTS:**
- Отсутствуют (допускается несколько клиентов с одинаковыми именами)

**CHECK CONSTRAINTS:**
- Отсутствуют

#### Индексы

| Имя индекса | Тип | Столбцы | Уникальность | Назначение |
|-------------|-----|---------|--------------|------------|
| PK_Customers | CLUSTERED | CustomerID | UNIQUE | Первичный ключ |

#### Связи с другими таблицами

**Исходящие связи (Foreign Keys в других таблицах):**
- `Sales.CustomerID` → `Customers.CustomerID` (ONE-TO-MANY)

#### SQL-скрипт создания таблицы

```sql
CREATE TABLE Customers (
    CustomerID INT PRIMARY KEY IDENTITY(1,1),
    CustomerName NVARCHAR(100) NOT NULL,
    Email NVARCHAR(100),
    Phone NVARCHAR(20),
    Address NVARCHAR(255),
    City NVARCHAR(50),
    Country NVARCHAR(50),
    CreatedDate DATETIME DEFAULT GETDATE()
);
```

#### Примеры данных

| CustomerID | CustomerName | Email | Phone | City | Country | CreatedDate |
|------------|--------------|-------|-------|------|---------|-------------|
| 1 | ООО "Рога и Копыта" | info@rogaikopita.ru | +7-495-123-4567 | Москва | Россия | 2025-11-13 10:00:00 |
| 2 | Иванов Иван Иванович | ivanov@mail.ru | +7-916-555-1234 | Санкт-Петербург | Россия | 2025-11-13 11:30:00 |
| 3 | ABC Corporation | contact@abc.com | +1-555-0100 | New York | USA | 2025-11-13 14:20:00 |

---

### 2. PRODUCTS (Продукты)

**Назначение:** Каталог товаров и управление складскими запасами.

**Описание:** Таблица содержит полную информацию о продуктах, включая цены, категории и текущие остатки на складе.

#### Структура таблицы

| № | Имя столбца | Тип данных | Размер | NULL | Значение по умолчанию | Описание |
|---|-------------|------------|--------|------|-----------------------|----------|
| 1 | **ProductID** | INT | 4 байта | NOT NULL | IDENTITY(1,1) | Уникальный идентификатор продукта (PRIMARY KEY) |
| 2 | ProductName | NVARCHAR | 100 символов | NOT NULL | - | Название товара/продукта |
| 3 | Category | NVARCHAR | 50 символов | NULL | - | Категория товара (электроника, одежда и т.д.) |
| 4 | UnitPrice | DECIMAL | (10,2) | NOT NULL | - | Цена за единицу товара (с точностью до копеек) |
| 5 | StockQuantity | INT | 4 байта | NULL | 0 | Количество товара на складе (в штуках) |
| 6 | Description | NVARCHAR | 500 символов | NULL | - | Подробное описание товара, характеристики |
| 7 | CreatedDate | DATETIME | 8 байт | NULL | GETDATE() | Дата добавления товара в каталог |

#### Ключи и ограничения

**PRIMARY KEY:**
- `ProductID` - автоинкрементируемый идентификатор (IDENTITY)

**CHECK CONSTRAINTS:**
- Отсутствуют (рекомендуется добавить: `UnitPrice >= 0`, `StockQuantity >= 0`)

#### Индексы

| Имя индекса | Тип | Столбцы | Уникальность | Назначение |
|-------------|-----|---------|--------------|------------|
| PK_Products | CLUSTERED | ProductID | UNIQUE | Первичный ключ |

**Рекомендуемые дополнительные индексы:**
```sql
CREATE INDEX IX_Products_Category ON Products(Category);
CREATE INDEX IX_Products_Name ON Products(ProductName);
```

#### Связи с другими таблицами

**Исходящие связи:**
- `SaleDetails.ProductID` → `Products.ProductID` (ONE-TO-MANY)

#### SQL-скрипт создания таблицы

```sql
CREATE TABLE Products (
    ProductID INT PRIMARY KEY IDENTITY(1,1),
    ProductName NVARCHAR(100) NOT NULL,
    Category NVARCHAR(50),
    UnitPrice DECIMAL(10, 2) NOT NULL,
    StockQuantity INT DEFAULT 0,
    Description NVARCHAR(500),
    CreatedDate DATETIME DEFAULT GETDATE()
);
```

#### Примеры данных

| ProductID | ProductName | Category | UnitPrice | StockQuantity | CreatedDate |
|-----------|-------------|----------|-----------|---------------|-------------|
| 1 | Ноутбук Lenovo ThinkPad | Электроника | 85000.00 | 15 | 2025-11-01 09:00:00 |
| 2 | Мышь Logitech MX Master | Периферия | 7500.00 | 50 | 2025-11-01 09:15:00 |
| 3 | Офисное кресло Premium | Мебель | 25000.00 | 8 | 2025-11-02 10:00:00 |
| 4 | Монитор Dell 27" | Электроника | 32000.00 | 0 | 2025-11-03 11:00:00 |

---

### 3. SALES (Продажи)

**Назначение:** Регистрация транзакций продаж (заголовок заказа).

**Описание:** Основная таблица для хранения информации о продажах. Связывает клиента с конкретными товарами через таблицу SaleDetails. Каждая запись представляет один заказ/чек.

#### Структура таблицы

| № | Имя столбца | Тип данных | Размер | NULL | Значение по умолчанию | Описание |
|---|-------------|------------|--------|------|-----------------------|----------|
| 1 | **SaleID** | INT | 4 байта | NOT NULL | IDENTITY(1,1) | Уникальный идентификатор продажи (PRIMARY KEY) |
| 2 | **CustomerID** | INT | 4 байта | NULL | - | Ссылка на клиента (FOREIGN KEY → Customers) |
| 3 | SaleDate | DATETIME | 8 байт | NULL | GETDATE() | Дата и время совершения продажи |
| 4 | TotalAmount | DECIMAL | (10,2) | NOT NULL | - | Общая сумма продажи (сумма всех SaleDetails) |
| 5 | Status | NVARCHAR | 20 символов | NULL | 'Completed' | Статус заказа (Completed, Pending, Cancelled) |
| 6 | Notes | NVARCHAR | 500 символов | NULL | - | Дополнительные заметки или комментарии к заказу |

#### Ключи и ограничения

**PRIMARY KEY:**
- `SaleID` - автоинкрементируемый идентификатор (IDENTITY)

**FOREIGN KEYS:**
- `CustomerID` → `Customers.CustomerID`
  - **ON DELETE:** NO ACTION (по умолчанию)
  - **ON UPDATE:** NO ACTION (по умолчанию)

**CHECK CONSTRAINTS:**
- Отсутствуют (рекомендуется добавить: `TotalAmount >= 0`)

#### Индексы

| Имя индекса | Тип | Столбцы | Уникальность | Назначение |
|-------------|-----|---------|--------------|------------|
| PK_Sales | CLUSTERED | SaleID | UNIQUE | Первичный ключ |
| FK_Sales_Customers | NON-CLUSTERED | CustomerID | NON-UNIQUE | Внешний ключ, ускорение JOIN |

**Рекомендуемые дополнительные индексы:**
```sql
CREATE INDEX IX_Sales_Date ON Sales(SaleDate DESC);
CREATE INDEX IX_Sales_Status ON Sales(Status);
```

#### Связи с другими таблицами

**Входящие связи:**
- `Customers.CustomerID` → `Sales.CustomerID` (MANY-TO-ONE)

**Исходящие связи:**
- `SaleDetails.SaleID` → `Sales.SaleID` (ONE-TO-MANY)

#### SQL-скрипт создания таблицы

```sql
CREATE TABLE Sales (
    SaleID INT PRIMARY KEY IDENTITY(1,1),
    CustomerID INT FOREIGN KEY REFERENCES Customers(CustomerID),
    SaleDate DATETIME DEFAULT GETDATE(),
    TotalAmount DECIMAL(10, 2) NOT NULL,
    Status NVARCHAR(20) DEFAULT 'Completed',
    Notes NVARCHAR(500)
);
```

#### Примеры данных

| SaleID | CustomerID | SaleDate | TotalAmount | Status | Notes |
|--------|------------|----------|-------------|--------|-------|
| 1 | 1 | 2025-11-13 10:30:00 | 92500.00 | Completed | Оптовая закупка |
| 2 | 2 | 2025-11-13 11:00:00 | 7500.00 | Completed | NULL |
| 3 | 1 | 2025-11-13 14:15:00 | 50000.00 | Pending | Ожидание оплаты |
| 4 | 3 | 2025-11-13 15:00:00 | 32000.00 | Cancelled | Отменен клиентом |

---

### 4. SALEDETAILS (Детали продаж)

**Назначение:** Хранение позиций (товаров) в каждой продаже.

**Описание:** Связующая таблица между Sales и Products. Реализует отношение Many-to-Many между продажами и продуктами. Каждая запись представляет одну позицию товара в заказе.

#### Структура таблицы

| № | Имя столбца | Тип данных | Размер | NULL | Значение по умолчанию | Описание |
|---|-------------|------------|--------|------|-----------------------|----------|
| 1 | **SaleDetailID** | INT | 4 байта | NOT NULL | IDENTITY(1,1) | Уникальный идентификатор строки детализации (PRIMARY KEY) |
| 2 | **SaleID** | INT | 4 байта | NULL | - | Ссылка на продажу (FOREIGN KEY → Sales) |
| 3 | **ProductID** | INT | 4 байта | NULL | - | Ссылка на продукт (FOREIGN KEY → Products) |
| 4 | Quantity | INT | 4 байта | NOT NULL | - | Количество единиц проданного товара |
| 5 | UnitPrice | DECIMAL | (10,2) | NOT NULL | - | Цена за единицу на момент продажи (фиксируется) |
| 6 | Subtotal | DECIMAL | (10,2) | NOT NULL | - | Итого по позиции (Quantity × UnitPrice) |

#### Ключи и ограничения

**PRIMARY KEY:**
- `SaleDetailID` - автоинкрементируемый идентификатор (IDENTITY)

**FOREIGN KEYS:**
- `SaleID` → `Sales.SaleID`
  - **ON DELETE:** CASCADE (при удалении продажи удаляются все её детали)
  - **ON UPDATE:** NO ACTION
  
- `ProductID` → `Products.ProductID`
  - **ON DELETE:** NO ACTION (нельзя удалить продукт, если есть продажи)
  - **ON UPDATE:** NO ACTION

**CHECK CONSTRAINTS:**
- Отсутствуют (рекомендуется добавить: `Quantity > 0`, `UnitPrice >= 0`, `Subtotal >= 0`)

**ВЫЧИСЛЯЕМЫЕ ПОЛЯ:**
- Рекомендуется: `Subtotal AS (Quantity * UnitPrice) PERSISTED`

#### Индексы

| Имя индекса | Тип | Столбцы | Уникальность | Назначение |
|-------------|-----|---------|--------------|------------|
| PK_SaleDetails | CLUSTERED | SaleDetailID | UNIQUE | Первичный ключ |
| FK_SaleDetails_Sales | NON-CLUSTERED | SaleID | NON-UNIQUE | Внешний ключ, ускорение JOIN |
| FK_SaleDetails_Products | NON-CLUSTERED | ProductID | NON-UNIQUE | Внешний ключ, ускорение JOIN |

**Рекомендуемые дополнительные индексы:**
```sql
CREATE INDEX IX_SaleDetails_Composite ON SaleDetails(SaleID, ProductID);
```

#### Связи с другими таблицами

**Входящие связи:**
- `Sales.SaleID` → `SaleDetails.SaleID` (MANY-TO-ONE)
- `Products.ProductID` → `SaleDetails.ProductID` (MANY-TO-ONE)

#### SQL-скрипт создания таблицы

```sql
CREATE TABLE SaleDetails (
    SaleDetailID INT PRIMARY KEY IDENTITY(1,1),
    SaleID INT FOREIGN KEY REFERENCES Sales(SaleID) ON DELETE CASCADE,
    ProductID INT FOREIGN KEY REFERENCES Products(ProductID),
    Quantity INT NOT NULL,
    UnitPrice DECIMAL(10, 2) NOT NULL,
    Subtotal DECIMAL(10, 2) NOT NULL
);
```

#### Примеры данных

| SaleDetailID | SaleID | ProductID | Quantity | UnitPrice | Subtotal |
|--------------|--------|-----------|----------|-----------|----------|
| 1 | 1 | 1 | 1 | 85000.00 | 85000.00 |
| 2 | 1 | 2 | 1 | 7500.00 | 7500.00 |
| 3 | 2 | 2 | 1 | 7500.00 | 7500.00 |
| 4 | 3 | 3 | 2 | 25000.00 | 50000.00 |
| 5 | 4 | 4 | 1 | 32000.00 | 32000.00 |

---

## СВЯЗИ МЕЖДУ ТАБЛИЦАМИ

### Граф всех связей

```
Customers (1) ←→ (N) Sales (1) ←→ (N) SaleDetails (N) ←→ (1) Products
```

### Детальное описание каждой связи

#### Связь 1: Customers → Sales

**Тип связи:** ONE-TO-MANY (Один ко многим)

**Описание:** Один клиент может иметь множество продаж, но каждая продажа принадлежит только одному клиенту.

**Реализация:**
```sql
FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID)
```

**Правила каскадного обновления:**
- **ON DELETE:** NO ACTION - нельзя удалить клиента, если у него есть продажи
- **ON UPDATE:** NO ACTION - обновление CustomerID не каскадируется

**Бизнес-логика:**
- При создании продажи обязательно указывается CustomerID
- CustomerID может быть NULL (для анонимных продаж, если требуется)
- Для удаления клиента сначала нужно удалить или переназначить все его продажи

---

#### Связь 2: Sales → SaleDetails

**Тип связи:** ONE-TO-MANY (Один ко многим)

**Описание:** Одна продажа может содержать несколько позиций товаров, каждая позиция принадлежит только одной продаже.

**Реализация:**
```sql
FOREIGN KEY (SaleID) REFERENCES Sales(SaleID) ON DELETE CASCADE
```

**Правила каскадного обновления:**
- **ON DELETE:** CASCADE - при удалении продажи автоматически удаляются все связанные детали
- **ON UPDATE:** NO ACTION

**Бизнес-логика:**
- Одна продажа = один заказ/чек
- В заказе может быть несколько разных товаров (SaleDetails)
- При отмене заказа все его позиции удаляются автоматически
- Общая сумма Sales.TotalAmount должна равняться сумме всех SaleDetails.Subtotal

**Пример запроса для проверки целостности:**
```sql
SELECT s.SaleID, s.TotalAmount, SUM(sd.Subtotal) as CalculatedTotal
FROM Sales s
LEFT JOIN SaleDetails sd ON s.SaleID = sd.SaleID
GROUP BY s.SaleID, s.TotalAmount
HAVING s.TotalAmount <> ISNULL(SUM(sd.Subtotal), 0);
```

---

#### Связь 3: Products → SaleDetails

**Тип связи:** ONE-TO-MANY (Один ко многим)

**Описание:** Один продукт может присутствовать в множестве позиций продаж, каждая позиция ссылается только на один продукт.

**Реализация:**
```sql
FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
```

**Правила каскадного обновления:**
- **ON DELETE:** NO ACTION - нельзя удалить продукт, если он использовался в продажах
- **ON UPDATE:** NO ACTION

**Бизнес-логика:**
- Цена продукта (UnitPrice) фиксируется в SaleDetails на момент продажи
- Даже если цена в Products изменится, старые продажи сохранят историческую цену
- Продукт можно "снять с продажи", изменив StockQuantity на 0, но нельзя удалить из базы
- История продаж сохраняется для аудита и отчетности

---

## ИНДЕКСЫ И ОГРАНИЧЕНИЯ

### Существующие индексы

#### Таблица Customers

| Тип индекса | Имя | Столбцы | Описание |
|-------------|-----|---------|----------|
| CLUSTERED | PK_Customers | CustomerID | Автоматически создан для PRIMARY KEY |

#### Таблица Products

| Тип индекса | Имя | Столбцы | Описание |
|-------------|-----|---------|----------|
| CLUSTERED | PK_Products | ProductID | Автоматически создан для PRIMARY KEY |

#### Таблица Sales

| Тип индекса | Имя | Столбцы | Описание |
|-------------|-----|---------|----------|
| CLUSTERED | PK_Sales | SaleID | Автоматически создан для PRIMARY KEY |
| NON-CLUSTERED | FK_Sales_Customers | CustomerID | Автоматически создан для FOREIGN KEY |

#### Таблица SaleDetails

| Тип индекса | Имя | Столбцы | Описание |
|-------------|-----|---------|----------|
| CLUSTERED | PK_SaleDetails | SaleDetailID | Автоматически создан для PRIMARY KEY |
| NON-CLUSTERED | FK_SaleDetails_Sales | SaleID | Автоматически создан для FOREIGN KEY |
| NON-CLUSTERED | FK_SaleDetails_Products | ProductID | Автоматически создан для FOREIGN KEY |

---

### Рекомендуемые дополнительные индексы

#### Для улучшения производительности запросов:

```sql
-- Поиск клиентов по имени
CREATE INDEX IX_Customers_Name ON Customers(CustomerName);

-- Поиск клиентов по городу и стране
CREATE INDEX IX_Customers_Location ON Customers(Country, City);

-- Поиск по email для валидации уникальности
CREATE INDEX IX_Customers_Email ON Customers(Email) WHERE Email IS NOT NULL;

-- Поиск продуктов по категории
CREATE INDEX IX_Products_Category ON Products(Category) WHERE Category IS NOT NULL;

-- Поиск продуктов по названию (для автодополнения)
CREATE INDEX IX_Products_Name ON Products(ProductName);

-- Фильтр продуктов в наличии
CREATE INDEX IX_Products_Stock ON Products(StockQuantity) WHERE StockQuantity > 0;

-- Отчеты по продажам за период
CREATE INDEX IX_Sales_Date ON Sales(SaleDate DESC);

-- Фильтрация продаж по статусу
CREATE INDEX IX_Sales_Status ON Sales(Status);

-- Составной индекс для отчетов по клиентам за период
CREATE INDEX IX_Sales_CustomerDate ON Sales(CustomerID, SaleDate DESC);

-- Оптимизация JOIN между Sales и SaleDetails
CREATE INDEX IX_SaleDetails_Composite ON SaleDetails(SaleID, ProductID) INCLUDE (Quantity, Subtotal);

-- Отчеты по продажам конкретного продукта
CREATE INDEX IX_SaleDetails_Product ON SaleDetails(ProductID) INCLUDE (Quantity, Subtotal);
```

---

### Ограничения целостности данных

#### Существующие ограничения

```sql
-- PRIMARY KEY constraints
ALTER TABLE Customers ADD CONSTRAINT PK_Customers PRIMARY KEY (CustomerID);
ALTER TABLE Products ADD CONSTRAINT PK_Products PRIMARY KEY (ProductID);
ALTER TABLE Sales ADD CONSTRAINT PK_Sales PRIMARY KEY (SaleID);
ALTER TABLE SaleDetails ADD CONSTRAINT PK_SaleDetails PRIMARY KEY (SaleDetailID);

-- FOREIGN KEY constraints
ALTER TABLE Sales ADD CONSTRAINT FK_Sales_Customers 
    FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID);

ALTER TABLE SaleDetails ADD CONSTRAINT FK_SaleDetails_Sales 
    FOREIGN KEY (SaleID) REFERENCES Sales(SaleID) ON DELETE CASCADE;

ALTER TABLE SaleDetails ADD CONSTRAINT FK_SaleDetails_Products 
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID);

-- NOT NULL constraints
ALTER TABLE Customers ALTER COLUMN CustomerName NVARCHAR(100) NOT NULL;
ALTER TABLE Products ALTER COLUMN ProductName NVARCHAR(100) NOT NULL;
ALTER TABLE Products ALTER COLUMN UnitPrice DECIMAL(10,2) NOT NULL;
ALTER TABLE Sales ALTER COLUMN TotalAmount DECIMAL(10,2) NOT NULL;
ALTER TABLE SaleDetails ALTER COLUMN Quantity INT NOT NULL;
ALTER TABLE SaleDetails ALTER COLUMN UnitPrice DECIMAL(10,2) NOT NULL;
ALTER TABLE SaleDetails ALTER COLUMN Subtotal DECIMAL(10,2) NOT NULL;
```

#### Рекомендуемые дополнительные ограничения

```sql
-- CHECK constraints для валидации данных
ALTER TABLE Products ADD CONSTRAINT CK_Products_UnitPrice 
    CHECK (UnitPrice >= 0);

ALTER TABLE Products ADD CONSTRAINT CK_Products_StockQuantity 
    CHECK (StockQuantity >= 0);

ALTER TABLE Sales ADD CONSTRAINT CK_Sales_TotalAmount 
    CHECK (TotalAmount >= 0);

ALTER TABLE Sales ADD CONSTRAINT CK_Sales_Status 
    CHECK (Status IN ('Completed', 'Pending', 'Cancelled', 'Refunded'));

ALTER TABLE SaleDetails ADD CONSTRAINT CK_SaleDetails_Quantity 
    CHECK (Quantity > 0);

ALTER TABLE SaleDetails ADD CONSTRAINT CK_SaleDetails_UnitPrice 
    CHECK (UnitPrice >= 0);

ALTER TABLE SaleDetails ADD CONSTRAINT CK_SaleDetails_Subtotal 
    CHECK (Subtotal >= 0);

-- UNIQUE constraints
ALTER TABLE Customers ADD CONSTRAINT UQ_Customers_Email 
    UNIQUE (Email);  -- Если требуется уникальность email
```

---

## БИЗНЕС-ПРАВИЛА

### 1. Управление клиентами

**Правило 1.1:** Имя клиента обязательно для заполнения
- `CustomerName` NOT NULL

**Правило 1.2:** Email должен быть уникальным (рекомендуется)
- Добавить UNIQUE constraint на Email

**Правило 1.3:** Клиента нельзя удалить, если у него есть продажи
- FOREIGN KEY без CASCADE DELETE

**Правило 1.4:** Дата регистрации автоматически устанавливается на текущую
- `CreatedDate DEFAULT GETDATE()`

---

### 2. Управление продуктами

**Правило 2.1:** Название продукта обязательно
- `ProductName` NOT NULL

**Правило 2.2:** Цена не может быть отрицательной
- Добавить: `CHECK (UnitPrice >= 0)`

**Правило 2.3:** Количество на складе не может быть отрицательным
- Добавить: `CHECK (StockQuantity >= 0)`

**Правило 2.4:** Нельзя удалить продукт, если он использовался в продажах
- FOREIGN KEY в SaleDetails без CASCADE DELETE

**Правило 2.5:** При продаже товара его количество на складе уменьшается
- Реализуется в приложении при создании продажи

---

### 3. Обработка продаж

**Правило 3.1:** Каждая продажа должна иметь хотя бы одну позицию (SaleDetail)
- Проверяется на уровне приложения

**Правило 3.2:** Сумма продажи должна равняться сумме всех позиций
```sql
Sales.TotalAmount = SUM(SaleDetails.Subtotal)
```

**Правило 3.3:** При удалении продажи удаляются все её детали
- `ON DELETE CASCADE` для SaleDetails.SaleID

**Правило 3.4:** Нельзя продать больше товара, чем есть на складе
- Проверка: `Quantity <= Products.StockQuantity`

**Правило 3.5:** Цена фиксируется на момент продажи
- `SaleDetails.UnitPrice` сохраняет цену из `Products.UnitPrice` на момент создания

**Правило 3.6:** Возможные статусы продажи:
- `Completed` - завершена успешно
- `Pending` - ожидает обработки/оплаты
- `Cancelled` - отменена
- `Refunded` - возврат средств

**Правило 3.7:** Subtotal рассчитывается как Quantity × UnitPrice
```sql
SaleDetails.Subtotal = SaleDetails.Quantity * SaleDetails.UnitPrice
```

---

### 4. Инвентаризация

**Правило 4.1:** Обновление складских остатков при продаже
```sql
UPDATE Products 
SET StockQuantity = StockQuantity - @Quantity 
WHERE ProductID = @ProductID;
```

**Правило 4.2:** При отмене продажи товар возвращается на склад
```sql
UPDATE Products 
SET StockQuantity = StockQuantity + @Quantity 
WHERE ProductID = @ProductID;
```

**Правило 4.3:** Продукты с нулевым остатком не доступны для продажи
- Фильтр: `WHERE StockQuantity > 0`

---

## ТРИГГЕРЫ И ХРАНИМЫЕ ПРОЦЕДУРЫ

### Рекомендуемые триггеры для автоматизации

#### TRIGGER 1: Автоматическое обновление TotalAmount в Sales

```sql
CREATE TRIGGER TR_SaleDetails_UpdateSaleTotalAmount
ON SaleDetails
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    
    -- Обновить TotalAmount для затронутых продаж
    UPDATE s
    SET TotalAmount = ISNULL((
        SELECT SUM(Subtotal)
        FROM SaleDetails
        WHERE SaleID = s.SaleID
    ), 0)
    FROM Sales s
    WHERE s.SaleID IN (
        SELECT DISTINCT SaleID FROM inserted
        UNION
        SELECT DISTINCT SaleID FROM deleted
    );
END;
GO
```

**Назначение:** Автоматически пересчитывает общую сумму продажи при добавлении, изменении или удалении позиций.

---

#### TRIGGER 2: Проверка наличия товара на складе

```sql
CREATE TRIGGER TR_SaleDetails_CheckStock
ON SaleDetails
INSTEAD OF INSERT
AS
BEGIN
    SET NOCOUNT ON;
    
    -- Проверить наличие товара
    IF EXISTS (
        SELECT 1
        FROM inserted i
        JOIN Products p ON i.ProductID = p.ProductID
        WHERE p.StockQuantity < i.Quantity
    )
    BEGIN
        RAISERROR('Insufficient stock for one or more products', 16, 1);
        ROLLBACK TRANSACTION;
        RETURN;
    END
    
    -- Если все OK, вставить данные
    INSERT INTO SaleDetails (SaleID, ProductID, Quantity, UnitPrice, Subtotal)
    SELECT SaleID, ProductID, Quantity, UnitPrice, Subtotal
    FROM inserted;
    
    -- Уменьшить количество на складе
    UPDATE p
    SET StockQuantity = StockQuantity - i.Quantity
    FROM Products p
    JOIN inserted i ON p.ProductID = i.ProductID;
END;
GO
```

**Назначение:** Проверяет наличие товара на складе перед продажей и автоматически уменьшает остаток.

---

#### TRIGGER 3: Восстановление склада при удалении продажи

```sql
CREATE TRIGGER TR_SaleDetails_RestoreStock
ON SaleDetails
AFTER DELETE
AS
BEGIN
    SET NOCOUNT ON;
    
    -- Вернуть товар на склад
    UPDATE p
    SET StockQuantity = StockQuantity + d.Quantity
    FROM Products p
    JOIN deleted d ON p.ProductID = d.ProductID;
END;
GO
```

**Назначение:** Возвращает товар на склад при отмене продажи или удалении позиции.

---

#### TRIGGER 4: Автоматический расчет Subtotal

```sql
CREATE TRIGGER TR_SaleDetails_CalculateSubtotal
ON SaleDetails
INSTEAD OF INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    
    -- При INSERT
    IF NOT EXISTS (SELECT * FROM deleted)
    BEGIN
        INSERT INTO SaleDetails (SaleID, ProductID, Quantity, UnitPrice, Subtotal)
        SELECT 
            SaleID, 
            ProductID, 
            Quantity, 
            UnitPrice, 
            Quantity * UnitPrice
        FROM inserted;
    END
    -- При UPDATE
    ELSE
    BEGIN
        UPDATE sd
        SET 
            SaleID = i.SaleID,
            ProductID = i.ProductID,
            Quantity = i.Quantity,
            UnitPrice = i.UnitPrice,
            Subtotal = i.Quantity * i.UnitPrice
        FROM SaleDetails sd
        JOIN inserted i ON sd.SaleDetailID = i.SaleDetailID;
    END
END;
GO
```

**Назначение:** Автоматически рассчитывает Subtotal при вставке или обновлении.

---

### Рекомендуемые хранимые процедуры

#### PROCEDURE 1: Создание полной продажи (транзакция)

```sql
CREATE PROCEDURE sp_CreateSale
    @CustomerID INT,
    @Items NVARCHAR(MAX), -- JSON: [{"ProductID":1,"Quantity":2}, ...]
    @Notes NVARCHAR(500) = NULL,
    @NewSaleID INT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @TotalAmount DECIMAL(10,2) = 0;
        
        -- Создать заголовок продажи
        INSERT INTO Sales (CustomerID, TotalAmount, Status, Notes)
        VALUES (@CustomerID, 0, 'Pending', @Notes);
        
        SET @NewSaleID = SCOPE_IDENTITY();
        
        -- Парсить JSON и добавить позиции
        INSERT INTO SaleDetails (SaleID, ProductID, Quantity, UnitPrice, Subtotal)
        SELECT 
            @NewSaleID,
            JSON_VALUE(value, '$.ProductID'),
            JSON_VALUE(value, '$.Quantity'),
            p.UnitPrice,
            JSON_VALUE(value, '$.Quantity') * p.UnitPrice
        FROM OPENJSON(@Items)
        CROSS APPLY (
            SELECT UnitPrice 
            FROM Products 
            WHERE ProductID = JSON_VALUE(value, '$.ProductID')
        ) p;
        
        -- Обновить сумму
        SELECT @TotalAmount = SUM(Subtotal)
        FROM SaleDetails
        WHERE SaleID = @NewSaleID;
        
        UPDATE Sales
        SET TotalAmount = @TotalAmount, Status = 'Completed'
        WHERE SaleID = @NewSaleID;
        
        COMMIT TRANSACTION;
        RETURN 0;
    END TRY
    BEGIN CATCH
        ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END;
GO
```

---

#### PROCEDURE 2: Отмена продажи с возвратом товара

```sql
CREATE PROCEDURE sp_CancelSale
    @SaleID INT
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    BEGIN TRY
        -- Вернуть товары на склад
        UPDATE p
        SET StockQuantity = StockQuantity + sd.Quantity
        FROM Products p
        JOIN SaleDetails sd ON p.ProductID = sd.ProductID
        WHERE sd.SaleID = @SaleID;
        
        -- Изменить статус продажи
        UPDATE Sales
        SET Status = 'Cancelled'
        WHERE SaleID = @SaleID;
        
        COMMIT TRANSACTION;
        RETURN 0;
    END TRY
    BEGIN CATCH
        ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END;
GO
```

---

#### PROCEDURE 3: Отчет по продажам за период

```sql
CREATE PROCEDURE sp_SalesReport
    @StartDate DATETIME,
    @EndDate DATETIME
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT 
        s.SaleID,
        s.SaleDate,
        c.CustomerName,
        c.City,
        c.Country,
        s.TotalAmount,
        s.Status,
        COUNT(sd.SaleDetailID) as ItemCount
    FROM Sales s
    LEFT JOIN Customers c ON s.CustomerID = c.CustomerID
    LEFT JOIN SaleDetails sd ON s.SaleID = sd.SaleID
    WHERE s.SaleDate BETWEEN @StartDate AND @EndDate
    GROUP BY s.SaleID, s.SaleDate, c.CustomerName, c.City, c.Country, s.TotalAmount, s.Status
    ORDER BY s.SaleDate DESC;
    
    -- Итоговая статистика
    SELECT 
        COUNT(DISTINCT s.SaleID) as TotalSales,
        SUM(s.TotalAmount) as TotalRevenue,
        AVG(s.TotalAmount) as AverageOrderValue,
        COUNT(DISTINCT s.CustomerID) as UniqueCustomers
    FROM Sales s
    WHERE s.SaleDate BETWEEN @StartDate AND @EndDate
        AND s.Status = 'Completed';
END;
GO
```

---

#### PROCEDURE 4: Топ продаваемых товаров

```sql
CREATE PROCEDURE sp_TopSellingProducts
    @TopN INT = 10,
    @StartDate DATETIME = NULL,
    @EndDate DATETIME = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT TOP (@TopN)
        p.ProductID,
        p.ProductName,
        p.Category,
        SUM(sd.Quantity) as TotalQuantitySold,
        SUM(sd.Subtotal) as TotalRevenue,
        AVG(sd.UnitPrice) as AveragePrice,
        COUNT(DISTINCT sd.SaleID) as NumberOfOrders
    FROM Products p
    JOIN SaleDetails sd ON p.ProductID = sd.ProductID
    JOIN Sales s ON sd.SaleID = s.SaleID
    WHERE (@StartDate IS NULL OR s.SaleDate >= @StartDate)
        AND (@EndDate IS NULL OR s.SaleDate <= @EndDate)
        AND s.Status = 'Completed'
    GROUP BY p.ProductID, p.ProductName, p.Category
    ORDER BY TotalRevenue DESC;
END;
GO
```

---

#### PROCEDURE 5: Список клиентов с историей покупок

```sql
CREATE PROCEDURE sp_CustomerPurchaseHistory
    @CustomerID INT
AS
BEGIN
    SET NOCOUNT ON;
    
    -- Информация о клиенте
    SELECT 
        CustomerID,
        CustomerName,
        Email,
        Phone,
        City,
        Country,
        CreatedDate
    FROM Customers
    WHERE CustomerID = @CustomerID;
    
    -- История покупок
    SELECT 
        s.SaleID,
        s.SaleDate,
        s.TotalAmount,
        s.Status,
        COUNT(sd.SaleDetailID) as ItemsCount
    FROM Sales s
    LEFT JOIN SaleDetails sd ON s.SaleID = sd.SaleID
    WHERE s.CustomerID = @CustomerID
    GROUP BY s.SaleID, s.SaleDate, s.TotalAmount, s.Status
    ORDER BY s.SaleDate DESC;
    
    -- Статистика по клиенту
    SELECT 
        COUNT(SaleID) as TotalOrders,
        SUM(CASE WHEN Status = 'Completed' THEN TotalAmount ELSE 0 END) as TotalSpent,
        AVG(CASE WHEN Status = 'Completed' THEN TotalAmount ELSE NULL END) as AverageOrderValue,
        MAX(SaleDate) as LastPurchaseDate
    FROM Sales
    WHERE CustomerID = @CustomerID;
END;
GO
```

---

## ПРИМЕРЫ ПОЛЕЗНЫХ ЗАПРОСОВ

### 1. Получить все продажи клиента с деталями

```sql
SELECT 
    s.SaleID,
    s.SaleDate,
    c.CustomerName,
    p.ProductName,
    sd.Quantity,
    sd.UnitPrice,
    sd.Subtotal,
    s.TotalAmount as OrderTotal
FROM Sales s
JOIN Customers c ON s.CustomerID = c.CustomerID
JOIN SaleDetails sd ON s.SaleID = sd.SaleID
JOIN Products p ON sd.ProductID = p.ProductID
WHERE c.CustomerID = 1
ORDER BY s.SaleDate DESC, sd.SaleDetailID;
```

---

### 2. Топ-10 клиентов по объему покупок

```sql
SELECT TOP 10
    c.CustomerID,
    c.CustomerName,
    c.City,
    c.Country,
    COUNT(s.SaleID) as TotalOrders,
    SUM(s.TotalAmount) as TotalSpent,
    AVG(s.TotalAmount) as AverageOrderValue,
    MAX(s.SaleDate) as LastPurchase
FROM Customers c
JOIN Sales s ON c.CustomerID = s.CustomerID
WHERE s.Status = 'Completed'
GROUP BY c.CustomerID, c.CustomerName, c.City, c.Country
ORDER BY TotalSpent DESC;
```

---

### 3. Товары, которые заканчиваются на складе

```sql
SELECT 
    ProductID,
    ProductName,
    Category,
    UnitPrice,
    StockQuantity
FROM Products
WHERE StockQuantity > 0 AND StockQuantity <= 10
ORDER BY StockQuantity ASC, ProductName;
```

---

### 4. Продажи по месяцам (за последний год)

```sql
SELECT 
    YEAR(SaleDate) as Year,
    MONTH(SaleDate) as Month,
    DATENAME(MONTH, SaleDate) as MonthName,
    COUNT(SaleID) as TotalSales,
    SUM(TotalAmount) as Revenue,
    AVG(TotalAmount) as AverageOrderValue
FROM Sales
WHERE SaleDate >= DATEADD(YEAR, -1, GETDATE())
    AND Status = 'Completed'
GROUP BY YEAR(SaleDate), MONTH(SaleDate), DATENAME(MONTH, SaleDate)
ORDER BY Year DESC, Month DESC;
```

---

### 5. Товары, которые никогда не продавались

```sql
SELECT 
    p.ProductID,
    p.ProductName,
    p.Category,
    p.UnitPrice,
    p.StockQuantity,
    p.CreatedDate
FROM Products p
LEFT JOIN SaleDetails sd ON p.ProductID = sd.ProductID
WHERE sd.ProductID IS NULL
ORDER BY p.CreatedDate DESC;
```

---

### 6. Проверка целостности: расхождение в суммах

```sql
SELECT 
    s.SaleID,
    s.TotalAmount as RecordedTotal,
    ISNULL(SUM(sd.Subtotal), 0) as CalculatedTotal,
    s.TotalAmount - ISNULL(SUM(sd.Subtotal), 0) as Difference
FROM Sales s
LEFT JOIN SaleDetails sd ON s.SaleID = sd.SaleID
GROUP BY s.SaleID, s.TotalAmount
HAVING s.TotalAmount <> ISNULL(SUM(sd.Subtotal), 0);
```

---

### 7. Анализ продаж по категориям товаров

```sql
SELECT 
    p.Category,
    COUNT(DISTINCT sd.SaleID) as NumberOfSales,
    SUM(sd.Quantity) as TotalUnitsSold,
    SUM(sd.Subtotal) as TotalRevenue,
    AVG(sd.UnitPrice) as AveragePrice
FROM Products p
JOIN SaleDetails sd ON p.ProductID = sd.ProductID
JOIN Sales s ON sd.SaleID = s.SaleID
WHERE s.Status = 'Completed'
GROUP BY p.Category
ORDER BY TotalRevenue DESC;
```

---

### 8. Клиенты без покупок

```sql
SELECT 
    c.CustomerID,
    c.CustomerName,
    c.Email,
    c.Phone,
    c.CreatedDate,
    DATEDIFF(DAY, c.CreatedDate, GETDATE()) as DaysSinceRegistration
FROM Customers c
LEFT JOIN Sales s ON c.CustomerID = s.CustomerID
WHERE s.SaleID IS NULL
ORDER BY c.CreatedDate DESC;
```

---

## РЕКОМЕНДАЦИИ ПО ОПТИМИЗАЦИИ

### 1. Индексация
✅ Добавить индексы на часто используемые поля для JOIN и WHERE
✅ Использовать INCLUDE для covering indexes
✅ Регулярно обновлять статистику: `UPDATE STATISTICS`

### 2. Партиционирование (для больших объемов)
- Партиционировать таблицу Sales по датам (по месяцам/годам)
- Архивировать старые продажи в отдельные таблицы

### 3. Денормализация для отчетов
- Создать отдельную таблицу с агрегированными данными для дашбордов
- Использовать материализованные представления (indexed views)

### 4. Кэширование
- Кэшировать часто запрашиваемые отчеты на уровне приложения
- Использовать Redis/Memcached для хранения статистики

### 5. Мониторинг производительности
```sql
-- Найти медленные запросы
SELECT TOP 10
    qs.execution_count,
    qs.total_elapsed_time / qs.execution_count as avg_time,
    SUBSTRING(qt.text, (qs.statement_start_offset/2)+1,
        ((CASE qs.statement_end_offset
            WHEN -1 THEN DATALENGTH(qt.text)
            ELSE qs.statement_end_offset
        END - qs.statement_start_offset)/2) + 1) as query_text
FROM sys.dm_exec_query_stats qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) qt
ORDER BY avg_time DESC;
```

---

## БЕЗОПАСНОСТЬ

### Рекомендации по безопасности

1. **Пользователи и роли**
```sql
-- Создать роли
CREATE ROLE SalesManager;
CREATE ROLE SalesEmployee;
CREATE ROLE ReportViewer;

-- Назначить права
GRANT SELECT, INSERT, UPDATE ON Customers TO SalesManager;
GRANT SELECT, INSERT, UPDATE ON Products TO SalesManager;
GRANT SELECT, INSERT ON Sales TO SalesEmployee;
GRANT SELECT ON Sales TO ReportViewer;
```

2. **Шифрование данных**
- Шифровать персональные данные клиентов (Email, Phone)
- Использовать Always Encrypted для чувствительных полей

3. **Аудит изменений**
- Создать таблицы аудита для отслеживания изменений
- Использовать триггеры для логирования операций

4. **SQL Injection Protection**
- Всегда использовать параметризованные запросы
- Валидировать входные данные на уровне приложения

---

## РЕЗЕРВНОЕ КОПИРОВАНИЕ

### Стратегия backup

```sql
-- Полное резервное копирование (ежедневно)
BACKUP DATABASE SalesManagement
TO DISK = 'C:\Backups\SalesManagement_Full.bak'
WITH INIT, COMPRESSION;

-- Дифференциальное (каждые 6 часов)
BACKUP DATABASE SalesManagement
TO DISK = 'C:\Backups\SalesManagement_Diff.bak'
WITH DIFFERENTIAL, COMPRESSION;

-- Резервная копия журнала транзакций (каждый час)
BACKUP LOG SalesManagement
TO DISK = 'C:\Backups\SalesManagement_Log.trn'
WITH COMPRESSION;
```

---

## ВЕРСИОНИРОВАНИЕ И МИГРАЦИИ

### История изменений схемы

| Версия | Дата | Изменения |
|--------|------|-----------|
| 1.0 | 2025-11-13 | Первоначальная версия: Customers, Products, Sales, SaleDetails |
| 1.1 | - | Добавить таблицу Users для авторизации |
| 1.2 | - | Добавить таблицу Categories для иерархии категорий |
| 2.0 | - | Добавить таблицы для управления поставщиками |

---

## КОНТАКТЫ И ПОДДЕРЖКА

**Администратор базы данных:** -  
**Разработчик:** GitHub Copilot  
**Дата создания документации:** 13 ноября 2025

---

**КОНЕЦ ДОКУМЕНТАЦИИ**

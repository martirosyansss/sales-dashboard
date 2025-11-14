# SQL Scripts Collection
# Полная коллекция SQL-скриптов для базы данных SalesManagement

## Создание всех таблиц с нуля

```sql
USE SalesManagement;
GO

-- =============================================
-- Создание таблицы Customers
-- =============================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Customers')
BEGIN
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
    PRINT 'Таблица Customers создана';
END
GO

-- =============================================
-- Создание таблицы Products
-- =============================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Products')
BEGIN
    CREATE TABLE Products (
        ProductID INT PRIMARY KEY IDENTITY(1,1),
        ProductName NVARCHAR(100) NOT NULL,
        Category NVARCHAR(50),
        UnitPrice DECIMAL(10, 2) NOT NULL,
        StockQuantity INT DEFAULT 0,
        Description NVARCHAR(500),
        CreatedDate DATETIME DEFAULT GETDATE()
    );
    PRINT 'Таблица Products создана';
END
GO

-- =============================================
-- Создание таблицы Sales
-- =============================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Sales')
BEGIN
    CREATE TABLE Sales (
        SaleID INT PRIMARY KEY IDENTITY(1,1),
        CustomerID INT FOREIGN KEY REFERENCES Customers(CustomerID),
        SaleDate DATETIME DEFAULT GETDATE(),
        TotalAmount DECIMAL(10, 2) NOT NULL,
        Status NVARCHAR(20) DEFAULT 'Completed',
        Notes NVARCHAR(500)
    );
    PRINT 'Таблица Sales создана';
END
GO

-- =============================================
-- Создание таблицы SaleDetails
-- =============================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'SaleDetails')
BEGIN
    CREATE TABLE SaleDetails (
        SaleDetailID INT PRIMARY KEY IDENTITY(1,1),
        SaleID INT FOREIGN KEY REFERENCES Sales(SaleID) ON DELETE CASCADE,
        ProductID INT FOREIGN KEY REFERENCES Products(ProductID),
        Quantity INT NOT NULL,
        UnitPrice DECIMAL(10, 2) NOT NULL,
        Subtotal DECIMAL(10, 2) NOT NULL
    );
    PRINT 'Таблица SaleDetails создана';
END
GO

-- =============================================
-- Вставка тестовых данных
-- =============================================

-- Тестовые клиенты
INSERT INTO Customers (CustomerName, Email, Phone, Address, City, Country) VALUES
('ООО "Альфа"', 'info@alpha.ru', '+7-495-123-4567', 'ул. Ленина, 1', 'Москва', 'Россия'),
('ИП Иванов', 'ivanov@mail.ru', '+7-916-555-1234', 'пр. Невский, 50', 'Санкт-Петербург', 'Россия'),
('ООО "Бета"', 'beta@company.ru', '+7-812-999-8877', 'ул. Пушкина, 10', 'Москва', 'Россия'),
('Петров Петр', 'petrov@gmail.com', '+7-903-111-2233', 'ул. Советская, 25', 'Новосибирск', 'Россия'),
('ABC Corporation', 'contact@abc.com', '+1-555-0100', '123 Main St', 'New York', 'USA');
GO

-- Тестовые продукты
INSERT INTO Products (ProductName, Category, UnitPrice, StockQuantity, Description) VALUES
('Ноутбук Lenovo ThinkPad X1', 'Электроника', 85000.00, 15, 'Профессиональный ноутбук 14" Intel i7, 16GB RAM, 512GB SSD'),
('Мышь Logitech MX Master 3', 'Периферия', 7500.00, 50, 'Беспроводная мышь для профессионалов'),
('Клавиатура Keychron K2', 'Периферия', 8900.00, 30, 'Механическая беспроводная клавиатура'),
('Монитор Dell UltraSharp 27"', 'Электроника', 32000.00, 12, '4K монитор для профессиональной работы'),
('Офисное кресло Herman Miller', 'Мебель', 95000.00, 5, 'Эргономичное кресло премиум класса'),
('Стол регулируемый по высоте', 'Мебель', 45000.00, 8, 'Электрический стол-трансформер'),
('Наушники Sony WH-1000XM5', 'Аудио', 28000.00, 25, 'Беспроводные наушники с шумоподавлением'),
('Веб-камера Logitech Brio', 'Периферия', 18500.00, 20, '4K веб-камера для видеоконференций'),
('USB-хаб Anker 7-портов', 'Аксессуары', 3500.00, 100, 'USB 3.0 хаб с питанием'),
('SSD Samsung 1TB', 'Комплектующие', 9500.00, 40, 'Внешний твердотельный накопитель');
GO

-- Тестовые продажи
DECLARE @SaleID1 INT, @SaleID2 INT, @SaleID3 INT;

-- Продажа 1
INSERT INTO Sales (CustomerID, TotalAmount, Status, Notes)
VALUES (1, 92500.00, 'Completed', 'Оптовая закупка для офиса');
SET @SaleID1 = SCOPE_IDENTITY();

INSERT INTO SaleDetails (SaleID, ProductID, Quantity, UnitPrice, Subtotal) VALUES
(@SaleID1, 1, 1, 85000.00, 85000.00),
(@SaleID1, 2, 1, 7500.00, 7500.00);

-- Продажа 2
INSERT INTO Sales (CustomerID, TotalAmount, Status)
VALUES (2, 16400.00, 'Completed');
SET @SaleID2 = SCOPE_IDENTITY();

INSERT INTO SaleDetails (SaleID, ProductID, Quantity, UnitPrice, Subtotal) VALUES
(@SaleID2, 2, 1, 7500.00, 7500.00),
(@SaleID2, 3, 1, 8900.00, 8900.00);

-- Продажа 3
INSERT INTO Sales (CustomerID, TotalAmount, Status, Notes)
VALUES (3, 140000.00, 'Pending', 'Ожидание оплаты');
SET @SaleID3 = SCOPE_IDENTITY();

INSERT INTO SaleDetails (SaleID, ProductID, Quantity, UnitPrice, Subtotal) VALUES
(@SaleID3, 5, 1, 95000.00, 95000.00),
(@SaleID3, 6, 1, 45000.00, 45000.00);

PRINT 'Тестовые данные добавлены';
GO
```

## Создание рекомендуемых индексов

```sql
-- =============================================
-- Индексы для оптимизации производительности
-- =============================================

USE SalesManagement;
GO

-- Индексы для таблицы Customers
CREATE INDEX IX_Customers_Name ON Customers(CustomerName);
CREATE INDEX IX_Customers_Location ON Customers(Country, City);
CREATE INDEX IX_Customers_Email ON Customers(Email) WHERE Email IS NOT NULL;
GO

-- Индексы для таблицы Products
CREATE INDEX IX_Products_Category ON Products(Category) WHERE Category IS NOT NULL;
CREATE INDEX IX_Products_Name ON Products(ProductName);
CREATE INDEX IX_Products_Stock ON Products(StockQuantity) WHERE StockQuantity > 0;
GO

-- Индексы для таблицы Sales
CREATE INDEX IX_Sales_Date ON Sales(SaleDate DESC);
CREATE INDEX IX_Sales_Status ON Sales(Status);
CREATE INDEX IX_Sales_CustomerDate ON Sales(CustomerID, SaleDate DESC);
GO

-- Индексы для таблицы SaleDetails
CREATE INDEX IX_SaleDetails_Composite ON SaleDetails(SaleID, ProductID) INCLUDE (Quantity, Subtotal);
CREATE INDEX IX_SaleDetails_Product ON SaleDetails(ProductID) INCLUDE (Quantity, Subtotal);
GO

PRINT 'Все индексы созданы';
GO
```

## Создание CHECK constraints

```sql
-- =============================================
-- Ограничения для проверки данных
-- =============================================

USE SalesManagement;
GO

-- Products constraints
ALTER TABLE Products ADD CONSTRAINT CK_Products_UnitPrice 
    CHECK (UnitPrice >= 0);

ALTER TABLE Products ADD CONSTRAINT CK_Products_StockQuantity 
    CHECK (StockQuantity >= 0);
GO

-- Sales constraints
ALTER TABLE Sales ADD CONSTRAINT CK_Sales_TotalAmount 
    CHECK (TotalAmount >= 0);

ALTER TABLE Sales ADD CONSTRAINT CK_Sales_Status 
    CHECK (Status IN ('Completed', 'Pending', 'Cancelled', 'Refunded'));
GO

-- SaleDetails constraints
ALTER TABLE SaleDetails ADD CONSTRAINT CK_SaleDetails_Quantity 
    CHECK (Quantity > 0);

ALTER TABLE SaleDetails ADD CONSTRAINT CK_SaleDetails_UnitPrice 
    CHECK (UnitPrice >= 0);

ALTER TABLE SaleDetails ADD CONSTRAINT CK_SaleDetails_Subtotal 
    CHECK (Subtotal >= 0);
GO

PRINT 'Все CHECK constraints созданы';
GO
```

## Создание триггеров

```sql
-- =============================================
-- Триггер: Автоматическое обновление TotalAmount
-- =============================================

USE SalesManagement;
GO

IF OBJECT_ID('TR_SaleDetails_UpdateSaleTotalAmount', 'TR') IS NOT NULL
    DROP TRIGGER TR_SaleDetails_UpdateSaleTotalAmount;
GO

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

PRINT 'Триггер TR_SaleDetails_UpdateSaleTotalAmount создан';
GO

-- =============================================
-- Триггер: Проверка наличия товара
-- =============================================

IF OBJECT_ID('TR_SaleDetails_CheckStock', 'TR') IS NOT NULL
    DROP TRIGGER TR_SaleDetails_CheckStock;
GO

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
        DECLARE @ProductName NVARCHAR(100), @Available INT, @Requested INT;
        
        SELECT TOP 1
            @ProductName = p.ProductName,
            @Available = p.StockQuantity,
            @Requested = i.Quantity
        FROM inserted i
        JOIN Products p ON i.ProductID = p.ProductID
        WHERE p.StockQuantity < i.Quantity;
        
        DECLARE @ErrorMsg NVARCHAR(500);
        SET @ErrorMsg = 'Недостаточно товара на складе! Товар: ' + @ProductName + 
                       ', Доступно: ' + CAST(@Available AS NVARCHAR(10)) + 
                       ', Запрошено: ' + CAST(@Requested AS NVARCHAR(10));
        
        RAISERROR(@ErrorMsg, 16, 1);
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

PRINT 'Триггер TR_SaleDetails_CheckStock создан';
GO

-- =============================================
-- Триггер: Восстановление склада при удалении
-- =============================================

IF OBJECT_ID('TR_SaleDetails_RestoreStock', 'TR') IS NOT NULL
    DROP TRIGGER TR_SaleDetails_RestoreStock;
GO

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

PRINT 'Триггер TR_SaleDetails_RestoreStock создан';
GO
```

## Создание хранимых процедур

```sql
-- =============================================
-- Процедура: Создание полной продажи
-- =============================================

USE SalesManagement;
GO

IF OBJECT_ID('sp_CreateSale', 'P') IS NOT NULL
    DROP PROCEDURE sp_CreateSale;
GO

CREATE PROCEDURE sp_CreateSale
    @CustomerID INT,
    @Notes NVARCHAR(500) = NULL,
    @NewSaleID INT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    BEGIN TRY
        -- Создать заголовок продажи
        INSERT INTO Sales (CustomerID, TotalAmount, Status, Notes)
        VALUES (@CustomerID, 0, 'Pending', @Notes);
        
        SET @NewSaleID = SCOPE_IDENTITY();
        
        COMMIT TRANSACTION;
        RETURN 0;
    END TRY
    BEGIN CATCH
        ROLLBACK TRANSACTION;
        DECLARE @ErrorMessage NVARCHAR(4000) = ERROR_MESSAGE();
        RAISERROR(@ErrorMessage, 16, 1);
        RETURN -1;
    END CATCH
END;
GO

PRINT 'Процедура sp_CreateSale создана';
GO

-- =============================================
-- Процедура: Добавление позиции в продажу
-- =============================================

IF OBJECT_ID('sp_AddSaleItem', 'P') IS NOT NULL
    DROP PROCEDURE sp_AddSaleItem;
GO

CREATE PROCEDURE sp_AddSaleItem
    @SaleID INT,
    @ProductID INT,
    @Quantity INT
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    BEGIN TRY
        DECLARE @UnitPrice DECIMAL(10,2), @Subtotal DECIMAL(10,2);
        
        -- Получить цену продукта
        SELECT @UnitPrice = UnitPrice
        FROM Products
        WHERE ProductID = @ProductID;
        
        IF @UnitPrice IS NULL
        BEGIN
            RAISERROR('Продукт не найден', 16, 1);
            ROLLBACK TRANSACTION;
            RETURN -1;
        END
        
        SET @Subtotal = @Quantity * @UnitPrice;
        
        -- Добавить позицию (триггер проверит наличие)
        INSERT INTO SaleDetails (SaleID, ProductID, Quantity, UnitPrice, Subtotal)
        VALUES (@SaleID, @ProductID, @Quantity, @UnitPrice, @Subtotal);
        
        COMMIT TRANSACTION;
        RETURN 0;
    END TRY
    BEGIN CATCH
        ROLLBACK TRANSACTION;
        DECLARE @ErrorMessage NVARCHAR(4000) = ERROR_MESSAGE();
        RAISERROR(@ErrorMessage, 16, 1);
        RETURN -1;
    END CATCH
END;
GO

PRINT 'Процедура sp_AddSaleItem создана';
GO

-- =============================================
-- Процедура: Завершение продажи
-- =============================================

IF OBJECT_ID('sp_CompleteSale', 'P') IS NOT NULL
    DROP PROCEDURE sp_CompleteSale;
GO

CREATE PROCEDURE sp_CompleteSale
    @SaleID INT
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE Sales
    SET Status = 'Completed'
    WHERE SaleID = @SaleID;
    
    RETURN 0;
END;
GO

PRINT 'Процедура sp_CompleteSale создана';
GO

-- =============================================
-- Процедура: Отмена продажи
-- =============================================

IF OBJECT_ID('sp_CancelSale', 'P') IS NOT NULL
    DROP PROCEDURE sp_CancelSale;
GO

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
        DECLARE @ErrorMessage NVARCHAR(4000) = ERROR_MESSAGE();
        RAISERROR(@ErrorMessage, 16, 1);
        RETURN -1;
    END CATCH
END;
GO

PRINT 'Процедура sp_CancelSale создана';
GO

-- =============================================
-- Процедура: Отчет по продажам за период
-- =============================================

IF OBJECT_ID('sp_SalesReport', 'P') IS NOT NULL
    DROP PROCEDURE sp_SalesReport;
GO

CREATE PROCEDURE sp_SalesReport
    @StartDate DATETIME,
    @EndDate DATETIME
AS
BEGIN
    SET NOCOUNT ON;
    
    -- Детальный отчет
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

PRINT 'Процедура sp_SalesReport создана';
GO

-- =============================================
-- Процедура: Топ продаваемых товаров
-- =============================================

IF OBJECT_ID('sp_TopSellingProducts', 'P') IS NOT NULL
    DROP PROCEDURE sp_TopSellingProducts;
GO

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
        COUNT(DISTINCT sd.SaleID) as NumberOfOrders,
        p.StockQuantity as CurrentStock
    FROM Products p
    JOIN SaleDetails sd ON p.ProductID = sd.ProductID
    JOIN Sales s ON sd.SaleID = s.SaleID
    WHERE (@StartDate IS NULL OR s.SaleDate >= @StartDate)
        AND (@EndDate IS NULL OR s.SaleDate <= @EndDate)
        AND s.Status = 'Completed'
    GROUP BY p.ProductID, p.ProductName, p.Category, p.StockQuantity
    ORDER BY TotalRevenue DESC;
END;
GO

PRINT 'Процедура sp_TopSellingProducts создана';
GO

-- =============================================
-- Процедура: История покупок клиента
-- =============================================

IF OBJECT_ID('sp_CustomerPurchaseHistory', 'P') IS NOT NULL
    DROP PROCEDURE sp_CustomerPurchaseHistory;
GO

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

PRINT 'Процедура sp_CustomerPurchaseHistory создана';
GO

PRINT '';
PRINT '===========================================';
PRINT 'ВСЕ СКРИПТЫ ВЫПОЛНЕНЫ УСПЕШНО!';
PRINT '===========================================';
GO
```

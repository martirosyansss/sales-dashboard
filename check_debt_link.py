import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# Проверить как fDEBTDOCISN связан с CUSTOMERS
print("\n=== ПОИСК СВЯЗИ fDEBTDOCISN С CUSTOMERS ===")
cursor.execute("""
    SELECT TOP 5 
        c.fID,
        c.fCODE,
        c.fNAME,
        h.fSUM as DebtSum
    FROM CUSTOMERS c
    INNER JOIN HIRESTCUSTOMERSDEBT h ON c.fID = h.fDEBTDOCISN
    WHERE h.fSUM > 0
    ORDER BY h.fSUM DESC
""")
try:
    rows = cursor.fetchall()
    if rows:
        print("Связь найдена (через c.fID = h.fDEBTDOCISN):")
        for row in rows:
            print(f"  Customer {row.fCODE} ({row.fNAME}): Debt = {row.DebtSum}")
    else:
        print("Нет записей с долгом")
except Exception as e:
    print(f"Ошибка связи через fID: {e}")

# Попробовать другую связь
print("\n=== АЛЬТЕРНАТИВНАЯ СВЯЗЬ ===")
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'CUSTOMERS'
    AND COLUMN_NAME LIKE '%DEBT%'
""")
customer_debt_cols = cursor.fetchall()
if customer_debt_cols:
    for row in customer_debt_cols:
        print(f"  {row[0]} - {row[1]}")
else:
    print("  Нет колонок DEBT в CUSTOMERS")

# Проверить есть ли колонка с GUID в CUSTOMERS
print("\n=== GUID КОЛОНКИ В CUSTOMERS ===")
cursor.execute("""
    SELECT COLUMN_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'CUSTOMERS'
    AND DATA_TYPE = 'uniqueidentifier'
""")
for row in cursor.fetchall():
    print(f"  {row[0]}")

# Проверить пример данных HIRESTCUSTOMERSDEBT с долгом
print("\n=== КЛИЕНТЫ С ДОЛГОМ > 0 ===")
cursor.execute("""
    SELECT TOP 10
        fDEBTDOCISN,
        fSUM
    FROM HIRESTCUSTOMERSDEBT
    WHERE fSUM > 0
    ORDER BY fSUM DESC
""")
for row in cursor.fetchall():
    print(f"  {row.fDEBTDOCISN}: {row.fSUM}")

# Попробовать найти через HICUSTOMERSDEBT
print("\n=== LAST OPERATIONS IN HICUSTOMERSDEBT ===")
cursor.execute("""
    SELECT TOP 5
        fDEBTDOCISN,
        fDATE,
        fSUM,
        fOP,
        fDBCR
    FROM HICUSTOMERSDEBT
    ORDER BY fDATE DESC
""")
for row in cursor.fetchall():
    print(f"  {row.fDEBTDOCISN}: {row.fDATE} - {row.fOP} {row.fDBCR} - {row.fSUM}")

conn.close()

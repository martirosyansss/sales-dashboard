import pyodbc

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=SalesManagement;"
    "UID=sa;"
    "PWD=Aa123456;"
    "TrustServerCertificate=yes;"
)

cursor = conn.cursor()

# Проверка ВСЕХ продаж (без фильтра по статусу)
cursor.execute("""
    SELECT 
        MIN(fDATE) as MinDate, 
        MAX(fDATE) as MaxDate, 
        COUNT(*) as Total,
        COUNT(DISTINCT fSTATE) as States
    FROM SALES
""")

result = cursor.fetchone()
print(f"Минимальная дата: {result[0]}")
print(f"Максимальная дата: {result[1]}")
print(f"Всего продаж: {result[2]}")
print(f"Разных статусов: {result[3]}")

# Проверка статусов
cursor.execute("""
    SELECT fSTATE, COUNT(*) as Count
    FROM SALES
    GROUP BY fSTATE
    ORDER BY Count DESC
""")
print("\nСтатусы продаж:")
for row in cursor.fetchall():
    print(f"Статус {row[0]}: {row[1]} записей")

# Проверка продаж за последний год
cursor.execute("""
    SELECT 
        FORMAT(fDATE, 'yyyy-MM') as Month,
        COUNT(*) as SalesCount,
        SUM(fTOTALSUM) as TotalSum
    FROM SALES
    WHERE fSTATE = 1
    AND fDATE >= DATEADD(YEAR, -1, GETDATE())
    GROUP BY FORMAT(fDATE, 'yyyy-MM')
    ORDER BY Month DESC
""")

print("\nПродажи за последние месяцы:")
for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]} продаж, сумма: {row[2]:,.0f}")

cursor.close()
conn.close()

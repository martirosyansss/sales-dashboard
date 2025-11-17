"""
Проверка Sales Areas в базе данных
"""
import pyodbc

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

print("=" * 80)
print("SALES AREAS В БАЗЕ ДАННЫХ")
print("=" * 80)

# 1. Проверяем таблицу TREES для SArea
cursor.execute("""
    SELECT fCODE, fCAPTION, fISN
    FROM TREES
    WHERE fTREEID = 'SArea'
    ORDER BY fCODE
""")

print("\n1. Sales Areas из таблицы TREES (fTREEID='SArea'):")
print("-" * 80)
areas_count = 0
for row in cursor.fetchall():
    areas_count += 1
    print(f"  {row.fCODE:<10} - {row.fCAPTION} (ISN: {row.fISN})")

print(f"\nВсего Sales Areas: {areas_count}")

# 2. Проверяем связи менеджеров с территориями
print("\n" + "=" * 80)
print("2. SALESAGENTAREAS - связи менеджеров с территориями:")
print("-" * 80)

cursor.execute("""
    SELECT 
        sa.fSALESAREA,
        COUNT(DISTINCT sa.fSALESAGENTID) as ManagerCount,
        STRING_AGG(ag.fCODE, ', ') as Managers
    FROM SALESAGENTAREAS sa
    INNER JOIN SALESAGENTS ag ON sa.fSALESAGENTID = ag.fID
    WHERE ag.fCLOSED = 0
    GROUP BY sa.fSALESAREA
    ORDER BY sa.fSALESAREA
""")

for row in cursor.fetchall():
    print(f"  {row.fSALESAREA:<10} - Менеджеров: {row.ManagerCount}")
    print(f"             {row.Managers[:100]}")

# 3. Проверяем уникальные значения fTREEID в TREES
print("\n" + "=" * 80)
print("3. Все типы справочников в TREES:")
print("-" * 80)

cursor.execute("""
    SELECT fTREEID, COUNT(*) as RecordCount
    FROM TREES
    GROUP BY fTREEID
    ORDER BY fTREEID
""")

for row in cursor.fetchall():
    print(f"  {row.fTREEID:<20} - записей: {row.RecordCount}")

# 4. Проверяем продажи по территориям
print("\n" + "=" * 80)
print("4. Продажи по территориям (последний месяц):")
print("-" * 80)

cursor.execute("""
    SELECT 
        sa.fSALESAREA,
        COUNT(DISTINCT s.fISN) as SalesCount,
        ISNULL(SUM(s.fTOTALSUM), 0) as TotalSum
    FROM SALES s
    INNER JOIN SALESAGENTS ag ON s.fSALESAGENTID = ag.fID
    INNER JOIN SALESAGENTAREAS sa ON ag.fID = sa.fSALESAGENTID
    WHERE s.fDATE >= DATEADD(month, -1, GETDATE())
        AND s.fSTATE = 2
        AND ag.fCLOSED = 0
    GROUP BY sa.fSALESAREA
    ORDER BY TotalSum DESC
""")

for row in cursor.fetchall():
    print(f"  {row.fSALESAREA:<10} - Продаж: {row.SalesCount:>6}, Сумма: {row.TotalSum:>15,.2f} AMD")

conn.close()
print("\n" + "=" * 80)

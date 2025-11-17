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

print("="*80)
print("ВСЕ ТАБЛИЦЫ С DEBT/REST В НАЗВАНИИ")
print("="*80)

cursor.execute("""
    SELECT TABLE_NAME 
    FROM INFORMATION_SCHEMA.TABLES 
    WHERE TABLE_TYPE = 'BASE TABLE' 
    AND (TABLE_NAME LIKE '%DEBT%' OR TABLE_NAME LIKE '%REST%')
    ORDER BY TABLE_NAME
""")

for row in cursor.fetchall():
    print(f"  - {row.TABLE_NAME}")

print("\n"+"="*80)
print("ПРОВЕРКА ЗНАЧЕНИЙ В КАЖДОЙ ТАБЛИЦЕ ДЛЯ МЕНЕДЖЕРА 3169")
print("="*80)

manager_id = 3169
groups = ('036', '002')
target = 5_289_036.77

# HICUSTOMERSDEBT через DOCUMENTS
query1 = """
    SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as debt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?)
    AND c.fGROUP IN ('036', '002')
"""
cursor.execute(query1, (manager_id,))
val1 = float(cursor.fetchone().debt)
print(f"\n1. HICUSTOMERSDEBT (D-C): {val1:,.2f} AMD - откл {abs(val1-target)/target*100:.1f}%")

# Попробуем найти прямую связь - может есть таблица связывающая менеджера и долг
cursor.execute("""
    SELECT COLUMN_NAME, TABLE_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE COLUMN_NAME LIKE '%SALESAGENT%' OR COLUMN_NAME LIKE '%MANAGER%'
    ORDER BY TABLE_NAME
""")

print("\n"+"="*80)
print("ТАБЛИЦЫ С КОЛОНКАМИ SALESAGENT/MANAGER:")
print("="*80)
for row in cursor.fetchall():
    print(f"  {row.TABLE_NAME}.{row.COLUMN_NAME}")

conn.close()

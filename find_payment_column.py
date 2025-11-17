import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()

print("Структура таблицы SALES (все колонки):")
print("="*80)

cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'SALES'
    ORDER BY ORDINAL_POSITION
""")

for row in cursor.fetchall():
    print(f"{row.COLUMN_NAME:35} {row.DATA_TYPE:20} {'NULL' if row.IS_NULLABLE=='YES' else 'NOT NULL'}")

print("\n" + "="*80)
print("Ищу колонки связанные с оплатой (PAY, CASH, CARD, CREDIT):")
print("="*80)

cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'SALES'
        AND (COLUMN_NAME LIKE '%PAY%' 
            OR COLUMN_NAME LIKE '%CASH%' 
            OR COLUMN_NAME LIKE '%CARD%'
            OR COLUMN_NAME LIKE '%CREDIT%'
            OR COLUMN_NAME LIKE '%BANK%'
            OR COLUMN_NAME LIKE '%DEBT%'
            OR COLUMN_NAME LIKE '%TYPE%')
    ORDER BY COLUMN_NAME
""")

payment_cols = cursor.fetchall()
if payment_cols:
    for row in payment_cols:
        print(f"  {row.COLUMN_NAME:30} {row.DATA_TYPE}")
else:
    print("  Нет колонок связанных с оплатой")

print("\n" + "="*80)
print("Пример данных из продажи A8E0FB21-0F69-40E9-8B5F-2C684377266D:")
print("="*80)

cursor.execute("""
    SELECT TOP 1 *
    FROM SALES
    WHERE fISN = ?
""", ('A8E0FB21-0F69-40E9-8B5F-2C684377266D',))

columns = [column[0] for column in cursor.description]
row = cursor.fetchone()

if row:
    for i, col in enumerate(columns):
        if row[i] is not None and row[i] != '':
            print(f"{col:30} = {row[i]}")

conn.close()

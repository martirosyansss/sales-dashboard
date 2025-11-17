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

print("Структура таблицы SALEDOCDETAILS:")
print("="*80)

cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'SALEDOCDETAILS'
    ORDER BY ORDINAL_POSITION
""")

for row in cursor.fetchall():
    print(f"{row.COLUMN_NAME:30} {row.DATA_TYPE:15} {'NULL' if row.IS_NULLABLE=='YES' else 'NOT NULL'}")

print("\n" + "="*80)
print("Пример данных из SALEDOCDETAILS для продажи AB94B2ED-0E5E-4E07-AD37-09D20CB777AF:")
print("="*80)

cursor.execute("""
    SELECT TOP 3 *
    FROM SALEDOCDETAILS
    WHERE fISN = ?
""", ('AB94B2ED-0E5E-4E07-AD37-09D20CB777AF',))

columns = [column[0] for column in cursor.description]
print("\nКолонки:", ", ".join(columns))

for row in cursor.fetchall():
    print(f"\nСтрока:")
    for i, col in enumerate(columns):
        print(f"  {col}: {row[i]}")

conn.close()

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
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'SALES'
    ORDER BY ORDINAL_POSITION
""")

print("Колонки таблицы SALES:")
print("-" * 60)
for row in cursor.fetchall():
    col_name = row.COLUMN_NAME
    data_type = row.DATA_TYPE
    max_len = row.CHARACTER_MAXIMUM_LENGTH if row.CHARACTER_MAXIMUM_LENGTH else ''
    print(f"{col_name:30} {data_type:15} {max_len}")

print("\n" + "=" * 60)
print("Проверка данных для клиента 1686:")
cursor.execute("""
    SELECT TOP 5 fISN, fDATE, fTOTALSUM, fSTATE
    FROM SALES
    WHERE fCUSTOMERID = 1686
    ORDER BY fDATE DESC
""")
rows = cursor.fetchall()
if rows:
    print(f"Найдено {len(rows)} продаж:")
    for r in rows:
        print(f"  ISN: {r.fISN}, Date: {r.fDATE}, Sum: {r.fTOTALSUM}, State: {r.fSTATE}")
else:
    print("Продажи не найдены")

conn.close()

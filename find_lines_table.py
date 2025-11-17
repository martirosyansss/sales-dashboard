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

print("Поиск таблиц содержащих 'LINE' или 'SALES':")
print("="*80)

cursor.execute("""
    SELECT TABLE_NAME 
    FROM INFORMATION_SCHEMA.TABLES 
    WHERE TABLE_TYPE='BASE TABLE' 
        AND (TABLE_NAME LIKE '%LINE%' OR TABLE_NAME LIKE '%SALE%')
    ORDER BY TABLE_NAME
""")

for row in cursor.fetchall():
    print(f"  {row.TABLE_NAME}")

conn.close()

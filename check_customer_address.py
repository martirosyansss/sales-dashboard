import pyodbc

conn_str = (
    r'DRIVER={ODBC Driver 17 for SQL Server};'
    r'SERVER=192.168.1.3;'
    r'DATABASE=SalesManagement;'
    r'UID=sa;'
    r'PWD=Aa123456;'
    r'TrustServerCertificate=yes;'
)

conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

print("=" * 80)
print("Структура таблицы CUSTOMERS - все колонки")
print("=" * 80)
cursor.execute("SELECT TOP 1 * FROM CUSTOMERS")
columns = [column[0] for column in cursor.description]
for i, col in enumerate(columns, 1):
    print(f"{i:2d}. {col}")

print("\n" + "=" * 80)
print("Проверка данных клиента 11307 (Էդուարդ Ավագյան)")
print("=" * 80)

cursor.execute("""
    SELECT *
    FROM CUSTOMERS
    WHERE fCODE = '11307'
""")

row = cursor.fetchone()
if row:
    for i, col in enumerate(columns):
        value = getattr(row, col, None)
        if value:
            print(f"{col}: {value}")
else:
    print("Клиент не найден")

conn.close()

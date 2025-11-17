import pyodbc

conn_str = (
    r'DRIVER={ODBC Driver 17 for SQL Server};'
    r'SERVER=localhost;'
    r'DATABASE=SalesManagement-;'
    r'UID=sa;'
    r'PWD=Aa123456;'
    r'TrustServerCertificate=yes;'
)

conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

print("=" * 80)
print("Структура таблицы HIRESTCUSTOMERSSUM")
print("=" * 80)
cursor.execute("""
    SELECT TOP 1 * FROM HIRESTCUSTOMERSSUM
""")
columns = [column[0] for column in cursor.description]
print("Колонки:", ', '.join(columns))
row = cursor.fetchone()
if row:
    print("\nПример данных:")
    for col, val in zip(columns, row):
        print(f"  {col}: {val}")

print("\n" + "=" * 80)
print("Проверка связи HIRESTCUSTOMERSSUM с менеджерами")
print("=" * 80)
cursor.execute("""
    SELECT TOP 10
        r.*
    FROM HIRESTCUSTOMERSSUM r
    ORDER BY r.fSUM DESC
""")
print("\nТоп 10 записей по сумме:")
for row in cursor.fetchall():
    print(row)

print("\n" + "=" * 80)
print("Попытка найти связь через CUSTOMERS")
print("=" * 80)
try:
    cursor.execute("""
        SELECT 
            c.fCODE as CustomerCode,
            c.fNAME as CustomerName,
            r.fTYPE,
            r.fSUM,
            d.fCODE as DocCode
        FROM HIRESTCUSTOMERSSUM r
        LEFT JOIN CUSTOMERS c ON c.fID = r.fCUSTOMERID
        LEFT JOIN DOCUMENTS d ON d.fISN = r.fDOCISN
        WHERE r.fSUM > 100000
        ORDER BY r.fSUM DESC
    """)
    print("\nЗаписи с суммой > 100,000:")
    for i, row in enumerate(cursor.fetchall()[:10]):
        print(f"{i+1}. Customer: {row.CustomerCode} {row.CustomerName}, Type: {row.fTYPE}, Sum: {row.fSUM:,.2f}, Doc: {row.DocCode}")
except Exception as e:
    print(f"Ошибка: {e}")

conn.close()

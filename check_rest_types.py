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

print("\n=== 1. HIRESTCUSTOMERSSUM - Структура таблицы ===")
cursor.execute("""
    SELECT TOP 1 * 
    FROM HIRESTCUSTOMERSSUM
""")
columns = [column[0] for column in cursor.description]
print("Колонки:", ", ".join(columns))

print("\n=== 2. Все уникальные fTYPE в HIRESTCUSTOMERSSUM ===")
cursor.execute("""
    SELECT fTYPE, COUNT(*) as Count
    FROM HIRESTCUSTOMERSSUM
    GROUP BY fTYPE
    ORDER BY fTYPE
""")
rows = cursor.fetchall()
for row in rows:
    print(f"Type: '{row[0]}', Count: {row[1]:,}")

print("\n=== 3. Примеры записей для каждого типа ===")
cursor.execute("""
    SELECT DISTINCT TOP 1 fTYPE
    FROM HIRESTCUSTOMERSSUM
    ORDER BY fTYPE
""")
types = [row[0] for row in cursor.fetchall()]

for t in types:
    print(f"\n--- Type '{t}' (первые 3 записи) ---")
    cursor.execute("""
        SELECT TOP 3 
            fCUSTOMER,
            fTYPE,
            fSUM,
            fDATE
        FROM HIRESTCUSTOMERSSUM
        WHERE fTYPE = ?
        ORDER BY fDATE DESC
    """, (t,))
    rows = cursor.fetchall()
    for row in rows:
        print(f"  Customer: {row[0]}, Sum: {row[2]:,.2f}, Date: {row[3]}")

print("\n=== 4. Суммы Type01 и Type02 для Area 105, Groups 002+036 ===")
cursor.execute("""
    SELECT 
        r.fTYPE,
        SUM(r.fSUM) as TotalSum,
        COUNT(*) as RecordCount
    FROM HIRESTCUSTOMERSSUM r
    JOIN CUSTOMERSALESAREAS csa ON r.fCUSTOMER = csa.fCUSTOMER
    WHERE csa.fAREA = '105'
    AND csa.fGROUP IN ('002', '036')
    AND r.fTYPE IN ('01', '02')
    GROUP BY r.fTYPE
    ORDER BY r.fTYPE
""")
rows = cursor.fetchall()
if rows:
    for row in rows:
        print(f"Type: '{row[0]}', Total: {row[1]:,.2f}, Records: {row[2]:,}")
else:
    print("Нет данных для Type 01 и 02 в указанных группах")

print("\n=== 5. Проверка: есть ли вообще данные Type01 и Type02? ===")
cursor.execute("""
    SELECT 
        r.fTYPE,
        SUM(r.fSUM) as TotalSum,
        COUNT(*) as RecordCount
    FROM HIRESTCUSTOMERSSUM r
    WHERE r.fTYPE IN ('01', '02')
    GROUP BY r.fTYPE
    ORDER BY r.fTYPE
""")
rows = cursor.fetchall()
if rows:
    for row in rows:
        print(f"Type: '{row[0]}', Total: {row[1]:,.2f}, Records: {row[2]:,}")
else:
    print("В базе НЕТ записей с Type '01' или '02'!")

conn.close()

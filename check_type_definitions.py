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

print("\n=== 1. HISALESDOCSTYPE - Типы документов ===")
cursor.execute("""
    SELECT fTYPE, fNAME 
    FROM HISALESDOCSTYPE 
    ORDER BY fTYPE
""")
rows = cursor.fetchall()
for row in rows:
    print(f"Type: {row[0]}, Name: {row[1]}")

print("\n=== 2. HIRESTCUSTOMERSSUM - Структура таблицы ===")
cursor.execute("""
    SELECT TOP 1 * 
    FROM HIRESTCUSTOMERSSUM
""")
columns = [column[0] for column in cursor.description]
print("Колонки:", ", ".join(columns))

print("\n=== 3. HIRESTCUSTOMERSSUM - Примеры Type 01 и 02 ===")
cursor.execute("""
    SELECT TOP 10 
        fCUSTOMER,
        fTYPE,
        fSUM,
        fDATE
    FROM HIRESTCUSTOMERSSUM
    WHERE fTYPE IN ('01', '02')
    ORDER BY fDATE DESC
""")
rows = cursor.fetchall()
for row in rows:
    print(f"Customer: {row[0]}, Type: {row[1]}, Sum: {row[2]:,.2f}, Date: {row[3]}")

print("\n=== 4. Все уникальные fTYPE в HIRESTCUSTOMERSSUM ===")
cursor.execute("""
    SELECT DISTINCT fTYPE, COUNT(*) as Count
    FROM HIRESTCUSTOMERSSUM
    GROUP BY fTYPE
    ORDER BY fTYPE
""")
rows = cursor.fetchall()
for row in rows:
    print(f"Type: {row[0]}, Count: {row[1]}")

print("\n=== 5. Суммы Type01 и Type02 для Area 105, Groups 002+036 ===")
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
for row in rows:
    print(f"Type: {row[0]}, Total: {row[1]:,.2f}, Records: {row[2]}")

conn.close()

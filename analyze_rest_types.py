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

print("\n=== 1. Структура HIRESTCUSTOMERSSUM ===")
cursor.execute("SELECT TOP 1 * FROM HIRESTCUSTOMERSSUM")
columns = [column[0] for column in cursor.description]
print("Колонки:", ", ".join(columns))

print("\n=== 2. Примеры Type 01 и 02 ===")
cursor.execute("""
    SELECT TOP 5 
        fDIVISION,
        fCUSTOMERID,
        fTYPE,
        fSUM
    FROM HIRESTCUSTOMERSSUM
    WHERE fTYPE IN ('01', '02')
    ORDER BY fTYPE, fSUM DESC
""")
rows = cursor.fetchall()
for row in rows:
    print(f"Division: {row[0]}, CustomerID: {row[1]}, Type: '{row[2]}', Sum: {row[3]:,.2f}")

print("\n=== 3. Что такое fCUSTOMERID в CUSTOMERS ===")
cursor.execute("""
    SELECT TOP 1 * 
    FROM CUSTOMERS
""")
columns = [column[0] for column in cursor.description]
print("Колонки CUSTOMERS:", ", ".join(columns))

print("\n=== 4. Связь fCUSTOMERID с CUSTOMERS ===")
cursor.execute("""
    SELECT TOP 5
        c.fCODE,
        c.fNAME,
        r.fTYPE,
        r.fSUM
    FROM HIRESTCUSTOMERSSUM r
    JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fTYPE IN ('01', '02')
    ORDER BY r.fSUM DESC
""")
rows = cursor.fetchall()
for row in rows:
    print(f"Customer: {row[0]} - {row[1]}, Type: '{row[2]}', Sum: {row[3]:,.2f}")

print("\n=== 5. Type01 и Type02 для Area 105, Groups 002+036 ===")
cursor.execute("""
    SELECT 
        r.fTYPE,
        SUM(r.fSUM) as TotalSum,
        COUNT(*) as RecordCount
    FROM HIRESTCUSTOMERSSUM r
    JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    JOIN CUSTOMERSALESAREAS csa ON c.fCODE = csa.fCUSTOMER
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
    print("Нет данных для Type 01 и 02 в группах 002, 036")

print("\n=== 6. Общие суммы Type01 и Type02 ===")
cursor.execute("""
    SELECT 
        fTYPE,
        SUM(fSUM) as TotalSum,
        COUNT(*) as RecordCount,
        AVG(fSUM) as AvgSum
    FROM HIRESTCUSTOMERSSUM
    WHERE fTYPE IN ('01', '02')
    GROUP BY fTYPE
    ORDER BY fTYPE
""")
rows = cursor.fetchall()
for row in rows:
    print(f"Type: '{row[0]}', Total: {row[1]:,.2f}, Records: {row[2]:,}, Avg: {row[3]:,.2f}")

conn.close()

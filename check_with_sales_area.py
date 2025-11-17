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

manager_code = 'A006/6'
customer_groups = ['002', '036']
sales_area = '106'

cursor.execute("SELECT fID FROM SALESAGENTS WHERE fCODE = ?", manager_code)
manager_id = cursor.fetchone().fID

expected_debt = 6297356.55
expected_rest01 = -48220.11  # Это на самом деле Type02!
expected_rest02 = -236762.19  # Это на самом деле Type01!
expected_total = 6012374.25

print("=" * 80)
print(f"ПРОВЕРКА С SALES AREA {sales_area}")
print("=" * 80)

# Проверяем структуру SALESAGENTAREAS
cursor.execute("""
    SELECT fSALESAREA, fDEFAULT
    FROM SALESAGENTAREAS
    WHERE fSALESAGENTID = ?
""", (manager_id,))
print(f"\nSales Areas для менеджера {manager_code}:")
for row in cursor.fetchall():
    print(f"  Area: {row.fSALESAREA}, Default: {row.fDEFAULT}")

# Долг с фильтром по Sales Area
print("\n" + "=" * 80)
print("ВАРИАНТ: Фильтр долга по Sales Area через CUSTOMERS")
print("=" * 80)

# Проверяем есть ли в CUSTOMERS поле fREGION (Sales Area)
cursor.execute("SELECT TOP 1 * FROM CUSTOMERS")
customer_columns = [column[0] for column in cursor.description]
print(f"\nПоля в CUSTOMERS: {', '.join([c for c in customer_columns if 'REGION' in c.upper() or 'AREA' in c.upper()])}")

# Пробуем фильтровать по fREGION
cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fSALESAGENTID = ?
        AND c.fREGION = ?
""", (manager_id, sales_area))
debt_with_area = float(cursor.fetchone().DebtFromDocs)

print(f"\nДолг с фильтром по Sales Area {sales_area}: {debt_with_area:,.2f}")
print(f"Ожидаемый долг: {expected_debt:,.2f}")
print(f"Разница: {abs(debt_with_area - expected_debt):,.2f}")

# Остатки С фильтром по группам (и ПОМЕНЯТЬ МЕСТАМИ!)
cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '02'
    AND c.fGROUP IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest_type02_as_01 = float(cursor.fetchone().RestSum)

cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '01'
    AND c.fGROUP IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest_type01_as_02 = float(cursor.fetchone().RestSum)

total_with_area = debt_with_area + rest_type02_as_01 + rest_type01_as_02

print(f"\n{'Компонент':<40} {'Ожидаемое':>20} {'Фактическое':>20} {'Разница':>15}")
print("-" * 100)
print(f"{'Долг (Sales Area ' + sales_area + ')':<40} {expected_debt:>20,.2f} {debt_with_area:>20,.2f} {abs(expected_debt - debt_with_area):>15,.2f}")
print(f"{'Type01 (из Type02, группы 002,036)':<40} {expected_rest01:>20,.2f} {rest_type02_as_01:>20,.2f} {abs(expected_rest01 - rest_type02_as_01):>15,.2f}")
print(f"{'Type02 (из Type01, группы 002,036)':<40} {expected_rest02:>20,.2f} {rest_type01_as_02:>20,.2f} {abs(expected_rest02 - rest_type01_as_02):>15,.2f}")
print("-" * 100)
print(f"{'ИТОГО':<40} {expected_total:>20,.2f} {total_with_area:>20,.2f} {abs(expected_total - total_with_area):>15,.2f}")

if abs(total_with_area - expected_total) < 1:
    print("\n✓✓✓ ПОЛНОЕ СОВПАДЕНИЕ!")
elif abs(total_with_area - expected_total) < 1000:
    print("\n✓ Очень близко! (разница < 1000 AMD)")
else:
    percent_diff = (abs(total_with_area - expected_total) / expected_total) * 100
    print(f"\n✗ Разница {abs(total_with_area - expected_total):,.2f} AMD ({percent_diff:.2f}%)")

# Анализ - сколько долга в разных Sales Areas
print("\n" + "=" * 80)
print("АНАЛИЗ: Долг по Sales Areas")
print("=" * 80)

cursor.execute("""
    SELECT 
        c.fREGION,
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs,
        COUNT(DISTINCT c.fID) as CustomerCount
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fSALESAGENTID = ?
    GROUP BY c.fREGION
    HAVING SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) > 100000
    ORDER BY DebtFromDocs DESC
""", (manager_id,))

print(f"\n{'Sales Area':<15} {'Долг':>20} {'Клиентов':>12}")
print("-" * 50)
for row in cursor.fetchall():
    print(f"{row.fREGION:<15} {float(row.DebtFromDocs):>20,.2f} {row.CustomerCount:>12}")

conn.close()

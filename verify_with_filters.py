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

# Находим ID менеджера
cursor.execute("SELECT fID, fCODE, fNAME FROM SALESAGENTS WHERE fCODE = ?", manager_code)
manager_row = cursor.fetchone()

if not manager_row:
    print(f"Менеджер {manager_code} не найден!")
    exit()

manager_id = manager_row.fID
manager_name = manager_row.fNAME

print("=" * 80)
print(f"ПРОВЕРКА С ФИЛЬТРАМИ: {manager_code} - {manager_name}")
print("=" * 80)
print(f"Customer Groups: {', '.join(customer_groups)}")
print(f"Sales Area: {sales_area}")

# 1. БЕЗ ФИЛЬТРОВ
print("\n" + "=" * 80)
print("1. БЕЗ ФИЛЬТРОВ")
print("=" * 80)

cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs,
        (SELECT ISNULL(SUM(r.fSUM), 0) FROM HIRESTCUSTOMERSSUM r
         WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
         AND r.fTYPE = '01') as RestType01,
        (SELECT ISNULL(SUM(r.fSUM), 0) FROM HIRESTCUSTOMERSSUM r
         WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
         AND r.fTYPE = '02') as RestType02
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    WHERE doc.fSALESAGENTID = ?
""", (manager_id, manager_id, manager_id))

row = cursor.fetchone()
debt_no_filter = float(row.DebtFromDocs)
rest01_no_filter = float(row.RestType01)
rest02_no_filter = float(row.RestType02)
total_no_filter = debt_no_filter + rest01_no_filter + rest02_no_filter

print(f"Долг из документов: {debt_no_filter:,.2f} AMD")
print(f"Остатки Type 01: {rest01_no_filter:,.2f} AMD")
print(f"Остатки Type 02: {rest02_no_filter:,.2f} AMD")
print(f"ИТОГО: {total_no_filter:,.2f} AMD")

# 2. С ФИЛЬТРОМ ПО ГРУППАМ КЛИЕНТОВ
print("\n" + "=" * 80)
print("2. С ФИЛЬТРОМ ПО ГРУППАМ КЛИЕНТОВ (002, 036)")
print("=" * 80)

cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs,
        (SELECT ISNULL(SUM(r.fSUM), 0) FROM HIRESTCUSTOMERSSUM r
         INNER JOIN CUSTOMERS c2 ON r.fCUSTOMERID = c2.fID
         WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
         AND r.fTYPE = '01'
         AND c2.fGROUP IN (?, ?)) as RestType01,
        (SELECT ISNULL(SUM(r.fSUM), 0) FROM HIRESTCUSTOMERSSUM r
         INNER JOIN CUSTOMERS c2 ON r.fCUSTOMERID = c2.fID
         WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
         AND r.fTYPE = '02'
         AND c2.fGROUP IN (?, ?)) as RestType02
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fSALESAGENTID = ?
        AND c.fGROUP IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1], 
      manager_id, customer_groups[0], customer_groups[1],
      manager_id, customer_groups[0], customer_groups[1]))

row = cursor.fetchone()
debt_group_filter = float(row.DebtFromDocs)
rest01_group_filter = float(row.RestType01)
rest02_group_filter = float(row.RestType02)
total_group_filter = debt_group_filter + rest01_group_filter + rest02_group_filter

print(f"Долг из документов: {debt_group_filter:,.2f} AMD")
print(f"Остатки Type 01: {rest01_group_filter:,.2f} AMD")
print(f"Остатки Type 02: {rest02_group_filter:,.2f} AMD")
print(f"ИТОГО: {total_group_filter:,.2f} AMD")

# Сравнение с ожидаемыми значениями
print("\n" + "=" * 80)
print("СРАВНЕНИЕ С ОЖИДАЕМЫМИ ЗНАЧЕНИЯМИ")
print("=" * 80)

expected_debt = 6297356.55
expected_rest01 = -48220.11
expected_rest02 = -236762.19
expected_total = 6012374.25

print(f"\n{'Компонент':<30} {'Ожидаемое':>20} {'Фактическое':>20} {'Разница':>15}")
print("-" * 90)
print(f"{'Долг из документов':<30} {expected_debt:>20,.2f} {debt_group_filter:>20,.2f} {abs(expected_debt - debt_group_filter):>15,.2f}")
print(f"{'Остатки Type 01':<30} {expected_rest01:>20,.2f} {rest01_group_filter:>20,.2f} {abs(expected_rest01 - rest01_group_filter):>15,.2f}")
print(f"{'Остатки Type 02':<30} {expected_rest02:>20,.2f} {rest02_group_filter:>20,.2f} {abs(expected_rest02 - rest02_group_filter):>15,.2f}")
print("-" * 90)
print(f"{'ИТОГО':<30} {expected_total:>20,.2f} {total_group_filter:>20,.2f} {abs(expected_total - total_group_filter):>15,.2f}")

if abs(total_group_filter - expected_total) < 1:
    print("\n✓✓✓ СОВПАДАЕТ С ОЖИДАЕМОЙ СУММОЙ!")
elif abs(total_group_filter - expected_total) < 1000:
    print(f"\n✓ Очень близко (разница < 1000 AMD)")
else:
    diff_percent = abs((total_group_filter - expected_total) / expected_total) * 100
    print(f"\n✗ Отличие {diff_percent:.2f}%")

conn.close()

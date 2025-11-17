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

cursor.execute("SELECT fID FROM SALESAGENTS WHERE fCODE = ?", manager_code)
manager_id = cursor.fetchone().fID

expected_debt = 6297356.55
expected_rest01 = -48220.11
expected_rest02 = -236762.19
expected_total = 6012374.25

print("=" * 80)
print("ПРОВЕРКА: Может Type01 и Type02 перепутаны местами?")
print("=" * 80)

# Долг БЕЗ фильтра
cursor.execute("""
    SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    WHERE doc.fSALESAGENTID = ?
""", (manager_id,))
debt_total = float(cursor.fetchone().DebtFromDocs)

# Остатки WITH группового фильтра
cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '01'
    AND c.fGROUP IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest01_filtered = float(cursor.fetchone().RestSum)

cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '02'
    AND c.fGROUP IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest02_filtered = float(cursor.fetchone().RestSum)

print(f"\nФактические значения:")
print(f"  Долг: {debt_total:,.2f}")
print(f"  Type 01: {rest01_filtered:,.2f}")
print(f"  Type 02: {rest02_filtered:,.2f}")

print(f"\nОжидаемые значения:")
print(f"  Долг: {expected_debt:,.2f}")
print(f"  Type 01 (ожидалось): {expected_rest01:,.2f}")
print(f"  Type 02 (ожидалось): {expected_rest02:,.2f}")

print("\n" + "=" * 80)
print("ВАРИАНТ 1: Type01 и Type02 как есть")
print("=" * 80)
total_v1 = debt_total + rest01_filtered + rest02_filtered
print(f"Долг + Type01 + Type02 = {total_v1:,.2f}")
print(f"Разница с ожидаемым: {abs(total_v1 - expected_total):,.2f}")

print("\n" + "=" * 80)
print("ВАРИАНТ 2: ПОМЕНЯТЬ МЕСТАМИ Type01 и Type02")
print("=" * 80)
# Используем Type02 как Type01 и наоборот
total_v2 = debt_total + rest02_filtered + rest01_filtered  # Порядок тот же, но смысл другой
rest01_swapped = rest02_filtered  # -47,715.96
rest02_swapped = rest01_filtered  # -243,081.79
print(f"Долг: {debt_total:,.2f}")
print(f"Type01 (взяли из Type02): {rest01_swapped:,.2f} (ожидалось {expected_rest01:,.2f}, разница: {abs(rest01_swapped - expected_rest01):,.2f})")
print(f"Type02 (взяли из Type01): {rest02_swapped:,.2f} (ожидалось {expected_rest02:,.2f}, разница: {abs(rest02_swapped - expected_rest02):,.2f})")
print(f"ИТОГО: {total_v2:,.2f} (разница с ожидаемым: {abs(total_v2 - expected_total):,.2f})")

print("\n" + "=" * 80)
print("ВАРИАНТ 3: ОТРИЦАТЕЛЬНОЕ значение Type01, ПОЛОЖИТЕЛЬНОЕ Type02")
print("=" * 80)
rest01_negated = -abs(rest01_filtered)
rest02_positived = abs(rest02_filtered)
total_v3 = debt_total + rest01_negated + rest02_positived
print(f"Долг + (-|Type01|) + |Type02| = {total_v3:,.2f}")
print(f"Разница с ожидаемым: {abs(total_v3 - expected_total):,.2f}")

print("\n" + "=" * 80)
print("ВАРИАНТ 4: Проверка остатков БЕЗ группового фильтра")
print("=" * 80)

cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '01'
""", (manager_id,))
rest01_no_filter = float(cursor.fetchone().RestSum)

cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '02'
""", (manager_id,))
rest02_no_filter = float(cursor.fetchone().RestSum)

total_v4 = debt_total + rest01_no_filter + rest02_no_filter
print(f"Type01 БЕЗ фильтра: {rest01_no_filter:,.2f}")
print(f"Type02 БЕЗ фильтра: {rest02_no_filter:,.2f}")
print(f"ИТОГО: {total_v4:,.2f}")

conn.close()

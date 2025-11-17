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

print("=" * 80)
print(f"РАЗНЫЕ ВАРИАНТЫ ФИЛЬТРАЦИИ ДЛЯ {manager_code}")
print("=" * 80)

expected_debt = 6297356.55
expected_rest01 = -48220.11
expected_rest02 = -236762.19
expected_total = 6012374.25

# ВАРИАНТ 1: Фильтр только на остатки, долг БЕЗ фильтра
print("\nВАРИАНТ 1: Долг БЕЗ фильтра, остатки С фильтром по группам")
print("-" * 80)

cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    WHERE doc.fSALESAGENTID = ?
""", (manager_id,))
debt_v1 = float(cursor.fetchone().DebtFromDocs)

cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '01'
    AND c.fGROUP IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest01_v1 = float(cursor.fetchone().RestSum)

cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '02'
    AND c.fGROUP IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest02_v1 = float(cursor.fetchone().RestSum)

total_v1 = debt_v1 + rest01_v1 + rest02_v1

print(f"Долг: {debt_v1:,.2f} (ожидалось {expected_debt:,.2f}, разница: {abs(debt_v1 - expected_debt):,.2f})")
print(f"Type01: {rest01_v1:,.2f} (ожидалось {expected_rest01:,.2f}, разница: {abs(rest01_v1 - expected_rest01):,.2f})")
print(f"Type02: {rest02_v1:,.2f} (ожидалось {expected_rest02:,.2f}, разница: {abs(rest02_v1 - expected_rest02):,.2f})")
print(f"ИТОГО: {total_v1:,.2f} (ожидалось {expected_total:,.2f}, разница: {abs(total_v1 - expected_total):,.2f})")

# ВАРИАНТ 2: Исключить НЕ входящих в группы (разница!)
print("\nВАРИАНТ 2: Долг ИСКЛЮЧАЯ клиентов НЕ из групп 002,036")
print("-" * 80)

cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fSALESAGENTID = ?
        AND c.fGROUP NOT IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
debt_v2 = float(cursor.fetchone().DebtFromDocs)

cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '01'
    AND c.fGROUP NOT IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest01_v2 = float(cursor.fetchone().RestSum)

cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (SELECT DISTINCT doc2.fCUSTOMERID FROM DOCUMENTS doc2 WHERE doc2.fSALESAGENTID = ?)
    AND r.fTYPE = '02'
    AND c.fGROUP NOT IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest02_v2 = float(cursor.fetchone().RestSum)

total_v2 = debt_v2 + rest01_v2 + rest02_v2

print(f"Долг: {debt_v2:,.2f} (ожидалось {expected_debt:,.2f}, разница: {abs(debt_v2 - expected_debt):,.2f})")
print(f"Type01: {rest01_v2:,.2f} (ожидалось {expected_rest01:,.2f}, разница: {abs(rest01_v2 - expected_rest01):,.2f})")
print(f"Type02: {rest02_v2:,.2f} (ожидалось {expected_rest02:,.2f}, разница: {abs(rest02_v2 - expected_rest02):,.2f})")
print(f"ИТОГО: {total_v2:,.2f} (ожидалось {expected_total:,.2f}, разница: {abs(total_v2 - expected_total):,.2f})")

# ВАРИАНТ 3: Проверим какие группы дают нужную разницу
print("\nВАРИАНТ 3: Анализ - сколько долга в каждой группе")
print("-" * 80)

cursor.execute("""
    SELECT 
        c.fGROUP,
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs,
        COUNT(DISTINCT c.fID) as CustomerCount
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fSALESAGENTID = ?
    GROUP BY c.fGROUP
    ORDER BY DebtFromDocs DESC
""", (manager_id,))

print(f"\n{'Группа':<10} {'Долг':>20} {'Клиентов':>10}")
print("-" * 45)
for row in cursor.fetchall()[:20]:
    print(f"{row.fGROUP:<10} {float(row.DebtFromDocs):>20,.2f} {row.CustomerCount:>10}")

# Проверяем разницу между полным долгом и долгом из групп 002,036
total_debt_all_groups = debt_v1
debt_in_groups = 5651376.01  # Из предыдущей проверки
debt_outside_groups = total_debt_all_groups - debt_in_groups

print(f"\nПолный долг (все группы): {total_debt_all_groups:,.2f}")
print(f"Долг в группах 002,036: {debt_in_groups:,.2f}")
print(f"Долг ВНЕ групп 002,036: {debt_outside_groups:,.2f}")
print(f"\nНужная цифра долга: {expected_debt:,.2f}")
print(f"Разница от полного: {abs(total_debt_all_groups - expected_debt):,.2f}")

conn.close()

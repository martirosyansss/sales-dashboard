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
print("ПОЛНЫЙ РАСЧЕТ ДОЛГА ДЛЯ МЕНЕДЖЕРА A006")
print("=" * 80)

# 1. Долг из HICUSTOMERSDEBT (документы)
query1 = """
SELECT 
    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
FROM HICUSTOMERSDEBT d
INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
INNER JOIN SALESAGENTS sa ON doc.fSALESAGENTID = sa.fID
WHERE sa.fCODE = 'A006'
"""
cursor.execute(query1)
debt_from_docs = cursor.fetchone().DebtFromDocs
print(f"1. Долг из HICUSTOMERSDEBT: {debt_from_docs:,.2f} AMD")

# 2. Остатки из HIRESTCUSTOMERSSUM через клиентов
query2 = """
SELECT 
    ISNULL(SUM(r.fSUM), 0) as RestSum
FROM HIRESTCUSTOMERSSUM r
INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
INNER JOIN SALESAGENTS sa ON c.fSALESMANAGERID = sa.fID
WHERE sa.fCODE = 'A006'
    AND r.fTYPE = '01'
"""
cursor.execute(query2)
rest_type_01 = cursor.fetchone().RestSum
print(f"2. Остатки Type 01 (HIRESTCUSTOMERSSUM): {rest_type_01:,.2f} AMD")

query3 = """
SELECT 
    ISNULL(SUM(r.fSUM), 0) as RestSum
FROM HIRESTCUSTOMERSSUM r
INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
INNER JOIN SALESAGENTS sa ON c.fSALESMANAGERID = sa.fID
WHERE sa.fCODE = 'A006'
    AND r.fTYPE = '02'
"""
cursor.execute(query3)
rest_type_02 = cursor.fetchone().RestSum
print(f"3. Остатки Type 02 (HIRESTCUSTOMERSSUM): {rest_type_02:,.2f} AMD")

total = debt_from_docs + rest_type_01 + rest_type_02
print(f"\n{'=' * 50}")
print(f"ИТОГО: {total:,.2f} AMD")
print(f"\nОжидаемая сумма: 6,012,374.25 AMD")
print(f"Разница: {6012374.25 - total:,.2f} AMD")

if abs(total - 6012374.25) < 1:
    print("\n✓ СОВПАДАЕТ!")
else:
    diff_percent = abs((total - 6012374.25) / 6012374.25 * 100)
    print(f"\n✗ Отличие: {diff_percent:.2f}%")

print("\n" + "=" * 80)
print("ДЕТАЛЬНАЯ ПРОВЕРКА: Сколько клиентов у менеджера A006?")
print("=" * 80)
cursor.execute("""
    SELECT COUNT(*) as CustomerCount
    FROM CUSTOMERS c
    INNER JOIN SALESAGENTS sa ON c.fSALESMANAGERID = sa.fID
    WHERE sa.fCODE = 'A006'
""")
customer_count = cursor.fetchone().CustomerCount
print(f"Клиентов у A006: {customer_count}")

cursor.execute("""
    SELECT COUNT(DISTINCT r.fCUSTOMERID) as CustomersWithRest
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN SALESAGENTS sa ON c.fSALESMANAGERID = sa.fID
    WHERE sa.fCODE = 'A006'
""")
customers_with_rest = cursor.fetchone().CustomersWithRest
print(f"Клиентов с остатками: {customers_with_rest}")

conn.close()

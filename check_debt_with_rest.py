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
print("ПОИСК ПРАВИЛЬНОЙ СВЯЗИ CUSTOMERS -> SALESAGENTS")
print("=" * 80)

# Смотрим на структуру DOCUMENTS - там точно есть связь с менеджером
cursor.execute("""
    SELECT TOP 5
        d.fCUSTOMERID,
        d.fSALESAGENTID,
        sa.fCODE as ManagerCode,
        sa.fNAME as ManagerName
    FROM DOCUMENTS d
    INNER JOIN SALESAGENTS sa ON d.fSALESAGENTID = sa.fID
    WHERE sa.fCODE = 'A006'
    ORDER BY d.fDATE DESC
""")
print("Связь в DOCUMENTS работает:")
for row in cursor.fetchall():
    print(f"  CustomerID: {row.fCUSTOMERID}, ManagerID: {row.fSALESAGENTID}, Code: {row.ManagerCode}")

# Теперь проверим: есть ли в CUSTOMERS прямая связь или только через DOCUMENTS?
print("\n" + "=" * 80)
print("Попытка найти менеджера через DOCUMENTS")
print("=" * 80)

query = """
SELECT 
    sa.fCODE,
    sa.fNAME,
    (
        SELECT ISNULL(SUM(CASE WHEN hd.fDBCR = 'D' THEN hd.fSUM ELSE -hd.fSUM END), 0)
        FROM HICUSTOMERSDEBT hd
        INNER JOIN DOCUMENTS doc ON hd.fDEBTDOCISN = doc.fISN
        WHERE doc.fSALESAGENTID = sa.fID
    ) as DebtFromDocs,
    (
        SELECT ISNULL(SUM(r.fSUM), 0)
        FROM HIRESTCUSTOMERSSUM r
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT d.fCUSTOMERID
            FROM DOCUMENTS d
            WHERE d.fSALESAGENTID = sa.fID
        )
        AND r.fTYPE = '01'
    ) as RestType01,
    (
        SELECT ISNULL(SUM(r.fSUM), 0)
        FROM HIRESTCUSTOMERSSUM r
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT d.fCUSTOMERID
            FROM DOCUMENTS d
            WHERE d.fSALESAGENTID = sa.fID
        )
        AND r.fTYPE = '02'
    ) as RestType02
FROM SALESAGENTS sa
WHERE sa.fCODE = 'A006'
"""

cursor.execute(query)
row = cursor.fetchone()

print(f"Менеджер: {row.fCODE} - {row.fNAME}")
print(f"\n1. Долг из HICUSTOMERSDEBT: {row.DebtFromDocs:,.2f} AMD")
print(f"2. Остатки Type 01: {row.RestType01:,.2f} AMD")
print(f"3. Остатки Type 02: {row.RestType02:,.2f} AMD")

total = float(row.DebtFromDocs) + float(row.RestType01) + float(row.RestType02)
print(f"\n{'=' * 50}")
print(f"ИТОГО: {total:,.2f} AMD")
print(f"\nОжидаемая сумма: 6,012,374.25 AMD")
print(f"Разница: {6012374.25 - total:,.2f} AMD")

if abs(total - 6012374.25) < 1:
    print("\n✓✓✓ СОВПАДАЕТ! ✓✓✓")
else:
    diff_percent = abs((total - 6012374.25) / 6012374.25 * 100)
    print(f"\n✗ Отличие: {diff_percent:.2f}%")

print("\n" + "=" * 80)
print("Детали HIRESTCUSTOMERSSUM для клиентов менеджера A006")
print("=" * 80)
cursor.execute("""
    SELECT 
        r.fTYPE,
        COUNT(*) as RecordCount,
        SUM(r.fSUM) as TotalSum,
        MIN(r.fSUM) as MinSum,
        MAX(r.fSUM) as MaxSum
    FROM HIRESTCUSTOMERSSUM r
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT d.fCUSTOMERID
        FROM DOCUMENTS d
        INNER JOIN SALESAGENTS sa ON d.fSALESAGENTID = sa.fID
        WHERE sa.fCODE = 'A006'
    )
    GROUP BY r.fTYPE
""")
for row in cursor.fetchall():
    print(f"Type {row.fTYPE}: {row.RecordCount} записей, сумма: {row.TotalSum:,.2f} AMD, мин: {row.MinSum:,.2f}, макс: {row.MaxSum:,.2f}")

conn.close()

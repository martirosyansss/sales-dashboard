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

# Находим ID менеджера A006/6
cursor.execute("SELECT fID, fCODE, fNAME FROM SALESAGENTS WHERE fCODE = 'A006/6'")
manager_row = cursor.fetchone()

if not manager_row:
    print("Менеджер A006/6 не найден!")
    exit()

manager_id = manager_row.fID
manager_code = manager_row.fCODE
manager_name = manager_row.fNAME

print("=" * 80)
print(f"ПРОВЕРКА ОБНОВЛЕННОГО РАСЧЕТА ДЛЯ: {manager_code} - {manager_name}")
print("=" * 80)

debt_query = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs,
        (
            SELECT ISNULL(SUM(r.fSUM), 0)
            FROM HIRESTCUSTOMERSSUM r
            WHERE r.fCUSTOMERID IN (
                SELECT DISTINCT doc2.fCUSTOMERID
                FROM DOCUMENTS doc2
                WHERE doc2.fSALESAGENTID = ?
            )
            AND r.fTYPE = '01'
        ) as RestType01,
        (
            SELECT ISNULL(SUM(r.fSUM), 0)
            FROM HIRESTCUSTOMERSSUM r
            WHERE r.fCUSTOMERID IN (
                SELECT DISTINCT doc2.fCUSTOMERID
                FROM DOCUMENTS doc2
                WHERE doc2.fSALESAGENTID = ?
            )
            AND r.fTYPE = '02'
        ) as RestType02
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fSALESAGENTID = ?
"""

cursor.execute(debt_query, (manager_id, manager_id, manager_id))
debt_row = cursor.fetchone()

if debt_row:
    debt_from_docs = float(debt_row.DebtFromDocs) if debt_row.DebtFromDocs else 0
    rest_type_01 = float(debt_row.RestType01) if debt_row.RestType01 else 0
    rest_type_02 = float(debt_row.RestType02) if debt_row.RestType02 else 0
    total_debt = debt_from_docs + rest_type_01 + rest_type_02
    
    print(f"\nКомпоненты долга:")
    print(f"  Долг из документов (HICUSTOMERSDEBT): {debt_from_docs:>20,.2f} AMD")
    print(f"  Остатки Type 01:                       {rest_type_01:>20,.2f} AMD")
    print(f"  Остатки Type 02:                       {rest_type_02:>20,.2f} AMD")
    print(f"  {'-' * 60}")
    print(f"  ИТОГО (новая формула):                 {total_debt:>20,.2f} AMD")
    
    print(f"\n{'=' * 60}")
    print(f"  Ожидаемая сумма:                       {6012374.25:>20,.2f} AMD")
    diff = abs(total_debt - 6012374.25)
    percent = (diff / 6012374.25) * 100
    print(f"  Разница:                               {diff:>20,.2f} AMD ({percent:.2f}%)")
    
    if diff < 1000:
        print("\n✓✓✓ СОВПАДАЕТ С ОЖИДАЕМОЙ СУММОЙ!")
    elif diff < 100000:
        print(f"\n✓ Близко к ожидаемой сумме (в пределах {diff/1000:.1f}k)")
    else:
        print(f"\n✗ Значительное отличие")

conn.close()

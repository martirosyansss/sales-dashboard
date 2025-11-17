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
print("1. Долг БЕЗ фильтра исключенных клиентов")
print("=" * 80)
query1 = """
SELECT 
    sa.fCODE,
    sa.fNAME,
    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as TotalDebt
FROM HICUSTOMERSDEBT d
INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
INNER JOIN SALESAGENTS sa ON doc.fSALESAGENTID = sa.fID
WHERE sa.fCODE = 'A006'
GROUP BY sa.fCODE, sa.fNAME
"""
cursor.execute(query1)
row = cursor.fetchone()
print(f"Total Debt (все клиенты): {row.TotalDebt:,.2f} AMD\n")

print("=" * 80)
print("2. Проверка: есть ли таблица HIRESTCUSTOMERSSUM (остатки)?")
print("=" * 80)
try:
    query2 = """
    SELECT 
        sa.fCODE,
        sa.fNAME,
        ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN SALESAGENTS sa ON c.fSALESAGENTID = sa.fID
    WHERE sa.fCODE = 'A006'
        AND r.fTYPE = '01'
    GROUP BY sa.fCODE, sa.fNAME
    """
    cursor.execute(query2)
    row = cursor.fetchone()
    if row:
        print(f"Rest Sum Type 01: {row.RestSum:,.2f} AMD")
    
    query3 = """
    SELECT 
        sa.fCODE,
        sa.fNAME,
        ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN SALESAGENTS sa ON c.fSALESAGENTID = sa.fID
    WHERE sa.fCODE = 'A006'
        AND r.fTYPE = '02'
    GROUP BY sa.fCODE, sa.fNAME
    """
    cursor.execute(query3)
    row = cursor.fetchone()
    if row:
        print(f"Rest Sum Type 02: {row.RestSum:,.2f} AMD\n")
except Exception as e:
    print(f"Ошибка: {e}\n")

print("=" * 80)
print("3. Комбинированный расчет (HICUSTOMERSDEBT + HIRESTCUSTOMERSSUM)")
print("=" * 80)
try:
    query4 = """
    SELECT 
        sa.fCODE,
        sa.fNAME,
        (
            SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0)
            FROM HICUSTOMERSDEBT d
            INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
            WHERE doc.fSALESAGENTID = sa.fID
        ) as DebtFromDocs,
        (
            SELECT ISNULL(SUM(r.fSUM), 0)
            FROM HIRESTCUSTOMERSSUM r
            INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
            WHERE c.fSALESAGENTID = sa.fID AND r.fTYPE = '01'
        ) as RestType01,
        (
            SELECT ISNULL(SUM(r.fSUM), 0)
            FROM HIRESTCUSTOMERSSUM r
            INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
            WHERE c.fSALESAGENTID = sa.fID AND r.fTYPE = '02'
        ) as RestType02
    FROM SALESAGENTS sa
    WHERE sa.fCODE = 'A006'
    """
    cursor.execute(query4)
    row = cursor.fetchone()
    if row:
        total = row.DebtFromDocs + row.RestType01 + row.RestType02
        print(f"Debt from Documents: {row.DebtFromDocs:,.2f} AMD")
        print(f"Rest Type 01:        {row.RestType01:,.2f} AMD")
        print(f"Rest Type 02:        {row.RestType02:,.2f} AMD")
        print(f"{'=' * 50}")
        print(f"TOTAL:               {total:,.2f} AMD")
        print(f"\nОжидаемая сумма:     6,012,374.25 AMD")
        print(f"Разница:             {6012374.25 - total:,.2f} AMD\n")
except Exception as e:
    print(f"Ошибка: {e}\n")

conn.close()

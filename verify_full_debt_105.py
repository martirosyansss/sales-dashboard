import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

print("\n=== FULL DEBT CALCULATION: Area 105, Groups 002+036, End of Oct 2025 ===")

# 1. Долг из документов (d.fDATE < '2025-11-01')
query_debt = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDATE < '2025-11-01'
"""
cursor.execute(query_debt)
debt_from_docs = cursor.fetchone().DebtFromDocs
print(f"Debt from docs (d.fDATE < 2025-11-01): {debt_from_docs:,.2f}")

# 2. Остатки Type01 и Type02
query_rest = """
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
"""
cursor.execute(query_rest)
rest_row = cursor.fetchone()
type01 = rest_row.Type01
type02 = rest_row.Type02

print(f"Type01: {type01:,.2f}")
print(f"Type02: {type02:,.2f}")

# 3. Итоговый долг
final_debt = debt_from_docs - abs(type01) - abs(type02)
print(f"\nFinal Debt = {debt_from_docs:,.2f} - |{type01:,.2f}| - |{type02:,.2f}| = {final_debt:,.2f}")

print(f"\nExpected value: 2,435,799.90")

# 4. Проверка через doc.fDATE
query_debt_doc = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND doc.fDATE < '2025-11-01'
"""
cursor.execute(query_debt_doc)
debt_from_docs_v2 = cursor.fetchone().DebtFromDocs
final_debt_v2 = debt_from_docs_v2 - abs(type01) - abs(type02)
print(f"\nWith doc.fDATE: {debt_from_docs_v2:,.2f} - |{type01:,.2f}| - |{type02:,.2f}| = {final_debt_v2:,.2f}")

conn.close()

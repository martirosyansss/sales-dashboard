import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

print("\n=== DEBT VERIFICATION: Area 105, Groups 002+036, End of Oct 2025 ===")

# 1. Долг из HICUSTOMERSDEBT (как в текущем коде)
query_current = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as total_debt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
"""
cursor.execute(query_current)
current_method = cursor.fetchone().total_debt
print(f"Current method (all history): {current_method:,.2f}")

# 2. Долг до конца октября 2025
query_oct_end = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as total_debt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDATE < '2025-11-01'
"""
cursor.execute(query_oct_end)
oct_end = cursor.fetchone().total_debt
print(f"End of Oct 2025 (d.fDATE < '2025-11-01'): {oct_end:,.2f}")

# 3. Долг на конец октября через doc.fDATE
query_doc_date = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as total_debt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND doc.fDATE < '2025-11-01'
"""
cursor.execute(query_doc_date)
doc_date = cursor.fetchone().total_debt
print(f"End of Oct 2025 (doc.fDATE < '2025-11-01'): {doc_date:,.2f}")

# 4. Долг только за октябрь
query_oct_only = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as total_debt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDATE >= '2025-10-01' AND d.fDATE < '2025-11-01'
"""
cursor.execute(query_oct_only)
oct_only = cursor.fetchone().total_debt
print(f"October 2025 only (d.fDATE in Oct): {oct_only:,.2f}")

print(f"\nExpected value: 2,435,799.90")

conn.close()

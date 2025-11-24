import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

print("\n=== CHECKING MONTHLY DEBT VALUES: Area 105, Groups 002+036 ===")

# Debt at end of September 2025
query_sep = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDATE < '2025-10-01'
"""
cursor.execute(query_sep)
debt_sep = float(cursor.fetchone().DebtFromDocs or 0)

# Debt at end of October 2025
query_oct = """
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
cursor.execute(query_oct)
debt_oct = float(cursor.fetchone().DebtFromDocs or 0)

# Change in October
debt_change_oct = debt_oct - debt_sep

print(f"Debt at end of Sep 2025: {debt_sep:,.2f}")
print(f"Debt at end of Oct 2025: {debt_oct:,.2f}")
print(f"Change in October: {debt_change_oct:,.2f}")

# Sales in October
query_sales_oct = """
    SELECT 
        ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND s.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND s.fDATE >= '2025-10-01' AND s.fDATE < '2025-11-01'
    AND s.fSTATE = 2
"""
cursor.execute(query_sales_oct)
sales_oct = float(cursor.fetchone().TotalSales or 0)

print(f"\nSales in October 2025: {sales_oct:,.2f}")
print(f"User's value for 'Кредиты' October: 1,696,049.41")
print(f"User's expected debt value: 2,435,799.90")

# Test if "Credits" might be debt change
print(f"\nIs 1,696,049.41 the debt change? Actual: {debt_change_oct:,.2f}")

# Test: Sales * some ratio
ratio1 = 2435799.90 / sales_oct if sales_oct > 0 else 0
ratio2 = 1696049.41 / sales_oct if sales_oct > 0 else 0
print(f"\nRatio (expected / sales): {ratio1:.4f}")
print(f"Ratio (credits / sales): {ratio2:.4f}")

conn.close()

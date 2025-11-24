import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)
cursor = conn.cursor()

print("\n" + "="*70)
print("CHECKING SALES WITH PRODUCT GROUP FILTER")
print("Sales Area 101, October 2025, Groups 002, 036")
print("Expected: 3,115,494.66 AMD")
print("="*70)

# Check if filtering SALEDOCDETAILS by product group helps
print("\n1. Using SALEDOCDETAILS with product group filter (exclude 015)")
print("-" * 70)

query = """
SELECT 
    ISNULL(SUM(sd.fSUM), 0) AS TotalSales
FROM SALES s
INNER JOIN SALEDOCDETAILS sd ON s.fISN = sd.fISN
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = '101'
    AND s.fSALESAREA = '101'
    AND s.fDATE >= '2025-10-01'
    AND s.fDATE <= '2025-10-31'
    AND s.fSTATE = 2
    AND c.fID NOT IN (1, 35461)
    AND c.fGROUP IN ('002', '036')
    AND sd.fPRODGROUP NOT IN ('015')
"""

cursor.execute(query)
row = cursor.fetchone()
if row:
    val = float(row.TotalSales)
    print(f"Total (excluding prodgroup 015): {val:,.2f} AMD")
    print(f"Difference: {abs(val - 3115494.66):,.2f} AMD")
    if abs(val - 3115494.66) < 1:
        print("✓ EXACT MATCH!")

# Check cash + NULL sales with product group filter
print("\n2. Cash + NULL sales WITH product group filter")
print("-" * 70)

query2 = """
SELECT 
    ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (1, NULL) OR s.fPAYTYPE IS NULL THEN sd.fSUM ELSE 0 END), 0) AS CashAndNull
FROM SALES s
INNER JOIN SALEDOCDETAILS sd ON s.fISN = sd.fISN
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = '101'
    AND s.fSALESAREA = '101'
    AND s.fDATE >= '2025-10-01'
    AND s.fDATE <= '2025-10-31'
    AND s.fSTATE = 2
    AND c.fID NOT IN (1, 35461)
    AND c.fGROUP IN ('002', '036')
    AND sd.fPRODGROUP NOT IN ('015')
"""

cursor.execute(query2)
row2 = cursor.fetchone()
if row2:
    val = float(row2.CashAndNull)
    print(f"Cash + NULL (excluding prodgroup 015): {val:,.2f} AMD")
    print(f"Difference: {abs(val - 3115494.66):,.2f} AMD")
    if abs(val - 3115494.66) < 1:
        print("✓ EXACT MATCH!")

# Check cash ONLY with product group filter
print("\n3. Cash ONLY (PAYTYPE=1) WITH product group filter")
print("-" * 70)

query3 = """
SELECT 
    ISNULL(SUM(CASE WHEN s.fPAYTYPE = 1 THEN sd.fSUM ELSE 0 END), 0) AS CashOnly
FROM SALES s
INNER JOIN SALEDOCDETAILS sd ON s.fISN = sd.fISN
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = '101'
    AND s.fSALESAREA = '101'
    AND s.fDATE >= '2025-10-01'
    AND s.fDATE <= '2025-10-31'
    AND s.fSTATE = 2
    AND c.fID NOT IN (1, 35461)
    AND c.fGROUP IN ('002', '036')
    AND sd.fPRODGROUP NOT IN ('015')
"""

cursor.execute(query3)
row3 = cursor.fetchone()
if row3:
    val = float(row3.CashOnly)
    print(f"Cash only (excluding prodgroup 015): {val:,.2f} AMD")
    print(f"Difference: {abs(val - 3115494.66):,.2f} AMD")
    if abs(val - 3115494.66) < 1:
        print("✓ EXACT MATCH!")

print("\n" + "="*70)

conn.close()

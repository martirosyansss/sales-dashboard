import pyodbc
from decimal import Decimal

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)
cursor = conn.cursor()

print("\n" + "="*70)
print("SEARCHING FOR PAYMENT VALUE: 3,115,494.66 AMD")
print("Sales Area 101, October 2025, Groups 002, 036")
print("="*70)

# Check HICUSTOMERSDEBT Credit records (payments)
print("\n1. HICUSTOMERSDEBT - Credit Records (fDBCR='C')")
print("-" * 70)
query1 = """
SELECT 
    ISNULL(SUM(ABS(d.fSUM)), 0) AS TotalPayments
FROM HICUSTOMERSDEBT d
INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE d.fDBCR = 'C'
    AND csa.fSALESAREA = '101'
    AND doc.fDATE >= '2025-10-01'
    AND doc.fDATE <= '2025-10-31'
    AND c.fID NOT IN (1, 35461)
    AND c.fGROUP IN ('002', '036')
"""
cursor.execute(query1)
row1 = cursor.fetchone()
if row1:
    val = float(row1.TotalPayments)
    print(f"Credit payments (C): {val:,.2f} AMD")
    print(f"Difference: {abs(val - 3115494.66):,.2f} AMD")
    if abs(val - 3115494.66) < 1:
        print("✓ MATCH!")

# Check Cash sales ONLY (PAYTYPE=1)
print("\n2. SALES - Cash Sales Only (PAYTYPE=1)")
print("-" * 70)
query2 = """
SELECT 
    ISNULL(SUM(CASE WHEN s.fPAYTYPE = 1 THEN s.fTOTALSUM ELSE 0 END), 0) AS CashSales
FROM SALES s
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = '101'
    AND s.fSALESAREA = '101'
    AND s.fDATE >= '2025-10-01'
    AND s.fDATE <= '2025-10-31'
    AND s.fSTATE = 2
    AND c.fID NOT IN (1, 35461)
    AND c.fGROUP IN ('002', '036')
"""
cursor.execute(query2)
row2 = cursor.fetchone()
if row2:
    val = float(row2.CashSales)
    print(f"Cash sales: {val:,.2f} AMD")
    print(f"Difference: {abs(val - 3115494.66):,.2f} AMD")
    if abs(val - 3115494.66) < 1:
        print("✓ MATCH!")

# Check if it's CashSales + Credit payments
print("\n3. Combination: Cash Sales + Credit Payments")
print("-" * 70)
if row1 and row2:
    combined = float(row1.TotalPayments) + float(row2.CashSales)
    print(f"Cash Sales + Credit Payments: {combined:,.2f} AMD")
    print(f"Difference: {abs(combined - 3115494.66):,.2f} AMD")
    if abs(combined - 3115494.66) < 1:
        print("✓ MATCH! Payments = CashSales + CreditPayments")

# Check all PAYTYPE values
print("\n4. SALES - Breakdown by PAYTYPE")
print("-" * 70)
query3 = """
SELECT 
    s.fPAYTYPE,
    COUNT(*) AS SalesCount,
    ISNULL(SUM(s.fTOTALSUM), 0) AS TotalAmount
FROM SALES s
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = '101'
    AND s.fSALESAREA = '101'
    AND s.fDATE >= '2025-10-01'
    AND s.fDATE <= '2025-10-31'
    AND s.fSTATE = 2
    AND c.fID NOT IN (1, 35461)
    AND c.fGROUP IN ('002', '036')
GROUP BY s.fPAYTYPE
ORDER BY s.fPAYTYPE
"""
cursor.execute(query3)
rows3 = cursor.fetchall()
total_by_paytype = 0
for row in rows3:
    val = float(row.TotalAmount)
    total_by_paytype += val
    print(f"PAYTYPE {row.fPAYTYPE}: {val:,.2f} AMD ({row.SalesCount} sales)")
    if abs(val - 3115494.66) < 1:
        print(f"  ✓ MATCH!")

print(f"\nTotal all PAYTYPEs: {total_by_paytype:,.2f} AMD")

print("\n" + "="*70)

conn.close()

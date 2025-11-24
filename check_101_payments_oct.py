import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)
cursor = conn.cursor()

# Check TotalSales for area 101 with groups 002, 036 in October
query = """
SELECT 
    COUNT(DISTINCT s.fCUSTOMERID) AS CustomerCount,
    COUNT(s.fISN) AS SalesCount,
    ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales,
    ISNULL(SUM(CASE WHEN s.fPAYTYPE = 1 THEN s.fTOTALSUM ELSE 0 END), 0) AS CashSales,
    ISNULL(SUM(CASE WHEN s.fPAYTYPE = 2 THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales
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

cursor.execute(query)
row = cursor.fetchone()

print("\n" + "="*60)
print("SALES AREA 101 - OCTOBER 2025")
print("Groups: 002, 036")
print("="*60)
if row:
    print(f"\nTotalSales (all): {row.TotalSales:,.2f} AMD")
    print(f"CashSales (PAYTYPE=1): {row.CashSales:,.2f} AMD")
    print(f"CreditSales (PAYTYPE=2): {row.CreditSales:,.2f} AMD")
    print(f"Customer Count: {row.CustomerCount}")
    print(f"Sales Count: {row.SalesCount}")
else:
    print("\nNo data found")

# Now check what we'd expect as payments (same value since Payments = TotalSales)
print(f"\nUser stated correct Payments: 3,115,494.66 AMD")

if row:
    total_sales = float(row.TotalSales)
    cash_sales = float(row.CashSales)
    credit_sales = float(row.CreditSales)
    expected = 3115494.66
    
    diff_total = abs(total_sales - expected)
    diff_cash = abs(cash_sales - expected)
    diff_credit = abs(credit_sales - expected)
    
    print(f"\nDifference (TotalSales): {diff_total:,.2f} AMD")
    print(f"Difference (CashSales): {diff_cash:,.2f} AMD")
    print(f"Difference (CreditSales): {diff_credit:,.2f} AMD")
    
    if diff_total < 1:
        print("\n✓ TotalSales MATCHES user value!")
    elif diff_cash < 1:
        print("\n✓ CashSales MATCHES user value!")
    elif diff_credit < 1:
        print("\n✓ CreditSales MATCHES user value!")
    else:
        print("\n✗ None match exactly - checking if it's TotalSales - CreditSales...")
        cash_calculation = total_sales - credit_sales
        diff_calc = abs(cash_calculation - expected)
        print(f"   TotalSales - CreditSales = {cash_calculation:,.2f}")
        print(f"   Difference: {diff_calc:,.2f} AMD")
        if diff_calc < 1:
            print("   ✓ MATCH! Payments = TotalSales - CreditSales")

print("="*60)

conn.close()

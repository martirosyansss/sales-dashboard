import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)
cursor = conn.cursor()

expected = 3115494.66

print("\n" + "="*70)
print("CHECKING PAYMENT-RELATED TABLES")
print("Sales Area 101, October 2025, Groups 002, 036")
print("Expected: 3,115,494.66 AMD")
print("="*70)

# 1. Check PAYMENTS table structure
print("\n1. PAYMENTS Table Structure")
print("-" * 70)
cursor.execute("SELECT TOP 1 * FROM PAYMENTS")
print("Columns:", [desc[0] for desc in cursor.description])

# Check with different date columns
print("\n2. PAYMENTS - Trying different queries")
print("-" * 70)

# Query 1: Basic sum
query1 = """
SELECT 
    COUNT(*) AS PaymentCount,
    ISNULL(SUM(p.fSUM), 0) AS TotalPayments
FROM PAYMENTS p
INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = '101'
    AND p.fDATE >= '2025-10-01'
    AND p.fDATE <= '2025-10-31'
    AND c.fID NOT IN (1, 35461)
    AND c.fGROUP IN ('002', '036')
"""
cursor.execute(query1)
row = cursor.fetchone()
print(f"All payments: {float(row.TotalPayments):,.2f} AMD ({row.PaymentCount} records)")
print(f"Difference: {abs(float(row.TotalPayments) - expected):,.2f}")

# Query 2: Check if there's a TYPE field
try:
    query2 = """
    SELECT 
        p.fTYPE,
        COUNT(*) AS Count,
        ISNULL(SUM(p.fSUM), 0) AS Total
    FROM PAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
        AND p.fDATE >= '2025-10-01'
        AND p.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
    GROUP BY p.fTYPE
    ORDER BY Total DESC
    """
    cursor.execute(query2)
    rows = cursor.fetchall()
    print("\nPayments by TYPE:")
    for row in rows:
        val = float(row.Total)
        diff = abs(val - expected)
        match_str = " ✓ MATCH!" if diff < 1 else ""
        print(f"  Type {row.fTYPE}: {val:,.2f} AMD ({row.Count} records){match_str}")
except Exception as e:
    print(f"TYPE column not found: {e}")

# Query 3: Check for DBCR field (like HICUSTOMERSDEBT)
try:
    query3 = """
    SELECT 
        p.fDBCR,
        COUNT(*) AS Count,
        ISNULL(SUM(ABS(p.fSUM)), 0) AS Total
    FROM PAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
        AND p.fDATE >= '2025-10-01'
        AND p.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
    GROUP BY p.fDBCR
    ORDER BY Total DESC
    """
    cursor.execute(query3)
    rows = cursor.fetchall()
    print("\nPayments by DBCR:")
    for row in rows:
        val = float(row.Total)
        diff = abs(val - expected)
        match_str = " ✓ MATCH!" if diff < 1 else ""
        print(f"  DBCR '{row.fDBCR}': {val:,.2f} AMD ({row.Count} records){match_str}")
except Exception as e:
    print(f"fDBCR column not found: {e}")

# 3. Check IBPAYMENTS
print("\n3. IBPAYMENTS Table")
print("-" * 70)
try:
    cursor.execute("SELECT TOP 1 * FROM IBPAYMENTS")
    print("Columns:", [desc[0] for desc in cursor.description])
    
    query = """
    SELECT 
        COUNT(*) AS Count,
        ISNULL(SUM(p.fSUM), 0) AS Total
    FROM IBPAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
        AND p.fDATE >= '2025-10-01'
        AND p.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
    """
    cursor.execute(query)
    row = cursor.fetchone()
    val = float(row.Total)
    print(f"IBPAYMENTS total: {val:,.2f} AMD ({row.Count} records)")
    print(f"Difference: {abs(val - expected):,.2f}")
    if abs(val - expected) < 1:
        print("✓ EXACT MATCH!")
except Exception as e:
    print(f"Error: {e}")

# 4. Check HIRESTCUSTOMERSDEBT
print("\n4. HIRESTCUSTOMERSDEBT Table")
print("-" * 70)
try:
    cursor.execute("SELECT TOP 1 * FROM HIRESTCUSTOMERSDEBT")
    print("Columns:", [desc[0] for desc in cursor.description])
    
    query = """
    SELECT 
        COUNT(*) AS Count,
        ISNULL(SUM(ABS(d.fSUM)), 0) AS Total
    FROM HIRESTCUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
        AND doc.fDATE >= '2025-10-01'
        AND doc.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
    """
    cursor.execute(query)
    row = cursor.fetchone()
    val = float(row.Total)
    print(f"HIRESTCUSTOMERSDEBT total: {val:,.2f} AMD ({row.Count} records)")
    print(f"Difference: {abs(val - expected):,.2f}")
    if abs(val - expected) < 1:
        print("✓ EXACT MATCH!")
except Exception as e:
    print(f"Error: {e}")

# 5. Combination: Cash Sales + something
print("\n5. Checking Combinations")
print("-" * 70)
cash_sales = 2904435.99
print(f"Cash Sales (PAYTYPE=1): {cash_sales:,.2f}")
print(f"Need to add: {expected - cash_sales:,.2f}")

# Try PAYMENTS with specific filter + cash sales
try:
    query = """
    SELECT 
        ISNULL(SUM(p.fSUM), 0) AS SpecificPayments
    FROM PAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
        AND p.fDATE >= '2025-10-01'
        AND p.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
        AND p.fSUM > 0
    """
    cursor.execute(query)
    row = cursor.fetchone()
    payments_val = float(row.SpecificPayments)
    
    # Try different combinations
    combos = [
        ("Cash Sales + Positive Payments", cash_sales + payments_val),
        ("Cash Sales - Payments", cash_sales - payments_val),
        ("Payments - Cash Sales", payments_val - cash_sales),
    ]
    
    for name, val in combos:
        diff = abs(val - expected)
        match_str = " ✓ MATCH!" if diff < 1 else ""
        print(f"{name}: {val:,.2f} (diff: {diff:,.2f}){match_str}")
except Exception as e:
    print(f"Error in combinations: {e}")

print("\n" + "="*70)

conn.close()

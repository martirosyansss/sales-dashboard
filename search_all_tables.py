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
print("SEARCHING ALL TABLES FOR PAYMENT VALUE: 3,115,494.66 AMD")
print("Sales Area 101, October 2025, Groups 002, 036")
print("="*70)

# 1. Check PAYMENTS table if it exists
print("\n1. Checking PAYMENTS table (if exists)")
print("-" * 70)
try:
    query = """
    SELECT 
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
    cursor.execute(query)
    row = cursor.fetchone()
    if row:
        val = float(row.TotalPayments)
        print(f"PAYMENTS table sum: {val:,.2f} AMD")
        print(f"Difference: {abs(val - expected):,.2f} AMD")
        if abs(val - expected) < 1:
            print("✓ EXACT MATCH!")
except Exception as e:
    print(f"PAYMENTS table not found or error: {e}")

# 2. Check CASHDOCS table
print("\n2. Checking CASHDOCS table")
print("-" * 70)
try:
    query = """
    SELECT 
        ISNULL(SUM(cd.fSUM), 0) AS TotalCash
    FROM CASHDOCS cd
    INNER JOIN CUSTOMERS c ON cd.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
        AND cd.fDATE >= '2025-10-01'
        AND cd.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
    """
    cursor.execute(query)
    row = cursor.fetchone()
    if row:
        val = float(row.TotalCash)
        print(f"CASHDOCS sum: {val:,.2f} AMD")
        print(f"Difference: {abs(val - expected):,.2f} AMD")
        if abs(val - expected) < 1:
            print("✓ EXACT MATCH!")
except Exception as e:
    print(f"CASHDOCS table not found or error: {e}")

# 3. Check RECEIPTS table
print("\n3. Checking RECEIPTS table")
print("-" * 70)
try:
    query = """
    SELECT 
        ISNULL(SUM(r.fSUM), 0) AS TotalReceipts
    FROM RECEIPTS r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
        AND r.fDATE >= '2025-10-01'
        AND r.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
    """
    cursor.execute(query)
    row = cursor.fetchone()
    if row:
        val = float(row.TotalReceipts)
        print(f"RECEIPTS sum: {val:,.2f} AMD")
        print(f"Difference: {abs(val - expected):,.2f} AMD")
        if abs(val - expected) < 1:
            print("✓ EXACT MATCH!")
except Exception as e:
    print(f"RECEIPTS table not found or error: {e}")

# 4. Check HICUSTOMERSDEBT with different filters
print("\n4. HICUSTOMERSDEBT - Different Type Filters")
print("-" * 70)
types_to_check = [
    ('01', 'Type 01'),
    ('02', 'Type 02'),
    ('03', 'Type 03'),
    ('1', 'Type 1'),
    ('2', 'Type 2'),
    ('3', 'Type 3'),
]

for type_code, type_name in types_to_check:
    try:
        query = f"""
        SELECT 
            ISNULL(SUM(ABS(d.fSUM)), 0) AS Total
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE d.fTYPE = '{type_code}'
            AND csa.fSALESAREA = '101'
            AND doc.fDATE >= '2025-10-01'
            AND doc.fDATE <= '2025-10-31'
            AND c.fID NOT IN (1, 35461)
            AND c.fGROUP IN ('002', '036')
        """
        cursor.execute(query)
        row = cursor.fetchone()
        if row and row.Total:
            val = float(row.Total)
            if val > 0:
                diff = abs(val - expected)
                match_str = " ✓ MATCH!" if diff < 1 else ""
                print(f"{type_name}: {val:,.2f} AMD (diff: {diff:,.2f}){match_str}")
    except Exception as e:
        pass

# 5. Check INVOICES table
print("\n5. Checking INVOICES table")
print("-" * 70)
try:
    query = """
    SELECT 
        ISNULL(SUM(i.fSUM), 0) AS TotalInvoices
    FROM INVOICES i
    INNER JOIN CUSTOMERS c ON i.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
        AND i.fDATE >= '2025-10-01'
        AND i.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
    """
    cursor.execute(query)
    row = cursor.fetchone()
    if row:
        val = float(row.TotalInvoices)
        print(f"INVOICES sum: {val:,.2f} AMD")
        print(f"Difference: {abs(val - expected):,.2f} AMD")
        if abs(val - expected) < 1:
            print("✓ EXACT MATCH!")
except Exception as e:
    print(f"INVOICES table not found or error: {e}")

# 6. Check TRANSACTIONS table
print("\n6. Checking TRANSACTIONS table")
print("-" * 70)
try:
    query = """
    SELECT 
        ISNULL(SUM(t.fSUM), 0) AS TotalTransactions
    FROM TRANSACTIONS t
    INNER JOIN CUSTOMERS c ON t.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
        AND t.fDATE >= '2025-10-01'
        AND t.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
    """
    cursor.execute(query)
    row = cursor.fetchone()
    if row:
        val = float(row.TotalTransactions)
        print(f"TRANSACTIONS sum: {val:,.2f} AMD")
        print(f"Difference: {abs(val - expected):,.2f} AMD")
        if abs(val - expected) < 1:
            print("✓ EXACT MATCH!")
except Exception as e:
    print(f"TRANSACTIONS table not found or error: {e}")

# 7. Check SALES with non-credit sales (PAYTYPE != 2)
print("\n7. SALES - Non-Credit Sales (PAYTYPE != 2 or NULL)")
print("-" * 70)
query = """
SELECT 
    ISNULL(SUM(CASE WHEN (s.fPAYTYPE != 2 OR s.fPAYTYPE IS NULL) THEN s.fTOTALSUM ELSE 0 END), 0) AS NonCreditSales
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
if row:
    val = float(row.NonCreditSales)
    print(f"Non-Credit Sales: {val:,.2f} AMD")
    print(f"Difference: {abs(val - expected):,.2f} AMD")
    if abs(val - expected) < 1:
        print("✓ EXACT MATCH!")

# 8. List all tables in database
print("\n8. Listing all tables in SalesManagement database")
print("-" * 70)
cursor.execute("""
    SELECT TABLE_NAME 
    FROM INFORMATION_SCHEMA.TABLES 
    WHERE TABLE_TYPE = 'BASE TABLE' 
    AND TABLE_NAME LIKE '%PAY%' OR TABLE_NAME LIKE '%CASH%' OR TABLE_NAME LIKE '%DEBT%'
    ORDER BY TABLE_NAME
""")
tables = cursor.fetchall()
print("Relevant tables found:")
for table in tables:
    print(f"  - {table.TABLE_NAME}")

print("\n" + "="*70)

conn.close()

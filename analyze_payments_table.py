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
print("PAYMENTS TABLE - DETAILED ANALYSIS")
print("Sales Area 101, October 2025, Groups 002, 036")
print("Expected: 3,115,494.66 AMD")
print("="*70)

# 1. Check by PAYMENTTYPE
print("\n1. PAYMENTS by fPAYMENTTYPE")
print("-" * 70)
query = """
SELECT 
    p.fPAYMENTTYPE,
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
GROUP BY p.fPAYMENTTYPE
ORDER BY Total DESC
"""
cursor.execute(query)
rows = cursor.fetchall()
for row in rows:
    val = float(row.Total)
    diff = abs(val - expected)
    match_str = " ✓ MATCH!" if diff < 1 else ""
    print(f"PaymentType {row.fPAYMENTTYPE}: {val:,.2f} AMD ({row.Count} records){match_str}")

# 2. Check by STATE
print("\n2. PAYMENTS by fSTATE")
print("-" * 70)
query = """
SELECT 
    p.fSTATE,
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
GROUP BY p.fSTATE
ORDER BY Total DESC
"""
cursor.execute(query)
rows = cursor.fetchall()
for row in rows:
    val = float(row.Total)
    diff = abs(val - expected)
    match_str = " ✓ MATCH!" if diff < 1 else ""
    print(f"State {row.fSTATE}: {val:,.2f} AMD ({row.Count} records){match_str}")

# 3. Check using SALESAREA directly from PAYMENTS
print("\n3. PAYMENTS using p.fSALESAREA (not via JOIN)")
print("-" * 70)
query = """
SELECT 
    COUNT(*) AS Count,
    ISNULL(SUM(p.fSUM), 0) AS Total
FROM PAYMENTS p
INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
WHERE p.fSALESAREA = '101'
    AND p.fDATE >= '2025-10-01'
    AND p.fDATE <= '2025-10-31'
    AND c.fID NOT IN (1, 35461)
    AND c.fGROUP IN ('002', '036')
"""
cursor.execute(query)
row = cursor.fetchone()
val = float(row.Total)
print(f"Direct SALESAREA filter: {val:,.2f} AMD ({row.Count} records)")
print(f"Difference: {abs(val - expected):,.2f}")
if abs(val - expected) < 1:
    print("✓ EXACT MATCH!")

# 4. Check specific payment types + state combinations
print("\n4. PAYMENTS - Specific Filters")
print("-" * 70)

filters = [
    ("STATE=2", "AND p.fSTATE = 2"),
    ("PAYMENTTYPE=1", "AND p.fPAYMENTTYPE = 1"),
    ("PAYMENTTYPE=2", "AND p.fPAYMENTTYPE = 2"),
    ("PAYMENTTYPE!=3", "AND (p.fPAYMENTTYPE != 3 OR p.fPAYMENTTYPE IS NULL)"),
    ("STATE=2 & PAYTYPE=1", "AND p.fSTATE = 2 AND p.fPAYMENTTYPE = 1"),
]

for filter_name, filter_clause in filters:
    query = f"""
    SELECT 
        ISNULL(SUM(p.fSUM), 0) AS Total
    FROM PAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    WHERE p.fSALESAREA = '101'
        AND p.fDATE >= '2025-10-01'
        AND p.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461)
        AND c.fGROUP IN ('002', '036')
        {filter_clause}
    """
    cursor.execute(query)
    row = cursor.fetchone()
    val = float(row.Total)
    diff = abs(val - expected)
    match_str = " ✓ MATCH!" if diff < 1 else ""
    print(f"{filter_name}: {val:,.2f} (diff: {diff:,.2f}){match_str}")

# 5. Check if fPREPAYMENT matters
print("\n5. PAYMENTS - fPREPAYMENT breakdown")
print("-" * 70)
query = """
SELECT 
    p.fPREPAYMENT,
    COUNT(*) AS Count,
    ISNULL(SUM(p.fSUM), 0) AS Total
FROM PAYMENTS p
INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
WHERE p.fSALESAREA = '101'
    AND p.fDATE >= '2025-10-01'
    AND p.fDATE <= '2025-10-31'
    AND c.fID NOT IN (1, 35461)
    AND c.fGROUP IN ('002', '036')
GROUP BY p.fPREPAYMENT
ORDER BY Total DESC
"""
cursor.execute(query)
rows = cursor.fetchall()
for row in rows:
    val = float(row.Total)
    diff = abs(val - expected)
    match_str = " ✓ MATCH!" if diff < 1 else ""
    prepay = row.fPREPAYMENT if row.fPREPAYMENT is not None else "NULL"
    print(f"Prepayment {prepay}: {val:,.2f} AMD ({row.Count} records){match_str}")

print("\n" + "="*70)

conn.close()

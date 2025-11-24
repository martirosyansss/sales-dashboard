import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()
expected = 3486213.18

print("\n" + "="*70)
print(f"AREA 105 OCTOBER 2025: Finding {expected:,.2f} AMD")
print("="*70)

# First, get correct column name
cursor.execute("SELECT TOP 1 * FROM PAYMENTS WHERE fSALESAREA = '105'")
columns = [column[0] for column in cursor.description]
print("\nPAYMENTS columns:", columns)

customer_col = None
for col in columns:
    if 'CUSTOMER' in col.upper():
        customer_col = col
        print(f"Using customer column: {customer_col}")
        break

tests = [
    ("All payments STATE=2", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        WHERE p.fSALESAREA = '105'
            AND p.fDATE >= '2025-10-01'
            AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
    """),
]

if customer_col:
    tests.extend([
        (f"With groups 002,036 (via {customer_col})", f"""
            SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
            INNER JOIN CUSTOMERS c ON p.{customer_col} = c.fISN
            WHERE p.fSALESAREA = '105'
                AND p.fDATE >= '2025-10-01'
                AND p.fDATE <= '2025-10-31'
                AND p.fSTATE = 2
                AND c.fGROUP IN ('002', '036')
        """),
        (f"Exclude 35461 (via {customer_col})", f"""
            SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
            INNER JOIN CUSTOMERS c ON p.{customer_col} = c.fISN
            WHERE p.fSALESAREA = '105'
                AND p.fDATE >= '2025-10-01'
                AND p.fDATE <= '2025-10-31'
                AND p.fSTATE = 2
                AND c.fGROUP IN ('002', '036')
                AND c.fCODE != '35461'
        """),
    ])

for test_name, query in tests:
    try:
        cursor.execute(query)
        row = cursor.fetchone()
        value = float(row[0]) if row and row[0] else 0
        diff = abs(value - expected)
        
        status = "🎯 EXACT MATCH!" if diff < 10 else ("✓ Very Close" if diff < 100 else ("✓ Close" if diff < 1000 else ""))
        
        print(f"\n{test_name}")
        print(f"  Value: {value:,.2f} AMD")
        print(f"  Diff:  {diff:,.2f} AMD {status}")
    except Exception as e:
        print(f"\n{test_name}")
        print(f"  ERROR: {e}")

# Also try via CUSTOMERSALESAREAS
print("\n" + "-"*70)
print("Testing via CUSTOMERSALESAREAS join:")
print("-"*70)

cursor.execute("""
    SELECT ISNULL(SUM(p.fSUM), 0) 
    FROM PAYMENTS p
    INNER JOIN CUSTOMERSALESAREAS csa ON p.fCUSTOMER = csa.fCUSTOMER
    WHERE csa.fSALESAREA = '105'
        AND p.fDATE >= '2025-10-01'
        AND p.fDATE <= '2025-10-31'
        AND p.fSTATE = 2
""")
row = cursor.fetchone()
value = float(row[0]) if row and row[0] else 0
diff = abs(value - expected)
status = "🎯 EXACT MATCH!" if diff < 10 else ("✓ Very Close" if diff < 100 else ("✓ Close" if diff < 1000 else ""))
print(f"Via CUSTOMERSALESAREAS: {value:,.2f} AMD (diff: {diff:,.2f}) {status}")

# With group filter
cursor.execute("""
    SELECT ISNULL(SUM(p.fSUM), 0) 
    FROM PAYMENTS p
    INNER JOIN CUSTOMERSALESAREAS csa ON p.fCUSTOMER = csa.fCUSTOMER
    INNER JOIN CUSTOMERS c ON csa.fCUSTOMER = c.fISN
    WHERE csa.fSALESAREA = '105'
        AND p.fDATE >= '2025-10-01'
        AND p.fDATE <= '2025-10-31'
        AND p.fSTATE = 2
        AND c.fGROUP IN ('002', '036')
""")
row = cursor.fetchone()
value = float(row[0]) if row and row[0] else 0
diff = abs(value - expected)
status = "🎯 EXACT MATCH!" if diff < 10 else ("✓ Very Close" if diff < 100 else ("✓ Close" if diff < 1000 else ""))
print(f"Via CUSTOMERSALESAREAS + groups: {value:,.2f} AMD (diff: {diff:,.2f}) {status}")

# With exclusion
cursor.execute("""
    SELECT ISNULL(SUM(p.fSUM), 0) 
    FROM PAYMENTS p
    INNER JOIN CUSTOMERSALESAREAS csa ON p.fCUSTOMER = csa.fCUSTOMER
    INNER JOIN CUSTOMERS c ON csa.fCUSTOMER = c.fISN
    WHERE csa.fSALESAREA = '105'
        AND p.fDATE >= '2025-10-01'
        AND p.fDATE <= '2025-10-31'
        AND p.fSTATE = 2
        AND c.fGROUP IN ('002', '036')
        AND c.fCODE != '35461'
""")
row = cursor.fetchone()
value = float(row[0]) if row and row[0] else 0
diff = abs(value - expected)
status = "🎯 EXACT MATCH!" if diff < 10 else ("✓ Very Close" if diff < 100 else ("✓ Close" if diff < 1000 else ""))
print(f"Via CUSTOMERSALESAREAS + groups + exclude: {value:,.2f} AMD (diff: {diff:,.2f}) {status}")

print("\n" + "="*70)

conn.close()

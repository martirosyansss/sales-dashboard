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
print(f"TESTING PAYMENTS WITHOUT GROUP FILTERS: {expected:,.2f} AMD")
print("Area 101 | October 2025")
print("="*70)

tests = [
    ("PAYMENTS - No group filter, only area", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01'
            AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
    """),
    ("PAYMENTS - No group filter, with customer join", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01'
            AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
    """),
    ("PAYMENTS - Exclude customer 35461, no groups", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01'
            AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
            AND c.fID NOT IN (35461)
    """),
    ("PAYMENTS - Via CUSTOMERSALESAREAS", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE csa.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01'
            AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
            AND c.fID NOT IN (35461)
    """),
]

for test_name, query in tests:
    try:
        cursor.execute(query)
        row = cursor.fetchone()
        value = float(row[0]) if row and row[0] else 0
        diff = abs(value - expected)
        
        status = "🎯 EXACT MATCH!" if diff < 1 else ("✓ Very Close" if diff < 100 else ("✓ Close" if diff < 1000 else ""))
        
        print(f"\n{test_name}")
        print(f"  Value: {value:,.2f} AMD")
        print(f"  Diff:  {diff:,.2f} AMD {status}")
    except Exception as e:
        print(f"\n{test_name}")
        print(f"  ERROR: {e}")

print("\n" + "="*70)

conn.close()

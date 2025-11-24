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
print(f"SEARCHING FOR PAYMENT VALUE: {expected:,.2f} AMD")
print("Area 101 | October 2025 | Groups 002, 036")
print("="*70)

# Test different scenarios
tests = [
    ("PAYMENTS table - All payments", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND c.fID NOT IN (35461)
            AND c.fGROUP IN ('002', '036')
    """),
    ("PAYMENTS - STATE=2 only", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
            AND c.fID NOT IN (35461)
            AND c.fGROUP IN ('002', '036')
    """),
    ("PAYMENTS - Without PREPAYMENT filter", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
            AND c.fID NOT IN (35461)
            AND c.fGROUP IN ('002', '036')
    """),
    ("PAYMENTS - With customer exclusion 35461", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
            AND c.fID NOT IN (35461)
            AND c.fGROUP IN ('002', '036')
    """),
    ("PAYMENTS - Without customer group filter", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
    """),
]

for test_name, query in tests:
    cursor.execute(query)
    row = cursor.fetchone()
    value = float(row[0]) if row and row[0] else 0
    diff = abs(value - expected)
    
    status = "✓ MATCH!" if diff < 1 else ("✓ Close" if diff < 1000 else "")
    
    print(f"\n{test_name}")
    print(f"  Value: {value:,.2f} AMD")
    print(f"  Diff:  {diff:,.2f} AMD {status}")

print("\n" + "="*70)

conn.close()

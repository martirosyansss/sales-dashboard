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
print("FINDING EXACT MATCH IN PAYMENTS TABLE")
print("="*70)

# Try different combinations
tests = [
    ("Prepayment=0 + State=2", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND c.fID NOT IN (1, 35461) AND c.fGROUP IN ('002', '036')
            AND p.fPREPAYMENT = 0 AND p.fSTATE = 2
    """),
    ("Prepayment=0 only", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND c.fID NOT IN (1, 35461) AND c.fGROUP IN ('002', '036')
            AND p.fPREPAYMENT = 0
    """),
    ("PaymentType 1 or 2", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND c.fID NOT IN (1, 35461) AND c.fGROUP IN ('002', '036')
            AND p.fPAYMENTTYPE IN (1, 2) AND p.fSTATE = 2
    """),
    ("PaymentType NOT 3", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND c.fID NOT IN (1, 35461) AND c.fGROUP IN ('002', '036')
            AND p.fPAYMENTTYPE != 3 AND p.fSTATE = 2
    """),
    ("Prepayment=0 + PaymentType NOT 3", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
            AND c.fID NOT IN (1, 35461) AND c.fGROUP IN ('002', '036')
            AND p.fPREPAYMENT = 0 AND p.fPAYMENTTYPE != 3 AND p.fSTATE = 2
    """),
]

for name, query in tests:
    cursor.execute(query)
    row = cursor.fetchone()
    val = float(row[0])
    diff = abs(val - expected)
    match_str = " ✓ MATCH!" if diff < 1 else ""
    print(f"{name:35s}: {val:>15,.2f} (diff: {diff:>10,.2f}){match_str}")

# Now try PAYMENTS minus something
print("\n" + "="*70)
print("TRYING SUBTRACTIONS")
print("="*70)

# Get base payments value
cursor.execute("""
    SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    WHERE p.fSALESAREA = '101'
        AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461) AND c.fGROUP IN ('002', '036')
        AND p.fSTATE = 2
""")
base_payments = float(cursor.fetchone()[0])

# Get prepayment amount (non-zero)
cursor.execute("""
    SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    WHERE p.fSALESAREA = '101'
        AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461) AND c.fGROUP IN ('002', '036')
        AND p.fPREPAYMENT > 0 AND p.fSTATE = 2
""")
prepayments = float(cursor.fetchone()[0])

# Get PaymentType 3
cursor.execute("""
    SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    WHERE p.fSALESAREA = '101'
        AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
        AND c.fID NOT IN (1, 35461) AND c.fGROUP IN ('002', '036')
        AND p.fPAYMENTTYPE = 3 AND p.fSTATE = 2
""")
type3 = float(cursor.fetchone()[0])

print(f"Base payments: {base_payments:,.2f}")
print(f"Prepayments (>0): {prepayments:,.2f}")
print(f"PaymentType 3: {type3:,.2f}")

calcs = [
    ("Base - Prepayments", base_payments - prepayments),
    ("Base - Type3", base_payments - type3),
    ("Base - Prepayments - Type3", base_payments - prepayments - type3),
]

for name, val in calcs:
    diff = abs(val - expected)
    match_str = " ✓ MATCH!" if diff < 1 else ""
    print(f"{name:35s}: {val:>15,.2f} (diff: {diff:>10,.2f}){match_str}")

print("\n" + "="*70)

conn.close()

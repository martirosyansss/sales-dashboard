import pyodbc

conn_str = (
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

target = 3486213.18

print("\n" + "="*70)
print(f"FIND CORRECT QUERY FOR: {target:,.2f} AMD")
print("="*70)

# Test: Area 105, Nov, all groups, no state filter
print("\n1. Area 105, Nov 1-30, ALL groups, NO state filter:")
cursor.execute("""
    SELECT SUM(p.fSUM) as Total, COUNT(*) as Count
    FROM PAYMENTS p
    WHERE p.fSALESAREA = '105'
        AND p.fDATE >= '2025-11-01'
        AND p.fDATE <= '2025-11-30'
""")
result = cursor.fetchone()
sum1 = float(result[0]) if result[0] else 0
print(f"   {sum1:,.2f} AMD ({result[1]} records)")
print(f"   Diff: {abs(sum1 - target):,.2f} AMD")
if abs(sum1 - target) < 1:
    print("   ✓✓✓ MATCH! Remove fSTATE filter and group filter!")

# Test: Check all states separately
print("\n2. Check states in Area 105, Nov:")
cursor.execute("""
    SELECT p.fSTATE, SUM(p.fSUM) as Total, COUNT(*) as Count
    FROM PAYMENTS p
    WHERE p.fSALESAREA = '105'
        AND p.fDATE >= '2025-11-01'
        AND p.fDATE <= '2025-11-30'
    GROUP BY p.fSTATE
    ORDER BY p.fSTATE
""")
for row in cursor.fetchall():
    print(f"   STATE {row[0]}: {float(row[1]):,.2f} AMD ({row[2]} records)")

# Test: Check payment types
print("\n3. Check payment types in Area 105, Nov:")
cursor.execute("""
    SELECT p.fPAYMENTTYPE, SUM(p.fSUM) as Total, COUNT(*) as Count
    FROM PAYMENTS p
    WHERE p.fSALESAREA = '105'
        AND p.fDATE >= '2025-11-01'
        AND p.fDATE <= '2025-11-30'
    GROUP BY p.fPAYMENTTYPE
    ORDER BY p.fPAYMENTTYPE
""")
total = 0
for row in cursor.fetchall():
    val = float(row[1])
    total += val
    print(f"   TYPE {row[0]}: {val:,.2f} AMD ({row[2]} records)")
print(f"   TOTAL: {total:,.2f} AMD")
if abs(total - target) < 1:
    print("   ✓✓✓ PERFECT MATCH!")

print("\n" + "="*70)

cursor.close()
conn.close()

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
print(f"COMPREHENSIVE SEARCH FOR: {target:,.2f} AMD")
print("="*70)

# Search by months in 2025
print("\n1. AREA 105 by months in 2025:")
months = [
    ('2025-01-01', '2025-01-31', 'January'),
    ('2025-02-01', '2025-02-28', 'February'),
    ('2025-03-01', '2025-03-31', 'March'),
    ('2025-04-01', '2025-04-30', 'April'),
    ('2025-05-01', '2025-05-31', 'May'),
    ('2025-06-01', '2025-06-30', 'June'),
    ('2025-07-01', '2025-07-31', 'July'),
    ('2025-08-01', '2025-08-31', 'August'),
    ('2025-09-01', '2025-09-30', 'September'),
    ('2025-10-01', '2025-10-31', 'October'),
    ('2025-11-01', '2025-11-30', 'November'),
]

for date_from, date_to, month_name in months:
    cursor.execute("""
        SELECT SUM(p.fSUM) as Total
        FROM PAYMENTS p
        WHERE p.fSALESAREA = '105'
            AND p.fDATE >= ?
            AND p.fDATE <= ?
    """, date_from, date_to)
    result = cursor.fetchone()
    if result[0]:
        sum_val = float(result[0])
        diff = abs(sum_val - target)
        status = '✓✓✓ MATCH!' if diff < 1 else ''
        if diff < 200000:  # Show if close
            print(f"   {month_name:10s}: {sum_val:,.2f} AMD (diff: {diff:,.2f}) {status}")

# Search all areas in November
print("\n2. ALL AREAS in November 2025:")
cursor.execute("""
    SELECT p.fSALESAREA, SUM(p.fSUM) as Total
    FROM PAYMENTS p
    WHERE p.fDATE >= '2025-11-01'
        AND p.fDATE <= '2025-11-30'
    GROUP BY p.fSALESAREA
    ORDER BY p.fSALESAREA
""")

for row in cursor.fetchall():
    area = row[0]
    sum_val = float(row[1]) if row[1] else 0
    diff = abs(sum_val - target)
    if diff < 200000:
        status = '✓✓✓ MATCH!' if diff < 1 else f'(diff: {diff:,.2f})'
        print(f"   Area {area}: {sum_val:,.2f} AMD {status}")

# Check combined periods
print("\n3. COMBINED PERIODS for Area 105:")
periods = [
    ('2025-10-01', '2025-11-23', 'Oct 1 - Nov 23'),
    ('2025-09-01', '2025-11-30', 'Sep 1 - Nov 30'),
    ('2025-11-01', '2025-12-31', 'Nov 1 - Dec 31'),
]

for date_from, date_to, label in periods:
    cursor.execute("""
        SELECT SUM(p.fSUM) as Total
        FROM PAYMENTS p
        WHERE p.fSALESAREA = '105'
            AND p.fDATE >= ?
            AND p.fDATE <= ?
    """, date_from, date_to)
    result = cursor.fetchone()
    if result[0]:
        sum_val = float(result[0])
        diff = abs(sum_val - target)
        status = '✓✓✓ MATCH!' if diff < 1 else ''
        print(f"   {label}: {sum_val:,.2f} AMD (diff: {diff:,.2f}) {status}")

print("\n" + "="*70)

cursor.close()
conn.close()

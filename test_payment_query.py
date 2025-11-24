import pyodbc
from datetime import datetime

# Database connection
conn = pyodbc.connect(
    'DRIVER={SQL Server};'
    'SERVER=DESKTOP-5CEL4K1\\SQLEXPRESS;'
    'DATABASE=SalesManagement;'
    'Trusted_Connection=yes;'
)

cursor = conn.cursor()

# Test parameters
area_code = '101'
date_from = datetime(2024, 10, 1)
date_to = datetime(2024, 10, 31)
excluded_customers = ['34695']
groups = ['002', '036']

# Build filters
excluded_filter = ""
if excluded_customers:
    excluded_ids = ','.join(excluded_customers)
    excluded_filter = f"AND c.fID NOT IN ({excluded_ids})"

group_filter = ""
if groups:
    group_list = ','.join([f"'{g}'" for g in groups])
    group_filter = f"""
        AND EXISTS (
            SELECT 1 FROM CUSTOMERSGROUPS cg
            WHERE cg.fCUSTOMERID = c.fID
            AND cg.fGROUP IN ({group_list})
        )
    """

# Payment query
payments_query = f"""
    SELECT ISNULL(SUM(p.fSUM), 0) AS TotalPayments
    FROM PAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    WHERE p.fSALESAREA = ?
        AND p.fDATE >= ? AND p.fDATE <= ?
        AND p.fSTATE = 2
        AND p.fPREPAYMENT = 0
        {excluded_filter}
        {group_filter}
"""

print("Testing payment query...")
print(f"Area: {area_code}")
print(f"Date range: {date_from} - {date_to}")
print(f"Excluded: {excluded_customers}")
print(f"Groups: {groups}")
print("\nQuery:")
print(payments_query)
print("\nParameters:", [area_code, date_from, date_to])

try:
    cursor.execute(payments_query, [area_code, date_from, date_to])
    result = cursor.fetchone()
    print(f"\n✅ SUCCESS: Total Payments = {result.TotalPayments:,.2f} AMD")
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

conn.close()

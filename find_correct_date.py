import pyodbc
from datetime import datetime

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()

area_code = '101'
expected = 3115494.66

print("\n" + "="*70)
print("SEARCHING FOR PAYMENT VALUE: 3,115,494.66 AMD")
print("Area 101 | Groups 002, 036 | Excluded customer 35461")
print("="*70)

# Test different date ranges
test_dates = [
    ("October 2024", datetime(2024, 10, 1), datetime(2024, 10, 31)),
    ("November 2024", datetime(2024, 11, 1), datetime(2024, 11, 30)),
    ("December 2024", datetime(2024, 12, 1), datetime(2024, 12, 31)),
    ("October 2025 (typo?)", datetime(2025, 10, 1), datetime(2025, 10, 31)),
]

payments_query = """
    SELECT 
        ISNULL(SUM(p.fSUM), 0) AS TotalPayments
    FROM PAYMENTS p
    INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
    WHERE p.fSALESAREA = ?
        AND p.fDATE >= ?
        AND p.fDATE <= ?
        AND p.fSTATE = 2
        AND p.fPREPAYMENT = 0
        AND c.fID NOT IN (35461)
        AND c.fGROUP IN ('002', '036')
"""

for label, date_from, date_to in test_dates:
    try:
        cursor.execute(payments_query, (area_code, date_from, date_to))
        row = cursor.fetchone()
        
        payment_value = float(row.TotalPayments) if row and row.TotalPayments else 0
        difference = abs(payment_value - expected)
        pct = (difference/expected)*100 if expected > 0 else 0
        
        status = "🎯 MATCH!" if difference < 100 else ("✓ Close" if difference < 10000 else "✗ Different")
        
        print(f"\n{label:20} {payment_value:15,.2f} AMD   (diff: {difference:12,.2f}, {pct:5.1f}%)  {status}")
        
    except Exception as e:
        print(f"\n{label:20} ERROR: {e}")

print("\n" + "="*70)

conn.close()

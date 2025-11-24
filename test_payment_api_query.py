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

# Test the payment query with correct parameters
area_code = '101'
date_from = datetime(2024, 11, 1)  # November 2024
date_to = datetime(2024, 11, 30)

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

print("Testing payment query...")
print(f"Area: {area_code}")
print(f"Date: November 2024")
print()

try:
    cursor.execute(payments_query, (area_code, date_from, date_to))
    row = cursor.fetchone()
    
    payment_value = float(row.TotalPayments) if row and row.TotalPayments else 0
    expected = 3115494.66
    difference = abs(payment_value - expected)
    
    print(f"✅ Query successful!")
    print(f"Payment value: {payment_value:,.2f} AMD")
    print(f"Expected:      {expected:,.2f} AMD")
    print(f"Difference:    {difference:,.2f} AMD ({(difference/expected)*100:.2f}%)")
    
    if difference < 100:
        print("\n🎯 VERY CLOSE MATCH!")
    elif difference < 1000:
        print("\n✓ Close match")
    else:
        print("\n⚠️ Significant difference")
        
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

conn.close()

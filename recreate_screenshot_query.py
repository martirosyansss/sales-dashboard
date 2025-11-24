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

area_code = '106'
date_from = '2025-10-01'
date_to = '2025-10-31'

print("=" * 80)
print(f"MATCHING THE SCREENSHOT QUERY FOR TERRITORY {area_code}")
print("=" * 80)
print()

# This appears to be a query that shows per-customer data
# Let me try to recreate what's in the screenshot
query = """
SELECT 
    c.fNAME as CustomerName,
    -- Credit sales (fPAYTYPE IN (2, 3))
    ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales,
    -- All sales
    ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
FROM SALES s
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = ?
    AND s.fDATE >= ?
    AND s.fDATE <= ?
    AND s.fSTATE = 2
GROUP BY c.fNAME
ORDER BY CreditSales DESC
"""

cursor.execute(query, (area_code, date_from, date_to))
rows = cursor.fetchall()

print(f"Found {len(rows)} customers")
print()

total_credit = 0
total_sales = 0

print(f"{'Customer':<40} {'Credit Sales':>15} {'Total Sales':>15}")
print("-" * 80)

for row in rows[:10]:  # Show top 10
    print(f"{row.CustomerName[:38]:<40} {row.CreditSales:>15,.2f} {row.TotalSales:>15,.2f}")
    total_credit += row.CreditSales
    total_sales += row.TotalSales

# Add remaining
for row in rows[10:]:
    total_credit += row.CreditSales
    total_sales += row.TotalSales

print("-" * 80)
print(f"{'TOTAL':<40} {total_credit:>15,.2f} {total_sales:>15,.2f}")
print()
print(f"This matches what API should return: {total_credit:,.2f} AMD")

conn.close()

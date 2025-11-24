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
print(f"TERRITORY {area_code} - COMPARING DIFFERENT QUERIES")
print("=" * 80)
print()

# METHOD 1: Simple - just SALES.fSALESAREA
print("METHOD 1: Simple query (s.fSALESAREA only):")
print("-" * 80)
cursor.execute("""
    SELECT 
        COUNT(DISTINCT s.fCUSTOMERID) as CustomerCount,
        COUNT(s.fISN) as SalesCount,
        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales,
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales
    FROM SALES s
    WHERE s.fSALESAREA = ?
        AND s.fDATE >= ?
        AND s.fDATE <= ?
        AND s.fSTATE = 2
""", (area_code, date_from, date_to))

row = cursor.fetchone()
simple_customers = row.CustomerCount
simple_sales = row.SalesCount
simple_total = row.TotalSales
simple_credit = row.CreditSales

print(f"Customers: {simple_customers}")
print(f"Sales: {simple_sales}")
print(f"Total: {simple_total:,.2f} AMD")
print(f"Credits: {simple_credit:,.2f} AMD")
print()

# METHOD 2: With CUSTOMERSALESAREAS join (current API method)
print("METHOD 2: With CUSTOMERSALESAREAS join (current API):")
print("-" * 80)
cursor.execute("""
    SELECT 
        COUNT(DISTINCT s.fCUSTOMERID) as CustomerCount,
        COUNT(s.fISN) as SalesCount,
        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales,
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fSALESAREA = ?
        AND s.fDATE >= ?
        AND s.fDATE <= ?
        AND s.fSTATE = 2
""", (area_code, area_code, date_from, date_to))

row = cursor.fetchone()
join_customers = row.CustomerCount
join_sales = row.SalesCount
join_total = row.TotalSales
join_credit = row.CreditSales

print(f"Customers: {join_customers}")
print(f"Sales: {join_sales}")
print(f"Total: {join_total:,.2f} AMD")
print(f"Credits: {join_credit:,.2f} AMD")
print()

# COMPARISON
print("=" * 80)
print("DIFFERENCE:")
print("=" * 80)
print(f"Lost customers: {simple_customers - join_customers}")
print(f"Lost sales: {simple_sales - join_sales}")
print(f"Lost total: {simple_total - join_total:,.2f} AMD")
print(f"Lost credits: {simple_credit - join_credit:,.2f} AMD")
print()

if simple_credit > join_credit:
    percent_lost = (simple_credit - join_credit) / simple_credit * 100
    print(f"PERCENTAGE LOST due to CUSTOMERSALESAREAS join: {percent_lost:.1f}%")
    print()
    print("ROOT CAUSE:")
    print("The CUSTOMERSALESAREAS join is filtering out sales that exist in SALES table")
    print("but customer is not mapped in CUSTOMERSALESAREAS for this territory.")

conn.close()

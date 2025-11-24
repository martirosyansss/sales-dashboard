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
print(f"TERRITORY {area_code} - TESTING DIFFERENT FILTERS")
print("=" * 80)
print()

# TEST 1: Only CUSTOMERSALESAREAS (no s.fSALESAREA check)
print("TEST 1: Using CUSTOMERSALESAREAS only (no s.fSALESAREA):")
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
        AND s.fDATE >= ?
        AND s.fDATE <= ?
        AND s.fSTATE = 2
""", (area_code, date_from, date_to))

row = cursor.fetchone()
test1_credit = row.CreditSales
print(f"Customers: {row.CustomerCount}")
print(f"Sales: {row.SalesCount}")
print(f"Total: {row.TotalSales:,.2f} AMD")
print(f"Credits: {test1_credit:,.2f} AMD")
print()

# TEST 2: CUSTOMERSALESAREAS + s.fSALESAREA (current method)
print("TEST 2: CUSTOMERSALESAREAS + s.fSALESAREA (current API method):")
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
test2_credit = row.CreditSales
print(f"Customers: {row.CustomerCount}")
print(f"Sales: {row.SalesCount}")
print(f"Total: {row.TotalSales:,.2f} AMD")
print(f"Credits: {test2_credit:,.2f} AMD")
print()

print("=" * 80)
print("DIFFERENCE:")
print("=" * 80)
print(f"Lost credits: {test1_credit - test2_credit:,.2f} AMD")

if test1_credit > test2_credit:
    percent = (test1_credit - test2_credit) / test1_credit * 100
    print(f"Lost: {percent:.1f}%")
    print()
    print("ROOT CAUSE: The s.fSALESAREA condition is filtering out sales")
    print("where SALES.fSALESAREA doesn't match CUSTOMERSALESAREAS.fSALESAREA")

conn.close()

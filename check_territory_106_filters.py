import pyodbc
from datetime import datetime
import json
import os

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
print(f"TERRITORY {area_code} - OCTOBER 2025 CREDITS ANALYSIS")
print("=" * 80)
print()

# 1. WITHOUT FILTERS
print("1. WITHOUT ANY FILTERS:")
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
base_customers = row.CustomerCount
base_sales_count = row.SalesCount
base_total = row.TotalSales
base_credit = row.CreditSales

print(f"Customers: {base_customers}")
print(f"Sales count: {base_sales_count}")
print(f"Total sales: {base_total:,.2f} AMD")
print(f"Credit sales: {base_credit:,.2f} AMD")
print()

# 2. WITH PRODUCT GROUP FILTER
print("2. WITH PRODUCT GROUP FILTER:")
print("-" * 80)

if os.path.exists('selected_product_groups.json'):
    with open('selected_product_groups.json', 'r', encoding='utf-8') as f:
        selected_groups = json.load(f)
    print(f"Selected product groups: {len(selected_groups)}")
    print(f"Groups: {selected_groups}")
    print()
    
    if selected_groups:
        placeholders_pg = ','.join(['?'] * len(selected_groups))
        
        cursor.execute(f"""
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
                AND EXISTS (
                    SELECT 1 FROM SALEDOCDETAILS sd
                    INNER JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
                    WHERE sd.fISN = s.fISN AND p.fGROUP IN ({placeholders_pg})
                )
        """, (area_code, area_code, date_from, date_to) + tuple(selected_groups))
        
        row = cursor.fetchone()
        pg_customers = row.CustomerCount
        pg_sales_count = row.SalesCount
        pg_total = row.TotalSales
        pg_credit = row.CreditSales
        
        print(f"Customers: {pg_customers} (was {base_customers})")
        print(f"Sales count: {pg_sales_count} (was {base_sales_count})")
        print(f"Total sales: {pg_total:,.2f} AMD (was {base_total:,.2f})")
        print(f"Credit sales: {pg_credit:,.2f} AMD (was {base_credit:,.2f})")
        print()
        print(f"LOST due to product filter:")
        print(f"  Customers: {base_customers - pg_customers}")
        print(f"  Sales: {base_sales_count - pg_sales_count}")
        print(f"  Total: {base_total - pg_total:,.2f} AMD ({(base_total - pg_total) / base_total * 100:.1f}%)")
        print(f"  Credits: {base_credit - pg_credit:,.2f} AMD ({(base_credit - pg_credit) / base_credit * 100:.1f}%)")
    else:
        pg_credit = base_credit
        print("No product groups selected - showing all data")
else:
    pg_credit = base_credit
    print("File selected_product_groups.json not found - showing all data")

print()
print("=" * 80)
print("SUMMARY:")
print("=" * 80)
print(f"WITHOUT filters: {base_credit:,.2f} AMD")
print(f"WITH filters: {pg_credit:,.2f} AMD")
print(f"LOST: {base_credit - pg_credit:,.2f} AMD")
print(f"Percentage shown: {pg_credit / base_credit * 100:.1f}%")

conn.close()

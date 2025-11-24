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

area_code = '106'

print("=" * 80)
print(f"TESTING DIFFERENT TIME PERIODS FOR TERRITORY {area_code}")
print("=" * 80)
print()

# 1. October 2025 only
print("1. OCTOBER 2025 ONLY:")
cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales,
        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fDATE >= '2025-10-01'
        AND s.fDATE <= '2025-10-31'
        AND s.fSTATE = 2
""", (area_code,))
row = cursor.fetchone()
print(f"Credit Sales: {row.CreditSales:,.2f} AMD")
print(f"Total Sales: {row.TotalSales:,.2f} AMD")
print()

# 2. All of 2025
print("2. ENTIRE YEAR 2025:")
cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales,
        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fDATE >= '2025-01-01'
        AND s.fDATE <= '2025-12-31'
        AND s.fSTATE = 2
""", (area_code,))
row = cursor.fetchone()
print(f"Credit Sales: {row.CreditSales:,.2f} AMD")
print(f"Total Sales: {row.TotalSales:,.2f} AMD")
print()

# 3. Last 12 months
print("3. LAST 12 MONTHS (from today):")
cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales,
        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fDATE >= DATEADD(MONTH, -12, GETDATE())
        AND s.fSTATE = 2
""", (area_code,))
row = cursor.fetchone()
print(f"Credit Sales: {row.CreditSales:,.2f} AMD")
print(f"Total Sales: {row.TotalSales:,.2f} AMD")
print()

# 4. WITHOUT any date filter
print("4. ALL TIME (no date filter):")
cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales,
        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fSTATE = 2
""", (area_code,))
row = cursor.fetchone()
print(f"Credit Sales: {row.CreditSales:,.2f} AMD")
print(f"Total Sales: {row.TotalSales:,.2f} AMD")
print()

print("=" * 80)
print("WHICH ONE IS CLOSEST TO 14,160,500.60?")
print("=" * 80)

conn.close()

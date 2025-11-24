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

print("TERRITORY 106 - FINDING THE MATCH")
print()

# October 2025
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
oct_credit = float(row.CreditSales or 0)
oct_total = float(row.TotalSales or 0)

# November 2024
cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales,
        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fDATE >= '2024-11-01'
        AND s.fDATE <= '2024-11-30'
        AND s.fSTATE = 2
""", (area_code,))
row = cursor.fetchone()
nov_credit = float(row.CreditSales or 0)
nov_total = float(row.TotalSales or 0)

print("October 2025:")
print(f"  Total Sales:  {oct_total:>15,.2f} (target: 6,097,396.08)")
print(f"  Credit Sales: {oct_credit:>15,.2f} (target: 1,880,479.58)")
print()
print("November 2024:")
print(f"  Total Sales:  {nov_total:>15,.2f} (target: 4,109,768.47)")
print(f"  Credit Sales: {nov_credit:>15,.2f} (target: 953,226.64)")
print()
print("Screenshot value:")
print(f"  6,494,151.40")
print()

# Check last 12 months TOTAL sales
cursor.execute("""
    SELECT 
        ISNULL(SUM(s.fTOTALSUM), 0) / 12.0 AS AvgTotalSales,
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) / 12.0 AS AvgCreditSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fDATE >= DATEADD(MONTH, -12, GETDATE())
        AND s.fSTATE = 2
""", (area_code,))
row = cursor.fetchone()
avg_total = float(row.AvgTotalSales or 0)
avg_credit = float(row.AvgCreditSales or 0)

print("Average (last 12 months):")
print(f"  Avg Total:    {avg_total:>15,.2f}")
print(f"  Avg Credit:   {avg_credit:>15,.2f}")

conn.close()

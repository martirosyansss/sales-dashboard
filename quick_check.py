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

print("TERRITORY 106 - CREDIT SALES BY PERIOD")
print()

# Last 12 months
cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fDATE >= DATEADD(MONTH, -12, GETDATE())
        AND s.fSTATE = 2
""", (area_code,))
last_12 = float(cursor.fetchone().CreditSales or 0)

print(f"Last 12 months: {last_12:,.2f}")

# Check if 14,160,500.60 / 12 = average per month
avg_per_month = 14160500.60 / 12.0
print(f"14,160,500.60 / 12 = {avg_per_month:,.2f}")
print()
print(f"Does {last_12:,.2f} match expected 14,160,500.60? {abs(last_12 - 14160500.60) < 1000}")

conn.close()

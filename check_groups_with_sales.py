"""
Check which Customer Groups have sales
"""
import pyodbc

# Connect to database
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

print("\n" + "="*80)
print("CUSTOMER GROUPS WITH SALES")
print("="*80 + "\n")

query = """
    SELECT 
        c.fGROUP, 
        COUNT(DISTINCT s.fISN) as SalesCount,
        SUM(s.fTOTALSUM) as TotalSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    WHERE s.fDATE >= '2024-01-01'
    GROUP BY c.fGROUP
    ORDER BY TotalSales DESC
"""
cursor.execute(query)
rows = cursor.fetchall()

print(f"{'Group':<10} {'Sales Count':<15} {'Total Sales':<20}")
print("-" * 50)

for row in rows:
    print(f"{row.fGROUP:<10} {row.SalesCount:<15} {row.TotalSales:<20,.2f}")

conn.close()

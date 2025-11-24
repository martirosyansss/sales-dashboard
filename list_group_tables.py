"""
List tables with GROUP in name
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
print("TABLES WITH 'GROUP' IN NAME")
print("="*80 + "\n")

query = "SELECT name FROM sys.tables WHERE name LIKE '%GROUP%'"
cursor.execute(query)
rows = cursor.fetchall()
for row in rows:
    print(row.name)

conn.close()

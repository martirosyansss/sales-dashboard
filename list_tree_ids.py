"""
List all TREEIDs in TREES table
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
print("DISTINCT TREEIDs")
print("="*80 + "\n")

query = "SELECT DISTINCT fTREEID FROM TREES ORDER BY fTREEID"
cursor.execute(query)
rows = cursor.fetchall()
for row in rows:
    print(row.fTREEID)

conn.close()

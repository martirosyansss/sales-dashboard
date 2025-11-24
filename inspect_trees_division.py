"""
Inspect TREES table for Division
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
print("TREES TABLE (Division)")
print("="*80 + "\n")

query = "SELECT * FROM TREES WHERE fTREEID = 'Division' ORDER BY fCODE"
cursor.execute(query)
columns = [column[0] for column in cursor.description]
print(f"Columns: {columns}\n")

rows = cursor.fetchall()
for row in rows:
    print(f"{row.fCODE}: {row.fCAPTION} (Parent: {row.fPARENT})")

conn.close()

"""
Inspect SALESAGENTDIVISIONS table
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
print("SALESAGENTDIVISIONS TABLE")
print("="*80 + "\n")

# Get first 20 rows
query = "SELECT TOP 20 * FROM SALESAGENTDIVISIONS"
cursor.execute(query)
columns = [column[0] for column in cursor.description]
print(f"Columns: {columns}\n")

rows = cursor.fetchall()
for row in rows:
    print(row)

print("\n" + "="*80)
print("DISTINCT fDIVISION")
print("="*80 + "\n")

cursor.execute("SELECT DISTINCT fDIVISION FROM SALESAGENTDIVISIONS ORDER BY fDIVISION")
divisions = cursor.fetchall()
for div in divisions:
    print(div.fDIVISION)

conn.close()

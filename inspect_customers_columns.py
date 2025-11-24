"""
Inspect CUSTOMERS table columns
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
print("CUSTOMERS TABLE COLUMNS")
print("="*80 + "\n")

query = "SELECT TOP 1 * FROM CUSTOMERS"
cursor.execute(query)
columns = [column[0] for column in cursor.description]
print(f"Columns: {columns}\n")

conn.close()

import pyodbc

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)

cursor = conn.cursor()
cursor.execute("""
    SELECT TOP 1 * FROM HICUSTOMERSDEBT
""")

print("\n=== HICUSTOMERSDEBT Columns ===")
for col in cursor.description:
    print(f"  {col[0]}")

conn.close()

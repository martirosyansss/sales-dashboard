import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes'
)

cursor = conn.cursor()

print("=" * 60)
print("Customer Groups from TREES (CustGrp)")
print("=" * 60)

cursor.execute("""
    SELECT fCODE, fCAPTION 
    FROM TREES 
    WHERE fTREEID = 'CustGrp'
    ORDER BY fCODE
""")

rows = cursor.fetchall()
print(f"\nFound {len(rows)} groups:")
for row in rows:
    print(f"  {row[0]} - {row[1]}")

cursor.close()
conn.close()

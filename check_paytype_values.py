import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# Get distinct payment types
print("\n=== DISTINCT PAYMENT TYPES ===")
cursor.execute("""
    SELECT DISTINCT fPAYTYPE, COUNT(*) as Count
    FROM SALES
    WHERE fSTATE = 2
    GROUP BY fPAYTYPE
    ORDER BY Count DESC
""")
for row in cursor.fetchall():
    paytype = row[0] if row[0] else '[NULL]'
    print(f"{paytype}: {row[1]} sales")

conn.close()

import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# Get payment type examples for each code
for paytype in ['1', '2', '3', '5', '6']:
    print(f"\n=== Type {paytype} examples ===")
    cursor.execute("""
        SELECT TOP 3 fCOMMENT
        FROM PAYMENTS
        WHERE fPAYMENTTYPE = ? AND fCOMMENT IS NOT NULL AND LEN(fCOMMENT) > 0
    """, (paytype,))
    for row in cursor.fetchall():
        print(f"  {row[0]}")

conn.close()

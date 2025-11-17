import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# Find payment type reference tables
print("\n=== TABLES WITH 'PAY' OR 'TYPE' IN NAME ===")
cursor.execute("""
    SELECT TABLE_NAME
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME LIKE '%PAY%' OR TABLE_NAME LIKE '%TYPE%'
    ORDER BY TABLE_NAME
""")
tables = cursor.fetchall()
for row in tables:
    print(row[0])

if not tables:
    print("No matching tables found")

# Let's check PAYMENTTYPES table if it exists
print("\n=== Checking common payment reference table names ===")
for table_name in ['PAYMENTTYPES', 'PAYTYPES', 'PAYMENT_TYPES', 'tblPaymentTypes']:
    try:
        cursor.execute(f"SELECT TOP 5 * FROM {table_name}")
        print(f"\n✓ Table {table_name} exists:")
        columns = [desc[0] for desc in cursor.description]
        print(f"Columns: {', '.join(columns)}")
        for row in cursor.fetchall():
            print(row)
    except Exception:
        pass

conn.close()

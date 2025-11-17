import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# Get all columns from SALES table
print("\n=== ALL SALES TABLE COLUMNS ===")
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'SALES'
    ORDER BY ORDINAL_POSITION
""")
for row in cursor.fetchall():
    print(f"{row[0]} - {row[1]}")

# Search for payment-related columns
print("\n=== PAYMENT-RELATED COLUMNS ===")
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'SALES'
    AND (
        COLUMN_NAME LIKE '%PAY%' OR
        COLUMN_NAME LIKE '%CASH%' OR
        COLUMN_NAME LIKE '%CARD%' OR
        COLUMN_NAME LIKE '%CREDIT%' OR
        COLUMN_NAME LIKE '%BANK%' OR
        COLUMN_NAME LIKE '%DEBT%' OR
        COLUMN_NAME LIKE '%TYPE%'
    )
""")
results = cursor.fetchall()
if results:
    for row in results:
        print(f"{row[0]} - {row[1]}")
else:
    print("No payment-related columns found")

conn.close()

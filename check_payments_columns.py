import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=localhost;'
    'DATABASE=SalesManagement;'
    'UID=sa;'
    'PWD=Aa123456;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()

print("=" * 70)
print("PAYMENTS TABLE - COLUMN STRUCTURE")
print("=" * 70)

query = """
SELECT 
    COLUMN_NAME,
    DATA_TYPE,
    CHARACTER_MAXIMUM_LENGTH,
    IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'PAYMENTS'
ORDER BY ORDINAL_POSITION
"""

cursor.execute(query)
rows = cursor.fetchall()

print(f"\nTotal columns: {len(rows)}")
print("\nColumn Details:")
print("-" * 70)
for row in rows:
    nullable = "NULL" if row.IS_NULLABLE == "YES" else "NOT NULL"
    max_len = f"({row.CHARACTER_MAXIMUM_LENGTH})" if row.CHARACTER_MAXIMUM_LENGTH else ""
    print(f"  {row.COLUMN_NAME:25} {row.DATA_TYPE}{max_len:15} {nullable}")

print("\n" + "=" * 70)

# Test simple query
print("\nTesting simple query on PAYMENTS table...")
try:
    cursor.execute("SELECT TOP 1 * FROM PAYMENTS")
    row = cursor.fetchone()
    if row:
        print("✅ Query successful")
        print(f"Columns in result: {[col[0] for col in cursor.description]}")
    else:
        print("⚠️ No rows found")
except Exception as e:
    print(f"❌ Error: {e}")

conn.close()

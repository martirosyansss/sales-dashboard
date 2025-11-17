import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# Check PAYMENTS table structure
print("\n=== PAYMENTS TABLE STRUCTURE ===")
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'PAYMENTS'
    ORDER BY ORDINAL_POSITION
""")
for row in cursor.fetchall():
    print(f"{row[0]} - {row[1]}")

# Check sample data
print("\n=== SAMPLE DATA FROM PAYMENTS ===")
cursor.execute("SELECT TOP 10 * FROM PAYMENTS")
columns = [desc[0] for desc in cursor.description]
print(f"Columns: {', '.join(columns)}")
for row in cursor.fetchall():
    print(row)

conn.close()

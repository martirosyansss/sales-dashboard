import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# Check for any dictionary/reference tables
print("\n=== All tables ===")
cursor.execute("""
    SELECT TABLE_NAME
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_TYPE = 'BASE TABLE'
    ORDER BY TABLE_NAME
""")
all_tables = [row[0] for row in cursor.fetchall()]
print(f"Total tables: {len(all_tables)}")

# Look for dictionary-like tables
dict_tables = [t for t in all_tables if any(x in t.upper() for x in ['DICT', 'REF', 'LOOKUP', 'CODE'])]
if dict_tables:
    print("\n=== Dictionary/Reference tables ===")
    for t in dict_tables:
        print(t)

# Check sample payment comments - they might have descriptive text
print("\n=== PAYMENT TYPES IN PAYMENTS TABLE (with comments) ===")
cursor.execute("""
    SELECT TOP 20 fPAYMENTTYPE, fCOMMENT
    FROM PAYMENTS
    WHERE fPAYMENTTYPE IS NOT NULL AND fCOMMENT IS NOT NULL
""")
for row in cursor.fetchall():
    print(f"Type {row[0]}: {row[1]}")

conn.close()

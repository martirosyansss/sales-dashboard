"""Check customer groups structure"""
from database import DatabaseConnection

db = DatabaseConnection()
db.connect()
cursor = db.connection.cursor()

print("=" * 60)
print("Customer Groups from TREES (CGroup)")
print("=" * 60)

cursor.execute("""
    SELECT fCODE, fCAPTION 
    FROM TREES 
    WHERE fTREEID = 'CGroup' 
    ORDER BY fCODE
""")

rows = cursor.fetchall()
print(f"\nFound {len(rows)} groups in TREES:")
for row in rows[:10]:
    print(f"  {row.fCODE} - {row.fCAPTION}")

if len(rows) > 10:
    print(f"  ... and {len(rows) - 10} more")

print("\n" + "=" * 60)
print("Customer Groups from CUSTOMERS.fGROUP")
print("=" * 60)

cursor.execute("""
    SELECT DISTINCT fGROUP 
    FROM CUSTOMERS 
    WHERE fGROUP IS NOT NULL AND fGROUP != ''
    ORDER BY fGROUP
""")

rows2 = cursor.fetchall()
print(f"\nFound {len(rows2)} distinct groups:")
for row in rows2[:10]:
    print(f"  {row.fGROUP}")

if len(rows2) > 10:
    print(f"  ... and {len(rows2) - 10} more")

cursor.close()

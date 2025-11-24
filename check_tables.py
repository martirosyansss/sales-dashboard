from app_v2 import db

try:
    conn = db.get_connection()
    cursor = conn.cursor()
    tables = ['CUSTOMERSALESAREAS']
    print("Analyzing tables:", tables)
    
    for table in tables:
        print(f"\nTable: {table}")
        cursor.execute(f"SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table}'")
        for col in cursor.fetchall():
            print(f"  {col[0]} ({col[1]})")
    conn.close()
except Exception as e:
    print(f"Error: {e}")

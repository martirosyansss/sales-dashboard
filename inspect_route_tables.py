import pyodbc
from app_v2 import db

def inspect_table(table_name):
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        print(f"\n--- {table_name} Columns ---")
        cursor.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table_name}'")
        columns = [row[0] for row in cursor.fetchall()]
        print(columns)
        
        # Also print a sample row to see data types/values
        print(f"--- {table_name} Sample ---")
        cursor.execute(f"SELECT TOP 1 * FROM {table_name}")
        row = cursor.fetchone()
        print(row)
        
        conn.close()
    except Exception as e:
        print(f"Error inspecting {table_name}: {e}")

if __name__ == "__main__":
    inspect_table('PLANNEDROUTESLISTMOBILE')
    inspect_table('ACTUALROUTES')
    inspect_table('SALES')

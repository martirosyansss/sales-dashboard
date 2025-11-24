import pyodbc
from app_v2 import db

def check_payment_types():
    print("Checking PAYMENTTYPES table...")
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Try to find a table with payment types
        cursor.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME LIKE '%PAY%'")
        tables = [r[0] for r in cursor.fetchall()]
        print(f"Found tables: {tables}")
        
        if 'PAYMENTTYPES' in tables:
            cursor.execute("SELECT * FROM PAYMENTTYPES")
            columns = [column[0] for column in cursor.description]
            print(f"Columns: {columns}")
            for row in cursor.fetchall():
                print(row)
        elif 'PAYTYPES' in tables:
            cursor.execute("SELECT * FROM PAYTYPES")
            columns = [column[0] for column in cursor.description]
            print(f"Columns: {columns}")
            for row in cursor.fetchall():
                print(row)
        else:
            print("No specific payment types table found.")
            
            # Check distinct types in SALES again with comments
            print("\nChecking SALES distinct paytypes with comments:")
            cursor.execute("""
                SELECT fPAYTYPE, COUNT(*), MAX(fCOMMENT)
                FROM SALES 
                WHERE fSTATE=2 
                GROUP BY fPAYTYPE
            """)
            for row in cursor.fetchall():
                pt = row[0] if row[0] is not None else 'NULL'
                print(f"Type {pt}: {row[1]} sales. Sample comment: {row[2]}")
                
            # Check NULL specifically
            print("\nChecking NULL paytype comments (top 5):")
            cursor.execute("SELECT TOP 5 fCOMMENT FROM SALES WHERE fSTATE=2 AND fPAYTYPE IS NULL")
            for row in cursor.fetchall():
                print(f"  {row[0]}")

                
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_payment_types()

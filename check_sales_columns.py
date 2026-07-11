
import pyodbc

def get_connection():
    try:
        conn = pyodbc.connect(
            'DRIVER={ODBC Driver 17 for SQL Server};'
            'SERVER=192.168.1.3;'
            'DATABASE=SalesManagement;'
            'UID=garni;'
            'PWD=garni2023;'
            'TrustServerCertificate=yes;'
        )
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

def check_columns():
    conn = get_connection()
    if not conn:
        return

    cursor = conn.cursor()
    
    query = """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'SALES'
        ORDER BY COLUMN_NAME
    """
    
    try:
        cursor.execute(query)
        print("Columns in SALES table:")
        for row in cursor.fetchall():
            print(f"- {row.COLUMN_NAME}")
    except Exception as e:
        print(f"Error executing query: {e}")
    
    conn.close()

if __name__ == "__main__":
    check_columns()

import pyodbc
from app_v2 import db
import datetime

def compare_credit_variants():
    print("Comparing Credit Calculation Variants...")
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Get data for last month
        today = datetime.datetime.now()
        start_date = today.replace(day=1) - datetime.timedelta(days=1)
        start_date = start_date.replace(day=1)
        end_date = today.replace(day=1)
        
        print(f"Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        
        # Variant 1: Only Type 2
        cursor.execute("""
            SELECT SUM(fTOTALSUM) 
            FROM SALES 
            WHERE fDATE >= ? AND fDATE < ? AND fSTATE=2 AND fPAYTYPE=2
        """, (start_date, end_date))
        v1 = cursor.fetchone()[0] or 0
        
        # Variant 2: Type 2 + 3
        cursor.execute("""
            SELECT SUM(fTOTALSUM) 
            FROM SALES 
            WHERE fDATE >= ? AND fDATE < ? AND fSTATE=2 AND fPAYTYPE IN (2, 3)
        """, (start_date, end_date))
        v2 = cursor.fetchone()[0] or 0
        
        # Variant 3: All Non-Cash (Not 1)
        cursor.execute("""
            SELECT SUM(fTOTALSUM) 
            FROM SALES 
            WHERE fDATE >= ? AND fDATE < ? AND fSTATE=2 AND fPAYTYPE != 1
        """, (start_date, end_date))
        v3 = cursor.fetchone()[0] or 0
        
        print(f"Variant 1 (Type 2 only):   {v1:,.2f}")
        print(f"Variant 2 (Type 2 + 3):    {v2:,.2f}")
        print(f"Variant 3 (All Non-Cash):  {v3:,.2f}")
        
        print(f"Difference V2-V1:          {v2-v1:,.2f}")
        
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    compare_credit_variants()

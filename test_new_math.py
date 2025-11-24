import pyodbc
from datetime import datetime

server = '192.168.1.3'
database = 'SalesManagement'
username = 'garni'
password = 'garni2023'

conn_str = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'

try:
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    
    print("Testing NEW MATH for Area 105...")
    
    # 1. Current Debt
    query_current = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as current_debt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    """
    cursor.execute(query_current)
    current_debt = float(cursor.fetchone()[0])
    print(f"Current Debt: {current_debt:,.2f}")
    
    # 2. Changes
    query_changes = """
    SELECT 
        YEAR(d.fDATE) as year,
        MONTH(d.fDATE) as month,
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as net_change
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
        AND d.fDATE >= DATEADD(MONTH, -13, GETDATE())
    GROUP BY YEAR(d.fDATE), MONTH(d.fDATE)
    """
    cursor.execute(query_changes)
    changes = {}
    for row in cursor.fetchall():
        changes[(row.year, row.month)] = float(row.net_change)
        
    # 3. Reconstruct History
    balances = []
    running_balance = current_debt
    balances.append(running_balance)
    
    today = datetime.now()
    curr_y, curr_m = today.year, today.month
    
    print("\nHistory Reconstruction:")
    print(f"Month {curr_m}/{curr_y}: Balance {running_balance:,.2f}")
    
    for i in range(11):
        change = changes.get((curr_y, curr_m), 0)
        prev_balance = running_balance - change
        balances.append(prev_balance)
        
        print(f"Change for {curr_m}/{curr_y}: {change:,.2f} -> Prev Balance: {prev_balance:,.2f}")
        
        running_balance = prev_balance
        curr_m -= 1
        if curr_m == 0:
            curr_m = 12
            curr_y -= 1
            
    avg_debt = sum(balances) / len(balances)
    print(f"\nAvg Debt (12 months): {avg_debt:,.2f}")
    
    # 4. Rest (Type01/02)
    query_rest = """
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    """
    cursor.execute(query_rest)
    row = cursor.fetchone()
    type01 = float(row.Type01)
    type02 = float(row.Type02)
    
    print(f"Type01: {type01:,.2f}")
    print(f"Type02: {type02:,.2f}")
    
    avg_debt_adjusted = avg_debt - abs(type01) - abs(type02)
    print(f"Avg Debt Adjusted: {avg_debt_adjusted:,.2f}")
    
    # 5. Plan
    season = 0.9
    growth = 1.1
    plan = avg_debt_adjusted * season * growth
    print(f"\nPlan (Season 0.9, Growth 1.1): {plan:,.2f}")
    
    conn.close()
    
except Exception as e:
    print(f"ERROR: {e}")

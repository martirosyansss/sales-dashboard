import pyodbc
import os

# Database connection (matching app_v2.py)
conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)

conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

area_code = '105'
requested_groups = ['002', '036']

# Build group filter
group_filter = ""
group_params = tuple()
if requested_groups:
    placeholders = ','.join(['?'] * len(requested_groups))
    group_filter = f" AND c.fGROUP IN ({placeholders})"
    group_params = tuple(requested_groups)

query = f"""
    SELECT 
        c.fCODE as CustomerCode,
        c.fNAME as CustomerName,
        c.fID as CustomerID,
        ISNULL(debt_data.DebtFromDocs, 0) as DebtFromDocs,
        ISNULL(rest_data.Type01, 0) as Type01,
        ISNULL(rest_data.Type02, 0) as Type02,
        ISNULL(debt_data.DebtFromDocs, 0) - ABS(ISNULL(rest_data.Type01, 0)) - ABS(ISNULL(rest_data.Type02, 0)) as RemainingDebt
    FROM CUSTOMERS c
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    OUTER APPLY (
        SELECT SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as DebtFromDocs
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID = c.fID
    ) debt_data
    OUTER APPLY (
        SELECT 
            SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END) as Type01,
            SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END) as Type02
        FROM HIRESTCUSTOMERSSUM r
        WHERE r.fCUSTOMERID = c.fID
    ) rest_data
    WHERE csa.fSALESAREA = ?
        {group_filter}
        AND (ISNULL(debt_data.DebtFromDocs, 0) - ABS(ISNULL(rest_data.Type01, 0)) - ABS(ISNULL(rest_data.Type02, 0))) > 0
    ORDER BY RemainingDebt DESC
"""

params = (area_code,) + group_params

print(f"Query parameters: {params}")
print(f"\nExecuting query...\n")

try:
    cursor.execute(query, params)
    
    customers = []
    total_debt = 0
    
    for row in cursor.fetchall():
        debt = float(row.RemainingDebt) if row.RemainingDebt else 0
        customers.append({
            'customerCode': row.CustomerCode,
            'customerName': row.CustomerName,
            'debtFromDocs': float(row.DebtFromDocs) if row.DebtFromDocs else 0,
            'type01': float(row.Type01) if row.Type01 else 0,
            'type02': float(row.Type02) if row.Type02 else 0,
            'remainingDebt': debt
        })
        total_debt += debt
    
    print(f"✓ Query executed successfully!")
    print(f"✓ Total customers: {len(customers)}")
    print(f"✓ Total debt: {total_debt:,.2f} AMD")
    
    if customers:
        print(f"\nFirst 5 customers:")
        for i, cust in enumerate(customers[:5], 1):
            print(f"  {i}. {cust['customerCode']} - {cust['customerName']}: {cust['remainingDebt']:,.0f} AMD")
    else:
        print("\n⚠ NO CUSTOMERS WITH DEBT!")
    
except Exception as e:
    print(f"✗ ERROR: {e}")
    import traceback
    traceback.print_exc()

finally:
    cursor.close()
    conn.close()

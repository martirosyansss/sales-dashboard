"""
Check debt calculation for A010/1 - Հունանյան Արման
"""

import pyodbc
import json

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

# Get manager ID
cursor.execute("SELECT fID FROM SALESAGENTS WHERE fCODE = 'A010/1'")
manager = cursor.fetchone()
if not manager:
    print("Manager A010/1 not found!")
    exit()

manager_id = manager.fID
print(f"Manager: A010/1 (ID={manager_id})")

# Load assigned groups
with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
    assignments = json.load(f)

assigned_groups = []
for group_code, manager_ids in assignments.items():
    if isinstance(manager_ids, list) and manager_id in manager_ids:
        assigned_groups.append(group_code)
    elif manager_id == manager_ids:
        assigned_groups.append(group_code)

print(f"Assigned groups: {len(assigned_groups)}")
placeholders = ','.join(['?'] * len(assigned_groups))

# Expected debt
expected = 2647651.10
print(f"\nExpected debt: {expected:,.2f} AMD")
print(f"  = 2,788,983.53 - 63,280.84 - 78,051.59")

# 1. Debt from documents
query1 = f"""
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
"""

cursor.execute(query1, (manager_id,) + tuple(assigned_groups))
debt_from_docs = float(cursor.fetchone().DebtFromDocs or 0)

# 1b. WITHOUT group filter
query1b = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
"""

cursor.execute(query1b, (manager_id,))
debt_no_filter = float(cursor.fetchone().DebtFromDocs or 0)

# 2. Type01 and Type02
query2 = f"""
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
"""

cursor.execute(query2, (manager_id,) + tuple(assigned_groups))
rest = cursor.fetchone()
type01 = float(rest.Type01 or 0)
type02 = float(rest.Type02 or 0)

# Calculate
calculated_debt = debt_from_docs - abs(type01) - abs(type02)

print(f"\n{'='*60}")
print("CALCULATION:")
print(f"{'='*60}")
print(f"Debt from documents (with group filter): {debt_from_docs:>12,.2f} AMD")
print(f"Debt from documents (NO group filter):   {debt_no_filter:>12,.2f} AMD")
print(f"Type01:               {type01:>20,.2f} AMD")
print(f"Type02:               {type02:>20,.2f} AMD")
print(f"{'-'*60}")
print(f"Formula: {debt_from_docs:,.2f} - {abs(type01):,.2f} - {abs(type02):,.2f}")
print(f"Result:               {calculated_debt:>20,.2f} AMD")
print(f"\nExpected:             {expected:>20,.2f} AMD")
print(f"Difference:           {abs(calculated_debt - expected):>20,.2f} AMD")
print(f"Error:                {abs(calculated_debt - expected) / expected * 100:>19.2f}%")

conn.close()

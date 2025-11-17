"""
Verify debt calculation fix - Credit minus Debit formula
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

try:
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    
    manager_id = 9  # A003
    
    # Load assigned groups
    with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
        assignments = json.load(f)
    
    assigned_groups = []
    for group_code, manager_ids in assignments.items():
        if isinstance(manager_ids, list) and manager_id in manager_ids:
            assigned_groups.append(group_code)
    
    placeholders = ','.join(['?'] * len(assigned_groups))
    
    print("=" * 80)
    print("DEBT CALCULATION VERIFICATION")
    print("=" * 80)
    print(f"\nManager: A003 (ID={manager_id})")
    print(f"Assigned groups: {len(assigned_groups)}")
    
    expected = 5289036.77
    print(f"Expected debt: {expected:,.2f} AMD")
    
    # OLD FORMULA: Debit - Credit (INCORRECT)
    print("\n" + "-" * 80)
    print("OLD FORMULA (INCORRECT): Debit - Credit")
    print("-" * 80)
    
    query_old = f"""
        SELECT 
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as Debt
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
    
    cursor.execute(query_old, (manager_id,) + tuple(assigned_groups))
    old_debt = float(cursor.fetchone().Debt or 0)
    print(f"Debt (D - C): {old_debt:,.2f} AMD")
    print(f"Difference: {abs(old_debt - expected):,.2f} AMD ({abs(old_debt - expected) / expected * 100:.2f}%)")
    
    # NEW FORMULA: Credit - Debit (CORRECT)
    print("\n" + "-" * 80)
    print("NEW FORMULA (CORRECT): Credit - Debit")
    print("-" * 80)
    
    query_new = f"""
        SELECT 
            SUM(CASE WHEN d.fDBCR = 'C' THEN d.fSUM ELSE -d.fSUM END) as Debt
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
    
    cursor.execute(query_new, (manager_id,) + tuple(assigned_groups))
    new_debt = float(cursor.fetchone().Debt or 0)
    print(f"Debt (C - D): {new_debt:,.2f} AMD")
    print(f"Difference: {abs(new_debt - expected):,.2f} AMD ({abs(new_debt - expected) / expected * 100:.2f}%)")
    
    # RESULT
    print("\n" + "=" * 80)
    print("RESULT")
    print("=" * 80)
    
    print(f"\nCurrent formula: Debit - Credit (D - C)")
    print(f"Result: {old_debt:,.2f} AMD")
    print(f"Expected: {expected:,.2f} AMD")
    print(f"Error: {abs(old_debt - expected) / expected * 100:.2f}%")
    
    if abs(old_debt - expected) / expected < 0.05:
        print(f"\n✓ SUCCESS! Formula is accurate (< 5% error)")
    else:
        print(f"\n✗ ERROR: Deviation is {abs(old_debt - expected) / expected * 100:.2f}%")
    
    conn.close()
    
except Exception as e:
    print(f"\nERROR: {e}")
    import traceback
    traceback.print_exc()

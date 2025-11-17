"""
Analyze the actual debt data structure to understand Credit vs Debit
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
print("ANALYZE DEBT STRUCTURE")
print("=" * 80)

# Get sample records
query = f"""
    SELECT TOP 10
        d.fDBCR,
        d.fSUM,
        c.fNAME as CustomerName
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
    ORDER BY d.fSUM DESC
"""

cursor.execute(query, (manager_id,) + tuple(assigned_groups))
print("\nSample records:")
print(f"{'fDBCR':<10} {'Amount':>15} {'Customer':<40}")
print("-" * 80)
for row in cursor.fetchall():
    print(f"{row.fDBCR:<10} {row.fSUM:>15,.2f} {row.CustomerName[:38]:<40}")

# Get totals
query_totals = f"""
    SELECT 
        d.fDBCR,
        COUNT(*) as RecordCount,
        SUM(d.fSUM) as TotalSum
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
    GROUP BY d.fDBCR
"""

cursor.execute(query_totals, (manager_id,) + tuple(assigned_groups))
print("\n" + "=" * 80)
print("TOTALS BY TYPE")
print("=" * 80)
print(f"{'Type':<10} {'Records':>10} {'Total Sum':>20}")
print("-" * 80)

totals = {}
for row in cursor.fetchall():
    totals[row.fDBCR] = float(row.TotalSum)
    print(f"{row.fDBCR:<10} {row.RecordCount:>10,} {row.TotalSum:>20,.2f}")

print("\n" + "=" * 80)
print("CALCULATION OPTIONS")
print("=" * 80)

if 'D' in totals and 'C' in totals:
    print(f"\nDebit (D):  {totals['D']:>20,.2f}")
    print(f"Credit (C): {totals['C']:>20,.2f}")
    print(f"\nD - C =     {totals['D'] - totals['C']:>20,.2f}")
    print(f"C - D =     {totals['C'] - totals['D']:>20,.2f}")
    print(f"|D - C| =   {abs(totals['D'] - totals['C']):>20,.2f}")
    
    expected = 5289036.77
    print(f"\nExpected:   {expected:>20,.2f}")
    print(f"\nWhich formula is closest?")
    print(f"  D - C error:   {abs(totals['D'] - totals['C'] - expected):>15,.2f} ({abs(totals['D'] - totals['C'] - expected) / expected * 100:>6.2f}%)")
    print(f"  C - D error:   {abs(totals['C'] - totals['D'] - expected):>15,.2f} ({abs(totals['C'] - totals['D'] - expected) / expected * 100:>6.2f}%)")
    print(f"  |D - C| error: {abs(abs(totals['D'] - totals['C']) - expected):>15,.2f} ({abs(abs(totals['D'] - totals['C']) - expected) / expected * 100:>6.2f}%)")

conn.close()

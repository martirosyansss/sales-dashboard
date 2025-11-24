import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()
expected = 3486213.18

print("\n" + "="*70)
print(f"CHECKING HICUSTOMERSDEBT FOR: {expected:,.2f} AMD")
print("Area 101 | October 2025")
print("="*70)

# Test HICUSTOMERSDEBT with Credit records (the old payment method)
tests = [
    ("HICUSTOMERSDEBT - Credit (C) via CUSTOMERSALESAREAS", """
        SELECT ISNULL(SUM(ABS(d.fSUM)), 0) FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE csa.fSALESAREA = '101'
            AND d.fDBCR = 'C'
            AND doc.fDATE >= '2025-10-01'
            AND doc.fDATE <= '2025-10-31'
            AND c.fID NOT IN (35461)
            AND c.fGROUP IN ('002', '036')
    """),
    ("HICUSTOMERSDEBT - Credit (C) without groups", """
        SELECT ISNULL(SUM(ABS(d.fSUM)), 0) FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE csa.fSALESAREA = '101'
            AND d.fDBCR = 'C'
            AND doc.fDATE >= '2025-10-01'
            AND doc.fDATE <= '2025-10-31'
            AND c.fID NOT IN (35461)
    """),
    ("HICUSTOMERSDEBT - Credit (C) with all groups", """
        SELECT ISNULL(SUM(ABS(d.fSUM)), 0) FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE csa.fSALESAREA = '101'
            AND d.fDBCR = 'C'
            AND doc.fDATE >= '2025-10-01'
            AND doc.fDATE <= '2025-10-31'
    """),
]

for test_name, query in tests:
    try:
        cursor.execute(query)
        row = cursor.fetchone()
        value = float(row[0]) if row and row[0] else 0
        diff = abs(value - expected)
        
        status = "🎯 EXACT MATCH!" if diff < 1 else ("✓ Close" if diff < 1000 else "")
        color = "GREEN" if diff < 1 else ("YELLOW" if diff < 1000 else "")
        
        print(f"\n{test_name}")
        print(f"  Value: {value:,.2f} AMD")
        print(f"  Diff:  {diff:,.2f} AMD {status}")
    except Exception as e:
        print(f"\n{test_name}")
        print(f"  ERROR: {e}")

print("\n" + "="*70)

conn.close()

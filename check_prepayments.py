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
print(f"TESTING WITH PREPAYMENTS INCLUDED: {expected:,.2f} AMD")
print("Area 101 | October 2025")
print("="*70)

tests = [
    ("PAYMENTS - Including prepayments", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01'
            AND p.fDATE <= '2025-10-31'
            AND p.fSTATE = 2
    """),
    ("PAYMENTS - All states, all prepayments", """
        SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
        WHERE p.fSALESAREA = '101'
            AND p.fDATE >= '2025-10-01'
            AND p.fDATE <= '2025-10-31'
    """),
    ("PAYMENTS + HICUSTOMERSDEBT combination?", """
        SELECT 
            (SELECT ISNULL(SUM(p.fSUM), 0) FROM PAYMENTS p
             WHERE p.fSALESAREA = '101'
               AND p.fDATE >= '2025-10-01' AND p.fDATE <= '2025-10-31'
               AND p.fSTATE = 2) +
            (SELECT ISNULL(SUM(ABS(d.fSUM)), 0) FROM HICUSTOMERSDEBT d
             INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
             WHERE doc.fDATE >= '2025-10-01' AND doc.fDATE <= '2025-10-31'
               AND d.fDBCR = 'C') AS Combined
    """),
]

for test_name, query in tests:
    try:
        cursor.execute(query)
        row = cursor.fetchone()
        value = float(row[0]) if row and row[0] else 0
        diff = abs(value - expected)
        
        status = "🎯 EXACT MATCH!" if diff < 10 else ("✓ Very Close" if diff < 100 else ("✓ Close" if diff < 1000 else ""))
        
        print(f"\n{test_name}")
        print(f"  Value: {value:,.2f} AMD")
        print(f"  Diff:  {diff:,.2f} AMD {status}")
    except Exception as e:
        print(f"\n{test_name}")
        print(f"  ERROR: {e}")

# Also check what prepayment values exist
print("\n" + "="*70)
print("PREPAYMENT BREAKDOWN")
print("="*70)

cursor.execute("""
    SELECT 
        CASE 
            WHEN p.fPREPAYMENT = 0 THEN 'Regular (0)'
            WHEN p.fPREPAYMENT > 0 THEN 'Prepayment (' + CAST(p.fPREPAYMENT AS VARCHAR) + ')'
            ELSE 'NULL'
        END AS PaymentType,
        COUNT(*) AS Count,
        ISNULL(SUM(p.fSUM), 0) AS Total
    FROM PAYMENTS p
    WHERE p.fSALESAREA = '101'
        AND p.fDATE >= '2025-10-01'
        AND p.fDATE <= '2025-10-31'
        AND p.fSTATE = 2
    GROUP BY CASE 
            WHEN p.fPREPAYMENT = 0 THEN 'Regular (0)'
            WHEN p.fPREPAYMENT > 0 THEN 'Prepayment (' + CAST(p.fPREPAYMENT AS VARCHAR) + ')'
            ELSE 'NULL'
        END
    ORDER BY Total DESC
""")

for row in cursor.fetchall():
    print(f"{row[0]:30} {row[1]:4} records   {float(row[2]):15,.2f} AMD")

prepayment_total = 3289272.23 - 3127184.79
print(f"\nPrepayments subtotal: ~{prepayment_total:,.2f} AMD")
print(f"If we add this to something: 3,127,184.79 + {prepayment_total:,.2f} = {3127184.79 + prepayment_total:,.2f}")

print("\n" + "="*70)

conn.close()

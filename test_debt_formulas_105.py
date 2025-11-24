import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

print("\n=== TESTING DIFFERENT DATE FILTERS: Area 105, Groups 002+036 ===")

# Test 1: d.fDATE <= '2025-10-31'
query1 = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDATE <= '2025-10-31'
"""
cursor.execute(query1)
debt1 = cursor.fetchone().DebtFromDocs

# Type01/Type02
query_rest = """
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
"""
cursor.execute(query_rest)
rest_row = cursor.fetchone()
type01 = rest_row.Type01
type02 = rest_row.Type02

final1 = debt1 - abs(type01) - abs(type02)
print(f"1. d.fDATE <= '2025-10-31': {debt1:,.2f} - rest = {final1:,.2f}")

# Test 2: d.fDATE < '2025-11-01' (current)
query2 = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDATE < '2025-11-01'
"""
cursor.execute(query2)
debt2 = cursor.fetchone().DebtFromDocs
final2 = debt2 - abs(type01) - abs(type02)
print(f"2. d.fDATE < '2025-11-01': {debt2:,.2f} - rest = {final2:,.2f}")

# Test 3: Without Type01/Type02
print(f"\n3. Without rest adjustment: {debt1:,.2f}")

# Test 4: Check if maybe Type01/Type02 shouldn't be subtracted
final4 = debt1 + abs(type01) + abs(type02)
print(f"4. Adding rest instead: {debt1:,.2f} + rest = {final4:,.2f}")

# Test 5: Only Type02
final5 = debt1 - abs(type02)
print(f"5. Only Type02 subtracted: {debt1:,.2f} - |{type02:,.2f}| = {final5:,.2f}")

print(f"\nExpected: 2,435,799.90")
print(f"Closest: {final1:,.2f} (diff: {abs(2435799.90 - final1):,.2f})")

conn.close()

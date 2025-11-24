import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

print("\n=== DEBT WITH TYPE01/TYPE02 DEDUCTION: Area 105, Groups 002+036 ===")

# 1. Дебет из HICUSTOMERSDEBT (на конец октября)
query_debit = """
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
cursor.execute(query_debit)
debit = float(cursor.fetchone().DebtFromDocs)

# 2. Type01 (Возвраты) и Type02 (Предоплата)
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
type01 = float(rest_row.Type01)
type02 = float(rest_row.Type02)

# 3. Итоговый долг
# ДОЛГ = ДЕБЕТ - ВОЗВРАТЫ - ПРЕДОПЛАТА
final_debt = debit - abs(type01) - abs(type02)

print(f"ДЕБЕТ (Debit from HICUSTOMERSDEBT): {debit:,.2f}")
print(f"Type01 (Возвраты/RETURN): {type01:,.2f}")
print(f"Type02 (Предоплата/ԿԱՆԽԱՎՃԱՐ): {type02:,.2f}")
print(f"\nФОРМУЛА: ДОЛГ = ДЕБЕТ - |ВОЗВРАТЫ| - |ПРЕДОПЛАТА|")
print(f"ДОЛГ = {debit:,.2f} - |{type01:,.2f}| - |{type02:,.2f}|")
print(f"ДОЛГ = {final_debt:,.2f}")

print(f"\nExpected (user's value): 2,435,799.90")
print(f"Difference: {abs(final_debt - 2435799.90):,.2f}")

conn.close()

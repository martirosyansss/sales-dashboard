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
area_code = '106'

print("TERRITORY 106 - COMPARING METRICS")
print()

# 1. Credit SALES (October 2025)
cursor.execute("""
    SELECT ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ? AND s.fDATE >= '2025-10-01' AND s.fDATE <= '2025-10-31' AND s.fSTATE = 2
""", (area_code,))
credit_sales = float(cursor.fetchone().CreditSales or 0)

# 2. Customer DEBT (current, all time)
# 2. Customer DEBT (current, all time) - CORRECT FORMULA
# Part A: Debit from HICUSTOMERSDEBT
cursor.execute("""
    SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as TotalDebt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
""", (area_code,))
debt_initial = float(cursor.fetchone().TotalDebt or 0)

# Part B: Type01 and Type02 from HIRESTCUSTOMERSSUM
cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
""", (area_code,))
row = cursor.fetchone()
type01 = float(row.Type01 or 0)
type02 = float(row.Type02 or 0)

debt = debt_initial - abs(type01) - abs(type02)

print(f"1. Credit SALES (Oct 2025):     {credit_sales:>20,.2f} AMD")
print(f"2. Customer DEBT (current):      {debt:>20,.2f} AMD")
print()
print(f"Expected value:                   14,160,500.60 AMD")
print()
print(f"Which matches?")
print(f"  Credit Sales? {abs(credit_sales - 14160500.60) < 1000}")
print(f"  Debt? {abs(debt - 14160500.60) < 1000}")

conn.close()

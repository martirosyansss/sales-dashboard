import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

print("\n=== DEBT FORMULA ANALYSIS: Area 105, Groups 002+036, October 2025 ===")

# 1. Debt at start of October (end of Sep)
query_start = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDATE < '2025-10-01'
"""
cursor.execute(query_start)
initial_debt = float(cursor.fetchone().DebtFromDocs or 0)

# 2. Sales in October
query_sales = """
    SELECT 
        ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND s.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND s.fDATE >= '2025-10-01' AND s.fDATE < '2025-11-01'
    AND s.fSTATE = 2
"""
cursor.execute(query_sales)
sales = float(cursor.fetchone().TotalSales or 0)

# 3. Payments in October (Credits from HICUSTOMERSDEBT)
query_payments = """
    SELECT 
        ISNULL(SUM(ABS(d.fSUM)), 0) as TotalPayments
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDBCR = 'C'
    AND d.fDATE >= '2025-10-01' AND d.fDATE < '2025-11-01'
"""
cursor.execute(query_payments)
payments = float(cursor.fetchone().TotalPayments or 0)

# 4. Debit (sales on credit) in October
query_debit = """
    SELECT 
        ISNULL(SUM(d.fSUM), 0) as TotalDebit
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDBCR = 'D'
    AND d.fDATE >= '2025-10-01' AND d.fDATE < '2025-11-01'
"""
cursor.execute(query_debit)
debit = float(cursor.fetchone().TotalDebit or 0)

print(f"Initial Debt (start of Oct): {initial_debt:,.2f}")
print(f"Debit (D) in October: {debit:,.2f}")
print(f"Credit (C) in October: {payments:,.2f}")
print(f"Sales in October: {sales:,.2f}")

# Calculate ending debt
ending_debt = initial_debt + debit - payments
print(f"\nEnding Debt = {initial_debt:,.2f} + {debit:,.2f} - {payments:,.2f} = {ending_debt:,.2f}")

# Check if maybe the user wants: InitialDebt - Payments + Sales
formula2 = initial_debt - payments + sales
print(f"Formula 2 (Initial - Payments + Sales): {formula2:,.2f}")

# Or maybe: Sales - Payments
formula3 = sales - payments
print(f"Formula 3 (Sales - Payments): {formula3:,.2f}")

print(f"\nUser's expected: 2,435,799.90")
print(f"User's 'Кредиты': 1,696,049.41")

# Check if 1,696,049.41 matches any of our values
print(f"\nDoes 1,696,049.41 match debit? {debit:,.2f}")
print(f"Difference from debit: {abs(1696049.41 - debit):,.2f}")

conn.close()

import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

print("\n=== DEBT HISTORY CHECK: Area 105, Groups 002+036 ===")

# Текущий запрос (транзакции ЗА октябрь)
query_current = """
    SELECT 
        FORMAT(EOMONTH(d.fDATE), 'yyyy-MM') AS Month,
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS TotalDebt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDATE >= '2025-10-01' AND d.fDATE < '2025-11-01'
    GROUP BY FORMAT(EOMONTH(d.fDATE), 'yyyy-MM')
"""
cursor.execute(query_current)
row = cursor.fetchone()
current_method = float(row.TotalDebt) if row else 0
print(f"Current method (transactions IN Oct): {current_method:,.2f}")

# Правильный запрос (баланс НА КОНЕЦ октября)
query_correct = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS TotalDebt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('002', '036')
    AND d.fDATE < '2025-11-01'
"""
cursor.execute(query_correct)
correct_balance = float(cursor.fetchone().TotalDebt)
print(f"Correct method (balance AT END of Oct): {correct_balance:,.2f}")

print(f"\nExpected: 2,593,250.47")
print(f"Matches correct method: {abs(correct_balance - 2593250.47) < 1}")

conn.close()

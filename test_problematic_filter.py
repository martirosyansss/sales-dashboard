import pyodbc
from datetime import datetime

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# Проверяем данные для клиента 1686 (из вашего примера)
query = """
    SELECT 
        c.fID AS CustomerId,
        c.fCODE AS CustomerCode,
        c.fNAME AS CustomerName,
        COUNT(s.fISN) AS SalesCount,
        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales,
        debt_data.DebtFromDocs,
        rest_data.Type01,
        rest_data.Type02,
        (debt_data.DebtFromDocs - ABS(rest_data.Type01) - ABS(rest_data.Type02)) AS Debt,
        payment_data.TotalPayments,
        payment_data.LastPaymentDate
    FROM CUSTOMERS c
    LEFT JOIN SALES s ON s.fCUSTOMERID = c.fID 
        AND s.fSTATE = 2 
        AND s.fDATE >= '2025-11-01' 
        AND s.fDATE <= '2025-11-30'
        AND s.fSALESAREA = '101'
    OUTER APPLY (
        SELECT 
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS DebtFromDocs
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID = c.fID
    ) AS debt_data
    OUTER APPLY (
        SELECT 
            ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) AS Type01,
            ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) AS Type02
        FROM HIRESTCUSTOMERSSUM r
        WHERE r.fCUSTOMERID = c.fID
    ) AS rest_data
    OUTER APPLY (
        SELECT 
            ISNULL(SUM(p.fSUM), 0) AS TotalPayments,
            MAX(p.fDATE) AS LastPaymentDate
        FROM PAYMENTS p
        WHERE p.fCUSTOMERID = c.fID
            AND p.fSTATE = 2
            AND p.fDATE >= '2025-11-01'
            AND p.fDATE <= '2025-11-30'
    ) AS payment_data
    WHERE c.fID IN (1686, 10651, 9473, 3892, 6842, 1025)
    GROUP BY c.fID, c.fCODE, c.fNAME, debt_data.DebtFromDocs, rest_data.Type01, rest_data.Type02, payment_data.TotalPayments, payment_data.LastPaymentDate
    ORDER BY c.fID
"""

cursor.execute(query)
rows = cursor.fetchall()

print("=" * 120)
print(f"{'ID':<6} {'Код':<6} {'Продажи':<12} {'Платежи':<12} {'Долг':<12} {'% долга':<10} {'Фильтр 102%'}")
print("=" * 120)

for row in rows:
    customer_id = row.CustomerId
    customer_code = row.CustomerCode
    sales = float(row.TotalSales) if row.TotalSales else 0
    payments = float(row.TotalPayments) if row.TotalPayments else 0
    debt = float(row.Debt) if row.Debt else 0
    
    # Рассчитываем процент долга
    debt_percent = (debt / sales * 100) if sales > 0 else 0
    
    # Проверяем критерий > 102%
    matches_102 = debt_percent > 102
    
    print(f"{customer_id:<6} {customer_code:<6} {sales:>11,.0f} {payments:>11,.0f} {debt:>11,.0f} {debt_percent:>9.1f}% {matches_102}")

conn.close()

print("\n" + "=" * 120)
print("Клиенты с долгом > 102% должны показываться при включении этого критерия")

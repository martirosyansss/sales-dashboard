import pyodbc
from datetime import datetime, timedelta

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()

print("=" * 80)
print("ПРОВЕРКА КРЕДИТОВ ПО ТЕРРИТОРИЯМ (для сравнения с /plans)")
print("=" * 80)

# Период: текущий месяц
today = datetime.now()
date_from = today.replace(day=1).strftime('%Y-%m-%d')
last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
date_to = last_day.strftime('%Y-%m-%d')

print(f"\nПериод: {date_from} - {date_to}")
print()

# Проверим для территорий 101, 102, 103
for area_code in ['101', '102', '103']:
    print(f"\n{'=' * 80}")
    print(f"ТЕРРИТОРИЯ: {area_code}")
    print('=' * 80)
    
    # Вариант 1: CreditSales = SUM(CASE WHEN fPAYTYPE IN (2,3))
    cursor.execute("""
        SELECT 
            ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales_Type23,
            ISNULL(SUM(CASE WHEN s.fPAYTYPE = 2 THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales_Type2,
            ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE csa.fSALESAREA = ?
            AND s.fSALESAREA = ?
            AND s.fDATE >= ?
            AND s.fDATE <= ?
            AND s.fSTATE = 2
    """, (area_code, area_code, date_from, date_to))
    
    row = cursor.fetchone()
    if row:
        credit_23 = row.CreditSales_Type23
        credit_2 = row.CreditSales_Type2
        total = row.TotalSales
        
        print(f"Всего продаж: {total:,.2f}")
        print(f"Кредиты (тип 2 только): {credit_2:,.2f}")
        print(f"Кредиты (тип 2+3): {credit_23:,.2f}")
        
        # Средние за 12 месяцев
        cursor.execute("""
            SELECT 
                ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) / 12.0 as AvgCredit
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE csa.fSALESAREA = ?
                AND s.fDATE >= DATEADD(MONTH, -12, GETDATE())
                AND s.fSTATE = 2
                AND s.fTOTALSUM > 0
                AND csa.fSALESAREA = s.fSALESAREA
            GROUP BY csa.fSALESAREA
        """, (area_code,))
        
        avg_row = cursor.fetchone()
        if avg_row:
            avg_credit = avg_row.AvgCredit or 0
            print(f"Средние кредиты за 12 мес: {avg_credit:,.2f}")

conn.close()

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

print("=" * 80)
print("ТОЧНЫЙ SQL ЗАПРОС ИЗ app_v2.py generate_plans() ДЛЯ ТЕРРИТОРИИ 106")
print("=" * 80)
print()

# Это ТОЧНЫЙ запрос из app_v2.py (lines 2098-2115)
query = """
SELECT 
    csa.fSALESAREA as area_code,
    ISNULL(SUM(s.fTOTALSUM), 0) / 12.0 as avg_monthly_sales,
    ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) / 12.0 as avg_monthly_credit
FROM SALES s
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE s.fDATE >= DATEADD(MONTH, -12, GETDATE())
    AND s.fSTATE = 2
    AND s.fTOTALSUM > 0
    AND csa.fSALESAREA = s.fSALESAREA
GROUP BY csa.fSALESAREA
HAVING csa.fSALESAREA = '106'
ORDER BY csa.fSALESAREA
"""

cursor.execute(query)
row = cursor.fetchone()

if row:
    print(f"area_code: {row.area_code}")
    print(f"avg_monthly_sales: {float(row.avg_monthly_sales):,.2f} AMD")
    print(f"avg_monthly_credit: {float(row.avg_monthly_credit):,.2f} AMD")
    print()
    print("Это те данные, которые API /api/generate-plans возвращает.")
    print()
    
    # Теперь давайте посмотрим детали - какие месяцы включены
    print("=" * 80)
    print("ДЕТАЛИ: Какие месяцы включены в расчёт?")
    print("=" * 80)
    cursor.execute("""
        SELECT 
            FORMAT(s.fDATE, 'yyyy-MM') as Month,
            COUNT(s.fISN) as SalesCount,
            ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales,
            ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) as CreditSales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE s.fDATE >= DATEADD(MONTH, -12, GETDATE())
            AND s.fSTATE = 2
            AND s.fTOTALSUM > 0
            AND csa.fSALESAREA = s.fSALESAREA
            AND csa.fSALESAREA = '106'
        GROUP BY FORMAT(s.fDATE, 'yyyy-MM')
        ORDER BY Month
    """)
    
    rows = cursor.fetchall()
    total_sales = 0
    total_credit = 0
    
    print(f"{'Месяц':<10} {'Продажи':>15} {'Кредиты':>15}")
    print("-" * 45)
    for r in rows:
        print(f"{r.Month:<10} {float(r.TotalSales):>15,.2f} {float(r.CreditSales):>15,.2f}")
        total_sales += float(r.TotalSales)
        total_credit += float(r.CreditSales)
    
    print("-" * 45)
    print(f"{'ИТОГО:':<10} {total_sales:>15,.2f} {total_credit:>15,.2f}")
    print(f"{'Среднее:':<10} {total_sales / 12.0:>15,.2f} {total_credit / 12.0:>15,.2f}")
    
else:
    print("Территория 106 не найдена!")

conn.close()

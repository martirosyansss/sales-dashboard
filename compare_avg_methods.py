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

area_code = '101'

print("=" * 80)
print(f"СРАВНЕНИЕ ДВУХ МЕТОДОВ РАСЧЁТА СРЕДНЕГО КРЕДИТА (Территория {area_code})")
print("=" * 80)
print()

# Метод 1: AVG(monthly totals) - как в generate_plans
print("МЕТОД 1: AVG(месячные итоги)")
print("-" * 80)
query1 = """
SELECT 
    csa.fSALESAREA as area_code,
    AVG(monthly_sales.total_sales) as avg_monthly_sales,
    AVG(monthly_sales.credit_sales) as avg_monthly_credit
FROM (
    SELECT 
        csa.fSALESAREA,
        YEAR(s.fDATE) as year,
        MONTH(s.fDATE) as month,
        SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END) as credit_sales,
        SUM(s.fTOTALSUM) as total_sales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE s.fDATE >= DATEADD(MONTH, -12, GETDATE())
        AND s.fSTATE = 2
        AND s.fTOTALSUM > 0
        AND csa.fSALESAREA = s.fSALESAREA
    GROUP BY csa.fSALESAREA, YEAR(s.fDATE), MONTH(s.fDATE)
) as monthly_sales
INNER JOIN CUSTOMERSALESAREAS csa ON monthly_sales.fSALESAREA = csa.fSALESAREA
WHERE csa.fSALESAREA = ?
GROUP BY csa.fSALESAREA
"""
cursor.execute(query1, (area_code,))
row1 = cursor.fetchone()
if row1:
    print(f"Средние продажи: {row1.avg_monthly_sales:,.2f}")
    print(f"Средние кредиты: {row1.avg_monthly_credit:,.2f}")

print()

# Метод 2: SUM / 12 - прямой метод
print("МЕТОД 2: SUM(все кредиты) / 12")
print("-" * 80)
query2 = """
SELECT 
    ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) / 12.0 as AvgCredit_Direct,
    ISNULL(SUM(s.fTOTALSUM), 0) / 12.0 as AvgSales_Direct
FROM SALES s
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = ?
    AND s.fDATE >= DATEADD(MONTH, -12, GETDATE())
    AND s.fSTATE = 2
    AND s.fTOTALSUM > 0
    AND csa.fSALESAREA = s.fSALESAREA
"""
cursor.execute(query2, (area_code,))
row2 = cursor.fetchone()
if row2:
    print(f"Средние продажи: {row2.AvgSales_Direct:,.2f}")
    print(f"Средние кредиты: {row2.AvgCredit_Direct:,.2f}")

print()
print("=" * 80)
print("РАЗНИЦА:")
print("=" * 80)
if row1 and row2:
    sales_diff = abs(row1.avg_monthly_sales - row2.AvgSales_Direct)
    credit_diff = abs(row1.avg_monthly_credit - row2.AvgCredit_Direct)
    print(f"Разница в продажах: {sales_diff:,.2f}")
    print(f"Разница в кредитах: {credit_diff:,.2f}")
    print()
    print("ПОЧЕМУ РАЗНИЦА:")
    print("Метод 1 (AVG месячных итогов) даёт среднее ТОЛЬКО по тем месяцам, где были продажи.")
    print("Метод 2 (SUM/12) делит на 12, даже если не во всех месяцах были продажи.")
    print()
    print("Если за последние 12 месяцев были продажи не во всех 12 месяцах,")
    print("то Метод 1 даст БОЛЕЕ ВЫСОКОЕ значение.")

# Проверим, сколько месяцев было с продажами
print()
print("=" * 80)
print("ПРОВЕРКА: Сколько месяцев было с продажами?")
print("=" * 80)
cursor.execute("""
    SELECT COUNT(DISTINCT FORMAT(s.fDATE, 'yyyy-MM')) as MonthsWithSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fDATE >= DATEADD(MONTH, -12, GETDATE())
        AND s.fSTATE = 2
        AND s.fTOTALSUM > 0
        AND csa.fSALESAREA = s.fSALESAREA
""", (area_code,))
months_row = cursor.fetchone()
if months_row:
    print(f"Месяцев с продажами: {months_row.MonthsWithSales}")
    print()
    if row1 and row2:
        print("Формула для Метода 1: SUM(все кредиты) / " + str(months_row.MonthsWithSales))
        print("Формула для Метода 2: SUM(все кредиты) / 12")

conn.close()

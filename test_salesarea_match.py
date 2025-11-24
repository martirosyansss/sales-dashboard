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

print("СРАВНЕНИЕ: С фильтром vs БЕЗ фильтра 'csa.fSALESAREA = s.fSALESAREA'")
print("=" * 80)
print()

# ВАРИАНТ 1: Текущий (С фильтром)
print("1. С фильтром 'csa.fSALESAREA = s.fSALESAREA' (текущий код):")
cursor.execute("""
    SELECT 
        ISNULL(SUM(s.fTOTALSUM), 0) / 12.0 as avg_monthly_sales,
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) / 12.0 as avg_monthly_credit
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE s.fDATE >= DATEADD(MONTH, -12, GETDATE())
        AND s.fSTATE = 2
        AND s.fTOTALSUM > 0
        AND csa.fSALESAREA = s.fSALESAREA
        AND csa.fSALESAREA = '106'
""")
row = cursor.fetchone()
with_filter_sales = float(row.avg_monthly_sales)
with_filter_credit = float(row.avg_monthly_credit)
print(f"   Avg Sales:  {with_filter_sales:,.2f}")
print(f"   Avg Credit: {with_filter_credit:,.2f}")
print()

# ВАРИАНТ 2: БЕЗ фильтра csa.fSALESAREA = s.fSALESAREA
print("2. БЕЗ фильтра 'csa.fSALESAREA = s.fSALESAREA':")
cursor.execute("""
    SELECT 
        ISNULL(SUM(s.fTOTALSUM), 0) / 12.0 as avg_monthly_sales,
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) / 12.0 as avg_monthly_credit
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE s.fDATE >= DATEADD(MONTH, -12, GETDATE())
        AND s.fSTATE = 2
        AND s.fTOTALSUM > 0
        AND csa.fSALESAREA = '106'
""")
row = cursor.fetchone()
without_filter_sales = float(row.avg_monthly_sales)
without_filter_credit = float(row.avg_monthly_credit)
print(f"   Avg Sales:  {without_filter_sales:,.2f}")
print(f"   Avg Credit: {without_filter_credit:,.2f}")
print()

# РАЗНИЦА
print("=" * 80)
print("РАЗНИЦА:")
print(f"   Sales:  {without_filter_sales - with_filter_sales:+,.2f} ({(without_filter_sales / with_filter_sales - 1) * 100:+.1f}%)")
print(f"   Credit: {without_filter_credit - with_filter_credit:+,.2f} ({(without_filter_credit / with_filter_credit - 1) * 100:+.1f}%)")
print()

if abs(without_filter_credit - with_filter_credit) > 100:
    print("ПРОБЛЕМА НАЙДЕНА!")
    print("Фильтр 'csa.fSALESAREA = s.fSALESAREA' отсекает часть продаж.")
    print("Это происходит когда SALES.fSALESAREA не совпадает с CUSTOMERSALESAREAS.fSALESAREA.")

conn.close()

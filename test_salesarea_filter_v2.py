import pyodbc
import os

conn_str = 'DRIVER={SQL Server};SERVER=192.168.1.7;DATABASE=SalesManagement;UID=sa;PWD=ggg'
conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

# Test query with csa.fSALESAREA = s.fSALESAREA filter
query = """
SELECT 
    csa.fSALESAREA as area_code,
    COUNT(s.fISN) as sale_count,
    ISNULL(SUM(s.fTOTALSUM), 0) / 12.0 as avg_monthly_sales,
    ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) / 12.0 as avg_monthly_credit
FROM SALES s
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE csa.fSALESAREA = s.fSALESAREA
    AND s.fDATE >= DATEADD(MONTH, -12, GETDATE())
    AND s.fSTATE = 2
    AND s.fTOTALSUM > 0
GROUP BY csa.fSALESAREA
ORDER BY csa.fSALESAREA
"""

print("Запрос с фильтром: WHERE csa.fSALESAREA = s.fSALESAREA")
print("="*60)
cursor.execute(query)
rows = cursor.fetchall()

for row in rows:
    print(f"Территория {row.area_code}: sales={row.avg_monthly_sales:,.2f}, credit={row.avg_monthly_credit:,.2f} (count={row.sale_count})")

print(f"\n\nВсего территорий: {len(rows)}")

# Теперь test without filter
query2 = """
SELECT 
    csa.fSALESAREA as area_code,
    COUNT(s.fISN) as sale_count,
    ISNULL(SUM(s.fTOTALSUM), 0) / 12.0 as avg_monthly_sales,
    ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) / 12.0 as avg_monthly_credit
FROM SALES s
INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
WHERE s.fDATE >= DATEADD(MONTH, -12, GETDATE())
    AND s.fSTATE = 2
    AND s.fTOTALSUM > 0
GROUP BY csa.fSALESAREA
ORDER BY csa.fSALESAREA
"""

print("\n\nЗапрос БЕЗ фильтра: (убрали WHERE csa.fSALESAREA = s.fSALESAREA)")
print("="*60)
cursor.execute(query2)
rows2 = cursor.fetchall()

for row in rows2:
    print(f"Территория {row.area_code}: sales={row.avg_monthly_sales:,.2f}, credit={row.avg_monthly_credit:,.2f} (count={row.sale_count})")

print(f"\n\nВсего территорий: {len(rows2)}")

# Найдем территорию 106 в обоих случаях
print("\n\nСРАВНЕНИЕ ДЛЯ ТЕРРИТОРИИ 106:")
print("="*60)
row1_106 = [r for r in rows if r.area_code == '106']
row2_106 = [r for r in rows2 if r.area_code == '106']

if row1_106:
    r = row1_106[0]
    print(f"С фильтром:    credit={r.avg_monthly_credit:,.2f}, sales={r.avg_monthly_sales:,.2f}, count={r.sale_count}")
if row2_106:
    r = row2_106[0]
    print(f"БЕЗ фильтра:   credit={r.avg_monthly_credit:,.2f}, sales={r.avg_monthly_sales:,.2f}, count={r.sale_count}")

cursor.close()
conn.close()

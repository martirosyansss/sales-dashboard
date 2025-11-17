import pyodbc
from datetime import datetime

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()

customer_id = 67529
date_from = '2025-11-01'
date_to = '2025-11-30'

print(f"Проверка покупок для клиента {customer_id}")
print(f"Период: {date_from} — {date_to}")
print("="*80)

# Точно такой же запрос как в API
query = """
    SELECT 
        s.fISN AS SaleId,
        s.fISN AS DocNumber,
        s.fDATE AS SaleDate,
        s.fTOTALSUM AS TotalSum,
        '' AS PaymentType,
        s.fSALESAREA AS SalesArea,
        sa.fCODE AS ManagerCode,
        sa.fNAME AS ManagerName
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    LEFT JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
    WHERE s.fCUSTOMERID = ?
        AND s.fSTATE = 2
        AND s.fDATE >= ?
        AND s.fDATE <= ?
    ORDER BY s.fDATE DESC, s.fISN DESC
"""

cursor.execute(query, (customer_id, date_from, date_to))
rows = cursor.fetchall()

print(f"Найдено продаж: {len(rows)}")
print("="*80)

if rows:
    total = 0
    for i, row in enumerate(rows[:3]):
        print(f"\n{i+1}. Продажа {row.SaleId}")
        print(f"   Дата: {row.SaleDate}")
        print(f"   Сумма: {row.TotalSum:,.2f} AMD")
        print(f"   Территория: {row.SalesArea}")
        print(f"   Менеджер: {row.ManagerCode} - {row.ManagerName}")
        
        total += float(row.TotalSum) if row.TotalSum else 0
        
        # Получить товары
        cursor.execute("""
            SELECT 
                sl.fLINENO AS [LineNo],
                p.fCODE AS ProductCode,
                p.fNAME AS ProductName,
                sl.fQTY AS Quantity,
                sl.fPRICE AS Price,
                sl.fTOTALSUM AS LineTotal
            FROM SALESLINES sl
            LEFT JOIN PRODUCTS p ON sl.fPRODUCTID = p.fID
            WHERE sl.fISN = ?
            ORDER BY sl.fLINENO
        """, (row.SaleId,))
        
        products = cursor.fetchall()
        print(f"   Товаров: {len(products)}")
        if products:
            for prod in products[:2]:
                print(f"      - {prod.ProductCode}: {prod.ProductName}, Кол: {prod.Quantity}, Сумма: {prod.LineTotal}")
    
    print(f"\nИтого по {len(rows)} продажам: {total:,.2f} AMD")
else:
    print("Продажи не найдены!")

conn.close()

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

# Получаем fID для клиента с кодом 10651
print("Поиск клиента с кодом 10651...")
cursor.execute("SELECT fID, fCODE, fNAME FROM CUSTOMERS WHERE fCODE = ?", ('10651',))
customer = cursor.fetchone()

if not customer:
    print("Клиент не найден!")
    conn.close()
    exit()

customer_id = customer.fID
print(f"Найден: ID={customer_id}, Code={customer.fCODE}, Name={customer.fNAME}")
print("="*80)

date_from = '2025-11-01'
date_to = '2025-11-30'

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
    for i, row in enumerate(rows):
        print(f"\n{i+1}. Продажа:")
        print(f"   ID: {row.SaleId}")
        print(f"   Дата: {row.SaleDate}")
        print(f"   Сумма: {row.TotalSum:,.2f} AMD")
        print(f"   Территория: {row.SalesArea}")
        print(f"   Менеджер: {row.ManagerCode} - {row.ManagerName}")
        
        total += float(row.TotalSum) if row.TotalSum else 0
        
        # Получить товары
        cursor.execute("""
            SELECT 
                sd.fROWNUM AS [LineNo],
                p.fCODE AS ProductCode,
                p.fNAME AS ProductName,
                sd.fQUANTITY AS Quantity,
                sd.fDISCOUNTEDPRICE AS Price,
                sd.fSUM AS LineTotal
            FROM SALEDOCDETAILS sd
            LEFT JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
            WHERE sd.fISN = ?
            ORDER BY sd.fROWNUM
        """, (row.SaleId,))
        
        products = cursor.fetchall()
        print(f"   Товаров: {len(products)}")
        if products:
            for prod in products[:3]:
                print(f"      {prod.LineNo}. {prod.ProductCode}: {prod.ProductName}")
                print(f"         Кол: {prod.Quantity}, Цена: {prod.Price}, Сумма: {prod.LineTotal}")
    
    print(f"\n{'='*80}")
    print(f"Итого по {len(rows)} продажам: {total:,.2f} AMD")
else:
    print("Продажи не найдены!")

conn.close()

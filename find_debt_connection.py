import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# Показать все GUID колонки в CUSTOMERS
print("\n=== GUID КОЛОНКИ В CUSTOMERS ===")
cursor.execute("""
    SELECT COLUMN_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'CUSTOMERS'
    AND DATA_TYPE = 'uniqueidentifier'
""")
guid_cols = cursor.fetchall()
for row in guid_cols:
    print(f"  {row[0]}")

# Попробовать найти связь через одну из GUID колонок
if guid_cols:
    for col in guid_cols:
        col_name = col[0]
        print(f"\n=== ПРОБУЕМ СВЯЗЬ ЧЕРЕЗ {col_name} ===")
        try:
            cursor.execute(f"""
                SELECT TOP 3
                    c.fID,
                    c.fCODE,
                    c.fNAME,
                    h.fSUM as DebtSum
                FROM CUSTOMERS c
                INNER JOIN HIRESTCUSTOMERSDEBT h ON c.{col_name} = h.fDEBTDOCISN
                WHERE h.fSUM > 0
                ORDER BY h.fSUM DESC
            """)
            rows = cursor.fetchall()
            if rows:
                print(f"✓ Связь найдена через {col_name}!")
                for row in rows:
                    print(f"  Customer {row.fCODE} ({row.fNAME}): Debt = {row.DebtSum}")
                break
            else:
                print(f"  Нет данных")
        except Exception as e:
            print(f"  Ошибка: {e}")

# Альтернативный подход - рассчитать долг как Продажи - Платежи
print("\n=== РАСЧЕТ ДОЛГА: ПРОДАЖИ - ПЛАТЕЖИ ===")
cursor.execute("""
    SELECT TOP 5
        c.fID,
        c.fCODE,
        c.fNAME,
        ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales,
        ISNULL((SELECT SUM(p.fSUM) 
                FROM PAYMENTS p 
                WHERE p.fCUSTOMERID = c.fID 
                AND p.fSTATE = 2), 0) as TotalPayments,
        ISNULL(SUM(s.fTOTALSUM), 0) - 
        ISNULL((SELECT SUM(p.fSUM) 
                FROM PAYMENTS p 
                WHERE p.fCUSTOMERID = c.fID 
                AND p.fSTATE = 2), 0) as Debt
    FROM CUSTOMERS c
    LEFT JOIN SALES s ON c.fID = s.fCUSTOMERID AND s.fSTATE = 2
    GROUP BY c.fID, c.fCODE, c.fNAME
    HAVING ISNULL(SUM(s.fTOTALSUM), 0) - 
           ISNULL((SELECT SUM(p.fSUM) 
                   FROM PAYMENTS p 
                   WHERE p.fCUSTOMERID = c.fID 
                   AND p.fSTATE = 2), 0) > 100000
    ORDER BY Debt DESC
""")
for row in cursor.fetchall():
    print(f"  {row.fCODE} ({row.fNAME}):")
    print(f"    Sales: {row.TotalSales:,.0f}")
    print(f"    Payments: {row.TotalPayments:,.0f}")
    print(f"    Debt: {row.Debt:,.0f}")

conn.close()

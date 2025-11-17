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

# Найти клиентов с продажами в ноябре 2025
cursor.execute("""
    SELECT TOP 5
        c.fID,
        c.fCODE,
        c.fNAME,
        COUNT(s.fISN) as SalesCount,
        SUM(s.fTOTALSUM) as TotalSum
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    WHERE s.fSTATE = 2
        AND s.fDATE >= '2025-11-01'
        AND s.fDATE <= '2025-11-30'
    GROUP BY c.fID, c.fCODE, c.fNAME
    ORDER BY SUM(s.fTOTALSUM) DESC
""")

print("Топ-5 клиентов с продажами в ноябре 2025:")
print("=" * 80)
rows = cursor.fetchall()
if rows:
    for row in rows:
        print(f"ID: {row.fID}, Code: {row.fCODE}, Name: {row.fNAME}")
        print(f"  Sales: {row.SalesCount}, Sum: {row.TotalSum:,.2f} AMD")
        print()
        
        # Для первого клиента показать детали
        if row == rows[0]:
            cursor.execute("""
                SELECT TOP 3
                    s.fISN,
                    s.fDATE,
                    s.fTOTALSUM,
                    '' as PaymentType
                FROM SALES s
                WHERE s.fCUSTOMERID = ?
                    AND s.fSTATE = 2
                    AND s.fDATE >= '2025-11-01'
                    AND s.fDATE <= '2025-11-30'
                ORDER BY s.fDATE DESC
            """, (row.fID,))
            
            print("  Примеры продаж:")
            for sale in cursor.fetchall():
                print(f"    ISN: {sale.fISN}, Date: {sale.fDATE}, Sum: {sale.fTOTALSUM}, Payment: {sale.PaymentType}")
            print()
else:
    print("Нет продаж в ноябре 2025!")
    
    # Проверим вообще есть ли продажи в 2025
    cursor.execute("""
        SELECT COUNT(*) as cnt, MIN(fDATE) as min_date, MAX(fDATE) as max_date
        FROM SALES
        WHERE fSTATE = 2 AND YEAR(fDATE) = 2025
    """)
    info = cursor.fetchone()
    print(f"Всего продаж в 2025: {info.cnt}")
    print(f"Диапазон дат: {info.min_date} - {info.max_date}")

conn.close()

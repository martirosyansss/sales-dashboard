"""
Проверка клиентов менеджера A003
"""

import pyodbc

conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)

try:
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    
    # Получить инфо о менеджере A003
    cursor.execute("""
        SELECT fID, fCODE, fNAME 
        FROM SALESAGENTS 
        WHERE fCODE = 'A003'
    """)
    manager = cursor.fetchone()
    
    if manager:
        print(f"Менеджер A003: {manager.fNAME} (ID={manager.fID})")
        manager_id = manager.fID
    else:
        print("Менеджер A003 не найден!")
        exit(1)
    
    # Проверить продажи менеджера за ноябрь 2025
    print("\n" + "=" * 80)
    print("ПРОДАЖИ ЗА НОЯБРЬ 2025")
    print("=" * 80)
    
    cursor.execute("""
        SELECT 
            COUNT(DISTINCT s.fCUSTOMERID) as CustomerCount,
            COUNT(s.fISN) as SalesCount,
            ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales
        FROM SALES s
        WHERE s.fSALESAGENTID = ?
            AND s.fDATE >= '2025-11-01'
            AND s.fDATE < '2025-12-01'
            AND s.fSTATE = 2
    """, (manager_id,))
    
    sales = cursor.fetchone()
    print(f"Клиентов: {sales.CustomerCount}")
    print(f"Продаж: {sales.SalesCount}")
    print(f"Сумма: {sales.TotalSales:,.2f} AMD")
    
    # Группы клиентов
    print("\n" + "=" * 80)
    print("ГРУППЫ КЛИЕНТОВ С ПРОДАЖАМИ")
    print("=" * 80)
    
    cursor.execute("""
        SELECT 
            c.fGROUP,
            COUNT(DISTINCT c.fID) as CustomerCount,
            COUNT(s.fISN) as SalesCount,
            ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        WHERE s.fSALESAGENTID = ?
            AND s.fDATE >= '2025-11-01'
            AND s.fDATE < '2025-12-01'
            AND s.fSTATE = 2
        GROUP BY c.fGROUP
        ORDER BY TotalSales DESC
    """, (manager_id,))
    
    groups = cursor.fetchall()
    for row in groups:
        print(f"Группа {row.fGROUP}: {row.CustomerCount} клиентов, {row.SalesCount} продаж, {row.TotalSales:,.2f} AMD")
    
    # Долг через DOCUMENTS
    print("\n" + "=" * 80)
    print("ДОЛГ ЧЕРЕЗ DOCUMENTS (текущая логика)")
    print("=" * 80)
    
    cursor.execute("""
        SELECT 
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fSALESAGENTID = ?
    """, (manager_id,))
    
    debt_docs = cursor.fetchone()
    print(f"Долг из HICUSTOMERSDEBT: {debt_docs.DebtFromDocs:,.2f} AMD")
    
    # Долг напрямую через SALESAGENTID в SALES
    print("\n" + "=" * 80)
    print("ДОЛГ ЧЕРЕЗ SALES.fSALESAGENTID")
    print("=" * 80)
    
    cursor.execute("""
        SELECT 
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID 
            FROM SALES 
            WHERE fSALESAGENTID = ?
        )
    """, (manager_id,))
    
    debt_via_sales = cursor.fetchone()
    print(f"Долг клиентов менеджера: {debt_via_sales.DebtFromDocs:,.2f} AMD")
    
    # Топ клиентов с долгами
    print("\n" + "=" * 80)
    print("ТОП 10 КЛИЕНТОВ С ДОЛГАМИ")
    print("=" * 80)
    
    cursor.execute("""
        SELECT TOP 10
            c.fCODE,
            c.fNAME,
            c.fGROUP,
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as Debt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID 
            FROM SALES 
            WHERE fSALESAGENTID = ?
        )
        GROUP BY c.fCODE, c.fNAME, c.fGROUP
        HAVING SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) > 0
        ORDER BY Debt DESC
    """, (manager_id,))
    
    debtors = cursor.fetchall()
    for row in debtors:
        print(f"{row.fCODE} - {row.fNAME} (Группа: {row.fGROUP}): {row.Debt:,.2f} AMD")
    
    conn.close()
    
except Exception as e:
    print(f"ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

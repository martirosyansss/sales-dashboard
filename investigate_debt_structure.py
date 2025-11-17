"""
Исследование структуры долгов в БД
Проверим все таблицы, связанные с долгами
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
    
    print("=" * 80)
    print("ИССЛЕДОВАНИЕ СТРУКТУРЫ ДОЛГОВ")
    print("=" * 80)
    
    # 1. Структура таблицы HICUSTOMERSDEBT
    print("\n1. СТРУКТУРА ТАБЛИЦЫ HICUSTOMERSDEBT:")
    print("-" * 80)
    cursor.execute("""
        SELECT TOP 10
            d.fISN,
            d.fDEBTDOCISN,
            d.fDBCR,
            d.fSUM,
            doc.fCUSTOMERID,
            doc.fSALESAGENTID,
            c.fNAME as CustomerName,
            c.fGROUP
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        ORDER BY d.fSUM DESC
    """)
    
    print(f"{'fISN':<10} {'fDEBTDOCISN':<15} {'D/C':<5} {'fSUM':<15} {'Customer':<30} {'Group':<10}")
    print("-" * 80)
    for row in cursor.fetchall():
        print(f"{row.fISN:<10} {row.fDEBTDOCISN:<15} {row.fDBCR:<5} {row.fSUM:<15,.2f} {row.CustomerName[:30]:<30} {row.fGROUP or 'N/A':<10}")
    
    # 2. Структура таблицы HIRESTCUSTOMERSSUM
    print("\n\n2. СТРУКТУРА ТАБЛИЦЫ HIRESTCUSTOMERSSUM:")
    print("-" * 80)
    cursor.execute("""
        SELECT TOP 10
            r.fCUSTOMERID,
            r.fTYPE,
            r.fSUM,
            c.fNAME as CustomerName,
            c.fGROUP
        FROM HIRESTCUSTOMERSSUM r
        INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
        ORDER BY ABS(r.fSUM) DESC
    """)
    
    print(f"{'fCUSTOMERID':<15} {'Type':<5} {'fSUM':<15} {'Customer':<30} {'Group':<10}")
    print("-" * 80)
    for row in cursor.fetchall():
        print(f"{row.fCUSTOMERID:<15} {row.fTYPE:<5} {row.fSUM:<15,.2f} {row.CustomerName[:30]:<30} {row.fGROUP or 'N/A':<10}")
    
    # 3. Проверка связи DOCUMENTS с SALESAGENTS
    print("\n\n3. СВЯЗЬ DOCUMENTS -> SALESAGENTS:")
    print("-" * 80)
    cursor.execute("""
        SELECT TOP 10
            doc.fISN,
            doc.fCUSTOMERID,
            doc.fSALESAGENTID,
            c.fNAME as CustomerName,
            sa.fNAME as SalesAgentName
        FROM DOCUMENTS doc
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        LEFT JOIN SALESAGENTS sa ON doc.fSALESAGENTID = sa.fID
        WHERE doc.fSALESAGENTID IS NOT NULL
        ORDER BY doc.fISN DESC
    """)
    
    print(f"{'DocISN':<10} {'CustomerID':<12} {'AgentID':<10} {'Customer':<30} {'Agent':<20}")
    print("-" * 80)
    for row in cursor.fetchall():
        agent_name = row.SalesAgentName if row.SalesAgentName else "NULL"
        print(f"{row.fISN:<10} {row.fCUSTOMERID:<12} {row.fSALESAGENTID or 0:<10} {row.CustomerName[:30]:<30} {agent_name[:20]:<20}")
    
    # 4. Сравнение методов расчета долга для менеджера A003 (ID=9)
    print("\n\n4. СРАВНЕНИЕ МЕТОДОВ РАСЧЕТА ДОЛГА ДЛЯ МЕНЕДЖЕРА A003 (ID=9):")
    print("=" * 80)
    
    manager_id = 9
    
    # Метод 1: Через DOCUMENTS.fSALESAGENTID (старый)
    print("\nМЕТОД 1: Через DOCUMENTS.fSALESAGENTID")
    print("-" * 80)
    cursor.execute("""
        SELECT 
            COUNT(*) as RecordCount,
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE 0 END) as DebitSum,
            SUM(CASE WHEN d.fDBCR = 'C' THEN d.fSUM ELSE 0 END) as CreditSum,
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fSALESAGENTID = ?
    """, (manager_id,))
    
    method1 = cursor.fetchone()
    print(f"Записей: {method1.RecordCount}")
    print(f"Дебет: {method1.DebitSum:,.2f} AMD")
    print(f"Кредит: {method1.CreditSum:,.2f} AMD")
    print(f"Чистый долг: {method1.NetDebt:,.2f} AMD")
    
    # Метод 2: Через клиентов из SALES (новый)
    print("\n\nМЕТОД 2: Через клиентов из SALES")
    print("-" * 80)
    cursor.execute("""
        SELECT 
            COUNT(*) as RecordCount,
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE 0 END) as DebitSum,
            SUM(CASE WHEN d.fDBCR = 'C' THEN d.fSUM ELSE 0 END) as CreditSum,
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
    """, (manager_id,))
    
    method2 = cursor.fetchone()
    print(f"Записей: {method2.RecordCount}")
    print(f"Дебет: {method2.DebitSum:,.2f} AMD")
    print(f"Кредит: {method2.CreditSum:,.2f} AMD")
    print(f"Чистый долг: {method2.NetDebt:,.2f} AMD")
    
    # 5. Проверка HIRESTCUSTOMERSSUM для A003
    print("\n\n5. HIRESTCUSTOMERSSUM ДЛЯ МЕНЕДЖЕРА A003:")
    print("=" * 80)
    
    # Type 01
    cursor.execute("""
        SELECT 
            COUNT(*) as RecordCount,
            SUM(r.fSUM) as TotalSum
        FROM HIRESTCUSTOMERSSUM r
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        AND r.fTYPE = '01'
    """, (manager_id,))
    
    type01 = cursor.fetchone()
    print(f"\nType='01':")
    print(f"  Записей: {type01.RecordCount}")
    print(f"  Сумма: {type01.TotalSum:,.2f} AMD")
    
    # Type 02
    cursor.execute("""
        SELECT 
            COUNT(*) as RecordCount,
            SUM(r.fSUM) as TotalSum
        FROM HIRESTCUSTOMERSSUM r
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        AND r.fTYPE = '02'
    """, (manager_id,))
    
    type02 = cursor.fetchone()
    print(f"\nType='02':")
    print(f"  Записей: {type02.RecordCount}")
    print(f"  Сумма: {type02.TotalSum:,.2f} AMD")
    
    # 6. Итоговый расчет долга по формуле
    print("\n\n6. ИТОГОВЫЙ РАСЧЕТ ПО ФОРМУЛЕ:")
    print("=" * 80)
    
    debt_from_docs = method2.NetDebt
    rest_type01 = type01.TotalSum if type01.TotalSum else 0
    rest_type02 = type02.TotalSum if type02.TotalSum else 0
    
    # В коде мы меняем местами: Type02 -> RestType01, Type01 -> RestType02
    swapped_rest_type01 = rest_type02  # Type='02' становится RestType01
    swapped_rest_type02 = rest_type01  # Type='01' становится RestType02
    
    total_debt = debt_from_docs + swapped_rest_type01 + swapped_rest_type02
    
    print(f"DebtFromDocs (Метод 2): {debt_from_docs:,.2f} AMD")
    print(f"Type='01' из БД: {rest_type01:,.2f} AMD")
    print(f"Type='02' из БД: {rest_type02:,.2f} AMD")
    print(f"\nПосле swap:")
    print(f"RestType01 (Type='02'): {swapped_rest_type01:,.2f} AMD")
    print(f"RestType02 (Type='01'): {swapped_rest_type02:,.2f} AMD")
    print(f"\n{'=' * 80}")
    print(f"ИТОГО ДОЛГ: {total_debt:,.2f} AMD")
    print(f"{'=' * 80}")
    
    # 7. Проверка: сколько клиентов у A003
    print("\n\n7. КЛИЕНТЫ МЕНЕДЖЕРА A003:")
    print("=" * 80)
    cursor.execute("""
        SELECT COUNT(DISTINCT fCUSTOMERID) as CustomerCount
        FROM SALES
        WHERE fSALESAGENTID = ?
    """, (manager_id,))
    
    customer_count = cursor.fetchone()
    print(f"Всего клиентов (когда-либо покупали): {customer_count.CustomerCount}")
    
    # 8. Проверка других таблиц с долгами
    print("\n\n8. ПОИСК ДРУГИХ ТАБЛИЦ С ДОЛГАМИ:")
    print("=" * 80)
    cursor.execute("""
        SELECT 
            t.name as TableName
        FROM sys.tables t
        WHERE t.name LIKE '%DEBT%' 
           OR t.name LIKE '%REST%'
           OR t.name LIKE '%BALANCE%'
           OR t.name LIKE '%ОСТАТОК%'
        ORDER BY t.name
    """)
    
    print("Таблицы с долгами/остатками:")
    for row in cursor.fetchall():
        print(f"  - {row.TableName}")
    
    conn.close()
    
    print("\n" + "=" * 80)
    print("ИССЛЕДОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 80)
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

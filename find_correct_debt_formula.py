"""
Анализ HIRESTCUSTOMERSDEBT и поиск правильной формулы долга
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
    
    manager_id = 9  # A003
    
    print("=" * 80)
    print("ПОИСК ПРАВИЛЬНОЙ ФОРМУЛЫ ДОЛГА")
    print("=" * 80)
    
    # 1. Структура HIRESTCUSTOMERSDEBT
    print("\n1. СТРУКТУРА HIRESTCUSTOMERSDEBT:")
    print("-" * 80)
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'HIRESTCUSTOMERSDEBT'
        ORDER BY ORDINAL_POSITION
    """)
    
    rest_debt_cols = []
    for row in cursor.fetchall():
        rest_debt_cols.append(row.COLUMN_NAME)
        print(f"  {row.COLUMN_NAME:<30} {row.DATA_TYPE}")
    
    # 2. Примеры данных из HIRESTCUSTOMERSDEBT
    print("\n2. ПРИМЕРЫ ДАННЫХ ИЗ HIRESTCUSTOMERSDEBT (TOP 10):")
    print("-" * 80)
    cursor.execute("SELECT TOP 10 * FROM HIRESTCUSTOMERSDEBT ORDER BY ABS(fSUM) DESC")
    
    for row in cursor.fetchall():
        print(f"  {dict(zip(rest_debt_cols, row))}")
    
    # 3. Связь HIRESTCUSTOMERSDEBT с DOCUMENTS
    print("\n3. ПОПЫТКА СВЯЗАТЬ HIRESTCUSTOMERSDEBT С DOCUMENTS:")
    print("-" * 80)
    cursor.execute("""
        SELECT TOP 5
            r.fDEBTDOCISN,
            r.fSUM as RestSum,
            doc.fCUSTOMERID,
            doc.fSALESAGENTID,
            c.fNAME as CustomerName
        FROM HIRESTCUSTOMERSDEBT r
        LEFT JOIN DOCUMENTS doc ON r.fDEBTDOCISN = doc.fISN
        LEFT JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fSALESAGENTID = ?
        ORDER BY ABS(r.fSUM) DESC
    """, (manager_id,))
    
    print(f"{'RestSum':<15} {'CustomerID':<12} {'CustomerName':<40}")
    print("-" * 80)
    for row in cursor.fetchall():
        customer_name = row.CustomerName if row.CustomerName else "N/A"
        print(f"{float(row.RestSum):<15,.2f} {row.fCUSTOMERID or 0:<12} {customer_name[:40]:<40}")
    
    # 4. Расчет долга ТОЛЬКО через DOCUMENTS.fSALESAGENTID
    print("\n" + "=" * 80)
    print("4. РАСЧЕТ ДОЛГА ЧЕРЕЗ DOCUMENTS.fSALESAGENTID")
    print("=" * 80)
    
    # Метод 1: Только HICUSTOMERSDEBT через SALESAGENTID
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fSALESAGENTID = ?
    """, (manager_id,))
    
    debt_via_agent = float(cursor.fetchone().NetDebt or 0)
    print(f"\nHICUSTOMERSDEBT (через SALESAGENTID): {debt_via_agent:,.2f} AMD")
    
    # Добавить HIRESTCUSTOMERSDEBT через SALESAGENTID
    cursor.execute("""
        SELECT 
            SUM(r.fSUM) as RestSum
        FROM HIRESTCUSTOMERSDEBT r
        INNER JOIN DOCUMENTS doc ON r.fDEBTDOCISN = doc.fISN
        WHERE doc.fSALESAGENTID = ?
    """, (manager_id,))
    
    rest_debt_via_agent = float(cursor.fetchone().RestSum or 0)
    print(f"HIRESTCUSTOMERSDEBT (через SALESAGENTID): {rest_debt_via_agent:,.2f} AMD")
    
    total_via_agent = debt_via_agent + rest_debt_via_agent
    print(f"ИТОГО (Метод 1): {total_via_agent:,.2f} AMD")
    
    # Метод 2: HICUSTOMERSDEBT через SALESAGENTID + HIRESTCUSTOMERSSUM
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN fTYPE = '01' THEN fSUM ELSE 0 END) as Type01,
            SUM(CASE WHEN fTYPE = '02' THEN fSUM ELSE 0 END) as Type02
        FROM HIRESTCUSTOMERSSUM
        WHERE fCUSTOMERID IN (
            SELECT DISTINCT doc.fCUSTOMERID
            FROM DOCUMENTS doc
            WHERE doc.fSALESAGENTID = ?
        )
    """, (manager_id,))
    
    row = cursor.fetchone()
    type01 = float(row.Type01 or 0)
    type02 = float(row.Type02 or 0)
    
    print(f"\nHIRESTCUSTOMERSSUM Type='01': {type01:,.2f} AMD")
    print(f"HIRESTCUSTOMERSSUM Type='02': {type02:,.2f} AMD")
    
    # Вариант без swap
    total_method2_no_swap = debt_via_agent + type01 + type02
    print(f"\nМетод 2 (без swap): {debt_via_agent:,.2f} + {type01:,.2f} + {type02:,.2f}")
    print(f"ИТОГО: {total_method2_no_swap:,.2f} AMD")
    
    # Вариант со swap
    total_method2_swap = debt_via_agent + type02 + type01  # поменяли местами
    print(f"\nМетод 2 (со swap): {debt_via_agent:,.2f} + {type02:,.2f} + {type01:,.2f}")
    print(f"ИТОГО: {total_method2_swap:,.2f} AMD")
    
    # Метод 3: HICUSTOMERSDEBT через клиентов SALES + HIRESTCUSTOMERSSUM
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
    """, (manager_id,))
    
    debt_via_sales = float(cursor.fetchone().NetDebt or 0)
    
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN fTYPE = '01' THEN fSUM ELSE 0 END) as Type01,
            SUM(CASE WHEN fTYPE = '02' THEN fSUM ELSE 0 END) as Type02
        FROM HIRESTCUSTOMERSSUM
        WHERE fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
    """, (manager_id,))
    
    row = cursor.fetchone()
    type01_sales = float(row.Type01 or 0)
    type02_sales = float(row.Type02 or 0)
    
    total_method3_swap = debt_via_sales + type02_sales + type01_sales
    print(f"\nМетод 3 (через SALES, со swap): {debt_via_sales:,.2f} + {type02_sales:,.2f} + {type01_sales:,.2f}")
    print(f"ИТОГО: {total_method3_swap:,.2f} AMD")
    
    # Сравнение с ожидаемым
    expected = 5289036.77
    print("\n" + "=" * 80)
    print("СРАВНЕНИЕ С ОЖИДАЕМЫМ ДОЛГОМ")
    print("=" * 80)
    print(f"\nОжидаемый долг: {expected:,.2f} AMD")
    print(f"\nРазличия:")
    print(f"  Метод 1 (DOCUMENTS.SALESAGENTID): {total_via_agent:,.2f} AMD (разница: {abs(total_via_agent - expected):,.2f})")
    print(f"  Метод 2 (DOCUMENTS.SALESAGENTID + HIRESTCUSTOMERSSUM без swap): {total_method2_no_swap:,.2f} AMD (разница: {abs(total_method2_no_swap - expected):,.2f})")
    print(f"  Метод 2 (DOCUMENTS.SALESAGENTID + HIRESTCUSTOMERSSUM со swap): {total_method2_swap:,.2f} AMD (разница: {abs(total_method2_swap - expected):,.2f})")
    print(f"  Метод 3 (SALES customers со swap): {total_method3_swap:,.2f} AMD (разница: {abs(total_method3_swap - expected):,.2f})")
    
    # Найти лучший метод
    methods = [
        ("Метод 1 (DOCUMENTS.SALESAGENTID)", total_via_agent),
        ("Метод 2 (без swap)", total_method2_no_swap),
        ("Метод 2 (со swap)", total_method2_swap),
        ("Метод 3 (SALES customers)", total_method3_swap)
    ]
    
    best_method = min(methods, key=lambda x: abs(x[1] - expected))
    print(f"\n✅ БЛИЖАЙШИЙ К ОЖИДАЕМОМУ: {best_method[0]}")
    print(f"   Значение: {best_method[1]:,.2f} AMD")
    print(f"   Разница: {abs(best_method[1] - expected):,.2f} AMD")
    
    conn.close()
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

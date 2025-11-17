"""
Полная проверка всех таблиц долгов для менеджера A003 (ID=9)
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
    print("ПОЛНЫЙ АНАЛИЗ ДОЛГОВ ДЛЯ МЕНЕДЖЕРА A003 (ID=9)")
    print("=" * 80)
    
    # Получить клиентов менеджера
    cursor.execute("""
        SELECT COUNT(DISTINCT fCUSTOMERID) as CustomerCount
        FROM SALES
        WHERE fSALESAGENTID = ?
    """, (manager_id,))
    customer_count = cursor.fetchone().CustomerCount
    print(f"\nВсего клиентов менеджера: {customer_count}")
    
    print("\n" + "=" * 80)
    print("1. HICUSTOMERSDEBT (Долги из документов)")
    print("=" * 80)
    
    cursor.execute("""
        SELECT 
            COUNT(*) as RecordCount,
            SUM(CASE WHEN fDBCR = 'D' THEN fSUM ELSE 0 END) as DebitSum,
            SUM(CASE WHEN fDBCR = 'C' THEN fSUM ELSE 0 END) as CreditSum,
            SUM(CASE WHEN fDBCR = 'D' THEN fSUM ELSE -fSUM END) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
    """, (manager_id,))
    
    row = cursor.fetchone()
    debt1_debit = float(row.DebitSum or 0)
    debt1_credit = float(row.CreditSum or 0)
    debt1_net = float(row.NetDebt or 0)
    
    print(f"Записей: {row.RecordCount}")
    print(f"Дебет (D): {debt1_debit:,.2f} AMD")
    print(f"Кредит (C): {debt1_credit:,.2f} AMD")
    print(f"ЧИСТЫЙ ДОЛГ: {debt1_net:,.2f} AMD")
    
    print("\n" + "=" * 80)
    print("2. HIRESTCUSTOMERSDEBT (Остатки долгов)")
    print("=" * 80)
    
    # Проверить структуру HIRESTCUSTOMERSDEBT
    cursor.execute("""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'HIRESTCUSTOMERSDEBT'
    """)
    
    rest_debt_columns = [row.COLUMN_NAME for row in cursor.fetchall()]
    print(f"Колонки: {', '.join(rest_debt_columns)}")
    
    # Получить данные из HIRESTCUSTOMERSDEBT
    cursor.execute("""
        SELECT TOP 5 *
        FROM HIRESTCUSTOMERSDEBT
        WHERE fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
    """, (manager_id,))
    
    print("\nПримеры данных (TOP 5):")
    for row in cursor.fetchall():
        print(f"  {dict(zip(rest_debt_columns, row))}")
    
    print("\n" + "=" * 80)
    print("3. HICUSTOMERSSUM (Суммы по клиентам)")
    print("=" * 80)
    
    # Проверить структуру HICUSTOMERSSUM
    cursor.execute("""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'HICUSTOMERSSUM'
    """)
    
    sum_columns = [row.COLUMN_NAME for row in cursor.fetchall()]
    print(f"Колонки: {', '.join(sum_columns)}")
    
    # Получить данные из HICUSTOMERSSUM
    cursor.execute("""
        SELECT TOP 10 *
        FROM HICUSTOMERSSUM
        WHERE fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        ORDER BY ABS(fSUM) DESC
    """, (manager_id,))
    
    print("\nТОП 10 записей по сумме:")
    for row in cursor.fetchall():
        print(f"  {dict(zip(sum_columns, row))}")
    
    print("\n" + "=" * 80)
    print("4. HIRESTCUSTOMERSSUM (Остатки по типам)")
    print("=" * 80)
    
    # Type 01
    cursor.execute("""
        SELECT 
            COUNT(*) as RecordCount,
            SUM(fSUM) as TotalSum
        FROM HIRESTCUSTOMERSSUM
        WHERE fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        AND fTYPE = '01'
    """, (manager_id,))
    
    type01 = cursor.fetchone()
    type01_sum = float(type01.TotalSum or 0)
    print(f"\nType='01':")
    print(f"  Записей: {type01.RecordCount}")
    print(f"  Сумма: {type01_sum:,.2f} AMD")
    
    # Type 02
    cursor.execute("""
        SELECT 
            COUNT(*) as RecordCount,
            SUM(fSUM) as TotalSum
        FROM HIRESTCUSTOMERSSUM
        WHERE fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        AND fTYPE = '02'
    """, (manager_id,))
    
    type02 = cursor.fetchone()
    type02_sum = float(type02.TotalSum or 0)
    print(f"\nType='02':")
    print(f"  Записей: {type02.RecordCount}")
    print(f"  Сумма: {type02_sum:,.2f} AMD")
    
    # Другие типы?
    cursor.execute("""
        SELECT 
            fTYPE,
            COUNT(*) as RecordCount,
            SUM(fSUM) as TotalSum
        FROM HIRESTCUSTOMERSSUM
        WHERE fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        GROUP BY fTYPE
        ORDER BY fTYPE
    """, (manager_id,))
    
    print(f"\nВсе типы в HIRESTCUSTOMERSSUM:")
    for row in cursor.fetchall():
        print(f"  Type='{row.fTYPE}': {row.RecordCount} записей, сумма {float(row.TotalSum):,.2f} AMD")
    
    print("\n" + "=" * 80)
    print("5. ИТОГОВЫЕ РАСЧЕТЫ")
    print("=" * 80)
    
    # Вариант 1: Только HICUSTOMERSDEBT
    total1 = debt1_net
    print(f"\nВариант 1 (только HICUSTOMERSDEBT):")
    print(f"  ДОЛГ = {total1:,.2f} AMD")
    
    # Вариант 2: HICUSTOMERSDEBT + HIRESTCUSTOMERSSUM (без swap)
    total2 = debt1_net + type01_sum + type02_sum
    print(f"\nВариант 2 (HICUSTOMERSDEBT + HIRESTCUSTOMERSSUM без swap):")
    print(f"  ДОЛГ = {debt1_net:,.2f} + {type01_sum:,.2f} + {type02_sum:,.2f}")
    print(f"  ДОЛГ = {total2:,.2f} AMD")
    
    # Вариант 3: HICUSTOMERSDEBT + HIRESTCUSTOMERSSUM (со swap)
    swapped_type01 = type02_sum  # Type='02' -> RestType01
    swapped_type02 = type01_sum  # Type='01' -> RestType02
    total3 = debt1_net + swapped_type01 + swapped_type02
    print(f"\nВариант 3 (HICUSTOMERSDEBT + HIRESTCUSTOMERSSUM со swap):")
    print(f"  ДОЛГ = {debt1_net:,.2f} + {swapped_type01:,.2f} + {swapped_type02:,.2f}")
    print(f"  ДОЛГ = {total3:,.2f} AMD")
    
    # Проверка ожидаемого долга
    expected_debt = 5289036.77
    print(f"\nОжидаемый долг: {expected_debt:,.2f} AMD")
    print(f"\nСравнение:")
    print(f"  Вариант 1: {total1:,.2f} AMD (разница: {abs(total1 - expected_debt):,.2f})")
    print(f"  Вариант 2: {total2:,.2f} AMD (разница: {abs(total2 - expected_debt):,.2f})")
    print(f"  Вариант 3: {total3:,.2f} AMD (разница: {abs(total3 - expected_debt):,.2f})")
    
    # Найти ближайший вариант
    diff1 = abs(total1 - expected_debt)
    diff2 = abs(total2 - expected_debt)
    diff3 = abs(total3 - expected_debt)
    
    min_diff = min(diff1, diff2, diff3)
    if min_diff == diff1:
        print(f"\n✅ Ближайший к ожидаемому: Вариант 1 (только HICUSTOMERSDEBT)")
    elif min_diff == diff2:
        print(f"\n✅ Ближайший к ожидаемому: Вариант 2 (без swap)")
    else:
        print(f"\n✅ Ближайший к ожидаемому: Вариант 3 (со swap)")
    
    conn.close()
    
    print("\n" + "=" * 80)
    print("АНАЛИЗ ЗАВЕРШЕН")
    print("=" * 80)
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

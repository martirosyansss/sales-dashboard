"""
Проверка гипотезы: долг в HICUSTOMERSDEBT записан дважды?
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
    print("АНАЛИЗ ДУБЛИРОВАНИЯ В HICUSTOMERSDEBT")
    print("=" * 80)
    
    # Взять один документ и посмотреть, сколько раз он записан
    cursor.execute("""
        SELECT TOP 1 fDEBTDOCISN
        FROM HICUSTOMERSDEBT
        WHERE fDBCR = 'D' AND fSUM > 100000
    """)
    
    test_doc = cursor.fetchone().fDEBTDOCISN
    print(f"\nТестовый документ: {test_doc}")
    
    # Посмотреть все записи для этого документа
    cursor.execute("""
        SELECT 
            fDATE,
            fDEBTDOCISN,
            fSUM,
            fOP,
            fDBCR,
            fBASE,
            fUSERID
        FROM HICUSTOMERSDEBT
        WHERE fDEBTDOCISN = ?
        ORDER BY fDATE, fDBCR
    """, (test_doc,))
    
    print("\nВсе записи для этого документа:")
    print("-" * 80)
    print(f"{'Дата':<12} {'D/C':<5} {'Операция':<6} {'Сумма':<15} {'BASE':<40}")
    print("-" * 80)
    
    for row in cursor.fetchall():
        print(f"{str(row.fDATE)[:10]:<12} {row.fDBCR:<5} {row.fOP:<6} {float(row.fSUM):<15,.2f} {str(row.fBASE)[:40]:<40}")
    
    # Статистика: сколько раз каждый документ записан
    print("\n" + "=" * 80)
    print("СТАТИСТИКА: Сколько записей на документ")
    print("=" * 80)
    
    cursor.execute("""
        SELECT 
            COUNT(*) as RecordCount,
            COUNT(DISTINCT fDEBTDOCISN) as UniqueDocCount,
            AVG(RecordsPerDoc) as AvgRecordsPerDoc
        FROM (
            SELECT 
                fDEBTDOCISN,
                COUNT(*) as RecordsPerDoc
            FROM HICUSTOMERSDEBT
            GROUP BY fDEBTDOCISN
        ) sub
    """)
    
    row = cursor.fetchone()
    print(f"\nВсего записей: {row.RecordCount:,}")
    print(f"Уникальных документов: {row.UniqueDocCount:,}")
    print(f"Среднее записей на документ: {float(row.AvgRecordsPerDoc):.2f}")
    
    # Распределение количества записей
    cursor.execute("""
        SELECT 
            RecordsPerDoc,
            COUNT(*) as DocsCount
        FROM (
            SELECT 
                fDEBTDOCISN,
                COUNT(*) as RecordsPerDoc
            FROM HICUSTOMERSDEBT
            GROUP BY fDEBTDOCISN
        ) sub
        GROUP BY RecordsPerDoc
        ORDER BY RecordsPerDoc
    """)
    
    print("\nРаспределение:")
    print(f"{'Записей на документ':<25} {'Документов':<15}")
    print("-" * 40)
    for row in cursor.fetchall():
        print(f"{row.RecordsPerDoc:<25} {row.DocsCount:<15}")
    
    # Проверка: расчет чистого долга
    print("\n" + "=" * 80)
    print("РАСЧЕТ ЧИСТОГО ДОЛГА")
    print("=" * 80)
    
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN fDBCR = 'D' THEN fSUM ELSE -fSUM END) as NetDebt,
            COUNT(*) as RecordCount
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = 9
        )
    """)
    
    row = cursor.fetchone()
    total_debt = float(row.NetDebt or 0)
    record_count = row.RecordCount
    
    print(f"\nМенеджер A003 (ID=9):")
    print(f"Всего записей: {record_count:,}")
    print(f"Чистый долг: {total_debt:,.2f} AMD")
    print(f"Ожидаемый долг: 5,289,036.77 AMD")
    print(f"Разница: {abs(total_debt - 5289036.77):,.2f} AMD ({abs(total_debt - 5289036.77) / 5289036.77 * 100:.2f}%)")
    
    # Проверка: может быть нужно фильтровать по fOP?
    print("\n" + "=" * 80)
    print("АНАЛИЗ ПОЛЯ fOP (операция)")
    print("=" * 80)
    
    cursor.execute("""
        SELECT 
            fOP,
            fDBCR,
            COUNT(*) as RecordCount,
            SUM(fSUM) as TotalSum
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = 9
        )
        GROUP BY fOP, fDBCR
        ORDER BY fOP, fDBCR
    """)
    
    print(f"\n{'Операция':<10} {'D/C':<5} {'Записей':<12} {'Сумма':<20}")
    print("-" * 50)
    for row in cursor.fetchall():
        print(f"{row.fOP:<10} {row.fDBCR:<5} {row.RecordCount:<12} {float(row.TotalSum):>20,.2f}")
    
    # Попытка: считать только RLZ (реализация)
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN fDBCR = 'D' THEN fSUM ELSE -fSUM END) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = 9
        )
        AND fOP = 'RLZ'
    """)
    
    rlz_debt = float(cursor.fetchone().NetDebt or 0)
    print(f"\nДолг только по RLZ: {rlz_debt:,.2f} AMD")
    print(f"Разница от ожидаемого: {abs(rlz_debt - 5289036.77):,.2f} AMD ({abs(rlz_debt - 5289036.77) / 5289036.77 * 100:.2f}%)")
    
    conn.close()
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

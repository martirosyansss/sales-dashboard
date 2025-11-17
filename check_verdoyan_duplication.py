"""
Проверка дублирования долга для менеджера 3169 (Վերդոյան Նորայր)
"""
import pyodbc

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

manager_id = 3169  # Վերդոյան Նորայր

print("=" * 80)
print(f"ПРОВЕРКА ДУБЛИРОВАНИЯ ДОЛГА ДЛЯ МЕНЕДЖЕРА ID={manager_id}")
print("=" * 80)

# Получить клиентов менеджера из групп 036 и 002
query_customers = """
    SELECT DISTINCT c.fID, c.fNAME, c.fGROUP
    FROM CUSTOMERS c
    INNER JOIN SALES s ON s.fCUSTOMERID = c.fID
    WHERE s.fSALESAGENTID = ?
    AND c.fGROUP IN ('036', '002')
    ORDER BY c.fGROUP, c.fNAME
"""

cursor.execute(query_customers, (manager_id,))
customers = cursor.fetchall()

print(f"\nКлиентов в группах 036, 002: {len(customers)}")

# Проверим дублирование на примере первых 5 клиентов с долгами
query_debt_detail = """
    SELECT TOP 5
        doc.fCUSTOMERID,
        c.fNAME as CustomerName,
        c.fGROUP,
        d.fDEBTDOCISN,
        COUNT(*) as RecordCount,
        SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as NetDebtPerDoc
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ('036', '002')
    GROUP BY doc.fCUSTOMERID, c.fNAME, c.fGROUP, d.fDEBTDOCISN
    HAVING SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) > 0
    ORDER BY NetDebtPerDoc DESC
"""

cursor.execute(query_debt_detail, (manager_id,))

print(f"\n{'Клиент':<30} {'Группа':<8} {'Документ':<12} {'Записей':<10} {'Долг':<15}")
print("-" * 80)

for row in cursor.fetchall():
    print(f"{row.CustomerName[:28]:<30} {row.fGROUP:<8} {row.fDEBTDOCISN:<12} {row.RecordCount:<10} {row.NetDebtPerDoc:>13,.2f}")

# Статистика по количеству записей на документ
query_stats = """
    SELECT 
        COUNT(DISTINCT d.fDEBTDOCISN) as TotalDocs,
        AVG(CAST(RecordCount as FLOAT)) as AvgRecordsPerDoc
    FROM (
        SELECT 
            d.fDEBTDOCISN,
            COUNT(*) as RecordCount
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        AND c.fGROUP IN ('036', '002')
        GROUP BY d.fDEBTDOCISN
    ) subquery
"""

cursor.execute(query_stats, (manager_id,))
stats = cursor.fetchone()

print("-" * 80)
print(f"\nСТАТИСТИКА:")
print(f"Всего документов: {stats.TotalDocs}")
print(f"Среднее записей на документ: {stats.AvgRecordsPerDoc:.2f}")

if stats.AvgRecordsPerDoc >= 1.9:
    print(f"\n\u2705 Долг ДУБЛИРУЕТСЯ (среднее ~2 записи на документ)")
    print(f"   Ожидаемый долг: 4,450,458.49 AMD")
else:
    print(f"\n\u26a0\ufe0f Долг НЕ дублируется (среднее < 2 записей на документ)")
    print(f"   Ожидаемый долг: 4,450,458.49 AMD")

print(f"\n{'='*80}")
print("ВЫВОД:")
print(f"{'='*80}")

target = 5_289_036.77
without_division = 4_450_458.49

diff_without = abs(without_division - target)

print(f"\nОжидаемый долг: {target:,.2f} AMD")
print(f"\nТекущий долг: {without_division:,.2f} AMD")
print(f"  Отклонение: {diff_without:,.2f} AMD ({diff_without/target*100:.1f}%)")

conn.close()

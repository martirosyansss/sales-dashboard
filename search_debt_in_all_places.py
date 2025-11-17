"""
Поиск долга 5,289,036.77 AMD для менеджера 3169 (Վերդոյան Նորայր) в разных местах БД
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

manager_id = 3169
target_debt = 5_289_036.77
groups = ['036', '002']

print("=" * 80)
print(f"ПОИСК ДОЛГА ДЛЯ МЕНЕДЖЕРА ID={manager_id} В РАЗНЫХ МЕСТАХ БД")
print("=" * 80)
print(f"Целевой долг: {target_debt:,.2f} AMD")
print(f"Группы: {', '.join(groups)}")

results = []

# 1. HICUSTOMERSDEBT - текущий метод
print(f"\n{'='*80}")
print("1. HICUSTOMERSDEBT (текущий метод)")
print(f"{'='*80}")

placeholders = ','.join(['?'] * len(groups))
query1 = f"""
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as NetDebt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
"""
cursor.execute(query1, (manager_id,) + tuple(groups))
debt1 = float(cursor.fetchone().NetDebt)
print(f"Чистый долг: {debt1:,.2f} AMD")
print(f"Отклонение: {abs(debt1-target_debt)/target_debt*100:.1f}%")
results.append(("HICUSTOMERSDEBT", debt1))

# 2. HIRESTCUSTOMERSDEBT - остатки долга
print(f"\n{'='*80}")
print("2. HIRESTCUSTOMERSDEBT (остатки долга)")
print(f"{'='*80}")

query2 = f"""
    SELECT 
        ISNULL(SUM(r.fSUM), 0) as RestDebt
    FROM HIRESTCUSTOMERSDEBT r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
"""
cursor.execute(query2, (manager_id,) + tuple(groups))
debt2 = float(cursor.fetchone().RestDebt)
print(f"Остаток долга: {debt2:,.2f} AMD - отклонение {abs(debt2-target_debt)/target_debt*100:.1f}%")
results.append(("HIRESTCUSTOMERSDEBT", debt2))

# 3. HIRESTCUSTOMERSSUM Type='01' и Type='02'
print(f"\n{'='*80}")
print("3. HIRESTCUSTOMERSSUM Type='01' (Type01)")
print(f"{'='*80}")

query3 = f"""
    SELECT 
        ISNULL(SUM(r.fSUM), 0) as Type01
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
    AND r.fTYPE = '01'
"""
cursor.execute(query3, (manager_id,) + tuple(groups))
type01 = float(cursor.fetchone().Type01)
print(f"Type01: {type01:,.2f} AMD")

query4 = f"""
    SELECT 
        ISNULL(SUM(r.fSUM), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
    AND r.fTYPE = '02'
"""
cursor.execute(query4, (manager_id,) + tuple(groups))
type02 = float(cursor.fetchone().Type02)
print(f"Type02: {type02:,.2f} AMD")

debt3 = debt1 - abs(type01) - abs(type02)
print(f"\nДолг - |Type01| - |Type02|: {debt3:,.2f} AMD")
print(f"Отклонение: {abs(debt3-target_debt)/target_debt*100:.1f}%")
results.append(("Долг - Type01 - Type02", debt3))

# 4. БЕЗ фильтра по группам
print(f"\n{'='*80}")
print("4. HICUSTOMERSDEBT БЕЗ ФИЛЬТРА ПО ГРУППАМ (все клиенты)")
print(f"{'='*80}")

query5 = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as NetDebt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
    )
"""
cursor.execute(query5, (manager_id,))
debt5 = float(cursor.fetchone().NetDebt)
print(f"Чистый долг (все клиенты): {debt5:,.2f} AMD")
print(f"Отклонение: {abs(debt5-target_debt)/target_debt*100:.1f}%")
results.append(("Все клиенты", debt5))

# 5. Проверка через SALES (возможно долг записан в таблице SALES?)
print(f"\n{'='*80}")
print("5. ПРОВЕРКА В ТАБЛИЦЕ SALES")
print(f"{'='*80}")

cursor.execute("""
    SELECT TOP 1 COLUMN_NAME 
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'SALES' 
    AND COLUMN_NAME LIKE '%DEBT%'
""")
debt_column = cursor.fetchone()
if debt_column:
    print(f"Найдена колонка: {debt_column.COLUMN_NAME}")
    query6 = f"""
        SELECT ISNULL(SUM(s.{debt_column.COLUMN_NAME}), 0) as SalesDebt
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        WHERE s.fSALESAGENTID = ?
        AND c.fGROUP IN ({placeholders})
    """
    cursor.execute(query6, (manager_id,) + tuple(groups))
    debt6 = float(cursor.fetchone().SalesDebt)
    print(f"Долг из SALES: {debt6:,.2f} AMD - отклонение {abs(debt6-target_debt)/target_debt*100:.1f}%")
    results.append((f"SALES.{debt_column.COLUMN_NAME}", debt6, debt6))
else:
    print("Колонок с долгом в SALES не найдено")

# 6. Проверка в DOCUMENTS (возможно есть поле с долгом)
print(f"\n{'='*80}")
print("6. ПРОВЕРКА В ТАБЛИЦЕ DOCUMENTS")
print(f"{'='*80}")

cursor.execute("""
    SELECT COLUMN_NAME 
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME = 'DOCUMENTS' 
    AND (COLUMN_NAME LIKE '%DEBT%' OR COLUMN_NAME LIKE '%REST%' OR COLUMN_NAME LIKE '%SUMM%')
""")
doc_columns = [row.COLUMN_NAME for row in cursor.fetchall()]
if doc_columns:
    print(f"Найдены колонки: {', '.join(doc_columns[:5])}")
else:
    print("Колонок с долгом в DOCUMENTS не найдено")

# ИТОГОВАЯ ТАБЛИЦА
print(f"\n{'='*80}")
print("ИТОГОВАЯ ТАБЛИЦА РЕЗУЛЬТАТОВ")
print(f"{'='*80}")
print(f"Целевой долг: {target_debt:,.2f} AMD")
print(f"\n{'Источник':<35} {'Долг':<20} {'Отклонение (%)':<20}")
print("-" * 90)

best_match = None
best_diff = float('inf')

for source, value in results:
    diff = abs(value - target_debt)
    percent = (diff / target_debt * 100)
    
    if diff < best_diff:
        best_diff = diff
        best_match = (source, value)
    
    print(f"{source:<35} {value:>18,.2f} {percent:>18.1f}%")

print("-" * 90)

if best_match:
    source, value = best_match
    print(f"\n✅ ЛУЧШИЙ РЕЗУЛЬТАТ: {source}")
    print(f"   Значение: {value:,.2f} AMD")
    print(f"   Отклонение: {best_diff:,.2f} AMD ({best_diff/target_debt*100:.2f}%)")

conn.close()

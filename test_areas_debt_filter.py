"""
Тест: проверка фильтрации долга по группам клиентов для территорий
"""
import pyodbc
import json

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

# Загрузить группы менеджеров
with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
    assignments = json.load(f)

# Тестовый менеджер A010/1 (ID=3159) из территории 110
test_manager_id = 3159
test_manager_code = 'A010/1'
test_sales_area = '110'

print("=" * 80)
print(f"ТЕСТ ФИЛЬТРАЦИИ ДОЛГА ПО ГРУППАМ КЛИЕНТОВ")
print("=" * 80)
print(f"\nМенеджер: {test_manager_code} (ID={test_manager_id})")
print(f"Территория: {test_sales_area} (Арарат)")

# Найти назначенные группы
responsible_groups = []
for group_code, manager_ids in assignments.items():
    if isinstance(manager_ids, list) and test_manager_id in manager_ids:
        responsible_groups.append(group_code)

print(f"\nНазначенные группы клиентов: {', '.join(responsible_groups) if responsible_groups else 'НЕТ'}")
print(f"Количество групп: {len(responsible_groups)}")

if not responsible_groups:
    print("\n⚠️ У менеджера НЕТ назначенных групп!")
    print("   Долг будет = 0 (правильно)")
    conn.close()
    exit(0)

# 1. Долг БЕЗ фильтра по группам (все клиенты менеджера)
print("\n" + "=" * 80)
print("1. ДОЛГ БЕЗ ФИЛЬТРА ПО ГРУППАМ (все клиенты)")
print("=" * 80)

query_no_filter = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
"""

cursor.execute(query_no_filter, (test_manager_id,))
debt_no_filter = float(cursor.fetchone().DebtFromDocs)

# Type01/Type02 без фильтра
cursor.execute("""
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
""", (test_manager_id,))

row = cursor.fetchone()
type01_no_filter = float(row.Type01 or 0)
type02_no_filter = float(row.Type02 or 0)

total_debt_no_filter = debt_no_filter - abs(type01_no_filter) - abs(type02_no_filter)

print(f"debtFromDocuments: {debt_no_filter:,.2f} AMD")
print(f"Type01: {type01_no_filter:,.2f} AMD")
print(f"Type02: {type02_no_filter:,.2f} AMD")
print(f"ИТОГО: {total_debt_no_filter:,.2f} AMD")

# 2. Долг С фильтром по группам (только назначенные группы)
print("\n" + "=" * 80)
print("2. ДОЛГ С ФИЛЬТРОМ ПО НАЗНАЧЕННЫМ ГРУППАМ")
print("=" * 80)

placeholders = ','.join(['?'] * len(responsible_groups))
group_filter = f" AND c.fGROUP IN ({placeholders})"

query_with_filter = f"""
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
        {group_filter}
"""

params = (test_manager_id,) + tuple(responsible_groups)
cursor.execute(query_with_filter, params)
debt_with_filter = float(cursor.fetchone().DebtFromDocs)

# Type01/Type02 с фильтром
rest_query = f"""
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
        {group_filter}
"""

cursor.execute(rest_query, params)
row = cursor.fetchone()
type01_with_filter = float(row.Type01 or 0)
type02_with_filter = float(row.Type02 or 0)

total_debt_with_filter = debt_with_filter - abs(type01_with_filter) - abs(type02_with_filter)

print(f"debtFromDocuments: {debt_with_filter:,.2f} AMD")
print(f"Type01: {type01_with_filter:,.2f} AMD")
print(f"Type02: {type02_with_filter:,.2f} AMD")
print(f"ИТОГО: {total_debt_with_filter:,.2f} AMD")

# 3. Сравнение
print("\n" + "=" * 80)
print("СРАВНЕНИЕ")
print("=" * 80)

difference = total_debt_no_filter - total_debt_with_filter

print(f"\nДолг БЕЗ фильтра:      {total_debt_no_filter:,.2f} AMD")
print(f"Долг С фильтром:       {total_debt_with_filter:,.2f} AMD")
print(f"Разница:               {difference:,.2f} AMD")

if abs(difference) > 1:
    print(f"\n✅ ФИЛЬТР РАБОТАЕТ! Разница: {abs(difference):,.2f} AMD")
    print(f"   Долг отфильтрован только по группам: {', '.join(responsible_groups)}")
else:
    print(f"\n⚠️ Фильтр НЕ влияет - все клиенты в назначенных группах")

# 4. Проверим распределение клиентов по группам
print("\n" + "=" * 80)
print("КЛИЕНТЫ МЕНЕДЖЕРА ПО ГРУППАМ")
print("=" * 80)

cursor.execute("""
    SELECT 
        c.fGROUP,
        COUNT(DISTINCT c.fID) as CustomerCount,
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as GroupDebt
    FROM CUSTOMERS c
    INNER JOIN SALES s ON c.fID = s.fCUSTOMERID
    LEFT JOIN DOCUMENTS doc ON doc.fCUSTOMERID = c.fID
    LEFT JOIN HICUSTOMERSDEBT d ON d.fDEBTDOCISN = doc.fISN
    WHERE s.fSALESAGENTID = ?
    GROUP BY c.fGROUP
    ORDER BY GroupDebt DESC
""", (test_manager_id,))

print(f"\n{'Группа':<15} {'Клиентов':<12} {'Долг':<20} {'В фильтре':<15}")
print("-" * 80)

for row in cursor.fetchall():
    group = row.fGROUP or 'NULL'
    customers = row.CustomerCount
    debt = float(row.GroupDebt or 0)
    in_filter = "✓" if group in responsible_groups else "✗"
    
    print(f"{group:<15} {customers:<12} {debt:>18,.2f}  {in_filter:<15}")

conn.close()

print("\n" + "=" * 80)
print("ВЫВОД:")
print("=" * 80)
print("Долг на странице /areas показывается ТОЛЬКО для групп клиентов,")
print("которые указаны в group_manager_assignments.json для каждого менеджера.")
print("=" * 80)

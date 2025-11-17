"""
Проверка долга для менеджера A003/9 (Վերդոյան Նորայր, ID=3136)
"""
import pyodbc
import json

# Подключение к БД
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

manager_id = 3169  # A003/9 (Վերդոյան Նորայր)

print("=" * 80)
print(f"ПРОВЕРКА ДОЛГА ДЛЯ МЕНЕДЖЕРА ID={manager_id}")
print("=" * 80)

# Получить информацию о менеджере
cursor.execute("SELECT fID, fCODE, fNAME FROM SALESAGENTS WHERE fID = ?", (manager_id,))
manager = cursor.fetchone()
print(f"\nМенеджер: {manager.fNAME} ({manager.fCODE})")
print(f"ID: {manager.fID}")

# Загрузить назначенные группы из JSON
try:
    with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
        assignments = json.load(f)
    
    # Найти группы для этого менеджера
    responsible_groups = []
    for group, manager_ids in assignments.items():
        if isinstance(manager_ids, list):
            if manager_id in manager_ids:
                responsible_groups.append(group)
        elif manager_ids == manager_id:
            responsible_groups.append(group)
    
    print(f"Назначено групп: {len(responsible_groups)}")
    if responsible_groups:
        print(f"Группы: {', '.join(responsible_groups)}")
except:
    responsible_groups = []
    print("Назначенных групп нет")

# Получить клиентов менеджера
cursor.execute("""
    SELECT DISTINCT fCUSTOMERID
    FROM SALES
    WHERE fSALESAGENTID = ?
""", (manager_id,))
customer_ids = [row.fCUSTOMERID for row in cursor.fetchall()]
print(f"\nВсего клиентов у менеджера (когда-либо): {len(customer_ids)}")

if not responsible_groups:
    print("\n⚠️ У менеджера НЕТ назначенных групп в settings!")
    print("По текущей логике его долг будет = 0")
else:
    # Расчет долга С фильтром по группам
    placeholders = ','.join(['?'] * len(responsible_groups))
    
    query_with_groups = f"""
        SELECT 
            COUNT(DISTINCT doc.fCUSTOMERID) as CustomerCount,
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        AND c.fGROUP IN ({placeholders})
    """
    
    params = (manager_id,) + tuple(responsible_groups)
    cursor.execute(query_with_groups, params)
    result = cursor.fetchone()
    
    net_debt = float(result.NetDebt) if result.NetDebt else 0
    customer_count = result.CustomerCount if result.CustomerCount else 0
    
    print(f"\nКлиентов с долгами (с фильтром по группам): {customer_count}")
    print(f"Чистый долг (Дебет - Кредит): {net_debt:,.2f} AMD")
    
    print(f"\n{'='*80}")
    print(f"✅ ИТОГОВЫЙ ДОЛГ: {net_debt:,.2f} AMD")
    print(f"{'='*80}")

# Сравнение с ожидаемым
expected = 5_289_036.77
if responsible_groups:
    actual = net_debt
    diff = abs(actual - expected)
    percent_diff = (diff / expected * 100) if expected > 0 else 0
    
    print(f"\nОжидаемый долг: {expected:,.2f} AMD")
    print(f"Фактический долг: {actual:,.2f} AMD")
    print(f"Разница: {diff:,.2f} AMD ({percent_diff:.2f}%)")

conn.close()

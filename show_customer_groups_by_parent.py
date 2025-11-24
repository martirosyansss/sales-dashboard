"""
Показать группы клиентов, сгруппированные по родительской группе
"""
import pyodbc

# Подключение к базе
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

print("\n" + "="*80)
print("ГРУППЫ КЛИЕНТОВ ПО РОДИТЕЛЯМ (Customer Groups by Parent)")
print("="*80 + "\n")

# 1. Получить все группы из TREES
query_trees = """
    SELECT fCODE, fCAPTION, fPARENT
    FROM TREES
    WHERE fTREEID = 'CustGrp'
    ORDER BY fPARENT, fCODE
"""
cursor.execute(query_trees)
all_groups = cursor.fetchall()

# 2. Получить количество клиентов в каждой группе
query_counts = """
    SELECT fGROUP, COUNT(*) as CustomerCount
    FROM CUSTOMERS
    WHERE fGROUP IS NOT NULL AND fGROUP != ''
    GROUP BY fGROUP
"""
cursor.execute(query_counts)
counts = {row.fGROUP: row.CustomerCount for row in cursor.fetchall()}

# Организовать по родителям
parents = {}
children = {}

for row in all_groups:
    code = row.fCODE
    name = row.fCAPTION
    parent = row.fPARENT
    
    # Если у группы нет родителя или родитель пустой, она сама может быть родителем (или корнем)
    if not parent:
        parents[code] = name
    else:
        if parent not in children:
            children[parent] = []
        children[parent].append({
            'code': code,
            'name': name,
            'count': counts.get(code, 0)
        })

# Вывести результат
for parent_code, parent_name in parents.items():
    print(f"\nРОДИТЕЛЬСКАЯ ГРУППА: {parent_code} - {parent_name}")
    print("-" * 80)
    
    if parent_code in children:
        kids = children[parent_code]
        # Сортировка по коду
        kids.sort(key=lambda x: x['code'])
        
        total_customers = sum(k['count'] for k in kids)
        
        for kid in kids:
            print(f"  {kid['code']:<10} {kid['name']:<50} {kid['count']:<10}")
        
        print("-" * 80)
        print(f"  ВСЕГО КЛИЕНТОВ В ГРУППЕ {parent_code}: {total_customers}")
    else:
        # Проверить, есть ли клиенты прямо в этой группе (если она используется как группа, а не папка)
        count = counts.get(parent_code, 0)
        if count > 0:
             print(f"  (Эта группа используется напрямую, клиентов: {count})")
        else:
             print(f"  (Нет подгрупп и нет клиентов)")

# Показать сирот (если есть группы, чей родитель не найден в parents)
print("\n" + "="*80)
print("ГРУППЫ БЕЗ ИЗВЕСТНОГО РОДИТЕЛЯ (Orphans)")
print("="*80 + "\n")

known_parents = set(parents.keys())
for parent_code, kids in children.items():
    if parent_code not in known_parents:
        print(f"Родитель {parent_code} (не найден в списке корневых групп):")
        for kid in kids:
            print(f"  {kid['code']:<10} {kid['name']:<50} {kid['count']:<10}")

conn.close()

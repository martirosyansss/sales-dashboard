"""
Показать все группы клиентов из базы данных
"""
import pyodbc

# Подключение к базе (как в app_v2.py)
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()
cursor = conn.cursor()

print("\n" + "="*80)
print("ГРУППЫ КЛИЕНТОВ ДЛЯ ПРОДАЖ (Customer Groups)")
print("="*80 + "\n")

# 1. Получить все группы из CUSTOMERS
query_groups = """
    SELECT DISTINCT fGROUP, COUNT(*) as CustomerCount
    FROM CUSTOMERS
    WHERE fGROUP IS NOT NULL AND fGROUP != ''
    GROUP BY fGROUP
    ORDER BY fGROUP
"""
cursor.execute(query_groups)
customer_groups = cursor.fetchall()

# 2. Получить названия из TREES
query_names = """
    SELECT fCODE, fCAPTION
    FROM TREES
    WHERE fTREEID = 'CustGrp'
    ORDER BY fCODE
"""
cursor.execute(query_names)
group_names = {row.fCODE: row.fCAPTION for row in cursor.fetchall()}

print(f"Найдено {len(customer_groups)} групп клиентов:\n")
print(f"{'Код':<10} {'Название':<50} {'Клиентов':<10}")
print("-" * 80)

for row in customer_groups:
    code = row.fGROUP
    name = group_names.get(code, '(нет названия)')
    count = row.CustomerCount
    print(f"{code:<10} {name:<50} {count:<10}")

print("\n" + "="*80)
print(f"Всего групп: {len(customer_groups)}")
print("="*80 + "\n")

# Показать какие группы есть в TREES, но не используются в CUSTOMERS
print("\nГруппы определены в TREES, но не используются:")
print("-" * 80)
unused = [code for code in group_names.keys() if code not in [r.fGROUP for r in customer_groups]]
for code in sorted(unused):
    print(f"{code:<10} {group_names[code]}")

conn.close()

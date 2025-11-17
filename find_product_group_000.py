from app_v2 import db

# Найти товары с группой содержащей "000"
print("=== Товары с группой содержащей '000' ===")
products = db.execute_query("""
    SELECT fID, fCODE, fNAME, fGROUP, fDISCOUNTGROUP
    FROM PRODUCTS 
    WHERE fGROUP LIKE '%000%' OR fGROUP LIKE '0%' OR fGROUP = '' OR fGROUP IS NULL
    ORDER BY fGROUP, fNAME
""")

print(f"Найдено товаров: {len(products)}\n")

# Сгруппировать по коду группы
from collections import defaultdict
by_group = defaultdict(list)
for p in products:
    group = p['fGROUP'] if p['fGROUP'] else '(пустая)'
    by_group[group].append(p)

# Показать статистику по группам
print("=== Статистика по группам ===")
for group in sorted(by_group.keys()):
    count = len(by_group[group])
    print(f"Группа '{group}': {count} товаров")

# Показать первые 10 товаров для каждой группы
print("\n=== Примеры товаров ===")
for group in sorted(by_group.keys())[:5]:  # Первые 5 групп
    products_list = by_group[group][:10]  # Первые 10 товаров
    print(f"\nГруппа '{group}':")
    for p in products_list:
        print(f"  {p['fCODE']}: {p['fNAME']}")

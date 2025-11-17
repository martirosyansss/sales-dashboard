from app_v2 import db

# Найти таблицы связанные с товарами
print("=== Поиск таблиц с товарами ===")
tables = db.execute_query("""
    SELECT TABLE_NAME 
    FROM INFORMATION_SCHEMA.TABLES 
    WHERE TABLE_TYPE='BASE TABLE' 
    ORDER BY TABLE_NAME
""")

product_tables = [t['TABLE_NAME'] for t in tables 
                  if any(word in t['TABLE_NAME'].upper() 
                        for word in ['MAT', 'PROD', 'GOOD', 'ITEM', 'TYPE'])]

print(f"Найдено таблиц: {len(product_tables)}")
for table in product_tables:
    print(f"  - {table}")

# Проверить структуру SALES
print("\n=== Колонки таблицы SALES ===")
sales_cols = db.execute_query("""
    SELECT COLUMN_NAME, DATA_TYPE 
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME='SALES' 
    ORDER BY ORDINAL_POSITION
""")
for col in sales_cols:
    print(f"  {col['COLUMN_NAME']}: {col['DATA_TYPE']}")

# Проверить есть ли таблица с товарами
print("\n=== Все таблицы в базе ===")
for t in sorted([x['TABLE_NAME'] for x in tables]):
    print(f"  {t}")

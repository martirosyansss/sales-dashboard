from app_v2 import db

# Посмотреть структуру таблицы PRODUCTS
print("=== Структура таблицы PRODUCTS ===")
cols = db.execute_query("""
    SELECT COLUMN_NAME, DATA_TYPE 
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME='PRODUCTS' 
    ORDER BY ORDINAL_POSITION
""")

for col in cols:
    print(f"  {col['COLUMN_NAME']}: {col['DATA_TYPE']}")

# Найти колонки с группами/типами
print("\n=== Колонки с GROUP/TYPE/CATEGORY ===")
group_cols = [c for c in cols if any(word in c['COLUMN_NAME'].upper() 
                                      for word in ['GROUP', 'TYPE', 'CATEG', 'CLASS'])]
for col in group_cols:
    print(f"  {col['COLUMN_NAME']}: {col['DATA_TYPE']}")

# Если есть колонка с группой - показать уникальные значения
if group_cols:
    for col in group_cols[:3]:  # Первые 3
        col_name = col['COLUMN_NAME']
        print(f"\n=== Уникальные значения в {col_name} ===")
        try:
            values = db.execute_query(f"""
                SELECT DISTINCT {col_name}, COUNT(*) as ProductCount
                FROM PRODUCTS 
                WHERE {col_name} IS NOT NULL
                GROUP BY {col_name}
                ORDER BY ProductCount DESC
            """)
            
            print(f"Найдено уникальных значений: {len(values)}")
            for v in values[:20]:  # Показать первые 20
                print(f"  {v[col_name]}: {v['ProductCount']} товаров")
        except Exception as e:
            print(f"  Ошибка: {e}")

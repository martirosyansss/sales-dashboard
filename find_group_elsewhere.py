from app_v2 import db

# Проверить таблицу SALEDOCDETAILS - там должны быть товары в продажах
print("=== Структура SALEDOCDETAILS ===")
cols = db.execute_query("""
    SELECT COLUMN_NAME, DATA_TYPE 
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE TABLE_NAME='SALEDOCDETAILS' 
    ORDER BY ORDINAL_POSITION
""")

for col in cols:
    print(f"  {col['COLUMN_NAME']}: {col['DATA_TYPE']}")

# Найти колонки с GROUP
print("\n=== Колонки с GROUP ===")
group_cols = [c for c in cols if 'GROUP' in c['COLUMN_NAME'].upper()]
for col in group_cols:
    print(f"  {col['COLUMN_NAME']}: {col['DATA_TYPE']}")

# Проверить есть ли товары с кодом группы
print("\n=== Поиск товаров по коду группы ===")
# Попробовать найти через fPRODUCTID
result = db.execute_query("""
    SELECT TOP 10 
        sd.fPRODUCTID,
        p.fCODE,
        p.fNAME,
        p.fGROUP as ProductGroup,
        sd.fQUANTITY,
        sd.fPRICE
    FROM SALEDOCDETAILS sd
    INNER JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
    WHERE p.fGROUP LIKE '%000%' OR p.fGROUP = ''
    ORDER BY sd.fSALEDOCISN DESC
""")

print(f"\nНайдено записей: {len(result)}")
for r in result[:5]:
    print(f"  Товар: {r['fCODE']} - {r['fNAME']}")
    print(f"    Группа: {r['ProductGroup']}, Кол-во: {r['fQUANTITY']}, Цена: {r['fPRICE']}")

# Проверить другие таблицы с GROUP
print("\n=== Поиск других таблиц с полем GROUP ===")
tables_with_group = db.execute_query("""
    SELECT DISTINCT TABLE_NAME, COLUMN_NAME
    FROM INFORMATION_SCHEMA.COLUMNS 
    WHERE COLUMN_NAME LIKE '%GROUP%'
    AND TABLE_NAME NOT IN ('PRODUCTS', 'CUSTOMERS')
    ORDER BY TABLE_NAME
""")

print(f"Найдено таблиц: {len(tables_with_group)}")
for t in tables_with_group[:20]:
    print(f"  {t['TABLE_NAME']}.{t['COLUMN_NAME']}")

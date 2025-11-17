import database as db

# Подключиться к базе
conn = db.get_connection()
cursor = conn.cursor()

# Получить список всех таблиц
cursor.execute("""
    SELECT TABLE_NAME 
    FROM INFORMATION_SCHEMA.TABLES 
    WHERE TABLE_TYPE = 'BASE TABLE'
    AND TABLE_NAME LIKE '%SALE%DETAIL%' OR TABLE_NAME LIKE '%DOC%DETAIL%'
    ORDER BY TABLE_NAME
""")

tables = cursor.fetchall()

print("Tables with SALE/DOC and DETAIL in name:")
for table in tables:
    print(f"  - {table[0]}")

# Попробуем найти все таблицы с DETAIL
cursor.execute("""
    SELECT TABLE_NAME 
    FROM INFORMATION_SCHEMA.TABLES 
    WHERE TABLE_TYPE = 'BASE TABLE'
    AND TABLE_NAME LIKE '%DETAIL%'
    ORDER BY TABLE_NAME
""")

tables2 = cursor.fetchall()

print("\nAll tables with DETAIL:")
for table in tables2:
    print(f"  - {table[0]}")

conn.close()

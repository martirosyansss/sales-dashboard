import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023'
)

cursor = conn.cursor()

# 1. Найти таблицы со словом DEBT
print("\n=== ТАБЛИЦЫ С DEBT В НАЗВАНИИ ===")
cursor.execute("""
    SELECT TABLE_NAME
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME LIKE '%DEBT%'
    ORDER BY TABLE_NAME
""")
debt_tables = cursor.fetchall()
if debt_tables:
    for row in debt_tables:
        print(f"  {row[0]}")
else:
    print("  Нет таблиц с DEBT")

# 2. Проверить структуру CUSTOMERS на наличие полей с долгом/лимитом
print("\n=== КОЛОНКИ CUSTOMERS СВЯЗАННЫЕ С ДОЛГОМ/ЛИМИТОМ ===")
cursor.execute("""
    SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'CUSTOMERS'
    AND (
        COLUMN_NAME LIKE '%DEBT%' OR
        COLUMN_NAME LIKE '%CREDIT%' OR
        COLUMN_NAME LIKE '%LIMIT%' OR
        COLUMN_NAME LIKE '%BALANCE%' OR
        COLUMN_NAME LIKE '%DELAY%' OR
        COLUMN_NAME LIKE '%DUE%'
    )
    ORDER BY ORDINAL_POSITION
""")
customer_cols = cursor.fetchall()
if customer_cols:
    for row in customer_cols:
        print(f"  {row[0]} - {row[1]}")
else:
    print("  Нет колонок")

# 3. Если есть таблица DEBT, показать ее структуру
for table in debt_tables:
    table_name = table[0]
    print(f"\n=== СТРУКТУРА ТАБЛИЦЫ {table_name} ===")
    cursor.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{table_name}'
        ORDER BY ORDINAL_POSITION
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]} - {row[1]}")
    
    # Показать пример данных
    print(f"\n=== ПРИМЕР ДАННЫХ ИЗ {table_name} ===")
    cursor.execute(f"SELECT TOP 3 * FROM {table_name}")
    columns = [desc[0] for desc in cursor.description]
    print(f"  Columns: {', '.join(columns)}")
    for row in cursor.fetchall():
        print(f"  {row}")

# 4. Проверить REST таблицу (остатки)
print("\n=== ТАБЛИЦА REST (ОСТАТКИ) ===")
try:
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'REST'
        ORDER BY ORDINAL_POSITION
    """)
    rest_cols = cursor.fetchall()
    if rest_cols:
        print("Колонки:")
        for row in rest_cols:
            print(f"  {row[0]} - {row[1]}")
        
        cursor.execute("SELECT TOP 3 * FROM REST")
        print("\nПример данных:")
        columns = [desc[0] for desc in cursor.description]
        print(f"  Columns: {', '.join(columns)}")
        for row in cursor.fetchall():
            print(f"  {row}")
    else:
        print("  Таблица REST не найдена")
except Exception as e:
    print(f"  Ошибка: {e}")

conn.close()

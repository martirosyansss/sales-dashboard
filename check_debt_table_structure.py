"""
Проверка реальной структуры таблиц долгов
"""

import pyodbc

conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)

try:
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    
    print("=" * 80)
    print("СТРУКТУРА ТАБЛИЦ ДОЛГОВ")
    print("=" * 80)
    
    # 1. Структура HICUSTOMERSDEBT
    print("\n1. КОЛОНКИ ТАБЛИЦЫ HICUSTOMERSDEBT:")
    print("-" * 80)
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'HICUSTOMERSDEBT'
        ORDER BY ORDINAL_POSITION
    """)
    
    for row in cursor.fetchall():
        length = f"({row.CHARACTER_MAXIMUM_LENGTH})" if row.CHARACTER_MAXIMUM_LENGTH else ""
        print(f"  {row.COLUMN_NAME:<30} {row.DATA_TYPE}{length}")
    
    # 2. Структура HIRESTCUSTOMERSSUM
    print("\n2. КОЛОНКИ ТАБЛИЦЫ HIRESTCUSTOMERSSUM:")
    print("-" * 80)
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'HIRESTCUSTOMERSSUM'
        ORDER BY ORDINAL_POSITION
    """)
    
    for row in cursor.fetchall():
        length = f"({row.CHARACTER_MAXIMUM_LENGTH})" if row.CHARACTER_MAXIMUM_LENGTH else ""
        print(f"  {row.COLUMN_NAME:<30} {row.DATA_TYPE}{length}")
    
    # 3. Примеры данных из HICUSTOMERSDEBT
    print("\n3. ПРИМЕРЫ ДАННЫХ ИЗ HICUSTOMERSDEBT (TOP 5):")
    print("-" * 80)
    cursor.execute("SELECT TOP 5 * FROM HICUSTOMERSDEBT")
    
    columns = [column[0] for column in cursor.description]
    print("Колонки:", ", ".join(columns))
    print()
    
    for row in cursor.fetchall():
        for i, col in enumerate(columns):
            print(f"  {col}: {row[i]}")
        print("-" * 40)
    
    # 4. Примеры данных из HIRESTCUSTOMERSSUM
    print("\n4. ПРИМЕРЫ ДАННЫХ ИЗ HIRESTCUSTOMERSSUM (TOP 5):")
    print("-" * 80)
    cursor.execute("SELECT TOP 5 * FROM HIRESTCUSTOMERSSUM")
    
    columns = [column[0] for column in cursor.description]
    print("Колонки:", ", ".join(columns))
    print()
    
    for row in cursor.fetchall():
        for i, col in enumerate(columns):
            print(f"  {col}: {row[i]}")
        print("-" * 40)
    
    # 5. Поиск других таблиц с долгами
    print("\n5. ВСЕ ТАБЛИЦЫ СО СЛОВОМ 'DEBT', 'REST', 'HI':")
    print("-" * 80)
    cursor.execute("""
        SELECT name
        FROM sys.tables
        WHERE name LIKE '%DEBT%' 
           OR name LIKE '%REST%'
           OR name LIKE 'HI%'
        ORDER BY name
    """)
    
    for row in cursor.fetchall():
        print(f"  - {row.name}")
    
    conn.close()
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

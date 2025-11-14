"""
Поиск таблиц с клиентами, продажами и менеджерами в AS-Sales Management
"""
import pyodbc

try:
    connection_string = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=SalesManagement;"
        "UID=sa;"
        "PWD=Aa123456;"
        "TrustServerCertificate=yes;"
    )
    
    conn = pyodbc.connect(connection_string)
    cursor = conn.cursor()
    
    print("=" * 80)
    print("ПОИСК ТАБЛИЦ С КЛИЕНТАМИ И ПРОДАЖАМИ")
    print("=" * 80)
    print()
    
    # Поиск таблиц со словом "Customer" или похожими
    search_terms = ['CUSTOMER', 'PARTNER', 'CLIENT', 'SALE', 'ORDER', 'AGENT', 'AREA', 'DIVISION']
    
    cursor.execute("""
        SELECT TABLE_NAME 
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
    """)
    
    all_tables = [row[0] for row in cursor.fetchall()]
    
    print("Таблицы которые могут содержать нужные данные:")
    print()
    
    for term in search_terms:
        matching = [t for t in all_tables if term in t.upper()]
        if matching:
            print(f"📁 Таблицы с '{term}':")
            for table in matching:
                cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
                count = cursor.fetchone()[0]
                print(f"   - {table} ({count} записей)")
            print()
    
    # Проверим таблицу PARTNERS (обычно там клиенты)
    if 'PARTNERS' in all_tables:
        print("=" * 80)
        print("СТРУКТУРА ТАБЛИЦЫ PARTNERS (Клиенты/Партнеры)")
        print("=" * 80)
        
        cursor.execute("""
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'PARTNERS'
            ORDER BY ORDINAL_POSITION
        """)
        
        for col in cursor.fetchall():
            col_name = col[0]
            data_type = col[1]
            max_len = col[2] if col[2] else ''
            print(f"  {col_name:<30} {data_type}({max_len})" if max_len else f"  {col_name:<30} {data_type}")
        
        # Примеры данных
        cursor.execute("SELECT TOP 3 * FROM PARTNERS")
        print("\nПримеры данных:")
        rows = cursor.fetchall()
        if rows:
            for i, row in enumerate(rows, 1):
                print(f"  Запись {i}: {row[:5]}")  # Первые 5 полей
        print()
    
    # Проверим таблицу SALES (продажи)
    if 'SALES' in all_tables:
        print("=" * 80)
        print("СТРУКТУРА ТАБЛИЦЫ SALES (Продажи)")
        print("=" * 80)
        
        cursor.execute("""
            SELECT COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'SALES'
            ORDER BY ORDINAL_POSITION
        """)
        
        for col in cursor.fetchall():
            print(f"  {col[0]:<30} {col[1]}")
        print()
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"Ошибка: {str(e)}")

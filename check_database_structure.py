"""
Скрипт для проверки структуры базы данных SalesManagement
"""
import pyodbc

try:
    # Подключение к БД
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
    print("СТРУКТУРА БАЗЫ ДАННЫХ SalesManagement")
    print("=" * 80)
    print()
    
    # Получить список всех таблиц
    cursor.execute("""
        SELECT TABLE_NAME 
        FROM INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
    """)
    
    tables = [row[0] for row in cursor.fetchall()]
    
    if not tables:
        print("⚠️  База данных пуста - таблицы не найдены")
        print()
        print("Возможные причины:")
        print("1. База данных не создана")
        print("2. Таблицы не созданы")
        print("3. Нет прав доступа")
        conn.close()
        exit()
    
    print(f"Найдено таблиц: {len(tables)}")
    print()
    
    # Для каждой таблицы вывести структуру
    for table in tables:
        print(f"📋 Таблица: {table}")
        print("-" * 80)
        
        # Получить колонки
        cursor.execute(f"""
            SELECT 
                COLUMN_NAME,
                DATA_TYPE,
                CHARACTER_MAXIMUM_LENGTH,
                IS_NULLABLE,
                COLUMN_DEFAULT
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = '{table}'
            ORDER BY ORDINAL_POSITION
        """)
        
        columns = cursor.fetchall()
        
        print(f"{'Колонка':<30} {'Тип':<20} {'Null?':<8} {'По умолчанию':<20}")
        print("-" * 80)
        
        for col in columns:
            col_name = col[0]
            data_type = col[1]
            max_length = col[2]
            is_nullable = "YES" if col[3] == "YES" else "NO"
            default_val = str(col[4]) if col[4] else "-"
            
            # Формат типа данных
            if max_length:
                type_str = f"{data_type}({max_length})"
            else:
                type_str = data_type
            
            print(f"{col_name:<30} {type_str:<20} {is_nullable:<8} {default_val:<20}")
        
        # Количество записей
        cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
        count = cursor.fetchone()[0]
        
        print()
        print(f"📊 Количество записей: {count}")
        
        # Если есть записи, показать пример
        if count > 0:
            cursor.execute(f"SELECT TOP 3 * FROM [{table}]")
            sample_rows = cursor.fetchall()
            
            if sample_rows:
                print()
                print("Примеры данных (первые 3 записи):")
                for i, row in enumerate(sample_rows, 1):
                    print(f"  Запись {i}: {tuple(row)}")
        
        print()
        print()
    
    # Проверить наличие ключевых полей для нашей логики
    print("=" * 80)
    print("ПРОВЕРКА ПОЛЕЙ ДЛЯ МЕНЕДЖЕРОВ, ДИСТРИБЬЮТОРОВ, СЕТЕЙ")
    print("=" * 80)
    print()
    
    if 'Customers' in tables:
        cursor.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'Customers'
        """)
        customer_columns = [row[0] for row in cursor.fetchall()]
        
        print("Колонки в таблице Customers:")
        for col in customer_columns:
            print(f"  - {col}")
        
        print()
        
        # Проверить наличие нужных полей
        required_fields = {
            'SalesArea': 'Менеджеры (территория продаж)',
            'CustomerGroup': 'Дистрибьюторы (группа покупателей)',
            'ManagerID': 'ID Менеджера',
            'NetworkID': 'ID Сети'
        }
        
        print("Проверка ключевых полей:")
        for field, description in required_fields.items():
            if field in customer_columns:
                print(f"  ✅ {field} - найдено ({description})")
                
                # Показать уникальные значения
                cursor.execute(f"""
                    SELECT DISTINCT TOP 10 [{field}]
                    FROM Customers
                    WHERE [{field}] IS NOT NULL
                    ORDER BY [{field}]
                """)
                values = [str(row[0]) for row in cursor.fetchall()]
                if values:
                    print(f"     Примеры значений: {', '.join(values[:5])}")
            else:
                print(f"  ❌ {field} - НЕ НАЙДЕНО ({description})")
        
        print()
    
    # Проверить Sales
    if 'Sales' in tables:
        cursor.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'Sales'
        """)
        sales_columns = [row[0] for row in cursor.fetchall()]
        
        print("Колонки в таблице Sales:")
        for col in sales_columns:
            print(f"  - {col}")
        print()
    
    print("=" * 80)
    print("ПРОВЕРКА ЗАВЕРШЕНА")
    print("=" * 80)
    
    cursor.close()
    conn.close()
    
except pyodbc.Error as e:
    print(f"❌ Ошибка подключения к базе данных:")
    print(f"   {str(e)}")
    print()
    print("Возможные решения:")
    print("1. Убедитесь, что SQL Server запущен")
    print("2. Проверьте логин и пароль (sa / Aa123456)")
    print("3. Проверьте имя базы данных (SalesManagement)")

except Exception as e:
    print(f"❌ Неожиданная ошибка: {str(e)}")

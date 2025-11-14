"""
Детальный анализ таблиц CUSTOMERS, SALESAGENTS
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
    print("ТАБЛИЦА CUSTOMERS (Клиенты)")
    print("=" * 80)
    print()
    
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'CUSTOMERS'
        ORDER BY ORDINAL_POSITION
    """)
    
    print("Колонки:")
    for col in cursor.fetchall():
        col_name = col[0]
        data_type = col[1]
        max_len = f"({col[2]})" if col[2] else ''
        print(f"  {col_name:<40} {data_type}{max_len}")
    
    # Примеры данных
    cursor.execute("SELECT TOP 5 fID, fCODE, fNAME, fPRICELIST, fGROUP, fREGION FROM CUSTOMERS")
    print("\nПримеры данных (первые 5 клиентов):")
    for row in cursor.fetchall():
        print(f"  ID: {row[0]}, Код: {row[1]}, Название: {row[2]}, Прайс: {row[3]}, Группа: {row[4]}, Регион: {row[5]}")
    
    print("\n" + "=" * 80)
    print("ТАБЛИЦА SALESAGENTS (Менеджеры по продажам)")
    print("=" * 80)
    print()
    
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'SALESAGENTS'
        ORDER BY ORDINAL_POSITION
    """)
    
    print("Колонки:")
    for col in cursor.fetchall():
        col_name = col[0]
        data_type = col[1]
        max_len = f"({col[2]})" if col[2] else ''
        print(f"  {col_name:<40} {data_type}{max_len}")
    
    # Примеры данных
    cursor.execute("SELECT TOP 10 fID, fCODE, fNAME, fUSERID, fCLOSED FROM SALESAGENTS")
    print("\nПримеры данных (все менеджеры):")
    for row in cursor.fetchall():
        print(f"  ID: {row[0]}, Код: {row[1]}, Имя: {row[2]}, UserID: {row[3]}, Закрыт: {row[4]}")
    
    # Проверим связь
    print("\n" + "=" * 80)
    print("СВЯЗЬ МЕЖДУ ТАБЛИЦАМИ")
    print("=" * 80)
    print()
    
    cursor.execute("""
        SELECT DISTINCT s.fSALESAGENTID, sa.fNAME, COUNT(DISTINCT s.fCUSTOMERID) as CustomerCount
        FROM SALES s
        LEFT JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
        WHERE s.fSALESAGENTID IS NOT NULL
        GROUP BY s.fSALESAGENTID, sa.fNAME
        ORDER BY CustomerCount DESC
    """)
    
    print("Менеджеры и количество их клиентов:")
    for row in cursor.fetchall():
        print(f"  Менеджер ID: {row[0]}, Имя: {row[1]}, Клиентов: {row[2]}")
    
    # Проверим группы клиентов (дистрибьюторы)
    print("\n" + "=" * 80)
    print("ГРУППЫ КЛИЕНТОВ (возможно дистрибьюторы)")
    print("=" * 80)
    print()
    
    cursor.execute("""
        SELECT fGROUP, COUNT(*) as Count
        FROM CUSTOMERS
        WHERE fGROUP IS NOT NULL AND fGROUP <> ''
        GROUP BY fGROUP
        ORDER BY Count DESC
    """)
    
    print("Группы клиентов:")
    for row in cursor.fetchall():
        print(f"  Группа: {row[0]}, Клиентов: {row[1]}")
    
    # Проверим территории (SalesArea)
    print("\n" + "=" * 80)
    print("ТЕРРИТОРИИ ПРОДАЖ (SalesArea)")
    print("=" * 80)
    print()
    
    cursor.execute("""
        SELECT DISTINCT fSALESAREA, COUNT(*) as SalesCount
        FROM SALES
        WHERE fSALESAREA IS NOT NULL
        GROUP BY fSALESAREA
        ORDER BY SalesCount DESC
    """)
    
    print("Территории:")
    for row in cursor.fetchall():
        print(f"  Территория: {row[0]}, Продаж: {row[1]}")
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"Ошибка: {str(e)}")

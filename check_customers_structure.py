import pyodbc

conn_str = (
    r'DRIVER={ODBC Driver 17 for SQL Server};'
    r'SERVER=localhost;'
    r'DATABASE=SalesManagement-;'
    r'UID=sa;'
    r'PWD=Aa123456;'
    r'TrustServerCertificate=yes;'
)

conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

print("=" * 80)
print("Структура таблицы CUSTOMERS")
print("=" * 80)
cursor.execute("SELECT TOP 1 * FROM CUSTOMERS")
columns = [column[0] for column in cursor.description]
print("Колонки:", ', '.join(columns))

print("\n" + "=" * 80)
print("Поиск связи с SALESAGENTS")
print("=" * 80)
# Ищем колонки с ID менеджера
salesagent_columns = [col for col in columns if 'SALES' in col.upper() or 'AGENT' in col.upper() or 'MANAGER' in col.upper()]
print(f"Колонки связанные с менеджером: {salesagent_columns}")

print("\n" + "=" * 80)
print("Проверка: какая колонка связывает CUSTOMERS с SALESAGENTS?")
print("=" * 80)

# Пробуем найти менеджера A006 через разные поля
for col in salesagent_columns:
    try:
        query = f"""
        SELECT COUNT(*) as cnt
        FROM CUSTOMERS c
        INNER JOIN SALESAGENTS sa ON c.{col} = sa.fID
        WHERE sa.fCODE = 'A006'
        """
        cursor.execute(query)
        count = cursor.fetchone().cnt
        print(f"  {col}: {count} клиентов")
    except Exception as e:
        print(f"  {col}: ОШИБКА - {e}")

conn.close()

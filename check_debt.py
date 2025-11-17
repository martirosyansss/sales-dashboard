import pyodbc

# Подключение к базе данных
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

# Запрос для менеджера A006 (Симонян Лиана)
query = """
SELECT 
    sa.fID,
    sa.fCODE,
    sa.fNAME,
    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as TotalDebt
FROM SALESAGENTS sa
LEFT JOIN DOCUMENTS doc ON doc.fSALESAGENTID = sa.fID
LEFT JOIN HICUSTOMERSDEBT d ON d.fDEBTDOCISN = doc.fISN
LEFT JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
WHERE sa.fCODE = 'A006'
GROUP BY sa.fID, sa.fCODE, sa.fNAME
"""

print("Запрос для менеджера A006 (Симонян Лиана):")
print("=" * 80)
cursor.execute(query)
for row in cursor.fetchall():
    print(f"ID: {row.fID}")
    print(f"Code: {row.fCODE}")
    print(f"Name: {row.fNAME}")
    print(f"Total Debt: {row.TotalDebt:,.2f} AMD")

print("\n" + "=" * 80)
print("\nЗапрос с фильтром по группам клиентов (из group_manager_assignments.json):")
print("=" * 80)

# Проверяем, какие группы назначены менеджеру
import json
import os

if os.path.exists('group_manager_assignments.json'):
    with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
        assignments = json.load(f)
    
    # Ищем ID менеджера A006
    cursor.execute("SELECT fID FROM SALESAGENTS WHERE fCODE = 'A006'")
    manager_id = cursor.fetchone().fID
    
    # Ищем группы для этого менеджера
    manager_groups = []
    for group_code, manager_ids in assignments.items():
        if not isinstance(manager_ids, list):
            manager_ids = [manager_ids]
        if manager_id in manager_ids:
            manager_groups.append(group_code)
    
    print(f"Менеджер ID: {manager_id}")
    print(f"Назначенные группы: {manager_groups}")
    
    if manager_groups:
        placeholders = ','.join(['?'] * len(manager_groups))
        query_with_groups = f"""
        SELECT 
            sa.fID,
            sa.fCODE,
            sa.fNAME,
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as TotalDebt
        FROM SALESAGENTS sa
        LEFT JOIN DOCUMENTS doc ON doc.fSALESAGENTID = sa.fID
        LEFT JOIN HICUSTOMERSDEBT d ON d.fDEBTDOCISN = doc.fISN
        LEFT JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE sa.fCODE = 'A006'
            AND c.fGROUP IN ({placeholders})
        GROUP BY sa.fID, sa.fCODE, sa.fNAME
        """
        
        cursor.execute(query_with_groups, tuple(manager_groups))
        for row in cursor.fetchall():
            print(f"\nDebt with group filter:")
            print(f"Total Debt: {row.TotalDebt:,.2f} AMD")
    else:
        print("\nНет назначенных групп для этого менеджера")

conn.close()

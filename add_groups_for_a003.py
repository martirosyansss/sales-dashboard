"""
Узнать какие группы нужно добавить для менеджера A003
"""

import pyodbc
import json

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
    
    # Получить инфо о менеджере A003
    cursor.execute("SELECT fID FROM SALESAGENTS WHERE fCODE = 'A003'")
    manager = cursor.fetchone()
    manager_id = manager.fID
    
    print(f"Менеджер A003 (ID={manager_id})")
    print("=" * 80)
    
    # Группы клиентов с долгами
    cursor.execute("""
        SELECT 
            c.fGROUP,
            COUNT(DISTINCT c.fID) as CustomerCount,
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as TotalDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID 
            FROM SALES 
            WHERE fSALESAGENTID = ?
        )
        GROUP BY c.fGROUP
        HAVING SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) > 0
        ORDER BY TotalDebt DESC
    """, (manager_id,))
    
    groups_with_debt = cursor.fetchall()
    
    print("\nГруппы клиентов с долгами:")
    print("-" * 80)
    total_debt = 0
    groups_list = []
    
    for row in groups_with_debt:
        group = row.fGROUP if row.fGROUP else "БЕЗ ГРУППЫ"
        debt = float(row.TotalDebt)
        total_debt += debt
        if row.fGROUP:
            groups_list.append(row.fGROUP)
        print(f"Группа {group}: {row.CustomerCount} клиентов, долг {debt:,.2f} AMD")
    
    print("-" * 80)
    print(f"ИТОГО: {len(groups_with_debt)} групп, долг {total_debt:,.2f} AMD")
    
    # Группы для добавления в JSON
    print("\n" + "=" * 80)
    print("РЕКОМЕНДАЦИЯ:")
    print("=" * 80)
    print(f"Добавить в group_manager_assignments.json следующие группы для ID={manager_id}:")
    print(f"Группы: {groups_list}")
    print(f"\nJSON код:")
    
    # Загрузить текущие назначения
    try:
        with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
            assignments = json.load(f)
    except:
        assignments = {}
    
    # Добавить группы для A003
    for group in groups_list:
        if group not in assignments:
            assignments[group] = []
        if manager_id not in assignments[group]:
            assignments[group].append(manager_id)
    
    print(json.dumps(assignments, indent=2, ensure_ascii=False))
    
    # Сохранить
    with open('group_manager_assignments.json', 'w', encoding='utf-8') as f:
        json.dump(assignments, f, indent=2, ensure_ascii=False)
    
    print("\n✓ Файл group_manager_assignments.json обновлен!")
    
    conn.close()
    
except Exception as e:
    print(f"ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

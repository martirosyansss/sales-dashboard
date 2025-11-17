"""
Тест нового расчета долга напрямую
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
    
    manager_id = 9  # A003
    
    # Загрузить назначенные группы
    with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
        assignments = json.load(f)
    
    responsible_groups = []
    for group_code, manager_ids in assignments.items():
        if isinstance(manager_ids, list) and manager_id in manager_ids:
            responsible_groups.append(group_code)
    
    if not responsible_groups:
        print("❌ Нет назначенных групп!")
        exit(1)
    
    placeholders = ','.join(['?'] * len(responsible_groups))
    group_filter = f" AND c.fGROUP IN ({placeholders})"
    group_params = tuple(responsible_groups)
    
    # НОВАЯ ФОРМУЛА
    debt_query = f"""
        SELECT 
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
            {group_filter}
    """
    
    all_params = (manager_id,) + group_params
    cursor.execute(debt_query, all_params)
    debt_row = cursor.fetchone()
    
    if debt_row:
        net_debt = float(debt_row.NetDebt) if debt_row.NetDebt else 0
        final_debt = net_debt
        
        print(f"Менеджер A003 (ID={manager_id})")
        print(f"Назначено групп: {len(responsible_groups)}")
        print(f"\nЧистый долг (D - C): {net_debt:,.2f} AMD")
        print(f"Финальный долг: {final_debt:,.2f} AMD")
        print(f"\nОжидаемый: 5,289,036.77 AMD")
        print(f"Разница: {abs(final_debt - 5289036.77):,.2f} AMD ({abs(final_debt - 5289036.77) / 5289036.77 * 100:.2f}%)")
        
        if abs(final_debt - 5289036.77) / 5289036.77 * 100 < 1:
            print("\n✅ ОТЛИЧНО! Отклонение менее 1%")
        else:
            print(f"\n❌ Отклонение {abs(final_debt - 5289036.77) / 5289036.77 * 100:.2f}%")
    
    conn.close()
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

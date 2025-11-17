"""
Показать реальный долг для менеджера A003 с новой формулой
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
    
    print("=" * 80)
    print("РЕАЛЬНЫЙ ДОЛГ МЕНЕДЖЕРА A003 (Վերդոյան Նորայր)")
    print("=" * 80)
    
    # Получить информацию о менеджере
    cursor.execute("""
        SELECT fID, fCODE, fNAME
        FROM SALESAGENTS
        WHERE fCODE = 'A003'
    """)
    
    manager = cursor.fetchone()
    if not manager:
        print("❌ Менеджер A003 не найден!")
        exit(1)
    
    manager_id = manager.fID
    manager_code = manager.fCODE
    manager_name = manager.fNAME
    
    print(f"\nМенеджер: {manager_name} ({manager_code})")
    print(f"ID: {manager_id}")
    
    # Загрузить назначенные группы
    with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
        assignments = json.load(f)
    
    responsible_groups = []
    for group_code, manager_ids in assignments.items():
        if isinstance(manager_ids, list) and manager_id in manager_ids:
            responsible_groups.append(group_code)
    
    print(f"\nНазначено групп: {len(responsible_groups)}")
    if responsible_groups:
        print(f"Группы: {', '.join(sorted(responsible_groups))}")
    else:
        print("❌ НЕТ НАЗНАЧЕННЫХ ГРУПП - долг будет 0!")
        exit(0)
    
    # НОВАЯ ФОРМУЛА ДОЛГА
    placeholders = ','.join(['?'] * len(responsible_groups))
    
    query = f"""
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
        AND c.fGROUP IN ({placeholders})
    """
    
    params = (manager_id,) + tuple(responsible_groups)
    cursor.execute(query, params)
    
    result = cursor.fetchone()
    net_debt = float(result.NetDebt) if result and result.NetDebt else 0
    final_debt = net_debt
    
    print("\n" + "=" * 80)
    print("РАСЧЕТ ДОЛГА")
    print("=" * 80)
    print(f"\nЧистый долг (Дебет - Кредит):")
    print(f"  {net_debt:,.2f} AMD")
    
    print("\n" + "=" * 80)
    print("ИТОГОВЫЙ ДОЛГ")
    print("=" * 80)
    print(f"\n✅ ДОЛГ МЕНЕДЖЕРА {manager_code} ({manager_name}):")
    print(f"   {final_debt:,.2f} AMD")
    print("=" * 80)
    
    # Дополнительно: количество клиентов с долгами
    query_customers = f"""
        SELECT COUNT(DISTINCT doc.fCUSTOMERID) as CustomerCount
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        AND c.fGROUP IN ({placeholders})
        AND d.fDBCR = 'D'
        AND d.fSUM > 0
    """
    
    cursor.execute(query_customers, params)
    customer_count = cursor.fetchone().CustomerCount
    
    print(f"\nКлиентов с долгами: {customer_count}")
    
    if customer_count > 0:
        print(f"Средний долг на клиента: {final_debt / customer_count:,.2f} AMD")
    
    conn.close()
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

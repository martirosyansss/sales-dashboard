"""
Проверка долга с учетом ТОЛЬКО назначенных групп (25 групп из настроек)
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
    
    # Найти группы для менеджера A003 (ID=9)
    assigned_groups = []
    for group_code, manager_ids in assignments.items():
        if isinstance(manager_ids, list) and manager_id in manager_ids:
            assigned_groups.append(group_code)
    
    print("=" * 80)
    print("ДОЛГ С УЧЕТОМ ТОЛЬКО НАЗНАЧЕННЫХ ГРУПП")
    print("=" * 80)
    print(f"\nМенеджер: A003 (ID={manager_id})")
    print(f"Назначено групп: {len(assigned_groups)}")
    print(f"Группы: {', '.join(sorted(assigned_groups))}")
    
    if not assigned_groups:
        print("\n❌ НЕТ НАЗНАЧЕННЫХ ГРУПП!")
        exit(1)
    
    # Создать плейсхолдеры для SQL
    placeholders = ','.join(['?'] * len(assigned_groups))
    
    print("\n" + "=" * 80)
    print("МЕТОД 1: DOCUMENTS.SALESAGENTID + группы")
    print("=" * 80)
    
    # Долг через HICUSTOMERSDEBT с фильтром по группам
    query1 = f"""
        SELECT 
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fSALESAGENTID = ?
        AND c.fGROUP IN ({placeholders})
    """
    
    cursor.execute(query1, (manager_id,) + tuple(assigned_groups))
    debt1 = float(cursor.fetchone().NetDebt or 0)
    print(f"\nHICUSTOMERSDEBT (SALESAGENTID + группы): {debt1:,.2f} AMD")
    
    # Добавить HIRESTCUSTOMERSSUM с фильтром по группам
    query2 = f"""
        SELECT 
            SUM(CASE WHEN fTYPE = '01' THEN fSUM ELSE 0 END) as Type01,
            SUM(CASE WHEN fTYPE = '02' THEN fSUM ELSE 0 END) as Type02
        FROM HIRESTCUSTOMERSSUM r
        INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT doc.fCUSTOMERID
            FROM DOCUMENTS doc
            WHERE doc.fSALESAGENTID = ?
        )
        AND c.fGROUP IN ({placeholders})
    """
    
    cursor.execute(query2, (manager_id,) + tuple(assigned_groups))
    row = cursor.fetchone()
    type01_m1 = float(row.Type01 or 0)
    type02_m1 = float(row.Type02 or 0)
    
    print(f"HIRESTCUSTOMERSSUM Type='01': {type01_m1:,.2f} AMD")
    print(f"HIRESTCUSTOMERSSUM Type='02': {type02_m1:,.2f} AMD")
    
    # Со swap
    total1 = debt1 + type02_m1 + type01_m1
    print(f"\nИТОГО (со swap): {debt1:,.2f} + {type02_m1:,.2f} + {type01_m1:,.2f}")
    print(f"ИТОГО: {total1:,.2f} AMD")
    
    print("\n" + "=" * 80)
    print("МЕТОД 2: SALES customers + группы")
    print("=" * 80)
    
    # Долг через SALES customers с фильтром по группам
    query3 = f"""
        SELECT 
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as NetDebt
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
    
    cursor.execute(query3, (manager_id,) + tuple(assigned_groups))
    debt2 = float(cursor.fetchone().NetDebt or 0)
    print(f"\nHICUSTOMERSDEBT (SALES + группы): {debt2:,.2f} AMD")
    
    # HIRESTCUSTOMERSSUM с группами
    query4 = f"""
        SELECT 
            SUM(CASE WHEN fTYPE = '01' THEN fSUM ELSE 0 END) as Type01,
            SUM(CASE WHEN fTYPE = '02' THEN fSUM ELSE 0 END) as Type02
        FROM HIRESTCUSTOMERSSUM r
        INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID
            FROM SALES
            WHERE fSALESAGENTID = ?
        )
        AND c.fGROUP IN ({placeholders})
    """
    
    cursor.execute(query4, (manager_id,) + tuple(assigned_groups))
    row = cursor.fetchone()
    type01_m2 = float(row.Type01 or 0)
    type02_m2 = float(row.Type02 or 0)
    
    print(f"HIRESTCUSTOMERSSUM Type='01': {type01_m2:,.2f} AMD")
    print(f"HIRESTCUSTOMERSSUM Type='02': {type02_m2:,.2f} AMD")
    
    # Со swap
    total2 = debt2 + type02_m2 + type01_m2
    print(f"\nИТОГО (со swap): {debt2:,.2f} + {type02_m2:,.2f} + {type01_m2:,.2f}")
    print(f"ИТОГО: {total2:,.2f} AMD")
    
    print("\n" + "=" * 80)
    print("МЕТОД 3: Только HICUSTOMERSDEBT (без HIRESTCUSTOMERSSUM)")
    print("=" * 80)
    
    print(f"\nМетод 1 (SALESAGENTID): {debt1:,.2f} AMD")
    print(f"Метод 2 (SALES customers): {debt2:,.2f} AMD")
    
    # Сравнение
    expected = 5289036.77
    print("\n" + "=" * 80)
    print("СРАВНЕНИЕ С ОЖИДАЕМЫМ")
    print("=" * 80)
    print(f"\nОжидаемый долг: {expected:,.2f} AMD")
    print(f"\nРазличия:")
    print(f"  Метод 1 (SALESAGENTID + swap): {total1:,.2f} AMD (разница: {abs(total1 - expected):,.2f})")
    print(f"  Метод 2 (SALES + swap): {total2:,.2f} AMD (разница: {abs(total2 - expected):,.2f})")
    print(f"  Метод 1 (только HICUSTOMERSDEBT): {debt1:,.2f} AMD (разница: {abs(debt1 - expected):,.2f})")
    print(f"  Метод 2 (только HICUSTOMERSDEBT): {debt2:,.2f} AMD (разница: {abs(debt2 - expected):,.2f})")
    
    # Найти лучший
    methods = [
        ("Метод 1 (SALESAGENTID + swap)", total1),
        ("Метод 2 (SALES + swap)", total2),
        ("Метод 1 (только HICUSTOMERSDEBT)", debt1),
        ("Метод 2 (только HICUSTOMERSDEBT)", debt2),
    ]
    
    best = min(methods, key=lambda x: abs(x[1] - expected))
    print(f"\n\u2705 БЛИЖАЙШИЙ: {best[0]}")
    print(f"   Значение: {best[1]:,.2f} AMD")
    print(f"   Разница: {abs(best[1] - expected):,.2f} AMD ({abs(best[1] - expected) / expected * 100:.1f}%)")
    
    conn.close()
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

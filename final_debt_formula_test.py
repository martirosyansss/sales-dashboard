"""
Финальная проверка: правильная формула долга
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
    
    assigned_groups = []
    for group_code, manager_ids in assignments.items():
        if isinstance(manager_ids, list) and manager_id in manager_ids:
            assigned_groups.append(group_code)
    
    placeholders = ','.join(['?'] * len(assigned_groups))
    
    print("=" * 80)
    print("ФИНАЛЬНЫЙ ТЕСТ: ПРАВИЛЬНАЯ ФОРМУЛА ДОЛГА")
    print("=" * 80)
    print(f"\nМенеджер: A003 (ID={manager_id})")
    print(f"Назначено групп: {len(assigned_groups)}")
    
    expected = 5289036.77
    print(f"Ожидаемый долг: {expected:,.2f} AMD")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 1: Только дебет (D) - без кредита")
    print("=" * 80)
    
    query1 = f"""
        SELECT 
            SUM(d.fSUM) as DebitOnly
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
    """
    
    cursor.execute(query1, (manager_id,) + tuple(assigned_groups))
    debit_only = float(cursor.fetchone().DebitOnly or 0)
    print(f"\nТолько дебет (D): {debit_only:,.2f} AMD")
    print(f"Разница: {abs(debit_only - expected):,.2f} AMD ({abs(debit_only - expected) / expected * 100:.2f}%)")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 2: Дебет минус кредит")
    print("=" * 80)
    
    query2 = f"""
        SELECT 
            SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE 0 END) as Debit,
            SUM(CASE WHEN d.fDBCR = 'C' THEN d.fSUM ELSE 0 END) as Credit
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
    
    cursor.execute(query2, (manager_id,) + tuple(assigned_groups))
    row = cursor.fetchone()
    debit = float(row.Debit or 0)
    credit = float(row.Credit or 0)
    net_debt = debit - credit
    
    print(f"\nДебет (D): {debit:,.2f} AMD")
    print(f"Кредит (C): {credit:,.2f} AMD")
    print(f"Чистый долг (D - C): {net_debt:,.2f} AMD")
    print(f"Разница: {abs(net_debt - expected):,.2f} AMD ({abs(net_debt - expected) / expected * 100:.2f}%)")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 3: С учетом Type01/Type02 вычитания")
    print("=" * 80)
    
    print("\n" + "=" * 80)
    print("ТЕСТ 4: С учетом HIRESTCUSTOMERSSUM")
    print("=" * 80)
    
    query3 = f"""
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
    
    cursor.execute(query3, (manager_id,) + tuple(assigned_groups))
    row = cursor.fetchone()
    type01 = float(row.Type01 or 0)
    type02 = float(row.Type02 or 0)
    
    # Вариант 1: net_debt - |Type01| - |Type02| (правильная формула)
    variant1 = net_debt - abs(type01) - abs(type02)
    print(f"\nВариант 1 (net_debt - |Type01| - |Type02|):")
    print(f"  {net_debt:,.2f} - {abs(type01):,.2f} - {abs(type02):,.2f}")
    print(f"  = {variant1:,.2f} AMD")
    print(f"  Разница: {abs(variant1 - expected):,.2f} AMD ({abs(variant1 - expected) / expected * 100:.2f}%)")
    
    # Вариант 2: net_debt + Type01 + Type02 (без модуля)
    variant2 = net_debt + type01 + type02
    print(f"\nВариант 2 (net_debt + Type01 + Type02):")
    print(f"  {net_debt:,.2f} + {type01:,.2f} + {type02:,.2f}")
    print(f"  = {variant2:,.2f} AMD")
    print(f"  Разница: {abs(variant2 - expected):,.2f} AMD ({abs(variant2 - expected) / expected * 100:.2f}%)")
    
    # Найти лучший
    print("\n" + "=" * 80)
    print("ИТОГ")
    print("=" * 80)
    
    methods = [
        ("Только дебет (D)", debit_only),
        ("Чистый долг (D - C)", net_debt),
        ("net_debt - |Type01| - |Type02|", variant1),
        ("net_debt + Type01 + Type02", variant2),
    ]
    
    print(f"\nОжидаемый долг: {expected:,.2f} AMD\n")
    for name, value in sorted(methods, key=lambda x: abs(x[1] - expected)):
        diff = abs(value - expected)
        pct = diff / expected * 100
        status = "✅" if pct < 1 else "❌"
        print(f"{status} {name:<30}: {value:>15,.2f} AMD (откл. {pct:>6.2f}%)")
    
    conn.close()
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

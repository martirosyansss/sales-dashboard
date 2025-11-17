#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Детальная проверка долга территории 101 (Կենտրոն)
"""
import sys
import json
sys.path.insert(0, r'C:\Sales Dashboard')

try:
    print("Загрузка приложения...")
    from app_v2 import app, db, load_group_manager_assignments
    
    # Загрузка настроек групп
    assignments = load_group_manager_assignments()
    managers_with_groups = {}
    for group_code, manager_ids in assignments.items():
        if not isinstance(manager_ids, list):
            manager_ids = [manager_ids]
        for mgr_id in manager_ids:
            if mgr_id not in managers_with_groups:
                managers_with_groups[mgr_id] = []
            managers_with_groups[mgr_id].append(group_code)
    
    print(f"✓ Менеджеров с назначенными группами: {len(managers_with_groups)}")
    
    # Получить всех менеджеров территории 101
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT sa.fID, sa.fCODE, sa.fNAME
        FROM SALESAGENTS sa
        INNER JOIN SALESAGENTAREAS saa ON sa.fID = saa.fSALESAGENTID
        WHERE saa.fSALESAREA = '101'
            AND sa.fCLOSED = 0
        ORDER BY sa.fCODE
    """)
    
    managers_101 = []
    for row in cursor.fetchall():
        managers_101.append({
            'id': row.fID,
            'code': row.fCODE,
            'name': row.fNAME
        })
    
    print(f"\n✓ Менеджеров в территории 101: {len(managers_101)}")
    print("=" * 100)
    
    total_debt = 0
    
    for mgr in managers_101:
        mgr_id = mgr['id']
        responsible_groups = managers_with_groups.get(mgr_id, [])
        
        if not responsible_groups:
            print(f"{mgr['code']:10} | {mgr['name']:30} | НЕТ назначенных групп → Долг: 0.00")
            continue
        
        # Расчет долга для менеджера
        placeholders = ','.join(['?'] * len(responsible_groups))
        group_filter = f" AND c.fGROUP IN ({placeholders})"
        rest_group_filter = f" AND c2.fGROUP IN ({placeholders})"
        group_params = tuple(responsible_groups)
        
        debt_query = f"""
            SELECT 
                ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs,
                (
                    SELECT ISNULL(SUM(r.fSUM), 0)
                    FROM HIRESTCUSTOMERSSUM r
                    INNER JOIN CUSTOMERS c2 ON r.fCUSTOMERID = c2.fID
                    WHERE r.fCUSTOMERID IN (
                        SELECT DISTINCT doc2.fCUSTOMERID
                        FROM DOCUMENTS doc2
                        WHERE doc2.fSALESAGENTID = ?
                    )
                    AND r.fTYPE = '02'
                    {rest_group_filter}
                ) as RestType01,
                (
                    SELECT ISNULL(SUM(r.fSUM), 0)
                    FROM HIRESTCUSTOMERSSUM r
                    INNER JOIN CUSTOMERS c2 ON r.fCUSTOMERID = c2.fID
                    WHERE r.fCUSTOMERID IN (
                        SELECT DISTINCT doc2.fCUSTOMERID
                        FROM DOCUMENTS doc2
                        WHERE doc2.fSALESAGENTID = ?
                    )
                    AND r.fTYPE = '01'
                    {rest_group_filter}
                ) as RestType02
            FROM HICUSTOMERSDEBT d
            INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
            INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
            WHERE doc.fSALESAGENTID = ?
                {group_filter}
        """
        
        all_params = (mgr_id,) + group_params + (mgr_id,) + group_params + (mgr_id,) + group_params
        cursor.execute(debt_query, all_params)
        debt_row = cursor.fetchone()
        
        if debt_row:
            debt_from_docs = float(debt_row.DebtFromDocs) if debt_row.DebtFromDocs else 0
            rest_type_01 = float(debt_row.RestType01) if debt_row.RestType01 else 0
            rest_type_02 = float(debt_row.RestType02) if debt_row.RestType02 else 0
            mgr_debt = debt_from_docs + rest_type_01 + rest_type_02
            total_debt += mgr_debt
            
            print(f"{mgr['code']:10} | {mgr['name']:30} | Группы: {','.join(responsible_groups):15} | Долг: {mgr_debt:15,.2f}")
        else:
            print(f"{mgr['code']:10} | {mgr['name']:30} | Группы: {','.join(responsible_groups):15} | Долг: 0.00")
    
    print("=" * 100)
    print(f"\n✓ ИТОГО долг территории 101 (Կենտրոն):")
    print(f"  Фактический: {total_debt:,.2f} AMD")
    print(f"  Ожидаемый:   6,051,194.09 AMD")
    print(f"  Разница:     {total_debt - 6051194.09:,.2f} AMD")
    
    conn.close()
    
except Exception as e:
    print(f"✗ Ошибка: {e}")
    import traceback
    traceback.print_exc()

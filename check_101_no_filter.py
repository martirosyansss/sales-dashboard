#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Проверка долга территории 101 БЕЗ фильтра по группам (все группы)
"""
import sys
sys.path.insert(0, r'C:\Sales Dashboard')

try:
    from app_v2 import db
    
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # Получить всех менеджеров территории 101
    cursor.execute("""
        SELECT DISTINCT sa.fID, sa.fCODE, sa.fNAME
        FROM SALESAGENTS sa
        INNER JOIN SALESAGENTAREAS saa ON sa.fID = saa.fSALESAGENTID
        WHERE saa.fSALESAREA = '101'
            AND sa.fCLOSED = 0
        ORDER BY sa.fCODE
    """)
    
    managers = []
    for row in cursor.fetchall():
        managers.append({
            'id': row.fID,
            'code': row.fCODE,
            'name': row.fNAME
        })
    
    print(f"Долг территории 101 БЕЗ фильтра по группам (все группы клиентов):")
    print("=" * 100)
    
    total_debt_101 = 0
    
    for mgr in managers:
        manager_id = mgr['id']
        
        # Долг БЕЗ фильтра по группам
        debt_query = """
            SELECT 
                ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs,
                (
                    SELECT ISNULL(SUM(r.fSUM), 0)
                    FROM HIRESTCUSTOMERSSUM r
                    WHERE r.fCUSTOMERID IN (
                        SELECT DISTINCT doc2.fCUSTOMERID
                        FROM DOCUMENTS doc2
                        WHERE doc2.fSALESAGENTID = ?
                    )
                    AND r.fTYPE = '02'
                ) as RestType01,
                (
                    SELECT ISNULL(SUM(r.fSUM), 0)
                    FROM HIRESTCUSTOMERSSUM r
                    WHERE r.fCUSTOMERID IN (
                        SELECT DISTINCT doc2.fCUSTOMERID
                        FROM DOCUMENTS doc2
                        WHERE doc2.fSALESAGENTID = ?
                    )
                    AND r.fTYPE = '01'
                ) as RestType02
            FROM HICUSTOMERSDEBT d
            INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
            WHERE doc.fSALESAGENTID = ?
        """
        
        cursor.execute(debt_query, (manager_id, manager_id, manager_id))
        debt_row = cursor.fetchone()
        
        if debt_row:
            debt_from_docs = float(debt_row.DebtFromDocs) if debt_row.DebtFromDocs else 0
            rest_type_01 = float(debt_row.RestType01) if debt_row.RestType01 else 0
            rest_type_02 = float(debt_row.RestType02) if debt_row.RestType02 else 0
            mgr_debt = debt_from_docs + rest_type_01 + rest_type_02
            
            if abs(mgr_debt) > 100:
                total_debt_101 += mgr_debt
                print(f"{mgr['code']:10} (ID:{manager_id:5}) | {mgr['name']:30} | Долг: {mgr_debt:15,.2f}")
    
    print("=" * 100)
    print(f"\n✓ ИТОГО долг территории 101 (БЕЗ фильтра по группам): {total_debt_101:,.2f} AMD")
    print(f"✓ Ожидается:                                          6,051,194.09 AMD")
    print(f"✓ Разница:                                            {total_debt_101 - 6051194.09:,.2f} AMD")
    print(f"✓ Точность:                                           {total_debt_101/6051194.09*100:.2f}%")
    
    conn.close()
    
except Exception as e:
    print(f"Ошибка: {e}")
    import traceback
    traceback.print_exc()

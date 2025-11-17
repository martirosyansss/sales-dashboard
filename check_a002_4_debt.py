#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Проверка долга менеджера A002/4 (ID: 3128) для групп 002, 036
"""
import sys
sys.path.insert(0, r'C:\Sales Dashboard')

try:
    from app_v2 import db
    
    conn = db.get_connection()
    cursor = conn.cursor()
    
    manager_id = 3128
    responsible_groups = ['002', '036']
    
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
    
    all_params = (manager_id,) + group_params + (manager_id,) + group_params + (manager_id,) + group_params
    cursor.execute(debt_query, all_params)
    debt_row = cursor.fetchone()
    
    if debt_row:
        debt_from_docs = float(debt_row.DebtFromDocs) if debt_row.DebtFromDocs else 0
        rest_type_01 = float(debt_row.RestType01) if debt_row.RestType01 else 0
        rest_type_02 = float(debt_row.RestType02) if debt_row.RestType02 else 0
        total_debt = debt_from_docs + rest_type_01 + rest_type_02
        
        print(f"Менеджер A002/4 (ID: {manager_id})")
        print(f"Группы: {', '.join(responsible_groups)}")
        print("=" * 60)
        print(f"DebtFromDocs:  {debt_from_docs:15,.2f}")
        print(f"RestType01:    {rest_type_01:15,.2f}")
        print(f"RestType02:    {rest_type_02:15,.2f}")
        print("-" * 60)
        print(f"ИТОГО:         {total_debt:15,.2f}")
        print("=" * 60)
        
        # Сравнение с A001/4
        print(f"\nA001/4 долг:   5,077,702.79")
        print(f"A002/4 долг:   {total_debt:,.2f}")
        print(f"ИТОГО (101):   {5077702.79 + total_debt:,.2f}")
        print(f"Ожидается:     6,051,194.09")
        print(f"Разница:       {(5077702.79 + total_debt) - 6051194.09:,.2f}")
    else:
        print("Нет данных о долге")
    
    conn.close()
    
except Exception as e:
    print(f"Ошибка: {e}")
    import traceback
    traceback.print_exc()

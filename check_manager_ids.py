#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Проверка ID менеджеров территории 101
"""
import sys
sys.path.insert(0, r'C:\Sales Dashboard')

try:
    from app_v2 import db
    
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
    
    print("Менеджеры территории 101:")
    print("=" * 80)
    for row in cursor.fetchall():
        print(f"ID: {row.fID:5} | Код: {row.fCODE:10} | Имя: {row.fNAME}")
    print("=" * 80)
    
    # Проверить менеджеров с именем "Հակոբյան Արման"
    cursor.execute("""
        SELECT fID, fCODE, fNAME, fCLOSED
        FROM SALESAGENTS
        WHERE fNAME LIKE '%Հակոբյան Արման%'
        ORDER BY fCODE
    """)
    
    print("\nВсе менеджеры 'Հակոբյան Արման':")
    print("=" * 80)
    for row in cursor.fetchall():
        closed = "ЗАКРЫТ" if row.fCLOSED else "АКТИВЕН"
        print(f"ID: {row.fID:5} | Код: {row.fCODE:10} | {closed}")
    print("=" * 80)
    
    conn.close()
    
except Exception as e:
    print(f"Ошибка: {e}")
    import traceback
    traceback.print_exc()

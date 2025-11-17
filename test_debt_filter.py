#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Тест расчета долга с фильтром по группам
"""
import sys
sys.path.insert(0, r'C:\Sales Dashboard')

try:
    # Импорт модуля
    print("Импорт app_v2...")
    import app_v2
    print("✓ Импорт успешен")
    
    # Проверка функций
    print("\nПроверка функций:")
    print(f"✓ load_group_manager_assignments: {callable(app_v2.load_group_manager_assignments)}")
    print(f"✓ get_excluded_filter_sql: {callable(app_v2.get_excluded_filter_sql)}")
    print(f"✓ get_product_groups_filter_sql: {callable(app_v2.get_product_groups_filter_sql)}")
    
    # Загрузка настроек групп
    print("\nЗагрузка настроек групп...")
    assignments = app_v2.load_group_manager_assignments()
    print(f"✓ Загружено групп: {len(assignments)}")
    
    # Построение managers_with_groups
    managers_with_groups = {}
    for group_code, manager_ids in assignments.items():
        if not isinstance(manager_ids, list):
            manager_ids = [manager_ids]
        for mgr_id in manager_ids:
            if mgr_id not in managers_with_groups:
                managers_with_groups[mgr_id] = []
            managers_with_groups[mgr_id].append(group_code)
    
    print(f"✓ Менеджеров с назначенными группами: {len(managers_with_groups)}")
    
    # Проверка менеджера 3152 (A006/6)
    if 3152 in managers_with_groups:
        print(f"\n✓ Менеджер 3152 (A006/6) имеет группы: {managers_with_groups[3152]}")
    else:
        print(f"\n✗ Менеджер 3152 (A006/6) НЕ найден в настройках")
    
    print("\n✓ Все проверки пройдены!")
    
except Exception as e:
    print(f"\n✗ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Тест endpoint /api/sales-areas с фильтром по группам
"""
import sys
import json
sys.path.insert(0, r'C:\Sales Dashboard')

try:
    print("Загрузка приложения...")
    from app_v2 import app
    
    with app.test_client() as client:
        print("Запрос к /api/sales-areas...")
        response = client.get('/api/sales-areas?date_from=2025-10-31&date_to=2025-11-14')
        
        if response.status_code == 200:
            data = json.loads(response.data)
            
            if data.get('success'):
                areas = data.get('data', [])
                print(f"✓ Получено территорий: {len(areas)}")
                
                # Топ-5 по продажам
                print(f"\nТоп-5 территорий по продажам:")
                print("=" * 120)
                for i, area in enumerate(areas[:5], 1):
                    managers_count = len(area.get('Managers', []))
                    print(f"{i}. {area.get('code'):4} - {area.get('name'):30} | Продажи: {area.get('TotalSales', 0):12,.0f} | Долг: {area.get('Debt', 0):15,.2f} | Менеджеров: {managers_count}")
                print("=" * 120)
                
                # Найти территорию 106 (Армавир) - там работает A006/6
                area_106 = next((a for a in areas if a.get('code') == '106'), None)
                if area_106:
                    print(f"\n✓ Территория 106 (Армавир):")
                    print(f"  Продажи: {area_106.get('TotalSales', 0):,.2f} AMD")
                    print(f"  Долг: {area_106.get('Debt', 0):,.2f} AMD (только для менеджеров с назначенными группами)")
                    print(f"  Менеджеров: {len(area_106.get('Managers', []))}")
                    
                    print("\n  Менеджеры территории 106:")
                    for mgr in area_106.get('Managers', []):
                        print(f"    - {mgr.get('code')}: {mgr.get('name')} ({mgr.get('role')})")
                
                # Общая статистика
                total_debt = sum(a.get('Debt', 0) for a in areas)
                total_sales = sum(a.get('TotalSales', 0) for a in areas)
                print(f"\n✓ ИТОГО по всем территориям:")
                print(f"  Продажи: {total_sales:,.2f} AMD")
                print(f"  Долг: {total_debt:,.2f} AMD (только для назначенных групп)")
                    
            else:
                print(f"✗ API вернул ошибку: {data.get('error')}")
        else:
            print(f"✗ Ошибка HTTP: {response.status_code}")
            
except Exception as e:
    print(f"✗ Ошибка: {e}")
    import traceback
    traceback.print_exc()

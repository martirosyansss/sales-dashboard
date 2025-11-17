#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Прямой тест endpoint /api/managers без requests
"""
import sys
import json
sys.path.insert(0, r'C:\Sales Dashboard')

try:
    print("Загрузка приложения...")
    from app_v2 import app, db
    
    # Создание тестового клиента Flask
    with app.test_client() as client:
        print("Запрос к /api/managers...")
        response = client.get('/api/managers?date_from=2025-10-31&date_to=2025-11-14')
        
        if response.status_code == 200:
            data = json.loads(response.data)
            
            if data.get('success'):
                managers = data.get('data', [])
                print(f"✓ Получено менеджеров: {len(managers)}")
                
                # Найти всех менеджеров A006*
                a006_managers = [m for m in managers if m.get('fCODE', '').startswith('A006')]
                
                print(f"\nМенеджеры A006*:")
                print("=" * 100)
                for m in a006_managers:
                    debt = m.get('Debt', 0)
                    print(f"Код: {m.get('fCODE'):10} | Имя: {m.get('fNAME'):30} | Продажи: {m.get('TotalSales', 0):12,.2f} | Долг: {debt:15,.2f}")
                print("=" * 100)
                
                # Проверка менеджера A006/6 (ID 3152)
                a006_6 = next((m for m in managers if m.get('fCODE') == 'A006/6'), None)
                if a006_6:
                    print(f"\n✓ Менеджер A006/6 (ID={a006_6.get('fID')}):")
                    print(f"  Долг: {a006_6.get('Debt', 0):,.2f} AMD")
                    print(f"  Ожидаемый долг: 6,012,374.25 AMD (для групп 002, 036)")
                    
                    expected = 6012374.25
                    actual = a006_6.get('Debt', 0)
                    diff = actual - expected
                    diff_pct = (diff / expected * 100) if expected else 0
                    print(f"  Разница: {diff:,.2f} AMD ({diff_pct:.2f}%)")
                else:
                    print("\n✗ Менеджер A006/6 не найден")
                
                # Показать менеджеров БЕЗ долга (нет назначенных групп)
                no_debt = [m for m in managers if m.get('Debt', 0) == 0]
                print(f"\n✓ Менеджеров БЕЗ долга (нет назначенных групп): {len(no_debt)}")
                if no_debt:
                    print("Первые 10:")
                    for m in no_debt[:10]:
                        print(f"  {m.get('fCODE')}: {m.get('fNAME')}")
                    
            else:
                print(f"✗ API вернул ошибку: {data.get('error')}")
        else:
            print(f"✗ Ошибка HTTP: {response.status_code}")
            print(f"Response: {response.data}")
            
except Exception as e:
    print(f"✗ Ошибка: {e}")
    import traceback
    traceback.print_exc()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Тест API /api/managers для проверки долга с фильтром по группам
"""
import requests
import json

try:
    print("Запрос к API /api/managers...")
    url = "http://localhost:5000/api/managers?date_from=2025-10-31&date_to=2025-11-14"
    
    response = requests.get(url, timeout=30)
    
    if response.status_code == 200:
        data = response.json()
        
        if data.get('success'):
            managers = data.get('data', [])
            print(f"✓ Получено менеджеров: {len(managers)}")
            
            # Найти всех менеджеров A006*
            a006_managers = [m for m in managers if m.get('fCODE', '').startswith('A006')]
            
            print(f"\nМенеджеры A006*:")
            print("-" * 80)
            for m in a006_managers:
                print(f"Код: {m.get('fCODE')}")
                print(f"Имя: {m.get('fNAME')}")
                print(f"Продажи: {m.get('TotalSales', 0):,.2f}")
                print(f"Долг: {m.get('Debt', 0):,.2f}")
                print("-" * 80)
            
            # Проверка менеджера A006/6 (ID 3152)
            a006_6 = next((m for m in managers if m.get('fCODE') == 'A006/6'), None)
            if a006_6:
                print(f"\n✓ Менеджер A006/6 найден:")
                print(f"  ID: {a006_6.get('fID')}")
                print(f"  Долг: {a006_6.get('Debt', 0):,.2f} AMD")
                print(f"  Ожидаемый долг: 6,012,374.25 AMD (для групп 002, 036)")
            else:
                print("\n✗ Менеджер A006/6 не найден")
                
        else:
            print(f"✗ API вернул ошибку: {data.get('error')}")
    else:
        print(f"✗ Ошибка HTTP: {response.status_code}")
        print(f"Response: {response.text}")
        
except requests.exceptions.ConnectionError:
    print("✗ Не удается подключиться к серверу. Убедитесь, что сервер запущен.")
except Exception as e:
    print(f"✗ Ошибка: {e}")
    import traceback
    traceback.print_exc()

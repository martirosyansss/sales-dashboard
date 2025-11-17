"""
Простая проверка долга менеджера A003 через API
"""

import requests
import time
import subprocess
import os

# Запустить сервер
print("Запуск сервера...")
process = subprocess.Popen(
    ["python.exe", "app_v2.py"],
    cwd=r"C:\Sales Dashboard",
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

# Подождать запуска
time.sleep(5)

try:
    # Проверить API
    print("\nПроверка API...")
    url = "http://localhost:5000/api/managers?date_from=2025-11-01&date_to=2025-11-30"
    response = requests.get(url, timeout=30)
    
    if response.status_code == 200:
        data = response.json()
        if data.get('success'):
            managers = data.get('data', [])
            a003 = [m for m in managers if m['fCODE'] == 'A003']
            
            if a003:
                manager = a003[0]
                print(f"\n✅ Найден менеджер A003:")
                print(f"   Имя: {manager['fNAME']}")
                print(f"   Продажи: {manager['TotalSales']:,.2f} AMD")
                print(f"   Долг: {manager['Debt']:,.2f} AMD")
                print(f"   Клиенты: {manager['CustomerCount']}")
                
                expected = 5289036.77
                diff = abs(manager['Debt'] - expected)
                pct = diff / expected * 100
                
                print(f"\n   Ожидаемый долг: {expected:,.2f} AMD")
                print(f"   Разница: {diff:,.2f} AMD ({pct:.2f}%)")
                
                if pct < 1:
                    print("\n   ✅ ОТЛИЧНО! Отклонение менее 1%")
                else:
                    print(f"\n   ❌ Отклонение {pct:.2f}%")
            else:
                print("❌ Менеджер A003 не найден")
        else:
            print(f"❌ API вернул ошибку: {data.get('error')}")
    else:
        print(f"❌ Ошибка HTTP: {response.status_code}")

except Exception as e:
    print(f"❌ Ошибка: {e}")

finally:
    # Остановить сервер
    print("\nОстановка сервера...")
    process.terminate()
    process.wait(timeout=5)
    print("Сервер остановлен")

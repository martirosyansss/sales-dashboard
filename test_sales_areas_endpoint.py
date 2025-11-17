import sys
sys.path.insert(0, 'c:\\Sales Dashboard')

from app_v2 import app
import json

# Тестовый запрос к новому endpoint
with app.test_client() as client:
    print("=" * 80)
    print("ТЕСТ НОВОГО ENDPOINT /api/sales-areas")
    print("=" * 80)
    
    # Запрос без параметров (текущий месяц)
    response = client.get('/api/sales-areas')
    data = json.loads(response.data)
    
    if data['success']:
        areas = data['data']
        print(f"\nНайдено территорий: {len(areas)}")
        print(f"\n{'Код':<10} {'Название':<30} {'Продажи':>15} {'Долг':>15} {'Менеджеров':>12}")
        print("-" * 90)
        
        total_sales = 0
        total_debt = 0
        
        for area in areas[:10]:  # Топ 10
            print(f"{area['code']:<10} {area['name']:<30} {area['TotalSales']:>15,.0f} {area['Debt']:>15,.0f} {len(area.get('Managers', [])):>12}")
            total_sales += area['TotalSales']
            total_debt += area['Debt']
        
        if len(areas) > 10:
            print("...")
        
        print("-" * 90)
        print(f"{'ИТОГО (топ 10)':<41} {total_sales:>15,.0f} {total_debt:>15,.0f}")
        
        # Показать менеджеров для первой территории
        if areas and areas[0].get('Managers'):
            print(f"\nМенеджеры территории {areas[0]['code']} ({areas[0]['name']}):")
            for mgr in areas[0]['Managers'][:5]:
                default_mark = " (основной)" if mgr['is_default'] else ""
                print(f"  - {mgr['code']}: {mgr['name']}{default_mark}")
        
        print("\n✓ Endpoint работает успешно!")
    else:
        print(f"\n✗ Ошибка: {data.get('error')}")

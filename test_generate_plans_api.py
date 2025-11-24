import requests
import json

print("=" * 80)
print("ПРОВЕРКА /api/generate-plans")
print("=" * 80)

# Test the API
response = requests.get('http://localhost:5000/api/generate-plans')
data = response.json()

if data.get('success'):
    print(f"\nНайдено территорий: {len(data['data'])}")
    print()
    
    # Show first 3 territories
    for area_code in ['101', '102', '103']:
        if area_code in data['data']:
            area_data = data['data'][area_code]
            avg_sales = area_data.get('avg_sales', 0)
            avg_credit = area_data.get('avg_credit', 0)
            
            print(f"Территория {area_code}:")
            print(f"  Средние продажи за 12 мес: {avg_sales:,.2f}")
            print(f"  Средние кредиты за 12 мес: {avg_credit:,.2f}")
            print()
else:
    print(f"Ошибка: {data.get('error')}")

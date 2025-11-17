import urllib.request
import json

# Клиент с кодом 10651 имеет ID 56883
customer_id = 56883
url = f'http://127.0.0.1:5000/api/customers/{customer_id}/purchases?date_from=2025-11-01&date_to=2025-11-30'

print(f"Запрос к API: {url}")
print("="*80)

try:
    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.loads(response.read().decode())
        
    print(f"Статус: {response.status}")
    print(f"Success: {data.get('success')}")
    print(f"Количество покупок: {data.get('summary', {}).get('count')}")
    print(f"Общая сумма: {data.get('summary', {}).get('total_sales')}")
    print("="*80)
    
    purchases = data.get('data', [])
    if purchases:
        print(f"\nПервые 3 покупки:")
        for i, purchase in enumerate(purchases[:3], 1):
            print(f"\n{i}. Дата: {purchase.get('SaleDate')}")
            print(f"   Сумма: {purchase.get('TotalSum')}")
            print(f"   Документ: {purchase.get('DocNumber')}")
            products = purchase.get('Products', [])
            print(f"   Товаров: {len(products)}")
            if products:
                for prod in products[:2]:
                    print(f"      - {prod.get('ProductCode')}: {prod.get('ProductName')}")
    else:
        print("\nПокупки отсутствуют в ответе!")
        print(f"\nПолный ответ:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        
except Exception as e:
    print(f"Ошибка: {e}")
    import traceback
    traceback.print_exc()

import requests
import json

# Тестируем API для клиента 67529
customer_id = 67529
url = f'http://127.0.0.1:5000/api/customers/{customer_id}/purchases'

params = {
    'date_from': '2025-11-01',
    'date_to': '2025-11-30'
}

print(f"Запрос к API: {url}")
print(f"Параметры: {params}")
print("="*80)

try:
    response = requests.get(url, params=params)
    print(f"Статус: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type')}")
    print("="*80)
    
    if response.status_code == 200:
        data = response.json()
        print(f"Success: {data.get('success')}")
        print(f"Количество покупок: {data.get('summary', {}).get('count')}")
        print(f"Общая сумма: {data.get('summary', {}).get('total_sales')}")
        print("="*80)
        
        purchases = data.get('data', [])
        if purchases:
            print(f"\nПервая покупка:")
            first = purchases[0]
            print(json.dumps(first, indent=2, ensure_ascii=False))
        else:
            print("\nПокупки отсутствуют в ответе!")
    else:
        print(f"Ошибка: {response.text}")
        
except Exception as e:
    print(f"Исключение: {e}")

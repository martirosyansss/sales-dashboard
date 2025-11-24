import requests

# Проверяем кредиты за октябрь 2025 через API /api/sales-areas
params = {
    'date_from': '2025-10-01',
    'date_to': '2025-10-31'
}

response = requests.get('http://127.0.0.1:5000/api/sales-areas', params=params, timeout=10)
data = response.json()

if data.get('success'):
    areas = data.get('data', [])
    
    # Найти территорию 106
    area106 = None
    for area in areas:
        if area.get('code') == '106':
            area106 = area
            break
    
    if area106:
        print("ТЕРРИТОРИЯ 106 - ОКТЯБРЬ 2025:")
        print(f"  Всего продаж:  {area106.get('TotalSales', 0):,.2f} AMD")
        print(f"  Кредитов:      {area106.get('CreditSales', 0):,.2f} AMD")
        print(f"  Клиентов:      {area106.get('CustomerCount', 0)}")
    else:
        print("Территория 106 не найдена!")
        print(f"Доступные территории: {[a.get('code') for a in areas]}")
else:
    print(f"API Error: {data.get('error')}")

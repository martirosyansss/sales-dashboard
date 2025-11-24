import requests
import json

# Запрос к API
url = "http://127.0.0.1:5000/api/sales-areas?date_from=2025-10-01&date_to=2025-10-30&groups=036,002"
response = requests.get(url, timeout=60)

if response.status_code == 200:
    data = response.json()
    
    # Найти Area 105
    area_105 = None
    for area in data.get('data', []):
        if area.get('code') == '105':
            area_105 = area
            break
    
    if area_105:
        print("\n=== API RESPONSE FOR AREA 105 ===")
        print(f"Code: {area_105.get('code')}")
        print(f"Name: {area_105.get('name')}")
        print(f"Debt: {area_105.get('Debt'):,.2f} AMD")
        print(f"TotalSales: {area_105.get('TotalSales', 0):,.2f} AMD")
        print(f"Payments: {area_105.get('Payments', 0):,.2f} AMD")
        
        print(f"\n=== EXPECTED ===")
        print(f"Debt should be: 2,409,287.66 AMD")
        print(f"Currently showing: 1,696,049.41 AMD")
        print(f"\nДолжен быть: 2,593,250.47 - 75,317.95 - 108,644.86 = 2,409,287.66")
    else:
        print("Area 105 not found in response")
else:
    print(f"Error: {response.status_code}")

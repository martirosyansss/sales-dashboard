import requests

try:
    response = requests.get('http://127.0.0.1:5000/api/generate-plans', timeout=10)
    data = response.json()
    
    if data.get('success'):
        print(f"API SUCCESS - Нашли {len(data.get('data', {}))} территорий")
        
        if '106' in data.get('data', {}):
            area106 = data['data']['106']
            print(f"\n✓ Территория 106 НАЙДЕНА:")
            print(f"  avg_sales:  {area106.get('avg_sales', 0):,.2f} AMD")
            print(f"  avg_credit: {area106.get('avg_credit', 0):,.2f} AMD")
        else:
            print("\n✗ Территория 106 НЕ НАЙДЕНА!")
            print(f"Доступные территории: {list(data.get('data', {}).keys())}")
    else:
        print(f"✗ API ERROR: {data.get('error')}")
        
except Exception as e:
    print(f"✗ EXCEPTION: {e}")

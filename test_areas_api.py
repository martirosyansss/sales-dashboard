import requests
import json
from datetime import datetime, timedelta

# Тестирование API /api/sales-areas
def test_areas_api():
    try:
        today = datetime.now()
        date_from = (today - timedelta(days=30)).strftime('%Y-%m-%d')
        date_to = today.strftime('%Y-%m-%d')
        
        url = f"http://localhost:5000/api/sales-areas?date_from={date_from}&date_to={date_to}"
        
        print(f"Testing URL: {url}")
        print("=" * 80)
        
        response = requests.get(url, timeout=60)
        
        print(f"Status code: {response.status_code}")
        print(f"Response headers: {response.headers}")
        print("=" * 80)
        
        if response.status_code == 200:
            data = response.json()
            print(f"Success: {data.get('success')}")
            print(f"Total areas: {len(data.get('data', []))}")
            
            if data.get('data'):
                print("\nFirst area:")
                print(json.dumps(data['data'][0], indent=2, ensure_ascii=False))
        else:
            print(f"Error: {response.text}")
            
    except Exception as e:
        print(f"Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_areas_api()

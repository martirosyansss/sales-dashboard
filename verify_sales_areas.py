import requests
import json

def verify_sales_areas():
    print("Testing /api/sales-areas...")
    try:
        response = requests.get('http://localhost:5000/api/sales-areas')
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Success: {data.get('success')}")
            if data.get('success'):
                areas = data.get('data', [])
                print(f"Found {len(areas)} areas.")
                if len(areas) > 0:
                    print(f"Sample area: {areas[0]['code']} - {areas[0]['name']}")
                else:
                    print("WARNING: No areas returned!")
            else:
                print(f"Error in response: {data.get('error')}")
        else:
            print(f"Request failed: {response.text}")
            
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    verify_sales_areas()

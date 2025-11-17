import requests
import json

# Test the purchases endpoint with a known customer
customer_id = 465  # From previous tests
url = f'http://localhost:5000/api/customers/{customer_id}/purchases'

response = requests.get(url)
print(f"Status: {response.status_code}")
print(f"\nResponse:")
data = response.json()
print(json.dumps(data, indent=2, ensure_ascii=False))

if data.get('success') and data.get('data'):
    print(f"\n=== Summary ===")
    print(f"Total purchases: {len(data['data'])}")
    if data['data']:
        first_purchase = data['data'][0]
        print(f"First purchase:")
        print(f"  Date: {first_purchase['SaleDate']}")
        print(f"  Payment: {first_purchase['PaymentType']}")
        print(f"  Sum: {first_purchase['TotalSum']} AMD")
        print(f"  Products: {len(first_purchase['Products'])}")

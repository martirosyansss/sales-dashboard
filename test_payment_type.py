import urllib.request
import json

url = 'http://localhost:5000/api/customers/465/purchases?date_from=2025-11-01&date_to=2025-11-30'
response = urllib.request.urlopen(url)
data = json.loads(response.read())

print(f"Success: {data.get('success')}")
print(f"Total purchases: {len(data.get('data', []))}")

if data.get('data'):
    purchase = data['data'][0]
    print(f"\nFirst purchase:")
    print(f"  Date: {purchase['SaleDate']}")
    print(f"  Payment Type: {purchase['PaymentType']}")
    print(f"  Total: {purchase['TotalSum']}")
    print(f"  Products: {len(purchase['Products'])}")

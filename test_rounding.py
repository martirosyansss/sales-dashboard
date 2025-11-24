import requests

response = requests.get('http://127.0.0.1:5000/api/generate-plans?month=11&year=2025')
data = response.json()

credit = data['data']['105']['credit']
sales = data['data']['105']['sales']

print(f"\nТерритория 105:")
print(f"  Кредит: {credit:,}")
print(f"  Продажи: {sales:,}")
print(f"\nПроверка округления:")
print(f"  Кредит % 10000 = {credit % 10000}")
print(f"  Продажи % 10000 = {sales % 10000}")

if credit % 10000 == 0 and sales % 10000 == 0:
    print("\n✓ Округление работает!")
else:
    print("\n✗ Округление НЕ работает")

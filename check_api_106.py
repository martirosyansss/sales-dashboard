import requests

response = requests.get('http://localhost:5000/api/generate-plans')
data = response.json()

if data.get('success') and '106' in data['data']:
    area106 = data['data']['106']
    print("API /api/generate-plans returns for territory 106:")
    print(f"  avg_sales:  {area106.get('avg_sales', 0):,.2f}")
    print(f"  avg_credit: {area106.get('avg_credit', 0):,.2f}")
else:
    print("Error or territory 106 not found")

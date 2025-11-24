import requests
import json

response = requests.get('http://localhost:5000/api/generate-plans')
data = response.json()

if data.get('success'):
    if '106' in data['data']:
        area106 = data['data']['106']
        avg_credit = area106.get('avg_credit', 0)
        avg_sales = area106.get('avg_sales', 0)
        
        print("API RETURNS FOR TERRITORY 106:")
        print(f"  avg_sales:  {avg_sales:,.2f} AMD")
        print(f"  avg_credit: {avg_credit:,.2f} AMD")
        print()
        
        # Expected after fix (without salesarea filter)
        expected_credit = 2049002.31  # From test_salesarea_match.py "WITHOUT filter"
        
        if abs(avg_credit - expected_credit) < 1000:
            print("✓ FIX VERIFIED! API returns correct value (without salesarea filter)")
        else:
            print(f"✗ Still using old value. Expected ~{expected_credit:,.2f}")
    else:
        print("Territory 106 not found in response")
else:
    print(f"API error: {data.get('error')}")

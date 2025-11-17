"""
Quick API test for SA 103 with include_zero_sales
"""
import urllib.request
import urllib.parse
import json

base_url = "http://localhost:5000/api/customers"
params = {
    'sales_area': '103',
    'date_from': '2024-11-16',
    'date_to': '2025-11-17',
    'include_zero_sales': '1',
    'groups': '002,036'
}

try:
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as response:
        data = json.loads(response.read().decode())
    
    print(f"\n{'='*80}")
    print(f"API TEST: SA 103 with include_zero_sales=1")
    print(f"{'='*80}\n")
    
    print(f"Total customers returned: {len(data)}")
    
    if len(data) > 0:
        print(f"\nFirst 5 customers:")
        print(f"{'Code':<12} {'Name':<40} {'Group':<8} {'Debt':>15}")
        print(f"{'-'*80}")
        for customer in data[:5]:
            print(f"{customer['CustomerCode']:<12} {customer['CustomerName']:<40} {customer['GroupCode']:<8} {customer['Debt']:>15,.2f}")
    
    print(f"\n{'='*80}")
    print(f"Expected: 122 customers (from database query)")
    print(f"Actual: {len(data)} customers (from API)")
    if len(data) == 122:
        print(f"✓ SUCCESS: Matches database query!")
    else:
        print(f"✗ MISMATCH: Check query logic")
    print(f"{'='*80}\n")
    
except Exception as e:
    print(f"ERROR: {e}")

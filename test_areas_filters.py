"""
Test script to verify /areas filters functionality
"""
import json
from urllib.request import urlopen
from urllib.parse import urlencode
from datetime import datetime, timedelta

BASE_URL = "http://localhost:5000"

def test_areas_filters():
    # Date range
    today = datetime.now()
    date_from = (today - timedelta(days=18)).strftime('%Y-%m-%d')
    date_to = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    
    print("=" * 60)
    print("Testing /api/sales-areas filters")
    print("=" * 60)
    
    # Test 1: No filters
    print("\n1. No filters:")
    params = urlencode({'date_from': date_from, 'date_to': date_to})
    with urlopen(f"{BASE_URL}/api/sales-areas?{params}") as resp:
        data = json.loads(resp.read())
    if data['success']:
        area101 = next((a for a in data['data'] if a['code'] == '101'), None)
        print(f"   Areas: {len(data['data'])}")
        if area101:
            print(f"   Area 101: Sales={area101['TotalSales']:.2f}, Customers={area101['CustomerCount']}, Debt={area101['Debt']:.2f}")
    
    # Test 2: Groups filter
    print("\n2. With groups filter (002,036):")
    params = urlencode({'date_from': date_from, 'date_to': date_to, 'groups': '002,036'})
    with urlopen(f"{BASE_URL}/api/sales-areas?{params}") as resp:
        data = json.loads(resp.read())
    if data['success']:
        area101 = next((a for a in data['data'] if a['code'] == '101'), None)
        print(f"   Areas: {len(data['data'])}")
        if area101:
            print(f"   Area 101: Sales={area101['TotalSales']:.2f}, Customers={area101['CustomerCount']}, Debt={area101['Debt']:.2f}")
    
    # Test 3: Divisions filter
    print("\n3. With divisions filter (000000,000001):")
    params = urlencode({'date_from': date_from, 'date_to': date_to, 'divisions': '000000,000001'})
    with urlopen(f"{BASE_URL}/api/sales-areas?{params}") as resp:
        data = json.loads(resp.read())
    if data['success']:
        area101 = next((a for a in data['data'] if a['code'] == '101'), None)
        print(f"   Areas: {len(data['data'])}")
        if area101:
            print(f"   Area 101: Sales={area101['TotalSales']:.2f}, Customers={area101['CustomerCount']}, Debt={area101['Debt']:.2f}")
    
    # Test 4: Both filters
    print("\n4. With both filters (divisions + groups):")
    params = urlencode({'date_from': date_from, 'date_to': date_to, 'divisions': '000000', 'groups': '002'})
    with urlopen(f"{BASE_URL}/api/sales-areas?{params}") as resp:
        data = json.loads(resp.read())
    if data['success']:
        area101 = next((a for a in data['data'] if a['code'] == '101'), None)
        print(f"   Areas: {len(data['data'])}")
        if area101:
            print(f"   Area 101: Sales={area101['TotalSales']:.2f}, Customers={area101['CustomerCount']}, Debt={area101['Debt']:.2f}")
    
    print("\n" + "=" * 60)
    print("✓ All tests completed")
    print("=" * 60)

if __name__ == "__main__":
    test_areas_filters()

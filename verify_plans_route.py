import sys
import os
import json
from app_v2 import app

def test_plans_route():
    print("Testing /plans route...")
    with app.test_client() as client:
        response = client.get('/plans')
        if response.status_code == 200:
            print("✓ /plans route exists and returns 200")
        else:
            print(f"✗ /plans route failed with status {response.status_code}")
            return False
    return True

def test_groups_api():
    print("\nTesting /api/settings/groups...")
    with app.test_client() as client:
        response = client.get('/api/settings/groups')
        if response.status_code == 200:
            data = json.loads(response.data)
            if data.get('success'):
                print(f"✓ /api/settings/groups returned success. Found {len(data.get('data', []))} groups.")
                if len(data.get('data', [])) > 0:
                    print(f"  Sample group: {data['data'][0]}")
            else:
                print(f"✗ /api/settings/groups returned success=False: {data.get('error')}")
                return False
        else:
            print(f"✗ /api/settings/groups failed with status {response.status_code}")
            return False
    return True

def test_generate_plans_api():
    print("\nTesting /api/generate-plans...")
    with app.test_client() as client:
        # Test with default params
        response = client.get('/api/generate-plans?month=1&year=2025')
        if response.status_code == 200:
            data = json.loads(response.data)
            if data.get('success'):
                print(f"✓ /api/generate-plans returned success. Found data for {len(data.get('data', {}))} areas.")
                if len(data.get('data', {})) > 0:
                    first_area = list(data['data'].keys())[0]
                    print(f"  Sample area {first_area}: {data['data'][first_area]}")
            else:
                print(f"✗ /api/generate-plans returned success=False: {data.get('error')}")
                return False
        else:
            print(f"✗ /api/generate-plans failed with status {response.status_code}")
            return False
    return True

if __name__ == "__main__":
    print("Starting verification...")
    success = True
    success &= test_plans_route()
    success &= test_groups_api()
    success &= test_generate_plans_api()
    
    if success:
        print("\n✓ All tests passed!")
        sys.exit(0)
    else:
        print("\n✗ Some tests failed!")
        sys.exit(1)

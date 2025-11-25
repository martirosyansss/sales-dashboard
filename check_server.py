import requests
import sys

try:
    response = requests.get('http://localhost:5000/plans', timeout=5)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        print("✓ /plans page is accessible")
        print(f"Content length: {len(response.text)} bytes")
    else:
        print(f"✗ Unexpected status code: {response.status_code}")
except requests.exceptions.ConnectionError:
    print("✗ Server is not running or not accessible")
    print("Start the server with: python app_v2.py")
    sys.exit(1)
except Exception as e:
    print(f"✗ Error: {e}")
    sys.exit(1)

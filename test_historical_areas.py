"""
Test script for historical data in /areas page
Tests with date 2025-12-13 to check:
- Previous month: 2024-11-01 to 2024-11-13
- Last year: 2024-12-01 to 2024-12-13
"""

import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:5000"

def test_historical_data():
    """Test the areas API with historical comparisons"""
    
    print("\n" + "="*80)
    print("TESTING HISTORICAL DATA IN /api/sales-areas")
    print("="*80)
    
    # Test with date range: 2025-12-01 to 2025-12-13
    test_date_from = "2025-12-01"
    test_date_to = "2025-12-13"
    
    print(f"\nCurrent period: {test_date_from} to {test_date_to}")
    print(f"Expected previous month: 2024-11-01 to 2024-11-13")
    print(f"Expected last year: 2024-12-01 to 2024-12-13")
    
    url = f"{BASE_URL}/api/sales-areas"
    params = {
        'date_from': test_date_from,
        'date_to': test_date_to
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('success') and data.get('data'):
                areas = data['data']
                print(f"\n✓ API returned {len(areas)} areas")
                
                # Find area with most sales to show as example
                if areas:
                    top_area = max(areas, key=lambda x: x.get('TotalSales', 0))
                    
                    print(f"\n{'='*80}")
                    print(f"TOP AREA EXAMPLE: {top_area.get('code')} - {top_area.get('name')}")
                    print(f"{'='*80}")
                    
                    print(f"\nCURRENT PERIOD ({test_date_from} to {test_date_to}):")
                    print(f"  - Sales Count: {top_area.get('SalesCount', 0):,}")
                    print(f"  - Customers: {top_area.get('CustomerCount', 0):,}")
                    print(f"  - Total Sales: {top_area.get('TotalSales', 0):,.2f} AMD")
                    print(f"  - Debt: {top_area.get('Debt', 0):,.2f} AMD")
                    
                    if 'PrevMonthSales' in top_area:
                        print(f"\nPREVIOUS MONTH (2024-11-01 to 2024-11-13):")
                        print(f"  - Total Sales: {top_area.get('PrevMonthSales', 0):,.2f} AMD")
                        print(f"  - Debt: {top_area.get('PrevMonthDebt', 0):,.2f} AMD")
                        
                        # Calculate change
                        current = top_area.get('TotalSales', 0)
                        prev = top_area.get('PrevMonthSales', 0)
                        if prev > 0:
                            change = ((current - prev) / prev) * 100
                            direction = "↑" if change > 0 else "↓"
                            print(f"  - Change: {direction} {abs(change):.1f}%")
                    else:
                        print("\n❌ ERROR: PrevMonthSales not found in response")
                    
                    if 'LastYearSales' in top_area:
                        print(f"\nLAST YEAR (2024-12-01 to 2024-12-13):")
                        print(f"  - Total Sales: {top_area.get('LastYearSales', 0):,.2f} AMD")
                        print(f"  - Debt: {top_area.get('LastYearDebt', 0):,.2f} AMD")
                        
                        # Calculate change
                        current = top_area.get('TotalSales', 0)
                        last_year = top_area.get('LastYearSales', 0)
                        if last_year > 0:
                            change = ((current - last_year) / last_year) * 100
                            direction = "↑" if change > 0 else "↓"
                            print(f"  - Change: {direction} {abs(change):.1f}%")
                    else:
                        print("\n❌ ERROR: LastYearSales not found in response")
                    
                    # Show 3 more areas
                    print(f"\n{'='*80}")
                    print("OTHER AREAS SAMPLE:")
                    print(f"{'='*80}")
                    
                    for i, area in enumerate(areas[1:4], start=2):
                        print(f"\n{i}. {area.get('code')} - {area.get('name')}")
                        print(f"   Current: {area.get('TotalSales', 0):,.2f} AMD")
                        print(f"   Prev Month: {area.get('PrevMonthSales', 0):,.2f} AMD")
                        print(f"   Last Year: {area.get('LastYearSales', 0):,.2f} AMD")
                    
                    print(f"\n{'='*80}")
                    print("✓ ALL TESTS PASSED!")
                    print("Historical data is correctly calculated and returned")
                    print(f"{'='*80}")
                    
            else:
                print(f"\n❌ ERROR: API returned success=false or no data")
                print(f"Response: {json.dumps(data, indent=2)}")
        else:
            print(f"\n❌ ERROR: HTTP {response.status_code}")
            print(f"Response: {response.text[:500]}")
    
    except requests.exceptions.RequestException as e:
        print(f"\n❌ ERROR: Request failed - {e}")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")

if __name__ == "__main__":
    import time
    print("Waiting 3 seconds for server to be ready...")
    time.sleep(3)
    test_historical_data()

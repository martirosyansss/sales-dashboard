#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test sales area endpoint with group filtering
"""
import pyodbc
import json
from datetime import datetime

# Database connection
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)

cursor = conn.cursor()

# Load group assignments
with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
    group_manager_assignments = json.load(f)

# Create reverse mapping: manager_id -> [group_codes]
managers_with_groups = {}
for group_code, manager_ids in group_manager_assignments.items():
    for manager_id in manager_ids:
        if manager_id not in managers_with_groups:
            managers_with_groups[manager_id] = []
        managers_with_groups[manager_id].append(group_code)

print(f"Loaded group assignments for {len(managers_with_groups)} managers")
print(f"Example - Manager 3169 has groups: {managers_with_groups.get(3169, [])}")

# Test for territory 110 (includes A010/1)
territory_code = '110'
date_from = '2025-01-01'
date_to = '2025-11-15'

# Get managers for this territory
query_managers = """
    SELECT DISTINCT ag.fID, ag.fNAME, ag.fCODE
    FROM SALESAGENTS ag
    INNER JOIN SALESAGENTAREAS sa ON ag.fID = sa.fSALESAGENTID
    WHERE sa.fSALESAREA = ?
        AND ag.fCLOSED = 0
"""

cursor.execute(query_managers, (territory_code,))
managers = [(row.fID, row.fNAME, row.fCODE) for row in cursor.fetchall()]

print(f"\n🏢 Territory: {territory_code}")
print(f"👥 Managers: {len(managers)}")
for mgr_id, mgr_name, mgr_code in managers:
    groups = managers_with_groups.get(mgr_id, [])
    print(f"  - {mgr_code} {mgr_name} (ID: {mgr_id}) -> Groups: {groups}")

# Calculate sales with group filtering
total_sales = 0
customer_ids = set()
sales_count = 0

print(f"\n📊 Sales calculation (filtered by assigned groups):")

for mgr_id, mgr_name, mgr_code in managers:
    responsible_groups = managers_with_groups.get(mgr_id, [])
    
    if not responsible_groups:
        print(f"  ⚠️  {mgr_code}: No assigned groups - SKIPPED")
        continue
    
    # Query sales for this manager's assigned groups
    placeholders = ','.join(['?'] * len(responsible_groups))
    query_sales = f"""
        SELECT 
            s.fCUSTOMERID,
            s.fISN,
            s.fTOTALSUM,
            c.fGROUP
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        WHERE s.fSALESAGENTID = ?
            AND s.fDATE >= ?
            AND s.fDATE <= ?
            AND s.fSTATE = 2
            AND c.fGROUP IN ({placeholders})
    """
    
    params = (mgr_id, date_from, date_to) + tuple(responsible_groups)
    cursor.execute(query_sales, params)
    
    mgr_sales = 0
    mgr_customers = set()
    mgr_sales_count = 0
    
    for row in cursor.fetchall():
        customer_ids.add(row.fCUSTOMERID)
        mgr_customers.add(row.fCUSTOMERID)
        sales_count += 1
        mgr_sales_count += 1
        mgr_sales += float(row.fTOTALSUM)
        total_sales += float(row.fTOTALSUM)
    
    print(f"  ✅ {mgr_code}: {mgr_sales:,.2f} AMD ({mgr_sales_count} sales, {len(mgr_customers)} customers)")

print(f"\n📈 TOTAL SALES: {total_sales:,.2f} AMD")
print(f"👥 Unique customers: {len(customer_ids)}")
print(f"🛒 Total sales count: {sales_count}")

conn.close()
print("\n✅ Test complete!")

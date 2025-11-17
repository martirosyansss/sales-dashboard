import pyodbc

conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)

try:
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    
    print("=" * 80)
    print("Analyzing CUSTOMERSALESAREAS table")
    print("=" * 80)
    
    # 1. Table structure
    print("\n1. Table structure:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            COLUMN_NAME,
            DATA_TYPE,
            CHARACTER_MAXIMUM_LENGTH,
            IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'CUSTOMERSALESAREAS'
        ORDER BY ORDINAL_POSITION
    """)
    
    cols = cursor.fetchall()
    for col in cols:
        max_len = f"({col.CHARACTER_MAXIMUM_LENGTH})" if col.CHARACTER_MAXIMUM_LENGTH else ""
        print(f"  {col.COLUMN_NAME}: {col.DATA_TYPE}{max_len}, NULL={col.IS_NULLABLE}")
    
    # 2. Total records
    print("\n2. Total records:")
    print("-" * 80)
    
    cursor.execute("SELECT COUNT(*) as Total FROM CUSTOMERSALESAREAS")
    total = cursor.fetchone().Total
    print(f"  Total: {total:,} records")
    
    # 3. Count by Sales Area
    print("\n3. Count by Sales Area:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            fSALESAREA,
            COUNT(*) as CustomerCount
        FROM CUSTOMERSALESAREAS
        GROUP BY fSALESAREA
        ORDER BY fSALESAREA
    """)
    
    areas = cursor.fetchall()
    print(f"  {'Sales Area':<15} {'Customers':<12}")
    print(f"  {'-'*15} {'-'*12}")
    for area in areas:
        print(f"  {area.fSALESAREA:<15} {area.CustomerCount:<12,}")
    
    # 4. Check for groups 002 and 036 in SA 103
    print("\n4. Customers in Sales Area 103:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            c.fGROUP,
            COUNT(*) as CustomerCount
        FROM CUSTOMERSALESAREAS csa
        INNER JOIN CUSTOMERS c ON csa.fCUSTOMERID = c.fID
        WHERE csa.fSALESAREA = '103'
        AND c.fGROUP IN ('002', '036')
        GROUP BY c.fGROUP
        ORDER BY c.fGROUP
    """)
    
    groups = cursor.fetchall()
    total_sa103 = 0
    for group in groups:
        print(f"  Group {group.fGROUP}: {group.CustomerCount:,} customers")
        total_sa103 += group.CustomerCount
    print(f"  TOTAL: {total_sa103:,} customers")
    
    # 5. Compare with our current logic
    print("\n5. Comparison:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT COUNT(DISTINCT c.fID) as Count
        FROM CUSTOMERS c
        WHERE c.fGROUP IN ('002', '036')
    """)
    
    total_groups = cursor.fetchone().Count
    print(f"  Total customers in groups 002+036: {total_groups:,}")
    print(f"  Assigned to SA 103 (CUSTOMERSALESAREAS): {total_sa103:,}")
    print(f"  Using JSON file (sales_area_group_assignments): 8,280")
    
    # 6. Sample records
    print("\n6. Sample records from SA 103:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT TOP 10
            csa.fCUSTOMERID,
            c.fCODE,
            c.fNAME,
            c.fGROUP,
            csa.fSALESAREA
        FROM CUSTOMERSALESAREAS csa
        INNER JOIN CUSTOMERS c ON csa.fCUSTOMERID = c.fID
        WHERE csa.fSALESAREA = '103'
        AND c.fGROUP IN ('002', '036')
        ORDER BY c.fCODE
    """)
    
    samples = cursor.fetchall()
    print(f"  {'ID':<8} {'Code':<10} {'Group':<6} {'SA':<5} Name")
    print(f"  {'-'*8} {'-'*10} {'-'*6} {'-'*5} {'-'*40}")
    for row in samples:
        print(f"  {row.fCUSTOMERID:<8} {row.fCODE:<10} {row.fGROUP:<6} {row.fSALESAREA:<5} {row.fNAME[:40]}")
    
    cursor.close()
    conn.close()
    
    print("\n" + "=" * 80)
    print("[+] Analysis completed")
    print("=" * 80)
    
except Exception as e:
    print(f"\n[!] Error: {e}")
    import traceback
    traceback.print_exc()

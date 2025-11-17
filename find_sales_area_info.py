import pyodbc

# Database connection
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
    print("Searching for Sales Area information in database")
    print("=" * 80)
    
    # 1. Find all tables with "SALES" or "AREA"
    print("\n1. Tables containing 'SALES' or 'AREA':")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            TABLE_SCHEMA,
            TABLE_NAME,
            TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE (TABLE_NAME LIKE '%SALES%' OR TABLE_NAME LIKE '%AREA%')
        AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
    """)
    
    tables = cursor.fetchall()
    for row in tables:
        print(f"  {row.TABLE_SCHEMA}.{row.TABLE_NAME} ({row.TABLE_TYPE})")
    
    # 2. Check SALES table for fSALESAREA field
    print("\n2. SALES table structure:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            COLUMN_NAME,
            DATA_TYPE,
            CHARACTER_MAXIMUM_LENGTH,
            IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'SALES'
        AND COLUMN_NAME LIKE '%AREA%'
        ORDER BY ORDINAL_POSITION
    """)
    
    cols = cursor.fetchall()
    print(f"  Columns with 'AREA' in SALES table:")
    for col in cols:
        max_len = f"({col.CHARACTER_MAXIMUM_LENGTH})" if col.CHARACTER_MAXIMUM_LENGTH else ""
        print(f"    - {col.COLUMN_NAME}: {col.DATA_TYPE}{max_len}, NULL={col.IS_NULLABLE}")
    
    # 3. Check unique fSALESAREA values
    print("\n3. Unique fSALESAREA values in SALES table:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT DISTINCT
            fSALESAREA,
            COUNT(*) AS SalesCount
        FROM SALES
        WHERE fSALESAREA IS NOT NULL
        GROUP BY fSALESAREA
        ORDER BY fSALESAREA
    """)
    
    areas = cursor.fetchall()
    print(f"  Found {len(areas)} unique Sales Areas:")
    for area in areas:
        print(f"    SA {area.fSALESAREA}: {area.SalesCount:,} sales")
    
    # 4. Search for reference tables
    print("\n4. Searching for reference tables:")
    print("-" * 80)
    
    # Check if tables like SALESAREAS, TERRITORIES, REGIONS exist
    possible_tables = ['SALESAREAS', 'TERRITORIES', 'REGIONS', 'AREAS', 'ZONES', 'SALESREGIONS']
    
    for table_name in possible_tables:
        try:
            cursor.execute(f"SELECT TOP 1 * FROM {table_name}")
            print(f"  [+] Table {table_name} exists!")
            
            # Show structure
            cursor.execute(f"""
                SELECT 
                    COLUMN_NAME,
                    DATA_TYPE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = '{table_name}'
                ORDER BY ORDINAL_POSITION
            """)
            
            cols = cursor.fetchall()
            print(f"    Columns: {', '.join([col.COLUMN_NAME for col in cols])}")
            
        except:
            pass
    
    # 5. Check CUSTOMERS link to Sales Area
    print("\n5. Checking CUSTOMERS relation to Sales Area:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            COLUMN_NAME,
            DATA_TYPE,
            CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'CUSTOMERS'
        AND (COLUMN_NAME LIKE '%AREA%' OR COLUMN_NAME LIKE '%REGION%' OR COLUMN_NAME LIKE '%TERRITORY%')
        ORDER BY ORDINAL_POSITION
    """)
    
    cols = cursor.fetchall()
    if cols:
        print(f"  Found columns in CUSTOMERS:")
        for col in cols:
            max_len = f"({col.CHARACTER_MAXIMUM_LENGTH})" if col.CHARACTER_MAXIMUM_LENGTH else ""
            print(f"    - {col.COLUMN_NAME}: {col.DATA_TYPE}{max_len}")
    else:
        print(f"  [-] No direct relation between CUSTOMERS and Sales Area")
        print(f"  -> Relation through SALES.fSALESAREA")
    
    # 6. Customer statistics by Sales Area
    print("\n6. Customer statistics by Sales Area:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            s.fSALESAREA,
            COUNT(DISTINCT s.fCUSTOMERID) AS UniqueCustomers,
            COUNT(*) AS TotalSales,
            MIN(s.fDATE) AS FirstSale,
            MAX(s.fDATE) AS LastSale
        FROM SALES s
        WHERE s.fSTATE = 2
        AND s.fSALESAREA IS NOT NULL
        GROUP BY s.fSALESAREA
        ORDER BY s.fSALESAREA
    """)
    
    stats = cursor.fetchall()
    print(f"  {'SA':<5} {'Customers':<12} {'Sales':<12} {'First Sale':<15} {'Last Sale':<15}")
    print(f"  {'-'*5} {'-'*12} {'-'*12} {'-'*15} {'-'*15}")
    for row in stats:
        print(f"  {row.fSALESAREA:<5} {row.UniqueCustomers:<12,} {row.TotalSales:<12,} {str(row.FirstSale)[:10]:<15} {str(row.LastSale)[:10]:<15}")
    
    # 7. Check customer groups in each Sales Area
    print("\n7. Customer groups selling in each Sales Area:")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            s.fSALESAREA,
            c.fGROUP,
            COUNT(DISTINCT s.fCUSTOMERID) AS CustomerCount
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        WHERE s.fSTATE = 2
        AND s.fSALESAREA IS NOT NULL
        AND c.fGROUP IN ('002', '036')
        GROUP BY s.fSALESAREA, c.fGROUP
        HAVING COUNT(DISTINCT s.fCUSTOMERID) > 10
        ORDER BY s.fSALESAREA, c.fGROUP
    """)
    
    groups = cursor.fetchall()
    current_sa = None
    for row in groups:
        if current_sa != row.fSALESAREA:
            print(f"\n  Sales Area {row.fSALESAREA}:")
            current_sa = row.fSALESAREA
        print(f"    - Group {row.fGROUP}: {row.CustomerCount} customers")
    
    cursor.close()
    conn.close()
    
    print("\n" + "=" * 80)
    print("[+] Analysis completed")
    print("=" * 80)
    
except Exception as e:
    print(f"\n[!] Error: {e}")
    import traceback
    traceback.print_exc()

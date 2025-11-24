import pyodbc

# Database connection
conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)
cursor = conn.cursor()

print("=" * 80)
print("CHECKING PREPAYMENT IN PAYMENTS TABLE")
print("=" * 80)

# 1. Check table structure
print("\n1. PAYMENTS table structure:")
cursor.execute("""
    SELECT 
        COLUMN_NAME,
        DATA_TYPE,
        CHARACTER_MAXIMUM_LENGTH,
        IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'PAYMENTS'
    ORDER BY ORDINAL_POSITION
""")
print("\nColumns:")
for row in cursor.fetchall():
    nullable = "NULL" if row.IS_NULLABLE == 'YES' else "NOT NULL"
    length = f"({row.CHARACTER_MAXIMUM_LENGTH})" if row.CHARACTER_MAXIMUM_LENGTH else ""
    print(f"  - {row.COLUMN_NAME}: {row.DATA_TYPE}{length} {nullable}")

# 2. Check for prepayment-related columns
print("\n2. Looking for prepayment-related columns:")
cursor.execute("""
    SELECT COLUMN_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'PAYMENTS'
        AND (
            COLUMN_NAME LIKE '%prepay%' 
            OR COLUMN_NAME LIKE '%PREPAY%'
            OR COLUMN_NAME LIKE '%advance%'
            OR COLUMN_NAME LIKE '%ADVANCE%'
            OR COLUMN_NAME LIKE '%credit%'
            OR COLUMN_NAME LIKE '%CREDIT%'
        )
""")
prepay_cols = cursor.fetchall()
if prepay_cols:
    print("  Found columns:")
    for row in prepay_cols:
        print(f"    - {row.COLUMN_NAME}")
else:
    print("  No prepayment-related columns found")

# 3. Check payment types
print("\n3. Checking payment types (fPAYMENTTYPE):")
cursor.execute("""
    SELECT 
        fPAYMENTTYPE,
        COUNT(*) as RecordCount,
        SUM(fSUM) as TotalAmount,
        MIN(fDATE) as FirstDate,
        MAX(fDATE) as LastDate
    FROM PAYMENTS
    WHERE fPAYMENTTYPE IS NOT NULL
    GROUP BY fPAYMENTTYPE
    ORDER BY fPAYMENTTYPE
""")
print("\nPayment types breakdown:")
for row in cursor.fetchall():
    print(f"  Type {row.fPAYMENTTYPE}: {row.RecordCount:,} records, "
          f"Total: {float(row.TotalAmount):,.2f} AMD, "
          f"Date range: {row.FirstDate} to {row.LastDate}")

# 4. Check if there's a PAYMENTTYPES reference table
print("\n4. Checking for PAYMENTTYPES reference table:")
cursor.execute("""
    SELECT TABLE_NAME
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME LIKE '%PAYMENT%TYPE%'
        OR TABLE_NAME LIKE '%TYPE%PAYMENT%'
""")
type_tables = cursor.fetchall()
if type_tables:
    print("  Found tables:")
    for row in type_tables:
        print(f"    - {row.TABLE_NAME}")
        # Check structure
        cursor.execute(f"""
            SELECT COLUMN_NAME, DATA_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = ?
        """, row.TABLE_NAME)
        print(f"      Columns: {', '.join([c.COLUMN_NAME for c in cursor.fetchall()])}")
        
        # Check data
        cursor.execute(f"SELECT TOP 10 * FROM {row.TABLE_NAME}")
        print(f"      Data:")
        for data_row in cursor.fetchall():
            print(f"        {data_row}")
else:
    print("  No payment type reference tables found")

# 5. Check for specific payment type values that might indicate prepayment
print("\n5. Sample PAYMENTS records by type:")
cursor.execute("""
    SELECT TOP 3
        fID,
        fPAYMENTTYPE,
        fSUM,
        fDATE,
        fSTATE,
        fSALESAREA,
        fCUSTOMERID
    FROM PAYMENTS
    WHERE fPAYMENTTYPE IS NOT NULL
    ORDER BY fPAYMENTTYPE, fDATE DESC
""")
for row in cursor.fetchall():
    print(f"  ID: {row.fID}, Type: {row.fPAYMENTTYPE}, "
          f"Amount: {float(row.fSUM):,.2f}, Date: {row.fDATE}, "
          f"State: {row.fSTATE}, Area: {row.fSALESAREA}")

# 6. Check for negative amounts (might indicate refunds/prepayments)
print("\n6. Checking for negative amounts:")
cursor.execute("""
    SELECT 
        COUNT(*) as NegativeCount,
        SUM(fSUM) as TotalNegative,
        MIN(fSUM) as MinAmount,
        MAX(fSUM) as MaxNegative
    FROM PAYMENTS
    WHERE fSUM < 0
""")
row = cursor.fetchone()
if row.NegativeCount and row.NegativeCount > 0:
    print(f"  Found {row.NegativeCount} records with negative amounts")
    print(f"  Total: {float(row.TotalNegative):,.2f} AMD")
    print(f"  Range: {float(row.MinAmount):,.2f} to {float(row.MaxNegative):,.2f}")
else:
    print("  No negative amounts found")

# 7. Check STATE values
print("\n7. Payment states (fSTATE):")
cursor.execute("""
    SELECT 
        fSTATE,
        COUNT(*) as RecordCount,
        SUM(fSUM) as TotalAmount
    FROM PAYMENTS
    GROUP BY fSTATE
    ORDER BY fSTATE
""")
for row in cursor.fetchall():
    state = row.fSTATE if row.fSTATE is not None else 'NULL'
    print(f"  STATE {state}: {row.RecordCount:,} records, "
          f"Total: {float(row.TotalAmount):,.2f} AMD")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)

conn.close()

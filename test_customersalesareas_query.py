"""
Test query using CUSTOMERSALESAREAS table for SA 103
"""
import pyodbc

conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)

conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

# Test the new query structure with CUSTOMERSALESAREAS JOIN
sales_area = '103'
groups = ['002', '036']
date_from = '2024-11-16'
date_to = '2025-11-17'

print(f"\n{'='*80}")
print(f"Testing CUSTOMERSALESAREAS query for SA {sales_area} with groups {groups}")
print(f"Date range: {date_from} to {date_to}")
print(f"{'='*80}\n")

# First, check how many customers are assigned to SA 103 in CUSTOMERSALESAREAS
cursor.execute("""
    SELECT COUNT(DISTINCT csa.fCUSTOMERID)
    FROM CUSTOMERSALESAREAS csa
    INNER JOIN CUSTOMERS c ON csa.fCUSTOMERID = c.fID
    WHERE csa.fSALESAREA = ?
        AND c.fGROUP IN (?, ?)
""", (sales_area, groups[0], groups[1]))

assigned_count = cursor.fetchone()[0]
print(f"Customers assigned to SA {sales_area} in groups {groups}: {assigned_count}")

# Now check how many have debt > 0
cursor.execute("""
    WITH AllCustomers AS (
        SELECT DISTINCT
            c.fID AS CustomerId,
            c.fCODE AS CustomerCode,
            c.fNAME AS CustomerName,
            c.fGROUP AS GroupCode
        FROM CUSTOMERS c
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE csa.fSALESAREA = ?
            AND c.fGROUP IN (?, ?)
    ),
    DebtData AS (
        SELECT 
            doc.fCUSTOMERID AS CustomerId,
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS DebtFromDocs
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        GROUP BY doc.fCUSTOMERID
    ),
    RestData AS (
        SELECT 
            fCUSTOMERID AS CustomerId,
            ISNULL(SUM(CASE WHEN fTYPE = '01' THEN fSUM ELSE 0 END), 0) AS Type01,
            ISNULL(SUM(CASE WHEN fTYPE = '02' THEN fSUM ELSE 0 END), 0) AS Type02
        FROM HIRESTCUSTOMERSSUM
        GROUP BY fCUSTOMERID
    )
    SELECT COUNT(*)
    FROM AllCustomers ac
    LEFT JOIN DebtData dd ON ac.CustomerId = dd.CustomerId
    LEFT JOIN RestData rd ON ac.CustomerId = rd.CustomerId
    WHERE (ISNULL(dd.DebtFromDocs, 0) - ABS(ISNULL(rd.Type01, 0)) - ABS(ISNULL(rd.Type02, 0))) > 0
""", (sales_area, groups[0], groups[1]))

debt_count = cursor.fetchone()[0]
print(f"Customers with debt > 0: {debt_count}")

# Get sample of customers with debt
cursor.execute("""
    WITH AllCustomers AS (
        SELECT DISTINCT
            c.fID AS CustomerId,
            c.fCODE AS CustomerCode,
            c.fNAME AS CustomerName,
            c.fGROUP AS GroupCode
        FROM CUSTOMERS c
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE csa.fSALESAREA = ?
            AND c.fGROUP IN (?, ?)
    ),
    DebtData AS (
        SELECT 
            doc.fCUSTOMERID AS CustomerId,
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS DebtFromDocs
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        GROUP BY doc.fCUSTOMERID
    ),
    RestData AS (
        SELECT 
            fCUSTOMERID AS CustomerId,
            ISNULL(SUM(CASE WHEN fTYPE = '01' THEN fSUM ELSE 0 END), 0) AS Type01,
            ISNULL(SUM(CASE WHEN fTYPE = '02' THEN fSUM ELSE 0 END), 0) AS Type02
        FROM HIRESTCUSTOMERSSUM
        GROUP BY fCUSTOMERID
    )
    SELECT TOP 10
        ac.CustomerCode,
        ac.CustomerName,
        ac.GroupCode,
        (ISNULL(dd.DebtFromDocs, 0) - ABS(ISNULL(rd.Type01, 0)) - ABS(ISNULL(rd.Type02, 0))) AS Debt
    FROM AllCustomers ac
    LEFT JOIN DebtData dd ON ac.CustomerId = dd.CustomerId
    LEFT JOIN RestData rd ON ac.CustomerId = rd.CustomerId
    WHERE (ISNULL(dd.DebtFromDocs, 0) - ABS(ISNULL(rd.Type01, 0)) - ABS(ISNULL(rd.Type02, 0))) > 0
    ORDER BY Debt DESC
""", (sales_area, groups[0], groups[1]))

print(f"\nFirst 10 customers with debt:")
print(f"{'Code':<12} {'Name':<40} {'Group':<8} {'Debt':>15}")
print(f"{'-'*80}")
for row in cursor.fetchall():
    print(f"{row.CustomerCode:<12} {row.CustomerName:<40} {row.GroupCode:<8} {row.Debt:>15,.2f}")

cursor.close()
conn.close()

print(f"\n{'='*80}")
print(f"SUMMARY:")
print(f"  - Total assigned to SA {sales_area}: {assigned_count} customers")
print(f"  - With debt > 0: {debt_count} customers")
print(f"  - Expected in web interface: {debt_count} customers")
print(f"{'='*80}\n")

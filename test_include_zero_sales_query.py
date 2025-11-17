import pyodbc
import json

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
    print("Testing include_zero_sales query for Sales Area 103")
    print("=" * 80)
    
    # Parameters
    sales_area = '103'
    date_from = '2024-11-16'
    date_to = '2025-11-17'
    groups = ['002', '036']
    
    # Build query (simplified version without excluded/product filters)
    base_customer_clause = " AND c.fGROUP IN (?,?)"
    
    query = f"""
        WITH AllCustomers AS (
            SELECT DISTINCT
                c.fID AS CustomerId,
                c.fCODE AS CustomerCode,
                c.fNAME AS CustomerName,
                c.fGROUP AS GroupCode
            FROM CUSTOMERS c
            WHERE 1=1
                {base_customer_clause}
                -- Проверить что у клиента есть положительный долг
                AND EXISTS (
                    SELECT 1 
                    FROM (
                        SELECT 
                            ISNULL(
                                (SELECT SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END)
                                 FROM HICUSTOMERSDEBT d
                                 INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                                 WHERE doc.fCUSTOMERID = c.fID), 0
                            ) - 
                            ABS(ISNULL(
                                (SELECT SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END)
                                 FROM HIRESTCUSTOMERSSUM r
                                 WHERE r.fCUSTOMERID = c.fID), 0
                            )) -
                            ABS(ISNULL(
                                (SELECT SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END)
                                 FROM HIRESTCUSTOMERSSUM r
                                 WHERE r.fCUSTOMERID = c.fID), 0
                            )) AS FinalDebt
                    ) debt_check
                    WHERE debt_check.FinalDebt > 0
                )
        ),
        FilteredSales AS (
            SELECT 
                ac.CustomerId,
                sa.fCODE AS ManagerCode,
                sa.fNAME AS ManagerName,
                COUNT(s.fISN) AS SalesCount,
                ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
            FROM SALES s
            INNER JOIN AllCustomers ac ON s.fCUSTOMERID = ac.CustomerId
            LEFT JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
            WHERE s.fSTATE = 2
                AND s.fDATE >= ?
                AND s.fDATE <= ?
                AND s.fSALESAREA = ?
            GROUP BY ac.CustomerId, sa.fCODE, sa.fNAME
        ),
        Totals AS (
            SELECT 
                ac.CustomerId,
                ac.CustomerCode,
                ac.CustomerName,
                ac.GroupCode,
                ISNULL(SUM(fs.SalesCount), 0) AS SalesCount,
                ISNULL(SUM(fs.TotalSales), 0) AS TotalSales
            FROM AllCustomers ac
            LEFT JOIN FilteredSales fs ON ac.CustomerId = fs.CustomerId
            GROUP BY ac.CustomerId, ac.CustomerCode, ac.CustomerName, ac.GroupCode
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
        ),
        PaymentData AS (
            SELECT 
                fCUSTOMERID AS CustomerId,
                ISNULL(SUM(fSUM), 0) AS TotalPayments
            FROM PAYMENTS
            WHERE fSTATE = 2
                AND fDATE >= ?
                AND fDATE <= ?
            GROUP BY fCUSTOMERID
        )
        SELECT 
            t.CustomerId,
            t.CustomerCode,
            t.CustomerName,
            ISNULL(t.GroupCode, '') AS GroupCode,
            t.SalesCount,
            t.TotalSales,
            ISNULL(dd.DebtFromDocs, 0) AS DebtFromDocs,
            ISNULL(rd.Type01, 0) AS Type01,
            ISNULL(rd.Type02, 0) AS Type02,
            (ISNULL(dd.DebtFromDocs, 0) - ABS(ISNULL(rd.Type01, 0)) - ABS(ISNULL(rd.Type02, 0))) AS Debt,
            ISNULL(pd.TotalPayments, 0) AS TotalPayments
        FROM Totals t
        LEFT JOIN DebtData dd ON t.CustomerId = dd.CustomerId
        LEFT JOIN RestData rd ON t.CustomerId = rd.CustomerId
        LEFT JOIN PaymentData pd ON t.CustomerId = pd.CustomerId
        ORDER BY t.TotalSales DESC
    """
    
    # Parameters in correct order
    params = tuple(groups) + (date_from, date_to, sales_area) + (date_from, date_to)
    
    print(f"\nQuery parameters:")
    print(f"  Groups: {groups}")
    print(f"  Date from: {date_from}")
    print(f"  Date to: {date_to}")
    print(f"  Sales Area: {sales_area}")
    print(f"\nParams tuple: {params}")
    print(f"Params count: {len(params)}")
    
    # Count ? in query
    param_markers = query.count('?')
    print(f"Parameter markers (?) in query: {param_markers}")
    
    if len(params) != param_markers:
        print(f"\n⚠️  ERROR: Parameter count mismatch!")
        print(f"   Expected: {param_markers}, Got: {len(params)}")
    else:
        print(f"\n✓ Parameter count matches!")
    
    print(f"\nExecuting query...")
    cursor.execute(query, params)
    
    rows = cursor.fetchall()
    
    print(f"\n{'=' * 80}")
    print(f"RESULTS: Found {len(rows)} customers")
    print(f"{'=' * 80}")
    
    # Show statistics
    with_sales = sum(1 for r in rows if r.SalesCount > 0)
    without_sales = sum(1 for r in rows if r.SalesCount == 0)
    total_debt = sum(r.Debt for r in rows)
    
    print(f"\nStatistics:")
    print(f"  Total customers: {len(rows)}")
    print(f"  With sales in period: {with_sales}")
    print(f"  Without sales in period: {without_sales}")
    print(f"  Total debt: {total_debt:,.2f} ֏")
    
    # Show first 10 customers
    print(f"\nFirst 10 customers:")
    print(f"{'ID':<8} {'Code':<10} {'Group':<6} {'Sales':>10} {'Amount':>15} {'Debt':>15}")
    print("-" * 80)
    for row in rows[:10]:
        print(f"{row.CustomerId:<8} {row.CustomerCode:<10} {row.GroupCode:<6} {row.SalesCount:>10} {row.TotalSales:>15,.2f} {row.Debt:>15,.2f}")
    
    if len(rows) > 10:
        print(f"... and {len(rows) - 10} more customers")
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

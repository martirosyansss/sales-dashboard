import pyodbc

conn = pyodbc.connect('DRIVER={SQL Server};SERVER=192.168.88.100;DATABASE=SalesManagement;UID=sa;PWD=M1t1g@t0r')
cursor = conn.cursor()

# Check debt for customer 35461
query = """
SELECT doc.fCUSTOMERID, c.fCODE, c.fNAME, c.fGROUP,
       SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) AS TotalDebt
FROM HICUSTOMERSDEBT d
INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
WHERE doc.fCUSTOMERID = 35461
GROUP BY doc.fCUSTOMERID, c.fCODE, c.fNAME, c.fGROUP
"""

cursor.execute(query)
row = cursor.fetchone()

if row:
    print(f"Customer ID: {row[0]}")
    print(f"Customer Code: {row[1]}")
    print(f"Customer Name: {row[2]}")
    print(f"Group: {row[3]}")
    print(f"Total Debt: {row[4]:.2f} AMD")
    print(f"Debt > 0: {row[4] > 0}")
else:
    print("No debt records found for customer 35461")

# Check if customer has any sales in period
cursor.execute("""
SELECT COUNT(*) FROM SALES 
WHERE fCUSTOMERID = 35461 
AND fSTATE = 2
AND fDATE >= '2025-10-31'
AND fDATE <= '2025-11-29'
""")
sales_count = cursor.fetchone()[0]
print(f"\nSales in period: {sales_count}")

# Check if customer has any sales at all
cursor.execute("""
SELECT COUNT(*) FROM SALES 
WHERE fCUSTOMERID = 35461 
AND fSTATE = 2
""")
total_sales = cursor.fetchone()[0]
print(f"Total sales (all time): {total_sales}")

conn.close()

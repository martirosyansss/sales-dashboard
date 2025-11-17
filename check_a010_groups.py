import pyodbc

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

cursor.execute("""
    SELECT COUNT(DISTINCT c.fID), COUNT(DISTINCT c.fGROUP) 
    FROM CUSTOMERS c 
    INNER JOIN SALES s ON c.fID = s.fCUSTOMERID 
    WHERE s.fSALESAGENTID = (SELECT fID FROM SALESAGENTS WHERE fCODE = 'A010/1')
""")
row = cursor.fetchone()
print(f'Customers: {row[0]}, Different Groups: {row[1]}')

cursor.execute("""
    SELECT DISTINCT c.fGROUP 
    FROM CUSTOMERS c 
    INNER JOIN SALES s ON c.fID = s.fCUSTOMERID 
    WHERE s.fSALESAGENTID = (SELECT fID FROM SALESAGENTS WHERE fCODE = 'A010/1')
    AND c.fGROUP IS NOT NULL
    ORDER BY c.fGROUP
""")
groups = [row[0] for row in cursor.fetchall()]
print(f'Groups: {", ".join(groups[:10])}{"..." if len(groups) > 10 else ""}')

conn.close()

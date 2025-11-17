import pyodbc

conn_str = 'DRIVER={ODBC Driver 17 for SQL Server};SERVER=DESKTOP-Q86VD1N;DATABASE=SalesManagement;UID=sa;PWD=root'
conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

# Проверим, есть ли продажи с товарами из группы 20
query = """
SELECT TOP 5 
    s.fISN,
    s.fTOTALSUM,
    p.fGROUP,
    p.fNAME
FROM SALES s
INNER JOIN SALEDOCDETAILS sd ON s.fISN = sd.fSALEDOCISN
INNER JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
WHERE s.fDATE >= '2024-11-01' AND s.fDATE <= '2024-11-30'
AND s.fSTATE = 2
AND p.fGROUP = '20'
"""

cursor.execute(query)
rows = cursor.fetchall()

if rows:
    print(f"Found {len(rows)} sales with products from group 20:")
    for row in rows:
        print(f"  Sale ISN: {row.fISN}, Total: {row.fTOTALSUM}, Group: [{row.fGROUP}], Product: {row.fNAME}")
else:
    print("No sales found with products from group 20")

# Теперь проверим с EXISTS подзапросом
query_exists = """
SELECT TOP 5 
    s.fISN,
    s.fTOTALSUM,
    s.fDATE
FROM SALES s
WHERE s.fDATE >= '2024-11-01' AND s.fDATE <= '2024-11-30'
AND s.fSTATE = 2
AND EXISTS (
    SELECT 1 FROM SALEDOCDETAILS sd
    INNER JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
    WHERE sd.fSALEDOCISN = s.fISN
    AND p.fGROUP = '20'
)
"""

cursor.execute(query_exists)
rows2 = cursor.fetchall()

print(f"\nUsing EXISTS subquery: Found {len(rows2)} sales")
for row in rows2:
    print(f"  Sale ISN: {row.fISN}, Total: {row.fTOTALSUM}, Date: {row.fDATE}")

conn.close()

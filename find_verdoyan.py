"""
Найти ID менеджера Վերդոյան Նորայր
"""
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

# Поиск по имени и коду
cursor.execute("""
    SELECT fID, fCODE, fNAME, fEXTERNALCODE, fCLOSED
    FROM SALESAGENTS
    WHERE fNAME LIKE '%Վերդոյան%' OR fNAME LIKE '%Verdoyan%'
       OR fCODE LIKE '%A003%'
    ORDER BY fCODE, fNAME
""")

print("Поиск менеджера Վերդոյան Նորայր:")
print("=" * 80)

for row in cursor.fetchall():
    print(f"ID: {row.fID:5d} | Код: {row.fCODE:10s} | Имя: {row.fNAME}")
    print(f"           External: {row.fEXTERNALCODE} | Closed: {row.fCLOSED}")
    print("-" * 80)

conn.close()

import pyodbc

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)
cursor = conn.cursor()

print("\n=== COMPARISON: Area 105 - WITH/WITHOUT Groups Filter ===")

print("\n--- WITHOUT GROUPS FILTER (ALL CUSTOMERS) ---")
# Дебет для всех клиентов Area 105
query1 = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as Debt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND d.fDATE < '2025-11-01'
"""
cursor.execute(query1)
debt_all = float(cursor.fetchone().Debt)
print(f"Дебет (все клиенты): {debt_all:,.2f} AMD")

# Type01/Type02 для всех
query2 = """
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
"""
cursor.execute(query2)
row = cursor.fetchone()
type01_all = float(row.Type01)
type02_all = float(row.Type02)
final_all = debt_all - abs(type01_all) - abs(type02_all)

print(f"Type01: {type01_all:,.2f} AMD")
print(f"Type02: {type02_all:,.2f} AMD")
print(f"ДОЛГ = {debt_all:,.2f} - {abs(type01_all):,.2f} - {abs(type02_all):,.2f} = {final_all:,.2f} AMD")

print("\n--- WITH GROUPS FILTER (036, 002) ---")
# Дебет только для групп 036, 002
query3 = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as Debt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('036', '002')
    AND d.fDATE < '2025-11-01'
"""
cursor.execute(query3)
debt_filtered = float(cursor.fetchone().Debt)
print(f"Дебет (группы 036, 002): {debt_filtered:,.2f} AMD")

# Type01/Type02 для групп 036, 002
query4 = """
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND c.fGROUP IN ('036', '002')
"""
cursor.execute(query4)
row = cursor.fetchone()
type01_filtered = float(row.Type01)
type02_filtered = float(row.Type02)
final_filtered = debt_filtered - abs(type01_filtered) - abs(type02_filtered)

print(f"Type01: {type01_filtered:,.2f} AMD")
print(f"Type02: {type02_filtered:,.2f} AMD")
print(f"ДОЛГ = {debt_filtered:,.2f} - {abs(type01_filtered):,.2f} - {abs(type02_filtered):,.2f} = {final_filtered:,.2f} AMD")

print(f"\n--- DISPLAYED ON PAGE ---")
print(f"Отображается: 1,696,049.41 AMD")
print(f"Правильное значение (с фильтром): {final_filtered:,.2f} AMD")
print(f"Разница: {abs(final_filtered - 1696049.41):,.2f} AMD")

# Проверим, откуда берется 1,696,049.41
print(f"\n--- ANALYSIS ---")
print(f"1,696,049.41 примерно равно дебету с фильтром без вычетов: {debt_filtered:,.2f}")
print(f"Разница между дебетом и отображаемым: {abs(debt_filtered - 1696049.41):,.2f}")

conn.close()

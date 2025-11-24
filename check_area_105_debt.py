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

print("\n=== DEBT CALCULATION FOR AREA 105 (October 2025) ===")

# 1. Дебет из HICUSTOMERSDEBT (на начало ноября = конец октября)
query_debit = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
    AND d.fDATE < '2025-11-01'
"""
cursor.execute(query_debit)
debit = float(cursor.fetchone().DebtFromDocs)

# 2. Type01 и Type02 для Area 105
query_rest = """
    SELECT 
        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '105'
"""
cursor.execute(query_rest)
rest_row = cursor.fetchone()
type01 = float(rest_row.Type01)
type02 = float(rest_row.Type02)

# 3. Расчет по формуле
final_debt = debit - abs(type01) - abs(type02)

print(f"\n1. ДЕБЕТ (из HICUSTOMERSDEBT до 2025-11-01):")
print(f"   {debit:,.2f} AMD")

print(f"\n2. Type01 (Возвраты):")
print(f"   {type01:,.2f} AMD")

print(f"\n3. Type02 (Предоплата):")
print(f"   {type02:,.2f} AMD")

print(f"\n4. ФОРМУЛА: ДОЛГ = ДЕБЕТ - |Type01| - |Type02|")
print(f"   ДОЛГ = {debit:,.2f} - |{type01:,.2f}| - |{type02:,.2f}|")
print(f"   ДОЛГ = {final_debt:,.2f} AMD")

print(f"\n5. Отображается на странице: 1,696,049.41 AMD")
print(f"   Разница: {abs(final_debt - 1696049.41):,.2f} AMD")

# 4. Проверим, что показывает API generate-plans для Area 105
print("\n6. Проверка через код app_v2.py:")
print("   Возможные причины расхождения:")
print("   - API может не учитывать Type01/Type02")
print("   - Может использоваться другой диапазон дат")
print("   - Может быть фильтр по группам клиентов")

conn.close()

import pyodbc

conn_str = (
    r'DRIVER={ODBC Driver 17 for SQL Server};'
    r'SERVER=localhost;'
    r'DATABASE=SalesManagement-;'
    r'UID=sa;'
    r'PWD=Aa123456;'
    r'TrustServerCertificate=yes;'
)

conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

manager_code = 'A006/6'
target_debt = 6012374.25

print("=" * 80)
print(f"ПОЛНЫЙ РАСЧЕТ ДОЛГА ДЛЯ МЕНЕДЖЕРА {manager_code} - Սիմոնյան Լիանա")
print("=" * 80)

# 1. Базовый долг из HICUSTOMERSDEBT
query1 = """
SELECT 
    sa.fCODE,
    sa.fNAME,
    ISNULL(SUM(CASE WHEN hd.fDBCR = 'D' THEN hd.fSUM ELSE -hd.fSUM END), 0) as DebtFromDocs
FROM HICUSTOMERSDEBT hd
INNER JOIN DOCUMENTS doc ON hd.fDEBTDOCISN = doc.fISN
INNER JOIN SALESAGENTS sa ON doc.fSALESAGENTID = sa.fID
WHERE sa.fCODE = ?
GROUP BY sa.fCODE, sa.fNAME
"""
cursor.execute(query1, manager_code)
row = cursor.fetchone()
if row:
    debt_from_docs = float(row.DebtFromDocs)
    print(f"\nМенеджер: {row.fCODE} - {row.fNAME}")
    print(f"1. Долг из HICUSTOMERSDEBT: {debt_from_docs:,.2f} AMD")
else:
    print(f"Менеджер {manager_code} не найден!")
    exit()

# 2. Остатки Type 01
query2 = """
SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
FROM HIRESTCUSTOMERSSUM r
WHERE r.fCUSTOMERID IN (
    SELECT DISTINCT d.fCUSTOMERID
    FROM DOCUMENTS d
    INNER JOIN SALESAGENTS sa ON d.fSALESAGENTID = sa.fID
    WHERE sa.fCODE = ?
)
AND r.fTYPE = '01'
"""
cursor.execute(query2, manager_code)
rest_type_01 = float(cursor.fetchone().RestSum)
print(f"2. Остатки Type 01: {rest_type_01:,.2f} AMD")

# 3. Остатки Type 02
query3 = """
SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
FROM HIRESTCUSTOMERSSUM r
WHERE r.fCUSTOMERID IN (
    SELECT DISTINCT d.fCUSTOMERID
    FROM DOCUMENTS d
    INNER JOIN SALESAGENTS sa ON d.fSALESAGENTID = sa.fID
    WHERE sa.fCODE = ?
)
AND r.fTYPE = '02'
"""
cursor.execute(query3, manager_code)
rest_type_02 = float(cursor.fetchone().RestSum)
print(f"3. Остатки Type 02: {rest_type_02:,.2f} AMD")

print("\n" + "=" * 50)
print("ВАРИАНТЫ РАСЧЕТА:")
print("=" * 50)

variant1 = debt_from_docs + rest_type_01 + rest_type_02
variant2 = debt_from_docs - rest_type_01 - rest_type_02
variant3 = debt_from_docs + abs(rest_type_01) + abs(rest_type_02)
variant4 = debt_from_docs - abs(rest_type_01) - abs(rest_type_02)

variants = [
    ("Вариант 1 (Долг + Type01 + Type02)", variant1),
    ("Вариант 2 (Долг - Type01 - Type02)", variant2),
    ("Вариант 3 (Долг + |Type01| + |Type02|)", variant3),
    ("Вариант 4 (Долг - |Type01| - |Type02|)", variant4),
]

for name, value in variants:
    diff = abs(value - target_debt)
    percent = (diff / target_debt) * 100
    if diff < 1000:
        status = "✓✓✓ СОВПАДАЕТ!"
    elif diff < 50000:
        status = f"близко! ({diff:,.2f} AMD, {percent:.2f}%)"
    else:
        status = f"разница {diff:,.2f} AMD ({percent:.2f}%)"
    
    print(f"{name}: {value:,.2f} AMD - {status}")

print("\n" + "=" * 50)
print(f"ЦЕЛЕВАЯ СУММА: {target_debt:,.2f} AMD")
print("=" * 50)

# Найти наиболее близкий вариант
best_variant = min(variants, key=lambda x: abs(x[1] - target_debt))
print(f"\nНАИЛУЧШИЙ ВАРИАНТ: {best_variant[0]}")
print(f"Значение: {best_variant[1]:,.2f} AMD")
print(f"Разница: {abs(best_variant[1] - target_debt):,.2f} AMD ({abs((best_variant[1] - target_debt) / target_debt) * 100:.2f}%)")

conn.close()

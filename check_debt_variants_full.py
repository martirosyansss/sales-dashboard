import pyodbc
import json

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

# Загружаем выбранные divisions
try:
    with open('selected_product_groups.json', 'r', encoding='utf-8') as f:
        selected_divisions = json.load(f)
    print(f"Выбранные divisions: {selected_divisions}")
except:
    selected_divisions = []
    print("Файл selected_product_groups.json не найден, проверяем без фильтра")

print("\n" + "=" * 80)
print("ВАРИАНТЫ РАСЧЕТА ДОЛГА ДЛЯ МЕНЕДЖЕРА A006")
print("=" * 80)

# Базовый долг
cursor.execute("""
    SELECT ISNULL(SUM(CASE WHEN hd.fDBCR = 'D' THEN hd.fSUM ELSE -hd.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT hd
    INNER JOIN DOCUMENTS doc ON hd.fDEBTDOCISN = doc.fISN
    INNER JOIN SALESAGENTS sa ON doc.fSALESAGENTID = sa.fID
    WHERE sa.fCODE = 'A006'
""")
debt_from_docs = float(cursor.fetchone().DebtFromDocs)
print(f"\n1. БАЗОВЫЙ долг из HICUSTOMERSDEBT: {debt_from_docs:,.2f} AMD")

# Остатки Type 01
cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT d.fCUSTOMERID
        FROM DOCUMENTS d
        INNER JOIN SALESAGENTS sa ON d.fSALESAGENTID = sa.fID
        WHERE sa.fCODE = 'A006'
    )
    AND r.fTYPE = '01'
""")
rest_type_01 = float(cursor.fetchone().RestSum)

# Остатки Type 02
cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT d.fCUSTOMERID
        FROM DOCUMENTS d
        INNER JOIN SALESAGENTS sa ON d.fSALESAGENTID = sa.fID
        WHERE sa.fCODE = 'A006'
    )
    AND r.fTYPE = '02'
""")
rest_type_02 = float(cursor.fetchone().RestSum)

print(f"2. Остатки Type 01: {rest_type_01:,.2f} AMD")
print(f"3. Остатки Type 02: {rest_type_02:,.2f} AMD")

print("\n" + "=" * 50)
print("ВАРИАНТЫ КОМБИНАЦИИ:")
print("=" * 50)

variant1 = debt_from_docs + rest_type_01 + rest_type_02
print(f"Вариант 1 (Долг + Type01 + Type02): {variant1:,.2f} AMD")

variant2 = debt_from_docs - rest_type_01 - rest_type_02
print(f"Вариант 2 (Долг - Type01 - Type02): {variant2:,.2f} AMD")

variant3 = debt_from_docs + abs(rest_type_01) + abs(rest_type_02)
print(f"Вариант 3 (Долг + |Type01| + |Type02|): {variant3:,.2f} AMD")

variant4 = debt_from_docs - abs(rest_type_01) - abs(rest_type_02)
print(f"Вариант 4 (Долг - |Type01| - |Type02|): {variant4:,.2f} AMD")

# Проверка с фильтром по division
if selected_divisions:
    division_list = "', '".join(selected_divisions)
    
    cursor.execute(f"""
        SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
        FROM HIRESTCUSTOMERSSUM r
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT d.fCUSTOMERID
            FROM DOCUMENTS d
            INNER JOIN SALESAGENTS sa ON d.fSALESAGENTID = sa.fID
            WHERE sa.fCODE = 'A006'
        )
        AND r.fTYPE = '01'
        AND r.fDIVISION IN ('{division_list}')
    """)
    rest_type_01_filtered = float(cursor.fetchone().RestSum)
    
    cursor.execute(f"""
        SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
        FROM HIRESTCUSTOMERSSUM r
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT d.fCUSTOMERID
            FROM DOCUMENTS d
            INNER JOIN SALESAGENTS sa ON d.fSALESAGENTID = sa.fID
            WHERE sa.fCODE = 'A006'
        )
        AND r.fTYPE = '02'
        AND r.fDIVISION IN ('{division_list}')
    """)
    rest_type_02_filtered = float(cursor.fetchone().RestSum)
    
    variant5 = debt_from_docs + rest_type_01_filtered + rest_type_02_filtered
    print(f"\nВариант 5 (Долг + Type01[filtered] + Type02[filtered]): {variant5:,.2f} AMD")
    print(f"  Type01 filtered: {rest_type_01_filtered:,.2f}")
    print(f"  Type02 filtered: {rest_type_02_filtered:,.2f}")

print("\n" + "=" * 50)
print(f"ЦЕЛЕВАЯ СУММА: 6,012,374.25 AMD")
print("=" * 50)

variants = [
    ("Вариант 1", variant1),
    ("Вариант 2", variant2),
    ("Вариант 3", variant3),
    ("Вариант 4", variant4),
]

for name, value in variants:
    diff = abs(6012374.25 - value)
    percent = (diff / 6012374.25) * 100
    status = "✓✓✓ СОВПАДАЕТ!" if diff < 1000 else f"отличие {percent:.2f}%"
    print(f"{name}: {value:,.2f} AMD - {status}")

conn.close()

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

print("=" * 80)
print("ВСЕ МЕНЕДЖЕРЫ И ИХ ДОЛГИ (отсортировано по сумме долга)")
print("=" * 80)

query = """
SELECT 
    sa.fCODE,
    sa.fNAME,
    ISNULL(SUM(CASE WHEN hd.fDBCR = 'D' THEN hd.fSUM ELSE -hd.fSUM END), 0) as DebtFromDocs
FROM SALESAGENTS sa
LEFT JOIN DOCUMENTS doc ON doc.fSALESAGENTID = sa.fID
LEFT JOIN HICUSTOMERSDEBT hd ON hd.fDEBTDOCISN = doc.fISN
GROUP BY sa.fCODE, sa.fNAME
HAVING SUM(CASE WHEN hd.fDBCR = 'D' THEN hd.fSUM ELSE -hd.fSUM END) IS NOT NULL
ORDER BY DebtFromDocs DESC
"""

cursor.execute(query)
rows = cursor.fetchall()

print(f"\nНайдено менеджеров с долгами: {len(rows)}")
print("\n{:<10} {:<40} {:>20}".format("КОД", "ИМЯ", "ДОЛГ (AMD)"))
print("-" * 80)

target_debt = 6012374.25
closest_managers = []

for row in rows:
    debt = float(row.DebtFromDocs)
    print(f"{row.fCODE:<10} {row.fNAME:<40} {debt:>20,.2f}")
    
    # Ищем близкие к целевой сумме
    diff = abs(debt - target_debt)
    if diff < 500000:  # В пределах 500k
        closest_managers.append((row.fCODE, row.fNAME, debt, diff))

if closest_managers:
    print("\n" + "=" * 80)
    print(f"МЕНЕДЖЕРЫ С ДОЛГОМ БЛИЗКИМ К {target_debt:,.2f} AMD:")
    print("=" * 80)
    for code, name, debt, diff in sorted(closest_managers, key=lambda x: x[3]):
        print(f"{code:<10} {name:<40} {debt:>20,.2f} (разница: {diff:,.2f})")

# Ищем менеджера Симонян Лиана
print("\n" + "=" * 80)
print("ПОИСК МЕНЕДЖЕРА 'Симонян Лиана'")
print("=" * 80)
cursor.execute("""
    SELECT fCODE, fNAME
    FROM SALESAGENTS
    WHERE fNAME LIKE N'%Симонян%' OR fNAME LIKE N'%Лиана%'
""")
for row in cursor.fetchall():
    print(f"Найден: {row.fCODE} - {row.fNAME}")
    
    # Получаем долг для этого менеджера
    cursor.execute(f"""
        SELECT ISNULL(SUM(CASE WHEN hd.fDBCR = 'D' THEN hd.fSUM ELSE -hd.fSUM END), 0) as Debt
        FROM HICUSTOMERSDEBT hd
        INNER JOIN DOCUMENTS doc ON hd.fDEBTDOCISN = doc.fISN
        INNER JOIN SALESAGENTS sa ON doc.fSALESAGENTID = sa.fID
        WHERE sa.fCODE = '{row.fCODE}'
    """)
    debt_row = cursor.fetchone()
    if debt_row:
        debt = float(debt_row.Debt)
        print(f"  Долг: {debt:,.2f} AMD")
        diff = abs(debt - target_debt)
        if diff < 1000:
            print(f"  ✓✓✓ СОВПАДАЕТ с целевой суммой!")
        else:
            print(f"  Разница с целевой: {diff:,.2f} AMD ({(diff/target_debt)*100:.1f}%)")

conn.close()

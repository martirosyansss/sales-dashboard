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
customer_groups = ['002', '036']

cursor.execute("SELECT fID FROM SALESAGENTS WHERE fCODE = ?", manager_code)
manager_id = cursor.fetchone().fID

expected_debt = 6297356.55
expected_rest01 = -48220.11
expected_rest02 = -236762.19
expected_total = 6012374.25

print("=" * 80)
print("ГИПОТЕЗА: Группы 002,036 используются ТОЛЬКО для фильтрации остатков!")
print("=" * 80)
print("Долг - для ВСЕХ клиентов менеджера")
print("Остатки - ТОЛЬКО для клиентов из групп 002,036")
print()

# Долг БЕЗ фильтра
cursor.execute("""
    SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    WHERE doc.fSALESAGENTID = ?
""", (manager_id,))
debt_total = float(cursor.fetchone().DebtFromDocs)

# Остатки С фильтром по группам
cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT doc2.fCUSTOMERID 
        FROM DOCUMENTS doc2 
        WHERE doc2.fSALESAGENTID = ?
    )
    AND r.fTYPE = '01'
    AND c.fGROUP IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest01_filtered = float(cursor.fetchone().RestSum)

cursor.execute("""
    SELECT ISNULL(SUM(r.fSUM), 0) as RestSum
    FROM HIRESTCUSTOMERSSUM r
    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
    WHERE r.fCUSTOMERID IN (
        SELECT DISTINCT doc2.fCUSTOMERID 
        FROM DOCUMENTS doc2 
        WHERE doc2.fSALESAGENTID = ?
    )
    AND r.fTYPE = '02'
    AND c.fGROUP IN (?, ?)
""", (manager_id, customer_groups[0], customer_groups[1]))
rest02_filtered = float(cursor.fetchone().RestSum)

total = debt_total + rest01_filtered + rest02_filtered

print(f"{'Компонент':<30} {'Ожидаемое':>20} {'Фактическое':>20} {'Разница':>15}")
print("-" * 90)
print(f"{'Долг (все клиенты)':<30} {expected_debt:>20,.2f} {debt_total:>20,.2f} {abs(expected_debt - debt_total):>15,.2f}")
print(f"{'Type 01 (группы 002,036)':<30} {expected_rest01:>20,.2f} {rest01_filtered:>20,.2f} {abs(expected_rest01 - rest01_filtered):>15,.2f}")
print(f"{'Type 02 (группы 002,036)':<30} {expected_rest02:>20,.2f} {rest02_filtered:>20,.2f} {abs(expected_rest02 - rest02_filtered):>15,.2f}")
print("-" * 90)
print(f"{'ИТОГО':<30} {expected_total:>20,.2f} {total:>20,.2f} {abs(expected_total - total):>15,.2f}")

if abs(total - expected_total) < 1:
    print("\n✓✓✓ ПОЛНОЕ СОВПАДЕНИЕ!")
elif abs(total - expected_total) < 1000:
    print("\n✓ Очень близко (разница < 1000 AMD)")
else:
    print(f"\n✗ Разница {abs(total - expected_total):,.2f} AMD")

# Дополнительный анализ - какая разница в долге
print("\n" + "=" * 80)
print("АНАЛИЗ РАЗНИЦЫ В ДОЛГЕ")
print("=" * 80)
diff_debt = debt_total - expected_debt
print(f"Полный долг менеджера: {debt_total:,.2f} AMD")
print(f"Ожидаемый долг: {expected_debt:,.2f} AMD")
print(f"Разница: {diff_debt:,.2f} AMD")
print(f"\nЭто составляет {(diff_debt / debt_total) * 100:.2f}% от полного долга")

# Проверяем - возможно нужно исключить определенную группу?
cursor.execute("""
    SELECT 
        c.fGROUP,
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fSALESAGENTID = ?
    GROUP BY c.fGROUP
    HAVING SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) > 500000
    ORDER BY DebtFromDocs DESC
""", (manager_id,))

print(f"\nГруппы с долгом > 500k:")
print(f"{'Группа':<10} {'Долг':>20}")
print("-" * 35)
for row in cursor.fetchall():
    print(f"{row.fGROUP:<10} {float(row.DebtFromDocs):>20,.2f}")
    if abs(float(row.DebtFromDocs) - diff_debt) < 10000:
        print(f"  ^^^ ВОЗМОЖНО эту группу нужно ИСКЛЮЧИТЬ!")

conn.close()

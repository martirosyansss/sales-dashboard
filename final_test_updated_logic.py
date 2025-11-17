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

manager_code = 'A006/6'
customer_groups = ['002', '036']

# Загружаем назначения групп
try:
    with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
        group_assignments = json.load(f)
except:
    group_assignments = {}

cursor.execute("SELECT fID, fCODE, fNAME FROM SALESAGENTS WHERE fCODE = ?", manager_code)
manager_row = cursor.fetchone()
manager_id = manager_row.fID

# Находим ответственные группы для менеджера
responsible_groups = []
for group, managers in group_assignments.items():
    if manager_id in managers:
        responsible_groups.append(group)

print("=" * 80)
print(f"ФИНАЛЬНЫЙ ТЕСТ ОБНОВЛЕННОЙ ЛОГИКИ")
print("=" * 80)
print(f"Менеджер: {manager_code} - {manager_row.fNAME}")
print(f"ID: {manager_id}")
print(f"Ответственные группы из JSON: {responsible_groups if responsible_groups else 'НЕТ'}")

expected_debt = 6297356.55
expected_rest01 = -48220.11
expected_rest02 = -236762.19
expected_total = 6012374.25

# СЦЕНАРИЙ 1: БЕЗ группового назначения (как сейчас в JSON)
print("\n" + "=" * 80)
print("СЦЕНАРИЙ 1: Менеджер БЕЗ назначенных групп (текущее состояние JSON)")
print("=" * 80)

debt_query = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs,
        (
            SELECT ISNULL(SUM(r.fSUM), 0)
            FROM HIRESTCUSTOMERSSUM r
            WHERE r.fCUSTOMERID IN (
                SELECT DISTINCT doc2.fCUSTOMERID
                FROM DOCUMENTS doc2
                WHERE doc2.fSALESAGENTID = ?
            )
            AND r.fTYPE = '02'
        ) as RestType01,
        (
            SELECT ISNULL(SUM(r.fSUM), 0)
            FROM HIRESTCUSTOMERSSUM r
            WHERE r.fCUSTOMERID IN (
                SELECT DISTINCT doc2.fCUSTOMERID
                FROM DOCUMENTS doc2
                WHERE doc2.fSALESAGENTID = ?
            )
            AND r.fTYPE = '01'
        ) as RestType02
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    WHERE doc.fSALESAGENTID = ?
"""

cursor.execute(debt_query, (manager_id, manager_id, manager_id))
row = cursor.fetchone()
debt1 = float(row.DebtFromDocs)
rest01_1 = float(row.RestType01)
rest02_1 = float(row.RestType02)
total1 = debt1 + rest01_1 + rest02_1

print(f"Долг: {debt1:,.2f}")
print(f"Type01 (из Type02): {rest01_1:,.2f}")
print(f"Type02 (из Type01): {rest02_1:,.2f}")
print(f"ИТОГО: {total1:,.2f}")

# СЦЕНАРИЙ 2: С назначенными группами 002,036 
print("\n" + "=" * 80)
print("СЦЕНАРИЙ 2: Менеджер С назначенными группами 002,036")
print("=" * 80)

debt_query2 = """
    SELECT 
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs,
        (
            SELECT ISNULL(SUM(r.fSUM), 0)
            FROM HIRESTCUSTOMERSSUM r
            INNER JOIN CUSTOMERS c2 ON r.fCUSTOMERID = c2.fID
            WHERE r.fCUSTOMERID IN (
                SELECT DISTINCT doc2.fCUSTOMERID
                FROM DOCUMENTS doc2
                WHERE doc2.fSALESAGENTID = ?
            )
            AND r.fTYPE = '02'
            AND c2.fGROUP IN (?, ?)
        ) as RestType01,
        (
            SELECT ISNULL(SUM(r.fSUM), 0)
            FROM HIRESTCUSTOMERSSUM r
            INNER JOIN CUSTOMERS c2 ON r.fCUSTOMERID = c2.fID
            WHERE r.fCUSTOMERID IN (
                SELECT DISTINCT doc2.fCUSTOMERID
                FROM DOCUMENTS doc2
                WHERE doc2.fSALESAGENTID = ?
            )
            AND r.fTYPE = '01'
            AND c2.fGROUP IN (?, ?)
        ) as RestType02
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fSALESAGENTID = ?
        AND c.fGROUP IN (?, ?)
"""

cursor.execute(debt_query2, (manager_id, customer_groups[0], customer_groups[1],
                              manager_id, customer_groups[0], customer_groups[1],
                              manager_id, customer_groups[0], customer_groups[1]))
row = cursor.fetchone()
debt2 = float(row.DebtFromDocs)
rest01_2 = float(row.RestType01)
rest02_2 = float(row.RestType02)
total2 = debt2 + rest01_2 + rest02_2

print(f"Долг (только группы 002,036): {debt2:,.2f}")
print(f"Type01 (из Type02, группы 002,036): {rest01_2:,.2f}")
print(f"Type02 (из Type01, группы 002,036): {rest02_2:,.2f}")
print(f"ИТОГО: {total2:,.2f}")

# Сравнение с ожидаемыми
print("\n" + "=" * 80)
print("СРАВНЕНИЕ С ОЖИДАЕМЫМИ ЗНАЧЕНИЯМИ")
print("=" * 80)

scenarios = [
    ("Сценарий 1 (без групп)", total1),
    ("Сценарий 2 (с группами 002,036)", total2),
]

print(f"\n{'Сценарий':<35} {'Результат':>20} {'Ожидаемое':>20} {'Разница':>15} {'%':>8}")
print("-" * 100)
for name, value in scenarios:
    diff = abs(value - expected_total)
    percent = (diff / expected_total) * 100
    status = " ✓" if diff < 1000 else ""
    print(f"{name:<35} {value:>20,.2f} {expected_total:>20,.2f} {diff:>15,.2f} {percent:>7.2f}%{status}")

print("\n" + "=" * 80)
print("РЕКОМЕНДАЦИЯ")
print("=" * 80)
print(f"Чтобы получить ожидаемый результат {expected_total:,.2f} AMD:")
print(f"1. Добавить в group_manager_assignments.json:")
print(f'   "002": [{manager_id}],')
print(f'   "036": [{manager_id}]')
print(f"\n2. Остатки (HIRESTCUSTOMERSSUM) будут фильтроваться по группам 002,036")
print(f"3. Type01 и Type02 ПОМЕНЯНЫ МЕСТАМИ в новом коде")
print(f"\nТекущая разница в долге ({abs(debt2 - expected_debt):,.2f} AMD) может быть связана")
print(f"с дополнительными фильтрами или исключениями, которые не учтены.")

conn.close()

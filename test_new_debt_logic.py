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

# Загружаем менеджеров
cursor.execute("""
    SELECT fID, fCODE, fNAME
    FROM SALESAGENTS
    WHERE fCLOSED = 0
    ORDER BY fCODE
""")

managers = []
for row in cursor.fetchall():
    managers.append({
        'fID': row.fID,
        'fCODE': row.fCODE,
        'fNAME': row.fNAME
    })

print("=" * 80)
print("ОБНОВЛЕННЫЙ РАСЧЕТ ДОЛГА (с HIRESTCUSTOMERSSUM)")
print("=" * 80)
print(f"\nВсего активных менеджеров: {len(managers)}\n")

# Рассчитываем долг для каждого менеджера
for manager in managers[:15]:  # Первые 15 для теста
    manager_id = manager['fID']
    
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
                AND r.fTYPE = '01'
            ) as RestType01,
            (
                SELECT ISNULL(SUM(r.fSUM), 0)
                FROM HIRESTCUSTOMERSSUM r
                WHERE r.fCUSTOMERID IN (
                    SELECT DISTINCT doc2.fCUSTOMERID
                    FROM DOCUMENTS doc2
                    WHERE doc2.fSALESAGENTID = ?
                )
                AND r.fTYPE = '02'
            ) as RestType02
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fSALESAGENTID = ?
    """
    
    cursor.execute(debt_query, (manager_id, manager_id, manager_id))
    debt_row = cursor.fetchone()
    
    if debt_row:
        debt_from_docs = float(debt_row.DebtFromDocs) if debt_row.DebtFromDocs else 0
        rest_type_01 = float(debt_row.RestType01) if debt_row.RestType01 else 0
        rest_type_02 = float(debt_row.RestType02) if debt_row.RestType02 else 0
        total_debt = debt_from_docs + rest_type_01 + rest_type_02
        manager['Debt'] = total_debt
        manager['DebtDetails'] = {
            'from_docs': debt_from_docs,
            'rest_type_01': rest_type_01,
            'rest_type_02': rest_type_02
        }
    else:
        manager['Debt'] = 0
        manager['DebtDetails'] = None

# Сортируем по долгу
managers.sort(key=lambda x: x.get('Debt', 0), reverse=True)

print("{:<15} {:<40} {:>20}".format("КОД", "ИМЯ", "ДОЛГ (AMD)"))
print("-" * 80)

for m in managers[:15]:
    print(f"{m['fCODE']:<15} {m['fNAME']:<40} {m.get('Debt', 0):>20,.2f}")

print("\n" + "=" * 80)
print("ДЕТАЛИ ДЛЯ МЕНЕДЖЕРА A006/6 (Симонян Лиана)")
print("=" * 80)

simonian = next((m for m in managers if m['fCODE'] == 'A006/6'), None)
if simonian and simonian.get('DebtDetails'):
    details = simonian['DebtDetails']
    print(f"\nМенеджер: {simonian['fCODE']} - {simonian['fNAME']}")
    print(f"  Долг из документов: {details['from_docs']:>20,.2f} AMD")
    print(f"  Остатки Type 01:    {details['rest_type_01']:>20,.2f} AMD")
    print(f"  Остатки Type 02:    {details['rest_type_02']:>20,.2f} AMD")
    print(f"  {'-' * 51}")
    print(f"  ИТОГО:              {simonian['Debt']:>20,.2f} AMD")
    print(f"\n  Ожидаемая сумма:    {6012374.25:>20,.2f} AMD")
    print(f"  Разница:            {abs(simonian['Debt'] - 6012374.25):>20,.2f} AMD ({abs((simonian['Debt'] - 6012374.25) / 6012374.25 * 100):.2f}%)")

conn.close()
print("\n✓ Тест завершен успешно!")

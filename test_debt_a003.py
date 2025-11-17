"""
Тест расчета долга для менеджера A003 (Վերդոյան Նորայր)
Ожидаемый долг: 5,289,036.77 AMD
Текущий долг: 2,675,395.12 AMD
"""

import pyodbc
import json

# Подключение к БД
conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)

print("=" * 80)
print("ТЕСТ ДОЛГА ДЛЯ МЕНЕДЖЕРА A003 (Վերդոյան Նորայր)")
print("=" * 80)

try:
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    
    # 1. Получить ID менеджера A003
    cursor.execute("SELECT fID, fNAME FROM SALESAGENTS WHERE fCODE = 'A003'")
    manager = cursor.fetchone()
    
    if not manager:
        print("❌ Менеджер A003 не найден!")
        exit(1)
    
    manager_id = manager.fID
    manager_name = manager.fNAME
    
    print(f"\n✓ Менеджер: {manager_name} (ID={manager_id})")
    
    # 2. Загрузить назначенные группы из settings
    try:
        with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
            assignments = json.load(f)
    except:
        assignments = {}
    
    responsible_groups = []
    for group_code, manager_ids in assignments.items():
        if not isinstance(manager_ids, list):
            manager_ids = [manager_ids]
        if manager_id in manager_ids:
            responsible_groups.append(group_code)
    
    print(f"✓ Назначенные группы: {responsible_groups if responsible_groups else 'НЕТ - будем рассчитывать без фильтра'}")
    
    # 3. Расчет долга БЕЗ фильтра по группам (все клиенты менеджера)
    print("\n" + "=" * 80)
    print("РАСЧЕТ ДОЛГА БЕЗ ФИЛЬТРА ПО ГРУППАМ (ВСЕ КЛИЕНТЫ МЕНЕДЖЕРА)")
    print("=" * 80)
    
    query_all = """
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
    
    cursor.execute(query_all, (manager_id, manager_id, manager_id))
    row_all = cursor.fetchone()
    
    debt_docs_all = float(row_all.DebtFromDocs) if row_all.DebtFromDocs else 0
    rest_type01_all = float(row_all.RestType01) if row_all.RestType01 else 0
    rest_type02_all = float(row_all.RestType02) if row_all.RestType02 else 0
    total_debt_all = debt_docs_all + rest_type01_all + rest_type02_all
    
    print(f"\nDebtFromDocs: {debt_docs_all:,.2f} AMD")
    print(f"RestType01 (Type='02'): {rest_type01_all:,.2f} AMD")
    print(f"RestType02 (Type='01'): {rest_type02_all:,.2f} AMD")
    print(f"{'=' * 40}")
    print(f"ИТОГО БЕЗ ФИЛЬТРА: {total_debt_all:,.2f} AMD")
    
    # 4. Расчет долга С ФИЛЬТРОМ по назначенным группам
    print("\n" + "=" * 80)
    print(f"РАСЧЕТ ДОЛГА С ФИЛЬТРОМ ПО ГРУППАМ: {responsible_groups}")
    print("=" * 80)
    
    placeholders = ','.join(['?'] * len(responsible_groups))
    group_filter = f" AND c.fGROUP IN ({placeholders})"
    rest_group_filter = f" AND c2.fGROUP IN ({placeholders})"
    group_params = tuple(responsible_groups)
    
    query_filtered = f"""
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
                {rest_group_filter}
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
                {rest_group_filter}
            ) as RestType02
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fSALESAGENTID = ?
            {group_filter}
    """
    
    all_params = (manager_id,) + group_params + (manager_id,) + group_params + (manager_id,) + group_params
    cursor.execute(query_filtered, all_params)
    row_filtered = cursor.fetchone()
    
    debt_docs_filtered = float(row_filtered.DebtFromDocs) if row_filtered.DebtFromDocs else 0
    rest_type01_filtered = float(row_filtered.RestType01) if row_filtered.RestType01 else 0
    rest_type02_filtered = float(row_filtered.RestType02) if row_filtered.RestType02 else 0
    total_debt_filtered = debt_docs_filtered + rest_type01_filtered + rest_type02_filtered
    
    print(f"\nDebtFromDocs: {debt_docs_filtered:,.2f} AMD")
    print(f"RestType01 (Type='02'): {rest_type01_filtered:,.2f} AMD")
    print(f"RestType02 (Type='01'): {rest_type02_filtered:,.2f} AMD")
    print(f"{'=' * 40}")
    print(f"ИТОГО С ФИЛЬТРОМ: {total_debt_filtered:,.2f} AMD")
    
    # 5. Сравнение
    print("\n" + "=" * 80)
    print("СРАВНЕНИЕ")
    print("=" * 80)
    print(f"Ожидаемый долг:    5,289,036.77 AMD")
    print(f"Долг БЕЗ фильтра: {total_debt_all:,.2f} AMD")
    print(f"Долг С фильтром:  {total_debt_filtered:,.2f} AMD")
    print(f"Текущий в UI:      2,675,395.12 AMD")
    
    difference_expected = 5289036.77 - total_debt_all
    difference_filtered = total_debt_filtered - 2675395.12
    
    print(f"\nРазница с ожидаемым: {difference_expected:+,.2f} AMD")
    print(f"Разница фильтр vs UI: {difference_filtered:+,.2f} AMD")
    
    # 6. Проверка клиентов по группам
    print("\n" + "=" * 80)
    print("КЛИЕНТЫ МЕНЕДЖЕРА ПО ГРУППАМ")
    print("=" * 80)
    
    cursor.execute("""
        SELECT c.fGROUP, COUNT(DISTINCT c.fID) as CustomerCount
        FROM CUSTOMERS c
        INNER JOIN DOCUMENTS doc ON c.fID = doc.fCUSTOMERID
        WHERE doc.fSALESAGENTID = ?
        GROUP BY c.fGROUP
        ORDER BY c.fGROUP
    """, (manager_id,))
    
    print("\nВсе группы клиентов менеджера:")
    for row in cursor.fetchall():
        in_filter = "✓" if row.fGROUP in responsible_groups else "✗"
        print(f"  {in_filter} Группа {row.fGROUP}: {row.CustomerCount} клиентов")
    
    conn.close()
    
    print("\n" + "=" * 80)
    print("ВЫВОДЫ:")
    print("=" * 80)
    
    if abs(total_debt_all - 5289036.77) < 1000:
        print("✓ Долг БЕЗ фильтра соответствует ожидаемому!")
        print("❌ Проблема: фильтр по группам отсекает нужных клиентов!")
        print(f"   Решение: проверить правильность назначенных групп в group_manager_assignments.json")
    elif abs(total_debt_filtered - 2675395.12) < 1000:
        print("✓ Долг С фильтром соответствует текущему в UI")
        print(f"❌ Проблема: не хватает {5289036.77 - total_debt_filtered:,.2f} AMD")
        print("   Возможно нужно добавить больше групп в settings для этого менеджера")
    else:
        print("❓ Расчет не совпадает ни с ожидаемым, ни с текущим")
        print("   Нужна дополнительная проверка источника данных")
    
except Exception as e:
    print(f"\n❌ ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

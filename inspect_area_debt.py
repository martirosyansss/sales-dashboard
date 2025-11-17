#!/usr/bin/env python
"""Сравнение долга по территории с/без фильтра групп."""
import pyodbc
import json

AREA_CODE = '101'
DATE_FROM = '2025-11-01'
DATE_TO = '2025-11-30'

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

with open('group_manager_assignments.json', 'r', encoding='utf-8') as f:
    assignments = json.load(f)

managers = []
cursor.execute(
    """
    SELECT DISTINCT ag.fID, ag.fCODE, ag.fNAME
    FROM SALESAGENTS ag
    INNER JOIN SALESAGENTAREAS sa ON sa.fSALESAGENTID = ag.fID
    WHERE sa.fSALESAREA = ?
      AND ag.fCLOSED = 0
    ORDER BY ag.fCODE
    """,
    (AREA_CODE,)
)
for row in cursor.fetchall():
    managers.append({
        'id': row.fID,
        'code': row.fCODE,
        'name': row.fNAME,
        'groups': [g for g, ids in assignments.items() if isinstance(ids, list) and row.fID in ids]
    })

print(f"Территория {AREA_CODE}: {len(managers)} менеджеров")

area_debt_no_filter = 0
area_debt_with_filter = 0

for manager in managers:
    manager_id = manager['id']
    groups = manager['groups']

    # debt from docs without filter
    cursor.execute(
        """
        SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0)
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
        )
        """,
        (manager_id,)
    )
    debt_docs = float(cursor.fetchone()[0])

    cursor.execute(
        """
        SELECT 
            ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
            ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
        FROM HIRESTCUSTOMERSSUM r
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
        )
        """,
        (manager_id,)
    )
    row = cursor.fetchone()
    type01 = float(row.Type01 or 0)
    type02 = float(row.Type02 or 0)

    debt_no_filter = debt_docs - abs(type01) - abs(type02)
    area_debt_no_filter += debt_no_filter

    if not groups:
        print(f"{manager['code']:>6} {manager['name'][:20]:<20} | групп нет → вклад только без фильтра")
        continue

    placeholders = ','.join(['?'] * len(groups))
    group_filter = f" AND c.fGROUP IN ({placeholders})"
    params = (manager_id, *groups)

    cursor.execute(
        f"""
        SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0)
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
        )
        {group_filter}
        """,
        params,
    )
    debt_docs_filtered = float(cursor.fetchone()[0] or 0)

    cursor.execute(
        f"""
        SELECT 
            ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
            ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
        FROM HIRESTCUSTOMERSSUM r
        INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
        WHERE r.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
        )
        {group_filter}
        """,
        params,
    )
    row = cursor.fetchone()
    type01_f = float(row.Type01 or 0)
    type02_f = float(row.Type02 or 0)

    debt_with_filter = debt_docs_filtered - abs(type01_f) - abs(type02_f)
    area_debt_with_filter += debt_with_filter

    print(f"{manager['code']:>6} {manager['name'][:20]:<20} | групп: {len(groups):2d} | долг без фильтра {debt_no_filter:>12,.2f} | с фильтром {debt_with_filter:>12,.2f}")

print("\n=== Итог по территории 101 ===")
print(f"Долг без фильтра:  {area_debt_no_filter:,.2f} AMD")
print(f"Долг с фильтром:   {area_debt_with_filter:,.2f} AMD")
print(f"Разница:           {area_debt_no_filter - area_debt_with_filter:,.2f} AMD")

conn.close()

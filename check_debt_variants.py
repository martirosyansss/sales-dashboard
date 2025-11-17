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
print("Вариант 1: LEFT JOIN (текущий подход)")
print("=" * 80)
query1 = """
SELECT 
    sa.fCODE,
    sa.fNAME,
    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as TotalDebt
FROM SALESAGENTS sa
LEFT JOIN DOCUMENTS doc ON doc.fSALESAGENTID = sa.fID
LEFT JOIN HICUSTOMERSDEBT d ON d.fDEBTDOCISN = doc.fISN
LEFT JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
WHERE sa.fCODE = 'A006'
GROUP BY sa.fCODE, sa.fNAME
"""
cursor.execute(query1)
row = cursor.fetchone()
print(f"Total Debt: {row.TotalDebt:,.2f} AMD\n")

print("=" * 80)
print("Вариант 2: INNER JOIN (только документы с долгом)")
print("=" * 80)
query2 = """
SELECT 
    sa.fCODE,
    sa.fNAME,
    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as TotalDebt
FROM SALESAGENTS sa
INNER JOIN DOCUMENTS doc ON doc.fSALESAGENTID = sa.fID
INNER JOIN HICUSTOMERSDEBT d ON d.fDEBTDOCISN = doc.fISN
INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
WHERE sa.fCODE = 'A006'
GROUP BY sa.fCODE, sa.fNAME
"""
cursor.execute(query2)
row = cursor.fetchone()
print(f"Total Debt: {row.TotalDebt:,.2f} AMD\n")

print("=" * 80)
print("Вариант 3: Прямо из HICUSTOMERSDEBT через DOCUMENTS")
print("=" * 80)
query3 = """
SELECT 
    sa.fCODE,
    sa.fNAME,
    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as TotalDebt
FROM HICUSTOMERSDEBT d
INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
INNER JOIN SALESAGENTS sa ON doc.fSALESAGENTID = sa.fID
WHERE sa.fCODE = 'A006'
GROUP BY sa.fCODE, sa.fNAME
"""
cursor.execute(query3)
row = cursor.fetchone()
print(f"Total Debt: {row.TotalDebt:,.2f} AMD\n")

print("=" * 80)
print("Проверка: Количество документов для менеджера A006")
print("=" * 80)
cursor.execute("SELECT COUNT(*) as cnt FROM DOCUMENTS WHERE fSALESAGENTID = 12")
doc_count = cursor.fetchone().cnt
print(f"Всего документов: {doc_count}")

cursor.execute("SELECT COUNT(DISTINCT d.fDEBTDOCISN) as cnt FROM HICUSTOMERSDEBT d INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN WHERE doc.fSALESAGENTID = 12")
debt_doc_count = cursor.fetchone().cnt
print(f"Документов с долгом: {debt_doc_count}\n")

conn.close()

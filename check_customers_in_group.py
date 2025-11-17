import pyodbc

# Подключение к базе данных
conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()

# Запросить группу у пользователя
group_code = input("Введите код группы (например, 002 или 036): ").strip()

print(f"\n{'='*100}")
print(f"Клиенты в группе {group_code}")
print(f"{'='*100}\n")

# Запрос всех клиентов в группе
cursor.execute("""
    SELECT 
        c.fID AS CustomerID,
        c.fCODE AS CustomerCode,
        c.fNAME AS CustomerName,
        c.fGROUP AS GroupCode,
        c.fDIVISION AS Division,
        -- Проверяем есть ли продажи
        CASE WHEN EXISTS(SELECT 1 FROM SALES s WHERE s.fCUSTOMERID = c.fID AND s.fSTATE = 2) 
             THEN 'Да' 
             ELSE 'Нет' 
        END AS HasSales,
        -- Последняя продажа
        (SELECT TOP 1 s.fSALESAREA 
         FROM SALES s 
         WHERE s.fCUSTOMERID = c.fID AND s.fSTATE = 2 
         ORDER BY s.fDATE DESC) AS LastSalesArea,
        -- Считаем долг
        ISNULL(
            (SELECT SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END)
             FROM HICUSTOMERSDEBT d
             INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
             WHERE doc.fCUSTOMERID = c.fID), 0
        ) - 
        ABS(ISNULL(
            (SELECT SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END)
             FROM HIRESTCUSTOMERSSUM r
             WHERE r.fCUSTOMERID = c.fID), 0
        )) -
        ABS(ISNULL(
            (SELECT SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END)
             FROM HIRESTCUSTOMERSSUM r
             WHERE r.fCUSTOMERID = c.fID), 0
        )) AS Debt
    FROM CUSTOMERS c
    WHERE c.fGROUP = ?
    ORDER BY c.fNAME
""", (group_code,))

count = 0
total_debt = 0

print(f"{'ID':<8} {'Код':<10} {'Название клиента':<50} {'Дивизион':<10} {'Продажи':<10} {'Посл.SA':<10} {'Долг':>15}")
print(f"{'-'*8} {'-'*10} {'-'*50} {'-'*10} {'-'*10} {'-'*10} {'-'*15}")

for row in cursor.fetchall():
    count += 1
    debt = float(row.Debt) if row.Debt else 0
    total_debt += debt
    
    print(f"{row.CustomerID:<8} {row.CustomerCode:<10} {row.CustomerName[:50]:<50} {row.Division or 'N/A':<10} {row.HasSales:<10} {row.LastSalesArea or 'N/A':<10} {debt:>15,.2f}")

print(f"\n{'-'*100}")
print(f"Всего клиентов в группе {group_code}: {count}")
print(f"Общий долг: {total_debt:,.2f} ֏")
print(f"{'='*100}\n")

conn.close()

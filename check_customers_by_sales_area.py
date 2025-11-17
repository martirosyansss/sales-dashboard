import pyodbc
import json

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

# Загрузить назначения групп к Sales Areas
with open('sales_area_group_assignments.json', 'r', encoding='utf-8') as f:
    area_assignments = json.load(f)

# Показать доступные Sales Areas
print("\n" + "="*100)
print("Доступные Sales Areas:")
print("="*100)
for area, groups in area_assignments.items():
    print(f"  {area}: группы {', '.join(groups)}")

# Запросить Sales Area у пользователя
sales_area = input("\nВведите код Sales Area (например, 101 или 103): ").strip()

if sales_area not in area_assignments:
    print(f"\nОшибка: Sales Area {sales_area} не найден в назначениях!")
    conn.close()
    exit(1)

assigned_groups = area_assignments[sales_area]

print(f"\n{'='*100}")
print(f"Клиенты закрепленные за Sales Area {sales_area}")
print(f"Группы: {', '.join(assigned_groups)}")
print(f"{'='*100}\n")

# Построить SQL запрос с плейсхолдерами для групп
placeholders = ','.join('?' * len(assigned_groups))

cursor.execute(f"""
    SELECT 
        c.fID AS CustomerID,
        c.fCODE AS CustomerCode,
        c.fNAME AS CustomerName,
        c.fGROUP AS GroupCode,
        -- Проверяем есть ли продажи вообще
        CASE WHEN EXISTS(SELECT 1 FROM SALES s WHERE s.fCUSTOMERID = c.fID AND s.fSTATE = 2) 
             THEN 'Да' 
             ELSE 'Нет' 
        END AS HasSales,
        -- Проверяем есть ли продажи в этом Sales Area
        CASE WHEN EXISTS(SELECT 1 FROM SALES s WHERE s.fCUSTOMERID = c.fID AND s.fSTATE = 2 AND s.fSALESAREA = ?) 
             THEN 'Да' 
             ELSE 'Нет' 
        END AS HasSalesInArea,
        -- Последняя продажа в любом Sales Area
        (SELECT TOP 1 s.fSALESAREA 
         FROM SALES s 
         WHERE s.fCUSTOMERID = c.fID AND s.fSTATE = 2 
         ORDER BY s.fDATE DESC) AS LastSalesArea,
        -- Последняя дата продажи
        (SELECT TOP 1 CONVERT(VARCHAR(10), s.fDATE, 120)
         FROM SALES s 
         WHERE s.fCUSTOMERID = c.fID AND s.fSTATE = 2 
         ORDER BY s.fDATE DESC) AS LastSaleDate,
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
    WHERE c.fGROUP IN ({placeholders})
    ORDER BY c.fGROUP, c.fNAME
""", (sales_area, *assigned_groups))

count = 0
count_with_sales = 0
count_with_sales_in_area = 0
count_with_debt = 0
total_debt = 0

print(f"{'ID':<8} {'Код':<10} {'Группа':<8} {'Название клиента':<40} {'Прод.':<8} {'SA прод.':<10} {'Посл.SA':<10} {'Посл.дата':<12} {'Долг':>15}")
print(f"{'-'*8} {'-'*10} {'-'*8} {'-'*40} {'-'*8} {'-'*10} {'-'*10} {'-'*12} {'-'*15}")

for row in cursor.fetchall():
    count += 1
    debt = float(row.Debt) if row.Debt else 0
    total_debt += debt
    
    if row.HasSales == 'Да':
        count_with_sales += 1
    if row.HasSalesInArea == 'Да':
        count_with_sales_in_area += 1
    if debt > 0:
        count_with_debt += 1
    
    print(f"{row.CustomerID:<8} {row.CustomerCode:<10} {row.GroupCode:<8} {row.CustomerName[:40]:<40} {row.HasSales:<8} {row.HasSalesInArea:<10} {row.LastSalesArea or 'N/A':<10} {row.LastSaleDate or 'N/A':<12} {debt:>15,.2f}")

print(f"\n{'-'*140}")
print(f"\nСтатистика для Sales Area {sales_area} (группы: {', '.join(assigned_groups)}):")
print(f"  Всего закрепленных клиентов: {count}")
print(f"  Клиентов с продажами (любой SA): {count_with_sales}")
print(f"  Клиентов с продажами в SA {sales_area}: {count_with_sales_in_area}")
print(f"  Клиентов без продаж: {count - count_with_sales}")
print(f"  Клиентов с долгом > 0: {count_with_debt}")
print(f"  Общий долг: {total_debt:,.2f} ֏")
print(f"{'='*100}\n")

conn.close()

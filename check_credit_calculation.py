import pyodbc
from datetime import datetime, timedelta

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()

print("=" * 80)
print("ПРОВЕРКА РАСЧЁТА КРЕДИТОВ ДЛЯ ОДНОГО КЛИЕНТА")
print("=" * 80)

# Возьмём первого клиента из территории 101
cursor.execute("""
    SELECT TOP 1 c.fID, c.fNAME, c.fGROUP, csa.fSALESAREA
    FROM CUSTOMERS c
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = '101'
""")

customer = cursor.fetchone()
if customer:
    customer_id = customer.fID
    customer_name = customer.fNAME
    customer_group = customer.fGROUP
    sales_area = customer.fSALESAREA
    
    print(f"\nКлиент: {customer_name}")
    print(f"ID: {customer_id}")
    print(f"Группа: {customer_group}")
    print(f"Территория: {sales_area}")
    print()
    
    # Период: текущий месяц
    today = datetime.now()
    date_from = today.replace(day=1).strftime('%Y-%m-%d')
    last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    date_to = last_day.strftime('%Y-%m-%d')
    
    print(f"Период: {date_from} - {date_to}")
    print()
    
    # 1. Продажи по типам оплаты
    print("1. ПРОДАЖИ ПО ТИПАМ ОПЛАТЫ:")
    print("-" * 80)
    cursor.execute("""
        SELECT 
            ISNULL(fPAYTYPE, 'NULL') as PayType,
            COUNT(*) as SalesCount,
            SUM(fTOTALSUM) as TotalSum
        FROM SALES
        WHERE fCUSTOMERID = ?
            AND fDATE >= ?
            AND fDATE <= ?
            AND fSTATE = 2
        GROUP BY fPAYTYPE
        ORDER BY SUM(fTOTALSUM) DESC
    """, (customer_id, date_from, date_to))
    
    total_sales = 0
    credit_sales_type2 = 0
    credit_sales_type23 = 0
    
    for row in cursor.fetchall():
        pay_type = row.PayType
        count = row.SalesCount
        total = row.TotalSum or 0
        print(f"  Тип оплаты {pay_type}: {count} продаж, {total:,.2f} драм")
        total_sales += total
        if pay_type == '2':
            credit_sales_type2 += total
        if pay_type in ['2', '3']:
            credit_sales_type23 += total
    
    print()
    print(f"ИТОГО продаж: {total_sales:,.2f} драм")
    print(f"Кредиты (только тип 2): {credit_sales_type2:,.2f} драм")
    print(f"Кредиты (тип 2+3): {credit_sales_type23:,.2f} драм")
    print()
    
    # 2. Задолженность из HICUSTOMERSDEBT
    print("2. ЗАДОЛЖЕННОСТЬ (HICUSTOMERSDEBT):")
    print("-" * 80)
    cursor.execute("""
        SELECT 
            d.fDBCR,
            COUNT(*) as RecordCount,
            SUM(d.fSUM) as TotalSum
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        WHERE doc.fCUSTOMERID = ?
        GROUP BY d.fDBCR
    """, (customer_id,))
    
    debt_debit = 0
    debt_credit = 0
    
    for row in cursor.fetchall():
        dbcr = row.fDBCR
        count = row.RecordCount
        total = row.TotalSum or 0
        if dbcr == 'D':
            debt_debit = total
            print(f"  Дебет (D - долг клиента): {count} записей, {total:,.2f} драм")
        elif dbcr == 'C':
            debt_credit = total
            print(f"  Кредит (C - оплаты): {count} записей, {total:,.2f} драм")
    
    total_debt = debt_debit - debt_credit
    print()
    print(f"ИТОГО задолженность: {total_debt:,.2f} драм (Дебет - Кредит)")
    print()
    
    # 3. Что показывает API?
    print("3. ЧТО ДОЛЖЕН ПОКАЗЫВАТЬ API?")
    print("-" * 80)
    print("В зависимости от того, что означает 'Кредиты':")
    print(f"  a) Если 'Кредиты' = Продажи в кредит (тип 2): {credit_sales_type2:,.2f}")
    print(f"  b) Если 'Кредиты' = Продажи в кредит (тип 2+3): {credit_sales_type23:,.2f}")
    print(f"  c) Если 'Кредиты' = Задолженность клиентов: {total_debt:,.2f}")

conn.close()

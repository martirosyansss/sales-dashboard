import pyodbc
from datetime import datetime

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=192.168.1.3;'
    'DATABASE=SalesManagement;'
    'UID=garni;'
    'PWD=garni2023;'
    'TrustServerCertificate=yes;'
)

cursor = conn.cursor()

area_code = '106'
date_from = '2025-10-01'
date_to = '2025-10-31'

print("=" * 80)
print(f"ПРОВЕРКА КРЕДИТОВ ДЛЯ ТЕРРИТОРИИ {area_code} ЗА ОКТЯБРЬ 2025")
print("=" * 80)
print()

# 1. БЕЗ ФИЛЬТРОВ - базовый запрос
print("1. БЕЗ ФИЛЬТРОВ (базовый запрос):")
print("-" * 80)
cursor.execute("""
    SELECT 
        COUNT(DISTINCT s.fCUSTOMERID) as CustomerCount,
        COUNT(s.fISN) as SalesCount,
        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales,
        ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales
    FROM SALES s
    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
    WHERE csa.fSALESAREA = ?
        AND s.fSALESAREA = ?
        AND s.fDATE >= ?
        AND s.fDATE <= ?
        AND s.fSTATE = 2
""", (area_code, area_code, date_from, date_to))

row = cursor.fetchone()
base_customers = row.CustomerCount
base_sales_count = row.SalesCount
base_total = row.TotalSales
base_credit = row.CreditSales

print(f"Клиентов: {base_customers}")
print(f"Продаж: {base_sales_count}")
print(f"Всего: {base_total:,.2f} AMD")
print(f"Кредиты: {base_credit:,.2f} AMD")
print()

# 2. С фильтром исключённых клиентов
print("2. С ФИЛЬТРОМ ИСКЛЮЧЁННЫХ КЛИЕНТОВ:")
print("-" * 80)

# Загрузить список исключённых
cursor.execute("SELECT fCUSTOMERID FROM EXCLUDEDCUSTOMERS")
excluded_ids = [row.fCUSTOMERID for row in cursor.fetchall()]
print(f"Исключённых клиентов: {len(excluded_ids)}")

if excluded_ids:
    placeholders = ','.join(['?'] * len(excluded_ids))
    cursor.execute(f"""
        SELECT 
            COUNT(DISTINCT s.fCUSTOMERID) as CustomerCount,
            ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales,
            ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE csa.fSALESAREA = ?
            AND s.fSALESAREA = ?
            AND s.fDATE >= ?
            AND s.fDATE <= ?
            AND s.fSTATE = 2
            AND s.fCUSTOMERID NOT IN ({placeholders})
    """, (area_code, area_code, date_from, date_to) + tuple(excluded_ids))
    
    row = cursor.fetchone()
    excl_customers = row.CustomerCount
    excl_total = row.TotalSales
    excl_credit = row.CreditSales
    
    print(f"Клиентов: {excl_customers} (было {base_customers})")
    print(f"Всего: {excl_total:,.2f} AMD (было {base_total:,.2f})")
    print(f"Кредиты: {excl_credit:,.2f} AMD (было {base_credit:,.2f})")
    print(f"Потеряно кредитов: {base_credit - excl_credit:,.2f} AMD")
else:
    excl_credit = base_credit
    print("Нет исключённых клиентов")

print()

# 3. С фильтром продуктовых групп
print("3. С ФИЛЬТРОМ ПРОДУКТОВЫХ ГРУПП:")
print("-" * 80)

# Проверим, есть ли selected_product_groups.json
import json
import os

if os.path.exists('selected_product_groups.json'):
    with open('selected_product_groups.json', 'r', encoding='utf-8') as f:
        selected_groups = json.load(f)
    print(f"Выбранных продуктовых групп: {len(selected_groups)}")
    
    if selected_groups:
        placeholders_pg = ','.join(['?'] * len(selected_groups))
        placeholders_excl = ','.join(['?'] * len(excluded_ids))
        
        cursor.execute(f"""
            SELECT 
                COUNT(DISTINCT s.fCUSTOMERID) as CustomerCount,
                ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales,
                ISNULL(SUM(CASE WHEN s.fPAYTYPE IN (2, 3) THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE csa.fSALESAREA = ?
                AND s.fSALESAREA = ?
                AND s.fDATE >= ?
                AND s.fDATE <= ?
                AND s.fSTATE = 2
                AND s.fCUSTOMERID NOT IN ({placeholders_excl})
                AND EXISTS (
                    SELECT 1 FROM SALEDOCDETAILS sd
                    INNER JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
                    WHERE sd.fISN = s.fISN AND p.fGROUP IN ({placeholders_pg})
                )
        """, (area_code, area_code, date_from, date_to) + tuple(excluded_ids) + tuple(selected_groups))
        
        row = cursor.fetchone()
        pg_customers = row.CustomerCount
        pg_total = row.TotalSales
        pg_credit = row.CreditSales
        
        print(f"Клиентов: {pg_customers} (было {excl_customers})")
        print(f"Всего: {pg_total:,.2f} AMD (было {excl_total:,.2f})")
        print(f"Кредиты: {pg_credit:,.2f} AMD (было {excl_credit:,.2f})")
        print(f"Потеряно кредитов: {excl_credit - pg_credit:,.2f} AMD")
    else:
        print("Нет выбранных продуктовых групп")
else:
    print("Файл selected_product_groups.json не найден")

print()
print("=" * 80)
print("ИТОГО:")
print("=" * 80)
print(f"БЕЗ фильтров: {base_credit:,.2f} AMD")
print(f"С фильтрами: {pg_credit if 'pg_credit' in locals() else excl_credit:,.2f} AMD")
print(f"Потеряно: {base_credit - (pg_credit if 'pg_credit' in locals() else excl_credit):,.2f} AMD")

conn.close()

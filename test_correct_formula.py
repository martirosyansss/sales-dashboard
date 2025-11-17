"""
ПРАВИЛЬНАЯ формула долга как в WindowsFormsApp1
GetCustomerDebtsInRangeAsync - Start Debt и End Debt
"""
import pyodbc
from datetime import datetime

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

manager_id = 3169  # Վերդոյան Նորայր
groups = ['036', '002']
target_debt = 5_289_036.77

# Используем текущую дату как endDate (на сегодня какой долг)
end_date = datetime.now()
# startDate можно взять очень давно (например начало времен = 1900-01-01)
start_date = datetime(1900, 1, 1)

print("=" * 80)
print(f"ПРАВИЛЬНАЯ ФОРМУЛА ДОЛГА (как в WindowsFormsApp1)")
print("=" * 80)
print(f"Менеджер ID: {manager_id}")
print(f"Группы: {', '.join(groups)}")
print(f"Целевой долг: {target_debt:,.2f} AMD")
print(f"Дата начала: {start_date.strftime('%Y-%m-%d')}")
print(f"Дата конца: {end_date.strftime('%Y-%m-%d')}")

placeholders = ','.join(['?'] * len(groups))

# ПРАВИЛЬНАЯ ФОРМУЛА из WindowsFormsApp1:
# Start Debt = SUM(долгов ДО startDate)
# End Debt = SUM(долгов ДО endDate)
# Current Debt = End Debt (долг на текущую дату)

query = f"""
    SELECT 
        c.fID,
        c.fNAME,
        c.fGROUP,
        -- Start Debt: долг ДО startDate
        ISNULL(SUM(CASE 
            WHEN doc.fDATE < ? THEN 
                CASE WHEN d.fDBCR = 'C' THEN -d.fSUM ELSE d.fSUM END
            ELSE 0 
        END), 0) as StartDebt,
        -- End Debt: долг ДО endDate (текущий долг)
        ISNULL(SUM(CASE 
            WHEN doc.fDATE < ? THEN 
                CASE WHEN d.fDBCR = 'C' THEN -d.fSUM ELSE d.fSUM END
            ELSE 0 
        END), 0) as EndDebt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID 
        FROM SALES 
        WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
    GROUP BY c.fID, c.fNAME, c.fGROUP
    HAVING SUM(CASE 
        WHEN doc.fDATE < ? THEN 
            CASE WHEN d.fDBCR = 'C' THEN -d.fSUM ELSE d.fSUM END
        ELSE 0 
    END) > 0
    ORDER BY EndDebt DESC
"""

params = (start_date, end_date, manager_id) + tuple(groups) + (end_date,)
cursor.execute(query, params)

print(f"\n{'Клиент':<40} {'Группа':<8} {'Start Debt':<18} {'End Debt (текущий)':<18}")
print("-" * 90)

total_end_debt = 0
customer_count = 0

for row in cursor.fetchall()[:10]:  # Топ-10 клиентов
    start_debt = float(row.StartDebt) if row.StartDebt else 0
    end_debt = float(row.EndDebt) if row.EndDebt else 0
    total_end_debt += end_debt
    customer_count += 1
    
    print(f"{row.fNAME[:38]:<40} {row.fGROUP:<8} {start_debt:>16,.2f} {end_debt:>16,.2f}")

# Получить общий долг
query_total = f"""
    SELECT 
        ISNULL(SUM(CASE 
            WHEN doc.fDATE < ? THEN 
                CASE WHEN d.fDBCR = 'C' THEN -d.fSUM ELSE d.fSUM END
            ELSE 0 
        END), 0) as TotalEndDebt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID 
        FROM SALES 
        WHERE fSALESAGENTID = ?
    )
    AND c.fGROUP IN ({placeholders})
"""

cursor.execute(query_total, (end_date, manager_id) + tuple(groups))
total_debt = float(cursor.fetchone().TotalEndDebt)

print("-" * 90)
print(f"{'ИТОГО:':<40} {'':<8} {'':<18} {total_debt:>16,.2f}")

print(f"\n{'='*80}")
print("РЕЗУЛЬТАТ:")
print(f"{'='*80}")
print(f"Текущий долг (End Debt): {total_debt:,.2f} AMD")
print(f"Целевой долг: {target_debt:,.2f} AMD")
print(f"Отклонение: {abs(total_debt - target_debt):,.2f} AMD ({abs(total_debt - target_debt)/target_debt*100:.2f}%)")

if abs(total_debt - target_debt) / target_debt < 0.05:  # Меньше 5%
    print(f"\n✅ ОТЛИЧНО! Отклонение меньше 5%")
else:
    print(f"\n⚠️ Отклонение больше 5%")
    
    # Попробуем без деления на 2
    print(f"\nПопробуем старую формулу для сравнения:")
    
    query_old = f"""
        SELECT 
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as NetDebt
        FROM HICUSTOMERSDEBT d
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = ?
        )
        AND c.fGROUP IN ({placeholders})
    """
    
    cursor.execute(query_old, (manager_id,) + tuple(groups))
    old_debt = float(cursor.fetchone().NetDebt)
    
    print(f"  Старая формула (D-C): {old_debt:,.2f} AMD - откл {abs(old_debt-target_debt)/target_debt*100:.1f}%")

conn.close()

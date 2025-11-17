"""
Подбор групп для достижения целевого долга 5,289,036.77 AMD
"""
import pyodbc

conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=192.168.1.3;"
    "DATABASE=SalesManagement;"
    "UID=garni;"
    "PWD=garni2023;"
    "TrustServerCertificate=yes;"
)
cursor = conn.cursor()

manager_id = 3169
target_debt = 5_289_036.77

# Получить долг по группам
query = """
    SELECT 
        c.fGROUP,
        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as NetDebt
    FROM HICUSTOMERSDEBT d
    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
    WHERE doc.fCUSTOMERID IN (
        SELECT DISTINCT fCUSTOMERID
        FROM SALES
        WHERE fSALESAGENTID = ?
    )
    GROUP BY c.fGROUP
    HAVING SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) > 0
    ORDER BY NetDebt DESC
"""

cursor.execute(query, (manager_id,))
groups_debt = [(row.fGROUP, float(row.NetDebt)) for row in cursor.fetchall()]

print("=" * 80)
print(f"ПОДБОР ГРУПП ДЛЯ ЦЕЛЕВОГО ДОЛГА: {target_debt:,.2f} AMD")
print("=" * 80)

# Пробуем разные комбинации
cumulative = 0
selected_groups = []

print(f"\n{'Группа':<10} {'Долг':<20} {'Накопительно':<20} {'% от цели':<15}")
print("-" * 80)

for group, debt in groups_debt:
    cumulative += debt
    selected_groups.append(group)
    percent = (cumulative / target_debt) * 100
    
    marker = ""
    if abs(cumulative - target_debt) < target_debt * 0.02:  # В пределах 2%
        marker = " ✅ БЛИЗКО!"
    
    print(f"{group:<10} {debt:>18,.2f} {cumulative:>18,.2f} {percent:>13,.1f}%{marker}")
    
    if cumulative >= target_debt * 0.98:  # Достигли 98% от цели
        break

print("-" * 80)
print(f"\nРЕКОМЕНДУЕМЫЕ ГРУППЫ: {', '.join(selected_groups)}")
print(f"Итоговый долг: {cumulative:,.2f} AMD")
print(f"Целевой долг:  {target_debt:,.2f} AMD")
print(f"Разница:       {abs(cumulative - target_debt):,.2f} AMD ({abs(cumulative - target_debt)/target_debt*100:.2f}%)")

conn.close()

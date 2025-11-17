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

# Менеджер 3169 (Վերդոյան Նորայր) - группы 036, 002
cursor.execute("""
    SELECT AVG(CAST(cnt as FLOAT)) as AvgRecords 
    FROM (
        SELECT fDEBTDOCISN, COUNT(*) as cnt 
        FROM HICUSTOMERSDEBT d 
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN 
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID 
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = 3169
        ) 
        AND c.fGROUP IN ('036', '002') 
        GROUP BY fDEBTDOCISN
    ) t
""")
result = cursor.fetchone()
avg_3169 = result[0]

# Менеджер 9 (Անդրանիկ Անտոնյան) - все группы
cursor.execute("""
    SELECT AVG(CAST(cnt as FLOAT)) as AvgRecords 
    FROM (
        SELECT fDEBTDOCISN, COUNT(*) as cnt 
        FROM HICUSTOMERSDEBT d 
        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN 
        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID 
        WHERE doc.fCUSTOMERID IN (
            SELECT DISTINCT fCUSTOMERID FROM SALES WHERE fSALESAGENTID = 9
        ) 
        GROUP BY fDEBTDOCISN
    ) t
""")
result = cursor.fetchone()
avg_9 = result[0]

print("="*80)
print("СРАВНЕНИЕ ДУБЛИРОВАНИЯ ДОЛГА")
print("="*80)
print(f"\nМенеджер 3169 (Վերդոյան Նորայր, группы 036+002):")
print(f"  Среднее записей на документ: {avg_3169:.2f}")
if avg_3169 >= 1.9:
    print(f"  ✅ ДУБЛИРУЕТСЯ - нужно делить на 2")
else:
    print(f"  ⚠️ НЕ дублируется - НЕ делить на 2")

print(f"\nМенеджер 9 (Անդրանիկ Անտոնյան, все группы):")
print(f"  Среднее записей на документ: {avg_9:.2f}")
if avg_9 >= 1.9:
    print(f"  ✅ ДУБЛИРУЕТСЯ - нужно делить на 2")
else:
    print(f"  ⚠️ НЕ дублируется - НЕ делить на 2")

print(f"\n{'='*80}")
print("ВЫВОД:")
print(f"{'='*80}")

if abs(avg_3169 - avg_9) < 0.1:
    print("\n✅ Оба менеджера имеют одинаковую структуру долга")
    print("   Деление на 2 должно применяться одинаково")
else:
    print("\n⚠️ У менеджеров РАЗНАЯ структура долга!")
    print("   Возможно нужны индивидуальные настройки")

target = 5_289_036.77
current_debt_no_div = 4_450_458.49

print(f"\nДЛЯ МЕНЕДЖЕРА 3169:")
print(f"  Ожидаемый долг: {target:,.2f} AMD")
print(f"  Текущий долг: {current_debt_no_div:,.2f} AMD - отклонение {abs(current_debt_no_div-target)/target*100:.1f}%")

conn.close()

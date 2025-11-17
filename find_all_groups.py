from app_v2 import db

# Посмотреть все уникальные группы товаров
print("=== Все группы товаров (PRODUCTS.fGROUP) ===")
product_groups = db.execute_query("""
    SELECT 
        ISNULL(fGROUP, '(пустая)') as GroupCode,
        COUNT(*) as ProductCount
    FROM PRODUCTS
    GROUP BY fGROUP
    ORDER BY GroupCode
""")

print(f"Всего групп: {len(product_groups)}\n")
for g in product_groups:
    print(f"  {g['GroupCode']}: {g['ProductCount']} товаров")

# Посмотреть группы скидок товаров
print("\n=== Группы скидок товаров (PRODUCTS.fDISCOUNTGROUP) ===")
discount_groups = db.execute_query("""
    SELECT 
        ISNULL(fDISCOUNTGROUP, '(пустая)') as DiscountGroup,
        COUNT(*) as ProductCount
    FROM PRODUCTS
    GROUP BY fDISCOUNTGROUP
    ORDER BY DiscountGroup
""")

print(f"Всего групп скидок: {len(discount_groups)}\n")
for g in discount_groups:
    print(f"  {g['DiscountGroup']}: {g['ProductCount']} товаров")

# Посмотреть группы клиентов
print("\n=== Все группы клиентов (CUSTOMERS.fGROUP) ===")
customer_groups = db.execute_query("""
    SELECT 
        ISNULL(fGROUP, '(пустая)') as GroupCode,
        COUNT(*) as CustomerCount
    FROM CUSTOMERS
    WHERE fGROUP IS NOT NULL AND fGROUP != ''
    GROUP BY fGROUP
    ORDER BY GroupCode
""")

print(f"Всего групп клиентов: {len(customer_groups)}\n")
for g in customer_groups:
    print(f"  {g['GroupCode']}: {g['CustomerCount']} клиентов")

# Искать группу "000"
print("\n=== Поиск группы '000' ===")
# В товарах
prod_000 = db.execute_query("""
    SELECT COUNT(*) as cnt FROM PRODUCTS WHERE fGROUP = '000'
""")
print(f"Товаров с группой '000': {prod_000[0]['cnt']}")

# В клиентах
cust_000 = db.execute_query("""
    SELECT COUNT(*) as cnt FROM CUSTOMERS WHERE fGROUP = '000'
""")
print(f"Клиентов с группой '000': {cust_000[0]['cnt']}")

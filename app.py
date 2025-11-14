from flask import Flask, render_template, jsonify, request, redirect, url_for
from database import get_database
from datetime import datetime, timedelta
import json

app = Flask(__name__)
app.secret_key = 'sales_management_secret_key_2025'

# Get database instance
db = get_database()

# ===========================
# DASHBOARD - Главная страница
# ===========================

@app.route('/')
def index():
    """Главная страница - Dashboard с графиками и аналитикой"""
    return render_template('dashboard.html')

@app.route('/api/dashboard/stats')
def dashboard_stats():
    """API: Получить статистику для дашборда"""
    try:
        stats = {}
        
        # Общая выручка
        query = "SELECT ISNULL(SUM(TotalAmount), 0) FROM Sales WHERE Status='Completed'"
        result = db.execute_query(query)
        stats['total_revenue'] = float(result[0][0]) if result else 0
        
        # Выручка за текущий месяц
        query = """SELECT ISNULL(SUM(TotalAmount), 0) FROM Sales 
                   WHERE Status='Completed' AND MONTH(SaleDate) = MONTH(GETDATE()) 
                   AND YEAR(SaleDate) = YEAR(GETDATE())"""
        result = db.execute_query(query)
        stats['monthly_revenue'] = float(result[0][0]) if result else 0
        
        # Выручка за прошлый месяц
        query = """SELECT ISNULL(SUM(TotalAmount), 0) FROM Sales 
                   WHERE Status='Completed' AND MONTH(SaleDate) = MONTH(DATEADD(MONTH, -1, GETDATE())) 
                   AND YEAR(SaleDate) = YEAR(DATEADD(MONTH, -1, GETDATE()))"""
        result = db.execute_query(query)
        prev_month_revenue = float(result[0][0]) if result else 0
        stats['revenue_growth'] = ((stats['monthly_revenue'] - prev_month_revenue) / prev_month_revenue * 100) if prev_month_revenue > 0 else 0
        
        # Количество продаж
        query = "SELECT COUNT(*) FROM Sales WHERE Status='Completed'"
        result = db.execute_query(query)
        stats['total_sales'] = result[0][0] if result else 0
        
        # Количество клиентов
        query = "SELECT COUNT(*) FROM Customers"
        result = db.execute_query(query)
        stats['total_customers'] = result[0][0] if result else 0
        
        # Количество продуктов
        query = "SELECT COUNT(*) FROM Products"
        result = db.execute_query(query)
        stats['total_products'] = result[0][0] if result else 0
        
        # Средний чек
        query = "SELECT AVG(TotalAmount) FROM Sales WHERE Status='Completed'"
        result = db.execute_query(query)
        stats['average_order'] = float(result[0][0]) if result and result[0][0] else 0
        
        # Продажи ожидающие обработки
        query = "SELECT COUNT(*) FROM Sales WHERE Status='Pending'"
        result = db.execute_query(query)
        stats['pending_sales'] = result[0][0] if result else 0
        
        # Товары заканчивающиеся на складе (меньше 10)
        query = "SELECT COUNT(*) FROM Products WHERE StockQuantity > 0 AND StockQuantity <= 10"
        result = db.execute_query(query)
        stats['low_stock_products'] = result[0][0] if result else 0
        
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/sales-chart')
def sales_chart():
    """API: Данные для графика продаж за последние 12 месяцев"""
    try:
        query = """
            SELECT 
                YEAR(SaleDate) as Year,
                MONTH(SaleDate) as Month,
                SUM(TotalAmount) as Revenue,
                COUNT(*) as OrderCount
            FROM Sales
            WHERE Status='Completed' 
                AND SaleDate >= DATEADD(MONTH, -12, GETDATE())
            GROUP BY YEAR(SaleDate), MONTH(SaleDate)
            ORDER BY YEAR(SaleDate), MONTH(SaleDate)
        """
        results = db.execute_query(query)
        
        months = []
        revenue = []
        orders = []
        
        if results:
            for row in results:
                month_name = datetime(row[0], row[1], 1).strftime('%b %Y')
                months.append(month_name)
                revenue.append(float(row[2]))
                orders.append(row[3])
        
        return jsonify({
            'labels': months,
            'revenue': revenue,
            'orders': orders
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/top-products')
def top_products():
    """API: Топ-10 продаваемых товаров"""
    try:
        query = """
            SELECT TOP 10
                p.ProductName,
                SUM(sd.Quantity) as TotalSold,
                SUM(sd.Subtotal) as Revenue
            FROM Products p
            JOIN SaleDetails sd ON p.ProductID = sd.ProductID
            JOIN Sales s ON sd.SaleID = s.SaleID
            WHERE s.Status = 'Completed'
            GROUP BY p.ProductName
            ORDER BY Revenue DESC
        """
        results = db.execute_query(query)
        
        products = []
        sold = []
        revenue = []
        
        if results:
            for row in results:
                products.append(row[0])
                sold.append(row[1])
                revenue.append(float(row[2]))
        
        return jsonify({
            'products': products,
            'sold': sold,
            'revenue': revenue
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/category-distribution')
def category_distribution():
    """API: Распределение продаж по категориям"""
    try:
        query = """
            SELECT 
                ISNULL(p.Category, 'Без категории') as Category,
                SUM(sd.Subtotal) as Revenue
            FROM Products p
            JOIN SaleDetails sd ON p.ProductID = sd.ProductID
            JOIN Sales s ON sd.SaleID = s.SaleID
            WHERE s.Status = 'Completed'
            GROUP BY p.Category
            ORDER BY Revenue DESC
        """
        results = db.execute_query(query)
        
        categories = []
        values = []
        
        if results:
            for row in results:
                categories.append(row[0])
                values.append(float(row[1]))
        
        return jsonify({
            'categories': categories,
            'values': values
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ===========================
# CUSTOMERS - Управление клиентами
# ===========================

@app.route('/customers')
def customers():
    """Страница управления клиентами"""
    return render_template('customers.html')

@app.route('/api/customers')
def get_customers():
    """API: Получить список всех клиентов"""
    try:
        query = """
            SELECT 
                c.CustomerID,
                c.CustomerName,
                c.Email,
                c.Phone,
                c.City,
                c.Country,
                COUNT(s.SaleID) as TotalOrders,
                ISNULL(SUM(CASE WHEN s.Status='Completed' THEN s.TotalAmount ELSE 0 END), 0) as TotalSpent
            FROM Customers c
            LEFT JOIN Sales s ON c.CustomerID = s.CustomerID
            GROUP BY c.CustomerID, c.CustomerName, c.Email, c.Phone, c.City, c.Country
            ORDER BY TotalSpent DESC
        """
        results = db.execute_query(query)
        
        customers = []
        if results:
            for row in results:
                customers.append({
                    'id': row[0],
                    'name': row[1],
                    'email': row[2] or '',
                    'phone': row[3] or '',
                    'city': row[4] or '',
                    'country': row[5] or '',
                    'orders': row[6],
                    'spent': float(row[7])
                })
        
        return jsonify(customers)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/customers/<int:customer_id>')
def get_customer(customer_id):
    """API: Получить информацию о конкретном клиенте"""
    try:
        query = "SELECT * FROM Customers WHERE CustomerID = ?"
        result = db.execute_query(query, (customer_id,))
        
        if result and len(result) > 0:
            row = result[0]
            return jsonify({
                'id': row[0],
                'name': row[1],
                'email': row[2] or '',
                'phone': row[3] or '',
                'address': row[4] or '',
                'city': row[5] or '',
                'country': row[6] or ''
            })
        return jsonify({'error': 'Customer not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/customers', methods=['POST'])
def add_customer():
    """API: Добавить нового клиента"""
    try:
        data = request.json
        query = """INSERT INTO Customers (CustomerName, Email, Phone, Address, City, Country) 
                   VALUES (?, ?, ?, ?, ?, ?)"""
        params = (data['name'], data.get('email'), data.get('phone'), 
                 data.get('address'), data.get('city'), data.get('country'))
        
        if db.execute_non_query(query, params):
            return jsonify({'success': True, 'message': 'Customer added successfully'})
        return jsonify({'error': 'Failed to add customer'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/customers/<int:customer_id>', methods=['PUT'])
def update_customer(customer_id):
    """API: Обновить данные клиента"""
    try:
        data = request.json
        query = """UPDATE Customers 
                   SET CustomerName=?, Email=?, Phone=?, Address=?, City=?, Country=? 
                   WHERE CustomerID=?"""
        params = (data['name'], data.get('email'), data.get('phone'), 
                 data.get('address'), data.get('city'), data.get('country'), customer_id)
        
        if db.execute_non_query(query, params):
            return jsonify({'success': True, 'message': 'Customer updated successfully'})
        return jsonify({'error': 'Failed to update customer'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/customers/<int:customer_id>', methods=['DELETE'])
def delete_customer(customer_id):
    """API: Удалить клиента"""
    try:
        query = "DELETE FROM Customers WHERE CustomerID=?"
        if db.execute_non_query(query, (customer_id,)):
            return jsonify({'success': True, 'message': 'Customer deleted successfully'})
        return jsonify({'error': 'Failed to delete customer'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ===========================
# PRODUCTS - Управление товарами
# ===========================

@app.route('/products')
def products():
    """Страница управления товарами"""
    return render_template('products.html')

@app.route('/api/products')
def get_products():
    """API: Получить список всех товаров"""
    try:
        query = """
            SELECT 
                p.ProductID,
                p.ProductName,
                p.Category,
                p.UnitPrice,
                p.StockQuantity,
                p.Description,
                ISNULL(SUM(sd.Quantity), 0) as TotalSold
            FROM Products p
            LEFT JOIN SaleDetails sd ON p.ProductID = sd.ProductID
            LEFT JOIN Sales s ON sd.SaleID = s.SaleID AND s.Status='Completed'
            GROUP BY p.ProductID, p.ProductName, p.Category, p.UnitPrice, p.StockQuantity, p.Description
            ORDER BY p.ProductName
        """
        results = db.execute_query(query)
        
        products = []
        if results:
            for row in results:
                products.append({
                    'id': row[0],
                    'name': row[1],
                    'category': row[2] or '',
                    'price': float(row[3]),
                    'stock': row[4],
                    'description': row[5] or '',
                    'sold': row[6]
                })
        
        return jsonify(products)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/products', methods=['POST'])
def add_product():
    """API: Добавить новый товар"""
    try:
        data = request.json
        query = """INSERT INTO Products (ProductName, Category, UnitPrice, StockQuantity, Description) 
                   VALUES (?, ?, ?, ?, ?)"""
        params = (data['name'], data.get('category'), data['price'], 
                 data.get('stock', 0), data.get('description'))
        
        if db.execute_non_query(query, params):
            return jsonify({'success': True, 'message': 'Product added successfully'})
        return jsonify({'error': 'Failed to add product'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/products/<int:product_id>', methods=['PUT'])
def update_product(product_id):
    """API: Обновить данные товара"""
    try:
        data = request.json
        query = """UPDATE Products 
                   SET ProductName=?, Category=?, UnitPrice=?, StockQuantity=?, Description=? 
                   WHERE ProductID=?"""
        params = (data['name'], data.get('category'), data['price'], 
                 data.get('stock', 0), data.get('description'), product_id)
        
        if db.execute_non_query(query, params):
            return jsonify({'success': True, 'message': 'Product updated successfully'})
        return jsonify({'error': 'Failed to update product'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/products/<int:product_id>', methods=['DELETE'])
def delete_product(product_id):
    """API: Удалить товар"""
    try:
        query = "DELETE FROM Products WHERE ProductID=?"
        if db.execute_non_query(query, (product_id,)):
            return jsonify({'success': True, 'message': 'Product deleted successfully'})
        return jsonify({'error': 'Failed to delete product'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ===========================
# SALES - Управление продажами
# ===========================

@app.route('/sales')
def sales():
    """Страница управления продажами"""
    return render_template('sales.html')

@app.route('/api/sales')
def get_sales():
    """API: Получить список продаж"""
    try:
        query = """
            SELECT 
                s.SaleID,
                c.CustomerName,
                s.SaleDate,
                s.TotalAmount,
                s.Status,
                COUNT(sd.SaleDetailID) as ItemCount
            FROM Sales s
            LEFT JOIN Customers c ON s.CustomerID = c.CustomerID
            LEFT JOIN SaleDetails sd ON s.SaleID = sd.SaleID
            GROUP BY s.SaleID, c.CustomerName, s.SaleDate, s.TotalAmount, s.Status
            ORDER BY s.SaleDate DESC
        """
        results = db.execute_query(query)
        
        sales = []
        if results:
            for row in results:
                sales.append({
                    'id': row[0],
                    'customer': row[1] or 'Unknown',
                    'date': row[2].strftime('%Y-%m-%d %H:%M') if row[2] else '',
                    'total': float(row[3]),
                    'status': row[4],
                    'items': row[5]
                })
        
        return jsonify(sales)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ===========================
# ANALYTICS - Аналитика и отчеты
# ===========================

@app.route('/analytics')
def analytics():
    """Страница аналитики и отчетов"""
    return render_template('analytics.html')

@app.route('/api/analytics/customer-problems')
def customer_problems():
    """API: Проблемные клиенты - неактивные, без покупок"""
    try:
        problems = {}
        
        # Клиенты без покупок
        query = """
            SELECT c.CustomerID, c.CustomerName, c.Email, c.Phone, c.City,
                   DATEDIFF(DAY, c.CreatedDate, GETDATE()) as DaysSinceRegistration
            FROM Customers c
            LEFT JOIN Sales s ON c.CustomerID = s.CustomerID
            WHERE s.SaleID IS NULL
            ORDER BY c.CreatedDate DESC
        """
        results = db.execute_query(query)
        no_purchases = []
        if results:
            for row in results:
                no_purchases.append({
                    'id': row[0],
                    'name': row[1],
                    'email': row[2] or '',
                    'phone': row[3] or '',
                    'city': row[4] or '',
                    'days_since_registration': row[5]
                })
        problems['no_purchases'] = no_purchases
        
        # Неактивные клиенты (более 90 дней без покупок)
        query = """
            SELECT c.CustomerID, c.CustomerName, c.Email, MAX(s.SaleDate) as LastPurchase,
                   DATEDIFF(DAY, MAX(s.SaleDate), GETDATE()) as DaysInactive,
                   COUNT(s.SaleID) as TotalOrders
            FROM Customers c
            JOIN Sales s ON c.CustomerID = s.CustomerID
            WHERE s.Status = 'Completed'
            GROUP BY c.CustomerID, c.CustomerName, c.Email
            HAVING DATEDIFF(DAY, MAX(s.SaleDate), GETDATE()) > 90
            ORDER BY DaysInactive DESC
        """
        results = db.execute_query(query)
        inactive = []
        if results:
            for row in results:
                inactive.append({
                    'id': row[0],
                    'name': row[1],
                    'email': row[2] or '',
                    'last_purchase': row[3].strftime('%Y-%m-%d') if row[3] else '',
                    'days_inactive': row[4],
                    'total_orders': row[5]
                })
        problems['inactive_customers'] = inactive
        
        # Клиенты с отмененными заказами
        query = """
            SELECT c.CustomerID, c.CustomerName, c.Email,
                   COUNT(CASE WHEN s.Status='Cancelled' THEN 1 END) as CancelledOrders,
                   COUNT(s.SaleID) as TotalOrders
            FROM Customers c
            JOIN Sales s ON c.CustomerID = s.CustomerID
            GROUP BY c.CustomerID, c.CustomerName, c.Email
            HAVING COUNT(CASE WHEN s.Status='Cancelled' THEN 1 END) > 0
            ORDER BY CancelledOrders DESC
        """
        results = db.execute_query(query)
        cancelled = []
        if results:
            for row in results:
                cancelled.append({
                    'id': row[0],
                    'name': row[1],
                    'email': row[2] or '',
                    'cancelled_orders': row[3],
                    'total_orders': row[4],
                    'cancellation_rate': round(row[3] / row[4] * 100, 1)
                })
        problems['cancelled_orders'] = cancelled
        
        return jsonify(problems)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analytics/recommendations')
def recommendations():
    """API: Рекомендации для бизнеса"""
    try:
        recs = []
        
        # Товары с низким остатком
        query = "SELECT COUNT(*) FROM Products WHERE StockQuantity > 0 AND StockQuantity <= 10"
        result = db.execute_query(query)
        if result and result[0][0] > 0:
            recs.append({
                'type': 'warning',
                'title': 'Товары заканчиваются на складе',
                'message': f'{result[0][0]} товаров требуют пополнения запасов',
                'action': 'Проверьте складские остатки в разделе Товары'
            })
        
        # Товары без продаж
        query = """
            SELECT COUNT(*)
            FROM Products p
            LEFT JOIN SaleDetails sd ON p.ProductID = sd.ProductID
            WHERE sd.ProductID IS NULL
        """
        result = db.execute_query(query)
        if result and result[0][0] > 0:
            recs.append({
                'type': 'info',
                'title': 'Непродаваемые товары',
                'message': f'{result[0][0]} товаров ни разу не были проданы',
                'action': 'Рассмотрите скидки или удаление из каталога'
            })
        
        # Клиенты без покупок
        query = """
            SELECT COUNT(*)
            FROM Customers c
            LEFT JOIN Sales s ON c.CustomerID = s.CustomerID
            WHERE s.SaleID IS NULL
        """
        result = db.execute_query(query)
        if result and result[0][0] > 0:
            recs.append({
                'type': 'info',
                'title': 'Неактивные клиенты',
                'message': f'{result[0][0]} клиентов еще не совершили покупку',
                'action': 'Отправьте промо-предложения этим клиентам'
            })
        
        # Рост продаж
        query = """
            SELECT 
                SUM(CASE WHEN MONTH(SaleDate) = MONTH(GETDATE()) THEN TotalAmount ELSE 0 END) as CurrentMonth,
                SUM(CASE WHEN MONTH(SaleDate) = MONTH(DATEADD(MONTH, -1, GETDATE())) THEN TotalAmount ELSE 0 END) as LastMonth
            FROM Sales WHERE Status='Completed'
        """
        result = db.execute_query(query)
        if result and result[0][0] and result[0][1]:
            current = float(result[0][0])
            last = float(result[0][1])
            growth = ((current - last) / last * 100) if last > 0 else 0
            
            if growth > 10:
                recs.append({
                    'type': 'success',
                    'title': 'Отличный рост продаж!',
                    'message': f'Продажи выросли на {growth:.1f}% по сравнению с прошлым месяцем',
                    'action': 'Продолжайте в том же духе!'
                })
            elif growth < -10:
                recs.append({
                    'type': 'danger',
                    'title': 'Снижение продаж',
                    'message': f'Продажи упали на {abs(growth):.1f}% по сравнению с прошлым месяцем',
                    'action': 'Необходимо принять меры для стимулирования продаж'
                })
        
        return jsonify(recs)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ===========================
# SETTINGS - Настройки
# ===========================

@app.route('/settings')
def settings():
    """Страница настроек"""
    return render_template('settings.html')

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Sales Management Web Application")
    print("=" * 60)
    print("🌐 Открывается на: http://localhost:5000")
    print("📊 Dashboard: http://localhost:5000/")
    print("👥 Customers: http://localhost:5000/customers")
    print("📦 Products: http://localhost:5000/products")
    print("💰 Sales: http://localhost:5000/sales")
    print("📈 Analytics: http://localhost:5000/analytics")
    print("=" * 60)
    app.run(debug=True, host='0.0.0.0', port=5000)

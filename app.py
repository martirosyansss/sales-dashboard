from flask import Flask, render_template, jsonify, request, redirect, url_for
from database import get_database
from datetime import datetime, timedelta
import json
import os

app = Flask(__name__)
app.secret_key = 'sales_management_secret_key_2025'

# File constants
GROUP_MANAGER_ASSIGNMENTS_FILE = 'group_manager_assignments.json'
SELECTED_PRODUCT_GROUPS_FILE = 'selected_product_groups.json'
SALES_AREA_GROUP_ASSIGNMENTS_FILE = 'sales_area_group_assignments.json'
DASHBOARD_SELECTED_GROUPS_FILE = 'dashboard_selected_groups.json'
DASHBOARD_SELECTED_AREAS_FILE = 'dashboard_selected_areas.json'
EXCLUDED_CUSTOMERS_FILE = 'excluded_customers.json'
EXCLUDED_GROUPS_FILE = 'excluded_groups.json'

PRODUCT_GROUP_NAMES = {
    '033': 'ԼԵԴ լամպ',
    '034': 'Թեյ',
    '035': 'Գրենական',
    '037': 'Անձեռոցիկ AURA',
    '100': 'Գառնի կոլա 0.5լ',
    '101': 'Գառնի կոլա 1.5լ',
    '102': 'Գառնի կոլա (ապակե)',
    '103': 'Գառնի կոլա ապակե բլոկ',
    '104': 'Մաքրության լաթ',
    '20': 'Գառնի ջուր',
    '21': 'Լուծվող սուրճ',
    '22': 'Սուրճ',
    '23': 'Nescafe',
    '25': 'Մաքրող միջոցներ',
    '26': 'Տնտեսական ապրանքներ',
    '27': 'Աղբի տոպրակներ',
    '28': 'Անձեռոցիկներ',
    '29': 'Մեկանգամյա ճաշասպասք',
    '30': 'Կոմպոտներ',
    '40': 'TASTEA',
    '50': 'Մրգային օղի',
    'X01': 'Սառնարաններ',
    'X02': 'Группа X02',
    'X03': 'Группа X03'
}

# Get database instance
db = get_database()

def get_db_connection():
    """Get database connection, retrying if necessary"""
    global db
    if db is None:
        db = get_database()
    return db

# ===========================
# HELPER FUNCTIONS
# ===========================

def load_group_manager_assignments():
    """Загрузить назначения менеджеров группам"""
    try:
        if os.path.exists(GROUP_MANAGER_ASSIGNMENTS_FILE):
            with open(GROUP_MANAGER_ASSIGNMENTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        print(f"[GroupAssignments] Error loading: {e}")
        return {}

def save_group_manager_assignments(assignments):
    """Сохранить назначения менеджеров группам"""
    try:
        with open(GROUP_MANAGER_ASSIGNMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(assignments, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[GroupAssignments] Error saving: {e}")
        return False

def load_sales_area_group_assignments():
    """Загрузить назначения групп к Sales Areas"""
    try:
        if os.path.exists(SALES_AREA_GROUP_ASSIGNMENTS_FILE):
            with open(SALES_AREA_GROUP_ASSIGNMENTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        print(f"[SalesAreaGroups] Error loading: {e}")
        return {}

def save_sales_area_group_assignments(assignments):
    """Сохранить назначения групп к Sales Areas"""
    try:
        with open(SALES_AREA_GROUP_ASSIGNMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(assignments, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[SalesAreaGroups] Error saving: {e}")
        return False

def load_selected_product_groups():
    """Загрузить список выбранных групп товаров для фильтрации"""
    try:
        if os.path.exists(SELECTED_PRODUCT_GROUPS_FILE):
            with open(SELECTED_PRODUCT_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []  # Пустой список = показывать все группы
    except Exception as e:
        print(f"[ProductGroups] Error loading: {e}")
        return []

def save_selected_product_groups(groups_list):
    """Сохранить список выбранных групп товаров"""
    try:
        with open(SELECTED_PRODUCT_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[ProductGroups] Error saving: {e}")
        return False

def load_dashboard_selected_groups():
    """Загрузить выбранные группы для дашборда"""
    try:
        if os.path.exists(DASHBOARD_SELECTED_GROUPS_FILE):
            with open(DASHBOARD_SELECTED_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        print(f"[DashboardGroups] Error loading: {e}")
        return []

def save_dashboard_selected_groups(groups_list):
    """Сохранить выбранные группы клиентов для Dashboard"""
    try:
        with open(DASHBOARD_SELECTED_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[DashboardGroups] Error saving: {e}")
        return False

def get_dashboard_groups_filter_sql():
    """Получить SQL фильтр для выбранных групп клиентов Dashboard"""
    selected_groups = load_dashboard_selected_groups()
    if not selected_groups or len(selected_groups) == 0:
        return "", ()
    placeholders = ','.join('?' * len(selected_groups))
    filter_clause = f"AND c.fGROUP IN ({placeholders})"
    return filter_clause, tuple(selected_groups)

def load_dashboard_selected_areas():
    """Загрузить выбранные территории для Dashboard"""
    try:
        if os.path.exists(DASHBOARD_SELECTED_AREAS_FILE):
            with open(DASHBOARD_SELECTED_AREAS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []  # Пустой список = все территории
    except Exception as e:
        print(f"[DashboardAreas] Error loading: {e}")
        return []

def save_dashboard_selected_areas(areas_list):
    """Сохранить выбранные территории для Dashboard"""
    try:
        with open(DASHBOARD_SELECTED_AREAS_FILE, 'w', encoding='utf-8') as f:
            json.dump(areas_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[DashboardAreas] Error saving: {e}")
        return False

def get_dashboard_areas_filter_sql():
    """Получить SQL фильтр для выбранных территорий Dashboard"""
    selected_areas = load_dashboard_selected_areas()
    if not selected_areas or len(selected_areas) == 0:
        return "", ()
    placeholders = ','.join('?' * len(selected_areas))
    filter_clause = f"AND csa.fSALESAREA IN ({placeholders})"
    return filter_clause, tuple(selected_areas)

def load_excluded_customers():
    """Загрузить список исключенных клиентов из файла"""
    try:
        if os.path.exists(EXCLUDED_CUSTOMERS_FILE):
            with open(EXCLUDED_CUSTOMERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        print(f"[Excluded] Error loading: {e}")
        return []

def load_excluded_groups():
    """Загрузить список исключенных групп из файла"""
    try:
        if os.path.exists(EXCLUDED_GROUPS_FILE):
            with open(EXCLUDED_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        print(f"[ExcludedGroups] Error loading: {e}")
        return []

def get_excluded_customer_ids():
    """Получить список ID исключенных клиентов (включая клиентов из исключенных групп)"""
    excluded_ids = load_excluded_customers()
    if not excluded_ids:
        excluded_ids = []
    else:
        # Преобразуем в список ID
        excluded_ids = [item['customerId'] for item in excluded_ids]
    
    # Добавляем клиентов из исключенных групп
    excluded_groups = load_excluded_groups()
    if excluded_groups:
        try:
            current_db = get_db_connection()
            if current_db:
                placeholders = ','.join('?' * len(excluded_groups))
                query = f"SELECT fID FROM CUSTOMERS WHERE fGROUP IN ({placeholders})"
                rows = current_db.execute_query(query, tuple(excluded_groups))
                if rows:
                    for row in rows:
                        if row[0] not in excluded_ids:
                            excluded_ids.append(row[0])
        except Exception as e:
            print(f"[Excluded] Error loading customers from groups: {e}")
            
    return excluded_ids

def get_excluded_filter_sql():
    """Получить SQL условие для фильтрации исключенных клиентов"""
    excluded_ids = get_excluded_customer_ids()
    if not excluded_ids:
        return "", ()
    
    placeholders = ','.join('?' * len(excluded_ids))
    return f" AND c.fID NOT IN ({placeholders})", tuple(excluded_ids)

def get_product_groups_filter_sql():
    """Получить SQL условие для фильтрации по выбранным дивизионам"""
    selected_divisions = load_selected_product_groups()
    
    if not selected_divisions or len(selected_divisions) == 0:
        return "", ()
    
    placeholders = ','.join('?' * len(selected_divisions))
    filter_clause = f"""
        AND s.fSALESAGENTID IN (
            SELECT DISTINCT fSALESAGENTID 
            FROM SALESAGENTDIVISIONS 
            WHERE fDIVISION IN ({placeholders})
        )
    """
    return filter_clause, tuple(selected_divisions)

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
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500

        # Получить параметры фильтра из запроса
        date_from = request.args.get('date_from', None)
        date_to = request.args.get('date_to', None)
        
        # Если даты не указаны - использовать текущий месяц
        today = datetime.now()
        if date_from and date_to:
            current_start = datetime.strptime(date_from, '%Y-%m-%d')
            current_end = datetime.strptime(date_to, '%Y-%m-%d')
        else:
            current_start = today.replace(day=1)
            if today.month == 12:
                current_end = today.replace(year=today.year+1, month=1, day=1)
            else:
                current_end = today.replace(month=today.month+1, day=1)
        
        # Вычислить период для сравнения (прошлый месяц)
        if current_start.month == 1:
            prev_start = current_start.replace(year=current_start.year-1, month=12, day=1)
        else:
            prev_start = current_start.replace(month=current_start.month-1, day=1)
        
        if prev_start.month == 12:
            prev_end = prev_start.replace(year=prev_start.year+1, month=1, day=1)
        else:
            prev_end = prev_start.replace(month=prev_start.month+1, day=1)
        
        # Сравнение с тем же месяцем прошлого года
        last_year_start = current_start.replace(year=current_start.year-1)
        last_year_end = current_end.replace(year=current_end.year-1)
        
        # Фильтры
        excluded_filter, excluded_params = get_excluded_filter_sql()
        product_groups_filter, product_groups_params = get_product_groups_filter_sql()
        
        # Фильтр по территориям Dashboard
        dashboard_areas_filter, dashboard_areas_params = get_dashboard_areas_filter_sql()
        areas_join = ""
        if dashboard_areas_params:
            areas_join = "INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID"
        
        # Фильтр по группам клиентов Dashboard
        dashboard_groups_filter, dashboard_groups_params = get_dashboard_groups_filter_sql()
        
        query_revenue = f"""
            SELECT ISNULL(SUM(s.fTOTALSUM), 0) as TotalRevenue
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            {{areas_join}}
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {{excluded_filter}}
            {{product_groups_filter}}
            {{dashboard_areas_filter}}
            {{dashboard_groups_filter}}
        """
        # Format the query string first
        query_revenue = query_revenue.format(
            areas_join=areas_join,
            excluded_filter=excluded_filter,
            product_groups_filter=product_groups_filter,
            dashboard_areas_filter=dashboard_areas_filter,
            dashboard_groups_filter=dashboard_groups_filter
        )
        
        params_current = (current_start, current_end) + excluded_params + product_groups_params + dashboard_areas_params + dashboard_groups_params
        params_prev = (prev_start, prev_end) + excluded_params + product_groups_params + dashboard_areas_params + dashboard_groups_params
        params_last_year = (last_year_start, last_year_end) + excluded_params + product_groups_params + dashboard_areas_params + dashboard_groups_params
        
        current_revenue = current_db.execute_query(query_revenue, params_current)
        prev_revenue = current_db.execute_query(query_revenue, params_prev)
        last_year_revenue = current_db.execute_query(query_revenue, params_last_year)
        
        # Количество продаж
        query_sales_count = f"""
            SELECT COUNT(s.fISN) as SalesCount
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            {{areas_join}}
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {{excluded_filter}}
            {{product_groups_filter}}
            {{dashboard_areas_filter}}
            {{dashboard_groups_filter}}
        """
        # Format the query string first
        query_sales_count = query_sales_count.format(
            areas_join=areas_join,
            excluded_filter=excluded_filter,
            product_groups_filter=product_groups_filter,
            dashboard_areas_filter=dashboard_areas_filter,
            dashboard_groups_filter=dashboard_groups_filter
        )

        current_sales = current_db.execute_query(query_sales_count, params_current)
        prev_sales = current_db.execute_query(query_sales_count, params_prev)
        last_year_sales = current_db.execute_query(query_sales_count, params_last_year)
        
        # Средний чек
        current_rev = float(current_revenue[0][0]) if current_revenue else 0
        current_cnt = current_sales[0][0] if current_sales else 0
        avg_check = current_rev / current_cnt if current_cnt > 0 else 0
        
        # Средний чек прошлого месяца и прошлого года
        prev_rev = float(prev_revenue[0][0]) if prev_revenue else 0
        prev_cnt = prev_sales[0][0] if prev_sales else 0
        prev_avg_check = prev_rev / prev_cnt if prev_cnt > 0 else 0
        
        last_year_rev = float(last_year_revenue[0][0]) if last_year_revenue else 0
        last_year_cnt = last_year_sales[0][0] if last_year_sales else 0
        last_year_avg_check = last_year_rev / last_year_cnt if last_year_cnt > 0 else 0
        
        # Активные клиенты (покупали в выбранном периоде)
        query_customers = f"""
            SELECT COUNT(DISTINCT s.fCUSTOMERID) as ActiveCustomers
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            {{areas_join}}
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {{excluded_filter}}
            {{product_groups_filter}}
            {{dashboard_areas_filter}}
            {{dashboard_groups_filter}}
        """
        # Format the query string first
        query_customers = query_customers.format(
            areas_join=areas_join,
            excluded_filter=excluded_filter,
            product_groups_filter=product_groups_filter,
            dashboard_areas_filter=dashboard_areas_filter,
            dashboard_groups_filter=dashboard_groups_filter
        )

        active_customers = current_db.execute_query(query_customers, params_current)
        prev_customers = current_db.execute_query(query_customers, params_prev)
        last_year_customers = current_db.execute_query(query_customers, params_last_year)
        
        # Топ менеджер периода
        query_top_manager = f"""
            SELECT TOP 1 
                sa.fNAME as ManagerName,
                SUM(s.fTOTALSUM) as TotalSales
            FROM SALES s
            INNER JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            {{areas_join}}
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {{excluded_filter}}
            {{product_groups_filter}}
            {{dashboard_areas_filter}}
            {{dashboard_groups_filter}}
            GROUP BY sa.fNAME
            ORDER BY TotalSales DESC
        """
        # Format the query string first
        query_top_manager = query_top_manager.format(
            areas_join=areas_join,
            excluded_filter=excluded_filter,
            product_groups_filter=product_groups_filter,
            dashboard_areas_filter=dashboard_areas_filter,
            dashboard_groups_filter=dashboard_groups_filter
        )

        top_manager = current_db.execute_query(query_top_manager, params_current)
        
        stats = {
            'total_revenue': current_rev,
            'monthly_revenue': current_rev, # Для совместимости
            'revenue_growth': ((current_rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else 0,
            'revenue_growth_year': ((current_rev - last_year_rev) / last_year_rev * 100) if last_year_rev > 0 else 0,
            
            'total_sales': current_cnt,
            'sales_growth': ((current_cnt - prev_cnt) / prev_cnt * 100) if prev_cnt > 0 else 0,
            'sales_growth_year': ((current_cnt - last_year_cnt) / last_year_cnt * 100) if last_year_cnt > 0 else 0,
            
            'average_order': avg_check,
            'avg_check_growth': ((avg_check - prev_avg_check) / prev_avg_check * 100) if prev_avg_check > 0 else 0,
            'avg_check_growth_year': ((avg_check - last_year_avg_check) / last_year_avg_check * 100) if last_year_avg_check > 0 else 0,
            
            'active_customers': active_customers[0][0] if active_customers else 0,
            'active_customers_growth': ((active_customers[0][0] - prev_customers[0][0]) / prev_customers[0][0] * 100) if prev_customers and prev_customers[0][0] > 0 else 0,
            'active_customers_growth_year': ((active_customers[0][0] - last_year_customers[0][0]) / last_year_customers[0][0] * 100) if last_year_customers and last_year_customers[0][0] > 0 else 0,
            
            'top_manager': top_manager[0][0] if top_manager else 'N/A',
            'top_manager_sales': float(top_manager[0][1]) if top_manager else 0
        }
        
        return jsonify(stats)
    except Exception as e:
        print(f"[DashboardStats] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/sales-chart')
def sales_chart():
    """API: Данные для графика продаж за последние 12 месяцев"""
    try:
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500

        query = """
            SELECT 
                FORMAT(fDATE, 'yyyy-MM') as Month,
                COUNT(*) as SalesCount,
                SUM(fTOTALSUM) as TotalSum
            FROM SALES
            WHERE fDATE >= DATEADD(MONTH, -12, GETDATE())
            AND fSTATE = 2
            GROUP BY FORMAT(fDATE, 'yyyy-MM')
            ORDER BY Month
        """
        results = current_db.execute_query(query)
        
        months = []
        revenue = []
        orders = []
        
        if results:
            for row in results:
                # row is likely a tuple or Row object
                # Month is row[0], SalesCount is row[1], TotalSum is row[2]
                month_str = row[0]
                try:
                    month_date = datetime.strptime(month_str, '%Y-%m')
                    month_name = month_date.strftime('%b %Y')
                except:
                    month_name = month_str
                
                months.append(month_name)
                orders.append(row[1])
                revenue.append(float(row[2]))
        
        return jsonify({
            'labels': months,
            'revenue': revenue,
            'orders': orders
        })
    except Exception as e:
        print(f"[SalesChart] Error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/top-products')
def top_products():
    """API: Топ-10 продаваемых товаров"""
    try:
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500

        query = """
            SELECT TOP 10
                p.fNAME as ProductName,
                SUM(sd.fQUANTITY) as TotalSold,
                SUM(sd.fSUM) as Revenue
            FROM SALEDOCDETAILS sd
            JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
            JOIN SALES s ON sd.fISN = s.fISN
            WHERE s.fSTATE = 2
            GROUP BY p.fNAME
            ORDER BY Revenue DESC
        """
        results = current_db.execute_query(query)
        
        products = []
        sold = []
        revenue = []
        
        if results:
            for row in results:
                products.append(row[0])
                sold.append(float(row[1]))
                revenue.append(float(row[2]))
        
        return jsonify({
            'products': products,
            'sold': sold,
            'revenue': revenue
        })
    except Exception as e:
        print(f"[TopProducts] Error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/dashboard/category-distribution')
def category_distribution():
    """API: Распределение продаж по категориям"""
    try:
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500

        query = """
            SELECT 
                ISNULL(t.fCAPTION, p.fGROUP) as Category,
                SUM(sd.fSUM) as Revenue
            FROM SALEDOCDETAILS sd
            JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
            JOIN SALES s ON sd.fISN = s.fISN
            LEFT JOIN TREES t ON p.fGROUP = t.fCODE AND t.fTREEID = 'PrdctGrp'
            WHERE s.fSTATE = 2
            GROUP BY ISNULL(t.fCAPTION, p.fGROUP)
        """
        results = current_db.execute_query(query)
        
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
        print(f"[CategoryDist] Error: {e}")
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
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500

        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        sales_area = request.args.get('sales_area', '101').strip() or '101'
        raw_divisions = request.args.get('divisions', '').strip()
        selected_divisions = [div.strip() for div in raw_divisions.split(',') if div.strip()]
        raw_groups = request.args.get('groups', '').strip()
        selected_groups = [grp.strip() for grp in raw_groups.split(',') if grp.strip()]
        include_zero_sales = request.args.get('include_zero_sales', '0') == '1'
        
        if not date_from or not date_to:
            today = datetime.now()
            date_from = today.replace(day=1).strftime('%Y-%m-%d')
            last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            date_to = last_day.strftime('%Y-%m-%d')

        excluded_filter, excluded_params = get_excluded_filter_sql()
        product_groups_filter, product_groups_params = get_product_groups_filter_sql()
        
        # Если нужно включить клиентов с 0 продаж, используем другой запрос
        if include_zero_sales:
            # Запрос со всеми клиентами, назначенными на Sales Area через таблицу CUSTOMERSALESAREAS
            base_customer_clause = ""
            customer_params_base = []
            
            # Дополнительная фильтрация по выбранным группам
            if selected_groups:
                placeholders = ','.join('?' * len(selected_groups))
                base_customer_clause += f" AND c.fGROUP IN ({placeholders})"
                customer_params_base.extend(selected_groups)
            
            # REMOVED: c.fDIVISION does not exist in CUSTOMERS table
            # if selected_divisions:
            #     placeholders = ','.join('?' * len(selected_divisions))
            #     base_customer_clause += f" AND c.fDIVISION IN ({placeholders})"
            #     customer_params_base.extend(selected_divisions)
            
            query = f"""
                WITH AllCustomers AS (
                    SELECT DISTINCT
                        c.fID AS CustomerId,
                        c.fCODE AS CustomerCode,
                        c.fNAME AS CustomerName,
                        c.fGROUP AS GroupCode,
                        c.fADDRESS AS CustomerAddress
                    FROM CUSTOMERS c
                    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                    WHERE csa.fSALESAREA = ?
                        {base_customer_clause}
                ),
                FilteredSales AS (
                    SELECT 
                        ac.CustomerId,
                        sa.fCODE AS ManagerCode,
                        sa.fNAME AS ManagerName,
                        COUNT(s.fISN) AS SalesCount,
                        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
                    FROM SALES s
                    INNER JOIN AllCustomers ac ON s.fCUSTOMERID = ac.CustomerId
                    LEFT JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
                    WHERE s.fSTATE = 2
                        AND s.fDATE >= ?
                        AND s.fDATE <= ?
                        AND s.fSALESAREA = ?
                        {excluded_filter}
                        {product_groups_filter}
                    GROUP BY ac.CustomerId, sa.fCODE, sa.fNAME
                ),
                Totals AS (
                    SELECT 
                        ac.CustomerId,
                        ac.CustomerCode,
                        ac.CustomerName,
                        ac.GroupCode,
                        ac.CustomerAddress,
                        ISNULL(SUM(fs.SalesCount), 0) AS SalesCount,
                        ISNULL(SUM(fs.TotalSales), 0) AS TotalSales
                    FROM AllCustomers ac
                    LEFT JOIN FilteredSales fs ON ac.CustomerId = fs.CustomerId
                    GROUP BY ac.CustomerId, ac.CustomerCode, ac.CustomerName, ac.GroupCode, ac.CustomerAddress
                ),
                Managers AS (
                    SELECT 
                        CustomerId,
                        ManagerCode,
                        ManagerName,
                        TotalSales,
                        ROW_NUMBER() OVER (PARTITION BY CustomerId ORDER BY TotalSales DESC) AS rn
                    FROM FilteredSales
                ),
                DebtData AS (
                    SELECT 
                        doc.fCUSTOMERID AS CustomerId,
                        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS DebtFromDocs
                    FROM HICUSTOMERSDEBT d
                    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                    GROUP BY doc.fCUSTOMERID
                ),
                RestData AS (
                    SELECT 
                        fCUSTOMERID AS CustomerId,
                        ISNULL(SUM(CASE WHEN fTYPE = '01' THEN fSUM ELSE 0 END), 0) AS Type01,
                        ISNULL(SUM(CASE WHEN fTYPE = '02' THEN fSUM ELSE 0 END), 0) AS Type02
                    FROM HIRESTCUSTOMERSSUM
                    GROUP BY fCUSTOMERID
                ),
                PaymentData AS (
                    SELECT 
                        d.fCUSTOMERID AS CustomerId,
                        ISNULL(SUM(CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END), 0) AS TotalPayments
                    FROM HICUSTOMERSDEBT h
                    INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
                    WHERE h.fOP = 'PAY'
                        AND h.fDATE >= ?
                        AND h.fDATE <= ?
                    GROUP BY d.fCUSTOMERID
                ),
                LastPaymentData AS (
                    SELECT 
                        d.fCUSTOMERID AS CustomerId,
                        MAX(h.fDATE) AS LastPaymentDate,
                        DATEDIFF(DAY, MAX(h.fDATE), GETDATE()) AS DaysSinceLastPayment
                    FROM HICUSTOMERSDEBT h
                    INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
                    WHERE h.fOP = 'PAY' AND h.fDBCR = 'C'
                    GROUP BY d.fCUSTOMERID
                ),
                LastSaleData AS (
                    SELECT 
                        fCUSTOMERID AS CustomerId,
                        MAX(fDATE) AS LastSaleDate,
                        DATEDIFF(DAY, MAX(fDATE), GETDATE()) AS DaysSinceLastSale
                    FROM SALES
                    WHERE fSTATE = 2
                    GROUP BY fCUSTOMERID
                )
                SELECT 
                    t.CustomerId,
                    t.CustomerCode,
                    t.CustomerName,
                    ISNULL(t.GroupCode, '') AS GroupCode,
                    ISNULL(t.CustomerAddress, '') AS CustomerAddress,
                    t.SalesCount,
                    t.TotalSales,
                    ISNULL(m.ManagerCode, 'N/A') AS ManagerCode,
                    ISNULL(m.ManagerName, 'N/A') AS ManagerName,
                    ISNULL(dd.DebtFromDocs, 0) AS DebtFromDocs,
                    ISNULL(rd.Type01, 0) AS Type01,
                    ISNULL(rd.Type02, 0) AS Type02,
                    (ISNULL(dd.DebtFromDocs, 0) - ABS(ISNULL(rd.Type01, 0)) - ABS(ISNULL(rd.Type02, 0))) AS Debt,
                    ISNULL(pd.TotalPayments, 0) AS TotalPayments,
                    ((ISNULL(dd.DebtFromDocs, 0) - ABS(ISNULL(rd.Type01, 0)) - ABS(ISNULL(rd.Type02, 0))) - t.TotalSales + ISNULL(pd.TotalPayments, 0)) AS InitialDebt,
                    lpd.LastPaymentDate,
                    lpd.DaysSinceLastPayment,
                    lsd.LastSaleDate,
                    lsd.DaysSinceLastSale
                FROM Totals t
                LEFT JOIN Managers m ON t.CustomerId = m.CustomerId AND m.rn = 1
                LEFT JOIN DebtData dd ON t.CustomerId = dd.CustomerId
                LEFT JOIN RestData rd ON t.CustomerId = rd.CustomerId
                LEFT JOIN PaymentData pd ON t.CustomerId = pd.CustomerId
                LEFT JOIN LastPaymentData lpd ON t.CustomerId = lpd.CustomerId
                LEFT JOIN LastSaleData lsd ON t.CustomerId = lsd.CustomerId
                WHERE (ISNULL(dd.DebtFromDocs, 0) - ABS(ISNULL(rd.Type01, 0)) - ABS(ISNULL(rd.Type02, 0))) > 0
                ORDER BY t.TotalSales DESC
            """
            params = (sales_area,) + tuple(customer_params_base) + (date_from, date_to, sales_area) + excluded_params + product_groups_params + (date_from, date_to)
        else:
            # Стандартный запрос только с клиентами, у которых есть продажи
            query = f"""
                WITH FilteredSales AS (
                    SELECT 
                        c.fID AS CustomerId,
                        c.fCODE AS CustomerCode,
                        c.fNAME AS CustomerName,
                        c.fGROUP AS GroupCode,
                        c.fADDRESS AS CustomerAddress,
                        sa.fCODE AS ManagerCode,
                        sa.fNAME AS ManagerName,
                        COUNT(s.fISN) AS SalesCount,
                        ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales
                    FROM SALES s
                    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
                    LEFT JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
                    INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                    WHERE s.fSTATE = 2
                        AND s.fDATE >= ?
                        AND s.fDATE <= ?
                        AND csa.fSALESAREA = ?
                        {excluded_filter}
                        {product_groups_filter}
                    GROUP BY c.fID, c.fCODE, c.fNAME, c.fGROUP, c.fADDRESS, sa.fCODE, sa.fNAME
                ),
                Totals AS (
                    SELECT 
                        CustomerId,
                        CustomerCode,
                        CustomerName,
                        GroupCode,
                        CustomerAddress,
                        SUM(SalesCount) AS SalesCount,
                        SUM(TotalSales) AS TotalSales
                    FROM FilteredSales
                    GROUP BY CustomerId, CustomerCode, CustomerName, GroupCode, CustomerAddress
                ),
                Managers AS (
                    SELECT 
                        CustomerId,
                        ManagerCode,
                        ManagerName,
                        TotalSales,
                        ROW_NUMBER() OVER (PARTITION BY CustomerId ORDER BY TotalSales DESC) AS rn
                    FROM FilteredSales
                ),
                DebtData AS (
                    SELECT 
                        doc.fCUSTOMERID AS CustomerId,
                        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS DebtFromDocs
                    FROM HICUSTOMERSDEBT d
                    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                    GROUP BY doc.fCUSTOMERID
                ),
                RestData AS (
                    SELECT 
                        fCUSTOMERID AS CustomerId,
                        ISNULL(SUM(CASE WHEN fTYPE = '01' THEN fSUM ELSE 0 END), 0) AS Type01,
                        ISNULL(SUM(CASE WHEN fTYPE = '02' THEN fSUM ELSE 0 END), 0) AS Type02
                    FROM HIRESTCUSTOMERSSUM
                    GROUP BY fCUSTOMERID
                ),
                PaymentData AS (
                    SELECT 
                        d.fCUSTOMERID AS CustomerId,
                        ISNULL(SUM(CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END), 0) AS TotalPayments
                    FROM HICUSTOMERSDEBT h
                    INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
                    WHERE h.fOP = 'PAY'
                        AND h.fDATE >= ?
                        AND h.fDATE <= ?
                    GROUP BY d.fCUSTOMERID
                ),
                LastPaymentData AS (
                    SELECT 
                        d.fCUSTOMERID AS CustomerId,
                        MAX(h.fDATE) AS LastPaymentDate,
                        DATEDIFF(DAY, MAX(h.fDATE), GETDATE()) AS DaysSinceLastPayment
                    FROM HICUSTOMERSDEBT h
                    INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
                    WHERE h.fOP = 'PAY' AND h.fDBCR = 'C'
                    GROUP BY d.fCUSTOMERID
                ),
                LastSaleData AS (
                    SELECT 
                        fCUSTOMERID AS CustomerId,
                        MAX(fDATE) AS LastSaleDate,
                        DATEDIFF(DAY, MAX(fDATE), GETDATE()) AS DaysSinceLastSale
                    FROM SALES
                    WHERE fSTATE = 2
                    GROUP BY fCUSTOMERID
                )
                SELECT 
                    t.CustomerId,
                    t.CustomerCode,
                    t.CustomerName,
                    ISNULL(t.GroupCode, '') AS GroupCode,
                    ISNULL(t.CustomerAddress, '') AS CustomerAddress,
                    t.SalesCount,
                    t.TotalSales,
                    ISNULL(m.ManagerCode, 'N/A') AS ManagerCode,
                    ISNULL(m.ManagerName, 'N/A') AS ManagerName,
                    ISNULL(dd.DebtFromDocs, 0) AS DebtFromDocs,
                    ISNULL(rd.Type01, 0) AS Type01,
                    ISNULL(rd.Type02, 0) AS Type02,
                    (ISNULL(dd.DebtFromDocs, 0) - ABS(ISNULL(rd.Type01, 0)) - ABS(ISNULL(rd.Type02, 0))) AS Debt,
                    ISNULL(pd.TotalPayments, 0) AS TotalPayments,
                    ((ISNULL(dd.DebtFromDocs, 0) - ABS(ISNULL(rd.Type01, 0)) - ABS(ISNULL(rd.Type02, 0))) - t.TotalSales + ISNULL(pd.TotalPayments, 0)) AS InitialDebt,
                    lpd.LastPaymentDate,
                    lpd.DaysSinceLastPayment,
                    lsd.LastSaleDate,
                    lsd.DaysSinceLastSale
                FROM Totals t
                LEFT JOIN Managers m ON t.CustomerId = m.CustomerId AND m.rn = 1
                LEFT JOIN DebtData dd ON t.CustomerId = dd.CustomerId
                LEFT JOIN RestData rd ON t.CustomerId = rd.CustomerId
                LEFT JOIN PaymentData pd ON t.CustomerId = pd.CustomerId
                LEFT JOIN LastPaymentData lpd ON t.CustomerId = lpd.CustomerId
                LEFT JOIN LastSaleData lsd ON t.CustomerId = lsd.CustomerId
                ORDER BY t.TotalSales DESC
            """
            params = (date_from, date_to, sales_area) + excluded_params + product_groups_params + (date_from, date_to)

        rows = current_db.execute_query(query, params)
        
        customers = []
        if rows:
            for row in rows:
                customers.append({
                    'id': row.CustomerId,
                    'code': row.CustomerCode,
                    'name': row.CustomerName,
                    'group': row.GroupCode,
                    'address': row.CustomerAddress,
                    'orders': row.SalesCount,
                    'spent': float(row.TotalSales),
                    'manager_code': row.ManagerCode,
                    'manager_name': row.ManagerName,
                    'debt': float(row.Debt),
                    'initial_debt': float(row.InitialDebt),
                    'payments': float(row.TotalPayments),
                    'last_payment_date': row.LastPaymentDate.strftime('%Y-%m-%d') if row.LastPaymentDate else '',
                    'days_since_payment': row.DaysSinceLastPayment,
                    'last_sale_date': row.LastSaleDate.strftime('%Y-%m-%d') if row.LastSaleDate else '',
                    'days_since_sale': row.DaysSinceLastSale
                })
        
        return jsonify(customers)
    except Exception as e:
        print(f"Error getting customers: {e}")
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
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500

        query = """
            SELECT 
                s.fISN,
                c.fNAME,
                s.fDATE,
                s.fTOTALSUM,
                s.fSTATE,
                COUNT(sd.fISN) as ItemCount
            FROM SALES s
            LEFT JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            LEFT JOIN SALEDOCDETAILS sd ON s.fISN = sd.fISN
            WHERE s.fSTATE = 2
            GROUP BY s.fISN, c.fNAME, s.fDATE, s.fTOTALSUM, s.fSTATE
            ORDER BY s.fDATE DESC
        """
        results = current_db.execute_query(query)
        
        sales = []
        if results:
            for row in results:
                sales.append({
                    'id': row[0],
                    'customer': row[1] or 'Unknown',
                    'date': row[2].strftime('%Y-%m-%d %H:%M') if row[2] else '',
                    'total': float(row[3]),
                    'status': 'Completed' if row[4] == 2 else 'Pending',
                    'items': row[5]
                })
        
        return jsonify(sales)
    except Exception as e:
        print(f"[Sales] Error: {e}")
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
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500

        problems = {}
        
        # Клиенты без покупок
        query = """
            SELECT TOP 50 c.fID, c.fNAME, c.fEMAIL, c.fPHONE, c.fADDRESS
            FROM CUSTOMERS c
            LEFT JOIN SALES s ON c.fID = s.fCUSTOMERID
            WHERE s.fISN IS NULL
        """
        results = current_db.execute_query(query)
        no_purchases = []
        if results:
            for row in results:
                no_purchases.append({
                    'id': row[0],
                    'name': row[1],
                    'email': row[2] or '',
                    'phone': row[3] or '',
                    'city': row[4] or '',
                    'days_since_registration': 0
                })
        problems['no_purchases'] = no_purchases
        
        # Неактивные клиенты (более 90 дней без покупок)
        query = """
            SELECT TOP 50 c.fID, c.fNAME, c.fEMAIL, MAX(s.fDATE) as LastPurchase,
                   DATEDIFF(DAY, MAX(s.fDATE), GETDATE()) as DaysInactive,
                   COUNT(s.fISN) as TotalOrders
            FROM CUSTOMERS c
            JOIN SALES s ON c.fID = s.fCUSTOMERID
            WHERE s.fSTATE = 2
            GROUP BY c.fID, c.fNAME, c.fEMAIL
            HAVING DATEDIFF(DAY, MAX(s.fDATE), GETDATE()) > 90
            ORDER BY DaysInactive DESC
        """
        results = current_db.execute_query(query)
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
        
        # Клиенты с отмененными заказами (placeholder)
        problems['cancelled_orders'] = []
        
        return jsonify(problems)
    except Exception as e:
        print(f"[Analytics] Error: {e}")
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

# ===== Группы =====
@app.route('/api/settings/groups')
def get_settings_groups():
    """Получить список всех групп клиентов с названиями и родителями из TREES"""
    try:
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500

        # Сначала получаем все группы из CUSTOMERS
        query_groups = """
            SELECT DISTINCT fGROUP
            FROM CUSTOMERS
            WHERE fGROUP IS NOT NULL AND fGROUP != ''
            ORDER BY fGROUP
        """
        rows = current_db.execute_query(query_groups)
        customer_groups = [row[0] for row in rows] if rows else []
        
        # Затем получаем названия и родителей из TREES
        query_trees = """
            SELECT fCODE, fCAPTION, fPARENT
            FROM TREES
            WHERE fTREEID = 'CustGrp'
        """
        rows_trees = current_db.execute_query(query_trees)
        tree_data = {}
        if rows_trees:
            for row in rows_trees:
                tree_data[row[0]] = {
                    'name': row[1],
                    'parent': row[2]
                }
        
        # Формируем результат с названиями и родителями
        groups = []
        for group_code in customer_groups:
            info = tree_data.get(group_code, {})
            name = info.get('name', group_code)
            parent_code = info.get('parent')
            
            parent_name = ""
            if parent_code:
                parent_info = tree_data.get(parent_code, {})
                parent_name = parent_info.get('name', parent_code)
            
            groups.append({
                'code': group_code,
                'name': name,
                'parent_code': parent_code,
                'parent_name': parent_name
            })
        
        # Сортируем: сначала по родителю, потом по коду
        groups.sort(key=lambda x: (x['parent_code'] or '', x['code']))
        
        return jsonify({'success': True, 'data': groups})
    except Exception as e:
        print(f"[Settings] Error loading groups: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Sales Areas → Groups =====
@app.route('/api/settings/sales-areas/list')
def get_settings_sales_areas_list():
    """Получить список Sales Areas из TREES с количеством назначенных клиентов из CUSTOMERSALESAREAS"""
    try:
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500
            
        query = """
            SELECT 
                t.fCODE, 
                t.fCAPTION,
                COUNT(DISTINCT csa.fCUSTOMERID) AS CustomerCount
            FROM TREES t
            LEFT JOIN CUSTOMERSALESAREAS csa ON t.fCODE = csa.fSALESAREA
            WHERE t.fTREEID = 'SArea'
            GROUP BY t.fCODE, t.fCAPTION
            ORDER BY t.fCODE
        """
        rows = current_db.execute_query(query)
        areas = []
        if rows:
            for row in rows:
                areas.append({
                    'code': row[0],
                    'name': row[1],
                    'customerCount': row[2] if row[2] else 0
                })
        return jsonify({'success': True, 'data': areas})
    except Exception as e:
        print(f"[SalesAreaGroups] Error loading areas: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/sales-areas/groups')
def get_sales_area_group_assignments():
    """Получить текущие назначения групп к Sales Areas"""
    try:
        assignments = load_sales_area_group_assignments()
        return jsonify({'success': True, 'data': assignments})
    except Exception as e:
        print(f"[SalesAreaGroups] Error loading assignments: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/sales-areas/groups/set', methods=['POST'])
def set_sales_area_group_assignments():
    """Установить список групп для конкретной Sales Area"""
    try:
        data = request.get_json()
        area_code = data.get('areaCode')
        groups = data.get('groups', [])
        if not area_code:
            return jsonify({'success': False, 'error': 'areaCode is required'}), 400
        assignments = load_sales_area_group_assignments()
        if groups:
            unique_groups = sorted({g.strip() for g in groups if g})
            assignments[area_code] = unique_groups
        else:
            assignments.pop(area_code, None)
        if save_sales_area_group_assignments(assignments):
            print(f"[SalesAreaGroups] Updated {area_code}: {len(groups)} groups")
            return jsonify({'success': True, 'data': assignments.get(area_code, [])})
        return jsonify({'success': False, 'error': 'Ошибка сохранения'}), 500
    except Exception as e:
        print(f"[SalesAreaGroups] Error saving assignments: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Назначение менеджеров группам =====
@app.route('/api/settings/group-manager-assignments')
def get_group_manager_assignments():
    """Получить назначения менеджеров группам"""
    try:
        assignments = load_group_manager_assignments()
        return jsonify({'success': True, 'data': assignments})
    except Exception as e:
        print(f"[GroupAssignments] Error loading: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/group-manager-assignments/set', methods=['POST'])
def set_group_manager_assignment():
    """Назначить/отменить назначение менеджера группе (поддержка множественных менеджеров)"""
    try:
        data = request.get_json()
        group_code = data.get('groupCode')
        manager_id = data.get('managerId')
        
        assignments = load_group_manager_assignments()
        
        # Конвертация старого формата в новый при необходимости
        if group_code in assignments and not isinstance(assignments[group_code], list):
            assignments[group_code] = [assignments[group_code]]
        
        if manager_id:
            manager_id = int(manager_id)
            # Добавить менеджера в массив для этой группы
            if group_code not in assignments:
                assignments[group_code] = []
            if manager_id not in assignments[group_code]:
                assignments[group_code].append(manager_id)
        else:
            # Если manager_id пустой - удалить всю группу
            assignments.pop(group_code, None)
        
        if save_group_manager_assignments(assignments):
            print(f"[GroupAssignments] Updated group {group_code} managers: {assignments.get(group_code, [])}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        print(f"[GroupAssignments] Error setting: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/group-manager-assignments/remove', methods=['POST'])
def remove_group_manager_assignment():
    """Удалить менеджера из группы"""
    try:
        data = request.get_json()
        group_code = data.get('groupCode')
        manager_id = data.get('managerId')
        
        assignments = load_group_manager_assignments()
        
        if group_code in assignments:
            # Конвертация старого формата в новый при необходимости
            if not isinstance(assignments[group_code], list):
                assignments[group_code] = [assignments[group_code]]
            
            # Удалить менеджера из массива
            manager_id = int(manager_id)
            if manager_id in assignments[group_code]:
                assignments[group_code].remove(manager_id)
            
            # Если массив пустой - удалить группу полностью
            if not assignments[group_code]:
                del assignments[group_code]
        
        if save_group_manager_assignments(assignments):
            print(f"[GroupAssignments] Removed manager {manager_id} from group {group_code}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        print(f"[GroupAssignments] Error removing: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Выбор групп товаров для фильтрации =====

@app.route('/api/settings/product-groups')
def get_all_product_groups():
    """Получить все дивизионы из таблицы TREES"""
    try:
        current_db = get_db_connection()
        if not current_db:
            return jsonify({'success': False, 'error': 'Database connection not initialized'}), 500

        query = """
            SELECT 
                fCODE,
                fCAPTION,
                fISN
            FROM TREES
            WHERE fTREEID = 'Division'
            AND fCLOSED = 0
            ORDER BY fCODE
        """
        rows = current_db.execute_query(query)
        
        divisions = []
        if rows:
            for row in rows:
                divisions.append({
                    'fGROUP': row[0],  # код дивизиона (000000, 000001 и т.д.)
                    'name': row[1],    # название на армянском
                    'product_count': 0  # пока не считаем товары
                })
        
        print(f"[Divisions] Loaded {len(divisions)} divisions from TREES")
        return jsonify({'success': True, 'data': divisions})
    except Exception as e:
        print(f"[Divisions] Error loading: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/selected-product-groups')
def get_selected_product_groups():
    """Получить список выбранных групп товаров"""
    try:
        selected = load_selected_product_groups()
        return jsonify({'success': True, 'data': selected})
    except Exception as e:
        print(f"[ProductGroups] Error loading selected: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/selected-product-groups/set', methods=['POST'])
def set_selected_product_groups():
    """Установить список выбранных групп товаров"""
    try:
        data = request.get_json()
        groups_list = data.get('selectedGroups', [])
        
        if save_selected_product_groups(groups_list):
            print(f"[ProductGroups] Saved {len(groups_list)} selected groups")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        print(f"[ProductGroups] Error saving selected: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

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

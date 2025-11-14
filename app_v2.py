"""
Sales Dashboard v2.0 - READ-ONLY Analytics Platform
Работает с реальной БД AS-Sales Management
"""

from flask import Flask, render_template, jsonify, request
import pyodbc
from datetime import datetime, timedelta
import os
import json
from typing import Dict, List, Any
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sales-dashboard-secret-key-2025'

# =============================================
# ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ
# =============================================

class DatabaseConnection:
    """Класс для работы с БД AS-Sales Management"""
    
    def __init__(self):
        # Имя базы можно задать через переменную окружения SALES_DB
        db_name = os.environ.get('SALES_DB', 'SalesManagement-')
        self.connection_string = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            "SERVER=localhost;"
            f"DATABASE={db_name};"
            "UID=sa;"
            "PWD=Aa123456;"
            "TrustServerCertificate=yes;"
        )
    
    def get_connection(self):
        """Получить подключение к БД"""
        try:
            return pyodbc.connect(self.connection_string)
        except Exception as e:
            logger.error(f"Ошибка подключения к БД: {e}")
            raise
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict]:
        """Выполнить SELECT запрос и вернуть результат как список словарей"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            # Получить названия колонок
            columns = [column[0] for column in cursor.description]
            
            # Преобразовать результат в список словарей
            results = []
            for row in cursor.fetchall():
                results.append(dict(zip(columns, row)))
            
            cursor.close()
            conn.close()
            
            return results
            
        except Exception as e:
            logger.error(f"Ошибка выполнения запроса: {e}")
            return []

# Глобальный экземпляр БД
db = DatabaseConnection()

# =============================================
# УТИЛИТЫ ДЛЯ ИСКЛЮЧЕННЫХ КЛИЕНТОВ
# =============================================

def get_excluded_filter_sql():
    """Получить SQL условие для фильтрации исключенных клиентов"""
    excluded_ids = get_excluded_customer_ids()
    if not excluded_ids:
        return "", ()
    
    placeholders = ','.join('?' * len(excluded_ids))
    return f" AND c.fID NOT IN ({placeholders})", tuple(excluded_ids)

def get_manager_responsible_groups_filter(manager_id):
    """Получить SQL условие для фильтрации по ответственным группам менеджера
    
    Если для менеджера назначены ответственные группы - фильтруем только по ним.
    Если не назначены - показываем всех клиентов (старая логика).
    """
    assignments = load_group_manager_assignments()
    
    # Найти группы, за которые ответственен этот менеджер
    responsible_groups = [group for group, mgr_id in assignments.items() if mgr_id == manager_id]
    
    if not responsible_groups:
        # Если не назначены ответственные группы - не фильтруем (старая логика)
        return "", ()
    
    # Фильтруем только по ответственным группам
    placeholders = ','.join('?' * len(responsible_groups))
    return f" AND c.fGROUP IN ({placeholders})", tuple(responsible_groups)

# =============================================
# ГЛАВНАЯ СТРАНИЦА
# =============================================

@app.route('/')
def index():
    """Главная страница - Dashboard"""
    return render_template('dashboard_v2.html')

# =============================================
# API: DASHBOARD СТАТИСТИКА
# =============================================

@app.route('/api/dashboard/stats')
def dashboard_stats():
    """Получить основную статистику для Dashboard с фильтрами по датам"""
    try:
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
        
        # Сравнение с тем же месяцем прошлого года (10 лет назад)
        last_year_start = current_start.replace(year=current_start.year-1)
        last_year_end = current_end.replace(year=current_end.year-1)
        
        ten_years_ago_start = current_start.replace(year=current_start.year-10)
        ten_years_ago_end = current_end.replace(year=current_end.year-10)
        
        # Общая выручка текущего периода
        excluded_filter, excluded_params = get_excluded_filter_sql()
        
        query_revenue = f"""
            SELECT ISNULL(SUM(s.fTOTALSUM), 0) as TotalRevenue
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
        """
        
        params_current = (current_start, current_end) + excluded_params
        params_prev = (prev_start, prev_end) + excluded_params
        params_last_year = (last_year_start, last_year_end) + excluded_params
        params_ten_years = (ten_years_ago_start, ten_years_ago_end) + excluded_params
        
        current_revenue = db.execute_query(query_revenue, params_current)
        prev_revenue = db.execute_query(query_revenue, params_prev)
        last_year_revenue = db.execute_query(query_revenue, params_last_year)
        ten_years_revenue = db.execute_query(query_revenue, params_ten_years)
        
        # Количество продаж
        query_sales_count = f"""
            SELECT COUNT(*) as SalesCount
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
        """
        current_sales = db.execute_query(query_sales_count, params_current)
        prev_sales = db.execute_query(query_sales_count, params_prev)
        last_year_sales = db.execute_query(query_sales_count, params_last_year)
        ten_years_sales = db.execute_query(query_sales_count, params_ten_years)
        
        # Средний чек
        current_rev = float(current_revenue[0]['TotalRevenue']) if current_revenue else 0
        current_cnt = current_sales[0]['SalesCount'] if current_sales else 0
        avg_check = current_rev / current_cnt if current_cnt > 0 else 0
        
        # Активные клиенты (покупали в выбранном периоде)
        query_customers = f"""
            SELECT COUNT(DISTINCT s.fCUSTOMERID) as ActiveCustomers
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
        """
        active_customers = db.execute_query(query_customers, params_current)
        
        # Топ менеджер периода
        query_top_manager = f"""
            SELECT TOP 1 
                sa.fNAME as ManagerName,
                SUM(s.fTOTALSUM) as TotalSales
            FROM SALES s
            INNER JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
            GROUP BY sa.fNAME
            ORDER BY TotalSales DESC
        """
        top_manager = db.execute_query(query_top_manager, params_current)
        
        # Расчет процентов роста к прошлому месяцу
        prev_rev = float(prev_revenue[0]['TotalRevenue']) if prev_revenue else 0
        prev_cnt = prev_sales[0]['SalesCount'] if prev_sales else 0
        
        revenue_growth = ((current_rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else 0
        sales_growth = ((current_cnt - prev_cnt) / prev_cnt * 100) if prev_cnt > 0 else 0
        
        # Сравнение с прошлым годом
        last_year_rev = float(last_year_revenue[0]['TotalRevenue']) if last_year_revenue else 0
        last_year_cnt = last_year_sales[0]['SalesCount'] if last_year_sales else 0
        
        revenue_growth_yoy = ((current_rev - last_year_rev) / last_year_rev * 100) if last_year_rev > 0 else 0
        sales_growth_yoy = ((current_cnt - last_year_cnt) / last_year_cnt * 100) if last_year_cnt > 0 else 0
        
        # Сравнение с 10 лет назад
        ten_years_rev = float(ten_years_revenue[0]['TotalRevenue']) if ten_years_revenue else 0
        ten_years_cnt = ten_years_sales[0]['SalesCount'] if ten_years_sales else 0
        
        revenue_growth_10y = ((current_rev - ten_years_rev) / ten_years_rev * 100) if ten_years_rev > 0 else 0
        sales_growth_10y = ((current_cnt - ten_years_cnt) / ten_years_cnt * 100) if ten_years_cnt > 0 else 0
        
        return jsonify({
            'success': True,
            'data': {
                'period': {
                    'from': current_start.strftime('%Y-%m-%d'),
                    'to': current_end.strftime('%Y-%m-%d')
                },
                'total_revenue': {
                    'value': current_rev,
                    'growth': revenue_growth,
                    'growth_yoy': revenue_growth_yoy,
                    'growth_10y': revenue_growth_10y,
                    'prev_month': prev_rev,
                    'last_year': last_year_rev,
                    'ten_years_ago': ten_years_rev
                },
                'sales_count': {
                    'value': current_cnt,
                    'growth': sales_growth,
                    'growth_yoy': sales_growth_yoy,
                    'growth_10y': sales_growth_10y,
                    'prev_month': prev_cnt,
                    'last_year': last_year_cnt,
                    'ten_years_ago': ten_years_cnt
                },
                'avg_check': {
                    'value': avg_check
                },
                'active_customers': {
                    'value': active_customers[0]['ActiveCustomers'] if active_customers else 0
                },
                'top_manager': {
                    'name': top_manager[0]['ManagerName'] if top_manager else 'N/A',
                    'sales': float(top_manager[0]['TotalSales']) if top_manager else 0
                }
            }
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# API: МЕНЕДЖЕРЫ (SALESAGENTS)
# =============================================

@app.route('/api/managers')
def get_managers():
    """Получить список всех менеджеров со статистикой (с учетом ответственных групп) - ОПТИМИЗИРОВАННАЯ ВЕРСИЯ"""
    try:
        # Получить параметры даты из запроса
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        
        # Если параметры не указаны, использовать текущий месяц
        if not date_from or not date_to:
            today = datetime.now()
            date_from = today.replace(day=1).strftime('%Y-%m-%d')
            last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            date_to = last_day.strftime('%Y-%m-%d')
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получить фильтры
        excluded_filter, excluded_params = get_excluded_filter_sql()
        assignments = load_group_manager_assignments()
        
        # Построить SQL для всех менеджеров за один запрос
        # Для менеджеров с назначенными группами добавляем фильтр по группам
        managers_with_groups = {}
        for group_code, mgr_id in assignments.items():
            if mgr_id not in managers_with_groups:
                managers_with_groups[mgr_id] = []
            managers_with_groups[mgr_id].append(group_code)
        
        # Один большой запрос вместо N запросов для каждого менеджера
        query = f"""
            SELECT 
                sa.fID,
                sa.fCODE,
                sa.fNAME,
                sa.fCLOSED,
                COUNT(DISTINCT s.fCUSTOMERID) as CustomerCount,
                COUNT(s.fISN) as SalesCount,
                ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales,
                ISNULL(AVG(s.fTOTALSUM), 0) as AvgSale
            FROM SALESAGENTS sa
            LEFT JOIN SALES s ON s.fSALESAGENTID = sa.fID 
                AND s.fDATE >= ? 
                AND s.fDATE <= ?
                AND s.fSTATE = 2
            LEFT JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            WHERE sa.fCLOSED = 0
                {excluded_filter}
            GROUP BY sa.fID, sa.fCODE, sa.fNAME, sa.fCLOSED
            ORDER BY sa.fNAME
        """
        
        params = (date_from, date_to) + excluded_params
        cursor.execute(query, params)
        
        managers = []
        for row in cursor.fetchall():
            manager_id = row.fID
            
            # Проверить, есть ли у менеджера назначенные группы
            responsible_groups = managers_with_groups.get(manager_id, [])
            
            # Если есть назначенные группы, пересчитать статистику только для них
            if responsible_groups:
                placeholders = ','.join(['?'] * len(responsible_groups))
                group_filter = f" AND c.fGROUP IN ({placeholders})"
                group_params = tuple(responsible_groups)
                
                filtered_query = f"""
                    SELECT 
                        COUNT(DISTINCT s.fCUSTOMERID) as CustomerCount,
                        COUNT(s.fISN) as SalesCount,
                        ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales,
                        ISNULL(AVG(s.fTOTALSUM), 0) as AvgSale
                    FROM SALES s
                    INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
                    WHERE s.fDATE >= ? 
                        AND s.fDATE <= ?
                        AND s.fSALESAGENTID = ?
                        AND s.fSTATE = 2
                        {excluded_filter}
                        {group_filter}
                """
                
                cursor.execute(filtered_query, (date_from, date_to, manager_id) + excluded_params + group_params)
                filtered_stats = cursor.fetchone()
                
                managers.append({
                    'fID': row.fID,
                    'fCODE': row.fCODE,
                    'fNAME': row.fNAME,
                    'CustomerCount': filtered_stats.CustomerCount if filtered_stats else 0,
                    'SalesCount': filtered_stats.SalesCount if filtered_stats else 0,
                    'TotalSales': float(filtered_stats.TotalSales) if filtered_stats else 0,
                    'AvgSale': float(filtered_stats.AvgSale) if filtered_stats else 0,
                    'Debt': 0,  # Долг вычисляется отдельно при необходимости
                    'IsClosed': row.fCLOSED
                })
            else:
                # Нет назначенных групп - используем данные из основного запроса
                managers.append({
                    'fID': row.fID,
                    'fCODE': row.fCODE,
                    'fNAME': row.fNAME,
                    'CustomerCount': row.CustomerCount if row.CustomerCount else 0,
                    'SalesCount': row.SalesCount if row.SalesCount else 0,
                    'TotalSales': float(row.TotalSales) if row.TotalSales else 0,
                    'AvgSale': float(row.AvgSale) if row.AvgSale else 0,
                    'Debt': 0,
                    'IsClosed': row.fCLOSED
                })
        
        conn.close()
        
        # Сортировать по продажам
        managers.sort(key=lambda x: x['TotalSales'], reverse=True)
        
        return jsonify({'success': True, 'data': managers})
        
    except Exception as e:
        logger.error(f"Ошибка получения менеджеров: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/managers/<int:manager_id>')
def get_manager_detail(manager_id):
    """Получить детальную информацию о менеджере"""
    try:
        # Информация о менеджере
        query_info = """
            SELECT fID, fCODE, fNAME, fEXTERNALCODE, fCLOSED
            FROM SALESAGENTS
            WHERE fID = ?
        """
        manager_info = db.execute_query(query_info, (manager_id,))
        
        if not manager_info:
            return jsonify({'success': False, 'error': 'Менеджер не найден'}), 404
        
        # Статистика продаж за последние 12 месяцев
        query_sales = """
            SELECT 
                FORMAT(fDATE, 'yyyy-MM') as Month,
                COUNT(*) as SalesCount,
                SUM(fTOTALSUM) as TotalSum
            FROM SALES
            WHERE fSALESAGENTID = ?
            AND fDATE >= DATEADD(MONTH, -12, GETDATE())
            AND fSTATE = 2
            GROUP BY FORMAT(fDATE, 'yyyy-MM')
            ORDER BY Month
        """
        sales_by_month = db.execute_query(query_sales, (manager_id,))
        
        # Топ клиенты менеджера
        query_top_customers = """
            SELECT TOP 10
                c.fCODE,
                c.fNAME,
                COUNT(s.fISN) as OrderCount,
                SUM(s.fTOTALSUM) as TotalSum
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            WHERE s.fSALESAGENTID = ?
            AND s.fSTATE = 2
            GROUP BY c.fCODE, c.fNAME
            ORDER BY TotalSum DESC
        """
        top_customers = db.execute_query(query_top_customers, (manager_id,))
        
        # Преобразовать Decimal в float
        for month in sales_by_month:
            month['TotalSum'] = float(month['TotalSum'])
        
        for customer in top_customers:
            customer['TotalSum'] = float(customer['TotalSum'])
        
        return jsonify({
            'success': True,
            'data': {
                'info': manager_info[0],
                'sales_by_month': sales_by_month,
                'top_customers': top_customers
            }
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения деталей менеджера: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# API: ГРУППЫ (ДИСТРИБЬЮТОРЫ)
# =============================================

@app.route('/api/groups')
def get_groups():
    """Получить статистику по группам клиентов (дистрибьюторы)"""
    try:
        today = datetime.now()
        current_month_start = today.replace(day=1)
        
        query = """
            SELECT 
                c.fGROUP as GroupCode,
                COUNT(DISTINCT c.fID) as CustomerCount,
                COUNT(s.fISN) as SalesCount,
                ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales
            FROM CUSTOMERS c
            LEFT JOIN SALES s ON c.fID = s.fCUSTOMERID 
                AND s.fDATE >= ? 
                AND s.fSTATE = 2
            WHERE c.fGROUP IS NOT NULL AND c.fGROUP <> ''
            GROUP BY c.fGROUP
            ORDER BY TotalSales DESC
        """
        
        groups = db.execute_query(query, (current_month_start,))
        
        # Преобразовать Decimal в float
        for group in groups:
            group['TotalSales'] = float(group['TotalSales'])
        
        return jsonify({
            'success': True,
            'data': groups,
            'count': len(groups)
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения групп: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# API: ТЕРРИТОРИИ (SALES AREAS)
# =============================================

@app.route('/api/areas')
def get_areas():
    """Получить статистику по территориям продаж"""
    try:
        today = datetime.now()
        current_month_start = today.replace(day=1)
        
        query = """
            SELECT 
                fSALESAREA as AreaCode,
                COUNT(*) as SalesCount,
                ISNULL(SUM(fTOTALSUM), 0) as TotalSales,
                COUNT(DISTINCT fCUSTOMERID) as CustomerCount
            FROM SALES
            WHERE fDATE >= ?
            AND fSTATE = 2
            AND fSALESAREA IS NOT NULL
            GROUP BY fSALESAREA
            ORDER BY TotalSales DESC
        """
        
        areas = db.execute_query(query, (current_month_start,))
        
        # Преобразовать Decimal в float
        for area in areas:
            area['TotalSales'] = float(area['TotalSales'])
        
        return jsonify({
            'success': True,
            'data': areas,
            'count': len(areas)
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения территорий: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# API: ДИНАМИКА ПРОДАЖ ДЛЯ ГРАФИКОВ
# =============================================

@app.route('/api/dashboard/sales-chart')
def sales_chart():
    """Данные для графика продаж за последние 12 месяцев"""
    try:
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
        
        data = db.execute_query(query)
        
        # Преобразовать Decimal в float
        for row in data:
            row['TotalSum'] = float(row['TotalSum'])
        
        return jsonify({
            'success': True,
            'data': data
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения данных графика: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# API: ТОП МЕНЕДЖЕРЫ ДЛЯ ГРАФИКА
# =============================================

@app.route('/api/dashboard/top-managers')
def top_managers_chart():
    """Топ-10 менеджеров для графика"""
    try:
        today = datetime.now()
        current_month_start = today.replace(day=1)
        
        query = """
            SELECT TOP 10
                sa.fNAME as ManagerName,
                SUM(s.fTOTALSUM) as TotalSales
            FROM SALES s
            INNER JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
            WHERE s.fDATE >= ?
            AND s.fSTATE = 2
            GROUP BY sa.fNAME
            ORDER BY TotalSales DESC
        """
        
        data = db.execute_query(query, (current_month_start,))
        
        # Преобразовать Decimal в float
        for row in data:
            row['TotalSales'] = float(row['TotalSales'])
        
        return jsonify({
            'success': True,
            'data': data
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения топ менеджеров: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# API: ДИНАМИКА ЗА ПОСЛЕДНИЕ 10 ЛЕТ (ЭТОТ ЖЕ МЕСЯЦ)
# =============================================

@app.route('/api/dashboard/10years-chart')
def ten_years_chart():
    """График продаж за последние 10 лет для текущего месяца"""
    try:
        # Получить текущий месяц или месяц из параметров
        date_from = request.args.get('date_from', None)
        
        if date_from:
            current_date = datetime.strptime(date_from, '%Y-%m-%d')
        else:
            current_date = datetime.now()
        
        current_month = current_date.month
        current_year = current_date.year
        
        # Собрать данные за последние 10 лет для этого же месяца
        results = []
        
        for year_offset in range(10, -1, -1):  # От 10 лет назад до текущего года
            year = current_year - year_offset
            
            # Начало и конец месяца
            month_start = datetime(year, current_month, 1)
            if current_month == 12:
                month_end = datetime(year + 1, 1, 1)
            else:
                month_end = datetime(year, current_month + 1, 1)
            
            # Запрос для конкретного месяца и года
            query = """
                SELECT 
                    COUNT(*) as SalesCount,
                    ISNULL(SUM(fTOTALSUM), 0) as TotalSum
                FROM SALES
                WHERE fDATE >= ? AND fDATE < ?
                AND fSTATE = 2
            """
            
            data = db.execute_query(query, (month_start, month_end))
            
            if data:
                results.append({
                    'Year': year,
                    'Month': month_start.strftime('%Y-%m'),
                    'SalesCount': data[0]['SalesCount'],
                    'TotalSum': float(data[0]['TotalSum'])
                })
        
        return jsonify({
            'success': True,
            'data': results,
            'current_month': current_date.strftime('%B'),  # Название месяца
            'month_number': current_month
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения данных за 10 лет: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# СТРАНИЦЫ
# =============================================

@app.route('/managers')
def managers_page():
    """Страница менеджеров"""
    return render_template('managers.html')

@app.route('/groups')
def groups_page():
    """Страница групп (дистрибьюторы)"""
    return render_template('groups.html')

@app.route('/areas')
def areas_page():
    """Страница с территориями"""
    return render_template('areas.html')

@app.route('/reports')
def reports_page():
    """Страница с детальными отчетами"""
    return render_template('reports.html')

# =============================================
# ТЕСТОВАЯ СТРАНИЦА ДЛЯ ПРОВЕРКИ БД
# =============================================

@app.route('/test-db')
def test_db():
    """Тестовая страница для проверки подключения к БД"""
    try:
        # Проверка подключения
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Простой запрос
        cursor.execute("SELECT COUNT(*) FROM SALESAGENTS")
        managers_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM CUSTOMERS")
        customers_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM SALES")
        sales_count = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Подключение к БД успешно!',
            'data': {
                'managers': managers_count,
                'customers': customers_count,
                'sales': sales_count
            }
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# =============================================
# API: ОТЧЕТЫ
# =============================================

@app.route('/api/reports/managers')
def reports_managers():
    """API: Детальный отчет по менеджерам с расчетами"""
    try:
        db = DatabaseConnection()
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Параметры запроса
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        
        if not date_from or not date_to:
            date_from = datetime.now().replace(day=1).strftime('%Y-%m-%d')
            date_to = datetime.now().strftime('%Y-%m-%d')
        
        # Получаем данные по каждому менеджеру
        query = """
        SELECT 
            sa.fCODE as Code,
            sa.fNAME as Name,
            COUNT(*) as SalesCount,
            COALESCE(SUM(s.fTOTALSUM), 0) as TotalSales,
            COALESCE(SUM(CASE WHEN CAST(s.fDATE AS DATE) = CAST(? AS DATE) THEN s.fTOTALSUM ELSE 0 END), 0) as TodaySales,
            COUNT(DISTINCT CAST(s.fDATE AS DATE)) as WorkingDays
        FROM SALESAGENTS sa
        LEFT JOIN SALES s ON sa.fID = s.fSALESAGENTID 
            AND s.fDATE >= ? 
            AND s.fDATE <= ? 
            AND s.fSTATE = 2
        WHERE sa.fCLOSED = 0
        GROUP BY sa.fCODE, sa.fNAME, sa.fID
        ORDER BY TotalSales DESC
        """
        
        cursor.execute(query, (date_to, date_from, date_to))
        rows = cursor.fetchall()
        
        # Расчеты
        managers = []
        totals = {
            'plan': 39_500_000,
            'daily_plan': 0,
            'fact': 0,
            'percent': 0,
            'today': 0,
            'avg_daily': 0,
            'forecast': 0,
            'forecast_percent': 0,
            'credit_plan': 30_000_000,
            'credit_fact': 0,
            'credit_percent': 0,
            'collected': 0,
            'salary': 0,
            'bonus': 0
        }
        
        # Планы по менеджерам
        plans = {
            101: 6_500_000, 102: 3_500_000, 103: 4_000_000, 104: 3_000_000,
            105: 5_500_000, 106: 6_500_000, 107: 4_000_000, 110: 3_500_000, 108: 8_437_500
        }
        
        credit_plans = {
            101: 5_500_000, 102: 5_000_000, 103: 4_500_000, 104: 2_300_000,
            105: 3_000_000, 106: 5_000_000, 107: 3_500_000, 110: 3_500_000, 108: 8_437_500
        }
        
        for row in rows:
            code = row.Code
            name = row.Name
            sales_fact = float(row.TotalSales or 0)
            today_sales = float(row.TodaySales or 0)
            working_days = row.WorkingDays or 1
            
            sales_plan = plans.get(code, 3_000_000)
            daily_plan = sales_plan / 25
            avg_daily = sales_fact / working_days if working_days > 0 else 0
            forecast = avg_daily * 25
            sales_percent = round((sales_fact / sales_plan * 100) if sales_plan > 0 else 0, 1)
            forecast_percent = round((forecast / sales_plan * 100) if sales_plan > 0 else 0)
            
            credit_plan = credit_plans.get(code, 3_000_000)
            credit_query = """
                SELECT COALESCE(SUM(fTOTALSUM), 0) 
                FROM SALES 
                WHERE fSALESAGENTID = (SELECT fID FROM SALESAGENTS WHERE fCODE = ?) 
                AND fDATE >= ? AND fDATE <= ?
                AND fSTATE = 2
            """
            cursor.execute(credit_query, (code, date_from, date_to))
            credit_fact = float(cursor.fetchone()[0] or 0)
            credit_percent = round((credit_fact / credit_plan * 100) if credit_plan > 0 else 0)
            
            collected = sales_fact * 0.7
            base_salary = 200_000
            bonus = 0
            
            if sales_percent >= 100:
                bonus += sales_plan * 0.04
            elif sales_percent >= 90:
                bonus += sales_plan * 0.035
            elif sales_percent >= 80:
                bonus += sales_plan * 0.02
            
            salary = base_salary + bonus
            
            managers.append({
                'code': code, 'name': name, 'sales_plan': sales_plan, 'daily_plan': daily_plan,
                'sales_fact': sales_fact, 'sales_percent': sales_percent, 'today_sales': today_sales,
                'avg_daily': avg_daily, 'forecast': forecast, 'forecast_percent': forecast_percent,
                'credit_plan': credit_plan, 'credit_fact': credit_fact, 'credit_percent': credit_percent,
                'collected': collected, 'salary': salary, 'bonus': bonus
            })
            
            totals['daily_plan'] += daily_plan
            totals['fact'] += sales_fact
            totals['today'] += today_sales
            totals['avg_daily'] += avg_daily
            totals['forecast'] += forecast
            totals['credit_fact'] += credit_fact
            totals['collected'] += collected
            totals['salary'] += salary
            totals['bonus'] += bonus
        
        if totals['plan'] > 0:
            totals['percent'] = round(totals['fact'] / totals['plan'] * 100, 1)
            totals['forecast_percent'] = round(totals['forecast'] / totals['plan'] * 100)
        
        if totals['credit_plan'] > 0:
            totals['credit_percent'] = round(totals['credit_fact'] / totals['credit_plan'] * 100)
        
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'data': managers, 'totals': totals})
        
    except Exception as e:
        logger.error(f"Ошибка в reports_managers: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/reports/daily-sales')
def reports_daily_sales():
    """API: Дневные продажи текущего месяца"""
    try:
        db = DatabaseConnection()
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Параметры запроса
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        
        if not date_from or not date_to:
            date_from = datetime.now().replace(day=1).strftime('%Y-%m-%d')
            date_to = datetime.now().strftime('%Y-%m-%d')
        
        # Получаем дневные продажи
        query = """
        SELECT 
            CAST(fDATE AS DATE) as SaleDate,
            COALESCE(SUM(fTOTALSUM), 0) as TotalSales,
            COUNT(*) as SalesCount
        FROM SALES
        WHERE fDATE >= ? AND fDATE <= ? AND fSTATE = 2
        GROUP BY CAST(fDATE AS DATE)
        ORDER BY SaleDate
        """
        
        cursor.execute(query, (date_from, date_to))
        rows = cursor.fetchall()
        
        daily_data = []
        for row in rows:
            daily_data.append({
                'date': row.SaleDate.strftime('%d.%m.%Y'),
                'date_short': row.SaleDate.strftime('%d.%m'),
                'total_sales': float(row.TotalSales or 0),
                'sales_count': row.SalesCount
            })
        
        # Статистика
        if daily_data:
            sales_values = [d['total_sales'] for d in daily_data]
            stats = {
                'total': float(sum(sales_values)),
                'average': float(sum(sales_values) / len(sales_values)),
                'max': float(max(sales_values)),
                'min': float(min(sales_values)),
                'days_count': len(daily_data)
            }
        else:
            stats = {'total': 0, 'average': 0, 'max': 0, 'min': 0, 'days_count': 0}
        
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'data': daily_data, 'stats': stats})
        
    except Exception as e:
        logger.error(f"Ошибка в reports_daily_sales: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/debts')
def get_debts():
    """API: Получить информацию о долгах клиентов
    
    Формула: debt = debtFromDocuments - |type01| - |type02|
    где:
    - debtFromDocuments = SUM(D) - SUM(C) из HICUSTOMERSDEBT
    - type01 = SUM(fSUM) где fTYPE='01' из HIRESTCUSTOMERSSUM
    - type02 = SUM(fSUM) где fTYPE='02' из HIRESTCUSTOMERSSUM
    
    ИСКЛЮЧАЕМ неблагонадежных клиентов из расчетов
    """
    try:
        db = DatabaseConnection()
        excluded_filter, excluded_params = get_excluded_filter_sql()
        
        # 1. Расчет долга из документов (debtFromDocuments)
        # Дебет (D) добавляется, Кредит (C) вычитается
        query_debt_from_docs = f"""
            SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
            FROM HICUSTOMERSDEBT d
            INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
            INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
            WHERE 1=1 {excluded_filter}
        """
        debt_docs_result = db.execute_query(query_debt_from_docs, excluded_params)
        debt_from_documents = float(debt_docs_result[0]['DebtFromDocs']) if debt_docs_result else 0
        
        # 2. Получение остатков Type01 и Type02 (тоже фильтруем)
        query_rest_sums = f"""
            SELECT 
                ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
                ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
            FROM HIRESTCUSTOMERSSUM r
            INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
            WHERE 1=1 {excluded_filter}
        """
        rest_result = db.execute_query(query_rest_sums, excluded_params)
        type01 = float(rest_result[0]['Type01']) if rest_result else 0
        type02 = float(rest_result[0]['Type02']) if rest_result else 0
        
        # 3. Конечный долг = debtFromDocuments - |type01| - |type02|
        final_debt = debt_from_documents - abs(type01) - abs(type02)
        
        # 4. Количество клиентов с долгами (исключая неблагонадежных)
        query_customers_with_debt = f"""
            SELECT COUNT(DISTINCT doc.fCUSTOMERID) as DebtCustomersCount
            FROM HICUSTOMERSDEBT d
            INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
            INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
            WHERE d.fDBCR = 'D' AND d.fSUM > 0
            {excluded_filter}
        """
        debt_customers_result = db.execute_query(query_customers_with_debt, excluded_params)
        debt_customers_count = debt_customers_result[0]['DebtCustomersCount'] if debt_customers_result else 0
        
        # 5. ТОП 10 клиентов по долгам (исключая неблагонадежных)
        query_top_debtors = f"""
            SELECT TOP 10
                c.fNAME as CustomerName,
                c.fCODE as CustomerCode,
                SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as DebtAmount
            FROM HICUSTOMERSDEBT d
            INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
            INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
            WHERE 1=1 {excluded_filter}
            GROUP BY c.fNAME, c.fCODE
            HAVING SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) > 0
            ORDER BY DebtAmount DESC
        """
        top_debtors = db.execute_query(query_top_debtors, excluded_params)
        
        # Преобразовать Decimal в float
        top_debtors_list = []
        if top_debtors:
            for debtor in top_debtors:
                top_debtors_list.append({
                    'customer_name': debtor['CustomerName'],
                    'customer_code': debtor['CustomerCode'],
                    'debt_amount': float(debtor['DebtAmount'])
                })
        
        logger.info(f"[Debts] DebtFromDocs: {debt_from_documents}, Type01: {type01}, Type02: {type02}, Final: {final_debt}")
        
        return jsonify({
            'success': True,
            'debt_from_documents': debt_from_documents,
            'type01': type01,
            'type02': type02,
            'final_debt': final_debt,
            'debt_customers_count': debt_customers_count,
            'top_debtors': top_debtors_list
        })
        
    except Exception as e:
        logger.error(f"Ошибка в get_debts: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# НАСТРОЙКИ - API ENDPOINTS
# =============================================

@app.route('/settings')
def settings_page():
    """Страница настроек"""
    return render_template('settings.html')

# ===== Менеджеры =====
@app.route('/api/settings/managers')
def get_settings_managers():
    """Получить список всех менеджеров для настроек"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT fID, fCODE, fNAME
            FROM SALESAGENTS
            ORDER BY fNAME
        """
        cursor.execute(query)
        
        managers = []
        for row in cursor.fetchall():
            managers.append({
                'fID': row.fID,
                'fCODE': row.fCODE,
                'fNAME': row.fNAME,
                'storesCount': 0  # Будет заполнено позже
            })
        
        conn.close()
        return jsonify({'success': True, 'data': managers})
    except Exception as e:
        app.logger.error(f"[Settings] Error loading managers: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/managers/<int:manager_id>/stores')
def get_manager_stores(manager_id):
    """Получить магазины конкретного менеджера"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получаем клиентов (магазины) менеджера
        query = """
            SELECT DISTINCT c.fID, c.fCODE, c.fNAME, c.fGROUP
            FROM CUSTOMERS c
            INNER JOIN SALES s ON s.fCUSTOMERID = c.fID
            WHERE s.fSALESAGENTID = ?
            ORDER BY c.fNAME
        """
        cursor.execute(query, (manager_id,))
        
        stores = []
        for row in cursor.fetchall():
            stores.append({
                'fID': row.fID,
                'fCODE': row.fCODE,
                'fNAME': row.fNAME,
                'fGROUP': row.fGROUP if row.fGROUP else ''
            })
        
        conn.close()
        return jsonify({'success': True, 'data': stores})
    except Exception as e:
        app.logger.error(f"[Settings] Error loading manager stores: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/managers/assign-store', methods=['POST'])
def assign_store_to_manager():
    """Назначить магазин менеджеру (READ-ONLY: возвращаем информацию)"""
    try:
        data = request.get_json()
        manager_id = data.get('managerId')
        store_id = data.get('storeId')
        
        # В READ-ONLY режиме мы не можем изменять БД
        # Возвращаем успех, но на самом деле ничего не делаем
        app.logger.warning(f"[Settings] READ-ONLY: Cannot assign store {store_id} to manager {manager_id}")
        
        return jsonify({
            'success': True,
            'message': 'READ-ONLY режим: связи уже существуют в БД через таблицу SALES'
        })
    except Exception as e:
        app.logger.error(f"[Settings] Error assigning store: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/managers/unassign-store', methods=['POST'])
def unassign_store_from_manager():
    """Удалить связь магазина с менеджером (READ-ONLY: возвращаем информацию)"""
    try:
        data = request.get_json()
        manager_id = data.get('managerId')
        store_id = data.get('storeId')
        
        # В READ-ONLY режиме мы не можем изменять БД
        app.logger.warning(f"[Settings] READ-ONLY: Cannot unassign store {store_id} from manager {manager_id}")
        
        return jsonify({
            'success': True,
            'message': 'READ-ONLY режим: связи определяются таблицей SALES'
        })
    except Exception as e:
        app.logger.error(f"[Settings] Error unassigning store: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Магазины =====
@app.route('/api/settings/stores')
def get_settings_stores():
    """Получить список всех магазинов (клиентов)"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получаем всех клиентов с их менеджерами
        query = """
            SELECT DISTINCT 
                c.fID, 
                c.fCODE, 
                c.fNAME, 
                c.fGROUP,
                sa.fNAME as managerName
            FROM CUSTOMERS c
            LEFT JOIN SALES s ON s.fCUSTOMERID = c.fID
            LEFT JOIN SALESAGENTS sa ON sa.fID = s.fSALESAGENTID
            ORDER BY c.fNAME
        """
        cursor.execute(query)
        
        stores = []
        seen_ids = set()
        for row in cursor.fetchall():
            if row.fID not in seen_ids:
                stores.append({
                    'fID': row.fID,
                    'fCODE': row.fCODE,
                    'fNAME': row.fNAME,
                    'fGROUP': row.fGROUP if row.fGROUP else '',
                    'managerName': row.managerName if row.managerName else 'Не назначен'
                })
                seen_ids.add(row.fID)
        
        conn.close()
        return jsonify({'success': True, 'data': stores})
    except Exception as e:
        app.logger.error(f"[Settings] Error loading stores: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/stores/update-group', methods=['POST'])
def update_store_group():
    """Обновить группу магазина (READ-ONLY: не выполняется)"""
    try:
        data = request.get_json()
        store_id = data.get('storeId')
        group = data.get('group')
        
        # В READ-ONLY режиме не можем менять БД
        app.logger.warning(f"[Settings] READ-ONLY: Cannot update group for store {store_id} to {group}")
        
        return jsonify({
            'success': True,
            'message': 'READ-ONLY режим: изменения не применяются'
        })
    except Exception as e:
        app.logger.error(f"[Settings] Error updating store group: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Группы =====
@app.route('/api/settings/groups')
def get_settings_groups():
    """Получить список всех групп"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT DISTINCT fGROUP
            FROM CUSTOMERS
            WHERE fGROUP IS NOT NULL AND fGROUP != ''
            ORDER BY fGROUP
        """
        cursor.execute(query)
        
        groups = [row.fGROUP for row in cursor.fetchall()]
        
        conn.close()
        return jsonify({'success': True, 'data': groups})
    except Exception as e:
        app.logger.error(f"[Settings] Error loading groups: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/groups/add', methods=['POST'])
def add_group():
    """Добавить новую группу (READ-ONLY: не выполняется)"""
    try:
        data = request.get_json()
        name = data.get('name')
        
        app.logger.warning(f"[Settings] READ-ONLY: Cannot add group {name}")
        
        return jsonify({
            'success': True,
            'message': 'READ-ONLY режим: группы берутся из CUSTOMERS.fGROUP'
        })
    except Exception as e:
        app.logger.error(f"[Settings] Error adding group: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/groups/delete', methods=['POST'])
def delete_group():
    """Удалить группу (READ-ONLY: не выполняется)"""
    try:
        data = request.get_json()
        name = data.get('name')
        
        app.logger.warning(f"[Settings] READ-ONLY: Cannot delete group {name}")
        
        return jsonify({
            'success': True,
            'message': 'READ-ONLY режим: группы нельзя удалять'
        })
    except Exception as e:
        app.logger.error(f"[Settings] Error deleting group: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Дистрибьюторы =====
@app.route('/api/settings/distributors')
def get_settings_distributors():
    """Получить список дистрибьюторов (групп из CUSTOMERS.fGROUP)"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT 
                c.fGROUP,
                COUNT(DISTINCT c.fID) as customerCount
            FROM CUSTOMERS c
            WHERE c.fGROUP IS NOT NULL AND c.fGROUP != ''
            GROUP BY c.fGROUP
            ORDER BY c.fGROUP
        """
        cursor.execute(query)
        
        distributors = []
        for row in cursor.fetchall():
            distributors.append({
                'fGROUP': row.fGROUP,
                'customerCount': row.customerCount,
                'assignedManager': ''  # Заглушка, т.к. нет таблицы связей
            })
        
        conn.close()
        return jsonify({'success': True, 'data': distributors})
    except Exception as e:
        app.logger.error(f"[Settings] Error loading distributors: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/distributors/assign', methods=['POST'])
def assign_distributor():
    """Назначить дистрибьютора менеджеру (READ-ONLY: не выполняется)"""
    try:
        data = request.get_json()
        distributor_group = data.get('distributorGroup')
        manager_id = data.get('managerId')
        
        app.logger.warning(f"[Settings] READ-ONLY: Cannot assign distributor {distributor_group} to manager {manager_id}")
        
        return jsonify({
            'success': True,
            'message': 'READ-ONLY режим: связи определяются через SALES'
        })
    except Exception as e:
        app.logger.error(f"[Settings] Error assigning distributor: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Исключенные клиенты =====
EXCLUDED_CUSTOMERS_FILE = 'excluded_customers.json'
EXCLUDED_GROUPS_FILE = 'excluded_groups.json'
GROUP_MANAGER_ASSIGNMENTS_FILE = 'group_manager_assignments.json'

def load_excluded_customers():
    """Загрузить список исключенных клиентов из файла"""
    try:
        if os.path.exists(EXCLUDED_CUSTOMERS_FILE):
            with open(EXCLUDED_CUSTOMERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        app.logger.error(f"[Excluded] Error loading: {e}")
        return []

def save_excluded_customers(excluded_list):
    """Сохранить список исключенных клиентов в файл"""
    try:
        with open(EXCLUDED_CUSTOMERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(excluded_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[Excluded] Error saving: {e}")
        return False

def load_excluded_groups():
    """Загрузить список исключенных групп"""
    try:
        if os.path.exists(EXCLUDED_GROUPS_FILE):
            with open(EXCLUDED_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        app.logger.error(f"[ExcludedGroups] Error loading: {e}")
        return []

def save_excluded_groups(groups_list):
    """Сохранить список исключенных групп"""
    try:
        with open(EXCLUDED_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[ExcludedGroups] Error saving: {e}")
        return False

def load_group_manager_assignments():
    """Загрузить назначения менеджеров группам"""
    try:
        if os.path.exists(GROUP_MANAGER_ASSIGNMENTS_FILE):
            with open(GROUP_MANAGER_ASSIGNMENTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        app.logger.error(f"[GroupAssignments] Error loading: {e}")
        return {}

def save_group_manager_assignments(assignments):
    """Сохранить назначения менеджеров группам"""
    try:
        with open(GROUP_MANAGER_ASSIGNMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(assignments, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[GroupAssignments] Error saving: {e}")
        return False

def get_excluded_customer_ids():
    """Получить список ID исключенных клиентов (включая клиентов из исключенных групп)"""
    excluded = load_excluded_customers()
    excluded_ids = [item['customerId'] for item in excluded]
    
    # Добавить клиентов из исключенных групп
    excluded_groups = load_excluded_groups()
    if excluded_groups:
        try:
            conn = db.get_connection()
            cursor = conn.cursor()
            
            placeholders = ','.join('?' * len(excluded_groups))
            query = f"SELECT fID FROM CUSTOMERS WHERE fGROUP IN ({placeholders})"
            cursor.execute(query, excluded_groups)
            
            for row in cursor.fetchall():
                if row.fID not in excluded_ids:
                    excluded_ids.append(row.fID)
            
            conn.close()
        except Exception as e:
            app.logger.error(f"[Excluded] Error getting group customers: {e}")
    
    return excluded_ids

@app.route('/api/settings/excluded-customers')
def get_excluded_customers():
    """Получить список исключенных клиентов с их данными"""
    try:
        excluded = load_excluded_customers()
        excluded_ids = [item['customerId'] for item in excluded]
        
        if not excluded_ids:
            return jsonify({'success': True, 'data': []})
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получаем данные клиентов
        placeholders = ','.join('?' * len(excluded_ids))
        query = f"""
            SELECT 
                c.fID,
                c.fCODE,
                c.fNAME,
                c.fGROUP,
                ISNULL((
                    SELECT SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END)
                    FROM HICUSTOMERSDEBT d
                    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                    WHERE doc.fCUSTOMERID = c.fID
                ), 0) as debt,
                ISNULL((
                    SELECT SUM(s.fSALESSUM)
                    FROM SALES s
                    WHERE s.fCUSTOMERID = c.fID 
                    AND YEAR(s.fDATE) = 2025
                ), 0) as sales
            FROM CUSTOMERS c
            WHERE c.fID IN ({placeholders})
        """
        
        cursor.execute(query, excluded_ids)
        
        customers_data = []
        for row in cursor.fetchall():
            # Найти причину исключения
            reason = next((item['reason'] for item in excluded if item['customerId'] == row.fID), 'Неблагонадежный')
            
            customers_data.append({
                'fID': row.fID,
                'fCODE': row.fCODE,
                'fNAME': row.fNAME,
                'fGROUP': row.fGROUP if row.fGROUP else '',
                'debt': float(row.debt) if row.debt else 0,
                'sales': float(row.sales) if row.sales else 0,
                'excludeReason': reason
            })
        
        conn.close()
        return jsonify({'success': True, 'data': customers_data})
    except Exception as e:
        app.logger.error(f"[Excluded] Error loading customers: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/search-customers')
def search_customers():
    """Поиск клиентов для добавления в исключенные"""
    try:
        query = request.args.get('query', '')
        if len(query) < 2:
            return jsonify({'success': True, 'data': []})
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        search_query = f"%{query}%"
        sql = """
            SELECT TOP 50
                c.fID,
                c.fCODE,
                c.fNAME,
                c.fGROUP,
                ISNULL((
                    SELECT SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END)
                    FROM HICUSTOMERSDEBT d
                    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                    WHERE doc.fCUSTOMERID = c.fID
                ), 0) as debt
            FROM CUSTOMERS c
            WHERE c.fNAME LIKE ? OR c.fCODE LIKE ?
            ORDER BY c.fNAME
        """
        
        cursor.execute(sql, (search_query, search_query))
        
        customers = []
        for row in cursor.fetchall():
            customers.append({
                'fID': row.fID,
                'fCODE': row.fCODE,
                'fNAME': row.fNAME,
                'fGROUP': row.fGROUP if row.fGROUP else '',
                'debt': float(row.debt) if row.debt else 0
            })
        
        conn.close()
        return jsonify({'success': True, 'data': customers})
    except Exception as e:
        app.logger.error(f"[Excluded] Error searching customers: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/excluded-customers/add', methods=['POST'])
def add_excluded_customer():
    """Добавить клиента в список исключенных"""
    try:
        data = request.get_json()
        customer_id = data.get('customerId')
        reason = data.get('reason', 'Неблагонадежный')
        
        excluded = load_excluded_customers()
        
        # Проверить, не добавлен ли уже
        if any(item['customerId'] == customer_id for item in excluded):
            return jsonify({'success': False, 'error': 'Клиент уже в списке исключенных'})
        
        excluded.append({
            'customerId': customer_id,
            'reason': reason,
            'addedDate': datetime.now().isoformat()
        })
        
        if save_excluded_customers(excluded):
            app.logger.info(f"[Excluded] Added customer {customer_id} with reason: {reason}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        app.logger.error(f"[Excluded] Error adding customer: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/excluded-customers/remove', methods=['POST'])
def remove_excluded_customer():
    """Удалить клиента из списка исключенных"""
    try:
        data = request.get_json()
        customer_id = data.get('customerId')
        
        excluded = load_excluded_customers()
        excluded = [item for item in excluded if item['customerId'] != customer_id]
        
        if save_excluded_customers(excluded):
            app.logger.info(f"[Excluded] Removed customer {customer_id}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        app.logger.error(f"[Excluded] Error removing customer: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Исключенные группы =====
@app.route('/api/settings/groups-with-stats')
def get_groups_with_stats():
    """Получить группы с количеством клиентов"""
    try:
        app.logger.info("[Groups] Loading groups with stats...")
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT 
                c.fGROUP,
                COUNT(DISTINCT c.fID) as customerCount
            FROM CUSTOMERS c
            WHERE c.fGROUP IS NOT NULL AND c.fGROUP != ''
            GROUP BY c.fGROUP
            ORDER BY customerCount DESC
        """
        cursor.execute(query)
        
        groups = []
        for row in cursor.fetchall():
            groups.append({
                'fGROUP': row.fGROUP,
                'customerCount': row.customerCount,
                'isExcluded': False,  # Будет обновлено на клиенте
                'assignedManager': ''  # Будет обновлено на клиенте
            })
        
        conn.close()
        app.logger.info(f"[Groups] Loaded {len(groups)} groups")
        return jsonify({'success': True, 'data': groups})
    except Exception as e:
        app.logger.error(f"[Groups] Error loading with stats: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/excluded-groups')
def get_excluded_groups():
    """Получить список исключенных групп"""
    try:
        excluded_groups = load_excluded_groups()
        return jsonify({'success': True, 'data': excluded_groups})
    except Exception as e:
        app.logger.error(f"[ExcludedGroups] Error loading: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/excluded-groups/add', methods=['POST'])
def add_excluded_group():
    """Добавить группу в список исключенных"""
    try:
        data = request.get_json()
        group_code = data.get('groupCode')
        
        excluded_groups = load_excluded_groups()
        
        if group_code not in excluded_groups:
            excluded_groups.append(group_code)
            
            if save_excluded_groups(excluded_groups):
                app.logger.info(f"[ExcludedGroups] Added group {group_code}")
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Ошибка сохранения'})
        else:
            return jsonify({'success': False, 'error': 'Группа уже исключена'})
    except Exception as e:
        app.logger.error(f"[ExcludedGroups] Error adding: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/excluded-groups/remove', methods=['POST'])
def remove_excluded_group():
    """Удалить группу из списка исключенных"""
    try:
        data = request.get_json()
        group_code = data.get('groupCode')
        
        excluded_groups = load_excluded_groups()
        excluded_groups = [g for g in excluded_groups if g != group_code]
        
        if save_excluded_groups(excluded_groups):
            app.logger.info(f"[ExcludedGroups] Removed group {group_code}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        app.logger.error(f"[ExcludedGroups] Error removing: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Назначение менеджеров группам =====
@app.route('/api/settings/group-manager-assignments')
def get_group_manager_assignments():
    """Получить назначения менеджеров группам"""
    try:
        assignments = load_group_manager_assignments()
        return jsonify({'success': True, 'data': assignments})
    except Exception as e:
        app.logger.error(f"[GroupAssignments] Error loading: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/group-manager-assignments/set', methods=['POST'])
def set_group_manager_assignment():
    """Назначить менеджера группе"""
    try:
        data = request.get_json()
        group_code = data.get('groupCode')
        manager_id = data.get('managerId')
        
        assignments = load_group_manager_assignments()
        
        if manager_id:
            assignments[group_code] = int(manager_id)
        else:
            # Удалить назначение если менеджер не выбран
            assignments.pop(group_code, None)
        
        if save_group_manager_assignments(assignments):
            app.logger.info(f"[GroupAssignments] Set group {group_code} to manager {manager_id}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        app.logger.error(f"[GroupAssignments] Error setting: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# ЗАПУСК ПРИЛОЖЕНИЯ
# =============================================

if __name__ == '__main__':
    print("=" * 80)
    print("🚀 Sales Dashboard v2.0 запускается...")
    print("=" * 80)
    print()
    db_name = os.environ.get('SALES_DB', 'SalesManagement')
    print(f"📊 Подключение к БД: {db_name}")
    print("🌐 Сервер: http://localhost:5000")
    print()
    print("Доступные страницы:")
    print("  • http://localhost:5000/          - Dashboard")
    print("  • http://localhost:5000/managers  - Менеджеры")
    print("  • http://localhost:5000/groups    - Дистрибьюторы")
    print("  • http://localhost:5000/areas     - Территории")
    print("  • http://localhost:5000/settings  - Настройки")
    print("  • http://localhost:5000/test-db   - Тест БД")
    print()
    print("=" * 80)
    
    app.run(debug=True, host='0.0.0.0', port=5000)


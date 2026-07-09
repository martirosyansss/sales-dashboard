"""
Sales Dashboard v2.0 - READ-ONLY Analytics Platform
Работает с реальной БД AS-Sales Management
"""

from flask import Flask, render_template, jsonify, request, send_file
import pyodbc
from datetime import datetime, timedelta
import os
import json
from typing import Dict, List, Any
import logging
from dotenv import load_dotenv
import anthropic
# import io
# from openpyxl import Workbook
# from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
# from openpyxl.utils import get_column_letter

# Настройка логирования
# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'sales-dashboard-secret-key-2025')

# API Keys
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

# =============================================
# ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ
# =============================================

class DatabaseConnection:
    """Класс для работы с БД AS-Sales Management"""
    
    def __init__(self):
        # Имя базы можно задать через переменную окружения SALES_DB
        db_name = os.environ.get('SALES_DB', 'SalesManagement')
        self.connection_string = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            "SERVER=192.168.1.4;"
            f"DATABASE={db_name};"
            "UID=sa;"
            "PWD=CHANGE_ME;"
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

# SQL-выражение: первый день ТЕКУЩЕГО календарного месяца.
# Используется как ПРАВАЯ (верхняя) граница окон истории в планах и сезонности,
# чтобы окна состояли строго из ПОЛНЫХ завершённых месяцев. Иначе скользящее окно
# от GETDATE() захватывает текущий месяц частично на обоих концах (текущий месяц
# попадает в 24-мес. окно трижды вместо двух раз) и систематически искажает
# коэффициенты сезонности и средние продажи именно для текущего месяца.
CURRENT_MONTH_START_SQL = "DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1)"

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
    # Новая структура: {"groupCode": [managerId1, managerId2, ...]}
    responsible_groups = []
    for group, manager_ids in assignments.items():
        # Поддержка старого формата (int) и нового формата (list)
        if isinstance(manager_ids, list):
            if manager_id in manager_ids:
                responsible_groups.append(group)
        elif manager_ids == manager_id:  # старый формат
            responsible_groups.append(group)
    
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
    """Получить основную статистику для Dashboard с фильтрами по датам и территориям"""
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
            {areas_join}
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
            {dashboard_areas_filter}
            {dashboard_groups_filter}
        """
        
        params_current = (current_start, current_end) + excluded_params + product_groups_params + dashboard_areas_params + dashboard_groups_params
        params_prev = (prev_start, prev_end) + excluded_params + product_groups_params + dashboard_areas_params + dashboard_groups_params
        params_last_year = (last_year_start, last_year_end) + excluded_params + product_groups_params + dashboard_areas_params + dashboard_groups_params
        params_ten_years = (ten_years_ago_start, ten_years_ago_end) + excluded_params + product_groups_params + dashboard_areas_params + dashboard_groups_params
        
        current_revenue = db.execute_query(query_revenue, params_current)
        prev_revenue = db.execute_query(query_revenue, params_prev)
        last_year_revenue = db.execute_query(query_revenue, params_last_year)
        ten_years_revenue = db.execute_query(query_revenue, params_ten_years)
        
        # Количество продаж
        query_sales_count = f"""
            SELECT COUNT(s.fISN) as SalesCount
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            {areas_join}
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
            {dashboard_areas_filter}
            {dashboard_groups_filter}
        """
        current_sales = db.execute_query(query_sales_count, params_current)
        prev_sales = db.execute_query(query_sales_count, params_prev)
        last_year_sales = db.execute_query(query_sales_count, params_last_year)
        ten_years_sales = db.execute_query(query_sales_count, params_ten_years)
        
        # Средний чек
        current_rev = float(current_revenue[0]['TotalRevenue']) if current_revenue else 0
        current_cnt = current_sales[0]['SalesCount'] if current_sales else 0
        avg_check = current_rev / current_cnt if current_cnt > 0 else 0
        
        # Средний чек прошлого месяца и прошлого года
        prev_rev = float(prev_revenue[0]['TotalRevenue']) if prev_revenue else 0
        prev_cnt = prev_sales[0]['SalesCount'] if prev_sales else 0
        prev_avg_check = prev_rev / prev_cnt if prev_cnt > 0 else 0
        
        last_year_rev = float(last_year_revenue[0]['TotalRevenue']) if last_year_revenue else 0
        last_year_cnt = last_year_sales[0]['SalesCount'] if last_year_sales else 0
        last_year_avg_check = last_year_rev / last_year_cnt if last_year_cnt > 0 else 0
        
        # Активные клиенты (покупали в выбранном периоде)
        query_customers = f"""
            SELECT COUNT(DISTINCT s.fCUSTOMERID) as ActiveCustomers
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            {areas_join}
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
            {dashboard_areas_filter}
            {dashboard_groups_filter}
        """
        active_customers = db.execute_query(query_customers, params_current)
        prev_customers = db.execute_query(query_customers, params_prev)
        last_year_customers = db.execute_query(query_customers, params_last_year)
        
        # Топ менеджер периода
        query_top_manager = f"""
            SELECT TOP 1 
                sa.fNAME as ManagerName,
                SUM(s.fTOTALSUM) as TotalSales
            FROM SALES s
            INNER JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            {areas_join}
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
            {dashboard_areas_filter}
            {dashboard_groups_filter}
            GROUP BY sa.fNAME
            ORDER BY TotalSales DESC
        """
        top_manager = db.execute_query(query_top_manager, params_current)
        
        # Расчет процентов роста к прошлому месяцу
        revenue_growth = ((current_rev - prev_rev) / prev_rev * 100) if prev_rev > 0 else 0
        sales_growth = ((current_cnt - prev_cnt) / prev_cnt * 100) if prev_cnt > 0 else 0
        
        # Сравнение с прошлым годом
        revenue_growth_yoy = ((current_rev - last_year_rev) / last_year_rev * 100) if last_year_rev > 0 else 0
        sales_growth_yoy = ((current_cnt - last_year_cnt) / last_year_cnt * 100) if last_year_cnt > 0 else 0
        
        # Сравнение с 10 лет назад
        ten_years_rev = float(ten_years_revenue[0]['TotalRevenue']) if ten_years_revenue else 0
        ten_years_cnt = ten_years_sales[0]['SalesCount'] if ten_years_sales else 0
        
        revenue_growth_10y = ((current_rev - ten_years_rev) / ten_years_rev * 100) if ten_years_rev > 0 else 0
        sales_growth_10y = ((current_cnt - ten_years_cnt) / ten_years_cnt * 100) if ten_years_cnt > 0 else 0
        
        # === СЕГОДНЯШНИЕ МЕТРИКИ ===
        # Выручка, продажи, средний чек и клиенты за сегодня
        today_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_date + timedelta(days=1)
        last_year_today = today_date.replace(year=today_date.year - 1)
        last_year_today_end = last_year_today + timedelta(days=1)
        
        params_today = (today_date, today_end) + excluded_params + product_groups_params + dashboard_areas_params + dashboard_groups_params
        params_last_year_today = (last_year_today, last_year_today_end) + excluded_params + product_groups_params + dashboard_areas_params + dashboard_groups_params
        
        # Выручка сегодня
        today_revenue = db.execute_query(query_revenue, params_today)
        last_year_today_revenue = db.execute_query(query_revenue, params_last_year_today)
        
        # Продажи сегодня
        today_sales = db.execute_query(query_sales_count, params_today)
        last_year_today_sales = db.execute_query(query_sales_count, params_last_year_today)
        
        # Клиенты сегодня
        today_customers = db.execute_query(query_customers, params_today)
        last_year_today_customers = db.execute_query(query_customers, params_last_year_today)
        
        # Средний чек сегодня
        today_rev = float(today_revenue[0]['TotalRevenue']) if today_revenue else 0
        today_cnt = today_sales[0]['SalesCount'] if today_sales else 0
        today_avg_check = today_rev / today_cnt if today_cnt > 0 else 0
        
        last_year_today_rev = float(last_year_today_revenue[0]['TotalRevenue']) if last_year_today_revenue else 0
        last_year_today_cnt = last_year_today_sales[0]['SalesCount'] if last_year_today_sales else 0
        last_year_today_avg_check = last_year_today_rev / last_year_today_cnt if last_year_today_cnt > 0 else 0
        
        # === ПРОГНОЗ ПРОДАЖ НА МЕСЯЦ ===
        # Рассчитываем прогноз на основе текущих темпов продаж (исключая воскресенья)
        
        # Считаем рабочие дни (исключая воскресенья)
        working_days_passed = 0
        check_date = current_start
        end_date = min(today, current_end)  # Берем минимум из сегодня и конца периода
        
        while check_date <= end_date:
            if check_date.weekday() != 6:  # 6 = воскресенье
                working_days_passed += 1
            check_date += timedelta(days=1)
        
        # Считаем общее количество рабочих дней в месяце
        total_working_days = 0
        check_date = current_start
        while check_date < current_end:  # Используем < вместо <= для конца периода
            if check_date.weekday() != 6:  # 6 = воскресенье
                total_working_days += 1
            check_date += timedelta(days=1)
        
        if working_days_passed > 0 and total_working_days > 0:
            daily_average = current_rev / working_days_passed
            monthly_forecast = daily_average * total_working_days
        else:
            monthly_forecast = 0
        
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
                    'value': avg_check,
                    'prev_month': prev_avg_check,
                    'last_year': last_year_avg_check
                },
                'active_customers': {
                    'value': active_customers[0]['ActiveCustomers'] if active_customers else 0,
                    'prev_month': prev_customers[0]['ActiveCustomers'] if prev_customers else 0,
                    'last_year': last_year_customers[0]['ActiveCustomers'] if last_year_customers else 0
                },
                'today_revenue': {
                    'value': today_rev,
                    'last_year': last_year_today_rev
                },
                'today_sales': {
                    'value': today_cnt,
                    'last_year': last_year_today_cnt
                },
                'today_avg_check': {
                    'value': today_avg_check,
                    'last_year': last_year_today_avg_check
                },
                'today_customers': {
                    'value': today_customers[0]['ActiveCustomers'] if today_customers else 0,
                    'last_year': last_year_today_customers[0]['ActiveCustomers'] if last_year_today_customers else 0
                },
                'monthly_forecast': {
                    'value': monthly_forecast,
                    'days_passed': working_days_passed,
                    'total_days': total_working_days,
                    'current_sales': current_rev
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
        sales_area = request.args.get('sales_area')
        if sales_area:
            sales_area = sales_area.strip() or None
        sales_area_clause = ""
        sales_area_params = ()
        if sales_area:
            sales_area_clause = " AND s.fSALESAREA = ?"
            sales_area_params = (sales_area,)
        
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
        product_groups_filter, product_groups_params = get_product_groups_filter_sql()
        assignments = load_group_manager_assignments()
        
        # Построить SQL для всех менеджеров за один запрос
        # Для менеджеров с назначенными группами добавляем фильтр по группам
        managers_with_groups = {}
        for group_code, manager_ids in assignments.items():
            # Поддержка старого формата (int) и нового (list)
            if not isinstance(manager_ids, list):
                manager_ids = [manager_ids]
            
            for mgr_id in manager_ids:
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
                {sales_area_clause}
            LEFT JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            WHERE sa.fCLOSED = 0
                {excluded_filter}
                {product_groups_filter}
            GROUP BY sa.fID, sa.fCODE, sa.fNAME, sa.fCLOSED
            ORDER BY sa.fNAME
        """
        
        params = (date_from, date_to) + sales_area_params + excluded_params + product_groups_params
        cursor.execute(query, params)
        
        # Сохранить результаты основного запроса
        manager_rows = cursor.fetchall()
        
        # Получить Sales Areas для всех агентов
        cursor.execute("""
            SELECT sa.fSALESAGENTID, sa.fSALESAREA, sa.fDEFAULT, t.fCAPTION
            FROM SALESAGENTAREAS sa
            LEFT JOIN TREES t ON t.fCODE = sa.fSALESAREA AND t.fTREEID = 'SArea'
            ORDER BY sa.fSALESAGENTID, sa.fDEFAULT DESC, sa.fROWNUM
        """)
        
        sales_areas_map = {}
        for area_row in cursor.fetchall():
            agent_id = area_row.fSALESAGENTID
            if agent_id not in sales_areas_map:
                sales_areas_map[agent_id] = []
            sales_areas_map[agent_id].append({
                'code': area_row.fSALESAREA,
                'name': area_row.fCAPTION if area_row.fCAPTION else str(area_row.fSALESAREA),
                'is_default': bool(area_row.fDEFAULT)
            })
        
        managers = []
        for row in manager_rows:
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
                        {sales_area_clause}
                        {excluded_filter}
                        {group_filter}
                        {product_groups_filter}
                """
                
                cursor.execute(
                    filtered_query,
                    (date_from, date_to, manager_id) + sales_area_params + excluded_params + group_params + product_groups_params
                )
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
                    'IsClosed': row.fCLOSED,
                    'SalesAreas': sales_areas_map.get(manager_id, [])
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
                    'IsClosed': row.fCLOSED,
                    'SalesAreas': sales_areas_map.get(manager_id, [])
                })
        
        conn.close()
        
        if sales_area:
            managers = [
                manager for manager in managers
                if any(area['code'] == sales_area for area in manager.get('SalesAreas', []))
            ]

        # Фильтровать только менеджеров с продажами за выбранный период
        active_managers = [m for m in managers if m['SalesCount'] > 0]
        
        # Добавить расчет долга для активных менеджеров
        if active_managers:
            conn = db.get_connection()
            cursor = conn.cursor()
            customer_subquery = f"""
                SELECT DISTINCT s.fCUSTOMERID
                FROM SALES s
                WHERE s.fSALESAGENTID = ?
                    AND s.fDATE >= ?
                    AND s.fDATE <= ?
                    AND s.fSTATE = 2
                    {sales_area_clause}
                    {product_groups_filter}
            """
            
            for manager in active_managers:
                manager_id = manager['fID']
                responsible_groups = managers_with_groups.get(manager_id, [])
                
                # ВАЖНО: Показываем долг ТОЛЬКО если у менеджера есть назначенные группы в settings
                if not responsible_groups:
                    manager['Debt'] = 0
                    continue

                if manager['SalesCount'] == 0:
                    manager['Debt'] = 0
                    continue
                
                # Формируем запрос долга с учетом групп (ОБЯЗАТЕЛЬНО фильтруем по группам)
                placeholders = ','.join(['?'] * len(responsible_groups))
                group_filter = f" AND c.fGROUP IN ({placeholders})"
                group_params = tuple(responsible_groups)
                
                # ПРАВИЛЬНАЯ ФОРМУЛА ДОЛГА:
                # ДОЛГ = debtFromDocuments - |type01| - |type02|
                # где debtFromDocuments = SUM(D) - SUM(C) из HICUSTOMERSDEBT
                
                # 1. Получаем долг из документов
                debt_query = f"""
                    SELECT 
                        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
                    FROM HICUSTOMERSDEBT d
                    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
                    WHERE doc.fCUSTOMERID IN (
                        {customer_subquery}
                    )
                        {excluded_filter}
                        {group_filter}
                """
                
                customer_subquery_params = (manager_id, date_from, date_to) + sales_area_params + product_groups_params
                all_params = customer_subquery_params + excluded_params + group_params
                cursor.execute(debt_query, all_params)
                debt_row = cursor.fetchone()
                debt_from_docs = float(debt_row.DebtFromDocs) if debt_row and debt_row.DebtFromDocs else 0
                
                # 2. Получаем остатки Type01 и Type02
                rest_query = f"""
                    SELECT 
                        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
                        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
                    FROM HIRESTCUSTOMERSSUM r
                    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
                    WHERE r.fCUSTOMERID IN (
                        {customer_subquery}
                    )
                        {excluded_filter}
                        {group_filter}
                """
                
                cursor.execute(rest_query, all_params)
                rest_row = cursor.fetchone()
                type01 = float(rest_row.Type01) if rest_row and rest_row.Type01 else 0
                type02 = float(rest_row.Type02) if rest_row and rest_row.Type02 else 0
                
                # 3. Итоговый долг = debtFromDocuments - |type01| - |type02|
                manager['Debt'] = debt_from_docs - abs(type01) - abs(type02)
            
            conn.close()
        
        # Сортировать по продажам
        active_managers.sort(key=lambda x: x['TotalSales'], reverse=True)
        
        return jsonify({'success': True, 'data': active_managers})
        
    except Exception as e:
        logger.error(f"Ошибка получения менеджеров: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/api/sales-areas')
def get_sales_areas():
    """Получить данные по Sales Areas (территориям)"""
    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        
        if not date_from or not date_to:
            today = datetime.now()
            date_from = today.replace(day=1).strftime('%Y-%m-%d')
            last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            date_to = last_day.strftime('%Y-%m-%d')
        
        raw_group_filter = request.args.get('groups')
        requested_groups = []
        if raw_group_filter:
            for grp in raw_group_filter.split(','):
                grp = grp.strip()
                if grp and grp not in requested_groups:
                    requested_groups.append(grp)

        raw_sales_groups_filter = request.args.get('sales_groups')
        requested_sales_groups = []
        if raw_sales_groups_filter:
            for grp in raw_sales_groups_filter.split(','):
                grp = grp.strip()
                if grp and grp not in requested_sales_groups:
                    requested_sales_groups.append(grp)

        raw_division_filter = request.args.get('divisions')
        requested_divisions = []
        if raw_division_filter:
            for div in raw_division_filter.split(','):
                div = div.strip()
                if div and div not in requested_divisions:
                    requested_divisions.append(div)
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Загрузить настройки групп для менеджеров и территорий
        assignments = load_group_manager_assignments()
        area_group_assignments = load_sales_area_group_assignments()
        managers_with_groups = {}
        for group_code, manager_ids in assignments.items():
            if not isinstance(manager_ids, list):
                manager_ids = [manager_ids]
            for mgr_id in manager_ids:
                if mgr_id not in managers_with_groups:
                    managers_with_groups[mgr_id] = []
                managers_with_groups[mgr_id].append(group_code)
        
        def resolve_effective_groups(manager_id, area_code):
            """Вернуть список групп для вычисления (None = исключить менеджера)."""
            responsible_groups = managers_with_groups.get(manager_id, [])
            area_specific_groups = area_group_assignments.get(area_code, [])
            if requested_groups:
                allowed = [grp for grp in requested_groups if grp]
                if area_specific_groups:
                    filtered = [grp for grp in area_specific_groups if grp in allowed]
                    return filtered if filtered else None
                if responsible_groups:
                    filtered = [grp for grp in responsible_groups if grp in allowed]
                    return filtered if filtered else None
                return allowed
            if area_specific_groups:
                return area_specific_groups
            if responsible_groups:
                return responsible_groups
            return []
        
        # Получить фильтры
        excluded_filter, excluded_params = get_excluded_filter_sql()
        product_groups_filter, product_groups_params = get_product_groups_filter_sql()
        
        # Получить все Sales Areas из TREES
        cursor.execute("""
            SELECT fCODE, fCAPTION
            FROM TREES
            WHERE fTREEID = 'SArea'
            ORDER BY fCODE
        """)
        
        all_areas = {}
        for row in cursor.fetchall():
            all_areas[row.fCODE] = {
                'code': row.fCODE,
                'name': row.fCAPTION,
                'TotalSales': 0,
                'CustomerCount': 0,
                'SalesCount': 0,
                'AvgSale': 0,
                'Debt': 0,
                'InitialDebt': 0,
                'Payments': 0,
                'Managers': [],
                'MonthlyHistory': []  # Новое поле для истории по месяцам
            }
        
        # Получить менеджеров для каждой Sales Area
        cursor.execute("""
            SELECT sa.fSALESAGENTID, sa.fSALESAREA, sa.fDEFAULT,
                   ag.fCODE as ManagerCode, ag.fNAME as ManagerName
            FROM SALESAGENTAREAS sa
            INNER JOIN SALESAGENTS ag ON sa.fSALESAGENTID = ag.fID
            WHERE ag.fCLOSED = 0
            ORDER BY sa.fSALESAREA, sa.fDEFAULT DESC
        """)
        
        area_managers = {}
        for row in cursor.fetchall():
            area_code = row.fSALESAREA
            if area_code not in area_managers:
                area_managers[area_code] = []
            area_managers[area_code].append({
                'id': row.fSALESAGENTID,
                'code': row.ManagerCode,
                'name': row.ManagerName,
                'is_default': bool(row.fDEFAULT)
            })
        
        # Добавить менеджеров к areas
        for area_code, managers in area_managers.items():
            if area_code in all_areas:
                all_areas[area_code]['Managers'] = managers
        
        # Вычислить исторические диапазоны дат
        date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
        date_to_dt = datetime.strptime(date_to, '%Y-%m-%d')
        
        # Прошлый месяц (сдвиг на 1 месяц назад)
        # Безопасный способ вычитания месяца
        def subtract_month(dt):
            """Вычесть один месяц от даты, обрабатывая переполнение дней"""
            month = dt.month - 1
            year = dt.year
            if month < 1:
                month = 12
                year -= 1
            
            # Обработка дней - если день больше чем дней в целевом месяце
            import calendar
            max_day = calendar.monthrange(year, month)[1]
            day = min(dt.day, max_day)
            
            return dt.replace(year=year, month=month, day=day)
        
        prev_month_from = subtract_month(date_from_dt)
        prev_month_to = subtract_month(date_to_dt)
        
        # Прошлый год (сдвиг на 1 год назад)
        # Обработка 29 февраля
        def subtract_year(dt):
            """Вычесть один год от даты, обрабатывая високосные года"""
            year = dt.year - 1
            # Если исходная дата - 29 февраля, а прошлый год не високосный
            if dt.month == 2 and dt.day == 29:
                import calendar
                if not calendar.isleap(year):
                    return dt.replace(year=year, day=28)
            return dt.replace(year=year)
        
        last_year_from = subtract_year(date_from_dt)
        last_year_to = subtract_year(date_to_dt)
        
        prev_month_from_str = prev_month_from.strftime('%Y-%m-%d')
        prev_month_to_str = prev_month_to.strftime('%Y-%m-%d')
        last_year_from_str = last_year_from.strftime('%Y-%m-%d')
        last_year_to_str = last_year_to.strftime('%Y-%m-%d')
        
        # Groups filter: для долгов и оплат (фильтр по группам клиентов)
        # Определяем ДО циклов, так как используется в обоих циклах
        group_filter = ""
        group_params = tuple()
        if requested_groups:
            placeholders = ','.join(['?'] * len(requested_groups))
            group_filter = f" AND c.fGROUP IN ({placeholders})"
            group_params = tuple(requested_groups)
        
        # Получить продажи и долги по Sales Areas
        # Используем тот же подход, что и в /api/customers - через CUSTOMERSALESAREAS
        for area_code, area_data in all_areas.items():
            # Divisions filter: для продаж (фильтр по товарным группам)
            division_filter = ""
            division_params = tuple()
            if requested_divisions:
                placeholders = ','.join(['?'] * len(requested_divisions))
                division_filter = f"""
                    AND s.fSALESAGENTID IN (
                        SELECT DISTINCT fSALESAGENTID 
                        FROM SALESAGENTDIVISIONS 
                        WHERE fDIVISION IN ({placeholders})
                    )
                """
                division_params = tuple(requested_divisions)
            
            # Sales groups filter: для продаж (фильтр по группам клиентов)
            sales_group_filter = ""
            sales_group_params = tuple()
            if requested_sales_groups:
                placeholders = ','.join(['?'] * len(requested_sales_groups))
                sales_group_filter = f" AND c.fGROUP IN ({placeholders})"
                sales_group_params = tuple(requested_sales_groups)
            
            # 1. Получить продажи для клиентов этой Sales Area (используем divisions + sales_groups filter)
            query_sales = f"""
                SELECT 
                    COUNT(DISTINCT s.fCUSTOMERID) AS CustomerCount,
                    COUNT(s.fISN) AS SalesCount,
                    ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales,
                    ISNULL(SUM(CASE WHEN s.fPAYTYPE = 2 THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales,
                    ISNULL(AVG(s.fTOTALSUM), 0) AS AvgSale,
                    ISNULL(SUM(d.DiscountAmount), 0) AS TotalDiscount
                FROM SALES s
                INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
                INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                OUTER APPLY (
                    SELECT SUM(sd.fPRICE * sd.fQUANTITY - sd.fSUM) as DiscountAmount
                    FROM SALEDOCDETAILS sd
                    WHERE sd.fISN = s.fISN
                ) d
                WHERE csa.fSALESAREA = ?
                    AND s.fDATE >= ?
                    AND s.fDATE < DATEADD(day, 1, CAST(? AS DATE))
                    AND s.fSTATE = 2
                    {excluded_filter}
                    {product_groups_filter}
                    {division_filter}
                    {sales_group_filter}
            """
            
            sales_params = (area_code, date_from, date_to) + excluded_params + product_groups_params + division_params + sales_group_params
            cursor.execute(query_sales, sales_params)
            sales_row = cursor.fetchone()
            
            if sales_row:
                area_data['CustomerCount'] = sales_row.CustomerCount or 0
                area_data['SalesCount'] = sales_row.SalesCount or 0
                area_data['TotalSales'] = float(sales_row.TotalSales) if sales_row.TotalSales else 0
                area_data['CreditSales'] = float(sales_row.CreditSales) if sales_row.CreditSales else 0
                area_data['AvgSale'] = float(sales_row.AvgSale) if sales_row.AvgSale else 0
                area_data['TotalDiscount'] = float(sales_row.TotalDiscount) if sales_row.TotalDiscount else 0
                
                # Calculate Discount Percent
                total_original = area_data['TotalSales'] + area_data['TotalDiscount']
                if total_original > 0:
                    area_data['DiscountPercent'] = (area_data['TotalDiscount'] / total_original) * 100
                else:
                    area_data['DiscountPercent'] = 0
            
            # 2. Получить долг для клиентов этой Sales Area (используем ТОЛЬКО groups filter)
            query_debt = f"""
                SELECT 
                    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
                FROM HICUSTOMERSDEBT d
                INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
                INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                WHERE csa.fSALESAREA = ?
                    {excluded_filter}
                    {group_filter}
            """
            
            debt_params = (area_code,) + excluded_params + group_params
            cursor.execute(query_debt, debt_params)
            debt_row = cursor.fetchone()
            debt_from_docs = float(debt_row.DebtFromDocs) if debt_row and debt_row.DebtFromDocs else 0
            
            # 3. Получить остатки Type01 и Type02 (divisions не применяются)
            query_rest = f"""
                SELECT 
                    ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
                    ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
                FROM HIRESTCUSTOMERSSUM r
                INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
                INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                WHERE csa.fSALESAREA = ?
                    {excluded_filter}
                    {group_filter}
            """
            
            cursor.execute(query_rest, debt_params)  # Reuse debt_params (same structure without divisions)
            rest_row = cursor.fetchone()
            type01 = float(rest_row.Type01) if rest_row and rest_row.Type01 else 0
            type02 = float(rest_row.Type02) if rest_row and rest_row.Type02 else 0
            
            # 4. Итоговый долг (текущий)
            area_data['Debt'] = debt_from_docs - abs(type01) - abs(type02)
            
            # ЛОГИРОВАНИЕ для Area 105
            if area_code == '105':
                logger.info(f"[AREA 105] debt_from_docs: {debt_from_docs:,.2f}, type01: {type01:,.2f}, type02: {type02:,.2f}, final_debt: {area_data['Debt']:,.2f}")
            
            # 5. Получить данные за прошлый месяц (те же даты, но месяц назад)
            prev_month_sales_params = (area_code, prev_month_from_str, prev_month_to_str) + excluded_params + product_groups_params + division_params + sales_group_params
            cursor.execute(query_sales, prev_month_sales_params)
            prev_month_row = cursor.fetchone()
            
            area_data['PrevMonthSales'] = float(prev_month_row.TotalSales) if prev_month_row and prev_month_row.TotalSales else 0
            
            # 5a. Долг за прошлый месяц (с формулой Type01/Type02)
            # 5a. Долг за прошлый месяц (с учетом даты)
            query_prev_debt = f"""
                SELECT 
                    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
                FROM HICUSTOMERSDEBT d
                INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
                INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                WHERE csa.fSALESAREA = ?
                    AND d.fDATE <= ?
                    {excluded_filter}
                    {group_filter}
            """
            prev_debt_params = (area_code, prev_month_to_str) + excluded_params + group_params
            cursor.execute(query_prev_debt, prev_debt_params)
            prev_debt_row = cursor.fetchone()
            # Для истории игнорируем Type01/02, так как HIRESTCUSTOMERSSUM не имеет истории
            area_data['PrevMonthDebt'] = float(prev_debt_row.DebtFromDocs) if prev_debt_row and prev_debt_row.DebtFromDocs else 0
            
            # 6. Получить данные за прошлый год (те же даты, но год назад)
            last_year_sales_params = (area_code, last_year_from_str, last_year_to_str) + excluded_params + product_groups_params + division_params + sales_group_params
            cursor.execute(query_sales, last_year_sales_params)
            last_year_row = cursor.fetchone()
            
            area_data['LastYearSales'] = float(last_year_row.TotalSales) if last_year_row and last_year_row.TotalSales else 0
            
            # 6a. Долг за прошлый год (с формулой Type01/Type02)
            # 6a. Долг за прошлый год (с учетом даты)
            query_last_year_debt = f"""
                SELECT 
                    ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
                FROM HICUSTOMERSDEBT d
                INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
                INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                WHERE csa.fSALESAREA = ?
                    AND d.fDATE <= ?
                    {excluded_filter}
                    {group_filter}
            """
            last_year_debt_params = (area_code, last_year_to_str) + excluded_params + group_params
            cursor.execute(query_last_year_debt, last_year_debt_params)
            last_year_debt_row = cursor.fetchone()
            area_data['LastYearDebt'] = float(last_year_debt_row.DebtFromDocs) if last_year_debt_row and last_year_debt_row.DebtFromDocs else 0
        
        # Получить платежи по Sales Areas из таблицы HICUSTOMERSDEBT
        logger.info("[PAYMENTS] Calculating actual payments from HICUSTOMERSDEBT table...")
        for area_code, area_data in all_areas.items():
            # Получить фактические платежи из таблицы HICUSTOMERSDEBT (история движения долгов)
            # fOP = 'PAY' - платежные операции
            # fDBCR = 'C' - кредит (уменьшение долга, т.е. платеж от клиента)
            query_payments = f"""
                SELECT 
                    ISNULL(SUM(CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END), 0) as TotalPayments
                FROM HICUSTOMERSDEBT h
                INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
                INNER JOIN CUSTOMERS c ON d.fCUSTOMERID = c.fID
                WHERE d.fSALESAREA = ?
                    AND h.fDATE >= ?
                    AND h.fDATE <= ?
                    AND h.fOP = 'PAY'
                    {excluded_filter}
                    {group_filter}
            """
            
            payments_params = (area_code, date_from, date_to) + excluded_params + group_params
            cursor.execute(query_payments, payments_params)
            payments_row = cursor.fetchone()
            
            area_data['Payments'] = float(payments_row.TotalPayments) if payments_row and payments_row.TotalPayments else 0
            area_data['InitialDebt'] = area_data['Debt'] - area_data['TotalSales'] + area_data['Payments']
        
        # Получить историю по месяцам за последние 24 месяца (ОПТИМИЗИРОВАННЫЙ ЗАПРОС)
        logger.info("[HISTORY] Starting monthly history calculation...")
        current_date = datetime.strptime(date_to, '%Y-%m-%d')
        
        # Вычислить дату начала (24 месяца назад)
        start_history_date = current_date.replace(day=1)
        for _ in range(24):
            if start_history_date.month == 1:
                start_history_date = start_history_date.replace(year=start_history_date.year - 1, month=12)
            else:
                start_history_date = start_history_date.replace(month=start_history_date.month - 1)
        
        logger.info(f"[HISTORY] Date range: {start_history_date.strftime('%Y-%m-%d')} to {date_to}")
        
        # Divisions filter для истории продаж (только товарные группы)
        division_filter = ""
        division_params = tuple()
        if requested_divisions:
            placeholders = ','.join(['?'] * len(requested_divisions))
            division_filter = f"""
                AND s.fSALESAGENTID IN (
                    SELECT DISTINCT fSALESAGENTID 
                    FROM SALESAGENTDIVISIONS 
                    WHERE fDIVISION IN ({placeholders})
                )
            """
            division_params = tuple(requested_divisions)
        
        # Sales groups filter для истории продаж (группы клиентов)
        sales_group_filter = ""
        sales_group_params = tuple()
        if requested_sales_groups:
            placeholders = ','.join(['?'] * len(requested_sales_groups))
            sales_group_filter = f" AND c.fGROUP IN ({placeholders})"
            sales_group_params = tuple(requested_sales_groups)
        
        # ОДИН запрос для получения всех исторических данных продаж (divisions + sales_groups filter)
        history_query = f"""
            SELECT 
                csa.fSALESAREA AS AreaCode,
                FORMAT(s.fDATE, 'yyyy-MM') AS Month,
                COUNT(DISTINCT s.fCUSTOMERID) AS CustomerCount,
                COUNT(s.fISN) AS SalesCount,
                ISNULL(SUM(s.fTOTALSUM), 0) AS TotalSales,
                ISNULL(SUM(CASE WHEN s.fPAYTYPE = 2 THEN s.fTOTALSUM ELSE 0 END), 0) AS CreditSales
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE csa.fSALESAREA = s.fSALESAREA
                AND s.fDATE >= ?
                AND s.fDATE <= ?
                AND s.fSTATE = 2
                {excluded_filter}
                {product_groups_filter}
                {division_filter}
                {sales_group_filter}
            GROUP BY csa.fSALESAREA, FORMAT(s.fDATE, 'yyyy-MM')
            ORDER BY csa.fSALESAREA, FORMAT(s.fDATE, 'yyyy-MM')
        """
        
        history_params = (start_history_date.strftime('%Y-%m-%d'), date_to) + excluded_params + product_groups_params + division_params + sales_group_params
        logger.info(f"[SALES HISTORY] Query has {history_query.count('?')} placeholders")
        logger.info(f"[SALES HISTORY] Supplying {len(history_params)} params")
        cursor.execute(history_query, history_params)
        history_rows = cursor.fetchall()
        logger.info(f"[HISTORY] Got {len(history_rows)} history rows")
        
        # Сгруппировать результаты по территориям (month -> metrics)
        history_by_area = {}
        for row in history_rows:
            area_code = row.AreaCode
            area_history = history_by_area.setdefault(area_code, {})
            
            # Преобразовать строку месяца в datetime для форматирования
            try:
                month_date = datetime.strptime(row.Month, '%Y-%m')
            except ValueError:
                continue

            area_history[row.Month] = {
                'month': row.Month,
                'monthName': month_date.strftime('%b %Y'),
                'customerCount': row.CustomerCount or 0,
                'salesCount': row.SalesCount or 0,
                'totalSales': float(row.TotalSales) if row.TotalSales else 0,
                'creditSales': float(row.CreditSales) if row.CreditSales else 0,
                'totalPayments': 0,
                'totalDebt': 0
            }

        # Получить историю оплат по месяцам из таблицы PAYMENTS
        payments_group_filter = ""
        payments_group_params = tuple()
        if requested_groups:
            placeholders = ','.join(['?'] * len(requested_groups))
            payments_group_filter = f" AND c.fGROUP IN ({placeholders})"
            payments_group_params = tuple(requested_groups)

        payments_history_query = f"""
            SELECT 
                d.fSALESAREA AS AreaCode,
                FORMAT(h.fDATE, 'yyyy-MM') AS Month,
                ISNULL(SUM(CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END), 0) AS TotalPayments
            FROM HICUSTOMERSDEBT h
            INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
            INNER JOIN CUSTOMERS c ON d.fCUSTOMERID = c.fID
            WHERE h.fDATE >= ?
                AND h.fDATE <= ?
                AND h.fOP = 'PAY'
                {excluded_filter}
                {payments_group_filter}
            GROUP BY d.fSALESAREA, FORMAT(h.fDATE, 'yyyy-MM')
            ORDER BY d.fSALESAREA, FORMAT(h.fDATE, 'yyyy-MM')
        """

        payments_history_params = (start_history_date.strftime('%Y-%m-%d'), date_to) + excluded_params + payments_group_params
        cursor.execute(payments_history_query, payments_history_params)
        payments_history_rows = cursor.fetchall()

        for row in payments_history_rows:
            area_code = row.AreaCode
            area_history = history_by_area.setdefault(area_code, {})

            month_key = row.Month
            try:
                month_date = datetime.strptime(month_key, '%Y-%m')
            except ValueError:
                continue

            if month_key not in area_history:
                area_history[month_key] = {
                    'month': month_key,
                    'monthName': month_date.strftime('%b %Y'),
                    'customerCount': 0,
                    'salesCount': 0,
                    'totalSales': 0,
                    'totalPayments': 0,
                    'totalDebt': 0
                }

            area_history[month_key]['totalPayments'] = float(row.TotalPayments) if row.TotalPayments else 0
        
        # Получить историю долга по месяцам (используем groups filter, БЕЗ divisions)
        debt_group_filter = ""
        debt_group_params = tuple()
        if requested_groups:
            placeholders = ','.join(['?'] * len(requested_groups))
            debt_group_filter = f" AND c.fGROUP IN ({placeholders})"
            debt_group_params = tuple(requested_groups)

        # История долга: получаем начальный баланс и изменения по месяцам
        # Сначала получим начальный баланс на начало периода истории
        initial_debt_query = f"""
            SELECT 
                csa.fSALESAREA AS AreaCode,
                ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS InitialDebt
            FROM HICUSTOMERSDEBT d WITH (NOLOCK)
            INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON d.fDEBTDOCISN = doc.fISN
            INNER JOIN CUSTOMERS c WITH (NOLOCK) ON doc.fCUSTOMERID = c.fID
            INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON c.fID = csa.fCUSTOMERID
            WHERE d.fDATE < ?
                {excluded_filter}
                {debt_group_filter}
            GROUP BY csa.fSALESAREA
        """
        
        initial_debt_params = (start_history_date.strftime('%Y-%m-%d'),) + excluded_params + debt_group_params
        cursor.execute(initial_debt_query, initial_debt_params)
        initial_debt_rows = cursor.fetchall()
        
        # Словарь начальных балансов по территориям
        initial_debts = {row.AreaCode: float(row.InitialDebt) if row.InitialDebt else 0 
                         for row in initial_debt_rows}
        
        # Теперь получим изменения долга помесячно
        debt_history_query = f"""
            SELECT 
                csa.fSALESAREA AS AreaCode,
                FORMAT(d.fDATE, 'yyyy-MM') AS Month,
                ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS DebtChange
            FROM HICUSTOMERSDEBT d WITH (NOLOCK)
            INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON d.fDEBTDOCISN = doc.fISN
            INNER JOIN CUSTOMERS c WITH (NOLOCK) ON doc.fCUSTOMERID = c.fID
            INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON c.fID = csa.fCUSTOMERID
            WHERE d.fDATE >= ? AND d.fDATE <= ?
                {excluded_filter}
                {debt_group_filter}
            GROUP BY csa.fSALESAREA, FORMAT(d.fDATE, 'yyyy-MM')
            ORDER BY csa.fSALESAREA, FORMAT(d.fDATE, 'yyyy-MM')
        """

        debt_history_params = (start_history_date.strftime('%Y-%m-%d'), date_to) + excluded_params + debt_group_params
        logger.info(f"[DEBT HISTORY] Query has {debt_history_query.count('?')} placeholders")
        logger.info(f"[DEBT HISTORY] Supplying {len(debt_history_params)} params")
        cursor.execute(debt_history_query, debt_history_params)
        debt_history_rows = cursor.fetchall()

        for row in debt_history_rows:
            area_code = row.AreaCode
            area_history = history_by_area.setdefault(area_code, {})

            month_key = row.Month
            try:
                month_date = datetime.strptime(month_key, '%Y-%m')
            except ValueError:
                continue

            if month_key not in area_history:
                area_history[month_key] = {
                    'month': month_key,
                    'monthName': month_date.strftime('%b %Y'),
                    'customerCount': 0,
                    'salesCount': 0,
                    'totalSales': 0,
                    'totalPayments': 0,
                    'totalDebt': 0,
                    'debtChange': 0  # Изменение долга за месяц
                }

            # Сохраняем изменение долга (не кумулятивный баланс)
            area_history[month_key]['debtChange'] = float(row.DebtChange) if row.DebtChange else 0
        
        # Рассчитать кумулятивный баланс долга для каждой территории
        logger.info(f"[HISTORY] Calculating cumulative debt balances...")
        for area_code, area_history in history_by_area.items():
            # Начальный баланс для этой территории
            cumulative_debt = initial_debts.get(area_code, 0)
            
            # Сортируем месяцы и пересчитываем баланс
            for month_key in sorted(area_history.keys()):
                debt_change = area_history[month_key].get('debtChange', 0)
                cumulative_debt += debt_change
                area_history[month_key]['totalDebt'] = cumulative_debt
        
        # Добавить историю к каждой территории
        logger.info(f"[HISTORY] Assigning history to {len(all_areas)} areas...")
        for area_code, area_data in all_areas.items():
            area_history = history_by_area.get(area_code, {})
            if isinstance(area_history, dict):
                sorted_history = [area_history[key] for key in sorted(area_history.keys())]
            else:
                sorted_history = area_history
            area_data['MonthlyHistory'] = sorted_history
        logger.info("[HISTORY] History assignment complete")
        
        conn.close()
        
        # Конвертировать в список и отфильтровать области без продаж и долгов
        areas_list = [
            area for area in all_areas.values()
            if area['TotalSales'] > 0 or area['Debt'] != 0
        ]
        
        # Сортировать по продажам
        areas_list.sort(key=lambda x: x['TotalSales'], reverse=True)
        
        return jsonify({'success': True, 'data': areas_list})
        
    except Exception as e:
        logger.error(f"Ошибка получения Sales Areas: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/customers')
def customers_api():
    """Получить клиентов с продажами и долгами, отфильтрованных по Sales Area"""
    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        sales_area = request.args.get('sales_area', '101').strip() or '101'
        raw_divisions = request.args.get('divisions', '').strip()
        selected_divisions = [div.strip() for div in raw_divisions.split(',') if div.strip()]
        raw_groups = request.args.get('groups', '').strip()
        selected_groups = [grp.strip() for grp in raw_groups.split(',') if grp.strip()]
        include_zero_sales = request.args.get('include_zero_sales', '0') == '1'
        
        app.logger.info(f"[API /customers] sales_area={sales_area}, date_from={date_from}, date_to={date_to}")
        app.logger.info(f"[API /customers] selected_divisions={selected_divisions}, selected_groups={selected_groups}")
        app.logger.info(f"[API /customers] include_zero_sales={include_zero_sales}")

        if not date_from or not date_to:
            today = datetime.now()
            date_from = today.replace(day=1).strftime('%Y-%m-%d')
            last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            date_to = last_day.strftime('%Y-%m-%d')

        conn = db.get_connection()
        cursor = conn.cursor()

        excluded_filter, excluded_params = get_excluded_filter_sql()
        product_groups_filter, product_groups_params = get_product_groups_filter_sql()
        division_clause = ""
        division_params = ()
        if selected_divisions:
            placeholders = ','.join('?' * len(selected_divisions))
            division_clause = f" AND c.fDIVISION IN ({placeholders})"
            division_params = tuple(selected_divisions)
        group_clause = ""
        group_params = ()
        if selected_groups:
            placeholders = ','.join('?' * len(selected_groups))
            group_clause = f" AND c.fGROUP IN ({placeholders})"
            group_params = tuple(selected_groups)

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
            
            # Дополнительная фильтрация по выбранным дивизионам
            if selected_divisions:
                placeholders = ','.join('?' * len(selected_divisions))
                base_customer_clause += f" AND c.fDIVISION IN ({placeholders})"
                customer_params_base.extend(selected_divisions)
            
            app.logger.info(f"[include_zero_sales] Using CUSTOMERSALESAREAS for sales_area={sales_area}")
            app.logger.info(f"[include_zero_sales] selected_groups={selected_groups}, selected_divisions={selected_divisions}")
            app.logger.info(f"[include_zero_sales] base_customer_clause={base_customer_clause}")
            app.logger.info(f"[include_zero_sales] customer_params_base={customer_params_base}")
            
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
            # Parameters: sales_area (for CUSTOMERSALESAREAS), customer_params_base (groups/divisions), dates, sales_area (for FilteredSales), excluded, product_groups, dates (for PaymentData)
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
                    WHERE s.fSTATE = 2
                        AND s.fDATE >= ?
                        AND s.fDATE <= ?
                        AND s.fSALESAREA = ?
                        {excluded_filter}
                        {division_clause}
                        {group_clause}
                        {product_groups_filter}
                    GROUP BY c.fID, c.fCODE, c.fNAME, c.fGROUP, c.fADDRESS, sa.fCODE, sa.fNAME
                ),
                Totals AS (
                    SELECT 
                        CustomerId,
                        MAX(CustomerCode) AS CustomerCode,
                        MAX(CustomerName) AS CustomerName,
                        MAX(GroupCode) AS GroupCode,
                        MAX(CustomerAddress) AS CustomerAddress,
                        SUM(SalesCount) AS SalesCount,
                        SUM(TotalSales) AS TotalSales
                    FROM FilteredSales
                    GROUP BY CustomerId
                ),
                Managers AS (
                    SELECT 
                        CustomerId,
                        ManagerCode,
                        ManagerName,
                        TotalSales,
                        ROW_NUMBER() OVER (PARTITION BY CustomerId ORDER BY TotalSales DESC) AS rn
                    FROM FilteredSales
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
                    debt_data.DebtFromDocs,
                    rest_data.Type01,
                    rest_data.Type02,
                    (debt_data.DebtFromDocs - ABS(rest_data.Type01) - ABS(rest_data.Type02)) AS Debt,
                    payment_data.TotalPayments,
                    ((debt_data.DebtFromDocs - ABS(rest_data.Type01) - ABS(rest_data.Type02)) - t.TotalSales + payment_data.TotalPayments) AS InitialDebt,
                    last_payment_data.LastPaymentDate,
                    last_payment_data.DaysSinceLastPayment,
                    last_sale_data.LastSaleDate,
                    last_sale_data.DaysSinceLastSale
                FROM Totals t
                LEFT JOIN Managers m ON t.CustomerId = m.CustomerId AND m.rn = 1
                OUTER APPLY (
                    SELECT 
                        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) AS DebtFromDocs
                    FROM HICUSTOMERSDEBT d
                    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                    WHERE doc.fCUSTOMERID = t.CustomerId
                ) AS debt_data
                OUTER APPLY (
                    SELECT 
                        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) AS Type01,
                        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) AS Type02
                    FROM HIRESTCUSTOMERSSUM r
                    WHERE r.fCUSTOMERID = t.CustomerId
                ) AS rest_data
                OUTER APPLY (
                    SELECT 
                        ISNULL(SUM(CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END), 0) AS TotalPayments
                    FROM HICUSTOMERSDEBT h
                    INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
                    WHERE d.fCUSTOMERID = t.CustomerId
                        AND h.fOP = 'PAY'
                        AND h.fDATE >= ?
                        AND h.fDATE <= ?
                ) AS payment_data
                OUTER APPLY (
                    SELECT 
                        MAX(h.fDATE) AS LastPaymentDate,
                        DATEDIFF(DAY, MAX(h.fDATE), GETDATE()) AS DaysSinceLastPayment
                    FROM HICUSTOMERSDEBT h
                    INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
                    WHERE d.fCUSTOMERID = t.CustomerId
                        AND h.fOP = 'PAY'
                        AND h.fDBCR = 'C'
                ) AS last_payment_data
                OUTER APPLY (
                    SELECT 
                        MAX(s.fDATE) AS LastSaleDate,
                        DATEDIFF(DAY, MAX(s.fDATE), GETDATE()) AS DaysSinceLastSale
                    FROM SALES s
                    WHERE s.fCUSTOMERID = t.CustomerId
                        AND s.fSTATE = 2
                ) AS last_sale_data
                ORDER BY t.TotalSales DESC
            """
            params = (date_from, date_to, sales_area) + excluded_params + division_params + group_params + product_groups_params + (date_from, date_to)
        
        app.logger.info(f"[Query params] Total params count: {len(params)}")
        app.logger.info(f"[Query params] params={params[:10]}..." if len(params) > 10 else f"[Query params] params={params}")

        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        app.logger.info(f"[Query result] Found {len(rows)} customers")
        
        conn.close()

        customers = []
        total_sales = 0.0
        total_debt = 0.0
        total_payments = 0.0
        total_initial_debt = 0.0

        # Log first 5 rows with debt info for debugging
        if include_zero_sales and len(rows) > 0:
            app.logger.info(f"[Debt Check] First 5 customers with debt:")
            for i, row in enumerate(rows[:5]):
                debt_val = float(row.Debt) if row.Debt else 0.0
                app.logger.info(f"  Customer {row.CustomerCode} ({row.CustomerName}): Debt={debt_val:.2f}")

        for row in rows:
            debt_value = float(row.Debt) if row.Debt else 0.0
            initial_debt_value = float(row.InitialDebt) if row.InitialDebt else 0.0
            sales_value = float(row.TotalSales) if row.TotalSales else 0.0
            payments_value = float(row.TotalPayments) if row.TotalPayments else 0.0
            days_since_payment = row.DaysSinceLastPayment if row.DaysSinceLastPayment else None
            last_payment_date = row.LastPaymentDate.strftime('%Y-%m-%d') if row.LastPaymentDate else None
            
            # Рассчитать процент долга от продаж
            debt_percent = (debt_value / sales_value * 100) if sales_value > 0 else 0
            last_sale_date = row.LastSaleDate.strftime('%Y-%m-%d') if row.LastSaleDate else None
            days_since_sale = row.DaysSinceLastSale if row.DaysSinceLastSale else None
            
            total_sales += sales_value
            total_debt += debt_value
            total_payments += payments_value
            total_initial_debt += initial_debt_value
            
            customer_address = row.CustomerAddress if hasattr(row, 'CustomerAddress') and row.CustomerAddress else ''
            
            customers.append({
                'CustomerId': row.CustomerId,
                'CustomerCode': row.CustomerCode,
                'CustomerName': row.CustomerName,
                'CustomerAddress': customer_address,
                'GroupCode': row.GroupCode,
                'ManagerCode': row.ManagerCode,
                'ManagerName': row.ManagerName,
                'SalesCount': row.SalesCount,
                'TotalSales': sales_value,
                'TotalPayments': payments_value,
                'Debt': debt_value,
                'InitialDebt': initial_debt_value,
                'DebtPercent': round(debt_percent, 1),
                'LastPaymentDate': last_payment_date,
                'DaysSinceLastPayment': days_since_payment,
                'LastSaleDate': last_sale_date,
                'DaysSinceLastSale': days_since_sale
            })

        return jsonify({
            'success': True,
            'data': customers,
            'summary': {
                'count': len(customers),
                'total_sales': total_sales,
                'total_debt': total_debt,
                'total_payments': total_payments,
                'total_initial_debt': total_initial_debt,
                'sales_area': sales_area,
                'period': {
                    'from': date_from,
                    'to': date_to
                }
            }
        })

    except Exception as e:
        logger.error(f"Ошибка получения клиентов: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def get_payment_type_name(code: str) -> str:
    """Преобразование кода типа оплаты в читаемое название"""
    payment_types = {
        '1': 'Կանխիկ',  # Cash
        '2': 'Բանկ',     # Bank transfer
        '3': 'Կրեդիտ',  # Credit/Debt
        '5': 'Այլ',      # Other
        '6': 'Խառը'      # Mixed
    }
    return payment_types.get(code, code if code else 'N/A')


@app.route('/api/customers/<int:customer_id>/purchases')
def customer_purchases(customer_id: int):
    """Получить покупки конкретного клиента с фильтрами по датам и типам оплаты"""
    try:
        logger.info(f"=== Запрос покупок для клиента ID={customer_id} ===")
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        raw_payments = request.args.get('payments', '').strip()
        selected_payments = [p.strip() for p in raw_payments.split(',') if p.strip()]

        if not date_from or not date_to:
            today = datetime.now()
            first_day = today.replace(day=1)
            last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            date_from = first_day.strftime('%Y-%m-%d')
            date_to = last_day.strftime('%Y-%m-%d')

        conn = db.get_connection()
        cursor = conn.cursor()

        payment_clause = ""
        payment_params = ()
        if selected_payments:
            placeholders = ','.join('?' * len(selected_payments))
            payment_clause = f" AND ISNULL(s.fPAYTYPE, '') IN ({placeholders})"
            payment_params = tuple(selected_payments)

        query = f"""
            SELECT 
                s.fISN AS SaleId,
                s.fISN AS DocNumber,
                s.fDATE AS SaleDate,
                s.fTOTALSUM AS TotalSum,
                s.fPAYTYPE AS PaymentType,
                s.fSALESAREA AS SalesArea,
                sa.fCODE AS ManagerCode,
                sa.fNAME AS ManagerName
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            LEFT JOIN SALESAGENTS sa ON s.fSALESAGENTID = sa.fID
            WHERE s.fCUSTOMERID = ?
                AND s.fSTATE = 2
                AND s.fDATE >= ?
                AND s.fDATE <= ?
            ORDER BY s.fDATE DESC, s.fISN DESC
        """

        params = (
            customer_id,
            date_from,
            date_to
        ) + payment_params

        logger.info(f"Выполнение запроса с параметрами: customer_id={customer_id}, date_from={date_from}, date_to={date_to}")
        cursor.execute(query, params)
        rows = cursor.fetchall()
        logger.info(f"Найдено продаж: {len(rows)}")

        purchases = []
        total_sales = 0.0
        payment_types_set = set()

        for row in rows:
            sale_sum = float(row.TotalSum) if row.TotalSum else 0.0
            total_sales += sale_sum
            payment_type_code = row.PaymentType.strip() if row.PaymentType else ''
            payment_type_name = get_payment_type_name(payment_type_code)
            if payment_type_code:
                payment_types_set.add(payment_type_code)
            sale_date_str = row.SaleDate.strftime('%Y-%m-%d') if row.SaleDate else None
            
            # Получить товары для этой продажи
            cursor.execute("""
                SELECT 
                    sd.fROWNUM AS [LineNo],
                    p.fCODE AS ProductCode,
                    p.fNAME AS ProductName,
                    sd.fQUANTITY AS Quantity,
                    sd.fPRICE AS OriginalPrice,
                    sd.fDISCOUNT AS DiscountAmount,
                    sd.fDISCOUNTEDPRICE AS Price,
                    sd.fSUM AS LineTotal
                FROM SALEDOCDETAILS sd
                LEFT JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
                WHERE sd.fISN = ?
                ORDER BY sd.fROWNUM
            """, (row.SaleId,))
            
            products = []
            for product_row in cursor.fetchall():
                original_price = float(product_row.OriginalPrice) if product_row.OriginalPrice else 0
                discount_amount = float(product_row.DiscountAmount) if product_row.DiscountAmount else 0
                price = float(product_row.Price) if product_row.Price else 0
                
                # fDISCOUNT в базе хранит процент скидки, а не сумму
                discount_percent = discount_amount
                
                products.append({
                    'LineNo': product_row.LineNo,
                    'ProductCode': product_row.ProductCode or '',
                    'ProductName': product_row.ProductName or 'N/A',
                    'Quantity': float(product_row.Quantity) if product_row.Quantity else 0,
                    'OriginalPrice': original_price,
                    'Price': price,
                    'DiscountPercent': round(discount_percent, 2),
                    'LineTotal': float(product_row.LineTotal) if product_row.LineTotal else 0
                })
            
            purchases.append({
                'SaleId': row.SaleId,
                'DocNumber': row.DocNumber,
                'SaleDate': sale_date_str,
                'TotalSum': sale_sum,
                'PaymentType': payment_type_name,
                'SalesArea': row.SalesArea,
                'ManagerCode': row.ManagerCode,
                'ManagerName': row.ManagerName,
                'Products': products
            })
        
        # Получить платежи клиента из таблицы PAYMENTS
        cursor.execute("""
            SELECT 
                h.fBASE AS PaymentId,
                h.fDATE AS PaymentDate,
                '' AS DocNumber,
                '' AS PaymentType,
                CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END AS Amount,
                'Платеж из истории долга' AS Comment,
                sa.fCODE AS ManagerCode,
                sa.fNAME AS ManagerName,
                d.fSALESAREA AS SalesArea
            FROM HICUSTOMERSDEBT h
            INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
            LEFT JOIN SALESAGENTS sa ON d.fSALESAGENTID = sa.fID
            WHERE d.fCUSTOMERID = ?
                AND h.fOP = 'PAY'
                AND h.fDBCR = 'C'
                AND h.fDATE >= ?
                AND h.fDATE <= ?
            ORDER BY h.fDATE DESC, h.fBASE DESC
        """, (customer_id, date_from, date_to))
        
        payments = []
        total_payments = 0.0
        for row in cursor.fetchall():
            payment_amount = float(row.Amount) if row.Amount else 0.0
            total_payments += payment_amount
            payment_date_str = row.PaymentDate.strftime('%Y-%m-%d') if row.PaymentDate else None
            
            payments.append({
                'PaymentId': row.PaymentId,
                'PaymentDate': payment_date_str,
                'DocNumber': row.DocNumber or '',
                'PaymentType': 'Платеж',
                'Amount': payment_amount,
                'Comment': row.Comment or '',
                'ManagerCode': row.ManagerCode or '',
                'ManagerName': row.ManagerName or '',
                'SalesArea': row.SalesArea or ''
            })
        
        conn.close()

        return jsonify({
            'success': True,
            'data': purchases,
            'payments': payments,
            'summary': {
                'count': len(purchases),
                'total_sales': total_sales,
                'payment_count': len(payments),
                'total_payments': total_payments,
                'period': {
                    'from': date_from,
                    'to': date_to
                }
            },
            'payment_types': sorted(payment_types_set)
        })

    except Exception as e:
        logger.error(f"Ошибка получения покупок клиента {customer_id}: {e}")
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
        
        # Статистика продаж за последние 36 месяцев (3 года)
        query_sales = """
            SELECT 
                FORMAT(fDATE, 'yyyy-MM') as Month,
                COUNT(*) as SalesCount,
                SUM(fTOTALSUM) as TotalSum
            FROM SALES
            WHERE fSALESAGENTID = ?
            AND fDATE >= DATEADD(MONTH, -36, GETDATE())
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

@app.route('/api/product-groups')
def get_product_groups_list():
    """Получить список дивизионов для фильтра дистрибьюторов"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT DISTINCT
                fCODE,
                fCAPTION
            FROM TREES
            WHERE fTREEID = 'Division'
            AND fCLOSED = 0
            ORDER BY fCODE
        """
        cursor.execute(query)
        
        divisions = []
        for row in cursor.fetchall():
            divisions.append({
                'code': row[0],
                'name': row[1]
            })
        
        conn.close()
        return jsonify({'success': True, 'data': divisions})
    except Exception as e:
        logger.error(f"Ошибка загрузки дивизионов: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/customer-groups')
def get_customer_groups_list():
    """Получить список групп клиентов для фильтра дистрибьюторов"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT DISTINCT
                c.fGROUP,
                ISNULL(t.fCAPTION, c.fGROUP) as GroupName
            FROM CUSTOMERS c
            LEFT JOIN TREES t ON t.fCODE = c.fGROUP AND t.fTREEID = 'CustGrp'
            WHERE c.fGROUP IS NOT NULL
            AND c.fGROUP != ''
            ORDER BY c.fGROUP
        """
        cursor.execute(query)
        
        groups = []
        for row in cursor.fetchall():
            groups.append({
                'code': row[0],
                'name': row[1]
            })
        
        conn.close()
        return jsonify({'success': True, 'data': groups})
    except Exception as e:
        logger.error(f"Ошибка загрузки групп клиентов: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/customer-groups-hierarchy')
def get_customer_groups_hierarchy():
    """Получить иерархический список групп клиентов (группы и подгруппы)"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получаем все группы из TREES с их родителями
        query = """
            SELECT 
                t.fCODE,
                t.fCAPTION,
                t.fPARENT
            FROM TREES t
            WHERE t.fTREEID = 'CustGrp'
            ORDER BY t.fPARENT, t.fCODE
        """
        cursor.execute(query)
        
        all_groups = []
        for row in cursor.fetchall():
            all_groups.append({
                'code': row[0],
                'name': row[1] or row[0],
                'parent': row[2]
            })
        
        # Строим иерархию
        # Сначала находим корневые группы (без родителя или родитель пустой)
        root_groups = [g for g in all_groups if not g['parent'] or g['parent'] == '']
        
        # Функция для получения детей группы
        def get_children(parent_code):
            return [g for g in all_groups if g['parent'] == parent_code]
        
        # Строим иерархическую структуру
        hierarchy = []
        for root in root_groups:
            children = get_children(root['code'])
            hierarchy.append({
                'code': root['code'],
                'name': root['name'],
                'parent': None,
                'children': [{
                    'code': child['code'],
                    'name': child['name'],
                    'parent': root['code']
                } for child in children]
            })
        
        # Также добавляем группы которые используются в CUSTOMERS но могут не быть в иерархии
        cursor.execute("""
            SELECT DISTINCT c.fGROUP
            FROM CUSTOMERS c
            WHERE c.fGROUP IS NOT NULL AND c.fGROUP != ''
        """)
        used_groups = set(row[0] for row in cursor.fetchall())
        all_codes = set(g['code'] for g in all_groups)
        
        conn.close()
        
        return jsonify({
            'success': True, 
            'data': {
                'hierarchy': hierarchy,
                'flat': all_groups,
                'used': list(used_groups)
            }
        })
    except Exception as e:
        logger.error(f"Ошибка загрузки иерархии групп: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/distributors')
def get_distributors():
    """
    Получить расширенную аналитику по клиентам-дистрибьюторам
    
    ФОРМУЛЫ РАСЧЁТА (такие же как на странице Areas):
    1. Продажи (TotalSales) - из таблицы SALES где fSTATE=2
    2. Платежи (Payments) - из HICUSTOMERSDEBT где fOP='PAY' и fDBCR='C'
    3. Долг (Debt) = ДолгИзДокументов - |Type01| - |Type02|
       - ДолгИзДокументов: HICUSTOMERSDEBT (D - C по fDEBTDOCISN)
       - Type01, Type02: HIRESTCUSTOMERSSUM (предоплаты)
    """
    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        divisions = request.args.get('divisions', '')
        groups = request.args.get('groups', '')
        
        if not date_from or not date_to:
            today = datetime.now()
            date_from = today.replace(day=1).strftime('%Y-%m-%d')
            date_to = today.strftime('%Y-%m-%d')
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Строим условия фильтрации для групп клиентов
        group_filter = ""
        group_params = []
        if groups:
            group_list = groups.split(',')
            placeholders = ','.join(['?'] * len(group_list))
            group_filter = f"AND c.fGROUP IN ({placeholders})"
            group_params = group_list
        
        # Строим условия фильтрации для дивизионов (по продажам)
        division_filter = ""
        division_params = []
        if divisions:
            division_list = divisions.split(',')
            placeholders = ','.join(['?'] * len(division_list))
            division_filter = f"AND s.fDIVISION IN ({placeholders})"
            division_params = division_list
        
        # ============================================================
        # 1. ПРОДАЖИ - из таблицы SALES где fSTATE=2
        # ============================================================
        sales_query = f"""
            SELECT 
                c.fID as CustomerID,
                c.fCODE as CustomerCode,
                c.fNAME as CustomerName,
                c.fGROUP as GroupCode,
                ISNULL(SUM(s.fTOTALSUM), 0) as TotalSales,
                COUNT(s.fISN) as SalesCount
            FROM CUSTOMERS c
            LEFT JOIN SALES s ON c.fID = s.fCUSTOMERID
                AND s.fDATE >= ?
                AND s.fDATE <= ?
                AND s.fSTATE = 2
                {division_filter}
            WHERE 1=1 {group_filter}
            GROUP BY c.fID, c.fCODE, c.fNAME, c.fGROUP
            HAVING ISNULL(SUM(s.fTOTALSUM), 0) > 0
        """
        
        sales_params = [date_from, date_to] + division_params + group_params
        cursor.execute(sales_query, sales_params)
        
        customers_data = {}
        for row in cursor.fetchall():
            customer_id = row[0]
            customers_data[customer_id] = {
                'CustomerID': customer_id,
                'CustomerCode': row[1],
                'CustomerName': row[2],
                'GroupCode': row[3] or '',
                'TotalSales': float(row[4] or 0),
                'SalesCount': row[5] or 0,
                'TotalPayments': 0,
                'DebtFromDocs': 0,
                'Type01': 0,
                'Type02': 0,
                'TotalDebt': 0
            }
        
        if not customers_data:
            conn.close()
            return jsonify({
                'success': True,
                'data': [],
                'count': 0,
                'period': {'from': date_from, 'to': date_to},
                'filters': {'divisions': divisions, 'groups': groups}
            })
        
        customer_ids = list(customers_data.keys())
        placeholders = ','.join(['?'] * len(customer_ids))
        
        # ============================================================
        # 2. ПЛАТЕЖИ - из HICUSTOMERSDEBT где fOP='PAY' и fDBCR='C'
        # ============================================================
        payments_query = f"""
            SELECT 
                d.fCUSTOMERID,
                ISNULL(SUM(ABS(h.fSUM)), 0) as TotalPayments
            FROM HICUSTOMERSDEBT h
            INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
            WHERE d.fCUSTOMERID IN ({placeholders})
                AND h.fDATE >= ?
                AND h.fDATE <= ?
                AND h.fOP = 'PAY'
                AND h.fDBCR = 'C'
            GROUP BY d.fCUSTOMERID
        """
        
        payments_params = customer_ids + [date_from, date_to]
        cursor.execute(payments_query, payments_params)
        
        for row in cursor.fetchall():
            customer_id = row[0]
            if customer_id in customers_data:
                customers_data[customer_id]['TotalPayments'] = float(row[1] or 0)
        
        # ============================================================
        # 3. ДОЛГ ИЗ ДОКУМЕНТОВ (DebtFromDocs) - на конец периода
        #    D (дебет) = увеличение долга
        #    C (кредит) = уменьшение долга (оплата)
        #    DebtFromDocs = SUM(D) - SUM(C)
        # ============================================================
        debt_query = f"""
            SELECT 
                d.fCUSTOMERID,
                ISNULL(SUM(CASE WHEN h.fDBCR = 'D' THEN ABS(h.fSUM) ELSE 0 END), 0) -
                ISNULL(SUM(CASE WHEN h.fDBCR = 'C' THEN ABS(h.fSUM) ELSE 0 END), 0) as DebtFromDocs
            FROM HICUSTOMERSDEBT h
            INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
            WHERE d.fCUSTOMERID IN ({placeholders})
                AND h.fDATE <= ?
            GROUP BY d.fCUSTOMERID
        """
        
        debt_params = customer_ids + [date_to]
        cursor.execute(debt_query, debt_params)
        
        for row in cursor.fetchall():
            customer_id = row[0]
            if customer_id in customers_data:
                customers_data[customer_id]['DebtFromDocs'] = float(row[1] or 0)
        
        # ============================================================
        # 4. ПРЕДОПЛАТЫ (Type01, Type02) - из HIRESTCUSTOMERSSUM
        #    Вычитаются из долга по модулю
        #    Примечание: таблица не имеет колонку даты - берём все записи
        # ============================================================
        rest_query = f"""
            SELECT 
                fCUSTOMERID,
                fTYPE,
                ISNULL(SUM(fSUM), 0) as RestSum
            FROM HIRESTCUSTOMERSSUM
            WHERE fCUSTOMERID IN ({placeholders})
                AND fTYPE IN ('01', '02')
            GROUP BY fCUSTOMERID, fTYPE
        """
        
        rest_params = customer_ids
        cursor.execute(rest_query, rest_params)
        
        for row in cursor.fetchall():
            customer_id = row[0]
            rest_type = row[1]
            rest_sum = float(row[2] or 0)
            
            if customer_id in customers_data:
                if rest_type == '01':
                    customers_data[customer_id]['Type01'] = rest_sum
                elif rest_type == '02':
                    customers_data[customer_id]['Type02'] = rest_sum
        
        conn.close()
        
        # ============================================================
        # 5. ИТОГОВЫЙ РАСЧЁТ ДОЛГА
        #    Долг = ДолгИзДокументов - |Type01| - |Type02|
        # ============================================================
        distributors = []
        for customer_id, data in customers_data.items():
            debt_from_docs = data['DebtFromDocs']
            type01 = abs(data['Type01'])
            type02 = abs(data['Type02'])
            
            # Формула долга (как на странице Areas)
            total_debt = debt_from_docs - type01 - type02
            data['TotalDebt'] = total_debt
            
            # Процент оплаты
            if data['TotalSales'] > 0:
                data['PaymentRate'] = (data['TotalPayments'] / data['TotalSales']) * 100
            else:
                data['PaymentRate'] = 0
            
            distributors.append(data)
        
        # Сортировка по продажам (убывание)
        distributors.sort(key=lambda x: x['TotalSales'], reverse=True)
        
        return jsonify({
            'success': True,
            'data': distributors,
            'count': len(distributors),
            'period': {
                'from': date_from,
                'to': date_to
            },
            'filters': {
                'divisions': divisions,
                'groups': groups
            }
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения дистрибьюторов: {e}")
        import traceback
        logger.error(traceback.format_exc())
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
                    ISNULL(SUM(fTOTALSUM), 0) as TotalSales
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
                    'TotalSum': float(data[0]['TotalSales'])
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


# =============================================
# KPI ПО МЕНЕДЖЕРАМ (страница + API)
# =============================================
# Отраслевые KPI van-sales/FMCG. Направление (higher_better) и вес в итоговом балле.
MANAGER_KPI_DEFS = [
    ("revenue",         "Հասույթ",             "currency", True,  20),
    ("activeCustomers", "Հաճախորդներ",         "number",   True,   9),
    ("routeVisit",      "Երթուղու շրջայց",     "percent",  True,   0),  # справочно (в балл не входит)
    ("routeOrder",      "Պատվեր ըստ երթուղու", "percent",  True,  10),
    ("strikeRate",      "Strike rate",         "percent",  True,   8),
    ("avgCheck",        "Միջին չեկ",           "currency", True,  10),
    ("linesPerInvoice", "Տողեր/ապր.",          "number",   True,   8),
    ("newCustomers",    "Նոր հաճախորդներ",     "number",   True,   8),
    ("collectRate",     "Պարտքի հավաքագրում",  "percent",  True,  10),
    ("returnsRate",     "Վերադարձեր",          "percent",  False,  7),
    ("planFact",        "Պլան/փաստ",           "percent",  True,  10),
]


def _months_between(d_from, d_to):
    a = datetime.strptime(d_from, "%Y-%m-%d")
    b = datetime.strptime(d_to, "%Y-%m-%d")
    return max(1, (b.year - a.year) * 12 + (b.month - a.month) + 1)


# --- Персистентные фильтры групп для страницы KPI (раздельно продажи/долг) ---
KPI_SALES_CLIENT_GROUPS_FILE = 'kpi_sales_client_groups.json'
KPI_DEBT_CLIENT_GROUPS_FILE  = 'kpi_debt_client_groups.json'
KPI_SALES_DIVISIONS_FILE     = 'kpi_sales_divisions.json'
KPI_DEBT_DIVISIONS_FILE      = 'kpi_debt_divisions.json'
KPI_TERRITORIES_FILE         = 'kpi_territories.json'      # территории (fSALESAREA) — весь анализ
KPI_PRODUCT_GROUPS_FILE      = 'kpi_product_groups.json'   # товарные группы (PrdctGrp) — весь анализ


def _kpi_load_list(path):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Ошибка чтения {path}: {e}")
    return []


def _kpi_save_list(path, items):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(items or [], f, ensure_ascii=False, indent=2)
    return True


@app.route('/api/managers/kpi/filters', methods=['GET'])
def get_managers_kpi_filters():
    """Сохранённые фильтры групп (товары/клиенты) отдельно для продаж и долга."""
    return jsonify({
        'success': True,
        'sales_client_groups': _kpi_load_list(KPI_SALES_CLIENT_GROUPS_FILE),
        'debt_client_groups':  _kpi_load_list(KPI_DEBT_CLIENT_GROUPS_FILE),
        'sales_divisions':     _kpi_load_list(KPI_SALES_DIVISIONS_FILE),
        'debt_divisions':      _kpi_load_list(KPI_DEBT_DIVISIONS_FILE),
        'territories':         _kpi_load_list(KPI_TERRITORIES_FILE),
        'product_groups':      _kpi_load_list(KPI_PRODUCT_GROUPS_FILE),
    })


@app.route('/api/managers/kpi/filters', methods=['POST'])
def set_managers_kpi_filters():
    """Сохранить (запомнить) выбор фильтров групп для страницы KPI."""
    try:
        d = request.get_json() or {}
        if 'sales_client_groups' in d: _kpi_save_list(KPI_SALES_CLIENT_GROUPS_FILE, d['sales_client_groups'])
        if 'debt_client_groups'  in d: _kpi_save_list(KPI_DEBT_CLIENT_GROUPS_FILE,  d['debt_client_groups'])
        if 'sales_divisions'     in d: _kpi_save_list(KPI_SALES_DIVISIONS_FILE,     d['sales_divisions'])
        if 'debt_divisions'      in d: _kpi_save_list(KPI_DEBT_DIVISIONS_FILE,      d['debt_divisions'])
        if 'territories'         in d: _kpi_save_list(KPI_TERRITORIES_FILE,         d['territories'])
        if 'product_groups'      in d: _kpi_save_list(KPI_PRODUCT_GROUPS_FILE,      d['product_groups'])
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Ошибка сохранения KPI-фильтров: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/managers/kpi/filter-options')
def get_managers_kpi_filter_options():
    """Опции для новых фильтров KPI: территории (SArea) и товарные группы (PrdctGrp). READ-ONLY."""
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT t.fCODE, ISNULL(t.fCAPTION, t.fCODE) AS name
            FROM TREES t WITH (NOLOCK)
            WHERE t.fTREEID='SArea'
              AND t.fCODE IN (SELECT DISTINCT fSALESAREA FROM CUSTOMERSALESAREAS WITH (NOLOCK))
            ORDER BY t.fCODE
        """)
        territories = [{'code': r.fCODE, 'name': r.name} for r in cur.fetchall()]
        cur.execute("""
            SELECT t.fCODE, ISNULL(t.fCAPTION, t.fCODE) AS name
            FROM TREES t WITH (NOLOCK)
            WHERE t.fTREEID='PrdctGrp'
              AND t.fCODE IN (SELECT DISTINCT fGROUP FROM PRODUCTS WITH (NOLOCK) WHERE fGROUP IS NOT NULL AND fGROUP<>'')
            ORDER BY t.fCODE
        """)
        product_groups = [{'code': r.fCODE, 'name': r.name} for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'territories': territories, 'product_groups': product_groups})
    except Exception as e:
        logger.error(f"Ошибка загрузки опций KPI-фильтров: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/managers-kpi')
def managers_kpi_page():
    """Страница KPI по менеджерам (рейтинг + скоркарты)"""
    return render_template('managers_kpi.html')


@app.route('/api/managers/kpi')
def api_managers_kpi():
    """API: полный набор KPI по менеджерам с перцентильным скорингом и рейтингом. READ-ONLY."""
    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        if not date_from or not date_to:
            today = datetime.now()
            date_from = today.replace(day=1).strftime('%Y-%m-%d')
            last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            date_to = last_day.strftime('%Y-%m-%d')

        # Целевой рост плана (%) к прошлогоднему периоду. По умолчанию 0 (чистый YoY).
        try:
            plan_growth = float(request.args.get('plan_growth', 0) or 0)
        except (TypeError, ValueError):
            plan_growth = 0.0
        plan_growth = max(-100.0, min(1000.0, plan_growth))

        excluded_filter, excluded_params = get_excluded_filter_sql()

        # Сохранённые фильтры групп (раздельно продажи/долг)
        sc = _kpi_load_list(KPI_SALES_CLIENT_GROUPS_FILE)   # группы клиентов — продажи
        dc = _kpi_load_list(KPI_DEBT_CLIENT_GROUPS_FILE)    # группы клиентов — долг
        sd = _kpi_load_list(KPI_SALES_DIVISIONS_FILE)       # дивизионы — продажи
        dd = _kpi_load_list(KPI_DEBT_DIVISIONS_FILE)        # дивизионы — долг
        # Новые фильтры «на весь анализ»: территории (клиентская привязка) и товарные группы (по строкам продаж).
        sa = _kpi_load_list(KPI_TERRITORIES_FILE)           # территории (fSALESAREA)
        pg = _kpi_load_list(KPI_PRODUCT_GROUPS_FILE)        # товарные группы (PrdctGrp)

        def grp_where(sel):
            if not sel:
                return "", ()
            return " AND c.fGROUP IN (%s)" % ','.join('?' * len(sel)), tuple(sel)

        def cust_join(sel, custid_expr):
            # INNER JOIN CUSTOMERS c добавляем ТОЛЬКО когда фильтр задан (иначе цифры не меняются)
            if not sel:
                return ""
            return " INNER JOIN CUSTOMERS c WITH (NOLOCK) ON %s = c.fID" % custid_expr

        def div_where(alias, sel):
            if not sel:
                return "", ()
            return (" AND %s.fSALESAGENTID IN (SELECT DISTINCT fSALESAGENTID FROM SALESAGENTDIVISIONS WITH (NOLOCK) WHERE fDIVISION IN (%s))"
                    % (alias, ','.join('?' * len(sel))), tuple(sel))

        def terr_where(custid_expr):
            # Территория: клиент принадлежит одной из выбранных зон (CUSTOMERSALESAREAS). Применяется везде.
            if not sa:
                return "", ()
            return (" AND %s IN (SELECT fCUSTOMERID FROM CUSTOMERSALESAREAS WITH (NOLOCK) WHERE fSALESAREA IN (%s))"
                    % (custid_expr, ','.join('?' * len(sa))), tuple(sa))

        def area_where(area_expr):
            # Тот же территориальный фильтр, но для запросов, где уже есть CUSTOMERSALESAREAS (ограничиваем зону напрямую).
            if not sa:
                return "", ()
            return (" AND %s IN (%s)" % (area_expr, ','.join('?' * len(sa))), tuple(sa))

        def prod_where(isn_expr):
            # Товарные группы: документ продажи содержит товар из выбранных групп (PrdctGrp). Только для продаж.
            if not pg:
                return "", ()
            return ((" AND %s IN (SELECT sd2.fISN FROM SALEDOCDETAILS sd2 WITH (NOLOCK)"
                     " INNER JOIN PRODUCTS p2 WITH (NOLOCK) ON p2.fID=sd2.fPRODUCTID WHERE p2.fGROUP IN (%s))")
                    % (isn_expr, ','.join('?' * len(pg))), tuple(pg))

        sc_w, sc_p = grp_where(sc)          # продажи: клиентские группы (alias c)
        dc_w, dc_p = grp_where(dc)          # долг: клиентские группы (alias c)
        sd_w, sd_p = div_where('s', sd)     # продажи: дивизионы (агент s)
        dd_w, dd_p = div_where('doc', dd)   # долг: дивизионы (агент doc)

        # Территория (по клиенту) — применяется ко ВСЕМ метрикам; товарные группы (по документу продажи) — к продажам.
        ta_s_w,    ta_s_p    = terr_where('s.fCUSTOMERID')
        ta_ar_w,   ta_ar_p   = terr_where('ar.fCUSTOMERID')
        ta_rt_w,   ta_rt_p   = terr_where('rt.fCUSTOMERID')
        ta_doc_w,  ta_doc_p  = terr_where('doc.fCUSTOMERID')
        ta_l_w,    ta_l_p    = terr_where('l.fCUSTOMERID')
        ta_area_w, ta_area_p = area_where('csa.fSALESAREA')
        pg_s_w,    pg_s_p    = prod_where('s.fISN')

        # ПОСТРОЧНАЯ выручка при активном фильтре товарных групп: выручка = сумма строк выбранных групп
        # (SALEDOCDETAILS.fSUM), а не вся накладная. Иначе товарный фильтр завышал бы выручку.
        # Счётчик накладных — COUNT(DISTINCT s.fISN) (строки размножают документ джойном).
        # Без товарного фильтра — документный режим (SUM(fTOTALSUM), COUNT(fISN)) = прежнее поведение.
        if pg:
            _pg_ph   = ','.join('?' * len(pg))
            rev_join = (" INNER JOIN SALEDOCDETAILS sdp WITH (NOLOCK) ON sdp.fISN=s.fISN"
                        " INNER JOIN PRODUCTS pp WITH (NOLOCK) ON pp.fID=sdp.fPRODUCTID")
            rev_pgw  = " AND pp.fGROUP IN (%s)" % _pg_ph
            rev_pgp  = tuple(pg)
            rev_expr = "ISNULL(SUM(sdp.fSUM),0)"
            doc_cnt  = "COUNT(DISTINCT s.fISN)"
            # B (глубина корзины): при товарном фильтре считаем только строки выбранных групп
            b_pg_join = " INNER JOIN PRODUCTS pb WITH (NOLOCK) ON pb.fID=sd.fPRODUCTID"
            b_pg_w    = " AND pb.fGROUP IN (%s)" % _pg_ph
            b_pg_p    = tuple(pg)
        else:
            rev_join, rev_pgw, rev_pgp = "", "", ()
            rev_expr = "ISNULL(SUM(s.fTOTALSUM),0)"
            doc_cnt  = "COUNT(s.fISN)"
            b_pg_join, b_pg_w, b_pg_p = "", "", ()

        conn = db.get_connection()
        cur = conn.cursor()

        # «As-of» дата = последний день с продажами в пределах периода (≤ date_to).
        # Для НЕЗАВЕРШЁННОГО периода (напр. текущий месяц: 9 дней) даёт сравнение/план like-for-like —
        # базовые окна (прошлый месяц/год) и план обрезаются до того же дня, а не берут полный месяц.
        # Для завершённого периода asof == date_to → поведение прежнее.
        cur.execute("SELECT MAX(fDATE) FROM SALES WITH (NOLOCK) WHERE fSTATE=2 AND fDATE<=?", (date_to,))
        _row = cur.fetchone()
        _last = _row[0] if _row else None
        asof = (_last.strftime('%Y-%m-%d') if hasattr(_last, 'strftime') else str(_last)[:10]) if _last else date_to

        M = {}
        def ensure(a):
            if a not in M:
                M[a] = {k: 0 for k in ["revenue", "salesCount", "activeCustomers", "avgCheck",
                        "assigned", "visits", "visitedCustomers", "lines", "returnsSum",
                        "returnCount", "newCustomers", "collected", "prevYear",
                        "routePlanned", "routeVisited", "routeOrdered"]}
            return M[a]

        # A: основные метрики продаж (исключённые клиенты + группы клиентов + дивизионы — продажи)
        # Выручка построчная при товарном фильтре (rev_expr/rev_join/rev_pgw); avgCheck = выручка/накладные
        # (математически = AVG(fTOTALSUM) в документном режиме, без регрессий).
        cur.execute(f"""
            SELECT s.fSALESAGENTID AS agent, {doc_cnt} AS SalesCount,
                   COUNT(DISTINCT s.fCUSTOMERID) AS ActiveCustomers,
                   {rev_expr} AS Revenue
            FROM SALES s WITH (NOLOCK){rev_join}
            INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID=c.fID
            WHERE s.fDATE>=? AND s.fDATE<=? AND s.fSTATE=2 {excluded_filter}{sc_w}{sd_w}{ta_s_w}{rev_pgw}
            GROUP BY s.fSALESAGENTID
        """, (date_from, date_to) + excluded_params + sc_p + sd_p + ta_s_p + rev_pgp)
        for r in cur.fetchall():
            m = ensure(r.agent)
            m["salesCount"] = r.SalesCount
            m["activeCustomers"] = r.ActiveCustomers
            m["revenue"] = float(r.Revenue)
            m["avgCheck"] = (m["revenue"] / m["salesCount"]) if m["salesCount"] else 0.0

        # СРАВНЕНИЕ ЗА 3 ГОДА: те же метрики агента за тот же период текущего года / год назад / 2 года назад.
        # По ТЕРРИТОРИИ менеджера (устойчиво к текучке — заполнено у всех). Фильтры: территория + группы клиентов + товарные группы.
        # years — целое (1 или 2), не пользовательский ввод → безопасно для подстановки.
        def _cmp_territory(years):
            cur.execute(f"""
                WITH AgentCustomers AS (
                    SELECT DISTINCT saa.fSALESAGENTID AS agent, csa.fCUSTOMERID AS cust
                    FROM SALESAGENTAREAS saa WITH (NOLOCK)
                    INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON csa.fSALESAREA = saa.fSALESAREA{ta_area_w}
                )
                SELECT ac.agent, {doc_cnt} AS cnt, COUNT(DISTINCT s.fCUSTOMERID) AS clients,
                       {rev_expr} AS rev
                FROM AgentCustomers ac
                INNER JOIN SALES s WITH (NOLOCK) ON s.fCUSTOMERID = ac.cust{rev_join}
                INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
                WHERE s.fDATE>=DATEADD(YEAR,-{years},?) AND s.fDATE<=DATEADD(YEAR,-{years},?) AND s.fSTATE=2 {excluded_filter}{sc_w}{rev_pgw}
                GROUP BY ac.agent
            """, ta_area_p + (date_from, asof) + excluded_params + sc_p + rev_pgp)
            out = {}
            for r in cur.fetchall():
                rev = float(r.rev)
                out[r.agent] = {"revenue": rev, "activeCustomers": r.clients,
                                "salesCount": r.cnt, "avgCheck": (rev / r.cnt if r.cnt else 0)}
            return out
        cmp_y1 = _cmp_territory(1)   # год назад
        cmp_y2 = _cmp_territory(2)   # 2 года назад

        # Командные итоги за 3 года (текущий / год назад / 2 года назад) — суммарно по всей команде, те же фильтры.
        def _team_totals(years):
            d_from = f"DATEADD(YEAR,-{years},?)" if years else "?"
            d_to   = f"DATEADD(YEAR,-{years},?)" if years else "?"
            cur.execute(f"""
                SELECT {doc_cnt} AS cnt, COUNT(DISTINCT s.fCUSTOMERID) AS clients,
                       {rev_expr} AS rev
                FROM SALES s WITH (NOLOCK){rev_join}
                INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID=c.fID
                WHERE s.fDATE>={d_from} AND s.fDATE<={d_to} AND s.fSTATE=2 {excluded_filter}{sc_w}{sd_w}{ta_s_w}{rev_pgw}
            """, (date_from, asof) + excluded_params + sc_p + sd_p + ta_s_p + rev_pgp)
            r = cur.fetchone()
            rev = float(r.rev)
            return {"revenue": rev, "activeCustomers": r.clients, "salesCount": r.cnt,
                    "avgCheck": (rev / r.cnt if r.cnt else 0)}
        team_cmp = {"current": _team_totals(0), "y1": _team_totals(1), "y2": _team_totals(2)}

        # B: строк в накладной (SKU depth)
        cur.execute(f"""
            SELECT s.fSALESAGENTID AS agent, COUNT(sd.fISN) AS Lines
            FROM SALES s WITH (NOLOCK) INNER JOIN SALEDOCDETAILS sd WITH (NOLOCK) ON sd.fISN=s.fISN{b_pg_join}
            {cust_join(sc, 's.fCUSTOMERID')}
            WHERE s.fDATE>=? AND s.fDATE<=? AND s.fSTATE=2 {sc_w}{ta_s_w}{b_pg_w} GROUP BY s.fSALESAGENTID
        """, (date_from, date_to) + sc_p + ta_s_p + b_pg_p)
        for r in cur.fetchall():
            ensure(r.agent)["lines"] = r.Lines

        # C: визиты (GPS, ACTUALROUTES)
        cur.execute(f"""
            SELECT ar.fSALESAGENTID AS agent, COUNT(*) AS Visits, COUNT(DISTINCT ar.fCUSTOMERID) AS VC
            FROM ACTUALROUTES ar WITH (NOLOCK)
            {cust_join(sc, 'ar.fCUSTOMERID')}
            WHERE ar.fDATE>=? AND ar.fDATE<=? {sc_w}{ta_ar_w} GROUP BY ar.fSALESAGENTID
        """, (date_from, date_to) + sc_p + ta_ar_p)
        for r in cur.fetchall():
            m = ensure(r.agent)
            m["visits"] = r.Visits
            m["visitedCustomers"] = r.VC

        # D: возвраты
        cur.execute(f"""
            SELECT rt.fSALESAGENTID AS agent, COUNT(*) AS RC, ISNULL(SUM(rt.fTOTALSUM),0) AS RS
            FROM RETURNS rt WITH (NOLOCK)
            {cust_join(sc, 'rt.fCUSTOMERID')}
            WHERE rt.fDATE>=? AND rt.fDATE<=? AND rt.fSTATE=2 {sc_w}{ta_rt_w} GROUP BY rt.fSALESAGENTID
        """, (date_from, date_to) + sc_p + ta_rt_p)
        for r in cur.fetchall():
            m = ensure(r.agent)
            m["returnCount"] = r.RC
            m["returnsSum"] = float(r.RS)

        # E: назначено клиентов (покрытие территорий агента)
        cur.execute(f"""
            SELECT saa.fSALESAGENTID AS agent, COUNT(DISTINCT csa.fCUSTOMERID) AS Assigned
            FROM SALESAGENTAREAS saa WITH (NOLOCK)
            INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON csa.fSALESAREA=saa.fSALESAREA
            {cust_join(sc, 'csa.fCUSTOMERID')}
            WHERE 1=1 {sc_w}{ta_area_w}
            GROUP BY saa.fSALESAGENTID
        """, sc_p + ta_area_p)
        for r in cur.fetchall():
            ensure(r.agent)["assigned"] = r.Assigned

        # F: новые клиенты (первая продажа в периоде)
        cur.execute(f"""
            WITH firsts AS (SELECT fCUSTOMERID, MIN(fDATE) AS firstsale FROM SALES WITH (NOLOCK)
                            WHERE fSTATE=2 GROUP BY fCUSTOMERID)
            SELECT s.fSALESAGENTID AS agent, COUNT(DISTINCT s.fCUSTOMERID) AS NewC
            FROM firsts f INNER JOIN SALES s WITH (NOLOCK)
                ON s.fCUSTOMERID=f.fCUSTOMERID AND s.fDATE=f.firstsale AND s.fSTATE=2
            {cust_join(sc, 's.fCUSTOMERID')}
            WHERE f.firstsale>=? AND f.firstsale<=? {sc_w}{ta_s_w}{pg_s_w} GROUP BY s.fSALESAGENTID
        """, (date_from, date_to) + sc_p + ta_s_p + pg_s_p)
        for r in cur.fetchall():
            ensure(r.agent)["newCustomers"] = r.NewC

        # G: сбор платежей (PAY через DOCUMENTS.fSALESAGENTID) + группы клиентов и дивизионы ДОЛГА
        cur.execute(f"""
            SELECT doc.fSALESAGENTID AS agent, ISNULL(SUM(ABS(h.fSUM)),0) AS Collected
            FROM HICUSTOMERSDEBT h WITH (NOLOCK)
            INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON h.fDEBTDOCISN=doc.fISN
            {cust_join(dc, 'doc.fCUSTOMERID')}
            WHERE h.fOP='PAY' AND h.fDBCR='C' AND h.fDATE>=? AND h.fDATE<=? {dc_w}{dd_w}{ta_doc_w}
            GROUP BY doc.fSALESAGENTID
        """, (date_from, date_to) + dc_p + dd_p + ta_doc_p)
        for r in cur.fetchall():
            ensure(r.agent)["collected"] = float(r.Collected)

        # H: план = продажи ТОГО ЖЕ ПЕРИОДА год назад по ТЕРРИТОРИИ менеджера × (1 + рост).
        #   База — прошлогодние продажи клиентов, которые СЕЙЧАС закреплены за территориями агента
        #   (SALESAGENTAREAS→CUSTOMERSALESAREAS, как в «Покрытии»), независимо от того, какой агент их тогда
        #   обслуживал. Устойчиво к текучке/смене агентов. Сезонность — натурально (тот же период год назад).
        #   Фильтры: исключённые клиенты + группы клиентов. Дивизионы не применяются (атрибуция территориальная).
        cur.execute(f"""
            WITH AgentCustomers AS (
                SELECT DISTINCT saa.fSALESAGENTID AS agent, csa.fCUSTOMERID AS cust
                FROM SALESAGENTAREAS saa WITH (NOLOCK)
                INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON csa.fSALESAREA = saa.fSALESAREA{ta_area_w}
            )
            SELECT ac.agent, {rev_expr} AS PrevYear
            FROM AgentCustomers ac
            INNER JOIN SALES s WITH (NOLOCK) ON s.fCUSTOMERID = ac.cust{rev_join}
            INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
            WHERE s.fDATE>=DATEADD(YEAR,-1,?) AND s.fDATE<=DATEADD(YEAR,-1,?) AND s.fSTATE=2 {excluded_filter}{sc_w}{rev_pgw}
            GROUP BY ac.agent
        """, ta_area_p + (date_from, asof) + excluded_params + sc_p + rev_pgp)
        for r in cur.fetchall():
            ensure(r.agent)["prevYear"] = float(r.PrevYear)

        # R: исполнение планового маршрута — план / подъехал / заказал (по клиентам маршрута агента)
        #   База = уникальные клиенты из документов планового маршрута (DOCUMENTS.fDOCTYPE=10) агента за период.
        #   «Подъехал» = клиенты маршрута с фактическим визитом (ACTUALROUTES) того же агента.
        #   «Заказал»  = клиенты маршрута с проведённой продажей (fSTATE=2) того же агента.
        #   Фильтр «группы клиентов (продажи)» (sc) применяется к базе маршрута; дивизионы (товары) не влияют.
        cur.execute(f"""
            WITH RoutePlan AS (
                SELECT DISTINCT d.fSALESAGENTID AS agent, l.fCUSTOMERID AS cust
                FROM DOCUMENTS d WITH (NOLOCK)
                JOIN PLANNEDROUTESLIST l WITH (NOLOCK) ON d.fISN = l.fISN
                {cust_join(sc, 'l.fCUSTOMERID')}
                WHERE d.fDOCTYPE=10 AND d.fDATE>=? AND d.fDATE<=? {sc_w}{ta_l_w}
            ),
            Visited AS (
                SELECT DISTINCT ar.fSALESAGENTID AS agent, ar.fCUSTOMERID AS cust
                FROM ACTUALROUTES ar WITH (NOLOCK) WHERE ar.fDATE>=? AND ar.fDATE<=?
            ),
            Ordered AS (
                SELECT DISTINCT s.fSALESAGENTID AS agent, s.fCUSTOMERID AS cust
                FROM SALES s WITH (NOLOCK) WHERE s.fDATE>=? AND s.fDATE<=? AND s.fSTATE=2
            )
            SELECT rp.agent,
                   COUNT(DISTINCT rp.cust) AS Planned,
                   COUNT(DISTINCT CASE WHEN v.cust IS NOT NULL THEN rp.cust END) AS Visited,
                   COUNT(DISTINCT CASE WHEN o.cust IS NOT NULL THEN rp.cust END) AS Ordered
            FROM RoutePlan rp
            LEFT JOIN Visited v ON v.agent=rp.agent AND v.cust=rp.cust
            LEFT JOIN Ordered o ON o.agent=rp.agent AND o.cust=rp.cust
            GROUP BY rp.agent
        """, (date_from, date_to) + sc_p + ta_l_p + (date_from, date_to) + (date_from, date_to))
        for r in cur.fetchall():
            m = ensure(r.agent)
            m["routePlanned"] = r.Planned
            m["routeVisited"] = r.Visited
            m["routeOrdered"] = r.Ordered

        # G2: ЧИСТЫЙ ДОЛГ по менеджеру (полная формула из DEBT_CALCULATION_FORMULA.md):
        #   ДОЛГ = Дебет(HICUSTOMERSDEBT, D−C) − |Возвраты Type01| − |Переплаты Type02| (HIRESTCUSTOMERSSUM)
        # Атрибутируется по клиентам, которым менеджер продавал в периоде (как в /api/managers).
        # Фильтр «долг: группы клиентов» (dc) применяется к набору клиентов.
        # Территория применяется и к долгу — через набор клиентов cust_sub (fCUSTOMERID).
        ta_cust_w, ta_cust_p = terr_where('fCUSTOMERID')
        cust_sub = ("SELECT DISTINCT fCUSTOMERID FROM SALES WITH (NOLOCK) "
                    "WHERE fSALESAGENTID=? AND fDATE>=? AND fDATE<=? AND fSTATE=2" + ta_cust_w)
        for aid, m in M.items():
            if m["salesCount"] == 0:
                continue
            cur.execute(f"""
                SELECT ISNULL(SUM(CASE WHEN h.fDBCR='D' THEN h.fSUM ELSE -h.fSUM END),0) AS Debit
                FROM HICUSTOMERSDEBT h WITH (NOLOCK)
                INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON h.fDEBTDOCISN=doc.fISN
                INNER JOIN CUSTOMERS c WITH (NOLOCK) ON doc.fCUSTOMERID=c.fID
                WHERE doc.fCUSTOMERID IN ({cust_sub}) {dc_w}
            """, (aid, date_from, date_to) + ta_cust_p + dc_p)
            debit = float(cur.fetchone().Debit or 0)
            cur.execute(f"""
                SELECT ISNULL(SUM(CASE WHEN r.fTYPE='01' THEN r.fSUM ELSE 0 END),0) AS T1,
                       ISNULL(SUM(CASE WHEN r.fTYPE='02' THEN r.fSUM ELSE 0 END),0) AS T2
                FROM HIRESTCUSTOMERSSUM r WITH (NOLOCK)
                INNER JOIN CUSTOMERS c WITH (NOLOCK) ON r.fCUSTOMERID=c.fID
                WHERE r.fCUSTOMERID IN ({cust_sub}) {dc_w}
            """, (aid, date_from, date_to) + ta_cust_p + dc_p)
            rr = cur.fetchone()
            t1 = abs(float(rr.T1 or 0))
            t2 = abs(float(rr.T2 or 0))
            m["debtDebit"] = debit
            m["returnsType01"] = t1
            m["overpayType02"] = t2
            m["debt"] = debit - t1 - t2

        # имена агентов + территории
        cur.execute("SELECT fID, fCODE, fNAME, fCLOSED FROM SALESAGENTS WITH (NOLOCK)")
        names = {r.fID: (r.fCODE, r.fNAME, r.fCLOSED) for r in cur.fetchall()}
        cur.execute("""
            SELECT sa.fSALESAGENTID, sa.fSALESAREA, sa.fDEFAULT, t.fCAPTION
            FROM SALESAGENTAREAS sa WITH (NOLOCK)
            LEFT JOIN TREES t WITH (NOLOCK) ON t.fCODE = sa.fSALESAREA AND t.fTREEID='SArea'
            ORDER BY sa.fSALESAGENTID, sa.fDEFAULT DESC, sa.fROWNUM
        """)
        areas_map = {}
        for r in cur.fetchall():
            areas_map.setdefault(r.fSALESAGENTID, []).append(
                {"code": r.fSALESAREA, "name": r.fCAPTION or str(r.fSALESAREA), "is_default": bool(r.fDEFAULT)})
        conn.close()

        period_months = _months_between(date_from, date_to)
        rows = []
        for aid, m in M.items():
            if m["salesCount"] == 0:
                continue
            code, name, closed = names.get(aid, (None, f"#{aid}", 0))
            rev = m["revenue"]
            coverage = min(100.0, m["activeCustomers"] / m["assigned"] * 100) if m["assigned"] else None
            rp = m["routePlanned"]
            route_visit = (m["routeVisited"] / rp * 100) if rp else None   # подъехал ÷ план маршрута
            route_order = (m["routeOrdered"] / rp * 100) if rp else None   # заказал ÷ план маршрута
            strike = (m["salesCount"] / m["visits"] * 100) if m["visits"] else None
            lpi = (m["lines"] / m["salesCount"]) if m["salesCount"] else 0
            returns_rate = (m["returnsSum"] / rev * 100) if rev else 0
            collect_rate = (m["collected"] / rev * 100) if rev else 0
            plan = (m["prevYear"] * (1 + plan_growth / 100.0)) if m["prevYear"] else None
            plan_fact = (rev / plan * 100) if plan else None
            rows.append({
                "fID": aid, "fCODE": code, "fNAME": name, "closed": int(closed or 0),
                "revenue": rev, "salesCount": m["salesCount"], "activeCustomers": m["activeCustomers"],
                "avgCheck": m["avgCheck"], "assigned": m["assigned"],
                "coverage": round(coverage, 1) if coverage is not None else None,
                "routePlanned": rp, "routeVisited": m["routeVisited"], "routeOrdered": m["routeOrdered"],
                "routeVisit": round(route_visit, 1) if route_visit is not None else None,
                "routeOrder": round(route_order, 1) if route_order is not None else None,
                "visits": m["visits"], "visitedCustomers": m["visitedCustomers"],
                "strikeRate": round(strike, 1) if strike is not None else None,
                "linesPerInvoice": round(lpi, 2), "returnsSum": m["returnsSum"],
                "returnsRate": round(returns_rate, 2), "newCustomers": m["newCustomers"],
                "collected": m["collected"], "collectRate": round(collect_rate, 1),
                "plan": round(plan) if plan else None,
                "prevYear": round(m["prevYear"]),
                "planFact": round(plan_fact, 1) if plan_fact is not None else None,
                "cmp": {"y1": cmp_y1.get(aid), "y2": cmp_y2.get(aid)},
                "debt": round(m.get("debt", 0), 2),
                "debtDebit": round(m.get("debtDebit", 0), 2),
                "returnsType01": round(m.get("returnsType01", 0), 2),
                "overpayType02": round(m.get("overpayType02", 0), 2),
                "SalesAreas": areas_map.get(aid, []),
            })

        # перцентильный скоринг 0..100 по каждому KPI (устойчив к выбросам)
        for r in rows:
            r["scores"] = {}
        for key, label, unit, higher, weight in MANAGER_KPI_DEFS:
            present = [r for r in rows if r.get(key) is not None]
            for r in rows:
                if r.get(key) is None:
                    r["scores"][key] = None
            n = len(present)
            if n == 0:
                continue
            if n == 1:
                present[0]["scores"][key] = 100.0
                continue
            order = sorted(present, key=lambda r: r[key], reverse=higher)
            i = 0
            while i < n:
                j = i
                while j < n and order[j][key] == order[i][key]:
                    j += 1
                avg_pos = (i + j - 1) / 2.0
                sc = round(100 * (n - 1 - avg_pos) / (n - 1), 1)
                for k in range(i, j):
                    order[k]["scores"][key] = sc
                i = j

        for r in rows:
            num = den = 0
            for key, label, unit, higher, weight in MANAGER_KPI_DEFS:
                s = r["scores"].get(key)
                if s is not None:
                    num += s * weight
                    den += weight
            r["score"] = round(num / den, 1) if den else 0

        rows.sort(key=lambda x: -x["score"])
        for i, r in enumerate(rows, 1):
            r["rank"] = i

        team = {
            "managers": len(rows),
            "revenue": sum(r["revenue"] for r in rows),
            "collected": sum(r["collected"] for r in rows),
            "salesCount": sum(r["salesCount"] for r in rows),
            "newCustomers": sum(r["newCustomers"] for r in rows),
            "returnsSum": sum(r["returnsSum"] for r in rows),
            "debt": sum(r["debt"] for r in rows),
            "returnsType01": sum(r["returnsType01"] for r in rows),
            "overpayType02": sum(r["overpayType02"] for r in rows),
            "avgCoverage": round(sum(r["coverage"] for r in rows if r["coverage"] is not None)
                                 / max(1, sum(1 for r in rows if r["coverage"] is not None)), 1),
            "cmp": team_cmp,
        }
        kpis = [{"key": k, "label": lbl, "unit": u, "higher": hb, "weight": w}
                for (k, lbl, u, hb, w) in MANAGER_KPI_DEFS]

        return jsonify({"success": True, "period": {"date_from": date_from, "date_to": date_to,
                        "months": period_months}, "team": team, "kpis": kpis, "managers": rows})

    except Exception as e:
        logger.error(f"Ошибка получения KPI менеджеров: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _aggregate_product_groups(rows):
    """Схлопывает строки (fCODE,fNAME,fMEASUREUNIT,grp,grpname,qcur,qy1,qy2) в группы по товарной группе
    (PRODUCTS.fGROUP -> PrdctGrp). qcur/qy1/qy2 — количество за тот же период текущего года / год назад / 2 года назад.
    Товары без группы — отдельными строками (grouped=False). Топ-300 по qcur."""
    groups = {}
    for r in rows:
        qcur, qy1, qy2 = float(r.qcur or 0), float(r.qy1 or 0), float(r.qy2 or 0)
        if qcur == 0 and qy1 == 0 and qy2 == 0:
            continue
        grp = (r.grp or '').strip()
        if grp:
            key, name, grouped = 'g:' + grp, (r.grpname or grp), True
        else:
            key, name, grouped = 'p:' + r.fCODE, r.fNAME, False
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"code": key, "name": name, "grouped": grouped,
                               "qcur": 0.0, "qy1": 0.0, "qy2": 0.0, "items": []}
        g["qcur"] += qcur; g["qy1"] += qy1; g["qy2"] += qy2
        g["items"].append({"code": r.fCODE, "name": r.fNAME, "unit": r.fMEASUREUNIT or "",
                           "qcur": round(qcur, 2), "qy1": round(qy1, 2), "qy2": round(qy2, 2)})
    out = []
    for g in groups.values():
        g["items"].sort(key=lambda x: -x["qcur"])
        g["n"] = len(g["items"])
        g["unit"] = g["items"][0]["unit"] if g["items"] else ""
        g["qcur"] = round(g["qcur"], 2); g["qy1"] = round(g["qy1"], 2); g["qy2"] = round(g["qy2"], 2)
        out.append(g)
    out.sort(key=lambda x: -x["qcur"])
    return out[:300]


@app.route('/api/managers/<int:agent_id>/product-sales')
def api_manager_product_sales(agent_id):
    """Продажи по товарам (количество) по ТЕРРИТОРИИ менеджера: этот период / прошлый месяц / прошлый год.
    Сравнение like-for-like: базовые окна обрезаются по asof (последний день с данными). READ-ONLY."""
    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        if not date_from or not date_to:
            today = datetime.now()
            date_from = today.replace(day=1).strftime('%Y-%m-%d')
            last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            date_to = last_day.strftime('%Y-%m-%d')

        excluded_filter, excluded_params = get_excluded_filter_sql()
        sc = _kpi_load_list(KPI_SALES_CLIENT_GROUPS_FILE)
        sc_w = (" AND c.fGROUP IN (%s)" % ','.join('?' * len(sc))) if sc else ""
        sc_p = tuple(sc)
        sa = _kpi_load_list(KPI_TERRITORIES_FILE)          # территории (ограничиваем зоны агента)
        sa_w = (" AND csa.fSALESAREA IN (%s)" % ','.join('?' * len(sa))) if sa else ""
        sa_p = tuple(sa)
        pg = _kpi_load_list(KPI_PRODUCT_GROUPS_FILE)        # товарные группы (PrdctGrp)
        pg_w = (" AND p.fGROUP IN (%s)" % ','.join('?' * len(pg))) if pg else ""
        pg_p = tuple(pg)

        conn = db.get_connection()
        cur = conn.cursor()

        # asof = последний день с продажами в пределах периода (для like-for-like сдвигов)
        cur.execute("SELECT MAX(fDATE) FROM SALES WITH (NOLOCK) WHERE fSTATE=2 AND fDATE<=?", (date_to,))
        _row = cur.fetchone()
        _last = _row[0] if _row else None
        asof = (_last.strftime('%Y-%m-%d') if hasattr(_last, 'strftime') else str(_last)[:10]) if _last else date_to

        # Товар × 3 периода одним проходом (по клиентам территории агента). Сдвиги окон через DATEADD.
        cur.execute(f"""
            WITH AC AS (
                SELECT DISTINCT csa.fCUSTOMERID AS cust
                FROM SALESAGENTAREAS saa WITH (NOLOCK)
                INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON csa.fSALESAREA = saa.fSALESAREA
                WHERE saa.fSALESAGENTID = ?{sa_w}
            )
            SELECT p.fCODE, p.fNAME, p.fMEASUREUNIT, p.fGROUP AS grp, gt.fCAPTION AS grpname,
                   SUM(CASE WHEN s.fDATE>=? AND s.fDATE<=? THEN sd.fQUANTITY ELSE 0 END) AS qcur,
                   SUM(CASE WHEN s.fDATE>=DATEADD(YEAR,-1,?) AND s.fDATE<=DATEADD(YEAR,-1,?) THEN sd.fQUANTITY ELSE 0 END) AS qy1,
                   SUM(CASE WHEN s.fDATE>=DATEADD(YEAR,-2,?) AND s.fDATE<=DATEADD(YEAR,-2,?) THEN sd.fQUANTITY ELSE 0 END) AS qy2
            FROM SALES s WITH (NOLOCK)
            INNER JOIN SALEDOCDETAILS sd WITH (NOLOCK) ON sd.fISN = s.fISN
            INNER JOIN PRODUCTS p WITH (NOLOCK) ON p.fID = sd.fPRODUCTID
            LEFT JOIN TREES gt WITH (NOLOCK) ON gt.fCODE = p.fGROUP AND gt.fTREEID = 'PrdctGrp'
            INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
            WHERE s.fSTATE=2 AND s.fCUSTOMERID IN (SELECT cust FROM AC)
              AND s.fDATE >= DATEADD(YEAR,-2,?) AND s.fDATE <= ? {excluded_filter}{sc_w}{pg_w}
            GROUP BY p.fCODE, p.fNAME, p.fMEASUREUNIT, p.fGROUP, gt.fCAPTION
        """, (agent_id,) + sa_p + (date_from, asof, date_from, asof, date_from, asof, date_from, asof) + excluded_params + sc_p + pg_p)

        # Схлопываем товары в группы по PRODUCTS.fGROUP: все вкусы одного продукта в одну строку.
        rows = cur.fetchall()
        conn.close()
        out = _aggregate_product_groups(rows)
        return jsonify({"success": True, "period": {"date_from": date_from, "date_to": date_to, "asof": asof},
                        "groups": out})
    except Exception as e:
        logger.error(f"Ошибка продаж по товарам менеджера {agent_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/managers/product-sales-total')
def api_managers_product_sales_total():
    """Итоговые продажи по товарам ПО ВСЕЙ КОМАНДЕ (все продажи с учётом KPI-фильтров):
    этот период / прошлый месяц / прошлый год, like-for-like (обрезка по asof). READ-ONLY."""
    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        if not date_from or not date_to:
            today = datetime.now()
            date_from = today.replace(day=1).strftime('%Y-%m-%d')
            last_day = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            date_to = last_day.strftime('%Y-%m-%d')

        excluded_filter, excluded_params = get_excluded_filter_sql()
        sc = _kpi_load_list(KPI_SALES_CLIENT_GROUPS_FILE)
        sc_w = (" AND c.fGROUP IN (%s)" % ','.join('?' * len(sc))) if sc else ""
        sc_p = tuple(sc)
        sd = _kpi_load_list(KPI_SALES_DIVISIONS_FILE)
        sd_w = (" AND s.fSALESAGENTID IN (SELECT DISTINCT fSALESAGENTID FROM SALESAGENTDIVISIONS WITH (NOLOCK) WHERE fDIVISION IN (%s))"
                % ','.join('?' * len(sd))) if sd else ""
        sd_p = tuple(sd)
        sa = _kpi_load_list(KPI_TERRITORIES_FILE)          # территории (клиент в выбранных зонах)
        sa_w = (" AND s.fCUSTOMERID IN (SELECT fCUSTOMERID FROM CUSTOMERSALESAREAS WITH (NOLOCK) WHERE fSALESAREA IN (%s))"
                % ','.join('?' * len(sa))) if sa else ""
        sa_p = tuple(sa)
        pg = _kpi_load_list(KPI_PRODUCT_GROUPS_FILE)        # товарные группы (PrdctGrp)
        pg_w = (" AND p.fGROUP IN (%s)" % ','.join('?' * len(pg))) if pg else ""
        pg_p = tuple(pg)

        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT MAX(fDATE) FROM SALES WITH (NOLOCK) WHERE fSTATE=2 AND fDATE<=?", (date_to,))
        _row = cur.fetchone()
        _last = _row[0] if _row else None
        asof = (_last.strftime('%Y-%m-%d') if hasattr(_last, 'strftime') else str(_last)[:10]) if _last else date_to

        # Все продажи команды (те же фильтры: исключённые + группы клиентов + дивизионы + территория + товарные группы).
        cur.execute(f"""
            SELECT p.fCODE, p.fNAME, p.fMEASUREUNIT, p.fGROUP AS grp, gt.fCAPTION AS grpname,
                   SUM(CASE WHEN s.fDATE>=? AND s.fDATE<=? THEN sd.fQUANTITY ELSE 0 END) AS qcur,
                   SUM(CASE WHEN s.fDATE>=DATEADD(YEAR,-1,?) AND s.fDATE<=DATEADD(YEAR,-1,?) THEN sd.fQUANTITY ELSE 0 END) AS qy1,
                   SUM(CASE WHEN s.fDATE>=DATEADD(YEAR,-2,?) AND s.fDATE<=DATEADD(YEAR,-2,?) THEN sd.fQUANTITY ELSE 0 END) AS qy2
            FROM SALES s WITH (NOLOCK)
            INNER JOIN SALEDOCDETAILS sd WITH (NOLOCK) ON sd.fISN = s.fISN
            INNER JOIN PRODUCTS p WITH (NOLOCK) ON p.fID = sd.fPRODUCTID
            LEFT JOIN TREES gt WITH (NOLOCK) ON gt.fCODE = p.fGROUP AND gt.fTREEID = 'PrdctGrp'
            INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
            WHERE s.fSTATE=2 AND s.fDATE >= DATEADD(YEAR,-2,?) AND s.fDATE <= ? {excluded_filter}{sc_w}{sd_w}{sa_w}{pg_w}
            GROUP BY p.fCODE, p.fNAME, p.fMEASUREUNIT, p.fGROUP, gt.fCAPTION
        """, (date_from, asof, date_from, asof, date_from, asof, date_from, asof) + excluded_params + sc_p + sd_p + sa_p + pg_p)
        rows = cur.fetchall()
        conn.close()
        out = _aggregate_product_groups(rows)
        return jsonify({"success": True, "period": {"date_from": date_from, "date_to": date_to, "asof": asof},
                        "groups": out})
    except Exception as e:
        logger.error(f"Ошибка итоговых продаж по товарам команды: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/managers/<int:agent_id>/revenue-trend')
def api_manager_revenue_trend(agent_id):
    """Динамика выручки за 36 месяцев по ТЕРРИТОРИИ менеджера (его нынешние клиенты, независимо от того,
    какой агент их вёл — устойчиво к текучке). Учитывает KPI-фильтры (территория/группы клиентов/товарные группы),
    zero-fill по месяцам. READ-ONLY."""
    try:
        excluded_filter, excluded_params = get_excluded_filter_sql()
        sc = _kpi_load_list(KPI_SALES_CLIENT_GROUPS_FILE)
        sc_w = (" AND c.fGROUP IN (%s)" % ','.join('?' * len(sc))) if sc else ""; sc_p = tuple(sc)
        sa = _kpi_load_list(KPI_TERRITORIES_FILE)
        sa_w = (" AND csa.fSALESAREA IN (%s)" % ','.join('?' * len(sa))) if sa else ""; sa_p = tuple(sa)
        pg = _kpi_load_list(KPI_PRODUCT_GROUPS_FILE)
        pg_w = (" AND s.fISN IN (SELECT sd2.fISN FROM SALEDOCDETAILS sd2 WITH (NOLOCK)"
                " INNER JOIN PRODUCTS p2 WITH (NOLOCK) ON p2.fID=sd2.fPRODUCTID WHERE p2.fGROUP IN (%s))"
                % ','.join('?' * len(pg))) if pg else ""; pg_p = tuple(pg)

        conn = db.get_connection(); cur = conn.cursor()
        cur.execute("SELECT MAX(fDATE) FROM SALES WITH (NOLOCK) WHERE fSTATE=2")
        last = cur.fetchone()[0]
        ly, lm = (last.year, last.month) if last else (datetime.now().year, datetime.now().month)
        # 36 ярлыков месяцев, заканчивая последним месяцем с данными
        labels = []
        y, mo = ly, lm
        for _ in range(36):
            labels.append(f"{y:04d}-{mo:02d}")
            mo -= 1
            if mo == 0:
                y -= 1; mo = 12
        labels.reverse()
        start = labels[0] + "-01"

        cur.execute(f"""
            WITH AC AS (
                SELECT DISTINCT csa.fCUSTOMERID AS cust
                FROM SALESAGENTAREAS saa WITH (NOLOCK)
                INNER JOIN CUSTOMERSALESAREAS csa WITH (NOLOCK) ON csa.fSALESAREA = saa.fSALESAREA
                WHERE saa.fSALESAGENTID = ?{sa_w}
            )
            SELECT FORMAT(s.fDATE,'yyyy-MM') AS Month, ISNULL(SUM(s.fTOTALSUM),0) AS TotalSum,
                   COUNT(s.fISN) AS SalesCount
            FROM SALES s WITH (NOLOCK)
            INNER JOIN CUSTOMERS c WITH (NOLOCK) ON s.fCUSTOMERID = c.fID
            WHERE s.fSTATE=2 AND s.fCUSTOMERID IN (SELECT cust FROM AC)
              AND s.fDATE >= ? {excluded_filter}{sc_w}{pg_w}
            GROUP BY FORMAT(s.fDATE,'yyyy-MM')
        """, (agent_id,) + sa_p + (start,) + excluded_params + sc_p + pg_p)
        by_month = {r.Month: (float(r.TotalSum), int(r.SalesCount)) for r in cur.fetchall()}
        conn.close()
        series = [{"Month": mth, "TotalSum": by_month.get(mth, (0.0, 0))[0],
                   "SalesCount": by_month.get(mth, (0.0, 0))[1]} for mth in labels]
        return jsonify({"success": True, "data": {"sales_by_month": series}})
    except Exception as e:
        logger.error(f"Ошибка динамики выручки менеджера {agent_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/groups')
def groups_page():
    """Страница групп (дистрибьюторы)"""
    return render_template('groups.html')

@app.route('/distributors')
def distributors_page():
    """Страница анализа дистрибьюторов"""
    return render_template('distributors.html')

@app.route('/customer-cards')
def customer_cards_page():
    """Страница с карточками клиентов и детальной аналитикой"""
    return render_template('customer_cards.html')

@app.route('/areas')
def areas_page():
    """Страница с территориями"""
    return render_template('areas.html')

@app.route('/plans')
def plans_page():
    """Страница планов продаж и кредитов по территориям"""
    return render_template('plans.html')

@app.route('/ai-assistant')
def ai_assistant_page():
    """Страница AI помощника для анализа проблем"""
    return render_template('ai_assistant.html')

@app.route('/api/ai-analysis')
def ai_analysis():
    """AI анализ проблем: высокий долг, низкие платежи, падение продаж, неактивные клиенты"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получить фильтры
        excluded_filter, excluded_params = get_excluded_filter_sql()
        ai_groups_filter, ai_groups_params = get_ai_groups_filter_sql()
        ai_areas_filter, ai_areas_params = get_ai_areas_filter_sql()
        
        # Объединяем параметры
        combined_filter = excluded_filter + " " + ai_groups_filter + " " + ai_areas_filter
        combined_params = excluded_params + ai_groups_params + ai_areas_params
        
        problems = {
            'highDebt': [],
            'lowPayment': [],
            'salesDrop': [],
            'inactive': [],
            'irregular': []
        }
        
        # Счетчик для уникальных ID
        problem_id = 0
        
        # 1. ВЫСОКИЙ ДОЛГ - клиенты с долгом из HICUSTOMERSDEBT
        query_high_debt = f"""
        SELECT 
            c.fID as CustomerID,
            c.fCODE as CustomerCode,
            c.fNAME as CustomerName,
            ISNULL(csa.fSALESAREA, 'N/A') as AreaCode,
            ISNULL(sa.fCAPTION, 'Не указана') as AreaName,
            ISNULL(debt.TotalDebt, 0) as Debt,
            ISNULL(sales.MonthlySales, 0) as MonthlySales,
            ISNULL(pay.MonthlyPayments, 0) as MonthlyPayments
        FROM CUSTOMERS c
        LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        LEFT JOIN TREES sa ON csa.fSALESAREA = sa.fCODE AND sa.fTREEID = 'SArea'
        LEFT JOIN (
            SELECT doc.fCUSTOMERID, 
                   SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END) as TotalDebt
            FROM HICUSTOMERSDEBT d
            INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
            GROUP BY doc.fCUSTOMERID
        ) debt ON c.fID = debt.fCUSTOMERID
        LEFT JOIN (
            SELECT fCUSTOMERID, SUM(fTOTALSUM) as MonthlySales
            FROM SALES 
            WHERE fDATE >= DATEADD(MONTH, -1, GETDATE()) AND fSTATE = 2
            GROUP BY fCUSTOMERID
        ) sales ON c.fID = sales.fCUSTOMERID
        LEFT JOIN (
            SELECT fCUSTOMERID, SUM(fSUM) as MonthlyPayments
            FROM PAYMENTS 
            WHERE fDATE >= DATEADD(MONTH, -1, GETDATE())
            GROUP BY fCUSTOMERID
        ) pay ON c.fID = pay.fCUSTOMERID
        WHERE ISNULL(debt.TotalDebt, 0) > 100000
            {combined_filter}
        ORDER BY debt.TotalDebt DESC
        """
        
        cursor.execute(query_high_debt, combined_params)
        for row in cursor.fetchall():
            debt = float(row.Debt) if row.Debt else 0
            sales = float(row.MonthlySales) if row.MonthlySales else 0
            payments = float(row.MonthlyPayments) if row.MonthlyPayments else 0
            
            # Высокий долг: долг > 30% от продаж или долг > 500,000 при отсутствии продаж
            if sales > 0:
                debt_ratio = (debt / sales) * 100
                if debt_ratio > 30:
                    problem_id += 1
                    severity = 'critical' if debt_ratio > 100 else ('warning' if debt_ratio > 50 else 'info')
                    problems['highDebt'].append({
                        'id': problem_id,
                        'customerCode': row.CustomerCode,
                        'customerName': row.CustomerName,
                        'areaCode': row.AreaCode or 'N/A',
                        'areaName': row.AreaName or 'Не указана',
                        'problemType': 'Высокий долг',
                        'description': f'Долг составляет {debt_ratio:.0f}% от месячных продаж',
                        'amount': debt,
                        'severity': severity,
                        'recommendation': 'Связаться с клиентом для согласования графика погашения. При долге >100% рассмотреть ограничение отгрузок.',
                        'metrics': {
                            'sales': sales,
                            'payments': payments,
                            'debt': debt,
                            'paymentRate': (payments / sales * 100) if sales > 0 else 0
                        }
                    })
            elif debt > 500000:
                problem_id += 1
                problems['highDebt'].append({
                    'id': problem_id,
                    'customerCode': row.CustomerCode,
                    'customerName': row.CustomerName,
                    'areaCode': row.AreaCode or 'N/A',
                    'areaName': row.AreaName or 'Не указана',
                    'problemType': 'Высокий долг без продаж',
                    'description': f'Долг {debt:,.0f} ֏ при отсутствии продаж за месяц',
                    'amount': debt,
                    'severity': 'critical',
                    'recommendation': 'Срочно связаться с клиентом! Возможно прекращение сотрудничества или проблемы с оплатой.',
                    'metrics': {
                        'sales': 0,
                        'payments': payments,
                        'debt': debt,
                        'paymentRate': 0
                    }
                })
        
        # 2. НИЗКИЙ УРОВЕНЬ ПЛАТЕЖЕЙ (платежи < 50% от продаж)
        query_low_payment = f"""
        SELECT 
            c.fID,
            c.fCODE as CustomerCode,
            c.fNAME as CustomerName,
            ISNULL(csa.fSALESAREA, 'N/A') as AreaCode,
            ISNULL(sa.fCAPTION, 'Не указана') as AreaName,
            ISNULL(sales.MonthlySales, 0) as MonthlySales,
            ISNULL(pay.MonthlyPayments, 0) as MonthlyPayments
        FROM CUSTOMERS c
        LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        LEFT JOIN TREES sa ON csa.fSALESAREA = sa.fCODE AND sa.fTREEID = 'SArea'
        LEFT JOIN (
            SELECT fCUSTOMERID, SUM(fTOTALSUM) as MonthlySales
            FROM SALES 
            WHERE fDATE >= DATEADD(MONTH, -1, GETDATE()) AND fSTATE = 2
            GROUP BY fCUSTOMERID
        ) sales ON c.fID = sales.fCUSTOMERID
        LEFT JOIN (
            SELECT fCUSTOMERID, SUM(fSUM) as MonthlyPayments
            FROM PAYMENTS 
            WHERE fDATE >= DATEADD(MONTH, -1, GETDATE())
            GROUP BY fCUSTOMERID
        ) pay ON c.fID = pay.fCUSTOMERID
        WHERE ISNULL(sales.MonthlySales, 0) > 100000
            {combined_filter}
        ORDER BY sales.MonthlySales DESC
        """
        
        cursor.execute(query_low_payment, combined_params)
        for row in cursor.fetchall():
            sales = float(row.MonthlySales) if row.MonthlySales else 0
            payments = float(row.MonthlyPayments) if row.MonthlyPayments else 0
            
            if sales > 0:
                payment_rate = (payments / sales) * 100
                if payment_rate < 50:
                    problem_id += 1
                    severity = 'critical' if payment_rate < 20 else ('warning' if payment_rate < 35 else 'info')
                    problems['lowPayment'].append({
                        'id': problem_id,
                        'customerCode': row.CustomerCode,
                        'customerName': row.CustomerName,
                        'areaCode': row.AreaCode or 'N/A',
                        'areaName': row.AreaName or 'Не указана',
                        'problemType': 'Низкий уровень платежей',
                        'description': f'Оплачено только {payment_rate:.0f}% от продаж за месяц',
                        'amount': sales - payments,
                        'severity': severity,
                        'recommendation': 'Напомнить клиенту о необходимости оплаты. Рассмотреть изменение условий отсрочки.',
                        'metrics': {
                            'sales': sales,
                            'payments': payments,
                            'debt': sales - payments,
                            'paymentRate': payment_rate
                        }
                    })
        
        # 3. ПАДЕНИЕ ПРОДАЖ (продажи упали более чем на 30% по сравнению со средним)
        query_sales_drop = f"""
        SELECT 
            c.fID,
            c.fCODE as CustomerCode,
            c.fNAME as CustomerName,
            ISNULL(csa.fSALESAREA, 'N/A') as AreaCode,
            ISNULL(sa.fCAPTION, 'Не указана') as AreaName,
            ISNULL(curr.CurrentSales, 0) as CurrentMonthSales,
            ISNULL(prev.AvgPrevSales, 0) as AvgPrevMonthsSales
        FROM CUSTOMERS c
        LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        LEFT JOIN TREES sa ON csa.fSALESAREA = sa.fCODE AND sa.fTREEID = 'SArea'
        LEFT JOIN (
            SELECT fCUSTOMERID, SUM(fTOTALSUM) as CurrentSales
            FROM SALES 
            WHERE fDATE >= DATEADD(MONTH, -1, GETDATE()) AND fSTATE = 2
            GROUP BY fCUSTOMERID
        ) curr ON c.fID = curr.fCUSTOMERID
        LEFT JOIN (
            SELECT fCUSTOMERID, SUM(fTOTALSUM) / 3.0 as AvgPrevSales
            FROM SALES 
            WHERE fDATE >= DATEADD(MONTH, -4, GETDATE())
              AND fDATE < DATEADD(MONTH, -1, GETDATE())
              AND fSTATE = 2
            GROUP BY fCUSTOMERID
        ) prev ON c.fID = prev.fCUSTOMERID
        WHERE ISNULL(prev.AvgPrevSales, 0) > 100000
            {combined_filter}
        """
        
        cursor.execute(query_sales_drop, combined_params)
        for row in cursor.fetchall():
            current = float(row.CurrentMonthSales) if row.CurrentMonthSales else 0
            avg_prev = float(row.AvgPrevMonthsSales) if row.AvgPrevMonthsSales else 0
            
            if avg_prev > 100000:  # Только для клиентов со средними продажами > 100k
                if current < avg_prev * 0.7:  # Падение более 30%
                    drop_percent = ((avg_prev - current) / avg_prev) * 100
                    problem_id += 1
                    severity = 'critical' if drop_percent > 70 else ('warning' if drop_percent > 50 else 'info')
                    problems['salesDrop'].append({
                        'id': problem_id,
                        'customerCode': row.CustomerCode,
                        'customerName': row.CustomerName,
                        'areaCode': row.AreaCode or 'N/A',
                        'areaName': row.AreaName or 'Не указана',
                        'problemType': 'Падение продаж',
                        'description': f'Продажи упали на {drop_percent:.0f}% (было {avg_prev:,.0f}, стало {current:,.0f})',
                        'amount': avg_prev - current,
                        'severity': severity,
                        'recommendation': 'Выяснить причину снижения активности. Возможно клиент перешел к конкуренту или изменились потребности.',
                        'metrics': {
                            'sales': current,
                            'payments': 0,
                            'debt': 0,
                            'paymentRate': 0
                        }
                    })
        
        # 4. НЕАКТИВНЫЕ КЛИЕНТЫ (нет продаж более 30 дней)
        query_inactive = f"""
        SELECT 
            c.fID,
            c.fCODE as CustomerCode,
            c.fNAME as CustomerName,
            ISNULL(csa.fSALESAREA, 'N/A') as AreaCode,
            ISNULL(sa.fCAPTION, 'Не указана') as AreaName,
            last_sale.LastSaleDate,
            DATEDIFF(DAY, last_sale.LastSaleDate, GETDATE()) as DaysSinceLastSale,
            ISNULL(avg_sales.AvgMonthlySales, 0) as AvgMonthlySales
        FROM CUSTOMERS c
        LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        LEFT JOIN TREES sa ON csa.fSALESAREA = sa.fCODE AND sa.fTREEID = 'SArea'
        INNER JOIN (
            SELECT fCUSTOMERID, MAX(fDATE) as LastSaleDate
            FROM SALES 
            WHERE fSTATE = 2
            GROUP BY fCUSTOMERID
        ) last_sale ON c.fID = last_sale.fCUSTOMERID
        LEFT JOIN (
            SELECT fCUSTOMERID, SUM(fTOTALSUM) / 5.0 as AvgMonthlySales
            FROM SALES 
            WHERE fDATE >= DATEADD(MONTH, -6, GETDATE())
              AND fDATE < DATEADD(MONTH, -1, GETDATE())
              AND fSTATE = 2
            GROUP BY fCUSTOMERID
        ) avg_sales ON c.fID = avg_sales.fCUSTOMERID
        WHERE DATEDIFF(DAY, last_sale.LastSaleDate, GETDATE()) > 30
          AND ISNULL(avg_sales.AvgMonthlySales, 0) > 50000
            {combined_filter}
        ORDER BY DaysSinceLastSale DESC
        """
        
        cursor.execute(query_inactive, combined_params)
        for row in cursor.fetchall():
            days = int(row.DaysSinceLastSale) if row.DaysSinceLastSale else 0
            avg_sales = float(row.AvgMonthlySales) if row.AvgMonthlySales else 0
            
            problem_id += 1
            severity = 'critical' if days > 60 else ('warning' if days > 45 else 'info')
            problems['inactive'].append({
                'id': problem_id,
                'customerCode': row.CustomerCode,
                'customerName': row.CustomerName,
                'areaCode': row.AreaCode or 'N/A',
                'areaName': row.AreaName or 'Не указана',
                'problemType': 'Неактивный клиент',
                'description': f'Нет продаж {days} дней (средние продажи были {avg_sales:,.0f} ֏/мес)',
                'amount': avg_sales,
                'severity': severity,
                'recommendation': 'Связаться с клиентом для выяснения причин. Предложить специальные условия для возобновления сотрудничества.',
                'metrics': {
                    'sales': 0,
                    'payments': 0,
                    'debt': 0,
                    'paymentRate': 0
                }
            })
        
        # Подсчет статистики
        all_problems = (
            problems['highDebt'] + 
            problems['lowPayment'] + 
            problems['salesDrop'] + 
            problems['inactive'] + 
            problems['irregular']
        )
        
        stats = {
            'critical': len([p for p in all_problems if p['severity'] == 'critical']),
            'warnings': len([p for p in all_problems if p['severity'] == 'warning']),
            'attention': len([p for p in all_problems if p['severity'] == 'info']),
            'totalCustomers': len(set([p['customerCode'] for p in all_problems]))
        }
        
        # Статистика по территориям
        area_stats_dict = {}
        for p in all_problems:
            area = p['areaCode']
            if area not in area_stats_dict:
                area_stats_dict[area] = {
                    'code': area,
                    'name': p['areaName'],
                    'critical': 0,
                    'warnings': 0,
                    'attention': 0
                }
            if p['severity'] == 'critical':
                area_stats_dict[area]['critical'] += 1
            elif p['severity'] == 'warning':
                area_stats_dict[area]['warnings'] += 1
            else:
                area_stats_dict[area]['attention'] += 1
        
        area_stats = sorted(area_stats_dict.values(), key=lambda x: x['critical'] + x['warnings'], reverse=True)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'problems': problems,
            'stats': stats,
            'areaStats': area_stats
        })
        
    except Exception as e:
        logger.error(f"Error in AI analysis: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================
# CLAUDE AI CHAT ENDPOINT
# =============================================

@app.route('/api/ai-chat', methods=['POST'])
def ai_chat():
    """Chat with Claude AI about sales data and problems"""
    try:
        if not ANTHROPIC_API_KEY:
            return jsonify({
                'success': False, 
                'error': 'ANTHROPIC_API_KEY not configured. Please add it to .env file.'
            }), 400
        
        data = request.get_json()
        user_message = data.get('message', '')
        context_data = data.get('context', {})
        
        if not user_message:
            return jsonify({'success': False, 'error': 'Message is required'}), 400
        
        # Build context about the sales data
        system_prompt = """Ты - AI помощник для анализа данных о продажах. Ты работаешь с Sales Dashboard - системой аналитики продаж.

Твои возможности:
1. Анализ проблем с клиентами (долги, низкие платежи, падение продаж)
2. Рекомендации по работе с проблемными клиентами
3. Анализ территорий и менеджеров
4. Прогнозирование и планирование

Отвечай на русском языке. Будь конкретен и давай практичные советы.
Используй данные, которые тебе предоставляют, для формирования ответов.

Формат ответа: структурированный, с выделением ключевых моментов."""

        # Add context if provided
        if context_data:
            context_str = f"\n\nТекущий контекст данных:\n{json.dumps(context_data, ensure_ascii=False, indent=2)}"
            system_prompt += context_str
        
        # Create Anthropic client
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        
        # Call Claude API
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )
        
        # Extract response
        response_text = message.content[0].text
        
        return jsonify({
            'success': True,
            'response': response_text,
            'model': message.model,
            'usage': {
                'input_tokens': message.usage.input_tokens,
                'output_tokens': message.usage.output_tokens
            }
        })
        
    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        return jsonify({'success': False, 'error': f'Claude API error: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Error in AI chat: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai-analyze-customer', methods=['POST'])
def ai_analyze_customer():
    """Get AI analysis and recommendations for a specific customer"""
    try:
        if not ANTHROPIC_API_KEY:
            return jsonify({
                'success': False, 
                'error': 'ANTHROPIC_API_KEY not configured'
            }), 400
        
        data = request.get_json()
        customer_data = data.get('customer', {})
        
        if not customer_data:
            return jsonify({'success': False, 'error': 'Customer data is required'}), 400
        
        # Build prompt for customer analysis
        prompt = f"""Проанализируй данные клиента и дай рекомендации:

Клиент: {customer_data.get('customerName', 'Неизвестно')}
Код: {customer_data.get('customerCode', 'N/A')}
Территория: {customer_data.get('areaName', 'Не указана')}

Метрики:
- Продажи за месяц: {customer_data.get('metrics', {}).get('sales', 0):,.0f} ֏
- Платежи за месяц: {customer_data.get('metrics', {}).get('payments', 0):,.0f} ֏
- Текущий долг: {customer_data.get('metrics', {}).get('debt', 0):,.0f} ֏
- Процент оплаты: {customer_data.get('metrics', {}).get('paymentRate', 0):.1f}%

Проблема: {customer_data.get('problemType', 'Не указана')}
Описание: {customer_data.get('description', '')}

Дай:
1. Краткий анализ ситуации (2-3 предложения)
2. Конкретные рекомендации по работе с клиентом (3-5 пунктов)
3. Возможные риски если не принять меры
4. Приоритет действий (высокий/средний/низкий)"""

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system="Ты - эксперт по управлению дебиторской задолженностью и работе с клиентами. Давай практичные и конкретные рекомендации на русском языке.",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        return jsonify({
            'success': True,
            'analysis': message.content[0].text,
            'customer': customer_data.get('customerCode')
        })
        
    except Exception as e:
        logger.error(f"Error in AI customer analysis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai-assistant/groups')
def get_ai_assistant_groups():
    """Получить выбранные группы клиентов для AI Assistant"""
    try:
        selected_groups = load_ai_selected_groups()
        return jsonify({
            'success': True,
            'data': selected_groups
        })
    except Exception as e:
        logger.error(f"Error getting AI groups: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ai-assistant/groups', methods=['POST'])
def save_ai_assistant_groups():
    """Сохранить выбранные группы клиентов для AI Assistant"""
    try:
        data = request.get_json()
        groups = data.get('groups', [])
        
        if save_ai_selected_groups(groups):
            app.logger.info(f"[AIGroups] Saved {len(groups)} groups: {groups}")
            return jsonify({
                'success': True,
                'message': f'Сохранено {len(groups)} групп'
            })
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'}), 500
    except Exception as e:
        logger.error(f"Error saving AI groups: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai-assistant/settings')
def get_ai_assistant_settings():
    """Получить настройки AI анализа"""
    try:
        settings = load_ai_analysis_settings()
        return jsonify({
            'success': True,
            'data': settings
        })
    except Exception as e:
        logger.error(f"Error getting AI settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ai-assistant/settings', methods=['POST'])
def save_ai_assistant_settings():
    """Сохранить настройки AI анализа"""
    try:
        data = request.get_json()
        if save_ai_analysis_settings(data):
            app.logger.info(f"[AISettings] Saved settings: {data}")
            return jsonify({
                'success': True,
                'message': 'Настройки сохранены'
            })
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'}), 500
    except Exception as e:
        logger.error(f"Error saving AI settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai-assistant/areas')
def get_ai_assistant_areas():
    """Получить выбранные территории для AI Assistant"""
    try:
        selected_areas = load_ai_selected_areas()
        return jsonify({
            'success': True,
            'data': selected_areas
        })
    except Exception as e:
        logger.error(f"Error getting AI areas: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ai-assistant/areas', methods=['POST'])
def save_ai_assistant_areas():
    """Сохранить выбранные территории для AI Assistant"""
    try:
        data = request.get_json()
        areas = data.get('areas', [])
        
        if save_ai_selected_areas(areas):
            app.logger.info(f"[AIAreas] Saved {len(areas)} areas: {areas}")
            return jsonify({
                'success': True,
                'message': f'Сохранено {len(areas)} территорий'
            })
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'}), 500
    except Exception as e:
        logger.error(f"Error saving AI areas: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sales-areas-list')
def get_sales_areas_list():
    """Получить список территорий (только код и имя)"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT fCODE, fCAPTION 
            FROM TREES 
            WHERE fTREEID = 'SArea' 
            ORDER BY fCODE
        """)
        
        areas = []
        for row in cursor.fetchall():
            areas.append({
                'code': row[0].strip() if row[0] else '',
                'name': row[1].strip() if row[1] else ''
            })
        
        conn.close()
        
        return jsonify({
            'success': True,
            'data': areas
        })
    except Exception as e:
        logger.error(f"Error getting sales areas list: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sales-areas-hierarchy')
def get_sales_areas_hierarchy():
    """Получить иерархический список территорий (группы и подгруппы)"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT fCODE, fCAPTION, fPARENT, fCLOSED
            FROM TREES 
            WHERE fTREEID = 'SArea' 
            ORDER BY fCODE
        """)
        
        all_areas = []
        for row in cursor.fetchall():
            all_areas.append({
                'code': row[0].strip() if row[0] else '',
                'name': row[1].strip() if row[1] else '',
                'parent': row[2].strip() if row[2] else '',
                'closed': row[3] if row[3] else 0
            })
        
        conn.close()
        
        # Build hierarchy - separate parents and children
        parents = []
        children_map = {}
        
        for area in all_areas:
            if area['closed'] == 1:
                continue  # Skip closed areas
            if area['parent'] == '':
                # This is a parent group
                parents.append({
                    'code': area['code'],
                    'name': area['name'],
                    'children': []
                })
            else:
                # This is a child
                parent_code = area['parent']
                if parent_code not in children_map:
                    children_map[parent_code] = []
                children_map[parent_code].append({
                    'code': area['code'],
                    'name': area['name']
                })
        
        # Assign children to parents
        for parent in parents:
            parent['children'] = children_map.get(parent['code'], [])
        
        # Sort parents by code
        parents.sort(key=lambda x: x['code'])
        
        return jsonify({
            'success': True,
            'data': parents
        })
    except Exception as e:
        logger.error(f"Error getting sales areas hierarchy: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/areas', methods=['GET'])
def get_dashboard_areas():
    """Получить выбранные территории для Dashboard"""
    try:
        selected = load_dashboard_selected_areas()
        return jsonify({
            'success': True,
            'data': selected
        })
    except Exception as e:
        logger.error(f"Error getting dashboard areas: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/areas', methods=['POST'])
def save_dashboard_areas():
    """Сохранить выбранные территории для Dashboard"""
    try:
        data = request.get_json()
        areas = data.get('areas', [])
        
        if save_dashboard_selected_areas(areas):
            return jsonify({
                'success': True,
                'message': f'Сохранено {len(areas)} территорий'
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save'}), 500
    except Exception as e:
        logger.error(f"Error saving dashboard areas: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/groups', methods=['GET'])
def get_dashboard_groups():
    """Получить выбранные группы клиентов для Dashboard"""
    try:
        selected = load_dashboard_selected_groups()
        return jsonify({
            'success': True,
            'groups': selected
        })
    except Exception as e:
        logger.error(f"Error getting dashboard groups: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/groups', methods=['POST'])
def save_dashboard_groups():
    """Сохранить выбранные группы клиентов для Dashboard"""
    try:
        data = request.get_json()
        groups = data.get('groups', [])
        
        if save_dashboard_selected_groups(groups):
            return jsonify({
                'success': True,
                'message': f'Сохранено {len(groups)} групп'
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save'}), 500
    except Exception as e:
        logger.error(f"Error saving dashboard groups: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/widgets', methods=['GET'])
def get_dashboard_widgets():
    """Получить настройки виджетов Dashboard"""
    try:
        widgets = load_dashboard_widgets()
        return jsonify({
            'success': True,
            'widgets': widgets
        })
    except Exception as e:
        logger.error(f"Error getting dashboard widgets: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/widgets', methods=['POST'])
def save_dashboard_widgets_api():
    """Сохранить настройки виджетов Dashboard"""
    try:
        data = request.get_json()
        widgets = data.get('widgets', [])
        
        if save_dashboard_widgets(widgets):
            return jsonify({
                'success': True,
                'message': f'Сохранено {len(widgets)} виджетов'
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save'}), 500
    except Exception as e:
        logger.error(f"Error saving dashboard widgets: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard/widgets/reset', methods=['POST'])
def reset_dashboard_widgets():
    """Сбросить настройки виджетов к дефолтным"""
    try:
        if save_dashboard_widgets(DEFAULT_DASHBOARD_WIDGETS.copy()):
            return jsonify({
                'success': True,
                'widgets': DEFAULT_DASHBOARD_WIDGETS
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to reset'}), 500
    except Exception as e:
        logger.error(f"Error resetting dashboard widgets: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/area-seasonality')
def get_area_seasonality():
    """Get calculated seasonality profiles for all areas based on history"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получить параметры фильтров
        raw_groups = request.args.get('groups', '').strip()
        selected_groups = [grp.strip() for grp in raw_groups.split(',') if grp.strip()]
        
        # Фильтры исключенных клиентов
        excluded_filter, excluded_params = get_excluded_filter_sql()
        product_groups_filter, product_groups_params = get_product_groups_filter_sql()
        
        # Фильтр по группам клиентов
        group_clause = ""
        group_params = tuple()
        if selected_groups:
            placeholders = ','.join('?' * len(selected_groups))
            group_clause = f" AND c.fGROUP IN ({placeholders})"
            group_params = tuple(selected_groups)

        # Берём данные за последние 24 ПОЛНЫХ месяца (без частичного текущего),
        # чтобы каждый месяц входил в окно ровно 2 раза и не искажал сезонность.
        query_seasonality_calc = f"""
        SELECT
            csa.fSALESAREA as area_code,
            MONTH(s.fDATE) as month,
            SUM(s.fTOTALSUM) as sales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE s.fDATE >= DATEADD(MONTH, -24, {CURRENT_MONTH_START_SQL})
            AND s.fDATE < {CURRENT_MONTH_START_SQL}
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
            {group_clause}
        GROUP BY csa.fSALESAREA, MONTH(s.fDATE)
        """

        season_params = excluded_params + product_groups_params + group_params
        cursor.execute(query_seasonality_calc, season_params)
        season_rows = cursor.fetchall()

        # Структура: area_code -> { month -> sales }
        area_monthly_sales = {}
        for row in season_rows:
            if row.area_code not in area_monthly_sales:
                area_monthly_sales[row.area_code] = {}
            area_monthly_sales[row.area_code][row.month] = float(row.sales)

        # Рассчитываем коэффициенты
        calculated_seasonality = {} # area_code -> { month -> coeff }

        for area_code, months_data in area_monthly_sales.items():
            total_sales = sum(months_data.values())
            if total_sales > 0:
                calculated_seasonality[area_code] = {}
                for m in range(1, 13):
                    m_sales = months_data.get(m, 0)
                    coeff = (m_sales * 12) / total_sales
                    calculated_seasonality[area_code][m] = round(coeff, 2)
        
        logger.info(f"Calculated Seasonality for {len(calculated_seasonality)} areas: {json.dumps(calculated_seasonality)}")
        return jsonify({'success': True, 'data': calculated_seasonality})
        
    except Exception as e:
        logger.error(f"Error calculating seasonality: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/generate-plans', methods=['GET', 'POST'])
def generate_plans():
    """Генерация планов продаж и кредитов с учетом сезонности"""
    try:
        growth_map = {}
        
        if request.method == 'POST':
            data = request.json
            target_month = int(data.get('month', datetime.now().month))
            target_year = int(data.get('year', datetime.now().year))
            growth_percent = float(data.get('growth', 10))
            growth_map = data.get('growth_map', {})
            seasonality_map = data.get('seasonality_map', {})
            
            raw_groups = data.get('groups', [])
            if isinstance(raw_groups, str):
                 selected_groups = [grp.strip() for grp in raw_groups.split(',') if grp.strip()]
            else:
                 selected_groups = raw_groups
            
            # Separate groups for debt calculations
            raw_debt_groups = data.get('debt_groups', [])
            if isinstance(raw_debt_groups, str):
                 selected_debt_groups = [grp.strip() for grp in raw_debt_groups.split(',') if grp.strip()]
            else:
                 selected_debt_groups = raw_debt_groups
        else:
            target_month = int(request.args.get('month', datetime.now().month))
            target_year = int(request.args.get('year', datetime.now().year))
            growth_percent = float(request.args.get('growth', 10))  # Параметр роста из запроса
            seasonality_map = {}
            
            # Получить параметры фильтров
            raw_groups = request.args.get('groups', '').strip()
            selected_groups = [grp.strip() for grp in raw_groups.split(',') if grp.strip()]
            
            # Separate groups for debt calculations
            raw_debt_groups = request.args.get('debt_groups', '').strip()
            selected_debt_groups = [grp.strip() for grp in raw_debt_groups.split(',') if grp.strip()]
        
        # Коэффициенты сезонности (на основе анализа данных)
        # Синхронизировано с frontend (templates/plans.html)
        global_seasonality = {
            1: 0.53, 2: 0.67, 3: 0.80, 4: 0.86,
            5: 1.14, 6: 1.31, 7: 1.49, 8: 1.43,
            9: 1.10, 10: 1.02, 11: 0.88, 12: 0.93
        }
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Фильтры исключенных клиентов
        excluded_filter, excluded_params = get_excluded_filter_sql()
        product_groups_filter, product_groups_params = get_product_groups_filter_sql()
        
        # Фильтр по группам клиентов для ПРОДАЖ
        group_clause = ""
        group_params = tuple()
        if selected_groups:
            placeholders = ','.join('?' * len(selected_groups))
            group_clause = f" AND c.fGROUP IN ({placeholders})"
            group_params = tuple(selected_groups)
        
        # Фильтр по группам клиентов для ДОЛГОВ (отдельный)
        debt_group_clause = ""
        debt_group_params = tuple()
        if selected_debt_groups:
            placeholders = ','.join('?' * len(selected_debt_groups))
            debt_group_clause = f" AND c.fGROUP IN ({placeholders})"
            debt_group_params = tuple(selected_debt_groups)
        elif selected_groups:
            # Fallback: если debt_groups не указаны, используем groups
            debt_group_clause = group_clause
            debt_group_params = group_params

        # 0. Расчет индивидуальной сезонности для каждой территории
        # Берём данные за последние 24 ПОЛНЫХ месяца (без частичного текущего):
        # каждый месяц входит в окно ровно 2 раза, поэтому деление на 12 даёт
        # среднемесячную долю без перекоса в пользу текущего месяца.
        query_seasonality_calc = f"""
        SELECT
            csa.fSALESAREA as area_code,
            MONTH(s.fDATE) as month,
            SUM(s.fTOTALSUM) as sales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE s.fDATE >= DATEADD(MONTH, -24, {CURRENT_MONTH_START_SQL})
            AND s.fDATE < {CURRENT_MONTH_START_SQL}
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
            {group_clause}
        GROUP BY csa.fSALESAREA, MONTH(s.fDATE)
        """
        
        season_params = excluded_params + product_groups_params + group_params
        cursor.execute(query_seasonality_calc, season_params)
        season_rows = cursor.fetchall()
        
        # Структура: area_code -> { month -> sales }
        area_monthly_sales = {}
        for row in season_rows:
            if row.area_code not in area_monthly_sales:
                area_monthly_sales[row.area_code] = {}
            area_monthly_sales[row.area_code][row.month] = float(row.sales)
            
        # Рассчитываем коэффициенты
        calculated_seasonality = {} # area_code -> { month -> coeff }
        
        for area_code, months_data in area_monthly_sales.items():
            total_sales = sum(months_data.values())
            if total_sales > 0:
                calculated_seasonality[area_code] = {}
                # Окно = ровно 24 полных месяца => каждый месяц входит 2 раза.
                # Коэффициент месяца = (продажи месяца / число лет) / среднемесячные
                #                    = (m_sales * 12) / total_sales.
                # Округляем до 2 знаков — так же, как /api/area-seasonality и как
                # значение, которое фронтенд показывает и возвращает при POST, чтобы
                # авто-расчёт (GET) и кнопка «Сгенерировать» (POST) совпадали.
                for m in range(1, 13):
                    m_sales = months_data.get(m, 0)
                    coeff = (m_sales * 12) / total_sales
                    calculated_seasonality[area_code][m] = round(coeff, 2)
        
        # 1. Средние месячные продажи за последние 12 ПОЛНЫХ месяцев по территориям.
        # Окно [начало месяца −12 ; начало текущего месяца) содержит ровно 12
        # завершённых месяцев, поэтому деление на 12.0 даёт корректное среднее
        # (частичный текущий месяц не занижает/не искажает базу).
        query_sales = f"""
        SELECT
            csa.fSALESAREA as area_code,
            ISNULL(SUM(s.fTOTALSUM), 0) / 12.0 as avg_monthly_sales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE s.fDATE >= DATEADD(MONTH, -12, {CURRENT_MONTH_START_SQL})
            AND s.fDATE < {CURRENT_MONTH_START_SQL}
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
            {group_clause}
        GROUP BY csa.fSALESAREA
        """
        
        sales_params = excluded_params + product_groups_params + group_params
        cursor.execute(query_sales, sales_params)
        sales_results = cursor.fetchall()
        
        # 2. Получить СРЕДНИЙ долг за последние 12 месяцев по Sales Areas
        # ОПТИМИЗИРОВАННЫЙ МЕТОД:
        # 1. Берем текущий баланс (Current Debt)
        # 2. Берем изменения за каждый месяц (Monthly Changes)
        # 3. Восстанавливаем баланс на конец каждого месяца обратным счетом
        
        # 2.1 Текущий долг
        query_current_debt = f"""
        SELECT 
            csa.fSALESAREA as area_code,
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as current_debt
        FROM HICUSTOMERSDEBT d WITH (NOLOCK)
        INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c WITH (NOLOCK) ON doc.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE 1=1
            {excluded_filter}
            {debt_group_clause}
        GROUP BY csa.fSALESAREA
        """
        
        debt_params = excluded_params + debt_group_params
        logger.info(f"[PLAN DEBT] Starting debt calculation (Optimized)")
        cursor.execute(query_current_debt, debt_params)
        current_debt_results = cursor.fetchall()
        
        # 2.2 Изменения по месяцам
        query_changes = f"""
        SELECT 
            csa.fSALESAREA as area_code,
            YEAR(d.fDATE) as year,
            MONTH(d.fDATE) as month,
            ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as net_change
        FROM HICUSTOMERSDEBT d WITH (NOLOCK)
        INNER JOIN DOCUMENTS doc WITH (NOLOCK) ON d.fDEBTDOCISN = doc.fISN
        INNER JOIN CUSTOMERS c WITH (NOLOCK) ON doc.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE d.fDATE >= DATEADD(MONTH, -13, {CURRENT_MONTH_START_SQL})
            {excluded_filter}
            {debt_group_clause}
        GROUP BY csa.fSALESAREA, YEAR(d.fDATE), MONTH(d.fDATE)
        """
        
        cursor.execute(query_changes, debt_params)
        changes_results = cursor.fetchall()
        logger.info(f"[PLAN DEBT] Got {len(current_debt_results)} areas and {len(changes_results)} monthly changes")
        
        # 3. Получить Type01 и Type02 (возвраты и предоплаты) для вычета
        query_rest = f"""
        SELECT 
            csa.fSALESAREA as area_code,
            ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
            ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
        FROM HIRESTCUSTOMERSSUM r WITH (NOLOCK)
        INNER JOIN CUSTOMERS c WITH (NOLOCK) ON r.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE 1=1
            {excluded_filter}
            {debt_group_clause}
        GROUP BY csa.fSALESAREA
        """
        
        rest_params = excluded_params + debt_group_params
        cursor.execute(query_rest, rest_params)
        rest_results = cursor.fetchall()
        
        # Объединяем результаты
        area_stats = {}
        
        for row in sales_results:
            area_stats[row.area_code] = {
                'avg_sales': float(row.avg_monthly_sales) if row.avg_monthly_sales else 0,
                'avg_debt': 0,
                'type01': 0,
                'type02': 0
            }
            
        # Обработка истории долга
        current_debts = {row.area_code: float(row.current_debt) for row in current_debt_results}
        changes_map = {} 
        for row in changes_results:
            if row.area_code not in changes_map: changes_map[row.area_code] = {}
            changes_map[row.area_code][(row.year, row.month)] = float(row.net_change)
            
        # Текущий год/месяц берём из SQL GETDATE(), а НЕ из datetime.now(),
        # чтобы стартовый месяц цикла реконструкции совпадал с окнами запросов
        # (все они привязаны к GETDATE() через CURRENT_MONTH_START_SQL). Иначе
        # при разных таймзонах приложения и SQL Server или в момент смены месяца
        # они могли бы разойтись на месяц.
        cursor.execute("SELECT YEAR(GETDATE()) AS y, MONTH(GETDATE()) AS m")
        _now_row = cursor.fetchone()
        current_year = int(_now_row.y)
        current_month = int(_now_row.m)

        for area_code, current_balance in current_debts.items():
            if area_code not in area_stats:
                area_stats[area_code] = {'avg_sales': 0, 'avg_debt': 0, 'type01': 0, 'type02': 0}

            # Восстанавливаем баланс долга на КОНЕЦ каждого из 12 ЗАВЕРШЁННЫХ
            # месяцев. Идём от текущего баланса назад, вычитая чистое изменение
            # долга за месяц: баланс(конец пред. месяца) = баланс(этот) − Δ(этот).
            # Незавершённый текущий месяц как отдельную точку НЕ включаем — иначе
            # среднее смешивает баланс «на сегодня» с 11 концами месяцев.
            balances = []
            running_balance = current_balance
            curr_y, curr_m = current_year, current_month

            for _ in range(12):
                change = changes_map.get(area_code, {}).get((curr_y, curr_m), 0)
                # После вычитания изменения текущего (curr_y, curr_m) получаем
                # баланс на конец предыдущего месяца — это завершённый месяц.
                running_balance = running_balance - change
                balances.append(running_balance)

                curr_m -= 1
                if curr_m == 0:
                    curr_m = 12
                    curr_y -= 1

            # Среднее по 12 концам завершённых месяцев
            avg_debt = sum(balances) / len(balances)
            area_stats[area_code]['avg_debt'] = avg_debt
        
        for row in rest_results:
            if row.area_code in area_stats:
                area_stats[row.area_code]['type01'] = float(row.Type01) if row.Type01 else 0
                area_stats[row.area_code]['type02'] = float(row.Type02) if row.Type02 else 0
        
        plans = {}
        default_season_coeff = global_seasonality.get(target_month, 1.0)
        # growth_factor = 1 + (growth_percent / 100)  # Moved inside loop
        
        for area_code, stats in area_stats.items():
            avg_sales = stats['avg_sales']
            avg_debt = stats['avg_debt']
            type01 = stats['type01']
            type02 = stats['type02']
            
            # ФОРМУЛА: Средний ДОЛГ = Средний кумулятивный баланс - ВОЗВРАТЫ - ПРЕДОПЛАТА
            avg_debt_adjusted = avg_debt - abs(type01) - abs(type02)
            
            # Determine growth for this area
            try:
                val = growth_map.get(str(area_code), growth_map.get(area_code, growth_percent))
                this_area_growth = float(val)
            except (ValueError, TypeError):
                this_area_growth = growth_percent
                
            growth_factor = 1 + (this_area_growth / 100.0)

            # Determine seasonality for this area
            # 1. Check if user provided explicit override in request (seasonality_map)
            # 2. If not, check if we calculated individual seasonality (calculated_seasonality)
            # 3. Fallback to global default
            
            this_area_seasonality = default_season_coeff
            
            # Check explicit override first
            user_val = seasonality_map.get(str(area_code), seasonality_map.get(area_code))
            if user_val is not None:
                try:
                    this_area_seasonality = float(user_val)
                except (ValueError, TypeError):
                    pass
            else:
                # Use calculated individual seasonality if available.
                # Проверяем 'is not None' (а не truthiness), чтобы коэффициент,
                # округлившийся ровно в 0.00, обрабатывался так же, как явный
                # override в POST (там условие тоже 'is not None') — иначе GET и
                # POST расходились бы на этой границе.
                if area_code in calculated_seasonality:
                    calc_val = calculated_seasonality[area_code].get(target_month)
                    if calc_val is not None:
                        this_area_seasonality = calc_val

            # Применяем сезонный коэффициент и настраиваемый рост
            # Округляем до 10,000
            plan_sales = int(round(avg_sales * this_area_seasonality * growth_factor / 10000) * 10000)
            # План по кредиту = Средний Долг × Сезонность × Рост (округлено до 10,000)
            plan_credit = int(round(avg_debt_adjusted * this_area_seasonality * growth_factor / 10000) * 10000)
            
            plans[area_code] = {
                'sales': plan_sales,
                'credit': plan_credit,
                'seasonality': this_area_seasonality,
                'avg_sales': round(avg_sales, 0),
                'avg_credit': round(avg_debt_adjusted, 0),  # Средний долг за 12 месяцев
                'calculated_seasonality': calculated_seasonality.get(area_code, {}) # Send full year profile for graph
            }
        
        conn.close()
        
        return jsonify({
            'success': True,
            'data': plans,
            'month': target_month,
            'year': target_year,
            'seasonality_coefficient': default_season_coeff
        })
        
    except Exception as e:
        logger.error(f"Ошибка генерации планов: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/calculate-seasonality')
def calculate_seasonality_api():
    """Рассчитать коэффициенты сезонности на основе исторических данных"""
    try:
        history_years = int(request.args.get('years', 2))
        
        # Получить параметры фильтров
        raw_groups = request.args.get('groups', '').strip()
        selected_groups = [grp.strip() for grp in raw_groups.split(',') if grp.strip()]
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Фильтры исключенных клиентов
        excluded_filter, excluded_params = get_excluded_filter_sql()
        product_groups_filter, product_groups_params = get_product_groups_filter_sql()
        
        # Фильтр по группам клиентов
        group_clause = ""
        group_params = tuple()
        if selected_groups:
            placeholders = ','.join('?' * len(selected_groups))
            group_clause = f" AND c.fGROUP IN ({placeholders})"
            group_params = tuple(selected_groups)
        
        # Продажи по месяцам за N ПОЛНЫХ лет (без частичного текущего месяца):
        # окно [начало месяца −N лет ; начало текущего месяца) содержит каждый
        # календарный месяц ровно N раз, поэтому нормировка коэффициентов не имеет
        # перекоса в пользу текущего месяца.
        query = f"""
        SELECT
            MONTH(s.fDATE) as month_num,
            ISNULL(SUM(s.fTOTALSUM), 0) as total_sales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        WHERE s.fDATE >= DATEADD(YEAR, -{history_years}, {CURRENT_MONTH_START_SQL})
            AND s.fDATE < {CURRENT_MONTH_START_SQL}
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
            {group_clause}
        GROUP BY MONTH(s.fDATE)
        ORDER BY MONTH(s.fDATE)
        """
        
        params = excluded_params + product_groups_params + group_params
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        # Рассчитать среднемесячные продажи
        monthly_sales = {}
        for row in results:
            month_num = row[0]
            total_sales = row[1]
            monthly_sales[month_num] = total_sales
        
        # Если нет данных, вернуть дефолтные коэффициенты
        if not monthly_sales:
            cursor.close()
            conn.close()
            return jsonify({
                'success': True,
                'seasonality': {
                    1: 0.53, 2: 0.67, 3: 0.80, 4: 0.86,
                    5: 1.14, 6: 1.31, 7: 1.49, 8: 1.43,
                    9: 1.10, 10: 1.02, 11: 0.88, 12: 0.93
                },
                'years': history_years,
                'message': 'Нет данных за указанный период, используются дефолтные коэффициенты'
            })
        
        # Суммарные продажи за всё окно (N полных лет).
        total_sum = float(sum(float(v) for v in monthly_sales.values()))

        # Коэффициент месяца = (продажи месяца * 12) / всего.
        # Так как каждый месяц входит в окно ровно N раз, это эквивалентно
        # (среднемесячные продажи месяца) / (общие среднемесячные) и надёжно даже
        # если у какого-то месяца нет продаж (тогда его коэффициент = 0).
        seasonality_coeffs = {}
        for month in range(1, 13):
            if total_sum > 0:
                coeff = (float(monthly_sales.get(month, 0)) * 12) / total_sum
                seasonality_coeffs[month] = round(coeff, 2)
            else:
                # Совсем нет данных — нейтральный средний уровень
                seasonality_coeffs[month] = 1.0
        
        cursor.close()
        conn.close()
        
        logger.info(f"Рассчитаны коэффициенты сезонности за {history_years} лет: {seasonality_coeffs}")
        
        # Среднемесячные продажи за окно: всего / (N лет * 12 месяцев)
        average_monthly = total_sum / (history_years * 12) if history_years > 0 else 0

        return jsonify({
            'success': True,
            'seasonality': seasonality_coeffs,
            'years': history_years,
            'average': round(average_monthly, 2)
        })
        
    except Exception as e:
        logger.error(f"Ошибка расчета сезонности: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/customers-grid')
def customers_grid_page():
    """Страница клиентов с AG Grid (DevExpress-style)"""
    return render_template('customers_aggrid.html')

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
        # ПРАВИЛЬНАЯ ФОРМУЛА: Дебет (D) добавляется, Кредит (C) вычитается
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
        type02 = float(rest_result[0]['Type02']) if rest_result and rest_result[0]['Type02'] is not None else 0
        
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
    """Получить список менеджеров с продажами за последние 2 месяца"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Вычислить дату 2 месяца назад
        two_months_ago = datetime.now() - timedelta(days=60)
        date_filter = two_months_ago.strftime('%Y-%m-%d')
        
        query = """
            SELECT DISTINCT sa.fID, sa.fCODE, sa.fNAME
            FROM SALESAGENTS sa
            INNER JOIN SALES s ON s.fSALESAGENTID = sa.fID
            WHERE s.fDATE >= ? 
              AND s.fSTATE = 2
              AND sa.fCLOSED = 0
            ORDER BY sa.fNAME
        """
        cursor.execute(query, (date_filter,))
        
        managers = []
        for row in cursor.fetchall():
            managers.append({
                'fID': row.fID,
                'fCODE': row.fCODE,
                'fNAME': row.fNAME,
                'storesCount': 0  # Будет заполнено позже
            })
        
        conn.close()
        app.logger.info(f"[Settings] Loaded {len(managers)} active managers (last 2 months)")
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
    """Получить список всех групп клиентов с названиями и родителями из TREES"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Сначала получаем все группы из CUSTOMERS
        query_groups = """
            SELECT DISTINCT fGROUP
            FROM CUSTOMERS
            WHERE fGROUP IS NOT NULL AND fGROUP != ''
            ORDER BY fGROUP
        """
        cursor.execute(query_groups)
        customer_groups = [row.fGROUP for row in cursor.fetchall()]
        
        # Затем получаем названия и родителей из TREES
        query_trees = """
            SELECT fCODE, fCAPTION, fPARENT
            FROM TREES
            WHERE fTREEID = 'CustGrp'
        """
        cursor.execute(query_trees)
        tree_data = {}
        for row in cursor.fetchall():
            tree_data[row.fCODE] = {
                'name': row.fCAPTION,
                'parent': row.fPARENT
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

# ===== Sales Areas → Groups =====
@app.route('/api/settings/sales-areas/list')
def get_settings_sales_areas_list():
    """Получить список Sales Areas из TREES с количеством назначенных клиентов из CUSTOMERSALESAREAS"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                t.fCODE, 
                t.fCAPTION,
                COUNT(DISTINCT csa.fCUSTOMERID) AS CustomerCount
            FROM TREES t
            LEFT JOIN CUSTOMERSALESAREAS csa ON t.fCODE = csa.fSALESAREA
            WHERE t.fTREEID = 'SArea'
            GROUP BY t.fCODE, t.fCAPTION
            ORDER BY t.fCODE
        """)
        areas = []
        for row in cursor.fetchall():
            areas.append({
                'code': row.fCODE,
                'name': row.fCAPTION,
                'customerCount': row.CustomerCount if row.CustomerCount else 0
            })
        conn.close()
        return jsonify({'success': True, 'data': areas})
    except Exception as e:
        app.logger.error(f"[SalesAreaGroups] Error loading areas: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/sales-areas/groups')
def get_sales_area_group_assignments():
    """Получить текущие назначения групп к Sales Areas"""
    try:
        assignments = load_sales_area_group_assignments()
        return jsonify({'success': True, 'data': assignments})
    except Exception as e:
        app.logger.error(f"[SalesAreaGroups] Error loading assignments: {e}")
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
            app.logger.info(f"[SalesAreaGroups] Updated {area_code}: {len(groups)} groups")
            return jsonify({'success': True, 'data': assignments.get(area_code, [])})
        return jsonify({'success': False, 'error': 'Ошибка сохранения'}), 500
    except Exception as e:
        app.logger.error(f"[SalesAreaGroups] Error saving assignments: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Исключенные клиенты =====
EXCLUDED_CUSTOMERS_FILE = 'excluded_customers.json'
EXCLUDED_GROUPS_FILE = 'excluded_groups.json'
GROUP_MANAGER_ASSIGNMENTS_FILE = 'group_manager_assignments.json'
SELECTED_PRODUCT_GROUPS_FILE = 'selected_product_groups.json'
SALES_AREA_GROUP_ASSIGNMENTS_FILE = 'sales_area_group_assignments.json'
DISTRIBUTOR_GROUPS_FILE = 'distributor_groups.json'
AI_SELECTED_GROUPS_FILE = 'ai_selected_groups.json'
AI_ANALYSIS_SETTINGS_FILE = 'ai_analysis_settings.json'
AI_SELECTED_AREAS_FILE = 'ai_selected_areas.json'
DASHBOARD_SELECTED_AREAS_FILE = 'dashboard_selected_areas.json'
DASHBOARD_SELECTED_GROUPS_FILE = 'dashboard_selected_groups.json'
DASHBOARD_WIDGETS_FILE = 'dashboard_widgets.json'

# Дефолтные виджеты Dashboard
DEFAULT_DASHBOARD_WIDGETS = [
    {'id': 'total_revenue', 'title': 'Общая выручка', 'type': 'stat', 'dataKey': 'total_revenue', 'icon': 'fa-dollar-sign', 'color': '#0d6efd', 'order': 1, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'sales_count', 'title': 'Количество продаж', 'type': 'stat', 'dataKey': 'sales_count', 'icon': 'fa-shopping-cart', 'color': '#198754', 'order': 2, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'avg_check', 'title': 'Средний чек', 'type': 'stat', 'dataKey': 'avg_check', 'icon': 'fa-receipt', 'color': '#0dcaf0', 'order': 3, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'active_customers', 'title': 'Активные клиенты', 'type': 'stat', 'dataKey': 'active_customers', 'icon': 'fa-users', 'color': '#ffc107', 'order': 4, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'today_revenue', 'title': 'Выручка сегодня', 'type': 'stat', 'dataKey': 'today_revenue', 'icon': 'fa-calendar-check', 'color': '#0d6efd', 'order': 5, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'today_sales', 'title': 'Продажи сегодня', 'type': 'stat', 'dataKey': 'today_sales', 'icon': 'fa-shopping-bag', 'color': '#198754', 'order': 6, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'today_avg_check', 'title': 'Средний чек сегодня', 'type': 'stat', 'dataKey': 'today_avg_check', 'icon': 'fa-receipt', 'color': '#0dcaf0', 'order': 7, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'today_customers', 'title': 'Клиенты сегодня', 'type': 'stat', 'dataKey': 'today_customers', 'icon': 'fa-user-check', 'color': '#ffc107', 'order': 8, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'monthly_forecast', 'title': 'Прогноз на месяц', 'type': 'stat', 'dataKey': 'monthly_forecast', 'icon': 'fa-chart-line', 'color': '#6f42c1', 'order': 9, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'total_debt', 'title': 'Общая задолженность', 'type': 'debt', 'dataKey': 'final_debt', 'icon': 'fa-hand-holding-usd', 'color': '#dc3545', 'order': 10, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'top_manager', 'title': 'Лучший менеджер', 'type': 'stat', 'dataKey': 'top_manager', 'icon': 'fa-trophy', 'color': '#fd7e14', 'order': 11, 'visible': True, 'size': 'col-xl-3 col-md-6'},
    {'id': 'sales_chart', 'title': 'График продаж', 'type': 'chart', 'chartType': 'sales', 'icon': 'fa-chart-area', 'color': '#0d6efd', 'order': 12, 'visible': True, 'size': 'col-xl-8 col-md-12'},
    {'id': 'managers_chart', 'title': 'ТОП менеджеры', 'type': 'chart', 'chartType': 'managers', 'icon': 'fa-chart-bar', 'color': '#198754', 'order': 13, 'visible': True, 'size': 'col-xl-4 col-md-12'},
    {'id': 'debts_chart', 'title': 'График долгов', 'type': 'chart', 'chartType': 'debts', 'icon': 'fa-chart-pie', 'color': '#dc3545', 'order': 14, 'visible': True, 'size': 'col-xl-6 col-md-12'},
    {'id': 'ten_years_chart', 'title': 'История 10 лет', 'type': 'chart', 'chartType': 'tenYears', 'icon': 'fa-history', 'color': '#6f42c1', 'order': 15, 'visible': True, 'size': 'col-xl-6 col-md-12'},
]

def load_dashboard_widgets():
    """Загрузить настройки виджетов Dashboard"""
    try:
        if os.path.exists(DASHBOARD_WIDGETS_FILE):
            with open(DASHBOARD_WIDGETS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return DEFAULT_DASHBOARD_WIDGETS.copy()
    except Exception as e:
        app.logger.error(f"[DashboardWidgets] Error loading: {e}")
        return DEFAULT_DASHBOARD_WIDGETS.copy()

def save_dashboard_widgets(widgets):
    """Сохранить настройки виджетов Dashboard"""
    try:
        with open(DASHBOARD_WIDGETS_FILE, 'w', encoding='utf-8') as f:
            json.dump(widgets, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[DashboardWidgets] Error saving: {e}")
        return False

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

def load_distributor_groups():
    """Загрузить список групп-дистрибьюторов"""
    try:
        if os.path.exists(DISTRIBUTOR_GROUPS_FILE):
            with open(DISTRIBUTOR_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        app.logger.error(f"[DistributorGroups] Error loading: {e}")
        return []

def save_distributor_groups(groups_list):
    """Сохранить список групп-дистрибьюторов"""
    try:
        with open(DISTRIBUTOR_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[DistributorGroups] Error saving: {e}")
        return False

def load_ai_selected_groups():
    """Загрузить выбранные группы клиентов для AI Assistant"""
    try:
        if os.path.exists(AI_SELECTED_GROUPS_FILE):
            with open(AI_SELECTED_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []  # Пустой список = все группы
    except Exception as e:
        app.logger.error(f"[AIGroups] Error loading: {e}")
        return []

def save_ai_selected_groups(groups_list):
    """Сохранить выбранные группы клиентов для AI Assistant"""
    try:
        with open(AI_SELECTED_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[AIGroups] Error saving: {e}")
        return False

def get_ai_groups_filter_sql():
    """Получить SQL фильтр для выбранных групп AI Assistant"""
    selected_groups = load_ai_selected_groups()
    if not selected_groups or len(selected_groups) == 0:
        return "", ()
    placeholders = ','.join('?' * len(selected_groups))
    filter_clause = f"AND c.fGROUP IN ({placeholders})"
    return filter_clause, tuple(selected_groups)

def load_ai_analysis_settings():
    """Загрузить настройки AI анализа"""
    default_settings = {
        'minDebt': 100000,
        'debtRatioInfo': 30,
        'debtRatioWarning': 50,
        'debtRatioCritical': 100,
        'minSalesForPayment': 100000,
        'paymentRateInfo': 50,
        'paymentRateWarning': 35,
        'paymentRateCritical': 20,
        'minAvgSales': 100000,
        'salesDropInfo': 30,
        'salesDropWarning': 50,
        'salesDropCritical': 70,
        'minAvgSalesInactive': 50000,
        'inactiveDaysInfo': 30,
        'inactiveDaysWarning': 45,
        'inactiveDaysCritical': 60
    }
    try:
        if os.path.exists(AI_ANALYSIS_SETTINGS_FILE):
            with open(AI_ANALYSIS_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                # Merge with defaults to ensure all keys exist
                default_settings.update(saved)
                return default_settings
        return default_settings
    except Exception as e:
        app.logger.error(f"[AISettings] Error loading: {e}")
        return default_settings

def save_ai_analysis_settings(settings):
    """Сохранить настройки AI анализа"""
    try:
        with open(AI_ANALYSIS_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[AISettings] Error saving: {e}")
        return False

def load_ai_selected_areas():
    """Загрузить выбранные территории для AI Assistant"""
    try:
        if os.path.exists(AI_SELECTED_AREAS_FILE):
            with open(AI_SELECTED_AREAS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []  # Пустой список = все территории
    except Exception as e:
        app.logger.error(f"[AIAreas] Error loading: {e}")
        return []

def save_ai_selected_areas(areas_list):
    """Сохранить выбранные территории для AI Assistant"""
    try:
        with open(AI_SELECTED_AREAS_FILE, 'w', encoding='utf-8') as f:
            json.dump(areas_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[AIAreas] Error saving: {e}")
        return False

def get_ai_areas_filter_sql():
    """Получить SQL фильтр для выбранных территорий AI Assistant"""
    selected_areas = load_ai_selected_areas()
    if not selected_areas or len(selected_areas) == 0:
        return "", ()
    placeholders = ','.join('?' * len(selected_areas))
    filter_clause = f"AND csa.fSALESAREA IN ({placeholders})"
    return filter_clause, tuple(selected_areas)

def load_dashboard_selected_areas():
    """Загрузить выбранные территории для Dashboard"""
    try:
        if os.path.exists(DASHBOARD_SELECTED_AREAS_FILE):
            with open(DASHBOARD_SELECTED_AREAS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []  # Пустой список = все территории
    except Exception as e:
        app.logger.error(f"[DashboardAreas] Error loading: {e}")
        return []

def save_dashboard_selected_areas(areas_list):
    """Сохранить выбранные территории для Dashboard"""
    try:
        with open(DASHBOARD_SELECTED_AREAS_FILE, 'w', encoding='utf-8') as f:
            json.dump(areas_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[DashboardAreas] Error saving: {e}")
        return False

def get_dashboard_areas_filter_sql():
    """Получить SQL фильтр для выбранных территорий Dashboard"""
    selected_areas = load_dashboard_selected_areas()
    if not selected_areas or len(selected_areas) == 0:
        return "", ()
    placeholders = ','.join('?' * len(selected_areas))
    filter_clause = f"AND csa.fSALESAREA IN ({placeholders})"
    return filter_clause, tuple(selected_areas)

def load_dashboard_selected_groups():
    """Загрузить выбранные группы клиентов для Dashboard"""
    try:
        if os.path.exists(DASHBOARD_SELECTED_GROUPS_FILE):
            with open(DASHBOARD_SELECTED_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []  # Пустой список = все группы
    except Exception as e:
        app.logger.error(f"[DashboardGroups] Error loading: {e}")
        return []

def save_dashboard_selected_groups(groups_list):
    """Сохранить выбранные группы клиентов для Dashboard"""
    try:
        with open(DASHBOARD_SELECTED_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[DashboardGroups] Error saving: {e}")
        return False

def get_dashboard_groups_filter_sql():
    """Получить SQL фильтр для выбранных групп клиентов Dashboard"""
    selected_groups = load_dashboard_selected_groups()
    if not selected_groups or len(selected_groups) == 0:
        return "", ()
    placeholders = ','.join('?' * len(selected_groups))
    filter_clause = f"AND c.fGROUP IN ({placeholders})"
    return filter_clause, tuple(selected_groups)

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

def load_sales_area_group_assignments():
    """Загрузить назначения групп к Sales Areas"""
    try:
        if os.path.exists(SALES_AREA_GROUP_ASSIGNMENTS_FILE):
            with open(SALES_AREA_GROUP_ASSIGNMENTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        app.logger.error(f"[SalesAreaGroups] Error loading: {e}")
        return {}

def save_sales_area_group_assignments(assignments):
    """Сохранить назначения групп к Sales Areas"""
    try:
        with open(SALES_AREA_GROUP_ASSIGNMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(assignments, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[SalesAreaGroups] Error saving: {e}")
        return False

def load_selected_product_groups():
    """Загрузить список выбранных групп товаров для фильтрации"""
    try:
        if os.path.exists(SELECTED_PRODUCT_GROUPS_FILE):
            with open(SELECTED_PRODUCT_GROUPS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []  # Пустой список = показывать все группы
    except Exception as e:
        app.logger.error(f"[ProductGroups] Error loading: {e}")
        return []

def save_selected_product_groups(groups_list):
    """Сохранить список выбранных групп товаров"""
    try:
        with open(SELECTED_PRODUCT_GROUPS_FILE, 'w', encoding='utf-8') as f:
            json.dump(groups_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"[ProductGroups] Error saving: {e}")
        return False

def get_product_groups_filter_sql():
    """Получить SQL условие для фильтрации по выбранным дивизионам
    Возвращает WHERE условие для фильтрации продаж по дивизионам менеджеров"""
    selected_divisions = load_selected_product_groups()  # Теперь это коды дивизионов (000000, 000001 и т.д.)
    
    if not selected_divisions or len(selected_divisions) == 0:
        # Пустой список = показывать все
        return "", ()
    
    # Формируем фильтр: показывать только продажи менеджеров, у которых есть хотя бы один из выбранных дивизионов
    placeholders = ','.join('?' * len(selected_divisions))
    filter_clause = f"""
        AND s.fSALESAGENTID IN (
            SELECT DISTINCT fSALESAGENTID 
            FROM SALESAGENTDIVISIONS 
            WHERE fDIVISION IN ({placeholders})
        )
    """
    return filter_clause, tuple(selected_divisions)

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
            conn = db.get_connection()
            cursor = conn.cursor()
            
            placeholders = ','.join('?' * len(excluded_groups))
            query = f"SELECT fID FROM CUSTOMERS WHERE fGROUP IN ({placeholders})"
            cursor.execute(query, tuple(excluded_groups))
            
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

# ===== Управление дистрибьюторами =====
@app.route('/api/settings/distributor-groups')
def get_distributor_groups():
    """Получить список групп-дистрибьюторов"""
    try:
        distributor_groups = load_distributor_groups()
        return jsonify({'success': True, 'data': distributor_groups})
    except Exception as e:
        app.logger.error(f"[DistributorGroups] Error loading: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/distributor-groups/set', methods=['POST'])
def set_distributor_groups():
    """Установить список групп-дистрибьюторов"""
    try:
        data = request.get_json()
        distributor_groups = data.get('groups', [])
        
        if save_distributor_groups(distributor_groups):
            app.logger.info(f"[DistributorGroups] Saved {len(distributor_groups)} distributor groups")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        app.logger.error(f"[DistributorGroups] Error saving: {e}")
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
            app.logger.info(f"[GroupAssignments] Updated group {group_code} managers: {assignments.get(group_code, [])}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        app.logger.error(f"[GroupAssignments] Error setting: {e}")
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
            app.logger.info(f"[GroupAssignments] Removed manager {manager_id} from group {group_code}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        app.logger.error(f"[GroupAssignments] Error removing: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Выбор групп товаров для фильтрации =====

# Словарь названий групп товаров (на основе анализа продуктов)
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

@app.route('/api/settings/product-groups')
def get_all_product_groups():
    """Получить все дивизионы из таблицы TREES"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
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
        cursor.execute(query)
        
        divisions = []
        for row in cursor.fetchall():
            divisions.append({
                'fGROUP': row[0],  # код дивизиона (000000, 000001 и т.д.)
                'name': row[1],    # название на армянском
                'product_count': 0  # пока не считаем товары
            })
        
        conn.close()
        app.logger.info(f"[Divisions] Loaded {len(divisions)} divisions from TREES")
        return jsonify({'success': True, 'data': divisions})
    except Exception as e:
        app.logger.error(f"[Divisions] Error loading: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/selected-product-groups')
def get_selected_product_groups():
    """Получить список выбранных групп товаров"""
    try:
        selected = load_selected_product_groups()
        return jsonify({'success': True, 'data': selected})
    except Exception as e:
        app.logger.error(f"[ProductGroups] Error loading selected: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/selected-product-groups/set', methods=['POST'])
def set_selected_product_groups():
    """Установить список выбранных групп товаров"""
    try:
        data = request.get_json()
        groups_list = data.get('selectedGroups', [])
        
        if save_selected_product_groups(groups_list):
            app.logger.info(f"[ProductGroups] Saved {len(groups_list)} selected groups")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Ошибка сохранения'})
    except Exception as e:
        app.logger.error(f"[ProductGroups] Error saving selected: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== DEBUG: Test product groups filter =====
@app.route('/api/debug/check-group/<group_code>')
def debug_check_group_products(group_code):
    """Проверить товары в указанной группе"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT TOP 10
                s.fISN,
                s.fTOTALSUM,
                p.fGROUP,
                p.fNAME
            FROM SALES s
            INNER JOIN SALEDOCDETAILS sd ON s.fISN = sd.fISN
            INNER JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
            WHERE s.fDATE >= '2024-11-01' AND s.fDATE <= '2024-11-30'
            AND s.fSTATE = 2
            AND p.fGROUP = '20'
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        
        test1_results = []
        for row in rows:
            test1_results.append({
                'sale_isn': row.fISN,
                'total': float(row.fTOTALSUM),
                'product_group': row.fGROUP,
                'product_name': row.fNAME
            })
        
        # Проверка 2: EXISTS подзапрос
        query2 = """
            SELECT TOP 5 
                s.fISN,
                s.fTOTALSUM,
                s.fDATE
            FROM SALES s
            WHERE s.fDATE >= '2024-11-01' AND s.fDATE <= '2024-11-30'
            AND s.fSTATE = 2
            AND EXISTS (
                SELECT 1 FROM SALEDOCDETAILS sd
                INNER JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
                WHERE sd.fISN = s.fISN
                AND p.fGROUP IN ('20','21','22')
            )
        """
        cursor.execute(query2)
        rows2 = cursor.fetchall()
        
        test2_results = []
        for row in rows2:
            test2_results.append({
                'sale_isn': row.fISN,
                'total': float(row.fTOTALSUM),
                'date': str(row.fDATE)
            })
        
        # Проверка 3: Сумма с фильтром
        query3 = """
            SELECT ISNULL(SUM(s.fTOTALSUM), 0) as Total
            FROM SALES s
            WHERE s.fDATE >= '2024-11-01' AND s.fDATE <= '2024-11-30'
            AND s.fSTATE = 2
            AND EXISTS (
                SELECT 1 FROM SALEDOCDETAILS sd
                INNER JOIN PRODUCTS p ON sd.fPRODUCTID = p.fID
                WHERE sd.fISN = s.fISN
                AND p.fGROUP IN ('20','21','22','23','25','26','27','28','29','30')
            )
        """
        cursor.execute(query3)
        total_row = cursor.fetchone()
        total_with_filter = float(total_row.Total) if total_row else 0
        
        conn.close()
        
        return jsonify({
            'success': True,
            'test1_join_direct': {
                'count': len(test1_results),
                'samples': test1_results
            },
            'test2_exists_subquery': {
                'count': len(test2_results),
                'samples': test2_results
            },
            'test3_total_with_filter': total_with_filter,
            'selected_groups': load_selected_product_groups()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sales-areas/<path:area_code>/route-stats')
def get_area_route_stats(area_code):
    """Получить статистику маршрутов для территории"""
    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        
        if not date_from or not date_to:
            today = datetime.now()
            date_from = today.strftime('%Y-%m-%d')
            date_to = today.strftime('%Y-%m-%d')
            
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Оптимизированный запрос: объединяем все метрики в один запрос
        # Используем CTE для списка клиентов территории и предварительной выборки посещений
        query = """
            WITH AreaCustomers AS (
                SELECT fCUSTOMERID 
                FROM CUSTOMERSALESAREAS 
                WHERE fSALESAREA = ?
            ),
            PlannedVisits AS (
                SELECT l.fCUSTOMERID, CAST(d.fDATE as DATE) as VisitDate
                FROM DOCUMENTS d
                JOIN PLANNEDROUTESLIST l ON d.fISN = l.fISN
                WHERE d.fDOCTYPE = 10
                  AND d.fDATE >= ? AND d.fDATE <= ?
                  AND l.fCUSTOMERID IN (SELECT fCUSTOMERID FROM AreaCustomers)
            ),
            ActualVisits AS (
                SELECT a.fCUSTOMERID, CAST(a.fDATE as DATE) as VisitDate
                FROM ACTUALROUTES a
                WHERE a.fDATE >= ? AND a.fDATE <= ?
                  AND a.fCUSTOMERID IN (SELECT fCUSTOMERID FROM AreaCustomers)
            )
            SELECT
                (SELECT COUNT(*) FROM PlannedVisits) as PlannedCount,
                (SELECT COUNT(*) FROM ActualVisits) as VisitedCount,
                (
                    SELECT COUNT(*) 
                    FROM PlannedVisits p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM ActualVisits a 
                        WHERE a.fCUSTOMERID = p.fCUSTOMERID 
                          AND a.VisitDate = p.VisitDate
                    )
                ) as MissedCount,
                (
                    SELECT COUNT(*) 
                    FROM ActualVisits a
                    WHERE NOT EXISTS (
                        SELECT 1 FROM PlannedVisits p 
                        WHERE p.fCUSTOMERID = a.fCUSTOMERID 
                          AND p.VisitDate = a.VisitDate
                    )
                ) as UnplannedCount,
                (
                    SELECT COUNT(DISTINCT s.fCUSTOMERID)
                    FROM SALES s
                    WHERE s.fSALESAREA = ?
                      AND s.fDATE >= ? AND s.fDATE <= ?
                      AND s.fSTATE = 2
                ) as OrderedCount
        """
        
        # Параметры:
        # 1. AreaCustomers: area_code
        # 2. PlannedVisits: date_from, date_to
        # 3. ActualVisits: date_from, date_to
        # 4. OrderedCount: area_code, date_from, date_to
        params = (
            area_code, 
            date_from, date_to, 
            date_from, date_to, 
            area_code, date_from, date_to
        )
        
        cursor.execute(query, params)
        row = cursor.fetchone()
        
        planned = row[0] or 0
        visited = row[1] or 0
        missed = row[2] or 0
        unplanned = row[3] or 0
        ordered = row[4] or 0
        
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'planned': planned,
                'visited': visited,
                'missed': missed,
                'unplanned': unplanned,
                'ordered': ordered
            }
        })
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики маршрутов: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/test-purchases')
def test_purchases():
    """Тестовая страница для проверки отображения покупок"""
    return render_template('test_purchases.html')

# @app.route('/api/sales-areas/<area_code>/unpaid-documents/export')
# def export_unpaid_documents(area_code):
#     """Export unpaid documents to Excel file - DISABLED: requires openpyxl"""
#     return jsonify({'success': False, 'error': 'Export functionality temporarily disabled'}), 501

@app.route('/api/sales-areas/<area_code>/unpaid-documents')
def get_unpaid_documents(area_code):
    """Получить документы продаж с неоплаченными суммами, сгруппированные по клиентам"""
    try:
        # Получить параметры фильтрации
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        groups_param = request.args.get('groups', '')
        requested_groups = [g.strip() for g in groups_param.split(',') if g.strip()] if groups_param else []
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Построить фильтр по датам
        date_filter = ""
        date_params = tuple()
        if date_from and date_to:
            date_filter = " AND doc.fDATE >= ? AND doc.fDATE <= ?"
            date_params = (date_from, date_to)
        
        # Построить фильтр по группам клиентов
        group_filter = ""
        group_params = tuple()
        if requested_groups:
            placeholders = ','.join(['?'] * len(requested_groups))
            group_filter = f" AND c.fGROUP IN ({placeholders})"
            group_params = tuple(requested_groups)
        
        # Получить документы продаж с неоплаченными суммами
        # Берём из HICUSTOMERSDEBT записи типа 'D' (дебет = долг клиента)
        query = f"""
            SELECT 
                c.fCODE as CustomerCode,
                c.fNAME as CustomerName,
                debt.fDEBTDOCISN as DocNumber,
                doc.fDATE as DocDate,
                debt.fSUM as DocSum,
                ISNULL(payments.PaidAmount, 0) as PaidAmount,
                debt.fSUM - ISNULL(payments.PaidAmount, 0) as UnpaidAmount
            FROM HICUSTOMERSDEBT debt
            INNER JOIN DOCUMENTS doc ON debt.fDEBTDOCISN = doc.fISN
            INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
            INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            OUTER APPLY (
                SELECT SUM(p.fSUM) as PaidAmount
                FROM HICUSTOMERSDEBT p
                WHERE p.fDEBTDOCISN = doc.fISN 
                    AND p.fDBCR = 'C'
            ) payments
            WHERE debt.fDBCR = 'D'
                AND csa.fSALESAREA = ?
                {date_filter}
                {group_filter}
                AND (debt.fSUM - ISNULL(payments.PaidAmount, 0)) > 0
            ORDER BY c.fNAME, doc.fDATE DESC
        """
        
        params = (area_code,) + date_params + group_params
        cursor.execute(query, params)
        
        # Группировка по клиентам
        customers_dict = {}
        total_debt = 0
        
        for row in cursor.fetchall():
            customer_code = row.CustomerCode
            
            if customer_code not in customers_dict:
                customers_dict[customer_code] = {
                    'customerCode': customer_code,
                    'customerName': row.CustomerName,
                    'documents': [],
                    'totalDebt': 0
                }
            
            unpaid = float(row.UnpaidAmount) if row.UnpaidAmount else 0
            
            customers_dict[customer_code]['documents'].append({
                'docNumber': row.DocNumber,
                'docDate': row.DocDate.strftime('%Y-%m-%d') if row.DocDate else '',
                'docSum': float(row.DocSum) if row.DocSum else 0,
                'paidAmount': float(row.PaidAmount) if row.PaidAmount else 0,
                'unpaidAmount': unpaid
            })
            
            customers_dict[customer_code]['totalDebt'] += unpaid
            total_debt += unpaid
        
        # Преобразовать в список и отсортировать по долгу
        customers_list = list(customers_dict.values())
        customers_list.sort(key=lambda x: x['totalDebt'], reverse=True)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': customers_list,
            'total_customers': len(customers_list),
            'total_debt': total_debt
        })
        
    except Exception as e:
        print(f"Error getting unpaid documents: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# =============================================
# КОНСТРУКТОР DASHBOARD
# =============================================

DASHBOARD_BUILDER_FILE = 'dashboard_builder_layout.json'

def load_dashboard_builder_layout():
    """Загрузить макет конструктора Dashboard"""
    try:
        if os.path.exists(DASHBOARD_BUILDER_FILE):
            with open(DASHBOARD_BUILDER_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading dashboard builder layout: {e}")
    return {'cards': [], 'nextId': 1}

def save_dashboard_builder_layout(layout):
    """Сохранить макет конструктора Dashboard"""
    try:
        with open(DASHBOARD_BUILDER_FILE, 'w', encoding='utf-8') as f:
            json.dump(layout, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving dashboard builder layout: {e}")
        return False

@app.route('/dashboard-builder')
def dashboard_builder_page():
    """Страница конструктора Dashboard"""
    return render_template('dashboard_builder.html')

@app.route('/api/dashboard-builder/layout', methods=['GET'])
def get_dashboard_builder_layout():
    """Получить макет конструктора Dashboard"""
    try:
        layout = load_dashboard_builder_layout()
        return jsonify({'success': True, 'data': layout})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard-builder/layout', methods=['POST'])
def save_dashboard_builder_layout_api():
    """Сохранить макет конструктора Dashboard"""
    try:
        data = request.get_json()
        if save_dashboard_builder_layout(data):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to save'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard-builder/card-data')
def get_card_data():
    """Получить данные для карточки конструктора Dashboard"""
    try:
        # Поддержка множественных фильтров
        areas = request.args.getlist('areas')  # Множественный выбор территорий
        groups = request.args.getlist('groups')  # Множественный выбор групп
        divisions = request.args.getlist('divisions')  # Множественный выбор дивизионов
        period = request.args.get('period', 'current_month')
        metric = request.args.get('metric', 'total_sales')
        show_comparison = request.args.get('show_comparison', 'false') == 'true'
        
        # Определяем даты периода
        now = datetime.now()
        current_day = now.day
        
        if period == 'today':
            date_from = now.strftime('%Y-%m-%d')
            date_to = now.strftime('%Y-%m-%d')
        elif period == 'current_month':
            date_from = now.replace(day=1).strftime('%Y-%m-%d')
            date_to = now.strftime('%Y-%m-%d')
        elif period == 'last_month':
            first_day_this_month = now.replace(day=1)
            last_day_prev_month = first_day_this_month - timedelta(days=1)
            date_from = last_day_prev_month.replace(day=1).strftime('%Y-%m-%d')
            date_to = last_day_prev_month.strftime('%Y-%m-%d')
        elif period == 'current_year':
            date_from = now.replace(month=1, day=1).strftime('%Y-%m-%d')
            date_to = now.strftime('%Y-%m-%d')
        elif period == 'last_year':
            date_from = (now.replace(month=1, day=1) - timedelta(days=365)).strftime('%Y-%m-%d')
            date_to = (now - timedelta(days=365)).strftime('%Y-%m-%d')
        else:
            date_from = now.replace(day=1).strftime('%Y-%m-%d')
            date_to = now.strftime('%Y-%m-%d')
        
        # Вычисляем даты для сравнения (те же дни в прошлом месяце и прошлом году)
        comparison_dates = {}
        if show_comparison and period == 'current_month':
            import calendar
            
            # Прошлый месяц - с 1 по текущий день
            if now.month == 1:
                prev_month_year = now.year - 1
                prev_month = 12
            else:
                prev_month_year = now.year
                prev_month = now.month - 1
            
            # Определяем последний день прошлого месяца для корректировки
            last_day_prev_month = calendar.monthrange(prev_month_year, prev_month)[1]
            prev_month_day = min(current_day, last_day_prev_month)
            
            comparison_dates['prev_month'] = {
                'from': f"{prev_month_year}-{prev_month:02d}-01",
                'to': f"{prev_month_year}-{prev_month:02d}-{prev_month_day:02d}"
            }
            
            # Прошлый год, тот же месяц - с 1 по текущий день
            prev_year = now.year - 1
            last_day_prev_year_month = calendar.monthrange(prev_year, now.month)[1]
            prev_year_day = min(current_day, last_day_prev_year_month)
            
            comparison_dates['prev_year'] = {
                'from': f"{prev_year}-{now.month:02d}-01",
                'to': f"{prev_year}-{now.month:02d}-{prev_year_day:02d}"
            }
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Проверяем нужны ли JOIN-ы
        needs_joins = (areas and len(areas) > 0) or (groups and len(groups) > 0)
        
        # Построение фильтров для множественного выбора
        area_filter = ""
        group_filter = ""
        division_filter = ""
        params = [date_from, date_to]
        
        if areas and len(areas) > 0:
            placeholders = ','.join(['?' for _ in areas])
            area_filter = f"AND csa.fSALESAREA IN ({placeholders})"
            params.extend(areas)
        
        if groups and len(groups) > 0:
            placeholders = ','.join(['?' for _ in groups])
            group_filter = f"AND c.fGROUP IN ({placeholders})"
            params.extend(groups)
        
        if divisions and len(divisions) > 0:
            placeholders = ','.join(['?' for _ in divisions])
            division_filter = f"AND s.fDIVISION IN ({placeholders})"
            params.extend(divisions)
        
        value = 0
        
        # Debug logging
        logger.info(f"Card data request: metric={metric}, areas={areas}, groups={groups}, divisions={divisions}")
        logger.info(f"Division filter: {division_filter}")
        logger.info(f"Params: {params}")
        
        if metric == 'total_sales':
            if needs_joins or (divisions and len(divisions) > 0):
                query = f"""
                SELECT ISNULL(SUM(s.fTOTALSUM), 0) as value
                FROM SALES s
                INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
                LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                WHERE s.fDATE BETWEEN ? AND ?
                AND s.fSTATE = 2
                {area_filter}
                {group_filter}
                {division_filter}
                """
            else:
                query = """
                SELECT ISNULL(SUM(s.fTOTALSUM), 0) as value
                FROM SALES s
                WHERE s.fDATE BETWEEN ? AND ?
                AND s.fSTATE = 2
                """
                params = [date_from, date_to]  # без фильтров - только даты
            logger.info(f"Query: {query}")
        elif metric == 'total_payments':
            # Для payments divisions не применяются (нет связи через агентов)
            if needs_joins:
                query = f"""
                SELECT ISNULL(SUM(ABS(p.fSUM)), 0) as value
                FROM PAYMENTS p
                INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
                LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                WHERE p.fDATE BETWEEN ? AND ?
                {area_filter}
                {group_filter}
                """
                # Пересоздаём params без divisions для payments
                params = [date_from, date_to]
                if areas and len(areas) > 0:
                    params.extend(areas)
                if groups and len(groups) > 0:
                    params.extend(groups)
            else:
                query = """
                SELECT ISNULL(SUM(ABS(p.fSUM)), 0) as value
                FROM PAYMENTS p
                WHERE p.fDATE BETWEEN ? AND ?
                """
                params = [date_from, date_to]
        elif metric == 'total_debt':
            # Для долга используем формулу: DebtFromDocs - Type01 - Type02
            # Divisions не применяются для долга (нет связи через агентов)
            debt_params = []
            debt_area_filter = ""
            debt_group_filter = ""
            
            if areas and len(areas) > 0:
                placeholders = ','.join(['?' for _ in areas])
                debt_area_filter = f"AND csa.fSALESAREA IN ({placeholders})"
                debt_params.extend(areas)
            if groups and len(groups) > 0:
                placeholders = ','.join(['?' for _ in groups])
                debt_group_filter = f"AND c.fGROUP IN ({placeholders})"
                debt_params.extend(groups)
            
            # Запрос для DebtFromDocs (D - C из HICUSTOMERSDEBT)
            query = f"""
            SELECT 
                ISNULL((
                    SELECT SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END)
                    FROM HICUSTOMERSDEBT d
                    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
                    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                    WHERE 1=1 {debt_area_filter} {debt_group_filter}
                ), 0) 
                - ISNULL((
                    SELECT SUM(CASE WHEN r.fTYPE = '01' THEN ABS(r.fSUM) ELSE 0 END)
                    FROM HIRESTCUSTOMERSSUM r
                    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
                    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                    WHERE 1=1 {debt_area_filter} {debt_group_filter}
                ), 0)
                - ISNULL((
                    SELECT SUM(CASE WHEN r.fTYPE = '02' THEN ABS(r.fSUM) ELSE 0 END)
                    FROM HIRESTCUSTOMERSSUM r
                    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
                    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                    WHERE 1=1 {debt_area_filter} {debt_group_filter}
                ), 0)
            as value
            """
            # Параметры нужно повторить 3 раза (для каждого подзапроса)
            params = debt_params + debt_params + debt_params
        elif metric == 'customer_count':
            query = f"""
            SELECT COUNT(DISTINCT s.fCUSTOMERID) as value
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            {area_filter}
            {group_filter}
            {division_filter}
            """
        elif metric == 'sales_count':
            query = f"""
            SELECT COUNT(*) as value
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            {area_filter}
            {group_filter}
            {division_filter}
            """
        elif metric == 'avg_check':
            query = f"""
            SELECT ISNULL(AVG(s.fTOTALSUM), 0) as value
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            {area_filter}
            {group_filter}
            {division_filter}
            """
        elif metric == 'payment_rate':
            # Для payment_rate: division_filter применяется только к sales, не к payments
            query = f"""
            SELECT 
                CASE 
                    WHEN ISNULL(SUM(sales.TotalSales), 0) = 0 THEN 0
                    ELSE ISNULL(SUM(ABS(pay.TotalPayments)), 0) * 100.0 / ISNULL(SUM(sales.TotalSales), 1)
                END as value
            FROM (
                SELECT s.fCUSTOMERID, SUM(s.fTOTALSUM) as TotalSales
                FROM SALES s
                INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
                LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                WHERE s.fDATE BETWEEN ? AND ?
                AND s.fSTATE = 2
                {area_filter}
                {group_filter}
                {division_filter}
                GROUP BY s.fCUSTOMERID
            ) sales
            LEFT JOIN (
                SELECT p.fCUSTOMERID, SUM(p.fSUM) as TotalPayments
                FROM PAYMENTS p
                INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
                LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                WHERE p.fDATE BETWEEN ? AND ?
                {area_filter}
                {group_filter}
                GROUP BY p.fCUSTOMERID
            ) pay ON sales.fCUSTOMERID = pay.fCUSTOMERID
            """
            # Для payment_rate: params для sales + params для payments
            params = [date_from, date_to]
            if areas and len(areas) > 0:
                params.extend(areas)
            if groups and len(groups) > 0:
                params.extend(groups)
            if divisions and len(divisions) > 0:
                params.extend(divisions)
            # Для payments части (без divisions)
            params.extend([date_from, date_to])
            if areas and len(areas) > 0:
                params.extend(areas)
            if groups and len(groups) > 0:
                params.extend(groups)
        elif metric == 'debt_customers':
            # Divisions не применяются для долга
            debt_params = []
            debt_area_filter = ""
            debt_group_filter = ""
            
            if areas and len(areas) > 0:
                placeholders = ','.join(['?' for _ in areas])
                debt_area_filter = f"AND csa.fSALESAREA IN ({placeholders})"
                debt_params.extend(areas)
            if groups and len(groups) > 0:
                placeholders = ','.join(['?' for _ in groups])
                debt_group_filter = f"AND c.fGROUP IN ({placeholders})"
                debt_params.extend(groups)
                
            query = f"""
            SELECT COUNT(DISTINCT doc.fCUSTOMERID) as value
            FROM HICUSTOMERSDEBT d
            INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
            INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE d.fDBCR = 'D'
            {debt_area_filter}
            {debt_group_filter}
            """
            params = debt_params
        elif metric in ['forecast_sales', 'forecast_completion', 'days_remaining', 'daily_avg', 'needed_daily', 'plan_gap']:
            # Метрики прогнозирования
            import calendar
            
            # Текущие продажи за период
            if needs_joins or (divisions and len(divisions) > 0):
                sales_query = f"""
                SELECT ISNULL(SUM(s.fTOTALSUM), 0) as value
                FROM SALES s
                INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
                LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                WHERE s.fDATE BETWEEN ? AND ?
                AND s.fSTATE = 2
                {area_filter}
                {group_filter}
                {division_filter}
                """
                sales_params = [date_from, date_to] + list(areas or []) + list(groups or []) + list(divisions or [])
            else:
                sales_query = """
                SELECT ISNULL(SUM(s.fTOTALSUM), 0) as value
                FROM SALES s
                WHERE s.fDATE BETWEEN ? AND ?
                AND s.fSTATE = 2
                """
                sales_params = [date_from, date_to]
            
            cursor.execute(sales_query, sales_params)
            current_sales = float(cursor.fetchone()[0] or 0)
            
            # Определяем параметры периода
            from datetime import datetime as dt_module
            from datetime import timedelta
            period_start = dt_module.strptime(date_from, '%Y-%m-%d') if isinstance(date_from, str) else date_from
            period_end = dt_module.strptime(date_to, '%Y-%m-%d') if isinstance(date_to, str) else date_to
            
            # Функция для подсчёта рабочих дней (без воскресений)
            def count_working_days(start_date, end_date):
                """Считает рабочие дни (пн-сб), исключая воскресенья"""
                count = 0
                current = start_date
                while current <= end_date:
                    if current.weekday() != 6:  # 6 = воскресенье
                        count += 1
                    current += timedelta(days=1)
                return count
            
            # Рабочие дни в месяце
            if period == 'current_month':
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                last_day = calendar.monthrange(now.year, now.month)[1]
                month_end = now.replace(day=last_day, hour=23, minute=59, second=59)
                
                elapsed_days = count_working_days(month_start, now)
                tomorrow = now + timedelta(days=1)
                remaining_days = count_working_days(tomorrow, month_end) if tomorrow <= month_end else 0
                total_days = elapsed_days + remaining_days
            elif period == 'current_year':
                year_start = dt_module(now.year, 1, 1)
                year_end = dt_module(now.year, 12, 31)
                elapsed_days = count_working_days(year_start, now)
                tomorrow = now + timedelta(days=1)
                remaining_days = count_working_days(tomorrow, year_end)
                total_days = elapsed_days + remaining_days
            else:
                elapsed_days = count_working_days(period_start, now) if now >= period_start else 0
                tomorrow = now + timedelta(days=1)
                remaining_days = count_working_days(tomorrow, period_end) if now < period_end else 0
                total_days = count_working_days(period_start, period_end)
            
            # Среднее в рабочий день
            daily_avg = current_sales / max(elapsed_days, 1)
            
            # Прогноз на конец периода
            forecast = current_sales + (daily_avg * remaining_days)
            
            # Получаем план (если есть)
            plan_value = 0
            try:
                # Пробуем получить план из таблицы планов
                plan_query = """
                SELECT ISNULL(SUM(fPLAN), 0) as plan_value
                FROM PLANS 
                WHERE fYEAR = ? AND fMONTH = ?
                """
                cursor.execute(plan_query, [now.year, now.month])
                plan_row = cursor.fetchone()
                if plan_row and plan_row[0]:
                    plan_value = float(plan_row[0])
            except:
                pass
            
            # Нужно в день для выполнения плана
            needed_daily = (plan_value - current_sales) / max(remaining_days, 1) if plan_value > current_sales and remaining_days > 0 else 0
            
            # Выбираем нужную метрику
            if metric == 'forecast_sales':
                value = forecast
            elif metric == 'forecast_completion':
                # % выполнения = текущие продажи / (план * процент прошедших дней)
                expected_sales = (plan_value * elapsed_days / total_days) if plan_value > 0 and total_days > 0 else current_sales
                value = (current_sales / expected_sales * 100) if expected_sales > 0 else 0
            elif metric == 'days_remaining':
                value = remaining_days
            elif metric == 'daily_avg':
                value = daily_avg
            elif metric == 'needed_daily':
                value = needed_daily
            elif metric == 'plan_gap':
                value = plan_value - current_sales
            
            # Не нужно выполнять основной запрос
            query = None
            params = []
        else:
            query = "SELECT 0 as value"
            params = []
        
        if query:
            cursor.execute(query, params)
            row = cursor.fetchone()
            value = row[0] if row and row[0] else 0
        
        # Получаем данные для сравнения
        comparison_data = {}
        if show_comparison and comparison_dates:
            for comp_key, comp_dates in comparison_dates.items():
                comp_value = get_metric_value_for_period(
                    cursor, conn, metric, comp_dates['from'], comp_dates['to'],
                    areas, groups, divisions
                )
                comparison_data[comp_key] = {
                    'value': float(comp_value) if comp_value else 0,
                    'from': comp_dates['from'],
                    'to': comp_dates['to']
                }
        
        # Вычисляем прогноз для метрики продаж
        forecast_data = None
        if metric == 'total_sales' and period in ['current_month', 'today']:
            import calendar
            from datetime import timedelta
            
            # Функция для подсчёта рабочих дней (без воскресений)
            def count_working_days(start_date, end_date):
                """Считает рабочие дни (пн-сб), исключая воскресенья"""
                count = 0
                current = start_date
                while current <= end_date:
                    if current.weekday() != 6:  # 6 = воскресенье
                        count += 1
                    current += timedelta(days=1)
                return count
            
            # Начало и конец месяца
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_day = calendar.monthrange(now.year, now.month)[1]
            month_end = now.replace(day=last_day, hour=23, minute=59, second=59)
            
            # Рабочие дни прошедшие (с 1 по сегодня)
            elapsed_working_days = count_working_days(month_start, now)
            
            # Рабочие дни оставшиеся (с завтра до конца месяца)
            tomorrow = now + timedelta(days=1)
            remaining_working_days = count_working_days(tomorrow, month_end) if tomorrow <= month_end else 0
            
            # Всего рабочих дней в месяце
            total_working_days = elapsed_working_days + remaining_working_days
            
            current_value = float(value) if value else 0
            
            # Средние продажи в рабочий день
            daily_avg = current_value / max(elapsed_working_days, 1)
            
            # Прогноз = текущие + (среднее * оставшиеся рабочие дни)
            forecast = current_value + (daily_avg * remaining_working_days)
            
            forecast_data = {
                'forecast': forecast,
                'daily_avg': daily_avg,
                'remaining_days': remaining_working_days,
                'total_days': total_working_days,
                'elapsed_days': elapsed_working_days
            }
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'value': float(value) if value else 0,
                'metric': metric,
                'period': period,
                'areas': areas,
                'groups': groups,
                'comparison': comparison_data if comparison_data else None,
                'forecast': forecast_data,
                'current_day': now.day
            }
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def get_metric_value_for_period(cursor, conn, metric, date_from, date_to, areas=None, groups=None, divisions=None):
    """Вспомогательная функция для получения значения метрики за период"""
    # Проверяем, нужны ли JOIN-ы
    needs_joins = (areas and len(areas) > 0) or (groups and len(groups) > 0) or (divisions and len(divisions) > 0)
    
    area_filter = ""
    group_filter = ""
    division_filter = ""
    params = [date_from, date_to]
    
    if areas and len(areas) > 0:
        placeholders = ','.join(['?' for _ in areas])
        area_filter = f"AND csa.fSALESAREA IN ({placeholders})"
        params.extend(areas)
    
    if groups and len(groups) > 0:
        placeholders = ','.join(['?' for _ in groups])
        group_filter = f"AND c.fGROUP IN ({placeholders})"
        params.extend(groups)
    
    if divisions and len(divisions) > 0:
        placeholders = ','.join(['?' for _ in divisions])
        division_filter = f"AND s.fDIVISION IN ({placeholders})"
        params.extend(divisions)
    
    if metric == 'total_sales':
        if needs_joins:
            query = f"""
            SELECT ISNULL(SUM(s.fTOTALSUM), 0) as value
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            {area_filter}
            {group_filter}
            {division_filter}
            """
        else:
            query = """
            SELECT ISNULL(SUM(s.fTOTALSUM), 0) as value
            FROM SALES s
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            """
    elif metric == 'total_payments':
        if needs_joins:
            query = f"""
            SELECT ISNULL(SUM(ABS(p.fSUM)), 0) as value
            FROM PAYMENTS p
            INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE p.fDATE BETWEEN ? AND ?
            {area_filter}
            {group_filter}
            """
        else:
            query = """
            SELECT ISNULL(SUM(ABS(p.fSUM)), 0) as value
            FROM PAYMENTS p
            WHERE p.fDATE BETWEEN ? AND ?
            """
    elif metric == 'customer_count':
        if needs_joins:
            query = f"""
            SELECT COUNT(DISTINCT s.fCUSTOMERID) as value
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            {area_filter}
            {group_filter}
            {division_filter}
            """
        else:
            query = """
            SELECT COUNT(DISTINCT s.fCUSTOMERID) as value
            FROM SALES s
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            """
    elif metric == 'sales_count':
        if needs_joins:
            query = f"""
            SELECT COUNT(*) as value
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            {area_filter}
            {group_filter}
            {division_filter}
            """
        else:
            query = """
            SELECT COUNT(*) as value
            FROM SALES s
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            """
    elif metric == 'avg_check':
        if needs_joins:
            query = f"""
            SELECT ISNULL(AVG(s.fTOTALSUM), 0) as value
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            {area_filter}
            {group_filter}
            {division_filter}
            """
        else:
            query = """
            SELECT ISNULL(AVG(s.fTOTALSUM), 0) as value
            FROM SALES s
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            """
    else:
        return 0
    
    try:
        new_conn = db.get_connection()
        new_cursor = new_conn.cursor()
        new_cursor.execute(query, params)
        row = new_cursor.fetchone()
        value = row[0] if row and row[0] else 0
        new_cursor.close()
        new_conn.close()
        return value
    except Exception as e:
        print(f"Error getting comparison data: {e}")
        return 0


@app.route('/api/dashboard-builder/chart-data')
def get_chart_data():
    """Получить данные для графика в конструкторе Dashboard"""
    try:
        # Фильтры
        areas = request.args.getlist('areas')
        groups = request.args.getlist('groups')
        divisions = request.args.getlist('divisions')
        period = request.args.get('period', 'current_month')
        chart_type = request.args.get('chart_type', 'line')  # line, bar, area, pie
        metrics = request.args.getlist('metrics')  # Множественные метрики: sales, payments, debt
        compare_periods = request.args.getlist('compare_periods')  # Периоды для сравнения: current, prev_month, prev_year
        compare_years = request.args.getlist('compare_years')  # Годы для сравнения: 2025, 2024, 2023...
        year_compare_mode = request.args.get('year_compare_mode', 'year')  # 'year' или 'month'
        
        if not metrics:
            metrics = ['sales']
        
        if not compare_periods:
            compare_periods = ['current']
        
        # Преобразуем годы в числа
        compare_years = [int(y) for y in compare_years if y.isdigit()]
        
        # Определяем даты периода
        now = datetime.now()
        
        if period == 'current_month':
            # Данные по дням текущего месяца
            date_from = now.replace(day=1)
            date_to = now
            group_by = 'day'
        elif period == 'last_month':
            first_day_this_month = now.replace(day=1)
            last_day_prev_month = first_day_this_month - timedelta(days=1)
            date_from = last_day_prev_month.replace(day=1)
            date_to = last_day_prev_month
            group_by = 'day'
        elif period == 'current_year':
            date_from = now.replace(month=1, day=1)
            date_to = now
            group_by = 'month'
        elif period == 'last_year':
            date_from = now.replace(year=now.year-1, month=1, day=1)
            date_to = now.replace(year=now.year-1, month=12, day=31)
            group_by = 'month'
        else:
            date_from = now.replace(day=1)
            date_to = now
            group_by = 'day'
        
        # Функция для получения дат периода сравнения
        def get_compare_period_dates(compare_period, base_from, base_to):
            """Возвращает даты для периода сравнения"""
            if compare_period == 'current':
                return base_from, base_to, 'Текущий'
            elif compare_period == 'prev_month':
                # Прошлый месяц (те же дни, месяц назад)
                prev_from = base_from.replace(month=base_from.month - 1) if base_from.month > 1 else base_from.replace(year=base_from.year - 1, month=12)
                prev_to = base_to.replace(month=base_to.month - 1) if base_to.month > 1 else base_to.replace(year=base_to.year - 1, month=12)
                # Корректировка дней
                import calendar
                max_day_from = calendar.monthrange(prev_from.year, prev_from.month)[1]
                max_day_to = calendar.monthrange(prev_to.year, prev_to.month)[1]
                prev_from = prev_from.replace(day=min(base_from.day, max_day_from))
                prev_to = prev_to.replace(day=min(base_to.day, max_day_to))
                return prev_from, prev_to, 'Пр. месяц'
            elif compare_period == 'prev_year':
                # Прошлый год (те же даты, год назад)
                prev_from = base_from.replace(year=base_from.year - 1)
                prev_to = base_to.replace(year=base_to.year - 1)
                # Корректировка для 29 февраля
                import calendar
                if base_from.month == 2 and base_from.day == 29:
                    max_day = calendar.monthrange(prev_from.year, 2)[1]
                    prev_from = prev_from.replace(day=min(29, max_day))
                if base_to.month == 2 and base_to.day == 29:
                    max_day = calendar.monthrange(prev_to.year, 2)[1]
                    prev_to = prev_to.replace(day=min(29, max_day))
                return prev_from, prev_to, 'Пр. год'
            return base_from, base_to, 'Текущий'
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Построение фильтров
        area_filter = ""
        group_filter = ""
        division_filter = ""
        base_params = []
        
        if areas and len(areas) > 0:
            placeholders = ','.join(['?' for _ in areas])
            area_filter = f"AND csa.fSALESAREA IN ({placeholders})"
            base_params.extend(areas)
        
        if groups and len(groups) > 0:
            placeholders = ','.join(['?' for _ in groups])
            group_filter = f"AND c.fGROUP IN ({placeholders})"
            base_params.extend(groups)
        
        if divisions and len(divisions) > 0:
            placeholders = ','.join(['?' for _ in divisions])
            division_filter = f"AND s.fDIVISION IN ({placeholders})"
            base_params.extend(divisions)
        
        # Цвета для периодов сравнения
        period_colors = {
            'current': {'sales': '#3b82f6', 'payments': '#10b981', 'debt': '#ef4444'},
            'prev_month': {'sales': '#8b5cf6', 'payments': '#06b6d4', 'debt': '#f97316'},
            'prev_year': {'sales': '#6366f1', 'payments': '#14b8a6', 'debt': '#f59e0b'}
        }
        
        period_line_styles = {
            'current': False,  # Сплошная линия
            'prev_month': [5, 5],  # Пунктир
            'prev_year': [10, 5]  # Длинный пунктир
        }
        
        # Получаем данные для каждой метрики
        chart_data = {
            'labels': [],
            'datasets': []
        }
        
        metric_labels = {
            'sales': 'Продажи',
            'payments': 'Оплаты',
            'debt': 'Долг'
        }
        
        labels_set = False
        
        # Цвета для годов
        year_colors = {
            2025: '#3b82f6',  # Синий
            2024: '#8b5cf6',  # Фиолетовый
            2023: '#f59e0b',  # Оранжевый
            2022: '#10b981',  # Зелёный
            2021: '#ec4899',  # Розовый
            2020: '#06b6d4',  # Голубой
            2019: '#ef4444',  # Красный
            2018: '#84cc16',  # Лайм
            2017: '#f97316',  # Оранжевый темнее
        }
        
        # Если выбраны годы для сравнения - показываем СУММУ за каждый год
        # Каждый год = одна точка на графике (как на Excel графике)
        if compare_years and len(compare_years) > 0:
            logger.info(f"Compare years mode: {year_compare_mode}, years: {compare_years}")
            
            # Сортируем годы
            compare_years = sorted(compare_years)
            
            # Labels - это годы
            chart_data['labels'] = [str(y) for y in compare_years]
            
            # Для каждой метрики создаём отдельный dataset
            for metric in metrics:
                data_values = []
                logger.info(f"Processing metric: {metric}")
                
                for year in compare_years:
                    logger.info(f"Processing year: {year}")
                    # Определяем период в зависимости от режима
                    if year_compare_mode == 'month':
                        # Режим "месяц" - данные за текущий месяц в каждом году
                        import calendar
                        current_month = now.month
                        current_day = now.day
                        
                        # Проверяем, сколько дней в этом месяце в выбранном году
                        max_day = calendar.monthrange(year, current_month)[1]
                        day_to_use = min(current_day, max_day)
                        
                        year_from = datetime(year, current_month, 1)
                        year_to = datetime(year, current_month, day_to_use)
                    else:
                        # Режим "год" - данные за весь год
                        year_from = datetime(year, 1, 1)
                        # Для текущего года берём данные до текущей даты
                        if year == now.year:
                            year_to = now
                        else:
                            year_to = datetime(year, 12, 31)
                    
                    logger.info(f"Year {year}: {year_from.date()} to {year_to.date()}")
                    
                    if metric == 'sales':
                        # Если нет фильтров - простой запрос без JOIN (чтобы не дублировать)
                        if not areas and not groups and not divisions:
                            query = """
                            SELECT ISNULL(SUM(s.fTOTALSUM), 0) as value
                            FROM SALES s
                            WHERE s.fDATE BETWEEN ? AND ?
                            AND s.fSTATE = 2
                            """
                            params = [year_from.strftime('%Y-%m-%d'), year_to.strftime('%Y-%m-%d')]
                        else:
                            # С фильтрами - нужны JOIN
                            query = f"""
                            SELECT ISNULL(SUM(s.fTOTALSUM), 0) as value
                            FROM SALES s
                            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
                            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                            WHERE s.fDATE BETWEEN ? AND ?
                            AND s.fSTATE = 2
                            {area_filter}
                            {group_filter}
                            {division_filter}
                            """
                            params = [year_from.strftime('%Y-%m-%d'), year_to.strftime('%Y-%m-%d')] + base_params.copy()
                    elif metric == 'payments':
                        # Если нет фильтров - простой запрос без JOIN
                        if not areas and not groups:
                            query = """
                            SELECT ISNULL(SUM(p.fSUM), 0) as value
                            FROM PAYMENTS p
                            WHERE p.fDATE BETWEEN ? AND ?
                            """
                            params = [year_from.strftime('%Y-%m-%d'), year_to.strftime('%Y-%m-%d')]
                        else:
                            query = f"""
                            SELECT ISNULL(SUM(p.fSUM), 0) as value
                            FROM PAYMENTS p
                            INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
                            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                            WHERE p.fDATE BETWEEN ? AND ?
                            {area_filter}
                            {group_filter}
                            """
                            # Для payments не используем division_filter, только area и group
                            payment_params = [year_from.strftime('%Y-%m-%d'), year_to.strftime('%Y-%m-%d')]
                            if areas:
                                payment_params.extend(areas)
                            if groups:
                                payment_params.extend(groups)
                            params = payment_params
                    elif metric == 'debt':
                        # Для долга - берём текущий долг на конец периода
                        debt_area_filter = ""
                        debt_group_filter = ""
                        debt_params = []
                        
                        if areas and len(areas) > 0:
                            placeholders = ','.join(['?' for _ in areas])
                            debt_area_filter = f"AND csa.fSALESAREA IN ({placeholders})"
                            debt_params.extend(areas)
                        
                        if groups and len(groups) > 0:
                            placeholders = ','.join(['?' for _ in groups])
                            debt_group_filter = f"AND c.fGROUP IN ({placeholders})"
                            debt_params.extend(groups)
                        
                        query = f"""
                        SELECT ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as value
                        FROM HICUSTOMERSDEBT d
                        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
                        LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                        WHERE d.fDATE <= ?
                        {debt_area_filter}
                        {debt_group_filter}
                        """
                        params = [year_to.strftime('%Y-%m-%d')] + debt_params
                    else:
                        continue
                    
                    cursor.execute(query, params)
                    row = cursor.fetchone()
                    value = float(row[0]) if row and row[0] else 0
                    data_values.append(value)
                
                # Определяем цвет для метрики
                metric_colors = {
                    'sales': '#3b82f6',     # Синий
                    'payments': '#10b981',  # Зелёный
                    'debt': '#ef4444'       # Красный
                }
                base_color = metric_colors.get(metric, '#6b7280')
                
                # Добавляем dataset
                bg_color = f"rgba({int(base_color[1:3], 16)}, {int(base_color[3:5], 16)}, {int(base_color[5:7], 16)}, 0.2)"
                
                dataset = {
                    'label': metric_labels.get(metric, metric),
                    'data': data_values,
                    'backgroundColor': base_color,
                    'borderColor': base_color,
                    'borderWidth': 2,
                    'fill': False,
                    'tension': 0.3,
                    'pointRadius': 6,
                    'pointHoverRadius': 8,
                    'pointBackgroundColor': base_color,
                    'pointBorderColor': '#fff',
                    'pointBorderWidth': 2
                }
                
                chart_data['datasets'].append(dataset)
            
            # Возвращаем результат
            cursor.close()
            conn.close()
            
            return jsonify({
                'success': True,
                'data': chart_data,
                'period': period,
                'chart_type': chart_type,
                'compare_years': compare_years
            })
        
        # Перебираем все комбинации периодов и метрик
        for compare_period in compare_periods:
            # Получаем даты для этого периода сравнения
            cp_from, cp_to, period_label_suffix = get_compare_period_dates(compare_period, date_from, date_to)
            
            for metric in metrics:
                # Определяем нужны ли JOIN-ы для этого запроса
                needs_filters = (areas and len(areas) > 0) or (groups and len(groups) > 0) or (divisions and len(divisions) > 0)
                
                params = [cp_from.strftime('%Y-%m-%d'), cp_to.strftime('%Y-%m-%d')]
                if needs_filters:
                    params = params + base_params.copy()
                
                # Определяем цвет и стиль линии
                base_color = period_colors.get(compare_period, period_colors['current']).get(metric, '#6b7280')
                line_dash = period_line_styles.get(compare_period, False)
                
                if metric == 'sales':
                    if needs_filters:
                        if group_by == 'day':
                            query = f"""
                            SELECT CONVERT(VARCHAR(10), s.fDATE, 120) as period_label, 
                                   ISNULL(SUM(s.fTOTALSUM), 0) as value
                            FROM SALES s
                            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
                            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                            WHERE s.fDATE BETWEEN ? AND ?
                            AND s.fSTATE = 2
                            {area_filter}
                            {group_filter}
                            {division_filter}
                            GROUP BY CONVERT(VARCHAR(10), s.fDATE, 120)
                            ORDER BY period_label
                            """
                        else:
                            query = f"""
                            SELECT FORMAT(s.fDATE, 'yyyy-MM') as period_label,
                                   ISNULL(SUM(s.fTOTALSUM), 0) as value
                            FROM SALES s
                            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
                            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                            WHERE s.fDATE BETWEEN ? AND ?
                            AND s.fSTATE = 2
                            {area_filter}
                            {group_filter}
                            {division_filter}
                            GROUP BY FORMAT(s.fDATE, 'yyyy-MM')
                            ORDER BY period_label
                            """
                    else:
                        if group_by == 'day':
                            query = """
                            SELECT CONVERT(VARCHAR(10), s.fDATE, 120) as period_label, 
                                   ISNULL(SUM(s.fTOTALSUM), 0) as value
                            FROM SALES s
                            WHERE s.fDATE BETWEEN ? AND ?
                            AND s.fSTATE = 2
                            GROUP BY CONVERT(VARCHAR(10), s.fDATE, 120)
                            ORDER BY period_label
                            """
                        else:
                            query = """
                            SELECT FORMAT(s.fDATE, 'yyyy-MM') as period_label,
                                   ISNULL(SUM(s.fTOTALSUM), 0) as value
                            FROM SALES s
                            WHERE s.fDATE BETWEEN ? AND ?
                            AND s.fSTATE = 2
                            GROUP BY FORMAT(s.fDATE, 'yyyy-MM')
                            ORDER BY period_label
                            """
                elif metric == 'payments':
                    # Для оплат division_filter не применяется
                    needs_payment_filters = (areas and len(areas) > 0) or (groups and len(groups) > 0)
                    
                    if needs_payment_filters:
                        params_payments = [cp_from.strftime('%Y-%m-%d'), cp_to.strftime('%Y-%m-%d')]
                        if areas:
                            params_payments.extend(areas)
                        if groups:
                            params_payments.extend(groups)
                        
                        area_filter_p = area_filter.replace('csa.', 'csa.')
                        group_filter_p = group_filter.replace('c.', 'c.')
                        
                        if group_by == 'day':
                            query = f"""
                            SELECT CONVERT(VARCHAR(10), p.fDATE, 120) as period_label,
                                   ISNULL(SUM(ABS(p.fSUM)), 0) as value
                            FROM PAYMENTS p
                            INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
                            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                            WHERE p.fDATE BETWEEN ? AND ?
                            {area_filter_p}
                            {group_filter_p}
                            GROUP BY CONVERT(VARCHAR(10), p.fDATE, 120)
                            ORDER BY period_label
                            """
                        else:
                            query = f"""
                            SELECT FORMAT(p.fDATE, 'yyyy-MM') as period_label,
                                   ISNULL(SUM(ABS(p.fSUM)), 0) as value
                            FROM PAYMENTS p
                            INNER JOIN CUSTOMERS c ON p.fCUSTOMERID = c.fID
                            LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                            WHERE p.fDATE BETWEEN ? AND ?
                            {area_filter_p}
                            {group_filter_p}
                            GROUP BY FORMAT(p.fDATE, 'yyyy-MM')
                            ORDER BY period_label
                            """
                        params = params_payments
                    else:
                        if group_by == 'day':
                            query = """
                            SELECT CONVERT(VARCHAR(10), p.fDATE, 120) as period_label,
                                   ISNULL(SUM(ABS(p.fSUM)), 0) as value
                            FROM PAYMENTS p
                            WHERE p.fDATE BETWEEN ? AND ?
                            GROUP BY CONVERT(VARCHAR(10), p.fDATE, 120)
                            ORDER BY period_label
                            """
                        else:
                            query = """
                            SELECT FORMAT(p.fDATE, 'yyyy-MM') as period_label,
                                   ISNULL(SUM(ABS(p.fSUM)), 0) as value
                            FROM PAYMENTS p
                            WHERE p.fDATE BETWEEN ? AND ?
                            GROUP BY FORMAT(p.fDATE, 'yyyy-MM')
                            ORDER BY period_label
                            """
                        params = [cp_from.strftime('%Y-%m-%d'), cp_to.strftime('%Y-%m-%d')]
                elif metric == 'debt':
                    # Долг - показываем НАКОПЛЕННЫЙ долг с начала периода
                    # Используем ту же формулу что и на странице /areas:
                    # FinalDebt = DebtFromDocs - Type01 - Type02
                    # Но показываем как изменяется долг по дням (кумулятивно)
                    
                    # Для долга divisions не применяются
                    debt_area_filter = ""
                    debt_group_filter = ""
                    debt_area_params = []
                    debt_group_params = []
                    if areas and len(areas) > 0:
                        placeholders = ','.join(['?' for _ in areas])
                        debt_area_filter = f"AND csa.fSALESAREA IN ({placeholders})"
                        debt_area_params = list(areas)
                    if groups and len(groups) > 0:
                        placeholders = ','.join(['?' for _ in groups])
                        debt_group_filter = f"AND c.fGROUP IN ({placeholders})"
                        debt_group_params = list(groups)
                    
                    # Сначала получаем базовый долг (до начала периода)
                    base_debt_params = [cp_from.strftime('%Y-%m-%d')] + debt_area_params + debt_group_params
                    base_debt_query = f"""
                    SELECT 
                        ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as DebtFromDocs
                    FROM HICUSTOMERSDEBT d
                    INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                    INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
                    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                    WHERE d.fDATE < ?
                    {debt_area_filter}
                    {debt_group_filter}
                    """
                    
                    cursor.execute(base_debt_query, base_debt_params)
                    base_row = cursor.fetchone()
                    base_debt = float(base_row[0]) if base_row and base_row[0] else 0
                    
                    # Получаем Type01 и Type02 (это остатки, не зависят от даты)
                    rest_params = debt_area_params + debt_group_params
                    rest_query = f"""
                    SELECT 
                        ISNULL(SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END), 0) as Type01,
                        ISNULL(SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END), 0) as Type02
                    FROM HIRESTCUSTOMERSSUM r
                    INNER JOIN CUSTOMERS c ON r.fCUSTOMERID = c.fID
                    LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                    WHERE 1=1
                    {debt_area_filter}
                    {debt_group_filter}
                    """
                    
                    cursor.execute(rest_query, rest_params)
                    rest_row = cursor.fetchone()
                    type01 = float(rest_row[0]) if rest_row and rest_row[0] else 0
                    type02 = float(rest_row[1]) if rest_row and rest_row[1] else 0
                    
                    # Получаем изменения долга по дням в периоде
                    params_debt = [cp_from.strftime('%Y-%m-%d'), cp_to.strftime('%Y-%m-%d')] + debt_area_params + debt_group_params
                    
                    if group_by == 'day':
                        query = f"""
                        SELECT CONVERT(VARCHAR(10), d.fDATE, 120) as period_label,
                               ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as value
                        FROM HICUSTOMERSDEBT d
                        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
                        LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                        WHERE d.fDATE BETWEEN ? AND ?
                        {debt_area_filter}
                        {debt_group_filter}
                        GROUP BY CONVERT(VARCHAR(10), d.fDATE, 120)
                        ORDER BY period_label
                        """
                    else:
                        query = f"""
                        SELECT FORMAT(d.fDATE, 'yyyy-MM') as period_label,
                               ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0) as value
                        FROM HICUSTOMERSDEBT d
                        INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
                        INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
                        LEFT JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
                        WHERE d.fDATE BETWEEN ? AND ?
                        {debt_area_filter}
                        {debt_group_filter}
                        GROUP BY FORMAT(d.fDATE, 'yyyy-MM')
                        ORDER BY period_label
                        """
                    
                    cursor.execute(query, params_debt)
                    rows = cursor.fetchall()
                    
                    # Вычисляем кумулятивный долг
                    # Начинаем с базового долга минус Type01 и Type02
                    cumulative_debt = base_debt - abs(type01) - abs(type02)
                    debt_data_values = []
                    debt_labels = []
                    
                    for row in rows:
                        label = row[0]
                        daily_change = float(row[1]) if row[1] else 0
                        cumulative_debt += daily_change
                        
                        # Форматируем label
                        if group_by == 'day' and label:
                            try:
                                day = label.split('-')[2] if '-' in label else label
                                debt_labels.append(day)
                            except:
                                debt_labels.append(label)
                        elif group_by == 'month' and label:
                            month_names = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 
                                           'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
                            try:
                                month_num = int(label.split('-')[1])
                                debt_labels.append(month_names[month_num - 1])
                            except:
                                debt_labels.append(label)
                        else:
                            debt_labels.append(label or '')
                        
                        debt_data_values.append(cumulative_debt)
                    
                    # Устанавливаем labels если еще не установлены
                    if not labels_set and debt_labels:
                        chart_data['labels'] = debt_labels
                        labels_set = True
                    
                    # Добавляем dataset для долга
                    # Цвет берем из period_colors
                    bg_color = f"rgba({int(base_color[1:3], 16)}, {int(base_color[3:5], 16)}, {int(base_color[5:7], 16)}, 0.2)"
                    border_color = base_color
                    
                    # Формируем label с суффиксом периода
                    label_text = 'Долг (накопленный)'
                    if period_label_suffix:
                        label_text = f"{label_text} {period_label_suffix}"
                    
                    dataset = {
                        'label': label_text,
                        'data': debt_data_values,
                        'backgroundColor': bg_color,
                        'borderColor': border_color,
                        'borderWidth': 2,
                        'fill': chart_type == 'area',
                        'tension': 0.4
                    }
                    
                    # Добавляем пунктир для периодов сравнения
                    if line_dash:
                        dataset['borderDash'] = line_dash
                    
                    chart_data['datasets'].append(dataset)
                    continue  # Пропускаем общую обработку ниже
                else:
                    continue
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            data_values = []
            labels = []
            
            for row in rows:
                label = row[0]
                value = float(row[1]) if row[1] else 0
                
                # Форматируем label
                if group_by == 'day' and label:
                    # Показываем только день месяца
                    try:
                        day = label.split('-')[2] if '-' in label else label
                        labels.append(day)
                    except:
                        labels.append(label)
                elif group_by == 'month' and label:
                    # Показываем месяц
                    month_names = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 
                                   'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
                    try:
                        month_num = int(label.split('-')[1])
                        labels.append(month_names[month_num - 1])
                    except:
                        labels.append(label)
                else:
                    labels.append(label or '')
                
                data_values.append(value)
            
            # Устанавливаем labels только один раз (от первой метрики)
            if not labels_set and labels:
                chart_data['labels'] = labels
                labels_set = True
            
            # Добавляем dataset для метрики
            # Цвет берем из period_colors
            bg_color = f"rgba({int(base_color[1:3], 16)}, {int(base_color[3:5], 16)}, {int(base_color[5:7], 16)}, 0.2)"
            border_color = base_color
            
            # Формируем label с суффиксом периода
            label_text = metric_labels.get(metric, metric)
            if period_label_suffix:
                label_text = f"{label_text} {period_label_suffix}"
            
            dataset = {
                'label': label_text,
                'data': data_values,
                'backgroundColor': bg_color,
                'borderColor': border_color,
                'borderWidth': 2,
                'fill': chart_type == 'area',
                'tension': 0.4
            }
            
            # Добавляем пунктир для периодов сравнения
            if line_dash:
                dataset['borderDash'] = line_dash
            
            chart_data['datasets'].append(dataset)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': chart_data,
            'period': period,
            'chart_type': chart_type
        })
        
    except Exception as e:
        logger.error(f"Error getting chart data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dashboard-builder/areas-table-data')
def get_areas_table_data():
    """Получить данные для таблицы территорий (Сводка по territories)"""
    try:
        # Фильтры
        areas = request.args.getlist('areas')
        groups = request.args.getlist('groups')
        debt_groups = request.args.getlist('debt_groups')  # Отдельный фильтр групп для долга
        divisions = request.args.getlist('divisions')
        period = request.args.get('period', 'current_month')
        
        if not areas:
            return jsonify({'success': False, 'error': 'No areas selected'})
        
        # Определяем даты периода
        now = datetime.now()
        
        if period == 'current_month':
            date_from = now.replace(day=1)
            date_to = now
        elif period == 'last_month':
            first_day_this_month = now.replace(day=1)
            last_day_prev_month = first_day_this_month - timedelta(days=1)
            date_from = last_day_prev_month.replace(day=1)
            date_to = last_day_prev_month
        elif period == 'current_year':
            date_from = now.replace(month=1, day=1)
            date_to = now
        elif period == 'last_year':
            date_from = now.replace(year=now.year-1, month=1, day=1)
            date_to = now.replace(year=now.year-1, month=12, day=31)
        else:
            date_from = now.replace(day=1)
            date_to = now
        
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получить фильтры исключённых клиентов
        excluded_filter, excluded_params = get_excluded_filter_sql()
        
        # Дополнительные даты для расчётов
        today = now.strftime('%Y-%m-%d')
        
        # Прошлый год - те же даты
        import calendar
        last_year = now.year - 1
        last_year_month = now.month
        last_year_day = min(now.day, calendar.monthrange(last_year, last_year_month)[1])
        last_year_from = datetime(last_year, last_year_month, 1)
        last_year_to = datetime(last_year, last_year_month, last_year_day)
        
        # Расчёт прогноза: дней прошло и всего дней в месяце
        days_elapsed = now.day
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        
        # Планы по территориям (продажи и долг)
        sales_plans = {
            '101': 6_500_000, '102': 3_500_000, '103': 4_000_000, '104': 3_000_000,
            '105': 5_500_000, '106': 6_500_000, '107/1': 4_000_000, '110': 3_500_000, 
            '108': 4_500_000, '108/1': 4_000_000, '109': 3_000_000
        }
        debt_plans = {
            '101': 5_500_000, '102': 5_000_000, '103': 4_500_000, '104': 2_300_000,
            '105': 3_000_000, '106': 5_000_000, '107/1': 3_500_000, '110': 3_500_000, 
            '108': 4_000_000, '108/1': 3_500_000, '109': 2_500_000
        }
        
        result_data = []
        
        # Для каждой территории получаем данные
        for area_code in areas:
            area_data = {
                'code': area_code,
                'name': '',
                'sales': 0,
                'sales_today': 0,
                'sales_forecast': 0,
                'sales_last_year': 0,
                'sales_plan': sales_plans.get(area_code, 3_000_000),
                'sales_percent': 0,
                'payments': 0,
                'debt': 0,
                'debt_plan': debt_plans.get(area_code, 3_000_000),
                'debt_percent': 0
            }
            
            # Получаем название территории из таблицы TREES
            cursor.execute("SELECT fCAPTION FROM TREES WHERE fTREEID = 'SArea' AND fCODE = ?", [area_code])
            area_row = cursor.fetchone()
            if area_row:
                area_data['name'] = (area_row[0] or '').strip()
            
            # Фильтры
            group_filter = ""
            group_params = []
            if groups and len(groups) > 0:
                placeholders = ','.join(['?' for _ in groups])
                group_filter = f"AND c.fGROUP IN ({placeholders})"
                group_params = list(groups)
            
            # Отдельный фильтр групп для долга
            debt_group_filter = ""
            debt_group_params = []
            if debt_groups and len(debt_groups) > 0:
                placeholders = ','.join(['?' for _ in debt_groups])
                debt_group_filter = f"AND c.fGROUP IN ({placeholders})"
                debt_group_params = list(debt_groups)
            
            division_filter = ""
            division_params = []
            if divisions and len(divisions) > 0:
                placeholders = ','.join(['?' for _ in divisions])
                division_filter = f"AND s.fDIVISION IN ({placeholders})"
                division_params = list(divisions)
            
            # Продажи
            sales_query = f"""
            SELECT ISNULL(SUM(s.fTOTALSUM), 0)
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE s.fDATE BETWEEN ? AND ?
            AND s.fSTATE = 2
            AND csa.fSALESAREA = ?
            {excluded_filter}
            {group_filter}
            {division_filter}
            """
            sales_params = [date_from.strftime('%Y-%m-%d'), date_to.strftime('%Y-%m-%d'), area_code] + list(excluded_params) + group_params + division_params
            cursor.execute(sales_query, sales_params)
            sales_row = cursor.fetchone()
            area_data['sales'] = float(sales_row[0]) if sales_row and sales_row[0] else 0
            
            # Продажи за сегодня
            today_params = [today, today, area_code] + list(excluded_params) + group_params + division_params
            cursor.execute(sales_query, today_params)
            today_row = cursor.fetchone()
            area_data['sales_today'] = float(today_row[0]) if today_row and today_row[0] else 0
            
            # Продажи за прошлый год (те же даты месяца)
            last_year_params = [last_year_from.strftime('%Y-%m-%d'), last_year_to.strftime('%Y-%m-%d'), area_code] + list(excluded_params) + group_params + division_params
            cursor.execute(sales_query, last_year_params)
            last_year_row = cursor.fetchone()
            area_data['sales_last_year'] = float(last_year_row[0]) if last_year_row and last_year_row[0] else 0
            
            # Прогноз на месяц = (продажи за период / дней прошло) * дней в месяце
            if days_elapsed > 0 and area_data['sales'] > 0:
                daily_avg = area_data['sales'] / days_elapsed
                area_data['sales_forecast'] = daily_avg * days_in_month
            else:
                area_data['sales_forecast'] = 0
            
            # Оплаты (из HICUSTOMERSDEBT как на странице /areas)
            payments_query = f"""
            SELECT ISNULL(SUM(CASE WHEN h.fDBCR = 'C' THEN h.fSUM ELSE 0 END), 0)
            FROM HICUSTOMERSDEBT h
            INNER JOIN DOCUMENTS d ON h.fDEBTDOCISN = d.fISN
            INNER JOIN CUSTOMERS c ON d.fCUSTOMERID = c.fID
            INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE h.fDATE BETWEEN ? AND ?
            AND h.fOP = 'PAY'
            AND csa.fSALESAREA = ?
            {excluded_filter}
            {group_filter}
            """
            payments_params = [date_from.strftime('%Y-%m-%d'), date_to.strftime('%Y-%m-%d'), area_code] + list(excluded_params) + group_params
            cursor.execute(payments_query, payments_params)
            payments_row = cursor.fetchone()
            area_data['payments'] = float(payments_row[0]) if payments_row and payments_row[0] else 0
            
            # Долг (текущий) минус ABS(Type01) и ABS(Type02)
            # Долг кумулятивный - без фильтра по дате, как на странице /areas
            # Применяем debt_group_filter (отдельный фильтр групп для долга)
            # Формула: Debt = (Debit - Credit) - ABS(Type01) - ABS(Type02)
            debt_query = f"""
            SELECT 
                ISNULL(SUM(CASE WHEN d.fDBCR = 'D' THEN d.fSUM ELSE -d.fSUM END), 0)
                - ABS(ISNULL((
                    SELECT SUM(CASE WHEN r.fTYPE = '01' THEN r.fSUM ELSE 0 END)
                    FROM HIRESTCUSTOMERSSUM r
                    INNER JOIN CUSTOMERS c2 ON r.fCUSTOMERID = c2.fID
                    INNER JOIN CUSTOMERSALESAREAS csa2 ON c2.fID = csa2.fCUSTOMERID
                    WHERE csa2.fSALESAREA = ?
                    {excluded_filter.replace('c.', 'c2.')}
                    {debt_group_filter.replace('c.fGROUP', 'c2.fGROUP')}
                ), 0))
                - ABS(ISNULL((
                    SELECT SUM(CASE WHEN r.fTYPE = '02' THEN r.fSUM ELSE 0 END)
                    FROM HIRESTCUSTOMERSSUM r
                    INNER JOIN CUSTOMERS c3 ON r.fCUSTOMERID = c3.fID
                    INNER JOIN CUSTOMERSALESAREAS csa3 ON c3.fID = csa3.fCUSTOMERID
                    WHERE csa3.fSALESAREA = ?
                    {excluded_filter.replace('c.', 'c3.')}
                    {debt_group_filter.replace('c.fGROUP', 'c3.fGROUP')}
                ), 0))
            FROM HICUSTOMERSDEBT d
            INNER JOIN DOCUMENTS doc ON d.fDEBTDOCISN = doc.fISN
            INNER JOIN CUSTOMERS c ON doc.fCUSTOMERID = c.fID
            INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
            WHERE csa.fSALESAREA = ?
            {excluded_filter}
            {debt_group_filter}
            """
            debt_params = [area_code] + list(excluded_params) + debt_group_params + [area_code] + list(excluded_params) + debt_group_params + [area_code] + list(excluded_params) + debt_group_params
            cursor.execute(debt_query, debt_params)
            debt_row = cursor.fetchone()
            area_data['debt'] = float(debt_row[0]) if debt_row and debt_row[0] else 0
            
            # Расчёт процентов выполнения планов
            if area_data['sales_plan'] > 0:
                area_data['sales_percent'] = round((area_data['sales'] / area_data['sales_plan']) * 100, 1)
            if area_data['debt_plan'] > 0:
                area_data['debt_percent'] = round((area_data['debt'] / area_data['debt_plan']) * 100, 1)
            
            # Статистика маршрутов: planned и visited
            route_query = """
            WITH AreaCustomers AS (
                SELECT fCUSTOMERID 
                FROM CUSTOMERSALESAREAS 
                WHERE fSALESAREA = ?
            ),
            PlannedVisits AS (
                SELECT l.fCUSTOMERID, CAST(d.fDATE as DATE) as VisitDate
                FROM DOCUMENTS d
                JOIN PLANNEDROUTESLIST l ON d.fISN = l.fISN
                WHERE d.fDOCTYPE = 10
                  AND d.fDATE >= ? AND d.fDATE <= ?
                  AND l.fCUSTOMERID IN (SELECT fCUSTOMERID FROM AreaCustomers)
            ),
            ActualVisits AS (
                SELECT a.fCUSTOMERID, CAST(a.fDATE as DATE) as VisitDate
                FROM ACTUALROUTES a
                WHERE a.fDATE >= ? AND a.fDATE <= ?
                  AND a.fCUSTOMERID IN (SELECT fCUSTOMERID FROM AreaCustomers)
            )
            SELECT
                (SELECT COUNT(*) FROM PlannedVisits) as PlannedCount,
                (SELECT COUNT(*) FROM ActualVisits) as VisitedCount
            """
            route_params = [area_code, date_from.strftime('%Y-%m-%d'), date_to.strftime('%Y-%m-%d'), 
                           date_from.strftime('%Y-%m-%d'), date_to.strftime('%Y-%m-%d')]
            cursor.execute(route_query, route_params)
            route_row = cursor.fetchone()
            area_data['visits_planned'] = route_row[0] or 0 if route_row else 0
            area_data['visits_actual'] = route_row[1] or 0 if route_row else 0
            
            result_data.append(area_data)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': result_data,
            'period': period,
            'date_from': date_from.strftime('%Y-%m-%d'),
            'date_to': date_to.strftime('%Y-%m-%d')
        })
        
    except Exception as e:
        logger.error(f"Error getting areas table data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================
# ЗАПУСК ПРИЛОЖЕНИЯ
# =============================================

if __name__ == '__main__':
    print("=" * 80)
    print("Sales Dashboard v2.0 starting...")
    print("=" * 80)
    print()
    db_name = os.environ.get('SALES_DB', 'SalesManagement')
    print(f"Database: {db_name}")
    print("Server: http://localhost:5000")
    print()
    print("Available pages:")
    print("  - http://localhost:5000/          - Dashboard")
    print("  - http://localhost:5000/managers  - Managers")
    print("  - http://localhost:5000/groups    - Groups Statistics")
    print("  - http://localhost:5000/distributors - Distributor Management")
    print("  - http://localhost:5000/areas     - Territories")
    print("  - http://localhost:5000/plans     - Plans")
    print("  - http://localhost:5000/ai-assistant - AI Problem Analysis")
    print("  - http://localhost:5000/dashboard-builder - Dashboard Builder")
    print("  - http://localhost:5000/settings  - Settings")
    print("  - http://localhost:5000/test-db   - Test DB")
    print()
    print("=" * 80)
    
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)


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
# import io
# from openpyxl import Workbook
# from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
# from openpyxl.utils import get_column_letter

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
        db_name = os.environ.get('SALES_DB', 'SalesManagement')
        self.connection_string = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            "SERVER=192.168.1.3;"
            f"DATABASE={db_name};"
            "UID=garni;"
            "PWD=garni2023;"
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
        product_groups_filter, product_groups_params = get_product_groups_filter_sql()
        
        query_revenue = f"""
            SELECT ISNULL(SUM(s.fTOTALSUM), 0) as TotalRevenue
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
        """
        
        params_current = (current_start, current_end) + excluded_params + product_groups_params
        params_prev = (prev_start, prev_end) + excluded_params + product_groups_params
        params_last_year = (last_year_start, last_year_end) + excluded_params + product_groups_params
        params_ten_years = (ten_years_ago_start, ten_years_ago_end) + excluded_params + product_groups_params
        
        current_revenue = db.execute_query(query_revenue, params_current)
        prev_revenue = db.execute_query(query_revenue, params_prev)
        last_year_revenue = db.execute_query(query_revenue, params_last_year)
        ten_years_revenue = db.execute_query(query_revenue, params_ten_years)
        
        # Количество продаж
        query_sales_count = f"""
            SELECT COUNT(s.fISN) as SalesCount
            FROM SALES s
            INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
            WHERE s.fDATE >= ? AND s.fDATE < ?
            AND s.fSTATE = 2
            {excluded_filter}
            {product_groups_filter}
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
            {product_groups_filter}
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
            {product_groups_filter}
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
                    AND s.fSALESAREA = ?
                    AND s.fDATE >= ?
                    AND s.fDATE <= ?
                    AND s.fSTATE = 2
                    {excluded_filter}
                    {product_groups_filter}
                    {division_filter}
                    {sales_group_filter}
            """
            
            sales_params = (area_code, area_code, date_from, date_to) + excluded_params + product_groups_params + division_params + sales_group_params
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
            prev_month_sales_params = (area_code, area_code, prev_month_from_str, prev_month_to_str) + excluded_params + product_groups_params + division_params + sales_group_params
            cursor.execute(query_sales, prev_month_sales_params)
            prev_month_row = cursor.fetchone()
            
            area_data['PrevMonthSales'] = float(prev_month_row.TotalSales) if prev_month_row and prev_month_row.TotalSales else 0
            
            # 5a. Долг за прошлый месяц (с формулой Type01/Type02)
            # Используем ТЕКУЩИЙ долг (без фильтра по датам), так как долг кумулятивный
            area_data['PrevMonthDebt'] = area_data['Debt']  # Копируем текущий долг
            
            # 6. Получить данные за прошлый год (те же даты, но год назад)
            last_year_sales_params = (area_code, area_code, last_year_from_str, last_year_to_str) + excluded_params + product_groups_params + division_params + sales_group_params
            cursor.execute(query_sales, last_year_sales_params)
            last_year_row = cursor.fetchone()
            
            area_data['LastYearSales'] = float(last_year_row.TotalSales) if last_year_row and last_year_row.TotalSales else 0
            
            # 6a. Долг за прошлый год (с формулой Type01/Type02)
            # Используем ТЕКУЩИЙ долг (без фильтра по датам), так как долг кумулятивный
            area_data['LastYearDebt'] = area_data['Debt']  # Копируем текущий долг
        
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

@app.route('/groups')
def groups_page():
    """Страница групп (дистрибьюторы)"""
    return render_template('groups.html')

@app.route('/distributors')
def distributors_page():
    """Страница управления дистрибьюторами"""
    return render_template('distributors.html')

@app.route('/areas')
def areas_page():
    """Страница с территориями"""
    return render_template('areas.html')

@app.route('/plans')
def plans_page():
    """Страница планов продаж и кредитов по территориям"""
    return render_template('plans.html')

@app.route('/api/generate-plans')
def generate_plans():
    """Генерация планов продаж и кредитов с учетом сезонности"""
    try:
        target_month = int(request.args.get('month', datetime.now().month))
        target_year = int(request.args.get('year', datetime.now().year))
        growth_percent = float(request.args.get('growth', 10))  # Параметр роста из запроса
        
        # Получить параметры фильтров
        raw_groups = request.args.get('groups', '').strip()
        selected_groups = [grp.strip() for grp in raw_groups.split(',') if grp.strip()]
        
        # Коэффициенты сезонности (на основе анализа данных)
        # Синхронизировано с frontend (templates/plans.html)
        seasonality = {
            1: 0.53, 2: 0.67, 3: 0.80, 4: 0.86,
            5: 1.14, 6: 1.31, 7: 1.49, 8: 1.43,
            9: 1.10, 10: 1.02, 11: 0.88, 12: 0.93
        }
        
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
        
        # 1. Получить средние продажи (Turnover) за последние 12 месяцев по территориям
        query_sales = f"""
        SELECT 
            csa.fSALESAREA as area_code,
            ISNULL(SUM(s.fTOTALSUM), 0) / 12.0 as avg_monthly_sales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        INNER JOIN CUSTOMERSALESAREAS csa ON c.fID = csa.fCUSTOMERID
        WHERE s.fSALESAREA = csa.fSALESAREA
            AND s.fDATE >= DATEADD(MONTH, -12, GETDATE())
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
            {group_clause}
        GROUP BY csa.fSALESAREA
        """
        
        debt_params = excluded_params + group_params
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
        WHERE d.fDATE >= DATEADD(MONTH, -13, GETDATE())
            {excluded_filter}
            {group_clause}
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
            {group_clause}
        GROUP BY csa.fSALESAREA
        """
        
        rest_params = excluded_params + group_params
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
            
        today = datetime.now()
        current_year = today.year
        current_month = today.month
        
        for area_code, current_balance in current_debts.items():
            if area_code not in area_stats:
                area_stats[area_code] = {'avg_sales': 0, 'avg_debt': 0, 'type01': 0, 'type02': 0}
                
            balances = []
            running_balance = current_balance
            
            # Точка 0: Текущий баланс (конец текущего месяца - прогноз)
            balances.append(running_balance)
            
            curr_y, curr_m = current_year, current_month
            
            # Идем назад на 11 месяцев
            for i in range(11):
                # Изменение за текущий рассматриваемый месяц
                change = changes_map.get(area_code, {}).get((curr_y, curr_m), 0)
                
                # Баланс на конец предыдущего месяца = Баланс(конец этого) - Изменение(этот)
                prev_balance = running_balance - change
                balances.append(prev_balance)
                running_balance = prev_balance
                
                # Сдвигаем месяц назад
                curr_m -= 1
                if curr_m == 0:
                    curr_m = 12
                    curr_y -= 1
            
            # Среднее за 12 точек
            avg_debt = sum(balances) / len(balances)
            area_stats[area_code]['avg_debt'] = avg_debt
        
        for row in rest_results:
            if row.area_code in area_stats:
                area_stats[row.area_code]['type01'] = float(row.Type01) if row.Type01 else 0
                area_stats[row.area_code]['type02'] = float(row.Type02) if row.Type02 else 0
        
        plans = {}
        season_coeff = seasonality.get(target_month, 1.0)
        growth_factor = 1 + (growth_percent / 100)  # Преобразуем 10% → 1.10, 20% → 1.20
        
        for area_code, stats in area_stats.items():
            avg_sales = stats['avg_sales']
            avg_debt = stats['avg_debt']
            type01 = stats['type01']
            type02 = stats['type02']
            
            # ФОРМУЛА: Средний ДОЛГ = Средний кумулятивный баланс - ВОЗВРАТЫ - ПРЕДОПЛАТА
            avg_debt_adjusted = avg_debt - abs(type01) - abs(type02)
            
            # Применяем сезонный коэффициент и настраиваемый рост
            # Округляем до 10,000
            plan_sales = int(round(avg_sales * season_coeff * growth_factor / 10000) * 10000)
            # План по кредиту = Средний Долг × Сезонность × Рост (округлено до 10,000)
            plan_credit = int(round(avg_debt_adjusted * season_coeff * growth_factor / 10000) * 10000)
            
            plans[area_code] = {
                'sales': plan_sales,
                'credit': plan_credit,
                'seasonality': season_coeff,
                'avg_sales': round(avg_sales, 0),
                'avg_credit': round(avg_debt_adjusted, 0)  # Средний долг за 12 месяцев
            }
        
        conn.close()
        
        return jsonify({
            'success': True,
            'data': plans,
            'month': target_month,
            'year': target_year,
            'seasonality_coefficient': season_coeff
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
        
        # Получить продажи по месяцам за указанный период
        query = f"""
        SELECT 
            MONTH(s.fDATE) as month_num,
            ISNULL(SUM(s.fTOTALSUM), 0) as total_sales
        FROM SALES s
        INNER JOIN CUSTOMERS c ON s.fCUSTOMERID = c.fID
        WHERE s.fDATE >= DATEADD(YEAR, -{history_years}, GETDATE())
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
        
        # Рассчитать средний уровень продаж
        total_sum = sum(monthly_sales.values())
        average_monthly = total_sum / len(monthly_sales)
        
        # Рассчитать коэффициенты сезонности для каждого месяца
        seasonality_coeffs = {}
        for month in range(1, 13):
            if month in monthly_sales:
                # Коэффициент = продажи месяца / средние продажи
                coeff = monthly_sales[month] / average_monthly if average_monthly > 0 else 1.0
                seasonality_coeffs[month] = round(coeff, 2)
            else:
                # Если данных нет, используем 1.0 (средний уровень)
                seasonality_coeffs[month] = 1.0
        
        cursor.close()
        conn.close()
        
        logger.info(f"Рассчитаны коэффициенты сезонности за {history_years} лет: {seasonality_coeffs}")
        
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
@app.route('/api/settings/groups-with-stats')
def get_groups_with_stats():
    """Получить группы с количеством клиентов и продажами"""
    try:
        app.logger.info("[Groups] Loading groups with stats...")
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Получаем текущий месяц и год
        current_date = datetime.now()
        current_year = current_date.year
        current_month = current_date.month
        
        # Даты для текущего месяца
        date_from = f"{current_year}-{current_month:02d}-01"
        # Последний день текущего месяца
        if current_month == 12:
            next_month = 1
            next_year = current_year + 1
        else:
            next_month = current_month + 1
            next_year = current_year
        date_to = (datetime(next_year, next_month, 1) - timedelta(days=1)).strftime('%Y-%m-%d')
        
        query = """
            SELECT 
                c.fGROUP,
                COUNT(DISTINCT c.fID) as customerCount,
                ISNULL(SUM(sd.fSUMMA), 0) as totalSales,
                ISNULL(SUM(CASE WHEN p.fTYPE IN ('01', '02') THEN ABS(p.fSUM) ELSE 0 END), 0) as totalPayments,
                ISNULL(SUM(sd.fSUMMA), 0) - ISNULL(SUM(CASE WHEN p.fTYPE IN ('01', '02') THEN ABS(p.fSUM) ELSE 0 END), 0) as totalDebt,
                ISNULL(AVG(sd.fSUMMA), 0) as avgOrderSize
            FROM CUSTOMERS c
            LEFT JOIN SALEDOC s ON c.fID = s.fCUSTID
                AND s.fDATE >= ?
                AND s.fDATE <= ?
                AND s.fFLAG = 0
            LEFT JOIN SALEDOCDETAILS sd ON s.fID = sd.fSALEDOCID
            LEFT JOIN PAYMENTS p ON c.fID = p.fCUSTID
                AND p.fDATE >= ?
                AND p.fDATE <= ?
            WHERE c.fGROUP IS NOT NULL AND c.fGROUP != ''
            GROUP BY c.fGROUP
            ORDER BY totalSales DESC
        """
        cursor.execute(query, (date_from, date_to, date_from, date_to))
        
        groups = []
        for row in cursor.fetchall():
            total_sales = float(row.totalSales or 0)
            total_payments = float(row.totalPayments or 0)
            groups.append({
                'fGROUP': row.fGROUP,
                'customerCount': row.customerCount,
                'totalSales': total_sales,
                'totalPayments': total_payments,
                'totalDebt': float(row.totalDebt or 0),
                'avgOrderSize': float(row.avgOrderSize or 0),
                'paymentRate': (total_payments / total_sales * 100) if total_sales > 0 else 0,
                'isExcluded': False,  # Будет обновлено на клиенте
                'assignedManager': ''  # Будет обновлено на клиенте
            })
        
        conn.close()
        app.logger.info(f"[Groups] Loaded {len(groups)} groups with sales data")
        return jsonify({'success': True, 'data': groups, 'period': {'from': date_from, 'to': date_to}})
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
    print("  - http://localhost:5000/settings  - Settings")
    print("  - http://localhost:5000/test-db   - Test DB")
    print()
    print("=" * 80)
    
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)


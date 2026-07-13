/* dashboard_v2 — page script (extracted from inline <script>). */
    function dashboardData() {
        return {
            loading: true,
            loadingDebts: true,
            showAdvancedFilters: false,
            currentTime: new Date().toLocaleString('ru-RU'),
            dateFrom: '',
            dateTo: '',
            selectedPeriodText: 'Текущий месяц',
            currentDaysInMonth: 0,
            
            // Filters
            selectedArea: '',
            selectedManager: '',
            selectedGroup: '',
            
            // Search
            searchQuery: '',
            searchResults: [],
            searchTimeout: null,
            
            // Data
            stats: {
                total_revenue: { value: 0, growth: 0, growth_yoy: 0, growth_10y: 0, prev_month: 0, last_year: 0, ten_years_ago: 0 },
                sales_count: { value: 0, growth: 0, growth_yoy: 0, growth_10y: 0, prev_month: 0, last_year: 0, ten_years_ago: 0 },
                avg_check: { value: 0, prev_month: 0, last_year: 0 },
                active_customers: { value: 0, prev_month: 0, last_year: 0 },
                today_revenue: { value: 0, last_year: 0 },
                today_sales: { value: 0, last_year: 0 },
                today_avg_check: { value: 0, last_year: 0 },
                today_customers: { value: 0, last_year: 0 },
                monthly_forecast: { value: 0, days_passed: 0, total_days: 0, current_sales: 0 },
                top_manager: { name: 'Загрузка...', sales: 0 }
            },
            debts: {
                debt_from_documents: 0,
                type01: 0,
                type02: 0,
                final_debt: 0,
                debt_customers_count: 0,
                prev_month_debt: 0,
                last_year_debt: 0,
                top_debtors: []
            },
            summary: {
                managersCount: 0,
                areasCount: 0,
                customersCount: 0,
                groupsCount: 0
            },
            
            // Filter data
            salesAreas: [],
            managers: [],
            customerGroups: [],
            
            // Charts
            salesChart: null,
            managersChart: null,
            tenYearsChart: null,
            debtsChart: null,
            topDebtorsChart: null,
            
            // Territory filter for dashboard
            showAreasPanel: false,
            availableAreas: [],
            selectedAreasForFilter: [],
            areaSearchQuery: '',
            savingAreas: false,
            
            // Customer groups filter for dashboard
            showGroupsPanel: false,
            availableGroups: [],
            selectedGroupsForFilter: [],
            groupSearchQuery: '',
            savingGroups: false,
            
            // Widget settings
            showWidgetSettings: false,
            showAddWidget: false,
            editMode: false,
            widgets: [],
            sortableInstance: null,
            
            // Available widget types for adding
            availableWidgetTypes: [
                { id: 'total_revenue', title: 'Общая выручка', icon: 'fa-dollar-sign', color: '#0d6efd' },
                { id: 'sales_count', title: 'Количество продаж', icon: 'fa-shopping-cart', color: '#198754' },
                { id: 'avg_check', title: 'Средний чек', icon: 'fa-receipt', color: '#0dcaf0' },
                { id: 'active_customers', title: 'Активные клиенты', icon: 'fa-users', color: '#ffc107' },
                { id: 'today_revenue', title: 'Выручка сегодня', icon: 'fa-calendar-check', color: '#0d6efd' },
                { id: 'today_sales', title: 'Продажи сегодня', icon: 'fa-shopping-bag', color: '#198754' },
                { id: 'today_avg_check', title: 'Средний чек сегодня', icon: 'fa-receipt', color: '#0dcaf0' },
                { id: 'today_customers', title: 'Клиенты сегодня', icon: 'fa-user-check', color: '#ffc107' },
                { id: 'monthly_forecast', title: 'Прогноз на месяц', icon: 'fa-chart-line', color: '#6f42c1' },
                { id: 'total_debt', title: 'Общая задолженность', icon: 'fa-hand-holding-usd', color: '#dc3545' }
            ],

            init() {
                // Установить текущий месяц по умолчанию
                this.setThisMonth();
                this.loadAvailableAreas();
                this.loadSelectedAreasFilter();
                this.loadAvailableGroups();
                this.loadSelectedGroupsFilter();
                this.loadWidgets();
                this.loadFilterData();
                this.loadSummary();
                this.loadCharts();
                this.loadDebts();
                
                // Обновление времени каждую секунду
                setInterval(() => {
                    this.currentTime = new Date().toLocaleString('ru-RU');
                }, 1000);

                // Автообновление данных каждые 5 минут
                setInterval(() => {
                    this.applyFilters();
                }, 300000);
            },
            
            async initDashboard() {
                // Дополнительная инициализация после загрузки виджетов
                await this.loadWidgets();
            },
            
            // ===== Widget Management =====
            async loadWidgets() {
                try {
                    const res = await fetch('/api/dashboard/widgets');
                    const data = await res.json();
                    if (data.success) {
                        this.widgets = data.widgets || [];
                    }
                } catch (error) {
                    console.error('Error loading widgets:', error);
                }
            },
            
            async saveWidgets() {
                try {
                    const res = await fetch('/api/dashboard/widgets', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ widgets: this.widgets })
                    });
                    const data = await res.json();
                    if (!data.success) {
                        console.error('Error saving widgets:', data.error);
                    }
                } catch (error) {
                    console.error('Error saving widgets:', error);
                }
            },
            
            toggleWidgetVisibility(widgetId) {
                const widget = this.widgets.find(w => w.id === widgetId);
                if (widget) {
                    widget.visible = !widget.visible;
                    this.saveWidgets();
                }
            },
            
            isWidgetVisible(widgetId) {
                const widget = this.widgets.find(w => w.id === widgetId);
                return widget ? widget.visible : true;
            },
            
            showAllWidgets() {
                this.widgets.forEach(w => w.visible = true);
                this.saveWidgets();
            },
            
            hideAllWidgets() {
                this.widgets.forEach(w => w.visible = false);
                this.saveWidgets();
            },
            
            toggleEditMode() {
                this.editMode = !this.editMode;
                if (this.editMode) {
                    this.$nextTick(() => {
                        this.initSortable();
                    });
                } else {
                    this.destroySortable();
                }
            },
            
            initSortable() {
                const container = document.getElementById('widgets-container');
                if (container && typeof Sortable !== 'undefined') {
                    this.sortableInstance = Sortable.create(container, {
                        animation: 150,
                        ghostClass: 'sortable-ghost',
                        dragClass: 'sortable-drag',
                        handle: '.widget-drag-handle',
                        onEnd: (evt) => {
                            // Обновить порядок виджетов
                            const items = container.querySelectorAll('[data-widget-id]');
                            items.forEach((item, index) => {
                                const widgetId = item.dataset.widgetId;
                                const widget = this.widgets.find(w => w.id === widgetId);
                                if (widget) {
                                    widget.order = index + 1;
                                }
                            });
                            this.widgets.sort((a, b) => a.order - b.order);
                            this.saveWidgets();
                        }
                    });
                }
            },
            
            destroySortable() {
                if (this.sortableInstance) {
                    this.sortableInstance.destroy();
                    this.sortableInstance = null;
                }
            },
            
            async resetWidgets() {
                if (confirm('Сбросить все настройки виджетов?')) {
                    try {
                        const res = await fetch('/api/dashboard/widgets/reset', {
                            method: 'POST'
                        });
                        const data = await res.json();
                        if (data.success) {
                            this.widgets = data.widgets;
                            location.reload();
                        }
                    } catch (error) {
                        console.error('Error resetting widgets:', error);
                    }
                }
            },
            
            // ===== Filters =====
            async loadFilterData() {
                try {
                    // Load sales areas
                    const areasRes = await fetch('/api/sales-areas?date_from=' + this.dateFrom + '&date_to=' + this.dateTo);
                    const areasData = await areasRes.json();
                    if (areasData.success) {
                        this.salesAreas = areasData.data.map(a => ({ code: a.code, name: a.name }));
                    }
                    
                    // Load managers
                    const managersRes = await fetch('/api/managers?date_from=' + this.dateFrom + '&date_to=' + this.dateTo);
                    const managersData = await managersRes.json();
                    if (managersData.success) {
                        this.managers = managersData.data;
                    }
                    
                    // Load groups
                    const groupsRes = await fetch('/api/settings/groups');
                    const groupsData = await groupsRes.json();
                    if (groupsData.success) {
                        this.customerGroups = groupsData.data;
                    }
                } catch (error) {
                    console.error('Error loading filter data:', error);
                }
            },
            
            // ===== Territory Filter Methods =====
            async loadAvailableAreas() {
                try {
                    const res = await fetch('/api/sales-areas-list');
                    const data = await res.json();
                    if (data.success) {
                        this.availableAreas = data.data;
                    }
                } catch (error) {
                    console.error('Error loading available areas:', error);
                }
            },
            
            async loadSelectedAreasFilter() {
                try {
                    const res = await fetch('/api/dashboard/areas');
                    const data = await res.json();
                    if (data.success) {
                        this.selectedAreasForFilter = data.areas || [];
                    }
                } catch (error) {
                    console.error('Error loading selected areas filter:', error);
                }
            },
            
            get filteredAvailableAreas() {
                if (!this.areaSearchQuery) return this.availableAreas;
                const query = this.areaSearchQuery.toLowerCase();
                return this.availableAreas.filter(a => 
                    a.name.toLowerCase().includes(query) || a.code.toLowerCase().includes(query)
                );
            },
            
            toggleAreaFilter(code) {
                const idx = this.selectedAreasForFilter.indexOf(code);
                if (idx >= 0) {
                    this.selectedAreasForFilter.splice(idx, 1);
                } else {
                    this.selectedAreasForFilter.push(code);
                }
            },
            
            selectAllAreas() {
                this.selectedAreasForFilter = this.availableAreas.map(a => a.code);
            },
            
            clearAllAreas() {
                this.selectedAreasForFilter = [];
            },
            
            async saveAreasFilter() {
                this.savingAreas = true;
                try {
                    const res = await fetch('/api/dashboard/areas', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ areas: this.selectedAreasForFilter })
                    });
                    const data = await res.json();
                    if (data.success) {
                        this.showAreasPanel = false;
                        this.applyFilters();
                    }
                } catch (error) {
                    console.error('Error saving areas filter:', error);
                }
                this.savingAreas = false;
            },
            
            // ===== Customer Groups Filter Methods =====
            async loadAvailableGroups() {
                try {
                    const res = await fetch('/api/customer-groups');
                    const data = await res.json();
                    if (data.success) {
                        this.availableGroups = data.data;
                    }
                } catch (error) {
                    console.error('Error loading available groups:', error);
                }
            },
            
            async loadSelectedGroupsFilter() {
                try {
                    const res = await fetch('/api/dashboard/groups');
                    const data = await res.json();
                    if (data.success) {
                        this.selectedGroupsForFilter = data.groups || [];
                    }
                } catch (error) {
                    console.error('Error loading selected groups filter:', error);
                }
            },
            
            get filteredAvailableGroups() {
                if (!this.groupSearchQuery) return this.availableGroups;
                const query = this.groupSearchQuery.toLowerCase();
                return this.availableGroups.filter(g => 
                    g.name.toLowerCase().includes(query) || g.code.toLowerCase().includes(query)
                );
            },
            
            toggleGroupFilter(code) {
                const idx = this.selectedGroupsForFilter.indexOf(code);
                if (idx >= 0) {
                    this.selectedGroupsForFilter.splice(idx, 1);
                } else {
                    this.selectedGroupsForFilter.push(code);
                }
            },
            
            selectAllGroups() {
                this.selectedGroupsForFilter = this.availableGroups.map(g => g.code);
            },
            
            clearAllGroups() {
                this.selectedGroupsForFilter = [];
            },
            
            async saveGroupsFilter() {
                this.savingGroups = true;
                try {
                    const res = await fetch('/api/dashboard/groups', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ groups: this.selectedGroupsForFilter })
                    });
                    const data = await res.json();
                    if (data.success) {
                        this.showGroupsPanel = false;
                        this.applyFilters();
                    }
                } catch (error) {
                    console.error('Error saving groups filter:', error);
                }
                this.savingGroups = false;
            },
            
            async loadSummary() {
                try {
                    // Managers count
                    const managersRes = await fetch('/api/managers?date_from=' + this.dateFrom + '&date_to=' + this.dateTo);
                    const managersData = await managersRes.json();
                    if (managersData.success) {
                        this.summary.managersCount = managersData.data.length;
                    }
                    
                    // Areas count
                    const areasRes = await fetch('/api/sales-areas?date_from=' + this.dateFrom + '&date_to=' + this.dateTo);
                    const areasData = await areasRes.json();
                    if (areasData.success) {
                        this.summary.areasCount = areasData.data.length;
                    }
                    
                    // Groups count
                    const groupsRes = await fetch('/api/groups');
                    const groupsData = await groupsRes.json();
                    if (groupsData.success) {
                        this.summary.groupsCount = groupsData.data.length;
                        // Sum up customer count
                        this.summary.customersCount = groupsData.data.reduce((sum, g) => sum + (g.CustomerCount || 0), 0);
                    }
                } catch (error) {
                    console.error('Error loading summary:', error);
                }
            },
            
            applyFilters() {
                this.updateDaysCount();
                this.loadStats();
                this.loadCharts();
                this.loadDebts();
                this.loadSummary();
            },
            
            updateDaysCount() {
                if (this.dateFrom && this.dateTo) {
                    const from = new Date(this.dateFrom);
                    const to = new Date(this.dateTo);
                    const diffTime = Math.abs(to - from);
                    this.currentDaysInMonth = Math.ceil(diffTime / (1000 * 60 * 60 * 24)) + 1;
                } else {
                    this.currentDaysInMonth = new Date().getDate();
                }
            },
            
            resetFilters() {
                this.selectedArea = '';
                this.selectedManager = '';
                this.selectedGroup = '';
                this.setThisMonth();
            },

            // ===== Search =====
            debounceSearch() {
                clearTimeout(this.searchTimeout);
                if (this.searchQuery.length < 2) {
                    this.searchResults = [];
                    return;
                }
                
                this.searchTimeout = setTimeout(() => {
                    this.performSearch();
                }, 300);
            },
            
            async performSearch() {
                try {
                    const query = encodeURIComponent(this.searchQuery);
                    
                    // Search in multiple endpoints
                    const [customersRes, managersRes, areasRes] = await Promise.all([
                        fetch(`/api/settings/search-customers?query=${query}`),
                        fetch(`/api/managers?date_from=${this.dateFrom}&date_to=${this.dateTo}`),
                        fetch(`/api/sales-areas?date_from=${this.dateFrom}&date_to=${this.dateTo}`)
                    ]);
                    
                    const customers = await customersRes.json();
                    const managers = await managersRes.json();
                    const areas = await areasRes.json();
                    
                    this.searchResults = [];
                    
                    // Add customers
                    if (customers.success && customers.data) {
                        customers.data.forEach(c => {
                            this.searchResults.push({
                                id: c.fID,
                                name: c.fNAME + ' (' + c.fCODE + ')',
                                type: 'Клиент',
                                url: '/customers-grid'
                            });
                        });
                    }
                    
                    // Add managers (filter by name)
                    if (managers.success && managers.data) {
                        managers.data
                            .filter(m => m.fNAME.toLowerCase().includes(this.searchQuery.toLowerCase()))
                            .forEach(m => {
                                this.searchResults.push({
                                    id: m.fID,
                                    name: m.fNAME + ' (' + m.fCODE + ')',
                                    type: 'Менеджер',
                                    url: '/managers'
                                });
                            });
                    }
                    
                    // Add areas (filter by name)
                    if (areas.success && areas.data) {
                        areas.data
                            .filter(a => a.name.toLowerCase().includes(this.searchQuery.toLowerCase()))
                            .forEach(a => {
                                this.searchResults.push({
                                    id: a.code,
                                    name: a.name + ' (' + a.code + ')',
                                    type: 'Территория',
                                    url: '/areas'
                                });
                            });
                    }
                    
                    // Limit to 10 results
                    this.searchResults = this.searchResults.slice(0, 10);
                } catch (error) {
                    console.error('Search error:', error);
                }
            },
            
            navigateToResult(result) {
                window.location.href = result.url;
            },
            
            clearSearch() {
                this.searchQuery = '';
                this.searchResults = [];
            },

            // ===== Date filters =====
            setThisMonth() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
                const lastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0);
                
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(lastDay);
                this.selectedPeriodText = 'Текущий месяц';
                this.currentDaysInMonth = today.getDate();
                this.applyFilters();
            },

            setLastMonth() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                const lastDay = new Date(today.getFullYear(), today.getMonth(), 0);
                
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(lastDay);
                this.selectedPeriodText = 'Прошлый месяц';
                this.currentDaysInMonth = lastDay.getDate();
                this.applyFilters();
            },

            setThisYear() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), 0, 1);
                const lastDay = new Date(today.getFullYear(), 11, 31);
                
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(lastDay);
                this.selectedPeriodText = 'Текущий год';
                const diffTime = Math.abs(lastDay - firstDay);
                this.currentDaysInMonth = Math.ceil(diffTime / (1000 * 60 * 60 * 24)) + 1;
                this.applyFilters();
            },

            resetDates() {
                this.setThisMonth();
            },

            formatDate(date) {
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                return `${year}-${month}-${day}`;
            },

            async loadStats() {
                try {
                    let url = '/api/dashboard/stats';
                    const params = new URLSearchParams();
                    if (this.dateFrom) params.append('date_from', this.dateFrom);
                    if (this.dateTo) params.append('date_to', this.dateTo);
                    if (this.selectedArea) params.append('sales_area', this.selectedArea);
                    
                    if (params.toString()) url += '?' + params.toString();
                    
                    const response = await fetch(url);
                    const result = await response.json();
                    
                    if (result.success) {
                        this.stats = result.data;
                        this.loading = false;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки статистики:', error);
                    this.loading = false;
                }
            },

            async loadDebts() {
                try {
                    let url = '/api/dashboard/debts';
                    
                    const response = await fetch(url);
                    const result = await response.json();
                    
                    if (result.success) {
                        this.debts = {
                            debt_from_documents: result.debt_from_documents,
                            type01: result.type01,
                            type02: result.type02,
                            final_debt: result.final_debt,
                            debt_customers_count: result.debt_customers_count,
                            top_debtors: result.top_debtors
                        };
                        this.loadingDebts = false;
                        
                        // Отрисовать графики долгов
                        this.renderDebtsChart();
                        this.renderTopDebtorsChart();
                    }
                } catch (error) {
                    console.error('Ошибка загрузки долгов:', error);
                    this.loadingDebts = false;
                }
            },

            async loadCharts() {
                try {
                    // График продаж
                    const salesResponse = await fetch('/api/dashboard/sales-chart');
                    const salesResult = await salesResponse.json();
                    
                    if (salesResult.success) {
                        this.renderSalesChart(salesResult.data);
                    }

                    // График менеджеров
                    const managersResponse = await fetch('/api/dashboard/top-managers');
                    const managersResult = await managersResponse.json();
                    
                    if (managersResult.success) {
                        this.renderManagersChart(managersResult.data);
                    }

                    // График за последние 10 лет
                    await this.load10YearsChart();
                } catch (error) {
                    console.error('Ошибка загрузки графиков:', error);
                }
            },

            async load10YearsChart() {
                try {
                    const response = await fetch(`/api/dashboard/10years-chart?date_from=${this.dateFrom}`);
                    const result = await response.json();
                    
                    if (result.success) {
                        this.render10YearsChart(result.data, result.current_month);
                    }
                } catch (error) {
                    console.error('Ошибка загрузки графика за 10 лет:', error);
                }
            },

            render10YearsChart(data, monthName) {
                const ctx = document.getElementById('tenYearsChart');
                if (!ctx) return; // Элемент не найден (виджет скрыт)
                
                if (this.tenYearsChart) {
                    this.tenYearsChart.destroy();
                }

                const labels = data.map(item => item.Year);
                const salesValues = data.map(item => item.SalesCount);
                const revenueValues = data.map(item => item.TotalSum);

                this.tenYearsChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: 'Выручка (AMD)',
                                data: revenueValues,
                                borderColor: 'rgb(13, 202, 240)',
                                backgroundColor: 'rgba(13, 202, 240, 0.1)',
                                tension: 0.4,
                                fill: true,
                                yAxisID: 'y'
                            },
                            {
                                label: 'Количество продаж',
                                data: salesValues,
                                borderColor: 'rgb(255, 193, 7)',
                                backgroundColor: 'rgba(255, 193, 7, 0.1)',
                                tension: 0.4,
                                fill: true,
                                yAxisID: 'y1'
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        interaction: {
                            mode: 'index',
                            intersect: false
                        },
                        plugins: {
                            title: {
                                display: true,
                                text: `Динамика за ${monthName} (последние 10 лет)`,
                                color: '#fff'
                            },
                            legend: {
                                display: true,
                                position: 'top',
                                labels: {
                                    color: '#fff'
                                }
                            },
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        let label = context.dataset.label || '';
                                        if (label) {
                                            label += ': ';
                                        }
                                        if (context.dataset.yAxisID === 'y') {
                                            label += formatCurrency(context.parsed.y);
                                        } else {
                                            label += formatNumber(context.parsed.y);
                                        }
                                        return label;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                ticks: { color: '#adb5bd' },
                                grid: { color: 'rgba(255, 255, 255, 0.1)' }
                            },
                            y: {
                                type: 'linear',
                                display: true,
                                position: 'left',
                                ticks: {
                                    color: '#adb5bd',
                                    callback: function(value) {
                                        return formatNumber(value);
                                    }
                                },
                                grid: { color: 'rgba(255, 255, 255, 0.1)' },
                                title: {
                                    display: true,
                                    text: 'Выручка (AMD)',
                                    color: '#adb5bd'
                                }
                            },
                            y1: {
                                type: 'linear',
                                display: true,
                                position: 'right',
                                ticks: {
                                    color: '#adb5bd',
                                    callback: function(value) {
                                        return formatNumber(value);
                                    }
                                },
                                grid: {
                                    drawOnChartArea: false
                                },
                                title: {
                                    display: true,
                                    text: 'Количество продаж',
                                    color: '#adb5bd'
                                }
                            }
                        }
                    }
                });
            },

            renderSalesChart(data) {
                const ctx = document.getElementById('salesChart');
                if (!ctx) return; // Элемент не найден (виджет скрыт)
                
                if (this.salesChart) {
                    this.salesChart.destroy();
                }

                const labels = data.map(item => item.Month);
                const values = data.map(item => item.TotalSum);

                this.salesChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Выручка (AMD)',
                            data: values,
                            borderColor: 'rgb(13, 110, 253)',
                            backgroundColor: 'rgba(13, 110, 253, 0.1)',
                            tension: 0.4,
                            fill: true
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: {
                                display: true,
                                position: 'top'
                            },
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        return 'Выручка: ' + formatCurrency(context.parsed.y);
                                    }
                                }
                            }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                ticks: {
                                    callback: function(value) {
                                        return formatNumber(value);
                                    }
                                }
                            }
                        }
                    }
                });
            },

            renderManagersChart(data) {
                const ctx = document.getElementById('managersChart');
                if (!ctx) return; // Элемент не найден (виджет скрыт)
                
                if (this.managersChart) {
                    this.managersChart.destroy();
                }

                const labels = data.map(item => item.ManagerName);
                const values = data.map(item => item.TotalSales);

                this.managersChart = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Продажи (AMD)',
                            data: values,
                            backgroundColor: [
                                'rgba(13, 110, 253, 0.8)',
                                'rgba(25, 135, 84, 0.8)',
                                'rgba(255, 193, 7, 0.8)',
                                'rgba(13, 202, 240, 0.8)',
                                'rgba(220, 53, 69, 0.8)',
                                'rgba(111, 66, 193, 0.8)',
                                'rgba(255, 87, 34, 0.8)',
                                'rgba(76, 175, 80, 0.8)',
                                'rgba(156, 39, 176, 0.8)',
                                'rgba(255, 152, 0, 0.8)'
                            ],
                            borderColor: 'rgba(255, 255, 255, 0.1)',
                            borderWidth: 1
                        }]
                    },
                    options: {
                        indexAxis: 'y',
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: {
                                display: false
                            },
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        return formatCurrency(context.parsed.x);
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                beginAtZero: true,
                                ticks: {
                                    callback: function(value) {
                                        return formatNumber(value);
                                    }
                                }
                            }
                        }
                    }
                });
            },

            renderDebtsChart() {
                const ctx = document.getElementById('debtsChart');
                if (!ctx) return; // Элемент не найден (виджет скрыт)
                
                if (this.debtsChart) {
                    this.debtsChart.destroy();
                }

                const debtFromDocs = Math.abs(this.debts.debt_from_documents);
                const type01Abs = Math.abs(this.debts.type01);
                const type02Abs = Math.abs(this.debts.type02);
                const finalDebt = Math.abs(this.debts.final_debt);

                this.debtsChart = new Chart(ctx, {
                    type: 'doughnut',
                    data: {
                        labels: ['Долг из документов', 'Type 01', 'Type 02', 'Конечный долг'],
                        datasets: [{
                            data: [debtFromDocs, type01Abs, type02Abs, finalDebt],
                            backgroundColor: [
                                'rgba(13, 110, 253, 0.8)',
                                'rgba(13, 202, 240, 0.8)',
                                'rgba(111, 66, 193, 0.8)',
                                'rgba(255, 193, 7, 0.8)'
                            ],
                            borderColor: [
                                'rgb(13, 110, 253)',
                                'rgb(13, 202, 240)',
                                'rgb(111, 66, 193)',
                                'rgb(255, 193, 7)'
                            ],
                            borderWidth: 2
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: {
                                position: 'bottom'
                            },
                            tooltip: {
                                callbacks: {
                                    label: (context) => {
                                        return context.label + ': ' + this.formatCurrency(context.parsed);
                                    }
                                }
                            }
                        }
                    }
                });
            },

            renderTopDebtorsChart() {
                const ctx = document.getElementById('topDebtorsChart');
                if (!ctx) return; // Элемент не найден (виджет скрыт)
                
                if (this.topDebtorsChart) {
                    this.topDebtorsChart.destroy();
                }

                if (!this.debts.top_debtors || this.debts.top_debtors.length === 0) {
                    return;
                }

                const labels = this.debts.top_debtors.map(d => d.customer_name.substring(0, 20));
                const data = this.debts.top_debtors.map(d => d.debt_amount);

                this.topDebtorsChart = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Долг',
                            data: data,
                            backgroundColor: 'rgba(220, 53, 69, 0.8)',
                            borderColor: 'rgb(220, 53, 69)',
                            borderWidth: 1
                        }]
                    },
                    options: {
                        indexAxis: 'y',
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: {
                                display: false
                            },
                            tooltip: {
                                callbacks: {
                                    label: (context) => {
                                        return this.formatCurrency(context.parsed.x);
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                beginAtZero: true,
                                ticks: {
                                    callback: (value) => {
                                        return this.formatNumber(value);
                                    }
                                }
                            }
                        }
                    }
                });
            },

            formatNumber(num) {
                return new Intl.NumberFormat('ru-RU').format(num);
            },

            formatCurrency(num) {
                return new Intl.NumberFormat('ru-RU', {
                    style: 'currency',
                    currency: 'AMD',
                    minimumFractionDigits: 0
                }).format(num);
            },

            formatPercent(num) {
                return (num >= 0 ? '+' : '') + num.toFixed(1) + '%';
            }
        }
    }

    // Инициализация drag & drop для карточек
    document.addEventListener('DOMContentLoaded', () => {
        const statsContainer = document.getElementById('stats-container');
        if (statsContainer) {
            new Sortable(statsContainer, {
                animation: 150,
                ghostClass: 'sortable-ghost',
                chosenClass: 'sortable-chosen',
                dragClass: 'sortable-drag',
                onEnd: function() {
                    // Сохранить порядок в localStorage
                    const order = Array.from(statsContainer.children).map((card, index) => index);
                    localStorage.setItem('stats-order', JSON.stringify(order));
                }
            });
        }
    });

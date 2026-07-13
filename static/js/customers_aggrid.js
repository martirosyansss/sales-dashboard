/* customers_aggrid — page script (extracted from inline <script>). */
function customersData() {
    return {
        customers: [],
        filteredCustomers: [],
        salesAreas: [],
        selectedArea: '',
        viewMode: localStorage.getItem('viewMode') || 'cards', // 'cards' or 'table' - сохраняем выбор
        dateFrom: '',
        dateTo: '',
        dateFromDisplay: '',
        dateToDisplay: '',
        dateFromError: '',
        dateToError: '',
        includeZeroSales: false,
        showOnlyProblematic: false,
        showCriteriaModal: false,
        criteria: JSON.parse(localStorage.getItem('criteriaSettings') || JSON.stringify({
            useDebt: true,
            minDebt: 100000,
            usePaymentDays: true,
            maxDaysSincePayment: 60,
            useDebtPercent: true,
            maxDebtPercent: 50,
            useSaleDays: true,
            maxDaysSinceSale: 90
        })),
        divisions: [],
        selectedDivisions: JSON.parse(localStorage.getItem('selectedDivisions') || '[]'),
        divisionSearch: '',
        customerGroups: [],
        selectedGroups: JSON.parse(localStorage.getItem('selectedGroups') || '[]'),
        groupSearch: '',
        showFiltersPanel: false,
        loading: false,
        summary: {
            count: 0,
            total_sales: 0,
            total_payments: 0,
            total_debt: 0,
            total_initial_debt: 0,
            period: { from: '', to: '' }
        },
        gridApi: null,
        gridInitialized: false,
        activityChart: null, // Для хранения экземпляра графика
        detailPanel: {
            open: false,
            customer: null,
            purchases: [],
            payments: [],
            chartPurchases: [],
            chartPayments: [],
            loadingPurchases: false,
            loadingPayments: false,
            dateFrom: '',
            dateTo: ''
        },
        
        init() {
            this.setCurrentMonth();
            // Explicitly clear any validation errors on init
            this.dateFromError = '';
            this.dateToError = '';
            this.loadSalesAreas().then(() => {
                // После загрузки Sales Areas, если есть selectedArea, загружаем данные
                if (this.selectedArea && this.salesAreas.length > 0) {
                    // Загружаем данные (грид инициализируется только при переключении на него)
                    this.loadCustomers().then(() => {
                        // После загрузки данных, если viewMode = 'table', инициализируем грид
                        if (this.viewMode === 'table') {
                            this.$nextTick(() => {
                                this.initAGGrid();
                            });
                        }
                    });
                }
            });
            this.loadDivisions();
            this.loadCustomerGroups();
        },
        
        initAGGrid() {
            if (this.gridInitialized) {
                // Если грид уже инициализирован, просто обновляем данные
                if (this.gridApi) {
                    this.gridApi.setGridOption('rowData', this.customers);
                }
                return;
            }
            
            const gridDiv = document.querySelector('#customersGrid');
            if (!gridDiv) {
                console.error('Grid container not found');
                return;
            }
            
            const gridOptions = {
                columnDefs: [
                    {
                        headerName: '',
                        field: 'IsProblematic',
                        width: 50,
                        cellRenderer: params => params.value ? '<i class="fas fa-exclamation-triangle text-danger"></i>' : '',
                        filter: 'agSetColumnFilter',
                        filterParams: {
                            values: [true, false],
                            valueFormatter: params => params.value ? 'Проблемный' : 'OK'
                        }
                    },
                    {
                        headerName: 'Код',
                        field: 'CustomerCode',
                        width: 120,
                        filter: 'agTextColumnFilter',
                        cellRenderer: params => {
                            const isProblematic = params.data.IsProblematic;
                            const badgeClass = isProblematic ? 'bg-danger' : 'bg-secondary';
                            return `<span class="badge ${badgeClass}">${params.value}</span>`;
                        }
                    },
                    {
                        headerName: 'Название',
                        field: 'CustomerName',
                        width: 250,
                        filter: 'agTextColumnFilter',
                        cellRenderer: params => `<strong>${params.value}</strong>`
                    },
                    {
                        headerName: 'Группа',
                        field: 'GroupCode',
                        width: 120,
                        filter: 'agSetColumnFilter'
                    },
                    {
                        headerName: 'Менеджер',
                        field: 'ManagerName',
                        width: 180,
                        filter: 'agSetColumnFilter',
                        cellRenderer: params => {
                            if (params.value) {
                                return `<span class="badge bg-primary">${params.data.ManagerCode}</span> ${params.value}`;
                            }
                            return '';
                        }
                    },
                    {
                        headerName: 'Продаж',
                        field: 'SalesCount',
                        width: 100,
                        type: 'numericColumn',
                        filter: 'agNumberColumnFilter',
                        valueFormatter: params => params.value ? params.value.toLocaleString('ru-RU') : '0'
                    },
                    {
                        headerName: 'Сумма',
                        field: 'TotalSales',
                        width: 150,
                        type: 'numericColumn',
                        filter: 'agNumberColumnFilter',
                        cellClass: 'text-primary fw-bold',
                        valueFormatter: params => params.value ? params.value.toLocaleString('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 0}) + ' AMD' : '0 AMD'
                    },
                    {
                        headerName: 'Платежи',
                        field: 'TotalPayments',
                        width: 150,
                        type: 'numericColumn',
                        filter: 'agNumberColumnFilter',
                        cellClass: 'text-success',
                        valueFormatter: params => params.value ? params.value.toLocaleString('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 0}) + ' AMD' : '0 AMD'
                    },
                    {
                        headerName: 'Нач. долг',
                        field: 'InitialDebt',
                        width: 150,
                        type: 'numericColumn',
                        filter: 'agNumberColumnFilter',
                        cellClass: params => params.value > 0 ? 'text-warning' : 'text-success',
                        valueFormatter: params => params.value ? params.value.toLocaleString('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 0}) + ' AMD' : '0 AMD'
                    },
                    {
                        headerName: 'Долг',
                        field: 'Debt',
                        width: 150,
                        type: 'numericColumn',
                        filter: 'agNumberColumnFilter',
                        cellClass: params => params.value > 0 ? 'text-danger fw-bold' : 'text-success',
                        valueFormatter: params => params.value ? params.value.toLocaleString('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 0}) + ' AMD' : '0 AMD'
                    },
                    {
                        headerName: '% долга',
                        field: 'DebtPercent',
                        width: 120,
                        type: 'numericColumn',
                        filter: 'agNumberColumnFilter',
                        cellRenderer: params => {
                            const value = params.value || 0;
                            let badgeClass = 'bg-secondary';
                            if (value > 50) badgeClass = 'bg-danger';
                            else if (value > 25) badgeClass = 'bg-warning';
                            return `<span class="badge ${badgeClass}">${value.toFixed(1)}%</span>`;
                        }
                    },
                    {
                        headerName: 'Посл. платеж',
                        field: 'LastPaymentDate',
                        width: 130,
                        filter: 'agDateColumnFilter',
                        cellRenderer: params => {
                            if (!params.value) return '<small class="text-muted">—</small>';
                            return `<span style="color: #adb5bd;">${params.value}</span>`;
                        }
                    },
                    {
                        headerName: 'Дней (платеж)',
                        field: 'DaysSinceLastPayment',
                        width: 130,
                        type: 'numericColumn',
                        filter: 'agNumberColumnFilter',
                        cellRenderer: params => {
                            if (!params.value) return '<small class="text-muted">—</small>';
                            let badgeClass = 'text-muted';
                            if (params.value > 60) badgeClass = 'badge bg-danger';
                            else if (params.value > 30) badgeClass = 'badge bg-warning';
                            return `<span class="${badgeClass}">${params.value}</span>`;
                        }
                    },
                    {
                        headerName: 'Посл. покупка',
                        field: 'LastSaleDate',
                        width: 130,
                        filter: 'agDateColumnFilter',
                        cellRenderer: params => {
                            if (!params.value) return '<small class="text-muted">—</small>';
                            return `<span style="color: #adb5bd;">${params.value}</span>`;
                        }
                    },
                    {
                        headerName: 'Дней (покупка)',
                        field: 'DaysSinceLastSale',
                        width: 130,
                        type: 'numericColumn',
                        filter: 'agNumberColumnFilter',
                        cellRenderer: params => {
                            if (!params.value) return '<small class="text-muted">—</small>';
                            let badgeClass = 'text-muted';
                            if (params.value > 90) badgeClass = 'badge bg-danger';
                            else if (params.value > 60) badgeClass = 'badge bg-warning';
                            return `<span class="${badgeClass}">${params.value}</span>`;
                        }
                    }
                ],
                defaultColDef: {
                    sortable: true,
                    resizable: true,
                    flex: 0
                },
                rowData: [],
                pagination: false, // Отключаем пагинацию, показываем все
                domLayout: 'autoHeight', // Автоматическая высота
                animateRows: true,
                onRowClicked: (event) => {
                    if (event.data) {
                        console.log('Row clicked:', event.data);
                        // Используем Alpine store или прямой вызов через window
                        const alpineComponent = Alpine.$data(document.querySelector('[x-data]'));
                        if (alpineComponent && alpineComponent.openCustomerPurchases) {
                            alpineComponent.openCustomerPurchases(event.data);
                        }
                    }
                },
                localeText: {
                    page: 'Страница',
                    more: 'Ещё',
                    to: 'до',
                    of: 'из',
                    next: 'Следующая',
                    last: 'Последняя',
                    first: 'Первая',
                    previous: 'Предыдущая',
                    loadingOoo: 'Загрузка...',
                    noRowsToShow: 'Нет данных для отображения',
                    filterOoo: 'Фильтр...',
                    searchOoo: 'Поиск...',
                    equals: 'Равно',
                    notEqual: 'Не равно',
                    lessThan: 'Меньше',
                    greaterThan: 'Больше',
                    contains: 'Содержит',
                    notContains: 'Не содержит',
                    startsWith: 'Начинается с',
                    endsWith: 'Заканчивается на',
                    columns: 'Колонки',
                    filters: 'Фильтры'
                }
            };
            
            this.gridApi = agGrid.createGrid(gridDiv, gridOptions);
            this.gridInitialized = true; // Отмечаем что грид инициализирован
            
            // Устанавливаем данные, если они уже загружены
            if (this.customers.length > 0) {
                this.gridApi.setGridOption('rowData', this.customers);
            }
        },
        
        setCurrentMonth() {
            const today = new Date();
            const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
            const lastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0);
            this.dateFrom = firstDay.toISOString().split('T')[0];
            this.dateTo = lastDay.toISOString().split('T')[0];
            this.dateFromDisplay = this.formatDateForDisplay(this.dateFrom);
            this.dateToDisplay = this.formatDateForDisplay(this.dateTo);
            this.dateFromError = '';
            this.dateToError = '';
        },
        
        setDateRange(range) {
            const today = new Date();
            let fromDate, toDate;
            
            switch(range) {
                case 'today':
                    fromDate = today;
                    toDate = today;
                    break;
                    
                case 'currentMonth':
                    fromDate = new Date(today.getFullYear(), today.getMonth(), 1);
                    toDate = new Date(today.getFullYear(), today.getMonth() + 1, 0);
                    break;
                    
                case 'lastMonth':
                    fromDate = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                    toDate = new Date(today.getFullYear(), today.getMonth(), 0);
                    break;
                    
                case 'quarter':
                    // Последние 3 месяца
                    fromDate = new Date(today.getFullYear(), today.getMonth() - 2, 1);
                    toDate = today;
                    break;
                    
                case 'year':
                    // Последний год от сегодня
                    fromDate = new Date(today.getFullYear() - 1, today.getMonth(), today.getDate());
                    toDate = today;
                    break;
                    
                case 'lastYear':
                    // Прошлый календарный год
                    fromDate = new Date(today.getFullYear() - 1, 0, 1);
                    toDate = new Date(today.getFullYear() - 1, 11, 31);
                    break;
                    
                default:
                    return;
            }
            
            this.dateFrom = fromDate.toISOString().split('T')[0];
            this.dateTo = toDate.toISOString().split('T')[0];
            this.dateFromDisplay = this.formatDateForDisplay(this.dateFrom);
            this.dateToDisplay = this.formatDateForDisplay(this.dateTo);
            this.dateFromError = '';
            this.dateToError = '';
            
            // Автоматически загружаем данные
            this.loadCustomers();
        },
        
        async loadSalesAreas() {
            try {
                const response = await fetch('/api/settings/sales-areas/list');
                const result = await response.json();
                if (result.success) {
                    // Remove duplicates by code
                    const uniqueAreas = [];
                    const seenCodes = new Set();
                    result.data.forEach(area => {
                        const code = area.Code || area.code || area.SalesAreaCode;
                        const name = area.Name || area.name || area.SalesAreaName;
                        if (code && !seenCodes.has(code)) {
                            seenCodes.add(code);
                            uniqueAreas.push({
                                code: String(code),
                                name: name
                            });
                        }
                    });
                    this.salesAreas = uniqueAreas;
                    
                    // Устанавливаем первую Sales Area по умолчанию или '101' если есть
                    if (this.salesAreas.length > 0) {
                        const area101 = this.salesAreas.find(a => a.code === '101');
                        this.selectedArea = area101 ? area101.code : this.salesAreas[0].code;
                    }
                }
            } catch (error) {
                console.error('Ошибка загрузки Sales Areas:', error);
            }
        },
        
        async loadDivisions() {
            try {
                const response = await fetch('/api/settings/product-groups');
                const result = await response.json();
                if (result.success) {
                    this.divisions = result.data || [];
                }
            } catch (error) {
                console.error('Ошибка загрузки дивизионов:', error);
            }
        },
        
        async loadCustomerGroups() {
            try {
                const response = await fetch('/api/settings/groups');
                const result = await response.json();
                if (result.success) {
                    this.customerGroups = result.data || [];
                }
            } catch (error) {
                console.error('Ошибка загрузки групп клиентов:', error);
            }
        },
        
        filteredDivisions() {
            if (!this.divisionSearch.trim()) return this.divisions;
            const search = this.divisionSearch.toLowerCase();
            return this.divisions.filter(d => 
                d.fGROUP.toLowerCase().includes(search) || 
                (d.name && d.name.toLowerCase().includes(search))
            );
        },
        
        filteredGroups() {
            if (!this.groupSearch.trim()) return this.customerGroups;
            const search = this.groupSearch.toLowerCase();
            return this.customerGroups.filter(g => 
                g.code.toLowerCase().includes(search) || 
                g.name.toLowerCase().includes(search)
            );
        },
        
        selectAllDivisions() {
            if (this.selectedDivisions.length === this.divisions.length) {
                this.selectedDivisions = [];
            } else {
                this.selectedDivisions = this.divisions.map(d => d.fGROUP);
            }
            this.saveDivisionsToStorage();
            this.loadCustomers();
        },
        
        selectAllGroups() {
            if (this.selectedGroups.length === this.customerGroups.length) {
                this.selectedGroups = [];
            } else {
                this.selectedGroups = this.customerGroups.map(g => g.code);
            }
            this.saveGroupsToStorage();
            this.loadCustomers();
        },
        
        saveDivisionsToStorage() {
            localStorage.setItem('selectedDivisions', JSON.stringify(this.selectedDivisions));
        },
        
        saveGroupsToStorage() {
            localStorage.setItem('selectedGroups', JSON.stringify(this.selectedGroups));
        },
        
        onAreaChange() {
            this.loadCustomers();
        },
        
        async loadCustomers() {
            if (!this.selectedArea) return;
            
            this.loading = true;
            try {
                const includeZero = this.includeZeroSales ? '&include_zero_sales=1' : '';
                let url = `/api/customers?sales_area=${this.selectedArea}&date_from=${this.dateFrom}&date_to=${this.dateTo}${includeZero}`;
                
                // Добавляем фильтры дивизионов
                if (this.selectedDivisions.length > 0) {
                    url += `&divisions=${this.selectedDivisions.join(',')}`;
                }
                
                // Добавляем фильтры групп
                if (this.selectedGroups.length > 0) {
                    url += `&groups=${this.selectedGroups.join(',')}`;
                }
                
                const response = await fetch(url);
                const result = await response.json();
                
                if (result.success) {
                    // Добавляем IsProblematic к каждому клиенту
                    this.customers = result.data.map(customer => ({
                        ...customer,
                        IsProblematic: this.checkIsProblematic(customer)
                    }));
                    this.summary = result.summary;
                    
                    // Update AG Grid only if in table mode and grid is initialized
                    if (this.viewMode === 'table' && this.gridApi) {
                        this.gridApi.setGridOption('rowData', this.customers);
                    }
                }
            } catch (error) {
                console.error('Ошибка загрузки клиентов:', error);
            } finally {
                this.loading = false;
            }
        },
        
        checkIsProblematic(customer) {
            // Проверяем по настраиваемым критериям (хотя бы один должен совпадать)
            let isProblematic = false;
            
            if (this.criteria.useDebt) {
                isProblematic = isProblematic || (customer.Debt > this.criteria.minDebt);
            }
            
            if (this.criteria.usePaymentDays) {
                isProblematic = isProblematic || (customer.DaysSinceLastPayment && customer.DaysSinceLastPayment > this.criteria.maxDaysSincePayment);
            }
            
            if (this.criteria.useDebtPercent) {
                isProblematic = isProblematic || (customer.DebtPercent > this.criteria.maxDebtPercent);
            }
            
            if (this.criteria.useSaleDays) {
                isProblematic = isProblematic || (customer.DaysSinceLastSale && customer.DaysSinceLastSale > this.criteria.maxDaysSinceSale);
            }
            
            return isProblematic;
        },
        
        applyProblematicFilter() {
            if (!this.gridApi) return;
            
            if (this.showOnlyProblematic) {
                // Фильтруем только проблемных клиентов
                this.gridApi.setGridOption('quickFilterText', '');
                const problematicCustomers = this.customers.filter(c => c.IsProblematic);
                this.gridApi.setGridOption('rowData', problematicCustomers);
            } else {
                // Показываем всех клиентов
                this.gridApi.setGridOption('rowData', this.customers);
            }
        },
        
        applyCriteria() {
            // Сохраняем критерии в localStorage
            localStorage.setItem('criteriaSettings', JSON.stringify(this.criteria));
            
            // Пересчитываем IsProblematic для всех клиентов
            this.customers = this.customers.map(customer => ({
                ...customer,
                IsProblematic: this.checkIsProblematic(customer)
            }));
            
            // Обновляем грид
            if (this.gridApi) {
                if (this.showOnlyProblematic) {
                    const problematicCustomers = this.customers.filter(c => c.IsProblematic);
                    this.gridApi.setGridOption('rowData', problematicCustomers);
                } else {
                    this.gridApi.setGridOption('rowData', this.customers);
                }
            }
            
            this.showCriteriaModal = false;
        },
        
        exportToExcel() {
            if (this.gridApi) {
                const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
                const areaName = this.salesAreas.find(a => a.code === this.selectedArea)?.name || this.selectedArea;
                
                this.gridApi.exportDataAsExcel({
                    fileName: `customers_${areaName}_${timestamp}.xlsx`,
                    sheetName: 'Клиенты',
                    // Export only filtered data (default behavior)
                    onlySelected: false,
                    onlySelectedAllPages: false,
                    // Column customization for Excel
                    columnKeys: ['CustomerCode', 'CustomerName', 'GroupCode', 'ManagerCode', 'ManagerName', 
                                 'SalesCount', 'TotalSales', 'TotalPayments', 'InitialDebt', 'Debt', 'DebtPercent',
                                 'LastPaymentDate', 'DaysSinceLastPayment', 'LastSaleDate', 'DaysSinceLastSale'],
                    processCellCallback: (params) => {
                        // Format numeric values for Excel
                        if (['TotalSales', 'TotalPayments', 'InitialDebt', 'Debt'].includes(params.column.getColId())) {
                            return params.value || 0;
                        }
                        if (params.column.getColId() === 'DebtPercent') {
                            return params.value ? params.value / 100 : 0; // Export as decimal for Excel percentage format
                        }
                        return params.value;
                    }
                });
                
                // Show notification
                this.$dispatch('show-toast', {
                    message: 'Экспорт в Excel завершен (только отфильтрованные данные)',
                    type: 'success'
                });
            }
        },
        
        exportToCsv() {
            if (this.gridApi) {
                const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
                const areaName = this.salesAreas.find(a => a.code === this.selectedArea)?.name || this.selectedArea;
                
                this.gridApi.exportDataAsCsv({
                    fileName: `customers_${areaName}_${timestamp}.csv`,
                    // Export only filtered data (default behavior)
                    onlySelected: false,
                    onlySelectedAllPages: false,
                    columnKeys: ['CustomerCode', 'CustomerName', 'GroupCode', 'ManagerCode', 'ManagerName', 
                                 'SalesCount', 'TotalSales', 'TotalPayments', 'InitialDebt', 'Debt', 'DebtPercent',
                                 'LastPaymentDate', 'DaysSinceLastPayment', 'LastSaleDate', 'DaysSinceLastSale']
                });
                
                // Show notification
                this.$dispatch('show-toast', {
                    message: 'Экспорт в CSV завершен (только отфильтрованные данные)',
                    type: 'success'
                });
            }
        },
        
        async openCustomerPurchases(customer) {
            console.log('Opening customer details:', customer);
            this.detailPanel.customer = customer;
            this.detailPanel.open = true;
            this.detailPanel.purchases = [];
            this.detailPanel.payments = [];
            
            // Устанавливаем даты из основных фильтров, если еще не установлены
            if (!this.detailPanel.dateFrom) {
                this.detailPanel.dateFrom = this.dateFrom;
            }
            if (!this.detailPanel.dateTo) {
                this.detailPanel.dateTo = this.dateTo;
            }
            
            // Загружаем продажи (используем CustomerId если есть, иначе CustomerCode)
            const customerId = customer.CustomerId || customer.CustomerCode;
            this.loadCustomerPurchases(customerId);
        },
        
        async loadCustomerPurchases(customerId) {
            this.detailPanel.loadingPurchases = true;
            this.detailPanel.loadingPayments = true;
            try {
                // Используем даты из detailPanel для таблиц
                const params = new URLSearchParams({
                    date_from: this.detailPanel.dateFrom || this.dateFrom,
                    date_to: this.detailPanel.dateTo || this.dateTo
                });
                const url = `/api/customers/${customerId}/purchases?${params.toString()}`;
                console.log('Loading purchases from:', url);
                const response = await fetch(url);
                const result = await response.json();
                
                if (result.success) {
                    this.detailPanel.purchases = result.data || [];
                    this.detailPanel.payments = result.payments || [];
                    console.log('Loaded purchases:', this.detailPanel.purchases.length);
                    console.log('Loaded payments:', this.detailPanel.payments.length);
                    
                    // Загружаем данные для графика за последний год
                    this.loadChartData(customerId);
                } else {
                    console.error('API returned error:', result);
                }
            } catch (error) {
                console.error('Ошибка загрузки продаж:', error);
            } finally {
                this.detailPanel.loadingPurchases = false;
                this.detailPanel.loadingPayments = false;
            }
        },
        
        async loadChartData(customerId) {
            try {
                // Вычисляем даты для последнего года
                const today = new Date();
                const oneYearAgo = new Date(today);
                oneYearAgo.setFullYear(today.getFullYear() - 1);
                
                const dateFrom = oneYearAgo.toISOString().split('T')[0];
                const dateTo = today.toISOString().split('T')[0];
                
                const params = new URLSearchParams({
                    date_from: dateFrom,
                    date_to: dateTo
                });
                const url = `/api/customers/${customerId}/purchases?${params.toString()}`;
                console.log('Loading chart data from:', url);
                const response = await fetch(url);
                const result = await response.json();
                
                if (result.success) {
                    this.detailPanel.chartPurchases = result.data || [];
                    this.detailPanel.chartPayments = result.payments || [];
                    
                    // Обновляем график после загрузки данных
                    this.$nextTick(() => {
                        this.updateActivityChart();
                    });
                }
            } catch (error) {
                console.error('Ошибка загрузки данных для графика:', error);
            }
        },
        
        reloadCustomerData() {
            // Перезагружаем данные с новыми датами
            if (this.detailPanel.customer) {
                const customerId = this.detailPanel.customer.CustomerId || this.detailPanel.customer.CustomerCode;
                this.loadCustomerPurchases(customerId);
            }
        },
        
        resetDetailDates() {
            // Сбрасываем даты на основные фильтры
            this.detailPanel.dateFrom = this.dateFrom;
            this.detailPanel.dateTo = this.dateTo;
            this.reloadCustomerData();
        },
        
        closeCustomerPurchases() {
            this.detailPanel.open = false;
            this.detailPanel.customer = null;
            this.detailPanel.purchases = [];
            this.detailPanel.payments = [];
            
            // Уничтожаем график при закрытии
            if (this.activityChart) {
                this.activityChart.destroy();
                this.activityChart = null;
            }
        },
        
        updateActivityChart() {
            // Уничтожаем предыдущий график если есть
            if (this.activityChart) {
                this.activityChart.destroy();
                this.activityChart = null;
            }
            
            // Проверяем наличие данных для графика (из chartPurchases/chartPayments)
            if (!this.detailPanel.chartPurchases || !this.detailPanel.chartPayments) {
                console.log('Chart data not loaded yet');
                return;
            }
            
            if (this.detailPanel.chartPurchases.length === 0 && this.detailPanel.chartPayments.length === 0) {
                console.log('No data for chart');
                return;
            }
            
            // Даем Alpine время отрендерить canvas
            setTimeout(() => {
                const canvas = this.$refs.activityChart;
                if (!canvas) {
                    console.log('Canvas not found after timeout');
                    return;
                }
                
                console.log('Purchases data:', this.detailPanel.chartPurchases.length);
                console.log('Payments data:', this.detailPanel.chartPayments.length);
                
                // Группируем данные по дням
                const dailyData = {};
                
            // Добавляем покупки
            this.detailPanel.chartPurchases.forEach(purchase => {
                console.log('Purchase object keys:', Object.keys(purchase));
                console.log('Purchase dates - DocDate:', purchase.DocDate, 'SaleDate:', purchase.SaleDate, 'Date:', purchase.Date);
                
                const date = purchase.DocDate || purchase.SaleDate || purchase.Date;
                const dateStr = date ? (date.split ? date.split('T')[0] : date) : null;
                
                if (dateStr && !dailyData[dateStr]) {
                    dailyData[dateStr] = { purchases: 0, payments: 0 };
                }
                if (dateStr) {
                    // Пробуем разные поля или считаем из Products
                    let sum = purchase.TotalSum || purchase.TotalWithDiscount || purchase.Total || 0;
                    
                    // Если нет прямого поля суммы, считаем из продуктов
                    if (!sum && purchase.Products && Array.isArray(purchase.Products)) {
                        sum = purchase.Products.reduce((total, product) => {
                            return total + (product.TotalWithDiscount || product.Total || 0);
                        }, 0);
                    }
                    
                    dailyData[dateStr].purchases += sum;
                    console.log(`Added purchase for ${dateStr}: ${sum}, total now: ${dailyData[dateStr].purchases}`);
                }
            });
            
            // Добавляем платежи
            this.detailPanel.chartPayments.forEach(payment => {
                console.log('Payment:', payment);
                const date = payment.PaymentDate ? payment.PaymentDate.split('T')[0] : payment.PaymentDate;
                if (date && !dailyData[date]) {
                    dailyData[date] = { purchases: 0, payments: 0 };
                }
                if (date) {
                    const sum = payment.Amount || payment.PaymentSum || payment.Sum || 0;
                    dailyData[date].payments += sum;
                    console.log(`Added payment for ${date}: ${sum}, total now: ${dailyData[date].payments}`);
                }
            });                console.log('Daily data:', dailyData);
                
                // Сортируем даты
                const sortedDates = Object.keys(dailyData).sort();
                
                if (sortedDates.length === 0) {
                    console.log('No dates to display');
                    return;
                }
                
                // Форматируем даты для отображения
                const labels = sortedDates.map(date => {
                    const [y, m, d] = date.split('-');
                    return `${d}.${m}`;
                });
                
                const purchasesData = sortedDates.map(date => dailyData[date].purchases);
                const paymentsData = sortedDates.map(date => dailyData[date].payments);
                
                console.log('Chart labels:', labels);
                console.log('Chart data - Purchases:', purchasesData);
                console.log('Chart data - Payments:', paymentsData);
                
                // Создаем график
                const ctx = canvas.getContext('2d');
                this.activityChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: 'Покупки',
                                data: purchasesData,
                                backgroundColor: 'rgba(13, 110, 253, 0.1)',
                                borderColor: 'rgba(13, 110, 253, 1)',
                                borderWidth: 2,
                                fill: true,
                                tension: 0.4,
                                pointBackgroundColor: 'rgba(13, 110, 253, 1)',
                                pointBorderColor: '#fff',
                                pointBorderWidth: 2,
                                pointRadius: 4,
                                pointHoverRadius: 6
                            },
                            {
                                label: 'Платежи',
                                data: paymentsData,
                                backgroundColor: 'rgba(46, 204, 113, 0.1)',
                                borderColor: 'rgba(46, 204, 113, 1)',
                                borderWidth: 2,
                                fill: true,
                                tension: 0.4,
                                pointBackgroundColor: 'rgba(46, 204, 113, 1)',
                                pointBorderColor: '#fff',
                                pointBorderWidth: 2,
                                pointRadius: 4,
                                pointHoverRadius: 6
                            }
                        ]
                    },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: true,
                            position: 'top',
                            labels: {
                                color: '#adb5bd',
                                font: {
                                    size: 11
                                },
                                padding: 8,
                                usePointStyle: true
                            }
                        },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    let label = context.dataset.label || '';
                                    if (label) {
                                        label += ': ';
                                    }
                                    label += context.parsed.y.toLocaleString('ru-RU') + ' ֏';
                                    return label;
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                color: '#adb5bd',
                                callback: function(value) {
                                    return value.toLocaleString('ru-RU') + ' ֏';
                                }
                            },
                            grid: {
                                color: 'rgba(255, 255, 255, 0.1)'
                            }
                        },
                        x: {
                            ticks: {
                                color: '#adb5bd',
                                maxRotation: 45,
                                minRotation: 45
                            },
                            grid: {
                                color: 'rgba(255, 255, 255, 0.05)'
                            }
                        }
                    }
                }
            });
            }, 100); // Закрытие setTimeout
        },
        
        formatCurrency(value) {
            if (!value) return '0';
            return value.toLocaleString('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
        },
        
        formatDateForDisplay(isoDate) {
            if (!isoDate) return '';
            const [year, month, day] = isoDate.split('-');
            return `${day}.${month}.${year}`;
        },
        
        parseDateFromDisplay(displayDate) {
            if (!displayDate) return '';
            
            // Удаляем лишние символы и пробелы
            let cleaned = displayDate.trim().replace(/[^\d.\/\-]/g, '');
            
            // Поддержка разных разделителей: . / -
            let parts = cleaned.split(/[.\/\-]/);
            if (parts.length !== 3) return '';
            
            let [day, month, year] = parts;
            
            // Валидация
            if (!day || !month || !year) return '';
            
            // Дополняем нулями если нужно
            day = day.padStart(2, '0');
            month = month.padStart(2, '0');
            
            // Проверяем год (если 2 цифры, добавляем 20)
            if (year.length === 2) {
                year = '20' + year;
            }
            
            // Базовая валидация диапазонов
            const dayNum = parseInt(day);
            const monthNum = parseInt(month);
            const yearNum = parseInt(year);
            
            if (dayNum < 1 || dayNum > 31) return '';
            if (monthNum < 1 || monthNum > 12) return '';
            if (yearNum < 2000 || yearNum > 2100) return '';
            
            // Проверяем валидность даты через JavaScript Date
            const testDate = new Date(yearNum, monthNum - 1, dayNum);
            if (testDate.getFullYear() !== yearNum || 
                testDate.getMonth() !== monthNum - 1 || 
                testDate.getDate() !== dayNum) {
                return ''; // Невалидная дата (например, 31 февраля)
            }
            
            return `${year}-${month}-${day}`;
        },
        
        updateDateFrom() {
            const parsed = this.parseDateFromDisplay(this.dateFromDisplay);
            if (parsed) {
                this.dateFrom = parsed;
                this.dateFromDisplay = this.formatDateForDisplay(parsed);
                this.dateFromError = '';
                this.loadCustomers();
            } else if (this.dateFromDisplay && this.dateFromDisplay.trim()) {
                // Показываем ошибку только если поле не пустое
                this.dateFromError = 'Такой даты не существует в календаре';
                // Восстанавливаем предыдущее значение через 3 секунды
                setTimeout(() => {
                    this.dateFromDisplay = this.formatDateForDisplay(this.dateFrom);
                    this.dateFromError = '';
                }, 3000);
            } else {
                // Если поле пустое, просто очищаем ошибку
                this.dateFromError = '';
            }
        },
        
        updateDateTo() {
            const parsed = this.parseDateFromDisplay(this.dateToDisplay);
            if (parsed) {
                this.dateTo = parsed;
                this.dateToDisplay = this.formatDateForDisplay(parsed);
                this.dateToError = '';
                this.loadCustomers();
            } else if (this.dateToDisplay && this.dateToDisplay.trim()) {
                // Показываем ошибку только если поле не пустое
                this.dateToError = 'Такой даты не существует в календаре';
                // Восстанавливаем предыдущее значение через 3 секунды
                setTimeout(() => {
                    this.dateToDisplay = this.formatDateForDisplay(this.dateTo);
                    this.dateToError = '';
                }, 3000);
            } else {
                // Если поле пустое, просто очищаем ошибку
                this.dateToError = '';
            }
        }
    }
}

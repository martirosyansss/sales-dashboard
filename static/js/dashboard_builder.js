/* Dashboard Builder — Alpine-компонент dashboardBuilder() (вынесен из inline <script>). */
function dashboardBuilder() {
    return {
        // Тема (true = темная, false = светлая)
        /* Bento-редизайн: страница конструктора — светлая, тема фиксирована */
        darkTheme: false,

        /* Bento size-picker: пресеты размеров карточки */
        sizePickerId: null,
        sizePresets: [
            { w: 1, h: 1 },
            { w: 2, h: 1 },
            { w: 1, h: 2 },
            { w: 2, h: 2 },
            { w: 4, h: 1 },
            { w: 4, h: 2 }
        ],
        
        // iOS Edit Mode - режим редактирования
        editMode: false,
        
        // Система страниц
        pages: [{ id: 1, name: 'Главная', cards: [] }],
        currentPageId: 1,
        nextPageId: 2,
        editingPageId: null,
        editingPageName: '',
        draggedPageIndex: null,
        
        // Карточки текущей страницы
        get cards() {
            const page = this.pages.find(p => p.id === this.currentPageId);
            return page ? page.cards : [];
        },
        set cards(value) {
            const page = this.pages.find(p => p.id === this.currentPageId);
            if (page) {
                page.cards = value;
            }
        },
        
        showConfigModal: false,
        editingCard: {
            id: 0,
            type: 'sales',
            title: '',
            metric: 'total_sales',
            format: 'currency',
            width: 1,
            height: 1,
            area: '',
            areaName: '',
            group: '',
            groupName: '',
            period: 'current_month',
            color: '#0d6efd',
            value: 0,
            loading: false,
            showComparison: false,
            // Поля для графиков
            chartType: 'line',
            chartMetrics: ['sales']
        },
        chartInstances: {}, // Хранилище экземпляров Chart.js
        draggedCard: null,
        availableAreas: [],
        areaHierarchy: [],
        expandedAreas: [],
        availableGroups: [],
        availableDivisions: [],
        groupHierarchy: [],
        expandedGroups: [],
        expandedDebtGroups: [],
        nextId: 1,
        configActiveTab: 'basic',
        
        async init() {
            // Применяем тему при загрузке
            this.applyTheme();
            
            await this.loadAreas();
            await this.loadGroups();
            await this.loadDivisions();
            await this.loadLayout();
        },
        
        toggleTheme() {
            this.darkTheme = !this.darkTheme;
            localStorage.setItem('dashboardTheme', this.darkTheme ? 'dark' : 'light');
            this.applyTheme();
            
            // Перерендерим все графики с новыми цветами
            this.$nextTick(() => {
                this.cards.filter(c => c.type === 'chart' && c.chartData).forEach(card => {
                    this.renderChart(card);
                });
            });
        },
        
        applyTheme() {
            if (this.darkTheme) {
                document.body.classList.remove('light-theme');
                document.body.classList.add('dark-theme');
            } else {
                document.body.classList.remove('dark-theme');
                document.body.classList.add('light-theme');
            }
        },
        
        // ========== УПРАВЛЕНИЕ СТРАНИЦАМИ ==========
        addPage() {
            const newPage = {
                id: this.nextPageId++,
                name: `Страница ${this.pages.length + 1}`,
                cards: []
            };
            this.pages.push(newPage);
            this.currentPageId = newPage.id;
            this.saveLayout();
        },
        
        switchPage(pageId) {
            if (this.currentPageId !== pageId) {
                // Уничтожаем графики текущей страницы
                Object.keys(this.chartInstances).forEach(key => {
                    if (this.chartInstances[key]) {
                        this.chartInstances[key].destroy();
                        delete this.chartInstances[key];
                    }
                });
                
                this.currentPageId = pageId;
                
                // Загружаем данные для карточек новой страницы
                this.$nextTick(() => {
                    const page = this.pages.find(p => p.id === pageId);
                    if (page) {
                        page.cards.forEach(card => {
                            if (card.type === 'chart') {
                                this.loadChartData(card);
                            } else if (card.type === 'areas_table') {
                                this.loadAreasTableData(card);
                            } else if (!card.isTextOnly && card.type !== 'header' && card.type !== 'text') {
                                this.loadCardData(card);
                            }
                        });
                    }
                });
            }
        },
        
        deletePage(pageId) {
            if (this.pages.length <= 1) {
                this.showToast('Нельзя удалить последнюю страницу', 'error');
                return;
            }
            
            if (!confirm('Удалить эту страницу и все её карточки?')) {
                return;
            }
            
            const index = this.pages.findIndex(p => p.id === pageId);
            if (index !== -1) {
                this.pages.splice(index, 1);
                
                // Если удалили текущую страницу, переключаемся на первую
                if (this.currentPageId === pageId) {
                    this.currentPageId = this.pages[0].id;
                }
                
                this.saveLayout();
                this.showToast('Страница удалена', 'success');
            }
        },
        
        startEditPageName(pageId) {
            const page = this.pages.find(p => p.id === pageId);
            if (page) {
                this.editingPageId = pageId;
                this.editingPageName = page.name;
                this.$nextTick(() => {
                    const input = this.$refs.pageNameInput;
                    if (input) {
                        input.focus();
                        input.select();
                    }
                });
            }
        },
        
        savePageName(pageId) {
            const page = this.pages.find(p => p.id === pageId);
            if (page && this.editingPageName.trim()) {
                page.name = this.editingPageName.trim();
                this.saveLayout();
            }
            this.editingPageId = null;
            this.editingPageName = '';
        },
        
        cancelEditPageName() {
            this.editingPageId = null;
            this.editingPageName = '';
        },
        
        dragPageStart(event, index) {
            this.draggedPageIndex = index;
            event.dataTransfer.effectAllowed = 'move';
        },
        
        dropPage(event, targetIndex) {
            if (this.draggedPageIndex !== null && this.draggedPageIndex !== targetIndex) {
                const [draggedPage] = this.pages.splice(this.draggedPageIndex, 1);
                this.pages.splice(targetIndex, 0, draggedPage);
                this.saveLayout();
            }
            this.draggedPageIndex = null;
        },
        
        async loadAreas() {
            try {
                // Загружаем плоский список для обратной совместимости
                const response = await fetch('/api/sales-areas-list');
                const result = await response.json();
                console.log('Sales Areas loaded:', result);
                if (result.success) {
                    this.availableAreas = result.data;
                    console.log('Available areas:', this.availableAreas.length);
                }
                
                // Загружаем иерархию
                const hierResponse = await fetch('/api/sales-areas-hierarchy');
                const hierResult = await hierResponse.json();
                if (hierResult.success) {
                    this.areaHierarchy = hierResult.data;
                    console.log('Area hierarchy loaded:', this.areaHierarchy.length);
                }
            } catch (error) {
                console.error('Error loading areas:', error);
            }
        },
        
        async loadGroups() {
            try {
                // Загружаем иерархию групп
                const response = await fetch('/api/customer-groups-hierarchy');
                const result = await response.json();
                console.log('Customer Groups loaded:', result);
                if (result.success) {
                    this.groupHierarchy = result.data.hierarchy;
                    this.availableGroups = result.data.flat;
                    console.log('Group hierarchy:', this.groupHierarchy.length, 'Flat groups:', this.availableGroups.length);
                }
            } catch (error) {
                console.error('Error loading groups:', error);
            }
        },
        
        async loadDivisions() {
            try {
                const response = await fetch('/api/product-groups');
                const result = await response.json();
                console.log('Divisions loaded:', result);
                if (result.success) {
                    this.availableDivisions = result.data;
                    console.log('Available divisions:', this.availableDivisions.length);
                }
            } catch (error) {
                console.error('Error loading divisions:', error);
            }
        },
        
        // Функции для работы с иерархией территорий (Sales Areas)
        toggleAreaExpand(code) {
            const idx = this.expandedAreas.indexOf(code);
            if (idx === -1) {
                this.expandedAreas.push(code);
            } else {
                this.expandedAreas.splice(idx, 1);
            }
        },
        
        isAreaSelected(code) {
            return (this.editingCard.areas || []).includes(code);
        },
        
        isAllAreaChildrenSelected(parentArea) {
            if (!parentArea.children || parentArea.children.length === 0) return false;
            return parentArea.children.every(child => this.isAreaSelected(child.code));
        },
        
        isSomeAreaChildrenSelected(parentArea) {
            if (!parentArea.children || parentArea.children.length === 0) return false;
            const selected = parentArea.children.filter(child => this.isAreaSelected(child.code)).length;
            return selected > 0 && selected < parentArea.children.length;
        },
        
        toggleParentArea(parentArea, checked) {
            if (!this.editingCard.areas) this.editingCard.areas = [];
            
            // Если есть дочерние элементы - работаем с ними
            if (parentArea.children && parentArea.children.length > 0) {
                parentArea.children.forEach(child => {
                    this.toggleArea(child.code, checked);
                });
                // Раскрываем группу при выборе
                if (checked && !this.expandedAreas.includes(parentArea.code)) {
                    this.expandedAreas.push(parentArea.code);
                }
            } else {
                // Если нет дочерних - это конечный элемент
                this.toggleArea(parentArea.code, checked);
            }
        },
        
        // Функции для работы с иерархией групп
        toggleGroupExpand(code) {
            const idx = this.expandedGroups.indexOf(code);
            if (idx === -1) {
                this.expandedGroups.push(code);
            } else {
                this.expandedGroups.splice(idx, 1);
            }
        },
        
        isGroupSelected(code) {
            return (this.editingCard.groups || []).includes(code);
        },
        
        isAllChildrenSelected(parentGroup) {
            if (!parentGroup.children || parentGroup.children.length === 0) return false;
            return parentGroup.children.every(child => this.isGroupSelected(child.code));
        },
        
        isSomeChildrenSelected(parentGroup) {
            if (!parentGroup.children || parentGroup.children.length === 0) return false;
            const selected = parentGroup.children.filter(child => this.isGroupSelected(child.code)).length;
            return selected > 0 && selected < parentGroup.children.length;
        },
        
        toggleGroup(code, checked) {
            if (!this.editingCard.groups) this.editingCard.groups = [];
            if (checked) {
                if (!this.editingCard.groups.includes(code)) {
                    this.editingCard.groups.push(code);
                }
            } else {
                this.editingCard.groups = this.editingCard.groups.filter(g => g !== code);
            }
        },
        
        toggleParentGroup(parentGroup, checked) {
            if (!this.editingCard.groups) this.editingCard.groups = [];
            
            // Добавляем/удаляем саму родительскую группу
            this.toggleGroup(parentGroup.code, checked);
            
            // Добавляем/удаляем все дочерние группы
            if (parentGroup.children && parentGroup.children.length > 0) {
                parentGroup.children.forEach(child => {
                    this.toggleGroup(child.code, checked);
                });
                // Раскрываем группу при выборе
                if (checked && !this.expandedGroups.includes(parentGroup.code)) {
                    this.expandedGroups.push(parentGroup.code);
                }
            }
        },
        
        // Функции для debt_groups (отдельный фильтр групп для долга)
        isDebtGroupSelected(code) {
            return this.editingCard.debt_groups && this.editingCard.debt_groups.includes(code);
        },
        
        isAllDebtChildrenSelected(parentGroup) {
            if (!parentGroup.children || parentGroup.children.length === 0) return false;
            return parentGroup.children.every(child => this.isDebtGroupSelected(child.code));
        },
        
        isSomeDebtChildrenSelected(parentGroup) {
            if (!parentGroup.children || parentGroup.children.length === 0) return false;
            const selected = parentGroup.children.filter(child => this.isDebtGroupSelected(child.code)).length;
            return selected > 0 && selected < parentGroup.children.length;
        },
        
        toggleDebtGroup(code, checked) {
            if (!this.editingCard.debt_groups) this.editingCard.debt_groups = [];
            if (checked) {
                if (!this.editingCard.debt_groups.includes(code)) {
                    this.editingCard.debt_groups.push(code);
                }
            } else {
                this.editingCard.debt_groups = this.editingCard.debt_groups.filter(g => g !== code);
            }
        },
        
        toggleDebtParentGroup(parentGroup, checked) {
            if (!this.editingCard.debt_groups) this.editingCard.debt_groups = [];
            this.toggleDebtGroup(parentGroup.code, checked);
            if (parentGroup.children && parentGroup.children.length > 0) {
                parentGroup.children.forEach(child => {
                    this.toggleDebtGroup(child.code, checked);
                });
                if (checked && !this.expandedDebtGroups.includes(parentGroup.code)) {
                    this.expandedDebtGroups.push(parentGroup.code);
                }
            }
        },
        
        toggleDebtGroupExpand(code) {
            const idx = this.expandedDebtGroups.indexOf(code);
            if (idx >= 0) {
                this.expandedDebtGroups.splice(idx, 1);
            } else {
                this.expandedDebtGroups.push(code);
            }
        },
        
        removeDebtGroup(code) {
            if (this.editingCard.debt_groups) {
                this.editingCard.debt_groups = this.editingCard.debt_groups.filter(g => g !== code);
            }
        },
        
        // Функции для настройки колонок таблицы
        getDefaultTableColumns() {
            return [
                { id: 'territory', name: 'Территория', visible: true },
                { id: 'today_status', name: '✓/✗ (Сегодня)', visible: true },
                { id: 'today', name: 'Сегодня', visible: true },
                { id: 'sales', name: 'Продажи', visible: true },
                { id: 'plan', name: 'План', visible: true },
                { id: 'plan_pct', name: '% плана', visible: true },
                { id: 'forecast', name: 'Прогноз', visible: true },
                { id: 'prev_year', name: 'Пр.год', visible: true },
                { id: 'trend', name: 'Тренд', visible: true },
                { id: 'debt', name: 'Долг', visible: true },
                { id: 'debt_plan', name: 'План долга', visible: true },
                { id: 'debt_pct', name: '% долга', visible: true }
            ];
        },
        
        toggleColumnVisibility(colId, visible) {
            if (!this.editingCard.tableColumns) {
                this.editingCard.tableColumns = this.getDefaultTableColumns();
            }
            const col = this.editingCard.tableColumns.find(c => c.id === colId);
            if (col) {
                col.visible = visible;
            }
        },
        
        moveColumn(targetId, draggedId) {
            if (!draggedId || targetId === draggedId) return;
            
            if (!this.editingCard.tableColumns) {
                this.editingCard.tableColumns = this.getDefaultTableColumns();
            }
            
            const cols = this.editingCard.tableColumns;
            const draggedIdx = cols.findIndex(c => c.id === draggedId);
            const targetIdx = cols.findIndex(c => c.id === targetId);
            
            if (draggedIdx === -1 || targetIdx === -1) return;
            
            // Удаляем перетаскиваемый элемент
            const [draggedCol] = cols.splice(draggedIdx, 1);
            // Вставляем на место целевого
            cols.splice(targetIdx, 0, draggedCol);
        },
        
        showAllColumns() {
            if (!this.editingCard.tableColumns) {
                this.editingCard.tableColumns = this.getDefaultTableColumns();
            }
            this.editingCard.tableColumns.forEach(col => col.visible = true);
        },
        
        resetColumnsOrder() {
            this.editingCard.tableColumns = this.getDefaultTableColumns();
        },
        
        getVisibleColumns(card) {
            const cols = card.tableColumns || this.getDefaultTableColumns();
            return cols.filter(c => c.visible);
        },
        
        isColumnVisible(card, colId) {
            const cols = card.tableColumns || this.getDefaultTableColumns();
            const col = cols.find(c => c.id === colId);
            return col ? col.visible : true;
        },
        
        async loadLayout() {
            try {
                const response = await fetch('/api/dashboard-builder/layout');
                const result = await response.json();
                if (result.success && result.data) {
                    // Проверяем новый формат с pages
                    if (result.data.pages && Array.isArray(result.data.pages)) {
                        this.pages = result.data.pages;
                        this.nextPageId = result.data.nextPageId || (Math.max(...this.pages.map(p => p.id)) + 1);
                        this.currentPageId = result.data.currentPageId || this.pages[0]?.id || 1;
                    } else {
                        // Старый формат - конвертируем в новый
                        this.pages = [{
                            id: 1,
                            name: 'Главная',
                            cards: result.data.cards || []
                        }];
                        this.nextPageId = 2;
                        this.currentPageId = 1;
                    }
                    
                    this.nextId = result.data.nextId || 1;
                    
                    // Ждём следующий тик чтобы DOM обновился
                    this.$nextTick(() => {
                        // Загрузить данные для карточек текущей страницы
                        const currentPage = this.pages.find(p => p.id === this.currentPageId);
                        if (currentPage) {
                            for (let i = 0; i < currentPage.cards.length; i++) {
                                const card = currentPage.cards[i];
                                // Сбрасываем флаги загрузки
                                card.loading = false;
                                card.chartLoading = false;
                                // Инициализируем areasTableData если не существует
                                if (card.type === 'areas_table' && !card.areasTableData) {
                                    card.areasTableData = [];
                                }
                                
                                if (card.type === 'chart') {
                                    this.loadChartData(card);
                                } else if (card.type === 'areas_table') {
                                    this.loadAreasTableData(card);
                                } else if (!card.isTextOnly && card.type !== 'header' && card.type !== 'text') {
                                    this.loadCardData(card);
                                }
                            }
                        }
                    });
                }
            } catch (error) {
                console.error('Error loading layout:', error);
            }
        },
        
        async saveLayout() {
            try {
                // Очищаем временные поля перед сохранением
                const pagesToSave = this.pages.map(page => ({
                    id: page.id,
                    name: page.name,
                    cards: page.cards.map(card => {
                        const cleanCard = { ...card };
                        // Не сохраняем временные поля загрузки
                        delete cleanCard.loading;
                        delete cleanCard.chartLoading;
                        return cleanCard;
                    })
                }));
                
                const response = await fetch('/api/dashboard-builder/layout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        pages: pagesToSave,
                        nextPageId: this.nextPageId,
                        currentPageId: this.currentPageId,
                        nextId: this.nextId
                    })
                });
                const result = await response.json();
                // Сохранение без уведомления
            } catch (error) {
                console.error('Error saving layout:', error);
                this.showToast('Ошибка сохранения', 'error');
            }
        },
        
        addCard(type) {
            const cardDefaults = {
                sales: { title: 'Продажи', metric: 'total_sales', format: 'currency', icon: 'fa-dollar-sign' },
                payments: { title: 'Оплаты', metric: 'total_payments', format: 'currency', icon: 'fa-credit-card' },
                debt: { title: 'Долг', metric: 'total_debt', format: 'currency', icon: 'fa-hand-holding-usd' },
                customers: { title: 'Клиенты', metric: 'customer_count', format: 'number', icon: 'fa-users' },
                chart: { title: 'График', metric: null, format: null, icon: 'fa-chart-line' },
                custom: { title: 'Карточка', metric: 'total_sales', format: 'currency', icon: 'fa-cog' },
                header: { title: 'Заголовок секции', metric: null, format: 'text', icon: 'fa-heading', isTextOnly: true },
                text: { title: 'Текстовая заметка', metric: null, format: 'text', icon: 'fa-font', isTextOnly: true },
                // Карточки прогнозирования
                forecast: { title: 'Прогноз продаж', metric: 'forecast_sales', format: 'currency', icon: 'fa-chart-line' },
                completion: { title: '% выполнения', metric: 'forecast_completion', format: 'percent', icon: 'fa-bullseye' },
                days: { title: 'Осталось дней', metric: 'days_remaining', format: 'number', icon: 'fa-hourglass-half' },
                daily: { title: 'Ср. в день', metric: 'daily_avg', format: 'currency', icon: 'fa-calculator' },
                needed: { title: 'Нужно в день', metric: 'needed_daily', format: 'currency', icon: 'fa-tasks' },
                gap: { title: 'До плана', metric: 'plan_gap', format: 'currency', icon: 'fa-balance-scale' },
                // Сводная таблица по территориям
                areas_table: { title: 'Сводка по территориям', metric: null, format: null, icon: 'fa-table', isAreasTable: true }
            };
            
            const defaults = cardDefaults[type] || cardDefaults.custom;
            
            const card = {
                id: this.nextId++,
                type: type,
                title: defaults.title,
                metric: defaults.metric,
                format: defaults.format,
                width: type === 'chart' ? 2 : (type === 'header' ? 4 : 1),
                height: type === 'chart' ? 2 : 1,
                area: '',
                areaName: '',
                group: '',
                groupName: '',
                period: 'current_month',
                color: this.getCardColor(type),
                value: 0,
                loading: defaults.isTextOnly ? false : true,
                showComparison: false,
                comparison: null,
                currentDay: new Date().getDate(),
                isTextOnly: defaults.isTextOnly || false,
                isAreasTable: defaults.isAreasTable || false,
                areasTableData: [],  // Данные для таблицы территорий - пустой массив по умолчанию
                textContent: defaults.isTextOnly ? 'Введите текст...' : '',
                // Параметры графика
                chartType: type === 'chart' ? 'line' : null,
                chartMetrics: type === 'chart' ? ['sales'] : [],
                comparePeriods: type === 'chart' ? ['current'] : [],
                compareYears: [],  // Для сравнения по годам
                yearCompareMode: 'year'  // 'year' или 'month'
            };
            
            // Для таблицы территорий - больший размер по умолчанию
            if (type === 'areas_table') {
                card.width = 2;
                card.height = 2;
            }
            
            this.cards.push(card);
            console.log('Card added:', card);
            console.log('Total cards now:', this.cards.length, this.cards.map(c => ({id: c.id, title: c.title})));
            
            // Загружаем данные только для карточек с метриками
            if (!defaults.isTextOnly) {
                if (type === 'chart') {
                    this.loadChartData(card);
                } else if (type === 'areas_table') {
                    this.loadAreasTableData(card);
                } else {
                    this.loadCardData(card);
                }
            }
            this.saveLayout(); // Автосохранение при добавлении карточки
            
            // Фокус и анимация на новую карточку
            this.focusOnCard(card.id);
        },
        
        async loadCardData(card) {
            card.loading = true;
            try {
                const params = new URLSearchParams();
                // Поддержка массивов areas, groups и divisions
                if (card.areas && card.areas.length > 0) {
                    card.areas.forEach(a => params.append('areas', a));
                } else if (card.area) {
                    params.append('areas', card.area);
                }
                if (card.groups && card.groups.length > 0) {
                    card.groups.forEach(g => params.append('groups', g));
                } else if (card.group) {
                    params.append('groups', card.group);
                }
                if (card.divisions && card.divisions.length > 0) {
                    card.divisions.forEach(d => params.append('divisions', d));
                }
                params.append('period', card.period || 'current_month');
                params.append('metric', card.metric);
                if (card.showComparison) params.append('show_comparison', 'true');
                
                const response = await fetch(`/api/dashboard-builder/card-data?${params}`);
                const result = await response.json();
                
                if (result.success) {
                    card.value = result.data.value;
                    card.comparison = result.data.comparison;
                    card.forecast = result.data.forecast;
                    card.currentDay = result.data.current_day;
                    // Обновляем имена для отображения
                    if (card.areas && card.areas.length > 0) {
                        card.areaNames = card.areas.map(a => this.availableAreas.find(ar => ar.code === a)?.name || a);
                    }
                    if (card.groups && card.groups.length > 0) {
                        card.groupNames = card.groups.map(g => this.availableGroups.find(gr => gr.code === g)?.name || g);
                    }
                    if (card.divisions && card.divisions.length > 0) {
                        card.divisionNames = card.divisions.map(d => this.availableDivisions.find(dv => dv.code === d)?.name || d);
                    }
                }
            } catch (error) {
                console.error('Error loading card data:', error);
                card.value = 0;
            }
            card.loading = false;
        },
        
        // Загрузка данных для графика
        async loadChartData(card) {
            console.log('loadChartData called for card:', card.id);
            
            // Ищем карточку во всех страницах
            let foundCard = null;
            let foundPage = null;
            let foundIndex = -1;
            
            for (const page of this.pages) {
                const idx = page.cards.findIndex(c => c.id === card.id);
                if (idx !== -1) {
                    foundCard = page.cards[idx];
                    foundPage = page;
                    foundIndex = idx;
                    break;
                }
            }
            
            if (!foundCard) {
                console.error('Card not found in any page:', card.id);
                return;
            }
            
            // Устанавливаем loading через присвоение нового объекта для реактивности
            foundPage.cards[foundIndex] = { ...foundPage.cards[foundIndex], loading: true };
            
            try {
                const params = new URLSearchParams();
                
                // Фильтры
                const currentCard = foundPage.cards[foundIndex];
                if (currentCard.areas && currentCard.areas.length > 0) {
                    currentCard.areas.forEach(a => params.append('areas', a));
                }
                if (currentCard.groups && currentCard.groups.length > 0) {
                    currentCard.groups.forEach(g => params.append('groups', g));
                }
                if (currentCard.divisions && currentCard.divisions.length > 0) {
                    currentCard.divisions.forEach(d => params.append('divisions', d));
                }
                
                params.append('period', currentCard.period || 'current_month');
                params.append('chart_type', currentCard.chartType || 'line');
                
                // Метрики
                const metrics = currentCard.chartMetrics || ['sales'];
                metrics.forEach(m => params.append('metrics', m));
                
                // Периоды для сравнения
                const comparePeriods = currentCard.comparePeriods || ['current'];
                comparePeriods.forEach(p => params.append('compare_periods', p));
                
                // Годы для сравнения
                const compareYears = currentCard.compareYears || [];
                compareYears.forEach(y => params.append('compare_years', y));
                
                // Режим сравнения годов (year или month)
                params.append('year_compare_mode', currentCard.yearCompareMode || 'year');
                
                console.log('Chart API request:', `/api/dashboard-builder/chart-data?${params}`);
                
                const response = await fetch(`/api/dashboard-builder/chart-data?${params}`);
                const result = await response.json();
                
                console.log('Chart API response:', result);
                
                if (result.success && result.data) {
                    // Обновляем данные через новый объект для реактивности
                    foundPage.cards[foundIndex] = { 
                        ...foundPage.cards[foundIndex], 
                        chartData: result.data, 
                        loading: false 
                    };
                    console.log('Card loading set to false, card:', foundPage.cards[foundIndex].id, 'loading:', foundPage.cards[foundIndex].loading);
                    
                    // Ждём обновления DOM и рендерим график
                    this.$nextTick(() => {
                        setTimeout(() => {
                            this.renderChart(foundPage.cards[foundIndex]);
                        }, 100);
                    });
                } else {
                    console.error('Chart data error:', result);
                    foundPage.cards[foundIndex] = { ...foundPage.cards[foundIndex], loading: false };
                }
            } catch (error) {
                console.error('Error loading chart data:', error);
                foundPage.cards[foundIndex] = { ...foundPage.cards[foundIndex], loading: false };
            }
        },
        
        // Загрузка данных для таблицы территорий
        async loadAreasTableData(card) {
            console.log('loadAreasTableData called for card:', card.id);
            
            // Ищем карточку во всех страницах
            let foundCard = null;
            let foundPage = null;
            let foundIndex = -1;
            
            for (const page of this.pages) {
                const idx = page.cards.findIndex(c => c.id === card.id);
                if (idx !== -1) {
                    foundCard = page.cards[idx];
                    foundPage = page;
                    foundIndex = idx;
                    break;
                }
            }
            
            if (!foundCard) {
                console.error('Card not found in any page:', card.id);
                return;
            }
            
            // Если территории не выбраны - показываем пустое состояние
            if (!card.areas || card.areas.length === 0) {
                foundPage.cards[foundIndex].areasTableData = [];
                foundPage.cards[foundIndex].loading = false;
                return;
            }
            
            foundPage.cards[foundIndex].loading = true;
            
            try {
                const params = new URLSearchParams();
                
                // Выбранные территории
                card.areas.forEach(a => params.append('areas', a));
                
                // Фильтры (опционально)
                if (card.groups && card.groups.length > 0) {
                    card.groups.forEach(g => params.append('groups', g));
                }
                // Отдельный фильтр групп для долга
                if (card.debt_groups && card.debt_groups.length > 0) {
                    card.debt_groups.forEach(g => params.append('debt_groups', g));
                }
                if (card.divisions && card.divisions.length > 0) {
                    card.divisions.forEach(d => params.append('divisions', d));
                }
                
                params.append('period', card.period || 'current_month');
                
                console.log('Areas Table API request:', `/api/dashboard-builder/areas-table-data?${params}`);
                
                const response = await fetch(`/api/dashboard-builder/areas-table-data?${params}`);
                const result = await response.json();
                
                console.log('Areas Table API response:', result);
                
                if (result.success && result.data) {
                    console.log('Areas Table data received:', result.data.length, 'rows');
                    console.log('First row:', result.data[0]);
                    
                    // Используем прямое присваивание для лучшей реактивности Alpine.js
                    foundPage.cards[foundIndex].areasTableData = result.data;
                    foundPage.cards[foundIndex].loading = false;
                    
                    // Принудительно обновляем UI
                    this.$nextTick(() => {
                        console.log('Areas table updated, card:', foundPage.cards[foundIndex].id);
                        console.log('areasTableData length:', foundPage.cards[foundIndex].areasTableData?.length);
                    });
                } else {
                    console.error('Areas Table data error:', result);
                    foundPage.cards[foundIndex].areasTableData = [];
                    foundPage.cards[foundIndex].loading = false;
                }
            } catch (error) {
                console.error('Error loading areas table data:', error);
                foundPage.cards[foundIndex].areasTableData = [];
                foundPage.cards[foundIndex].loading = false;
            }
        },
        
        // Рендеринг графика по ID карточки (вызывается через событие)
        renderChartById(cardId) {
            const card = this.cards.find(c => c.id === cardId);
            if (card && card.chartData) {
                this.renderChart(card);
            }
        },
        
        // Рендеринг графика Chart.js
        renderChart(card, attempt = 0) {
            const wrapperId = 'chart-wrapper-' + card.id;
            const wrapper = document.getElementById(wrapperId);
            
            if (!wrapper) {
                console.warn('Chart wrapper not found:', wrapperId, 'attempt:', attempt);
                if (attempt < 10) {
                    setTimeout(() => this.renderChart(card, attempt + 1), 200);
                } else {
                    console.error('Chart wrapper not found after 10 attempts:', wrapperId);
                }
                return;
            }
            
            console.log('Rendering chart in wrapper:', wrapperId, 'data:', card.chartData);
            
            // Уничтожаем предыдущий экземпляр если есть
            if (this.chartInstances[card.id]) {
                try {
                    this.chartInstances[card.id].destroy();
                } catch (e) {
                    console.warn('Error destroying chart:', e);
                }
                delete this.chartInstances[card.id];
            }
            
            // Удаляем старый canvas если есть
            const oldCanvas = wrapper.querySelector('canvas');
            if (oldCanvas) {
                oldCanvas.remove();
            }
            
            // Создаём новый canvas через JS (вне контроля Alpine)
            const canvas = document.createElement('canvas');
            canvas.id = 'chart-canvas-' + card.id;
            // НЕ задаём стили width/height - Chart.js сам управляет размером
            wrapper.appendChild(canvas);
            
            const ctx = canvas.getContext('2d');
            if (!ctx) {
                console.error('Cannot get 2d context for canvas');
                return;
            }
            
            const chartType = card.chartType || 'line';
            
            // Определяем цвета в зависимости от темы
            const isLightTheme = !this.darkTheme;
            const textColor = isLightTheme ? 'rgba(30, 41, 59, 0.8)' : 'rgba(255,255,255,0.8)';
            const gridColor = isLightTheme ? 'rgba(100, 116, 139, 0.15)' : 'rgba(255,255,255,0.1)';
            const tickColor = isLightTheme ? 'rgba(51, 65, 85, 0.7)' : 'rgba(255,255,255,0.7)';
            const tooltipBg = isLightTheme ? 'rgba(15, 23, 42, 0.9)' : 'rgba(0,0,0,0.8)';
            
            // Конфигурация графика
            const config = {
                type: chartType === 'area' ? 'line' : chartType,
                data: card.chartData,
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false, // Отключаем анимацию для стабильности
                    layout: {
                        padding: {
                            left: 5,
                            right: 10,
                            top: 5,
                            bottom: 5
                        }
                    },
                    interaction: {
                        mode: 'index',
                        intersect: false
                    },
                    plugins: {
                        legend: {
                            display: true,
                            position: 'top',
                            labels: {
                                color: textColor,
                                font: { size: 11, weight: '500' },
                                padding: 12,
                                usePointStyle: true
                            }
                        },
                        tooltip: {
                            backgroundColor: tooltipBg,
                            titleColor: '#fff',
                            bodyColor: '#fff',
                            padding: 12,
                            cornerRadius: 8,
                            displayColors: true,
                            callbacks: {
                                label: function(context) {
                                    const value = context.parsed.y;
                                    return context.dataset.label + ': ' + new Intl.NumberFormat('ru-RU').format(Math.round(value)) + ' ֏';
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            grid: {
                                color: gridColor,
                                drawBorder: false
                            },
                            ticks: {
                                color: tickColor,
                                font: { size: 10 }
                            }
                        },
                        y: {
                            beginAtZero: true,
                            grid: {
                                color: gridColor,
                                drawBorder: false
                            },
                            ticks: {
                                color: tickColor,
                                font: { size: 10 },
                                callback: function(value) {
                                    if (value >= 1000000) {
                                        return (value / 1000000).toFixed(1) + 'М';
                                    } else if (value >= 1000) {
                                        return (value / 1000).toFixed(0) + 'К';
                                    }
                                    return value;
                                }
                            }
                        }
                    }
                }
            };
            
            // Для круговой диаграммы другие настройки
            if (chartType === 'pie' || chartType === 'doughnut') {
                config.options.scales = {};
                config.options.plugins.legend.position = 'right';
                config.options.plugins.legend.labels.color = textColor;
            }
            
            this.chartInstances[card.id] = new Chart(ctx, config);
        },
        
        editCard(card) {
            console.log('editCard called with card:', card);
            console.log('card.id:', card.id, 'type:', typeof card.id);
            this.editingCard = {
                id: card.id,
                type: card.type,
                title: card.title,
                metric: card.metric,
                format: card.format,
                width: card.width,
                height: card.height,
                areas: card.areas || (card.area ? [card.area] : []),
                groups: card.groups || (card.group ? [card.group] : []),
                debt_groups: card.debt_groups || [],
                divisions: card.divisions || [],
                period: card.period || 'current_month',
                color: card.color || '#0d6efd',
                customColor: card.customColor || null,
                value: card.value || 0,
                loading: false,
                showComparison: card.showComparison || false,
                isTextOnly: card.isTextOnly || false,
                isAreasTable: card.isAreasTable || false,
                textContent: card.textContent || '',
                tableColumns: card.tableColumns || null,
                // Параметры графика
                chartType: card.chartType || 'line',
                chartMetrics: card.chartMetrics || ['sales'],
                comparePeriods: card.comparePeriods || ['current'],
                compareYears: card.compareYears || [],
                yearCompareMode: card.yearCompareMode || 'year'
            };
            console.log('editingCard set to:', this.editingCard);
            this.configActiveTab = 'basic';
            this.showConfigModal = true;
        },
        
        saveCardConfig() {
            console.log('saveCardConfig called, editingCard:', this.editingCard);
            console.log('Looking for card with id:', this.editingCard.id);
            console.log('Current cards:', JSON.stringify(this.cards.map(c => ({id: c.id, title: c.title}))));
            
            const index = this.cards.findIndex(c => c.id === this.editingCard.id);
            console.log('Found index:', index);
            
            if (index !== -1) {
                // Обновляем существующую карточку
                const isChart = this.cards[index].type === 'chart';
                Object.assign(this.cards[index], {
                    title: this.editingCard.title,
                    metric: this.editingCard.metric,
                    format: this.editingCard.format,
                    width: parseInt(this.editingCard.width) || 1,
                    height: parseInt(this.editingCard.height) || 1,
                    areas: [...(this.editingCard.areas || [])],
                    groups: [...(this.editingCard.groups || [])],
                    debt_groups: [...(this.editingCard.debt_groups || [])],
                    divisions: [...(this.editingCard.divisions || [])],
                    period: this.editingCard.period,
                    color: this.editingCard.color,
                    customColor: this.editingCard.customColor || null,
                    showComparison: this.editingCard.showComparison,
                    isTextOnly: this.editingCard.isTextOnly || false,
                    isAreasTable: this.editingCard.isAreasTable || false,
                    textContent: this.editingCard.textContent || '',
                    tableColumns: this.editingCard.tableColumns ? JSON.parse(JSON.stringify(this.editingCard.tableColumns)) : null,
                    // Параметры графика
                    chartType: this.editingCard.chartType || 'line',
                    chartMetrics: [...(this.editingCard.chartMetrics || ['sales'])],
                    comparePeriods: [...(this.editingCard.comparePeriods || ['current'])],
                    compareYears: [...(this.editingCard.compareYears || [])],
                    yearCompareMode: this.editingCard.yearCompareMode || 'year'
                });
                console.log('Card updated:', this.cards[index]);
                // Загружаем данные
                if (!this.cards[index].isTextOnly && this.cards[index].type !== 'header' && this.cards[index].type !== 'text') {
                    if (isChart) {
                        this.loadChartData(this.cards[index]);
                    } else if (this.cards[index].type === 'areas_table' || this.cards[index].isAreasTable) {
                        this.loadAreasTableData(this.cards[index]);
                    } else {
                        this.loadCardData(this.cards[index]);
                    }
                }
                this.showToast('Карточка обновлена!', 'success');
            } else {
                // Карточка не найдена - создаём новую
                console.log('Card not found, creating new card');
                const newCard = {
                    id: this.nextId++,
                    type: this.editingCard.type || 'custom',
                    title: this.editingCard.title,
                    metric: this.editingCard.metric,
                    format: this.editingCard.format,
                    width: parseInt(this.editingCard.width) || 1,
                    height: parseInt(this.editingCard.height) || 1,
                    areas: [...(this.editingCard.areas || [])],
                    groups: [...(this.editingCard.groups || [])],
                    debt_groups: [...(this.editingCard.debt_groups || [])],
                    divisions: [...(this.editingCard.divisions || [])],
                    period: this.editingCard.period,
                    color: this.editingCard.color,
                    customColor: this.editingCard.customColor || null,
                    showComparison: this.editingCard.showComparison,
                    isTextOnly: this.editingCard.isTextOnly || false,
                    textContent: this.editingCard.textContent || '',
                    tableColumns: this.editingCard.tableColumns ? JSON.parse(JSON.stringify(this.editingCard.tableColumns)) : null,
                    comparison: null,
                    currentDay: new Date().getDate(),
                    value: 0,
                    loading: this.editingCard.isTextOnly ? false : true
                };
                this.cards.push(newCard);
                if (!newCard.isTextOnly && newCard.type !== 'header' && newCard.type !== 'text') {
                    this.loadCardData(newCard);
                }
                console.log('New card created:', newCard);
                this.showToast('Карточка создана!', 'success');
            }
            this.showConfigModal = false;
            this.saveLayout(); // Автосохранение после изменения
        },
        
        toggleSizePicker(cardId) {
            this.sizePickerId = this.sizePickerId === cardId ? null : cardId;
        },

        applySizePreset(card, preset) {
            card.width = preset.w;
            card.height = preset.h;
            this.sizePickerId = null;

            this.$nextTick(() => {
                // Мягкий «поп» плитки при смене размера
                const el = document.querySelector(`.dashboard-card[data-id="${card.id}"]`);
                if (el) {
                    el.classList.remove('size-pop');
                    void el.offsetWidth;
                    el.classList.add('size-pop');
                    setTimeout(() => el.classList.remove('size-pop'), 500);
                }
                // График перерисовываем под новый размер холста
                if (card.type === 'chart' && card.chartData) {
                    this.renderChart(card);
                }
            });
        },
        
        cloneCard(card) {
            // Создаём глубокую копию карточки
            const isTextCard = card.isTextOnly || card.type === 'header' || card.type === 'text';
            const isChart = card.type === 'chart';
            const clonedCard = {
                id: this.nextId++,
                title: card.title + ' (копия)',
                type: card.type,
                metric: card.metric,
                period: card.period,
                format: card.format,
                width: card.width,
                height: card.height,
                showComparison: card.showComparison,
                customColor: card.customColor || null,
                // Глубокое копирование массивов фильтров
                areas: card.areas ? [...card.areas] : [],
                groups: card.groups ? [...card.groups] : [],
                divisions: card.divisions ? [...card.divisions] : [],
                // Копирование остальных параметров
                filters: card.filters ? [...card.filters] : [],
                // Для текстовых карточек
                isTextOnly: card.isTextOnly || false,
                textContent: card.textContent || '',
                // Параметры графика
                chartType: card.chartType || 'line',
                chartMetrics: card.chartMetrics ? [...card.chartMetrics] : ['sales'],
                comparePeriods: card.comparePeriods ? [...card.comparePeriods] : ['current'],
                // Инициализация состояния
                value: 0,
                previousValue: null,
                loading: isTextCard ? false : true
            };
            
            // Добавляем карточку в список
            this.cards.push(clonedCard);
            
            // Загружаем данные
            if (!isTextCard) {
                if (isChart) {
                    this.loadChartData(clonedCard);
                } else {
                    this.loadCardData(clonedCard);
                }
            }
            
            // Сохраняем layout
            this.saveLayout();
            
            // Фокус и анимация на клонированную карточку
            this.focusOnCard(clonedCard.id);
            
            // Показываем уведомление
            this.showToast('Карточка клонирована!', 'success');
        },
        
        resetLayout() {
            if (confirm('Сбросить все карточки на этой странице?')) {
                const page = this.pages.find(p => p.id === this.currentPageId);
                if (page) {
                    page.cards = [];
                }
                this.saveLayout(); // Автосохранение при сбросе
            }
        },
        
        // ==========================================
        // Professional Drag & Drop System
        // ==========================================
        isDragging: false,
        draggedCard: null,
        draggedIndex: -1,
        dragElement: null,
        dragOffsetX: 0,
        dragOffsetY: 0,
        dragStartX: 0,
        dragStartY: 0,
        lastSwapTime: 0,      // Throttle для свопов
        swapCooldown: 150,    // Минимальный интервал между свопами
        lastTargetIndex: -1,  // Последний индекс для предотвращения повторов
        
        startDrag(event, card, index) {
            // Только в режиме редактирования
            if (!this.editMode) return;
            
            // Игнорируем клики на кнопках
            if (event.target.closest('.card-actions') || event.target.closest('.resize-handle')) {
                return;
            }
            
            const touch = event.touches ? event.touches[0] : event;
            const cardEl = event.target.closest('.dashboard-card');
            if (!cardEl) return;
            
            // Запоминаем карточку для отложенного старта
            this.pendingCard = card;
            this.pendingIndex = index;
            
            // Запоминаем начальную позицию
            this.dragStartX = touch.clientX;
            this.dragStartY = touch.clientY;
            
            // Задержка перед началом перетаскивания
            this.dragTimeout = setTimeout(() => {
                this.initDrag(touch, card, index, cardEl);
            }, 120);
            
            // Обработчики для отмены
            this.boundCheckDrag = (e) => this.checkDrag(e);
            this.boundCancelDrag = () => this.cancelDrag();
            
            document.addEventListener('mousemove', this.boundCheckDrag);
            document.addEventListener('touchmove', this.boundCheckDrag, {passive: false});
            document.addEventListener('mouseup', this.boundCancelDrag);
            document.addEventListener('touchend', this.boundCancelDrag);
        },
        
        checkDrag(event) {
            const touch = event.touches ? event.touches[0] : event;
            const dx = Math.abs(touch.clientX - this.dragStartX);
            const dy = Math.abs(touch.clientY - this.dragStartY);
            
            // Если сдвинулись больше 10px - начинаем перетаскивание сразу
            if (dx > 10 || dy > 10) {
                clearTimeout(this.dragTimeout);
                if (!this.isDragging) {
                    const cardEl = document.querySelector(`[data-id="${this.pendingCard?.id}"]`);
                    if (cardEl && this.pendingCard) {
                        this.initDrag(touch, this.pendingCard, this.pendingIndex, cardEl);
                    }
                }
            }
        },
        
        initDrag(touch, card, index, cardEl) {
            this.isDragging = true;
            this.draggedCard = card;
            this.draggedIndex = index;
            this.originalIndex = index;
            this.pendingCard = null;
            this.draggedCardEl = cardEl;
            this.lastSwapTime = 0;
            this.lastTargetIndex = -1;
            
            const rect = cardEl.getBoundingClientRect();
            this.dragOffsetX = touch.clientX - rect.left;
            this.dragOffsetY = touch.clientY - rect.top;
            
            // Включаем jiggle mode
            const cardGrid = document.querySelector('.card-grid');
            if (cardGrid) {
                cardGrid.classList.add('jiggle-mode');
            }
            
            // Создаём плавающий элемент (простой, без Alpine)
            this.dragElement = document.createElement('div');
            this.dragElement.className = 'dashboard-card is-dragging size-' + card.width + 'x' + card.height + ' card-' + card.type;
            this.dragElement.setAttribute('x-ignore', '');
            
            // Упрощённое содержимое
            const iconClass = this.getCardIcon(card.type);
            const cardColor = card.customColor || this.getCardColor(card.type);
            const formattedValue = this.formatValue(card.value, card.format);
            
            this.dragElement.innerHTML = `
                <i class="fas card-icon ${iconClass}" style="color: ${cardColor}"></i>
                <div class="d-flex align-items-center mb-2">
                    <span class="card-label">${card.title || ''}</span>
                </div>
                <div class="card-value" style="color: ${cardColor}">${formattedValue}</div>
            `;
            
            // Позиционирование
            Object.assign(this.dragElement.style, {
                position: 'fixed',
                zIndex: '10000',
                width: rect.width + 'px',
                height: rect.height + 'px',
                left: rect.left + 'px',
                top: rect.top + 'px',
                transform: 'scale(1)',
                opacity: '1',
                pointerEvents: 'none',
                willChange: 'transform, left, top',
                backfaceVisibility: 'hidden'
            });
            
            document.body.appendChild(this.dragElement);
            
            // Плавная анимация поднятия
            requestAnimationFrame(() => {
                if (this.dragElement) {
                    this.dragElement.style.transition = 'transform 0.2s cubic-bezier(0.2, 0.8, 0.2, 1), box-shadow 0.2s ease';
                    this.dragElement.style.transform = 'scale(1.05)';
                    this.dragElement.style.boxShadow = '0 20px 60px rgba(0,0,0,0.4)';
                    
                    setTimeout(() => {
                        if (this.dragElement) {
                            this.dragElement.style.transition = 'box-shadow 0.2s ease';
                        }
                    }, 200);
                }
            });
            
            // Placeholder
            cardEl.classList.add('is-placeholder');
            
            // Вибрация
            if (navigator.vibrate) {
                navigator.vibrate(10);
            }
            
            // Обработчики перемещения
            this.boundMoveDrag = (e) => this.moveDrag(e);
            this.boundEndDrag = (e) => this.endDrag(e);
            
            document.removeEventListener('mousemove', this.boundCheckDrag);
            document.removeEventListener('touchmove', this.boundCheckDrag);
            document.removeEventListener('mouseup', this.boundCancelDrag);
            document.removeEventListener('touchend', this.boundCancelDrag);
            
            document.addEventListener('mousemove', this.boundMoveDrag);
            document.addEventListener('touchmove', this.boundMoveDrag, {passive: false});
            document.addEventListener('mouseup', this.boundEndDrag);
            document.addEventListener('touchend', this.boundEndDrag);
            
            document.body.style.userSelect = 'none';
            document.body.style.cursor = 'grabbing';
        },
        
        moveDrag(event) {
            if (!this.isDragging || !this.dragElement) return;
            
            event.preventDefault();
            const touch = event.touches ? event.touches[0] : event;
            
            // Автоскролл при приближении к краям
            const scrollThreshold = 80;
            const maxScrollSpeed = 12;
            const viewportHeight = window.innerHeight;
            
            if (touch.clientY > viewportHeight - scrollThreshold) {
                const factor = 1 - (viewportHeight - touch.clientY) / scrollThreshold;
                window.scrollBy(0, Math.round(maxScrollSpeed * factor));
            } else if (touch.clientY < scrollThreshold) {
                const factor = 1 - touch.clientY / scrollThreshold;
                window.scrollBy(0, -Math.round(maxScrollSpeed * factor));
            }
            
            // Позиционирование плавающего элемента (без requestAnimationFrame для мгновенности)
            const targetX = touch.clientX - this.dragOffsetX;
            const targetY = touch.clientY - this.dragOffsetY;
            this.dragElement.style.left = targetX + 'px';
            this.dragElement.style.top = targetY + 'px';
            
            // Находим элемент под курсором
            this.dragElement.style.visibility = 'hidden';
            const elementBelow = document.elementFromPoint(touch.clientX, touch.clientY);
            this.dragElement.style.visibility = 'visible';
            
            // Проверяем зону "в конец"
            const dropZone = elementBelow?.closest('.drop-zone-end');
            if (dropZone) {
                dropZone.classList.add('drag-active');
                return; // Не обрабатываем своп если над drop-zone
            } else {
                document.querySelectorAll('.drop-zone-end.drag-active').forEach(el => el.classList.remove('drag-active'));
            }
            
            // Находим целевую карточку
            const targetCard = elementBelow?.closest('.dashboard-card:not(.is-placeholder):not(.is-dragging)');
            
            if (targetCard) {
                const targetIndex = parseInt(targetCard.dataset.index);
                const now = Date.now();
                
                // Throttle: не менять чаще чем swapCooldown мс и не на тот же индекс
                if (!isNaN(targetIndex) && 
                    targetIndex !== this.draggedIndex && 
                    targetIndex !== this.lastTargetIndex &&
                    now - this.lastSwapTime > this.swapCooldown) {
                    
                    this.lastSwapTime = now;
                    this.lastTargetIndex = targetIndex;
                    
                    // Визуальный эффект на целевой карточке
                    targetCard.style.transition = 'transform 0.15s ease';
                    targetCard.style.transform = 'scale(0.95)';
                    setTimeout(() => {
                        targetCard.style.transform = '';
                        targetCard.style.transition = '';
                    }, 150);
                    
                    // Перемещаем в массиве
                    const [removed] = this.cards.splice(this.draggedIndex, 1);
                    this.cards.splice(targetIndex, 0, removed);
                    this.draggedIndex = targetIndex;
                    
                    // Обновляем placeholder после рендера Alpine
                    this.$nextTick(() => {
                        document.querySelectorAll('.dashboard-card.is-placeholder').forEach(el => {
                            el.classList.remove('is-placeholder');
                        });
                        const newPlaceholder = document.querySelector(`[data-index="${targetIndex}"]`);
                        if (newPlaceholder) {
                            newPlaceholder.classList.add('is-placeholder');
                        }
                        // Сбрасываем lastTargetIndex после рендера
                        setTimeout(() => { this.lastTargetIndex = -1; }, 50);
                    });
                    
                    // Вибрация
                    if (navigator.vibrate) {
                        navigator.vibrate(5);
                    }
                }
            }
        },
        
        endDrag(event) {
            if (!this.isDragging) {
                this.cancelDrag();
                return;
            }
            
            // Убираем jiggle mode
            const cardGrid = document.querySelector('.card-grid');
            if (cardGrid) {
                cardGrid.classList.remove('jiggle-mode');
            }
            
            // Находим элемент под курсором
            const touch = event.changedTouches ? event.changedTouches[0] : event;
            
            if (this.dragElement) {
                this.dragElement.style.visibility = 'hidden';
            }
            
            const elementBelow = document.elementFromPoint(touch.clientX, touch.clientY);
            const dropZone = elementBelow?.closest('.drop-zone-end');
            
            if (this.dragElement) {
                this.dragElement.style.visibility = 'visible';
            }
            
            const draggedCardId = this.draggedCard?.id;
            
            // Если дроп в зону "в конец"
            if (dropZone && this.draggedCard) {
                const currentIndex = this.cards.findIndex(c => c.id === draggedCardId);
                if (currentIndex !== -1 && currentIndex !== this.cards.length - 1) {
                    const [removed] = this.cards.splice(currentIndex, 1);
                    this.cards.push(removed);
                }
            }
            
            // Анимация возврата
            const dragEl = this.dragElement;
            if (dragEl) {
                this.$nextTick(() => {
                    const targetCard = document.querySelector(`[data-id="${draggedCardId}"]`);
                    if (targetCard) {
                        const targetRect = targetCard.getBoundingClientRect();
                        
                        dragEl.style.transition = 'all 0.25s cubic-bezier(0.2, 0.8, 0.2, 1)';
                        dragEl.style.left = targetRect.left + 'px';
                        dragEl.style.top = targetRect.top + 'px';
                        dragEl.style.transform = 'scale(1)';
                        dragEl.style.opacity = '0.5';
                        
                        setTimeout(() => {
                            if (dragEl.parentNode) {
                                dragEl.remove();
                            }
                        }, 250);
                    } else {
                        dragEl.remove();
                    }
                });
                this.dragElement = null;
            }
            
            // Убираем placeholder
            document.querySelectorAll('.dashboard-card.is-placeholder').forEach(el => {
                el.classList.remove('is-placeholder');
            });
            
            // Эффект приземления
            this.$nextTick(() => {
                const droppedCard = document.querySelector(`[data-id="${draggedCardId}"]`);
                if (droppedCard) {
                    droppedCard.style.transition = 'transform 0.3s cubic-bezier(0.2, 0.8, 0.2, 1)';
                    droppedCard.style.transform = 'scale(1.03)';
                    setTimeout(() => {
                        droppedCard.style.transform = '';
                        setTimeout(() => { droppedCard.style.transition = ''; }, 300);
                    }, 150);
                }
            });
            
            // Вибрация
            if (navigator.vibrate) {
                navigator.vibrate(10);
            }
            
            this.cleanup();
            this.saveLayout();
        },
        
        cancelDrag() {
            clearTimeout(this.dragTimeout);
            this.pendingCard = null;
            this.pendingIndex = -1;
            
            // iOS: убираем jiggle mode
            const cardGrid = document.querySelector('.card-grid');
            if (cardGrid) {
                cardGrid.classList.remove('jiggle-mode');
            }
            
            document.removeEventListener('mousemove', this.boundCheckDrag);
            document.removeEventListener('touchmove', this.boundCheckDrag);
            document.removeEventListener('mouseup', this.boundCancelDrag);
            document.removeEventListener('touchend', this.boundCancelDrag);
        },
        
        dropToEnd() {
            // Просто вызываем endDrag - он сам определит что мы над drop-zone
            // Создаём фейковый event с позицией над drop-zone
            if (this.isDragging && this.draggedCard) {
                const dropZone = document.querySelector('.drop-zone-end');
                if (dropZone) {
                    const rect = dropZone.getBoundingClientRect();
                    const fakeEvent = {
                        clientX: rect.left + rect.width / 2,
                        clientY: rect.top + rect.height / 2,
                        changedTouches: null
                    };
                    this.endDrag(fakeEvent);
                }
            }
        },
        
        cleanup() {
            this.isDragging = false;
            this.draggedCard = null;
            this.draggedIndex = -1;
            this.lastSwapTime = 0;
            this.lastTargetIndex = -1;
            
            if (this.animationFrame) {
                cancelAnimationFrame(this.animationFrame);
                this.animationFrame = null;
            }
            
            // Убираем jiggle mode
            const cardGrid = document.querySelector('.card-grid');
            if (cardGrid) {
                cardGrid.classList.remove('jiggle-mode');
            }
            
            // Убираем все drag/drop классы
            document.querySelectorAll('.drop-zone-end.drag-active').forEach(el => el.classList.remove('drag-active'));
            document.querySelectorAll('.dashboard-card.is-placeholder').forEach(el => el.classList.remove('is-placeholder'));
            
            // Удаляем слушатели
            document.removeEventListener('mousemove', this.boundMoveDrag);
            document.removeEventListener('touchmove', this.boundMoveDrag);
            document.removeEventListener('mouseup', this.boundEndDrag);
            document.removeEventListener('touchend', this.boundEndDrag);
            document.removeEventListener('mousemove', this.boundCheckDrag);
            document.removeEventListener('touchmove', this.boundCheckDrag);
            document.removeEventListener('mouseup', this.boundCancelDrag);
            document.removeEventListener('touchend', this.boundCancelDrag);
            
            // Сбрасываем стили body
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
            
            // Удаляем плавающий элемент если остался
            if (this.dragElement && this.dragElement.parentNode) {
                this.dragElement.remove();
                this.dragElement = null;
            }
        },
        
        // ==========================================
        // Resize
        // ==========================================
        resizingCard: null,
        resizeIndicator: null,
        
        startResize(event, card) {
            // Только в режиме редактирования
            if (!this.editMode) return;

            event.preventDefault();
            event.stopPropagation();

            // Мышь или палец — берём общие координаты
            const pt = event.touches ? event.touches[0] : event;

            this.resizingCard = card;
            this.resizeStartX = pt.clientX;
            this.resizeStartY = pt.clientY;
            this.resizeStartWidth = card.width;
            this.resizeStartHeight = card.height;
            
            const cardEl = event.target.closest('.dashboard-card');
            cardEl.classList.add('resizing');
            this.resizingCardEl = cardEl;
            
            // Получаем размеры карточки для превью
            const rect = cardEl.getBoundingClientRect();
            this.resizeBaseRect = rect;
            
            // iOS: Создаём превью размера с blur
            this.resizePreview = document.createElement('div');
            this.resizePreview.className = 'resize-preview';
            this.resizePreview.style.left = rect.left + 'px';
            this.resizePreview.style.top = rect.top + 'px';
            this.resizePreview.style.width = rect.width + 'px';
            this.resizePreview.style.height = rect.height + 'px';
            this.resizePreview.style.transform = 'scale(0.95)';
            this.resizePreview.style.opacity = '0';
            document.body.appendChild(this.resizePreview);
            
            // iOS spring появление
            requestAnimationFrame(() => {
                this.resizePreview.style.transition = 'all 0.3s cubic-bezier(0.2, 0.8, 0.2, 1)';
                this.resizePreview.style.transform = 'scale(1)';
                this.resizePreview.style.opacity = '1';
            });
            
            // iOS: Создаём pill-индикатор размера
            this.resizeIndicator = document.createElement('div');
            this.resizeIndicator.className = 'resize-indicator';
            this.resizeIndicator.innerHTML = `<span style="opacity:0.6">${card.width}</span> × <span style="opacity:0.6">${card.height}</span>`;
            document.body.appendChild(this.resizeIndicator);
            this.updateIndicatorPosition(pt);
            
            // iOS haptic feedback
            if (navigator.vibrate) {
                navigator.vibrate(10);
            }
            
            // Обработчики (мышь + тач)
            this.boundHandleResize = (e) => this.handleResize(e);
            this.boundStopResize = (e) => this.stopResize(e);
            this.boundHandleResizeTouch = (e) => {
                e.preventDefault();
                if (e.touches && e.touches[0]) this.handleResize(e.touches[0]);
            };

            document.addEventListener('mousemove', this.boundHandleResize);
            document.addEventListener('mouseup', this.boundStopResize);
            document.addEventListener('touchmove', this.boundHandleResizeTouch, { passive: false });
            document.addEventListener('touchend', this.boundStopResize);
            document.addEventListener('touchcancel', this.boundStopResize);
            document.body.style.userSelect = 'none';
            document.body.style.cursor = 'se-resize';
        },
        
        handleResize(event) {
            if (!this.resizingCard) return;
            
            const deltaX = event.clientX - this.resizeStartX;
            const deltaY = event.clientY - this.resizeStartY;
            
            // Вычисляем размер одной ячейки грида (4 колонки)
            const grid = document.getElementById('cardGrid');
            if (!grid) return;
            const gridRect = grid.getBoundingClientRect();
            const gap = parseFloat(getComputedStyle(grid).columnGap) || 16;
            const cellWidth = (gridRect.width - gap * 3) / 4; // (ширина - 3 промежутка) / 4 колонки
            const cellHeight = 120; // фиксированная высота строки из CSS
            
            // Плавно обновляем превью с ограничением до 4
            if (this.resizePreview) {
                const maxWidth = cellWidth * 4;
                const maxHeight = cellHeight * 4;
                const newPreviewWidth = Math.max(cellWidth, Math.min(maxWidth, this.resizeBaseRect.width + deltaX));
                const newPreviewHeight = Math.max(cellHeight, Math.min(maxHeight, this.resizeBaseRect.height + deltaY));
                
                this.resizePreview.style.transition = 'width 0.1s ease, height 0.1s ease';
                this.resizePreview.style.width = newPreviewWidth + 'px';
                this.resizePreview.style.height = newPreviewHeight + 'px';
            }
            
            // Порог: размер ячейки на единицу
            let newWidth = this.resizeStartWidth + Math.round(deltaX / cellWidth);
            let newHeight = this.resizeStartHeight + Math.round(deltaY / cellHeight);
            
            // Ограничения 1-4
            newWidth = Math.max(1, Math.min(4, newWidth));
            newHeight = Math.max(1, Math.min(4, newHeight));
            
            // Обновляем только если размер изменился
            const cardIndex = this.cards.findIndex(c => c.id === this.resizingCard.id);
            if (cardIndex !== -1) {
                const oldWidth = this.cards[cardIndex].width;
                const oldHeight = this.cards[cardIndex].height;
                
                if (oldWidth !== newWidth || oldHeight !== newHeight) {
                    this.cards[cardIndex] = {
                        ...this.cards[cardIndex],
                        width: newWidth,
                        height: newHeight
                    };
                    
                    // iOS haptic feedback при изменении размера
                    if (navigator.vibrate) {
                        navigator.vibrate(3);
                    }
                }
            }
            
            // iOS: Обновляем индикатор с подсветкой изменённых значений
            if (this.resizeIndicator) {
                const widthChanged = newWidth !== this.resizeStartWidth;
                const heightChanged = newHeight !== this.resizeStartHeight;
                this.resizeIndicator.innerHTML = `
                    <span style="opacity:${widthChanged ? '1' : '0.6'}">${newWidth}</span> × 
                    <span style="opacity:${heightChanged ? '1' : '0.6'}">${newHeight}</span>
                `;
                this.updateIndicatorPosition(event);
            }
        },
        
        updateIndicatorPosition(event) {
            if (this.resizeIndicator) {
                this.resizeIndicator.style.left = (event.clientX + 15) + 'px';
                this.resizeIndicator.style.top = (event.clientY + 15) + 'px';
            }
        },
        
        stopResize(event) {
            // iOS spring эффект на карточке
            if (this.resizingCardEl) {
                this.resizingCardEl.classList.remove('resizing');
                this.resizingCardEl.classList.add('just-dropped');
                setTimeout(() => {
                    if (this.resizingCardEl) {
                        this.resizingCardEl.classList.remove('just-dropped');
                        this.resizingCardEl = null;
                    }
                }, 500);
            }
            
            // iOS: Удаляем превью с spring анимацией
            if (this.resizePreview) {
                this.resizePreview.style.transition = 'all 0.3s cubic-bezier(0.2, 0.8, 0.2, 1)';
                this.resizePreview.style.opacity = '0';
                this.resizePreview.style.transform = 'scale(0.9)';
                setTimeout(() => {
                    if (this.resizePreview) {
                        this.resizePreview.remove();
                        this.resizePreview = null;
                    }
                }, 300);
            }
            
            // iOS: Удаляем индикатор с bounce
            if (this.resizeIndicator) {
                this.resizeIndicator.style.transition = 'all 0.25s cubic-bezier(0.2, 0.8, 0.2, 1)';
                this.resizeIndicator.style.opacity = '0';
                this.resizeIndicator.style.transform = 'scale(0.5) translateY(10px)';
                setTimeout(() => {
                    if (this.resizeIndicator) {
                        this.resizeIndicator.remove();
                        this.resizeIndicator = null;
                    }
                }, 250);
            }
            
            // iOS haptic feedback
            if (navigator.vibrate) {
                navigator.vibrate([5, 30, 5]);
            }
            
            this.resizingCard = null;
            this.resizeBaseRect = null;
            
            document.removeEventListener('mousemove', this.boundHandleResize);
            document.removeEventListener('mouseup', this.boundStopResize);
            document.removeEventListener('touchmove', this.boundHandleResizeTouch);
            document.removeEventListener('touchend', this.boundStopResize);
            document.removeEventListener('touchcancel', this.boundStopResize);
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
            
            this.saveLayout(); // Автосохранение после изменения размера
        },
        
        // Helpers для мультиселекта
        toggleArea(code, checked) {
            if (!this.editingCard.areas) this.editingCard.areas = [];
            if (checked) {
                if (!this.editingCard.areas.includes(code)) {
                    this.editingCard.areas.push(code);
                }
            } else {
                this.editingCard.areas = this.editingCard.areas.filter(a => a !== code);
            }
        },
        
        addArea(code) {
            if (code && !this.editingCard.areas) this.editingCard.areas = [];
            if (code && !this.editingCard.areas.includes(code)) {
                this.editingCard.areas.push(code);
            }
        },
        
        removeArea(code) {
            this.editingCard.areas = this.editingCard.areas.filter(a => a !== code);
        },
        
        addGroup(code) {
            if (code && !this.editingCard.groups) this.editingCard.groups = [];
            if (code && !this.editingCard.groups.includes(code)) {
                this.editingCard.groups.push(code);
            }
        },
        
        removeGroup(code) {
            this.editingCard.groups = this.editingCard.groups.filter(g => g !== code);
        },
        
        toggleDivision(code, checked) {
            if (!this.editingCard.divisions) this.editingCard.divisions = [];
            if (checked) {
                if (!this.editingCard.divisions.includes(code)) {
                    this.editingCard.divisions.push(code);
                }
            } else {
                this.editingCard.divisions = this.editingCard.divisions.filter(d => d !== code);
            }
        },
        
        removeDivision(code) {
            this.editingCard.divisions = this.editingCard.divisions.filter(d => d !== code);
        },
        
        getDivisionName(code) {
            const division = this.availableDivisions.find(d => d.code === code);
            return division ? division.name : code;
        },
        
        getAreaName(code) {
            const area = this.availableAreas.find(a => a.code === code);
            return area ? area.name : code;
        },
        
        getGroupName(code) {
            const group = this.availableGroups.find(g => g.code === code);
            return group ? group.name : code;
        },
        
        // Helpers
        getCardIcon(type) {
            const icons = {
                sales: 'fa-dollar-sign',
                payments: 'fa-credit-card',
                debt: 'fa-hand-holding-usd',
                customers: 'fa-users',
                chart: 'fa-chart-line',
                custom: 'fa-cog',
                header: 'fa-heading',
                text: 'fa-font'
            };
            return icons[type] || 'fa-square';
        },
        
        getCardColor(type) {
            const colors = {
                sales: '#3b82f6',
                payments: '#10b981',
                debt: '#ef4444',
                customers: '#f59e0b',
                chart: '#06b6d4',
                custom: '#8b5cf6',
                header: '#374151',
                text: '#6b7280'
            };
            return colors[type] || '#8b5cf6';
        },
        
        // Генерация стиля для кастомного цвета карточки
        getCustomColorStyle(color) {
            if (!color) return '';
            
            // Проверяем текущую тему
            if (this.darkTheme) {
                // Тёмная тема - тёмный градиент
                const darkerColor = this.darkenColor(color, 40);
                return `border-left-color: ${color}; background: linear-gradient(145deg, ${darkerColor} 0%, rgba(30, 35, 45, 0.95) 100%) !important;`;
            } else {
                // Светлая тема - светлый градиент с акцентным цветом
                const lightColor = this.lightenColor(color, 85);
                return `border-left: none !important; background: linear-gradient(145deg, #ffffff 0%, ${lightColor} 100%) !important;`;
            }
        },
        
        // Полный стиль карточки включая CSS переменную для акцента
        getCardFullStyle(card) {
            if (!card.customColor) return '';
            
            let style = this.getCustomColorStyle(card.customColor);
            // Добавляем CSS переменную для использования в ::after
            style += ` --custom-accent-color: ${card.customColor};`;
            return style;
        },
        
        // Осветление цвета
        lightenColor(hex, percent) {
            const num = parseInt(hex.replace('#', ''), 16);
            const amt = Math.round(2.55 * percent);
            const R = Math.min((num >> 16) + amt, 255);
            const G = Math.min((num >> 8 & 0x00FF) + amt, 255);
            const B = Math.min((num & 0x0000FF) + amt, 255);
            return '#' + (0x1000000 + R * 0x10000 + G * 0x100 + B).toString(16).slice(1);
        },
        
        // Затемнение цвета
        darkenColor(hex, percent) {
            const num = parseInt(hex.replace('#', ''), 16);
            const amt = Math.round(2.55 * percent);
            const R = Math.max((num >> 16) - amt, 0);
            const G = Math.max((num >> 8 & 0x00FF) - amt, 0);
            const B = Math.max((num & 0x0000FF) - amt, 0);
            return '#' + (0x1000000 + R * 0x10000 + G * 0x100 + B).toString(16).slice(1);
        },
        
        formatValue(value, format) {
            if (value === null || value === undefined) return '—';
            
            switch (format) {
                case 'currency':
                    return new Intl.NumberFormat('ru-RU').format(Math.round(value)) + ' ֏';
                case 'percent':
                    return value.toFixed(1) + '%';
                case 'number':
                default:
                    return new Intl.NumberFormat('ru-RU').format(value);
            }
        },
        
        // Функции для сравнения
        getChangePercent(currentValue, previousValue) {
            if (!previousValue || previousValue === 0) {
                if (currentValue > 0) return '+∞%';
                return '0%';
            }
            const change = ((currentValue - previousValue) / previousValue) * 100;
            const sign = change >= 0 ? '+' : '';
            return sign + change.toFixed(1) + '%';
        },
        
        getChangeClass(currentValue, previousValue) {
            if (!previousValue || previousValue === 0) {
                return currentValue > 0 ? 'positive' : 'neutral';
            }
            const change = currentValue - previousValue;
            if (change > 0) return 'positive';
            if (change < 0) return 'negative';
            return 'neutral';
        },
        
        // Фокус и анимация на карточку
        focusOnCard(cardId) {
            // Даём время Alpine отрендерить карточку
            setTimeout(() => {
                const cardEl = document.querySelector(`.dashboard-card[data-id="${cardId}"]`);
                if (cardEl) {
                    // Добавляем класс для анимации
                    cardEl.classList.add('card-new');
                    
                    // Плавный скролл к карточке
                    cardEl.scrollIntoView({ 
                        behavior: 'smooth', 
                        block: 'center',
                        inline: 'center'
                    });
                    
                    // Убираем класс анимации после завершения
                    setTimeout(() => {
                        cardEl.classList.remove('card-new');
                    }, 2000);
                }
            }, 50);
        },
        
        // Переключение метрики для графика
        toggleChartMetric(metric) {
            if (!this.editingCard.chartMetrics) {
                this.editingCard.chartMetrics = [];
            }
            const index = this.editingCard.chartMetrics.indexOf(metric);
            if (index > -1) {
                // Убираем, но оставляем хотя бы одну метрику
                if (this.editingCard.chartMetrics.length > 1) {
                    this.editingCard.chartMetrics.splice(index, 1);
                }
            } else {
                this.editingCard.chartMetrics.push(metric);
            }
        },
        
        // Переключение периода для сравнения
        toggleComparePeriod(period) {
            if (!this.editingCard.comparePeriods) {
                this.editingCard.comparePeriods = ['current'];
            }
            const index = this.editingCard.comparePeriods.indexOf(period);
            if (index > -1) {
                // Убираем, но оставляем хотя бы один период
                if (this.editingCard.comparePeriods.length > 1) {
                    this.editingCard.comparePeriods.splice(index, 1);
                }
            } else {
                this.editingCard.comparePeriods.push(period);
            }
        },
        
        // Переключение года для сравнения
        toggleCompareYear(year) {
            if (!this.editingCard.compareYears) {
                this.editingCard.compareYears = [];
            }
            const index = this.editingCard.compareYears.indexOf(year);
            if (index > -1) {
                this.editingCard.compareYears.splice(index, 1);
            } else {
                this.editingCard.compareYears.push(year);
            }
            // Сортируем годы по убыванию
            this.editingCard.compareYears.sort((a, b) => b - a);
        },
        
        showToast(message, type = 'info') {
            // Simple alert for now
            alert(message);
        },
        
        // Удаление карточки с паролем
        removeCard(id) {
            const password = prompt('Введите пароль для удаления карточки:');
            if (password === 'maximuss') {
                this.cards = this.cards.filter(c => c.id !== id);
                this.saveLayout();
                this.showToast('Карточка удалена', 'success');
            } else if (password !== null) {
                this.showToast('Неверный пароль!', 'error');
            }
        },
        
        // Экспорт layout в файл
        exportLayout() {
            const layoutData = {
                pages: this.pages.map(page => ({
                    id: page.id,
                    name: page.name,
                    cards: page.cards.map(card => {
                        const cleanCard = { ...card };
                        delete cleanCard.loading;
                        delete cleanCard.chartLoading;
                        return cleanCard;
                    })
                })),
                nextPageId: this.nextPageId,
                currentPageId: this.currentPageId,
                nextId: this.nextId,
                exportDate: new Date().toISOString()
            };
            
            const blob = new Blob([JSON.stringify(layoutData, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `dashboard_layout_${new Date().toISOString().slice(0,10)}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            
            this.showToast('Layout экспортирован!', 'success');
        },
        
        // Импорт layout из файла
        importLayout(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            const reader = new FileReader();
            reader.onload = async (e) => {
                try {
                    const layoutData = JSON.parse(e.target.result);
                    
                    // Валидация
                    if (!layoutData.pages || !Array.isArray(layoutData.pages)) {
                        throw new Error('Неверный формат файла');
                    }
                    
                    if (!confirm(`Загрузить layout из файла?\nЭто заменит текущий layout.\nСтраниц: ${layoutData.pages.length}\nДата экспорта: ${layoutData.exportDate || 'неизвестно'}`)) {
                        return;
                    }
                    
                    // Загружаем данные
                    this.pages = layoutData.pages;
                    this.nextPageId = layoutData.nextPageId || (Math.max(...this.pages.map(p => p.id)) + 1);
                    this.currentPageId = layoutData.currentPageId || this.pages[0].id;
                    this.nextId = layoutData.nextId || (Math.max(...this.pages.flatMap(p => p.cards.map(c => c.id)), 0) + 1);
                    
                    // Сохраняем на сервер
                    await this.saveLayout();
                    
                    // Обновляем все карточки
                    this.cards.forEach(card => {
                        this.loadCardData(card);
                    });
                    
                    this.showToast('Layout загружен!', 'success');
                } catch (error) {
                    console.error('Import error:', error);
                    this.showToast('Ошибка импорта: ' + error.message, 'error');
                }
            };
            reader.readAsText(file);
            
            // Сброс input для возможности повторной загрузки того же файла
            event.target.value = '';
        }
    };
}
